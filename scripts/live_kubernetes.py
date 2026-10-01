"""Real Codex plus a disposable loopback-only, agentless K3s API server.

Uses actual API admission, controller observation and stored Deployment state.
There is no kubelet or container runtime: this does not assert pod readiness.
The explicitly trusted-local named API check needs the local control plane.
No credentials enter model context or the generated verification report.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opendots.agents import AgentRegistry
from opendots.config import Config, Target
from opendots.engine import Engine
from opendots.tools import bounded_process, ToolRegistry
from live_upstream import ObservedCodex, require, source_hashes

MANIFEST = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "opendots-api-test"},
            "spec": {"replicas": 0, "selector": {"matchLabels": {"app": "opendots-api-test"}},
                     "template": {"metadata": {"labels": {"app": "opendots-api-test"}},
                                  "spec": {"containers": [{"name": "web", "image": "nginx:1.27.5", "ports": [{"containerPort": 80}]}]}}}}


def acceptance(args):
    directory = Path(args.directory).resolve()
    require(not directory.exists(), "Use a fresh run directory")
    directory.mkdir(parents=True)
    control = directory / "private-control-plane"
    control.mkdir(mode=0o700)
    kubeconfig = control / "kubeconfig.yaml"
    k3s = str(Path(args.k3s).resolve())
    # Kubernetes rejects a loopback advertised Service endpoint. The listener
    # stays on loopback; advertise this disposable runtime's private address.
    advertise_address = socket.gethostbyname(socket.gethostname())
    require(not advertise_address.startswith("127."), "A private advertised address is required")
    server_command = [k3s, "server", "--disable-agent", "--bind-address", "127.0.0.1", "--advertise-address", advertise_address,
                      "--https-listen-port", str(args.port), "--data-dir", str(control / "data"),
                      "--write-kubeconfig", str(kubeconfig), "--write-kubeconfig-mode", "600",
                      "--disable", "coredns,servicelb,traefik,local-storage,metrics-server",
                      "--disable-network-policy", "--flannel-backend", "none", "--egress-selector-mode", "disabled"]
    log = (control / "server-private.log").open("wb")
    server = subprocess.Popen(server_command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    client = [k3s, "kubectl", "--kubeconfig", str(kubeconfig), "--request-timeout=10s"]

    def kubectl(*arguments, require_success=True):
        code, output = bounded_process(client + list(arguments), directory, 20)
        if require_success:
            require(code == 0, "Local Kubernetes command failed: " + output[:1500])
        return code, output

    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            require(server.poll() is None, "Local K3s server stopped; inspect the private local log")
            if kubeconfig.exists():
                code, output = kubectl("get", "--raw=/readyz", require_success=False)
                if code == 0 and output.strip() == "ok":
                    break
            time.sleep(.5)
        else:
            raise RuntimeError("Local Kubernetes API did not become ready")
        version = json.loads(kubectl("get", "--raw=/version")[1])["gitVersion"]
        print(json.dumps({"api_ready": version, "port": args.port}), flush=True)
        source = directory / "source"
        source.mkdir()
        (source / ".validation").mkdir()
        (source / "deployment.json").write_text(json.dumps(MANIFEST, indent=2) + "\n")
        (source / ".validation/intent.py").write_text("import json\nd=json.load(open('deployment.json'))\nassert type(d['spec']['replicas']) is int and d['spec']['replicas'] >= 1, 'Deployment must request at least one replica'\nprint('PASS: desired replica intent met')\n")
        (source / "MAINTAINER.md").write_text("Owner: a real local Kubernetes API currently stores this Deployment with zero replicas. Repair only deployment.json to request at least one replica, preserving selector, labels, container image and remaining fields. Validate intent and actual API admission using both named checks, then use supplied action results to conclude. If both already passed after the edit, return complete. This agentless test server does not run workloads; never claim pods ready. No external cluster actions or credential reads are authorized.")
        kubectl("apply", "-f", str(source / "deployment.json"))
        initial = json.loads(kubectl("get", "deployment", "opendots-api-test", "-o", "json")[1])
        require(initial["spec"]["replicas"] == 0, "Actual initial API state must be zero replicas")
        malformed = json.loads(json.dumps(MANIFEST))
        del malformed["spec"]["template"]["spec"]["containers"][0]["image"]
        malformed_path = directory / "invalid-manifest.json"
        malformed_path.write_text(json.dumps(malformed))
        code, rejection = kubectl("apply", "--dry-run=server", "-f", str(malformed_path), require_success=False)
        require(code != 0 and "image" in rejection, "Actual API must reject an invalid pod template")
        before = source_hashes(source)
        checks = {"intent": ["{python}", ".validation/intent.py"],
                  "admission": client + ["apply", "--dry-run=server", "-f", "deployment.json"]}
        target = Target("kubernetes-live", "Actual Kubernetes API", "Repair observed zero-replica desired state and validate real API admission.", source,
                        ({"types": ["kubernetes.observation"]},),
                        {"read_file": "auto", "write_file": "approval", "replace_text": "approval", "run_check": "auto", "note": "auto"},
                        checks=checks, skills=("MAINTAINER.md",), write_paths=("deployment.json",), agent="codex")
        registry = ToolRegistry("trusted-local")
        provider = ObservedCodex(args.codex_command, timeout=240, registry=registry)
        providers = AgentRegistry()
        providers.register("codex", provider)
        config = Config((target,), directory / "state.db", backend="codex", sandbox="trusted-local")
        engine = Engine(config, registry=registry, agents=providers)
        engine.ingest({"id": "actual-api-zero-replicas", "type": "kubernetes.observation", "source": "local-kubernetes-api", "target_id": target.id,
                       "payload": {"observed_generation": initial["metadata"]["generation"], "resource_version": initial["metadata"]["resourceVersion"],
                                   "observed_spec_replicas": initial["spec"]["replicas"], "desired_minimum": 1}})
        first = engine.drain(timeout=600)
        require(first["counts"] == {"waiting_approval": 1}, "Actual Kubernetes repair must wait for review")
        first_work = first["work"][0]
        require(json.loads((Path(first_work["workspace"]) / "deployment.json").read_text())["spec"]["replicas"] == 0, "Write occurred before approval")
        for cycle in range(8):
            for work in engine.snapshot()["work"]:
                if work["status"] == "waiting_approval":
                    action = work["plan"]["actions"][work["approval_index"]]
                    require(action["tool"] in {"write_file", "replace_text"} and action["args"]["path"] == "deployment.json", "Unexpected Kubernetes proposal")
                    registry.preview(replace(target, workspace=Path(work["workspace"])), action)
                    engine.store.decide(work["id"], True, work["approval_token"])
            final = engine.drain(timeout=600)
            if final["counts"] == {"completed": 1}:
                break
            require(not any(w["status"] in {"failed", "blocked"} for w in final["work"]), "Kubernetes repair failed")
        require(final["counts"] == {"completed": 1}, "Kubernetes workflow incomplete")
        work = final["work"][0]
        results = [a["result"] for a in engine.store.work_results(work["id"]) if "exit_code" in a["result"]]
        require({c["name"] for c in results if c["exit_code"] == 0} >= {"intent", "admission"}, "Both actual checks must pass")
        # Apply the reviewed repair only to the newly-created disposable server.
        kubectl("apply", "-f", str(Path(work["workspace"]) / "deployment.json"))
        actual = json.loads(kubectl("get", "deployment", "opendots-api-test", "-o", "json")[1])
        require(actual["spec"]["replicas"] >= 1, "Actual API readback does not match approved repair")
        require(source_hashes(source) == before, "Original source changed")
        report = {"backend": "codex", "api_version": version, "counts": final["counts"], "initial_spec_replicas": 0,
                  "final_spec_replicas": actual["spec"]["replicas"], "invalid_template_rejected": True,
                  "checks": results, "provider_calls": provider.calls, "work": work,
                  "truth": "Real ephemeral agentless K3s API, observed zero replicas, real Codex repair, API admission and applied/read-back state. Named local API checks explicitly use trusted-local. No kubelet/container runtime or pod readiness assertion; no external cluster contacted."}
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"success": True, "report": str(directory / "report.json"), "actual_replicas": actual["spec"]["replicas"]}), flush=True)
    finally:
        import os
        if server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait()
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k3s", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--port", type=int, default=16443)
    parser.add_argument("--codex-command", default="codex")
    acceptance(parser.parse_args())
