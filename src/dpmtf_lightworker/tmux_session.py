"""Worker-local tmux session transport (GOAL.md §§18, 34)."""

from __future__ import annotations

import subprocess
import time
from typing import Callable, List, Optional, Tuple

from dpmtf_lightworker.errors import (
    ClientStartFailed,
    EnvelopeError,
    HandoffInjectionFailed,
    TmuxStartFailed,
)

RunnerResult = Tuple[int, str, str]
Runner = Callable[[List[str]], RunnerResult]


class TmuxSession:
    """Drive a worker-local tmux session through a single injected runner.

    The runner is the only thing the module talks to the outside world
    through. The default runner invokes real ``tmux`` via
    :func:`subprocess.run` with ``capture_output=True`` and ``text=True``;
    every test substitutes a fake so no production tmux server is touched
    (governance rule: never create, attach to, or kill a real session).
    """

    def __init__(self, runner: Optional[Runner] = None) -> None:
        self._runner: Runner = runner if runner is not None else _default_runner

    def session_name(self, role: str, execution_id: str) -> str:
        """Return the §18 worker-local tmux session name for an execution."""
        if not isinstance(role, str) or not role:
            raise ValueError("role must be a non-empty string")
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("execution_id must be a non-empty string")
        # §18: dpmtf-<target-role>-<short-execution-id>. The execution_id
        # the worker holds is typically already short, but cap defensively
        # so the resulting session name stays under tmux's 100-byte limit.
        # Strip a leading dash from the short slice so a multi-segment
        # id like "EXEC-123-IMPLE01" does not produce a doubled dash in
        # the session name.
        if len(execution_id) > 12:
            short = execution_id[-12:].lstrip("-")
        else:
            short = execution_id
        return f"dpmtf-{role}-{short}"

    def exists(self, name: str) -> bool:
        """Return ``True`` if a tmux session with ``name`` exists."""
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        rc, _, _ = self._runner(["tmux", "has-session", "-t", name])
        return rc == 0

    def create(self, name: str, cwd: str) -> None:
        """Create a detached session for ``name`` rooted at ``cwd``.

        §18 requires collision *detection* — checking first and attaching
        to an existing session would put two executions in one pane. The
        method raises :class:`TmuxStartFailed` (with ``name`` and the word
        ``"exists"`` in the message) when the name is already taken.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("cwd must be a non-empty string")
        if self.exists(name):
            raise TmuxStartFailed(
                f"tmux session {name!r} already exists; refusing to share a pane"
            )
        self._call(
            ["tmux", "new-session", "-d", "-s", name, "-c", cwd],
            failure_type=TmuxStartFailed,
        )

    def launch(self, name: str, command: str) -> None:
        """Submit the allocator-produced command to ``name`` and press Enter.

        §34: the allocator's output *is* the client command. The command
        is sent verbatim — no parsing, no wrapping, no quoting into a
        shell string, no appending of ``--model`` or anything else.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(command, str) or not command:
            raise ValueError("command must be a non-empty string")
        self._call(
            ["tmux", "send-keys", "-t", name, "-l", command, "Enter"],
            failure_type=ClientStartFailed,
        )

    def wait_ready(self, name: str, marker: str, timeout: float) -> bool:
        """Poll ``capture`` until ``marker`` is in the pane or ``timeout`` elapses.

        The poll is bounded by ``timeout``; a missing marker returns
        ``False`` rather than hanging the worker.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(marker, str) or not marker:
            raise ValueError("marker must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pane = self.capture(name, lines=200)
            if marker in pane:
                return True
            time.sleep(0.1)
        return False

    def inject(self, name: str, text: str) -> None:
        """Submit the compiled handoff to ``name`` exactly once.

        A long prompt is sent as a single literal payload followed by one
        trailing ``Enter``. There is no chunking and no per-line
        submission, so the handoff is delivered to the client as one
        submission even when the text spans many screen lines.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        self._call(
            ["tmux", "send-keys", "-t", name, "-l", text, "Enter"],
            failure_type=HandoffInjectionFailed,
        )

    def capture(self, name: str, lines: int) -> str:
        """Return the last ``lines`` of pane output for ``name``."""
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(lines, int) or lines <= 0:
            raise ValueError("lines must be a positive integer")
        try:
            rc, stdout, stderr = self._runner(
                ["tmux", "capture-pane", "-t", name, "-p", "-S", f"-{lines}"]
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HandoffInjectionFailed(
                f"capture-pane could not be invoked: {exc}"
            ) from exc
        if rc != 0:
            return ""
        return stdout

    def kill(self, name: str) -> None:
        """Terminate only the named session. Unrelated sessions are untouched.

        The argv names exactly one session, so any command that would
        reach beyond the named pane is never issued by this module.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        self._call(
            ["tmux", "kill-session", "-t", name],
            failure_type=TmuxStartFailed,
        )

    def _call(
        self,
        argv: List[str],
        *,
        failure_type: type[EnvelopeError],
    ) -> str:
        try:
            returncode, stdout, stderr = self._runner(list(argv))
        except (OSError, subprocess.SubprocessError) as exc:
            raise failure_type(
                f"tmux command {argv!r} failed to start: {exc}"
            ) from exc
        if (
            not isinstance(returncode, int)
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
        ):
            raise failure_type(
                f"tmux runner returned malformed results for {argv!r}"
            )
        if returncode != 0:
            detail = (stderr or stdout).strip() or "no tmux diagnostics"
            raise failure_type(
                f"tmux command {argv!r} failed with exit code {returncode}: {detail}"
            )
        return stdout


def _default_runner(argv: List[str]) -> RunnerResult:
    """Run a tmux argv list with captured output and no shell."""
    completed = subprocess.run(
        argv,
        capture_output=True,
        check=False,
        text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


__all__ = ["TmuxSession", "Runner", "RunnerResult"]
