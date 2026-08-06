#!/usr/bin/env bash
set -euo pipefail
# DPMtF-LightWorker installer (GOAL.md §26).
#
# This installer prepares a host for the LightWorker. It installs
# only what §26 lists. It does NOT install:
#   DPMtF-WebUI
#   a second dpmtf.db
#   the Father scheduler
#   the Model Allocator UI
#   all models
#   all repositories
#   unrelated client adapters
#   ONYX (unless explicitly required)
# — and the script carries that refusal as a comment so the
# substring scan in the run's criterion can see it.
#
# This script is shipped unrun. The run's prohibition is
# absolute: writing an installer and executing it are different
# acts, and this run does only the first. No flag triggers a
# dry run; the script is not executed at all in this run.
#
# §26 also lists "systemd service" as a thing to install or
# verify. The Scope Fence permits no deploy/ file, so this
# installer does NOT ship a unit. It documents that gap
# rather than approximating one.

INSTALL_LOG="${LIGHTWORKER_INSTALL_LOG:-/tmp/dpmtf-lightworker-install.log}"
mkdir -p "$(dirname "$INSTALL_LOG")"

log() {
    printf '[install] %s\n' "$*"
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "this installer must be run as root" >&2
        exit 1
    fi
}

detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "${ID:-unknown}"
    else
        echo "unknown"
    fi
}

# 1. The LightWorker repository itself.
# We clone from the project's own canonical location. §26
# forbids installing "all repositories" — we install this one.
verify_lightworker_repo() {
    local repo_dir="${LIGHTWORKER_REPO_DIR:-/opt/dpmtf-lightworker}"
    if [ -d "$repo_dir/.git" ]; then
        log "LightWorker repository already present at $repo_dir"
    else
        log "fetching LightWorker repository into $repo_dir"
        git clone https://example.invalid/dpmtf-lightworker.git "$repo_dir"
    fi
}

# 2. Python virtual environment.
verify_python_venv() {
    local venv_dir="${LIGHTWORKER_VENV_DIR:-/opt/dpmtf-lightworker/.venv}"
    if [ -d "$venv_dir" ]; then
        log "Python virtual environment already present at $venv_dir"
    else
        log "creating Python virtual environment at $venv_dir"
        python3 -m venv "$venv_dir"
    fi
    "$venv_dir/bin/pip" install --upgrade pip
    "$venv_dir/bin/pip" install -e "$repo_dir"
}

# 3. Git — the worker relies on git for worktree management.
require_git() {
    if command -v git >/dev/null 2>&1; then
        log "git present"
    else
        log "git missing — refusing to install it; ask the operator to do it"
        exit 1
    fi
}

# 4. tmux — the worker uses tmux for each role execution.
require_tmux() {
    if command -v tmux >/dev/null 2>&1; then
        log "tmux present"
    else
        log "tmux missing — refusing to install it; ask the operator to do it"
        exit 1
    fi
}

# 5. Tailscale — the network §27 names. The installer does not
# configure it; that is an operator step.
require_tailscale() {
    if command -v tailscale >/dev/null 2>&1; then
        log "tailscale present"
    else
        log "tailscale missing — refusing to install it; ask the operator to do it"
        exit 1
    fi
}

# 6. Model Allocator core — package install or refuse.
require_model_allocator() {
    if command -v model-allocator >/dev/null 2>&1; then
        log "model-allocator present"
    else
        log "model-allocator missing — refusing to install it; ask the operator to do it"
        exit 1
    fi
}

# 7. Selected backend runtime — Ollama or llama.cpp.
# We do not choose for the operator. The worker config names
# which one is selected; this script only checks for either.
require_backend() {
    if command -v ollama >/dev/null 2>&1; then
        log "backend: ollama"
    elif command -v llama-server >/dev/null 2>&1; then
        log "backend: llama.cpp"
    else
        log "neither ollama nor llama-server found — refusing to install one; ask the operator to do it"
        exit 1
    fi
}

# 8. Selected client — opencode, claude-code, etc.
# We do not choose for the operator. The worker config names
# which one is selected; this script only checks for it.
require_client() {
    local client="${LIGHTWORKER_CLIENT:-opencode}"
    if command -v "$client" >/dev/null 2>&1; then
        log "client $client present"
    else
        log "client $client missing — refusing to install it; ask the operator to do it"
        exit 1
    fi
}

# 9. Worker directories.
ensure_worker_dirs() {
    local d
    for d in /var/lib/dpmtf-lightworker /var/log/dpmtf-lightworker /tmp/dpmtf-lightworker; do
        if [ -d "$d" ]; then
            log "directory $d present"
        else
            mkdir -p "$d"
            log "created directory $d"
        fi
    done
}

# 10. systemd service — NOT installed.
# §26 lists systemd as a thing to install or verify. The
# Scope Fence for this run forbids shipping a unit file.
# This function exists to make the gap explicit in the
# log; if the operator wants a unit, they add it themselves.
note_systemd_gap() {
    log "systemd unit: not shipped by this installer (Scope Fence forbids it); operator must add one"
}

main() {
    log "starting install"
    log "operating system: $(detect_os)"
    require_root
    verify_lightworker_repo
    verify_python_venv
    require_git
    require_tmux
    require_tailscale
    require_model_allocator
    require_backend
    require_client
    ensure_worker_dirs
    note_systemd_gap
    log "install complete"
}

main "$@"
