# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The outcome and counts of a compliance API call, for its VAMS action record.

Every compliance route-method is registered in ``common/auditActions.py``. Each compliance handler
records its route's action with ``log_action`` once the service function has answered, with the
outcome that answer carries (:func:`audit_outcome`), and records a call that raised, or that the
API-level check refused, with the route's own action. A data-level denial is also written by the
Casbin check that made it, to the authorization log.

What is recorded follows the record contract: a write records the fields it set (never a schema
body or another caller-authored document), a read records what was asked (ids, filters, page size)
and how many items it returned, never the items.
"""

import json
from typing import Any, Dict, Iterable, Optional

from customLogging.auditLogging import (
    OUTCOME_DENIED, OUTCOME_FAILED, OUTCOME_SUCCESS, audit_records_reads,
)

# The generic reason category of a refused or failed call, by its status code.
_REASONS = {400: "validation", 403: "forbidden", 404: "notFound", 409: "conflict",
            429: "throttled", 503: "unavailable"}


def audit_outcome(response: Any) -> Dict[str, str]:
    """``outcome`` (and ``reason``) of the response a compliance service function returned: a 2xx is
    a success, a 403 a denial, anything else a failure with its generic reason category."""
    status = response.get("statusCode") if isinstance(response, dict) else None
    if isinstance(status, int) and 200 <= status < 300:
        return {"outcome": OUTCOME_SUCCESS}
    if status == 403:
        return {"outcome": OUTCOME_DENIED, "reason": "forbidden"}
    return {"outcome": OUTCOME_FAILED, "reason": _REASONS.get(status, "internal")}


def failure_outcome(status_code: int) -> Dict[str, str]:
    """The outcome of a call the handler answered with ``status_code`` without a service response
    (a validation error raised while reading the request, an internal error)."""
    return audit_outcome({"statusCode": status_code})


def response_count(response: Any, *keys: str) -> Optional[int]:
    """The number of items the response body lists under the first of ``keys`` that holds a list,
    or None. The body is read only to count; nothing of it is recorded. With read records turned off
    (``app.auditLogging.recordReads``) the successful read writes no record, so the body is not
    parsed at all."""
    if not isinstance(response, dict) or not audit_records_reads():
        return None
    body = response.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            return None
    if not isinstance(body, dict):
        return None
    for key in keys:
        value = body.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def request_parameters(params: Optional[Dict[str, Any]], keys: Iterable[str]) -> Dict[str, Any]:
    """The given query or body parameters that were sent, by name. A continuation token is recorded
    only as having been sent (``startingTokenSent``), never its value."""
    params = params or {}
    picked: Dict[str, Any] = {}
    for key in keys:
        value = params.get(key)
        if value in (None, ""):
            continue
        if key in ("startingToken", "NextToken"):
            picked["startingTokenSent"] = True
        else:
            picked[key] = value
    return picked
