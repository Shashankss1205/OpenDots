from dataclasses import replace
import hashlib
import os
from pathlib import Path
import shutil

from .tools import bounded_process


def git(cwd, *args):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8"}
    command = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
               "-c", "user.name=OpenDots", "-c", "user.email=opendots@localhost", *args]
    code, output = bounded_process(command, cwd, 30, env=env)
    if code:
        raise RuntimeError(f"Git operation failed ({args[0]}): {output[:2000]}")
    return output.strip()


class Workspaces:
    def __init__(self, root, store):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store

    def prepare(self, target, work):
        if work.get("workspace"):
            path = Path(work["workspace"])
            if not path.is_dir():
                raise RuntimeError("Persisted task workspace is missing; inspect before resubmitting work")
            return replace(target, workspace=path)
        key = hashlib.sha256(target.id.encode()).hexdigest()[:16]
        repository = self.root / "repositories" / key
        if not (repository / ".git").is_dir():
            if repository.exists():
                shutil.rmtree(repository)
            repository.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target.workspace, repository, symlinks=True,
                            ignore=shutil.ignore_patterns(".git", ".env*", ".aws", ".ssh", ".codex", "__pycache__", "node_modules"))
            git(repository, "init", "--initial-branch=main")
            git(repository, "add", "-A")
            git(repository, "commit", "--allow-empty", "-m", "Snapshot owner-configured target workspace")
        state = self.store.state(target.id)
        base = state.get("latest_branch", "main")
        branch = f"opendots/{key}/task-{work['id']}"
        directory = self.root / "tasks" / str(work["id"])
        directory.parent.mkdir(parents=True, exist_ok=True)
        git(repository, "worktree", "add", "-b", branch, str(directory), base)
        self.store.set_workspace(work["id"], directory, branch, base)
        work.update(workspace=str(directory), branch=branch, base_ref=base)
        return replace(target, workspace=directory)

    def finalize(self, work):
        directory = Path(work["workspace"])
        patch = git(directory, "diff", "--no-ext-diff", "--no-textconv", "HEAD")
        # Include newly-created files in a reproducible branch; never push or touch source workspace.
        git(directory, "add", "-A")
        staged = git(directory, "diff", "--cached", "--no-ext-diff", "--no-textconv")
        if staged:
            git(directory, "commit", "-m", f"OpenDots task {work['id']}: proposed local changes")
        artifact = self.root / "artifacts" / f"task-{work['id']}.patch"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(staged or patch)
        return {"branch": work["branch"], "workspace": str(directory), "patch": str(artifact),
                "changed": bool(staged or patch), "commit": git(directory, "rev-parse", "HEAD")}
