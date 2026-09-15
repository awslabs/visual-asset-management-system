#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Asset resolution behind the subscription create and delete paths (S2-BACKEND-039).

``subscriptionService`` carries THREE of the four call sites the finding names — its own
``get_asset``, plus the ``delete_sns_subscriptions`` and ``create_sns_subscriptions`` that
dereference the result. ``unsubscribeService`` carries the fourth and is covered by
``test_unsubscribe_asset_lookup_paging.py``; this file is its sibling rather than a copy,
because the two modules resolve the asset independently and only the create path can raise.

WHY THIS FILE EXISTS SEPARATELY, which is worth stating: the ``subscriptionService`` half of
the fix was applied and the whole subscription suite passed — and it passed just as well with
``get_asset`` mutated to return ``None`` unconditionally. Nothing exercised these three sites.
A change that no test can distinguish from its own absence is not covered, so the properties
below are asserted here directly.

The properties are the same three the lookup must provide, and none survives a single filtered
``Scan``:

* **Paging.** A ``FilterExpression`` is applied AFTER the 1 MB page is read, so a page with
  zero matching Items alongside a ``LastEvaluatedKey`` is the ordinary shape for "the match is
  on a later page". Deciding from one page answers "no such asset" for an asset that exists.
* **A not-found answer each caller survives.** Both callers run AFTER the subscription row has
  been written, so an unresolvable asset must be reported rather than dereferenced. The two
  paths differ deliberately and the difference is asserted: the delete path degrades to a
  logged skip (the row is already updated and topic cleanup is best effort), while the create
  path raises ``VAMSGeneralErrorResponse``, which the handler maps to a 400 — replacing the
  ``AttributeError`` that surfaced as a 500.
* **The live asset, not an archived namesake.** Archiving rewrites a record under a
  ``{databaseId}#deleted`` partition while keeping the assetId, so one assetId can match an
  archived row in one database and a live row in another. Acting on the wrong row writes the
  ``snsTopic`` onto the wrong database's record. An ambiguous match resolves to ``None``
  rather than to whichever row the index happened to yield first.

Every negative case is paired with a positive control that resolves a single live asset, so a
lookup answering ``None`` unconditionally fails the pair rather than passing half of it.
"""

from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.subscription import subscriptionService
from backend.tests.pagingStub import Pager

ASSET_ID = "test-asset-id"
LIVE_DATABASE_ID = "live-database-id"
LIVE_TOPIC = "arn:aws:sns:us-east-1:123456789012:AssetTopic-live"
ARCHIVED_TOPIC = "arn:aws:sns:us-east-1:123456789012:AssetTopic-archived"
SUBSCRIBER = "test-user@example.com"

# The cursor DynamoDB returns on a page whose filter matched nothing but which is not the end
# of the result set.
PAGE_ONE_CURSOR = {"assetId": ASSET_ID, "databaseId": "some-earlier-database"}


def _live_row(database_id=LIVE_DATABASE_ID, sns_topic=LIVE_TOPIC):
    row = {"assetId": ASSET_ID, "databaseId": database_id, "assetName": "Test Asset"}
    if sns_topic is not None:
        row["snsTopic"] = sns_topic
    return row


def _archived_row():
    return {
        "assetId": ASSET_ID,
        "databaseId": f"{LIVE_DATABASE_ID}#deleted",
        "assetName": "Test Asset",
        "snsTopic": ARCHIVED_TOPIC,
    }


def _status_archived_row():
    """The other archived shape: the partition is untouched and a status attribute marks it."""
    return {
        "assetId": ASSET_ID,
        "databaseId": "another-database-id",
        "assetName": "Test Asset",
        "snsTopic": ARCHIVED_TOPIC,
        "status": "archived",
    }


def _wire(monkeypatch, *pages, scan_items=None):
    """Point the module's asset reads at a scripted page sequence and record SNS calls.

    Both readers are stubbed. The paged index read is what the fixed lookup issues; ``scan`` is
    stubbed with the first scripted page so a lookup that reads only one page (the pre-fix
    shape) resolves against a realistic response rather than failing on an absent endpoint —
    which is what keeps the PRE arm's failure the finding's own mechanism.
    """
    table = MagicMock()
    pager = Pager(*pages, name="assetIdGSI lookup")
    table.query.side_effect = pager

    resource = MagicMock()
    resource.Table.return_value = table
    monkeypatch.setattr(subscriptionService, "dynamodb", resource)

    client = MagicMock()
    client.scan.return_value = {
        "Items": scan_items if scan_items is not None else [],
        **({"LastEvaluatedKey": pages[0]["LastEvaluatedKey"]}
           if "LastEvaluatedKey" in pages[0] else {}),
    }
    monkeypatch.setattr(subscriptionService, "dynamodb_client", client)

    sns = MagicMock()
    sns.list_subscriptions_by_topic.return_value = {"Subscriptions": []}
    monkeypatch.setattr(subscriptionService, "sns_client", sns)

    return pager, sns, table


@pytest.mark.unit
class TestAssetLookupPagesToExhaustion:
    """A match beyond the first page is found, not reported absent."""

    def test_asset_on_a_later_page_resolves(self, monkeypatch):
        pager, _sns, _table = _wire(
            monkeypatch,
            {"Items": [], "LastEvaluatedKey": PAGE_ONE_CURSOR},
            {"Items": [_live_row()]},
        )

        assert subscriptionService.get_asset(ASSET_ID) == {
            "databaseId": LIVE_DATABASE_ID, "snsTopic": LIVE_TOPIC}
        pager.assert_paged_to_exhaustion()

    def test_asset_on_a_later_page_reaches_sns_cleanup(self, monkeypatch):
        pager, sns, _table = _wire(
            monkeypatch,
            {"Items": [], "LastEvaluatedKey": PAGE_ONE_CURSOR},
            {"Items": [_live_row()]},
        )

        subscriptionService.delete_sns_subscriptions(ASSET_ID, [SUBSCRIBER], delete_sns=False)

        # The topic acted on, not how many times: an idempotent retry is not a defect here.
        assert sns.list_subscriptions_by_topic.called
        assert {call.kwargs.get("TopicArn")
                for call in sns.list_subscriptions_by_topic.call_args_list} == {LIVE_TOPIC}
        pager.assert_paged_to_exhaustion()

    def test_asset_on_the_first_page_needs_no_continuation(self, monkeypatch):
        """Positive control: a single-page result costs one read and still resolves."""
        pager, _sns, _table = _wire(monkeypatch, {"Items": [_live_row()]})

        assert subscriptionService.get_asset(ASSET_ID) == {
            "databaseId": LIVE_DATABASE_ID, "snsTopic": LIVE_TOPIC}
        assert pager.resumed_from == [], "a complete first page needs no continuation"

    def test_the_read_is_a_keyed_index_query_not_a_filtered_scan(self, monkeypatch):
        """The mechanism, not just the outcome.

        A scan that happened to be given every row would satisfy the assertions above while
        leaving the paging defect in place, so what the lookup ISSUES is asserted too.
        """
        _pager, _sns, table = _wire(monkeypatch, {"Items": [_live_row()]})

        subscriptionService.get_asset(ASSET_ID)

        assert table.query.called, "the lookup must query assetIdGSI"
        assert table.query.call_args.kwargs.get("IndexName") == "assetIdGSI"
        assert not subscriptionService.dynamodb_client.scan.called, (
            "a filtered scan applies its filter only to the page already read")


@pytest.mark.unit
class TestTheDeletePathSurvivesAnUnresolvableAsset:
    """The delete path degrades to a logged skip; it must not raise."""

    def test_an_unresolvable_asset_is_skipped_rather_than_dereferenced(self, monkeypatch):
        _pager, sns, _table = _wire(monkeypatch, {"Items": []})

        # Pre-fix this raised AttributeError: 'NoneType' object has no attribute 'get',
        # which the handler reported as a 500.
        subscriptionService.delete_sns_subscriptions(ASSET_ID, [SUBSCRIBER], delete_sns=False)

        sns.list_subscriptions_by_topic.assert_not_called()
        sns.delete_topic.assert_not_called()

    def test_an_unresolvable_asset_deletes_no_topic_when_delete_sns_is_set(self, monkeypatch):
        """The destructive arm of the same path: no topic is deleted from an unknown asset."""
        _pager, sns, _table = _wire(monkeypatch, {"Items": []})

        subscriptionService.delete_sns_subscriptions(ASSET_ID, [SUBSCRIBER], delete_sns=True)

        sns.delete_topic.assert_not_called()

    def test_an_asset_carrying_no_topic_is_still_a_skip(self, monkeypatch):
        """Positive control: resolving fine but holding no topic is a different condition.

        It must reach the same skip WITHOUT the None guard swallowing it, so a guard written
        as ``if not asset_obj`` (which also catches an empty dict) is not what is passing.
        """
        _pager, sns, _table = _wire(monkeypatch, {"Items": [_live_row(sns_topic=None)]})

        subscriptionService.delete_sns_subscriptions(ASSET_ID, [SUBSCRIBER], delete_sns=False)

        sns.list_subscriptions_by_topic.assert_not_called()


@pytest.mark.unit
class TestTheCreatePathReportsAnUnresolvableAsset:
    """The create path cannot degrade — it must raise a mapped error, not dereference None."""

    def test_an_unresolvable_asset_raises_a_mapped_error(self, monkeypatch):
        _pager, sns, _table = _wire(monkeypatch, {"Items": []})

        with pytest.raises(subscriptionService.VAMSGeneralErrorResponse):
            subscriptionService.create_sns_subscriptions(ASSET_ID, [SUBSCRIBER])

        sns.create_topic.assert_not_called()
        sns.subscribe.assert_not_called()

    def test_the_raised_error_is_not_an_attribute_error(self, monkeypatch):
        """The specific pre-fix failure, named.

        ``VAMSGeneralErrorResponse`` is mapped to a 400 by the handler; ``AttributeError`` is
        not mapped at all and surfaces as a 500 telling the caller nothing.
        """
        _pager, _sns, _table = _wire(monkeypatch, {"Items": []})

        with pytest.raises(Exception) as caught:
            subscriptionService.create_sns_subscriptions(ASSET_ID, [SUBSCRIBER])

        assert not isinstance(caught.value, AttributeError), (
            "an unresolved asset was dereferenced instead of reported")

    def test_a_resolvable_asset_still_subscribes(self, monkeypatch):
        """Positive control: the guard must not have closed the working path."""
        _pager, sns, _table = _wire(monkeypatch, {"Items": [_live_row()]})

        subscriptionService.create_sns_subscriptions(ASSET_ID, [SUBSCRIBER])

        # That it subscribed to the right topic; a retry would not be a defect.
        assert sns.subscribe.called
        assert {call.kwargs.get("TopicArn") for call in sns.subscribe.call_args_list} == {
            LIVE_TOPIC
        }


@pytest.mark.unit
class TestTheLiveAssetIsResolvedNotAnArchivedNamesake:
    """An archived row must not stand in for the live asset, in either archived shape."""

    def test_an_archived_partition_row_is_not_taken_as_the_asset(self, monkeypatch):
        _pager, _sns, _table = _wire(monkeypatch, {"Items": [_archived_row(), _live_row()]})

        assert subscriptionService.get_asset(ASSET_ID) == {
            "databaseId": LIVE_DATABASE_ID, "snsTopic": LIVE_TOPIC}

    def test_a_status_archived_row_is_not_taken_as_the_asset(self, monkeypatch):
        _pager, _sns, _table = _wire(
            monkeypatch, {"Items": [_status_archived_row(), _live_row()]})

        assert subscriptionService.get_asset(ASSET_ID) == {
            "databaseId": LIVE_DATABASE_ID, "snsTopic": LIVE_TOPIC}

    def test_two_live_rows_resolve_to_neither(self, monkeypatch):
        """Ambiguity is refused rather than guessed.

        assetIds are unique within a database only, so two live matches cannot be attributed
        to one database's record — and writing the topic onto the wrong one is the finding's
        wrong-database half.
        """
        _pager, sns, _table = _wire(monkeypatch, {"Items": [
            _live_row(database_id="database-a", sns_topic="arn:aws:sns:us-east-1:1:A"),
            _live_row(database_id="database-b", sns_topic="arn:aws:sns:us-east-1:1:B"),
        ]})

        assert subscriptionService.get_asset(ASSET_ID) is None
        subscriptionService.delete_sns_subscriptions(ASSET_ID, [SUBSCRIBER], delete_sns=True)
        sns.delete_topic.assert_not_called()

    def test_only_an_archived_row_resolves_to_nothing(self, monkeypatch):
        """The paired control for the two archived cases above.

        Without it, "the live row was chosen" would be satisfied by a filter that discards
        nothing, because there would be no case where discarding is what produces the answer.
        """
        _pager, _sns, _table = _wire(monkeypatch, {"Items": [_archived_row()]})

        assert subscriptionService.get_asset(ASSET_ID) is None
