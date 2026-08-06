"""Failure categories for the envelope and allocator layers.

The category strings are the contract required by GOAL.md §24; they are
read by structured result reporters and never localised.
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


class AllocatorError(EnvelopeError):
    """Base class for failures returned by the allocator adapter."""

    category = "ALLOCATOR_NOT_AVAILABLE"

    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message, field=field)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class AllocatorNotAvailable(AllocatorError):
    """The allocator command could not be invoked or returned unusable output."""

    category = "ALLOCATOR_NOT_AVAILABLE"


class AllocatorPreflightFailed(AllocatorError):
    """The allocator rejected preflight for a role and client."""

    category = "ALLOCATOR_PREFLIGHT_FAILED"


class AliasValidationFailed(AllocatorError):
    """The allocator rejected an alias and client combination."""

    category = "ALIAS_VALIDATION_FAILED"


class ClientConfigRenderFailed(AllocatorError):
    """The allocator failed to render client configuration."""

    category = "CLIENT_CONFIG_RENDER_FAILED"


class RuntimeReleaseFailed(AllocatorError):
    """The allocator failed to release a runtime."""

    category = "RUNTIME_RELEASE_FAILED"


class GitError(EnvelopeError):
    """Base class for failures returned by the git workspace layer."""

    category = "REPOSITORY_FETCH_FAILED"

    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message, field=field)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RepositoryFetchFailed(GitError):
    """The git fetch or mirror clone could not complete."""

    category = "REPOSITORY_FETCH_FAILED"


class BaseCommitNotFound(GitError):
    """The given base commit SHA does not exist in the mirror."""

    category = "BASE_COMMIT_NOT_FOUND"


class WorktreeCreationFailed(GitError):
    """A git worktree could not be created at the requested base commit."""

    category = "WORKTREE_CREATION_FAILED"


class PatchGenerationFailed(GitError):
    """A binary-safe patch could not be generated from the worktree."""

    category = "PATCH_GENERATION_FAILED"


class TmuxStartFailed(EnvelopeError):
    """A tmux session could not be created for an execution."""

    category = "TMUX_START_FAILED"


class ClientStartFailed(EnvelopeError):
    """The client launch command could not be started in its tmux session."""

    category = "CLIENT_START_FAILED"


class HandoffInjectionFailed(EnvelopeError):
    """The compiled handoff could not be injected into the tmux session."""

    category = "HANDOFF_INJECTION_FAILED"


__all__ = [
    "AliasValidationFailed",
    "AllocatorError",
    "AllocatorNotAvailable",
    "AllocatorPreflightFailed",
    "BaseCommitNotFound",
    "ClientConfigRenderFailed",
    "ClientStartFailed",
    "EnvelopeError",
    "GitError",
    "HandoffInjectionFailed",
    "InvalidExecutionEnvelope",
    "PatchGenerationFailed",
    "RepositoryFetchFailed",
    "RuntimeReleaseFailed",
    "TmuxStartFailed",
    "UnsupportedClient",
    "UnsupportedModelSource",
    "UnsupportedSchemaVersion",
    "WorkerMismatch",
    "WorktreeCreationFailed",
]

