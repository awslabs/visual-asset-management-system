# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError


@pytest.mark.unit
class TestAssetHistoryModels:
    def test_request_defaults(self):
        from models.assetHistory import GetAssetHistoryRequestModel
        model = parse({}, model=GetAssetHistoryRequestModel)
        assert model.pageSize == 100
        assert model.startingToken is None

    def test_request_coerces_string_page_size(self):
        from models.assetHistory import GetAssetHistoryRequestModel
        model = parse({"pageSize": "50"}, model=GetAssetHistoryRequestModel)
        assert model.pageSize == 50

    def test_request_rejects_invalid_page_size(self):
        from models.assetHistory import GetAssetHistoryRequestModel
        with pytest.raises(ValidationError):
            parse({"pageSize": "0"}, model=GetAssetHistoryRequestModel)
        with pytest.raises(ValidationError):
            parse({"pageSize": "not-a-number"}, model=GetAssetHistoryRequestModel)

    def test_record_snapshot_is_open_schema(self):
        from models.assetHistory import AssetHistoryRecordModel
        record = parse({
            "historyRecordId": "2026-07-05T00:00:00Z#abc12345",
            "databaseId": "db1", "assetId": "a1",
            "recordDate": "2026-07-05T00:00:00Z",
            "changeSource": "edit", "changeUserId": "u1",
            "assetSnapshot": {"assetName": "A", "someFutureField": {"x": 1}},
        }, model=AssetHistoryRecordModel)
        # Unknown snapshot keys pass through untouched
        assert record.assetSnapshot["someFutureField"] == {"x": 1}
        assert record.migratedRecord is None

    def test_response_model(self):
        from models.assetHistory import GetAssetHistoryResponseModel
        model = parse({"Items": [], "NextToken": None}, model=GetAssetHistoryResponseModel)
        assert model.Items == []
        assert model.NextToken is None
