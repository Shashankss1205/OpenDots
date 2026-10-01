"""Explicitly enabled, owner-trusted package extensions."""
from dataclasses import dataclass
from importlib.metadata import entry_points


@dataclass(frozen=True)
class ExtensionAPI:
    agents: object
    tools: object
    sources: object
    version: int = 1


def load_extensions(names, api):
    available={}
    for entry in entry_points(group='opendots.plugins'):
        if entry.name in available:
            raise ValueError(f'Duplicate installed extension name: {entry.name}')
        available[entry.name]=entry
    for name in names:
        if name not in available:
            raise ValueError(f'Extension is not installed: {name}')
        register=available[name].load()
        if not callable(register): raise ValueError(f'Extension entry point must be callable: {name}')
        register(api)
