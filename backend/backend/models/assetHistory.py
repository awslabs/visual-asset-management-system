# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Dict, List, Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel


class GetAssetHistoryRequestModel(BaseModel, extra='ignore'):
    """Query parameters for listing asset lifecycle history records"""
    pageSize: Optional[int] = Field(default=100, ge=1, le=1000)
    startingToken: Optional[str] = None


class AssetHistoryRecordModel(BaseModel, extra='ignore'):
    """One asset lifecycle history record. assetSnapshot is an open-schema
    map of asset fields as they stood after the operation; unknown keys are
    passed through so older readers keep working as the snapshot grows."""
    historyRecordId: str
    databaseId: str
    assetId: str
    recordDate: str
    changeSource: str
    changeUserId: str
    assetSnapshot: Dict[str, Any] = {}
    migratedRecord: Optional[bool] = None


class GetAssetHistoryResponseModel(BaseModel, extra='ignore'):
    """Response model for listing asset lifecycle history records"""
    Items: List[AssetHistoryRecordModel]
    NextToken: Optional[str] = None
