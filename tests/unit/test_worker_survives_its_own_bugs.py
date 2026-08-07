"""An unexpected exception mid-sequence must not jam the queue.

Every step in the sequence catches `EnvelopeError` -- the failures it
expects. Anything else used to propagate out, get logged by `_main`'s
catch-all, and leave the execution `claimed` forever on Father's side. A
claimed execution blocks every future offer to that worker, so one malformed
JSON file from a collaborator would have jammed the queue permanently --
exactly the way EXEC-013 did, through a different door.

§24 has a name for a bug in the loop itself: INTERNAL_WORKER_ERROR. It was
defined on day one and used only in `_safe_call`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests.fakes import envelope, loop_with  # noqa: E402


def _boom(*a, **k):
    raise RuntimeError("a bug the sequence never anticipated")


def test_an_unexpected_exception_is_reported_not_leaked():
    """The execution must reach a terminal state on Father's side."""
    loop, spies = loop_with(offered=envelope())
    loop._render_config = _boom
    loop.run_once()  # must not raise
    assert spies["father"].failed == 1, "the execution would stay claimed"


def test_it_is_reported_as_an_internal_worker_error():
    """§24's category for loop bugs, not a borrowed one."""
    loop, spies = loop_with(offered=envelope())
    loop._render_config = _boom
    loop.run_once()
    failed = [e for e in loop._events
              if e.event_type.value == "ROLE_EXECUTION_FAILED"]
    assert failed
    assert failed[-1].payload["category"] == "INTERNAL_WORKER_ERROR"


def test_cleanup_still_runs():
    """The worktree and tmux session must not outlive the failure."""
    loop, spies = loop_with(offered=envelope())
    loop._render_config = _boom
    loop.run_once()
    assert spies["tmux"].killed >= 1 or spies["git"].cleaned


def test_the_worker_is_not_left_in_flight():
    loop, spies = loop_with(offered=envelope())
    loop._render_config = _boom
    loop.run_once()
    assert loop._in_flight is False
