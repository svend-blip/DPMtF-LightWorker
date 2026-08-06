"""DPMtF-LightWorker envelope layer.

The envelope layer is the trust boundary between DPMtF Father and a
LightWorker. It validates the payload described in GOAL.md §13 and
returns an immutable dataclass that downstream code can consume without
re-checking any field.
"""

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
    EnvelopeError,
    InvalidExecutionEnvelope,
    UnsupportedClient,
    UnsupportedModelSource,
    UnsupportedSchemaVersion,
    WorkerMismatch,
)
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
    "AllocatorSection",
    "ConfigPath",
    "EnvelopeError",
    "Event",
    "EventType",
    "ExecutionEnvelope",
    "FatherClient",
    "FatherClientError",
    "FatherSection",
    "HandoffPayload",
    "InvalidExecutionEnvelope",
    "NetworkSection",
    "PathsSection",
    "RepositoryRef",
    "ResultContract",
    "ResultMode",
    "RetentionSection",
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
    "load_config",
    "validate_envelope",
]
