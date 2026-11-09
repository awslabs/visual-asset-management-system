# SPDX-License-Identifier: Apache-2.0
"""B-050-35 (E2E-050 Phase B10d): ``Ref`` of a Lambda function (or a DynamoDB table) is its name, not its ARN.

The iam-policy-report of the Conversion3dBasic registration's provider framework names the handler (a function of
another nested stack) as ``arn:(PARTITION):lambda:us-east-1:<acct>:function:(<path>/Resource.Ref)``. `vra iam`
resolved ``Ref`` to the full ARN, so IT's role `vrae2e-demo11-frameworkonEvent-a78d52` was granted
``lambda:GetFunction`` on ``function:arn:aws:lambda:...:function:vams-core-demo11-us-east-*``, which matches no
function. demo11's fresh prod create (2026-11-07 17:47 UTC) then hit ROLLBACK_FAILED: the registration custom
resource could not GetFunction ``vams-core-demo11-us-east--importGlobalPipelineWork-vXdz4VFaJMPz`` on delete.
The statements below are copied from ``state/iam/prod/3/iam-policy-report.json``."""

from __future__ import annotations

import fnmatch

from vra import iam

ACCT = "111111111111"
ENV = {"partition": "aws", "region": "us-east-1", "account": ACCT}
CORE = "vams-core-demo11-us-east-1"
FN_PATH = f"{CORE}/ApiBuilder2/importGlobalPipelineWorkflow/Resource"
LIVE_FN = "vams-core-demo11-us-east--importGlobalPipelineWork-vXdz4VFaJMPz"
LIVE_ARN = f"arn:aws:lambda:us-east-1:{ACCT}:function:{LIVE_FN}"
NAMELESS_FN = {"Type": "AWS::Lambda::Function", "Properties": {"Handler": "index.handler"}}
NAMELESS_TABLE = {"Type": "AWS::DynamoDB::Table", "Properties": {}}


class _Asm:
    def __init__(self, by_path):
        self.by_path = by_path


ASM = _Asm({FN_PATH: (None, None, NAMELESS_FN), f"{CORE}/StorageResourcesBuilder/T/Resource": (None, None,
                                                                                               NAMELESS_TABLE)})
REPORT_STMTS = [
    {"Action": "lambda:InvokeFunction", "Effect": "Allow",
     "Resource": [f"arn:(PARTITION):lambda:us-east-1:{ACCT}:function:({FN_PATH}.Ref)",
                  f"arn:(PARTITION):lambda:us-east-1:{ACCT}:function:({FN_PATH}.Ref):*"]},
    {"Action": "lambda:GetFunction", "Effect": "Allow",
     "Resource": f"arn:(PARTITION):lambda:us-east-1:{ACCT}:function:({FN_PATH}.Ref)"},
]


def _resources(stmts, action):
    return [x for st in stmts if action in iam._list(st["Action"]) for x in iam._list(st["Resource"])]


def test_function_ref_inside_an_arn_resolves_to_one_matching_arn():
    stmts, unresolved = iam.resolve_statements(REPORT_STMTS, ASM, f"{CORE}/PipelineBuilder/x/ServiceRole", ENV)
    assert unresolved == []
    gets = _resources(stmts, "lambda:GetFunction")
    assert gets and all(x.count("arn:") == 1 for x in gets), gets
    assert any(fnmatch.fnmatchcase(LIVE_ARN, x) for x in gets), gets
    invokes = _resources(stmts, "lambda:InvokeFunction")
    assert any(fnmatch.fnmatchcase(LIVE_ARN, x) for x in invokes), invokes
    assert any(fnmatch.fnmatchcase(LIVE_ARN + ":live", x) for x in invokes), invokes


def test_ref_is_the_name_and_arn_stays_the_arn():
    assert iam._arn_for(NAMELESS_FN, "Ref", CORE, ENV) == "vams-core-demo11-us-east-*"
    named = {"Type": "AWS::Lambda::Function", "Properties": {"FunctionName": "vams-demo11-explicit"}}
    assert iam._arn_for(named, "Ref", CORE, ENV) == "vams-demo11-explicit"
    assert iam._arn_for(NAMELESS_FN, "Arn", CORE, ENV) == f"arn:aws:lambda:us-east-1:{ACCT}:function:vams-core-demo11-us-east-*"


def test_table_ref_is_the_name_inside_a_table_arn():
    got, why = iam.resolve_resource(
        f"arn:(PARTITION):dynamodb:(REGION):(ACCOUNT):table/({CORE}/StorageResourcesBuilder/T/Resource.Ref)", ASM, ENV)
    assert got == f"arn:aws:dynamodb:us-east-1:{ACCT}:table/{CORE}*" and why == "pattern"
    assert iam._arn_for(NAMELESS_TABLE, "Arn", CORE, ENV) == f"arn:aws:dynamodb:us-east-1:{ACCT}:table/{CORE}*"
    assert iam._arn_for(NAMELESS_TABLE, "StreamArn", CORE, ENV).endswith("/stream/*")
