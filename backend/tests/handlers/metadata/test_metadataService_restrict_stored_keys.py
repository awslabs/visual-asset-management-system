# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""restrictMetadataOutsideSchemas refuses only a key that is new to the record (#968).

A pipeline write-back (SYSTEM_USER) skips the restriction and can store a key the schema does not
declare. The user and API write paths checked the restriction against the MERGED record, so that
stored key refused every later user write on the record, including an edit of a declared field.

The rule now: a key already stored on the record passes the off-schema key check, including when
its value changes, whoever wrote it first. A key that is new to the record and not declared by the
schema is still refused. Pipeline write-backs stay exempt.

Every enforcing write entry point is driven (create and update, for asset-link, asset, file
metadata, file attribute and database metadata, plus the replace-all form of each update), through
the real `get_aggregated_schemas`. The asset-link update path does not apply the key restriction at
all; it is driven only where that does not matter (declared-field and stored-key edits).
"""

import contextlib
import sys

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import VAMSGeneralErrorResponse
from backend.backend.models.metadata import (
    CreateAssetLinkMetadataRequestModel,
    CreateAssetMetadataRequestModel,
    CreateDatabaseMetadataRequestModel,
    CreateFileMetadataRequestModel,
    MetadataItemModel,
    UpdateAssetLinkMetadataRequestModel,
    UpdateAssetMetadataRequestModel,
    UpdateDatabaseMetadataRequestModel,
    UpdateFileMetadataRequestModel,
)

USER_CLAIMS = {"tokens": ["user1"]}
PIPELINE_CLAIMS = {"tokens": ["SYSTEM_USER"]}

DECLARED_KEY = "declared"
PIPELINE_KEY = "pipelineAddedKey"
NEW_KEY = "brandNewKey"

# The undeclared row a pipeline wrote back, as each table stores it.
_PIPELINE_METADATA_ROW = {
    "metadataKey": {"S": PIPELINE_KEY},
    "metadataValue": {"S": "from-pipeline"},
    "metadataValueType": {"S": "string"},
}
_PIPELINE_ATTRIBUTE_ROW = {
    "attributeKey": {"S": PIPELINE_KEY},
    "attributeValue": {"S": "from-pipeline"},
    "attributeValueType": {"S": "string"},
}

# A one-field schema: only DECLARED_KEY is defined.
ONE_FIELD_SCHEMA = {
    "Items": [
        {
            "metadataSchemaId": {"S": "schema1"},
            "databaseId": {"S": "db1"},
            "schemaName": {"S": "Schema One"},
            "enabled": {"BOOL": True},
            "fields": {"L": [{
                "M": {
                    "metadataFieldKeyName": {"S": DECLARED_KEY},
                    "metadataFieldName": {"S": DECLARED_KEY},
                    "metadataFieldValueType": {"S": "string"},
                    "required": {"BOOL": False},
                    "sequence": {"N": "1"},
                }
            }]},
        }
    ]
}


def _paginator(items):
    paginator = MagicMock()
    page_iterator = MagicMock()
    page_iterator.build_full_result.return_value = {"Items": list(items)}
    paginator.paginate.return_value = page_iterator
    return paginator


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    modules = [
        sys.modules.get("common.metadataSchemaValidation"),
        sys.modules.get("backend.backend.common.metadataSchemaValidation"),
    ]
    for module in modules:
        if module is not None:
            module._schema_cache.clear()
    yield
    for module in modules:
        if module is not None:
            module._schema_cache.clear()


class _WriteHarness:
    """Module globals a metadata write touches, on a database with the restriction enabled."""

    def __init__(self, existing_rows=()):
        self.client = MagicMock()
        self.client.get_paginator.return_value = _paginator(existing_rows)
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}
        self.client.query.return_value = ONE_FIELD_SCHEMA

        self.asset_table = MagicMock()
        self.asset_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "asset1", "assetName": "A", "tags": []}
        }
        self.database_table = MagicMock()
        self.database_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "restrictMetadataOutsideSchemas": True}
        }
        self.asset_links_table = MagicMock()
        self.asset_links_table.get_item.return_value = {
            "Item": {
                "assetLinkId": "link1",
                "fromAssetDatabaseId": "db1", "fromAssetId": "asset1",
                "toAssetDatabaseId": "db1", "toAssetId": "asset2",
            }
        }
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        self.enforcer_cls = MagicMock(return_value=enforcer)
        self._stack = contextlib.ExitStack()

    def __enter__(self):
        for target, replacement in (
            ("dynamodb_client", self.client),
            ("asset_storage_table", self.asset_table),
            ("database_storage_table", self.database_table),
            ("asset_links_table", self.asset_links_table),
            ("asset_links_metadata_table", MagicMock()),
            ("asset_file_metadata_table", MagicMock()),
            ("file_attribute_table", MagicMock()),
            ("database_metadata_table", MagicMock()),
            ("CasbinEnforcer", self.enforcer_cls),
            ("validate_file_exists", MagicMock(return_value=True)),
        ):
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False

    @property
    def wrote(self):
        return self.client.batch_write_item.call_count > 0


def _items(keys):
    return [MetadataItemModel(metadataKey=key, metadataValue="edited") for key in keys]


def _create_asset_link(keys, claims, update_type=None):
    return metadataService.create_asset_link_metadata(
        "link1", CreateAssetLinkMetadataRequestModel(metadata=_items(keys)), claims)


def _update_asset_link(keys, claims, update_type="update"):
    return metadataService.update_asset_link_metadata(
        "link1",
        UpdateAssetLinkMetadataRequestModel(metadata=_items(keys), updateType=update_type), claims)


def _create_asset(keys, claims, update_type=None):
    return metadataService.create_asset_metadata(
        "db1", "asset1", CreateAssetMetadataRequestModel(metadata=_items(keys)), claims)


def _update_asset(keys, claims, update_type="update"):
    return metadataService.update_asset_metadata(
        "db1", "asset1",
        UpdateAssetMetadataRequestModel(metadata=_items(keys), updateType=update_type), claims)


def _create_file(file_type):
    def invoke(keys, claims, update_type=None):
        return metadataService.create_file_metadata(
            "db1", "asset1",
            CreateFileMetadataRequestModel(
                filePath="/folder/file.txt", type=file_type, metadata=_items(keys)),
            claims)
    return invoke


def _update_file(file_type):
    def invoke(keys, claims, update_type="update"):
        return metadataService.update_file_metadata(
            "db1", "asset1",
            UpdateFileMetadataRequestModel(
                filePath="/folder/file.txt", type=file_type, metadata=_items(keys),
                updateType=update_type),
            claims)
    return invoke


def _create_database(keys, claims, update_type=None):
    return metadataService.create_database_metadata(
        "db1", CreateDatabaseMetadataRequestModel(metadata=_items(keys)), claims)


def _update_database(keys, claims, update_type="update"):
    return metadataService.update_database_metadata(
        "db1",
        UpdateDatabaseMetadataRequestModel(metadata=_items(keys), updateType=update_type), claims)


# (id, entry point, update type, stored pipeline row, enforces the key restriction)
WRITE_PATHS = [
    ("create-assetLink", _create_asset_link, None, _PIPELINE_METADATA_ROW, True),
    ("update-assetLink", _update_asset_link, "update", _PIPELINE_METADATA_ROW, False),
    ("replaceAll-assetLink", _update_asset_link, "replace_all", _PIPELINE_METADATA_ROW, False),
    ("create-asset", _create_asset, None, _PIPELINE_METADATA_ROW, True),
    ("update-asset", _update_asset, "update", _PIPELINE_METADATA_ROW, True),
    ("replaceAll-asset", _update_asset, "replace_all", _PIPELINE_METADATA_ROW, True),
    ("create-fileMetadata", _create_file("metadata"), None, _PIPELINE_METADATA_ROW, True),
    ("update-fileMetadata", _update_file("metadata"), "update", _PIPELINE_METADATA_ROW, True),
    ("replaceAll-fileMetadata", _update_file("metadata"), "replace_all",
     _PIPELINE_METADATA_ROW, True),
    ("create-fileAttribute", _create_file("attribute"), None, _PIPELINE_ATTRIBUTE_ROW, True),
    ("update-fileAttribute", _update_file("attribute"), "update", _PIPELINE_ATTRIBUTE_ROW, True),
    ("replaceAll-fileAttribute", _update_file("attribute"), "replace_all",
     _PIPELINE_ATTRIBUTE_ROW, True),
    ("create-database", _create_database, None, _PIPELINE_METADATA_ROW, True),
    ("update-database", _update_database, "update", _PIPELINE_METADATA_ROW, True),
    ("replaceAll-database", _update_database, "replace_all", _PIPELINE_METADATA_ROW, True),
]
_IDS = [entry[0] for entry in WRITE_PATHS]
ENFORCING_PATHS = [entry for entry in WRITE_PATHS if entry[4]]
_ENFORCING_IDS = [entry[0] for entry in ENFORCING_PATHS]


def _keys_for(update_type, keys):
    """A replace-all write lists every key the record keeps, so it carries the stored key too."""
    if update_type == "replace_all" and PIPELINE_KEY not in keys:
        return [PIPELINE_KEY, *keys]
    return list(keys)


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,update_type,stored_row,enforces", WRITE_PATHS, ids=_IDS)
class TestAStoredUndeclaredKeyDoesNotBlockUserWrites:
    def test_editing_a_declared_field_succeeds(
            self, path_name, invoke, update_type, stored_row, enforces):
        """The defect: the pipeline's key refused every later user write on the record."""
        with _WriteHarness(existing_rows=(stored_row,)) as harness:
            response = invoke(_keys_for(update_type, [DECLARED_KEY]), USER_CLAIMS, update_type)

        assert response.success is True, f"{path_name}: {response}"
        assert harness.wrote, f"{path_name} reported success without writing"
        assert harness.client.query.called, "the real schema lookup was never reached"

    def test_editing_the_stored_undeclared_key_succeeds(
            self, path_name, invoke, update_type, stored_row, enforces):
        with _WriteHarness(existing_rows=(stored_row,)) as harness:
            response = invoke(_keys_for(update_type, [PIPELINE_KEY]), USER_CLAIMS, update_type)

        assert response.success is True, f"{path_name}: {response}"
        assert harness.wrote


@pytest.mark.unit
@pytest.mark.parametrize(
    "path_name,invoke,update_type,stored_row,enforces", ENFORCING_PATHS, ids=_ENFORCING_IDS)
class TestANewUndeclaredKeyIsStillRefused:
    def test_a_new_undeclared_key_is_refused_and_named(
            self, path_name, invoke, update_type, stored_row, enforces):
        with _WriteHarness(existing_rows=(stored_row,)) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                invoke(_keys_for(update_type, [DECLARED_KEY, NEW_KEY]), USER_CLAIMS, update_type)

        message = str(raised.value)
        assert "Metadata key validation failed" in message, f"{path_name}: {message}"
        assert NEW_KEY in message, f"{path_name} did not name the refused key: {message}"
        assert PIPELINE_KEY not in message, (
            f"{path_name} still refused the stored key: {message}")
        assert not harness.wrote

    def test_a_new_undeclared_key_is_refused_on_an_empty_record(
            self, path_name, invoke, update_type, stored_row, enforces):
        """Control: stored keys pass, not every key -- an empty record admits nothing undeclared."""
        with _WriteHarness(existing_rows=()) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                invoke([NEW_KEY], USER_CLAIMS, update_type)

        assert NEW_KEY in str(raised.value)
        assert not harness.wrote


@pytest.mark.unit
@pytest.mark.parametrize(
    "path_name,invoke,update_type,stored_row,enforces", ENFORCING_PATHS, ids=_ENFORCING_IDS)
def test_a_pipeline_write_back_stays_exempt(path_name, invoke, update_type, stored_row, enforces):
    """SYSTEM_USER write-backs can still add keys outside the schema."""
    with _WriteHarness(existing_rows=()) as harness:
        response = invoke([NEW_KEY], PIPELINE_CLAIMS, update_type)

    assert response.success is True, f"{path_name}: {response}"
    assert harness.wrote
