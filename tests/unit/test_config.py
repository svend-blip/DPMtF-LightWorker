"""Tests for the worker configuration loader (GOAL.md §12)."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from dpmtf_lightworker import (
    AllocatorSection,
    FatherSection,
    NetworkSection,
    PathsSection,
    RetentionSection,
    WorkerConfig,
    WorkerConfigError,
    WorkerSection,
    load_config,
)


_EXAMPLE_YAML = textwrap.dedent(
    """\
    worker:
      id: svend3060
      display_name: RTX 3060 LightWorker
      max_parallel_executions: 1
      poll_interval_seconds: 10
      heartbeat_interval_seconds: 15

    father:
      base_url: http://main5090:9130
      request_timeout_seconds: 30

    network:
      type: tailscale
      expected_father_host: main5090

    allocator:
      command: model-allocator
      client: opencode
      config_root: ${MODEL_ALLOCATOR_CONFIG_ROOT}

    paths:
      repository_root: ${LIGHTWORKER_REPOSITORY_ROOT}
      worktree_root: ${LIGHTWORKER_WORKTREE_ROOT}
      artifact_root: ${LIGHTWORKER_ARTIFACT_ROOT}
      log_root: ${LIGHTWORKER_LOG_ROOT}
      opencode_config_root: ${LIGHTWORKER_OPENCODE_CONFIG_ROOT}

    retention:
      successful_worktree_hours: 1
      failed_worktree_days: 7
      artifact_days: 14
      log_days: 30
    """
)


@pytest.fixture
def example_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_ALLOCATOR_CONFIG_ROOT", "/etc/lightworker/allocator")
    monkeypatch.setenv("LIGHTWORKER_REPOSITORY_ROOT", "/srv/lightworker/repos")
    monkeypatch.setenv("LIGHTWORKER_WORKTREE_ROOT", "/srv/lightworker/worktrees")
    monkeypatch.setenv("LIGHTWORKER_ARTIFACT_ROOT", "/srv/lightworker/artifacts")
    monkeypatch.setenv("LIGHTWORKER_LOG_ROOT", "/var/log/lightworker")
    monkeypatch.setenv("LIGHTWORKER_OPENCODE_CONFIG_ROOT", "/etc/lightworker/opencode")


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "worker.yaml"
    config_path.write_text(body, encoding="utf-8")
    return config_path


def test_load_config_returns_typed_dataclass_tree(
    tmp_path: Path, example_env: None
) -> None:
    """``load_config`` returns a :class:`WorkerConfig` dataclass tree."""
    config_path = _write_config(tmp_path, _EXAMPLE_YAML)
    config = load_config(config_path)
    assert isinstance(config, WorkerConfig)
    assert isinstance(config.worker, WorkerSection)
    assert isinstance(config.father, FatherSection)
    assert isinstance(config.network, NetworkSection)
    assert isinstance(config.allocator, AllocatorSection)
    assert isinstance(config.paths, PathsSection)
    assert isinstance(config.retention, RetentionSection)


def test_load_config_expands_env_vars(
    tmp_path: Path, example_env: None
) -> None:
    """Every ``${VAR}`` reference is expanded from the process environment."""
    config_path = _write_config(tmp_path, _EXAMPLE_YAML)
    config = load_config(config_path)
    assert config.allocator.config_root == "/etc/lightworker/allocator"
    assert config.paths.repository_root == "/srv/lightworker/repos"
    assert config.paths.worktree_root == "/srv/lightworker/worktrees"
    assert config.paths.artifact_root == "/srv/lightworker/artifacts"
    assert config.paths.log_root == "/var/log/lightworker"
    assert config.paths.opencode_config_root == "/etc/lightworker/opencode"


def test_load_config_accepts_string_path(
    tmp_path: Path, example_env: None
) -> None:
    """The contract accepts ``str`` as well as ``os.PathLike``."""
    config_path = _write_config(tmp_path, _EXAMPLE_YAML)
    config = load_config(str(config_path))
    assert config.worker.id == "svend3060"


def test_load_config_fails_on_unset_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unset variable is a load failure whose message names it."""
    # Ensure the variables from the example are NOT set.
    for name in [
        "MODEL_ALLOCATOR_CONFIG_ROOT",
        "LIGHTWORKER_REPOSITORY_ROOT",
        "LIGHTWORKER_WORKTREE_ROOT",
        "LIGHTWORKER_ARTIFACT_ROOT",
        "LIGHTWORKER_LOG_ROOT",
        "LIGHTWORKER_OPENCODE_CONFIG_ROOT",
    ]:
        monkeypatch.delenv(name, raising=False)

    config_path = _write_config(tmp_path, _EXAMPLE_YAML)
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(config_path)
    assert "MODEL_ALLOCATOR_CONFIG_ROOT" in str(excinfo.value)


def test_load_config_fails_on_unset_variable_in_any_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The variable name appears regardless of which section references it."""
    for name in [
        "MODEL_ALLOCATOR_CONFIG_ROOT",
        "LIGHTWORKER_REPOSITORY_ROOT",
        "LIGHTWORKER_WORKTREE_ROOT",
        "LIGHTWORKER_ARTIFACT_ROOT",
        "LIGHTWORKER_LOG_ROOT",
    ]:
        monkeypatch.setenv(name, f"/tmp/{name}")
    monkeypatch.delenv("LIGHTWORKER_OPENCODE_CONFIG_ROOT", raising=False)

    config_path = _write_config(tmp_path, _EXAMPLE_YAML)
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(config_path)
    assert "LIGHTWORKER_OPENCODE_CONFIG_ROOT" in str(excinfo.value)


def test_load_config_handles_bare_dollar_var_form(
    tmp_path: Path, example_env: None
) -> None:
    """``$VAR`` (without braces) is also expanded."""
    body = textwrap.dedent(
        """\
        worker:
          id: svend3060
          display_name: RTX 3060 LightWorker
          max_parallel_executions: 1
          poll_interval_seconds: 10
          heartbeat_interval_seconds: 15

        father:
          base_url: http://main5090:9130
          request_timeout_seconds: 30

        network:
          type: tailscale
          expected_father_host: main5090

        allocator:
          command: model-allocator
          client: opencode
          config_root: ${MODEL_ALLOCATOR_CONFIG_ROOT}

        paths:
          repository_root: ${LIGHTWORKER_REPOSITORY_ROOT}
          worktree_root: $LIGHTWORKER_WORKTREE_ROOT
          artifact_root: ${LIGHTWORKER_ARTIFACT_ROOT}
          log_root: ${LIGHTWORKER_LOG_ROOT}
          opencode_config_root: ${LIGHTWORKER_OPENCODE_CONFIG_ROOT}

        retention:
          successful_worktree_hours: 1
          failed_worktree_days: 7
          artifact_days: 14
          log_days: 30
        """
    )
    config_path = _write_config(tmp_path, body)
    config = load_config(config_path)
    assert config.paths.worktree_root == "/srv/lightworker/worktrees"


def test_load_config_missing_top_level_section(
    tmp_path: Path, example_env: None
) -> None:
    """A missing top-level section is reported with the section name."""
    body = textwrap.dedent(
        """\
        worker:
          id: svend3060
          display_name: RTX 3060 LightWorker
          max_parallel_executions: 1
          poll_interval_seconds: 10
          heartbeat_interval_seconds: 15
        """
    )
    config_path = _write_config(tmp_path, body)
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(config_path)
    assert "father" in str(excinfo.value)


def test_load_config_wrong_field_type(
    tmp_path: Path, example_env: None
) -> None:
    """A non-string field is rejected with a clear message."""
    body = _EXAMPLE_YAML.replace("display_name: RTX 3060 LightWorker",
                                "display_name: 12345")
    config_path = _write_config(tmp_path, body)
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(config_path)
    assert "display_name" in str(excinfo.value)
    assert "string" in str(excinfo.value)


def test_load_config_reports_io_errors(tmp_path: Path) -> None:
    """A missing file is reported as a load failure."""
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(missing)
    assert "does-not-exist.yaml" in str(excinfo.value)


def test_load_config_reports_invalid_yaml(tmp_path: Path) -> None:
    """Malformed YAML is reported as a load failure."""
    config_path = _write_config(tmp_path, "worker: : invalid\n")
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(config_path)
    assert "YAML" in str(excinfo.value)


def test_load_config_rejects_top_level_non_mapping(
    tmp_path: Path, example_env: None
) -> None:
    """Top-level must be a YAML mapping."""
    config_path = _write_config(tmp_path, "just a string\n")
    with pytest.raises(WorkerConfigError):
        load_config(config_path)


def test_loaded_config_is_immutable(
    tmp_path: Path, example_env: None
) -> None:
    """WorkerConfig is a frozen dataclass — no mutation after load."""
    config_path = _write_config(tmp_path, _EXAMPLE_YAML)
    config = load_config(config_path)
    with pytest.raises((AttributeError, Exception)):
        config.worker.id = "other"  # type: ignore[misc]
    with pytest.raises((AttributeError, Exception)):
        config.paths.repository_root = "/elsewhere"  # type: ignore[misc]


def test_boolean_not_accepted_as_integer(
    tmp_path: Path, example_env: None
) -> None:
    """Booleans must not be silently coerced to integers."""
    body = _EXAMPLE_YAML.replace(
        "max_parallel_executions: 1", "max_parallel_executions: true"
    )
    config_path = _write_config(tmp_path, body)
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(config_path)
    assert "max_parallel_executions" in str(excinfo.value)



def test_execution_timeout_is_read_when_set(tmp_path, example_env):
    """Until 2026-08-07 this knob was documented in worker.py's comments and
    did not exist on WorkerSection, so setting it in worker.yaml silently did
    nothing. A knob that is documented must exist."""
    body = _EXAMPLE_YAML.replace(
        "poll_interval_seconds: 10",
        "poll_interval_seconds: 10\n  execution_timeout_seconds: 300",
    )
    cfg = load_config(_write_config(tmp_path, body))
    assert cfg.worker.execution_timeout_seconds == 300


def test_execution_timeout_defaults_to_zero_meaning_builtin(tmp_path, example_env):
    """0 is 'use the loop's built-in default' -- every worker.yaml written
    before the knob existed must keep loading."""
    cfg = load_config(_write_config(tmp_path, _EXAMPLE_YAML))
    assert cfg.worker.execution_timeout_seconds == 0


def test_execution_timeout_with_the_wrong_type_raises(tmp_path, example_env):
    """Absent is 0; present-but-wrong must not be. YAML's `yes` arrives as a
    bool, which is an int subclass, and silently becoming 1 second would be
    worse than an error."""
    body = _EXAMPLE_YAML.replace(
        "poll_interval_seconds: 10",
        "poll_interval_seconds: 10\n  execution_timeout_seconds: yes",
    )
    with pytest.raises(WorkerConfigError):
        load_config(_write_config(tmp_path, body))
