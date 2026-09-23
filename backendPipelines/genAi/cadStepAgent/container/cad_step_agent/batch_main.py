# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AWS Batch entrypoint: ``python -m cad_step_agent.batch_main``.

The definition document arrives in the ``CAD_AGENT_DEFINITION`` environment variable and the inner
task token in ``TASK_TOKEN`` (both set by the executeBatchJob Lambda as container overrides). Neither
travels in the command line: ``/proc/<pid>/cmdline`` is readable by every process in the container,
while the environment of a non-dumpable process is not.

The job definition runs the container under an init process, so the SIGTERM that AWS Batch sends when
the job is terminated reaches this interpreter; the handler stops the run (``cancellation``), which then
uploads nothing, reports nothing on its token and exits with ``EXIT_CANCELLED``.
"""

import logging
import os
import sys

from . import cancellation, run, sandbox

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("cad_step_agent.batch")

DEFINITION_ENV = "CAD_AGENT_DEFINITION"
# 128 + SIGINT, the shell convention for a process ended by an interrupt.
EXIT_CANCELLED = 130


def main(environ=None):
    environ = os.environ if environ is None else environ
    # First, before any thread exists: the agent's /proc entry must be closed to the scripts it runs.
    hardened = sandbox.harden_agent_process()
    cancellation.install_signal_handlers()
    # The uid the work runs under; a job's outcome says nothing about which account it ran as.
    logger.info("container.runtime_uid uid=%s euid=%s non_dumpable=%s", os.getuid(), os.geteuid(), hardened)
    definition = environ.get(DEFINITION_ENV, "")
    if not definition:
        logger.error("%s is not set; the executeBatchJob Lambda supplies it as a container override", DEFINITION_ENV)
        return 2
    task_token = environ.get(run.TASK_TOKEN_ENV, "")
    try:
        run.run_job(definition, task_token)
    except run.RunCancelled:
        return EXIT_CANCELLED
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
