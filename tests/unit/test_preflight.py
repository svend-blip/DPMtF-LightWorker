"""Tests for scripts/preflight.sh (GOAL.md §25).

The script is run as a subprocess. These tests cover what can be
held to without a real machine:

* the script parses with ``bash -n``
* ``set -euo pipefail`` is within the first twenty lines
* ``--json`` produces a parseable JSON document whose ``checks`` list
  has at least twelve entries
* secrets in the environment do not appear in stdout or stderr
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PREFLIGHT = REPO_ROOT / "scripts" / "preflight.sh"


def _has_bash() -> bool:
    return shutil.which("bash") is not None


@pytest.mark.skipif(not _has_bash(), reason="bash not available")
def test_preflight_is_parseable() -> None:
    """``bash -n`` exits 0 — the script has no syntax errors."""
    completed = subprocess.run(
        ["bash", "-n", str(PREFLIGHT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(not _has_bash(), reason="bash not available")
def test_preflight_has_set_euo_pipefail_in_first_twenty_lines() -> None:
    """``set -euo pipefail`` is within the first twenty lines."""
    with PREFLIGHT.open("r") as f:
        head = "".join(f.readline() for _ in range(20))
    assert "set -euo pipefail" in head


@pytest.mark.skipif(not _has_bash(), reason="bash not available")
def test_preflight_json_is_parseable_and_has_at_least_twelve_checks() -> None:
    """``--json`` emits a JSON document; the checks list has ≥12 entries."""
    completed = subprocess.run(
        ["bash", str(PREFLIGHT), "--json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode in (0, 1), (
        f"unexpected exit {completed.returncode}: {completed.stderr}"
    )
    assert completed.stdout, "preflight produced no JSON on stdout"
    document = json.loads(completed.stdout)
    checks = document.get("checks")
    assert isinstance(checks, list)
    assert len(checks) >= 12, f"only {len(checks)} checks reported"
    for check in checks:
        assert "name" in check
        assert "status" in check
        assert "blocking" in check
        assert "reason" in check


@pytest.mark.skipif(not _has_bash(), reason="bash not available")
def test_preflight_does_not_leak_secrets() -> None:
    """A secret in the env does not appear anywhere in stdout or stderr."""
    env = {
        **os.environ,
        "ANTHROPIC_API_KEY": "sk-ant-TESTSECRET123",
        "LIGHTWORKER_AUTH_TOKEN": "tok-TESTSECRET456",
    }
    completed = subprocess.run(
        ["bash", str(PREFLIGHT), "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    combined = (completed.stdout or "") + (completed.stderr or "")
    assert combined, "preflight produced no output at all"
    assert "TESTSECRET" not in combined, combined
