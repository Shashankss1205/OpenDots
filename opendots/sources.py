"""Shared lifecycle for polling adapters and persistent event listeners."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time
import threading

# Preserve existing imports for extension authors.
from .connectors.github import GitHubPollSource, normalize_github, verify_github_signature
from .connectors.jsonl import JSONLSource


class ListenerContext:
    """One listener attempt. Acknowledge upstream only after emit returns successfully."""
    def __init__(self, registry, source_id, ingest):
        self._registry, self._source_id, self._ingest = registry, source_id, ingest
        self.stop_event = registry.listener_stop
        self._closed = threading.Event()
        self.state = deepcopy(registry._state(source_id).get("cursor", {}))

    def ready(self):
        """Call after authentication/subscription succeeds, not just thread creation."""
        self._active()
        self._registry._health(self._source_id, status="listening", last_success=time.time(), last_error=None)

    def _active(self):
        if self.stop_event.is_set() or self._closed.is_set():
            raise RuntimeError("Event listener attempt is stopped")

    def emit(self, event):
        self._active()
        receipt = self._ingest(event)
        self._registry._health(self._source_id, last_received=time.time(), last_success=time.time(),
                               last_error=None, consecutive_failures=0)
        return receipt

    def checkpoint(self, state):
        """Persist a JSON cursor after durable receipt; never checkpoint rejected deliveries."""
        self._active()
        if not isinstance(state, dict):
            raise ValueError("Listener checkpoint must be a JSON object")
        encoded = json.dumps(state, allow_nan=False)
        if len(encoded.encode()) > 256_000:
            raise ValueError("Listener checkpoint exceeds size limit")
        self.state = json.loads(encoded)
        self._registry._health(self._source_id, cursor=self.state)


class SourceRegistry:
    def __init__(self, configs, store, *, builtins=True):
        self.store = store
        self.factories = {}
        self.modes = {}
        self.validators = {}
        self.configs = configs
        self.instances = {}
        self.pool = None
        self.pending = {}
        self.workers = 4
        self.listeners = {}
        self.listener_stop = threading.Event()
        self.closed = False
        if builtins:
            from .builtin_plugins import register_sources
            register_sources(self)

    def register(self, kind, factory, *, validate_config=None):
        self._register(kind, factory, "poll", validate_config)

    def register_listener(self, kind, factory, *, validate_config=None):
        self._register(kind, factory, "listener", validate_config)

    def _register(self, kind, factory, mode, validator):
        if not isinstance(kind, str) or not kind or not callable(factory):
            raise ValueError("A source adapter needs a name and callable factory")
        if validator is not None and not callable(validator):
            raise ValueError("Source configuration validator must be callable")
        if kind in self.factories:
            raise ValueError("Source kind already registered")
        self.factories[kind] = factory
        self.modes[kind] = mode
        self.validators[kind] = validator

    def validate_configs(self):
        ids = set()
        for config in self.configs:
            source_id = config.get("id")
            if not isinstance(source_id, str) or not source_id or source_id in ids:
                raise ValueError("Source IDs must be nonempty and unique")
            ids.add(source_id)
            kind = config.get("kind")
            if not isinstance(kind, str) or kind not in self.factories:
                raise ValueError(f"Unknown source adapter for {source_id}: {kind}; enable its plugin first")
            if type(config.get("interval_seconds", 5)) is not int or config.get("interval_seconds", 5) < 1:
                raise ValueError("Source interval_seconds must be positive")
            if self.validators[kind]:
                self.validators[kind](deepcopy(config))

    def _state(self, source_id):
        with self.store.connect() as db:
            row = db.execute("SELECT state FROM source_state WHERE id=?", (source_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _health(self, source_id, **changes):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM source_state WHERE id=?", (source_id,)).fetchone()
            state = json.loads(row[0]) if row else {}
            state.update(changes)
            db.execute("INSERT INTO source_state VALUES(?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                       (source_id, json.dumps(state)))

    def start_listeners(self, ingest, *, stop_event=None):
        if self.closed:
            raise RuntimeError("Source registry is closed; create a new Engine to restart")
        if stop_event is not None:
            if self.listeners and stop_event is not self.listener_stop:
                raise ValueError("Cannot replace the stop signal of running listeners")
            self.listener_stop = stop_event
        self.validate_configs()
        for config in self.configs:
            if self.modes[config["kind"]] != "listener" or config["id"] in self.listeners:
                continue
            thread = threading.Thread(target=self._listen, args=(config, ingest),
                                      name="opendots-listener-" + config["id"], daemon=True)
            self.listeners[config["id"]] = thread
            try:
                thread.start()
            except Exception:
                del self.listeners[config["id"]]
                raise

    def _listen(self, config, ingest):
        source_id = config["id"]
        while not self.listener_stop.is_set():
            context = None
            try:
                self._health(source_id, status="starting", next_poll=None)
                context = ListenerContext(self, source_id, ingest)
                listener = self.factories[config["kind"]](deepcopy(config))
                listener.run(context)
                if not self.listener_stop.is_set():
                    raise RuntimeError("Listener exited before shutdown")
            except Exception as exc:
                if context is not None:
                    context._closed.set()
                if self.listener_stop.is_set():
                    break
                failures = self._state(source_id).get("consecutive_failures", 0) + 1
                delay = min(30, 2 ** min(failures, 5))
                error = str(exc)[:1000]
                self._health(source_id, status="retrying", last_error=error,
                             last_failure=time.time(), consecutive_failures=failures,
                             next_poll=time.time() + delay)
                with self.store.connect() as db:
                    self.store.log(db, "source_failed", {"source": source_id, "error": error})
                self.listener_stop.wait(delay)
            finally:
                if context is not None:
                    context._closed.set()
        self._health(source_id, status="stopped", next_poll=None)

    def poll_due(self, ingest, now=None, asynchronous=False):
        if self.closed:
            raise RuntimeError("Source registry is closed")
        now = time.time() if now is None else now
        if asynchronous and self.pool is None:
            self.pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="opendots-source")
        for config in self.configs:
            if self.modes.get(config["kind"], "poll") != "poll":
                continue
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
        if self.closed:
            return
        self.closed = True
        self.listener_stop.set()
        deadline = time.monotonic() + 5
        for source_id, thread in self.listeners.items():
            thread.join(max(0, deadline - time.monotonic()))
            if thread.is_alive():
                self._health(source_id, status="stop_timeout", last_error="Plugin listener did not stop within five seconds")
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.pool = None

    def _poll_one(self, config, ingest, now):
        source_id = config["id"]
        with self.store.connect() as db:
            row = db.execute("SELECT state FROM source_state WHERE id=?", (source_id,)).fetchone()
        state = json.loads(row[0]) if row else {}
        if (state.get("next_poll") or 0) > now:
            return
        started=time.monotonic()
        try:
            if source_id not in self.instances:
                self.instances[source_id] = self.factories[config["kind"]](config)
            events, next_state = self.instances[source_id].poll(state)
            for event in events:
                if getattr(self.instances[source_id], "skip_existing_ids", False):
                    with self.store.connect() as db:
                        if db.execute("SELECT 1 FROM events WHERE id=?",(event["id"],)).fetchone():
                            continue
                try:
                    ingest(event)
                except (ValueError, TypeError) as exc:
                    if not getattr(self.instances[source_id], "reject_invalid_records", False):
                        raise
                    with self.store.connect() as db:
                        self.store.log(db, "source_record_rejected", {"source":source_id,"event":event,"error":str(exc)[:1000]})
            next_state.update(last_success=now,last_error=None,consecutive_failures=0,
                              last_event_count=len(events),duration_seconds=time.monotonic()-started)
            next_state["next_poll"] = now + max(1, int(next_state.get("poll_interval", config.get("interval_seconds", 5))))
            with self.store.connect() as db:
                db.execute("INSERT INTO source_state VALUES(?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                           (source_id, json.dumps(next_state)))
                for record in next_state.pop("rejected", []):
                    self.store.log(db, "source_record_rejected", {"source":source_id, **record})
                if next_state.get("gap_detected_at") != state.get("gap_detected_at"):
                    self.store.log(db,"source_gap_detected",{"source":source_id,"message":next_state.get("gap_message")})
                self.store.log(db, "source_polled", {"source": source_id, "events": len(events)})
        except Exception as exc:
            state.update(last_error=str(exc)[:1000],last_failure=now,consecutive_failures=state.get("consecutive_failures",0)+1)
            state["next_poll"] = now + max(30, int(config.get("interval_seconds", 60)))
            with self.store.connect() as db:
                db.execute("INSERT INTO source_state VALUES(?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state", (source_id, json.dumps(state)))
                self.store.log(db, "source_failed", {"source": source_id, "error": str(exc)[:1000]})
