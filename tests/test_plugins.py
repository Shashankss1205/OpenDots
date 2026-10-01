"""Plugin contract, durable listener lifecycle and compatibility integration tests."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from opendots.agents import AgentRegistry
from opendots.config import Config, Target, validate_config
from opendots.engine import Engine
from opendots.plugins import Plugin, PluginManager, PluginManifest
from opendots.server import make_server
from opendots.sources import SourceRegistry
from opendots.store import Store
from opendots.terminal import Client, Session
from opendots.tools import ToolRegistry


class Entry:
    def __init__(self, name, plugin):
        self.name, self.plugin, self.loads = name, plugin, 0

    def load(self):
        self.loads += 1
        return self.plugin


class Provider:
    def plan(self, target, event, state):
        return {"summary": "Record incoming information", "actions": [
            {"tool": "connector_action", "args": {"text": event["payload"]["title"]}}]}


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        workspace = self.root / "project"
        workspace.mkdir()
        (workspace / "readme.txt").write_text("User project")
        self.target = Target("project", "Project", "Improve the project", workspace,
            ({"types": ["input.*"]},), {"connector_action": "approval"}, write_paths=())
        self.config = Config((self.target,), self.root / "state.db", backend="demo", sandbox="trusted-local")

    def tearDown(self):
        self.temp.cleanup()

    def manager(self):
        return PluginManager(AgentRegistry(), ToolRegistry(), SourceRegistry((), Store(self.root / "plugins.db")))

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            threading.Event().wait(.01)
        self.fail("Timed out waiting for listener state")

    def test_builtin_ownership_and_demo_is_opt_in(self):
        engine = Engine(replace(self.config, backend="claude"))
        inventory = engine.plugins.snapshot()
        self.assertEqual(inventory["owners"]["tools"]["write_file"], "builtin.local")
        self.assertEqual(inventory["owners"]["sources"]["github_poll"], "builtin.github")
        self.assertEqual(inventory["owners"]["agents"]["claude"], "builtin.claude")
        self.assertNotIn("demo", engine.agents.agents)
        self.assertIn("demo", Engine(self.config).agents.agents)

    def test_one_plugin_registers_multiple_capabilities_and_actions_still_require_approval(self):
        calls = []
        def register(api):
            api.agents.register("connector_agent", Provider())
            api.tools.register("connector_action", lambda target, args: calls.append(args) or {"note": args["text"]}, ["text"])
            api.sources.register("connector_poll", lambda config: None)
        entry = Entry("connector", Plugin(PluginManifest("connector", "1.0"), register))
        with patch("opendots.plugins.entry_points", return_value=[entry]):
            engine = Engine(replace(self.config, plugins=("connector",), backend="connector_agent"))
        self.assertEqual(engine.plugins.snapshot()["plugins"][-1]["capabilities"], {
            "agents": ["connector_agent"], "tools": ["connector_action"], "sources": ["connector_poll"], "notifications": [], "providers": []})
        engine.ingest({"id": "request", "type": "input.report", "payload": {"title": "Fix a problem"}})
        work = engine.drain()["work"][0]
        self.assertEqual(work["status"], "waiting_approval")
        self.assertEqual(calls, [])
        engine.store.decide(work["id"], True, work["approval_token"])
        self.assertEqual(engine.drain()["counts"], {"completed": 1})
        self.assertEqual(calls, [{"text": "Fix a problem"}])

    def test_failed_registration_rolls_back_all_capabilities(self):
        manager = self.manager()
        before = manager.snapshot()
        def register(api):
            api.agents.register("new_agent", Provider())
            api.sources.register_listener("new_stream", lambda config: None)
            api.tools.register("new_tool", lambda target, args: {}, [])
            api.tools.register("note", lambda target, args: {}, [])
        with self.assertRaisesRegex(ValueError, "already registered"):
            manager.load(Plugin(PluginManifest("broken", "1"), register))
        self.assertEqual(manager.snapshot(), before)
        self.assertNotIn("new_tool", manager.tools.handlers)
        self.assertNotIn("new_stream", manager.sources.factories)
        self.assertNotIn("new_agent", manager.agents.agents)

    def test_api_config_and_duplicate_validation_precede_registration(self):
        calls = []
        manager = self.manager()
        schema = {"type": "object", "properties": {"room": {"type": "string"}}, "required": ["room"]}
        manifest = PluginManifest("validated", "1", config_schema=schema)
        plugin = Plugin(manifest, lambda api: calls.append(api.config))
        for candidate, config in [(replace(plugin, manifest=replace(manifest, api_version=999)), {"room": "a"}),
                                  (plugin, {}), (plugin, {"room": True}), (plugin, {"room": "a", "typo": 1})]:
            with self.assertRaises(ValueError):
                manager.load(candidate, config)
        self.assertEqual(calls, [])
        manager.load(plugin, {"room": "sensitive-value"})
        self.assertNotIn("sensitive-value", json.dumps(manager.snapshot()))
        with self.assertRaisesRegex(ValueError, "already loaded"):
            manager.load(plugin, {"room": "a"})

    def test_only_enabled_entry_points_load_and_legacy_api_still_works(self):
        versions = []
        disabled = Entry("disabled", lambda api: self.fail("Disabled plugin loaded"))
        legacy = Entry("legacy", lambda api: (versions.append(api.version), api.tools.register("old_tool", lambda target, args: {}, [])))
        with patch("opendots.plugins.entry_points", return_value=[disabled, legacy]):
            manager = self.manager()
            manager.load_installed(["legacy"])
        self.assertEqual(versions, [1])
        self.assertEqual(disabled.loads, 0)
        self.assertEqual(manager.snapshot()["owners"]["tools"]["old_tool"], "legacy")
        self.assertTrue(manager.snapshot()["plugins"][0]["legacy"])

    def test_missing_duplicate_and_mismatched_installed_plugins_fail_clearly(self):
        entry = Entry("wrong", Plugin(PluginManifest("different", "1"), lambda api: None))
        for entries, names, message in [([], ["missing"], "not installed"),
                ([entry, entry], ["wrong"], "Duplicate installed"),
                ([entry], ["wrong"], "must match"), ([entry], ["wrong", "wrong"], "unique")]:
            with self.subTest(message=message), patch("opendots.plugins.entry_points", return_value=entries):
                with self.assertRaisesRegex(ValueError, message):
                    self.manager().load_installed(names)

    def test_real_python_entry_point_discovery(self):
        # Exercise importlib's actual distribution discovery, not only a mocked registry.
        package = self.root / "contract_plugin.py"
        package.write_text('from opendots.plugins import Plugin, PluginManifest\n'
            'def register(api):\n    api.tools.register("package_tool", lambda target, args: {}, [])\n'
            'plugin = Plugin(PluginManifest("contract", "1.0"), register)\n')
        metadata = self.root / "contract_plugin-1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: contract-plugin\nVersion: 1.0\n")
        (metadata / "entry_points.txt").write_text("[opendots.plugins]\ncontract = contract_plugin:plugin\n")
        sys.path.insert(0, str(self.root))
        try:
            engine = Engine(replace(self.config, plugins=("contract",)))
            self.assertEqual(engine.plugins.snapshot()["owners"]["tools"]["package_tool"], "contract")
        finally:
            sys.path.remove(str(self.root))
            sys.modules.pop("contract_plugin", None)

    def listener_engine(self, listener, **changes):
        def register(api):
            api.sources.register_listener("stream", listener)
        entry = Entry("stream-plugin", Plugin(PluginManifest("stream-plugin", "1"), register))
        config = replace(self.config, plugins=("stream-plugin",), sources=({"id": "stream-1", "kind": "stream"},), **changes)
        with patch("opendots.plugins.entry_points", return_value=[entry]):
            return Engine(config)

    def test_listener_is_lazy_and_durable_replay_deduplicates_after_restart(self):
        observed = []
        class Listener:
            def __init__(self, config): pass
            def run(inner, context):
                observed.append(context.state)
                context.ready()
                context.emit({"id": "delivery", "type": "input.message", "payload": {"title": "Real input"}})
                context.checkpoint({"last_id": "delivery"})
                context.stop_event.wait()
        engine = self.listener_engine(Listener)
        engine.snapshot()
        self.assertEqual(observed, [])
        self.assertEqual(engine.listeners()["sources"][0]["health"]["status"], "not_running")
        engine.sources.start_listeners(engine.ingest)
        engine.sources.start_listeners(engine.ingest)
        try:
            self.wait_for(lambda: engine.sources._state("stream-1").get("cursor") == {"last_id": "delivery"})
            self.assertEqual(len(observed), 1)
            self.assertEqual(engine.listeners()["sources"][0]["health"]["status"], "listening")
        finally:
            engine.sources.close()
        self.assertFalse(engine.sources.listeners["stream-1"].is_alive())
        previous_received = engine.sources._state("stream-1")["last_received"]
        restarted = self.listener_engine(Listener)
        restarted.sources.start_listeners(restarted.ingest)
        try:
            self.wait_for(lambda: len(observed) == 2 and restarted.sources._state("stream-1").get("last_received", 0) > previous_received)
            self.assertEqual(observed[-1], {"last_id": "delivery"})
            self.assertEqual(len(restarted.store.events()["events"]), 1)
            self.assertEqual(len(restarted.snapshot()["work"]), 1)
        finally:
            restarted.sources.close()

    def test_capacity_failure_is_not_acknowledged_or_checkpointed(self):
        acknowledged = []
        class Listener:
            def __init__(self, config): pass
            def run(inner, context):
                context.ready()
                context.emit({"id": "second", "type": "input.message"})
                context.checkpoint({"last_id": "second"})
                acknowledged.append(True)
        engine = self.listener_engine(Listener, max_queued_per_target=1)
        engine.ingest({"id": "first", "type": "input.message"})
        engine.sources.start_listeners(engine.ingest)
        try:
            self.wait_for(lambda: engine.sources._state("stream-1").get("status") == "retrying")
            self.assertNotIn("cursor", engine.sources._state("stream-1"))
            self.assertEqual(acknowledged, [])
            self.assertEqual(len(engine.store.events()["events"]), 1)
        finally:
            engine.sources.close()

    def test_failed_listener_reconnects_and_does_not_starve_polling(self):
        attempts = []
        class Listener:
            def __init__(self, config): pass
            def run(inner, context):
                attempts.append(True)
                if len(attempts) == 1:
                    raise OSError("Transport disconnected")
                context.ready()
                context.emit({"id": "stream-delivery", "type": "input.message"})
                context.stop_event.wait()
        engine = self.listener_engine(Listener)
        inbox = self.root / "events.jsonl"
        inbox.write_text('{"id":"file-delivery","type":"input.message"}\n')
        engine.sources.configs += ({"id": "file", "kind": "jsonl", "path": str(inbox)},)
        engine.sources.workers = 1
        engine.sources.start_listeners(engine.ingest)
        try:
            self.wait_for(lambda: engine.sources._state("stream-1").get("status") == "retrying")
            engine.sources.poll_due(engine.ingest)
            self.assertEqual(engine.store.events()["events"][0]["id"], "file-delivery")
            self.wait_for(lambda: len(engine.store.events()["events"]) == 2)
            self.assertEqual(len(attempts), 2)
        finally:
            engine.sources.close()

    def test_worker_failure_stops_listener_and_releases_resources(self):
        stopped = threading.Event()
        started = threading.Event()
        class Listener:
            def __init__(self, config): pass
            def run(inner, context):
                started.set()
                try:
                    context.stop_event.wait()
                finally:
                    stopped.set()
        engine = self.listener_engine(Listener)
        def fail():
            self.assertTrue(started.wait(1))
            raise RuntimeError("Scheduler failed")
        with patch.object(engine, "_worker_loop", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "Scheduler failed"):
                engine.worker_loop()
        self.assertTrue(stopped.is_set())

    def test_callbacks_from_failed_listener_attempt_cannot_emit_or_move_cursor(self):
        contexts = []
        class Listener:
            def __init__(self, config): pass
            def run(inner, context):
                contexts.append(context)
                raise OSError("Disconnected")
        engine = self.listener_engine(Listener)
        engine.sources.start_listeners(engine.ingest)
        try:
            self.wait_for(lambda: engine.sources._state("stream-1").get("status") == "retrying")
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                contexts[0].emit({"id": "late", "type": "input.message"})
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                contexts[0].checkpoint({"cursor": "late"})
            self.assertEqual(engine.store.events()["events"], [])
        finally:
            engine.sources.close()

    def test_unknown_adapter_and_invalid_builtin_config_fail_before_start(self):
        for source in ({"id": "x", "kind": "missing"}, {"id": "x", "kind": "github_poll", "repo": "bad"},
                       {"id": "x", "kind": "jsonl"}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                Engine(replace(self.config, sources=(source,)))
        for raw in ({"targets": [], "plugins": ["x", "x"]},
                    {"targets": [], "plugin_config": {"not-enabled": {}}}):
            with self.assertRaises(ValueError):
                validate_config(raw)

    def test_api_and_tui_expose_inventory_without_starting_connections(self):
        engine = Engine(self.config)
        server = make_server(engine, port=0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            client = Client(f"http://127.0.0.1:{server.server_address[1]}")
            self.assertEqual(client.request("/api/plugins")["api_version"], 2)
            self.assertIn("builtin.github", Session(client).submit("/plugins"))
            self.assertEqual(engine.sources.listeners, {})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
