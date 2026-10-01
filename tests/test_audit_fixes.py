import tempfile
import unittest
from pathlib import Path
from opendots.config import Target
from opendots.tools import ToolRegistry, digest, confined_path

class AuditFixTests(unittest.TestCase):
    def test_symlink_cannot_bypass_write_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'private.txt').write_text('old')
            (root / 'allowed.txt').symlink_to('private.txt')
            target = Target('t', 'T', 'Test', root, (), {}, write_paths=('allowed.txt',))
            with self.assertRaisesRegex(ValueError, 'symlink'):
                ToolRegistry().execute(target, {'tool':'write_file', 'args':{
                    'path':'allowed.txt', 'content':'new', 'expected_sha256':digest('old')}})
            self.assertEqual((root / 'private.txt').read_text(), 'old')

    def test_alias_to_protected_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('fixture')
            (root / 'alias').symlink_to('.env')
            with self.assertRaisesRegex(ValueError, 'credential'):
                confined_path(root, 'alias')

    def test_changed_check_requires_fresh_approval(self):
        from dataclasses import replace
        from opendots.config import Config
        from opendots.engine import Engine
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); workspace = root / 'source'; workspace.mkdir()
            target = Target('t', 'T', 'Test', workspace, ({'types':['test']},),
                            {'run_check':'approval'}, checks={'check':['{python}','-c','print("old")']})
            class Planner:
                def plan(self, *args):
                    return {'summary':'Check', 'actions':[{'tool':'run_check','args':{'name':'check'}}]}
            config = Config((target,), root/'state.db', sandbox='trusted-local')
            engine = Engine(config, agent=Planner()); engine.ingest({'type':'test'}); engine.drain()
            work = engine.snapshot()['work'][0]
            changed = replace(target, checks={'check':['{python}','-c','print("new")']})
            resumed = Engine(replace(config, targets=(changed,)), agent=Planner())
            resumed.store.decide(work['id'], True, work['approval_token']); resumed.drain()
            refreshed = resumed.snapshot()['work'][0]
            self.assertEqual(refreshed['status'], 'waiting_approval')
            self.assertNotEqual(refreshed['approval_token'], work['approval_token'])
            self.assertEqual(resumed.store.work_results(work['id']), [])

    def test_large_retained_patch_applies_exactly(self):
        import subprocess
        from opendots.config import Config
        from opendots.engine import Engine
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'source'; source.mkdir()
            (source/'a.txt').write_text('old\n')
            target = Target('t','T','Test',source,({'types':['test']},),{'write_file':'auto'})
            content = 'new line\n' * 15000
            class Planner:
                def plan(self,*args):
                    return {'summary':'Edit','actions':[{'tool':'write_file','args':{
                        'path':'a.txt','content':content,'expected_sha256':digest('old\n')}}]}
            engine = Engine(Config((target,),root/'state.db',sandbox='trusted-local'),agent=Planner())
            engine.ingest({'type':'test'}); engine.drain()
            artifact = engine.store.state('t')['artifacts'][0]
            patch = Path(artifact['patch'])
            self.assertGreater(patch.stat().st_size, 65536)
            subprocess.run(['git','apply',str(patch)],cwd=source,check=True,capture_output=True)
            self.assertEqual((source/'a.txt').read_text(), content)

    def test_pending_approval_survives_history_window(self):
        from opendots.config import Config
        from opendots.engine import Engine
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source'; source.mkdir()
            target=Target('t','T','Test',source,({'types':['test']},),{'note':'approval'})
            engine=Engine(Config((target,),root/'state.db',sandbox='trusted-local'))
            engine.ingest({'id':'first','type':'test'});engine.drain()
            for n in range(205): engine.ingest({'id':str(n),'type':'test'})
            state=engine.snapshot()
            self.assertEqual(sum(w['status']=='waiting_approval' for w in state['work']),1)
            self.assertTrue(any(a['kind']=='approval_requested' for a in state['audit']))

    def test_github_comment_preserves_comment_and_issue(self):
        from opendots.sources import normalize_github
        event=normalize_github('issue_comment', {'action':'created','issue':{'number':7,'body':'issue'},
            'comment':{'id':9,'body':'comment','html_url':'https://example.test/comment'},
            'sender':{'login':'owner'}}, 'delivery')
        self.assertEqual(event['payload']['body'],'comment')
        self.assertEqual(event['payload']['issue_body'],'issue')
        self.assertEqual(event['payload']['number'],7)
        self.assertEqual(event['payload']['actor'],'owner')

    def test_desired_state_reconciles_on_restart(self):
        from dataclasses import replace
        from opendots.store import Store
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state.db')
            target=Target('t','T','Test',root,(),{},desired_state={'goal':'old'})
            store.register_targets((target,));old=store.state('t')['config_revision']
            store.register_targets((replace(target,desired_state={'goal':'new'}),))
            self.assertEqual(store.state('t')['desired_state'],{'goal':'new'})
            self.assertNotEqual(store.state('t')['config_revision'],old)

    def test_init_creates_external_config_without_overwrite(self):
        from opendots.setup import initialize
        from opendots.config import load_config
        with tempfile.TemporaryDirectory() as directory:
            path=initialize(Path(directory)/'config')
            config=load_config(path)
            self.assertEqual(len(config.targets),2)
            self.assertTrue(all(t.workspace.is_dir() for t in config.targets))
            with self.assertRaises(ValueError): initialize(path.parent)

    def test_failed_check_is_returned_to_bounded_repair_planner(self):
        from opendots.config import Config
        from opendots.engine import Engine
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source';source.mkdir();(source/'value').write_text('bad')
            target=Target('t','T','Repair',source,({'types':['test']},),{'write_file':'auto','run_check':'auto'},
                checks={'valid':['{python}','-c',"from pathlib import Path; assert Path('value').read_text() == 'good'"]},required_checks=('valid',))
            class Planner:
                def plan(self,target,event,state):
                    results=state['task_action_results']
                    actions=[]
                    if results:
                        assert results[-1]['result']['exit_code'] != 0
                        actions=[{'tool':'write_file','args':{'path':'value','content':'good','expected_sha256':digest('bad')}}]
                    return {'summary':'Validate','actions':actions+[{'tool':'run_check','args':{'name':'valid'}}]}
            engine=Engine(Config((target,),root/'state.db',sandbox='trusted-local'),agent=Planner())
            engine.ingest({'type':'test'});result=engine.drain()
            self.assertEqual(result['counts'],{'completed':1})
            self.assertEqual(result['work'][0]['repair_attempts'],1)

    def test_storage_cannot_be_nested_in_source(self):
        from opendots.config import Config
        from opendots.engine import Engine
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            target=Target('t','T','Test',root,(),{})
            with self.assertRaisesRegex(ValueError,'outside'):
                Engine(Config((target,),root/'.opendots/state.db'))
            self.assertFalse((root/'.opendots').exists())

    def test_sigterm_exits_service_cleanly(self):
        import json, subprocess, sys, select
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=root/'config.json'
            config.write_text(json.dumps({'database':str(root/'state.db'),'targets':[]}))
            process=subprocess.Popen([sys.executable,'-m','opendots','--config',str(config),'serve','--port','0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                ready,_,_=select.select([process.stdout],[],[],5)
                self.assertTrue(ready,'Service failed to start')
                self.assertIn(b'OpenDots:',process.stdout.readline())
                process.terminate()
                _,error=process.communicate(timeout=5)
                self.assertEqual(process.returncode,0,error)
            finally:
                if process.poll() is None: process.kill();process.communicate()

    def test_slow_source_does_not_block_dispatch(self):
        import threading, time
        from opendots.sources import SourceRegistry
        from opendots.store import Store
        with tempfile.TemporaryDirectory() as directory:
            release=threading.Event()
            class Slow:
                def __init__(self,config): pass
                def poll(self,state): release.wait(2); return [],state
            sources=SourceRegistry(({'id':'slow','kind':'slow'},),Store(Path(directory)/'state.db'))
            sources.register('slow',Slow)
            try:
                start=time.monotonic();sources.poll_due(lambda event:None,asynchronous=True)
                self.assertLess(time.monotonic()-start,.5)
            finally: release.set();sources.close()

    def test_jsonl_skips_poison_and_detects_rotation(self):
        import json, os
        from opendots.sources import JSONLSource
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.jsonl'
            path.write_text('invalid\n'+json.dumps({'type':'one'})+'\n')
            source=JSONLSource({'id':'file','path':str(path)})
            events,state=source.poll({})
            self.assertEqual(events[0]['type'],'one');self.assertEqual(len(state['rejected']),1)
            replacement=path.with_suffix('.new');replacement.write_text(json.dumps({'type':'replacement','payload':{'long':'x'*100}})+'\n')
            os.replace(replacement,path)
            events,state=source.poll(state)
            self.assertEqual(events[0]['type'],'replacement')

    def test_invalid_configuration_has_field_specific_errors(self):
        from opendots.config import validate_config
        for key,value in [('workers',False),('max_planning_rounds',0),('max_repair_attempts',-1),('source_workers',0)]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,key):
                validate_config({'targets':[],key:value})
        with self.assertRaisesRegex(ValueError,'checks.bad'):
            validate_config({'targets':[{'id':'t','name':'T','objective':'O','workspace':'.','checks':{'bad':'shell string'}}]})

    def test_process_output_is_bounded_while_captured(self):
        import sys
        from opendots.tools import bounded_process
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'output'
            with self.assertRaisesRegex(RuntimeError,'output exceeded'):
                bounded_process([sys.executable,'-c','import sys; sys.stdout.write("x"*1000000)'],directory,5,output_path=output,output_limit=10000)
            self.assertLessEqual(output.stat().st_size,10000)

    def test_pause_and_cancel_release_queue_safely(self):
        from opendots.store import Store
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state.db');target=Target('t','T','Test',root,(),{})
            store.register_targets((target,));store.ingest({'id':'e','type':'test'},[('t',50,True,'test')])
            store.pause('t',True);self.assertIsNone(store.claim(['t']))
            store.pause('t',False);work=store.claim(['t']);self.assertIsNotNone(work)
            store.cancel(work['id']);self.assertTrue(store.cancellation_requested(work['id']))

    def test_goal_conditions_measure_actual_file_state(self):
        from dataclasses import replace
        from opendots.goals import evaluate
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'state.json').write_text('{"replicas":0}')
            target=Target('t','T','Test',root,(),{},success_conditions=({'name':'available','path':'state.json','format':'json','pointer':'/replicas','minimum':2},))
            self.assertFalse(evaluate(target)[0]['passed'])
            (root/'state.json').write_text('{"replicas":2}')
            self.assertTrue(evaluate(target)[0]['passed'])

    def test_terminal_approval_requires_review_and_confirmation(self):
        from opendots.terminal import Session, safe_text
        calls=[]
        class Fake:
            def request(self,path,body=None):
                if body is not None: calls.append((path,body));return {}
                return {'targets':[],'work':[{'id':1,'target_id':'t','status':'waiting_approval','approval_token':'exact',
                    'plan':{'summary':'Proposed edit','actions':[{'tool':'write_file','args':{}}]},'approval_index':0}],'audit':[]}
        session=Session(Fake())
        with self.assertRaisesRegex(ValueError,'Review'):session.submit('/approve 1')
        session.submit('/review 1');session.submit('/approve 1');self.assertEqual(calls,[])
        session.submit('approve 1');self.assertEqual(calls[0][1]['approval_token'],'exact')
        self.assertNotIn('\x1b',safe_text('untrusted\x1b[2J'))

    def test_terminal_client_uses_real_runtime_approval_api(self):
        import threading
        from opendots.config import Config
        from opendots.engine import Engine
        from opendots.server import make_server
        from opendots.terminal import Client, Session
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            target=Target('t','T','Test',source,({'types':['owner.*']},),{'note':'approval'})
            engine=Engine(Config((target,),root/'state.db',sandbox='trusted-local'))
            server=make_server(engine,port=0);thread=threading.Thread(target=server.serve_forever);thread.start()
            try:
                session=Session(Client(f'http://127.0.0.1:{server.server_address[1]}'))
                session.submit('Investigate');engine.drain();session.submit('/review 1')
                session.submit('/approve 1');session.submit('approve 1')
                self.assertEqual(engine.drain()['counts'],{'completed':1})
                self.assertIsNone(session.submit('/quit'))
                self.assertEqual(session.client.request('/api/health')['status'],'ok')
            finally:server.shutdown();server.server_close();thread.join()

    def test_extensions_are_explicitly_enabled(self):
        from unittest.mock import patch
        from opendots.extensions import ExtensionAPI,load_extensions
        calls=[]
        class Entry:
            name='fixture'
            def load(self):return lambda api:calls.append(api.version)
        with patch('opendots.extensions.entry_points',return_value=[Entry()]):
            api=ExtensionAPI(None,None,None);load_extensions([],api);self.assertEqual(calls,[])
            load_extensions(['fixture'],api);self.assertEqual(calls,[1])
            with self.assertRaises(ValueError):load_extensions(['missing'],api)

    def test_custom_tools_accept_typed_nested_arguments(self):
        registry=ToolRegistry();registry.register('typed',lambda target,args:args,schema={'type':'object','properties':{
            'enabled':{'type':'boolean'},'values':{'type':'array','items':{'type':'integer'}}},'required':['enabled','values'],'additionalProperties':False})
        target=Target('t','T','Test',Path('.'),(),{})
        action={'tool':'typed','args':{'enabled':True,'values':[1,2]}}
        self.assertEqual(registry.execute(target,action),action['args'])
        action['args']['values']=[True]
        with self.assertRaisesRegex(ValueError,'integer'):registry.execute(target,action)

    def test_queue_capacity_rejects_without_acknowledging_event(self):
        from opendots.store import Store,CapacityError
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'state.db');store.queue_limit=1
            store.register_targets((Target('t','T','Test',root,(),{}),))
            matches=[('t',50,True,'test')]
            store.ingest({'id':'one','type':'test'},matches)
            with self.assertRaises(CapacityError):store.ingest({'id':'two','type':'test'},matches)
            self.assertEqual(store.snapshot()['event_count'],1)

    def test_owner_check_is_protected_even_with_broad_write_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'check.py').write_text('assert False')
            target=Target('t','T','Test',root,(),{},write_paths=('*',))
            with self.assertRaisesRegex(ValueError,'protected'):
                ToolRegistry().execute(target,{'tool':'write_file','args':{'path':'check.py','content':'pass','expected_sha256':digest('assert False')}})

    def test_snapshot_bounds_inventory_and_serialized_bytes(self):
        import json
        from opendots.agents import workspace_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for n in range(40):(root/f'{n}.txt').write_text('x'*100)
            entries,truncated=workspace_snapshot(root,{'max_files':3,'max_bytes':400})
            self.assertTrue(truncated);self.assertLessEqual(len(entries),3)
            self.assertLessEqual(len(json.dumps(entries).encode()),400)
