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
    from . import __version__
    parser.add_argument("--version", action="version", version="OpenDots " + __version__)
    parser.add_argument("--config", type=Path, default=None)
    commands = parser.add_subparsers(dest="command")
    tui = commands.add_parser("tui", help="Open the interactive terminal client")
    tui.add_argument("--url", default="http://127.0.0.1:8765")
    service = commands.add_parser("service", help="Manage a user background service")
    service.add_argument("action", choices=["install","start","stop","status"])
    service.add_argument("--name", default="opendots")
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
    proposal = commands.add_parser("proposal", help="Inspect a completed local proposal")
    proposal.add_argument("work_id", type=int)
    accept = commands.add_parser("accept", help="Use a reviewed proposal as the base of future work")
    accept.add_argument("work_id", type=int)
    accept.add_argument("--commit", required=True)
    sync = commands.add_parser("sync", help="Replace the task base with a fresh source snapshot; stop the service first")
    sync.add_argument("target_id")
    backup = commands.add_parser("backup", help="Back up stopped runtime data to a new directory")
    backup.add_argument("directory", type=Path)
    restore = commands.add_parser("restore", help="Restore a backup to its original, empty runtime paths")
    restore.add_argument("directory", type=Path)
    cleanup = commands.add_parser("cleanup", help="Preview or archive old terminal task workspaces")
    cleanup.add_argument("--older-than", type=int, default=30, metavar="DAYS")
    cleanup.add_argument("--apply", action="store_true")
    pause = commands.add_parser("pause")
    pause.add_argument("target_id")
    resume = commands.add_parser("resume")
    resume.add_argument("target_id")
    retry = commands.add_parser("retry", help="Replan failed work after inspecting effects")
    retry.add_argument("work_id", type=int)
    retry.add_argument("--inspected", action="store_true")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("work_id", type=int)
    decide = commands.add_parser("decide")
    decide.add_argument("work_id", type=int)
    decide.add_argument("decision", choices=["approve", "reject"])
    args = parser.parse_args()
    try:
        if args.command in {None, "tui"}:
            from .terminal import run
            run(getattr(args,"url","http://127.0.0.1:8765"))
            return 0
        if args.command == "init":
            path = initialize(args.directory, args.workspace, args.backend)
            print(f"Created {path}\nNext: opendots --config {path} doctor\nThen: opendots --config {path} serve")
            return 0
        args.config = args.config or default_config()
        if args.command == "service":
            from .service import manage
            if args.action == "install":
                load_config(args.config)
            print(json.dumps(manage(args.action,args.config,args.name),indent=2))
            return 0
        if args.command == "doctor":
            result = diagnose(args.config)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        config = load_config(args.config)
        if args.command in {"backup", "restore", "cleanup"}:
            from . import maintenance
            if args.command == "backup":
                result = maintenance.backup(config, args.directory, args.config)
            elif args.command == "restore":
                result = maintenance.restore(config, args.directory)
            else:
                result = maintenance.cleanup(config, args.older_than, args.apply)
            print(json.dumps(result, indent=2))
            return 0
        engine = Engine(config)
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
        elif args.command in {"pause", "resume"}:
            print(json.dumps(engine.store.pause(args.target_id, args.command == "pause")))
        elif args.command == "retry":
            print(json.dumps(engine.retry(args.work_id,args.inspected)))
        elif args.command == "cancel":
            print(json.dumps(engine.store.cancel(args.work_id)))
        elif args.command == "proposal":
            print(json.dumps(engine.workspaces.proposal(args.work_id), indent=2))
        elif args.command == "accept":
            print(json.dumps(engine.workspaces.accept(args.work_id, args.commit)))
        elif args.command == "sync":
            from .engine import process_lock
            with process_lock(engine.config.database):
                print(json.dumps(engine.workspaces.sync(engine.targets[args.target_id]), indent=2))
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
