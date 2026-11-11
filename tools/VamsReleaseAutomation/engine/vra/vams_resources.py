# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve a deployed VAMS stack's physical resource names from SSM.

All DynamoDB tables and non-asset buckets are published under
``/{configName}-{baseStackName}/resourceNames`` (core stack output ``ResourceNamesSSMParamPrefixOutput``).
Asset buckets are rows of the ``s3AssetBucketsStorage`` table.
"""

from __future__ import annotations

from typing import Any

from vra.errors import PermissionDenied, NeedsHuman

TABLES = "dynamoTables"
BUCKETS = "s3Buckets"
LAMBDAS = "lambdaFunctions"
ASSET_BUCKETS_TABLE_KEY = "dynamoTables/s3AssetBucketsStorage"
REINDEXER_KEY = "lambdaFunctions/crOsReindexer"


def stack_output_prefix(cfn, stack: str) -> str | None:
    try:
        resp = cfn.describe_stacks(StackName=stack)
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc):
            return None
        raise
    for o in resp["Stacks"][0].get("Outputs", []) or []:
        if o.get("OutputKey") == "ResourceNamesSSMParamPrefixOutput":
            return o["OutputValue"]
    return None


# A stack in one of these states never finished its first create, so it holds no deployment to back up, inspect or
# migrate: ROLLBACK_COMPLETE is a failed CREATE (CloudFormation cannot update it; `cdk deploy` deletes and re-creates
# it), REVIEW_IN_PROGRESS is a change set that was never executed. A failed UPDATE ends in UPDATE_ROLLBACK_COMPLETE,
# which stays "deployed".
NEVER_CREATED = ("DELETE_COMPLETE", "ROLLBACK_COMPLETE", "REVIEW_IN_PROGRESS")


def stack_exists(cfn, stack: str) -> bool:
    """True when the stack holds a deployment (its first create completed)."""
    try:
        resp = cfn.describe_stacks(StackName=stack)
        return resp["Stacks"][0]["StackStatus"] not in NEVER_CREATED
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc):
            return False
        raise


def resource_names(ssm, prefix: str) -> dict[str, str]:
    """{relative key (e.g. 'dynamoTables/assetStorage'): value} for everything under the prefix."""
    prefix = prefix.rstrip("/")
    out: dict[str, str] = {}
    paginator = ssm.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=prefix, Recursive=True):
        for p in page.get("Parameters", []):
            out[p["Name"][len(prefix) + 1:]] = p["Value"]
    return out


def discover(ctx, env: str, role: str = "deploy", base_override: str | None = None) -> dict[str, Any]:
    """Resolve prefix (stack output, else derived), tables, buckets, lambdas for an env."""
    from vra import permissions
    cfn = ctx.aws.client(env, "cloudformation", role)
    ssm = ctx.aws.client(env, "ssm", role)
    stack = ctx.ledger.core_stack_name(env, base_override)
    try:
        prefix = stack_output_prefix(cfn, stack) or ctx.ledger.ssm_prefix(env, base_override)
        names = resource_names(ssm, prefix)
    except Exception as exc:  # noqa: BLE001
        if permissions.is_denied(exc):
            raise PermissionDenied(f"cannot resolve VAMS resource names for {env}",
                                   permissions.classify_exception(exc, "ssm"), env=env, role=role) from exc
        raise
    if not names:
        raise NeedsHuman(f"no SSM resource names under {prefix} for {env}; is the core stack {stack} deployed "
                         f"(VAMS >= 2.6 publishes them)?")
    tables = {k: v for k, v in names.items() if k.startswith(TABLES + "/")}
    buckets = {k: v for k, v in names.items() if k.startswith(BUCKETS + "/")}
    lambdas = {k: v for k, v in names.items() if k.startswith(LAMBDAS + "/")}
    return {"stack": stack, "prefix": prefix, "tables": tables, "buckets": buckets, "lambdas": lambdas,
            "all": names}


def asset_buckets(ddb, table: str) -> list[dict]:
    """Rows of s3AssetBucketsStorage: [{bucketId, bucketName, baseAssetsPrefix}]."""
    out = []
    kw: dict[str, Any] = {"TableName": table}
    while True:
        resp = ddb.scan(**kw)
        for it in resp.get("Items", []):
            row = {k: list(v.values())[0] for k, v in it.items() if isinstance(v, dict) and len(v) == 1}
            out.append({"bucketId": row.get("bucketId"), "bucketName": row.get("bucketName"),
                        "baseAssetsPrefix": row.get("baseAssetsPrefix")})
        if "LastEvaluatedKey" not in resp:
            break
        kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return out


def all_buckets(ctx, env: str, names: dict, role: str = "deploy") -> list[dict]:
    """Non-asset buckets from SSM plus asset buckets from the s3AssetBucketsStorage table."""
    out = [{"name": v, "key": k, "kind": "ssm"} for k, v in sorted(names["buckets"].items())]
    tbl = names["tables"].get(ASSET_BUCKETS_TABLE_KEY)
    if tbl:
        seen = {b["name"] for b in out}
        for row in asset_buckets(ctx.aws.client(env, "dynamodb", role), tbl):
            if row.get("bucketName") and row["bucketName"] not in seen:
                out.append({"name": row["bucketName"], "key": f"assetBucket/{row.get('bucketId')}", "kind": "asset",
                            "bucketId": row.get("bucketId"), "baseAssetsPrefix": row.get("baseAssetsPrefix")})
                seen.add(row["bucketName"])
    return out
