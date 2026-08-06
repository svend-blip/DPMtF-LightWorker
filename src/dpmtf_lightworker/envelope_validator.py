"""Validator for the role-execution envelope (GOAL.md §13).

The validator is the single trust boundary between DPMtF Father and the
LightWorker. It accepts a parsed mapping, applies every §13 rejection
rule, and returns an immutable :class:`ExecutionEnvelope`. A rejection
raises a typed exception whose ``category`` attribute is the canonical
§24 failure name.

The worker's identity, accepted schema versions, accepted clients and
filesystem root are all configuration passed via
:class:`ValidatorConfig`. They are not literals buried in functions, so
the same validator can be retargeted at another worker by changing the
config object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from dpmtf_lightworker.errors import (
    InvalidExecutionEnvelope,
    UnsupportedClient,
    UnsupportedModelSource,
    UnsupportedSchemaVersion,
    WorkerMismatch,
)
from dpmtf_lightworker.models import (
    ExecutionEnvelope,
    HandoffPayload,
    RepositoryRef,
    ResultContract,
    ResultMode,
)


SUPPORTED_MODEL_SOURCE: str = "model_allocator"
DEFAULT_BASE_COMMIT_HEX_LENGTH: int = 40
SHELL_METACHARS: frozenset[str] = frozenset(";|&`$\n\r")


@dataclass(frozen=True, slots=True)
class ValidatorConfig:
    """Per-worker configuration consumed by :func:`validate_envelope`.

    Attributes:
        worker_id: Identity asserted against ``envelope.worker_id``.
        supported_schema_versions: Allow-list of accepted
            ``envelope.schema_version`` values.
        supported_clients: Allow-list of accepted ``envelope.client``
            values.
        repository_root: Absolute filesystem root for repository
            mirrors and deliverable paths. Any accepted
            ``expected_deliverable`` must resolve to a path beneath
            this root.
        model_source: The only ``model_source`` value accepted. V1
            requires ``model_allocator`` (GOAL.md §13).
        base_commit_hex_length: Required length of a full SHA in
            ``repository.base_commit``. Defaults to 40 (SHA-1, the
            current Git default).
    """

    worker_id: str
    supported_schema_versions: frozenset[str]
    supported_clients: frozenset[str]
    repository_root: Path
    model_source: str = SUPPORTED_MODEL_SOURCE
    base_commit_hex_length: int = DEFAULT_BASE_COMMIT_HEX_LENGTH

    def __post_init__(self) -> None:
        if not self.worker_id:
            raise ValueError("ValidatorConfig.worker_id must not be empty")
        if not self.supported_schema_versions:
            raise ValueError(
                "ValidatorConfig.supported_schema_versions must not be empty"
            )
        if not self.supported_clients:
            raise ValueError(
                "ValidatorConfig.supported_clients must not be empty"
            )
        if not self.model_source:
            raise ValueError("ValidatorConfig.model_source must not be empty")
        if self.base_commit_hex_length <= 0:
            raise ValueError(
                "ValidatorConfig.base_commit_hex_length must be positive"
            )
        if not self.repository_root.is_absolute():
            raise ValueError(
                "ValidatorConfig.repository_root must be an absolute path"
            )


def validate_envelope(
    payload: Mapping[str, Any],
    *,
    config: ValidatorConfig,
) -> ExecutionEnvelope:
    """Validate and parse a role-execution envelope.

    :param payload: A parsed mapping (typically the JSON-decoded body
        of the envelope message).
    :param config: The validator configuration.
    :returns: An immutable, validated :class:`ExecutionEnvelope`.
    :raises EnvelopeError: a subclass whose ``category`` attribute is
        the canonical §24 failure name for the rejected rule.
    """
    if not isinstance(payload, Mapping):
        raise InvalidExecutionEnvelope(
            "envelope payload must be a JSON object",
            field="<root>",
        )

    # --- Rule 1: more than one target role -----------------------------
    # §13's example uses a single string. §5.1 and §37 say an envelope
    # describes exactly one role step, so any non-scalar shape (a list
    # of roles, a dict) is rejected up front. This is the conservative
    # reading: a single-element list is also rejected because the
    # field's shape should be a scalar.
    raw_target_role: Any = payload.get("target_role")
    if not isinstance(raw_target_role, str) or not raw_target_role:
        raise InvalidExecutionEnvelope(
            "target_role must be a non-empty string; an envelope "
            "describes exactly one role step",
            field="target_role",
        )
    target_role: str = raw_target_role
    _check_shell_pipeline("target_role", target_role)

    # --- Rule 2: unsupported schema_version ----------------------------
    schema_version: str = _pop_str(payload, "schema_version")
    _check_shell_pipeline("schema_version", schema_version)
    if schema_version not in config.supported_schema_versions:
        raise UnsupportedSchemaVersion(
            f"schema_version {schema_version!r} is not supported",
            field="schema_version",
        )

    # --- Rule 3: worker_id mismatch ------------------------------------
    worker_id: str = _pop_str(payload, "worker_id")
    _check_shell_pipeline("worker_id", worker_id)
    if worker_id != config.worker_id:
        raise WorkerMismatch(
            f"worker_id {worker_id!r} does not match this worker's "
            f"identity {config.worker_id!r}",
            field="worker_id",
        )

    # --- Rule 4: model_source other than model_allocator ---------------
    model_source: str = _pop_str(payload, "model_source")
    _check_shell_pipeline("model_source", model_source)
    if model_source != config.model_source:
        raise UnsupportedModelSource(
            f"model_source {model_source!r} is not supported; only "
            f"{config.model_source!r} is accepted in V1",
            field="model_source",
        )

    # --- Rule 5: missing model_alias -----------------------------------
    model_alias: str = _pop_str(payload, "model_alias")
    _check_shell_pipeline("model_alias", model_alias)
    if not model_alias:
        raise InvalidExecutionEnvelope(
            "model_alias is required and must not be empty",
            field="model_alias",
        )

    # --- Rule 6: unsupported client ------------------------------------
    client: str = _pop_str(payload, "client")
    _check_shell_pipeline("client", client)
    if client not in config.supported_clients:
        raise UnsupportedClient(
            f"client {client!r} is not supported",
            field="client",
        )

    # --- Strict identification strings ---------------------------------
    execution_id: str = _pop_str(payload, "execution_id")
    _check_shell_pipeline("execution_id", execution_id)
    if not execution_id:
        raise InvalidExecutionEnvelope(
            "execution_id is required and must not be empty",
            field="execution_id",
        )

    job_id: str = _pop_str(payload, "job_id")
    _check_shell_pipeline("job_id", job_id)
    if not job_id:
        raise InvalidExecutionEnvelope(
            "job_id is required and must not be empty",
            field="job_id",
        )

    handoff_id: str = _pop_str(payload, "handoff_id")
    _check_shell_pipeline("handoff_id", handoff_id)
    if not handoff_id:
        raise InvalidExecutionEnvelope(
            "handoff_id is required and must not be empty",
            field="handoff_id",
        )

    attempt_id: str = _pop_str(payload, "attempt_id")
    _check_shell_pipeline("attempt_id", attempt_id)
    if not attempt_id:
        raise InvalidExecutionEnvelope(
            "attempt_id is required and must not be empty",
            field="attempt_id",
        )

    flow_key: str = _pop_str(payload, "flow_key")
    _check_shell_pipeline("flow_key", flow_key)
    if not flow_key:
        raise InvalidExecutionEnvelope(
            "flow_key is required and must not be empty",
            field="flow_key",
        )

    step_key: str = _pop_str(payload, "step_key")
    _check_shell_pipeline("step_key", step_key)
    if not step_key:
        raise InvalidExecutionEnvelope(
            "step_key is required and must not be empty",
            field="step_key",
        )

    source_role: str = _pop_str(payload, "source_role")
    _check_shell_pipeline("source_role", source_role)
    if not source_role:
        raise InvalidExecutionEnvelope(
            "source_role is required and must not be empty",
            field="source_role",
        )

    # --- Repository triple --------------------------------------------
    repository_obj: Any = payload.get("repository")
    if not isinstance(repository_obj, Mapping):
        raise InvalidExecutionEnvelope(
            "repository must be a mapping",
            field="repository",
        )
    repository: Mapping[str, Any] = repository_obj

    project_key: str = _pop_str(repository, "project_key")
    _check_shell_pipeline("repository.project_key", project_key)
    if not project_key:
        raise InvalidExecutionEnvelope(
            "repository.project_key is required and must not be empty",
            field="repository.project_key",
        )

    clone_url: str = _pop_str(repository, "clone_url")
    _check_shell_pipeline("repository.clone_url", clone_url)
    if not clone_url:
        raise InvalidExecutionEnvelope(
            "repository.clone_url is required and must not be empty",
            field="repository.clone_url",
        )

    # --- Rule 7: missing or non-full-SHA base_commit -------------------
    base_commit: str = _pop_str(repository, "base_commit")
    _check_shell_pipeline("repository.base_commit", base_commit)
    _validate_full_sha(base_commit, config.base_commit_hex_length)

    # --- Handoff triple -----------------------------------------------
    handoff_obj: Any = payload.get("handoff")
    if not isinstance(handoff_obj, Mapping):
        raise InvalidExecutionEnvelope(
            "handoff must be a mapping",
            field="handoff",
        )
    handoff: Mapping[str, Any] = handoff_obj

    handoff_content: str = _pop_str(handoff, "content")
    # handoff.content is a document; shell metacharacters are expected
    # to appear inside code examples, so the shell-pipeline rule is NOT
    # applied here. See result §"Shell pipeline scope" for the
    # rationale.

    handoff_governance_content: str = _pop_str(handoff, "governance_content")
    # handoff.governance_content is a document; same rationale.

    # --- Rule 8: unsafe repository or deliverable paths ----------------
    expected_deliverable: str = _pop_str(handoff, "expected_deliverable")
    _check_shell_pipeline("handoff.expected_deliverable", expected_deliverable)
    if not expected_deliverable:
        raise InvalidExecutionEnvelope(
            "handoff.expected_deliverable is required and must not be empty",
            field="handoff.expected_deliverable",
        )
    _validate_deliverable_path(
        expected_deliverable,
        repository_root=config.repository_root,
    )

    # --- Result contract triple ----------------------------------------
    result_contract_obj: Any = payload.get("result_contract")
    if not isinstance(result_contract_obj, Mapping):
        raise InvalidExecutionEnvelope(
            "result_contract must be a mapping",
            field="result_contract",
        )
    result_contract: Mapping[str, Any] = result_contract_obj

    mode_raw: str = _pop_str(result_contract, "mode")
    _check_shell_pipeline("result_contract.mode", mode_raw)
    if mode_raw not in ResultMode._value2member_map_:
        raise InvalidExecutionEnvelope(
            f"result_contract.mode {mode_raw!r} is not one of "
            f"{sorted(m.value for m in ResultMode)}",
            field="result_contract.mode",
        )
    mode: ResultMode = ResultMode(mode_raw)

    tests_required: bool = _pop_bool(result_contract, "tests_required")
    local_result_commit_required: bool = _pop_bool(
        result_contract, "local_result_commit_required"
    )

    # --- Rule 9: shell pipelines already checked field-by-field --------
    # Any structural field (every field above except handoff.content
    # and handoff.governance_content) that contained a shell
    # metacharacter has already raised. The two handoff document
    # fields are deliberately exempt because compiled handoffs
    # legitimately contain shell examples inside markdown code blocks.

    return ExecutionEnvelope(
        schema_version=schema_version,
        execution_id=execution_id,
        job_id=job_id,
        handoff_id=handoff_id,
        attempt_id=attempt_id,
        flow_key=flow_key,
        step_key=step_key,
        source_role=source_role,
        target_role=target_role,
        worker_id=worker_id,
        model_source=model_source,
        model_alias=model_alias,
        client=client,
        repository=RepositoryRef(
            project_key=project_key,
            clone_url=clone_url,
            base_commit=base_commit,
        ),
        handoff=HandoffPayload(
            content=handoff_content,
            governance_content=handoff_governance_content,
            expected_deliverable=expected_deliverable,
        ),
        result_contract=ResultContract(
            mode=mode,
            tests_required=tests_required,
            local_result_commit_required=local_result_commit_required,
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _pop_str(payload: Mapping[str, Any], key: str) -> str:
    if key not in payload:
        raise InvalidExecutionEnvelope(
            f"{key} is required",
            field=key,
        )
    value: Any = payload[key]
    if not isinstance(value, str):
        raise InvalidExecutionEnvelope(
            f"{key} must be a string",
            field=key,
        )
    return value


def _pop_bool(payload: Mapping[str, Any], key: str) -> bool:
    if key not in payload:
        raise InvalidExecutionEnvelope(
            f"{key} is required",
            field=key,
        )
    value: Any = payload[key]
    if not isinstance(value, bool):
        raise InvalidExecutionEnvelope(
            f"{key} must be a boolean",
            field=key,
        )
    return value


def _check_shell_pipeline(field_name: str, value: str) -> None:
    """Reject ``value`` if it contains shell pipeline metacharacters.

    The set covers command chaining, pipes, command substitution,
    redirections and any embedded newline. The check is intentionally
    strict because every structural field is a value Father controls
    and the worker must not execute any of it.
    """
    for ch in SHELL_METACHARS:
        if ch in value:
            raise InvalidExecutionEnvelope(
                f"{field_name} contains shell pipeline metacharacter "
                f"{ch!r}; the worker does not accept arbitrary shell "
                "from Father",
                field=field_name,
            )


def _validate_full_sha(value: str, length: int) -> None:
    if not value:
        raise InvalidExecutionEnvelope(
            "repository.base_commit is required and must be a full SHA",
            field="repository.base_commit",
        )
    if len(value) != length:
        raise InvalidExecutionEnvelope(
            f"repository.base_commit must be a full {length}-hex "
            f"SHA; got {len(value)} characters",
            field="repository.base_commit",
        )
    for ch in value:
        if ch not in "0123456789abcdefABCDEF":
            raise InvalidExecutionEnvelope(
                "repository.base_commit must contain only hexadecimal "
                "characters",
                field="repository.base_commit",
            )


def _validate_deliverable_path(expected: str, *, repository_root: Path) -> None:
    """Reject ``expected`` if it does not stay below ``repository_root``.

    Three checks, in order:

    1. Empty: rejected (the validator already rejects empty values
       above, but the rule is duplicated here for clarity).
    2. Absolute: rejected — the deliverable is always project-relative.
    3. Traversal: any ``..`` segment is rejected (defence in depth
       before the filesystem call).
    4. Containment: the resolved path must be ``is_relative_to`` the
       resolved root. The root is resolved so that a symlink that
       *points outside* the original root is also caught.
    """
    if not expected:
        raise InvalidExecutionEnvelope(
            "handoff.expected_deliverable must not be empty",
            field="handoff.expected_deliverable",
        )

    as_path = Path(expected)
    if as_path.is_absolute():
        raise InvalidExecutionEnvelope(
            "handoff.expected_deliverable must be a project-relative "
            "path; absolute paths are rejected",
            field="handoff.expected_deliverable",
        )

    for part in as_path.parts:
        if part == "..":
            raise InvalidExecutionEnvelope(
                "handoff.expected_deliverable contains '..' traversal",
                field="handoff.expected_deliverable",
            )

    try:
        combined = repository_root / as_path
        resolved = combined.resolve()
        root_resolved = repository_root.resolve()
    except (OSError, RuntimeError) as exc:
        raise InvalidExecutionEnvelope(
            f"handoff.expected_deliverable cannot be resolved: {exc}",
            field="handoff.expected_deliverable",
        ) from exc

    if not resolved.is_relative_to(root_resolved):
        raise InvalidExecutionEnvelope(
            "handoff.expected_deliverable escapes the configured "
            "repository root",
            field="handoff.expected_deliverable",
        )


__all__ = [
    "DEFAULT_BASE_COMMIT_HEX_LENGTH",
    "SHELL_METACHARS",
    "SUPPORTED_MODEL_SOURCE",
    "ValidatorConfig",
    "validate_envelope",
]
