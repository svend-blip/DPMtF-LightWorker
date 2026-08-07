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
    "model",
    "provider",
)

# `mcp` is deliberately not required. §9 says the merge must preserve
# existing keys "such as $schema, permission, mcp, other providers" — a
# conditional list of what must survive, not a list of what must be
# present. Father's own OpenCode config on this network has no mcp block
# at all, so requiring one made a correct config unusable.
#
# `permission` is required, because §19 makes it load-bearing: it is what
# confines the role to its worktree, and a config without it is not a
# weaker config but an unconfined one.

# The placeholder a base config uses for the execution's worktree. The
# worktree path is different on every execution and is not known when the
# operator writes the file.
WORKTREE_PLACEHOLDER = "{worktree}"


def _load_base_config(base_config_path: str, worktree: str) -> dict:
    """Read the operator's base OpenCode config and bind it to this worktree.

    The allocator renders only `model` and `provider`; it never produces
    `permission` or `mcp`. It *merges into* an existing config, which is
    what §9 means by preserving existing keys. So something has to write
    that existing config first, and it is not Father: §19 is explicit that
    the worker must not depend on Father knowing its filesystem.

    The file belongs to the machine's steward and is named in
    `config/worker.yaml`. Occurrences of ``{worktree}`` are replaced with
    this execution's worktree, which no operator can write down in advance.
    """
    path = Path(base_config_path).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClientConfigRenderFailed(
            f"base client config could not be read at {path}: {exc}"
        ) from exc
    text = text.replace(WORKTREE_PLACEHOLDER, worktree)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClientConfigRenderFailed(
            f"base client config at {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise ClientConfigRenderFailed(
            f"base client config at {path} is not a JSON object"
        )
    return dict(data)


def _deep_merge(base: Mapping, override: Mapping) -> dict:
    """Merge ``override`` onto ``base``, recursing into nested objects.

    A shallow merge is what the allocator does, and it is why this exists:
    replacing `provider.ollama` wholesale drops the endpoint the machine
    configured while adding the model the role needs. Both belong in the
    same object and neither side knows about the other.

    ``override`` wins every conflict. A list replaces rather than extends —
    an allowlist that silently grew by concatenation would be worse than
    one that is simply stated.
    """
    out = dict(base)
    for key, value in override.items():
        if (
            key in out
            and isinstance(out[key], Mapping)
            and isinstance(value, Mapping)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def render_execution_config(
    allocator: "AllocatorAdapter",
    role: str,
    output_path: str,
    base_config_path: str = "",
    worktree: str = "",
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
        rendered = json.loads(tmp_path.read_text(encoding="utf-8"))

        # The base config goes UNDERNEATH what the allocator produced.
        #
        # Seeding it first and letting `render-config` merge into it was the
        # obvious order and it loses data: the allocator replaces
        # `provider.<name>` wholesale rather than merging within it. Measured
        # on svend3060 — a base carrying `provider.ollama.options.baseURL`
        # came back with only `provider.ollama.models`, and OpenCode then
        # failed with:
        #
        #   "undefined/chat/completions" cannot be parsed as a URL.
        #
        # Merging here instead makes the worker's own semantics the ones that
        # matter: deep, with the allocator winning every conflict. It renders
        # the model and its limits (§32); the base supplies everything about
        # this machine — the permission block, the provider endpoint, any mcp.
        if base_config_path:
            base = _load_base_config(base_config_path, worktree)
            rendered = _deep_merge(base, rendered)

        text = json.dumps(rendered, indent=2) + "\n"
        tmp_path.write_text(text, encoding="utf-8")
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

    A usable config carries ``permission`` — §19's confinement — plus the
    ``model`` and ``provider`` the allocator renders. JSON that is missing,
    unparseable, or not a top-level object is equally unusable.

    ``mcp`` is not required. §9 lists it among keys the merge must preserve
    *if present*, which is not the same as requiring one; Father's own
    OpenCode config has none.
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
