# SPDX-License-Identifier: Apache-2.0
"""(live test Phase B9): CloudFormation keeps at most 25 characters of the stack name in a generated Lambda
function name. prod01's first prod deploy failed on it: IT's precreated provider-framework role
`vrae2e-prod01-frameworkonEvent-e78af3` was granted `lambda:GetFunction` on `function:vams-core-prod01-us-east-1*`,
but the handler is `vams-core-prod01-us-east--PopulateS3AssetBucketsTa-3HgTIcjc1sKz` (26-char core stack name, cut
to 25). The same `<core stack>*` pattern sat in the vra roles' ReindexInvoke grants (VAMS's crOsReindexer is
nameless too). Real names below are from the live account census (e2e-050-run/K/K11-gen-name-census.txt)."""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

from vra import iam, permissions

PKG = Path(__file__).resolve().parents[2]
ENV = {"partition": "aws", "region": "us-east-1", "account": "111111111111"}
LIVE = {
    # (core stack name, a real generated function name of that stack)
    "vams-core-prod01-us-east-1": "vams-core-prod01-us-east--PopulateS3AssetBucketsTa-3HgTIcjc1sKz",
    "vams-core-prod02-us-east-1": "vams-core-prod02-us-east--AOSSOSSDeploySchemaProvi-GZKaBDXD07wz",
    "vams-core-test1-us-east-1": "vams-core-test1-us-east-1--createAssetLinkBA382DDD-XTT8RYDwzCLO",
}
NAMELESS_FN = {"Type": "AWS::Lambda::Function", "Properties": {"Handler": "index.handler"}}


def _fn_name(arn: str) -> str:
    return arn.split(":function:", 1)[1]


@pytest.mark.parametrize("stack", sorted(LIVE))
def test_generated_lambda_pattern_matches_the_real_cloudformation_name(stack):
    arn = iam._arn_for(NAMELESS_FN, "Arn", stack, ENV)
    assert arn.startswith("arn:aws:lambda:us-east-1:111111111111:function:vams-core-")
    assert fnmatch.fnmatchcase(LIVE[stack], _fn_name(arn)), (arn, LIVE[stack])


def test_short_stack_names_keep_their_full_pattern_and_named_functions_stay_exact():
    assert _fn_name(iam._arn_for(NAMELESS_FN, "Arn", "vams-core-test1-us-east-1", ENV)) == "vams-core-test1-us-east-1*"
    named = {"Type": "AWS::Lambda::Function", "Properties": {"FunctionName": "vams-prod01-explicit"}}
    assert _fn_name(iam._arn_for(named, "Arn", "vams-core-prod01-us-east-1", ENV)) == "vams-prod01-explicit"


@pytest.mark.parametrize("workflow", ["migrate", "data-copy-target"])
def test_reindex_grant_of_the_manifests_matches_a_generated_lambda_name(workflow):
    vals = {"Partition": "aws", "Region": "us-east-1", "Account": "111111111111", "ConfigName": "vams",
            "CoreStack": "vams-core-prod01-us-east-1", "CoreStackLambda": "vams-core-prod01-us-east-1"[:25],
            "SsmPrefix": "/vams-prod01-us-east-1/resourceNames", "CdkQualifier": "hnb659fds"}
    st = [s for s in permissions.expand_manifest(permissions.load_manifest(workflow), vals)
          if "lambda:InvokeFunction" in s["actions"]]
    assert st, workflow
    fns = [_fn_name(r) for s in st for r in s["resources"] if ":function:" in r]
    reindexer = "vams-core-prod01-us-east--crOsReindexer518D309-AbCdEfGh1234"
    assert any(fnmatch.fnmatchcase(reindexer, f) for f in fns), fns


def test_placeholders_truncate_the_lambda_stack_prefix():
    class _L:
        project: dict = {}

        def env(self, e):
            return {"region": "us-east-1", "account": "111111111111", "configName": "vams", "partition": "aws"}

        def core_stack_name(self, e):
            return "vams-core-prod01-us-east-1"

        def ssm_prefix(self, e):
            return "/vams-prod01-us-east-1/resourceNames"

        def stack_suffix(self, e):
            return "prod01-us-east-1"

    class _C:
        ledger = _L()
        aws = None

    vals = permissions.placeholders(_C(), "prod")
    assert vals["CoreStack"] == "vams-core-prod01-us-east-1" and vals["CoreStackLambda"] == "vams-core-prod01-us-east-"


@pytest.mark.parametrize("tpl", ["vra-deploy-role.yaml", "vra-data-copy-roles.yaml"])
def test_role_templates_reindex_grant_survives_the_truncation(tpl):
    text = (PKG / "iam" / "cloudformation" / tpl).read_text()
    lines = [ln for ln in text.splitlines() if ":function:${ConfigName}-core-${BaseStackName}" in ln]
    assert lines, tpl
    for ln in lines:
        pattern = ln.split(":function:", 1)[1].strip()
        assert pattern == "${ConfigName}-core-${BaseStackName}-*", ln
        rendered = pattern.replace("${ConfigName}", "vams").replace("${BaseStackName}", "prod01")
        assert fnmatch.fnmatchcase(LIVE["vams-core-prod01-us-east-1"], rendered)
