"""Worker configuration loader (GOAL.md §12).

The worker configuration is YAML. ``${VAR}`` references in any string
field are expanded from the process environment at load time. An
unset variable is a load failure whose message names the variable —
the operator hitting this error is on a machine they have not yet
provisioned and the variable name is the entire content of the
answer they need.

The return type is a typed dataclass tree, matching run 001's style
(models, errors, envelope_validator are all ``frozen=True``,
``slots=True`` dataclasses). Consumers read typed attributes instead
of navigating free-form mappings, and the loader is the only
constructor.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Union

import yaml


ConfigPath = Union[str, os.PathLike]


_ENV_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)


class WorkerConfigError(Exception):
    """Raised when the worker configuration cannot be loaded.

    The message names the variable or field that caused the failure,
    because the operator hitting this error is on a machine they have
    not provisioned yet and the variable name is the entire answer.
    """


@dataclass(frozen=True, slots=True)
class WorkerSection:
    """The ``worker:`` block of GOAL.md §12."""

    id: str
    display_name: str
    max_parallel_executions: int
    poll_interval_seconds: int
    heartbeat_interval_seconds: int


@dataclass(frozen=True, slots=True)
class FatherSection:
    """The ``father:`` block of GOAL.md §12."""

    base_url: str
    request_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class NetworkSection:
    """The ``network:`` block of GOAL.md §12."""

    type: str
    expected_father_host: str


@dataclass(frozen=True, slots=True)
class AllocatorSection:
    """The ``allocator:`` block of GOAL.md §12."""

    command: str
    client: str
    config_root: str


@dataclass(frozen=True, slots=True)
class PathsSection:
    """The ``paths:`` block of GOAL.md §12."""

    repository_root: str
    worktree_root: str
    artifact_root: str
    log_root: str
    opencode_config_root: str


@dataclass(frozen=True, slots=True)
class RetentionSection:
    """The ``retention:`` block of GOAL.md §12."""

    successful_worktree_hours: int
    failed_worktree_days: int
    artifact_days: int
    log_days: int


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """A validated worker configuration."""

    worker: WorkerSection
    father: FatherSection
    network: NetworkSection
    allocator: AllocatorSection
    paths: PathsSection
    retention: RetentionSection


def load_config(path: ConfigPath) -> WorkerConfig:
    """Load and validate the worker configuration from ``path``.

    ``${VAR}`` references in string values are expanded from the
    process environment. An unset variable raises
    :class:`WorkerConfigError` whose message names the variable.

    :param path: Filesystem path to a YAML file. ``str`` or any
        ``os.PathLike`` is accepted.
    :returns: A typed, validated :class:`WorkerConfig`.
    :raises WorkerConfigError: on missing keys, wrong types, or any
        unset environment variable referenced by a ``${VAR}`` token.
    """
    path_obj = Path(path)
    try:
        raw_text = path_obj.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkerConfigError(
            f"cannot read worker configuration file {path_obj}: {exc}"
        ) from exc

    try:
        loaded = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise WorkerConfigError(
            f"worker configuration file {path_obj} is not valid YAML: {exc}"
        ) from exc

    if not isinstance(loaded, Mapping):
        raise WorkerConfigError(
            f"worker configuration file {path_obj} must be a YAML mapping "
            f"at the top level, got {type(loaded).__name__}"
        )

    expanded = _expand_env(loaded)

    return _parse(expanded, source=str(path_obj))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` references from ``os.environ``."""
    if isinstance(value, str):
        return _expand_string(value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _expand_env(val) for key, val in value.items()}
    return value


def _expand_string(text: str) -> str:
    """Expand every ``${VAR}`` or ``$VAR`` reference in ``text``.

    An unset variable raises :class:`WorkerConfigError` whose message
    names the variable. This is the contract: the operator hitting
    this error has not provisioned the variable yet and the name is
    the entire content of the answer.
    """

    def replace(match: "re.Match[str]") -> str:
        name = match.group(1) or match.group(2)
        if name is None:
            return match.group(0)
        if name not in os.environ:
            raise WorkerConfigError(
                f"environment variable {name!r} is referenced by the worker "
                f"configuration but is not set; set it (for example in "
                f".env) before loading this configuration"
            )
        return os.environ[name]

    return _ENV_RE.sub(replace, text)


def _parse(data: Mapping[str, Any], *, source: str) -> WorkerConfig:
    """Parse a top-level mapping into a :class:`WorkerConfig`."""
    worker_raw = _section(data, "worker", source)
    father_raw = _section(data, "father", source)
    network_raw = _section(data, "network", source)
    allocator_raw = _section(data, "allocator", source)
    paths_raw = _section(data, "paths", source)
    retention_raw = _section(data, "retention", source)

    return WorkerConfig(
        worker=_parse_worker(worker_raw, source),
        father=_parse_father(father_raw, source),
        network=_parse_network(network_raw, source),
        allocator=_parse_allocator(allocator_raw, source),
        paths=_parse_paths(paths_raw, source),
        retention=_parse_retention(retention_raw, source),
    )


def _section(
    data: Mapping[str, Any], name: str, source: str
) -> Mapping[str, Any]:
    if name not in data:
        raise WorkerConfigError(
            f"worker configuration file {source} is missing the top-level "
            f"{name!r} section"
        )
    section = data[name]
    if not isinstance(section, Mapping):
        raise WorkerConfigError(
            f"worker configuration file {source}: {name!r} section must be a "
            f"mapping, got {type(section).__name__}"
        )
    return section


def _parse_worker(section: Mapping[str, Any], source: str) -> WorkerSection:
    return WorkerSection(
        id=_required_str(section, "id", "worker", source),
        display_name=_required_str(section, "display_name", "worker", source),
        max_parallel_executions=_required_int(
            section, "max_parallel_executions", "worker", source
        ),
        poll_interval_seconds=_required_int(
            section, "poll_interval_seconds", "worker", source
        ),
        heartbeat_interval_seconds=_required_int(
            section, "heartbeat_interval_seconds", "worker", source
        ),
    )


def _parse_father(section: Mapping[str, Any], source: str) -> FatherSection:
    return FatherSection(
        base_url=_required_str(section, "base_url", "father", source),
        request_timeout_seconds=_required_int(
            section, "request_timeout_seconds", "father", source
        ),
    )


def _parse_network(section: Mapping[str, Any], source: str) -> NetworkSection:
    return NetworkSection(
        type=_required_str(section, "type", "network", source),
        expected_father_host=_required_str(
            section, "expected_father_host", "network", source
        ),
    )


def _parse_allocator(
    section: Mapping[str, Any], source: str
) -> AllocatorSection:
    return AllocatorSection(
        command=_required_str(section, "command", "allocator", source),
        client=_required_str(section, "client", "allocator", source),
        config_root=_required_str(section, "config_root", "allocator", source),
    )


def _parse_paths(section: Mapping[str, Any], source: str) -> PathsSection:
    return PathsSection(
        repository_root=_required_str(
            section, "repository_root", "paths", source
        ),
        worktree_root=_required_str(
            section, "worktree_root", "paths", source
        ),
        artifact_root=_required_str(
            section, "artifact_root", "paths", source
        ),
        log_root=_required_str(section, "log_root", "paths", source),
        opencode_config_root=_required_str(
            section, "opencode_config_root", "paths", source
        ),
    )


def _parse_retention(
    section: Mapping[str, Any], source: str
) -> RetentionSection:
    return RetentionSection(
        successful_worktree_hours=_required_int(
            section, "successful_worktree_hours", "retention", source
        ),
        failed_worktree_days=_required_int(
            section, "failed_worktree_days", "retention", source
        ),
        artifact_days=_required_int(
            section, "artifact_days", "retention", source
        ),
        log_days=_required_int(section, "log_days", "retention", source),
    )


def _required_str(
    section: Mapping[str, Any], key: str, section_name: str, source: str
) -> str:
    if key not in section:
        raise WorkerConfigError(
            f"worker configuration file {source}: {section_name!r} section "
            f"is missing required string field {key!r}"
        )
    value = section[key]
    if not isinstance(value, str):
        raise WorkerConfigError(
            f"worker configuration file {source}: {section_name!r}.{key} must "
            f"be a string, got {type(value).__name__}"
        )
    return value


def _required_int(
    section: Mapping[str, Any], key: str, section_name: str, source: str
) -> int:
    if key not in section:
        raise WorkerConfigError(
            f"worker configuration file {source}: {section_name!r} section "
            f"is missing required integer field {key!r}"
        )
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkerConfigError(
            f"worker configuration file {source}: {section_name!r}.{key} must "
            f"be an integer, got {type(value).__name__}"
        )
    return value


__all__ = [
    "AllocatorSection",
    "ConfigPath",
    "FatherSection",
    "NetworkSection",
    "PathsSection",
    "RetentionSection",
    "WorkerConfig",
    "WorkerConfigError",
    "WorkerSection",
    "load_config",
]
