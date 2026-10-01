"""Narrow fixture check; this does not validate a live Kubernetes deployment."""
import json
from pathlib import Path
import sys

deployment = json.loads(Path("deployment.json").read_text())
replicas = deployment["spec"]["replicas"]
if not isinstance(replicas, int) or replicas < 1:
    print("FAIL: a local deployment must request at least one replica")
    sys.exit(1)
print(f"PASS: local deployment requests {replicas} replicas")
