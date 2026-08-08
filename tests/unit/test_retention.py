"""Tests for the retention sweeper module."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dpmtf_lightworker.config import PathsSection, RetentionSection
from dpmtf_lightworker.retention import SweepReport, sweep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> float:
    """A fixed epoch second for test determinism."""
    return 1_700_000_000.0


def _paths(tmp_path: Path) -> PathsSection:
    """Build a PathsSection pointing at sub-directories under tmp_path."""
    repo = tmp_path / "repos"
    wt = tmp_path / "worktrees"
    art = tmp_path / "artifacts"
    logs = tmp_path / "logs"
    for d in (repo, wt, art, logs):
        d.mkdir(exist_ok=True)
    return PathsSection(
        repository_root=str(repo),
        worktree_root=str(wt),
        artifact_root=str(art),
        log_root=str(logs),
        opencode_config_root=str(tmp_path / "opencode"),
    )


def _retention() -> RetentionSection:
    return RetentionSection(
        successful_worktree_hours=1,
        failed_worktree_days=7,
        artifact_days=14,
        log_days=30,
    )


# ---------------------------------------------------------------------------
# 1. Old worktree removed, young one kept
# ---------------------------------------------------------------------------


def test_old_worktree_removed_young_kept(tmp_path: Path) -> None:
    """A worktree whose mtime exceeds the failed window is removed;
    a recent one is left alone."""
    now = _now()
    paths = _paths(tmp_path)
    retention = _retention()

    old_dir = Path(paths.worktree_root) / "old-tree"
    old_dir.mkdir()
    os.utime(str(old_dir), (now - 8 * 86400, now - 8 * 86400))

    new_dir = Path(paths.worktree_root) / "new-tree"
    new_dir.mkdir()
    os.utime(str(new_dir), (now, now))

    report = sweep(paths, retention, now=now)
    assert not old_dir.exists()
    assert new_dir.exists()
    assert str(old_dir) in report.removed_worktrees
    assert str(new_dir) not in report.removed_worktrees


# ---------------------------------------------------------------------------
# 2. Artifact window enforced with its own threshold
# ---------------------------------------------------------------------------


def test_artifact_window(tmp_path: Path) -> None:
    """Artifacts older than artifact_days are removed; recent ones are not."""
    now = _now()
    paths = _paths(tmp_path)
    retention = _retention()

    old_art = Path(paths.artifact_root) / "old.dat"
    old_art.write_bytes(b"data")
    os.utime(str(old_art), (now - 15 * 86400, now - 15 * 86400))

    new_art = Path(paths.artifact_root) / "new.dat"
    new_art.write_bytes(b"data")
    os.utime(str(new_art), (now, now))

    report = sweep(paths, retention, now=now)
    assert not old_art.exists()
    assert new_art.exists()
    assert str(old_art) in report.removed_artifacts
    assert str(new_art) not in report.removed_artifacts


# ---------------------------------------------------------------------------
# 3. Log window enforced with its own threshold
# ---------------------------------------------------------------------------


def test_log_window(tmp_path: Path) -> None:
    """Logs older than log_days are removed; recent ones are not."""
    now = _now()
    paths = _paths(tmp_path)
    retention = _retention()

    old_log = Path(paths.log_root) / "old.log"
    old_log.write_bytes(b"logline\n")
    os.utime(str(old_log), (now - 31 * 86400, now - 31 * 86400))

    new_log = Path(paths.log_root) / "new.log"
    new_log.write_bytes(b"logline\n")
    os.utime(str(new_log), (now, now))

    report = sweep(paths, retention, now=now)
    assert not old_log.exists()
    assert new_log.exists()
    assert str(old_log) in report.removed_logs
    assert str(new_log) not in report.removed_logs


# ---------------------------------------------------------------------------
# 4. dry_run removes nothing but reports what would go
# ---------------------------------------------------------------------------


def test_dry_run_no_removal(tmp_path: Path) -> None:
    """When dry_run=True nothing is removed, but the report is populated."""
    now = _now()
    paths = _paths(tmp_path)
    retention = _retention()

    old_art = Path(paths.artifact_root) / "old.dat"
    old_art.write_bytes(b"data")
    os.utime(str(old_art), (now - 20 * 86400, now - 20 * 86400))

    old_dir = Path(paths.worktree_root) / "old-tree"
    old_dir.mkdir()
    os.utime(str(old_dir), (now - 10 * 86400, now - 10 * 86400))

    report = sweep(paths, retention, now=now, dry_run=True)
    assert old_art.exists()
    assert old_dir.exists()
    assert str(old_art) in report.removed_artifacts
    assert str(old_dir) in report.removed_worktrees
    assert len(report.errors) == 0


# ---------------------------------------------------------------------------
# 5. repository_root survives even ancient entries
# ---------------------------------------------------------------------------


def test_repository_root_never_touched(tmp_path: Path) -> None:
    """Even an ancient directory under repository_root survives."""
    now = _now()
    paths = _paths(tmp_path)
    retention = _retention()

    old_repo = Path(paths.repository_root) / "ancient"
    old_repo.mkdir()
    os.utime(str(old_repo), (now - 365 * 86400, now - 365 * 86400))

    report = sweep(paths, retention, now=now)
    assert str(old_repo) not in report.removed_worktrees
    assert str(old_repo) not in report.removed_artifacts
    assert str(old_repo) not in report.removed_logs
    assert old_repo.exists()


# ---------------------------------------------------------------------------
# 6. Unremovable entry lands in errors, sweep continues
# ---------------------------------------------------------------------------


def test_unremovable_entry_in_errors(tmp_path: Path) -> None:
    """An entry that raises OSError on removal is recorded and the sweep
    continues with other entries."""
    now = _now()
    paths = _paths(tmp_path)
    retention = _retention()

    # Create an old directory containing a file with 0o000 perms.
    # shutil.rmtree will fail when it tries to delete the inner file
    # as non-root (and even as root the OSError propagates from the
    # inner unlink call on some kernels).  We patch the error path
    # instead to keep the test deterministic and portable.
    import dpmtf_lightworker.retention as mod

    bad_dir = Path(paths.artifact_root) / "locked-dir"
    bad_dir.mkdir()
    inner = bad_dir / "inner.dat"
    inner.write_bytes(b"inner")
    os.utime(str(bad_dir), (now - 20 * 86400, now - 20 * 86400))

    good_file = Path(paths.artifact_root) / "ok.dat"
    good_file.write_bytes(b"ok")
    os.utime(str(good_file), (now, now))

    # Patch shutil.rmtree to raise OSError on "locked-dir"
    real_rmtree = __import__("shutil").rmtree

    def fake_rmtree(path, *args, **kwargs):
        if "locked-dir" in str(path):
            raise OSError("Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    import shutil
    shutil.rmtree = fake_rmtree
    try:
        report = sweep(paths, retention, now=now)
        assert good_file.exists()
        # The bad_dir should still exist (removal failed)
        assert bad_dir.exists()
        # And the error should be recorded
        assert any("locked-dir" in err for err in report.errors)
    finally:
        shutil.rmtree = real_rmtree


# ---------------------------------------------------------------------------
# 7. Files (not dirs) under worktree_root are left alone
# ---------------------------------------------------------------------------


def test_files_under_worktree_root_left_alone(tmp_path: Path) -> None:
    """Only directories are removed from worktree_root; standalone files
    are left untouched regardless of age."""
    now = _now()
    paths = _paths(tmp_path)
    retention = _retention()

    old_file = Path(paths.worktree_root) / "old-file.txt"
    old_file.write_text("data")
    os.utime(str(old_file), (now - 10 * 86400, now - 10 * 86400))

    old_dir = Path(paths.worktree_root) / "old-tree"
    old_dir.mkdir()
    os.utime(str(old_dir), (now - 10 * 86400, now - 10 * 86400))

    report = sweep(paths, retention, now=now)
    assert old_file.exists()
    assert str(old_file) not in report.removed_worktrees
    assert not old_dir.exists()
    assert str(old_dir) in report.removed_worktrees


# ---------------------------------------------------------------------------
# 8. Missing root is skipped silently
# ---------------------------------------------------------------------------


def test_missing_root_skipped(tmp_path: Path) -> None:
    """If a root directory does not exist, the sweep does not raise."""
    now = _now()
    paths = _paths(tmp_path)
    retention = _retention()

    artifact_root_path = Path(paths.artifact_root)
    artifact_root_path.rmdir()

    report = sweep(paths, retention, now=now, dry_run=True)
    assert report.removed_artifacts == ()
    assert len(report.errors) == 0


# ---------------------------------------------------------------------------
# 9. Report is a SweepReport frozen dataclass
# ---------------------------------------------------------------------------


def test_report_is_frozen_dataclass() -> None:
    """SweepReport is immutable."""
    report = SweepReport(
        removed_worktrees=(),
        removed_artifacts=(),
        removed_logs=(),
        errors=(),
    )
    with pytest.raises((AttributeError, Exception)):
        report.removed_worktrees = ("x",)  # type: ignore[misc]


def test_directories_under_artifact_and_log_roots_are_swept_too(tmp_path):
    """Directory-sweeping under artifact_root and log_root is INTENDED.

    The review of EXEC-018 found the contract said "Files" while the code
    also removes directories, and the suite left the distinction unpinned.
    The Human accepted the behaviour at merge (2026-08-08): a stray
    directory under those roots is refuse like any other. This test turns
    that decision from an accepted ambiguity into a bound semantic, so it
    cannot silently regress either way.
    """
    import os
    import time as _time

    from dpmtf_lightworker.config import PathsSection, RetentionSection
    from dpmtf_lightworker.retention import sweep

    art = tmp_path / "artifacts"
    art.mkdir()
    old_dir = art / "stray-directory"
    old_dir.mkdir()
    (old_dir / "junk.bin").write_bytes(b"x")
    stamp = _time.time() - 60 * 24 * 3600
    os.utime(old_dir, (stamp, stamp))

    paths = PathsSection(
        repository_root=str(tmp_path / "repos"),
        worktree_root=str(tmp_path / "wt"),
        artifact_root=str(art),
        log_root=str(tmp_path / "logs"),
        opencode_config_root=str(tmp_path / "oc"),
    )
    retention = RetentionSection(
        successful_worktree_hours=1, failed_worktree_days=7,
        artifact_days=14, log_days=30,
    )
    report = sweep(paths, retention, now=_time.time())
    assert str(old_dir) in report.removed_artifacts
    assert not old_dir.exists()
