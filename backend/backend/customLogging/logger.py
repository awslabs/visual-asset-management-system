# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import sys

from aws_lambda_powertools import Logger
from aws_lambda_powertools.logging.formatter import LambdaPowertoolsFormatter

location_format = "[%(funcName)s] %(module)s"
date_format = "%m/%d/%Y %I:%M:%S %p"

REDACTED = "<redacted>"

# Credential-shaped keys - the value is never safe to log. Key matching is on the WHOLE lowercased
# key name, so the api-key identifier fields (apiKeyId, apiKeyName, apiKeyHash) stay in the clear and
# the audit trail keeps naming which key an action used.
SENSITIVE_KEYS = ("authorization", "idJwtToken", "Credentials", "AccessKeyId", "SecretAccessKey",
                  "SessionToken", "apiKey", "apiKeySecret", "rawKey",
                  # The origin verification value CloudFront adds to forwarded API requests
                  # (common/auth/originVerify.py ORIGIN_VERIFY_HEADER_NAME).
                  "x-vams-origin-verify")

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


# Set once the Lambda entrypoint's name is applied; later names are ignored.
_service_name_pinned = False
# Set once the entrypoint's source has been read for its declared name.
_entrypoint_source_read = False

# The entrypoint's module-level declaration, e.g. logger = safeLogger(service_name="AssetService").
_DECLARED_NAME = re.compile(
    r"""^logger\s*=\s*safeLogger\(\s*(?:service_name|service)\s*=\s*(["'])([^"'\r\n]+)\1""", re.M)


def _entrypoint_module():
    # The Lambda runtime sets _HANDLER to "<module path>.<function>", e.g.
    # "handlers.assets.assetService.lambda_handler".
    handler = os.environ.get("_HANDLER", "")
    return handler.rsplit(".", 1)[0] if "." in handler else None


def _declared_service_name(path):
    try:
        with open(path, encoding="utf-8") as source:
            match = _DECLARED_NAME.search(source.read())
    except (OSError, ValueError):
        return None
    return match.group(2) if match else None


def _entrypoint_declared_name():
    # Read from the entrypoint's source so lines logged while its imports run, before its own
    # module body creates its logger, already carry its name.
    module = _entrypoint_module()
    if not module:
        return None
    relative = os.path.join(*module.split(".")) + ".py"
    for root in sys.path:
        path = os.path.join(root or os.getcwd(), relative)
        if os.path.isfile(path):
            return _declared_service_name(path)
    return None


def _caller_module(depth=2):
    try:
        return sys._getframe(depth).f_globals.get("__name__")
    except (AttributeError, ValueError):
        return None


def safeLogger(service_name=None, service=None, **kwargs):
    """Return the process-wide Powertools logger, labelled with the Lambda handler's service name.

    Every logger in a Lambda process is the same underlying logger with one formatter, so a service
    name labels every line the process writes, including lines from shared modules. Shared modules
    (common/, models/, customLogging/) call safeLogger() without a name and log under the handler's.

    A name is applied as both the `service_name` key and the Powertools `service` key. In Lambda the
    first call applies and pins the name the entrypoint module (from _HANDLER) declares in its source
    as `logger = safeLogger(service_name="...")`, so lines logged while its imports run carry it, and
    a handler module imported as a library, at cold start or lazily during an invocation, does not
    relabel them. If the declaration cannot be read, the entrypoint's own call pins its name. Without
    _HANDLER (local runs, tests) the most recent name wins, which at cold start is the entrypoint's
    because its module body runs after its imports.
    """
    global _service_name_pinned, _entrypoint_source_read
    logger = Logger(
        logger_formatter=CustomFormatter(),
        location=location_format,
        datefmt=date_format,
        log_uncaught_exceptions=True,
        level="INFO",
        **kwargs)
    if kwargs.get("child"):
        return logger
    if not _entrypoint_source_read:
        _entrypoint_source_read = True
        declared = _entrypoint_declared_name()
        if declared:
            logger.append_keys(service=declared, service_name=declared)
            _service_name_pinned = True
    name = service_name if service_name is not None else service
    if name and not _service_name_pinned:
        logger.append_keys(service=name, service_name=name)
        entrypoint = _entrypoint_module()
        if entrypoint is not None and _caller_module() == entrypoint:
            _service_name_pinned = True
    return logger


class CustomFormatter(LambdaPowertoolsFormatter):
    def serialize(self, log: dict) -> str:
        """Serialize final structured log dict to JSON str"""
        log = mask_sensitive_data(event=log)  # rename message key to event
        return self.json_serializer(log)  # use configured json serializer
