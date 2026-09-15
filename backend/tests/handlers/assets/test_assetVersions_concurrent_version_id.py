# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Two overlapping version writes on one asset must not mint the same assetVersionId.

`create_asset_version` and `revert_asset_version` derived the next version id from the asset
record's `currentVersionId` and then wrote three unconditional records: the per-file snapshot rows,
the metadata snapshot rows, and the version-metadata record. Two callers that both read
`currentVersionId='5'` both derived `'6'`, so the second `put_item` replaced the first caller's
version record (its comment and versionAlias) while the per-file snapshot rows -- keyed by
`fileKey` -- merged into a single hybrid snapshot that a later revert would restore. Both callers
received a 200.

The load-bearing assertion is that the two calls land on DIFFERENT version ids, and that the file
snapshot each caller wrote went to its own version. "It did not raise" is satisfied equally by the
defect, which returned success to both callers.

The version id is reserved BEFORE any snapshot row is written, so the losing caller never puts rows
under the winner's version -- checking only the version-metadata record would leave the merged
snapshot in place.
"""

import pytest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before the
# root conftest's autouse fixture installs its non-package placeholder (see S27-TEST-001). Every
# mutation below uses `patch.object`, which restores itself, so no reload is needed.
from backend.backend.handlers.assets import assetVersions as _assetVersions

DB = "db1"
ASSET = "asset-1"
BUCKET = "asset-bucket"
PREFIX = "asset-1/"


@pytest.fixture
def av():
    return _assetVersions


class ConditionalVersionsTable:
    """`asset_versions_table` stub that honours `attribute_not_exists(assetVersionId)`.

    A `put_item` carrying the condition fails once some record already holds that version id,
    which is what makes a lost update observable: without the condition both writes land on the
    same key and the first one is simply gone.
    """

    def __init__(self, taken=()):
        self.taken = set(taken)
        self.written = []

    def put_item(self, **kwargs):
        item = kwargs["Item"]
        version_id = item["assetVersionId"]
        if kwargs.get("ConditionExpression") and version_id in self.taken:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException",
                           "Message": "The conditional request failed"}},
                "PutItem")
        self.taken.add(version_id)
        self.written.append(item)
        return {}

    @property
    def written_version_ids(self):
        return [item["assetVersionId"] for item in self.written]

    def comments_by_version(self):
        """Version id -> the comments written against it, in write order.

        More than one distinct comment under a single id is the lost update itself.
        """
        by_version = {}
        for item in self.written:
            by_version.setdefault(item["assetVersionId"], []).append(item.get("comment"))
        return by_version


def _asset(current_version="5"):
    return {
        "databaseId": DB, "assetId": ASSET, "assetName": "N", "description": "d",
        "bucketId": "bucket-1", "assetLocation": {"Key": PREFIX},
        "currentVersionId": current_version,
    }


def _create_request(comment, alias=None):
    request = MagicMock()
    request.useLatestFiles = True
    request.files = []
    request.comment = comment
    request.versionAlias = alias
    return request


def _revert_request(comment, source_version="2"):
    request = MagicMock()
    request.assetVersionId = source_version
    request.revertMetadata = False
    request.comment = comment
    return request


def _s3_files():
    return [{
        "relativeKey": "/model.glb", "versionId": "s3v1", "size": 10,
        "lastModified": "2026-01-01T00:00:00", "etag": "e1",
    }]


def _run_writes(av, versions_table, asset_current_version, runner, list_files=None):
    """Run `runner(snapshot_spy)` with every side path stubbed except the version-id derivation.

    `asset_table.get_item` keeps reporting the ORIGINAL currentVersionId, which is the state a
    concurrent winner leaves behind: it only advances its own record once its version write
    completes, so the loser cannot learn the taken id from the counter.

    Only names that exist both before and after the fix are patched, so a failure here is the
    duplicate version id rather than a missing attribute.
    """
    snapshot_spy = MagicMock(return_value=True)
    asset_table = MagicMock()
    asset_table.get_item.return_value = {"Item": _asset(asset_current_version)}

    with patch.object(av, "asset_versions_table", versions_table), \
            patch.object(av, "asset_table", asset_table), \
            patch.object(av, "get_asset_s3_location", return_value=(BUCKET, PREFIX)), \
            patch.object(av, "list_s3_files_with_versions",
                         list_files or MagicMock(return_value=_s3_files())), \
            patch.object(av, "does_file_version_exist", return_value=True), \
            patch.object(av, "copy_s3_object_version", return_value="s3v-new"), \
            patch.object(av, "delete_assetAuxiliary_files", MagicMock()), \
            patch.object(av, "save_asset_file_versions", snapshot_spy), \
            patch.object(av, "save_asset_metadata_version", MagicMock(return_value=True)), \
            patch.object(av, "mark_assetVersion_as_current", MagicMock(return_value=True)), \
            patch.object(av, "update_asset_current_version_reference", MagicMock(return_value=True)), \
            patch.object(av, "send_subscription_email", MagicMock()):
        result = runner()

    return result, snapshot_spy


def _snapshot_version_ids(snapshot_spy):
    """The assetVersionId each `save_asset_file_versions` call snapshotted into."""
    return [call.args[2] for call in snapshot_spy.call_args_list]


@pytest.mark.unit
class TestOverlappingCreateVersionCalls:
    """Both callers read currentVersionId='5'; they must not both write version '6'."""

    def _two_creates(self, av):
        versions_table = ConditionalVersionsTable()

        def runner():
            # Two SEPARATE asset dicts, both carrying '5': the two readers of the same record.
            first = av.create_asset_version(
                DB, ASSET, _create_request("first", alias="RC1"), {"tokens": ["alice"]})
            second = av.create_asset_version(
                DB, ASSET, _create_request("second", alias="RC2"), {"tokens": ["bob"]})
            return first, second

        with patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset("5")):
            (first, second), snapshot_spy = _run_writes(av, versions_table, "5", runner)

        return first, second, versions_table, snapshot_spy

    def test_the_two_calls_report_different_version_ids(self, av):
        first, second, _table, _spy = self._two_creates(av)
        assert first.assetVersionId != second.assetVersionId, (
            "both createVersion calls reported the same assetVersionId "
            f"{first.assetVersionId!r}, so one caller's version was silently replaced")

    def test_no_two_distinct_comments_land_on_one_version(self, av):
        """The version-metadata record is the part the defect overwrote outright."""
        _first, _second, table, _spy = self._two_creates(av)
        for version_id, comments in table.comments_by_version().items():
            assert len(set(comments)) == 1, (
                f"version {version_id} was written with more than one comment {comments}: the "
                "later write replaced the earlier caller's version record")
        assert {"first", "second"}.issubset(
            {comment for comments in table.comments_by_version().values() for comment in comments}), (
            "one of the two callers' comments never reached the versions table")

    def test_each_caller_snapshots_into_its_own_version(self, av):
        """The per-file rows are keyed by fileKey, so a shared version id MERGES the two
        snapshots -- a hybrid a later revert would restore. Asserting on the version record
        alone would leave that in place."""
        _first, _second, _table, snapshot_spy = self._two_creates(av)
        snapshot_versions = _snapshot_version_ids(snapshot_spy)
        assert len(snapshot_versions) == len(set(snapshot_versions)), (
            f"both callers wrote their file snapshot into the same version {snapshot_versions}")

    def test_the_second_call_steps_past_the_taken_id(self, av):
        """The counter is still '5' while the winner is in flight, so the loser has to step past
        the id it lost rather than re-deriving it."""
        first, second, _table, _spy = self._two_creates(av)
        assert {first.assetVersionId, second.assetVersionId} == {"6", "7"}


@pytest.mark.unit
class TestOverlappingRevertVersionCalls:
    """revert_asset_version derives the next id the same way and needs the same protection."""

    def _two_reverts(self, av):
        versions_table = ConditionalVersionsTable()

        def runner():
            first = av.revert_asset_version(
                DB, ASSET, _revert_request("first"), {"tokens": ["alice"]})
            second = av.revert_asset_version(
                DB, ASSET, _revert_request("second"), {"tokens": ["bob"]})
            return first, second

        with patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset("5")), \
                patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "2"}), \
                patch.object(av, "get_asset_file_versions", return_value={"files": [
                    {"relativeKey": "/model.glb", "versionId": "s3v-old", "size": 10, "etag": "e1"}]}):
            (first, second), snapshot_spy = _run_writes(av, versions_table, "5", runner)

        return first, second, versions_table, snapshot_spy

    def test_the_two_reverts_report_different_version_ids(self, av):
        first, second, _table, _spy = self._two_reverts(av)
        assert first.assetVersionId != second.assetVersionId, (
            "both revert calls reported the same assetVersionId "
            f"{first.assetVersionId!r}, so one revert's snapshot was silently replaced")

    def test_each_revert_snapshots_into_its_own_version(self, av):
        _first, _second, _table, snapshot_spy = self._two_reverts(av)
        snapshot_versions = _snapshot_version_ids(snapshot_spy)
        assert len(snapshot_versions) == len(set(snapshot_versions))


@pytest.mark.unit
class TestADriftedCounterConverges:
    """The asset's counter can lag the versions that exist -- every create that reserved an id and
    then failed leaves one behind without advancing the counter. Stepping past them one attempt at
    a time exhausts the attempt bound, which would leave the asset permanently unable to create a
    version; the highest recorded id has to bound the next one too."""

    def _versions_table_holding(self, version_ids):
        """A table whose listing reports the records it already holds."""
        table = ConditionalVersionsTable(taken=version_ids)
        table.query = MagicMock(return_value={
            "Items": [{"assetVersionId": version_id, "isCurrentVersion": False}
                      for version_id in sorted(version_ids)]})
        return table

    def _create_over(self, av, versions_table):
        def runner():
            return av.create_asset_version(
                DB, ASSET, _create_request("drifted"), {"tokens": ["alice"]})

        with patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset("5")):
            return _run_writes(av, versions_table, "5", runner)

    def test_a_drift_past_the_attempt_bound_still_reserves(self, av):
        # The counter says '5' while versions 6..99 exist: 94 taken ids against 10 attempts.
        table = self._versions_table_holding({str(n) for n in range(6, 100)})
        response, _spy = self._create_over(av, table)

        assert response.assetVersionId == "100", (
            "the reservation stepped past the taken ids one at a time and exhausted its attempt "
            f"bound instead of jumping past the highest recorded version; got "
            f"{response.assetVersionId!r}")
        assert table.written_version_ids == ["100"], (
            f"an existing version record was overwritten: {table.written_version_ids}")

    def test_no_existing_version_is_overwritten_when_the_bound_is_reached(self, av):
        """The bound still has to hold: with no listing to learn from -- a failed read -- the walk
        must give up rather than land on a version that exists."""
        table = ConditionalVersionsTable(taken={str(n) for n in range(6, 100)})
        # No .query attribute, so get_all_asset_versions degrades to an empty list.

        def runner():
            with pytest.raises(av.VAMSGeneralErrorResponse):
                av.create_asset_version(
                    DB, ASSET, _create_request("drifted"), {"tokens": ["alice"]})
            return None

        with patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset("5")):
            _result, snapshot_spy = _run_writes(av, table, "5", runner)

        assert not table.written, (
            "an existing version record was overwritten while the counter was behind: "
            f"{table.written_version_ids}")
        snapshot_spy.assert_not_called()


@pytest.mark.unit
class TestAFailedCreateDoesNotMintAVersion:
    """The reservation writes a real version record, so it must not run ahead of the read-only
    work that can fail: a version record left behind carries the request's versionAlias, and a
    retry with that same alias leaves two versions sharing it, which makes every later alias
    lookup ambiguous."""

    def test_an_s3_listing_failure_leaves_no_version_record(self, av):
        versions_table = ConditionalVersionsTable()

        def runner():
            with pytest.raises(RuntimeError):
                av.create_asset_version(
                    DB, ASSET, _create_request("aborted", alias="RC1"), {"tokens": ["alice"]})
            return None

        with patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset("5")):
            _result, snapshot_spy = _run_writes(
                av, versions_table, "5", runner,
                list_files=MagicMock(side_effect=RuntimeError("s3 listing unavailable")))

        assert versions_table.written == [], (
            "the failed create left a version record behind, so its versionAlias is taken and a "
            f"retry reusing that alias makes it ambiguous: {versions_table.written_version_ids}")
        snapshot_spy.assert_not_called()


@pytest.mark.unit
class TestSingleWriterStillWorks:
    """Positive control. Without this a reservation that rejects EVERY write is
    indistinguishable from a correct one."""

    def test_one_create_version_still_succeeds_and_numbers_from_the_counter(self, av):
        versions_table = ConditionalVersionsTable()

        def runner():
            return av.create_asset_version(
                DB, ASSET, _create_request("only caller", alias="RC1"), {"tokens": ["alice"]})

        with patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset("5")):
            response, snapshot_spy = _run_writes(av, versions_table, "5", runner)

        assert response.success is True
        assert response.assetVersionId == "6"
        assert versions_table.written_version_ids == ["6"], (
            "a single writer must write exactly one version record")
        assert versions_table.written[0]["comment"] == "only caller"
        assert versions_table.written[0]["versionAlias"] == "RC1"
        assert _snapshot_version_ids(snapshot_spy) == ["6"]

    def test_a_first_version_numbers_from_one(self, av):
        """An asset with no currentVersionId still starts at '1'."""
        versions_table = ConditionalVersionsTable()
        asset = _asset("5")
        del asset["currentVersionId"]

        def runner():
            return av.create_asset_version(
                DB, ASSET, _create_request("first ever"), {"tokens": ["alice"]})

        with patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: dict(asset)):
            response, _spy = _run_writes(av, versions_table, "0", runner)

        assert response.assetVersionId == "1"

    def test_a_v_prefixed_counter_is_still_parsed(self, av):
        """A legacy 'v3' currentVersionId numbers the next version '4', not '1'."""
        versions_table = ConditionalVersionsTable()

        def runner():
            return av.create_asset_version(
                DB, ASSET, _create_request("legacy counter"), {"tokens": ["alice"]})

        with patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset("v3")):
            response, _spy = _run_writes(av, versions_table, "v3", runner)

        assert response.assetVersionId == "4"
