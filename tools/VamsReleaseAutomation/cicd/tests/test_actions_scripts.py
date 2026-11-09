# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the GitHub Actions helper scripts: the hand-off gate and the ASH report."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PKG = Path(__file__).resolve().parents[2]
SCRIPTS = PKG / "cicd/github-actions/scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


gate = _load("checks_passed")
ash = _load("ash_report")
SHA = "a" * 40
REQ = ["vra-pr-checks", "vra-security-scan"]


def _run(name, status="completed", conclusion="success", n=1, attempt=1, event="push", sha=SHA):
    return {"name": name, "status": status, "conclusion": conclusion, "run_number": n, "run_attempt": attempt,
            "event": event, "head_sha": sha}


def test_gate_ready_only_when_every_required_workflow_succeeded():
    assert gate.decide([_run(REQ[0]), _run(REQ[1])], SHA, REQ)[0] == "ready"
    assert gate.decide([_run(REQ[0])], SHA, REQ)[:2] == ("waiting", {REQ[0]: "success", REQ[1]: "missing"})
    assert gate.decide([_run(REQ[0]), _run(REQ[1], status="in_progress", conclusion=None)], SHA, REQ)[0] == "waiting"
    assert gate.decide([_run(REQ[0]), _run(REQ[1], conclusion="failure")], SHA, REQ)[0] == "failed"


def test_gate_counts_the_latest_run_of_this_commit_from_pushes_only():
    rerun_ok = [_run(REQ[0]), _run(REQ[1], conclusion="failure", n=4), _run(REQ[1], n=4, attempt=2)]
    assert gate.decide(rerun_ok, SHA, REQ)[0] == "ready"
    later_failed = [_run(REQ[0]), _run(REQ[1], n=4), _run(REQ[1], conclusion="failure", n=5)]
    assert gate.decide(later_failed, SHA, REQ)[0] == "failed"
    foreign = [_run(REQ[0]), _run(REQ[1], event="pull_request"), _run(REQ[1], sha="b" * 40)]
    assert gate.decide(foreign, SHA, REQ)[0] == "waiting"


def test_gate_cli_prints_the_state_and_fails_on_a_failed_check(tmp_path, capsys):
    f = tmp_path / "runs.json"
    f.write_text(json.dumps({"workflow_runs": [_run(REQ[0]), _run(REQ[1])]}))
    assert gate.main(["--runs", str(f), "--sha", SHA, "--require", ",".join(REQ)]) == 0
    assert capsys.readouterr().out.strip() == "ready"
    f.write_text(json.dumps({"workflow_runs": [_run(REQ[0], conclusion="cancelled"), _run(REQ[1])]}))
    assert gate.main(["--runs", str(f), "--sha", SHA, "--require", ",".join(REQ)]) == 1
    assert gate.main(["--runs", str(f), "--sha", SHA, "--require", " , "]) == 2


def _ash_results(tmp_path: Path) -> Path:
    report = {
        "metadata": {"tool_version": "3.7.1", "summary_stats": {"severity_counts": {"high": 1, "low": 1}}},
        "sarif": {"version": "2.1.0", "runs": [
            {"tool": {"driver": {"name": "bandit"}}, "results": [
                {"ruleId": "B602", "level": "error", "properties": {"issue_severity": "HIGH"},
                 "locations": [{"physicalLocation": {"artifactLocation": {"uri": "backend/x.py"}}}]},
                {"ruleId": "B101", "level": "note"},
                {"ruleId": "B603", "level": "error", "suppressions": [{"kind": "inSource"}]}]},
            {"tool": {"driver": {"name": "checkov / cfn"}}, "results": []}]},
    }
    path = tmp_path / "ash_aggregated_results.json"
    path.write_text(json.dumps(report))
    return path


def test_ash_report_writes_the_summary_and_one_sarif_file_per_scanner(tmp_path):
    summary = tmp_path / "summary.md"
    rc = ash.main(["--results", str(_ash_results(tmp_path)), "--sarif-dir", str(tmp_path / "sarif"),
                   "--summary", str(summary), "--exit-code", "2"])
    assert rc == 0, "report only by default (as upstream)"
    text = summary.read_text()
    assert "| HIGH | bandit | B602 | backend/x.py |" in text and "B603" not in text and "B101" not in text
    assert "1 unsuppressed HIGH or CRITICAL findings remain." in text
    files = sorted(p.name for p in (tmp_path / "sarif").iterdir())
    assert files == ["01-bandit.sarif", "02-checkov-cfn.sarif"]
    one = json.loads((tmp_path / "sarif" / files[0]).read_text())
    assert one["version"] == "2.1.0" and len(one["runs"]) == 1


def test_ash_report_threshold_and_missing_results(tmp_path):
    res = _ash_results(tmp_path)
    assert ash.main(["--results", str(res), "--sarif-dir", str(tmp_path / "s"), "--fail-on", "high"]) == 1
    assert ash.main(["--results", str(res), "--sarif-dir", str(tmp_path / "s"), "--fail-on", "critical"]) == 0
    summary = tmp_path / "summary.md"
    assert ash.main(["--results", str(tmp_path / "none.json"), "--sarif-dir", str(tmp_path / "s"),
                     "--summary", str(summary), "--exit-code", "1"]) == 3
    assert "did not run" in summary.read_text()
