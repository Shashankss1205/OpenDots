from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from opendots.agents import ClaudeAgent
from opendots.config import Config, Target, load_config
from opendots.engine import Engine
from opendots.setup import initialize, diagnose


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root/'source'; self.source.mkdir()
        self.target = Target('project','Project','Improve this project',self.source,
                             ({'types':['owner.*','timer.heartbeat']},), {'note':'approval'})

    def test_subprocess_protocol_and_exact_approval(self):
        # Exercise the real subprocess boundary with a controlled CLI fixture.
        command = self.root/'claude-test'
        command.write_text('#!'+sys.executable+'''\nimport json, sys, os
args=sys.argv[1:]
assert '--print' in args and '--safe-mode' in args
assert args[args.index('--tools')+1] == ''
assert args[args.index('--permission-mode')+1] == 'plan'
assert args[args.index('--disallowedTools')+1] == 'mcp__*'
assert '--strict-mcp-config' in args and '--no-session-persistence' in args
assert 'GITHUB_TOKEN' not in os.environ
schema=json.loads(args[args.index('--json-schema')+1]); assert 'actions' in schema['properties']
prompt=sys.stdin.read(); assert 'Improve this project' in prompt and 'event_untrusted' in prompt
print(json.dumps({'type':'result','subtype':'success','is_error':False,'structured_output':{
 'summary':'Record investigation','outcome':'complete','actions':[{'tool':'note','args':{'text':'Inspected project context'}}]}}))
'''); command.chmod(0o755)
        config = Config((self.target,),self.root/'state.db',backend='claude',
                        claude_command=str(command),sandbox='trusted-local')
        with patch.dict(os.environ,{'GITHUB_TOKEN':'fixture-not-a-real-token'}):
            engine=Engine(config); engine.ingest({'type':'owner.request'}); state=engine.drain()
        work=state['work'][0]; self.assertEqual(work['status'],'waiting_approval')
        self.assertEqual(engine.store.state('project')['notes'],[])
        engine.store.decide(work['id'],True,work['approval_token'])
        self.assertEqual(engine.drain()['counts'],{'completed':1})
        self.assertEqual(engine.snapshot()['metrics']['planning_calls_today'][0]['calls'],1)

    def test_reads_complete_output_and_rejects_invalid_envelopes(self):
        plan={'summary':'Large reply','outcome':'complete','actions':[{'tool':'note','args':{'text':'x'*70000}}]}
        def invoke(payload, code=0):
            def fake(argv,cwd,timeout,stdin,env=None,output_path=None,output_limit=None):
                self.assertEqual(output_limit,1_000_000)
                output_path.write_text(json.dumps(payload) if not isinstance(payload,str) else payload)
                return code,'truncated preview must not be parsed'
            return fake
        with patch('opendots.agents.shutil.which',return_value='/claude'):
            with patch('opendots.agents.bounded_process',side_effect=invoke({'subtype':'success','structured_output':plan})):
                self.assertEqual(ClaudeAgent().plan(self.target,{},{}),plan)
            for payload in ('bad JSON',{'subtype':'success'},{'subtype':'error_max_turns','is_error':True}):
                with self.subTest(payload=payload), patch('opendots.agents.bounded_process',side_effect=invoke(payload)):
                    with self.assertRaises((ValueError,RuntimeError)):ClaudeAgent().plan(self.target,{},{})
            with patch('opendots.agents.bounded_process',side_effect=invoke({},1)):
                with self.assertRaisesRegex(RuntimeError,'exit code 1'):ClaudeAgent().plan(self.target,{},{})

    def test_missing_provider_fails_without_demo_fallback(self):
        config=Config((self.target,),self.root/'state.db',backend='claude',claude_command='opendots-missing-claude')
        engine=Engine(config); engine.ingest({'type':'owner.request'})
        self.assertEqual(engine.drain()['counts'],{'failed':1})
        self.assertIn('Claude CLI is not installed',engine.snapshot()['work'][0]['error'])

    def test_init_and_doctor_use_claude_profile(self):
        path=initialize(self.root/'config',self.source,'claude',goal='Improve this project')
        raw=json.loads(path.read_text());raw.update(sandbox='trusted-local',claude_home='profile')
        path.write_text(json.dumps(raw));config=load_config(path)
        self.assertEqual(config.backend,'claude')
        self.assertEqual(config.claude_home,str(path.parent/'profile'))
        from types import SimpleNamespace
        with patch('opendots.setup.shutil.which',side_effect=lambda name:'/usr/bin/'+name), patch('opendots.setup.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(diagnose(path)['ok'])
            self.assertEqual(run.call_args.args[0],['/usr/bin/claude','auth','status'])
            self.assertEqual(run.call_args.kwargs['env']['CLAUDE_CONFIG_DIR'],config.claude_home)
            self.assertNotIn('CODEX_HOME',run.call_args.kwargs['env'])

    def test_claude_credentials_and_mcp_config_are_excluded(self):
        from opendots.tools import confined_path
        for name in ('.claude','.claude.json','.mcp.json'):
            path=self.source/name
            if name == '.claude':
                path.mkdir(); (path/'credentials.json').write_text('private')
            else:path.write_text('private')
            with self.assertRaisesRegex(ValueError,'credential'):
                confined_path(self.source,name+'/credentials.json' if path.is_dir() else name)
        engine=Engine(Config((self.target,),self.root/'state.db',sandbox='trusted-local',backend='demo'))
        engine.ingest({'type':'owner.request'}); engine.drain()
        work=engine.snapshot()['work'][0]
        self.assertTrue(all(not (Path(work['workspace'])/name).exists() for name in ('.claude','.claude.json','.mcp.json')))
