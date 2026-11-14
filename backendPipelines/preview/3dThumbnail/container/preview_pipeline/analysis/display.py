# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""X display bootstrap for the 3D render image.

The PyPI ``vtk`` wheel renders through GLX, so an off-screen render still needs an X server. The Fargate
image's ENTRYPOINT starts Xvfb on ``:99`` before the pipeline runs (the SYSTEM GenAI metadata Fargate render
job and the 3D preview thumbnail jobs run that image); the Lambda image has no entrypoint of its own to do
that in, so the handler starts it here — once per execution environment, reused across warm invocations —
with the server's socket and lock under ``/tmp``, the only writable path in Lambda.

A server this process did not start (the ENTRYPOINT's, ``xvfb-run``'s) is reused as it is, and its lock
and socket are never touched: a second Xvfb cannot bind a display a running server holds. A lock or socket
is removed only when no server answers on the display and no live process holds its lock.
"""

import os
import re
import shutil
import socket
import subprocess  # nosec B404 - Xvfb is started with a fixed argument list and no shell
import time
from typing import Optional

from ..utils.logging import get_logger

logger = get_logger()

DEFAULT_DISPLAY = ":99"
SCREEN_GEOMETRY = "1280x1024x24"
# Fixed by the X server at build time, not scratch locations of ours: the socket directory and the
# directory of the ``.X<n>-lock`` file.
X11_SOCKET_DIR = "/tmp/.X11-unix"  # nosec B108
X11_LOCK_DIR = "/tmp"  # nosec B108
# How long one connection attempt to a display's socket may take.
PROBE_TIMEOUT_SECONDS = 1.0
POLL_SECONDS = 0.05

# ":N", ":N.S" and "unix:N" name a server on this host; a host name in front names a remote one.
_LOCAL_DISPLAY = re.compile(r"^(?:unix)?:(\d+)(?:\.\d+)?$")
# /proc/<pid>/stat states of a process that has exited: zombie and dead.
_EXITED_STATES = (b"Z", b"X", b"x")

_process = None


def xvfb_command(display: str = DEFAULT_DISPLAY) -> list:
    return ["Xvfb", display, "-screen", "0", SCREEN_GEOMETRY, "-nolisten", "tcp", "-noreset"]


def socket_path(display: str = DEFAULT_DISPLAY, socket_dir: str = X11_SOCKET_DIR) -> str:
    return os.path.join(socket_dir, "X" + display.lstrip(":"))


def lock_path(display: str = DEFAULT_DISPLAY, lock_dir: str = X11_LOCK_DIR) -> str:
    return os.path.join(lock_dir, f".X{display.lstrip(':')}-lock")


def local_display(value: Optional[str]) -> Optional[str]:
    """``":N"`` for a display name that names a server on this host (``:N``, ``:N.S``, ``unix:N``), else
    ``None``."""
    match = _LOCAL_DISPLAY.match((value or "").strip())
    return f":{match.group(1)}" if match else None


def answers(sock: str, timeout_seconds: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """True when a server accepts a connection on the X socket ``sock``. A socket file left by a server
    that has exited refuses the connection."""
    if not hasattr(socket, "AF_UNIX") or not os.path.exists(sock):
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(timeout_seconds)
    try:
        probe.connect(sock)
        return True
    except OSError:
        return False
    finally:
        probe.close()


def process_alive(pid: int) -> bool:
    """True when process ``pid`` runs. A zombie counts as exited: the image's ENTRYPOINT ``exec``s Python,
    which never reaps an Xvfb that died, so such a server's pid stays in the process table."""
    if pid <= 0:
        return False
    if os.path.isdir("/proc/self"):
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                stat = handle.read()
        except OSError:
            return False
        fields = stat.rsplit(b")", 1)[-1].split()
        return bool(fields) and fields[0] not in _EXITED_STATES
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def lock_owner(display: str = DEFAULT_DISPLAY, lock_dir: str = X11_LOCK_DIR) -> Optional[int]:
    """The pid the display's lock file names when that process runs, else ``None`` (no lock, an unreadable
    one, or one whose server has exited)."""
    try:
        with open(lock_path(display, lock_dir), "rb") as handle:
            pid = int(handle.read(64).strip() or b"0")
    except (OSError, ValueError):
        return None
    return pid if process_alive(pid) else None


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def _use(display: str, how: str) -> str:
    os.environ["DISPLAY"] = display
    logger.info(f"X display {display}: {how}")
    return display


def _server_of_another_process(display: str, sock: str, lock_dir: str, deadline: float,
                               timeout_seconds: float) -> bool:
    """True once a server this process did not start serves ``display``. A live process holding the
    display's lock whose socket does not answer yet is a server still starting: it is waited for until
    ``deadline``, never replaced. False when nothing holds the display."""
    owner = lock_owner(display, lock_dir)
    if answers(sock):
        return True
    if owner is None:
        return False
    while time.monotonic() < deadline:
        if answers(sock):
            return True
        if lock_owner(display, lock_dir) is None:
            return False
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"The X server holding {display} (pid {owner}) did not answer within "
                       f"{timeout_seconds:.1f}s")


def ensure_display(display: str = DEFAULT_DISPLAY, timeout_seconds: float = 15.0,
                   socket_dir: str = X11_SOCKET_DIR, lock_dir: str = X11_LOCK_DIR) -> str:
    """An X display to render on: the server ``DISPLAY`` already names when it answers, a server another
    process runs on ``display``, or else an Xvfb started here on ``display``. Exports ``DISPLAY`` and
    returns the display name; raises ``RuntimeError`` when no server can be brought up."""
    global _process
    sock = socket_path(display, socket_dir)
    if is_running() and os.path.exists(sock):
        os.environ["DISPLAY"] = display
        return display

    current = local_display(os.environ.get("DISPLAY"))
    if current and current != display and answers(socket_path(current, socket_dir)):
        return _use(current, "reusing the server DISPLAY names")
    deadline = time.monotonic() + timeout_seconds
    if _server_of_another_process(display, sock, lock_dir, deadline, timeout_seconds):
        return _use(display, "reusing the running server")

    if shutil.which("Xvfb") is None:
        raise RuntimeError("Xvfb is not installed in this image; rendering needs an X server")
    os.makedirs(socket_dir, exist_ok=True)
    try:
        os.chmod(socket_dir, 0o1777)
    except OSError:
        pass
    # No server answers on the display and no live process holds its lock, so a lock or socket still there
    # was left by a server that has exited, and it would stop the new one from binding the display.
    for stale in (lock_path(display, lock_dir), sock):
        if os.path.exists(stale):
            os.remove(stale)

    _process = subprocess.Popen(  # nosec B603 - fixed argument list, no shell
        xvfb_command(display), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while time.monotonic() < deadline:
        if _process.poll() is not None:
            code = _process.returncode
            _process = None
            # Another process's server took the display between the checks above and this start.
            if _server_of_another_process(display, sock, lock_dir, deadline, timeout_seconds):
                return _use(display, "reusing the server that started first")
            raise RuntimeError(f"Xvfb exited with code {code} before opening {display}")
        if os.path.exists(sock):
            pid = getattr(_process, "pid", None)
            return _use(display, f"started Xvfb (pid {pid})" if pid else "started Xvfb")
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"Xvfb did not open {display} within {timeout_seconds:.1f}s")
