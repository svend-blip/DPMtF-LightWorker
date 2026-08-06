"""Model Allocator command adapter (GOAL.md §§14, 15, 31, 32, 35)."""

from __future__ import annotations

import subprocess
from typing import Callable, Optional

from dpmtf_lightworker.errors import (
    AliasValidationFailed,
    AllocatorError,
    AllocatorNotAvailable,
    AllocatorPreflightFailed,
    ClientConfigRenderFailed,
    RuntimeReleaseFailed,
)

RunnerResult = tuple[int, str, str]
Runner = Callable[[list[str], float], RunnerResult]


class AllocatorAdapter:
    """Invoke the allocator through a bounded, injectable command seam."""

    def __init__(
        self,
        command: str = "model-allocator",
        runner: Optional[Runner] = None,
        timeout: float = 30.0,
    ) -> None:
        if not command:
            raise ValueError("AllocatorAdapter.command must not be empty")
        if timeout <= 0:
            raise ValueError("AllocatorAdapter.timeout must be positive")
        self._command = command
        self._runner = _default_runner if runner is None else runner
        self._timeout = timeout

    def preflight(self, role: str, client: str) -> str:
        """Verify the role, client, and runtime combination."""
        argv = self._build_command("preflight", role=role, client=client)
        return self._call(argv, AllocatorPreflightFailed)

    def validate(self, alias: str, client: str) -> str:
        """Validate an allocator alias before any handoff is injected."""
        argv = self._build_command("validate", alias=alias, client=client)
        return self._call(argv, AliasValidationFailed)

    def render_config(self, role: str, client: str, output: str) -> str:
        """Render client configuration to the execution-specific output path."""
        argv = self._build_command(
            "render-config", role=role, client=client, output=output
        )
        return self._call(argv, ClientConfigRenderFailed)

    def run(self, role: str, client: str) -> str:
        """Return the allocator-produced client command without modification."""
        argv = self._build_command("run", role=role, client=client)
        output = self._call(argv, AllocatorNotAvailable)
        if not output:
            raise AllocatorNotAvailable(
                "allocator run returned no client command",
                stdout=output,
            )
        return output

    def release(
        self,
        alias: str,
        policy: str,
        other_lease_holders: int = 0,
    ) -> Optional[str]:
        """Release according to a caller-supplied lifecycle lease assertion.

        ``other_lease_holders`` is not discovered by this adapter. Its default
        of zero is an assertion made by the caller, which is responsible for
        supplying the true count under the Father-owned lease design.
        """
        if other_lease_holders < 0:
            raise ValueError("other_lease_holders must not be negative")
        if policy in {"cloud_noop", "persistent", "shared_runtime"}:
            return None
        if policy == "stop_after_step":
            if other_lease_holders != 0:
                return None
            argv = self._build_command("stop", alias=alias)
            return self._call(argv, RuntimeReleaseFailed)
        raise ValueError(f"unsupported lifecycle policy: {policy}")

    def _build_command(self, operation: str, **values: str) -> list[str]:
        if operation in {"preflight", "render-config", "run"}:
            return [
                self._command,
                operation,
                "--role",
                values["role"],
                "--client",
                values["client"],
                *(["--output", values["output"]] if operation == "render-config" else []),
            ]
        if operation == "validate":
            return [
                self._command,
                operation,
                "--alias",
                values["alias"],
                "--client",
                values["client"],
            ]
        if operation == "stop":
            return [self._command, operation, "--alias", values["alias"]]
        raise ValueError(f"unsupported allocator operation: {operation}")

    def _call(self, argv: list[str], failure_type: type[AllocatorError]) -> str:
        try:
            returncode, stdout, stderr = self._runner(argv, self._timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AllocatorNotAvailable(
                f"allocator command failed to start: {exc}"
            ) from exc
        if not isinstance(returncode, int) or not isinstance(stdout, str) or not isinstance(stderr, str):
            raise AllocatorNotAvailable(
                "allocator runner returned malformed results"
            )
        if returncode != 0:
            detail = stderr.strip() or stdout.strip() or "no allocator diagnostics"
            raise failure_type(
                f"allocator command {argv[1]!r} failed with exit code {returncode}: {detail}",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return stdout


def _default_runner(argv: list[str], timeout: float) -> RunnerResult:
    """Run an allocator argv list with captured output and a finite timeout."""
    completed = subprocess.run(
        argv,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


__all__ = ["AllocatorAdapter", "Runner", "RunnerResult"]
