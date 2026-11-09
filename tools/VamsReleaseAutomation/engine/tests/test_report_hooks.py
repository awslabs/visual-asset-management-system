# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber

from vra import build as build_mod, deploy, errors, report, runner, tests_runner, util

from conftest import FakeAws
from test_build_deploy import FakeCfn, _build
from test_gates import fake_build


def test_customer_hooks_junit_and_env(ctx, ledger, tmp_path):
    hook = tmp_path / "hook.py"
    hook.write_text(textwrap.dedent('''\
        import os, pathlib
        d = pathlib.Path(os.environ["VRA_JUNIT_DIR"])
        ok = os.environ["VRA_ENV"] == "dev" and os.environ["VAMS_WEB_URL"] == "https://dev.vams.example.com"
        (d / "r.xml").write_text('<testsuite name="cust" tests="2" failures="%d"><testcase name="a"/>'
                                 '<testcase name="b">%s</testcase></testsuite>' % (0 if ok else 1, "" if ok else "<failure/>"))
    '''))
    util.write_yaml(ledger.root / "tests/customer.yaml", [
        {"name": "regression", "cmd": [sys.executable, str(hook)], "envs": ["dev"], "timeoutMin": 1},
        {"name": "prod-only", "cmd": [sys.executable, "-c", "raise SystemExit(1)"], "envs": ["prod"]},
        {"name": "failing", "cmd": [sys.executable, "-c", "raise SystemExit(3)"]}])
    env = util.read_yaml(ledger.root / "envs/dev/env.yaml")
    env["liveTest"] = {"webUrl": "https://dev.vams.example.com"}
    util.write_yaml(ledger.root / "envs/dev/env.yaml", env)
    ledger._envs.clear()
    out = runner.start(ctx, "test", {"env": "dev", "suites": ["customer"]}, env="dev")
    assert out.exit_code == errors.TESTS_FAILED
    summ = out.run.step_result("test.dev.customer")
    assert summ["hooks"] == {"regression": "passed", "failing": "failed"}
    agg = tests_runner.aggregate_junit(out.run.junit_dir)
    assert agg["tests"] == 3 and agg["failures"] == 1
    assert ledger.history("dev")[-1]["result"] == "FAILED"


def test_live_cli_smoke_password_on_stdin(ctx, ledger, calls):
    env = util.read_yaml(ledger.root / "envs/dev/env.yaml")
    env["liveTest"] = {"webUrl": "https://dev.vams.example.com", "credentialsSecret": "vra/dev/test-user"}
    util.write_yaml(ledger.root / "envs/dev/env.yaml", env)
    ledger._envs.clear()
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    st = Stubber(sm)
    st.add_response("get_secret_value", {"SecretString": json.dumps({"username": "smoke@example.com",
                                                                     "password": "pw-value"})},
                    {"SecretId": "vra/dev/test-user"})
    ctx.aws = FakeAws(ledger, {"secretsmanager": sm})
    with st:
        out = runner.start(ctx, "test", {"env": "dev", "suites": ["live-cli"]}, env="dev")
    assert out.exit_code == 0, out.message
    login = next(c for c in calls if "login" in c["argv"])
    assert "pw-value" not in " ".join(login["argv"]) and login["stdin"] == "pw-value\n"
    assert "--password-stdin" in login["argv"] and login["argv"][1:3] == ["--profile", "vra-dev"]
    assert "pw-value" not in "".join(p.read_text() for p in out.run.dir.rglob("*") if p.is_file())


def test_live_cli_smoke_without_vamscli_is_a_failed_case_not_a_crash(ctx, ledger, tmp_path):
    """E2E-050 B-050-18: in CodeBuild vamscli was not installed; the bare FileNotFoundError ended `vra deploy`
    after cdk deploy without a deploy record or the auto-rollback. It is a failed smoke case now, which the deploy's
    failure path handles like any failed test."""
    env = util.read_yaml(ledger.root / "envs/dev/env.yaml")
    env["liveTest"] = {"webUrl": "https://dev.vams.example.com"}
    util.write_yaml(ledger.root / "envs/dev/env.yaml", env)
    ledger._envs.clear()

    class NoCli:
        def run(self, argv, **kw):
            raise FileNotFoundError(2, "No such file or directory", argv[0])

    class Sc:
        log_path = tmp_path / "smoke.log"

    ctx.shell = lambda *a, **k: NoCli()
    jdir = tmp_path / "j"
    res = tests_runner.live_cli_smoke(ctx, "dev", jdir, Sc())
    assert res["cases"]["setup"] == "failed" and set(res["cases"].values()) == {"failed", "skipped"}
    summ = tests_runner.aggregate_junit(jdir)
    assert summ["failures"] == 1
    assert "vamscli not found" in (jdir / "live-cli.xml").read_text()


def test_build_packs_the_forks_vamscli_wheel_and_the_smoke_runs_exactly_that_wheel(ctx, ledger, tmp_path,
                                                                                    monkeypatch):
    """E2E-050 B-050-18a: the pipeline's CodeBuild jobs had no VamsCLI, so the post-deploy smoke could never run.
    `vra build` now packs a wheel built from the fork's tools/VamsCLI into the bundle (sha256 in the manifest); the
    smoke installs that wheel into its own venv after the hash matched and runs it. A changed wheel is refused, a
    build without one (an older bundle) falls through to the next build named (rollback: the build left)."""
    from vra.state import Run
    monkeypatch.delenv("VRA_VAMSCLI", raising=False)
    env = util.read_yaml(ledger.root / "envs/dev/env.yaml")
    env["liveTest"] = {"webUrl": "https://dev.vams.example.com"}
    util.write_yaml(ledger.root / "envs/dev/env.yaml", env)
    ledger._envs.clear()
    src = tmp_path / "src"
    (src / "tools/VamsCLI").mkdir(parents=True)
    (src / "tools/VamsCLI/pyproject.toml").write_text("[project]\nname = 'vamscli'\n")
    calls = []

    class P:
        returncode, stdout, stderr = 0, "{}", ""

    class Sh:
        def run(self, argv, **kw):
            argv = [str(a) for a in argv]
            calls.append(argv)
            if argv[1:3] == ["-m", "venv"]:
                (Path(argv[3]) / "bin").mkdir(parents=True, exist_ok=True)
                (Path(argv[3]) / "bin/python").write_text("")
            elif argv[1:4] == ["-m", "pip", "wheel"]:
                out = Path(argv[argv.index("-w") + 1])
                (out / "vamscli-2.6.6-py3-none-any.whl").write_bytes(b"wheel built from " + argv[-1].encode())
            elif argv[1:4] == ["-m", "pip", "install"]:
                exe = Path(argv[0]).parent / "vamscli"
                exe.write_text("")
            return P()

    ctx.shell = lambda *a, **k: Sh()
    run = Run.create(ctx.work_dir, "x", {}, "me")
    bdir = build_mod.build_dir(ctx, "b-new")
    info = run.step("vamscli", lambda sc: build_mod.vendor_vamscli(ctx, src, bdir, sc))
    assert info["vendored"] and info["wheel"] == "tools/vamscli/vamscli-2.6.6-py3-none-any.whl"
    assert info["sha256"] == util.sha256_file(bdir / info["wheel"]) and info["version"] == "2.6.6"
    assert calls[-1][-1] == str(src / "tools/VamsCLI") and "--no-deps" in calls[-1]
    util.write_json(bdir / "manifest.json", {"buildId": "b-new", "vamscli": info})
    old = build_mod.build_dir(ctx, "b-old")      # a bundle from before the wheel
    old.mkdir(parents=True)
    util.write_json(old / "manifest.json", {"buildId": "b-old"})

    calls.clear()
    res = run.step("smoke", lambda sc: tests_runner.live_cli_smoke(ctx, "dev", tmp_path / "j", sc,
                                                                    builds=[None, "b-old", "b-new"]))
    venv = ctx.work_dir / "vamscli" / info["sha256"][:16]
    assert res["cases"]["setup"] == "passed"
    assert any(c[1:4] == ["-m", "pip", "install"] and c[-1] == str(bdir / info["wheel"]) for c in calls)
    smoke = [c for c in calls if c[0].endswith("vamscli")]
    assert smoke and all(c[0] == str(venv / "bin/vamscli") for c in smoke)
    assert not any("--index-url" in c or c[-1] == "vamscli" for c in calls)   # never from a package index

    (bdir / info["wheel"]).write_bytes(b"something else")
    with pytest.raises(errors.VraError, match="does not match its manifest sha256"):
        tests_runner.resolve_vamscli(ctx, ["b-new"], type("Sc", (), {"log_path": tmp_path / "l", "log": print})())
    assert tests_runner.resolve_vamscli(ctx, ["b-old"], None) == "vamscli"   # no wheel anywhere: PATH
    monkeypatch.setenv("VRA_VAMSCLI", "/opt/own/vamscli")
    assert tests_runner.resolve_vamscli(ctx, ["b-new"], None) == "/opt/own/vamscli"


def test_post_deploy_smoke_reads_with_the_deploy_identity_not_the_test_role(ctx, ledger, tmp_path, monkeypatch):
    """E2E-050 B-050-27: on an env with ``testRoleArn`` the post-deploy smoke of a deploy (and of a rollback) read
    the web URL parameter and the sign-in secret as ``vra-test-<env>``. That role trusts only the env's test CI
    identity, so in the pipeline's deploy job the AssumeRole was denied and every gated deploy failed its smoke
    after a good `cdk deploy`. The deploy run's smoke now reads with its own deploy identity (the deploy role
    grants both reads); `vra test` keeps using the test role."""
    from vra.state import Run
    monkeypatch.setenv("VRA_VAMSCLI", "/opt/own/vamscli")
    env = util.read_yaml(ledger.root / "envs/test/env.yaml")
    env["testRoleArn"] = f"arn:aws:iam::{env['account']}:role/vra-test-test"
    env["liveTest"] = {"auth": {"type": "password", "secret": "vra/demo4/smoke-user"}}
    util.write_yaml(ledger.root / "envs/test/env.yaml", env)
    ledger._envs.clear()
    asked = []

    class Ssm:
        def get_parameter(self, Name):
            return {"Parameter": {"Value": "https://test.vams.example.com"}}

    class Sm:
        def get_secret_value(self, SecretId):
            return {"SecretString": json.dumps({"username": "smoke@example.com", "password": "pw-not-real"})}

    class TrustOnlyTestCi(FakeAws):
        def client(self, env_, service, role="deploy", region=None):
            asked.append((service, role))
            if role == "test":   # what the pipeline's deploy job gets from sts:AssumeRole on vra-test-<env>
                raise AssertionError("AccessDenied: sts:AssumeRole on vra-test-test from the deploy job")
            return {"ssm": Ssm(), "secretsmanager": Sm()}[service]

    class P:
        returncode, stdout, stderr = 0, "{}", ""

    class Sh:
        def run(self, argv, **kw):
            return P()

    ctx.aws = TrustOnlyTestCi(ledger)
    ctx.shell = lambda *a, **k: Sh()
    run = Run.create(ctx.work_dir, "x", {}, "me")
    res = run.step("smoke", lambda sc: deploy._smoke(ctx, run, "test", sc, None, []))
    assert res["summary"]["passed"] >= 1 and res["smoke"]["cases"]["login"] == "passed"
    assert asked and {r for _, r in asked} == {"deploy"}
    # `vra test` (a test job, where the test CI identity is trusted) still reads as the test role
    asked.clear()
    ctx.aws = FakeAws(ledger, {("test", "ssm", "test"): Ssm(), ("test", "secretsmanager", "test"): Sm()})
    assert tests_runner.resolve_live(ctx, "test")["webUrl"] == "https://test.vams.example.com"
    assert tests_runner.live_auth(ctx, "test")["username"] == "smoke@example.com"


@pytest.mark.parametrize("code,expected", [("AccessDeniedException", "PermissionDenied"),
                                           ("ResourceNotFoundException", "UsageError")])
def test_smoke_secret_read_errors_are_engine_errors_not_crashes(ctx, ledger, tmp_path, monkeypatch, code, expected):
    """E2E-050 B-050-28: the post-deploy smoke's secret read raised a bare botocore ClientError, which crashed the
    deploy run after `cdk deploy` (FAILED with a traceback, no deploy recorded, the env lock held dirty, and a
    pipeline retry could not resume it). A denial is now PermissionDenied (the run parks BLOCKED_ON_IT and a retry
    resumes it); any other error, e.g. the first deploy's smoke secret that does not exist yet, is a VraError, so
    the deploy's failure path records the deploy and follows rollbackPolicy."""
    from botocore.exceptions import ClientError
    from vra.state import Run
    monkeypatch.setenv("VRA_VAMSCLI", "/opt/own/vamscli")
    env = util.read_yaml(ledger.root / "envs/test/env.yaml")
    env["liveTest"] = {"webUrl": "https://test.vams.example.com",
                       "auth": {"type": "password", "secret": "vra/demo11/smoke-user"}}
    util.write_yaml(ledger.root / "envs/test/env.yaml", env)
    ledger._envs.clear()

    class Sm:
        def get_secret_value(self, SecretId):
            raise ClientError({"Error": {"Code": code, "Message": f"User: arn:aws:sts::111111111111:assumed-role/"
                                                                   f"vra-deploy-test/x is not authorized to perform: "
                                                                   f"secretsmanager:GetSecretValue on resource: "
                                                                   f"{SecretId}" if "Denied" in code else
                                                                   "Secrets Manager can't find the specified secret."}},
                              "GetSecretValue")

    ctx.aws = FakeAws(ledger, {("test", "secretsmanager"): Sm()})
    run = Run.create(ctx.work_dir, "x", {}, "me")
    with pytest.raises(errors.VraError) as info:
        run.step("smoke", lambda sc: deploy._smoke(ctx, run, "test", sc, None, []))
    assert type(info.value).__name__ == expected
    assert "vra/demo11/smoke-user" in info.value.message


def test_ledger_diff_against_deployed_bundle(ctx, repos, ledger):
    b = _build(ctx).run.state["result"]["buildId"]
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()})
    assert runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev").exit_code == 0
    cfg = ledger.overlay_dir("dev") / "infra/config/config.json"
    d = util.read_json(cfg)
    d["app"]["useWaf"] = False
    util.write_json(cfg, d)
    res = ledger.diff("dev", ctx.artifacts_dir)
    assert res["baseline"] == b and res["changed"] == ["infra/config/config.json"]
    assert '"useWaf": false' in res["diffs"]["infra/config/config.json"]


def test_report_and_notify(ctx, ledger):
    fake_build(ctx, "cust-v2.6.6-1")
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1"}, env="test")
    md = (out.run.dir / "report.md").read_text()
    assert "NEEDS_HUMAN" in md and "vra resume" in md
    rep = util.read_json(out.run.dir / "report.json")
    assert rep["metrics"][0]["env"] == "test"
    # sns: target topic ARN read from a secret NAME
    proj = util.read_yaml(ledger.root / "vra.yaml")
    proj["notifications"] = {"type": "sns", "target": "vra/notify-topic"}
    util.write_yaml(ledger.root / "vra.yaml", proj)
    ledger._project = None
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    sns = boto3.client("sns", region_name="us-east-1")
    s1, s2 = Stubber(sm), Stubber(sns)
    s1.add_response("get_secret_value", {"SecretString": "arn:aws-us-gov:sns:us-gov-west-1:111111111111:vra"},
                    {"SecretId": "vra/notify-topic"})
    s2.add_response("publish", {"MessageId": "m1"}, {"TopicArn": "arn:aws-us-gov:sns:us-gov-west-1:111111111111:vra",
                                                     "Subject": ANY, "Message": ANY})
    ctx.aws = FakeAws(ledger, {("tools", "secretsmanager"): sm, ("tools", "sns"): sns})
    with s1, s2:
        assert report.notify(ctx, out.run)["sent"] is True
    ctx.dry_run = True
    proj["notifications"] = {"type": "slack-webhook", "target": "vra/slack"}
    util.write_yaml(ledger.root / "vra.yaml", proj)
    ledger._project = None
    assert report.notify(ctx, out.run) == {"sent": False, "dryRun": True, "type": "slack-webhook",
                                           "subject": report.summary_line(rep)[:100], "bytes": len(md)}


def test_ab_slot_alternates(ctx, ledger):
    assert deploy.active_slot(ctx, "dev") is None
    ledger.append_history("dev", {"action": "deploy", "build": "b1", "result": "SUCCEEDED", "slot": "blue"})
    assert deploy.active_slot(ctx, "dev") == "blue"
    env = util.read_yaml(ledger.root / "envs/dev/env.yaml")
    env["ab"] = {"slots": {"blue": "devblue", "green": "devgreen"}}
    util.write_yaml(ledger.root / "envs/dev/env.yaml", env)
    ledger._envs.clear()
    assert build_mod.synth_targets(ctx, "release", ["dev"]) == [("dev", None), ("dev", "blue"), ("dev", "green")]
    assert ledger.core_stack_name("dev", "devgreen") == "vams-core-devgreen-us-east-1"
    fake_build(ctx, "cust-v2.6.6-1", envs=("dev", "dev@blue", "dev@green"))
    ctx.dry_run = True
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()})  # migration plan reads the live slot's version
    out = runner.start(ctx, "deploy", {"env": "dev", "build": "cust-v2.6.6-1", "mode": "ab"}, env="dev")
    # dry-run walks the A/B path to the DNS switch, which needs a hook (or a human)
    assert out.exit_code == errors.NEEDS_HUMAN and "dnsSwitchHook" in out.message
    assert out.run.step_result("deploy.dev.gate")["target"] == "dev@green"


def test_report_shows_data_copy_counts():
    """E2E finding (W8/W10): the data-copy report listed 50+ green steps but no item or object counts, although
    the W8 SOP's report step asks for them."""
    base = {"runId": "data-copy-x", "workflow": "data-copy", "status": "SUCCEEDED", "message": None,
            "initiator": "op", "startedAt": "t0", "endedAt": "t1", "steps": [], "tests": {"tests": 0},
            "itRequests": [], "approvals": [], "metrics": [], "notes": []}
    real = {"tables": {"dynamoTables/assetStorage": {"cleared": 22, "written": 26},
                       "dynamoTables/apiKeyStorage": {"skipped": True}},
            "buckets": [{"source": "src-assets", "target": "dst-assets", "copied": 115, "removed": 91}]}
    md = report.render_markdown({**base, "result": {"copy": real}})
    assert "## Data copy" in md and "cleared 22, written 26" in md
    assert "| `src-assets` | `dst-assets` | 115 | 91 |" in md and "`dynamoTables/apiKeyStorage`" in md
    plan = {"dryRun": True, "tables": {"dynamoTables/assetStorage": {"sourceItems": 26, "targetItems": 22}},
            "buckets": [{"source": "src-assets", "target": "dst-assets", "sourceObjects": 115, "targetObjects": 91}]}
    md = report.render_markdown({**base, "result": {"copy": plan}})
    assert "## Data copy (dry-run plan)" in md and "source items 26 would replace 22" in md


def test_report_shows_takeovers_and_human_authorizations():
    """E2E-040 (W10): the report of a deploy that machine B took over from a killed machine A did not mention the
    takeover or the approval that authorized it."""
    rep = {"runId": "deploy-x", "workflow": "deploy", "status": "SUCCEEDED", "message": None, "initiator": "op",
           "startedAt": "t0", "endedAt": "t1", "steps": [], "tests": {"tests": 0}, "itRequests": [], "approvals": [],
           "metrics": [], "notes": [],
           "takeovers": [{"at": "t2", "by": "op-b", "lock": "dev",
                          "previous": {"fromHost": "machine-a", "dirty": "cdk deploy of b1 to dev"}}],
           "humanAuthorizations": [{"action": "takeover", "by": "op-b", "how": "approval",
                                    "approval": "approvals/dev/human_takeover.yaml", "approver": "lead@example.com"}]}
    md = report.render_markdown(rep)
    assert "## Takeovers and human-only actions" in md
    assert "takeover of `dev` by `op-b` at t2 from host `machine-a` (dirty: cdk deploy of b1 to dev)" in md
    assert "approval `approvals/dev/human_takeover.yaml` by `lead@example.com`" in md
