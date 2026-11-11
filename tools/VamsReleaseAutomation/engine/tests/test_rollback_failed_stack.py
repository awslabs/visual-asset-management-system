# SPDX-License-Identifier: Apache-2.0
"""A cdk deploy whose stack CloudFormation could not roll back is ROLLBACK_FAILED,
not an IT block.

A fresh prod create failed first on the account's CloudFront cache-policy quota
(StaticWeb `ApiCachePolicy` CREATE_FAILED, ServiceLimitExceeded). During the rollback the Conversion3dBasic
registration's provider role was denied `lambda:GetFunction`, so the root stack ended ROLLBACK_FAILED.
vra saw the denial in the events and stopped BLOCKED_ON_IT (exit 10) with an IT request that says the run "resumes
automatically once the access below is in place": wrong twice, since the quota still blocks and CloudFormation cannot
deploy over a ROLLBACK_FAILED stack. The events below have the shape of real CloudFormation events; names are synthetic."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from vra import deploy, errors
from vra.errors import CommandFailed, PermissionDenied, RollbackFailed

ROOT = "vams-core-prodfr-us-east-1"
WEB = "arn:aws:cloudformation:us-east-1:111111111111:stack/vams-core-prodfr-us-east-1-StaticWeb-X/1"
REG = "arn:aws:cloudformation:us-east-1:111111111111:stack/vams-core-prodfr-us-east-1-Conv3dPipe-Y/2"
DENY = ("Received response status [FAILED] from custom resource. Message returned: TimeoutError: "
        '{"state":"TIMEOUT","observedResponses":{"403: User: arn:aws:sts::111111111111:assumed-role/'
        "itvams-prodfr-frameworkonEvent-3c4b5a/vams-core-prodfr-us-east--Conversion3dBasicRegistr-Mm3Nn4Oo5Pp6 is "
        "not authorized to perform: lambda:GetFunction on resource: arn:aws:lambda:us-east-1:111111111111:function:"
        "vams-core-prodfr-us-east--importGlobalPipelineWork-Qq7Rr8Ss9Tt0 because no identity-based policy allows "
        'the lambda:GetFunction action":10},"reason":"Waiter has timed out"}')
QUOTA = ("Resource handler returned message: \"Limit exceeded for resource of type 'AWS::CloudFront::CachePolicy'. "
         "Reason: Account's Cache Policies limit reached. (Service: CloudFront, Status Code: 400)\" "
         "(HandlerErrorCode: ServiceLimitExceeded)")


def _t(hms: str) -> datetime:
    h, m, s = map(int, hms.split(":"))
    return datetime(2026, 11, 7, h, m, s, tzinfo=timezone.utc)


def _ev(at, lid, typ, st, why, phys="x", stack_id="root"):
    return {"Timestamp": _t(at), "LogicalResourceId": lid, "ResourceType": typ, "ResourceStatus": st,
            "ResourceStatusReason": why, "PhysicalResourceId": phys, "StackId": stack_id}


# newest first, as DescribeStackEvents returns them
EVENTS = {
    ROOT: [
        _ev("17:54:45", ROOT, "AWS::CloudFormation::Stack", "ROLLBACK_FAILED",
            "The following resource(s) failed to delete: [PipelineBuilder].", ROOT, ROOT),
        _ev("17:54:45", "PipelineBuilder", "AWS::CloudFormation::Stack", "DELETE_FAILED",
            "Embedded stack " + REG + " was not successfully deleted", REG),
        _ev("17:47:13", "PipelineBuilder", "AWS::CloudFormation::Stack", "CREATE_FAILED",
            "Resource creation cancelled", REG),
        _ev("17:47:13", "StaticWeb", "AWS::CloudFormation::Stack", "CREATE_FAILED",
            "Embedded stack " + WEB + " was not successfully created: null", WEB),
    ],
    WEB: [
        _ev("17:46:56", "WebAppCachePolicy37ECCD6E", "AWS::CloudFront::CachePolicy", "CREATE_FAILED", QUOTA,
            stack_id=WEB),
        _ev("17:46:56", "ApiOriginRequestPolicy7A536D4C", "AWS::CloudFront::OriginRequestPolicy", "CREATE_FAILED",
            "Resource creation cancelled", stack_id=WEB),
        _ev("17:46:55", "ApiCachePolicyF71AA3E6", "AWS::CloudFront::CachePolicy", "CREATE_FAILED", QUOTA,
            stack_id=WEB),
    ],
    REG: [
        _ev("17:52:43", "Conversion3dBasicRegistrationD71A4AEE", "AWS::CloudFormation::CustomResource",
            "DELETE_FAILED", DENY, stack_id=REG),
        _ev("17:47:24", "Conversion3dBasicRegistrationD71A4AEE", "AWS::CloudFormation::CustomResource",
            "CREATE_FAILED", "Resource creation cancelled", stack_id=REG),
    ],
}


class _Cfn:
    def __init__(self, status):
        self.status = status

    def describe_stack_events(self, StackName):
        return {"StackEvents": EVENTS.get(StackName, [])}

    def describe_stacks(self, StackName):
        return {"Stacks": [{"StackName": StackName, "StackStatus": self.status}]}


class _Shell:
    def run(self, *a, **k):
        raise CommandFailed("cdk deploy failed", 1, "❌  vams-core-prodfr-us-east-1 failed")


def _ctx(status):
    cfn = _Cfn(status)
    return SimpleNamespace(
        dry_run=False,
        aws=SimpleNamespace(client=lambda env, svc: cfn, subprocess_env=lambda env, role: {}),
        ledger=SimpleNamespace(env=lambda env: {"requiresApproval": True}),
        shell=lambda log: _Shell())


@pytest.fixture(autouse=True)
def _no_cdk(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "cdk", lambda manifest, *a, **k: ["cdk", *a])
    monkeypatch.setattr(deploy, "assembly_dir", lambda ctx, m, t: tmp_path / "cdk.out" / t)


MANIFEST = {"buildId": "cust-v2.6.6-11", "targets": {"prod": {"stacks": [ROOT]}}}
SC = SimpleNamespace(log_path=None)


def test_a_stack_left_rollback_failed_is_rollback_failed_with_the_first_cause():
    with pytest.raises(RollbackFailed) as ei:
        deploy.cdk_deploy(_ctx("ROLLBACK_FAILED"), "prod", MANIFEST, "prod", SC)
    exc = ei.value
    assert exc.exit_code == errors.ROLLBACK_FAILED and not isinstance(exc, PermissionDenied)
    assert f"{ROOT} is ROLLBACK_FAILED" in exc.message
    assert "ApiCachePolicyF71AA3E6" in exc.message and "Cache Policies limit reached" in exc.message
    assert "1 permission error(s) blocked the rollback" in exc.message
    first = exc.detail["firstFailure"]
    assert first["resource"] == "ApiCachePolicyF71AA3E6" and first["stack"] == WEB
    assert exc.detail["stuck"] == {ROOT: "ROLLBACK_FAILED"}
    assert [d["action"] for d in exc.detail["denials"]] == ["lambda:GetFunction"]


def test_a_denial_on_a_rolled_back_stack_is_still_an_it_block():
    # guard: the stack rolled back cleanly, so granting the call and resuming is the right next step
    with pytest.raises(PermissionDenied):
        deploy.cdk_deploy(_ctx("ROLLBACK_COMPLETE"), "prod", MANIFEST, "prod", SC)


def test_first_failure_skips_follow_on_and_rollback_events():
    got = deploy.first_failure(_ctx("ROLLBACK_FAILED"), "prod", [ROOT])
    assert got["resource"] == "ApiCachePolicyF71AA3E6" and got["status"] == "CREATE_FAILED"
