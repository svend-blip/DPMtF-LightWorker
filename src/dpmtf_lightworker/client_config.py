"""Execution-specific OpenCode client configuration (GOAL.md §§9, 19, 33)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from dpmtf_lightworker.errors import ClientConfigRenderFailed

if TYPE_CHECKING:
    from dpmtf_lightworker.allocator import AllocatorAdapter


_REQUIRED_BLOCKS = (
    "permission",
    "mcp",
    "model",
    "provider",
)


def render_execution_config(
    allocator: "AllocatorAdapter",
    role: str,
    output_path: str,
) -> str:
    """Render an execution-specific OpenCode config, validate, and publish.

    §33 requires the sequence render → validate → publish, in that order.
    The render writes to a temporary file in the **same directory** as the
    target, so the eventual ``os.replace`` is a rename within one
    filesystem and cannot half-succeed. The target path is replaced
    atomically only after validation passes; on any failure the target is
    never created and the temporary file is removed.

    Returns the published path as a ``str`` (not a ``Path``, not the
    rendered content). The path is exactly ``str(output_path)`` — the
    caller's argument, verbatim.
    """
    if not isinstance(role, str) or not role:
        raise ValueError("role must be a non-empty string")
    if not isinstance(output_path, str) or not output_path:
        raise ValueError("output_path must be a non-empty string")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # A temporary DIRECTORY, not a temporary file. `mkstemp` creates a
    # zero-byte file, and `render-config` treats an existing output as one to
    # merge into — §9 requires it to preserve an existing permission and mcp
    # block. Handed an empty file it refuses:
    #
    #   ERROR: existing opencode.json is not valid JSON:
    #   Expecting value: line 1 column 1 (char 0)
    #
    # lightworker run 001 found this on the first real execution, at
    # RENDERING_CLIENT_CONFIG. Both sides were right on their own: this
    # function reserves its temp path so the publish is atomic, and the
    # allocator preserves what it finds. They disagreed about whether the
    # output path exists beforehand.
    #
    # The directory sits beside the target, so `os.replace` is still a rename
    # within one filesystem and the publish stays atomic.
    tmp_dir = Path(tempfile.mkdtemp(prefix=".opencode-", dir=str(target.parent)))
    tmp_path = tmp_dir / target.name
    tmp_name = str(tmp_path)
    try:
        try:
            allocator.render_config(
                role=role, client="opencode", output=tmp_name
            )
        except ClientConfigRenderFailed:
            raise
        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            raise ClientConfigRenderFailed(
                f"allocator did not write a config to {tmp_path}"
            )
        text = tmp_path.read_text(encoding="utf-8")
        # Validate before publishing: a config that fails validation must
        # never have existed at the target path. This is the §33 ordering
        # and the property the testgoal cannot measure.
        validate_rendered_config(text)
        os.replace(tmp_name, str(target))
        return str(target)
    except BaseException:
        # On any failure (validation, allocator, IO), remove the temp
        # directory so nothing is left behind. The target path was never
        # created.
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def validate_rendered_config(text: str) -> None:
    """Raise :class:`ClientConfigRenderFailed` if the rendered config is unusable.

    §33 names the four required blocks: ``permission``, ``mcp``, the
    top-level ``model`` field, and a ``provider`` block. JSON that is
    missing, unparseable, or not a top-level object is equally
    unusable.
    """
    if not isinstance(text, str):
        raise ClientConfigRenderFailed(
            f"rendered config must be a string, got {type(text).__name__}"
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClientConfigRenderFailed(
            f"rendered config is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise ClientConfigRenderFailed(
            "rendered config is not a JSON object at the top level"
        )
    missing = [name for name in _REQUIRED_BLOCKS if name not in data]
    if missing:
        raise ClientConfigRenderFailed(
            "rendered config missing required blocks: "
            + ", ".join(missing)
        )


__all__ = ["render_execution_config", "validate_rendered_config"]
