# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A failed version listing is not an empty version set (S2-BACKEND-087).

`get_all_asset_versions` collapsed every exception into `[]`, which is the answer for an
asset that genuinely has no versions. The two states are indistinguishable to a caller, and
each caller reads the listing for something a wrong empty answer silently breaks:

* `resolve_asset_version_id_from_alias` reports "No asset versions found for asset" — a 400
  telling the caller its request was wrong — for a transient DynamoDB failure;
* `mark_assetVersion_as_current` sees no versions to clear, so it flags the new version by
  key and leaves the previously current record still flagged, i.e. two records reading as
  current, with the read failure never surfaced;
* `assetFiles.get_file_info` builds an empty archived-version set, so an archived version is
  omitted from the archive filter.

The failure is therefore raised and the empty listing is returned, which keeps the two
answers distinct. `reserve_next_asset_version` is the one caller for which the listing is
only a hint — it uses the recorded IDs to skip past a drifted counter, and the conditional
write is what keeps it off an ID that exists — so it absorbs the failure locally.

Each failure case is paired with the same call over a HEALTHY empty listing, so a change
that made every empty answer raise would fail the pair.
"""

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before
# the root conftest's autouse fixture installs its non-package placeholder.
from backend.backend.handlers.assets import assetVersions as _assetVersions

DB = "db1"
ASSET = "asset-1"
PK = f"{DB}:{ASSET}"

THROTTLE = ClientError(
    {"Error": {"Code": "ProvisionedThroughputExceededException",
               "Message": "throughput exceeded"}},
    "Query",
)


@pytest.fixture
def av():
    return _assetVersions


def _version(version_id, alias="", archived=False, is_current=False):
    return {
        "databaseId": DB, "databaseId:assetId": PK, "assetId": ASSET,
        "assetVersionId": version_id, "isCurrentVersion": is_current,
        "isArchived": archived, "versionAlias": alias,
    }


def _failing_table():
    """A versions table whose read fails; writes on it are still observable."""
    table = MagicMock()
    table.query.side_effect = THROTTLE
    return table


def _empty_table():
    """A healthy versions table holding no version records."""
    table = MagicMock()
    table.query.return_value = {"Items": []}
    return table


@pytest.mark.unit
class TestAFailedListingIsRaised:
    """The read failure reaches the caller instead of arriving as an empty set."""

    def test_get_all_asset_versions_raises_the_read_failure(self, av):
        with patch.object(av, "asset_versions_table", _failing_table()):
            with pytest.raises(ClientError) as excinfo:
                av.get_all_asset_versions(DB, ASSET)

        assert excinfo.value.response["Error"]["Code"] == (
            "ProvisionedThroughputExceededException")

    def test_an_empty_listing_is_still_an_empty_list(self, av):
        """Positive control: the empty answer is not collateral damage of the raise."""
        with patch.object(av, "asset_versions_table", _empty_table()):
            assert av.get_all_asset_versions(DB, ASSET) == []

    def test_a_populated_listing_is_still_returned(self, av):
        """Positive control: a healthy read is unaffected."""
        table = MagicMock()
        table.query.return_value = {"Items": [_version("1"), _version("2")]}
        with patch.object(av, "asset_versions_table", table):
            versions = av.get_all_asset_versions(DB, ASSET)

        assert [v["assetVersionId"] for v in versions] == ["2", "1"]


@pytest.mark.unit
class TestAliasResolutionDistinguishesFailureFromNoVersions:
    """A throttled read must not be reported as "this asset has no versions"."""

    def test_a_failed_read_propagates_rather_than_reporting_no_versions(self, av):
        with patch.object(av, "asset_versions_table", _failing_table()):
            with pytest.raises(ClientError):
                av.resolve_asset_version_id_from_alias(DB, ASSET, "RC1")

    def test_an_asset_with_no_versions_still_reports_no_versions(self, av):
        """Positive control: the 4xx answer for a genuinely version-less asset is kept."""
        with patch.object(av, "asset_versions_table", _empty_table()):
            with pytest.raises(av.VAMSGeneralErrorResponse) as excinfo:
                av.resolve_asset_version_id_from_alias(DB, ASSET, "RC1")

        assert "No asset versions found" in str(excinfo.value)


@pytest.mark.unit
class TestCurrentVersionFlagIsNotRewrittenOnAFailedRead:
    """With the version set unknown, no flag may be written: the previously current record
    cannot be cleared, so flagging the new one leaves two records reading as current."""

    @staticmethod
    def _flag_updates(table):
        """assetVersionId -> the isCurrentVersion value written for it."""
        return {
            call.kwargs["Key"]["assetVersionId"]:
                call.kwargs["ExpressionAttributeValues"][":is_current"]
            for call in table.update_item.call_args_list
        }

    def test_a_failed_read_writes_no_flag_and_reports_failure(self, av):
        table = _failing_table()
        logger = MagicMock()
        with patch.object(av, "asset_versions_table", table), \
                patch.object(av, "logger", logger):
            result = av.mark_assetVersion_as_current(DB, ASSET, "6")

        assert result is False
        assert self._flag_updates(table) == {}, (
            "a flag was written from an unknown version set, so the previously current record "
            "was left flagged current alongside the new one")
        assert logger.exception.called, (
            "the read failure was not logged with its stack trace")

    def test_a_healthy_listing_missing_the_target_still_flags_it_by_key(self, av):
        """Positive control: the eventual-consistency remedy is only disabled for a FAILED
        read, not for a listing that legitimately does not yet carry the new record."""
        table = _empty_table()
        with patch.object(av, "asset_versions_table", table), \
                patch.object(av, "logger", MagicMock()):
            result = av.mark_assetVersion_as_current(DB, ASSET, "6")

        assert self._flag_updates(table).get("6") is True
        assert result is False


@pytest.mark.unit
class TestVersionIdReservationAbsorbsAFailedListing:
    """The recorded IDs are a hint for the reservation walk, not a correctness dependency."""

    def test_the_walk_still_reserves_when_the_listing_fails(self, av):
        reserved = []

        def save(*args, **kwargs):
            candidate = args[1]
            reserved.append(candidate)
            # The first derived ID is already taken, which is what drives the walk into the
            # branch that consults the recorded version IDs.
            if candidate == "6":
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException",
                               "Message": "exists"}},
                    "PutItem")
            return True

        asset_table = MagicMock()
        asset_table.get_item.return_value = {"Item": {"currentVersionId": "5"}}

        with patch.object(av, "save_asset_version_metadata", side_effect=save), \
                patch.object(av, "asset_table", asset_table), \
                patch.object(av, "get_all_asset_versions", side_effect=THROTTLE), \
                patch.object(av, "logger", MagicMock()):
            candidate = av.reserve_next_asset_version(
                DB, ASSET, {"currentVersionId": "5"}, "c", "alice", None)

        assert candidate == "7", (
            f"a failed version listing aborted the reservation walk; tried {reserved}")
