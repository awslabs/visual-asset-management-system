# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""REVIEW-050 fix wave: the GitHub Actions samples, the hand-off gate and the OIDC trust source."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

CICD = Path(__file__).resolve().parents[1]
GA = CICD / "github-actions"


def _mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m


checks = _mod("checks_passed", GA / "scripts/checks_passed.py")
trust = _mod("github_trust", CICD / "oidc/github_trust.py")
HOME_WORKFLOWS = ["deploy.yml", "upstream-preview.yml", "upstream-release.yml", "vra-deploy-env.yml",
                  "vra-operator.yml", "vra-release.yml", "vra-watch.yml"]


def wf(name: str) -> dict:
    return yaml.safe_load((GA / name).read_text())


# ------------------------------------------------------------------ R50-11
@pytest.mark.parametrize("name", HOME_WORKFLOWS)
def test_jobs_that_run_fork_code_hold_no_home_write_token(name):
    text = (GA / name).read_text()
    assert "secrets.VRA_HOME_TOKEN" not in text, name
    assert "home-push" not in text, name
    doc = wf(name)
    for job_id, job in doc["jobs"].items():
        if "uses" in job:
            continue
        for step in job.get("steps") or []:
            w = step.get("with") or {}
            if str(step.get("uses", "")).startswith("actions/checkout@") and w.get("path") == "vra-home":
                assert w.get("persist-credentials") is False, (name, job_id)
                assert "token" not in w, (name, job_id)
        if any((s.get("with") or {}).get("path") == "vra-home" for s in job.get("steps") or []):
            assert (job.get("env") or {}).get("VRA_HOME_DEFER_PUSH") == "1", (name, job_id)
            publish = doc["jobs"].get(f"publish-{job_id}")
            assert publish and publish["uses"] == "./.github/workflows/vra-home-publish.yml", (name, job_id)
            assert publish["needs"] == [job_id] and publish["if"] == "always()"
            names = [(s.get("with") or {}).get("name") for s in job["steps"]]
            assert publish["with"]["state-artifact"] in names, (name, job_id)


def test_the_publish_job_is_the_only_holder_of_the_home_write_token():
    doc = wf("vra-home-publish.yml")
    (job,) = doc["jobs"].values()
    holders = [s for s in job["steps"] if "VRA_HOME_TOKEN" in (s.get("env") or {})]
    assert len(holders) == 1 and "adopt" in holders[0]["run"] and "home-push" in holders[0]["run"]
    for s in job["steps"]:
        if str(s.get("uses", "")).startswith("actions/checkout@"):
            assert s["with"]["persist-credentials"] is False
    assert not any("npm" in str(s.get("run", "")) for s in job["steps"]), "no fork code in the publish job"


def test_adopt_copies_only_engine_paths_and_refuses_links(tmp_path):
    home = tmp_path / "home"
    subprocess.run(["git", "init", "-q", str(home)], check=True)
    (home / "vra-home.yaml").write_text("homeId: x\n")
    subprocess.run(["git", "-C", str(home), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(home), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm",
                    "init"], check=True)
    src = tmp_path / "art"
    (src / "state/runs/r1").mkdir(parents=True)
    (src / "state/runs/r1/state.json").write_text("{}")
    (src / "ledger").mkdir()
    (src / "ledger/approval.yaml").write_text("x")
    env = {"VRA_HOME": str(home), "PATH": "/usr/bin:/bin", "VRA_HOME_REF": "main"}
    sh = CICD / "codebuild/scripts/vra-home.sh"
    r = subprocess.run(["bash", str(sh), "adopt", str(src)], env=env, capture_output=True, text=True)
    assert (home / "state/runs/r1/state.json").exists() and not (home / "ledger").exists(), r.stderr
    (src / "state/link").symlink_to("/etc/passwd")
    r = subprocess.run(["bash", str(sh), "adopt", str(src)], env=env, capture_output=True, text=True)
    assert r.returncode == 20 and "symbolic links" in r.stderr


def test_checkout_clones_into_the_folder_the_build_artifact_already_filled(tmp_path):
    """E2E-050 B-050-16: a deploy job's primary input (the build's artifact) carries vra-home/state/iam/, so
    `git clone` refused the non-empty folder and no deploy stage could start. The checkout clones anyway, adds
    back only the carried state files the home lacks, and keeps the home's committed copies."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "vrahome", str(repo)], check=True)
    (repo / "ledger").mkdir()
    (repo / "ledger/vra.yaml").write_text("x: 1\n")
    (repo / "vra-home.yaml").write_text("homeId: x\n")
    (repo / "state/iam/prod/2").mkdir(parents=True)
    (repo / "state/iam/prod/2/package.json").write_text('{"from": "home"}')
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm",
                    "init"], check=True)
    home = tmp_path / "src/vra-home"
    (home / "state/iam/prod/2").mkdir(parents=True)
    (home / "state/iam/prod/2/package.json").write_text('{"from": "artifact"}')
    (home / "state/iam/prod/3").mkdir(parents=True)
    (home / "state/iam/prod/3/package.json").write_text('{"new": true}')
    env = {"VRA_HOME": str(home), "PATH": "/usr/bin:/bin", "VRA_HOME_REF": "vrahome", "VRA_HOME_REPO": str(repo),
           "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(CICD / "codebuild/scripts/vra-home.sh"), "checkout"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (home / ".git").is_dir() and (home / "ledger/vra.yaml").exists()
    assert json.loads((home / "state/iam/prod/2/package.json").read_text()) == {"from": "home"}
    assert json.loads((home / "state/iam/prod/3/package.json").read_text()) == {"new": True}


# ------------------------------------------------------------------ R50-13 / R50-17
def _run(name, path, branch="main", conclusion="success", n=1, rid=None):
    return {"name": name, "path": path, "head_sha": "a" * 40, "event": "push", "head_branch": branch,
            "status": "completed", "conclusion": conclusion, "run_number": n, "run_attempt": 1, "id": rid or n}


REQ = {"vra-pr-checks": ".github/workflows/vra-pr-checks.yml",
       "vra-security-scan": ".github/workflows/vra-security-scan.yml"}


def test_checks_count_only_the_trunk_and_the_real_workflow_files():
    good = [_run("vra-pr-checks", REQ["vra-pr-checks"]), _run("vra-security-scan", REQ["vra-security-scan"], rid=9)]
    assert checks.decide(good, "a" * 40, REQ, "push", "main")[0] == "ready"
    # a branch's own workflow named vra-security-scan, in another file or on another branch, does not count
    fake = [_run("vra-pr-checks", REQ["vra-pr-checks"]),
            _run("vra-security-scan", ".github/workflows/my-copy.yml", n=5),
            _run("vra-security-scan", REQ["vra-security-scan"], branch="feature", n=6)]
    assert checks.decide(fake, "a" * 40, REQ, "push", "main")[0] == "waiting"
    state, _detail, latest = checks.decide(good, "a" * 40, REQ, "push", "main")
    assert latest["vra-security-scan"]["id"] == 9


def test_ash_gate_reads_the_summary(tmp_path):
    p = tmp_path / "s.json"
    assert checks.ash_gate(p)[0] == 3
    p.write_text(json.dumps({"counts": {"CRITICAL": 0, "HIGH": 2}}))
    assert checks.ash_gate(p, "high")[0] == 1 and checks.ash_gate(p, "critical")[0] == 0
    p.write_text(json.dumps({"counts": {"CRITICAL": 0, "HIGH": 0}}))
    assert checks.ash_gate(p)[0] == 0


def test_security_scan_fails_on_high_for_pushes_by_default_and_hand_off_reads_ash():
    text = (GA / "security-scan.yml").read_text()
    assert "(vars.VRA_ASH_FAIL_ON || 'high')" in text and "(vars.VRA_ASH_FAIL_ON_PR || 'none')" in text
    assert "--json-out ash-output/vra-ash-summary.json" in text
    hand = (GA / "hand-off-to-codepipeline.yml").read_text()
    assert "checks_passed.py ash" in hand and "compare/${SHA}...${TRUNK}" in hand
    assert ".github/workflows/vra-security-scan.yml" in hand and '--branch "${TRUNK}"' in hand


# ------------------------------------------------------------------ R50-18
def test_gated_envs_isolate_tests_and_prod_class_refuses_unisolated_tests():
    text = (GA / "vra-deploy-env.yml").read_text()
    assert "!(inputs.isolate-tests || inputs.requires-approval)" in text
    assert "if: (inputs.isolate-tests || inputs.requires-approval) && inputs.test-suites != ''" in text
    assert "ci_graph.py is-prod-class" in text and "exit 20" in text


# ------------------------------------------------------------------ R50-16
def test_one_trust_source_for_github_com_ghec_and_ghes():
    assert trust.issuer_host("https://github.com") == "token.actions.githubusercontent.com"
    assert trust.issuer_host("https://github.com", "acme") == "token.actions.githubusercontent.com/acme"
    assert trust.issuer_host("https://acme.ghe.com") == "token.actions.acme.ghe.com"
    assert trust.issuer_host("https://ghes.example.com") == "ghes.example.com/_services/token"
    arn = "arn:aws:iam::111111111111:oidc-provider/ghes.example.com/_services/token"
    pol = trust.trust_policy(arn, [trust.subject("prod", "acme/home", owner_id="1", repository_id="2")])
    cond = pol["Statement"][0]["Condition"]
    assert cond["StringLike"] == {"ghes.example.com/_services/token:sub":
                                  ["repo:acme@1/home@2:environment:prod"]}
    with pytest.raises(ValueError):
        trust.trust_policy(arn, ["*"])
    rendered = trust.render_template("ghes.example.com/_services/token")
    assert "token.actions.githubusercontent.com:" not in rendered
    assert "ghes.example.com/_services/token:sub:" in rendered and "https://ghes.example.com/_services/token" in rendered


def test_the_template_and_the_pipeline_use_the_trust_sources_subject_formats():
    tpl = (CICD / "oidc/vra-ci-oidc-role.yaml").read_text()
    assert trust.IMMUTABLE_SUBJECT.format(owner="${O}", owner_id="${RepositoryOwnerId}", name="${R}",
                                          repository_id="${RepositoryId}", env="${E}") in tpl
    assert trust.NAME_SUBJECT.format(repo="${Repository}", env="${E}") in tpl
    ts = (CICD / "pipeline/lib/config.ts").read_text()
    assert f'"{trust.IMMUTABLE_SUBJECT}"' in ts


def test_github_trust_cli_prints_a_policy():
    r = subprocess.run([sys.executable, str(CICD / "oidc/github_trust.py"), "policy", "--provider-arn",
                        "arn:aws:iam::111111111111:oidc-provider/token.actions.githubusercontent.com",
                        "--repo", "example-org/vra-home", "--environment", "prod"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert re.search(r"repo:example-org/vra-home:environment:prod", json.dumps(doc))
