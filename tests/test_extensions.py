from dataclasses import replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from opendots.agents import AgentRegistry, plan_schema
from opendots.config import load_config
from opendots.engine import Engine
from opendots.sandbox import sandbox_command
from opendots.sources import JSONLSource, normalize_github, verify_github_signature
from opendots.tools import bounded_process, ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "examples", self.root / "examples")
        self.config = load_config(self.root / "examples/config.json")
        # These cases isolate routing/policy; check enforcement has dedicated tests.
        self.config = replace(self.config, targets=tuple(replace(t, required_checks=()) for t in self.config.targets))
        if (os.environ.get("OPENDOTS_TEST_SANDBOX") or os.environ.get("SPOTS_TEST_SANDBOX")) == "trusted-local":
            self.config = replace(self.config, sandbox="trusted-local")

    def tearDown(self):
        self.temp.cleanup()

    def test_real_sandbox_blocks_sibling_files_network_and_credentials(self):
        if (os.environ.get("OPENDOTS_TEST_SANDBOX") or os.environ.get("SPOTS_TEST_SANDBOX")) == "trusted-local":
            self.skipTest("Explicit portable test mode: this host cannot exercise namespace isolation")
        workspace = self.root / "sandbox"
        workspace.mkdir()
        secret = self.root / "secret"
        secret.write_text("should not be readable")
        script = f'''import os, pathlib, socket
assert os.environ.get("OPENDOTS_TEST_SECRET") is None
with open(os.devnull, "w") as sink:
    sink.write("validation tools can use standard null devices")
try:
    pathlib.Path({str(secret)!r}).read_text()
except (PermissionError, FileNotFoundError):
    pass
else:
    raise AssertionError("sibling secret readable")
try:
    socket.create_connection(("1.1.1.1", 443), timeout=.2)
except OSError:
    pass
else:
    raise AssertionError("network connection succeeded")
pathlib.Path("output.txt").write_text("allowed local write")
print("PASS: sibling files, network, credentials isolated")
'''
        (workspace / "check.py").write_text(script)
        os.environ["OPENDOTS_TEST_SECRET"] = "not inherited"
        try:
            code, output = bounded_process(sandbox_command([sys.executable, "check.py"], workspace), workspace, 5)
        finally:
            os.environ.pop("OPENDOTS_TEST_SECRET")
        self.assertEqual(code, 0, output)
        self.assertTrue((workspace / "output.txt").exists())

    def test_custom_tool_extends_schema_and_runtime(self):
        registry = ToolRegistry()
        registry.register("memory", lambda target, args: {"note": args["value"]}, ["value"])
        self.assertIn("memory", str(plan_schema(registry)))
        target = replace(self.config.targets[0], policy={"memory": "auto"})
        class Provider:
            def plan(inner, *args):
                return {"summary": "Custom action", "actions": [{"tool": "memory", "args": {"value": "Remember me"}}]}
        providers = AgentRegistry()
        providers.register("custom-provider", Provider())
        engine = Engine(replace(self.config, backend="custom-provider", targets=(target,)), registry=registry, agents=providers)
        engine.ingest({"type": "github.discussion.created", "payload": {"repo": "kubernetes/kubernetes"}})
        self.assertEqual(engine.drain()["counts"], {"completed": 1})
        self.assertEqual(engine.store.state(target.id)["notes"][0], "Remember me")

    def test_low_priority_star_updates_state_without_worker(self):
        engine = Engine(self.config)
        engine.ingest({"id": "star", "type": "github.star", "payload": {"repo": "facebook/react"}})
        self.assertEqual(engine.snapshot()["counts"], {})
        self.assertEqual(engine.store.state("react")["actual_state"]["stars_observed"], 1)

    def test_draft_policy_persists_plan_without_write(self):
        target = replace(self.config.targets[0], policy={"read_file": "auto", "write_file": "draft", "run_check": "auto", "note": "auto"})
        engine = Engine(replace(self.config, targets=(target,)))
        engine.ingest({"type": "github.issue.opened", "payload": {"repo": "kubernetes/kubernetes"}})
        work = engine.drain()["work"][0]
        self.assertEqual(work["status"], "drafted")
        self.assertEqual(json.loads((Path(work["workspace"]) / "deployment.json").read_text())["spec"]["replicas"], 0)

    def test_webhook_signature_and_normalization(self):
        body = json.dumps({"action": "opened", "repository": {"full_name": "owner/repo"}, "issue": {"number": 1, "title": "Report", "labels": []}}).encode()
        signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        verify_github_signature(body, signature, "test-secret")
        with self.assertRaises(ValueError):
            verify_github_signature(body + b"x", signature, "test-secret")
        event = normalize_github("issues", json.loads(body), "delivery-1")
        self.assertEqual(event["type"], "github.issue.opened")
        self.assertEqual(event["payload"]["repo"], "owner/repo")

    def test_jsonl_source_retains_partial_lines_and_replays_safely(self):
        path = self.root / "events.jsonl"
        source = JSONLSource({"id": "file", "path": str(path)})
        path.write_bytes(b'{"type":"one"}\n{"type":')
        events, state = source.poll({})
        self.assertEqual(len(events), 1)
        with path.open("ab") as handle:
            handle.write(b'"two"}\n')
        next_events, next_state = source.poll(state)
        self.assertEqual([e["type"] for e in next_events], ["two"])
        self.assertEqual(source.poll(next_state)[0], [])
        self.assertEqual(source.poll({})[0][0]["id"], events[0]["id"])

    def test_repeated_demo_uses_prior_branch_and_skips_identical_write(self):
        engine = Engine(self.config)
        engine.ingest({"id": "one", "type": "github.issue.opened", "payload": {"repo": "kubernetes/kubernetes"}})
        work = engine.drain()["work"][0]
        engine.store.decide(work["id"], True)
        engine.drain()
        engine.ingest({"id": "two", "type": "github.issue.opened", "payload": {"repo": "kubernetes/kubernetes"}})
        final = engine.drain()
        self.assertEqual(final["counts"], {"completed": 2})
        recent = final["work"][0]
        self.assertEqual(recent["base_ref"], work["branch"])
        self.assertNotEqual(recent["workspace"], work["workspace"])
        self.assertFalse(any(a["tool"] == "write_file" for a in recent["plan"]["actions"]))

    def test_databases_in_same_directory_have_separate_branches_and_workspaces(self):
        first = Engine(replace(self.config, database=self.root / "one.db"))
        second = Engine(replace(self.config, database=self.root / "two.db"))
        event = {"type": "github.discussion.created", "payload": {"repo": "kubernetes/kubernetes"}}
        first.ingest(event)
        second.ingest(event)
        a, b = first.drain(), second.drain()
        self.assertEqual(a["counts"], {"completed": 1})
        self.assertEqual(b["counts"], {"completed": 1})
        self.assertNotEqual(a["work"][0]["workspace"], b["work"][0]["workspace"])

    def test_schema_has_explicit_string_type_for_tool_enum(self):
        for variant in plan_schema(ToolRegistry())["properties"]["actions"]["items"]["anyOf"]:
            self.assertEqual(variant["properties"]["tool"]["type"], "string")

    def test_configured_write_scopes_protect_checks(self):
        target = replace(self.config.targets[0], write_paths=("deployment.json",))
        with self.assertRaisesRegex(ValueError, "scope"):
            ToolRegistry().validate(target, {"tool": "write_file", "args": {
                "path": "check.py", "content": "print('PASS')", "expected_sha256": "any"}})

    def test_follow_up_receives_real_action_results(self):
        calls = []
        class InvestigatingAgent:
            def plan(inner, target, event, state):
                calls.append(state["task_action_results"])
                if not state["task_action_results"]:
                    return {"summary": "Read before deciding", "outcome": "needs_follow_up", "actions": [
                        {"tool": "read_file", "args": {"path": "deployment.json"}}]}
                return {"summary": "Investigation completed", "outcome": "complete", "actions": [
                    {"tool": "note", "args": {"text": "Read result confirmed zero replicas"}}]}
        engine = Engine(self.config, agent=InvestigatingAgent())
        engine.ingest({"type": "github.discussion.created", "payload": {"repo": "kubernetes/kubernetes"}})
        result = engine.drain()
        self.assertEqual(result["counts"], {"completed": 1})
        self.assertEqual(result["work"][0]["planning_round"], 2)
        self.assertIn('"replicas": 0', calls[1][0]["result"]["content"])

    def test_surgical_edit_keeps_scope_uniqueness_and_stale_file_protection(self):
        from opendots.tools import digest
        target = replace(self.config.targets[0], write_paths=("deployment.json",))
        path = target.workspace / "deployment.json"
        original = path.read_text()
        registry = ToolRegistry()
        action = {"tool": "replace_text", "args": {"path": "deployment.json",
                  "old_text": '"replicas": 0', "new_text": '"replicas": 3', "expected_sha256": digest(original)}}
        self.assertIn('+', registry.preview(target, action)["diff"])
        path.write_text(original + "\n")
        with self.assertRaisesRegex(ValueError, "changed since planning"):
            registry.execute(target, action)
        self.assertEqual(path.read_text(), original + "\n")
        path.write_text(original)
        registry.execute(target, action)
        self.assertEqual(json.loads(path.read_text())["spec"]["replicas"], 3)
        ambiguous = {"tool": "replace_text", "args": {"path": "deployment.json",
                     "old_text": '"', "new_text": "!", "expected_sha256": digest(path.read_text())}}
        with self.assertRaisesRegex(ValueError, "exactly once"):
            registry.preview(target, ambiguous)
        forbidden = {"tool": "replace_text", "args": {**action["args"], "path": "check.py"}}
        with self.assertRaisesRegex(ValueError, "scope"):
            registry.validate(target, forbidden)

    def test_owner_selected_standalone_runtime_is_available_inside_sandbox(self):
        if (os.environ.get("OPENDOTS_TEST_SANDBOX") or os.environ.get("SPOTS_TEST_SANDBOX")) == "trusted-local":
            self.skipTest("Explicit portable test mode: standalone sandbox mounts require namespaces")
        executable = self.root / "private-runtime" / "true"
        executable.parent.mkdir()
        shutil.copy2(shutil.which("true"), executable)
        workspace = self.root / "standalone-check"
        workspace.mkdir()
        code, output = bounded_process(sandbox_command([str(executable)], workspace), workspace, 5)
        self.assertEqual(code, 0, output)

    def test_follow_up_budget_stops_infinite_planning(self):
        class LoopAgent:
            def plan(inner, *args):
                return {"summary": "More investigation", "actions": [], "outcome": "needs_follow_up"}
        engine = Engine(replace(self.config, max_planning_rounds=3), agent=LoopAgent())
        engine.ingest({"type": "github.discussion.created", "payload": {"repo": "kubernetes/kubernetes"}})
        result = engine.drain()
        self.assertEqual(result["counts"], {"blocked": 1})
        self.assertEqual(result["work"][0]["planning_round"], 3)

    def test_stale_approval_token_is_rejected(self):
        engine = Engine(self.config)
        engine.ingest({"type": "github.issue.opened", "payload": {"repo": "kubernetes/kubernetes"}})
        work = engine.drain()["work"][0]
        with self.assertRaisesRegex(ValueError, "changed"):
            engine.store.decide(work["id"], True, "old-proposal")
        engine.store.decide(work["id"], True, work["approval_token"])
        self.assertEqual(engine.drain()["counts"], {"completed": 1})

    def test_action_list_has_no_fixed_count_limit(self):
        class ManyActions:
            def plan(inner, *args):
                return {"summary": "Configurable action list", "actions": [
                    {"tool": "note", "args": {"text": f"action {i}"}} for i in range(150)]}
        engine = Engine(self.config, agent=ManyActions())
        engine.ingest({"type": "github.discussion.created", "payload": {"repo": "kubernetes/kubernetes"}})
        engine.drain()
        with engine.store.connect() as db:
            count = db.execute("SELECT count(*) FROM audit WHERE kind='action_completed'").fetchone()[0]
        self.assertEqual(count, 150)

    def test_completion_summary_is_actual_execution_evidence(self):
        engine = Engine(self.config)
        engine.ingest({"type": "github.issue.opened", "payload": {"repo": "kubernetes/kubernetes"}})
        work = engine.drain()["work"][0]
        engine.store.decide(work["id"], True)
        final = engine.drain()["work"][0]
        self.assertIn("Passed configured checks: deployment", final["summary"])
        self.assertIn("local change proposal", final["summary"])

    def test_required_check_cannot_complete_a_failing_unchecked_task(self):
        class NoCheck:
            def plan(inner, *args):
                return {"summary": "Assume prior evidence is enough", "actions": []}
        target = replace(self.config.targets[0], required_checks=("deployment",))
        engine = Engine(replace(self.config, targets=(target,)), agent=NoCheck())
        engine.ingest({"type": "github.discussion.created", "payload": {"repo": "kubernetes/kubernetes"}})
        result = engine.drain()
        self.assertEqual(result["counts"], {"failed": 1})
        self.assertTrue(any(a["kind"] == "required_checks_added" for a in result["audit"]))

    def test_required_check_is_invalidated_by_a_later_edit(self):
        from opendots.tools import digest
        target = replace(self.config.targets[0], required_checks=("deployment",))
        path = target.workspace / "deployment.json"
        original = path.read_text().replace('"replicas": 0', '"replicas": 3')
        path.write_text(original)
        class CheckThenBreak:
            def plan(inner, target, event, state):
                if not state["task_action_results"]:
                    return {"summary": "Check", "outcome": "needs_follow_up", "actions": [
                        {"tool": "run_check", "args": {"name": "deployment"}}]}
                return {"summary": "Edit after check", "actions": [{"tool": "write_file", "args": {
                    "path": "deployment.json", "content": original.replace('"replicas": 3', '"replicas": 0'),
                    "expected_sha256": digest(original)}}]}
        target = replace(target, policy={**target.policy, "write_file": "auto"})
        engine = Engine(replace(self.config, targets=(target,)), agent=CheckThenBreak())
        engine.ingest({"type": "github.discussion.created", "payload": {"repo": "kubernetes/kubernetes"}})
        result = engine.drain()
        self.assertEqual(result["counts"], {"failed": 1})
        self.assertEqual(len([r for r in engine.store.work_results(result["work"][0]["id"]) if r["tool"] == "run_check" and r["result"]["exit_code"] == 0]), 1)
        self.assertEqual(result["work"][0]["plan"]["actions"][-1]["tool"], "run_check")

    def test_required_check_obeys_approval_and_resume_without_replaying_notes(self):
        class NoCheck:
            def plan(inner, *args):
                return {"summary": "Record", "actions": [{"tool": "note", "args": {"text": "once"}}]}
        target = replace(self.config.targets[0], required_checks=("deployment",),
                         policy={**self.config.targets[0].policy, "run_check": "approval"})
        path = target.workspace / "deployment.json"
        path.write_text(path.read_text().replace('"replicas": 0', '"replicas": 3'))
        config = replace(self.config, targets=(target,))
        engine = Engine(config, agent=NoCheck())
        engine.ingest({"type": "github.discussion.created", "payload": {"repo": "kubernetes/kubernetes"}})
        work = engine.drain()["work"][0]
        self.assertEqual(work["status"], "waiting_approval")
        self.assertEqual(work["approval_index"], 1)
        restarted = Engine(config, agent=NoCheck())
        restarted.store.decide(work["id"], True, work["approval_token"])
        result = restarted.drain()
        self.assertEqual(result["counts"], {"completed": 1})
        self.assertEqual(restarted.store.state(target.id)["notes"].count("once"), 1)
        self.assertEqual(len([r for r in restarted.store.work_results(work["id"]) if r["tool"] == "run_check"]), 1)

    def test_required_checks_added_to_an_older_persisted_plan_keep_approved_cursor(self):
        from opendots.tools import digest
        original = (self.config.targets[0].workspace / "deployment.json").read_text()
        class RepairWithoutCheck:
            def plan(inner, *args):
                return {"summary": "Repair", "actions": [{"tool": "note", "args": {"text": "once"}},
                    {"tool": "write_file", "args": {"path": "deployment.json", "expected_sha256": digest(original),
                     "content": original.replace('"replicas": 0', '"replicas": 3')}}]}
        engine = Engine(self.config, agent=RepairWithoutCheck())
        engine.ingest({"type": "github.discussion.created", "payload": {"repo": "kubernetes/kubernetes"}})
        work = engine.drain()["work"][0]
        engine.store.decide(work["id"], True, work["approval_token"])
        target = replace(self.config.targets[0], required_checks=("deployment",))
        restarted = Engine(replace(self.config, targets=(target,)), agent=RepairWithoutCheck())
        fresh = restarted.drain()["work"][0]
        self.assertEqual(fresh["status"], "waiting_approval")
        restarted.store.decide(fresh["id"], True, fresh["approval_token"])
        result = restarted.drain()
        self.assertEqual(result["counts"], {"completed": 1})
        self.assertEqual(restarted.store.state(target.id)["notes"].count("once"), 1)
        self.assertTrue(any(r["tool"] == "run_check" for r in restarted.store.work_results(work["id"])))
