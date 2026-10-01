"""Versioned, explicitly enabled plugins sharing the runtime's capability registries.

Plugins are trusted Python code. Registration is transactional, not sandboxed.
Loading registers capabilities only; source connections start under the scheduler lock.
"""
from copy import copy, deepcopy
from dataclasses import dataclass, field
from importlib.metadata import entry_points
import re

from .schema import validate


API_VERSION = 2


@dataclass(frozen=True)
class PluginManifest:
    id: str
    version: str
    description: str = ""
    api_version: int = API_VERSION
    config_schema: dict = field(default_factory=lambda: {
        "type": "object", "properties": {}, "additionalProperties": False})


@dataclass(frozen=True)
class Plugin:
    manifest: PluginManifest
    register: object


@dataclass(frozen=True)
class PluginAPI:
    agents: object
    tools: object
    sources: object
    config: dict
    version: int = API_VERSION
    notifications: object = None


class _Registration:
    """Expose registration methods without exposing another plugin's registry entries."""
    def __init__(self, registry):
        self.register = registry.register


class _SourceRegistration(_Registration):
    def __init__(self, registry):
        super().__init__(registry)
        self.register_listener = registry.register_listener


class PluginManager:
    def __init__(self, agents, tools, sources, notifications=None):
        self.agents, self.tools, self.sources = agents, tools, sources
        from .notifications import NotificationRegistry
        self.notifications = notifications if notifications is not None else NotificationRegistry()
        self.records = []
        self.owners = {"agents": {}, "tools": {}, "sources": {}, "notifications": {}}
        for capability, values in self._capabilities().items():
            self.owners[capability].update({name: "application" for name in values})

    def _capabilities(self):
        return {"agents": self.agents.agents, "tools": self.tools.handlers,
                "sources": self.sources.factories, "notifications": self.notifications.factories}

    def load(self, plugin, config=None, *, origin="installed", legacy=False):
        manifest = plugin.manifest
        if not isinstance(manifest, PluginManifest):
            raise ValueError("Plugin must supply a PluginManifest")
        if not isinstance(manifest.id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", manifest.id):
            raise ValueError("Plugin ID must use letters, digits, dots, underscores or hyphens")
        if any(record["id"] == manifest.id for record in self.records):
            raise ValueError(f"Plugin already loaded: {manifest.id}")
        if type(manifest.api_version) is not int or manifest.api_version != (1 if legacy else API_VERSION):
            raise ValueError(f"Unsupported plugin API version for {manifest.id}: {manifest.api_version}")
        if not isinstance(manifest.version, str) or not manifest.version or not isinstance(manifest.description, str):
            raise ValueError(f"Plugin {manifest.id} requires version and description strings")
        if not callable(plugin.register):
            raise ValueError(f"Plugin {manifest.id} register must be callable")
        if not isinstance(manifest.config_schema, dict) or manifest.config_schema.get("type") != "object":
            raise ValueError("Plugin config schema must describe an object")
        options = deepcopy(config if config is not None else {})
        validate(options, manifest.config_schema, f"plugin_config.{manifest.id}")

        # Staging keeps a failed registration from leaving partially registered tools/providers.
        agents, tools, sources = copy(self.agents), copy(self.tools), copy(self.sources)
        agents.agents = dict(self.agents.agents)
        tools.handlers = dict(self.tools.handlers)
        tools.arg_names, tools.schemas = deepcopy(self.tools.arg_names), deepcopy(self.tools.schemas)
        sources.factories = dict(self.sources.factories)
        sources.modes, sources.validators = dict(self.sources.modes), dict(self.sources.validators)
        notifications = copy(self.notifications)
        notifications.factories, notifications.validators = dict(self.notifications.factories), dict(self.notifications.validators)
        before = {kind: set(entries) for kind, entries in self._capabilities().items()}
        if legacy:
            from .extensions import ExtensionAPI
            api = ExtensionAPI(agents, tools, sources)
        else:
            api = PluginAPI(_Registration(agents), _Registration(tools), _SourceRegistration(sources), options,
                            notifications=_Registration(notifications))
        plugin.register(api)
        after = {"agents": agents.agents, "tools": tools.handlers, "sources": sources.factories, "notifications": notifications.factories}
        for kind, entries in self._capabilities().items():
            if any(name not in after[kind] or after[kind][name] is not value for name, value in entries.items()):
                raise ValueError(f"Plugin {manifest.id} cannot replace existing {kind}")
        self.agents.agents.update(agents.agents)
        self.tools.handlers.update(tools.handlers)
        self.tools.arg_names.update(tools.arg_names)
        self.tools.schemas.update(tools.schemas)
        self.sources.factories.update(sources.factories)
        self.sources.modes.update(sources.modes)
        self.sources.validators.update(sources.validators)
        self.notifications.factories.update(notifications.factories)
        self.notifications.validators.update(notifications.validators)
        # Legacy providers may retain their API registries for later schema generation.
        # Keep those views live after commit so subsequent plugins remain visible.
        agents.agents = self.agents.agents
        tools.handlers, tools.arg_names, tools.schemas = self.tools.handlers, self.tools.arg_names, self.tools.schemas
        sources.factories, sources.modes, sources.validators = self.sources.factories, self.sources.modes, self.sources.validators
        notifications.factories, notifications.validators = self.notifications.factories, self.notifications.validators
        capabilities = {kind: sorted(set(values) - before[kind]) for kind, values in after.items()}
        for kind, names in capabilities.items():
            self.owners[kind].update({name: manifest.id for name in names})
        self.records.append({"id": manifest.id, "version": manifest.version,
            "api_version": manifest.api_version, "description": manifest.description,
            "origin": origin, "legacy": legacy, "status": "loaded", "capabilities": capabilities})

    def load_installed(self, names, configs=None):
        configs = configs or {}
        if len(set(names)) != len(names):
            raise ValueError("Enabled plugin names must be unique")
        if set(configs) - set(names):
            raise ValueError("plugin_config may only configure explicitly enabled plugins")
        if not names:
            return
        available = {}
        for entry in entry_points(group="opendots.plugins"):
            if entry.name not in names:
                continue
            if entry.name in available:
                raise ValueError(f"Duplicate installed plugin name: {entry.name}")
            available[entry.name] = entry
        for name in names:
            if name not in available:
                raise ValueError(f"Plugin is not installed: {name}; install it into the OpenDots Python environment")
            entry = available[name]
            candidate = entry.load()
            legacy = not isinstance(candidate, Plugin)
            if legacy:
                if not callable(candidate):
                    raise ValueError(f"Plugin entry point must be a Plugin or register(api) callable: {name}")
                version = getattr(getattr(entry, "dist", None), "version", "legacy")
                candidate = Plugin(PluginManifest(name, version, "Legacy register(api) extension", api_version=1), candidate)
            if candidate.manifest.id != name:
                raise ValueError(f"Plugin manifest ID must match entry point name: {name}")
            self.load(candidate, configs.get(name), legacy=legacy)

    def snapshot(self):
        # Deliberately excludes options, credentials, plugin objects and arbitrary runtime state.
        return {"api_version": API_VERSION, "plugins": deepcopy(self.records), "owners": deepcopy(self.owners)}
