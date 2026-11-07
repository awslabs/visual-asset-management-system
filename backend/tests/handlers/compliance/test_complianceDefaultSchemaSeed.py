# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The default compliance schema seed (`customResources/complianceDefaultSchemaSeed.py`).

Two properties of the deployment-time seed are pinned here:

- `registeredAt` is the time the handler writes the row, not a value carried in the custom-resource
  properties (a property value is fixed when `cdk synth` runs, and changes the template every synth).
- Every Create and Update against a table that already holds the row is a no-op: the row, its
  creation time and every other version of the schema are left exactly as they are, and no second row
  is written. Delete never removes the row.

The table double is built from the CDK definition of the compliance schema table (`_cdkTables`), so
the conditional put is checked against the deployed key schema.
"""

import json
from datetime import datetime, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
from moto import mock_aws

from backend.backend.customResources import complianceDefaultSchemaSeed as seed_module
from backend.tests.handlers.compliance import _cdkTables

TABLE = "test-compliance-schema-table"
SCHEMA_KEY = {"schemaName": {"S": "default-compliance-schema"}, "internalVersion": {"N": "1"}}

# The row as the infra construct passes it (dynamodb-complianceschema-defaults-construct.ts).
SEED_ITEM = {
    **SCHEMA_KEY,
    "databaseId": {"S": "GLOBAL"},
    "description": {"S": "Default compliance schema: written once at deployment."},
    "schemaBody": {"S": json.dumps({"schemaFormat": "vams-rules-v1", "rules": {}})},
    "registeredBy": {"S": "SYSTEM_USER"},
    "isSystem": {"BOOL": True},
}


def _event(request_type, item=None, physical_id=None):
    event = {
        "RequestType": request_type,
        "ResourceProperties": {
            "ServiceToken": "arn:aws:lambda:us-east-1:123456789012:function:provider",
            "TableName": TABLE,
            "Item": json.dumps(item if item is not None else SEED_ITEM),
        },
    }
    if physical_id:
        event["PhysicalResourceId"] = physical_id
    return event


@pytest.fixture
def table(monkeypatch):
    """A compliance schema table with the deployed key schema, and the handler's client bound to it."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        cdk_table = _cdkTables.storage_tables()["dynamoTables/complianceSchemaStorage"]
        client.create_table(**cdk_table.create_table_kwargs(TABLE))
        monkeypatch.setattr(seed_module, "dynamodb_client", client)
        yield client


def _rows(client):
    return client.scan(TableName=TABLE)["Items"]


def _row(client, version="1"):
    key = {"schemaName": SCHEMA_KEY["schemaName"], "internalVersion": {"N": version}}
    return client.get_item(TableName=TABLE, Key=key).get("Item")


@pytest.mark.unit
class TestCreateOnEmptyTable:
    def test_writes_the_row_stamped_with_the_write_time(self, table):
        before = datetime.now(timezone.utc)
        response = seed_module.lambda_handler(_event("Create"), None)
        after = datetime.now(timezone.utc)

        assert response["Data"] == {"Written": "true"}
        row = _row(table)
        registered_at = datetime.fromisoformat(row.pop("registeredAt")["S"])
        assert before <= registered_at <= after
        assert registered_at.tzinfo is not None
        assert row == SEED_ITEM

    def test_physical_id_is_stable_and_names_the_table(self, table):
        create = seed_module.lambda_handler(_event("Create"), None)
        update = seed_module.lambda_handler(
            _event("Update", physical_id=create["PhysicalResourceId"]), None)
        # A physical id that changed on Update would make CloudFormation delete the old resource.
        assert create["PhysicalResourceId"] == f"{TABLE}_default_schema_initialization"
        assert update["PhysicalResourceId"] == create["PhysicalResourceId"]


@pytest.mark.unit
class TestExistingRowIsNeverChanged:
    """The upgrade path: the stack already holds the row (written by an earlier seed or an earlier
    deployment of this one) when the seed resource is created or updated."""

    # The row an earlier seed wrote, with a creation time from that deployment.
    EXISTING = {**SEED_ITEM, "registeredAt": {"S": "2026-10-01T09:00:00.000Z"}}

    @pytest.mark.parametrize("request_type", ["Create", "Update"])
    def test_existing_row_is_left_exactly_as_it_is(self, table, request_type):
        table.put_item(TableName=TABLE, Item=self.EXISTING)

        response = seed_module.lambda_handler(_event(request_type), None)

        assert response["Data"] == {"Written": "false"}
        assert _row(table) == self.EXISTING
        assert len(_rows(table)) == 1

    def test_a_changed_default_schema_does_not_overwrite_the_existing_row(self, table):
        # A release that changes the default schema changes the properties, which runs an Update;
        # the deployed row (bound, evaluated, possibly versioned) stays as it is.
        table.put_item(TableName=TABLE, Item=self.EXISTING)
        changed = {**SEED_ITEM, "description": {"S": "A newer description"},
                   "schemaBody": {"S": json.dumps({"schemaFormat": "vams-rules-v1",
                                                   "rules": {"x": {}}})}}

        seed_module.lambda_handler(_event("Update", item=changed), None)

        assert _row(table) == self.EXISTING

    def test_later_versions_are_kept_and_no_version_is_added(self, table):
        table.put_item(TableName=TABLE, Item=self.EXISTING)
        version_2 = {**self.EXISTING, "internalVersion": {"N": "2"},
                     "registeredAt": {"S": "2026-10-15T12:00:00+00:00"}}
        table.put_item(TableName=TABLE, Item=version_2)

        seed_module.lambda_handler(_event("Update"), None)

        assert _row(table, "1") == self.EXISTING
        assert _row(table, "2") == version_2
        assert len(_rows(table)) == 2

    def test_repeated_create_and_update_write_once(self, table):
        seed_module.lambda_handler(_event("Create"), None)
        first = _row(table)

        for request_type in ("Update", "Update", "Create"):
            response = seed_module.lambda_handler(_event(request_type), None)
            assert response["Data"] == {"Written": "false"}

        assert _row(table) == first
        assert len(_rows(table)) == 1


@pytest.mark.unit
class TestOnlyTheConditionalPutIsIssued:
    def test_one_conditional_put_on_the_schema_table_and_nothing_else(self, monkeypatch):
        # Bindings, evaluations and every other table are out of reach: the handler's only call is a
        # PutItem on the table it is given, conditional on the key not existing. No update, delete or
        # read of any table is made, so nothing an administrator set can be reset.
        client = boto3.client("dynamodb", region_name="us-east-1")
        monkeypatch.setattr(seed_module, "dynamodb_client", client)
        with Stubber(client) as stub:
            stub.add_response("put_item", {}, {
                "TableName": TABLE,
                "Item": {**SEED_ITEM, "registeredAt": ANY},
                "ConditionExpression": seed_module.SEED_CONDITION,
            })
            seed_module.lambda_handler(_event("Create"), None)
            stub.assert_no_pending_responses()

    def test_condition_covers_both_key_attributes(self):
        cdk_table = _cdkTables.storage_tables()["dynamoTables/complianceSchemaStorage"]
        for attribute in cdk_table.key_attributes():
            assert f"attribute_not_exists({attribute})" in seed_module.SEED_CONDITION

    def test_other_errors_fail_the_resource(self, monkeypatch):
        client = boto3.client("dynamodb", region_name="us-east-1")
        monkeypatch.setattr(seed_module, "dynamodb_client", client)
        with Stubber(client) as stub:
            stub.add_client_error("put_item", service_error_code="AccessDeniedException")
            with pytest.raises(ClientError):
                seed_module.lambda_handler(_event("Create"), None)


@pytest.mark.unit
class TestDeleteAndInvalidInput:
    def test_delete_keeps_the_row(self, table):
        seed_module.lambda_handler(_event("Create"), None)
        written = _row(table)

        response = seed_module.lambda_handler(
            _event("Delete", physical_id=f"{TABLE}_default_schema_initialization"), None)

        assert response["PhysicalResourceId"] == f"{TABLE}_default_schema_initialization"
        assert _row(table) == written

    def test_a_timestamp_in_the_properties_is_refused(self, table):
        # registeredAt in the template is the defect this handler exists to remove.
        stamped = {**SEED_ITEM, "registeredAt": {"S": "2026-10-29T21:04:08.000Z"}}
        with pytest.raises(ValueError, match="registeredAt"):
            seed_module.lambda_handler(_event("Create", item=stamped), None)
        assert _rows(table) == []

    @pytest.mark.parametrize("item", [
        {k: v for k, v in SEED_ITEM.items() if k != "internalVersion"},
        {k: v for k, v in SEED_ITEM.items() if k != "schemaName"},
    ])
    def test_an_item_without_its_key_is_refused(self, table, item):
        with pytest.raises(ValueError, match="key"):
            seed_module.lambda_handler(_event("Create", item=item), None)

    def test_an_unparseable_item_is_refused(self, table):
        event = _event("Create")
        event["ResourceProperties"]["Item"] = "not json"
        with pytest.raises(ValueError, match="JSON"):
            seed_module.lambda_handler(event, None)

    def test_an_unknown_request_type_is_refused(self, table):
        with pytest.raises(ValueError, match="Unsupported"):
            seed_module.lambda_handler(_event("Replace"), None)
