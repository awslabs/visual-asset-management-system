# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Live test Phase B10c regression.

the first prod deploy of prod01 failed during CREATE, leaving `vams-core-prod01-us-east-1` in
ROLLBACK_COMPLETE (no outputs, no SSM resource names; CloudFormation cannot update it and `cdk deploy` deletes and
re-creates it). The next deploy treated that stack as a live deployment: the pre-deploy checkpoint looked for the
SSM resource names and stopped every retry with NEEDS_HUMAN ("no SSM resource names under ... is the core stack
deployed?"), so an env whose first install failed could never be installed. A stack that never finished its first
create is a first install: no checkpoint, no data migrations, template IAM counts.
"""

from __future__ import annotations

import pytest

from vra import deploy, errors, runner, vams_resources

from conftest import FakeAws
from test_build_deploy import FakeCfn, _build

DESCRIPTION = "(SO9299) (VAMS-CORE) (version:2.6.6) Primary Components for the Visual Asset Management Systems"


class StatusCfn(FakeCfn):
    """The core stack exists in the given status, with the given outputs (a failed create has none)."""

    def __init__(self, status, outputs=None):
        super().__init__()
        self.status, self.outputs = status, outputs or []

    def describe_stacks(self, StackName):
        return {"Stacks": [{"StackName": StackName, "StackStatus": self.status, "Description": DESCRIPTION,
                            "Outputs": self.outputs}]}


@pytest.mark.parametrize("status", ["ROLLBACK_COMPLETE", "REVIEW_IN_PROGRESS", "DELETE_COMPLETE"])
def test_a_stack_that_never_finished_its_first_create_is_not_deployed(status):
    assert vams_resources.stack_exists(StatusCfn(status), "vams-core-prod-us-east-1") is False


@pytest.mark.parametrize("status", ["CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"])
def test_a_created_stack_stays_deployed(status):
    """Guard: a failed UPDATE (UPDATE_ROLLBACK_COMPLETE) still holds the deployment and its data."""
    assert vams_resources.stack_exists(StatusCfn(status), "vams-core-prod-us-east-1") is True


def test_deployed_version_of_a_failed_first_create_is_a_first_install(ctx):
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": StatusCfn("ROLLBACK_COMPLETE")})
    assert deploy.deployed_vams_version(ctx, "dev") == (None, None)
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": StatusCfn("UPDATE_ROLLBACK_COMPLETE")})
    assert deploy.deployed_vams_version(ctx, "dev")[0] == "2.6.6"


def test_deploy_over_a_failed_first_create_installs_instead_of_stopping(ctx, repos, calls):
    """The live failure: the checkpoint of the redeploy stopped with 'no SSM resource names'."""
    b = _build(ctx).run.state["result"]["buildId"]
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": StatusCfn("ROLLBACK_COMPLETE")})
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev", build=b)
    assert out.exit_code == 0, out.message
    ckpt = out.run.find_step("deploy.dev.checkpoint")
    assert ckpt["status"] == "done"
    assert "first install" in str(ckpt.get("result")), ckpt
    assert out.run.find_step("deploy.dev.cdk-deploy")["status"] == "done"
    assert out.exit_code != errors.NEEDS_HUMAN
