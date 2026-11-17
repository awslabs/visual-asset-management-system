# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Audit log search API models for VAMS.

The search filters themselves are validated by ``common/auditSearch/queryBuilder.validate_filters`` (the
caller sends no query text, only enumerated values), so the start request carries them as one object
and the model checks only that the body is an object. The path and query parameters of the other
routes are validated here.
"""

import re
from typing import Any, Dict, List, Optional

from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator

from customLogging.logger import safeLogger

logger = safeLogger()

# A search handle is a UUID the service generates. Anything else is the same generic "not found" as an
# unknown handle, so the pattern is checked in the handler, not turned into a validation message here.
QUERY_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")

# Page size of a results read (rows). The response budget is 5 MB (under the 6 MB Lambda response limit).
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

# The long-poll wait a caller may ask for, before the configured wait and the Lambda's remaining time
# clamp it. The ceiling keeps a hostile value from reaching the clamp arithmetic.
MAX_WAIT_SECONDS = 60

# The offset token of a results page or export chunk: a decimal row offset. Short by construction.
MAX_TOKEN_LENGTH = 16

EXPORT_FORMATS = ("csv", "ndjson")
SORT_ORDERS = ("asc", "desc")


def _as_int(value: Any) -> Any:
    """Query-string numbers arrive as text; keep anything else as it is so the field check refuses it."""
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value):
        return int(value)
    return value


######################## Audit API Models ##########################

class StartAuditQueryRequestModel(BaseModel, extra='ignore'):
    """Request model for starting a search: the body is the filter object."""
    filters: Dict[str, Any]

    @root_validator(skip_on_failure=True)
    def validate_fields(cls, values):
        if not isinstance(values.get('filters'), dict):
            raise ValueError("Request body must be a JSON object")
        return values


class GetAuditQueryRequestModel(BaseModel, extra='ignore'):
    """Request model for a poll or results read of a search handle."""
    queryId: str = Field(default="")
    wait: Optional[int] = Field(default=0, ge=0, le=MAX_WAIT_SECONDS)
    pageSize: Optional[int] = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    startingToken: Optional[str] = Field(default=None, max_length=MAX_TOKEN_LENGTH)
    sort: Optional[str] = None

    _numbers = validator('wait', 'pageSize', pre=True, allow_reuse=True)(_as_int)

    @root_validator(skip_on_failure=True)
    def validate_fields(cls, values):
        if values.get('sort') is not None and values['sort'] not in SORT_ORDERS:
            raise ValueError("sort must be asc or desc")
        token = values.get('startingToken')
        if token is not None and not re.fullmatch(r"[0-9]{1,9}", token):
            raise ValueError("startingToken is not valid")
        return values


class StopAuditQueryRequestModel(BaseModel, extra='ignore'):
    """Request model for stopping a search."""
    queryId: str = Field(default="")


class ExportAuditQueryRequestModel(BaseModel, extra='ignore'):
    """Request model for one export chunk of a search."""
    queryId: str = Field(default="")
    format: str = Field(default="csv", min_length=1, max_length=16)
    startingToken: Optional[str] = Field(default=None, max_length=MAX_TOKEN_LENGTH)

    @root_validator(skip_on_failure=True)
    def validate_fields(cls, values):
        if values.get('format') not in EXPORT_FORMATS:
            raise ValueError("format must be csv or ndjson")
        token = values.get('startingToken')
        if token is not None and not re.fullmatch(r"[0-9]{1,9}", token):
            raise ValueError("startingToken is not valid")
        return values


######################## Audit API Response Models ##########################

class AuditQueryStartedResponseModel(BaseModel):
    """Response of a started search."""
    queryId: str
    status: str


class AuditLogGroupModel(BaseModel):
    """One log group the caller may search."""
    key: str
    label: str
    prefix: str
    description: str
    sensitivity: List[str] = []


def parse_start_body(body: Any) -> StartAuditQueryRequestModel:
    """The start request of a raw body object (the model wraps it as ``filters``)."""
    return StartAuditQueryRequestModel(filters=body)


def validate_query_id(query_id: Any) -> bool:
    """True for a handle in the shape the service generates."""
    return isinstance(query_id, str) and QUERY_ID_PATTERN.match(query_id) is not None
