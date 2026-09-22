# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AWS Batch entrypoint: ``python -m cad_step_agent.batch_main '<definition json>'``.

The definition document arrives as the job's command override and the inner task token as the
``TASK_TOKEN`` environment variable (both set by the executeBatchJob Lambda).
"""

import logging
import os
import sys

from . import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("cad_step_agent.batch")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # The uid the work runs under; a job's outcome says nothing about which account it ran as.
    logger.info("container.runtime_uid uid=%s euid=%s", os.getuid(), os.geteuid())
    if not argv:
        logger.error("usage: python -m cad_step_agent.batch_main '<definition json>'")
        return 2
    task_token = os.environ.get(run.TASK_TOKEN_ENV, "")
    try:
        run.run_job(argv[0], task_token)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
