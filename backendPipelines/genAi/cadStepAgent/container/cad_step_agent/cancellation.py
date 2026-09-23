# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cooperative cancellation of the run in progress.

A stop request (SIGTERM from AWS Batch's TerminateJob or the AgentCore session ending, SIGINT at a
terminal) sets one process-wide flag and kills the script the sandbox is running. The agent loop runs in
its own thread, so nothing can be raised into it from a signal handler; instead every tool checks the
flag before it does anything and raises ``RunCancelled``, the agent's before-model-call hook
(``agent.CancellationHook``) ends the loop instead of making the next model call, and ``run.run_job``
checks the flag once more when the agent returns. A cancelled run uploads nothing and reports nothing on
its task token: the workflow that sent the stop has already recorded the outcome.
"""

import logging
import signal
import threading

from . import sandbox

logger = logging.getLogger("cad_step_agent.cancellation")

# Signals a stop request arrives on.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class RunCancelled(Exception):
    """The run was stopped from outside before it finished."""


_requested = threading.Event()
_reason = {"value": ""}


def request(reason):
    """Record a stop request and kill the script the sandbox is running, if any."""
    _reason["value"] = str(reason)
    _requested.set()
    sandbox.kill_active_scripts()


def requested():
    """True once a stop has been requested."""
    return _requested.is_set()


def reason():
    """What requested the stop (``SIGTERM``, ``SIGINT``, or the caller's own words)."""
    return _reason["value"]


def raise_if_requested():
    """Raise ``RunCancelled`` when a stop has been requested."""
    if _requested.is_set():
        raise RunCancelled(_reason["value"])


def reset():
    """Clear a recorded stop request (a process that runs several jobs in turn, and the tests)."""
    _requested.clear()
    _reason["value"] = ""


def install_signal_handlers(chain=True):
    """Route ``STOP_SIGNALS`` to ``request``.

    With ``chain`` the handler that was installed before (a web server's graceful-exit handler, for
    example) still runs after the request is recorded; the interpreter's own defaults are not chained,
    since the default SIGTERM action would end the process before the request took effect. Must be
    called from the main thread. Returns the previous handlers by signal number.
    """
    previous = {}
    for sig in STOP_SIGNALS:
        previous[sig] = signal.getsignal(sig)

    def _handle(signum, frame):
        request(signal.Signals(signum).name)
        earlier = previous.get(signum)
        if chain and callable(earlier) and earlier is not signal.default_int_handler:
            earlier(signum, frame)

    for sig in STOP_SIGNALS:
        signal.signal(sig, _handle)
    return previous
