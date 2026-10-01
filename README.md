# OpenDots

<img src="assets/OpenDots-wordmark.png" alt="OpenDots" width="360">

An open-source experiment inspired by OpenAI’s Dots idea: persistent agents that pursue goals, react to events, and keep work moving over time.

OpenDots is an independent local prototype. Targets subscribe to events; workers propose actions, follow owner policies, run checks, and retain reviewable Git branches and patches. SQLite keeps task state, memory, and an audit trail across restarts.

## Quick start

Requires Python 3.11+, Git, and Linux with working bubblewrap namespaces for isolated checks.

```bash
sudo apt-get install git bubblewrap
python3 -m opendots serve
```

Open **http://127.0.0.1:8765**, click **Run five-event demo**, and review the proposed actions. Run from the repository root. The default planner is deterministic and clearly labeled; the runtime, checks, persistence, approvals, and Git worktrees are real.

For disposable fixtures on a host without working namespaces, explicitly set `"sandbox": "trusted-local"` in [the configuration](examples/config.json). This executes checks without process or network isolation. There is no silent fallback.

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
