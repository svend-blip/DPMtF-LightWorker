"""The permission block comes from a base config the worker's steward owns.

`model-allocator render-config` emits only `model` and `provider`. It never
produces `permission` or `mcp` -- it *merges into* a config that already
exists, which is what §9 means by preserving existing keys. So something has
to write that config first.

Not Father: §19 is explicit that the worker must not depend on Father knowing
its filesystem. The file belongs to the machine's steward and is named in
config/worker.yaml.

lightworker run 001 found this the hard way. Rendering into an empty directory
succeeded and produced a config with no permission block at all -- not a
weaker confinement but none.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dpmtf_lightworker.client_config import (  # noqa: E402
    WORKTREE_PLACEHOLDER,
    render_execution_config,
    validate_rendered_config,
)
from dpmtf_lightworker.errors import ClientConfigRenderFailed  # noqa: E402


class MergingAllocator:
    """Stands in for `render-config`: merges model+provider into what it finds.

    That is the real behaviour, and it is the whole reason a base config is
    needed. An allocator fake that ignored the existing file would let a
    broken implementation pass.
    """

    def render_config(self, *, role: str, client: str, output: str) -> None:
        path = Path(output)
        existing = {}
        if path.exists() and path.stat().st_size:
            existing = json.loads(path.read_text(encoding="utf-8"))
        existing.update({"model": "m", "provider": {"ollama": {}}})
        path.write_text(json.dumps(existing), encoding="utf-8")


def _base(tmp_path, body):
    p = tmp_path / "base.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return str(p)


def test_the_permission_block_survives_the_merge(tmp_path):
    base = _base(tmp_path, {"permission": {"external_directory": {"/w": "allow"}}})
    target = tmp_path / "out" / "opencode.json"
    render_execution_config(
        MergingAllocator(), "imple01LW", str(target), base, "/w")
    published = json.loads(target.read_text(encoding="utf-8"))
    assert published["permission"] == {"external_directory": {"/w": "allow"}}
    assert published["model"] == "m"


def test_the_worktree_is_bound_into_the_base_config(tmp_path):
    """The worktree differs on every execution, so no operator can write it
    down in advance."""
    base = _base(tmp_path, {
        "permission": {"external_directory": {
            WORKTREE_PLACEHOLDER: "allow",
            WORKTREE_PLACEHOLDER + "/**": "allow",
        }}})
    target = tmp_path / "opencode.json"
    render_execution_config(
        MergingAllocator(), "imple01LW", str(target), base,
        "/home/svend/lightworker/worktrees/EXEC-004")
    allowed = json.loads(target.read_text(encoding="utf-8"))["permission"][
        "external_directory"]
    assert "/home/svend/lightworker/worktrees/EXEC-004" in allowed
    assert WORKTREE_PLACEHOLDER not in json.dumps(allowed)


def test_no_base_config_means_no_permission_block_and_a_refusal(tmp_path):
    """The state every worker.yaml written before 2026-08-07 produces. It must
    fail loudly: an unconfined role is worse than a stopped one."""
    target = tmp_path / "opencode.json"
    with pytest.raises(ClientConfigRenderFailed) as exc:
        render_execution_config(MergingAllocator(), "imple01LW", str(target))
    assert "permission" in str(exc.value)
    assert not target.exists()


def test_an_unreadable_base_config_names_the_path(tmp_path):
    target = tmp_path / "opencode.json"
    missing = str(tmp_path / "nope.json")
    with pytest.raises(ClientConfigRenderFailed) as exc:
        render_execution_config(
            MergingAllocator(), "imple01LW", str(target), missing, "/w")
    assert missing in str(exc.value)


def test_a_malformed_base_config_is_not_silently_skipped(tmp_path):
    """Skipping it would produce an unconfined config from a typo."""
    bad = tmp_path / "base.json"
    bad.write_text("{ not json", encoding="utf-8")
    target = tmp_path / "opencode.json"
    with pytest.raises(ClientConfigRenderFailed) as exc:
        render_execution_config(
            MergingAllocator(), "imple01LW", str(target), str(bad), "/w")
    assert "not valid JSON" in str(exc.value)
    assert not target.exists()


def test_mcp_is_not_required():
    """§9 lists it among keys to preserve if present, not keys to require.
    Father's own OpenCode config has none."""
    validate_rendered_config(json.dumps({
        "permission": {}, "model": "m", "provider": {}}))


def test_a_config_without_permission_is_still_refused():
    with pytest.raises(ClientConfigRenderFailed):
        validate_rendered_config(json.dumps({"model": "m", "provider": {}}))


class TestTheMergeIsDeep:
    """The allocator replaces `provider.<name>` wholesale.

    Measured on svend3060: a base carrying `provider.ollama.options.baseURL`
    came back with only `provider.ollama.models`, and OpenCode then failed
    with `"undefined/chat/completions" cannot be parsed as a URL` -- the
    endpoint the machine configured had been dropped while adding the model
    the role needed. Both belong in the same object and neither side knows
    about the other.
    """

    def test_the_provider_endpoint_survives_the_model_being_added(self, tmp_path):
        base = _base(tmp_path, {
            "permission": {},
            "provider": {"ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {"baseURL": "http://localhost:11434/v1"},
            }},
        })
        target = tmp_path / "opencode.json"
        render_execution_config(
            MergingAllocator(), "imple01LW", str(target), base, "/w")
        ollama = json.loads(target.read_text(encoding="utf-8"))["provider"]["ollama"]
        assert ollama["options"]["baseURL"] == "http://localhost:11434/v1"
        assert ollama["npm"] == "@ai-sdk/openai-compatible"

    def test_the_allocator_wins_a_genuine_conflict(self):
        """§32 makes the model and its context the allocator's to decide. A
        base config must not be able to quietly pin an older one."""
        from dpmtf_lightworker.client_config import _deep_merge
        merged = _deep_merge({"model": "stale", "provider": {"o": {"a": 1}}},
                             {"model": "fresh", "provider": {"o": {"b": 2}}})
        assert merged["model"] == "fresh"
        assert merged["provider"]["o"] == {"a": 1, "b": 2}

    def test_a_list_replaces_rather_than_extends(self):
        """An allowlist that silently grew by concatenation would be worse
        than one that is simply stated."""
        from dpmtf_lightworker.client_config import _deep_merge
        assert _deep_merge({"k": [1, 2]}, {"k": [3]})["k"] == [3]


class TestTheRenderedConfigIsTheOneOpenCodeReads:
    """A config nobody opens is not a config.

    The worker rendered a per-execution config, merged in the permission
    block confining the role to its worktree (§19) and this machine's
    provider endpoint, validated it and published it -- and then asked the
    allocator for a launch command that named the allocator's own shared role
    file. Three executions ran that way. The role had no confinement at all,
    and OpenCode failed on an endpoint it did not have.

    The command still comes back verbatim (§34). The worker asks for a
    different command; it does not edit the one it gets.
    """

    def test_the_launch_command_is_asked_for_the_published_config(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tests.fakes import envelope, loop_with

        loop, spies = loop_with(offered=envelope())
        loop.run_once()
        paths = spies["allocator"].run_config_paths
        assert paths and paths[-1], "run was asked for no config at all"
        assert paths[-1].endswith(".json")

    def test_it_is_the_same_file_that_was_published(self):
        """Naming a different path would be the same defect wearing the
        opposite disguise."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tests.fakes import envelope, loop_with

        loop, spies = loop_with(offered=envelope())
        loop.run_once()
        rendered = [e for e in loop._events
                    if e.event_type.value == "CLIENT_CONFIG_RENDERED"]
        assert rendered, "nothing was published"
        assert spies["allocator"].run_config_paths[-1] == \
            rendered[-1].payload["path"]
