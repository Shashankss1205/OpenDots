import hashlib
from pathlib import Path
import tempfile
import unittest

from opendots.config import Config
from opendots.engine import Engine
from opendots.store import CapacityError


class JSONLGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = self.root / 'events.jsonl'
        self.first = b'{"type":"input.repeat"}\n'
        self.second = b'{"type":"input.other"}\n'
        self.inbox.write_bytes(self.first)
        self.config = Config((), self.root / 'runtime/state.db', backend='demo',
                             sources=({'id': 'inbox', 'kind': 'jsonl',
                                       'path': str(self.inbox), 'interval_seconds': 1},))

    def poll(self, now):
        engine = Engine(self.config)
        engine.sources.poll_due(engine.ingest, now=now)
        return engine

    def rotate(self, contents):
        replacement = self.root / 'replacement.jsonl'
        replacement.write_bytes(contents)
        replacement.replace(self.inbox)

    def test_identical_record_in_replacement_file_is_a_new_occurrence(self):
        engine = self.poll(10)
        original_id = engine.store.events()['events'][0]['id']
        self.rotate(self.first)
        engine = self.poll(20)
        events = engine.store.events()['events']
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]['id'], original_id)
        self.assertEqual(self.poll(30).snapshot()['event_count'], 2)

    def test_detected_copy_truncation_separates_repeated_occurrences(self):
        self.inbox.write_bytes(self.first + self.second)
        inode = self.inbox.stat().st_ino
        self.assertEqual(self.poll(10).snapshot()['event_count'], 2)
        self.inbox.write_bytes(self.first)
        self.assertEqual(self.inbox.stat().st_ino, inode)
        self.assertEqual(self.poll(20).snapshot()['event_count'], 3)
        with self.inbox.open('ab') as handle:
            handle.write(self.second)
        self.assertEqual(self.poll(30).snapshot()['event_count'], 4)
        self.inbox.write_bytes(self.first)
        self.assertEqual(self.poll(40).snapshot()['event_count'], 5)

    def test_partial_delivery_retry_keeps_ids_stable_across_restart(self):
        engine = self.poll(10)
        self.rotate(self.first + self.second)
        attempts = []

        def limited_ingest(event):
            attempts.append(event['id'])
            if len(attempts) == 2:
                raise CapacityError('Controlled queue pressure')
            return engine.ingest(event)

        engine.sources.poll_due(limited_ingest, now=20)
        self.assertEqual(engine.snapshot()['event_count'], 2)
        restarted = self.poll(60)
        self.assertEqual(restarted.snapshot()['event_count'], 3)
        self.assertEqual(restarted.store.event(attempts[0])['type'], 'input.repeat')
        self.assertEqual(restarted.store.event(attempts[1])['type'], 'input.other')
        self.assertEqual(self.poll(80).snapshot()['event_count'], 3)

    def test_producer_supplied_ids_still_deduplicate_across_file_generations(self):
        explicit = b'{"id":"upstream-delivery","type":"input.repeat"}\n'
        self.inbox.write_bytes(explicit)
        self.poll(10)
        self.rotate(explicit)
        engine = self.poll(20)
        self.assertEqual(engine.snapshot()['event_count'], 1)
        self.assertEqual(engine.store.events()['events'][0]['id'], 'upstream-delivery')

    def test_legacy_unacknowledged_batch_keeps_original_generated_ids(self):
        # Materialize an older partially delivered batch: receipt is durable but
        # its byte cursor was not advanced, and no generation field existed.
        engine = Engine(self.config)
        old_id = 'jsonl:inbox:0:' + hashlib.sha256(self.first).hexdigest()
        engine.ingest({'id': old_id, 'type': 'input.repeat', 'source': 'file'})
        stat = self.inbox.stat()
        engine.sources._health('inbox', offset=0, identity=[stat.st_dev, stat.st_ino])
        engine = self.poll(10)
        self.assertEqual(engine.snapshot()['event_count'], 1)
        with self.inbox.open('ab') as handle:
            handle.write(self.second)
        self.assertEqual(self.poll(20).snapshot()['event_count'], 2)
