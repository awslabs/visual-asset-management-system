#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Turn an ASH (Automated Security Helper) v3 results file into a job summary and SARIF files for code scanning.

  ash_report.py --results ash-output/ash_aggregated_results.json --sarif-dir ash-sarif
                [--summary $GITHUB_STEP_SUMMARY] [--exit-code $ASH_EXIT_CODE] [--fail-on none|high|critical]

- The summary lists unsuppressed HIGH / CRITICAL findings (the same table as the upstream VAMS ci.yml ASH job).
- Each SARIF run of the results file is written to its own file in --sarif-dir (one tool per file), so
  github/codeql-action/upload-sarif accepts the directory under one category.
- Exit 0 (report only, the upstream default), 1 when --fail-on is met, 3 when the results file is missing or
  unreadable (the scan did not run).

Python standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LEVEL_TO_SEVERITY = {"error": "HIGH", "warning": "MEDIUM", "note": "LOW", "none": "INFO"}
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
CAP = 50


def severity(result: dict) -> str:
    props = result.get("properties") or {}
    sev = str(props.get("issue_severity") or "").upper()
    if sev not in SEVERITIES:
        sev = LEVEL_TO_SEVERITY.get(str(result.get("level") or "").lower(), "MEDIUM")
    return sev


def findings(report: dict) -> list[list[str]]:
    rows = []
    for run in (report.get("sarif") or {}).get("runs") or []:
        tool = (((run.get("tool") or {}).get("driver") or {}).get("name")) or "unknown"
        for result in run.get("results") or []:
            if result.get("suppressions"):
                continue
            sev = severity(result)
            if sev not in ("CRITICAL", "HIGH"):
                continue
            props = result.get("properties") or {}
            scanner = props.get("scanner_name") or (props.get("scanner_details") or {}).get("tool_name") or tool
            loc = (result.get("locations") or [{}])[0]
            uri = ((loc.get("physicalLocation") or {}).get("artifactLocation") or {}).get("uri") or "-"
            rows.append([sev, str(scanner), str(result.get("ruleId") or "-"), str(uri)])
    return rows


def split_sarif(report: dict, out_dir: Path) -> list[Path]:
    sarif = report.get("sarif") or {}
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for n, run in enumerate(sarif.get("runs") or [], 1):
        tool = (((run.get("tool") or {}).get("driver") or {}).get("name")) or "unknown"
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", tool).strip("-") or "unknown"
        doc = {"$schema": sarif.get("$schema", "https://json.schemastore.org/sarif-2.1.0.json"),
               "version": sarif.get("version", "2.1.0"), "runs": [run]}
        path = out_dir / f"{n:02d}-{slug}.sarif"
        path.write_text(json.dumps(doc), encoding="utf-8")
        written.append(path)
    return written


def summary_text(report: dict, rows: list[list[str]], exit_code: str) -> str:
    meta = report.get("metadata") or {}
    counts = (meta.get("summary_stats") or {}).get("severity_counts") or {}
    names = ["critical", "high", "medium", "low", "info", "suppressed"]
    count_text = ", ".join(f"{name} {counts.get(name, 0)}" for name in names)
    meaning = {"0": "no actionable findings", "2": "actionable findings"}.get(exit_code, "see the scan log")
    out = ["## ASH security scan", "",
           f"- ASH version: {meta.get('tool_version') or 'not recorded'}",
           f"- Result: exit code {exit_code} ({meaning})",
           f"- Findings by severity: {count_text}", ""]
    if rows:
        clean = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
        out += ["| Severity | Scanner | Rule | File |", "| --- | --- | --- | --- |"]
        out += ["| " + " | ".join(clean(v) for v in row) + " |" for row in rows[:CAP]]
        if len(rows) > CAP:
            out += ["", f"{len(rows) - CAP} more not listed."]
        out += ["", f"{len(rows)} unsuppressed HIGH or CRITICAL findings remain."]
    else:
        out.append("No unsuppressed HIGH or CRITICAL findings remain.")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--sarif-dir", type=Path, required=True)
    ap.add_argument("--summary", type=Path)
    ap.add_argument("--exit-code", default="unknown")
    ap.add_argument("--fail-on", choices=["none", "high", "critical"], default="none")
    ap.add_argument("--json-out", type=Path, help="write the unsuppressed counts as JSON (vra-ash-summary.json)")
    ns = ap.parse_args(argv)
    try:
        report = json.loads(ns.results.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        msg = f"The scan did not run: no readable results file (exit code {ns.exit_code})."
        if ns.summary:
            with ns.summary.open("a", encoding="utf-8") as fh:
                fh.write(f"## ASH security scan\n\n{msg}\n")
        print(f"::error title=ASH scan::{msg}")
        return 3
    rows = findings(report)
    files = split_sarif(report, ns.sarif_dir)
    text = summary_text(report, rows, ns.exit_code)
    if ns.summary:
        with ns.summary.open("a", encoding="utf-8") as fh:
            fh.write(text)
    print(f"ash report: {len(rows)} unsuppressed HIGH/CRITICAL, {len(files)} SARIF file(s) in {ns.sarif_dir}")
    if ns.json_out:
        # read by the hand-off gate (checks_passed.py ash): unsuppressed findings by severity
        counts = {s: sum(1 for r in rows if r[0] == s) for s in ("CRITICAL", "HIGH")}
        ns.json_out.parent.mkdir(parents=True, exist_ok=True)
        ns.json_out.write_text(json.dumps({"counts": counts, "failOn": ns.fail_on, "exitCode": ns.exit_code},
                                          indent=2), encoding="utf-8")
    if ns.fail_on == "critical" and any(r[0] == "CRITICAL" for r in rows):
        return 1
    if ns.fail_on == "high" and rows:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
