# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded execution of agent-generated Python scripts.

A generated script never runs in the agent's own interpreter. It is written to a per-attempt directory
and run as a subprocess in its own session with an isolated interpreter (``-I``), an allow-listed
environment that carries no credential variable, task token or API key, resource limits on address
space, process count and file size, and a wall-clock timeout after which the whole process group is
killed. Its output is kept as a fixed-size tail.

What the sandbox is not: the script runs under the SAME uid, in the same network namespace and in the
same container as the agent, so it is not a uid, network or credential boundary on its own. The agent
process withholds its environment from same-uid readers by making itself non-dumpable
(``harden_agent_process``): ``/proc/<agent pid>/{environ,maps,mem,fd}`` then answer EACCES to any
process without CAP_SYS_PTRACE, which is what keeps the container credential pointer and the task
token out of a script that reads its parent's ``/proc`` entry.
"""

import ctypes
import os
import resource
import signal
import subprocess  # nosec B404 - the interpreter is invoked by absolute path with a fixed argv
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Variables the script may see. Everything else -- the AWS credential variables, the task token, the
# model API key, the agent's own configuration -- is withheld.
ALLOWED_ENV = ("PATH", "LANG", "LC_ALL", "PYTHONHASHSEED")
# Prefixes/names that are never forwarded even if a caller adds them to the allow list.
SECRET_ENV_MARKERS = ("AWS_", "TASK_TOKEN", "OPENAI", "SECRET", "TOKEN", "_KEY", "PASSWORD", "CREDENTIAL")
DEFAULT_TIMEOUT_SECONDS = 300
OUTPUT_TAIL_LINES = 80
OUTPUT_TAIL_LINE_CHARS = 2000
SCRIPT_FILE_NAME = "script.py"
INPUT_STEP_ENV = "CAD_INPUT_STEP"
OUTPUT_STEP_ENV = "CAD_OUTPUT_STEP"

# Resource limits applied to the script process (and inherited by anything it spawns). Address space
# rather than resident memory is what RLIMIT_AS bounds, and the Open CASCADE libraries map several GB
# of virtual space before a solid exists, so the bound is generous; it is there to stop an unbounded
# allocation from taking the whole container (an OOM-killed agent never reports its task token).
SCRIPT_MAX_ADDRESS_SPACE_BYTES = 8 * 1024 ** 3
# RLIMIT_NPROC counts every process and thread of the uid system-wide, the agent's own included, so
# the bound is this many tasks ABOVE the uid's count at launch: room for the interpreter's and Open
# CASCADE's worker threads, an end to a fork loop.
SCRIPT_MAX_PROCESSES = 256
# The largest single file a script may write; a generated STEP file is kilobytes to megabytes.
SCRIPT_MAX_FILE_BYTES = 512 * 1024 ** 2

# prctl(2) option that clears the process's dumpable flag.
PR_SET_DUMPABLE = 4


@dataclass
class ScriptResult:
    returncode: Optional[int]
    timed_out: bool
    output_tail: str
    output_path: str
    output_exists: bool
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def succeeded(self):
        return self.returncode == 0 and not self.timed_out and self.output_exists


def harden_agent_process():
    """Make the calling process non-dumpable so same-uid processes cannot read its /proc entry.

    Returns True when the flag was cleared. Linux only: elsewhere (a developer's machine running the
    unit tests) there is no prctl and the function returns False without raising.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError):
        return False
    return result == 0


def scrubbed_environment(source=None, extra=None):
    """The environment a generated script receives: an allow list, minus anything credential-shaped."""
    source = os.environ if source is None else source
    env = {}
    for key in ALLOWED_ENV:
        if key in source and not _looks_secret(key):
            env[key] = source[key]
    env["PYTHONSAFEPATH"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    for key, value in (extra or {}).items():
        if _looks_secret(key):
            raise ValueError(f"refusing to forward credential-shaped variable {key} to the sandbox")
        env[key] = value
    return env


def _looks_secret(key):
    upper = key.upper()
    return any(marker in upper for marker in SECRET_ENV_MARKERS)


def bounded_tail(lines, max_lines=OUTPUT_TAIL_LINES, max_chars=OUTPUT_TAIL_LINE_CHARS):
    """The last ``max_lines`` lines, each cut to ``max_chars`` characters."""
    kept = [line[:max_chars] for line in lines[-max_lines:]]
    return "\n".join(kept)


def uid_task_count(uid=None):
    """How many processes and threads the real uid owns right now (what RLIMIT_NPROC is checked
    against), or None where /proc does not answer."""
    uid = os.getuid() if uid is None else uid
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as handle:
                owner, threads = None, 1
                for line in handle:
                    if line.startswith("Uid:"):
                        owner = int(line.split()[1])
                    elif line.startswith("Threads:"):
                        threads = int(line.split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if owner == uid:
            total += threads
    return total


def script_rlimits():
    """The (resource, limit) pairs a script process is started under, computed in the parent."""
    limits = [(resource.RLIMIT_AS, SCRIPT_MAX_ADDRESS_SPACE_BYTES),
              (resource.RLIMIT_FSIZE, SCRIPT_MAX_FILE_BYTES)]
    current = uid_task_count()
    if current is not None:
        limits.append((resource.RLIMIT_NPROC, current + SCRIPT_MAX_PROCESSES))
    return tuple(limits)


def _rlimit_preexec(limits):
    """A preexec function that applies ``limits`` in the child between fork and exec.

    Only setrlimit calls: no imports, no logging, nothing that takes a lock another thread of the
    agent may hold at fork time.
    """
    def _apply():
        for which, limit in limits:
            _, hard = resource.getrlimit(which)
            bound = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
            resource.setrlimit(which, (bound, bound))
    return _apply


def _kill_process_group(proc):
    """SIGKILL the script's session (the script and everything it forked)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        proc.kill()


def run_script(code, work_dir, input_step=None, output_name="output.step",
               timeout_seconds=DEFAULT_TIMEOUT_SECONDS, python_executable=None):
    """Run ``code`` as a script in ``work_dir`` and return a ScriptResult.

    ``input_step`` (a path) is exposed to the script as ``CAD_INPUT_STEP`` and the required output path
    as ``CAD_OUTPUT_STEP``. The script is expected to write that output; whether it did is part of the
    result, so the agent can reason about a script that "succeeded" without producing geometry.
    """
    os.makedirs(work_dir, mode=0o700, exist_ok=True)
    script_path = os.path.join(work_dir, SCRIPT_FILE_NAME)
    output_path = os.path.join(work_dir, output_name)
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(code)

    extra = {OUTPUT_STEP_ENV: output_path, "HOME": work_dir, "TMPDIR": work_dir}
    if input_step:
        extra[INPUT_STEP_ENV] = input_step
    env = scrubbed_environment(extra=extra)

    argv = [python_executable or sys.executable, "-I", script_path]
    timed_out = False
    lines: List[str] = []
    returncode = None
    # A new session puts the script and every process it spawns in one process group, so a timeout
    # kills the grandchildren too rather than leaving them running for the rest of the run.
    proc = subprocess.Popen(  # nosec B603 - the interpreter is invoked by absolute path with a fixed argv
        argv,
        cwd=work_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,
        preexec_fn=_rlimit_preexec(script_rlimits()),
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout_seconds)
        returncode = proc.returncode
        lines = (stdout or "").splitlines()
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc)
        stdout, _ = proc.communicate()
        lines = (stdout or "").splitlines() + [f"[sandbox] script exceeded {timeout_seconds}s and was terminated"]

    return ScriptResult(
        returncode=returncode,
        timed_out=timed_out,
        output_tail=bounded_tail(lines),
        output_path=output_path,
        output_exists=os.path.isfile(output_path) and os.path.getsize(output_path) > 0,
    )


def new_attempt_dir(root=None, attempt=1):
    """A fresh directory for one script attempt."""
    base = root or tempfile.mkdtemp(prefix="cad-agent-")
    path = os.path.join(base, f"attempt-{attempt:02d}")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path
