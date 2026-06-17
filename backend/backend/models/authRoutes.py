# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Auth routes API models for VAMS (web route checks and API route listing)."""

from typing import List, Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from customLogging.logger import safeLogger

logger = safeLogger(service_name="AuthRoutesModels")

# Maximum number of web routes accepted in a single check request.
MAX_WEB_ROUTES_PER_REQUEST = 500


class WebRouteCheckItemModel(BaseModel, extra='ignore'):
    """A single web route to check access for"""
    method: str = Field(min_length=1, max_length=10, strip_whitespace=True)
    route__path: str = Field(min_length=1, max_length=512, strip_whitespace=True)


class CheckWebRoutesRequestModel(BaseModel, extra='ignore'):
    """Request model for POST /auth/routes (web route access checks)"""
    routes: List[WebRouteCheckItemModel] = Field(min_items=1)

    @root_validator
    def validate_fields(cls, values):
        """Validate the route check list"""
        routes = values.get('routes') or []
        if len(routes) > MAX_WEB_ROUTES_PER_REQUEST:
            message = f"A maximum of {MAX_WEB_ROUTES_PER_REQUEST} routes can be checked per request"
            logger.error(message)
            raise ValueError(message)
        return values


class AllowedWebRouteModel(BaseModel, extra='ignore'):
    """A web route the user is allowed to access"""
    method: str
    route__path: str
    object__type: str = "web"


class CheckWebRoutesResponseModel(BaseModel, extra='ignore'):
    """Response model for POST /auth/routes"""
    allowedRoutes: List[AllowedWebRouteModel]
    email: str


class ApiRouteModel(BaseModel, extra='ignore'):
    """A single API route definition (full listing)"""
    path: str
    methods: List[str]
    category: str
    unauthenticated: Optional[bool] = False


class GetApiRoutesResponseModel(BaseModel, extra='ignore'):
    """Response model for GET /auth/routes/api (full API route list)"""
    routes: List[ApiRouteModel]


class AllowedApiRouteModel(BaseModel, extra='ignore'):
    """An API route with only the methods the requesting user is allowed to call"""
    path: str
    methods: List[str]
    category: str


class GetAllowedApiRoutesResponseModel(BaseModel, extra='ignore'):
    """Response model for GET /auth/routes/api/allowed (user-allowed API routes)"""
    routes: List[AllowedApiRouteModel]
    userId: str
