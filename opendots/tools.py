import difflib
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def confined_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Tool paths must be relative to the target workspace")
    parts = Path(relative).parts
    # Resolve reads safely, but never mutate through an alias of an allowed path.
    if any(p in {"..", ".git", ".aws", ".ssh", ".codex"} or p.startswith(".env") for p in parts):
        raise ValueError("Traversal and credential paths are not allowed")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate == root.resolve():
        raise ValueError("Path escapes the target workspace")
    resolved_parts = candidate.relative_to(root.resolve()).parts
    if any(p in {".git", ".aws", ".ssh", ".codex"} or p.startswith(".env") for p in resolved_parts):
        raise ValueError("Resolved credential paths are not allowed")
    return candidate


def bounded_process(command, cwd, timeout, stdin=None, env=None):
    # Trusted argv, never shell interpolation. Kill descendants on timeout on POSIX.
    if sys.platform == "linux":
        command = [sys.executable, "-I", "-B", str(Path(__file__).with_name("process_guard.py")), str(os.getpid()), *command]
    with tempfile.TemporaryFile(mode="w+b") as output:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name == "posix", env=env)
        try:
            process.communicate(stdin.encode() if stdin is not None else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            if sys.platform == "linux":
                # Let the Linux supervisor stop its command's separate group.
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            elif os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
            raise RuntimeError(f"Tool timed out after {timeout}s") from None
        output.seek(0)
        text = output.read(65536).decode("utf-8", errors="replace")
        return process.returncode, text


class ToolRegistry:
    """Add new handlers without changing the scheduler or target count."""
    def __init__(self, sandbox="bubblewrap"):
        self.sandbox = sandbox
        self.handlers = {"read_file": self.read_file, "write_file": self.write_file, "replace_text": self.replace_text,
                         "run_check": self.run_check, "note": self.note}
        self.arg_names = {"read_file": ["path"], "write_file": ["path", "content", "expected_sha256"],
                          "replace_text": ["path", "old_text", "new_text", "expected_sha256"],
                          "run_check": ["name"], "note": ["text"]}

    def register(self, name, handler, arg_names):
        if name in self.handlers:
            raise ValueError("Tool already registered")
        if not isinstance(name, str) or not name or not callable(handler):
            raise ValueError("A tool needs a nonempty name and a callable handler")
        if not isinstance(arg_names, list) or any(not isinstance(s, str) for s in arg_names):
            raise ValueError("Tool arg_names must be an array of strings")
        self.handlers[name] = handler
        self.arg_names[name] = arg_names

    def validate(self, target, action):
        if not isinstance(action, dict) or set(action) != {"tool", "args"}:
            raise ValueError("Actions need exactly tool and args")
        if action["tool"] not in self.handlers or not isinstance(action["args"], dict):
            raise ValueError("Unknown tool or invalid tool arguments")
        args = action["args"]
        tool = action["tool"]
        if set(args) != set(self.arg_names[tool]):
            raise ValueError(f"Invalid arguments for {tool}")
        if any(not isinstance(v, str) for v in args.values()):
            raise ValueError("Tool arguments must be strings")
        if tool in {"read_file", "write_file", "replace_text"}:
            confined_path(target.workspace, args["path"])
        if tool in {"write_file", "replace_text"}:
            current = target.workspace
            for part in Path(args["path"]).parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("Writes through symlinks are not allowed")
        if tool in {"write_file", "replace_text"} and not any(fnmatch.fnmatchcase(args["path"], pattern) for pattern in target.write_paths):
            raise ValueError("Write path is outside the owner-configured scope")
        if tool == "write_file" and len(args["content"].encode()) > 256_000:
            raise ValueError("Proposed file exceeds the V0 size limit")
        if tool == "replace_text" and (not args["old_text"] or len((args["old_text"] + args["new_text"]).encode()) > 256_000):
            raise ValueError("Replacement needs bounded, nonempty old text")
        if tool == "run_check" and args["name"] not in target.checks:
            raise ValueError("Check is not in the owner-configured allowlist")

    def preview(self, target, action):
        self.validate(target, action)
        args = action["args"]
        if action["tool"] in {"write_file", "replace_text"}:
            path = confined_path(target.workspace, args["path"])
            old = path.read_text() if path.exists() else None
            expected = digest(old) if old is not None else "absent"
            if args["expected_sha256"] != expected:
                raise ValueError("File changed since planning; submit a new event to replan")
            content = args["content"] if action["tool"] == "write_file" else self.replacement_content(old, args)
            diff = "".join(difflib.unified_diff((old or "").splitlines(keepends=True), content.splitlines(keepends=True),
                                               fromfile=args["path"], tofile=args["path"]))
            return {"path": args["path"], "diff": diff, "expected_sha256": expected}
        if action["tool"] == "run_check":
            return {"command": target.checks[args["name"]], "workspace": str(target.workspace)}
        return args

    def execute(self, target, action):
        self.validate(target, action)
        return self.handlers[action["tool"]](target, action["args"])

    @staticmethod
    def read_file(target, args):
        path = confined_path(target.workspace, args["path"])
        if path.stat().st_size > 256_000:
            raise ValueError("File exceeds the V0 read limit")
        text = path.read_text()
        return {"path": args["path"], "content": text, "sha256": digest(text)}

    @staticmethod
    def replacement_content(old, args):
        if old is None or old.count(args["old_text"]) != 1:
            raise ValueError("Replacement text must match exactly once")
        content = old.replace(args["old_text"], args["new_text"], 1)
        if len(content.encode()) > 256_000:
            raise ValueError("Proposed file exceeds the V0 size limit")
        return content

    def replace_text(self, target, args):
        path = confined_path(target.workspace, args["path"])
        old = path.read_text() if path.exists() else None
        if args["expected_sha256"] != (digest(old) if old is not None else "absent"):
            raise ValueError("File changed since planning; write cancelled")
        content = self.replacement_content(old, args)
        return self.write_file(target, {"path": args["path"], "content": content, "expected_sha256": args["expected_sha256"]})

    @staticmethod
    def write_file(target, args):
        path = confined_path(target.workspace, args["path"])
        old = path.read_text() if path.exists() else None
        if args["expected_sha256"] != (digest(old) if old is not None else "absent"):
            raise ValueError("File changed since planning; write cancelled")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".opendots-write-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(args["content"])
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"path": args["path"], "sha256": digest(args["content"]), "bytes": len(args["content"].encode())}

    def run_check(self, target, args):
        from .evidence import check_signature, workspace_fingerprint
        before = workspace_fingerprint(target.workspace)
        command = target.checks[args["name"]]
        if not isinstance(command, list) or not command or any(not isinstance(s, str) for s in command):
            raise ValueError("Configured checks must be nonempty argv lists")
        command = [os.sys.executable if part == "{python}" else part for part in command]
        if self.sandbox == "bubblewrap":
            from .sandbox import sandbox_command
            command = sandbox_command(command, target.workspace)
        code, output = bounded_process(command, target.workspace, 30)
        result = {"name": args["name"], "exit_code": code, "output": output}
        if code:
            raise RuntimeError("Configured check failed: " + json.dumps(result))
        after = workspace_fingerprint(target.workspace)
        if before != after:
            raise RuntimeError("Configured check changed workspace inputs; successful exit lacks stable validation evidence")
        result.update(workspace_fingerprint=after, check_signature=check_signature(target, args["name"], self.sandbox))
        return result

    @staticmethod
    def note(target, args):
        if len(args["text"]) > 8000:
            raise ValueError("Note exceeds size limit")
        return {"note": args["text"]}
