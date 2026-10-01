"""First-run configuration and dependency diagnostics."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from .config import load_config


def default_config():
    local = Path('examples/config.json')
    if local.is_file():
        return local
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config'))) / 'opendots/config.json'


def initialize(directory=None, workspace=None, backend='demo'):
    root = Path(directory) if directory else Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'opendots'
    root = root.expanduser().resolve()
    destination = root/'config.json'
    if destination.exists():
        raise ValueError(f'Configuration already exists: {destination}')
    if workspace and not Path(workspace).expanduser().resolve().is_dir():
        raise ValueError('Workspace must be an existing directory')
    root.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).parent/'templates'
    config = json.loads((template/'config.json').read_text())
    if workspace:
        config['targets'] = [{'id':'project','name':'My project','objective':'Investigate incoming events and propose reviewable improvements.',
            'workspace':str(Path(workspace).expanduser().resolve()),'subscriptions':[{'types':['owner.*']}],
            'policy':{'read_file':'auto','note':'auto','write_file':'approval','replace_text':'approval','run_check':'approval'},
            'write_paths':[], 'checks':{}, 'required_checks':[]}]
    else:
        if (root/'workspaces').exists():
            raise ValueError('Demo workspace destination already exists; choose a new directory')
        shutil.copytree(template/'workspaces', root/'workspaces')
    config['backend'] = backend
    data = root/'data' if directory else Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'opendots'
    config['database'] = str((data/'state.db').resolve())
    destination.write_text(json.dumps(config, indent=2)+'\n')
    destination.chmod(0o600)
    return destination


def diagnose(config_path):
    checks = []
    def add(name, ok, detail): checks.append({'name':name,'ok':ok,'detail':detail})
    config = load_config(config_path)
    add('configuration', True, str(config_path.resolve()))
    add('git', bool(shutil.which('git')), shutil.which('git') or 'Install Git')
    if config.backend == 'codex' or any(t.agent == 'codex' for t in config.targets):
        command = shutil.which(config.codex_command)
        add('codex', bool(command), command or 'Install Codex CLI')
        if command:
            try:
                result = subprocess.run([command,'login','status'], capture_output=True, timeout=10)
                add('codex_authentication', result.returncode == 0, 'Authenticated' if result.returncode == 0 else 'Run codex login')
            except (OSError, subprocess.TimeoutExpired):
                add('codex_authentication', False, 'Authentication probe failed or timed out')
    if config.sandbox == 'bubblewrap':
        from .sandbox import sandbox_command
        try:
            with tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(sandbox_command(['/usr/bin/true'],Path(directory)),capture_output=True,timeout=5)
            add('sandbox', result.returncode == 0, 'Namespace probe passed' if result.returncode == 0 else 'Bubblewrap namespaces are unavailable on this host')
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            add('sandbox', False, str(exc))
    else:
        add('sandbox', True, 'Explicit trusted-local mode; checks are not isolated')
    for target in config.targets:
        add(f'target:{target.id}', True, f'{len(target.required_checks)} required checks; write scopes: {list(target.write_paths)}')
    return {'ok':all(item['ok'] for item in checks),'checks':checks}
