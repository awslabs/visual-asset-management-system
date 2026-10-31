# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The per-entity metadata record limit fails closed when its count query fails.

Each of the four metadata create paths counts the entity's existing records before writing, to
hold MAX_METADATA_RECORDS_PER_ENTITY. When that count fails or is throttled, the create writes
nothing and answers 503, for every caller, and the POST handlers carry that status to the client.
"""

import contextlib
import json

import pytest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import (
    RECORD_LIMIT_CHECK_FAILED_MESSAGE,
    VAMSGeneralErrorResponse,
)
from backend.backend.models.metadata import (
    CreateAssetLinkMetadataRequestModel,
    CreateAssetMetadataRequestModel,
    CreateDatabaseMetadataRequestModel,
    CreateFileMetadataRequestModel,
    MetadataItemModel,
)

THROTTLE = ClientError(
    {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttled"}},
    "Query",
)


def _items():
    return [MetadataItemModel(metadataKey="k", metadataValue="v")]


@contextlib.contextmanager
def _failing_count():
    """Every module global a create touches before its record count, with the count failing."""
    paginator = MagicMock()
    paginator.paginate.side_effect = THROTTLE
    client = MagicMock()
    client.get_paginator.return_value = paginator

    asset_table = MagicMock()
    asset_table.get_item.return_value = {
        "Item": {"databaseId": "db1", "assetId": "asset1", "assetName": "A", "tags": []}
    }
    database_table = MagicMock()
    database_table.get_item.return_value = {"Item": {"databaseId": "db1"}}
    asset_links_table = MagicMock()
    asset_links_table.get_item.return_value = {
        "Item": {
            "assetLinkId": "link1",
            "fromAssetDatabaseId": "db1", "fromAssetId": "asset1",
            "toAssetDatabaseId": "db1", "toAssetId": "asset2",
        }
    }
    enforcer = MagicMock()
    enforcer.enforce.return_value = True

    with contextlib.ExitStack() as stack:
        for target, replacement in (
            ("dynamodb_client", client),
            ("asset_storage_table", asset_table),
            ("database_storage_table", database_table),
            ("asset_links_table", asset_links_table),
            ("asset_links_metadata_table", MagicMock()),
            ("asset_file_metadata_table", MagicMock()),
            ("file_attribute_table", MagicMock()),
            ("database_metadata_table", MagicMock()),
            ("CasbinEnforcer", MagicMock(return_value=enforcer)),
            ("validate_file_exists", MagicMock(return_value=True)),
        ):
            stack.enter_context(patch.object(metadataService, target, replacement))
        yield client


CREATES = [
    pytest.param(
        lambda claims: metadataService.create_asset_link_metadata(
            "link1", CreateAssetLinkMetadataRequestModel(metadata=_items()), claims),
        id="asset link"),
    pytest.param(
        lambda claims: metadataService.create_asset_metadata(
            "db1", "asset1", CreateAssetMetadataRequestModel(metadata=_items()), claims),
        id="asset"),
    pytest.param(
        lambda claims: metadataService.create_file_metadata(
            "db1", "asset1",
            CreateFileMetadataRequestModel(filePath="/folder/file.txt", type="metadata",
                                           metadata=_items()),
            claims),
        id="file"),
    pytest.param(
        lambda claims: metadataService.create_database_metadata(
            "db1", CreateDatabaseMetadataRequestModel(metadata=_items()), claims),
        id="database"),
]


@pytest.mark.unit
class TestRecordLimitCheckFailsClosed:
    @pytest.mark.parametrize("caller", ["user1", "SYSTEM_USER"])
    @pytest.mark.parametrize("create", CREATES)
    def test_failed_count_writes_nothing_and_answers_503(self, create, caller):
        with _failing_count() as client:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                create({"tokens": [caller]})

        assert raised.value.status_code == 503
        assert RECORD_LIMIT_CHECK_FAILED_MESSAGE in str(raised.value)
        client.batch_write_item.assert_not_called()
        client.put_item.assert_not_called()

    @pytest.mark.parametrize("handler,create_name,path_parameters", [
        ("handle_asset_link_metadata_post", "create_asset_link_metadata",
         {"assetLinkId": "link1"}),
        ("handle_asset_metadata_post", "create_asset_metadata",
         {"databaseId": "db1", "assetId": "asset1"}),
        ("handle_file_metadata_post", "create_file_metadata",
         {"databaseId": "db1", "assetId": "asset1"}),
        ("handle_database_metadata_post", "create_database_metadata",
         {"databaseId": "db1"}),
    ])
    def test_post_handlers_carry_the_503(self, handler, create_name, path_parameters):
        body = {"metadata": [{"metadataKey": "k", "metadataValue": "v"}]}
        if create_name == "create_file_metadata":
            body.update({"filePath": "/folder/file.txt", "type": "metadata"})
        event = {"pathParameters": path_parameters, "body": json.dumps(body)}
        failure = VAMSGeneralErrorResponse(RECORD_LIMIT_CHECK_FAILED_MESSAGE, status_code=503)

        with patch.object(metadataService, create_name, side_effect=failure):
            response = getattr(metadataService, handler)(event)

        assert response["statusCode"] == 503
        assert RECORD_LIMIT_CHECK_FAILED_MESSAGE in json.loads(response["body"])["message"]

    def test_other_create_errors_keep_their_400(self):
        event = {
            "pathParameters": {"databaseId": "db1", "assetId": "asset1"},
            "body": json.dumps({"metadata": [{"metadataKey": "k", "metadataValue": "v"}]}),
        }
        with patch.object(metadataService, "create_asset_metadata",
                          side_effect=VAMSGeneralErrorResponse("Maximum 500 metadata records")):
            response = metadataService.handle_asset_metadata_post(event)

        assert response["statusCode"] == 400
