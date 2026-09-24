# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Process-wide logging for both entrypoints (AWS Batch and Amazon Bedrock AgentCore Runtime)."""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
# The HTTP client behind the page-fetch tool logs every request it makes at INFO with the full URL,
# query string included; a redirect target's signed token would land verbatim in a log group kept for
# the stack's retention. The agent logs each tool call itself (host, no query string), so these
# libraries speak only when something goes wrong.
QUIET_LOGGERS = ("httpx", "httpcore")


def configure_logging(level=logging.INFO):
    """Set the root logger up and quiet the third-party loggers that would echo fetched URLs."""
    logging.basicConfig(level=level, format=LOG_FORMAT)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
