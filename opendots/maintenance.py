"""Offline maintenance of one runtime database and its managed workspaces."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time

from .engine import process_lock
from .store import Store
from .workspaces import git


def workspace_root(config):
    key = hashlib.sha256(config.database.name.encode()).hexdigest()[:16]
    return config.database.parent / 'workspaces' / key


def inventory(root):
    from .evidence import hash_file
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if relative == 'manifest.json':
            continue
        if path.is_symlink():
            result[relative] = {'link': str(path.readlink())}
        elif path.is_file():
            result[relative] = {'sha256': hash_file(path), 'mode': path.stat().st_mode & 0o777}
    return result


def backup(config, destination, config_path):
    destination = Path(destination).resolve()
    root = workspace_root(config).resolve()
    if not config.database.is_file():
        raise ValueError('Runtime database does not exist')
    if destination.exists():
        raise ValueError('Backup destination must not exist')
    if destination.is_relative_to(config.database.parent.resolve()) or any(destination.is_relative_to(t.workspace.resolve()) for t in config.targets):
        raise ValueError('Backup destination must be outside runtime storage and source workspaces')
    with process_lock(config.database):
        destination.mkdir(parents=True, mode=0o700)
        with sqlite3.connect(config.database) as source, sqlite3.connect(destination/'state.db') as target:
            source.backup(target)
        (destination/'state.db').chmod(0o600)
        if root.exists():
            shutil.copytree(root, destination/'workspaces', symlinks=True)
        shutil.copyfile(config_path, destination/'config.json')
        (destination/'config.json').chmod(0o600)
        manifest = {'version': 1, 'database': str(config.database.resolve()), 'workspaces': str(root),
                    'created': time.time(), 'files': inventory(destination)}
        (destination/'manifest.json').write_text(json.dumps(manifest, indent=2))
    return {'backup': str(destination), 'files': len(manifest['files'])}


def restore(config, source):
    source = Path(source).resolve()
    manifest = json.loads((source/'manifest.json').read_text())
    root = workspace_root(config).resolve()
    if manifest.get('version') != 1 or manifest.get('database') != str(config.database.resolve()) or manifest.get('workspaces') != str(root):
        raise ValueError('Restore requires the original database and workspace paths from the backup')
    if inventory(source) != manifest.get('files'):
        raise ValueError('Backup inventory does not match; restore refused')
    config.database.parent.mkdir(parents=True, exist_ok=True)
    with process_lock(config.database):
        if any(p.exists() or p.is_symlink() for p in (config.database, root, Path(str(config.database)+'-wal'), Path(str(config.database)+'-shm'))):
            raise ValueError('Restore refuses to overwrite existing runtime data; move it aside first')
        with sqlite3.connect(source/'state.db') as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup database failed integrity check')
        if (source/'workspaces').exists():
            shutil.copytree(source/'workspaces', root, symlinks=True)
        shutil.copyfile(source/'state.db', config.database)
        config.database.chmod(0o600)
    return {'restored': str(config.database)}


def cleanup(config, older_than_days=30, apply=False):
    if older_than_days < 1:
        raise ValueError('Retention must be at least one day')
    root = workspace_root(config).resolve()
    cutoff = time.time() - older_than_days * 86400
    with process_lock(config.database):
        store = Store(config.database)
        with store.connect() as db:
            protected = {json.loads(row[0]).get('accepted_work_id') for row in db.execute('SELECT state FROM targets')}
            candidates = []
            for row in db.execute("SELECT id,status,workspace,branch FROM work WHERE updated<? AND status NOT IN ('queued','running','ready','waiting_approval')", (cutoff,)):
                path = Path(row['workspace']) if row['workspace'] else None
                if row['id'] in protected or path is None or not path.exists():
                    continue
                if path.is_symlink() or path.parent.resolve() != root/'tasks':
                    raise ValueError('Task workspace is outside managed storage')
                candidates.append(dict(row))
            if apply:
                for work in candidates:
                    path = Path(work['workspace'])
                    common = Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
                    if not common.resolve().is_relative_to(root/'repositories'):
                        raise ValueError('Task Git metadata is outside managed repositories')
                    git(common.parent, 'worktree', 'remove', '--force', str(path))
                    store.log(db, 'workspace_archived', {'workspace':str(path),'branch':work['branch']}, work_id=work['id'])
                    db.commit()
    return {'dry_run': not apply, 'workspaces': candidates, 'retained': 'branches, patches, task history and audit evidence'}
