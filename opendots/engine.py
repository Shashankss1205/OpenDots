from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fnmatch
import hashlib
import json
import os
import threading
import time
import uuid

from .agents import AgentRegistry, CodexAgent, DemoAgent, validate_plan
from .store import Store
from .tools import ToolRegistry, CheckFailed
from .workspaces import Workspaces


@contextmanager
def process_lock(database):
    """Single scheduler process per database; threads are coordinated through SQLite."""
    path = str(database) + ".lock"
    handle = open(path, "a+b")
    try:
        if os.name == "posix":
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another OpenDots scheduler is using this database") from None
        else:
            import msvcrt
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("Another OpenDots scheduler is using this database") from None
        yield
    finally:
        handle.close()


class Engine:
    def __init__(self, config, agent=None, registry=None, agents=None):
        self.config = config
        storage = config.database.resolve().parent / "workspaces"
        for target in config.targets:
            if config.database.resolve().is_relative_to(target.workspace.resolve()) or storage.is_relative_to(target.workspace.resolve()):
                raise ValueError("Runtime database and managed storage must be outside source workspaces")
        self.targets = {target.id: target for target in config.targets}
        self.store = Store(config.database)
        self.store.queue_limit = config.max_queued_per_target
        self.store.event_rate_limit = config.max_events_per_minute
        self.store.aging_seconds = config.priority_aging_seconds
        self.store.register_targets(config.targets)
        self.registry = registry or ToolRegistry(config.sandbox)
        self.agents = agents or AgentRegistry()
        if not agents:
            self.agents.register("demo", DemoAgent())
            self.agents.register("codex", CodexAgent(config.codex_command, config.model, config.agent_timeout, self.registry, config.context_limits))
        self.agent = agent
        database_key = hashlib.sha256(config.database.name.encode()).hexdigest()[:16]
        self.workspaces = Workspaces(config.database.parent / "workspaces" / database_key, self.store)
        from .sources import SourceRegistry
        self.sources = SourceRegistry(config.sources, self.store)
        self.sources.workers = config.source_workers
        if config.plugins:
            from .extensions import ExtensionAPI, load_extensions
            load_extensions(config.plugins, ExtensionAPI(self.agents, self.registry, self.sources))
        if agent is None:
            for target in config.targets:
                self.agents.get(target.agent or config.backend)
        self.stop_event = threading.Event()

    def ingest(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("type"), str) or not raw["type"]:
            raise ValueError("Event requires a nonempty type")
        if not isinstance(raw.get("payload", {}), dict):
            raise ValueError("Event payload must be an object")
        event = {"id": raw.get("id", str(uuid.uuid4())), "type": raw["type"],
                 "source": raw.get("source", "local"), "payload": raw.get("payload", {})}
        if not isinstance(event["id"], str) or not 0 < len(event["id"]) <= 256:
            raise ValueError("Invalid event ID")
        if not isinstance(event["source"], str) or len(event["type"]) > 256:
            raise ValueError("Invalid source/type")
        if "target_id" in raw:
            if raw["target_id"] not in self.targets:
                raise ValueError("Unknown target ID")
            event["target_id"] = raw["target_id"]
        if "priority" in raw:
            if type(raw["priority"]) is not int or not 0 <= raw["priority"] <= 100:
                raise ValueError("Priority must be an integer from 0 to 100")
            event["priority"] = raw["priority"]
        if len(json.dumps(event).encode()) > 256_000:
            raise ValueError("Event exceeds V0 size limit")
        matches = []
        for target in self.targets.values():
            if "target_id" in event and event["target_id"] != target.id:
                continue
            subscribed = any(
                any(fnmatch.fnmatchcase(event["type"], pattern) for pattern in rule["types"])
                and (not rule.get("sources") or event["source"] in rule["sources"])
                and (not rule.get("repos") or event["payload"].get("repo") in rule["repos"])
                for rule in target.subscriptions)
            if not subscribed:
                continue
            severity = event["payload"].get("severity")
            if severity is not None and not isinstance(severity, str):
                raise ValueError("Severity must be a string")
            default = {"critical": 100, "high": 80, "normal": 50, "low": 10}.get(severity, 50)
            priority = event.get("priority", 5 if event["type"] == "github.star" else default)
            reason = "Explicit priority" if "priority" in event else f"Severity rule: {severity or 'normal'}"
            matches.append((target.id, priority, priority >= target.minimum_priority, reason))
        return self.store.ingest(event, matches)

    def process(self, work):
        target = self.targets[work["target_id"]]
        try:
            target = self.workspaces.prepare(target, work)
            while True:
                if self.store.cancellation_requested(work["id"]):
                    self.store.finish(work, "cancelled", "Owner cancelled work at an action boundary")
                    return
                if self.stop_event.is_set():
                    self.store.finish(work, "interrupted", "Service stopped between bounded actions; inspect before retry")
                    return
                if work["plan"]:
                    plan = validate_plan(json.loads(work["plan"]), target, self.registry)
                    enforced = self._require_checks(target, work, plan)
                    if enforced != plan:
                        # Append only: retain the executed cursor and the exact
                        # approved action when resuming an older persisted plan.
                        with self.store.connect() as db:
                            db.execute("UPDATE work SET plan=? WHERE id=?", (json.dumps(enforced), work["id"]))
                        plan = enforced
                else:
                    if work.get("planning_round", 0) >= self.config.max_planning_rounds:
                        self.store.finish(work, "blocked", "Configured planning round budget exhausted")
                        return
                    event = self.store.event(work["event_id"])
                    provider = self.agent or self.agents.get(target.agent or self.config.backend)
                    state = self.store.state(target.id)
                    state["task_action_results"] = self.store.work_results(work["id"])
                    state["current_task"] = {"work_id": work["id"],
                                             "planning_round": work.get("planning_round", 0) + 1,
                                             "max_planning_rounds": self.config.max_planning_rounds,
                                             "phase": "continuing" if work.get("planning_round", 0) else "initial"}
                    plan = validate_plan(provider.plan(target, event, state), target, self.registry)
                    plan = self._require_checks(target, work, plan)
                    self.store.save_plan(work["id"], plan)
                    work["planning_round"] = work.get("planning_round", 0) + 1
                work["summary"] = plan["summary"]
                if plan.get("outcome") == "blocked":
                    self.store.finish(work, "blocked", plan["summary"])
                    return
                retry_check = False
                for index in range(work["next_action"], len(plan["actions"])):
                    if self.store.cancellation_requested(work["id"]):
                        self.store.finish(work, "cancelled", "Owner cancelled work at an action boundary")
                        return
                    if self.stop_event.is_set():
                        self.store.finish(work, "interrupted", "Service stopped between bounded actions; inspect before retry")
                        return
                    action = plan["actions"][index]
                    mode = target.policy.get(action["tool"], "deny")
                    if mode == "deny":
                        self.store.finish(work, "blocked", f"Policy denies tool: {action['tool']}")
                        return
                    if mode == "draft":
                        self.store.finish(work, "drafted", "Policy kept the plan as a draft; no action at this cursor was executed")
                        return
                    context = self._approval_context(target, action) if mode in {"approval", "ask"} else None
                    if mode in {"approval", "ask"} and (work["approved_index"] != index
                            or work.get("approval_context") != context):
                        self.store.await_approval(work, index, action, self.registry.preview(target, action), context)
                        return
                    try:
                        result = self.registry.execute(target, action)
                    except CheckFailed as exc:
                        self.store.action_failed(work, index, exc.result, action["tool"])
                        provider = self.agent or self.agents.get(target.agent or self.config.backend)
                        if isinstance(provider, DemoAgent) or work["repair_attempts"] > self.config.max_repair_attempts:
                            raise
                        retry_check = True
                        break
                    self.store.action_done(work, index, result, action["tool"])
                if retry_check:
                    work.update(plan=None, next_action=0, approved_index=None)
                    continue
                if plan.get("outcome") != "needs_follow_up":
                    break
                work.update(plan=None, next_action=0, approved_index=None)
            missing = set(target.required_checks) - self._passed_checks(work, target)
            if missing:
                raise ValueError("Required completion checks lack current evidence: " + ", ".join(sorted(missing)))
            from .goals import evaluate
            goals = evaluate(target)
            if any(not goal["passed"] for goal in goals):
                raise ValueError("Owner-defined success conditions are not satisfied: " + json.dumps(goals))
            artifact = self.workspaces.finalize(work)
            if set(target.required_checks) - self._passed_checks(work, target):
                raise ValueError("Workspace changed while retaining the artifact; completion evidence is stale")
            results = self.store.work_results(work["id"])
            checks = [item["result"]["name"] for item in results if isinstance(item["result"], dict)
                      and item["result"].get("exit_code") == 0 and "name" in item["result"]]
            work["summary"] = "Local workflow completed. " + (
                "Passed configured checks: " + ", ".join(dict.fromkeys(checks)) + ". " if checks else
                "No configured checks were requested. ") + (
                "Retained the local change proposal branch and patch." if artifact["changed"] else
                "Recorded the investigation and target memory.")
            if evaluate(target) != goals:
                raise ValueError("Success conditions changed during artifact retention")
            if goals:
                with self.store.connect() as db:
                    self.store.log(db,"goals_verified",goals,target.id,work["id"])
            self.store.finish(work, artifact=artifact)
        except Exception as exc:
            self.store.finish(work, "failed", str(exc)[:8000])

    def _approval_context(self, target, action):
        from .evidence import workspace_fingerprint
        return json.dumps({"workspace": str(target.workspace.resolve()),
                           "fingerprint": workspace_fingerprint(target.workspace),
                           "checks": target.checks, "sandbox": self.registry.sandbox,
                           "policy": target.policy, "write_paths": target.write_paths,
                           "required_checks": target.required_checks, "success_conditions": target.success_conditions,
                           "protected_paths": target.protected_paths}, sort_keys=True)

    def _passed_checks(self, work, target):
        from .evidence import check_signature, workspace_fingerprint
        satisfied = set()
        current = workspace_fingerprint(target.workspace) if target.required_checks else None
        for item in self.store.work_results(work["id"]):
            if item["tool"] in {"write_file", "replace_text"}:
                satisfied.clear()
            elif item["tool"] == "run_check" and item["result"].get("exit_code") == 0:
                result = item["result"]
                name = result["name"]
                if (name in target.checks and result.get("workspace_fingerprint") == current
                        and result.get("check_signature") == check_signature(target, name, self.registry.sandbox)):
                    satisfied.add(name)
        return satisfied

    def _require_checks(self, target, work, plan):
        if plan.get("outcome", "complete") != "complete" or not target.required_checks:
            return plan
        satisfied = self._passed_checks(work, target)
        for action in plan["actions"][work["next_action"]:]:
            if action["tool"] in {"write_file", "replace_text"}:
                satisfied.clear()
            elif action["tool"] == "run_check":
                # Failed execution terminates the task before completion.
                satisfied.add(action["args"]["name"])
        missing = [name for name in dict.fromkeys(target.required_checks) if name not in satisfied]
        if not missing:
            return plan
        with self.store.connect() as db:
            self.store.log(db, "required_checks_added", {"source": "owner_configuration", "names": missing,
                "original_model_plan": plan}, target.id, work["id"])
        # Owner-required checks use the same policy and approval path as all tools.
        return {**plan, "actions": [*plan["actions"], *[
            {"tool": "run_check", "args": {"name": name}} for name in missing]]}

    def _drain(self, timeout=60):
        deadline = time.monotonic() + timeout
        with ThreadPoolExecutor(max_workers=self.config.workers) as pool:
            running = set()
            while True:
                for future in tuple(running):
                    if future.done():
                        future.result()
                        running.remove(future)
                while len(running) < self.config.workers:
                    work = self.store.claim(self.targets)
                    if work is None:
                        break
                    running.add(pool.submit(self.process, work))
                if not running:
                    return self.store.snapshot()
                if time.monotonic() > deadline:
                    raise TimeoutError("Drain deadline exceeded; running bounded tools will finish before shutdown")
                self.stop_event.wait(0.02)

    def drain(self, timeout=60):
        with process_lock(self.config.database):
            self.store.recover_interrupted()
            return self._drain(timeout)

    def tick_schedules(self, now=None):
        now = int(time.time() if now is None else now)
        for schedule in self.config.schedules:
            tick = now // int(schedule["interval_seconds"])
            with self.store.connect() as db:
                found = db.execute("SELECT last_tick FROM schedule_ticks WHERE id=?", (schedule["id"],)).fetchone()
            if found and found[0] >= tick:
                continue
            # Ingest first; deterministic ID makes crash between ingestion and tick persistence safe.
            event = {"id": f"schedule:{schedule['id']}:{tick}", "type": schedule["type"], "source": "timer",
                     "target_id": schedule["target_id"], "payload": schedule.get("payload", {})}
            self.ingest(event)
            with self.store.connect() as db:
                db.execute("INSERT INTO schedule_ticks VALUES(?,?) ON CONFLICT(id) DO UPDATE SET last_tick=excluded.last_tick",
                           (schedule["id"], tick))

    def worker_loop(self):
        """Caller owns the process lock; one shared loop for service and CLI workers."""
        with ThreadPoolExecutor(max_workers=self.config.workers) as pool:
            running = set()
            while not self.stop_event.is_set():
                self.tick_schedules()
                self.sources.poll_due(self.ingest, asynchronous=True)
                for future in tuple(running):
                    if future.done():
                        future.result()
                        running.remove(future)
                while len(running) < self.config.workers:
                    work = self.store.claim(self.targets)
                    if work is None:
                        break
                    running.add(pool.submit(self.process, work))
                self.stop_event.wait(0.1)

    def snapshot(self):
        data = self.store.snapshot()
        for target in data["targets"]:
            if target["id"] in self.targets:
                spec = self.targets[target["id"]]
                target.update(name=spec.name, objective=spec.objective, policy=spec.policy, subscriptions=spec.subscriptions)
        data.update(backend=self.config.backend, workers=self.config.workers,
                    tool_names=list(self.registry.handlers), sandbox=self.config.sandbox,
                    source_names=[item["id"] for item in self.config.sources], agent_names=list(self.agents.agents))
        return data
