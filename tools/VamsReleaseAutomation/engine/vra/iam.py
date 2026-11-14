# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra iam plan|apply|verify`: the IT role package for environments where IT creates or constrains IAM roles.

VAMS (``infra/``) has three IAM mechanisms; this module drives them and never reinvents them:

- ``boundary``: ``infra/cdk.json`` context ``environments.aws.{PermissionBoundaryArn, IamRoleNamePrefix}``, read by
  ``infra/lib/core-stack.ts`` into the ``IamRoleTransform`` aspect (``infra/lib/aspects/iam-role-transform.aspect.ts``)
  for every ``AWS::IAM::Role`` of the core stack and its nested stacks.
- ``precreated``: ``app.iamRoleConfig.useCustomVamsStackRoles`` (``infra/config/config.ts``) +
  ``infra/config/policy/iamRoleConfig.json`` ``vamsStacks``, applied by ``applyVamsStackRoleCustomization``
  (``infra/lib/helper/iamRoleCustomization.ts``) as ``iam.Role.customizeRoles`` on the whole app.
  ``generateReportOnly: true`` writes ``cdk.out/iam-policy-report.json`` (construct path, trust, managed policies,
  identity statements of every role); ``false`` plus ``precreatedRoles`` (construct path -> role NAME) replaces
  each role, and synth fails on a role that is not mapped.
- ``bootstrap``: ``app.iamRoleConfig.useCustomBootstrapRoles`` + the ``bootstrap`` section: pre-created CDK bootstrap
  role ARNs (``DefaultStackSynthesizer``) or ``useCliCredentialsSynthesizer`` (no bootstrap roles; deploy under the
  caller's credentials).

The package (``<home>/state/iam/<env>/<n>/``) lists every role IT must create or allow, with one policy file per
role, a CloudFormation template IT may deploy (``it-roles.yaml``), a CSV for ticket systems and the delta against
package ``n-1``. ``apply`` validates what IT returns and writes the ledger overlay; ``verify`` checks the live roles
read-only. ``gate`` / ``check_assembly`` stop preflight and build with exit 10 (BLOCKED_ON_IT) while the env's
package is missing, unapplied or stale; ``vra resume`` continues after ``vra iam apply``.
"""

from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from vra import util
from vra.errors import BlockedOnIT, ConfigError, NeedsHuman, UsageError, VraError
from vra.runner import workflow

MODES = ("standard", "boundary", "precreated", "bootstrap")
# the fork fix for GitHub issue #431 (the aspect drops environments.aws.PermissionBoundaryArn in VAMS 2.6.6 and
# earlier; VAMS 2.6.7 fixed it upstream, where the patch's Present-when marker reports it present), shipped in
# ledger-template/core-patches/ and applied by vra.core_patches when core-patches.md lists it
BOUNDARY_PATCH = "core-patches/iam-role-transform-boundary.patch"
# GitHub issue #1210: a role-name prefix switch replaces every role, but the REST API Deployment keeps its logical
# id (VAMS hashes only the OpenAPI spec, which names the invoke role through an unresolved GetAtt), so the stage keeps
# serving a deployment whose authorizer credentials name a deleted role. Needed with BOUNDARY_PATCH when a rolePrefix
# is introduced on an existing environment.
API_DEPLOYMENT_PATCH = "core-patches/api-deployment-role-name.patch"
BOOTSTRAP_ROLES = {  # bootstrap role kind -> iamRoleConfig.json bootstrap key (infra/lib/helper/iamRoleCustomization.ts)
    "deploy": "deployRoleArn",
    "cfn-exec": "cloudFormationExecutionRoleArn",
    "lookup": "lookupRoleArn",
    "file-publishing": "fileAssetPublishingRoleArn",
    "image-publishing": "imageAssetPublishingRoleArn",
}
BOOTSTRAP_KEYS = ("qualifier", *BOOTSTRAP_ROLES.values(), "fileAssetsBucketName", "imageAssetsRepositoryName")
IAM_ROLE_CONFIG = "infra/config/policy/iamRoleConfig.json"
CONFIG_JSON = "infra/config/config.json"
MERGE_SUFFIX = ".vra-merge.json"          # overlay files deep-merged into the checkout's JSON (vra.build)
CDK_JSON_MERGE = "infra/cdk.json" + MERGE_SUFFIX
REPORT = "iam-policy-report.json"
DEFAULT_QUALIFIER = "hnb659fds"
ROLE_NAME_RE = re.compile(r"^[\w+=,.@-]{1,64}$")
QUALIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,10}$")
INLINE_LIMIT = 10240                      # IAM: aggregate inline policy size per role (characters, no whitespace)
MANAGED_LIMIT = 6144                      # IAM: one managed policy document
PENDING = "PENDING-FIRST-DEPLOY"

# ---------------------------------------------------------------- what causes a role (infra/lib/core-stack.ts)
FEATURES = {
    "VPCBuilder": ("Global VPC (flow logs)", "app.useGlobalVpc.enabled"),
    "LambdaLayers": ("Lambda layers", None),
    "StorageResourcesBuilder": ("Storage: asset buckets, tables, indexing queues", None),
    "ResourceNamesBuilder": ("Resource-name parameters", None),
    "CloudTrail-VAMS": ("Stack CloudTrail to CloudWatch Logs", "app.addStackCloudTrailLogs"),
    "AuthBuilder": ("Authentication (Cognito / external IdP)", "app.authProvider"),
    "ApiBuilder": ("API handlers (assets, databases, metadata, users)", None),
    "ApiBuilder2": ("API handlers (workflows, pipelines, tags, auth constraints)", None),
    "SearchBuilder": ("Search (OpenSearch)", "app.openSearch"),
    "PipelineBuilder": ("Processing pipelines", "app.pipelines"),
    "AddonBuilder": ("Add-ons", "app.addons"),
    "RestApi": ("REST API (API Gateway, authorizer)", None),
    "StaticWeb": ("Web app hosting", "app.useCloudFront.enabled / app.useAlb.enabled"),
    "LocationService": ("Location Service maps", "app.useLocationService.enabled"),
    "CustomFeatureEnabledConfig": ("Feature switches", None),
}
CDK_INTERNAL = (  # construct id markers of roles aws-cdk-lib itself creates
    ("AWS679f53fac002430cb0da5b7982bd2287", "CDK AwsCustomResource handler"),
    ("BucketNotificationsHandler", "CDK S3 bucket-notifications handler"),
    ("Custom::CDKBucketDeployment", "CDK BucketDeployment handler"),
    ("Custom::S3AutoDeleteObjectsCustomResourceProvider", "CDK auto-delete-objects handler"),
    ("framework-onEvent", "CDK custom-resource provider framework"),
)
PRINCIPAL_KIND = {
    "lambda": "Lambda execution role", "states": "Step Functions state machine role",
    "apigateway": "API Gateway role", "cognito-idp": "Cognito user pool SMS role",
    "cloudtrail": "CloudTrail to CloudWatch Logs role", "vpc-flow-logs": "VPC flow-log role",
    "cognito-identity": "Cognito identity pool role", "cognito-identity-us-gov": "Cognito identity pool role",
}
# resource type -> ARN built from the synthesized resource (literal name when the template has one, else the
# name prefix CloudFormation generates: the core stack name, lower case for buckets)
NAME_PROPS = {"AWS::DynamoDB::Table": "TableName", "AWS::S3::Bucket": "BucketName",
              "AWS::Logs::LogGroup": "LogGroupName", "AWS::Lambda::Function": "FunctionName",
              "AWS::SQS::Queue": "QueueName", "AWS::SNS::Topic": "TopicName", "AWS::Events::EventBus": "Name",
              "AWS::OpenSearchServerless::Collection": "Name", "AWS::Cognito::UserPool": "UserPoolName",
              "AWS::StepFunctions::StateMachine": "StateMachineName"}


# ================================================================ settings (env.yaml `iam`)
def parse_modes(value: Any) -> list[str]:
    """``standard`` | ``boundary`` | ``precreated`` | ``bootstrap``, combined with ``+`` / ``,`` or as a list;
    ``all`` = the three IT modes. Returned in canonical order."""
    if value in (None, "", []):
        return ["standard"]
    items = value if isinstance(value, list) else re.split(r"[+,\s]+", str(value))
    got: set[str] = set()
    for x in items:
        x = str(x).strip().lower()
        if not x:
            continue
        if x == "all":
            got |= {"boundary", "precreated", "bootstrap"}
        elif x in MODES:
            got.add(x)
        else:
            raise ConfigError(f"iam.mode: {x!r} is not one of {list(MODES)} (or all)")
    if "standard" in got and len(got) > 1:
        raise ConfigError("iam.mode: standard cannot be combined with another mode")
    return [m for m in MODES if m in got] or ["standard"]


def settings(e: dict) -> dict:
    raw = e.get("iam") or {}
    bs = dict(raw.get("bootstrap") or {})
    return {"modes": parse_modes(raw.get("mode")), "boundaryArn": raw.get("boundaryArn") or None,
            "rolePrefix": raw.get("rolePrefix") or "",
            "cliCredentialsSynthesizer": bool(raw.get("cliCredentialsSynthesizer")),
            "bootstrap": {k: bs.get(k) for k in BOOTSTRAP_KEYS if bs.get(k)}}


# The scoped CDK execution policy (iam/cloudformation/vra-cdk-execution-policy.yaml) lets CloudFormation pass and
# change IT's precreated roles only by the env's role-name prefix: without one there is no start of name that is
# VAMS's alone, so precreated mode requires iam.rolePrefix.
PRECREATED_PREFIX_REQUIRED = ("iam.mode precreated needs iam.rolePrefix: IT's roles must start with a prefix that is "
                              "VAMS's alone (for example ITVAMS-), which the scoped CDK execution policy grants as "
                              "RoleNamePrefix")


def needs_package(s: dict) -> bool:
    return s["modes"] != ["standard"]


def validate_settings(e: dict, where: str) -> tuple[list[str], list[str]]:
    """Semantic checks of env.yaml ``iam`` for `vra ledger validate`."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        s = settings(e)
    except ConfigError as exc:
        return [f"{where}: {exc.message}"], []
    part = e.get("partition")
    if s["boundaryArn"]:
        errors += [f"{where}: iam.boundaryArn {p}" for p in arn_problems(s["boundaryArn"], "policy", part,
                                                                           e.get("account"))]
    for k, v in s["bootstrap"].items():
        if k.endswith("RoleArn"):
            errors += [f"{where}: iam.bootstrap.{k} {p}" for p in arn_problems(v, "role", part, e.get("account"))]
    if s["bootstrap"].get("qualifier") and not QUALIFIER_RE.match(s["bootstrap"]["qualifier"]):
        errors.append(f"{where}: iam.bootstrap.qualifier must be 1-10 letters, digits, - or _")
    if s["rolePrefix"] and not re.match(r"^[\w+=,.@-]{1,32}$", s["rolePrefix"]):
        errors.append(f"{where}: iam.rolePrefix must be 1-32 IAM role-name characters")
    if "precreated" in s["modes"] and not s["rolePrefix"]:
        errors.append(f"{where}: {PRECREATED_PREFIX_REQUIRED}")
    if s["modes"] == ["standard"] and (s["boundaryArn"] or s["rolePrefix"] or s["bootstrap"]):
        warnings.append(f"{where}: iam settings are ignored with iam.mode standard")
    if "boundary" in s["modes"] and not s["boundaryArn"]:
        warnings.append(f"{where}: iam.mode boundary without iam.boundaryArn: the build takes the ARN from IT's "
                        f"applied response and stops (exit 20) until one is applied; set it in env.yaml so reviewers "
                        f"see it")
    if s["cliCredentialsSynthesizer"] and "bootstrap" not in s["modes"]:
        warnings.append(f"{where}: iam.cliCredentialsSynthesizer only applies with iam.mode bootstrap")
    if "boundary" in s["modes"] and e.get("permissionsBoundaryName") and s["boundaryArn"] and \
            not s["boundaryArn"].endswith(f":policy/{e['permissionsBoundaryName']}"):
        warnings.append(f"{where}: iam.boundaryArn and permissionsBoundaryName name different boundaries")
    return errors, warnings


def arn_problems(arn: str, resource: str, partition: str | None, account: str | None) -> list[str]:
    """``arn`` is an IAM ``resource`` (role / policy) ARN in the env's partition and account."""
    m = re.match(r"^arn:([a-z-]+):iam::(\d{12}):(role|policy)/(.+)$", str(arn or ""))
    if not m:
        return [f"{arn!r} is not an IAM {resource} ARN (arn:<partition>:iam::<account>:{resource}/<name>)"]
    out = []
    if m.group(3) != resource:
        out.append(f"{arn!r} is a {m.group(3)}, not a {resource}")
    if partition and m.group(1) != partition:
        out.append(f"{arn!r} is in partition {m.group(1)}, the env is in {partition}")
    if account and m.group(2) != str(account):
        out.append(f"{arn!r} is in account {m.group(2)}, the env is in {account}")
    return out


# ================================================================ assembly + report
class Assembly:
    """What `vra iam` reads from a cloud assembly: the iam-policy-report, template resources by construct path,
    and the stack artifacts of manifest.json."""

    def __init__(self, report: dict | None, templates: dict[str, dict], manifest: dict | None, where: str = ""):
        self.report = report
        self.templates = templates
        self.manifest = manifest or {}
        self.where = where
        self.by_path: dict[str, tuple[str, str, dict]] = {}   # cdk path -> (template, logical id, resource)
        for tname, t in templates.items():
            for lid, res in (t.get("Resources") or {}).items():
                p = (res.get("Metadata") or {}).get("aws:cdk:path")
                if p:
                    self.by_path[p] = (tname, lid, res)

    @classmethod
    def load(cls, out: Path) -> "Assembly":
        """A cdk.out directory, including nested cloud assemblies (CDK stages, ``cdk:cloud-assembly`` artifacts
        of manifest.json): their templates and stack artifacts are read too."""
        out = Path(out)
        rep = out / REPORT
        report = util.read_json(rep) if rep.exists() else None
        templates: dict[str, dict] = {}
        artifacts: dict[str, dict] = {}
        assets: list[dict] = []
        seen: set[Path] = set()

        def walk(d: Path, prefix: str) -> None:
            d = d.resolve()
            if d in seen or not d.is_dir():
                return
            seen.add(d)
            for f in sorted(d.glob("*.template.json")):
                templates[prefix + f.name] = util.read_json(f)
            for f in sorted(d.glob("*.assets.json")):
                try:
                    assets.append(util.read_json(f))
                except ValueError:
                    continue
            man = d / "manifest.json"
            if not man.exists():
                return
            m = util.read_json(man)
            for k, a in (m.get("artifacts") or {}).items():
                if a.get("type") == "cdk:cloud-assembly":
                    sub = (a.get("properties") or {}).get("directoryName")
                    if sub:
                        walk(d / sub, f"{prefix}{sub}/")
                else:
                    artifacts[prefix + k] = a
        walk(out, "")
        top = util.read_json(out / "manifest.json") if (out / "manifest.json").exists() else None
        nested = any(a.get("type") == "cdk:cloud-assembly" for a in ((top or {}).get("artifacts") or {}).values())
        manifest = {**top, "artifacts": artifacts} if top is not None and nested else top
        asm = cls(report, templates, manifest, str(out))
        asm.asset_manifests = assets
        return asm

    asset_manifests: list = []

    def publishing_arns(self) -> dict[str, set]:
        """``file-publishing`` / ``image-publishing`` -> the assumeRoleArn values of the asset manifests."""
        out: dict[str, set] = {"file-publishing": set(), "image-publishing": set()}
        for m in self.asset_manifests or []:
            for kind, key in (("file-publishing", "files"), ("image-publishing", "dockerImages")):
                for a in (m.get(key) or {}).values():
                    for dest in (a.get("destinations") or {}).values():
                        if dest.get("assumeRoleArn"):
                            out[kind].add(dest["assumeRoleArn"])
        return out

    @classmethod
    def from_fixture(cls, path: Path) -> "Assembly":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
        return cls(doc.get("report"), doc.get("templates") or {}, doc.get("manifest"), str(path))

    def write(self, out: Path) -> None:
        """Lay the assembly out as a cdk.out directory (tests, `--report` copies)."""
        out.mkdir(parents=True, exist_ok=True)
        if self.report is not None:
            util.write_json(out / REPORT, self.report)
        for n, t in self.templates.items():
            util.write_json(out / n, t)
        if self.manifest:
            util.write_json(out / "manifest.json", self.manifest)

    # ------------------------------------------------------------ roles in templates
    def template_roles(self) -> list[dict]:
        """Every ``AWS::IAM::Role`` CloudFormation would create (construct path, name, boundary)."""
        out = []
        for tname, t in sorted(self.templates.items()):
            for lid, res in sorted((t.get("Resources") or {}).items()):
                if res.get("Type") != "AWS::IAM::Role":
                    continue
                p = (res.get("Metadata") or {}).get("aws:cdk:path") or f"{tname}:{lid}"
                props = res.get("Properties") or {}
                out.append({"path": role_path(p), "template": tname, "logicalId": lid,
                            "roleName": props.get("RoleName"), "boundary": props.get("PermissionsBoundary")})
        return out

    def bootstrap_arns(self) -> dict[str, dict]:
        out = {}
        for k, a in (self.manifest.get("artifacts") or {}).items():
            if a.get("type") != "aws:cloudformation:stack":
                continue
            p = a.get("properties") or {}
            out[p.get("stackName") or k] = {"deploy": p.get("assumeRoleArn"),
                                            "cfn-exec": p.get("cloudFormationExecutionRoleArn"),
                                            "lookup": (p.get("lookupRole") or {}).get("arn")}
        return out

    # ------------------------------------------------------------ deploy-time vs run-time roles
    def deploy_time_roles(self) -> set[str]:
        """Construct paths of roles whose function backs a CloudFormation custom resource (used while a deploy
        runs). In a report-only synth the functions do not reference their roles (CDK renders ``arn:...:role``
        with no name), so the role is found by the construct convention: ``<function>/ServiceRole`` for
        ``lambda.Function``, ``<provider>/Role`` for a ``CustomResourceProvider`` (handler ``<provider>/Handler``);
        a provider framework's ``USER_*_FUNCTION_ARN`` names the user handler behind it."""
        out: set[str] = set()
        for t in self.templates.values():
            res = t.get("Resources") or {}
            fn_path = {lid: role_path((r.get("Metadata") or {}).get("aws:cdk:path") or lid)
                       for lid, r in res.items() if r.get("Type") == "AWS::Lambda::Function"}
            users: dict[str, set[str]] = {}
            for lid in fn_path:
                variables = ((res[lid].get("Properties") or {}).get("Environment") or {}).get("Variables") or {}
                for v in variables.values():
                    target = _getatt_lid(v)
                    if target in fn_path:
                        users.setdefault(lid, set()).add(target)
            backing: set[str] = set()
            for r in res.values():
                ty = r.get("Type", "")
                if ty.startswith("Custom::") or ty == "AWS::CloudFormation::CustomResource":
                    target = _getatt_lid((r.get("Properties") or {}).get("ServiceToken"))
                    if target in fn_path:
                        backing.add(target)
                        backing |= users.get(target, set())
            for lid in backing:
                p = fn_path[lid]
                out.add(f"{p}/ServiceRole")
                if p.endswith("/Handler"):
                    out.add(p[:-len("/Handler")] + "/Role")
        return out

    def phases(self) -> dict[str, str]:
        """construct path of a role -> ``deploy`` or ``runtime`` (empty without templates)."""
        if not self.templates:
            return {}
        dt = self.deploy_time_roles()
        paths = {r["roleConstructPath"] for r in (self.report or {}).get("roles") or []} | \
            {r["path"] for r in self.template_roles()}
        return {p: ("deploy" if p in dt or "CustomResource" in p.rsplit("/", 1)[-1] else "runtime") for p in paths}


def _getatt_lid(v: Any) -> str | None:
    if isinstance(v, dict):
        ga = v.get("Fn::GetAtt")
        if isinstance(ga, list) and ga:
            return ga[0]
        if isinstance(ga, str):
            return ga.split(".")[0]
        if "Ref" in v:
            return v["Ref"]
    return None


def role_path(cdk_path: str) -> str:
    """Template metadata path of a role -> the construct path the iam-policy-report and precreatedRoles use."""
    return cdk_path[:-len("/Resource")] if cdk_path.endswith("/Resource") else cdk_path


# ================================================================ role rows
def classify(path: str) -> dict:
    parts = path.split("/")
    top = parts[1] if len(parts) > 1 else ""
    label, option = FEATURES.get(top, (top or "core stack", None))
    if top in ("PipelineBuilder", "AddonBuilder") and len(parts) > 2:
        sub = parts[2]
        name = sub[:-len("NestedStack")] if sub.endswith("NestedStack") else sub
        if "EmptyLambda" not in sub:
            group = "pipelines" if top == "PipelineBuilder" else "addons"
            label = f"{label}: {name}"
            option = f"app.{group}.use{name[:1].upper()}{name[1:]}.enabled"
        else:
            label, option = f"{label} (placeholder handler)", None
    return {"feature": label, "configOption": option}


def purpose(path: str, trust: list[dict]) -> str:
    parts = path.split("/")
    for marker, what in CDK_INTERNAL:
        if any(marker in p for p in parts):
            return what
    svc = None
    for st in trust:
        pr = st.get("Principal") or {}
        val = pr.get("Service") or pr.get("Federated")
        if isinstance(val, list):
            val = val[0] if val else None
        if val:
            svc = str(val).split(".amazonaws.com")[0]
            break
    kind = PRINCIPAL_KIND.get(svc or "", f"{svc} role" if svc else "role")
    name = parts[-2] if parts[-1] in ("ServiceRole", "Role", "IAMRole") and len(parts) > 2 else parts[-1]
    return f"{kind}: {name}"


def _principals(trust: list[dict]) -> list[str]:
    out = []
    for st in trust:
        for k, v in (st.get("Principal") or {}).items():
            for x in (v if isinstance(v, list) else [v]):
                if x == {"Ref": "AWS::AccountId"}:
                    x = "this account"
                out.append(f"{k}:{x if isinstance(x, str) else json.dumps(x)}")
    return sorted(set(out))


def role_rows(asm: Assembly, env_info: dict) -> tuple[list[dict], list[dict]]:
    """(roles VAMS creates, references to roles that must already exist) from the iam-policy-report."""
    if asm.report is None:
        raise UsageError(f"{asm.where}: no {REPORT}; synthesize with app.iamRoleConfig.useCustomVamsStackRoles and "
                         f"vamsStacks.generateReportOnly (vra iam plan does)")
    phases = asm.phases()
    roles: list[dict] = []
    refs: dict[str, dict] = {}
    # a VAMS role that grants iam:PassRole on another VAMS role: in a report-only synth CDK renders the other
    # role as arn:<p>:iam::<a>:role (no name); with precreated roles it is that role's IT name. Both become the
    # name pattern of the env's IT-created VAMS roles (prefix + baseStackName, as it-roles.yaml names them).
    assigned = sorted({x["roleName"] for x in asm.report.get("roles") or []
                       if x.get("missing") is False and x.get("roleName")}, key=len, reverse=True)
    path_of_name = {x["roleName"]: x["roleConstructPath"] for x in asm.report.get("roles") or []
                    if x.get("missing") is False and x.get("roleName")}
    all_paths = {x["roleConstructPath"] for x in asm.report.get("roles") or [] if x.get("assumeRolePolicy")}
    role_arn = f"arn:{env_info['partition']}:iam::{env_info['account']}:role"
    pattern = f"{role_arn}/{env_info.get('rolePattern') or '*'}"
    exact = bool(env_info.get("precreated")) or bool(path_of_name)

    def name_for(path: str) -> str:
        if path in path_of_name.values():
            return next(n for n, p in path_of_name.items() if p == path)
        prefix = env_info.get("rolePrefix") or ""
        return prefix + suggested_name({"constructPath": path, "id": role_id(path)}, prefix,
                                       env_info.get("nameBase") or "vams")

    def pass_targets(path: str, x: str) -> list[str] | None:
        """Construct paths of the VAMS roles a PassRole resource ``x`` of role ``path`` names (None: not a
        VAMS role reference)."""
        if x.startswith(role_arn + "/") and x[len(role_arn) + 1:] in path_of_name:
            return [path_of_name[x[len(role_arn) + 1:]]]
        if x == role_arn or (x.startswith(role_arn + "/") and "*" in x):
            tail = path.split("/", 1)[1] if "/" in path else path
            stack = path.split("/", 1)[0]
            got = [f"{stack}/{t}" for t in PASSROLE_TARGETS.get(tail, ()) if f"{stack}/{t}" in all_paths]
            if got:
                return got
            return None if x != role_arn else []
        return None
    for r in asm.report.get("roles") or []:
        path = r["roleConstructPath"]
        if r.get("missing") is None and r.get("roleName") and not r.get("assumeRolePolicy"):
            # Role.fromRoleName: VAMS references a role it does not create (e.g. a service-linked role)
            ref = refs.setdefault(r["roleName"], {"roleName": r["roleName"], "constructPaths": []})
            ref["constructPaths"].append(path)
            continue
        trust = r.get("assumeRolePolicy") or []
        stmts, unresolved = resolve_statements(
            (r.get("identityPolicyStatements") or []) + (r.get("managedPolicyStatements") or []), asm, path, env_info)
        # iam:PassRole on another VAMS role: with IT-created roles the grant names exactly the roles VAMS passes
        #; the digest form names them by construct path, so a report-only plan and a precreated synth
        # (other names) give the same policy digest
        targets: set[str] = set()
        out_stmts, norm_stmts = [], []
        for st in stmts:
            res_out, res_norm = [], []
            for x in _list(st["Resource"]):
                tg = pass_targets(path, x) if "iam:PassRole" in _list(st["Action"]) else \
                    ([] if (x == role_arn or x in {f"{role_arn}/{n}" for n in assigned}) else None)
                if tg and exact:
                    targets |= set(tg)
                    res_out += [f"{role_arn}/{name_for(t)}" for t in tg]
                    res_norm += [f"{role_arn}/<vams-role:{t}>" for t in tg]
                elif tg is not None:
                    # VAMS creates the roles (no IT names): the nameless reference becomes the env's role-name
                    # pattern, a literal VAMS pattern (role/*<config.name>*) stays what VAMS grants
                    targets |= set(tg or [])
                    res_out.append(pattern if x == role_arn or x.startswith(role_arn + "/") and "*" not in x
                                   else x)
                    res_norm += [f"{role_arn}/<vams-role:{t}>" for t in tg] if tg else [pattern]
                else:
                    res_out.append(x)
                    res_norm.append(x)
            out_stmts.append({**st, "Resource": res_out, "Action": _list(st["Action"])})
            norm_stmts.append({**st, "Resource": res_norm, "Action": _list(st["Action"])})
        stmts = merge_statements(out_stmts)
        managed = sorted({_partition(m, env_info["partition"]) for m in r.get("managedPolicyArns") or []})
        trust_r, trust_pending = resolve_trust(trust, env_info)
        policy = {"Version": "2012-10-17", "Statement": stmts}
        digest_policy = {"Version": "2012-10-17", "Statement": merge_statements(norm_stmts)}
        row = {"id": role_id(path), "constructPath": path, "kind": "vams-stack", **classify(path),
               "purpose": purpose(path, trust), "phase": phases.get(path, "unknown"),
               "trust": trust_r, "trustPrincipals": _principals(trust), "trustPending": trust_pending,
               "managedPolicyArns": managed, "policy": policy, "unresolvedResources": unresolved,
               "actions": sorted({a for st in stmts for a in _list(st.get("Action"))}),
               "assignedRoleName": None if r.get("missing", True) else r.get("roleName"),
               "passRoleTargets": sorted(targets)}
        row["policySha256"] = util.sha256_bytes(json.dumps([trust_r, managed, digest_policy],
                                                           sort_keys=True).encode())
        roles.append(row)
    roles.sort(key=lambda x: x["constructPath"])
    return roles, sorted(refs.values(), key=lambda x: x["roleName"])


def role_id(path: str) -> str:
    seg = [p for p in path.split("/")[1:] if p not in ("ServiceRole", "Role", "Resource")]
    tail = re.sub(r"[^A-Za-z0-9]", "", seg[-1] if seg else "role")[:24] or "role"
    return f"{tail}-{hashlib.sha256(path.encode()).hexdigest()[:8]}"


def _list(v: Any) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _partition(s: str, partition: str) -> str:
    return s.replace("arn:(PARTITION):", f"arn:{partition}:").replace("arn:${AWS::Partition}:", f"arn:{partition}:")


# Generated (CloudFormation-named) Lambda functions keep at most this many characters of the stack name: a census of
# 639 generated function names in 26 VAMS stacks (a live census) found the full name for 25-char core stack
# names and exactly 25 characters for 26-char ones. DynamoDB tables, SNS topics and SQS queues kept the full name.
LAMBDA_GENERATED_STACK_CHARS = 25
TOKEN_RE = re.compile(r"\(([^()]+)\)")
OPAQUE_RE = re.compile(r"\$\{Token\[[^\]]+\]\}")


def resolve_resource(s: str, asm: Assembly, env_info: dict) -> tuple[str | None, str]:
    """Report resource string -> concrete ARN or name pattern. ``(None, why)`` when it cannot be resolved."""
    if not isinstance(s, str):
        return None, "intrinsic"
    if OPAQUE_RE.search(s):
        return None, "unresolved CDK token"
    out = s.replace("(PARTITION)", env_info["partition"]).replace("(ACCOUNT)", env_info["account"]) \
        .replace("(REGION)", env_info["region"])
    for tok in TOKEN_RE.findall(out):
        if "." not in tok or "/" not in tok:
            return None, f"token {tok}"
        cpath, attr = tok.rsplit(".", 1)
        hit = asm.by_path.get(cpath)
        if hit is None:
            return None, f"{cpath} not in the assembly"
        arn = _arn_for(hit[2], attr, cpath.split("/")[0], env_info)
        if arn is None:
            return None, f"{hit[2].get('Type')}.{attr}"
        out = out.replace(f"({tok})", arn)
    return out, "exact" if "*" not in out else "pattern"


def _arn_for(res: dict, attr: str, stack: str, v: dict) -> str | None:
    t = res.get("Type")
    name = (res.get("Properties") or {}).get(NAME_PROPS.get(t, ""), None)
    name = name if isinstance(name, str) else None
    p, r, a = v["partition"], v["region"], v["account"]
    gen = f"{stack}*"
    # ``Ref`` of a table or a function is its NAME (CDK writes ``arn:...:function:${Ref}``); only
    # ``Arn`` is the ARN. Returning the ARN for Ref doubled the prefix (function:arn:<p>:lambda:...) and the grant
    # matched nothing.
    if t == "AWS::DynamoDB::Table":
        base = f"arn:{p}:dynamodb:{r}:{a}:table/{name or gen}"
        if attr == "Ref":
            return name or gen
        return base + "/stream/*" if attr == "StreamArn" else (base if attr == "Arn" else None)
    if t == "AWS::S3::Bucket" and attr in ("Arn", "Ref"):
        return f"arn:{p}:s3:::{name or stack.lower() + '*'}" if attr == "Arn" else (name or stack.lower() + "*")
    if t == "AWS::Logs::LogGroup" and attr == "Arn":
        return f"arn:{p}:logs:{r}:{a}:log-group:{name or '*'}:*"
    if t == "AWS::Lambda::Function" and attr in ("Arn", "Ref"):
        # CloudFormation keeps at most LAMBDA_GENERATED_STACK_CHARS of the stack name in a generated
        # function name (vams-core-prodfr-us-east-1 -> vams-core-prodfr-us-east--PopulateS3AssetBucketsTa-3HgT...)
        fn = name or stack[:LAMBDA_GENERATED_STACK_CHARS] + "*"
        return fn if attr == "Ref" else f"arn:{p}:lambda:{r}:{a}:function:{fn}"
    if t == "AWS::SQS::Queue" and attr == "Arn":
        return f"arn:{p}:sqs:{r}:{a}:{name or gen}"
    if t == "AWS::SNS::Topic" and attr in ("Ref", "TopicArn"):
        return f"arn:{p}:sns:{r}:{a}:{name or gen}"
    if t == "AWS::Events::EventBus" and attr == "Arn":
        return f"arn:{p}:events:{r}:{a}:event-bus/{name or '*'}"
    if t == "AWS::OpenSearchServerless::Collection" and attr == "Arn":
        return f"arn:{p}:aoss:{r}:{a}:collection/*"
    if t == "AWS::Cognito::UserPool" and attr == "Arn":
        return f"arn:{p}:cognito-idp:{r}:{a}:userpool/*"
    if t == "AWS::StepFunctions::StateMachine" and attr in ("Arn", "Ref"):
        return f"arn:{p}:states:{r}:{a}:stateMachine:{name or gen}"
    if t == "AWS::KMS::Key" and attr == "Arn":
        return f"arn:{p}:kms:{r}:{a}:key/*"
    return None


def resolve_statements(stmts: list[dict], asm: Assembly, path: str, env_info: dict) -> tuple[list[dict], list[dict]]:
    """Resolve and merge a role's statements. Unresolvable resources become service-scoped ARNs in the env's
    account and region, listed in ``unresolved`` for IT to narrow."""
    resolved: list[dict] = []
    unresolved: list[dict] = []
    for st in stmts:
        actions = sorted(set(_list(st.get("Action"))))
        svcs = sorted({a.split(":")[0] for a in actions if ":" in a}) or ["*"]
        stack = path.split("/")[0]
        res_out: list[str] = []
        for res in _list(st.get("Resource")) or ["*"]:
            got, why = resolve_resource(res, asm, env_info)
            if got is None and isinstance(res, str) and OPAQUE_RE.fullmatch(res):
                got = _runtime_pattern(path, svcs, env_info)   #
            if got is None:
                original = res if isinstance(res, str) else json.dumps(res)
                scoped = _stack_scoped(original, svcs, stack, env_info)
                fbs = [scoped] if scoped else [f"arn:{env_info['partition']}:{svc}:{env_info['region']}:"
                                               f"{env_info['account']}:*" for svc in svcs]
                for fb in fbs:
                    res_out.append(fb)
                    unresolved.append({"actions": actions, "original": original, "why": why, "grantedOn": fb,
                                       "scope": "core stack name" if scoped else "service"})
            else:
                res_out.append(got)
        item = {"Effect": st.get("Effect", "Allow"), "Action": actions, "Resource": sorted(set(res_out))}
        if st.get("Condition"):
            item["Condition"] = _sub_literals(st["Condition"], env_info)
        resolved.append(item)
    return merge_statements(resolved), unresolved


# VAMS roles whose opaque-token resource is a runtime-named ARN pattern, built with ``cdk.Fn.sub`` (the
# iam-policy-report shows only ``${Token[...]}``, and the precreated build synth has no policy to read it from, so
# the plan and the deploy gate must both resolve it from this table to get the same policy digest). By construct
# path below the core stack:
# - per-asset SNS topics ``AssetTopic<databaseId>-<assetId>``, created by createAsset at runtime:
#   infra/lib/lambdaBuilder/assetFunctions.ts (createAsset, assetService), sendEmailFunctions.ts (sendEmail),
#   subscriptionFunctions.ts (subscriptionService, unsubscribeService), searchIndexBucketSyncFunctions.ts (the
#   sqsBucketSync-<event>-<n> handlers)
RUNTIME_PATTERNS = [
    (re.compile(r"ApiBuilder/(createAsset|assetService|sendEmail|subscriptionService|unsubscribeService)/ServiceRole"),
     "sns", "arn:{p}:sns:{r}:{a}:AssetTopic*"),
    (re.compile(r"StorageResourcesBuilder/sqsBucketSync-[A-Za-z]+-[0-9]+/ServiceRole"),
     "sns", "arn:{p}:sns:{r}:{a}:AssetTopic*"),
]


def _runtime_pattern(path: str, svcs: list[str], v: dict) -> str | None:
    tail = path.split("/", 1)[1] if "/" in path else path
    for rx, svc, pattern in RUNTIME_PATTERNS:
        if svcs == [svc] and rx.fullmatch(tail):
            return pattern.format(p=v["partition"], r=v["region"], a=v["account"])
    return None


# A cross-stack CDK token (a table or topic of another VAMS nested stack, passed in as a parameter) whose
# statement names one service: every resource of that service in VAMS has a CloudFormation-generated name
# that starts with the core stack name (no explicit TableName / TopicName in the v2.6.6 templates).
# The VAMS roles a VAMS role passes (iam:PassRole), by construct path below the core stack. In a report-only synth
# CDK renders the passed role as ``arn:<p>:iam::<a>:role`` (no name), and workflowService grants the literal
# ``role/*<config.name>*``; both name these roles:
# - infra/lib/lambdaBuilder/pipelineFunctions.ts: pipelineService passes newPipelineLambdaRole (lambdaPipelineRole)
# - infra/lib/lambdaBuilder/workflowFunctions.ts: workflowService passes the workflow role (VAMSWorkflowIAMRole)
PASSROLE_TARGETS = {
    "ApiBuilder2/pipelineService/ServiceRole": ("ApiBuilder2/lambdaPipelineRole",),
    "ApiBuilder2/workflowService/ServiceRole": ("ApiBuilder2/VAMSWorkflowIAMRole",),
    "ApiBuilder2/workflowServiceV2/ServiceRole": ("ApiBuilder2/VAMSWorkflowIAMRole",),
}
STACK_SCOPED = {"dynamodb": "arn:{p}:dynamodb:{r}:{a}:table/{stack}*", "sns": "arn:{p}:sns:{r}:{a}:{stack}*"}


def _stack_scoped(original: str, svcs: list[str], stack: str, v: dict) -> str | None:
    m = OPAQUE_RE.match(original)
    if len(svcs) != 1 or svcs[0] not in STACK_SCOPED or not m or OPAQUE_RE.search(original, m.end()) \
            or TOKEN_RE.search(original[m.end():]):
        return None   # only "<token>" or "<token>/index/*": the token is the resource ARN itself
    base = STACK_SCOPED[svcs[0]].format(p=v["partition"], r=v["region"], a=v["account"], stack=stack)
    return base + original[m.end():]


def _sub_literals(obj: Any, env_info: dict) -> Any:
    text = json.dumps(obj).replace("(PARTITION)", env_info["partition"]).replace("(ACCOUNT)", env_info["account"]) \
        .replace("(REGION)", env_info["region"])
    return json.loads(text)


def _subify(v: Any) -> Any:
    """Strings naming CloudFormation pseudo parameters (``${AWS::...}``, packages written by `write_package`)
    become ``Fn::Sub``; any other ``${`` in them (IAM policy variables) is escaped as ``${!``."""
    if isinstance(v, str):
        if "${AWS::" not in v:
            return v
        return {"Fn::Sub": re.sub(r"\$\{(?!AWS::)", "${!", v)}
    if isinstance(v, list):
        return [_subify(x) for x in v]
    if isinstance(v, dict):
        if set(v) & {"Fn::Sub", "Ref", "Fn::If", "Fn::GetAtt"}:
            return v
        return {k: _subify(x) for k, x in v.items()}
    return v


def merge_statements(stmts: list[dict]) -> list[dict]:
    """Same effect + condition + resources -> one statement (actions unioned), then same actions -> resources
    unioned. Deterministic order."""
    def key(st, what):
        return json.dumps([st["Effect"], st.get("Condition"), st[what]], sort_keys=True)
    by_res: dict[str, dict] = {}
    for st in stmts:
        k = key(st, "Resource")
        cur = by_res.setdefault(k, {**st, "Action": []})
        cur["Action"] = sorted(set(cur["Action"]) | set(st["Action"]))
    by_act: dict[str, dict] = {}
    for st in by_res.values():
        k = key(st, "Action")
        cur = by_act.setdefault(k, {**st, "Resource": []})
        cur["Resource"] = sorted(set(cur["Resource"]) | set(st["Resource"]))
    out = []
    for st in sorted(by_act.values(), key=lambda s: json.dumps(s, sort_keys=True)):
        st = dict(st)
        st["Action"] = st["Action"][0] if len(st["Action"]) == 1 else st["Action"]
        st["Resource"] = st["Resource"][0] if len(st["Resource"]) == 1 else st["Resource"]
        out.append(st)
    return out


def resolve_trust(trust: list[dict], env_info: dict) -> tuple[list[dict], list[dict]]:
    """Trust statements with literal values; a condition that names a VAMS resource created later (the Cognito
    identity pool) becomes a ``PENDING`` placeholder IT replaces after the first deploy. Never widened."""
    pending: list[dict] = []
    out = []
    for st in trust:
        st = _sub_literals(st, env_info)

        def walk(v, where):
            if isinstance(v, dict):
                if set(v) & {"Ref", "Fn::GetAtt", "Fn::ImportValue", "Fn::Join", "Fn::Sub"}:
                    ref = v.get("Ref") or json.dumps(v)
                    pending.append({"where": where, "reference": ref})
                    return PENDING
                return {k: walk(x, f"{where}.{k}") for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x, where) for x in v]
            return v
        out.append(walk(st, "Condition"))
    return out, pending


# ================================================================ vra-side + bootstrap roles
def vra_roles(ctx, env: str, s: dict) -> list[dict]:
    """The roles vra itself runs as (iam/cloudformation/*), with what each must be allowed for this env."""
    from vra import permissions
    e = ctx.ledger.env(env)
    vals = permissions.placeholders(ctx, env)
    feats = permissions.env_features(ctx, env)

    def statements(workflows: list[str], tools: bool = False) -> list[dict]:
        out = []
        for wf in workflows:
            try:
                man = permissions.load_manifest(wf)
            except Exception:  # noqa: BLE001 - a missing manifest is listed, not fatal
                continue
            for st in permissions.expand_manifest(man, vals, feats):
                if st["tools"] == tools:
                    out.append({"Sid": st.get("sid"), "Effect": "Allow", "Action": st["actions"],
                                "Resource": st["resources"], "workflow": wf, "optional": st["optional"]})
        return out

    deploy_wfs = ["deploy", "backup", "migrate", "test-live", "preflight", "rollback", "restore", "reindex"]
    xp = execution_policy_parameters(e, env, s)
    deploy_notes = ["CloudFormation runs as the CDK execution role; use iam/cloudformation/vra-cdk-execution-"
                    "policy.yaml as its policy (RequiredPermissionsBoundaryArn = the env's boundary in boundary "
                    "mode) with ConfigName=" + xp["ConfigName"] + ", CoreRoleNameStem=" + xp["CoreRoleNameStem"] +
                    " and RoleNamePrefix=" + (xp["RoleNamePrefix"] or '""') + ": it creates, changes and passes "
                    "only roles with those names (also the IT-precreated VAMS roles), never cdk-* / vra-* roles"]
    q = s["bootstrap"].get("qualifier") or ctx.ledger.project.get("cdk", {}).get("qualifier") or DEFAULT_QUALIFIER
    if "bootstrap" in s["modes"] and s["cliCredentialsSynthesizer"]:
        deploy_notes.append("bootstrap mode with cliCredentialsSynthesizer: there are no CDK bootstrap roles; this "
                            "role deploys with its own credentials, so it needs the CDK execution policy itself "
                            "plus the staging bucket and ECR repository: deploy vra-deploy-role.yaml with "
                            "CliCredentialsDeploy=true, CdkExecutionPolicyArn (the vra-cdk-execution-policy.yaml "
                            "policy), StagingBucketName and StagingRepositoryName")
    elif "bootstrap" in s["modes"]:
        arns = bootstrap_assume_arns(ctx, env) or []
        deploy_notes.append("bootstrap mode: this role assumes the IT-provided bootstrap roles instead of "
                            "cdk-<qualifier>-*: deploy vra-deploy-role.yaml with BootstrapRoleArns="
                            + ",".join(arns) + " (the ARNs IT returns; CdkBootstrapRolesAssume names them)")
    if "bootstrap" in s["modes"]:
        deploy_notes.append(f"the CDK CLI reads the SSM parameter /cdk-bootstrap/{q}/version before every deploy; "
                            f"when IT creates the bootstrap resources without `cdk bootstrap`, IT also creates that "
                            f"String parameter (the bootstrap template version, e.g. the one in it-roles.yaml)")
    out = [{"id": f"vra-deploy-{env}", "kind": "vra", "name": _arn_name(e.get("deployRoleArn")) or f"vra-deploy-{env}",
            "arn": e.get("deployRoleArn"), "template": "iam/cloudformation/vra-deploy-role.yaml",
            "purpose": "vra deploy / backup / migrate / test / rollback / restore in this env",
            "trustPrincipals": ["tools / CI identity (TrustedPrincipalArns)"], "phase": "deploy",
            "statements": statements(deploy_wfs), "notes": deploy_notes,
            "executionPolicyParameters": xp}]
    if e.get("synthRoleArn") or e.get("requiresApproval"):
        out.append({"id": f"vra-synth-{env}", "kind": "vra", "name": _arn_name(e.get("synthRoleArn")) or
                    f"vra-synth-{env}", "arn": e.get("synthRoleArn"),
                    "template": "iam/cloudformation/vra-deploy-role.yaml (CreateSynthRole=true)",
                    "purpose": "build-only cdk synth (CDK context lookups only)", "trustPrincipals":
                    ["tools / CI identity"], "phase": "build", "statements": [], "notes":
                    ["optional: without it an approval-gated env synthesizes with no AWS credentials"]})
    if e.get("testRoleArn"):
        out.append({"id": f"vra-test-{env}", "kind": "vra", "name": _arn_name(e["testRoleArn"]),
                    "arn": e["testRoleArn"], "template": "iam/cloudformation/vra-deploy-role.yaml (CreateTestRole=true)",
                    "purpose": "live tests (stack outputs, resource-name parameters, the test secret)",
                    "trustPrincipals": ["tools / CI identity"], "phase": "test", "statements": [], "notes": []})
    sources = [o for o in ctx.ledger.env_names() if env in ((ctx.ledger.env(o).get("dataCopy") or {})
                                                            .get("allowedSources") or [])]
    if (e.get("dataCopy") or {}).get("allowedSources"):
        out.append({"id": f"vra-data-copy-target-{env}", "kind": "vra", "name": f"vra-data-copy-target-{env}",
                    "arn": e.get("dataCopyRoleArn"), "template": "iam/cloudformation/vra-data-copy-roles.yaml "
                    "(Side=target)", "purpose": "load copied data into this env", "trustPrincipals":
                    ["tools / CI identity"], "phase": "data-copy", "statements": statements(["data-copy-target"]),
                    "notes": []})
    if sources:
        out.append({"id": f"vra-data-copy-source-{env}", "kind": "vra", "name": f"vra-data-copy-source-{env}",
                    "arn": e.get("dataCopyRoleArn"), "template": "iam/cloudformation/vra-data-copy-roles.yaml "
                    "(Side=source)", "purpose": f"export this env's data for {', '.join(sources)}",
                    "trustPrincipals": ["tools / CI identity"], "phase": "data-copy",
                    "statements": statements(["data-copy-source"]), "notes": []})
    tools = statements(["sync", "build", "report", "deploy"], tools=True)
    if tools:
        out.append({"id": "vra-tools", "kind": "vra", "name": "tools / CI identity", "arn": None,
                    "template": "iam/cloudformation/vra-state-store.yaml (stateStore.type s3) + your CI role",
                    "purpose": "the orchestrator: state store, locks, artifacts, assuming the env roles",
                    "trustPrincipals": ["your CI / operator principal"], "phase": "all",
                    "statements": _dedupe(tools), "notes": ["one identity for all envs (tools account)"]})
    return out


def _dedupe(stmts: list[dict]) -> list[dict]:
    seen, out = set(), []
    for st in stmts:
        k = json.dumps([st.get("Sid"), st["Action"], st["Resource"]], sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(st)
    return out


def _arn_name(arn: str | None) -> str | None:
    return arn.rsplit("/", 1)[-1] if arn else None


_CFN_TAGS = ("Ref", "Condition")


class _CfnLoader(yaml.SafeLoader):
    pass


def _cfn_ctor(loader, tag_suffix, node):
    name = "Ref" if tag_suffix == "Ref" else ("Condition" if tag_suffix == "Condition" else f"Fn::{tag_suffix}")
    if isinstance(node, yaml.ScalarNode):
        val: Any = loader.construct_scalar(node)
        if tag_suffix == "GetAtt":
            val = str(val).split(".", 1)
    elif isinstance(node, yaml.SequenceNode):
        val = loader.construct_sequence(node, deep=True)
    else:
        val = loader.construct_mapping(node, deep=True)
    return {name: val}


_CfnLoader.add_multi_constructor("!", _cfn_ctor)


def load_cfn_yaml(text: str) -> dict:
    loader = _CfnLoader(text)  # a yaml.SafeLoader subclass that only adds the CFN tags
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def bootstrap_roles(ctx, env: str, s: dict, template_text: str | None = None) -> list[dict]:
    """The CDK bootstrap roles IT pre-creates (bootstrap mode, not cliCredentialsSynthesizer), modelled on the
    bootstrap template of the CDK CLI the fork pins (``aws-cdk/lib/api/bootstrap/bootstrap-template.yaml``), else
    on the packaged copy taken from it (``data/cdk-bootstrap-roles.json``)."""
    from vra import data as vra_data
    if "bootstrap" not in s["modes"] or s["cliCredentialsSynthesizer"]:
        return []
    if template_text:
        src = extract_bootstrap_roles(load_cfn_yaml(template_text))
        source = "fork's aws-cdk bootstrap template"
    else:
        src = json.loads(vra_data.text("cdk-bootstrap-roles.json"))
        source = f"packaged copy ({src.get('source')})"
    e = ctx.ledger.env(env)
    q = s["bootstrap"].get("qualifier") or ctx.ledger.project.get("cdk", {}).get("qualifier") or DEFAULT_QUALIFIER
    out = []
    for kind, key in BOOTSTRAP_ROLES.items():
        d = src["roles"][kind]
        out.append({"id": f"cdk-{kind}", "kind": "cdk-bootstrap", "bootstrapKey": key, "qualifier": q,
                    "defaultName": bootstrap_role_name(kind, q, e, s["rolePrefix"]), "arn": s["bootstrap"].get(key),
                    "purpose": d["purpose"], "trustPrincipals": d["trustPrincipals"], "phase": "deploy",
                    "trust": d["trust"], "policy": d["policy"], "managedPolicyArns": d.get("managedPolicyArns", []),
                    "notes": d.get("notes", []), "source": source})
    return out


ASSUMED_BOOTSTRAP = ("deploy", "lookup", "file-publishing", "image-publishing")   # the CDK CLI assumes these


def bootstrap_assume_arns(ctx, env: str) -> list[str] | None:
    """iam.mode bootstrap: the bootstrap role ARNs vra's deploy role assumes, from env.yaml
    ``iam.bootstrap``, else IT's applied response, else the names `it-roles.yaml` gives them. ``[]`` with
    cliCredentialsSynthesizer (no bootstrap roles), ``None`` outside bootstrap mode (the cdk-* defaults apply)."""
    e = ctx.ledger.env(env)
    s = settings(e)
    if "bootstrap" not in s["modes"]:
        return None
    if s["cliCredentialsSynthesizer"]:
        return []
    applied = (applied_record(ctx, env) or {}).get("bootstrap") or {}
    if applied.get("useCliCredentialsSynthesizer"):
        return []
    from vra.ledger import partition_for_region
    part = e.get("partition") or partition_for_region(e["region"]) or "aws"
    q = s["bootstrap"].get("qualifier") or applied.get("qualifier") or \
        ctx.ledger.project.get("cdk", {}).get("qualifier") or DEFAULT_QUALIFIER
    out = []
    for kind in ASSUMED_BOOTSTRAP:
        key = BOOTSTRAP_ROLES[kind]
        out.append(s["bootstrap"].get(key) or applied.get(key) or
                   f"arn:{part}:iam::{e['account']}:role/{s['rolePrefix']}"
                   f"{bootstrap_role_name(kind, q, e, s['rolePrefix'])}")
    return out


def bootstrap_role_name(kind: str, q: str, e: dict, prefix: str) -> str:
    """`cdk bootstrap`'s name (cdk-<q>-<kind>-role-<account>-<region>) without the prefix; when prefix + name
    would pass IAM's 64 characters the account id is left out (the role lives in that account anyway)."""
    name = f"cdk-{q}-{kind}-role-{e['account']}-{e['region']}"
    return name if len(prefix) + len(name) <= 64 else f"cdk-{q}-{kind}-role-{e['region']}"[:64 - len(prefix)]


BOOTSTRAP_PURPOSE = {
    "deploy": ("CDK deploy role: starts CloudFormation change sets, passes the execution role",
               "LogicalId DeploymentActionRole"),
    "cfn-exec": ("CloudFormation execution role: creates every VAMS resource",
                 "LogicalId CloudFormationExecutionRole; its policy is --cloudformation-execution-policies "
                 "(use iam/cloudformation/vra-cdk-execution-policy.yaml)"),
    "lookup": ("CDK lookup role: read-only context lookups at synth", "LogicalId LookupRole"),
    "file-publishing": ("CDK file-asset publishing role: uploads Lambda code and nested templates to the "
                        "staging bucket", "LogicalId FilePublishingRole"),
    "image-publishing": ("CDK image-asset publishing role: pushes container images to the staging ECR "
                         "repository", "LogicalId ImagePublishingRole"),
}
_BOOT_LIDS = {"deploy": "DeploymentActionRole", "cfn-exec": "CloudFormationExecutionRole", "lookup": "LookupRole",
              "file-publishing": "FilePublishingRole", "image-publishing": "ImagePublishingRole"}


def extract_bootstrap_roles(t: dict) -> dict:
    """Role trust + policies of the CDK bootstrap template, with its intrinsics resolved to the plain form
    `it-roles.yaml` uses (staging bucket / repository / execution role by their default names)."""
    res = t.get("Resources") or {}
    version = ((res.get("CdkBootstrapVersion") or {}).get("Properties") or {}).get("Value")
    pol_of: dict[str, list] = {}
    for lid, r in res.items():
        if r.get("Type") == "AWS::IAM::Policy":
            for role in (r.get("Properties") or {}).get("Roles") or []:
                pol_of.setdefault((role or {}).get("Ref"), []).append(r["Properties"]["PolicyDocument"])
    roles = {}
    for kind, lid in _BOOT_LIDS.items():
        props = (res.get(lid) or {}).get("Properties") or {}
        docs = [p["PolicyDocument"] for p in props.get("Policies") or []] + pol_of.get(lid, [])
        stmts = [st for d in docs for st in _list((d or {}).get("Statement"))]
        trust = _simplify(props.get("AssumeRolePolicyDocument", {}).get("Statement") or [])
        mp = props.get("ManagedPolicyArns")
        managed = [_simplify(x) for x in mp] if isinstance(mp, list) else []
        what, note = BOOTSTRAP_PURPOSE[kind]
        roles[kind] = {"purpose": what, "trust": trust, "trustPrincipals": _principals(trust),
                       "policy": {"Version": "2012-10-17", "Statement": _simplify(stmts)},
                       "managedPolicyArns": managed, "notes": [note]}
    return {"source": f"aws-cdk bootstrap template version {version}", "bootstrapVersion": version, "roles": roles}


_SIMPLE = {"${StagingBucket.Arn}": "arn:${AWS::Partition}:s3:::${FileAssetsBucketName}",
           "${ContainerAssetsRepository.Arn}": "arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}:"
                                                "repository/${ImageAssetsRepositoryName}",
           "${CloudFormationExecutionRole.Arn}": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/"
                                                 "${RolePrefix}cdk-${Qualifier}-cfn-exec-role-${AWS::AccountId}-"
                                                 "${AWS::Region}",   # = _CFN_EXEC_REF, renamed in it-roles.yaml
           "${FileAssetsBucketEncryptionKey.Arn}": "${FileAssetsKmsKeyArn}",
           "parameter${CdkBootstrapVersion}": "parameter/cdk-bootstrap/${Qualifier}/version"}
_CFN_EXEC_REF = "${RolePrefix}cdk-${Qualifier}-cfn-exec-role-${AWS::AccountId}-${AWS::Region}"
_IF_DEFAULTS = {"ShouldDenyExternalId": False, "HasTrustedAccounts": False, "HasTrustedAccountsForLookup": False,
                "CreateNewKey": True, "HasCloudFormationExecutionPolicies": True, "PermissionsBoundarySet": False}


def _simplify(v: Any) -> Any:
    """Resolve the bootstrap template's conditions to the defaults IT starts from (no trusted accounts, no
    external id, a new staging key) and rewrite its resource references to names (see ``_SIMPLE``)."""
    if isinstance(v, list):
        out = [_simplify(x) for x in v]
        return [x for x in out if x != {"Ref": "AWS::NoValue"}]
    if isinstance(v, dict):
        if "Fn::If" in v:
            cond, yes, no = v["Fn::If"]
            return _simplify(yes if _IF_DEFAULTS.get(cond, False) else no)
        if "Fn::Sub" in v and isinstance(v["Fn::Sub"], str):
            s = v["Fn::Sub"]
            for a, b in _SIMPLE.items():
                s = s.replace(a, b)
            return {"Fn::Sub": s}
        if v.get("Ref") == "AWS::AccountId":
            return {"Ref": "AWS::AccountId"}
        out = {k: _simplify(x) for k, x in v.items()}
        return {k: x for k, x in out.items() if x != {"Ref": "AWS::NoValue"}}
    return v


# ================================================================ package
def package_root(ctx, env: str) -> Path:
    return ctx.state_dir / "iam" / env


def pull_state(ctx, env: str) -> None:
    """``stateStore.type s3``: bring ``<prefix>/iam/<env>/`` (packages, responses, applied.json) to this machine
    once per process, so a CI job or another operator sees the package another machine planned or applied."""
    store = getattr(ctx, "store", None)
    if store is None:
        return
    pulled = ctx.__dict__.setdefault("_iam_pulled", set())
    if env in pulled:
        return
    pulled.add(env)
    store.pull_prefix(f"iam/{env}/")


def package_numbers(ctx, env: str) -> list[int]:
    pull_state(ctx, env)
    d = package_root(ctx, env)
    if not d.exists():
        return []
    return sorted(int(p.name) for p in d.iterdir() if p.is_dir() and p.name.isdigit() and (p / "package.json").exists())


def load_package(ctx, env: str, n: int | None = None) -> dict | None:
    nums = package_numbers(ctx, env)
    if not nums:
        return None
    n = nums[-1] if n is None else n
    if n not in nums:
        raise UsageError(f"no IAM package {n} for {env} (have {nums})")
    d = package_root(ctx, env) / str(n)
    pkg = util.read_json(d / "package.json")
    pkg["roles"] = util.read_json(d / "roles.json")
    pkg["dir"] = str(d)
    return pkg


def applied_record(ctx, env: str) -> dict | None:
    pull_state(ctx, env)
    p = package_root(ctx, env) / "applied.json"
    return util.read_json(p) if p.exists() else None


def env_info(ctx, env: str, asm: Assembly | None = None) -> dict:
    from vra.ledger import partition_for_region
    e = ctx.ledger.env(env)
    part = e.get("partition") or partition_for_region(e["region"]) or "aws"   # no AWS call
    stack = None
    if asm is not None and asm.report:
        first = next(iter(asm.report.get("roles") or []), None)
        stack = first["roleConstructPath"].split("/")[0] if first else None
    if not stack:
        try:
            stack = ctx.ledger.core_stack_name(env)
        except Exception:  # noqa: BLE001
            stack = "*"
    s = settings(e)
    return {"partition": part, "region": e["region"], "account": str(e["account"]), "coreStack": stack,
            "rolePattern": f"{s['rolePrefix']}{name_base(e, env)}-*", "precreated": "precreated" in s["modes"],
            "rolePrefix": s["rolePrefix"], "nameBase": name_base(e, env)}


def core_role_name_stem(config_name: str) -> str:
    """The start every core-stack role name has without a role-name prefix.

    VAMS's IamRoleTransform aspect (infra/lib/aspects/iam-role-transform.aspect.ts, getRoleName) names each role
    ``<prefix><cdk.Names.uniqueResourceName(role, maxLength 40 - prefix length)>-<region>``. uniqueResourceName
    drops every non-alphanumeric character of the construct path, which starts with the core stack id
    ``<configName>-core-<baseStackName>``, and for a long path keeps its first 16 characters (then the last 16 and
    a hash). So with no prefix every core role name starts with the first 16 characters of
    ``<configName without non-alphanumerics>core<baseStackName...>``. The stem stops after ``core`` (or at 16),
    so environments with different base stack names in one account share it."""
    return (re.sub(r"[^A-Za-z0-9]", "", str(config_name)) + "core")[:16]


def execution_policy_parameters(e: dict, env: str, s: dict | None = None) -> dict:
    """The ledger's values for iam/cloudformation/vra-cdk-execution-policy.yaml, which confines role changes and
    iam:PassRole to VAMS's role names: ``ConfigName`` (the env's configName; CloudFormation names of roles in the
    other VAMS stacks start with ``<ConfigName>-``), ``CoreRoleNameStem`` (core-stack role names without a prefix)
    and ``RoleNamePrefix`` (iam.rolePrefix, required in precreated mode)."""
    s = s or settings(e)
    if "precreated" in s["modes"] and not s["rolePrefix"]:
        raise UsageError(f"envs/{env}: {PRECREATED_PREFIX_REQUIRED}")
    name = str(e.get("configName") or "vams")
    return {"ConfigName": name, "CoreRoleNameStem": core_role_name_stem(name), "RoleNamePrefix": s["rolePrefix"]}


def name_base(e: dict, env: str) -> str:
    """First part of every suggested VAMS role name (after the prefix): the env's baseStackName."""
    return re.sub(r"[^A-Za-z0-9-]", "", str(e.get("baseStackName") or env))[:12] or env


def role_set_digest(roles: list[dict]) -> str:
    return util.sha256_bytes("\n".join(sorted(r["constructPath"] for r in roles)).encode())


def policy_digest(roles: list[dict]) -> str:
    return util.sha256_bytes("\n".join(sorted(f"{r['constructPath']}={r['policySha256']}" for r in roles)).encode())


def suggested_name(row: dict, prefix: str, base: str) -> str:
    h = hashlib.sha256(row["constructPath"].encode()).hexdigest()[:6]
    seg = row["id"].rsplit("-", 1)[0]
    budget = 64 - len(prefix) - len(base) - len(h) - 2
    return f"{base}-{seg[:max(1, budget)]}-{h}"


def plan(ctx, env: str, asm: Assembly, modes: list[str] | None = None, source: dict | None = None,
         bootstrap_template: str | None = None) -> dict:
    """Write IT package n+1 for ``env`` from a synthesized assembly. Returns the package summary."""
    e = ctx.ledger.env(env)
    s = settings(e)
    if modes:
        s["modes"] = parse_modes(modes)
    if not needs_package(s):
        raise UsageError(f"envs/{env}: iam.mode is standard (VAMS creates its own roles); set iam.mode (boundary, "
                         f"precreated, bootstrap) or pass --mode")
    if "precreated" in s["modes"] and not s["rolePrefix"]:
        raise UsageError(f"envs/{env}: {PRECREATED_PREFIX_REQUIRED}; set it in envs/{env}/env.yaml")
    info = env_info(ctx, env, asm)
    info["precreated"] = "precreated" in s["modes"]     # --mode may differ from env.yaml
    roles, refs = role_rows(asm, info)
    base = name_base(e, env)
    for r in roles:
        r["suggestedRoleName"] = s["rolePrefix"] + suggested_name(r, s["rolePrefix"], base)
    boot = bootstrap_roles(ctx, env, s, bootstrap_template)
    vra_side = vra_roles(ctx, env, s)
    nums = package_numbers(ctx, env)
    n = (nums[-1] + 1) if nums else 1
    prev = load_package(ctx, env) if nums else None
    d = package_root(ctx, env) / str(n)
    meta = {"env": env, "createdBy": ctx.initiator, "modes": s["modes"], "account": info["account"],
            "region": info["region"], "partition": info["partition"], "coreStack": info["coreStack"],
            "source": source or {}, "gated": bool(e.get("requiresApproval"))}
    res = render_package(d, n, prev, meta, roles, refs, boot, vra_side, s, asm.report)
    _publish(ctx, d, f"iam plan {res['packageId']}", create_only=True)
    return res


def render_package(d: Path, n: int, prev: dict | None, meta: dict, roles: list[dict], refs: list[dict],
                   boot: list[dict], vra_side: list[dict], s: dict, report: dict | None) -> dict:
    """Write package ``n`` into ``d`` (IT-ROLES.md, roles.json, policies/, it-roles.yaml, roles.csv, delta.json,
    it-response.template.yaml, package.json). ``meta`` carries env / account / region / partition / source."""
    if d.exists():
        shutil.rmtree(d)
    (d / "policies").mkdir(parents=True)
    pkg_id = f"{meta['env']}-{n}"
    delta = compute_delta(prev, roles, boot)
    meta = {"schemaVersion": 1, "packageId": pkg_id, "n": n, "createdAt": util.now_iso(), **meta,
            "modes": s["modes"], "boundaryArn": s["boundaryArn"], "rolePrefix": s["rolePrefix"],
            "cliCredentialsSynthesizer": s["cliCredentialsSynthesizer"],
            "previous": prev["packageId"] if prev else None,
            "counts": {"vamsRoles": len(roles), "references": len(refs), "bootstrapRoles": len(boot),
                       "vraRoles": len(vra_side),
                       "stackScopedResources": sum(1 for r in roles for u in r["unresolvedResources"]
                                                   if u.get("scope") == "core stack name"),
                       "serviceScopedResources": sum(1 for r in roles for u in r["unresolvedResources"]
                                                     if u.get("scope") != "core stack name"),
                       "pendingTrust": sum(1 for r in roles if r["trustPending"]),
                       "deployTime": sum(1 for r in roles if r["phase"] == "deploy"),
                       "runTime": sum(1 for r in roles if r["phase"] == "runtime")},
            "roleSetDigest": role_set_digest(roles), "policyDigest": policy_digest(roles), "delta": delta["summary"]}
    for r in roles:
        util.write_json(d / "policies" / f"{r['id']}.json", r["policy"])
    for b in boot:
        util.write_json(d / "policies" / f"{b['id']}.json", b["policy"])
    for v in vra_side:
        if v["statements"]:
            util.write_json(d / "policies" / f"{v['id']}.json",
                            {"Version": "2012-10-17", "Statement": [
                                {k: x for k, x in st.items() if k in ("Sid", "Effect", "Action", "Resource")}
                                for st in v["statements"]]})
    util.write_json(d / "roles.json", {"packageId": pkg_id, "roles": roles, "references": refs,
                                       "bootstrapRoles": boot, "vraRoles": vra_side})
    util.write_json(d / "delta.json", delta)
    if report is not None:
        util.write_json(d / REPORT, report)
    files = ["IT-ROLES.md", "roles.json", "roles.csv", "delta.json", "it-response.template.yaml"]
    template = it_roles_template(meta, roles, boot, s)
    if template["Resources"]:
        util.atomic_write(d / "it-roles.yaml", "# Generated by `vra iam plan`. Deploying it is optional: IT may "
                          "create the roles its own way.\n" + util.dump_yaml(template, width=120))
        files.append("it-roles.yaml")
    if "boundary" in s["modes"]:
        util.write_json(d / "boundary-actions.json", boundary_actions(roles))
        files.append("boundary-actions.json")
    util.atomic_write(d / "roles.csv", roles_csv(roles, boot, vra_side))
    util.atomic_write(d / "it-response.template.yaml", response_template(meta, roles, boot, s))
    util.atomic_write(d / "IT-ROLES.md", render_markdown(meta, roles, refs, boot, vra_side, delta, s, files))
    meta["files"] = {f: util.sha256_file(d / f) for f in sorted(files)}
    util.write_json(d / "package.json", meta)
    return {"packageId": pkg_id, "n": n, "dir": str(d), "itRoles": str(d / "IT-ROLES.md"), "counts": meta["counts"],
            "modes": s["modes"], "delta": delta["summary"], "deltaDetail": delta,
            "roleQuota": role_quota_summary(s, roles), "files": sorted(files + ["package.json"])}


def role_quota_summary(s: dict, roles: list[dict]) -> dict:
    """What the env's VAMS stacks need from the account's IAM role quota. Pre-created roles: none (IT
    creates them). Otherwise the stacks create every role, and switching a deployed stack to a boundary or role
    prefix replaces them all, so the account briefly holds both sets."""
    if "precreated" in s["modes"]:
        return {"stackRoles": 0, "note": "IT pre-creates the VAMS roles: the stacks create none"}
    n = len(roles)
    return {"stackRoles": n, "note": f"the VAMS stacks create {n} role(s); a permission-boundary or role-prefix switch "
                                     f"on a deployed stack replaces all {n}, so the account needs {n} free roles plus "
                                     f"headroom (`vra deploy` checks iam:GetAccountSummary before cdk deploy)"}


def write_package(roles: list[dict], out_root: Path, *, scope: str, title: str, boundary_arn: str | None = None,
                  name_prefix: str | None = None, purposes: dict[str, str] | None = None,
                  phases: dict[str, str] | None = None, unresolved: list[str] | None = None) -> dict:
    """The IT package writer for other role sets (`vra pipeline plan`: the pipeline stack's roles). ``roles`` are
    iam-policy-report entries; ``(PARTITION)`` / ``(ACCOUNT)`` / ``(REGION)`` become the CloudFormation pseudo
    parameters of the account the template is deployed in. Writes ``out_root/<n>/`` with the same files as an env
    package and returns ``{dir, n, roles, diff: {added, removed, changed}, files, packageId}``."""
    out_root = Path(out_root)
    nums = sorted(int(p.name) for p in out_root.iterdir() if p.is_dir() and p.name.isdigit()
                  and (p / "package.json").exists()) if out_root.exists() else []
    prev = None
    if nums:
        pd = out_root / str(nums[-1])
        prev = {**util.read_json(pd / "package.json"), "roles": util.read_json(pd / "roles.json"), "dir": str(pd)}
    n = (nums[-1] + 1) if nums else 1
    info = {"partition": "${AWS::Partition}", "region": "${AWS::Region}", "account": "${AWS::AccountId}",
            "coreStack": scope, "rolePattern": f"{name_prefix or ''}*"}
    asm = Assembly({"roles": roles}, {}, None, title)
    rows, refs = role_rows(asm, info)
    try:   # the pipeline's own naming (vra.pipeline_iam.proposed_name), else the env package's
        from vra.pipeline_iam import proposed_name
    except ImportError:  # pragma: no cover - the pipeline module ships with the engine
        proposed_name = None
    for r in rows:
        r["kind"] = scope
        given = next((x.get("roleName") for x in roles if x.get("roleConstructPath") == r["constructPath"]), None)
        if given and given != "missing role":
            r["suggestedRoleName"] = given
        elif proposed_name is not None:
            r["suggestedRoleName"] = proposed_name(r["constructPath"], name_prefix or "")
        else:
            r["suggestedRoleName"] = (name_prefix or "") + suggested_name(r, name_prefix or "", scope[:12])
        r["id"] = r["suggestedRoleName"]       # policies/<role name>.json
        if purposes and purposes.get(r["constructPath"]):
            r["purpose"] = purposes[r["constructPath"]]
        if phases and phases.get(r["constructPath"]):
            r["phase"] = phases[r["constructPath"]]
    s = {"modes": ["precreated"], "boundaryArn": boundary_arn, "rolePrefix": name_prefix or "",
         "cliCredentialsSynthesizer": False, "bootstrap": {}}
    meta = {"env": scope, "title": title, "createdBy": None, "account": "(the account it-roles.yaml is deployed in)",
            "region": "(its region)", "partition": "(its partition)", "coreStack": scope,
            "source": {"report": title}, "gated": False, "extraUnresolved": list(unresolved or [])}
    res = render_package(out_root / str(n), n, prev, meta, rows, refs, [], [], s, {"roles": roles})
    dd = res["deltaDetail"]
    diff = {"added": dd["added"], "removed": dd["removed"], "changed": [c["constructPath"] for c in dd["changed"]]}
    d = Path(res["dir"])
    # the 0.5.0 pipeline package files (vra.pipeline_iam): diff.json and the caller's unresolved references
    util.write_json(d / "diff.json", {"previous": str(nums[-1]) if nums else None, **diff})
    rj = util.read_json(d / "roles.json")
    rj["unresolved"] = list(unresolved or [])
    util.write_json(d / "roles.json", rj)
    return {"dir": res["dir"], "n": n, "roles": len(rows), "packageId": res["packageId"],
            "files": sorted(res["files"] + ["diff.json"]), "diff": diff, "writer": "vra.iam"}


def compute_delta(prev: dict | None, roles: list[dict], boot: list[dict]) -> dict:
    if prev is None:
        return {"summary": {"against": None, "added": len(roles), "removed": 0, "changed": 0},
                "added": [r["constructPath"] for r in roles], "removed": [], "changed": []}
    old = {r["constructPath"]: r for r in prev["roles"]["roles"]}
    new = {r["constructPath"]: r for r in roles}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = []
    for p in sorted(set(new) & set(old)):
        if new[p]["policySha256"] != old[p]["policySha256"]:
            changed.append({"constructPath": p, "addedActions": sorted(set(new[p]["actions"]) - set(old[p]["actions"])),
                            "removedActions": sorted(set(old[p]["actions"]) - set(new[p]["actions"])),
                            "trustChanged": new[p]["trust"] != old[p]["trust"]})
    old_boot = {b["id"]: b for b in prev["roles"].get("bootstrapRoles") or []}
    boot_changed = sorted(b["id"] for b in boot if b["id"] not in old_boot or old_boot[b["id"]]["policy"] != b["policy"])
    return {"summary": {"against": prev["packageId"], "added": len(added), "removed": len(removed),
                        "changed": len(changed), "bootstrapChanged": len(boot_changed)},
            "added": added, "removed": removed, "changed": changed, "bootstrapChanged": boot_changed}


def boundary_actions(roles: list[dict]) -> dict:
    """What a permission boundary must allow for the roles VAMS creates (union of their actions)."""
    acts = sorted({a for r in roles for a in r["actions"]})
    managed = sorted({m for r in roles for m in r["managedPolicyArns"]})
    return {"comment": "Union of the actions of every role VAMS creates in this env. A permission boundary that "
                       "denies any of them breaks the role that needs it. Managed policies attached by VAMS are "
                       "listed separately; the boundary must allow their actions too.",
            "managedPolicyArns": managed,
            "policy": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": acts, "Resource": "*"}]}}


def _cfn_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s.title())[:200] or "Role"


def _chunks(stmts: list[dict], limit: int) -> list[list[dict]]:
    out: list[list[dict]] = [[]]
    for st in stmts:
        cur = out[-1]
        if cur and len(json.dumps({"Version": "2012-10-17", "Statement": cur + [st]}, separators=(",", ":"))) > limit:
            out.append([st])
        else:
            cur.append(st)
    return [c for c in out if c]


def it_roles_template(meta: dict, roles: list[dict], boot: list[dict], s: dict) -> dict:
    """CloudFormation IT may deploy: every role of the package with the boundary and name prefix as parameters.
    Literal account / region / partition (the package is for one env). Trust conditions that name a VAMS
    resource created later are parameters (``PENDING`` matches nothing until IT sets them)."""
    params: dict[str, Any] = {
        "RolePrefix": {"Type": "String", "Default": s["rolePrefix"], "AllowedPattern": r"^[\w+=,.@-]{0,32}$",
                       "Description": "Prefix of every role name (env.yaml iam.rolePrefix)"},
        "PermissionsBoundaryArn": {"Type": "String", "Default": s["boundaryArn"] or "",
                                   "Description": "Permission boundary of every role; empty = none"}}
    conds: dict[str, Any] = {"HasBoundary": {"Fn::Not": [{"Fn::Equals": [{"Ref": "PermissionsBoundaryArn"}, ""]}]}}
    resources: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    boundary = {"Fn::If": ["HasBoundary", {"Ref": "PermissionsBoundaryArn"}, {"Ref": "AWS::NoValue"}]}
    pending_params: dict[str, str] = {}
    if "precreated" in s["modes"]:
        for r in roles:
            lid = "Role" + _cfn_id(r["id"])
            name = r["suggestedRoleName"]
            prefixed = name.startswith(s["rolePrefix"])
            suffix = name[len(s["rolePrefix"]):] if prefixed else name
            trust = json.loads(json.dumps(r["trust"]))
            for pend in r["trustPending"]:
                pname = "Trust" + _cfn_id(str(pend["reference"]))[:60]
                pending_params[pname] = str(pend["reference"])
                trust = _replace_pending(trust, {"Ref": pname})
            props: dict[str, Any] = {"RoleName": {"Fn::Sub": "${RolePrefix}" + suffix} if prefixed else name,
                                     "Description": f"VAMS {r['purpose']}"[:1000],
                                     "AssumeRolePolicyDocument": {"Version": "2012-10-17",
                                                                  "Statement": _subify(trust)},
                                     "PermissionsBoundary": boundary,
                                     "Tags": [{"Key": "vra:constructPath", "Value": r["constructPath"][-256:]}]}
            managed = _subify(list(r["managedPolicyArns"]))
            stmts = r["policy"]["Statement"]
            if stmts and len(json.dumps(r["policy"], separators=(",", ":"))) <= INLINE_LIMIT:
                props["Policies"] = [{"PolicyName": "vams", "PolicyDocument": _subify(r["policy"])}]
            elif stmts:
                for i, chunk in enumerate(_chunks(stmts, MANAGED_LIMIT), 1):
                    mlid = f"{lid}Policy{i}"
                    resources[mlid] = {"Type": "AWS::IAM::ManagedPolicy", "Properties": {
                        "Description": f"VAMS {r['id']} part {i}",
                        "PolicyDocument": {"Version": "2012-10-17", "Statement": _subify(chunk)}}}
                    managed.append({"Ref": mlid})
            if managed:
                props["ManagedPolicyArns"] = managed
            resources[lid] = {"Type": "AWS::IAM::Role", "Properties": props}
            outputs[lid] = {"Description": r["constructPath"][-1000:], "Value": {"Ref": lid}}
    for b in boot:
        lid = "Bootstrap" + _cfn_id(b["id"])
        # `cdk bootstrap`'s names behind the prefix (bootstrap_role_name); the deploy role passes the execution
        # role by that same name
        cfn_exec = next((x["defaultName"] for x in boot if x["id"] == "cdk-cfn-exec"), "")
        policy = json.loads(json.dumps(b["policy"]).replace(_CFN_EXEC_REF, "${RolePrefix}" + cfn_exec))
        props = {"RoleName": {"Fn::Sub": "${RolePrefix}" + b["defaultName"]},
                 "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": b["trust"]},
                 "PermissionsBoundary": boundary}
        if policy["Statement"]:
            props["Policies"] = [{"PolicyName": "default", "PolicyDocument": policy}]
        managed = list(b["managedPolicyArns"])
        if b["id"] == "cdk-cfn-exec":
            managed = [{"Ref": "CloudFormationExecutionPolicyArn"}]
            params["CloudFormationExecutionPolicyArn"] = {
                "Type": "String", "Description": "Policy CloudFormation creates VAMS with (deploy "
                "iam/cloudformation/vra-cdk-execution-policy.yaml first and pass its policy ARN)"}
        if managed:
            props["ManagedPolicyArns"] = managed
        resources[lid] = {"Type": "AWS::IAM::Role", "Properties": props}
        outputs[lid] = {"Description": b["bootstrapKey"], "Value": {"Fn::GetAtt": [lid, "Arn"]}}
    if boot:
        q = boot[0]["qualifier"]
        params["Qualifier"] = {"Type": "String", "Default": q, "AllowedPattern": "^[A-Za-z0-9_-]{1,10}$"}
        params["FileAssetsBucketName"] = {"Type": "String", "Default": s["bootstrap"].get("fileAssetsBucketName") or
                                          f"cdk-{q}-assets-{meta['account']}-{meta['region']}"}
        params["ImageAssetsRepositoryName"] = {"Type": "String", "Default": s["bootstrap"].get(
            "imageAssetsRepositoryName") or f"cdk-{q}-container-assets-{meta['account']}-{meta['region']}"}
        params["FileAssetsKmsKeyArn"] = {"Type": "String", "Description": "KMS key of the staging bucket "
                                         "(the bootstrap stack's FileAssetsBucketEncryptionKey)"}
    for pname, ref in sorted(pending_params.items()):
        params[pname] = {"Type": "String", "Default": PENDING,
                         "Description": f"Value of {ref} after the first VAMS deploy (until then the trust matches "
                                        f"nothing)"}
    return {"AWSTemplateFormatVersion": "2010-09-09",
            "Description": f"IT-created IAM roles for VAMS env {meta['env']} ({meta['packageId']}, "
                           f"{meta['account']} {meta['region']})",
            "Parameters": params, "Conditions": conds, "Resources": resources, "Outputs": outputs}


def _replace_pending(v: Any, repl: Any) -> Any:
    if v == PENDING:
        return repl
    if isinstance(v, dict):
        return {k: _replace_pending(x, repl) for k, x in v.items()}
    if isinstance(v, list):
        return [_replace_pending(x, repl) for x in v]
    return v


def roles_csv(roles: list[dict], boot: list[dict], vra_side: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["kind", "id", "suggested_role_name", "construct_path", "purpose", "feature", "config_option",
                "phase", "trust_principals", "managed_policies", "actions", "unresolved_resources", "trust_pending",
                "policy_file"])
    for r in roles:
        w.writerow([r["kind"], r["id"], r["suggestedRoleName"], r["constructPath"], r["purpose"], r["feature"],
                    r["configOption"] or "", r["phase"], " ".join(r["trustPrincipals"]),
                    " ".join(r["managedPolicyArns"]), len(r["actions"]), len(r["unresolvedResources"]),
                    "yes" if r["trustPending"] else "", f"policies/{r['id']}.json"])
    for b in boot:
        w.writerow([b["kind"], b["id"], b["defaultName"], "", b["purpose"], "CDK bootstrap",
                    f"iamRoleConfig.json bootstrap.{b['bootstrapKey']}", "deploy", " ".join(b["trustPrincipals"]),
                    "", "", "", "", f"policies/{b['id']}.json"])
    for v in vra_side:
        w.writerow([v["kind"], v["id"], v["name"], "", v["purpose"], "vra", v["template"], v["phase"],
                    " ".join(v["trustPrincipals"]), "", sum(len(_list(st["Action"])) for st in v["statements"]),
                    "", "", f"policies/{v['id']}.json" if v["statements"] else ""])
    return buf.getvalue()


def response_template(meta: dict, roles: list[dict], boot: list[dict], s: dict) -> str:
    """What IT fills in and returns (`vra iam apply --from`). Prefilled with the names `it-roles.yaml` uses."""
    doc: dict[str, Any] = {"package": meta["packageId"]}
    if "boundary" in s["modes"] or s["boundaryArn"]:
        doc["boundaryArn"] = s["boundaryArn"] or f"arn:{meta['partition']}:iam::{meta['account']}:policy/<boundary>"
    if "boundary" in s["modes"]:
        doc["rolePrefix"] = s["rolePrefix"]
    if "precreated" in s["modes"]:
        doc["roles"] = {r["constructPath"]: r["suggestedRoleName"] for r in roles}
    if "bootstrap" in s["modes"]:
        bs: dict[str, Any] = {"qualifier": (boot[0]["qualifier"] if boot else
                                            s["bootstrap"].get("qualifier") or DEFAULT_QUALIFIER),
                              "cliCredentialsSynthesizer": s["cliCredentialsSynthesizer"]}
        for b in boot:
            bs[b["bootstrapKey"]] = b["arn"] or (f"arn:{meta['partition']}:iam::{meta['account']}:role/"
                                                 f"{s['rolePrefix']}{b['defaultName']}")
        for k in ("fileAssetsBucketName", "imageAssetsRepositoryName"):
            if s["bootstrap"].get(k):
                bs[k] = s["bootstrap"][k]
        doc["bootstrap"] = bs
    head = (f"# IT response for {meta['packageId']} (env {meta['env']}, account {meta['account']}, "
            f"{meta['region']}).\n# Fill in what IT created, then a person runs:\n"
            f"#   vra iam apply --env {meta['env']} --from <this file>\n"
            "# roles: construct path -> role NAME or ARN (VAMS precreatedRoles takes names; ARNs are checked "
            "and reduced to the name).\n")
    return head + util.dump_yaml(doc, width=200)


def render_markdown(meta: dict, roles: list[dict], refs: list[dict], boot: list[dict], vra_side: list[dict],
                    delta: dict, s: dict, files: list[str]) -> str:
    c = meta["counts"]
    modes = " + ".join(s["modes"])
    title = meta.get("title") or f"IAM roles for VAMS env `{meta['env']}`"
    lines = [f"# {title} ({meta['packageId']})", "",
             f"- Account / region / partition: `{meta['account']}` / `{meta['region']}` / `{meta['partition']}`",
             f"- IAM mode: **{modes}**", f"- Stack: `{meta['coreStack']}`",
             f"- Source: {_source_line(meta['source'])}",
             f"- Created: {meta['createdAt']} by {meta.get('createdBy') or 'vra'}", ""]
    lines += ["## What IT is asked to do", ""]
    if meta.get("title"):
        lines.append(f"- Create the **{c['vamsRoles']}** roles in the table below (or deploy `it-roles.yaml`) and "
                     f"return their names.")
    elif "precreated" in s["modes"]:
        lines.append(f"- Create the **{c['vamsRoles']}** roles in the table below (VAMS creates none of them: "
                     f"`app.iamRoleConfig.useCustomVamsStackRoles` with `precreatedRoles`). `it-roles.yaml` "
                     f"creates them all; one policy file per role is in `policies/`.")
    if "boundary" in s["modes"]:
        lines.append(f"- Provide the permission boundary (and role name prefix) VAMS applies to the **{c['vamsRoles']}"
                     f"** roles it creates (`environments.aws` in `infra/cdk.json`). The boundary must allow the "
                     f"actions in `boundary-actions.json`.")
    if "bootstrap" in s["modes"] and not s["cliCredentialsSynthesizer"]:
        lines.append(f"- Create the **{len(boot)}** CDK bootstrap roles (`useCustomBootstrapRoles`); the staging "
                     f"bucket and ECR repository must exist (a standard `cdk bootstrap` creates them).")
    if "bootstrap" in s["modes"] and s["cliCredentialsSynthesizer"]:
        lines.append("- No CDK bootstrap roles (`useCliCredentialsSynthesizer`): the deploy role deploys with its "
                     "own credentials and needs the CDK execution policy itself.")
    if vra_side:
        lines.append(f"- Check the **{len(vra_side)}** roles vra itself runs as (templates in `iam/cloudformation/`).")
    lines += ["- Return the filled-in `it-response.template.yaml`. A person runs "
              f"`vra iam apply --env {meta['env']} --from <file>`" +
              (" with an approval record (subject `iam-apply:" + meta["packageId"] + ":<first 16 hex of the "
               "response's sha256>`, which `vra iam apply --dry-run` prints; optional `itTicket` in the response "
               "is recorded in history)" if meta.get("gated") else "") +
              "; the blocked run then continues with `vra resume <run-id>`.", ""]
    lines += ["## Summary", "", "| | Count |", "|---|---|",
              f"| Roles VAMS needs | {c['vamsRoles']} ({c['deployTime']} deploy time, {c['runTime']} run time) |",
              f"| Existing roles VAMS references | {c['references']} |",
              f"| CDK bootstrap roles | {c['bootstrapRoles']} |", f"| vra roles | {c['vraRoles']} |",
              f"| References scoped by the stack name (a CDK token in the report) | {c['stackScopedResources']} |",
              f"| References scoped to a whole service (narrow them) | {c['serviceScopedResources']} |",
              f"| Trust conditions to set after the first deploy | {c['pendingTrust']} |", ""]
    ds = delta["summary"]
    prev_n = (ds.get("against") or "").rsplit("-", 1)[-1]
    lines += [f"## Changes since package {prev_n}" if ds.get("against") else "## Changes since the previous package",
              ""]
    if not ds.get("against"):
        lines += ["First package for this env: review everything.", ""]
    else:
        lines += [f"Against `{ds['against']}`: {ds['added']} added, {ds['removed']} removed, {ds['changed']} changed "
                  f"role(s){', ' + str(ds.get('bootstrapChanged')) + ' bootstrap role(s) changed' if ds.get('bootstrapChanged') else ''}.", ""]
        for p in delta["added"]:
            lines.append(f"- added `{p}`")
        for p in delta["removed"]:
            lines.append(f"- removed `{p}` (IT may delete the role after the next deploy)")
        for ch in delta["changed"]:
            more = ", ".join(ch["addedActions"][:12]) + (" ..." if len(ch["addedActions"]) > 12 else "")
            lines.append(f"- changed `{ch['constructPath']}`" + (f": new actions {more}" if more else "") +
                         (" (trust changed)" if ch["trustChanged"] else ""))
        lines.append("")
    lines += ["## Roles VAMS needs", "",
              "Phase: `deploy` = used only by CloudFormation custom resources during a deploy; `runtime` = used "
              "while VAMS runs.", "",
              "| Role (suggested name) | Purpose | Construct path | Trust | Managed policies | Inline policy | "
              "Caused by | Phase |", "|---|---|---|---|---|---|---|---|"]
    for r in roles:
        inline = f"[{len(r['actions'])} actions](policies/{r['id']}.json)"
        if r["unresolvedResources"]:
            inline += f", {len(r['unresolvedResources'])} pattern-scoped"
        trust = ", ".join(f"`{p}`" for p in r["trustPrincipals"]) + (" (condition set after first deploy)"
                                                                        if r["trustPending"] else "")
        managed = ", ".join(f"`{m.rsplit('/', 1)[-1]}`" for m in r["managedPolicyArns"]) or "-"
        cause = r["feature"] + (f" (`{r['configOption']}`)" if r["configOption"] else "")
        lines.append(f"| `{r['suggestedRoleName']}` | {r['purpose']} | `{r['constructPath']}` | {trust} | {managed} | "
                     f"{inline} | {cause} | {r['phase']} |")
    if refs:
        lines += ["", "## Existing roles VAMS references", "",
                  "VAMS does not create these; they must exist in the account (service-linked roles are created by "
                  "the service on first use, or with `iam create-service-linked-role`).", "",
                  "| Role name | Referenced by |", "|---|---|"]
        for ref in refs:
            lines.append(f"| `{ref['roleName']}` | {len(ref['constructPaths'])} construct(s), e.g. "
                         f"`{ref['constructPaths'][0]}` |")
    if boot:
        lines += ["", "## CDK bootstrap roles", "", f"Modelled on {boot[0]['source']}.", "",
                  "| Role (default name) | iamRoleConfig.json key | Purpose | Trust |", "|---|---|---|---|"]
        for b in boot:
            lines.append(f"| `{b['defaultName']}` | `bootstrap.{b['bootstrapKey']}` | {b['purpose']} | "
                         f"{', '.join('`' + p + '`' for p in b['trustPrincipals'])} |")
    if vra_side:
        lines += ["", "## Roles vra runs as", "", "| Role | Template | Purpose | Policy | Notes |",
                  "|---|---|---|---|---|"]
    for v in vra_side:
        pol = f"[{sum(len(_list(st['Action'])) for st in v['statements'])} actions](policies/{v['id']}.json)" \
            if v["statements"] else "-"
        lines.append(f"| `{v['name']}` | `{v['template']}` | {v['purpose']} | {pol} | {' '.join(v['notes'])} |")
    unresolved = [(r, u) for r in roles for u in r["unresolvedResources"]]
    if unresolved:
        lines += ["", "## References the report leaves as CDK tokens", "",
                  "The report names these resources (tables or topics of another VAMS nested stack) by a CDK token. "
                  "DynamoDB tables and SNS topics are granted by the name CloudFormation gives them (the core stack "
                  "name); anything else on `arn:<partition>:<service>:<region>:<account>:*`, which IT should narrow "
                  "once the resources exist.", "", "| Role | Actions | Granted on | Scope |", "|---|---|---|---|"]
        for r, u in unresolved[:200]:
            lines.append(f"| `{r['id']}` | {', '.join(u['actions'][:6])}{' ...' if len(u['actions']) > 6 else ''} | "
                         f"`{u['grantedOn']}` | {u.get('scope', 'service')} |")
        if len(unresolved) > 200:
            lines.append(f"| ... {len(unresolved) - 200} more in `roles.json` | | | |")
    broad = broad_grants(roles)
    if broad:
        lines += ["", "## Broad grants", "",
                  "Statements on `Resource: \"*\"` or with a wildcard action, carried from VAMS as it grants them "
                  "(review them; narrowing one can break the feature that needs it).", "",
                  "| Role | Actions | Resource |", "|---|---|---|"]
        for b in broad[:200]:
            lines.append(f"| `{b['role']}` | {', '.join(b['actions'][:8])}{' ...' if len(b['actions']) > 8 else ''} "
                         f"| `{b['resource']}` |")
        if len(broad) > 200:
            lines.append(f"| ... {len(broad) - 200} more in `roles.json` | | |")
    passing = [r for r in roles if r.get("passRoleTargets")]
    if passing and "precreated" in s["modes"]:
        lines += ["", "## iam:PassRole between VAMS roles", "",
                  "These roles pass another VAMS role (by its suggested name). If IT names a role differently, "
                  "change the grant to that name too; `vra iam verify` checks it against the names IT returned.", ""]
        by = {r["constructPath"]: r["suggestedRoleName"] for r in roles}
        for r in passing:
            lines.append(f"- `{r['suggestedRoleName']}` passes " +
                         ", ".join(f"`{by.get(t, t)}`" for t in r["passRoleTargets"]))
    if meta.get("extraUnresolved"):
        lines += ["", "## References to generalize", "",
                  "These name resources the stack creates with generated names; replace them with a scoped pattern "
                  "before creating the roles:", ""] + [f"- `{u}`" for u in meta["extraUnresolved"]]
    lines += ["", "## Files", ""] + [f"- `{f}`" for f in sorted(files)] + ["- `policies/<role id>.json`", ""]
    return "\n".join(lines)


def broad_grants(roles: list[dict]) -> list[dict]:
    """Statements on ``Resource: "*"`` or with a wildcard action, one row per role and resource."""
    out = []
    for r in roles:
        for st in r["policy"]["Statement"]:
            acts = _list(st.get("Action"))
            for res in _list(st.get("Resource")):
                wild = [a for a in acts if "*" in a]
                if res == "*" or wild:
                    out.append({"role": r.get("suggestedRoleName") or r["id"], "constructPath": r["constructPath"],
                                "actions": acts if res == "*" else wild, "resource": res})
    return out


def _source_line(src: dict) -> str:
    if not src:
        return "n/a"
    bits = [f"{k} `{v}`" for k, v in src.items() if v and k in ("ref", "commit", "vamsVersion", "report")]
    return ", ".join(bits) or "n/a"


def _publish(ctx, d: Path, message: str, create_only: bool = False, files: list[Path] | None = None) -> None:
    """Share a package (create-only: a package number names one package forever) or the apply record."""
    if ctx.dry_run:
        return
    if ctx.store is not None:
        if files is not None:
            for f in files:
                if not ctx.store.push_file(f, create_only=create_only):
                    raise NeedsHuman(f"{f.name} already exists in the state store with other content; another "
                                     f"machine wrote it (pull and re-run)")
            return
        clashes = ctx.store.push_dir(d, create_only=create_only)
        if clashes:
            raise NeedsHuman(f"{d} already exists in the state store with other content ({clashes[:5]}); another "
                             f"machine planned the same package number: re-run `vra iam plan`")
    else:
        try:
            ctx.home.publish(message)
        except VraError as exc:
            util.eprint(f"warning: could not publish {d}: {exc.message}")


# ================================================================ apply
def apply(ctx, env: str, response_path: Path, approval: str | None = None, package: int | None = None,
          authorization: list[dict] | None = None) -> dict:
    """Validate IT's response against the package and write the ledger overlay. Human-only; approval-gated envs
    also need an approval record (subject ``iam-apply:<packageId>``, scope ``iam-apply``)."""
    e = ctx.ledger.env(env)
    s = settings(e)
    pkg = load_package(ctx, env, package)
    if pkg is None:
        raise UsageError(f"no IAM package for {env}; run `vra iam plan --env {env}` first")
    latest = package_numbers(ctx, env)[-1]
    if pkg["n"] != latest:
        raise UsageError(f"package {pkg['packageId']} is not the latest ({env}-{latest}); answer the latest package")
    resp = util.read_yaml(Path(response_path))
    if not isinstance(resp, dict):
        raise UsageError(f"{response_path}: the IT response must be a YAML mapping")
    modes = pkg["modes"]
    if modes != s["modes"]:
        raise UsageError(f"package {pkg['packageId']} was planned for iam mode {'+'.join(modes)}, envs/{env}/env.yaml "
                         f"says {'+'.join(s['modes'])}; run `vra iam plan --env {env}` again")
    vals, problems = validate_response(pkg, resp, e, s)
    if problems:
        raise UsageError(f"IT response {response_path} does not answer {pkg['packageId']}: " + "; ".join(problems),
                         {"problems": problems})
    writes = overlay_writes(ctx, env, vals, modes)
    resp_sha = util.sha256_file(Path(response_path))
    subject = approval_subject(pkg["packageId"], resp_sha)
    ticket = str(resp.get("itTicket") or "").strip() or None
    out = {"env": env, "packageId": pkg["packageId"], "modes": modes, "writes": sorted(writes),
           "dryRun": ctx.dry_run, "responseSha256": resp_sha, "approvalSubject": subject, "itTicket": ticket}
    if ctx.dry_run:   # shows what IT's answer changes; the approval is checked when it is applied
        out["proposed"] = {k: v for k, v in writes.items()}
        return out
    rec = None
    bound_human = bool(getattr(ctx, "iam_apply_bound_authorization", False))
    if bound_human and not approval:
        raise NeedsHuman("`vra iam apply` is human-only; pass --approval naming the record bound to this response "
                         f"(subject {subject}, scope iam-apply)", {"humanOnly": "iam-apply"})
    if e.get("requiresApproval") or approval:
        from vra import approvals
        # the approver approves this exact response (its sha256), not only the package id: another response
        # (other role names, a broader role) needs its own approval
        rec = approvals.check(ctx, env, subject, "iam-apply", approval,
                              force_required=bool(e.get("requiresApproval")) or bound_human)
    if bound_human:
        if rec is None:
            raise NeedsHuman(f"approval {approval} does not authorize iam-apply of {subject}",
                             {"humanOnly": "iam-apply"})
        authorization = [*(authorization or []), {
            "action": "iam-apply", "by": getattr(ctx, "initiator", None), "how": "approval",
            "approval": rec.get("path"), "approvalSha256": rec.get("sha256"), "approver": rec.get("approver")}]
    use = None
    if rec is not None:
        use = f"human:iam-apply:{pkg['packageId']}:{util.now_iso()}"   # "human:" claims always bind
        got = ctx.backend.consumed.claim(env, rec["sha256"], {"runId": use, "env": env, "action": "iam-apply",
                                                              "approver": rec.get("approver"), "at": util.now_iso()})
        if got.get("runId") != use:
            raise NeedsHuman(f"approval {rec.get('path')} was already used ({got.get('action')}, {got.get('runId')})")
    od = ctx.ledger.overlay_dir(env)
    for rel, content in writes.items():
        p = od / rel
        if content is None:
            p.unlink(missing_ok=True)
        else:
            util.write_json(p, content)
    root = package_root(ctx, env)
    resp_copy = root / "responses" / f"{pkg['n']}-{resp_sha[:16]}.yaml"   # one file per response (create-only)
    resp_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(response_path, resp_copy)
    if util.sha256_file(resp_copy) != resp_sha:
        raise UsageError(f"{response_path} changed while it was applied")
    applied = {"packageId": pkg["packageId"], "n": pkg["n"], "appliedAt": util.now_iso(), "by": ctx.initiator,
               "modes": modes, "boundaryArn": vals.get("boundaryArn"), "rolePrefix": vals.get("rolePrefix"),
               "roleSetDigest": pkg["roleSetDigest"], "policyDigest": pkg["policyDigest"],
               "responseSha256": resp_sha, "itTicket": ticket, "roles": vals.get("roles") or {},
               "bootstrap": vals.get("bootstrap") or {},
               "overlay": overlay_hashes(ctx, env), "approval": (rec or {}).get("path"),
               "approvalSha256": (rec or {}).get("sha256"), "authorization": authorization or []}
    applied["appliedSha256"] = applied_digest(applied)
    util.write_json(root / "applied.json", applied)
    # the create-only history record is what the gates trust: applied.json and the overlay are mutable
    # files, the record binds them to this package, response and approval
    ctx.ledger.append_history(env, {"action": "iam-apply", "result": "SUCCEEDED", "by": ctx.initiator,
                                    "runId": use, "packageId": pkg["packageId"], "n": pkg["n"],
                                    "responseSha256": resp_sha, "itTicket": ticket,
                                    "overlay": applied["overlay"], "boundaryArn": applied["boundaryArn"],
                                    "appliedSha256": applied["appliedSha256"],
                                    "approvalSubject": subject if rec is not None else None,
                                    "approval": applied["approval"],
                                    "approvalSha256": applied["approvalSha256"], "authorization": authorization or []})
    if ctx.store is not None:
        _publish(ctx, root, "", create_only=True, files=[resp_copy])     # the response: create-only
        _publish(ctx, root, "", files=[root / "applied.json"])         # the current record: replaced
    else:
        _publish(ctx, root, f"iam apply {pkg['packageId']}")
    out["applied"] = str(root / "applied.json")
    out["next"] = (f"commit and push the ledger overlay change (envs/{env}/overlay) through review; then "
                   f"`vra resume <run-id>` for a run blocked on this package, or `vra iam verify --env {env}`")
    return out


def approval_subject(package_id: str, response_sha: str) -> str:
    """Approval subject of `vra iam apply` on an approval-gated env: the package AND the exact response."""
    return f"iam-apply:{package_id}:{response_sha[:16]}"


_APPLIED_BOUND = ("packageId", "n", "modes", "boundaryArn", "rolePrefix", "roleSetDigest", "policyDigest",
                  "responseSha256", "roles", "bootstrap", "overlay", "approvalSha256")


def applied_digest(applied: dict) -> str:
    """Digest of the applied.json fields the gates act on (recorded in the create-only history record)."""
    return util.sha256_bytes(json.dumps({k: applied.get(k) for k in _APPLIED_BOUND}, sort_keys=True,
                                        default=str).encode())


def apply_record(ctx, env: str, applied: dict) -> dict | None:
    """The create-only history record of the `vra iam apply` that wrote ``applied`` (None: applied.json was not
    written by `vra iam apply`, or was edited afterwards)."""
    want = applied_digest(applied)
    for rec in reversed(ctx.ledger.history(env)):
        if rec.get("action") == "iam-apply" and rec.get("result") == "SUCCEEDED" and \
                rec.get("packageId") == applied.get("packageId") and rec.get("appliedSha256") == want and \
                rec.get("responseSha256") == applied.get("responseSha256") and \
                rec.get("overlay") == applied.get("overlay"):
            return rec
    return None


def boundary_arn(ctx, env: str, s: dict | None = None, applied: dict | None = None) -> str | None:
    """The boundary the env's roles must carry, wherever it was set: env.yaml ``iam.boundaryArn``, else what IT
    returned (applied.json), else the applied overlay (``infra/cdk.json.vra-merge.json``)."""
    s = s or settings(ctx.ledger.env(env))
    if s["boundaryArn"]:
        return s["boundaryArn"]
    applied = applied if applied is not None else (applied_record(ctx, env) or {})
    if applied.get("boundaryArn"):
        return applied["boundaryArn"]
    p = ctx.ledger.overlay_dir(env) / CDK_JSON_MERGE
    if p.exists():
        try:
            got = ((util.read_json(p).get("context") or {}).get("environments") or {}).get("aws", {}) \
                .get("PermissionBoundaryArn")
        except (ValueError, AttributeError):
            got = None
        if got:
            return str(got)
    return None


def validate_response(pkg: dict, resp: dict, e: dict, s: dict) -> tuple[dict, list[str]]:
    problems: list[str] = []
    part, acct = pkg["partition"], pkg["account"]
    if str(resp.get("package") or "") != pkg["packageId"]:
        problems.append(f"package is {resp.get('package')!r}, expected {pkg['packageId']!r}")
    vals: dict[str, Any] = {}
    modes = pkg["modes"]
    if "boundary" in modes:
        b = resp.get("boundaryArn") or s["boundaryArn"]
        if not b:
            problems.append("boundaryArn is required in boundary mode")
        else:
            problems += arn_problems(b, "policy", part, acct)
            if s["boundaryArn"] and b != s["boundaryArn"]:
                problems.append(f"boundaryArn {b} differs from envs/{e['name']}/env.yaml iam.boundaryArn "
                                f"{s['boundaryArn']}; change env.yaml (reviewed) or the response")
        prefix = resp.get("rolePrefix") if resp.get("rolePrefix") is not None else s["rolePrefix"]
        if s["rolePrefix"] and prefix != s["rolePrefix"]:
            problems.append(f"rolePrefix {prefix!r} differs from env.yaml iam.rolePrefix {s['rolePrefix']!r}")
        vals.update(boundaryArn=b, rolePrefix=prefix or "")
    if "precreated" in modes:
        want = {r["constructPath"] for r in pkg["roles"]["roles"]}
        got = resp.get("roles") or {}
        if not isinstance(got, dict):
            problems.append("roles must map construct path -> role name or ARN")
            got = {}
        missing = sorted(want - set(got))
        extra = sorted(set(got) - want)
        if missing:
            problems.append(f"{len(missing)} role(s) of the package have no name: " + ", ".join(missing[:5]) +
                            (" ..." if len(missing) > 5 else ""))
        if extra:
            problems.append(f"{len(extra)} construct path(s) are not in the package: " + ", ".join(extra[:5]))
        names: dict[str, str] = {}
        prefix = s["rolePrefix"]
        for path in sorted(want & set(got)):
            v = str(got[path] or "").strip()
            if v.startswith("arn:"):
                m = re.match(r"^arn:([a-z-]+):iam::(\d{12}):role/(.+)$", v)
                if not m:
                    problems.append(f"{path}: {v!r} is not a role ARN")
                    continue
                if m.group(1) != part or m.group(2) != acct:
                    problems.append(f"{path}: {v} is not in {part} account {acct}")
                if "/" in m.group(3):
                    problems.append(f"{path}: {v} has a role path; VAMS precreatedRoles take a role name and CDK "
                                    f"builds arn:...:role/<name>, so a role with a path is not found")
                v = m.group(3).rsplit("/", 1)[-1]
            if not ROLE_NAME_RE.match(v):
                problems.append(f"{path}: {v!r} is not a valid IAM role name")
            if prefix and not v.startswith(prefix):
                problems.append(f"{path}: {v!r} does not start with iam.rolePrefix {prefix!r}")
            names[path] = v
        dup = sorted({n for n in names.values() if list(names.values()).count(n) > 1})
        if dup:
            problems.append(f"role name(s) {dup} are mapped to more than one construct path (one role per "
                            f"construct keeps least privilege)")
        vals["roles"] = names
    if "bootstrap" in modes:
        bs = resp.get("bootstrap") or {}
        if not isinstance(bs, dict):
            problems.append("bootstrap must be a mapping")
            bs = {}
        cli = bool(bs.get("cliCredentialsSynthesizer", s["cliCredentialsSynthesizer"]))
        if cli != s["cliCredentialsSynthesizer"]:
            problems.append("bootstrap.cliCredentialsSynthesizer differs from env.yaml iam.cliCredentialsSynthesizer")
        q = str(bs.get("qualifier") or s["bootstrap"].get("qualifier") or DEFAULT_QUALIFIER)
        if not QUALIFIER_RE.match(q):
            problems.append(f"bootstrap.qualifier {q!r} must be 1-10 letters, digits, - or _")
        out = {"useCliCredentialsSynthesizer": cli, "qualifier": q}
        if not cli:
            for key in BOOTSTRAP_ROLES.values():
                v = bs.get(key) or s["bootstrap"].get(key)
                if not v:
                    problems.append(f"bootstrap.{key} is required (custom bootstrap roles)")
                    continue
                problems += [f"bootstrap.{key}: {p}" for p in arn_problems(v, "role", part, acct)]
                if s["bootstrap"].get(key) and v != s["bootstrap"][key]:
                    problems.append(f"bootstrap.{key} differs from env.yaml iam.bootstrap.{key}")
                out[key] = v
        for key in ("fileAssetsBucketName", "imageAssetsRepositoryName"):
            v = bs.get(key) or s["bootstrap"].get(key)
            if v:
                out[key] = v
        vals["bootstrap"] = out
    return vals, problems


def overlay_writes(ctx, env: str, vals: dict, modes: list[str]) -> dict[str, Any]:
    """Overlay file -> new JSON content (``None`` = remove a file `iam apply` wrote earlier)."""
    od = ctx.ledger.overlay_dir(env)
    cfg_p = od / CONFIG_JSON
    if not cfg_p.exists():
        raise ConfigError(f"envs/{env}/overlay/{CONFIG_JSON} is missing; `vra iam apply` sets app.iamRoleConfig in it")
    cfg = util.read_json(cfg_p)
    cfg.setdefault("app", {})["iamRoleConfig"] = {"useCustomBootstrapRoles": "bootstrap" in modes,
                                                  "useCustomVamsStackRoles": "precreated" in modes}
    writes: dict[str, Any] = {CONFIG_JSON: cfg}
    if "bootstrap" in modes or "precreated" in modes:
        boot = {"useCliCredentialsSynthesizer": False, "qualifier": "", "deployRoleArn": "",
                "cloudFormationExecutionRoleArn": "", "lookupRoleArn": "", "fileAssetPublishingRoleArn": "",
                "imageAssetPublishingRoleArn": "", "fileAssetsBucketName": "", "imageAssetsRepositoryName": ""}
        boot.update(vals.get("bootstrap") or {})
        writes[IAM_ROLE_CONFIG] = {"bootstrap": boot, "vamsStacks": {
            "generateReportOnly": False if "precreated" in modes else True,
            "precreatedRoles": dict(sorted((vals.get("roles") or {}).items()))}}
    elif (od / IAM_ROLE_CONFIG).exists():
        writes[IAM_ROLE_CONFIG] = None
    if "boundary" in modes:
        writes[CDK_JSON_MERGE] = {"context": {"environments": {"aws": {
            "PermissionBoundaryArn": vals.get("boundaryArn") or "", "IamRoleNamePrefix": vals.get("rolePrefix") or ""}}}}
    elif (od / CDK_JSON_MERGE).exists():
        writes[CDK_JSON_MERGE] = None
    return writes


def overlay_hashes(ctx, env: str) -> dict[str, str | None]:
    od = ctx.ledger.overlay_dir(env)
    return {rel: (util.sha256_file(od / rel) if (od / rel).exists() else None)
            for rel in (CONFIG_JSON, IAM_ROLE_CONFIG, CDK_JSON_MERGE)}


# ================================================================ gates (preflight, build)
def gate(ctx, run, env: str, step_id: str) -> dict:
    """Before synth / at preflight: the env's IAM package exists, is applied, matches env.yaml and the overlay
    still holds what `iam apply` wrote. BLOCKED_ON_IT (IT request naming the package) otherwise."""
    e = ctx.ledger.env(env)
    s = settings(e)
    if not needs_package(s):
        return {"env": env, "modes": s["modes"], "package": None}
    pkg = load_package(ctx, env)
    applied = applied_record(ctx, env)
    if pkg is None:
        block(ctx, run, env, step_id, f"{env} uses iam.mode {'+'.join(s['modes'])} and has no IT role package yet",
              None, [f"run `vra iam plan --env {env}` (synthesizes, no AWS writes) and hand the package to IT"])
    if pkg["modes"] != s["modes"]:
        block(ctx, run, env, step_id, f"the IT package {pkg['packageId']} was planned for iam mode "
              f"{'+'.join(pkg['modes'])}; env.yaml now says {'+'.join(s['modes'])}", pkg,
              [f"run `vra iam plan --env {env}` again and hand the new package to IT"])
    if applied is None or applied.get("n") != pkg["n"]:
        why = "IT has not returned it yet" if applied is None else \
            f"the applied package is {applied.get('packageId')}"
        block(ctx, run, env, step_id, f"IT role package {pkg['packageId']} for {env} is not applied ({why})", pkg,
              [f"send {pkg['dir']}/IT-ROLES.md (and its files) to IT",
               f"a person runs `vra iam apply --env {env} --from <it-response.yaml>`", f"`vra resume {run.run_id}`"])
    hist = apply_record(ctx, env, applied)
    if hist is None:
        raise NeedsHuman(f"state/iam/{env}/applied.json ({applied.get('packageId')}) is not the record of a "
                         f"`vra iam apply` in the {env} history (written or edited by hand); a person re-runs "
                         f"`vra iam apply --env {env} --from <it-response.yaml>`", {"package": pkg["packageId"]})
    drift = {k: v for k, v in overlay_hashes(ctx, env).items() if v != (hist.get("overlay") or {}).get(k)}
    if drift:
        raise NeedsHuman(f"envs/{env}/overlay {sorted(drift)} changed after `vra iam apply` of {pkg['packageId']}; "
                         f"re-run `vra iam apply --env {env} --from <response>` (or restore the files)",
                         {"drift": sorted(drift)})
    if "boundary" in s["modes"] and s["boundaryArn"] and applied.get("boundaryArn") != s["boundaryArn"]:
        block(ctx, run, env, step_id, f"env.yaml iam.boundaryArn changed since {pkg['packageId']} was applied",
              pkg, [f"`vra iam apply --env {env} --from <response>` with the new boundary"])
    if "boundary" in s["modes"] and not boundary_arn(ctx, env, s, applied):
        raise NeedsHuman(f"{env} uses iam.mode boundary but no boundary ARN is set (env.yaml iam.boundaryArn, IT's "
                         f"response, the applied overlay); the build could not check that every role carries it")
    return {"env": env, "modes": s["modes"], "package": pkg["packageId"], "applied": applied.get("appliedAt"),
            "responseSha256": applied.get("responseSha256"), "identity": build_identity(ctx, env)}


def build_identity(ctx, env: str) -> dict:
    """What decides how the env's roles are synthesized: iam mode, the applied package and response,
    the overlay files `vra iam apply` writes. Part of the bundle reuse key and checked again by `vra deploy`."""
    s = settings(ctx.ledger.env(env))
    if not needs_package(s):
        return {"modes": s["modes"]}
    applied = applied_record(ctx, env) or {}
    return {"modes": s["modes"], "package": applied.get("packageId"), "responseSha256": applied.get("responseSha256"),
            "boundaryArn": boundary_arn(ctx, env, s, applied) if "boundary" in s["modes"] else None,
            "overlay": overlay_hashes(ctx, env)}


def _same_synthesis(built: dict, now: dict) -> bool:
    """Two build identities synthesize the same assembly: same iam modes and boundary, and every overlay file
    `vra iam apply` writes has the same hash (an absent or empty overlay record never counts as the same)."""
    if not any((built.get("overlay") or {}).values()) or not any((now.get("overlay") or {}).values()):
        return False
    return all(built.get(k) == now.get(k) for k in ("modes", "boundaryArn", "overlay"))


def deploy_gate(ctx, run, env: str, manifest: dict, step_id: str) -> dict:
    """`vra deploy` / promote: the env's IAM package gate again, and the bundle was built with the IAM state the
    env has now (a bundle synthesized before `vra iam apply` or an iam.mode change is refused)."""
    s = settings(ctx.ledger.env(env))
    built = (manifest.get("iam") or {}).get(env)
    if not needs_package(s) and (built is None or built.get("modes") == ["standard"]):
        return {"env": env, "modes": s["modes"]}
    res = gate(ctx, run, env, step_id) if needs_package(s) else {"env": env, "modes": s["modes"]}
    now = build_identity(ctx, env)
    if built != now and built is not None and _same_synthesis(built, now):
        # IT changed only role policies (the remedy a BLOCKED_ON_IT deploy asks for) and the new package
        # was applied: `gate` above proved it is applied and recorded, and the overlay `vra iam apply` writes - the
        # only IAM input of the synth - is byte-identical, so the bundle is the one this package would produce.
        return {**res, "bundleIam": "matches", "bundlePackage": built.get("package"),
                "note": f"bundle synthesized under {built.get('package')}; {now.get('package')} is applied with the "
                        f"same overlay (role names, boundary and config unchanged)"}
    if built != now:
        raise NeedsHuman(f"build {manifest.get('buildId')} was synthesized for {env} with other IAM settings "
                         f"({(built or {}).get('modes') or 'not recorded'}, package {(built or {}).get('package')}) "
                         f"than the env has now ({now['modes']}, package {now.get('package')}); rebuild "
                         f"(`vra build`) so the IAM gate checks the assembly", {"built": built, "now": now})
    return {**res, "bundleIam": "matches"}


def check_assembly(ctx, run, env: str, out: Path, step_id: str, source: dict | None = None,
                   synth_failed: bool = False, boundary_only: bool = False) -> dict:
    """After synth: the roles the build really has match the applied package, and VAMS applied the boundary.

    precreated: the iam-policy-report of the build (always written with useCustomVamsStackRoles) names every role
    and its policy; a new or changed role writes the next package from it and stops (exit 10), also when synth
    failed because a role is not mapped. boundary (and the CDK ``permissionsBoundaryName``): every
    ``AWS::IAM::Role`` must carry the boundary; VAMS whose role transform drops it stops with exit 20."""
    e = ctx.ledger.env(env)
    s = settings(e)
    res: dict[str, Any] = {"env": env, "modes": s["modes"]}
    if not needs_package(s) and not e.get("permissionsBoundaryName"):
        return res
    asm = Assembly.load(out)
    # the boundary wherever it ends up (env.yaml, IT's response, the applied overlay): the #431 check must never
    # be skipped because env.yaml left iam.boundaryArn blank
    want_boundary = boundary_arn(ctx, env, s) if "boundary" in s["modes"] else None
    if "boundary" in s["modes"] and not want_boundary and not synth_failed:
        raise NeedsHuman(f"{env} uses iam.mode boundary but no boundary ARN is known (env.yaml iam.boundaryArn, the "
                         f"applied IT response, envs/{env}/overlay/{CDK_JSON_MERGE}); the build cannot check that "
                         f"every role carries it, so it stops. Set iam.boundaryArn or run `vra iam apply`",
                         {"rolesWithoutBoundary": None})
    cdk_boundary = e.get("permissionsBoundaryName")
    if not synth_failed and (want_boundary or cdk_boundary):
        unbounded = []
        for r in asm.template_roles():
            b = r["boundary"]
            ok = isinstance(b, str) and ((want_boundary and b == want_boundary) or
                                         (cdk_boundary and b.endswith(f":policy/{cdk_boundary}")))
            if not ok and not (isinstance(b, dict) and _boundary_intrinsic_ok(b, want_boundary, cdk_boundary)):
                unbounded.append(r["path"])
        res["rolesWithoutBoundary"] = len(unbounded)
        if unbounded:
            raise NeedsHuman(
                f"{len(unbounded)} IAM role(s) in the {env} build carry no permission boundary although "
                f"{'iam.boundaryArn' if want_boundary else 'permissionsBoundaryName'} is set (e.g. {unbounded[0]}). "
                f"VAMS's IamRoleTransform (infra/lib/aspects/iam-role-transform.aspect.ts) overrides "
                f"PermissionsBoundary with AWS::NoValue in both branches in VAMS 2.6.6 and earlier (GitHub issue "
                f"#431, fixed in VAMS 2.6.7), which also removes the CDK @aws-cdk/core:permissionsBoundary context. "
                f"Upgrade the fork to VAMS 2.6.7 or later, or add the core patch {BOUNDARY_PATCH} (CP-431) to the "
                f"ledger (copy it from the package's ledger-template/ and list it in core-patches.md), or commit the "
                f"same fix to the fork, or use iam.mode precreated; never deploy it unbounded",
                {"rolesWithoutBoundary": unbounded[:50], "corePatch": BOUNDARY_PATCH})
    if boundary_only or not needs_package(s):
        return res     # an A/B slot synth: its stack names differ from the package's construct paths
    if "bootstrap" in s["modes"] and not synth_failed:
        applied = applied_record(ctx, env) or {}
        bs = applied.get("bootstrap") or {}
        for stack, arns in asm.bootstrap_arns().items():
            for kind in ("deploy", "cfn-exec", "lookup"):
                want = None if bs.get("useCliCredentialsSynthesizer") else bs.get(BOOTSTRAP_ROLES[kind])
                got = arns.get(kind)
                if want and got != want or (bs.get("useCliCredentialsSynthesizer") and got):
                    raise NeedsHuman(f"stack {stack} of the {env} build uses {kind} role {got!r}, the applied IT "
                                     f"package says {want or 'none (cliCredentialsSynthesizer)'}; the overlay was "
                                     f"not applied to this synth")
        # the asset publishing roles live in the asset manifests (*.assets.json), not the stack artifacts
        part = env_info(ctx, env)["partition"]
        for kind, got in asm.publishing_arns().items():
            got = {g.replace("${AWS::Partition}", part) for g in got}
            want = None if bs.get("useCliCredentialsSynthesizer") else bs.get(BOOTSTRAP_ROLES[kind])
            if got and ((want and got != {want}) or bs.get("useCliCredentialsSynthesizer")):
                raise NeedsHuman(f"the asset manifests of the {env} build publish with {kind} role(s) {sorted(got)}, "
                                 f"the applied IT package says {want or 'none (cliCredentialsSynthesizer)'}; the "
                                 f"overlay was not applied to this synth")
        res["bootstrap"] = "matches"
    if "precreated" in s["modes"]:
        if asm.report is None:
            if synth_failed:
                return res
            raise NeedsHuman(f"the {env} build has no {REPORT} although iam.mode is precreated "
                             f"(app.iamRoleConfig.useCustomVamsStackRoles); the overlay was not applied")
        info = env_info(ctx, env, asm)
        roles, _refs = role_rows(asm, info)
        pkg = load_package(ctx, env)
        applied = applied_record(ctx, env) or {}
        missing = [r["constructPath"] for r in roles if not r["assignedRoleName"]]
        stale = pkg is None or role_set_digest(roles) != pkg["roleSetDigest"] or \
            policy_digest(roles) != pkg["policyDigest"]
        res.update(roles=len(roles), unmapped=len(missing))
        if stale or missing:
            new = plan(ctx, env, asm, source=source)
            new_pkg = load_package(ctx, env)
            ds = new["delta"]
            block(ctx, run, env, step_id, f"the {env} build needs IAM roles IT has not created yet "
                  f"({ds['added']} new, {ds['changed']} changed, {len(missing)} unmapped); new IT package "
                  f"{new['packageId']}", new_pkg,
                  [f"send {new['itRoles']} to IT (the delta section lists only what changed)",
                   f"a person runs `vra iam apply --env {env} --from <it-response.yaml>`",
                   f"`vra resume {run.run_id}`"])
        res["package"] = applied.get("packageId")
    elif "boundary" in s["modes"] and not synth_failed:
        # VAMS creates the roles; IT reviewed the boundary against the package's roles. A role the package does
        # not list (an upstream merge, a newly enabled feature) needs a new package before it is deployed.
        pkg = load_package(ctx, env)
        have = {r["path"] for r in asm.template_roles()}
        known = {r["constructPath"] for r in (pkg or {}).get("roles", {}).get("roles", [])}
        new = sorted(have - known)
        res.update(roles=len(have), newRoles=len(new))
        if new:
            block(ctx, run, env, step_id, f"the {env} build creates {len(new)} IAM role(s) the applied IT package "
                  f"{(pkg or {}).get('packageId')} does not list (e.g. {new[0]})", pkg,
                  [f"run `vra iam plan --env {env} --ref <the built ref>` and send the new package's IT-ROLES.md "
                   f"(its delta section) to IT, so the boundary is checked against the new roles",
                   f"a person runs `vra iam apply --env {env} --from <it-response.yaml>`", f"`vra resume {run.run_id}`"])
    return res


def _boundary_intrinsic_ok(b: dict, want: str | None, cdk_name: str | None) -> bool:
    text = json.dumps(b)
    if '"AWS::NoValue"' in text:
        return False
    return bool((want and want in text) or (cdk_name and f":policy/{cdk_name}" in text))


def block(ctx, run, env: str, step_id: str, reason: str, pkg: dict | None, steps: list[str]):
    """Write an IT request that hands over the package, then raise BLOCKED_ON_IT (exit 10). `vra resume`
    re-runs the gate step (the request carries no denials to re-simulate)."""
    run.it_dir.mkdir(parents=True, exist_ok=True)
    n = len(run.state.get("itRequests", [])) + 1
    rid = f"IT-REQUEST-{n}"
    e = ctx.ledger.env(env)
    req = {"id": rid, "n": n, "kind": "iam-package", "runId": run.run_id, "workflow": run.state["workflow"],
           "step": step_id, "env": env, "account": e.get("account"), "region": e.get("region"),
           "partition": e.get("partition"), "createdAt": util.now_iso(), "status": "open", "reason": reason,
           "package": (pkg or {}).get("packageId"), "packageDir": (pkg or {}).get("dir"), "steps": steps,
           "denials": [], "actions": [], "principals": [], "resume": f"vra resume {run.run_id}"}
    base = run.it_dir / rid
    util.write_json(base.with_suffix(".json"), req)
    lines = [f"# {rid}: IAM roles for `{env}` ({req['package'] or 'package not generated yet'})", "",
             f"- Run: `{run.run_id}` step `{step_id}`",
             f"- Account / region / partition: `{req['account']}` / `{req['region']}` / `{req['partition']}`", "",
             "## Why the run stopped", "", reason + ".", ""]
    if pkg:
        lines += [f"The package is `{pkg['dir']}`: start with `IT-ROLES.md`.", ""]
    lines += ["## Steps", ""] + [f"{i}. {x}" for i, x in enumerate(steps, 1)] + [""]
    util.atomic_write(base.with_suffix(".md"), "\n".join(lines))
    run.state.setdefault("itRequests", []).append({"id": rid, "n": n, "step": step_id, "env": env, "kind": "iam-package",
                                                   "package": req["package"], "status": "open", "blocking": True,
                                                   "actions": [], "path": str(base.with_suffix(".md"))})
    run.save()
    run.push_files(run.it_dir)
    raise BlockedOnIT(f"{reason} -> {rid}", {"itRequest": rid, "package": req["package"],
                                             "path": str(base.with_suffix(".md"))})


# ================================================================ verify (read-only)
def verify(ctx, env: str) -> dict:
    """Read-only: the roles IT returned exist, carry the boundary, trust the right principals and allow what the
    package needs (``iam:SimulatePrincipalPolicy``). What cannot be checked is reported, never assumed."""
    from vra import permissions
    e = ctx.ledger.env(env)
    s = settings(e)
    pkg = load_package(ctx, env)
    applied = applied_record(ctx, env)
    res: dict[str, Any] = {"env": env, "modes": s["modes"], "package": (pkg or {}).get("packageId"),
                           "applied": (applied or {}).get("packageId"), "checked": [], "problems": [],
                           "notChecked": []}
    if not needs_package(s):
        res["notChecked"].append("iam.mode standard: VAMS creates its own roles")
        return res
    if pkg is None or applied is None:
        res["problems"].append("no applied IT package (`vra iam plan`, then `vra iam apply`)")
        return res
    if applied.get("n") != pkg["n"]:
        res["problems"].append(f"the latest package {pkg['packageId']} is not applied ({applied.get('packageId')} is)")
    role = "readonly" if e.get("readOnlyRoleArn") else "deploy"
    try:
        iam = ctx.aws.client(env, "iam", role)
    except Exception as exc:  # noqa: BLE001
        res["notChecked"].append(f"no IAM client for {env} ({role} role): {util.redact(str(exc))[:200]}")
        return res
    want_boundary = applied.get("boundaryArn") or s["boundaryArn"]

    def get_role(name: str) -> dict | None:
        try:
            return iam.get_role(RoleName=name)["Role"]
        except Exception as exc:  # noqa: BLE001
            code = _code(exc)
            if code == "NoSuchEntity":
                res["problems"].append(f"role {name} does not exist")
            elif permissions.is_denied(exc):
                res["notChecked"].append(f"iam:GetRole {name} denied for the {role} role")
            else:
                res["notChecked"].append(f"iam:GetRole {name}: {code or type(exc).__name__}")
            return None

    if "boundary" in s["modes"] and want_boundary:
        try:
            iam.get_policy(PolicyArn=want_boundary)
            res["checked"].append(f"boundary policy {want_boundary} exists")
        except Exception as exc:  # noqa: BLE001
            if _code(exc) == "NoSuchEntity":
                res["problems"].append(f"boundary policy {want_boundary} does not exist")
            else:
                res["notChecked"].append(f"iam:GetPolicy {want_boundary}: {_code(exc) or type(exc).__name__}")
    if "precreated" in s["modes"]:
        by_path = {r["constructPath"]: r for r in pkg["roles"]["roles"]}
        role_arn = f"arn:{pkg['partition']}:iam::{pkg['account']}:role/"
        # PassRole grants name the suggested role names; IT may have chosen others
        renamed = {role_arn + by_path[p]["suggestedRoleName"]: role_arn + n
                   for p, n in (applied.get("roles") or {}).items() if p in by_path}
        for path, name in sorted((applied.get("roles") or {}).items()):
            row = by_path.get(path)
            r = get_role(name)
            if r is None or row is None:
                continue
            if want_boundary and (r.get("PermissionsBoundary") or {}).get("PermissionsBoundaryArn") != want_boundary:
                res["problems"].append(f"role {name} does not carry the boundary {want_boundary}")
            doc = r.get("AssumeRolePolicyDocument") or {}
            if isinstance(doc, str):
                from urllib.parse import unquote
                doc = json.loads(unquote(doc))
            have = set(_principals(_list(doc.get("Statement"))))
            lacking = sorted(set(row["trustPrincipals"]) - have)
            if lacking:
                res["problems"].append(f"role {name} does not trust {lacking}")
            extra = sorted(have - set(row["trustPrincipals"]))
            if extra:
                res["problems"].append(f"role {name} also trusts {extra}, which the package does not list")
            _check_broader(iam, name, row, res)
            stmts = [{"sid": None, "actions": _list(st["Action"]),
                      "resources": [renamed.get(x, x) for x in _list(st["Resource"])],
                      "optional": False} for st in row["policy"]["Statement"] if st.get("Effect") == "Allow"]
            try:
                sim = permissions.simulate(iam, r["Arn"], stmts)
                for d in sim["denied"]:
                    if ineffective_grant(d.get("action"), d.get("resource")):
                        res.setdefault("notEffective", []).append(
                            f"role {name}: {d.get('action')} on {d.get('resource')}: {NOT_EFFECTIVE}")
                        continue
                    res["problems"].append(f"role {name} is not allowed {d.get('action')} on {d.get('resource')}")
                res["checked"].append(f"role {name}: exists, trust, {sum(len(x['actions']) for x in stmts)} actions "
                                      f"simulated (conditions not evaluated)")
            except Exception as exc:  # noqa: BLE001
                res["notChecked"].append(f"iam:SimulatePrincipalPolicy for {name}: "
                                         f"{'denied' if permissions.is_denied(exc) else _code(exc) or type(exc).__name__}")
    if "bootstrap" in s["modes"]:
        bs = applied.get("bootstrap") or {}
        if bs.get("useCliCredentialsSynthesizer"):
            res["checked"].append("cliCredentialsSynthesizer: no bootstrap roles to check")
        for kind, key in BOOTSTRAP_ROLES.items():
            arn = bs.get(key)
            if not arn or bs.get("useCliCredentialsSynthesizer"):
                continue
            r = get_role(arn.rsplit("/", 1)[-1])
            if r is None:
                continue
            if r.get("Arn") and r["Arn"] != arn:
                res["problems"].append(f"bootstrap {key}: role ARN is {r['Arn']}, IT returned {arn}")
            if want_boundary and "boundary" in s["modes"] and \
                    (r.get("PermissionsBoundary") or {}).get("PermissionsBoundaryArn") != want_boundary:
                res["problems"].append(f"bootstrap role {arn} does not carry the boundary {want_boundary}")
            res["checked"].append(f"bootstrap {kind} role {arn} exists")
    if "boundary" in s["modes"]:
        res["notChecked"].append("roles VAMS creates under the boundary exist only after a deploy; the build "
                                 "checks every synthesized role carries the boundary")
    res["ok"] = not res["problems"]
    return res


NOT_EFFECTIVE = "not effective in VAMS's policy, not an IT gap"
# Actions that take only `*` as resource, and SNS actions on subscriptions (`<topic arn>:<id>`). VAMS's own
# synthesized policies grant them on key or topic ARNs, which they never match, so IAM denies them for the roles
# VAMS creates as well (live test, a documented limitation of `vra iam verify`).
WILDCARD_ONLY_ACTIONS = {"kms:listkeys", "kms:listaliases"}
SNS_SUBSCRIPTION_ACTIONS = {"sns:getsubscriptionattributes", "sns:setsubscriptionattributes", "sns:unsubscribe"}


def ineffective_grant(action: str | None, resource: str | None) -> bool:
    """Whether a simulated denial is of a grant VAMS's policy never makes effective (no IT role can change it)."""
    a, r = (action or "").lower(), resource or ""
    if a in WILDCARD_ONLY_ACTIONS:
        return r not in ("", "*")
    if a in SNS_SUBSCRIPTION_ACTIONS:
        parts = r.split(":")
        return len(parts) == 6 and parts[2] == "sns"   # a topic (pattern), never a subscription ARN
    return False


def _code(exc: BaseException) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


BROAD_MANAGED = ("AdministratorAccess", "PowerUserAccess", "IAMFullAccess")


def _check_broader(iam, name: str, row: dict, res: dict) -> None:
    """Upper bound: managed policies and inline actions the package does not grant. A customer managed
    policy or a wildcard / unlisted inline action is a problem; an extra AWS managed policy a warning (IT often
    adds e.g. AWSLambdaBasicExecutionRole), except the broad ones in ``BROAD_MANAGED``."""
    import fnmatch
    res.setdefault("warnings", [])
    want_managed = set(row.get("managedPolicyArns") or [])
    want_actions = [a.lower() for a in row.get("actions") or []]
    try:
        pages = iam.get_paginator("list_attached_role_policies").paginate(RoleName=name) \
            if hasattr(iam, "get_paginator") else [iam.list_attached_role_policies(RoleName=name)]
        attached = [p for page in pages for p in page.get("AttachedPolicies") or []]
        inline = iam.list_role_policies(RoleName=name).get("PolicyNames") or []
    except Exception as exc:  # noqa: BLE001
        res["notChecked"].append(f"role {name}: attached / inline policies not listed "
                                 f"({_code(exc) or type(exc).__name__}); extra grants not checked")
        return
    for p in attached:
        arn = p.get("PolicyArn") or ""
        if arn in want_managed:
            continue
        if ":iam::aws:policy/" in arn and arn.rsplit("/", 1)[-1] not in BROAD_MANAGED:
            res["warnings"].append(f"role {name} has AWS managed policy {arn} the package does not list")
        else:
            res["problems"].append(f"role {name} has managed policy {arn} the package does not list")
    for pname in inline:
        try:
            doc = iam.get_role_policy(RoleName=name, PolicyName=pname).get("PolicyDocument") or {}
        except Exception as exc:  # noqa: BLE001
            res["notChecked"].append(f"role {name} inline policy {pname}: {_code(exc) or type(exc).__name__}")
            continue
        if isinstance(doc, str):
            from urllib.parse import unquote
            doc = json.loads(unquote(doc))
        for st in _list(doc.get("Statement")):
            if st.get("Effect", "Allow") != "Allow":
                continue
            for a in _list(st.get("Action")):
                al = str(a).lower()
                covered = any(fnmatch.fnmatchcase(al, w) for w in want_actions)
                if al in ("*", "*:*") or (not covered and "*" in al) or not covered:
                    res["problems"].append(f"role {name} inline policy {pname} allows {a}, which the package "
                                           f"does not grant")


# ================================================================ plan synth (vra iam plan)
def discovery_overlay(src: Path) -> None:
    """Force the discovery synth in a scratch checkout: useCustomVamsStackRoles + generateReportOnly (VAMS still
    creates every role in the templates and writes the iam-policy-report), no custom bootstrap roles."""
    cfg_p = src / CONFIG_JSON
    cfg = util.read_json(cfg_p) if cfg_p.exists() else {}
    cfg.setdefault("app", {})["iamRoleConfig"] = {"useCustomBootstrapRoles": False, "useCustomVamsStackRoles": True}
    util.write_json(cfg_p, cfg)
    rc_p = src / IAM_ROLE_CONFIG
    rc = util.read_json(rc_p) if rc_p.exists() else {}
    rc = copy.deepcopy(rc) if isinstance(rc, dict) else {}
    rc.setdefault("bootstrap", {})
    rc["vamsStacks"] = {"generateReportOnly": True, "precreatedRoles": {}}
    util.write_json(rc_p, rc)


@workflow("iam-plan")
def wf_plan(ctx, run, args):
    """Workflow `iam-plan`: synthesize the env's VAMS (discovery mode) from a fork ref, then write the package."""
    from vra import build, gitops
    env = args["env"]
    ctx.ledger.env(env)
    if args.get("report"):
        def _from_report(sc):
            p = Path(args["report"])
            d = p if p.is_dir() else p.parent
            asm = Assembly.load(d)
            if asm.report is None:
                raise UsageError(f"{p}: no {REPORT}")
            return plan(ctx, env, asm, args.get("modes"),
                        source={"report": str(p), "reportSha256": util.sha256_file(d / REPORT)})
        return run.step(f"iam.plan.{env}", _from_report, f"IT package for {env} from a report")
    ref = args.get("ref") or ctx.ledger.project.get("trunk") or "main"
    work = ctx.scratch_dir / f"iam-plan-{env}"
    out = work / "cdk.out"

    def _synth(sc):
        if ctx.dry_run:
            return {"dryRun": True, "ref": ref, "out": str(out)}
        g = gitops.ensure_repo(ctx, sc.log_path)
        g.run("fetch", "origin", "--prune", "--tags")
        commit = gitops.resolve_commit(g, ref)
        version = build.vams_version_at(g, commit)
        src = work / "src"
        if src.exists():
            build.discard_dir(ctx, src, sc)
        if out.exists():
            build.discard_dir(ctx, out, sc)
        gitops.archive_to(g, commit, src)
        from vra import core_patches   # the same ledger core patches as vra build (step build.source)
        patches = core_patches.apply_all(ctx.ledger.root, src, sc.log, where=f"iam plan of {env}")
        e = ctx.ledger.env(env)
        with build.overlay_applied(ctx, src, env):
            discovery_overlay(src)
            dist = src / "web" / "dist"
            if not dist.exists():  # the IAM report does not depend on the web build; this assembly is never deployed
                dist.mkdir(parents=True)
                (dist / "index.html").write_text("<!doctype html><title>vra iam plan</title>\n")
            sh = ctx.shell(sc.log_path)
            if not (src / "infra" / "node_modules").exists():
                sh.run(["npm", "ci"], cwd=src / "infra")
            argv = ["synth", "--all", "--quiet", "-o", str(out), "-c", "configFile=config.json",
                    "-c", f"stack-name={e.get('baseStackName')}", "-c", f"region={e['region']}",
                    "-c", f"account={e['account']}"]
            region_env = {"AWS_REGION": e["region"], "AWS_DEFAULT_REGION": e["region"],
                          "CDK_DEFAULT_REGION": e["region"], "CDK_DEFAULT_ACCOUNT": str(e["account"])}
            try:
                creds, cred_role = build.synth_credentials(ctx, env)
            except Exception as exc:  # noqa: BLE001
                creds, cred_role = {**region_env, **build.NO_CREDENTIALS_ENV}, "none"
                sc.log(f"no AWS credentials for synth lookups in {env}: {exc}")
            # as vra build: prove the build-only role before minutes of synth, and turn a lookup that found no
            # credentials into what to set (live test: stage's VPC lookup failed as a bare `command failed (1)`)
            if cred_role in ("synth", "readonly"):
                build.prove_synth_role(ctx, env, cred_role)
            sub_env, _keys = build.synth_environment(ctx, env, {**region_env, **creds})
            sc.log(f"iam plan synth {env} at {commit[:12]} (VAMS {version}); AWS credentials = {cred_role}")
            try:
                ctx.shell(sc.log_path).run(["npx", "cdk", *argv], cwd=src / "infra", env=sub_env)
            except VraError:
                build._raise_if_lookups_need_credentials(sc.log_path, env, cred_role)
                raise
        tpl = src / "infra" / "node_modules" / "aws-cdk" / "lib" / "api" / "bootstrap" / "bootstrap-template.yaml"
        return {"ref": ref, "commit": commit, "vamsVersion": version, "out": str(out),
                "bootstrapTemplate": str(tpl) if tpl.exists() else None,
                "corePatches": [{k: r[k] for k in ("file", "sha256", "status")} for r in patches]}

    synth = run.step(f"iam.synth.{env}", _synth, f"discovery synth of {env}")
    if ctx.dry_run:
        return {"dryRun": True, **synth}

    def _plan(sc):
        asm = Assembly.load(Path(synth["out"]))
        tpl = Path(synth["bootstrapTemplate"]).read_text() if synth.get("bootstrapTemplate") else None
        source = {k: synth[k] for k in ("ref", "commit", "vamsVersion")}
        if synth.get("corePatches"):
            source["corePatches"] = synth["corePatches"]
        res = plan(ctx, env, asm, args.get("modes"), source=source, bootstrap_template=tpl)
        shutil.rmtree(work, ignore_errors=True)
        return res

    return run.step(f"iam.plan.{env}", _plan, f"IT package for {env}")


__all__ = ["Assembly", "apply", "check_assembly", "gate", "parse_modes", "plan", "settings", "validate_settings",
           "verify", "wf_plan"]
