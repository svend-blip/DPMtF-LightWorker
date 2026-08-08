"""Retention sweeper (GOAL.md §12).

Scans the four path roots and removes entries whose age (measured by mtime)
exceeds the configured retention window.

Worktrees: every directory that still lives directly under ``worktree_root``
is a leftover from a prior run — either a completed run whose successful
window has passed or a failed run whose failure window has passed. The
conservative choice is to apply the *failure* window (``failed_worktree_days``)
to all directories, regardless of outcome, because the worker has no way to
distinguish a finished run from a failed one at sweep time.

Artifacts and logs are treated as simple files with their own independent
thresholds.

``repository_root`` is NEVER touched — it holds the mirrors and must not
even reach the deletion code path.

Directories under artifact_root and log_root are swept like files — a
stray directory there is refuse like any other. Accepted as intended by
the Human at the EXEC-018 merge (2026-08-08) after review flagged the
contract's narrower "Files" wording.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from dpmtf_lightworker.config import PathsSection, RetentionSection


@dataclass(frozen=True, slots=True)
class SweepReport:
    """Immutable summary of what the sweeper did (or would have done)."""

    removed_worktrees: tuple[str, ...]
    removed_artifacts: tuple[str, ...]
    removed_logs: tuple[str, ...]
    errors: tuple[str, ...]


# Thresholds are stored as integers in RetentionSection; convert once
# into seconds for the age comparison.
def _seconds(days: int) -> float:
    return days * 86400.0


def _sweep_dir_with_errors(
    root: str,
    max_age_seconds: float,
    now: float,
    dry_run: bool,
    only_dirs: bool = False,
) -> tuple[str, ...]:
    """Sweep a directory tree with error collection.

    :param root: Path to the root directory.
    :param max_age_seconds: Entries older than this (by mtime) are removed.
    :param now: Current time (epoch seconds).
    :param dry_run: If ``True``, populate report without removing.
    :param only_dirs: When ``True``, skip non-directory entries entirely
        (used for ``worktree_root`` where only subdirectories are candidates).
    """
    removed: list[str] = []
    errors: list[str] = []
    root_path = Path(root)

    if not root_path.is_dir():
        return tuple(), tuple()

    for entry in root_path.iterdir():
        if only_dirs and not entry.is_dir():
            continue

        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue

        age = now - mtime
        if age < max_age_seconds:
            continue

        if dry_run:
            removed.append(str(entry))
        else:
            try:
                if entry.is_dir():
                    import shutil
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed.append(str(entry))
            except OSError as exc:
                errors.append(f"{entry}: {exc}")

    return tuple(removed), tuple(errors)


def sweep(
    paths: PathsSection,
    retention: RetentionSection,
    *,
    now: float | None = None,
    dry_run: bool = False,
) -> SweepReport:
    """Run the retention sweeper.

    :param paths: A :class:`PathsSection` giving the four roots.
    :param retention: A :class:`RetentionSection` giving the windows.
    :param now: An injectable clock (epoch seconds). Defaults to
        ``time.time``.
    :param dry_run: When ``True``, compute the report without removing
        anything.
    :returns: A :class:`SweepReport` summarising what was removed or
        would have been removed.
    """
    if now is None:
        now = time.time()

    now = float(now)

    # Separate the sweep into two phases: one that tracks errors (worktrees)
    # and ones that don't (artifacts, logs — no entries under those should
    # raise, but we keep the contract uniform).

    # 1. Worktrees — directories only under worktree_root
    removed_worktrees, errors = _sweep_dir_with_errors(
        paths.worktree_root,
        _seconds(retention.failed_worktree_days),
        now,
        dry_run,
        only_dirs=True,
    )

    # 2. Artifacts — files under artifact_root
    removed_artifacts, art_errors = _sweep_dir_with_errors(
        paths.artifact_root,
        _seconds(retention.artifact_days),
        now,
        dry_run,
    )
    errors = (*errors, *art_errors)

    # 3. Logs — files under log_root
    removed_logs, log_errors = _sweep_dir_with_errors(
        paths.log_root,
        _seconds(retention.log_days),
        now,
        dry_run,
    )
    errors = (*errors, *log_errors)

    # 4. repository_root is NEVER touched — no sweep call at all.

    return SweepReport(
        removed_worktrees=removed_worktrees,
        removed_artifacts=removed_artifacts,
        removed_logs=removed_logs,
        errors=errors,
    )


__all__ = ["SweepReport", "sweep"]
