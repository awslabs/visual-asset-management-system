# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A malformed STORED metadata row must not make an entity's metadata permanently unwritable.

The four metadata DELETE paths read their existing rows with `.get` precisely because a row written
by an earlier deployment can carry no `metadataValueType` -- handlers/indexing/fileIndexer.py
tolerates exactly that shape. The eight WRITE-path copies of the same read kept a direct subscript
inside the now fail-closed schema-validation arm, so a `KeyError` on one such row was answered with
SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE: a 400 on every metadata create AND update for that entity,
unrecoverable through the API, because the write that would repair the row is the write being
refused. An upgraded deployment holds rows like that.

Reading the row tolerantly is not a relaxation of the arm. "This stored row is missing an attribute"
and "the schema lookup did not complete" are different conditions and only the second may deny the
write; the first is reported (once for the whole entity, not once per row) and the attribute
evaluated as absent, which is the conservative reading (absent value == empty, so a required field
still blocks).

## What is asserted, and in what shape

* All ten write entry points are driven -- create and update, for asset-link, asset, file metadata,
  file attribute and database metadata -- because the block is copy-pasted at each one. "Fixed at six
  of eight sites" is the failure mode this parametrisation exists to catch. The two file-ATTRIBUTE
  entries already read their row tolerantly (`attributeValueType` was always read with `.get`); they
  are driven so a later edit cannot quietly regress them.
* Every scenario runs through the REAL `get_aggregated_schemas`: this directory's conftest loads the
  real `common.metadataSchemaValidation`, so the schema half of the block is the real function rather
  than a patched stand-in.
* `TestTheArmStillFailsClosed` is the counter-test. Tolerating a malformed stored row must not
  tolerate a schema lookup that did not complete -- otherwise "the write succeeded" would also be
  satisfied by deleting the guard entirely.
* `TestRetroactiveEnforcementIsUnchanged` pins the other direction. FIX-061 (S2-BACKEND-119) records
  the owner's ruling that retroactive enforcement of a newly required field is INTENDED, so a stored
  row carrying no value for a schema-required field must keep blocking the write. A tolerant read
  must not become "drop the malformed row from the aggregate", which would grandfather it.
* `TestReportingDoesNotScaleWithTheMalformedRowCount` bounds the log volume. Reporting each row on
  its own emitted a line per legacy row on every create and update, which on an upgraded entity is
  a line per row per request. Asserted as invariance across 2 and 8 malformed rows rather than as an
  exact count, so a safer form -- one line, one per absent attribute, an extra unrelated line --
  still passes. The READ side of the same property is in
  test_metadataService_legacy_row_read_path.py.

The aggregate cache is a module global on `common.metadataSchemaValidation`, which this directory's
conftest loads as a SEPARATE module object from `backend.backend.common.metadataSchemaValidation` --
clearing only one leaves the other answering the next test's query.
"""

import contextlib
import sys

import pytest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import (
    SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE,
    VAMSGeneralErrorResponse,
)
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

THROTTLE = ClientError(
    {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttled"}},
    "Query",
)

CLAIMS = {"tokens": ["user1"]}

# The stored row as a 2.5.x deployment could have written it: a value, and no metadataValueType at
# all. `governedKey` is declared by the schema below, so the row is an ordinary schema field whose
# stored shape predates the attribute -- not an off-schema key, which
# restrictMetadataOutsideSchemas would refuse for an unrelated reason.
_LEGACY_METADATA_ROW = {
    "metadataKey": {"S": "governedKey"},
    "metadataValue": {"S": "v"},
}

# The same row on a file-ATTRIBUTE table, which stores the attribute* names
# (_upsert_file_metadata writes attributeKey/attributeValue/attributeValueType for type=attribute).
_LEGACY_ATTRIBUTE_ROW = {
    "attributeKey": {"S": "governedKey"},
    "attributeValue": {"S": "v"},
}

# A required schema field whose stored row carries no value at all -- the retroactive-enforcement
# scenario, which must keep blocking.
_VALUELESS_REQUIRED_ROW = {"metadataKey": {"S": "requiredField"}}


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


# The schema every scenario aggregates: the key the request writes and the key the stored row holds
# are both declared, so restrictMetadataOutsideSchemas (enabled on the harness database row) has no
# reason of its own to refuse.
DECLARED_SCHEMA = _schema_page(_field("declared"), _field("governedKey"))

# Keys for the many-malformed-rows scenarios. Digit-free, so asserting that the reported COUNT
# appears in a line cannot be satisfied by a key name that happens to contain the digit.
MALFORMED_KEY_NAMES = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")

# Every one of them declared, for the same restrictMetadataOutsideSchemas reason as above.
MANY_KEYS_SCHEMA = _schema_page(
    _field("declared"), *[_field(name) for name in MALFORMED_KEY_NAMES])


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


class _WriteHarness:
    """Module globals every metadata write touches before batch_write_item.

    The existing-metadata paginator serves `existing_rows`, and `query` answers the REAL schema
    lookup, so both inputs of the schema-validation block are exercised end to end.
    """

    def __init__(self, existing_rows=(), query_return=None, query_side_effect=None):
        self.client = MagicMock()
        self.client.get_paginator.return_value = _paginator(existing_rows)
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}
        if query_side_effect is not None:
            self.client.query.side_effect = query_side_effect
        if query_return is not None:
            # A return_value rather than a list: the real lookup issues one query per database in
            # scope (the entity's database plus GLOBAL), and pinning the count would make this a
            # test of how many databases the handler aggregates.
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
            # The file paths check S3 for the file; the read under test is downstream of it.
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


def _items(keys=("declared",)):
    return [MetadataItemModel(metadataKey=key, metadataValue="x") for key in keys]


def _create_asset_link_metadata():
    return metadataService.create_asset_link_metadata(
        "link1", CreateAssetLinkMetadataRequestModel(metadata=_items()), CLAIMS)


def _update_asset_link_metadata():
    return metadataService.update_asset_link_metadata(
        "link1",
        UpdateAssetLinkMetadataRequestModel(metadata=_items(), updateType="update"), CLAIMS)


def _create_asset_metadata():
    return metadataService.create_asset_metadata(
        "db1", "asset1", CreateAssetMetadataRequestModel(metadata=_items()), CLAIMS)


def _update_asset_metadata():
    return metadataService.update_asset_metadata(
        "db1", "asset1",
        UpdateAssetMetadataRequestModel(metadata=_items(), updateType="update"), CLAIMS)


def _create_file_metadata():
    return metadataService.create_file_metadata(
        "db1", "asset1",
        CreateFileMetadataRequestModel(
            filePath="/folder/file.txt", type="metadata", metadata=_items()),
        CLAIMS)


def _update_file_metadata():
    return metadataService.update_file_metadata(
        "db1", "asset1",
        UpdateFileMetadataRequestModel(
            filePath="/folder/file.txt", type="metadata", metadata=_items(),
            updateType="update"),
        CLAIMS)


def _create_file_attributes():
    return metadataService.create_file_metadata(
        "db1", "asset1",
        CreateFileMetadataRequestModel(
            filePath="/folder/file.txt", type="attribute", metadata=_items()),
        CLAIMS)


def _update_file_attributes():
    return metadataService.update_file_metadata(
        "db1", "asset1",
        UpdateFileMetadataRequestModel(
            filePath="/folder/file.txt", type="attribute", metadata=_items(),
            updateType="update"),
        CLAIMS)


def _create_database_metadata():
    return metadataService.create_database_metadata(
        "db1", CreateDatabaseMetadataRequestModel(metadata=_items()), CLAIMS)


def _update_database_metadata():
    return metadataService.update_database_metadata(
        "db1",
        UpdateDatabaseMetadataRequestModel(metadata=_items(), updateType="update"), CLAIMS)


# (id, entry point, the legacy row shape that entry point's table stores). Both the create and the
# update half of every entity type is here: the eight write sites are two per entity family, and the
# file family has a second mode reading a different table.
WRITE_PATHS = [
    ("create-assetLinkMetadata", _create_asset_link_metadata, _LEGACY_METADATA_ROW),
    ("update-assetLinkMetadata", _update_asset_link_metadata, _LEGACY_METADATA_ROW),
    ("create-assetMetadata", _create_asset_metadata, _LEGACY_METADATA_ROW),
    ("update-assetMetadata", _update_asset_metadata, _LEGACY_METADATA_ROW),
    ("create-fileMetadata", _create_file_metadata, _LEGACY_METADATA_ROW),
    ("update-fileMetadata", _update_file_metadata, _LEGACY_METADATA_ROW),
    ("create-fileAttribute", _create_file_attributes, _LEGACY_ATTRIBUTE_ROW),
    ("update-fileAttribute", _update_file_attributes, _LEGACY_ATTRIBUTE_ROW),
    ("create-databaseMetadata", _create_database_metadata, _LEGACY_METADATA_ROW),
    ("update-databaseMetadata", _update_database_metadata, _LEGACY_METADATA_ROW),
]

_IDS = [name for name, _, _ in WRITE_PATHS]


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,legacy_row", WRITE_PATHS, ids=_IDS)
class TestALegacyRowShapeDoesNotBlockTheWrite:
    def test_a_stored_row_without_a_value_type_still_writes(self, path_name, invoke, legacy_row):
        """The defect: one such row refused every create and update for the entity, forever."""
        with _WriteHarness(existing_rows=(legacy_row,), query_return=DECLARED_SCHEMA) as harness:
            response = invoke()

        assert response.success is True, (
            f"{path_name} refused the write because a stored row predates metadataValueType: "
            f"{response}")
        assert harness.wrote, f"{path_name} reported success without writing"
        assert harness.client.query.called, (
            "the real schema lookup was never reached, so this asserts nothing about the block")

    def test_the_same_path_writes_when_every_stored_row_is_well_formed(
            self, path_name, invoke, legacy_row):
        """Positive control: the harness reaches the write on a complete row too.

        Without it, the assertion above could pass on a path that ignores its stored rows entirely.
        """
        well_formed = dict(legacy_row)
        type_field = "attributeValueType" if "attributeKey" in legacy_row else "metadataValueType"
        well_formed[type_field] = {"S": "string"}

        with _WriteHarness(existing_rows=(well_formed,), query_return=DECLARED_SCHEMA) as harness:
            response = invoke()

        assert response.success is True, f"{path_name}: {response}"
        assert harness.wrote


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,legacy_row", WRITE_PATHS, ids=_IDS)
class TestTheArmStillFailsClosed:
    """The counter-test: a malformed stored row is tolerated, an incomplete schema lookup is not.

    S2-BACKEND-060 made this arm fail closed because every control it carries -- schema conformance,
    the controlled-list check, the type-change guard and the restrictMetadataOutsideSchemas
    prohibition -- reads a missing schema as permission. Distinguishing the two conditions is the
    whole point; a fix that simply stopped raising would satisfy the class above and reopen that
    finding.
    """

    def test_a_throttled_schema_query_still_refuses_the_write(
            self, path_name, invoke, legacy_row):
        with _WriteHarness(existing_rows=(legacy_row,),
                           query_side_effect=THROTTLE) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                invoke()

        assert SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value), (
            f"{path_name} refused with an unexpected message: {raised.value}")
        assert not harness.wrote, (
            f"{path_name} wrote metadata even though the schema lookup did not complete")

    def test_a_failed_existing_metadata_read_still_refuses_the_write(
            self, path_name, invoke, legacy_row):
        """The other input. An error READING the rows is not the same as a row missing a field."""
        with _WriteHarness(existing_rows=(legacy_row,), query_return=DECLARED_SCHEMA) as harness:
            harness.client.get_paginator.return_value.paginate.return_value \
                .build_full_result.side_effect = THROTTLE
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                invoke()

        assert SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value)
        assert not harness.wrote


@pytest.mark.unit
class TestTheMalformedRowIsReported:
    """Tolerated, not hidden: the row has to be findable so an operator can repair it.

    Asserted on what `log_absent_stored_fields` is HANDED -- attribute name -> the keys of the rows
    that lack it -- rather than on the rendered line. The rendered form is free to change wording,
    add a count or truncate a long key list; what may not change is which rows are reported. An
    earlier version of the control here asserted a phrase absent from the well-formed case, and
    when the wording changed no emitted line could fail it any more.
    """

    @staticmethod
    def _reported(existing_rows):
        with _WriteHarness(existing_rows=existing_rows, query_return=DECLARED_SCHEMA), \
                patch.object(metadataService, "log_absent_stored_fields",
                             wraps=metadataService.log_absent_stored_fields) as report, \
                patch.object(metadataService, "logger") as log:
            response = metadataService.update_asset_metadata(
                "db1", "asset1",
                UpdateAssetMetadataRequestModel(metadata=_items(), updateType="update"), CLAIMS)
        reported = {}
        for call in report.call_args_list:
            for field_name, keys in call.args[0].items():
                reported.setdefault(field_name, set()).update(keys)
        logged = " ".join(str(call) for call in log.method_calls)
        return response, reported, logged

    def test_the_missing_attribute_and_its_key_are_reported(self):
        response, reported, logged = self._reported((_LEGACY_METADATA_ROW,))

        assert response.success is True, response
        assert reported.get("metadataValueType") == {"governedKey"}, (
            f"the malformed stored row was tolerated without being reported: {reported}")
        assert "metadataValueType" in logged and "governedKey" in logged, (
            f"nothing reaches the log, so an operator cannot find the row: {logged}")

    def test_a_well_formed_row_is_not_reported(self):
        """Control: reporting is conditional on the row, not emitted on every write."""
        well_formed = dict(_LEGACY_METADATA_ROW, metadataValueType={"S": "string"})
        _, reported, logged = self._reported((well_formed,))

        assert not any(reported.values()), (
            f"a well-formed row was reported as malformed: {reported}")
        assert "governedKey" not in logged, (
            f"a well-formed row's key was logged as malformed: {logged}")


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,legacy_row", WRITE_PATHS, ids=_IDS)
class TestReportingDoesNotScaleWithTheMalformedRowCount:
    """One line per malformed row meant an upgraded entity logged a line per row, per request.

    Stated as invariance rather than as an exact count: a safer implementation -- one line for the
    whole request, one per absent attribute, an extra unrelated line -- passes, while the per-row
    form fails. Driven at every write entry point because the read loop is reached from ten of
    them and "aggregated at eight of ten" is the failure mode this parametrisation exists to catch.
    """

    def _rows(self, legacy_row, count):
        key_field = "attributeKey" if "attributeKey" in legacy_row else "metadataKey"
        value_field = "attributeValue" if "attributeKey" in legacy_row else "metadataValue"
        return tuple({key_field: {"S": name}, value_field: {"S": "v"}}
                     for name in MALFORMED_KEY_NAMES[:count])

    def _lines(self, invoke, rows):
        with _WriteHarness(existing_rows=rows, query_return=MANY_KEYS_SCHEMA), \
                patch.object(metadataService, "logger") as log:
            invoke()
        return [str(call) for call in log.warning.call_args_list if "ValueType" in str(call)]

    def test_the_line_count_does_not_grow_with_the_number_of_malformed_rows(
            self, path_name, invoke, legacy_row):
        few = self._lines(invoke, self._rows(legacy_row, 2))
        many = self._lines(invoke, self._rows(legacy_row, 8))

        assert len(few) >= 1, (
            f"{path_name} tolerated malformed rows with no report at all, so nobody learns they "
            f"exist")
        assert len(many) == len(few), (
            f"{path_name} emitted {len(many)} lines for 8 malformed rows and {len(few)} for 2, so "
            f"reporting still scales with the data")

    def test_the_report_names_the_count_and_every_affected_key(self, path_name, invoke,
                                                               legacy_row):
        joined = " ".join(self._lines(invoke, self._rows(legacy_row, 8)))

        assert "8" in joined, (
            f"{path_name} aggregated the lines but dropped the count: {joined}")
        unnamed = [name for name in MALFORMED_KEY_NAMES[:8] if name not in joined]
        assert not unnamed, (
            f"{path_name} does not name the keys an operator has to repair ({unnamed}): {joined}")


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke", [
    ("create-assetMetadata", _create_asset_metadata),
    ("update-assetMetadata", _update_asset_metadata),
], ids=["create-assetMetadata", "update-assetMetadata"])
class TestRetroactiveEnforcementIsUnchanged:
    """FIX-061 (S2-BACKEND-119): retroactive enforcement of a newly required field is INTENDED.

    A stored row that holds no value for a schema-required field must keep blocking every write for
    that entity until the field is filled in. Reading stored rows tolerantly therefore has to mean
    "evaluate the absent attribute as absent" -- an absent value reads as EMPTY -- and not "leave the
    row out of the aggregate", which would grandfather exactly the records the ruling says must be
    blocked.
    """

    REQUIRED_SCHEMA = _schema_page(_field("declared"), _field("requiredField", required=True))

    def test_a_stored_row_with_no_value_for_a_required_field_still_blocks(
            self, path_name, invoke):
        with _WriteHarness(existing_rows=(_VALUELESS_REQUIRED_ROW,),
                           query_return=self.REQUIRED_SCHEMA) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                invoke()

        assert "Schema validation failed" in str(raised.value), (
            f"{path_name} refused for the wrong reason, or the required-field check did not run: "
            f"{raised.value}")
        assert not harness.wrote

    def test_the_same_write_is_allowed_once_the_required_field_has_a_value(
            self, path_name, invoke):
        """Positive control: the block above is the required-field check, not a broken row read."""
        filled_in = dict(_VALUELESS_REQUIRED_ROW,
                         metadataValue={"S": "v"}, metadataValueType={"S": "string"})
        with _WriteHarness(existing_rows=(filled_in,),
                           query_return=self.REQUIRED_SCHEMA) as harness:
            response = invoke()

        assert response.success is True, f"{path_name}: {response}"
        assert harness.wrote
