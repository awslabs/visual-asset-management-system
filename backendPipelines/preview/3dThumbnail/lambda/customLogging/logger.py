# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import re

from aws_lambda_powertools import Logger
from aws_lambda_powertools.logging.formatter import LambdaPowertoolsFormatter

location_format = "[%(funcName)s] %(module)s"
date_format = "%m/%d/%Y %I:%M:%S %p"

REDACTED = "<redacted>"

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

# The Batch container environment carries the token as a {"name": <key>, "value": <token>} pair
# inside a list, so the key sits in a VALUE rather than naming the entry.
_ENV_PAIR_NAME_KEY = "name"
_ENV_PAIR_VALUE_KEY = "value"

_KEY_ALTERNATION = "|".join(re.escape(key) for key in KEYS_TO_REDACT)
_QUOTE = r"""['"]"""

# A redacted key followed by its quoted value, as it appears once a dict has been rendered into
# text by an f-string (Python repr, single quotes) or by JSON (double quotes). Only the value is
# replaced, so the key stays visible as evidence that the field was present.
_QUOTED_KEY_VALUE = re.compile(
    r"(?P<lead>(?P<kq>" + _QUOTE + r")(?:" + _KEY_ALTERNATION + r")(?P=kq)\s*:\s*)"
    r"(?P<vq>" + _QUOTE + r")[^'\"]*(?P=vq)"
)

# The rendered form of the Batch environment pair: 'name': '<key>', 'value': '<token>'.
_QUOTED_ENV_PAIR = re.compile(
    r"(?P<lead>(?P<nq>" + _QUOTE + r")" + _ENV_PAIR_NAME_KEY + r"(?P=nq)\s*:\s*"
    r"(?P<kq>" + _QUOTE + r")(?:" + _KEY_ALTERNATION + r")(?P=kq)\s*,\s*"
    r"(?P<pq>" + _QUOTE + r")" + _ENV_PAIR_VALUE_KEY + r"(?P=pq)\s*:\s*)"
    r"(?P<vq>" + _QUOTE + r")[^'\"]*(?P=vq)"
)

# A Step Functions task token is one opaque run of URL-safe base64 several hundred characters
# long. Any such run is redacted wherever it stands in a string, whatever text surrounds it. The
# length floor keeps hashes (a hex SHA-512 is 128 characters), identifiers and keys out of reach;
# a single character class under a counted quantifier scans in linear time.
_TOKEN_SHAPE_MIN_LENGTH = 200
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9+/=_-]{" + str(_TOKEN_SHAPE_MIN_LENGTH) + r",}")


def scrub_token_text(text):
    """Redact task tokens from a string: by rendered key, by rendered Batch env pair, by shape."""
    text = _QUOTED_KEY_VALUE.sub(lambda m: m.group("lead") + m.group("vq") + REDACTED + m.group("vq"), text)
    text = _QUOTED_ENV_PAIR.sub(lambda m: m.group("lead") + m.group("vq") + REDACTED + m.group("vq"), text)
    return _TOKEN_SHAPE.sub(REDACTED, text)


def mask_sensitive_data(event):
    if isinstance(event, str):
        return scrub_token_text(event)
    if isinstance(event, list):
        return [mask_sensitive_data(item) for item in event]
    if not isinstance(event, dict):
        return event
    is_token_env_pair = event.get(_ENV_PAIR_NAME_KEY) in KEYS_TO_REDACT
    result = {}
    for k, v in event.items():
        if k in KEYS_TO_REDACT:
            result[k] = REDACTED
        elif is_token_env_pair and k == _ENV_PAIR_VALUE_KEY:
            result[k] = REDACTED
        elif isinstance(v, (dict, list, str)):
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
