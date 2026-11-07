# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""CloudFormation custom resource that writes the GLOBAL default compliance schema once.

`DynamoDbComplianceSchemaDefaultsConstruct` (infra) calls this handler through a custom-resource
provider with two properties:

- `TableName`: the compliance schema table.
- `Item`: the schema row as a DynamoDB attribute-value map in JSON, without `registeredAt`.

Create and Update put the row with `registeredAt` set to the time of the write, on the condition that
no row with the same key (schemaName, internalVersion) exists. A row that exists is left exactly as it
is, so a redeploy, a seed resource that is replaced, or a retained table adopted by a new stack never
overwrites the schema, its creation time or anything written beside it, and never adds a second row.
Delete leaves the row in place: the schema outlives the seed (see `app.compliance.autoLoadDefaultSchema`
in the configuration reference).

The time is stamped here rather than in the template so the synthesized template does not change from
one `cdk synth` to the next. The resource then runs only when its properties change: when it is first
created, or when a release changes the default schema the construct passes.
"""

import json
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from customLogging.logger import safeLogger

logger = safeLogger(service_name="ComplianceDefaultSchemaSeed")

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb_client = boto3.client("dynamodb", config=retry_config)

# Holds for a missing row only; an existing row fails the condition and is left alone.
SEED_CONDITION = "attribute_not_exists(schemaName) AND attribute_not_exists(internalVersion)"
TIMESTAMP_ATTRIBUTE = "registeredAt"


def _physical_resource_id(table_name):
    return f"{table_name}_default_schema_initialization"


def _seed_item(properties):
    """The table name and the row to write, validated, without the timestamp."""
    table_name = properties.get("TableName")
    if not isinstance(table_name, str) or not table_name:
        raise ValueError("TableName property is required")
    try:
        item = json.loads(properties.get("Item") or "")
    except (TypeError, ValueError):
        raise ValueError("Item property must be a JSON attribute-value map")
    if not isinstance(item, dict):
        raise ValueError("Item property must be a JSON attribute-value map")
    if "S" not in (item.get("schemaName") or {}) or "N" not in (item.get("internalVersion") or {}):
        raise ValueError("Item must carry the schemaName (S) and internalVersion (N) key")
    if TIMESTAMP_ATTRIBUTE in item:
        # A timestamp in the properties would be the synth time, the defect this handler removes.
        raise ValueError(f"Item must not carry {TIMESTAMP_ATTRIBUTE}: the handler sets it")
    return table_name, item


def seed(properties, now=None):
    """Write the row unless it exists. Returns True when it was written, False when left alone."""
    table_name, item = _seed_item(properties)
    stamped = dict(item)
    stamped[TIMESTAMP_ATTRIBUTE] = {"S": now or datetime.now(timezone.utc).isoformat()}
    schema_name = item["schemaName"]["S"]
    try:
        dynamodb_client.put_item(
            TableName=table_name,
            Item=stamped,
            ConditionExpression=SEED_CONDITION,
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info(f"Default compliance schema '{schema_name}' exists, left unchanged")
            return False
        raise
    logger.info(f"Default compliance schema '{schema_name}' written")
    return True


def lambda_handler(event, context):
    """Custom-resource provider entry point. Raising reports the request as FAILED."""
    request_type = event.get("RequestType")
    properties = event.get("ResourceProperties") or {}
    logger.info(f"Compliance default schema seed: {request_type}")

    if request_type == "Delete":
        # The row stays: removing the seed (flag turned off, resource replaced, stack deleted)
        # must not remove a schema databases and assets may be bound to.
        return {"PhysicalResourceId": event.get("PhysicalResourceId")
                or _physical_resource_id(properties.get("TableName", ""))}

    if request_type not in ("Create", "Update"):
        raise ValueError(f"Unsupported request type: {request_type}")

    written = seed(properties)
    return {
        "PhysicalResourceId": _physical_resource_id(properties["TableName"]),
        "Data": {"Written": "true" if written else "false"},
    }
