# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Data copy between VAMS deployments.

`vra data-copy --from prod --to test`: full copy, approval gated, cross-account:
  PITR export of every source table to a staging bucket the target can read (+ KMS grant for the target
  role) -> clear target tables (CDK-owned, so load into the existing tables) -> batch-write with id /
  bucket / SSM-name / account+region-in-ARN remapping -> S3 copy per asset bucket in two hops (source
  role: source -> staging ``vra/<run>/assets/``; target role: staging -> target; S3 Batch Operations
  above a threshold, CopyObject below) -> OpenSearch reindex -> audit record. Source reads always use
  the source env's role; the source data owner approves ``data-copy-to:<target>``.
User-role assignments optional, Cognito users never copied. Mask/subset hooks pluggable, off by default.

A/B slot copy (same account) reuses the same remap/load path with a direct scan instead of an export.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import secrets
import shlex
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from vra import approvals, backup, buckets, permissions, restore, util, vams_resources
from vra.errors import NeedsHuman, PermissionDenied, UsageError, VraError
from vra.runner import env_lock, mark_clean, mark_dirty, workflow

NEVER_COPY = {"dynamoTables/appFeatureEnabledStorage"}
NOT_ACROSS_ENVS = {"dynamoTables/apiKeyStorage", "dynamoTables/s3AssetBucketsStorage"}
OPTIONAL_USER_ROLES = {"dynamoTables/userRolesStorage"}
COPY_BUCKET_KEYS = {"s3Buckets/assetAuxiliary"}
ARN_IN_TEXT_RE = re.compile(r"arn:(?P<p>[a-z0-9-]+):(?P<svc>[a-z0-9-]+):(?P<region>[a-z0-9-]*):(?P<acct>\d{12})?:")


# ===================================================================== remapping
class Remapper:
    """Rewrites every string inside a DynamoDB-typed item."""

    def __init__(self, strings: dict[str, str], src_account: str, dst_account: str, src_region: str,
                 dst_region: str, src_partition: str, dst_partition: str):
        self.pairs = sorted(((a, b) for a, b in strings.items() if a and b and a != b), key=lambda x: -len(x[0]))
        self.src_account, self.dst_account = src_account, dst_account
        self.src_region, self.dst_region = src_region, dst_region
        self.src_partition, self.dst_partition = src_partition, dst_partition

    def text(self, s: str) -> str:
        for a, b in self.pairs:
            if a in s:
                s = s.replace(a, b)
        if "arn:" in s:
            def rep(m: re.Match) -> str:
                acct = m.group("acct") or ""
                if acct and acct != self.src_account:
                    return m.group(0)
                region = m.group("region")
                if region == self.src_region:
                    region = self.dst_region
                part = self.dst_partition if m.group("p") == self.src_partition else m.group("p")
                return f"arn:{part}:{m.group('svc')}:{region}:{self.dst_account if acct else ''}:"
            s = ARN_IN_TEXT_RE.sub(rep, s)
        return s

    def value(self, v: dict) -> dict:
        (t, x), = v.items()
        if t == "S":
            return {"S": self.text(x)}
        if t == "SS":
            return {"SS": sorted({self.text(i) for i in x})}
        if t == "L":
            return {"L": [self.value(i) for i in x]}
        if t == "M":
            return {"M": {k: self.value(i) for k, i in x.items()}}
        return v

    def item(self, it: dict) -> dict:
        return {k: self.value(v) for k, v in it.items()}


def item_in_databases(it: dict, ids: set[str]) -> bool:
    """Subset filter: keep items of the selected databases and items not scoped to any database."""
    db = it.get("databaseId", {}).get("S")
    if db is not None:
        return db in ids
    # composite attributes such as "databaseId:assetId" = "<db>:<asset>"
    for k, v in it.items():
        s = v.get("S") if isinstance(v, dict) else None
        if s and k.startswith("databaseId:"):
            return s.split(":", 1)[0] in ids
    return True


def asset_prefixes(items: Iterable[dict]) -> set[str]:
    out = set()
    for it in items:
        loc = it.get("assetLocation", {}).get("M", {})
        key = loc.get("Key", {}).get("S")
        if key:
            out.add(key)
    return out


# ===================================================================== sides
def side(ctx, env: str, base: str | None, role: str) -> dict:
    names = vams_resources.discover(ctx, env, role, base)
    e = ctx.ledger.env(env)
    return {"env": env, "base": base, "role": role, "names": names, "account": e["account"], "region": e["region"],
            "partition": e.get("partition") or ctx.aws.partition(env),
            "buckets": vams_resources.all_buckets(ctx, env, names, role)}


def pair_asset_buckets(src: dict, dst: dict, bucket_map: dict | None = None) -> list[tuple[dict, dict]]:
    """Every registered SOURCE asset bucket maps to a distinct registered TARGET asset bucket.

    ``dataCopy.bucketMap`` {sourceBucket: targetBucket} decides when set; without it buckets pair automatically
    only when each side has exactly one. Anything else stops with NeedsHuman naming every unmapped bucket and the
    bucketMap fix: an unmapped bucket is never skipped (its rows would point at objects that were not copied).
    """
    s_assets = [b for b in src["buckets"] if b["kind"] == "asset"]
    d_assets = [b for b in dst["buckets"] if b["kind"] == "asset"]
    by_name = {b["name"]: b for b in d_assets}
    pairs: list[tuple[dict, dict]] = []
    unmapped: list[dict] = []
    if bucket_map:
        for sb in s_assets:
            tgt = bucket_map.get(sb["name"])
            if not tgt:
                unmapped.append({"bucket": sb["name"], "why": "no dataCopy.bucketMap entry"})
            elif tgt not in by_name:
                unmapped.append({"bucket": sb["name"], "why": f"maps to {tgt}, which is not a registered asset bucket "
                                                              f"of {dst['env']}"})
            else:
                pairs.append((sb, by_name[tgt]))
    elif len(s_assets) == 1 and len(d_assets) == 1:
        pairs = [(s_assets[0], d_assets[0])]
    elif s_assets:
        why = (f"{len(s_assets)} source vs {len(d_assets)} target asset buckets; automatic pairing needs exactly one "
               f"on each side")
        unmapped = [{"bucket": b["name"], "why": why} for b in s_assets]
    targets: dict[str, list[str]] = {}
    for sb, db in pairs:
        targets.setdefault(db["name"], []).append(sb["name"])
    dup = {t: s for t, s in targets.items() if len(s) > 1}
    if unmapped or dup:
        needs = [u["bucket"] for u in unmapped] + sorted({s for v in dup.values() for s in v})
        fix = "\n".join(f"    {b}: <one of {', '.join(sorted(by_name)) or 'no target asset bucket'}>" for b in needs)
        parts = [f"{u['bucket']} ({u['why']})" for u in unmapped]
        parts += [f"{', '.join(s)} all map to {t} (each source needs its own target bucket)" for t, s in dup.items()]
        raise NeedsHuman(
            f"data copy {src['env']} -> {dst['env']} stopped: unmapped source asset bucket(s): {'; '.join(parts)}. "
            f"Map every registered source asset bucket to a distinct target asset bucket in envs/{dst['env']}/env.yaml"
            f" (register a bucket in {dst['env']} first if it has too few):\n  dataCopy:\n    bucketMap:\n{fix}",
            {"unmapped": unmapped, "duplicateTargets": dup, "targetAssetBuckets": sorted(by_name),
             "fix": f"envs/{dst['env']}/env.yaml dataCopy.bucketMap"})
    return pairs


def build_remap(src: dict, dst: dict, bucket_map: dict | None = None) -> tuple[Remapper, dict]:
    strings: dict[str, str] = {}
    for k, v in src["names"]["all"].items():
        if k in dst["names"]["all"]:
            strings[v] = dst["names"]["all"][k]
    pairs = pair_asset_buckets(src, dst, bucket_map)
    for sb, db in pairs:
        if (sb.get("baseAssetsPrefix") or "") != (db.get("baseAssetsPrefix") or ""):
            raise NeedsHuman(f"baseAssetsPrefix differs between {sb['name']} and {db['name']}; object keys would not "
                             f"line up")
        strings[sb["name"]] = db["name"]
        if sb.get("bucketId") and db.get("bucketId"):
            strings[sb["bucketId"]] = db["bucketId"]
    remap = Remapper(strings, src["account"], dst["account"], src["region"], dst["region"], src["partition"],
                     dst["partition"])
    bucket_pairs = [(sb["name"], db["name"]) for sb, db in pairs]
    for key in COPY_BUCKET_KEYS:
        if key in src["names"]["buckets"] and key in dst["names"]["buckets"]:
            bucket_pairs.append((src["names"]["buckets"][key], dst["names"]["buckets"][key]))
    return remap, {"strings": len(strings), "bucketPairs": bucket_pairs}


# ===================================================================== table sources
def scan_source(ctx, src: dict, table: str) -> Iterator[dict]:
    ddb = ctx.aws.client(src["env"], "dynamodb", src["role"])
    yield from restore.scan_items(ddb, table)


def export_source(ctx, run, src: dict, dst: dict, key: str, table: str, staging: str, kms: str | None,
                  sc) -> Iterator[dict]:
    """PITR export -> read DYNAMODB_JSON data files from the staging bucket with the target's credentials."""
    ddb = ctx.aws.client(src["env"], "dynamodb", src["role"])
    arn = ddb.describe_table(TableName=table)["Table"]["TableArn"]
    pitr = ddb.describe_continuous_backups(TableName=table)["ContinuousBackupsDescription"]
    if (pitr.get("PointInTimeRecoveryDescription") or {}).get("PointInTimeRecoveryStatus") != "ENABLED":
        raise NeedsHuman(f"PITR is disabled on source table {table}; enable PITR (see the productionizing guide) "
                         f"or re-run with --method scan")
    s3_prefix = f"vra/{run.run_id}/{key.replace('/', '_')}"
    kw: dict[str, Any] = {"TableArn": arn, "S3Bucket": staging, "S3Prefix": s3_prefix, "ExportFormat": "DYNAMODB_JSON",
                          "ClientToken": f"{run.run_id}-{key}"[-64:]}
    if kms:
        kw.update({"S3SseAlgorithm": "KMS", "S3SseKmsKeyId": kms})
    else:
        kw["S3SseAlgorithm"] = "AES256"
    desc = ddb.export_table_to_point_in_time(**kw)["ExportDescription"]
    export_arn = desc["ExportArn"]
    sc.log(f"export {table} -> s3://{staging}/{s3_prefix} ({export_arn})")
    deadline = time.monotonic() + 6 * 3600
    while desc.get("ExportStatus") == "IN_PROGRESS" and time.monotonic() < deadline:
        time.sleep(backup._poll_seconds())
        desc = ddb.describe_export(ExportArn=export_arn)["ExportDescription"]
    if desc.get("ExportStatus") != "COMPLETED":
        raise VraError(f"export of {table} ended {desc.get('ExportStatus')}: {desc.get('FailureMessage')}")
    s3 = ctx.aws.client(dst["env"], "s3", dst["role"])
    manifest_key = desc["ExportManifest"]  # .../manifest-summary.json
    files_key = manifest_key.rsplit("/", 1)[0] + "/manifest-files.json"
    body = s3.get_object(Bucket=staging, Key=files_key)["Body"].read().decode()
    for line in body.splitlines():
        if not line.strip():
            continue
        data_key = json.loads(line)["dataFileS3Key"]
        raw = s3.get_object(Bucket=staging, Key=data_key)["Body"].read()
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            for row in gz:
                if row.strip():
                    yield json.loads(row)["Item"]


def apply_mask_hook(ctx, hook: str, table_key: str, items: list[dict], workdir: Path, sc) -> list[dict]:
    workdir.mkdir(parents=True, exist_ok=True)
    fin, fout = workdir / f"{table_key.replace('/', '_')}.in.jsonl", workdir / f"{table_key.replace('/', '_')}.out.jsonl"
    with open(fin, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it) + "\n")
    p = ctx.shell(sc.log_path).run(shlex.split(hook), env={"VRA_MASK_TABLE": table_key, "VRA_MASK_IN": str(fin),
                                                           "VRA_MASK_OUT": str(fout)}, check=False, mutating=False,
                                   timeout=3600)
    if p.returncode != 0 or not fout.exists():
        raise VraError(f"mask hook failed for {table_key} (exit {p.returncode})")
    out = [json.loads(line) for line in fout.read_text(encoding="utf-8").splitlines() if line.strip()]
    fin.unlink(missing_ok=True)
    fout.unlink(missing_ok=True)
    return out


# ===================================================================== core copy
def copy_tables(ctx, run, src: dict, dst: dict, remap: Remapper, skip: set[str], source_fn: Callable[[str, str], Iterator[dict]],
                databases: set[str] | None, mask_hook: str | None, prefix: str) -> dict:
    results: dict[str, Any] = {}
    collected_assets: set[str] = set()
    for key, src_table in sorted(src["names"]["tables"].items()):
        if key in skip:
            results[key] = {"skipped": True}
            continue
        dst_table = dst["names"]["tables"].get(key)
        if not dst_table:
            results[key] = {"skipped": True, "reason": "no matching table in target"}
            continue

        def _copy(sc, key=key, src_table=src_table, dst_table=dst_table):
            if ctx.dry_run:
                return {"dryRun": True, "source": src_table, "target": dst_table}
            want_assets = key == "dynamoTables/assetStorage"

            def remapped():
                for it in source_fn(key, src_table):
                    if databases and not item_in_databases(it, databases):
                        continue
                    yield remap.item(it)

            # without a mask hook items stream from the source into the target in pages (no whole-table list);
            # a mask hook needs the table as one file, so it materializes
            items_iter = remapped()
            if mask_hook:
                items_iter = iter(apply_mask_hook(ctx, mask_hook, key, list(items_iter), run.dir / "mask", sc))
            ddb = ctx.aws.client(dst["env"], "dynamodb", dst["role"])
            assets: set[str] = set()
            written = 0
            try:
                cleared = restore.clear_table(ddb, dst_table)
                buf: list[dict] = []
                for it in items_iter:
                    if want_assets:
                        assets |= asset_prefixes([it])
                    buf.append({"PutRequest": {"Item": it}})
                    if len(buf) >= 500:
                        written += restore.batch_write(ddb, dst_table, buf)
                        buf = []
                if buf:
                    written += restore.batch_write(ddb, dst_table, buf)
            except Exception as exc:  # noqa: BLE001
                if permissions.is_denied(exc):
                    raise PermissionDenied(f"load into {dst_table} denied",
                                           permissions.classify_exception(exc, "dynamodb", dst_table),
                                           env=dst["env"], role=dst["role"]) from exc
                raise
            return {"source": src_table, "target": dst_table, "cleared": cleared, "written": written,
                    "assetPrefixes": sorted(assets)}

        r = run.step(f"{prefix}table.{key}", _copy, f"copy {key}")
        results[key] = {k: v for k, v in r.items() if k != "assetPrefixes"} if r else r
        collected_assets |= set((r or {}).get("assetPrefixes") or [])
    return {"tables": results, "assetPrefixes": sorted(collected_assets)}


def list_keys(s3, bucket: str, prefixes: list[str] | None = None) -> list[str]:
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for pre in (prefixes or [""]):
        for page in paginator.paginate(Bucket=bucket, Prefix=pre):
            keys += [o["Key"] for o in page.get("Contents", [])]
    return sorted(set(keys))


def _copy_many(client, pairs: list[tuple[dict, str, str]], workers: int = 16) -> int:
    """Server-side CopyObject for (CopySource, Bucket, Key) triples, in parallel for large sets."""
    def one(t):
        client.copy_object(Bucket=t[1], Key=t[2], CopySource=t[0], MetadataDirective="COPY")
    if len(pairs) <= 50:
        for t in pairs:
            one(t)
        return len(pairs)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, pairs))
    return len(pairs)


def copy_bucket(ctx, run, src: dict, dst: dict, sb: str, db: str, prefixes: list[str] | None, threshold: int,
                batch_role: str | None, staging: str | None, full: bool, sc) -> dict:
    """Copy one asset bucket. Each side only uses its own identity:

    - cross-env copy with a staging bucket (``dataCopy.stagingBucket``, owned by the source account): hop 1, the
      SOURCE data-copy role copies source objects into ``staging/vra/<run>/assets/<bucket>/`` (S3 Batch
      Operations in the source account with the source batch role above the threshold); hop 2, the TARGET role
      copies them from staging into the target bucket. Neither role ever reads or writes the other side's VAMS
      buckets, so no CDK-owned bucket or key policy has to trust another account.
    - without staging, only a same-account copy is possible (the target role then needs read on the source
      buckets); a cross-account copy without staging is refused.
    """
    if sb == db:
        return {"source": sb, "target": db, "skipped": "same bucket"}
    if ctx.dry_run:
        return {"dryRun": True, "source": sb, "target": db}
    s3_src = ctx.aws.client(src["env"], "s3", src["role"])
    s3_dst = ctx.aws.client(dst["env"], "s3", dst["role"])
    keys = list_keys(s3_src, sb, prefixes)
    two_hop = bool(staging) and src["env"] != dst["env"]
    res: dict[str, Any] = {"source": sb, "target": db, "objects": len(keys)}
    if two_hop:
        stage = f"vra/{run.run_id}/assets/{sb}/"
        if len(keys) > threshold and batch_role:
            res["hop1"] = batch_copy(ctx, run, src, sb, keys, staging, batch_role, staging, sc, target_prefix=stage)
        else:
            _copy_many(s3_src, [({"Bucket": sb, "Key": k}, staging, stage + k) for k in keys])
            res["hop1"] = {"method": "CopyObject", "role": f"{src['env']}:{src['role']}", "staged": len(keys)}
        _copy_many(s3_dst, [({"Bucket": staging, "Key": stage + k}, db, k) for k in keys])
        res.update({"copied": len(keys), "method": "staged", "staging": f"s3://{staging}/{stage}"})
    else:
        if src["account"] != dst["account"]:
            raise NeedsHuman(f"cross-account asset copy {sb} -> {db} needs dataCopy.stagingBucket (the source "
                             f"role stages objects, the target role loads them); a direct copy would need the "
                             f"target account to read the source buckets")
        if len(keys) > threshold and batch_role and staging:
            res.update(batch_copy(ctx, run, dst, sb, keys, db, batch_role, staging, sc))
        else:
            res.update({"copied": _copy_many(s3_dst, [({"Bucket": sb, "Key": k}, db, k) for k in keys]),
                        "method": "CopyObject"})
    removed = 0
    if full:
        src_set = set(keys)
        for k in list_keys(s3_dst, db):
            if k not in src_set:
                s3_dst.delete_object(Bucket=db, Key=k)
                removed += 1
    res["removed"] = removed
    return res


def batch_copy(ctx, run, side: dict, sb: str, keys: list[str], dest_bucket: str, role_arn: str, manifest_bucket: str,
               sc, target_prefix: str | None = None) -> dict:
    """S3 Batch Operations copy job in ``side``'s account, run as ``role_arn`` (the side's batch role).
    Manifest and report live under ``batch/`` of the staging bucket (the batch roles' ManifestAndReport grant)."""
    import urllib.parse
    s3 = ctx.aws.client(side["env"], "s3", side["role"])
    csv = "".join(f"{sb},{urllib.parse.quote(k)}\n" for k in keys).encode()
    mkey = f"batch/{run.run_id}/{side['env']}-{sb}.csv"
    put = s3.put_object(Bucket=manifest_bucket, Key=mkey, Body=csv)
    part = side["partition"]
    op: dict[str, Any] = {"TargetResource": f"arn:{part}:s3:::{dest_bucket}", "MetadataDirective": "COPY"}
    if target_prefix:
        op["TargetKeyPrefix"] = target_prefix
    s3c = ctx.aws.client(side["env"], "s3control", side["role"])
    job = s3c.create_job(
        AccountId=side["account"], ConfirmationRequired=False, Priority=10, RoleArn=role_arn,
        ClientRequestToken=f"{run.run_id}-{side['env']}-{sb}"[-64:],
        Operation={"S3PutObjectCopy": op},
        Manifest={"Spec": {"Format": "S3BatchOperations_CSV_20180820", "Fields": ["Bucket", "Key"]},
                  "Location": {"ObjectArn": f"arn:{part}:s3:::{manifest_bucket}/{mkey}",
                               "ETag": put["ETag"].strip('"')}},
        Report={"Bucket": f"arn:{part}:s3:::{manifest_bucket}", "Prefix": f"batch/{run.run_id}/report",
                "Format": "Report_CSV_20180820", "Enabled": True, "ReportScope": "FailedTasksOnly"})
    job_id = job["JobId"]
    deadline = time.monotonic() + 12 * 3600
    status = "New"
    while time.monotonic() < deadline:
        d = s3c.describe_job(AccountId=side["account"], JobId=job_id)["Job"]
        status = d["Status"]
        if status in ("Complete", "Failed", "Cancelled"):
            break
        time.sleep(max(backup._poll_seconds(), 1))
    if status != "Complete":
        raise VraError(f"S3 Batch copy job {job_id} ended {status}")
    return {"source": sb, "target": dest_bucket, "objects": len(keys), "method": "S3BatchOperations",
            "jobId": job_id, "account": side["account"]}


def copy_data(ctx, run, src: dict, dst: dict, skip: set[str], source_fn, databases: set[str] | None,
              mask_hook: str | None, prefix: str, threshold: int, batch_role: str | None, staging: str | None,
              bucket_map: dict | None) -> dict:
    """``batch_role``: the batch role of the side that runs S3 Batch jobs (the SOURCE side's for a staged
    cross-env copy, the env's own for an A/B slot copy)."""
    remap, info = build_remap(src, dst, bucket_map)
    tables = copy_tables(ctx, run, src, dst, remap, skip, source_fn, databases, mask_hook, prefix)
    buckets = []
    prefixes = tables["assetPrefixes"] if databases else None
    for sb, db in info["bucketPairs"]:
        buckets.append(run.step(f"{prefix}bucket.{sb}",
                                lambda sc, sb=sb, db=db: copy_bucket(ctx, run, src, dst, sb, db, prefixes, threshold,
                                                                     batch_role, staging, not databases, sc),
                                f"copy s3://{sb}"))
    return {"tables": tables["tables"], "buckets": buckets, "remappedStrings": info["strings"]}


def plan_copy(ctx, src: dict, dst: dict, skip: set[str], bucket_map: dict | None) -> dict:
    """Dry-run plan: per table and per bucket, what would be read from the source and replaced in the target.

    Read-only (DescribeTable, ListObjectsV2). Table counts are DynamoDB's ItemCount, which DynamoDB refreshes about
    every six hours, so they are approximate.
    """
    _, info = build_remap(src, dst, bucket_map)
    sddb = ctx.aws.client(src["env"], "dynamodb", src["role"])
    tddb = ctx.aws.client(dst["env"], "dynamodb", dst["role"])

    def count(ddb, table):
        return ddb.describe_table(TableName=table)["Table"].get("ItemCount")

    tables: dict[str, Any] = {}
    for key, src_table in sorted(src["names"]["tables"].items()):
        dst_table = dst["names"]["tables"].get(key)
        if key in skip or not dst_table:
            tables[key] = {"skipped": True, **({} if dst_table else {"reason": "no matching table in target"})}
            continue
        tables[key] = {"source": src_table, "target": dst_table, "sourceItems": count(sddb, src_table),
                       "targetItems": count(tddb, dst_table)}
    s3_src = ctx.aws.client(src["env"], "s3", src["role"])
    s3_dst = ctx.aws.client(dst["env"], "s3", dst["role"])
    buckets = []
    for sb, db in info["bucketPairs"]:
        if sb == db:
            buckets.append({"source": sb, "target": db, "skipped": "same bucket"})
            continue
        buckets.append({"source": sb, "target": db, "sourceObjects": len(list_keys(s3_src, sb)),
                        "targetObjects": len(list_keys(s3_dst, db))})
    copied = [t for t in tables.values() if not t.get("skipped")]
    return {"dryRun": True, "tables": tables, "buckets": buckets, "remappedStrings": info["strings"],
            "totals": {"tables": len(copied), "sourceItems": sum(t["sourceItems"] or 0 for t in copied),
                       "targetItemsReplaced": sum(t["targetItems"] or 0 for t in copied),
                       "sourceObjects": sum(b.get("sourceObjects", 0) for b in buckets)},
            "itemCountsApproximate": True}


# ===================================================================== A/B slots
def copy_slots(ctx, run, env: str, from_base: str, to_base: str, sc) -> dict:
    if ctx.dry_run:
        return {"dryRun": True, "from": from_base, "to": to_base}
    src = side(ctx, env, from_base, "deploy")
    dst = side(ctx, env, to_base, "deploy")
    e = ctx.ledger.env(env)
    dc = e.get("dataCopy") or {}
    res = copy_data(ctx, run, src, dst, NEVER_COPY | {"dynamoTables/s3AssetBucketsStorage"},
                    lambda key, table: scan_source(ctx, src, table), None, None, f"deploy.{env}.ab-copy.",
                    int(dc.get("batchOperationsThreshold", 10000)), dc.get("batchOperationsRoleArn"),
                    dc.get("stagingBucket"), dc.get("bucketMap"))
    res["reindex"] = restore.trigger_reindex(ctx, env, "deploy", True, to_base, sc=sc, run=run,
                                            what="the A/B slot copy")
    return res


# ===================================================================== prod -> test
def target_allowed(ledger, env: str) -> tuple[bool, str]:
    """May ``env`` be the TARGET of a data copy?

    Rule: ``dataCopy.allowAsTarget`` decides when set. When unset, a prod-class env is refused: one with
    ``requiresApproval: true`` that no other env promotes from (the end of a promotion chain, e.g. prod).
    Every other env is allowed (still subject to ``allowedSources`` and approvals).
    """
    e = ledger.env(env)
    flag = (e.get("dataCopy") or {}).get("allowAsTarget")
    if flag is not None:
        return bool(flag), f"envs/{env} dataCopy.allowAsTarget={str(bool(flag)).lower()}"
    from vra.ledger import is_prod_class
    if is_prod_class(ledger, env):
        return False, (f"{env} is a prod-class env (tier prod, or approval-gated at the end of a promotion chain); "
                       f"data copies into it are refused unless envs/{env} sets dataCopy.allowAsTarget: true")
    return True, f"{env} is not prod-class"


def source_approval_needed(ledger, frm: str) -> bool:
    fe = ledger.env(frm)
    return bool(fe.get("dataOwners") or fe.get("requiresApproval"))


def do_data_copy(ctx, run, frm: str, to: str, approval: str | None, databases: list[str] | None,
                 mask_hook: str | None, method: str = "export", source_approval: str | None = None,
                 write_freeze: str | None = None, abort_executions: bool = False) -> dict:
    from vra import freeze
    fe, te = ctx.ledger.env(frm), ctx.ledger.env(to)
    dc = te.get("dataCopy") or {}
    masked = bool(mask_hook or dc.get("maskHook"))

    def _gate(sc):
        if frm == to:
            raise UsageError("--from and --to must differ")
        ok, why = target_allowed(ctx.ledger, to)
        if not ok:
            raise NeedsHuman(f"data-copy refused: {why}", {"target": to})
        if frm not in (dc.get("allowedSources") or []):
            raise NeedsHuman(f"{frm} is not in envs/{to} dataCopy.allowedSources")
        subject = f"data-copy:{frm}"
        rec = approvals.check(ctx, to, subject, "data-copy", approval,
                              force_required=bool(fe.get("requiresApproval") or te.get("requiresApproval")))
        approvals.record(run, rec)
        # the owner of the SOURCE data approves it leaving the source env (envs/<src> dataOwners, else approvers)
        src_rec = None
        if source_approval_needed(ctx.ledger, frm):
            src_rec = approvals.check(ctx, frm, f"data-copy-to:{to}", "data-copy",
                                      source_approval or f"data-copy-to:{to}", force_required=True,
                                      approvers=fe.get("dataOwners") or None)
            approvals.record(run, src_rec)
            if rec and approvals.same_person(rec.get("approver"), src_rec.get("approver")) and fe.get("dataOwners"):
                run.state.setdefault("notes", []).append("the same person approved both sides of the data copy")
        for r in (rec, src_rec):
            if r and r.get("masked") is not None and bool(r["masked"]) != masked:
                raise NeedsHuman(f"approval {Path(r['path']).name} was given for masked={r['masked']}, but this copy "
                                 f"is masked={masked}")
        sv = (ctx.ledger.current_deploy(frm) or {}).get("vamsVersion")
        tv = (ctx.ledger.current_deploy(to) or {}).get("vamsVersion")
        if sv and tv and util.version_tuple(util.minor(sv)) > util.version_tuple(util.minor(tv)):
            raise NeedsHuman(f"source {frm} runs VAMS {sv}, newer than target {to} ({tv}); upgrade {to} first")
        if method == "export" and not (dc.get("stagingBucket") or (fe.get("dataCopy") or {}).get("stagingBucket")):
            raise NeedsHuman(f"set envs/{to} dataCopy.stagingBucket (in a location {to}'s role can read) or use "
                             f"--method scan")
        return {"approval": rec and rec.get("path"), "sourceApproval": src_rec and src_rec.get("path"),
                "approvalFields": approvals.history_fields(rec),
                "sourceApprovalFields": approvals.history_fields(src_rec),
                "sourceVersion": sv, "targetVersion": tv}

    staging = dc.get("stagingBucket") or (fe.get("dataCopy") or {}).get("stagingBucket")
    if dc.get("chainSourceRole"):
        # the orchestrator (e.g. the target env's CI role) may only reach the target loader role; the source
        # export role trusts that loader role, so the source session is assumed from it (role chaining)
        if hasattr(ctx.aws, "set_via"):
            ctx.aws.set_via(frm, "dataCopy", (to, "dataCopy"))
    extra = {"StagingBucket": staging or "*", "SourceAccount": fe["account"], "TargetAccount": te["account"]}
    kms = dc.get("kmsKeyArn") or (fe.get("dataCopy") or {}).get("kmsKeyArn")
    with env_lock(ctx, run, to):
        gate = run.step("data-copy.gate", _gate, "data-copy gates", revalidate=True)
        run.step("data-copy.preflight.target",
                 lambda sc: permissions.preflight(ctx, to, "data-copy-target", extra, role="dataCopy"),
                 f"preflight {to}")
        run.step("data-copy.preflight.source",
                 lambda sc: permissions.preflight(ctx, frm, "data-copy-source", extra, role="dataCopy"),
                 f"preflight {frm}")
        # the target checkpoint runs as the target deploy role (CreateBackup; kms:Decrypt via DynamoDB on CMK
        # stacks), so its permissions are simulated too
        run.step("data-copy.preflight.backup", lambda sc: permissions.preflight(ctx, to, "backup"),
                 f"preflight checkpoint of {to}")
        # read-only discovery also in --dry-run: the dry run reports what the copy would replace
        src = side(ctx, frm, None, "dataCopy")
        dst = side(ctx, to, None, "dataCopy")
        # before anything is written: every source asset bucket has its own target, no bucket is shared
        run.step("data-copy.bucket-map",
                 lambda sc: [{"source": s["name"], "target": d["name"]}
                             for s, d in pair_asset_buckets(src, dst, dc.get("bucketMap"))],
                 "map every registered source asset bucket to a target bucket", revalidate=True)
        run.step("data-copy.shared-buckets",
                 lambda sc: buckets.shared_guard(ctx, run, to, [b["name"] for b in dst["buckets"]],
                                                 f"data copy {frm} -> {to}", source=frm,
                                                 source_names=[b["name"] for b in src["buckets"]]),
                 "target buckets are not shared with the source or another env", revalidate=True)
        # registered buckets can appear at any time: re-checked at the start of every data copy (and on resume)
        buckets.require(ctx, run, frm, ["data-copy-source"], "data-copy.registered-buckets.source",
                        f"data copy out of {frm}", role="dataCopy")
        buckets.require(ctx, run, to, ["data-copy-target"], "data-copy.registered-buckets.target",
                        f"data copy into {to}", role="dataCopy")
        buckets.require(ctx, run, to, ["backup"], "data-copy.registered-buckets.checkpoint",
                        f"checkpoint of {to}")
        run.step("data-copy.checkpoint",
                 lambda sc: backup.create_checkpoint(ctx, to, run, sc, "pre-data-copy",
                                                     strict=backup.strict_for(ctx, to)),
                 f"checkpoint {to}")
        grant = None
        if kms and method == "export" and not ctx.dry_run:
            def _grant(sc):
                kc = ctx.aws.client(frm, "kms", "dataCopy")
                grantee = ctx.aws.role_arn(to, "dataCopy")
                if not grantee:
                    raise NeedsHuman(f"envs/{to} needs dataCopyRoleArn (or deployRoleArn) for the KMS grant")
                g = kc.create_grant(KeyId=kms, GranteePrincipal=grantee, Operations=["Decrypt", "DescribeKey"],
                                    Name=f"vra-{run.run_id}"[:256])
                return {"grantId": g["GrantId"], "keyId": kms}
            grant = run.step("data-copy.kms-grant", _grant, "KMS grant for target")
        skip = set(NEVER_COPY) | NOT_ACROSS_ENVS
        if not dc.get("copyUserRoles"):
            skip |= OPTIONAL_USER_ROLES
        freeze.workflow_executions_step(ctx, run, to, "data-copy.workflow-executions", f"data copy into {to}",
                                        abort=abort_executions, role="dataCopy", names=dst["names"])
        with freeze.Frozen(ctx, run, to, "data-copy.write-freeze", f"data copy {frm} -> {to}", role="dataCopy",
                           override=write_freeze,
                           complete_step=None if ctx.dry_run else "data-copy.load-complete") as fr:
            if ctx.dry_run:
                res = run.step("data-copy.plan", lambda sc: plan_copy(ctx, src, dst, skip, dc.get("bucketMap")),
                               "item and object counts (dry run)")
            else:
                if method == "export":
                    fn = lambda key, table: export_source(ctx, run, src, dst, key, table, staging, kms,  # noqa: E731
                                                          _StepLog(run, f"data-copy.export.{key}"))
                else:
                    fn = lambda key, table: scan_source(ctx, src, table)  # noqa: E731
                mark_dirty(run, to, f"data copy {frm} -> {to} (target tables cleared and reloaded)")
                # staged asset copies run S3 Batch jobs in the SOURCE account as the source batch role
                batch_role = (fe.get("dataCopy") or {}).get("batchOperationsRoleArn") if staging else \
                    dc.get("batchOperationsRoleArn")
                res = copy_data(ctx, run, src, dst, skip, fn, set(databases) if databases else None,
                                mask_hook if mask_hook is not None else dc.get("maskHook"), "data-copy.",
                                int(dc.get("batchOperationsThreshold", 10000)), batch_role, staging,
                                dc.get("bucketMap"))
                run.step("data-copy.load-complete", lambda sc: True, "tables loaded and objects copied")
        if grant:
            def _retire(sc):
                ctx.aws.client(frm, "kms", "dataCopy").retire_grant(KeyId=kms, GrantId=grant["grantId"])
                return True
            try:
                run.step("data-copy.kms-retire", _retire, "retire KMS grant")
            except Exception as exc:  # noqa: BLE001
                run.state.setdefault("notes", []).append(f"retire KMS grant {grant['grantId']} manually: {exc}")
        run.step("data-copy.reindex", lambda sc: restore.trigger_reindex(ctx, to, "dataCopy", sc=sc, run=run,
                                                                         what="the data copy"), "OpenSearch reindex")

        def _audit(sc):
            rec = {"action": "data-copy", "from": frm, "to": to, "runId": run.run_id, "result": "SUCCEEDED",
                   "databases": databases or "all", "method": method,
                   "masked": masked, "initiator": ctx.initiator,
                   "cognitoUsersCopied": False, "userRolesCopied": bool(dc.get("copyUserRoles")),
                   **fr.history_fields(), **(gate.get("approvalFields") or {})}
            ctx.ledger.append_history(to, rec, dry_run=ctx.dry_run)
            ctx.ledger.append_history(frm, {**rec, "action": "data-copy-source",
                                            **(gate.get("sourceApprovalFields") or {})}, dry_run=ctx.dry_run)
            return rec
        audit = run.step("data-copy.audit", _audit, "audit record")
        mark_clean(run, to)
    notes = ["Cognito users are not copied; create test users in the target user pool."]
    if gate.get("sourceVersion") and gate.get("targetVersion") and gate["sourceVersion"] != gate["targetVersion"]:
        notes.append(f"source {gate['sourceVersion']} < target {gate['targetVersion']}: run `vra migrate --env {to} "
                     f"--from {gate['sourceVersion']} --to {gate['targetVersion']}`")
    return {"copy": res, "audit": audit, "notes": notes}


class _StepLog:
    def __init__(self, run, sid):
        self.run, self.sid = run, sid

    def log(self, text):
        p = self.run.log_path(self.sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(util.redact(text) + "\n")


@workflow("data-copy")
def wf_data_copy(ctx, run, args):
    from vra import freeze
    freeze.record_ack(run, ctx, bool(args.get("writesStopped")))
    return do_data_copy(ctx, run, args["from"], args["to"], args.get("approval"), args.get("databases"),
                        args.get("maskHook"), args.get("method", "export"), args.get("sourceApproval"),
                        write_freeze=args.get("writeFreeze"),
                        abort_executions=bool(args.get("abortWorkflowExecutions")))
