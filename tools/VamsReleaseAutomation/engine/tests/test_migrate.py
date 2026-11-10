# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import sys

import pytest

from vra import migrate, runner, util
from vra.errors import NeedsHuman

from conftest import stub_inventory, vams_tree


@pytest.fixture
def mig_root(tmp_path):
    src = tmp_path / "vams"
    vams_tree(src, "2.6.6")
    return src / "infra/deploymentDataMigration"


def test_discover_reads_interfaces(mig_root):
    ts = {t["id"]: t for t in migrate.discover(mig_root)}
    assert sorted(ts) == ["v2.3_to_v2.4", "v2.4_to_v2.5", "v2.5_to_v2.6"]
    assert ts["v2.5_to_v2.6"]["interface"]["confirmAccount"] is True
    assert ts["v2.5_to_v2.6"]["interface"]["ssmPrefix"] is True
    assert ts["v2.4_to_v2.5"]["interface"]["confirmAccount"] is False
    assert ts["v2.4_to_v2.5"]["resolvesViaSsm"] is False  # from upgrade-matrix.yaml


def test_chain_2_4_to_2_6(mig_root):
    ts = migrate.discover(mig_root)
    assert [t["id"] for t in migrate.chain(ts, "2.4.3", "2.6.6")] == ["v2.4_to_v2.5", "v2.5_to_v2.6"]
    assert [t["id"] for t in migrate.chain(ts, "v2.3.0", "2.5.1")] == ["v2.3_to_v2.4", "v2.4_to_v2.5"]
    assert migrate.chain(ts, "2.6.1", "2.6.6") == []
    with pytest.raises(NeedsHuman, match="downgrade"):
        migrate.chain(ts, "2.6.0", "2.5.0")
    with pytest.raises(NeedsHuman, match="no migration chain"):
        migrate.chain(ts, "2.1.0", "2.6.0")


def test_pre_ssm_config_requires_overrides(ctx, mig_root):
    from vra.state import Run
    run = Run.create(ctx.work_dir, "migrate", {}, "me")
    p = migrate.plan(ctx, "dev", mig_root, "2.4.0", "2.6.6")
    step = p["steps"][0]
    with pytest.raises(NeedsHuman, match="asset_storage_table_name"):
        migrate.build_config(ctx, "dev", step, run.dir / "c.json", "/vams-dev-us-east-1/resourceNames")


def test_migrate_runs_chain_with_real_scripts(ctx, mig_root, ledger, tmp_path, monkeypatch):
    stub_inventory(monkeypatch)
    log = tmp_path / "mig.log"
    monkeypatch.setenv("MIGRATION_LOG", str(log))
    env = util.read_yaml(ledger.root / "envs/dev/env.yaml")
    env["migrations"] = {"configOverrides": {"v2.4_to_v2.5": {"asset_storage_table_name": "devAssetStorage"}}}
    util.write_yaml(ledger.root / "envs/dev/env.yaml", env)
    ledger._envs.clear()
    out = runner.start(ctx, "migrate", {"env": "dev", "from": "2.4.2", "to": "2.6.6", "source": str(mig_root)},
                       env="dev")
    assert out.exit_code == 0, out.message
    runs = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["script"] for r in runs] == ["v2.4_to_v2.5_migration.py"] * 2 + ["v2.5_to_v2.6_migration.py"] * 2
    dry, apply_ = runs[2]["argv"], runs[3]["argv"]
    assert "--dry-run" in dry and "--confirm-account" not in dry
    assert apply_[apply_.index("--confirm-account") + 1] == "111111111111" and "--yes" in apply_
    assert "--confirm-account" not in runs[1]["argv"]  # older script has no such flag
    cfg = util.read_json(out.run.dir / "migrations" / "v2.5_to_v2.6.config.json")
    assert cfg["resource_names_ssm_param_prefix"] == "/vams-dev-us-east-1/resourceNames"
    assert cfg["workflow_storage_table_name"] is None  # placeholder nulled -> script resolves via SSM
    assert util.read_json(out.run.dir / "migrations" / "v2.4_to_v2.5.config.json")["asset_storage_table_name"] == "devAssetStorage"
    assert ledger.history("dev")[-1]["applied"] == ["v2.4_to_v2.5", "v2.5_to_v2.6"]


def test_migrate_dry_run_only(ctx, mig_root, tmp_path, monkeypatch):
    stub_inventory(monkeypatch)
    log = tmp_path / "mig.log"
    monkeypatch.setenv("MIGRATION_LOG", str(log))
    out = runner.start(ctx, "migrate", {"env": "dev", "from": "2.5.0", "to": "2.6.6", "source": str(mig_root),
                                        "dryRun": True}, env="dev")
    assert out.exit_code == 0, out.message
    runs = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(runs) == 1 and "--dry-run" in runs[0]["argv"]


def test_standalone_migrate_on_gated_env_needs_approval(ctx, mig_root, tmp_path, monkeypatch):
    stub_inventory(monkeypatch)
    log = tmp_path / "mig.log"
    monkeypatch.setenv("MIGRATION_LOG", str(log))
    out = runner.start(ctx, "migrate", {"env": "test", "from": "2.5.0", "to": "2.6.6", "source": str(mig_root)},
                       env="test")
    assert out.exit_code == 20, out.message
    assert "approval" in out.message.lower()
    assert not log.exists() or not log.read_text().strip()  # no script ran


def test_standalone_migrate_takes_the_env_lock_before_its_approval_gate(ctx, mig_root, tmp_path, monkeypatch):
    """Live test: standalone migrate checked its approval before taking the env lock (deploy does lock, then gates)."""
    stub_inventory(monkeypatch)
    order = []
    from vra import approvals, runner as runner_mod
    real_lock, real_check = runner_mod.env_lock, approvals.check

    import contextlib

    @contextlib.contextmanager
    def spy_lock(c, run, env):
        order.append("lock")
        with real_lock(c, run, env):
            yield

    def spy_check(*a, **k):
        order.append("gate")
        return real_check(*a, **k)
    monkeypatch.setattr("vra.migrate.env_lock", spy_lock)
    monkeypatch.setattr(approvals, "check", spy_check)
    out = runner.start(ctx, "migrate", {"env": "test", "from": "2.5.0", "to": "2.6.6", "source": str(mig_root)},
                       env="test")
    assert out.exit_code == 20 and order[:2] == ["lock", "gate"], (order, out.message)


def test_standalone_migrate_dry_run_on_gated_env_needs_no_approval(ctx, mig_root, tmp_path, monkeypatch):
    stub_inventory(monkeypatch)
    log = tmp_path / "mig.log"
    monkeypatch.setenv("MIGRATION_LOG", str(log))
    out = runner.start(ctx, "migrate", {"env": "test", "from": "2.5.0", "to": "2.6.6", "source": str(mig_root),
                                        "dryRun": True}, env="test")
    assert out.exit_code == 0, out.message


# --- standalone migrate on an env with no vra deploy history (live test O2) ------------------------------------------

class _CoreStack:
    """describe_stacks of an imported env: the core stack is live, its description names the VAMS version."""

    def __init__(self, description: str | None):
        self.description = description

    def describe_stacks(self, StackName):
        if self.description is None:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "ValidationError",
                                         "Message": f"Stack with id {StackName} does not exist"}}, "DescribeStacks")
        return {"Stacks": [{"StackName": StackName, "StackStatus": "UPDATE_COMPLETE",
                            "Description": self.description}]}


def _imported(ctx, description):
    ctx.aws.clients["cloudformation"] = _CoreStack(description)
    assert ctx.ledger.current_deploy("dev") is None  # imported: no vra history


def test_migrate_on_imported_env_reads_from_stack_and_to_from_source_tree(ctx, mig_root, monkeypatch):
    """Live test O2: with no vra history, `vra migrate --source` refused without --from/--to (exit 2). `from` now
    comes from the core stack description (as `vra deploy` reads it), `to` from the tree's VAMS_VERSION."""
    stub_inventory(monkeypatch)
    _imported(ctx, "(SO9299) (VAMS-CORE) (version:2.5.3) Primary Components")
    out = runner.start(ctx, "migrate", {"env": "dev", "source": str(mig_root), "dryRun": True}, env="dev")
    assert out.exit_code == 0, out.message
    rv = out.run.find_step("migrate.versions")["result"]
    assert (rv["from"], rv["to"]) == ("2.5.3", "2.6.6")
    assert rv["fromSource"].startswith("stack description") and "config.ts" in rv["toSource"]
    plan = out.run.find_step("migrate.plan")["result"]
    assert [s["id"] for s in plan["steps"]] == ["v2.5_to_v2.6"]


def test_migrate_on_imported_env_defaults_to_the_newest_build_for_the_env(ctx, mig_root, monkeypatch):
    stub_inventory(monkeypatch)
    _imported(ctx, "(VAMS-CORE) (version:2.5.1)")
    import shutil
    for bid, created, version in (("cust-v2.5.0-1", "2026-01-01T00:00:00Z", "2.5.0"),
                                  ("cust-v2.6.6-1", "2026-02-01T00:00:00Z", "2.6.6")):
        bdir = ctx.artifacts_dir / bid
        shutil.copytree(mig_root, bdir / "migrations")
        util.write_json(bdir / "manifest.json", {"buildId": bid, "createdAt": created, "vamsVersion": version,
                                                 "targets": {"dev": {"env": "dev"}}})
    out = runner.start(ctx, "migrate", {"env": "dev", "dryRun": True}, env="dev")
    assert out.exit_code == 0, out.message
    rv = out.run.find_step("migrate.versions")["result"]
    assert (rv["build"], rv["from"], rv["to"]) == ("cust-v2.6.6-1", "2.5.1", "2.6.6")
    assert [s["id"] for s in out.run.find_step("migrate.plan")["result"]["steps"]] == ["v2.5_to_v2.6"]


def test_migrate_on_imported_env_without_build_or_source_names_the_flags(ctx, monkeypatch):
    stub_inventory(monkeypatch)
    _imported(ctx, "(VAMS-CORE) (version:2.5.3)")
    out = runner.start(ctx, "migrate", {"env": "dev", "dryRun": True}, env="dev")
    assert out.exit_code == 20 and "--build" in out.message and "--source" in out.message, out.message


def test_migrate_on_imported_env_with_unreadable_stack_version_needs_from(ctx, mig_root, monkeypatch):
    stub_inventory(monkeypatch)
    _imported(ctx, "some stack")
    out = runner.start(ctx, "migrate", {"env": "dev", "source": str(mig_root), "dryRun": True}, env="dev")
    assert out.exit_code == 20 and "--from" in out.message and "VAMS version" in out.message, out.message
    ctx.run_id = None
    _imported(ctx, None)  # no core stack at all
    out = runner.start(ctx, "migrate", {"env": "dev", "source": str(mig_root), "dryRun": True}, env="dev")
    assert out.exit_code == 20 and "--from" in out.message and "no deployed core stack" in out.message, out.message
