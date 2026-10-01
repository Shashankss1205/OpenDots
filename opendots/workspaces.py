from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .tools import bounded_process


def git(cwd, *args, output_path=None):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8"}
    command = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
               "-c", "user.name=OpenDots", "-c", "user.email=opendots@localhost", *args]
    code, output = bounded_process(command, cwd, 30, env=env, output_path=output_path)
    if code:
        raise RuntimeError(f"Git operation failed ({args[0]}): {output[:2000]}")
    return output.strip()


class Workspaces:
    def __init__(self, root, store):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store

    @staticmethod
    def snapshot(source, repository, branch="main"):
        from .evidence import workspace_fingerprint
        before = workspace_fingerprint(source)
        repository.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, repository, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", ".env*", ".aws", ".ssh", ".codex", "__pycache__", "node_modules", ".venv", "venv", ".opendots", ".spots"))
        if workspace_fingerprint(source) != before:
            raise ValueError("Source changed during snapshot; stop source edits and retry")
        git(repository, "init", f"--initial-branch={branch}")
        git(repository, "add", "-A")
        git(repository, "commit", "--allow-empty", "-m", "Snapshot owner-configured target workspace")
        return git(repository, "rev-parse", "HEAD")

    def prepare(self, target, work):
        if work.get("workspace"):
            path = Path(work["workspace"])
            if not path.is_dir():
                raise RuntimeError("Persisted task workspace is missing; inspect before resubmitting work")
            return replace(target, workspace=path)
        if self.root.resolve().is_relative_to(target.workspace.resolve()):
            raise ValueError("Managed storage must be outside the source workspace")
        key = hashlib.sha256(target.id.encode()).hexdigest()[:16]
        state = self.store.state(target.id)
        key += state.get("source_generation", "")
        repository = self.root / "repositories" / key
        if not (repository / ".git").is_dir():
            if repository.exists():
                shutil.rmtree(repository)
            self.snapshot(target.workspace, repository, state.get("source_base_ref", "main"))
        base = state.get("accepted_branch", state.get("source_base_ref", "main"))
        branch = f"opendots/{key}/task-{work['id']}"
        directory = self.root / "tasks" / str(work["id"])
        directory.parent.mkdir(parents=True, exist_ok=True)
        git(repository, "worktree", "add", "-b", branch, str(directory), base)
        self.store.set_workspace(work["id"], directory, branch, base)
        work.update(workspace=str(directory), branch=branch, base_ref=base)
        return replace(target, workspace=directory)

    def finalize(self, work):
        directory = Path(work["workspace"])
        # Preserve exact complete patch bytes, including binary changes and final newlines.
        git(directory, "add", "-A")
        artifact = self.root / "artifacts" / f"task-{work['id']}.patch"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        git(directory, "diff", "--cached", "--binary", "--full-index", "--no-ext-diff",
            "--no-textconv", output_path=artifact)
        changed = artifact.stat().st_size > 0
        if changed:
            git(directory, "commit", "-m", f"OpenDots task {work['id']}: proposed local changes")
        return {"branch": work["branch"], "workspace": str(directory), "patch": str(artifact),
                "changed": changed, "commit": git(directory, "rev-parse", "HEAD")}

    def proposal(self, work_id):
        with self.store.connect() as db:
            return self._proposal(db, work_id)

    def _proposal(self, db, work_id):
        work = db.execute("SELECT * FROM work WHERE id=?", (work_id,)).fetchone()
        if work is None or work["status"] != "completed":
            raise ValueError("Only completed work has an acceptable proposal")
        row = db.execute("SELECT detail FROM audit WHERE work_id=? AND kind='work_completed' ORDER BY id DESC LIMIT 1", (work_id,)).fetchone()
        artifact = json.loads(row[0]).get("artifact") if row else None
        if not artifact:
            raise ValueError("Retained proposal is unavailable")
        return {"work_id": work_id, "target_id": work["target_id"], "base_ref": work["base_ref"], **artifact}

    def accept(self, work_id, expected_commit):
        # Serialize with claim(): no task can start against an intermediate base.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            proposal = self._proposal(db, work_id)
            if not expected_commit or expected_commit != proposal["commit"]:
                raise ValueError("Review the proposal and supply its exact commit")
            target_id = proposal["target_id"]
            state = json.loads(db.execute("SELECT state FROM targets WHERE id=?", (target_id,)).fetchone()[0])
            if state.get("accepted_work_id") == work_id:
                return {"work_id": work_id, "accepted_commit": expected_commit}
            if db.execute("SELECT 1 FROM work WHERE target_id=? AND status IN ('running','ready','waiting_approval')", (target_id,)).fetchone():
                raise ValueError("Pause the target and finish or cancel active work before accepting")
            if proposal["base_ref"] != state.get("accepted_branch", state.get("source_base_ref", "main")):
                raise ValueError("Proposal has a stale base; submit fresh work against the accepted base")
            directory = Path(proposal["workspace"])
            if git(directory, "rev-parse", "HEAD") != expected_commit or git(directory, "status", "--porcelain", "--untracked-files=all"):
                raise ValueError("Proposal workspace changed after completion; inspect and replan")
            state.update(accepted_branch=proposal["branch"], accepted_commit=expected_commit, accepted_work_id=work_id)
            db.execute("UPDATE targets SET state=? WHERE id=?", (json.dumps(state), target_id))
            self.store.log(db, "proposal_accepted", proposal, target_id, work_id)
            return {"work_id": work_id, "accepted_commit": expected_commit}

    def sync(self, target):
        """Call with the scheduler process lock held; preserve every old generation."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM work WHERE target_id=? AND status IN ('running','ready','waiting_approval')", (target.id,)).fetchone():
                raise ValueError("Finish or cancel active work before refreshing the source")
            state = json.loads(db.execute("SELECT state FROM targets WHERE id=?", (target.id,)).fetchone()[0])
            generation = "-" + uuid.uuid4().hex
            base = "source/" + generation[1:]
            key = hashlib.sha256(target.id.encode()).hexdigest()[:16] + generation
            repository = self.root / "repositories" / key
            commit = self.snapshot(target.workspace, repository, base)
            previous = {name: state.pop(name, None) for name in ("accepted_branch", "accepted_commit", "accepted_work_id")}
            state.update(source_generation=generation, source_base_ref=base, source_commit=commit)
            db.execute("UPDATE targets SET state=? WHERE id=?", (json.dumps(state), target.id))
            result = {"target_id": target.id, "source": str(target.workspace), "base_ref": base, "commit": commit, "previous_acceptance": previous}
            self.store.log(db, "source_synchronized", result, target.id)
            return result
