"""Extend a successful scale run with goal-scoped, untargeted HTTP verification.

Goals are owner grouping metadata. Existing source-filtered subscriptions route
one event to all active targets in its goal; no new runtime abstraction is used.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

from live_scale import ROOT, worker
from live_upstream import Engine, require, source_hashes
from opendots.config import load_config
from opendots.server import make_server


def extend(directory):
    original = directory / "report.json"
    report = json.loads(original.read_text())
    require(report["success"], "Extend only a successful scale run")
    (directory / "initial-report.json").write_text(original.read_text())
    config_path = directory / "config.json"
    (directory / "initial-config.json").write_text(config_path.read_text())
    raw = json.loads(config_path.read_text())
    workload = report["workload"]
    active = {c["id"]:c for c in workload["targets"] if c.get("decision", "approve") == "approve" and c["policy"] != "draft"}
    for target in raw["targets"]:
        rules = [{"types": ["ci.failure", "timer.scout", "github.star"]}]
        if target["id"] in active:
            rules.append({"types": ["ci.verify"], "sources": ["local-ci", "goal:" + active[target["id"]]["goal_id"]]})
        target["subscriptions"] = rules
    config_path.write_text(json.dumps(raw, indent=2) + "\n")
    engine = Engine(load_config(config_path))
    server = make_server(engine, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    new_work_ids = []
    expected_targets = {}
    try:
        for goal in workload["goals"]:
            target_ids = {tid for tid,c in active.items() if c["goal_id"] == goal["id"]}
            if not target_ids:
                continue
            event = {"id": "goal-verify:" + goal["id"], "type": "ci.verify", "source": "goal:" + goal["id"], "priority": 80,
                     "payload": {"title": "Verify every retained target repair serving this owner goal", "goal": goal["objective"]}}
            expected_targets[event["id"]] = target_ids
            report["events"].append(event)
            for copy in range(workload["delivery_copies"]):
                response = json.load(urlopen(Request(base + "/api/events", data=json.dumps(event).encode(),
                     headers={"Content-Type": "application/json", "X-OpenDots-Request": "dashboard"}), timeout=30))
                require(response["queued"] == (len(target_ids) if copy == 0 else 0), "Wrong goal fanout/dedup count")
                require(response["duplicate"] == (copy > 0), "Goal event dedup failed")
                report["http_receipts"].append({"event_id": event["id"], **response})
        before = engine.snapshot()
        for event_id,target_ids in expected_targets.items():
            work = [w for w in before["work"] if w["event_id"] == event_id]
            require({w["target_id"] for w in work} == target_ids, "Cross-goal target leakage")
            new_work_ids += [w["id"] for w in work]
        with (directory / "goal-fanout-worker.log").open("w") as log:
            subprocess.run([sys.executable,str(ROOT / "scripts/live_scale.py"),"--worker-directory",str(directory),"--phase","goal-fanout"],
                           cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=3600)
        final = engine.snapshot()
        for work in final["work"]:
            if work["id"] not in new_work_ids:
                continue
            require(work["status"] == "completed", "Goal verification failed: " + str(work.get("error")))
            previous = max((w for w in report["work"] if w["target_id"] == work["target_id"] and w["status"] == "completed"),key=lambda w:w["updated"])
            require(work["base_ref"] == previous["branch"], "Goal fanout lost retained branch")
            target = replace(engine.targets[work["target_id"]],workspace=Path(work["workspace"]))
            changed = subprocess.check_output(["git","-C",str(target.workspace),"diff","--name-only",work["base_ref"],"HEAD"],text=True).splitlines()
            require(not changed, "Goal revalidation made an unnecessary edit")
            checks = [r["result"] for r in engine.store.work_results(work["id"]) if r["tool"] == "run_check"]
            require(checks and all(c["exit_code"] == 0 for c in checks), "Goal completion lacks a passing check")
            for name in target.checks:
                result = engine.registry.run_check(target,{"name":name})
                report["independent_checks"].append({"work_id":work["id"],"target":target.id,**result})
        for target in engine.targets.values():
            require(source_hashes(target.workspace) == report["controls"]["source_hashes"][target.id], "Source changed during fanout")
        with engine.store.connect() as db:
            audit = [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id")]
        for row in audit:
            row["detail"] = json.loads(row["detail"])
        report.update(counts=final["counts"],event_count=final["event_count"],work=final["work"],targets=final["targets"],audit=audit,
                      provider_calls=[json.loads(l) for l in (directory / "provider-calls.jsonl").read_text().splitlines()])
        timeline = sorted([(c["started"],1) for c in report["provider_calls"]] + [(c["finished"],-1) for c in report["provider_calls"]])
        active_calls = peak = 0
        for _, change in timeline:
            active_calls += change
            peak = max(peak,active_calls)
        require(peak <= raw["workers"], "Final workload exceeded worker capacity")
        report["maximum_parallel"] = peak
        for target in raw["targets"]:
            intervals = sorted([c for c in report["provider_calls"] if c["target"] == target["id"]],key=lambda c:c["started"])
            require(all(b["started"] >= a["finished"] for a,b in zip(intervals,intervals[1:])), "Goal follow-up overlapped same-target work")
        report["worker_processes"].append(json.loads((directory / "goal-fanout-worker.json").read_text()))
        report["phases"].append({"phase":"goal-fanout","counts":final["counts"],"event_count":final["event_count"]})
        report["assertions"] += ["one untargeted HTTP event fans out to every active target in its owner goal",
             "goal source subscriptions prevent cross-goal routing and exclude draft/rejection controls",
             "duplicate multi-target goal events create no extra work",
             "goal-wide verification inherits every target repair and passes independently without edits"]
        report["goal_fanout"] = {"events":len(expected_targets),"tasks":len(new_work_ids),"routing":{k:sorted(v) for k,v in expected_targets.items()}}
        report.update(finished=time.time(), elapsed_seconds=time.time()-report["started"])
        original.write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps({"success":True,"counts":final["counts"],"events":final["event_count"],"goal_fanout_tasks":len(new_work_ids),"real_codex_calls":len(report["provider_calls"])}),flush=True)
    except Exception as error:
        (directory / "fanout-failure.json").write_text(json.dumps({"error":str(error),"snapshot":engine.snapshot()},indent=2)+"\n")
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",required=True)
    extend(Path(parser.parse_args().directory).resolve())
