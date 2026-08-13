# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.logging.formatter import LambdaPowertoolsFormatter

location_format = "[%(funcName)s] %(module)s"
date_format = "%m/%d/%Y %I:%M:%S %p"

REDACTED = "<redacted>"

# Credential-shaped keys - the value is never safe to log.
SENSITIVE_KEYS = ("authorization", "idJwtToken", "Credentials", "AccessKeyId", "SecretAccessKey", "SessionToken")

# Caller-authored payload keys - a pipeline template body or a tag value carries free-form content
# (prompts, model configuration, file paths). The key is kept so the record still shows that the field
# was submitted; the value is not. Every field that carries a template body belongs here, whichever
# request delivers it: an execute request supplies one as customTemplateOverride, and a template record
# holds the same content as configBody with its form definition in webFormJson.
CONTENT_KEYS = ("configBody", "templateTags", "tagValues",
                "customTemplateOverride", "webFormJson", "inputInstructions")

# Keys whose value is the raw request payload, delivered either as a dict or as a JSON string.
BODY_KEYS = ("body",)

# Keys are matched case-insensitively: API Gateway preserves header case, so the same header arrives as
# both `Authorization` and `authorization` depending on the client.
_REDACT_KEYS = frozenset(k.lower() for k in SENSITIVE_KEYS + CONTENT_KEYS)
_BODY_KEYS = frozenset(k.lower() for k in BODY_KEYS)


def _mask_body_string(value):
    # A JSON-string body hides its keys from the key walk, so parse it, mask the parsed structure and
    # re-serialize only when something was redacted (an untouched body stays byte-identical). A body
    # that names a content key but does not parse is redacted whole - there is no way to reach the
    # value on its own.
    try:
        parsed = json.loads(value)
    except Exception:
        parsed = None
    if not isinstance(parsed, (dict, list)):
        lowered = value.lower()
        return REDACTED if any(k in lowered for k in _REDACT_KEYS) else value
    masked = mask_sensitive_data(parsed)
    if masked == parsed:
        return value
    try:
        return json.dumps(masked)
    except Exception:
        return REDACTED


def mask_sensitive_data(event):
    # remove sensitive data from request object before logging
    try:
        if isinstance(event, dict):
            result = {}
            for k, v in event.items():
                key = k.lower() if isinstance(k, str) else k
                if key in _REDACT_KEYS:
                    result[k] = REDACTED
                elif key in _BODY_KEYS and isinstance(v, str):
                    result[k] = _mask_body_string(v)
                elif isinstance(v, (dict, list, tuple)):
                    result[k] = mask_sensitive_data(v)
                else:
                    result[k] = v
            return result
        if isinstance(event, (list, tuple)):
            return [mask_sensitive_data(v) for v in event]
        return event
    except Exception:
        # Logging and audit writes must never fail on an unmaskable structure; drop it instead.
        return REDACTED


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
        """Serialize final structured log dict to JSON str"""
        log = mask_sensitive_data(event=log)  # rename message key to event
        return self.json_serializer(log)  # use configured json serializer
