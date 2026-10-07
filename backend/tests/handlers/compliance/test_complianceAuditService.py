# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceAuditService: dispatch of its two method+path pairs, both authorization tiers, input
validation, and the two externally paged listings -- the per-asset history and the cross-asset
listing, which is one newest-first query on AuditByDateGSI, or on one EventTypeIndex partition
when filtered."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, USER, body_of, claims_for, enforcer, rest_event,
)
from backend.tests.pagingStub import Pager
from handlers.compliance import complianceAuditService as svc

MOD = "handlers.compliance.complianceAuditService"

LIST_PATH = "/compliance/audit"
ASSET_PATH = f"/compliance/audit/{DB}/{ASSET}"
ASSET_PARAMS = {"databaseId": DB, "assetId": ASSET}

# What the AuditByDateGSI key condition is anchored on, as boto3 stores it.
LIST_PARTITION = svc.AUDIT_LIST_PARTITION


def _entry(event_type="compliance_check", database_id=DB):
    return {"entryId": f"{event_type}-{database_id}", "eventType": event_type,
            "databaseId": database_id, "assetId": ASSET}


def _date_key(entry_id, timestamp):
    """A LastEvaluatedKey as AuditByDateGSI returns one."""
    return {"entryId": entry_id, "allListPartition": LIST_PARTITION, "timestamp": timestamp}


def _token(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def _decode(token):
    return json.loads(base64.b64decode(token))


def _partition_of(query_kwargs):
    """The partition value the query's key condition is anchored on."""
    condition = query_kwargs["KeyConditionExpression"]
    # `_timestamp_condition` wraps the equality in an And when a date window is set.
    while type(condition).__name__ == "And":
        condition = condition._values[0]
    return condition._values[1]


def _enforced(instance):
    """Every (object, action) a CasbinEnforcer stand-in was asked to enforce, in call order."""
    return [call.args for call in instance.enforce.call_args_list]


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
        response, table = _run(rest_event("GET", LIST_PATH), pages=[{"Items": []}])
        assert response["statusCode"] == 200
        assert body_of(response) == {"entries": []}
        # One page is one read of the date index, not a read per event type.
        assert table.query.call_count <= 1
        assert table.query.call_args.kwargs["IndexName"] == "AuditByDateGSI"

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
        assert ({"object__type": "complianceEvaluation", "databaseId": DB, "complianceState": ""},
                "GET") in _enforced(instance)

    def _list_with(self, instance, rows, query=None):
        """Run the global listing; `query` None filters to one event type, {} is the unfiltered list."""
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
        assert ({"object__type": "complianceEvaluation", "databaseId": "other", "complianceState": ""},
                "GET") in _enforced(instance)

    def test_a_denied_enforcer_yields_an_empty_unfiltered_listing(self):
        rows = [_entry(database_id=DB), _entry(database_id="other")]
        response, table = self._list_with(enforcer(obj=False), rows, query={})
        assert response["statusCode"] == 200
        assert body_of(response)["entries"] == []
        # The page was read and every row was refused: nothing leaks and nothing is skipped.
        assert table.query.call_count <= 1
        assert table.query.call_args.kwargs["IndexName"] == "AuditByDateGSI"

    def test_an_empty_token_list_yields_an_empty_page_but_keeps_the_token(self):
        """The handler never reaches the listing with no tokens (Tier 1 denies), but the listing's
        own contract holds regardless: with no enforcer nothing is appended, while the page's
        continuation is still handed out."""
        table = MagicMock()
        table.query.return_value = {"Items": [_entry()], "LastEvaluatedKey": _date_key("e", "t")}
        event = rest_event("GET", LIST_PATH)
        with patch(f"{MOD}.claims_and_roles", {"tokens": [], "roles": []}), \
                patch(f"{MOD}.audit_table", table):
            response = svc.query_audit(event, {})
        assert response["statusCode"] == 200
        assert body_of(response)["entries"] == []
        assert _decode(body_of(response)["NextToken"]) == _date_key("e", "t")

    def test_an_entry_without_a_database_is_listed_only_for_the_empty_database_object(self):
        orphan = {k: v for k, v in _entry().items() if k != "databaseId"}
        instance = enforcer()
        instance.enforce.side_effect = lambda obj, act: obj["databaseId"] == ""
        response, _ = self._list_with(instance, [orphan, _entry(database_id=DB)])
        assert [e["entryId"] for e in body_of(response)["entries"]] == [orphan["entryId"]]
        assert ({"object__type": "complianceEvaluation", "databaseId": "", "complianceState": ""},
                "GET") in _enforced(instance)


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

    @pytest.mark.parametrize("token", [
        {"partition": "compliance_check", "key": None},
        {"entryId": "e", "eventType": "compliance_check", "timestamp": "t"},
        {"databaseId:assetId": f"{DB}:{ASSET}", "timestamp": "t"},
        {"entryId": 1, "databaseId:assetId": f"{DB}:{ASSET}", "timestamp": "t"},
    ], ids=["listing-token", "event-type-key", "missing-table-key", "non-string-key"])
    def test_an_asset_history_token_from_another_listing_is_rejected_before_any_read(self, token):
        response, table = _run(rest_event("GET", ASSET_PATH, ASSET_PARAMS,
                                          query_params={"startingToken": _token(token)}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        table.query.assert_not_called()

    @pytest.mark.parametrize("path,params", [(LIST_PATH, None), (ASSET_PATH, ASSET_PARAMS)])
    @pytest.mark.parametrize("token", [_token([1]), _token("key"), _token({}), _token(None)],
                             ids=["list", "string", "empty-object", "null"])
    def test_a_token_that_is_not_an_object_is_rejected(self, path, params, token):
        response, table = _run(rest_event("GET", path, params, query_params={"startingToken": token}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        table.query.assert_not_called()

    @pytest.mark.parametrize("token", [
        {"partition": "compliance_check", "key": None},
        {"partition": "compliance_check",
         "key": {"entryId": "e", "eventType": "compliance_check", "timestamp": "t"}},
        {"entryId": "e", "eventType": "compliance_check", "timestamp": "t"},
        {"entryId": "e", "databaseId:assetId": f"{DB}:{ASSET}", "timestamp": "t"},
        {"allListPartition": LIST_PARTITION, "timestamp": "t"},
        {"entryId": 1, "allListPartition": LIST_PARTITION, "timestamp": "t"},
        {"entryId": "e", "allListPartition": None, "timestamp": "t"},
    ], ids=["walk-token-without-key", "walk-token-with-key", "event-type-key", "asset-index-key",
            "key-missing-the-table-key", "non-string-table-key", "null-partition"])
    def test_an_unfiltered_listing_token_that_is_not_the_date_index_key_is_rejected_before_any_read(
            self, token):
        """A token shaped for the per-asset or per-event-type listing, or one carrying a partition
        plus key, never reaches ExclusiveStartKey, where DynamoDB would fail it as an internal
        error."""
        response, table = _run(rest_event("GET", LIST_PATH, query_params={"startingToken": _token(token)}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        table.query.assert_not_called()

    @pytest.mark.parametrize("token", [
        {"partition": "exception_granted", "key": None},
        {"partition": "exception_granted",
         "key": {"entryId": "e", "eventType": "exception_granted", "timestamp": "t"}},
        {"key": {"entryId": "e", "eventType": "compliance_check", "timestamp": "t"}},
        {"partition": "compliance_check", "key": "not-an-object"},
        {"partition": "compliance_check", "key": [1]},
        {"partition": "compliance_check", "key": {"eventType": "compliance_check", "timestamp": "t"}},
        {"partition": "compliance_check",
         "key": {"entryId": "e", "databaseId:assetId": "db:a", "timestamp": "t"}},
        {"partition": "compliance_check",
         "key": {"entryId": "e", "allListPartition": LIST_PARTITION, "timestamp": "t"}},
        {"eventType": "compliance_check", "timestamp": "t"},
        {"entryId": "e", "allListPartition": LIST_PARTITION, "timestamp": "t"},
    ], ids=["other-partition", "other-partition-with-key", "no-partition", "string-key", "list-key",
            "key-missing-the-table-key", "asset-listing-key", "date-listing-key",
            "bare-key-under-filter", "bare-date-key-under-filter"])
    def test_a_filtered_listing_token_outside_its_partition_is_rejected_before_any_read(self, token):
        """A token naming a partition other than the filter, lacking the partition shape, or
        carrying a key that is not the EventTypeIndex's never reaches ExclusiveStartKey."""
        other_partition = token.get("partition") if token.get("partition") != "compliance_check" else None
        response, table = _run(rest_event("GET", LIST_PATH, query_params={
            "eventType": "compliance_check", "startingToken": _token(token)}))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Invalid pagination token"
        if other_partition:
            assert other_partition not in response["body"]
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

    def test_the_unfiltered_listing_is_one_newest_first_query_on_the_date_index(self):
        response, table = _run(rest_event("GET", LIST_PATH, query_params={"limit": "7"}),
                               pages=[{"Items": [_entry(), _entry("quarantine_released")]}])
        assert [e["eventType"] for e in body_of(response)["entries"]] == [
            "compliance_check", "quarantine_released"]
        assert "NextToken" not in body_of(response)
        # One page is one read: the listing is a query on the date index, not a read per event type.
        assert table.query.call_count <= 1
        query = table.query.call_args.kwargs
        assert query["IndexName"] == "AuditByDateGSI"
        assert query["ScanIndexForward"] is False
        assert query["Limit"] == 7
        assert "ExclusiveStartKey" not in query
        assert _partition_of(query) == LIST_PARTITION
        assert type(query["KeyConditionExpression"]).__name__ == "Equals"

    def test_a_full_page_without_a_last_key_has_no_token(self):
        response, table = _run(rest_event("GET", LIST_PATH, query_params={"limit": "2"}),
                               pages=[{"Items": [_entry(), _entry()]}])
        assert len(body_of(response)["entries"]) == 2
        assert "NextToken" not in body_of(response)
        assert table.query.call_count <= 1
        assert table.query.call_args.kwargs["IndexName"] == "AuditByDateGSI"

    def test_the_token_round_trips_and_page_two_resumes_where_page_one_stopped(self):
        """Page two is read from page one's LastEvaluatedKey, not from the start; the two pages
        together are the full set in index order."""
        entries = [dict(_entry(), entryId=f"e{i}") for i in range(3)]
        last_key = _date_key("e1", "t1")
        pager = Pager({"Items": entries[:2], "LastEvaluatedKey": last_key},
                      {"Items": entries[2:]}, name="AuditByDateGSI")

        response, _ = _run(rest_event("GET", LIST_PATH, query_params={"limit": "2"}), pages=pager)
        page_one = body_of(response)
        assert [e["entryId"] for e in page_one["entries"]] == ["e0", "e1"]
        assert _decode(page_one["NextToken"]) == last_key

        response, _ = _run(
            rest_event("GET", LIST_PATH,
                       query_params={"limit": "2", "startingToken": page_one["NextToken"]}),
            pages=pager)
        page_two = body_of(response)
        assert [e["entryId"] for e in page_two["entries"]] == ["e2"]
        assert "NextToken" not in page_two
        pager.assert_paged_to_exhaustion()
        assert last_key in pager.resumed_from
        assert all(call["IndexName"] == "AuditByDateGSI" for call in pager.calls)
        assert [e["entryId"] for e in page_one["entries"] + page_two["entries"]] == ["e0", "e1", "e2"]

    def test_a_token_carrying_extra_attributes_resumes_from_the_index_key_alone(self):
        last_key = _date_key("e1", "t1")
        pager = Pager({"Items": [_entry()], "LastEvaluatedKey": last_key}, {"Items": []})
        token = _token(dict(last_key, eventType="compliance_check", databaseId=DB))
        response, _ = _run(rest_event("GET", LIST_PATH, query_params={"startingToken": token}),
                           pages=pager)
        assert response["statusCode"] == 200
        assert last_key in pager.resumed_from
        assert all(resumed == last_key for resumed in pager.resumed_from)

    @pytest.mark.parametrize("window,shape,bounds", [
        ({"startDate": "2026-01-01", "endDate": "2026-02-01"}, "Between", ("2026-01-01", "2026-02-01")),
        ({"startDate": "2026-01-01"}, "GreaterThanEquals", ("2026-01-01",)),
        ({"endDate": "2026-02-01"}, "LessThanEquals", ("2026-02-01",)),
    ], ids=["both", "start-only", "end-only"])
    def test_a_date_window_narrows_the_date_index_key_condition(self, window, shape, bounds):
        _, table = _run(rest_event("GET", LIST_PATH, query_params=window), pages=[{"Items": []}])
        query = table.query.call_args.kwargs
        assert query["IndexName"] == "AuditByDateGSI"
        assert _partition_of(query) == LIST_PARTITION
        condition = query["KeyConditionExpression"]
        assert type(condition).__name__ == "And"
        timestamp = condition._values[1]
        assert type(timestamp).__name__ == shape
        assert timestamp._values[0].name == "timestamp"
        assert timestamp._values[1:] == bounds

    def test_a_filtered_listing_pages_one_partition_and_carries_it_in_the_token(self):
        last_key = {"eventType": "compliance_check", "timestamp": "t", "entryId": "e"}
        response, table = _run(
            rest_event("GET", LIST_PATH, query_params={"eventType": "compliance_check", "limit": "1"}),
            pages=[{"Items": [_entry()], "LastEvaluatedKey": last_key}])
        assert table.query.call_count <= 1
        query = table.query.call_args.kwargs
        assert query["IndexName"] == "EventTypeIndex"
        assert query["ScanIndexForward"] is False
        assert query["Limit"] == 1
        assert _partition_of(query) == "compliance_check"
        assert _decode(body_of(response)["NextToken"]) == {
            "partition": "compliance_check", "key": last_key}

    def test_a_filtered_listing_token_resumes_its_partition_from_its_key(self):
        last_key = {"eventType": "exception_granted", "timestamp": "t", "entryId": "e"}
        token = _token({"partition": "exception_granted", "key": last_key})
        response, table = _run(
            rest_event("GET", LIST_PATH,
                       query_params={"eventType": "exception_granted", "startingToken": token}),
            pages=[{"Items": [_entry("exception_granted")]}])
        assert response["statusCode"] == 200
        assert "NextToken" not in body_of(response)
        assert table.query.call_count <= 1
        query = table.query.call_args.kwargs
        assert query["IndexName"] == "EventTypeIndex"
        assert _partition_of(query) == "exception_granted"
        assert query["ExclusiveStartKey"] == last_key

    def test_a_filtered_listing_token_without_a_key_starts_its_partition_from_the_top(self):
        token = _token({"partition": "exception_granted", "key": None})
        response, table = _run(
            rest_event("GET", LIST_PATH,
                       query_params={"eventType": "exception_granted", "startingToken": token}),
            pages=[{"Items": []}])
        assert response["statusCode"] == 200
        assert "ExclusiveStartKey" not in table.query.call_args.kwargs
