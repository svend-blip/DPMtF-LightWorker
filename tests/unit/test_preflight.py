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


def _run_in(repo_root: Path, env_extra: dict | None = None) -> dict:
    """Run preflight against a fixture tree.

    The script derives its own repo root from `dirname $0/..`, so the only
    way to point it at a fixture is to put it there. An env override does
    nothing — rehearse the mechanism, not a stand-in for it.
    """
    scripts = repo_root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO_ROOT / "scripts" / "preflight.sh", scripts / "preflight.sh")
    env = dict(os.environ)
    env.update(env_extra or {})
    out = subprocess.run(
        ["bash", str(scripts / "preflight.sh"), "--json"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    return {c["name"]: c for c in json.loads(out.stdout)["checks"]}


def _fixture_repo(tmp_path: Path, *, worker_yaml: str | None,
                  example: bool = True) -> Path:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    if example:
        (tmp_path / "config" / "worker.example.yaml").write_text(
            "father:\n  base_url: http://main5090:9130\n"
            "network:\n  expected_father_host: main5090\n", encoding="utf-8")
    if worker_yaml is not None:
        (tmp_path / "config" / "worker.yaml").write_text(worker_yaml, encoding="utf-8")
    return tmp_path


class ChecksMustNotClaimMoreThanTheyMeasure:
    """All three defects here were found by the first real run on svend3060.

    Each check reported a cause it had not tested. A wrong *reason* is worse
    than a wrong result: it sends the next person after a problem that does
    not exist. One of these had a reader reinstalling a tool that worked.
    """


def test_the_example_config_is_not_a_configuration(tmp_path: Path) -> None:
    """`worker.example.yaml` is committed, so accepting it made this check
    unable to fail — it passed on a machine with no configuration at all."""
    checks = _run_in(_fixture_repo(tmp_path, worker_yaml=None))
    assert checks["worker_configuration"]["status"] == "fail", \
        "an example config was accepted as a configuration"


def test_a_real_worker_yaml_passes(tmp_path: Path) -> None:
    checks = _run_in(_fixture_repo(tmp_path, worker_yaml="father:\n  base_url: http://127.0.0.1:1\n"))
    assert checks["worker_configuration"]["status"] == "pass"


def test_father_check_does_not_fall_back_to_the_example(tmp_path: Path) -> None:
    """With no worker.yaml the reason must name the missing config.

    It used to read the example's `main5090`, fail to resolve it, and report
    the Father unreachable — on a machine where `curl /api/health` returned
    200.
    """
    checks = _run_in(_fixture_repo(tmp_path, worker_yaml=None))
    reason = checks["father_reachability"]["reason"]
    assert "worker.yaml" in reason, f"reason blames the wrong thing: {reason}"
    assert "main5090" not in reason, "the example's placeholder host leaked in"


def test_father_check_reports_no_answer_rather_than_no_resolution(tmp_path: Path) -> None:
    """Named 'reachability', it must test whether Father answers.

    Port 1 on loopback resolves perfectly and answers nothing.
    """
    repo = _fixture_repo(tmp_path, worker_yaml="father:\n  base_url: http://127.0.0.1:1\n")
    checks = _run_in(repo)
    assert checks["father_reachability"]["status"] == "fail"
    assert "answer" in checks["father_reachability"]["reason"]


def test_a_base_url_with_a_port_survives_extraction(tmp_path: Path) -> None:
    """The whole URL must reach the reason, not the tail after the last colon.

    The greedy `.*:` used elsewhere in the script reduced
    `http://100.82.231.128:9130` to `9130`, and the check then reported a live
    Father as unreachable. The earlier test missed it because it asserted the
    SHAPE of the outcome — a failure whose reason mentions "answer" — and both
    held with a mangled URL. Assert the value.
    """
    repo = _fixture_repo(
        tmp_path, worker_yaml="father:\n  base_url: http://127.0.0.1:1\n")
    reason = _run_in(repo)["father_reachability"]["reason"]
    assert "http://127.0.0.1:1" in reason, f"URL was mangled: {reason}"


def test_absent_and_broken_allocator_give_different_reasons(tmp_path: Path) -> None:
    """`--version` is not a flag that CLI has; it exits 2. Reporting that as
    'not found on PATH' sent a reader to reinstall a working binary."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # on PATH, but exits non-zero for every invocation
    (fake_bin / "model-allocator").write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    (fake_bin / "model-allocator").chmod(0o755)
    repo = _fixture_repo(tmp_path, worker_yaml="father:\n  base_url: http://127.0.0.1:1\n")

    broken = _run_in(repo, {"PATH": f"{fake_bin}:{os.environ['PATH']}"})["model_allocator_command"]
    assert broken["status"] == "fail"
    assert "not found on PATH" not in broken["reason"], \
        "a present-but-broken allocator was reported as absent"
    assert "on PATH" in broken["reason"]

    absent = _run_in(repo, {"PATH": "/usr/bin:/bin"})["model_allocator_command"]
    assert "not found on PATH" in absent["reason"]


def test_data_directories_follow_the_config_not_a_hardcoded_triple(tmp_path: Path) -> None:
    """The check used /var/lib, /var/log and /tmp regardless of worker.yaml.

    On a worker without passwordless sudo every root sits under $HOME, so it
    reported three directories missing while all the configured ones existed.
    """
    roots = tmp_path / "roots"
    (roots / "repos").mkdir(parents=True)
    (roots / "logs").mkdir(parents=True)
    repo = _fixture_repo(tmp_path, worker_yaml=(
        "father:\n  base_url: http://127.0.0.1:1\n"
        f"paths:\n  repository_root: {roots}/repos\n  log_root: {roots}/logs\n"))
    check = _run_in(repo)["worker_data_directories"]
    assert check["status"] == "pass", f"configured dirs exist but: {check['reason']}"
    assert "/var/lib" not in check["reason"]


def test_a_missing_configured_directory_is_reported_by_name(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path, worker_yaml=(
        "father:\n  base_url: http://127.0.0.1:1\n"
        "paths:\n  repository_root: /nonexistent/repos\n"))
    check = _run_in(repo)["worker_data_directories"]
    assert check["status"] == "fail"
    assert "/nonexistent/repos" in check["reason"]
