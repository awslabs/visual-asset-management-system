# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra watch`: the cron entry point (EventBridge / CodeBuild / crontab).

sync -> per lane with something new: merge -> build -> deploy to every autoDeploy env of the lane
(promotion-chain order) -> tests -> rollback on failure (env policy) -> report (+ notify).
Hard rule: watch never acts on an env with ``autoDeploy: false`` or ``requiresApproval: true``.
"""

from __future__ import annotations

from vra import build as build_mod, deploy, errors, gitops, report, tests_runner, util
from vra.errors import PermissionDenied, VraError, BlockedOnIT, NeedsHuman, TestsFailed, RolledBack, RollbackFailed
from vra.runner import handle_permission, workflow

EXIT_PRIORITY = [errors.ROLLBACK_FAILED, errors.BLOCKED_ON_IT, errors.NEEDS_HUMAN, errors.TESTS_FAILED,
                 errors.OTHER_ERROR]


def auto_envs(ctx, lane: str) -> list[str]:
    """autoDeploy, non-approval envs of a lane, ordered so promotesFrom parents come first."""
    names = [n for n in ctx.ledger.env_names()
             if ctx.ledger.env(n)["lane"] == lane and ctx.ledger.env(n).get("autoDeploy")
             and not ctx.ledger.env(n).get("requiresApproval")]

    def depth(n: str) -> int:
        d, cur, seen = 0, ctx.ledger.env(n).get("promotesFrom"), set()
        while cur and cur not in seen and cur in ctx.ledger.env_names():
            seen.add(cur)
            d += 1
            cur = ctx.ledger.env(cur).get("promotesFrom")
        return d
    return sorted(names, key=lambda n: (depth(n), n))


def refused_envs(ctx, lane: str) -> list[str]:
    return [n for n in ctx.ledger.env_names() if ctx.ledger.env(n)["lane"] == lane and n not in auto_envs(ctx, lane)]


def lanes_in_use(ctx) -> list[str]:
    """Lanes that are enabled and have at least one env (a customer without a preview env never merges preview)."""
    lanes = ctx.ledger.project.get("lanes") or {}
    used = {ctx.ledger.env(n)["lane"] for n in ctx.ledger.env_names()}
    return [ln for ln in ("release", "preview") if (lanes.get(ln) or {}).get("enabled", True) and ln in used]


def run_lane(ctx, run, lane: str, ref: str, suites: list[str] | None) -> dict:
    p = f"{lane}."
    envs = auto_envs(ctx, lane)
    if lane == "preview" and not envs:
        run.skip(f"{p}merge", "no autoDeploy env in the preview lane: nothing to merge for")
        run.skip(f"{p}build", f"no autoDeploy envs in lane {lane}")
        return {"merge": None, "deployed": []}
    merged = gitops.do_merge(ctx, run, lane, ref, prefix=p)
    branch = merged["branch"]
    if not envs:
        run.skip(f"{p}build", f"no autoDeploy envs in lane {lane}")
        return {"merge": merged, "deployed": []}
    built = build_mod.do_build(ctx, run, lane, branch if not ctx.dry_run else ref, prefix=p, only_envs=None)
    build_id = built["buildId"]
    deployed = []
    for env in envs:
        deploy.do_deploy(ctx, run, env, build_id, origin="watch", prefix=p)
        try:
            tests_runner.do_test(ctx, run, env, suites, prefix=p, build=build_id)
        except TestsFailed as exc:
            e = ctx.ledger.env(env)
            if e.get("rollbackPolicy", "auto") == "auto":
                try:
                    res = deploy.perform_rollback(ctx, run, env, exclude_build=build_id,
                                                  restore_checkpoint=run.state.get("checkpoint")
                                                  if run.state.get("migrationsApplied") else None,
                                                  prefix=f"{p}rollback.{env}.", skip_gate=True)
                except VraError as rexc:
                    raise RollbackFailed(f"tests failed on {env} and rollback failed: {rexc.message}") from rexc
                raise RolledBack(f"{exc.message}; rolled back {env} to {res.get('build')}") from exc
            raise
        deployed.append(env)
    return {"merge": merged, "build": build_id, "deployed": deployed, "refusedEnvs": refused_envs(ctx, lane)}


@workflow("watch")
def wf_watch(ctx, run, args):
    lanes = args.get("lanes") or lanes_in_use(ctx)
    from vra.runner import env_lock
    with env_lock(ctx, run, "watch"):  # one watch cycle at a time across every machine (shared sync state)
        return _watch(ctx, run, args, lanes)


def _watch(ctx, run, args, lanes):
    synced = gitops.do_sync(ctx, run)
    todo = {}
    if "release" in lanes and synced.get("newRelease"):
        todo["release"] = synced["newRelease"]
    if "preview" in lanes and synced.get("newPreviewCommit"):
        todo["preview"] = synced["newPreviewCommit"][:12]
    lane_results: dict = {}
    codes = []
    for lane, ref in todo.items():
        try:
            lane_results[lane] = {"status": "SUCCEEDED", **run_lane(ctx, run, lane, ref, args.get("suites"))}
        except PermissionDenied as exc:
            sid = next((s["id"] for s in reversed(run.state["steps"]) if s["status"] in ("blocked", "failed")),
                       f"{lane}.unknown")
            req = handle_permission(ctx, run, sid, exc)
            lane_results[lane] = {"status": errors.S_BLOCKED_ON_IT, "itRequest": req["id"], "message": exc.message}
            codes.append(errors.BLOCKED_ON_IT)
        except VraError as exc:
            lane_results[lane] = {"status": exc.run_status, "message": exc.message}
            codes.append(exc.exit_code)
    out = {"sync": synced, "lanes": lane_results, "nothingNew": not todo}
    run.state["result"] = out
    run.save()
    if args.get("notify", True) and (todo or args.get("notifyAlways")):
        try:
            report.write(ctx, run)
            report.notify(ctx, run)
        except Exception as exc:  # noqa: BLE001
            util.eprint(f"warning: notification failed: {exc}")
    for code in EXIT_PRIORITY:
        if code in codes:
            msg = "; ".join(f"{k}: {v.get('message')}" for k, v in lane_results.items() if v.get("message"))
            cls = {errors.ROLLBACK_FAILED: RollbackFailed, errors.BLOCKED_ON_IT: BlockedOnIT,
                   errors.NEEDS_HUMAN: NeedsHuman, errors.TESTS_FAILED: TestsFailed}.get(code, VraError)
            if code == errors.TESTS_FAILED and any(v.get("status") == errors.ROLLED_BACK for v in lane_results.values()):
                cls = RolledBack
            raise cls(msg, out)
    return out


def cron_line(ledger_path: str, work_dir: str, schedule: str = "17 6 * * *", home: str | None = None) -> str:
    where = f"--home {home}" if home else f"--ledger {ledger_path} --work-dir {work_dir}"
    return f"{schedule} vra {where} --non-interactive --json watch >> {work_dir}/watch.log 2>&1"
