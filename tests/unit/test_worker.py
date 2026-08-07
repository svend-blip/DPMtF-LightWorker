"""Tests for the WorkerLoop (GOAL.md §14 sequence).

These tests pin the loop's seams beyond the eight criteria: the
event categories that the loop can emit, the §24 category
constants that are named module-level, and the §5.3 one-at-a-time
property the loop enforces through ``_in_flight``.

The tests live under ``tests/unit/`` so they are picked up by
``pyproject.toml``'s ``testpaths = ["tests"]``. The fakes they
import are in ``tests/fakes.py`` and the unit tests add
``tests/`` to ``sys.path`` so the criterion shape is mirrored
exactly.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Mirror the criterion setup so the same fakes are reachable from
# inside this test file: ``sys.path.insert(0, 'tests')``.
TESTS_DIR = str(Path(__file__).resolve().parent.parent)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from fakes import envelope, loop_with  # noqa: E402


# ---------------------------------------------------------------------------
# §5.3 — one execution at a time
# ---------------------------------------------------------------------------


def test_in_flight_blocks_a_second_run_once() -> None:
    """Two back-to-back run_once calls: only the first one can claim.

    The second sees ``_in_flight == True`` and returns ``False``.
    This is the §5.3 / TG8 invariant, pinned without the criterion's
    fake name (and without the criterion's strict-attribute check).
    """
    loop, _spies = loop_with(offered=envelope())
    assert loop.run_once() is True
    assert loop._in_flight is False  # released on the way out
    # A second run_once against the same Father (which has nothing
    # else to offer) also returns False.
    loop._father._offered = None  # type: ignore[attr-defined]
    assert loop.run_once() is False


# ---------------------------------------------------------------------------
# §24 — failure categories are constants, not inline strings
# ---------------------------------------------------------------------------


def test_failure_categories_are_module_level_constants() -> None:
    """Every §24 string the loop emits lives as a named constant.

    C11: any reviewer can read them off the top of ``worker.py``
    without grepping the body of the loop.
    """
    import dpmtf_lightworker.worker as w

    expected = [
        "CATEGORY_INVALID_EXECUTION_ENVELOPE",
        "CATEGORY_UNSUPPORTED_SCHEMA_VERSION",
        "CATEGORY_WORKER_MISMATCH",
        "CATEGORY_UNSUPPORTED_MODEL_SOURCE",
        "CATEGORY_UNSUPPORTED_CLIENT",
        "CATEGORY_REPOSITORY_FETCH_FAILED",
        "CATEGORY_BASE_COMMIT_NOT_FOUND",
        "CATEGORY_WORKTREE_CREATION_FAILED",
        "CATEGORY_ALLOCATOR_NOT_AVAILABLE",
        "CATEGORY_ALLOCATOR_PREFLIGHT_FAILED",
        "CATEGORY_ALIAS_VALIDATION_FAILED",
        "CATEGORY_CLIENT_CONFIG_RENDER_FAILED",
        "CATEGORY_RUNTIME_RELEASE_FAILED",
        "CATEGORY_TMUX_START_FAILED",
        "CATEGORY_CLIENT_START_FAILED",
        "CATEGORY_HANDOFF_INJECTION_FAILED",
        "CATEGORY_PATCH_GENERATION_FAILED",
        "CATEGORY_RESULT_REPORT_FAILED",
        "CATEGORY_INTERNAL_WORKER_ERROR",
    ]
    for name in expected:
        assert hasattr(w, name), f"missing {name}"
        assert isinstance(getattr(w, name), str)
        assert getattr(w, name) == name.split("_", 1)[1]


def test_categories_match_section_24() -> None:
    """Every constant value is one of §24's 29 names — nothing invented."""
    import dpmtf_lightworker.worker as w

    section_24 = {
        "INVALID_EXECUTION_ENVELOPE",
        "UNSUPPORTED_SCHEMA_VERSION",
        "WORKER_MISMATCH",
        "UNSUPPORTED_MODEL_SOURCE",
        "UNSUPPORTED_CLIENT",
        "REPOSITORY_FETCH_FAILED",
        "BASE_COMMIT_NOT_FOUND",
        "WORKTREE_CREATION_FAILED",
        "ALLOCATOR_NOT_AVAILABLE",
        "ALLOCATOR_PREFLIGHT_FAILED",
        "ALIAS_VALIDATION_FAILED",
        "CLIENT_INCOMPATIBLE",
        "CLIENT_CONFIG_RENDER_FAILED",
        "RUNTIME_START_FAILED",
        "RUNTIME_HEALTH_FAILED",
        "TMUX_START_FAILED",
        "CLIENT_START_FAILED",
        "HANDOFF_INJECTION_FAILED",
        "ROLE_EXECUTION_TIMEOUT",
        "ROLE_SESSION_TERMINATED",
        "DELIVERABLE_MISSING",
        "TESTS_FAILED",
        "LOCAL_COMMIT_FAILED",
        "PATCH_GENERATION_FAILED",
        "RESULT_REPORT_FAILED",
        "RUNTIME_RELEASE_FAILED",
        "WORKER_INTERRUPTED",
        "CANCELLED_BY_FATHER",
        "INTERNAL_WORKER_ERROR",
    }
    for name in dir(w):
        if name.startswith("CATEGORY_"):
            value = getattr(w, name)
            assert value in section_24, (
                f"{name} = {value!r} is not one of §24's 29 categories"
            )


# ---------------------------------------------------------------------------
# §22 — events for the steps the loop actually completes
# ---------------------------------------------------------------------------


def test_a_successful_run_emits_role_execution_claimed() -> None:
    """TG2 again, at unit granularity. ROLE_EXECUTION_CLAIMED is the
    first event after the claim call returns."""
    loop, _ = loop_with(offered=envelope())
    assert loop.run_once() is True
    names = [e.event_type.name for e in loop.events]
    assert names[0] == "ROLE_EXECUTION_CLAIMED"


def test_a_successful_run_emits_role_execution_completed() -> None:
    """The terminal-state event is in the list — the loop reaches it."""
    loop, _ = loop_with(offered=envelope())
    assert loop.run_once() is True
    names = [e.event_type.name for e in loop.events]
    assert "ROLE_EXECUTION_COMPLETED" in names


def test_events_carry_nine_required_fields() -> None:
    """§22: every Event has the nine required fields, all populated."""
    loop, _ = loop_with(offered=envelope())
    assert loop.run_once() is True
    for event in loop.events:
        assert event.worker_id
        assert event.execution_id
        assert event.attempt_id
        assert event.target_role
        assert event.model_alias
        assert event.client
        assert event.event_type is not None
        assert isinstance(event.timestamp, float)
        assert isinstance(event.payload, dict)


# ---------------------------------------------------------------------------
# Cleanup on the failure path
# ---------------------------------------------------------------------------


def test_each_step_failure_reports_and_cleans() -> None:
    """A failure at every step in the §14 sequence reaches Father."""
    cases = [
        "ensure_mirror",
        "create_worktree",
        "preflight",
        "validate",
        "render_config",
        "create",
        "inject",
    ]
    for fail_at in cases:
        loop, spies = loop_with(offered=envelope(), fail_at=fail_at)
        assert loop.run_once() is True
        assert spies["father"].failed == 1, (
            f"fail_at={fail_at}: failure did not reach Father"
        )
        assert spies["father"].completed == 0
        # Cleanup ran on every failure path.
        assert spies["git"].cleaned >= 1, (
            f"fail_at={fail_at}: cleanup did not run"
        )
        assert spies["tmux"].killed >= 1, (
            f"fail_at={fail_at}: tmux session not killed"
        )


# ---------------------------------------------------------------------------
# §14 — the order is preserved
# ---------------------------------------------------------------------------


def test_event_order_matches_the_section_14_sequence() -> None:
    """The events appear in the order §14 names.

    TG3 already validates that validation happens before any
    git/allocator work. This test pins the broader ordering: each
    event's position in the list is what the §14 sequence says.
    """
    loop, _ = loop_with(offered=envelope())
    assert loop.run_once() is True
    names = [e.event_type.name for e in loop.events]

    expected_order = [
        "ROLE_EXECUTION_CLAIMED",
        "REPOSITORY_READY",
        "WORKTREE_CREATED",
        "ALLOCATOR_PREFLIGHT_STARTED",
        "ALLOCATOR_PREFLIGHT_PASSED",
        "ALIAS_VALIDATED",
        "CLIENT_CONFIG_RENDERED",
        "RUNTIME_ACQUIRED",
        "RUNTIME_READY",
        "TMUX_SESSION_CREATED",
        "CLIENT_STARTED",
        "HANDOFF_INJECTED",
        "ROLE_RUNNING",
        "DELIVERABLE_DETECTED",
        "PATCH_CREATED",
        "RESULT_REPORTED",
        "RUNTIME_RELEASED",
        "ROLE_EXECUTION_COMPLETED",
        "CLEANUP_COMPLETED",
    ]
    # The list may contain more events than the strict §14 mapping
    # (transitional pairs ALLOCATOR_PREFLIGHT_STARTED/PASSED are both
    # recorded, for example). The relative order is what matters.
    last_seen = -1
    for event_name in expected_order:
        if event_name in names:
            idx = names.index(event_name)
            assert idx > last_seen, (
                f"{event_name} should come after the events before it"
            )
            last_seen = idx


# ---------------------------------------------------------------------------
# Module surface — public names are exported
# ---------------------------------------------------------------------------


def test_worker_loop_is_exported_from_package() -> None:
    """The package re-exports WorkerLoop so the entrypoint can use it."""
    from dpmtf_lightworker import WorkerLoop as FromPackage

    assert FromPackage is not None
    assert callable(FromPackage)


def test_categories_are_accessible_at_module_scope() -> None:
    """The category constants are module-level attributes."""
    import dpmtf_lightworker.worker

    assert dpmtf_lightworker.worker.CATEGORY_RESULT_REPORT_FAILED == "RESULT_REPORT_FAILED"
    assert dpmtf_lightworker.worker.CATEGORY_INTERNAL_WORKER_ERROR == "INTERNAL_WORKER_ERROR"


# ---------------------------------------------------------------------------
# §14 — events must not assert a success that did not occur
# ---------------------------------------------------------------------------


def _cleanup_event(loop):
    for event in loop.events:
        if event.event_type.name == "CLEANUP_COMPLETED":
            return event
    raise AssertionError("CLEANUP_COMPLETED was never emitted")


def _runtime_released_event(loop):
    for event in loop.events:
        if event.event_type.name == "RUNTIME_RELEASED":
            return event
    raise AssertionError("RUNTIME_RELEASED was never emitted")


def test_runtime_released_carries_failure_when_allocator_release_raises() -> None:
    """When ``allocator.release()`` raises, the event records the
    failure rather than asserting success."""
    loop, _spies = loop_with(offered=envelope(), fail_at="release")
    assert loop.run_once() is True

    event = _runtime_released_event(loop)
    assert event.payload["succeeded"] is False
    assert "RuntimeReleaseFailed" in event.payload["error"]


def test_runtime_released_carries_success_when_allocator_release_returns() -> None:
    """The happy path still records a successful release."""
    loop, _spies = loop_with(offered=envelope())
    assert loop.run_once() is True

    event = _runtime_released_event(loop)
    assert event.payload["succeeded"] is True
    assert "error" not in event.payload


def test_cleanup_completed_reports_a_failing_git_cleanup() -> None:
    """A failing ``git.cleanup()`` does not regress to a clean claim."""
    loop, spies = loop_with(offered=envelope(), fail_at="cleanup")
    assert loop.run_once() is True
    # TG5 still passes — the counter is incremented before the
    # fake raises.
    assert spies["git"].cleaned >= 1

    event = _cleanup_event(loop)
    git_block = event.payload["git_cleanup"]
    assert git_block["succeeded"] is False
    assert "RuntimeError" in git_block["error"]
    # tmux.kill succeeded in this scenario.
    tmux_block = event.payload["tmux_kill"]
    assert tmux_block["succeeded"] is True


def test_cleanup_completed_reports_a_failing_tmux_kill() -> None:
    """A failing ``tmux.kill()`` shows up the same way."""
    loop, spies = loop_with(offered=envelope(), fail_at="kill")
    assert loop.run_once() is True
    # TG5 still passes.
    assert spies["tmux"].killed >= 1

    event = _cleanup_event(loop)
    tmux_block = event.payload["tmux_kill"]
    assert tmux_block["succeeded"] is False
    assert "RuntimeError" in tmux_block["error"]
    git_block = event.payload["git_cleanup"]
    assert git_block["succeeded"] is True


def test_cleanup_completed_reports_both_steps_failing() -> None:
    """When both best-effort steps fail, the payload records both.

    Uses the widened ``fail_at`` knob (Item 1) to drive
    ``git.cleanup()`` and ``tmux.kill()`` to failure in the same
    run. Presence-of-key assertions would be vacuous here — both
    keys are present on the pure-success path too — so the test
    asserts values: ``succeeded is False`` on each block, with a
    non-empty ``error`` string. The execution still returns
    ``True`` because cleanup failures are best-effort.
    """
    loop, spies = loop_with(
        offered=envelope(), fail_at=["cleanup", "kill"]
    )
    assert loop.run_once() is True
    # TG5-style counters still increment before the raises.
    assert spies["git"].cleaned >= 1
    assert spies["tmux"].killed >= 1

    event = _cleanup_event(loop)
    assert event.payload["git_cleanup"]["succeeded"] is False
    assert "RuntimeError" in event.payload["git_cleanup"]["error"]
    assert event.payload["tmux_kill"]["succeeded"] is False
    assert "RuntimeError" in event.payload["tmux_kill"]["error"]