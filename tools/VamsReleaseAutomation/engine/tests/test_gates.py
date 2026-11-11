# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Approval / SoD gates, watch refusals, promotion refusals."""

import datetime as dt

import pytest

from vra import approvals, build as build_mod, deploy, errors, runner, util, watch
from vra.errors import NeedsHuman


def default_scope(subject):
    if subject.startswith("data-copy"):
        return ["data-copy"]
    if subject.startswith("restore:"):
        return ["restore", "rollback"]
    return ["deploy", "promote", "rollback"]


def approve(ledger, env, subject, approver, **extra):
    p = ledger.approval_path(env, subject.replace(":", "_"))
    extra.setdefault("scope", default_scope(subject))
    util.write_yaml(p, {"build": subject, "env": env, "approver": approver, "approvedAt": util.now_iso(),
                        "ticket": "CHG-0001", **extra})
    return p


def passed_test(ledger, env, build, suites=("live-cli",), passed=4):
    """A `vra test` history record as `vra test` writes it (per-suite counts)."""
    counts = {s: {"tests": passed, "failures": 0, "errors": 0, "skipped": 0, "passed": passed, "executed": passed}
              for s in suites}
    return ledger.append_history(env, {"action": "test", "build": build, "result": "SUCCEEDED", "passed": passed,
                                       "executed": passed, "tests": passed, "failures": 0, "suiteCounts": counts})


def fake_build(ctx, build_id, lane="release", envs=("dev", "test", "prod"), commit="0" * 40):
    """A minimal, internally consistent bundle so gates/verification can run without a real synth."""
    bdir = build_mod.build_dir(ctx, build_id)
    targets = {}
    for e in envs:
        out = bdir / "cdk.out" / e
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifest.json").write_text('{"artifacts": {}}')
        targets[e] = {"env": e, "slot": None, "sha256": util.combined_hash(util.tree_hashes(out)), "stacks": []}
    (bdir / "migrations").mkdir(parents=True, exist_ok=True)
    m = {"buildId": build_id, "lane": lane, "promotable": lane == "release", "commit": commit, "vamsVersion": "2.6.6",
         "cdkCliVersion": "2.1135.0", "targets": targets, "complete": True}
    util.write_json(bdir / "manifest.json", m)
    (bdir / "manifest.sha256").write_text(util.sha256_file(bdir / "manifest.json") + "  manifest.json\n")
    return m


# ------------------------------------------------------------------ approvals / SoD
def test_sod_refuses_self_approval(ctx, ledger):
    util.write_yaml(ledger.root / "envs/test/env.yaml",
                    {**util.read_yaml(ledger.root / "envs/test/env.yaml"),
                     "approvers": ["qa-lead@example.com", "dev-operator@example.com"]})
    ledger._envs.clear()
    approve(ledger, "test", "cust-v2.6.6-1", "dev-operator@example.com")
    with pytest.raises(NeedsHuman, match="separation of duties"):
        approvals.check(ctx, "test", "cust-v2.6.6-1", "deploy")
    # same person, different identity spelling (STS session name / bare user) is still refused
    assert approvals.same_person("arn:aws-us-gov:sts::111111111111:assumed-role/Ops/dev-operator@example.com",
                                 "dev-operator@example.com")
    assert approvals.same_person("dev-operator", "dev-operator@example.com")


def test_approval_rules(ctx, ledger):
    with pytest.raises(NeedsHuman, match="no record"):
        approvals.check(ctx, "test", "cust-v2.6.6-1", "deploy")
    approve(ledger, "test", "cust-v2.6.6-1", "mallory@example.com")
    with pytest.raises(NeedsHuman, match="not in envs/test approvers"):
        approvals.check(ctx, "test", "cust-v2.6.6-1", "deploy")
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com", expiresAt="2020-01-01T00:00:00Z")
    with pytest.raises(NeedsHuman, match="expired"):
        approvals.check(ctx, "test", "cust-v2.6.6-1", "deploy")
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com", scope=["deploy"])
    with pytest.raises(NeedsHuman, match="scope"):
        approvals.check(ctx, "test", "cust-v2.6.6-1", "data-copy")
    rec = approvals.check(ctx, "test", "cust-v2.6.6-1", "deploy")
    assert rec["approver"] == "qa-lead@example.com"
    assert approvals.check(ctx, "dev", "anything", "deploy") is None  # dev is not gated


def test_deploy_to_gated_env_without_approval_exits_20(ctx):
    fake_build(ctx, "cust-v2.6.6-1")
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1"}, env="test", build="cust-v2.6.6-1")
    assert out.exit_code == errors.NEEDS_HUMAN and "approval required" in out.message


def test_change_window_blocks_without_emergency(ctx, ledger):
    d = util.read_yaml(ledger.root / "envs/prod/env.yaml")
    d["changeWindow"] = {"cron": "0 22 * * 6", "durationMinutes": 60}
    util.write_yaml(ledger.root / "envs/prod/env.yaml", d)
    ledger._envs.clear()
    ctx.now = lambda: dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.timezone.utc)  # Tuesday noon
    with pytest.raises(NeedsHuman, match="change window"):
        approvals.check_change_window(ctx, "prod", False, "b", None)
    approve(ledger, "prod", "b", "release-manager@example.com", scope=["deploy", "emergency"],
            approvedAt="2026-11-03T11:00:00Z")  # dated by the test clock, not the wall clock
    assert approvals.check_change_window(ctx, "prod", True, "b", None)["emergency"] is True


def test_bundle_tamper_detected(ctx):
    m = fake_build(ctx, "cust-v2.6.6-1")
    (build_mod.build_dir(ctx, "cust-v2.6.6-1") / "cdk.out/dev/evil.json").write_text("{}")
    with pytest.raises(errors.VraError, match="integrity"):
        build_mod.verify_bundle(ctx, m, "dev")


# ------------------------------------------------------------------ watch
def test_watch_only_targets_auto_envs(ctx):
    assert watch.auto_envs(ctx, "release") == ["dev"]
    assert watch.auto_envs(ctx, "preview") == ["dev-preview"]
    assert set(watch.refused_envs(ctx, "release")) == {"test", "prod"}


def test_watch_origin_refused_on_gated_env_even_with_approval(ctx, ledger):
    fake_build(ctx, "cust-v2.6.6-1")
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com")
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1", "origin": "watch"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN
    assert "watch never deploys to test" in out.message


def test_watch_nothing_new_is_noop(ctx, repos):
    from vra import gitops
    runner.start(ctx, "sync", {})
    st = gitops.load_sync_state(ctx)
    gitops.mark_processed(ctx, "release", "v2.6.6", "x")
    gitops.mark_processed(ctx, "preview", "x", st["lanes"]["preview"]["vendorSha"])
    ctx.run_id = None
    out = runner.start(ctx, "watch", {"notify": False})
    assert out.exit_code == 0 and out.run.state["result"]["nothingNew"] is True


# ------------------------------------------------------------------ promotion
def test_promote_refuses_preview_build(ctx, ledger):
    fake_build(ctx, "preview-v2.7.0-abc", lane="preview", envs=("dev-preview",))
    approve(ledger, "test", "preview-v2.7.0-abc", "qa-lead@example.com")
    out = runner.start(ctx, "promote", {"from": "dev", "to": "test", "build": "preview-v2.7.0-abc",
                                        "approval": "preview-v2.7.0-abc"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "never promotable" in out.message


def test_promote_refuses_untested_and_wrong_chain(ctx, ledger):
    fake_build(ctx, "cust-v2.6.6-1")
    with pytest.raises(NeedsHuman, match="no successful deploy"):
        deploy.check_promotion(ctx, "dev", "test", "cust-v2.6.6-1")
    with pytest.raises(NeedsHuman, match="promotes from"):
        deploy.check_promotion(ctx, "dev", "prod", "cust-v2.6.6-1")
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.6.6-1", "result": "SUCCEEDED"})
    with pytest.raises(NeedsHuman, match="did not pass tests"):
        deploy.check_promotion(ctx, "dev", "test", "cust-v2.6.6-1")
    ledger.append_history("dev", {"action": "test", "build": "cust-v2.6.6-1", "result": "FAILED"})
    with pytest.raises(NeedsHuman, match="did not pass tests"):
        deploy.check_promotion(ctx, "dev", "test", "cust-v2.6.6-1")
    # a passing record without per-suite counts is not accepted as evidence
    ledger.append_history("dev", {"action": "test", "build": "cust-v2.6.6-1", "result": "SUCCEEDED"})
    with pytest.raises(NeedsHuman, match="predates per-suite counts"):
        deploy.check_promotion(ctx, "dev", "test", "cust-v2.6.6-1")
    passed_test(ledger, "dev", "cust-v2.6.6-1")
    # commit not on trunk -> still refused (sync PR not merged yet)
    with pytest.raises(NeedsHuman, match="not on main"):
        deploy.check_promotion(ctx, "dev", "test", "cust-v2.6.6-1")
    ctx.dry_run = True  # skips the trunk check (no fork fetch)
    assert deploy.check_promotion(ctx, "dev", "test", "cust-v2.6.6-1")["to"] == "test"


def test_lane_mismatch_refused(ctx):
    fake_build(ctx, "preview-v2.7.0-abc", lane="preview", envs=("dev",))
    out = runner.start(ctx, "deploy", {"env": "dev", "build": "preview-v2.7.0-abc"}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN and "preview-lane build" in out.message
