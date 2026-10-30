# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A finished cascade's audit entry and notification count an archived descendant only for a reader
who may see it.

The `cascade_completed` audit entry used to carry `summary`, the node count per status, with every
descendant the cascade skipped as archived counted under `skipped_archived`, and `nodesEvaluated`
counting them too; the subscriber notification listed the same summary. A role that may not see a
database's archive (the Compliance Admin and Compliance Read-Only templates, and any role with a
deny on `<databaseId>#deleted`) learned how many archived assets the cascade met, from the audit
trail and the email.

Now the entry records those descendants per database (`skippedArchivedByDatabase`), outside the
shared `summary` and `nodesEvaluated`; the audit service counts a database's archived descendants
only for a reader who may see that database's archive (`ArchivedAssets.archive_visible`, the
evaluation GET on `<databaseId>#deleted`), on every path that returns the entry. The notification,
one message for every subscriber, carries no archived count. An entry written by an earlier build
keeps its `skipped_archived` count only for a reader who may see the archive of the parent's
database.

The same holds for the cascade the parent's next evaluation opens (`cascade_auto_triggered`, D89):
its `childCount` used to count every direct child link, archived children included, and the
approval email said "Downstream assets affected: 2" for one live and one archived child. Now the
entry counts the archived children per database (`archivedChildrenByDatabase`), outside
`childCount`, the audit service adds them back only for a reader who may see that database's
archive, and the email counts the live children only.

The matrix runs the executor, the audit writes and the audit service on moto tables built from the
CDK definitions with the production enforcer of every shipped role (as
`test_complianceArchivedCounts` does); a role's archive visibility is read from the same world: may
it read the archived descendant's compliance record.
"""

import json
import pathlib
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    body_of, claims_for, mirror_engine, real_constants, rest_event,
)
from backend.tests.handlers.compliance.test_complianceArchivedAssetAccess import _enforcer
from backend.tests.handlers.compliance.test_complianceArchivedCounts import (
    ARCHIVE_DENY_SET, ARCHIVED_CHILD, CASCADE_ID, DB, PARENT, SET_NAMES, USER_ID, _roles, _seed,
)
from common.compliance import cascadeAuditSummary as summaries
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeExecutor as executor
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceNotifications as notifications
from handlers.compliance import complianceTrigger as trigger

SERVICES = (store, evaluate_svc, trigger, executor, cascade_svc, audit_svc)
# A finished cascade of the same parent written by an earlier build: its summary counts the
# archived descendant under `skipped_archived`, and there is no per-database record.
OLD_CASCADE_ID = "7a0c0000-0000-4000-8000-0000000000c2"
OLD_DETAILS = {"nodesEvaluated": 2, "summary": {"compliant": 1, "skipped_archived": 1}}
TOPIC = "arn:aws:sns:us-east-1:1:asset-topic"

# What a reader sees of each entry: one that may see DB's archive, one that may not. A descendant
# skipped as archived was not evaluated: `nodesEvaluated` leaves it out for every reader, and an
# entry written by an earlier build keeps the count it was written with.
SEEN_BY_ARCHIVE_READER = {
    CASCADE_ID: {"nodesEvaluated": 1, "summary": {"compliant": 1, "skipped_archived": 1},
                 "skippedArchivedByDatabase": {DB: 1}},
    OLD_CASCADE_ID: OLD_DETAILS,
}
SEEN_BY_OTHER_READER = {
    CASCADE_ID: {"nodesEvaluated": 1, "summary": {"compliant": 1}},
    OLD_CASCADE_ID: {"nodesEvaluated": 1, "summary": {"compliant": 1}},
}

# The cascade the parent's next evaluation opens (`check_and_trigger_cascade`) over the same two
# children: what each reader sees of its `cascade_auto_triggered` entry, and what is stored.
OPENED_SEEN_BY_ARCHIVE_READER = {"reason": "parent_update", "childCount": 2,
                                 "archivedChildrenByDatabase": {DB: 1}}
OPENED_SEEN_BY_OTHER_READER = {"reason": "parent_update", "childCount": 1}
OPENED_STORED = {"reason": "parent_update", "childCount": 1,
                 "archivedChildrenByDatabase": {DB: [ARCHIVED_CHILD]}}

# The audit reads that return the parent's entries: the asset's audit trail and each listing path.
AUDIT_READS = {
    "asset audit": (f"/compliance/audit/{DB}/{PARENT}", {"databaseId": DB, "assetId": PARENT},
                    None),
    "listing": ("/compliance/audit", None, None),
    "listing by event type": ("/compliance/audit", None, {"eventType": "cascade_completed"}),
    "listing by database": ("/compliance/audit", None, {"databaseId": DB}),
    "listing by asset": ("/compliance/audit", None, {"databaseId": DB, "assetId": PARENT}),
    "listing by text": ("/compliance/audit", None, {"text": PARENT}),
}
# The paths that return the opened cascade's entry: the same, with its own event-type listing.
OPENED_READS = dict(
    {name: read for name, read in AUDIT_READS.items() if name != "listing by event type"},
    **{"listing by opened event type": (
        "/compliance/audit", None, {"eventType": "cascade_auto_triggered"})})


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
# The world: the archived-counts world (a cascade of PARENT run by the executor over a live and an
# archived child), here with the real audit writes and notification text, and an earlier build's
# entry of the same parent
# ---------------------------------------------------------------------------------------------
def _observe_world(enforcer):
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = enforcer.enforce
    mirror_engine(stand_in, enforcer)
    publish = MagicMock()
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
        for module in (evaluate_svc, cascade_svc, audit_svc):
            patches += [patch.object(module, "CasbinEnforcer", return_value=stand_in),
                        patch.object(module, "request_to_claims", claims_for(USER_ID))]
        patches += [
            patch.object(store, "run_evaluation", return_value={
                "evaluationId": "e-new", "verdict": "compliant", "complianceState": "compliant"}),
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()),
            patch.object(notifications, "_get_asset_topic", return_value=(f"Asset {PARENT}", TOPIC)),
            patch.object(notifications, "_publish", publish),
        ]
        for active in patches:
            active.start()
        try:
            doubles["complianceAuditStorage"].put_item(Item=build_audit_item(
                DB, PARENT, "cascade_completed", store.SYSTEM_ACTOR, OLD_DETAILS,
                cascade_id=OLD_CASCADE_ID, timestamp="2026-10-01T00:00:00+00:00",
                asset_name=f"Asset {PARENT}"))
            _seed(doubles)
            # The parent's next evaluation opens a cascade over the same live and archived child.
            trigger.check_and_trigger_cascade(DB, PARENT)

            def call(service, method, path, params=None, query=None):
                response = service.lambda_handler(
                    rest_event(method, path, params, query), MagicMock())
                assert response["statusCode"] < 500, (method, path, response)
                return response

            out = {"archive visible": call(
                evaluate_svc, "GET", f"/compliance/state/{DB}/{ARCHIVED_CHILD}",
                {"databaseId": DB, "assetId": ARCHIVED_CHILD})["statusCode"] == 200}
            for name, (path, params, query) in {**AUDIT_READS, **OPENED_READS}.items():
                response = call(audit_svc, "GET", path, params, query)
                if response["statusCode"] != 200:
                    out[name] = None
                    continue
                entries = body_of(response)["entries"]
                out[name] = {
                    "details": {entry["cascadeId"]: json.loads(entry["details"])
                                for entry in entries
                                if entry.get("eventType") == "cascade_completed"},
                    "opened": [json.loads(entry["details"]) for entry in entries
                               if entry.get("eventType") == "cascade_auto_triggered"],
                    "text": json.dumps(entries, default=str),
                }
            out["published"] = [
                (c.kwargs.get("subject", c.args[2] if len(c.args) > 2 else ""),
                 c.args[1] if len(c.args) > 1 else c.kwargs["message"])
                for c in publish.call_args_list]
            items = doubles["complianceAuditStorage"].scan()["Items"]
            out["stored"] = {item["cascadeId"]: json.loads(item["details"]) for item in items
                             if item.get("eventType") == "cascade_completed"}
            out["stored opened"] = [json.loads(item["details"]) for item in items
                                    if item.get("eventType") == "cascade_auto_triggered"]
            return out
        finally:
            for active in reversed(patches):
                active.stop()


@pytest.fixture(scope="module")
def observations():
    return {}


def _observed(set_name, role_enforcer, cache):
    if set_name not in cache:
        roles, constraints = _roles(set_name)
        cache[set_name] = _observe_world(role_enforcer(roles, constraints))
    return cache[set_name]


# ---------------------------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("set_name", SET_NAMES)
def test_the_audit_entry_counts_the_archived_descendants_the_reader_may_see(
        set_name, role_enforcer, observations):
    found = _observed(set_name, role_enforcer, observations)
    expected = SEEN_BY_ARCHIVE_READER if found["archive visible"] else SEEN_BY_OTHER_READER
    for name in AUDIT_READS:
        view = found[name]
        if view is None or not view["details"]:
            # A role that may not read the parent's audit trail sees neither entry.
            continue
        assert view["details"] == expected, (set_name, name, view["details"])
        if not found["archive visible"]:
            assert "skipped_archived" not in view["text"], (set_name, name)
            assert summaries.ARCHIVED_BY_DATABASE_KEY not in view["text"], (set_name, name)


@pytest.mark.unit
@pytest.mark.parametrize("set_name,archive_visible", [
    ("template:database-admin.json", True),
    ("template:compliance-admin.json", False),
    ("template:compliance-readonly.json", False),
    (ARCHIVE_DENY_SET, False),
])
def test_each_compliance_role_reads_the_entry_with_its_own_archived_count(
        set_name, archive_visible, role_enforcer, observations):
    """The named roles all read the parent's audit trail (the comparison is not vacuous); only the
    Database Admin, who may see the archive, is told of the archived descendant."""
    found = _observed(set_name, role_enforcer, observations)
    assert found["archive visible"] is archive_visible
    expected = SEEN_BY_ARCHIVE_READER if archive_visible else SEEN_BY_OTHER_READER
    for name in AUDIT_READS:
        assert found[name] is not None, (set_name, name)
        assert found[name]["details"] == expected, (set_name, name, found[name]["details"])


@pytest.mark.unit
def test_the_stored_entry_keeps_the_archived_descendants_out_of_the_shared_summary(
        role_enforcer, observations):
    found = _observed("template:database-admin.json", role_enforcer, observations)
    assert found["stored"][CASCADE_ID] == {
        "nodesEvaluated": 1, "summary": {"compliant": 1},
        "skippedArchivedByDatabase": {DB: [ARCHIVED_CHILD]}}


@pytest.mark.unit
@pytest.mark.parametrize("set_name", ["template:database-admin.json",
                                      "template:compliance-admin.json"])
def test_the_notification_carries_no_archived_count(set_name, role_enforcer, observations):
    """One message goes to every subscriber, whoever started or reads the cascade: it lists the
    live descendant's verdict and nothing of the archived one."""
    found = _observed(set_name, role_enforcer, observations)
    completed = [message for subject, message in found["published"]
                 if subject.startswith("[CASCADE COMPLETE]")]
    assert len(completed) == 1
    message = completed[0]
    assert "compliant: 1" in message
    assert "skipped_archived" not in message
    assert "archived" not in message.lower()
    results = message.split("Results:", 1)[1].split("Best Regards", 1)[0]
    assert [line.strip() for line in results.strip().splitlines()] == ["compliant: 1"]


# ---------------------------------------------------------------------------------------------
# The cascade the parent's next evaluation opens (D89)
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("set_name", SET_NAMES)
def test_the_opened_entry_counts_the_archived_children_the_reader_may_see(
        set_name, role_enforcer, observations):
    found = _observed(set_name, role_enforcer, observations)
    expected = (OPENED_SEEN_BY_ARCHIVE_READER if found["archive visible"]
                else OPENED_SEEN_BY_OTHER_READER)
    for name in OPENED_READS:
        view = found[name]
        if view is None or not view["opened"]:
            # A role that may not read the parent's audit trail sees no entry.
            continue
        assert view["opened"] == [expected], (set_name, name, view["opened"])
        if not found["archive visible"]:
            assert "archivedChildrenByDatabase" not in view["text"], (set_name, name)


@pytest.mark.unit
@pytest.mark.parametrize("set_name,archive_visible", [
    ("template:database-admin.json", True),
    ("template:compliance-admin.json", False),
    ("template:compliance-readonly.json", False),
    (ARCHIVE_DENY_SET, False),
])
def test_each_compliance_role_reads_the_opened_entry_with_its_own_archived_count(
        set_name, archive_visible, role_enforcer, observations):
    found = _observed(set_name, role_enforcer, observations)
    assert found["archive visible"] is archive_visible
    expected = OPENED_SEEN_BY_ARCHIVE_READER if archive_visible else OPENED_SEEN_BY_OTHER_READER
    for name in OPENED_READS:
        assert found[name] is not None, (set_name, name)
        assert found[name]["opened"] == [expected], (set_name, name, found[name]["opened"])


@pytest.mark.unit
def test_the_stored_opened_entry_keeps_the_archived_children_out_of_the_child_count(
        role_enforcer, observations):
    found = _observed("template:database-admin.json", role_enforcer, observations)
    assert found["stored opened"] == [OPENED_STORED]


@pytest.mark.unit
@pytest.mark.parametrize("set_name", ["template:database-admin.json",
                                      "template:compliance-admin.json"])
def test_the_approval_notification_counts_no_archived_child(set_name, role_enforcer, observations):
    """One message to every subscriber: it counts the live child only."""
    found = _observed(set_name, role_enforcer, observations)
    pending = [message for subject, message in found["published"]
               if subject.startswith("[CASCADE PENDING]")]
    assert len(pending) == 1
    assert "Downstream assets affected: 1\n" in pending[0]
    assert "archived" not in pending[0].lower()


# ---------------------------------------------------------------------------------------------
# The entry's shape, unit by unit
# ---------------------------------------------------------------------------------------------
def _entry(details, event_type="cascade_completed", database_id=DB, encode=True):
    return {"entryId": "e-1", "eventType": event_type, "databaseId": database_id,
            "assetId": PARENT, "details": json.dumps(details) if encode else details}


def _details(entry):
    raw = entry["details"]
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.mark.unit
def test_the_status_is_the_executors():
    """The status the summary moves out is the one the executor records, and the event is the one
    it writes."""
    assert summaries.SKIPPED_ARCHIVED_STATUS == executor.NODE_STATE_SKIPPED_ARCHIVED
    assert summaries.SKIPPED_ARCHIVED_STATUS == cascade_svc.NODE_STATE_SKIPPED_ARCHIVED


@pytest.mark.unit
def test_the_statuses_not_counted_as_evaluated_are_the_executors():
    assert summaries.NOT_EVALUATED_STATUSES == {
        executor.NODE_STATE_SKIPPED, executor.NODE_STATE_SKIPPED_NOT_AUTHORIZED,
        executor.NODE_STATE_SKIPPED_ARCHIVED, executor.NODE_STATE_SUPERSEDED,
        executor.NODE_STATE_ERROR}


@pytest.mark.unit
def test_completion_details_record_the_archived_descendants_per_database():
    results = [
        {"node": "db-a:1", "status": "compliant"},
        {"node": "db-a:2", "status": "skipped_archived"},
        {"node": "db-b:3", "status": "skipped_archived"},
        {"node": "db-b:4", "status": "skipped_archived"},
        {"node": "db-b:5", "status": "quarantined"},
        {"node": "db-c:6", "status": "skipped_not_authorized"},
    ]
    assert summaries.completion_details(results, "db-a") == {
        "nodesEvaluated": 1,
        "summary": {"compliant": 1},
        "summaryByDatabase": {"db-b": {"quarantined": 1}, "db-c": {"skipped_not_authorized": 1}},
        "skippedArchivedByDatabase": {"db-a": ["2"], "db-b": ["3", "4"]},
    }


@pytest.mark.unit
def test_completion_details_without_archived_descendants_keep_the_shape():
    assert summaries.completion_details([{"node": "db-a:1", "status": "compliant"}], "db-a") == {
        "nodesEvaluated": 1, "summary": {"compliant": 1}}
    assert summaries.completion_details([], "db-a") == {"nodesEvaluated": 0, "summary": {}}


@pytest.mark.unit
def test_nodes_evaluated_counts_the_descendants_evaluated_to_a_verdict_only():
    # The cascade progress the web and CLI show counts a node as evaluated only when its status is
    # a verdict; the entry's `nodesEvaluated` agrees, and the others stay in `summary` by status.
    results = [{"node": f"db-a:{n}", "status": status} for n, status in enumerate([
        "compliant", "quarantined", "pending_pipeline", "non_compliant", "skipped",
        "skipped_not_authorized", "superseded", "error"])]
    results.append({"node": "db-b:9", "status": "skipped_not_authorized"})
    details = summaries.completion_details(results, "db-a")
    assert details["nodesEvaluated"] == 4
    assert sum(details["summary"].values()) == 8
    # A reader of db-b adds its evaluated descendants only (none here) to `nodesEvaluated`.
    seen = _details(summaries.details_for_reader(
        _entry(details), lambda _db, _asset: True, lambda database_id: True))
    assert seen["nodesEvaluated"] == 4
    assert seen["summary"]["skipped_not_authorized"] == 2


@pytest.mark.unit
def test_a_reader_is_counted_only_the_databases_whose_archive_it_may_see():
    stored = {"nodesEvaluated": 3,
              "summary": {"compliant": 2, "quarantined": 1},
              "skippedArchivedByDatabase": {"db-a": 1, "db-b": 2}}
    asked = []

    def only_b(database_id, asset_id):
        asked.append(database_id)
        return database_id == "db-b"

    seen = _details(summaries.details_for_reader(_entry(stored), only_b))
    # The archived descendants are counted under `skipped_archived` where the reader may see them;
    # `nodesEvaluated` counts the evaluated ones only, for every reader.
    assert seen == {"nodesEvaluated": 3,
                    "summary": {"compliant": 2, "quarantined": 1, "skipped_archived": 2},
                    "skippedArchivedByDatabase": {"db-b": 2}}
    assert sorted(asked) == ["db-a", "db-b"]

    everything = _details(summaries.details_for_reader(_entry(stored), lambda _db, _asset: True))
    assert everything == {"nodesEvaluated": 3,
                          "summary": {"compliant": 2, "quarantined": 1, "skipped_archived": 3},
                          "skippedArchivedByDatabase": {"db-a": 1, "db-b": 2}}

    nothing = _details(summaries.details_for_reader(_entry(stored), lambda _db, _asset: False))
    assert nothing == {"nodesEvaluated": 3, "summary": {"compliant": 2, "quarantined": 1}}


@pytest.mark.unit
@pytest.mark.parametrize("event_type,count_of,base", [
    # A finished cascade counts the archived descendants a reader may see under `skipped_archived`
    # (not in `nodesEvaluated`: they were not evaluated); an opened one in `childCount`.
    ("cascade_completed", lambda details: details["summary"].get("skipped_archived", 0),
     {"nodesEvaluated": 1, "summary": {"compliant": 1}}),
    ("cascade_auto_triggered", lambda details: details["childCount"],
     {"reason": "parent_update", "childCount": 1}),
], ids=["cascade_completed", "cascade_auto_triggered"])
def test_a_record_by_asset_id_is_decided_per_asset(event_type, count_of, base):
    """The archived assets an entry names are each decided on their own (a deny on one asset's
    tag hides that asset and no other); the reader learns counts, never the ids."""
    key = (summaries.ARCHIVED_BY_DATABASE_KEY if event_type == "cascade_completed"
           else summaries.ARCHIVED_CHILDREN_KEY)
    stored = dict(base, **{key: {"db-a": ["a1", "a2", "a3"], "db-b": ["b1"]}})
    asked = []

    def visible(database_id, asset_id):
        asked.append((database_id, asset_id))
        return asset_id in ("a1", "a3", "b1")

    entry = _entry(stored, event_type=event_type)
    seen = _details(summaries.details_for_reader(entry, visible))
    assert seen[key] == {"db-a": 2, "db-b": 1}
    assert count_of(seen) == count_of(base) + 3
    assert seen.get("nodesEvaluated") == base.get("nodesEvaluated")
    assert sorted(asked) == [("db-a", "a1"), ("db-a", "a2"), ("db-a", "a3"), ("db-b", "b1")]
    assert "a2" not in json.dumps(seen) and "a1" not in json.dumps(seen)
    none = _details(summaries.details_for_reader(entry, lambda _db, _asset: False))
    assert key not in none and count_of(none) == count_of(base)


@pytest.mark.unit
def test_an_earlier_builds_entry_is_counted_by_the_parents_database():
    old = {"nodesEvaluated": 4, "summary": {"compliant": 1, "skipped_archived": 3}}
    asked = []

    def refuse(database_id, asset_id):
        asked.append(database_id)
        assert asset_id is None
        return False

    seen = _details(summaries.details_for_reader(_entry(old), refuse))
    assert seen == {"nodesEvaluated": 1, "summary": {"compliant": 1}}
    assert asked == [DB]
    assert _details(summaries.details_for_reader(_entry(old), lambda _db, _asset: True)) == old


@pytest.mark.unit
@pytest.mark.parametrize("details", [
    "not json", json.dumps(["a", "list"]), json.dumps({"summary": "text"}),
    json.dumps({"nodesEvaluated": 1, "summary": {"compliant": 1}}), None, "",
])
def test_an_entry_it_cannot_or_need_not_change_is_returned_as_it_is(details):
    entry = {"entryId": "e-1", "eventType": "cascade_completed", "databaseId": DB,
             "details": details}
    assert summaries.details_for_reader(entry, lambda _db, _asset: False) is entry


@pytest.mark.unit
def test_other_events_are_returned_as_they_are():
    entry = _entry({"summary": {"skipped_archived": 1}}, event_type="cascade_auto_triggered")
    assert summaries.details_for_reader(entry, lambda _db, _asset: False) is entry


@pytest.mark.unit
def test_details_stored_as_an_object_stay_an_object():
    entry = _entry({"nodesEvaluated": 1, "summary": {"compliant": 1},
                    "skippedArchivedByDatabase": {DB: 1}}, encode=False)
    seen = summaries.details_for_reader(entry, lambda _db, _asset: False)
    assert seen["details"] == {"nodesEvaluated": 1, "summary": {"compliant": 1}}
    assert entry["details"]["skippedArchivedByDatabase"] == {DB: 1}


@pytest.mark.unit
def test_an_unreadable_per_database_record_counts_nothing():
    entry = _entry({"nodesEvaluated": 1, "summary": {"compliant": 1},
                    "skippedArchivedByDatabase": {DB: "two", "db-b": -1, "db-c": True}})
    assert _details(summaries.details_for_reader(entry, lambda _db, _asset: True)) == {
        "nodesEvaluated": 1, "summary": {"compliant": 1}}


@pytest.mark.unit
def test_an_empty_summary_is_notified_as_none():
    with patch.object(notifications, "_get_asset_topic", return_value=("Parent", TOPIC)), \
            patch.object(notifications, "_publish") as publish:
        notifications.notify_cascade_completed(DB, PARENT, CASCADE_ID, {})
    message = publish.call_args.args[1]
    results = message.split("Results:", 1)[1].split("Best Regards", 1)[0]
    assert [line.strip() for line in results.strip().splitlines()] == ["none"]


@pytest.mark.unit
def test_the_opened_event_is_the_triggers():
    """The event whose child count the audit service reads per reader is the one the trigger
    writes when an evaluation opens a cascade."""
    source = pathlib.Path(trigger.__file__).read_text(encoding="utf-8")
    assert f'event_type="{summaries.CASCADE_OPENED_EVENT}"' in source


@pytest.mark.unit
def test_opened_details_record_the_archived_children_per_database():
    archived = {("db-a", "2"), ("db-b", "3"), ("db-b", "4")}
    children = [("db-a", "1"), ("db-a", "2"), ("db-b", "3"), ("db-b", "4"), ("db-c", "5")]
    assert summaries.opened_details(
        "parent_update", children, lambda db, asset: (db, asset) in archived) == {
        "reason": "parent_update", "childCount": 2,
        "archivedChildrenByDatabase": {"db-a": ["2"], "db-b": ["3", "4"]}}
    assert summaries.opened_details("parent_update", [("db-a", "1")], lambda *_: False) == {
        "reason": "parent_update", "childCount": 1}


@pytest.mark.unit
def test_a_reader_is_told_only_of_the_archived_children_it_may_see():
    stored = {"reason": "parent_update", "childCount": 1,
              "archivedChildrenByDatabase": {"db-a": 1, "db-b": 2}}
    entry = _entry(stored, event_type="cascade_auto_triggered")
    assert _details(summaries.details_for_reader(entry, lambda db, _asset: db == "db-b")) == {
        "reason": "parent_update", "childCount": 3, "archivedChildrenByDatabase": {"db-b": 2}}
    assert _details(summaries.details_for_reader(entry, lambda _db, _asset: True)) == {
        "reason": "parent_update", "childCount": 4,
        "archivedChildrenByDatabase": {"db-a": 1, "db-b": 2}}
    assert _details(summaries.details_for_reader(entry, lambda _db, _asset: False)) == {
        "reason": "parent_update", "childCount": 1}


@pytest.mark.unit
def test_an_opened_entry_without_archived_children_is_returned_as_it_is():
    """No per-database record: no archived child, or an entry written before the record existed
    (which cannot be told apart, and exists only on test deployments)."""
    entry = _entry({"reason": "parent_update", "childCount": 2}, event_type="cascade_auto_triggered")
    assert summaries.details_for_reader(entry, lambda _db, _asset: False) is entry


@pytest.mark.unit
def test_a_child_is_counted_once_and_a_link_naming_no_asset_is_not_archived():
    links = [{"toAssetDatabaseId": DB, "toAssetId": "x"}, {"toAssetDatabaseId": DB, "toAssetId": "x"},
             {"toAssetDatabaseId": DB}, {"toAssetDatabaseId": "GLOBAL", "toAssetId": "y"}]
    assert trigger._child_keys(links) == [(DB, "x"), (DB, ""), ("GLOBAL", "y")]
    with patch.object(trigger, "asset_is_archived", return_value=True) as archived:
        assert trigger._child_is_archived(DB, "") is False
        assert trigger._child_is_archived("GLOBAL", "y") is False
        archived.assert_not_called()
        assert trigger._child_is_archived(DB, "x") is True
