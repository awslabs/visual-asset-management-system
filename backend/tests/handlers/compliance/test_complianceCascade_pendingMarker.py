# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""At most one automatic cascade awaits approval per parent, however many evaluations of the parent
overlap. The trigger's pending check and its cascade write were separate steps, so evaluations of
one parent finishing a few milliseconds apart each opened a cascade. Now the trigger writes the
cascade in one transaction with a per-parent marker row, conditional on no marker, and the marker is
released whenever the cascade leaves `pending_approval`: approved (also when its executor then
cannot start), rejected, or aborted on its approval timeout from any of the paths that enforce it.
A marker whose release was lost is released by the parent's next open. Cascades created through the
API keep opening regardless and claim no marker.

Against a DynamoDB double built from the CDK table definition, whose transactions are serialized
as DynamoDB's are atomic."""

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws
from moto.dynamodb.models import DynamoDBBackend

from backend.tests.handlers.compliance._harness import (
    USER, body_of, claims_for, enforcer, rest_event,
)
from backend.tests.handlers.compliance.test_complianceCascade_approvalTimeout import (
    CASCADE_TABLE_DEFINITION,
)
from handlers.compliance import complianceCascadeService as svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger

SVC = "handlers.compliance.complianceCascadeService"
STORE = "handlers.compliance.complianceEvaluationStore"
NOTIFY = "handlers.compliance.complianceNotifications.notify_cascade_pending"

DB = "db1"
PARENT = "parent-asset"
CHILDREN = [{"toAssetDatabaseId": DB, "toAssetId": "child-1", "relationshipType": "parentChild"}]
MARKER_ID = f"pendingCascadeMarker:{DB}:{PARENT}"  # the parent's marker row, as the trigger keys it
OLD_ID = "5b1e2c3d-0000-4000-8000-0000000000aa"


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def _cascade(cascade_id=OLD_ID, state="pending_approval", timeout=None, actor="SYSTEM_USER"):
    row = {"cascadeId": cascade_id, "state": state, "triggeredByDatabaseId": DB,
           "triggeredByAssetId": PARENT, "createdAt": "2026-10-25T10:00:00+00:00",
           "actor": actor, "requireApproval": True, "nodes": "{}", "executionOrder": "[]",
           "approvalTimeoutAt": timeout or _iso(timedelta(hours=1))}
    return row


def _marker(holder=OLD_ID):
    return {"cascadeId": MARKER_ID, "markerFor": holder, "triggeredByDatabaseId": DB,
            "triggeredByAssetId": PARENT, "claimedAt": "2026-10-25T10:00:00+00:00"}


class Deployment:
    """The cascade table, the cascade service and the trigger's open, sharing one resource."""

    def __init__(self, resource, table):
        self.resource = resource
        self.table = table
        self.audit = MagicMock(name="write_audit")
        self.invoke = MagicMock(name="invoke")

    def rows(self):
        return {item["cascadeId"]: item for item in self.table.scan()["Items"]}

    def cascades(self):
        return [row for row in self.rows().values() if "state" in row]

    def pending(self):
        return [row for row in self.cascades() if row["state"] == "pending_approval"]

    def marker(self):
        return self.rows().get(MARKER_ID)

    def open(self):
        """One evaluation of the parent finishing: the trigger's cascade check."""
        trigger.check_and_trigger_cascade(DB, PARENT)

    def call(self, method, path, params=None, body=None):
        return svc.lambda_handler(rest_event(method, path, params, body=body), MagicMock())

    def events(self):
        return [call.kwargs["event_type"] for call in self.audit.call_args_list]


@contextmanager
def deployment(*rows, invoke_raises=False):
    lock = threading.Lock()
    original = DynamoDBBackend.transact_write_items

    def atomic(self, *args, **kwargs):
        with lock:
            return original(self, *args, **kwargs)

    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = resource.create_table(**CASCADE_TABLE_DEFINITION)
        for row in rows:
            table.put_item(Item=row)
        env = Deployment(resource, table)
        lambda_client = MagicMock(name="lambda_client")
        lambda_client.invoke = env.invoke
        if invoke_raises:
            env.invoke.side_effect = RuntimeError("invoke failed")
        with patch.object(DynamoDBBackend, "transact_write_items", atomic), \
                patch.object(trigger, "cascade_table", table), \
                patch.object(trigger, "asset_table", MagicMock(name="asset_table")), \
                patch(f"{STORE}.get_child_links", return_value=CHILDREN), \
                patch(f"{STORE}.write_audit", env.audit), \
                patch(f"{STORE}.all_database_ids", return_value=[DB]), \
                patch(NOTIFY), \
                patch(f"{SVC}.request_to_claims", claims_for(USER)), \
                patch(f"{SVC}.CasbinEnforcer", return_value=enforcer()), \
                patch(f"{SVC}.dynamodb", resource), \
                patch(f"{SVC}.cascade_table", table), \
                patch(f"{SVC}.lambda_client", lambda_client), \
                patch(f"{SVC}.cascade_executor_function_name", "vams-complianceCascadeExecutor"), \
                patch(f"{STORE}.get_asset_item", return_value={"assetId": PARENT}):
            yield env


def _pending_check_then_wait(barrier):
    """The trigger's pending check, held at `barrier` until every opener has made it: the
    interleaving in which each evaluation checks before any writes."""
    real = trigger.pending_cascade_exists

    def check(database_id, asset_id):
        found = real(database_id, asset_id)
        barrier.wait(timeout=10)
        return found
    return check


@pytest.mark.unit
class TestOneAutomaticCascadePerParent:

    @pytest.mark.parametrize("openers", [2, 4])
    def test_simultaneous_opens_of_one_parent_open_one_cascade(self, openers):
        barrier = threading.Barrier(openers)
        errors = []

        def run(env):
            try:
                env.open()
            except Exception as e:  # pragma: no cover - reported below
                errors.append(e)

        with deployment() as env, \
                patch.object(trigger, "pending_cascade_exists", _pending_check_then_wait(barrier)):
            threads = [threading.Thread(target=run, args=(env,)) for _ in range(openers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            pending = env.pending()
            marker = env.marker()
            events = env.events()
        assert errors == []
        assert len(pending) == 1
        assert marker["markerFor"] == pending[0]["cascadeId"]
        assert events == ["cascade_auto_triggered"]

    def test_an_open_that_checked_before_another_wrote_writes_nothing(self):
        """Sequential form of the same interleaving: the second open's pending check ran before the
        first open wrote, so only the marker can stop it."""
        with deployment() as env:
            env.open()
            with patch.object(trigger, "pending_cascade_exists", return_value=False):
                env.open()
            assert len(env.cascades()) == 1
            assert env.events() == ["cascade_auto_triggered"]

    def test_the_marker_names_the_cascade_and_stays_out_of_the_queue(self):
        with deployment() as env:
            env.open()
            marker = env.marker()
            (cascade,) = env.cascades()
            listed = body_of(env.call("GET", "/compliance/cascades"))["cascades"]
            refused = env.call("GET", f"/compliance/cascades/{MARKER_ID}", {"cascadeId": MARKER_ID})
        assert marker["markerFor"] == cascade["cascadeId"]
        assert "state" not in marker
        assert [c["cascadeId"] for c in listed] == [cascade["cascadeId"]]
        assert refused["statusCode"] == 400


@pytest.mark.unit
class TestTheMarkerIsReleasedWhenTheCascadeLeavesPending:

    def _reopened(self, env):
        """After the release, the parent's next evaluation opens a new cascade."""
        env.open()
        pending = env.pending()
        assert len(pending) == 1 and pending[0]["cascadeId"] != OLD_ID
        assert env.marker()["markerFor"] == pending[0]["cascadeId"]

    def test_approve(self):
        with deployment(_cascade(), _marker()) as env:
            response = env.call("POST", f"/compliance/cascades/{OLD_ID}/approve",
                                {"cascadeId": OLD_ID}, body={})
            assert response["statusCode"] == 202, response
            assert env.rows()[OLD_ID]["state"] == "executing"
            assert env.marker() is None
            self._reopened(env)

    def test_approve_whose_executor_cannot_start(self):
        with deployment(_cascade(), _marker(), invoke_raises=True) as env:
            response = env.call("POST", f"/compliance/cascades/{OLD_ID}/approve",
                                {"cascadeId": OLD_ID}, body={})
            assert response["statusCode"] == 400
            assert env.rows()[OLD_ID]["state"] == "aborted"
            assert env.marker() is None
            self._reopened(env)

    def test_reject(self):
        with deployment(_cascade(), _marker()) as env:
            response = env.call("POST", f"/compliance/cascades/{OLD_ID}/reject",
                                {"cascadeId": OLD_ID}, body={})
            assert response["statusCode"] == 200, response
            assert env.rows()[OLD_ID]["state"] == "aborted"
            assert env.marker() is None
            self._reopened(env)

    @pytest.mark.parametrize("method, suffix, body", [
        ("GET", "", None),                 # the queue listing
        ("GET", f"/{OLD_ID}", None),       # a get of the cascade
        ("POST", f"/{OLD_ID}/approve", {}),  # a refused approve
        ("POST", f"/{OLD_ID}/reject", {}),   # a refused reject
    ])
    def test_the_approval_timeout_on_each_path_that_enforces_it(self, method, suffix, body):
        expired = _cascade(timeout=_iso(timedelta(minutes=-1)))
        with deployment(expired, _marker()) as env:
            params = {"cascadeId": OLD_ID} if suffix else None
            env.call(method, f"/compliance/cascades{suffix}", params, body=body)
            assert env.rows()[OLD_ID]["abortReason"] == "approval timed out"
            assert env.marker() is None
            self._reopened(env)

    def test_the_approval_timeout_found_by_the_triggers_pending_check(self):
        expired = _cascade(timeout=_iso(timedelta(minutes=-1)))
        with deployment(expired, _marker()) as env:
            self._reopened(env)
            assert env.rows()[OLD_ID]["abortReason"] == "approval timed out"
            assert env.events() == ["cascade_timed_out", "cascade_auto_triggered"]


@pytest.mark.unit
class TestAMarkerWhoseReleaseWasLost:

    @pytest.mark.parametrize("state", ["executing", "completed", "aborted"])
    def test_one_naming_a_cascade_that_left_pending_is_released_by_the_next_open(self, state):
        with deployment(_cascade(state=state), _marker()) as env:
            env.open()
            pending = env.pending()
            assert len(pending) == 1
            assert env.marker()["markerFor"] == pending[0]["cascadeId"]

    def test_one_naming_a_cascade_that_is_gone_is_released_by_the_next_open(self):
        with deployment(_marker()) as env:
            env.open()
            assert len(env.pending()) == 1

    def test_one_naming_a_timed_out_cascade_aborts_it_and_opens_anew(self):
        """The marker check itself enforces the timeout (here the StateIndex check is passed by)."""
        expired = _cascade(timeout=_iso(timedelta(minutes=-1)))
        with deployment(expired, _marker()) as env, \
                patch.object(trigger, "pending_cascade_exists", return_value=False):
            env.open()
            assert env.rows()[OLD_ID]["abortReason"] == "approval timed out"
            assert len(env.pending()) == 1

    def test_one_naming_a_pending_cascade_within_its_timeout_blocks(self):
        with deployment(_cascade(), _marker()) as env, \
                patch.object(trigger, "pending_cascade_exists", return_value=False):
            env.open()
            assert [c["cascadeId"] for c in env.cascades()] == [OLD_ID]
            assert env.marker()["markerFor"] == OLD_ID
            assert env.events() == []


@pytest.mark.unit
class TestCascadesCreatedThroughTheApi:

    def test_they_open_regardless_and_leave_the_automatic_claim_alone(self):
        with deployment() as env:
            env.open()
            (automatic,) = env.cascades()
            created = [body_of(env.call("POST", "/compliance/cascades",
                                        body={"databaseId": DB, "assetId": PARENT}))["cascadeId"]
                       for _ in range(2)]
            assert len(env.pending()) == 3
            assert env.marker()["markerFor"] == automatic["cascadeId"]
            # Approving or rejecting one of them releases nothing.
            env.call("POST", f"/compliance/cascades/{created[0]}/approve",
                     {"cascadeId": created[0]}, body={})
            env.call("POST", f"/compliance/cascades/{created[1]}/reject",
                     {"cascadeId": created[1]}, body={})
            assert env.marker()["markerFor"] == automatic["cascadeId"]
            env.open()
            assert [c["cascadeId"] for c in env.pending()] == [automatic["cascadeId"]]
