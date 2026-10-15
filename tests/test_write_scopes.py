from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from opendots.config import Config, Target
from opendots.engine import Engine
from opendots.tools import ToolRegistry, digest


class WriteScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.target = Target('project', 'Project', 'Maintain source', self.source,
                             ({'types': ['input.*']},), {'write_file': 'auto', 'run_check': 'auto'},
                             write_paths=('*',), skills=('./instructions//GUIDE.md', 'instructions/guide[1].md'))
        self.registry = ToolRegistry('trusted-local')

    def write(self, path, content='old\n'):
        destination = self.source / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)

    def action(self, tool, path):
        args = {'path': path, 'expected_sha256': digest('old\n')}
        args.update({'content': 'new\n'} if tool == 'write_file' else
                    {'old_text': 'old', 'new_text': 'new'})
        return {'tool': tool, 'args': args}

    def test_protected_checks_tests_workflows_and_skills_reject_equivalent_paths(self):
        for path in ('check.py', 'tests/test_app.py', '.github/workflows/ci.yml', 'instructions/GUIDE.md', 'instructions/guide[1].md'):
            self.write(path)
            aliases = (path, './' + path, './/' + path.replace('/', '//./'))
            for tool in ('write_file', 'replace_text'):
                for alias in aliases:
                    with self.subTest(tool=tool, path=alias):
                        action = self.action(tool, alias)
                        original = deepcopy(action)
                        for operation in (self.registry.preview, self.registry.execute):
                            with self.assertRaisesRegex(ValueError, 'protected'):
                                operation(self.target, action)
                        self.assertEqual(action, original)
                        self.assertEqual((self.source / path).read_text(), 'old\n')

    def test_skill_symlink_also_protects_its_actual_instruction_file(self):
        self.write('instructions/actual.md')
        (self.source / 'instructions/current.md').symlink_to('actual.md')
        target = replace(self.target, skills=('./instructions/current.md',))
        for tool in ('write_file', 'replace_text'):
            for path in ('instructions/current.md', './instructions/actual.md'):
                with self.subTest(tool=tool, path=path), self.assertRaisesRegex(ValueError, 'protected'):
                    self.registry.execute(target, self.action(tool, path))
        self.assertEqual((self.source / 'instructions/actual.md').read_text(), 'old\n')

    def test_owner_scope_patterns_and_allowed_paths_share_normalization(self):
        self.write('src/app.py')
        target = replace(self.target, write_paths=('./src//*.py',), protected_paths=('./src//check.py',))
        self.write('src/check.py')
        for path in ('src/check.py', './src/./check.py'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'protected'):
                self.registry.execute(target, self.action('write_file', path))
        for tool, path in (('write_file', './src/./app.py'), ('replace_text', 'src//app.py')):
            with self.subTest(tool=tool, path=path):
                self.write('src/app.py')
                self.registry.execute(target, self.action(tool, path))
                self.assertEqual((self.source / 'src/app.py').read_text(), 'new\n')
        self.write('outside.py')
        with self.assertRaisesRegex(ValueError, 'scope'):
            self.registry.execute(target, self.action('write_file', './outside.py'))

    def test_alias_cannot_replace_owner_check_and_claim_runtime_completion(self):
        original = "from pathlib import Path\nassert Path('value.txt').read_text() == 'valid'\n"
        self.write('check.py', original)
        self.write('value.txt', 'broken')
        target = replace(self.target, checks={'validate': ['{python}', '-B', 'check.py']},
                         required_checks=('validate',))

        class Planner:
            def plan(self, *args):
                return {'summary': 'Replace validation through an equivalent path', 'actions': [
                    {'tool': 'write_file', 'args': {'path': './check.py', 'content': 'pass\n',
                                                  'expected_sha256': digest(original)}}]}

        engine = Engine(Config((target,), self.root / 'runtime/state.db', backend='demo',
                               sandbox='trusted-local'), agent=Planner())
        engine.ingest({'id': 'attempt', 'type': 'input.request'})
        result = engine.drain()
        self.assertEqual(result['counts'], {'failed': 1})
        work = result['work'][0]
        self.assertIn('protected', work['error'])
        self.assertEqual((Path(work['workspace']) / 'check.py').read_text(), original)
        self.assertEqual(engine.store.work_results(work['id']), [])
        self.assertEqual(engine.store.state(target.id)['artifacts'], [])
