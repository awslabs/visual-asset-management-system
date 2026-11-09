# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CI lanes (offline): OIDC identity boundaries, topology from the ledger, approval writers, home folder.

- GitLab: every job requests its own token audience and each env role trusts only its own; GitHub: the
  subject carries the environment; test-only roles never reach a deploy role.
- ci_graph: the shipped GitHub and GitLab job graphs match the ledger, a mismatch fails, render output
  passes check for a fan-out topology.
- approval_record.py: the generic writer, scopes from the approval comment.
- vra-home.sh: refuses to publish changes outside state/, artifacts/, checkpoints/; run-vra.sh always leaves
  a JSON envelope.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PKG = Path(__file__).resolve().parents[2]
SCRIPTS = PKG / "cicd" / "codebuild" / "scripts"
LEDGER = PKG / "ledger-template"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ CloudFormation rendering + IAM evaluation
_spec = importlib.util.spec_from_file_location("cfn_eval", PKG / "iam" / "tests" / "cfn_eval.py")
cfn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfn)
render = cfn.render


OIDC = cfn.load_cfn(PKG / "cicd/oidc/vra-ci-oidc-role.yaml")


def _oidc_full(provider: str, env: str, **over) -> tuple[list, list]:
    """(trust statements, every policy statement of the role) for one parameter set."""
    params = {"AWS::Partition": "aws", "AWS::Region": "us-east-1", "AWS::AccountId": "111111111111",
              "Provider": provider, "CiEnvName": env, "Repository": "example-org/vams-home", "Branch": "main",
              "GitHubEnvironment": "", "SubjectPatterns": [""], "Audience": "", "ProviderAudiences": [""],
              "TestEnvs": [""], "DeployableEnvs": [""], "EnvAccountIds": ["222222222222"],
              "StateBucketName": "", "StatePrefix": "vra", "LockTableName": "vra-locks", "StateKeyArn": "",
              "OidcProviderArn": "", "TrustedPrincipalArns": [""], "ExternalId": "", "AllowWatchLock": "false",
              "ApprovalSigningKeyArns": [""], "SigningKeyArn": "", "AllowSigning": "false"}
    params.update(over)
    j = lambda k: ",".join(params[k])  # noqa: E731
    generic = provider == "generic"
    conds = {"IsGeneric": generic, "CreateProvider": params["OidcProviderArn"] == "" and not generic,
             "IsGitHub": provider == "github", "HasExternalId": params["ExternalId"] != "",
             "HasSubjectPatterns": j("SubjectPatterns") != "",
             "HasAudience": params["Audience"] != "", "HasProviderAudiences": j("ProviderAudiences") != "",
             "HasGitHubEnvironment": params["GitHubEnvironment"] != "", "HasTestEnvs": j("TestEnvs") != "",
             "HasRepositoryIds": params.get("RepositoryId", "") != "" and params.get("RepositoryOwnerId", "") != "",
             "OwnEnvOnly": j("DeployableEnvs") == "", "NoEnvRoles": j("DeployableEnvs") == "none",
             "SynthRoles": params.get("AllowSynthRoles") == "true", "HasStateStore": params["StateBucketName"] != "",
             "HasStateKey": params["StateKeyArn"] != "", "HasBoundary": False,
             "HasSigningKey": params["SigningKeyArn"] != "",
             "MaySign": params["SigningKeyArn"] != "" and params["AllowSigning"] == "true",
             "WatchLock": params["AllowWatchLock"] == "true",
             "HasApprovalSigningKeys": j("ApprovalSigningKeyArns") != ""}
    trust = render(OIDC["Resources"]["CiRole"]["Properties"]["AssumeRolePolicyDocument"], params, conds)
    stmts = []
    for name, res in OIDC["Resources"].items():
        if res["Type"] != "AWS::IAM::ManagedPolicy" or (res.get("Condition") and not conds[res["Condition"]]):
            continue
        stmts += render(res["Properties"]["PolicyDocument"], params, conds)["Statement"]
    return trust["Statement"], stmts


def _oidc(provider: str, env: str, **over) -> tuple[dict, list]:
    trust, stmts = _oidc_full(provider, env, **over)
    return trust[0]["Condition"], stmts


def test_gitlab_roles_trust_only_their_own_audience():
    prod, _ = _oidc("gitlab", "prod")
    build, _ = _oidc("gitlab", "build")
    assert prod["StringEquals"]["gitlab.com:aud"] == "vra-prod"
    assert build["StringEquals"]["gitlab.com:aud"] == "vra-build"
    assert prod["StringEquals"]["gitlab.com:aud"] != build["StringEquals"]["gitlab.com:aud"]
    rules = OIDC["Rules"]["GitLabAudiencePerEnv"]
    assert "sts.amazonaws.com" in json.dumps(rules)


def test_gitlab_jobs_request_the_audience_of_their_environment():
    doc = yaml.safe_load((PKG / "cicd/gitlab-ci/.gitlab-ci.yml").read_text())
    assert "id_tokens" not in doc[".vra"], "the shared template must not hand every job a token"
    for job_id, job in doc.items():
        if not isinstance(job, dict) or job_id.startswith(".") or "environment" not in job:
            continue
        aud = job["id_tokens"]["VRA_OIDC_TOKEN"]["aud"]
        env = job["environment"]["name"]
        assert aud == f"vra-{env}", (job_id, aud, env)


def test_github_roles_are_pinned_to_their_environment():
    cond, _ = _oidc("github", "prod")
    assert cond["StringLike"]["token.actions.githubusercontent.com:sub"] == ["repo:example-org/vams-home:environment:prod"]
    assert cond["StringEquals"]["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    # R50-16: the immutable subject when the repository's ids are given
    cond, _ = _oidc("github", "prod", RepositoryOwnerId="12", RepositoryId="34")
    assert cond["StringLike"]["token.actions.githubusercontent.com:sub"] == [
        "repo:example-org@12/vams-home@34:environment:prod"]


def test_test_only_ci_role_never_reaches_a_deploy_role():
    _, stmts = _oidc("github", "prod-tests", TestEnvs=["prod"], DeployableEnvs=["none"])
    assert not any(s.get("Sid") == "AssumeOwnEnvRoles" for s in stmts)
    assume = next(s for s in stmts if s.get("Sid") == "AssumeOwnTestRoles")
    assert assume["Resource"] == "arn:aws:iam::*:role/vra-test-*"
    assert assume["Condition"]["StringEquals"]["aws:ResourceTag/vra:env"] == ["prod"]
    assert "vra-deploy-" not in json.dumps(stmts)
    _, deploy = _oidc("github", "prod")
    assert "arn:aws:iam::*:role/vra-deploy-*" in next(s for s in deploy if s.get("Sid") == "AssumeOwnEnvRoles")["Resource"]


def test_codebuild_lane_has_one_deploy_project_per_env():
    text = (PKG / "cicd/pipeline/lib/vra-pipeline-stack.ts").read_text()
    assert "`Deploy-${env.name}`" in text and "concurrentBuildLimit: 1" in text


# ------------------------------------------------------------------ topology
def test_shipped_job_graphs_match_the_ledger(capsys):
    g = _module("ci_graph")
    graph = g.load_graph(LEDGER)          # `vra --json ledger graph` on the template ledger
    assert graph["test"]["promotesFrom"] == "dev" and graph["prod"]["requiresApproval"] is True
    assert graph["prod"]["tier"] == "prod" and graph["dev"]["requiredSuites"] == ["live-cli"]
    assert "dev-preview" not in graph     # another lane
    gh = yaml.safe_load((PKG / "cicd/github-actions/deploy.yml").read_text())
    gl = yaml.safe_load((PKG / "cicd/gitlab-ci/.gitlab-ci.yml").read_text())
    assert g.check_github(graph, gh) == []
    assert g.check_gitlab(graph, gl) == []


def test_a_job_graph_that_disagrees_with_the_ledger_fails():
    g = _module("ci_graph")
    graph = g.load_graph(LEDGER)
    gh = yaml.safe_load((PKG / "cicd/github-actions/deploy.yml").read_text())
    gh["jobs"]["deploy-prod"]["with"]["promote-from"] = "dev"
    assert any("promote-from" in p for p in g.check_github(graph, gh))
    gl = yaml.safe_load((PKG / "cicd/gitlab-ci/.gitlab-ci.yml").read_text())
    gl["deploy-prod"]["id_tokens"]["VRA_OIDC_TOKEN"]["aud"] = "vra-build"
    gl["deploy-test"]["extends"] = [".deploy-auto"]
    problems = g.check_gitlab(graph, gl)
    assert any("aud" in p for p in problems) and any("requiresApproval" in p for p in problems)


def _graph_env(lane="release", parent=None, gated=False, children=(), suites=("live-cli",), tier="nonprod"):
    return {"lane": lane, "promotesFrom": parent, "requiresApproval": gated, "autoDeploy": not gated,
            "tier": tier, "isProdClass": tier == "prod", "requiredSuites": list(suites), "account": "222222222222",
            "region": "us-east-1", "children": list(children)}


def _fanout_graph(tmp_path: Path) -> Path:
    """`vra --json ledger graph` output (the envelope) for dev -> int -> {prod-us, prod-eu} + a preview env."""
    envs = {"dev": _graph_env(children=["int"]),
            "int": _graph_env(parent="dev", children=["prod-eu", "prod-us"], suites=("live-cli", "smoke")),
            "prod-us": _graph_env(parent="int", gated=True, tier="prod"),
            "prod-eu": _graph_env(parent="int", gated=True, tier="prod"),
            "dev-preview": _graph_env(lane="preview", suites=())}
    f = tmp_path / "ledger-graph.json"
    f.write_text(json.dumps({"exitCode": 0, "message": None, "result": {"envs": envs, "roots": ["dev", "dev-preview"]}}))
    return f


def test_rendered_graphs_pass_check_for_fan_out(tmp_path):
    g = _module("ci_graph")
    graph = g.load_graph(graph_file=_fanout_graph(tmp_path))
    assert g.order(graph) == ["dev", "int", "prod-eu", "prod-us"]
    gh = {"jobs": yaml.safe_load(g.render_github(graph))}
    assert g.check_github(graph, gh) == []
    assert gh["jobs"]["deploy-prod-eu"]["with"]["promote-from"] == "int"
    assert gh["jobs"]["deploy-prod-us"]["needs"] == ["build", "deploy-int"]
    assert gh["jobs"]["deploy-prod-us"]["with"]["requires-approval"] is True
    assert gh["jobs"]["deploy-int"]["with"]["requires-approval"] is False
    # int feeds the prods: its suites include what the ledger requires on it before it promotes
    assert gh["jobs"]["deploy-int"]["with"]["test-suites"] == "live-cli,live-web,customer,smoke"
    gl = yaml.safe_load(g.render_gitlab(graph))
    assert g.check_gitlab(graph, gl) == []
    assert gl["deploy-int"]["extends"] == [".deploy-auto"] and gl["deploy-prod-us"]["extends"] == [".deploy-gated"]


def test_preview_lane_renders_its_own_deploy_jobs(tmp_path):
    """0.5.0: `render --lane preview --build-job merge-build` prints the jobs upstream-preview.yml can use."""
    out = subprocess.run([sys.executable, str(SCRIPTS / "ci_graph.py"), "render", "--platform", "github", "--graph",
                          str(_fanout_graph(tmp_path)), "--lane", "preview", "--build-job", "merge-build"],
                         capture_output=True, text=True, check=True)
    jobs = yaml.safe_load(out.stdout)
    assert list(jobs) == ["deploy-dev-preview"]
    job = jobs["deploy-dev-preview"]
    assert job["needs"] == ["merge-build"] and job["with"]["requires-approval"] is False
    assert job["with"]["build-id"] == "${{ needs.merge-build.outputs.build-id }}"
    g = _module("ci_graph")
    assert g.check_github(g.load_graph(graph_file=_fanout_graph(tmp_path), lane="preview"), {"jobs": jobs}) == []
    # the release lane (default) is unchanged: build job "build", no preview env
    rel = yaml.safe_load(g.render_github(g.load_graph(graph_file=_fanout_graph(tmp_path))))
    assert "deploy-dev-preview" not in rel and rel["deploy-dev"]["needs"] == ["build"]
    generic = subprocess.run([sys.executable, str(SCRIPTS / "ci_graph.py"), "render", "--platform", "generic",
                              "--graph", str(_fanout_graph(tmp_path)), "--lane", "preview"],
                             capture_output=True, text=True, check=True).stdout
    assert 'vra-ci.sh build --lane preview' in generic and "--env dev-preview" in generic


def test_requires_approval_comes_from_the_ledger(tmp_path):
    graph = _fanout_graph(tmp_path)
    for env, want in (("int", "false"), ("prod-eu", "true"), ("dev-preview", "false")):
        out = subprocess.run([sys.executable, str(SCRIPTS / "ci_graph.py"), "requires-approval", "--graph",
                              str(graph), "--env", env], capture_output=True, text=True, check=True)
        assert out.stdout.strip() == want, env   # int: promoted, but ungated: no approval record
    out = subprocess.run([sys.executable, str(SCRIPTS / "ci_graph.py"), "requires-approval", "--ledger",
                          str(LEDGER), "--env", "test"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "true"


def test_codepipeline_topology_check():
    g = _module("ci_graph")
    graph = g.load_graph(LEDGER)
    cfg = {"release": {"envs": [{"name": "dev", "account": "222222222222"},
                                {"name": "test", "account": "333333333333", "promotesFrom": "dev",
                                 "requiresApproval": True},
                                {"name": "prod", "account": "444444444444", "promotesFrom": "test",
                                 "requiresApproval": True}]}}
    assert g.check_codepipeline(graph, cfg) == []
    cfg["release"]["envs"][2]["promotesFrom"] = "dev"
    assert g.check_codepipeline(graph, cfg)


# ------------------------------------------------------------------ approval writers
def test_generic_writer_binds_to_the_run_and_refuses_self_approval():
    w = _module("approval_record")
    env = {"VRA_CI_PROVIDER": "generic", "VRA_INITIATOR": "alice", "VRA_RUN_REF": "jenkins:release/42",
           "VRA_APPROVAL_REF": "jenkins:release/42/approve-prod"}
    rec = w.from_generic("bob", "2026-11-03T10:00:00Z", "ticket: CHG-7; notes: ok", "prod", "b1", env)
    assert rec["source"] == "jenkins:release/42" and rec["approver"] == "bob" and rec["scope"] == ["promote"]
    assert rec["binding"]["approvalRef"] == "jenkins:release/42/approve-prod" and rec["ticket"] == "CHG-7"
    with pytest.raises(w.Refused, match="separation of duties"):
        w.from_generic("Alice", "t", "", "prod", "b1", env)
    with pytest.raises(w.Refused, match="generic"):
        w.from_generic("bob", "t", "", "prod", "b1", {**env, "VRA_CI_PROVIDER": ""})
    with pytest.raises(w.Refused, match="run reference"):
        w.from_generic("bob", "t", "", "prod", "b1", {**env, "VRA_RUN_REF": ""})


def test_scope_from_the_approval_comment_is_limited():
    w = _module("approval_record")
    env = {"VRA_CI_PROVIDER": "generic", "VRA_INITIATOR": "alice", "VRA_RUN_REF": "r"}
    rec = w.from_generic("bob", "t", "ticket: CHG-1; scope: emergency, accept-drift; notes: x", "prod", "b", env,
                         scope="deploy")
    assert rec["scope"] == ["deploy", "emergency", "accept-drift"]
    with pytest.raises(w.Refused, match="restore"):
        w.from_generic("bob", "t", "scope: restore", "prod", "b", env)


# ------------------------------------------------------------------ home folder + run-vra.sh
def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture()
def home(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git not installed")
    origin = tmp_path / "origin.git"
    _git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    _git("init", "--initial-branch=main", str(seed), cwd=tmp_path)
    (seed / "vra-home.yaml").write_text("homeId: test\n")
    (seed / "ledger").mkdir()
    (seed / "ledger" / "vra.yaml").write_text("project: {}\n")
    _git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", ".", cwd=seed)
    _git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "seed", cwd=seed)
    _git("remote", "add", "origin", str(origin), cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)
    env = {**os.environ, "VRA_HOME": str(tmp_path / "vra-home"), "VRA_HOME_REPO": str(origin),
           "VRA_HOME_REF": "main", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    env.pop("VRA_HOME_TOKEN", None)
    env.pop("VRA_GIT_TOKEN", None)
    r = subprocess.run(["bash", str(SCRIPTS / "vra-home.sh"), "checkout"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return env


def test_home_sync_refuses_to_publish_ledger_changes(home):
    h = Path(home["VRA_HOME"])
    (h / "ledger" / "approvals").mkdir()
    (h / "ledger" / "approvals" / "planted.yaml").write_text("approver: someone\n")
    r = subprocess.run(["bash", str(SCRIPTS / "vra-home.sh"), "sync"], env=home, capture_output=True, text=True)
    assert r.returncode == 20 and "ledger/approvals/planted.yaml" in r.stderr


def test_home_sync_accepts_state_changes(home):
    h = Path(home["VRA_HOME"])
    (h / "state" / "runs" / "r1").mkdir(parents=True)
    (h / "state" / "runs" / "r1" / "state.json").write_text("{}")
    # no vra on PATH: the script falls back to a fast-forward pull and never pushes
    r = subprocess.run(["bash", str(SCRIPTS / "vra-home.sh"), "sync"], env=home, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_run_vra_writes_an_envelope_when_vra_prints_nothing(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "vra"
    fake.write_text("#!/usr/bin/env bash\necho boom >&2\nexit 1\n")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "VRA_OUT_DIR": str(tmp_path / "out")}
    r = subprocess.run(["bash", str(SCRIPTS / "run-vra.sh"), "step", "status"], env=env, capture_output=True,
                       text=True)
    assert r.returncode == 1
    data = json.loads((tmp_path / "out" / "step.json").read_text())
    assert data["exitCode"] == 1 and data["status"] == "FAILED"
    assert json.loads((tmp_path / "out" / "step.result.json").read_text())["outcome"] == "ERROR"


def test_generic_entry_script_requires_the_contract(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("VRA_")}
    r = subprocess.run(["bash", str(PKG / "cicd/generic/vra-ci.sh"), "home"], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "VRA_CI_PROVIDER" in r.stderr
    env.update({"VRA_CI_PROVIDER": "generic", "VRA_RUN_REF": "jenkins:x/1", "VRA_HOME": str(tmp_path)})
    r = subprocess.run(["bash", str(PKG / "cicd/generic/vra-ci.sh"), "home"], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "VRA_INITIATOR" in r.stderr
