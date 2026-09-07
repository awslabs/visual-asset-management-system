# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Handler tests for metadataSchemaService's storage and authorization-scope contract.

`test_metadataSchema_tier2_fail_closed.py` covers the four Tier-2 enforcement sites and the delete
confirmation interlock. This file covers the rest of the handler's surface -- the parts a regression
would move silently, because DynamoDB accepts whatever shape it is handed:

- **The `fields` JSON round-trip.** `fields` is stored as a JSON *string* (`json.dumps`) and parsed
  back (`json.loads`) on every read path. Storing the dict instead, or dropping the parse on read,
  keeps every write succeeding: the create still returns 200 and the row still lands, but the
  response model rejects the column and the listing degrades to the raw DynamoDB item shape. An
  update that re-serializes an already-serialized column double-encodes it the same way.

- **The composite sort key.** The table's sort key is `databaseId:metadataEntityType`, built at
  create from `databaseId` + the entity type's *enum value* and read back verbatim from the stored
  row on delete. Two failure modes hide here: an f-string over the enum MEMBER writes a key the
  `DatabaseIdMetadataEntityTypeIndex` listing can never match, and a delete that RECOMPUTES the key
  instead of reading it misses a row whose stored key does not match the recomputation.

- **Which databaseId authorization is evaluated against.** Create authorizes the databaseId the
  request supplies; update, delete and the single-schema GET authorize the databaseId on the STORED
  row, which is not the one in the path. A regression that swapped either for the other would leave
  every same-database test passing.

- **Pagination.** The listings emit a base64 `NextToken` wrapping `LastEvaluatedKey`; it has to be
  accepted back on `startingToken` and reach the next read as `ExclusiveStartKey`, or page one looks
  perfect and every later page is unreachable.

Every enforcer here is an explicit stand-in rather than the root conftest's MagicMock: a MagicMock's
`enforce()` returns a truthy Mock, so a test left on it authorizes everything by accident and passes
for the wrong reason. Each denial assertion is paired with the permitted case that must still work.
"""

import base64
import json

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.metadataschema import metadataSchemaService as svc

# Reference the model classes through the module under test: the root conftest's path juggling means
# `models.metadataSchema` and `backend.backend.models.metadataSchema` are distinct module objects
# loaded from the same file, so an isinstance() check against the wrong one always fails.
MetadataSchemaEntityType = svc.MetadataSchemaEntityType

SCHEMA_ID = "schema-abc-123"
GLOBAL_DATABASE_ID = "GLOBAL"
# The database a stored row belongs to. Deliberately not the id used in the request path, so a test
# can tell which of the two authorization was evaluated against.
OWNING_DATABASE_ID = "factory-db"
OTHER_DATABASE_ID = "other-db"
SCHEMA_NAME = "Test Schema"
# The stored spelling, taken from the enum so the literal cannot drift from it.
ENTITY_TYPE = MetadataSchemaEntityType.ASSET_METADATA.value

AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}
NO_IDENTITY = {"tokens": [], "roles": [], "mfaEnabled": False}

# Every key is set explicitly, so `dict(exclude_unset=True)` reproduces exactly this shape and the
# round-trip assertions can compare for equality rather than for a subset.
_FIELDS = {
    "fields": [
        {
            "metadataFieldKeyName": "partNumber",
            "metadataFieldValueType": "string",
            "required": False,
        }
    ]
}

_REPLACEMENT_FIELDS = {
    "fields": [
        {
            "metadataFieldKeyName": "serialNumber",
            "metadataFieldValueType": "string",
            "required": True,
        }
    ]
}


def _stored_schema(database_id=OWNING_DATABASE_ID, entity_type=ENTITY_TYPE,
                   composite_key=None, fields=None):
    """A schema row as DynamoDB returns it (fields held as a JSON string)."""
    return {
        "metadataSchemaId": SCHEMA_ID,
        "databaseId:metadataEntityType": (composite_key if composite_key is not None
                                          else f"{database_id}:{entity_type}"),
        "databaseId": database_id,
        "metadataSchemaEntityType": entity_type,
        "schemaName": SCHEMA_NAME,
        "fields": json.dumps(_FIELDS) if fields is None else fields,
        "enabled": True,
    }


def _ddb_item(database_id=OWNING_DATABASE_ID, entity_type=ENTITY_TYPE):
    """The same row in the low-level client's typed shape, as the GSI reads return it."""
    return {
        "metadataSchemaId": {"S": SCHEMA_ID},
        "databaseId:metadataEntityType": {"S": f"{database_id}:{entity_type}"},
        "databaseId": {"S": database_id},
        "metadataSchemaEntityType": {"S": entity_type},
        "schemaName": {"S": SCHEMA_NAME},
        "fields": {"S": json.dumps(_FIELDS)},
        "enabled": {"BOOL": True},
    }


def _enforcer(allowed=None):
    """A CasbinEnforcer stand-in recording every decision it was asked for.

    `allowed` is the set of databaseIds it authorizes; None authorizes everything.
    """

    class _Enforcer:
        calls = []

        def __init__(self, claims_and_roles):
            pass

        def enforce(self, obj, act):
            # Copy: the handlers mutate the same dict afterwards, so a reference would record the
            # post-call state.
            _Enforcer.calls.append((dict(obj), act))
            return True if allowed is None else obj.get("databaseId") in allowed

        def enforceAPI(self, event):
            return True

    return _Enforcer


@pytest.fixture
def enforcer():
    """Installs a permissive CasbinEnforcer stand-in, and yields the installer so a test can swap in
    a database-scoped one."""
    original = svc.CasbinEnforcer

    def _install(cls=None):
        cls = cls if cls is not None else _enforcer()
        svc.CasbinEnforcer = cls
        return cls

    _install()
    try:
        yield _install
    finally:
        svc.CasbinEnforcer = original


@pytest.fixture
def claims():
    """Sets the module global the request handlers read; restores it afterwards."""
    original = svc.claims_and_roles
    svc.claims_and_roles = AUTHENTICATED

    def _set(value):
        svc.claims_and_roles = value
        return value

    try:
        yield _set
    finally:
        svc.claims_and_roles = original


@pytest.fixture
def schema_table():
    """The module-level table, replaced for the duration of one test."""
    table = MagicMock()
    table.query.return_value = {"Items": [_stored_schema()]}
    with patch.object(svc, "metadata_schema_table", table):
        yield table


@pytest.fixture
def database_table():
    """The database table, answering "the database exists" by default."""
    table = MagicMock()
    table.get_item.return_value = {"Item": {"databaseId": OWNING_DATABASE_ID}}
    with patch.object(svc, "database_table", table):
        yield table


@pytest.fixture
def ddb_client():
    """The low-level client the three listing reads use."""
    client = MagicMock()
    client.query.return_value = {"Items": [_ddb_item()]}
    client.scan.return_value = {"Items": [_ddb_item()]}
    with patch.object(svc, "dynamodb_client", client):
        yield client


def _status(response):
    return response["statusCode"]


def _body(response):
    return json.loads(response["body"])


def _written(table):
    """The Item handed to put_item."""
    return table.put_item.call_args.kwargs["Item"]


def _create_event(database_id=GLOBAL_DATABASE_ID, entity_type=ENTITY_TYPE, fields=None):
    return {
        "requestContext": {"http": {"method": "POST", "path": "/metadataschema"}},
        "body": json.dumps({
            "databaseId": database_id,
            "metadataSchemaEntityType": entity_type,
            "schemaName": SCHEMA_NAME,
            "fields": _FIELDS if fields is None else fields,
            "enabled": True,
        }),
    }


def _update_event(body=None):
    return {
        "requestContext": {"http": {"method": "PUT", "path": "/metadataschema"}},
        "body": json.dumps(body if body is not None
                           else {"metadataSchemaId": SCHEMA_ID, "fields": _REPLACEMENT_FIELDS}),
    }


def _delete_event(database_id=GLOBAL_DATABASE_ID, body={"confirmDelete": True}):
    event = {
        "requestContext": {
            "http": {"method": "DELETE",
                     "path": f"/database/{database_id}/metadataschema/{SCHEMA_ID}"}
        },
        "pathParameters": {"databaseId": database_id, "metadataSchemaId": SCHEMA_ID},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _get_event(database_id=GLOBAL_DATABASE_ID):
    return {
        "requestContext": {
            "http": {"method": "GET",
                     "path": f"/database/{database_id}/metadataschema/{SCHEMA_ID}"}
        },
        "pathParameters": {"databaseId": database_id, "metadataSchemaId": SCHEMA_ID},
        "queryStringParameters": None,
    }


def _list_event(query=None):
    return {
        "requestContext": {"http": {"method": "GET", "path": "/metadataschema"}},
        "pathParameters": {},
        "queryStringParameters": query if query is not None else {},
    }


@pytest.mark.unit
class TestCreateStoresFieldsAndKeys:
    """What create writes is what every read path -- the single GET, both GSI listings, and the
    delete -- depends on, so the stored shape is the contract."""

    def test_fields_are_stored_as_a_json_string_that_round_trips(
            self, enforcer, claims, schema_table, database_table):
        response = svc.handle_post_request(_create_event())

        assert _status(response) == 200
        item = _written(schema_table)
        # A dict here would be accepted by DynamoDB and rejected by the response model on read.
        assert isinstance(item["fields"], str)
        assert json.loads(item["fields"]) == _FIELDS

    def test_composite_sort_key_and_entity_type_are_plain_strings(
            self, enforcer, claims, schema_table, database_table):
        response = svc.handle_post_request(_create_event())

        assert _status(response) == 200
        item = _written(schema_table)
        assert item["databaseId:metadataEntityType"] == f"{GLOBAL_DATABASE_ID}:{ENTITY_TYPE}"
        assert item["metadataSchemaEntityType"] == ENTITY_TYPE
        # MetadataSchemaEntityType subclasses str, so the equality above holds for the enum MEMBER
        # too -- the type check is what pins that the stored value is the enum's value. The member
        # renders as "MetadataSchemaEntityType.ASSET_METADATA" inside the composite key f-string.
        assert type(item["metadataSchemaEntityType"]) is str
        assert type(item["databaseId:metadataEntityType"]) is str

    def test_stored_composite_key_matches_the_key_the_listing_queries_for(
            self, enforcer, claims, schema_table, database_table, ddb_client):
        """The write and the GSI read build the same key from two places in the module; drift in
        either one makes a created schema invisible to the entity-type listing."""
        svc.handle_post_request(_create_event())
        stored_key = _written(schema_table)["databaseId:metadataEntityType"]

        svc.get_metadata_schemas_by_database_and_type(
            GLOBAL_DATABASE_ID, ENTITY_TYPE, {"pageSize": 10})
        queried_key = ddb_client.query.call_args.kwargs["ExpressionAttributeValues"][":pkValue"]

        assert queried_key == {"S": stored_key}

    def test_provenance_records_the_calling_user(
            self, enforcer, claims, schema_table, database_table):
        svc.handle_post_request(_create_event())

        item = _written(schema_table)
        assert item["createdBy"] == AUTHENTICATED["tokens"][0]
        assert item["modifiedBy"] == AUTHENTICATED["tokens"][0]

    def test_a_global_schema_is_not_gated_on_a_database_row(
            self, enforcer, claims, schema_table, database_table):
        """GLOBAL is a reserved scope rather than a row in the database table, so the existence
        check short-circuits before the lookup."""
        database_table.get_item.return_value = {}

        response = svc.handle_post_request(_create_event(database_id=GLOBAL_DATABASE_ID))

        assert _status(response) == 200
        database_table.get_item.assert_not_called()

    def test_a_named_database_is_still_gated_on_its_row(
            self, enforcer, claims, schema_table, database_table):
        """Control arm for the short-circuit: it must apply to GLOBAL only."""
        database_table.get_item.return_value = {}

        response = svc.handle_post_request(_create_event(database_id=OWNING_DATABASE_ID))

        assert _status(response) == 400
        schema_table.put_item.assert_not_called()


@pytest.mark.unit
class TestGetSingleSchemaParsesStoredFields:
    def test_fields_are_returned_as_an_object_not_a_json_string(
            self, enforcer, claims, schema_table):
        response = svc.handle_get_request(_get_event())

        assert _status(response) == 200
        fields = _body(response)["fields"]
        assert not isinstance(fields, str)
        assert fields["fields"][0]["metadataFieldKeyName"] == "partNumber"
        assert fields["fields"][0]["metadataFieldValueType"] == "string"

    def test_an_unparsable_fields_column_does_not_become_an_internal_error(
            self, enforcer, claims, schema_table):
        """The parse is guarded because a row written by an earlier release, or by hand, can hold
        anything. Unguarded, the JSONDecodeError reaches the handler's catch-all as a 500."""
        schema_table.query.return_value = {"Items": [_stored_schema(fields="{not-json")]}
        installed = enforcer()

        response = svc.handle_get_request(_get_event())

        assert _status(response) != 500
        # The parse failure did not short-circuit the request before authorization ran. Asserted as
        # membership rather than as the whole call list: a handler that authorizes the same object
        # twice is strictly safer and must not turn this red.
        assert "GET" in [act for _obj, act in installed.calls]

    def test_a_missing_schema_is_reported_as_not_found(self, enforcer, claims, schema_table):
        schema_table.query.return_value = {"Items": []}

        response = svc.handle_get_request(_get_event())

        assert _status(response) == 404


@pytest.mark.unit
class TestUpdateReserializesFields:
    def test_replacement_fields_are_re_serialized_to_a_json_string(
            self, enforcer, claims, schema_table):
        response = svc.handle_put_request(_update_event())

        assert _status(response) == 200
        item = _written(schema_table)
        assert isinstance(item["fields"], str)
        assert json.loads(item["fields"]) == _REPLACEMENT_FIELDS

    def test_an_update_that_omits_fields_leaves_the_stored_string_untouched(
            self, enforcer, claims, schema_table):
        """The stored column is already a JSON string, and the update writes the row it read back.
        Re-serializing it would double-encode the column into a JSON string of a JSON string."""
        stored = _stored_schema()
        original_fields_json = stored["fields"]
        schema_table.query.return_value = {"Items": [stored]}

        response = svc.handle_put_request(
            _update_event({"metadataSchemaId": SCHEMA_ID, "enabled": False}))

        assert _status(response) == 200
        item = _written(schema_table)
        assert item["fields"] == original_fields_json
        assert json.loads(item["fields"]) == _FIELDS
        assert item["enabled"] is False

    def test_the_composite_sort_key_survives_the_update(self, enforcer, claims, schema_table):
        """put_item replaces the whole item, so a dropped sort key writes a second row rather than
        updating this one."""
        response = svc.handle_put_request(_update_event())

        assert _status(response) == 200
        assert (_written(schema_table)["databaseId:metadataEntityType"]
                == f"{OWNING_DATABASE_ID}:{ENTITY_TYPE}")

    def test_an_update_to_a_missing_schema_writes_nothing(self, enforcer, claims, schema_table):
        schema_table.query.return_value = {"Items": []}

        response = svc.handle_put_request(_update_event())

        assert _status(response) == 400
        schema_table.put_item.assert_not_called()


@pytest.mark.unit
class TestDeleteUsesTheStoredCompositeKey:
    def test_the_sort_key_is_read_from_the_stored_row_not_recomputed(
            self, enforcer, claims, schema_table):
        """A row whose stored sort key does not match `databaseId:metadataSchemaEntityType` -- one
        written before an entity type was corrected, say -- is deletable only through the value the
        row actually carries. Recomputing the key deletes nothing and still answers 200."""
        drifted_key = f"{OWNING_DATABASE_ID}:{ENTITY_TYPE}"
        schema_table.query.return_value = {
            "Items": [_stored_schema(entity_type="fileMetadata", composite_key=drifted_key)]
        }

        response = svc.handle_delete_request(_delete_event())

        assert _status(response) == 200
        assert schema_table.delete_item.call_args.kwargs["Key"] == {
            "metadataSchemaId": SCHEMA_ID,
            "databaseId:metadataEntityType": drifted_key,
        }

    def test_a_consistent_row_deletes_on_its_own_key(self, enforcer, claims, schema_table):
        """Positive control: the ordinary row, whose stored key and recomputed key agree."""
        response = svc.handle_delete_request(_delete_event())

        assert _status(response) == 200
        assert schema_table.delete_item.call_args.kwargs["Key"] == {
            "metadataSchemaId": SCHEMA_ID,
            "databaseId:metadataEntityType": f"{OWNING_DATABASE_ID}:{ENTITY_TYPE}",
        }

    def test_a_delete_of_a_missing_schema_removes_nothing(self, enforcer, claims, schema_table):
        schema_table.query.return_value = {"Items": []}

        response = svc.handle_delete_request(_delete_event())

        assert _status(response) == 400
        schema_table.delete_item.assert_not_called()

    def test_a_delete_with_no_body_is_rejected(self, enforcer, claims, schema_table):
        """The confirmation interlock lives in the request model, so a request carrying no body at
        all must be rejected before the model is reached rather than defaulting through it."""
        response = svc.handle_delete_request(_delete_event(body=None))

        assert _status(response) == 400
        schema_table.delete_item.assert_not_called()


@pytest.mark.unit
class TestAuthorizationScopeFollowsTheRightDatabase:
    """A metadataSchema is authorized on databaseId + metadataSchemaName + metadataSchemaEntityType.
    Which databaseId reaches the enforcer is therefore the whole scope of the decision, and every
    same-database test is blind to it."""

    def test_create_outside_the_scoped_database_is_denied(
            self, enforcer, claims, schema_table, database_table):
        enforcer(_enforcer(allowed={OWNING_DATABASE_ID}))

        response = svc.handle_post_request(_create_event(database_id=OTHER_DATABASE_ID))

        assert _status(response) == 403
        schema_table.put_item.assert_not_called()
        # The denial precedes the database-existence lookup, so it discloses nothing about which
        # databases exist.
        database_table.get_item.assert_not_called()

    def test_create_inside_the_scoped_database_succeeds(
            self, enforcer, claims, schema_table, database_table):
        """Positive control: the same scoped role must still create in the database it holds."""
        enforcer(_enforcer(allowed={OWNING_DATABASE_ID}))

        response = svc.handle_post_request(_create_event(database_id=OWNING_DATABASE_ID))

        assert _status(response) == 200
        assert _written(schema_table)["databaseId"] == OWNING_DATABASE_ID

    def test_get_authorizes_the_stored_database_not_the_path_database(
            self, enforcer, claims, schema_table):
        """The stored row lives in OWNING_DATABASE_ID while the path names GLOBAL. A role scoped to
        GLOBAL must not read it."""
        enforcer(_enforcer(allowed={GLOBAL_DATABASE_ID}))

        response = svc.handle_get_request(_get_event(database_id=GLOBAL_DATABASE_ID))

        assert _status(response) == 403
        assert SCHEMA_NAME not in response["body"]

    def test_get_succeeds_for_a_role_scoped_to_the_stored_database(
            self, enforcer, claims, schema_table):
        """Positive control, and the other half of the property: scoping to the row's own database
        does read it."""
        enforcer(_enforcer(allowed={OWNING_DATABASE_ID}))

        response = svc.handle_get_request(_get_event(database_id=GLOBAL_DATABASE_ID))

        assert _status(response) == 200
        assert _body(response)["databaseId"] == OWNING_DATABASE_ID

    def test_delete_authorizes_the_stored_database_not_the_path_database(
            self, enforcer, claims, schema_table):
        enforcer(_enforcer(allowed={GLOBAL_DATABASE_ID}))

        response = svc.handle_delete_request(_delete_event(database_id=GLOBAL_DATABASE_ID))

        assert _status(response) == 403
        schema_table.delete_item.assert_not_called()

    def test_delete_succeeds_for_a_role_scoped_to_the_stored_database(
            self, enforcer, claims, schema_table):
        enforcer(_enforcer(allowed={OWNING_DATABASE_ID}))

        response = svc.handle_delete_request(_delete_event(database_id=GLOBAL_DATABASE_ID))

        assert _status(response) == 200
        schema_table.delete_item.assert_called_once()

    def test_update_authorizes_the_stored_database(self, enforcer, claims, schema_table):
        """The update request carries no databaseId at all -- only the schema id -- so the stored
        row is the only source of scope."""
        enforcer(_enforcer(allowed={OTHER_DATABASE_ID}))

        response = svc.handle_put_request(_update_event())

        assert _status(response) == 403
        schema_table.put_item.assert_not_called()

    def test_update_succeeds_for_a_role_scoped_to_the_stored_database(
            self, enforcer, claims, schema_table):
        enforcer(_enforcer(allowed={OWNING_DATABASE_ID}))

        response = svc.handle_put_request(_update_event())

        assert _status(response) == 200
        schema_table.put_item.assert_called_once()


@pytest.mark.unit
class TestListingReads:
    """The three listings each build their own read, and each filters its page through Casbin."""

    def test_by_database_and_type_queries_the_composite_key_index(self, enforcer, claims,
                                                                  ddb_client):
        result = svc.get_metadata_schemas_by_database_and_type(
            OWNING_DATABASE_ID, ENTITY_TYPE, {"pageSize": 10})

        kwargs = ddb_client.query.call_args.kwargs
        assert kwargs["TableName"] == svc.metadata_schema_table_name
        assert kwargs["IndexName"] == "DatabaseIdMetadataEntityTypeIndex"
        assert kwargs["ExpressionAttributeNames"]["#pk"] == "databaseId:metadataEntityType"
        assert (kwargs["ExpressionAttributeValues"][":pkValue"]
                == {"S": f"{OWNING_DATABASE_ID}:{ENTITY_TYPE}"})
        # The typed row is deserialized and its fields column parsed for the caller.
        assert result["Items"][0]["fields"] == _FIELDS

    def test_by_database_queries_the_database_index(self, enforcer, claims, ddb_client):
        result = svc.get_metadata_schemas_by_database(OWNING_DATABASE_ID, {"pageSize": 10})

        kwargs = ddb_client.query.call_args.kwargs
        assert kwargs["IndexName"] == "DatabaseIdIndex"
        assert kwargs["ExpressionAttributeValues"][":dbId"] == {"S": OWNING_DATABASE_ID}
        assert result["Items"][0]["fields"] == _FIELDS

    def test_the_unfiltered_listing_scans_the_base_table_and_parses_fields(self, enforcer, claims,
                                                                           ddb_client):
        """The third reader carries its own copy of the deserialize-and-parse block, so the two GSI
        assertions above say nothing about it."""
        result = svc.get_all_metadata_schemas({"pageSize": 10})

        kwargs = ddb_client.scan.call_args.kwargs
        assert kwargs["TableName"] == svc.metadata_schema_table_name
        # No index: an unfiltered listing has to see every schema, including any whose GSI key
        # attribute is absent.
        assert "IndexName" not in kwargs
        assert result["Items"][0]["fields"] == _FIELDS

    @pytest.mark.parametrize(
        "reader",
        ["get_metadata_schemas_by_database_and_type", "get_metadata_schemas_by_database"],
    )
    def test_the_gsi_listings_are_fail_closed_on_an_empty_token_list(
            self, enforcer, claims, ddb_client, reader):
        """The list-filtering shape appends only when enforce() passes, so no identity yields an
        empty page by construction. Guards against a later `else` arm."""
        claims(NO_IDENTITY)
        args = ((OWNING_DATABASE_ID, ENTITY_TYPE, {"pageSize": 10})
                if reader.endswith("_and_type") else (OWNING_DATABASE_ID, {"pageSize": 10}))

        result = getattr(svc, reader)(*args)

        assert result["Items"] == []

    @pytest.mark.parametrize(
        "reader",
        ["get_metadata_schemas_by_database_and_type", "get_metadata_schemas_by_database"],
    )
    def test_the_gsi_listings_return_rows_for_an_authorized_caller(
            self, enforcer, claims, ddb_client, reader):
        """Positive control for the pair above: "empty" must not be the answer to every call."""
        args = ((OWNING_DATABASE_ID, ENTITY_TYPE, {"pageSize": 10})
                if reader.endswith("_and_type") else (OWNING_DATABASE_ID, {"pageSize": 10}))

        result = getattr(svc, reader)(*args)

        assert len(result["Items"]) == 1


@pytest.mark.unit
class TestListingPaginationTokenRoundTrip:
    """A NextToken the request model cannot accept back caps the listing at page one with no error,
    so the assertion has to feed the emitted token in and watch where it lands."""

    _LAST_KEY = {
        "metadataSchemaId": {"S": SCHEMA_ID},
        "databaseId:metadataEntityType": {"S": f"{OWNING_DATABASE_ID}:{ENTITY_TYPE}"},
    }

    def test_the_emitted_token_is_an_opaque_string_that_decodes_to_the_cursor(
            self, enforcer, claims, ddb_client):
        ddb_client.scan.return_value = {"Items": [_ddb_item()], "LastEvaluatedKey": self._LAST_KEY}

        result = svc.get_all_metadata_schemas({"pageSize": 10})

        token = result["NextToken"]
        assert isinstance(token, str)
        assert json.loads(base64.b64decode(token).decode("utf-8")) == self._LAST_KEY

    def test_feeding_the_token_back_resumes_the_scan_from_that_cursor(
            self, enforcer, claims, ddb_client):
        ddb_client.scan.return_value = {"Items": [_ddb_item()], "LastEvaluatedKey": self._LAST_KEY}
        token = svc.get_all_metadata_schemas({"pageSize": 10})["NextToken"]

        svc.get_all_metadata_schemas({"pageSize": 10, "startingToken": token})

        assert ddb_client.scan.call_args.kwargs["ExclusiveStartKey"] == self._LAST_KEY

    def test_a_complete_scan_emits_no_token_and_starts_from_the_beginning(
            self, enforcer, claims, ddb_client):
        """Positive control: the first page of a finished listing carries no cursor either way."""
        result = svc.get_all_metadata_schemas({"pageSize": 10})

        assert "NextToken" not in result
        assert "ExclusiveStartKey" not in ddb_client.scan.call_args.kwargs

    def test_a_token_that_is_not_a_cursor_is_rejected_before_the_read(
            self, enforcer, claims, ddb_client):
        bad_token = base64.b64encode(b"not-a-cursor").decode("utf-8")

        response = svc.handle_get_request(_list_event({"startingToken": bad_token}))

        assert _status(response) == 400
        ddb_client.scan.assert_not_called()
