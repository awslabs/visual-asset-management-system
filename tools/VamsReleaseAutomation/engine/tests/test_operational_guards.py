# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Data-copy target protection, manual-step confirmations, refreshable credentials for long deploys,
CLI convenience keys and the merge path policy."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from vra import aws as aws_mod, cli, datacopy, errors, gitops, runner, util
from vra.errors import NeedsHuman
from vra.runner import workflow
from vra.state import Run

PKG = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------- data-copy target rule
def test_prod_class_env_is_never_a_data_copy_target_by_default(ctx, ledger):
    ok, why = datacopy.target_allowed(ledger, "prod")
    assert not ok and "prod-class" in why
    assert datacopy.target_allowed(ledger, "test")[0]      # prod promotes from test
    assert datacopy.target_allowed(ledger, "dev")[0]       # not approval-gated
    out = runner.start(ctx, "data-copy", {"from": "test", "to": "prod", "approval": "x"}, env="prod")
    assert out.exit_code == errors.NEEDS_HUMAN and "data-copy refused" in out.message


def test_allow_as_target_flag_overrides_default(ctx, ledger):
    doc = util.read_yaml(ledger.root / "envs/test/env.yaml")
    doc["dataCopy"]["allowAsTarget"] = False
    util.write_yaml(ledger.root / "envs/test/env.yaml", doc)
    ledger._envs.clear()
    assert not datacopy.target_allowed(ledger, "test")[0]
    res = ledger.validate()
    assert any("allowAsTarget=false" in e for e in res["errors"]), res["errors"]
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "x"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "data-copy refused" in out.message


def test_validate_rejects_allowed_sources_on_prod_class_env(ledger):
    doc = util.read_yaml(ledger.root / "envs/prod/env.yaml")
    doc["dataCopy"] = {"allowedSources": ["test"]}
    util.write_yaml(ledger.root / "envs/prod/env.yaml", doc)
    ledger._envs.clear()
    res = ledger.validate()
    assert not res["ok"] and any("envs/prod: dataCopy.allowedSources is set" in e for e in res["errors"])


# ----------------------------------------------------------------- confirmations
_CALLS: list[str] = []


@workflow("_test-manual")
def _wf_manual(ctx, run, args):
    run.step("a", lambda sc: _CALLS.append("a") or "a")
    conf = run.step("manual", lambda sc: run.require_confirmation(sc.step["id"], "do the thing by hand"))
    run.step("b", lambda sc: _CALLS.append("b") or "b")
    return {"confirmedBy": conf["by"]}


def test_manual_step_needs_resume_confirm(ctx):
    _CALLS.clear()
    out = runner.start(ctx, "_test-manual", {})
    assert out.exit_code == errors.NEEDS_HUMAN and "--confirm" in out.message
    rid = out.run.run_id
    assert Run.load(ctx.work_dir, rid).state["pendingConfirmations"] == ["manual"]
    again = runner.resume(ctx, rid)                       # plain resume: still waiting
    assert again.exit_code == errors.NEEDS_HUMAN
    done = runner.resume(ctx, rid, confirm=True)
    assert done.exit_code == 0 and done.result["confirmedBy"] == ctx.initiator
    assert _CALLS == ["a", "b"]                           # step a ran once; b ran after confirmation
    with pytest.raises(errors.UsageError):
        runner.resume(ctx, rid, confirm=True)             # nothing pending any more


# ----------------------------------------------------------------- CLI output keys
def test_outcome_exposes_build_id_and_pr_url():
    o = runner.Outcome(None, 0, {"buildId": "cust-v2.6.6-1", "commit": "abc"})
    assert o.to_dict()["buildId"] == "cust-v2.6.6-1"
    o = runner.Outcome(None, 0, {"publish": {"pr": {"url": "https://git.example.com/pr/7", "id": 7}}})
    assert o.to_dict()["prUrl"] == "https://git.example.com/pr/7"


# ----------------------------------------------------------------- credentials
class _Ledger:
    root = Path("/tmp/ledger-example")

    def env(self, name):
        return {"region": "us-gov-west-1", "account": "111111111111",
                "deployRoleArn": "arn:aws-us-gov:iam::111111111111:role/vra-deploy-dev"}


def test_child_processes_get_credential_process_profile(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "BASEKEY")
    for k in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.delenv(k, raising=False)
    f = aws_mod.AwsFactory(_Ledger(), "run-1")
    env = f.subprocess_env("dev")
    assert env["AWS_ACCESS_KEY_ID"] == "" and env["VRA_BASE_AWS_ACCESS_KEY_ID"] == "BASEKEY"
    assert env["AWS_PROFILE"] == "vra-dev-deploy" and "AWS_SECRET_ACCESS_KEY" in env
    cfg = Path(env["AWS_CONFIG_FILE"]).read_text()
    assert "[profile vra-dev-deploy]" in cfg and "credential-process" in cfg and "--env dev" in cfg
    assert "BASEKEY" not in cfg                           # the config file never holds secrets
    child = dict(env)
    aws_mod.restore_base_env(child)
    assert child["AWS_ACCESS_KEY_ID"] == "BASEKEY" and "AWS_PROFILE" not in child and "AWS_CONFIG_FILE" not in child


def test_credential_process_children_never_fall_back_to_host_credentials(monkeypatch):
    """E2E-040: `cdk synth` with the synth role's credential_process profile ran as the build host's EC2 instance
    profile (another account) when the profile did not resolve inside CDK. A child with a vra profile gets no
    instance-metadata, container or web-identity fallback; the helper itself gets its base sources back."""
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/credentials/example")
    monkeypatch.delenv("AWS_EC2_METADATA_DISABLED", raising=False)
    monkeypatch.delenv("AWS_WEB_IDENTITY_TOKEN_FILE", raising=False)
    f = aws_mod.AwsFactory(_Ledger(), "run-1")
    env = f.subprocess_env("dev")
    assert env["AWS_EC2_METADATA_DISABLED"] == "true"
    assert env["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"] == "" and env["AWS_WEB_IDENTITY_TOKEN_FILE"] == ""
    child = dict(env)
    aws_mod.restore_base_env(child)          # inside `vra credential-process`: the base session's sources
    assert child["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"] == "/v2/credentials/example"
    assert "AWS_EC2_METADATA_DISABLED" not in child and "AWS_WEB_IDENTITY_TOKEN_FILE" not in child


def test_refreshable_session_refetches(monkeypatch):
    import boto3
    n = {"calls": 0}
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)  # inside botocore's refresh window

    def fetch():
        n["calls"] += 1
        return {"AccessKeyId": f"K{n['calls']}", "SecretAccessKey": "s", "SessionToken": "t", "Expiration": exp}

    sess = aws_mod.refreshable_session(boto3, fetch, "us-east-1")
    first = sess.get_credentials().get_frozen_credentials().access_key
    assert n["calls"] >= 2 and first.startswith("K")      # near-expiry credentials trigger a fresh AssumeRole


def test_credential_process_command(monkeypatch, capsys, ledger):
    exp = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(aws_mod.AwsFactory, "assume_fetcher", lambda self, e, r="deploy": (
        lambda: {"AccessKeyId": "AK", "SecretAccessKey": "SK", "SessionToken": "ST", "Expiration": exp}))
    nonce = ledger.root.parent / "nonce"
    nonce.write_text("n-1234567890")
    monkeypatch.setenv("VRA_CREDPROC_NONCE", "n-1234567890")
    rc = cli.main(["credential-process", "--ledger", str(ledger.root), "--env", "dev", "--nonce-file", str(nonce)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["Version"] == 1 and out["AccessKeyId"] == "AK" and out["Expiration"].startswith("2030")


def test_credential_process_refuses_without_the_engine_nonce(monkeypatch, capsys, ledger):
    """Run by hand (an agent's shell), the helper prints nothing: only vra's own child processes hold the nonce."""
    monkeypatch.setattr(aws_mod.AwsFactory, "assume_fetcher", lambda self, e, r="deploy": (
        lambda: {"AccessKeyId": "AK", "SecretAccessKey": "SK", "SessionToken": "ST", "Expiration": "x"}))
    rc = cli.main(["credential-process", "--ledger", str(ledger.root), "--env", "dev"])
    assert rc == 2 and capsys.readouterr().out == ""
    nonce = ledger.root.parent / "nonce"
    nonce.write_text("right")
    monkeypatch.setenv("VRA_CREDPROC_NONCE", "wrong")
    assert cli.main(["credential-process", "--ledger", str(ledger.root), "--env", "dev",
                     "--nonce-file", str(nonce)]) == 2


# ----------------------------------------------------------------- path policy / config
def test_custom_config_is_fork_owned_and_template_matches_default():
    from vra import data as vra_data
    pol = gitops.PathPolicy(util.read_yaml(vra_data.path("defaults", "path-policy.yaml")))
    assert pol.rule_for("infra/config/custom/customConfig.json")["strategy"] == "fork"
    assert pol.rule_for("infra/config/config.json")["strategy"] == "upstream"
    shipped = (PKG / "ledger-template" / "path-policy.yaml").read_bytes()
    assert shipped == Path(str(vra_data.path("defaults", "path-policy.yaml"))).read_bytes()


def test_upgrade_custom_config_adds_new_upstream_keys_only(ledger):
    p = ledger.overlay_dir("dev") / "infra/config/custom/customConfig.json"
    util.write_json(p, {"mine": {"a": 1}})
    res = ledger.upgrade_custom_config("dev", json.dumps({"mine": {"a": 0}, "newFeature": {"enabled": False}}), "v9.9.9")
    assert res["newKeys"] == ["newFeature"]
    assert res["proposed"] == {"mine": {"a": 1}, "newFeature": {"enabled": False}}


def test_approval_path_relative_to_ledger(ctx, ledger):
    from test_gates import approve
    from vra import approvals
    approve(ledger, "test", "data-copy:prod", "qa-lead@example.com")
    rel = ledger.approval_path("test", "data-copy_prod").relative_to(ledger.root)
    rec = approvals.check(ctx, "test", "data-copy:prod", "data-copy", str(rel))
    assert rec["approver"] == "qa-lead@example.com"


def test_validate_rejects_unknown_unit_suites(ledger):
    doc = util.read_yaml(ledger.root / "vra.yaml")
    doc.setdefault("build", {})["unitSuites"] = ["lint", "cli"]
    util.write_yaml(ledger.root / "vra.yaml", doc)
    ledger._project = None
    res = ledger.validate()
    assert any("build.unitSuites ['cli'] unknown" in e for e in res["errors"]), res["errors"]


def test_ledger_template_validates():
    from vra.ledger import Ledger
    res = Ledger(PKG / "ledger-template").validate()
    assert res["ok"], res["errors"]
