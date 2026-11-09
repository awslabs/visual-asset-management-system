#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Add sts:SetSourceIdentity to the trust policies of the CDK bootstrap roles.

Why: test and prod deploy roles require sts:SourceIdentity by default (vra-deploy-role.yaml
RequireSourceIdentity=auto). A session that carries a source identity can only chain into a role whose trust
policy allows sts:SetSourceIdentity, and the stock CDK bootstrap template does not. Without this patch
`cdk deploy` from a vra-deploy-<env> session is denied when it assumes the file-publishing, image-publishing,
lookup or deploy role. (The CloudFormation execution role is assumed by the CloudFormation service, not chained
into, and is left unchanged.)

How: every trust statement that allows sts:TagSession is copied as an sts:SetSourceIdentity statement with the
same principal and condition (also inside Fn::If wrappers), so the patch follows the template's own trust model
(same account, --trust, --trust-for-lookup).

Usage (in each env account and region, as the account administrator):

    npx cdk bootstrap --show-template > bootstrap-template.yaml
    python3 iam/scripts/patch-bootstrap-template.py bootstrap-template.yaml > bootstrap-template.vra.yaml
    npx cdk bootstrap "aws://<account>/<region>" --template bootstrap-template.vra.yaml \\
        --cloudformation-execution-policies "<policy arn>"   # plus your usual flags

Needs PyYAML. Exit 1 when a role could not be patched (unexpected template shape).
"""

from __future__ import annotations

import copy
import json
import sys

import yaml

CHAINED_ROLES = ("FilePublishingRole", "ImagePublishingRole", "LookupRole", "DeploymentActionRole")
MARK = "sts:SetSourceIdentity"


def _actions(st: dict) -> list[str]:
    a = st.get("Action") or []
    return [a] if isinstance(a, str) else list(a)


def _patch_statements(stmts: list) -> int:
    added = []
    have = any(isinstance(s, dict) and MARK in _actions(s) for s in stmts)
    if have:
        return 0
    for s in stmts:
        if isinstance(s, dict) and "Fn::If" in s:
            cond, yes, no = s["Fn::If"]
            if isinstance(yes, dict) and "sts:TagSession" in _actions(yes):
                new = copy.deepcopy(yes)
                new["Action"] = MARK
                added.append({"Fn::If": [cond, new, copy.deepcopy(no)]})
        elif isinstance(s, dict) and s.get("Effect") == "Allow" and "sts:TagSession" in _actions(s):
            new = copy.deepcopy(s)
            new["Action"] = MARK
            added.append(new)
    stmts.extend(added)
    return len(added)


def patch(template: dict) -> dict:
    res = template.get("Resources") or {}
    report = {}
    for name in CHAINED_ROLES:
        role = res.get(name)
        if not role:
            continue
        doc = role.get("Properties", {}).get("AssumeRolePolicyDocument") or {}
        stmts = doc.get("Statement")
        if not isinstance(stmts, list):
            raise ValueError(f"{name}: AssumeRolePolicyDocument.Statement is not a list")
        n = _patch_statements(stmts)
        if n == 0 and not any(isinstance(s, dict) and MARK in _actions(s) for s in stmts):
            raise ValueError(f"{name}: no sts:TagSession trust statement to mirror")
        report[name] = n
    if not report:
        raise ValueError("no CDK bootstrap roles found; is this the output of `cdk bootstrap --show-template`?")
    desc = template.get("Description") or "CDK bootstrap"
    if "SetSourceIdentity" not in desc:
        template["Description"] = f"{desc} (patched: bootstrap roles trust sts:SetSourceIdentity for vra)"
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    text = open(argv[1], encoding="utf-8").read()
    tpl = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
    try:
        report = patch(tpl)
    except ValueError as exc:
        print(f"patch-bootstrap-template: {exc}", file=sys.stderr)
        return 1
    print(f"patched trust statements: {report}", file=sys.stderr)
    sys.stdout.write(yaml.safe_dump(tpl, sort_keys=False, width=120))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
