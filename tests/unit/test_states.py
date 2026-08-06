"""Tests for the worker state model (GOAL.md §21)."""

from __future__ import annotations

import dataclasses

import pytest

from dpmtf_lightworker import WorkerState


def test_state_count_matches_section_21() -> None:
    """§21 lists 21 states; the enum must have exactly that many."""
    members = list(WorkerState)
    assert len(members) == 21, (
        f"expected 21 WorkerState members per §21, got {len(members)}: "
        f"{[m.name for m in members]}"
    )


def test_required_states_present() -> None:
    """Every name listed in §21 is present as a WorkerState member."""
    required = {
        "RECEIVED",
        "VALIDATING_ENVELOPE",
        "CLAIMED",
        "PREPARING_REPOSITORY",
        "PREPARING_WORKTREE",
        "ALLOCATOR_PREFLIGHT",
        "ALLOCATOR_VALIDATING",
        "RENDERING_CLIENT_CONFIG",
        "ACQUIRING_RUNTIME",
        "CREATING_TMUX",
        "STARTING_CLIENT",
        "INJECTING_HANDOFF",
        "RUNNING_ROLE",
        "COLLECTING_RESULT",
        "BUILDING_PATCH",
        "REPORTING_RESULT",
        "RELEASING_RUNTIME",
        "ROLE_EXECUTION_COMPLETED",
        "ROLE_EXECUTION_FAILED",
        "CANCELLED",
        "CLEANING_UP",
    }
    actual = {m.name for m in WorkerState}
    assert required <= actual, (
        f"missing required states: {required - actual}"
    )


def test_forbidden_states_absent() -> None:
    """§21 lists three states the model must NOT include."""
    forbidden = {
        "ADVANCING_CHAIN",
        "STARTING_NEXT_ROLE",
        "COMPLETING_DPMTF_JOB",
    }
    actual = {m.name for m in WorkerState}
    assert not (forbidden & actual), (
        f"forbidden states present: {forbidden & actual}"
    )


def test_state_values_are_canonical_strings() -> None:
    """The string value matches the member name (round-trip safe)."""
    for member in WorkerState:
        assert member.value == member.name


def test_states_are_string_enum() -> None:
    """WorkerState is a str-Enum so it serializes through JSON."""
    state = WorkerState.RUNNING_ROLE
    assert state == "RUNNING_ROLE"
    # String comparison works because WorkerState(str, Enum).
    assert state in {"RECEIVED", "RUNNING_ROLE"}
