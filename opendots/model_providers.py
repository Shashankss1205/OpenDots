"""Named, plugin-owned planning profiles; transports never execute model tools."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
import re
import shutil
from urllib.parse import urlsplit

from .agents import StructuredAgent, ClaudeAgent, CodexAgent, plan_schema
from .http_client import post_json, validate_url


HTTP_KINDS = ('openai', 'openai_compatible', 'anthropic', 'ollama')
DEFAULT_URLS = {'openai': 'https://api.openai.com/v1',
                'anthropic': 'https://api.anthropic.com/v1',
                'ollama': 'http://127.0.0.1:11434'}
DEFAULT_KEYS = {'openai': 'OPENAI_API_KEY', 'openai_compatible': 'OPENAI_API_KEY',
                'anthropic': 'ANTHROPIC_API_KEY'}


@dataclass(frozen=True)
class ProviderContext:
    config: object
    tools: object


class ProviderRegistry:
    def __init__(self):
        self.factories, self.validators = {}, {}

    def register(self, kind, factory, *, validate_config=None):
        if not isinstance(kind, str) or not kind or not callable(factory):
            raise ValueError('Provider kind requires a name and callable factory')
        if kind in self.factories:
            raise ValueError('Provider kind already registered')
        if validate_config is not None and not callable(validate_config):
            raise ValueError('Provider validator must be callable')
        self.factories[kind], self.validators[kind] = factory, validate_config


def validate_profiles(profiles, registry=None):
    if not isinstance(profiles, (list, tuple)):
        raise ValueError('providers must be an array')
    names = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError('Each provider must be an object')
        name = profile.get('id')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) or name in names:
            raise ValueError('Provider IDs must be unique names using letters, digits, dots, underscores or hyphens')
        names.add(name)
        if not isinstance(profile.get('kind'), str) or not profile['kind']:
            raise ValueError('Provider requires kind')
        if registry is None and profile['kind'] in (*HTTP_KINDS, 'claude_cli', 'codex_cli'):
            validate_builtin(profile)
        if registry is not None:
            if profile['kind'] not in registry.factories:
                raise ValueError('Unknown provider kind: ' + profile['kind'])
            validator = registry.validators[profile['kind']]
            if validator: validator(profile)


def validate_builtin(profile):
    kind = profile['kind']
    common = {'id', 'kind', 'model', 'timeout_seconds'}
    allowed = common | ({'base_url', 'api_key_env', 'max_output_tokens', 'structured_output'} if kind in HTTP_KINDS
                        else {'command', 'home', 'env'})
    if set(profile) - allowed:
        raise ValueError('Unknown provider option; use api_key_env for credentials')
    model = profile.get('model')
    if (kind in HTTP_KINDS or model is not None) and (not isinstance(model, str) or not model.strip() or len(model) > 256):
        raise ValueError('Provider requires a nonempty model identifier (at most 256 characters)')
    timeout = profile.get('timeout_seconds', 180)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 600:
        raise ValueError('Provider timeout_seconds must be between 1 and 600')
    if kind in HTTP_KINDS:
        base = profile.get('base_url', DEFAULT_URLS.get(kind))
        validate_url(base)
        if urlsplit(base).query:
            raise ValueError('Provider base_url must not contain a query')
        if type(profile.get('max_output_tokens', 4096)) is not int or not 1 <= profile.get('max_output_tokens', 4096) <= 1000000:
            raise ValueError('max_output_tokens must be between 1 and 1000000')
        mode = profile.get('structured_output', 'json_schema')
        if mode not in {'json_schema', 'json_object'} or (kind in {'anthropic', 'ollama'} and mode != 'json_schema'):
            raise ValueError('structured_output must be json_schema; OpenAI transports also accept json_object')
        key = profile.get('api_key_env', DEFAULT_KEYS.get(kind))
        if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key)):
            raise ValueError('api_key_env must name an environment variable')
        if kind != 'ollama' and not key:
            raise ValueError('API provider requires api_key_env')
    else:
        for key in ('command', 'home'):
            if key in profile and (not isinstance(profile[key], str) or not profile[key]):
                raise ValueError('CLI command and home must be nonempty strings')
        env = profile.get('env', [])
        if not isinstance(env, list) or any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) for key in env):
            raise ValueError('CLI env must list environment variable names')


def identity(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, allow_nan=False).encode()).hexdigest()


def wire_schema(schema, *, anthropic=False):
    """Anthropic lacks these constraints; retain the original for local validation."""
    if isinstance(schema, list):
        return [wire_schema(item, anthropic=anthropic) for item in schema]
    if not isinstance(schema, dict):
        return schema
    removed = {'minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems', 'multipleOf', 'pattern', 'format'} if anthropic else set()
    result = {}
    for key, value in schema.items():
        if key in removed: continue
        # Property names are data, even when they share a schema keyword.
        result[key] = ({name: wire_schema(spec, anthropic=anthropic) for name, spec in value.items()}
                       if key == 'properties' else wire_schema(value, anthropic=anthropic))
    if result.get('type') == 'object':
        if set(result.get('required', [])) != set(result.get('properties', {})) or result.get('additionalProperties') is not False:
            raise ValueError('Strict model schemas require all object properties to be required and additionalProperties=false; use an OpenAI json_object profile for optional tool arguments')
    return result


def reject_constant(value):
    raise ValueError('Model JSON must not contain nonfinite numbers')


class HTTPModel(StructuredAgent):
    def __init__(self, profile, context):
        validate_builtin(profile)
        self.profile = deepcopy(profile)
        self.kind, self.model = profile['kind'], profile['model']
        self.identity = identity(profile)
        self.registry, self.context_limits = context.tools, context.config.context_limits
        self.timeout = profile.get('timeout_seconds', context.config.agent_timeout)
        self.base_url = profile.get('base_url', DEFAULT_URLS.get(self.kind)).rstrip('/')
        self.key_env = profile.get('api_key_env', DEFAULT_KEYS.get(self.kind))

    def plan(self, target, event, state):
        return self.respond(target, self.prompt(target, event, state) +
            '\nYou have no native tools. Request OpenDots read_file actions for additional context; never claim unexecuted actions succeeded.',
            plan_schema(self.registry))

    def respond(self, target, prompt, schema_spec):
        headers = {}
        if self.key_env:
            key = os.environ.get(self.key_env)
            if not key or any(ord(c) <= 32 or ord(c) >= 127 for c in key):
                raise RuntimeError('Provider credential environment variable is missing or invalid: ' + self.key_env)
            headers['x-api-key' if self.kind == 'anthropic' else 'Authorization'] = key if self.kind == 'anthropic' else 'Bearer ' + key
        prompt += '\nReturn only one JSON object matching this exact schema:\n' + json.dumps(schema_spec)
        mode = self.profile.get('structured_output', 'json_schema')
        schema = wire_schema(schema_spec, anthropic=self.kind == 'anthropic') if mode == 'json_schema' else None
        tokens = self.profile.get('max_output_tokens', 4096)
        messages = [{'role': 'user', 'content': prompt}]
        payload = {'model': self.model}
        if self.kind == 'openai':
            endpoint = '/responses'
            fmt = {'type': 'json_schema', 'name': 'opendots', 'strict': True, 'schema': schema} if schema else {'type': 'json_object'}
            payload.update(input=messages, text={'format': fmt}, max_output_tokens=tokens, store=False)
        elif self.kind == 'openai_compatible':
            endpoint = '/chat/completions'
            fmt = {'type': 'json_schema', 'json_schema': {'name': 'opendots', 'strict': True, 'schema': schema}} if schema else {'type': 'json_object'}
            payload.update(messages=messages, response_format=fmt, max_tokens=tokens, stream=False)
        elif self.kind == 'anthropic':
            endpoint = '/messages'
            headers['anthropic-version'] = '2023-06-01'
            payload.update(messages=messages, max_tokens=tokens, output_config={'format': {'type': 'json_schema', 'schema': schema}})
        else:
            endpoint = '/api/chat'
            payload.update(messages=messages, format=schema, stream=False, options={'num_predict': tokens})
        # Exactly one HTTP attempt: retry decisions and call budgets belong to the runtime.
        reply = post_json(self.base_url + endpoint, payload, headers, self.timeout)
        try:
            text = self._text(reply)
            value = json.loads(text, parse_constant=reject_constant)
            if not isinstance(value, dict): raise ValueError()
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise ValueError('Model returned an incomplete, refused, or invalid structured response') from None
        # Enforce the original schema even when a service supports only part of it.
        from .schema import validate
        validate(value, schema_spec, 'model response')
        return value

    def _text(self, reply):
        if self.kind == 'openai':
            if reply.get('status') != 'completed' or reply.get('error'): raise ValueError()
            blocks = []
            for item in reply['output']:
                if item['type'] == 'reasoning': continue
                if item['type'] != 'message' or item.get('role') != 'assistant': raise ValueError()
                for block in item['content']:
                    if block['type'] != 'output_text': raise ValueError()
                    blocks.append(block['text'])
            return ''.join(blocks)
        if self.kind == 'anthropic':
            if reply.get('stop_reason') != 'end_turn': raise ValueError()
            if any(block['type'] != 'text' for block in reply['content']): raise ValueError()
            return ''.join(block['text'] for block in reply['content'])
        if self.kind == 'openai_compatible':
            choice = reply['choices'][0]
            if choice.get('finish_reason') != 'stop': raise ValueError()
            message = choice['message']
        else:
            if reply.get('done') is not True or reply.get('done_reason', 'stop') != 'stop': raise ValueError()
            message = reply['message']
        if message.get('refusal') or message.get('tool_calls'): raise ValueError()
        return message['content']


def cli_factory(profile, context):
    validate_builtin(profile)
    claude = profile['kind'] == 'claude_cli'
    config = context.config
    cls = ClaudeAgent if claude else CodexAgent
    agent = cls(profile.get('command', config.claude_command if claude else config.codex_command),
                profile.get('model'), profile.get('timeout_seconds', config.agent_timeout), context.tools,
                config.context_limits, profile.get('env', config.planner_env),
                profile.get('home', config.claude_home if claude else config.planner_home))
    agent.identity = identity(profile)
    return agent


def register_builtins(api):
    for kind in HTTP_KINDS:
        api.providers.register(kind, HTTPModel, validate_config=validate_builtin)
    for kind in ('claude_cli', 'codex_cli'):
        api.providers.register(kind, cli_factory, validate_config=validate_builtin)


def provider_snapshot(config, agents, plugins):
    profiles = {item['id']: item for item in config.providers}
    rows = []
    for name, agent in agents.agents.items():
        spec = profiles.get(name, {})
        kind = spec.get('kind', name if name in ('claude', 'codex', 'demo') else 'plugin')
        credential = getattr(agent, 'key_env', None)
        status = ('credential present (not verified)' if os.environ.get(credential) else 'missing credential: ' + credential) if credential else 'configured (not probed)'
        if isinstance(agent, CodexAgent):
            status = 'CLI installed (login not probed)' if shutil.which(agent.command) else 'CLI executable missing'
        rows.append({'id': name, 'kind': kind, 'model': getattr(agent, 'model', None),
            'plugin': plugins.owners['agents'].get(name, 'application'), 'status': status,
            'targets': [target.id for target in config.targets if (target.agent or config.backend) == name]})
    return {'default': config.backend, 'available_kinds': sorted(plugins.providers.factories), 'providers': rows}
