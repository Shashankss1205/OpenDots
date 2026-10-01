# Planning providers

OpenDots uses an installed CLI, a cloud API, or a local Ollama model to produce
structured plans. OpenDots then
validates arguments, applies owner policy, requests approvals and runs tools.
The provider never grants itself application permissions.

| Config backend | Executable | Authentication check |
| --- | --- | --- |
| `claude` | Claude Code (`claude_command`, default `claude`) | `claude auth status` |
| `codex` | Codex CLI (`codex_command`, default `codex`) | `codex login status` |

Install and authenticate your chosen CLI independently. Run OpenDots under the
same OS account. Use absolute executable paths for background services if their
PATH differs from your terminal. Provider failures are reported; there is no
fallback to a deterministic planner.

## Claude Code

```bash
claude auth login
claude auth status
opendots init --directory "$HOME/.config/opendots-project" \
  --workspace /absolute/path/to/your/repository --goal "Your goal" --backend claude
opendots --config "$HOME/.config/opendots-project/config.json" doctor
```

Configure the target goal, write scopes and checks before starting work. Set
`"backend": "claude"` in an existing real-project config, or set a target's
`"agent": "claude"` to override the installation default. An optional `model`
selects the model understood by the chosen CLI. Leave it unset when mixing
providers unless the identifier is valid for both.

The adapter requires Claude Code with print mode, JSON Schema output and safe
mode support. It reads the successful result envelope's `structured_output`;
plain text, malformed results, missing structured output and CLI errors fail.
It requests no built-in tools, denies MCP tools, uses an empty strict MCP config,
disables optional settings sources and session persistence, and enables safe
mode. Additional file reads are proposed as OpenDots `read_file` actions.
Managed organizational policy still applies; these flags do not attest the
behavior of an arbitrary externally installed executable.

The provider receives a bounded repository snapshot, owner goal, event, recent
memory and this task's action results. `agent_timeout` bounds each invocation;
normal planning-round and daily invocation budgets apply. Output is limited to
1 MB and parsed from the complete capture, not the diagnostic preview.

By default, use the CLI's existing login. Set `claude_home` to select a separate
`CLAUDE_CONFIG_DIR` profile and authenticate that same profile first. Additional
authentication environment variables, if needed, must be explicitly named in
`planner_env`, for example `["ANTHROPIC_API_KEY"]`; put the value in the runtime's
environment, never in the JSON configuration. `planner_home` remains the separate
Codex `CODEX_HOME` setting. OpenDots does not forward runtime GitHub tokens by default.

Claude credential/config paths (`.claude`, `.claude.json`, `.mcp.json`) are excluded
from managed snapshots and blocked by application file tools.

Protocol tests exercise a controlled subprocess, large structured output,
malformed/error responses, authentication diagnostics and real OpenDots approval
handling. They do not prove a successful live Claude model response. Verify with
your installed, authenticated CLI using the [goal-and-heartbeat walkthrough](GOALS_AND_EVENTS.md).

Official references: [CLI options](https://code.claude.com/docs/en/cli-reference),
[programmatic use](https://code.claude.com/docs/en/headless).

## Direct APIs and local models

You can also use an API key or a locally running Ollama server, without either
CLI. All four transports support both goal relevance and structured planning.
Choose a model available to your account/server that supports structured JSON.
OpenDots does not download models or silently switch providers.

| Profile `kind` | Protocol | Default base URL | Default credential variable |
| --- | --- | --- | --- |
| `openai` | Responses API | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `anthropic` | Messages API | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| `openai_compatible` | Chat Completions API | Required explicitly, including `/v1` if needed | `OPENAI_API_KEY` |
| `ollama` | Native `/api/chat` | `http://127.0.0.1:11434` | None; optional `api_key_env` |
| `claude_cli` | Claude Code | Installed executable | Existing CLI login |
| `codex_cli` | Codex CLI | Installed executable | Existing CLI login |

### First run with an API

Put your key in the runtime environment (and in your service environment if you
run a background service). Replace `YOUR_MODEL_ID` with the exact model ID you
have access to. The placeholder is not a bundled model.

```bash
export OPENAI_API_KEY='your-key'
opendots init --directory "$HOME/.config/opendots-api" \
  --workspace /absolute/path/to/your/repository \
  --goal "Your actual goal" --backend openai --model YOUR_MODEL_ID
opendots --config "$HOME/.config/opendots-api/config.json" doctor
opendots --config "$HOME/.config/opendots-api/config.json" serve
```

For Anthropic use `--backend anthropic` and `ANTHROPIC_API_KEY`. For an
OpenAI-compatible server use `--backend openai_compatible`, `--base-url
https://your-server.example/v1` and optionally `--api-key-env YOUR_KEY_VARIABLE`.
The compatible adapter sends `max_tokens` and structured `response_format`;
servers must support that dialect. Compatibility is not a claim that every
server or model implements these options.

For local inference, install Ollama and make your chosen model available first:

```bash
opendots init --directory "$HOME/.config/opendots-local-model" \
  --workspace /absolute/path/to/your/repository \
  --goal "Your actual goal" --backend ollama --model YOUR_INSTALLED_MODEL
opendots --config "$HOME/.config/opendots-local-model/config.json" doctor
opendots --config "$HOME/.config/opendots-local-model/config.json" serve
```

`doctor` checks configuration, credential presence and local dependencies. It
makes no model request, checks no model availability, and cannot confirm that an
API key is valid or a local model server is running. Normal `init` creates no
sample tasks. Use your own goal, then send an event or enable a heartbeat as in
the [goal guide](GOALS_AND_EVENTS.md). Review write scopes and checks before work.

### Choose a different provider for each agent

Merge these fields into your existing config. The top-level `backend` and each
target's `agent` select a **profile ID**, not its kind:

```json
{
  "backend": "cloud-planner",
  "providers": [
    {"id": "cloud-planner", "kind": "openai", "model": "YOUR_MODEL_ID",
     "api_key_env": "OPENAI_API_KEY", "max_output_tokens": 4096,
     "timeout_seconds": 180},
    {"id": "local-planner", "kind": "ollama", "model": "YOUR_INSTALLED_MODEL"},
    {"id": "cli-writer", "kind": "claude_cli", "model": "YOUR_CLAUDE_MODEL"}
  ]
}
```

Set `"agent": "local-planner"` inside a target to override the default. Multiple
profiles can use the same kind with different models, endpoints or credentials.
Restart the runtime after editing config. Profile IDs cannot replace an existing
registered agent such as `claude` or `codex`. Existing configs selecting those two
names continue to work.

HTTP profiles require `model`; the top-level legacy `model` does not override
named profiles. Optional HTTP fields are `base_url`, `api_key_env`,
`max_output_tokens` (default 4096), `timeout_seconds` (defaults to
`agent_timeout`), and `structured_output` (default `json_schema`). HTTPS is
required except for loopback HTTP. Redirects are rejected. Base URLs cannot
contain credentials, query strings or fragments. Put secrets only in environment
variables, never in the profile JSON or URL path.

OpenAI transports also accept `"structured_output": "json_object"` when a server
does not support strict JSON Schema or a custom tool has optional arguments.
This mode still supplies the original schema in the prompt and validates output
locally; a model may need more attempts to produce valid results. OpenDots never
automatically downgrades the output mode. Anthropic's wire schema omits unsupported
constraints such as numeric bounds; the runtime still validates original numeric
bounds and its tool schema subset. Strict mode requires explicit object
properties, all required, with `additionalProperties: false`.

CLI profiles accept `command`, `home` (relative to the config file), `env` (names
of environment variables to forward), `timeout_seconds`, and optional `model`.
Unspecified command/home/env inherit the corresponding global CLI settings;
model selection uses that profile or the CLI's own default. Existing authentication
and restricted CLI invocation behavior still applies. Named CLI diagnostics check
executable presence; run the relevant CLI's login-status command for authentication.

### Inspect and understand execution

```bash
opendots providers
```

Use `/providers` in the TUI, **Model providers** on the dashboard, or
`GET /api/providers`. These show profile ID, kind, model, plugin owner, basic
readiness and assigned targets. Inspection does not send prompts. API URLs and
credentials are excluded from the public inventory. An unused built-in CLI may
show as missing even when your selected API profile is ready.

API planning sends your goal, event, bounded workspace snapshot, owner skills,
task results and memory to the configured endpoint. Relevance assessment sends
only the goal, desired state and event. Choose context exclusions appropriately.
Cloud services receive that context under their own data policies; OpenAI
Responses requests set `store: false`. This is not a universal retention guarantee.

Each assessment/planning invocation reserves one call from OpenDots' daily
budgets and makes one HTTP attempt. There is no automatic billable transport
retry or provider fallback. HTTP errors, refusals, truncated replies, invalid
JSON and unexpected native tool calls fail without executing actions. Failure
messages omit remote error bodies. HTTP requests/responses are bounded to 2 MB /
1 MB; socket operations use the configured timeout (not a hard wall-clock deadline
against a server that keeps sending bytes). Limits on planning rounds and output
tokens still apply; call budgets are not dollar or token spending caps.

The model only proposes actions. Existing schema checks, file scopes, check
requirements and exact-plan approvals control execution. Changing a profile/model
invalidates cached goal relevance for resumed work. Pending approvals still refer
to their existing plan, not an automatically regenerated plan.

### Add your own provider kind

Inside an installed [plugin](PLUGINS.md), register a factory:

```python
def register(api):
    api.providers.register("team_model", make_provider, validate_config=validate_profile)


def make_provider(profile, context):
    # No network calls during construction. context.tools is the live tool registry;
    # context.config contains runtime limits. Return an instance implementing:
    # plan(target, event, state) -> OpenDots plan dict
    # assess_relevance(target, event) -> {decision, confidence, reason}
    return TeamProvider(profile, context)
```

Then add `{"id":"team-planner","kind":"team_model", ...}` to `providers` and
select `team-planner`. The validator/factory own their custom options. The plugin
must supply the `TeamProvider` implementation. Factories are staged: if any
configured profile fails, no new profile aliases are committed. Core budgets,
plan validation and approval rules remain in effect. Set a stable `identity`
attribute that changes with your provider's effective model/config so relevance
caching notices changes. Existing `api.agents.register(name, instance)` remains
available for fixed instances.

Protocol references: [OpenAI structured outputs](https://platform.openai.com/docs/guides/structured-outputs),
[Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
[Ollama chat API](https://docs.ollama.com/api/chat).
