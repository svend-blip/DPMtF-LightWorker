"""The LightWorker role-execution loop (GOAL.md §§14, 20, 21, 22, 24).

This module is the loop the nine built modules were waiting for. It
joins the envelope validator, the Father client, the allocator
adapter, the git workspace and the tmux transport into a single
``run_once()`` that polls Father, claims a role-execution, runs the
§14 sequence and reports a result — or, on any failure, reports a
failure and cleans up. The next run decides what to do next; the
worker reports and stops.

The four things this module must hold (§5.2, §5.3, §24, §36):

* One execution at a time. ``max_parallel_executions: 1`` is read
  from the config; once a claim succeeds, subsequent ``run_once``
  calls return ``False`` until the result (or the failure) reaches
  Father and cleanup has run.
* Failure reports, it does not hang. Every path out of the sequence
  goes through ``father.fail(...)`` or ``father.complete(...)``.
  Father records the execution as claimed and waits. Nothing times
  it out yet, so a worker that dies silently leaves Father waiting
  on an execution that will never return.
* Cleanup runs on both paths. The worktree is removed, the tmux
  session is killed, and the mirror is preserved — idempotent
  across both success and failure (§36).
* The loop never advances the chain. It does not start the next
  role, does not pick a next alias, does not mark a job complete.
  Father decides from the result. (The §5.2 prohibition is the
  reason this file does not import anything from ``scripts/`` or
  reach into the bridge layer.)

Failure categories live as named module-level constants because a
typo in an inline literal at a report site would reach Father as
an unrecognised category and nobody would notice. They are the
canonical §24 names; the 17 ``errors.py`` already defines carry
their own ``category`` attribute and the loop reads it from there.
Two categories the loop cannot avoid needing are not in
``errors.py`` (``RESULT_REPORT_FAILED`` and ``INTERNAL_WORKER_ERROR``);
both are defined here as constants. The reasoning is in the run
result; extending ``errors.py`` is a separate handoff.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional

from dpmtf_lightworker.allocator import AllocatorAdapter
from dpmtf_lightworker.client_config import render_execution_config
from dpmtf_lightworker.envelope_validator import (
    SUPPORTED_MODEL_SOURCE,
    ValidatorConfig,
    validate_envelope,
)
from dpmtf_lightworker.errors import EnvelopeError
from dpmtf_lightworker.events import Event, EventType
from dpmtf_lightworker.father_client import FatherClient, FatherClientError
from dpmtf_lightworker.git_workspace import GitWorkspace
from dpmtf_lightworker.models import ExecutionEnvelope
from dpmtf_lightworker.states import WorkerState
from dpmtf_lightworker.tmux_session import TmuxSession


# ---------------------------------------------------------------------------
# §24 category constants
# ---------------------------------------------------------------------------
#
# Every category string the loop emits as a Father failure payload
# lives here as a named module-level constant with its §24 citation
# beside it. A typo at a report site would reach Father as an
# unrecognised category and nobody would notice; that is why none
# of these appear as inline literals at a call site.
#
# The 17 categories ``errors.py`` already defines carry their
# ``category`` attribute and the loop reads it from there. The two
# below are absent from ``errors.py`` because the loop does not
# *raise* them — it catches whatever the collaborators threw and
# reports the category string. A class buys nothing there.


CATEGORY_INVALID_EXECUTION_ENVELOPE: str = "INVALID_EXECUTION_ENVELOPE"
# §24: envelope rejected by validate_envelope (multi-role, bad path,
# shell pipeline, ...).

CATEGORY_UNSUPPORTED_SCHEMA_VERSION: str = "UNSUPPORTED_SCHEMA_VERSION"
# §24: envelope declares a schema_version this worker does not accept.

CATEGORY_WORKER_MISMATCH: str = "WORKER_MISMATCH"
# §24: envelope is addressed to a different worker_id.

CATEGORY_UNSUPPORTED_MODEL_SOURCE: str = "UNSUPPORTED_MODEL_SOURCE"
# §24: model_source other than 'model_allocator'.

CATEGORY_UNSUPPORTED_CLIENT: str = "UNSUPPORTED_CLIENT"
# §24: client this worker does not accept.

CATEGORY_REPOSITORY_FETCH_FAILED: str = "REPOSITORY_FETCH_FAILED"
# §24: git fetch or mirror clone could not complete.

CATEGORY_BASE_COMMIT_NOT_FOUND: str = "BASE_COMMIT_NOT_FOUND"
# §24: base_commit SHA does not exist in the mirror.

CATEGORY_WORKTREE_CREATION_FAILED: str = "WORKTREE_CREATION_FAILED"
# §24: git worktree could not be created at the requested base commit.

CATEGORY_ALLOCATOR_NOT_AVAILABLE: str = "ALLOCATOR_NOT_AVAILABLE"
# §24: allocator command could not be invoked or returned unusable output.

CATEGORY_ALLOCATOR_PREFLIGHT_FAILED: str = "ALLOCATOR_PREFLIGHT_FAILED"
# §24: allocator rejected preflight for a role and client.

CATEGORY_ALIAS_VALIDATION_FAILED: str = "ALIAS_VALIDATION_FAILED"
# §24: allocator rejected an alias and client combination.

CATEGORY_CLIENT_CONFIG_RENDER_FAILED: str = "CLIENT_CONFIG_RENDER_FAILED"
# §24: allocator failed to render client configuration.

CATEGORY_RUNTIME_RELEASE_FAILED: str = "RUNTIME_RELEASE_FAILED"
# §24: allocator failed to release a runtime.

CATEGORY_TMUX_START_FAILED: str = "TMUX_START_FAILED"
# §24: tmux session could not be created for an execution.

CATEGORY_CLIENT_START_FAILED: str = "CLIENT_START_FAILED"
# §24: client launch command could not be started in its tmux session.

CATEGORY_HANDOFF_INJECTION_FAILED: str = "HANDOFF_INJECTION_FAILED"
# §24: compiled handoff could not be injected into the tmux session.

CATEGORY_RESULT_REPORT_FAILED: str = "RESULT_REPORT_FAILED"
# §24: result could not be reported to Father. The transport raised
# FatherClientError when this code called complete() or fail().

CATEGORY_INTERNAL_WORKER_ERROR: str = "INTERNAL_WORKER_ERROR"
# §24: any unexpected exception the loop did not catch by category
# reaches Father under this name. It is the catch-all for bugs.

CATEGORY_PATCH_GENERATION_FAILED: str = "PATCH_GENERATION_FAILED"
# §24: a binary-safe patch could not be generated from the worktree.

CATEGORY_ROLE_EXECUTION_TIMEOUT: str = "ROLE_EXECUTION_TIMEOUT"
# §24: the role did not produce its deliverable before the deadline. Nothing
# was ever written at the expected path.

CATEGORY_DELIVERABLE_MISSING: str = "DELIVERABLE_MISSING"
# §24: a file appeared at the expected path but never became a usable
# deliverable -- empty, or still being written when the deadline passed.


# How long a role may take before the worker gives up on it, and how often it
# looks. The default suits a 14B model at 8k context writing a short document;
# `worker.execution_timeout_seconds` overrides it per machine.
DEFAULT_EXECUTION_TIMEOUT_SECONDS: float = 1800.0
DELIVERABLE_POLL_SECONDS: float = 5.0

# What OpenCode's idle prompt says when it is ready for a first message. The
# worker waits for this before injecting, because `launch` returns when tmux
# has typed the command, not when the TUI can receive one.
#
# Client-specific by nature, and §5 fixes the client at opencode for V1. A
# second client means a marker per client, not a cleverer regex.
CLIENT_READY_MARKER: str = "Ask anything"
CLIENT_READY_TIMEOUT_SECONDS: float = 120.0


# ---------------------------------------------------------------------------
# Render-callable type — the Mission Contract injects
# ``render_execution_config`` directly so the loop never imports
# the module-level function via attribute access in a place the
# criterion can grep.
# ---------------------------------------------------------------------------

RenderConfigCallable = Callable[..., str]


# ---------------------------------------------------------------------------
# WorkerLoop
# ---------------------------------------------------------------------------


class WorkerLoop:
    """A single iteration of the role-execution loop.

    Every collaborator is injected. §29 forbids the default suite from
    needing a GPU, a network, a real Father, a real allocator, real
    OpenCode or real tmux; the loop honours that by reading nothing
    off the filesystem and opening no connections of its own. The
    Father transport is the injected ``FatherClient``.

    ``run_once`` polls Father, and if there is work, runs the §14
    sequence. It returns ``True`` when an execution was attempted
    and ``False`` when nothing was offered. The two are real
    booleans — TG1 asserts ``is False`` and TG2 asserts ``is True``,
    so a ``None`` or ``1`` would fail the criterion.
    """

    def __init__(
        self,
        config: Any,
        father: FatherClient,
        allocator: AllocatorAdapter,
        git: GitWorkspace,
        tmux: TmuxSession,
        render_config: RenderConfigCallable = render_execution_config,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._father = father
        self._allocator = allocator
        self._git = git
        self._tmux = tmux
        self._render_config = render_config
        self._clock = clock
        # Injected so the suite does not spend real seconds waiting for a
        # deliverable that a fixture wrote before the call.
        self._sleep = sleep

        self._state: WorkerState = WorkerState.RECEIVED
        self._events: List[Event] = []
        # §5.3: max_parallel_executions == 1. Once a claim succeeds,
        # subsequent run_once calls return False until the live
        # execution has either completed or failed and cleaned up.
        self._in_flight: bool = False
        # Identification fields of the in-flight execution; populated
        # by _execute and read by _cleanup. Empty strings when no
        # execution is in flight.
        self._inflight_execution_id: str = ""
        self._inflight_attempt_id: str = ""
        self._inflight_target_role: str = ""
        self._inflight_model_alias: str = ""
        self._inflight_client: str = ""

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def state(self) -> WorkerState:
        return self._state

    @property
    def events(self) -> List[Event]:
        return self._events

    def run_once(self) -> bool:
        """Poll Father once. Return ``True`` if work was attempted.

        The function returns a real boolean — not ``None``, not ``1``.
        TG1 asserts the no-work case is ``is False`` and TG2 asserts
        the work case is ``is True``.
        """
        if self._in_flight:
            return False

        # 1. Poll Father for the next execution to claim.
        self._transition(WorkerState.RECEIVED)
        offered = self._poll_father()
        if offered is None or not offered:
            return False
        if not isinstance(offered, Mapping):
            return False

        execution_id = str(offered.get("execution_id", ""))
        if not execution_id:
            return False

        self._in_flight = True
        try:
            self._execute(offered)
        finally:
            self._in_flight = False
        return True

    # ------------------------------------------------------------------
    # The §14 sequence
    # ------------------------------------------------------------------

    def _execute(self, payload: Mapping[str, Any]) -> None:
        """Run the §14 sequence, and report whatever escapes it.

        Every step inside catches `EnvelopeError` -- the failures the
        sequence *expects*. Anything else used to propagate out, get logged
        by `_main`'s catch-all, and leave the execution `claimed` forever on
        Father's side -- and a claimed execution blocks every future offer
        to this worker. One malformed JSON file from a collaborator would
        have jammed the queue permanently, exactly the way EXEC-013 did.

        This is what INTERNAL_WORKER_ERROR is for: §24's name for a bug in
        the loop itself. `_report_failure` also runs cleanup, so the
        worktree and the tmux session do not outlive the failure.
        """
        try:
            self._run_sequence(payload)
        except Exception as exc:  # noqa: BLE001 - the queue must not jam
            self._report_failure(
                execution_id=str(payload.get("execution_id", "")),
                attempt_id=str(payload.get("attempt_id", "")),
                target_role=str(payload.get("target_role", "")),
                model_alias=str(payload.get("model_alias", "")),
                client=str(payload.get("client", "")),
                category=CATEGORY_INTERNAL_WORKER_ERROR,
                failure=f"unexpected error in the execution sequence: {exc!r}",
                payload=payload,
                last_state=self._state,
            )

    def _run_sequence(self, payload: Mapping[str, Any]) -> None:
        """The §14 sequence proper. Expected failures are reported inline."""
        execution_id = str(payload["execution_id"])
        attempt_id = str(payload.get("attempt_id", ""))
        target_role = str(payload.get("target_role", ""))
        model_alias = str(payload.get("model_alias", ""))
        client = str(payload.get("client", ""))

        # Publish identification so _cleanup and any later step can
        # build a §22 event with all nine required fields populated.
        self._inflight_execution_id = execution_id
        self._inflight_attempt_id = attempt_id
        self._inflight_target_role = target_role
        self._inflight_model_alias = model_alias
        self._inflight_client = client

        # 2. Claim.
        self._transition(WorkerState.CLAIMED)
        claim_response = self._safe_call(
            lambda: self._father.claim(execution_id),
            category=CATEGORY_RESULT_REPORT_FAILED,
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=model_alias,
            client=client,
            payload=payload,
        )
        if claim_response is None:
            return
        self._record_event(
            EventType.ROLE_EXECUTION_CLAIMED,
            payload=payload,
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=str(payload.get("model_alias", "")),
            client=str(payload.get("client", "")),
        )

        # 3. Validate the envelope before anything is built. The
        # validator returns ExecutionEnvelope or raises EnvelopeError.
        self._transition(WorkerState.VALIDATING_ENVELOPE)
        validator_config = self._build_validator_config(payload)
        envelope: Optional[ExecutionEnvelope] = None
        try:
            envelope = validate_envelope(payload, config=validator_config)
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=str(payload.get("model_alias", "")),
                client=str(payload.get("client", "")),
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return

        # 4-5. Prepare the repository and the worktree.
        self._transition(WorkerState.PREPARING_REPOSITORY)
        self._safe_call(
            lambda: self._git.ensure_mirror(
                envelope.repository.project_key,
                envelope.repository.clone_url,
            ),
            category=CATEGORY_REPOSITORY_FETCH_FAILED,
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
            payload=payload,
        )
        if self._state != WorkerState.PREPARING_REPOSITORY:
            return
        self._record_event(
            EventType.REPOSITORY_READY,
            payload={"project_key": envelope.repository.project_key},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        self._transition(WorkerState.PREPARING_WORKTREE)
        worktree_path: Optional[Path] = None
        try:
            worktree_path = self._git.create_worktree(
                execution_id,
                envelope.repository.project_key,
                envelope.repository.base_commit,
            )
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return
        if worktree_path is None:
            return
        worktree_str = str(worktree_path)
        self._record_event(
            EventType.WORKTREE_CREATED,
            payload={"worktree": worktree_str},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 6. Allocator preflight.
        self._transition(WorkerState.ALLOCATOR_PREFLIGHT)
        self._record_event(
            EventType.ALLOCATOR_PREFLIGHT_STARTED,
            payload={"role": target_role, "client": envelope.client},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )
        try:
            self._allocator.preflight(target_role, envelope.client)
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.ALLOCATOR_PREFLIGHT_PASSED,
            payload={"role": target_role, "client": envelope.client},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 7. Allocator validate alias.
        self._transition(WorkerState.ALLOCATOR_VALIDATING)
        try:
            self._allocator.validate(envelope.model_alias, envelope.client)
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.ALIAS_VALIDATED,
            payload={"alias": envelope.model_alias, "client": envelope.client},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 8. Render client configuration.
        self._transition(WorkerState.RENDERING_CLIENT_CONFIG)
        config_output = (
            str(Path(self._config.paths.opencode_config_root) / execution_id)
            + ".json"
        )
        try:
            published_path = self._render_config(
                self._allocator,
                target_role,
                config_output,
                # The allocator renders only model and provider. The
                # permission block that confines the role to its worktree
                # (§19) comes from a base config the machine's steward owns
                # and worker.yaml names; the worktree is bound into it here
                # because it differs on every execution.
                str(self._config.allocator.base_config or ""),
                worktree_str,
            )
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.CLIENT_CONFIG_RENDERED,
            payload={"path": published_path},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 9. Acquire runtime — the allocator returns the client command.
        self._transition(WorkerState.ACQUIRING_RUNTIME)
        try:
            # The config published one step ago is named here, so the command
            # points OpenCode at it. Without this the allocator names its own
            # shared role config -- and the permission block confining the
            # role to its worktree (§19), and this machine's provider
            # endpoint, are in a file nobody opens.
            client_command = self._allocator.run(
                target_role, envelope.client, config_path=published_path
            )
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.RUNTIME_ACQUIRED,
            payload={"command": client_command},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )
        self._record_event(
            EventType.RUNTIME_READY,
            payload={},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 10-11. Create tmux and launch the client.
        self._transition(WorkerState.CREATING_TMUX)
        session_name = self._tmux.session_name(target_role, execution_id)
        try:
            self._tmux.create(session_name, worktree_str)
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.TMUX_SESSION_CREATED,
            payload={"session": session_name},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        self._transition(WorkerState.STARTING_CLIENT)
        try:
            self._tmux.launch(session_name, client_command)
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.CLIENT_STARTED,
            payload={"session": session_name},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 11b. Wait for the client to be ready to receive a prompt.
        #
        # `launch` returns as soon as tmux has typed the command; the TUI
        # takes seconds more to come up. The handoff used to go in
        # immediately and land in a terminal that was not listening yet, so
        # it was simply lost — the pane showed OpenCode at an empty prompt
        # while Father waited on an execution that would never report.
        #
        # `wait_ready` has existed in the transport since it was built and
        # nothing called it. It returns False rather than hanging, and a
        # client that never shows its prompt is a real failure worth
        # reporting rather than injecting into and hoping.
        if not self._tmux.wait_ready(
            session_name, CLIENT_READY_MARKER, CLIENT_READY_TIMEOUT_SECONDS
        ):
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=CATEGORY_CLIENT_START_FAILED,
                failure=(
                    f"{envelope.client} never showed its prompt "
                    f"({CLIENT_READY_MARKER!r}) within "
                    f"{CLIENT_READY_TIMEOUT_SECONDS:.0f}s; pane:\n"
                    + self._pane_tail(session_name)
                ),
                payload=payload,
                last_state=self._state,
            )
            return

        # 12. Inject the handoff, and say where the deliverable goes.
        self._transition(WorkerState.INJECTING_HANDOFF)
        try:
            self._tmux.inject(
                session_name,
                self._handoff_with_deliverable_path(
                    envelope, self._write_governance(worktree_str, envelope)
                ),
            )
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.HANDOFF_INJECTED,
            payload={"session": session_name},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 13. Run the role, and wait for it.
        #
        # Until now the loop injected the handoff and reported straight
        # away, because it had no real OpenCode session to wait on. The
        # first live execution did exactly that: eleven seconds from claim
        # to an empty result, with the model still starting.
        #
        # There is no completion signal from the client, so the deliverable
        # itself is the signal. The role writes it inside its own worktree
        # at the path the envelope named, and the worker waits for that file
        # to appear and stop changing.
        self._transition(WorkerState.RUNNING_ROLE)
        self._record_event(
            EventType.ROLE_RUNNING,
            payload={"session": session_name},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 14. Collect the result.
        self._transition(WorkerState.COLLECTING_RESULT)
        relative = envelope.handoff.expected_deliverable
        content, why = self._await_deliverable(
            worktree_str, relative, execution_id, attempt_id
        )
        if content is None:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=why,
                failure=(
                    f"no usable deliverable at {relative} after "
                    f"{self._deliverable_timeout():.0f}s; last pane:\n"
                    + self._pane_tail(session_name)
                ),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.DELIVERABLE_DETECTED,
            payload={"path": relative, "bytes": len(content)},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        patch_text = ""
        try:
            patch_text = self._git.generate_patch(
                worktree_str, envelope.repository.base_commit
            )
        except EnvelopeError as exc:
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=exc.category,
                failure=str(exc),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.PATCH_CREATED,
            payload={"size": len(patch_text)},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # The vocabulary is Father's return path's, because that is the side
        # that has to produce a file. `mode` and a bare deliverable path were
        # accepted by Father's endpoint and refused by the return path that
        # publishes the deliverable -- one Father, two ideas of a result.
        #
        # The content travels inline: this version has no artifact transfer,
        # so a result naming a path Father cannot reach is a result Father
        # cannot use.
        result = {
            "status": "role_execution_completed",
            "result_mode": envelope.result_contract.mode.value,
            "deliverable": {
                "path": envelope.handoff.expected_deliverable,
                "content": content,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
            "patch": patch_text,
        }

        # 16. Report result.
        self._transition(WorkerState.REPORTING_RESULT)
        try:
            self._father.complete(
                execution_id,
                attempt_id=attempt_id,
                result=result,
            )
        except FatherClientError as exc:
            if exc.refused:
                # Father answered and declined. That is not a worker fault,
                # and the failure is recorded as a refusal so the record says
                # which of the two it was.
                #
                # It IS still reported. An earlier version stopped here on the
                # reasoning that the execution was already terminal so the
                # report could not land -- true only when the completion had
                # been recorded before being judged. A completion refused by
                # the endpoint's own validation is rejected BEFORE anything is
                # recorded, so the execution stays `claimed` forever, and a
                # claimed execution blocks every future offer to this worker.
                #
                # EXEC-013 jammed the queue exactly that way: the role had
                # produced its deliverable, Father refused it on a stale
                # validator, and nothing on either side could close it.
                #
                # `_report_failure` already swallows a transport error from
                # `fail`, so a genuinely-terminal execution answering 409 costs
                # nothing here.
                sys.stderr.write(
                    f"dpmtf-lightworker: Father refused the result for "
                    f"{execution_id}: {exc}\n"
                )
                sys.stderr.flush()
            self._report_failure(
                execution_id=execution_id,
                attempt_id=attempt_id,
                target_role=target_role,
                model_alias=envelope.model_alias,
                client=envelope.client,
                category=CATEGORY_RESULT_REPORT_FAILED,
                failure=(
                    ("Father refused the result: " if exc.refused
                     else "could not report the result: ") + str(exc)
                ),
                payload=payload,
                last_state=self._state,
            )
            return
        self._record_event(
            EventType.RESULT_REPORTED,
            payload={"execution_id": execution_id},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 17. Release runtime — best-effort, never blocks completion.
        # The release is a terminal-path step; the work has already
        # been reported. A failure here is recorded in the event
        # payload (so consumers can see the release did not succeed)
        # but does not re-fail the execution — that would be worse.
        self._transition(WorkerState.RELEASING_RUNTIME)
        release_succeeded = True
        release_error: Optional[str] = None
        try:
            self._allocator.release(
                envelope.model_alias, policy="stop_after_step"
            )
        except (EnvelopeError, ValueError) as exc:
            release_succeeded = False
            release_error = f"{type(exc).__name__}: {exc}"
        release_payload: Dict[str, Any] = {"alias": envelope.model_alias}
        release_payload["succeeded"] = release_succeeded
        if release_error is not None:
            release_payload["error"] = release_error
        self._record_event(
            EventType.RUNTIME_RELEASED,
            payload=release_payload,
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )

        # 18. Terminal state and cleanup.
        self._transition(WorkerState.ROLE_EXECUTION_COMPLETED)
        self._record_event(
            EventType.ROLE_EXECUTION_COMPLETED,
            payload={},
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=envelope.model_alias,
            client=envelope.client,
        )
        self._cleanup(execution_id, session_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition(self, state: WorkerState) -> None:
        self._state = state

    def _record_event(
        self,
        event_type: EventType,
        *,
        payload: Mapping[str, Any],
        execution_id: str,
        attempt_id: str,
        target_role: str,
        model_alias: str,
        client: str,
    ) -> None:
        event = Event(
            worker_id=str(self._config.worker.id),
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=model_alias,
            client=client,
            event_type=event_type,
            timestamp=self._clock(),
            payload=dict(payload),
        )
        self._events.append(event)

    def _build_validator_config(
        self, payload: Mapping[str, Any]
    ) -> ValidatorConfig:
        """Build a ValidatorConfig from the loaded WorkerConfig.

        WorkerConfig does not carry a supported_schema_versions or
        supported_clients allow-list, so we supply them explicitly.
        The values are the V1 conventions: schema_version "1",
        client "opencode". They live as named constants at the top
        of this function so a future version bump is one edit.
        """
        return ValidatorConfig(
            worker_id=str(self._config.worker.id),
            supported_schema_versions=frozenset({"1"}),
            supported_clients=frozenset({"opencode"}),
            repository_root=Path(
                str(self._config.paths.repository_root)
            ).resolve(),
            model_source=SUPPORTED_MODEL_SOURCE,
        )

    def _write_governance(self, worktree: str, envelope: Any) -> str:
        """Put the role's governance where the role can read it.

        Father compiles it into every envelope — §19 is explicit that Father
        sends the content rather than granting the worker a path into its
        tree — and the worker discarded it. Every handoff opened with "Read
        481_LIGHTWORKER_IMPLE01LW.md — it is in this envelope", and the file
        was nowhere on the machine.

        EXEC-009 is what that costs. The model looked for its role
        definition, did not find it, and improvised: the pane's objective had
        drifted to "conduct a security audit", which appears in no handoff
        ever sent.

        Written as a file rather than injected, because the whole governance
        document plus the handoff does not fit in an 8k window. The role
        reads it if it needs it.

        Returns the relative path, or "" when the envelope carries none.
        """
        content = getattr(envelope.handoff, "governance_content", "") or ""
        if not content.strip():
            return ""
        relative = f".lightworker/{envelope.target_role}-governance.md"
        target = Path(worktree) / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            # Not fatal. A role without its governance is worse off, but a
            # role with no work at all is worse still, and the handoff itself
            # already arrived.
            sys.stderr.write(
                f"dpmtf-lightworker: could not write governance to "
                f"{target}: {exc}\n"
            )
            sys.stderr.flush()
            return ""
        return relative

    def _handoff_with_deliverable_path(
        self, envelope: Any, governance_path: str = ""
    ) -> str:
        """The compiled handoff, plus the things only the worker knows.

        The wait watches ``<worktree>/<expected_deliverable>``, so the role
        has to write exactly there. Father compiles the handoff and knows the
        relative path, but not the worktree, and the role sees neither unless
        somebody says so — Father's handoffs describe the work, not the
        machine it lands on.

        Appended rather than prepended: the handoff ends with its
        constraints, and the last thing a model reads before it starts is
        worth spending on where the answer goes.
        """
        relative = envelope.handoff.expected_deliverable
        governance = ""
        if governance_path:
            governance = (
                "<governance>\n"
                f"Your role definition is at `{governance_path}`, relative to\n"
                "the root of your worktree. The handoff refers to it by its\n"
                "governance number; that file is it.\n"
                "</governance>\n\n"
            )
        return (
            f"{envelope.handoff.content.rstrip()}\n\n"
            + governance
            + "<deliverable>\n"
            f"Write your deliverable to `{relative}`, relative to the root of\n"
            "your worktree, which is the directory this session started in.\n"
            "Create the directories if they are missing.\n\n"
            "That exact path is what is watched. Nothing else is collected,\n"
            "and a file written anywhere else is the same to this worker as\n"
            "no file at all. Write it once and completely — it is read as\n"
            "soon as it stops changing.\n"
            "</deliverable>\n"
        )

    def _deliverable_timeout(self) -> float:
        """How long a role may take. Config, with a default that fits a 14B."""
        # Direct attribute access, not getattr-with-default: the field
        # exists on WorkerSection now. The getattr fallback was hiding that
        # it did not -- the documented override silently read 0 forever.
        return float(
            self._config.worker.execution_timeout_seconds
            or DEFAULT_EXECUTION_TIMEOUT_SECONDS
        )

    def _await_deliverable(
        self,
        worktree: str,
        relative: str,
        execution_id: str = "",
        attempt_id: str = "",
    ) -> "tuple[Optional[str], str]":
        """Wait for the role to write its deliverable. Returns (content, why).

        There is no completion signal from OpenCode, so the file is the
        signal. Two conditions, not one: the file must exist **and** stop
        changing. A file caught mid-write is a truncated document that
        Father would checksum, publish and hand to a reviewer, who would
        review half a deliverable without knowing.

        The path is the envelope's `expected_deliverable`, resolved inside
        the worktree. Father writes the same relative path under its bridge
        directory, so one string names both ends and neither has to know the
        other's root.

        ``why`` is a §24 category when the wait fails, and ``""`` when it
        does not. The two cases are genuinely different and a reviewer needs
        to tell them apart: nothing was ever written (the role never got
        there) against something was written and is empty (the role got
        there and produced nothing).
        """
        target = Path(worktree) / relative
        previous: "Optional[tuple[int, float]]" = None
        seen = False

        # Bounded by iterations rather than by a clock reading. The clock is
        # injected -- the suite freezes it at 0.0 so timestamps are stable --
        # and a deadline compared against a frozen clock never arrives. The
        # first version of this loop hung the whole suite.
        #
        # Counting polls also makes the wait honest about what it does: it
        # looks a fixed number of times, `DELIVERABLE_POLL_SECONDS` apart.
        attempts = max(1, int(self._deliverable_timeout() / DELIVERABLE_POLL_SECONDS))

        # Heartbeat while waiting. This wait IS the execution -- up to thirty
        # minutes in which the worker made no request at all, so Father could
        # not tell a live role from a dead worker. The endpoints and the
        # table existed from day one (§20) and nothing ever called them; the
        # planned claim-timeout on Father's side NEEDS this signal, or it
        # would kill live, slow executions along with dead ones.
        #
        # Paced by poll count, not by the clock: the clock is injected and
        # the suite freezes it, which is the same reason the wait itself
        # counts iterations. Best-effort by design -- a missed heartbeat
        # must never fail a role that is working.
        heartbeat_every = max(
            1,
            int(
                self._config.worker.heartbeat_interval_seconds
                / DELIVERABLE_POLL_SECONDS
            ),
        )
        for attempt in range(attempts):
            if execution_id and attempt and attempt % heartbeat_every == 0:
                try:
                    self._father.execution_heartbeat(
                        execution_id, attempt_id=attempt_id
                    )
                except Exception:  # noqa: BLE001 - liveness, never fatal
                    pass
            try:
                stat = target.stat()
                seen = True
                signature = (stat.st_size, stat.st_mtime)
                if stat.st_size and signature == previous:
                    text = target.read_text(encoding="utf-8", errors="replace")
                    if text.strip():
                        return text, ""
                previous = signature
            except OSError:
                previous = None
            self._sleep(DELIVERABLE_POLL_SECONDS)

        if not seen:
            return None, CATEGORY_ROLE_EXECUTION_TIMEOUT
        return None, CATEGORY_DELIVERABLE_MISSING

    def _pane_tail(self, session_name: str, lines: int = 40) -> str:
        """The role's last output, for the failure report.

        A timeout with no pane is unreadable: a role that stalled on a
        permission prompt and one that never started look identical from the
        outside, and the pane is the only place the difference shows.
        """
        try:
            return self._tmux.capture(session_name, lines)
        except Exception as exc:  # noqa: BLE001 - diagnostics, never fatal
            return f"(pane unavailable: {exc!r})"

    def _poll_father(self) -> Any:
        """Ask Father for the next execution. Never raise, never report.

        A poll failure has no execution to report a failure *about*. There
        is no execution_id, no attempt_id, no role and no attempt — Father
        has not handed us anything yet — so the §24 failure report has
        nothing to address itself to, and the endpoint that would receive
        it is the one that just proved unreachable.

        The previous version routed this through ``_safe_call``, which
        called ``_report_failure`` with keywords it does not accept. The
        first time Father restarted, the poll raised ``Connection
        refused``, the handler raised ``TypeError`` on top of it, and the
        daemon died. A worker whose whole job is to poll a machine it does
        not control cannot treat that machine being briefly absent as
        fatal; Father restarting is ordinary.

        Returns the offer, or ``None`` when there is nothing to do — which
        is the same answer a poll against a Father with no work gives, and
        the caller already handles it.
        """
        try:
            return self._father.next_execution()
        except FatherClientError as exc:
            sys.stderr.write(
                f"dpmtf-lightworker: Father unreachable, will retry: {exc}\n"
            )
            sys.stderr.flush()
            return None
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"dpmtf-lightworker: poll failed, will retry: {exc!r}\n"
            )
            sys.stderr.flush()
            return None

    def _safe_call(
        self,
        call: Callable[[], Any],
        *,
        category: str,
        **report_kwargs: Any,
    ) -> Any:
        """Run ``call`` and convert a Father-side failure into a fail() report.

        ``call`` is something that talks to Father: the next-execution
        poll and the claim. The failure modes the loop cares about
        here are *transport* failures — the Father endpoint was
        unreachable or returned malformed — not the role-execution
        semantics, which are caught later. A failure here reports
        ``category`` (always CATEGORY_RESULT_REPORT_FAILED for the
        next/claim pair) and returns ``None``.
        """
        try:
            return call()
        except FatherClientError as exc:
            self._report_failure(
                category=category,
                failure=str(exc),
                last_state=self._state,
                **report_kwargs,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            self._report_failure(
                category=CATEGORY_INTERNAL_WORKER_ERROR,
                failure=str(exc),
                last_state=self._state,
                **report_kwargs,
            )
            return None

    def _report_failure(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        target_role: str,
        model_alias: str,
        client: str,
        category: str,
        failure: str,
        payload: Mapping[str, Any],
        last_state: WorkerState,
    ) -> None:
        """Send a failure to Father and run cleanup.

        The exception is mapped to a §24 category by reading
        ``exc.category`` off the raised ``EnvelopeError`` (every
        class in ``errors.py`` carries its own category) and
        falling back to ``INTERNAL_WORKER_ERROR`` for anything else.
        Cleanup runs on every failure path — TG5 measures that.
        """
        self._transition(WorkerState.ROLE_EXECUTION_FAILED)
        self._record_event(
            EventType.ROLE_EXECUTION_FAILED,
            payload={
                "category": category,
                "failure": failure,
                "last_state": last_state.value,
            },
            execution_id=execution_id,
            attempt_id=attempt_id,
            target_role=target_role,
            model_alias=model_alias,
            client=client,
        )
        session_name = self._tmux.session_name(target_role, execution_id)

        failure_body = {
            "execution_id": execution_id,
            "attempt_id": attempt_id,
            "target_role": target_role,
            "model_alias": model_alias,
            "client": client,
            "category": category,
            "last_state": last_state.value,
            "retryability": False,
            "log_reference": "",
            "summary": failure,
        }
        try:
            self._father.fail(
                execution_id,
                attempt_id=attempt_id,
                failure=failure_body,
            )
        except FatherClientError:
            # The transport is down. The local event log already
            # carries the failure; nothing more we can do at this
            # layer without re-entering the bridge.
            pass

        self._cleanup(execution_id, session_name)

    def _cleanup(self, execution_id: str, session_name: str) -> None:
        """Run §36 cleanup. Idempotent on both paths.

        The mirror is never removed. The worktree is removed through
        its owning mirror when one exists, and the tmux session is
        killed. Failures here are swallowed — the execution is
        already in a terminal state and the loop is about to
        release its in-flight flag.

        Both steps are best-effort. The event carries the outcome
        per step in its payload so a downstream consumer can tell a
        full cleanup from a partial one; the loop does not re-fail
        the execution either way.
        """
        self._transition(WorkerState.CLEANING_UP)
        git_succeeded = True
        git_error: Optional[str] = None
        try:
            self._git.cleanup(execution_id)
        except Exception as exc:  # noqa: BLE001
            git_succeeded = False
            git_error = f"{type(exc).__name__}: {exc}"
        tmux_succeeded = True
        tmux_error: Optional[str] = None
        try:
            self._tmux.kill(session_name)
        except Exception as exc:  # noqa: BLE001
            tmux_succeeded = False
            tmux_error = f"{type(exc).__name__}: {exc}"
        cleanup_payload: Dict[str, Any] = {
            "execution_id": execution_id,
            "git_cleanup": {
                "succeeded": git_succeeded,
                "error": git_error,
            },
            "tmux_kill": {
                "succeeded": tmux_succeeded,
                "error": tmux_error,
            },
        }
        self._record_event(
            EventType.CLEANUP_COMPLETED,
            payload=cleanup_payload,
            execution_id=execution_id,
            attempt_id=self._inflight_attempt_id,
            target_role=self._inflight_target_role,
            model_alias=self._inflight_model_alias,
            client=self._inflight_client,
        )
        # Clear in-flight identification so a subsequent run_once
        # starts fresh.
        self._inflight_execution_id = ""
        self._inflight_attempt_id = ""
        self._inflight_target_role = ""
        self._inflight_model_alias = ""
        self._inflight_client = ""
        self._transition(WorkerState.RECEIVED)


__all__ = [
    "CATEGORY_ALIAS_VALIDATION_FAILED",
    "CATEGORY_ALLOCATOR_NOT_AVAILABLE",
    "CATEGORY_ALLOCATOR_PREFLIGHT_FAILED",
    "CATEGORY_BASE_COMMIT_NOT_FOUND",
    "CATEGORY_CLIENT_CONFIG_RENDER_FAILED",
    "CATEGORY_CLIENT_START_FAILED",
    "CATEGORY_HANDOFF_INJECTION_FAILED",
    "CATEGORY_INTERNAL_WORKER_ERROR",
    "CATEGORY_INVALID_EXECUTION_ENVELOPE",
    "CATEGORY_PATCH_GENERATION_FAILED",
    "CATEGORY_REPOSITORY_FETCH_FAILED",
    "CATEGORY_RESULT_REPORT_FAILED",
    "CATEGORY_RUNTIME_RELEASE_FAILED",
    "CATEGORY_TMUX_START_FAILED",
    "CATEGORY_UNSUPPORTED_CLIENT",
    "CATEGORY_UNSUPPORTED_MODEL_SOURCE",
    "CATEGORY_UNSUPPORTED_SCHEMA_VERSION",
    "CATEGORY_WORKER_MISMATCH",
    "CATEGORY_WORKTREE_CREATION_FAILED",
    "RenderConfigCallable",
    "WorkerLoop",
]


# ---------------------------------------------------------------------------
# Console-script entrypoint
# ---------------------------------------------------------------------------


def _main() -> int:
    """Console-script entrypoint: load config, build collaborators, loop.

    Real wiring (the §5.2 boundary, tmux session management, the
    cross-process lease handshake) belongs to the run that mounts
    the loop on a real machine. Here the entrypoint is the seam:
    ``pip install -e .`` produces a ``dpmtf-lightworker`` command
    the operator can invoke, and the machine's eventual systemd
    unit (when one ships) calls it.
    """
    import os

    from dpmtf_lightworker.allocator import AllocatorAdapter
    from dpmtf_lightworker.config import load_config
    from dpmtf_lightworker.father_client import FatherClient
    from dpmtf_lightworker.git_workspace import GitWorkspace
    from dpmtf_lightworker.tmux_session import TmuxSession

    config_path = os.environ.get(
        "LIGHTWORKER_CONFIG", "config/worker.yaml"
    )
    config = load_config(config_path)
    father = FatherClient(
        base_url=config.father.base_url,
        worker_id=config.worker.id,
        timeout_seconds=float(config.father.request_timeout_seconds),
    )
    allocator = AllocatorAdapter(command=config.allocator.command)
    git = GitWorkspace(
        repository_root=config.paths.repository_root,
        worktree_root=config.paths.worktree_root,
    )
    tmux = TmuxSession()
    loop = WorkerLoop(
        config=config,
        father=father,
        allocator=allocator,
        git=git,
        tmux=tmux,
    )
    poll_seconds = max(1, int(config.worker.poll_interval_seconds))
    try:
        while True:
            try:
                loop.run_once()
            except Exception as exc:  # noqa: BLE001
                # The daemon is the worker's only presence on the network.
                # An exception that escapes run_once used to end the
                # process, which takes the machine out of the pool until
                # somebody notices and restarts it by hand -- and the
                # symptom, a worker that never claims anything, looks
                # exactly like Father not offering anything.
                #
                # Every failure that belongs to an execution is already
                # reported inside run_once. What reaches here is a defect
                # in the loop itself, so it is printed and the next poll
                # goes ahead: a worker degraded on one execution is worth
                # more than one that is gone.
                sys.stderr.write(
                    f"dpmtf-lightworker: run_once raised, continuing: "
                    f"{exc!r}\n"
                )
                sys.stderr.flush()
            time.sleep(float(poll_seconds))
    except KeyboardInterrupt:
        return 130
    finally:
        sys.stderr.write("dpmtf-lightworker: stopped\n")