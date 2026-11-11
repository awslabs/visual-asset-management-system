# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra backup`: checkpoint an env before change.

DynamoDB on-demand backup for every table under the SSM prefix, PITR status per table, S3 versioning
check + object-version manifest per bucket, optional Cognito user export (attributes only, no
secrets). Writes ``.vra/checkpoints/<id>/checkpoint.json`` and a ledger history record.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from vra import permissions, util, vams_resources
from vra.errors import PermissionDenied
from vra.runner import env_lock, workflow


def checkpoint_id(env: str, now) -> str:
    return f"ckpt-{env}-{now.strftime('%Y%m%dT%H%M%SZ')}"


def checkpoint_dir(ctx, cid: str) -> Path:
    return ctx.checkpoints_dir / cid


def load_checkpoint(ctx, cid: str) -> dict:
    p = checkpoint_dir(ctx, cid) / "checkpoint.json"
    if not p.exists() and ctx.store is not None:
        ctx.store.pull_prefix(f"checkpoints/{cid}/")
    if not p.exists():
        from vra.errors import UsageError
        raise UsageError(f"unknown checkpoint {cid}")
    return util.read_json(p)


def _denied(exc, service, env, resource=None):
    if permissions.is_denied(exc):
        return PermissionDenied(f"{service} access denied during backup of {env}",
                                permissions.classify_exception(exc, service, resource), env=env)
    return None


def backup_name(cid: str, key: str) -> str:
    # DynamoDB backup names: [a-zA-Z0-9_.-]+, max 255
    return re.sub(r"[^A-Za-z0-9_.-]", "-", f"vra-{cid}-{key.split('/')[-1]}")[:255]


def degraded_allowed(ctx, env: str) -> bool:
    return bool((ctx.ledger.env(env).get("backup") or {}).get("allowDegraded"))


def strict_for(ctx, env: str) -> bool:
    """Approval-gated envs (and data-copy / restore targets that are gated) need a complete checkpoint."""
    return bool(ctx.ledger.env(env).get("requiresApproval")) and not degraded_allowed(ctx, env)


def create_checkpoint(ctx, env: str, run, sc, label: str = "manual", wait_seconds: int = 900,
                      strict: bool = False) -> dict:
    """``strict``: a checkpoint with warnings (PITR off, a backup not AVAILABLE in time, S3 versioning off) is
    refused (NeedsHuman) instead of recorded, because the change it protects could not be undone with it."""
    e = ctx.ledger.env(env)
    cid = checkpoint_id(env, ctx.now())
    if label != "manual":
        cid = f"{cid}-{label}"
    cdir = checkpoint_dir(ctx, cid)
    if ctx.dry_run:
        return {"id": cid, "dryRun": True}
    names = vams_resources.discover(ctx, env)
    ddb = ctx.aws.client(env, "dynamodb")
    s3 = ctx.aws.client(env, "s3")
    current = ctx.ledger.current_deploy(env) or {}
    cp: dict[str, Any] = {"id": cid, "env": env, "account": e["account"], "region": e["region"],
                          "partition": e.get("partition") or ctx.aws.partition(env), "createdAt": util.now_iso(),
                          "runId": run.run_id, "label": label, "build": current.get("build"),
                          "vamsVersion": current.get("vamsVersion"), "ssmPrefix": names["prefix"],
                          "stack": names["stack"], "tables": [], "buckets": [], "cognito": None, "warnings": []}
    pending = []
    for key, table in sorted(names["tables"].items()):
        rec: dict[str, Any] = {"key": key, "name": table}
        try:
            cb = ddb.describe_continuous_backups(TableName=table)["ContinuousBackupsDescription"]
            pitr = cb.get("PointInTimeRecoveryDescription", {})
            rec["pitr"] = pitr.get("PointInTimeRecoveryStatus", "DISABLED")
            rec["pitrEarliest"] = str(pitr.get("EarliestRestorableDateTime") or "") or None
            if rec["pitr"] != "ENABLED":
                cp["warnings"].append(f"PITR disabled on {table} ({key})")
            resp = ddb.create_backup(TableName=table, BackupName=backup_name(cid, key))
            rec["backupArn"] = resp["BackupDetails"]["BackupArn"]
            rec["backupStatus"] = resp["BackupDetails"].get("BackupStatus")
            pending.append(rec)
        except Exception as exc:  # noqa: BLE001
            d = _denied(exc, "dynamodb", env, table)
            if d:
                raise d from exc
            if "not found" in str(exc).lower() or "ResourceNotFound" in str(exc):
                rec["skipped"] = "table not found (legacy name kept in SSM)"
            else:
                raise
        cp["tables"].append(rec)
    deadline = time.monotonic() + wait_seconds
    for rec in pending:
        while rec.get("backupStatus") != "AVAILABLE" and time.monotonic() < deadline:
            d = ddb.describe_backup(BackupArn=rec["backupArn"])["BackupDescription"]["BackupDetails"]
            rec["backupStatus"] = d.get("BackupStatus")
            rec["sizeBytes"] = d.get("BackupSizeBytes")
            if rec["backupStatus"] != "AVAILABLE":
                time.sleep(float(_poll_seconds()))
        if rec.get("backupStatus") != "AVAILABLE":
            cp["warnings"].append(f"backup of {rec['name']} not AVAILABLE after {wait_seconds}s "
                                  f"({rec.get('backupStatus')})")
    for b in vams_resources.all_buckets(ctx, env, names):
        brec = dict(b)
        try:
            v = s3.get_bucket_versioning(Bucket=b["name"]).get("Status", "Disabled")
            brec["versioning"] = v
            if v == "Enabled":
                mpath = cdir / "s3" / f"{b['name']}.versions.jsonl"
                brec["manifest"] = str(mpath.relative_to(cdir))
                brec["objects"] = write_version_manifest(s3, b["name"], mpath)
            else:
                cp["warnings"].append(f"S3 versioning not enabled on {b['name']}; objects cannot be restored to "
                                      f"this checkpoint")
        except Exception as exc:  # noqa: BLE001
            d = _denied(exc, "s3", env, b["name"])
            if d:
                raise d from exc
            raise
        cp["buckets"].append(brec)
    if e.get("cognitoExport"):
        cp["cognito"] = export_cognito(ctx, env, names, cdir, cp)
    cp["strict"] = strict
    util.write_json(cdir / "checkpoint.json", cp)
    if strict and cp["warnings"]:
        from vra.errors import NeedsHuman
        raise NeedsHuman(f"checkpoint {cid} of {env} is incomplete: {'; '.join(cp['warnings'][:5])}. {env} is "
                         f"approval-gated, so vra does not change it without a complete checkpoint. Fix the cause "
                         f"(enable PITR / S3 versioning, wait for backups) and `vra resume`, or set "
                         f"envs/{env} backup.allowDegraded: true (recorded)", {"checkpoint": cid,
                                                                               "warnings": cp["warnings"]})
    sc.artifact(cdir / "checkpoint.json")
    if ctx.store is not None:
        ctx.store.push_dir(cdir)
    ctx.ledger.append_history(env, {"action": "backup", "checkpoint": cid, "runId": run.run_id, "result": "SUCCEEDED",
                                    "tables": len(cp["tables"]), "buckets": len(cp["buckets"]),
                                    "bucketNames": sorted(b["name"] for b in cp["buckets"]),
                                    "warnings": len(cp["warnings"])})
    return {"id": cid, "tables": len(cp["tables"]), "buckets": len(cp["buckets"]), "warnings": cp["warnings"]}


def _poll_seconds() -> float:
    import os
    return float(os.environ.get("VRA_POLL_SECONDS", "5"))


def write_version_manifest(s3, bucket: str, path: Path) -> int:
    """JSONL of the latest version per key {key, versionId, etag, size}; delete-markers recorded as deleted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    paginator = s3.get_paginator("list_object_versions")
    with open(path, "w", encoding="utf-8") as fh:
        for page in paginator.paginate(Bucket=bucket):
            for v in page.get("Versions", []):
                if v.get("IsLatest"):
                    fh.write(json.dumps({"key": v["Key"], "versionId": v.get("VersionId"), "etag": v.get("ETag"),
                                         "size": v.get("Size")}) + "\n")
                    n += 1
            for dm in page.get("DeleteMarkers", []):
                if dm.get("IsLatest"):
                    fh.write(json.dumps({"key": dm["Key"], "versionId": dm.get("VersionId"), "deleted": True}) + "\n")
    return n


def export_cognito(ctx, env: str, names: dict, cdir: Path, cp: dict) -> dict | None:
    cfn = ctx.aws.client(env, "cloudformation")
    pool = None
    try:
        for o in cfn.describe_stacks(StackName=names["stack"])["Stacks"][0].get("Outputs", []) or []:
            if re.search(r"UserPoolId", o.get("OutputKey", "")):
                pool = o["OutputValue"]
                break
    except Exception as exc:  # noqa: BLE001
        cp["warnings"].append(f"cognito export skipped: {exc}")
        return None
    if not pool:
        cp["warnings"].append("cognito export skipped: no *UserPoolId* stack output (external IdP?)")
        return None
    idp = ctx.aws.client(env, "cognito-idp")
    users = []
    token = None
    while True:
        kw = {"UserPoolId": pool, "Limit": 60}
        if token:
            kw["PaginationToken"] = token
        resp = idp.list_users(**kw)
        for u in resp.get("Users", []):
            users.append({"username": u["Username"], "status": u.get("UserStatus"), "enabled": u.get("Enabled"),
                          "attributes": {a["Name"]: a["Value"] for a in u.get("Attributes", [])}})
        token = resp.get("PaginationToken")
        if not token:
            break
    util.write_json(cdir / "cognito-users.json", {"userPoolId": pool, "users": users})
    return {"userPoolId": pool, "users": len(users), "file": "cognito-users.json",
            "note": "attributes only; passwords cannot be exported"}


def do_backup(ctx, run, env: str, prefix: str = "", label: str = "manual") -> dict:
    def _pre(sc):
        from vra import permissions as perm
        return perm.preflight(ctx, env, "backup")
    run.step(f"{prefix}backup.preflight.{env}", _pre, f"preflight backup {env}")
    from vra import buckets
    buckets.require(ctx, run, env, ["backup"], f"{prefix}backup.registered-buckets.{env}", f"checkpoint of {env}")
    res = run.step(f"{prefix}backup.{env}", lambda sc: create_checkpoint(ctx, env, run, sc, label),
                   f"checkpoint {env}")
    run.state["checkpoint"] = res.get("id")
    run.save()
    return res


@workflow("backup")
def wf_backup(ctx, run, args):
    with env_lock(ctx, run, args["env"]):
        return do_backup(ctx, run, args["env"])
