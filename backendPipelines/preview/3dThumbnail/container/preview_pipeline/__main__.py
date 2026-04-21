# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
import sys
import traceback
from .core import run, hello
from .utils.pipeline.objects import PipelineStatus
from .utils.logging import set_log_level

set_log_level(logging.INFO)


def main():
    hello()

    # run core application
    if sys.argv[1] == "localTest":
        # Local Test input
        # Usage: python -m preview_pipeline localTest PREVIEW_3D_THUMBNAIL [/data/input/filename.ext] [inputParametersJSON]
        # docker run -v /local/path:/data/input:ro -v /local/output:/data/output:rw <image> localTest PREVIEW_3D_THUMBNAIL [file] [params]
        testStageNameInput = sys.argv[2]
        localFilePath = sys.argv[3] if len(sys.argv) > 3 else ""
        inputParametersArg = sys.argv[4] if len(sys.argv) > 4 else ""
        # Escape any double quotes in inputParameters for safe JSON embedding
        inputParametersEscaped = inputParametersArg.replace('"', '\\"')
        testInput = (
            '{"jobName": "localTest", "stages": [{"type": "'
            + testStageNameInput
            + '", "inputFile": {"bucketName": "localTest", "objectKey": "'
            + localFilePath
            + '", "fileExtension": ""}, '
            '"outputFiles": {"bucketName": "localTest", "objectDir": "/data/output"}, '
            '"outputMetadata": {"bucketName": "localTest", "objectDir": "/data/output"}, '
            '"temporaryFiles": {"bucketName": "localTest", "objectDir": "/tmp/work"}}], '
            '"inputMetadata":"", "inputParameters":"'
            + inputParametersEscaped
            + '", '
            '"externalSfnTaskToken":"", "localTest":"True"}'
        )
        response = run(json.loads(testInput))
    else:
        try:
            response = run(json.loads(sys.argv[1]))
        except Exception as e:
            # Fatal error: ensure SFN callback fires before exiting
            error_msg = f"Fatal pipeline error: {str(e)}\n{traceback.format_exc()}"
            logging.getLogger().error(error_msg)
            _send_sfn_failure_on_fatal(error_msg)
            exit(1)

    # exit application with status
    exit_status = 0 if response.status is PipelineStatus.COMPLETE else 1
    exit(exit_status)


def _send_sfn_failure_on_fatal(error_msg: str):
    """
    Last-resort SFN failure callback for fatal errors that crash run() entirely.
    Ensures Step Functions always receives a callback even on unrecoverable failures.
    """
    task_token = os.getenv("TASK_TOKEN")
    if not task_token:
        return

    try:
        import boto3
        client = boto3.client(
            "stepfunctions", region_name=os.getenv("AWS_REGION", "us-east-1")
        )
        # SFN cause field has a 256-char limit — truncate with indicator
        if len(error_msg) > 253:
            cause = error_msg[:250] + "..."
        else:
            cause = error_msg
        client.send_task_failure(
            taskToken=task_token,
            error="Fatal pipeline error",
            cause=cause,
        )
        logging.getLogger().info("Sent SFN task failure callback for fatal error")
    except Exception as sfn_err:
        logging.getLogger().error(f"Failed to send SFN task failure callback: {sfn_err}")


if __name__ == "__main__":
    main()
