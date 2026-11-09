# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
import sys
from .pipelines import core
from .utils.pipeline.objects import PipelineStatus, apply_input_versions
from .utils.logging import log

log.set_log_level(logging.INFO)


def main():
    core.hello()

    # The uid the work actually runs under. The image declares a non-root USER and the Batch job
    # definition sets no `user` override, and neither is readable from a run's outcome: a job that
    # succeeds says nothing about which account it succeeded as.
    log.get_logger().info(
        "container.runtime_uid uid=%s euid=%s", os.getuid(), os.geteuid()
    )

    # run core application
    if sys.argv[1] == "localTest":
        #Local Test input
        testStageNameInput = sys.argv[2]
        testInput = "{\"jobName\": \"XXX\", \"stages\": [{\"type\": \""+testStageNameInput+"\", \"inputFile\": {\"bucketName\": \"XXX\", \"objectKey\": \"XXX\", \"fileExtension\": \"XXX\"}, \
            \"outputFiles\": {\"bucketName\": \"XXX\", \"objectDir\": \"XXX\"}, \"outputMetadata\": {\"bucketName\": \"XXX\", \"objectDir\": \"XXX\"}, \
            \"temporaryFiles\": {\"bucketName\": \"XXX\", \"objectDir\": \"XXX\"}}], \"inputMetadataS3Location\":\"\", \"inputConfigurationS3Location\":\"\", \
            \"externalSfnTaskToken\":\"\", \"localTest\":\"True\"}"
        
        response = core.run(json.loads(testInput))
    else:
        # The definition, then (when the run has one) the input's launch version (#994)
        response = core.run(apply_input_versions(json.loads(sys.argv[1]), sys.argv[2:]))

    # exit application with status
    exit_status = 0 if response.status is PipelineStatus.COMPLETE else 1
    exit(exit_status)


if __name__ == "__main__":
    main()
