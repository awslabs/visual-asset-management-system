# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Redaction for log text returned to API callers.

`safeLogger`/`mask_sensitive_data` (customLogging.logger) scrubs sensitive KEYS from the structured
dicts VAMS itself logs. This module is the counterpart for FREE-TEXT log data that VAMS returns to a
client — CloudWatch `filter_log_events` messages and Step Functions execution-history lines — where a
credential can appear inline (a JSON fragment, a `key=value` pair, a bearer token, an AWS key id, or a
JWT). Every log string surfaced by the executions logs endpoint must pass through `redact_log_text`
(scalars) or `redact_log_events` (the `[{message,...}]` event lists) before it leaves the handler.

The redaction is intentionally conservative: it targets labelled secrets (the same key names
safeLogger redacts) plus a few unambiguous credential shapes (Bearer tokens, AWS access-key ids,
JWTs). It never tries to guess at unlabelled high-entropy strings, so ordinary log content is left
intact.
"""

import re

REDACTED = "<redacted>"

# Sensitive key names — the credential keys in customLogging.logger.SENSITIVE_KEYS plus the two that
# reach a caller only through returned log text: the security-token header, and a Step Functions task
# token (an opaque capability to complete or fail the pending pipeline task that no IAM policy can
# resource-scope). Matched case-insensitively. `TaskToken` is unanchored in the JSON rule below, so it
# also covers the prefixed spellings the pipelines use (`externalSfnTaskToken`, `sfnExternalTaskToken`).
SENSITIVE_KEYS = [
    "authorization",
    "idJwtToken",
    "Credentials",
    "AccessKeyId",
    "SecretAccessKey",
    "SessionToken",
    "x-amz-security-token",
    "TaskToken",
]

_KEYS_ALT = "|".join(re.escape(k) for k in SENSITIVE_KEYS)

# A quote delimiter, optionally backslash-escaped. CloudWatch stores Step Functions state input and
# Lambda payloads as ESCAPED JSON, so the same pair reaches the redactor both as `"key": "value"` and
# as `\"key\": \"value\"` — with more backslashes the deeper the payload was nested. The repeat is
# BOUNDED: an unbounded `\\*` backtracks quadratically on a long run of backslashes, and log text is
# caller-influenced content, so a single crafted event could outlast the Lambda timeout. Eight covers
# three levels of re-encoding, well beyond anything the orchestration emits.
_Q = r'''(?:\\{0,8}["'])'''

# "key": "value"  /  "key": value  — JSON-style, value up to the next quote or delimiter.
_JSON_KV = re.compile(
    r'(?i)(' + _Q + r'?(?:' + _KEYS_ALT + r')' + _Q + r'?\s*[:=]\s*)('
    + _Q + r')([^"\']*?)(' + _Q + r')'
)
# key=value / key: value — bare (unquoted) value up to whitespace, comma, or delimiter.
_BARE_KV = re.compile(
    r'(?i)\b((?:' + _KEYS_ALT + r')\s*[:=]\s*)([^\s,;&"\']+)'
)
# Authorization bearer scheme.
_BEARER = re.compile(r'(?i)(bearer\s+)([A-Za-z0-9\-._~+/]+=*)')
# AWS access key id (AKIA/ASIA/AIDA/AROA/ANPA/AIPA + 16 uppercase alnum).
_AWS_KEY_ID = re.compile(r'\b(?:AKIA|ASIA|AIDA|AROA|ANPA|AIPA|ABIA|ACCA)[A-Z0-9]{16}\b')
# JWT (three base64url segments; first two are JSON objects that start with `eyJ`).
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')


def redact_log_text(text):
    """Redact sensitive credentials from a single free-text log string. Returns the text unchanged
    when there is nothing to redact. Non-string input is returned as-is."""
    if not text or not isinstance(text, str):
        return text
    # Bearer runs before the bare key=value rule so `Authorization: Bearer <token>` redacts the
    # TOKEN, not just the word "Bearer" (the bare rule would otherwise stop at the first space).
    redacted = _BEARER.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    redacted = _JSON_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(4)}", redacted)
    redacted = _BARE_KV.sub(lambda m: f"{m.group(1)}{REDACTED}", redacted)
    redacted = _AWS_KEY_ID.sub(REDACTED, redacted)
    redacted = _JWT.sub(REDACTED, redacted)
    return redacted


def redact_log_events(events):
    """Redact the `message` field of each CloudWatch/history event dict in a list. Returns a new
    list; entries without a string `message` are passed through untouched. Non-list input is
    returned as-is."""
    if not isinstance(events, list):
        return events
    out = []
    for ev in events:
        if isinstance(ev, dict) and isinstance(ev.get("message"), str):
            ev = {**ev, "message": redact_log_text(ev["message"])}
        out.append(ev)
    return out
