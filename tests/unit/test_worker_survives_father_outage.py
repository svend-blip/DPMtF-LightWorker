"""The worker must outlive Father being briefly absent.

Father restarts. That is ordinary -- a config fix, a deploy, an operator.
The worker's whole job is to poll a machine it does not control, so an
unreachable Father is a normal reading of the network, not a fatal event.

lightworker run 001 proved it was fatal. Father was restarted; the poll
raised `Connection refused`; the handler for that raised `TypeError` on top
of it, because it called `_report_failure` with keywords the method does not
accept. The daemon died and stayed dead. The symptom an operator sees -- a
worker that never claims anything -- is indistinguishable from Father having
no work to offer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dpmtf_lightworker.father_client import FatherClientError  # noqa: E402
from tests.fakes import loop_with  # noqa: E402


def _breaking(loop, exc):
    """Point the loop's Father at something that always raises."""
    class Broken:
        def next_execution(self):
            raise exc
    loop._father = Broken()
    return loop


def test_an_unreachable_father_is_not_fatal():
    loop, _ = loop_with(offered=None)[:2]
    _breaking(loop, FatherClientError("Connection refused"))
    assert loop.run_once() is False


def test_it_keeps_polling_after_an_outage():
    """One refused poll must not end the worker's participation."""
    loop, _ = loop_with(offered=None)[:2]
    _breaking(loop, FatherClientError("Connection refused"))
    assert [loop.run_once() for _ in range(3)] == [False, False, False]


def test_the_worker_is_not_left_in_flight_after_a_failed_poll():
    """An execution was never claimed, so nothing is in flight. Leaving the
    flag set would make every later poll return early -- a worker that is
    alive, reachable, and permanently deaf."""
    loop, _ = loop_with(offered=None)[:2]
    _breaking(loop, FatherClientError("down"))
    loop.run_once()
    assert loop._in_flight is False


def test_an_unexpected_error_in_the_poll_is_not_fatal_either():
    """The category that actually killed it was not the transport error but
    the TypeError raised while handling it."""
    loop, _ = loop_with(offered=None)[:2]
    _breaking(loop, TypeError("unexpected keyword argument 'in_flight'"))
    assert loop.run_once() is False


def test_a_failed_poll_reports_nothing_to_father():
    """There is no execution_id, no attempt_id and no role yet, so a §24
    failure report has nothing to address itself to -- and the endpoint that
    would receive it is the one that just proved unreachable."""
    loop, spies = loop_with(offered=None)[:2]
    father = loop._father
    calls = []

    class Broken:
        def next_execution(self):
            raise FatherClientError("Connection refused")

        def fail(self, *a, **k):
            calls.append(("fail", a, k))

        def complete(self, *a, **k):
            calls.append(("complete", a, k))

    loop._father = Broken()
    loop.run_once()
    assert calls == []
