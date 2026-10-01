"""Explicit destinations and a durable audit-to-notification outbox."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from .http_client import post_json, validate_url


EVENTS = ("approval_requested", "approval_rejected", "work_completed", "work_failed",
          "work_blocked", "work_drafted", "work_interrupted", "work_cancelled",
          "source_failed", "source_gap_detected")
DEFAULT_EVENTS = ("approval_requested", "work_completed", "work_failed", "work_blocked")


class NotificationRegistry:
    def __init__(self):
        self.factories, self.validators = {}, {}

    def register(self, kind, factory, *, validate_config=None):
        if not isinstance(kind, str) or not kind or not callable(factory):
            raise ValueError("Notification adapter requires a name and callable factory")
        if kind in self.factories:
            raise ValueError("Notification adapter already registered")
        if validate_config is not None and not callable(validate_config):
            raise ValueError("Notification validator must be callable")
        self.factories[kind], self.validators[kind] = factory, validate_config


def validate_routes(configs, registry=None, targets=None):
    if not isinstance(configs, (list, tuple)):
        raise ValueError("notifications must be an array")
    ids = set()
    for config in configs:
        if not isinstance(config, dict) or not isinstance(config.get("id"), str) or not config["id"] or config["id"] in ids:
            raise ValueError("Notification IDs must be nonempty and unique")
        ids.add(config["id"])
        if not isinstance(config.get("kind"), str) or not config["kind"]:
            raise ValueError("Notification kind must name an adapter")
        if type(config.get("enabled", True)) is not bool:
            raise ValueError("Notification enabled must be boolean")
        events = config.get("events", list(DEFAULT_EVENTS))
        if not isinstance(events, list) or not events or any(e not in EVENTS for e in events):
            raise ValueError("Notification events must list supported runtime lifecycle events")
        selected = config.get("targets", [])
        if not isinstance(selected, list) or any(not isinstance(t, str) or (targets is not None and t not in targets) for t in selected):
            raise ValueError("Notification targets must list known target IDs")
        for field, default, maximum in (("timeout_seconds", 10, 60), ("max_attempts", 5, 20), ("max_pending", 1000, 100000)):
            value = config.get(field, default)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"Notification {field} must be between 1 and {maximum}")
        if registry is not None:
            if config["kind"] not in registry.factories:
                raise ValueError(f"Unknown notification adapter: {config['kind']}")
            validator = registry.validators[config["kind"]]
            if validator:
                validator(deepcopy(config))


def validate_webhook(config):
    if not isinstance(config.get("url_env"), str) or not config["url_env"]:
        raise ValueError("Webhook url_env must name an environment variable")
    if config.get("format", "json") not in {"json", "slack", "discord"}:
        raise ValueError("Webhook format must be json, slack or discord")
    if "token_env" in config and (not isinstance(config["token_env"], str) or not config["token_env"]):
        raise ValueError("Webhook token_env must name an environment variable")


def validate_jsonl(config):
    if not isinstance(config.get("path"), str) or not config["path"]:
        raise ValueError("JSONL notifications require a path")


class WebhookNotification:
    def __init__(self, config):
        self.config = config

    def send(self, message):
        url = os.environ.get(self.config["url_env"])
        if not url:
            raise RuntimeError("Notification webhook URL environment variable is missing")
        validate_url(url)
        headers = {"Idempotency-Key": message["id"]}
        if self.config.get("token_env"):
            token = os.environ.get(self.config["token_env"])
            if not token:
                raise RuntimeError("Notification token environment variable is missing")
            headers["Authorization"] = "Bearer " + token
        kind = self.config.get("format", "json")
        text = f"OpenDots: {message['event']} | agent {message['target_id'] or '-'} | work {message['work_id'] or '-'} | delivery {message['id']}"
        payload = {"text": text} if kind == "slack" else ({"content": text, "allowed_mentions": {"parse": []}} if kind == "discord" else message)
        post_json(url, payload, headers, self.config.get("timeout_seconds", 10), parse_response=False)


class JSONLNotification:
    def __init__(self, config):
        self.path = Path(config["path"])

    def send(self, message):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class NotificationDispatcher:
    def __init__(self, store, configs, registry):
        self.store, self.configs, self.registry = store, deepcopy(configs), registry
        self.routes = {c["id"]: c for c in self.configs if c.get("enabled", True)}
        self.stop_event = threading.Event()
        self.thread = None

    @staticmethod
    def revision(config):
        # Bind queued deliveries to configuration and resolved credential identity without storing secrets.
        secrets = {key: os.environ.get(value) for key, value in config.items() if key.endswith("_env") and isinstance(value, str)}
        return hashlib.sha256(json.dumps([config, secrets], sort_keys=True).encode()).hexdigest()

    def collect(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT COALESCE(MAX(id),0) FROM audit").fetchone()[0]
            for row in db.execute("SELECT id FROM notification_routes").fetchall():
                if row["id"] not in self.routes:
                    db.execute("DELETE FROM notification_routes WHERE id=?", (row["id"],))
            for route_id, config in self.routes.items():
                revision = self.revision(config)
                row = db.execute("SELECT * FROM notification_routes WHERE id=?", (route_id,)).fetchone()
                if not row or row["revision"] != revision:
                    db.execute("INSERT INTO notification_routes VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,cursor=excluded.cursor",
                               (route_id, revision, latest))
                    continue  # New/changed destinations begin with future events, never historical replay.
                remaining = config.get("max_pending", 1000) - db.execute(
                    "SELECT COUNT(*) FROM notifications WHERE route_id=? AND revision=? AND status IN ('pending','retry','sending')", (route_id, revision)).fetchone()[0]
                cursor = row["cursor"]
                for audit in db.execute("SELECT * FROM audit WHERE id>? ORDER BY id LIMIT 500", (cursor,)).fetchall():
                    match = audit["kind"] in config.get("events", DEFAULT_EVENTS) and (not config.get("targets") or audit["target_id"] in config["targets"])
                    if match and remaining <= 0:
                        break  # Preserve audit cursor; task execution never waits for notification capacity.
                    if match:
                        delivery_id = str(uuid.uuid4())
                        message = {"id": delivery_id, "event": audit["kind"], "target_id": audit["target_id"],
                                   "work_id": audit["work_id"], "created": audit["at"]}
                        db.execute("INSERT INTO notifications(id,route_id,revision,audit_id,body,status,attempts,next_attempt,created,updated) VALUES(?,?,?,?,?,'pending',0,0,?,?)",
                                   (delivery_id, route_id, revision, audit["id"], json.dumps(message), time.time(), time.time()))
                        remaining -= 1
                    cursor = audit["id"]
                db.execute("UPDATE notification_routes SET cursor=? WHERE id=?", (cursor, route_id))

    def recover(self):
        with self.store.connect() as db:
            db.execute("UPDATE notifications SET status='retry',next_attempt=0 WHERE status='sending'")

    def retry(self, delivery_id):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM notifications WHERE id=?", (delivery_id,)).fetchone()
            if not row or row["status"] != "failed":
                raise ValueError("Only failed notification deliveries can be retried")
            config = self.routes.get(row["route_id"])
            if not config or self.revision(config) != row["revision"]:
                raise ValueError("Destination changed or is disabled; old notifications cannot be redirected")
            db.execute("UPDATE notifications SET status='retry',attempts=0,error=NULL,next_attempt=0,updated=? WHERE id=?", (time.time(), delivery_id))
        return {"id": delivery_id, "status": "retry"}

    def deliver(self, limit=10):
        for _ in range(limit):
            if self.stop_event.is_set():
                return
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM notifications WHERE status IN ('pending','retry') AND next_attempt<=? ORDER BY next_attempt,created LIMIT 1", (time.time(),)).fetchone()
                if not row:
                    return
                config = self.routes.get(row["route_id"])
                if not config or self.revision(config) != row["revision"]:
                    db.execute("UPDATE notifications SET status='cancelled',error='Destination disabled, removed or changed',updated=? WHERE id=?", (time.time(), row["id"]))
                    continue
                if row["attempts"] >= config.get("max_attempts", 5):
                    db.execute("UPDATE notifications SET status='failed',error='Delivery attempt limit reached',updated=? WHERE id=?", (time.time(), row["id"]))
                    continue
                attempt = row["attempts"] + 1
                db.execute("UPDATE notifications SET status='sending',attempts=?,updated=? WHERE id=?", (attempt, time.time(), row["id"]))
            try:
                self.registry.factories[config["kind"]](deepcopy(config)).send(json.loads(row["body"]))
            except Exception as exc:
                # Extension error text can contain credentials. Store its class only.
                status = "failed" if attempt >= config.get("max_attempts", 5) else "retry"
                error = f"{type(exc).__name__}: notification delivery failed; inspect destination configuration"
                next_attempt = time.time() + min(300, 2 ** min(attempt, 9))
            else:
                status, error, next_attempt = "delivered", None, 0
            with self.store.connect() as db:
                db.execute("UPDATE notifications SET status=?,error=?,next_attempt=?,updated=? WHERE id=?",
                           (status, error, next_attempt, time.time(), row["id"]))

    def start(self):
        if self.thread is not None:
            return
        self.recover()
        self.collect()  # Establish baselines before workers/listeners can produce new work.
        self.thread = threading.Thread(target=self._run, name="opendots-notifications", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.collect()
                self.deliver()
            except Exception:
                # Keep transport/storage failure out of the scheduler; expose dispatcher health.
                self.error = "Notification dispatcher failed; inspect local storage and configuration"
            else:
                self.error = None
            self.stop_event.wait(.5)

    def close(self):
        self.stop_event.set()
        if self.thread:
            # Keep scheduler ownership until the bounded in-flight send has finished.
            self.thread.join()

    def snapshot(self):
        with self.store.connect() as db:
            counts = {r["status"]: r["n"] for r in db.execute("SELECT status,COUNT(*) AS n FROM notifications GROUP BY status")}
            deliveries = [dict(r) for r in db.execute("SELECT id,route_id,audit_id,status,attempts,error,created,updated FROM notifications ORDER BY created DESC LIMIT 50")]
        return {"destinations": [{"id": c["id"], "kind": c["kind"], "enabled": c.get("enabled", True),
                  "events": c.get("events", list(DEFAULT_EVENTS)), "targets": c.get("targets", [])} for c in self.configs],
                "counts": counts, "deliveries": deliveries, "available_events": list(EVENTS),
                "running": bool(self.thread and self.thread.is_alive()), "error": getattr(self, "error", None)}
