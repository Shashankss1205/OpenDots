# Development and reproduction

## Setup

Python 3.11+, Git and Linux bubblewrap are required for default isolated checks. The application has no third-party Python runtime dependencies. Run from the repository root to find the example configuration.

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
python3 -m opendots serve
```

For real planning, provide an installed authenticated Codex CLI and set `backend` to `codex`. Optional `model`, `codex_command`, `agent_timeout` and target `agent` select behavior. Account access/limits apply. Provider errors do not switch silently to the deterministic planner.

Use separate disposable configurations/databases for acceptance runs. Independent source workspaces must not overlap. Keep credentials outside fixture directories.

## CLI and fixtures

```bash
python3 -m opendots ingest examples/five-events.json
python3 -m opendots drain
python3 -m opendots status
python3 -m opendots decide 1 approve
python3 -m opendots decide 2 approve
python3 -m opendots drain
python3 scripts/demo_kubernetes.py
python3 scripts/demo_react.py
python3 scripts/demo.py combined --approve-fixture-actions
```

The numbered approvals assume a fresh database. Initially, the five-event fixture has two reviews, one queued follow-up, a cheap star observation and an unrelated event. Approvals execute actual fixture actions/checks. Demo runners explicitly authorize their supplied local fixture actions. Kubernetes/React fixture names do not imply upstream clones or a real cluster.

## Runner catalog

| Scripts | Purpose / prerequisites |
| --- | --- |
| `demo.py`, `demo_kubernetes.py`, `demo_react.py`, `make_demo_config.py` | Deterministic examples and configuration |
| `live_upstream.py` | Codex repairs against pinned upstream Python projects |
| `live_case_set.py`, `live_scale.py`, `live_goal_fanout.py` | Different cases, concurrency, repeated delivery and required-check acceptance |
| `live_recovery.py` | Process death, persisted plans and completion evidence |
| `live_github.py` | Actual read-only public GitHub observation |
| `live_react.py`, `react_runtime_check.cjs` | Real React; Node, React/ReactDOM and DOM dependencies |
| `live_kubernetes.py` | Disposable K3s API; executable and explicit trusted-local execution |
| `prepare_live_demo.py`, `start_live_demo.sh` | Real-agent showcase; Codex and upstream dependencies |
| `live_browser.cjs`, `verify_scale_ui.cjs` | Playwright and installed Chromium |
| `record_demos.cjs`, `record_live_showcase.cjs`, `edit_showcase.py` | Optional browser/video recording tooling |
| `summarize_scale.py`, `verify_showcase.py` | Summaries and artifact verification |

Inspect runner help/source for arguments and dependency paths. Some browser scripts load Playwright from an environment-specific installation path; adapt it locally. Live runners can download upstream projects, execute owner-selected tests and launch disposable processes. They are development utilities, not new runtime tools. Generated outputs are excluded; [VALIDATION.md](VALIDATION.md) summarizes results.

## Tests and packaging

```bash
python3 -m unittest discover -s tests -v
OPENDOTS_TEST_SANDBOX=trusted-local python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

Use the relaxed test command only for explicitly trusted disposable fixtures without namespaces. Wheel building needs setuptools 68+. Wheels contain the package, dashboard and compatibility commands, but not example workspaces or external executables; installed CLI use requires an explicit external config.

## Deployment and extensions

`docker compose up --build` uses the optional Git/bubblewrap image, loopback host port and persistent `.opendots`. Nested namespaces depend on the host. Codex and credentials are not bundled. `deploy/opendots.service.example` is an editable systemd template. Template execution is not part of current validation.

The dashboard is a local control surface. Public hosting needs deliberate authentication/proxy design; request guards are not a multi-user login system.

Extend agents with `AgentRegistry.register`, named-string tools with `ToolRegistry.register`, and normalized adapters with `SourceRegistry.register`. Grant new tools explicitly; custom handlers own their scope/preview safety. See [IMPLEMENTATION.md](IMPLEMENTATION.md).

## Completion policy

The shipped demo and init templates require their named checks. For real targets,
configure `required_checks` explicitly before enabling writes. Investigation-only
targets may leave it empty; their completion summary reports no checks.
Failed checks return diagnostics to real planners for up to `max_repair_attempts`
(default 2), still bounded by `max_planning_rounds`. Set zero to disable repair.

## Observable goals

Targets may define `success_conditions`, independently checked before completion:

```json
{"name":"replicas","path":"deployment.json","format":"json","pointer":"/spec/replicas","minimum":2}
```

Use `equals` for exact typed JSON/text equality, or `minimum` for numbers.
Text is the default format. Conditions complement required checks and do not
claim to prove arbitrary natural-language goals. Every configured condition must pass.

## Installable extensions

Install an owner-trusted Python package into the OpenDots environment, then list
its entry-point name in `plugins`. Packages declare an `opendots.plugins` entry
point pointing to `register(api)`. API version 1 exposes `api.agents`, `api.tools`
and `api.sources`; use their registration methods. Only explicitly listed plugins
are loaded. Plugins run as trusted application code; they are not sandboxed.

Custom tools can pass `schema={"type":"object", ...}` to `register` instead of
string-only `arg_names`. Supported schema types are object, array, string, boolean,
integer, number and null, with required fields, enums, numeric bounds and strict
extra-field rejection. Existing string argument registrations remain compatible.

## Queue limits

`max_queued_per_target` and `max_events_per_minute` default to 1000 and are
configurable. Capacity failures do not acknowledge events; HTTP returns 429 and
source cursors retry. `priority_aging_seconds` (default 60) raises waiting work
priority over time to avoid starvation. Pause/resume and cancel are available in
the CLI/API; cancellation of running work occurs between bounded actions.
