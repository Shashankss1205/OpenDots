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
