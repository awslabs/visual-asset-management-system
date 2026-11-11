# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra restore`: bring an env's data back to a checkpoint.

CDK owns the live tables, so a table cannot be swapped for a restored one. Instead each table is
restored to a temporary new table, the live table is cleared, items are copied back, and the temporary
table is deleted. S3 objects are put back to the recorded version (copy of the old version becomes the
latest; objects created after the checkpoint get a delete marker - reversible with versioning).
"""

from __future__ import annotations

import json
import re
import secrets
import time
from typing import Any, Iterator

from vra import approvals, backup, permissions, util
from vra.errors import NeedsHuman, PermissionDenied, UsageError
from vra.runner import env_lock, mark_clean, mark_dirty, workflow


def _wrap(exc, service, env, resource=None):
    if permissions.is_denied(exc):
        raise PermissionDenied(f"{service} denied during restore of {env}",
                               permissions.classify_exception(exc, service, resource), env=env) from exc
    raise exc


def key_schema(ddb, table: str) -> list[str]:
    return [k["AttributeName"] for k in ddb.describe_table(TableName=table)["Table"]["KeySchema"]]


def scan_items(ddb, table: str) -> Iterator[dict]:
    kw: dict[str, Any] = {"TableName": table}
    while True:
        resp = ddb.scan(**kw)
        yield from resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            return
        kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def batch_write(ddb, table: str, requests: list[dict], max_retries: int = 8) -> int:
    """BatchWriteItem in chunks of 25 with UnprocessedItems retry + backoff."""
    n = 0
    for i in range(0, len(requests), 25):
        chunk = requests[i:i + 25]
        pending = {table: chunk}
        attempt = 0
        while pending:
            resp = ddb.batch_write_item(RequestItems=pending)
            pending = resp.get("UnprocessedItems") or {}
            if pending:
                attempt += 1
                if attempt > max_retries:
                    raise RuntimeError(f"batch_write_item to {table} kept returning unprocessed items")
                time.sleep(min(2 ** attempt * 0.05, 5))
        n += len(chunk)
    return n


def clear_table(ddb, table: str, page: int = 500) -> int:
    """Delete every item, streaming (keys of one page at a time, never the whole table in memory)."""
    keys = key_schema(ddb, table)
    buf, n = [], 0
    for it in scan_items(ddb, table):
        buf.append({"DeleteRequest": {"Key": {k: it[k] for k in keys}}})
        if len(buf) >= page:
            n += batch_write(ddb, table, buf)
            buf = []
    if buf:
        n += batch_write(ddb, table, buf)
    return n


def copy_table(ddb, src: str, dst: str) -> int:
    buf, n = [], 0
    for it in scan_items(ddb, src):
        buf.append({"PutRequest": {"Item": it}})
        if len(buf) >= 500:
            n += batch_write(ddb, dst, buf)
            buf = []
    if buf:
        n += batch_write(ddb, dst, buf)
    return n


def wait_active(ddb, table: str, timeout: int = 3600) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = ddb.describe_table(TableName=table)["Table"]["TableStatus"]
        if st == "ACTIVE":
            return
        time.sleep(backup._poll_seconds())
    raise RuntimeError(f"table {table} not ACTIVE after {timeout}s")


TEMP_PREFIX = "vra-restore-"
DEFAULT_SETTLE_SECONDS = 60


def temp_table_name(ctx, env: str, key: str, tag: str) -> str:
    """Name of the temporary restore table: ``vra-restore-<baseStackName>-<region>-<tableKey>-<tag>``.

    It must match both IAM grants for the temporary-table lifecycle (DeleteTable is granted on these tables
    only, never on the CDK-owned live tables): iam/actions/restore.yaml ``table/vra-restore-${BaseStackName}-*``
    (the manifest placeholder is the region-suffixed base) and vra-deploy-role.yaml RestorePolicy
    ``table/vra-restore-${BaseStackName}-*`` (the raw CloudFormation parameter). The region-suffixed form
    satisfies both. DynamoDB table names: [a-zA-Z0-9_.-]{3,255}.
    """
    base = ctx.ledger.stack_suffix(env)
    # the whole key below dynamoTables/ (dynamoTables/legacy/tagStorage and dynamoTables/tagStorage are two tables;
    # their last segment alone collided, a restore drill finding)
    rel = key.split("/", 1)[1] if key.startswith("dynamoTables/") else key
    short = re.sub(r"[^A-Za-z0-9_.-]", "-", rel)
    name = f"{TEMP_PREFIX}{base}-{short}-{tag}"
    if len(name) > 255:
        name = f"{TEMP_PREFIX}{base}-{short[:180]}-{util.sha256_bytes(rel.encode())[:12]}-{tag}"[:255]
    return name


def start_temp_restores(ctx, env: str, tables: list[dict], tag: str, max_wait: int = 3600) -> dict:
    """Start RestoreTableFromBackup for every temporary table at once (a restore takes minutes per table, so one
    after the other is hours for a VAMS stack with ~50 tables). Already-started tables are left alone, so a resumed
    run does not restore twice. ``LimitExceededException`` (too many tables being created) waits and retries."""
    ddb = ctx.aws.client(env, "dynamodb")
    started, existing = [], []
    deadline = time.monotonic() + max_wait
    names = [temp_table_name(ctx, env, rec.get("key") or rec["name"], tag) for rec in tables]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise NeedsHuman(f"temporary restore table names collide ({', '.join(dupes)}); refusing to restore two tables "
                         f"through one temporary table")
    for rec, tmp in zip(tables, names):
        while True:
            try:
                check_temp_source(ddb.describe_table(TableName=tmp)["Table"], rec)
                existing.append(tmp)
                break
            except ddb.exceptions.ResourceNotFoundException:
                pass
            except Exception as exc:  # noqa: BLE001
                _wrap(exc, "dynamodb", env, tmp)
            try:
                ddb.restore_table_from_backup(TargetTableName=tmp, BackupArn=rec["backupArn"])
                started.append(tmp)
                break
            except Exception as exc:  # noqa: BLE001
                if "LimitExceeded" in type(exc).__name__ or "LimitExceeded" in str(exc):
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(float(backup._poll_seconds()))
                    continue
                if permissions.is_denied(exc):
                    _wrap(exc, "dynamodb", env, tmp)
                raise
    return {"started": started, "alreadyPresent": existing}


def check_temp_source(desc: dict, rec: dict) -> None:
    """An existing temporary table (resumed run) must have been restored from THIS table's checkpoint backup."""
    src = (desc.get("RestoreSummary") or {}).get("SourceBackupArn")
    if src and src != rec["backupArn"]:
        raise NeedsHuman(f"temporary table {desc.get('TableName')} was restored from {src}, not from the checkpoint "
                         f"backup of {rec['name']}; delete it or start a new restore run",
                         {"table": desc.get("TableName"), "restoredFrom": src, "expected": rec["backupArn"]})


def count_items(ddb, table: str) -> int:
    kw: dict[str, Any] = {"TableName": table, "Select": "COUNT"}
    n = 0
    while True:
        resp = ddb.scan(**kw)
        n += int(resp.get("Count", 0))
        if "LastEvaluatedKey" not in resp:
            return n
        kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def verify_tables(ctx, env: str, results: dict) -> dict:
    """Right after the reload, before any S3 object is touched and while writes are frozen: every live table
    holds exactly the items copied back from its restored temporary table."""
    ddb = ctx.aws.client(env, "dynamodb")
    bad, tables, items = [], 0, 0
    for r in results["tables"]:
        if r.get("dryRun"):
            continue
        live = count_items(ddb, r["table"])
        tables += 1
        items += live
        if live != r["copied"]:
            bad.append(f"{r['table']}: {live} items, restored copy had {r['copied']}")
    out = {"tables": tables, "items": items, "mismatches": bad}
    if bad:
        raise NeedsHuman(f"restore verification failed on {env}: {'; '.join(bad[:5])}", out)
    return out


def recorded_keys(cdir, b: dict) -> set[str]:
    keys = set()
    with open(cdir / b["manifest"], encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rr = json.loads(line)
                if not rr.get("deleted"):
                    keys.add(rr["key"])
    return keys


def verify_buckets(ctx, env: str, cdir, buckets: list[dict]) -> dict:
    """After the bucket rollback: each bucket's latest (non-deleted) objects are exactly the checkpoint's."""
    s3 = ctx.aws.client(env, "s3")
    bad, objs = [], {}
    for b in buckets:
        recorded = recorded_keys(cdir, b)
        current = set()
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=b["name"]):
            current |= {o["Key"] for o in page.get("Contents", [])}
        objs[b["name"]] = {"recorded": len(recorded), "current": len(current)}
        if current != recorded:
            bad.append(f"s3://{b['name']}: {len(current)} objects, checkpoint recorded {len(recorded)} "
                       f"({len(current - recorded)} extra, {len(recorded - current)} missing)")
    out = {"buckets": objs, "mismatches": bad}
    if bad:
        raise NeedsHuman(f"restore verification failed on {env}: {'; '.join(bad[:5])}", out)
    return out


def settle(ctx, env: str, results: dict, seconds: int, max_rounds: int = 10) -> dict:
    """Rolling objects back writes new S3 versions, and VAMS's own S3 event processing reacts to them (for
    example new rows in the file version-history table). Wait until the item counts stop changing (still under
    the write freeze), then report per table what VAMS added or removed after the reload. These derived writes
    describe the restored objects' new versions; they are reported, not undone."""
    ddb = ctx.aws.client(env, "dynamodb")

    def snapshot():
        return {r["table"]: count_items(ddb, r["table"]) for r in results["tables"] if not r.get("dryRun")}
    wait = float(seconds) if float(backup._poll_seconds()) > 0 else 0.0
    rounds, prev = 0, None
    while True:
        if wait:
            time.sleep(wait)
        cur = snapshot()
        rounds += 1
        if cur == prev or rounds >= max_rounds or not wait:
            break
        prev = cur
    derived = {r["table"]: {"copied": r["copied"], "live": cur[r["table"]], "delta": cur[r["table"]] - r["copied"]}
               for r in results["tables"] if not r.get("dryRun") and cur[r["table"]] != r["copied"]}
    return {"settleSeconds": seconds, "rounds": rounds, "stable": cur == prev or not wait,
            "derivedWrites": derived}


def _settle_step(ctx, run, env, results, secs, sc) -> dict:
    res = settle(ctx, env, results, secs)
    if res["derivedWrites"]:
        msg = (f"after the object rollback VAMS's event processing changed {len(res['derivedWrites'])} table(s) of "
               f"{env}: " + ", ".join(f"{t.split('-')[-2] if '-' in t else t} {v['delta']:+d}"
                                      for t, v in list(res["derivedWrites"].items())[:5])
               + " (rows for the new object versions the rollback created; reported, not undone)")
        run.state.setdefault("notes", []).append(msg)
        run.save()
        sc.log(msg)
    return res


def restore_table(ctx, env: str, rec: dict, tag: str, keep_restored: bool = False) -> dict:
    ddb = ctx.aws.client(env, "dynamodb")
    tmp = temp_table_name(ctx, env, rec.get("key") or rec["name"], tag)
    try:
        try:
            check_temp_source(ddb.describe_table(TableName=tmp)["Table"], rec)
        except ddb.exceptions.ResourceNotFoundException:
            ddb.restore_table_from_backup(TargetTableName=tmp, BackupArn=rec["backupArn"])
        wait_active(ddb, tmp)
        check_temp_source(ddb.describe_table(TableName=tmp)["Table"], rec)
        cleared = clear_table(ddb, rec["name"])
        copied = copy_table(ddb, tmp, rec["name"])
        if not keep_restored:
            delete_temp_table(ddb, env, tmp)
        return {"table": rec["name"], "cleared": cleared, "copied": copied, "temp": tmp, "tempDeleted": not keep_restored}
    except PermissionDenied:
        raise
    except Exception as exc:  # noqa: BLE001
        if permissions.is_denied(exc):
            _wrap(exc, "dynamodb", env, rec["name"])
        raise


def restored_map_path(cp_dir, brec: dict):
    return cp_dir / (brec["manifest"].rsplit(".versions.jsonl", 1)[0] + ".restored.json")


def restore_bucket(ctx, env: str, cp_dir, brec: dict) -> dict:
    """Make each recorded version the latest again (CopyObject of the old version) and add delete markers for
    objects created after the checkpoint. The version id of every copy is recorded next to the checkpoint
    (``<bucket>.restored.json``): with SSE-KMS the ETag differs per copy, so this is how a later restore of the same
    checkpoint recognises that an object already holds the recorded content and does not copy it again."""
    s3 = ctx.aws.client(env, "s3")
    bucket = brec["name"]
    mpath = restored_map_path(cp_dir, brec)
    copies: dict[str, dict] = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    recorded: dict[str, dict] = {}
    with open(cp_dir / brec["manifest"], encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                recorded[r["key"]] = r
    current: dict[str, dict] = {}
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket):
        for v in page.get("Versions", []):
            if v.get("IsLatest"):
                current[v["Key"]] = {"versionId": v.get("VersionId"), "etag": v.get("ETag"), "size": v.get("Size")}
        for dm in page.get("DeleteMarkers", []):
            if dm.get("IsLatest"):
                current[dm["Key"]] = {"versionId": dm.get("VersionId"), "deleted": True}
    restored = removed = unchanged = 0
    for key, r in recorded.items():
        cur = current.get(key)
        if r.get("deleted"):
            if cur and not cur.get("deleted"):
                s3.delete_object(Bucket=bucket, Key=key)
                removed += 1
            continue
        own_copy = (copies.get(key) or {}).get(r["versionId"]) == (cur or {}).get("versionId")
        if cur and not cur.get("deleted") and (cur.get("versionId") == r["versionId"] or own_copy or (
                r.get("etag") and cur.get("etag") == r.get("etag") and cur.get("size") == r.get("size"))):
            # same version, or already a copy of it (same ETag and size, e.g. from an earlier restore run):
            # copying again would only add a version (and VAMS event-processing rows) with the same content
            unchanged += 1
            continue
        resp = s3.copy_object(Bucket=bucket, Key=key,
                              CopySource={"Bucket": bucket, "Key": key, "VersionId": r["versionId"]},
                              MetadataDirective="COPY")
        if resp.get("VersionId"):
            copies.setdefault(key, {})[r["versionId"]] = resp["VersionId"]
        restored += 1
    for key, cur in current.items():
        if key not in recorded and not cur.get("deleted"):
            s3.delete_object(Bucket=bucket, Key=key)  # delete marker; prior versions retained
            removed += 1
    if restored:
        util.atomic_write(mpath, json.dumps(copies, indent=1, sort_keys=True))
        if getattr(ctx, "store", None) is not None:
            ctx.store.push_file(mpath)
    return {"bucket": bucket, "restored": restored, "deleteMarkers": removed, "unchanged": unchanged}


# Tables that hold access: API keys, auth entities, roles and role assignments. Restoring them would silently
# bring back keys revoked and permissions removed after the checkpoint, so they are left out unless asked for.
AUTH_TABLES = ("dynamoTables/apiKeyStorage", "dynamoTables/authEntitiesStorage", "dynamoTables/rolesStorage",
               "dynamoTables/userRolesStorage")


def do_restore(ctx, run, env: str, cid: str, approval: str | None = None, prefix: str = "",
               skip_gate: bool = False, keep_restored: bool = False, write_freeze: str | None = None,
               abort_executions: bool = False, unattended: bool = False, include_auth_tables: bool = False) -> dict:
    """Restore ``env`` to checkpoint ``cid``.

    Order: gate -> preflight -> safety checkpoint -> list running workflow executions (abort only with the flag)
    -> start every temporary-table restore at once -> [write freeze: clear + reload each live table, verify the
    item counts against the restored copies (strict, before any object changes), roll each bucket back, verify the
    object sets against the checkpoint (strict), wait for VAMS's own S3 event processing to settle and report the
    rows it derived from the rolled-back objects] -> reindex -> delete the temporary tables -> history. The freeze
    is lifted when the frozen block ends, also on failure.
    """
    from vra import freeze
    cp = backup.load_checkpoint(ctx, cid)
    if cp["env"] != env:
        raise UsageError(f"checkpoint {cid} belongs to env {cp['env']}, not {env}")

    gate: dict = {}
    if not skip_gate:
        def _gate(sc):
            rec = approvals.check(ctx, env, f"restore:{cid}", "restore", approval)
            if include_auth_tables and rec and "restore-auth-tables" not in (rec.get("scope") or []):
                raise NeedsHuman(f"--include-auth-tables on {env} needs the restore approval to name scope "
                                 f"restore-auth-tables (it brings back revoked API keys and removed permissions)")
            approvals.record(run, rec)
            return {"approval": rec and rec.get("path"), "approvalFields": approvals.history_fields(rec)}
        gate = run.step(f"{prefix}restore.gate", _gate, "approval gate", revalidate=True)

    from vra import buckets
    # never roll back a bucket another env also uses (a prod-class owner's bucket is never written)
    run.step(f"{prefix}restore.shared-buckets",
             lambda sc: buckets.shared_guard(ctx, run, env, [b["name"] for b in cp["buckets"]], f"restore of {cid}"),
             "buckets are not shared with another env", revalidate=True)
    run.step(f"{prefix}restore.preflight", lambda sc: permissions.preflight(ctx, env, "restore"), "preflight restore")
    buckets.require(ctx, run, env, ["backup", "restore"], f"{prefix}restore.registered-buckets",
                    f"restore of {env}")
    run.step(f"{prefix}restore.safety-checkpoint",
             lambda sc: backup.create_checkpoint(ctx, env, run, sc, "pre-restore", strict=backup.strict_for(ctx, env)),
             "safety checkpoint")
    tag = run.step(f"{prefix}restore.tag", lambda sc: secrets.token_hex(3), "restore tag")
    tables = [t for t in cp["tables"] if t.get("backupArn")]
    auth_skipped = [] if include_auth_tables else [t for t in tables if (t.get("key") or "") in AUTH_TABLES]
    tables = [t for t in tables if t not in auth_skipped]
    if auth_skipped:
        run.state.setdefault("notes", []).append(
            f"restore leaves {len(auth_skipped)} access table(s) as they are now ({', '.join(t['key'] for t in auth_skipped)}): "
            f"keys revoked and permissions removed after the checkpoint stay revoked (--include-auth-tables restores them)")
    if not tables and not cp["buckets"]:
        raise NeedsHuman(f"checkpoint {cid} has nothing restorable")
    results: dict[str, Any] = {"tables": [], "buckets": [],
                               "skipped": [{"table": t["key"], "reason": "access table (default)"} for t in auth_skipped]}
    freeze.workflow_executions_step(ctx, run, env, f"{prefix}restore.workflow-executions", "restore",
                                    abort=abort_executions)
    if not ctx.dry_run:
        run.step(f"{prefix}restore.start", lambda sc: start_temp_restores(ctx, env, tables, tag),
                 f"start {len(tables)} temporary-table restores")
    cdir = backup.checkpoint_dir(ctx, cid)
    restorable = []
    for b in cp["buckets"]:
        if b.get("versioning") != "Enabled" or not b.get("manifest"):
            results["skipped"].append(f"{b['name']}: versioning not enabled at checkpoint time")
        else:
            restorable.append(b)
    with freeze.Frozen(ctx, run, env, f"{prefix}restore.write-freeze", f"restore of {cid}",
                       override=write_freeze, unattended=unattended,
                       complete_step=None if ctx.dry_run else f"{prefix}restore.settle") as fr:
        if not ctx.dry_run:
            mark_dirty(run, env, f"restore of checkpoint {cid} (live tables cleared and reloaded)")
        for t in tables:
            if ctx.dry_run:
                results["tables"].append({"table": t["name"], "dryRun": True})
                continue
            # temporary tables are kept until the whole restore (buckets, reindex) is done: a failure half-way
            # leaves the restored copy in place for a retry instead of the only good copy being gone
            results["tables"].append(run.step(f"{prefix}restore.table.{t['key']}",
                                              lambda sc, t=t: restore_table(ctx, env, t, tag, keep_restored=True),
                                              f"restore {t['name']}"))
        if not ctx.dry_run:
            # strict, before any object changes: rolling objects back triggers VAMS's own event processing
            results["verify"] = run.step(f"{prefix}restore.verify", lambda sc: verify_tables(ctx, env, results),
                                         "verify item counts against the restored copies")
        for b in restorable:
            if ctx.dry_run:
                results["buckets"].append({"bucket": b["name"], "dryRun": True})
                continue
            results["buckets"].append(run.step(f"{prefix}restore.bucket.{b['name']}",
                                               lambda sc, b=b: _bucket_step(ctx, env, cdir, b),
                                               f"restore {b['name']}"))
        if not ctx.dry_run:
            results["verifyBuckets"] = run.step(f"{prefix}restore.verify-buckets",
                                                lambda sc: verify_buckets(ctx, env, cdir, restorable),
                                                "verify object sets against the checkpoint")
            secs = int((ctx.ledger.env(env).get("writeFreeze") or {}).get("settleSeconds", DEFAULT_SETTLE_SECONDS))
            results["settle"] = run.step(f"{prefix}restore.settle", lambda sc: _settle_step(ctx, run, env, results,
                                                                                          secs, sc),
                                         "wait for VAMS event processing, report derived writes")
    results["writeFreeze"] = fr.history_fields()["writeFreeze"]
    run.step(f"{prefix}restore.reindex", lambda sc: trigger_reindex(ctx, env, sc=sc, run=run, what="the restore"),
             "OpenSearch reindex")

    def _cleanup(sc):
        if ctx.dry_run or keep_restored:
            return {"kept": [r.get("temp") for r in results["tables"] if r.get("temp")]}
        ddb = ctx.aws.client(env, "dynamodb")
        deleted = []
        for r in results["tables"]:
            if r.get("temp") and not r.get("tempDeleted"):
                delete_temp_table(ddb, env, r["temp"])
                deleted.append(r["temp"])
        return {"deleted": deleted}
    run.step(f"{prefix}restore.cleanup", _cleanup, "delete temporary restore tables")

    def _record(sc):
        ctx.ledger.append_history(env, {"action": "restore", "checkpoint": cid, "runId": run.run_id,
                                        "result": "SUCCEEDED", "tables": len(results["tables"]),
                                        "buckets": len(results["buckets"]), "initiator": ctx.initiator,
                                        "verified": (results.get("verify") or {}).get("items"),
                                        "derivedWrites": len((results.get("settle") or {}).get("derivedWrites") or {}),
                                        **fr.history_fields(), **(gate.get("approvalFields") or {})},
                                  dry_run=ctx.dry_run)
        return True
    run.step(f"{prefix}restore.record", _record, "record history")
    mark_clean(run, env)
    return {"checkpoint": cid, **results}


def delete_temp_table(ddb, env: str, name: str, max_wait: int = 900) -> None:
    """DeleteTable, idempotent: a table that is gone or already being deleted (a retried or resumed call) counts as
    deleted (a restore drill finding); too many concurrent table operations waits and retries."""
    deadline = time.monotonic() + max_wait
    while True:
        try:
            ddb.delete_table(TableName=name)
            return
        except Exception as exc:  # noqa: BLE001
            code = permissions.client_error_parts(exc)[0] or type(exc).__name__
            text = str(exc)
            if "ResourceNotFound" in code or "being deleted" in text:
                return
            if ("LimitExceeded" in code or "ResourceInUse" in code) and time.monotonic() < deadline:
                time.sleep(float(backup._poll_seconds()))
                continue
            if permissions.is_denied(exc):
                _wrap(exc, "dynamodb", env, name)
            raise


def _bucket_step(ctx, env, cdir, b):
    try:
        return restore_bucket(ctx, env, cdir, b)
    except Exception as exc:  # noqa: BLE001
        if permissions.is_denied(exc):
            _wrap(exc, "s3", env, b["name"])
        raise


def trigger_reindex(ctx, env: str, role: str = "deploy", clear_indexes: bool = True, base: str | None = None,
                    sc=None, run=None, what: str = "the") -> dict:
    """Reindex ``env`` and confirm the result (vra.reindex). A restore or data copy does not run ``cdk deploy``, so
    ``app.openSearch.reindexOnCdkDeploy`` never covers it: the data changed after the last deploy's reindex."""
    from vra import reindex
    if ctx.dry_run:
        return {"dryRun": True}
    mode = reindex.settings(ctx.ledger.env(env))["mode"]
    if mode == "direct" and sc is not None:
        cur = ctx.ledger.current_deploy(env) or {}
        res = reindex.run(ctx, env, sc, mode="direct", clear_indexes=clear_indexes, build_id=cur.get("build"),
                          base=base, cfg=reindex.deployed_config(ctx, env))
    else:
        res = reindex.lambda_reindex(ctx, env, role, clear_indexes, base)
    return reindex.note_result(run, env, res, what) if run is not None else res


@workflow("restore")
def wf_restore(ctx, run, args):
    from vra import freeze
    freeze.record_ack(run, ctx, bool(args.get("writesStopped")))
    with env_lock(ctx, run, args["env"]):
        return do_restore(ctx, run, args["env"], args["checkpoint"], args.get("approval"),
                          write_freeze=args.get("writeFreeze"),
                          abort_executions=bool(args.get("abortWorkflowExecutions")),
                          include_auth_tables=bool(args.get("includeAuthTables")))
