#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The release topology of the ledger, as the CI lanes see it.

The topology is the engine's: ``vra --json ledger graph`` (lane, promotesFrom, requiresApproval, tier,
requiredSuites, account, children per env). This script never re-reads ``envs/*/env.yaml``, so no CI file can
disagree with the engine about which env is gated. CI job graphs are generated from it (``render``) or checked
against it (``check``), and deploy jobs ask it whether an env needs an approval record (``requires-approval``)
instead of deciding from "is this a promotion".

  ci_graph.py graph             --ledger DIR | --graph FILE [--lane L]    one lane (default release), JSON
  ci_graph.py requires-approval --ledger DIR | --graph FILE --env E       prints true / false
  ci_graph.py promotes-from     --ledger DIR | --graph FILE --env E       prints the parent env ('' for a root)
  ci_graph.py is-prod-class     --ledger DIR | --graph FILE --env E       prints true / false (tests of a prod-class
                                                                          env never run under its deploy role)
  ci_graph.py render --platform github|gitlab|generic --ledger DIR | --graph FILE [--lane L] [--build-job J]
                                                                          prints the deploy jobs (steps)
  ci_graph.py check  --platform github|gitlab|codepipeline --ledger DIR | --graph FILE --file F [--lane L]
                                                                          exit 1 and lists every difference

--lane (release or preview, default release) picks the lane: `render --platform github --lane preview
--build-job merge-build` prints the preview deploy jobs for upstream-preview.yml (its build job is merge-build).
--build-job (default build) is the job every root deploy job needs and whose `build-id` output it deploys.

--ledger runs ``vra --json ledger graph`` on DIR (with this interpreter's vra when it has one, else ``vra`` on
PATH); --graph reads a file holding that output (the JSON envelope or its ``result``). Run it with the vra
tool's interpreter (``vra-py.sh ci_graph.py ...``); it needs PyYAML for ``check``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

LANE = "release"
DEFAULT_SUITES = ("live-cli", "live-web", "customer")


class GraphError(SystemExit):
    pass


# ------------------------------------------------------------------ the engine's graph
def vra_command() -> list[str]:
    """How to run vra: this interpreter's package when it has one (vra-py.sh), else the vra on PATH."""
    if importlib.util.find_spec("vra") is not None:
        return [sys.executable, "-m", "vra"]
    exe = shutil.which("vra")
    if exe:
        return [exe]
    raise GraphError("ci graph: vra is not installed (run with vra-py.sh, or put vra on PATH)")


def engine_graph(ledger: Path) -> dict:
    """``vra --json ledger graph`` for one ledger directory (legacy mode: no home, nothing written)."""
    env = {k: v for k, v in os.environ.items() if k not in ("VRA_HOME", "VRA_LEDGER")}
    cmd = [*vra_command(), "--json", "--non-interactive", "--ledger", str(ledger), "ledger", "graph"]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        doc = {}
    if proc.returncode != 0 or not isinstance(doc.get("result"), dict):
        msg = doc.get("message") or proc.stderr.strip()[-2000:] or f"exit {proc.returncode}"
        raise GraphError(f"ci graph: `vra ledger graph` failed for {ledger}: {msg}")
    return doc["result"]


def read_graph(path: Path) -> dict:
    doc = json.loads(Path(path).read_text())
    graph = doc.get("result", doc) if isinstance(doc, dict) else None
    if not isinstance(graph, dict) or not isinstance(graph.get("envs"), dict):
        raise GraphError(f"ci graph: {path} is not `vra --json ledger graph` output (no envs)")
    return graph


def full_graph(ledger: Path | None = None, graph_file: Path | None = None) -> dict:
    if graph_file is not None:
        return read_graph(graph_file)
    if ledger is None:
        raise GraphError("ci graph: pass --ledger DIR or --graph FILE")
    return engine_graph(ledger)


def lane_graph(graph: dict, lane: str = LANE) -> dict[str, dict]:
    """Envs of one lane: {name: {promotesFrom, requiresApproval, autoDeploy, tier, isProdClass, requiredSuites,
    account, region, children}}; promotesFrom / children outside the lane are dropped."""
    envs = {n: e for n, e in graph["envs"].items() if (e.get("lane") or LANE) == lane}
    out: dict[str, dict] = {}
    for name, e in envs.items():
        out[name] = {"promotesFrom": e.get("promotesFrom") or None,
                     "requiresApproval": bool(e.get("requiresApproval")),
                     "autoDeploy": bool(e.get("autoDeploy")),
                     "tier": e.get("tier"),
                     "isProdClass": bool(e.get("isProdClass")),
                     "requiredSuites": list(e.get("requiredSuites") or []),
                     "account": str(e.get("account") or ""),
                     "region": str(e.get("region") or ""),
                     "children": sorted(c for c in (e.get("children") or []) if c in envs)}
    return out


def load_graph(ledger: Path | None = None, lane: str = LANE, graph_file: Path | None = None) -> dict[str, dict]:
    return lane_graph(full_graph(ledger, graph_file), lane)


def order(graph: dict[str, dict]) -> list[str]:
    """Parents before children; siblings by name."""
    out: list[str] = []
    level = sorted(n for n, e in graph.items() if not e["promotesFrom"] or e["promotesFrom"] not in graph)
    while level:
        out.extend(level)
        level = sorted(c for n in level for c in graph[n]["children"])
    return out


def env_of(graph: dict, env: str) -> dict:
    """One env of any lane (deploy jobs of the preview lane ask too)."""
    for lane in {(e.get("lane") or LANE) for e in graph["envs"].values()}:
        g = lane_graph(graph, lane)
        if env in g:
            return g[env]
    raise GraphError(f"ci graph: env {env!r} is not in the ledger")


def suites_for(e: dict, configured: str = ",".join(DEFAULT_SUITES)) -> str:
    """Suites a deploy job runs after the deploy: none for a leaf, else the configured list plus every suite the
    engine requires on the env before another env promotes from it."""
    if not e["children"]:
        return ""
    out = [s for s in configured.split(",") if s]
    out += [s for s in e["requiredSuites"] if s not in out]
    return ",".join(out)


def _missing_suites(e: dict, suites: str) -> list[str]:
    have = {s.strip() for s in str(suites or "").split(",") if s.strip()}
    return [s for s in e["requiredSuites"] if s not in have] if e["children"] else []


# ------------------------------------------------------------------ render
def render_github(graph: dict[str, dict], build_job: str = "build") -> str:
    lines = []
    for name in order(graph):
        e = graph[name]
        needs = [build_job] + ([f"deploy-{e['promotesFrom']}"] if e["promotesFrom"] else [])
        lines += [f"    deploy-{name}:",
                  f"        needs: [{', '.join(needs)}]",
                  "        uses: ./.github/workflows/vra-deploy-env.yml",
                  "        with:",
                  f"            env-name: {name}",
                  f"            promote-from: \"{e['promotesFrom'] or ''}\"",
                  f"            requires-approval: {str(e['requiresApproval']).lower()}",
                  f"            build-id: ${{{{ needs.{build_job}.outputs.build-id }}}}",
                  f"            test-suites: \"{suites_for(e)}\"",
                  "        # inherit: no workflow that runs fork code names the home write token; only "
                  "vra-home-publish.yml reads it",
                  "        # nosemgrep: yaml.github-actions.security.secrets-inherit.secrets-inherit",
                  "        secrets: inherit",
                  ""]
    return "\n".join(lines)


def render_gitlab(graph: dict[str, dict], build_job: str = "build") -> str:
    lines = []
    for name in order(graph):
        e = graph[name]
        needs = [build_job] + ([f"deploy-{e['promotesFrom']}"] if e["promotesFrom"] else [])
        lines += [f"deploy-{name}:",
                  f"    extends: [{'.deploy-gated' if e['requiresApproval'] else '.deploy-auto'}]",
                  f"    needs: [{', '.join(needs)}]",
                  f"    environment: {{ name: {name} }}",
                  f"    resource_group: vra-{name}",
                  "    id_tokens:",
                  "        VRA_OIDC_TOKEN:",
                  f"            aud: vra-{name}",
                  "    variables:",
                  f"        VRA_ENV: {name}",
                  f"        VRA_PROMOTE_FROM: \"{e['promotesFrom'] or ''}\"",
                  f"        VRA_TEST_SUITES: \"{suites_for(e)}\"",
                  ""]
    return "\n".join(lines)


def render_generic(graph: dict[str, dict], build_job: str = "build", lane: str = LANE) -> str:
    """The ordered `vra-ci.sh` steps of one lane (Jenkins, Azure Pipelines, any other CI). An approval step of the
    CI precedes every gated env; its approver is passed with --approver."""
    lines = [f"# {lane} lane, parents first; siblings may run in parallel (one job per env at a time)",
             f'vra-ci.sh build --lane {lane} --ref "${{VRA_REF}}"']
    for name in order(graph):
        e = graph[name]
        cmd = f'vra-ci.sh deploy --env {name} --build "${{VRA_BUILD_ID}}"'
        if e["promotesFrom"]:
            cmd += f" --promote-from {e['promotesFrom']}"
        if e["requiresApproval"]:
            lines.append(f"# {name}: approval-gated; the CI's approval step for {name} names the approver")
            cmd += ' --approver "${VRA_APPROVER}" --approved-at "${VRA_APPROVED_AT}" --comment "${VRA_APPROVAL_COMMENT}"'
        lines.append(cmd)
        suites = suites_for(e)
        if suites:
            lines.append(f"vra-ci.sh test --env {name} --suites {suites} --fork-dir fork")
    lines.append("vra-ci.sh finish")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ check
def check_github(graph: dict[str, dict], doc: dict) -> list[str]:
    problems: list[str] = []
    jobs = {}
    for job_id, job in (doc.get("jobs") or {}).items():
        if str(job.get("uses", "")).endswith("vra-deploy-env.yml"):
            w = job.get("with") or {}
            jobs[str(w.get("env-name"))] = (job_id, job, w)
    for name, e in graph.items():
        if name not in jobs:
            problems.append(f"env {name}: no deploy job (uses vra-deploy-env.yml with env-name: {name})")
            continue
        job_id, job, w = jobs[name]
        want_from = e["promotesFrom"] or ""
        if str(w.get("promote-from") or "") != want_from:
            problems.append(f"job {job_id}: promote-from {w.get('promote-from')!r}, ledger promotesFrom {want_from!r}")
        if w.get("requires-approval") is not e["requiresApproval"]:
            problems.append(f"job {job_id}: requires-approval {w.get('requires-approval')!r}, ledger requiresApproval "
                            f"{e['requiresApproval']}")
        missing = _missing_suites(e, w.get("test-suites", ",".join(DEFAULT_SUITES)))
        if missing:
            problems.append(f"job {job_id}: test-suites misses {missing}, which the ledger requires on {name} "
                            f"before it promotes")
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        if want_from:
            parent_job = jobs.get(want_from, (None,))[0]
            if parent_job and parent_job not in needs:
                problems.append(f"job {job_id}: needs {needs} misses {parent_job} (the job of {want_from})")
    for name in jobs:
        if name not in graph:
            problems.append(f"job {jobs[name][0]}: env {name} is not an env of this lane of the ledger")
    return problems


def check_gitlab(graph: dict[str, dict], doc: dict) -> list[str]:
    problems: list[str] = []
    jobs = {}
    for job_id, job in doc.items():
        if not isinstance(job, dict) or job_id.startswith("."):
            continue
        ext = job.get("extends") or []
        ext = [ext] if isinstance(ext, str) else ext
        if not any(str(x).startswith(".deploy") for x in ext):
            continue
        env = str((job.get("variables") or {}).get("VRA_ENV") or "")
        jobs[env] = (job_id, job)
    for name, e in graph.items():
        if name not in jobs:
            problems.append(f"env {name}: no deploy job (extends .deploy-*, variables.VRA_ENV: {name})")
            continue
        job_id, job = jobs[name]
        v = job.get("variables") or {}
        want_from = e["promotesFrom"] or ""
        if str(v.get("VRA_PROMOTE_FROM") or "") != want_from:
            problems.append(f"job {job_id}: VRA_PROMOTE_FROM {v.get('VRA_PROMOTE_FROM')!r}, ledger {want_from!r}")
        ext = job.get("extends") or []
        ext = [ext] if isinstance(ext, str) else ext
        gated = ".deploy-gated" in ext
        if gated != e["requiresApproval"]:
            problems.append(f"job {job_id}: extends {ext}, ledger requiresApproval {e['requiresApproval']} "
                            f"(use {'.deploy-gated' if e['requiresApproval'] else '.deploy-auto'})")
        missing = _missing_suites(e, v.get("VRA_TEST_SUITES", ""))
        if missing:
            problems.append(f"job {job_id}: VRA_TEST_SUITES misses {missing}, which the ledger requires on {name} "
                            f"before it promotes")
        env_name = (job.get("environment") or {}).get("name") if isinstance(job.get("environment"), dict) else None
        if env_name != name:
            problems.append(f"job {job_id}: environment {env_name!r}, expected {name!r}")
        aud = ((job.get("id_tokens") or {}).get("VRA_OIDC_TOKEN") or {}).get("aud")
        if aud != f"vra-{name}":
            problems.append(f"job {job_id}: id_tokens VRA_OIDC_TOKEN aud {aud!r}, expected 'vra-{name}' "
                            f"(one audience per env: only this job's token is accepted by the env's role)")
        if job.get("resource_group") != f"vra-{name}":
            problems.append(f"job {job_id}: resource_group {job.get('resource_group')!r}, expected 'vra-{name}'")
        needs = job.get("needs") or []
        needs = [n if isinstance(n, str) else n.get("job") for n in needs]
        if want_from and want_from in jobs and jobs[want_from][0] not in needs:
            problems.append(f"job {job_id}: needs {needs} misses {jobs[want_from][0]} (the job of {want_from})")
    for name in jobs:
        if name not in graph:
            problems.append(f"job {jobs[name][0]}: env {name} is not an env of this lane of the ledger")
    return problems


def check_codepipeline(graph: dict[str, dict], cfg: dict) -> list[str]:
    """``cfg``: the topology the pipeline stack was synthesized from (VRA_PIPELINE_TOPOLOGY, release.envs)."""
    problems: list[str] = []
    envs = {e["name"]: e for e in (cfg.get("release") or {}).get("envs", [])}
    for name, e in graph.items():
        if name not in envs:
            problems.append(f"env {name}: missing from release.envs")
            continue
        c = envs[name]
        if (c.get("promotesFrom") or None) != e["promotesFrom"]:
            problems.append(f"release.envs {name}: promotesFrom {c.get('promotesFrom')!r}, ledger {e['promotesFrom']!r}")
        if bool(c.get("requiresApproval")) != e["requiresApproval"]:
            problems.append(f"release.envs {name}: requiresApproval {c.get('requiresApproval')}, "
                            f"ledger {e['requiresApproval']}")
        if e["account"] and str(c.get("account")) != e["account"]:
            problems.append(f"release.envs {name}: account {c.get('account')}, ledger {e['account']}")
        if "requiredSuites" in c and sorted(c.get("requiredSuites") or []) != sorted(e["requiredSuites"]):
            problems.append(f"release.envs {name}: requiredSuites {c.get('requiredSuites')}, "
                            f"ledger {e['requiredSuites']}")
    for name in envs:
        if name not in graph:
            problems.append(f"release.envs {name}: not a release-lane env of the ledger")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("graph", "requires-approval", "promotes-from", "is-prod-class", "render", "check"):
        s = sub.add_parser(c)
        src = s.add_mutually_exclusive_group(required=True)
        src.add_argument("--ledger", type=Path, help="ledger dir; runs `vra --json ledger graph` on it")
        src.add_argument("--graph", type=Path, help="file with `vra --json ledger graph` output")
        if c in ("requires-approval", "promotes-from", "is-prod-class"):
            s.add_argument("--env", required=True)
        if c in ("graph", "render", "check"):
            s.add_argument("--lane", default=LANE, choices=["release", "preview"],
                           help="lane of the ledger (default release)")
        if c == "render":
            s.add_argument("--platform", required=True, choices=["github", "gitlab", "generic"])
            s.add_argument("--build-job", default="build",
                           help="job the root deploy jobs need and take build-id from (default build)")
        if c == "check":
            s.add_argument("--platform", required=True, choices=["github", "gitlab", "codepipeline"])
            s.add_argument("--file", type=Path, required=True)
    ns = ap.parse_args(argv)
    full = full_graph(ns.ledger, ns.graph)
    if ns.cmd == "graph":
        print(json.dumps(lane_graph(full, ns.lane), indent=2))
        return 0
    if ns.cmd == "requires-approval":
        print(str(env_of(full, ns.env)["requiresApproval"]).lower())
        return 0
    if ns.cmd == "is-prod-class":
        print(str(bool(env_of(full, ns.env).get("isProdClass"))).lower())
        return 0
    if ns.cmd == "promotes-from":
        print(env_of(full, ns.env)["promotesFrom"] or "")
        return 0
    graph = lane_graph(full, ns.lane)
    if not graph and ns.lane != LANE:
        raise GraphError(f"ci graph: the ledger has no env in the {ns.lane} lane")
    if ns.cmd == "render":
        if ns.platform == "generic":
            print(render_generic(graph, ns.build_job, ns.lane))
        else:
            print({"github": render_github, "gitlab": render_gitlab}[ns.platform](graph, ns.build_job))
        return 0
    text = ns.file.read_text()
    if ns.platform == "codepipeline":
        doc = json.loads(text)
    else:
        import yaml
        doc = yaml.safe_load(text)
    problems = {"github": check_github, "gitlab": check_gitlab, "codepipeline": check_codepipeline}[ns.platform](
        graph, doc)
    for p in problems:
        print(f"ci graph: {p}", file=sys.stderr)
    if problems:
        print(f"ci graph: {ns.file} does not match the ledger ({len(problems)} difference(s)); "
              f"regenerate with `ci_graph.py render --platform {ns.platform}`"
              + (" / `vra --json ledger graph` and redeploy the pipeline" if ns.platform == "codepipeline" else ""),
              file=sys.stderr)
        return 1
    print(f"ci graph: {ns.file} matches the ledger ({len(graph)} {ns.lane} envs)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
