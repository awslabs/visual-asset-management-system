# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the GitHub Actions workflows (cicd/github-actions): pins, permissions, concurrency, GHES
settings, the ledger-rendered deploy jobs, and the mirror of the upstream VAMS ci.yml jobs.

actionlint runs when it is installed (on PATH, or VRA_ACTIONLINT=<binary>); everything else needs no tools."""

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
GHA = PKG / "cicd/github-actions"
LEDGER = PKG / "ledger-template"
sys.path.insert(0, str(PKG / "cicd/codebuild/scripts"))
import ci_graph  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


pins = _load("vra_pins", GHA / "pins.py")
WORKFLOWS = sorted(GHA.glob("*.yml"))
NEW = ["pr-checks.yml", "security-scan.yml", "upstream-release.yml", "upstream-preview.yml", "deploy.yml",
       "hand-off-to-codepipeline.yml"]
RUNS_ON = "${{ fromJSON(vars.VRA_RUNS_ON || '\"ubuntu-latest\"') }}"


def wf(name: str) -> dict:
    return yaml.safe_load((GHA / name).read_text())


def steps(doc: dict):
    for job_id, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            yield job_id, job, step


# ------------------------------------------------------------------ pins
def test_every_action_is_pinned_to_a_listed_commit_sha():
    table = pins.load_pins()
    assert all(re.fullmatch(r"[0-9a-f]{40}", p["sha"]) for p in table.values())
    files = WORKFLOWS + [GHA / "setup-vra/action.yml"]
    problems = [p for f in files for p in pins.check_text(f.read_text(), table, f.name)]
    assert problems == []


def test_pin_check_refuses_tags_unknown_actions_and_wrong_comments():
    table = pins.load_pins()
    sha = table["actions/checkout"]["sha"]
    text = "\n".join([
        "      - uses: actions/checkout@v7",
        f"      - uses: someone/else@{sha} # v1",
        f"      - uses: actions/checkout@{sha} # v6.0.0",
        f"      - uses: actions/checkout@{'0' * 40} # v7.0.1",
        "      - uses: ./vra-src/cicd/github-actions/setup-vra",
        f"      - uses: actions/checkout@{sha} # v7.0.1",
    ])
    problems = pins.check_text(text, table, "t.yml")
    assert [p.split(":")[1] for p in problems] == ["1", "2", "3", "4"]


def test_ghes_copy_uses_the_v3_artifact_pins_and_drops_unknown_inputs(tmp_path):
    table = pins.load_pins()
    out = pins.ghes_text((GHA / "deploy.yml").read_text(), table)
    up, down = table["actions/upload-artifact"], table["actions/download-artifact"]
    assert up["sha"] not in out and f"actions/upload-artifact@{up['ghes']['sha']} # {up['ghes']['tag']}" in out
    assert "include-hidden-files" not in out
    assert pins.check_text(out, table, "ghes") == []
    doc = yaml.safe_load(out)
    assert doc["jobs"]["build"]["steps"][-1]["with"]["retention-days"] == 90  # the other inputs stay
    env = pins.ghes_text((GHA / "vra-deploy-env.yml").read_text(), table)
    assert f"actions/download-artifact@{down['ghes']['sha']}" in env
    assert yaml.safe_load(env)["jobs"].keys() == wf("vra-deploy-env.yml")["jobs"].keys()
    # the CLI writes a full copy of the directory
    assert pins.main(["ghes", str(GHA), str(tmp_path / "ghes")]) == 0
    assert sorted(p.name for p in (tmp_path / "ghes").glob("*.yml")) == [p.name for p in WORKFLOWS]


def test_downloaded_tools_match_the_pin_list():
    tools = json.loads((GHA / "action-pins.json").read_text())["tools"]
    scan = wf("security-scan.yml")["jobs"]
    assert scan["ash"]["env"]["TRIVY_VERSION"] == tools["trivy"]["version"]
    assert scan["ash"]["env"]["TRIVY_SHA256"] == tools["trivy"]["sha256"]
    assert scan["ash"]["env"]["ASH_REF"] == tools["aws-samples/automated-security-helper"]["sha"]
    assert scan["secrets"]["env"]["GITLEAKS_VERSION"] == tools["gitleaks"]["version"]
    assert scan["secrets"]["env"]["GITLEAKS_SHA256"] == tools["gitleaks"]["sha256"]


# ------------------------------------------------------------------ least privilege, injection, concurrency
ALLOWED_JOB_PERMS = {"contents": {"read"}, "id-token": {"write"}, "actions": {"read"},
                     "security-events": {"write"}, "packages": {"read"}}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_permissions_are_least_privilege(path):
    doc = yaml.safe_load(path.read_text())
    assert doc["permissions"] == {"contents": "read"}, path.name
    for job_id, job in (doc.get("jobs") or {}).items():
        perms = job.get("permissions") or {}
        for k, v in perms.items():
            assert v in ALLOWED_JOB_PERMS.get(k, set()), (path.name, job_id, k, v)
        uses = [s.get("uses", "") for s in job.get("steps") or []]
        has_oidc = any(u.startswith("aws-actions/configure-aws-credentials@") for u in uses)
        assert (perms.get("id-token") == "write") == has_oidc, (path.name, job_id)
        sarif = any("codeql-action/" in u for u in uses)
        assert (perms.get("security-events") == "write") == sarif, (path.name, job_id)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_expression_is_expanded_inside_a_script(path):
    """Script injection: every value reaches a run: step through env:, never as ${{ ... }} text."""
    for job_id, _, step in steps(yaml.safe_load(path.read_text())):
        assert "${{" not in str(step.get("run", "")), (path.name, job_id, step.get("name"))


CURRENT = [p for p in WORKFLOWS if p.name != "vra-release.yml"]  # the replaced 0.4.0 workflow stays as shipped


@pytest.mark.parametrize("path", CURRENT, ids=lambda p: p.name)
def test_every_job_runs_on_the_configurable_runner(path):
    for job_id, job in (yaml.safe_load(path.read_text()).get("jobs") or {}).items():
        if "uses" in job:
            continue  # reusable workflow call
        assert job["runs-on"] == RUNS_ON, (path.name, job_id)


@pytest.mark.parametrize("path", CURRENT, ids=lambda p: p.name)
def test_oidc_audience_is_configurable(path):
    for job_id, _, step in steps(yaml.safe_load(path.read_text())):
        if str(step.get("uses", "")).startswith("aws-actions/configure-aws-credentials@"):
            assert step["with"]["audience"] == "${{ vars.VRA_OIDC_AUDIENCE || 'sts.amazonaws.com' }}", (
                path.name, job_id)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_gh_gets_a_token_for_github_enterprise_server_too(path):
    for job_id, _, step in steps(yaml.safe_load(path.read_text())):
        env = step.get("env") or {}
        if "GH_TOKEN" in env:
            assert env.get("GH_ENTERPRISE_TOKEN") == env["GH_TOKEN"], (path.name, job_id, step.get("name"))


def test_env_jobs_run_one_at_a_time_and_are_never_cancelled():
    for name, job_id in [("vra-deploy-env.yml", "deploy"), ("vra-deploy-env.yml", "tests"),
                         ("vra-operator.yml", "operator"), ("upstream-preview.yml", "rollback-preview")]:
        c = wf(name)["jobs"][job_id]["concurrency"]
        assert c["group"].startswith("vra-env-") and c["cancel-in-progress"] is False, (name, job_id)
    assert wf("upstream-release.yml")["concurrency"] == {"group": "vra-upstream-sync", "cancel-in-progress": False}
    assert wf("upstream-preview.yml")["jobs"]["merge-build"]["concurrency"]["group"] == "vra-upstream-sync"
    assert "concurrency" not in wf("deploy.yml"), "no workflow-wide group: a waiting prod must not block dev"
    hand = wf("hand-off-to-codepipeline.yml")["jobs"]["hand-off"]["concurrency"]
    assert hand["group"].startswith("vra-handoff-") and hand["cancel-in-progress"] is False


def test_checkouts_that_never_push_drop_their_credentials():
    for name in ["pr-checks.yml", "security-scan.yml", "hand-off-to-codepipeline.yml"]:
        for job_id, _, step in steps(wf(name)):
            if str(step.get("uses", "")).startswith("actions/checkout@") and job_id != "vra-plan":
                assert (step.get("with") or {}).get("persist-credentials") is False, (name, job_id)


# ------------------------------------------------------------------ deploy.yml and the ledger graph
def _rendered_block(text: str) -> str:
    m = re.search(r"# --- rendered: .*?\n(.*?)\n    # --- end rendered ---", text, re.S)
    assert m, "deploy.yml has no rendered block"
    return m.group(1)


def test_deploy_jobs_are_exactly_the_rendered_ledger_graph():
    graph = ci_graph.load_graph(LEDGER)
    text = (GHA / "deploy.yml").read_text()
    assert _rendered_block(text).strip() == ci_graph.render_github(graph).strip()
    doc = yaml.safe_load(text)
    assert ci_graph.check_github(graph, doc) == []
    # the replaced 0.4.0 workflow carries the same deploy jobs
    old = {k: v for k, v in wf("vra-release.yml")["jobs"].items() if k.startswith("deploy-")}
    assert {k: v for k, v in doc["jobs"].items() if k.startswith("deploy-")} == old


def test_deploy_build_job_checks_this_file_against_the_ledger():
    run = "\n".join(str(s.get("run", "")) for _, _, s in steps(wf("deploy.yml")))
    assert "ci_graph.py check --platform github" in run and ".github/workflows/deploy.yml" in run
    assert wf("deploy.yml")["jobs"]["build"]["environment"] == "vra-build"


def test_a_changed_gate_in_deploy_yml_is_caught():
    graph = ci_graph.load_graph(LEDGER)
    doc = wf("deploy.yml")
    doc["jobs"]["deploy-prod"]["with"]["requires-approval"] = False
    assert any("requires-approval" in p for p in ci_graph.check_github(graph, doc))


def test_deploy_env_writes_the_approval_record_from_the_environment_reviewers():
    run = "\n".join(str(s.get("run", "")) for _, _, s in steps(wf("vra-deploy-env.yml")))
    assert "actions/runs/${GITHUB_RUN_ID}/approvals" in run
    assert "approval_record.py github --approvals approvals.json" in run
    assert "ci_graph.py requires-approval" in run
    job = wf("vra-deploy-env.yml")["jobs"]["deploy"]
    assert job["environment"] == "${{ inputs.env-name }}"


# ------------------------------------------------------------------ the fork workflows
UPSTREAM_JOBS = {"lint", "vamscli-test", "web-build", "infra-typecheck", "infra-test", "synth", "web-test",
                 "backend-test", "pipelines-test", "vamsmcp-test"}


def test_pr_checks_mirror_the_upstream_ci_jobs():
    jobs = wf("pr-checks.yml")["jobs"]
    assert UPSTREAM_JOBS <= set(jobs) and {"vra-plan", "customer-checks"} <= set(jobs)
    assert jobs["infra-test"]["strategy"]["matrix"]["shard"] == [1, 2, 3, 4, 5, 6]
    assert jobs["pipelines-test"]["strategy"]["matrix"]["group"] == [1, 2, 3]
    assert set(wf("security-scan.yml")["jobs"]) == {"ash", "codeql", "dependency-review", "sbom", "secrets"}


def _upstream_ci() -> dict | None:
    repo = os.environ.get("VAMS_REPO")
    if not repo:
        return None
    p = subprocess.run(["git", "-C", repo, "show", "origin/development:.github/workflows/ci.yml"],
                       capture_output=True, text=True)
    return yaml.safe_load(p.stdout) if p.returncode == 0 else None


def test_pr_checks_run_the_same_commands_as_upstream_ci():
    up = _upstream_ci()
    if up is None:
        pytest.skip("set VAMS_REPO to compare with the upstream ci.yml (read-only git show)")
    ours = {**wf("pr-checks.yml")["jobs"], **wf("security-scan.yml")["jobs"]}
    assert set(up["jobs"]) <= set(ours), "a new upstream ci.yml job is not mirrored"

    def norm(cmd: str) -> str:
        cmd = re.sub(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", lambda m: "${" + m.group(1).upper() + "}", cmd)
        return re.sub(r"\s+", " ", cmd.replace('"', "").replace("\\\n", " ")).strip()

    for job_id, job in up["jobs"].items():
        mine = " ".join(norm(str(s.get("run", ""))) for s in ours[job_id].get("steps") or [])
        for s in job.get("steps") or []:
            run = str(s.get("run", "")).strip()
            if not run or job_id == "ash" or "export REGION" in run:
                continue  # ASH runs in security-scan with SARIF; synth passes region as CDK context
            first = norm(run.splitlines()[0]).replace("npm@11.19.1", "npm@${NPM_VERSION}")
            assert first.split(" --")[0] in mine, (job_id, first)


def test_pr_checks_never_export_vams_config_variables():
    from vra.ledger import VAMS_CONFIG_ENV_VARS
    doc = wf("pr-checks.yml")
    for job_id, job in doc["jobs"].items():
        for scope in [job.get("env") or {}] + [s.get("env") or {} for s in job.get("steps") or []]:
            assert not set(scope) & set(VAMS_CONFIG_ENV_VARS), job_id
    run = "\n".join(str(s.get("run", "")) for _, _, s in steps(doc))
    assert "-c \"region=${SYNTH_REGION}\"" in run


def test_vra_plan_never_runs_with_secrets_for_foreign_pull_requests():
    job = wf("pr-checks.yml")["jobs"]["vra-plan"]
    assert "head.repo.full_name == github.repository" in job["if"]
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "--dry-run build" in run and " deploy " not in run


def test_hand_off_starts_one_execution_for_the_checked_commit():
    doc = wf("hand-off-to-codepipeline.yml")
    trig = doc[True]["workflow_run"]
    assert set(trig["workflows"]) == {wf("pr-checks.yml")["name"], wf("security-scan.yml")["name"]}
    job = doc["jobs"]["hand-off"]
    assert job["environment"] == "vra-handoff"
    # each check workflow by name and file
    assert {x.split("=")[0] for x in job["env"]["REQUIRED"].split(",")} == set(trig["workflows"])
    assert all("=.github/workflows/" in x for x in job["env"]["REQUIRED"].split(","))
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "--client-request-token \"vra-${SHA}\"" in run
    assert "revisionType=COMMIT_ID,revisionValue=${SHA}" in run
    assert "checks_passed.py" in run and "^[0-9a-f]{40}$" in run
    start = [s for s in job["steps"] if "start-pipeline-execution" in str(s.get("run", ""))][0]
    assert start["if"] == "steps.gate.outputs.state == 'ready'"


def test_upstream_release_merges_and_opens_a_pull_request_but_never_deploys():
    run = "\n".join(str(s.get("run", "")) for _, _, s in steps(wf("upstream-release.yml")))
    assert "run-vra.sh sync sync" in run and "merge --lane release --ref" in run
    assert not re.search(r"\b(deploy|promote|rollback)\b --", run)
    doc = wf("upstream-release.yml")
    assert set(doc[True]) == {"schedule", "workflow_dispatch"}


def test_upstream_preview_deploys_only_an_ungated_preview_env():
    doc = wf("upstream-preview.yml")
    run = "\n".join(str(s.get("run", "")) for _, _, s in steps(doc))
    assert "merge --lane preview" in run and "build --lane preview" in run
    deploy = doc["jobs"]["deploy-preview"]
    assert deploy["uses"] == "./.github/workflows/vra-deploy-env.yml"
    assert deploy["with"]["requires-approval"] is False and deploy["with"]["promote-from"] == ""
    assert doc["jobs"]["merge-build"]["environment"] == "vra-build"
    rb = doc["jobs"]["rollback-preview"]
    assert "failure()" in rb["if"] and "rollback --env" in "\n".join(str(s.get("run", "")) for s in rb["steps"])


def test_setup_vra_targets_this_server_and_never_stores_the_git_token():
    action = yaml.safe_load((GHA / "setup-vra/action.yml").read_text())
    run = "\n".join(str(s.get("run", "")) for s in action["runs"]["steps"])
    assert 'GH_HOST=${GITHUB_SERVER_URL#https://}' in run
    assert "credential.${GITHUB_SERVER_URL}.helper" in run and "${VRA_GIT_TOKEN}" in run
    assert "secrets." not in (GHA / "setup-vra/action.yml").read_text()


# ------------------------------------------------------------------ actionlint (when installed)
def test_actionlint_is_clean():
    exe = os.environ.get("VRA_ACTIONLINT") or shutil.which("actionlint")
    if not exe:
        pytest.skip("actionlint not installed (VRA_ACTIONLINT=<binary> to run it)")
    args = [exe]
    sc = os.environ.get("VRA_SHELLCHECK") or shutil.which("shellcheck")
    args.append(f"-shellcheck={sc}" if sc else "-shellcheck=")
    p = subprocess.run(args + [str(p) for p in WORKFLOWS], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
