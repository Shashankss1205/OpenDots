"""Linux supervisor: parent death terminates this owned process group.

Launched in its own session by bounded_process. Staying alive as the direct
parent lets SIGTERM from PR_SET_PDEATHSIG kill both command and descendants.
No preexec_fn is used in the application's multithreaded worker.
"""
import ctypes
import os
import signal
import subprocess
import sys


command_process = None
stopping = 0


def clean_group():
    if command_process is not None:
        try:
            os.killpg(command_process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def stop_group(signum, frame):
    global stopping
    stopping = signum
    # If a signal arrives during Popen construction, record it and perform
    # cleanup immediately after construction. No command PID race is ignored.
    clean_group()


def main():
    global command_process
    if len(sys.argv) < 3 or os.getpgrp() != os.getpid():
        raise RuntimeError("Process guard requires its own session and an owner command")
    parent_pid = int(sys.argv[1])
    signal.signal(signal.SIGTERM, stop_group)
    signal.signal(signal.SIGINT, stop_group)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "Cannot configure parent-death process guard")
    # Cover parent death before registration; kernel notification covers after.
    if os.getppid() != parent_pid:
        stop_group(signal.SIGTERM, None)
    if stopping:
        return 128 + stopping
    command_process = subprocess.Popen(sys.argv[2:], start_new_session=True)
    if stopping:
        clean_group()
    try:
        result = command_process.wait()
    finally:
        # Also clean ordinary background descendants after successful exit.
        # The guard has its own group, so cleanup preserves the command's code.
        clean_group()
    if stopping:
        return 128 + stopping
    return result if result >= 0 else 128 - result


if __name__ == "__main__":
    raise SystemExit(main())
