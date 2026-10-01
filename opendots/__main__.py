import argparse
import json
from pathlib import Path
import sys
import uuid

from .config import load_config
from .engine import Engine
from .server import serve
from .setup import default_config, initialize, diagnose


def main():
    parser = argparse.ArgumentParser(description="OpenDots local event-driven agent prototype")
    parser.add_argument("--config", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create first-run configuration")
    init.add_argument("--directory", type=Path)
    init.add_argument("--workspace", type=Path)
    init.add_argument("--backend", choices=["demo", "codex"], default="demo")
    commands.add_parser("doctor", help="Check configuration, dependencies and sandbox")
    run = commands.add_parser("serve", help="Run workers and the local dashboard")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--host", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1")
    ingest = commands.add_parser("ingest", help="Ingest a JSON object or array from a file")
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--fresh-ids", action="store_true", help="Give sample events new IDs")
    drain = commands.add_parser("drain", help="Process the queue until idle or awaiting approval")
    drain.add_argument("--timeout", type=int, default=300)
    commands.add_parser("status")
    decide = commands.add_parser("decide")
    decide.add_argument("work_id", type=int)
    decide.add_argument("decision", choices=["approve", "reject"])
    args = parser.parse_args()
    try:
        if args.command == "init":
            path = initialize(args.directory, args.workspace, args.backend)
            print(f"Created {path}\nNext: opendots --config {path} doctor\nThen: opendots --config {path} serve")
            return 0
        args.config = args.config or default_config()
        if args.command == "doctor":
            result = diagnose(args.config)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        engine = Engine(load_config(args.config))
        if args.command == "serve":
            serve(engine, args.host, args.port)
        elif args.command == "ingest":
            events = json.loads(args.file.read_text())
            if not isinstance(events, list):
                events = [events]
            for event in events:
                if args.fresh_ids:
                    event["id"] = str(uuid.uuid4())
                print(json.dumps(engine.ingest(event)))
        elif args.command == "drain":
            result = engine.drain(args.timeout)
            print(json.dumps({"counts": result["counts"], "work": result["work"]}, indent=2))
        elif args.command == "decide":
            print(json.dumps(engine.store.decide(args.work_id, args.decision == "approve")))
        else:
            print(json.dumps(engine.snapshot(), indent=2))
    except (ValueError, RuntimeError, TimeoutError, OSError, KeyError) as exc:
        print(f"OpenDots: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
