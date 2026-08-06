"""Immutable models for the role-execution envelope (GOAL.md §13).

All dataclasses are ``frozen=True`` and ``slots=True`` so that an
envelope, once validated, cannot be mutated by downstream code. The
nested blocks — ``RepositoryRef``, ``HandoffPayload`` and
``ResultContract`` — are themselves dataclasses, not free-form
dictionaries, so consumers read typed attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ResultMode(str, Enum):
    """The three result modes listed in GOAL.md §17."""

    PATCH = "patch"
    DELIVERABLE_ONLY = "deliverable_only"
    PATCH_AND_DELIVERABLE = "patch_and_deliverable"


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    """The repository triple described in GOAL.md §13."""

    project_key: str
    clone_url: str
    base_commit: str


@dataclass(frozen=True, slots=True)
class HandoffPayload:
    """The handoff triple described in GOAL.md §13."""

    content: str
    governance_content: str
    expected_deliverable: str


@dataclass(frozen=True, slots=True)
class ResultContract:
    """The result contract described in GOAL.md §13."""

    mode: ResultMode
    tests_required: bool
    local_result_commit_required: bool


@dataclass(frozen=True, slots=True)
class ExecutionEnvelope:
    """A validated role-execution envelope.

    The validator in ``envelope_validator`` is the only constructor
    callers should use. Direct construction is possible but skips every
    rejection rule.
    """

    schema_version: str
    execution_id: str
    job_id: str
    handoff_id: str
    attempt_id: str
    flow_key: str
    step_key: str
    source_role: str
    target_role: str
    worker_id: str
    model_source: str
    model_alias: str
    client: str
    repository: RepositoryRef
    handoff: HandoffPayload
    result_contract: ResultContract


__all__ = [
    "ExecutionEnvelope",
    "HandoffPayload",
    "RepositoryRef",
    "ResultContract",
    "ResultMode",
]
