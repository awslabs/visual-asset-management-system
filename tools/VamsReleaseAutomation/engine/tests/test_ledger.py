# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import datetime as dt
import json

import pytest

from vra import cron, util
from vra.errors import ConfigError, UsageError
from vra.ledger import Ledger, partition_for_region

from conftest import env_doc, write_ledger


def test_valid_ledger(ledger):
    res = ledger.validate()
    assert res["ok"], res["errors"]
    assert ledger.core_stack_name("dev") == "vams-core-dev-us-east-1"
    assert ledger.ssm_prefix("test") == "/vams-test-us-east-1/resourceNames"


def _errors(tmp_path, envs=None, **proj):
    lg = write_ledger(tmp_path / "lg", "file:///fork.git", "file:///up.git", envs, **proj)
    return lg.validate()["errors"]


def test_auto_and_approval_mutually_exclusive(tmp_path):
    errs = _errors(tmp_path, {"dev": env_doc("dev", requiresApproval=True, approvers=["a@example.com"])})
    assert any("mutually exclusive" in e for e in errs)


def test_promote_from_preview_rejected(tmp_path):
    envs = {"dev-preview": env_doc("dev-preview"), "test": env_doc("test", promotesFrom="dev-preview",
                                                                  dataCopy={"allowedSources": []})}
    errs = _errors(tmp_path, envs)
    assert any("preview-lane env (never promotable)" in e for e in errs)


def test_partition_region_mismatch(tmp_path):
    errs = _errors(tmp_path, {"dev": env_doc("dev", region="us-gov-west-1", partition="aws",
                                             deployRoleArn="arn:aws:iam::111111111111:role/x")})
    assert any("does not match region" in e for e in errs)


def test_govcloud_env_is_valid(tmp_path):
    lg = write_ledger(tmp_path / "lg", "file:///f.git", "file:///u.git",
                      {"dev": env_doc("dev", region="us-gov-west-1", templateBase="govcloud")})
    res = lg.validate()
    assert res["ok"], res["errors"]
    assert lg.env("dev")["deployRoleArn"].startswith("arn:aws-us-gov:")
    assert partition_for_region("eusc-de-east-1") == "aws-eusc"


def test_requires_approval_needs_approvers(tmp_path):
    errs = _errors(tmp_path, {"prod": env_doc("prod", approvers=[], promotesFrom=None)})
    assert any("at least one approver" in e for e in errs)


def test_schema_errors_bad_account(tmp_path):
    errs = _errors(tmp_path, {"dev": env_doc("dev", account="12345")})
    assert any("account" in e for e in errs)


def test_literal_secret_rejected(tmp_path):
    lg = write_ledger(tmp_path / "lg", "file:///f.git", "file:///u.git", {"dev": env_doc("dev")})
    cfg = lg.overlay_dir("dev") / "infra/config/config.json"
    data = util.read_json(cfg)
    data["app"]["authProvider"] = {"oidc": {"clientSecret": "s3cr3t-value"}}
    util.write_json(cfg, data)
    errs = lg.validate()["errors"]
    assert any("literal secret" in e for e in errs)


def test_overlay_region_mismatch(tmp_path):
    lg = write_ledger(tmp_path / "lg", "file:///f.git", "file:///u.git", {"dev": env_doc("dev")})
    cfg = lg.overlay_dir("dev") / "infra/config/config.json"
    data = util.read_json(cfg)
    data["env"]["region"] = "eu-west-1"
    util.write_json(cfg, data)
    assert any("env.region" in e for e in lg.validate()["errors"])


def test_upgrade_config_reports_new_and_removed(ledger):
    cfg = ledger.overlay_dir("dev") / "infra/config/config.json"
    data = util.read_json(cfg)
    data["app"]["oldKey"] = 1
    util.write_json(cfg, data)
    template = json.dumps({"name": "vams", "env": {"account": None, "region": None},
                           "app": {"baseStackName": "prod", "useWaf": True, "newFeature": {"on": False}}})
    res = Ledger(ledger.root).upgrade_config("dev", template, "v2.6.6")
    assert res["newKeys"] == ["app.newFeature"]
    assert res["removedKeys"] == ["app.oldKey"]
    assert res["proposed"]["app"]["baseStackName"] == "dev"  # customer value kept
    assert res["proposed"]["app"]["newFeature"] == {"on": False}


def test_import_refuses_secrets(ledger, tmp_path):
    src = tmp_path / "in.json"
    util.write_json(src, {"name": "vams", "env": {"region": "us-east-1"}, "app": {"password": "x"}})
    with pytest.raises(UsageError):
        ledger.import_config("dev", src)


def test_import_copies_config(ledger, tmp_path):
    src = tmp_path / "cfg" / "config.json"
    util.write_json(src, {"name": "vams", "env": {"region": "us-east-1", "account": "111111111111"},
                          "app": {"baseStackName": "dev"}})
    util.write_json(src.parent / "custom" / "customConfig.json", {"x": 1})
    res = ledger.import_config("dev", src)
    assert res["baseStackName"] == "dev"
    assert (ledger.overlay_dir("dev") / "infra/config/custom/customConfig.json").exists()


def test_import_of_another_deployment_is_refused(ledger, tmp_path):
    """env.yaml names stack base `dev`; a config that deploys `devx` would make the engine check, back up and
    migrate vams-core-dev-* while synth deploys vams-core-devx-*."""
    src = tmp_path / "cfg" / "config.json"
    util.write_json(src, {"name": "vams", "env": {"region": "us-east-1", "account": "111111111111"},
                          "app": {"baseStackName": "devx"}})
    before = (ledger.overlay_dir("dev") / "infra/config/config.json").read_text()
    with pytest.raises(UsageError, match="different deployment"):
        ledger.import_config("dev", src)
    assert (ledger.overlay_dir("dev") / "infra/config/config.json").read_text() == before


def test_import_fills_env_yaml_from_the_config(ledger, tmp_path):
    raw = util.read_yaml(ledger.env_dir("dev") / "env.yaml")
    raw.pop("baseStackName")
    util.write_yaml(ledger.env_dir("dev") / "env.yaml", raw)
    src = tmp_path / "cfg" / "config.json"
    util.write_json(src, {"name": "vams", "env": {"region": None, "account": None}, "app": {"baseStackName": "demo8"}})
    res = ledger.import_config("dev", src)
    assert res["envYamlUpdates"]["baseStackName"] == "demo8"
    assert util.read_yaml(ledger.env_dir("dev") / "env.yaml")["baseStackName"] == "demo8"
    cfg = util.read_json(ledger.overlay_dir("dev") / "infra/config/config.json")
    assert cfg["env"] == {"region": "us-east-1", "account": "111111111111"}  # never left to the host's region
    assert Ledger(ledger.root).core_stack_name("dev") == "vams-core-demo8-us-east-1"


def test_env_yaml_and_overlay_must_name_the_same_stack(ledger):
    cfg = util.read_json(ledger.overlay_dir("dev") / "infra/config/config.json")
    cfg["app"]["baseStackName"] = "demo8"
    util.write_json(ledger.overlay_dir("dev") / "infra/config/config.json", cfg)
    lg = Ledger(ledger.root)
    res = lg.validate()
    assert not res["ok"] and any("baseStackName 'dev' != overlay" in e for e in res["errors"])
    with pytest.raises(ConfigError, match="would not match"):
        lg.core_stack_name("dev")


def test_change_window():
    w = {"cron": "0 22 * * 2", "durationMinutes": 120, "timezone": "UTC"}  # Tue 22:00-24:00
    tue = dt.datetime(2026, 11, 3, 22, 30, tzinfo=dt.timezone.utc)
    assert cron.in_window(w, tue)
    assert not cron.in_window(w, tue.replace(hour=12))
    with pytest.raises(ValueError):
        cron.parse("* * *")
