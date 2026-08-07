"""Tests for the git workspace isolation layer (GOAL.md §§16, 17, 36)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from dpmtf_lightworker.errors import (
    BaseCommitNotFound,
    EnvelopeError,
    GitError,
    PatchGenerationFailed,
    RepositoryFetchFailed,
    WorktreeCreationFailed,
)
from dpmtf_lightworker.git_workspace import GitWorkspace, RunnerResult


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate git's global config from the host user's ``~/.gitconfig``."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home


@pytest.fixture
def preexisting_gitconfig(isolated_home: Path) -> Path:
    """Drop a pre-existing global config so tests can prove ``commit_result`` does not mutate it."""
    config = isolated_home / ".gitconfig"
    config.write_text(
        "[user]\n"
        "  name = preexisting\n"
        "  email = pre@example.com\n",
        encoding="utf-8",
    )
    return config


def _run(
    argv: List[str], *, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        check=check,
        text=True,
    )


def _make_remote(tmp_path: Path, name: str, content: str = "hello\n") -> Tuple[str, str]:
    """Build a bare "remote" with one initial commit. Returns (url, base_sha)."""
    remote_dir = tmp_path / f"{name}.git"
    _run(["git", "init", "--bare", "--initial-branch=main", str(remote_dir)])

    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _run(["git", "init", "--initial-branch=main"], cwd=seed)
    _run(["git", "config", "user.email", "seed@example.com"], cwd=seed)
    _run(["git", "config", "user.name", "Seed"], cwd=seed)
    (seed / "README.md").write_text(content, encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=seed)
    _run(["git", "commit", "-m", "Initial"], cwd=seed)
    _run(["git", "remote", "add", "origin", str(remote_dir)], cwd=seed)
    _run(["git", "push", "origin", "main"], cwd=seed)

    head = _run(["git", "rev-parse", "HEAD"], cwd=seed)
    return str(remote_dir), head.stdout.strip()


def _workspace(tmp_path: Path, **kwargs: Any) -> GitWorkspace:
    return GitWorkspace(
        repository_root=str(tmp_path / "repos"),
        worktree_root=str(tmp_path / "work"),
        **kwargs,
    )


def _setup(tmp_path: Path) -> Tuple[GitWorkspace, str, Path, str, str]:
    """Provision a workspace + a remote + an initial worktree; return all the pieces tests need."""
    url, base = _make_remote(tmp_path, "trade-ui")
    w = _workspace(tmp_path)
    w.ensure_mirror("trade-ui", url)
    wt = w.create_worktree("EXEC-1", "trade-ui", base)
    mirror = tmp_path / "repos" / "trade-ui.git"
    return w, url, wt, base, str(mirror)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_empty_repository_root_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        GitWorkspace(repository_root="", worktree_root=str(tmp_path / "w"))


def test_empty_worktree_root_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        GitWorkspace(repository_root=str(tmp_path / "r"), worktree_root="")


def test_constructor_creates_roots(tmp_path: Path) -> None:
    GitWorkspace(
        repository_root=str(tmp_path / "r"),
        worktree_root=str(tmp_path / "w"),
    )
    assert (tmp_path / "r").is_dir()
    assert (tmp_path / "w").is_dir()


# ---------------------------------------------------------------------------
# §16.2 — mirror layout
# ---------------------------------------------------------------------------


def test_mirror_path_uses_bare_git_suffix(tmp_path: Path) -> None:
    w = _workspace(tmp_path)
    assert w.mirror_path("trade-ui") == tmp_path / "repos" / "trade-ui.git"


@pytest.mark.parametrize(
    "bad_key",
    ["", ".", "..", "../etc", "foo/bar", "foo\\bar", ".hidden", "with..double"],
)
def test_mirror_path_rejects_unsafe_project_keys(
    tmp_path: Path, bad_key: str
) -> None:
    w = _workspace(tmp_path)
    with pytest.raises(ValueError):
        w.mirror_path(bad_key)


# ---------------------------------------------------------------------------
# §16.2 — ensure_mirror
# ---------------------------------------------------------------------------


def test_ensure_mirror_creates_bare_clone(tmp_path: Path) -> None:
    url, _ = _make_remote(tmp_path, "trade-ui")
    w = _workspace(tmp_path)
    mirror = w.ensure_mirror("trade-ui", url)
    assert mirror.is_dir()
    bare = _run(["git", "config", "core.bare"], cwd=mirror)
    assert bare.stdout.strip() == "true"


def test_ensure_mirror_fetches_when_present(tmp_path: Path) -> None:
    url, _ = _make_remote(tmp_path, "trade-ui")
    w = _workspace(tmp_path)
    mirror = w.ensure_mirror("trade-ui", url)
    # Add a new commit to the remote.
    extra_seed = tmp_path / "extra"
    extra_seed.mkdir()
    _run(["git", "clone", url, str(extra_seed)])
    _run(["git", "config", "user.email", "x@example.com"], cwd=extra_seed)
    _run(["git", "config", "user.name", "X"], cwd=extra_seed)
    (extra_seed / "new.txt").write_text("new\n", encoding="utf-8")
    _run(["git", "add", "new.txt"], cwd=extra_seed)
    _run(["git", "commit", "-m", "Add new"], cwd=extra_seed)
    _run(["git", "push", "origin", "main"], cwd=extra_seed)
    new_head = _run(["git", "rev-parse", "HEAD"], cwd=extra_seed).stdout.strip()
    # Re-ensure the mirror — must fetch.
    w.ensure_mirror("trade-ui", url)
    # The new commit's objects must be present in the mirror after the fetch.
    has_object = _run(
        ["git", "cat-file", "-e", new_head],
        cwd=Path(mirror),
        check=False,
    )
    assert has_object.returncode == 0


def test_ensure_mirror_raises_on_bad_url(tmp_path: Path) -> None:
    w = _workspace(tmp_path)
    with pytest.raises(RepositoryFetchFailed) as exc:
        w.ensure_mirror("trade-ui", "https://nonexistent.invalid/repo.git")
    assert exc.value.category == "REPOSITORY_FETCH_FAILED"
    assert "exit code" in str(exc.value)


def test_ensure_mirror_rejects_empty_url(tmp_path: Path) -> None:
    w = _workspace(tmp_path)
    with pytest.raises(ValueError):
        w.ensure_mirror("trade-ui", "")


# ---------------------------------------------------------------------------
# §16.1 / §16.3 — create_worktree uses the exact base commit
# ---------------------------------------------------------------------------


def test_create_worktree_uses_exact_base_commit(tmp_path: Path) -> None:
    w, _, wt, base, _ = _setup(tmp_path)
    head = _run(["git", "rev-parse", "HEAD"], cwd=wt)
    assert head.stdout.strip() == base


def test_create_worktree_is_detached_at_base(tmp_path: Path) -> None:
    w, _, wt, base, _ = _setup(tmp_path)
    head = _run(["git", "symbolic-ref", "--quiet", "HEAD"], cwd=wt, check=False)
    assert head.returncode != 0  # detached


@pytest.mark.parametrize(
    "bad_commit",
    ["main", "HEAD", "origin/main", "deadbeef", "0" * 39, "g" * 40, ""],
)
def test_create_worktree_rejects_non_full_sha(
    tmp_path: Path, bad_commit: str
) -> None:
    url, base = _make_remote(tmp_path, "trade-ui")
    w = _workspace(tmp_path)
    w.ensure_mirror("trade-ui", url)
    with pytest.raises(ValueError):
        w.create_worktree("EXEC-1", "trade-ui", bad_commit)


def test_create_worktree_raises_base_commit_not_found_for_unknown_sha(
    tmp_path: Path,
) -> None:
    url, _ = _make_remote(tmp_path, "trade-ui")
    w = _workspace(tmp_path)
    w.ensure_mirror("trade-ui", url)
    fake_sha = "0" * 40
    with pytest.raises(BaseCommitNotFound) as exc:
        w.create_worktree("EXEC-1", "trade-ui", fake_sha)
    assert exc.value.category == "BASE_COMMIT_NOT_FOUND"


def test_create_worktree_raises_when_mirror_missing(tmp_path: Path) -> None:
    url, base = _make_remote(tmp_path, "trade-ui")
    w = _workspace(tmp_path)
    with pytest.raises(RepositoryFetchFailed):
        w.create_worktree("EXEC-1", "trade-ui", base)


def test_create_worktree_unique_per_execution(tmp_path: Path) -> None:
    url, base = _make_remote(tmp_path, "trade-ui")
    w = _workspace(tmp_path)
    w.ensure_mirror("trade-ui", url)
    wt1 = w.create_worktree("EXEC-1", "trade-ui", base)
    wt2 = w.create_worktree("EXEC-2", "trade-ui", base)
    assert wt1 != wt2
    assert wt1.is_dir()
    assert wt2.is_dir()


def test_create_worktree_rejects_collision(tmp_path: Path) -> None:
    w, _, wt, base, _ = _setup(tmp_path)
    with pytest.raises(WorktreeCreationFailed):
        w.create_worktree("EXEC-1", "trade-ui", base)


# ---------------------------------------------------------------------------
# §16.4 — result_branch_name
# ---------------------------------------------------------------------------


def test_result_branch_name_format(tmp_path: Path) -> None:
    w = _workspace(tmp_path)
    assert (
        w.result_branch_name("EXEC-123-IMPLE01", "ATTEMPT-1")
        == "dpmtf-local/EXEC-123-IMPLE01/ATTEMPT-1"
    )


# ---------------------------------------------------------------------------
# §16.5 / §17.1 — commit_result and patch generation
# ---------------------------------------------------------------------------


def test_commit_result_returns_sha_and_descends_from_base(tmp_path: Path) -> None:
    w, _, wt, base, _ = _setup(tmp_path)
    (wt / "new.txt").write_text("new\n", encoding="utf-8")
    branch = w.result_branch_name("EXEC-1", "ATTEMPT-1")
    _run(["git", "checkout", "-b", branch], cwd=wt)
    sha = w.commit_result(str(wt), "DPMtF test commit")
    assert sha is not None
    assert len(sha) == 40
    parent = _run(["git", "rev-parse", "HEAD^"], cwd=wt).stdout.strip()
    assert parent == base


def test_commit_result_returns_none_for_clean_worktree(tmp_path: Path) -> None:
    w, _, wt, _, _ = _setup(tmp_path)
    assert w.commit_result(str(wt), "no-op") is None


def test_commit_result_does_not_mutate_global_git_config(
    tmp_path: Path,
    preexisting_gitconfig: Path,
) -> None:
    before = preexisting_gitconfig.read_text(encoding="utf-8")
    w, _, wt, _, _ = _setup(tmp_path)
    (wt / "x.txt").write_text("x\n", encoding="utf-8")
    branch = w.result_branch_name("EXEC-1", "ATTEMPT-1")
    _run(["git", "checkout", "-b", branch], cwd=wt)
    w.commit_result(str(wt), "test")
    after = preexisting_gitconfig.read_text(encoding="utf-8")
    assert before == after


def test_commit_result_rejects_empty_message(tmp_path: Path) -> None:
    w, _, wt, _, _ = _setup(tmp_path)
    with pytest.raises(ValueError):
        w.commit_result(str(wt), "")


def test_generate_patch_is_binary_safe(tmp_path: Path) -> None:
    w, _, wt, base, _ = _setup(tmp_path)
    (wt / "image.bin").write_bytes(b"\x00\x01\x02\x03\xff\xfe")
    (wt / "README.md").write_text("updated\n", encoding="utf-8")
    branch = w.result_branch_name("EXEC-1", "ATTEMPT-1")
    _run(["git", "checkout", "-b", branch], cwd=wt)
    sha = w.commit_result(str(wt), "binary + text")
    assert sha is not None
    patch = w.generate_patch(str(wt), base)
    assert "GIT binary patch" in patch or "Binary files" in patch


def test_generate_patch_passes_git_apply_check(tmp_path: Path) -> None:
    w, _, wt, base, mirror = _setup(tmp_path)
    (wt / "image.bin").write_bytes(b"\x00\x01\x02\x03\xff\xfe")
    (wt / "README.md").write_text("updated\n", encoding="utf-8")
    branch = w.result_branch_name("EXEC-1", "ATTEMPT-1")
    _run(["git", "checkout", "-b", branch], cwd=wt)
    w.commit_result(str(wt), "apply check")
    patch = w.generate_patch(str(wt), base)
    fresh = tmp_path / "work" / "EXEC-1-apply"
    _run(
        ["git", "worktree", "add", "--detach", str(fresh), base],
        cwd=Path(mirror),
    )
    result = _run(
        ["git", "apply", "--check", "-"],
        cwd=fresh,
        check=False,
    )
    # Inject the patch via stdin.
    proc = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=str(fresh),
        input=patch,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_generate_patch_rejects_non_sha_base(tmp_path: Path) -> None:
    w, _, wt, _, _ = _setup(tmp_path)
    with pytest.raises(ValueError):
        w.generate_patch(str(wt), "main")


# ---------------------------------------------------------------------------
# §36 — cleanup is idempotent and never touches a mirror
# ---------------------------------------------------------------------------


def test_cleanup_removes_worktree_and_keeps_mirror(tmp_path: Path) -> None:
    w, _, wt, _, mirror = _setup(tmp_path)
    w.cleanup("EXEC-1")
    assert not wt.exists()
    assert Path(mirror).is_dir()


def test_cleanup_prunes_mirror_view(tmp_path: Path) -> None:
    w, _, wt, _, mirror = _setup(tmp_path)
    w.cleanup("EXEC-1")
    listing = _run(
        ["git", "worktree", "list", "--porcelain"], cwd=Path(mirror)
    ).stdout
    assert str(wt) not in listing


def test_cleanup_is_idempotent(tmp_path: Path) -> None:
    w, _, _, _, _ = _setup(tmp_path)
    w.cleanup("EXEC-1")
    w.cleanup("EXEC-1")
    assert not (tmp_path / "work" / "EXEC-1").exists()


def test_cleanup_is_noop_when_worktree_never_existed(tmp_path: Path) -> None:
    w = _workspace(tmp_path)
    w.cleanup("EXEC-DOES-NOT-EXIST")  # must not raise


def test_cleanup_falls_back_to_rmtree_when_worktree_has_uncommitted_changes(
    tmp_path: Path,
) -> None:
    w, _, wt, _, _ = _setup(tmp_path)
    (wt / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    w.cleanup("EXEC-1")
    assert not wt.exists()


# ---------------------------------------------------------------------------
# §16.4 / §16.6 — prohibitions
# ---------------------------------------------------------------------------


def test_module_has_no_push_merge_or_force_tokens() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "dpmtf_lightworker"
        / "git_workspace.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "shell=True",
        '"push"',
        "'push'",
        "--force",
        "reset --hard",
        "checkout main",
        "pull",
    ):
        assert forbidden not in source, (
            f"forbidden token {forbidden!r} present in git_workspace.py"
        )


# ---------------------------------------------------------------------------
# §24 — category strings are the §24 contract
# ---------------------------------------------------------------------------


def test_categories_match_section_24() -> None:
    assert RepositoryFetchFailed.category == "REPOSITORY_FETCH_FAILED"
    assert BaseCommitNotFound.category == "BASE_COMMIT_NOT_FOUND"
    assert WorktreeCreationFailed.category == "WORKTREE_CREATION_FAILED"
    assert PatchGenerationFailed.category == "PATCH_GENERATION_FAILED"


def test_git_errors_inherit_envelope_error_base() -> None:
    assert issubclass(GitError, EnvelopeError)
    err = RepositoryFetchFailed(
        "boom", returncode=1, stdout="x", stderr="y"
    )
    assert isinstance(err, EnvelopeError)
    assert err.returncode == 1
    assert err.stdout == "x"
    assert err.stderr == "y"


# ---------------------------------------------------------------------------
# Runner injection
# ---------------------------------------------------------------------------


def test_default_runner_is_real_git(tmp_path: Path) -> None:
    repo_root = tmp_path / "r"
    work_root = tmp_path / "w"
    home_probe = tmp_path / "nowhere-home-probe"
    w = GitWorkspace(repository_root=str(repo_root), worktree_root=str(work_root))
    assert w._runner is not None
    # Replace HOME so git cannot read global config during this probe.
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home_probe)
    try:
        rc, stdout, stderr = w._runner(["git", "--version"], None)
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
    assert rc == 0
    assert "git version" in stdout


def test_fake_runner_is_invoked_with_list_and_cwd(tmp_path: Path) -> None:
    calls: List[Tuple[List[str], Any]] = []

    def fake(argv: List[str], cwd: Any) -> RunnerResult:
        calls.append((list(argv), cwd))
        return (0, "", "")

    w = GitWorkspace(
        repository_root=str(tmp_path / "r"),
        worktree_root=str(tmp_path / "w"),
        runner=fake,
    )
    w.ensure_mirror("trade-ui", "/nonexistent")
    assert calls
    argv, cwd = calls[0]
    assert isinstance(argv, list)
    assert "clone" in argv
    assert cwd is None


def test_runner_malformed_results_rejected(tmp_path: Path) -> None:
    def malformed(argv: List[str], cwd: Any) -> Any:
        return ("zero", "", "")

    w = _workspace(tmp_path, runner=malformed)  # type: ignore[arg-type]
    with pytest.raises(RepositoryFetchFailed):
        w.ensure_mirror("trade-ui", "https://nonexistent.invalid/repo.git")


def test_runner_subprocess_start_failure_raises_repository_fetch_failed(
    tmp_path: Path,
) -> None:
    def boom(argv: List[str], cwd: Any) -> RunnerResult:
        raise FileNotFoundError("git not found")

    w = _workspace(tmp_path, runner=boom)
    with pytest.raises(RepositoryFetchFailed) as exc:
        w.ensure_mirror("trade-ui", "https://nonexistent.invalid/repo.git")
    assert exc.value.category == "REPOSITORY_FETCH_FAILED"


class TestExcludeFromStatus:
    """Worker-written files must not read as the role's mess.

    The governance file and the deliverable are untracked, so a handoff
    saying "the tree must be clean when you finish" blamed the role for
    files it never chose to create. Git's per-worktree exclude hides them
    from status without touching the repository or the patch.
    """

    def _real_repo(self, tmp_path):
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        for argv in (["git", "init", "-q"],
                     ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                      "commit", "-q", "--allow-empty", "-m", "seed"]):
            subprocess.run(argv, cwd=repo, check=True, capture_output=True)
        return repo

    def test_excluded_paths_vanish_from_status(self, tmp_path):
        import subprocess
        from dpmtf_lightworker.git_workspace import GitWorkspace
        repo = self._real_repo(tmp_path)
        (repo / ".lightworker").mkdir()
        (repo / ".lightworker" / "gov.md").write_text("g", encoding="utf-8")
        (repo / "results").mkdir()
        (repo / "results" / "r.md").write_text("r", encoding="utf-8")
        ws = GitWorkspace(repository_root=str(tmp_path),
                          worktree_root=str(tmp_path))
        ws.exclude_from_status(str(repo), [".lightworker/", "results/r.md"])
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo,
            capture_output=True, text=True, check=True).stdout
        assert ".lightworker" not in status
        assert "results" not in status

    def test_it_works_before_the_files_exist(self, tmp_path):
        """The worker excludes at worktree creation, before the role has
        written anything. Patterns must not require the file."""
        import subprocess
        from dpmtf_lightworker.git_workspace import GitWorkspace
        repo = self._real_repo(tmp_path)
        ws = GitWorkspace(repository_root=str(tmp_path),
                          worktree_root=str(tmp_path))
        ws.exclude_from_status(str(repo), ["late/file.md"])
        (repo / "late").mkdir()
        (repo / "late" / "file.md").write_text("x", encoding="utf-8")
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo,
            capture_output=True, text=True, check=True).stdout
        assert "late" not in status

    def test_a_broken_runner_never_raises(self, tmp_path):
        """Cosmetic, never fatal: a role without the exclusions is mildly
        noisier, which is not worth failing an execution over."""
        from dpmtf_lightworker.git_workspace import GitWorkspace
        ws = GitWorkspace(repository_root=str(tmp_path),
                          worktree_root=str(tmp_path),
                          runner=lambda argv, cwd: (_ for _ in ()).throw(
                              RuntimeError("boom")))
        ws.exclude_from_status(str(tmp_path), [".lightworker/"])  # må ikke rejse
