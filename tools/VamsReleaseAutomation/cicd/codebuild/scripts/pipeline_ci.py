#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Helpers the CodePipeline lane's buildspecs call (run them with ``vra-py.sh pipeline_ci.py ...``).

  pipeline_ci.py run-plan <name> <vra-subcommand>
      Inside a pipeline execution (VRA_PIPELINE_EXECUTION_ID set): prints ``start <runId>`` or
      ``resume <runId>``. The run id is bound to the execution and the action (``cp-<execution>-<name>``), so a
      CodePipeline retry of a failed action continues the run it parked (BLOCKED_ON_IT, NEEDS_HUMAN, or a run
      whose machine went away) with ``vra resume`` instead of starting over. A run that ended FAILED or
      TESTS_FAILED is not resumable: the retry starts ``<runId>-r<n>``.
  pipeline_ci.py watch-merges <sync-result.json>
      The lanes ``vra sync`` found something new for, one ``<lane> <ref>`` line each.
  pipeline_ci.py blocked-summary <out-dir> <home-dir> <name>
      Writes <out-dir>/BLOCKED_ON_IT.md: the IT requests and IT packages a parked run handed over (they are in
      this action's artifact), and how to resume.
  pipeline_ci.py rollback-policy --ledger DIR --env E
      Prints the env's rollbackPolicy (auto | ask).

No AWS calls; ``run-plan`` reads the run state with ``vra --json status``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# run statuses a CodePipeline retry continues with `vra resume`
RESUMABLE = ("BLOCKED_ON_IT", "NEEDS_HUMAN", "RUNNING", "SUCCEEDED")
# vra subcommands whose runs are bound to the pipeline execution
BOUND_COMMANDS = ("build", "deploy", "promote", "test", "rollback")
SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def base_run_id(execution_id: str, name: str) -> str:
    return SAFE_RE.sub("-", f"cp-{execution_id}-{name}")[:120]


def vra_cmd() -> list[str]:
    import importlib.util
    import shutil
    if importlib.util.find_spec("vra") is not None:
        return [sys.executable, "-m", "vra"]
    return [shutil.which("vra") or "vra"]


def list_runs() -> list[dict]:
    """The recent runs of the shared state (``vra --json status``); [] when vra cannot answer."""
    try:
        proc = subprocess.run([*vra_cmd(), "--json", "--non-interactive", "status"], capture_output=True,
                              text=True, timeout=300)
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    res = data.get("result") if isinstance(data, dict) else None
    return list((res or {}).get("runs") or [])


def run_plan(name: str, command: str, execution_id: str | None, runs: list[dict] | None = None) -> tuple[str, str]:
    """("start" | "resume" | "plain", run id). "plain": not inside a pipeline execution, or a command whose runs
    are not bound to it (vra picks the run id)."""
    if not execution_id or command not in BOUND_COMMANDS:
        return "plain", ""
    base = base_run_id(execution_id, name)
    mine = [r for r in (runs if runs is not None else list_runs())
            if str(r.get("runId", "")) == base or str(r.get("runId", "")).startswith(base + "-r")]
    if not mine:
        return "start", base
    latest = sorted(mine, key=lambda r: (r.get("startedAt") or "", str(r.get("runId"))))[-1]
    if latest.get("status") in RESUMABLE:
        return "resume", str(latest["runId"])
    attempt = 2 + sum(1 for r in mine if str(r.get("runId", "")).startswith(base + "-r"))
    return "start", f"{base}-r{attempt}"


def watch_merges(sync_result: dict) -> list[tuple[str, str]]:
    """(lane, ref) for every lane `vra sync` found something new for."""
    res = sync_result.get("result", sync_result) if isinstance(sync_result, dict) else {}
    if isinstance(res, dict) and "sync" in res and isinstance(res["sync"], dict):
        res = res["sync"]
    out: list[tuple[str, str]] = []
    if isinstance(res, dict):
        if res.get("newRelease"):
            out.append(("release", str(res["newRelease"])))
        if res.get("newPreviewCommit"):
            out.append(("preview", str(res["newPreviewCommit"])[:12]))
    return out


def blocked_summary(out_dir: Path, home: Path, name: str) -> Path:
    """BLOCKED_ON_IT.md next to the action's other outputs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    rpath = out_dir / f"{name}.result.json"
    if rpath.exists():
        try:
            result = json.loads(rpath.read_text(encoding="utf-8"))
        except ValueError:
            result = {}
    run_id = result.get("runId") or ""
    reqs = sorted(p for p in (home / "state" / "runs").glob(f"{run_id or '*'}/it-requests/*") if p.is_file()) \
        if (home / "state" / "runs").exists() else []
    pkgs = sorted({p.parent for p in (home / "state" / "iam").rglob("IT-ROLES.md")}) \
        if (home / "state" / "iam").exists() else []
    lines = [f"# {name}: BLOCKED_ON_IT", "",
             f"Run `{run_id or 'unknown'}` stopped on a permission gap or a missing IT-provided role. The files "
             "below are in this action's output artifact (`vra-home/state/...`).", "", "## IT requests", ""]
    lines += [f"- `{p.relative_to(home.parent) if home.parent in p.parents else p}`" for p in reqs] or ["- none"]
    lines += ["", "## IT packages (roles to create)", ""]
    lines += [f"- `{p.relative_to(home.parent) if home.parent in p.parents else p}/IT-ROLES.md`" for p in pkgs] \
        or ["- none"]
    lines += ["", "## Resume", "",
              "Once IT has granted the permissions or created the roles (and `vra iam apply` recorded what IT "
              "returned, for IT-provided roles), choose **Retry failed actions** on this stage. The retry "
              f"continues run `{run_id or '<run-id>'}` with `vra resume` (the run id is bound to this pipeline "
              "execution); it does not start over.", ""]
    path = out_dir / "BLOCKED_ON_IT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def rollback_policy(ledger: Path, env: str) -> str:
    try:
        from vra.ledger import Ledger
        return str(Ledger(ledger).env(env).get("rollbackPolicy", "auto"))
    except Exception:  # noqa: BLE001 - unknown policy: never roll back on our own
        return "ask"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline_ci.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run-plan")
    s.add_argument("name")
    s.add_argument("command")
    s = sub.add_parser("watch-merges")
    s.add_argument("file")
    s = sub.add_parser("blocked-summary")
    s.add_argument("out_dir")
    s.add_argument("home")
    s.add_argument("name")
    s = sub.add_parser("rollback-policy")
    s.add_argument("--ledger", required=True)
    s.add_argument("--env", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "run-plan":
        action, run_id = run_plan(a.name, a.command, os.environ.get("VRA_PIPELINE_EXECUTION_ID"))
        print(f"{action} {run_id}".strip())
    elif a.cmd == "watch-merges":
        try:
            data = json.loads(Path(a.file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for lane, ref in watch_merges(data):
            print(f"{lane} {ref}")
    elif a.cmd == "blocked-summary":
        print(blocked_summary(Path(a.out_dir), Path(a.home), a.name))
    elif a.cmd == "rollback-policy":
        print(rollback_policy(Path(a.ledger), a.env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
