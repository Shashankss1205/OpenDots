# Development and reproduction

## Setup

Python 3.11+, Git and Linux bubblewrap are required for default isolated checks. The application has no third-party Python runtime dependencies. Use `opendots init --workspace PATH --goal "Your goal"` to create a real configuration; `init --demo` explicitly creates packaged test fixtures, or select a repository config with `--config`.

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
python3 -m opendots --config examples/config.json serve
```

For real planning, provide an installed authenticated Claude Code or Codex CLI and set `backend` to `claude` or `codex`. Start with the [goal and event-stream guide](GOALS_AND_EVENTS.md) and [provider setup](PROVIDERS.md). Optional `model`, `codex_command`, `agent_timeout` and target `agent` select behavior. Account access/limits apply. Provider errors do not switch silently to the deterministic planner.

Use separate disposable configurations/databases for acceptance runs. Independent source workspaces must not overlap. Keep credentials outside fixture directories.

## CLI and fixtures

```bash
python3 -m opendots --config examples/config.json ingest examples/five-events.json
python3 -m opendots --config examples/config.json drain
python3 -m opendots --config examples/config.json status
python3 -m opendots --config examples/config.json decide 1 approve
python3 -m opendots --config examples/config.json decide 2 approve
python3 -m opendots --config examples/config.json drain
python3 scripts/demo_kubernetes.py
python3 scripts/demo_react.py
python3 scripts/demo.py combined --approve-fixture-actions
```

The numbered approvals assume a fresh database. Initially, the five-event fixture has two reviews, one queued follow-up, a cheap star observation and an unrelated event. Approvals execute actual fixture actions/checks. Demo runners explicitly authorize their supplied local fixture actions. Kubernetes/React fixture names do not imply upstream clones or a real cluster.

Commands that need a configuration report its missing path and point to
`opendots init --workspace PATH --goal "Your goal"` when no configuration exists.
Use `--config` to select the file printed by `init`, or initialize the default
configuration directory. A failed command does not initialize runtime state.
Malformed configuration and missing-workspace errors retain their own messages.

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

Use the relaxed test command only for explicitly trusted disposable fixtures without namespaces. Wheel building needs setuptools 77+. Wheels contain the package, dashboard, first-run templates, fixture workspaces and compatibility commands. Git, Bubblewrap and the optional Codex CLI remain external executables. `opendots init --workspace PATH --goal "Your goal"` creates the external config.

## PyPI releases

The distribution name is `opendots`; the console command is also `opendots`.
Before each release, keep the versions in `pyproject.toml` and
`opendots/__init__.py` in sync, update the changelog, and run the tests above.
Build into an empty `dist/` directory to avoid uploading old artifacts:

```bash
python3 -m pip install build twine
python3 -m build
python3 -m twine check dist/*
python3 -m twine upload --username __token__ dist/*
```

Enter the PyPI token only at Twine's password prompt. Never put tokens in source
files or commit them. PyPI release files cannot be overwritten; fixes require a
new version. The existing GitHub release workflow prepares draft release assets
and does not publish to PyPI.

## Deployment and extensions

`docker compose up --build --wait` installs the package and runs as UID 10001, binds the host port to loopback, and initializes demo configuration in the named `opendots-data` volume. The volume persists configuration, fixture sources and runtime data across container restarts. `docker compose down --volumes` deletes it. Codex and credentials are not bundled; mount an owner-configured setup and set `OPENDOTS_CONFIG` for real targets. Nested Bubblewrap namespaces depend on the host and fail closed when unavailable. Container CI validates installation, startup, non-root execution, health and restart; it does not establish nested sandbox or live-planner acceptance. `opendots service install` creates a user systemd unit; `deploy/opendots.service.example` is an editable alternative. Use an absolute `codex_command` if the CLI is outside the service PATH.

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

Use the [shared plugin framework guide](PLUGINS.md) for package installation,
manifest/config contracts, capability registration, polling and persistent
listeners. Built-ins and enabled packages share the same PluginManager. API 2
exports a `Plugin` object; legacy `register(api)` entry points continue on API 1.
Only explicitly named installed packages load. Plugins are trusted Python code,
not sandboxed. `opendots plugins`, `/plugins` and the web inventory show ownership.

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

## Write scope defaults

Omitted `write_paths` in file configuration now permits no writes. Broad access
requires an explicit `"*"`. `protected_paths` defaults to check.py, tests/** and
.github/** and takes precedence over write scopes; configured skills are also
protected. Owners can customize protected paths, and should include every local
validation harness they rely on as independent evidence.

## Planner environment boundary

The planner receives only PATH, HOME, locale/temp platform variables and names
explicitly listed in `planner_env`. Runtime GitHub/webhook credentials are not
forwarded by default. `planner_home` selects a dedicated CODEX_HOME profile;
authenticate that profile separately and review its configuration. `doctor`
checks the same environment. The planner still uses Codex read-only sandboxing;
this is not an attestation of arbitrary CLI hooks, network behavior or plugins.
Use a dedicated OS account/container when a stronger planner boundary is needed.

## Planning budgets

`max_model_calls_per_day` caps the installation (default 1000), and each target
may set `model_calls_per_day` (default 100). Reservations are atomic and durable,
include failed real-provider attempts, and reset at UTC midnight. Exhausted work
is blocked. These are invocation budgets: Codex CLI does not provide this runtime
with an authoritative per-call bill, so dollar/token limits are not claimed.

## Backup, restore and retention

Stop the service before maintenance. `opendots backup /path/to/new-backup` copies
one database using SQLite's backup API, its managed repositories/worktrees, and
the config. The destination must be new and outside runtime storage and source
workspaces. A SHA-256 inventory detects accidental backup changes. Backups contain
local source and task data: keep them private and restore only trusted backups.
External source repositories, planner credentials and installed dependencies are
not included.

`opendots restore /path/to/backup` verifies the inventory and database integrity,
then restores only to the original database/workspace paths specified by the
current config. Git worktree metadata contains absolute paths. Existing data is
never overwritten: move damaged data aside first, including SQLite WAL/SHM files.
Keep the config at its original location, or restore the saved config there
before invoking the command. Restart normally to mark interrupted work for inspection.

`opendots cleanup --older-than 30` previews terminal task workspaces eligible for
archiving; `--apply` removes those worktrees. Active tasks and the currently
accepted workspace are retained. Git branches, patches, database history and
audit evidence remain. Uncommitted contents of removed worktrees are lost, so
back up first if needed. An archived proposal can be inspected through its patch,
but must be replanned before acceptance. This does not prune Git object history
or database rows; their long-term archival remains a separate operational task.
