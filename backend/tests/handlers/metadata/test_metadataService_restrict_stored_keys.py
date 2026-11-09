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
the real `get_aggregated_schemas`. The asset-link update path applies the restriction with the same
stored-key rule as its create path (#1018), enforced when either linked database restricts.
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

    def __init__(self, existing_rows=(), schema=ONE_FIELD_SCHEMA):
        self.client = MagicMock()
        self.client.get_paginator.return_value = _paginator(existing_rows)
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}
        self.client.query.return_value = schema

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
    """Write items from bare keys (a string value "edited") or from ready-made items."""
    return [
        key if isinstance(key, MetadataItemModel)
        else MetadataItemModel(metadataKey=key, metadataValue="edited")
        for key in keys
    ]


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
    ("update-assetLink", _update_asset_link, "update", _PIPELINE_METADATA_ROW, True),
    ("replaceAll-assetLink", _update_asset_link, "replace_all", _PIPELINE_METADATA_ROW, True),
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


def _restricting_databases(*restricted_ids):
    """database_storage_table.get_item side effect: only the named databases restrict."""
    def get_item(Key):
        database_id = Key["databaseId"]
        return {"Item": {
            "databaseId": database_id,
            "restrictMetadataOutsideSchemas": database_id in restricted_ids,
        }}
    return get_item


def _link_between_two_databases(harness):
    harness.asset_links_table.get_item.return_value["Item"]["toAssetDatabaseId"] = "db2"


@pytest.mark.unit
@pytest.mark.parametrize("update_type", ["update", "replace_all"])
class TestAssetLinkUpdateRestriction:
    """PUT /asset-links/{id}/metadata applies the restriction the way POST does (#1018)."""

    @pytest.mark.parametrize("restricted", [("db1",), ("db2",)], ids=["from-db", "to-db"])
    def test_either_linked_database_restricting_refuses_a_new_undeclared_key(
            self, update_type, restricted):
        with _WriteHarness(existing_rows=(_PIPELINE_METADATA_ROW,)) as harness:
            _link_between_two_databases(harness)
            harness.database_table.get_item.side_effect = _restricting_databases(*restricted)
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                _update_asset_link(
                    _keys_for(update_type, [DECLARED_KEY, NEW_KEY]), USER_CLAIMS, update_type)

        message = str(raised.value)
        assert "Metadata key validation failed" in message
        assert f"Field '{NEW_KEY}' is not defined in the metadata schema" in message
        assert PIPELINE_KEY not in message
        assert not harness.wrote

    def test_no_restriction_on_either_database_admits_a_new_undeclared_key(self, update_type):
        with _WriteHarness(existing_rows=(_PIPELINE_METADATA_ROW,)) as harness:
            _link_between_two_databases(harness)
            harness.database_table.get_item.side_effect = _restricting_databases()
            response = _update_asset_link(
                _keys_for(update_type, [DECLARED_KEY, NEW_KEY]), USER_CLAIMS, update_type)

        assert response.success is True
        assert harness.wrote

    def test_no_asset_link_schema_admits_a_new_undeclared_key(self, update_type):
        with _WriteHarness(existing_rows=(), schema={"Items": []}) as harness:
            response = _update_asset_link([NEW_KEY], USER_CLAIMS, update_type)

        assert response.success is True
        assert harness.wrote


@pytest.mark.unit
@pytest.mark.parametrize(
    "path_name,invoke,update_type,stored_row,enforces", ENFORCING_PATHS, ids=_ENFORCING_IDS)
def test_a_pipeline_write_back_stays_exempt(path_name, invoke, update_type, stored_row, enforces):
    """SYSTEM_USER write-backs can still add keys outside the schema."""
    with _WriteHarness(existing_rows=()) as harness:
        response = invoke([NEW_KEY], PIPELINE_CLAIMS, update_type)

    assert response.success is True, f"{path_name}: {response}"
    assert harness.wrote


# --- Value and type checks look only at the keys a user write sets (#968) ---------------------
#
# A pipeline write-back skips schema validation, so it can store a value the schema refuses: the
# wrong type for a declared field, or a value outside a controlled list. The user write paths
# checked value and type against the MERGED record, so that stored value refused every later user
# write on the record. Value and type are now checked only for the keys the write sets or changes
# (plus schema defaults it adds); required-field and dependsOn checks still read the merged record.

TYPED_KEY = "count"
LIST_KEY = "colour"

VALUE_SCHEMA = {
    "Items": [
        {
            "metadataSchemaId": {"S": "schema2"},
            "databaseId": {"S": "db1"},
            "schemaName": {"S": "Schema Two"},
            "enabled": {"BOOL": True},
            "fields": {"L": [
                {"M": {
                    "metadataFieldKeyName": {"S": DECLARED_KEY},
                    "metadataFieldName": {"S": DECLARED_KEY},
                    "metadataFieldValueType": {"S": "string"},
                    "required": {"BOOL": False},
                    "sequence": {"N": "1"},
                }},
                {"M": {
                    "metadataFieldKeyName": {"S": TYPED_KEY},
                    "metadataFieldName": {"S": TYPED_KEY},
                    "metadataFieldValueType": {"S": "number"},
                    "required": {"BOOL": False},
                    "sequence": {"N": "2"},
                }},
                {"M": {
                    "metadataFieldKeyName": {"S": LIST_KEY},
                    "metadataFieldName": {"S": LIST_KEY},
                    "metadataFieldValueType": {"S": "inline_controlled_list"},
                    "required": {"BOOL": False},
                    "sequence": {"N": "3"},
                    "controlledListKeys": {"L": [{"S": "red"}, {"S": "blue"}]},
                }},
            ]},
        }
    ]
}

# What the pipeline stored: text in the number field, and (metadata tables only, since file
# attributes accept only string values from a user write) a value outside the controlled list.
_STORED_TYPED = ("not-a-number", "string")
_STORED_LIST = ("green", "inline_controlled_list")


def _stored_rows(is_attribute):
    def row(key, value, value_type):
        prefix = "attribute" if is_attribute else "metadata"
        return {
            f"{prefix}Key": {"S": key},
            f"{prefix}Value": {"S": value},
            f"{prefix}ValueType": {"S": value_type},
        }
    rows = [row(TYPED_KEY, *_STORED_TYPED)]
    if not is_attribute:
        rows.append(row(LIST_KEY, *_STORED_LIST))
    return tuple(rows)


def _item(key, value, value_type="string"):
    return MetadataItemModel(metadataKey=key, metadataValue=value, metadataValueType=value_type)


def _unchanged_stored_items(is_attribute):
    """A replace-all write lists every key the record keeps: the stored values, sent back as read."""
    items = [_item(TYPED_KEY, *_STORED_TYPED)]
    if not is_attribute:
        items.append(_item(LIST_KEY, *_STORED_LIST))
    return items


def _is_attribute(path_name):
    return path_name.endswith("fileAttribute")


_METADATA_PATHS = [entry for entry in WRITE_PATHS if not _is_attribute(entry[0])]
_METADATA_IDS = [entry[0] for entry in _METADATA_PATHS]

# Paths that compared an incoming value type against the stored one before the write ("Cannot
# change type"): every create, and every replace-all except the asset link's, which passed no
# stored state. A user correcting the stored value to the declared type was refused there.
_TYPE_CHANGE_PATHS = [
    entry for entry in _METADATA_PATHS
    if entry[2] != "update" and entry[0] != "replaceAll-assetLink"
]
_TYPE_CHANGE_IDS = [entry[0] for entry in _TYPE_CHANGE_PATHS]


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,update_type,stored_row,enforces", WRITE_PATHS, ids=_IDS)
def test_a_stored_value_the_schema_refuses_does_not_block_editing_another_field(
        path_name, invoke, update_type, stored_row, enforces):
    is_attribute = _is_attribute(path_name)
    items = [_item(DECLARED_KEY, "edited")]
    if update_type == "replace_all":
        items += _unchanged_stored_items(is_attribute)

    with _WriteHarness(existing_rows=_stored_rows(is_attribute), schema=VALUE_SCHEMA) as harness:
        response = invoke(items, USER_CLAIMS, update_type)

    assert response.success is True, f"{path_name}: {response}"
    assert harness.wrote, f"{path_name} reported success without writing"
    assert harness.client.query.called, "the real schema lookup was never reached"


@pytest.mark.unit
@pytest.mark.parametrize(
    "path_name,invoke,update_type,stored_row,enforces", _TYPE_CHANGE_PATHS, ids=_TYPE_CHANGE_IDS)
def test_correcting_a_stored_value_to_the_declared_type_succeeds(
        path_name, invoke, update_type, stored_row, enforces):
    items = [_item(TYPED_KEY, "7", "number")]
    if update_type == "replace_all":
        items.append(_item(LIST_KEY, *_STORED_LIST))

    with _WriteHarness(existing_rows=_stored_rows(False), schema=VALUE_SCHEMA) as harness:
        response = invoke(items, USER_CLAIMS, update_type)

    assert response.success is True, f"{path_name}: {response}"
    assert harness.wrote


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,update_type,stored_row,enforces", WRITE_PATHS, ids=_IDS)
def test_a_user_write_of_the_wrong_type_for_a_declared_field_is_still_refused(
        path_name, invoke, update_type, stored_row, enforces):
    """Control: the incoming value is still type-checked against the schema."""
    with _WriteHarness(existing_rows=(), schema=VALUE_SCHEMA) as harness:
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            invoke([_item(TYPED_KEY, "still-text", "string")], USER_CLAIMS, update_type)

    message = str(raised.value)
    assert "Schema validation failed" in message, f"{path_name}: {message}"
    assert f"Field '{TYPED_KEY}' has incorrect type" in message, f"{path_name}: {message}"
    assert not harness.wrote


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,update_type,stored_row,enforces", WRITE_PATHS, ids=_IDS)
def test_the_type_error_names_the_plain_value_types(
        path_name, invoke, update_type, stored_row, enforces):
    """The refusal names the types as the API spells them, not the Python enum."""
    with _WriteHarness(existing_rows=(), schema=VALUE_SCHEMA) as harness:
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            invoke([_item(TYPED_KEY, "still-text", "string")], USER_CLAIMS, update_type)

    message = str(raised.value)
    assert (f"Field '{TYPED_KEY}' has incorrect type. Expected 'number', got 'string'"
            in message), f"{path_name}: {message}"
    assert "MetadataValueType" not in message, f"{path_name}: {message}"
    assert not harness.wrote


@pytest.mark.unit
@pytest.mark.parametrize(
    "path_name,invoke,update_type,stored_row,enforces", _METADATA_PATHS, ids=_METADATA_IDS)
def test_changing_a_stored_value_to_one_the_schema_refuses_is_still_refused(
        path_name, invoke, update_type, stored_row, enforces):
    """Control: a stored key is value-checked again as soon as the write changes it."""
    items = [_item(LIST_KEY, "purple", "inline_controlled_list")]
    if update_type == "replace_all":
        items.append(_item(TYPED_KEY, *_STORED_TYPED))

    with _WriteHarness(existing_rows=_stored_rows(False), schema=VALUE_SCHEMA) as harness:
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            invoke(items, USER_CLAIMS, update_type)

    message = str(raised.value)
    assert "'purple' is not in controlled list" in message, f"{path_name}: {message}"
    assert TYPED_KEY not in message, f"{path_name} checked the unchanged stored key: {message}"
    assert not harness.wrote


@pytest.mark.unit
@pytest.mark.parametrize(
    "path_name,invoke,update_type,stored_row,enforces", _METADATA_PATHS, ids=_METADATA_IDS)
def test_a_required_field_is_still_checked_on_the_merged_record(
        path_name, invoke, update_type, stored_row, enforces):
    """Control: required-field checks still read the whole record, not only the incoming keys."""
    schema = {"Items": [dict(VALUE_SCHEMA["Items"][0])]}
    fields = [dict(field["M"]) for field in VALUE_SCHEMA["Items"][0]["fields"]["L"]]
    fields[0]["required"] = {"BOOL": True}
    schema["Items"][0]["fields"] = {"L": [{"M": field} for field in fields]}

    with _WriteHarness(existing_rows=(), schema=schema) as harness:
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            invoke([_item(LIST_KEY, "red", "inline_controlled_list")], USER_CLAIMS, update_type)

    assert f"Required field '{DECLARED_KEY}' is missing" in str(raised.value), path_name
    assert not harness.wrote
