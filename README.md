# OpenDots

<img src="assets/OpenDots-wordmark.png" alt="OpenDots" width="360">

An open-source experiment inspired by OpenAI’s Dots idea: persistent agents that pursue goals, react to events, and keep work moving over time.

OpenDots is an independent local prototype. Targets subscribe to events; workers propose actions, follow owner policies, run checks, and retain reviewable Git branches and patches. SQLite keeps task state, memory, and an audit trail across restarts.

## Start with a real goal

Read **[Goals, heartbeats and event streams](docs/GOALS_AND_EVENTS.md)** for the
complete runnable walkthrough. It uses a real repository and Claude Code or
Codex, explains recurring proactive planning, and shows JSONL, HTTP and GitHub
configuration. [Provider setup](docs/PROVIDERS.md) covers authentication.

Requires Python 3.11+, Git, Linux with working Bubblewrap namespaces, and an
installed authenticated planning CLI. From a current checkout:

```bash
git clone https://github.com/Shashankss1205/OpenDots.git
cd OpenDots
# Install/authenticate Claude Code separately, or select Codex in the config.
claude auth login
python3 -m opendots --config examples/goal-agent.json doctor
python3 -m opendots --config examples/goal-agent.json serve --port 8766
```

In another terminal in that checkout:

```bash
python3 -m opendots tui --url http://127.0.0.1:8766
```

The example pursues a real CLI-usability goal in OpenDots itself, with write
approval and a required syntax check. Adjust its goal, workspace, permissions and
behavioral checks for your project. It is not a fixed repair recipe. A heartbeat
starts on service startup and repeats every 30 minutes. Runtime data stays outside
the source repository. Read the guide before enabling unattended work.

## Install the command

```bash
bash install.sh --source "$PWD"
export PATH="$HOME/.local/share/opendots/runtime/bin:$PATH"
```

Or download the installer:

```bash
curl -fsSLo install-opendots.sh https://raw.githubusercontent.com/Shashankss1205/OpenDots/main/install.sh
bash install-opendots.sh
```

The installer creates an isolated Python environment without sudo. Install system
prerequisites first (`sudo apt-get install git bubblewrap python3-venv` on
Ubuntu/Debian with Python 3.11+). `--ref COMMIT` pins a reviewed remote revision;
`--prefix DIR` chooses a new installation directory. Existing installations are
not overwritten. For a fresh real-project config:

```bash
opendots init --directory "$HOME/.config/opendots-project" \
  --workspace /path/to/your/repository --backend claude
```

Set the goal, scopes, checks and heartbeat using the guide. Pass the printed
configuration path explicitly to `doctor`, `serve` and service installation.
`init` without a workspace still creates optional deterministic fixtures; those
are not the real-agent onboarding path.

## Background service (Linux systemd)

```bash
opendots --config examples/goal-agent.json service install --name opendots-goal
opendots service start --name opendots-goal
opendots                 # open the terminal client on the service port 8765
opendots service status --name opendots-goal
opendots service stop --name opendots-goal
```

The service runs under your user account on port 8765. Stop any existing runtime
on that port first; stop the foreground walkthrough runtime before starting a
service for the same database. Set up and diagnose the configuration first. User services normally follow your login session; configure user lingering
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

Use `/history TEXT` and `/next` to find older work, `/work ID` for evidence,
`/status` for connector health and daily planning usage, and `/cancel ID` to stop
a task between actions. Tab completes commands; arrow keys edit the prompt or
recall previous inputs. Task progress appears in the transcript as work proceeds.

Completed changes remain proposals until you accept them. Use `/proposal ID` to
inspect the patch, then `/accept ID COMMIT` with the displayed commit to make it
the base for future tasks. Acceptance retains changes locally; your source
repository is unchanged. Pause the target and finish or cancel active work first.

To refresh from edits in your source repository, finish or cancel active work,
stop the service, run `opendots sync TARGET_ID`, then restart the service. This
replaces the base for future tasks with a new source snapshot and clears the
previous acceptance. Old proposals remain available for inspection. Update your
source repository from its remote yourself before syncing.

## Optional deterministic fixtures

For runtime development, `examples/config.json` and `examples/five-events.json`
exercise the predefined Kubernetes/React fixtures. They use no model by default.
The fixture dashboard includes a five-event button. These examples do not modify
upstream repositories or a real cluster. Explicit `trusted-local` execution is
only for owner-controlled disposable fixtures when namespaces are unavailable;
the runtime never selects it automatically.

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

[Goals and events](docs/GOALS_AND_EVENTS.md) is the user walkthrough. [Implementation](docs/IMPLEMENTATION.md) explains every subsystem and its boundaries. [Validation](docs/VALIDATION.md) records current and historical results. [Development](docs/DEVELOPMENT.md) covers installation, demo runners, and deployment templates.

OpenDots 0.2.0 retains local proposals. Outbound GitHub publishing, automatic PRs, production multi-user hosting, and automatic synchronization with upstream source changes are outside its implemented scope. Explicit source synchronization is available. See the [remaining work](docs/ROADMAP.md) and [contributor guide](CONTRIBUTING.md).

MIT licensed; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
