"""DPMtF-LightWorker envelope layer.

The envelope layer is the trust boundary between DPMtF Father and a
LightWorker. It validates the payload described in GOAL.md §13 and
returns an immutable dataclass that downstream code can consume without
re-checking any field.
"""

from dpmtf_lightworker.allocator import AllocatorAdapter
from dpmtf_lightworker.client_config import (
    render_execution_config,
    validate_rendered_config,
)
from dpmtf_lightworker.config import (
    AllocatorSection,
    ConfigPath,
    FatherSection,
    NetworkSection,
    PathsSection,
    RetentionSection,
    WorkerConfig,
    WorkerConfigError,
    WorkerSection,
    load_config,
)
from dpmtf_lightworker.errors import (
    AliasValidationFailed,
    AllocatorError,
    AllocatorNotAvailable,
    AllocatorPreflightFailed,
    BaseCommitNotFound,
    ClientConfigRenderFailed,
    ClientStartFailed,
    EnvelopeError,
    GitError,
    HandoffInjectionFailed,
    InvalidExecutionEnvelope,
    PatchGenerationFailed,
    RepositoryFetchFailed,
    RuntimeReleaseFailed,
    TmuxStartFailed,
    UnsupportedClient,
    UnsupportedModelSource,
    UnsupportedSchemaVersion,
    WorkerMismatch,
    WorktreeCreationFailed,
)
from dpmtf_lightworker.git_workspace import GitWorkspace
from dpmtf_lightworker.envelope_validator import (
    ValidatorConfig,
    validate_envelope,
)
from dpmtf_lightworker.events import Event, EventType
from dpmtf_lightworker.father_client import (
    FatherClient,
    FatherClientError,
    Transport,
)
from dpmtf_lightworker.models import (
    ExecutionEnvelope,
    HandoffPayload,
    RepositoryRef,
    ResultContract,
    ResultMode,
)
from dpmtf_lightworker.safe_paths import redact, resolve_within
from dpmtf_lightworker.states import WorkerState
from dpmtf_lightworker.tmux_session import TmuxSession

__all__ = [
    "AliasValidationFailed",
    "AllocatorAdapter",
    "AllocatorError",
    "AllocatorNotAvailable",
    "AllocatorPreflightFailed",
    "AllocatorSection",
    "BaseCommitNotFound",
    "ClientConfigRenderFailed",
    "ClientStartFailed",
    "ConfigPath",
    "EnvelopeError",
    "Event",
    "EventType",
    "ExecutionEnvelope",
    "FatherClient",
    "FatherClientError",
    "FatherSection",
    "GitError",
    "GitWorkspace",
    "HandoffInjectionFailed",
    "HandoffPayload",
    "InvalidExecutionEnvelope",
    "NetworkSection",
    "PatchGenerationFailed",
    "PathsSection",
    "RepositoryFetchFailed",
    "RepositoryRef",
    "redact",
    "render_execution_config",
    "resolve_within",
    "ResultContract",
    "ResultMode",
    "RetentionSection",
    "RuntimeReleaseFailed",
    "TmuxSession",
    "TmuxStartFailed",
    "Transport",
    "UnsupportedClient",
    "UnsupportedModelSource",
    "UnsupportedSchemaVersion",
    "validate_envelope",
    "validate_rendered_config",
    "ValidatorConfig",
    "WorkerConfig",
    "WorkerConfigError",
    "WorkerMismatch",
    "WorkerSection",
    "WorkerState",
    "WorktreeCreationFailed",
    "load_config",
]
