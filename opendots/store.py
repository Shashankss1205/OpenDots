from contextlib import contextmanager
from dataclasses import asdict
import json
import hashlib
from pathlib import Path
import sqlite3
import time


SCHEMA = """
CREATE TABLE IF NOT EXISTS targets(id TEXT PRIMARY KEY, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS work(
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL REFERENCES events(id),
 target_id TEXT NOT NULL REFERENCES targets(id), priority INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', plan TEXT, next_action INTEGER NOT NULL DEFAULT 0,
 approval_index INTEGER, approved_index INTEGER, summary TEXT, error TEXT,
 workspace TEXT, branch TEXT, base_ref TEXT,
 planning_round INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(event_id,target_id));
CREATE INDEX IF NOT EXISTS work_queue ON work(status,priority DESC,id);
CREATE UNIQUE INDEX IF NOT EXISTS target_active ON work(target_id)
 WHERE status IN ('running','waiting_approval','ready');
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, kind TEXT NOT NULL,
 target_id TEXT, work_id INTEGER, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedule_ticks(id TEXT PRIMARY KEY, last_tick INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_state(id TEXT PRIMARY KEY, state TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(work)")}
            for column in ("workspace", "branch", "base_ref", "approval_context"):
                if column not in columns:
                    db.execute(f"ALTER TABLE work ADD COLUMN {column} TEXT")
            if "planning_round" not in columns:
                db.execute("ALTER TABLE work ADD COLUMN planning_round INTEGER NOT NULL DEFAULT 0")

            if "repair_attempts" not in columns:
                db.execute("ALTER TABLE work ADD COLUMN repair_attempts INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def register_targets(self, targets):
        with self.connect() as db:
            for target in targets:
                db.execute("INSERT OR IGNORE INTO targets VALUES(?,?)", (target.id, json.dumps({"completed": 0, "notes": [],
                    "desired_state": target.desired_state, "actual_state": {"open_issues": [], "stars_observed": 0}, "artifacts": []})))
                state = json.loads(db.execute("SELECT state FROM targets WHERE id=?", (target.id,)).fetchone()[0])
                revision = hashlib.sha256(json.dumps(asdict(target), sort_keys=True, default=str).encode()).hexdigest()
                if state.get("config_revision") != revision:
                    previous = state.get("config_revision")
                    state.update(desired_state=target.desired_state, config_revision=revision)
                    db.execute("UPDATE targets SET state=? WHERE id=?", (json.dumps(state), target.id))
                    self.log(db, "target_configuration_changed", {"previous": previous, "revision": revision}, target.id)


    @staticmethod
    def log(db, kind, detail, target_id=None, work_id=None):
        db.execute("INSERT INTO audit(at,kind,target_id,work_id,detail) VALUES(?,?,?,?,?)",
                   (time.time(), kind, target_id, work_id, json.dumps(detail)))

    def ingest(self, event, matches):
        body = json.dumps(event, sort_keys=True)
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT body FROM events WHERE id=?", (event["id"],)).fetchone()
            if found:
                if found["body"] != body:
                    raise ValueError("An event ID cannot be reused with a different payload")
                return {"event_id": event["id"], "duplicate": True, "queued": 0}
            db.execute("INSERT INTO events VALUES(?,?,?)", (event["id"], body, now))
            queued = 0
            self.log(db, "event_received", {"id": event["id"], "type": event["type"]})
            for target_id, priority, accepted, reason in matches:
                # Cheap signals can update state without consuming a worker/model invocation.
                state = json.loads(db.execute("SELECT state FROM targets WHERE id=?", (target_id,)).fetchone()[0])
                actual = state.setdefault("actual_state", {"open_issues": [], "stars_observed": 0})
                if event["type"] == "github.star":
                    actual["stars_observed"] = actual.get("stars_observed", 0) + 1
                if event["type"] == "github.issue.opened":
                    actual["open_issues"] = list(dict.fromkeys(actual.get("open_issues", []) + [event["id"]]))
                state["last_observed_event"] = event["id"]
                db.execute("UPDATE targets SET state=? WHERE id=?", (json.dumps(state), target_id))
                self.log(db, "attention", {"priority": priority, "accepted": accepted, "reason": reason}, target_id)
                if accepted:
                    db.execute("INSERT INTO work(event_id,target_id,priority,created,updated) VALUES(?,?,?,?,?)",
                               (event["id"], target_id, priority, now, now))
                    queued += 1
            if not matches:
                self.log(db, "event_ignored", {"reason": "No matching subscription", "event_id": event["id"]})
            return {"event_id": event["id"], "duplicate": False, "queued": queued}

    def claim(self, target_ids):
        if not target_ids:
            return None
        placeholders = ",".join("?" for _ in target_ids)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"""SELECT w.* FROM work w WHERE w.target_id IN ({placeholders}) AND
             (w.status='ready' OR (w.status='queued' AND NOT EXISTS
              (SELECT 1 FROM work active WHERE active.target_id=w.target_id
               AND active.status IN ('running','waiting_approval','ready'))))
             ORDER BY CASE WHEN w.status='ready' THEN 0 ELSE 1 END, w.priority DESC,w.id LIMIT 1""",
                             tuple(target_ids)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE work SET status='running',updated=? WHERE id=?", (time.time(), row["id"]))
            self.log(db, "work_started", {"resumed": row["status"] == "ready"}, row["target_id"], row["id"])
            return dict(db.execute("SELECT * FROM work WHERE id=?", (row["id"],)).fetchone())

    def event(self, event_id):
        with self.connect() as db:
            return json.loads(db.execute("SELECT body FROM events WHERE id=?", (event_id,)).fetchone()[0])

    def state(self, target_id):
        with self.connect() as db:
            return json.loads(db.execute("SELECT state FROM targets WHERE id=?", (target_id,)).fetchone()[0])

    def save_plan(self, work_id, plan):
        with self.connect() as db:
            db.execute("UPDATE work SET plan=?,summary=?,updated=?,next_action=0,approved_index=NULL,planning_round=planning_round+1 WHERE id=?",
                       (json.dumps(plan), plan["summary"], time.time(), work_id))
            self.log(db, "plan_created", plan, work_id=work_id)

    def work_results(self, work_id):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT detail FROM audit WHERE work_id=? AND kind IN ('action_completed','action_failed') ORDER BY id", (work_id,))]

    def set_workspace(self, work_id, workspace, branch, base_ref):
        with self.connect() as db:
            db.execute("UPDATE work SET workspace=?,branch=?,base_ref=? WHERE id=?", (str(workspace), branch, base_ref, work_id))
            self.log(db, "workspace_created", {"workspace": str(workspace), "branch": branch, "base_ref": base_ref}, work_id=work_id)

    def action_done(self, work, index, result, tool=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE work SET next_action=?,approved_index=NULL,updated=? WHERE id=?",
                       (index + 1, time.time(), work["id"]))
            self.log(db, "action_completed", {"index": index, "tool": tool, "result": result}, work["target_id"], work["id"])
            # Notes remain task-local audit evidence until successful completion.
            # Failed/rejected/interrupted tasks cannot publish success claims to
            # memory that later tasks treat as validated target history.

    def action_failed(self, work, index, result, tool):
        with self.connect() as db:
            db.execute("UPDATE work SET repair_attempts=repair_attempts+1 WHERE id=?", (work["id"],))
            self.log(db, "action_failed", {"index": index, "tool": tool, "result": result}, work["target_id"], work["id"])
        work["repair_attempts"] = work.get("repair_attempts", 0) + 1

    def await_approval(self, work, index, action, preview, context=None):
        with self.connect() as db:
            db.execute("UPDATE work SET status='waiting_approval',approval_index=?,approved_index=NULL,approval_context=?,updated=? WHERE id=?",
                       (index, context, time.time(), work["id"]))
            self.log(db, "approval_requested", {"index": index, "action": action, "preview": preview},
                     work["target_id"], work["id"])

    @staticmethod
    def approval_token(work):
        plan = json.loads(work["plan"]) if isinstance(work["plan"], str) else work["plan"]
        data = {"id": work["id"], "round": work["planning_round"], "index": work["approval_index"],
                "action": plan["actions"][work["approval_index"]],
                "context": dict(work).get("approval_context")}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def decide(self, work_id, approved, expected_token=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM work WHERE id=?", (work_id,)).fetchone()
            if row is None or row["status"] != "waiting_approval":
                raise ValueError("This work item is not awaiting approval")
            if expected_token is not None and expected_token != self.approval_token(row):
                raise ValueError("Approval proposal changed; refresh and review the current action")
            status = "ready" if approved else "rejected"
            db.execute("UPDATE work SET status=?,approved_index=?,approval_index=NULL,updated=? WHERE id=?",
                       (status, row["approval_index"] if approved else None, time.time(), work_id))
            self.log(db, "approval_granted" if approved else "approval_rejected", {"index": row["approval_index"]},
                     row["target_id"], work_id)
            return {"work_id": work_id, "status": status}

    def finish(self, work, status="completed", error=None, artifact=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE work SET status=?,error=?,summary=?,updated=? WHERE id=?", (status, error, work.get("summary"), time.time(), work["id"]))
            if status == "completed":
                state = json.loads(db.execute("SELECT state FROM targets WHERE id=?", (work["target_id"],)).fetchone()[0])
                state["completed"] = state.get("completed", 0) + 1
                state["last_summary"] = work.get("summary")
                notes = []
                for row in db.execute("SELECT detail FROM audit WHERE work_id=? AND kind='action_completed' ORDER BY id", (work["id"],)):
                    result = json.loads(row[0])["result"]
                    if isinstance(result, dict) and "note" in result:
                        notes.append(result["note"])
                state["notes"] = (state.get("notes", []) + notes + ["Execution evidence: " + (work.get("summary") or "Local work item completed")])[-50:]
                state["last_event_id"] = work["event_id"]
                if artifact:
                    state["artifacts"] = (state.get("artifacts", []) + [artifact])[-50:]
                    state["latest_branch"] = artifact["branch"]
                    state["actual_state"]["latest_proposal_commit"] = artifact["commit"]
                db.execute("UPDATE targets SET state=? WHERE id=?", (json.dumps(state), work["target_id"]))
            self.log(db, "work_" + status, {"error": error, "summary": work.get("summary"), "artifact": artifact}, work["target_id"], work["id"])

    def recover_interrupted(self):
        # Called only while holding the process lock. Never replay ambiguous side effects.
        with self.connect() as db:
            rows = db.execute("SELECT * FROM work WHERE status='running'").fetchall()
            for row in rows:
                db.execute("UPDATE work SET status='interrupted',error=?,updated=? WHERE id=?",
                           ("Process stopped during execution; inspect effects before submitting a new event", time.time(), row["id"]))
                self.log(db, "work_interrupted", {"reason": "Recovered after process interruption"}, row["target_id"], row["id"])
            return len(rows)

    def snapshot(self, limit=200):
        with self.connect() as db:
            targets = [{"id": row["id"], "state": json.loads(row["state"])} for row in db.execute("SELECT * FROM targets")]
            work = [dict(row) for row in db.execute("""SELECT * FROM work
                WHERE status IN ('running','waiting_approval','ready')
                OR id IN (SELECT id FROM work ORDER BY id DESC LIMIT ?)
                ORDER BY id DESC""", (limit,))]
            for item in work:
                item["plan"] = json.loads(item["plan"]) if item["plan"] else None
                if item["status"] == "waiting_approval":
                    item["approval_token"] = self.approval_token(item)
            audit = [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]
            for item in audit:
                item["detail"] = json.loads(item["detail"])
            # Approval previews must survive unrelated audit traffic as well.
            known = {item["id"] for item in audit}
            for item in work:
                if item["status"] == "waiting_approval":
                    row = db.execute("SELECT * FROM audit WHERE work_id=? AND kind='approval_requested' ORDER BY id DESC LIMIT 1", (item["id"],)).fetchone()
                    if row and row["id"] not in known:
                        entry = dict(row); entry["detail"] = json.loads(entry["detail"])
                        audit.append(entry)
            audit.sort(key=lambda item: item["id"], reverse=True)
            counts = {row["status"]: row["count"] for row in db.execute("SELECT status,count(*) count FROM work GROUP BY status")}
            events = db.execute("SELECT count(*) FROM events").fetchone()[0]
            return {"targets": targets, "work": work, "audit": audit, "counts": counts, "event_count": events}
