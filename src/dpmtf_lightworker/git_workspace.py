"""Git workspace isolation (GOAL.md §§16, 17, 36)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional, Tuple

from dpmtf_lightworker.errors import (
    BaseCommitNotFound,
    GitError,
    PatchGenerationFailed,
    RepositoryFetchFailed,
    WorktreeCreationFailed,
)

RunnerResult = Tuple[int, str, str]
Runner = Callable[[list[str], Optional[str]], RunnerResult]


_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_PROJECT_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_ID_FORBIDDEN_RE = re.compile(r"[/\\\x00]")


class GitWorkspace:
    """Per-execution git isolation: one bare mirror per project, one worktree per execution.

    The mirror is a cache, not a workspace (§16.2). The worktree is created at
    the exact base commit Father provided (§16.1) and is the only place a role
    execution may write. Nothing in this class transmits a commit to a remote,
    merges a branch, force-updates a ref, opens a merge request, or modifies a
    protected branch (§16.4, §16.6).
    """

    def __init__(
        self,
        *,
        repository_root: str,
        worktree_root: str,
        runner: Optional[Runner] = None,
    ) -> None:
        if not isinstance(repository_root, str) or not repository_root:
            raise ValueError("GitWorkspace.repository_root must be a non-empty string")
        if not isinstance(worktree_root, str) or not worktree_root:
            raise ValueError("GitWorkspace.worktree_root must be a non-empty string")
        self._repository_root = Path(repository_root)
        self._worktree_root = Path(worktree_root)
        self._repository_root.mkdir(parents=True, exist_ok=True)
        self._worktree_root.mkdir(parents=True, exist_ok=True)
        self._runner: Runner = runner if runner is not None else _default_runner

    def mirror_path(self, project_key: str) -> Path:
        """Return the bare-mirror path for a project under ``repository_root``."""
        _validate_project_key(project_key)
        return self._repository_root / f"{project_key}.git"

    def ensure_mirror(self, project_key: str, clone_url: str) -> Path:
        """Create the bare mirror if absent; fetch it if present.

        The mirror is updated with ``git fetch --prune`` and never with any
        operation that would advance the mirror to a position not chosen by
        the caller. The mirror is a cache (§16.2), not a workspace; its role
        is to make ``base_commit`` lookups independent of the original remote.
        """
        _validate_project_key(project_key)
        if not isinstance(clone_url, str) or not clone_url:
            raise ValueError("clone_url must be a non-empty string")
        mirror = self.mirror_path(project_key)
        if mirror.is_dir():
            self._call(
                ["git", "fetch", "--prune", "origin"],
                cwd=str(mirror),
                failure_type=RepositoryFetchFailed,
            )
            return mirror
        mirror.parent.mkdir(parents=True, exist_ok=True)
        self._call(
            ["git", "clone", "--bare", clone_url, str(mirror)],
            cwd=None,
            failure_type=RepositoryFetchFailed,
        )
        return mirror

    def create_worktree(
        self,
        execution_id: str,
        project_key: str,
        base_commit: str,
    ) -> Path:
        """Create a worktree at exactly ``base_commit`` and return its path.

        The base commit must be a 40-character hex SHA. Symbolic references
        (``main``, ``HEAD``, abbreviated SHAs) are rejected at the boundary so
        that no resolution step can silently substitute a different commit
        (§16.1). The worktree is created with ``--detach`` so it is not on a
        branch until the caller explicitly creates the local result branch.
        """
        _validate_id(execution_id, "execution_id")
        _validate_project_key(project_key)
        _validate_full_sha(base_commit, "base_commit")
        mirror = self.mirror_path(project_key)
        if not mirror.is_dir():
            raise RepositoryFetchFailed(
                f"mirror {mirror} does not exist; call ensure_mirror first"
            )
        self._call(
            ["git", "cat-file", "-e", base_commit],
            cwd=str(mirror),
            failure_type=BaseCommitNotFound,
        )
        worktree_path = self._worktree_root / execution_id
        if worktree_path.exists():
            raise WorktreeCreationFailed(
                f"worktree path {worktree_path} already exists"
            )
        self._call(
            ["git", "worktree", "add", "--detach", str(worktree_path), base_commit],
            cwd=str(mirror),
            failure_type=WorktreeCreationFailed,
        )
        return worktree_path

    def result_branch_name(self, execution_id: str, attempt_id: str) -> str:
        """Return the §16.4 local result branch name."""
        _validate_id(execution_id, "execution_id")
        _validate_id(attempt_id, "attempt_id")
        return f"dpmtf-local/{execution_id}/{attempt_id}"

    def commit_result(
        self,
        worktree: str,
        message: str,
    ) -> Optional[str]:
        """Commit everything in the worktree; return the SHA, or ``None`` when there is nothing to commit.

        The committer identity is supplied per invocation through ``-c`` flags
        so the global ``~/.gitconfig`` is never written. The empty-tree case
        is what makes §17.2 (deliverable-only) work without a special flag:
        a clean worktree returns ``None`` and the caller treats the absence of
        a result commit as the deliverable mode.
        """
        if not isinstance(message, str) or not message:
            raise ValueError("message must be a non-empty string")
        worktree_path = _validate_worktree(worktree)
        self._call(
            ["git", "add", "-A"],
            cwd=str(worktree_path),
            failure_type=RepositoryFetchFailed,
        )
        stdout, _ = self._call(
            ["git", "status", "--porcelain"],
            cwd=str(worktree_path),
            failure_type=RepositoryFetchFailed,
        )
        if not stdout.strip():
            return None
        self._call(
            [
                "git",
                "-c", "user.name=dpmtf-lightworker",
                "-c", "user.email=worker@dpmtf.local",
                "commit",
                "-m", message,
            ],
            cwd=str(worktree_path),
            failure_type=RepositoryFetchFailed,
        )
        head, _ = self._call(
            ["git", "rev-parse", "HEAD"],
            cwd=str(worktree_path),
            failure_type=RepositoryFetchFailed,
        )
        return head.strip()

    def generate_patch(
        self,
        worktree: str,
        base_commit: str,
    ) -> str:
        """Return a binary-safe patch from ``base_commit`` to the worktree's HEAD.

        ``--binary`` ensures binary files are diffed as binary; ``--full-index``
        prints the full pre/post blob SHAs so a receiver can apply the patch
        to a worktree that does not share the original objects (§17.1).
        """
        worktree_path = _validate_worktree(worktree)
        _validate_full_sha(base_commit, "base_commit")
        stdout, _ = self._call(
            ["git", "diff", "--binary", "--full-index", base_commit, "HEAD"],
            cwd=str(worktree_path),
            failure_type=PatchGenerationFailed,
        )
        return stdout

    def cleanup(self, execution_id: str) -> None:
        """Remove the worktree for ``execution_id`` and keep the mirror consistent.

        ``cleanup`` is not given a project key. It locates the worktree at
        ``<worktree_root>/<execution_id>``, finds the mirror that registered
        it by scanning each mirror's ``git worktree list`` output, removes the
        worktree through that mirror, and prunes the mirror's worktree list
        so it does not retain a registration whose directory is gone (§36).
        Mirrors are never removed. Calling ``cleanup`` when nothing exists is
        a no-op (§36 idempotency).
        """
        _validate_id(execution_id, "execution_id")
        worktree_path = self._worktree_root / execution_id
        if not worktree_path.exists():
            return
        owning_mirror = self._find_owning_mirror(worktree_path)
        if owning_mirror is not None:
            try:
                self._call(
                    ["git", "worktree", "remove", str(worktree_path)],
                    cwd=str(owning_mirror),
                    failure_type=RepositoryFetchFailed,
                )
            except RepositoryFetchFailed:
                shutil.rmtree(worktree_path)
            self._call(
                ["git", "worktree", "prune"],
                cwd=str(owning_mirror),
                failure_type=RepositoryFetchFailed,
            )
            return
        shutil.rmtree(worktree_path)

    def _find_owning_mirror(self, worktree_path: Path) -> Optional[Path]:
        if not self._repository_root.is_dir():
            return None
        target = str(worktree_path)
        for mirror in sorted(self._repository_root.glob("*.git")):
            if not mirror.is_dir():
                continue
            try:
                stdout, _ = self._call(
                    ["git", "worktree", "list", "--porcelain"],
                    cwd=str(mirror),
                    failure_type=RepositoryFetchFailed,
                )
            except RepositoryFetchFailed:
                continue
            for line in stdout.splitlines():
                if line.startswith("worktree ") and line[len("worktree "):] == target:
                    return mirror
        return None

    def _call(
        self,
        argv: list[str],
        *,
        cwd: Optional[str],
        failure_type: type[GitError],
    ) -> tuple[str, str]:
        try:
            returncode, stdout, stderr = self._runner(list(argv), cwd)
        except (OSError, subprocess.SubprocessError) as exc:
            raise failure_type(
                f"git command {argv!r} failed to start: {exc}"
            ) from exc
        if (
            not isinstance(returncode, int)
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
        ):
            raise failure_type(
                f"git runner returned malformed results for {argv!r}"
            )
        if returncode != 0:
            detail = (stderr or stdout).strip() or "no git diagnostics"
            raise failure_type(
                f"git command {argv!r} failed with exit code {returncode}: {detail}",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return stdout, stderr


def _default_runner(argv: list[str], cwd: Optional[str]) -> RunnerResult:
    """Run a git argv list with captured output and no shell."""
    completed = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _validate_project_key(project_key: str) -> None:
    if (
        not isinstance(project_key, str)
        or not _PROJECT_KEY_RE.match(project_key)
        or ".." in project_key
    ):
        raise ValueError(f"project_key {project_key!r} is not a valid project key")


def _validate_id(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or _ID_FORBIDDEN_RE.search(value)
    ):
        raise ValueError(f"{name} {value!r} is not a valid identifier")


def _validate_full_sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _FULL_SHA_RE.match(value):
        raise ValueError(
            f"{name} {value!r} must be a 40-character hex SHA"
        )


def _validate_worktree(worktree: str) -> Path:
    if not isinstance(worktree, str) or not worktree:
        raise ValueError("worktree must be a non-empty string")
    path = Path(worktree)
    if not path.is_dir():
        raise ValueError(f"worktree {path} does not exist or is not a directory")
    return path


__all__ = ["GitWorkspace", "Runner", "RunnerResult"]
