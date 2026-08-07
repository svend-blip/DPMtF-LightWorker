#!/usr/bin/env bash
set -euo pipefail
# DPMtF-LightWorker preflight (GOAL.md §25).
#
# Verifies the sixteen checks §25 lists on a machine before it is
# allowed to claim a role execution. Non-destructive (§25): no
# downloads, no installs, no writes outside a temporary file.
#
# Modes:
#   --json   one JSON document on stdout, nothing else.
#   (default) human-readable lines on stdout.
#
# In --json mode, any human-readable progress or reason must go to
# stderr. Criteria pipe stdout into json.load, and any other byte
# breaks the parser.
#
# Blocking vs advisory: a blocking check failure exits non-zero in
# non-JSON mode and is reported as "blocking": true in the JSON
# document. Advisory checks (GPU visibility on a machine without
# one) may warn without failing, but the JSON document's "checks"
# list marks each entry as "blocking" so a reader can tell a
# warning from a refusal.

MODE="text"
for arg in "$@"; do
    case "$arg" in
        --json) MODE="json" ;;
        --help|-h)
            echo "Usage: $0 [--json]" >&2
            exit 0
            ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_OUT="$(mktemp)"
trap 'rm -f "$TMP_OUT"' EXIT

# Append one JSON object to TMP_OUT. Each line is one check.
emit_check() {
    local name="$1"
    local status="$2"
    local blocking="$3"
    local reason="$4"
    python3 -c "
import json, sys
print(json.dumps({
    'name': sys.argv[1],
    'status': sys.argv[2],
    'blocking': sys.argv[3] == 'true',
    'reason': sys.argv[4],
}))
" "$name" "$status" "$blocking" "$reason" >> "$TMP_OUT"
}

try_silent() {
    if "$@" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

# 1. Python version
if try_silent python3 --version; then
    emit_check "python_version" "pass" "true" "python3 available"
else
    emit_check "python_version" "fail" "true" "python3 not found on PATH"
fi

# 2. Git
if try_silent git --version; then
    emit_check "git" "pass" "true" "git available"
else
    emit_check "git" "fail" "true" "git not found on PATH"
fi

# 3. tmux
if try_silent tmux -V; then
    emit_check "tmux" "pass" "true" "tmux available"
else
    emit_check "tmux" "fail" "true" "tmux not found on PATH"
fi

# 4. Tailscale status
if try_silent tailscale status; then
    emit_check "tailscale_status" "pass" "true" "tailscale reachable"
else
    emit_check "tailscale_status" "fail" "true" "tailscale not reachable or not installed"
fi

# 5. Father reachability
#
# Asks whether Father ANSWERS, not whether its name resolves. This ran
# `getent hosts` and reported "reachability" — named for one thing, measuring
# another. On svend3060, 2026-08-07, it declared Father unreachable while
# `curl /api/health` on that same machine returned 200: the name in the config
# did not exist, but the service did.
#
# It reads only worker.yaml. The example ships `base_url: http://main5090:9130`,
# a host that exists nowhere, so falling back to it turned a missing config
# into a confident false report about a live service.
FATHER_URL=""
if [ -f "$REPO_ROOT/config/worker.yaml" ]; then
    FATHER_URL=$(grep -E '^[[:space:]]*base_url' "$REPO_ROOT/config/worker.yaml" 2>/dev/null | head -n1 | sed -E 's/.*:[[:space:]]*([^"#[:space:]]+).*/\1/' || true)
fi
if [ -z "$FATHER_URL" ]; then
    emit_check "father_reachability" "fail" "true" "no base_url in config/worker.yaml"
elif try_silent curl -s -m 5 -f "$FATHER_URL/api/health"; then
    emit_check "father_reachability" "pass" "true" "father answered /api/health at $FATHER_URL"
else
    emit_check "father_reachability" "fail" "true" "father did not answer /api/health at $FATHER_URL"
fi

# 6. Worker configuration
#
# `config/worker.example.yaml` is committed, so accepting it made this check
# unable to fail: it passed on a machine with no configuration at all, and
# then fed its placeholder hostname to the Father check above. A template is
# not a configuration.
if [ -f "$REPO_ROOT/config/worker.yaml" ]; then
    emit_check "worker_configuration" "pass" "true" "config/worker.yaml present"
else
    emit_check "worker_configuration" "fail" "true" "no config/worker.yaml — copy worker.example.yaml and fill it in"
fi

# 7. Worker authentication — report that the credential is set,
# never what it contains. §25 says report the state, never the value.
if [ -n "${LIGHTWORKER_AUTH_TOKEN:-}" ]; then
    emit_check "worker_authentication" "pass" "true" "LIGHTWORKER_AUTH_TOKEN is set"
else
    emit_check "worker_authentication" "warn" "false" "LIGHTWORKER_AUTH_TOKEN is not set"
fi

# 8. Model Allocator command
#
# Absent and present-but-broken are different faults and get different
# reasons. This ran `model-allocator --version`, which that CLI does not
# support — it requires a subcommand and exits 2 — and reported the failure as
# "not found on PATH". Measured on svend3060, 2026-08-07: the binary was on
# PATH and working, and preflight sent the reader to reinstall it.
if ! try_silent command -v model-allocator; then
    emit_check "model_allocator_command" "fail" "true" "model-allocator not found on PATH"
elif try_silent model-allocator --help; then
    emit_check "model_allocator_command" "pass" "true" "model-allocator on PATH and runs"
else
    emit_check "model_allocator_command" "fail" "true" "model-allocator is on PATH but did not run"
fi

# 9. Allocator configuration loading
if try_silent model-allocator preflight --role imple01 --client opencode; then
    emit_check "allocator_configuration_loading" "pass" "true" "allocator preflight succeeded"
else
    emit_check "allocator_configuration_loading" "fail" "true" "allocator preflight failed or not installed"
fi

# 10. Selected client installation
if try_silent command -v opencode; then
    emit_check "selected_client_installation" "pass" "true" "opencode on PATH"
else
    emit_check "selected_client_installation" "fail" "true" "opencode not found on PATH"
fi

# 11. Selected backend availability
if try_silent command -v ollama; then
    emit_check "selected_backend_availability" "pass" "true" "ollama on PATH"
elif try_silent command -v llama-server; then
    emit_check "selected_backend_availability" "pass" "true" "llama-server on PATH"
else
    emit_check "selected_backend_availability" "warn" "false" "no recognised backend on PATH (ollama or llama-server)"
fi

# 12. GPU visibility (advisory)
if try_silent nvidia-smi -L; then
    emit_check "gpu_visibility" "pass" "true" "nvidia-smi reports a GPU"
else
    emit_check "gpu_visibility" "warn" "false" "nvidia-smi not available or no GPU detected"
fi

# 13. VRAM information
if try_silent nvidia-smi --query-gpu=memory.total --format=csv,noheader; then
    emit_check "vram_information" "pass" "true" "nvidia-smi reports VRAM"
else
    emit_check "vram_information" "warn" "false" "nvidia-smi could not report VRAM"
fi

# 14. Worker data directories
MISSING_DIRS=""
for d in /var/lib/dpmtf-lightworker /var/log/dpmtf-lightworker /tmp/dpmtf-lightworker; do
    if [ ! -d "$d" ]; then
        MISSING_DIRS="$MISSING_DIRS $d"
    fi
done
if [ -z "$MISSING_DIRS" ]; then
    emit_check "worker_data_directories" "pass" "true" "all worker directories present"
else
    emit_check "worker_data_directories" "warn" "false" "missing directories:$MISSING_DIRS"
fi

# 15. Repository read access
if try_silent git -C "$REPO_ROOT" ls-remote --heads origin; then
    emit_check "repository_read_access" "pass" "true" "origin reachable"
else
    emit_check "repository_read_access" "warn" "false" "origin not reachable from this network"
fi

# 16. One-execution limit
if [ -f "$REPO_ROOT/config/worker.yaml" ]; then
    MAX=$(grep -E '^[[:space:]]*max_parallel_executions' "$REPO_ROOT/config/worker.yaml" 2>/dev/null | head -n1 | sed -E 's/.*:[[:space:]]*([^"#[:space:]]+).*/\1/' || true)
    if [ "$MAX" = "1" ]; then
        emit_check "one_execution_limit" "pass" "true" "max_parallel_executions=1"
    else
        emit_check "one_execution_limit" "warn" "false" "max_parallel_executions is not 1 (got: $MAX)"
    fi
else
    emit_check "one_execution_limit" "warn" "false" "no worker.yaml to inspect"
fi

python3 - "$TMP_OUT" "$MODE" <<'PY'
import json
import sys

path = sys.argv[1]
mode = sys.argv[2]

with open(path, "r", encoding="utf-8") as f:
    raw = f.read().strip()

checks = []
if raw:
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        checks.append(json.loads(line))

blocking_failed = any(
    c["status"] == "fail" and c.get("blocking", False) for c in checks
)

if mode == "json":
    print(json.dumps({
        "checks": checks,
        "exit_code": 1 if blocking_failed else 0,
    }))
else:
    for c in checks:
        marker = "OK" if c["status"] == "pass" else ("WARN" if c["status"] == "warn" else "FAIL")
        print(f"[{marker}] {c['name']}: {c['reason']}")
PY

# Exit non-zero on a blocking failure.
if python3 -c "
import json
import sys
with open('$TMP_OUT') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d['status'] == 'fail' and d.get('blocking', False):
            sys.exit(0)
sys.exit(1)
"; then
    exit 1
fi
exit 0
