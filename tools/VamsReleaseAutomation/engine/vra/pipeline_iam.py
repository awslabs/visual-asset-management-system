# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""IT package for the pipeline's own IAM roles (`vra pipeline plan`).

The roles come from the CDK policy report of the pipeline app (``cdk.out/iam-policy-report.json``, written when
the app is synthesized with ``-c vraIamReport=true``), the same report format VAMS writes with
``generateReportOnly``. The package itself is written by :func:`vra.iam.write_package`, the same writer as the
env packages of ``vra iam plan`` (IT-ROLES.md, roles.json, roles.csv, it-roles.yaml, delta, policies/).

Before handing the roles over, references to resources of the pipeline stack are resolved where the stack gives
the resource a fixed name (CodeBuild projects, pipelines, log groups); a reference to a resource whose name
CloudFormation generates (artifact bucket, KMS key) stays a placeholder and is flagged for IT to generalize.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PLACEHOLDER = re.compile(r"\(([A-Za-z0-9_./+-]+)\.(Ref|Arn)\)")
# CloudFormation properties that fix a resource's physical name (the value a Ref resolves to)
NAME_PROPS = {"AWS::CodeBuild::Project": "Name", "AWS::CodePipeline::Pipeline": "Name",
              "AWS::Logs::LogGroup": "LogGroupName", "AWS::IAM::Role": "RoleName",
              "AWS::Scheduler::Schedule": "Name"}


def physical_names(template: dict) -> dict[str, str]:
    """construct path of each resource (``aws:cdk:path`` without ``/Resource``) -> its fixed physical name."""
    out: dict[str, str] = {}
    for res in (template.get("Resources") or {}).values():
        path = ((res.get("Metadata") or {}).get("aws:cdk:path") or "")
        prop = NAME_PROPS.get(res.get("Type", ""))
        name = (res.get("Properties") or {}).get(prop) if prop else None
        if path and isinstance(name, str):
            out[path.removesuffix("/Resource")] = name
    return out


# ARN of a named resource (report placeholders for partition, region, account)
ARN_OF = {"AWS::CodeBuild::Project": "arn:(PARTITION):codebuild:(REGION):(ACCOUNT):project/{}",
          "AWS::CodePipeline::Pipeline": "arn:(PARTITION):codepipeline:(REGION):(ACCOUNT):{}",
          "AWS::Logs::LogGroup": "arn:(PARTITION):logs:(REGION):(ACCOUNT):log-group:{}:*"}


def physical_arns(template: dict) -> dict[str, str]:
    """construct path -> ARN, for resources with a fixed name whose ARN format is known."""
    out: dict[str, str] = {}
    for res in (template.get("Resources") or {}).values():
        path = ((res.get("Metadata") or {}).get("aws:cdk:path") or "").removesuffix("/Resource")
        fmt = ARN_OF.get(res.get("Type", ""))
        name = (res.get("Properties") or {}).get(NAME_PROPS.get(res.get("Type", ""), ""))
        if path and fmt and isinstance(name, str):
            out[path] = fmt.format(name)
    return out


def _role_arn_placeholder(v: Any) -> bool:
    """A role ARN CDK could not name in report mode (``arn:<p>:iam::<acct>:role`` without a name)."""
    text = json.dumps(v)
    return text.rstrip('"]} ').endswith(":role")


def resolve_roles(report: dict, template: dict, name_prefix: str = "") -> tuple[list[dict], list[str]]:
    """The report's roles with stack references resolved where possible, and the placeholders left for IT.

    - ``(<path>.Ref)`` / ``(<path>.Arn)`` of a resource with a fixed name (CodeBuild project, pipeline, log group)
      -> its name / ARN;
    - the trust of a CodePipeline action role (CDK renders the not-yet-named pipeline role as ``...:role``) -> the
      pipeline role of the same pipeline, under the role name the package proposes for it.
    """
    names, arns = physical_names(template), physical_arns(template)
    left: set[str] = set()
    paths = {r.get("roleConstructPath", "") for r in report.get("roles") or []}

    def fix(value: Any) -> Any:
        if isinstance(value, str):
            def sub(m: re.Match) -> str:
                path, attr = m.group(1).removesuffix("/Resource"), m.group(2)
                if attr == "Ref" and path in names:
                    return names[path]
                if attr == "Arn" and path in arns:
                    return arns[path]
                left.add(m.group(0))
                return m.group(0)
            return PLACEHOLDER.sub(sub, value)
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        return value

    roles = []
    for r in report.get("roles") or []:
        r = fix(r)
        path = r.get("roleConstructPath", "")
        if path.endswith("/CodePipelineActionRole"):
            pipeline_role = "/".join(path.split("/")[:2]) + "/Role"
            if pipeline_role in paths:
                arn = f"arn:(PARTITION):iam::(ACCOUNT):role/{proposed_name(pipeline_role, name_prefix)}"
                for st in r.get("assumeRolePolicy") or []:
                    pr = st.get("Principal") or {}
                    if "AWS" in pr and _role_arn_placeholder(pr["AWS"]):
                        pr["AWS"] = arn
        roles.append(r)
    return roles, sorted(left)


def proposed_name(construct_path: str, prefix: str = "") -> str:
    """A role name IT can use (64 characters at most): <prefix>vra-pl-<last path segment>-<hash>."""
    segs = construct_path.split("/")[1:] or [construct_path]
    if len(segs) > 1 and segs[-1] in ("Role", "CodePipelineActionRole"):
        segs = segs[:-1]
    tail = re.sub(r"[^A-Za-z0-9+=,.@_-]", "", "-".join(segs[-2:]) if len(segs) > 1 else segs[0])
    h = hashlib.sha256(construct_path.encode()).hexdigest()[:6]
    base = f"{prefix}vra-pl-"
    return f"{base}{tail[:64 - len(base) - len(h) - 1]}-{h}"


def write_package(roles: list[dict], out_root: Path, *, scope: str, title: str, boundary_arn: str | None = None,
                  name_prefix: str | None = None, purposes: dict[str, str] | None = None,
                  phases: dict[str, str] | None = None, unresolved: list[str] | None = None) -> dict:
    """Write the IT package with the env packages' writer (:func:`vra.iam.write_package`)."""
    from vra import iam  # imported here: vra.iam imports this module's helpers
    return iam.write_package(roles, out_root, scope=scope, title=title, boundary_arn=boundary_arn,
                             name_prefix=name_prefix, purposes=purposes, phases=phases, unresolved=unresolved)


__all__ = ["write_package", "resolve_roles", "physical_names", "proposed_name"]
