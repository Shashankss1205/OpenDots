from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from opendots.config import Config, Target
from opendots.engine import Engine
from opendots.store import CapacityError


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'
        source.mkdir()
        self.target = Target('project', 'Project', 'Record incoming work', source,
                             ({'types': ['input.*']},), {'note': 'auto'})
        self.schedule = {'id': 'heartbeat', 'interval_seconds': 60,
                         'target_id': 'project', 'type': 'input.heartbeat'}
        self.config = Config((self.target,), self.root / 'runtime/state.db',
                             backend='demo', sandbox='trusted-local',
                             max_queued_per_target=1, schedules=(self.schedule,))

    def ticks(self, engine):
        with engine.store.connect() as db:
            return {row['id']: row['last_tick'] for row in db.execute('SELECT * FROM schedule_ticks')}

    def test_full_target_does_not_block_other_schedules_and_retry_survives_restart(self):
        other_source = self.root / 'other'
        other_source.mkdir()
        other = replace(self.target, id='other', workspace=other_source)
        config = replace(self.config, targets=(self.target, other), schedules=(
            self.schedule, {**self.schedule, 'id': 'other-heartbeat', 'target_id': 'other'}))
        engine = Engine(config)
        engine.ingest({'id': 'backlog', 'type': 'input.request', 'target_id': 'project'})
        engine.tick_schedules(now=120)
        self.assertEqual(self.ticks(engine), {'other-heartbeat': 2})
        self.assertEqual(engine.snapshot()['event_count'], 2)
        with self.assertRaises(CapacityError):
            engine.ingest({'id': 'manual-retry', 'type': 'input.request', 'target_id': 'project'})

        restarted = Engine(config)
        backlog = restarted.store.claim(['project'])
        restarted.store.finish(backlog)
        restarted.tick_schedules(now=121)
        self.assertEqual(self.ticks(restarted), {'heartbeat': 2, 'other-heartbeat': 2})
        self.assertEqual(restarted.store.event('schedule:heartbeat:2')['target_id'], 'project')
        Engine(config).tick_schedules(now=122)
        self.assertEqual(restarted.snapshot()['event_count'], 3)

    def test_event_rate_pressure_defers_without_acknowledging_the_tick(self):
        engine = Engine(replace(self.config, max_events_per_minute=1))
        engine.ingest({'id': 'recent', 'type': 'unmatched'})
        engine.tick_schedules(now=120)
        self.assertEqual(self.ticks(engine), {})
        self.assertEqual(engine.snapshot()['event_count'], 1)
        later = time.time() + 61
        with patch('opendots.store.time.time', return_value=later):
            engine.tick_schedules(now=121)
        self.assertEqual(self.ticks(engine), {'heartbeat': 2})
        self.assertEqual(engine.snapshot()['event_count'], 2)

    def test_invalid_scheduled_events_still_report_their_error(self):
        invalid = {**self.schedule, 'payload': {'severity': []}}
        engine = Engine(replace(self.config, schedules=(invalid,)))
        with self.assertRaisesRegex(ValueError, 'Severity'):
            engine.tick_schedules(now=120)
        self.assertEqual(self.ticks(engine), {})

    def test_service_drains_full_queue_and_processes_deferred_heartbeat(self):
        engine = Engine(self.config)
        engine.ingest({'id': 'backlog', 'type': 'input.request', 'target_id': 'project'})
        tick = engine.tick_schedules
        errors = []

        def serve():
            try:
                engine.worker_loop()
            except Exception as exc:
                errors.append(exc)

        with patch.object(engine, 'tick_schedules', side_effect=lambda: tick(now=120)):
            thread = threading.Thread(target=serve)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while thread.is_alive() and time.monotonic() < deadline:
                    if engine.snapshot()['counts'].get('completed') == 2:
                        break
                    engine.stop_event.wait(.02)
                self.assertEqual(errors, [])
                self.assertEqual(engine.snapshot()['counts'], {'completed': 2})
                self.assertEqual(self.ticks(engine), {'heartbeat': 2})
            finally:
                engine.stop_event.set()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive(), 'Scheduler did not stop')
