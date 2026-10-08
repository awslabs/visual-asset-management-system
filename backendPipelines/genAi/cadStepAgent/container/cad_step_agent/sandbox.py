# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded execution of agent-generated Python scripts.

A generated script never runs in the agent's own interpreter. It is written to a per-attempt directory
and run as a subprocess in its own session with an isolated interpreter (``-I``), an allow-listed
environment that carries no credential variable, task token or API key, resource limits on address
space, process count and file size, and a wall-clock timeout after which the whole process group is
killed, along with every same-uid process that descends from the script or shares its session but
left the group (``os.setsid()`` / ``os.setpgid()`` in a child). Its output is kept as a fixed-size
tail, and collecting it after the kill is bounded too: a child that escaped the walk and still holds
the output pipe cannot keep the agent waiting.

What the sandbox is not: the script runs under the SAME uid, in the same network namespace and in the
same container as the agent, so it is not a uid, network or credential boundary on its own. The agent
process withholds its environment from same-uid readers by making itself non-dumpable
(``harden_agent_process``): ``/proc/<agent pid>/{environ,maps,mem,fd}`` then answer EACCES to any
process without CAP_SYS_PTRACE, which is what keeps the container credential pointer and the task
token out of a script that reads its parent's ``/proc`` entry. The container's init (``init``, PID 1,
the agent's parent) holds the same environment and hardens itself the same way before the agent
exists, so the walk up the process tree finds no readable ancestor.
"""

import ctypes
import os
import resource
import signal
import subprocess  # nosec B404 - the interpreter is invoked by absolute path with a fixed argv
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Variables the script may see. Everything else -- the AWS credential variables, the task token, the
# model API key, the agent's own configuration -- is withheld.
ALLOWED_ENV = ("PATH", "LANG", "LC_ALL", "PYTHONHASHSEED")
# Prefixes/names that are never forwarded even if a caller adds them to the allow list.
SECRET_ENV_MARKERS = ("AWS_", "TASK_TOKEN", "OPENAI", "SECRET", "TOKEN", "_KEY", "PASSWORD", "CREDENTIAL")
DEFAULT_TIMEOUT_SECONDS = 300
# How long, after the script's process group has been killed, the sandbox waits for the output pipe to
# close. EOF arrives only when every holder of the write end has exited; a child that left the group and
# escaped the descendant walk (a double fork that has already been reparented, for instance) still holds
# it, and past this grace the partial output is kept and the collection abandoned rather than blocking
# the agent for as long as that process lives.
SANDBOX_POST_KILL_GRACE_SECONDS = 3
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

# The script processes running right now, so a stop request can end them from another thread.
_active_lock = threading.Lock()
_active_scripts: List[subprocess.Popen] = []


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


def _proc_uid_and_parent(pid):
    """``(uid, ppid)`` from ``/proc/<pid>/status``, or None where the process is gone or unreadable."""
    uid = ppid = None
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("Uid:"):
                    uid = int(line.split()[1])
                elif line.startswith("PPid:"):
                    ppid = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    if uid is None or ppid is None:
        return None
    return uid, ppid


def _proc_session(pid):
    """The session id from ``/proc/<pid>/stat`` (field 6), or None."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as handle:
            stat = handle.read()
        # The command name (field 2) is parenthesised and may itself hold spaces or parentheses; the
        # numeric fields follow the LAST closing parenthesis: state, ppid, pgrp, session, ...
        fields = stat[stat.rindex(")") + 1:].split()
        return int(fields[3])
    except (OSError, ValueError, IndexError):
        return None


def script_descendants(script_pid, uid=None):
    """The pids of every process of ``uid`` that descends from the script (its ``PPid`` chain reaches
    ``script_pid``) or belongs to the script's session, the script itself excluded.

    The session catches a child that changed its process group but stayed in the session; the parent
    chain catches one that called ``setsid()`` -- for as long as its ancestors up to the script are
    alive, which is why the walk runs BEFORE the group is killed. Best-effort: a process that has gone
    or cannot be read is skipped, and the set is empty where there is no ``/proc``.
    """
    uid = os.getuid() if uid is None else uid
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return set()
    parents = {}
    for pid in pids:
        if pid == script_pid:
            continue
        found = _proc_uid_and_parent(pid)
        if found is not None and found[0] == uid:
            parents[pid] = found[1]
    descendants = set()
    for pid, ppid in parents.items():
        if _proc_session(pid) == script_pid:
            descendants.add(pid)
            continue
        seen = set()
        while ppid > 1 and ppid not in seen:
            if ppid == script_pid:
                descendants.add(pid)
                break
            seen.add(ppid)
            ppid = parents.get(ppid, 0)
    return descendants


def _kill_escapees(script_pid, pids):
    """SIGKILL every pid in ``pids`` that still belongs to this uid; returns how many were signalled.

    Never pid 1, the agent itself or the script (the group kill handles that one); a process that
    has gone or that cannot be signalled is skipped, and nothing raises into the caller.
    """
    killed = 0
    uid = os.getuid()
    for pid in pids:
        if pid <= 1 or pid in (os.getpid(), script_pid):
            continue
        found = _proc_uid_and_parent(pid)
        if found is None or found[0] != uid:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue
        killed += 1
    return killed


def _kill_script(proc):
    """SIGKILL the script's process group and every process that left it; returns the escapee count.

    The descendants are collected while the script is alive (a ``setsid()`` child is reachable only
    through its parent chain), the group is killed, the collected escapees are killed, and one more
    walk catches a process that joined the session between the walk and the kill.
    """
    escapees = script_descendants(proc.pid)
    _kill_process_group(proc)
    killed = _kill_escapees(proc.pid, escapees)
    killed += _kill_escapees(proc.pid, script_descendants(proc.pid) - escapees)
    return killed


def kill_active_scripts():
    """SIGKILL every script running right now, its process group and every process that left the
    group; returns how many scripts there were."""
    with _active_lock:
        procs = list(_active_scripts)
    for proc in procs:
        _kill_script(proc)
    return len(procs)


def _track(proc):
    with _active_lock:
        _active_scripts.append(proc)


def _untrack(proc):
    with _active_lock:
        if proc in _active_scripts:
            _active_scripts.remove(proc)


def _decode(captured):
    """The script's combined output as text; bytes the script wrote that are not UTF-8 are replaced."""
    if not captured:
        return ""
    if isinstance(captured, bytes):
        return captured.decode("utf-8", errors="replace")
    return str(captured)


def _collect_after_kill(proc, timeout_seconds):
    """The output lines of a script that was killed on timeout, collected within the post-kill grace.

    ``communicate`` returns when the output pipe reaches EOF, which happens only once every holder of
    the write end has exited. The group kill and the escapee walk end the script's processes, so that
    is normally immediate; a process the walk could not reach (already reparented, for instance) keeps
    the pipe open, and after ``SANDBOX_POST_KILL_GRACE_SECONDS`` the partial output is kept, the read
    end closed and the collection abandoned, so the escapee holds nothing of the agent's.
    """
    notice = f"[sandbox] script exceeded {timeout_seconds}s and was terminated"
    try:
        stdout, _ = proc.communicate(timeout=SANDBOX_POST_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout  # what communicate had read so far, across both attempts
        try:
            proc.stdout.close()
        except OSError:
            pass
        proc.wait()  # the script itself is dead; only the pipe was open
        return _decode(partial).splitlines() + [
            notice,
            f"[sandbox] output collection abandoned after {SANDBOX_POST_KILL_GRACE_SECONDS}s "
            "(a child process left the process group and still holds the output pipe)",
        ]
    return _decode(stdout).splitlines() + [notice]


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
    # kills the grandchildren too rather than leaving them running for the rest of the run; a child
    # that leaves the group is found through /proc and killed with it.
    proc = subprocess.Popen(  # nosec B603 - the interpreter is invoked by absolute path with a fixed argv
        argv,
        cwd=work_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=_rlimit_preexec(script_rlimits()),
    )
    _track(proc)
    try:
        stdout, _ = proc.communicate(timeout=timeout_seconds)
        returncode = proc.returncode
        lines = _decode(stdout).splitlines()
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_script(proc)
        lines = _collect_after_kill(proc, timeout_seconds)
    finally:
        _untrack(proc)

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
