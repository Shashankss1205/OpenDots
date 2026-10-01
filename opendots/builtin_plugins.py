"""Built-in capability registrations use the same contract as installed plugins."""
from . import __version__
from .plugins import Plugin, PluginManifest


def register_local_tools(api, registry):
    for name, arguments in {
        "read_file": ["path"],
        "write_file": ["path", "content", "expected_sha256"],
        "replace_text": ["path", "old_text", "new_text", "expected_sha256"],
        "run_check": ["name"], "note": ["text"],
    }.items():
        api.tools.register(name, getattr(registry, name), arguments)


def validate_github(config):
    import re
    if not isinstance(config.get("repo"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config["repo"]):
        raise ValueError("GitHub source repo must be owner/name")
    if config.get("bootstrap", "observe") not in {"observe", "replay"}:
        raise ValueError("GitHub bootstrap must be observe or replay")
    if type(config.get("max_pages", 5)) is not int or config.get("max_pages", 5) < 1:
        raise ValueError("GitHub max_pages must be positive")
    if not isinstance(config.get("token_env", "GITHUB_TOKEN"), str) or not config.get("token_env", "GITHUB_TOKEN"):
        raise ValueError("GitHub token_env must name an environment variable")


def validate_jsonl(config):
    if not isinstance(config.get("path"), str) or not config["path"]:
        raise ValueError("JSONL source requires a path")
    if type(config.get("batch_size", 1000)) is not int or config.get("batch_size", 1000) < 1:
        raise ValueError("JSONL batch_size must be positive")


def register_sources(registry):
    from .connectors.github import GitHubPollSource
    from .connectors.jsonl import JSONLSource
    registry.register("github_poll", GitHubPollSource, validate_config=validate_github)
    registry.register("jsonl", JSONLSource, validate_config=validate_jsonl)


def load_builtins(manager, config, *, local_tools=True, providers=True):
    from .agents import ClaudeAgent, CodexAgent, DemoAgent
    from .connectors.github import GitHubPollSource
    from .connectors.jsonl import JSONLSource
    from .notifications import WebhookNotification, JSONLNotification, validate_webhook, validate_jsonl as validate_notification_jsonl

    def add(name, description, register):
        manager.load(Plugin(PluginManifest("builtin." + name, __version__, description), register), origin="builtin")

    if local_tools:
        add("local", "Scoped files, configured checks and persistent notes", lambda api: register_local_tools(api, manager.tools))
    if providers:
        add("claude", "Claude Code agent provider", lambda api: api.agents.register("claude", ClaudeAgent(
            config.claude_command, config.model, config.agent_timeout, manager.tools,
            config.context_limits, config.planner_env, config.claude_home)))
        add("codex", "Codex agent provider", lambda api: api.agents.register("codex", CodexAgent(
            config.codex_command, config.model, config.agent_timeout, manager.tools,
            config.context_limits, config.planner_env, config.planner_home)))
        if config.backend == "demo" or any(t.agent == "demo" for t in config.targets):
            add("demo", "Explicit optional deterministic examples", lambda api: api.agents.register("demo", DemoAgent()))
    from .model_providers import register_builtins
    add("models", "Named API, local model and CLI provider profiles", register_builtins)
    add("github", "GitHub repository activity polling", lambda api: api.sources.register(
        "github_poll", GitHubPollSource, validate_config=validate_github))
    add("jsonl", "Append-only JSON event inbox", lambda api: api.sources.register(
        "jsonl", JSONLSource, validate_config=validate_jsonl))
    def notifications(api):
        api.notifications.register("webhook", WebhookNotification, validate_config=validate_webhook)
        api.notifications.register("jsonl", JSONLNotification, validate_config=validate_notification_jsonl)
    add("notifications", "Durable webhook and JSONL notifications", notifications)
