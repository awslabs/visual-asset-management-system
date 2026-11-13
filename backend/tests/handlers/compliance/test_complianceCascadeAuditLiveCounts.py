# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A finished cascade's audit entry counts each live descendant for each reader as the cascade read
does (D101 follow-up, D104).

The `cascade_completed` entry counted its live descendants per database: a database the reader may
not read went to `unauthorizedNodes`, but within a database the reader may read, a descendant a
field rule hides (a deny on one of its tags) was still counted. An entry written now also records
the live descendants by id, per status per database (`liveNodesByDatabase`), and each one is decided
for the reader as the cascade read decides it: one the reader may see is counted by status, one the
reader may not read (its database's compliance, or the asset itself) is counted in
`unauthorizedNodes`, without its id, name or verdict, and one archived since that the reader may
not see is left out. The ids are never returned, the notification keeps counts only, and an entry
written before keeps its counts.
"""

import json
from collections import Counter

import pytest

from backend.tests.handlers.compliance._harness import body_of
from backend.tests.handlers.compliance.test_complianceLiveAssetAccess import (  # noqa: F401
    ASSETS, COMPLETED_NODES, DB1, DB2, ROLE_SETS, _Oracle, _in_world,
    _real_permission_constants, _role_set, role_enforcer,
)
from common.compliance import cascadeAuditSummary as summaries
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc

PARENT = "lv-parent"
CASCADE_ID = "7a0c0000-0000-4000-8000-0000000d1777"
RESULTS = [{"node": f"{ASSETS[asset_id][0]}:{asset_id}", "status": status}
           for asset_id, status in COMPLETED_NODES.items()]


def _entry_details(response):
    """The `cascade_completed` entry's details in an audit response (None when refused or absent)."""
    if response["statusCode"] != 200:
        return None
    for entry in body_of(response)["entries"]:
        if entry.get("eventType") == "cascade_completed":
            details = entry["details"]
            return json.loads(details) if isinstance(details, str) else details
    return None


def _observe(call, list_assets, doubles):
    # What the asset listing shows the reader: its live assets, and its archived ones (the
    # oracle's input, as in the D101 matrix).
    listing = {}
    for database_id in (DB1, DB2):
        listing.update({asset_id: True for asset_id in list_assets(database_id, False)})
        listing.update({asset_id: True for asset_id in list_assets(database_id, True)
                        if ASSETS.get(asset_id, (None, False))[1]})
    out = {"listing": listing}
    doubles["complianceAuditStorage"].put_item(Item=build_audit_item(
        DB1, PARENT, "cascade_completed", "SYSTEM_USER",
        summaries.completion_details(RESULTS, DB1), cascade_id=CASCADE_ID,
        timestamp="2026-10-05T00:00:00+00:00", asset_name=f"Name of {PARENT}"))
    out["asset trail"] = _entry_details(call(
        audit_svc, "GET", f"/compliance/audit/{DB1}/{PARENT}",
        {"databaseId": DB1, "assetId": PARENT}))
    out["listing by event type"] = _entry_details(call(
        audit_svc, "GET", "/compliance/audit", query={"eventType": "cascade_completed"}))
    out["listing by database"] = _entry_details(call(
        audit_svc, "GET", "/compliance/audit", query={"databaseId": DB1}))
    out["whole trail"] = _entry_details(call(audit_svc, "GET", "/compliance/audit"))
    return out


@pytest.fixture(scope="module")
def observations():
    return {}


def _observed(name, role_enforcer, cache):  # noqa: F811
    if name not in cache:
        roles, constraints = _role_set(name)
        enforcer = role_enforcer(roles, constraints)
        cache[name] = (_in_world(enforcer, roles, _observe), _Oracle(enforcer, constraints))
    return cache[name]


def _expected(oracle, listing):
    """What the reader is given: each live descendant it may see (the D101 oracle), in its status,
    and every other live descendant (a database or an asset the reader may not read) as
    `unauthorizedNodes`, as the cascade read counts them; the archived one under `skipped_archived`
    where the reader may see it."""
    summary, unauthorized = Counter(), 0
    for asset_id, status in COMPLETED_NODES.items():
        _database_id, archived, _tags = ASSETS[asset_id]
        if archived:
            if oracle.visible(asset_id, listing):
                summary[status] += 1
            continue
        if oracle.visible(asset_id, listing):
            summary[status] += 1
        else:
            unauthorized += 1
    evaluated = sum(count for status, count in summary.items() if status != "skipped_archived")
    return dict(summary), evaluated, unauthorized


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_the_entry_counts_the_descendants_the_reader_may_see(name, role_enforcer, observations):  # noqa: F811
    """On every path that returns the entry: `summary` and `nodesEvaluated` count only the live
    descendants the reader may see (as the asset listing shows them), every other live descendant
    is in `unauthorizedNodes`, and no id is returned."""
    found, oracle = _observed(name, role_enforcer, observations)
    listing = found["listing"]
    summary, evaluated, unauthorized = _expected(oracle, listing)
    for path in ("asset trail", "listing by event type", "listing by database", "whole trail"):
        details = found[path]
        if not oracle.visible(PARENT, listing):
            assert details is None, (name, path)
            continue
        assert details is not None, (name, path)
        assert details["summary"] == summary, (name, path, details)
        assert details["nodesEvaluated"] == evaluated, (name, path, details)
        assert details["unauthorizedNodes"] == unauthorized, (name, path, details)
        assert "liveNodesByDatabase" not in details, (name, path)
        text = json.dumps(details)
        for asset_id in COMPLETED_NODES:
            assert asset_id not in text, (name, path, asset_id)


@pytest.mark.unit
def test_a_tag_deny_reader_counts_the_tagged_descendants_as_not_accessible(role_enforcer, observations):  # noqa: F811
    """The round-16b reader reads both databases: before the record by id, the entry counted all
    four live descendants (two compliant, two quarantined) by status; the two tagged ones (the
    quarantined) are left out of the counts by status and counted as not accessible, as the
    cascade read counts them."""
    found, _oracle = _observed("round-16b", role_enforcer, observations)
    details = found["asset trail"]
    assert details["summary"]["compliant"] == 2 and "quarantined" not in details["summary"]
    assert details["nodesEvaluated"] == 2 and details["unauthorizedNodes"] == 2


# ---------------------------------------------------------------------------------------------
# The entry as written, and as read without the record
# ---------------------------------------------------------------------------------------------
def _entry(details, database_id="db-a"):
    return {"eventType": "cascade_completed", "databaseId": database_id, "assetId": "p",
            "details": json.dumps(details)}


def _read(details, live_outcome, database_readable=lambda _db: True):
    out = summaries.details_for_reader(_entry(details), lambda _db, _asset: True,
                                       database_readable, live_outcome)
    return json.loads(out["details"])


def _outcomes(unauthorized=(), hidden=()):
    """`live_outcome` showing every descendant but those named `(databaseId, assetId)`."""
    def live_outcome(database_id, asset_id):
        if (database_id, asset_id) in unauthorized:
            return summaries.LIVE_UNAUTHORIZED
        if (database_id, asset_id) in hidden:
            return summaries.LIVE_HIDDEN
        return summaries.LIVE_SHOWN
    return live_outcome


RESULTS_AB = [
    {"node": "db-a:1", "status": "compliant"},
    {"node": "db-a:2", "status": "quarantined"},
    {"node": "db-a:3", "status": "skipped_not_authorized"},
    {"node": "db-b:4", "status": "compliant"},
    {"node": "db-b:5", "status": "non_compliant"},
    {"node": "db-a:6", "status": "skipped_archived"},
]


@pytest.mark.unit
def test_the_entry_records_the_live_descendants_by_id_and_keeps_its_counts():
    assert summaries.LIVE_BY_DATABASE_KEY == "liveNodesByDatabase"
    details = summaries.completion_details(RESULTS_AB, "db-a")
    assert details[summaries.LIVE_BY_DATABASE_KEY] == {
        "db-a": {"compliant": ["1"], "quarantined": ["2"], "skipped_not_authorized": ["3"]},
        "db-b": {"compliant": ["4"], "non_compliant": ["5"]}}
    # The counts the notification carries are those of an entry without the record.
    assert details["summary"] == {"compliant": 1, "quarantined": 1, "skipped_not_authorized": 1}
    assert details["nodesEvaluated"] == 2
    assert summaries.other_database_node_count(details) == 2


@pytest.mark.unit
def test_a_reader_is_counted_the_descendants_it_may_see():
    details = summaries.completion_details(RESULTS_AB, "db-a")
    # A descendant the reader may not read, in the parent's database or another, is counted as not
    # accessible, without its status.
    seen = _read(details, _outcomes(unauthorized={("db-a", "2"), ("db-b", "5")}))
    assert seen["summary"] == {"compliant": 2, "skipped_not_authorized": 1,
                               "skipped_archived": 1}
    assert seen["nodesEvaluated"] == 2
    assert seen["unauthorizedNodes"] == 2
    assert summaries.LIVE_BY_DATABASE_KEY not in seen
    assert summaries.OTHER_DATABASES_KEY not in seen
    # A database the reader may not read: every node of it is counted as not accessible.
    seen = _read(details, _outcomes(unauthorized={("db-a", "2"), ("db-b", "4"), ("db-b", "5")}))
    assert seen["summary"] == {"compliant": 1, "skipped_not_authorized": 1, "skipped_archived": 1}
    assert seen["nodesEvaluated"] == 1
    assert seen["unauthorizedNodes"] == 3
    # A descendant archived since the cascade reached it, that the reader may not see, is left out
    # and not counted, as the cascade read leaves it out.
    seen = _read(details, _outcomes(hidden={("db-a", "1")}))
    assert seen["summary"] == {"quarantined": 1, "skipped_not_authorized": 1, "compliant": 1,
                               "non_compliant": 1, "skipped_archived": 1}
    assert seen["nodesEvaluated"] == 3
    assert seen["unauthorizedNodes"] == 0


@pytest.mark.unit
def test_unauthorized_nodes_is_given_when_another_database_was_reached_or_a_node_is_counted():
    """As an entry counted per database gives it: on an entry whose cascade reached another
    database, and on one that counts a descendant the reader may not read."""
    one_database = summaries.completion_details(RESULTS_AB[:3], "db-a")
    assert "unauthorizedNodes" not in _read(one_database, _outcomes())
    assert "unauthorizedNodes" not in _read(one_database, _outcomes(hidden={("db-a", "1")}))
    seen = _read(one_database, _outcomes(unauthorized={("db-a", "1")}))
    assert seen["unauthorizedNodes"] == 1 and seen["summary"] == {
        "quarantined": 1, "skipped_not_authorized": 1}
    assert _read(summaries.completion_details(RESULTS_AB, "db-a"),
                 _outcomes())["unauthorizedNodes"] == 0


@pytest.mark.unit
def test_without_a_live_decision_the_entry_is_counted_per_database_and_names_no_id():
    details = summaries.completion_details(RESULTS_AB, "db-a")
    without = dict(details)
    without.pop(summaries.LIVE_BY_DATABASE_KEY)
    as_before = _read(without, None)
    assert _read(details, None) == as_before
    assert as_before["summary"]["compliant"] == 2 and as_before["unauthorizedNodes"] == 0


@pytest.mark.unit
def test_an_entry_written_before_the_record_keeps_its_counts():
    """An entry with no `liveNodesByDatabase` is read as it was: a descendant a field rule hides is
    still counted in a database the reader may read."""
    details = summaries.completion_details(RESULTS_AB, "db-a")
    details.pop(summaries.LIVE_BY_DATABASE_KEY)
    seen = _read(details, _outcomes(unauthorized={("db-a", "1"), ("db-a", "2")}))
    assert seen["summary"] == {"compliant": 2, "quarantined": 1, "skipped_not_authorized": 1,
                               "non_compliant": 1, "skipped_archived": 1}
    assert seen["nodesEvaluated"] == 4


def _uuid_ids(count):
    """Asset ids as VAMS creates them: `x` and a UUID, 37 characters."""
    return [f"x{index:08d}-0000-4000-8000-{index:012d}" for index in range(count)]


@pytest.mark.unit
def test_a_2000_node_cascade_stays_well_inside_the_item_limit(capsys):
    """2,000 live descendants with ids as VAMS creates them, over three databases and four statuses:
    the stored entry (JSON-encoded `details`) is printed and bounded; at the cascade's node bound
    (`MAX_CASCADE_NODES`), ids of the longest form an asset id may take (256 characters) fit too.
    Ids that would not fit leave the entry with its counts only."""
    statuses = ("compliant", "non_compliant", "quarantined", "skipped_not_authorized")
    results = [{"node": f"db-{index % 3}:{asset_id}", "status": statuses[index % 4]}
               for index, asset_id in enumerate(_uuid_ids(2000))]
    stored = json.dumps(summaries.completion_details(results, "db-0")).encode("utf-8")
    from handlers.compliance.complianceEvaluationStore import MAX_CASCADE_NODES
    longest = [{"node": f"db-{index % 3}:{'a' * 250}{index:06d}", "status": "compliant"}
               for index in range(MAX_CASCADE_NODES)]
    longest_stored = json.dumps(summaries.completion_details(longest, "db-0")).encode("utf-8")
    with capsys.disabled():
        print(f"\nD101 entry size: 2,000 nodes, 37-char ids: {len(stored)} bytes; "
              f"{MAX_CASCADE_NODES} nodes, 256-char ids: {len(longest_stored)} bytes")
    assert len(stored) < 100_000
    assert summaries.LIVE_BY_DATABASE_KEY in json.loads(stored)
    assert summaries.LIVE_BY_DATABASE_KEY in json.loads(longest_stored)
    assert len(longest_stored) <= summaries.MAX_LIVE_IDS_BYTES + 10_000
    too_long = [{"node": f"db-0:{'a' * 250}{index:06d}", "status": "compliant"}
                for index in range(2000)]
    over = summaries.completion_details(too_long, "db-0")
    assert summaries.LIVE_BY_DATABASE_KEY not in over
    assert over["summary"] == {"compliant": 2000}
    assert len(json.dumps(over).encode("utf-8")) < 1_000
