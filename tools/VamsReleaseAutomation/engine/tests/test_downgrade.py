# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Migration audit 2026-11-10 (#1244 D16, D17): downgrades that cross a data migration stop, upgrades skip
releases without one, and a real gap in the chain still stops."""

import json

import pytest

from vra import build as build_mod, deploy, errors, migrate, runner
from vra.errors import NeedsHuman

from conftest import MIGRATION_SCRIPT, FakeAws, write

ALL = ("2.1", "2.2", "2.3", "2.4", "2.5")


def mig_tree(root, starts=ALL):
    """deploymentDataMigration with v<s>_to_v<s+1> for every start minor in ``starts``."""
    for frm in starts:
        maj, mi = frm.split(".")
        name = f"v{frm}_to_v{maj}.{int(mi) + 1}"
        write(root, f"{name}/upgrade/{name}_migration.py", MIGRATION_SCRIPT.format(extra=""))
        write(root, f"{name}/upgrade/{name}_migration_config.json", json.dumps({"dry_run": False}))
    return root


@pytest.fixture
def full(tmp_path):
    return mig_tree(tmp_path / "mig")


# ----------------------------------------------------------------- D16 / D17: the audit's probe table
@pytest.mark.parametrize("frm,to,steps", [
    ("2.6.6", "2.6.7", []),                                   # patch upgrade
    ("2.6.6", "2.7.0", []),                                   # no 2.6 -> 2.7 data change
    ("2.5.3", "2.6.6", ["v2.5_to_v2.6"]),
    ("2.5.3", "2.7.0", ["v2.5_to_v2.6"]),                     # D17: skip a release with no migration folder
    ("2.4.0", "2.7.0", ["v2.4_to_v2.5", "v2.5_to_v2.6"]),     # D17
    ("2.2.0", "2.4.1", ["v2.2_to_v2.3", "v2.3_to_v2.4"]),
    ("2.6.7", "2.6.6", []),                                   # downgrade within a minor
    ("2.7.0", "2.6.6", []),                                   # downgrade that crosses no migration
])
def test_plan_allows(ctx, full, frm, to, steps):
    p = migrate.plan(ctx, "dev", full, frm, to)
    assert [s["id"] for s in p["steps"]] == steps


@pytest.mark.parametrize("frm,to,crossed", [
    ("2.6.6", "2.5.3", ["v2.5_to_v2.6"]),                     # D16
    ("2.6.6", "2.4.0", ["v2.4_to_v2.5", "v2.5_to_v2.6"]),     # D16
    ("2.7.0", "2.5.3", ["v2.5_to_v2.6"]),                     # D16
])
def test_plan_stops_a_downgrade_across_a_migration(ctx, full, frm, to, crossed):
    with pytest.raises(NeedsHuman, match="crosses data migrations") as ei:
        migrate.plan(ctx, "dev", full, frm, to)
    assert ei.value.exit_code == errors.NEEDS_HUMAN
    assert ei.value.detail["crossed"] == crossed


def test_plan_stops_a_real_gap(ctx, tmp_path):
    root = mig_tree(tmp_path / "gap", ("2.1", "2.2", "2.4", "2.5"))   # v2.3_to_v2.4 missing
    with pytest.raises(NeedsHuman, match="no migration chain") as ei:
        migrate.plan(ctx, "dev", root, "2.2.0", "2.6.0")
    assert ei.value.detail["reached"] == "2.3"
    assert ei.value.detail["gap"] == ["v2.4_to_v2.5", "v2.5_to_v2.6"]


def test_downgrade_names_the_checkpoint_to_restore(ctx, full, ledger):
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.5.3-1", "result": "SUCCEEDED",
                                  "vamsVersion": "2.5.3", "checkpoint": "ckpt-old", "migrationsApplied": []})
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.6.6-1", "result": "SUCCEEDED",
                                  "vamsVersion": "2.6.6", "checkpoint": "ckpt-a",
                                  "migrationsApplied": ["v2.5_to_v2.6"]})
    # migrated, restored, migrated again: the newer pre-deploy checkpoint is the one to restore
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.6.6-2", "result": "SUCCEEDED",
                                  "vamsVersion": "2.6.6", "checkpoint": "ckpt-b",
                                  "migrationsApplied": ["v2.5_to_v2.6"]})
    with pytest.raises(NeedsHuman) as ei:
        migrate.plan(ctx, "dev", full, "2.6.6", "2.5.3")
    assert ei.value.detail["checkpoint"] == "ckpt-b"
    assert "restore checkpoint ckpt-b" in ei.value.message and "--restore-checkpoint" in ei.value.message


# ----------------------------------------------------------------- D16: rollback across a migrated boundary
def _bundle(ctx, build_id, version, starts=ALL):
    bdir = build_mod.build_dir(ctx, build_id)
    mig_tree(bdir / "migrations", starts)
    return {"buildId": build_id, "vamsVersion": version}


def test_rollback_to_an_older_minor_across_a_migration_stops(ctx, ledger):
    _bundle(ctx, "cust-v2.6.6-1", "2.6.6")
    old = _bundle(ctx, "cust-v2.5.3-1", "2.5.3")
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.5.3-1", "result": "SUCCEEDED",
                                  "vamsVersion": "2.5.3", "checkpoint": "ckpt-0", "migrationsApplied": []})
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.6.6-1", "result": "SUCCEEDED",
                                  "vamsVersion": "2.6.6", "checkpoint": "ckpt-1",
                                  "migrationsApplied": ["v2.5_to_v2.6"]})
    with pytest.raises(NeedsHuman, match="crosses data migrations v2.5_to_v2.6") as ei:
        deploy.rollback_downgrade_check(ctx, "dev", old, "cust-v2.6.6-1")
    assert ei.value.detail["checkpoint"] == "ckpt-1"
    # same minor, or a newer one: no stop
    assert deploy.rollback_downgrade_check(ctx, "dev", {"buildId": "x", "vamsVersion": "2.6.1"},
                                           "cust-v2.6.6-1")["downgrade"] is False


def test_rollback_after_a_failed_deploy_that_migrated_stops(ctx, ledger):
    """rollbackPolicy ask: the failed deploy migrated the data, the last good deploy is still the old build; a
    `vra rollback` to it compares equal versions but must not run the old code over the migrated data."""
    old = _bundle(ctx, "cust-v2.5.3-1", "2.5.3")
    _bundle(ctx, "cust-v2.6.6-1", "2.6.6")
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.5.3-1", "result": "SUCCEEDED",
                                  "vamsVersion": "2.5.3", "migrationsApplied": []})
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.6.6-1", "result": "FAILED",
                                  "vamsVersion": "2.6.6", "checkpoint": "ckpt-pre",
                                  "migrationsApplied": ["v2.5_to_v2.6"]})
    with pytest.raises(NeedsHuman) as ei:
        deploy.rollback_downgrade_check(ctx, "dev", old, "cust-v2.6.6-1")
    assert ei.value.detail["crossed"] == ["v2.5_to_v2.6"] and ei.value.detail["checkpoint"] == "ckpt-pre"
    assert "2.6.6 -> 2.5.3" in ei.value.message


def test_rollback_workflow_stops_before_cdk_deploy(ctx, repos, calls, ledger):
    from test_build_deploy import FakeCfn, _build
    b = _build(ctx).run.state["result"]["buildId"]                      # VAMS 2.6.5
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    assert runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev").exit_code == 0
    ctx.run_id = None
    # a later 2.7 build that applied a (hypothetical) v2.6_to_v2.7 migration
    _bundle(ctx, "cust-v2.7.0-1", "2.7.0", ALL + ("2.6",))
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.7.0-1", "result": "SUCCEEDED",
                                  "vamsVersion": "2.7.0", "checkpoint": "ckpt-27",
                                  "migrationsApplied": ["v2.6_to_v2.7"]})
    calls.clear()
    out = runner.start(ctx, "rollback", {"env": "dev", "toBuild": b}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN, out.message
    assert "v2.6_to_v2.7" in out.message and "ckpt-27" in out.message
    assert not any("deploy" in c["argv"] and "--app" in c["argv"] for c in calls)


def test_auto_rollback_after_a_failed_migrating_deploy_restores_the_checkpoint(ctx, monkeypatch):
    """The automatic rollback keeps its behaviour: migrations applied -> it restores the pre-deploy checkpoint, so
    the downgrade check (which runs only without --restore-checkpoint) never stops it."""
    seen = {}

    def fake_rollback(ctx_, run_, env, **kw):
        seen.update(kw)
        return {"build": "cust-v2.5.3-1"}
    monkeypatch.setattr(deploy, "perform_rollback", fake_rollback)

    class R:
        run_id = "r-1"
        state = {"migrationsApplied": ["v2.5_to_v2.6"]}
    with pytest.raises(errors.RolledBack):
        deploy._on_failure(ctx, R(), "dev", {"buildId": "cust-v2.6.6-1", "vamsVersion": "2.6.6"},
                           errors.TestsFailed("smoke failed"), {"mode": "in-place"}, {"id": "ckpt-pre"}, "cli",
                           "deploy.dev.")
    assert seen["restore_checkpoint"] == "ckpt-pre"
    rec = ctx.ledger.history("dev")[-1]
    assert rec["result"] == "FAILED" and rec["migrationsApplied"] == ["v2.5_to_v2.6"]
    assert rec["checkpoint"] == "ckpt-pre" and rec["vamsVersion"] == "2.6.6"
