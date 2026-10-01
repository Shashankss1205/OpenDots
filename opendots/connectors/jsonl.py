"""Append-only file inbox with restart-safe byte cursors."""
import hashlib
import json
from pathlib import Path


class JSONLSource:
    reject_invalid_records = True

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
