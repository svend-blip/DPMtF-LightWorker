"""DPMtF-LightWorker envelope layer.

The envelope layer is the trust boundary between DPMtF Father and a
LightWorker. It validates the payload described in GOAL.md §13 and
returns an immutable dataclass that downstream code can consume without
re-checking any field.
"""

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
from dpmtf_lightworker.models import (
    ExecutionEnvelope,
    HandoffPayload,
    RepositoryRef,
    ResultContract,
    ResultMode,
)

__all__ = [
    "EnvelopeError",
    "ExecutionEnvelope",
    "HandoffPayload",
    "InvalidExecutionEnvelope",
    "RepositoryRef",
    "ResultContract",
    "ResultMode",
    "UnsupportedClient",
    "UnsupportedModelSource",
    "UnsupportedSchemaVersion",
    "ValidatorConfig",
    "WorkerMismatch",
    "validate_envelope",
]
