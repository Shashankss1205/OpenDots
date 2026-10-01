import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from opendots.setup import initialize, default_config
from opendots.config import load_config


class OnboardingTests(unittest.TestCase):
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
