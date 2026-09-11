# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The record a metadata GET returns must be writable back, or the repair path does not exist.

A stored metadata record can carry no `metadataValue` and no `metadataValueType`. Two properties
were established separately: the write path no longer refuses the whole entity because of such a
record (test_metadataService_legacy_row_write_path.py), and the read path reports it with those
attributes null instead of answering 400 for the entity
(test_metadataService_legacy_row_read_path.py). Neither one is the property an operator needs.
The clients all round-trip a GET response straight back into the write body -- the web metadata
editor, `vamscli metadata <entity> update --json-input`, the VamsMCP metadata tools calling the
same APIClient methods, and the two external connectors that shell out to the CLI -- and the
write model refused an explicit null. So the operator could SEE the record and still had no
request body that would repair it: opening the editor and saving returned a validation error on a
field they never touched.

## What is asserted here, and why it is a different test

`TestTheGetResponseIsWritableBack` runs the whole sequence in one harness: it stores the record,
calls the real GET, takes the GET's OWN `.dict()` output, and hands that list to the real write
entry point. A test that only inspects the GET's shape, or only feeds a hand-built body to the
write, passes on a tree where the two halves disagree -- which is exactly the state that shipped.

* The repair is asserted on the ITEM DYNAMODB WAS HANDED, not on the response object. "Accepted"
  alone is also satisfied by a write that reported success and stored nothing, or that stored the
  record still missing its type. `written_items` reads them back out of `batch_write_item`.
* Both write modes for every entity type. Create and update are separate copies of the same
  block, and the file family has a second mode against a different table under the attribute*
  names -- whose write additionally checks `item.metadataValueType` against `string`, which a
  None would either reject or crash on.
* `test_a_complete_record_is_writable_back_too` is the positive control. Without it the
  assertions above could pass on a harness that never reached the write at all.

## FIX-061: the tolerant read must not become a tolerant validation

FIX-061 (S2-BACKEND-119) records the owner's ruling that retroactive enforcement of a newly
required schema field is INTENDED -- existing records are not grandfathered, and
`defaultMetadataFieldValue` is not required. `TestRetroactiveEnforcementSurvivesTheRoundTrip` is
the counter-test: a record that carries no value for a schema-REQUIRED field is returned by the
GET like any other, and feeding that response back is still refused, because the absent value
resolves to the empty string and `is_empty_value("")` is True. Its positive control drives the
same required field carrying a value, so the refusal is attributable to the empty value rather
than to "a required field always refuses".

The aggregate cache is a module global on `common.metadataSchemaValidation`, which this
directory's conftest loads as a SEPARATE module object from
`backend.backend.common.metadataSchemaValidation` -- clearing only one leaves the other answering
the next test's query.
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
    UpdateAssetLinkMetadataRequestModel,
    UpdateAssetMetadataRequestModel,
    UpdateDatabaseMetadataRequestModel,
    UpdateFileMetadataRequestModel,
)

CLAIMS = {"tokens": ["user1"]}

FILE_PATH = "/folder/file.txt"

# The record as a deployment predating the attribute holds it: a value, and no metadataValueType.
LEGACY_ROW = {
    "metadataKey": {"S": "legacyKey"},
    "metadataValue": {"S": "legacy value"},
}
WELL_FORMED_ROW = {
    "metadataKey": {"S": "legacyKey"},
    "metadataValue": {"S": "legacy value"},
    "metadataValueType": {"S": "string"},
}

# The same pair on a file-ATTRIBUTE table, which stores the attribute* names.
LEGACY_ATTRIBUTE_ROW = {
    "attributeKey": {"S": "legacyKey"},
    "attributeValue": {"S": "legacy value"},
}
WELL_FORMED_ATTRIBUTE_ROW = {
    "attributeKey": {"S": "legacyKey"},
    "attributeValue": {"S": "legacy value"},
    "attributeValueType": {"S": "string"},
}

# The retroactive-enforcement pair: a schema-required field whose record holds no value at all,
# and the same field carrying one.
VALUELESS_REQUIRED_ROW = {"metadataKey": {"S": "requiredField"}}
VALUED_REQUIRED_ROW = {
    "metadataKey": {"S": "requiredField"},
    "metadataValue": {"S": "supplied"},
    "metadataValueType": {"S": "string"},
}
VALUELESS_REQUIRED_ATTRIBUTE_ROW = {"attributeKey": {"S": "requiredField"}}
VALUED_REQUIRED_ATTRIBUTE_ROW = {
    "attributeKey": {"S": "requiredField"},
    "attributeValue": {"S": "supplied"},
    "attributeValueType": {"S": "string"},
}


def _field(field_name, required=False):
    """One typed schema field definition."""
    return {
        "M": {
            "metadataFieldKeyName": {"S": field_name},
            "metadataFieldName": {"S": field_name},
            "metadataFieldValueType": {"S": "string"},
            "required": {"BOOL": required},
            "sequence": {"N": "1"},
        }
    }


def _schema_page(*fields):
    """One completed schema query response declaring the given fields."""
    return {
        "Items": [
            {
                "metadataSchemaId": {"S": "schema1"},
                "databaseId": {"S": "db1"},
                "schemaName": {"S": "Schema One"},
                "enabled": {"BOOL": True},
                "fields": {"L": list(fields)},
            }
        ]
    }


# The stored key is declared, so restrictMetadataOutsideSchemas (enabled on the harness database
# row) has no reason of its own to refuse the record on the way back in. No field is required, so
# the schema fields the GET injects with an empty value are writable back as well -- the editor
# returns the whole list, injected rows included.
DECLARED_SCHEMA = _schema_page(_field("legacyKey"))

# The same, plus a field the schema requires.
REQUIRED_FIELD_SCHEMA = _schema_page(_field("legacyKey"), _field("requiredField", required=True))


def _paginator(items):
    paginator = MagicMock()
    page_iterator = MagicMock()
    page_iterator.build_full_result.return_value = {"Items": list(items)}
    paginator.paginate.return_value = page_iterator
    return paginator


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """Clear the aggregate cache on BOTH module objects that expose it (see the module docstring)."""
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


class _RoundTripHarness:
    """One store, serving both requests of the sequence.

    A single harness for the GET and the following write is the point: the write reads the same
    stored rows the GET reported, through the same client, and answers the same real
    `get_aggregated_schemas` (this directory's conftest loads the real
    `common.metadataSchemaValidation`).
    """

    def __init__(self, stored_rows=(), query_return=DECLARED_SCHEMA):
        self.client = MagicMock()
        self.client.get_paginator.return_value = _paginator(stored_rows)
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}
        # A return_value rather than a list: the real lookup issues one query per database in
        # scope (the entity's database plus GLOBAL) and pinning the count would make this a test
        # of how many databases the handler aggregates.
        self.client.query.return_value = query_return

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
            # The file paths check S3 for the file; the sequence under test is downstream of it.
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

    def written_items(self):
        """Every item handed to batch_write_item as a PutRequest, keyed by metadata key.

        The item is read back out of the call rather than inferred from the response, because a
        write that reported success and stored nothing satisfies "the write was accepted".
        """
        by_key = {}
        for call in self.client.batch_write_item.call_args_list:
            request_items = call.kwargs.get("RequestItems") or call.args[0]
            for requests in request_items.values():
                for request in requests:
                    item = (request.get("PutRequest") or {}).get("Item")
                    if not item:
                        continue
                    key_attr = item.get("metadataKey") or item.get("attributeKey")
                    by_key[key_attr["S"]] = item
        return by_key


def _stored_value_type(item):
    """The value type attribute of a written item, under whichever name its table uses."""
    attr = item.get("metadataValueType") or item.get("attributeValueType")
    return attr["S"] if attr else None


# ------------------------------------------------------------------------------------------
# The five GETs, and the create/update write for each, driven with the GET's own output.
# ------------------------------------------------------------------------------------------
def _get_asset_link_metadata():
    return metadataService.get_asset_link_metadata("link1", {}, CLAIMS)


def _get_asset_metadata():
    return metadataService.get_asset_metadata("db1", "asset1", {}, CLAIMS)


def _get_file_metadata():
    return metadataService.get_file_metadata(
        "db1", "asset1", FILE_PATH, "metadata", {}, CLAIMS)


def _get_file_attributes():
    return metadataService.get_file_metadata(
        "db1", "asset1", FILE_PATH, "attribute", {}, CLAIMS)


def _get_database_metadata():
    return metadataService.get_database_metadata("db1", {}, CLAIMS)


def _create_asset_link_metadata(items):
    return metadataService.create_asset_link_metadata(
        "link1", CreateAssetLinkMetadataRequestModel(metadata=items), CLAIMS)


def _update_asset_link_metadata(items):
    return metadataService.update_asset_link_metadata(
        "link1",
        UpdateAssetLinkMetadataRequestModel(metadata=items, updateType="update"), CLAIMS)


def _create_asset_metadata(items):
    return metadataService.create_asset_metadata(
        "db1", "asset1", CreateAssetMetadataRequestModel(metadata=items), CLAIMS)


def _update_asset_metadata(items):
    return metadataService.update_asset_metadata(
        "db1", "asset1",
        UpdateAssetMetadataRequestModel(metadata=items, updateType="update"), CLAIMS)


def _create_file_metadata(items):
    return metadataService.create_file_metadata(
        "db1", "asset1",
        CreateFileMetadataRequestModel(filePath=FILE_PATH, type="metadata", metadata=items),
        CLAIMS)


def _update_file_metadata(items):
    return metadataService.update_file_metadata(
        "db1", "asset1",
        UpdateFileMetadataRequestModel(
            filePath=FILE_PATH, type="metadata", metadata=items, updateType="update"),
        CLAIMS)


def _create_file_attributes(items):
    return metadataService.create_file_metadata(
        "db1", "asset1",
        CreateFileMetadataRequestModel(filePath=FILE_PATH, type="attribute", metadata=items),
        CLAIMS)


def _update_file_attributes(items):
    return metadataService.update_file_metadata(
        "db1", "asset1",
        UpdateFileMetadataRequestModel(
            filePath=FILE_PATH, type="attribute", metadata=items, updateType="update"),
        CLAIMS)


def _create_database_metadata(items):
    return metadataService.create_database_metadata(
        "db1", CreateDatabaseMetadataRequestModel(metadata=items), CLAIMS)


def _update_database_metadata(items):
    return metadataService.update_database_metadata(
        "db1",
        UpdateDatabaseMetadataRequestModel(metadata=items, updateType="update"), CLAIMS)


class _Rows:
    """The four record shapes a given entity's table stores, for one parametrised case."""

    def __init__(self, legacy, well_formed, valueless_required, valued_required):
        self.legacy = legacy
        self.well_formed = well_formed
        self.valueless_required = valueless_required
        self.valued_required = valued_required


METADATA_ROWS = _Rows(
    LEGACY_ROW, WELL_FORMED_ROW, VALUELESS_REQUIRED_ROW, VALUED_REQUIRED_ROW)
ATTRIBUTE_ROWS = _Rows(
    LEGACY_ATTRIBUTE_ROW, WELL_FORMED_ATTRIBUTE_ROW,
    VALUELESS_REQUIRED_ATTRIBUTE_ROW, VALUED_REQUIRED_ATTRIBUTE_ROW)


# (id, GET, write, the record shapes that entity's table stores).
ROUND_TRIPS = [
    ("create-assetLinkMetadata", _get_asset_link_metadata,
     _create_asset_link_metadata, METADATA_ROWS),
    ("update-assetLinkMetadata", _get_asset_link_metadata,
     _update_asset_link_metadata, METADATA_ROWS),
    ("create-assetMetadata", _get_asset_metadata, _create_asset_metadata, METADATA_ROWS),
    ("update-assetMetadata", _get_asset_metadata, _update_asset_metadata, METADATA_ROWS),
    ("create-fileMetadata", _get_file_metadata, _create_file_metadata, METADATA_ROWS),
    ("update-fileMetadata", _get_file_metadata, _update_file_metadata, METADATA_ROWS),
    ("create-fileAttribute", _get_file_attributes, _create_file_attributes, ATTRIBUTE_ROWS),
    ("update-fileAttribute", _get_file_attributes, _update_file_attributes, ATTRIBUTE_ROWS),
    ("create-databaseMetadata", _get_database_metadata,
     _create_database_metadata, METADATA_ROWS),
    ("update-databaseMetadata", _get_database_metadata,
     _update_database_metadata, METADATA_ROWS),
]

_IDS = [name for name, _, _, _ in ROUND_TRIPS]


def _get_items(response):
    """The GET's own serialized metadata list, exactly as a client receives it."""
    return response.dict()["metadata"]


@pytest.mark.unit
@pytest.mark.parametrize("path_name,do_get,do_write,rows", ROUND_TRIPS, ids=_IDS)
class TestTheGetResponseIsWritableBack:
    def test_the_get_output_for_a_typeless_record_is_accepted_by_the_write(
            self, path_name, do_get, do_write, rows):
        """The defect: this write answered 400 on a field the operator never supplied."""
        with _RoundTripHarness(stored_rows=(rows.legacy,)) as harness:
            get_response = do_get()
            items = _get_items(get_response)

            assert any(item["metadataKey"] == "legacyKey" and item["metadataValueType"] is None
                       for item in items), (
                f"{path_name}: the GET did not report the record with a null value type, so "
                f"this asserts nothing about the round-trip: {items}")

            write_response = do_write(items)

        assert write_response.success is True, (
            f"{path_name} refused the write body the GET just produced: {write_response}")
        assert harness.wrote, f"{path_name} reported success without writing"

        written = harness.written_items()
        assert "legacyKey" in written, (
            f"{path_name} did not write the record being repaired: {sorted(written)}")
        assert _stored_value_type(written["legacyKey"]) is not None, (
            f"{path_name} stored the record still carrying no value type, so the round-trip "
            f"did not repair it: {written['legacyKey']}")

    def test_a_complete_record_is_writable_back_too(
            self, path_name, do_get, do_write, rows):
        """Positive control: the sequence reaches the write on a well-formed record as well."""
        with _RoundTripHarness(stored_rows=(rows.well_formed,)) as harness:
            items = _get_items(do_get())
            write_response = do_write(items)

        assert write_response.success is True, f"{path_name}: {write_response}"
        assert harness.wrote
        written = harness.written_items()
        assert _stored_value_type(written["legacyKey"]) is not None


@pytest.mark.unit
@pytest.mark.parametrize("path_name,do_get,do_write,rows", ROUND_TRIPS, ids=_IDS)
class TestRetroactiveEnforcementSurvivesTheRoundTrip:
    """FIX-061: a schema-required field with no value keeps blocking, round-trip or not.

    Accepting a null on the write must not waive required-field validation. The absent value
    resolves to the empty string, which `is_empty_value` reads as empty -- so the refusal comes
    from the same check that refuses an explicitly blank required field.
    """

    def test_a_valueless_required_field_still_refuses_the_round_tripped_write(
            self, path_name, do_get, do_write, rows):
        with _RoundTripHarness(stored_rows=(rows.valueless_required,),
                               query_return=REQUIRED_FIELD_SCHEMA) as harness:
            items = _get_items(do_get())

            assert any(item["metadataKey"] == "requiredField"
                       and item["metadataValue"] is None for item in items), (
                f"{path_name}: the GET did not report the required field as carrying no value, "
                f"so this asserts nothing: {items}")

            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                do_write(items)

        assert "requiredField" in str(raised.value), (
            f"{path_name} refused for an unrelated reason: {raised.value}")
        assert not harness.wrote, (
            f"{path_name} wrote a record that leaves a schema-required field empty")

    def test_the_same_required_field_carrying_a_value_round_trips(
            self, path_name, do_get, do_write, rows):
        """Positive control: the refusal above is the empty value, not the required flag."""
        with _RoundTripHarness(stored_rows=(rows.valued_required,),
                               query_return=REQUIRED_FIELD_SCHEMA) as harness:
            items = _get_items(do_get())
            write_response = do_write(items)

        assert write_response.success is True, f"{path_name}: {write_response}"
        assert harness.wrote
        assert "requiredField" in harness.written_items()
