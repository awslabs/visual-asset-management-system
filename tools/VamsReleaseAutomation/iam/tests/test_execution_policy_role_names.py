# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The scoped CDK execution policy confines role changes and iam:PassRole to VAMS's own roles.

``vams-role-names.json`` lists every role name of real VAMS synths (v2.6.6 and development, with and without
``IamRoleNamePrefix``). Every one of them must be creatable (with the boundary), changeable and passable under
the policy rendered with the env's ConfigName and RoleNamePrefix; a role of another team, the CDK bootstrap
roles and the vra roles must not be."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfn_eval import allowed as _cfn_allowed  # noqa: E402
from cfn_eval import load_cfn, render  # noqa: E402

HERE = Path(__file__).resolve().parent
POLICY = HERE.parent / "cloudformation" / "vra-cdk-execution-policy.yaml"
SYNTHS = json.loads((HERE / "vams-role-names.json").read_text())["synths"]
ACCT, P, REGION = "111111111111", "aws", "us-east-1"
BOUNDARY = f"arn:{P}:iam::{ACCT}:policy/vra-cdk-execution-role-boundary"
ROLE = f"arn:{P}:iam::{ACCT}:role/"
CHANGES = ("iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy",
           "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:TagRole")
# CloudFormation-generated names of roles in the other VAMS stacks (cross-Region notifications, origin verify,
# CDK cross-Region export providers): "<stack name>-<logical id>-<random>"
GENERATED = ["vams-xregion-prod-us-east-1-us-west-2-BucketNotificationsHandl-AbCdEf123456",
             "vams-origin-verify-prod-us-east-1-AWS679f53fac002430cb0-AbCdEf123456",
             "vams-core-prod-us-east-1-CustomCrossRegionExportRead-AbCdEf123456"]
FOREIGN = ["SomeOtherTeam-Admin", "OrganizationAccountAccessRole", "vamsAdministrator", "admin-vams-core"]
PROTECTED = [f"cdk-hnb659fds-cfn-exec-role-{ACCT}-{REGION}", f"cdk-hnb659fds-deploy-role-{ACCT}-{REGION}",
             "vra-deploy-prod", "vra-ci-dev"]


def statements(prefix: str, policy: Path = POLICY) -> list[dict]:
    t = load_cfn(policy)
    params = {"AWS::Partition": P, "AWS::AccountId": ACCT, "AWS::Region": REGION, "PolicyName": "vra-cdk-execution",
              "ConfigName": "vams", "RoleNamePrefix": prefix, "AllowedRegions": [REGION],
              "RequiredPermissionsBoundaryArn": "", "AllowRolesWithoutBoundary": "false",
              "ProtectForeignTrailsAndKeys": "true"}
    conds = {"CreateBoundary": True, "BoundaryOptional": False, "BoundaryEnforced": True, "ProtectForeign": True,
             "HasRoleNamePrefix": prefix != ""}
    doc = render(t["Resources"]["ExecutionPolicy"]["Properties"]["PolicyDocument"], params, conds,
                 {"RoleBoundary": BOUNDARY})
    return doc["Statement"]


@pytest.fixture(scope="module")
def evaluator():
    return _cfn_allowed


def _can_manage(allowed, stmts, name: str) -> bool:
    arn = ROLE + name
    return (allowed(stmts, "iam:CreateRole", arn, {"iam:PermissionsBoundary": BOUNDARY})
            and all(allowed(stmts, a, arn, {"iam:PolicyARN": f"arn:{P}:iam::aws:policy/AWSXrayWriteOnlyAccess"})
                    for a in CHANGES)
            and allowed(stmts, "iam:PassRole", arn, {"iam:PassedToService": "lambda.amazonaws.com"}))


def _cannot_touch(allowed, stmts, name: str) -> bool:
    arn = ROLE + name
    ctx = {"iam:PermissionsBoundary": BOUNDARY, "iam:PassedToService": "lambda.amazonaws.com",
           "iam:PolicyARN": f"arn:{P}:iam::aws:policy/AWSXrayWriteOnlyAccess"}
    return not any(allowed(stmts, a, arn, ctx) for a in ("iam:CreateRole", "iam:PassRole") + CHANGES)


@pytest.mark.parametrize("synth", sorted(SYNTHS))
def test_every_real_vams_role_is_covered(evaluator, synth):
    s = SYNTHS[synth]
    stmts = statements(s["IamRoleNamePrefix"])
    names = s["roleNames"]
    assert len(names) >= 80
    missing = [n for n in names if not _can_manage(evaluator, stmts, n)]
    assert missing == [], missing[:5]


def test_generated_names_of_the_other_vams_stacks_are_covered(evaluator):
    for prefix in ("", "ITVAMS-"):
        stmts = statements(prefix)
        assert [n for n in GENERATED if not _can_manage(evaluator, stmts, n)] == []


def test_a_prefix_switch_can_still_remove_the_unprefixed_roles(evaluator):
    stmts = statements("ITVAMS-")
    for n in SYNTHS["development-noprefix"]["roleNames"]:
        assert evaluator(stmts, "iam:DeleteRole", ROLE + n), n


@pytest.mark.parametrize("prefix", ["", "ITVAMS-"])
def test_roles_that_are_not_vams_roles_are_out_of_reach(evaluator, prefix):
    stmts = statements(prefix)
    for n in FOREIGN + PROTECTED:
        assert _cannot_touch(evaluator, stmts, n), n
    if prefix:
        # bootstrap and vra roles IT created with the env's prefix stay protected
        for n in (f"{prefix}cdk-hnb659fds-cfn-exec-role-{ACCT}-{REGION}", f"{prefix}vra-deploy-prod"):
            assert _cannot_touch(evaluator, stmts, n), n


def test_the_role_grants_name_no_wildcard_resource():
    for prefix in ("", "ITVAMS-"):
        for st in statements(prefix):
            acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            if st["Effect"] == "Allow" and set(acts) & {"iam:PassRole", "iam:CreateRole", *CHANGES}:
                res = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
                assert "*" not in res and all(":role/" in r for r in res), st.get("Sid")


def test_config_name_is_limited_to_what_the_core_role_names_keep():
    p = load_cfn(POLICY)["Parameters"]
    assert p["ConfigName"]["AllowedPattern"] == "^[A-Za-z0-9]{1,12}$"
    assert p["RoleNamePrefix"]["Default"] == ""


@pytest.mark.parametrize("iam_cfg,want", [
    ({}, ""),
    ({"mode": "boundary", "boundaryArn": "arn:aws:iam::111111111111:policy/b", "rolePrefix": "ITVAMS-"}, "ITVAMS-"),
    ({"mode": "precreated", "rolePrefix": "ITVAMS-"}, "ITVAMS-"),
    ({"mode": "precreated"}, "prod-"),
])
def test_the_policy_parameters_come_from_the_ledger(iam_cfg, want):
    sys.path.insert(0, str(HERE.parents[1] / "engine"))
    from vra import iam
    e = {"configName": "acme", "baseStackName": "prod", "iam": iam_cfg}
    assert iam.execution_policy_parameters(e, "prod") == {"ConfigName": "acme", "RoleNamePrefix": want}
    # what the IT package suggests in precreated mode starts with that prefix
    if iam_cfg.get("mode") == "precreated":
        name = (iam_cfg.get("rolePrefix") or "") + iam.suggested_name(
            {"constructPath": "vams-core-prod-us-east-1/ApiBuilder/fn/ServiceRole", "id": "ApiBuilder-fn-x"},
            iam_cfg.get("rolePrefix") or "", iam.name_base(e, "prod"))
        assert name.startswith(want)
