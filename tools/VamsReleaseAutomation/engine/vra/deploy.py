# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra deploy`, `vra promote`, `vra rollback`.

deploy: env lock (taken BEFORE the gates) -> promotion evidence (every env with promotesFrom, also via
`vra deploy`) -> gate (lane, approval + SoD + CI binding, change window, bundle integrity, scanner waiver,
pinned CDK CLI; re-checked on every resume) -> IAM preflight -> backup checkpoint (strict for gated envs)
-> drift check -> cdk diff report (fails closed; blocks data-resource replacement and forward-only
changes; IAM quota check: roles/managed policies added + replaced vs iam:GetAccountSummary; CloudFront quota
check: cache/origin request/response headers policies and distributions added + replaced vs the account's CloudFront
quotas) -> migration plan -> change-window re-check -> cdk deploy from the bundle's cdk.out/<env> ->
[A/B: data copy into the new slot] -> migrations dry-run + apply -> post-deploy smoke (required suites)
-> [A/B: DNS-switch hook] -> history record. Failures roll back per the env's rollbackPolicy; a run that
parks after the CloudFormation deploy started keeps the env locked.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from vra import approvals, backup, build as build_mod, iam as iam_mod, identity, migrate, permissions, restore, \
    tests_runner, util
from vra.errors import (NeedsHuman, PermissionDenied, RollbackFailed, RolledBack, TestsFailed, UsageError, VraError,
                        CommandFailed)
from vra.ledger import is_prod_class, required_suites
from vra.runner import env_lock, mark_clean, mark_dirty, workflow

DATA_RESOURCE_TYPES = ("AWS::DynamoDB::Table", "AWS::DynamoDB::GlobalTable", "AWS::S3::Bucket",
                       "AWS::Cognito::UserPool", "AWS::KMS::Key", "AWS::OpenSearchService::Domain",
                       "AWS::Elasticsearch::Domain", "AWS::OpenSearchServerless::Collection", "AWS::Logs::LogGroup",
                       "AWS::EFS::FileSystem")
DIFF_LINE_RE = re.compile(r"^\s*\[(?P<op>[-~+])\]\s+(?P<type>AWS::[A-Za-z0-9:]+)\s+(?P<rest>.*)$")
# property-level lines under a resource: " ├─ [~] Schema", " └─ [-] DeletionPolicy"
PROP_LINE_RE = re.compile(r"^\s*[\u2500-\u257f│|`\- ]*\[(?P<op>[-~+])\]\s+(?P<prop>[A-Za-z][A-Za-z0-9.]*)\b")
SUMMARY_RE = re.compile(r"Number of stacks with differences:\s*(\d+)")
# Changes that are not replacements but cannot be undone by redeploying the previous build.
FORWARD_ONLY = {("AWS::Cognito::UserPool", "Schema"): "user pool schema attributes cannot be modified or removed"}
POLICY_PROPS = ("DeletionPolicy", "UpdateReplacePolicy")
# IAM quota check: resources that count against the account's IAM quotas (iam:GetAccountSummary keys).
# A replacement counts too: CloudFormation creates the new role before it deletes the old one.
IAM_QUOTA_TYPES = {"AWS::IAM::Role": ("roles", "Roles", "RolesQuota"),
                   "AWS::IAM::ManagedPolicy": ("policies", "Policies", "PoliciesQuota")}
REPLACE_RE = re.compile(r"\b(replace|may be replaced|requires replacement|may cause replacement)\b")
# CloudFront quota check: account-wide CloudFront quotas a VAMS CloudFront deployment draws on (2 cache
# policies, 1 origin request policy, 1 response headers policy, 1 distribution). Per type: counter key, Service
# Quotas code (service code `cloudfront`, verified 2026-11-09 with servicequotas:ListServiceQuotas), quota name,
# documented AWS default (the fallback when the quota cannot be read), and the in-use list call (custom only).
CF_QUOTA_TYPES = {
    "AWS::CloudFront::CachePolicy": ("cachePolicies", "L-7D134442", "Cache policies per AWS account", 20,
                                     "list_cache_policies", "CachePolicyList"),
    "AWS::CloudFront::OriginRequestPolicy": ("originRequestPolicies", "L-C3659C43",
                                             "Origin request policies per AWS account", 20,
                                             "list_origin_request_policies", "OriginRequestPolicyList"),
    "AWS::CloudFront::ResponseHeadersPolicy": ("responseHeadersPolicies", "L-CF0D4FC5", "Response headers policies",
                                               20, "list_response_headers_policies", "ResponseHeadersPolicyList"),
    "AWS::CloudFront::Distribution": ("distributions", "L-24B04930", "Web distributions per AWS account", 500,
                                      "list_distributions", "DistributionList"),
}
# resource type -> (counter group, counter key) for every type a quota check counts
QUOTA_KINDS = {**{t: ("iam", v[0]) for t, v in IAM_QUOTA_TYPES.items()},
               **{t: ("cloudfront", v[0]) for t, v in CF_QUOTA_TYPES.items()}}


def _iam_counter() -> dict:
    return {k: {"added": 0, "replaced": 0} for k, _, _ in IAM_QUOTA_TYPES.values()}


def _cf_counter() -> dict:
    return {v[0]: {"added": 0, "replaced": 0} for v in CF_QUOTA_TYPES.values()}


def _quota_counters() -> dict:
    return {"iam": _iam_counter(), "cloudfront": _cf_counter()}


def _count(counters: dict, rtype: str, field: str) -> None:
    group, key = QUOTA_KINDS[rtype]
    counters[group][key][field] += 1


def parse_diff(text: str) -> dict:
    """Parse `cdk diff` text. ``understood`` is False when cdk reports stacks with differences but no resource
    line could be parsed (new CLI wording, nested rendering): the gate then fails closed. ``iam`` counts the IAM
    roles and managed policies the change adds or replaces (the IAM quota check), ``cloudfront`` the CloudFront
    policies and distributions (the CloudFront quota check)."""
    destructive, forward_only, changes = [], [], 0
    counters = _quota_counters()
    current: dict | None = None
    for line in text.splitlines():
        m = DIFF_LINE_RE.match(line)
        if m:
            changes += 1
            rtype, rest, op = m.group("type"), m.group("rest"), m.group("op")
            res = rest.split()[0] if rest.split() else rest
            current = {"type": rtype, "resource": res}
            replace = bool(REPLACE_RE.search(rest))
            if (op == "-" or replace) and rtype in DATA_RESOURCE_TYPES:
                destructive.append({"op": "remove" if op == "-" else "replace", "type": rtype, "resource": res})
            if rtype in QUOTA_KINDS and (op == "+" or replace):
                _count(counters, rtype, "added" if op == "+" else "replaced")
                current["quotaCounted"] = True
            continue
        pm = PROP_LINE_RE.match(line)
        indented = line[:1] in (" ", "\t") or "\u2500" <= line[:1] <= "\u257f"
        if pm and current is not None and indented:
            if current["type"] in QUOTA_KINDS and not current.get("quotaCounted") and REPLACE_RE.search(line):
                # " └─ [~] RoleName (requires replacement)" under a resource line without "replace"
                _count(counters, current["type"], "replaced")
                current["quotaCounted"] = True
            prop = pm.group("prop")
            why = FORWARD_ONLY.get((current["type"], prop))
            if why:
                forward_only.append({"op": "modify", "type": current["type"], "resource": current["resource"],
                                     "property": prop, "why": why})
            elif prop in POLICY_PROPS and current["type"] in DATA_RESOURCE_TYPES:
                forward_only.append({"op": "modify", "type": current["type"], "resource": current["resource"],
                                     "property": prop, "why": f"{prop} change on a data-bearing resource"})
        elif line.strip() and not indented:
            current = None  # "Stack ...", "Outputs", "Parameters": the next block starts
    sm = SUMMARY_RE.search(text)
    stacks_with_diff = int(sm.group(1)) if sm else None
    understood = not (stacks_with_diff and changes == 0)
    return {"changes": changes, "destructive": destructive + forward_only, "replacements": destructive,
            "forwardOnly": forward_only, "stacksWithDifferences": stacks_with_diff, "understood": understood,
            **counters}


# ===================================================================== IAM quota check
QUOTA_HEADROOM_DEFAULT = 10


def quota_headroom(e: dict) -> int:
    """``iam.quotaHeadroom`` of the env (roles / managed policies kept free after the deploy), default 10."""
    v = (e.get("iam") or {}).get("quotaHeadroom")
    if v is None:
        return QUOTA_HEADROOM_DEFAULT
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise UsageError(f"iam.quotaHeadroom {v!r}: use a whole number >= 0")
    return v


def template_quota_counts(asm: Path, stacks: list[str] | None = None) -> dict:
    """Quota-counted resources in the synthesized templates, ``{"iam": ..., "cloudfront": ...}``: of ``stacks``
    (their own templates), or of every template in the assembly (nested stacks included) when ``stacks`` is None.
    Used for stacks that do not exist yet: a new stack gets no change set and `cdk diff` may not render its
    nested stacks."""
    out = _quota_counters()
    files = sorted(asm.glob("*.template.json")) if stacks is None else \
        [asm / f"{s}.template.json" for s in stacks]
    for f in files:
        if not f.is_file():
            continue
        try:
            res = (util.read_json(f).get("Resources") or {}).values()
        except Exception:  # noqa: BLE001 - an unreadable template is the build's problem, not the quota check's
            continue
        for r in res:
            rtype = (r or {}).get("Type")
            if rtype in QUOTA_KINDS:
                _count(out, rtype, "added")
    return out


def template_iam_counts(asm: Path, stacks: list[str] | None = None) -> dict:
    """IAM roles and managed policies in the synthesized templates (see template_quota_counts)."""
    return template_quota_counts(asm, stacks)["iam"]


def template_cloudfront_counts(asm: Path, stacks: list[str] | None = None) -> dict:
    """CloudFront cache / origin request / response headers policies and distributions in the templates."""
    return template_quota_counts(asm, stacks)["cloudfront"]


def _merge_counts(blank, *counts: dict) -> dict:
    out = blank()
    for c in counts:
        for k in out:
            for f in ("added", "replaced"):
                out[k][f] += ((c or {}).get(k) or {}).get(f, 0)
    return out


def _merge_iam(*counts: dict) -> dict:
    return _merge_counts(_iam_counter, *counts)


def _merge_cf(*counts: dict) -> dict:
    return _merge_counts(_cf_counter, *counts)


def _first_install_counts(ctx, env: str, manifest: dict, target: str, base_override: str | None) -> dict | None:
    """Template counts ``{"iam", "cloudfront"}`` of the whole assembly when the env's core stack is not deployed
    yet, else None."""
    from vra import vams_resources
    try:
        cfn = ctx.aws.client(env, "cloudformation")
        if vams_resources.stack_exists(cfn, ctx.ledger.core_stack_name(env, base_override)):
            return None
    except Exception:  # noqa: BLE001 - the diff counts stand; the CloudFormation read is only a refinement
        return None
    return template_quota_counts(assembly_dir(ctx, manifest, target))


def _first_install_merge(diff_counts: dict, template_counts: dict) -> dict:
    """First install: nothing exists, so the larger of the diff's and the templates' additions is the need."""
    return {k: {"added": max(template_counts[k]["added"], (diff_counts.get(k) or {}).get("added", 0)), "replaced": 0}
            for k in template_counts}


def iam_quota_check(ctx, env: str, counts: dict, blocking: bool = True) -> dict:
    """Stop before `cdk deploy` (NeedsHuman, exit 20) when the account's IAM quotas cannot hold what the change
    adds plus what it replaces (a replaced role briefly exists twice): ``Roles + needed + headroom > RolesQuota``,
    same for managed policies. Reads iam:GetAccountSummary only when the change needs a role or a policy
    (pre-created-roles envs need none). A denied read is an IT request (exit 10). ``blocking=False`` (rollback)
    returns a warning instead of stopping."""
    headroom = quota_headroom(ctx.ledger.env(env))
    need = {k: v["added"] + v["replaced"] for k, v in counts.items()}
    res: dict[str, Any] = {"needed": need, "counts": counts, "headroom": headroom}
    if not any(need.values()):
        res["skipped"] = "the change adds or replaces no IAM role or managed policy"
        return res
    if ctx.dry_run:
        res["dryRun"] = True
        return res
    try:
        summary = ctx.aws.client(env, "iam").get_account_summary()["SummaryMap"]
    except Exception as exc:  # noqa: BLE001
        if permissions.is_denied(exc):
            if not blocking:
                res["unverified"] = "iam:GetAccountSummary denied"
                return res
            raise PermissionDenied(f"cannot read the IAM quotas of {env} (iam:GetAccountSummary) for the role-quota "
                                   f"check", permissions.classify_exception(exc, "iam"), env=env) from exc
        raise
    over = []
    for key, used_k, quota_k in IAM_QUOTA_TYPES.values():
        used, quota = summary.get(used_k), summary.get(quota_k)
        res[used_k], res[quota_k] = used, quota
        if need[key] and used is not None and quota is not None and used + need[key] + headroom > quota:
            c = counts[key]
            over.append(f"{used_k} {used} + {need[key]} needed ({c['added']} added, {c['replaced']} replaced) + "
                        f"headroom {headroom} > {quota_k} {quota}")
    res["ok"] = not over
    if over and blocking:
        raise NeedsHuman(f"the IAM quota of {env}'s account cannot hold this deploy: {'; '.join(over)}. A replaced "
                         f"role exists twice while CloudFormation swaps it (a permission-boundary or role-prefix "
                         f"switch replaces every VAMS role), so cdk deploy would fail and roll back. Raise the "
                         f"account's IAM quota (Service Quotas > IAM > Roles per account / Managed policies per "
                         f"account) or free roles, then resume", {"iamQuota": res})
    if over:
        res["warning"] = "; ".join(over)
    return res


# ===================================================================== CloudFront quota check
CF_QUOTA_HEADROOM_DEFAULT = 2
CF_QUOTA_REGION = "us-east-1"   # CloudFront quotas are global; Service Quotas serves them from us-east-1 (aws)


def cloudfront_quota_headroom(e: dict) -> int:
    """``cloudfront.quotaHeadroom`` of the env (policies / distributions kept free after the deploy), default 2."""
    v = (e.get("cloudfront") or {}).get("quotaHeadroom")
    if v is None:
        return CF_QUOTA_HEADROOM_DEFAULT
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise UsageError(f"cloudfront.quotaHeadroom {v!r}: use a whole number >= 0")
    return v


def _cf_in_use(cf, method: str, list_key: str) -> int:
    """Custom policies (or distributions) in the account: the Quantity of every page of the list call."""
    kw: dict[str, Any] = {} if method == "list_distributions" else {"Type": "custom"}
    total, marker, pages = 0, None, 0
    while True:
        page = getattr(cf, method)(**kw, **({"Marker": marker} if marker else {})).get(list_key) or {}
        total += int(page.get("Quantity") or 0)
        marker = page.get("NextMarker")
        pages += 1
        if not marker or pages > 100:
            return total


def _cf_quota_value(sq, code: str) -> tuple[float | None, str | None]:
    """The applied quota value, or (None, reason) when Service Quotas has none for the account. A denial raises."""
    try:
        return float(sq.get_service_quota(ServiceCode="cloudfront", QuotaCode=code)["Quota"]["Value"]), None
    except Exception as exc:  # noqa: BLE001
        if permissions.is_denied(exc):
            raise
        return None, f"{type(exc).__name__}: {exc}"[:200]


def cloudfront_quota_check(ctx, env: str, counts: dict, blocking: bool = True) -> dict:
    """Stop before `cdk deploy` (NeedsHuman, exit 20) when the account's CloudFront quotas cannot hold what the
    change adds plus what it replaces: ``in use + needed + headroom > quota`` for custom cache policies, origin
    request policies, response headers policies and distributions. Reads nothing when the change adds no
    CloudFront resource (ALB and GovCloud deployments, ordinary updates). In use: cloudfront:List* (``--type
    custom``); quota: servicequotas:GetServiceQuota (service code ``cloudfront``, us-east-1 in the aws partition),
    else the documented AWS default, named in the result. A denied read is an IT request (exit 10).
    ``blocking=False`` (rollback) returns a warning instead of stopping."""
    headroom = cloudfront_quota_headroom(ctx.ledger.env(env))
    need = {k: v["added"] + v["replaced"] for k, v in counts.items()}
    res: dict[str, Any] = {"needed": need, "counts": counts, "headroom": headroom}
    if not any(need.values()):
        res["skipped"] = "the change adds or replaces no CloudFront policy or distribution"
        return res
    if ctx.dry_run:
        res["dryRun"] = True
        return res
    try:
        partition = ctx.aws.partition(env)
    except Exception:  # noqa: BLE001 - the env's region decides then
        partition = None
    region = CF_QUOTA_REGION if partition in (None, "aws") else None
    quotas: dict[str, dict] = {}
    over = []
    service = "cloudfront"
    try:
        cf = ctx.aws.client(env, "cloudfront")
        # boto3 names the service "service-quotas"; its IAM prefix is servicequotas
        sq = ctx.aws.client(env, "service-quotas", region=region)
        for key, code, name, default, method, list_key in CF_QUOTA_TYPES.values():
            if not need[key]:
                continue
            service = "cloudfront"
            used = _cf_in_use(cf, method, list_key)
            service = "service-quotas"
            value, why = _cf_quota_value(sq, code)
            q = {"code": code, "name": name, "inUse": used, "needed": need[key],
                 "quota": int(value) if value is not None else default,
                 "quotaSource": "service-quotas" if value is not None else "aws-default"}
            if why:
                q["note"] = f"quota not readable ({why}); using the AWS default {default}"
            quotas[key] = q
            if used + need[key] + headroom > q["quota"]:
                c = counts[key]
                dflt = " (AWS default, quota not readable)" if value is None else ""
                over.append(f"{name} ({code}): {used} in use + {need[key]} needed ({c['added']} added, "
                            f"{c['replaced']} replaced) + headroom {headroom} > quota {q['quota']}{dflt}")
    except Exception as exc:  # noqa: BLE001
        if permissions.is_denied(exc):
            if not blocking:
                res["unverified"] = f"{service} read denied"
                return res
            raise PermissionDenied(f"cannot read the CloudFront quotas of {env} ({service}) for the CloudFront "
                                   f"quota check", permissions.classify_exception(exc, service), env=env) from exc
        raise
    res["quotas"] = quotas
    notes = [q["note"] for q in quotas.values() if q.get("note")]
    if notes:
        res["notes"] = notes
    res["ok"] = not over
    if over and blocking:
        raise NeedsHuman(f"the CloudFront quota of {env}'s account cannot hold this deploy: {'; '.join(over)}. A "
                         f"VAMS CloudFront deployment adds 2 cache policies, 1 origin request policy, 1 response "
                         f"headers policy and 1 distribution, so cdk deploy would fail and roll back. Request an "
                         f"increase (Service Quotas > Amazon CloudFront > the quota named above) or delete unused "
                         f"custom policies or distributions, then resume", {"cloudfrontQuota": res})
    if over:
        res["warning"] = "; ".join(over)
    return res


# ===================================================================== diff gate: CloudFormation change sets
DIFF_GATES = ("text", "changeset")
NO_CHANGES_RE = re.compile(r"didn't contain changes|No updates are to be performed", re.I)
POLICY_ATTRS = ("DeletionPolicy", "UpdateReplacePolicy")


def diff_gate(ctx, env: str) -> str:
    g = ctx.ledger.env(env).get("diffGate") or "text"
    if g not in DIFF_GATES:
        raise UsageError(f"envs/{env} diffGate {g!r}: use one of {', '.join(DIFF_GATES)}")
    return g


def change_set_name(run_id: str) -> str:
    return ("vra-gate-" + re.sub(r"[^A-Za-z0-9-]", "-", run_id))[:128]


def _describe_all(cfn, **kw) -> dict:
    """DescribeChangeSet with every page of Changes."""
    first = cfn.describe_change_set(**kw)
    out = dict(first)
    changes = list(first.get("Changes") or [])
    token = first.get("NextToken")
    while token:
        nxt = cfn.describe_change_set(**kw, NextToken=token)
        changes += nxt.get("Changes") or []
        token = nxt.get("NextToken")
    out["Changes"] = changes
    return out


def analyze_change_set(cfn, stack: str, name: str, depth: int = 0) -> dict:
    """Machine-readable data-protection check of one change set (and its nested change sets).

    Destructive: Remove, or Modify with Replacement True/Conditional, on a data-bearing type. Forward-only:
    Cognito user pool Schema changes, DeletionPolicy / UpdateReplacePolicy changes on data-bearing types.
    ``understood`` is False when a nested stack is modified but its change set is not available (fail closed)."""
    cs = _describe_all(cfn, ChangeSetName=name, StackName=stack) if depth == 0 else _describe_all(cfn, ChangeSetName=name)
    res = {"stack": cs.get("StackName") or stack, "status": cs.get("Status"), "changes": 0, "replacements": [],
           "forwardOnly": [], "unreadable": [], "nested": [], **_quota_counters()}
    if cs.get("Status") == "FAILED":
        if NO_CHANGES_RE.search(cs.get("StatusReason") or ""):
            res["noChanges"] = True
            return res
        res["unreadable"].append({"stack": res["stack"], "why": f"change set FAILED: {cs.get('StatusReason')}"})
        return res
    for ch in cs.get("Changes") or []:
        rc = ch.get("ResourceChange") or {}
        res["changes"] += 1
        rtype, action = rc.get("ResourceType", ""), rc.get("Action")
        lid = rc.get("LogicalResourceId")
        if rtype == "AWS::CloudFormation::Stack" and action == "Modify":
            if rc.get("ChangeSetId") and depth < 5:
                sub = analyze_change_set(cfn, lid, rc["ChangeSetId"], depth + 1)
                res["nested"].append(sub["stack"])
                for k in ("replacements", "forwardOnly", "unreadable"):
                    res[k] += sub[k]
                res["changes"] += sub["changes"]
                res["nested"] += sub["nested"]
                res["iam"] = _merge_iam(res["iam"], sub["iam"])
                res["cloudfront"] = _merge_cf(res["cloudfront"], sub["cloudfront"])
            else:
                res["unreadable"].append({"stack": res["stack"], "resource": lid,
                                          "why": "nested stack modified but its change set is not available"})
            continue
        if rtype in QUOTA_KINDS:
            if action == "Add":
                _count(res, rtype, "added")
            elif action == "Modify" and rc.get("Replacement") in ("True", "Conditional", True):
                _count(res, rtype, "replaced")
            continue
        if rtype not in DATA_RESOURCE_TYPES:
            continue
        where = {"type": rtype, "resource": lid, "stack": res["stack"], "physical": rc.get("PhysicalResourceId")}
        if action == "Remove":
            res["replacements"].append({"op": "remove", "policyAction": rc.get("PolicyAction"), **where})
            continue
        if action == "Modify" and rc.get("Replacement") in ("True", "Conditional", True):
            res["replacements"].append({"op": "replace", "replacement": str(rc.get("Replacement")),
                                        "policyAction": rc.get("PolicyAction"), **where})
            continue
        if action == "Modify":
            targets = [(d.get("Target") or {}) for d in rc.get("Details") or []]
            scope = set(rc.get("Scope") or [])
            if rtype == "AWS::Cognito::UserPool" and any(t.get("Name") == "Schema" for t in targets):
                res["forwardOnly"].append({"op": "modify", "property": "Schema",
                                           "why": FORWARD_ONLY[("AWS::Cognito::UserPool", "Schema")], **where})
            for attr in POLICY_ATTRS:
                if attr in scope or any(t.get("Attribute") == attr for t in targets):
                    res["forwardOnly"].append({"op": "modify", "property": attr,
                                               "why": f"{attr} change on a data-bearing resource", **where})
    return res


def changeset_diff(ctx, run, env: str, manifest: dict, target: str, gated: bool, sc) -> dict:
    """``diffGate: changeset``: `cdk deploy --method=prepare-change-set` for every DEPLOYED stack of the target
    (new stacks get no change set: creating one would leave a REVIEW_IN_PROGRESS stack behind), then
    DescribeChangeSet (nested stacks included) and DeleteChangeSet. Nothing is executed."""
    from vra import vams_resources
    asm = assembly_dir(ctx, manifest, target)
    stacks = (manifest["targets"].get(target) or {}).get("stacks") or []
    cfn = ctx.aws.client(env, "cloudformation")
    try:
        existing = [s for s in stacks if vams_resources.stack_exists(cfn, s)]
    except Exception as exc:  # noqa: BLE001
        if permissions.is_denied(exc):
            raise PermissionDenied(f"cannot read the stacks of {env} for the change-set gate",
                                   permissions.classify_exception(exc, "cloudformation"), env=env) from exc
        raise
    new = [s for s in stacks if s not in existing]
    name = change_set_name(run.run_id)
    out: dict[str, Any] = {"gate": "changeset", "changeSet": name, "newStacks": new, "stacks": {}}
    if existing:
        argv = cdk(manifest, "deploy", "--app", str(asm), "--method=prepare-change-set", "--change-set-name", name,
                   "--require-approval", "never", "--ci", "--exclusively", *existing, gated=gated, ctx=ctx)
        sub_env = {"CDK_DISABLE_VERSION_CHECK": "1", **ctx.aws.subprocess_env(env, "deploy")}
        pr = ctx.shell(sc.log_path).run(argv, cwd=asm.parent, env=sub_env, check=False, timeout=3600)
        text = (pr.stdout or "") + (pr.stderr or "")
        if pr.returncode != 0:
            denials = permissions.classify_text(text, "cloudformation")
            if denials:
                raise PermissionDenied(f"change-set gate for {env} was denied", denials, env=env)
            raise NeedsHuman(f"change-set gate for {env}: `cdk deploy --method=prepare-change-set` failed (exit "
                             f"{pr.returncode}); vra does not deploy without a checked change set",
                             {"tail": util.redact(text[-2000:])})
    try:
        for s in existing:
            try:
                out["stacks"][s] = analyze_change_set(cfn, s, name)
            except Exception as exc:  # noqa: BLE001
                if permissions.is_denied(exc):
                    raise PermissionDenied(f"DescribeChangeSet on {s} denied",
                                           permissions.classify_exception(exc, "cloudformation", s), env=env) from exc
                out["stacks"][s] = {"stack": s, "changes": 0, "replacements": [], "forwardOnly": [], "nested": [],
                                    "unreadable": [{"stack": s, "why": f"change set not readable: {exc}"}]}
    finally:
        deleted = []
        for s in existing:
            try:
                cfn.delete_change_set(ChangeSetName=name, StackName=s)
                deleted.append(s)
            except Exception as exc:  # noqa: BLE001
                if "does not exist" not in str(exc) and "ChangeSetNotFound" not in str(exc):
                    run.state.setdefault("notes", []).append(f"delete change set {name} on {s} by hand: {exc}")
        out["deleted"] = deleted
    reps = [r for v in out["stacks"].values() for r in v["replacements"]]
    fwd = [r for v in out["stacks"].values() for r in v["forwardOnly"]]
    unreadable = [r for v in out["stacks"].values() for r in v["unreadable"]]
    changes = sum(v["changes"] for v in out["stacks"].values())
    # new stacks have no change set: their roles, policies and CloudFront resources come from the synthesized
    # templates (all templates on a first install, when nested stacks are new as well)
    new_counts = template_quota_counts(asm, None if len(new) == len(stacks) else new) if new else _quota_counters()
    out["iam"] = _merge_iam(new_counts["iam"], *(v.get("iam") for v in out["stacks"].values()))
    out["cloudfront"] = _merge_cf(new_counts["cloudfront"], *(v.get("cloudfront") for v in out["stacks"].values()))
    out.update({"changes": changes, "replacements": reps, "forwardOnly": fwd, "destructive": reps + fwd,
                "unreadable": unreadable, "understood": not unreadable,
                "stacksWithDifferences": sum(1 for v in out["stacks"].values() if v["changes"]) + len(new)})
    return out


def cdk(manifest: dict, *args: str, gated: bool = False, ctx=None) -> list[str]:
    """The CDK CLI the build pinned: the vendored tarball in the bundle when present (integrity-checked by
    verify_bundle), else ``aws-cdk@<exact version>``. ``VRA_CDK_CLI`` is ignored for approval-gated envs."""
    tarball = None
    cli = manifest.get("cdkCli") or {}
    if cli.get("tarball") and ctx is not None:
        p = build_mod.build_dir(ctx, manifest["buildId"]) / cli["tarball"]
        tarball = str(p) if p.exists() else None
    return build_mod.cdk_argv(manifest.get("cdkCliVersion"), *args, allow_override=not gated, tarball=tarball)


def assembly_dir(ctx, manifest: dict, target: str) -> Path:
    return build_mod.build_dir(ctx, manifest["buildId"]) / "cdk.out" / target


def active_slot(ctx, env: str) -> str | None:
    last = ctx.ledger.last_good_deploy(env)
    return (last or {}).get("slot")


def stack_failure_text(ctx, env: str, stacks: list[str], depth: int = 0) -> str:
    """Collect *_FAILED StatusReasons from CloudFormation events (recursing into nested stacks)."""
    if depth > 3 or ctx.dry_run:
        return ""
    try:
        cfn = ctx.aws.client(env, "cloudformation")
    except Exception:  # noqa: BLE001
        return ""
    lines = []
    for s in stacks:
        try:
            events = cfn.describe_stack_events(StackName=s).get("StackEvents", [])[:200]
        except Exception as exc:  # noqa: BLE001
            if permissions.is_denied(exc):
                lines.append(str(exc))
            continue
        nested = []
        for ev in events:
            st = ev.get("ResourceStatus", "")
            if st.endswith("FAILED") and ev.get("ResourceStatusReason"):
                lines.append(f"{ev.get('LogicalResourceId')} {st}: {ev['ResourceStatusReason']}")
                if ev.get("ResourceType") == "AWS::CloudFormation::Stack" and ev.get("PhysicalResourceId", "").startswith("arn:") \
                        and ev.get("PhysicalResourceId") != ev.get("StackId"):
                    nested.append(ev["PhysicalResourceId"])
        if nested:
            lines.append(stack_failure_text(ctx, env, sorted(set(nested)), depth + 1))
    return "\n".join(x for x in lines if x)


# a stack CloudFormation could not roll back. A retry or a `vra resume` cannot deploy over it (a failed
# first create must be deleted, a failed update needs continue-update-rollback), whatever IT grants.
STUCK_STATES = ("ROLLBACK_FAILED", "UPDATE_ROLLBACK_FAILED", "DELETE_FAILED")
_FOLLOW_ON = ("Resource creation cancelled", "Resource update cancelled", "Embedded stack ",
              "The following resource(s) failed")


def first_failure(ctx, env: str, stacks: list[str], depth: int = 0) -> dict | None:
    """The earliest CREATE_FAILED / UPDATE_FAILED event that is a cause, not a follow-on (cancelled siblings, the
    nested-stack summary), across ``stacks`` and their nested stacks. Rollback-phase failures (DELETE_FAILED) are
    consequences and never the first failure."""
    if depth > 3 or ctx.dry_run:
        return None
    try:
        cfn = ctx.aws.client(env, "cloudformation")
    except Exception:  # noqa: BLE001
        return None
    best = None
    for s in stacks:
        try:
            events = cfn.describe_stack_events(StackName=s).get("StackEvents", [])[:200]
        except Exception:  # noqa: BLE001
            continue
        nested = set()
        for ev in events:
            st, why = ev.get("ResourceStatus", ""), ev.get("ResourceStatusReason") or ""
            if ev.get("ResourceType") == "AWS::CloudFormation::Stack" and st.endswith("FAILED") \
                    and str(ev.get("PhysicalResourceId", "")).startswith("arn:") \
                    and ev.get("PhysicalResourceId") != ev.get("StackId"):
                nested.add(ev["PhysicalResourceId"])
            if st in ("CREATE_FAILED", "UPDATE_FAILED") and why and not why.startswith(_FOLLOW_ON):
                hit = {"stack": s, "resource": ev.get("LogicalResourceId"), "type": ev.get("ResourceType"),
                       "status": st, "reason": util.redact(why[:600]), "at": ev.get("Timestamp")}
                if best is None or (hit["at"] and best["at"] and hit["at"] < best["at"]):
                    best = hit
        sub = first_failure(ctx, env, sorted(nested), depth + 1) if nested else None
        if sub and (best is None or (sub["at"] and best["at"] and sub["at"] < best["at"])):
            best = sub
    return best


STACK_VERSION_RE = re.compile(r"\(version:\s*v?(\d+\.\d+\.\d+)\)")


def deployed_vams_version(ctx, env: str, base_override: str | None = None) -> tuple[str | None, str | None]:
    """VAMS version of the live core stack, from its description ``(VAMS-CORE) (version:X.Y.Z)``.

    Returns (None, None) when the core stack does not exist (a true first install). Raises NeedsHuman when the
    stack exists but its version cannot be read, rather than guessing "fresh install" and skipping migrations.
    """
    stack = ctx.ledger.core_stack_name(env, base_override)
    try:
        cfn = ctx.aws.client(env, "cloudformation")
        resp = cfn.describe_stacks(StackName=stack)
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc):
            return None, None
        if permissions.is_denied(exc):
            raise PermissionDenied(f"cannot read {stack} to find the deployed VAMS version",
                                   permissions.classify_exception(exc, "cloudformation", stack), env=env) from exc
        raise
    s = resp["Stacks"][0]
    from vra import vams_resources
    if s.get("StackStatus") in vams_resources.NEVER_CREATED:  # a failed first create is still a first install
        return None, None
    m = STACK_VERSION_RE.search(s.get("Description") or "")
    if not m:
        raise NeedsHuman(f"{stack} is deployed but vra has no deploy history for {env} and the stack description "
                         f"does not name a VAMS version; cannot plan data migrations safely",
                         {"stack": stack, "description": s.get("Description")})
    return m.group(1), f"stack description of {stack}"


def stack_status(ctx, env: str, stack: str) -> str | None:
    try:
        return ctx.aws.client(env, "cloudformation").describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
    except Exception:  # noqa: BLE001
        return None


def cdk_deploy(ctx, env: str, manifest: dict, target: str, sc) -> dict:
    asm = assembly_dir(ctx, manifest, target)
    argv = cdk(manifest, "deploy", "--app", str(asm), "--all", "--require-approval", "never", "--progress", "events",
               "--ci", gated=bool(ctx.ledger.env(env).get("requiresApproval")), ctx=ctx)
    sub_env = {"CDK_DISABLE_VERSION_CHECK": "1", **ctx.aws.subprocess_env(env, "deploy")} if not ctx.dry_run else {}
    try:
        ctx.shell(sc.log_path).run(argv, cwd=asm.parent, env=sub_env, timeout=4 * 3600)
    except CommandFailed as exc:
        stacks = manifest["targets"][target].get("stacks") or []
        text = (exc.output or "") + "\n" + stack_failure_text(ctx, env, stacks)
        denials = permissions.classify_text(text, "cloudformation")
        stuck = {s: st for s in stacks if (st := stack_status(ctx, env, s)) in STUCK_STATES}
        if stuck:
            # not an IT block. Granting the denied calls (often rollback-phase ones) does not make the
            # step resumable; the stack needs cleanup and the first failure needs its own fix.
            first = first_failure(ctx, env, stacks)
            cause = f"first failure: {first['resource']} ({first['type']}) {first['status']}: {first['reason']}" \
                if first else "first failure not found in the stack events"
            perm = f"; {len(denials)} permission error(s) blocked the rollback (see denials)" if denials else ""
            raise RollbackFailed(
                f"cdk deploy to {env} failed and CloudFormation could not roll back: "
                f"{', '.join(f'{s} is {st}' for s, st in sorted(stuck.items()))}; {cause}{perm}. Fix the cause and "
                f"any denial, clean the stack up (delete a failed first install; continue-update-rollback after a "
                f"failed update), then retry",
                {"stuck": stuck, "firstFailure": {**first, "at": str(first["at"])} if first else None,
                 "denials": denials}) from exc
        if denials:
            raise PermissionDenied(f"cdk deploy to {env} was denied ({len(denials)} permission error(s))", denials,
                                   env=env) from exc
        raise VraError(f"cdk deploy to {env} failed", {"tail": util.redact(text[-3000:])}) from exc
    return {"target": target, "stacks": manifest["targets"][target].get("stacks"), "dryRun": ctx.dry_run}


def run_hook(ctx, cmd: str | list, env_vars: dict, sc, timeout: int = 1800) -> dict:
    import shlex
    argv = cmd if isinstance(cmd, list) else shlex.split(cmd)
    p = ctx.shell(sc.log_path).run(argv, cwd=ctx.ledger.root, env=env_vars, check=False, timeout=timeout)
    if p.returncode != 0 and not ctx.dry_run:
        raise VraError(f"hook {argv[0]} failed with exit {p.returncode}")
    return {"hook": argv[0], "exit": p.returncode}


CDK_ROLE_KINDS = ("deploy-role", "file-publishing-role", "image-publishing-role", "lookup-role")


def _trust_allows_set_source_identity(doc) -> bool:
    import json as _json
    import urllib.parse
    if isinstance(doc, str):
        doc = _json.loads(urllib.parse.unquote(doc))
    for st in (doc or {}).get("Statement", []):
        if st.get("Effect") != "Allow":
            continue
        acts = st.get("Action") or []
        acts = [acts] if isinstance(acts, str) else acts
        if any(a in ("sts:SetSourceIdentity", "sts:*", "*") for a in acts):
            return True
    return False


def bootstrap_trust_check(ctx, env: str) -> dict:
    """With sts:SourceIdentity on the env session, every role cdk chains into must trust sts:SetSourceIdentity
    (AWS requires it in the target role's trust for role chaining). The stock CDK bootstrap template does not
    grant it, so a deploy would fail at the first asset upload. Checked here, before anything changes."""
    from vra.ledger import source_identity_on
    e = ctx.ledger.env(env)
    if not (source_identity_on(e) and getattr(ctx.aws, "source_identity_value", None)):
        return {"skipped": "no sts:SourceIdentity on this env's sessions"}
    if ctx.dry_run:
        return {"dryRun": True}
    q = ctx.ledger.project.get("cdk", {}).get("qualifier", "hnb659fds")
    iam = ctx.aws.client(env, "iam")
    missing, checked, unverified = [], [], []
    for kind in CDK_ROLE_KINDS:
        name = f"cdk-{q}-{kind}-{e['account']}-{e['region']}"
        try:
            doc = iam.get_role(RoleName=name)["Role"]["AssumeRolePolicyDocument"]
        except Exception as exc:  # noqa: BLE001
            unverified.append(f"{name}: {permissions.client_error_parts(exc)[0] or type(exc).__name__}")
            continue
        checked.append(name)
        if not _trust_allows_set_source_identity(doc):
            missing.append(name)
    if missing:
        raise NeedsHuman(f"{env} sessions carry sts:SourceIdentity, but the CDK bootstrap role(s) {', '.join(missing)} "
                         f"do not trust sts:SetSourceIdentity, so cdk deploy would be denied. Re-bootstrap with the "
                         f"patched template (iam/scripts/patch-bootstrap-template.py, iam/cdk-bootstrap.md), or set "
                         f"setSourceIdentity: false for {env} and RequireSourceIdentity=false on its deploy role",
                         {"roles": missing})
    if unverified:
        ctx_note = f"{env}: could not read CDK bootstrap role trust ({'; '.join(unverified[:4])}); not verified"
        return {"checked": checked, "unverified": unverified, "note": ctx_note}
    return {"checked": checked}


# ===================================================================== gates shared by deploy/promote
def assembly_matches_ledger(ctx, env: str, manifest: dict, target: str, base_override: str | None) -> dict:
    """The core stack the bundle deploys is the one the engine checkpoints, inspects and migrates.

    Fails closed: a bundle synthesized from an overlay that names another deployment would otherwise upgrade
    that deployment with no checkpoint and no data migrations."""
    expected = ctx.ledger.core_stack_name(env, base_override)
    stacks = (manifest.get("targets", {}).get(target) or {}).get("stacks") or []
    if not any("-core-" in s for s in stacks):
        return {"expected": expected, "stacks": stacks, "checked": False}
    if expected not in stacks:
        raise NeedsHuman(f"bundle target {target} deploys {stacks[:4]}, but the ledger names {expected}: the overlay "
                         f"config and env.yaml disagree on the deployment. Align them and rebuild")
    return {"expected": expected, "checked": True}


def promotion_evidence_needed(ctx, env: str, action: str) -> str | None:
    """`vra deploy` into an env that promotes from another must show the same evidence as `vra promote`."""
    e = ctx.ledger.env(env)
    return e.get("promotesFrom") if action == "deploy" else None


def drift_check(ctx, env: str, stacks: list[str], sc) -> dict:
    """CloudFormation drift detection on the deployed stacks of this target (read-only for resources)."""
    from vra import vams_resources
    if ctx.dry_run:
        return {"dryRun": True}
    cfn = ctx.aws.client(env, "cloudformation")
    out: dict[str, Any] = {"stacks": {}, "drifted": []}
    for s in stacks:
        try:
            if not vams_resources.stack_exists(cfn, s):
                out["stacks"][s] = "NOT_DEPLOYED"
                continue
            did = cfn.detect_stack_drift(StackName=s)["StackDriftDetectionId"]
            deadline = time.monotonic() + 900
            st = cfn.describe_stack_drift_detection_status(StackDriftDetectionId=did)
            while st.get("DetectionStatus") == "DETECTION_IN_PROGRESS" and time.monotonic() < deadline:
                time.sleep(backup._poll_seconds())
                st = cfn.describe_stack_drift_detection_status(StackDriftDetectionId=did)
        except Exception as exc:  # noqa: BLE001
            if permissions.is_denied(exc):
                raise PermissionDenied(f"drift detection on {s} denied",
                                       permissions.classify_exception(exc, "cloudformation", s), env=env) from exc
            raise
        status = st.get("StackDriftStatus") or st.get("DetectionStatus")
        out["stacks"][s] = status
        if st.get("DetectionStatus") == "DETECTION_FAILED":
            sc.log(f"drift detection incomplete on {s}: {st.get('DetectionStatusReason')}")
        if status == "DRIFTED":
            drifts = cfn.describe_stack_resource_drifts(
                StackName=s, StackResourceDriftStatusFilters=["MODIFIED", "DELETED"]).get("StackResourceDrifts", [])
            out["drifted"] += [{"stack": s, "resource": d.get("LogicalResourceId"), "type": d.get("ResourceType"),
                                "status": d.get("StackResourceDriftStatus")} for d in drifts[:100]]
            if not drifts:
                out["drifted"].append({"stack": s, "resource": None, "status": "DRIFTED"})
    return out


def _require_extra_approval(ctx, env: str, build_id: str, action: str, approval: str | None, what: str) -> dict | None:
    """--allow-replacements / --accept-drift need an approval whose scope names them in gated envs, and always
    when an agent runs vra (VRA_AGENT=1): an agent cannot waive a data-protection gate on its own."""
    e = ctx.ledger.env(env)
    if not (e.get("requiresApproval") or os.environ.get("VRA_AGENT") == "1"):
        return None
    try:
        return approvals.check(ctx, env, build_id, action, approval, force_required=True)
    except NeedsHuman as exc:
        raise NeedsHuman(f"{what} on {env} needs an approval with scope [{action}]: {exc.message}", exc.detail) from exc


# ===================================================================== deploy
def do_deploy(ctx, run, env: str, build_id: str, approval: str | None = None, mode: str | None = None,
              emergency: bool = False, origin: str = "cli", prefix: str = "", allow_replacements: bool = False,
              action: str = "deploy", promote_from: str | None = None, accept_drift: bool = False,
              abort_executions: bool = False) -> dict:
    e = ctx.ledger.env(env)
    p = f"{prefix}deploy.{env}."
    manifest = build_mod.load_manifest(ctx, build_id)
    mode = mode or e.get("deployMode", "in-place")
    gated = bool(e.get("requiresApproval"))

    # The lock is taken before the gates (no time-of-check/time-of-use gap) and the gates re-run on resume.
    with env_lock(ctx, run, env):
        frm = promote_from or promotion_evidence_needed(ctx, env, action)
        prev_gate = (run.find_step(p + "gate") or {}).get("result")

        def _gate(sc):
            if origin == "watch" and (not e.get("autoDeploy") or gated):
                raise NeedsHuman(f"watch never deploys to {env}: autoDeploy={e.get('autoDeploy')} "
                                 f"requiresApproval={gated}")
            if manifest["lane"] != e["lane"]:
                raise NeedsHuman(f"build {build_id} is a {manifest['lane']}-lane build; {env} is a {e['lane']}-lane env")
            if mode == "ab" and not e.get("ab"):
                raise UsageError(f"--mode ab needs ab.slots in envs/{env}/env.yaml")
            waived = (manifest.get("scanners") or {}).get("waived")
            if waived and is_prod_class(ctx.ledger, env):
                raise NeedsHuman(f"build {build_id} was built with --allow-missing-scanners ({', '.join(waived)} did "
                                 f"not run); it can never be deployed to {env}. Rebuild with the scanners installed")
            if gated and not manifest.get("cdkCliVersion"):
                raise NeedsHuman(f"build {build_id} records no CDK CLI version; {env} only deploys with the CLI the "
                                 f"build pinned (rebuild from a tree with infra/package-lock.json)")
            ctx.change_authors = manifest.get("changeAuthors") or []
            rec = approvals.check(ctx, env, build_id, action, approval)
            approvals.record(run, rec)
            identity.record(run, ctx.identity)
            win = approvals.check_change_window(ctx, env, emergency, build_id, approval)
            slot = None
            target = env
            if mode == "ab":
                act = active_slot(ctx, env)
                slot = "green" if act == "blue" else "blue"
                target = build_mod.target_name(env, slot)
            integ = build_mod.verify_bundle(ctx, manifest, target, gated=gated)
            integ["tagSignature"] = build_mod.verify_release_tag(ctx, manifest)
            res = {"target": target, "mode": mode, "slot": slot,
                   "activeSlot": active_slot(ctx, env) if mode == "ab" else None,
                   "approval": rec and rec.get("path"), "approvalFields": approvals.history_fields(rec),
                   "changeWindow": win, "bundle": integ}
            if prev_gate and (prev_gate.get("target"), prev_gate.get("slot")) != (target, slot):
                raise NeedsHuman(f"resumed deploy of {env} would now target {target} (was {prev_gate.get('target')}); "
                                 f"the env changed underneath this run - start a new run")
            return res

        def _promotion():
            # same evidence for `vra deploy` as for `vra promote`
            sid = "promote.check" if promote_from else p + "promotion-check"
            run.step(sid, lambda sc: check_promotion(ctx, frm, env, build_id), "promotion checks", revalidate=True)

        if promote_from:
            _promotion()
        gate = run.step(p + "gate", _gate, f"gates for {env}", revalidate=True)
        if frm and not promote_from:
            _promotion()
        elif not frm and gated and origin != "watch":
            run.state.setdefault("notes", []).append(
                f"{env} is approval-gated but has no promotesFrom: the deploy is checked on approval and SoD only")
        target, slot = gate["target"], gate["slot"]
        base_override = e["ab"]["slots"][slot] if slot else None
        active_base = e["ab"]["slots"][gate["activeSlot"]] if gate.get("activeSlot") else None
        run.step(p + "stack-identity", lambda sc: assembly_matches_ledger(ctx, env, manifest, target, base_override),
                 "bundle stacks match the ledger", revalidate=True)
        # the env's IT role package is applied (recorded in history) and the bundle was synthesized with it
        run.step(p + "iam", lambda sc: iam_mod.deploy_gate(ctx, run, env, manifest, p + "iam"),
                 f"IAM package of {env} matches the bundle", revalidate=True)

        run.step(p + "preflight", lambda sc: permissions.preflight(ctx, env, "deploy"), "IAM preflight")
        run.step(p + "source-identity-trust", lambda sc: bootstrap_trust_check(ctx, env),
                 "CDK bootstrap roles accept sts:SourceIdentity")

        def _checkpoint(sc):
            if not e.get("backupBeforeDeploy", True):
                return {"skipped": "backupBeforeDeploy=false"}
            if ctx.dry_run:
                return {"dryRun": True}
            from vra import vams_resources
            cfn = ctx.aws.client(env, "cloudformation")
            live = ctx.ledger.core_stack_name(env, active_base) if mode == "ab" else ctx.ledger.core_stack_name(env)
            if not vams_resources.stack_exists(cfn, live):
                return {"skipped": f"{live} not deployed yet (first install)"}
            # the checkpoint reads every bucket: registered asset buckets are re-checked first
            from vra import buckets
            buckets.require(ctx, run, env, ["backup"], p + "checkpoint.registered-buckets", f"checkpoint of {env}",
                            base_override=active_base if mode == "ab" else None)
            res = backup.create_checkpoint(ctx, env, run, sc, "pre-deploy", strict=backup.strict_for(ctx, env))
            run.state["checkpoint"] = res["id"]
            run.save()
            return res

        ckpt = run.step(p + "checkpoint", _checkpoint, "backup checkpoint")

        stacks = (manifest["targets"].get(target) or {}).get("stacks") or []
        drift_on = e.get("driftCheck") if e.get("driftCheck") is not None else gated

        def _drift(sc):
            res = drift_check(ctx, env, stacks, sc)
            if res.get("drifted"):
                if gated and not accept_drift:
                    raise NeedsHuman(f"{env} has drifted from its CloudFormation templates ({len(res['drifted'])} "
                                     f"resource(s)); review them, then re-run with --accept-drift and an approval "
                                     f"with scope [accept-drift], or revert the out-of-band change",
                                     {"drift": res["drifted"]})
                if gated:
                    res["acceptedBy"] = (_require_extra_approval(ctx, env, build_id, "accept-drift", approval,
                                                                 "--accept-drift") or {}).get("approver")
                run.state.setdefault("notes", []).append(
                    f"{env}: CloudFormation drift on {len(res['drifted'])} resource(s); the deploy overwrites it")
                run.save()
            return res

        if drift_on and stacks:
            run.step(p + "drift", _drift, "drift detection")
        else:
            run.skip(p + "drift", "driftCheck off for this env" if not drift_on else "no stacks in the bundle")

        def _changeset(sc):
            parsed = changeset_diff(ctx, run, env, manifest, target, gated, sc)
            out = run.dir / f"change-sets-{env}.json"
            util.write_json(out, parsed)
            sc.artifact(out)
            if not parsed["understood"]:
                raise NeedsHuman(f"change-set gate for {env}: {len(parsed['unreadable'])} change set(s) could not be "
                                 f"checked ({parsed['unreadable'][0].get('why')}); vra does not deploy what it cannot "
                                 f"check. See {out.name}", {"unreadable": parsed["unreadable"], "report": str(out)})
            return out, parsed

        def _diff(sc):
            if diff_gate(ctx, env) == "changeset" and not ctx.dry_run:
                out, parsed = _changeset(sc)
                if parsed["destructive"] and mode == "in-place":
                    if not allow_replacements:
                        raise NeedsHuman(f"change sets for {env} replace/remove or irreversibly change "
                                         f"{len(parsed['destructive'])} data-bearing resource(s); review {out.name}, "
                                         f"then use --mode ab or --allow-replacements",
                                         {"destructive": parsed["destructive"], "report": str(out)})
                    rec = _require_extra_approval(ctx, env, build_id, "allow-replacements", approval,
                                                  "--allow-replacements")
                    parsed["allowedBy"] = (rec or {}).get("approver") or ctx.initiator
                parsed["iamQuota"] = iam_quota_check(ctx, env, parsed["iam"])
                parsed["cloudfrontQuota"] = cloudfront_quota_check(ctx, env, parsed["cloudfront"])
                return {"report": str(out), **{k: v for k, v in parsed.items() if k != "stacks"}}
            asm = assembly_dir(ctx, manifest, target)
            argv = cdk(manifest, "diff", "--app", str(asm), "--all", gated=gated, ctx=ctx)
            sub_env = {"CDK_DISABLE_VERSION_CHECK": "1"}
            if not ctx.dry_run:
                sub_env.update(ctx.aws.subprocess_env(env, "deploy"))
            pr = ctx.shell(sc.log_path).run(argv, cwd=asm.parent, env=sub_env, check=False, mutating=False)
            text = (pr.stdout or "") + (pr.stderr or "")
            out = run.dir / f"cdk-diff-{env}.txt"
            util.atomic_write(out, util.redact(text))
            sc.artifact(out)
            parsed = parse_diff(text)
            if pr.returncode != 0 and not ctx.dry_run:
                denials = permissions.classify_text(text, "cdk")
                if denials:
                    raise PermissionDenied(f"cdk diff for {env} was denied", denials, env=env)
                # Fail closed: without a diff there is no data-protection check, so there is no deploy.
                raise NeedsHuman(f"cdk diff for {env} failed (exit {pr.returncode}); vra does not deploy without a "
                                 f"diff. See {out.name}", {"diff": str(out), "exit": pr.returncode})
            if not parsed["understood"] and not ctx.dry_run:
                raise NeedsHuman(f"cdk diff for {env} reports {parsed['stacksWithDifferences']} stack(s) with "
                                 f"differences but vra could not parse any resource change (CLI output format?); "
                                 f"review {out.name} - vra does not deploy what it cannot check",
                                 {"diff": str(out)})
            if parsed["destructive"] and mode == "in-place":
                if not allow_replacements:
                    raise NeedsHuman(f"cdk diff for {env} replaces/removes or irreversibly changes "
                                     f"{len(parsed['destructive'])} data-bearing resource(s); review {out.name}, "
                                     f"then use --mode ab or --allow-replacements",
                                     {"destructive": parsed["destructive"], "diff": str(out)})
                rec = _require_extra_approval(ctx, env, build_id, "allow-replacements", approval,
                                              "--allow-replacements")
                parsed["allowedBy"] = (rec or {}).get("approver") or ctx.initiator
            # IAM and CloudFront quotas: on a first install (or a new A/B slot) the templates
            # are the complete count
            first = None if ctx.dry_run else _first_install_counts(ctx, env, manifest, target, base_override)
            counts = parsed["iam"] if first is None else _first_install_merge(parsed["iam"], first["iam"])
            cf_counts = parsed["cloudfront"] if first is None else \
                _first_install_merge(parsed["cloudfront"], first["cloudfront"])
            parsed["iamQuota"] = iam_quota_check(ctx, env, counts)
            parsed["cloudfrontQuota"] = cloudfront_quota_check(ctx, env, cf_counts)
            gate_kind = diff_gate(ctx, env)
            return {"report": str(out), "gate": "text", **parsed,
                    **({"note": "diffGate=changeset runs on the real deploy; the dry run used cdk diff"}
                       if gate_kind == "changeset" else {})}

        run.step(p + "diff", _diff, "cdk diff report" if diff_gate(ctx, env) == "text" else "change-set gate")

        prev_version = None
        last = ctx.ledger.last_good_deploy(env)
        if last:
            prev_version = last.get("vamsVersion")
        mig_root = build_mod.build_dir(ctx, build_id) / "migrations"

        def _plan(sc):
            frm_v, source = prev_version, "ledger history" if prev_version else None
            if not frm_v:
                # No version in the vra history (e.g. an env imported with `vra ledger import`): the deployed stack's
                # description carries "(version:X.Y.Z)". Treating it as a fresh install would skip migrations.
                frm_v, source = deployed_vams_version(ctx, env, active_base if mode == "ab" else None)
            pl = migrate.plan(ctx, env, mig_root, frm_v, manifest["vamsVersion"])
            if pl.get("requiresAB") and mode == "in-place":
                raise NeedsHuman(f"upgrade {frm_v} -> {manifest['vamsVersion']} requires an A/B deployment "
                                 f"(see upgrade matrix); re-run with --mode ab", {"plan": migrate._json_plan(pl)})
            if pl.get("steps") and (ckpt or {}).get("warnings") and not backup.degraded_allowed(ctx, env):
                raise NeedsHuman(f"{env} has data migrations planned but its checkpoint is incomplete "
                                 f"({'; '.join(ckpt['warnings'][:3])}); fix it or set backup.allowDegraded",
                                 {"warnings": ckpt["warnings"]})
            return {**migrate._json_plan(pl), "fromSource": source}

        mplan = run.step(p + "migration-plan", _plan, "migration plan")
        prev_version = mplan.get("from") or prev_version

        # registered asset buckets, re-checked BEFORE cdk deploy changes anything: migrations and the A/B slot
        # copy read/write bucket data and stop here (exit 10); a deploy that touches no bucket data only records
        # the pending IT request in its report
        from vra import buckets
        ab_copy = mode == "ab" and bool(active_base)
        bucket_wfs = (["migrate"] if mplan.get("steps") else []) + \
            (["data-copy-source", "data-copy-target"] if ab_copy else [])
        if bucket_wfs:
            buckets.require(ctx, run, env, bucket_wfs, p + "registered-buckets",
                            "data migrations" if mplan.get("steps") else "A/B slot data copy", role="deploy",
                            base_override=active_base if mode == "ab" else None)
        elif not (ckpt or {}).get("id"):
            buckets.require(ctx, run, env, ["backup", "restore", "migrate"], p + "registered-buckets",
                            f"deploy of {env}", role="deploy", blocking=False,
                            base_override=active_base if mode == "ab" else None)
        if mplan.get("steps"):
            # data migrations rewrite tables that running workflows may also write: list them (abort only on request)
            from vra import freeze
            freeze.workflow_executions_step(ctx, run, env, p + "workflow-executions", "data migrations",
                                            abort=abort_executions)

        def _window(label):
            return run.step(p + f"window.{label}",
                            lambda sc: approvals.check_change_window(ctx, env, emergency, build_id, approval),
                            f"change window before {label}", revalidate=True)

        try:
            _window("cdk-deploy")
            if not ctx.dry_run:
                mark_dirty(run, env, f"cdk deploy of {build_id} to {target}")
            run.step(p + "cdk-deploy", lambda sc: cdk_deploy(ctx, env, manifest, target, sc), f"cdk deploy {target}")
            if mode == "ab" and active_base:
                from vra import datacopy
                run.step(p + "ab-data-copy", lambda sc: datacopy.copy_slots(ctx, run, env, active_base, base_override, sc),
                         "copy data into the new slot")
            if mplan["steps"]:
                _window("migrations")
                migrate.do_migrate(ctx, run, env, mig_root, prev_version, manifest["vamsVersion"], prefix=p,
                                   ssm_prefix=ctx.ledger.ssm_prefix(env, base_override), check_buckets=False)
            old_build = (last or {}).get("build")
            old_target = (build_mod.target_name(env, last["slot"]) if (last or {}).get("slot") else env) \
                if last else None
            from vra import reindex
            run.step(p + "reindex",
                     lambda sc: reindex.post_deploy(ctx, run, env, sc, prev_version, manifest["vamsVersion"], build_id,
                                                    target, old_build, old_target,
                                                    list(run.state.get("migrationsApplied") or []),
                                                    base_override if mode == "ab" else None,
                                                    "the A/B slot copy reindexed the new slot" if ab_copy else None),
                     "search reindex after the upgrade")
            live = (e.get("ab") or {}).get("slotLiveTest", {}).get(slot) if slot else None
            run.step(p + "smoke", lambda sc: _smoke(ctx, run, env, sc, live, [build_id]), "post-deploy smoke")
            if mode == "ab":
                def _switch(sc):
                    hook = e["ab"].get("dnsSwitchHook")
                    if not hook:
                        conf = run.require_confirmation(
                            sc.step["id"], f"new slot {slot} is deployed and smoke-tested; no ab.dnsSwitchHook "
                            f"configured - switch DNS to {ctx.ledger.core_stack_name(env, base_override)} manually")
                        return {"manual": True, "confirmedBy": conf["by"], "confirmedAt": conf["at"]}
                    return run_hook(ctx, hook, {"VRA_ENV": env, "VRA_FROM_SLOT": gate.get("activeSlot") or "",
                                                "VRA_TO_SLOT": slot,
                                                "VRA_TARGET_STACK": ctx.ledger.core_stack_name(env, base_override),
                                                "VRA_SSM_PREFIX": ctx.ledger.ssm_prefix(env, base_override)}, sc)
                run.step(p + "dns-switch", _switch, "DNS switch hook")
        except (PermissionDenied, NeedsHuman, RollbackFailed):
            # RollbackFailed from cdk-deploy: CloudFormation already failed to roll back; a vra
            # rollback on top would deploy over a stuck stack
            raise
        except VraError as exc:
            return _on_failure(ctx, run, env, manifest, exc, gate, ckpt, origin, p)

        def _record(sc):
            ctx.ledger.append_history(env, {
                "action": "deploy", "build": build_id, "commit": manifest["commit"], "lane": manifest["lane"],
                "vamsVersion": manifest["vamsVersion"], "previousVamsVersion": prev_version, "result": "SUCCEEDED",
                "runId": run.run_id, "mode": mode, "slot": slot, "checkpoint": (ckpt or {}).get("id"),
                "migrationsApplied": run.state.get("migrationsApplied", []), **(gate.get("approvalFields") or {}),
                "initiator": ctx.initiator, "initiatorSource": ctx.identity.source, "origin": origin,
                "promotedFrom": frm, "scannersWaived": (manifest.get("scanners") or {}).get("waived") or None,
                "manifestSha256": _manifest_sha(ctx, build_id)},
                dry_run=ctx.dry_run)
            return True

        run.step(p + "record", _record, "record history")
        mark_clean(run, env)
    if mode == "ab":
        run.state.setdefault("notes", []).append(
            f"{env}: slot {gate.get('activeSlot')} is now idle; retire it after the soak period "
            f"(never automated - data-bearing stacks).")
        run.save()
    return {"env": env, "build": build_id, "target": target, "mode": mode, "slot": slot,
            "migrations": [s["id"] for s in mplan["steps"]], "checkpoint": (ckpt or {}).get("id")}


def _manifest_sha(ctx, build_id: str) -> str | None:
    p = build_mod.build_dir(ctx, build_id) / "manifest.json"
    return util.sha256_file(p) if p.exists() else None


def _smoke(ctx, run, env, sc, live, builds=None):
    """Post-deploy smoke. Fails on any failed case, and when a required suite of the env (live-cli for gated
    envs and the envs they promote from) executed nothing, e.g. because liveTest.webUrl is unset. ``builds``: whose
    bundled VamsCLI wheel runs it (tests_runner.resolve_vamscli). The reads (web URL parameter, sign-in secret)
    use the run's own deploy identity, never ``testRoleArn``: that role trusts only the env's test CI identity
    (live test)."""
    jdir = run.junit_dir / f"smoke-{env}"
    res = tests_runner.live_cli_smoke(ctx, env, jdir, sc, live, builds, role="deploy")
    summ = tests_runner.aggregate_junit(jdir)
    if ctx.dry_run:
        return {"smoke": res, "summary": summ}
    if summ["failures"] or summ["errors"]:
        raise TestsFailed(f"post-deploy smoke failed on {env}", {"failed": summ["failed"]})
    if "live-cli" in required_suites(ctx.ledger, env) and summ["passed"] < 1:
        raise TestsFailed(f"post-deploy smoke on {env} executed nothing ({res.get('skipped') or summ.get('reason')}); "
                          f"live-cli is a required suite here (envs/{env} requiredSuites / liveTest)")
    return {"smoke": res, "summary": summ}


def _on_failure(ctx, run, env, manifest, exc: VraError, gate, ckpt, origin, p) -> dict:
    e = ctx.ledger.env(env)
    ctx.ledger.append_history(env, {"action": "deploy", "build": manifest["buildId"], "result": "FAILED",
                                    "runId": run.run_id, "error": exc.message[:500], "mode": gate["mode"],
                                    "slot": gate.get("slot"), "vamsVersion": manifest.get("vamsVersion"),
                                    "checkpoint": (ckpt or {}).get("id"),
                                    # the data is migrated even though the deploy failed (a rollback reads this)
                                    "migrationsApplied": list(run.state.get("migrationsApplied") or [])},
                              dry_run=ctx.dry_run)
    is_test = isinstance(exc, TestsFailed)
    if gate["mode"] == "ab":
        # live traffic never left the active slot; the new slot is left for inspection
        mark_clean(run, env)
        raise RolledBack(f"{exc.message}; A/B: DNS not switched, {gate.get('activeSlot') or 'previous'} slot stays live",
                         {"failedSlot": gate.get("slot")})
    if e.get("rollbackPolicy", "auto") != "auto":
        # the env runs the failed build: it stays locked by this run until a human decides
        if is_test:
            raise TestsFailed(f"{exc.message}; rollbackPolicy=ask: run `vra rollback --env {env}` "
                              f"(or `vra resume {run.run_id}` after fixing the cause)")
        raise NeedsHuman(f"{exc.message}; rollbackPolicy=ask: run `vra rollback --env {env}`", exc.detail)
    applied = list(run.state.get("migrationsApplied", []))
    try:
        res = perform_rollback(ctx, run, env, exclude_build=manifest["buildId"],
                               restore_checkpoint=(ckpt or {}).get("id") if applied else None, prefix=p + "rollback.",
                               skip_gate=True, unattended=True)
    except PermissionDenied as perm:
        raise RollbackFailed(f"{exc.message}; automatic rollback was denied: {perm.message}") from perm
    except VraError as rexc:
        raise RollbackFailed(f"{exc.message}; automatic rollback failed: {rexc.message}") from rexc
    raise RolledBack(f"{exc.message}; rolled back to {res.get('build') or 'CloudFormation-rolled-back state'}", res)


# ===================================================================== rollback
def migrations_since_good(ctx, env: str) -> list[str]:
    """Migrations recorded after the env's last good deploy: a deploy that migrated the data and then failed
    (rollbackPolicy ask leaves it running), or a standalone ``vra migrate``. The data is migrated even though the
    last good deploy is older."""
    hist = ctx.ledger.history(env)
    good = ctx.ledger.last_good_deploy(env)
    start = 0
    if good:
        idx = [i for i, h in enumerate(hist) if h is good or (h.get("runId"), h.get("build"), h.get("action")) ==
               (good.get("runId"), good.get("build"), good.get("action"))]
        start = idx[-1] + 1 if idx else 0
    out: list[str] = []
    for h in hist[start:]:
        for m in h.get("migrationsApplied") or h.get("applied") or []:
            if m not in out:
                out.append(m)
    return out


def rollback_downgrade_check(ctx, env: str, target_manifest: dict, leaving: str | None) -> dict:
    """A rollback to a build of an older VAMS minor must not run old code over data migrated past it: compare the
    target build's version with the deployed one and stop (exit 20) when a migration lies between them and no
    ``--restore-checkpoint`` was given. Migrations applied after the last good deploy (a failed deploy left on the
    env) count too. The transitions come from the deployed (newer) bundle, else the target's."""
    cur = ctx.ledger.current_deploy(env) or {}
    frm, to = cur.get("vamsVersion"), target_manifest.get("vamsVersion")
    if not to:
        return {"from": frm, "to": to, "downgrade": False, "note": "the target bundle names no VAMS version"}
    roots = [build_mod.build_dir(ctx, b) / "migrations" for b in (leaving, cur.get("build"),
                                                                 target_manifest.get("buildId")) if b]
    ts = next((migrate.discover(r) for r in roots if r.is_dir() and migrate.discover(r)), [])
    back: list[dict] = []
    if frm and util.version_tuple(util.minor(frm)) > util.version_tuple(util.minor(to)):
        back = migrate.crossed(ts, frm, to)
    since = migrations_since_good(ctx, env)
    by_id = {t["id"]: t for t in ts}
    for mid in since:
        t = by_id.get(mid) or {"id": mid, "from": None, "to": None}
        # a transition whose target minor is above the rollback target's: the old code cannot read its data
        if t["to"] is None or util.version_tuple(t["to"]) > util.version_tuple(util.minor(to)):
            if all(b["id"] != mid for b in back):
                back.append(t)
    if back:
        seen = [v for v in [frm] + [h.get("vamsVersion") or h.get("to") for h in ctx.ledger.history(env)
                                    if set(h.get("migrationsApplied") or h.get("applied") or []) & set(since)] if v]
        newest = max(seen, key=util.version_tuple) if seen else "the migrated data"
        raise migrate.downgrade_refusal(ctx, env, back, newest, to,
                                        "pass --restore-checkpoint to roll the data back with the code")
    down = bool(frm) and util.version_tuple(util.minor(frm)) > util.version_tuple(util.minor(to))
    return {"from": frm, "to": to, "downgrade": down, "crossed": [], "migrationsSinceGood": since}


def perform_rollback(ctx, run, env: str, to_build: str | None = None, exclude_build: str | None = None,
                     restore_checkpoint: str | None = None, prefix: str = "", skip_gate: bool = False,
                     approval: str | None = None, approval_fields: dict | None = None,
                     write_freeze: str | None = None, abort_executions: bool = False,
                     unattended: bool = False) -> dict:
    prev = None
    # the build rolled away from: its VamsCLI wheel runs the smoke when the target's bundle predates the wheel
    leaving = exclude_build or (ctx.ledger.current_deploy(env) or {}).get("build")
    if to_build:
        prev = {"build": to_build}
        seen = [h for h in ctx.ledger.history(env) if h.get("build") == to_build and h.get("manifestSha256")
                and h.get("action") in ("deploy", "rollback") and h.get("result") == "SUCCEEDED"]
        if seen:
            prev["manifestSha256"] = seen[-1]["manifestSha256"]
    else:
        last = ctx.ledger.last_good_deploy(env, exclude_build=exclude_build)
        if last:
            prev = last
    out: dict[str, Any] = {"env": env}
    if prev is None:
        # first install: nothing to go back to; CloudFormation already rolled back failed stack updates
        out["build"] = None
        out["note"] = "no previous successful deploy recorded"
    else:
        m = build_mod.load_manifest(ctx, prev["build"])
        # a bundle fetched from a bundle store must be the one this env deployed
        want = prev.get("manifestSha256")
        if want and _manifest_sha(ctx, prev["build"]) != want:
            raise VraError(f"bundle of {prev['build']} does not match the manifest {env} deployed "
                           f"(history manifestSha256 {want[:16]})")
        prev = {**prev, "manifestSha256": want or _manifest_sha(ctx, prev["build"])}
        target = build_mod.target_name(env, prev.get("slot")) if prev.get("slot") else env
        build_mod.verify_bundle(ctx, m, target)
        build_mod.verify_release_tag(ctx, m)
        if not restore_checkpoint:
            run.step(prefix + "downgrade-check", lambda sc: rollback_downgrade_check(ctx, env, m, leaving),
                     "migrated data and the rollback target")
        def _quota(sc):
            # advisory, never blocks a rollback: there is no diff, so the bundle's roles are the worst case (a
            # rollback across a boundary/prefix switch replaces all of them)
            try:
                res = iam_quota_check(ctx, env, template_iam_counts(assembly_dir(ctx, m, target)), blocking=False)
            except Exception as exc:  # noqa: BLE001
                return {"unverified": f"IAM quota not read: {exc}"[:300]}
            if res.get("warning"):
                run.state.setdefault("notes", []).append(
                    f"{env}: if this rollback replaces the stack's roles (boundary/prefix switch), the account's IAM "
                    f"quota may not hold them: {res['warning']}")
                run.save()
            return {**res, "worstCase": True}

        run.step(prefix + "iam-quota", _quota, "IAM quota (advisory)")
        cf_counts = template_cloudfront_counts(assembly_dir(ctx, m, target))

        def _cf_quota(sc):
            # advisory, never blocks a rollback: the redeploy creates the bundle's CloudFront policies only where
            # the stack lacks them (e.g. a rollback across an ALB -> CloudFront switch); the bundle is the worst case
            try:
                res = cloudfront_quota_check(ctx, env, cf_counts, blocking=False)
            except Exception as exc:  # noqa: BLE001
                return {"unverified": f"CloudFront quota not read: {exc}"[:300]}
            if res.get("warning"):
                run.state.setdefault("notes", []).append(
                    f"{env}: if this rollback creates the bundle's CloudFront policies or distribution again, the "
                    f"account's CloudFront quota may not hold them: {res['warning']}")
                run.save()
            return {**res, "worstCase": True}

        if any(v["added"] for v in cf_counts.values()):
            run.step(prefix + "cloudfront-quota", _cf_quota, "CloudFront quota (advisory)")
        if not ctx.dry_run:
            mark_dirty(run, env, f"rollback redeploy of {prev['build']}")
        run.step(prefix + "redeploy", lambda sc: cdk_deploy(ctx, env, m, target, sc), f"redeploy {prev['build']}")
        out["build"] = prev["build"]
        # `--to-build` gives only the build id; the version comes from its manifest (a rollback history line
        # without vamsVersion made the next deploy plan its migrations from "no version")
        prev = {**prev, "vamsVersion": prev.get("vamsVersion") or m.get("vamsVersion")}
    if restore_checkpoint:
        out["restore"] = restore.do_restore(ctx, run, env, restore_checkpoint, approval=approval,
                                            prefix=prefix, skip_gate=skip_gate, write_freeze=write_freeze,
                                            abort_executions=abort_executions, unattended=unattended)

    def _smoke_rb(sc):
        return _smoke(ctx, run, env, sc, None, [out.get("build"), leaving])
    try:
        run.step(prefix + "smoke", _smoke_rb, "smoke after rollback")
    except TestsFailed as exc:
        raise RollbackFailed(f"smoke failed after rollback of {env}: {exc.message}") from exc
    ctx.ledger.append_history(env, {"action": "rollback", "build": out.get("build"), "result": "SUCCEEDED",
                                    "runId": run.run_id, "restoredCheckpoint": restore_checkpoint,
                                    "vamsVersion": (prev or {}).get("vamsVersion"), "slot": (prev or {}).get("slot"),
                                    "manifestSha256": (prev or {}).get("manifestSha256"),
                                    "initiator": ctx.initiator, **(approval_fields or {})},
                              dry_run=ctx.dry_run or out.get("build") is None)
    mark_clean(run, env)
    return out


# ===================================================================== promote
def _conclusive_tests(hist: list[dict], since: str) -> list[dict]:
    """`vra test` records after ``since`` that count as evidence either way (INCOMPLETE partial runs do not)."""
    return [h for h in hist if h.get("action") == "test" and h.get("ts", "") >= since
            and h.get("result") in ("SUCCEEDED", "FAILED", "NO_TESTS")]


def check_promotion(ctx, frm: str, to: str, build_id: str) -> dict:
    from vra import gitops
    te = ctx.ledger.env(to)
    if te.get("promotesFrom") != frm:
        raise NeedsHuman(f"{to} promotes from {te.get('promotesFrom')!r}, not {frm!r}")
    m = build_mod.load_manifest(ctx, build_id)
    if m["lane"] != "release" or not m.get("promotable"):
        raise NeedsHuman(f"build {build_id} is a {m['lane']}-lane build; preview builds are never promotable")
    hist = [h for h in ctx.ledger.history(frm) if h.get("build") == build_id]
    deploys = [h for h in hist if h.get("action") == "deploy"]
    if not deploys or deploys[-1].get("result") != "SUCCEEDED":
        raise NeedsHuman(f"build {build_id} has no successful deploy on {frm}")
    tests_after = _conclusive_tests(hist, deploys[-1].get("ts", ""))
    if not tests_after or tests_after[-1].get("result") != "SUCCEEDED":
        raise NeedsHuman(f"build {build_id} did not pass tests on {frm} after its deploy (run `vra test --env {frm}`)")
    last_test = tests_after[-1]
    counts = last_test.get("suiteCounts")
    if counts is None:
        raise NeedsHuman(f"the passing test record of {build_id} on {frm} predates per-suite counts (vra < 0.2.0); "
                         f"re-run `vra test --env {frm}` so the evidence shows what executed")
    missing = [s for s in required_suites(ctx.ledger, frm) if (counts.get(s) or {}).get("passed", 0) < 1]
    if missing or not last_test.get("passed"):
        raise NeedsHuman(f"build {build_id} has no executed evidence on {frm} for required suite(s) "
                         f"{missing or ['any']}; run `vra test --env {frm}` with them configured")
    later_fail = [h for h in ctx.ledger.history(frm) if h.get("action") in ("deploy", "rollback")
                  and h.get("ts", "") > deploys[-1].get("ts", "") and h.get("build") == build_id
                  and h.get("result") != "SUCCEEDED"]
    if later_fail:
        raise NeedsHuman(f"build {build_id} later failed on {frm}")
    # Only an approval-gated target needs a reviewed merge commit on the trunk. An ungated env promoted from
    # another (dev -> int, both auto) takes the sync build `vra watch` just tested, before its PR is merged.
    if not ctx.dry_run and te.get("requiresApproval"):
        g = gitops.ensure_repo(ctx)
        g.run("fetch", "origin", "--prune")
        if not gitops.on_trunk(ctx, g, m["commit"]):
            raise NeedsHuman(f"build commit {m['commit'][:12]} is not on {ctx.ledger.project['trunk']}; merge the sync "
                             f"pull request with a merge commit first")
    if build_mod.target_name(to, None) not in m["targets"] and not any(t.startswith(f"{to}@") for t in m["targets"]):
        raise NeedsHuman(f"build {build_id} has no synth for {to}; rebuild including {to}")
    return {"from": frm, "to": to, "build": build_id, "testedAt": last_test.get("ts"), "testRun": last_test.get("runId"),
            "suiteCounts": counts}


@workflow("deploy")
def wf_deploy(ctx, run, args):
    return do_deploy(ctx, run, args["env"], args["build"], args.get("approval"), args.get("mode"),
                     bool(args.get("emergency")), args.get("origin", "cli"),
                     allow_replacements=bool(args.get("allowReplacements")),
                     accept_drift=bool(args.get("acceptDrift")),
                     abort_executions=bool(args.get("abortWorkflowExecutions")))


@workflow("promote")
def wf_promote(ctx, run, args):
    return do_deploy(ctx, run, args["to"], args["build"], args.get("approval"), args.get("mode"),
                     bool(args.get("emergency")), "promote", allow_replacements=bool(args.get("allowReplacements")),
                     action="promote", promote_from=args["from"], accept_drift=bool(args.get("acceptDrift")),
                     abort_executions=bool(args.get("abortWorkflowExecutions")))


@workflow("rollback")
def wf_rollback(ctx, run, args):
    env = args["env"]
    with env_lock(ctx, run, env):
        def _gate(sc):
            e = ctx.ledger.env(env)
            target = args.get("toBuild") or (ctx.ledger.last_good_deploy(env, exclude_build=(ctx.ledger.current_deploy(env)
                                                                                             or {}).get("build")) or {}).get("build")
            if not target and not args.get("restoreCheckpoint"):
                raise NeedsHuman(f"no previous good build recorded for {env}; pass --to-build")
            rec = approvals.check(ctx, env, target or f"restore:{args.get('restoreCheckpoint')}", "rollback",
                                  args.get("approval")) if e.get("requiresApproval") else None
            approvals.record(run, rec)
            return {"toBuild": target, "approvalFields": approvals.history_fields(rec)}

        from vra import freeze
        freeze.record_ack(run, ctx, bool(args.get("writesStopped")))
        g = run.step("rollback.gate", _gate, "rollback gate", revalidate=True)
        run.step("rollback.preflight", lambda sc: permissions.preflight(ctx, env, "rollback"), "preflight rollback")
        return perform_rollback(ctx, run, env, to_build=g["toBuild"], restore_checkpoint=args.get("restoreCheckpoint"),
                                prefix="rollback.", skip_gate=False, approval=args.get("restoreApproval"),
                                approval_fields=g.get("approvalFields"), write_freeze=args.get("writeFreeze"),
                                abort_executions=bool(args.get("abortWorkflowExecutions")))
