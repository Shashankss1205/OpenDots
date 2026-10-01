"""Reproducible demos using the real runtime, task branches, checks, state, and clock source."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opendots.config import load_config
from opendots.engine import Engine


def demo(mode, directory=None, approve=False):
    directory = Path(directory or tempfile.mkdtemp(prefix="opendots-demo-")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    config = load_config(ROOT / "examples/config.json")
    selected = tuple(t for t in config.targets if mode == "combined" or t.id == mode)
    config = replace(config, targets=selected, database=directory / "state.db")
    if mode in {"react", "combined"}:
        schedule = {"id": "react-scout", "target_id": "react", "type": "timer.scout", "interval_seconds": 3600,
                    "payload": {"repo": "facebook/react", "title": "Scheduled accessibility inspection"}}
        config = replace(config, schedules=(schedule,))
    engine = Engine(config)
    if mode in {"kubernetes", "combined"}:
        # Only this incoming GitHub delivery is synthetic. Everything after ingestion is real.
        engine.ingest({"id": "demo:issue", "source": "github-demo", "type": "github.issue.opened", "priority": 95,
                       "payload": {"repo": "kubernetes/kubernetes", "title": "Zero replicas in deployment fixture"}})
    if mode in {"react", "combined"}:
        engine.tick_schedules()
    first = engine.drain()
    if approve:
        # This explicit CLI flag authorizes only the fixture actions created above.
        for work in first["work"]:
            if work["status"] == "waiting_approval":
                engine.store.decide(work["id"], True)
        final = engine.drain()
    else:
        final = first
    report = {"mode": mode, "backend": config.backend, "sandbox": config.sandbox, "state_directory": str(directory),
              "counts": final["counts"], "targets": final["targets"], "work": final["work"],
              "truth": "Synthetic GitHub input and deterministic planner; real timer, SQLite, Git worktrees, policies, checks, and artifacts."}
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"mode": mode, "counts": report["counts"], "report": str(directory / "report.json")}, indent=2))
    if approve and any(w["status"] != "completed" for w in final["work"]):
        raise RuntimeError("Demo did not complete successfully; inspect report")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["kubernetes", "react", "combined"])
    parser.add_argument("--directory")
    parser.add_argument("--approve-fixture-actions", action="store_true")
    args = parser.parse_args()
    demo(args.mode, args.directory, args.approve_fixture_actions)
