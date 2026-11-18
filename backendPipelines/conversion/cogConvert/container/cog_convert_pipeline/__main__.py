# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entry point of the TIF to COG conversion container."""

import json
import logging
import os
import signal
import sys
import threading

from .utils.logging import log
from .utils.pipeline.objects import PipelineStatus, apply_input_versions

log.set_log_level(logging.INFO)

# AWS Batch sends SIGTERM and waits 30 seconds before SIGKILL. The task failure gets this much of that grace.
SIGTERM_REPORT_SECONDS = 10
SIGTERM_EXIT_CODE = 143


def install_sigterm_handler() -> None:
    """On SIGTERM: kill the GDAL child, report the failure on the internal task token and exit at once.

    Python runs as PID 1 in the container, so without a handler SIGTERM is ignored; Batch then waits out its grace
    period, kills the container and nothing reports on the token, which leaves the run waiting for its full task
    timeout. The report runs in a thread so a slow call cannot outlast the grace period."""

    def handle(_signum, _frame):
        logger = log.get_logger()
        logger.error("SIGTERM received: stopping the conversion")
        from . import convert
        from .utils.aws import sfn

        convert.kill_children()
        reporter = threading.Thread(
            target=lambda: _report(sfn, "The container was stopped (SIGTERM) before the conversion finished"),
            daemon=True)
        reporter.start()
        reporter.join(SIGTERM_REPORT_SECONDS)
        os._exit(SIGTERM_EXIT_CODE)

    signal.signal(signal.SIGTERM, handle)


def _report(sfn, message: str) -> None:
    try:
        sfn.send_task_failure(message, "CogConvertContainerError")
    except Exception as report_error:  # noqa: BLE001 - nothing more can be done; the exit code remains
        log.get_logger().error(f"The failure could not be reported: {report_error}")


def main() -> int:
    logger = log.get_logger()
    logger.info("COG Conversion Pipeline - Container Start")
    # The uid the work actually runs under (the image declares a non-root USER).
    logger.info("container.runtime_uid uid=%s euid=%s", os.getuid(), os.geteuid())
    install_sigterm_handler()

    from .core import run
    from .utils.aws import sfn

    try:
        if len(sys.argv) < 2:
            raise ValueError("The pipeline definition argument is missing")
        # The definition, then (when the run has one) the input's launch version.
        response = run(apply_input_versions(json.loads(sys.argv[1]), sys.argv[2:]))
    except BaseException as error:  # noqa: BLE001 - every route out of the container reports on the token
        logger.exception(error)
        _report(sfn, f"{type(error).__name__}: {error}"[:256])
        return 1
    return 0 if response.status is PipelineStatus.COMPLETE else 1


if __name__ == "__main__":
    sys.exit(main())
