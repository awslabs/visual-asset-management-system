# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Automation tiers (vra.yaml ``automation.mode``) and the CodePipeline lane (`vra pipeline ...`).

The ladder, one setting per project; every tier runs the same engine, gates and ledger:

- ``agent`` (default): a local agent or operator runs `vra` (cron through the agent CLI). No CI/CD.
- ``hybrid``: the pipeline app runs the scheduled watch, the build and the envs reachable without an approval
  (scope ``dev``); people or agents run the gated envs with `vra promote`.
- ``pipeline``: CodePipeline end to end: source, scheduled sync, build, dev deploy + test (auto-rollback),
  manual approvals bound to the execution, test, prod.
- ``pipeline+actions``: as ``pipeline``; GitHub Actions (``automation.actions``) runs PR checks, security scans and
  fork-branch tests and hands deploys to CodePipeline.

`vra pipeline` renders the ``cicd/pipeline`` CDK app's config from ``automation.pipeline`` plus `vra ledger graph`
(the pipeline never re-derives a gate), and:

- ``plan``: renders, synthesizes (offline; cdk-nag applies), diffs against the deployed stack (tools account,
  read-only), and writes the IT package for the pipeline's own roles (``state/iam/pipeline/<n>/``). The plan record
  (``state/pipeline/plans/<id>.json``) pins the template digest the deploy must reproduce.
- ``deploy``: re-synthesizes, refuses a template that differs from the plan, then ``cdk deploy`` into the tools
  account. Human-only (``pipeline-deploy``) when the stack does not exist yet, when the plan changes IAM or the
  source bucket policy, or when it removes a manual approval; otherwise an agent may run it. A role IT has not created (``iamRoles.mode
  precreated``) stops with exit 10 and the IT package.
- ``status``: the stack, its pipelines' stage states and pending approvals, and whether the deployed topology
  (stack output ``TopologyDigest``) matches the ledger.
- ``sync-ledger``: when the ledger's topology moved away from the deployed pipeline, plans again (and with
  ``--deploy`` deploys, under the same gates); exit 20 when a deploy is still needed.
- ``push-source``: the S3 source fed by vra: uploads the fork branch (or, with ``--package``, this package) as a zip
  when its commit changed and starts the pipeline.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from vra import errors, util
from vra.errors import BlockedOnIT, ConfigError, NeedsHuman, UsageError

MODES = ("agent", "hybrid", "pipeline", "pipeline+actions")
PIPELINE_MODES = ("hybrid", "pipeline", "pipeline+actions")
HUMAN_ACTION = "pipeline-deploy"
HUMAN_WHY = ("`vra pipeline deploy` creates the pipeline, changes its IAM roles or removes a manual approval in the "
             "tools account")
ACCOUNT_RE = re.compile(r"^[0-9]{12}$")
ARN_RE = re.compile(r"^arn:aws[a-z-]*:")
APP_EXCLUDE = ("node_modules", "cdk.out", "__pycache__", ".git")


# ====================================================================== the ladder
def automation(project: dict) -> dict:
    a = project.get("automation") or {}
    return a if isinstance(a, dict) else {}


def mode(project: dict) -> str:
    return str(automation(project).get("mode") or "agent")


def validate(ledger) -> tuple[list[str], list[str]]:
    """Errors and warnings of vra.yaml ``automation`` against the ledger (called by `vra ledger validate`)."""
    errs: list[str] = []
    warns: list[str] = []
    try:
        proj = ledger.raw_project()
    except ConfigError:
        return errs, warns
    a = proj.get("automation")
    if a is None:
        return errs, warns
    if not isinstance(a, dict):
        return ["vra.yaml: automation must be a mapping"], warns
    m = a.get("mode", "agent")
    w = "vra.yaml: automation"
    if m not in MODES:
        return [f"{w}.mode {m!r} is not one of {', '.join(MODES)}"], warns
    pl = a.get("pipeline")
    if m == "agent":
        if pl:
            warns.append(f"{w}.pipeline is ignored with mode agent (no pipeline is rendered)")
        return errs, warns
    # automation.actions is optional: the GitHub Actions side is configured in its workflow files and repository
    # variables (cicd/github-actions), and vra reads nothing from it
    if m == "pipeline+actions" and isinstance(pl, dict) and not pl.get("actionsHandoff"):
        warns.append(f"{w}.pipeline.actionsHandoff is not set: the GitHub Actions hand-off has no role to start the "
                     "release pipeline with (and the fork source does not trigger on push in this mode)")
    if m in ("pipeline", "pipeline+actions") and not isinstance(pl, dict):
        errs.append(f"{w}.pipeline is required for mode {m} (the cicd/pipeline config, without the topology)")
        return errs, warns
    if m == "hybrid" and not isinstance(pl, dict):
        if not isinstance(a.get("actions"), dict):
            warns.append(f"{w}: mode hybrid without automation.pipeline or automation.actions: nothing runs in CI")
        return errs, warns
    p = f"{w}.pipeline"
    tools = str(pl.get("toolsAccount") or proj.get("toolsAccount") or "")
    if not ACCOUNT_RE.match(tools):
        errs.append(f"{p}.toolsAccount (or vra.yaml toolsAccount) must be a 12-digit account id")
    if not pl.get("region"):
        errs.append(f"{p}.region is required (the tools account region)")
    src = pl.get("sources") or {}
    for k in ("fork", "vra"):
        if not isinstance(src.get(k), dict):
            errs.append(f"{p}.sources.{k} is required")
    if not (pl.get("home") or {}).get("repoUrl"):
        errs.append(f"{p}.home.repoUrl is required (git URL of the vra home repository every job clones)")
    if not (pl.get("notifications") or {}).get("topicArn"):
        errs.append(f"{p}.notifications.topicArn is required")
    for k in ("ledgerGraph", "ledgerDir", "automationMode"):
        if k in pl:
            errs.append(f"{p}.{k} is set by vra (from `vra ledger graph` and automation.mode); remove it")
    if m == "hybrid" and pl.get("scope") not in (None, "dev"):
        errs.append(f"{p}.scope must be dev with mode hybrid (gated envs are run with `vra promote`)")
    if (pl.get("watch") or {}).get("command") == "watch":
        errs.append(f"{p}.watch.command watch deploys dev outside the pipeline, which also deploys it; use "
                    "sync-merge (default) or sync")
    iam_mode = (pl.get("iamRoles") or {}).get("mode", "create")
    if iam_mode not in ("create", "precreated"):
        errs.append(f"{p}.iamRoles.mode must be create or precreated (vra pipeline plan writes the report itself)")
    ss = proj.get("stateStore") or {}
    pss = pl.get("stateStore")
    if ss.get("type") == "s3":
        if not isinstance(pss, dict) or not pss.get("keyArn"):
            errs.append(f"{p}.stateStore.keyArn is required with vra.yaml stateStore.type s3 (vra-state-store.yaml "
                        "StateKeyArn output); bucket, prefix and lock table come from vra.yaml")
    elif pss:
        errs.append(f"{p}.stateStore is set but vra.yaml stateStore.type is not s3")
    ae = a.get("approvalEnv")
    names = ledger.env_names()
    if ae is not None and ae not in names:
        errs.append(f"{w}.approvalEnv {ae!r} is not a known env")
    for n in names:
        try:
            e = ledger.env(n)
        except ConfigError:
            continue
        if e.get("lane", "release") != "release":
            continue
        # a CodeBuild tier role may only assume the env's deploy role; without deployRoleArn the engine
        # in CI would run as the tier role itself, every preflight would be denied, and its IT request would ask to
        # widen the tier role (defeating the tier split). Hybrid pipelines run only the ungated root envs
        in_pipeline = m in ("pipeline", "pipeline+actions") or (
            not e.get("requiresApproval") and not e.get("promotesFrom"))
        if in_pipeline and not e.get("deployRoleArn"):
            errs.append(f"envs/{n}: deployRoleArn is required with automation.mode {m}: the pipeline's CodeBuild "
                        f"role for {n} can only assume that role (IT deploys iam/cloudformation/vra-deploy-role.yaml "
                        f"as vra-deploy-{n} with TrustedPrincipalArns = the pipeline tier role); a profile or "
                        "ambient credentials do not exist in CodeBuild")
        if e.get("autoDeploy") and m in ("pipeline", "pipeline+actions"):
            warns.append(f"envs/{n}: autoDeploy is true but the pipeline deploys {n}; a `vra watch` cron would "
                         "deploy it too (set autoDeploy: false, or do not schedule `vra watch`)")
        if e.get("requiresApproval") and not any(ARN_RE.match(str(x)) for x in e.get("approvers") or []):
            if m != "hybrid":
                warns.append(f"envs/{n}: no approver is an IAM ARN or ARN glob; CodePipeline records approvers as "
                             "assumed-role ARNs, so its approvals for this env would be refused")
    return errs, warns


# ====================================================================== topology
def graph(ledger) -> dict:
    from vra.cli import ledger_graph
    return ledger_graph(ledger)


def scope_envs(g: dict, scope: str | None) -> dict[str, dict]:
    """Release-lane envs a pipeline scope covers (cicd/pipeline lib/config.ts scopeEnvs)."""
    rel = {n: e for n, e in (g.get("envs") or {}).items() if (e.get("lane") or "release") == "release"}
    if (scope or "all") == "all":
        return rel

    def ungated(n: str, seen: frozenset) -> bool:
        e = rel.get(n)
        if not e or e.get("requiresApproval") or n in seen:
            return False
        return True if not e.get("promotesFrom") else ungated(e["promotesFrom"], seen | {n})
    return {n: e for n, e in rel.items() if ungated(n, frozenset())}


def topology_digest(g: dict, scope: str | None = None) -> str:
    """Same digest as cicd/pipeline lib/config.ts topologyDigest (stack output TopologyDigest)."""
    rows = [{"name": n, "account": str(e.get("account") or ""), "promotesFrom": e.get("promotesFrom") or None,
             "requiresApproval": bool(e.get("requiresApproval")), "requiredSuites": list(e.get("requiredSuites") or [])}
            for n, e in sorted(scope_envs(g, scope).items())]
    return hashlib.sha256(json.dumps({"envs": rows}, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


# ====================================================================== render
def render(ctx, out_dir: Path) -> dict:
    """Write ``pipeline.json`` + ``ledger-graph.json`` for the cicd/pipeline app into ``out_dir``."""
    L = ctx.ledger
    errs, _w = validate(L)
    if errs:
        raise ConfigError("vra.yaml automation is invalid:\n  - " + "\n  - ".join(errs))
    proj = L.project
    m = mode(proj)
    if m not in PIPELINE_MODES or not isinstance(automation(proj).get("pipeline"), dict):
        raise UsageError(f"automation.mode is {m}: no pipeline to render (set automation.mode pipeline, hybrid or "
                         "pipeline+actions with automation.pipeline)")
    cfg = copy.deepcopy(automation(proj)["pipeline"])
    cfg.setdefault("toolsAccount", str(proj.get("toolsAccount") or ""))
    cfg["automationMode"] = m
    if m == "hybrid":
        cfg["scope"] = "dev"
    if m == "pipeline+actions":
        # the GitHub Actions hand-off starts executions at a checked commit; a push must not start one too
        fork = (cfg.get("sources") or {}).get("fork") or {}
        if fork.get("type") != "s3":
            fork.setdefault("triggerOnPush", False)
    cfg["ledgerGraph"] = "ledger-graph.json"
    cfg["ledgerDir"] = str(L.root)
    ss = proj.get("stateStore") or {}
    if ss.get("type") == "s3":
        cfg["stateStore"] = {"bucketName": ss.get("bucket"), "prefix": (ss.get("prefix") or "vra").strip("/"),
                             "lockTableName": ss.get("lockTable"), "keyArn": (cfg.get("stateStore") or {})["keyArn"]}
    g = graph(L)
    out_dir.mkdir(parents=True, exist_ok=True)
    util.write_json(out_dir / "ledger-graph.json", {"result": g})
    util.write_json(out_dir / "pipeline.json", cfg)
    # ledgerDir is this machine's checkout path: not part of what was planned
    text = json.dumps({k: v for k, v in cfg.items() if k != "ledgerDir"}, sort_keys=True) + json.dumps(g, sort_keys=True)
    return {"dir": str(out_dir), "config": cfg, "graph": g, "mode": m,
            "stackName": f"{cfg.get('prefix') or 'vra'}-pipeline",
            "digest": topology_digest(g, cfg.get("scope")), "renderSha256": util.sha256_bytes(text.encode())}


# ====================================================================== the CDK app
def package_root() -> Path:
    """The vams-release-automation package this engine runs from (engine/vra -> package root)."""
    return Path(__file__).resolve().parents[2]


def app_source() -> Path:
    """The cicd/ directory holding pipeline/ and codebuild/ (VRA_PIPELINE_APP_DIR overrides: path of cicd/)."""
    d = Path(os.environ.get("VRA_PIPELINE_APP_DIR") or package_root() / "cicd").expanduser().resolve()
    if not (d / "pipeline" / "cdk.json").exists() or not (d / "codebuild").is_dir():
        raise UsageError(f"cicd/pipeline app not found under {d}: run vra from the package checkout, or set "
                         "VRA_PIPELINE_APP_DIR to the package's cicd/ directory")
    return d


def app_workspace(ctx) -> Path:
    """A machine-local copy of cicd/ (pipeline/ next to codebuild/) with its npm dependencies installed."""
    from vra import __version__
    src = app_source()
    dst = ctx.work_dir / "pipeline-app" / __version__ / "cicd"
    for sub in ("pipeline", "codebuild"):
        shutil.copytree(src / sub, dst / sub, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*APP_EXCLUDE))
    app = dst / "pipeline"
    if not (app / "node_modules").exists():
        ctx.shell(ctx.work_dir / "pipeline-app" / "npm.log").run(
            ["npm", "ci", "--no-audit", "--no-fund"], cwd=app, mutating=False, timeout=1800)
    return app


def synth(ctx, app: Path, config_file: Path, out: Path, iam_report: bool = False) -> dict:
    """``cdk synth`` offline into ``out``; returns the stack template (cdk-nag findings fail the synth)."""
    argv = ["npx", "cdk", "synth", "--quiet", "--no-notices", "-c", f"vraPipelineConfig={config_file}", "-o", str(out)]
    if iam_report:
        argv += ["-c", "vraIamReport=true"]
    env = {"AWS_EC2_METADATA_DISABLED": "true", "CDK_DISABLE_VERSION_CHECK": "1"}
    try:
        ctx.shell(out.parent / "synth.log").run(argv, cwd=app, env=env, mutating=False, timeout=1800)
    except errors.CommandFailed as exc:
        missing = sorted(set(re.findall(r"([A-Za-z0-9-]+(?:/[A-Za-z0-9_.-]+)+)\s*\(missing", exc.output or "")))
        raise PipelineSynthFailed(f"cdk synth of the pipeline failed: {util.redact((exc.output or '')[-1500:])}",
                                  {"missingRoles": missing}) from exc
    return read_template(out)


class PipelineSynthFailed(errors.VraError):
    exit_code = errors.OTHER_ERROR


def read_template(out: Path) -> dict:
    manifest = util.read_json(out / "manifest.json") if (out / "manifest.json").exists() else {}
    for name, art in (manifest.get("artifacts") or {}).items():
        if art.get("type") == "aws:cloudformation:stack":
            f = out / (art.get("properties") or {}).get("templateFile", f"{name}.template.json")
            if f.exists():
                return util.read_json(f)
    files = sorted(out.glob("*.template.json"))
    return util.read_json(files[0]) if files else {}


def template_digest(t: dict) -> str:
    return util.sha256_bytes(json.dumps(t, sort_keys=True, separators=(",", ":")).encode())


# resource policies that decide who may act on the pipeline: the source bucket policy decides who may write the
# release source (sourceWriterArns), so widening it is as human-only as an IAM change
ACCESS_POLICY_TYPES = ("AWS::S3::BucketPolicy",)


def iam_resources(t: dict) -> dict[str, Any]:
    """IAM resources and access-policy resources (ACCESS_POLICY_TYPES): a change to any is human-only."""
    return {k: v for k, v in (t.get("Resources") or {}).items()
            if str(v.get("Type", "")).startswith("AWS::IAM::") or v.get("Type") in ACCESS_POLICY_TYPES}


def approval_actions(t: dict) -> set[str]:
    """`<pipeline name>/<stage>/<action>` of every manual approval in a template."""
    out = set()
    for res in (t.get("Resources") or {}).values():
        if res.get("Type") != "AWS::CodePipeline::Pipeline":
            continue
        pname = (res.get("Properties") or {}).get("Name")
        pname = pname if isinstance(pname, str) else json.dumps(pname, sort_keys=True)
        for st in (res.get("Properties") or {}).get("Stages") or []:
            for a in st.get("Actions") or []:
                if (a.get("ActionTypeId") or {}).get("Category") == "Approval":
                    out.add(f"{pname}/{st.get('Name')}/{a.get('Name')}")
    return out


# ====================================================================== AWS reads (tools account)
def _cfn(ctx, region: str):
    return ctx.aws.tools_client("cloudformation", region)


def deployed_stack(ctx, stack: str, region: str) -> dict | None:
    """The deployed pipeline stack (DescribeStacks) or None when it does not exist."""
    try:
        res = _cfn(ctx, region).describe_stacks(StackName=stack)
    except Exception as exc:  # noqa: BLE001
        from vra.aws import client_error_parts
        code, msg, _ = client_error_parts(exc)
        if code == "ValidationError" and "does not exist" in msg:
            return None
        raise
    stacks = res.get("Stacks") or []
    if not stacks or stacks[0].get("StackStatus") == "DELETE_COMPLETE":
        return None
    return stacks[0]


def deployed_template(ctx, stack: str, region: str) -> dict:
    body = _cfn(ctx, region).get_template(StackName=stack, TemplateStage="Original").get("TemplateBody")
    if isinstance(body, str):
        try:
            return json.loads(body)
        except ValueError:  # a YAML template was not deployed by this app
            return {}
    return body or {}


def outputs(stack: dict | None) -> dict[str, str]:
    return {o["OutputKey"]: o.get("OutputValue") for o in (stack or {}).get("Outputs") or []}


# ====================================================================== plan / deploy
def _plans_dir(ctx) -> Path:
    return ctx.state_dir / "pipeline" / "plans"


def _purposes(cfg: dict) -> dict[str, str]:
    p = cfg.get("prefix") or "vra"
    return {f"{p}-pipeline/BuildRole": "build tier: unit suites, synth, bundle (assumes only vra-synth-<env>)",
            f"{p}-pipeline/DevRole": "dev tier: validate, ungated root envs",
            f"{p}-pipeline/WatchRole": "watch tier: scheduled sync / merge, feeds the S3 sources (no env role)",
            f"{p}-pipeline/PreviewRole": "preview tier: dev-preview deploy and test (no release source access)",
            **{f"{p}-pipeline/DeployRole-{n}": f"deploy tier of {n} (assumes only vra-deploy-{n})"
               for n in ("dev", "test", "prod")}}


def plan(ctx, offline: bool = False) -> dict:
    """Render, synthesize, diff and write the IT package; returns the plan record."""
    from vra import pipeline_iam
    stamp = util.utcnow().strftime("%Y%m%dT%H%M%SZ")
    work = ctx.work_dir / "pipeline-plans" / stamp
    r = render(ctx, work / "render")
    cfg = r["config"]
    app = app_workspace(ctx)
    cfg_file = Path(r["dir"]) / "pipeline.json"
    # IT package first: it is useful even when the deployable synth fails on a role IT has not created yet
    report_t = synth(ctx, app, cfg_file, work / "cdk.out.iam-report", iam_report=True)
    report_file = work / "cdk.out.iam-report" / "iam-policy-report.json"
    report = util.read_json(report_file) if report_file.exists() else {"roles": []}
    iam_cfg = cfg.get("iamRoles") or {}
    if getattr(ctx, "store", None) is not None:
        ctx.store.pull_prefix("iam/pipeline/")      # number the package after the ones other machines wrote
    roles, unresolved = pipeline_iam.resolve_roles(report, report_t, iam_cfg.get("roleNamePrefix") or "")
    pkg = pipeline_iam.write_package(
        roles, ctx.state_dir / "iam" / "pipeline", scope="pipeline",
        title=f"IAM roles of the vra pipeline stack {r['stackName']} (tools account {cfg.get('toolsAccount')})",
        boundary_arn=cfg.get("permissionsBoundaryArn"), name_prefix=iam_cfg.get("roleNamePrefix"),
        purposes=_purposes(cfg), phases={x.get("roleConstructPath", ""): "run time" for x in roles},
        unresolved=unresolved)
    rec: dict[str, Any] = {
        "planId": f"pl-{stamp}", "createdAt": util.now_iso(), "by": ctx.initiator, "mode": r["mode"],
        "stackName": r["stackName"], "toolsAccount": cfg.get("toolsAccount"), "region": cfg.get("region"),
        "renderSha256": r["renderSha256"], "topologyDigest": r["digest"], "itPackage": pkg, "offline": offline,
        "workDir": str(work), "iamMode": iam_cfg.get("mode", "create")}
    try:
        tmpl = synth(ctx, app, cfg_file, work / "cdk.out")
    except PipelineSynthFailed as exc:
        if rec["iamMode"] == "precreated":
            rec["blocked"] = {"reason": "roles IT has not created", "missingRoles": exc.detail.get("missingRoles")}
            _save_plan(ctx, rec)
            raise BlockedOnIT(f"automation.pipeline.iamRoles.precreatedRoles misses roles of the pipeline stack: hand "
                              f"IT the package {pkg['dir']}/IT-ROLES.md, record the names IT returns in "
                              f"precreatedRoles, then run `vra pipeline plan` again", rec) from exc
        raise
    rec["templateSha256"] = template_digest(tmpl)
    rec["approvals"] = sorted(approval_actions(tmpl))
    if offline:
        rec.update({"firstDeploy": None, "iamChanged": None, "gatesRemoved": None,
                    "note": "offline: no comparison with the deployed stack; `vra pipeline deploy` plans online"})
    else:
        stack = deployed_stack(ctx, r["stackName"], cfg["region"])
        rec["firstDeploy"] = stack is None
        if stack is None:
            rec.update({"iamChanged": sorted(iam_resources(tmpl)), "gatesRemoved": [], "gatesAdded": rec["approvals"]})
        else:
            old = deployed_template(ctx, r["stackName"], cfg["region"])
            oi, ni = iam_resources(old), iam_resources(tmpl)
            rec["iamChanged"] = sorted(k for k in set(oi) | set(ni) if oi.get(k) != ni.get(k))
            oa = approval_actions(old)
            rec["gatesRemoved"] = sorted(oa - set(rec["approvals"]))
            rec["gatesAdded"] = sorted(set(rec["approvals"]) - oa)
            rec["deployedTopologyDigest"] = outputs(stack).get("TopologyDigest")
        rec["diff"] = _cdk_diff(ctx, app, work, r["stackName"])
    rec["humanOnly"] = human_only_reasons(rec)
    _save_plan(ctx, rec)
    return rec


def human_only_reasons(rec: dict) -> list[str]:
    out = []
    if rec.get("firstDeploy") is not False:
        out.append("first deploy of the pipeline stack" if rec.get("firstDeploy") else
                   "plan made offline (whether the stack exists is unknown)")
    if rec.get("iamChanged"):
        out.append(f"IAM / access-policy changes: {', '.join(rec['iamChanged'][:10])}")
    if rec.get("gatesRemoved"):
        out.append(f"removes manual approvals: {', '.join(rec['gatesRemoved'])}")
    return out


def _cdk_diff(ctx, app: Path, work: Path, stack: str) -> dict:
    """`cdk diff` of the planned assembly against the deployed stack (read-only; tools-account credentials)."""
    p = ctx.shell(work / "diff.log").run(["npx", "cdk", "diff", stack, "--app", str(work / "cdk.out"), "--no-notices"],
                                         cwd=app, check=False, mutating=False, timeout=1800)
    text = (p.stdout or "") + (p.stderr or "")
    (work / "diff.txt").write_text(util.redact(text), encoding="utf-8")
    return {"file": str(work / "diff.txt"), "exitCode": p.returncode, "summary": util.redact(text)[-2000:]}


def _share(ctx, paths: list[Path], message: str, create_only: bool = True) -> None:
    """Share pipeline state: the S3 state store when configured (the home is read-only then), else the home."""
    if ctx.dry_run:
        return
    store = getattr(ctx, "store", None)
    if store is not None:
        for p in paths:
            if p.is_dir():
                clashes = store.push_dir(p, create_only=create_only)
                if clashes:
                    raise NeedsHuman(f"{p} already exists in the state store with other content ({clashes[:5]})")
            elif not store.push_file(p, create_only=create_only):
                raise NeedsHuman(f"{p.name} already exists in the state store with other content")
        return
    ctx.home.publish(message)


def _save_plan(ctx, rec: dict) -> None:
    if ctx.dry_run:
        return
    p = _plans_dir(ctx) / f"{rec['planId']}.json"
    util.write_json(p, rec)
    pkg_dir = Path((rec.get("itPackage") or {}).get("dir") or "")
    _share(ctx, [p] + ([pkg_dir] if (rec.get("itPackage") or {}).get("dir") and pkg_dir.is_dir() else []),
           f"vra pipeline plan {rec['planId']}")


def load_plan(ctx, plan_id: str | None) -> dict:
    ctx.home.pull()
    if getattr(ctx, "store", None) is not None:
        ctx.store.pull_prefix("pipeline/plans/")
    d = _plans_dir(ctx)
    if plan_id:
        f = d / f"{plan_id}.json"
        if not f.exists():
            raise UsageError(f"no pipeline plan {plan_id} (run `vra pipeline plan`)")
        return util.read_json(f)
    files = sorted(d.glob("pl-*.json")) if d.exists() else []
    if not files:
        raise UsageError("no pipeline plan yet: run `vra pipeline plan` first")
    return util.read_json(files[-1])


def approval_env(ctx) -> str | None:
    """The env whose approvers authorize a human-only pipeline deploy without a terminal (automation.approvalEnv,
    else the first prod-class gated env, else the first gated env)."""
    a = automation(ctx.ledger.project).get("approvalEnv")
    if a:
        return a
    g = graph(ctx.ledger)
    gated = sorted(n for n, e in g["envs"].items() if e.get("requiresApproval"))
    prod = [n for n in gated if g["envs"][n].get("isProdClass")]
    return (prod or gated or [None])[0]


def deploy(ctx, plan_id: str | None = None, approval: str | None = None) -> dict:
    """Deploy the planned pipeline stack. The template must equal the plan's; gated as described in the module."""
    from vra import human
    rec = load_plan(ctx, plan_id)
    if rec.get("blocked"):
        raise BlockedOnIT(f"plan {rec['planId']} is blocked on IT ({rec['blocked']['reason']}); see "
                          f"{rec['itPackage']['dir']}/IT-ROLES.md, then plan again", rec)
    if rec.get("offline"):
        raise UsageError(f"plan {rec['planId']} was made with --offline; deploy needs an online plan (`vra pipeline "
                         "plan` with tools-account read access)")
    r = render(ctx, Path(tempfile.mkdtemp(prefix="vra-pl-", dir=_tmp_root(ctx))) / "render")
    if r["renderSha256"] != rec.get("renderSha256"):
        raise NeedsHuman(f"the ledger or automation.pipeline changed since plan {rec['planId']}: run `vra pipeline "
                         "plan` again and review it", {"planId": rec["planId"]})
    app = app_workspace(ctx)
    out = Path(r["dir"]).parent / "cdk.out"
    tmpl = synth(ctx, app, Path(r["dir"]) / "pipeline.json", out)
    if template_digest(tmpl) != rec.get("templateSha256"):
        raise NeedsHuman(f"the synthesized template differs from plan {rec['planId']} (a different vra package or "
                         "app dependencies?): run `vra pipeline plan` again", {"planId": rec["planId"]})
    exists = deployed_stack(ctx, rec["stackName"], rec["region"]) is not None
    if exists == bool(rec.get("firstDeploy")):
        raise NeedsHuman(f"the stack {'now exists' if exists else 'no longer exists'} since plan {rec['planId']}: "
                         "plan again", {"planId": rec["planId"]})
    # what decides human-only is recomputed against the stack as it is now, never taken from the stored plan
    # (another deploy may have changed the stack; the plan file is a writable record)
    live = live_changes(ctx, rec["stackName"], rec["region"], tmpl, exists)
    if (sorted(rec.get("iamChanged") or []), sorted(rec.get("gatesRemoved") or [])) != \
            (live["iamChanged"], live["gatesRemoved"]):
        raise NeedsHuman(f"the deployed pipeline stack changed since plan {rec['planId']} (IAM changes now "
                         f"{live['iamChanged'][:5]}, removed approvals now {live['gatesRemoved']}): run `vra pipeline "
                         f"plan` again and review it", {"planId": rec["planId"], "live": live})
    reasons = human_only_reasons({**rec, **live, "firstDeploy": not exists})
    auth = None
    if reasons:
        if human.is_agent():
            raise NeedsHuman(f"{HUMAN_WHY} ({'; '.join(reasons)}); it is human-only and an agent cannot run it. Ask "
                             "the human to run `vra pipeline deploy`", {"humanOnly": HUMAN_ACTION, "reasons": reasons})
        auth = human.require(ctx, HUMAN_ACTION, approval_env(ctx), approval or getattr(ctx, "human_approval", None))
    argv = ["npx", "cdk", "deploy", rec["stackName"], "--app", str(out), "--require-approval", "never",
            "--no-notices"]
    ctx.shell(Path(r["dir"]).parent / "deploy.log").run(argv, cwd=app, timeout=7200)
    res = {"planId": rec["planId"], "stackName": rec["stackName"], "toolsAccount": rec["toolsAccount"],
           "region": rec["region"], "templateSha256": rec["templateSha256"], "topologyDigest": rec["topologyDigest"],
           "deployedAt": util.now_iso(), "by": ctx.initiator, "humanOnly": reasons, "authorization": auth,
           "dryRun": ctx.dry_run}
    if not ctx.dry_run:
        p = ctx.state_dir / "pipeline" / "deploys" / f"{util.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
        util.write_json(p, res)
        _share(ctx, [p], f"vra pipeline deploy {rec['planId']}")
    return res


def live_changes(ctx, stack_name: str, region: str | None, tmpl: dict, exists: bool) -> dict:
    """IAM resources and manual approvals the template changes against the deployed stack, read now."""
    if not exists:
        return {"iamChanged": sorted(iam_resources(tmpl)), "gatesRemoved": []}
    old = deployed_template(ctx, stack_name, region)
    oi, ni = iam_resources(old), iam_resources(tmpl)
    return {"iamChanged": sorted(k for k in set(oi) | set(ni) if oi.get(k) != ni.get(k)),
            "gatesRemoved": sorted(approval_actions(old) - approval_actions(tmpl))}


def _tmp_root(ctx) -> str:
    d = ctx.work_dir / "pipeline-plans"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


# ====================================================================== status / sync-ledger
def status(ctx) -> dict:
    """Stack, stage states and pending approvals of the pipelines, and whether the topology is in step."""
    proj = ctx.ledger.project
    pl = automation(proj).get("pipeline") or {}
    m = mode(proj)
    if m not in PIPELINE_MODES or not pl:
        return {"mode": m, "pipeline": None, "message": "no pipeline in this automation mode"}
    region = pl.get("region")
    stack_name = f"{pl.get('prefix') or 'vra'}-pipeline"
    scope = "dev" if m == "hybrid" else pl.get("scope")
    want = topology_digest(graph(ctx.ledger), scope)
    stack = deployed_stack(ctx, stack_name, region)
    if stack is None:
        return {"mode": m, "stackName": stack_name, "deployed": False, "inSync": False, "ledgerTopologyDigest": want}
    outs = outputs(stack)
    have = outs.get("TopologyDigest")
    cp = ctx.aws.tools_client("codepipeline", region)
    pipes = {}
    for key in ("ReleasePipelineName", "PreviewPipelineName"):
        name = outs.get(key)
        if not name:
            continue
        st = cp.get_pipeline_state(name=name)
        stages, pending = [], []
        for s in st.get("stageStates") or []:
            le = s.get("latestExecution") or {}
            stages.append({"stage": s.get("stageName"), "status": le.get("status")})
            for a in s.get("actionStates") or []:
                ale = a.get("latestExecution") or {}
                if ale.get("status") == "InProgress" and a.get("actionName", "").rsplit("-", 1)[0] in (
                        "Approval", "FinalApproval"):
                    pending.append({"stage": s.get("stageName"), "action": a.get("actionName"), "token":
                                    bool(ale.get("token"))})
        ex = cp.list_pipeline_executions(pipelineName=name, maxResults=5).get("pipelineExecutionSummaries") or []
        pipes[name] = {"stages": stages, "pendingApprovals": pending,
                       "executions": [{k: e.get(k) for k in ("pipelineExecutionId", "status", "startTime")}
                                      for e in ex]}
    return {"mode": m, "stackName": stack_name, "deployed": True, "stackStatus": stack.get("StackStatus"),
            "inSync": have == want, "ledgerTopologyDigest": want, "deployedTopologyDigest": have,
            "connectionArns": {k: v for k, v in outs.items() if k.startswith("ConnectionArn")},
            "sourceBucket": outs.get("SourceBucketName"), "pipelines": pipes}


def sync_ledger(ctx, do_deploy: bool = False, approval: str | None = None) -> tuple[int, dict, str]:
    st = status(ctx)
    if st.get("pipeline", "x") is None:
        return errors.OK, st, st["message"]
    if st.get("inSync"):
        return errors.OK, {"status": st}, "pipeline topology matches the ledger"
    rec = plan(ctx)
    if do_deploy:
        res = deploy(ctx, rec["planId"], approval)
        return errors.OK, {"plan": rec, "deploy": res}, "pipeline redeployed to the ledger's topology"
    who = "a human" if rec["humanOnly"] else "you (or an agent)"
    return errors.NEEDS_HUMAN, {"plan": rec, "status": st}, (
        f"the pipeline differs from the ledger's topology; plan {rec['planId']} is ready: {who} can run `vra pipeline "
        f"deploy --plan {rec['planId']}`")


# ====================================================================== S3 source fed by vra
REVISION_FILE = ".vra-revision"   # commit of a fork zip uploaded by push-source; read by buildspec-build.yml


def _zip_dir(root: Path, dest: Path, exclude: tuple[str, ...] = APP_EXCLUDE) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if any(part in exclude for part in rel.parts) or not p.is_file():
                continue
            z.write(p, rel.as_posix())


def push_source(ctx, lane: str | None, bucket: str | None, key: str | None, pipeline_name: str | None,
                package: bool = False) -> dict:
    """Upload the fork branch of ``lane`` (or this package) to the S3 source when it changed; start the pipeline."""
    pl = automation(ctx.ledger.project).get("pipeline") or {}
    region = pl.get("region") or None
    srcs = pl.get("sources") or {}
    spec = srcs.get("vra" if package else ("forkPreview" if lane == "preview" else "fork")) or {}
    bucket = bucket or spec.get("bucketName")
    key = key or spec.get("objectKey")
    if spec.get("type") == "s3" and not spec.get("bucketName") and (not bucket or (not pipeline_name and not package)):
        # the stack created the bucket: its name and the pipeline to start (the created source never polls) are
        # stack outputs, so an operator does not have to look them up
        outs = outputs(deployed_stack(ctx, f"{pl.get('prefix') or 'vra'}-pipeline", region))
        bucket = bucket or outs.get("SourceBucketName")
        if not package and not pipeline_name:
            pipeline_name = outs.get("PreviewPipelineName" if lane == "preview" else "ReleasePipelineName")
    if not bucket or not key:
        raise UsageError("push-source needs --bucket and --key (or an s3 source with objectKey in "
                         "automation.pipeline.sources, whose bucket the deployed pipeline stack outputs)")
    s3 = ctx.aws.tools_client("s3", region)
    tmp = Path(tempfile.mkdtemp(prefix="vra-src-", dir=_tmp_root(ctx)))
    if package:
        from vra import __version__
        revision = f"package-{__version__}-" + util.combined_hash(
            util.tree_hashes(package_root(), exclude=list(APP_EXCLUDE)))[:16]
        z = tmp / "package.zip"
        _zip_dir(package_root(), z)
    else:
        from vra import gitops
        proj = ctx.ledger.project
        branch = proj["trunk"] if (lane or "release") == "release" else proj["lanes"]["preview"]["targetBranch"]
        g = gitops.ensure_repo(ctx)
        g.run("fetch", "origin", branch)
        revision = g.run("rev-parse", f"origin/{branch}").stdout.strip()
        z = tmp / "source.zip"
        g.run("archive", "--format=zip", "-o", str(z), f"origin/{branch}")
        # an S3 source's CODEBUILD_RESOLVED_SOURCE_VERSION is the object's version id, not a commit, and the
        # archive has no .git: the build stage reads the commit from this file
        with zipfile.ZipFile(z, "a", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(REVISION_FILE, revision + "\n")
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        meta = head.get("Metadata") or {}
        current = meta.get("vra-revision")
        if not package and meta.get("vra-revision-file") != "1":
            current = None   # uploaded before the zip carried REVISION_FILE: upload again
    except Exception:  # noqa: BLE001 - missing object: upload
        current = None
    res: dict[str, Any] = {"bucket": bucket, "key": key, "revision": revision, "uploaded": False, "started": None}
    if current == revision:
        res["message"] = "unchanged"
        return res
    if ctx.dry_run:
        res["message"] = "dry-run: would upload and start the pipeline"
        return res
    with open(z, "rb") as fh:
        meta = {"vra-revision": revision}
        if not package:
            meta["vra-revision-file"] = "1"
        put = s3.put_object(Bucket=bucket, Key=key, Body=fh, Metadata=meta, ServerSideEncryption="aws:kms")
    res.update({"uploaded": True, "versionId": put.get("VersionId")})
    if pipeline_name and not package:
        ex = ctx.aws.tools_client("codepipeline", region).start_pipeline_execution(name=pipeline_name)
        res["started"] = ex.get("pipelineExecutionId")
    return res


__all__ = ["MODES", "PIPELINE_MODES", "validate", "mode", "render", "plan", "deploy", "status", "sync_ledger",
           "push_source", "topology_digest", "scope_envs", "human_only_reasons"]
