# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CodePipeline lane scripts (0.5.0), offline with a fake `vra` on PATH.

- run-vra.sh inside a pipeline execution binds the run id to the execution and the action; a retry of the action
  resumes a parked run (BLOCKED_ON_IT, NEEDS_HUMAN) instead of starting over, and a run that ended FAILED gets a
  fresh attempt id. Outside a pipeline nothing changes.
- BLOCKED_ON_IT leaves BLOCKED_ON_IT.md (the IT requests and IT packages in the action's artifact).
- pipeline-watch.sh: sync, then one `vra merge` per lane with something new, then push-source for S3 sources.
- auto-rollback reads the env's rollbackPolicy; every buildspec ships the IT package directory as an artifact.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

CICD = Path(__file__).resolve().parents[1]
SCRIPTS = CICD / "codebuild" / "scripts"
_spec = importlib.util.spec_from_file_location("pipeline_ci", SCRIPTS / "pipeline_ci.py")
pci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pci)

EXEC = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"


# ------------------------------------------------------------------ run-plan
def test_outside_a_pipeline_or_for_unbound_commands_vra_picks_the_run_id():
    assert pci.run_plan("deploy-dev", "deploy", None, []) == ("plain", "")
    assert pci.run_plan("report-dev", "report", EXEC, []) == ("plain", "")


def test_first_attempt_starts_a_run_bound_to_the_execution():
    assert pci.run_plan("deploy-dev", "deploy", EXEC, []) == ("start", f"cp-{EXEC}-deploy-dev")


@pytest.mark.parametrize("status", ["BLOCKED_ON_IT", "NEEDS_HUMAN", "RUNNING", "SUCCEEDED"])
def test_retry_resumes_the_parked_run(status):
    base = f"cp-{EXEC}-promote-test"
    runs = [{"runId": base, "status": status, "startedAt": "2026-11-04T10:00:00Z"}]
    assert pci.run_plan("promote-test", "promote", EXEC, runs) == ("resume", base)


def test_retry_after_a_failed_run_starts_a_new_attempt_and_resumes_that_one_later():
    base = f"cp-{EXEC}-test-dev"
    runs = [{"runId": base, "status": "TESTS_FAILED", "startedAt": "2026-11-04T10:00:00Z"}]
    assert pci.run_plan("test-dev", "test", EXEC, runs) == ("start", f"{base}-r2")
    runs.append({"runId": f"{base}-r2", "status": "BLOCKED_ON_IT", "startedAt": "2026-11-04T11:00:00Z"})
    assert pci.run_plan("test-dev", "test", EXEC, runs) == ("resume", f"{base}-r2")
    runs[-1]["status"] = "FAILED"
    assert pci.run_plan("test-dev", "test", EXEC, runs) == ("start", f"{base}-r3")


# ------------------------------------------------------------------ run-vra.sh with a fake vra
def _fake_vra(tmp_path: Path, runs: list[dict], exit_code: int = 0) -> tuple[dict, Path]:
    """A fake `vra`: `status` prints the given runs; any other command records argv and exits exit_code."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    argv = tmp_path / "argv.txt"
    status = json.dumps({"exitCode": 0, "result": {"runs": runs}})
    (bin_dir / "vra").write_text(
        "#!/usr/bin/env bash\n"
        "for a in \"$@\"; do [ \"$a\" = status ] && { cat <<'J'\n" + status + "\nJ\nexit 0; }; done\n"
        f"printf '%s\\n' \"$@\" > {argv}\n"
        f"echo '{{\"exitCode\": {exit_code}, \"runId\": \"cp-run\"}}'\nexit {exit_code}\n")
    (bin_dir / "vra").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("VRA_")}
    # pipeline_ci.py asks the vra on PATH (no vra package importable from this interpreter's view)
    env.update({"PATH": f"{bin_dir}:/usr/bin:/bin", "VRA_OUT_DIR": str(tmp_path / "out"), "HOME": str(tmp_path),
                "VRA_HOME": str(tmp_path / "home"), "PYTHONPATH": ""})
    from vra.ledger import VAMS_CONFIG_ENV_VARS
    for v in VAMS_CONFIG_ENV_VARS:
        env.pop(v, None)
    return env, argv


def _run(env, *args):
    return subprocess.run(["bash", str(SCRIPTS / "run-vra.sh"), *args], env=env, capture_output=True, text=True)


def test_run_vra_binds_the_run_id_inside_a_pipeline(tmp_path):
    env, argv = _fake_vra(tmp_path, [])
    r = _run({**env, "VRA_PIPELINE_EXECUTION_ID": EXEC}, "deploy-dev", "deploy", "--env", "dev", "--build", "b1")
    assert r.returncode == 0, r.stderr
    args = argv.read_text().split("\n")
    assert args[0] == "deploy" and args[args.index("--run-id") + 1] == f"cp-{EXEC}-deploy-dev"


def test_run_vra_resumes_a_blocked_run_on_retry(tmp_path):
    env, argv = _fake_vra(tmp_path, [{"runId": f"cp-{EXEC}-deploy-dev", "status": "BLOCKED_ON_IT"}])
    r = _run({**env, "VRA_PIPELINE_EXECUTION_ID": EXEC}, "deploy-dev", "deploy", "--env", "dev", "--build", "b1")
    assert r.returncode == 0, r.stderr
    assert argv.read_text().split("\n")[:2] == ["resume", f"cp-{EXEC}-deploy-dev"]
    assert "resuming run" in r.stderr


def test_run_vra_unchanged_outside_a_pipeline_and_when_turned_off(tmp_path):
    env, argv = _fake_vra(tmp_path, [{"runId": f"cp-{EXEC}-deploy-dev", "status": "BLOCKED_ON_IT"}])
    _run(env, "deploy-dev", "deploy", "--env", "dev", "--build", "b1")
    assert "--run-id" not in argv.read_text() and argv.read_text().startswith("deploy")
    _run({**env, "VRA_PIPELINE_EXECUTION_ID": EXEC, "VRA_PIPELINE_RESUME": "false"}, "deploy-dev", "deploy")
    assert argv.read_text().startswith("deploy") and "--run-id" not in argv.read_text()


def test_blocked_on_it_fails_the_action_with_the_it_package_summary(tmp_path):
    env, _argv = _fake_vra(tmp_path, [], exit_code=10)
    home = tmp_path / "home"
    (home / "state" / "runs" / "cp-run" / "it-requests").mkdir(parents=True)
    (home / "state" / "runs" / "cp-run" / "it-requests" / "IT-REQUEST-1.md").write_text("x")
    (home / "state" / "iam" / "dev" / "1").mkdir(parents=True)
    (home / "state" / "iam" / "dev" / "1" / "IT-ROLES.md").write_text("x")
    r = _run({**env, "VRA_PIPELINE_EXECUTION_ID": EXEC}, "deploy-dev", "deploy", "--env", "dev")
    assert r.returncode == 10
    md = (tmp_path / "out" / "BLOCKED_ON_IT.md").read_text()
    assert "IT-REQUEST-1.md" in md and "state/iam/dev/1/IT-ROLES.md" in md and "Retry failed actions" in md


# ------------------------------------------------------------------ watch
def test_watch_merges_lists_lanes_with_something_new():
    env = {"result": {"newRelease": "v2.7.0", "newPreviewCommit": "0123456789abcdef0123"}}
    assert pci.watch_merges(env) == [("release", "v2.7.0"), ("preview", "0123456789ab")]
    assert pci.watch_merges({"result": {}}) == []


def test_pipeline_watch_syncs_merges_and_feeds_s3_sources(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.txt"
    (bin_dir / "vra").write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$*\" >> {log}\n"
        "case \"$1\" in sync) echo '{\"exitCode\": 0, \"result\": {\"newRelease\": \"v9.9.9\"}}' ;;\n"
        "*) echo '{\"exitCode\": 0}' ;; esac\n")
    (bin_dir / "vra").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("VRA_")}
    env.update({"PATH": f"{bin_dir}:/usr/bin:/bin", "VRA_OUT_DIR": str(tmp_path / "out"), "HOME": str(tmp_path),
                "VRA_SOURCE_BUCKET": "b", "VRA_SOURCE_RELEASE_KEY": "fork/main.zip",
                "VRA_RELEASE_PIPELINE": "vra-release"})
    r = subprocess.run(["bash", str(SCRIPTS / "pipeline-watch.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert calls[0].startswith("sync")
    assert calls[1].startswith("merge --lane release --ref v9.9.9")
    assert calls[2].startswith("pipeline push-source --lane release --bucket b --key fork/main.zip --pipeline vra-release")
    assert not any(c.startswith(("deploy", "promote", "watch")) for c in calls), "the watch never deploys"


# ------------------------------------------------------------------ rollback + artifacts
def test_rollback_policy_reads_the_ledger(tmp_path):
    led = tmp_path / "ledger"
    (led / "envs" / "dev").mkdir(parents=True)
    (led / "vra.yaml").write_text("project: {name: x}\n")
    (led / "envs" / "dev" / "env.yaml").write_text(yaml.safe_dump({
        "name": "dev", "account": "111111111111", "region": "us-east-1", "lane": "release",
        "rollbackPolicy": "ask"}))
    assert pci.rollback_policy(led, "dev") == "ask"
    assert pci.rollback_policy(led, "nope") == "ask"  # unknown: never roll back on our own


@pytest.mark.parametrize("spec", ["buildspec-build.yml", "buildspec-deploy.yml", "buildspec-test.yml",
                                  "buildspec-watch.yml"])
def test_every_pipeline_buildspec_ships_the_it_packages(spec):
    doc = yaml.safe_load((CICD / "codebuild" / spec).read_text())
    assert "vra-home/state/iam/**/*" in doc["artifacts"]["files"]
    assert "vra-out/**/*" in doc["artifacts"]["files"]


def test_watch_buildspec_can_take_the_package_from_the_s3_source():
    text = (CICD / "codebuild" / "buildspec-watch.yml").read_text()
    assert "VRA_PACKAGE_S3_URI" in text and "aws s3 cp" in text
