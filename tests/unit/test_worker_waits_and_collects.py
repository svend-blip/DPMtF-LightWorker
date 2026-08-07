"""The worker waits for the role and returns what it wrote.

Until this existed, `run_once` injected the handoff and reported immediately.
The first live execution took eleven seconds from claim to an empty result,
with the model still starting -- and Father, correctly, refused it.

There is no completion signal from OpenCode, so the deliverable is the signal.
Two conditions, not one: the file must exist AND stop changing. A file caught
mid-write is a truncated document Father would checksum, publish, and hand to
a reviewer who reviews half a deliverable without knowing.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dpmtf_lightworker.father_client import FatherClientError  # noqa: E402
from tests.fakes import envelope, loop_with  # noqa: E402

# The §24 categories are imported inside the two tests that name them, not at
# module level. Imported here, a version without them fails COLLECTION, and
# every other test in the file reports an ImportError instead of what it
# actually measures -- which would hide whether the result-shape and
# refusal tests discriminate at all.


def short(loop, seconds):
    """Shorten the wait. WorkerSection is frozen, so the timeout is overridden
    at the accessor rather than in the config object."""
    loop._deliverable_timeout = lambda: float(seconds)
    return loop


class TestTheWait:

    def test_it_returns_what_the_role_wrote(self, tmp_path):
        loop = loop_with(offered=None)[0]
        target = tmp_path / "docs" / "out.md"
        target.parent.mkdir(parents=True)
        target.write_text("a document\n", encoding="utf-8")
        content, why = loop._await_deliverable(str(tmp_path), "docs/out.md")
        assert content == "a document\n"
        assert why == ""

    def test_nothing_written_is_a_timeout_not_a_missing_deliverable(self, tmp_path):
        """Two different facts. Nothing was ever written means the role never
        got there; a reviewer needs to tell that apart from a role that got
        there and produced nothing."""
        loop = short(loop_with(offered=None)[0], 10)
        from dpmtf_lightworker.worker import CATEGORY_ROLE_EXECUTION_TIMEOUT
        content, why = loop._await_deliverable(str(tmp_path), "docs/out.md")
        assert content is None
        assert why == CATEGORY_ROLE_EXECUTION_TIMEOUT

    def test_an_empty_file_is_a_missing_deliverable(self, tmp_path):
        loop = short(loop_with(offered=None)[0], 10)
        target = tmp_path / "out.md"
        target.write_text("   \n", encoding="utf-8")
        from dpmtf_lightworker.worker import CATEGORY_DELIVERABLE_MISSING
        content, why = loop._await_deliverable(str(tmp_path), "out.md")
        assert content is None
        assert why == CATEGORY_DELIVERABLE_MISSING

    def test_a_file_still_being_written_is_not_returned_early(self, tmp_path):
        """The property that protects a reviewer from half a document. The
        file grows on every poll and is never stable, so the wait must run
        out rather than publish a prefix."""
        loop = short(loop_with(offered=None)[0], 15)
        target = tmp_path / "out.md"
        target.write_text("start", encoding="utf-8")

        grown = {"n": 0}

        def grow(_seconds):
            grown["n"] += 1
            target.write_text("start" + "x" * grown["n"], encoding="utf-8")

        loop._sleep = grow
        content, why = loop._await_deliverable(str(tmp_path), "out.md")
        assert content is None, "a growing file was published as finished"
        assert grown["n"] > 1


class TestTheResultItReports:

    def test_the_content_travels_inline_with_its_checksum(self):
        """Father has no artifact transfer, so a result naming a path Father
        cannot reach is a result Father cannot use."""
        loop, spies = loop_with(offered=envelope())
        assert loop.run_once() is True
        result = spies["father"].results[-1]
        deliverable = result["deliverable"]
        assert deliverable["content"].strip()
        assert deliverable["sha256"] == hashlib.sha256(
            deliverable["content"].encode("utf-8")).hexdigest()

    def test_it_speaks_fathers_vocabulary(self):
        """`mode` and a bare path were accepted by Father's endpoint and
        refused by the return path that writes the file."""
        loop, spies = loop_with(offered=envelope())
        loop.run_once()
        result = spies["father"].results[-1]
        assert "result_mode" in result and "mode" not in result
        assert result["status"] == "role_execution_completed"
        assert isinstance(result["deliverable"], dict)


class TestARefusedResultIsNotAWorkerFailure:

    def test_a_422_still_terminates_the_execution(self):
        """A refused completion MUST be reported.

        An earlier version stopped without reporting, reasoning that the
        execution was already terminal so the report could not land. That
        holds only when the completion was recorded before being judged. A
        completion the endpoint's own validation rejects is refused BEFORE
        anything is recorded, so the execution stays `claimed` forever -- and
        a claimed execution blocks every future offer to this worker.

        EXEC-013 jammed the queue exactly that way: the role had produced its
        deliverable, Father refused it on a stale validator, and nothing on
        either side could close it.
        """
        loop, spies = loop_with(offered=envelope())
        father = spies["father"]

        def refuse(*a, **k):
            raise FatherClientError("refused with 422: nope", status=422)

        father.complete = refuse
        loop.run_once()
        assert father.failed == 1, "the execution would stay claimed forever"

    def test_a_refusal_is_recorded_as_a_refusal_not_a_worker_fault(self):
        """Both reach Father as a failure; the record must still say which."""
        loop, spies = loop_with(offered=envelope())

        def refuse(*a, **k):
            raise FatherClientError("refused with 422: nope", status=422)

        spies["father"].complete = refuse
        loop.run_once()
        failed = [e for e in loop._events
                  if e.event_type.value == "ROLE_EXECUTION_FAILED"]
        assert failed and "refused" in failed[-1].payload["failure"].lower()

    def test_a_500_still_produces_a_failure_report(self):
        """Father broken is not Father deciding."""
        loop, spies = loop_with(offered=envelope())
        father = spies["father"]

        def broken(*a, **k):
            raise FatherClientError("boom", status=500)

        father.complete = broken
        loop.run_once()
        assert father.failed == 1

    def test_an_unreachable_father_still_produces_a_failure_report(self):
        loop, spies = loop_with(offered=envelope())
        father = spies["father"]

        def gone(*a, **k):
            raise FatherClientError("Connection refused")

        father.complete = gone
        loop.run_once()
        assert father.failed == 1


class TestTheRoleIsToldWhereToWrite:
    """The wait watches one exact path. A role that writes anywhere else has,
    from this worker's side, written nothing.

    Father compiles the handoff and knows the relative path; it does not know
    the worktree, and the role sees neither unless the worker says so.
    """

    def test_the_injected_handoff_names_the_deliverable_path(self):
        loop, spies = loop_with(offered=envelope())
        loop.run_once()
        injected = spies["tmux"].injected[-1]
        assert "docs/dpmtf/403_IMPLEMENTATION.md" in injected

    def test_the_original_handoff_survives_intact(self):
        """Appending must not cost the role its instructions."""
        loop, spies = loop_with(offered=envelope())
        loop.run_once()
        assert "compiled handoff body" in spies["tmux"].injected[-1]


class TestTheClientIsReadyBeforeTheHandoffGoesIn:
    """`launch` returns when tmux has typed the command, not when the TUI can
    receive one. OpenCode takes seconds more to come up, and a handoff typed
    into a terminal that is not listening is simply lost.

    lightworker run 001 saw exactly that: the pane showed OpenCode at an empty
    prompt while Father waited on an execution that would never report.
    `wait_ready` had existed in the transport since it was built, and nothing
    called it.
    """

    def test_it_waits_before_injecting(self):
        loop, spies = loop_with(offered=envelope())
        loop.run_once()
        calls = spies["tmux"].calls
        assert "wait_ready" in calls, "the handoff went in without waiting"
        assert calls.index("wait_ready") < calls.index("inject")

    def test_a_client_that_never_shows_its_prompt_is_a_failure(self):
        """Injecting anyway and hoping leaves Father waiting forever on an
        execution whose role never read anything."""
        loop, spies = loop_with(offered=envelope())
        spies["tmux"].wait_ready = lambda *a, **k: False
        loop.run_once()
        assert spies["father"].failed == 1
        assert "inject" not in spies["tmux"].calls


class TestTheRoleCanReadItsOwnGovernance:
    """Father compiles the governance into every envelope and the worker
    discarded it.

    Every handoff opens with "Read 481_LIGHTWORKER_IMPLE01LW.md -- it is in
    this envelope", and the file was nowhere on the machine. EXEC-009 is what
    that costs: the model looked for its role definition, did not find it,
    and improvised. The pane's objective had drifted to "conduct a security
    audit", which appears in no handoff ever sent.

    Written as a file rather than injected: the whole governance document
    plus the handoff does not fit in an 8k window.
    """

    def test_the_governance_lands_in_the_worktree(self):
        loop, spies = loop_with(offered=envelope())
        loop.run_once()
        worktree = Path(spies["git"]._worktree_root) / "EXEC-1-IMPLE01"
        written = list(worktree.glob(".lightworker/*governance*.md"))
        assert written, "the governance was carried and thrown away"
        assert "compiled governance body" in written[0].read_text(
            encoding="utf-8")

    def test_the_role_is_told_where_it_is(self):
        """A file the role cannot find is the same as no file."""
        loop, spies = loop_with(offered=envelope())
        loop.run_once()
        assert ".lightworker/" in spies["tmux"].injected[-1]

    def test_an_envelope_without_governance_still_runs(self):
        """Not every role has one, and a missing governance is not a reason
        to refuse work that already arrived."""
        env = envelope()
        env["handoff"]["governance_content"] = ""
        loop, spies = loop_with(offered=env)
        assert loop.run_once() is True
        assert "<governance>" not in spies["tmux"].injected[-1]


class TestTheWorkerStaysAudibleWhileWaiting:
    """The wait IS the execution: up to thirty minutes in which the worker
    used to make no request at all, so Father could not tell a live role from
    a dead worker. §20's heartbeat endpoints and table existed from day one
    and nothing called them.

    The claim-timeout Father needs next DEPENDS on this signal -- without it,
    a timeout kills live, slow executions along with dead ones.
    """

    def test_heartbeats_are_sent_during_a_long_wait(self, tmp_path):
        loop, spies = loop_with(offered=None)
        loop._deliverable_timeout = lambda: 60.0  # 12 polls, beat every 3rd
        loop._await_deliverable(str(tmp_path), "out.md", "EXEC-X", "ATTEMPT-1")
        assert spies["father"].execution_heartbeats >= 3

    def test_no_execution_id_means_no_heartbeats(self, tmp_path):
        """The bare two-argument call is still legal and must not invent an
        execution to report on -- the poll path taught that lesson."""
        loop, spies = loop_with(offered=None)
        loop._deliverable_timeout = lambda: 60.0
        loop._await_deliverable(str(tmp_path), "out.md")
        assert spies["father"].execution_heartbeats == 0

    def test_a_failing_heartbeat_does_not_fail_the_wait(self, tmp_path):
        """Liveness reporting must never kill a live role."""
        loop, spies = loop_with(offered=None)
        loop._deliverable_timeout = lambda: 60.0

        def boom(*a, **k):
            raise FatherClientError("Father blinked")

        spies["father"].execution_heartbeat = boom
        target = tmp_path / "out.md"
        target.write_text("the deliverable\n", encoding="utf-8")
        content, why = loop._await_deliverable(
            str(tmp_path), "out.md", "EXEC-X", "ATTEMPT-1")
        assert content == "the deliverable\n"

    def test_the_full_run_heartbeats_through_the_loop(self):
        """End to end: run_once with a deliverable that exists passes the
        ids through, so a real execution is audible, not only the helper."""
        loop, spies = loop_with(offered=envelope())
        loop.run_once()
        # The fixture's deliverable exists immediately, so the wait is two
        # polls and no beat fires -- what matters is that the ids reached
        # the wait. Starve the deliverable to see beats:
        loop2, spies2 = loop_with(offered=envelope(), no_deliverable=True)
        loop2._deliverable_timeout = lambda: 60.0
        loop2.run_once()
        assert spies2["father"].execution_heartbeats >= 3
