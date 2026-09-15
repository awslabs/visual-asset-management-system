# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceAuditService: dispatch of its two method+path pairs, both authorization tiers, input
validation, and the two externally paged listings -- the per-asset history and the cross-asset
listing that walks one EventTypeIndex partition per known event type behind one token."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, USER, body_of, claims_for, enforcer, rest_event,
)
from handlers.compliance import complianceAuditService as svc

MOD = "handlers.compliance.complianceAuditService"

LIST_PATH = "/compliance/audit"
ASSET_PATH = f"/compliance/audit/{DB}/{ASSET}"
ASSET_PARAMS = {"databaseId": DB, "assetId": ASSET}


def _entry(event_type="compliance_check", database_id=DB):
    return {"entryId": f"{event_type}-{database_id}", "eventType": event_type,
            "databaseId": database_id, "assetId": ASSET}


def _token(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def _decode(token):
    return json.loads(base64.b64decode(token))


def _run(event, tokens=(USER,), api=True, obj=True, pages=None):
    audit_table = MagicMock(name="audit_table")
    if pages is None:
        audit_table.query.return_value = {"Items": []}
    elif callable(pages):
        audit_table.query.side_effect = pages
    else:
        audit_table.query.side_effect = list(pages)
    with patch(f"{MOD}.request_to_claims", claims_for(*tokens)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer(api=api, obj=obj)), \
            patch(f"{MOD}.audit_table", audit_table):
        response = svc.lambda_handler(event, MagicMock())
    return response, audit_table


@pytest.mark.unit
class TestRouteDispatch:

    def test_get_asset_history(self):
        response, table = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS),
                               pages=[{"Items": [_entry()]}])
        assert response["statusCode"] == 200
        assert body_of(response)["entries"][0]["eventType"] == "compliance_check"
        query = table.query.call_args.kwargs
        assert query["IndexName"] == "AssetIndex"
        assert query["ScanIndexForward"] is False
        assert 1 <= query["Limit"] <= svc.DEFAULT_AUDIT_PAGE_SIZE

    def test_get_listing_with_null_rest_params(self):
        response, table = _run(rest_event("GET", LIST_PATH), pages=lambda **kw: {"Items": []})
        assert response["statusCode"] == 200
        assert body_of(response) == {"entries": []}
        partitions = [c.kwargs["KeyConditionExpression"]._values[1] for c in table.query.call_args_list]
        assert partitions == list(svc.AUDIT_EVENT_TYPES)

    @pytest.mark.parametrize("method,path", [
        ("POST", LIST_PATH), ("PUT", ASSET_PATH), ("DELETE", LIST_PATH),
        ("GET", f"/compliance/audit/{DB}"), ("GET", f"{ASSET_PATH}/extra"),
    ])
    def test_an_unknown_method_or_path_is_refused(self, method, path):
        response, table = _run(rest_event(method, path, ASSET_PARAMS))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Method not allowed"
        table.query.assert_not_called()


@pytest.mark.unit
class TestAuthorization:

    @pytest.mark.parametrize("tokens", [(), (USER,)], ids=["empty-tokens", "api-denied"])
    def test_tier_one_denies(self, tokens):
        response, table = _run(rest_event("GET", LIST_PATH), tokens=tokens, api=False)
        assert response["statusCode"] == 403
        table.query.assert_not_called()

    def test_tier_two_denial_on_the_asset_history_reads_nothing(self):
        response, table = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS), obj=False)
        assert response["statusCode"] == 403
        table.query.assert_not_called()

    def test_the_asset_history_check_names_the_database(self):
        instance = enforcer()
        table = MagicMock()
        table.query.return_value = {"Items": []}
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{MOD}.audit_table", table):
            svc.lambda_handler(rest_event("GET", ASSET_PATH, ASSET_PARAMS), MagicMock())
        instance.enforce.assert_called_once_with(
            {"object__type": "complianceEvaluation", "databaseId": DB}, "GET")

    def test_the_listing_filters_entries_by_their_database(self):
        instance = enforcer()
        instance.enforce.side_effect = lambda obj, act: obj["databaseId"] == DB
        table = MagicMock()
        table.query.side_effect = lambda **kw: {"Items": [_entry(database_id=DB),
                                                          _entry(database_id="other")]}
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{MOD}.audit_table", table):
            response = svc.lambda_handler(
                rest_event("GET", LIST_PATH, query_params={"eventType": "compliance_check"}),
                MagicMock())
        entries = body_of(response)["entries"]
        assert [e["databaseId"] for e in entries] == [DB]


@pytest.mark.unit
class TestValidation:

    @pytest.mark.parametrize("path,params", [
        (f"/compliance/audit/x/{ASSET}", {"databaseId": "x", "assetId": ASSET}),
        (f"/compliance/audit/{DB}/bad<id>", {"databaseId": DB, "assetId": "bad<id>"}),
    ])
    def test_a_bad_path_parameter_is_rejected(self, path, params):
        response, table = _run(rest_event("GET", path, params))
        assert response["statusCode"] == 400
        table.query.assert_not_called()

    @pytest.mark.parametrize("path,params,query", [
        (LIST_PATH, None, {"limit": "many"}),
        (LIST_PATH, None, {"maxItems": "1.5"}),
        (LIST_PATH, None, {"startingToken": "%%%"}),
        (LIST_PATH, None, {"eventType": "x" * 300}),
        (LIST_PATH, None, {"startDate": "d" * 300}),
        (ASSET_PATH, ASSET_PARAMS, {"limit": "many"}),
        (ASSET_PATH, ASSET_PARAMS, {"maxItems": "1.5"}),
        (ASSET_PATH, ASSET_PARAMS, {"startingToken": "%%%"}),
        (ASSET_PATH, ASSET_PARAMS, {"endDate": "d" * 300}),
    ])
    def test_a_bad_query_parameter_is_rejected(self, path, params, query):
        response, table = _run(rest_event("GET", path, params, query_params=query))
        assert response["statusCode"] == 400
        table.query.assert_not_called()

    def test_the_page_size_is_clamped(self):
        _, table = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS, query_params={"limit": "99999"}),
                        pages=[{"Items": []}])
        assert 1 <= table.query.call_args.kwargs["Limit"] <= svc.MAX_AUDIT_PAGE_SIZE
        _, table = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS, query_params={"maxItems": "-3"}),
                        pages=[{"Items": []}])
        assert table.query.call_args.kwargs["Limit"] == 1


@pytest.mark.unit
class TestAssetHistoryPaging:

    def test_the_token_round_trips(self):
        last_key = {"databaseId:assetId": f"{DB}:{ASSET}", "timestamp": "t1", "entryId": "e1"}
        response, _ = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS, query_params={"limit": "1"}),
                           pages=[{"Items": [_entry()], "LastEvaluatedKey": last_key}])
        token = body_of(response)["NextToken"]
        assert _decode(token) == last_key
        response, table = _run(
            rest_event("GET", ASSET_PATH, ASSET_PARAMS,
                       query_params={"limit": "1", "startingToken": token}),
            pages=[{"Items": [_entry("exception_granted")]}])
        assert body_of(response)["entries"][0]["eventType"] == "exception_granted"
        assert "NextToken" not in body_of(response)
        assert table.query.call_args.kwargs["ExclusiveStartKey"] == last_key

    def test_a_date_window_narrows_the_key_condition(self):
        _, table = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS,
                                   query_params={"startDate": "2026-01-01", "endDate": "2026-02-01"}),
                        pages=[{"Items": []}])
        condition = table.query.call_args.kwargs["KeyConditionExpression"]
        window = condition._values[1]
        assert type(window).__name__ == "Between"
        assert window._values[1:] == ("2026-01-01", "2026-02-01")


@pytest.mark.unit
class TestCrossAssetListing:

    def test_a_filtered_listing_pages_one_partition_and_carries_it_in_the_token(self):
        last_key = {"eventType": "compliance_check", "timestamp": "t", "entryId": "e"}
        response, table = _run(
            rest_event("GET", LIST_PATH, query_params={"eventType": "compliance_check", "limit": "1"}),
            pages=[{"Items": [_entry()], "LastEvaluatedKey": last_key}])
        assert table.query.call_count == 1
        assert table.query.call_args.kwargs["IndexName"] == "EventTypeIndex"
        assert _decode(body_of(response)["NextToken"]) == {
            "partition": "compliance_check", "key": last_key}

    def test_a_partition_token_resumes_that_partition_from_its_key(self):
        last_key = {"eventType": "exception_granted", "timestamp": "t", "entryId": "e"}
        token = _token({"partition": "exception_granted", "key": last_key})
        seen = []

        def pages(**kwargs):
            seen.append(kwargs)
            return {"Items": []}

        response, _ = _run(rest_event("GET", LIST_PATH, query_params={"startingToken": token}),
                           pages=pages)
        assert response["statusCode"] == 200
        partitions = [k["KeyConditionExpression"]._values[1] for k in seen]
        index = list(svc.AUDIT_EVENT_TYPES).index("exception_granted")
        assert partitions == list(svc.AUDIT_EVENT_TYPES)[index:]
        assert seen[0]["ExclusiveStartKey"] == last_key
        assert all("ExclusiveStartKey" not in k for k in seen[1:])

    def test_a_full_page_from_one_partition_points_the_token_at_the_next(self):
        response, table = _run(
            rest_event("GET", LIST_PATH, query_params={"limit": "2"}),
            pages=[{"Items": [_entry(), _entry()]}])
        assert table.query.call_count == 1
        assert len(body_of(response)["entries"]) == 2
        assert _decode(body_of(response)["NextToken"]) == {
            "partition": svc.AUDIT_EVENT_TYPES[1], "key": None}

    def test_the_walk_fills_a_page_across_partitions(self):
        counts = iter([1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])

        def pages(**kwargs):
            return {"Items": [_entry(kwargs["KeyConditionExpression"]._values[1])]
                    * next(counts)}

        response, table = _run(rest_event("GET", LIST_PATH, query_params={"limit": "3"}), pages=pages)
        entries = body_of(response)["entries"]
        assert [e["eventType"] for e in entries] == ["compliance_check", "exception_granted"]
        assert table.query.call_count == len(svc.AUDIT_EVENT_TYPES)
        assert table.query.call_args_list[1].kwargs["Limit"] == 2
        assert "NextToken" not in body_of(response)
