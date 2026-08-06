"""Tests for structured events (GOAL.md §22)."""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping

import pytest

from dpmtf_lightworker import Event, EventType


def _event_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "worker_id": "svend3060",
        "execution_id": "EXEC-123-IMPLE01",
        "attempt_id": "ATTEMPT-1",
        "target_role": "imple01",
        "model_alias": "imple01-3060",
        "client": "opencode",
        "event_type": EventType.ROLE_RUNNING,
        "timestamp": 1700000000.0,
        "payload": {"step": "running", "progress": 0.5},
    }
    kwargs.update(overrides)
    return kwargs


def test_event_is_dataclass_with_nine_fields() -> None:
    """§22 names nine mandatory fields; ``Event`` must be a dataclass."""
    assert dataclasses.is_dataclass(Event)
    field_names = {f.name for f in dataclasses.fields(Event)}
    expected = {
        "worker_id",
        "execution_id",
        "attempt_id",
        "target_role",
        "model_alias",
        "client",
        "event_type",
        "timestamp",
        "payload",
    }
    assert field_names == expected, (
        f"expected {expected}, got {field_names}"
    )


def test_event_carries_required_fields() -> None:
    """All nine fields are populated by the constructor."""
    event = Event(**_event_kwargs())
    assert event.worker_id == "svend3060"
    assert event.execution_id == "EXEC-123-IMPLE01"
    assert event.attempt_id == "ATTEMPT-1"
    assert event.target_role == "imple01"
    assert event.model_alias == "imple01-3060"
    assert event.client == "opencode"
    assert event.event_type == EventType.ROLE_RUNNING
    assert event.timestamp == 1700000000.0
    assert event.payload == {"step": "running", "progress": 0.5}


def test_event_is_immutable() -> None:
    """Frozen dataclass: field assignment raises."""
    event = Event(**_event_kwargs())
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        event.worker_id = "other"  # type: ignore[misc]


def test_event_type_count_matches_section_22() -> None:
    """§22 lists 24 minimum event types."""
    members = list(EventType)
    assert len(members) == 24, (
        f"expected 24 EventType members per §22, got {len(members)}: "
        f"{[m.name for m in members]}"
    )


def test_required_event_types_present() -> None:
    """Every name listed in §22 is present as an EventType member."""
    required = {
        "WORKER_REGISTERED",
        "ROLE_EXECUTION_RECEIVED",
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
        "TESTS_COMPLETED",
        "LOCAL_RESULT_COMMITTED",
        "PATCH_CREATED",
        "RESULT_REPORTED",
        "RUNTIME_RELEASED",
        "ROLE_EXECUTION_COMPLETED",
        "ROLE_EXECUTION_FAILED",
        "CLEANUP_COMPLETED",
    }
    actual = {m.name for m in EventType}
    assert required <= actual, (
        f"missing required event types: {required - actual}"
    )


def test_event_type_values_are_canonical_strings() -> None:
    """Round-trip safety for serialization."""
    for member in EventType:
        assert member.value == member.name


def test_payload_accepts_arbitrary_mapping() -> None:
    """The payload is a structured mapping — §22 forbids raw strings."""
    event = Event(**_event_kwargs(payload={"key": "value", "nested": [1, 2, 3]}))
    assert event.payload["key"] == "value"
    assert event.payload["nested"] == [1, 2, 3]


def test_timestamp_is_a_plain_float() -> None:
    """Constructible in a test without freezing the clock."""
    event = Event(**_event_kwargs(timestamp=123.456))
    assert event.timestamp == 123.456
    assert isinstance(event.timestamp, float)


def test_event_type_accepts_string_coercion() -> None:
    """EventType is a str-Enum, so the constructor accepts the canonical name."""
    event = Event(**_event_kwargs(event_type=EventType.WORKER_REGISTERED))
    assert event.event_type == "WORKER_REGISTERED"
