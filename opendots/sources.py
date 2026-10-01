"""Real, replaceable inputs into one durable stream. No outbound writes."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def verify_github_signature(body, signature, secret):
    if not secret or not isinstance(signature, str):
        raise ValueError("GitHub webhook secret/signature missing")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("GitHub webhook signature mismatch")


def normalize_github(kind, payload, delivery_id, repo=None):
    if not isinstance(payload, dict):
        raise ValueError("GitHub payload must be an object")
    mapping = {"IssuesEvent": "issues", "PullRequestEvent": "pull_request", "IssueCommentEvent": "issue_comment",
               "WatchEvent": "star", "PushEvent": "push", "ReleaseEvent": "release"}
    kind = mapping.get(kind, kind)
    action = payload.get("action", "created")
    if kind == "star":
        event_type = "github.star"
    else:
        names = {"issues": "issue", "pull_request": "pr", "issue_comment": "comment", "discussion": "discussion"}
        event_type = f"github.{names.get(kind, kind)}.{action}"
    repository = repo or payload.get("repository", {}).get("full_name")
    entity = payload.get("issue") or payload.get("pull_request") or payload.get("discussion") or payload
    comment = payload.get("comment") or payload.get("review")
    content = comment if isinstance(comment, dict) else entity
    labels = entity.get("labels", [])
    label_names = [label.get("name", "") if isinstance(label, dict) else str(label) for label in labels]
    severity = "critical" if any("critical" in name.lower() for name in label_names) else "normal"
    return {"id": "github:" + str(delivery_id), "type": event_type, "source": "github",
            "payload": {"repo": repository, "title": entity.get("title", event_type), "body": content.get("body", ""),
                        "action": action, "actor": payload.get("sender", {}).get("login"),
                        "comment_id": content.get("id") if comment else None,
                        "issue_body": entity.get("body", "") if comment else None,
                        "ref": payload.get("ref"), "before": payload.get("before"),
                        "after": payload.get("after"), "commits": payload.get("commits", []),
                        "number": entity.get("number"), "url": content.get("html_url") or entity.get("html_url"), "severity": severity,
                        "labels": label_names}}


class GitHubPollSource:
    def __init__(self, config):
        self.config = config
        self.repo = config["repo"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo):
            raise ValueError("GitHub source repo must be owner/name")

    def poll(self, state):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "OpenDots-local-runtime", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get(self.config.get("token_env", "GITHUB_TOKEN"))
        if token:
            headers["Authorization"] = "Bearer " + token
        if state.get("etag"):
            headers["If-None-Match"] = state["etag"]
        request = Request(f"https://api.github.com/repos/{self.repo}/events?per_page=100", headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise ValueError("GitHub response too large")
                items = json.loads(body)
                state = {**state, "etag": response.headers.get("ETag"),
                         "poll_interval": max(int(self.config.get("interval_seconds", 60)), int(response.headers.get("X-Poll-Interval", 60)))}
        except HTTPError as exc:
            if exc.code == 304:
                return [], state
            raise RuntimeError(f"GitHub polling HTTP {exc.code}; check rate limits and configured credentials") from None
        if not isinstance(items, list):
            raise ValueError("GitHub returned an invalid event list")
        events = [normalize_github(item["type"], item["payload"], item["id"], self.repo) for item in reversed(items)]
        if not state.get("initialized") and self.config.get("bootstrap", "observe") == "observe":
            # Avoid creating work for old history on first connection.
            events = []
        state["initialized"] = True
        return events, state


class JSONLSource:
    def __init__(self, config):
        self.config = config
        self.path = Path(config["path"])

    def poll(self, state):
        if not self.path.exists():
            return [], state
        offset = int(state.get("offset", 0))
        stat = self.path.stat()
        identity = [stat.st_dev, stat.st_ino]
        size = stat.st_size
        if size < offset or (state.get("identity") and state["identity"] != identity):
            offset = 0
        events = []
        rejected = []
        batch_size = max(1, int(self.config.get("batch_size", 1000)))
        with self.path.open("rb") as handle:
            handle.seek(offset)
            for _ in range(batch_size):
                start = handle.tell()
                line = handle.readline(256_001)
                if len(line) > 256_000:
                    # Consume this complete oversized record without retaining it in memory.
                    while line and not line.endswith(b"\n"):
                        line = handle.readline(256_001)
                    if not line:
                        offset = start
                        break
                    rejected.append({"offset":start,"error":"Record exceeds 256000 bytes"})
                    offset = handle.tell()
                    continue
                if not line or not line.endswith(b"\n"):
                    # An incomplete append is retried, not acknowledged.
                    offset = start
                    break
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not event["type"]:
                        raise ValueError("Record needs a nonempty type")
                except (ValueError, UnicodeDecodeError) as exc:
                    rejected.append({"offset":start,"sha256":hashlib.sha256(line).hexdigest(),"error":str(exc)[:300]})
                    offset = handle.tell()
                    continue
                event.setdefault("id", "jsonl:" + self.config["id"] + ":" + str(start) + ":" + hashlib.sha256(line).hexdigest())
                event.setdefault("source", "file")
                events.append(event)
                offset = handle.tell()
        return events, {**state, "offset": offset, "identity": identity, "rejected": rejected}


class SourceRegistry:
    def __init__(self, configs, store):
        self.store = store
        self.factories = {"github_poll": GitHubPollSource, "jsonl": JSONLSource}
        self.configs = configs
        self.instances = {}
        self.pool = None
        self.pending = {}
        self.workers = 4

    def register(self, kind, factory):
        if kind in self.factories:
            raise ValueError("Source kind already registered")
        self.factories[kind] = factory

    def poll_due(self, ingest, now=None, asynchronous=False):
        now = time.time() if now is None else now
        if asynchronous and self.pool is None:
            self.pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="opendots-source")
        for config in self.configs:
            key = config["id"]
            if asynchronous:
                pending = self.pending.get(key)
                if pending and not pending.done():
                    continue
                if pending:
                    pending.result()
                self.pending[key] = self.pool.submit(self._poll_one, config, ingest, now)
            else:
                self._poll_one(config, ingest, now)

    def close(self):
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.pool = None

    def _poll_one(self, config, ingest, now):
        source_id = config["id"]
        with self.store.connect() as db:
            row = db.execute("SELECT state FROM source_state WHERE id=?", (source_id,)).fetchone()
        state = json.loads(row[0]) if row else {}
        if state.get("next_poll", 0) > now:
            return
        try:
            if source_id not in self.instances:
                self.instances[source_id] = self.factories[config["kind"]](config)
            events, next_state = self.instances[source_id].poll(state)
            for event in events:
                try:
                    ingest(event)
                except (ValueError, TypeError) as exc:
                    if config["kind"] != "jsonl":
                        raise
                    with self.store.connect() as db:
                        self.store.log(db, "source_record_rejected", {"source":source_id,"event":event,"error":str(exc)[:1000]})
            next_state["next_poll"] = now + max(1, int(next_state.get("poll_interval", config.get("interval_seconds", 5))))
            with self.store.connect() as db:
                db.execute("INSERT INTO source_state VALUES(?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                           (source_id, json.dumps(next_state)))
                for record in next_state.pop("rejected", []):
                    self.store.log(db, "source_record_rejected", {"source":source_id, **record})
                self.store.log(db, "source_polled", {"source": source_id, "events": len(events)})
        except Exception as exc:
            state["next_poll"] = now + max(30, int(config.get("interval_seconds", 60)))
            with self.store.connect() as db:
                db.execute("INSERT INTO source_state VALUES(?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state", (source_id, json.dumps(state)))
                self.store.log(db, "source_failed", {"source": source_id, "error": str(exc)[:1000]})
