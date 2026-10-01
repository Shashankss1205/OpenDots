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


def load_config(path: Path) -> Config:
    path = path.resolve()
    raw = json.loads(path.read_text())
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
                              tuple(item.get("write_paths", ["*"])), tuple(required_checks)))
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
                  tuple(sources), sandbox, int(raw.get("max_planning_rounds", 8)), int(raw.get("max_repair_attempts", 2)), int(raw.get("source_workers", 4)))
