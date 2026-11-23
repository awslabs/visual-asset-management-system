# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A chosen assetId is refused when an asset in any database, live or archived, already carries it.

The refusal is the answer the caller's own database already gives for a taken id: `400` with
"Asset with specified ID already exists", which names no database. A generated id is not read for.
A failed read of the id refuses the create (`500`) and writes nothing.

The location checks stay real (``_wire`` from the prefix-takeover tests), so each case here is one
those checks pass: the other asset lives under another bucket record or at an unrelated key.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.tests.pagingStub import Pager
from tests.handlers.assets.test_createAsset_conditional_put import _load
from tests.handlers.assets.test_createAsset_cross_database_prefix_takeover import (
    _BASE_PREFIX,
    _BUCKET_NAME,
    _OTHER_DB,
    _OWN_DB,
    _VICTIM_ASSET_ID,
    _assert_nothing_persisted,
    _asset_record,
    _condition_terms,
    _request_model,
    _term_selects,
    _wire,
)

# A bucket record no colocation lookup returns, so the location layers never see its assets.
_ELSEWHERE_BUCKET_ID = "bucket-record-elsewhere"
_ELSEWHERE_KEY = "elsewhere/" + _VICTIM_ASSET_ID + "/"


def _elsewhere(database_id, archived=False):
    return _asset_record(database_id, _VICTIM_ASSET_ID, _ELSEWHERE_KEY,
                         bucket_id=_ELSEWHERE_BUCKET_ID, archived=archived)


def _id_reads(m):
    """Every asset-table read made through assetIdGSI."""
    return [call for call in m.asset_table.query.call_args_list
            if call.kwargs.get("IndexName") == "assetIdGSI"]


def _route_id_reads(m, reader):
    """Answer assetIdGSI reads with ``reader``; every other read keeps the ``_wire`` stub."""
    location_reader = m.asset_table.query.side_effect

    def _query(**kwargs):
        if kwargs.get("IndexName") == "assetIdGSI":
            return reader(**kwargs)
        return location_reader(**kwargs)

    m.asset_table.query.side_effect = _query


@pytest.mark.unit
class TestAnIdTakenInAnotherDatabase:

    def test_a_live_asset_in_another_database_refuses_the_id(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[_elsewhere(_OTHER_DB)], prefix_exists=False)

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        _assert_nothing_persisted(m)

    def test_an_archived_asset_in_another_database_refuses_the_id(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[_elsewhere(f"{_OTHER_DB}#deleted", archived=True)],
              prefix_exists=False)

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        _assert_nothing_persisted(m)

    def test_the_refusal_is_the_same_database_answer_and_names_no_database(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[_elsewhere(_OTHER_DB)], prefix_exists=False)

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        assert str(caught.value).endswith("Asset with specified ID already exists")
        assert _OTHER_DB not in str(caught.value)

    def test_an_asset_found_from_an_s3_folder_is_refused_the_id_too(self):
        # Bucket sync creates through the same function with s3ExternalGenerated set; its folder
        # name is the id, so the rule applies to it as well and nothing is bound onto the folder.
        m = _load(fresh=True)
        _wire(m, owned_records=[_elsewhere(_OTHER_DB)], prefix_exists=True)

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["SYSTEM_USER"]}, True)

        _assert_nothing_persisted(m)


@pytest.mark.unit
class TestTheSameDatabaseAnswerIsUnchanged:

    def test_a_live_asset_in_the_same_database_is_refused_as_before(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        m.asset_table.get_item.return_value = {
            "Item": {"databaseId": _OWN_DB, "assetId": _VICTIM_ASSET_ID}}

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        assert str(caught.value).endswith("Asset with specified ID already exists")
        _assert_nothing_persisted(m)


@pytest.mark.unit
class TestFreeAndGeneratedIds:
    """Positive controls: without these, a check that refused every create would pass."""

    def test_a_free_chosen_id_is_created_after_its_id_is_read(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[_elsewhere(_OTHER_DB)], prefix_exists=False)

        response = m.create_asset(
            _request_model(m, asset_id="brand-new-asset"), {"tokens": ["user1"]})

        assert response.assetId == "brand-new-asset"
        assert m.save_asset_details.called
        m.create_prefix_folder.assert_any_call(_BUCKET_NAME, _BASE_PREFIX + "brand-new-asset/")
        assert _id_reads(m), "the chosen id was never looked up"

    def test_a_generated_id_is_created_without_reading_for_it(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[_elsewhere(_OTHER_DB)], prefix_exists=False)

        response = m.create_asset(
            m.CreateAssetRequestModel(
                databaseId=_OWN_DB,
                assetName="generated id asset",
                description="no assetId supplied",
                isDistributable=True,
                tags=[],
            ),
            {"tokens": ["user1"]},
        )

        assert response.assetId.startswith("x")
        assert m.save_asset_details.called
        assert not _id_reads(m)


@pytest.mark.unit
class TestTheIdRead:

    def test_the_read_is_a_keyed_lookup_of_the_chosen_id_in_every_database(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)

        m.create_asset(_request_model(m), {"tokens": ["user1"]})

        reads = _id_reads(m)
        assert reads, "the chosen id was never looked up"
        for call in reads:
            terms = _condition_terms(call.kwargs["KeyConditionExpression"])
            assert _term_selects(terms["assetId"], _VICTIM_ASSET_ID)
            # Not narrowed to a database or a bucket: the id must be free in all of them.
            assert "databaseId" not in terms
            assert "bucketId" not in terms

    def test_a_match_on_a_later_page_refuses_the_id(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetId": _VICTIM_ASSET_ID, "databaseId": "m"}},
            {"Items": [_elsewhere(f"{_OTHER_DB}#deleted", archived=True)]},
            name="assetIdGSI",
        )
        _route_id_reads(m, pager)

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        pager.assert_paged_to_exhaustion()
        _assert_nothing_persisted(m)

    def test_a_failed_read_refuses_the_create_and_writes_nothing(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)

        def _throttled(**kwargs):
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Query")

        _route_id_reads(m, _throttled)

        with pytest.raises(ClientError):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        _assert_nothing_persisted(m)


def _event(asset_id=_VICTIM_ASSET_ID):
    return {
        "requestContext": {"http": {"method": "POST", "path": "/assets"}},
        "body": json.dumps({
            "databaseId": _OWN_DB,
            "assetId": asset_id,
            "assetName": "asset-1",
            "description": "asset id in use test",
            "isDistributable": True,
            "tags": [],
        }),
    }


def _handler(m):
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.return_value = True
    m.CasbinEnforcer = MagicMock(return_value=enforcer)
    m.request_to_claims = lambda event: {"tokens": ["user1"], "roles": []}
    return m


@pytest.mark.unit
class TestTheApiAnswer:

    def test_an_id_taken_in_another_database_answers_400_with_the_existing_message(self):
        m = _handler(_load(fresh=True))
        _wire(m, owned_records=[_elsewhere(f"{_OTHER_DB}#deleted", archived=True)],
              prefix_exists=False)

        response = m.lambda_handler(_event(), None)

        assert response["statusCode"] == 400, response["body"]
        assert json.loads(response["body"])["message"].endswith(
            "Asset with specified ID already exists")
        _assert_nothing_persisted(m)

    def test_an_id_taken_in_the_same_database_answers_the_same(self):
        m = _handler(_load(fresh=True))
        _wire(m, owned_records=[], prefix_exists=False)
        m.asset_table.get_item.return_value = {
            "Item": {"databaseId": _OWN_DB, "assetId": _VICTIM_ASSET_ID}}

        response = m.lambda_handler(_event(), None)

        assert response["statusCode"] == 400, response["body"]
        assert json.loads(response["body"])["message"].endswith(
            "Asset with specified ID already exists")

    def test_a_failed_id_read_answers_500_and_creates_nothing(self):
        m = _handler(_load(fresh=True))
        _wire(m, owned_records=[], prefix_exists=False)

        def _throttled(**kwargs):
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Query")

        _route_id_reads(m, _throttled)

        response = m.lambda_handler(_event(), None)

        assert response["statusCode"] == 500, response["body"]
        _assert_nothing_persisted(m)

    def test_a_free_id_answers_200(self):
        m = _handler(_load(fresh=True))
        _wire(m, owned_records=[_elsewhere(_OTHER_DB)], prefix_exists=False)

        response = m.lambda_handler(_event(asset_id="brand-new-asset"), None)

        assert response["statusCode"] == 200, response["body"]
        assert json.loads(response["body"])["assetId"] == "brand-new-asset"
