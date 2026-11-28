# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A listing filtered by a database decides the caller's access to that database before it reads
any of it (D175).

Every row a database-filtered listing reads carries the named database id, and every row is
decided first on that id. So a caller who may not read the database may read none of its rows,
and the listing answers it exactly as for an id that names no database: one empty page, no
`NextToken`, the same status and body, and nothing of the database read. Its answer then depends
neither on whether the database exists nor on how many entries it holds, and no token is built
from a row the caller may not read. The same holds for an asset's history (`assetId`) in such a
database, for an asset the caller's own asset rules refuse, for a deleted database's history
(`<databaseId>#deleted`), and for the quarantine listing's database filter. The refusal is
recorded as every refused check is, by the engine's authorization audit record.

Run on moto tables built from the CDK definitions, with the production enforcer of each role (the
shipped templates and the real permission field matrix). Every DynamoDB call a request makes is
recorded at the client.
"""

import base64
import contextlib
import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import boto3
import botocore.client
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    body_of, claims_for, mirror_engine, real_constants, rest_event,
)
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc

SERVICES = (store, quarantine_svc, audit_svc)
# The retirement records are read from the moto tables (the deleted database's history has one), so
# every read of them is a request the recorder below sees.
USES_RETIREMENT_RECORD = True
USER_ID = "named-database-user"
BASE_TIME = datetime(2026, 10, 1, tzinfo=timezone.utc)

OWN = "own-db"
OWN_ASSET = "own-a1"
OWN_TAGGED = "own-tagged"
OWN_UNKNOWN_ASSET = "own-never"
DENIED_TAG = "restricted"
# Databases the scoped callers may not read, by the number of audit entries they hold.
SIZED = {"other-0": 0, "other-100": 100, "other-3000": 3_000, "other-6000": 6_000}
LARGEST = "other-6000"
HOT_ASSET = "hot-asset"
UNKNOWN = "never-db"
HISTORY = "gone-db#deleted"
HISTORY_ENTRIES = 2_600
UNKNOWN_HISTORY = "never-db#deleted"
TAGGED_ENTRIES = 2_600
OWN_ENTRIES = 40
QUARANTINED_DB = "other-q"
QUARANTINED = 2_600
OWN_QUARANTINED = 3
EVENTS = ("compliance_check", "exception_granted", "schema_binding_applied_to_asset")


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


# --------------------------------------------------------------------------------------------
# The data
# --------------------------------------------------------------------------------------------

def _entries():
    """Every seeded audit entry, oldest first, with distinct timestamps."""
    entries, clock = [], 0

    def add(database_id, asset_id, event_type, name):
        nonlocal clock
        clock += 1
        item = build_audit_item(database_id, asset_id, event_type, "administrator", {"n": clock},
                                timestamp=(BASE_TIME + timedelta(seconds=clock)).isoformat(),
                                asset_name=name)
        item["entryId"] = f"e{clock:06d}"
        entries.append(item)

    def block(database_id, count, asset_id):
        for n in range(count):
            if n % 6 == 0:
                add(database_id, "*", "schema_bound_to_database", None)
            else:
                add(database_id, asset_id, EVENTS[n % len(EVENTS)], f"Bracket {n}")

    for database_id, count in SIZED.items():
        block(database_id, count, HOT_ASSET)
    block(HISTORY, HISTORY_ENTRIES, "gone-a1")
    for n in range(OWN_ENTRIES):
        add(OWN, OWN_ASSET, EVENTS[n % len(EVENTS)], f"Bracket {n}")
    for n in range(TAGGED_ENTRIES):
        add(OWN, OWN_TAGGED, EVENTS[n % len(EVENTS)], f"Tagged {n}")
    return entries


ENTRIES = _entries()


def _seed(t):
    with t["complianceAuditStorage"].batch_writer() as batch:
        for entry in ENTRIES:
            batch.put_item(Item=entry)
    for database_id in (OWN, *SIZED, QUARANTINED_DB, HISTORY):
        t["databaseStorage"].put_item(Item={"databaseId": database_id})
    t["complianceEvaluationStorage"].put_item(Item={
        "evaluationId": "databaseRetirement#gone-db", "recordType": "databaseRetirement",
        "databaseId": "gone-db", "deletedId": HISTORY, "state": "done",
        "deletedAt": "2026-09-30T00:00:00+00:00", "generation": 1})
    own_assets = [(OWN_ASSET, ["ok"]), (OWN_TAGGED, [DENIED_TAG])] + [
        (f"{OWN}-q{n:05d}", ["ok"]) for n in range(OWN_QUARANTINED)]
    for asset_id, tags in own_assets:
        t["assetStorage"].put_item(Item={"databaseId": OWN, "assetId": asset_id,
                                         "assetName": asset_id, "tags": tags})
    with t["complianceAssetStateStorage"].batch_writer() as batch:
        for database_id, count in ((QUARANTINED_DB, QUARANTINED), (OWN, OWN_QUARANTINED)):
            for n in range(count):
                batch.put_item(Item={
                    "databaseId": database_id, "assetId": f"{database_id}-q{n:05d}",
                    "complianceState": "quarantined", "assetName": f"Part {n}",
                    "updatedAt": (BASE_TIME + timedelta(seconds=n)).isoformat()})


# --------------------------------------------------------------------------------------------
# Roles: the production enforcer of each
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
    "compliance admin of own-db": (
        ["cadmin"], lambda: _template("compliance-admin.json", "cadmin", OWN)),
    "compliance read-only of own-db": (
        ["cro"], lambda: _template("compliance-readonly.json", "cro", OWN)),
    "database user of own-db": (
        ["dbu"], lambda: _template("database-user.json", "dbu", OWN)),
    "tag-deny reader of own-db": (
        ["dba"], lambda: _template("database-admin.json", "dba", OWN) + _tag_deny("dba")),
}

# The roles that may read own-db and no other database.
SCOPED = ("compliance admin of own-db", "compliance read-only of own-db", "database user of own-db")


def _enforcer(roles, constraints):
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    with patch.object(authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=[{"userId": USER_ID, "roleName": r} for r in roles]), \
            patch.object(authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
                         return_value=constraints):
        return authz_module.CasbinEnforcer({"tokens": [USER_ID], "roles": list(roles),
                                            "mfaEnabled": True})


@pytest.fixture(scope="module")
def world():
    previous = (authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map)
    mock = mock_aws()
    mock.start()
    resource = boto3.resource("dynamodb", region_name="us-east-1")
    patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
    for active in patches:
        active.start()
    _seed(doubles)
    yield SimpleNamespace(doubles=doubles)
    for active in reversed(patches):
        active.stop()
    mock.stop()
    authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = previous


@contextlib.contextmanager
def _dynamodb_calls():
    """Every DynamoDB request sent inside the block, as `(operation, request body)`: the body as
    the client serialized it for the wire, key conditions and keys included, so a read through any
    table resource or client is seen with every value it names."""
    seen = []
    original = botocore.client.BaseClient._make_request

    def recording(client, operation_model, request_dict, request_context):
        if client.meta.service_model.service_name == "dynamodb":
            body = request_dict.get("body") or b""
            seen.append((operation_model.name,
                         body.decode("utf-8") if isinstance(body, bytes) else str(body)))
        return original(client, operation_model, request_dict, request_context)

    with patch.object(botocore.client.BaseClient, "_make_request", recording):
        yield seen


class _Caller:
    """One role calling a listing on the seeded data."""

    PATHS = {"audit": (audit_svc, "/compliance/audit", "entries"),
             "quarantine": (quarantine_svc, "/compliance/quarantine", "quarantinedAssets")}

    def __init__(self, role):
        roles, constraints = ROLES[role]
        real = _enforcer(roles, constraints())
        stand_in = MagicMock(name="CasbinEnforcer")
        stand_in.enforceAPI.return_value = True
        stand_in.enforce.side_effect = real.enforce
        mirror_engine(stand_in, real)
        self.denials = MagicMock(name="log_authorization")
        self.asset_denials = MagicMock(name="log_authorization_denials")
        self._patches = [
            patch.object(audit_svc, "CasbinEnforcer", return_value=stand_in),
            patch.object(audit_svc, "request_to_claims", claims_for(USER_ID)),
            patch.object(quarantine_svc, "CasbinEnforcer", return_value=stand_in),
            patch.object(quarantine_svc, "request_to_claims", claims_for(USER_ID)),
            patch.object(authz_module, "log_authorization", self.denials),
            patch("customLogging.auditLogging.log_authorization_denials", self.asset_denials),
        ]

    def __enter__(self):
        for active in self._patches:
            active.start()
        return self

    def __exit__(self, *exc):
        for active in reversed(self._patches):
            active.stop()

    def page(self, listing="audit", **query):
        """`(status, body, DynamoDB calls)` of one request."""
        service, path, _ = self.PATHS[listing]
        if "databaseId" in query:
            query["databaseId"] = query["databaseId"].replace("#", "%23")
        with _dynamodb_calls() as calls:
            response = service.lambda_handler(rest_event("GET", path, query_params=query),
                                              MagicMock())
        assert response["statusCode"] < 500, response
        return response["statusCode"], body_of(response), calls

    def walk(self, listing="audit", **query):
        """`(answers, calls)` of the listing from its first page to its end: each page's
        `(status, body)`, and every DynamoDB call the pages made."""
        answers, calls, token = [], [], None
        while True:
            request = dict(query)
            if token:
                request["startingToken"] = token
            status, body, made = self.page(listing, **request)
            answers.append((status, body))
            calls.extend(made)
            token = body.get("NextToken") if status == 200 else None
            if not token:
                return answers, calls
            assert len(answers) < 50, "the walk does not end"

    def refused_ids(self):
        """`(object type, database id, action)` of every refused engine check recorded so far."""
        refused = set()
        for call in self.denials.call_args_list:
            _claims, authorized, data = call.args
            if authorized is False:
                obj = data["obj"]
                refused.add((obj.get("object__type"), obj.get("databaseId"), data["action"]))
        return refused


def _audit_table_read(calls):
    """Whether any of `calls` reads the audit table."""
    name = json.dumps(audit_svc.audit_table_name)
    return any(name in body for _operation, body in calls)


def _names(calls, database_id):
    """Whether any of `calls` names `database_id`: as a key or key condition value, a start key, a
    composite `<databaseId>:<assetId>` or a `databaseRetirement#<databaseId>` record key."""
    named = re.compile(r'["#:]' + re.escape(database_id) + r'["#:]')
    return any(named.search(body) for _operation, body in calls)


EMPTY_AUDIT_PAGE = [(200, {"entries": []})]
EMPTY_QUARANTINE_PAGE = [(200, {"quarantinedAssets": []})]


# --------------------------------------------------------------------------------------------
# The audit listing
# --------------------------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("role", SCOPED)
class TestADatabaseTheCallerMayNotReadAnswersAsAnUnknownId:

    @pytest.mark.parametrize("database_id", [*SIZED, UNKNOWN])
    def test_one_empty_page_and_nothing_of_the_database_read(self, world, role, database_id):
        with _Caller(role) as caller:
            answers, calls = caller.walk(databaseId=database_id, maxItems="50")
        assert answers == EMPTY_AUDIT_PAGE
        assert not _audit_table_read(calls)
        assert not _names(calls, database_id)

    def test_every_size_and_an_unknown_id_give_the_same_answer(self, world, role):
        with _Caller(role) as caller:
            answers = {database_id: caller.walk(databaseId=database_id, maxItems="50")[0]
                       for database_id in (*SIZED, UNKNOWN)}
        assert len(answers) == len(SIZED) + 1
        assert all(answer == answers[UNKNOWN] for answer in answers.values())

    @pytest.mark.parametrize("filters", [
        {"eventType": "compliance_check"},
        {"text": "bracket"},
        {"startDate": (BASE_TIME + timedelta(seconds=200)).isoformat(),
         "endDate": (BASE_TIME + timedelta(seconds=9_000)).isoformat()},
        {"eventType": "exception_granted", "text": "bracket",
         "startDate": BASE_TIME.isoformat()},
    ], ids=["eventType", "text", "window", "all-three"])
    def test_with_the_other_filters(self, world, role, filters):
        with _Caller(role) as caller:
            largest, largest_calls = caller.walk(databaseId=LARGEST, maxItems="50", **filters)
            unknown, _ = caller.walk(databaseId=UNKNOWN, maxItems="50", **filters)
        assert largest == unknown == EMPTY_AUDIT_PAGE
        assert not _audit_table_read(largest_calls)

    def test_an_assets_history_in_that_database(self, world, role):
        with _Caller(role) as caller:
            known, known_calls = caller.walk(databaseId=LARGEST, assetId=HOT_ASSET,
                                             maxItems="50")
            unknown, _ = caller.walk(databaseId=UNKNOWN, assetId=HOT_ASSET, maxItems="50")
        assert known == unknown == EMPTY_AUDIT_PAGE
        assert not _audit_table_read(known_calls)
        assert not _names(known_calls, LARGEST)

    def test_a_deleted_databases_history(self, world, role):
        with _Caller(role) as caller:
            known, known_calls = caller.walk(databaseId=HISTORY, maxItems="50")
            unknown, _ = caller.walk(databaseId=UNKNOWN_HISTORY, maxItems="50")
        assert known == unknown == EMPTY_AUDIT_PAGE
        assert not _audit_table_read(known_calls)
        assert not _names(known_calls, HISTORY)

    def test_a_token_sent_with_the_filter_reads_nothing_either(self, world, role):
        """A token of the database's own index, as an allowed caller would hold it."""
        newest = next(entry for entry in reversed(ENTRIES) if entry["databaseId"] == LARGEST)
        token = base64.b64encode(json.dumps({
            "entryId": newest["entryId"], "databaseId": LARGEST,
            "timestamp": newest["timestamp"]}).encode("utf-8")).decode("utf-8")
        with _Caller(role) as caller:
            status, body, calls = caller.page(databaseId=LARGEST, startingToken=token)
        assert (status, body) == EMPTY_AUDIT_PAGE[0]
        assert not _audit_table_read(calls)

    def test_the_refusal_is_recorded_as_a_refused_check(self, world, role):
        with _Caller(role) as caller:
            caller.page(databaseId=LARGEST)
            caller.page(databaseId=UNKNOWN)
            refused = caller.refused_ids()
        assert ("complianceEvaluation", LARGEST, "GET") in refused
        assert ("complianceEvaluation", UNKNOWN, "GET") in refused


@pytest.mark.unit
class TestAnAssetTheCallersRulesRefuse:
    """A caller who may read the database but whose asset rules refuse the asset."""

    ROLE = "tag-deny reader of own-db"

    def test_answers_as_an_unknown_asset_without_reading_its_history(self, world):
        with _Caller(self.ROLE) as caller:
            tagged, tagged_calls = caller.walk(databaseId=OWN, assetId=OWN_TAGGED, maxItems="50")
            unknown, unknown_calls = caller.walk(databaseId=OWN, assetId=OWN_UNKNOWN_ASSET,
                                                 maxItems="50")
        assert tagged == unknown == EMPTY_AUDIT_PAGE
        assert not _audit_table_read(tagged_calls)
        # The asset's row decides it: no more reads than for an asset that does not exist.
        assert len(tagged_calls) <= len(unknown_calls)

    def test_the_refusal_is_recorded_as_a_refused_asset_check(self, world):
        with _Caller(self.ROLE) as caller:
            caller.page(databaseId=OWN, assetId=OWN_TAGGED)
            denied = {(record["obj"].get("assetId"), record["action"])
                      for call in caller.asset_denials.call_args_list
                      for record in call.args[1]}
        assert (OWN_TAGGED, "GET") in denied

    def test_an_asset_it_may_read_keeps_its_whole_history(self, world):
        expected = [entry["entryId"] for entry in reversed(ENTRIES)
                    if entry["databaseId"] == OWN and entry["assetId"] == OWN_ASSET]
        with _Caller(self.ROLE) as caller:
            answers, _ = caller.walk(databaseId=OWN, assetId=OWN_ASSET, maxItems="200")
        assert len(expected) == OWN_ENTRIES
        assert [entry["entryId"] for _status, body in answers
                for entry in body["entries"]] == expected


@pytest.mark.unit
class TestACallerWhoMayReadTheDatabaseIsUnchanged:

    def test_an_administrators_first_page_of_the_largest_database(self, world):
        expected = [entry["entryId"] for entry in reversed(ENTRIES)
                    if entry["databaseId"] == LARGEST]
        with _Caller("admin") as caller:
            status, body, calls = caller.page(databaseId=LARGEST, maxItems="50")
        assert status == 200
        assert [entry["entryId"] for entry in body["entries"]] == expected[:50]
        assert "NextToken" in body
        # The two read detectors the refusals rely on see this request's reads.
        assert _audit_table_read(calls) and _names(calls, LARGEST)

    def test_an_administrators_deleted_history(self, world):
        with _Caller("admin") as caller:
            answers, _ = caller.walk(databaseId=HISTORY, maxItems="200")
        ids = [entry["entryId"] for _status, body in answers for entry in body["entries"]]
        assert len(ids) == HISTORY_ENTRIES == len(set(ids))

    def test_an_unknown_id_is_still_not_found_for_an_administrator(self, world):
        with _Caller("admin") as caller:
            status, body, _ = caller.page(databaseId=UNKNOWN)
        assert (status, body) == (400, {"message": "Database not found"})

    @pytest.mark.parametrize("role", SCOPED)
    def test_a_scoped_callers_own_database(self, world, role):
        expected = [entry["entryId"] for entry in reversed(ENTRIES) if entry["databaseId"] == OWN]
        with _Caller(role) as caller:
            status, body, _ = caller.page(databaseId=OWN, maxItems="50")
        assert status == 200
        assert [entry["entryId"] for entry in body["entries"]] == expected[:50]
        assert "NextToken" in body


# --------------------------------------------------------------------------------------------
# The quarantine listing
# --------------------------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("role", SCOPED)
class TestTheQuarantineListingDecidesTheDatabaseFirst:

    @pytest.mark.parametrize("database_id", [QUARANTINED_DB, LARGEST, UNKNOWN])
    def test_one_empty_page_and_nothing_of_the_database_read(self, world, role, database_id):
        with _Caller(role) as caller:
            answers, calls = caller.walk("quarantine", databaseId=database_id, maxItems="50")
        assert answers == EMPTY_QUARANTINE_PAGE
        assert not _names(calls, database_id)

    def test_the_refusal_is_recorded_as_a_refused_check(self, world, role):
        with _Caller(role) as caller:
            caller.page("quarantine", databaseId=QUARANTINED_DB)
            refused = caller.refused_ids()
        assert ("complianceEvaluation", QUARANTINED_DB, "GET") in refused

    def test_its_own_database_is_unchanged(self, world, role):
        with _Caller(role) as caller:
            status, body, _ = caller.page("quarantine", databaseId=OWN, maxItems="50")
        assert status == 200
        assert sorted(row["assetId"] for row in body["quarantinedAssets"]) == [
            f"{OWN}-q{n:05d}" for n in range(OWN_QUARANTINED)]


@pytest.mark.unit
def test_an_administrators_quarantine_listing_of_a_large_database_is_unchanged(world):
    with _Caller("admin") as caller:
        status, body, calls = caller.page("quarantine", databaseId=QUARANTINED_DB, maxItems="50")
    assert status == 200
    assert len(body["quarantinedAssets"]) == 50
    assert {row["databaseId"] for row in body["quarantinedAssets"]} == {QUARANTINED_DB}
    assert "NextToken" in body
    # The read detector the quarantine refusals rely on sees this request's reads.
    assert _names(calls, QUARANTINED_DB)
