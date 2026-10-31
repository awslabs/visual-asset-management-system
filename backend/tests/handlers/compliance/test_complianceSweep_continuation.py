# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A sweep of more assets than one call evaluates finishes (D69).

One call of a schema sweep (`POST /compliance/sweep/{schemaName}`) or of a database evaluation
(`POST /compliance/evaluate/{databaseId}`, `vamscli compliance sweep -d`) evaluates at most
MAX_SWEEP_ASSETS assets. Calls used to order the rows the same way every time and keep no cursor,
so once the pending rows were done every call evaluated the same first 200 and reported the same
`assetsRemaining`: the documented "repeat while assets remain" never ended past 200 assets.

Now a call that leaves assets unreached returns `NextToken`; the next call passes it back as
`startingToken` and evaluates only the assets not evaluated since the sweep started, so with 513
assets three calls evaluate each exactly once, `assetsRemaining` falls 313, 113, 0, and the last
response carries no token. A call without the token also advances (least recently evaluated first).

Run against DynamoDB doubles built from the CDK table definitions; the evaluation itself is a
stand-in that records its start on the row the way the evaluation's claim does (`lastEvaluatedAt`).
"""

import base64
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    USER, body_of, claims_for, enforcer, rest_event, schema_row, SYNC_RULES_SCHEMA_BODY,
)
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger

SCHEMA = "sweep-schema"
DB_A = "sweep-a"
DB_B = "sweep-b"
LONG_AGO = "2026-10-01T00:00:00+00:00"


@pytest.fixture
def tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (store, evaluate_svc, trigger))
        for active in patches:
            active.start()
        try:
            doubles["complianceSchemaStorage"].put_item(
                Item=schema_row(SCHEMA, body=SYNC_RULES_SCHEMA_BODY))
            for database_id in (DB_A, DB_B):
                doubles["databaseStorage"].put_item(Item={"databaseId": database_id})
            yield doubles
        finally:
            for active in reversed(patches):
                active.stop()


def _seed(tables, database_id, count, pending=0, prefix="x"):
    """`count` records bound to SCHEMA, all evaluated long ago, the first `pending` awaiting
    evaluation; each asset exists."""
    with tables["complianceAssetStateStorage"].batch_writer() as batch:
        for index in range(count):
            asset_id = f"{prefix}{index:04d}"
            row = {"databaseId": database_id, "assetId": asset_id, "schemaName": SCHEMA,
                   "schemaSource": "database",
                   "complianceState": "pending_evaluation" if index < pending else "compliant"}
            if index >= pending:
                row.update(lastEvaluatedAt=LONG_AGO, lastEvaluationStatus="completed")
            batch.put_item(Item=row)
    with tables["assetStorage"].batch_writer() as batch:
        for index in range(count):
            batch.put_item(Item={"databaseId": database_id, "assetId": f"{prefix}{index:04d}"})


class Evaluations:
    """A `store.run_evaluation` stand-in: records each call and marks the row the way the
    evaluation's claim does, `lastEvaluatedAt` = the evaluation's start."""

    def __init__(self, tables):
        self.table = tables["complianceAssetStateStorage"]
        self.calls = []

    def __call__(self, database_id, asset_id, schema_name, actor=None):
        self.calls.append((database_id, asset_id))
        self.table.update_item(
            Key={"databaseId": database_id, "assetId": asset_id},
            UpdateExpression="SET lastEvaluatedAt = :at, complianceState = :state",
            ExpressionAttributeValues={":at": datetime.now(timezone.utc).isoformat(),
                                       ":state": "compliant"})
        return {"evaluationId": f"eval-{asset_id}", "verdict": "compliant",
                "complianceState": "compliant"}


def _call(evaluations, event):
    with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
            patch.object(evaluate_svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()), \
            patch.object(store, "run_evaluation", evaluations), \
            patch.object(store, "register_unrecorded_assets", return_value=0):
        response = evaluate_svc.lambda_handler(event, MagicMock())
    assert response["statusCode"] == 200, response
    return body_of(response)


def _database_sweep(token=None):
    body = {"startingToken": token} if token else {}
    return rest_event("POST", f"/compliance/evaluate/{DB_A}", {"databaseId": DB_A}, body=body)


def _schema_sweep(token=None):
    body = {"startingToken": token} if token else {}
    return rest_event("POST", f"/compliance/sweep/{SCHEMA}", {"schemaName": SCHEMA}, body=body)


def _walk(evaluations, make_event, limit=10):
    """Follow NextToken until a response carries none; `(responses, calls made)`."""
    responses = []
    token = None
    for _ in range(limit):
        body = _call(evaluations, make_event(token))
        responses.append(body)
        token = body.get("NextToken")
        if not token:
            return responses
    raise AssertionError(f"the sweep did not finish in {limit} calls: "
                         f"{[r['assetsRemaining'] for r in responses]}")


@pytest.mark.unit
class TestASweepOfMoreThanOneCallFinishes:

    def test_a_database_evaluation_of_513_assets_evaluates_each_once_in_three_calls(self, tables):
        _seed(tables, DB_A, 513, pending=20)
        evaluations = Evaluations(tables)
        responses = _walk(evaluations, _database_sweep)
        assert [r["assetsRemaining"] for r in responses] == [313, 113, 0]
        assert [len(r["assetsTriggered"]) for r in responses] == [200, 200, 113]
        counts = Counter(evaluations.calls)
        assert len(counts) == 513 and set(counts.values()) == {1}
        assert "NextToken" not in responses[-1]
        # The pending rows go first.
        assert {asset_id for _, asset_id in evaluations.calls[:20]} == {
            f"x{index:04d}" for index in range(20)}

    def test_a_schema_sweep_over_two_databases_evaluates_each_once(self, tables):
        _seed(tables, DB_A, 300)
        _seed(tables, DB_B, 213, prefix="y")
        evaluations = Evaluations(tables)
        responses = _walk(evaluations, _schema_sweep)
        assert [r["assetsRemaining"] for r in responses] == [313, 113, 0]
        counts = Counter(evaluations.calls)
        assert len(counts) == 513 and set(counts.values()) == {1}
        assert {database_id for database_id, _ in counts} == {DB_A, DB_B}

    def test_calls_without_the_token_still_advance(self, tables):
        # A client that does not pass the token back (an older one) does not repeat the same
        # assets: the least recently evaluated go first.
        _seed(tables, DB_A, 450)
        evaluations = Evaluations(tables)
        for _ in range(3):
            _call(evaluations, _database_sweep())
        counts = Counter(evaluations.calls)
        assert len(counts) == 450
        assert max(counts.values()) <= 2 and sum(counts.values()) == 600

    def test_an_asset_evaluated_meanwhile_is_not_evaluated_again(self, tables):
        _seed(tables, DB_A, 250)
        evaluations = Evaluations(tables)
        first = _call(evaluations, _database_sweep())
        assert first["assetsRemaining"] == 50
        reached = {asset_id for _, asset_id in evaluations.calls}
        # A trigger evaluation of one of the 50 unreached assets runs between the calls.
        unreached = sorted({f"x{index:04d}" for index in range(250)} - reached)
        tables["complianceAssetStateStorage"].update_item(
            Key={"databaseId": DB_A, "assetId": unreached[0]},
            UpdateExpression="SET lastEvaluatedAt = :at",
            ExpressionAttributeValues={":at": datetime.now(timezone.utc).isoformat()})
        second = _call(evaluations, _database_sweep(first["NextToken"]))
        assert second["assetsRemaining"] == 0 and "NextToken" not in second
        assert len(second["assetsTriggered"]) == 49
        assert unreached[0] not in {entry["assetId"] for entry in second["assetsTriggered"]}

    def test_a_sweep_within_one_call_returns_no_token(self, tables):
        _seed(tables, DB_A, 5)
        body = _call(Evaluations(tables), _database_sweep())
        assert body["assetsRemaining"] == 0 and "NextToken" not in body


@pytest.mark.unit
class TestTheSweepToken:

    @pytest.mark.parametrize("token", [
        "not base64!",
        base64.b64encode(b"[]").decode(),
        base64.b64encode(json.dumps({"sweepStartedAt": "yesterday"}).encode()).decode(),
        base64.b64encode(json.dumps({"sweepStartedAt": (
            datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}).encode()).decode(),
    ])
    def test_a_malformed_or_future_token_is_refused(self, tables, token):
        for event in (_database_sweep(token), _schema_sweep(token)):
            with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
                    patch.object(evaluate_svc, "CasbinEnforcer", return_value=enforcer()):
                response = evaluate_svc.lambda_handler(event, MagicMock())
            assert response["statusCode"] == 400
            assert body_of(response)["message"] == "Invalid pagination token"
