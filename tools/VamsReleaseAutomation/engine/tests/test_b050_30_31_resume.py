# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""E2E-050 Phase B10 regressions.

B-050-30: `vra --dry-run resume <run>` of a real run re-simulated its IT requests, reset its steps, ran the
workflow and saved (and published) the run: the parked prod run of execution 0b86fcb7 went from BLOCKED_ON_IT to
FAILED on the shared home. A dry run must change nothing.

B-050-31: a deploy parked BLOCKED_ON_IT on a precreated env asks IT for a policy change; IT updates the role
policies (same role names), `vra iam plan` writes package n+1 and a person applies it. The overlay `vra iam apply`
writes is byte-identical, yet `vra resume` was refused by the deploy IAM gate ("rebuild") because the bundle's
identity names package n, so the engine's own IT loop could never resume a deploy.
"""

from __future__ import annotations

import copy

import pytest

from vra import errors, iam, runner, util
from vra.errors import NeedsHuman, PermissionDenied
from vra.runner import workflow

from test_iam_package import _ungate, make_ctx, new_run, plan_from, response_for


# ================================================================ B-050-30
CALLS = {"n": 0}


@workflow("_test_b05030_blocked")
def _wf(ctx, run, args):
    def deploy(sc):
        CALLS["n"] += 1
        if CALLS["n"] == 1:
            raise PermissionDenied("cdk deploy to dev was denied", [
                {"action": "lambda:GetFunction", "kind": "identity",
                 "resource": "arn:aws:lambda:us-east-1:111111111111:function:vams-core-dev-us-east-1-X",
                 "principal": "arn:aws:iam::111111111111:role/vra-deploy"}], env="dev")
        return "deployed"
    return {"r": run.step("deploy", deploy)}


def test_dry_run_resume_of_a_real_run_changes_nothing(ctx):
    CALLS["n"] = 0
    out = runner.start(ctx, "_test_b05030_blocked", {}, env="dev")
    assert out.exit_code == errors.BLOCKED_ON_IT
    run_id = out.run.run_id
    state_file = out.run.dir / "state.json"
    before = state_file.read_bytes()
    files_before = sorted(p.relative_to(out.run.dir) for p in out.run.dir.rglob("*") if p.is_file())
    ctx.run_id = None
    ctx.dry_run = True
    with pytest.raises(errors.UsageError) as ei:
        runner.resume(ctx, run_id)
    assert "--dry-run resume" in ei.value.message and "vra report" in ei.value.message
    assert state_file.read_bytes() == before                     # status, steps, IT requests untouched
    assert sorted(p.relative_to(out.run.dir) for p in out.run.dir.rglob("*") if p.is_file()) == files_before
    assert CALLS["n"] == 1                                         # the workflow did not run
    # the real resume still works afterwards
    ctx.dry_run = False
    out2 = runner.resume(ctx, run_id)
    assert out2.exit_code == 0, out2.message
    assert CALLS["n"] == 2


def test_resume_of_a_dry_run_run_stays_allowed(ctx):
    """A run that was itself started with --dry-run keeps resuming as a dry run (unchanged behaviour)."""
    CALLS["n"] = 0
    ctx.dry_run = True
    out = runner.start(ctx, "_test_b05030_blocked", {}, env="dev")
    assert out.run.state.get("dryRun") is True
    ctx.run_id = None
    out2 = runner.resume(ctx, out.run.run_id)
    assert out2.exit_code in (0, errors.BLOCKED_ON_IT)


# ================================================================ B-050-31
def _policy_only_change(asm):
    """IT-side remedy of a denial: one role's policy gets a wider resource, no role added, removed or renamed."""
    role = next(r for r in asm.report["roles"] if r["roleConstructPath"].endswith("/assetService/ServiceRole"))
    stmts = _statements(role)
    assert stmts, "fixture role has no statements to widen"
    stmts[0]["Resource"] = "arn:aws:lambda:us-east-1:111111111111:function:vams-core-prod-us-east-*"


def _statements(obj):
    if isinstance(obj, dict):
        if "Effect" in obj and ("Action" in obj or "NotAction" in obj):
            return [obj]
        out = []
        for v in obj.values():
            out += _statements(v)
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out += _statements(v)
        return out
    return []


def _prod_with_applied(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "precreated", "rolePrefix": "ITVAMS-"})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    built = {"buildId": "cust-1", "iam": {"prod": copy.deepcopy(iam.build_identity(c, "prod"))}}
    assert built["iam"]["prod"]["package"] == "prod-1"
    return c, built


def test_resumed_deploy_accepts_a_bundle_after_a_policy_only_it_update(tmp_path):
    c, built = _prod_with_applied(tmp_path)
    plan_from(c, "prod", "commercial-report", tmp_path, mutate=_policy_only_change)
    pkg = iam.load_package(c, "prod")
    assert pkg["packageId"] == "prod-2"
    iam.apply(c, "prod", response_for(c, "prod"))
    now = iam.build_identity(c, "prod")
    assert now["package"] == "prod-2" and now["responseSha256"] != built["iam"]["prod"]["responseSha256"]
    assert now["overlay"] == built["iam"]["prod"]["overlay"]      # role names unchanged: same overlay
    res = iam.deploy_gate(c, new_run(c, "deploy"), "prod", built, "deploy.prod.iam")
    assert res["bundleIam"] == "matches" and res["package"] == "prod-2" and res["bundlePackage"] == "prod-1"


def test_deploy_gate_still_refuses_when_it_renamed_a_role(tmp_path):
    c, built = _prod_with_applied(tmp_path)
    plan_from(c, "prod", "commercial-report", tmp_path, mutate=_policy_only_change)

    def rename(d):
        first = next(iter(d["roles"]))
        d["roles"][first] = d["roles"][first] + "-v2"
    iam.apply(c, "prod", response_for(c, "prod", edit=rename))
    with pytest.raises(NeedsHuman) as ei:
        iam.deploy_gate(c, new_run(c, "deploy"), "prod", built, "deploy.prod.iam")
    assert "rebuild" in ei.value.message


def test_deploy_gate_still_refuses_a_bundle_without_an_overlay_record(tmp_path):
    c, built = _prod_with_applied(tmp_path)
    built["iam"]["prod"]["overlay"] = {k: None for k in built["iam"]["prod"]["overlay"]}
    built["iam"]["prod"]["package"] = "prod-0"
    with pytest.raises(NeedsHuman):
        iam.deploy_gate(c, new_run(c, "deploy"), "prod", built, "deploy.prod.iam")
