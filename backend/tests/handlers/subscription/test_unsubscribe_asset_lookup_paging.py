#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Asset resolution behind the unsubscribe SNS cleanup (S2-BACKEND-039).

``unsubscribeService.get_asset`` resolves the SNS topic and databaseId of the asset a
subscription points at. Three properties are asserted here, each of which a single
filtered ``Scan`` cannot provide:

* **Paging.** A ``FilterExpression`` is applied AFTER the 1 MB page has been read, so a
  page carrying zero matching Items alongside a ``LastEvaluatedKey`` is the normal shape
  for "the match is on a later page". A lookup decided from one page answers "no such
  asset" for an asset that plainly exists, and the caller then dereferences that ``None``.
* **A not-found answer the caller survives.** ``delete_sns_subscriptions`` runs after the
  subscription row has already been rewritten, so an unresolvable asset must degrade to a
  logged skip rather than an ``AttributeError`` the handler reports as a 500.
* **The live asset, not an archived namesake.** Archiving rewrites a record under a
  ``{databaseId}#deleted`` partition while keeping the assetId, so an assetId can match an
  archived row in one database and a live row in another. The topic acted on must be the
  live record's; a filtered scan returns both rows and takes whichever the index yielded
  first.

Each negative case is paired with a positive control that resolves a single live asset, so
a lookup that answers ``None`` unconditionally fails the pair.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.subscription import unsubscribeService
from backend.tests.pagingStub import Pager

ASSET_ID = "test-asset-id"
LIVE_DATABASE_ID = "live-database-id"
LIVE_TOPIC = "arn:aws:sns:us-east-1:123456789012:AssetTopic-live"
ARCHIVED_TOPIC = "arn:aws:sns:us-east-1:123456789012:AssetTopic-archived"

# The cursor DynamoDB returns on a page whose filter matched nothing but which is not the
# end of the result set.
PAGE_ONE_CURSOR = {"assetId": ASSET_ID, "databaseId": "some-earlier-database"}


def _live_row(database_id=LIVE_DATABASE_ID, sns_topic=LIVE_TOPIC):
    return {
        "assetId": ASSET_ID,
        "databaseId": database_id,
        "assetName": "Test Asset",
        "snsTopic": sns_topic,
    }


def _archived_row():
    return {
        "assetId": ASSET_ID,
        "databaseId": f"{LIVE_DATABASE_ID}#deleted",
        "assetName": "Test Asset",
        "snsTopic": ARCHIVED_TOPIC,
    }


def _wire(monkeypatch, *pages, scan_items=None):
    """Point the module's asset reads at a scripted page sequence and record SNS calls.

    Both readers are stubbed. The paged index read is what the lookup issues; ``scan`` is
    stubbed with the FIRST scripted page so a lookup that reads only one page resolves
    against a realistic response rather than failing on an absent AWS endpoint.
    """
    table = MagicMock()
    pager = Pager(*pages, name="assetIdGSI lookup")
    table.query.side_effect = pager

    resource = MagicMock()
    resource.Table.return_value = table
    monkeypatch.setattr(unsubscribeService, "dynamodb", resource)

    client = MagicMock()
    client.scan.return_value = {
        "Items": scan_items if scan_items is not None else [],
        **({"LastEvaluatedKey": pages[0]["LastEvaluatedKey"]}
           if "LastEvaluatedKey" in pages[0] else {}),
    }
    monkeypatch.setattr(unsubscribeService, "dynamodb_client", client)

    sns = MagicMock()
    sns.list_subscriptions_by_topic.return_value = {"Subscriptions": []}
    monkeypatch.setattr(unsubscribeService, "sns_client", sns)

    return pager, sns, table


def _typed(row):
    """The low-level attribute-value shape a client-side read returns."""
    return {key: {"S": value} for key, value in row.items()}


@pytest.mark.unit
class TestAssetLookupPagesToExhaustion:
    """A match beyond the first page is found, not reported absent."""

    def test_asset_on_a_later_page_resolves(self, monkeypatch):
        pager, sns, _table = _wire(
            monkeypatch,
            {"Items": [], "LastEvaluatedKey": PAGE_ONE_CURSOR},
            {"Items": [_live_row()]},
        )

        asset_obj = unsubscribeService.get_asset(ASSET_ID)

        assert asset_obj == {"databaseId": LIVE_DATABASE_ID, "snsTopic": LIVE_TOPIC}
        pager.assert_paged_to_exhaustion()

    def test_asset_on_a_later_page_reaches_sns_cleanup(self, monkeypatch):
        pager, sns, _table = _wire(
            monkeypatch,
            {"Items": [], "LastEvaluatedKey": PAGE_ONE_CURSOR},
            {"Items": [_live_row()]},
        )

        unsubscribeService.delete_sns_subscriptions(
            ASSET_ID, ["test-user@example.com"], delete_sns=False)

        # The topic acted on, not how many times: an idempotent retry is not a defect here.
        assert sns.list_subscriptions_by_topic.called
        assert {call.kwargs.get("TopicArn")
                for call in sns.list_subscriptions_by_topic.call_args_list} == {LIVE_TOPIC}
        pager.assert_paged_to_exhaustion()

    def test_asset_on_the_first_page_needs_no_continuation(self, monkeypatch):
        """Positive control: a single-page result costs one read and still resolves."""
        pager, _sns, _table = _wire(monkeypatch, {"Items": [_live_row()]})

        asset_obj = unsubscribeService.get_asset(ASSET_ID)

        assert asset_obj == {"databaseId": LIVE_DATABASE_ID, "snsTopic": LIVE_TOPIC}
        assert pager.resumed_from == [], "a complete first page needs no continuation"


@pytest.mark.unit
class TestUnresolvableAssetDegradesInsteadOfRaising:
    """An assetId with no live asset is a logged skip, not an AttributeError."""

    def test_no_matching_row_returns_none(self, monkeypatch):
        _pager, _sns, _table = _wire(monkeypatch, {"Items": []})

        assert unsubscribeService.get_asset(ASSET_ID) is None

    def test_no_matching_row_skips_sns_cleanup_without_raising(self, monkeypatch):
        _pager, sns, _table = _wire(monkeypatch, {"Items": []})

        unsubscribeService.delete_sns_subscriptions(
            ASSET_ID, ["test-user@example.com"], delete_sns=False)

        sns.list_subscriptions_by_topic.assert_not_called()
        sns.unsubscribe.assert_not_called()
        sns.delete_topic.assert_not_called()

    def test_only_an_archived_row_is_not_a_live_asset(self, monkeypatch):
        _pager, sns, _table = _wire(monkeypatch, {"Items": [_archived_row()]})

        assert unsubscribeService.get_asset(ASSET_ID) is None

        unsubscribeService.delete_sns_subscriptions(
            ASSET_ID, ["test-user@example.com"], delete_sns=False)
        sns.list_subscriptions_by_topic.assert_not_called()

    def test_an_ambiguous_assetid_is_refused_rather_than_guessed(self, monkeypatch):
        """Two live databases hold this assetId, so no single record can be attributed."""
        first, second = _live_row(database_id="db-a"), _live_row(database_id="db-b")
        _pager, _sns, _table = _wire(
            monkeypatch,
            {"Items": [first, second]},
            scan_items=[_typed(first), _typed(second)],
        )

        assert unsubscribeService.get_asset(ASSET_ID) is None

    def test_the_handler_still_answers_200_when_the_asset_vanished(self, monkeypatch):
        """The subscription row was already rewritten, so the request itself succeeded."""
        _pager, _sns, _table = _wire(monkeypatch, {"Items": []})
        monkeypatch.setattr(
            unsubscribeService, "get_subscription_obj",
            lambda event_name, entity_name, entity_id: {
                "subscribers": {"L": [{"S": "test-user@example.com"}]}})

        response = unsubscribeService.delete_subscription({
            "eventName": "Asset Version Change",
            "entityName": "Asset",
            "entityId": ASSET_ID,
            "subscribers": ["test-user@example.com"],
        })

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "success"


@pytest.mark.unit
class TestArchivedNamesakeDoesNotWinTheLookup:
    """An archived row sharing the assetId never supplies the topic or the databaseId."""

    def test_live_row_wins_when_the_archived_row_is_returned_first(self, monkeypatch):
        # The index returns the archived partition first; a lookup that takes the first row
        # acts on the archived record's topic and reports its "{databaseId}#deleted" partition
        archived, live = _archived_row(), _live_row()
        _pager, sns, _table = _wire(
            monkeypatch,
            {"Items": [archived, live]},
            scan_items=[_typed(archived), _typed(live)],
        )

        asset_obj = unsubscribeService.get_asset(ASSET_ID)

        assert asset_obj == {"databaseId": LIVE_DATABASE_ID, "snsTopic": LIVE_TOPIC}

        unsubscribeService.delete_sns_subscriptions(
            ASSET_ID, ["test-user@example.com"], delete_sns=False)
        # The topic acted on, not how many times: an idempotent retry is not a defect here.
        assert sns.list_subscriptions_by_topic.called
        assert {call.kwargs.get("TopicArn")
                for call in sns.list_subscriptions_by_topic.call_args_list} == {LIVE_TOPIC}

    def test_status_archived_row_is_excluded_too(self, monkeypatch):
        archived = _live_row(database_id="other-database-id", sns_topic=ARCHIVED_TOPIC)
        archived["status"] = "archived"
        _pager, _sns, _table = _wire(monkeypatch, {"Items": [archived, _live_row()]})

        asset_obj = unsubscribeService.get_asset(ASSET_ID)

        assert asset_obj == {"databaseId": LIVE_DATABASE_ID, "snsTopic": LIVE_TOPIC}


@pytest.mark.unit
class TestLookupReadsTheIndexNotAFilteredScan:
    """The read is a keyed index query, so no FilterExpression decides the answer."""

    def test_the_read_is_a_keyed_assetidgsi_query(self, monkeypatch):
        _pager, _sns, table = _wire(monkeypatch, {"Items": [_live_row()]})

        unsubscribeService.get_asset(ASSET_ID)

        assert table.query.call_count >= 1
        first_read = table.query.call_args_list[0].kwargs
        assert first_read["IndexName"] == "assetIdGSI"
        assert "FilterExpression" not in first_read, (
            "a filter is applied after the page read, so it cannot decide a lookup")
