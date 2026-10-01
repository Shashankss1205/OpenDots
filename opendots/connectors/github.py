"""GitHub activity normalization, signed webhooks and bounded polling."""
import hashlib
import hmac
import json
import os
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
    event = {"id": "github:" + str(delivery_id), "type": event_type, "source": "github",
            "payload": {"repo": repository, "title": entity.get("title", event_type), "body": content.get("body", ""),
                        "action": action, "actor": payload.get("sender", {}).get("login"),
                        "comment_id": content.get("id") if comment else None,
                        "issue_body": entity.get("body", "") if comment else None,
                        "ref": payload.get("ref"), "before": payload.get("before"),
                        "after": payload.get("after"), "commits": payload.get("commits", []),
                        "number": entity.get("number"), "url": content.get("html_url") or entity.get("html_url"), "severity": severity,
                        "labels": label_names}}
    stamp=content.get("updated_at") or content.get("created_at")
    identity=content.get("id")
    if kind == "push":
        identity=payload.get("ref");stamp=payload.get("after")
    if repository and identity is not None and stamp:
        semantic=[repository,event_type,str(identity),stamp]
        event["dedup_key"]="github:"+hashlib.sha256(json.dumps(semantic,sort_keys=True).encode()).hexdigest()
    return event



class GitHubPollSource:
    skip_existing_ids = True

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
        previous=set(state.get("seen_ids", []))
        page=1
        max_pages=max(1,int(self.config.get("max_pages",5)))
        while previous and items and not previous.intersection(str(item["id"]) for item in items) and len(items)%100==0 and page<max_pages:
            page+=1
            paged_headers={key:value for key,value in headers.items() if key != "If-None-Match"}
            paged=Request(f"https://api.github.com/repos/{self.repo}/events?per_page=100&page={page}",headers=paged_headers)
            with urlopen(paged,timeout=10) as response:
                body=response.read(2_000_001)
                if len(body)>2_000_000:raise ValueError("GitHub page too large")
                batch=json.loads(body)
            if not isinstance(batch,list):raise ValueError("Invalid GitHub page")
            items.extend(batch)
            if len(batch)<100:break
        ids=[str(item["id"]) for item in items]
        if previous and items and not previous.intersection(ids):
            state["gap_detected_at"]=time.time()
            state["gap_message"]="Previous cursor not found in available event history; reconcile repository state"
        state["seen_ids"]=ids[:max_pages*100]
        events = [normalize_github(item["type"], item["payload"], item["id"], self.repo) for item in reversed(items) if str(item["id"]) not in previous]
        if not state.get("initialized") and self.config.get("bootstrap", "observe") == "observe":
            # Avoid creating work for old history on first connection.
            events = []
        state["initialized"] = True
        return events, state
