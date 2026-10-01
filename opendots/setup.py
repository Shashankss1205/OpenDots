"""First-run configuration and dependency diagnostics."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from .config import load_config


def default_config():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config'))) / 'opendots/config.json'


def initialize(directory=None, workspace=None, backend='claude', goal=None, demo=False, heartbeat=0):
    root = Path(directory) if directory else Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'opendots'
    root = root.expanduser().resolve()
    destination = root/'config.json'
    if destination.exists():
        raise ValueError(f'Configuration already exists: {destination}')
    if type(heartbeat) is not int or heartbeat < 0:
        raise ValueError('Heartbeat must be zero (off) or a positive number of seconds')
    if demo:
        if workspace or goal or heartbeat:
            raise ValueError('--demo cannot be combined with a workspace, goal, or heartbeat')
        template = Path(__file__).parent/'templates'
        config = json.loads((template/'config.json').read_text())
        if (root/'workspaces').exists():
            raise ValueError('Demo workspace destination already exists; choose a new directory')
        root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template/'workspaces', root/'workspaces')
        config['backend'] = 'demo'
    else:
        if backend == 'demo':
            raise ValueError('Demo recipes require explicit --demo; choose claude or codex for real work')
        if not workspace or not Path(workspace).expanduser().resolve().is_dir():
            raise ValueError('Pass --workspace with an existing project directory')
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError('Pass --goal with the objective you want your agent to pursue')
        config = {'backend': backend, 'sandbox': 'bubblewrap', 'targets': [{
            'id': 'project', 'name': 'My project', 'objective': goal.strip(),
            'workspace': str(Path(workspace).expanduser().resolve()),
            'subscriptions': [{'types': ['owner.*', 'input.*'], 'sources': ['local', 'file']},
                              {'types': ['timer.heartbeat'], 'sources': ['timer']}],
            'policy': {'read_file': 'auto', 'note': 'auto', 'write_file': 'approval',
                       'replace_text': 'approval', 'run_check': 'approval'},
            'write_paths': [], 'checks': {}, 'required_checks': [],
            'relevance': {'mode': 'model', 'minimum_confidence': 0.7}}],
            'sources': [], 'schedules': []}
        if heartbeat:
            config['schedules'] = [{'id': 'project-heartbeat', 'target_id': 'project',
                'type': 'timer.heartbeat', 'interval_seconds': heartbeat,
                'payload': {'title': 'Reassess progress toward the configured goal'}}]
        root.mkdir(parents=True, exist_ok=True)
    data = root/'data' if directory else Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'opendots'
    if not demo and data.resolve().is_relative_to(Path(workspace).expanduser().resolve()):
        raise ValueError('Configuration/data directory must be outside the project workspace')
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
    for provider, executable, login_args, profile in (
            ('codex', config.codex_command, ['login','status'], config.planner_home),
            ('claude', config.claude_command, ['auth','status'], config.claude_home)):
        if config.backend != provider and not any(t.agent == provider for t in config.targets):
            continue
        command = shutil.which(executable)
        add(provider, bool(command), command or f'Install {provider} CLI')
        if command:
            try:
                from .agents import CodexAgent, ClaudeAgent
                adapter = ClaudeAgent if provider == 'claude' else CodexAgent
                environment=adapter(planner_env=config.planner_env, planner_home=profile).environment()
                result = subprocess.run([command,*login_args], capture_output=True, timeout=10, env=environment)
                login = 'claude auth login' if provider == 'claude' else 'codex login'
                add(provider+'_authentication', result.returncode == 0, 'Authenticated' if result.returncode == 0 else 'Run '+login)
            except (OSError, subprocess.TimeoutExpired):
                add(provider+'_authentication', False, 'Authentication probe failed or timed out')
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
