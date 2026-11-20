# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The deploy-time readiness marker is written only for a new, empty asset table, on Create only, and
never over an existing marker. The marker it writes is one index_ready() accepts."""

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing",
                     "crAssetLocationIndexMarker.py")

TABLE = "vams-AssetStorageTable"
PARAM = "/vams/resourceNames/dataMigrations/assetLocationIndex"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _load():
    with patch("boto3.client", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location("crAssetLocationIndexMarker_under_test", os.path.abspath(_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module._now = lambda: NOW
    module.dynamodb_client = MagicMock()
    module.ssm_client = MagicMock()
    return module


def _create(m, request_type="Create"):
    return m.lambda_handler({"RequestType": request_type, "PhysicalResourceId": "pid",
                             "ResourceProperties": {"assetTableName": TABLE, "markerParameterName": PARAM,
                                                    "newTableWindowSeconds": "3600"}}, MagicMock())


def _table(m, created, items=()):
    m.dynamodb_client.describe_table.return_value = {"Table": {"CreationDateTime": created}}
    m.dynamodb_client.scan.return_value = {"Items": list(items)}


@pytest.mark.unit
class TestReadinessMarker:
    def test_a_new_empty_table_gets_the_marker_index_ready_accepts(self):
        m = _load()
        _table(m, NOW - timedelta(minutes=5))
        _create(m)
        kwargs = m.ssm_client.put_parameter.call_args.kwargs
        assert (kwargs["Name"], kwargs["Type"], kwargs["Overwrite"]) == (PARAM, "String", False)
        marker = json.loads(kwargs["Value"])
        assert marker == {"version": 1, "status": "complete", "assetTable": TABLE,
                          "completedAt": NOW.isoformat(), "assetRows": 0, "indexed": 0, "notIndexed": 0,
                          "writtenBy": "deploy"}
        assert sys.modules["common.assetLocations"].marker_says_complete(kwargs["Value"], TABLE) is True
        assert m.dynamodb_client.scan.call_args.kwargs["Limit"] == 1

    @pytest.mark.parametrize("created,items", [
        (NOW - timedelta(minutes=5), [{"databaseId": {"S": "db1"}}]),   # holds a row
        (NOW - timedelta(days=30), []),                                  # predates this deployment
        (None, []),                                                      # no creation time
    ])
    def test_no_marker_for_a_table_with_a_row_or_an_older_table(self, created, items):
        m = _load()
        _table(m, created, items)
        assert _create(m) == {"PhysicalResourceId": "pid"}
        m.ssm_client.put_parameter.assert_not_called()

    def test_an_existing_marker_is_left_untouched(self):
        m = _load()
        _table(m, NOW - timedelta(minutes=5))
        m.ssm_client.put_parameter.side_effect = ClientError(
            {"Error": {"Code": "ParameterAlreadyExists", "Message": "exists"}}, "PutParameter")
        assert _create(m) == {"PhysicalResourceId": "pid"}
        assert m.ssm_client.put_parameter.call_args.kwargs["Overwrite"] is False

    @pytest.mark.parametrize("request_type", ["Update", "Delete"])
    def test_update_and_delete_do_nothing(self, request_type):
        m = _load()
        _table(m, NOW - timedelta(minutes=5))
        assert _create(m, request_type) == {"PhysicalResourceId": "pid"}
        m.dynamodb_client.describe_table.assert_not_called()
        m.ssm_client.put_parameter.assert_not_called()

    def test_a_failure_never_fails_the_deployment(self):
        m = _load()
        m.dynamodb_client.describe_table.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "DescribeTable")
        assert _create(m) == {"PhysicalResourceId": "pid"}
        m.ssm_client.put_parameter.assert_not_called()
