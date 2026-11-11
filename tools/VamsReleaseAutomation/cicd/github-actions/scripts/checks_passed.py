#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Have all required check workflows passed for one trunk commit? (hand-off-to-codepipeline.yml)

  checks_passed.py --runs FILE --sha SHA --require vra-pr-checks=.github/workflows/vra-pr-checks.yml,...
                   --branch main [--event push] [--print-run NAME]
  checks_passed.py ash --summary FILE [--fail-on high|critical]

FILE = `gh api "repos/<repo>/actions/runs?head_sha=<sha>&per_page=100"` (JSON). For each required workflow the
LATEST run for SHA counts (highest run_number, then run_attempt), from the given event only, on the trunk branch
only, and only from the workflow FILE named after `=`: a branch cannot satisfy the gate with its own workflow of
the same name. A name without `=<path>` matches any file (not recommended).

Prints one word and exits:
  ready    every required workflow's latest run completed with conclusion success          exit 0
  waiting  at least one has no run yet or is still running (another trigger will retry)    exit 0
  failed   at least one completed with any other conclusion                               exit 1
With --print-run NAME it prints the run id of NAME's counted run instead (empty when none).

`ash`: reads the vra-ash-summary.json that ash_report.py writes; exit 1 when unsuppressed findings at or above
--fail-on (default high) exist, 3 when the summary is missing (the scan did not run). The hand-off reads it
from the security-scan run it counted, so a scan that only reported findings still stops the release.

Python standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")


def parse_required(spec: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for item in (x.strip() for x in spec.split(",")):
        if not item:
            continue
        name, _, path = item.partition("=")
        out[name.strip()] = path.strip() or None
    return out


def decide(runs: list[dict], sha: str, required, event: str | None = "push",
           branch: str | None = None) -> tuple[str, dict, dict]:
    req = parse_required(",".join(required)) if isinstance(required, list) else dict(required)
    latest: dict[str, dict] = {}
    for r in runs:
        if r.get("head_sha") != sha or (event and r.get("event") != event):
            continue
        if branch and r.get("head_branch") != branch:
            continue
        name = r.get("name")
        if name not in req:
            continue
        want_path = req[name]
        if want_path and str(r.get("path") or "").split("@", 1)[0] != want_path:
            continue          # a workflow of the same name in another file (a branch's own copy)
        key = (int(r.get("run_number") or 0), int(r.get("run_attempt") or 0))
        cur = latest.get(name)
        if cur is None or key > (int(cur.get("run_number") or 0), int(cur.get("run_attempt") or 0)):
            latest[name] = r
    detail = {}
    state = "ready"
    for name in req:
        r = latest.get(name)
        if r is None:
            detail[name] = "missing"
            state = "waiting" if state == "ready" else state
        elif r.get("status") != "completed":
            detail[name] = str(r.get("status"))
            state = "waiting" if state == "ready" else state
        elif r.get("conclusion") != "success":
            detail[name] = f"completed/{r.get('conclusion')}"
            state = "failed"
        else:
            detail[name] = "success"
    return state, detail, latest


def ash_gate(summary: Path, fail_on: str = "high") -> tuple[int, str]:
    if not summary.exists():
        return 3, f"no ASH summary at {summary}: the scan did not run"
    try:
        doc = json.loads(summary.read_text(encoding="utf-8"))
        counts = {k.upper(): int(v) for k, v in (doc.get("counts") or {}).items()}
    except (ValueError, AttributeError, TypeError):
        return 3, f"{summary} is not an ASH summary"
    level = fail_on.upper()
    if level not in SEVERITIES:
        return 2, f"--fail-on {fail_on} is not one of {', '.join(s.lower() for s in SEVERITIES)}"
    over = {s: counts.get(s, 0) for s in SEVERITIES[:SEVERITIES.index(level) + 1] if counts.get(s, 0)}
    if over:
        return 1, f"ASH findings at or above {level}: {over}"
    return 0, f"no unsuppressed ASH findings at or above {level}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["ash"]:
        ap = argparse.ArgumentParser(description="ASH summary gate")
        ap.add_argument("--summary", type=Path, required=True)
        ap.add_argument("--fail-on", default="high")
        ns = ap.parse_args(argv[1:])
        code, msg = ash_gate(ns.summary, ns.fail_on)
        print(f"checks_passed: {msg}", file=sys.stderr)
        print("ready" if code == 0 else "failed")
        return code
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--require", required=True, help="comma list of workflow name=.github/workflows/<file>")
    ap.add_argument("--branch", default="", help="the trunk: only runs on this branch count ('' = any)")
    ap.add_argument("--event", default="push", help="workflow trigger event to count ('' = any)")
    ap.add_argument("--print-run", default="", help="print the counted run id of this workflow name")
    ns = ap.parse_args(argv)
    doc = json.loads(ns.runs.read_text(encoding="utf-8"))
    runs = doc.get("workflow_runs", []) if isinstance(doc, dict) else doc
    required = parse_required(ns.require)
    if not required:
        print("checks_passed: --require is empty", file=sys.stderr)
        return 2
    state, detail, latest = decide(runs, ns.sha, required, ns.event or None, ns.branch or None)
    for name, d in detail.items():
        print(f"checks_passed: {name}: {d}", file=sys.stderr)
    if ns.print_run:
        print((latest.get(ns.print_run) or {}).get("id") or "")
        return 0
    print(state)
    return 1 if state == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
