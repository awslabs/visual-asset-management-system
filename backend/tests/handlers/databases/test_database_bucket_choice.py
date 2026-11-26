# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The bucket permission type on the bucket routes (issue #1978).

- `GET /buckets` lists a registration only when the caller may GET it (Tier 2 `bucket`), and each item
  carries `source` and `status`.
- Database create checks GET on the chosen bucket; database update checks it when the database moves
  to another bucket. An unknown, retired or unreadable bucket id gets one identical answer, so the
  answer does not tell them apart. The refused check is a `CasbinEnforcer.enforce` call on the bucket
  object, which writes the authorization audit record (tests/handlers/authz/test_bucket_object_type.py
  runs the real enforcer on the same objects).
- Bucket sync's own database creation (`create_database()` called directly) is not checked.

The enforcer here answers from a role description (which object types and bucket names it may read)
and records every Tier 2 check; only the table reads are stubbed.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.databases import createDatabase as create_handler
from backend.backend.handlers.databases import databaseService as svc
from backend.tests.handlers.databases.test_databaseService_targeted_update import (  # noqa: F401 - fixtures
    DATABASE_ID,
    FakeDatabaseTable,
    _stored_database,
    wire,
)

USER_ID = "team-a-user"
TEAM_A = "aa11bb22-c092-475f-978a-d39e5d5a2657"
TEAM_B = "bb22cc33-c092-475f-978a-d39e5d5a2657"
RETIRED = "cc33dd44-c092-475f-978a-d39e5d5a2657"
UNKNOWN = "dd44ee55-c092-475f-978a-d39e5d5a2657"
REFUSAL = "Bucket ID not found or not available for new databases"

ROWS = {
    TEAM_A: {"bucketId": TEAM_A, "bucketName": "team-a-assets", "baseAssetsPrefix": "/",
             "bucketRegion": "us-east-1", "isDefault": True},
    TEAM_B: {"bucketId": TEAM_B, "bucketName": "team-b-assets", "baseAssetsPrefix": "data/",
             "bucketRegion": "us-west-2"},
    RETIRED: {"bucketId": RETIRED, "bucketName": "team-a-old", "baseAssetsPrefix": "/", "retired": True},
}

# Role descriptions: the object types the role holds, and for `bucket` the bucket-name prefix it reads.
ALL_ROUTES = ("api",)
ALL_DATABASES = ("database",)
TEAM_A_BUCKETS = ("bucket:team-a-",)
ALL_BUCKETS = ("bucket:",)

CLAIMS = {"tokens": [USER_ID], "roles": ["role"], "mfaEnabled": True}


class _Enforcer:
    def __init__(self, grants, checks):
        self.grants = grants
        self.checks = checks

    def enforceAPI(self, event, *args):
        return "api" in self.grants

    def enforce(self, obj, act):
        object_type = obj.get("object__type")
        if object_type == "bucket":
            allowed = act == "GET" and any(
                g.startswith("bucket:") and str(obj.get("bucketName", "")).startswith(g[len("bucket:"):])
                for g in self.grants)
            self.checks.append((dict(obj), act, allowed))
            return allowed
        return object_type in self.grants


@pytest.fixture
def role(monkeypatch):
    """Give the user the grants passed; returns the list of Tier 2 bucket checks made."""
    checks = []

    def give(*grants):
        flat = tuple(g for group in grants for g in group)
        factory = lambda claims_and_roles: _Enforcer(flat, checks)  # noqa: E731
        monkeypatch.setattr(svc, "CasbinEnforcer", factory)
        monkeypatch.setattr(create_handler, "CasbinEnforcer", factory)
        return checks

    return give


def _audited_buckets(checks):
    """The bucket ids whose Tier 2 check was refused (each refusal writes the audit record)."""
    return [obj["bucketId"] for obj, act, allowed in checks if not allowed]


# ------------------------------------------------------------------ GET /buckets

def _scan(rows):
    def typed(value):
        return {"BOOL": value} if isinstance(value, bool) else {"S": str(value)}
    return {"Items": [{k: typed(v) for k, v in row.items()} for row in rows]}


def _list(rows, claims=CLAIMS):
    with patch("backend.backend.handlers.databases.databaseService.dbClient") as db:
        db.scan.return_value = _scan(rows)
        return svc.get_buckets({}, claims).Items


@pytest.mark.unit
class TestBucketListing:
    def test_a_role_limited_to_team_a_sees_only_team_a(self, role):
        audit = role(TEAM_A_BUCKETS)
        items = _list([ROWS[TEAM_A], ROWS[TEAM_B]])
        assert [i.bucketId for i in items] == [TEAM_A]
        assert _audited_buckets(audit) == [TEAM_B]

    def test_a_role_without_a_bucket_constraint_sees_none(self, role):
        audit = role(ALL_DATABASES)
        assert _list([ROWS[TEAM_A], ROWS[TEAM_B]]) == []
        assert sorted(_audited_buckets(audit)) == sorted([TEAM_A, TEAM_B])

    def test_a_role_reading_every_bucket_sees_every_active_row(self, role):
        role(ALL_BUCKETS)
        assert [i.bucketId for i in _list([ROWS[TEAM_A], ROWS[TEAM_B], ROWS[RETIRED]])] == [TEAM_A, TEAM_B]

    def test_no_identity_lists_nothing(self, role):
        role(ALL_BUCKETS)
        assert _list([ROWS[TEAM_A]], claims={"tokens": []}) == []
        assert _list([ROWS[TEAM_A]], claims=None) == []

    def test_items_carry_source_and_status(self, role):
        role(ALL_BUCKETS)
        runtime = {"bucketId": UNKNOWN, "bucketName": "rt", "baseAssetsPrefix": "/", "source": "runtime",
                   "status": "disabled"}
        items = {i.bucketId: i.dict() for i in _list([ROWS[TEAM_A], runtime])}
        assert (items[TEAM_A]["source"], items[TEAM_A]["status"]) == ("static", "active")
        assert (items[UNKNOWN]["source"], items[UNKNOWN]["status"]) == ("runtime", "disabled")

    def test_the_next_token_survives_a_filtered_page(self, role):
        role(TEAM_A_BUCKETS)
        with patch("backend.backend.handlers.databases.databaseService.dbClient") as db:
            response = _scan([ROWS[TEAM_B]])
            response["LastEvaluatedKey"] = {"bucketId": {"S": TEAM_B}}
            db.scan.return_value = response
            result = svc.get_buckets({}, CLAIMS)
        assert result.Items == [] and result.NextToken


# ------------------------------------------------------------------ POST /database

def _create_event(bucket_id):
    body = {"databaseId": "team-a-db", "description": "team a database", "defaultBucketId": bucket_id}
    return {"body": json.dumps(body), "requestContext": {"http": {"method": "POST", "path": "/database"}}}


@pytest.fixture
def create_tables(monkeypatch):
    database_table = MagicMock()
    buckets_table = MagicMock()
    buckets_table.query.side_effect = lambda **kwargs: {
        "Items": [ROWS[v] for v in [kwargs["KeyConditionExpression"].get_expression()["values"][1]]
                  if v in ROWS]}
    resource = MagicMock()
    resource.Table.side_effect = lambda name: buckets_table if name == create_handler.s3_asset_buckets_table \
        else database_table
    monkeypatch.setattr(create_handler, "dynamodb", resource)
    monkeypatch.setattr(create_handler, "request_to_claims", lambda event: CLAIMS)
    monkeypatch.setattr(create_handler, "log_action", MagicMock())
    return database_table


@pytest.mark.unit
class TestDatabaseCreate:
    def test_a_readable_bucket_is_accepted(self, role, create_tables):
        role(ALL_ROUTES, ALL_DATABASES, TEAM_A_BUCKETS)
        response = create_handler.lambda_handler(_create_event(TEAM_A), None)
        assert response["statusCode"] == 200
        create_tables.put_item.assert_called_once()
        assert create_tables.put_item.call_args.kwargs["Item"]["defaultBucketId"] == TEAM_A

    @pytest.mark.parametrize("bucket_id", [TEAM_B, RETIRED, UNKNOWN])
    def test_an_unreadable_retired_or_unknown_bucket_gets_one_answer(self, role, create_tables, bucket_id):
        role(ALL_ROUTES, ALL_DATABASES, TEAM_A_BUCKETS)
        response = create_handler.lambda_handler(_create_event(bucket_id), None)
        assert response["statusCode"] == 400
        assert json.loads(response["body"]) == {"message": REFUSAL}
        create_tables.put_item.assert_not_called()

    def test_the_answers_are_identical(self, role, create_tables):
        role(ALL_ROUTES, ALL_DATABASES, TEAM_A_BUCKETS)
        answers = {json.dumps(create_handler.lambda_handler(_create_event(b), None), sort_keys=True,
                              default=str) for b in (TEAM_B, UNKNOWN)}
        assert len(answers) == 1

    def test_a_refused_bucket_writes_the_authorization_audit_record(self, role, create_tables):
        audit = role(ALL_ROUTES, ALL_DATABASES, TEAM_A_BUCKETS)
        create_handler.lambda_handler(_create_event(TEAM_B), None)
        assert _audited_buckets(audit) == [TEAM_B]

    def test_a_role_without_a_bucket_constraint_cannot_choose_any_bucket(self, role, create_tables):
        role(ALL_ROUTES, ALL_DATABASES)
        assert create_handler.lambda_handler(_create_event(TEAM_A), None)["statusCode"] == 400
        create_tables.put_item.assert_not_called()

    def test_bucket_syncs_own_creation_is_not_checked(self, role, create_tables):
        role()
        create_handler.create_database(create_handler.CreateDatabaseRequestModel(
            databaseId="auto-db", description="auto database", defaultBucketId=TEAM_B))
        create_tables.put_item.assert_called_once()


# ------------------------------------------------------------------ PUT /database/{databaseId}

def _update(wire_fn, bucket_id, stored_bucket=TEAM_A, **fields):  # noqa: F811
    table = FakeDatabaseTable(_stored_database(defaultBucketId=stored_bucket))
    wire_fn(table, bucket_items=[ROWS[bucket_id]] if bucket_id in ROWS else [])
    return table, lambda: svc.update_database(DATABASE_ID, dict(fields, defaultBucketId=bucket_id), CLAIMS)


@pytest.mark.unit
class TestDatabaseUpdate:
    def test_a_move_onto_a_readable_bucket_is_accepted(self, role, wire):  # noqa: F811
        role(ALL_DATABASES, ALL_BUCKETS)
        table, run = _update(wire, TEAM_B)
        run()
        assert table.stored()["defaultBucketId"] == TEAM_B

    @pytest.mark.parametrize("bucket_id", [TEAM_B, RETIRED, UNKNOWN])
    def test_a_move_onto_an_unreadable_retired_or_unknown_bucket_gets_one_answer(self, role, wire,  # noqa: F811
                                                                                  bucket_id):
        audit = role(ALL_DATABASES, TEAM_A_BUCKETS)
        table, run = _update(wire, bucket_id)
        with pytest.raises(svc.VAMSGeneralErrorResponse) as raised:
            run()
        # The update handler answers str(error): "VAMS General Error: " + the sentence, for all three.
        assert str(raised.value) == "VAMS General Error: " + REFUSAL
        assert table.update_item_calls == []
        assert table.stored()["defaultBucketId"] == TEAM_A
        if bucket_id == TEAM_B:
            assert _audited_buckets(audit) == [TEAM_B]

    def test_an_edit_that_keeps_the_bucket_is_not_checked_on_it(self, role, wire):  # noqa: F811
        # The edit form sends the current bucket with every save; a role that may not read it still
        # edits the database's other fields.
        audit = role(ALL_DATABASES)
        table, run = _update(wire, TEAM_B, stored_bucket=TEAM_B, description="after")
        run()
        assert table.stored()["description"] == "after"
        assert _audited_buckets(audit) == []
