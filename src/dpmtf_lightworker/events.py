"""Structured event types and records (GOAL.md §22).

Every state transition a LightWorker makes is reported as an
:class:`Event` with a fixed set of identification fields plus a
``payload`` mapping. §22 closes with the rule that raw pane output
must not be the only source of execution state; ``payload`` is
therefore a structured mapping, not a rendered string.

The timestamp representation is a float of POSIX seconds since the
epoch (``time.time()``). It is constructible in a test by passing a
plain number, no clock required.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class EventType(str, Enum):
    """The 24 minimum event types listed in GOAL.md §22."""

    WORKER_REGISTERED = "WORKER_REGISTERED"
    ROLE_EXECUTION_RECEIVED = "ROLE_EXECUTION_RECEIVED"
    ROLE_EXECUTION_CLAIMED = "ROLE_EXECUTION_CLAIMED"
    REPOSITORY_READY = "REPOSITORY_READY"
    WORKTREE_CREATED = "WORKTREE_CREATED"
    ALLOCATOR_PREFLIGHT_STARTED = "ALLOCATOR_PREFLIGHT_STARTED"
    ALLOCATOR_PREFLIGHT_PASSED = "ALLOCATOR_PREFLIGHT_PASSED"
    ALIAS_VALIDATED = "ALIAS_VALIDATED"
    CLIENT_CONFIG_RENDERED = "CLIENT_CONFIG_RENDERED"
    RUNTIME_ACQUIRED = "RUNTIME_ACQUIRED"
    RUNTIME_READY = "RUNTIME_READY"
    TMUX_SESSION_CREATED = "TMUX_SESSION_CREATED"
    CLIENT_STARTED = "CLIENT_STARTED"
    HANDOFF_INJECTED = "HANDOFF_INJECTED"
    ROLE_RUNNING = "ROLE_RUNNING"
    DELIVERABLE_DETECTED = "DELIVERABLE_DETECTED"
    TESTS_COMPLETED = "TESTS_COMPLETED"
    LOCAL_RESULT_COMMITTED = "LOCAL_RESULT_COMMITTED"
    PATCH_CREATED = "PATCH_CREATED"
    RESULT_REPORTED = "RESULT_REPORTED"
    RUNTIME_RELEASED = "RUNTIME_RELEASED"
    ROLE_EXECUTION_COMPLETED = "ROLE_EXECUTION_COMPLETED"
    ROLE_EXECUTION_FAILED = "ROLE_EXECUTION_FAILED"
    CLEANUP_COMPLETED = "CLEANUP_COMPLETED"


@dataclass(frozen=True, slots=True)
class Event:
    """A structured event emitted by a LightWorker.

    The nine fields are the §22 contract; ``payload`` is a structured
    mapping because the closing line of §22 forbids relying on raw
    pane output as the only signal of execution state.
    """

    worker_id: str
    execution_id: str
    attempt_id: str
    target_role: str
    model_alias: str
    client: str
    event_type: EventType
    timestamp: float
    payload: Mapping[str, Any]


__all__ = ["Event", "EventType"]
