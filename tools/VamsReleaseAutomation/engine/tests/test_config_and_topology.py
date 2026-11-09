# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Customer configurations and topologies: config identity, partitions, synth isolation, FIPS, live-test sign-in,
A/B on ALB, tiers, chained auto envs, generic CI, and the validate rules around them."""

from __future__ import annotations

import subprocess
import sys

import pytest

from vra import approvals, aws as aws_mod, build as build_mod, deploy, errors, gitops, identity, setup as setup_mod
from vra import tests_runner, util, watch
from vra.errors import NeedsHuman, UsageError
from vra.ledger import Ledger, is_prod_class, partition_for_region, tier

from conftest import FakeAws, env_doc, write_ledger
from test_gates import approve, fake_build, passed_test


def set_env(ledger, env, **kv):
    d = util.read_yaml(ledger.root / f"envs/{env}/env.yaml")
    d.update(kv)
    util.write_yaml(ledger.root / f"envs/{env}/env.yaml", d)
    ledger._envs.clear()


def set_cfg(ledger, env, patch):
    p = ledger.overlay_dir(env) / "infra/config/config.json"
    util.write_json(p, util.deep_merge(util.read_json(p), patch))
    ledger._envs.clear()


# ------------------------------------------------------------------ partitions
@pytest.mark.parametrize("region,part", [("eusc-de-east-1", "aws-eusc"), ("eu-isoe-west-1", "aws-iso-e"),
                                         ("us-isof-south-1", "aws-iso-f"), ("il-central-1", "aws"),
                                         ("us-gov-west-1", "aws-us-gov"), ("cn-north-1", "aws-cn")])
def test_every_partition_resolves(region, part):
    assert partition_for_region(region) == part
    assert aws_mod.dns_suffix(part)


def test_eu_sovereign_env_validates(repos, tmp_path):
    d = env_doc("dev", region="eusc-de-east-1")
    d.update(partition="aws-eusc", deployRoleArn="arn:aws-eusc:iam::111111111111:role/vra-deploy",
             templateBase="eusovereign", registeredBuckets={"granted": False})
    lg = write_ledger(tmp_path / "l", str(repos["fork"]), str(repos["upstream"]), envs={"dev": d})
    res = lg.validate()
    assert res["ok"], res["errors"]


# ------------------------------------------------------------------ synth isolation (names + environment)
def test_synth_environment_is_an_allowlist_and_refuses_vams_overrides(ctx, ledger, monkeypatch):
    monkeypatch.setenv("SOME_RANDOM_TOKEN", "x")
    env, keys = build_mod.synth_environment(ctx, "dev", {"AWS_REGION": "us-east-1", "AWS_USE_FIPS_ENDPOINT": "true"})
    assert env[util.CLEAN_ENV] == "1" and "SOME_RANDOM_TOKEN" not in env and "AWS_USE_FIPS_ENDPOINT" not in env
    assert "PATH" in keys and "AWS_REGION" in keys
    monkeypatch.setenv("ADMIN_EMAIL_ADDRESS", "someone@example.com")
    with pytest.raises(NeedsHuman, match="ADMIN_EMAIL_ADDRESS"):
        build_mod.synth_environment(ctx, "dev", {})
    set_env(ledger, "dev", synthEnv={"ADMIN_EMAIL_ADDRESS": "admin@example.com"})
    env, _ = build_mod.synth_environment(ctx, "dev", {})
    assert env["ADMIN_EMAIL_ADDRESS"] == "admin@example.com"  # the ledger's value, not the host's


def test_shell_starts_from_nothing_for_a_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("STACK_NAME", "from-host")
    out = util.Shell().run([sys.executable, "-c", "import os; print(os.environ.get('STACK_NAME'))"],
                           env={util.CLEAN_ENV: "1", "PATH": "/usr/bin:/bin"})
    assert out.stdout.strip() == "None"


def test_synth_passes_names_region_and_config_file(ctx, ledger, calls):
    from test_build_deploy import _build
    _build(ctx)
    synths = [c for c in calls if "synth" in c["argv"]]
    assert synths
    for c in synths:
        a = c["argv"]
        pairs = {a[i + 1].split("=", 1)[0]: a[i + 1].split("=", 1)[1] for i, x in enumerate(a) if x == "-c"}
        assert pairs["configFile"] == "config.json" and pairs["region"] == "us-east-1"
        assert pairs["stack-name"] and pairs["account"].isdigit()
        assert c["env"].get(util.CLEAN_ENV) == "1"


def test_deploy_refuses_a_bundle_whose_core_stack_is_not_the_ledgers(ctx, ledger):
    m = {"targets": {"dev": {"stacks": ["vams-core-demo8-us-east-1", "vams-web-demo8-us-east-1"]}}}
    with pytest.raises(NeedsHuman, match="ledger names vams-core-dev-us-east-1"):
        deploy.assembly_matches_ledger(ctx, "dev", m, "dev", None)
    m["targets"]["dev"]["stacks"] = ["vams-core-dev-us-east-1"]
    assert deploy.assembly_matches_ledger(ctx, "dev", m, "dev", None)["checked"]


# ------------------------------------------------------------------ FIPS
def test_fips_follows_the_env(ledger):
    f = aws_mod.AwsFactory(ledger)
    assert not f._fips("dev")
    set_cfg(ledger, "dev", {"app": {"useFips": True}})
    assert f._fips("dev")
    set_env(ledger, "dev", fips=False)
    assert not f._fips("dev")
    e = {"region": "eusc-de-east-1", "fips": "auto"}
    assert not aws_mod.fips_on(e, {"app": {"useFips": True}})  # no FIPS endpoints in that partition


# ------------------------------------------------------------------ live-test sign-in
class _SM:
    def __init__(self, doc):
        self.doc = doc

    def get_secret_value(self, SecretId):
        import json
        return {"SecretString": json.dumps(self.doc)}


def test_live_sign_in_password_api_key_and_token(ctx, ledger, tmp_path):
    ctx.aws = FakeAws(ledger, {"secretsmanager": _SM({"userId": "ci@example.com", "apiKey": "vams_abcdefgh123"})})
    set_env(ledger, "dev", liveTest={"webUrl": "https://dev.example.com", "auth": {"type": "apiKey", "secret": "s"}})
    a = tests_runner.live_auth(ctx, "dev")
    name, argv, stdin = tests_runner.login_step("vamscli", "p", a)
    assert "--token-override-stdin" in argv and "--user-id" in argv and stdin == "vams_abcdefgh123\n"
    assert "vams_abcdefgh123" not in " ".join(argv) and util.redact("key vams_abcdefgh123") == "key ***"
    script = tmp_path / "tok.py"
    script.write_text("print('tok-1234567890')\n")
    set_env(ledger, "dev", liveTest={"auth": {"type": "token", "userId": "u1", "tokenCommand": [sys.executable,
                                                                                             str(script)]}})
    a = tests_runner.live_auth(ctx, "dev")
    assert a["token"] == "tok-1234567890" and tests_runner.login_step("vamscli", "p", a)[1][-3:] != ["-u"]
    ctx.aws = FakeAws(ledger, {"secretsmanager": _SM({"username": "u", "password": "pw-123456"})})
    set_env(ledger, "dev", liveTest={"credentialsSecret": "legacy"})
    assert tests_runner.login_step("vamscli", "p", tests_runner.live_auth(ctx, "dev"))[1][4:7] == ["login", "-u", "u"]


class _SSM:
    def get_parameter(self, Name):
        assert Name == "/vams-dev-us-east-1/web/deployedUrl"
        return {"Parameter": {"Value": "https://d1.cloudfront.example.com"}}


def test_web_url_comes_from_ssm_when_unset(ctx, ledger):
    set_env(ledger, "dev", liveTest={})
    ctx.aws = FakeAws(ledger, {"ssm": _SSM()})
    assert tests_runner.resolve_live(ctx, "dev")["webUrl"] == "https://d1.cloudfront.example.com"


# ------------------------------------------------------------------ A/B on ALB
def test_ab_on_alb_needs_a_domain_host_per_slot(ledger):
    set_cfg(ledger, "test", {"app": {"useAlb": {"enabled": True, "domainHost": "vams.customer.test"}}})
    set_env(ledger, "test", deployMode="ab", ab={"slots": {"blue": "testb", "green": "testg"}})
    res = Ledger(ledger.root).validate()
    assert any("A/B on the ALB path" in e for e in res["errors"])
    set_env(ledger, "test", ab={"slots": {"blue": "testb", "green": "testg"}, "slotOverlays": {
        "blue": {"app": {"useAlb": {"domainHost": "blue.vams.customer.test"}}},
        "green": {"app": {"useAlb": {"domainHost": "green.vams.customer.test"}}}}})
    assert not any("A/B on the ALB path" in e for e in Ledger(ledger.root).validate()["errors"])


def test_slot_overlay_is_merged_into_that_slots_config(ctx, ledger, tmp_path):
    set_env(ledger, "test", ab={"slots": {"blue": "testb", "green": "testg"}, "slotOverlays": {
        "green": {"app": {"useAlb": {"domainHost": "green.example.test"}}}}})
    src = tmp_path / "src"
    util.write_json(src / "infra/config/config.json", {"app": {"useAlb": {"enabled": True, "domainHost": "a"}}})
    assert build_mod.slot_overlay(ctx, src, "test", "green")
    assert util.read_json(src / "infra/config/config.json")["app"]["useAlb"] == {"enabled": True,
                                                                               "domainHost": "green.example.test"}


# ------------------------------------------------------------------ validate: naming and collisions
def test_names_must_be_lower_case_and_domains_unique(ledger):
    set_env(ledger, "dev", baseStackName="Dev")
    set_cfg(ledger, "dev", {"app": {"baseStackName": "Dev"}})
    set_cfg(ledger, "test", {"app": {"useAlb": {"domainHost": "vams.customer.test"}}})
    set_cfg(ledger, "prod", {"app": {"useAlb": {"domainHost": "vams.customer.test"}}})
    errs = Ledger(ledger.root).validate()["errors"]
    assert any("must be lower case" in e for e in errs)
    assert any("used by more than one env" in e for e in errs)


def test_gated_env_without_overlay_config_is_an_error(ledger):
    (ledger.overlay_dir("prod") / "infra/config/config.json").unlink()
    assert any("must carry its own VAMS config" in e for e in Ledger(ledger.root).validate()["errors"])


def test_release_auto_merge_and_cross_partition_promotion_are_refused(ledger):
    doc = util.read_yaml(ledger.root / "vra.yaml")
    doc["lanes"]["release"]["autoMerge"] = True
    util.write_yaml(ledger.root / "vra.yaml", doc)
    set_env(ledger, "prod", region="us-gov-west-1", partition="aws-us-gov",
            deployRoleArn="arn:aws-us-gov:iam::333333333333:role/vra-deploy",
            registeredBuckets={"granted": False})
    set_cfg(ledger, "prod", {"env": {"region": "us-gov-west-1"}})
    errs = Ledger(ledger.root).validate()["errors"]
    assert any("lanes.release.autoMerge" in e for e in errs)
    assert any("crosses partitions" in e for e in errs)


# ------------------------------------------------------------------ tiers
def test_wave_prods_keep_prod_class_with_an_explicit_tier(repos, tmp_path):
    envs = {"dev": env_doc("dev"), "test": env_doc("test"), "prod": env_doc("prod")}
    envs["prod"]["baseStackName"] = "produs"
    eu = env_doc("prod")
    eu.update(promotesFrom="prod", baseStackName="prodeu", region="eu-west-1")
    envs["prodeu"] = eu
    lg = write_ledger(tmp_path / "l", str(repos["fork"]), str(repos["upstream"]), envs=envs)
    assert not is_prod_class(lg, "prod") and is_prod_class(lg, "prodeu")      # derived: the wave env is lost
    assert any("without an explicit tier" in w for w in lg.validate()["warnings"])
    set_env(lg, "prod", tier="prod")
    assert is_prod_class(lg, "prod") and tier(lg, "test") == "nonprod"
    from vra.datacopy import target_allowed
    assert not target_allowed(lg, "prod")[0]


def test_setup_refuses_an_unknown_promotes_from_and_chains_custom_envs(ctx, repos):
    base = {"fork": {"url": str(repos["fork"])}, "githost": {"type": "local"}}
    with pytest.raises(UsageError, match="not one of the environments"):
        setup_mod.collect(ctx, {**base, "envs": {"dev": {"account": "111111111111"},
                                                 "prod": {"account": "333333333333", "approvers": ["a@example.com"],
                                                          "promotesFrom": "tset"}}}, False)
    got = setup_mod.collect(ctx, {**base, "envs": {"dev": {"account": "111111111111"},
                                                   "int": {"account": "111111111111", "approvers": ["a@example.com"]},
                                                   "staging": {"account": "222222222222",
                                                               "approvers": ["a@example.com"]}}}, False)
    assert got["envs"]["int"]["promotesFrom"] == "dev" and got["envs"]["staging"]["promotesFrom"] == "int"


# ------------------------------------------------------------------ chained auto envs
def test_ungated_promoted_env_takes_the_untested_sync_build_but_a_gated_one_needs_the_trunk(ctx, ledger, monkeypatch):
    set_env(ledger, "test", requiresApproval=False, approvers=[], autoDeploy=True, rollbackPolicy="auto")
    fake_build(ctx, "cust-v2.6.6-1")
    ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.6.6-1", "result": "SUCCEEDED"})
    passed_test(ledger, "dev", "cust-v2.6.6-1")
    monkeypatch.setattr(gitops, "ensure_repo", lambda c, *a, **k: type("G", (), {"run": lambda self, *a, **k: None})())
    monkeypatch.setattr(gitops, "on_trunk", lambda *a, **k: False)
    assert deploy.check_promotion(ctx, "dev", "test", "cust-v2.6.6-1")["to"] == "test"
    set_env(ledger, "test", requiresApproval=True, approvers=["qa-lead@example.com"], autoDeploy=False)
    with pytest.raises(NeedsHuman, match="not on main"):
        deploy.check_promotion(ctx, "dev", "test", "cust-v2.6.6-1")


def test_watch_uses_only_lanes_with_envs(ctx, ledger):
    assert watch.lanes_in_use(ctx) == ["release", "preview"]
    import shutil
    shutil.rmtree(ledger.root / "envs/dev-preview")
    ledger._envs.clear()
    assert watch.lanes_in_use(ctx) == ["release"]


# ------------------------------------------------------------------ generic CI and SoD
def test_generic_ci_identity_contract():
    i = identity.resolve(None, {"VRA_CI_PROVIDER": "generic", "VRA_INITIATOR": "jdoe", "VRA_RUN_REF": "jenkins:rel/42",
                                "VRA_APPROVAL_REF": "input-7"})
    assert (i.source, i.initiator, i.run_ref, i.verified) == ("generic", "jdoe", "jenkins:rel/42", True)
    assert i.attributes["VRA_APPROVAL_REF"] == "input-7"
    assert not identity.resolve(None, {"VRA_CI_PROVIDER": "generic", "VRA_INITIATOR": "jdoe"}).verified


def test_gated_env_refuses_generic_ci_unless_it_opts_in(ctx, ledger):
    ctx.identity = identity.resolve(None, {"VRA_CI_PROVIDER": "generic", "VRA_INITIATOR": "jdoe",
                                           "VRA_RUN_REF": "jenkins:rel/42"})
    rec = str(approve(ledger, "test", "b1", "qa-lead@example.com", source="jenkins:rel/42"))
    with pytest.raises(NeedsHuman, match="allowGenericCi"):
        approvals.check(ctx, "test", "b1", "promote", rec)
    set_env(ledger, "test", allowGenericCi=True)
    assert approvals.check(ctx, "test", "b1", "promote", rec)["approver"] == "qa-lead@example.com"
    ctx.identity = identity.resolve(None, {"VRA_CI_PROVIDER": "generic", "VRA_INITIATOR": "jdoe"})
    with pytest.raises(NeedsHuman, match="needs VRA_INITIATOR and VRA_RUN_REF"):
        approvals.check(ctx, "test", "b1", "promote", rec)


def test_commit_author_cannot_approve_the_promotion(ctx, ledger):
    ctx.identity = identity.resolve(None, {"VRA_CI_PROVIDER": "generic", "VRA_INITIATOR": "ci-bot",
                                           "VRA_RUN_REF": "r"})
    set_env(ledger, "test", allowGenericCi=True)
    rec = str(approve(ledger, "test", "b2", "qa-lead@example.com", source="r"))
    ctx.change_authors = ["qa-lead@example.com"]
    with pytest.raises(NeedsHuman, match="authored or committed"):
        approvals.check(ctx, "test", "b2", "promote", rec)


# ------------------------------------------------------------------ fork lanes / versions
def test_documented_fork_viewer_path_is_fork_owned(ledger):
    pol = gitops.PathPolicy(ledger.path_policy())
    assert pol.rule_for("web/src/visualizerPlugin/viewers/custom/WidgetViewerPlugin/WidgetViewerComponent.tsx")[
        "strategy"] == "fork"
    assert pol.rule_for("web/src/visualizerPlugin/config/customViewerConfig.json")["strategy"] == "fork"
    assert pol.rule_for("tools/VamsReleaseAutomation/engine/vra/cli.py")["strategy"] == "upstream"


def test_half_rolled_version_is_reported(tmp_path):
    from conftest import git
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "package.json").write_text('{"version": "2.6.5"}\n')
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "x")

    class G:
        def show(self, ref, rel):
            p = subprocess.run(["git", "-C", str(r), "show", f"{ref}:{rel}"], capture_output=True, text=True)
            return p.stdout if p.returncode == 0 else None
    w = build_mod.version_roll_warnings(G(), "HEAD", "2.6.6")
    assert len(w) == 1 and "package.json says 2.6.5" in w[0]
