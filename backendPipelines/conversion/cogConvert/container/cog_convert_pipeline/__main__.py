# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entry point of the TIF to COG conversion container."""

import json
import logging
import os
import sys

from .utils.logging import log
from .utils.pipeline.objects import PipelineStatus, apply_input_versions

log.set_log_level(logging.INFO)


def main() -> int:
    logger = log.get_logger()
    logger.info("COG Conversion Pipeline - Container Start")
    # The uid the work actually runs under (the image declares a non-root USER).
    logger.info("container.runtime_uid uid=%s euid=%s", os.getuid(), os.geteuid())

    from .core import run
    from .utils.aws import sfn

    try:
        if len(sys.argv) < 2:
            raise ValueError("The pipeline definition argument is missing")
        # The definition, then (when the run has one) the input's launch version.
        response = run(apply_input_versions(json.loads(sys.argv[1]), sys.argv[2:]))
    except BaseException as error:  # noqa: BLE001 - every route out of the container reports on the token
        logger.exception(error)
        try:
            sfn.send_task_failure(f"{type(error).__name__}: {error}"[:256])
        except Exception as report_error:  # noqa: BLE001 - nothing more can be done; the exit code remains
            logger.error(f"The failure could not be reported: {report_error}")
        return 1
    return 0 if response.status is PipelineStatus.COMPLETE else 1


if __name__ == "__main__":
    sys.exit(main())
