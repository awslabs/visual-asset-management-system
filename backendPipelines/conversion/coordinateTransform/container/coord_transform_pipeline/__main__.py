# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entry point for Coordinate Transform Pipeline container."""

import json
import logging
import sys

from .core import run
from .utils.logging import log
from .utils.pipeline.objects import PipelineStatus

log.set_log_level(logging.INFO)


def main():
    logger = log.get_logger()
    logger.info("Coordinate Transform Pipeline - Container Start")

    if sys.argv[1] == "localTest":
        test_stage = sys.argv[2] if len(sys.argv) > 2 else "COORD_TRANSFORM"
        test_input = json.dumps({
            "jobName": "LocalTest",
            "stages": [{
                "type": test_stage,
                "inputFile": {
                    "bucketName": "XXX",
                    "objectKey": "XXX",
                    "fileExtension": ".e57",
                },
                "outputFiles": {
                    "bucketName": "XXX",
                    "objectDir": "XXX",
                },
                "outputMetadata": {
                    "bucketName": "XXX",
                    "objectDir": "XXX",
                },
                "transformConfig": "",
            }],
            "inputMetadata": "",
            "inputParameters": "",
            "externalSfnTaskToken": "",
            "localTest": "True",
        })
        response = run(json.loads(test_input))
    else:
        response = run(json.loads(sys.argv[1]))

    exit_status = 0 if response.status is PipelineStatus.COMPLETE else 1
    exit(exit_status)


if __name__ == "__main__":
    main()
