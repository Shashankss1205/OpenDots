from dataclasses import dataclass, field
import json
from pathlib import Path


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    objective: str
    workspace: Path
    subscriptions: tuple[dict, ...]
    policy: dict[str, str]
    recipes: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    minimum_priority: int = 20
    desired_state: dict = field(default_factory=dict)
    skills: tuple[str, ...] = ()
    agent: str | None = None
    write_paths: tuple[str, ...] = ("*",)
    required_checks: tuple[str, ...] = ()
    success_conditions: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Config:
    targets: tuple[Target, ...]
    database: Path
    workers: int = 4
    backend: str = "demo"
    agent_timeout: int = 180
    codex_command: str = "codex"
    model: str | None = None
    schedules: tuple[dict, ...] = ()
    sources: tuple[dict, ...] = ()
    sandbox: str = "bubblewrap"
    max_planning_rounds: int = 8
    max_repair_attempts: int = 2
    source_workers: int = 4
    plugins: tuple[str, ...] = ()


def validate_config(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("targets"), list):
        raise ValueError("config.targets must be an array")
    for key, minimum in (("workers",1),("source_workers",1),("agent_timeout",1),
                         ("max_planning_rounds",1),("max_repair_attempts",0)):
        if key in raw and (type(raw[key]) is not int or raw[key] < minimum):
            raise ValueError(f"config.{key} must be an integer >= {minimum}")
    if not isinstance(raw.get("plugins",[]),list) or any(not isinstance(v,str) or not v for v in raw.get("plugins",[])):
        raise ValueError("config.plugins must be an array of installed extension names")
    for index, target in enumerate(raw["targets"]):
        prefix = f"targets[{index}]"
        if not isinstance(target, dict):
            raise ValueError(f"{prefix} must be an object")
        for key in ("id","name","objective","workspace"):
            if not isinstance(target.get(key), str) or not target[key]:
                raise ValueError(f"{prefix}.{key} must be a nonempty string")
        for key in ("policy","checks","recipes","desired_state"):
            if not isinstance(target.get(key, {}), dict):
                raise ValueError(f"{prefix}.{key} must be an object")
        for key in ("write_paths","skills","required_checks"):
            values = target.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v,str) or not v for v in values):
                raise ValueError(f"{prefix}.{key} must be an array of nonempty strings")
        for name, command in target.get("checks", {}).items():
            if not isinstance(command,list) or not command or any(not isinstance(v,str) for v in command):
                raise ValueError(f"{prefix}.checks.{name} must be a nonempty argv array")
        conditions = target.get("success_conditions", [])
        if not isinstance(conditions,list):
            raise ValueError(f"{prefix}.success_conditions must be an array")
        for condition in conditions:
            if not isinstance(condition,dict) or not all(isinstance(condition.get(k),str) and condition[k] for k in ("name","path")):
                raise ValueError(f"{prefix}.success_conditions need name and path")
            if condition.get("format","text") not in {"text","json"} or ("equals" in condition) == ("minimum" in condition):
                raise ValueError(f"{prefix}.success_conditions need a supported format and exactly one comparator")
            if "minimum" in condition and type(condition["minimum"]) not in (int,float):
                raise ValueError(f"{prefix}.success_conditions.minimum must be numeric")
        priority = target.get("minimum_priority",20)
        if type(priority) is not int or not 0 <= priority <= 100:
            raise ValueError(f"{prefix}.minimum_priority must be an integer from 0 to 100")
        if not isinstance(target.get("subscriptions", []), list):
            raise ValueError(f"{prefix}.subscriptions must be an array")
        for rule in target.get("subscriptions", []):
            if not isinstance(rule,dict):
                raise ValueError(f"{prefix}.subscriptions entries must be objects")
            for key in ("types","repos","sources"):
                if key in rule and (not isinstance(rule[key],list) or any(not isinstance(v,str) for v in rule[key])):
                    raise ValueError(f"{prefix}.subscriptions.{key} must be an array of strings")
    for collection in ("sources","schedules"):
        values = raw.get(collection, [])
        if not isinstance(values,list) or any(not isinstance(v,dict) for v in values):
            raise ValueError(f"config.{collection} must be an array of objects")
        for value in values:
            if not isinstance(value.get("id"),str) or not value["id"]:
                raise ValueError(f"{collection}.id must be a nonempty string")
            if "interval_seconds" in value and (type(value["interval_seconds"]) is not int or value["interval_seconds"] < 1):
                raise ValueError(f"{collection}.interval_seconds must be positive")
    for schedule in raw.get("schedules",[]):
        for key in ("target_id","type","interval_seconds"):
            if key not in schedule:
                raise ValueError(f"schedules.{key} is required")


def load_config(path: Path) -> Config:
    path = path.resolve()
    raw = json.loads(path.read_text())
    validate_config(raw)
    base = path.parent
    targets = []
    ids = set()
    for item in raw["targets"]:
        target_id = item["id"]
        if not isinstance(target_id, str) or not target_id or target_id in ids:
            raise ValueError("Target IDs must be nonempty and unique")
        ids.add(target_id)
        root = (base / item["workspace"]).resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist for {target_id}: {root}")
        policy = item.get("policy", {})
        if any(mode not in {"auto", "approval", "ask", "draft", "deny"} for mode in policy.values()):
            raise ValueError("Policy values must be auto, approval/ask, draft, or deny")
        subscriptions = item.get("subscriptions", [])
        required_checks = item.get("required_checks", [])
        if (not isinstance(required_checks, list)
                or any(not isinstance(name, str) or name not in item.get("checks", {}) for name in required_checks)):
            raise ValueError("required_checks must list configured check names")
        if not subscriptions or any(not isinstance(s, dict) or not isinstance(s.get("types"), list)
                                    or not s["types"] or any(not isinstance(v, str) for v in s["types"])
                                    for s in subscriptions):
            raise ValueError("Each target needs subscriptions with a types array")
        for other in targets:
            if root.is_relative_to(other.workspace) or other.workspace.is_relative_to(root):
                raise ValueError("Parallel targets need separate, non-overlapping workspaces or worktrees")
        targets.append(Target(target_id, item["name"], item["objective"], root,
                              tuple(subscriptions), policy, item.get("recipes", {}),
                              item.get("checks", {}), int(item.get("minimum_priority", 20)),
                              item.get("desired_state", {}), tuple(item.get("skills", [])), item.get("agent"),
                              tuple(item.get("write_paths", ["*"])), tuple(required_checks), tuple(item.get("success_conditions", []))))
    workers = int(raw.get("workers", 4))
    if workers < 1:
        raise ValueError("workers must be positive")
    backend = raw.get("backend", "demo")
    if not isinstance(backend, str) or not backend:
        raise ValueError("backend must name a registered provider")
    schedules = raw.get("schedules", [])
    schedule_ids = set()
    for schedule in schedules:
        if schedule["id"] in schedule_ids or schedule["target_id"] not in ids:
            raise ValueError("Schedules require unique IDs and a known target")
        schedule_ids.add(schedule["id"])
        if int(schedule["interval_seconds"]) < 1:
            raise ValueError("Schedule interval must be positive")
    agent_timeout = int(raw.get("agent_timeout", 180))
    if agent_timeout < 1:
        raise ValueError("agent_timeout must be positive")
    sandbox = raw.get("sandbox", "bubblewrap")
    if sandbox not in {"bubblewrap", "trusted-local"}:
        raise ValueError("sandbox must be bubblewrap or trusted-local")
    sources = raw.get("sources", [])
    source_ids = set()
    for source in sources:
        if not isinstance(source.get("id"), str) or not source["id"] or source["id"] in source_ids:
            raise ValueError("Source IDs must be nonempty and unique")
        source_ids.add(source["id"])
        if source.get("path"):
            source["path"] = str((base / source["path"]).resolve())
    database = (base / raw.get("database", "../.opendots/state.db")).resolve()
    # Preserve an existing default Spots database without copying or renaming it.
    if database == (base / "../.opendots/state.db").resolve() and not database.exists():
        legacy_database = (base / "../.spots/state.db").resolve()
        if legacy_database.exists():
            database = legacy_database
    return Config(tuple(targets), database,
                  workers, backend, agent_timeout,
                  raw.get("codex_command", "codex"), raw.get("model"), tuple(schedules),
                  tuple(sources), sandbox, int(raw.get("max_planning_rounds", 8)), int(raw.get("max_repair_attempts", 2)), int(raw.get("source_workers", 4)), tuple(raw.get("plugins", [])))
