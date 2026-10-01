"""Content-bound evidence for owner-configured validation checks."""
import hashlib
import json
import os
from pathlib import Path


def workspace_fingerprint(workspace):
    root = Path(workspace).resolve()
    # Include Git-ignored validation inputs too. A dependency archive or harness
    # may be ignored by Git while still determining the check's actual result.
    paths = []
    excluded = {".git", ".aws", ".ssh", ".codex", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    for directory, subdirs, files in os.walk(root, followlinks=False):
        subdirs[:] = [name for name in subdirs if name not in excluded and not name.startswith(".env")]
        for name in files:
            if name not in excluded and not name.startswith(".env") and not name.endswith(".pyc"):
                paths.append((Path(directory) / name).relative_to(root).as_posix())
        paths.extend((Path(directory) / name).relative_to(root).as_posix() for name in subdirs
                     if (Path(directory) / name).is_symlink())
    entries = []
    for relative in sorted(paths):
        path = root / relative
        if path.is_symlink():
            entry = [relative, "link", os.readlink(path)]
            if path.resolve().is_relative_to(root) and path.is_file():
                entry.append(hash_file(path))
        elif path.is_file():
            entry = [relative, path.stat().st_mode & 0o777, hash_file(path)]
        else:
            entry = [relative, "missing"]
        entries.append(entry)
    return hashlib.sha256(json.dumps(entries, ensure_ascii=True).encode()).hexdigest()


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_signature(target, name, sandbox):
    return hashlib.sha256(json.dumps({"command": target.checks[name], "sandbox": sandbox}, sort_keys=True).encode()).hexdigest()
