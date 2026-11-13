# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The database handlers record each action after it succeeded, and nothing when it did not.

A write records what it changed (and the prior values the handler already read for its check); a
read records what was asked and how many items it returned, never the items. A denied or failed
request records no success: the authorization record is a denial's record, and the entrypoint
records a failed request's outcome.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.databases import createDatabase as create_svc
from backend.backend.handlers.databases import databaseService as svc

DATABASE_ID = "factory-db"
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}
EVENT = {"requestContext": {"http": {"method": "PUT", "path": f"/database/{DATABASE_ID}"}}}


def _stored():
    return {"databaseId": DATABASE_ID, "description": "before", "defaultBucketId": "bucket-1",
            "restrictMetadataOutsideSchemas": False, "restrictFileUploadsToExtensions": ""}


@pytest.fixture
def recorder(monkeypatch):
    recorder = MagicMock()
    monkeypatch.setattr(svc, "log_action", recorder)
    return recorder


@pytest.fixture
def table(monkeypatch):
    table = MagicMock()
    table.get_item.return_value = {"Item": _stored()}
    buckets = MagicMock()
    buckets.query.return_value = {"Items": [{"bucketId": "bucket-2"}]}
    resource = MagicMock()
    resource.Table.side_effect = lambda name: buckets if name == svc.s3_asset_buckets_table else table
    monkeypatch.setattr(svc, "dynamodb", resource)
    monkeypatch.setattr(svc, "to_update_expr", lambda updates: ({}, {}, "SET x = :x"), raising=False)
    return table


@pytest.fixture
def allow(monkeypatch):
    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    monkeypatch.setattr(svc, "CasbinEnforcer", enforcer)
    return enforcer


@pytest.mark.unit
class TestDatabaseUpdateRecord:
    def test_an_update_records_the_changed_fields_and_their_prior_values(self, recorder, table, allow):
        svc.update_database(DATABASE_ID, {"description": "after", "defaultBucketId": "bucket-2",
                                          "restrictMetadataOutsideSchemas": None}, AUTHENTICATED, event=EVENT)
        recorder.assert_called_once_with(
            EVENT, "databaseUpdate", database_id=DATABASE_ID,
            changes={"description": "after", "defaultBucketId": "bucket-2"},
            previous={"description": "before", "defaultBucketId": "bucket-1"})

    def test_a_denied_update_records_nothing(self, recorder, table, allow):
        allow.return_value.enforce.return_value = False
        with pytest.raises(svc.DatabaseAccessDeniedError):
            svc.update_database(DATABASE_ID, {"description": "after"}, AUTHENTICATED, event=EVENT)
        recorder.assert_not_called()

    def test_an_update_of_a_missing_database_records_nothing(self, recorder, table, allow):
        table.get_item.return_value = {}
        with pytest.raises(svc.VAMSGeneralErrorResponse):
            svc.update_database(DATABASE_ID, {"description": "after"}, AUTHENTICATED, event=EVENT)
        recorder.assert_not_called()

    def test_the_handler_passes_its_event(self, recorder, table, allow):
        response = svc.update_database_handler(EVENT, {"databaseId": DATABASE_ID},
                                               {"description": "after text"}, AUTHENTICATED)
        assert response["statusCode"] == 200
        assert recorder.call_args.args == (EVENT, "databaseUpdate")


@pytest.mark.unit
class TestDatabaseDeleteRecord:
    @pytest.fixture(autouse=True)
    def empty(self, monkeypatch):
        for check in ("check_workflows", "check_pipelines", "check_assets"):
            monkeypatch.setattr(svc, check, lambda database_id: False)

    def test_a_delete_is_recorded(self, recorder, table, allow):
        response = svc.delete_database_handler(EVENT, {"databaseId": DATABASE_ID}, AUTHENTICATED)
        assert response["statusCode"] == 200
        recorder.assert_called_once_with(EVENT, "databaseDelete", database_id=DATABASE_ID)

    @pytest.mark.parametrize("setup, status", [
        ("missing", 404), ("denied", 403), ("busy", 400)])
    def test_a_delete_that_did_not_happen_records_nothing(self, recorder, table, allow, monkeypatch,
                                                         setup, status):
        if setup == "missing":
            table.get_item.return_value = {}
        elif setup == "denied":
            allow.return_value.enforce.return_value = False
        else:
            monkeypatch.setattr(svc, "check_assets", lambda database_id: True)
        response = svc.delete_database_handler(EVENT, {"databaseId": DATABASE_ID}, AUTHENTICATED)
        assert response["statusCode"] == status
        recorder.assert_not_called()


@pytest.mark.unit
class TestDatabaseReadRecords:
    def test_a_get_records_the_request(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "get_database", lambda *args: MagicMock(dict=lambda: {}))
        svc.get_database_handler(EVENT, {"databaseId": DATABASE_ID}, {"showDeleted": "true"}, AUTHENTICATED)
        recorder.assert_called_once_with(EVENT, "databaseGet", database_id=DATABASE_ID,
                                         request_parameters={"showDeleted": True})

    def test_a_get_of_a_missing_database_records_nothing(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "get_database", lambda *args: None)
        response = svc.get_database_handler(EVENT, {"databaseId": DATABASE_ID}, {}, AUTHENTICATED)
        assert response["statusCode"] == 404
        recorder.assert_not_called()

    def test_a_list_records_the_request_and_the_item_count_only(self, recorder, monkeypatch):
        listing = svc.GetDatabasesResponseModel(Items=[
            svc.GetDatabaseResponseModel(databaseId="a", description="secret description one"),
            svc.GetDatabaseResponseModel(databaseId="b", description="secret description two")])
        monkeypatch.setattr(svc, "get_databases", lambda *args: listing)
        monkeypatch.setattr(svc, "validate_pagination_info", lambda params: None)
        svc.get_databases_handler(EVENT, {"maxItems": "10"}, AUTHENTICATED)
        recorder.assert_called_once_with(EVENT, "databaseList", item_count=2,
                                         request_parameters={"maxItems": "10", "showDeleted": False})
        assert "secret description" not in json.dumps(recorder.call_args.kwargs, default=str)


@pytest.mark.unit
class TestDatabaseCreateRecord:
    def test_a_create_records_the_written_fields(self, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(create_svc, "log_action", recorder)
        monkeypatch.setattr(create_svc, "request_to_claims", lambda event: AUTHENTICATED)
        enforcer = MagicMock()
        enforcer.return_value.enforceAPI.return_value = True
        enforcer.return_value.enforce.return_value = True
        monkeypatch.setattr(create_svc, "CasbinEnforcer", enforcer)
        monkeypatch.setattr(create_svc, "create_database",
                            lambda model: create_svc.CreateDatabaseResponseModel(databaseId=model.databaseId,
                                                                                 message="ok"))
        body = {"databaseId": DATABASE_ID, "description": "a new database",
                "defaultBucketId": "00000000-0000-4000-8000-000000000000"}
        event = {"body": json.dumps(body),
                 "requestContext": {"http": {"method": "POST", "path": "/database"}}}
        response = create_svc.lambda_handler(event, None)
        assert response["statusCode"] == 200
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, "databaseCreate")
        assert recorder.call_args.kwargs["database_id"] == DATABASE_ID
        changes = recorder.call_args.kwargs["changes"]
        assert changes["description"] == "a new database" and changes["databaseId"] == DATABASE_ID

    def test_a_denied_create_records_nothing(self, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(create_svc, "log_action", recorder)
        monkeypatch.setattr(create_svc, "request_to_claims", lambda event: AUTHENTICATED)
        enforcer = MagicMock()
        enforcer.return_value.enforceAPI.return_value = True
        enforcer.return_value.enforce.return_value = False
        monkeypatch.setattr(create_svc, "CasbinEnforcer", enforcer)
        body = {"databaseId": DATABASE_ID, "description": "a new database",
                "defaultBucketId": "00000000-0000-4000-8000-000000000000"}
        event = {"body": json.dumps(body), "requestContext": {"http": {"method": "POST", "path": "/database"}}}
        assert create_svc.lambda_handler(event, None)["statusCode"] == 403
        recorder.assert_not_called()
