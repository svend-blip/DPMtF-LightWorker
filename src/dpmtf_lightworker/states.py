"""Worker state model (GOAL.md §21).

The worker moves through a fixed set of states while it executes a single
role step. The set is named verbatim in §21; three members explicitly
excluded by §21 are absent because a LightWorker never advances the
flow — §30's boundary tests live or die on that absence.

Transitions are deliberately not modelled here. §21 names the states; the
state machine that moves between them belongs to the worker loop in a
later run.
"""

from __future__ import annotations

from enum import Enum


class WorkerState(str, Enum):
    """The 21 states listed in GOAL.md §21.

    The string value is the canonical name, identical to the enum member
    name, so a serialized event or log line is round-trip-safe.
    """

    RECEIVED = "RECEIVED"
    VALIDATING_ENVELOPE = "VALIDATING_ENVELOPE"
    CLAIMED = "CLAIMED"
    PREPARING_REPOSITORY = "PREPARING_REPOSITORY"
    PREPARING_WORKTREE = "PREPARING_WORKTREE"
    ALLOCATOR_PREFLIGHT = "ALLOCATOR_PREFLIGHT"
    ALLOCATOR_VALIDATING = "ALLOCATOR_VALIDATING"
    RENDERING_CLIENT_CONFIG = "RENDERING_CLIENT_CONFIG"
    ACQUIRING_RUNTIME = "ACQUIRING_RUNTIME"
    CREATING_TMUX = "CREATING_TMUX"
    STARTING_CLIENT = "STARTING_CLIENT"
    INJECTING_HANDOFF = "INJECTING_HANDOFF"
    RUNNING_ROLE = "RUNNING_ROLE"
    COLLECTING_RESULT = "COLLECTING_RESULT"
    BUILDING_PATCH = "BUILDING_PATCH"
    REPORTING_RESULT = "REPORTING_RESULT"
    RELEASING_RUNTIME = "RELEASING_RUNTIME"
    ROLE_EXECUTION_COMPLETED = "ROLE_EXECUTION_COMPLETED"
    ROLE_EXECUTION_FAILED = "ROLE_EXECUTION_FAILED"
    CANCELLED = "CANCELLED"
    CLEANING_UP = "CLEANING_UP"


__all__ = ["WorkerState"]
