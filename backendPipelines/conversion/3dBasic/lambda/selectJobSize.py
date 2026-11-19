#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import computeTier
from customLogging.logger import safeLogger

logger = safeLogger(service="SelectJobSize3dBasic")


def lambda_handler(event, context):
    """
    SelectJobSize
    The state machine's tier selector: reads the named result the Lambda attempt returned and answers
    the AWS Batch job size to run it on, or fails the run with the message that names the limit.
    """
    attempt = event.get("attempt") if isinstance(event, dict) else None
    escalation = (attempt or {}).get("Payload") if isinstance(attempt, dict) else None
    if not isinstance(escalation, dict) or escalation.get("escalate") is not True:
        raise ValueError("The Lambda attempt did not ask for a larger job.")
    size = computeTier.choose_job_size(escalation, event.get("maxMemoryGiB"))
    logger.info(f"Job size {size['vcpu']} vCPU / {size['memoryMiB']} MiB for reason {escalation.get('reason')}")
    return {
        "jobSize": size,
        "escalationReason": escalation.get("reason"),
        "requiredMemoryMiB": escalation.get("requiredMemoryMiB"),
    }
