# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""(live test Phase B10g): VAMS builds the ARN of its per-asset SNS topics (``AssetTopic<db>-<asset>``,
created at runtime) with ``cdk.Fn.sub(...)``. The iam-policy-report shows such a resource only as an opaque
``${Token[TOKEN.n]}``, and `vra iam` took every single-service SNS token for a template topic named after the core
stack, so IT's precreated roles got ``sns:CreateTopic`` on ``...:vams-core-prod01-us-east-1*``. On prod01 every
asset create then failed ("Error creating SNS topic": AuthorizationError on ``AssetTopice2e050r506-...``). 7 roles
were affected. The precreated build synth carries no policy for these roles, so the pattern comes from a table
(the plan's report-only synth and the deploy gate's precreated synth must give the same policy digest).

The report statements below are copied from ``state/iam/prod/4/iam-policy-report.json``."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

import pytest

from vra import iam

ACCT = "111111111111"
ENV = {"partition": "aws", "region": "us-east-1", "account": ACCT}
CORE = "vams-core-prod01-us-east-1"
LIVE_TOPIC = f"arn:aws:sns:us-east-1:{ACCT}:AssetTopice2e050r506-x81ce7ba4-7f79-4759-ac21-98f190d24afe"
EMPTY = iam.Assembly({"roles": []}, {}, None)

# role (below the core stack) -> its SNS statement in the prod-4 report
AFFECTED = {
    "ApiBuilder/createAsset/ServiceRole": ["sns:CreateTopic"],
    "ApiBuilder/assetService/ServiceRole": ["sns:CreateTopic", "sns:DeleteTopic"],
    "ApiBuilder/sendEmail/ServiceRole": ["sns:Publish"],
    "ApiBuilder/subscriptionService/ServiceRole": [
        "sns:BatchUnsubscribe", "sns:CreateTopic", "sns:DeleteTopic", "sns:GetSubscriptionAttributes",
        "sns:ListSubscriptionsByTopic", "sns:Subscribe", "sns:Unsubscribe"],
    "ApiBuilder/unsubscribeService/ServiceRole": [
        "sns:BatchUnsubscribe", "sns:DeleteTopic", "sns:GetSubscriptionAttributes", "sns:ListSubscriptionsByTopic",
        "sns:Subscribe", "sns:Unsubscribe"],
    "StorageResourcesBuilder/sqsBucketSync-created-0/ServiceRole": ["sns:CreateTopic", "sns:DeleteTopic"],
    "StorageResourcesBuilder/sqsBucketSync-deleted-1/ServiceRole": ["sns:CreateTopic", "sns:DeleteTopic"],
}


def _resources(stmts, action):
    return [x for st in stmts if action in iam._list(st["Action"]) for x in iam._list(st["Resource"])]


@pytest.mark.parametrize("tail", sorted(AFFECTED))
def test_asset_topic_roles_get_the_runtime_topic_pattern(tail):
    actions = AFFECTED[tail]
    report = [{"Action": actions if len(actions) > 1 else actions[0], "Effect": "Allow",
               "Resource": "${Token[TOKEN.1539]}"}]
    stmts, unresolved = iam.resolve_statements(report, EMPTY, f"{CORE}/{tail}", ENV)
    got = _resources(stmts, actions[0])
    assert got == [f"arn:aws:sns:us-east-1:{ACCT}:AssetTopic*"], got
    assert any(fnmatch.fnmatchcase(LIVE_TOPIC, x) for x in got)
    assert unresolved == []


def test_the_prod4_report_has_no_core_stack_topic_grant_left():
    """Every SNS token of the real prod-4 report resolves to AssetTopic* (none to the core stack pattern)."""
    rep = Path(__file__).parent / "fixtures" / "asset_topic_role_sns_statements.json"
    rows = json.loads(rep.read_text())["rows"]
    assert len(rows) == 7
    for row in rows:
        opaque = [s for s in row["statements"] if iam.OPAQUE_RE.fullmatch(str(s["Resource"]))]
        assert len(opaque) == 1, row
        stmts, unresolved = iam.resolve_statements(opaque, EMPTY, row["roleConstructPath"], ENV)
        sns = [x for st in stmts for x in iam._list(st["Resource"]) if ":sns:" in x]
        assert sns == [f"arn:aws:sns:us-east-1:{ACCT}:AssetTopic*"], (row["roleConstructPath"], sns)


def test_other_sns_tokens_keep_the_core_stack_scope():
    # a topic of another nested stack passed in as a parameter (e.g. a notification topic): unchanged
    report = [{"Action": "sns:Publish", "Effect": "Allow", "Resource": "${Token[TOKEN.7]}"}]
    stmts, unresolved = iam.resolve_statements(report, EMPTY, f"{CORE}/ApiBuilder/someOtherFunction/ServiceRole",
                                               ENV)
    assert _resources(stmts, "sns:Publish") == [f"arn:aws:sns:us-east-1:{ACCT}:{CORE}*"]
    assert unresolved and unresolved[0]["scope"] == "core stack name"


def test_an_affected_role_keeps_its_other_services_unchanged():
    report = [{"Action": "dynamodb:GetItem", "Effect": "Allow", "Resource": "${Token[TOKEN.1551]}"}]
    stmts, _ = iam.resolve_statements(report, EMPTY, f"{CORE}/ApiBuilder/createAsset/ServiceRole", ENV)
    assert _resources(stmts, "dynamodb:GetItem") == [f"arn:aws:dynamodb:us-east-1:{ACCT}:table/{CORE}*"]


def test_partition_is_the_envs():
    v = {"partition": "aws-us-gov", "region": "us-gov-west-1", "account": ACCT}
    report = [{"Action": "sns:CreateTopic", "Effect": "Allow", "Resource": "${Token[TOKEN.1]}"}]
    stmts, _ = iam.resolve_statements(report, EMPTY, f"{CORE}/ApiBuilder/createAsset/ServiceRole", v)
    assert _resources(stmts, "sns:CreateTopic") == [f"arn:aws-us-gov:sns:us-gov-west-1:{ACCT}:AssetTopic*"]
