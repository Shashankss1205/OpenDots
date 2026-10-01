from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from opendots.config import Config, Target, validate_config
from opendots.engine import Engine
from opendots.http_client import post_json
from opendots.notifications import NotificationDispatcher, WebhookNotification
from opendots.plugins import Plugin, PluginManifest
from opendots.server import make_server
from opendots.terminal import Client, Session


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        project = self.root / "project"; project.mkdir()
        (project / "file").write_text("Project contents")
        target = Target("project", "Project", "Maintain project", project,
                        ({"types": ["input.*"]},), {"note": "auto"})
        self.route = {"id": "log", "kind": "jsonl", "path": str(self.root / "alerts.jsonl")}
        self.config = Config((target,), self.root / "runtime/state.db", backend="demo",
                             sandbox="trusted-local", notifications=(self.route,))
        self.engine = Engine(self.config)

    def tearDown(self):
        self.engine.notifications.close()
        self.temp.cleanup()

    def log(self, kind="work_completed", target="project", detail=None):
        with self.engine.store.connect() as db:
            self.engine.store.log(db, kind, detail or {"summary": "private project text"}, target, 1)

    def dispatcher(self, configs=None):
        return NotificationDispatcher(self.engine.store, configs if configs is not None else (self.route,), self.engine.plugins.notifications)

    def test_future_only_filtering_durable_collection_and_minimal_payload(self):
        self.log()
        dispatcher = self.dispatcher(); dispatcher.collect()
        self.assertEqual(dispatcher.snapshot()["counts"], {})
        self.log("action_completed")
        self.log("approval_requested", detail={"approval_token": "SECRET", "preview": "private source"})
        dispatcher.collect(); dispatcher.collect()
        self.assertEqual(dispatcher.snapshot()["counts"], {"pending": 1})
        restarted = self.dispatcher(); restarted.deliver()
        self.assertEqual(restarted.snapshot()["counts"], {"delivered": 1})
        message = json.loads(Path(self.route["path"]).read_text())
        self.assertEqual(message["event"], "approval_requested")
        self.assertEqual(set(message), {"id", "event", "target_id", "work_id", "created"})
        self.assertNotIn("SECRET", json.dumps(message))

    def test_queue_pressure_preserves_cursor_without_losing_events(self):
        dispatcher = self.dispatcher(({**self.route, "max_pending": 1},)); dispatcher.collect()
        for _ in range(3): self.log()
        for _ in range(3):
            dispatcher.collect()
            self.assertEqual(dispatcher.snapshot()["counts"].get("pending"), 1)
            dispatcher.deliver()
        self.assertEqual(len(Path(self.route["path"]).read_text().splitlines()), 3)
        self.assertEqual(dispatcher.snapshot()["counts"], {"delivered": 3})

    def test_retry_and_recovery_keep_delivery_identity_and_redact_errors(self):
        calls = []
        class Broken:
            def __init__(inner, config): pass
            def send(inner, message):
                calls.append(message["id"])
                raise RuntimeError("https://secret-token@example.invalid/private")
        self.engine.plugins.notifications.register("broken", Broken)
        route = {"id": "remote", "kind": "broken", "max_attempts": 2}
        dispatcher = self.dispatcher((route,)); dispatcher.collect(); self.log(); dispatcher.collect()
        dispatcher.deliver()
        self.assertEqual(dispatcher.snapshot()["counts"], {"retry": 1})
        self.assertNotIn("secret-token", json.dumps(dispatcher.snapshot()))
        with self.engine.store.connect() as db:
            db.execute("UPDATE notifications SET status='sending'")
        restarted = self.dispatcher((route,)); restarted.recover(); restarted.deliver()
        self.assertEqual(restarted.snapshot()["counts"], {"failed": 1})
        self.assertEqual(calls, [calls[0], calls[0]])
        restarted.retry(calls[0])
        self.assertEqual(restarted.snapshot()["counts"], {"retry": 1})

    def test_changed_or_removed_destinations_do_not_receive_old_notifications(self):
        dispatcher = self.dispatcher(); dispatcher.collect(); self.log(); dispatcher.collect()
        changed = self.dispatcher(({**self.route, "path": str(self.root / "different.jsonl")},))
        changed.collect(); changed.deliver()
        self.assertEqual(changed.snapshot()["counts"], {"cancelled": 1})
        self.assertFalse((self.root / "different.jsonl").exists())
        with self.assertRaises(ValueError): changed.retry(dispatcher.snapshot()["deliveries"][0]["id"])
        disabled = self.dispatcher(()); disabled.collect()
        self.log()
        reenabled = self.dispatcher(); reenabled.collect(); reenabled.deliver()
        self.assertFalse(Path(self.route["path"]).exists())

    def test_environment_rotation_changes_destination_revision_without_exposing_secret(self):
        route = {"id": "remote", "kind": "webhook", "url_env": "TEST_NOTIFY_URL"}
        with patch.dict(os.environ, {"TEST_NOTIFY_URL": "https://one.invalid/secret-one"}):
            dispatcher = self.dispatcher((route,)); dispatcher.collect(); self.log(); dispatcher.collect()
        with patch.dict(os.environ, {"TEST_NOTIFY_URL": "https://two.invalid/secret-two"}):
            dispatcher.collect(); dispatcher.deliver()
        self.assertEqual(dispatcher.snapshot()["counts"], {"cancelled": 1})
        self.assertNotIn("secret-", json.dumps(dispatcher.snapshot()))

    def test_webhook_formats_receipts_and_redirects_against_local_http(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(302 if self.path == '/redirect' else 204)
                if self.path == '/redirect': self.send_header('Location', '/stolen')
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_address[1]}'
            message = {"id": "same-delivery", "event": "work_completed", "target_id": "project", "work_id": 1, "created": 1}
            for format in ('json', 'slack', 'discord'):
                with patch.dict(os.environ, {'TEST_NOTIFY_URL': url + '/hook'}):
                    WebhookNotification({'url_env': 'TEST_NOTIFY_URL', 'format': format}).send(message)
            self.assertEqual(requests[0][2], message)
            self.assertIn('text', requests[1][2])
            self.assertEqual(requests[2][2]['allowed_mentions'], {'parse': []})
            self.assertEqual(requests[0][1]['Idempotency-Key'], 'same-delivery')
            with self.assertRaisesRegex(RuntimeError, 'HTTP 302'):
                post_json(url + '/redirect', {}, {'Authorization': 'Bearer private-key'})
            self.assertEqual([r[0] for r in requests], ['/hook', '/hook', '/hook', '/redirect'])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_collection_failure_rolls_back_cursor_and_does_not_erase_audit(self):
        dispatcher = self.dispatcher(); dispatcher.collect(); self.log()
        with self.engine.store.connect() as db:
            db.execute("CREATE TRIGGER fail_delivery BEFORE INSERT ON notifications BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        with self.assertRaises(Exception): dispatcher.collect()
        with self.engine.store.connect() as db: db.execute("DROP TRIGGER fail_delivery")
        dispatcher.collect()
        self.assertEqual(dispatcher.snapshot()["counts"], {"pending": 1})

    def test_plugin_registration_rolls_back_notification_capability(self):
        manager = self.engine.plugins
        def register(api):
            api.notifications.register("custom", lambda config: None)
            api.tools.register("note", lambda target, args: {}, [])
        with self.assertRaises(ValueError):
            manager.load(Plugin(PluginManifest("broken-notifier", "1"), register))
        self.assertNotIn("custom", manager.notifications.factories)
        self.assertEqual(manager.snapshot()["owners"]["notifications"]["webhook"], "builtin.notifications")

    def test_real_task_lifecycle_queues_notification_without_network_in_engine_init(self):
        self.assertFalse(Path(self.route["path"]).exists())
        dispatcher = self.engine.notifications; dispatcher.collect()
        self.engine.ingest({"id": "task", "type": "input.request"})
        self.assertEqual(self.engine.drain()["counts"], {"completed": 1})
        dispatcher.collect(); dispatcher.deliver()
        self.assertEqual(json.loads(Path(self.route["path"]).read_text())["event"], "work_completed")

    def test_configuration_and_user_interfaces(self):
        for route in ({**self.route, "max_attempts": True}, {**self.route, "events": ["notification_failed"]}):
            with self.assertRaises(ValueError): validate_config({"targets": [], "notifications": [route]})
        with self.assertRaises(ValueError):
            Engine(replace(self.config, notifications=({**self.route, "path": str(self.config.targets[0].workspace / "alerts")},)))
        server = make_server(self.engine, port=0)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            client = Client(f'http://127.0.0.1:{server.server_address[1]}')
            self.assertEqual(client.request('/api/notifications')['destinations'][0]['id'], 'log')
            self.assertIn('log | jsonl', Session(client).submit('/notifications'))
        finally:
            server.shutdown(); server.server_close(); thread.join()
