"""Tests for the worker-local tmux session transport (GOAL.md §§18, 34)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from dpmtf_lightworker.errors import (
    ClientStartFailed,
    HandoffInjectionFailed,
    TmuxStartFailed,
)
from dpmtf_lightworker.tmux_session import RunnerResult, TmuxSession


class FakeRunner:
    """A test runner that records argv and returns queued responses."""

    def __init__(self, responses: List[RunnerResult] | None = None) -> None:
        self.calls: List[List[str]] = []
        self._responses: List[RunnerResult] = list(responses or [])
        self._default: RunnerResult = (0, "", "")

    def __call__(self, argv: List[str]) -> RunnerResult:
        self.calls.append(list(argv))
        if self._responses:
            return self._responses.pop(0)
        return self._default


def _session(runner: FakeRunner) -> TmuxSession:
    return TmuxSession(runner=runner)


# ---------------------------------------------------------------------------
# session_name
# ---------------------------------------------------------------------------


def test_session_name_format_uses_role_and_short_execution_id() -> None:
    s = _session(FakeRunner())
    # Last 12 characters of the execution id are the short tag, so a
    # 15-character id keeps its last 12 and a 6-character id stays whole.
    assert s.session_name("imple01", "EXEC-123-IMPLE01") == "dpmtf-imple01-123-IMPLE01"
    assert s.session_name("review", "EXEC-X") == "dpmtf-review-EXEC-X"


def test_session_name_rejects_empty_role() -> None:
    s = _session(FakeRunner())
    with pytest.raises(ValueError):
        s.session_name("", "EXEC-1")


def test_session_name_rejects_empty_execution_id() -> None:
    s = _session(FakeRunner())
    with pytest.raises(ValueError):
        s.session_name("imple01", "")


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


def test_exists_returns_true_when_has_session_succeeds() -> None:
    runner = FakeRunner(responses=[(0, "", "")])
    assert _session(runner).exists("dpmtf-imple01-1") is True
    assert runner.calls == [["tmux", "has-session", "-t", "dpmtf-imple01-1"]]


def test_exists_returns_false_when_has_session_fails() -> None:
    runner = FakeRunner(responses=[(1, "", "can't find session")])
    assert _session(runner).exists("dpmtf-imple01-1") is False


def test_exists_rejects_empty_name() -> None:
    s = _session(FakeRunner())
    with pytest.raises(ValueError):
        s.exists("")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_uses_detached_session_with_cwd() -> None:
    runner = FakeRunner(responses=[(1, "", "")])  # has-session fails
    _session(runner).create("dpmtf-imple01-1", cwd="/srv/work/EXEC-1")
    assert runner.calls == [
        ["tmux", "has-session", "-t", "dpmtf-imple01-1"],
        ["tmux", "new-session", "-d", "-s", "dpmtf-imple01-1", "-c", "/srv/work/EXEC-1"],
    ]


def test_create_raises_on_collision_with_name_in_message() -> None:
    runner = FakeRunner(responses=[(0, "", "")])  # has-session succeeds
    s = _session(runner)
    with pytest.raises(TmuxStartFailed) as exc:
        s.create("dpmtf-imple01-1", cwd="/srv/work/EXEC-1")
    assert "dpmtf-imple01-1" in str(exc.value)
    assert "exists" in str(exc.value)
    assert exc.value.category == "TMUX_START_FAILED"


def test_create_propagates_new_session_failure() -> None:
    runner = FakeRunner(responses=[
        (1, "", ""),  # has-session fails
        (1, "", "create failed"),
    ])
    with pytest.raises(TmuxStartFailed):
        _session(runner).create("dpmtf-imple01-1", cwd="/srv/work/EXEC-1")


def test_create_rejects_empty_name() -> None:
    s = _session(FakeRunner())
    with pytest.raises(ValueError):
        s.create("", cwd="/x")


def test_create_rejects_empty_cwd() -> None:
    s = _session(FakeRunner())
    with pytest.raises(ValueError):
        s.create("dpmtf-imple01-1", cwd="")


# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------


def test_launch_sends_command_verbatim_with_one_enter() -> None:
    runner = FakeRunner()
    cmd = "opencode --model imple01-3060 --session exec-1"
    _session(runner).launch("dpmtf-imple01-1", cmd)
    assert runner.calls == [
        ["tmux", "send-keys", "-t", "dpmtf-imple01-1", "-l", cmd, "Enter"]
    ]


def test_launch_does_not_inject_model_argument() -> None:
    runner = FakeRunner()
    # Even an allocator command that already names a model must go through
    # verbatim — LightWorker never adds or rewrites model flags.
    cmd = "opencode --model imple01-3060"
    _session(runner).launch("dpmtf-imple01-1", cmd)
    argv = runner.calls[0]
    # The command arg is the *exact* string the caller passed; no flag
    # munging, no shell quoting, no --model injection.
    assert cmd in argv
    assert "--model" in cmd  # the caller's string still contains it
    # The argv has the verbatim command as one element, not tokenised.
    assert argv.count(cmd) == 1


def test_launch_propagates_failure_as_client_start_failed() -> None:
    runner = FakeRunner(responses=[(1, "", "send failed")])
    with pytest.raises(ClientStartFailed) as exc:
        _session(runner).launch("dpmtf-imple01-1", "opencode")
    assert exc.value.category == "CLIENT_START_FAILED"


def test_launch_rejects_empty_command() -> None:
    s = _session(FakeRunner())
    with pytest.raises(ValueError):
        s.launch("dpmtf-imple01-1", "")


# ---------------------------------------------------------------------------
# wait_ready
# ---------------------------------------------------------------------------


def test_wait_ready_returns_true_when_marker_appears() -> None:
    runner = FakeRunner(responses=[
        (0, "starting up\n", ""),  # no marker yet
        (0, "ready marker here\n", ""),  # marker present
    ])
    assert _session(runner).wait_ready("dpmtf-imple01-1", "ready", timeout=2.0) is True
    assert len(runner.calls) == 2
    for argv in runner.calls:
        assert argv[:3] == ["tmux", "capture-pane", "-t"]


def test_wait_ready_returns_false_on_timeout_without_hanging() -> None:
    runner = FakeRunner(responses=[(0, "still booting\n", "")])
    started = time.monotonic()
    result = _session(runner).wait_ready("dpmtf-imple01-1", "ready", timeout=0.3)
    elapsed = time.monotonic() - started
    assert result is False
    # 0.3s timeout, with a 0.1s poll interval, must finish in well under 2s.
    assert elapsed < 2.0


def test_wait_ready_rejects_non_positive_timeout() -> None:
    s = _session(FakeRunner())
    with pytest.raises(ValueError):
        s.wait_ready("dpmtf-imple01-1", "ready", timeout=0)


# ---------------------------------------------------------------------------
# inject — exactly one Enter, no chunking
# ---------------------------------------------------------------------------


def test_inject_submits_text_as_one_literal_payload_with_one_enter() -> None:
    runner = FakeRunner()
    long_text = "line one\nline two\nline three\n" * 50
    _session(runner).inject("dpmtf-imple01-1", long_text)
    # Exactly one recorded call.
    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert argv == ["tmux", "send-keys", "-t", "dpmtf-imple01-1", "-l", long_text, "Enter"]
    # The full payload is the literal text — no per-line splitting, no
    # per-chunk submission.
    assert long_text in argv
    # The text was sent intact, not tokenised.
    assert argv.count(long_text) == 1
    # Exactly one trailing "Enter" across the entire submission.
    enter_count = sum(1 for arg in argv if arg == "Enter")
    assert enter_count == 1


def test_inject_submits_empty_text_with_just_enter() -> None:
    runner = FakeRunner()
    _session(runner).inject("dpmtf-imple01-1", "")
    argv = runner.calls[0]
    assert argv == ["tmux", "send-keys", "-t", "dpmtf-imple01-1", "-l", "", "Enter"]


def test_inject_propagates_failure_as_handoff_injection_failed() -> None:
    runner = FakeRunner(responses=[(1, "", "send failed")])
    with pytest.raises(HandoffInjectionFailed) as exc:
        _session(runner).inject("dpmtf-imple01-1", "hello")
    assert exc.value.category == "HANDOFF_INJECTION_FAILED"


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def test_capture_returns_pane_text() -> None:
    runner = FakeRunner(responses=[(0, "pane line 1\npane line 2\n", "")])
    out = _session(runner).capture("dpmtf-imple01-1", lines=50)
    assert out == "pane line 1\npane line 2\n"
    assert runner.calls[0] == ["tmux", "capture-pane", "-t", "dpmtf-imple01-1", "-p", "-S", "-50"]


def test_capture_returns_empty_string_on_failure() -> None:
    runner = FakeRunner(responses=[(1, "", "no session")])
    assert _session(runner).capture("dpmtf-imple01-1", lines=20) == ""


def test_capture_rejects_non_positive_lines() -> None:
    s = _session(FakeRunner())
    with pytest.raises(ValueError):
        s.capture("dpmtf-imple01-1", lines=0)


# ---------------------------------------------------------------------------
# kill — names only the assigned session
# ---------------------------------------------------------------------------


def test_kill_names_only_the_target_session() -> None:
    runner = FakeRunner()
    _session(runner).kill("dpmtf-imple01-1")
    assert runner.calls == [["tmux", "kill-session", "-t", "dpmtf-imple01-1"]]


def test_kill_does_not_use_kill_server_or_kill_window() -> None:
    runner = FakeRunner()
    _session(runner).kill("dpmtf-imple01-1")
    argv = runner.calls[0]
    assert "kill-server" not in argv
    assert "kill-window" not in argv


def test_kill_propagates_failure_as_tmux_start_failed() -> None:
    runner = FakeRunner(responses=[(1, "", "no session")])
    with pytest.raises(TmuxStartFailed):
        _session(runner).kill("dpmtf-imple01-1")


# ---------------------------------------------------------------------------
# Source-level guard — no broad-kill or shell=True
# ---------------------------------------------------------------------------


def test_module_has_no_shell_true_or_broad_kill_tokens() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "dpmtf_lightworker"
        / "tmux_session.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("shell=True", "kill-server", "kill-window"):
        assert forbidden not in source, (
            f"forbidden token {forbidden!r} present in tmux_session.py"
        )


# ---------------------------------------------------------------------------
# Default runner — real tmux
# ---------------------------------------------------------------------------


def test_default_runner_invokes_subprocess_with_capture_output(tmp_path: Path) -> None:
    import subprocess
    import sys

    from dpmtf_lightworker import tmux_session as mod

    s = mod.TmuxSession()  # default runner
    real_runner = s._runner
    # Ask the default runner for the python version; tmux may not be on
    # the test host, so route through python instead and assert the
    # capture_output contract.
    completed = subprocess.run(
        [sys.executable, "-c", "print('ok')"],
        capture_output=True,
        check=False,
        text=True,
    )
    rc, stdout, stderr = real_runner([sys.executable, "-c", "print('ok')"])
    assert rc == completed.returncode
    assert stdout == "ok\n"
    assert isinstance(stderr, str)
