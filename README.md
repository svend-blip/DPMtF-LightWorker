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

## Environment Variables

The committed `.env.example` carries environment-variable *names*, never
resolved absolute paths or secrets. Required names:

* `LIGHTWORKER_ID` — worker identity asserted against `envelope.worker_id`
* `LIGHTWORKER_SUPPORTED_SCHEMA_VERSIONS` — comma-separated allow-list
* `LIGHTWORKER_SUPPORTED_CLIENTS` — comma-separated allow-list
* `LIGHTWORKER_REPOSITORY_ROOT` — root for repository and deliverable paths
