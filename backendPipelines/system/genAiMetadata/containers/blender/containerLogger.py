# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Structured logger for the Blender Lambda image -- the same Powertools configuration the other pipeline
containers carry, redacting `authorization` values before serialisation."""

from aws_lambda_powertools import Logger
from aws_lambda_powertools.logging.formatter import LambdaPowertoolsFormatter

location_format = "[%(funcName)s] %(module)s"
date_format = "%m/%d/%Y %I:%M:%S %p"


def mask_sensitive_data(event):
    keys_to_redact = ["authorization"]
    result = {}
    for key, value in event.items():
        if isinstance(value, dict):
            result[key] = mask_sensitive_data(value)
        elif key in keys_to_redact:
            result[key] = "<redacted>"
        else:
            result[key] = value
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
