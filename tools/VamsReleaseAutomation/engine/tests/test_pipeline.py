# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""automation.mode ladder and `vra pipeline` (offline: fake shell for npm/cdk, fake or moto AWS clients).

- vra.yaml automation validation: the four modes, what each needs, what vra sets itself, dev-ownership conflicts.
- render: pipeline.json + ledger-graph.json for cicd/pipeline; the three-env example is what render produces.
- topology digest: the same value the CDK stack outputs (cicd/pipeline test/v050.test.ts reads the same file).
- plan: IT package for the pipeline's own roles, first-deploy / IAM / removed-approval gates, BLOCKED_ON_IT for
  roles IT has not created.
- deploy: refuses a template or ledger that moved since the plan; human-only first deploy (agents refused);
  an agent may deploy a plan without IAM or approval changes.
- status / sync-ledger; push-source (moto S3) uploads once per revision and starts the pipeline.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import boto3
import pytest
import yaml
from conftest import FakeAws, env_doc, fake_shell_cls, write_ledger

from vra import cli, errors, pipeline, pipeline_iam, util
from vra.context import Context

PKG = Path(__file__).resolve().parents[2]
EXAMPLE = PKG / "cicd" / "pipeline" / "examples" / "three-env"
ACCT = "111111111111"


def automation_block(**over) -> dict:
    a = yaml.safe_load((EXAMPLE / "vra-automation.yaml").read_text())["automation"]
    a.update(over)
    return a


def three_envs(arn_approvers: bool = True) -> dict:
    appr = (lambda n: [f"arn:aws:sts::{ACCT}:assumed-role/ReleaseApprover/{n}@example.com"]) if arn_approvers \
        else (lambda n: [f"{n}@example.com"])
    return {
        "dev": env_doc("dev", account=ACCT, autoDeploy=False),
        "test": env_doc("test", account=ACCT, approvers=appr("qa"), requiredSuites=["live-cli"]),
        "prod": env_doc("prod", account=ACCT, approvers=appr("rm"), requiredSuites=["live-cli"]),
    }


def make_ledger(repos, tmp_path, automation: dict | None = None, envs: dict | None = None, **proj):
    if automation is not None:
        proj["automation"] = automation
    return write_ledger(tmp_path / "ledger", str(repos["fork"]), str(repos["upstream"]),
                        envs=envs or three_envs(), toolsAccount=ACCT, **proj)


# ====================================================================== fakes
class FakeCfn:
    def __init__(self, stack: dict | None = None, template: dict | None = None):
        self.stack, self.template = stack, template

    def describe_stacks(self, StackName):
        if self.stack is None:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "ValidationError", "Message": f"Stack with id {StackName} does not "
                                                                                 "exist"}}, "DescribeStacks")
        return {"Stacks": [self.stack]}

    def get_template(self, StackName, TemplateStage):
        return {"TemplateBody": self.template or {}}


class FakeCodePipeline:
    def __init__(self):
        self.started = []

    def get_pipeline_state(self, name):
        return {"stageStates": [
            {"stageName": "Deploy-dev", "latestExecution": {"status": "Succeeded"}, "actionStates": []},
            {"stageName": "Approve-test", "latestExecution": {"status": "InProgress"},
             "actionStates": [{"actionName": "Approval-test", "latestExecution": {"status": "InProgress",
                                                                                   "token": "t"}}]}]}

    def list_pipeline_executions(self, pipelineName, maxResults):
        return {"pipelineExecutionSummaries": [{"pipelineExecutionId": "e1", "status": "InProgress"}]}

    def start_pipeline_execution(self, name):
        self.started.append(name)
        return {"pipelineExecutionId": f"exec-{len(self.started)}"}


ROLE_REPORT = {"roles": [
    {"roleConstructPath": "vra-pipeline/BuildRole", "roleName": "missing role", "missing": True,
     "assumeRolePolicy": [{"Action": "sts:AssumeRole", "Effect": "Allow",
                           "Principal": {"Service": "codebuild.amazonaws.com"}}],
     "managedPolicyArns": [], "managedPolicyStatements": [],
     "identityPolicyStatements": [
         {"Action": "logs:PutLogEvents", "Effect": "Allow",
          "Resource": "arn:(PARTITION):logs:(REGION):(ACCOUNT):log-group:/aws/codebuild/(vra-pipeline/Build/Resource.Ref)"},
         {"Action": "kms:Decrypt", "Effect": "Allow", "Resource": "(vra-pipeline/PipelineKey/Resource.Arn)"}]},
    {"roleConstructPath": "vra-pipeline/ReleasePipeline/Source/Fork/CodePipelineActionRole", "roleName": "missing role",
     "missing": True, "assumeRolePolicy": [{"Action": "sts:AssumeRole", "Effect": "Allow",
                                            "Principal": {"AWS": "arn:(PARTITION):iam::(ACCOUNT):root"}}],
     "managedPolicyArns": [], "managedPolicyStatements": [],
     "identityPolicyStatements": [{"Action": "codeconnections:UseConnection", "Effect": "Allow", "Resource": "*"}]},
]}


def template(approvals=("Approval-test", "FinalApproval-prod"), role_policy="a") -> dict:
    stages = [{"Name": f"Approve-{a.split('-')[-1]}", "Actions": [{"Name": a, "ActionTypeId": {"Category": "Approval"}}]}
              for a in approvals]
    return {"Resources": {
        "Build": {"Type": "AWS::CodeBuild::Project", "Properties": {"Name": "vra-build"},
                  "Metadata": {"aws:cdk:path": "vra-pipeline/Build/Resource"}},
        "BuildRole": {"Type": "AWS::IAM::Role", "Properties": {"Policy": role_policy}},
        "Release": {"Type": "AWS::CodePipeline::Pipeline", "Properties": {"Name": "vra-release", "Stages": stages}}}}


def cdk_handler(state: dict):
    """npm ci ok; cdk synth writes a template (and with vraIamReport the policy report); deploy/diff recorded."""
    def handler(argv, cwd, env):
        if argv[:2] == ["npm", "ci"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["npx", "cdk", "synth"]:
            out = Path(argv[argv.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            cfg_arg = next(a for a in argv if a.startswith("vraPipelineConfig="))
            state.setdefault("configs", []).append(json.loads(Path(cfg_arg.split("=", 1)[1]).read_text()))
            if "vraIamReport=true" in argv:
                (out / "iam-policy-report.json").write_text(json.dumps(ROLE_REPORT))
            elif state.get("synthFails"):
                return subprocess.CompletedProcess(argv, 1, "", "vra-pipeline/BuildRole (missing role)\n")
            t = state.get("template") or template()
            (out / "manifest.json").write_text(json.dumps({"artifacts": {"vra-pipeline": {
                "type": "aws:cloudformation:stack", "properties": {"templateFile": "vra-pipeline.template.json"}}}}))
            (out / "vra-pipeline.template.json").write_text(json.dumps(t))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] in (["npx", "cdk", "diff"], ["npx", "cdk", "deploy"]):
            state.setdefault("cdk", []).append(argv)
            return subprocess.CompletedProcess(argv, 0, "Stack vra-pipeline\nIAM Statement Changes\n", "")
        return None
    return handler


def make_ctx(ledger, tmp_path, cfn: FakeCfn | None = None, cp: FakeCodePipeline | None = None, state=None,
             moto=False):
    calls: list = []
    c = Context(ledger=ledger, work_dir=tmp_path / "work", initiator="dev-operator@example.com")
    clients = {("tools", "cloudformation"): cfn or FakeCfn(), ("tools", "codepipeline"): cp or FakeCodePipeline()}
    c.aws = FakeAws(ledger, clients, moto=moto)
    c.shell_cls = fake_shell_cls(calls, cdk_handler(state if state is not None else {}))
    c.calls = calls
    return c


# ====================================================================== the ladder
def test_agent_is_the_default_and_validates_without_an_automation_block(repos, tmp_path):
    L = make_ledger(repos, tmp_path)
    assert pipeline.mode(L.project) == "agent"
    res = L.validate()
    assert not [e for e in res["errors"] if "automation" in e]


@pytest.mark.parametrize("auto,needle", [
    ({"mode": "manual"}, "automation/mode"),
    ({"mode": "pipeline", "extra": 1}, "Additional properties"),
    ({"mode": "pipeline"}, "automation.pipeline is required for mode pipeline"),
    ({"mode": "pipeline+actions"}, "automation.pipeline is required for mode pipeline+actions"),
])
def test_invalid_automation_blocks_fail_ledger_validate(repos, tmp_path, auto, needle):
    L = make_ledger(repos, tmp_path, automation=auto)
    res = L.validate()
    assert not res["ok"] and any(needle in e for e in res["errors"]), res["errors"]


def test_pipeline_mode_requires_its_inputs_and_refuses_what_vra_sets(repos, tmp_path):
    a = automation_block()
    del a["pipeline"]["region"]
    a["pipeline"]["ledgerGraph"] = "x.json"
    a["pipeline"]["watch"]["command"] = "watch"
    a["pipeline"]["iamRoles"] = {"mode": "report"}
    L = make_ledger(repos, tmp_path, automation=a)
    errs, _ = pipeline.validate(L)
    joined = "\n".join(errs)
    assert "region is required" in joined
    assert "ledgerGraph is set by vra" in joined
    assert "watch.command watch deploys dev outside the pipeline" in joined
    assert "iamRoles.mode must be create or precreated" in joined


def test_hybrid_scope_and_state_store_key(repos, tmp_path):
    a = automation_block(mode="hybrid")
    a["pipeline"]["scope"] = "all"
    L = make_ledger(repos, tmp_path, automation=a, stateStore={"type": "s3", "bucket": "vra-state-x", "lockTable": "t"})
    errs, _ = pipeline.validate(L)
    assert any("scope must be dev with mode hybrid" in e for e in errs)
    assert any("stateStore.keyArn is required" in e for e in errs)


def test_warnings_for_two_dev_owners_and_non_arn_approvers(repos, tmp_path):
    envs = three_envs(arn_approvers=False)
    envs["dev"]["autoDeploy"] = True
    L = make_ledger(repos, tmp_path, automation=automation_block(), envs=envs)
    errs, warns = pipeline.validate(L)
    assert not errs, errs
    assert any("envs/dev: autoDeploy is true but the pipeline deploys dev" in w for w in warns)
    assert any("envs/test: no approver is an IAM ARN" in w for w in warns)
    agent = make_ledger(repos, tmp_path / "b", automation={"mode": "agent", "pipeline": {"region": "x"}})
    assert any("ignored with mode agent" in w for w in pipeline.validate(agent)[1])


def test_a_pipeline_env_without_a_deploy_role_fails_validate(repos, tmp_path):
    """B-050-7: the CodeBuild tier role can only assume deployRoleArn; without it CI runs as the tier role."""
    envs = three_envs()
    del envs["dev"]["deployRoleArn"]
    envs["test"]["profile"] = "test-admin"  # a local profile does not exist in CodeBuild
    del envs["test"]["deployRoleArn"]
    L = make_ledger(repos, tmp_path, automation=automation_block(), envs=envs)
    errs, _ = pipeline.validate(L)
    assert any(e.startswith("envs/dev: deployRoleArn is required with automation.mode pipeline") for e in errs), errs
    assert any(e.startswith("envs/test: deployRoleArn is required") for e in errs), errs
    assert not any(e.startswith("envs/prod:") for e in errs), errs
    # hybrid runs only the ungated root envs in CI: a gated env may keep a local profile
    hy = automation_block(mode="hybrid")
    hy["pipeline"]["scope"] = "dev"
    H = make_ledger(repos, tmp_path / "h", automation=hy, envs=envs)
    herrs, _ = pipeline.validate(H)
    assert any(e.startswith("envs/dev: deployRoleArn is required with automation.mode hybrid") for e in herrs)
    assert not any(e.startswith("envs/test:") for e in herrs), herrs


# ====================================================================== topology + render
def test_topology_digest_matches_the_cdk_stack_output():
    g = json.loads((EXAMPLE / "ledger-graph.json").read_text())["result"]
    assert pipeline.topology_digest(g) == (EXAMPLE / "topology-digest.txt").read_text().strip()


def test_scope_dev_keeps_only_the_ungated_chain():
    g = {"envs": {"dev": {"lane": "release"}, "qa": {"lane": "release", "promotesFrom": "dev"},
                  "test": {"lane": "release", "promotesFrom": "qa", "requiresApproval": True},
                  "prod": {"lane": "release", "promotesFrom": "test"},
                  "dev-preview": {"lane": "preview"}}}
    assert sorted(pipeline.scope_envs(g, "dev")) == ["dev", "qa"]
    assert sorted(pipeline.scope_envs(g, None)) == ["dev", "prod", "qa", "test"]


def test_render_produces_the_three_env_example(repos, tmp_path):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    ctx = make_ctx(L, tmp_path)
    r = pipeline.render(ctx, tmp_path / "render")
    cfg = json.loads((tmp_path / "render" / "pipeline.json").read_text())
    assert cfg.pop("ledgerDir") == str(L.root)
    assert cfg == json.loads((EXAMPLE / "pipeline.json").read_text())
    g = json.loads((tmp_path / "render" / "ledger-graph.json").read_text())["result"]
    assert r["digest"] == (EXAMPLE / "topology-digest.txt").read_text().strip() == pipeline.topology_digest(g)


def test_render_hybrid_sets_scope_and_the_state_store(repos, tmp_path):
    a = automation_block(mode="hybrid")
    a["pipeline"]["stateStore"] = {"keyArn": f"arn:aws:kms:us-east-1:{ACCT}:key/k"}
    L = make_ledger(repos, tmp_path, automation=a,
                    stateStore={"type": "s3", "bucket": "vra-state-x", "prefix": "vra/", "lockTable": "vra-locks"})
    r = pipeline.render(make_ctx(L, tmp_path), tmp_path / "render")
    assert r["config"]["scope"] == "dev" and r["config"]["automationMode"] == "hybrid"
    assert r["config"]["stateStore"] == {"bucketName": "vra-state-x", "prefix": "vra", "lockTableName": "vra-locks",
                                         "keyArn": f"arn:aws:kms:us-east-1:{ACCT}:key/k"}
    assert r["digest"] == pipeline.topology_digest(r["graph"], "dev")


def test_render_pipeline_plus_actions_turns_off_the_push_trigger(repos, tmp_path):
    L = make_ledger(repos, tmp_path, automation=automation_block(mode="pipeline+actions", actions={"handoff": True}))
    errs, warns = pipeline.validate(L)
    assert not errs and any("actionsHandoff is not set" in w for w in warns)
    r = pipeline.render(make_ctx(L, tmp_path), tmp_path / "render")
    assert r["config"]["sources"]["fork"]["triggerOnPush"] is False


def test_pipeline_plus_actions_does_not_require_an_unread_actions_block(repos, tmp_path):
    """R50-23: vra reads nothing from automation.actions, so pipeline+actions validates without it."""
    a = automation_block(mode="pipeline+actions")
    a.pop("actions", None)
    L = make_ledger(repos, tmp_path, automation=a)
    errs, _warns = pipeline.validate(L)
    assert not [e for e in errs if "actions" in e], errs


def test_render_refuses_agent_mode(repos, tmp_path):
    L = make_ledger(repos, tmp_path)
    with pytest.raises(errors.UsageError, match="no pipeline to render"):
        pipeline.render(make_ctx(L, tmp_path), tmp_path / "r")


# ====================================================================== plan
def test_first_plan_is_human_only_and_writes_the_it_package(repos, tmp_path):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    state: dict = {}
    ctx = make_ctx(L, tmp_path, state=state)
    rec = pipeline.plan(ctx)
    assert rec["firstDeploy"] is True and rec["humanOnly"][0] == "first deploy of the pipeline stack"
    assert rec["approvals"] == ["vra-release/Approve-prod/FinalApproval-prod", "vra-release/Approve-test/Approval-test"]
    # two synths: the IT report (vraIamReport) and the deployable one, both offline from the rendered config
    assert len(state["configs"]) == 2 and state["configs"][0]["ledgerGraph"] == "ledger-graph.json"
    pkg = Path(rec["itPackage"]["dir"])
    for f in ("IT-ROLES.md", "roles.json", "roles.csv", "it-roles.yaml", "diff.json"):
        assert (pkg / f).exists(), f
    roles = util.read_json(pkg / "roles.json")
    # the CodeBuild project name was resolved; the generated KMS key ARN is flagged for IT
    pol = json.loads(next((pkg / "policies").glob("vra-pl-BuildRole-*.json")).read_text())
    assert "/aws/codebuild/vra-build" in json.dumps(pol)
    assert roles["unresolved"] == ["(vra-pipeline/PipelineKey/Resource.Arn)"]
    cfn = yaml.safe_load((pkg / "it-roles.yaml").read_text())
    assert any("${AWS::Partition}" in json.dumps(r) for r in cfn["Resources"].values())
    assert (ctx.state_dir / "pipeline" / "plans" / f"{rec['planId']}.json").exists()
    assert any(a[:3] == ["npx", "cdk", "diff"] for a in state["cdk"])


def test_resolve_roles_names_stack_resources_and_the_pipeline_role_trust():
    unnamed_role = {"Fn::Join": ["", ["arn:", {"Ref": "AWS::Partition"}, f":iam::{ACCT}:role"]]}
    report = {"roles": [
        {"roleConstructPath": "vra-pipeline/ReleasePipeline/Role", "assumeRolePolicy": [], "identityPolicyStatements":
            [{"Action": "codebuild:StartBuild", "Resource": "(vra-pipeline/Build/Resource.Arn)"}]},
        {"roleConstructPath": "vra-pipeline/ReleasePipeline/Build/Build/CodePipelineActionRole",
         "assumeRolePolicy": [{"Action": "sts:AssumeRole", "Principal": {"AWS": unnamed_role}}]}]}
    roles, left = pipeline_iam.resolve_roles(report, template(), "app-")
    assert roles[0]["identityPolicyStatements"][0]["Resource"] == \
        "arn:(PARTITION):codebuild:(REGION):(ACCOUNT):project/vra-build"
    want = pipeline_iam.proposed_name("vra-pipeline/ReleasePipeline/Role", "app-")
    assert want.startswith("app-vra-pl-ReleasePipeline-") and len(want) <= 64
    assert roles[1]["assumeRolePolicy"][0]["Principal"]["AWS"] == f"arn:(PARTITION):iam::(ACCOUNT):role/{want}"
    assert left == []


def test_second_package_lists_the_delta(tmp_path):
    out = tmp_path / "iam"
    pipeline_iam.write_package(ROLE_REPORT["roles"][:1], out, scope="pipeline", title="t")
    p2 = pipeline_iam.write_package(ROLE_REPORT["roles"], out, scope="pipeline", title="t")
    assert p2["n"] == 2 and p2["diff"]["added"] == ["vra-pipeline/ReleasePipeline/Source/Fork/CodePipelineActionRole"]
    assert "Changes since package 1" in (Path(p2["dir"]) / "IT-ROLES.md").read_text()


def test_plan_against_a_deployed_stack_detects_iam_and_removed_approvals(repos, tmp_path):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    old = template(approvals=("Approval-test", "FinalApproval-prod", "Approval-uat"), role_policy="old")
    ctx = make_ctx(L, tmp_path, cfn=FakeCfn({"StackStatus": "UPDATE_COMPLETE", "Outputs": []}, old))
    rec = pipeline.plan(ctx)
    assert rec["firstDeploy"] is False
    assert rec["iamChanged"] == ["BuildRole"]
    assert rec["gatesRemoved"] == ["vra-release/Approve-uat/Approval-uat"]
    assert len(rec["humanOnly"]) == 2


def test_precreated_roles_missing_blocks_on_it_with_the_package(repos, tmp_path):
    a = automation_block()
    a["pipeline"]["iamRoles"] = {"mode": "precreated", "precreatedRoles": {"vra-pipeline/DevRole": "it-dev"}}
    L = make_ledger(repos, tmp_path, automation=a)
    ctx = make_ctx(L, tmp_path, state={"synthFails": True})
    with pytest.raises(errors.BlockedOnIT) as exc:
        pipeline.plan(ctx)
    assert exc.value.exit_code == errors.BLOCKED_ON_IT
    assert "IT-ROLES.md" in exc.value.message
    assert exc.value.detail["blocked"]["missingRoles"] == ["vra-pipeline/BuildRole"]
    with pytest.raises(errors.BlockedOnIT):
        pipeline.deploy(ctx)


# ====================================================================== deploy gates
def test_first_deploy_is_refused_for_an_agent_and_needs_a_person(repos, tmp_path, monkeypatch):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    state: dict = {}
    ctx = make_ctx(L, tmp_path, state=state)
    rec = pipeline.plan(ctx)
    monkeypatch.setenv("VRA_AGENT", "1")
    with pytest.raises(errors.NeedsHuman, match="human-only and an agent cannot run it"):
        pipeline.deploy(ctx, rec["planId"])
    monkeypatch.delenv("VRA_AGENT")
    with pytest.raises(errors.NeedsHuman, match="needs --approval"):  # no terminal, no approval record
        pipeline.deploy(ctx, rec["planId"])
    assert not any(a[:3] == ["npx", "cdk", "deploy"] for a in state.get("cdk", []))
    from vra import human
    monkeypatch.setattr(human, "_tty", lambda: True)
    monkeypatch.setattr(human, "_typed_confirmation_blocker", lambda: None)
    monkeypatch.setattr(human, "_default_ask", lambda q: "prod")   # the person types the approval env's name
    res = pipeline.deploy(ctx, rec["planId"])
    dep = [a for a in state["cdk"] if a[:3] == ["npx", "cdk", "deploy"]][-1]
    assert dep[3] == "vra-pipeline" and "--app" in dep and "--require-approval" in dep
    assert res["authorization"]["how"] == "tty" and res["humanOnly"]
    assert list((ctx.state_dir / "pipeline" / "deploys").glob("*.json"))


def test_first_deploy_in_ci_takes_a_human_pipeline_deploy_record_of_the_approval_env(repos, tmp_path):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    ctx = make_ctx(L, tmp_path)
    rec = pipeline.plan(ctx)
    approver = f"arn:aws:sts::{ACCT}:assumed-role/ReleaseApprover/rm@example.com"
    util.write_yaml(L.root / "approvals/prod/human_pipeline-deploy.yaml",
                    {"build": "human:pipeline-deploy", "env": "prod", "approver": approver,
                     "approvedAt": util.now_iso(), "scope": ["pipeline-deploy"]})
    assert L.validate()["ok"], L.validate()["errors"]   # the scope is in the approval schema
    res = pipeline.deploy(ctx, rec["planId"], "approvals/prod/human_pipeline-deploy.yaml")
    assert res["authorization"]["how"] == "approval" and res["authorization"]["approver"] == approver
    with pytest.raises(errors.NeedsHuman, match="already used"):
        pipeline.deploy(ctx, rec["planId"], "approvals/prod/human_pipeline-deploy.yaml")


def test_agent_may_deploy_a_plan_without_iam_or_approval_changes(repos, tmp_path, monkeypatch):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    state: dict = {"template": template()}
    ctx = make_ctx(L, tmp_path, state=state, cfn=FakeCfn({"StackStatus": "UPDATE_COMPLETE"}, template()))
    rec = pipeline.plan(ctx)
    assert rec["humanOnly"] == []
    monkeypatch.setenv("VRA_AGENT", "1")
    res = pipeline.deploy(ctx, rec["planId"])
    assert res["authorization"] is None


def test_a_source_bucket_policy_change_is_human_only(repos, tmp_path, monkeypatch):
    """B-050-6: widening who may write the release source (sourceWriterArns -> the source bucket policy) is an
    access change: an agent must not deploy it, as with an IAM change."""
    def with_policy(writers):
        t = template()
        t["Resources"]["SourceBucketPolicy"] = {"Type": "AWS::S3::BucketPolicy", "Properties": {"PolicyDocument": {
            "Statement": [{"Sid": "OnlyTheWatchTierWritesSources", "Effect": "Deny",
                           "Condition": {"ArnNotEquals": {"aws:PrincipalArn": writers}}}]}}}
        return t
    L = make_ledger(repos, tmp_path, automation=automation_block())
    state: dict = {"template": with_policy(["watch", f"arn:aws:iam::{ACCT}:role/Agent"])}
    ctx = make_ctx(L, tmp_path, state=state, cfn=FakeCfn({"StackStatus": "UPDATE_COMPLETE"}, with_policy(["watch"])))
    rec = pipeline.plan(ctx)
    assert rec["iamChanged"] == ["SourceBucketPolicy"] and rec["humanOnly"]
    monkeypatch.setenv("VRA_AGENT", "1")
    with pytest.raises(errors.NeedsHuman, match="human-only"):
        pipeline.deploy(ctx, rec["planId"])


def test_deploy_refuses_a_ledger_or_template_that_moved_since_the_plan(repos, tmp_path):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    state: dict = {"template": template()}
    ctx = make_ctx(L, tmp_path, state=state, cfn=FakeCfn({"StackStatus": "UPDATE_COMPLETE"}, template()))
    rec = pipeline.plan(ctx)
    state["template"] = template(role_policy="other")      # a different package / dependency set
    with pytest.raises(errors.NeedsHuman, match="differs from plan"):
        pipeline.deploy(ctx, rec["planId"])
    env = util.read_yaml(L.root / "envs" / "test" / "env.yaml")
    env["requiresApproval"] = False
    env["approvers"] = []
    util.write_yaml(L.root / "envs" / "test" / "env.yaml", env)
    ctx2 = make_ctx(type(L)(L.root), tmp_path, state=state, cfn=FakeCfn({"StackStatus": "UPDATE_COMPLETE"}, template()))
    with pytest.raises(errors.NeedsHuman, match="changed since plan"):
        pipeline.deploy(ctx2, rec["planId"])


def test_offline_plan_cannot_be_deployed(repos, tmp_path):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    ctx = make_ctx(L, tmp_path)
    rec = pipeline.plan(ctx, offline=True)
    assert rec["firstDeploy"] is None and "offline" in rec["humanOnly"][0]
    with pytest.raises(errors.UsageError, match="--offline"):
        pipeline.deploy(ctx, rec["planId"])


# ====================================================================== status / sync-ledger
def test_status_compares_the_deployed_topology_and_lists_pending_approvals(repos, tmp_path):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    digest = pipeline.topology_digest(pipeline.graph(L))
    outs = [{"OutputKey": "TopologyDigest", "OutputValue": digest},
            {"OutputKey": "ReleasePipelineName", "OutputValue": "vra-release"},
            {"OutputKey": "ConnectionArn", "OutputValue": f"arn:aws:codeconnections:us-east-1:{ACCT}:connection/c"}]
    ctx = make_ctx(L, tmp_path, cfn=FakeCfn({"StackStatus": "UPDATE_COMPLETE", "Outputs": outs}))
    st = pipeline.status(ctx)
    assert st["inSync"] is True and st["connectionArns"]["ConnectionArn"].endswith(":connection/c")
    assert st["pipelines"]["vra-release"]["pendingApprovals"] == [
        {"stage": "Approve-test", "action": "Approval-test", "token": True}]
    outs[0]["OutputValue"] = "stale"
    assert pipeline.status(ctx)["inSync"] is False


def test_sync_ledger_plans_again_and_exits_20_until_deployed(repos, tmp_path):
    L = make_ledger(repos, tmp_path, automation=automation_block())
    outs = [{"OutputKey": "TopologyDigest", "OutputValue": "stale"}]
    ctx = make_ctx(L, tmp_path, cfn=FakeCfn({"StackStatus": "UPDATE_COMPLETE", "Outputs": outs}, template()))
    code, res, msg = pipeline.sync_ledger(ctx)
    assert code == errors.NEEDS_HUMAN and res["plan"]["planId"] in msg


# ====================================================================== push-source (S3 source fed by vra)
def test_push_source_uploads_once_per_revision_and_starts_the_pipeline(repos, tmp_path, monkeypatch):
    from moto import mock_aws
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)  # moto intercepts; nothing leaves the process
    L = make_ledger(repos, tmp_path, automation=automation_block())
    cp = FakeCodePipeline()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="vra-pipeline-sourcebucket1")
        ctx = make_ctx(L, tmp_path, cp=cp, moto=True)
        del ctx.aws.clients[("tools", "cloudformation")]
        r1 = pipeline.push_source(ctx, "release", "vra-pipeline-sourcebucket1", "fork/main.zip", "vra-release")
        assert r1["uploaded"] and r1["started"] == "exec-1"
        r2 = pipeline.push_source(ctx, "release", "vra-pipeline-sourcebucket1", "fork/main.zip", "vra-release")
        assert not r2["uploaded"] and r2["message"] == "unchanged" and cp.started == ["vra-release"]
        head = boto3.client("s3", region_name="us-east-1").head_object(Bucket="vra-pipeline-sourcebucket1",
                                                                        Key="fork/main.zip")
        assert head["Metadata"]["vra-revision"] == r1["revision"]
        pkg = pipeline.push_source(ctx, None, "vra-pipeline-sourcebucket1", "vra/p.zip", None, package=True)
        assert pkg["uploaded"] and pkg["revision"].startswith("package-") and pkg["started"] is None


def test_push_source_zip_carries_the_commit_the_build_stage_reads(repos, tmp_path, monkeypatch):
    """E2E-050 B-050-13: an S3 source resolves to the object's version id and the git archive has no .git, so the
    build stage ran `vra build --ref <S3 version id>` ("cannot resolve ... in the fork"). The zip carries the commit
    in .vra-revision, buildspec-build.yml prefers it, and an object uploaded without it is uploaded again."""
    import io
    import zipfile
    from moto import mock_aws
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    L = make_ledger(repos, tmp_path, automation=automation_block())
    cp = FakeCodePipeline()
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="vra-pipeline-sourcebucket1")
        ctx = make_ctx(L, tmp_path, cp=cp, moto=True)
        del ctx.aws.clients[("tools", "cloudformation")]
        r1 = pipeline.push_source(ctx, "release", "vra-pipeline-sourcebucket1", "fork/main.zip", "vra-release")
        body = s3.get_object(Bucket="vra-pipeline-sourcebucket1", Key="fork/main.zip")["Body"].read()
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            assert z.read(pipeline.REVISION_FILE).decode().strip() == r1["revision"]
        # an object of the same commit uploaded by the old code (no revision file): uploaded again
        s3.put_object(Bucket="vra-pipeline-sourcebucket1", Key="fork/main.zip", Body=b"old",
                      Metadata={"vra-revision": r1["revision"]})
        r2 = pipeline.push_source(ctx, "release", "vra-pipeline-sourcebucket1", "fork/main.zip", "vra-release")
        assert r2["uploaded"] and r2["revision"] == r1["revision"]
        r3 = pipeline.push_source(ctx, "release", "vra-pipeline-sourcebucket1", "fork/main.zip", "vra-release")
        assert not r3["uploaded"]
    spec = yaml.safe_load((pipeline.package_root() / "cicd/codebuild/buildspec-build.yml").read_text())
    pre = "\n".join(spec["phases"]["pre_build"]["commands"])
    assert ".vra-revision" in pre and pre.index(".vra-revision") < pre.index("export VRA_REF=")


def test_push_source_takes_the_bucket_and_pipeline_of_a_stack_created_source_from_the_stack(repos, tmp_path,
                                                                                            monkeypatch):
    """B-050-3: the stack creates the bucket (no bucketName) and that source never polls, so push-source run by an
    operator without --bucket / --pipeline reads both from the stack outputs instead of refusing or uploading
    without starting anything."""
    from moto import mock_aws
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    a = automation_block()
    a["pipeline"]["sources"]["fork"] = {"type": "s3", "objectKey": "fork/main.zip"}
    a["pipeline"]["sources"]["vra"] = {"type": "s3", "objectKey": "vra/p.zip"}
    L = make_ledger(repos, tmp_path, automation=a)
    cp = FakeCodePipeline()
    outs = [{"OutputKey": "SourceBucketName", "OutputValue": "vra-pipeline-sourcebucket2"},
            {"OutputKey": "ReleasePipelineName", "OutputValue": "vra-release"}]
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="vra-pipeline-sourcebucket2")
        ctx = make_ctx(L, tmp_path, cp=cp, moto=True,
                       cfn=FakeCfn({"StackStatus": "UPDATE_COMPLETE", "Outputs": outs}))
        r = pipeline.push_source(ctx, "release", None, None, None)
        assert r["bucket"] == "vra-pipeline-sourcebucket2" and r["key"] == "fork/main.zip"
        assert r["uploaded"] and r["started"] == "exec-1" and cp.started == ["vra-release"]
        pkg = pipeline.push_source(ctx, None, None, None, None, package=True)
        assert pkg["bucket"] == "vra-pipeline-sourcebucket2" and pkg["uploaded"] and pkg["started"] is None
        assert cp.started == ["vra-release"]


# ====================================================================== CLI
def test_cli_registers_the_pipeline_group():
    ns = cli.build_parser().parse_args(["pipeline", "deploy", "--plan", "pl-1"])
    assert ns.cmd == "pipeline" and ns.pipeline_cmd == "deploy" and ns.plan_id == "pl-1" and callable(ns.handler)
    for sub in ("plan", "status", "sync-ledger", "push-source"):
        assert cli.build_parser().parse_args(["pipeline", sub]).pipeline_cmd == sub


def test_cli_pipeline_status_in_agent_mode(repos, tmp_path, capsys):
    L = make_ledger(repos, tmp_path)
    code = cli.main(["--ledger", str(L.root), "--work-dir", str(tmp_path / "w"), "--json", "pipeline", "status"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["result"]["mode"] == "agent" and out["result"]["pipeline"] is None
