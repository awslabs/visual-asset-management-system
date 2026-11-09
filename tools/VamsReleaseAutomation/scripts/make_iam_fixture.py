#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Turn a real VAMS cloud assembly (``cdk.out``) into a compact test fixture for `vra iam`.

Keeps what `vra iam` reads and nothing else: the full ``iam-policy-report.json``, each template's resources
reduced to ``Type`` + ``aws:cdk:path`` (plus the ``AWS::IAM::Role`` properties the role checks read), and the
stack artifacts of ``manifest.json`` (bootstrap role ARNs). The synth account id is replaced by a placeholder
account so the fixture passes the neutrality scan. Output: one gzip'd JSON document.

Usage: make_iam_fixture.py <cdk.out> <out.json.gz> --account <synth account> [--note TEXT]
Fixtures are written into a cdk.out layout again by ``engine/tests/iam_fixtures.py``.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

PLACEHOLDER_ACCOUNT = "111111111111"
ROLE_PROPS = ("RoleName", "PermissionsBoundary", "AssumeRolePolicyDocument", "ManagedPolicyArns", "Path")
# literal physical names vra iam turns into exact ARNs (vra.iam.NAME_PROPS)
NAME_PROPS = ("TableName", "BucketName", "LogGroupName", "FunctionName", "QueueName", "TopicName", "Name",
              "UserPoolName", "StateMachineName")


def slim_template(t: dict) -> dict:
    out = {}
    for lid, res in (t.get("Resources") or {}).items():
        r = {"Type": res.get("Type")}
        path = (res.get("Metadata") or {}).get("aws:cdk:path")
        if path:
            r["Metadata"] = {"aws:cdk:path": path}
        props = res.get("Properties") or {}
        if res.get("Type") == "AWS::IAM::Role":
            r["Properties"] = {k: v for k, v in props.items() if k in ROLE_PROPS}
        elif res.get("Type") == "AWS::Lambda::Function":
            # role -> function -> custom resource: tells deploy-time roles from run-time roles
            env = ((props.get("Environment") or {}).get("Variables") or {})
            keep = {k: v for k, v in env.items() if k.startswith("USER_") and k.endswith("_FUNCTION_ARN")}
            r["Properties"] = {"Role": props.get("Role"), **({"Environment": {"Variables": keep}} if keep else {})}
        elif res.get("Type", "").startswith("Custom::") or res.get("Type") == "AWS::CloudFormation::CustomResource":
            r["Properties"] = {"ServiceToken": props.get("ServiceToken")}
        else:
            names = {k: v for k, v in props.items() if k in NAME_PROPS and isinstance(v, str)}
            if names:
                r["Properties"] = names
        out[lid] = r
    return {"Resources": out}


def slim_manifest(m: dict) -> dict:
    arts = {}
    for k, a in (m.get("artifacts") or {}).items():
        if a.get("type") == "aws:cloudformation:stack":
            props = {x: a["properties"][x] for x in ("stackName", "templateFile", "assumeRoleArn",
                                                       "cloudFormationExecutionRoleArn", "lookupRole")
                     if x in (a.get("properties") or {})}
            arts[k] = {"type": a["type"], "properties": props}
    return {"artifacts": arts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cdk_out")
    ap.add_argument("out")
    ap.add_argument("--account", required=True)
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    d = Path(a.cdk_out)
    doc: dict = {"note": a.note, "templates": {}}
    rep = d / "iam-policy-report.json"
    if rep.exists():
        doc["report"] = json.loads(rep.read_text())
    for f in sorted(d.glob("*.template.json")):
        doc["templates"][f.name] = slim_template(json.loads(f.read_text()))
    if (d / "manifest.json").exists():
        doc["manifest"] = slim_manifest(json.loads((d / "manifest.json").read_text()))
    text = json.dumps(doc, sort_keys=True).replace(a.account, PLACEHOLDER_ACCOUNT)
    with open(a.out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:  # reproducible bytes
        fh.write(text.encode("utf-8"))
    print(f"{a.out}: {len(text)} bytes uncompressed, {Path(a.out).stat().st_size} gzip'd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
