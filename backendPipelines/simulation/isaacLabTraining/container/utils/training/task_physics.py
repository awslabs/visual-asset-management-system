#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Readable failures for the Isaac Lab 3.0 run (issue #1669).

Isaac Lab 3.0 tasks declare the physics presets they support, and a run asks for one with the
``physics=<preset>`` Hydra override. A preset the task does not list fails during configuration
validation, 11 to 17 seconds after the command starts and before Isaac Sim starts, with::

    ValueError: Unknown preset(s): newton_mjwarp

    Available presets (grouped by affected paths):

      isaacsim_physx, newton_kamino, ovphysx, physx
        -> env.sim.physics

The container runs the real command and, when it exits with that error, raises a message that names
the task, the engine and the options this pipeline can select, in place of a Hydra traceback. There
is no separate listing run beforehand (``list_envs`` takes about a minute).

``run_isaac_lab`` also bounds a run with a wall-clock limit: play did not terminate in some runs
(a Kit visualizer under Newton, a Newton checkpoint played on PhysX), and without a limit the run
waits for the workflow's own timeout.
"""

import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from typing import List, Optional

# The value a run records when its task lists no physics presets and so runs without the selector.
# Kept equal to TASK_DEFAULT_PHYSICS in utils/training/config.py and lambda/openPipeline.py.
TASK_DEFAULT_PHYSICS = "task-default"

NO_PRESETS_NOTE = ("Task {task} declares no physics presets; it runs on its registered physics. "
                   "The physics setting is ignored.")

# The lines of output kept to read an error from.
TAIL_LINES = 400

# Isaac Lab's rejection of a ``physics=<preset>`` selector for a task that lists no preset of that
# type. It happens during configuration validation, before Kit starts (the reviewer's run: 15 s).
_TYPED_PRESET_REJECTION = re.compile(r"Typed preset selector\(s\)")
_PRESET_ERROR_LINE = re.compile(r"^\w*(?:Error|Exception):[^\n]*\bpreset[^\n]*$", re.IGNORECASE | re.MULTILINE)

_UNKNOWN_PRESET = re.compile(r"Unknown preset\(s\):\s*([^\n]+)")
_AVAILABLE_HEADER = re.compile(r"Available presets[^\n]*\n")
_OPTIONS_LINE = re.compile(r"^\s{2,}([A-Za-z0-9_]+(?:\s*,\s*[A-Za-z0-9_]+)*)\s*$")


def isaac_lab_version() -> str:
    """The Isaac Lab release of the image (``ISAAC_LAB_VERSION``, set by the Dockerfile)."""
    return os.environ.get("ISAAC_LAB_VERSION") or "3.0.0"


def parse_unknown_preset_error(output: str) -> Optional[dict]:
    """The rejected and the available presets of an ``Unknown preset(s)`` error, or ``None``.

    ``rejected`` is the list Isaac Lab named; ``available`` is the first group of presets it listed
    (``-> env.sim.physics`` in the real error), empty when the output has no such list.
    """
    match = _UNKNOWN_PRESET.search(output or "")
    if not match:
        return None
    rejected = [name.strip() for name in match.group(1).split(",") if name.strip()]
    available: List[str] = []
    header = _AVAILABLE_HEADER.search(output, match.end())
    if header:
        for line in output[header.end():].splitlines():
            option_line = _OPTIONS_LINE.match(line)
            if option_line:
                available = [name.strip() for name in option_line.group(1).split(",")]
                break
    return {"rejected": rejected, "available": available}


def is_typed_preset_rejection(output: str) -> bool:
    """Whether the run failed because the task has no preset of the type ``physics=`` selects."""
    return bool(_TYPED_PRESET_REJECTION.search(output or ""))


def preset_error_message(task: str, output: str) -> Optional[str]:
    """A readable message for a preset error of any other form, or ``None``.

    The first error line that mentions a preset is quoted (cut to 300 characters) so the run does
    not end in a bare exit code.
    """
    match = _PRESET_ERROR_LINE.search(output or "")
    if not match:
        return None
    return f"Task {task} failed while Isaac Lab resolved its presets: {match.group(0).strip()[:300]}"


def unknown_preset_message(task: str, physics: str, output: str, offered: tuple) -> Optional[str]:
    """The message for a run that failed on an unknown preset, or ``None`` for any other output."""
    parsed = parse_unknown_preset_error(output)
    if not parsed:
        return None
    version = isaac_lab_version()
    settable = [engine for engine in offered if engine in parsed["available"]]
    if settable:
        return (
            f"Task {task} does not offer physics={physics} in Isaac Lab {version}. Options for "
            f"this task: {', '.join(settable)}. Set \"Physics engine\" to one of these."
        )
    if parsed["available"]:
        return (
            f"Task {task} does not offer physics={physics} in Isaac Lab {version}, and none of the "
            f"engines this pipeline selects ({', '.join(offered)}). The task lists: "
            f"{', '.join(parsed['available'])}."
        )
    return (
        f"Task {task} does not accept physics={physics} in Isaac Lab {version}. A task that lists "
        f"no physics presets (an environment ported from Isaac Lab 2.x usually does not) cannot "
        f"select an engine this way; Isaac Lab rejected: {', '.join(parsed['rejected'])}."
    )


class IsaacLabRun:
    """The result of ``run_isaac_lab``."""

    def __init__(self, returncode: int, tail: str, timed_out: bool):
        self.returncode = returncode
        self.tail = tail
        self.timed_out = timed_out


def kill_process_group(process) -> None:
    """Kill ``process`` and everything it started.

    ``isaaclab.sh`` starts Isaac Lab's Python process as a child, so killing only the shell leaves
    Kit and the GPU work running. The run starts in its own session (``start_new_session``), so its
    process group id equals its pid and the group holds the whole tree. Falls back to the single
    process when the group cannot be signalled.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError, TypeError, OSError):
        process.kill()


def run_isaac_lab(cmd: list, cwd: str, timeout_seconds: Optional[float] = None,
                  popen=subprocess.Popen, clock=time.monotonic) -> IsaacLabRun:
    """Run ``cmd``, echoing its output as it comes and keeping the last lines.

    With ``timeout_seconds`` the process and its children (one process group) are killed when the limit passes. The command is a list and
    no shell is involved.
    """
    process = popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,  # nosemgrep: dangerous-subprocess-use-audit
                    text=True, errors="replace", bufsize=1,
                    start_new_session=True)
    tail = deque(maxlen=TAIL_LINES)
    timed_out = threading.Event()

    def pump():
        for line in process.stdout:
            print(line, end="", flush=True)
            tail.append(line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    started = clock()
    while process.poll() is None:
        if timeout_seconds is not None and clock() - started > timeout_seconds:
            timed_out.set()
            kill_process_group(process)
            break
        time.sleep(0.5)
    process.wait()
    reader.join(timeout=10)
    return IsaacLabRun(process.returncode, "".join(tail), timed_out.is_set())
