"""Failure categories for the envelope layer.

The category strings are the contract required by GOAL.md §24; they are
read by structured result reporters and never localised. This handoff
defines only the five categories raised by the envelope layer. The other
twenty-four categories in §24 belong to layers that do not exist yet.
"""

from __future__ import annotations

from typing import Optional


class EnvelopeError(Exception):
    """Base class for all envelope-layer failures.

    The ``category`` attribute is the canonical §24 failure name. Callers
    read it without inspecting the exception hierarchy, so a different
    class with the same category string is interchangeable.
    """

    category: str = "INVALID_EXECUTION_ENVELOPE"

    def __init__(self, message: str, *, field: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        category = cls.__dict__.get("category")
        if not isinstance(category, str) or not category:
            raise TypeError(
                f"{cls.__name__} must define a non-empty 'category' class "
                "attribute carrying the canonical §24 failure name."
            )

    def __str__(self) -> str:
        if self.field is None:
            return self.message
        return f"{self.message} (field={self.field})"


class InvalidExecutionEnvelope(EnvelopeError):
    """General envelope rejection (multi-role, bad path, shell pipeline, ...)."""

    category = "INVALID_EXECUTION_ENVELOPE"


class UnsupportedSchemaVersion(EnvelopeError):
    """The envelope declares a schema_version this worker does not accept."""

    category = "UNSUPPORTED_SCHEMA_VERSION"


class WorkerMismatch(EnvelopeError):
    """The envelope is addressed to a different worker_id."""

    category = "WORKER_MISMATCH"


class UnsupportedModelSource(EnvelopeError):
    """The envelope declares a model_source other than 'model_allocator'."""

    category = "UNSUPPORTED_MODEL_SOURCE"


class UnsupportedClient(EnvelopeError):
    """The envelope declares a client this worker does not accept."""

    category = "UNSUPPORTED_CLIENT"


__all__ = [
    "EnvelopeError",
    "InvalidExecutionEnvelope",
    "UnsupportedClient",
    "UnsupportedModelSource",
    "UnsupportedSchemaVersion",
    "WorkerMismatch",
]
