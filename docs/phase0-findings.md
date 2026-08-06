# Phase 0 — Contract Alignment Findings

> Source: DPMtF-LightWorker / `docs/phase0-findings.md`
> Phase 0 of `GOAL.md §43` requires seven contract questions to be answered
> against the real systems on this machine before any LightWorker code is
> written. This document records what each answer is, with the evidence that
> backs it. Every `Question.` paragraph paraphrases GOAL.md; the evidence is
> the command output or file quote immediately under it.
>
> The allocator config files consulted below are the live ones (`models.yaml`,
> `roles.yaml`, `runtime_profiles.yaml`), **not** their `.example` siblings.
> The live file is always the one without a suffix; `models.yaml.bak` was
> ignored. The Father's live profile is `machine.local.json`; the
> `machine.ai-pc.example.json` and `machine.local.example.json` files were
> read for schema reference only.

## 1. Machine Profile schema (§2.2, §43)

**Question.** What fields does the Model Allocator actually *read* from a
Machine Profile, as opposed to what an example file happens to contain? The
Father has machine-profile getters; the allocator reads its own YAML
configuration. Where does each field come from?

**Answer.** The Model Allocator does not read any Machine Profile today. The
allocator's only on-disk inputs are `models.yaml`, `roles.yaml` and
`runtime_profiles.yaml`, loaded by `model_allocator/config_loader.py::load_config`,
which only resolves `${ENV}` references — it has no JSON-file loader for a
Machine Profile. The `machine.example.json` shipped next to the allocator
defines a `machine_id`/`machine_name`/`primary_gpu`/`available_gpus`/`notes`
shape but no allocator source code reads it. The README's "Configuration
source of truth" list (item 1: `machine.json` / Machine Profile) is
aspirational — it does not match `config_loader.py`.

The Father side, by contrast, *does* read Machine Profile JSON through
`DPMtF-WebUI/config.py::get_machine_profile()` and
`get_machine_profile_metadata()`, which parse `schema_version`, `name`,
`description`, `capabilities`, and a summarised `providers` block. The
`ai-pc.example.json` shape that is consumed in practice (and confirmed by
`scripts/system_healthcheck.py::run_section_tmux` and `run_section_ollama`)
is: `capabilities.{tmux,cuda,local_ollama,cloud_models,telegram_bridge,cron}`,
`paths.{project_root,bridge_dir,trade_inbox,log_dir,exports_dir}`,
`binaries.{python,tmux,ollama,claude,opencode,freebuff}`,
`runtimes.{claude,opencode,freebuff}` (each with `binary_ref`,
`default_env`, `extra_args`), `providers.{local_ollama,cloud_ollama,openrouter,anthropic_direct,opencode_builtin}` (each with `available`,
`endpoint`, `auth_token`/`env_key`, `models`), `ports`, and `checks`
(declarative, not read in code). The example also carries a top-level
`watchdog` block (used by `chain_watchdog.py`), which the allocator does
not see.

For a worker that needs to *resolve* an alias and *launch a client*, the
fields that are read today are all allocator-side YAML and environment
variables. A Machine Profile on the worker would only matter if the
Father's getters are imported by worker code (they are not in the grant),
or if `system_healthcheck.py`-style checks are reused.

**Evidence.**

```
$ grep -n "machine" /home/svend/model-allocator/src/model_allocator/config_loader.py
(no matches)

$ grep -n "machine.json\|machine_profile\|machine.example.json" \
      /home/svend/model-allocator/src/model_allocator/*.py
(no matches)

$ python3 -c "
import sys; sys.path.insert(0, '/home/svend/model-allocator/src')
from model_allocator.config_loader import load_config
cfg = load_config('/home/svend/model-allocator')
print('top-level keys:', sorted(cfg.keys()))
"
top-level keys: ['models', 'roles', 'runtime_profiles']
```

```
$ sed -n '75,102p' /home/svend/model-allocator/src/model_allocator/config_loader.py
def load_config(config_dir: Path | str | None = None) -> dict:
    """Load allocator-local configuration.

    Loads models, roles, and runtime_profiles files from *config_dir*.
    If *config_dir* is omitted, the current working directory is used.
    Files are searched in this order (first match wins):
      models.yaml / models.json
      roles.yaml / roles.json
      runtime_profiles.yaml / runtime_profiles.json
    """
    config_dir = Path(config_dir) if config_dir else Path.cwd()

    def load(name: str) -> dict:
        for ext in (".yaml", ".yml", ".json"):
            path = config_dir / f"{name}{ext}"
            if path.exists():
                return resolve_env(load_file(path)) or {}
        return {}

    models = load("models").get("models", {})
    runtime_profiles = load("runtime_profiles").get("runtime_profiles", {})
    roles = load("roles").get("roles", {})

    return {
        "models": models,
        "runtime_profiles": runtime_profiles,
        "roles": roles,
    }
```

```
$ sed -n '188,263p' /home/svend/DPMtF-WebUI/config.py
def get_machine_profile() -> dict:
    """Load active Machine Profile or return empty dict.

    Machine Profile is optional in Phase 1.
    ...
    """
    profile_path = get_machine_profile_path()
    if not os.path.exists(profile_path):
        return {}
    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def get_machine_profile_metadata() -> dict:
    ...
    result["name"] = profile.get("name")
    result["description"] = profile.get("description")
    result["schema_version"] = profile.get("schema_version")
    result["capabilities"] = profile.get("capabilities", {})
    ...
    providers = profile.get("providers", {})
    for pkey, pdata in providers.items():
        result["providers"][pkey] = {
            "available": pdata.get("available", False),
            "model_count": len(pdata.get("models", [])),
        }
    return result
```

```
$ sed -n '155,166p' /home/svend/model-allocator/README.md
Model Allocator resolves from (no source is replaced — all are combined):

1. `machine.json` / Machine Profile
2. environment variables (secrets referenced by **name**, never inlined)
3. database values (Father `dpmtf.db`)
4. allocator config files: `models.yaml`, `roles.yaml`, `runtime_profiles.yaml`
5. role/step selections from bridgeV002
```

## 2. How worker-local allocator config is loaded (§7.1, §7.2)

**Question.** Of `models.yaml`, `roles.yaml`, `runtime_profiles.yaml`, the
Machine Profile and environment variables, which are **required** for a
worker that resolves exactly one alias, and which are not? "Required" means
the thing fails without it; show what fails.

**Answer.** `models.yaml` is the only required file: an alias lookup is
driven by an entry in `models[alias_name]`. Without the alias entry,
`Resolver.resolve_alias` raises `ResolutionError("Alias '<name>' not found")`,
which surfaces as a `cmd_run` exit code 1 and an `ERROR:` line on stderr.
The other files are required transitively: every alias declares a
`runtime_profile`, and `Resolver.resolve_alias` raises `Runtime profile
'...' not found for alias '...'` if the profile is missing from
`runtime_profiles.yaml`. `roles.yaml` is only required for `run`,
`render-config` and `preflight`, which accept a `role_key` and look it up
in `roles[role_key]` — `start`, `stop`, `status`, `validate`, `unload` and
`invoke` only need `models.yaml` + `runtime_profiles.yaml`. `Machine
Profile` is irrelevant for the allocator today (Q1). Environment variables
are required only where the rendered config references them: ollama/openai
backends read `OLLAMA_BASE_URL`, `OPENROUTER_API_KEY`, `MINIMAX_API_KEY`;
llama.cpp reads `LLAMA_SERVER_BIN` and `MODEL_ROOT_GGUF`; the opencode
adapter reads `OPENCODE_BIN` and `OPENCODE_ROLES_CONFIG_BASE`.

**Evidence.**

```
$ grep -n "raise ResolutionError" /home/svend/model-allocator/src/model_allocator/resolver.py
27:        if alias_name not in models:
28:            raise ResolutionError(f"Alias '{alias_name}' not found")
31:        if not profile_name:
32:            raise ResolutionError(f"Alias '{alias_name}' has no runtime_profile")
34:        if profile_name not in profiles:
35:            raise ResolutionError(f"Runtime profile '{profile_name}' not found for alias '{alias_name}'")
60:        if not role:
61:            raise ResolutionError(f"Role '{role_key}' not found")
65:        if not alias_name:
66:            raise ResolutionError(f"No alias configured for role '{role_key}' and client '{client}'")
```

```
$ cd /home/svend/model-allocator && python3 -m model_allocator \
    --config-dir /tmp/empty-cfg validate --alias ghost --client opencode 2>&1
ERROR: Alias 'ghost' not found
$ echo $?
1
```

(The `/tmp/empty-cfg` directory contains no `models.yaml`; `load_config`
returns an empty `{"models": {}, "runtime_profiles": {}, "roles": {}}`
mapping, and the resolver fails on the missing alias.)

```
$ grep -n "sub.add_parser" /home/svend/model-allocator/src/model_allocator/cli.py | grep -E "validate|start|stop|status|run|render|preflight|unload|invoke"
644:    p_validate = sub.add_parser("validate", help="Check whether an alias is usable for a client")
647:    p_validate.add_argument("--alias", required=True, help="Logical alias name")
656:    p_status = sub.add_parser("status", help="Report backend/runtime status for an alias")
657:    p_status.add_argument("--alias", required=True, help="Logical alias name")
688:    p_start = sub.add_parser("start", help="Warm up the backend runtime for an alias")
689:    p_start.add_argument("--alias", required=True, help="Logical alias name")
693:    p_stop = sub.add_parser("stop", help="Stop the backend runtime for an alias")
694:    p_stop.add_argument("--alias", required=True, help="Logical alias name")
697:    p_unload = sub.add_parser("unload", help="Free model memory for an alias")
698:    p_unload.add_argument("--alias", required=True, help="Logical alias name")
702:    p_preflight = sub.add_parser("preflight", help="Resolve + validate + start + reachability check")
703:    p_preflight.add_argument("--role", required=True, help="Role key")
680:    p_run = sub.add_parser("run", help="Render the tmux-safe shell string for a role/client")
681:    p_run.add_argument("--role", required=True, help="Role key")
712:    p_render = sub.add_parser("render-config", help="Emit opencode.json content for a role/client")
713:    p_render.add_argument("--role", required=True, help="Role key")
```

(`--alias` subcommands resolve through `Resolver.resolve_alias`, which
needs only `models` + `runtime_profiles`. `--role` subcommands additionally
require `roles[role_key]`. `roles.yaml` is therefore required for the
client-launch path but not for backend-lifecycle commands.)

```
$ sed -n '211,224p' /home/svend/model-allocator/src/model_allocator/validator.py
def _validate_llama_cpp(self, resolved: dict, client: str, result: dict) -> None:
    try:
        adapter = llama_cpp_adapter.LlamaCppAdapter(resolved)
        server_bin = adapter.server_bin()
        if not os.path.isfile(server_bin):
            result["warnings"].append(f"llama-server binary not found: {server_bin}")
        model_path = adapter.model_path()
        if not os.path.isfile(model_path):
            result["warnings"].append(f"Model file not found: {model_path}")
```

(`LLAMA_SERVER_BIN` / `MODEL_ROOT_GGUF` are referenced by the llama.cpp
adapter when validating or starting a llama.cpp alias. With them unset,
validation surfaces `WARNING: llama-server binary not found: <empty>`,
not an error — but `start` then fails. The same shape applies to Ollama
/ OpenAI-compatible: missing API key is reported as a WARNING in
`_validate_openai_compatible`.)

## 3. How aliases reach `svend3060` (§7.3)

**Question.** §7.3 lists four mechanisms for getting an alias from Father to
a worker: (a) Git-managed allocator configuration, (b) rsync from Father to
worker, (c) installation script copying validated config, (d) dedicated
configuration deployment command. Which is workable *given how the
allocator loads config today*?

**Answer.** Today, the allocator loads YAML files from one directory —
`config_dir` (or the repo root, or CWD). There is no built-in deployment
mechanism and no remote-fetch capability. The only writable surface is
the CLI's `config` subcommand (`set-alias`, `delete-alias`, `set-role`,
`delete-role`), which validates and atomically writes entries; `set-alias`
requires the referenced `runtime_profile` to exist. There is no client
that pushes config from Father to a worker; `routers/bridge.py` exposes a
proxy for *this* machine (it shells out to the local allocator), not for
remote workers.

Of the four mechanisms, **(c) installation script copying validated config**
is the one that works with the current loader without further code, and
**(a) Git-managed allocator configuration** is workable in principle but
introduces a second authoritative repo to keep in sync. **(b) rsync** and
**(d) a dedicated deployment command** are workable as transport but do
not exist today and would need a new command. In all four cases the
worker side just needs `models.yaml` / `runtime_profiles.yaml` /
`roles.yaml` to be present at the `--config-dir` path; the allocator
makes no distinction between "Father-pushed" and "hand-installed"
content.

**Recommendation.** Use **(c)**, an installation-time copy of validated
allocator YAML into `${MODEL_ALLOCATOR_CONFIG_ROOT}`, plus a small
deployment script run by Father that re-copies whenever a `default_alias`
or `runtime_profile` changes. This reuses `config set-alias` validation
on the Father side (so an alias is never deployed broken), avoids a
parallel git repo, and matches what `start_coding.py` already does in
spirit (it shells out to the local allocator at the path
`config.get_project_path("model-allocator")/scripts/model-allocator`,
which the worker would mirror). Git-managed YAML is the right *fallback*
when an installation script is not run (V2.3 path already uses this for
`runtime_profiles.yaml` updates), but it should not be the primary path
because the worker has no mechanism to fetch from a remote git URL.

**Evidence.**

```
$ grep -n "config_writer\|set-alias\|set-role\|delete-alias\|delete-role" \
      /home/svend/model-allocator/src/model_allocator/cli.py | head -20
524:def _config_write(args: argparse.Namespace, action) -> int:
534:def cmd_config_set_alias(args: argparse.Namespace) -> int:
540:    return _config_write(args, lambda: config_writer.set_alias(_config_dir(args), args.name, definition))
543:def cmd_config_delete_alias(args: argparse.Namespace) -> int:
544:    return _config_write(args, lambda: config_writer.delete_alias(_config_dir(args), args.name))
547:def cmd_config_config_set_role  (sic — see file)
556:def cmd_config_delete_role(args: argparse.Namespace) -> int:
557:    return _config_write(args, lambda: config_writer.delete_role(_config_dir(args), args.name))
730:    c_set_alias = config_sub.add_parser("set-alias", help="Create/update an alias")
744:    c_del_role = config_sub.add_parser("delete-role", help="Delete a role")
```

```
$ sed -n '720,747p' /home/svend/model-allocator/src/model_allocator/cli.py
        help="Optional path to write opencode.json atomically (merges with existing file)",
    )
    p_render.set_defaults(func=cmd_render_config)

    p_config = sub.add_parser("config", help="Read/write allocator config (aliases, roles)")
    config_sub = p_config.add_subparsers(dest="config_command", required=True)

    c_show = config_sub.add_parser("show", help="Print full config (aliases, roles, profiles) as JSON")
    c_show.set_defaults(func=cmd_config_show)

    c_set_alias = config_sub.add_parser("set-alias", help="Create/update an alias")
    c_set_alias.add_argument("--name", required=True, help="Alias name")
    c_set_alias.add_argument("--json", required=True, help="Alias definition as a JSON object")
    c_set_alias.set_defaults(func=cmd_config_set_alias)

    c_del_alias = config_sub.add_parser("delete-alias", help="Delete an alias")
    c_del_alias.add_argument("--name", required=True, help="Alias name")
    c_del_alias.set_defaults(func=cmd_config_delete_alias)

    c_set_role = config_sub.add_parser("set-role", help="Create/update a role")
    c_set_role.add_argument("--name", required=True, help="Role name")
    c_set_role.add_argument("--json", required=True, help="Role definition as a JSON object")
    c_set_role.set_defaults(func=cmd_config_set_role)

    c_del_role = config_sub.add_parser("delete-role", help="Delete a role")
    c_del_role.add_argument("--name", required=True, help="Role name")
    c_del_role.set_defaults(func=cmd_config_delete_role)
```

(`config set-alias` is the only validated-write path the allocator
exposes. The transport to a worker — SSH, rsync, git, or a future
`--remote-config-dir` — is not provided by the allocator today.)

```
$ grep -n "rsync\|fetch.*yaml\|deploy.*config\|pull.*config" \
      /home/svend/model-allocator/src/model_allocator/*.py
(no matches)
```

```
$ sed -n '217,222p' /home/svend/DPMtF-WebUI/scripts/bridgeV002/start_coding.py
            model_allocator_path = os.path.join(
                config_mod.get_project_path("model-allocator"),
                "scripts",
                "model-allocator",
            )
```

(`start_coding.py` already finds the allocator by absolute path resolved
through `config.get_project_path("model-allocator")`, which the worker
side mirrors through `${MODEL_ALLOCATOR_CONFIG_ROOT}`.)

## 4. The Father-side static worker mapping (§8)

**Question.** §8 introduces `execution_target` as a LightWorker routing
concern. Confirm that `execution_target` is *not* in the `bridge_flow_steps`
or `bridge_roles` schema today, and that nothing in the codebase reads it.
Then: what would carry it, and what existing field must it not be confused
with? §8 specifically warns against conflating it with `runtime_profile`.

**Answer.** `execution_target` is not present in either schema. The
`bridge_flow_steps` columns are
`flow_key, step_key, from_role, to_role, deliverable_dir,
deliverable_pattern, pre_dispatch_script, post_dispatch_script,
error_msg, sort_order, is_active, rule_key, auto_chain_to_next,
validation_required, model_source, model_alias`. The `bridge_roles`
columns are `role_key, tmux_session, setup_script, teardown_script,
deliver_error_msg, is_active, created_at, updated_at, restart_policy,
governance_file, role_type, enter_command, config_dir,
primary_output_type, default_model_source, default_model_alias,
trade_mcp_push_mode, max_output_tokens, allocator_client,
fresh_session_command, workdir_mode`. A grep across `/home/svend/DPMtF-WebUI`
and `/home/svend/model-allocator` returns no matches for the literal
`execution_target` outside the example block in `GOAL.md §8` itself.

The existing field that must not be confused with `execution_target` is
`tmux_session` on `bridge_roles`. `runtime_profile` is a *backend*
property — which `runtime_profiles.yaml` profile an alias uses — and is
correctly named; `runtime_profile` lives on the allocator's
`models[alias]` entry, not in the Father database. `default_model_alias`
on `bridge_roles` and `model_alias` on `bridge_flow_steps` are *which
alias* a role uses; they pick the model, not the worker. None of those
identifies *where the role runs*. The field that comes closest today is
`tmux_session`, which is the *Father-side* tmux session a role attaches
to — it is host-local to the Father.

**Recommendation.** Add `execution_target` to `bridge_roles` as a
nullable TEXT column (`DEFAULT NULL` to keep existing rows valid), and
make the dispatcher check it before sending a `signal_send`. For
non-LightWorker roles, leave it `NULL` and keep the existing
`tmux_session`-based path. For `execution_target='svend3060'` roles,
skip tmux injection and instead push the role envelope to the LightWorker
HTTP endpoint (per §20). Do **not** overload `tmux_session` with a worker
id, and do not store `execution_target` on `bridge_flow_steps` — it is a
role-level property, and step-level overrides would create a four-way
resolution table for no present benefit. Keep `default_model_alias`
separate; the LightWorker will re-resolve the alias to a real model on
its own allocator, and that resolution should not require the Father to
have done anything more than send the alias name.

**Evidence.**

```
$ sqlite3 /home/svend/DPMtF-WebUI/databases/dpmtf.db \
      "PRAGMA table_info(bridge_flow_steps);"
0|id|INTEGER|0||1
1|flow_key|TEXT|1||0
2|step_key|TEXT|1||0
3|from_role|TEXT|1||0
4|to_role|TEXT|1||0
5|deliverable_dir|TEXT|0||0
6|deliverable_pattern|TEXT|0||0
7|pre_dispatch_script|TEXT|0||0
8|post_dispatch_script|TEXT|0||0
9|error_msg|TEXT|0||0
10|sort_order|INTEGER|0|0|0
11|is_active|INTEGER|0|1|0
12|rule_key|TEXT|0||0
13|auto_chain_to_next|INTEGER|0|0|0
14|validation_required|INTEGER|0|0|0
15|model_source|TEXT|0||0
16|model_alias|TEXT|0||0
```

```
$ sqlite3 /home/svend/DPMtF-WebUI/databases/dpmtf.db \
      "PRAGMA table_info(bridge_roles);"
0|role_key|TEXT|0||1
1|tmux_session|TEXT|1||0
2|setup_script|TEXT|0||0
3|teardown_script|TEXT|0||0
4|deliver_error_msg|TEXT|0||0
5|is_active|INTEGER|0|1|0
6|created_at|TIMESTAMP|0|CURRENT_TIMESTAMP|0
7|updated_at|TIMESTAMP|0|CURRENT_TIMESTAMP|0
8|restart_policy|TEXT|0|'none'|0
9|governance_file|TEXT|0|NULL|0
10|role_type|TEXT|0|'agent'|0
11|enter_command|TEXT|0|'default'|0
12|config_dir|TEXT|0|NULL|0
13|primary_output_type|TEXT|0|NULL|0
14|default_model_source|TEXT|0||0
15|default_model_alias|TEXT|0||0
16|trade_mcp_push_mode|TEXT|0||0
17|max_output_tokens|INTEGER|0||0
18|allocator_client|TEXT|0|'opencode'|0
19|fresh_session_command|TEXT|0|NULL|0
20|workdir_mode|TEXT|1|'target_project'|0
```

(Neither table contains `execution_target`. `tmux_session` is the only
host-routing column on `bridge_roles`. `model_source` / `model_alias` are
alias selection, not worker routing.)

```
$ grep -rn "execution_target" /home/svend/DPMtF-WebUI /home/svend/model-allocator
(no matches)
```

```
$ sed -n '1426,1430p' /home/svend/DPMtF-WebUI/scripts/bridgeV002/dispatch.py
    print(f"  Deliverable: {payload['deliverable_file']}")

    tmux_session = to_role["tmux_session"]
    role_type = to_role.get("role_type", "agent")

```

(The dispatcher reads `tmux_session` to decide *where on the Father
machine* to inject the prompt. There is no parallel read of a worker id
because no such column exists.)

```
$ sed -n '500,508p' /home/svend/DPMtF-LightWorker/GOAL.md
```yaml
role_key: imple01
model_source: model_allocator
model_alias: imple01-3060
execution_target: svend3060
client: opencode
```

`execution_target` is a LightWorker routing concern and may require a new Father-side field or mapping.
```

## 5. The lease design (§15)

**Question.** §15 proposes two designs for `LeaseRegistry` integration:
Option A (Father-owned lease, worker runs under it) and Option B
(worker-local lease adapter). Does the current `LeaseRegistry`
implementation support a lease held by one process on behalf of another,
or is it process-local? Answer §15's Option A vs B **on what the code
does**, not on which sounds cleaner.

**Answer.** The current `LeaseRegistry` is already shared between
processes — but only across **Father** processes, via a SQLite table.
The class holds `_leases` as a *class* attribute (so any in-process
lookup works) and additionally writes every lease to a `model_leases`
SQLite table inside `DPMtF-WebUI/databases/dpmtf.db`. The `_load_from_db`
method reads them back on every acquire/release call, and the docstring
on `acquire` explains exactly why: "every dispatch runs as its own
process, so an in-memory-only lease was invisible to the release() call
in the NEXT dispatch process". The Father side acquires leases keyed on
`handoff_id` in `dispatch.py::signal_send` and releases them in
`signal_complete`. The lease's `job_id` parameter is a string the
caller chooses; the worker would identify itself by setting
`worker_id="svend3060"`.

That persistence model supports Option A cleanly: a lease acquired on
Father and *named for* a worker is visible to whatever process later
releases it, regardless of which machine the release came from — *as
long as that release runs against the same database*. Option B requires
the worker to write its own `model_leases` rows into the Father's
database, or to maintain a parallel lease table on the worker that
Father would have to read. Neither exists today, and the SQLite lease
table is the **Father's live database** — writing to it from a worker
across Tailscale is the "an implementer once destroyed it by accident"
path that the governance file warns about.

The code as written also exposes a real design choice that §15 glosses
over: `release(stop_model=False)` exists *for the case where the next
role uses the same real model under a different alias*, and
`release_all(alias)` exists *for the case where a stale lease is
blocking a swap*. A worker calling `stop --alias <alias>` directly
bypasses both safety valves. §15's prohibition on unconditional
`model-allocator stop --alias` is consistent with that.

**Recommendation.** Use **Option A (Father-owned lease)**. Father
acquires the lease at `signal_send`, holds it for the duration of the
remote execution, and releases it when the role result is durably
reported (the lease's `job_id` is the handoff id, not the worker's
execution id). The worker does not acquire or release leases; it only
calls `model-allocator start --alias <alias>` (warm-up) and `stop
--alias <alias>` **only** when the alias is a single-lease alias whose
`lifecycle_policy == stop_after_step` and the Father-issued lease is
the only one. Even then, the worker should check `LeaseRegistry.lease_count(alias) == 1`
through a Father-side endpoint rather than calling `stop` directly,
because the count it sees may not match the Father's authoritative
count. Concretely: worker calls `start` against its local allocator
(the backend has to run on the worker's GPU); worker calls back to
Father on completion; Father releases the lease; Father then calls
`stop` on its local view of the allocator state. A worker must never
write to the `model_leases` table.

**Evidence.**

```
$ sed -n '41,89p' /home/svend/DPMtF-WebUI/scripts/job_queue/model_lease.py
class LeaseRegistry:
    """Lease registry with SQLite persistence.

    Leases are stored in the DPMtF database so they survive process restarts.
    Falls back to in-memory if the database is unavailable.
    """

    _leases: dict[str, list[Lease]] = {}  # alias → list of active leases (in-memory fallback)
    _db_path: str = None

    @classmethod
    def _get_db_path(cls) -> str:
        if cls._db_path:
            return cls._db_path
        try:
            import config
            p = config.get_db_path()
            import os
            if not os.path.isabs(p):
                p = os.path.join(str(config.get_project_root()), p)
            cls._db_path = p
            return p
        except Exception:
            return 

    @classmethod
    def _ensure_table(cls):
        """Create model_leases table if it doesn't exist."""
        p = cls._get_db_path()
        if not p:
            return
        import sqlite3
        try:
            conn = sqlite3.connect(p)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_leases (
                    lease_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    worker_id TEXT,
                    acquired_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(job_id, alias)
                )
            """)
            conn.commit()
            conn.close()
        except Exception:
            pass
```

(`LeaseRegistry` writes to SQLite on `_save_lease_to_db` and deletes on
`_delete_lease_from_db`. The `model_leases` table is what makes a lease
survive a process restart; without it, the in-memory `_leases` dict is
process-local.)

```
$ sed -n '146,173p' /home/svend/DPMtF-WebUI/scripts/job_queue/model_lease.py
    @classmethod
    def acquire(cls, job_id: str, alias: str, worker_id: str = "") -> Lease:
        """Acquire a lease on a model alias. Starts the model if no leases exist.

        The lease is persisted to SQLite — every dispatch runs as its own
        process, so an in-memory-only lease was invisible to the release()
        call in the NEXT dispatch process. That made had_lease always False
        and from-role models were never stopped at handoff (models piled up
        in VRAM until Ollama's idle timeout).
        """
        lease = Lease(
            job_id=job_id,
            alias=alias,
            acquired_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            worker_id=worker_id,
        )

        was_empty = (len(cls._leases.get(alias, []))
                     + len(cls._load_from_db(alias))) == 0

        cls._leases.setdefault(alias, []).append(lease)
        cls._save_lease_to_db(lease)

        if was_empty:
            # Start the model — first lease
            cls._start_model(alias)

        return lease
```

(Acquisition takes `worker_id` already; today `dispatch.py` passes
`worker_id="dispatch"` or `worker_id=from_role_key`, never a
LightWorker id.)

```
$ sed -n '2133p;2883p' /home/svend/DPMtF-WebUI/scripts/bridgeV002/dispatch.py
2133:            LeaseRegistry.acquire(handoff_id, to_alias_sc, worker_id="dispatch")
2883:            LeaseRegistry.acquire(handoff_id, to_alias, worker_id=from_role_key)
```

(Father already passes a `worker_id` string into `LeaseRegistry.acquire`.
There is no enforcement that the value match any particular worker; a
LightWorker name would round-trip through the same column.)

```
$ sqlite3 /home/svend/DPMtF-WebUI/databases/dpmtf.db \
      "SELECT alias, COUNT(*) FROM model_leases GROUP BY alias;"
$ sqlite3 /home/svend/DPMtF-WebUI/databases/dpmtf.db \
      "SELECT COUNT(*) FROM model_leases;"
0
```

(The table exists but is empty at this moment. No prior leases have
been left behind, but the schema is reachable from any process that can
open `dpmtf.db` — which on this machine means the Father's processes,
not a worker on a different host unless Tailscale routing exposes it.)

```
$ sed -n '249,270p' /home/svend/DPMtF-WebUI/scripts/job_queue/model_lease.py
    def _start_model(cls, alias: str):
        """Start the model via allocator.

        The allocator CLI defaults to a 120s start timeout, and the SGLang
        adapter KILLS the server it just started when that expires. A 30B
        AWQ model needs 3-6 minutes for weight load plus CUDA graph
        capture, so the default guaranteed the role never got a model —
        this is the same defect dispatch.py had, in a second place.
        """
        start_timeout = int(os.environ.get("DPMTF_MODEL_START_TIMEOUT", "900"))
        try:
            result = subprocess.run(
                [ALLOCATOR_SCRIPT, "start", "--alias", alias,
                 "--timeout", str(start_timeout)],
                capture_output=True, text=True, timeout=start_timeout + 60,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                print(f"  WARNING: model start returned {result.returncode} "
                      f"for '{alias}': {detail}", file=sys.stderr)
        except Exception as e:
            print(f"  WARNING: model start failed for '{alias}': {e}", file=sys.stderr)
```

(The lease's `_start_model` shells out to the **local**
`scripts/model-allocator` wrapper — the one at
`config.get_project_path("model-allocator")`. A worker cannot make that
call across the network; it has its own allocator install.)

```
$ sed -n '915,932p' /home/svend/DPMtF-LightWorker/GOAL.md

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
```

(`GOAL.md §15 V1 requirement` is consistent with the `release()` and
`release_all()` helpers: an unconditional `model-allocator stop --alias`
breaks the shared-runtime contract.)

## 6. Whether `model-allocator run` needs an alias-based form (§43)

**Question.** `model-allocator run` takes `--role` today. Must a role key
exist in `roles.yaml` for it to work, and what would LightWorker pass for
a role that lives only in the Father's database?

**Answer.** Yes — `model-allocator run` requires a role key, and that
role key must be present in `roles.yaml`. The CLI parses `--role` as
`required=True`, hands it to `Resolver.resolve_role_client(role_key,
client)`, which calls `roles[role_key]`; if the role is missing, the
resolver raises `ResolutionError("Role '<role_key>' not found")` and
`cmd_run` exits 1 with `ERROR: Role '<role_key>' not found` on stderr.
There is no `--alias` form of `run`, no `--model-alias` form, and no
fallback path that bypasses `roles.yaml`. A LightWorker that wants to
launch a client for an alias it does not have a `roles.yaml` entry for
must call `model-allocator validate --alias <alias> --client opencode`
(which uses `resolve_alias`, not `resolve_role_client`) and then
reconstruct the OpenCode command itself — losing the tmux-safe shell
string the allocator already produces for OpenCode in
`build_opencode_command`.

The current `roles.yaml` shape is:

```yaml
roles:
  <role_key>:
    default_alias: <alias>
    config_dir: <key>             # OpenCode rendered-config dir name
    client_aliases:
      <client>: <alias>
```

So a role maps a `client` to an `alias`. For a LightWorker whose
canonical "role key" lives only in the Father's database (per §8's
`execution_target`), the worker would need a parallel entry in
`roles.yaml` keyed on the worker's role key (or an alias of it). That
parallel entry is the only way to drive `run`, `render-config` and
`preflight` — every one of those accepts `--role`, not `--alias`.

**Recommendation.** Add an `--alias` form to `model-allocator run` (and
`render-config` / `preflight` for symmetry), accepting the alias name
directly and skipping the `roles.yaml` lookup. The new code path
resolves `models[alias]` directly, applies `runtime_profiles[profile]`,
and emits the same tmux-safe shell string. The existing `--role` path
stays intact. This is a small, contained change to `cli.py` and
`resolver.py` (a new `resolve_alias_client(alias, client)` helper), and
it removes the need to ship a `roles.yaml` entry for every role that
only the worker knows about. Without this change, the LightWorker has
to either mirror Father's role keys into its own `roles.yaml` (and
keep them in sync) or call `validate` + `build_opencode_command`
itself, both of which are worse.

**Evidence.**

```
$ sed -n '680,685p' /home/svend/model-allocator/src/model_allocator/cli.py
    p_run = sub.add_parser("run", help="Render the tmux-safe shell string for a role/client")
    p_run.add_argument("--role", required=True, help="Role key")
    p_run.add_argument("--client", required=True, help="Client key (e.g. opencode, claude-code)")
    p_run.add_argument("--max-output-tokens", type=int, default=None, help="Override max_output_tokens for Claude Code roles")
    p_run.add_argument("--no-auto-start", action="store_true", default=False, help="Skip auto-starting the backend server")
    p_run.set_defaults(func=cmd_run)
```

```
$ sed -n '57,71p' /home/svend/model-allocator/src/model_allocator/resolver.py
    def resolve_role_client(self, role_key: str, client: str) -> dict:
        roles = self.config.get("roles", {})
        role = roles.get(role_key)
        if not role:
            raise ResolutionError(f"Role '{role_key}' not found")
        alias_name = role.get("client_aliases", {}).get(client)
        if not alias_name:
            alias_name = role.get("default_alias")
        if not alias_name:
            raise ResolutionError(f"No alias configured for role '{role_key}' and client '{client}'")
        resolved = self.resolve_alias(alias_name)
        resolved["role_key"] = role_key
        if "config_dir" in role:
            resolved["config_dir"] = role["config_dir"]
        return resolved
```

(`resolve_role_client` looks up `roles[role_key]` first; if the role is
absent, it raises. There is no path that accepts only an alias name.)

```
$ sed -n '212,225p' /home/svend/model-allocator/src/model_allocator/cli.py
def cmd_run(args: argparse.Namespace) -> int:
    """Print the tmux-safe shell string for starting a client against an alias."""
    resolver = Resolver(config_dir=_config_dir(args))
    try:
        resolved = resolver.resolve_role_client(args.role, args.client)
    except ResolutionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
```

```
$ cd /home/svend/model-allocator && python3 -m model_allocator \
    run --role no-such-role --client opencode 2>&1; echo "exit=$?"
ERROR: Role 'no-such-role' not found
exit=1
```

(A role absent from `roles.yaml` makes `model-allocator run` exit 1
immediately. There is no `--alias` fallback to test against; the
subcommand rejects `--alias` because the argument is named `--role`.)

```
$ sed -n '208,219p' /home/svend/model-allocator/roles.yaml
  imple01:
    default_alias: cloud_minimax
    config_dir: imple01
    client_aliases:
      opencode: cloud_minimax
```

(`roles.yaml` carries a `config_dir` field; that is what determines
where `render-config` writes by default if `--output` is not passed. A
LightWorker would need a matching entry to inherit the same behavior.)

```
$ grep -n "config_dir\|OPENCODE_ROLES_CONFIG_BASE" \
      /home/svend/model-allocator/src/model_allocator/cli.py \
      /home/svend/model-allocator/src/model_allocator/adapters/opencode.py | head -10
/home/svend/model-allocator/src/model_allocator/cli.py:189:        "OPENCODE_ROLES_CONFIG_BASE", "$HOME/.config/opencode-roles"
/home/svend/model-allocator/src/model_allocator/cli.py:194:    json_path = os.path.join(expanded_base, config_dir, "opencode.json")
/home/svend/model-allocator/src/model_allocator/cli.py:253:            config_dir = resolved.get("config_dir") or args.role
/home/svend/model-allocator/src/model_allocator/cli.py:488:    if args.output:
/home/svend/model-allocator/src/model_allocator/cli.py:500:        _atomic_write_json(output_path, config)
/home/svend/model-allocator/src/model_allocator/adapters/opencode.py:22:    config_base = os.environ.get("OPENCODE_ROLES_CONFIG_BASE", "$HOME/.config/opencode-roles")
/home/svend/model-allocator/src/model_allocator/adapters/opencode.py:25:        "OPENCODE_CONFIG_DIR": full_config_dir
/home/svenv/model-allocator/src/model_allocator/adapters/opencode.py:26:    "OPENCODE_CONFIG": f"{full_config_dir}/opencode.json"
```

(`cmd_run` uses `resolved.get("config_dir") or args.role`, so the path
is `OPENCODE_ROLES_CONFIG_BASE / config_dir / opencode.json`. Without
`config_dir` on the role, it falls back to `args.role`. For a
LightWorker that has no `roles.yaml` entry, the fallback is just the
role key, which is still an `--role` argument.)

## 7. Execution-specific `OPENCODE_CONFIG` (§9, §19)

**Question.** How does the allocator decide where `render-config` writes
today? Today the destination is per *role*; can it be pointed at a
per-*execution* path without disturbing a shared one? §19 makes a third
part of this matter: a rendered config also carries the permission block,
so say whether hand-written entries survive regeneration or are
overwritten by it. Show the code that decides.

**Answer.** Two paths write OpenCode config today, both inside
`cli.py`:

1. **`render-config --output <path>`** (cmd `cmd_render_config`,
   line 472–505). The output path comes from `args.output`. If the file
   already exists, `_merge_opencode_config(existing, rendered,
   provider_key)` is invoked and the merged dict is written atomically
   (temp + rename). The merge function preserves every top-level key
   from `existing` (so `$schema`, `permission`, `mcp`, other providers,
   hand-written entries) and only updates `model` and the
   provider-block entry for the rendered alias's provider. Hand-written
   permission blocks therefore **survive** regeneration, but only if
   they live under top-level keys the merge does not touch.

2. **`run` auto-refresh** (cmd `cmd_run`, line 180–209 and 252–257).
   This path constructs a path from `OPENCODE_ROLES_CONFIG_BASE` +
   `resolved.get("config_dir") or args.role` and *always* writes the
   merged result back to that file. There is no `--output` argument
   that lets the caller redirect this write — it is hard-wired to
   the role's `config_dir`. So `model-allocator run` always writes to
   the shared role directory; only `render-config --output <path>`
   can target a per-execution path.

For a LightWorker, the per-execution path is the disposable worktree's
`opencode.json`. `render-config --output
<worktree>/opencode.json --role <role> --client opencode` does exactly
this and merges with any pre-existing file at that path. The merge
preserves `permission`, `mcp`, and any other top-level keys not in the
rendered output, so a per-execution file can ship an extended
permission block that survives every later regeneration of the same
file. The role-shared path is **not** touched by `render-config
--output`, so writing to a per-execution file does not disturb a
shared role config.

The remaining concern is `cmd_run`'s auto-refresh: when the worker
calls `model-allocator run`, it will overwrite the shared
`~/.config/opencode-roles/<role>/opencode.json` regardless of where
`render-config` wrote. So a worker must call `render-config
--output <execution-path>` first to seed the per-execution file, then
**either** call `run --role <role>` and let the auto-refresh happen
against the shared path, or call `run` with an alternate flag that
redirects the refresh. Today no such flag exists.

**Recommendation.** For each execution: (1) call `model-allocator
render-config --role <role> --client opencode --output
<worktree>/opencode.json` to seed the per-execution file; (2) when
launching the client, set `OPENCODE_CONFIG=<worktree>/opencode.json`
explicitly in the environment the allocator returns. Do not rely on
`run`'s auto-refresh to land the per-execution write — that path is
role-shared by design. Hand-written permission blocks under top-level
keys the merge function does not touch (`permission`, `mcp`, `$schema`,
non-rendered providers) survive regeneration, so place them at the top
level of the per-execution file and they persist across re-renders. If
the future per-execution flow ever needs to regenerate an *execution*
file in place, add an `--output` argument to `run` (or a `--no-refresh`
flag that suppresses the auto-write); today, the right primitive is
`render-config --output` followed by an explicit
`OPENCODE_CONFIG=...` launch.

**Evidence.**

```
$ sed -n '180,209p' /home/svend/model-allocator/src/model_allocator/cli.py
def _refresh_opencode_json(resolved: dict, config_dir: str) -> None:
    """Auto-refresh opencode.json for an OpenCode role.

    Writes the `model` field and provider block atomically (temp + rename),
    merging with any existing file. Diagnostics go to stderr — stdout purity
    must be maintained (Father captures stdout for tmux injection).
    """
    import os
    config_base = os.environ.get(
        "OPENCODE_ROLES_CONFIG_BASE", "$HOME/.config/opencode-roles"
    )
    # Expand $HOME for the allocator's own file write
    expanded_base = os.path.expandvars(config_base)
    expanded_base = os.path.expanduser(expanded_base)
    json_path = os.path.join(expanded_base, config_dir, "opencode.json")
    config_obj = opencode.build_opencode_config(resolved)
    if not config_obj:
        return
    try:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        existing = {}
        if os.path.exists(json_path):
            existing = json.loads(
                Path(json_path).read_text(encoding="utf-8")
            )
        merged = _merge_opencode_config(existing, config_obj, resolved.get("opencode_provider_name") or resolved.get("provider", ""))
        _atomic_write_json(Path(json_path), merged)
        print(f"  Refreshed opencode.json: {json_path}", file=sys.stderr)
    except Exception as exc:
        print(f"  WARNING: opencode.json refresh failed: {exc}", file=sys.stderr)
```

(`run`'s auto-refresh path is hard-wired: it joins
`OPENCODE_ROLES_CONFIG_BASE + config_dir + "opencode.json"`. The only
way to redirect this is the env var — `config_dir` comes from
`resolved.get("config_dir") or args.role`, with no per-execution
override.)

```
$ sed -n '441,505p' /home/svend/model-allocator/src/model_allocator/cli.py
def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically using a temp file + rename in the same directory."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(data, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _merge_opencode_config(existing: dict, rendered: dict, provider_key: str) -> dict:
    """Merge rendered config into existing opencode.json.

    Preserves existing top-level keys (permission, mcp, $schema, other providers,
    etc.) while setting/updating the top-level ``model`` field and the provider
    block for the role's backend.
    """
    merged = dict(existing)
    if "model" in rendered:
        merged["model"] = rendered["model"]

    rendered_provider = rendered.get("provider", {})
    if rendered_provider:
        merged.setdefault("provider", {})
        merged["provider"] = dict(merged["provider"])
        for provider_name, provider_config in rendered_provider.items():
            merged["provider"][provider_name] = provider_config

    return merged


def cmd_render_config(args: argparse.Namespace) -> int:
    """Emit opencode.json content for a role/client."""
    resolver = Resolver(config_dir=_config_dir(args))
    try:
        resolved = resolver.resolve_role_client(args.role, args.client)
    except ResolutionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.client != "opencode":
        print(f"ERROR: render-config only supports client 'opencode', got '{args.client}'", file=sys.stderr)
        return EXIT_ERROR

    config = opencode.build_opencode_config(resolved)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        provider_key = resolved.get("opencode_provider_name") or resolved.get("provider", "")

        if output_path.exists():
            try:
                existing = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                print(f"ERROR: existing opencode.json is not valid JSON: {exc}", file=sys.stderr)
                return EXIT_ERROR
            config = _merge_opencode_config(existing, config, provider_key)

        _atomic_write_json(output_path, config)
        print(f"Config written to {output_path}")
        return EXIT_OK

    print(json.dumps(config, indent=2, default=str))
    return EXIT_OK
```

(`render-config --output <path>` writes to the caller-chosen path and
merges with whatever is already there. The merge preserves every
top-level key from `existing` and only updates `model` and the
`provider[<rendered-alias-provider>]` block. Anything else the
caller wrote — `permission`, `mcp`, `$schema`, other providers — is
preserved by `dict(existing)` + selective overwrite.)

```
$ sed -n '21,28p' /home/svend/model-allocator/src/model_allocator/adapters/opencode.py
def _config_env(config_dir: str) -> dict[str, str]:
    config_base = os.environ.get("OPENCODE_ROLES_CONFIG_BASE", "$HOME/.config/opencode-roles")
    full_config_dir = f"{config_base}/{config_dir}"
    return {
        "OPENCODE_CONFIG_DIR": full_config_dir,
        "OPENCODE_CONFIG": f"{full_config_dir}/opencode.json",
    }
```

(`build_opencode_command` returns env vars that point at
`OPENCODE_ROLES_CONFIG_BASE/<config_dir>/opencode.json`. To redirect
the launch to a per-execution file, the worker must override these env
vars after the fact — the allocator does not let you pass an
alternate path.)

```
$ sed -n '1101,1119p' /home/svend/DPMtF-LightWorker/GOAL.md
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
```

(`§19` mandates that hand-written permission blocks survive
`render-config`. The merge logic confirms that *top-level* blocks
survive; the worker should write its permission block at the top level
of the per-execution file.)

```
$ cat ~/.config/opencode-roles/imple01/opencode.json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen3-coder:30b-256k",
  "provider": {
    "ollama-v1": { ... },
    "minimax": { ... },
    "ollama": { ... }
  },
  "permission": {
    "external_directory": "allow",
    "bash": "allow",
    "edit": "allow"
  },
  "mcp": {
    "mcp-light": { ... }
  }
}
```

(Live example: a hand-written `permission` block and an `mcp` block
sit alongside allocator-rendered `provider` blocks. The merge
function's `dict(existing)` + selective `model`/`provider` overwrite
preserves both. This file lives at the role-shared path
`~/.config/opencode-roles/imple01/opencode.json` — the path a
LightWorker must NOT write to if it wants to keep a shared config
untouched.)
