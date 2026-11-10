# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The scoped CDK execution policy confines role changes and iam:PassRole to VAMS's own roles.

``vams-role-names.json`` lists every role name of real VAMS synths (v2.6.6 and development, with and without
``IamRoleNamePrefix``, config names ``vams``, ``my-vams``, a name longer than 16 characters and a short one, and a
us-west-2 CloudFront deployment whose cross-Region export roles CloudFormation names). Every one of them must be
creatable (with the boundary), changeable and passable under the policy rendered with the values `vra iam plan`
computes (ConfigName, CoreRoleNameStem, RoleNamePrefix); a role of another team, the CDK bootstrap roles and the
vra roles must not be."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
from cfn_eval import allowed as _cfn_allowed  # noqa: E402
from cfn_eval import load_cfn, render  # noqa: E402

from vra import iam  # noqa: E402
from vra.errors import UsageError  # noqa: E402

HERE = Path(__file__).resolve().parent
POLICY = HERE.parent / "cloudformation" / "vra-cdk-execution-policy.yaml"
SYNTHS = json.loads((HERE / "vams-role-names.json").read_text())["synths"]
ACCT, P, REGION = "111111111111", "aws", "us-east-1"
BOUNDARY = f"arn:{P}:iam::{ACCT}:policy/vra-cdk-execution-role-boundary"
ROLE = f"arn:{P}:iam::{ACCT}:role/"
CHANGES = ("iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy",
           "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:TagRole")
FOREIGN = ["SomeOtherTeam-Admin", "OrganizationAccountAccessRole", "vamsAdministrator", "admin-vams-core",
           "prod-SomeOtherTeam-Admin"]
PROTECTED = [f"cdk-hnb659fds-cfn-exec-role-{ACCT}-{REGION}", f"cdk-hnb659fds-deploy-role-{ACCT}-{REGION}",
             "vra-deploy-prod", "vra-ci-dev"]


def statements(prefix: str, config_name: str = "vams", policy: Path = POLICY) -> list[dict]:
    xp = iam.execution_policy_parameters({"configName": config_name, "baseStackName": "prod",
                                          "iam": {"mode": "boundary", "rolePrefix": prefix} if prefix else {}}, "prod")
    t = load_cfn(policy)
    params = {"AWS::Partition": P, "AWS::AccountId": ACCT, "AWS::Region": REGION, "PolicyName": "vra-cdk-execution",
              **xp, "AllowedRegions": [REGION], "RequiredPermissionsBoundaryArn": "",
              "AllowRolesWithoutBoundary": "false", "ProtectForeignTrailsAndKeys": "true"}
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
    stmts = statements(s["IamRoleNamePrefix"], s["configName"])
    names = s["roleNames"]
    assert len(names) >= 80
    missing = [n for n in names if not _can_manage(evaluator, stmts, n)]
    assert missing == [], missing[:5]


@pytest.mark.parametrize("config_name,stem", [
    ("vams", "vamscore"),
    ("my-vams", "myvamscore"),                       # hyphen dropped, as uniqueResourceName does
    ("vamsenterprisefork", "vamsenterprisefo"),      # longer than 16: the first 16 characters
    ("va", "vacore"),
    ("my-team-vams-2", "myteamvams2core"),
])
def test_the_core_role_name_stem_is_what_iam_role_transform_produces(config_name, stem):
    assert iam.core_role_name_stem(config_name) == stem


@pytest.mark.parametrize("synth", ["development-my-vams", "development-vamsenterprisefork", "development-va"])
def test_the_stem_starts_every_core_role_of_a_real_synth(synth):
    s = SYNTHS[synth]
    stem = iam.core_role_name_stem(s["configName"])
    core = [n for n in s["roleNames"] if not n.startswith(s["configName"] + "-")]
    assert len(core) >= 80 and all(n.startswith(stem) for n in core), (stem, core[:3])


def test_roles_cloudformation_names_need_the_config_name_grant(evaluator):
    """The us-west-2 CloudFront synth has two cross-Region export provider roles CloudFormation names
    "<stack name>-..." (they are added after IamRoleTransform runs); only the <ConfigName>-* grant covers them."""
    s = SYNTHS["development-my-vams-us-west-2"]
    generated = [n for n in s["roleNames"] if n.startswith("my-vams-")]
    assert len(generated) == 2
    stmts = statements("", "my-vams")
    assert all(_can_manage(evaluator, stmts, n) for n in generated)


def test_a_prefix_switch_can_still_remove_the_unprefixed_roles(evaluator):
    stmts = statements("ITVAMS-")
    for n in SYNTHS["development-noprefix"]["roleNames"]:
        assert evaluator(stmts, "iam:DeleteRole", ROLE + n), n


@pytest.mark.parametrize("prefix", ["", "ITVAMS-"])
@pytest.mark.parametrize("config_name", ["vams", "my-vams"])
def test_roles_that_are_not_vams_roles_are_out_of_reach(evaluator, prefix, config_name):
    stmts = statements(prefix, config_name)
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


def test_every_vams_config_name_is_accepted():
    """ConfigName takes what VAMS accepts (the CDK stack-name rule on <name>-core-<base>): hyphens, any length."""
    import re
    p = load_cfn(POLICY)["Parameters"]
    rx = re.compile(p["ConfigName"]["AllowedPattern"])
    for name in ("vams", "my-vams", "vamsenterprisefork", "a-very-long-vams-config-name-2026"):
        assert rx.match(name), name
        assert re.match(p["CoreRoleNameStem"]["AllowedPattern"], iam.core_role_name_stem(name)), name
    assert p["RoleNamePrefix"]["Default"] == ""


@pytest.mark.parametrize("iam_cfg,want", [
    ({}, ""),
    ({"mode": "boundary", "boundaryArn": "arn:aws:iam::111111111111:policy/b", "rolePrefix": "ITVAMS-"}, "ITVAMS-"),
    ({"mode": "precreated", "rolePrefix": "ITVAMS-"}, "ITVAMS-"),
])
def test_the_policy_parameters_come_from_the_ledger(iam_cfg, want):
    e = {"configName": "my-acme", "baseStackName": "prod", "iam": iam_cfg}
    assert iam.execution_policy_parameters(e, "prod") == {"ConfigName": "my-acme", "CoreRoleNameStem": "myacmecore",
                                                          "RoleNamePrefix": want}


def test_precreated_mode_without_a_prefix_never_falls_back_to_the_base_stack_name():
    e = {"configName": "vams", "baseStackName": "prod", "iam": {"mode": "precreated"}}
    with pytest.raises(UsageError, match="needs iam.rolePrefix"):
        iam.execution_policy_parameters(e, "prod")
    errors, _ = iam.validate_settings(e, "envs/prod")
    assert any("needs iam.rolePrefix" in x for x in errors)
