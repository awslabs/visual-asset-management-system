# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""One stored key must not read back as two different shapes on one deployment.

`get_asset_metadata_version` defaulted an absent `metadataValueType` to the literal string
"string" (and an absent `metadataValue` to ""), while the metadata GET reports both as **null**
(`metadataService.TOLERATED_ABSENT_STORED_FIELDS` and `metadata_response_models`). So the same
stored row answered `"string"` from the version GET and `null` from the metadata GET -- and the
version-snapshot write omits the attribute when the source row lacked it, which makes the read the
place the two shapes meet. `metadataService.get_asset_metadata`/`get_file_metadata` also serve the
`assetVersionId` form of the metadata GET out of this very function, so the disagreement was
reachable through one endpoint as well as two.

Two siblings had to move with it, and both are covered here:

* `revert_asset_metadata_version` restores a snapshot row by writing `{'S': metadata_item.<field>}`
  straight through. A None reaches boto3 as `{'S': None}`, which is rejected before the batch is
  sent -- so reporting null without omitting on restore would have turned one legacy row into a
  failed revert of the whole asset.
* the ATTRIBUTE half of `save_asset_metadata_version` defaulted its value type to "string" on the
  way IN. A version snapshot is immutable history, so a default recorded there becomes a type the
  row never had for as long as the version exists.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before the
# root conftest's autouse fixture installs its non-package placeholder (see S27-TEST-001).
from backend.backend.handlers.assets import assetVersions as _assetVersions

DB = "db1"
ASSET = "asset1"
VERSION = "v2"
FILE_PATH = "/folder/file.txt"
LEGACY_KEY = "legacyKey"
HEALTHY_KEY = "healthyKey"


@pytest.fixture
def av():
    return _assetVersions


def _typed(row):
    """A DynamoDB-typed item as the low-level client returns it."""
    return {k: {"S": v} for k, v in row.items()}


def _paginating_client(items):
    """A low-level client whose `get_paginator('query')` chain answers with `items`.

    The handler chains `.build_full_result()` onto `paginate(...)` and then reads `.get("Items")`,
    so the stub has to answer at that depth. A shallower stub raises AttributeError inside the
    loop, which the enclosing `except Exception` swallows -- the read would return [] and every
    assertion below would pass vacuously. Each class carries a positive control for that.
    """
    client = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value.build_full_result.return_value = {"Items": list(items)}
    client.get_paginator.return_value = paginator
    return client


def _warning_text(log):
    return " | ".join(str(call) for call in log.warning.call_args_list)


@pytest.mark.unit
class TestTheVersionReadReportsAnAbsentAttributeAsNull:
    """get_asset_metadata_version must not invent the type the metadata GET reports as null."""

    @staticmethod
    def _rows():
        return [
            # No metadataValueType -- what the snapshot write leaves when the source row had none.
            _typed({"type:filePath:metadataKey": f"metadata:/:{LEGACY_KEY}",
                    "metadataValue": "kept"}),
            _typed({"type:filePath:metadataKey": f"metadata:/:{HEALTHY_KEY}",
                    "metadataValue": "also-kept", "metadataValueType": "string"}),
        ]

    def _run(self, av, rows):
        log = MagicMock()
        with patch.object(av, "dynamodb_client", _paginating_client(rows)), \
                patch.object(av, "asset_file_metadata_versions_table", MagicMock()), \
                patch.object(av, "logger", log):
            items = av.get_asset_metadata_version(DB, ASSET, VERSION)
        return {item.metadataKey: item for item in items}, log

    def test_the_well_formed_row_is_still_returned(self, av):
        """Positive control for the whole class: the read really ran and produced rows.

        Without it, a stub of the wrong shape would make every assertion below pass against an
        empty list -- the exact way this class of bug hides in its own harness.
        """
        by_key, _log = self._run(av, self._rows())
        assert HEALTHY_KEY in by_key, f"the read returned nothing usable: {by_key}"
        assert by_key[HEALTHY_KEY].metadataValueType == "string"
        assert by_key[HEALTHY_KEY].metadataValue == "also-kept"

    def test_no_type_is_fabricated_for_a_row_that_had_none(self, av):
        """The defect: "string" here versus null from the metadata GET, for one stored row."""
        by_key, _log = self._run(av, self._rows())
        assert LEGACY_KEY in by_key, f"the legacy row was dropped from the version: {by_key}"
        assert by_key[LEGACY_KEY].metadataValueType is None, (
            "the absent type was defaulted, so this key reads differently from the metadata GET")

    def test_the_value_the_legacy_row_does_hold_survives(self, av):
        """Kept rather than dropped: the row's own value is not collateral of the missing type."""
        by_key, _log = self._run(av, self._rows())
        assert by_key[LEGACY_KEY].metadataValue == "kept"

    def test_an_absent_value_reads_as_null_too(self, av):
        """Both tolerated attributes behave alike; "" is a value the row does not carry."""
        rows = [_typed({"type:filePath:metadataKey": f"metadata:/:{LEGACY_KEY}"})]
        by_key, _log = self._run(av, rows)
        assert by_key[LEGACY_KEY].metadataValue is None
        assert by_key[LEGACY_KEY].metadataValueType is None

    def test_the_absence_is_reported_with_the_key_and_the_version(self, av):
        """An operator needs the key and the version to find the row that needs repairing."""
        _by_key, log = self._run(av, self._rows())
        text = _warning_text(log)
        assert "metadataValueType" in text, f"the absence was not reported at all: {text}"
        assert LEGACY_KEY in text, f"the reported line does not name the key: {text}"
        assert f"{DB}:{ASSET}:{VERSION}" in text, (
            f"the reported line does not name the version: {text}")

    def test_two_complete_rows_report_nothing(self, av):
        """Control: the report must be caused by the absent attribute, not emitted always."""
        rows = [
            _typed({"type:filePath:metadataKey": f"metadata:/:{LEGACY_KEY}",
                    "metadataValue": "a", "metadataValueType": "string"}),
            _typed({"type:filePath:metadataKey": f"metadata:/:{HEALTHY_KEY}",
                    "metadataValue": "b", "metadataValueType": "number"}),
        ]
        by_key, log = self._run(av, rows)
        assert set(by_key) == {LEGACY_KEY, HEALTHY_KEY}
        assert log.warning.call_args_list == [], _warning_text(log)

    def test_the_null_survives_the_version_response_model(self, av):
        """AssetVersionMetadataItemModel declares both fields as str, non-optional.

        The version GET nests these items in AssetVersionResponseModel (assetVersions.py:1966) and
        serializes with `.dict()`, so a tolerant read that could not be serialized would still be a
        500. This is the assertion that the placeholder-then-copy conversion reaches the caller as
        null rather than as the placeholder.

        Both classes are taken off the module under test rather than imported here. Pydantic v1
        re-validates a nested item only when it is NOT an instance of the declared class, and a
        second import of `assetsV3` under a different module name produces a second, unrelated
        class -- so a locally imported model would exercise the re-validating path the deployed
        handler never takes, and would fail on a correct fix.
        """
        by_key, _log = self._run(av, self._rows())
        response = av.AssetVersionResponseModel(
            assetId=ASSET, assetVersionId=VERSION, databaseId=DB,
            dateCreated="2026-01-01T00:00:00", comment="",
            versionedMetadata=list(by_key.values()))

        body = json.loads(json.dumps(response.dict(), default=str))
        serialized = {item["metadataKey"]: item for item in body["versionedMetadata"]}
        assert serialized[LEGACY_KEY]["metadataValueType"] is None, serialized[LEGACY_KEY]
        assert serialized[LEGACY_KEY]["metadataValue"] == "kept"
        assert serialized[HEALTHY_KEY]["metadataValueType"] == "string"


def _snapshot(item_type, key, value, value_type, file_path="/"):
    """One version-snapshot item, built the way get_asset_metadata_version now returns them.

    An absent attribute is set through `copy(update=...)` because the model declares the field as
    str; that is the same route the handler takes, so the fixture cannot be more permissive than
    the code under test. The class is taken off the module under test for the reason given in
    test_the_null_survives_the_version_response_model.
    """
    item = _assetVersions.AssetVersionMetadataItemModel(
        type=item_type, filePath=file_path, metadataKey=key,
        metadataValue=value if value is not None else "",
        metadataValueType=value_type if value_type is not None else "string")
    update = {}
    if value is None:
        update["metadataValue"] = None
    if value_type is None:
        update["metadataValueType"] = None
    return item.copy(update=update) if update else item


@pytest.mark.unit
class TestTheRevertRestoresTheSnapshotShape:
    """revert_asset_metadata_version must omit an attribute the snapshot row does not carry.

    `{'S': None}` is rejected by boto3 parameter validation before the request leaves the process,
    and the restore batch is written inside a try that logs and returns False -- so one legacy row
    would have failed the revert for the whole asset.
    """

    def _run(self, av, snapshot):
        written = []

        def batch_write_item(**kwargs):
            for table_requests in kwargs.get("RequestItems", {}).values():
                for request in table_requests:
                    if "PutRequest" in request:
                        written.append(request["PutRequest"]["Item"])
            return {"UnprocessedItems": {}}

        # No current rows to delete, so the delete phase is a no-op and every captured put is a
        # restore.
        client = _paginating_client([])
        client.batch_write_item.side_effect = batch_write_item

        files = [{"relativeKey": FILE_PATH.lstrip("/")}]
        with patch.object(av, "dynamodb_client", client), \
                patch.object(av, "asset_file_metadata_versions_table", MagicMock()), \
                patch.object(av, "asset_file_metadata_table", MagicMock()), \
                patch.object(av, "file_attribute_table", MagicMock()), \
                patch.object(av, "get_asset_metadata_version",
                             MagicMock(return_value=list(snapshot))):
            ok = av.revert_asset_metadata_version(DB, ASSET, "v1", VERSION, files)

        return ok, {item["metadataKey"]["S"] if "metadataKey" in item
                    else item["attributeKey"]["S"]: item for item in written}

    @staticmethod
    def _metadata_snapshot():
        return [
            _snapshot("metadata", LEGACY_KEY, "kept", None),
            _snapshot("metadata", HEALTHY_KEY, "also-kept", "string"),
        ]

    def test_the_revert_succeeds_and_restores_both_rows(self, av):
        """Positive control and the load-bearing assertion in one.

        Before the fix a real client rejected the batch and the function returned False, losing
        every row -- so "the legacy row is tolerated" has to be stated as "both rows arrived".
        """
        ok, by_key = self._run(av, self._metadata_snapshot())
        assert ok is True
        assert set(by_key) == {LEGACY_KEY, HEALTHY_KEY}, f"rows were lost on restore: {by_key}"

    def test_the_absent_attribute_is_omitted_rather_than_written_as_none(self, av):
        _ok, by_key = self._run(av, self._metadata_snapshot())
        assert "metadataValueType" not in by_key[LEGACY_KEY], by_key[LEGACY_KEY]
        assert by_key[LEGACY_KEY]["metadataValue"] == {"S": "kept"}

    def test_no_restored_attribute_carries_a_none(self, av):
        """The condition boto3 itself rejects, stated directly over everything written."""
        _ok, by_key = self._run(av, self._metadata_snapshot())
        assert by_key, "nothing was written, so this proves nothing"
        for key, item in by_key.items():
            for attribute, typed_value in item.items():
                assert list(typed_value.values())[0] is not None, (key, attribute, item)

    def test_the_complete_row_keeps_both_attributes(self, av):
        """Control: the omission must be caused by the absent attribute, not applied to all."""
        _ok, by_key = self._run(av, self._metadata_snapshot())
        assert by_key[HEALTHY_KEY]["metadataValueType"] == {"S": "string"}
        assert by_key[HEALTHY_KEY]["metadataValue"] == {"S": "also-kept"}

    def test_an_attribute_row_omits_its_absent_value_type_too(self, av):
        """The attribute half restores under the attribute* names, and moved with the metadata half."""
        snapshot = [
            _snapshot("attribute", LEGACY_KEY, "kept", None, file_path=FILE_PATH),
            _snapshot("attribute", HEALTHY_KEY, "also-kept", "string", file_path=FILE_PATH),
        ]
        ok, by_key = self._run(av, snapshot)
        assert ok is True
        assert set(by_key) == {LEGACY_KEY, HEALTHY_KEY}, f"rows were lost on restore: {by_key}"
        assert "attributeValueType" not in by_key[LEGACY_KEY], by_key[LEGACY_KEY]
        assert by_key[LEGACY_KEY]["attributeValue"] == {"S": "kept"}
        assert by_key[HEALTHY_KEY]["attributeValueType"] == {"S": "string"}


@pytest.mark.unit
class TestTheAttributeSnapshotWriteDoesNotInventAType:
    """save_asset_metadata_version's attribute half must not record a type the row never had.

    A version snapshot is immutable history: a default written here cannot be repaired later by
    fixing the source row. The metadata half already omits both attributes; this is its sibling.
    """

    @staticmethod
    def _rows():
        return [
            # Neither attributeValue nor attributeValueType -- the pre-upgrade attribute shape.
            _typed({"attributeKey": LEGACY_KEY}),
            _typed({"attributeKey": HEALTHY_KEY, "attributeValue": "kept",
                    "attributeValueType": "number"}),
        ]

    def _run(self, av, rows):
        written = []

        def batch_write_item(**kwargs):
            for table_requests in kwargs.get("RequestItems", {}).values():
                for request in table_requests:
                    if "PutRequest" in request:
                        written.append(request["PutRequest"]["Item"])
            return {"UnprocessedItems": {}}

        client = _paginating_client(rows)
        client.batch_write_item.side_effect = batch_write_item

        # Both halves read through the same paginator, so the metadata table is switched off to
        # leave only the attribute half running against these rows.
        with patch.object(av, "dynamodb_client", client), \
                patch.object(av, "asset_file_metadata_versions_table", MagicMock()), \
                patch.object(av, "asset_file_metadata_table", None), \
                patch.object(av, "file_attribute_table", MagicMock()):
            ok = av.save_asset_metadata_version(DB, ASSET, VERSION, [])

        return ok, {item["metadataKey"]["S"]: item for item in written}

    def test_both_attribute_rows_are_snapshotted(self, av):
        """Positive control: the attribute half ran and wrote rows."""
        ok, by_key = self._run(av, self._rows())
        assert ok is True
        assert set(by_key) == {LEGACY_KEY, HEALTHY_KEY}, f"rows were lost: {by_key}"

    def test_no_type_is_recorded_for_a_row_that_had_none(self, av):
        _ok, by_key = self._run(av, self._rows())
        assert "metadataValueType" not in by_key[LEGACY_KEY], by_key[LEGACY_KEY]
        assert "metadataValue" not in by_key[LEGACY_KEY], by_key[LEGACY_KEY]

    def test_the_complete_row_records_what_it_carries(self, av):
        """Control: the omission is driven by the row, not applied to every attribute."""
        _ok, by_key = self._run(av, self._rows())
        assert by_key[HEALTHY_KEY]["metadataValue"] == {"S": "kept"}
        assert by_key[HEALTHY_KEY]["metadataValueType"] == {"S": "number"}

    def test_a_legacy_row_under_the_old_field_names_is_read_too(self, av):
        """The branch accepts metadata* names on an attribute row; that fallback still applies."""
        rows = [_typed({"metadataKey": LEGACY_KEY, "metadataValue": "kept"})]
        ok, by_key = self._run(av, rows)
        assert ok is True
        assert by_key[LEGACY_KEY]["metadataValue"] == {"S": "kept"}
        assert "metadataValueType" not in by_key[LEGACY_KEY], by_key[LEGACY_KEY]
