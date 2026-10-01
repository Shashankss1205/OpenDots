"""Real-Codex approval-drift and SIGKILL acceptance on disposable local fixtures.

Owner-authorized checks use explicit trusted-local mode when namespaces are
unavailable. No planner response is replaced. This is runtime fault injection,
not an upstream repair benchmark or sandbox isolation claim.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

from live_upstream import AgentRegistry, Engine, ObservedCodex, require
from opendots.config import load_config
from opendots.server import make_server
from opendots.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


class RecordedCodex(ObservedCodex):
    def __init__(self, *args, journal, **kwargs):
        super().__init__(*args, **kwargs)
        self.journal = journal

    def plan(self, target, event, state):
        try:
            return super().plan(target, event, state)
        finally:
            with self.lock:
                call = {**self.calls[-1], "worker_pid": os.getpid(), "work_id": state["current_task"]["work_id"]}
                with self.journal.open("a") as output:
                    output.write(json.dumps(call) + "\n")


def worker(directory):
    config = load_config(directory / "config.json")
    registry = ToolRegistry(config.sandbox)
    agents = AgentRegistry()
    if config.backend == "codex":
        agents.register("codex", RecordedCodex(config.codex_command, timeout=config.agent_timeout,
                            registry=registry, journal=directory / "provider-calls.jsonl"))
    engine = Engine(config, registry=registry, agents=agents)
    result = engine.drain(timeout=600)
    print(json.dumps({"worker_pid": os.getpid(), "counts": result["counts"]}), flush=True)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_for(predicate, message, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError(message)


def prepare(directory, command, sandbox, owner_plans=False):
    targets = []
    for name in ["approval-drift", "worker-kill"]:
        source = directory / "sources" / name
        source.mkdir(parents=True)
        (source / "calculator.py").write_text("def total(values):\n    return sum(values)\n")
        if name == "approval-drift":
            check = '''from calculator import total
assert total([2, 3, -1]) == 4
assert total([]) == 0
assert total([0, 0]) == 0
print("PASS: three calculator behavior cases")
'''
            owner = "Run the configured behavior check on the inherited calculator, then propose a note with its actual outcome. Do not edit anything. The note requires exact owner approval. Prefer a complete plan containing run_check followed by note; use follow-up only if truly necessary."
            check_name = "behavior"
        else:
            check = '''import json,os,pathlib,subprocess,sys,time
child_code = "import pathlib,time\\nfor n in range(4000):\\n pathlib.Path('child-heartbeat.txt').write_text(str(n))\\n time.sleep(.03)"
child = subprocess.Popen([sys.executable, "-B", "-c", child_code])
while not pathlib.Path("child-heartbeat.txt").exists():
    time.sleep(.01)
pathlib.Path("root-heartbeat.txt").write_text("0")
pathlib.Path("started.json").write_text(json.dumps({"root_pid":os.getpid(),"descendant_pid":child.pid}))
try:
    for n in range(4000):
        pathlib.Path("root-heartbeat.txt").write_text(str(n))
        time.sleep(.03)
finally:
    child.kill()
    child.wait()
'''
            owner = "Request only the configured lifetime check. Do not execute checks through native Codex commands. The owner will deliberately kill the OpenDots worker during the action to inspect actual recovery. No source edits or extra investigation are needed. Return one run_check action; native application tools exist only as proposed JSON actions."
            check_name = "lifetime"
        (source / "check.py").write_text(check)
        (source / "OWNER.md").write_text(owner)
        (source / ".gitignore").write_text("__pycache__/\n*.pyc\nstarted.json\n*-heartbeat.txt\n")
        targets.append({"id": name, "name": name, "objective": owner, "workspace": str(source),
            "subscriptions": [{"types": ["owner.validation"]}], "skills": ["OWNER.md"],
            "policy": {"read_file": "auto", "run_check": "auto", "note": "approval" if name == "approval-drift" else "auto"},
            "write_paths": ["calculator.py"], "checks": {check_name: ["{python}", "-B", "check.py"]},
            "required_checks": [check_name]})
    (directory / "config.json").write_text(json.dumps({"database": str(directory / "state.db"),
        "backend": "owner-persisted" if owner_plans else "codex", "codex_command": command, "workers": 1, "agent_timeout": 180,
        "sandbox": sandbox, "max_planning_rounds": 4, "targets": targets}, indent=2) + "\n")


def acceptance(args):
    directory = Path(args.directory).resolve()
    require(not directory.exists(), "Use a fresh directory")
    directory.mkdir(parents=True)
    prepare(directory, args.codex_command, args.sandbox, args.owner_plans)
    engine = Engine(load_config(directory / "config.json"))
    server = make_server(engine, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    children = []
    reported_pids = []
    started = time.time()
    report = {"sandbox": args.sandbox, "expect_bugs": args.expect_bugs, "planning": "explicit owner-persisted plans; no model provider" if args.owner_plans else "actual authenticated Codex",
        "truth": "Production HTTP decisions, real check processes, real SIGKILL, SQLite and Git. Disposable owner-defined fixtures. No sandbox-isolation claim for trusted-local. Planning provenance is recorded separately.", "assertions": []}

    def post(route, body):
        return json.load(urlopen(Request(base + route, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-OpenDots-Request": "dashboard"}), timeout=30))

    def launch(name):
        log = (directory / (name + "-worker.log")).open("w")
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker-directory", str(directory)],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        log.close()
        children.append(child)
        return child

    def owner_plan(event_id, actions):
        if not args.owner_plans:
            return
        with engine.store.connect() as db:
            work_id = db.execute("SELECT id FROM work WHERE event_id=?", (event_id,)).fetchone()[0]
            engine.store.log(db, "owner_plan_loaded", {"source": "explicit test-owner plan; no model response"}, work_id=work_id)
        engine.store.save_plan(work_id, {"summary": "Execute owner-defined process boundary probe", "outcome": "complete", "actions": actions})

    def snapshot():
        data = engine.snapshot()
        with engine.store.connect() as db:
            audit = [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id")]
        for item in audit:
            item["detail"] = json.loads(item["detail"])
        data["audit"] = audit
        return data

    try:
        post("/api/events", {"id": "validate:before-review", "type": "owner.validation", "target_id": "approval-drift"})
        owner_plan("validate:before-review", [{"tool": "run_check", "args": {"name": "behavior"}},
                    {"tool": "note", "args": {"text": "Three actual behavior cases passed before owner review"}}])
        child = launch("approval-initial")
        require(child.wait(timeout=300) == 0, "Initial worker failed")
        initial = snapshot()
        work = initial["work"][0]
        require(work["status"] == "waiting_approval", "Expected an actual reviewed note: " + str(work.get("error")))
        require(work["plan"]["actions"][work["approval_index"]]["tool"] == "note", "Unexpected reviewed action")
        checks = [r for r in engine.store.work_results(work["id"]) if r["tool"] == "run_check"]
        require(checks and checks[-1]["result"]["exit_code"] == 0, "No actual successful baseline check")
        source = Path(work["workspace"]) / "calculator.py"
        source.write_text("def total(values):\n    return len(values)\n")
        post(f"/api/work/{work['id']}/decision", {"approved": True, "approval_token": work["approval_token"]})
        child = launch("approval-resumed")
        require(child.wait(timeout=300) == 0, "Resumed worker failed")
        after = snapshot()
        changed = next(w for w in after["work"] if w["id"] == work["id"])
        try:
            engine.registry.run_check(replace(engine.targets["approval-drift"], workspace=Path(work["workspace"])), {"name": "behavior"})
            rerun_failed = False
        except RuntimeError:
            rerun_failed = True
        require(rerun_failed, "External mutation did not actually break behavior")
        report["approval_drift"] = {"work_id": work["id"], "status": changed["status"], "error": changed["error"],
            "independent_rerun_failed": rerun_failed, "false_completion": changed["status"] == "completed"}
        print(json.dumps({"approval_drift": report["approval_drift"]}), flush=True)
        require((changed["status"] == "completed") if args.expect_bugs else changed["status"] == "failed", "Approval drift outcome did not match expectation")

        post("/api/events", {"id": "validate:kill-during-check", "type": "owner.validation", "target_id": "worker-kill"})
        owner_plan("validate:kill-during-check", [{"tool": "run_check", "args": {"name": "lifetime"}}])
        child = launch("kill-initial")
        def marker():
            for item in engine.snapshot()["work"]:
                if item["target_id"] == "worker-kill" and item["workspace"]:
                    path = Path(item["workspace"]) / "started.json"
                    if path.exists():
                        try:
                            return (item, json.loads(path.read_text()))
                        except ValueError:
                            pass
            require(child.poll() is None, "Worker exited before lifetime check started")
        killed_work, pids = wait_for(marker, "Actual Codex lifetime plan/check did not start", timeout=300)
        reported_pids += list(pids.values())
        require(all(alive(pid) for pid in pids.values()), "Test descendants were not running")
        heartbeats = {"root_pid": Path(killed_work["workspace"]) / "root-heartbeat.txt",
                      "descendant_pid": Path(killed_work["workspace"]) / "child-heartbeat.txt"}
        child.kill()
        child.wait(timeout=10)
        time.sleep(.3)
        before_beats = {key: path.stat().st_mtime_ns for key,path in heartbeats.items()}
        time.sleep(.5)
        survivors = [key for key,path in heartbeats.items() if path.stat().st_mtime_ns != before_beats[key]]
        # A new real worker process performs normal recovery. It must not replay.
        resumed = launch("kill-recovery")
        require(resumed.wait(timeout=30) == 0, "Recovery worker failed")
        final = snapshot()
        recovered = next(w for w in final["work"] if w["id"] == killed_work["id"])
        require(recovered["status"] == "interrupted", "SIGKILL task was silently completed/replayed")
        require(not engine.store.work_results(killed_work["id"]), "Uncertain check was recorded/replayed as complete")
        report["worker_kill"] = {"work_id": killed_work["id"], "worker_pid": child.pid, "check_pids": pids,
            "surviving_processes": survivors, "recovered_status": recovered["status"], "uncertain_action_replayed": False}
        report["worker_kill"]["liveness_evidence"] = "root and descendant heartbeat writes sampled after worker death; surviving_processes lists those still updating, independent of host /proc PID mapping"
        if not args.expect_bugs:
            require(not engine.store.state("approval-drift")["notes"], "Failed validation leaked a success claim into target memory")
        print(json.dumps({"worker_kill": report["worker_kill"]}), flush=True)
        require(bool(survivors) if args.expect_bugs else not survivors, "Process lifetime outcome did not match expectation")
        if not args.expect_bugs:
            # Recovery uses a fresh event and owner-selected bounded check. The
            # interrupted action stays interrupted and is never replayed.
            raw = json.loads((directory / "config.json").read_text())
            for target in raw["targets"]:
                if target["id"] == "worker-kill":
                    target["checks"]["lifetime"] = ["{python}", "-B", "-c",
                        "from calculator import total; assert total([2,3,-1]) == 4; print('PASS: fresh task after SIGKILL; source behavior verified')"]
            (directory / "config.json").write_text(json.dumps(raw, indent=2) + "\n")
            engine = Engine(load_config(directory / "config.json"))
            post("/api/events", {"id": "validate:fresh-after-kill", "type": "owner.validation", "target_id": "worker-kill"})
            owner_plan("validate:fresh-after-kill", [{"tool": "run_check", "args": {"name": "lifetime"}}])
            fresh_worker = launch("fresh-after-kill")
            require(fresh_worker.wait(timeout=300) == 0, "Fresh recovery worker failed")
            final = snapshot()
            fresh = next(w for w in final["work"] if w["event_id"] == "validate:fresh-after-kill")
            original = next(w for w in final["work"] if w["id"] == killed_work["id"])
            require(fresh["status"] == "completed" and original["status"] == "interrupted", "Fresh delivery/recovery changed uncertain task status")
            require(fresh["workspace"] != original["workspace"], "Fresh retry reused an uncertain workspace")
            report["fresh_recovery"] = {"status": fresh["status"], "work_id": fresh["id"], "separate_workspace": True,
                "original_status": original["status"], "actual_check": engine.store.work_results(fresh["id"])[0]["result"]}
        report["assertions"] += ["actual check passes before owner review", "owner's external edit breaks independent validation",
            "exact-token approval resumes the same task in a different process", "actual worker is killed during a running check with a descendant",
            "new worker marks uncertain action interrupted without replay"]
        if not args.expect_bugs:
            report["assertions"] += ["changed workspace cannot reuse earlier validation for completion", "worker death terminates check and descendant process group",
                "fresh delivery completes in a separate workspace without replaying the interrupted action"]
        report.update(success=True, snapshot=final)
    except Exception as error:
        report.update(success=False, error=str(error), snapshot=snapshot())
        raise
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
        for pid in reported_pids:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
        report["provider_calls"] = [json.loads(line) for line in (directory / "provider-calls.jsonl").read_text().splitlines()] if (directory / "provider-calls.jsonl").exists() else []
        report["elapsed_seconds"] = time.time() - started
        report["cli_version"] = subprocess.check_output([args.codex_command, "--version"], text=True).strip()
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory")
    parser.add_argument("--worker-directory")
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--sandbox", choices=["bubblewrap", "trusted-local"], default="bubblewrap")
    parser.add_argument("--expect-bugs", action="store_true")
    parser.add_argument("--owner-plans", action="store_true", help="Use explicit persisted owner actions without registering any model provider")
    args = parser.parse_args()
    if args.worker_directory:
        worker(Path(args.worker_directory).resolve())
    else:
        acceptance(args)
