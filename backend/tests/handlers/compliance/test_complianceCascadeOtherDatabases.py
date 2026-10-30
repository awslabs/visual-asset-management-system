# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A cascade's descendants in another database are shown only to a reader who may read that
database's compliance records; the others are counted, not named.

A cascade read or listing is authorized on its parent's database (the cascade object and the
evaluation GET on it), but its descendants can sit in other databases. The cascade used to return
every descendant to every such reader: its `databaseId:assetId` key in `nodes`, `executionOrder`
and `results`, and for an evaluated one its asset name (`nodeNames`) and verdict. The finished
cascade's audit entry counted their verdicts in its shared `summary`, and the completion email
listed the same summary. A Compliance Admin of one database learned asset ids, names and compliance
outcomes of another database, which a direct compliance read of those assets refuses.

Now a descendant is returned only to a reader who passes the decision a direct compliance read of
that asset makes (the evaluation GET on its database, and the archived-asset decision). Those in a
database the reader may not read are counted in `unauthorizedNodes`, as the asset links count the
linked assets a reader may not see. The audit entry counts their results only for a reader of that
database (the others in `unauthorizedNodes`), and the email counts them without results.

The tests run the executor, the trigger, the cascade service, the audit service and the
notification text on moto tables built from the CDK definitions, with the production enforcer of
shipped permission templates scoped to one database, to both, and with a deny on one archive.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    body_of, claims_for, mirror_engine, real_constants, rest_event,
)
from backend.tests.handlers.compliance.test_complianceArchivedAssetAccess import (
    USER_ID, _enforcer, asset_read_deny,
)
from common.compliance import cascadeAuditSummary as summaries
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeExecutor as executor
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceNotifications as notifications
from handlers.compliance import complianceTrigger as trigger

DB1 = "xdb-one"
DB2 = "xdb-two"
ARCHIVED_DB1 = f"{DB1}#deleted"
SCHEMA = "xdb-schema"
PARENT = "xp-parent"
CHILD1 = "xp-child-one"
CHILD2 = "xt-child-two"
ARCHIVED = "xp-archived"
LOCAL_PARENT = "xp-local-parent"
LOCAL_CHILD = "xp-local-child"
CHILD2_NAME = "Second Database Child"
NAMES = {PARENT: "Parent Assembly", CHILD1: "First Database Child", CHILD2: CHILD2_NAME,
         ARCHIVED: "Archived Child", LOCAL_PARENT: "Local Parent", LOCAL_CHILD: "Local Child"}
DATABASE_OF = {PARENT: DB1, CHILD1: DB1, CHILD2: DB2, ARCHIVED: DB1, LOCAL_PARENT: DB1,
               LOCAL_CHILD: DB1}
VERDICTS = {CHILD1: "compliant", CHILD2: "non_compliant", LOCAL_CHILD: "compliant"}
KEY1, KEY2, ARCHIVED_KEY, LOCAL_KEY = (f"{DB1}:{CHILD1}", f"{DB2}:{CHILD2}", f"{DB1}:{ARCHIVED}",
                                       f"{DB1}:{LOCAL_CHILD}")
TOPIC = "arn:aws:sns:us-east-1:1:asset-topic"
# The `details` key a finished cascade's entry records its other-database descendants under
# (`cascadeAuditSummary.OTHER_DATABASES_KEY`, pinned below); never returned to a reader.
OTHER_DATABASES_KEY = "summaryByDatabase"

# Run by the executor: started by a caller who may evaluate both databases; by one who may
# evaluate DB1 only (the DB2 child is skipped_not_authorized); and a cascade within DB1.
EVALUATED = "7a0c0000-0000-4000-8000-0000000d9601"
NOT_AUTHORIZED = "7a0c0000-0000-4000-8000-0000000d9602"
LOCAL = "7a0c0000-0000-4000-8000-0000000d9605"
# Seeded mid-run and after an abort.
EXECUTING = "7a0c0000-0000-4000-8000-0000000d9603"
ABORTED = "7a0c0000-0000-4000-8000-0000000d9604"
# The cascade the parent's next evaluation opens (awaiting approval); its id is generated.
PENDING = "pending"

SERVICES = (store, evaluate_svc, trigger, executor, cascade_svc, audit_svc)
LISTING_STATES = ("pending_approval", "executing", "completed", "aborted")
AUDIT_READS = {
    "asset audit": (f"/compliance/audit/{DB1}/{PARENT}", {"databaseId": DB1, "assetId": PARENT},
                    None),
    "listing": ("/compliance/audit", None, None),
    "listing by event type": ("/compliance/audit", None, {"eventType": "cascade_completed"}),
    "listing by database": ("/compliance/audit", None, {"databaseId": DB1}),
}
VARIABLES = {"TAG_VALUE": "locked"}


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    """The real permission field matrix for the enforcer and the template import (see
    test_complianceTemplateRolesEndToEnd)."""
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


@pytest.fixture
def role_enforcer():
    previous = (authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map)
    with patch.object(authz_module, "log_authorization", MagicMock()):
        yield _enforcer
    authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = previous


# ---------------------------------------------------------------------------------------------
# Readers: shipped templates, each on its own role scoped to one database
# ---------------------------------------------------------------------------------------------
def _template_roles(*grants):
    roles, constraints = [], []
    for template, database_id in grants:
        role = f"tpl-{template.split('.')[0]}-{database_id}"
        roles.append(role)
        constraints += shipped_constraints.template_constraints(
            template, role, dict(VARIABLES, DATABASE_ID=database_id))
    return roles, constraints


def _archive_deny(role):
    """A deny on DB1's archive: every action on the asset and on its compliance evaluation where
    databaseId equals `<DB1>#deleted`."""
    return [{
        "constraintId": f"deny-archive-{object_type}", "name": f"deny-archive-{object_type}",
        "objectType": object_type,
        "criteriaAnd": [{"field": "databaseId", "id": "d", "operator": "equals",
                         "value": ARCHIVED_DB1}],
        "criteriaOr": [],
        "groupPermissions": [{"groupId": role, "id": f"d-{object_type}-{action}",
                              "permission": action, "permissionType": "deny"}
                             for action in ("GET", "PUT", "POST", "DELETE")],
        "userPermissions": [],
    } for object_type in ("asset", "complianceEvaluation")]


DB1_ADMIN = "Compliance Admin of DB1"
DB1_READONLY = "Compliance Read-Only of DB1"
BOTH_COMPLIANCE = "Compliance Admin of DB1 and DB2"
BOTH_DATABASE = "Database Admin of DB1 and DB2"
ARCHIVE_DENY = "Database Admin of DB1 and DB2, deny on DB1's archive"
TAG_DENY = "Database Admin of DB1, deny on one tag"


def _reader(name):
    """(roles, constraints) of a reader."""
    if name == DB1_ADMIN:
        return _template_roles(("compliance-admin.json", DB1))
    if name == DB1_READONLY:
        return _template_roles(("compliance-readonly.json", DB1))
    if name == BOTH_COMPLIANCE:
        return _template_roles(("compliance-admin.json", DB1), ("compliance-admin.json", DB2))
    if name == BOTH_DATABASE:
        return _template_roles(("database-admin.json", DB1), ("database-admin.json", DB2))
    if name == ARCHIVE_DENY:
        roles, constraints = _template_roles(("database-admin.json", DB1),
                                             ("database-admin.json", DB2))
        return roles, constraints + _archive_deny(roles[0])
    if name == TAG_DENY:
        roles, constraints = _template_roles(("database-admin.json", DB1))
        return roles, constraints + asset_read_deny(roles[0], "deny-tag", {
            "field": "tags", "operator": "is_one_of", "value": "restricted"}, ("asset",))
    raise KeyError(name)


READERS = (DB1_ADMIN, DB1_READONLY, BOTH_COMPLIANCE, BOTH_DATABASE, ARCHIVE_DENY)
# What each reader may read: DB2's compliance, and DB1's archive.
READS_DB2 = {DB1_ADMIN: False, DB1_READONLY: False, BOTH_COMPLIANCE: True, BOTH_DATABASE: True,
             ARCHIVE_DENY: True}
SEES_ARCHIVE = {DB1_ADMIN: False, DB1_READONLY: False, BOTH_COMPLIANCE: False, BOTH_DATABASE: True,
                ARCHIVE_DENY: False}


# ---------------------------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------------------------
class _CountingTable:
    """A table whose reads are counted (`calls`), every other attribute the table's."""

    READS = ("get_item", "query", "scan", "batch_get_item")

    def __init__(self, table):
        self._table = table
        self.calls = []

    def __getattr__(self, name):
        attribute = getattr(self._table, name)
        if name not in self.READS:
            return attribute

        def counted(*args, **kwargs):
            self.calls.append(name)
            return attribute(*args, **kwargs)
        return counted


def _seed_assets(doubles):
    for database_id in (DB1, DB2):
        doubles["databaseStorage"].put_item(Item={"databaseId": database_id,
                                                  "complianceSchemaName": SCHEMA,
                                                  "complianceAutoEval": False})
    for asset_id, database_id in DATABASE_OF.items():
        archived = asset_id == ARCHIVED
        doubles["assetStorage"].put_item(Item={
            "databaseId": f"{database_id}#deleted" if archived else database_id,
            "assetId": asset_id, "assetName": NAMES[asset_id], "tags": [],
            **({"status": "archived"} if archived else {})})
        doubles["complianceAssetStateStorage"].put_item(Item={
            "databaseId": database_id, "assetId": asset_id, "schemaName": SCHEMA,
            "schemaSource": "database", "complianceState": "compliant",
            "assetName": NAMES[asset_id],
            "assetNameSort": f"{NAMES[asset_id].lower()}#{asset_id}",
            "assetSearchText": f"{NAMES[asset_id].lower()} {asset_id}",
            "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"})
    links = [(PARENT, CHILD1), (PARENT, CHILD2), (PARENT, ARCHIVED), (LOCAL_PARENT, LOCAL_CHILD)]
    for index, (parent, child) in enumerate(links):
        doubles["assetLinksStorageV2"].put_item(Item={
            "assetLinkId": f"xdb-link-{index}", "relationshipType": "parentChild",
            "fromAssetDatabaseId": DATABASE_OF[parent], "fromAssetId": parent,
            "toAssetDatabaseId": DATABASE_OF[child], "toAssetId": child,
            "fromAssetDatabaseId:fromAssetId": f"{DATABASE_OF[parent]}:{parent}",
            "toAssetDatabaseId:toAssetId": f"{DATABASE_OF[child]}:{child}"})


def _cascade_row(cascade_id, parent, state, created_at, **attributes):
    return {"cascadeId": cascade_id, "state": state, "createdAt": created_at,
            "triggeredByDatabaseId": DATABASE_OF[parent], "triggeredByAssetId": parent,
            "triggeredByAssetName": NAMES[parent], **attributes}


def _seed_cascades(doubles):
    table = doubles["complianceCascadeStorage"]
    for cascade_id, parent, reach, created_at in (
            (EVALUATED, PARENT, [DB1, DB2], "2026-10-30T01:00:00+00:00"),
            (NOT_AUTHORIZED, PARENT, [DB1], "2026-10-30T02:00:00+00:00"),
            (LOCAL, LOCAL_PARENT, [DB1], "2026-10-30T03:00:00+00:00")):
        table.put_item(Item=_cascade_row(cascade_id, parent, "executing", created_at,
                                         authorizedDatabaseIds=json.dumps(reach)))
        executor.execute_cascade(cascade_id)
    two_nodes = {"executionOrder": json.dumps([KEY1, KEY2]), "totalNodes": 2,
                 "nodeNames": json.dumps({KEY1: NAMES[CHILD1], KEY2: CHILD2_NAME}),
                 "authorizedDatabaseIds": json.dumps([DB1, DB2])}
    table.put_item(Item=_cascade_row(
        EXECUTING, PARENT, "executing", "2026-10-30T04:00:00+00:00",
        nodes=json.dumps({KEY1: "compliant", KEY2: "evaluating"}), **two_nodes))
    table.put_item(Item=_cascade_row(
        ABORTED, PARENT, "aborted", "2026-10-30T05:00:00+00:00",
        nodes=json.dumps({KEY1: "compliant", KEY2: "non_compliant"}),
        abortReason="Cascade execution failed", completedAt="2026-10-30T05:01:00+00:00",
        **two_nodes))
    # The parent's next evaluation opens a cascade awaiting approval.
    trigger.check_and_trigger_cascade(DB1, PARENT)


def _run_world(enforcer, roles, seed, body):
    """Run `body(call, world)` in a fresh world seeded by `seed(doubles)`, for one enforcer.
    `call(service, method, path, params, query)` invokes a handler; `world` holds the published
    notifications (`published`), the stored audit details (`stored`), the counted asset-table reads
    (`asset reads`) and the engine calls (`engine`, `asset checks`)."""
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = enforcer.enforce
    mirror_engine(stand_in, enforcer)
    stand_in.service_object = MagicMock(wraps=enforcer.service_object)
    publish = MagicMock()
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
        for module in (evaluate_svc, cascade_svc, audit_svc):
            patches += [patch.object(module, "CasbinEnforcer", return_value=stand_in),
                        patch.object(module, "request_to_claims", claims_for(USER_ID))]
        patches += [
            patch.object(store, "run_evaluation", side_effect=lambda database_id, asset_id, *_: {
                "evaluationId": f"e-{asset_id}", "verdict": VERDICTS[asset_id],
                "complianceState": VERDICTS[asset_id]}),
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()),
            patch.object(notifications, "_get_asset_topic",
                         side_effect=lambda database_id, asset_id: (NAMES[asset_id], TOPIC)),
            patch.object(notifications, "_publish", publish),
        ]
        for active in patches:
            active.start()
        counting = None
        try:
            seed(doubles)
            counting = _CountingTable(store.asset_table)
            counted = patch.object(store, "asset_table", counting)
            counted.start()
            patches.append(counted)
            stand_in.enforce.reset_mock()
            stand_in.service_object.enforce.reset_mock()

            def call(service, method, path, params=None, query=None):
                response = service.lambda_handler(
                    rest_event(method, path, params, query), MagicMock())
                assert response["statusCode"] < 500, (method, path, response)
                return response

            world = {
                "published": [c.args[1] if len(c.args) > 1 else c.kwargs["message"]
                              for c in publish.call_args_list],
                "stored": {item["cascadeId"]: json.loads(item["details"])
                           for item in doubles["complianceAuditStorage"].scan()["Items"]
                           if item.get("eventType") == "cascade_completed"},
                "asset reads": counting.calls,
                "engine": stand_in.enforce,
                "asset checks": stand_in.service_object.enforce,
            }
            return body(call, world)
        finally:
            for active in reversed(patches):
                active.stop()


def _view(record):
    """What a cascade GET body or listing row names, attribute by attribute."""
    if record is None:
        return None

    def decoded(name, default):
        raw = record.get(name)
        return json.loads(raw) if isinstance(raw, str) and raw else (raw or default)
    nodes = decoded("nodes", {})
    return {
        "nodes": nodes,
        "executionOrder": decoded("executionOrder", []),
        "nodeNames": decoded("nodeNames", {}),
        "results": {entry["node"]: entry["status"] for entry in decoded("results", [])},
        "totalNodes": record.get("totalNodes"),
        "unauthorizedNodes": record.get("unauthorizedNodes"),
        "text": json.dumps(record, default=str),
    }


def _observe(call, world):
    pending = [row["cascadeId"] for row in body_of(call(
        cascade_svc, "GET", "/compliance/cascades", query={"state": "pending_approval"})
    ).get("cascades", [])]
    out = {"get": {}, "listing": {}, "audit": {}, "opened": {}, "published": world["published"],
           "stored": world["stored"]}
    pending_id = pending[0] if pending else None
    for cascade_id in (EVALUATED, NOT_AUTHORIZED, LOCAL, EXECUTING, ABORTED, pending_id):
        if cascade_id is None:
            continue
        response = call(cascade_svc, "GET", f"/compliance/cascades/{cascade_id}",
                        {"cascadeId": cascade_id})
        name = PENDING if cascade_id == pending_id else cascade_id
        out["get"][name] = _view(body_of(response)) if response["statusCode"] == 200 else None
    for state in LISTING_STATES:
        response = call(cascade_svc, "GET", "/compliance/cascades", query={"state": state})
        rows = body_of(response).get("cascades", []) if response["statusCode"] == 200 else []
        out["listing"][state] = {(PENDING if row["cascadeId"] == pending_id else row["cascadeId"]):
                                 _view(row) for row in rows}
    for name, (path, params, query) in AUDIT_READS.items():
        response = call(audit_svc, "GET", path, params, query)
        entries = body_of(response).get("entries", []) if response["statusCode"] == 200 else []
        out["audit"][name] = {
            "details": {entry["cascadeId"]: json.loads(entry["details"]) for entry in entries
                        if entry.get("eventType") == "cascade_completed"},
            "text": json.dumps([entry for entry in entries
                                if str(entry.get("eventType", "")).startswith("cascade_")],
                               default=str),
        }
        out["opened"][name] = [json.loads(entry["details"]) for entry in entries
                               if entry.get("eventType") == "cascade_auto_triggered"]
    return out


@pytest.fixture(scope="module")
def observations():
    return {}


def _observed(name, role_enforcer, cache):
    if name not in cache:
        roles, constraints = _reader(name)
        cache[name] = _run_world(role_enforcer(roles, constraints), roles,
                                 lambda doubles: (_seed_assets(doubles), _seed_cascades(doubles)),
                                 _observe)
    return cache[name]


def _visible_keys(reader, cascade_id):
    """The descendant keys the reader is shown of a cascade."""
    stored = {
        EVALUATED: {KEY1, KEY2, ARCHIVED_KEY},
        NOT_AUTHORIZED: {KEY1, KEY2, ARCHIVED_KEY},
        EXECUTING: {KEY1, KEY2},
        ABORTED: {KEY1, KEY2},
        LOCAL: {LOCAL_KEY},
        PENDING: set(),
    }[cascade_id]
    if not READS_DB2[reader]:
        stored = stored - {KEY2}
    if not SEES_ARCHIVE[reader]:
        stored = stored - {ARCHIVED_KEY}
    return stored


def _unauthorized(reader, cascade_id):
    """How many descendants the reader is told it may not access: those in DB2, for a reader of DB1
    only (archived ones it may not see are not counted)."""
    if READS_DB2[reader] or cascade_id in (LOCAL, PENDING):
        return 0
    return 1


def _assert_cascade_view(reader, cascade_id, view, where):
    assert view is not None, (reader, where, cascade_id)
    expected = _visible_keys(reader, cascade_id)
    assert set(view["nodes"]) == expected, (reader, where, cascade_id, view["nodes"])
    assert set(view["executionOrder"]) <= expected, (reader, where, cascade_id)
    assert set(view["nodeNames"]) <= expected, (reader, where, cascade_id)
    assert set(view["results"]) <= expected, (reader, where, cascade_id)
    if cascade_id != PENDING:
        assert set(view["executionOrder"]) == expected, (reader, where, cascade_id)
        assert int(view["totalNodes"]) == len(expected), (reader, where, cascade_id, view)
    assert view["unauthorizedNodes"] == _unauthorized(reader, cascade_id), (
        reader, where, cascade_id, view["unauthorizedNodes"])
    if not READS_DB2[reader]:
        # No DB2 key, name or verdict anywhere in the record.
        for secret in (DB2, CHILD2, CHILD2_NAME, "non_compliant"):
            assert secret not in view["text"], (reader, where, cascade_id, secret)


# ---------------------------------------------------------------------------------------------
# GET and every listing state
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("cascade_id", [EVALUATED, NOT_AUTHORIZED, EXECUTING, ABORTED, LOCAL,
                                        PENDING])
def test_the_cascade_get_shows_the_descendants_the_reader_may_read(
        reader, cascade_id, role_enforcer, observations):
    found = _observed(reader, role_enforcer, observations)
    _assert_cascade_view(reader, cascade_id, found["get"].get(cascade_id), "get")


@pytest.mark.unit
@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("state,cascade_ids", [
    ("pending_approval", (PENDING,)),
    ("executing", (EXECUTING,)),
    ("completed", (EVALUATED, NOT_AUTHORIZED, LOCAL)),
    ("aborted", (ABORTED,)),
])
def test_each_listing_state_shows_the_descendants_the_reader_may_read(
        reader, state, cascade_ids, role_enforcer, observations):
    found = _observed(reader, role_enforcer, observations)
    rows = found["listing"][state]
    assert set(rows) == set(cascade_ids), (reader, state, set(rows))
    for cascade_id in cascade_ids:
        _assert_cascade_view(reader, cascade_id, rows[cascade_id], f"listing {state}")


@pytest.mark.unit
@pytest.mark.parametrize("reader", [DB1_ADMIN, DB1_READONLY])
def test_a_reader_of_one_database_learns_nothing_of_the_other(reader, role_enforcer, observations):
    """The finding itself: a reader with compliance access to DB1 only gets no DB2 key, name or
    verdict from any cascade read, and the count of what it cannot access."""
    found = _observed(reader, role_enforcer, observations)
    views = list(found["get"].values()) + [view for rows in found["listing"].values()
                                           for view in rows.values()]
    assert len(views) == 12
    for view in views:
        for secret in (DB2, CHILD2, CHILD2_NAME, "non_compliant"):
            assert secret not in view["text"], (reader, secret)
    assert found["get"][EVALUATED]["unauthorizedNodes"] == 1
    assert found["get"][EVALUATED]["nodes"] == {KEY1: "compliant"}
    assert found["get"][EVALUATED]["nodeNames"] == {KEY1: NAMES[CHILD1]}
    assert found["get"][EVALUATED]["results"] == {KEY1: "compliant"}


@pytest.mark.unit
def test_a_reader_of_both_databases_sees_every_descendant(role_enforcer, observations):
    """The control: a reader who may read both databases (and DB1's archive) sees every node, the
    DB2 verdict and name included, and is told of none it cannot access. A node the approver could
    not evaluate is still listed to it, by its id and without a name."""
    found = _observed(BOTH_DATABASE, role_enforcer, observations)
    evaluated = found["get"][EVALUATED]
    assert evaluated["nodes"] == {KEY1: "compliant", KEY2: "non_compliant",
                                  ARCHIVED_KEY: "skipped_archived"}
    assert evaluated["nodeNames"][KEY2] == CHILD2_NAME
    assert evaluated["unauthorizedNodes"] == 0
    assert int(evaluated["totalNodes"]) == 3
    not_authorized = found["get"][NOT_AUTHORIZED]
    assert not_authorized["nodes"][KEY2] == "skipped_not_authorized"
    assert KEY2 not in not_authorized["nodeNames"]
    assert not_authorized["unauthorizedNodes"] == 0


@pytest.mark.unit
def test_the_archive_deny_reader_is_unchanged(role_enforcer, observations):
    """A reader of both databases with a deny on DB1's archive still sees the DB2 descendant, and
    not the archived one, which is not counted."""
    found = _observed(ARCHIVE_DENY, role_enforcer, observations)
    for cascade_id in (EVALUATED, NOT_AUTHORIZED):
        view = found["get"][cascade_id]
        assert set(view["nodes"]) == {KEY1, KEY2}, (cascade_id, view["nodes"])
        assert ARCHIVED not in view["text"]
        assert view["unauthorizedNodes"] == 0
        assert int(view["totalNodes"]) == 2


@pytest.mark.unit
@pytest.mark.parametrize("reader", READERS)
def test_a_cascade_within_the_parents_database_is_unaffected(reader, role_enforcer, observations):
    """A cascade whose descendants are all in the parent's database is returned whole, with a zero
    count, and one with no descendant yet (awaiting approval) has neither nodes nor a count."""
    found = _observed(reader, role_enforcer, observations)
    local = found["get"][LOCAL]
    assert local["nodes"] == {LOCAL_KEY: "compliant"}
    assert local["nodeNames"] == {LOCAL_KEY: NAMES[LOCAL_CHILD]}
    assert local["results"] == {LOCAL_KEY: "compliant"}
    assert local["unauthorizedNodes"] == 0
    pending = found["get"][PENDING]
    assert pending["nodes"] == {} and pending["unauthorizedNodes"] == 0


# ---------------------------------------------------------------------------------------------
# Audit entries
# ---------------------------------------------------------------------------------------------
def _expected_audit(reader, cascade_id):
    """The `cascade_completed` details the reader is shown. `nodesEvaluated` counts the descendants
    evaluated to a verdict: not one skipped as not authorized, nor one skipped as archived."""
    other_status = {EVALUATED: "non_compliant", NOT_AUTHORIZED: "skipped_not_authorized"}
    if cascade_id == LOCAL:
        return {"nodesEvaluated": 1, "summary": {"compliant": 1}}
    summary = {"compliant": 1}
    details = {"nodesEvaluated": 1, "unauthorizedNodes": 1}
    if READS_DB2[reader]:
        summary[other_status[cascade_id]] = 1
        details = {"nodesEvaluated": 2 if cascade_id == EVALUATED else 1, "unauthorizedNodes": 0}
    if SEES_ARCHIVE[reader]:
        summary["skipped_archived"] = 1
        details[summaries.ARCHIVED_BY_DATABASE_KEY] = {DB1: 1}
    details["summary"] = summary
    return details


@pytest.mark.unit
@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("read", list(AUDIT_READS))
def test_the_audit_entry_counts_other_databases_only_for_their_readers(
        reader, read, role_enforcer, observations):
    found = _observed(reader, role_enforcer, observations)
    view = found["audit"][read]
    # The asset trail is the parent's (the cascade within DB1 has another parent).
    expected_ids = {EVALUATED, NOT_AUTHORIZED} | (set() if read == "asset audit" else {LOCAL})
    assert set(view["details"]) == expected_ids, (reader, read, set(view["details"]))
    for cascade_id, details in view["details"].items():
        assert details == _expected_audit(reader, cascade_id), (reader, read, cascade_id, details)
    assert OTHER_DATABASES_KEY not in view["text"], (reader, read)
    if not READS_DB2[reader]:
        for secret in (DB2, CHILD2, CHILD2_NAME, "non_compliant"):
            assert secret not in view["text"], (reader, read, secret)
    if read == "listing by event type":
        return
    # The opened cascade's entry counts its children (in both databases) and names none.
    assert found["opened"][read] == [
        {"reason": "parent_update", "childCount": 3 if SEES_ARCHIVE[reader] else 2,
         **({summaries.ARCHIVED_CHILDREN_KEY: {DB1: 1}} if SEES_ARCHIVE[reader] else {})}], (
        reader, read, found["opened"][read])


@pytest.mark.unit
def test_the_stored_entry_keeps_other_databases_out_of_the_shared_summary(
        role_enforcer, observations):
    found = _observed(DB1_ADMIN, role_enforcer, observations)
    assert found["stored"][EVALUATED] == {
        "nodesEvaluated": 1, "summary": {"compliant": 1},
        OTHER_DATABASES_KEY: {DB2: {"non_compliant": 1}},
        summaries.ARCHIVED_BY_DATABASE_KEY: {DB1: [ARCHIVED]}}
    assert found["stored"][LOCAL] == {"nodesEvaluated": 1, "summary": {"compliant": 1}}


# ---------------------------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
def test_the_emails_carry_no_other_database_id_name_or_result(role_enforcer, observations):
    """Every subscriber gets the same message, whatever databases each may read: the completion
    email counts the descendants in other databases without their results, and neither email names
    one. (The messages do not depend on the reader; one world is enough.)"""
    found = _observed(DB1_ADMIN, role_enforcer, observations)
    by_cascade = {cascade_id: [message for message in found["published"]
                               if f"Cascade ID: {cascade_id}" in message]
                  for cascade_id in (EVALUATED, NOT_AUTHORIZED, LOCAL)}
    assert all(len(messages) == 1 for messages in by_cascade.values()), by_cascade
    for message in found["published"]:
        for secret in (DB2, CHILD2, CHILD2_NAME, "non_compliant", "skipped_not_authorized"):
            assert secret not in message, (secret, message)
    for cascade_id in (EVALUATED, NOT_AUTHORIZED):
        message = by_cascade[cascade_id][0]
        assert "      compliant: 1" in message
        assert "Downstream assets in other databases: 1" in message
    assert "other databases" not in by_cascade[LOCAL][0]
    pending = [message for message in found["published"] if "approval" in message]
    assert len(pending) == 1 and "Downstream assets affected: 2" in pending[0]


# ---------------------------------------------------------------------------------------------
# Cost: one decision per database, whatever the number of descendants
# ---------------------------------------------------------------------------------------------
LARGE = "7a0c0000-0000-4000-8000-0000000d9610"
SMALL = "7a0c0000-0000-4000-8000-0000000d9611"
LARGE_NODES = 200


def _node_keys(count):
    """`count` descendant keys, half in DB1 and half in DB2."""
    return [f"{DB1 if index % 2 == 0 else DB2}:xn-{index:03d}" for index in range(count)]


def _seed_sized(doubles):
    _seed_assets(doubles)
    for cascade_id, keys, created_at in ((LARGE, _node_keys(LARGE_NODES), "2026-10-30T06:00:00+00:00"),
                                         (SMALL, _node_keys(2), "2026-10-30T07:00:00+00:00")):
        doubles["complianceCascadeStorage"].put_item(Item=_cascade_row(
            cascade_id, PARENT, "completed", created_at,
            nodes=json.dumps({key: "compliant" for key in keys}),
            executionOrder=json.dumps(keys), totalNodes=len(keys),
            nodeNames=json.dumps({key: f"Node {key}" for key in keys}),
            results=json.dumps([{"node": key, "status": "compliant",
                                 "evaluationId": f"e-{index}"}
                                for index, key in enumerate(keys)])))


def _measure(call, world):
    def counts(method, path, params=None, query=None):
        world["engine"].reset_mock()
        world["asset checks"].reset_mock()
        del world["asset reads"][:]
        response = call(cascade_svc, method, path, params, query)
        assert response["statusCode"] == 200, response
        return {"engine": world["engine"].call_count,
                "asset checks": world["asset checks"].call_count,
                "asset reads": len(world["asset reads"]), "body": body_of(response)}

    out = {name: counts("GET", f"/compliance/cascades/{cascade_id}", {"cascadeId": cascade_id})
           for name, cascade_id in (("large", LARGE), ("small", SMALL))}
    out["listing"] = counts("GET", "/compliance/cascades", query={"state": "completed"})
    return out


@pytest.fixture(scope="module")
def measurements():
    return {}


def _measured(name, role_enforcer, cache):
    if name not in cache:
        roles, constraints = _reader(name)
        cache[name] = _run_world(role_enforcer(roles, constraints), roles, _seed_sized, _measure)
    return cache[name]


@pytest.mark.unit
@pytest.mark.parametrize("reader", [DB1_ADMIN, BOTH_DATABASE, TAG_DENY])
def test_a_200_node_cascade_costs_what_a_2_node_one_does(
        reader, role_enforcer, measurements, capsys):
    """The descendants are decided once per database of the request, and their archived status
    read once per database (one Query, only when the reader's answer depends on it), so a
    200-node cascade costs the engine and the asset table what a 2-node one does, for a reader
    whose rules read no asset field and for one whose rules read the tags. The measured calls are
    printed (run with `-s` to read them)."""
    found = _measured(reader, role_enforcer, measurements)
    with capsys.disabled():
        print(f"\nD96 call counts [{reader}]: " + "; ".join(
            f"{name}: engine {found[name]['engine']}, asset checks "
            f"{found[name]['asset checks']}, asset reads {found[name]['asset reads']}"
            for name in ("large", "small", "listing")))
    large, small = found["large"], found["small"]
    for measure in ("engine", "asset checks", "asset reads"):
        assert large[measure] == small[measure], (reader, measure, large[measure], small[measure])
    # At most the parent's archived key and one archived partition per descendant database.
    assert large["asset reads"] <= 3, (reader, large["asset reads"])
    # The listing returns both rows with the same decisions: no more than one GET's worth each.
    listing = found["listing"]
    assert listing["engine"] <= 2 * large["engine"], (reader, listing["engine"])
    assert listing["asset reads"] <= large["asset reads"] + 1, (reader, listing["asset reads"])
    expected_unauthorized = 0 if READS_DB2.get(reader, False) else LARGE_NODES // 2
    assert large["body"]["unauthorizedNodes"] == expected_unauthorized
    assert int(large["body"]["totalNodes"]) == LARGE_NODES - expected_unauthorized


@pytest.mark.unit
def test_the_keys_are_the_services():
    """The per-database key the tests read is the one the summary writes, and the count the
    cascade service and the audit service return share one name."""
    assert summaries.OTHER_DATABASES_KEY == OTHER_DATABASES_KEY
    assert summaries.UNAUTHORIZED_NODES_KEY == cascade_svc.UNAUTHORIZED_NODES_ATTRIBUTE == (
        "unauthorizedNodes")


def _completed_entry(details):
    return {"eventType": "cascade_completed", "databaseId": "db-a", "assetId": "parent",
            "details": json.dumps(details)}


@pytest.mark.unit
def test_without_a_readability_decision_no_other_database_is_readable():
    """Fail closed: a caller that passes no `database_readable` counts every other database as one
    the reader may not read. An unreadable per-database record counts nothing."""
    stored = {"nodesEvaluated": 1, "summary": {"compliant": 1},
              OTHER_DATABASES_KEY: {"db-b": {"non_compliant": 2}, "db-c": "unreadable"}}
    seen = json.loads(summaries.details_for_reader(
        _completed_entry(stored), lambda database_id, asset_id: True)["details"])
    assert seen == {"nodesEvaluated": 1, "summary": {"compliant": 1}, "unauthorizedNodes": 2}


@pytest.mark.unit
def test_each_other_database_is_decided_for_the_reader():
    stored = {"nodesEvaluated": 1, "summary": {"compliant": 1},
              OTHER_DATABASES_KEY: {"db-b": {"compliant": 1, "quarantined": 1},
                                    "db-c": {"skipped_not_authorized": 3}}}
    asked = []

    def only_b(database_id):
        asked.append(database_id)
        return database_id == "db-b"

    seen = json.loads(summaries.details_for_reader(
        _completed_entry(stored), lambda database_id, asset_id: True, only_b)["details"])
    assert seen == {"nodesEvaluated": 3, "summary": {"compliant": 2, "quarantined": 1},
                    "unauthorizedNodes": 3}
    assert sorted(asked) == ["db-b", "db-c"]
