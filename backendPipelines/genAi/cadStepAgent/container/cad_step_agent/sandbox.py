# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hardened execution of agent-generated Python scripts.

A generated script never runs in the agent's own interpreter. It is written to a per-attempt directory
and run as a subprocess under a SEPARATE uid with a scrubbed environment, so it cannot read the
agent's memory, its task credentials, or the model API key, and a runaway script is bounded by a
wall-clock timeout and a fixed-size output tail.
"""

import os
import pwd
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
# The uid the generated script runs under (created in the Dockerfile as `sandboxrunner`).
SANDBOX_USER = os.environ.get("CAD_AGENT_SANDBOX_USER", "sandboxrunner")
DEFAULT_TIMEOUT_SECONDS = 300
OUTPUT_TAIL_LINES = 80
OUTPUT_TAIL_LINE_CHARS = 2000
SCRIPT_FILE_NAME = "script.py"
INPUT_STEP_ENV = "CAD_INPUT_STEP"
OUTPUT_STEP_ENV = "CAD_OUTPUT_STEP"


@dataclass
class ScriptResult:
    returncode: Optional[int]
    timed_out: bool
    output_tail: str
    output_path: str
    output_exists: bool
    uid: Optional[int] = None
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def succeeded(self):
        return self.returncode == 0 and not self.timed_out and self.output_exists


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


def resolve_sandbox_uid(user=SANDBOX_USER):
    """The numeric uid of the sandbox account, or None when it does not exist on this host.

    Only root can switch uid; when the agent already runs as a non-root user (the container's own
    account) the subprocess inherits that account, which still gains the scrubbed environment and the
    timeout. Both outcomes are recorded in the result so a run's log says which applied.
    """
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        return None


def _demote(uid):
    """A preexec function that switches the child to the sandbox uid (and its primary gid)."""
    def _switch():
        entry = pwd.getpwuid(uid)
        os.setgid(entry.pw_gid)
        os.setuid(uid)
    return _switch


def run_script(code, work_dir, input_step=None, output_name="output.step",
               timeout_seconds=DEFAULT_TIMEOUT_SECONDS, python_executable=None, uid=None):
    """Run ``code`` as a script in ``work_dir`` and return a ScriptResult.

    ``input_step`` (a path) is exposed to the script as ``CAD_INPUT_STEP`` and the required output path
    as ``CAD_OUTPUT_STEP``. The script is expected to write that output; whether it did is part of the
    result, so the agent can reason about a script that "succeeded" without producing geometry.
    """
    os.makedirs(work_dir, exist_ok=True)
    script_path = os.path.join(work_dir, SCRIPT_FILE_NAME)
    output_path = os.path.join(work_dir, output_name)
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(code)

    extra = {OUTPUT_STEP_ENV: output_path, "HOME": work_dir, "TMPDIR": work_dir}
    if input_step:
        extra[INPUT_STEP_ENV] = input_step
    env = scrubbed_environment(extra=extra)

    resolved_uid = uid if uid is not None else (resolve_sandbox_uid() if os.geteuid() == 0 else None)
    # The sandbox account must be able to enter the run directory and write the output.
    if resolved_uid is not None:
        os.chmod(work_dir, 0o777)  # nosec B103 - per-run temp directory, discarded after the attempt
        if input_step and os.path.exists(input_step):
            os.chmod(input_step, 0o444)

    argv = [python_executable or sys.executable, "-I", script_path]
    kwargs = {
        "cwd": work_dir,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "errors": "replace",
    }
    if resolved_uid is not None:
        kwargs["preexec_fn"] = _demote(resolved_uid)

    timed_out = False
    lines: List[str] = []
    returncode = None
    try:
        completed = subprocess.run(argv, timeout=timeout_seconds, check=False, **kwargs)  # nosec B603
        returncode = completed.returncode
        lines = (completed.stdout or "").splitlines()
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        captured = exc.stdout or ""
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", errors="replace")
        lines = captured.splitlines() + [f"[sandbox] script exceeded {timeout_seconds}s and was terminated"]

    return ScriptResult(
        returncode=returncode,
        timed_out=timed_out,
        output_tail=bounded_tail(lines),
        output_path=output_path,
        output_exists=os.path.isfile(output_path) and os.path.getsize(output_path) > 0,
        uid=resolved_uid,
    )


def new_attempt_dir(root=None, attempt=1):
    """A fresh directory for one script attempt."""
    base = root or tempfile.mkdtemp(prefix="cad-agent-")
    path = os.path.join(base, f"attempt-{attempt:02d}")
    os.makedirs(path, exist_ok=True)
    return path
