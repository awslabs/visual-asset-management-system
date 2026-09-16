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

    @pytest.mark.parametrize("path,params", [(LIST_PATH, None), (ASSET_PATH, ASSET_PARAMS)])
    def test_tier_one_with_empty_tokens_denies_even_when_the_api_check_would_pass(self, path, params):
        instance = enforcer(api=True, obj=True)
        table = MagicMock()
        table.query.return_value = {"Items": [_entry()]}
        with patch(f"{MOD}.request_to_claims", claims_for()), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{MOD}.audit_table", table):
            response = svc.lambda_handler(rest_event("GET", path, params), MagicMock())
        assert response["statusCode"] == 403
        instance.enforce.assert_not_called()
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
            {"object__type": "complianceEvaluation", "databaseId": DB, "complianceState": ""}, "GET")

    def _list_with(self, instance, rows, query=None):
        """Run the global listing; `query` None narrows the walk to one partition, {} walks them all."""
        if query is None:
            query = {"eventType": "compliance_check"}
        table = MagicMock()
        table.query.side_effect = lambda **kw: {"Items": list(rows)}
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=instance), \
                patch(f"{MOD}.audit_table", table):
            response = svc.lambda_handler(rest_event("GET", LIST_PATH, query_params=query), MagicMock())
        return response, table

    def test_the_listing_filters_entries_by_their_database(self):
        instance = enforcer()
        instance.enforce.side_effect = lambda obj, act: obj["databaseId"] == DB
        response, _ = self._list_with(instance, [_entry(database_id=DB), _entry(database_id="other")])
        entries = body_of(response)["entries"]
        assert [e["databaseId"] for e in entries] == [DB]
        assert "other" not in response["body"]
        assert instance.enforce.call_args_list[1].args == (
            {"object__type": "complianceEvaluation", "databaseId": "other", "complianceState": ""},
            "GET")

    def test_a_denied_enforcer_yields_an_empty_listing_across_the_whole_walk(self):
        rows = [_entry(database_id=DB), _entry(database_id="other")]
        response, table = self._list_with(enforcer(obj=False), rows, query={})
        assert response["statusCode"] == 200
        assert body_of(response)["entries"] == []
        # Every partition was read and every row was refused: nothing leaks and nothing is skipped.
        assert table.query.call_count == len(svc.AUDIT_EVENT_TYPES)

    def test_an_entry_without_a_database_is_listed_only_for_the_empty_database_object(self):
        orphan = {k: v for k, v in _entry().items() if k != "databaseId"}
        instance = enforcer()
        instance.enforce.side_effect = lambda obj, act: obj["databaseId"] == ""
        response, _ = self._list_with(instance, [orphan, _entry(database_id=DB)])
        assert [e["entryId"] for e in body_of(response)["entries"]] == [orphan["entryId"]]
        assert instance.enforce.call_args_list[0].args == (
            {"object__type": "complianceEvaluation", "databaseId": "", "complianceState": ""}, "GET")


@pytest.mark.unit
class TestAuditEventTypeRegistry:

    def test_every_event_type_the_compliance_handlers_write_is_walked(self):
        assert set(svc.AUDIT_EVENT_TYPES) >= {
            "compliance_check", "quarantine_released", "exception_granted", "exception_revoked",
            "exception_superseded", "schema_bound_to_database", "schema_unbound_from_database",
            "schema_bound_to_asset", "schema_unbound_from_asset", "schema_deleted",
            "cascade_triggered", "cascade_auto_triggered", "cascade_approved", "cascade_rejected",
            "cascade_completed", "evaluation_error",
        }
        assert len(svc.AUDIT_EVENT_TYPES) == len(set(svc.AUDIT_EVENT_TYPES)) == 16

    def test_the_stores_evaluation_error_type_is_the_registered_one(self):
        from handlers.compliance import complianceEvaluationStore as store
        assert store.AUDIT_EVALUATION_ERROR in svc.AUDIT_EVENT_TYPES
        assert store.AUDIT_EXCEPTION_SUPERSEDED in svc.AUDIT_EVENT_TYPES


@pytest.mark.unit
class TestValidation:

    BAD_DB = "bad<database-id>"
    BAD_ASSET = "bad<asset-id>"

    @pytest.mark.parametrize("path,params,bad", [
        (f"/compliance/audit/{BAD_DB}/{ASSET}", {"databaseId": BAD_DB, "assetId": ASSET}, BAD_DB),
        (f"/compliance/audit/{DB}/{BAD_ASSET}", {"databaseId": DB, "assetId": BAD_ASSET}, BAD_ASSET),
    ])
    def test_a_bad_path_parameter_is_rejected(self, path, params, bad):
        response, table = _run(rest_event("GET", path, params))
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        table.query.assert_not_called()

    @pytest.mark.parametrize("path,params,query,bad", [
        (LIST_PATH, None, {"limit": "many"}, "many"),
        (LIST_PATH, None, {"maxItems": "1.5"}, "1.5"),
        (LIST_PATH, None, {"startingToken": "%%%"}, "%%%"),
        (LIST_PATH, None, {"eventType": "x" * 300}, "x" * 300),
        (LIST_PATH, None, {"startDate": "d" * 300}, "d" * 300),
        (ASSET_PATH, ASSET_PARAMS, {"limit": "many"}, "many"),
        (ASSET_PATH, ASSET_PARAMS, {"maxItems": "1.5"}, "1.5"),
        (ASSET_PATH, ASSET_PARAMS, {"startingToken": "%%%"}, "%%%"),
        (ASSET_PATH, ASSET_PARAMS, {"endDate": "d" * 300}, "d" * 300),
    ])
    def test_a_bad_query_parameter_is_rejected(self, path, params, query, bad):
        response, table = _run(rest_event("GET", path, params, query_params=query))
        assert response["statusCode"] == 400
        assert bad not in response["body"]
        table.query.assert_not_called()

    @pytest.mark.parametrize("path,params", [(LIST_PATH, None), (ASSET_PATH, ASSET_PARAMS)])
    @pytest.mark.parametrize("token", [_token([1]), _token("key"), _token({}), _token(None)],
                             ids=["list", "string", "empty-object", "null"])
    def test_a_token_that_is_not_an_object_is_rejected(self, path, params, token):
        response, table = _run(rest_event("GET", path, params, query_params={"startingToken": token}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        table.query.assert_not_called()

    @pytest.mark.parametrize("query,token", [
        ({}, {"partition": "not-an-event-type", "key": None}),
        ({}, {"partition": "not-an-event-type", "key": {"eventType": "x", "timestamp": "t"}}),
        ({}, {"key": {"eventType": "compliance_check", "timestamp": "t"}}),
        ({}, {"partition": "compliance_check", "key": "not-an-object"}),
        ({}, {"partition": "compliance_check", "key": [1]}),
        ({"eventType": "compliance_check"}, {"partition": "exception_granted", "key": None}),
        ({"eventType": "compliance_check"}, {"eventType": "compliance_check", "timestamp": "t"}),
    ], ids=["unknown-partition", "unknown-partition-with-key", "no-partition", "string-key",
            "list-key", "partition-outside-the-filter", "bare-key-under-filter"])
    def test_a_listing_token_outside_the_walk_is_rejected_before_any_read(self, query, token):
        """A token naming a partition the walk does not contain (or lacking the partition shape)
        never reaches ExclusiveStartKey, where DynamoDB would fail it as an internal error."""
        bad_partition = token.get("partition")
        response, table = _run(rest_event("GET", LIST_PATH, query_params=dict(
            query, startingToken=_token(token))))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        if bad_partition:
            assert bad_partition not in response["body"]
        table.query.assert_not_called()

    def test_the_page_size_is_clamped(self):
        _, table = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS, query_params={"limit": "99999"}),
                        pages=[{"Items": []}])
        assert 1 <= table.query.call_args.kwargs["Limit"] <= svc.MAX_AUDIT_PAGE_SIZE
        _, table = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS, query_params={"maxItems": "-3"}),
                        pages=[{"Items": []}])
        assert table.query.call_args.kwargs["Limit"] == 1

    def test_the_default_and_maximum_page_sizes_are_the_contract_values(self):
        assert svc.DEFAULT_AUDIT_PAGE_SIZE == 100
        assert svc.MAX_AUDIT_PAGE_SIZE == 500
        _, table = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS), pages=[{"Items": []}])
        assert table.query.call_args.kwargs["Limit"] == 100


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

    def test_the_token_round_trips_and_the_pages_partition_the_full_set(self):
        """Page two resumes the partition page one stopped in, from its key; the two pages together
        are exactly the entries of the walk."""
        entries = [dict(_entry(), entryId=f"e{i}") for i in range(3)]
        last_key = {"eventType": "compliance_check", "timestamp": "t2", "entryId": "e1"}

        def pages(**kwargs):
            if kwargs["KeyConditionExpression"]._values[1] != "compliance_check":
                return {"Items": []}
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": entries[:2], "LastEvaluatedKey": last_key}
            assert kwargs["ExclusiveStartKey"] == last_key
            return {"Items": entries[2:]}

        response, _ = _run(rest_event("GET", LIST_PATH, query_params={"limit": "2"}), pages=pages)
        page_one = body_of(response)
        assert [e["entryId"] for e in page_one["entries"]] == ["e0", "e1"]
        assert _decode(page_one["NextToken"]) == {"partition": "compliance_check", "key": last_key}

        response, table = _run(
            rest_event("GET", LIST_PATH,
                       query_params={"limit": "2", "startingToken": page_one["NextToken"]}),
            pages=pages)
        page_two = body_of(response)
        assert [e["entryId"] for e in page_two["entries"]] == ["e2"]
        assert "NextToken" not in page_two
        assert table.query.call_args_list[0].kwargs["ExclusiveStartKey"] == last_key
        assert [e["entryId"] for e in page_one["entries"] + page_two["entries"]] == ["e0", "e1", "e2"]

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
        # One entry in the first partition (compliance_check), one in the third (exception_granted),
        # none in any other: the counts are positional over the registry, whatever its length.
        types = list(svc.AUDIT_EVENT_TYPES)
        per_partition = [0] * len(types)
        per_partition[types.index("compliance_check")] = 1
        per_partition[types.index("exception_granted")] = 1
        counts = iter(per_partition)

        def pages(**kwargs):
            return {"Items": [_entry(kwargs["KeyConditionExpression"]._values[1])]
                    * next(counts)}

        response, table = _run(rest_event("GET", LIST_PATH, query_params={"limit": "3"}), pages=pages)
        entries = body_of(response)["entries"]
        assert [e["eventType"] for e in entries] == ["compliance_check", "exception_granted"]
        assert table.query.call_count == len(svc.AUDIT_EVENT_TYPES)
        assert table.query.call_args_list[1].kwargs["Limit"] == 2
        assert "NextToken" not in body_of(response)
