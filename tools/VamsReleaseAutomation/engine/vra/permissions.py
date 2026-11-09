# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Permission loop.

1. classify: turn CloudFormation event text, CDK output, CLI output and botocore errors into
   structured denials {principal, action, resource, reason, kind}.
2. IT request: write IT-REQUEST-<n>.md + .json with a consolidated least-privilege policy.
3. preflight: iam:SimulatePrincipalPolicy against action manifests (iam/actions/<workflow>.yaml),
   falling back to read-only probes when simulation itself is not permitted.
4. resume: re-simulate exactly the requested actions; still denied -> IT request n+1.
"""

from __future__ import annotations

import json
import os
import re
from vra import data as vra_data
from pathlib import Path
from typing import Any, Iterable

import jsonschema
import yaml

from vra import util
from vra.aws import client_error_parts, parse_arn, principal_arn_for_simulation, substitute
from vra.errors import PermissionDenied, UsageError

# ------------------------------------------------------------------ classifier
_ARN = r"arn:[a-z0-9-]+:[a-z0-9-]*:[a-z0-9-]*:[0-9]{0,12}:[^\s\"',;)]+"
_ACTION = r"[a-z0-9-]+:[A-Za-z0-9*]+"

NOT_AUTHORIZED_RE = re.compile(
    r"(?:User|Principal):\s*(?P<principal>" + _ARN + r")\s+is not authorized to perform:\s*(?P<action>" + _ACTION +
    r")(?:\s+on resource:\s*(?P<resource>(?:" + _ARN + r")|[^\s\"',;)]+))?"
    r"(?P<tail>[^\n\"]*)", re.IGNORECASE)
# Old CloudFormation / EC2 style: "API: ec2:CreateVpc You are not authorized to perform this operation."
API_NOT_AUTH_RE = re.compile(r"API:\s*(?P<action>" + _ACTION + r")\s+You are not authorized to perform this operation",
                             re.IGNORECASE)
ENCODED_RE = re.compile(r"Encoded authorization failure message:\s*(?P<encoded>[A-Za-z0-9_\-]{20,})")
BOTO_OP_RE = re.compile(r"An error occurred \((?P<code>[A-Za-z.]+)\) when calling the (?P<op>[A-Za-z0-9]+) operation"
                        r"(?: \(reached max retries: \d+\))?:\s*(?P<msg>.*)")
CDK_ASSUME_RE = re.compile(r"could not be used to assume '(?P<role>" + _ARN + r")'")
KMS_HINT_RE = re.compile(r"(?i)(ciphertext refers to a customer \w+ key that does not exist|"
                         r"KMS key is disabled|KMSAccessDenied|KMS\.AccessDeniedException|"
                         r"\bkms\b[^\n]{0,200}?(not authorized|access denied|AccessDenied|is not allowed))")
DENIED_CODES_RE = re.compile(r"^(AccessDenied(Exception)?|UnauthorizedOperation|Unauthorized(Access)?|"
                             r"[A-Za-z]*NotAuthorized[A-Za-z]*|AuthorizationError|Forbidden|"
                             r"KMSAccessDeniedException|AccessDeniedByVpcEndpoint|InsufficientPrivileges)$")
GENERIC_DENIED_RE = re.compile(r"(?i)\b(AccessDenied(Exception)?|UnauthorizedOperation|not authorized|"
                               r"Access Denied|NotAuthorized)\b")

_REASON_KINDS = [
    (re.compile(r"(?i)explicit deny in a service control policy"), "scp"),
    (re.compile(r"(?i)explicit deny in a resource control policy"), "rcp"),
    (re.compile(r"(?i)no permissions boundary allows|explicit deny in a permissions boundary"), "boundary"),
    (re.compile(r"(?i)no session policy allows|explicit deny in a session policy"), "session-policy"),
    (re.compile(r"(?i)resource-based policy"), "resource-policy"),
    (re.compile(r"(?i)VPC endpoint policy"), "vpce"),
    (re.compile(r"(?i)explicit deny in an identity-based policy"), "explicit-deny"),
    (re.compile(r"(?i)no identity-based policy allows"), "identity"),
]

# botocore service name -> IAM action prefix where they differ
SERVICE_PREFIX = {"cognito-idp": "cognito-idp", "opensearch": "es", "opensearchserverless": "aoss",
                  "s3control": "s3", "stepfunctions": "states", "elbv2": "elasticloadbalancing",
                  "elb": "elasticloadbalancing", "logs": "logs", "events": "events", "apigatewayv2": "apigateway",
                  "service-quotas": "servicequotas"}
# operations whose IAM action name differs from the API operation name
OPERATION_ACTION = {("s3", "ListObjectsV2"): "s3:ListBucket", ("s3", "ListObjectVersions"): "s3:ListBucketVersions",
                    ("s3", "HeadObject"): "s3:GetObject", ("s3", "HeadBucket"): "s3:ListBucket",
                    ("s3", "CopyObject"): "s3:PutObject", ("s3", "UploadPart"): "s3:PutObject",
                    ("s3", "CreateMultipartUpload"): "s3:PutObject", ("s3", "CompleteMultipartUpload"): "s3:PutObject",
                    ("s3", "DeleteObjects"): "s3:DeleteObject", ("s3", "GetBucketVersioning"): "s3:GetBucketVersioning",
                    ("s3control", "CreateJob"): "s3:CreateJob"}


def _kind_from(text: str) -> tuple[str, str | None]:
    for rx, kind in _REASON_KINDS:
        m = rx.search(text or "")
        if m:
            return kind, m.group(0)
    return "unknown", None


def _clean_resource(r: str | None) -> str | None:
    if not r:
        return None
    return r.rstrip(".").rstrip(",")


def classify_text(text: str, source: str = "text", principal_hint: str | None = None) -> list[dict]:
    """Extract denials from free text (CloudFormation StatusReason, cdk/CLI output, logs)."""
    text = text or ""
    out: list[dict] = []
    seen: set[tuple] = set()

    def add(d: dict) -> None:
        key = (d.get("principal"), d.get("action"), d.get("resource"), d.get("kind"))
        if key not in seen:
            seen.add(key)
            out.append(d)

    for m in NOT_AUTHORIZED_RE.finditer(text):
        tail = m.group("tail") or ""
        kind, reason = _kind_from(tail)
        add({"principal": m.group("principal"), "action": m.group("action"),
             "resource": _clean_resource(m.group("resource")), "kind": kind, "reason": reason, "source": source,
             "excerpt": util.redact(_excerpt(text, m.start(), m.end()))})
    for m in API_NOT_AUTH_RE.finditer(text):
        add({"principal": principal_hint, "action": m.group("action"), "resource": None, "kind": "unknown",
             "reason": "not authorized", "source": source, "excerpt": util.redact(_excerpt(text, m.start(), m.end()))})
    for m in CDK_ASSUME_RE.finditer(text):
        add({"principal": principal_hint, "action": "sts:AssumeRole", "resource": m.group("role"),
             "kind": "cdk-bootstrap-trust", "reason": "CDK could not assume the bootstrap role (trust policy or "
             "sts:AssumeRole grant missing)", "source": source,
             "excerpt": util.redact(_excerpt(text, m.start(), m.end()))})
    for m in ENCODED_RE.finditer(text):
        add({"principal": principal_hint, "action": None, "resource": None, "kind": "encoded",
             "reason": "encoded authorization failure", "encoded": m.group("encoded"), "source": source,
             "excerpt": util.redact(_excerpt(text, m.start(), m.end()))})
    if not out:
        for m in BOTO_OP_RE.finditer(text):
            if DENIED_CODES_RE.match(m.group("code")):
                kind, reason = _kind_from(m.group("msg"))
                add({"principal": principal_hint, "action": None, "operation": m.group("op"), "resource": None,
                     "kind": "kms" if KMS_HINT_RE.search(m.group("msg")) else kind, "reason": reason or m.group("code"),
                     "source": source, "excerpt": util.redact(_excerpt(text, m.start(), m.end()))})
    if KMS_HINT_RE.search(text) and not any(d["kind"] == "kms" or (d.get("action") or "").startswith("kms:")
                                            for d in out):
        m = KMS_HINT_RE.search(text)
        add({"principal": principal_hint, "action": None, "resource": None, "kind": "kms",
             "reason": "KMS key policy / grant denies the principal", "source": source,
             "excerpt": util.redact(_excerpt(text, m.start(), m.end()))})
    for d in out:
        if (d.get("action") or "").startswith("kms:") and d["kind"] in ("unknown", "identity"):
            d["kmsHint"] = True
    return out


def classify_exception(exc: BaseException, service: str | None = None, resource: str | None = None,
                       principal_hint: str | None = None) -> list[dict]:
    """Denials from a botocore ClientError (or any exception whose text carries a denial)."""
    code, msg, op = client_error_parts(exc)
    if not code:
        return classify_text(str(exc), "exception", principal_hint)
    if not DENIED_CODES_RE.match(code) and not GENERIC_DENIED_RE.search(msg or ""):
        return []
    found = classify_text(msg, "botocore", principal_hint)
    found = [d for d in found if d["kind"] != "encoded" or d.get("encoded")]
    if found:
        for d in found:
            d.setdefault("operation", op)
            if not d.get("action") and service and op:
                d["action"] = action_for(service, op)
            if not d.get("resource") and resource:
                d["resource"] = resource
        return found
    kind, reason = _kind_from(msg)
    if KMS_HINT_RE.search(msg) or code.startswith("KMS"):
        kind = "kms"
    return [{"principal": principal_hint, "action": action_for(service, op) if service and op else None,
             "operation": op, "resource": resource, "kind": kind, "reason": reason or code, "source": "botocore",
             "excerpt": util.redact(f"{code}: {msg}"[:500])}]


def is_denied(exc: BaseException) -> bool:
    code, msg, _ = client_error_parts(exc)
    if code:
        return bool(DENIED_CODES_RE.match(code))
    return bool(GENERIC_DENIED_RE.search(str(exc)))


def action_for(service: str, operation: str) -> str:
    if (service, operation) in OPERATION_ACTION:
        return OPERATION_ACTION[(service, operation)]
    prefix = SERVICE_PREFIX.get(service, service)
    return f"{prefix}:{operation}"


def _excerpt(text: str, start: int, end: int, pad: int = 160) -> str:
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    return text[a:b].strip()


def decode_encoded(sts_client, denials: list[dict]) -> list[dict]:
    """Expand ``encoded`` denials via sts:DecodeAuthorizationMessage when permitted."""
    out = []
    for d in denials:
        if d.get("kind") != "encoded" or not d.get("encoded") or sts_client is None:
            out.append(d)
            continue
        try:
            resp = sts_client.decode_authorization_message(EncodedMessage=d["encoded"])
            dec = json.loads(resp["DecodedMessage"])
            ctx = dec.get("context", {})
            out.append({"principal": (ctx.get("principal") or {}).get("arn") or d.get("principal"),
                        "action": ctx.get("action"), "resource": ctx.get("resource"),
                        "kind": "explicit-deny" if dec.get("explicitDeny") else "identity",
                        "reason": "decoded authorization message", "source": d.get("source"),
                        "excerpt": d.get("excerpt")})
        except Exception:  # noqa: BLE001 - decoding is best effort; keep the encoded record
            d = dict(d)
            d["reason"] = (d.get("reason") or "") + " (sts:DecodeAuthorizationMessage not permitted; ask IT to decode)"
            out.append(d)
    return out


# ------------------------------------------------------------------ manifests
def manifest_dirs() -> list[Path]:
    dirs: list[Path] = []
    if os.environ.get("VRA_IAM_ACTIONS_DIR"):
        dirs.append(Path(os.environ["VRA_IAM_ACTIONS_DIR"]))
    pkg_root = Path(__file__).resolve().parents[2]  # <package>/engine/vra -> <package>
    dirs.append(pkg_root / "iam" / "actions")
    dirs.append(vra_data.path("iam-actions"))
    return dirs


def manifest_schema() -> dict:
    return json.loads(vra_data.text("iam-actions.schema.json"))


# engine workflow -> manifest file names tried in order (matches iam/actions/ naming)
MANIFEST_ALIASES = {"test": ["test-live", "test"], "live-test": ["test-live"],
                    "promote": ["deploy"], "watch": ["deploy"]}


def load_manifest(workflow: str) -> dict:
    names = MANIFEST_ALIASES.get(workflow, [workflow])
    for d in manifest_dirs():
        for name in names:
            p = d / f"{name}.yaml"
            if not p.exists():
                continue
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            errs = list(jsonschema.Draft202012Validator(manifest_schema()).iter_errors(data))
            if errs:
                raise UsageError(f"invalid IAM action manifest {p}: {errs[0].message}")
            data["_path"] = str(p)
            return data
    raise UsageError(f"no IAM action manifest for workflow {workflow!r} in {[str(d) for d in manifest_dirs()]}")


def known_workflows() -> list[str]:
    names: set[str] = set()
    for d in manifest_dirs():
        if d.exists():
            names |= {p.stem for p in d.glob("*.yaml")}
    return sorted(names)


def placeholders(ctx, env: str, extra: dict | None = None) -> dict[str, str]:
    e = ctx.ledger.env(env)
    vals = {"Partition": e.get("partition") or ctx.aws.partition(env), "Region": e["region"], "Account": e["account"],
            "ConfigName": e.get("configName") or "vams",
            "CdkQualifier": ctx.ledger.project.get("cdk", {}).get("qualifier", "hnb659fds"),
            "StagingBucket": (e.get("dataCopy") or {}).get("stagingBucket") or "*", "SourceAccount": e["account"]}
    try:
        vals["CoreStack"] = ctx.ledger.core_stack_name(env)
        # B-050-29: a CloudFormation-generated Lambda name keeps at most 25 characters of the stack name
        from vra.iam import LAMBDA_GENERATED_STACK_CHARS
        vals["CoreStackLambda"] = vals["CoreStack"][:LAMBDA_GENERATED_STACK_CHARS]
        vals["SsmPrefix"] = ctx.ledger.ssm_prefix(env)
        vals["BaseStackName"] = ctx.ledger.stack_suffix(env)
    except Exception:  # noqa: BLE001
        vals.setdefault("CoreStack", "*")
        vals.setdefault("CoreStackLambda", "*")
        vals.setdefault("SsmPrefix", "/*")
    lt = e.get("liveTest") or {}
    vals["LiveTestSecret"] = (lt.get("auth") or {}).get("secret") or lt.get("credentialsSecret") or "*"
    vals["ExternalTableKmsKey"] = external_table_key(ctx, env) or "*"
    try:
        from vra import iam
        boot = iam.bootstrap_assume_arns(ctx, env)
    except Exception:  # noqa: BLE001 - a broken iam block is reported by `vra ledger validate`
        boot = None
    if boot is not None:
        vals["BootstrapRoleArns"] = boot   # type: ignore[assignment] - a list, read by expand_manifest only
    vals.update(extra or {})
    return vals


def external_table_key(ctx, env: str) -> str | None:
    """The overlay's ``app.useKmsCmkEncryption.optionalExternalCmkArn`` (tables encrypted with a key of another
    account), which the data phases need kms access to."""
    try:
        cfg = ctx.ledger.vams_config(env) or {}
    except Exception:  # noqa: BLE001
        return None
    kms = (cfg.get("app") or {}).get("useKmsCmkEncryption") or {}
    return kms.get("optionalExternalCmkArn") or None if kms.get("enabled", True) else None


def env_features(ctx, env: str) -> dict[str, str]:
    """Feature-gated manifest statements (``feature:``): "on" = simulated, "optional" = a gap warns, absent =
    not simulated. changeSetGate follows ``diffGate``; maintenanceMode follows ``writeFreeze.backend`` (auto
    detects support at run time, so its statement is an optional check)."""
    e = ctx.ledger.env(env)
    out: dict[str, str] = {}
    if e.get("diffGate") == "changeset":
        out["changeSetGate"] = "on"
    backend = (e.get("writeFreeze") or {}).get("backend") or "auto"
    if backend == "maintenance-mode":
        out["maintenanceMode"] = "on"
    elif backend == "auto":
        out["maintenanceMode"] = "optional"
    if external_table_key(ctx, env):
        out["externalTableKey"] = "on"
    if e.get("cognitoExport"):
        out["cognitoExport"] = "on"
    cf = cloudfront_feature(ctx, env)
    if cf:
        out["cloudFrontQuota"] = cf
    return out


def cloudfront_feature(ctx, env: str) -> str | None:
    """cloudFrontQuota (O-050-15): "on" when the env's overlay config.json deploys the web app on CloudFront
    (``app.useCloudFront.enabled``, which VAMS defaults to true when the block is absent); None for ALB envs, an
    explicit ``enabled: false`` and partitions without CloudFront support in VAMS (GovCloud, EU Sovereign: ALB
    only); "optional" when no overlay says (a gap warns; the deploy's quota check still needs the reads)."""
    from vra import aws
    e = ctx.ledger.env(env)
    part = e.get("partition") or aws.partition_for_region(e.get("region"))
    if part and part != "aws":
        return None
    try:
        cfg = ctx.ledger.vams_config(env)
    except Exception:  # noqa: BLE001 - a broken overlay is reported by `vra ledger validate`
        cfg = None
    if not cfg:
        return "optional"
    app = cfg.get("app") or {}
    if (app.get("useAlb") or {}).get("enabled"):
        return None
    cf = app.get("useCloudFront")
    return None if isinstance(cf, dict) and cf.get("enabled") is False else "on"


def condition_context(condition: dict | None, values: dict[str, str]) -> list[dict]:
    """SimulatePrincipalPolicy context entries for a manifest statement's ``condition:`` (B-050-8). The role
    templates carry the same conditions (kms:ViaService dynamodb.* / s3.*, kms:CallerAccount ...); simulated
    without the keys, such a statement is an implicit deny although the real call (made through the service)
    is allowed. A wildcard value is made concrete the way the service sends it: ``dynamodb.*`` ->
    ``dynamodb.<region>.<dns suffix>``."""
    from vra import aws
    if not condition:
        return []
    region = values.get("Region") or "us-east-1"
    suffix = aws.dns_suffix(values.get("Partition") or "aws")
    entries: dict[str, dict] = {}
    for op, keys in condition.items():
        if not isinstance(keys, dict):
            continue
        multi = op.startswith("ForAnyValue:") or op.startswith("ForAllValues:")
        for key, vals in keys.items():
            concrete = []
            for v in vals if isinstance(vals, list) else [vals]:
                v = substitute(str(v), values)
                if v.endswith(".*") and key.lower().endswith(":viaservice"):
                    v = f"{v[:-2]}.{region}.{suffix}"
                if "*" in v or "${" in v:
                    continue  # no concrete value to simulate with: leave the key out
                concrete.append(v)
            if not concrete:
                continue
            entries[key] = {"ContextKeyName": key, "ContextKeyValues": concrete[:1] if not multi else concrete,
                            "ContextKeyType": "stringList" if multi else "string"}
    return list(entries.values())


def expand_manifest(manifest: dict, values: dict[str, str], features: dict[str, str] | None = None,
                    inventory: dict | None = None, for_each: bool = True) -> list[dict]:
    """Statements with resolved placeholders. ``forEach`` statements (``${RegisteredBucket}`` /
    ``${RegisteredBucketKmsKey}``) expand to one statement per bucket / key of ``inventory`` (vra.buckets);
    without an inventory a bucket statement falls back to the stack-name pattern. ``for_each=False`` leaves
    them out (preflight simulates them through vra.buckets.check_access)."""
    from vra import buckets
    out = []
    for st in manifest["statements"]:
        feat = st.get("feature")
        if feat and features is not None and feat not in features:
            continue
        if st.get("forEach"):
            if not for_each:
                continue
            for x in buckets.expand_for_each(st, values, inventory):
                out.append({**x, "optional": bool(st.get("optional")),
                            "tools": str(st.get("sid") or "").startswith("Tools")})
            continue
        res = [substitute(r, values) for r in (st.get("resources") or ["*"])]
        boot = values.get("BootstrapRoleArns")
        if st.get("sid") == "CdkBootstrapRolesAssume" and isinstance(boot, list):
            # iam.mode bootstrap: the roles IT created (any name, R50-7); none with cliCredentialsSynthesizer
            if not boot:
                continue
            res = list(boot)
        ctx_entries = condition_context(st.get("condition"), values)
        out.append({"sid": st.get("sid"), "actions": list(st["actions"]), "resources": res,
                    **({"context": ctx_entries} if ctx_entries else {}),
                    "optional": bool(st.get("optional")) or bool(feat and (features or {}).get(feat) == "optional"),
                    # iam/actions convention: sid "Tools*" = orchestrator identity, not the env role
                    "tools": str(st.get("sid") or "").startswith("Tools")})
    return out


# ------------------------------------------------------------------ simulation
def simulation_principal(ctx, env: str, role: str = "deploy") -> str:
    arn = ctx.aws.role_arn(env, role) if hasattr(ctx.aws, "role_arn") else None
    if arn:
        return arn
    return principal_arn_for_simulation(ctx.aws.identity(env, role)["Arn"])


_THROTTLE_CODES = {"Throttling", "ThrottlingException", "RequestLimitExceeded", "TooManyRequestsException"}
_SIM_MAX_ATTEMPTS = 8


def _sleep(seconds: float) -> None:  # patched in tests
    import time
    time.sleep(seconds)


def _simulate_call(iam_client, kwargs: dict) -> dict:
    """simulate_principal_policy with backoff on throttling. `preflight --all` issues dozens of calls in a
    burst and IAM's account-level rate limit answers ``Throttling: Rate exceeded`` even after the SDK's
    own retries; a throttled preflight is not a failed preflight."""
    for attempt in range(_SIM_MAX_ATTEMPTS):
        try:
            return iam_client.simulate_principal_policy(**kwargs)
        except Exception as exc:  # noqa: BLE001
            code = client_error_parts(exc)[0]
            if code not in _THROTTLE_CODES or attempt == _SIM_MAX_ATTEMPTS - 1:
                raise
            _sleep(min(2 ** attempt, 30))
    raise AssertionError("unreachable")


def _resource_agnostic(r: dict) -> bool:
    """IAM answers with its own ARN template (``...:${ResourceType}/${Id}``) when the action has no
    resource-level permissions (it only accepts ``*``); a resource-scoped simulation of such an action is
    always an implicit deny, so it must be re-simulated against ``*``."""
    return "${" in str(r.get("EvalResourceName") or "") and r.get("EvalDecision") != "allowed"


def _different_authorization(exc: BaseException) -> bool:
    """IAM's InvalidInput for one SimulatePrincipalPolicy call whose actions "require different authorization
    information" (e.g. kms key actions together with kms:ListKeys)."""
    code = client_error_parts(exc)[0]
    return code == "InvalidInput" and "different authorization" in str(exc)


def _simulate_pages(iam_client, kwargs: dict) -> list[dict]:
    """All EvaluationResults of one call (paginated). A call IAM refuses because its actions need different
    authorization information is split into one call per action (E2E-050 B-050-12)."""
    kwargs = dict(kwargs)
    results: list[dict] = []
    try:
        while True:
            resp = _simulate_call(iam_client, kwargs)
            results += resp.get("EvaluationResults", [])
            if not resp.get("IsTruncated"):
                return results
            kwargs["Marker"] = resp.get("Marker")
    except Exception as exc:  # noqa: BLE001
        if len(kwargs["ActionNames"]) < 2 or not _different_authorization(exc):
            raise
    kwargs.pop("Marker", None)
    out: list[dict] = []
    for action in kwargs["ActionNames"]:
        out += _simulate_pages(iam_client, {**kwargs, "ActionNames": [action]})
    return out


def simulate(iam_client, principal: str, statements: list[dict]) -> dict:
    """SimulatePrincipalPolicy per statement. Returns {denied: [...], allowed: n, optional: [...]}.

    Wildcard actions (``dynamodb:*``) are simulated as given; IAM evaluates them against the policy.
    Actions without resource-level permissions are re-simulated against ``*`` (see _resource_agnostic).
    """
    denied: list[dict] = []
    optional: list[dict] = []
    allowed = 0
    for st in statements:
        resources_ = [r for r in st["resources"] if "${" not in r] or ["*"]
        kwargs: dict[str, Any] = {"PolicySourceArn": principal, "ActionNames": st["actions"]}
        if resources_ != ["*"]:
            kwargs["ResourceArns"] = resources_
        if st.get("context"):
            kwargs["ContextEntries"] = st["context"]
        results = _simulate_pages(iam_client, kwargs)
        agnostic = sorted({r.get("EvalActionName") for r in results if _resource_agnostic(r)}) \
            if "ResourceArns" in kwargs else []
        if agnostic:
            results = [r for r in results if r.get("EvalActionName") not in agnostic]
            results += _simulate_pages(iam_client, {"PolicySourceArn": principal, "ActionNames": agnostic})
        for r in results:
            if r.get("EvalDecision") == "allowed":
                allowed += 1
                continue
            kind = "explicit-deny" if r.get("EvalDecision") == "explicitDeny" else "identity"
            org = (r.get("OrganizationsDecisionDetail") or {}).get("AllowedByOrganizations")
            if org is False:
                kind = "scp"
            pb = (r.get("PermissionsBoundaryDecisionDetail") or {}).get("AllowedByPermissionsBoundary")
            if pb is False:
                kind = "boundary"
            rec = {"principal": principal, "action": r.get("EvalActionName"),
                   "resource": r.get("EvalResourceName") if r.get("EvalResourceName") not in (None, "*") else
                   ("*" if r.get("EvalActionName") in agnostic else
                    (resources_[0] if resources_ != ["*"] else "*")),
                   "kind": kind, "reason": f"simulated: {r.get('EvalDecision')}", "source": "simulation",
                   "sid": st.get("sid")}
            for k in ("bucket", "kmsKey", "buckets"):
                if st.get(k):
                    rec[k] = st[k]
            (optional if st.get("optional") else denied).append(rec)
    return {"denied": denied, "optional": optional, "allowed": allowed}


def preflight(ctx, env: str, workflow: str, extra_values: dict | None = None, role: str | None = None) -> dict:
    """Simulate a workflow's manifest for an env. Raises PermissionDenied on gaps."""
    manifest = load_manifest(workflow)
    role = role or manifest.get("role", "deploy")
    if role in ("readonly", "backup"):
        role = "deploy" if not ctx.ledger.env(env).get("readOnlyRoleArn") else "readonly"
    values = placeholders(ctx, env, extra_values)
    per_bucket = any(s.get("forEach") for s in manifest["statements"])
    if ctx.dry_run:
        expanded = expand_manifest(manifest, values, env_features(ctx, env))
        statements = [s for s in expanded if not s["tools"]]
        return {"env": env, "workflow": workflow, "dryRun": True, "manifest": manifest["_path"],
                "actions": sum(len(s["actions"]) for s in statements),
                "toolsStatements": [s["sid"] for s in expanded if s["tools"]],
                **({"registeredBuckets": "resolved at run time (dry run uses the stack-name pattern)"}
                   if per_bucket else {})}
    expanded = expand_manifest(manifest, values, env_features(ctx, env), for_each=False)
    statements = [s for s in expanded if not s["tools"]]
    tools_sids = [s["sid"] for s in expanded if s["tools"]]
    principal = simulation_principal(ctx, env, role)
    iam = ctx.aws.client(env, "iam", role)
    try:
        res = simulate(iam, principal, statements)
        method = "simulate"
    except Exception as exc:  # noqa: BLE001
        if not is_denied(exc):
            raise
        res = probe_fallback(ctx, env, role, statements, exc)
        method = "probe"
    bucket_info = None
    if per_bucket and method == "simulate":
        bucket_info, more = _bucket_preflight(ctx, env, workflow, role, values)
        res["denied"] += more
    out = {"env": env, "workflow": workflow, "principal": principal, "method": method,
           "allowed": res["allowed"], "denied": res["denied"], "optionalGaps": res["optional"],
           "unverified": res.get("unverified", []), "manifest": manifest["_path"],
           "toolsStatements": tools_sids,
           "note": "Tools* statements apply to the orchestrator identity and are proven by assuming the env role"
           if tools_sids else None}
    if bucket_info is not None:
        out["registeredBuckets"] = bucket_info
    if res["denied"]:
        msg = f"preflight {workflow} on {env}: {len(res['denied'])} action(s) not allowed for {principal}"
        named = sorted({d.get("bucket") or ", ".join(d.get("buckets") or []) for d in res["denied"]} - {""})
        if named:
            msg += f"; registered bucket(s) the role cannot be shown to use: {', '.join(named)}"
        new = sorted({d["bucket"] for d in res["denied"] if d.get("newlyRegistered") and d.get("bucket")})
        if new:
            msg += f"; newly registered since the last check: {', '.join(new)}"
        raise PermissionDenied(msg, res["denied"], principal=principal, env=env, role=role)
    return out


def _bucket_preflight(ctx, env: str, workflow: str, role: str, values: dict) -> tuple[dict, list[dict]]:
    """forEach statements against the env's real buckets (SSM + registered asset buckets, each with its
    default-encryption KMS key). Not deployed yet: the stack-name pattern is simulated instead."""
    from vra import buckets
    from vra.errors import NeedsHuman
    try:
        res = buckets.check_access(ctx, env, [workflow], role)
    except NeedsHuman as exc:
        manifest = load_manifest(workflow)
        st = [s for s in expand_manifest(manifest, values, env_features(ctx, env)) if s.get("sid") and not s["tools"]
              and any(s["sid"] == b.get("sid") for b in buckets.bucket_statements(manifest))]
        sim = simulate(ctx.aws.client(env, "iam", role), simulation_principal(ctx, env, role), st)
        return {"resolved": False, "reason": exc.message, "pattern": f"{values.get('CoreStack')}*"}, sim["denied"]
    except PermissionDenied as exc:  # cannot even list the buckets (SSM / s3AssetBucketsStorage read denied)
        return {"resolved": False, "reason": exc.message}, list(exc.denials)
    return {"resolved": True, "buckets": res["buckets"]}, res["denials"]


_PROBES = {
    "sts:GetCallerIdentity": ("sts", "get_caller_identity", {}),
    "cloudformation:DescribeStacks": ("cloudformation", "describe_stacks", {}),
    "cloudformation:ListStacks": ("cloudformation", "list_stacks", {}),
    "dynamodb:ListTables": ("dynamodb", "list_tables", {"Limit": 1}),
    "dynamodb:ListBackups": ("dynamodb", "list_backups", {"Limit": 1}),
    "s3:ListAllMyBuckets": ("s3", "list_buckets", {}),
    "ssm:GetParametersByPath": ("ssm", "get_parameters_by_path", {"Path": "${SsmPrefix}", "MaxResults": 1}),
    "lambda:ListFunctions": ("lambda", "list_functions", {"MaxItems": 1}),
}


def probe_fallback(ctx, env: str, role: str, statements: list[dict], sim_exc: BaseException) -> dict:
    """iam:SimulatePrincipalPolicy itself denied: run safe read-only probes; the rest is 'unverified'."""
    vals = placeholders(ctx, env)
    denied, unverified, allowed = [], [], 0
    for st in statements:
        for action in st["actions"]:
            probe = _PROBES.get(action)
            if not probe:
                unverified.append(action)
                continue
            svc, op, params = probe
            params = {k: substitute(v, vals) if isinstance(v, str) else v for k, v in params.items()}
            try:
                getattr(ctx.aws.client(env, svc, role), op)(**params)
                allowed += 1
            except Exception as exc:  # noqa: BLE001
                if is_denied(exc):
                    denied.append({"principal": None, "action": action, "resource": "*", "kind": "identity",
                                   "reason": "probe denied", "source": "probe", "sid": st.get("sid")})
                else:
                    allowed += 1  # reachable; non-permission error
    return {"denied": denied, "optional": [], "allowed": allowed, "unverified": sorted(set(unverified)),
            "simulationError": str(sim_exc)[:300]}


# ------------------------------------------------------------------ policy generation
def consolidated_policy(denials: Iterable[dict]) -> dict:
    """Least-privilege policy: group identity-fixable denials by resource set; no wildcards added."""
    by_resource: dict[str, set[str]] = {}
    for d in denials:
        if not d.get("action") or d.get("kind") in ("scp", "rcp", "encoded", "vpce", "cdk-bootstrap-trust-only"):
            continue
        by_resource.setdefault(d.get("resource") or "*", set()).add(d["action"])
    # merge resources that need the same action set
    by_actions: dict[tuple, list[str]] = {}
    for res, actions in by_resource.items():
        by_actions.setdefault(tuple(sorted(actions)), []).append(res)
    statements = []
    for i, (actions, res_list) in enumerate(sorted(by_actions.items())):
        svc = actions[0].split(":")[0].replace("-", "")
        statements.append({"Sid": f"Vra{svc.capitalize()}{i + 1}", "Effect": "Allow", "Action": list(actions),
                           "Resource": sorted(res_list) if len(res_list) > 1 else res_list[0]})
    return {"Version": "2012-10-17", "Statement": statements}


def _guidance(kind: str) -> str:
    return {
        "identity": "Attach the policy below to the principal (or its permission set).",
        "explicit-deny": "An identity-based policy explicitly denies this; remove/scope the Deny statement.",
        "scp": "Denied by an AWS Organizations service control policy. The org/cloud admin must grant an SCP "
               "exception for this principal; an IAM policy change alone will not fix it.",
        "rcp": "Denied by a resource control policy (Organizations). The org admin must adjust the RCP.",
        "boundary": "Denied by the principal's permissions boundary. Extend the boundary to include the actions.",
        "session-policy": "Denied by the session policy passed at AssumeRole; widen the session policy.",
        "resource-policy": "Denied by the resource's own policy (bucket/key/queue policy). Update that policy.",
        "vpce": "Denied by a VPC endpoint policy; update the endpoint policy.",
        "kms": "KMS key policy or grant does not allow the principal. Add the principal to the key policy or "
               "create a grant (kms:Decrypt/GenerateDataKey/DescribeKey as needed).",
        "cdk-bootstrap-trust": "The CDK bootstrap role does not trust this principal. Re-bootstrap with "
                               "`cdk bootstrap --trust <account>` or add sts:AssumeRole + trust.",
        "encoded": "Encoded failure message; decode with `aws sts decode-authorization-message` (IT).",
        "registered-bucket": "An asset bucket registered in VAMS (or its KMS key) that the ledger does not record as "
                             "granted to this role. Grant the actions below on exactly these bucket / key ARNs: in "
                             "the role's identity policy (iam/cloudformation `AdditionalBucketArns` / "
                             "`AdditionalKmsKeyArns`), and, when the bucket or key is in another account or has its "
                             "own policy, in the bucket policy / key policy too. Then record it in "
                             "envs/<env>/env.yaml `registeredBuckets` (granted: true, bucketArns, kmsKeyArns) and "
                             "run `vra resume`.",
        "unknown": "Grant the action below; resource could not be determined from the error.",
    }.get(kind, "Grant the action below.")


# ------------------------------------------------------------------ IT requests
def write_it_request(ctx, run, step_id: str, exc: PermissionDenied) -> dict:
    run.it_dir.mkdir(parents=True, exist_ok=True)
    n = len(run.state.get("itRequests", [])) + 1
    env = exc.env or run.state.get("env")
    denials = exc.denials or []
    principals = sorted({d.get("principal") or exc.principal or "<unknown principal>" for d in denials}) or \
        [exc.principal or "<unknown principal>"]
    e = ctx.ledger.env(env) if env and env in ctx.ledger.env_names() else {}
    policy = consolidated_policy(denials)
    kinds = sorted({d.get("kind", "unknown") for d in denials})
    partition = e.get("partition")
    if not partition:
        for p in principals:
            try:
                partition = parse_arn(p)["partition"]
                break
            except ValueError:
                continue
    req = {"id": f"IT-REQUEST-{n}", "n": n, "runId": run.run_id, "workflow": run.state["workflow"], "step": step_id,
           "env": env, "account": e.get("account"), "region": e.get("region"), "partition": partition,
           "principals": principals, "role": exc.role, "createdAt": util.now_iso(), "status": "open",
           "kinds": kinds, "denials": denials, "actions": sorted({d["action"] for d in denials if d.get("action")}),
           "resources": sorted({d.get("resource") or "*" for d in denials if d.get("action")}),
           "policy": policy, "resume": f"vra resume {run.run_id}"}
    per_bucket = bucket_summary(denials)
    if per_bucket:
        req["registeredBuckets"] = per_bucket
    base = run.it_dir / f"IT-REQUEST-{n}"
    util.write_json(base.with_suffix(".json"), req)
    util.atomic_write(base.with_suffix(".md"), render_it_markdown(req))
    run.state.setdefault("itRequests", []).append({"id": req["id"], "n": n, "step": step_id, "env": env,
                                                   "role": exc.role, "actions": req["actions"],
                                                   "status": "open", "path": str(base.with_suffix(".md"))})
    run.save()
    return req


def bucket_summary(denials: list[dict]) -> list[dict]:
    """Per bucket / KMS key: the exact actions needed (IT request section 'Registered asset buckets')."""
    rows: dict[tuple, dict] = {}
    for d in denials:
        if d.get("bucket"):
            key = ("bucket", d["bucket"])
            row = rows.setdefault(key, {"bucket": d["bucket"], "kmsKey": None, "actions": set(), "resources": set(),
                                        "newlyRegistered": False, "workflows": set()})
        elif d.get("kmsKey"):
            key = ("key", d["kmsKey"])
            row = rows.setdefault(key, {"bucket": ", ".join(d.get("buckets") or []), "kmsKey": d["kmsKey"],
                                        "actions": set(), "resources": set(), "newlyRegistered": False,
                                        "workflows": set()})
        else:
            continue
        if d.get("action"):
            row["actions"].add(d["action"])
        if d.get("resource"):
            row["resources"].add(d["resource"])
        row["newlyRegistered"] = row["newlyRegistered"] or bool(d.get("newlyRegistered"))
        if d.get("workflow"):
            row["workflows"].add(d["workflow"])
    return [{**r, "actions": sorted(r["actions"]), "resources": sorted(r["resources"]),
             "workflows": sorted(r["workflows"])} for _, r in sorted(rows.items())]


def render_it_markdown(req: dict) -> str:
    lines = [f"# {req['id']}: permissions needed for `{req['workflow']}` ({req['env'] or 'n/a'})", "",
             f"- Run: `{req['runId']}` step `{req['step']}`",
             f"- Account / region / partition: `{req.get('account')}` / `{req.get('region')}` / `{req.get('partition')}`",
             f"- Principal(s): " + ", ".join(f"`{p}`" for p in req["principals"]),
             f"- Created: {req['createdAt']}", "",
             "## What is blocked", "",
             "The release automation stopped before changing anything further. It resumes automatically from this "
             "step once the access below is in place.", "",
             "| Action | Resource | Denied by | Reason |", "|---|---|---|---|"]
    for d in req["denials"]:
        lines.append(f"| `{d.get('action') or d.get('operation') or '?'}` | `{d.get('resource') or '*'}` | "
                     f"{d.get('kind')} | {(d.get('reason') or '').replace('|', '/')} |")
    if req.get("registeredBuckets"):
        lines += ["", "## Registered asset buckets", "",
                  "VAMS asset buckets can be registered at any time (also customer-named buckets and buckets in "
                  "other accounts). Exact actions per bucket and KMS key:", "",
                  "| Bucket | KMS key | Newly registered | Workflows | Actions | Resources |", "|---|---|---|---|---|---|"]
        for r in req["registeredBuckets"]:
            lines.append(f"| `{r['bucket']}` | {('`' + r['kmsKey'] + '`') if r.get('kmsKey') else '-'} | "
                         f"{'yes' if r['newlyRegistered'] else 'no'} | {', '.join(r['workflows']) or '-'} | "
                         f"{', '.join('`' + a + '`' for a in r['actions'])} | "
                         f"{', '.join('`' + x + '`' for x in r['resources'])} |")
        if any(r["newlyRegistered"] for r in req["registeredBuckets"]):
            lines += ["", "Buckets marked 'yes' were registered in VAMS after the last check of this env."]
    lines += ["", "## What to change", ""]
    for k in req["kinds"]:
        lines.append(f"- **{k}**: {_guidance(k)}")
    if req["policy"]["Statement"]:
        lines += ["", "## Consolidated least-privilege policy", "",
                  "Grant exactly these actions on these resources (no wildcards were added):", "", "```json",
                  json.dumps(req["policy"], indent=2), "```"]
    excerpts = [d.get("excerpt") for d in req["denials"] if d.get("excerpt")]
    if excerpts:
        lines += ["", "## Error excerpts (redacted)", ""]
        for ex in excerpts[:10]:
            lines += ["```", ex[:800], "```"]
    lines += ["", "## After the change", "", f"Run `{req['resume']}`. The engine re-simulates exactly these actions; "
              "if any are still denied it writes the next IT request and stops again.", ""]
    return "\n".join(lines)


def resimulate(ctx, run, req_entry: dict) -> list[dict] | None:
    """Re-simulate exactly the actions of an open IT request. ``None`` = could not simulate (retry the step)."""
    path = run.it_dir / f"{req_entry['id']}.json"
    if not path.exists():
        return None
    req = util.read_json(path)
    env = req.get("env")
    if not env or ctx.dry_run:
        return None
    sim_denials = [d for d in req["denials"] if d.get("action")]
    if not sim_denials or any(d.get("kind") in ("kms", "resource-policy", "vpce", "encoded", "registered-bucket")
                              for d in req["denials"]):
        return None  # simulation cannot prove these; retry the real step instead
    statements: dict[str, list[str]] = {}
    for d in sim_denials:
        statements.setdefault(d.get("resource") or "*", []).append(d["action"])
    principal = req["principals"][0] if req["principals"] and not req["principals"][0].startswith("<") else \
        simulation_principal(ctx, env, req.get("role") or "deploy")
    principal = principal_arn_for_simulation(principal) if ":sts:" in principal else principal
    parts = principal.split(":")
    env_account = str(ctx.ledger.env(env).get("account") or "")
    if len(parts) > 4 and env_account and parts[4] and parts[4] != env_account:
        # the denied principal lives in another account (e.g. a child fell back to the host's role): the env's IAM
        # cannot simulate it; retry the real step instead of crashing the resume
        return None
    try:
        iam = ctx.aws.client(env, "iam", req.get("role") or "deploy")
        res = simulate(iam, principal, [{"sid": None, "actions": sorted(set(a)), "resources": [r], "optional": False}
                                        for r, a in statements.items()])
    except Exception as exc:  # noqa: BLE001
        from vra.aws import client_error_parts
        if is_denied(exc) or client_error_parts(exc)[0] in ("InvalidInput", "NoSuchEntity"):
            return None
        raise
    return res["denied"]
