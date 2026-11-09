# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Registered asset buckets.

VAMS lets operators register asset buckets at any time (rows of the ``s3AssetBucketsStorage`` table),
including customer-named buckets and buckets in other accounts with their own KMS keys. The stock vra
role templates only grant buckets named after the core stack, so every phase that touches bucket data
resolves the real bucket list first:

- :func:`inventory`: SSM ``s3Buckets/*`` + every registered asset bucket, each with its default
  encryption (``GetBucketEncryption``; the KMS key ARN for SSE-KMS with a customer key).
- :func:`check_access`: per bucket and per workflow, simulate the manifest's ``forEach`` statements
  (``${RegisteredBucket}`` / ``${RegisteredBucketKmsKey}``) against the env role, and compare each bucket
  and key with ``envs/<env>/env.yaml`` ``registeredBuckets`` (what the operator recorded as granted).
- :func:`require`: a re-validated run step at the start of each bucket phase (backup checkpoint, restore,
  data copy, data migrations). Gaps -> IT request listing the exact actions per bucket / key, exit 10;
  non-blocking mode (deploys that touch no bucket data) records the IT request and a note instead.
- :func:`shared_guard`: refuse (exit 20) to write a bucket that another env of the ledger also uses.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from vra import permissions, vams_resources
from vra.aws import client_error_parts, parse_arn, substitute
from vra.errors import NeedsHuman, PermissionDenied

BUCKET_TEMPLATE = "RegisteredBucket"
KEY_TEMPLATE = "RegisteredBucketKmsKey"
FOR_EACH_BUCKET = "registeredBucket"
FOR_EACH_KEY = "registeredBucketKmsKey"
#: manifests whose forEach statements are checked per phase
PHASE_WORKFLOWS = {"backup": ["backup"], "restore": ["restore"], "migrate": ["migrate"],
                   "data-copy-source": ["data-copy-source"], "data-copy-target": ["data-copy-target"]}
INVENTORY_ACTION = "bucket-inventory"


# ------------------------------------------------------------------ ledger config
def config(e: dict) -> dict:
    """``registeredBuckets`` of an env, normalised: {granted, bucketArns, kmsKeyArns}."""
    rb = e.get("registeredBuckets") or {}
    return {"granted": bool(rb.get("granted")), "bucketArns": list(rb.get("bucketArns") or []),
            "kmsKeyArns": list(rb.get("kmsKeyArns") or [])}


def bucket_arn(partition: str, name: str) -> str:
    return f"arn:{partition}:s3:::{name}"


def _key_arn(key_id: str, partition: str, region: str, account: str) -> str:
    if key_id.startswith("arn:"):
        return key_id
    if key_id.startswith("alias/"):
        return f"arn:{partition}:kms:{region}:{account}:{key_id}"
    return f"arn:{partition}:kms:{region}:{account}:key/{key_id}"


# ------------------------------------------------------------------ inventory
def encryption(s3, bucket: str, partition: str, region: str, account: str) -> dict:
    """Default encryption of a bucket: {sse, kmsKeyArn}. ``kmsKeyArn`` is None for SSE-S3 and for the AWS
    managed key (aws/s3), which needs no IAM grant. A denied GetBucketEncryption is reported, not raised."""
    try:
        resp = s3.get_bucket_encryption(Bucket=bucket)
    except Exception as exc:  # noqa: BLE001
        code = client_error_parts(exc)[0]
        if code == "ServerSideEncryptionConfigurationNotFoundError":
            return {"sse": "none", "kmsKeyArn": None}
        if permissions.is_denied(exc):
            return {"sse": "unknown", "kmsKeyArn": None, "encryptionDenied": True}
        raise
    for rule in (resp.get("ServerSideEncryptionConfiguration") or {}).get("Rules", []):
        d = rule.get("ApplyServerSideEncryptionByDefault") or {}
        alg = d.get("SSEAlgorithm")
        if alg in ("aws:kms", "aws:kms:dsse"):
            kid = d.get("KMSMasterKeyID")
            return {"sse": alg, "kmsKeyArn": _key_arn(kid, partition, region, account) if kid else None}
        if alg:
            return {"sse": alg, "kmsKeyArn": None}
    return {"sse": "none", "kmsKeyArn": None}


def inventory(ctx, env: str, role: str = "deploy", names: dict | None = None,
              base_override: str | None = None) -> dict:
    """Every bucket of the deployed env (SSM + registered asset buckets) with its encryption key."""
    e = ctx.ledger.env(env)
    names = names or vams_resources.discover(ctx, env, role, base_override)
    part = e.get("partition") or ctx.aws.partition(env)
    s3 = ctx.aws.client(env, "s3", role)
    stack = (names.get("stack") or ctx.ledger.core_stack_name(env, base_override)).lower()
    out = []
    for b in vams_resources.all_buckets(ctx, env, names, role):
        rec = {k: b.get(k) for k in ("name", "kind", "key", "bucketId") if b.get(k) is not None}
        rec["arn"] = bucket_arn(part, b["name"])
        rec["stackOwned"] = b["name"].lower().startswith(stack)
        rec.update(encryption(s3, b["name"], part, e["region"], e["account"]))
        out.append(rec)
    return {"env": env, "stack": names.get("stack"), "partition": part, "region": e["region"],
            "account": e["account"], "buckets": out}


def previous_names(ctx, env: str) -> set[str] | None:
    """Bucket names of the env's last recorded inventory (history action ``bucket-inventory``)."""
    for rec in reversed(ctx.ledger.history(env)):
        if rec.get("action") == INVENTORY_ACTION:
            return set(rec.get("buckets") or [])
    return None


def record_inventory(ctx, env: str, inv: dict, run_id: str | None) -> bool:
    """Append a ``bucket-inventory`` history record when the bucket set changed (also what the shared-bucket
    guard of other envs reads)."""
    names = sorted(b["name"] for b in inv["buckets"])
    if previous_names(ctx, env) == set(names):
        return False
    ctx.ledger.append_history(env, {"action": INVENTORY_ACTION, "result": "SUCCEEDED", "runId": run_id,
                                    "buckets": names,
                                    "kmsKeys": sorted({b["kmsKeyArn"] for b in inv["buckets"] if b.get("kmsKeyArn")})},
                              dry_run=ctx.dry_run)
    return True


# ------------------------------------------------------------------ coverage
def _matches(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, p) for p in patterns)


def bucket_covered(e: dict, b: dict) -> tuple[bool, str]:
    """Is the role recorded as able to use this bucket? Buckets named after the core stack are granted by the
    stock role templates; every other bucket needs ``registeredBuckets.granted: true`` and a matching ARN."""
    if b.get("stackOwned"):
        return True, "named after the core stack (stock role templates)"
    cfg = config(e)
    if not cfg["granted"]:
        return False, "envs/<env>/env.yaml registeredBuckets.granted is not true"
    if _matches(b["arn"], cfg["bucketArns"]) or _matches(b["name"], cfg["bucketArns"]):
        return True, "registeredBuckets.bucketArns"
    return False, "not matched by registeredBuckets.bucketArns"


def key_covered(e: dict, key: str | None) -> tuple[bool, str]:
    if not key:
        return True, "no customer KMS key"
    try:
        a = parse_arn(key)
        if a.get("account") == e["account"] and a.get("region") == e["region"] and ":key/" in key:
            return True, "key in the env account and region (stock role templates, via S3)"
    except ValueError:
        pass
    cfg = config(e)
    if cfg["granted"] and _matches(key, cfg["kmsKeyArns"]):
        return True, "registeredBuckets.kmsKeyArns"
    return False, ("registeredBuckets.granted is not true" if not cfg["granted"]
                   else "not matched by registeredBuckets.kmsKeyArns")


# ------------------------------------------------------------------ manifest expansion
def kms_context(inv: dict) -> list[dict]:
    """Context entries so key/* statements conditioned on kms:ViaService s3.* simulate as S3 would call."""
    from vra import aws
    host = f"s3.{inv['region']}.{aws.dns_suffix(inv['partition'])}"
    return [{"ContextKeyName": "kms:ViaService", "ContextKeyValues": [host], "ContextKeyType": "string"},
            {"ContextKeyName": "kms:CallerAccount", "ContextKeyValues": [inv["account"]], "ContextKeyType": "string"}]


def expand_for_each(st: dict, values: dict, inv: dict | None) -> list[dict]:
    """One concrete statement per bucket (``forEach: registeredBucket``) or per distinct bucket KMS key
    (``forEach: registeredBucketKmsKey``). Without an inventory (dry run, stack not deployed yet) a bucket
    statement falls back to the stack-name pattern and a key statement is not simulated."""
    each = st.get("forEach")
    resources = st.get("resources") or ["*"]
    if inv is None:
        if each == FOR_EACH_KEY:
            return []
        vals = {**values, BUCKET_TEMPLATE: f"{values.get('CoreStack', '*')}*"}
        return [{"sid": st.get("sid"), "actions": list(st["actions"]), "resources":
                 [substitute(r, vals) for r in resources]}]
    out = []
    if each == FOR_EACH_BUCKET:
        for b in inv["buckets"]:
            vals = {**values, BUCKET_TEMPLATE: b["name"]}
            out.append({"sid": f"{st.get('sid')}[{b['name']}]", "actions": list(st["actions"]),
                        "resources": [substitute(r, vals) for r in resources], "bucket": b["name"]})
    elif each == FOR_EACH_KEY:
        by_key: dict[str, list[str]] = {}
        for b in inv["buckets"]:
            if b.get("kmsKeyArn"):
                by_key.setdefault(b["kmsKeyArn"], []).append(b["name"])
        for key, bnames in sorted(by_key.items()):
            vals = {**values, KEY_TEMPLATE: key}
            out.append({"sid": f"{st.get('sid')}[{key.rsplit('/', 1)[-1]}]", "actions": list(st["actions"]),
                        "resources": [substitute(r, vals) for r in resources], "kmsKey": key,
                        "buckets": sorted(bnames), "context": kms_context(inv)})
    return out


def bucket_statements(manifest: dict) -> list[dict]:
    return [s for s in manifest["statements"] if s.get("forEach")]


# ------------------------------------------------------------------ access check
def role_for(workflows: list[str], role: str | None = None) -> str:
    """The env role a phase runs under: explicit, else the first manifest's (backup/readonly -> deploy)."""
    role = role or permissions.load_manifest(workflows[0]).get("role", "deploy")
    return "deploy" if role in ("readonly", "backup") else role


def check_access(ctx, env: str, workflows: list[str], role: str | None = None, inv: dict | None = None) -> dict:
    """Per bucket / key: coverage (ledger) + simulation of every forEach statement of ``workflows``.

    Returns {inventory, principal, denials, buckets: [{name, arn, kmsKeyArn, covered, keyCovered,
    newlyRegistered}]}. Denials carry ``bucket`` / ``kmsKey`` / ``newlyRegistered`` / ``workflow``."""
    e = ctx.ledger.env(env)
    manifests = [permissions.load_manifest(w) for w in workflows]
    role = role_for(workflows, role)
    inv = inv or inventory(ctx, env, role)
    prev = previous_names(ctx, env)
    principal = permissions.simulation_principal(ctx, env, role)
    values = permissions.placeholders(ctx, env)
    summary = []
    for b in inv["buckets"]:
        cov, why = bucket_covered(e, b)
        kcov, kwhy = key_covered(e, b.get("kmsKeyArn"))
        summary.append({"name": b["name"], "arn": b["arn"], "kind": b.get("kind"), "kmsKeyArn": b.get("kmsKeyArn"),
                        "covered": cov, "coveredBy": why if cov else None, "keyCovered": kcov,
                        "newlyRegistered": bool(prev is not None and b["name"] not in prev),
                        "reason": None if cov and kcov else (why if not cov else kwhy)})
    by_name = {s["name"]: s for s in summary}
    denials: list[dict] = []
    seen: set[tuple] = set()

    def add(d: dict) -> None:
        k = (d.get("action"), d.get("resource"))
        if k in seen:
            return
        seen.add(k)
        denials.append(d)

    for b in inv["buckets"]:
        if b.get("encryptionDenied"):
            add({"principal": principal, "action": "s3:GetEncryptionConfiguration", "resource": b["arn"],
                 "kind": "identity", "reason": "denied: cannot read the bucket's default encryption (KMS key)",
                 "source": "inventory", "bucket": b["name"],
                 "newlyRegistered": by_name[b["name"]]["newlyRegistered"]})
    iam = ctx.aws.client(env, "iam", role)
    for wf, m in zip(workflows, manifests):
        expanded = []
        for st in bucket_statements(m):
            if str(st.get("sid") or "").startswith("Tools"):
                continue
            expanded += expand_for_each(st, values, inv)
        # coverage first: an uncovered bucket / key needs every action, also when the identity policy allows it
        # (its bucket policy or key policy, possibly in another account, is outside the simulation)
        for st in expanded:
            s = by_name.get(st.get("bucket")) if st.get("bucket") else None
            if st.get("kmsKey"):
                kc, kwhy = key_covered(e, st["kmsKey"])
                if not kc:
                    new = any(by_name[n]["newlyRegistered"] for n in st["buckets"])
                    for a in st["actions"]:
                        add({"principal": principal, "action": a, "resource": st["kmsKey"], "kind": "registered-bucket",
                             "reason": f"KMS key of registered bucket(s) {', '.join(st['buckets'])}: {kwhy}"
                                       + (" (newly registered since the last check)" if new else ""),
                             "source": "registered-buckets", "kmsKey": st["kmsKey"], "buckets": st["buckets"],
                             "newlyRegistered": new, "workflow": wf})
            elif s is not None and not s["covered"]:
                for r in st["resources"]:
                    for a in st["actions"]:
                        add({"principal": principal, "action": a, "resource": r, "kind": "registered-bucket",
                             "reason": f"registered bucket {s['name']}: {s['reason']}"
                                       + (" (newly registered since the last check)" if s["newlyRegistered"] else ""),
                             "source": "registered-buckets", "bucket": s["name"],
                             "newlyRegistered": s["newlyRegistered"], "workflow": wf})
        if not ctx.dry_run and expanded:
            res = permissions.simulate(iam, principal, [{**st, "optional": False} for st in expanded])
            for d in res["denied"]:
                d["workflow"] = wf
                if d.get("bucket"):
                    d["newlyRegistered"] = by_name[d["bucket"]]["newlyRegistered"]
                add(d)
    return {"inventory": inv, "principal": principal, "role": role, "denials": denials, "buckets": summary}


def _message(env: str, res: dict, what: str) -> str:
    bad = sorted({d.get("bucket") or ", ".join(d.get("buckets") or []) for d in res["denials"]} - {""})
    new = sorted({d.get("bucket") for d in res["denials"] if d.get("newlyRegistered") and d.get("bucket")})
    msg = (f"{what}: the {res['role']} role of {env} cannot be shown to use {len(bad)} bucket(s) "
           f"({', '.join(bad[:6])}{'...' if len(bad) > 6 else ''})")
    if new:
        msg += f"; newly registered since the last check: {', '.join(new)}"
    return msg


def require(ctx, run, env: str, workflows: list[str], step_id: str, what: str, role: str | None = None,
            blocking: bool = True, base_override: str | None = None) -> Any:
    """Re-validated step at the start of a phase that touches bucket data. Blocking: gaps raise
    PermissionDenied (IT request, exit 10; `vra resume` re-checks). Non-blocking: the IT request is written and
    listed in the report, and the run continues."""

    def _check(sc):
        if ctx.dry_run:
            return {"dryRun": True, "workflows": workflows}
        inv = inventory(ctx, env, role_for(workflows, role), base_override=base_override)
        res = check_access(ctx, env, workflows, role, inv)
        record_inventory(ctx, env, inv, run.run_id)
        out = {"workflows": workflows, "buckets": res["buckets"], "denials": len(res["denials"])}
        if res["denials"]:
            raise PermissionDenied(_message(env, res, what), res["denials"], principal=res["principal"], env=env,
                                   role=res["role"])
        return out

    def _advisory(sc):
        try:
            return _check(sc)
        except NeedsHuman as exc:  # e.g. first install: nothing deployed, nothing registered yet
            return {"skipped": exc.message}
        except PermissionDenied as exc:
            return {"itRequest": _non_blocking(ctx, run, step_id, exc, what), "blocking": False}
        except Exception as exc:  # noqa: BLE001 - advisory only: a check that cannot run never fails such a deploy
            note = f"{env}: registered-bucket check skipped ({type(exc).__name__}: {str(exc)[:200]})"
            run.state.setdefault("notes", []).append(note)
            run.save()
            return {"skipped": note}

    return run.step(step_id, _check if blocking else _advisory, f"registered asset buckets ({what})",
                    revalidate=True)


def _non_blocking(ctx, run, step_id: str, exc: PermissionDenied, what: str) -> str:
    want = sorted([d.get("action") or "", d.get("resource") or ""] for d in exc.denials)
    for r in run.state.get("itRequests", []):
        if r.get("step") == step_id and r.get("status") == "open" and r.get("pairs") == want:
            return r["id"]
    req = permissions.write_it_request(ctx, run, step_id, exc)
    entry = run.state["itRequests"][-1]
    entry["blocking"] = False
    entry["pairs"] = want
    run.state.setdefault("notes", []).append(
        f"{req['id']} is pending ({what} did not need bucket access): {exc.message}. The next backup, restore, "
        f"data copy or deploy with data migrations on {exc.env} stops until it is granted.")
    run.save()
    return req["id"]


# ------------------------------------------------------------------ shared-bucket guard
def _literal_names(arns: list[str]) -> set[str]:
    out = set()
    for a in arns:
        if any(c in a for c in "*?["):
            continue
        out.add(a.split(":::", 1)[1].split("/", 1)[0] if ":::" in a else a)
    return out


def other_env_buckets(ctx, env: str, exclude: set[str] | None = None) -> tuple[dict[str, set[str]], list[str]]:
    """{bucket name: {other envs that use it}} from recorded history (bucket inventories, checkpoints),
    each env's literal registeredBuckets.bucketArns, and a best-effort live discovery. Second value: notes on
    envs that could not be discovered live."""
    used: dict[str, set[str]] = {}
    notes: list[str] = []
    for n in ctx.ledger.env_names():
        if n == env or n in (exclude or set()):
            continue
        names: set[str] = set()
        for rec in ctx.ledger.history(n):
            if rec.get("action") == INVENTORY_ACTION:
                names |= set(rec.get("buckets") or [])
            names |= set(rec.get("bucketNames") or [])
        names |= _literal_names(config(ctx.ledger.env(n))["bucketArns"])
        try:
            role = "readonly" if ctx.ledger.env(n).get("readOnlyRoleArn") else "deploy"
            disc = vams_resources.discover(ctx, n, role)
            names |= {b["name"] for b in vams_resources.all_buckets(ctx, n, disc, role)}
        except Exception as exc:  # noqa: BLE001 - live discovery of OTHER envs is best effort; history still counts
            notes.append(f"{n}: live bucket discovery skipped ({type(exc).__name__}); recorded history used")
        for b in names:
            used.setdefault(b, set()).add(n)
    return used, notes


def shared_guard(ctx, run, env: str, names: list[str], what: str, source: str | None = None,
                 source_names: list[str] | None = None) -> dict:
    """Refuse (NeedsHuman) when a bucket ``env`` would write is also a source bucket or used by another env."""
    from vra.ledger import is_prod_class
    names = sorted(set(names))
    if source is not None:
        same = sorted(set(names) & set(source_names or []))
        if same:
            raise NeedsHuman(f"{what} refused: target {env} and source {source} share bucket(s) {', '.join(same)}; a "
                             f"data copy never writes into its own source. Give {env} its own buckets (and "
                             f"dataCopy.bucketMap) first", {"sharedBuckets": same, "otherEnv": source})
    used, notes = other_env_buckets(ctx, env, exclude={source} if source else None)
    shared = {b: sorted(used[b]) for b in names if b in used}
    if shared:
        others = sorted({o for v in shared.values() for o in v})
        prod = [o for o in others if is_prod_class(ctx.ledger, o)]
        lines = "; ".join(f"{b} (also used by {', '.join(v)})" for b, v in sorted(shared.items()))
        raise NeedsHuman(f"{what} refused: {env} writes bucket(s) that another env also uses: {lines}. vra never "
                         f"writes a shared bucket"
                         + (f"; {', '.join(prod)} is prod-class and its buckets are never written from another env"
                            if prod else "")
                         + f". Register separate buckets for {env}", {"sharedBuckets": shared, "otherEnvs": others})
    if notes:
        run.state.setdefault("notes", []).extend(n for n in notes if n not in run.state.get("notes", []))
        run.save()
    return {"checked": names, "otherEnvNotes": notes}


__all__ = ["inventory", "check_access", "require", "shared_guard", "config", "expand_for_each", "encryption",
           "record_inventory", "role_for"]
