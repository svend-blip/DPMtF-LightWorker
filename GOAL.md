# DPMtF-LightWorker — GOAL.md

> **Status:** Initial implementation goal
> **Language:** en-US
> **Repository:** `DPMtF-LightWorker`
> **Father project:** `DPMtF-WebUI`
> **Runtime/model layer:** `model-allocator`
> **Primary worker:** `svend3060`
> **Network:** Private Tailscale network
> **Routing strategy:** Static role/step selection through a stable logical model alias
> **Initial client:** OpenCode
> **Initial execution transport:** Worker-local tmux
> **Initial concurrency:** One active role execution per LightWorker
> **Git result model:** Disposable worktree returning a patch, deliverable, or both

---

## 1. Purpose

Build `DPMtF-LightWorker` as a lightweight remote role-execution node for the DPMtF ecosystem.

A LightWorker normally executes **one assigned DPMtF role step** and returns control to DPMtF Father after that role execution finishes.

The LightWorker does not execute or control the complete DPMtF flow.

The initial deployment must allow a role selected by bridgeV002 on the main RTX 5090 machine to run on the secondary RTX 3060 machine using:

* a stable logical model alias
* a worker-local Model Allocator installation
* a worker-local model backend
* a worker-local client adapter
* a worker-local tmux session
* a disposable Git worktree
* structured result reporting to Father

The solution must expose the RTX 3060 machine's GPU, VRAM, CPU, RAM and local storage without installing a second DPMtF Father instance.

---

## 2. Authoritative Terminology

This project must use the terminology and separation already defined by Model Allocator.

### 2.1 Stable logical alias

A DPMtF role or step refers to a model through a stable logical alias such as:

```text
imple01-3060
review01-3060
svend3060-llama-test
```

The role must not contain:

* a concrete model filename
* a concrete Ollama model name
* a llama.cpp start command
* a provider-specific endpoint
* a hardcoded model path

### 2.2 Machine Profile

A Machine Profile contains machine-specific configuration and environment information.

For the initial worker:

```text
Machine Profile: svend3060
```

Machine-specific absolute paths and runtime locations belong in the worker's Machine Profile or environment configuration, not in reusable repository code.

### 2.3 Runtime profile

A runtime profile identifies the backend type and the configuration mechanism required to operate it.

Examples:

```text
local_ollama_cuda0
local_llamacpp
remote_worker_llamacpp_cuda0
```

A new runtime-profile name may be introduced for LightWorker if needed, but it must retain Model Allocator's existing semantics:

```text
runtime profile
→ backend adapter
→ backend-specific configuration
```

### 2.4 Backend adapter

A backend adapter owns concrete model-runtime operations:

```text
start
stop
unload
status
validate
```

Initial relevant backend adapters:

```text
ollama
llama_cpp
```

The LightWorker must not duplicate these concrete runtime commands outside Model Allocator.

### 2.5 Client adapter

A client adapter owns the concrete command used to launch the role client.

Initial client:

```text
opencode
```

Future supported clients may include:

```text
claude_code
headless
```

The LightWorker must consume the command produced by Model Allocator rather than independently construct an OpenCode launch command.

### 2.6 Lifecycle policy

The alias determines model lifecycle through an existing Model Allocator lifecycle policy.

Relevant policies:

```text
persistent
stop_after_step
shared_runtime
cloud_noop
```

For a dedicated RTX 3060 role execution, the expected initial policy is normally:

```text
stop_after_step
```

The exact policy remains alias configuration, not LightWorker logic.

---

## 3. Existing Three-Layer Architecture

The LightWorker must preserve the existing architecture:

```text
bridgeV002
    owns role/step orchestration and model_alias selection

Model Allocator
    owns alias resolution, validation and runtime lifecycle

Backend adapters
    own concrete runtime commands
```

For remote role execution, the extended architecture is:

```text
DPMtF Father / bridgeV002
    │
    ├── selects flow
    ├── selects step
    ├── selects target role
    ├── selects model_source = model_allocator
    ├── selects model_alias
    └── creates one remote role execution
             │
             │ Tailscale
             ▼
DPMtF-LightWorker
    │
    ├── validates the role-execution envelope
    ├── prepares the disposable worktree
    ├── invokes worker-local Model Allocator
    ├── starts a worker-local tmux session
    ├── launches the allocator-produced client command
    ├── injects the compiled handoff
    ├── monitors the assigned role
    └── returns the role result
             │
             ▼
DPMtF Father / bridgeV002
    │
    ├── validates the result
    ├── applies or stores the result
    ├── records the checkpoint
    └── advances the existing flow
```

---

## 4. Primary Goal

Enable one selected DPMtF role step to be statically routed to `svend3060` through a stable logical alias.

The role must execute in an isolated worker-local environment and return one of:

```text
patch
deliverable_only
patch_and_deliverable
```

A successful implementation must prove:

```text
bridgeV002 role/step selection
→ model_source = model_allocator
→ stable logical alias
→ LightWorker svend3060
→ worker-local alias resolution
→ backend lifecycle
→ client launch in tmux
→ one role execution
→ patch or deliverable returned
→ Father validation
→ independently routed next role
```

---

## 5. Role-Execution Boundary

### 5.1 One role, not one complete flow

A complete DPMtF job may contain several roles:

```text
strict_review
├── archi01
├── imple01
├── review01
├── review02
└── human01
```

The LightWorker normally receives only one role execution:

```text
execution_id: EXEC-123-IMPLE01
target_role: imple01
```

Example placement:

```text
archi01     → main5090
imple01     → svend3060
review01    → main5090
review02    → cloud or main5090
human01     → human
```

### 5.2 Father regains control after every role

The LightWorker must not:

* start the next flow step
* select the next role
* select the next alias
* call `_advance_chain`
* mark the complete DPMtF job as completed
* assume the next role will use the same worker
* keep a role session alive for another execution without a new contract

### 5.3 One active role execution

V1 supports:

```yaml
max_parallel_executions: 1
```

This prevents:

* VRAM contention
* shared model ambiguity
* overlapping tmux ownership
* conflicting disposable worktrees
* duplicate role execution

---

## 6. Responsibility Boundaries

## 6.1 bridgeV002 and DPMtF Father

Father owns:

* complete DPMtF jobs
* flows and flow order
* steps
* roles
* `model_source`
* `model_alias`
* resolution priority between step, role and system default
* handoff IDs
* role-execution IDs
* compiled handoffs
* governance source
* tmux orchestration policy
* deliverable routing
* validation
* checkpoints
* retries
* chain advancement
* final job outcome

The existing resolution priority must remain:

```text
step override
→ role default
→ system default
```

### 6.2 Model Allocator

Model Allocator owns:

* stable logical aliases
* role mappings
* runtime profiles
* alias resolution
* client compatibility
* runtime validation
* runtime start
* runtime stop
* runtime unload
* runtime status
* context configuration
* CPU/GPU offload configuration
* backend-specific flags
* lifecycle policy
* rendering client configuration
* generation of the client launch command

### 6.3 DPMtF-LightWorker

LightWorker owns:

* worker registration
* capability reporting
* polling
* execution claim
* attempt-bound heartbeat
* execution-envelope validation
* repository mirror handling
* disposable worktree handling
* worker-local tmux session handling
* calling worker-local Model Allocator commands
* launching the command returned by `model-allocator run`
* calling `render-config` for OpenCode when required
* handoff injection
* execution monitoring
* result collection
* patch creation
* deliverable packaging
* structured result reporting
* worker-local cleanup

### 6.4 Backend adapters

Backend adapters continue to own concrete runtime behavior.

Examples:

```text
ollama adapter
    warm-up, status and stop through Ollama

llama_cpp adapter
    llama-server argv, PID, port, health polling and process shutdown
```

LightWorker must never directly issue backend lifecycle commands when an allocator adapter already owns them.

---

## 7. Model Allocator Deployment on the Worker

Model Allocator must be installed on `svend3060`.

Minimum installation:

```text
model-allocator core
worker Machine Profile
models.yaml
roles.yaml when role resolution is used locally
runtime_profiles.yaml
required environment variables
required backend runtime
required client
```

The worker installation must support:

```bash
model-allocator resolve
model-allocator validate
model-allocator status
model-allocator start
model-allocator stop
model-allocator unload
model-allocator run
model-allocator render-config
model-allocator preflight
```

The worker must use the installed wrapper when available:

```text
scripts/model-allocator
```

or the module entrypoint:

```text
python3 -m model_allocator
```

### 7.1 Configuration source of truth

Model Allocator configuration remains based on the existing combined sources:

```text
Machine Profile
environment variables
Father database values where available
models.yaml
roles.yaml
runtime_profiles.yaml
bridgeV002 role/step selection
```

For V1 LightWorker execution, Father should send the selected alias and client explicitly.

The worker should not need a complete copy of the Father database to resolve the execution.

### 7.2 Required worker-local configuration

The worker must have enough local allocator configuration to resolve the assigned alias:

```text
alias
runtime_profile
real_model or model_path
context
offload fields
lifecycle_policy
client compatibility
backend environment-variable references
```

### 7.3 Configuration synchronization

V1 may use an explicit deployment or synchronization step for allocator configuration.

Acceptable V1 mechanisms:

```text
Git-managed allocator configuration
rsync from Father to worker
installation script copying validated config
dedicated configuration deployment command
```

The worker must not silently modify the authoritative Father-side allocator configuration.

---

## 8. Static Alias Routing

The selected role or step must use the existing bridgeV002 fields:

```text
model_source = model_allocator
model_alias = <stable logical alias>
```

Example conceptual role selection:

```yaml
role_key: imple01
model_source: model_allocator
model_alias: imple01-3060
execution_target: svend3060
client: opencode
```

`execution_target` is a LightWorker routing concern and may require a new Father-side field or mapping.

It must not be confused with `runtime_profile`.

### 8.1 Alias example

Conceptual worker-capable alias:

```yaml
models:
  imple01-3060:
    runtime_profile: local_llamacpp_3060
    model_path: ${MODEL_ROOT_GGUF}/<model>.gguf
    context: 32768
    n_gpu_layers: 99
    cache_type_k: q8_0
    cache_type_v: q8_0
    flash_attn: "on"
    lifecycle_policy: stop_after_step
    clients:
      opencode: true
```

The final fields must match the current Model Allocator schema and the chosen model architecture.

Do not use `n_cpu_moe` for a dense model.

Do not use `n_gpu_layers` as a substitute for MoE expert offload.

### 8.2 Runtime profile example

```yaml
runtime_profiles:
  local_llamacpp_3060:
    backend: llama_cpp
    server_bin_env: LLAMA_SERVER_BIN
```

Machine-specific values belong in environment variables or Machine Profile configuration:

```bash
LLAMA_SERVER_BIN=/path/to/llama-server
MODEL_ROOT_GGUF=/path/to/models
```

Reusable configuration must not hardcode `/home/svend/...` paths.

---

## 9. OpenCode Client Handling

The initial client is OpenCode.

The worker must respect the existing Model Allocator behavior:

```text
OpenCode TUI does not reliably honor --model
```

Therefore the worker must use:

```bash
model-allocator render-config \
  --role <role> \
  --client opencode \
  --output <execution-specific-opencode.json>
```

before launching OpenCode when the current allocator integration requires role-based rendering.

The generated configuration must be merged atomically and preserve existing keys such as:

```text
$schema
permission
mcp
other providers
```

The worker must then obtain the tmux-safe launch command through:

```bash
model-allocator run \
  --role <role> \
  --client opencode
```

or an alias-based equivalent if added explicitly for LightWorker.

### 9.1 Tmux ownership

Model Allocator does not own tmux.

LightWorker must:

1. create the execution-specific tmux session
2. obtain the tmux-safe shell command from Model Allocator
3. start that command inside the tmux session
4. wait for client readiness
5. inject the compiled handoff
6. monitor the session
7. stop only the assigned session

---

## 10. Worker Deployment Model

## 10.1 Main RTX 5090 machine

The main machine hosts:

```text
DPMtF-WebUI
DPMtF database
Job Queue
BridgeV002
scheduler
governance source
checkpoint store
Father-side Model Allocator proxy and UI
authoritative project repositories
```

## 10.2 RTX 3060 LightWorker

The worker hosts:

```text
DPMtF-LightWorker
Model Allocator core
worker allocator configuration
Python
Git
tmux
Tailscale
OpenCode
Ollama and/or llama.cpp
selected local model files
repository mirrors
disposable worktrees
worker logs
temporary result artifacts
```

## 10.3 Components not required on the worker

The worker must not require:

```text
DPMtF-WebUI
a second dpmtf.db
the Father Job Queue scheduler
Bridge Setup UI
Model Allocator UI
all Father repositories
complete flow configuration
complete governance source tree
Claude Code skills unrelated to the assigned client
```

---

## 11. Proposed Repository Structure

```text
DPMtF-LightWorker/
├── src/
│   └── dpmtf_lightworker/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── models.py
│       ├── api_client.py
│       ├── worker_loop.py
│       ├── execution_runner.py
│       ├── envelope_validator.py
│       ├── heartbeat.py
│       ├── event_reporter.py
│       ├── repository_manager.py
│       ├── worktree_manager.py
│       ├── tmux_adapter.py
│       ├── allocator_adapter.py
│       ├── opencode_adapter.py
│       ├── result_collector.py
│       ├── patch_builder.py
│       ├── cleanup.py
│       └── errors.py
├── config/
│   └── lightworker.example.yaml
├── scripts/
│   ├── install.sh
│   ├── preflight.sh
│   ├── start.sh
│   ├── stop.sh
│   └── uninstall.sh
├── systemd/
│   └── dpmtf-lightworker.service
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── pyproject.toml
├── .env.example
├── README.md
└── GOAL.md
```

---

## 12. Worker Configuration

Example:

```yaml
worker:
  id: svend3060
  display_name: RTX 3060 LightWorker
  max_parallel_executions: 1
  poll_interval_seconds: 10
  heartbeat_interval_seconds: 15

father:
  base_url: http://main5090:9130
  request_timeout_seconds: 30

network:
  type: tailscale
  expected_father_host: main5090

allocator:
  command: model-allocator
  client: opencode
  config_root: ${MODEL_ALLOCATOR_CONFIG_ROOT}

paths:
  repository_root: ${LIGHTWORKER_REPOSITORY_ROOT}
  worktree_root: ${LIGHTWORKER_WORKTREE_ROOT}
  artifact_root: ${LIGHTWORKER_ARTIFACT_ROOT}
  log_root: ${LIGHTWORKER_LOG_ROOT}
  opencode_config_root: ${LIGHTWORKER_OPENCODE_CONFIG_ROOT}

retention:
  successful_worktree_hours: 1
  failed_worktree_days: 7
  artifact_days: 14
  log_days: 30
```

Committed configuration must contain environment-variable names rather than machine-specific absolute paths.

---

## 13. Role-Execution Envelope

Every remote execution must describe exactly one role step.

Conceptual schema:

```json
{
  "schema_version": "1",
  "execution_id": "EXEC-123-IMPLE01",
  "job_id": "JOB-123",
  "handoff_id": "HOFF-456",
  "attempt_id": "ATTEMPT-1",
  "flow_key": "strict_review",
  "step_key": "archi01-imple01",
  "source_role": "archi01",
  "target_role": "imple01",
  "worker_id": "svend3060",
  "model_source": "model_allocator",
  "model_alias": "imple01-3060",
  "client": "opencode",
  "repository": {
    "project_key": "trade-ui",
    "clone_url": "<read-only clone URL>",
    "base_commit": "<full commit SHA>"
  },
  "handoff": {
    "content": "<compiled handoff>",
    "governance_content": "<compiled role governance>",
    "expected_deliverable": "docs/dpmtf/403_IMPLEMENTATION.md"
  },
  "result_contract": {
    "mode": "patch_and_deliverable",
    "tests_required": true,
    "local_result_commit_required": true
  }
}
```

The worker must reject envelopes that:

* assign more than one target role
* use an unsupported schema
* use a different worker ID
* use a `model_source` other than `model_allocator` in V1
* omit the stable logical alias
* request an unsupported client
* omit the exact base commit
* contain unsafe repository or deliverable paths
* contain arbitrary shell pipelines

---

## 14. Model Allocator Execution Sequence

For each claimed role execution, LightWorker must follow this conceptual sequence.

### 14.1 Preflight

```bash
model-allocator preflight \
  --role <target-role> \
  --client <client>
```

The preflight must verify the role/client/runtime combination.

### 14.2 Validate the selected alias

```bash
model-allocator validate \
  --alias <model-alias> \
  --client <client>
```

Execution must stop before prompt injection when validation returns an error.

### 14.3 Render client configuration

For OpenCode:

```bash
model-allocator render-config \
  --role <target-role> \
  --client opencode \
  --output <execution-specific-opencode.json>
```

### 14.4 Start or acquire runtime

Runtime start and lease behavior must follow the integration contract selected for LightWorker.

The worker must not directly call:

```text
ollama start/stop
llama-server
kill
```

when those operations belong to the allocator adapter.

### 14.5 Obtain client launch command

```bash
model-allocator run \
  --role <target-role> \
  --client <client>
```

The output is treated as the allocator-produced tmux-safe shell command.

### 14.6 Start inside worker-local tmux

LightWorker starts the returned command in the assigned execution-specific tmux session.

### 14.7 Release runtime

After the role result is durably reported, lifecycle release must respect:

```text
persistent
stop_after_step
shared_runtime
cloud_noop
```

The worker must not unconditionally stop an alias shared by another lease.

---

## 15. Model Lease Integration

Model Allocator README identifies `LeaseRegistry` as the reference-counted lifecycle mechanism used by DPMtF Father.

The LightWorker integration must define one authoritative lease design before implementation.

Acceptable designs:

### Option A — Father-owned lease

Father acquires the alias lease before making the role execution available and releases it after durable result handling.

The worker invokes runtime operations under the Father-issued lease identity.

### Option B — Worker-local lease adapter

LightWorker acquires and releases a worker-local allocator lease using:

```text
job_id
handoff_id
execution_id
alias
```

This requires an explicit supported interface and must not duplicate Father ownership.

### V1 requirement

Phase 0 must determine which design fits the current `LeaseRegistry` implementation.

Until that decision is made, LightWorker must not implement unconditional:

```bash
model-allocator stop --alias <alias>
```

A stop that ignores shared runtime ownership is prohibited.

---

## 16. Simplified Git Model

## 16.1 Exact base commit

Father provides a full commit SHA.

The worker must use exactly that commit.

It must not substitute:

```text
latest main
remote HEAD
latest local commit
an inferred revision
```

## 16.2 Repository mirror

The worker may keep one reusable bare or mirror clone for each assigned project:

```text
${LIGHTWORKER_REPOSITORY_ROOT}/trade-ui.git
```

The mirror is a cache, not an authoritative working repository.

## 16.3 Disposable worktree

Each role execution receives a unique worktree:

```text
${LIGHTWORKER_WORKTREE_ROOT}/EXEC-123-IMPLE01
```

The worktree is created from the exact `base_commit`.

## 16.4 Local result branch

Patch-producing executions may use a local-only branch:

```text
dpmtf-local/EXEC-123-IMPLE01/ATTEMPT-1
```

The worker must not automatically push this branch.

## 16.5 Local result commit

The local result commit exists to provide:

* stable diff generation
* reproducibility
* binary patch generation
* audit metadata
* failure diagnostics

Example commit message:

```text
DPMtF LightWorker execution EXEC-123-IMPLE01

Job: JOB-123
Handoff: HOFF-456
Role: imple01
Attempt: ATTEMPT-1
Worker: svend3060
Alias: imple01-3060
Client: opencode
```

## 16.6 No Git push requirement

The worker should use read-only repository access in V1.

It must not require permission to:

```text
push
merge
force-push
create pull requests
modify protected branches
```

## 16.7 Father remains authoritative

Father is responsible for:

1. confirming the expected base commit
2. validating the returned patch or deliverable
3. applying the result
4. running authoritative checks
5. creating the authoritative commit
6. recording the checkpoint
7. advancing the flow

---

## 17. Result Modes

### 17.1 Patch

Used for code-changing roles.

The worker returns:

```text
binary-safe Git patch
base commit
local result commit
patch checksum
test summary
logs
```

### 17.2 Deliverable only

Used for roles that primarily produce a defined document or verdict.

The worker returns:

```text
expected deliverable
deliverable checksum
review/check summary
logs
```

### 17.3 Patch and deliverable

Used for implementers that modify code and produce the required DPMtF convention file.

---

## 18. Tmux Execution

Each role execution must use a unique worker-local tmux session.

Recommended name:

```text
dpmtf-<target-role>-<short-execution-id>
```

Example:

```text
dpmtf-imple01-123
```

The tmux adapter must support:

* session creation
* collision detection
* execution-specific working directory
* allocator-produced command launch
* readiness detection
* multiline handoff injection
* long-prompt injection
* pane capture
* health checks
* targeted termination
* targeted cleanup

The worker must not terminate or reuse unrelated tmux sessions.

---

## 19. OpenCode Permissions

The worker's execution-specific OpenCode configuration must preserve required permission blocks when `render-config` merges model and provider configuration.

V1 must define the minimum approved external paths required by a remote role.

Preferred design:

```text
worktree
    used as OpenCode cwd

execution artifact directory
    explicitly allowlisted only when required

compiled handoff file
    stored inside the execution worktree or approved execution directory
```

The LightWorker should avoid requiring direct access to Father paths such as:

```text
Father bridge directory
Father governance directory
Father project root
```

Father should send the required compiled content in the execution envelope.

---

## 20. Polling and Claim Protocol

V1 uses worker-initiated polling over Tailscale.

Conceptual Father endpoints:

```text
POST /api/lightworkers/register
POST /api/lightworkers/heartbeat
GET  /api/lightworkers/{worker_id}/executions/next
POST /api/lightworkers/executions/{execution_id}/claim
POST /api/lightworkers/executions/{execution_id}/heartbeat
POST /api/lightworkers/executions/{execution_id}/events
POST /api/lightworkers/executions/{execution_id}/complete
POST /api/lightworkers/executions/{execution_id}/fail
```

The protocol must support:

* worker identity
* worker capabilities
* static worker matching
* atomic role-execution claim
* attempt-bound heartbeat
* cancellation
* idempotent completion
* idempotent failure reporting
* duplicate-execution protection

---

## 21. Worker State Model

```text
RECEIVED
VALIDATING_ENVELOPE
CLAIMED
PREPARING_REPOSITORY
PREPARING_WORKTREE
ALLOCATOR_PREFLIGHT
ALLOCATOR_VALIDATING
RENDERING_CLIENT_CONFIG
ACQUIRING_RUNTIME
CREATING_TMUX
STARTING_CLIENT
INJECTING_HANDOFF
RUNNING_ROLE
COLLECTING_RESULT
BUILDING_PATCH
REPORTING_RESULT
RELEASING_RUNTIME
ROLE_EXECUTION_COMPLETED
ROLE_EXECUTION_FAILED
CANCELLED
CLEANING_UP
```

The state model must not include:

```text
ADVANCING_CHAIN
STARTING_NEXT_ROLE
COMPLETING_DPMTF_JOB
```

---

## 22. Structured Events

Minimum event types:

```text
WORKER_REGISTERED
ROLE_EXECUTION_RECEIVED
ROLE_EXECUTION_CLAIMED
REPOSITORY_READY
WORKTREE_CREATED
ALLOCATOR_PREFLIGHT_STARTED
ALLOCATOR_PREFLIGHT_PASSED
ALIAS_VALIDATED
CLIENT_CONFIG_RENDERED
RUNTIME_ACQUIRED
RUNTIME_READY
TMUX_SESSION_CREATED
CLIENT_STARTED
HANDOFF_INJECTED
ROLE_RUNNING
DELIVERABLE_DETECTED
TESTS_COMPLETED
LOCAL_RESULT_COMMITTED
PATCH_CREATED
RESULT_REPORTED
RUNTIME_RELEASED
ROLE_EXECUTION_COMPLETED
ROLE_EXECUTION_FAILED
CLEANUP_COMPLETED
```

Every event must contain:

```text
worker_id
execution_id
attempt_id
target_role
model_alias
client
event_type
timestamp
structured payload
```

Raw pane output must not be the only source of execution state.

---

## 23. Completion Result

Example:

```json
{
  "schema_version": "1",
  "execution_id": "EXEC-123-IMPLE01",
  "job_id": "JOB-123",
  "handoff_id": "HOFF-456",
  "attempt_id": "ATTEMPT-1",
  "worker_id": "svend3060",
  "target_role": "imple01",
  "model_source": "model_allocator",
  "model_alias": "imple01-3060",
  "client": "opencode",
  "status": "role_execution_completed",
  "result_mode": "patch_and_deliverable",
  "repository": {
    "base_commit": "<base SHA>",
    "local_result_commit": "<local SHA>"
  },
  "patch": {
    "format": "git-binary-patch",
    "artifact_reference": "<reference>",
    "sha256": "<checksum>"
  },
  "deliverable": {
    "path": "docs/dpmtf/403_IMPLEMENTATION.md",
    "artifact_reference": "<reference>",
    "sha256": "<checksum>"
  },
  "checks": {
    "exit_code": 0,
    "summary": "<summary>"
  },
  "allocator": {
    "runtime_profile": "<resolved runtime profile>",
    "backend": "<resolved backend>",
    "lifecycle_policy": "stop_after_step"
  }
}
```

Worker completion is not equivalent to Father acceptance.

Father performs authoritative validation.

---

## 24. Failure Categories

```text
INVALID_EXECUTION_ENVELOPE
UNSUPPORTED_SCHEMA_VERSION
WORKER_MISMATCH
UNSUPPORTED_MODEL_SOURCE
UNSUPPORTED_CLIENT
REPOSITORY_FETCH_FAILED
BASE_COMMIT_NOT_FOUND
WORKTREE_CREATION_FAILED
ALLOCATOR_NOT_AVAILABLE
ALLOCATOR_PREFLIGHT_FAILED
ALIAS_VALIDATION_FAILED
CLIENT_INCOMPATIBLE
CLIENT_CONFIG_RENDER_FAILED
RUNTIME_START_FAILED
RUNTIME_HEALTH_FAILED
TMUX_START_FAILED
CLIENT_START_FAILED
HANDOFF_INJECTION_FAILED
ROLE_EXECUTION_TIMEOUT
ROLE_SESSION_TERMINATED
DELIVERABLE_MISSING
TESTS_FAILED
LOCAL_COMMIT_FAILED
PATCH_GENERATION_FAILED
RESULT_REPORT_FAILED
RUNTIME_RELEASE_FAILED
WORKER_INTERRUPTED
CANCELLED_BY_FATHER
INTERNAL_WORKER_ERROR
```

Failure results must include:

* execution ID
* attempt ID
* target role
* model alias
* client
* failure category
* last state
* retryability
* log reference
* allocator output summary when relevant

---

## 25. Preflight

`scripts/preflight.sh` must verify:

```text
Python version
Git
tmux
Tailscale status
Father reachability
worker configuration
worker authentication
Model Allocator command
allocator configuration loading
selected client installation
selected backend availability
GPU visibility
VRAM information
worker data directories
repository read access
one-execution limit
```

For configured test aliases, preflight should use:

```bash
model-allocator preflight --role <role> --client <client>
model-allocator validate --alias <alias> --client <client>
```

Preflight must:

* be non-destructive
* support JSON output
* return non-zero on blocking failure
* not download models
* not reveal secrets

---

## 26. Installation Goal

The worker installer should install or verify:

```text
DPMtF-LightWorker
Python virtual environment
Git
tmux
Tailscale
Model Allocator core
selected backend runtime
selected client
worker directories
systemd service
```

The installer must not install:

```text
DPMtF-WebUI
a second dpmtf.db
Father scheduler
Model Allocator UI
all models
all repositories
unrelated client adapters
ONYX unless explicitly required
```

Model files and runtime-specific dependencies may use separate explicit installation procedures.

---

## 27. Security

Requirements:

* communication occurs over the private Tailscale network
* worker identity is authenticated
* Tailscale membership is not the sole authorization mechanism
* repository access is read-only by default
* secrets are referenced by environment-variable name
* API keys are never written into committed YAML
* machine-specific absolute paths are not committed
* arbitrary shell pipelines are not accepted from Father
* only the allocator-produced launch command may be executed as the client launch command
* envelope paths must remain below configured roots
* path traversal is rejected
* symlink escapes are rejected
* logs redact tokens and credentials
* cleanup affects only execution-owned resources

---

## 28. Non-Goals for V1

V1 must not implement:

* complete-flow execution on LightWorker
* worker-side chain advancement
* dynamic worker selection
* load balancing
* multiple concurrent role executions
* distributed DPMtF databases
* a second Father
* worker-side alias authority
* worker-side lifecycle reimplementation
* direct hardcoded Ollama commands
* direct hardcoded llama.cpp commands
* automatic Git push
* automatic Git merge
* pull-request creation
* arbitrary remote shell execution
* shared NFS or SSHFS worktrees
* public worker endpoints
* Kubernetes
* transparent worker failover
* live model-context migration
* ONYX integration unless selected by a later scope
* MCP serving unless selected by a later scope

---

# Test Goals

## 29. Default Test Environment

The default test suite must run without:

```text
real GPU
real Tailscale
real Father
real Model Allocator backend
real OpenCode
internet access
```

Use fakes for:

```text
Father API
Model Allocator CLI
tmux
OpenCode
backend runtime
Git remote
```

---

## 30. Terminology and Boundary Tests

Verify that:

* Father selects the target role
* Father selects `model_source`
* Father selects the stable logical alias
* LightWorker does not select a different alias
* Model Allocator resolves alias to runtime profile and backend
* backend adapter owns runtime commands
* LightWorker owns worker-local tmux
* Model Allocator does not create tmux sessions
* LightWorker does not advance the flow
* one envelope contains exactly one target role

---

## 31. Model Allocator Command Tests

Verify correct use of:

```text
resolve
validate
status
start
stop
unload
run
render-config
preflight
```

Tests must verify:

* argument construction is safe
* output is parsed safely
* command timeouts are bounded
* malformed JSON or output fails safely
* stderr is captured
* aliases are not replaced by concrete model names
* backend-specific commands are not constructed by LightWorker
* lifecycle policy is preserved
* shared runtimes are not stopped unconditionally

---

## 32. Alias Validation Tests

Verify:

* valid alias/client combination passes
* missing alias fails
* incompatible client fails
* wrong worker alias fails according to routing policy
* unresolved runtime profile fails
* missing backend environment variable fails
* missing model file fails for llama.cpp
* backend health failure is structured
* context is taken from allocator resolution
* LightWorker does not infer context size

---

## 33. OpenCode Configuration Tests

Verify:

* `render-config` is called before OpenCode launch
* output is written to an execution-specific path
* write is atomic
* existing permission block is preserved
* existing MCP block is preserved
* top-level model field is set
* provider block is present
* unsupported rendered output fails
* LightWorker does not rely on `opencode --model`
* execution-specific config does not modify unrelated role configs

---

## 34. Tmux Command Tests

Verify:

* `model-allocator run` output is used as the client command
* LightWorker does not invent the OpenCode model argument
* command is launched in the disposable worktree
* unique tmux session is created
* long handoff is injected without truncation
* handoff is injected once
* tmux collision is handled safely
* unrelated tmux sessions are untouched
* session failure is observable
* cleanup terminates only the assigned session

---

## 35. Lifecycle and Lease Tests

Verify:

* runtime is validated before use
* runtime readiness is checked
* `stop_after_step` releases after durable result reporting
* `persistent` remains running
* `shared_runtime` is not stopped by one role
* `cloud_noop` performs no local lifecycle action
* an alias shared by another lease is not stopped
* release failure is reported
* unrelated runtime processes remain untouched
* no direct backend stop command is issued outside the allocator adapter

---

## 36. Git Tests

Verify:

* repository mirror uses read-only access
* exact base commit is required
* disposable worktree is created from that commit
* each execution gets a unique worktree
* local branch is not pushed
* local result commit descends from base commit
* binary-safe patch can be generated
* patch passes `git apply --check`
* deliverable-only mode does not require a result commit
* cleanup does not remove repository mirrors
* cleanup is idempotent

---

## 37. Role Boundary Tests

Verify that LightWorker:

* executes one target role
* does not request the next role
* does not call `_advance_chain`
* does not mark the complete job completed
* reports `role_execution_completed`
* returns control after durable reporting
* rejects multi-role envelopes
* requires a new execution ID for later work

---

## 38. Polling, Claim and Heartbeat Tests

Verify:

* registration reports worker capabilities
* idle worker polls Father
* only matching static assignments are offered
* claim is atomic
* failed claim causes no execution
* busy worker does not claim another role
* duplicate poll response does not duplicate execution
* execution heartbeat includes attempt ID
* heartbeat stops after terminal state
* authentication errors remain observable
* retries are bounded

---

## 39. Security Tests

Verify:

* arbitrary shell command fields are rejected
* path traversal is rejected
* symlink escape is rejected
* worker mismatch is rejected
* unsupported Father host is rejected
* secrets are redacted
* committed example config contains no secret
* committed config uses environment-variable names
* reusable code contains no hardcoded `/home/svend` path
* worker never pushes
* worker never merges
* only execution-owned tmux and worktree resources are deleted

---

## 40. Fake End-to-End Test

Provide a default E2E test proving:

1. worker registers
2. one statically assigned role execution is offered
3. worker claims it
4. exact base commit is validated
5. disposable worktree is created
6. fake `model-allocator preflight` passes
7. fake `model-allocator validate` resolves the alias
8. fake `render-config` creates an OpenCode config
9. fake runtime becomes ready
10. fake `model-allocator run` returns a tmux-safe command
11. LightWorker launches it in fake tmux
12. handoff is injected once
13. fake role produces changes and a deliverable
14. local result commit is created
15. binary-safe patch is generated
16. completion is reported
17. runtime is released according to lifecycle policy
18. no next role is started
19. no push or merge occurs
20. cleanup occurs after durable reporting

---

## 41. Real RTX 3060 End-to-End Test

Provide an opt-in E2E test proving:

1. `main5090` and `svend3060` communicate through Tailscale
2. LightWorker is registered
3. worker-local Model Allocator is installed
4. worker-local Machine Profile loads
5. assigned alias validates with the selected client
6. Father routes one role step to `svend3060`
7. exact repository commit is fetched
8. disposable worktree is created
9. OpenCode config is rendered
10. backend runtime starts through Model Allocator
11. runtime status becomes healthy
12. Model Allocator returns the client launch command
13. LightWorker starts the command in tmux
14. handoff is injected exactly once
15. assigned role produces the expected result
16. patch or deliverable is returned
17. Father validates the result
18. Father independently advances the flow
19. LightWorker does not start the next role
20. runtime lifecycle policy is respected
21. tmux and worktree cleanup succeeds

---

## 42. Acceptance Criteria

V1 is complete when:

* [ ] `DPMtF-LightWorker` installs independently on `svend3060`.
* [ ] Worker and Father communicate over Tailscale.
* [ ] Worker-local Model Allocator is installed and validated.
* [ ] Worker Machine Profile contains machine-specific configuration.
* [ ] No reusable code hardcodes machine-specific absolute paths.
* [ ] One role or step can select `model_source = model_allocator`.
* [ ] One role or step can select a stable logical alias routed to `svend3060`.
* [ ] LightWorker executes one role at a time.
* [ ] Alias validation occurs before execution.
* [ ] Model Allocator owns runtime lifecycle.
* [ ] Backend adapters own concrete runtime commands.
* [ ] LightWorker owns worker-local tmux orchestration.
* [ ] OpenCode model configuration is produced through `render-config`.
* [ ] The OpenCode launch command comes from `model-allocator run`.
* [ ] Every execution uses an exact base commit.
* [ ] Every execution uses a disposable worktree.
* [ ] Patch-producing roles return a binary-safe patch.
* [ ] Deliverable roles return the expected deliverable.
* [ ] Worker requires no Git push access.
* [ ] Father validates and applies the result.
* [ ] Father controls checkpoints and chain advancement.
* [ ] LightWorker never starts the next role.
* [ ] Lifecycle policies are respected.
* [ ] Shared aliases are not stopped unconditionally.
* [ ] Default tests run without GPU or network.
* [ ] A real RTX 3060 execution succeeds.
* [ ] Existing local bridgeV002 execution remains backward compatible.

---

## 43. Recommended Implementation Phases

### Phase 0 — Contract alignment

* Confirm the exact Machine Profile schema.
* Confirm how worker-specific allocator config is loaded.
* Confirm how aliases are deployed to `svend3060`.
* Confirm the Father-side static worker mapping.
* Confirm the authoritative lease design.
* Confirm whether `model-allocator run` requires role mapping or should gain an alias-based form.
* Confirm execution-specific `OPENCODE_CONFIG`.
* Spike Tailscale polling.
* Spike remote disposable worktree.
* Spike worker-local allocator validation and OpenCode launch.

### Phase 1 — Worker core

* Implement configuration.
* Implement registration.
* Implement health.
* Implement polling.
* Implement claim.
* Implement heartbeat.
* Implement state machine.
* Implement structured events.
* Add unit tests.

### Phase 2 — Model Allocator adapter

* Implement bounded CLI invocation.
* Implement `preflight`.
* Implement `validate`.
* Implement `render-config`.
* Implement `run`.
* Implement runtime status.
* Implement lifecycle release.
* Add fake allocator tests.

### Phase 3 — Git isolation

* Implement repository mirrors.
* Implement exact commit validation.
* Implement disposable worktrees.
* Implement local result commits.
* Implement patch generation.
* Implement retention and cleanup.
* Add Git integration tests.

### Phase 4 — Tmux and OpenCode

* Implement unique tmux sessions.
* Launch allocator-produced command.
* Detect client readiness.
* Inject compiled handoff.
* Capture pane output.
* Handle timeout and cancellation.
* Add tmux and client tests.

### Phase 5 — Father integration

* Add LightWorker registration endpoints.
* Add remote role-execution records or equivalent state.
* Add static worker selection.
* Add claim and heartbeat endpoints.
* Add artifact transfer.
* Add Father-side patch/deliverable validation.
* Preserve existing checkpoints and chain advancement.

### Phase 6 — Real-machine validation

* Install LightWorker on `svend3060`.
* Install and configure Model Allocator.
* Validate one machine-bound alias.
* Execute one harmless role.
* Return patch or deliverable.
* Verify Father validation and next-role routing.
* Verify lifecycle and cleanup.
* Document operational procedures.

---

## 44. Final System Boundary

```text
bridgeV002 / DPMtF Father
    owns role, step, flow order, tmux policy,
    deliverable routing and model_alias selection

Model Allocator
    owns alias resolution, validation, context/offload,
    client compatibility and runtime lifecycle

Backend adapters
    own concrete model-runtime commands

DPMtF-LightWorker
    owns worker-local execution, disposable worktree,
    worker-local tmux and result transport

DPMtF Father repository
    remains authoritative
```

The LightWorker must remain a lightweight execution component.

It must reuse Model Allocator terminology and interfaces rather than introducing a parallel remote model-management system.
