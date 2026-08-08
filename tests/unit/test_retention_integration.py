"""The sweeper runs FROM the loop: hourly, journal-logged, never fatal.

The module existed on main with nothing calling it -- a tool on a shelf.
The Human's decision (2026-08-08): hourly cadence in the daemon loop with
journal logging. Three properties carry that decision:

- due at startup and after the interval, not in between
- every removed path is logged by name -- a daemon that deletes must say
  what it deleted, or a swept file is indistinguishable from a vanished one
- a sweep failure is logged and skipped: housekeeping must never take the
  worker off the network
"""

from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dpmtf_lightworker.config import PathsSection, RetentionSection  # noqa: E402
from dpmtf_lightworker.worker import (  # noqa: E402
    RETENTION_SWEEP_INTERVAL_SECONDS,
    retention_sweep_due,
    run_retention_sweep,
)


def _config(tmp_path):
    art = tmp_path / "artifacts"
    art.mkdir()
    return types.SimpleNamespace(
        paths=PathsSection(
            repository_root=str(tmp_path / "repos"),
            worktree_root=str(tmp_path / "wt"),
            artifact_root=str(art),
            log_root=str(tmp_path / "logs"),
            opencode_config_root=str(tmp_path / "oc"),
        ),
        retention=RetentionSection(
            successful_worktree_hours=1, failed_worktree_days=7,
            artifact_days=14, log_days=30,
        ),
    )


class TestDue:

    def test_due_at_startup(self):
        assert retention_sweep_due(None, 1000.0) is True

    def test_not_due_inside_the_interval(self):
        assert retention_sweep_due(1000.0, 1000.0 + 100) is False

    def test_due_after_the_interval(self):
        assert retention_sweep_due(
            1000.0, 1000.0 + RETENTION_SWEEP_INTERVAL_SECONDS) is True


class TestTheSweepCall:

    def test_removed_paths_are_logged_by_name(self, tmp_path, capsys):
        cfg = _config(tmp_path)
        old = Path(cfg.paths.artifact_root) / "ancient.bin"
        old.write_bytes(b"x")
        stamp = time.time() - 60 * 24 * 3600
        os.utime(old, (stamp, stamp))
        run_retention_sweep(cfg)
        err = capsys.readouterr().err
        assert str(old) in err, "the deleted path was not named in the journal"
        assert "1 removed" in err
        assert not old.exists()

    def test_the_heartbeat_line_appears_even_when_nothing_goes(self, tmp_path, capsys):
        run_retention_sweep(_config(tmp_path))
        assert "retention sweep done: 0 removed" in capsys.readouterr().err

    def test_a_failing_sweep_never_raises(self, tmp_path, capsys, monkeypatch):
        import dpmtf_lightworker.retention as retention

        def boom(*a, **k):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(retention, "sweep", boom)
        run_retention_sweep(_config(tmp_path))    # må ikke rejse
        assert "retention sweep failed" in capsys.readouterr().err
