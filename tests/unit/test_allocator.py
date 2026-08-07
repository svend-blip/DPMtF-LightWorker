"""Tests for the model-allocator adapter (GOAL.md §§14, 15, 31, 32, 35)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from dpmtf_lightworker.allocator import AllocatorAdapter, RunnerResult
from dpmtf_lightworker.errors import (
    AliasValidationFailed,
    AllocatorError,
    AllocatorNotAvailable,
    AllocatorPreflightFailed,
    ClientConfigRenderFailed,
    EnvelopeError,
    RuntimeReleaseFailed,
)


class FakeRunner:
    """A test runner that records argv/timout pairs and returns canned output."""

    def __init__(
        self,
        response: RunnerResult = (0, "ok", ""),
        responses: List[RunnerResult] | None = None,
        side_effect: Any = None,
    ) -> None:
        self.calls: List[Tuple[List[str], float]] = []
        self._response = response
        self._responses = list(responses) if responses is not None else None
        self._side_effect = side_effect

    def __call__(self, argv: List[str], timeout: float) -> RunnerResult:
        self.calls.append((list(argv), timeout))
        if self._side_effect is not None:
            return self._side_effect(argv, timeout)
        if self._responses is not None:
            if not self._responses:
                raise AssertionError("no more responses queued")
            return self._responses.pop(0)
        return self._response


def _adapter(runner: FakeRunner, **kwargs: Any) -> AllocatorAdapter:
    defaults: dict[str, Any] = {"command": "model-allocator", "runner": runner, "timeout": 12.5}
    defaults.update(kwargs)
    return AllocatorAdapter(**defaults)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_empty_command_rejected() -> None:
    with pytest.raises(ValueError):
        AllocatorAdapter(command="")


def test_non_positive_timeout_rejected() -> None:
    with pytest.raises(ValueError):
        AllocatorAdapter(command="model-allocator", runner=FakeRunner(), timeout=0)


# ---------------------------------------------------------------------------
# §14 / §31: argument construction is safe
# ---------------------------------------------------------------------------


def test_preflight_builds_role_form() -> None:
    runner = FakeRunner(response=(0, "READY", ""))
    adapter = _adapter(runner)
    assert adapter.preflight("imple01", "opencode") == "READY"
    argv, timeout = runner.calls[0]
    assert argv == ["model-allocator", "preflight", "--role", "imple01", "--client", "opencode"]
    assert timeout == 12.5


def test_validate_builds_alias_form() -> None:
    runner = FakeRunner(response=(0, "OK", ""))
    adapter = _adapter(runner)
    assert adapter.validate("imple01-3060", "opencode") == "OK"
    argv, _ = runner.calls[0]
    assert argv == ["model-allocator", "validate", "--alias", "imple01-3060", "--client", "opencode"]


def test_render_config_passes_output() -> None:
    runner = FakeRunner(response=(0, "Config written to /tmp/x.json", ""))
    adapter = _adapter(runner)
    output = adapter.render_config("imple01", "opencode", "/tmp/x.json")
    assert output == "Config written to /tmp/x.json"
    argv, _ = runner.calls[0]
    assert argv == [
        "model-allocator",
        "render-config",
        "--role",
        "imple01",
        "--client",
        "opencode",
        "--output",
        "/tmp/x.json",
    ]


def test_run_returns_verbatim_shell_string() -> None:
    payload = "opencode --model imple01-3060 --session exec-1"
    runner = FakeRunner(response=(0, payload, ""))
    adapter = _adapter(runner)
    assert adapter.run("imple01", "opencode") == payload
    argv, _ = runner.calls[0]
    assert argv == ["model-allocator", "run", "--role", "imple01", "--client", "opencode"]


def test_alias_never_replaced_by_concrete_model() -> None:
    runner = FakeRunner(response=(0, "ok", ""))
    adapter = _adapter(runner)
    adapter.validate("my-alias-without-model", "opencode")
    argv, _ = runner.calls[0]
    assert "--alias" in argv
    assert "my-alias-without-model" in argv
    assert not any(arg.startswith("--model") for arg in argv)


def test_alias_passed_through_unchanged_for_stop() -> None:
    runner = FakeRunner(response=(0, "", ""))
    adapter = _adapter(runner)
    adapter.release("alias-x", "stop_after_step", other_lease_holders=0)
    argv, _ = runner.calls[0]
    assert argv == ["model-allocator", "stop", "--alias", "alias-x"]


def test_runner_receives_list_and_timeout() -> None:
    """The configured timeout applies to the FAST operations. preflight and
    run get a fixed 600s instead -- they may have to cold-start a model
    server, and EXEC-016 died at 30.0s doing exactly that. A slow resolve
    or stop is still a defect worth surfacing fast."""
    runner = FakeRunner(response=(0, "ok", ""))
    adapter = AllocatorAdapter(command="model-allocator", runner=runner, timeout=4.2)
    adapter.validate_alias("a", "c")
    argv, timeout = runner.calls[0]
    assert isinstance(argv, list)
    assert timeout == 4.2


def test_preflight_and_run_get_cold_start_time() -> None:
    runner = FakeRunner(response=(0, "ok", ""))
    adapter = AllocatorAdapter(command="model-allocator", runner=runner, timeout=4.2)
    adapter.preflight("r", "c")
    adapter.run("r", "c")
    assert [t for _, t in runner.calls] == [600.0, 600.0]


# ---------------------------------------------------------------------------
# §31: stderr capture and structured failure categories
# ---------------------------------------------------------------------------


def test_preflight_failure_carries_stderr() -> None:
    runner = FakeRunner(response=(2, "", "backend not ready"))
    adapter = _adapter(runner)
    with pytest.raises(AllocatorPreflightFailed) as exc:
        adapter.preflight("imple01", "opencode")
    assert exc.value.returncode == 2
    assert "backend not ready" in str(exc.value)
    assert exc.value.category == "ALLOCATOR_PREFLIGHT_FAILED"


def test_validate_failure_raises_alias_validation_failed() -> None:
    runner = FakeRunner(response=(1, "", "alias missing"))
    adapter = _adapter(runner)
    with pytest.raises(AliasValidationFailed) as exc:
        adapter.validate("missing", "opencode")
    assert exc.value.returncode == 1
    assert exc.value.category == "ALIAS_VALIDATION_FAILED"


def test_render_config_failure_raises_client_config_render_failed() -> None:
    runner = FakeRunner(response=(3, "", "render error"))
    adapter = _adapter(runner)
    with pytest.raises(ClientConfigRenderFailed) as exc:
        adapter.render_config("r", "opencode", "/tmp/x.json")
    assert exc.value.category == "CLIENT_CONFIG_RENDER_FAILED"


def test_release_failure_raises_runtime_release_failed() -> None:
    runner = FakeRunner(response=(1, "", "stop failed"))
    adapter = _adapter(runner)
    with pytest.raises(RuntimeReleaseFailed) as exc:
        adapter.release("alias-a", "stop_after_step", other_lease_holders=0)
    assert exc.value.category == "RUNTIME_RELEASE_FAILED"


def test_subprocess_start_failure_raises_allocator_not_available() -> None:
    def boom(argv: List[str], timeout: float) -> RunnerResult:
        raise FileNotFoundError("model-allocator: command not found")

    runner = FakeRunner(side_effect=boom)
    adapter = _adapter(runner)
    with pytest.raises(AllocatorNotAvailable) as exc:
        adapter.preflight("r", "c")
    assert exc.value.category == "ALLOCATOR_NOT_AVAILABLE"


def test_subprocess_timeout_raises_allocator_not_available() -> None:
    def timeout(argv: List[str], timeout: float) -> RunnerResult:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    runner = FakeRunner(side_effect=timeout)
    adapter = _adapter(runner)
    with pytest.raises(AllocatorNotAvailable) as exc:
        adapter.preflight("r", "c")
    assert exc.value.category == "ALLOCATOR_NOT_AVAILABLE"


def test_malformed_runner_results_rejected() -> None:
    runner = FakeRunner(response=("zero", "ok", ""))  # type: ignore[arg-type]
    adapter = _adapter(runner)
    with pytest.raises(AllocatorNotAvailable):
        adapter.preflight("r", "c")


def test_run_empty_output_raises_allocator_not_available() -> None:
    runner = FakeRunner(response=(0, "", ""))
    adapter = _adapter(runner)
    with pytest.raises(AllocatorNotAvailable):
        adapter.run("r", "opencode")


# ---------------------------------------------------------------------------
# §14 / §35: release policy table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy",
    ["cloud_noop", "persistent", "shared_runtime"],
)
def test_release_never_invokes_runner_for_persistent_policies(policy: str) -> None:
    runner = FakeRunner(response=(0, "", ""))
    adapter = _adapter(runner)
    result = adapter.release("alias-a", policy, other_lease_holders=0)
    assert result is None
    assert runner.calls == []


def test_release_shared_runtime_never_invokes_runner_even_with_zero_other_holders() -> None:
    runner = FakeRunner(response=(0, "", ""))
    adapter = _adapter(runner)
    assert adapter.release("alias-a", "shared_runtime", other_lease_holders=0) is None
    assert runner.calls == []


def test_release_stop_after_step_waits_until_no_other_lease_holders() -> None:
    runner = FakeRunner(response=(0, "", ""))
    adapter = _adapter(runner)
    assert adapter.release("alias-a", "stop_after_step", other_lease_holders=2) is None
    assert runner.calls == []


def test_release_stop_after_step_stops_when_alone() -> None:
    runner = FakeRunner(response=(0, "", ""))
    adapter = _adapter(runner)
    result = adapter.release("alias-a", "stop_after_step", other_lease_holders=0)
    assert result == ""
    assert runner.calls


def test_release_unknown_policy_rejected() -> None:
    runner = FakeRunner(response=(0, "", ""))
    adapter = _adapter(runner)
    with pytest.raises(ValueError):
        adapter.release("alias-a", "garbage", other_lease_holders=0)


def test_release_negative_other_lease_holders_rejected() -> None:
    runner = FakeRunner(response=(0, "", ""))
    adapter = _adapter(runner)
    with pytest.raises(ValueError):
        adapter.release("alias-a", "stop_after_step", other_lease_holders=-1)


# ---------------------------------------------------------------------------
# §31: output parsing
# ---------------------------------------------------------------------------


def test_stripping_is_not_applied_to_run_output() -> None:
    payload = "  opencode --model imple01-3060  \n"
    runner = FakeRunner(response=(0, payload, ""))
    adapter = _adapter(runner)
    assert adapter.run("imple01", "opencode") == payload


# ---------------------------------------------------------------------------
# §24: category strings are the §24 contract
# ---------------------------------------------------------------------------


def test_categories_match_section_24() -> None:
    assert AliasValidationFailed.category == "ALIAS_VALIDATION_FAILED"
    assert AllocatorNotAvailable.category == "ALLOCATOR_NOT_AVAILABLE"
    assert AllocatorPreflightFailed.category == "ALLOCATOR_PREFLIGHT_FAILED"
    assert ClientConfigRenderFailed.category == "CLIENT_CONFIG_RENDER_FAILED"
    assert RuntimeReleaseFailed.category == "RUNTIME_RELEASE_FAILED"


def test_allocator_errors_inherit_envelope_error_base() -> None:
    assert issubclass(AllocatorError, EnvelopeError)
    err = AllocatorNotAvailable("boom", returncode=1, stdout="x", stderr="y")
    assert isinstance(err, EnvelopeError)
    assert err.returncode == 1
    assert err.stdout == "x"
    assert err.stderr == "y"


# ---------------------------------------------------------------------------
# §31: ensure no forbidden tokens in source
# ---------------------------------------------------------------------------


def test_adapter_module_has_no_forbidden_subprocess_flags() -> None:
    source = (Path(__file__).resolve().parents[2] / "src" / "dpmtf_lightworker" / "allocator.py").read_text(encoding="utf-8")
    for forbidden in ("shell=True", "ollama", "llama-server", "nvidia-smi"):
        assert forbidden not in source, f"forbidden token {forbidden!r} present in allocator.py"
