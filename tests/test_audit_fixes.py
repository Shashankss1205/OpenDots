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
