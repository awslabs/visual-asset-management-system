# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from vra import cli, errors, setup as setup_mod, util
from vra.context import Context
from vra.errors import UsageError
from vra.ledger import Ledger

from conftest import FakeAws, write_ledger

ANSWERS = {
    "project": {"name": "example-fork"},
    "fork": "https://git.example.com/acme/vams-fork.git",
    "githost": {"type": "gitlab"},
    "tagPrefix": "acme-",
    "notifications": {"type": "teams-webhook", "target": "vra/teams-webhook"},
    "envs": {
        "dev": {"account": "111111111111", "region": "us-gov-west-1"},
        "dev-preview": {"account": "111111111111", "region": "us-gov-west-1"},
        "test": {"account": "222222222222", "region": "us-gov-west-1", "approvers": "qa-lead@example.com"},
        "prod": {"account": "333333333333", "region": "us-gov-east-1", "approvers": ["release-manager@example.com"],
                 "changeWindow": "0 22 * * 6"},
    },
}


def _ctx(tmp_path):
    lg = Ledger(tmp_path / "ledger")
    c = Context(ledger=lg, work_dir=tmp_path / "work", non_interactive=True, initiator="op@example.com")
    c.aws = FakeAws(lg)
    return c


def test_setup_from_answers_govcloud(tmp_path):
    ans = tmp_path / "answers.yaml"
    util.write_yaml(ans, ANSWERS)
    ctx = _ctx(tmp_path)
    res = setup_mod.run_setup(ctx, str(ans), skip_preflight=True)
    assert res["validation"]["ok"], res["validation"]["errors"]
    lg = Ledger(tmp_path / "ledger")
    assert lg.project["githost"] == {"type": "gitlab", "repo": "acme/vams-fork", "apiUrl": None,
                                     "pushVendorBranches": True}
    dev = lg.env("dev")
    assert dev["partition"] == "aws-us-gov" and dev["templateBase"] == "govcloud"
    assert dev["deployRoleArn"] == "arn:aws-us-gov:iam::111111111111:role/vra-deploy"
    assert dev["autoDeploy"] and not dev["requiresApproval"]
    test = lg.env("test")
    assert test["promotesFrom"] == "dev" and test["requiresApproval"] and test["approvers"] == ["qa-lead@example.com"]
    assert test["dataCopy"]["allowedSources"] == ["prod"]
    assert lg.env("prod")["changeWindow"]["cron"] == "0 22 * * 6"
    assert lg.env("dev-preview")["lane"] == "preview"
    cfg = lg.vams_config("prod")
    assert cfg["env"] == {"account": "333333333333", "region": "us-gov-east-1"} and cfg["app"]["baseStackName"] == "prod"
    assert (lg.root / "path-policy.yaml").exists() and (lg.root / "tests/customer.yaml").exists()
    text = "\n".join(p.read_text() for p in lg.root.rglob("*") if p.is_file())
    assert "arn:aws:" not in text


def test_setup_missing_required(tmp_path):
    ans = tmp_path / "a.yaml"
    util.write_yaml(ans, {"fork": "https://git.example.com/a/b.git", "envs": {"dev": {}}})
    with pytest.raises(UsageError, match="envs.dev.account"):
        setup_mod.run_setup(_ctx(tmp_path), str(ans), skip_preflight=True)


def test_setup_interactive_prompts_every_value(tmp_path):
    asked = []

    def prompt(q, default):
        asked.append(q)
        if "account id" in q and "Tools" not in q:
            return "111111111111"
        if "Customer fork git URL" in q:
            return "https://github.example.com/acme/fork.git"
        if "Approver ids" in q:
            return "approver@example.com"
        return ""
    ctx = _ctx(tmp_path)
    ctx.prompt = prompt
    ctx.non_interactive = False
    res = setup_mod.run_setup(ctx, None, skip_preflight=True)
    assert res["validation"]["ok"], res["validation"]["errors"]
    joined = "\n".join(asked)
    for needle in ("Upstream VAMS git URL", "Git host", "Release tag prefix", "Notification channel", "State backend",
                   "[prod] AWS account id", "[prod] Partition", "[prod] Deploy role ARN", "[prod] VAMS config template",
                   "[test] Promotes from", "[test] Approver ids", "[dev] Rollback policy", "[test] Envs allowed to copy",
                   "[dev] Web URL for live tests", "[prod] Change window"):
        assert needle in joined, needle


def test_cli_ledger_validate_json(tmp_path, capsys, repos):
    write_ledger(tmp_path / "lg", str(repos["fork"]), str(repos["upstream"]))
    rc = cli.main(["--ledger", str(tmp_path / "lg"), "--work-dir", str(tmp_path / "w"), "--json", "ledger", "validate"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["result"]["ok"] is True
    # global flags also accepted after the subcommand
    rc = cli.main(["ledger", "show", "--env", "prod", "--ledger", str(tmp_path / "lg"), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["result"]["coreStack"] == "vams-core-prod-us-east-1"


def test_cli_usage_errors(tmp_path, capsys):
    assert cli.main(["deploy", "--env", "dev"]) == errors.USAGE_ERROR  # missing --build
    rc = cli.main(["--ledger", str(tmp_path / "missing"), "--json", "ledger", "validate"])
    assert rc == errors.USAGE_ERROR
    assert cli.main(["--ledger", str(tmp_path / "missing"), "preflight"]) == errors.USAGE_ERROR


def test_cli_status_and_print_cron(tmp_path, capsys, repos):
    write_ledger(tmp_path / "lg", str(repos["fork"]), str(repos["upstream"]))
    base = ["--ledger", str(tmp_path / "lg"), "--work-dir", str(tmp_path / "w"), "--json"]
    assert cli.main(base + ["status"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == {"runs": []}
    assert cli.main(base + ["watch", "--print-cron"]) == 0
    assert "vra --ledger" in json.loads(capsys.readouterr().out)["result"]["cron"]


def test_setup_gated_env_keys_and_approvals_readme(tmp_path):
    """Setup writes synthRoleArn, dataOwners and permissionsBoundaryName for gated envs only, and the
    approvals README documents the approval record (scope required, expiry, single use)."""
    answers = json.loads(json.dumps(ANSWERS))
    answers["envs"]["prod"].update({
        "synthRoleArn": "arn:aws-us-gov:iam::333333333333:role/vra-synth-prod",
        "dataOwners": "data-owner@example.com",
        "permissionsBoundaryName": "vra-role-boundary"})
    answers["envs"]["dev"]["synthRoleArn"] = "arn:aws-us-gov:iam::111111111111:role/ignored"
    ans = tmp_path / "answers.yaml"
    util.write_yaml(ans, answers)
    res = setup_mod.run_setup(_ctx(tmp_path), str(ans), skip_preflight=True)
    assert res["validation"]["ok"], res["validation"]["errors"]
    lg = Ledger(tmp_path / "ledger")
    prod = lg.env("prod")
    assert prod["synthRoleArn"].endswith("role/vra-synth-prod")
    assert prod["dataOwners"] == ["data-owner@example.com"]
    assert prod["permissionsBoundaryName"] == "vra-role-boundary"
    assert "synthRoleArn" not in lg.env("dev")          # ungated envs are not asked
    assert "dataOwners" not in lg.env("test")           # blank answer = the approvers, key omitted
    readme = (lg.root / "approvals/README.md").read_text()
    assert "scope, expiresAt?" in readme and "used once" in readme and "data-copy-to:<targetEnv>" in readme


def test_blank_strings_in_the_answers_file_mean_unset(tmp_path):
    """Live test O1: `synthRoleArn: ""` in an answers file was written to env.yaml and failed validation; a blank
    answer in the file is unset, the same as a blank answer at the prompt."""
    answers = json.loads(json.dumps(ANSWERS))
    answers["envs"]["prod"].update({"synthRoleArn": "", "permissionsBoundaryName": "  ", "dataOwners": ""})
    ans = tmp_path / "answers.yaml"
    util.write_yaml(ans, answers)
    res = setup_mod.run_setup(_ctx(tmp_path), str(ans), skip_preflight=True)
    assert res["validation"]["ok"], res["validation"]["errors"]
    prod = Ledger(tmp_path / "ledger").env("prod")
    assert not prod.get("synthRoleArn") and not prod.get("permissionsBoundaryName") and not prod.get("dataOwners")
