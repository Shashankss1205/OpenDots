# Connect capabilities through one plugin framework

A plugin is an installed Python package that supplies one or more **agent
providers**, **event adapters**, or **tools**. One Slack package could eventually
own its listener, thread-reading tool and reply tool. You enable the package once;
each configured connection gets its own ID, cursor and health state.

OpenDots now uses this framework for its built-in capabilities too. Native Slack,
Discord, MCP, notification and memory-provider integrations are not included by
this change. Slack/Discord SDK integration remains work for a connector package.

## See what is loaded

```bash
opendots plugins
opendots listeners
```

In the TUI, use `/plugins` and `/listeners`. The web interface shows **Loaded
plugins** and **What is listening?**. API clients can use `GET /api/plugins` and
`GET /api/listeners`.

**Loaded** means the plugin's capabilities are registered. It does not mean a
connection is authenticated or running. Only `serve` starts configured listeners
and polling. Inspection commands and `drain` do not open source connections.

| Built-in plugin | Registered capabilities |
| --- | --- |
| `builtin.local` | `read_file`, `write_file`, `replace_text`, `run_check`, `note` |
| `builtin.claude` | `claude` agent provider |
| `builtin.codex` | `codex` agent provider |
| `builtin.github` | `github_poll` event adapter |
| `builtin.jsonl` | `jsonl` event adapter |
| `builtin.demo` | `demo` provider, only when explicitly selected |

Built-ins need no entry in `plugins`. Existing provider names, source kinds,
subscriptions, permissions and SQLite data continue to work. Programmatically
injected registries report their preexisting capability owner as `application`.

## Install and enable a plugin

Review the package first. Plugins run as **trusted application code**, with the
runtime's OS access; this framework is not a Python sandbox. Install into the
same environment as OpenDots. For a package you have developed locally:

```bash
# Default Bash-installer environment:
"$HOME/.local/share/opendots/runtime/bin/python" -m pip install /absolute/path/to/your-plugin

# Or, for an existing pipx installation:
pipx inject opendots-local-prototype /absolute/path/to/your-plugin
```

Choose the command matching your installation. Custom prefixes require their own
Python path. Add the package's **entry-point name**, not necessarily its pip
distribution name, to the configuration:

```json
{
  "plugins": ["team_connector"],
  "plugin_config": {
    "team_connector": {"label": "Operations"}
  }
}
```

Merge these fields into your existing config, restart `serve`, then inspect
`/plugins`. A source still needs a `sources` entry, and an agent needs matching
subscriptions. Installing or enabling a plugin does not grant its tools approval.
Use environment-variable references in connector configuration for credentials;
the plugin resolves those variables when establishing the connection. Plugin
options and cursor contents are excluded from the public inventory. Plugins must
avoid including credentials in diagnostic strings or emitted event payloads.

## Build a package: complete minimal tool example

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "my-opendots-team-connector"
version = "0.1.0"
requires-python = ">=3.11"

[project.entry-points."opendots.plugins"]
team_connector = "team_connector:plugin"

[tool.setuptools]
py-modules = ["team_connector"]
```

Alongside it create `team_connector.py`:

```python
from opendots.plugins import Plugin, PluginManifest


def register(api):
    label = api.config.get("label", "Team")

    def record_note(target, args):
        return {"note": f"{label}: {args['text']}"}

    api.tools.register("team.record_note", record_note, ["text"])


plugin = Plugin(
    PluginManifest(
        id="team_connector",
        version="0.1.0",
        description="Team-prefixed persistent notes",
        config_schema={
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "additionalProperties": False,
        },
    ),
    register=register,
)
```

Install and enable it using the preceding instructions. To allow an agent to use
the tool, add `"team.record_note": "approval"` to that target's `policy`. The
tool becomes available to planning; its execution still requires an approved
action. You can choose `auto` after reviewing the handler.

API version 2 exposes these registration surfaces:

| Call | Contract |
| --- | --- |
| `api.agents.register(name, provider)` | Provider has `plan(target, event, state)`; model relevance also requires `assess_relevance(target, event)` |
| `api.tools.register(name, handler, arg_names)` | Handler receives `(target, args)` and returns a JSON-compatible result |
| `api.tools.register(name, handler, schema=...)` | Typed object arguments using OpenDots' supported schema subset |
| `api.sources.register(kind, factory, validate_config=...)` | Factory takes one source config and returns a polling adapter |
| `api.sources.register_listener(kind, factory, validate_config=...)` | Factory takes one source config and returns a persistent listener |

The optional `validate_config(config)` must raise `ValueError` for invalid source
settings without opening a network connection. Plugin-level options are validated
against the manifest's `config_schema`; it uses the same schema subset as tools
(objects, arrays, strings, booleans, numbers, integers, null, enums, required
properties and numeric bounds), not arbitrary JSON Schema.

Register capabilities during `register(api)`; do not open connections or execute
actions there. A failed registration discards that plugin's staged registry
changes. Existing capability names cannot be replaced. The plugin ID must match
the entry-point name, API versions are checked, and duplicate enabled/installed
names fail clearly. Registration rollback cannot undo arbitrary Python side
effects; that is another reason to keep registration declarative.

## Polling adapter contract

```python
class MyPollingAdapter:
    def __init__(self, config):
        self.config = config

    def poll(self, state):
        # Fetch a bounded batch using a finite network timeout.
        # Return normalized event dictionaries and a JSON-serializable cursor.
        return [], state


def register(api):
    api.sources.register("my_poll", MyPollingAdapter)
```

Add `{"id":"connection-1","kind":"my_poll","interval_seconds":30}` to
`sources`. The runtime schedules bounded workers, ingests the batch and advances
the cursor only after successful ingestion. Retry IDs must remain stable.

Optional adapter attributes `skip_existing_ids = True` and
`reject_invalid_records = True` select delivery behavior. The former skips an
already recorded ID, appropriate only for immutable upstream delivery IDs; the
latter audits and skips invalid complete records. GitHub polling uses the first,
JSONL uses the second. By default, invalid events fail the batch and leave its
cursor unchanged. Capacity failures always retry rather than skip.

## Persistent listener contract

Persistent listeners accommodate SDK sockets, subscriptions and long-running
consumers without occupying polling workers. Implement `run(context)`, and
register its factory with `api.sources.register_listener`.

| Context member | Meaning |
| --- | --- |
| `state` | Last explicitly saved JSON cursor for this source ID |
| `ready()` | Mark authenticated/subscribed and listening |
| `emit(event)` | Validate, route and durably ingest an event; returns a receipt or raises |
| `checkpoint(cursor)` | Save a JSON object for reconnect/restart, at most 256 KB |
| `stop_event` | Cooperative shutdown signal; `stop_event.wait(seconds)` is interruptible |

Within your SDK callback or receive loop:

1. Normalize the platform delivery into an [event envelope](EVENTS.md#create-an-event).
2. Call `context.emit(event)` with a stable upstream delivery ID.
3. After it succeeds, save any resume cursor with `context.checkpoint(...)`.
4. Acknowledge the upstream delivery, if the platform supports acknowledgments.

If ingestion raises (including queue capacity), do not advance or acknowledge
that delivery. Retrying an identical event ID/content is safe. Durable receipt is
not successful task execution; relevance and approval run later.

Call `ready()` only after the SDK has authenticated and subscribed. Make receive
operations time out regularly so they can inspect `stop_event`. Close the client
in `finally`. If `run()` raises or unexpectedly returns, the supervisor recreates
the listener with its persisted cursor and retries with exponential delays from
2 to 30 seconds. Fix bad credentials/config and restart; this version does not
classify permanent transport errors separately.

Each configured listener has one daemon thread, independent of `source_workers`.
Shutdown signals all listeners and waits up to five seconds in total; an
uncooperative listener reports `stop_timeout`. Python threads cannot be forcibly
terminated, so trusted plugins must cooperate and manage their SDK resources.
Polling retains its existing bounded-call shutdown behavior. Transport buffering,
resume guarantees and message retention remain the connector/platform's
responsibility; no universal lossless or exactly-once delivery claim is made.

## Ownership and migration

The core owns event validation, persistence, deduplication, task routing, budgets,
goal relevance and action approvals. Registered tools use the normal action
pipeline. Source plugins emit events; event content does not grant permission.
Custom handlers still own their own resource access and scope validation.

Existing `opendots.plugins` entry points exposing `register(api)` are supported as
**legacy API 1** and appear that way in the inventory. Their original three
registries remain available. To migrate, export a `Plugin` object with a manifest,
change the entry-point target to that object, and use API 2 registration methods.
API 2 intentionally exposes registration methods instead of mutable registries.

There is no automatic package download, hot unload/reload, dependency resolver,
marketplace, arbitrary lifecycle hook API or platform account wizard in this
version. Install dependencies with pip, configure explicitly, then restart.
