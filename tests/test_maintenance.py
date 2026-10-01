from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from opendots.config import Config, Target
from opendots.engine import Engine, process_lock
from opendots.maintenance import backup, restore, cleanup, workspace_root
from opendots.workspaces import git


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root/'source'; source.mkdir(); (source/'value').write_text('original')
        target = Target('t','T','Test',source,({'types':['test']},),{'note':'auto'})
        self.config = Config((target,),self.root/'runtime'/'state.db',sandbox='trusted-local')
        self.engine = Engine(self.config)
        self.config_path = self.root/'config.json'; self.config_path.write_text('{}')
        self.engine.ingest({'type':'test'}); self.engine.drain()

    def test_backup_restore_preserves_git_and_refuses_overwrite_or_corruption(self):
        destination = self.root/'backup'
        result = backup(self.config, destination, self.config_path)
        self.assertGreater(result['files'], 3)
        with self.assertRaisesRegex(ValueError,'overwrite'):
            restore(self.config,destination)
        changed = destination/'config.json'; changed.write_text('altered')
        with self.assertRaisesRegex(ValueError,'inventory'):
            restore(self.config,destination)
        changed.write_text('{}')
        with self.assertRaisesRegex(ValueError,'original'):
            restore(replace(self.config,database=self.root/'elsewhere.db'),destination)
        proposal = self.engine.workspaces.proposal(1)
        self.config.database.unlink()
        for suffix in ('-wal','-shm'):
            Path(str(self.config.database)+suffix).unlink(missing_ok=True)
        shutil.rmtree(workspace_root(self.config))
        restore(self.config,destination)
        recovered = Engine(self.config)
        self.assertEqual(recovered.workspaces.proposal(1),proposal)
        self.assertEqual(git(Path(proposal['workspace']),'rev-parse','HEAD'),proposal['commit'])
        recovered.workspaces.accept(1,proposal['commit'])

    def test_cleanup_previews_and_preserves_accepted_active_and_artifacts(self):
        proposal=self.engine.workspaces.proposal(1)
        self.engine.workspaces.accept(1,proposal['commit'])
        self.engine.ingest({'type':'test'}); self.engine.drain()
        second=self.engine.workspaces.proposal(2)
        self.engine.ingest({'type':'test'})
        with self.engine.store.connect() as db:
            db.execute('UPDATE work SET updated=?',(time.time()-40*86400,))
        preview=cleanup(self.config)
        self.assertTrue(preview['dry_run'])
        self.assertEqual([w['id'] for w in preview['workspaces']],[2])
        self.assertTrue(Path(second['workspace']).exists())
        cleanup(self.config,apply=True)
        self.assertFalse(Path(second['workspace']).exists())
        self.assertTrue(Path(proposal['workspace']).exists())
        self.assertTrue(Path(second['patch']).exists())
        self.assertEqual(self.engine.store.detail(2)['work']['status'],'completed')
        with self.assertRaisesRegex(ValueError,'archived'):
            self.engine.workspaces.accept(2,second['commit'])
        self.assertEqual(cleanup(self.config)['workspaces'],[])

    def test_maintenance_refuses_live_scheduler_and_recursive_destination(self):
        with process_lock(self.config.database):
            with self.assertRaisesRegex(RuntimeError,'scheduler'):
                backup(self.config,self.root/'backup',self.config_path)
            with self.assertRaisesRegex(RuntimeError,'scheduler'):
                cleanup(self.config,apply=True)
        with self.assertRaisesRegex(ValueError,'outside'):
            backup(self.config,self.config.database.parent/'backup',self.config_path)
