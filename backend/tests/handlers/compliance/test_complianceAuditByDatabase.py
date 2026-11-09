# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The audit listing reads only the databases a page can show (D173).

A database filter read the date-ordered trail of every database and filtered it within a bounded
number of reads, so a burst of entries in other databases pushed a database's own entries off the
page: after about 3,150 entries in other databases, the first page of `fin-db1` was empty for an
administrator, and so was the unfiltered page of a Compliance Admin or Read-only user of
`fin-db1` ("Open the next page to continue"). The listing now reads a database filter from that
database's `DatabaseIndex` partition, and the unfiltered listing of a caller who may read only some
databases from each of their partitions, merged by time.

Run on moto tables built from the CDK definitions, with the production enforcer of each role (the
shipped templates and the real permission field matrix) and real retirement records. The trail:

- `fin-db1`: 286 entries (database-level and per asset, one asset tagged), the oldest;
- `recreated-db`: a history `recreated-db#deleted` (known only from its retirement record, as the
  re-created database removed its `#deleted` row) and newer entries of the live database;
- `gone-db#deleted`: a deleted database's history;
- `moving-db`: a deleted database whose move is still running, its rows still under the live id;
- `*`: schema-level entries;
- `burst-a` and `burst-b`: 3,150 entries, the newest, as round 31's binding changes wrote them.

Each role's entries are computed from the seed (`_visible`), newest first, and every listing is
walked to its end: each entry once, in order. The first page of a database filter, and of a
database-scoped caller's unfiltered listing, is full and is one read.
"""

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    body_of, claims_for, mirror_engine, real_constants, rest_event,
)
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc

# The retirement records of the trail's deleted databases are read from the moto tables.
USES_RETIREMENT_RECORD = True

FIN = "fin-db1"
FIN_TAGGED = "fin-tag"
FIN_ASSETS = ("fin-a1", "fin-a2", "fin-a3", FIN_TAGGED)
FIN_ENTRIES = 286
RECREATED = "recreated-db"
RECREATED_HISTORY = f"{RECREATED}#deleted"
GONE_HISTORY = "gone-db#deleted"
MOVING = "moving-db"
MOVING_HISTORY = f"{MOVING}#deleted"
BURSTS = ("burst-a", "burst-b")
BURST_ENTRIES = 3_150
DENIED_TAG = "restricted"
USER_ID = "audit-by-database-user"
BASE_TIME = datetime(2026, 10, 1, tzinfo=timezone.utc)
PER_ASSET_EVENTS = ("compliance_check", "compliance_check", "exception_granted",
                    "schema_binding_applied_to_asset")


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


# --------------------------------------------------------------------------------------------
# The trail
# --------------------------------------------------------------------------------------------

def _entries():
    """Every seeded entry, oldest first, with distinct timestamps (a total order)."""
    entries, clock = [], 0

    def add(database_id, asset_id, event_type, name=None):
        nonlocal clock
        clock += 1
        item = build_audit_item(
            database_id, asset_id, event_type, "administrator" if clock % 13 else "auditor-7",
            {"n": clock}, timestamp=(BASE_TIME + timedelta(seconds=clock)).isoformat(),
            asset_name=name)
        item["entryId"] = f"e{clock:05d}"
        entries.append(item)

    def block(database_id, assets, count):
        for n in range(count):
            if n % 11 == 0:
                add(database_id, "*", "schema_bound_to_database")
            else:
                asset_id = assets[n % len(assets)]
                add(database_id, asset_id, PER_ASSET_EVENTS[n % len(PER_ASSET_EVENTS)],
                    "Turbine Blade" if n % 7 == 0 else f"Bracket {n}")

    block(FIN, FIN_ASSETS, FIN_ENTRIES)
    block(RECREATED_HISTORY, ("rec-old",), 60)
    block(RECREATED, ("rec-a1", "rec-a2"), 40)
    block(GONE_HISTORY, ("gone-a1", "gone-a2"), 120)
    block(MOVING, ("mov-a1",), 10)
    for _n in range(3):
        add("*", "*", "schema_deleted")
    for n in range(BURST_ENTRIES):
        database_id = BURSTS[n % 2]
        if n % 11 == 0:
            add(database_id, "*", "schema_binding_apply_finished")
        else:
            add(database_id, f"b-{n % 5}", "schema_binding_applied_to_asset", f"Part {n}")
    return entries


ENTRIES = _entries()


def _presented(entry):
    """The database id the listing shows an entry under: a moving database's rows are its
    deleted history's (`databaseRetirement.Presenter`)."""
    return MOVING_HISTORY if entry["databaseId"] == MOVING else entry["databaseId"]


def _seed(t):
    with t["complianceAuditStorage"].batch_writer() as batch:
        for entry in ENTRIES:
            batch.put_item(Item=entry)
    for database_id in (FIN, *BURSTS, RECREATED, GONE_HISTORY, MOVING_HISTORY):
        t["databaseStorage"].put_item(Item={"databaseId": database_id})
    for base, state in ((RECREATED, "done"), ("gone-db", "done"), (MOVING, "moving")):
        t["complianceEvaluationStorage"].put_item(Item={
            "evaluationId": f"databaseRetirement#{base}", "recordType": "databaseRetirement",
            "databaseId": base, "deletedId": f"{base}#deleted", "state": state,
            "deletedAt": "2026-10-01T03:00:00+00:00", "generation": 1})
    for asset_id in FIN_ASSETS:
        t["assetStorage"].put_item(Item={
            "databaseId": FIN, "assetId": asset_id, "assetName": asset_id,
            "tags": [DENIED_TAG] if asset_id == FIN_TAGGED else ["ok"]})


# --------------------------------------------------------------------------------------------
# Roles: the production enforcer of each, and the entries it may read
# --------------------------------------------------------------------------------------------

def _template(name, role, database_id=None):
    variables = {"ROLE_NAME": role}
    if database_id:
        variables["DATABASE_ID"] = database_id
    return shipped_constraints.template_constraints(name, role, variables)


def _tag_deny(role):
    return [{
        "constraintId": "deny-tag-asset", "name": "deny-tag-asset", "objectType": "asset",
        "criteriaAnd": [{"field": "tags", "id": "t", "operator": "is_one_of",
                         "value": DENIED_TAG}],
        "criteriaOr": [],
        "groupPermissions": [{"groupId": role, "id": f"deny-tag-{action}", "permission": action,
                              "permissionType": "deny"} for action in ("GET", "PUT", "POST",
                                                                       "DELETE")],
        "userPermissions": [],
    }]


ROLES = {
    "admin": (["admin"], lambda: shipped_constraints.seeded_constraints("admin")),
    "compliance admin of fin-db1": (
        ["cadmin"], lambda: _template("compliance-admin.json", "cadmin", FIN)),
    "compliance read-only of fin-db1": (
        ["cro"], lambda: _template("compliance-readonly.json", "cro", FIN)),
    "database admin of the deleted gone-db": (
        ["dba"], lambda: _template("database-admin.json", "dba", "gone-db")),
    "database admin of the re-created database": (
        ["dba"], lambda: _template("database-admin.json", "dba", RECREATED)),
    "database admin of the database being moved": (
        ["dba"], lambda: _template("database-admin.json", "dba", MOVING)),
    "tag-deny reader of fin-db1": (
        ["dba"], lambda: _template("database-admin.json", "dba", FIN) + _tag_deny("dba")),
    "compliance global admin + database user of burst-a": (
        ["cga", "dbu"], lambda: (_template("compliance-global-admin.json", "cga")
                                 + _template("database-user.json", "dbu", BURSTS[0]))),
    "compliance read-only of a database with no entries": (
        ["cro"], lambda: _template("compliance-readonly.json", "cro", "empty-db")),
}

# The roles that may read only some databases: their unfiltered listing is merged from them.
SCOPED = ("compliance admin of fin-db1", "compliance read-only of fin-db1",
          "database admin of the deleted gone-db", "database admin of the re-created database",
          "database admin of the database being moved", "tag-deny reader of fin-db1")


def _visible(role, entry):
    """Whether `role` reads `entry` (the documented rules: the database, its deleted history, the
    asset rules decided as for the asset routes, D168)."""
    database_id, asset_id = _presented(entry), entry["assetId"]
    if role == "admin":
        return True
    if role in ("compliance admin of fin-db1", "compliance read-only of fin-db1"):
        return database_id == FIN
    if role == "database admin of the deleted gone-db":
        return database_id == GONE_HISTORY
    if role == "database admin of the re-created database":
        return database_id in (RECREATED, RECREATED_HISTORY)
    if role == "database admin of the database being moved":
        return database_id == MOVING_HISTORY
    if role == "tag-deny reader of fin-db1":
        return database_id == FIN and asset_id != FIN_TAGGED
    if role == "compliance global admin + database user of burst-a":
        return asset_id == "*" or database_id == BURSTS[0]
    return False


def _matches(entry, filters):
    if filters.get("eventType") and entry["eventType"] != filters["eventType"]:
        return False
    text = filters.get("text")
    if text and not (text in entry.get("searchText", "") or text in entry["assetId"]
                     or text in entry["actor"]):
        return False
    if filters.get("startDate") and entry["timestamp"] < filters["startDate"]:
        return False
    if filters.get("endDate") and entry["timestamp"] > filters["endDate"]:
        return False
    if filters.get("databaseId") and _presented(entry) != filters["databaseId"]:
        return False
    return True


def _expected(role, filters=None):
    """The entry ids `role` reads with `filters`, newest first."""
    filters = filters or {}
    return [entry["entryId"] for entry in reversed(ENTRIES)
            if _visible(role, entry) and _matches(entry, filters)]


def _enforcer(roles, constraints):
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    with patch.object(authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=[{"userId": USER_ID, "roleName": r} for r in roles]), \
            patch.object(authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
                         return_value=constraints):
        return authz_module.CasbinEnforcer({"tokens": [USER_ID], "roles": list(roles),
                                            "mfaEnabled": True})


class _Audit:
    """The audit table double, its queries counted by index; with `building`, a DatabaseIndex query
    is refused as DynamoDB refuses one while it builds the index after an update."""

    def __init__(self, table, building=False):
        self._table, self.building = table, building
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs.get("IndexName"))
        if self.building and kwargs.get("IndexName") == audit_svc.DATABASE_INDEX:
            raise ClientError({"Error": {
                "Code": "ValidationException",
                "Message": "Cannot read from backfilling global secondary index: DatabaseIndex"}},
                "Query")
        return self._table.query(**kwargs)

    def __getattr__(self, name):
        return getattr(self._table, name)


@pytest.fixture(scope="module")
def trail():
    previous = (authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map)
    mock = mock_aws()
    mock.start()
    resource = boto3.resource("dynamodb", region_name="us-east-1")
    patches, doubles = _cdkTables.table_doubles(resource, (audit_svc,))
    for active in patches:
        active.start()
    _seed(doubles)
    yield SimpleNamespace(doubles=doubles)
    for active in reversed(patches):
        active.stop()
    mock.stop()
    authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = previous


class _Caller:
    """One role calling the audit listing on the seeded trail."""

    def __init__(self, trail, role, building=False):
        self.role = role
        roles, constraints = ROLES[role]
        real = _enforcer(roles, constraints())
        stand_in = MagicMock(name="CasbinEnforcer")
        stand_in.enforceAPI.return_value = True
        stand_in.enforce.side_effect = real.enforce
        mirror_engine(stand_in, real)
        self.audit = _Audit(trail.doubles["complianceAuditStorage"], building)
        self._patches = [
            patch.object(audit_svc, "CasbinEnforcer", return_value=stand_in),
            patch.object(audit_svc, "request_to_claims", claims_for(USER_ID)),
            patch.object(audit_svc, "audit_table", self.audit),
            patch.object(authz_module, "log_authorization", MagicMock()),
        ]

    def __enter__(self):
        for active in self._patches:
            active.start()
        return self

    def __exit__(self, *exc):
        for active in reversed(self._patches):
            active.stop()

    def page(self, **query):
        """(status, body, audit reads) of one request."""
        if "databaseId" in query:
            query["databaseId"] = query["databaseId"].replace("#", "%23")
        reads = len(self.audit.queries)
        response = audit_svc.lambda_handler(
            rest_event("GET", "/compliance/audit", query_params=query), MagicMock())
        assert response["statusCode"] < 500, response
        return response["statusCode"], body_of(response), len(self.audit.queries) - reads

    def walk(self, page_size, token=None, **filters):
        """(entry ids, requests) of the listing from `token` (or the first page) to its end."""
        ids, requests = [], 0
        while True:
            query = dict(filters, maxItems=str(page_size))
            if token:
                query["startingToken"] = token
            status, body, _ = self.page(**query)
            assert status == 200, body
            ids.extend(entry["entryId"] for entry in body["entries"])
            requests += 1
            token = body.get("NextToken")
            if not token:
                break
            assert requests < 400, "the walk does not end"
        assert len(ids) == len(set(ids)), "an entry was returned twice"
        return ids, requests


def _token(value):
    return base64.b64encode(json.dumps(value).encode("utf-8")).decode("utf-8")


def _decoded(token):
    return json.loads(base64.b64decode(token))


def _entry(entry_id):
    return next(entry for entry in ENTRIES if entry["entryId"] == entry_id)


# --------------------------------------------------------------------------------------------
# The first page is full
# --------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestTheFirstPageIsFullWhateverOtherDatabasesHold:

    @pytest.mark.parametrize("database_id", [FIN, GONE_HISTORY, RECREATED_HISTORY, RECREATED])
    def test_a_database_filter_for_an_administrator(self, trail, database_id):
        with _Caller(trail, "admin") as caller:
            status, body, reads = caller.page(databaseId=database_id, maxItems="50")
        expected = _expected("admin", {"databaseId": database_id})
        assert status == 200
        assert [e["entryId"] for e in body["entries"]] == expected[:50]
        assert {e["databaseId"] for e in body["entries"]} == {database_id}
        # One read of the database's partition: the burst in other databases is never read.
        assert reads == 1
        assert ("NextToken" in body) == (len(expected) > 50)

    @pytest.mark.parametrize("role", SCOPED)
    def test_the_unfiltered_listing_of_a_caller_who_may_read_some_databases(self, trail, role):
        with _Caller(trail, role) as caller:
            status, body, reads = caller.page(maxItems="50")
        expected = _expected(role)
        assert status == 200
        assert [e["entryId"] for e in body["entries"]] == expected[:50]
        assert len(body["entries"]) == min(50, len(expected))
        # One DatabaseIndex query per database the caller may read, nothing of the others.
        assert set(caller.audit.queries) == {audit_svc.DATABASE_INDEX}
        assert reads <= 2

    @pytest.mark.parametrize("role", ["compliance admin of fin-db1",
                                      "compliance read-only of fin-db1"])
    def test_the_owners_fin_db1_audit_log_for_its_compliance_roles(self, trail, role):
        """The round-31 case: unfiltered, and with `fin-db1` chosen."""
        with _Caller(trail, role) as caller:
            _, unfiltered, _ = caller.page(maxItems="50")
            _, chosen, _ = caller.page(databaseId=FIN, maxItems="50")
        assert len(unfiltered["entries"]) == 50 and len(chosen["entries"]) == 50
        assert [e["entryId"] for e in unfiltered["entries"]] == [
            e["entryId"] for e in chosen["entries"]] == _expected(role)[:50]

    def test_a_caller_who_may_read_no_database_reads_nothing(self, trail):
        with _Caller(trail, "compliance read-only of a database with no entries") as caller:
            status, body, reads = caller.page(maxItems="50")
        assert (status, body, reads) == (200, {"entries": []}, 0)


# --------------------------------------------------------------------------------------------
# Every entry once, in order
# --------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestEveryEntryOnceInOrder:

    @pytest.mark.parametrize("role", sorted(ROLES))
    def test_the_unfiltered_listing(self, trail, role):
        with _Caller(trail, role) as caller:
            ids, _ = caller.walk(200)
        assert ids == _expected(role)

    @pytest.mark.parametrize("role", ["admin", "tag-deny reader of fin-db1",
                                      "compliance global admin + database user of burst-a",
                                      "database admin of the re-created database"])
    @pytest.mark.parametrize("database_id", [FIN, RECREATED, RECREATED_HISTORY, GONE_HISTORY,
                                             BURSTS[0]])
    def test_each_database_filter(self, trail, role, database_id):
        with _Caller(trail, role) as caller:
            ids, _ = caller.walk(97, databaseId=database_id)
        assert ids == _expected(role, {"databaseId": database_id})

    def test_a_merged_listing_resumes_each_database_where_its_page_stopped(self, trail):
        """Two databases merged at an odd page size: every token resumes each database after the
        last entry taken from it."""
        role = "database admin of the re-created database"
        with _Caller(trail, role) as caller:
            ids, requests = caller.walk(37)
        assert ids == _expected(role)
        assert requests == -(-len(ids) // 37)

    def test_the_moving_databases_rows_are_shown_under_its_deleted_history(self, trail):
        with _Caller(trail, "database admin of the database being moved") as caller:
            _, body, _ = caller.page(maxItems="50")
        assert body["entries"] and {e["databaseId"] for e in body["entries"]} == {MOVING_HISTORY}
        assert all(e["databaseId:assetId"].startswith(f"{MOVING_HISTORY}:")
                   for e in body["entries"])


FILTERS = [
    {"eventType": "compliance_check"},
    {"text": "turbine"},
    {"text": "auditor-7"},
    {"startDate": (BASE_TIME + timedelta(seconds=40)).isoformat(),
     "endDate": (BASE_TIME + timedelta(seconds=240)).isoformat()},
    {"eventType": "exception_granted", "text": "bracket",
     "startDate": (BASE_TIME + timedelta(seconds=20)).isoformat()},
]


@pytest.mark.unit
@pytest.mark.parametrize("filters", FILTERS, ids=["eventType", "text-name", "text-actor",
                                                  "window", "eventType-text-start"])
class TestEveryFilterCombinationKeepsItsResults:

    def test_a_database_filter(self, trail, filters):
        with _Caller(trail, "admin") as caller:
            ids, _ = caller.walk(23, databaseId=FIN, **filters)
        assert ids == _expected("admin", dict(filters, databaseId=FIN))
        assert set(caller.audit.queries) == {audit_svc.DATABASE_INDEX}

    def test_a_scoped_callers_listing(self, trail, filters):
        role = "compliance admin of fin-db1"
        with _Caller(trail, role) as caller:
            ids, _ = caller.walk(23, **filters)
        assert ids == _expected(role, filters)
        assert set(caller.audit.queries) == {audit_svc.DATABASE_INDEX}

    def test_an_administrators_listing_across_databases(self, trail, filters):
        with _Caller(trail, "admin") as caller:
            ids, _ = caller.walk(200, **filters)
        assert ids == _expected("admin", filters)
        assert audit_svc.DATABASE_INDEX not in caller.audit.queries


# --------------------------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestTokens:

    def test_a_date_index_token_issued_before_the_update_continues_on_its_index(self, trail):
        fin = _expected("admin", {"databaseId": FIN})
        last = _entry(fin[9])
        token = _token({"entryId": last["entryId"], "allListPartition": "audit",
                        "timestamp": last["timestamp"]})
        with _Caller(trail, "admin") as caller:
            ids, _ = caller.walk(50, token=token, databaseId=FIN)
        assert ids == fin[10:]
        assert set(caller.audit.queries) == {"AuditByDateGSI"}

    def test_an_event_type_token_issued_before_the_update_continues_on_its_index(self, trail):
        checks = _expected("admin", {"databaseId": FIN, "eventType": "compliance_check"})
        last = _entry(checks[4])
        token = _token({"partition": "compliance_check",
                        "key": {"entryId": last["entryId"], "eventType": "compliance_check",
                                "timestamp": last["timestamp"]}})
        with _Caller(trail, "admin") as caller:
            ids, _ = caller.walk(50, token=token, databaseId=FIN, eventType="compliance_check")
        assert ids == checks[5:]
        assert set(caller.audit.queries) == {"EventTypeIndex"}

    def test_a_merged_token_names_each_database_and_its_resume_point(self, trail):
        role = "database admin of the re-created database"
        with _Caller(trail, role) as caller:
            _, body, _ = caller.page(maxItems="30")
        named = _decoded(body["NextToken"])["databases"]
        assert set(named) <= {RECREATED, RECREATED_HISTORY}
        assert all(value is None or (isinstance(value, list) and len(value) == 2)
                   for value in named.values())

    def test_a_database_the_caller_may_not_read_is_dropped_from_a_token(self, trail):
        role = "compliance admin of fin-db1"
        token = _token({"databases": {BURSTS[0]: None, FIN: None}})
        with _Caller(trail, role) as caller:
            ids, _ = caller.walk(50, token=token)
        assert ids == _expected(role)

    @pytest.mark.parametrize("token,filters", [
        ({"databases": {f"db-{n}": None for n in range(audit_svc.MAX_MERGED_DATABASES + 1)}}, {}),
        ({"databases": {FIN: ["t"]}}, {}),
        ({"databases": {FIN: [1, "e"]}}, {}),
        ({"databases": {"bad<id>": None}}, {}),
        ({"databases": {}}, {}),
        ({"databases": {FIN: None}}, {"databaseId": FIN}),
        ({"entryId": "e", "databaseId": BURSTS[0], "timestamp": "t"}, {"databaseId": FIN}),
        ({"entryId": "e", "databaseId": FIN, "timestamp": "t"}, {}),
    ], ids=["too-many-databases", "short-position", "non-string-position", "bad-database-id",
            "no-database", "merged-token-with-a-database-filter",
            "another-databases-key", "database-key-without-a-database-filter"])
    def test_a_token_of_another_listing_is_refused_before_any_read(self, trail, token, filters):
        with _Caller(trail, "admin") as caller:
            status, body, reads = caller.page(startingToken=_token(token), **filters)
        assert (status, body["message"], reads) == (400, "Invalid pagination token", 0)


# --------------------------------------------------------------------------------------------
# While DynamoDB builds DatabaseIndex after an update
# --------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestWhileTheIndexIsBeingBuilt:

    def test_a_database_filter_reads_the_date_ordered_trail_to_the_end(self, trail):
        with _Caller(trail, "admin", building=True) as caller:
            _, first, _ = caller.page(databaseId=FIN, maxItems="200")
            ids, _ = caller.walk(200, databaseId=FIN)
        assert ids == _expected("admin", {"databaseId": FIN})
        assert "allListPartition" in _decoded(first["NextToken"])

    def test_a_scoped_callers_listing_reads_the_date_ordered_trail_to_the_end(self, trail):
        role = "compliance admin of fin-db1"
        with _Caller(trail, role, building=True) as caller:
            ids, _ = caller.walk(200)
        assert ids == _expected(role)
        assert "AuditByDateGSI" in caller.audit.queries


# --------------------------------------------------------------------------------------------
# The row the index cannot hold
# --------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestAnEntryWithoutADatabase:

    def test_it_is_written_and_listed_for_an_administrator(self, trail):
        """DynamoDB refuses an empty string or a null as an index key: the row is built without
        `databaseId`, so the write succeeds and only DatabaseIndex leaves it out."""
        item = build_audit_item("", "*", "schema_deleted", "administrator", {},
                                timestamp="2027-01-01T00:00:00+00:00")
        assert "databaseId" not in item
        table = trail.doubles["complianceAuditStorage"]
        table.put_item(Item=item)
        try:
            with _Caller(trail, "admin") as caller:
                _, body, _ = caller.page(maxItems="5")
            assert body["entries"][0]["entryId"] == item["entryId"]
        finally:
            table.delete_item(Key={"entryId": item["entryId"]})
