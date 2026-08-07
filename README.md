# DPMtF-LightWorker

Lightweight remote role-execution node for the DPMtF (Delegated Project
Management through Father) ecosystem. Each LightWorker receives exactly one
role-execution envelope from DPMtF Father, executes it inside a disposable
Git worktree under a worker-local Model Allocator, and returns a structured
result.

This repository currently contains only the **envelope layer** specified in
`GOAL.md` §13. Later phases will add the worker loop, the Model Allocator
adapter, the Git isolation layer, the tmux/OpenCode transport and the Father
integration. See `GOAL.md` §43 for the planned phases.

## Place in the DPMtF Ecosystem

Four components, one machine boundary:

```
   model-allocator                  model-allocator
   (Father's copy)                  (worker's copy)
         │ resolves role→model            │
         ▼                                ▼
   DPMtF-WebUI ("Father") ◄──────── DPMtF-LightWorker
   flows · dispatch · evidence      polls Father over Tailscale,
   gates · SQLite · port 9130       executes one role at a time in
         │                          disposable worktrees
         └── mcp-light (port 9135)
             read-only context: loopback for Father's own
             roles, a second tailnet instance for workers
```

| Component | Depends on | Provides |
|-----------|-----------|----------|
| model-allocator | its own machine's `models.yaml`/`roles.yaml` | role→model resolution, runtime lifecycle, client configs |
| DPMtF-WebUI | model-allocator (same machine), SQLite | flows, dispatch, evidence gates, LightWorker endpoints, watchdog |
| mcp-light | read access to DPMtF-WebUI's files and database | governance/flow/verdict lookup over MCP |
| DPMtF-LightWorker | model-allocator (worker machine), Father reachable over Tailscale | remote role execution |

**Install order — each step's preflight checks the one before it:**

1. **model-allocator** — on every machine that runs models (Father and
   each worker), with that machine's own config files.
2. **DPMtF-WebUI** — on Father: `init_db` → `migrate` → uvicorn on 9130.
3. **mcp-light** — on Father (optional but standard): loopback unit, plus
   the tailnet unit if remote workers should reach it.
4. **DPMtF-LightWorker** — on each worker: venv → `worker.yaml` → auth
   token → base client config → `preflight.sh` 16/16 → daemon.

Each repository's own Installation section covers its steps in detail.

## Repository Layout

```text
DPMtF-LightWorker/
├── src/dpmtf_lightworker/
│   ├── __init__.py
│   ├── models.py              # frozen dataclasses for the envelope
│   ├── envelope_validator.py  # validator + ValidatorConfig
│   └── errors.py              # five §24 failure categories
├── tests/unit/
│   └── test_envelope_validator.py
├── pyproject.toml
├── .env.example
└── GOAL.md                    # read-only specification
```

## Requirements

* Python 3.10 or newer
* `pytest` for the test suite

The default test suite runs without GPU, network, a real Father, a real
Model Allocator, a real OpenCode or a real tmux. See `GOAL.md` §29.

## Installation (worker machine)

Prerequisites: model-allocator installed and configured on THIS machine
(step 1 of the ecosystem order), Father reachable over Tailscale, git and
tmux present. Then:

```bash
git clone https://github.com/svend-blip/DPMtF-LightWorker.git
cd DPMtF-LightWorker
python3 -m venv venv && ./venv/bin/pip install -e .

cp config/worker.example.yaml config/worker.yaml   # edit for this machine
echo 'LIGHTWORKER_AUTH_TOKEN="<token from Father>"' > ~/.lightworker-auth
cp <your opencode base config> ~/lightworker/opencode-base.json

bash scripts/preflight.sh          # must be 16/16 before the daemon starts
```

The base client config is required — see "The Base Client Config" below.
A render without a `permission` block is refused by design.

## Running the Worker

The daemon polls Father and executes one role at a time. It runs in a tmux
session the steward starts (a systemd unit needs nothing more than a user
unit — see mcp-light's units for the pattern — but is not yet written):

```bash
tmux new-session -d -s lightworker-daemon -c ~/DPMtF-LightWorker \
  bash -lc 'set -a; . ~/.lightworker-auth; set +a;
            export PYTHONUNBUFFERED=1;
            exec ./venv/bin/dpmtf-lightworker 2>&1 \
              | tee -a ~/lightworker/logs/daemon.log'
```

`PYTHONUNBUFFERED` matters: piped through `tee`, Python block-buffers
stdout, and the log otherwise stays empty until the process dies — which
is exactly when you need it.

**Stopping from Father's UI:** DPMtF-WebUI's *Stop tmux* kills this
daemon session and any `dpmtf-*` execution session over ssh; *Stop
servers* resolves the role's alias ON this machine (its `roles.yaml`, not
Father's stored value) and stops that runtime. Restart is the tmux
command above.

**Watchdog:** Father's `chain-watchdog.service` watches every flow
permanently — remote roles prove life through execution heartbeats, and
are never auto-nudged (a re-sent signal would mint a second execution
offer). A claimed execution with 90s of heartbeat silence draws a
CRITICAL log line for the Human.

## Running the Test Suite

```bash
python3 -m pytest tests/unit -q
```

## Envelope at a Glance

`models.ExecutionEnvelope` is the validated, immutable result of parsing
the role-execution envelope described in `GOAL.md` §13. It carries three
nested structures — `repository`, `handoff` and `result_contract` — and
refuses to validate any payload that violates the nine §13 rejection rules.
The validator raises a typed exception whose `category` attribute is the
canonical §24 failure name, so callers can report failures without knowing
the exception hierarchy.

## The Base Client Config

`model-allocator render-config` emits only `model` and `provider`. Everything
that belongs to *this machine* rather than to the role comes from a base
config the machine's steward owns, named by `allocator.base_config` in
`config/worker.yaml`. The worker deep-merges it **underneath** the allocator's
render — the allocator wins every conflict, so §32 keeps the model and its
context — and validates the result before publishing it.

Three things live there:

* **`permission`** — the §19 confinement. `{worktree}` is replaced with this
  execution's worktree, which no operator can write down in advance. Required:
  a render with no permission block is refused, because an unconfined role is
  worse than a stopped one.
* **`provider.<name>.options.baseURL`** — the endpoint the model is served on.
  The allocator renders the model and its limits but never the endpoint.
* **`mcp`** — optional. See below.

```json
{
  "permission": {
    "external_directory": {
      "{worktree}": "allow",
      "{worktree}/**": "allow"
    }
  },
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://localhost:11434/v1" }
    }
  }
}
```

### Reaching mcp-light from a worker

A role on a worker can use Father's `mcp-light` context server — governance,
flows, roles and verdicts — instead of relying only on what the envelope
carries. It is optional: §19's design is that Father sends the compiled
content in the envelope, and a worker that cannot reach mcp-light still runs.
What it buys is a role that can look context up itself, which measurably
speeds up local models.

mcp-light listens on loopback by default, which a remote worker cannot reach.
Father runs a second instance bound to its Tailscale address for exactly this
(see the mcp-light repository's README). Add to the base config:

```json
"mcp": {
  "mcp-light": {
    "type": "remote",
    "url": "http://<father-tailscale-ip>:9135/mcp",
    "enabled": true,
    "timeout": 10000
  }
}
```

**mcp-light has no authentication**, so the tailnet is the boundary. Enabling
this also means a worker execution can now degrade because a service on
Father is down — a dependency the envelope-only design deliberately avoided.

## Environment Variables

The committed `.env.example` carries environment-variable *names*, never
resolved absolute paths or secrets. Required names:

* `LIGHTWORKER_ID` — worker identity asserted against `envelope.worker_id`
* `LIGHTWORKER_SUPPORTED_SCHEMA_VERSIONS` — comma-separated allow-list
* `LIGHTWORKER_SUPPORTED_CLIENTS` — comma-separated allow-list
* `LIGHTWORKER_REPOSITORY_ROOT` — root for repository and deliverable paths
