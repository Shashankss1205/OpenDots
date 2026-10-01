"""User-scoped systemd lifecycle; no root installation."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def unit_text(config, executable=None):
    config=Path(config).resolve()
    executable=executable or sys.executable
    if any(c in str(config)+executable for c in '\n\r\x00'):
        raise ValueError('Service paths cannot contain control characters')
    argv=[executable,'-m','opendots','--config',str(config),'serve']
    command=' '.join(shlex.quote(value.replace('%','%%')) for value in argv)
    return f'''[Unit]
Description=OpenDots persistent agent runtime
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=5
TimeoutStopSec=240
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=default.target
'''


def manage(action, config, name='opendots'):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',name): raise ValueError('Invalid service name')
    systemctl=shutil.which('systemctl')
    if not systemctl: raise RuntimeError('User services require systemd on Linux')
    unit=name+'.service'
    root=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'systemd/user'
    path=root/unit
    def control(*args):
        result=subprocess.run([systemctl,'--user',*args],capture_output=True,text=True,timeout=250)
        if result.returncode: raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'systemd command failed')
        return result.stdout.strip()
    if action=='install':
        text=unit_text(config)
        if path.exists() and path.read_text()!=text:
            raise ValueError(f'Existing service differs: {path}. Use a different --name.')
        root.mkdir(parents=True,exist_ok=True);path.write_text(text)
        control('daemon-reload')
        return {'unit':unit,'path':str(path),'next':f'opendots service start --name {name}'}
    if action=='start': return {'unit':unit,'result':control('enable','--now',unit)}
    if action=='stop': return {'unit':unit,'result':control('stop',unit)}
    if action=='status': return {'unit':unit,'result':control('status','--no-pager',unit)}
    raise ValueError('Unknown service action')
