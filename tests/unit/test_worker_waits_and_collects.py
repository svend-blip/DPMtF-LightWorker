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

    def test_a_422_does_not_produce_a_failure_report(self):
        """Father answering 'no' is Father working. Reporting a failure
        against it cannot even land -- the execution is terminal on Father's
        side by then, which came back 500."""
        loop, spies = loop_with(offered=envelope())
        father = spies["father"]

        def refuse(*a, **k):
            raise FatherClientError("refused with 422: nope", status=422)

        father.complete = refuse
        loop.run_once()
        assert father.failed == 0

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
