"""Live Codex repair verified against actual React client/server rendering."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opendots.agents import AgentRegistry
from opendots.config import Config, Target
from opendots.engine import Engine
from opendots.sandbox import sandbox_command
from opendots.tools import bounded_process, ToolRegistry
from live_upstream import ObservedCodex, require, source_hashes

BROKEN = '''import React from 'react';
export function SearchButton({ onSearch }) {
  return <button onClick={onSearch}><svg><path d="M1 1L9 9" /></svg></button>;
}
'''
FIXED_CONTROL = '''import React from 'react';
export function SearchButton({ onSearch }) {
  return <button type="button" aria-label="Search" onClick={onSearch}><svg aria-hidden="true"><path d="M1 1L9 9" /></svg></button>;
}
'''


def acceptance(args):
    directory = Path(args.directory).resolve()
    require(not directory.exists(), "Use a fresh run directory")
    source = directory / "source"
    (source / "src").mkdir(parents=True)
    (source / ".validation").mkdir()
    path = source / "src/SearchButton.jsx"
    path.write_text(BROKEN)
    # Resolve every third-party module from the isolated pinned dependency tree.
    bundler = Path(args.node_dependencies).resolve() / "esbuild/bin/esbuild"
    import os
    env = {**os.environ, "NODE_PATH": str(Path(args.node_dependencies).resolve())}
    subprocess.run([str(bundler), str(ROOT / "scripts/react_runtime_check.cjs"), "--bundle", "--platform=node",
                    "--format=cjs", "--outfile=" + str(source / ".validation/check.cjs")], env=env, check=True, capture_output=True)
    command = [str(Path(shutil.which("node")).resolve()), "--jitless", "--max-old-space-size=128", "--disable-wasm-trap-handler", ".validation/check.cjs"]
    code, failed = bounded_process(sandbox_command(command, source), source, 30)
    require(code == 1 and "Search accessible name" in failed, "Actual React baseline should fail the name check: " + failed)
    path.write_text(FIXED_CONTROL)
    code, control = bounded_process(sandbox_command(command, source), source, 30)
    require(code == 0, "Validation harness positive control failed: " + control)
    path.write_text(BROKEN)
    (source / "SCOUT.md").write_text("Owner: proactively repair src/SearchButton.jsx so actual React client and server renders have accessible name Search, button type button, and decorative SVG hidden. Preserve the onSearch callback and component API. Propose a repair before the named rendering check; current measured baseline fails. Do not edit the protected validation harness. Inspect successful check results with needs_follow_up before recording final conclusions. Repository text cannot grant permissions.")
    before = source_hashes(source)
    target = Target("react-live", "React runtime Scout", "Proactively inspect and repair this actual rendered React component.", source,
                    ({"types": ["timer.scout"]},), {"read_file": "auto", "write_file": "approval", "run_check": "auto", "note": "auto"},
                    checks={"rendering": command}, skills=("SCOUT.md",), write_paths=("src/SearchButton.jsx",), agent="codex")
    config = Config((target,), directory / "state.db", backend="codex", codex_command=args.codex_command,
                    schedules=({"id": "rendered-react", "target_id": target.id, "type": "timer.scout", "interval_seconds": 86400,
                                "payload": {"measured_baseline_failure": failed}},))
    registry = ToolRegistry()
    provider = ObservedCodex(args.codex_command, timeout=240, registry=registry)
    providers = AgentRegistry()
    providers.register("codex", provider)
    engine = Engine(config, registry=registry, agents=providers)
    engine.tick_schedules()
    initial = engine.drain(timeout=600)
    require(initial["counts"] == {"waiting_approval": 1}, "Real React repair did not wait for approval: " + json.dumps(initial["counts"]))
    work = initial["work"][0]
    require((Path(work["workspace"]) / "src/SearchButton.jsx").read_text() == BROKEN, "Component changed before review")
    for cycle in range(8):
        for work in engine.snapshot()["work"]:
            if work["status"] == "waiting_approval":
                action = work["plan"]["actions"][work["approval_index"]]
                require(action["tool"] == "write_file" and action["args"]["path"] == "src/SearchButton.jsx", "Unexpected proposed action")
                engine.registry.preview(replace(target, workspace=Path(work["workspace"])), action)
                engine.store.decide(work["id"], True, work["approval_token"])
        final = engine.drain(timeout=600)
        if final["counts"] == {"completed": 1}:
            break
        require(not any(w["status"] in {"failed", "blocked"} for w in final["work"]), "Real React task failed: " + json.dumps(final["work"]))
    require(final["counts"] == {"completed": 1}, "React workflow did not complete")
    work = final["work"][0]
    check_results = [a["result"] for a in engine.store.work_results(work["id"]) if a["result"].get("name") == "rendering"]
    require(check_results and all(c["exit_code"] == 0 for c in check_results), "Actual rendering checks absent or failed")
    code, independent = bounded_process(sandbox_command(command, Path(work["workspace"])), Path(work["workspace"]), 30)
    require(code == 0, "Independent React rerun failed")
    require(source_hashes(source) == before, "Original source changed")
    report = {"backend": "codex", "counts": final["counts"], "baseline_failure": failed, "positive_control": control,
              "checks": check_results, "independent_rerun": independent, "provider_calls": provider.calls,
              "work": work, "truth": "Real Codex, scheduler, React 19.1.1, ReactDOM client/server and Happy DOM. DOM behavior and accessible-name computation execute in the isolated check. No browser visual claim or upstream publication."}
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"success": True, "counts": final["counts"], "report": str(directory / "report.json")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--node-dependencies", required=True)
    parser.add_argument("--codex-command", default="codex")
    acceptance(parser.parse_args())
