from pathlib import Path
import tempfile
import unittest

from opendots.config import Config
from opendots.engine import Engine


class SourceRejectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = self.root / 'inbox.jsonl'
        self.inbox.write_text('not-json\n')
        self.config = Config((), self.root / 'runtime/state.db', backend='demo',
                             sources=({'id': 'inbox', 'kind': 'jsonl',
                                       'path': str(self.inbox), 'interval_seconds': 1},))

    def rejections(self, engine):
        return [a for a in engine.store.snapshot()['audit'] if a['kind'] == 'source_record_rejected']

    def test_acknowledged_rejections_are_not_replayed_when_inbox_disappears(self):
        engine = Engine(self.config)
        engine.sources.poll_due(engine.ingest, now=10)
        self.assertEqual(len(self.rejections(engine)), 1)
        self.inbox.unlink()
        for now in (20, 30):
            restarted = Engine(self.config)
            restarted.sources.poll_due(restarted.ingest, now=now)
            self.assertEqual(len(self.rejections(restarted)), 1)
            state = restarted.sources._state('inbox')
            self.assertEqual(state['offset'], len('not-json\n'))
            self.assertNotIn('rejected', state)

    def test_legacy_persisted_diagnostics_are_not_new_rejections(self):
        engine = Engine(self.config)
        record = {'offset': 0, 'error': 'Previously acknowledged malformed record'}
        engine.sources._health('inbox', offset=9, rejected=[record])
        with engine.store.connect() as db:
            engine.store.log(db, 'source_record_rejected', {'source': 'inbox', **record})
        self.inbox.unlink()
        restarted = Engine(self.config)
        restarted.sources.poll_due(restarted.ingest, now=10)
        self.assertEqual(len(self.rejections(restarted)), 1)
        self.assertNotIn('rejected', restarted.sources._state('inbox'))

    def test_rejection_audit_failure_does_not_acknowledge_the_file_cursor(self):
        self.inbox.write_text('not-json\n{"id":"valid","type":"input.request"}\n')
        engine = Engine(self.config)
        with engine.store.connect() as db:
            db.execute("""CREATE TRIGGER fail_rejection BEFORE INSERT ON audit
                          WHEN NEW.kind='source_record_rejected'
                          BEGIN SELECT RAISE(ABORT, 'diagnostic write failed'); END""")
        engine.sources.poll_due(engine.ingest, now=10)
        self.assertEqual(self.rejections(engine), [])
        self.assertNotIn('offset', engine.sources._state('inbox'))
        with engine.store.connect() as db:
            db.execute('DROP TRIGGER fail_rejection')
        restarted = Engine(self.config)
        restarted.sources.poll_due(restarted.ingest, now=50)
        self.assertEqual(len(self.rejections(restarted)), 1)
        self.assertEqual(restarted.sources._state('inbox')['offset'], self.inbox.stat().st_size)
        self.assertEqual(restarted.store.snapshot()['event_count'], 1)
        restarted.sources.poll_due(restarted.ingest, now=60)
        self.assertEqual(len(self.rejections(restarted)), 1)
