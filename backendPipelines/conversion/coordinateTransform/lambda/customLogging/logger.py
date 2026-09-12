# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from aws_lambda_powertools import Logger
from aws_lambda_powertools.logging.formatter import LambdaPowertoolsFormatter

location_format = "[%(funcName)s] %(module)s"
date_format = "%m/%d/%Y %I:%M:%S %p"

# Keys whose values never reach a log line. The two task tokens travel under several spellings:
# the workflow body's TaskToken, the nested-invoke payload's sfnExternalTaskToken, the state
# machine input's externalSfnTaskToken, and the Batch container environment's TASK_TOKEN.
KEYS_TO_REDACT = (
    "authorization",
    "externalSfnTaskToken",
    "sfnExternalTaskToken",
    "taskToken",
    "TaskToken",
    "TASK_TOKEN",
)


def mask_sensitive_data(event):
    if isinstance(event, list):
        return [mask_sensitive_data(item) for item in event]
    if not isinstance(event, dict):
        return event
    result = {}
    for k, v in event.items():
        if k in KEYS_TO_REDACT:
            result[k] = "<redacted>"
        elif isinstance(v, (dict, list)):
            result[k] = mask_sensitive_data(v)
        else:
            result[k] = v
    return result


def safeLogger(**kwargs):
    return Logger(
        logger_formatter=CustomFormatter(),
        location=location_format,
        datefmt=date_format,
        log_uncaught_exceptions=True,
        level="INFO",
        **kwargs)


class CustomFormatter(LambdaPowertoolsFormatter):
    def serialize(self, log: dict) -> str:
        log = mask_sensitive_data(event=log)
        return self.json_serializer(log)
