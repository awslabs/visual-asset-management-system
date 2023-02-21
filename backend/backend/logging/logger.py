# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from aws_lambda_powertools import Logger
from aws_lambda_powertools.logging.formatter import LambdaPowertoolsFormatter

location_format = "[%(funcName)s] %(module)s"
date_format = "%m/%d/%Y %I:%M:%S %p"


def mask_sensitive_data(event):
    # remove sensitive data from request object before logging
    keys_to_redact = ["authorization"]
    result = {}
    for k, v in event.items():
        if isinstance(v, dict):
            result[k] = mask_sensitive_data(v)
        elif k in keys_to_redact:
            result[k] = "<redacted>"
        else:
            result[k] = v
    return result


class CustomFormatter(LambdaPowertoolsFormatter):
    def serialize(self, log: dict) -> str:
        """Serialize final structured log dict to JSON str"""
        log = mask_sensitive_data(event=log)  # rename message key to event
        return self.json_serializer(log)  # use configured json serializer


def safeLogger(**kwargs):
    return Logger(
        logger_formatter=CustomFormatter(),
        location=location_format,
        datefmt=date_format,
        **kwargs)
