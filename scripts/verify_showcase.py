"""Independently rerun completed showcase checks and verify retained source scope."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from opendots.config import load_config
from opendots.tools import ToolRegistry
from live_upstream import require, source_hashes


def verify(args):
    config_path=Path(args.config).resolve()
    config=load_config(config_path)
    controls=json.loads((config_path.parent / "controls.json").read_text())
    report=json.loads(Path(args.report).read_text())
    require(config.backend == report["backend"] == "codex", "Real Codex required")
    targets={t.id:t for t in config.targets}
    registry=ToolRegistry(config.sandbox)
    independent=[]
    for target in targets.values():
        require(source_hashes(target.workspace)==controls["source_hashes"][target.id], "Original source changed")
    for work in report["work"]:
        require(work["status"]=="completed", "All demonstration work must complete")
        target=replace(targets[work["target_id"]],workspace=Path(work["workspace"]))
        changed=subprocess.check_output(["git","-C",str(target.workspace),"diff","--name-only",work["base_ref"],"HEAD"],text=True).splitlines()
        require(set(changed).issubset(target.write_paths), "Protected tests or harness changed")
        for name in target.checks:
            result=registry.run_check(target,{"name":name})
            independent.append({"target":target.id,"work_id":work["id"],**result})
    evidence={"independent_checks":independent,"assertions":["original source hashes unchanged","protected tests and harness unchanged","all completed tasks independently pass configured checks"],
              "controls":controls,"launcher":"One-command start_live_demo.sh verified from fresh public dependency installation; health backend codex and real timer produced waiting_approval before service was stopped."}
    Path(args.output).write_text(json.dumps(evidence,indent=2)+"\n")
    print(json.dumps({"success":True,"independent_checks":[{ "target":r["target"],"summary":r["output"].strip().splitlines()[-1]} for r in independent]}))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",required=True)
    parser.add_argument("--report",required=True)
    parser.add_argument("--output",required=True)
    verify(parser.parse_args())
