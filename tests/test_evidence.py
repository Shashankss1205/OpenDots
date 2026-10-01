from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from opendots.config import Config, Target
from opendots.engine import Engine
from opendots.evidence import workspace_fingerprint
from opendots.tools import ToolRegistry, bounded_process


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "value.txt").write_text("valid")
        (self.source / "check.py").write_text("from pathlib import Path\nassert Path('value.txt').read_text() == 'valid'\nprint('PASS: actual input validated')\n")
        plan = {"summary": "Validate before reviewed note", "actions": [
            {"tool": "run_check", "args": {"name": "input"}},
            {"tool": "note", "args": {"text": "Actual input check passed"}}]}
        self.target = Target("input", "Input", "Keep validated input", self.source,
            ({"types": ["owner.test"]},), {"run_check": "auto", "note": "approval"},
            recipes={"owner.test": plan}, checks={"input": ["{python}", "-B", "check.py"]},
            required_checks=("input",))
        # Explicit owner-controlled fixture mode, independent of host namespaces.
        self.config = Config((self.target,), self.root / "state.db", sandbox="trusted-local")

    def tearDown(self):
        self.temp.cleanup()

    def reviewed(self):
        engine = Engine(self.config)
        engine.ingest({"type": "owner.test", "id": "verify"})
        work = engine.drain()["work"][0]
        self.assertEqual(work["status"], "waiting_approval")
        return engine, work

    def test_external_edit_during_review_invalidates_completed_check(self):
        engine, work = self.reviewed()
        (Path(work["workspace"]) / "value.txt").write_text("broken")
        engine.store.decide(work["id"], True, work["approval_token"])
        resumed = Engine(self.config)
        final = resumed.drain()
        self.assertEqual(final["counts"], {"failed": 1})
        self.assertEqual(resumed.store.state("input")["notes"], [])
        self.assertTrue(any(a["kind"] == "required_checks_added" for a in final["audit"]))

    def test_changed_owner_check_command_requires_new_validation(self):
        engine, work = self.reviewed()
        engine.store.decide(work["id"], True, work["approval_token"])
        target = replace(self.target, checks={"input": ["{python}", "-c", "raise SystemExit(9)"]})
        resumed = Engine(replace(self.config, targets=(target,)))
        self.assertEqual(resumed.drain()["counts"], {"failed": 1})
        self.assertEqual(resumed.store.state("input")["notes"], [])

    def test_edit_while_retaining_artifact_cannot_publish_completion(self):
        target = replace(self.target, policy={"run_check": "auto", "note": "auto"})
        engine = Engine(replace(self.config, targets=(target,)))
        retain = engine.workspaces.finalize
        def changed_during_retention(work):
            artifact = retain(work)
            (Path(work["workspace"]) / "value.txt").write_text("owner changed after artifact creation")
            return artifact
        engine.workspaces.finalize = changed_during_retention
        engine.ingest({"type": "owner.test"})
        final = engine.drain()
        self.assertEqual(final["counts"], {"failed": 1})
        self.assertIn("retaining the artifact", final["work"][0]["error"])
        self.assertEqual(engine.store.state("input")["notes"], [])
        self.assertEqual(engine.store.state("input")["artifacts"], [])

    def test_successful_check_that_changes_inputs_is_rejected(self):
        (self.source / "check.py").write_text("from pathlib import Path\nassert Path('value.txt').read_text() == 'valid'\nPath('value.txt').write_text('broken')\nprint('PASS')\n")
        with self.assertRaisesRegex(RuntimeError, "changed workspace inputs"):
            ToolRegistry("trusted-local").run_check(self.target, {"name": "input"})

    def test_git_ignored_input_is_still_bound_to_check_evidence(self):
        (self.source / ".gitignore").write_text("value.txt\n")
        subprocess.run(["git", "init", "--quiet", str(self.source)], check=True)
        registry = ToolRegistry("trusted-local")
        result = registry.run_check(self.target, {"name": "input"})
        (self.source / "value.txt").write_text("broken")
        self.assertNotEqual(result["workspace_fingerprint"], workspace_fingerprint(self.source))
        with self.assertRaisesRegex(RuntimeError, "Configured check failed"):
            registry.run_check(self.target, {"name": "input"})

    def test_generated_python_and_pytest_caches_do_not_invalidate_inputs(self):
        (self.source / "check.py").write_text("from pathlib import Path\nassert Path('value.txt').read_text() == 'valid'\nPath('__pycache__').mkdir(exist_ok=True)\nPath('__pycache__/generated.pyc').write_bytes(b'cache')\nPath('.pytest_cache').mkdir(exist_ok=True)\nPath('.pytest_cache/state').write_text('cache')\nprint('PASS')\n")
        result = ToolRegistry("trusted-local").run_check(self.target, {"name": "input"})
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["workspace_fingerprint"], workspace_fingerprint(self.source))

    def test_failed_task_keeps_notes_in_audit_without_publishing_target_memory(self):
        target = replace(self.target, policy={"note": "auto", "run_check": "auto"},
            recipes={"owner.test": {"summary": "Note then fail", "actions": [
                {"tool": "note", "args": {"text": "premature success claim"}},
                {"tool": "run_check", "args": {"name": "input"}}]}})
        (self.source / "value.txt").write_text("broken")
        engine = Engine(replace(self.config, targets=(target,)))
        engine.ingest({"type": "owner.test"})
        final = engine.drain()
        self.assertEqual(final["counts"], {"failed": 1})
        self.assertEqual(engine.store.state("input")["notes"], [])
        self.assertEqual(engine.store.work_results(final["work"][0]["id"])[0]["result"]["note"], "premature success claim")

    def test_legacy_check_results_are_revalidated_after_restart(self):
        engine, work = self.reviewed()
        # Materialize a genuine older on-disk result shape: the check actually
        # executed, but v0.1.3 did not record content/config signatures.
        with engine.store.connect() as db:
            row = db.execute("SELECT id,detail FROM audit WHERE work_id=? AND kind='action_completed'", (work["id"],)).fetchone()
            detail = json.loads(row["detail"])
            detail["result"].pop("workspace_fingerprint")
            detail["result"].pop("check_signature")
            db.execute("UPDATE audit SET detail=? WHERE id=?", (json.dumps(detail), row["id"]))
        engine.store.decide(work["id"], True, work["approval_token"])
        resumed = Engine(self.config)
        self.assertEqual(resumed.drain()["counts"], {"completed": 1})
        results = [r for r in resumed.store.work_results(work["id"]) if r["tool"] == "run_check"]
        self.assertEqual(len(results), 2)
        self.assertIn("workspace_fingerprint", results[-1]["result"])
        self.assertEqual(resumed.store.state("input")["notes"].count("Actual input check passed"), 1)

    @unittest.skipUnless(sys.platform == "linux", "Parent-death process guard is Linux-specific")
    def test_timeout_stops_the_actual_command_and_its_descendant(self):
        child_code = "import pathlib,time\nfor n in range(1000):\n pathlib.Path('child-beat').write_text(str(n))\n time.sleep(.02)"
        code = f"import pathlib,subprocess,sys,time\nsubprocess.Popen([sys.executable,'-B','-c',{child_code!r}])\nfor n in range(1000):\n pathlib.Path('root-beat').write_text(str(n))\n time.sleep(.02)\n"
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            bounded_process([sys.executable, "-B", "-c", code], self.source, .4)
        time.sleep(.1)
        before = [(self.source / name).stat().st_mtime_ns for name in ["root-beat", "child-beat"]]
        time.sleep(.15)
        self.assertEqual(before, [(self.source / name).stat().st_mtime_ns for name in ["root-beat", "child-beat"]])

    @unittest.skipUnless(sys.platform == "linux", "Process-group supervisor is Linux-specific")
    def test_successful_exit_also_stops_background_descendants(self):
        child = "import pathlib,time\nfor n in range(1000):\n pathlib.Path('background-beat').write_text(str(n))\n time.sleep(.02)"
        command = f"import pathlib,subprocess,sys,time\nsubprocess.Popen([sys.executable,'-B','-c',{child!r}])\nwhile not pathlib.Path('background-beat').exists():\n time.sleep(.01)\nprint('PASS: normal command exit')\n"
        code, output = bounded_process([sys.executable, "-B", "-c", command], self.source, 5)
        self.assertEqual(code, 0, output)
        time.sleep(.05)
        before = (self.source / "background-beat").stat().st_mtime_ns
        time.sleep(.15)
        self.assertEqual(before, (self.source / "background-beat").stat().st_mtime_ns)
