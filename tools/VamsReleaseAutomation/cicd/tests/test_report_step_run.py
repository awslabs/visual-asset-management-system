# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Every CI deploy/test/watch job ends with `run-vra.sh report-<env> report --notify`.
Without a run id `vra report` picks the newest run in the home by start time. A Deploy-prod retry
resumed a run started two days earlier, so its POST_BUILD reported and notified the morning's watch-sync run
(sync-20261109T060202Z-a7ac) instead of the promote it had just finished. run-vra.sh now remembers the run of the
job's own vra step and hands it to a later `report` that names none."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

PKG = Path(__file__).resolve().parents[2]
SCRIPTS = PKG / "cicd" / "codebuild" / "scripts"

# a fake vra: logs its argv, prints the envelope of the run the command names (resume <id>) or of the report
FAKE = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${FAKE_LOG}"
if [ "$1" = "resume" ]; then rid="$2"; elif [ "$1" = "report" ] && [ "${2#-}" = "$2" ]; then rid="$2"
else rid="newest-run-in-home"; fi
printf '{"exitCode": 0, "status": "OK", "runId": "%s"}\\n' "${rid}"
"""


def _env(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "vra"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("VRA_")}
    env.update({"PATH": f"{bin_dir}:{os.environ['PATH']}", "VRA_OUT_DIR": str(tmp_path / "out"),
                "FAKE_LOG": str(tmp_path / "argv.log")})
    return env


def _run(env, *args):
    r = subprocess.run(["bash", str(SCRIPTS / "run-vra.sh"), *args], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "argv.log").read_text().splitlines()


def test_report_after_a_resume_reports_the_resumed_run(tmp_path):
    env = _env(tmp_path)
    _run(env, "promote-prod", "resume", "cp-0a1b2c3d-promote-prod")
    _run(env, "report-prod", "report", "--notify")
    report_call = _calls(tmp_path)[-1]
    assert report_call.startswith("report cp-0a1b2c3d-promote-prod --notify"), report_call
    assert json.loads((tmp_path / "out" / "report-prod.result.json").read_text())["runId"] == \
        "cp-0a1b2c3d-promote-prod"


def test_an_explicit_run_id_is_kept(tmp_path):
    env = _env(tmp_path)
    _run(env, "promote-prod", "resume", "cp-a")
    _run(env, "report-prod", "report", "cp-b", "--notify")
    assert _calls(tmp_path)[-1].startswith("report cp-b --notify")


def test_without_an_earlier_step_report_still_picks_the_newest(tmp_path):
    env = _env(tmp_path)
    _run(env, "report-prod", "report", "--notify")
    assert _calls(tmp_path)[-1].startswith("report --notify")
    # a report never becomes the remembered run
    assert not (tmp_path / "out" / "last-run-id").exists()
