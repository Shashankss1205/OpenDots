# OpenDots

<img src="assets/OpenDots-wordmark.png" alt="OpenDots" width="360">

An open-source experiment inspired by OpenAI’s Dots idea: persistent agents that pursue goals, react to events, and keep work moving over time.

OpenDots is an independent local prototype. Targets subscribe to events; workers propose actions, follow owner policies, run checks, and retain reviewable Git branches and patches. SQLite keeps task state, memory, and an audit trail across restarts.

## Quick start

Requires Python 3.11+, Git, and Linux with working bubblewrap namespaces for isolated checks.

```bash
git clone https://github.com/Shashankss1205/OpenDots.git
cd OpenDots
bash install.sh --source "$PWD"
export PATH="$HOME/.local/share/opendots/runtime/bin:$PATH"
cd ..
opendots init
opendots doctor
opendots serve
```

The installer uses an isolated Python environment and does not require sudo. Install
system prerequisites first (`sudo apt-get install git bubblewrap python3-venv` on
Ubuntu/Debian with Python 3.11+). Use `--ref COMMIT` when installing remotely to pin
a reviewed revision; `--prefix DIR` changes the installation directory. Run
`bash install.sh --help` for options. It never overwrites an existing installation.
For an existing repository, use `opendots init --workspace /path/to/repo --backend codex`,
then configure subscriptions, write scopes and required checks in the printed config.

Open **http://127.0.0.1:8765**, click **Run five-event demo**, and review the proposed actions. The default planner is deterministic and clearly labeled; the runtime, checks, persistence, approvals, and Git worktrees are real.

For disposable fixtures on a host without working namespaces, explicitly set `"sandbox": "trusted-local"` in [the configuration](examples/config.json). This executes checks without process or network isolation. There is no silent fallback.

## Background service (Linux systemd)

```bash
opendots service install
opendots service start
opendots                 # open the terminal client
opendots service status
opendots service stop
```

The service runs under your user account. Set up and diagnose the configuration
first. User services normally follow your login session; configure user lingering
with your system administrator if agents must run after logout. No system-wide
service or sudo action is performed by these commands.

## Terminal interface

Keep `opendots serve` running, then open another terminal and run `opendots`
(or `opendots tui --url http://127.0.0.1:8765`). The prompt-first interface has
`/agents`, `/use ID`, `/reviews`, `/review ID`, `/activity`, `/pause`, `/resume`
and `/help`. Type a request for a target subscribed to `owner.request`, or use
`/send TYPE MESSAGE`. Review an action before `/approve ID`; a second explicit
confirmation is required. `/quit` or Ctrl+D disconnects without stopping agents.
The browser and terminal use the same runtime state and approval tokens.

Completed changes remain proposals until you accept them. Use `/proposal ID` to
inspect the patch, then `/accept ID COMMIT` with the displayed commit to make it
the base for future tasks. Acceptance retains changes locally; your source
repository is unchanged. Pause the target and finish or cancel active work first.

## Use a real agent

Install and authenticate the Codex CLI, then set `"backend": "codex"` in your configuration. OpenDots asks Codex for structured plans and executes permitted actions through its own tool registry. You can configure workers, models, subscriptions, write scopes, required checks, schedules, and providers.

```bash
python3 -m opendots --config examples/config.json serve
python3 -m opendots --config examples/config.json ingest examples/five-events.json
python3 -m opendots --config examples/config.json drain
python3 -m opendots --config examples/config.json status
```

## Included

- Durable event routing, deduplication, attention rules, and concurrent workers.
- Sequential work per target, exact-action approvals, and bounded planning rounds.
- Scoped file tools, Git proposal worktrees, required checks, and content-bound validation evidence.
- GitHub polling, signed incoming webhooks, JSONL input, timers, and a local dashboard.
- Extension registries for agents, tools, and sources; legacy `spots` command compatibility.

## Tests and docs

```bash
python3 -m unittest discover -s tests -v
# Explicitly trusted test fixtures when namespaces are unavailable:
OPENDOTS_TEST_SANDBOX=trusted-local python3 -m unittest discover -s tests -v
```

[Implementation](docs/IMPLEMENTATION.md) explains every subsystem and its boundaries. [Validation](docs/VALIDATION.md) records current and historical results. [Development](docs/DEVELOPMENT.md) covers installation, demo runners, and deployment templates.

This V0 retains local proposals. Outbound GitHub publishing, automatic PRs, production multi-user hosting, and automatic synchronization with upstream source changes are outside its implemented scope.

MIT licensed; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
