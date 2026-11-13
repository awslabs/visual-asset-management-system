# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset create, ingest and history record their action after it succeeded."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.assets.test_assetHistory_handler import _authorize as _authorize_history
from tests.handlers.assets.test_assetHistory_handler import _event as _history_event
from tests.handlers.assets.test_assetHistory_handler import _load as _load_history
from tests.handlers.assets.test_createAsset_conditional_put import _load as _load_create
from tests.handlers.assets.test_ingestAsset_upload_status_passthrough import _event as _ingest_event

CREATE_BODY = {"databaseId": "db-one", "assetName": "Bracket", "description": "A bracket", "isDistributable": True,
               "tags": []}


@pytest.fixture
def create_handler():
    m = _load_create(fresh=True)
    instance = MagicMock()
    instance.enforceAPI.return_value = True
    instance.enforce.return_value = True
    m.CasbinEnforcer = MagicMock(return_value=instance)
    m.request_to_claims = lambda event: {"tokens": ["tester"], "roles": []}
    m.create_asset = MagicMock(return_value=m.CreateAssetResponseModel(assetId="asset-1", message="created"))
    m.log_action = MagicMock()
    m.enforcer = instance
    return m


@pytest.mark.unit
class TestCreateRecord:
    def test_a_create_records_the_written_asset(self, create_handler):
        event = {"requestContext": {"http": {"method": "POST", "path": "/assets"}}, "body": json.dumps(CREATE_BODY)}
        assert create_handler.lambda_handler(event, None)["statusCode"] == 200
        create_handler.log_action.assert_called_once()
        kwargs = create_handler.log_action.call_args.kwargs
        assert create_handler.log_action.call_args.args == (event, "assetCreate")
        assert kwargs["database_id"] == "db-one" and kwargs["asset_id"] == "asset-1"
        assert kwargs["changes"]["assetName"] == "Bracket"

    def test_a_denied_create_records_nothing(self, create_handler):
        create_handler.enforcer.enforce.return_value = False
        event = {"requestContext": {"http": {"method": "POST", "path": "/assets"}}, "body": json.dumps(CREATE_BODY)}
        assert create_handler.lambda_handler(event, None)["statusCode"] == 403
        create_handler.log_action.assert_not_called()


@pytest.mark.unit
class TestIngestRecord:
    def _run(self, response, complete=False):
        from backend.backend.handlers.assets import ingestAsset as ia
        recorder = MagicMock()
        enforcer = MagicMock()
        enforcer.return_value.enforce.return_value = True
        enforcer.return_value.enforceAPI.return_value = True
        with patch.object(ia, "request_to_claims", return_value={"tokens": ["u1"], "roles": []}), \
                patch.object(ia, "CasbinEnforcer", enforcer), patch.object(ia, "log_action", recorder), \
                patch.object(ia, "initialize_ingest", return_value=response), \
                patch.object(ia, "complete_ingest", return_value=response):
            event = _ingest_event(complete)
            return ia.lambda_handler(event, None), recorder, event

    def test_an_initialize_records_the_files_and_declared_size(self):
        result, recorder, event = self._run(MagicMock(dict=lambda: {}))
        assert result["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "assetIngest")
        assert kwargs["file_count"] == 1 and "bytes" not in kwargs, "declared sizes are not stored bytes"
        assert kwargs["details"] == {"stage": "initialize", "files": ["asset-1/a.glb"], "declaredBytes": 1024}
        assert "files" not in kwargs["changes"]

    def test_a_complete_records_the_upload_id_and_the_files_that_landed(self):
        results = [MagicMock(success=True), MagicMock(success=False)]
        result, recorder, _ = self._run(MagicMock(dict=lambda: {}, fileResults=results), complete=True)
        assert result["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert kwargs["uploadId"] == "up-1" and kwargs["details"]["stage"] == "complete"
        assert kwargs["file_count"] == 1 and "bytes" not in kwargs
        assert "declaredBytes" not in kwargs["details"]


@pytest.mark.unit
class TestHistoryRecord:
    def test_a_history_read_records_the_request_and_the_count(self):
        m = _load_history()
        _authorize_history(m)
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {"Item": {"databaseId": "db1", "assetId": "a1"}}
        m.history_table = MagicMock()
        m.history_table.query.return_value = {"Items": []}
        m.log_action = MagicMock()
        event = _history_event()
        assert m.lambda_handler(event, MagicMock())["statusCode"] == 200
        assert m.log_action.call_args.args == (event, "assetHistoryGet")
        assert m.log_action.call_args.kwargs["item_count"] == 0

    def test_a_denied_history_read_records_nothing(self):
        m = _load_history()
        _authorize_history(m, allowed=False)
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {"Item": {"databaseId": "db1", "assetId": "a1"}}
        m.log_action = MagicMock()
        assert m.lambda_handler(_history_event(), MagicMock())["statusCode"] == 403
        m.log_action.assert_not_called()
