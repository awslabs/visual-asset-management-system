# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The container's init process (PID 1), the image's ``ENTRYPOINT`` on both runtimes.

``python3 -m cad_step_agent.init <command...>`` makes itself non-dumpable, then forks and execs the
command (the AgentCore app from the image's ``CMD``, ``batch_main`` from the Batch job definition's
command override), forwards the stop signals it receives to that child, reaps every process that is
reparented to it, and ends with the child's exit status once the child has ended -- the exit code for
a normal exit, ``128 + signal`` for a signal death, the shell convention.

Why the image carries its own init: PID 1 receives only the signals it has installed handlers for, and
descendants that lose their parent are reparented to it, so a container needs something at PID 1 that
forwards signals and reaps. The init the container service can inject (``initProcessEnabled``) is an
ordinary process of the container's uid holding the whole task environment -- the container credential
pointer, the task token, the definition document -- and it answers ``/proc/1/environ`` to any same-uid
reader, which is what a generated script is. This init holds the same environment but is non-dumpable
before its first child exists, so that read is refused (``sandbox.harden_agent_process``); its command
line, which ``/proc`` shows regardless, carries nothing but module names.
"""

import ctypes
import os
import signal
import sys

from . import sandbox

# prctl(2) option: orphaned descendants are reparented to this process rather than to the namespace's
# PID 1, so it reaps them wherever it runs (a unit test's init is not PID 1 of its namespace).
PR_SET_CHILD_SUBREAPER = 36
# Forwarded to the child. SIGKILL and SIGSTOP cannot be caught; SIGCHLD is the reaper's own signal.
FORWARDED_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)
EXIT_USAGE = 2
# The process would hold the task environment readable by every same-uid process: it does not start.
EXIT_NOT_HARDENED = 3
EXIT_EXEC_FAILED = 127


def set_child_subreaper():
    """Make the calling process the reaper of its orphaned descendants; True when the flag was set."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def exit_code(status):
    """The shell convention for a wait status: the exit code, or 128 + the number of the signal."""
    code = os.waitstatus_to_exitcode(status)
    return code if code >= 0 else 128 - code


def main(argv, harden=sandbox.harden_agent_process, subreaper=set_child_subreaper):
    """Run ``argv`` as this process's child and return the exit code to end with."""
    if not argv:
        print("usage: python3 -m cad_step_agent.init <command> [arguments...]", file=sys.stderr)
        return EXIT_USAGE
    # Before the child exists. The flag does not survive the child's exec (the child hardens itself),
    # so what is protected here is this process alone -- the one that must never be readable.
    if not harden():
        print("cad_step_agent.init: cannot make PID 1 non-dumpable; refusing to run with the task "
              "environment readable", file=sys.stderr)
        return EXIT_NOT_HARDENED
    subreaper()

    child = {"pid": None}

    def forward(signum, frame):
        # Installed before the fork, so no stop can arrive at PID 1 with no handler in place. In the
        # child the pid is still unset (fork copies the pre-fork state) and the handler does nothing.
        if child["pid"]:
            try:
                os.kill(child["pid"], signum)
            except ProcessLookupError:
                pass

    for sig in FORWARDED_SIGNALS:
        signal.signal(sig, forward)

    pid = os.fork()
    if pid == 0:  # the child: default signal dispositions, then the command
        for sig in FORWARDED_SIGNALS:
            signal.signal(sig, signal.SIG_DFL)
        try:
            os.execvp(argv[0], argv)  # nosec B606 - argv is this process's own command line, no shell
        except OSError as exc:
            print(f"cad_step_agent.init: cannot start {argv[0]}: {exc}", file=sys.stderr)
        os._exit(EXIT_EXEC_FAILED)
    child["pid"] = pid

    # Reap whatever ends -- the child, or an orphan reparented here -- until the child has ended. A
    # forwarded signal interrupts the wait, runs the handler and the wait resumes (PEP 475).
    while True:
        try:
            ended, status = os.waitpid(-1, 0)
        except ChildProcessError:  # nothing left to wait for: the child is gone without a status
            return 1
        if ended == pid:
            return exit_code(status)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
