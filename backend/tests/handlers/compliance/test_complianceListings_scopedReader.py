# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The compliance listings that read rows of many databases, for a reader who may read one of them.

The unfiltered audit trail, the quarantine listing and the cascade listing read an index that holds
every database's rows and decide each row for the reader. A reader who may read one database of
fifty finds one row in fifty, so a page fills slowly. Before, each row was decided on the engine
(one `enforce` per row, about 9 ms deployed because each refusal also writes an authorization audit
record), and a page of the audit trail ran past the API timeout. Now the engine is asked once per
database id of a request, and `listing.query_page` bounds both the reads and the time spent deciding
rows, so every request answers quickly with a token, and walking the tokens returns exactly the rows
the reader may read, in the listing's order.

The indexes are in-memory doubles. Each request's reads and engine decisions are counted and bounded,
and the time `listing.query_page` spends deciding rows is measured with an injected clock that
charges every decision a fixed cost (`_decision_cost`), so where the decision budget ends a page
does not depend on the host's load: a cheap decision leaves every page to the read budget, a costly
one ends each page on the decision budget. The wall time of each request is printed (`SCOPED ...`)
for the record, never asserted. The audit trail is decided by the production enforcer of the
shipped Compliance Admin template, scoped to one database.
"""

import math
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.compliance._harness import (
    USER, body_of, claims_for, enforcer, live_only_asset_table, mirror_engine, real_constants,
    rest_event,
)
from backend.tests.handlers.auth.test_permission_templates_compliance_binding import (  # noqa: F401
    roles_enforcer,
)
from common.compliance import listing
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceQuarantineService as quarantine_svc

DATABASES = [f"db-{n:02d}" for n in range(50)]
READER_DB = "db-25"
AUDIT_ROWS = 20_000
STATE_ROWS = 5_000
CASCADE_ROWS = 5_000
# A request decides each database id once, and an allowed one a few times more (its archive's
# stored id and the asset check on it); anything near a call per row is the old behaviour.
ENGINE_CALLS_BOUND = len(DATABASES) + 10
# What the injected clock charges each row decision (powers of two, so the sums are exact): a cheap
# decision (a decision asked once per database id) spends a fraction of the 1 s decision budget in
# the 2,500 rows five reads hold, so the reads end the page; a costly one (about the 1 ms of a
# per-row engine decision) spends the budget after 1,024 decisions.
CHEAP_DECISION_SECONDS = 1 / 65536
COSTLY_DECISION_SECONDS = 1 / 1024
BASE_TIME = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    """The real permission field matrix for the production enforcer the audit tests build."""
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


class _DecisionClock:
    """The clock `listing.query_page` measures its decision budget with: it moves only when a row is
    decided, by `step` seconds a decision, and counts the decisions."""

    def __init__(self, step):
        self.step, self.decisions, self._now = step, 0, 0.0

    def __call__(self):
        return self._now

    def counted(self, accept):
        def decided(item):
            self.decisions += 1
            self._now += self.step
            return accept(item)
        return decided


@contextmanager
def _decision_cost(step):
    """Every listing the handlers page through `listing.query_page` decides its rows on a
    `_DecisionClock` charging `step` seconds a decision, instead of the host's clock."""
    clock = _DecisionClock(step)
    real = listing.query_page

    def query_page(*args, accept=None, **kwargs):
        return real(*args, accept=clock.counted(accept) if accept else None, clock=clock,
                    **kwargs)
    with patch.object(listing, "query_page", query_page):
        yield clock


def _decision_bounds(request, step, last):
    """A request's decisions: at most the rows its reads hold, and the time they cost (`step`
    each) within one decision of the decision budget. With a cheap decision the budget is never
    spent, so every request but the last ends on a full page or the read budget; with a costly one
    every request but the last ends on the budget, after exactly the decisions it pays for."""
    assert request["decisions"] <= request["reads"] * listing.FILTERED_READ_LIMIT, request
    budget_decisions = math.ceil(listing.DEFAULT_DECISION_BUDGET_SECONDS / step)
    assert request["decisions"] <= budget_decisions, request
    if last:
        return
    if budget_decisions > listing.DEFAULT_READ_BUDGET * listing.FILTERED_READ_LIMIT:
        assert (request["entries"] == request["maxItems"]
                or request["reads"] == listing.DEFAULT_READ_BUDGET), request
    else:
        assert request["decisions"] == budget_decisions, request


# --------------------------------------------------------------------------------------------
# The audit trail: 20,000 entries over 50 databases, the production enforcer of one Compliance
# Admin
# --------------------------------------------------------------------------------------------

def _audit_entry(index):
    database_id = DATABASES[index % len(DATABASES)]
    asset_id = f"x{index % 400:04d}"
    item = build_audit_item(database_id, asset_id, "compliance_check", "reviewer", {"i": index},
                            timestamp=(BASE_TIME + timedelta(seconds=index)).isoformat(),
                            asset_name=f"Bracket {index % 400:04d}")
    item["entryId"] = f"e{index:06d}"
    return item


@pytest.fixture(scope="module")
def audit_entries():
    return [_audit_entry(index) for index in range(AUDIT_ROWS)]


class _OrderedIndex:
    """The audit trail's AuditByDateGSI, newest first, keyed by its three key attributes."""

    def __init__(self, entries):
        self._newest_first = sorted(entries, key=lambda e: (e["timestamp"], e["entryId"]),
                                    reverse=True)
        self._position = {entry["entryId"]: index for index, entry in enumerate(self._newest_first)}
        self.reads = 0

    def query(self, **kwargs):
        assert kwargs["IndexName"] == "AuditByDateGSI"
        assert kwargs["ScanIndexForward"] is False
        assert "FilterExpression" not in kwargs, "the unfiltered listing sends no filter"
        self.reads += 1
        start = 0
        if kwargs.get("ExclusiveStartKey"):
            start = self._position[kwargs["ExclusiveStartKey"]["entryId"]] + 1
        page = self._newest_first[start:start + kwargs["Limit"]]
        response = {"Items": [dict(entry) for entry in page]}
        if start + kwargs["Limit"] < len(self._newest_first):
            last = page[-1]
            response["LastEvaluatedKey"] = {"entryId": last["entryId"],
                                            "allListPartition": last["allListPartition"],
                                            "timestamp": last["timestamp"]}
        return response


class _EngineCount:
    """Counts the engine's decisions (every `CasbinEnforcerService.enforce`, the asset checks of
    `AssetAccess` included) and the refusals that each write an authorization audit record."""

    def __init__(self, real):
        self.calls = 0
        service = real.service_object
        original = service.enforce

        def counting(obj, act):
            self.calls += 1
            return original(obj, act)
        service.enforce = counting

    @staticmethod
    def refusals():
        return authz_module.log_authorization.call_count


def _stand_in_for(real):
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = real.enforce
    return mirror_engine(stand_in, real)


def _walk_audit(table, stand_in, count, max_items, step=CHEAP_DECISION_SECONDS):
    """Every request of the walk from the first page to the last: (entries, requests), each
    request a dict of its entries, reads, row decisions (`step` seconds each on the injected
    clock), engine calls, refusals and wall seconds (printed only)."""
    requests, entries, token = [], [], None
    while True:
        params = {"maxItems": str(max_items)}
        if token:
            params["startingToken"] = token
        reads, calls, refusals = table.reads, count.calls if count else 0, _EngineCount.refusals()
        with patch.object(audit_svc, "request_to_claims", claims_for(USER)), \
                patch.object(audit_svc, "CasbinEnforcer", return_value=stand_in), \
                patch.object(audit_svc, "audit_table", table), \
                patch.object(audit_svc, "asset_table", live_only_asset_table()), \
                _decision_cost(step) as clock:
            started = time.perf_counter()
            response = audit_svc.lambda_handler(
                rest_event("GET", "/compliance/audit", query_params=params), MagicMock())
            seconds = time.perf_counter() - started
        assert response["statusCode"] == 200, response
        body = body_of(response)
        entries.extend(body["entries"])
        requests.append({"entries": len(body["entries"]), "maxItems": max_items,
                         "reads": table.reads - reads, "decisions": clock.decisions,
                         "engine": (count.calls - calls) if count else None,
                         "refusals": _EngineCount.refusals() - refusals, "seconds": seconds})
        token = body.get("NextToken")
        if not token:
            return entries, requests
        assert len(requests) < 200, "the walk does not end"


def _summary(name, requests):
    worst = max(requests, key=lambda r: r["seconds"])

    def refusals(request):
        return "" if request["refusals"] is None else f", {request['refusals']} refusals"
    print(f"SCOPED {name}: {len(requests)} requests; first page {requests[0]['entries']} rows, "
          f"{requests[0]['reads']} reads, {requests[0].get('decisions')} decisions, "
          f"{requests[0]['engine']} engine calls"
          f"{refusals(requests[0])}, {requests[0]['seconds'] * 1000:.0f} ms; "
          f"worst request {worst['seconds'] * 1000:.0f} ms ({worst['reads']} reads, "
          f"{worst['engine']} engine calls{refusals(worst)}); max reads "
          f"{max(r['reads'] for r in requests)}, max engine calls "
          f"{max(r['engine'] or 0 for r in requests)}")


@pytest.fixture
def admin_of_one_database(roles_enforcer):
    real = roles_enforcer({"compliance": ("compliance-admin.json", READER_DB)})
    count = _EngineCount(real)
    return _stand_in_for(real), count


@pytest.mark.unit
class TestTheAuditTrailForAReaderOfOneDatabase:

    @pytest.mark.parametrize("max_items,step", [
        (50, CHEAP_DECISION_SECONDS), (200, CHEAP_DECISION_SECONDS),
        (200, COSTLY_DECISION_SECONDS)], ids=["50-cheap", "200-cheap", "200-costly"])
    def test_every_page_is_bounded_and_the_walk_returns_exactly_the_readers_entries(
            self, audit_entries, admin_of_one_database, max_items, step):
        stand_in, count = admin_of_one_database
        table = _OrderedIndex(audit_entries)
        entries, requests = _walk_audit(table, stand_in, count, max_items, step)

        expected = [entry["entryId"] for entry in sorted(
            audit_entries, key=lambda e: (e["timestamp"], e["entryId"]), reverse=True)
            if entry["databaseId"] == READER_DB]
        assert [entry["entryId"] for entry in entries] == expected
        assert {entry["databaseId"] for entry in entries} == {READER_DB}
        # The walk took several bounded requests (one in fifty entries is the reader's).
        assert len(requests) > 1
        for position, request in enumerate(requests):
            assert request["reads"] <= listing.DEFAULT_READ_BUDGET
            assert request["engine"] <= ENGINE_CALLS_BOUND, request
            # One refusal per database the reader may not read, not one per entry.
            assert request["refusals"] <= len(DATABASES), request
            _decision_bounds(request, step, last=position == len(requests) - 1)
        _summary(f"audit maxItems={max_items} step={step} (400 of 20,000 entries readable)",
                 requests)

    def test_the_first_page_for_a_reader_of_every_database_is_one_read(self, audit_entries):
        table = _OrderedIndex(audit_entries)
        admin = enforcer()
        body, requests = _walk_audit_first(table, admin)
        newest = sorted(audit_entries, key=lambda e: (e["timestamp"], e["entryId"]),
                        reverse=True)[:100]
        assert [e["entryId"] for e in body["entries"]] == [e["entryId"] for e in newest]
        assert requests["entries"] == 100 and requests["reads"] == 1
        # Two decisions per database id on the page (the live id, and its archive's stored id
        # once, which this caller is allowed: no archive is read), not one per entry.
        assert admin.enforce.call_count <= 2 * len(DATABASES)
        print(f"SCOPED audit admin first page: {requests['reads']} read, "
              f"{admin.enforce.call_count} engine calls, {requests['seconds'] * 1000:.0f} ms")


def _walk_audit_first(table, stand_in):
    with patch.object(audit_svc, "request_to_claims", claims_for(USER)), \
            patch.object(audit_svc, "CasbinEnforcer", return_value=stand_in), \
            patch.object(audit_svc, "audit_table", table), \
            patch.object(audit_svc, "asset_table", live_only_asset_table()):
        reads = table.reads
        started = time.perf_counter()
        response = audit_svc.lambda_handler(rest_event("GET", "/compliance/audit"), MagicMock())
        seconds = time.perf_counter() - started
    assert response["statusCode"] == 200
    body = body_of(response)
    return body, {"entries": len(body["entries"]), "reads": table.reads - reads,
                  "seconds": seconds}


# --------------------------------------------------------------------------------------------
# The quarantine listing and the cascade listing, with a counting stand-in engine
# --------------------------------------------------------------------------------------------

def _reader_of(database_id, cascade_ids_read=False):
    """A CasbinEnforcer stand-in allowing everything on `database_id` (and its archive) only. With
    `cascade_ids_read`, the caller's policy reads the cascade id (each cascade decided on its
    own)."""
    instance = enforcer()

    def decide(obj, action):
        return obj.get("databaseId", "").split("#")[0] == database_id
    instance.enforce.side_effect = decide
    if cascade_ids_read:
        instance.policy_object_fields.return_value = frozenset({"cascadeId"})
    return instance


def _engine_calls(instance):
    return instance.enforce.call_count + instance.service_object.enforce.call_count


def _quarantined_rows():
    return [{"databaseId": DATABASES[index % len(DATABASES)], "assetId": f"q{index:05d}",
             "complianceState": "quarantined", "assetName": f"Part {index:05d}",
             "schemaName": "std", "schemaSource": "database"}
            for index in range(STATE_ROWS)]


class _StateIndex:
    """ComplianceStateIndex (complianceState, databaseId), ascending; ties by asset id."""

    def __init__(self, rows):
        self._rows = sorted(rows, key=lambda r: (r["databaseId"], r["assetId"]))
        self._position = {(r["databaseId"], r["assetId"]): i for i, r in enumerate(self._rows)}
        self.reads = 0

    def query(self, **kwargs):
        assert kwargs["IndexName"] == "ComplianceStateIndex"
        assert "FilterExpression" not in kwargs
        self.reads += 1
        start = 0
        if kwargs.get("ExclusiveStartKey"):
            key = kwargs["ExclusiveStartKey"]
            start = self._position[(key["databaseId"], key["assetId"])] + 1
        page = self._rows[start:start + kwargs["Limit"]]
        response = {"Items": [dict(row) for row in page]}
        if start + kwargs["Limit"] < len(self._rows):
            response["LastEvaluatedKey"] = {name: page[-1][name]
                                            for name in listing.COMPLIANCE_STATE_INDEX_KEYS}
        return response


def _walk(module, path, params, table_patches, instance, rows_key, max_requests=400,
          step=CHEAP_DECISION_SECONDS):
    """Every request of a listing walk: (rows, requests), each row decision costing `step` seconds
    on the injected clock."""
    requests, rows, token = [], [], None
    while True:
        query = dict(params)
        if token:
            query["startingToken"] = token
        calls, archive_reads = _engine_calls(instance), table_patches["asset_table"].query.call_count
        index = table_patches["index"]
        reads = index.reads
        with patch.object(module, "request_to_claims", claims_for(USER)), \
                patch.object(module, "CasbinEnforcer", return_value=instance), \
                patch.object(*table_patches["index_patch"], index), \
                patch.object(*table_patches["asset_patch"], table_patches["asset_table"]), \
                _decision_cost(step) as clock:
            started = time.perf_counter()
            response = module.lambda_handler(rest_event("GET", path, query_params=query),
                                             MagicMock())
            seconds = time.perf_counter() - started
        assert response["statusCode"] == 200, response
        body = body_of(response)
        rows.extend(body[rows_key])
        requests.append({"entries": len(body[rows_key]), "maxItems": int(params["maxItems"]),
                         "reads": index.reads - reads, "decisions": clock.decisions,
                         "engine": _engine_calls(instance) - calls,
                         "archiveReads": table_patches["asset_table"].query.call_count
                         - archive_reads,
                         "refusals": None, "seconds": seconds})
        token = body.get("NextToken")
        if not token:
            return rows, requests
        assert len(requests) < max_requests, "the walk does not end"


@pytest.mark.unit
class TestTheQuarantineListingForAReaderOfOneDatabase:

    def test_every_page_is_bounded_and_the_walk_returns_exactly_the_readers_rows(self):
        rows = _quarantined_rows()
        instance = _reader_of(READER_DB)
        asset_table = live_only_asset_table()
        listed, requests = _walk(
            quarantine_svc, "/compliance/quarantine", {"maxItems": "100"},
            {"index": _StateIndex(rows), "index_patch": (quarantine_svc, "asset_state_table"),
             "asset_table": asset_table, "asset_patch": (quarantine_svc, "asset_table")},
            instance, "quarantinedAssets")
        expected = sorted((r["assetId"] for r in rows if r["databaseId"] == READER_DB))
        assert [r["assetId"] for r in listed] == expected
        assert all(r["permissions"] == {"release": True, "exception": True} for r in listed)
        assert len(requests) > 1
        for position, request in enumerate(requests):
            assert request["reads"] <= listing.DEFAULT_READ_BUDGET
            assert request["engine"] <= ENGINE_CALLS_BOUND, request
            # Only the readable database's archived assets are read, never the other 49.
            assert request["archiveReads"] <= 1, request
            _decision_bounds(request, CHEAP_DECISION_SECONDS, last=position == len(requests) - 1)
        _summary("quarantine maxItems=100 (100 of 5,000 rows readable)", requests)


def _cascade_rows(count=CASCADE_ROWS):
    return [{"cascadeId": f"c{index:05d}", "state": "completed",
             "createdAt": (BASE_TIME + timedelta(seconds=index)).isoformat(),
             "triggeredByDatabaseId": DATABASES[index % len(DATABASES)],
             "triggeredByAssetId": f"p{index:05d}", "triggeredByAssetName": f"Parent {index:05d}"}
            for index in range(count)]


class _CascadeStateIndex:
    """StateIndex (state, createdAt), newest first."""

    def __init__(self, rows):
        self._rows = sorted(rows, key=lambda r: (r["createdAt"], r["cascadeId"]), reverse=True)
        self._position = {r["cascadeId"]: i for i, r in enumerate(self._rows)}
        self.reads = 0

    def query(self, **kwargs):
        assert kwargs["IndexName"] == "StateIndex" and kwargs["ScanIndexForward"] is False
        assert "FilterExpression" not in kwargs
        self.reads += 1
        start = 0
        if kwargs.get("ExclusiveStartKey"):
            start = self._position[kwargs["ExclusiveStartKey"]["cascadeId"]] + 1
        page = self._rows[start:start + kwargs["Limit"]]
        response = {"Items": [dict(row) for row in page]}
        if start + kwargs["Limit"] < len(self._rows):
            response["LastEvaluatedKey"] = {name: page[-1][name]
                                            for name in cascade_svc.CASCADE_STATE_INDEX_KEYS}
        return response


def _walk_cascades(rows, instance, max_items="100", max_requests=400,
                   step=CHEAP_DECISION_SECONDS):
    asset_table = live_only_asset_table()
    return _walk(cascade_svc, "/compliance/cascades",
                 {"state": "completed", "maxItems": max_items},
                 {"index": _CascadeStateIndex(rows), "index_patch": (cascade_svc, "cascade_table"),
                  "asset_table": asset_table, "asset_patch": (cascade_svc.store, "asset_table")},
                 instance, "cascades", max_requests, step)


@pytest.mark.unit
class TestTheCascadeListingForAReaderOfOneDatabase:

    def test_every_page_is_bounded_and_the_walk_returns_exactly_the_readers_cascades(self):
        rows = _cascade_rows()
        instance = _reader_of(READER_DB)
        listed, requests = _walk_cascades(rows, instance)
        expected = [r["cascadeId"] for r in sorted(rows, key=lambda r: r["createdAt"],
                                                   reverse=True)
                    if r["triggeredByDatabaseId"] == READER_DB]
        assert [r["cascadeId"] for r in listed] == expected
        assert len(requests) > 1
        for position, request in enumerate(requests):
            assert request["reads"] <= listing.DEFAULT_READ_BUDGET
            # Two checks per database id (the evaluation object, then the cascade object).
            assert request["engine"] <= 2 * ENGINE_CALLS_BOUND, request
            _decision_bounds(request, CHEAP_DECISION_SECONDS, last=position == len(requests) - 1)
        _summary("cascades state=completed maxItems=100 (100 of 5,000 readable)", requests)

    def test_a_policy_that_reads_the_cascade_id_is_bounded_by_the_decision_budget(self):
        # Each cascade is decided on its own (the policy reads cascadeId), and the injected clock
        # charges each decision 1/128 s; the budget, lowered to 1/16 s, ends each page after
        # exactly eight decisions.
        rows = _cascade_rows(240)
        instance = _reader_of("db-02", cascade_ids_read=True)
        with patch.object(listing, "DEFAULT_DECISION_BUDGET_SECONDS", 1 / 16):
            listed, requests = _walk_cascades(rows, instance, max_items="100", step=1 / 128)
            for position, request in enumerate(requests):
                _decision_bounds(request, 1 / 128, last=position == len(requests) - 1)
        expected = [r["cascadeId"] for r in sorted(rows, key=lambda r: r["createdAt"],
                                                   reverse=True)
                    if r["triggeredByDatabaseId"] == "db-02"]
        assert [r["cascadeId"] for r in listed] == expected
        assert len(requests) == len(rows) // 8
        # Each cascade was decided on the engine, not once per database.
        assert sum(request["engine"] for request in requests) >= len(rows)


# --------------------------------------------------------------------------------------------
# `listing.query_page`: the decision budget
# --------------------------------------------------------------------------------------------

class _Pages:
    """Scripted reads keyed on `ExclusiveStartKey` (`k` of the last item of the previous page)."""

    def __init__(self, *pages):
        self._pages = {None: pages[0]}
        for previous, page in zip(pages, pages[1:]):
            self._pages[previous["LastEvaluatedKey"]["k"]] = page
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        start = kwargs.get("ExclusiveStartKey")
        return self._pages[start["k"] if start else None]


def _rows(start, stop):
    return [{"k": f"i{n:03d}", "keep": n % 3 == 0} for n in range(start, stop)]


class _Clock:
    """A clock that advances `step` seconds on every reading."""

    def __init__(self, step):
        self.now, self._step = 0.0, step

    def __call__(self):
        self.now += self._step
        return self.now


@pytest.mark.unit
class TestTheDecisionBudget:

    def test_a_cheap_accept_fills_the_page_as_before(self):
        table = _Pages({"Items": _rows(0, 30)})
        items, key, reads = listing.query_page(table, {}, 5, ("k",),
                                               accept=lambda item: item["keep"])
        assert [i["k"] for i in items] == ["i000", "i003", "i006", "i009", "i012"]
        assert key == {"k": "i012"} and reads == 1

    def test_the_budget_ends_the_page_after_the_row_decided_with_its_key(self):
        # Each decision takes one clock step (0.05 s); a budget of 0.33 s is spent by the seventh
        # decision (i006), an accepted row: the page ends there, with its key.
        table = _Pages({"Items": _rows(0, 30), "LastEvaluatedKey": {"k": "i029"}})
        items, key, reads = listing.query_page(table, {}, 10, ("k",),
                                               accept=lambda item: item["keep"],
                                               decision_budget=0.33, clock=_Clock(0.05))
        assert [i["k"] for i in items] == ["i000", "i003", "i006"]
        assert key == {"k": "i006"} and reads == 1

    def test_a_refused_row_that_spends_the_budget_ends_the_page_with_its_key(self):
        table = _Pages({"Items": _rows(0, 30), "LastEvaluatedKey": {"k": "i029"}})
        items, key, _ = listing.query_page(table, {}, 10, ("k",),
                                           accept=lambda item: item["keep"],
                                           decision_budget=0.23, clock=_Clock(0.05))
        # The fifth decision (i004, refused) spends the budget: the page is the rows kept before
        # it, and resumes after i004.
        assert [i["k"] for i in items] == ["i000", "i003"]
        assert key == {"k": "i004"}

    def test_the_last_row_of_the_query_ends_it_without_a_token(self):
        table = _Pages({"Items": _rows(0, 2)})
        items, key, _ = listing.query_page(table, {}, 10, ("k",),
                                           accept=lambda item: True,
                                           decision_budget=0.04, clock=_Clock(0.05))
        assert [i["k"] for i in items] == ["i000"] and key == {"k": "i000"}
        follow, key, _ = listing.query_page(_Pages({"Items": _rows(1, 2)}), {}, 10, ("k",),
                                            accept=lambda item: False,
                                            decision_budget=0.04, clock=_Clock(0.05))
        assert follow == [] and key is None

    def test_resuming_from_each_token_returns_every_accepted_row_once_in_order(self):
        rows = _rows(0, 90)
        pages = [{"Items": rows[0:30], "LastEvaluatedKey": {"k": "i029"}},
                 {"Items": rows[30:60], "LastEvaluatedKey": {"k": "i059"}},
                 {"Items": rows[60:90]}]

        class Resumable:
            def query(self, **kwargs):
                start = kwargs.get("ExclusiveStartKey")
                begin = 0 if not start else int(start["k"][1:]) + 1
                for page in pages:
                    ks = [int(item["k"][1:]) for item in page["Items"]]
                    if begin <= ks[-1]:
                        offset = ks.index(begin) if begin in ks else 0
                        return dict(page, Items=page["Items"][offset:])
                return {"Items": []}

        found, key, requests = [], None, 0
        while True:
            items, key, _ = listing.query_page(Resumable(), {}, 7, ("k",), start_key=key,
                                               accept=lambda item: item["keep"],
                                               decision_budget=0.38, clock=_Clock(0.05))
            found.extend(item["k"] for item in items)
            requests += 1
            if key is None:
                break
            assert requests < 100
        assert found == [row["k"] for row in rows if row["keep"]]
        assert requests > 3

    def test_once_per_key_asks_once_per_distinct_key(self):
        asked = []
        decided = listing.once_per_key(lambda key: asked.append(key) or key == "a")
        assert [decided(k) for k in ("a", "b", "a", "b", "c")] == [True, False, True, False, False]
        assert asked == ["a", "b", "c"]
