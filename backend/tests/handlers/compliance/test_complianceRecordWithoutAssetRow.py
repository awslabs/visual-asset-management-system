# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance record whose asset has no row is refused to a reader whose asset rules read an
asset's own fields (D101 follow-up).

Permanently deleting an asset removes its asset-table row; its compliance record stays until the
trigger or a sweep removes it. A reader whose asset rules read only the database id (Database Admin,
Database User) or who has no asset rules (the global Compliance Admin) is decided on the database,
as for any asset of it, and keeps seeing the record. A reader whose rules read an asset's fields (a
deny on a tag) has nothing to decide it on, and was shown it as if the database alone decided; now
it is refused (fail closed), as a record naming an archived asset that is no longer in the archive
is not shown (D90): its reads answer 403, the listings, counts and cascades leave it out (a cascade
counts it as a node the reader may not access), its actions are refused, and Evaluate All and the
schema sweep skip it as not authorized instead of removing it.

The world is the D101 one (`test_complianceLiveAssetAccess`), with one more record, `lv-gone`, in
DB1 and no row for it, live or archived. For every role set the record is compared with `lv-plain`,
a live untagged asset of DB1 whose row exists: a reader decided per database must see the two
alike; a reader decided on each asset's row sees `lv-plain` and not `lv-gone`.
"""

import json

import pytest

from backend.tests.handlers.compliance._harness import body_of
from backend.tests.handlers.compliance.test_complianceLiveAssetAccess import (  # noqa: F401
    CASCADE_IDS, DB1, ROLE_SETS, SCHEMA, _in_world, _real_permission_constants, _role_set,
    role_enforcer,
)
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceSchemaBindingService as binding_svc

GONE = "lv-gone"
PLAIN = "lv-plain"
GONE_CASCADE = "7a0c0000-0000-4000-8000-0000000d1500"
NODES_CASCADE = "7a0c0000-0000-4000-8000-0000000d1501"
# Role sets whose asset rules read an asset's own fields (`deny-tagged-assets.json` reads tags).
FIELD_READERS = ("database-admin+deny-tagged-assets", "database-admin+deny-tagged-assets+get",
                 "round-16b")


def _seed_gone(doubles):
    """The records a permanently deleted asset leaves until they are removed: its state (an
    asset-level override, quarantined), an evaluation, an audit entry, a pending cascade it
    triggered, and a node of a finished cascade. No asset row."""
    name = f"Name of {GONE}"
    doubles["complianceAssetStateStorage"].put_item(Item={
        "databaseId": DB1, "assetId": GONE, "schemaName": SCHEMA, "schemaSource": "asset",
        "complianceState": "quarantined", "quarantineReason": "rule failed",
        "lastEvaluationId": f"eval-{GONE}", "assetName": name,
        "assetNameSort": f"{name.lower()}#{GONE}", "assetSearchText": f"{name.lower()} {GONE}",
        "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"})
    doubles["complianceEvaluationStorage"].put_item(Item={
        "evaluationId": f"eval-{GONE}", "databaseId:assetId": f"{DB1}:{GONE}",
        "databaseId": DB1, "assetId": GONE, "schemaName": SCHEMA,
        "evaluatedAt": "2026-10-01T00:00:00+00:00", "verdict": "quarantined",
        "status": "completed", "ruleResults": "[]"})
    doubles["complianceAuditStorage"].put_item(Item=build_audit_item(
        DB1, GONE, "compliance_check", "SYSTEM_USER", {},
        timestamp="2026-10-01T00:00:59+00:00", asset_name=name))
    doubles["complianceCascadeStorage"].put_item(Item={
        "cascadeId": GONE_CASCADE, "state": "pending_approval",
        "createdAt": "2026-10-01T00:00:59+00:00", "triggeredByDatabaseId": DB1,
        "triggeredByAssetId": GONE, "triggeredByAssetName": name,
        "approvalTimeoutAt": "2999-01-01T00:00:00+00:00", "nodes": "{}", "executionOrder": "[]"})
    keys = [f"{DB1}:{PLAIN}", f"{DB1}:{GONE}"]
    doubles["complianceCascadeStorage"].put_item(Item={
        "cascadeId": NODES_CASCADE, "state": "completed", "createdAt": "2026-10-03T00:00:00+00:00",
        "triggeredByDatabaseId": DB1, "triggeredByAssetId": "lv-parent",
        "triggeredByAssetName": "Name of lv-parent", "totalNodes": len(keys),
        "nodes": json.dumps({key: "compliant" for key in keys}),
        "executionOrder": json.dumps(keys),
        "results": json.dumps([{"node": key, "status": "compliant"} for key in keys]),
        "nodeNames": json.dumps({key: f"Name of {key.split(':', 1)[1]}" for key in keys})})


def _ok(response):
    return response["statusCode"] == 200


def _listed(response, key, id_of=lambda row: row["assetId"]):
    if response["statusCode"] != 200:
        return None
    return {id_of(row) for row in body_of(response)[key]}


def _observe(call, _list_assets, doubles):
    out = {}

    def sweeps(reseed=False):
        evaluate_all = call(evaluate_svc, "POST", f"/compliance/evaluate/{DB1}",
                            {"databaseId": DB1}, request_body={})
        if reseed:
            # Evaluate All removes the record for a reader who may read it; the sweep finds it again.
            _seed_gone(doubles)
        sweep = call(evaluate_svc, "POST", f"/compliance/sweep/{SCHEMA}", {"schemaName": SCHEMA},
                     request_body={})
        return tuple(body_of(response) if _ok(response) else None
                     for response in (evaluate_all, sweep))

    # The sweeps as the D101 world has them, before the record is added.
    out["sweeps before"] = sweeps()
    _seed_gone(doubles)
    for asset_id in (PLAIN, GONE):
        params = {"databaseId": DB1, "assetId": asset_id}
        out.setdefault("state", {})[asset_id] = _ok(call(
            evaluate_svc, "GET", f"/compliance/state/{DB1}/{asset_id}", params))
        out.setdefault("evaluations", {})[asset_id] = _ok(call(
            evaluate_svc, "GET", f"/compliance/evaluations/{DB1}/{asset_id}", params))
        out.setdefault("asset audit", {})[asset_id] = _ok(call(
            audit_svc, "GET", f"/compliance/audit/{DB1}/{asset_id}", params))
    cascade_of = {PLAIN: CASCADE_IDS[PLAIN], GONE: GONE_CASCADE}
    out["cascade"] = {asset_id: _ok(call(cascade_svc, "GET", f"/compliance/cascades/{cascade_id}",
                                         {"cascadeId": cascade_id}))
                      for asset_id, cascade_id in cascade_of.items()}
    overview = call(evaluate_svc, "GET", f"/compliance/state/{DB1}", {"databaseId": DB1})
    out["overview"] = _listed(overview, "assets")
    if out["overview"] is not None:
        body = body_of(overview)
        out["overview counts"] = (body["summary"]["quarantined"], body["totalAssets"])
    out["bindings listing"] = _listed(call(
        binding_svc, "GET", f"/compliance/bind/{DB1}", {"databaseId": DB1}), "assetOverrides")
    out["quarantine listing"] = _listed(call(quarantine_svc, "GET", "/compliance/quarantine"),
                                        "quarantinedAssets")
    out["audit listing"] = _listed(call(audit_svc, "GET", "/compliance/audit"), "entries")
    out["audit listing by event type"] = _listed(call(
        audit_svc, "GET", "/compliance/audit", query={"eventType": "compliance_check"}), "entries")
    out["audit listing by database"] = _listed(call(
        audit_svc, "GET", "/compliance/audit", query={"databaseId": DB1}), "entries")
    out["cascade listing"] = _listed(call(cascade_svc, "GET", "/compliance/cascades"), "cascades")
    nodes = call(cascade_svc, "GET", f"/compliance/cascades/{NODES_CASCADE}",
                 {"cascadeId": NODES_CASCADE})
    out["cascade nodes"] = None
    if _ok(nodes):
        body = body_of(nodes)
        out["cascade nodes"] = ({key.split(":", 1)[1] for key in json.loads(body["nodes"])},
                                body["unauthorizedNodes"], json.dumps(body))
    out["sweeps after"] = sweeps(reseed=True)
    # Actions last (the sweeps above may remove the record for a reader who may read it).
    _seed_gone(doubles)
    for asset_id in (PLAIN, GONE):
        params = {"databaseId": DB1, "assetId": asset_id}
        base = f"/compliance/quarantine/{DB1}/{asset_id}"
        for name, method, service, path, request_body, path_params in (
                ("release", "POST", quarantine_svc, f"{base}/release", {}, params),
                ("grant exception", "POST", quarantine_svc, f"{base}/exception",
                 {"reason": "accepted"}, params),
                ("evaluate", "POST", evaluate_svc, f"/compliance/evaluate/{DB1}/{asset_id}", {},
                 params),
                ("approve cascade", "POST", cascade_svc,
                 f"/compliance/cascades/{cascade_of[asset_id]}/approve", {},
                 {"cascadeId": cascade_of[asset_id]})):
            out.setdefault(name, {})[asset_id] = call(
                service, method, path, path_params, request_body=request_body)["statusCode"] != 403
    return out


@pytest.fixture(scope="module")
def observations():
    return {}


def _observed(name, role_enforcer, cache):  # noqa: F811
    if name not in cache:
        roles, constraints = _role_set(name)
        cache[name] = _in_world(role_enforcer(roles, constraints), roles, _observe)
    return cache[name]


def _expected_gone(name, plain):
    """What the reader gets for the record with no row, given what it gets for `lv-plain`."""
    return False if name in FIELD_READERS else plain


@pytest.mark.unit
def test_the_field_readers_see_the_record_of_an_asset_that_has_a_row(role_enforcer, observations):  # noqa: F811
    """The comparison is meaningful: every field reader sees `lv-plain` on every read."""
    for name in FIELD_READERS:
        found = _observed(name, role_enforcer, observations)
        for read in ("state", "evaluations", "asset audit", "cascade"):
            assert found[read][PLAIN] is True, (name, read)


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_the_single_reads_refuse_it_to_a_field_reader_only(name, role_enforcer, observations):  # noqa: F811
    found = _observed(name, role_enforcer, observations)
    for read in ("state", "evaluations", "asset audit", "cascade"):
        assert found[read][GONE] is _expected_gone(name, found[read][PLAIN]), (name, read)


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_the_listings_and_counts_leave_it_out_for_a_field_reader_only(
        name, role_enforcer, observations):  # noqa: F811
    found = _observed(name, role_enforcer, observations)
    for listing in ("overview", "bindings listing", "quarantine listing", "audit listing",
                    "audit listing by event type", "audit listing by database",
                    "cascade listing"):
        rows = found[listing]
        if rows is None:
            continue
        assert (GONE in rows) is _expected_gone(name, PLAIN in rows), (name, listing, rows)
    if found["overview"] is not None:
        # The counts follow the rows: every row listed is quarantined, and none other is counted.
        assert found["overview counts"] == (len(found["overview"]), len(found["overview"])), (
            name, found["overview counts"])


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_a_cascade_counts_its_node_as_not_accessible_for_a_field_reader(
        name, role_enforcer, observations):  # noqa: F811
    found = _observed(name, role_enforcer, observations)
    view = found["cascade nodes"]
    if view is None:
        return
    nodes, unauthorized, text = view
    gone = _expected_gone(name, PLAIN in nodes)
    assert (GONE in nodes) is gone, (name, nodes)
    if PLAIN in nodes and not gone:
        assert unauthorized == 1 and GONE not in text, (name, unauthorized)


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_its_actions_are_refused_to_a_field_reader_only(name, role_enforcer, observations):  # noqa: F811
    found = _observed(name, role_enforcer, observations)
    for action in ("release", "grant exception", "evaluate", "approve cascade"):
        assert found[action][GONE] is _expected_gone(name, found[action][PLAIN]), (name, action)


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_the_sweeps_skip_it_as_not_authorized_for_a_field_reader(name, role_enforcer, observations):  # noqa: F811
    """A reader decided per database finds the record's asset gone and removes the record
    (`removedOrphans`); a field reader may not read it, so it is skipped as not authorized and
    left for the trigger or a sweep by a reader who may read it. A reader who may not evaluate DB1
    skips it as not authorized in a sweep, as any asset of DB1."""
    found = _observed(name, role_enforcer, observations)
    may_evaluate_db1 = found["sweeps before"][0] is not None
    for before, after in zip(found["sweeps before"], found["sweeps after"]):
        if before is None:
            assert after is None, name
            continue
        if name in FIELD_READERS or not may_evaluate_db1:
            assert after["skippedNotAuthorized"] == before["skippedNotAuthorized"] + 1, (name, after)
            assert after["removedOrphans"] == before["removedOrphans"], (name, after)
        else:
            assert after["skippedNotAuthorized"] == before["skippedNotAuthorized"], (name, after)
            assert after["removedOrphans"] == before["removedOrphans"] + 1, (name, after)
        assert GONE not in {entry["assetId"] for entry in after["assetsTriggered"]}, (name, after)
