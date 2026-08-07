"""Tests for the execution-specific OpenCode config pipeline (GOAL.md §§9, 19, 33)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List

import pytest

from dpmtf_lightworker.client_config import (
    render_execution_config,
    validate_rendered_config,
)
from dpmtf_lightworker.errors import ClientConfigRenderFailed


# ---------------------------------------------------------------------------
# Fake allocator
# ---------------------------------------------------------------------------


class FakeAllocator:
    """A stand-in for ``AllocatorAdapter`` that records calls and writes a canned config."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.calls: List[Any] = []

    def render_config(self, *, role: str, client: str, output: str) -> str:
        self.calls.append({"role": role, "client": client, "output": output})
        Path(output).write_text(self.body, encoding="utf-8")
        return f"wrote {output}"


def _valid_body() -> str:
    return json.dumps(
        {
            "permission": {"read": True},
            "mcp": {},
            "model": "imple01-3060",
            "provider": {"cloud_minimax": {}},
        }
    )


# ---------------------------------------------------------------------------
# validate_rendered_config
# ---------------------------------------------------------------------------


def test_validate_rendered_config_passes_for_valid_body() -> None:
    validate_rendered_config(_valid_body())


def test_validate_rendered_config_raises_for_invalid_json() -> None:
    with pytest.raises(ClientConfigRenderFailed) as exc:
        validate_rendered_config("not json at all")
    assert "not valid JSON" in str(exc.value)
    assert exc.value.category == "CLIENT_CONFIG_RENDER_FAILED"


def test_validate_rendered_config_raises_for_non_object_top_level() -> None:
    with pytest.raises(ClientConfigRenderFailed) as exc:
        validate_rendered_config(json.dumps(["an", "array", "not", "object"]))
    assert "not a JSON object" in str(exc.value)


def test_validate_rendered_config_raises_for_non_string_input() -> None:
    with pytest.raises(ClientConfigRenderFailed):
        validate_rendered_config(123)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "missing",
    # `mcp` is absent from this list on purpose. §9 lists it among keys the
    # merge must preserve *if present*, which is not the same as requiring
    # one; Father's own OpenCode config has none, and requiring it made a
    # correct config unusable in lightworker run 001.
    ["permission", "model", "provider"],
)
def test_validate_rendered_config_raises_when_required_block_missing(
    missing: str,
) -> None:
    body = json.dumps(
        {
            "permission": {"read": True},
            "mcp": {},
            "model": "imple01-3060",
            "provider": {"cloud_minimax": {}},
        }
    )
    data = json.loads(body)
    del data[missing]
    with pytest.raises(ClientConfigRenderFailed) as exc:
        validate_rendered_config(json.dumps(data))
    assert missing in str(exc.value)


# ---------------------------------------------------------------------------
# render_execution_config — happy path
# ---------------------------------------------------------------------------


def test_render_execution_config_returns_published_path(
    tmp_path: Path,
) -> None:
    target = tmp_path / "opencode.json"
    allocator = FakeAllocator(_valid_body())
    path = render_execution_config(allocator, "imple01", str(target))
    # The contract binds the return value to the published path,
    # as a str — not a Path, not the rendered content.
    assert path == str(target)
    # The allocator was still called with role=imple01 and
    # client=opencode, and the target file holds the rendered body.
    assert allocator.calls and allocator.calls[0]["role"] == "imple01"
    assert allocator.calls[0]["client"] == "opencode"
    # Compared as JSON, not as bytes. The published file is re-serialised so
    # the base config can be merged underneath it, so formatting is not
    # preserved -- the config is the value, its whitespace is not.
    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(
        _valid_body())


def test_render_execution_config_calls_allocator_with_keyword_arguments(
    tmp_path: Path,
) -> None:
    target = tmp_path / "opencode.json"
    allocator = FakeAllocator(_valid_body())
    render_execution_config(allocator, "imple01", str(target))
    assert allocator.calls == [
        {"role": "imple01", "client": "opencode", "output": allocator.calls[0]["output"]}
    ]
    # The output the allocator saw must be a real path string, not None.
    assert isinstance(allocator.calls[0]["output"], str)
    assert allocator.calls[0]["output"]  # non-empty


# ---------------------------------------------------------------------------
# render_execution_config — atomicity and ordering (Step 5b)
# ---------------------------------------------------------------------------


def test_render_execution_config_validates_before_publishing(
    tmp_path: Path,
) -> None:
    """An invalid render must never reach the published path."""
    target = tmp_path / "opencode.json"
    allocator = FakeAllocator(_valid_body().replace('"permission"', '"wrong"'))
    with pytest.raises(ClientConfigRenderFailed):
        render_execution_config(allocator, "imple01", str(target))
    # The target path must not exist after a validation failure.
    assert not target.exists()


def test_render_execution_config_publishes_atomically_on_success(
    tmp_path: Path,
) -> None:
    target = tmp_path / "opencode.json"
    allocator = FakeAllocator(_valid_body())
    render_execution_config(allocator, "imple01", str(target))
    assert target.exists()
    # No temp file may be left behind on the success path.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".opencode-")]
    assert leftovers == []


def test_render_execution_config_no_temp_leftover_on_validation_failure(
    tmp_path: Path,
) -> None:
    target = tmp_path / "opencode.json"
    # Valid JSON, but with no `permission` block -- the one §19 makes
    # load-bearing, because it is what confines the role to its worktree.
    body = json.dumps(
        {
            "model": "imple01-3060",
            "provider": {"cloud_minimax": {}},
        }
    )
    allocator = FakeAllocator(body)
    with pytest.raises(ClientConfigRenderFailed):
        render_execution_config(allocator, "imple01", str(target))
    # The target must never have been created.
    assert not target.exists()
    # No temp file may be left behind on the failure path.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".opencode-")]
    assert leftovers == []


def test_render_execution_config_uses_temp_in_same_directory_as_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "opencode.json"
    captured: dict = {}

    class RecordingAllocator(FakeAllocator):
        def render_config(self, *, role: str, client: str, output: str) -> str:
            captured["output"] = output
            captured["output_dir"] = os.path.dirname(output)
            return super().render_config(
                role=role, client=client, output=output
            )

    allocator = RecordingAllocator(_valid_body())
    render_execution_config(allocator, "imple01", str(target))
    # The temp path must sit beside the target so os.replace is a rename
    # within one filesystem. It is inside a temporary DIRECTORY there, not a
    # temporary file: `render-config` merges into an existing output to
    # preserve the permission and mcp blocks (§9), and a zero-byte file makes
    # it refuse with "existing opencode.json is not valid JSON". lightworker
    # run 001 hit exactly that on the first real execution.
    assert os.path.dirname(captured["output_dir"]) == str(tmp_path), \
        f'temp lives at {captured["output_dir"]}, not beside {tmp_path}'
    assert captured["output_dir"] != str(tmp_path), \
        "the temp path is directly in the target directory again"


def test_the_allocator_is_handed_a_path_that_does_not_exist_yet(
    tmp_path: Path,
) -> None:
    """`render-config` refuses an existing output it cannot parse.

    It preserves what it finds (§9), so handing it the zero-byte file
    `mkstemp` leaves behind makes it fail with "existing opencode.json is not
    valid JSON". Found on lightworker run 001 at RENDERING_CLIENT_CONFIG:
    both sides were correct alone and disagreed about whether the output path
    exists beforehand.
    """
    target = tmp_path / "opencode.json"
    seen: dict = {}

    class ExistenceCheckingAllocator(FakeAllocator):
        def render_config(self, *, role: str, client: str, output: str) -> str:
            seen["existed"] = os.path.exists(output)
            return super().render_config(role=role, client=client, output=output)

    render_execution_config(
        ExistenceCheckingAllocator(_valid_body()), "imple01", str(target))
    assert seen["existed"] is False, \
        "the allocator was handed an output path that already existed"


def test_render_execution_config_raises_when_allocator_writes_nothing(
    tmp_path: Path,
) -> None:
    target = tmp_path / "opencode.json"

    class SilentAllocator(FakeAllocator):
        def render_config(self, *, role: str, client: str, output: str) -> str:
            # Do not write the file.
            return ""

    with pytest.raises(ClientConfigRenderFailed) as exc:
        render_execution_config(SilentAllocator(_valid_body()), "imple01", str(target))
    assert "did not write" in str(exc.value)
    assert not target.exists()


def test_render_execution_config_rejects_empty_role(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        render_execution_config(
            FakeAllocator(_valid_body()), "", str(tmp_path / "x.json")
        )


def test_render_execution_config_rejects_empty_output_path() -> None:
    with pytest.raises(ValueError):
        render_execution_config(FakeAllocator(_valid_body()), "imple01", "")
