# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A stored metadata row written by an earlier release must not empty an exported metadata block.

Three helpers in assetExportService built their result by subscripting the stored attributes --
`get_asset_metadata` and `get_file_metadata` on `deserialized['metadataValue']`,
`get_asset_link_metadata` on `item['metadataValueType']` and `item['metadataValue']`. A row lacking
either is the shape a pre-upgrade write leaves behind (`metadataService.TOLERATED_ABSENT_STORED_FIELDS`
names exactly those two attributes), so ONE such row raised `KeyError` inside the conversion loop and
the enclosing `except Exception` answered with an empty dict.

Silence is what made that serious. An export is what a customer takes off the platform, and an empty
metadata block reads as "this asset has no metadata" rather than "one row could not be read". The
asset export API is not the product's backup or migration path -- `deployment/uninstall.md` names
DynamoDB on-demand table export for that, and there is no import counterpart to this endpoint -- but
it is still the customer's data leaving the platform, so a block that silently loses every sibling row
is data loss with no signal attached to it.

The load-bearing assertion in each class is that the OTHER row is still present. "It did not raise" is
satisfied equally by an empty block, which is exactly what the defect produced.
"""

import pytest
from unittest.mock import MagicMock, patch

# Reuse the loader from the fail-closed suite: assetExportService cannot be imported normally
# because the root conftest registers a mock `handlers` package that shadows the real one.
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
    _asset,
    _enforcer,
    _by_asset_id,
    _DB,
    _ASSET,
)

LEGACY_KEY = "legacyKey"
HEALTHY_KEY = "healthyKey"
FILE_PATH = "/folder/file.txt"
LINK_ID = "link-1"


def _typed(row):
    """A DynamoDB-typed item as the low-level client returns it."""
    return {k: {"S": v} for k, v in row.items()}


def _metadata_rows():
    """One row carrying neither stored attribute (the pre-upgrade shape) and one complete row.

    "Complete" means BOTH `metadataValue` and `metadataValueType`: the getters report each absence
    separately, and the export bundle reports the row's own type rather than defaulting it, so a row
    with a value and no type is a legacy row too.
    """
    return [
        _typed({"metadataKey": LEGACY_KEY}),
        _typed({
            "metadataKey": HEALTHY_KEY,
            "metadataValue": "kept",
            "metadataValueType": "string",
        }),
    ]


def _healthy_metadata_rows():
    return [
        _typed({
            "metadataKey": LEGACY_KEY,
            "metadataValue": "also-complete",
            "metadataValueType": "string",
        }),
        _typed({
            "metadataKey": HEALTHY_KEY,
            "metadataValue": "kept",
            "metadataValueType": "number",
        }),
    ]


def _query_client(items):
    """A low-level DynamoDB client stub whose single query answers with `items`."""
    client = MagicMock()
    client.query.return_value = {"Items": list(items)}
    return client


def _warning_text(log):
    """Every warning line the run emitted, as one searchable blob."""
    return " | ".join(str(call) for call in log.warning.call_args_list)


@pytest.mark.unit
class TestAssetMetadataToleratesALegacyRow:
    """get_asset_metadata: one row with no metadataValue must not empty the asset's block."""

    def _run(self, rows):
        m = _load_asset_export_service()
        log = MagicMock()
        with patch.object(m, "dynamodb_client", _query_client(rows)), \
                patch.object(m, "logger", log):
            result = m.get_asset_metadata(_DB, _ASSET)
        return result, log

    def test_the_well_formed_row_survives_the_legacy_row(self):
        """The distinguishing assertion: before the fix the KeyError emptied the whole block."""
        result, _log = self._run(_metadata_rows())
        assert HEALTHY_KEY in result, (
            f"the legacy row emptied the asset's metadata block; got {result}")
        assert result[HEALTHY_KEY]["value"] == "kept"

    def test_the_legacy_row_is_kept_with_a_null_value(self):
        """Kept rather than dropped: a dropped row leaves nobody aware that it exists."""
        result, _log = self._run(_metadata_rows())
        assert LEGACY_KEY in result, f"the legacy row was dropped entirely; got {result}"
        assert result[LEGACY_KEY]["value"] is None, (
            "no value may be invented for a row that carries none")
        assert result[LEGACY_KEY]["valueType"] is None, (
            "nor may a TYPE be invented -- the export bundle reports what this returns")

    def test_the_absence_is_reported_with_the_key_and_the_count(self):
        """An operator needs to find it afterwards; the key is what begins the repair."""
        _result, log = self._run(_metadata_rows())
        text = _warning_text(log)
        assert "metadataValue" in text, f"the absence was not reported at all: {text}"
        assert LEGACY_KEY in text, f"the reported line does not name the key: {text}"
        assert "1 stored" in text, f"the reported line does not carry the count: {text}"

    def test_two_complete_rows_are_returned_and_report_nothing(self):
        """Control: the report above must be caused by the legacy row, not emitted always.

        Also the positive control for the whole class -- without it, every assertion here would
        pass against a helper that had stopped reading rows at all.
        """
        result, log = self._run(_healthy_metadata_rows())
        assert result == {
            LEGACY_KEY: {"value": "also-complete", "valueType": "string"},
            HEALTHY_KEY: {"value": "kept", "valueType": "number"},
        }
        assert log.warning.call_args_list == [], (
            f"a complete pair of rows was reported as malformed: {_warning_text(log)}")

    def test_a_failed_query_still_reports_at_error_level(self):
        """The whole-query failure is a different condition from a malformed row.

        It still degrades to an empty block so the asset keeps exporting, so the log line is the
        only signal there is -- it must carry the identifiers and a stack trace, not a warning.
        """
        m = _load_asset_export_service()
        log = MagicMock()
        client = MagicMock()
        client.query.side_effect = RuntimeError("table unavailable")
        with patch.object(m, "dynamodb_client", client), patch.object(m, "logger", log):
            result = m.get_asset_metadata(_DB, _ASSET)

        assert result == {}
        assert log.exception.call_count == 1, "a failed query must be reported at error level"
        assert _DB in str(log.exception.call_args) and _ASSET in str(log.exception.call_args)


@pytest.mark.unit
class TestFileMetadataToleratesALegacyRow:
    """get_file_metadata: the sibling copy of the same conversion loop."""

    def _run(self, rows):
        m = _load_asset_export_service()
        log = MagicMock()
        with patch.object(m, "dynamodb_client", _query_client(rows)), \
                patch.object(m, "logger", log):
            result = m.get_file_metadata(_DB, _ASSET, FILE_PATH)
        return result, log

    def test_the_well_formed_row_survives_the_legacy_row(self):
        result, _log = self._run(_metadata_rows())
        assert HEALTHY_KEY in result, (
            f"the legacy row emptied the file's metadata block; got {result}")
        assert result[HEALTHY_KEY]["value"] == "kept"

    def test_the_legacy_row_is_kept_with_a_null_value(self):
        result, _log = self._run(_metadata_rows())
        assert LEGACY_KEY in result, f"the legacy row was dropped entirely; got {result}"
        assert result[LEGACY_KEY]["value"] is None
        assert result[LEGACY_KEY]["valueType"] is None

    def test_the_absence_is_reported_with_the_file_path(self):
        """The file path is what tells the operator which of an asset's rows to repair."""
        _result, log = self._run(_metadata_rows())
        text = _warning_text(log)
        assert "metadataValue" in text and LEGACY_KEY in text, text
        assert FILE_PATH in text, f"the reported line does not name the file: {text}"

    def test_two_complete_rows_are_returned_and_report_nothing(self):
        """Control, and the positive control for this class."""
        result, log = self._run(_healthy_metadata_rows())
        assert result == {
            LEGACY_KEY: {"value": "also-complete", "valueType": "string"},
            HEALTHY_KEY: {"value": "kept", "valueType": "number"},
        }
        assert log.warning.call_args_list == [], _warning_text(log)


@pytest.mark.unit
class TestAssetLinkMetadataToleratesALegacyRow:
    """get_asset_link_metadata: reads BOTH stored attributes, so both absences matter.

    This helper queries through the resource API, so its rows arrive already deserialized.
    """

    @staticmethod
    def _rows():
        return [
            # No metadataValueType -- the attribute the metadata GET now reports as null.
            {"metadataKey": LEGACY_KEY, "metadataValue": "kept"},
            {"metadataKey": HEALTHY_KEY, "metadataValue": "also-kept",
             "metadataValueType": "string"},
        ]

    def _run(self, rows):
        m = _load_asset_export_service()
        log = MagicMock()
        table = MagicMock()
        table.query.return_value = {"Items": list(rows)}
        with patch.object(m, "asset_links_metadata_table", table), \
                patch.object(m, "logger", log):
            result = m.get_asset_link_metadata(LINK_ID)
        return result, log

    def test_the_well_formed_row_survives_the_legacy_row(self):
        """The distinguishing assertion: before the fix the link exported with no metadata."""
        result, _log = self._run(self._rows())
        assert HEALTHY_KEY in result, (
            f"the legacy row emptied the link's metadata block; got {result}")
        assert result[HEALTHY_KEY] == {"valueType": "string", "value": "also-kept"}

    def test_the_legacy_row_keeps_the_value_it_has(self):
        """The row holds a value; only its type is absent, so the value must survive."""
        result, _log = self._run(self._rows())
        assert LEGACY_KEY in result, f"the legacy row was dropped entirely; got {result}"
        assert result[LEGACY_KEY]["value"] == "kept"

    def test_no_type_is_fabricated_for_a_row_that_had_none(self):
        """Null, not "string" -- the shape the metadata GET settled on this release.

        Reporting a fabricated type here would make the export disagree with the metadata GET
        about the same stored row.
        """
        result, _log = self._run(self._rows())
        assert result[LEGACY_KEY]["valueType"] is None

    def test_an_absent_value_is_reported_as_null_too(self):
        """Both stored attributes are tolerated, and neither is invented."""
        result, _log = self._run([{"metadataKey": LEGACY_KEY}])
        assert result[LEGACY_KEY] == {"valueType": None, "value": None}

    def test_both_absences_are_reported(self):
        _result, log = self._run([{"metadataKey": LEGACY_KEY}])
        text = _warning_text(log)
        assert "metadataValue" in text and "metadataValueType" in text, text
        assert LINK_ID in text, f"the reported line does not name the link: {text}"

    def test_two_complete_rows_are_returned_and_report_nothing(self):
        """Control, and the positive control for this class."""
        rows = [
            {"metadataKey": LEGACY_KEY, "metadataValue": "a", "metadataValueType": "string"},
            {"metadataKey": HEALTHY_KEY, "metadataValue": "b", "metadataValueType": "number"},
        ]
        result, log = self._run(rows)
        assert result == {
            LEGACY_KEY: {"valueType": "string", "value": "a"},
            HEALTHY_KEY: {"valueType": "number", "value": "b"},
        }
        assert log.warning.call_args_list == [], _warning_text(log)


@pytest.mark.unit
class TestTheExportedBundleShowsTheDegradedRow:
    """End to end through process_asset_batch: what a customer actually receives.

    The helpers above return the raw value; the bundle wraps it. `str(value)` turned an absent
    value into the literal string "None", which is a fabricated value wearing a plausible shape --
    worse than a null, because nothing downstream can tell it from a real one.
    """

    def _export(self, rows):
        m = _load_asset_export_service()
        identifiers = [{"databaseId": _DB, "assetId": _ASSET, "isRoot": True}]
        details = {f"{_DB}:{_ASSET}": _asset(_ASSET)}
        log = MagicMock()

        with patch.object(m, "batch_get_assets", MagicMock(return_value=details)), \
                patch.object(m, "CasbinEnforcer", _enforcer()), \
                patch.object(m, "get_default_bucket_details", MagicMock(return_value={
                    "bucketId": "bucket-1", "bucketName": "bucket-name",
                    "baseAssetsPrefix": "prefix/"})), \
                patch.object(m, "list_s3_files", MagicMock(return_value=[])), \
                patch.object(m, "get_asset_version_info", MagicMock(return_value=None)), \
                patch.object(m, "get_asset_file_versions", MagicMock(return_value=None)), \
                patch.object(m, "dynamodb_client", _query_client(rows)), \
                patch.object(m, "logger", log):
            result = m.process_asset_batch(
                identifiers, m.AssetExportRequestModel(), {"tokens": ["alice"]})

        entry = _by_asset_id(result)[_ASSET]
        return entry

    def test_the_bundle_still_carries_the_well_formed_row(self):
        """The distinguishing assertion, restated at the level the customer sees."""
        entry = self._export(_metadata_rows())
        assert HEALTHY_KEY in entry["metadata"], (
            f"the exported bundle lost the sibling row; got {entry['metadata']}")
        assert entry["metadata"][HEALTHY_KEY]["value"] == "kept"

    def test_the_degraded_row_carries_a_null_and_not_the_string_None(self):
        entry = self._export(_metadata_rows())
        assert entry["metadata"][LEGACY_KEY]["value"] is None, entry["metadata"][LEGACY_KEY]

    def test_a_complete_pair_exports_both_values(self):
        """Positive control: the harness really did run the export and read the rows."""
        entry = self._export(_healthy_metadata_rows())
        assert entry["metadata"][LEGACY_KEY]["value"] == "also-complete"
        assert entry["metadata"][HEALTHY_KEY]["value"] == "kept"
