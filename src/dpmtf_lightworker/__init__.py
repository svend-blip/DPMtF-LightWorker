"""DPMtF-LightWorker envelope layer.

The envelope layer is the trust boundary between DPMtF Father and a
LightWorker. It validates the payload described in GOAL.md §13 and
returns an immutable dataclass that downstream code can consume without
re-checking any field.
"""

from dpmtf_lightworker.allocator import AllocatorAdapter
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
    EnvelopeError,
    GitError,
    InvalidExecutionEnvelope,
    PatchGenerationFailed,
    RepositoryFetchFailed,
    RuntimeReleaseFailed,
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
from dpmtf_lightworker.states import WorkerState

__all__ = [
    "AliasValidationFailed",
    "AllocatorAdapter",
    "AllocatorError",
    "AllocatorNotAvailable",
    "AllocatorPreflightFailed",
    "AllocatorSection",
    "BaseCommitNotFound",
    "ClientConfigRenderFailed",
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
    "HandoffPayload",
    "InvalidExecutionEnvelope",
    "NetworkSection",
    "PatchGenerationFailed",
    "PathsSection",
    "RepositoryFetchFailed",
    "RepositoryRef",
    "ResultContract",
    "ResultMode",
    "RetentionSection",
    "RuntimeReleaseFailed",
    "Transport",
    "UnsupportedClient",
    "UnsupportedModelSource",
    "UnsupportedSchemaVersion",
    "ValidatorConfig",
    "WorkerConfig",
    "WorkerConfigError",
    "WorkerMismatch",
    "WorkerSection",
    "WorkerState",
    "WorktreeCreationFailed",
    "load_config",
    "validate_envelope",
]
