from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from opendots.agents import RELEVANCE_SCHEMA
from opendots.config import Config, Target, load_config, validate_config
from opendots.engine import Engine
from opendots.model_providers import ProviderContext, HTTPModel, wire_schema, validate_profiles
from opendots.plugins import Plugin, PluginManifest
from opendots.server import make_server
from opendots.setup import initialize, diagnose
from opendots.terminal import Client, Session


class ModelProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); project = self.root / 'project'; project.mkdir()
        (project / 'readme.txt').write_text('Actual project context')
        self.target = Target('project', 'Project', 'Maintain this project', project,
                             ({'types': ['owner.*']},), {'note': 'approval'}, relevance={'mode': 'model'})
        self.config = Config((self.target,), self.root / 'state.db', backend='chosen', sandbox='trusted-local')
        self.requests = []; self.reply = None; self.status = 200
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append((self.path, dict(self.headers), body))
                text = json.dumps(body)
                value = {'decision': 'relevant', 'confidence': .9, 'reason': 'The event concerns the goal'} if 'Assess whether' in text else {
                    'summary': 'Record investigation', 'outcome': 'complete',
                    'actions': [{'tool': 'note', 'args': {'text': 'Investigated the supplied context'}}]}
                if self.path.endswith('/responses'):
                    reply = {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}
                elif self.path.endswith('/messages'):
                    reply = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(value)}]}
                elif self.path.endswith('/completions'):
                    reply = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}
                else:
                    reply = {'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps(value)}}
                self.send_response(owner.status); self.end_headers()
                self.wfile.write(json.dumps(owner.reply if owner.reply is not None else reply).encode())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.env = patch.dict(os.environ, {'TEST_MODEL_KEY': 'local-fixture-secret'}); self.env.start(); self.addCleanup(self.env.stop)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def profile(self, kind='openai', **kwargs):
        return {'id': 'chosen', 'kind': kind, 'model': 'test-model', 'base_url': self.url,
                'api_key_env': 'TEST_MODEL_KEY', **kwargs}

    def engine(self, kind='openai', **kwargs):
        profile = self.profile(kind, **kwargs)
        return Engine(replace(self.config, providers=(profile,)))

    def test_all_transports_assess_plan_and_preserve_approval_and_call_budget(self):
        for index, kind in enumerate(('openai', 'anthropic', 'openai_compatible', 'ollama')):
            with self.subTest(kind=kind):
                engine = Engine(replace(self.config, database=self.root / f'{kind}.db', providers=(self.profile(kind),)))
                engine.ingest({'type': 'owner.request', 'payload': {'title': 'Please investigate'}})
                work = engine.drain()['work'][0]
                self.assertEqual(work['status'], 'waiting_approval')
                self.assertEqual(engine.store.state('project')['notes'], [])
                self.assertEqual(len(self.requests), (index+1)*2)
                path, headers, body = self.requests[-1]
                self.assertEqual(body['model'], 'test-model'); self.assertNotIn('tools', body)
                self.assertIn('Actual project context', json.dumps(body))
                self.assertNotIn('Actual project context', json.dumps(self.requests[-2][2]))
                self.assertEqual(headers.get('X-Api-Key') if kind == 'anthropic' else headers.get('Authorization'),
                                 'local-fixture-secret' if kind == 'anthropic' else 'Bearer local-fixture-secret')
                if kind == 'openai':
                    self.assertFalse(body['store']); self.assertEqual(body['text']['format']['type'], 'json_schema')
                elif kind == 'anthropic':
                    self.assertEqual(headers['Anthropic-Version'], '2023-06-01')
                    self.assertNotIn('minimum', self.requests[-2][2]['output_config']['format']['schema']['properties']['confidence'])
                elif kind == 'ollama': self.assertFalse(body['stream']); self.assertIn('properties', body['format'])
                else: self.assertTrue(body['response_format']['json_schema']['strict'])
                engine.store.decide(work['id'], True, work['approval_token'])
                self.assertEqual(engine.drain()['counts'], {'completed': 1})
                self.assertEqual(len(self.requests), (index+1)*2)
                self.assertEqual(engine.snapshot()['metrics']['planning_calls_today'][0]['calls'], 2)

    def test_cloud_failures_do_not_retry_disclose_body_or_fall_back(self):
        self.status = 429; self.reply = {'error': 'local-fixture-secret and private data'}
        engine = self.engine(); engine.ingest({'type': 'owner.request'})
        work = engine.drain()['work'][0]
        self.assertEqual(work['status'], 'blocked'); self.assertEqual(len(self.requests), 1)
        state = json.dumps(engine.snapshot())
        self.assertIn('HTTP 429', state); self.assertNotIn('local-fixture-secret', state)
        self.assertEqual(engine.snapshot()['metrics']['planning_calls_today'][0]['calls'], 1)

    def test_malformed_refused_truncated_and_native_tool_outputs_fail(self):
        cases = [('openai', {'status': 'incomplete', 'output': []}),
            ('openai', {'status': 'completed', 'output': [{'type': 'function_call', 'name': 'bad'}]}),
            ('anthropic', {'stop_reason': 'max_tokens', 'content': []}),
            ('anthropic', {'stop_reason': 'refusal', 'content': []}),
            ('openai_compatible', {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]}),
            ('ollama', {'done': False, 'message': {'content': '{}'}}),
            ('ollama', {'done': True, 'message': {'content': '{}', 'tool_calls': [{}]}})]
        for kind, reply in cases:
            with self.subTest(kind=kind, reply=reply):
                self.reply = reply
                with self.assertRaisesRegex(ValueError, 'structured response'):
                    self.engine(kind).agents.get('chosen').assess_relevance(self.target, {})
        for content in ('```json\n{}\n```', '[]', '{"decision":"relevant","confidence":NaN,"reason":"bad"}'):
            self.reply = {'done': True, 'message': {'content': content}}
            with self.assertRaises(ValueError): self.engine('ollama').agents.get('chosen').assess_relevance(self.target, {})

    def test_original_relevance_constraints_are_enforced_after_anthropic_wire_conversion(self):
        self.reply = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({
            'decision': 'relevant', 'confidence': 4, 'reason': 'Invalid'})}]}
        with self.assertRaisesRegex(ValueError, 'range'):
            self.engine('anthropic').agents.get('chosen').assess_relevance(self.target, {})
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['minimum'],
                  'properties': {'minimum': {'type': 'number', 'minimum': 1}}}
        self.assertIn('minimum', wire_schema(schema, anthropic=True)['properties'])
        self.assertNotIn('minimum', wire_schema(schema, anthropic=True)['properties']['minimum'])

    def test_json_object_compatibility_and_optional_tool_arguments(self):
        engine = self.engine('openai_compatible', structured_output='json_object')
        engine.registry.register('optional', lambda target, args: {}, [], schema={
            'type': 'object', 'properties': {'text': {'type': 'string'}}, 'additionalProperties': False})
        engine.agents.get('chosen').plan(self.target, {}, {})
        self.assertEqual(self.requests[-1][2]['response_format'], {'type': 'json_object'})
        strict = self.engine()
        strict.registry.register('optional', lambda target, args: {}, [], schema={
            'type': 'object', 'properties': {'text': {'type': 'string'}}, 'additionalProperties': False})
        with self.assertRaisesRegex(ValueError, 'optional tool arguments'):
            strict.agents.get('chosen').plan(self.target, {}, {})

    def test_missing_invalid_credentials_and_ollama_without_auth(self):
        for value in ('', 'bad\nsecret', 'secret\u00e9'):
            with patch.dict(os.environ, {'TEST_MODEL_KEY': value}):
                with self.assertRaisesRegex(RuntimeError, 'credential environment'):
                    self.engine().agents.get('chosen').assess_relevance(self.target, {})
        self.assertEqual(self.requests, [])
        profile = self.profile('ollama'); del profile['api_key_env']
        engine = Engine(replace(self.config, providers=(profile,)))
        engine.agents.get('chosen').assess_relevance(self.target, {})
        self.assertNotIn('Authorization', self.requests[-1][1])

    def test_profile_changes_invalidate_relevance_cache(self):
        engine = self.engine(); engine.ingest({'type': 'owner.request'})
        work = engine.drain()['work'][0]
        engine.store.decide(work['id'], True, work['approval_token'])
        changed = self.engine(model='different-model')
        self.assertEqual(changed.drain()['counts'], {'completed': 1})
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[-1][2]['model'], 'different-model')

    def test_invalid_profiles_and_alias_collisions_fail_before_model_calls(self):
        for changed in ({'model': ''}, {'base_url': 'http://remote.example'}, {'base_url': self.url+'?key=secret'},
                        {'api_key': 'secret'}, {'timeout_seconds': float('nan')}, {'max_output_tokens': True},
                        {'api_key_env': 'not an env'}, {'kind': 'anthropic', 'structured_output': 'json_object'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.engine(**changed)
        with self.assertRaises(ValueError): validate_profiles([self.profile(), self.profile()])
        with self.assertRaisesRegex(ValueError, 'already registered'):
            self.engine(id='claude')
        self.assertEqual(self.requests, [])

    def test_named_cli_profiles_and_public_inventory(self):
        profile = {'id': 'writer', 'kind': 'claude_cli', 'model': 'chosen-model', 'command': '/missing-claude', 'home': str(self.root / 'profile')}
        config = replace(self.config, backend='writer', providers=(profile, self.profile('ollama')))
        engine = Engine(config)
        self.assertEqual(engine.agents.get('writer').model, 'chosen-model')
        self.assertEqual(engine.agents.get('writer').environment()['CLAUDE_CONFIG_DIR'], profile['home'])
        inventory = engine.provider_snapshot()
        self.assertEqual(next(row for row in inventory['providers'] if row['id']=='writer')['targets'], ['project'])
        self.assertNotIn(self.url, json.dumps(inventory)); self.assertNotIn('local-fixture-secret', json.dumps(inventory))
        self.assertEqual(engine.plugins.owners['agents']['writer'], 'builtin.models')
        self.assertEqual(self.requests, [])

    def test_plugin_provider_registration_and_profile_loading_are_transactional(self):
        engine = self.engine(); manager = engine.plugins
        def broken(api):
            api.providers.register('partial', lambda spec, context: None)
            api.agents.register('claude', object())
        with self.assertRaises(ValueError): manager.load(Plugin(PluginManifest('broken', '1'), broken))
        self.assertNotIn('partial', manager.providers.factories)
        class Custom:
            def plan(self, target, event, state): return {'summary': 'Custom', 'actions': []}
        manager.load(Plugin(PluginManifest('custom', '1'), lambda api: api.providers.register('custom', lambda spec, context: Custom())))
        context = ProviderContext(self.config, engine.registry)
        with self.assertRaises(ValueError):
            manager.configure_providers([{'id': 'new', 'kind': 'custom'}, {'id': 'claude', 'kind': 'custom'}], context)
        self.assertNotIn('new', engine.agents.agents)
        manager.configure_providers([{'id': 'new', 'kind': 'custom'}], context)
        self.assertEqual(manager.owners['agents']['new'], 'custom')

    def test_init_doctor_and_interfaces_do_not_call_models(self):
        path = initialize(self.root / 'config', self.target.workspace, 'openai', 'Maintain project',
                          model='test-model', base_url=self.url, api_key_env='TEST_MODEL_KEY')
        raw = json.loads(path.read_text()); raw['sandbox']='trusted-local'; path.write_text(json.dumps(raw))
        self.assertTrue(diagnose(path)['ok']); self.assertFalse(load_config(path).database.exists())
        with patch.dict(os.environ, {'TEST_MODEL_KEY': ''}): self.assertFalse(diagnose(path)['ok'])
        engine = Engine(load_config(path)); server = make_server(engine, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            client = Client('http://127.0.0.1:' + str(server.server_port))
            self.assertEqual(client.request('/api/providers')['default'], 'openai')
            session = Session(client); session.refresh()
            self.assertIn('test-model', session.submit('/providers'))
        finally:
            server.shutdown(); server.server_close(); thread.join()
        result = subprocess.run([sys.executable, '-m', 'opendots', '--config', str(path), 'providers'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr); self.assertIn('test-model', result.stdout)
        self.assertEqual(self.requests, [])
        with self.assertRaises(ValueError): initialize(self.root/'bad', self.target.workspace, 'openai', 'Goal')


if __name__ == '__main__': unittest.main()
