# Planning providers

OpenDots runs an installed CLI to produce structured plans. OpenDots then
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
  --workspace /absolute/path/to/your/repository --backend claude
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
your installed, authenticated CLI using the goal-and-heartbeat walkthrough.

Official references: [CLI options](https://code.claude.com/docs/en/cli-reference),
[programmatic use](https://code.claude.com/docs/en/headless).
