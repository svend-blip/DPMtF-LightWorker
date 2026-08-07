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

    def run(self, role: str, client: str, config_path: str = "") -> str:
        """Return the allocator-produced client command without modification.

        ``config_path`` names the config THIS worker rendered, so the command
        points OpenCode at it. Without it the allocator names its own shared
        role config and refreshes it, and the execution-specific config the
        worker built -- with the permission block confining the role to its
        worktree, and this machine's provider endpoint -- is never read.

        lightworker run 001 found that: three executions rendered a config
        nobody opened. The role had no §19 confinement at all, and OpenCode
        failed on a provider endpoint it did not have.

        The command still comes back verbatim (§34). The worker asks for a
        different command; it does not edit the one it gets.
        """
        argv = self._build_command(
            "run", role=role, client=client, config_path=config_path)
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
                *(["--config", values["config_path"]]
                  if operation == "run" and values.get("config_path") else []),
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
            # preflight and run may have to COLD-START a model server --
            # 21.7 GB of 35B weights took minutes on svend3060, and EXEC-016
            # died at 30.0s for exactly this: the steward had pressed Stop
            # servers, and the first execution after it found a cold card.
            # Same defect family as the llama_SG note "start-timeout was too
            # short". Everything else keeps the tight timeout: a slow
            # resolve or render IS a defect worth surfacing fast.
            op_timeout = (600.0 if argv[1:2] and argv[1] in ("preflight", "run")
                          else self._timeout)
            returncode, stdout, stderr = self._runner(argv, op_timeout)
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
