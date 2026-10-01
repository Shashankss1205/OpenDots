from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from opendots.agents import CodexAgent
from opendots.config import Config, Target, load_config
from opendots.engine import Engine, process_lock
from opendots.server import make_server
from opendots.tools import ToolRegistry, confined_path, digest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "examples", self.root / "examples")
        self.config = load_config(self.root / "examples/config.json")
        # These cases isolate routing/policy; check enforcement has dedicated tests.
        self.config = replace(self.config, targets=tuple(replace(t, required_checks=()) for t in self.config.targets))
        if (os.environ.get("OPENDOTS_TEST_SANDBOX") or os.environ.get("SPOTS_TEST_SANDBOX")) == "trusted-local":
            self.config = replace(self.config, sandbox="trusted-local")
        self.engine = Engine(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def event(self, id="e", target="kubernetes", priority=80, type="github.discussion.created"):
        return {"id": id, "target_id": target, "type": type, "priority": priority,
                "payload": {"repo": "kubernetes/kubernetes" if target == "kubernetes" else "facebook/react"}}

    def test_five_event_acceptance_and_restart(self):
        for event in json.loads((self.root / "examples/five-events.json").read_text()):
            self.engine.ingest(event)
        snapshot = self.engine.drain()
        self.assertEqual(snapshot["counts"], {"queued": 1, "waiting_approval": 2})
        self.assertEqual(json.loads((self.root / "examples/workspaces/kubernetes/deployment.json").read_text())["spec"]["replicas"], 0)
        self.assertNotIn('aria-label', (self.root / "examples/workspaces/react/src/SearchButton.jsx").read_text())
        # Persistence survives a new Engine instance, including exact plan and action cursor.
        resumed = Engine(self.config)
        approvals = [w for w in snapshot["work"] if w["status"] == "waiting_approval"]
        for item in approvals:
            resumed.store.decide(item["id"], True)
        final = resumed.drain()
        self.assertEqual(final["counts"], {"completed": 3})
        checks = [a for a in final["audit"] if a["kind"] == "action_completed" and "exit_code" in a["detail"]["result"]]
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(a["detail"]["result"]["exit_code"] == 0 for a in checks))
        self.assertEqual(resumed.store.state("kubernetes")["completed"], 2)
        self.assertEqual(resumed.store.state("react")["completed"], 1)
        completed = {w["target_id"]: w for w in final["work"]}
        self.assertEqual(json.loads((Path(completed["kubernetes"]["workspace"]) / "deployment.json").read_text())["spec"]["replicas"], 2)
        self.assertEqual(json.loads((self.root / "examples/workspaces/kubernetes/deployment.json").read_text())["spec"]["replicas"], 0)
        self.assertTrue(resumed.store.state("react")["artifacts"])

    def test_duplicate_is_idempotent_and_collision_is_rejected(self):
        event = self.event()
        self.assertEqual(self.engine.ingest(event)["queued"], 1)
        self.assertTrue(self.engine.ingest(event)["duplicate"])
        with self.assertRaisesRegex(ValueError, "different payload"):
            self.engine.ingest({**event, "priority": 99})
        self.assertEqual(self.engine.snapshot()["event_count"], 1)

    def test_attention_and_unmatched_events_are_audited(self):
        self.assertEqual(self.engine.ingest(self.event(priority=5))["queued"], 0)
        self.assertEqual(self.engine.ingest({"id": "u", "type": "unmatched"})["queued"], 0)
        audit = self.engine.snapshot()["audit"]
        self.assertTrue(any(a["kind"] == "attention" and not a["detail"]["accepted"] for a in audit))
        self.assertTrue(any(a["kind"] == "event_ignored" for a in audit))

    def test_priority_is_highest_first_and_same_target_is_exclusive(self):
        self.engine.ingest(self.event("low", priority=30))
        self.engine.ingest(self.event("high", priority=90))
        first = self.engine.store.claim(self.engine.targets)
        self.assertEqual(first["event_id"], "high")
        self.assertIsNone(self.engine.store.claim(self.engine.targets))
        self.engine.store.finish(first)
        self.assertEqual(self.engine.store.claim(self.engine.targets)["event_id"], "low")

    def test_reject_releases_target_without_writing(self):
        self.engine.ingest(self.event(type="github.issue.opened"))
        self.engine.ingest(self.event("follow"))
        snapshot = self.engine.drain()
        waiting = next(w for w in snapshot["work"] if w["status"] == "waiting_approval")
        self.engine.store.decide(waiting["id"], False)
        with self.assertRaises(ValueError):
            self.engine.store.decide(waiting["id"], True)
        final = self.engine.drain()
        self.assertEqual(final["counts"], {"completed": 1, "rejected": 1})
        self.assertEqual(json.loads((self.root / "examples/workspaces/kubernetes/deployment.json").read_text())["spec"]["replicas"], 0)

    def test_approval_does_not_override_deny_policy(self):
        target = replace(self.config.targets[0], policy={"note": "deny"})
        engine = Engine(replace(self.config, targets=(target,)))
        engine.ingest(self.event())
        self.assertEqual(engine.drain()["counts"], {"blocked": 1})
        self.assertEqual(engine.store.state("kubernetes")["notes"], [])

    def test_changed_file_invalidates_approved_write(self):
        self.engine.ingest(self.event(type="github.issue.opened"))
        waiting = self.engine.drain()["work"][0]
        path = Path(waiting["workspace"]) / "deployment.json"
        path.write_text("{\"externally_changed\": true}")
        self.engine.store.decide(waiting["id"], True)
        final = self.engine.drain()
        self.assertEqual(final["counts"], {"failed": 1})
        self.assertEqual(json.loads(path.read_text()), {"externally_changed": True})

    def test_interrupted_actions_are_not_replayed(self):
        self.engine.ingest(self.event())
        self.engine.store.claim(self.engine.targets)
        self.assertEqual(Engine(self.config).drain()["counts"], {"interrupted": 1})

    def test_schedule_ticks_are_durable_and_deduplicated(self):
        schedule = {"id": "scout", "interval_seconds": 60, "target_id": "react", "type": "timer.scout",
                    "payload": {"repo": "facebook/react"}}
        engine = Engine(replace(self.config, schedules=(schedule,)))
        engine.tick_schedules(now=120)
        Engine(replace(self.config, schedules=(schedule,))).tick_schedules(now=121)
        self.assertEqual(engine.snapshot()["event_count"], 1)
        engine.tick_schedules(now=180)
        self.assertEqual(engine.snapshot()["event_count"], 2)

    def test_seven_targets_run_without_fixed_target_count(self):
        barrier = threading.Barrier(3)
        lock = threading.Lock()
        active = set()
        violations = []
        peaks = []
        calls = []

        class ConcurrentAgent:
            def plan(inner, target, event, state):
                with lock:
                    if target.id in active:
                        violations.append(target.id)
                    active.add(target.id)
                    peaks.append(len(active))
                    number = len(calls)
                    calls.append(target.id)
                if number < 3:
                    barrier.wait(timeout=3)
                time.sleep(.03)
                with lock:
                    active.remove(target.id)
                return {"summary": "Parallel task", "actions": [{"tool": "note", "args": {"text": event["id"]}}]}

        targets = []
        for i in range(7):
            workspace = self.root / f"target-{i}"
            workspace.mkdir()
            targets.append(Target(str(i), str(i), "Work", workspace, ({"types": ["test"]},), {"note": "auto"}))
        engine = Engine(Config(tuple(targets), self.root / "parallel.db", workers=3), agent=ConcurrentAgent())
        for i in range(7):
            for j in range(2):
                engine.ingest({"id": f"{i}:{j}", "type": "test", "target_id": str(i)})
        final = engine.drain()
        self.assertEqual(final["counts"], {"completed": 14})
        self.assertEqual(violations, [])
        self.assertEqual(max(peaks), 3)

    def test_filesystem_confinement_and_check_allowlist(self):
        target = self.config.targets[0]
        for path in ["../escape", "/etc/passwd", ".env", ".git/config"]:
            with self.assertRaises(ValueError):
                confined_path(target.workspace, path)
        (target.workspace / "escape").symlink_to(self.root)
        with self.assertRaises(ValueError):
            confined_path(target.workspace, "escape/anything")
        with self.assertRaisesRegex(ValueError, "allowlist"):
            ToolRegistry().execute(target, {"tool": "run_check", "args": {"name": "shell"}})

    def test_failed_check_stops_plan_before_success_note(self):
        target = replace(self.config.targets[0], recipes={"github.discussion.created": {
            "summary": "Validation should fail", "actions": [
                {"tool": "run_check", "args": {"name": "deployment"}},
                {"tool": "note", "args": {"text": "Should never be recorded"}}]}})
        engine = Engine(replace(self.config, targets=(target,)))
        engine.ingest(self.event())
        final = engine.drain()
        self.assertEqual(final["counts"], {"failed": 1})
        self.assertEqual(engine.store.state(target.id)["notes"], [])

    def test_invalid_model_plan_is_rejected_before_any_action(self):
        class BadAgent:
            def plan(inner, *args):
                return {"summary": "Bad", "actions": [
                    {"tool": "note", "args": {"text": "Must not run"}},
                    {"tool": "unknown", "args": {}}]}
        engine = Engine(self.config, agent=BadAgent())
        engine.ingest(self.event())
        self.assertEqual(engine.drain()["counts"], {"failed": 1})
        self.assertEqual(engine.store.state("kubernetes")["notes"], [])

    def test_process_lock_refuses_second_scheduler(self):
        with process_lock(self.config.database):
            with self.assertRaisesRegex(RuntimeError, "Another"):
                with process_lock(self.config.database):
                    pass

    def test_codex_adapter_invocation_contract(self):
        observed = {}
        def fake_run(argv, cwd, timeout, stdin, env=None):
            observed.update(argv=argv, cwd=cwd, stdin=stdin)
            self.assertIsInstance(env, dict)
            schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
            self.assertEqual(schema["required"], ["summary", "actions", "outcome"])
            Path(argv[argv.index("--output-last-message") + 1]).write_text(json.dumps({"summary": "Planned", "actions": [], "outcome": "complete"}))
            return 0, ""
        with patch("opendots.agents.shutil.which", return_value="/test/codex"), patch("opendots.agents.bounded_process", side_effect=fake_run):
            plan = CodexAgent().plan(self.config.targets[0], self.event(), {})
        self.assertEqual(plan["summary"], "Planned")
        self.assertEqual(observed["argv"][:4], ["/test/codex", "-a", "never", "exec"])
        self.assertEqual(observed["argv"][observed["argv"].index("--sandbox") + 1], "read-only")
        self.assertEqual(observed["argv"][-1], "-")
        self.assertIn("event_untrusted", observed["stdin"])

    def test_missing_codex_fails_without_falling_back_to_demo(self):
        engine = Engine(replace(self.config, backend="codex", codex_command="opendots-nonexistent-cli"))
        engine.ingest(self.event())
        final = engine.drain()
        self.assertEqual(final["counts"], {"failed": 1})
        self.assertIn("not installed", final["work"][0]["error"])

    def test_http_state_ingestion_and_csrf_boundary(self):
        server = make_server(self.engine, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            self.assertEqual(json.load(urlopen(base + "/api/health"))["status"], "ok")
            payload = json.dumps(self.event()).encode()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + "/api/events", data=payload, headers={"Content-Type": "application/json"}))
            self.assertEqual(error.exception.code, 403)
            headers = {"Content-Type": "application/json", "X-OpenDots-Request": "dashboard"}
            self.assertEqual(json.load(urlopen(Request(base + "/api/events", data=payload, headers=headers)))["queued"], 1)
            with self.assertRaises(HTTPError):
                urlopen(Request(base + "/api/events", data=payload, headers={**headers, "Origin": "https://other.invalid"}))
            self.assertEqual(json.load(urlopen(base + "/api/state"))["event_count"], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_dashboard_retained_patch_is_exact_and_confined(self):
        self.engine.ingest(self.event(type="github.issue.opened"))
        work = self.engine.drain()["work"][0]
        server = make_server(self.engine, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/api/work/{work['id']}/patch"
        try:
            with self.assertRaises(HTTPError) as pending:
                urlopen(url)
            self.assertEqual(pending.exception.code, 409)
            self.engine.store.decide(work["id"], True, work["approval_token"])
            final = self.engine.drain()["work"][0]
            artifact = self.engine.workspaces.root / "artifacts" / f"task-{work['id']}.patch"
            result = json.load(urlopen(url))
            self.assertEqual(result["patch"], artifact.read_text())
            self.assertEqual(result["branch"], final["branch"])
            self.assertIn('+', result["patch"])
            with self.assertRaises(HTTPError) as other_origin:
                urlopen(Request(url, headers={"Origin": "https://other.invalid"}))
            self.assertEqual(other_origin.exception.code, 403)
            outside = self.root / "owner-private.txt"
            outside.write_text("must never appear in the patch response")
            artifact.unlink()
            artifact.symlink_to(outside)
            with self.assertRaises(HTTPError) as escaped:
                urlopen(url)
            self.assertEqual(escaped.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
