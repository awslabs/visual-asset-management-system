# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Runs the REAL bucketReadConstraints step of the v2.6 to v2.7 migration (bucket_read_constraints.py, and
# the main script's main() for the wiring) against a fake constraints table and prints one JSON object per
# scenario. migrationBucketReadConstraints.test.ts reads it. boto3 and botocore are replaced by small fakes
# so the harness needs only the Python standard library; the migration tools carry no committed Python
# tests (infra Development Rule 6).
#
# Usage: bucketReadConstraintsHarness.py <v2.6_to_v2.7/upgrade directory> <scratch directory>
import copy
import importlib.util
import json
import logging
import os
import sys
import types

TABLE = "vams-test-constraintsStorageTable"
ACCOUNT = "111122223333"


class ClientError(Exception):
    def __init__(self, error_response, operation_name):
        self.response = error_response
        self.operation_name = operation_name
        code = error_response.get("Error", {}).get("Code", "")
        super().__init__(f"An error occurred ({code}) when calling the {operation_name} operation")


class ReadTimeoutError(Exception):
    pass


class Config:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeConstraintsTable:
    """The constraints table: items keyed by constraintId, ObjectTypeIndex queries served two items a page
    (so the step must follow LastEvaluatedKey), PutItem honouring attribute_not_exists(constraintId)."""

    def __init__(self, items, page_size=2):
        self.items = {item["constraintId"]["S"]: copy.deepcopy(item) for item in items}
        self.page_size = page_size
        self.index_snapshot = None  # set to serve the index from an older copy (index lag)
        self.put_error = None
        self.calls = []

    def query(self, **kwargs):
        assert kwargs["TableName"] == TABLE, kwargs["TableName"]
        assert kwargs["IndexName"] == "ObjectTypeIndex", kwargs["IndexName"]
        assert kwargs["KeyConditionExpression"] == "objectType = :t", kwargs["KeyConditionExpression"]
        object_type = kwargs["ExpressionAttributeValues"][":t"]["S"]
        self.calls.append({"op": "query", "objectType": object_type, "start": kwargs.get("ExclusiveStartKey")})
        source = self.index_snapshot if self.index_snapshot is not None else self.items
        ordered = sorted(cid for cid, item in source.items() if item["objectType"]["S"] == object_type)
        start = 0
        if kwargs.get("ExclusiveStartKey"):
            start = ordered.index(kwargs["ExclusiveStartKey"]["constraintId"]["S"]) + 1
        page = ordered[start:start + self.page_size]
        response = {"Items": [copy.deepcopy(source[cid]) for cid in page]}
        if start + self.page_size < len(ordered):
            response["LastEvaluatedKey"] = {"constraintId": {"S": page[-1]}, "objectType": {"S": object_type}}
        return response

    def put_item(self, **kwargs):
        item = kwargs["Item"]
        self.calls.append({"op": "put", "item": copy.deepcopy(item), "condition": kwargs.get("ConditionExpression"),
                           "table": kwargs["TableName"]})
        if self.put_error:
            raise ClientError({"Error": {"Code": self.put_error}}, "PutItem")
        if kwargs.get("ConditionExpression") == "attribute_not_exists(constraintId)" and \
                item["constraintId"]["S"] in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[item["constraintId"]["S"]] = copy.deepcopy(item)

    def puts(self):
        return [call for call in self.calls if call["op"] == "put"]


class FakeSts:
    def get_caller_identity(self):
        return {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:role/harness"}


CURRENT_TABLE = {"table": None}


class FakeSession:
    region_name = "us-east-1"

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def client(self, name, config=None):
        if name == "sts":
            return FakeSts()
        if name == "dynamodb":
            return CURRENT_TABLE["table"]
        raise AssertionError(f"unexpected client {name}")


def _install_fakes():
    botocore = types.ModuleType("botocore")
    config = types.ModuleType("botocore.config")
    config.Config = Config
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = ClientError
    exceptions.ReadTimeoutError = ReadTimeoutError
    botocore.config, botocore.exceptions = config, exceptions
    boto3 = types.ModuleType("boto3")
    boto3.Session = FakeSession
    boto3.client = lambda *args, **kwargs: FakeSession().client(*args, **kwargs)
    dynamodb = types.ModuleType("boto3.dynamodb")
    dynamodb.__path__ = []
    dynamodb_types = types.ModuleType("boto3.dynamodb.types")
    dynamodb_types.TypeDeserializer = type("TypeDeserializer", (), {})
    dynamodb_types.TypeSerializer = type("TypeSerializer", (), {})
    boto3.dynamodb, dynamodb.types = dynamodb, dynamodb_types
    sys.modules.update({"botocore": botocore, "botocore.config": config, "botocore.exceptions": exceptions,
                        "boto3": boto3, "boto3.dynamodb": dynamodb, "boto3.dynamodb.types": dynamodb_types})


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stored(base_id, principal, object_type, criteria_and, criteria_or=(), permissions=(("GET", "allow"),),
            raw_criteria_and=None):
    """The stored items of one constraint, one per principal, as the constraint service writes them."""
    kind, name = principal
    key = "groupId" if kind == "role" else "userId"
    entries = [{"id": f"{base_id}-{i}", key: name, "permission": action, "permissionType": effect}
               for i, (action, effect) in enumerate(permissions)]
    item = {
        "constraintId": {"S": f"{base_id}#{'group' if kind == 'role' else 'user'}#{name}"},
        key: {"S": name},
        "name": {"S": base_id},
        "description": {"S": base_id},
        "objectType": {"S": object_type},
        "criteriaAnd": {"S": raw_criteria_and if raw_criteria_and is not None else json.dumps(list(criteria_and))},
        "criteriaOr": {"S": json.dumps(list(criteria_or))},
        "groupPermissions": {"S": json.dumps(entries if kind == "role" else [])},
        "userPermissions": {"S": json.dumps(entries if kind == "user" else [])},
        "dateCreated": {"S": "2026-01-01T00:00:00+00:00"},
        "dateModified": {"S": "2026-01-01T00:00:00+00:00"},
        "createdBy": {"S": "SYSTEM_USER"},
        "modifiedBy": {"S": "SYSTEM_USER"},
    }
    return item


def _route(operator, value):
    return {"field": "route__path", "operator": operator, "value": value}


# The api-routes constraint a database-admin role imported from the 2.6 template holds: all methods on
# twenty starts_with routes, /buckets among them.
LEGACY_ADMIN_ROUTES = [_route("starts_with", value) for value in (
    "/database", "/assets", "/auth", "/buckets", "/comments", "/metadata", "/pipelines", "/workflows",
    "/tags", "/tag-types", "/subscriptions", "/search", "/upload", "/download", "/roles", "/user",
    "/check-subscription", "/unsubscribe", "/secure-config", "/api",
)]
ALL_METHODS = [("GET", "allow"), ("PUT", "allow"), ("POST", "allow"), ("DELETE", "allow")]


def seed():
    return [
        _stored("legacy-admin-api", ("role", "legacy-admin"), "api", [], LEGACY_ADMIN_ROUTES, ALL_METHODS),
        _stored("legacy-admin-web", ("role", "legacy-admin"), "web", [_route("starts_with", "/")]),
        _stored("has-bucket-api", ("role", "has-bucket"), "api", [_route("equals", "/buckets")]),
        _stored("has-bucket-team", ("role", "has-bucket"), "bucket",
                [{"field": "bucketName", "operator": "starts_with", "value": "team-a-"}]),
        _stored("reader-api", ("role", "reader"), "api", [_route("starts_with", "/database")]),
        _stored("denied-api", ("role", "denied"), "api", [_route("starts_with", "/")]),
        _stored("denied-buckets", ("role", "denied"), "api", [_route("equals", "/buckets")],
                permissions=[("GET", "deny")]),
        _stored("post-only-api", ("role", "post-only"), "api", [_route("equals", "/buckets")],
                permissions=[("POST", "allow")]),
        _stored("broken-api", ("role", "broken"), "api", [], raw_criteria_and="not json"),
        _stored("list-route-api", ("role", "list-route"), "api", [_route("is_one_of", ["/x", "/buckets"])]),
        _stored("alice-api", ("user", "alice"), "api", [_route("equals", "/buckets")]),
        _stored("alice-asset", ("user", "alice"), "asset", [{"field": "databaseId", "operator": "equals",
                                                              "value": "db-a"}]),
    ]


def _plain(item):
    return {key: next(iter(value.values())) for key, value in item.items()}


def _read_report(path):
    with open(path, encoding="utf-8") as report:
        return [json.loads(line) for line in report]


def run_step(step, table, dry_run, output_dir):
    CURRENT_TABLE["table"] = table
    before = copy.deepcopy(table.items)
    result = step.run_bucket_read_constraints(table, TABLE, dry_run, output_dir)
    unchanged = all(table.items.get(cid) == item for cid, item in before.items())
    return {
        "counts": result["counts"],
        "report": os.path.basename(result["report"]),
        "reportLines": _read_report(result["report"]),
        "puts": [{"item": _plain(call["item"]), "condition": call["condition"], "table": call["table"]}
                 for call in table.puts()],
        "queries": [call["objectType"] for call in table.calls if call["op"] == "query"],
        "existingItemsUnchanged": unchanged,
        "itemCount": len(table.items),
        "exitStatus": step.exit_status(result),
        "summary": step.summary_lines(result, dry_run),
    }


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def run_main(migration, scratch, name, argv_extra, dry_run_config):
    table = FakeConstraintsTable(seed())
    CURRENT_TABLE["table"] = table
    output_dir = os.path.join(scratch, name)
    config_path = os.path.join(scratch, f"{name}-config.json")
    with open(config_path, "w", encoding="utf-8") as config:
        json.dump({"resource_names_ssm_param_prefix": None, "constraints_storage_table_name": TABLE,
                   "bucket_read_constraints_output_dir": output_dir, "dry_run": dry_run_config}, config)
    collect = _Collect()
    root = logging.getLogger()
    root.handlers = [collect]
    try:
        code = migration.main(["--config", config_path, "--steps", "bucketReadConstraints"] + argv_extra)
    finally:
        root.handlers = []
    return {"exit": code, "puts": [_plain(call["item"])["groupId"] for call in table.puts()],
            "log": collect.lines}


def main():
    upgrade_dir, scratch = sys.argv[1], sys.argv[2]
    sys.dont_write_bytecode = True  # leave no __pycache__ in the migration tree
    _install_fakes()
    step = _load("bucket_read_constraints", os.path.join(upgrade_dir, "bucket_read_constraints.py"))
    results = {"template": step.TEMPLATE_BUCKET_CONSTRAINT,
               "moduleImports": sorted(name for name in sys.modules if name.startswith("backend"))}

    table = FakeConstraintsTable(seed())
    results["dryRun"] = run_step(step, table, True, os.path.join(scratch, "dry"))
    results["execute"] = run_step(step, table, False, os.path.join(scratch, "execute"))
    written = copy.deepcopy(table.items)
    table.calls = []
    results["rerun"] = run_step(step, table, False, os.path.join(scratch, "rerun"))

    lagging = FakeConstraintsTable(seed())
    lagging.index_snapshot = copy.deepcopy(lagging.items)
    lagging.items = copy.deepcopy(written)
    results["indexLag"] = run_step(step, lagging, False, os.path.join(scratch, "lag"))

    failing = FakeConstraintsTable(seed())
    failing.put_error = "AccessDeniedException"
    results["writeFails"] = run_step(step, failing, False, os.path.join(scratch, "fails"))

    # The main script's wiring: the step alone, a dry run, then a real run confirmed against the account.
    migration = _load("v2_6_to_v2_7_migration", os.path.join(upgrade_dir, "v2.6_to_v2.7_migration.py"))
    logging.getLogger().handlers = []
    results["mainDryRun"] = run_main(migration, scratch, "main-dry", [], True)
    results["mainExecute"] = run_main(migration, scratch, "main-exec", ["--execute", "--confirm-account", ACCOUNT],
                                      False)
    results["mainExecuteWrongAccount"] = run_main(migration, scratch, "main-wrong",
                                                  ["--execute", "--confirm-account", "999999999999"], False)
    print(json.dumps(results))


if __name__ == "__main__":
    main()
