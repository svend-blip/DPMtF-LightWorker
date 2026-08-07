"""Test fakes for the LightWorker loop.

Every criterion does ``sys.path.insert(0, 'tests')`` and
``from fakes import ...``. The fakes live here so the criterion
asserts measure the loop's seams, not the fakes' internals.

``loop_with(offered=..., fail_at=...)`` returns ``(loop, spies)``.
``envelope()`` returns a valid §13 payload addressed to the
configured worker. The spies record exactly what the criteria read:

* ``spies['git'].calls``     — truthy when git was used
* ``spies['git'].cleaned``   — truthy when cleanup ran
* ``spies['tmux'].calls``    — truthy when tmux was used
* ``spies['tmux'].killed``   — truthy when the session was killed
* ``spies['allocator'].calls`` — truthy when the allocator was used
* ``spies['father'].claims`` — count of claim calls (TG8 asserts <= 1)
* ``spies['father'].completed`` — truthy when complete() reached Father
* ``spies['father'].failed``    — truthy when fail() reached Father
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Union

from dpmtf_lightworker.allocator import AllocatorAdapter
from dpmtf_lightworker.config import (
    AllocatorSection,
    FatherSection,
    NetworkSection,
    PathsSection,
    RetentionSection,
    WorkerConfig,
    WorkerSection,
)
from dpmtf_lightworker.git_workspace import GitWorkspace
from dpmtf_lightworker.tmux_session import TmuxSession
from dpmtf_lightworker.worker import WorkerLoop


WORKER_ID: str = "svend3060"


def envelope() -> Dict[str, Any]:
    """A minimal but valid §13 payload addressed to ``WORKER_ID``.

    Every §13 rejection rule is satisfied. The flow/step/role
    identifiers are placeholders; the criteria only care that the
    envelope is valid.
    """
    return {
        "schema_version": "1",
        "execution_id": "EXEC-1-IMPLE01",
        "job_id": "JOB-1",
        "handoff_id": "HOFF-1",
        "attempt_id": "ATTEMPT-1",
        "flow_key": "preferred_cloud",
        "step_key": "archi01-imple01",
        "source_role": "archi01",
        "target_role": "imple01",
        "worker_id": WORKER_ID,
        "model_source": "model_allocator",
        "model_alias": "imple01-3060",
        "client": "opencode",
        "repository": {
            "project_key": "dpmtf-lightworker",
            "clone_url": "https://example.invalid/dpmtf-lightworker.git",
            "base_commit": "0" * 40,
        },
        "handoff": {
            "content": "compiled handoff body",
            "governance_content": "compiled governance body",
            "expected_deliverable": "docs/dpmtf/403_IMPLEMENTATION.md",
        },
        "result_contract": {
            "mode": "patch_and_deliverable",
            "tests_required": True,
            "local_result_commit_required": True,
        },
    }


# ---------------------------------------------------------------------------
# `fail_at` normalisation
# ---------------------------------------------------------------------------
#
# `fail_at` accepts either a single string or an iterable of strings,
# so a single ``loop_with(fail_at=["cleanup", "kill"])`` can drive
# multiple collaborators to failure in one run. A bare string is itself
# iterable — ``"cleanup"`` iterates character-by-character — so the
# value is normalised into a frozenset at the point it enters each
# fake, before any membership test. ``None`` becomes an empty set,
# which means "nothing fails" — the existing behaviour.


FailAtInput = Union[None, str, Iterable[str]]


def _normalise_fail_at(fail_at: FailAtInput) -> FrozenSet[str]:
    if fail_at is None:
        return frozenset()
    if isinstance(fail_at, str):
        return frozenset((fail_at,))
    return frozenset(fail_at)


# ---------------------------------------------------------------------------
# Spy storage
# ---------------------------------------------------------------------------


@dataclass
class GitSpy:
    """What the criteria read off ``spies['git']``.

    ``calls`` is a list of method names that were invoked (truthy
    when used, falsy when not). ``cleaned`` counts cleanup calls.
    """
    calls: List[str] = field(default_factory=list)
    cleaned: int = 0

    def __bool__(self) -> bool:
        return bool(self.calls)


@dataclass
class TmuxSpy:
    """What the criteria read off ``spies['tmux']``."""
    calls: List[str] = field(default_factory=list)
    killed: int = 0

    def __bool__(self) -> bool:
        return bool(self.calls)


@dataclass
class AllocatorSpy:
    """What the criteria read off ``spies['allocator']``."""
    calls: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.calls)


@dataclass
class FatherSpy:
    """What the criteria read off ``spies['father']``."""
    claims: int = 0
    completed: int = 0
    failed: int = 0

    def __bool__(self) -> bool:
        return self.claims + self.completed + self.failed > 0


# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------


class FakeFather:
    """A stand-in FatherClient that records what the loop called.

    The ``offered`` argument to ``loop_with`` controls what
    ``next_execution()`` returns. The counters ``claims``,
    ``completed`` and ``failed`` are exposed directly because the
    criteria read them by attribute name — no spy wrapper needed.
    """

    def __init__(self, offered: Optional[Dict[str, Any]]) -> None:
        self._offered = offered
        self.claims: int = 0
        self.completed: int = 0
        self.failed: int = 0
        self.results: List[Any] = []

    def next_execution(self) -> Any:
        return self._offered

    def claim(self, execution_id: str) -> Dict[str, Any]:
        self.claims += 1
        return {"status": "claimed", "execution_id": execution_id}

    def heartbeat(self) -> Any:
        return None

    def register(self, *, capabilities: Any) -> Any:
        return None

    def execution_heartbeat(self, execution_id: str, *, attempt_id: str) -> Any:
        return None

    def report_event(self, execution_id: str, *, event: Any) -> Any:
        return None

    def complete(
        self, execution_id: str, *, attempt_id: str, result: Any
    ) -> Any:
        self.completed += 1
        # Kept, not just counted. The result's *shape* is what disagreed with
        # Father's return path, and a counter cannot see a shape.
        self.results.append(result)
        return {"status": "completed", "execution_id": execution_id}

    def fail(
        self, execution_id: str, *, attempt_id: str, failure: Any
    ) -> Any:
        self.failed += 1
        return {"status": "failed", "execution_id": execution_id}


class FakeGit(GitWorkspace):
    """A git workspace that records calls and returns synthetic paths.

    ``calls`` records the *real* work methods (ensure_mirror,
    create_worktree, generate_patch, commit_result) — not cleanup.
    Cleanup is recorded in ``cleaned`` instead, because the criteria
    distinguish "did work" from "tore down".

    ``fail_at`` is normalised into a ``frozenset`` at construction;
    each method tests ``name in self._fail_set``. A bare string is
    accepted for backward compatibility — every existing criterion
    passes a single string.
    """

    def __init__(
        self, *, fail_at: FailAtInput = None
    ) -> None:
        import tempfile

        tmp = tempfile.mkdtemp(prefix="dpmtf-fake-git-")
        self.deliverable_relative = "docs/dpmtf/403_IMPLEMENTATION.md"
        self.deliverable_content = "# Implementation\n\nWhat the role wrote.\n"
        self.no_deliverable = False
        super().__init__(
            repository_root=tmp,
            worktree_root=tmp,
            runner=lambda argv, cwd: (0, "", ""),
        )
        self.calls: List[str] = []
        self.cleaned: int = 0
        self._fail_set: FrozenSet[str] = _normalise_fail_at(fail_at)

    def ensure_mirror(self, project_key: str, clone_url: str) -> Path:
        self.calls.append("ensure_mirror")
        if "ensure_mirror" in self._fail_set:
            from dpmtf_lightworker.errors import RepositoryFetchFailed
            raise RepositoryFetchFailed("fake: ensure_mirror failed")
        return Path(self._repository_root) / f"{project_key}.git"

    def create_worktree(
        self, execution_id: str, project_key: str, base_commit: str
    ) -> Path:
        self.calls.append("create_worktree")
        if "create_worktree" in self._fail_set:
            from dpmtf_lightworker.errors import WorktreeCreationFailed
            raise WorktreeCreationFailed("fake: create_worktree failed")
        worktree = Path(self._worktree_root) / execution_id
        # A worktree with the role's deliverable already in it. The loop now
        # waits for that file and reads it, so a fake worktree without one
        # models a role that produced nothing -- which is a real case, but
        # not the one the success criteria are about.
        #
        # `no_deliverable` leaves it out on purpose, for the criteria that
        # measure the timeout.
        if not self.no_deliverable:
            target = worktree / self.deliverable_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.deliverable_content, encoding="utf-8")
        return worktree

    def commit_result(
        self, worktree: str, message: str
    ) -> Optional[str]:
        self.calls.append("commit_result")
        return None

    def generate_patch(self, worktree: str, base_commit: str) -> str:
        self.calls.append("generate_patch")
        if "generate_patch" in self._fail_set:
            from dpmtf_lightworker.errors import PatchGenerationFailed
            raise PatchGenerationFailed("fake: generate_patch failed")
        return "diff --git a/x b/x"

    def cleanup(self, execution_id: str) -> None:
        # Deliberately do NOT record this in ``calls`` — the criteria
        # distinguish "did work" from "tore down".
        #
        # Increment the counter *before* raising so TG5's
        # ``spies['git'].cleaned`` assertion still holds when a
        # cleanup failure is being exercised; the loop records
        # the failure in the CLEANUP_COMPLETED event payload.
        self.cleaned += 1
        if "cleanup" in self._fail_set:
            raise RuntimeError("fake: git.cleanup failed")


class FakeTmux(TmuxSession):
    """A tmux session transport that records calls and never invokes tmux."""

    def __init__(
        self, *, fail_at: FailAtInput = None
    ) -> None:
        super().__init__(runner=lambda argv: (0, "", ""))
        self.calls: List[str] = []
        self.killed: int = 0
        self._fail_set: FrozenSet[str] = _normalise_fail_at(fail_at)

    def session_name(self, role: str, execution_id: str) -> str:
        self.calls.append("session_name")
        return super().session_name(role, execution_id)

    def exists(self, name: str) -> bool:
        return False

    def create(self, name: str, cwd: str) -> None:
        self.calls.append("create")
        if "create" in self._fail_set:
            from dpmtf_lightworker.errors import TmuxStartFailed
            raise TmuxStartFailed("fake: tmux create failed")

    def launch(self, name: str, command: str) -> None:
        self.calls.append("launch")
        if "launch" in self._fail_set:
            from dpmtf_lightworker.errors import ClientStartFailed
            raise ClientStartFailed("fake: tmux launch failed")

    def inject(self, name: str, text: str) -> None:
        self.calls.append("inject")
        if "inject" in self._fail_set:
            from dpmtf_lightworker.errors import HandoffInjectionFailed
            raise HandoffInjectionFailed("fake: tmux inject failed")

    def wait_ready(self, name: str, marker: str, timeout: float) -> bool:
        self.calls.append("wait_ready")
        return True

    def capture(self, name: str, lines: int) -> str:
        self.calls.append("capture")
        return ""

    def kill(self, name: str) -> None:
        # Increment the counter *before* raising so TG5's
        # ``spies['tmux'].killed`` assertion still holds when a
        # kill failure is being exercised; the loop records the
        # failure in the CLEANUP_COMPLETED event payload.
        self.killed += 1
        if "kill" in self._fail_set:
            raise RuntimeError("fake: tmux.kill failed")


class FakeAllocator(AllocatorAdapter):
    """An allocator that records calls and never invokes a subprocess."""

    def __init__(
        self, *, fail_at: FailAtInput = None
    ) -> None:
        super().__init__(
            command="model-allocator",
            runner=lambda argv, timeout: (0, "ok", ""),
            timeout=30.0,
        )
        self.calls: List[str] = []
        self._fail_set: FrozenSet[str] = _normalise_fail_at(fail_at)

    def preflight(self, role: str, client: str) -> str:
        self.calls.append("preflight")
        if "preflight" in self._fail_set:
            from dpmtf_lightworker.errors import AllocatorPreflightFailed
            raise AllocatorPreflightFailed("fake: preflight failed")
        return "ok"

    def validate(self, alias: str, client: str) -> str:
        self.calls.append("validate")
        if "validate" in self._fail_set:
            from dpmtf_lightworker.errors import AliasValidationFailed
            raise AliasValidationFailed("fake: validate failed")
        return "ok"

    def render_config(self, role: str, client: str, output: str) -> str:
        self.calls.append("render_config")
        if "render_config" in self._fail_set:
            from dpmtf_lightworker.errors import ClientConfigRenderFailed
            raise ClientConfigRenderFailed("fake: render_config failed")
        # Write a syntactically-valid JSON the validator accepts.
        import json
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(
            json.dumps(
                {
                    "permission": {},
                    "mcp": {},
                    "model": "fake",
                    "provider": {"id": "fake"},
                }
            ),
            encoding="utf-8",
        )
        return output

    def run(self, role: str, client: str) -> str:
        self.calls.append("run")
        if "run" in self._fail_set:
            from dpmtf_lightworker.errors import AllocatorNotAvailable
            raise AllocatorNotAvailable("fake: run failed")
        return "echo hello"

    def release(
        self, alias: str, policy: str, other_lease_holders: int = 0
    ) -> Optional[str]:
        self.calls.append("release")
        if "release" in self._fail_set:
            from dpmtf_lightworker.errors import RuntimeReleaseFailed
            raise RuntimeReleaseFailed("fake: allocator release failed")
        return None


def _build_config() -> WorkerConfig:
    """A WorkerConfig that satisfies the loop's uses of it.

    The validator needs an absolute ``repository_root``; we point
    it at a real directory. The other path fields can be anything
    the loop does not use.
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix="dpmtf-fake-cfg-")
    return WorkerConfig(
        worker=WorkerSection(
            id=WORKER_ID,
            display_name="fake",
            max_parallel_executions=1,
            poll_interval_seconds=10,
            heartbeat_interval_seconds=15,
        ),
        father=FatherSection(
            base_url="http://example.invalid",
            request_timeout_seconds=30,
        ),
        network=NetworkSection(
            type="tailscale", expected_father_host="example.invalid"
        ),
        allocator=AllocatorSection(
            command="model-allocator",
            client="opencode",
            config_root=tmp,
        ),
        paths=PathsSection(
            repository_root=tmp,
            worktree_root=tmp,
            artifact_root=tmp,
            log_root=tmp,
            opencode_config_root=tmp,
        ),
        retention=RetentionSection(
            successful_worktree_hours=1,
            failed_worktree_days=7,
            artifact_days=14,
            log_days=30,
        ),
    )


def loop_with(
    *,
    offered: Optional[Dict[str, Any]],
    fail_at: FailAtInput = None,
    clock: Optional[Callable[[], float]] = None,
    no_deliverable: bool = False,
) -> tuple:
    """Build a ``WorkerLoop`` plus the spies the criteria read.

    ``fail_at`` is the method name on the relevant collaborator that
    will raise. The mapping is by method name because the criteria
    use the same string ("create_worktree", "inject", ...) to mean
    the same thing. A string or any iterable of strings is accepted
    so a single run can drive multiple collaborators to failure;
    every fake tests membership in its normalised set.
    """
    father = FakeFather(offered)
    git = FakeGit(fail_at=fail_at)
    if no_deliverable:
        git.no_deliverable = True
    tmux = FakeTmux(fail_at=fail_at)
    allocator = FakeAllocator(fail_at=fail_at)
    config = _build_config()

    loop = WorkerLoop(
        config=config,
        father=father,
        allocator=allocator,
        git=git,
        tmux=tmux,
        clock=clock or (lambda: 0.0),
        # No real waiting. The wait for a deliverable polls
        # DELIVERABLE_POLL_SECONDS apart, which is right on a worker and
        # thirty minutes in a test suite.
        sleep=lambda _seconds: None,
    )

    spies = {
        "git": git,
        "tmux": tmux,
        "allocator": allocator,
        "father": father,
    }
    return loop, spies