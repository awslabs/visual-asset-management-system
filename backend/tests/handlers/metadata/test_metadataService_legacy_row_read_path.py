# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""One malformed STORED metadata row must not hide an entity's whole metadata list.

The write path was unblocked first (test_metadataService_legacy_row_write_path.py). The READ path
still subscripted `item['metadataValueType']` twelve times -- once in each metadata GET's
schema-enrichment arm and once again in that arm's unenriched fallback -- so a row written by a
2.5.x deployment, which can carry no `metadataValueType` at all, raised `KeyError` in the arm, the
fallback raised the same `KeyError` on the same row, and the entity answered
400 "Error retrieving metadata". The operator could not see which key to repair with the write that
had just been unblocked.

## The shape chosen, and why

A tolerated row is returned with the absent attribute NULL.

* Not dropped. Dropping it silently is worse than the 400: the list would look complete and nobody
  would learn the row exists. `TestTheRowIsNotDropped` is the assertion that separates "tolerated
  the bad row" from "returned nothing", and it is stated as containment of (key, value, type)
  tuples so schema-injected fields and ordering are free to change.
* Not fabricated. `metadataValueType=null` reports what the row carries; defaulting it to "string"
  would assert a type the row does not have, and would make the response indistinguishable from a
  row that really is a string. `TestTheAbsentTypeIsNotInvented` pins that.
* A well-formed sibling row is returned unchanged in the same response. Without that, "tolerated"
  could be satisfied by a handler that returned an empty list.

## What is deliberately NOT changed

Only the READ paths. `stored_metadata_entries` feeds the fail-closed schema-VALIDATION arm, and that
arm must keep denying a write when the schema lookup did not complete -- a different condition from
a malformed stored row. FIX-061 (S2-BACKEND-119) further records the owner's ruling that retroactive
enforcement of a newly required field is INTENDED, so a stored row holding no value for a
schema-required field must keep blocking writes and must not be grandfathered.
`TestTheValidationArmIsUnaffected` re-states both against the read-tolerant code, so a later change
that reaches for the same tolerance on the validation side fails here.

`TestNoGetPathReadsAToleratedAttributeBySubscript` is the class guard. The behavioural tests above
cover the six GET entry points that exist; the guard walks the module and fails on any `get_*`
function -- including one added later -- that reads a tolerated attribute by subscript again.
"""

import ast
import contextlib
import inspect
import sys
from enum import Enum

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import (
    SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE,
    TOLERATED_ABSENT_STORED_FIELDS,
    VAMSGeneralErrorResponse,
)
from backend.backend.models.assetsV3 import AssetVersionMetadataItemModel

CLAIMS = {"tokens": ["user1"]}

# The row as a 2.5.x deployment could have written it: a value, and no metadataValueType at all.
LEGACY_ROW = {
    "metadataKey": {"S": "legacyKey"},
    "metadataValue": {"S": "legacy value"},
}

# The sibling that proves the response is not simply empty.
WELL_FORMED_ROW = {
    "metadataKey": {"S": "goodKey"},
    "metadataValue": {"S": "good value"},
    "metadataValueType": {"S": "string"},
}

# The same pair on a file-ATTRIBUTE table, which stores the attribute* names.
LEGACY_ATTRIBUTE_ROW = {
    "attributeKey": {"S": "legacyKey"},
    "attributeValue": {"S": "legacy value"},
}
WELL_FORMED_ATTRIBUTE_ROW = {
    "attributeKey": {"S": "goodKey"},
    "attributeValue": {"S": "good value"},
    "attributeValueType": {"S": "string"},
}


def _field(field_name, required=False):
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


# Both stored keys are declared, plus one the schema declares with no stored row at all -- so the
# response also exercises the field-injection branch of enrich_metadata_with_schema alongside the
# tolerated row.
DECLARED_SCHEMA = _schema_page(
    _field("goodKey"), _field("legacyKey"), _field("injectedKey"))


def _paginator(items):
    paginator = MagicMock()
    page_iterator = MagicMock()
    page_iterator.build_full_result.return_value = {"Items": list(items)}
    paginator.paginate.return_value = page_iterator
    return paginator


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """Clear the aggregate cache on BOTH module objects that expose it.

    This directory's conftest loads `common.metadataSchemaValidation` as a separate module object
    from `backend.backend.common.metadataSchemaValidation`; clearing only one leaves the other
    answering the next test's query.
    """
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


class _ReadHarness:
    """Module globals every metadata GET touches.

    The stored-row paginator serves `stored_rows`; `query` answers the REAL `get_aggregated_schemas`
    (this directory's conftest loads the real `common.metadataSchemaValidation`), so schema
    enrichment is the real function rather than a patched stand-in.
    """

    def __init__(self, stored_rows=(), query_return=DECLARED_SCHEMA, query_side_effect=None):
        self.client = MagicMock()
        self.client.get_paginator.return_value = _paginator(stored_rows)
        if query_side_effect is not None:
            self.client.query.side_effect = query_side_effect
        else:
            # A return_value rather than a list: the real lookup issues one query per database in
            # scope (the entity's database plus GLOBAL) and pinning the count would make this a
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
            ("CasbinEnforcer", self.enforcer_cls),
        ):
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


def _plain(value):
    return value.value if isinstance(value, Enum) else value


def _triples(response):
    """(metadataKey, metadataValue, metadataValueType) for every item in a GET response.

    A set of tuples rather than a list: a safer implementation is free to add schema-injected
    fields, reorder by sequence, or widen the projection without failing these tests.
    """
    return {
        (item.metadataKey, _plain(item.metadataValue), _plain(item.metadataValueType))
        for item in response.metadata
    }


# ---------------------------------------------------------------------------------------------
# The five GET entry points that read stored DynamoDB rows, each with the legacy row shape its
# own table stores. Both file modes are here: the ATTRIBUTE mode reads a different table under
# different attribute names, and its per-row normalization was already tolerant -- it is driven
# so a later edit cannot quietly regress it.
# ---------------------------------------------------------------------------------------------
def _get_asset_link_metadata():
    return metadataService.get_asset_link_metadata("link1", {}, CLAIMS)


def _get_asset_metadata():
    return metadataService.get_asset_metadata("db1", "asset1", {}, CLAIMS)


def _get_file_metadata():
    return metadataService.get_file_metadata(
        "db1", "asset1", "/folder/file.txt", "metadata", {}, CLAIMS)


def _get_file_attributes():
    return metadataService.get_file_metadata(
        "db1", "asset1", "/folder/file.txt", "attribute", {}, CLAIMS)


def _get_database_metadata():
    return metadataService.get_database_metadata("db1", {}, CLAIMS)


READ_PATHS = [
    ("assetLinkMetadata", _get_asset_link_metadata, LEGACY_ROW, WELL_FORMED_ROW),
    ("assetMetadata", _get_asset_metadata, LEGACY_ROW, WELL_FORMED_ROW),
    ("fileMetadata", _get_file_metadata, LEGACY_ROW, WELL_FORMED_ROW),
    ("fileAttribute", _get_file_attributes, LEGACY_ATTRIBUTE_ROW, WELL_FORMED_ATTRIBUTE_ROW),
    ("databaseMetadata", _get_database_metadata, LEGACY_ROW, WELL_FORMED_ROW),
]

_IDS = [name for name, _, _, _ in READ_PATHS]


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,legacy_row,well_formed_row", READ_PATHS, ids=_IDS)
class TestTheRowIsNotDropped:
    def test_the_legacy_row_and_its_well_formed_sibling_are_both_returned(
            self, path_name, invoke, legacy_row, well_formed_row):
        """The defect: this call answered 400 for the whole entity, sibling rows included."""
        with _ReadHarness(stored_rows=(legacy_row, well_formed_row)) as harness:
            response = invoke()

        observed = _triples(response)
        assert ("legacyKey", "legacy value", None) in observed, (
            f"{path_name} dropped or refused the row that carries no metadataValueType: {observed}")
        assert ("goodKey", "good value", "string") in observed, (
            f"{path_name} lost the well-formed sibling row, so the response is not simply "
            f"'the bad row tolerated': {observed}")
        assert harness.client.query.called, (
            "the real schema lookup was never reached, so this asserts nothing about enrichment")

    def test_a_row_carrying_neither_value_nor_type_is_still_returned(
            self, path_name, invoke, legacy_row, well_formed_row):
        """A row can predate both attributes; the DELETE paths already read both with .get."""
        key_field = "attributeKey" if "attributeKey" in legacy_row else "metadataKey"
        bare_row = {key_field: {"S": "legacyKey"}}

        with _ReadHarness(stored_rows=(bare_row, well_formed_row)):
            response = invoke()

        observed = _triples(response)
        assert ("legacyKey", None, None) in observed, (
            f"{path_name} refused or dropped a row carrying only its key: {observed}")
        assert ("goodKey", "good value", "string") in observed, observed

    def test_a_fully_well_formed_entity_is_unchanged(
            self, path_name, invoke, legacy_row, well_formed_row):
        """Positive control: the harness returns rows at all, and nothing gained a null."""
        with _ReadHarness(stored_rows=(well_formed_row,)):
            response = invoke()

        observed = _triples(response)
        assert ("goodKey", "good value", "string") in observed, observed
        assert not any(value is None or value_type is None
                       for _, value, value_type in observed), (
            f"{path_name} reported a well-formed row as missing an attribute: {observed}")

    def test_the_row_survives_a_failed_schema_enrichment(
            self, path_name, invoke, legacy_row, well_formed_row):
        """The unenriched fallback arm was the SECOND copy of the subscript.

        Fixing only the enrichment arm moved the KeyError one line down and still answered 400,
        so the fallback is driven by making the schema lookup itself fail.
        """
        with _ReadHarness(stored_rows=(legacy_row, well_formed_row),
                          query_side_effect=RuntimeError("schema read failed")):
            response = invoke()

        observed = _triples(response)
        assert ("legacyKey", "legacy value", None) in observed, (
            f"{path_name} unenriched fallback still refused the legacy row: {observed}")
        assert ("goodKey", "good value", "string") in observed, observed
        assert response.restrictMetadataOutsideSchemas is False


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,legacy_row,well_formed_row", READ_PATHS, ids=_IDS)
class TestTheAbsentTypeIsNotInvented:
    """Reporting the absent type as "string" would be indistinguishable from a real string row."""

    def test_the_absent_type_is_null_and_not_a_default_value_type(
            self, path_name, invoke, legacy_row, well_formed_row):
        with _ReadHarness(stored_rows=(legacy_row,)):
            response = invoke()

        legacy = [item for item in response.metadata if item.metadataKey == "legacyKey"]
        assert legacy, f"{path_name} returned no row for the legacy key: {_triples(response)}"
        assert legacy[0].metadataValueType is None, (
            f"{path_name} invented a value type for a row that carries none: "
            f"{legacy[0].metadataValueType!r}")
        # The stored value is untouched -- tolerance applies to the ABSENT attribute only.
        assert legacy[0].metadataValue == "legacy value"

    def test_the_response_serializes(self, path_name, invoke, legacy_row, well_formed_row):
        """The null has to survive the response model, which declares the type non-optional.

        A tolerant read that cannot be serialized is still a 500 for the caller.
        """
        import json

        with _ReadHarness(stored_rows=(legacy_row, well_formed_row)):
            response = invoke()

        body = json.loads(json.dumps(response.dict(), default=str))
        by_key = {item["metadataKey"]: item for item in body["metadata"]}
        assert by_key["legacyKey"]["metadataValueType"] is None, by_key["legacyKey"]
        assert by_key["goodKey"]["metadataValueType"] == "string", by_key["goodKey"]


@pytest.mark.unit
class TestTheVersionSnapshotGetsStillReturnTheirRows:
    """The two version GETs read a Pydantic snapshot, not stored attributes.

    They convert through the same helper, so a regression there would silently empty a version's
    metadata list. Their upstream (assetVersions.get_asset_metadata_version) supplies a value type
    for every row, so the absent shape cannot reach them -- what is asserted here is that routing
    them through the shared conversion did not lose rows.
    """

    SNAPSHOT = [
        AssetVersionMetadataItemModel(
            type="metadata", filePath="/", metadataKey="goodKey",
            metadataValue="good value", metadataValueType="string"),
        AssetVersionMetadataItemModel(
            type="metadata", filePath="/folder/file.txt", metadataKey="fileKey",
            metadataValue="file value", metadataValueType="string"),
        AssetVersionMetadataItemModel(
            type="attribute", filePath="/folder/file.txt", metadataKey="attrKey",
            metadataValue="attr value", metadataValueType="string"),
    ]

    @contextlib.contextmanager
    def _version_harness(self):
        with _ReadHarness() as harness, \
                patch.object(metadataService, "validate_asset_version_exists",
                             MagicMock(return_value=True)), \
                patch.object(metadataService, "get_asset_metadata_version",
                             MagicMock(return_value=list(self.SNAPSHOT))):
            yield harness

    def test_asset_version_metadata(self):
        with self._version_harness():
            response = metadataService.get_asset_metadata(
                "db1", "asset1", {"assetVersionId": "v1"}, CLAIMS)
        assert ("goodKey", "good value", "string") in _triples(response), _triples(response)

    def test_file_version_metadata(self):
        with self._version_harness():
            response = metadataService.get_file_metadata(
                "db1", "asset1", "/folder/file.txt", "metadata",
                {"assetVersionId": "v1"}, CLAIMS)
        assert ("fileKey", "file value", "string") in _triples(response), _triples(response)

    def test_file_version_attributes(self):
        with self._version_harness():
            response = metadataService.get_file_metadata(
                "db1", "asset1", "/folder/file.txt", "attribute",
                {"assetVersionId": "v1"}, CLAIMS)
        assert ("attrKey", "attr value", "string") in _triples(response), _triples(response)


# --------------------------------------------------------------------------------------------
# Log volume. The per-row form reported one warning per malformed row per request, so an upgraded
# entity emitted a line for every legacy row on every metadata request.
# --------------------------------------------------------------------------------------------
# Digit-free so that asserting the reported COUNT appears in the line cannot be satisfied by a
# key name that happens to contain the digit.
MALFORMED_KEY_NAMES = ("alpha", "beta", "gamma", "delta",
                       "epsilon", "zeta", "eta", "theta")


def legacy_rows(count):
    return tuple({"metadataKey": {"S": name}, "metadataValue": {"S": "v"}}
                 for name in MALFORMED_KEY_NAMES[:count])


def warning_lines_naming(log, attribute):
    """Every warning line that reports `attribute` as absent."""
    return [str(call) for call in log.warning.call_args_list if attribute in str(call)]


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke,legacy_row,well_formed_row", READ_PATHS, ids=_IDS)
class TestTheReadReportsMalformedRowsWithoutOnePerRow:
    """An UPPER bound stated as invariance: the line count must not track the row count.

    Not an exact count, so a safer implementation -- one line for the whole request, a summary
    per attribute, an extra unrelated line -- passes, while the per-row form fails.
    """

    def _lines(self, invoke, rows, well_formed_row):
        with _ReadHarness(stored_rows=rows + (well_formed_row,)), \
                patch.object(metadataService, "logger") as log:
            invoke()
        return warning_lines_naming(log, "metadataValueType")

    def test_the_line_count_does_not_grow_with_the_number_of_malformed_rows(
            self, path_name, invoke, legacy_row, well_formed_row):
        few = self._lines(invoke, legacy_rows(2), well_formed_row)
        many = self._lines(invoke, legacy_rows(8), well_formed_row)

        assert len(few) >= 1, (
            f"{path_name} tolerated malformed rows with no report at all, so nobody learns they "
            f"exist")
        assert len(many) == len(few), (
            f"{path_name} emitted {len(many)} lines for 8 malformed rows and {len(few)} for 2, so "
            f"reporting still scales with the data")

    def test_the_report_names_the_count_and_every_affected_key(
            self, path_name, invoke, legacy_row, well_formed_row):
        lines = self._lines(invoke, legacy_rows(8), well_formed_row)
        joined = " ".join(lines)

        assert "8" in joined, (
            f"{path_name} aggregated the lines but dropped the count: {joined}")
        unnamed = [name for name in MALFORMED_KEY_NAMES[:8] if name not in joined]
        assert not unnamed, (
            f"{path_name} does not name the keys an operator has to repair ({unnamed}): {joined}")
        assert "goodKey" not in joined, (
            f"{path_name} reported a well-formed row as malformed: {joined}")


@pytest.mark.unit
class TestTheValidationArmIsUnaffected:
    """The read is tolerant; the write-path VALIDATION arm is not, and must stay that way.

    Two prohibitions bear on this file. S2-BACKEND-060 made the schema-validation arm fail closed,
    so a schema lookup that did not complete must still deny the write. FIX-061 (S2-BACKEND-119)
    records that retroactive enforcement of a newly required field is INTENDED, so a stored row
    holding no value for a schema-required field must still block. Neither may be relaxed by the
    read-side tolerance, and the shared helper is the place a later change would relax both.
    """

    REQUIRED_SCHEMA = _schema_page(_field("declared"), _field("requiredField", required=True))

    @contextlib.contextmanager
    def _write_harness(self, stored_rows, **kwargs):
        harness = _ReadHarness(stored_rows=stored_rows, **kwargs)
        with harness:
            harness.client.batch_write_item.return_value = {"UnprocessedItems": {}}
            with patch.object(metadataService, "asset_file_metadata_table", MagicMock()):
                yield harness

    def _update(self):
        from backend.backend.models.metadata import (
            MetadataItemModel, UpdateAssetMetadataRequestModel)
        return metadataService.update_asset_metadata(
            "db1", "asset1",
            UpdateAssetMetadataRequestModel(
                metadata=[MetadataItemModel(metadataKey="declared", metadataValue="x")],
                updateType="update"),
            CLAIMS)

    def test_a_stored_row_with_no_value_for_a_required_field_still_blocks_the_write(self):
        valueless_required = {"metadataKey": {"S": "requiredField"}}
        with self._write_harness((valueless_required,),
                                 query_return=self.REQUIRED_SCHEMA) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                self._update()

        assert "Schema validation failed" in str(raised.value), raised.value
        assert harness.client.batch_write_item.call_count == 0, (
            "read tolerance was carried into validation and grandfathered the row")

    def test_the_same_write_is_allowed_once_the_required_field_has_a_value(self):
        """Positive control: the block above is the required-field check, not a broken read."""
        filled_in = {"metadataKey": {"S": "requiredField"},
                     "metadataValue": {"S": "v"}, "metadataValueType": {"S": "string"}}
        with self._write_harness((filled_in,), query_return=self.REQUIRED_SCHEMA) as harness:
            response = self._update()

        assert response.success is True, response
        assert harness.client.batch_write_item.call_count > 0

    def test_a_schema_lookup_that_did_not_complete_still_denies_the_write(self):
        with self._write_harness((LEGACY_ROW,),
                                 query_side_effect=RuntimeError("throttled")) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                self._update()

        assert SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value), raised.value
        assert harness.client.batch_write_item.call_count == 0


@pytest.mark.unit
class TestNoGetPathReadsAToleratedAttributeBySubscript:
    """The class guard: it fails on a GET path added later that reintroduces the subscript.

    The behavioural tests above cover the entry points that exist today. This one walks the module,
    so a seventh metadata GET written the old way fails here even though no parametrisation names
    it. A subscript is the only form that can raise KeyError on a legacy row; `.get` cannot.
    """

    # Not in the sweep: metadataKey is the table's sort key, so it is present on every stored row
    # by construction, and a read that invents one would be worse than raising.
    SWEPT_ATTRIBUTES = frozenset(
        TOLERATED_ABSENT_STORED_FIELDS
        + ('attributeValue', 'attributeValueType'))

    def _get_functions(self):
        tree = ast.parse(inspect.getsource(metadataService))
        return {node.name: node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name.startswith('get_')}

    def test_the_sweep_covers_every_metadata_get_entry_point(self):
        """Non-emptiness guard: an empty sweep would make the assertion below pass vacuously."""
        found = set(self._get_functions())
        expected = {
            'get_asset_link_metadata', 'get_asset_metadata', 'get_asset_metadata_from_version',
            'get_file_metadata', 'get_file_metadata_from_version', 'get_database_metadata',
        }
        assert expected <= found, f"the sweep missed {sorted(expected - found)}"

    def test_no_get_function_subscripts_a_tolerated_stored_attribute(self):
        offenders = []
        for name, node in self._get_functions().items():
            for child in ast.walk(node):
                if not isinstance(child, ast.Subscript):
                    continue
                index = child.slice
                if (isinstance(index, ast.Constant) and isinstance(index.value, str)
                        and index.value in self.SWEPT_ATTRIBUTES):
                    offenders.append(f"{name}:{child.lineno} [{index.value!r}]")

        assert not offenders, (
            "a metadata GET reads a stored attribute a legacy row can lack by subscript, which "
            "raises KeyError and turns one bad row into a 400 for the whole entity: "
            + ", ".join(offenders))
