"""Tests for the §27 path-boundary and log-redaction helpers.

The traversal, absolute-path, and symlink-escape cases are the three
§27 requires. The symlink case is the one that would survive a
naïve string comparison, and the run's Mission Contract calls it out
explicitly: TG2 asserts that ``normpath`` accepts the string before
``resolve_within`` rejects it.
"""

from __future__ import annotations

import os
import string
from pathlib import Path

import pytest

from dpmtf_lightworker.safe_paths import redact, resolve_within


# ---------------------------------------------------------------------------
# resolve_within — accept case
# ---------------------------------------------------------------------------


def test_legitimate_path_resolves(tmp_path: Path) -> None:
    """A path below the root is returned unchanged."""
    root = tmp_path / "root"
    root.mkdir()
    sub = root / "sub"
    sub.mkdir()
    target = sub / "ok.txt"
    target.write_text("ok")

    resolved = resolve_within(root, target)
    assert str(resolved).endswith("/root/sub/ok.txt")
    assert Path(resolved).samefile(target)


def test_legitimate_path_need_not_exist(tmp_path: Path) -> None:
    """A path that does not exist yet is still accepted.

    An expected deliverable is often a path that has not been written
    yet; what we are preventing is the path *string* pointing
    somewhere it must not.
    """
    root = tmp_path / "root"
    root.mkdir()
    target = root / "future.txt"

    resolved = resolve_within(root, target)
    assert str(resolved).endswith("/root/future.txt")


# ---------------------------------------------------------------------------
# resolve_within — reject cases
# ---------------------------------------------------------------------------


def test_traversal_is_rejected(tmp_path: Path) -> None:
    """``../../etc/passwd`` is rejected."""
    root = tmp_path / "root"
    root.mkdir()

    candidate = root / ".." / ".." / "etc" / "passwd"
    with pytest.raises(ValueError):
        resolve_within(root, candidate)


def test_deep_traversal_is_rejected(tmp_path: Path) -> None:
    """Traversal that stays inside the root string-wise is still rejected.

    The handoff's Mission Contract uses ``root/../../etc/passwd`` which
    escapes the root under both string and path resolution. §27's
    hard case is the symlink escape — tested below. This case is
    covered for completeness.
    """
    root = tmp_path / "root"
    root.mkdir()
    sub = root / "sub"
    sub.mkdir()

    candidate = sub / ".." / ".." / ".." / "etc" / "passwd"
    with pytest.raises(ValueError):
        resolve_within(root, candidate)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    """A link inside the root whose target is outside is rejected."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("secret")

    link = root / "link.txt"
    link.symlink_to(target)

    with pytest.raises(ValueError):
        resolve_within(root, link)


def test_symlink_string_stays_inside_root(tmp_path: Path) -> None:
    """The link's path string stays inside the root — proving the
    string-only check is the wrong tool for this case."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("secret")

    link = root / "link.txt"
    link.symlink_to(target)

    assert os.path.normpath(str(link)).startswith(str(root))


def test_absolute_path_outside_root_is_rejected(tmp_path: Path) -> None:
    """An absolute path outside the root is rejected."""
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(ValueError):
        resolve_within(root, "/etc/passwd")


def test_str_input_is_accepted(tmp_path: Path) -> None:
    """The function accepts ``str`` as well as ``Path``."""
    root = tmp_path / "root"
    root.mkdir()
    target = root / "ok.txt"
    target.write_text("ok")

    resolved = resolve_within(str(root), str(target))
    assert str(resolved).endswith("/root/ok.txt")


# ---------------------------------------------------------------------------
# redact — credential-shaped values
# ---------------------------------------------------------------------------


def test_redact_masks_anthropic_api_key() -> None:
    """A run of the form ``NAME_KEY=value`` is masked."""
    text = "ANTHROPIC_API_KEY=sk-ant-abc123XYZ and the rest survives"
    out = redact(text)
    assert "sk-ant-abc123XYZ" not in out
    assert "ANTHROPIC_API_KEY=***" in out
    assert "the rest survives" in out


def test_redact_masks_bare_token() -> None:
    """A bare ``token=value`` is masked."""
    text = "token=ghp_0123456789abcdef and the rest survives"
    out = redact(text)
    assert "ghp_0123456789abcdef" not in out
    assert "token=***" in out
    assert "the rest survives" in out


def test_redact_preserves_ordinary_text() -> None:
    """A path like ``/w/tree`` is not touched by redact."""
    text = "ANTHROPIC_API_KEY=sk-ant-abc123XYZ token=ghp_0123456789abcdef and the worktree is /w/tree"
    out = redact(text)
    assert "/w/tree" in out


def test_redact_preserves_value_of_an_unrelated_assignment() -> None:
    """A name that does not match the secret heuristic is kept."""
    text = "PATH=/usr/local/bin and the rest survives"
    out = redact(text)
    assert "PATH=/usr/local/bin" in out


def test_redact_handles_password_secret_credential() -> None:
    """Other covered names — password, secret, credential — are masked."""
    for name in ("password", "PASSWORD", "my_secret", "DB_CREDENTIAL"):
        text = f"{name}=hunter2"
        out = redact(text)
        assert "hunter2" not in out, f"{name}: {out!r}"


def test_redact_is_idempotent() -> None:
    """Running redact twice gives the same result as running it once."""
    text = "ANTHROPIC_API_KEY=sk-ant-abc123XYZ token=ghp_0123456789abcdef"
    once = redact(text)
    twice = redact(once)
    assert once == twice
