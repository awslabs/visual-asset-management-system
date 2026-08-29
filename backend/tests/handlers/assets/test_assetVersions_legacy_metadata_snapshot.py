# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A stored metadata row written by an earlier release must not empty a version snapshot.

`save_asset_metadata_version` built each version row by subscripting `deserialized['metadataValue']`
and `deserialized['metadataValueType']`. A row lacking either — the shape a pre-upgrade write leaves
behind — raised `KeyError` inside the metadata loop, and the enclosing `except Exception` logged it at
WARNING level and carried on to the attribute section. So the version was created with **no metadata at
all**, the function still returned True, and its caller recorded a successful snapshot.

That is permanent: an asset version is immutable history, so the metadata for that version is not
recoverable by repairing the row afterwards. It is a worse outcome than the 400 the same stored shape
caused on the metadata GET path, which at least refuses loudly.

The load-bearing assertion is that the OTHER rows are still written. "It did not raise" is satisfied
equally by a snapshot that silently dropped everything, which is exactly what the defect did.
"""

import pytest
from unittest.mock import MagicMock, patch

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before the root
# conftest's autouse fixture installs its non-package placeholder (see S27-TEST-001).
#
# The import must be HERE, at module scope, and the fixture must hand back this object. Importing it
# inside the fixture instead fails with "'backend.backend.handlers' is not a package", because a fixture
# body runs after the autouse fixture has installed the placeholder — and reloading the module fails
# differently again, with an ImportError for `CasbinEnforcer`, because the reload re-runs its imports
# against that same placeholder. Every mutation below uses `patch.object`, which restores itself, so no
# reload is needed.
from backend.backend.handlers.assets import assetVersions as _assetVersions

DB = "db1"
ASSET = "asset1"
VERSION = "v2"


def _typed(row):
    """A DynamoDB-typed item as the low-level client returns it."""
    return {k: {"S": v} for k, v in row.items()}


@pytest.fixture
def av():
    return _assetVersions


@pytest.mark.unit
class TestALegacyRowDoesNotEmptyTheSnapshot:
    """One row missing `metadataValueType` must cost only that attribute, not the whole snapshot."""

    @staticmethod
    def _rows():
        legacy = {
            "metadataKey": "legacyKey",
            "metadataValue": "kept",
            # No metadataValueType — the pre-upgrade shape.
        }
        healthy = {
            "metadataKey": "healthyKey",
            "metadataValue": "also-kept",
            "metadataValueType": "string",
        }
        return [_typed(legacy), _typed(healthy)]

    def _run(self, av):
        """Drive the snapshot with both rows and return every version item it wrote."""
        written = []

        def batch_write_item(**kwargs):
            for table_requests in kwargs.get("RequestItems", {}).values():
                for request in table_requests:
                    if "PutRequest" in request:
                        written.append(request["PutRequest"]["Item"])
            return {"UnprocessedItems": {}}

        client = MagicMock()
        client.batch_write_item.side_effect = batch_write_item
        paginator = MagicMock()
        # The handler chains `.build_full_result()` onto `paginate(...)` and then reads `.get("Items")`,
        # so the stub has to answer at that depth. Returning a plain mapping from `paginate` instead
        # raises AttributeError inside the loop — which the enclosing `except Exception` swallows as a
        # warning, exactly as it swallowed the KeyError this file exists to prevent. The bug under test
        # can therefore hide a bug in its own harness; `test_the_snapshot_still_reports_success` is what
        # tells the two apart.
        paginator.paginate.return_value.build_full_result.return_value = {"Items": self._rows()}
        client.get_paginator.return_value = paginator

        # No files are being versioned, which keeps both rows asset-level: neither carries a
        # `databaseId:assetId:filePath` attribute, so the handler resolves their filePath to "/" and
        # asset-level metadata is always included regardless of the versioned-file set.
        # `asset_file_metadata_table` gates the metadata fetch and is None in this environment (it is
        # built from an env var at import), so without patching it truthy the whole loop is skipped and
        # the function returns True having written nothing — which would make every assertion here pass
        # vacuously against a snapshot that never ran. `test_the_snapshot_still_reports_success` is the
        # control that catches exactly that.
        with patch.object(av, "dynamodb_client", client), \
                patch.object(av, "asset_file_metadata_table", MagicMock()), \
                patch.object(av, "asset_file_metadata_versions_table", MagicMock()), \
                patch.object(av, "file_attribute_table", None):
            ok = av.save_asset_metadata_version(DB, ASSET, VERSION, [])

        return ok, written

    def test_the_well_formed_row_is_still_snapshotted(self, av):
        """The distinguishing assertion. Before the fix the legacy row raised, the enclosing handler
        logged a warning, and NOTHING was written — so a check that merely tolerated the legacy row
        would have passed on an empty snapshot."""
        _ok, written = self._run(av)
        keys = {item.get("metadataKey", {}).get("S") for item in written}
        assert "healthyKey" in keys, (
            "the legacy row aborted the metadata loop, so a well-formed sibling row never reached the "
            f"version snapshot; written keys were {keys}"
        )

    def test_the_legacy_row_is_snapshotted_with_what_it_has(self, av):
        """Kept rather than dropped: a dropped row is invisible to the operator, which is worse than a
        row with one attribute missing. The value it DOES hold must survive."""
        _ok, written = self._run(av)
        legacy = next(
            (item for item in written if item.get("metadataKey", {}).get("S") == "legacyKey"), None
        )
        assert legacy is not None, "the legacy row was dropped from the snapshot entirely"
        assert legacy.get("metadataValue", {}).get("S") == "kept"

    def test_no_type_is_fabricated_for_a_row_that_had_none(self, av):
        """Inventing one would record a type the row never had as though the deployment had always
        held it — and a version snapshot is immutable history, so it could never be corrected.
        `get_asset_metadata_version` reports the omitted attribute as null on read (covered in
        test_assetVersions_legacy_metadata_version_read.py), so an absent attribute round-trips."""
        _ok, written = self._run(av)
        legacy = next(item for item in written if item.get("metadataKey", {}).get("S") == "legacyKey")
        assert "metadataValueType" not in legacy

    def test_the_snapshot_still_reports_success(self, av):
        """Positive control: the run must not have failed for some unrelated reason, or every
        assertion above would be vacuous."""
        ok, written = self._run(av)
        assert ok is True
        assert written, "nothing was written at all, so the assertions above prove nothing"
