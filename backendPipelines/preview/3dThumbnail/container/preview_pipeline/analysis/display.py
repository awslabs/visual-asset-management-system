# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""X display bootstrap for the Lambda runtime.

The PyPI ``vtk`` wheel renders through GLX, so an off-screen render still needs an X server. The Fargate
image starts Xvfb from its ENTRYPOINT; the Lambda image has no entrypoint of its own to do that in, so
the handler starts it here — once per execution environment, reused across warm invocations — with the
server's socket and lock under ``/tmp``, the only writable path in Lambda.
"""

import os
import shutil
import subprocess  # nosec B404 - Xvfb is started with a fixed argument list and no shell
import time

from ..utils.logging import get_logger

logger = get_logger()

DEFAULT_DISPLAY = ":99"
SCREEN_GEOMETRY = "1280x1024x24"
# Fixed by the X server at build time, not scratch locations of ours: the socket directory and the
# directory of the ``.X<n>-lock`` file.
X11_SOCKET_DIR = "/tmp/.X11-unix"  # nosec B108
X11_LOCK_DIR = "/tmp"  # nosec B108

_process = None


def xvfb_command(display: str = DEFAULT_DISPLAY) -> list:
    return ["Xvfb", display, "-screen", "0", SCREEN_GEOMETRY, "-nolisten", "tcp", "-noreset"]


def socket_path(display: str = DEFAULT_DISPLAY, socket_dir: str = X11_SOCKET_DIR) -> str:
    return os.path.join(socket_dir, "X" + display.lstrip(":"))


def lock_path(display: str = DEFAULT_DISPLAY, lock_dir: str = X11_LOCK_DIR) -> str:
    return os.path.join(lock_dir, f".X{display.lstrip(':')}-lock")


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def ensure_display(display: str = DEFAULT_DISPLAY, timeout_seconds: float = 15.0,
                   socket_dir: str = X11_SOCKET_DIR, lock_dir: str = X11_LOCK_DIR) -> str:
    """Start Xvfb on ``display`` unless this environment already runs one. Exports ``DISPLAY`` and
    returns the display name; raises ``RuntimeError`` when no server can be brought up."""
    global _process
    sock = socket_path(display, socket_dir)
    if is_running() and os.path.exists(sock):
        os.environ["DISPLAY"] = display
        return display
    if shutil.which("Xvfb") is None:
        raise RuntimeError("Xvfb is not installed in this image; rendering needs an X server")

    os.makedirs(socket_dir, exist_ok=True)
    try:
        os.chmod(socket_dir, 0o1777)
    except OSError:
        pass
    # A lock or socket left by a server that no longer runs blocks the new one from binding the display.
    for stale in (lock_path(display, lock_dir), sock):
        if os.path.exists(stale):
            os.remove(stale)

    _process = subprocess.Popen(  # nosec B603 - fixed argument list, no shell
        xvfb_command(display), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if os.path.exists(sock):
            os.environ["DISPLAY"] = display
            logger.info(f"Xvfb serving {display} (pid {_process.pid})" if hasattr(_process, "pid")
                        else f"Xvfb serving {display}")
            return display
        if _process.poll() is not None:
            code = _process.returncode
            _process = None
            raise RuntimeError(f"Xvfb exited with code {code} before opening {display}")
        time.sleep(0.05)
    raise RuntimeError(f"Xvfb did not open {display} within {timeout_seconds:.1f}s")
