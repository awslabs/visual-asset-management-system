#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Entry point of the AWS Batch render branch of the SYSTEM GenAI metadata pipeline.

The state machine passes the whole execution state through the ANALYSIS_STATE_JSON environment
variable; the render3d Lambda handler runs it unchanged under a stub context, so the Fargate branch
fulfils the same branch-task contract as the Lambda image. A non-zero exit fails the .sync task.

Reached through the image's entry point as ``python3 -m preview_pipeline analysisBatch``.
"""

import json
import os
import sys

import lambda_handler

# The Batch job definition's attempt duration; reported as the remaining time so the handler's
# budget checks see the Fargate allowance rather than a Lambda one.
_ATTEMPT_MILLIS = 4 * 60 * 60 * 1000


class _BatchContext:
    """The subset of the Lambda context the handler reads."""

    function_name = "FargateRenderJob"
    memory_limit_in_mb = 0

    def __init__(self) -> None:
        self.aws_request_id = os.environ.get("AWS_BATCH_JOB_ID", "")

    def get_remaining_time_in_millis(self) -> int:
        return _ATTEMPT_MILLIS


def main() -> int:
    raw = os.environ.get("ANALYSIS_STATE_JSON")
    if not raw:
        print("ANALYSIS_STATE_JSON is not set", file=sys.stderr)
        return 2
    try:
        state = json.loads(raw)
        lambda_handler.lambda_handler(state, _BatchContext())
    except Exception as exc:  # any failure fails the Batch job
        print(f"render job failed: {exc!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
