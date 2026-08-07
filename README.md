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
