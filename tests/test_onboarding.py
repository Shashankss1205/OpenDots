import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
from opendots.setup import initialize, default_config
from opendots.config import load_config


class OnboardingTests(unittest.TestCase):
    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, '-m', 'opendots', *args],
                              capture_output=True, text=True, env=env, timeout=10)

    def test_missing_config_cli_explains_setup_without_creating_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, XDG_CONFIG_HOME=str(root/'config'), XDG_DATA_HOME=str(root/'data'))
            explicit = root/'custom'/'settings.json'
            default = root/'config'/'opendots'/'config.json'
            for args, missing in ((('--config', str(explicit), 'doctor'), explicit), (('status',), default)):
                with self.subTest(path=missing):
                    result = self.cli(*args, env=env)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(str(missing), result.stderr)
                    self.assertIn('opendots init', result.stderr)
                    self.assertIn('--workspace', result.stderr)
                    self.assertIn('--goal', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertFalse(result.stdout)
                    self.assertEqual(list(root.iterdir()), [])

    def test_malformed_config_cli_preserves_the_parse_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.json'
            path.write_text('{')
            result = self.cli('--config', str(path), 'doctor')
            self.assertEqual(result.returncode, 1)
            self.assertIn('Expecting property name', result.stderr)
            self.assertNotIn('opendots init', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_existing_config_with_missing_workspace_is_not_a_setup_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root/'project'; project.mkdir()
            path = initialize(root/'config', project, goal='Maintain this project')
            project.rmdir()
            result = self.cli('--config', str(path), 'doctor')
            self.assertEqual(result.returncode, 1)
            self.assertIn('Workspace does not exist', result.stderr)
            self.assertNotIn('opendots init', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse((root/'config'/'data').exists())

    def test_no_implicit_demo_or_placeholder_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); project=root/'project';project.mkdir()
            with self.assertRaisesRegex(ValueError,'workspace'): initialize(root/'config')
            with self.assertRaisesRegex(ValueError,'goal'): initialize(root/'config',project)
            self.assertFalse((root/'config').exists())
            path=initialize(root/'config',project,goal='My actual objective',heartbeat=300)
            config=load_config(path)
            self.assertEqual(config.backend,'claude')
            self.assertEqual(config.targets[0].objective,'My actual objective')
            self.assertEqual(config.targets[0].write_paths,())
            self.assertEqual(config.targets[0].relevance['mode'],'model')
            self.assertFalse(config.targets[0].recipes)
            self.assertEqual(config.schedules[0]['interval_seconds'],300)
            self.assertFalse((path.parent/'workspaces').exists())

    def test_default_config_never_selects_checkout_demo(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'XDG_CONFIG_HOME':directory}):
            self.assertEqual(default_config(),Path(directory)/'opendots/config.json')

    def test_demo_requires_explicit_flag_and_no_real_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaises(ValueError):initialize(root/'bad',root,backend='demo',goal='Goal')
            with self.assertRaises(ValueError):initialize(root/'bad',demo=True,goal='Goal')
            config=load_config(initialize(root/'demo',demo=True))
            self.assertEqual(config.backend,'demo')
            self.assertEqual(len(config.targets),2)
