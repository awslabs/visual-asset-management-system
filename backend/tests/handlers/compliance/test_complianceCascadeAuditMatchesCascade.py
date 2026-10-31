# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A finished cascade's audit entry counts each descendant as the cascade read does (D104).

For a reader whose asset rules hide a live descendant in a database the reader may read (a `GET`
deny on one of its tags), the cascade read and its listing counted that descendant in
`unauthorizedNodes` ("1 node you cannot access"), while the cascade's `cascade_completed` audit
entry left it out of every count. Now the entry counts it in `unauthorizedNodes` too, without its
id, name or verdict, as core VAMS asset links count the links a caller may not open. An archived
descendant the reader may not see is still left out of both views uncounted (D88), and so is a
descendant archived after the cascade reached it.

Both views are read of one cascade: the finished cascade row of the D101 world (`lv-parent` over
`lv-plain`, `lv-tagged` and the archived `lv-archived` in the parent's database, `lv-other` and
`lv-other-tagged` in a second one) and the entry the executor writes from the same results
(`completion_details`), on every audit path that returns it, for the tag-deny reader, an
archive-deny reader and a reader who may see everything, with the production enforcer.
"""

import json
from collections import Counter

import pytest

from backend.tests.handlers.compliance._harness import body_of
from backend.tests.handlers.compliance.test_complianceArchivedAssetAccess import asset_read_deny
from backend.tests.handlers.compliance.test_complianceLiveAssetAccess import (  # noqa: F401
    COMPLETED_ID, DB1, DB2, _in_world, _real_permission_constants, _role_set, _template,
    role_enforcer,
)
from common.compliance import cascadeAuditSummary as summaries
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeService as cascade_svc

PARENT = "lv-parent"
ARCHIVED_SINCE = "lv-plain"
AUDIT_PATHS = {
    "asset trail": (f"/compliance/audit/{DB1}/{PARENT}", {"databaseId": DB1, "assetId": PARENT},
                    None),
    "listing by event type": ("/compliance/audit", None, {"eventType": "cascade_completed"}),
    "listing by database": ("/compliance/audit", None, {"databaseId": DB1}),
    "whole trail": ("/compliance/audit", None, None),
}


def _readers():
    """(roles, constraints) of the three readers: the round-16b tag-deny reader (Database Admin of
    both databases with a `GET` deny on the tag), a Database Admin of both databases with a deny on
    the parent database's archive, and a Database Admin of both databases."""
    both = (["r-a1", "r-a2"], _template("database-admin.json", "r-a1", DB1)
            + _template("database-admin.json", "r-a2", DB2))
    archive_deny = (both[0], both[1] + asset_read_deny(
        "r-a1", "deny-archive", {"field": "databaseId", "operator": "equals",
                                 "value": f"{DB1}#deleted"}, ("asset", "complianceEvaluation")))
    return {"tag deny": _role_set("round-16b"), "archive deny": archive_deny, "everything": both}


def _cascade_view(call):
    """The cascade read and its listing row: the descendants shown, by status, and the count of
    those the reader may not access."""
    response = call(cascade_svc, "GET", f"/compliance/cascades/{COMPLETED_ID}",
                    {"cascadeId": COMPLETED_ID})
    assert response["statusCode"] == 200, response
    body = body_of(response)
    listed = [row for row in body_of(call(cascade_svc, "GET", "/compliance/cascades",
                                          query={"state": "completed"}))["cascades"]
              if row["cascadeId"] == COMPLETED_ID]
    assert len(listed) == 1
    return {
        "get": (dict(Counter(json.loads(body["nodes"]).values())), body["unauthorizedNodes"]),
        "listing": (dict(Counter(json.loads(listed[0]["nodes"]).values())),
                    listed[0]["unauthorizedNodes"]),
    }


def _entry_views(call):
    """The cascade's `cascade_completed` entry's details on every audit path."""
    out = {}
    for name, (path, params, query) in AUDIT_PATHS.items():
        response = call(audit_svc, "GET", path, params, query)
        assert response["statusCode"] == 200, (name, response)
        found = [entry for entry in body_of(response)["entries"]
                 if entry.get("eventType") == "cascade_completed"
                 and entry.get("cascadeId") == COMPLETED_ID]
        assert len(found) == 1, (name, found)
        out[name] = json.loads(found[0]["details"])
    return out


def _archive(doubles, asset_id):
    """Archive a descendant after the cascade ran: its row moves to `<databaseId>#deleted`."""
    table = doubles["assetStorage"]
    row = table.get_item(Key={"databaseId": DB1, "assetId": asset_id})["Item"]
    table.put_item(Item=dict(row, databaseId=f"{DB1}#deleted", status="archived"))
    table.delete_item(Key={"databaseId": DB1, "assetId": asset_id})


def _both_views(call, _list_assets, doubles):
    row = doubles["complianceCascadeStorage"].get_item(Key={"cascadeId": COMPLETED_ID})["Item"]
    # The entry the executor writes when the cascade finishes, from the same results.
    doubles["complianceAuditStorage"].put_item(Item=build_audit_item(
        DB1, PARENT, "cascade_completed", "SYSTEM_USER",
        summaries.completion_details(json.loads(row["results"]), DB1), cascade_id=COMPLETED_ID,
        timestamp="2026-10-05T00:00:00+00:00", asset_name=f"Name of {PARENT}"))
    out = {"as finished": (_cascade_view(call), _entry_views(call))}
    _archive(doubles, ARCHIVED_SINCE)
    out["after archiving lv-plain"] = (_cascade_view(call), _entry_views(call))
    return out


@pytest.fixture(scope="module")
def observations():
    return {}


def _observed(reader, role_enforcer, cache):  # noqa: F811
    if reader not in cache:
        roles, constraints = _readers()[reader]
        cache[reader] = _in_world(role_enforcer(roles, constraints), roles, _both_views)
    return cache[reader]


# What each reader is shown of the cascade: (descendants shown, by status; not accessible), as
# finished and after `lv-plain` is archived.
EXPECTED = {
    "tag deny": {
        "as finished": ({"compliant": 2, "skipped_archived": 1}, 2),
        "after archiving lv-plain": ({"compliant": 2, "skipped_archived": 1}, 2),
    },
    "archive deny": {
        "as finished": ({"compliant": 2, "quarantined": 2}, 0),
        "after archiving lv-plain": ({"compliant": 1, "quarantined": 2}, 0),
    },
    "everything": {
        "as finished": ({"compliant": 2, "quarantined": 2, "skipped_archived": 1}, 0),
        "after archiving lv-plain": ({"compliant": 2, "quarantined": 2, "skipped_archived": 1}, 0),
    },
}


@pytest.mark.unit
@pytest.mark.parametrize("reader", sorted(EXPECTED))
@pytest.mark.parametrize("moment", ["as finished", "after archiving lv-plain"])
def test_the_entry_counts_each_descendant_as_the_cascade_read_does(
        reader, moment, role_enforcer, observations):  # noqa: F811
    cascade, entries = _observed(reader, role_enforcer, observations)[moment]
    shown, unauthorized = EXPECTED[reader][moment]
    assert cascade["get"] == (shown, unauthorized), (reader, moment, cascade)
    assert cascade["listing"] == (shown, unauthorized), (reader, moment, cascade)
    evaluated = sum(count for status, count in shown.items()
                    if status not in summaries.NOT_EVALUATED_STATUSES)
    for path, details in entries.items():
        assert details["summary"] == shown, (reader, moment, path, details)
        assert details["nodesEvaluated"] == evaluated, (reader, moment, path, details)
        # The cascade reached the second database, so the entry always gives the count.
        assert details["unauthorizedNodes"] == unauthorized, (reader, moment, path, details)
        assert summaries.LIVE_BY_DATABASE_KEY not in details, (reader, moment, path)
        text = json.dumps(details)
        for asset_id in ("lv-plain", "lv-tagged", "lv-other", "lv-other-tagged", "lv-archived"):
            assert asset_id not in text, (reader, moment, path, asset_id)
