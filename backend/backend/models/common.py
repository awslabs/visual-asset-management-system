# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from decimal import Decimal
from typing import Any, Dict, TypedDict, Optional
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_errors

logger = safeLogger(service_name="CommonModels")


def _json_default(obj: Any):
    """json.dumps `default` hook for response bodies. DynamoDB numbers deserialize as Decimal, which
    the stdlib JSON encoder cannot serialize; convert to int when integral, else float. Applied to
    every API response body so any handler returning DynamoDB-sourced records is Decimal-safe."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


# Fallback when a ValidationError yields nothing reportable.
_GENERIC_VALIDATION_MESSAGE = "Invalid request."

# pydantic renders __root__ for a model-level (root_validator) error. It is an internal token, so it
# never reaches a caller — the accompanying message is emitted on its own instead.
_PYDANTIC_ROOT_LOC = "__root__"


def validation_error_message(exc: Any) -> str:
    """A caller-safe message for a pydantic ValidationError (backend Rule 11).

    `str(ValidationError)` must never be returned to a client: pydantic's own formatting wraps the
    errors in a header naming the MODEL CLASS and appends its error taxonomy plus internal constraint
    values — "1 validation error for CreatePipelineRequestModel / pipelineName / ensure this value has
    at most 256 characters (type=value_error.any_str.max_length; limit_value=256)". The model class
    names appear in no published documentation, and the taxonomy and limits describe the
    implementation rather than the contract.

    `.errors()` carries the same information in separate fields (loc / msg / type) with NO model name
    and NO taxonomy inside `msg`, so a safe message is assembled from it:

      - a FIELD error reports "field: msg". Field names are already published in the OpenAPI
        specification, so naming them discloses nothing and is what makes the error actionable.
      - a MODEL-level error (loc `__root__`) reports its `msg` alone. Those come from this codebase's
        own validators, which name their subject already ("id is invalid. Must follow the regexp ...");
        the `__root__` token itself is internal and is dropped.

    What has been VERIFIED about `msg`, stated precisely because the gap matters:
      - pydantic's OWN wordings describe the constraint that failed and never echo the submitted
        value ("ensure this value has at most 256 characters", "field required").
      - this codebase's hand-written validator messages carry no model class name and no taxonomy
        token.
    Those two facts do NOT add up to "every msg is safe". Some hand-written validators interpolate a
    CALLER-SUPPLIED VALUE into their message — "Extension '{ext}' must start with a dot" in
    models/databases.py surfaces the submitted extension verbatim. This function cannot detect that:
    an echoed value is indistinguishable from authored prose once it is inside `msg`. Echoes are
    therefore fixed AT THE VALIDATOR, by not interpolating the value in the first place (Rule 11).

    So when adding or editing a validator message, do not interpolate a caller-supplied value —
    describe the rule instead ("Extension must start with a dot (e.g., '.pdf')"). Interpolating one of
    this module's own labels or an allowed-values vocabulary is fine; both are ours, not the caller's.

    The full `str(exc)` still belongs in the log — pass it to `logger.exception` at the call site so
    the specifics stay debuggable. Returns a generic message when the exception exposes no usable
    errors, so a caller always receives something rather than an empty body."""
    try:
        errors = exc.errors()
    except Exception:  # nosec B110 - not a pydantic ValidationError; fall back to the generic message
        return _GENERIC_VALIDATION_MESSAGE

    parts = []
    for error in errors or []:
        message = (error.get("msg") or "").strip()
        if not message:
            continue
        location = [str(part) for part in (error.get("loc") or ())
                    if str(part) != _PYDANTIC_ROOT_LOC]
        # Nested locations join with '.' ("filters.0.query_string") so the caller can find the field
        # inside a request body; an index stays in place because it identifies which element failed.
        field = ".".join(location)
        parts.append(f"{field}: {message}" if field else message)

    return "; ".join(parts) if parts else _GENERIC_VALIDATION_MESSAGE


class APIGatewayProxyResponseV2(TypedDict):
    isBase64Encoded: bool
    statusCode: int
    headers: Dict[str, str]
    body: str


def commonHeaders() -> Dict[str, str]:
    return {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache, no-store',
        # The REST API integration returns Lambda responses verbatim, so the CORS
        # origin header must be set on the response itself (the OPTIONS preflight is
        # handled separately by the API's MOCK method). Mirrors the preflight's
        # allow-origin so cross-origin browser callers can read the response.
        'Access-Control-Allow-Origin': '*',
    }


def success(status_code: int = 200, body: Any = {'message': 'Success'}) -> APIGatewayProxyResponseV2:
    logger.info(f"Success response: {body}")
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body, default=_json_default)
    )


def validation_error(status_code: int = 400, body: dict = {'message': 'Validation Error'}, event: Optional[Dict[str, Any]] = None) -> APIGatewayProxyResponseV2:
    logger.error(f"Validation error: {body}")
    
    # AUDIT LOG: Log validation error if event provided
    if event:
        try:
            log_errors(event, "validation", {
                "statusCode": status_code,
                "errorMessage": body.get('message', 'Validation Error')
            })
        except Exception as audit_error:
            logger.exception(f"Failed to log validation error audit: {audit_error}")
    
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body, default=_json_default)
    )

def general_error(status_code: int = 400, body: dict = {'message': 'VAMS General Error'}, event: Optional[Dict[str, Any]] = None) -> APIGatewayProxyResponseV2:
    logger.error(f"General error: {body}")
    
    # AUDIT LOG: Log general error if event provided
    if event:
        try:
            log_errors(event, "general", {
                "statusCode": status_code,
                "errorMessage": body.get('message', 'VAMS General Error')
            })
        except Exception as audit_error:
            logger.exception(f"Failed to log general error audit: {audit_error}")
    
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body, default=_json_default)
    )


def authorization_error(status_code: int = 403, body: dict = {'message': 'Not Authorized'}, event: Optional[Dict[str, Any]] = None) -> APIGatewayProxyResponseV2:
    logger.error(f"Not Authorized Error: {body}")
    
    #Logged as part of Casbin auth checks
    # # AUDIT LOG: Log authorization error if event provided
    # # Note: This logs the error response, not the authorization check itself
    # # Authorization checks are logged by the Casbin enforcer
    # if event:
    #     try:
    #         log_errors(event, "authorization", {
    #             "statusCode": status_code,
    #             "errorMessage": body.get('message', 'Not Authorized')
    #         })
    #     except Exception as audit_error:
    #         logger.exception(f"Failed to log authorization error audit: {audit_error}")
    
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body, default=_json_default)
    )


def internal_error(status_code: int = 500, body: Any = {'message': 'Internal Server Error'}, event: Optional[Dict[str, Any]] = None) -> APIGatewayProxyResponseV2:
    logger.error(f"Internal Server Error: {body}")
    
    # AUDIT LOG: Log internal error if event provided
    if event:
        try:
            log_errors(event, "internal", {
                "statusCode": status_code,
                "errorMessage": body.get('message', 'Internal Server Error') if isinstance(body, dict) else str(body)
            })
        except Exception as audit_error:
            logger.exception(f"Failed to log internal error audit: {audit_error}")
    
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body, default=_json_default)
    )


#Define VAMS Custom Exceptions

class VAMSGeneralError(Exception):
    pass

class VAMSGeneralErrorResponse(VAMSGeneralError):
    def __init__(self, message, status_code=400):
        super().__init__(f"VAMS General Error: {message}")
        self.status_code = status_code