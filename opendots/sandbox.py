"""Linux namespaces + explicit filesystem mounts for configured local checks."""
import os
from pathlib import Path
import shutil
import sys


def sandbox_command(command, workspace):
    bwrap = shutil.which("bwrap")
    if not bwrap or sys.platform != "linux":
        raise RuntimeError("bubblewrap sandbox unavailable; install bubblewrap on Linux or explicitly select trusted-local for your own fixtures")
    argv = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    mounted = set()
    # Runtime libraries only. No host home, credentials, application DB, or sibling target.
    for directory in ["/usr", "/bin", "/lib", "/lib64", str(Path(sys.prefix).resolve())]:
        if Path(directory).exists() and directory not in mounted:
            argv += ["--ro-bind", directory, directory]
            mounted.add(directory)
    command = [str(Path(part).resolve()) if part == sys.executable else part for part in command]
    executable = Path(shutil.which(command[0]) or command[0]).resolve()
    if executable.is_file():
        command[0] = ("/workspace/" + executable.relative_to(workspace.resolve()).as_posix()
                      if executable.is_relative_to(workspace.resolve()) else str(executable))
    # Owner-configured standalone runtimes can live outside the system roots.
    # Bind only the selected executable, never its enclosing host directory.
    runtime_bind = []
    if executable.is_file() and not executable.is_relative_to(workspace.resolve()) and not any(
        executable.is_relative_to(Path(directory)) for directory in mounted
    ):
        runtime_bind = ["--ro-bind", str(executable), str(executable)]
    argv += ["--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/workspace", "--bind", str(workspace.resolve()), "/workspace",
             "--chdir", "/workspace", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp",
             "--setenv", "TMPDIR", "/tmp", "--setenv", "PYTHONDONTWRITEBYTECODE", "1", *runtime_bind, "--"]
    # Bound each process tree's resource use where prlimit is available.
    limit = shutil.which("prlimit")
    if limit:
        command = [limit, "--cpu=20", "--as=1073741824", "--fsize=16777216", "--nofile=128", "--", *command]
    return argv + command
