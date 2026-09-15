# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-143: the four single-resource Tier-2 sites in metadataSchemaService must fail closed.

Each site wrapped its `enforce()` in `if len(claims_and_roles["tokens"]) > 0:` with no `else` that
denies -- the shape backend/CLAUDE.md Rule 4 forbids by name -- so an empty token list skipped
authorization entirely and fell through to the schema create / update / delete write and to the
single-schema response. The sites are `create_metadata_schema`, `update_metadata_schema`,
`delete_metadata_schema`, and the single-schema branch of `handle_get_request`.

The three mutating functions also returned `authorization_error()` -- a plain `TypedDict` -- to
callers that immediately did `result.dict()`, so every denial raised `AttributeError` and surfaced
as a 500. `TestDenialReachesTheClient` pins the status code the caller actually receives.

S2-BACKEND-145 also applies here: `create_metadata_schema` ran its database-existence check ahead of
the gate, so the 400/403 split told an unauthorized caller which databases exist. See
`TestCreateAuthorizesBeforeDisclosingDatabaseExistence`. The remaining pre-gate lookups in this
module (`update_metadata_schema`, `delete_metadata_schema`, and the single-schema GET) cannot be
reordered: a metadataSchema is authorized on databaseId + metadataSchemaName +
metadataSchemaEntityType, and only the stored row carries the latter two, so a pre-lookup gate would
evaluate them as the empty defaults `PERMISSION_CONSTRAINT_FIELDS` seeds and deny a caller whose role
is scoped by schema name.

## Why these tests assert who was consulted, not only the verdict

The root conftest replaces `handlers.authz` with a stand-in whose `CasbinEnforcer` is a MagicMock,
and a MagicMock's `enforce()` returns a truthy Mock. A test written against the verdict alone can
therefore pass for the wrong reason. `_EnforcerSpy` below records every construction and every
`enforce()` call, so the empty-token tests assert the property that actually matters -- Casbin was
never consulted and nothing was written -- while the authorized tests assert the document and action
handed to Casbin. Every denial assertion is paired with the permitted case that must still work,
because "denied" alone is satisfied by a handler that denies everything.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.metadataschema import metadataSchemaService as svc

# Reference the model class through the module under test: the root conftest's path juggling means
# `models.metadataSchema` and `backend.backend.models.metadataSchema` are distinct module objects
# loaded from the same file, so an isinstance() check against the wrong one always fails.
MetadataSchemaEntityType = svc.MetadataSchemaEntityType
MetadataSchemaOperationResponseModel = svc.MetadataSchemaOperationResponseModel

SCHEMA_ID = "schema-abc-123"
DATABASE_ID = "GLOBAL"
# A real database, for the cases that must reach the database lookup GLOBAL short-circuits.
NAMED_DATABASE_ID = "factory-db"
SCHEMA_NAME = "Test Schema"
_SCHEMA_PATH = f"/database/{DATABASE_ID}/metadataschema/{SCHEMA_ID}"

AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}
NO_IDENTITY = {"tokens": [], "roles": [], "mfaEnabled": False}

_FIELDS = {
    "fields": [
        {
            "metadataFieldKeyName": "partNumber",
            "metadataFieldValueType": "string",
            "required": False,
        }
    ]
}


def _stored_schema():
    """A schema row as DynamoDB returns it (fields held as a JSON string)."""
    return {
        "metadataSchemaId": SCHEMA_ID,
        "databaseId:metadataEntityType": f"{DATABASE_ID}:assetMetadata",
        "databaseId": DATABASE_ID,
        "metadataSchemaEntityType": "assetMetadata",
        "schemaName": SCHEMA_NAME,
        "fields": json.dumps(_FIELDS),
        "enabled": True,
    }


def _create_payload():
    return {
        "databaseId": DATABASE_ID,
        "metadataSchemaEntityType": MetadataSchemaEntityType.ASSET_METADATA,
        "schemaName": SCHEMA_NAME,
        "fields": _FIELDS,
        "enabled": True,
    }


class _EnforcerSpy:
    """Stands in for CasbinEnforcer, recording every construction and every enforce() call."""

    constructions = []
    calls = []
    verdict = True

    def __init__(self, claims_and_roles):
        _EnforcerSpy.constructions.append(claims_and_roles)

    def enforce(self, obj, act):
        # Copy: callers mutate the same dict afterwards, so a reference would record the
        # post-call state.
        _EnforcerSpy.calls.append((dict(obj), act))
        return _EnforcerSpy.verdict

    def enforceAPI(self, event):
        return True

    @classmethod
    def reset(cls, verdict=True):
        cls.constructions = []
        cls.calls = []
        cls.verdict = verdict

    @classmethod
    def decisions(cls):
        """The SET of (object, action) decisions Casbin was asked for.

        A set of identifying tuples rather than a list, and asserted with `in` / `<=` rather than
        `==`: a handler that authorizes the same object twice (defence in depth) or authorizes an
        additional object is strictly safer and must not turn a test red, while a handler that
        drops a check disappears from the set and does. Pinning `actions() == [...]` inverts both --
        it fails on the safer handler and is satisfied by the wrong object being authorized.
        """
        return {
            (
                doc.get("object__type"),
                doc.get("databaseId"),
                doc.get("metadataSchemaName"),
                act,
            )
            for doc, act in cls.calls
        }


@pytest.fixture
def spy():
    _EnforcerSpy.reset()
    original = svc.CasbinEnforcer
    svc.CasbinEnforcer = _EnforcerSpy
    try:
        yield _EnforcerSpy
    finally:
        svc.CasbinEnforcer = original


@pytest.fixture
def schema_table():
    """The module-level table, replaced for the duration of one test."""
    table = MagicMock()
    table.query.return_value = {"Items": [_stored_schema()]}
    with patch.object(svc, "metadata_schema_table", table):
        yield table


@pytest.fixture
def claims():
    """Sets the module global the request handlers read; restores it afterwards."""
    original = svc.claims_and_roles

    def _set(value):
        svc.claims_and_roles = value
        return value

    try:
        yield _set
    finally:
        svc.claims_and_roles = original


def _status(response):
    return response["statusCode"]


def _decision(action, database_id=DATABASE_ID, schema_name=SCHEMA_NAME):
    """The identifying tuple that `spy.decisions()` must contain for `action`.

    A metadataSchema is authorized on databaseId + metadataSchemaName + metadataSchemaEntityType
    (CONSTRAINT_OBJECT_TYPE_FIELDS), so naming the object__type, scope and schema name pins that the
    right object was authorized -- which is the property. How many times it was authorized is not.
    """
    return ("metadataSchema", database_id, schema_name, action)


@pytest.mark.unit
class TestCreateMetadataSchema:
    def test_authorized_create_writes_and_consults_casbin(self, spy, schema_table):
        """Positive control: the permitted case must still work, and the document Casbin receives
        must be typed metadataSchema and carry the schema's identifying attributes."""
        result = svc.create_metadata_schema(_create_payload(), AUTHENTICATED)

        assert isinstance(result, MetadataSchemaOperationResponseModel)
        schema_table.put_item.assert_called_once()
        assert _decision("POST") in spy.decisions()

    def test_empty_tokens_denies_without_consulting_casbin(self, spy, schema_table):
        response = svc.create_metadata_schema(_create_payload(), NO_IDENTITY)

        assert _status(response) == 403
        schema_table.put_item.assert_not_called()
        # The property: with no identity there is nothing to authorize against, so the enforcer
        # must not be built or called at all.
        assert spy.constructions == []
        assert spy.calls == []

    def test_denied_create_does_not_write(self, spy, schema_table):
        """Negative control for the gate itself: a real Casbin denial must also block the write."""
        spy.reset(verdict=False)
        response = svc.create_metadata_schema(_create_payload(), AUTHENTICATED)

        assert _status(response) == 403
        schema_table.put_item.assert_not_called()
        assert _decision("POST") in spy.decisions()


@pytest.mark.unit
class TestCreateAuthorizesBeforeDisclosingDatabaseExistence:
    """S2-BACKEND-145 at the create site.

    `create_metadata_schema` ran `verify_database_exists()` -- a `database_table.get_item` that
    raises "Database does not exist" (400) -- before the Tier-2 gate (403), so a caller holding the
    POST route but no matching metadataSchema constraint could tell an existing database from an
    absent one by the status code alone. That is a database-name oracle: `databaseService.py`
    Casbin-filters the database listing, so these are names the caller cannot enumerate.

    A metadataSchema is authorized on databaseId + metadataSchemaName + metadataSchemaEntityType,
    all of which the create request supplies, so nothing has to be read to reach the decision.

    The existing create tests use databaseId "GLOBAL", which `verify_database_exists` short-circuits
    before the lookup -- these use a named database so the lookup is actually reached.
    """

    def _post_event(self):
        return {
            "requestContext": {"http": {"method": "POST", "path": "/metadataschema"}},
            "body": json.dumps(
                {
                    "databaseId": NAMED_DATABASE_ID,
                    "metadataSchemaEntityType": "assetMetadata",
                    "schemaName": SCHEMA_NAME,
                    "fields": _FIELDS,
                    "enabled": True,
                }
            ),
        }

    def _post(self, database_present):
        """POST through the request handler, with the database row present or absent."""
        database_table = MagicMock()
        database_table.get_item.return_value = (
            {"Item": {"databaseId": NAMED_DATABASE_ID}} if database_present else {}
        )
        schema_table = MagicMock()
        with patch.object(svc, "database_table", database_table), patch.object(
            svc, "metadata_schema_table", schema_table
        ):
            response = svc.handle_post_request(self._post_event())
        return response, database_table, schema_table

    @pytest.mark.parametrize("database_present", [True, False], ids=["db_exists", "db_absent"])
    def test_unauthorized_create_never_probes_the_database_table(
        self, spy, claims, database_present
    ):
        spy.reset(verdict=False)
        claims(AUTHENTICATED)
        response, database_table, schema_table = self._post(database_present)

        assert _status(response) == 403
        # The oracle was the lookup itself: no read, no signal, and no per-probe table traffic.
        database_table.get_item.assert_not_called()
        schema_table.put_item.assert_not_called()
        # Casbin was still asked, and asked about the requested database.
        assert _decision("POST", database_id=NAMED_DATABASE_ID) in spy.decisions()

    def test_unauthorized_denials_are_indistinguishable_across_database_states(self, spy, claims):
        """The property, not one spelling: same status and same body either way."""
        seen = set()
        for database_present in (True, False):
            spy.reset(verdict=False)
            claims(AUTHENTICATED)
            response, _database_table, _schema_table = self._post(database_present)
            seen.add((_status(response), response["body"]))
        assert len(seen) == 1

    def test_empty_tokens_never_probes_the_database_table(self, spy, claims):
        claims(NO_IDENTITY)
        response, database_table, schema_table = self._post(database_present=True)

        assert _status(response) == 403
        database_table.get_item.assert_not_called()
        schema_table.put_item.assert_not_called()
        assert spy.constructions == []
        assert spy.calls == []

    def test_authorized_create_in_an_existing_database_succeeds(self, spy, claims):
        """Positive control: the permitted case must still work end to end."""
        claims(AUTHENTICATED)
        response, database_table, schema_table = self._post(database_present=True)

        assert _status(response) == 200
        database_table.get_item.assert_called_once()
        schema_table.put_item.assert_called_once()

    def test_authorized_create_in_an_absent_database_is_still_rejected(self, spy, claims):
        """Positive control for the check that moved: reordering must not delete it. An authorized
        caller naming a database that does not exist still gets the 400 and no write."""
        claims(AUTHENTICATED)
        response, database_table, schema_table = self._post(database_present=False)

        assert _status(response) == 400
        database_table.get_item.assert_called_once()
        schema_table.put_item.assert_not_called()


@pytest.mark.unit
class TestUpdateMetadataSchema:
    def test_authorized_update_writes_and_consults_casbin(self, spy, schema_table):
        result = svc.update_metadata_schema(SCHEMA_ID, {"enabled": False}, AUTHENTICATED)

        assert isinstance(result, MetadataSchemaOperationResponseModel)
        schema_table.put_item.assert_called_once()
        assert _decision("POST") in spy.decisions()

    def test_empty_tokens_denies_without_consulting_casbin(self, spy, schema_table):
        response = svc.update_metadata_schema(SCHEMA_ID, {"enabled": False}, NO_IDENTITY)

        assert _status(response) == 403
        schema_table.put_item.assert_not_called()
        assert spy.constructions == []
        assert spy.calls == []

    def test_denied_update_does_not_write(self, spy, schema_table):
        spy.reset(verdict=False)
        response = svc.update_metadata_schema(SCHEMA_ID, {"enabled": False}, AUTHENTICATED)

        assert _status(response) == 403
        schema_table.put_item.assert_not_called()


@pytest.mark.unit
class TestDeleteMetadataSchema:
    def test_authorized_delete_removes_the_schema(self, spy, schema_table):
        result = svc.delete_metadata_schema(SCHEMA_ID, AUTHENTICATED)

        assert isinstance(result, MetadataSchemaOperationResponseModel)
        schema_table.delete_item.assert_called_once()
        assert _decision("DELETE") in spy.decisions()

    def test_empty_tokens_denies_without_consulting_casbin(self, spy, schema_table):
        response = svc.delete_metadata_schema(SCHEMA_ID, NO_IDENTITY)

        assert _status(response) == 403
        schema_table.delete_item.assert_not_called()
        assert spy.constructions == []
        assert spy.calls == []

    def test_denied_delete_does_not_remove_the_schema(self, spy, schema_table):
        spy.reset(verdict=False)
        response = svc.delete_metadata_schema(SCHEMA_ID, AUTHENTICATED)

        assert _status(response) == 403
        schema_table.delete_item.assert_not_called()


def _get_event():
    return {
        "requestContext": {"http": {"method": "GET", "path": _SCHEMA_PATH}},
        "pathParameters": {"databaseId": DATABASE_ID, "metadataSchemaId": SCHEMA_ID},
        "queryStringParameters": None,
    }


@pytest.mark.unit
class TestGetSingleMetadataSchema:
    def test_authorized_get_returns_the_schema(self, spy, schema_table, claims):
        claims(AUTHENTICATED)
        response = svc.handle_get_request(_get_event())

        assert _status(response) == 200
        assert json.loads(response["body"])["metadataSchemaId"] == SCHEMA_ID
        assert _decision("GET") in spy.decisions()

    def test_empty_tokens_denies_without_consulting_casbin(self, spy, schema_table, claims):
        claims(NO_IDENTITY)
        response = svc.handle_get_request(_get_event())

        assert _status(response) == 403
        assert spy.constructions == []
        assert spy.calls == []
        assert SCHEMA_NAME not in response["body"]

    def test_denied_get_returns_no_schema_data(self, spy, schema_table, claims):
        spy.reset(verdict=False)
        claims(AUTHENTICATED)
        response = svc.handle_get_request(_get_event())

        assert _status(response) == 403
        assert SCHEMA_NAME not in response["body"]


@pytest.mark.unit
class TestListingsStayFailClosed:
    """The list-filtering shape (Rule 4's exception) appends only when enforce() passes, so an empty
    token list yields an empty page by construction. Guards against a later `else` arm."""

    def _stub_scan(self, item_count=1):
        client = MagicMock()
        client.scan.return_value = {
            "Items": [
                {
                    "metadataSchemaId": {"S": SCHEMA_ID},
                    "databaseId": {"S": DATABASE_ID},
                    "metadataSchemaEntityType": {"S": "assetMetadata"},
                    "schemaName": {"S": SCHEMA_NAME},
                    "fields": {"S": json.dumps(_FIELDS)},
                    "enabled": {"BOOL": True},
                }
            ]
            * item_count
        }
        return client

    def test_authorized_listing_returns_rows(self, spy, claims):
        claims(AUTHENTICATED)
        with patch.object(svc, "dynamodb_client", self._stub_scan()):
            result = svc.get_all_metadata_schemas({"pageSize": 10})
        assert len(result["Items"]) == 1

    def test_empty_tokens_listing_returns_nothing(self, spy, claims):
        claims(NO_IDENTITY)
        with patch.object(svc, "dynamodb_client", self._stub_scan()):
            result = svc.get_all_metadata_schemas({"pageSize": 10})
        assert result["Items"] == []
        assert spy.calls == []


@pytest.mark.unit
class TestDenialReachesTheClient:
    """A Tier-2 denial must reach the caller as 403. The mutating business functions return a
    response dict rather than the operation model, and the request handlers used to call
    `.dict()` on it unconditionally -- turning every denial into a 500."""

    def _post_event(self):
        return {
            "requestContext": {"http": {"method": "POST", "path": "/metadataschema"}},
            "body": json.dumps(
                {
                    "databaseId": DATABASE_ID,
                    "metadataSchemaEntityType": "assetMetadata",
                    "schemaName": SCHEMA_NAME,
                    "fields": _FIELDS,
                    "enabled": True,
                }
            ),
        }

    def _put_event(self):
        return {
            "requestContext": {"http": {"method": "PUT", "path": "/metadataschema"}},
            "body": json.dumps({"metadataSchemaId": SCHEMA_ID, "enabled": False}),
        }

    def _delete_event(self):
        return {
            "requestContext": {"http": {"method": "DELETE", "path": _SCHEMA_PATH}},
            "pathParameters": {"databaseId": DATABASE_ID, "metadataSchemaId": SCHEMA_ID},
            "body": json.dumps({"confirmDelete": True}),
        }

    @pytest.mark.parametrize(
        "handler_name, event_name",
        [
            ("handle_post_request", "_post_event"),
            ("handle_put_request", "_put_event"),
            ("handle_delete_request", "_delete_event"),
        ],
    )
    def test_denial_is_403_not_500(self, spy, schema_table, claims, handler_name, event_name):
        spy.reset(verdict=False)
        claims(AUTHENTICATED)
        response = getattr(svc, handler_name)(getattr(self, event_name)())
        assert _status(response) == 403

    @pytest.mark.parametrize(
        "handler_name, event_name",
        [
            ("handle_post_request", "_post_event"),
            ("handle_put_request", "_put_event"),
            ("handle_delete_request", "_delete_event"),
        ],
    )
    def test_empty_tokens_is_403_not_500(self, spy, schema_table, claims, handler_name, event_name):
        claims(NO_IDENTITY)
        response = getattr(svc, handler_name)(getattr(self, event_name)())
        assert _status(response) == 403
        assert spy.calls == []

    @pytest.mark.parametrize(
        "handler_name, event_name",
        [
            ("handle_post_request", "_post_event"),
            ("handle_put_request", "_put_event"),
            ("handle_delete_request", "_delete_event"),
        ],
    )
    def test_authorized_request_still_succeeds(self, spy, schema_table, claims, handler_name, event_name):
        """Positive control: the passthrough must not swallow the success path."""
        claims(AUTHENTICATED)
        response = getattr(svc, handler_name)(getattr(self, event_name)())
        assert _status(response) == 200


@pytest.mark.unit
class TestDeleteRequiresConfirmation:
    """S2-BACKEND-114: the `confirmDelete` interlock is enforced end to end.

    `DeleteMetadataSchemaRequestModel` is parsed by `handle_delete_request`, and the parsed model is
    never read afterwards -- so the model validator is the whole gate. With the validator inert for an
    omitted field, `DELETE` with body `{}` deleted the schema.
    """

    def _event(self, body):
        return {
            "requestContext": {"http": {"method": "DELETE", "path": _SCHEMA_PATH}},
            "pathParameters": {"databaseId": DATABASE_ID, "metadataSchemaId": SCHEMA_ID},
            "body": json.dumps(body),
        }

    @pytest.mark.parametrize(
        "body, label",
        [
            ({}, "no confirmDelete field"),
            ({"confirmDelete": False}, "confirmDelete explicitly false"),
        ],
    )
    def test_unconfirmed_delete_is_rejected(self, spy, schema_table, claims, body, label):
        claims(AUTHENTICATED)
        response = svc.handle_delete_request(self._event(body))

        assert _status(response) == 400, label
        schema_table.delete_item.assert_not_called()

    def test_confirmed_delete_proceeds(self, spy, schema_table, claims):
        """Positive control: a confirmed request from an authorized caller must still delete."""
        claims(AUTHENTICATED)
        response = svc.handle_delete_request(self._event({"confirmDelete": True}))

        assert _status(response) == 200
        schema_table.delete_item.assert_called_once()
