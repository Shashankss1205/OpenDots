# OpenDots

![OpenDots architecture: your goal and incoming events drive planning, permitted actions, checks, and changes for your review.](assets/opendots-overview.svg)

**Give an agent your goal. Connect the information it needs. Review the work it proposes.**

OpenDots runs on your computer and coordinates Claude Code or Codex around a goal you choose. It listens for incoming information, keeps task history and notes, and can check progress on a schedule. You choose the project, allowed changes, checks, and approval rules.

A **heartbeat** is a scheduled check-in. An **event** is a message from you or a connected tool. A **target** is one configured agent with its own goal. The **runtime** is the process you leave running.

## What can it do?

- Pursue your saved goal using Claude Code or Codex.
- React to terminal requests, local HTTP messages, JSONL files, and GitHub activity.
- Assess matching events against each goal, with a visible confidence estimate and reason.
- Revisit progress on a configurable heartbeat schedule.
- Read project files, propose scoped edits, and run checks you configure.
- Ask for approval, retain local patches, and preserve history across restarts.
- Manage multiple agents through a terminal or a local web interface.

This is a local prototype. It does not automatically publish PRs, deploy applications, or pull upstream changes. Model access comes from your provider account. Checks prove only what you configure them to test.

## What you need

- **Linux, Python 3.11+, Git, and Bubblewrap** with working namespaces for isolated checks.
- **Claude Code or Codex CLI**, installed and signed in. See [provider setup](docs/PROVIDERS.md).
- **Your own project directory and a goal** you want the agent to pursue.

On Ubuntu/Debian, install prerequisites with `sudo apt-get install python3 python3-venv git bubblewrap curl`. Check `python3 --version` is at least 3.11. Docker and a GitHub token are not needed to start.

## Install OpenDots

Choose **one** method.

### Option A: download and run the installer

```bash
curl -fsSLo install-opendots.sh https://raw.githubusercontent.com/Shashankss1205/OpenDots/main/install.sh
bash install-opendots.sh
```

The script installs into a separate Python environment without sudo. Follow its printed PATH instruction; with the default location:

```bash
export PATH="$HOME/.local/share/opendots/runtime/bin:$PATH"
```

Add that line to your shell startup file for new terminals. `--prefix DIR` chooses a different installation directory; `--ref COMMIT` pins a reviewed revision. Existing installations are not overwritten.

### Option B: install the Python package from GitHub

With [pipx](https://pipx.pypa.io/stable/installation/) installed:

```bash
pipx install 'git+https://github.com/Shashankss1205/OpenDots.git'
pipx ensurepath
```

Open a new terminal if pipx asks you to. Both methods provide the `opendots` command. There is no published npm package or PyPI release being advertised here.

## Start with your project and your goal

### 1. Create your configuration

First sign in to your chosen CLI (`claude auth login`, or `codex login`). From your project's directory, replace the goal text below with your own objective:

```bash
opendots init --workspace "$PWD" --goal "Describe what you want this agent to achieve" --backend claude
```

Use `--backend codex` if that is your provider. No sample project, predefined repair, or demo event is created.

The command prints your configuration path. By default it is `~/.config/opendots/config.json` (or under `XDG_CONFIG_HOME`). Open that file to review your saved goal and settings. Normal setup starts with **reads and notes allowed, no writable paths, no configured checks, model goal-relevance assessment enabled, and no heartbeat**. Before enabling changes, set `write_paths`, `checks`, and `required_checks` for your project.

Want periodic work? Add `--heartbeat 1800` to `init` for a 30-minute check-in. A heartbeat can start work immediately when the runtime starts, and uses your provider's allowance. You can also configure schedules later.

### 2. Check the setup, then start the runtime

```bash
opendots doctor
opendots serve
```

Continue only when `doctor` reports top-level `"ok": true`. It checks configuration, executables, authentication, and isolation; it is not proof of a completed live model task. Leave the `serve` terminal open.

If you used `init --directory DIR`, pass the printed path explicitly: `opendots --config DIR/config.json doctor` and `opendots --config DIR/config.json serve`. Configuration and runtime data must stay outside your project directory.

### 3. Open an interface

In another terminal:

```bash
opendots
```

Or open **http://127.0.0.1:8765** on the same machine. Both interfaces use the same runtime.

## Talk to your agent

Type these commands inside the OpenDots terminal interface, not your shell:

```text
/agents
/use project
```

Then type a normal message about your goal. It becomes an `owner.request` event for the selected agent. The agent can investigate, save notes, or explain a blocker. It cannot write until you configure allowed paths and approve the proposed action.

| Command | What it does |
| --- | --- |
| `/listeners` | See configured sources, subscriptions, schedules, and relevance settings. |
| `/events` / `/event ID` | Browse received events and inspect payloads, routing reasons, and confidence. |
| `/send TYPE MESSAGE` | Create a message with your chosen event type. |
| `/connect` | Learn how to connect an event producer. |
| `/status` | Show the provider, source health, and model usage. |
| `/activity` | See what is happening. |
| `/reviews` | Find work waiting for approval. |
| `/review ID` | Inspect the action for the task number shown. |
| `/approve ID` | Request approval; type the requested confirmation to permit that exact action. |
| `/work ID` | Inspect task results and check evidence. |
| `/proposal ID` | Inspect a completed patch and its acceptance command. |
| `/pause` / `/resume` | Stop or resume the selected agent taking new work. |
| `/help` | Show all commands. |

Accepting a completed proposal makes its commit the base of future tasks; it does not change your original checkout or push code. Pause the agent and finish or cancel active work before acceptance. Editing your saved goal or subscriptions requires restarting the runtime.

`/quit` or Ctrl+D disconnects the interface. **Ctrl+C in the `serve` terminal stops the runtime.** Pausing does not cancel active work or stop incoming messages from queuing.

## Connect incoming information

Use `/listeners` and `/events` in the terminal, or **What is listening?** and **Received events** in the web interface. The web form can preview and send arbitrary JSON payloads.

A **source** brings messages into OpenDots. A **subscription** specifies which message types an agent listens to. Setting up one without the other does not create useful work.

| Method | How information arrives |
| --- | --- |
| Terminal / web form | You submit a message. |
| HTTP | Your tool sends a JSON event to `POST /api/events`. |
| JSONL | Your tool appends one JSON object per line to a watched inbox. |
| GitHub polling | OpenDots periodically fetches new repository activity. |
| GitHub webhook | A separately configured receiver forwards signed deliveries. |
| Heartbeat | OpenDots emits a scheduled event while running. |

**[Create events and connect sources](docs/EVENTS.md)** explains each setup with producer commands, subscription rules, and how confidence controls execution. New setups assess matching events before actions; uncertain decisions block work for inspection. Confidence is a model estimate, not a calibrated probability.

Native Slack, email, Kafka, Redis, and arbitrary filesystem-watch adapters are not built in. External tools can bridge into HTTP or JSONL. GitHub activity does not automatically refresh the agent's source-code snapshot.

## Documentation

- [Events and listeners](docs/EVENTS.md): create messages, connect tools, inspect history and relevance.
- [Provider setup](docs/PROVIDERS.md): Claude Code and Codex authentication.
- [Implementation](docs/IMPLEMENTATION.md): configuration, policies, event routing, and storage.
- [Development and operations](docs/DEVELOPMENT.md): checks, packaging, services, backup, and source refresh.
- [Validation](docs/VALIDATION.md): tested behavior and remaining live-provider validation.
- [Roadmap](docs/ROADMAP.md) and [Contributing](CONTRIBUTING.md).

<details>
<summary>Show me a demo or a worked sample</summary>

Demos are optional and separate from normal setup. For deterministic fixtures:

```bash
opendots init --demo --directory "$HOME/.config/opendots-demo"
opendots --config "$HOME/.config/opendots-demo/config.json" serve --port 8766
```

Open http://127.0.0.1:8766 and explicitly select the five-event demo. It uses no model and makes no changes to a real cluster or upstream repository.

For a fully worked real-provider sample using OpenDots' own source, see [the optional goal walkthrough](docs/GOALS_AND_EVENTS.md). Its predefined goal and syntax check are illustrative; they are not applied to your project by normal setup.

</details>

OpenDots is an independent experiment inspired by OpenAI's Dots idea. MIT licensed; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
