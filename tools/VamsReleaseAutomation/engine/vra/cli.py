# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra` command line. Machine output with --json on stdout; logs on stderr."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from vra import __version__, errors, runner, util
from vra.context import Context
from vra.errors import UsageError, VraError
from vra.ledger import Ledger


def _csv(v: str | None) -> list[str] | None:
    if v is None:
        return None
    if v.strip().lower() == "none":
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def _globals(p: argparse.ArgumentParser, suppress: bool) -> None:
    d = argparse.SUPPRESS if suppress else None
    p.add_argument("--home", default=d, help="shared home folder (default $VRA_HOME, else the one `vra setup` "
                                            "registered in ~/.config/vra/config.yaml)")
    p.add_argument("--ledger", default=d, help="ledger dir without a shared home (default $VRA_LEDGER or ./ledger)")
    p.add_argument("--work-dir", default=d, help="machine-local work dir: fork clone, scratch (default "
                                                "$VRA_WORK_DIR; ~/.cache/vra/<home id> with a home, ./.vra without)")
    p.add_argument("--human-approval", default=d,
                   help="approval record (subject human:<action>) that authorizes a human-only flag when no "
                        "interactive terminal is available")
    p.add_argument("--lock-wait", type=int, default=d, metavar="MINUTES",
                   help="wait up to MINUTES for a busy env lock instead of failing at once")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False,
                   help="machine output on stdout")
    p.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS if suppress else False,
                   help="change nothing; show what would happen")
    p.add_argument("--non-interactive", action="store_true", default=argparse.SUPPRESS if suppress else False)
    p.add_argument("--run-id", default=d, help="explicit run id (new runs) or target run (merge --continue)")


def _freeze_flags(s: argparse.ArgumentParser) -> None:
    s.add_argument("--write-freeze", choices=["auto", "maintenance-mode", "manual"],
                   help="write-freeze backend (default envs/<env> writeFreeze.backend, else auto: VAMS maintenance "
                        "mode when the deployed version supports it, otherwise manual)")
    s.add_argument("--writes-stopped", action="store_true",
                   help="manual freeze: you confirm writes to the env are stopped (recorded who/when; human-only)")
    _abort_flag(s)


def _abort_flag(s: argparse.ArgumentParser) -> None:
    s.add_argument("--abort-workflow-executions", action="store_true",
                   help="stop the VAMS workflow executions still running (default: list and report them only)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vra", description="VAMS release automation engine")
    ap.add_argument("--version", action="version", version=f"vra {__version__}")
    _globals(ap, suppress=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, help_):
        sp = sub.add_parser(name, help=help_)
        _globals(sp, suppress=True)
        return sp

    s = add("setup", "first-run wizard (creates the shared home), or --join an existing home")
    s.add_argument("--answers")
    s.add_argument("--force", action="store_true", help="overwrite existing ledger files (human-only)")
    s.add_argument("--skip-preflight", action="store_true")
    s.add_argument("--join", metavar="PATH_OR_GIT_URL", help="point this machine at an existing home")
    s.add_argument("--branch", help="with --join <git-url>: branch to clone")

    s = add("home", "shared home folder")
    hs = s.add_subparsers(dest="home_cmd", required=True)
    x = hs.add_parser("show")
    _globals(x, suppress=True)
    x = hs.add_parser("sync", help="pull, then publish local changes")
    _globals(x, suppress=True)
    x = hs.add_parser("migrate", help="create a home from a 0.3 ledger and its .vra work dir (human-only)")
    _globals(x, suppress=True)
    x.add_argument("--from", dest="from_ledger", required=True, help="old ledger checkout")
    x.add_argument("--old-work-dir", dest="old_work", help="old .vra work dir (runs, artifacts, checkpoints)")
    x.add_argument("--to", dest="to_home", help="new home folder (default --home / $VRA_HOME)")
    x.add_argument("--sync", choices=["none", "git", "command"], default="none")

    s = add("history", "env history")
    hs = s.add_subparsers(dest="history_cmd", required=True)
    x = hs.add_parser("verify", help="check every history record; fails on malformed, duplicate, unsynced or "
                                     "local-only records")
    _globals(x, suppress=True)
    x.add_argument("--env")
    x = hs.add_parser("show")
    _globals(x, suppress=True)
    x.add_argument("--env", required=True)

    s = add("mcp", "run the MCP server (stdio) for agent front-ends")

    s = add("ledger", "ledger operations")
    ls = s.add_subparsers(dest="ledger_cmd", required=True)
    for n in ("validate", "show", "diff", "import", "upgrade-config", "graph"):
        x = ls.add_parser(n)
        _globals(x, suppress=True)
        x.add_argument("--env", required=n in ("diff", "import", "upgrade-config"))
        if n == "import":
            x.add_argument("--from", dest="from_path", required=True)
        if n == "upgrade-config":
            x.add_argument("--to-ref", required=True)
            x.add_argument("--write", action="store_true", help="write the proposed config into the overlay")

    s = add("iam", "IT role package for strict-IAM envs (plan, apply, verify)")
    ias = s.add_subparsers(dest="iam_cmd", required=True)
    x = ias.add_parser("plan", help="synthesize the env (report only, no AWS writes) and write the IT package")
    _globals(x, suppress=True)
    x.add_argument("--env", required=True)
    x.add_argument("--mode", help="IAM mode(s) to plan for: boundary, precreated, bootstrap, all or a "
                                  "combination with + (default: env.yaml iam.mode)")
    x.add_argument("--ref", help="fork ref to synthesize (default: the trunk)")
    x.add_argument("--report", help="use an existing iam-policy-report.json (its cdk.out folder) instead of "
                                    "synthesizing")
    x = ias.add_parser("apply", help="validate what IT returned and write the ledger overlay (human-only)")
    _globals(x, suppress=True)
    x.add_argument("--env", required=True)
    x.add_argument("--from", dest="from_path", required=True, help="the filled-in it-response YAML")
    x.add_argument("--package", type=int, help="package number (default: the latest)")
    x.add_argument("--approval", help="approval record (subject iam-apply:<packageId>); required on gated envs")
    x = ias.add_parser("verify", help="read-only check of the roles IT created against the applied package")
    _globals(x, suppress=True)
    x.add_argument("--env", required=True)

    s = add("preflight", "IAM simulation; IT request on gaps")
    s.add_argument("--env")
    s.add_argument("--workflow")
    s.add_argument("--all", action="store_true", help="every env, every workflow")

    add("sync", "fetch upstream, ff vendor mirrors, detect new releases/commits")

    s = add("merge", "merge a vendor ref into sync/<lane>-<ref>")
    s.add_argument("--lane", choices=["release", "preview"])
    s.add_argument("--ref")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--continue", dest="cont", action="store_true")
    g.add_argument("--abort", action="store_true")

    s = add("build", "unit suites, per-env synth, bundle, tag")
    s.add_argument("--lane", required=True, choices=["release", "preview"])
    s.add_argument("--ref", required=True)
    s.add_argument("--suites", help="comma list of unit suites or 'none'")
    s.add_argument("--envs", help="comma list to limit synth targets")
    s.add_argument("--allow-missing-scanners", action="store_true",
                   help="build although an enabled scanner (ASH, SBOM) is not installed; recorded in the manifest, "
                        "and such a build is refused by every prod-class env")

    s = add("deploy", "deploy a build to an env")
    s.add_argument("--env", required=True)
    s.add_argument("--build", required=True)
    s.add_argument("--approval")
    s.add_argument("--mode", choices=["in-place", "ab"])
    s.add_argument("--emergency", action="store_true")
    s.add_argument("--allow-replacements", action="store_true",
                   help="allow cdk diff replacements/removals of data-bearing resources")
    s.add_argument("--accept-drift", action="store_true",
                   help="deploy over CloudFormation drift (gated envs: needs an approval with scope accept-drift)")
    _abort_flag(s)

    s = add("test", "run test suites against an env")
    s.add_argument("--env", required=True)
    s.add_argument("--suites", help="unit,synth,live-cli,live-web,customer")

    s = add("promote", "promote the same build to the next env")
    s.add_argument("--from", dest="from_env", required=True)
    s.add_argument("--to", dest="to_env", required=True)
    s.add_argument("--build", required=True)
    s.add_argument("--approval", required=True)
    s.add_argument("--mode", choices=["in-place", "ab"])
    s.add_argument("--emergency", action="store_true")
    s.add_argument("--allow-replacements", action="store_true")
    s.add_argument("--accept-drift", action="store_true")
    _abort_flag(s)

    s = add("backup", "checkpoint an env")
    s.add_argument("--env", required=True)

    s = add("restore", "restore an env to a checkpoint")
    s.add_argument("--env", required=True)
    s.add_argument("--checkpoint", required=True)
    s.add_argument("--approval")
    s.add_argument("--include-auth-tables", action="store_true",
                   help="also restore API keys, auth entities, roles and role assignments (gated envs: approval scope "
                        "restore-auth-tables)")
    _freeze_flags(s)

    s = add("migrate", "chain VAMS data migrations")
    s.add_argument("--env", required=True)
    s.add_argument("--from", dest="from_v")
    s.add_argument("--to", dest="to_v")
    s.add_argument("--build")
    s.add_argument("--source", help="deploymentDataMigration dir (default: deployed build bundle)")
    s.add_argument("--approval", help="approval record for an approval-gated env (subject migrate:<from>-<to>)")

    s = add("rollback", "redeploy the previous good build")
    s.add_argument("--env", required=True)
    s.add_argument("--to-build")
    s.add_argument("--restore-checkpoint")
    s.add_argument("--approval")
    s.add_argument("--restore-approval")
    _freeze_flags(s)

    s = add("reindex", "rebuild the OpenSearch indexes of an env and confirm the result")
    s.add_argument("--env", required=True)
    s.add_argument("--mode", choices=["lambda", "direct"],
                   help="lambda: invoke the deployed reindexer and wait (15-minute limit); direct: run the bundle's "
                        "reindex_utility.py locally (no limit). Default: the env's reindex.mode, else lambda")
    s.add_argument("--clear-indexes", action="store_true", help="delete and recreate the indexes first")
    s.add_argument("--approval", help="approval record for an approval-gated env (subject reindex:<env>, scope "
                                      "reindex)")

    s = add("data-copy", "copy data between envs (prod -> test)")
    s.add_argument("--from", dest="from_env", required=True)
    s.add_argument("--to", dest="to_env", required=True)
    s.add_argument("--approval", required=True)
    s.add_argument("--source-approval",
                   help="approval of the source env's data owner (subject data-copy-to:<target>); default record "
                        "approvals/<source>/data-copy-to_<target>.yaml")
    s.add_argument("--databases")
    s.add_argument("--mask-hook")
    s.add_argument("--method", choices=["export", "scan"], default="export")
    _freeze_flags(s)

    s = add("unlock", "release an env lock held by a parked/failed run (recorded)")
    s.add_argument("--env", required=True)
    s.add_argument("--run", dest="lock_run", help="run id that holds the lock (required for an active lock)")
    s.add_argument("--reason", required=True)

    s = add("watch", "cron entry point")
    s.add_argument("--lanes", help="release,preview")
    s.add_argument("--suites")
    s.add_argument("--no-notify", action="store_true")
    s.add_argument("--print-cron", action="store_true")

    s = add("resume", "continue a blocked/failed run")
    s.add_argument("resume_id", metavar="run-id")
    s.add_argument("--confirm", action="store_true",
                   help="record that you did the manual work the run waits for (manual migration steps, DNS switch)")
    s.add_argument("--takeover", action="store_true",
                   help="continue a run whose holder machine went away, after its lease expired (recorded; "
                        "human-only)")

    s = add("status", "run state")
    s.add_argument("status_id", nargs="?", metavar="run-id")
    s.add_argument("--all", dest="status_all", action="store_true",
                   help="every run and lock in the shared state, from every machine")

    s = add("report", "Markdown + JSON report")
    s.add_argument("report_id", nargs="?", metavar="run-id")
    s.add_argument("--notify", action="store_true")

    __import__("vra.pipeline_cli").pipeline_cli.register(sub, _globals)  # `vra pipeline ...` (sets ns.handler)

    # Internal: AWS SDK credential_process helper used by cdk / migration child processes
    # (see AwsFactory.credential_process_env). Not listed in --help.
    s = sub.add_parser("credential-process")
    _globals(s, suppress=True)
    s.add_argument("--env", required=True)
    s.add_argument("--role", default="deploy")
    s.add_argument("--base-profile")
    s.add_argument("--session-run-id", default="vra")
    s.add_argument("--source-identity")
    s.add_argument("--nonce-file")
    return ap


def human_actions(ns: argparse.Namespace) -> list[tuple[str, str | None]]:
    """The human-only actions (vra.human.HUMAN_ONLY) this invocation asks for, with the env they concern."""
    out: list[tuple[str, str | None]] = []
    env = getattr(ns, "env", None) or getattr(ns, "to_env", None)
    if getattr(ns, "confirm", False):
        out.append(("confirm", env))
    if getattr(ns, "allow_missing_scanners", False):
        out.append(("allow-missing-scanners", env))
    if getattr(ns, "writes_stopped", False):
        out.append(("writes-stopped", env))
    if getattr(ns, "abort_workflow_executions", False):
        out.append(("abort-workflow-executions", env))
    if getattr(ns, "takeover", False):
        out.append(("takeover", env))
    if ns.cmd == "unlock":
        out.append(("unlock", ns.env))
    if ns.cmd == "ledger" and ns.ledger_cmd == "import":
        out.append(("ledger-import", ns.env))
    if ns.cmd == "ledger" and ns.ledger_cmd == "upgrade-config" and getattr(ns, "write", False):
        out.append(("upgrade-config-write", ns.env))
    if ns.cmd == "setup" and getattr(ns, "force", False):
        out.append(("setup-force", None))
    if ns.cmd == "home" and ns.home_cmd == "migrate":
        out.append(("home-migrate", None))
    if ns.cmd == "iam" and ns.iam_cmd == "apply" and not getattr(ns, "dry_run", False):
        out.append(("iam-apply", ns.env))
    return out


def agent_guard(ns: argparse.Namespace) -> None:
    """Refuse human-only actions for an agent before any context is built (VRA_AGENT=1 / VRA_MCP=1)."""
    from vra import human
    if not human.is_agent():
        return
    for action, _env in human_actions(ns):
        raise errors.NeedsHuman(f"{human.HUMAN_ONLY[action]}; it is human-only and an agent cannot run it. Ask the "
                                f"human to run this command", {"humanOnly": action})


def _resume_env(ctx: Context, ns: argparse.Namespace) -> str | None:
    """The env of the run `vra resume <run-id>` continues (resume has no --env): a human-only flag on it
    (--takeover, --confirm, --writes-stopped) is authorized by an approval record of that env."""
    if ns.cmd != "resume" or not getattr(ns, "resume_id", None):
        return None
    from vra.state import Run
    try:
        env = Run.load(ctx.state_dir, ns.resume_id, store=ctx.store, home=ctx.home).state.get("env")
    except Exception:  # noqa: BLE001 - an unknown run fails later with its own message
        return None
    if env is None and getattr(ns, "human_approval", None):
        # a run of no env (a build) holds only its run lock. live test: such a run could never be taken
        # over without a TTY ("(and an env)"). The record's own env authorizes it, checked against that env's
        # approvers like every human-only record; it must name an env of this ledger.
        from vra import approvals
        try:
            rec, _p = approvals.load_record(ctx, "", ns.human_approval, "human:takeover")
        except Exception:  # noqa: BLE001 - a missing or invalid record fails later with its own message
            return None
        if rec.get("env") in ctx.ledger.env_names():
            return rec["env"]
    return env


def human_gate(ctx: Context, ns: argparse.Namespace) -> list[dict]:
    from vra import human
    tty = human._tty() and not ns.non_interactive
    actions = human_actions(ns)
    if any(env is None for _a, env in actions):
        renv = _resume_env(ctx, ns)
        actions = [(a, env or renv) for a, env in actions]
    out = []
    for action, env in actions:
        if (action == "iam-apply" and not tty and not getattr(ns, "human_approval", None)
                and getattr(ns, "approval", None)):
            # `vra iam apply --approval <record>`: the record bound to this exact IT response (subject
            # iam-apply:<package>:<sha16>, scope iam-apply, single use) is the person's authorization; iam.apply
            # requires and claims it on every env (live test: the documented one-record form was refused)
            ctx.iam_apply_bound_authorization = True
            continue
        if (action == "iam-apply" and not tty and not getattr(ns, "human_approval", None)
                and not human.is_agent()):
            # live test: the generic message asked for a `human:iam-apply` record, which iam.apply then
            # refuses; name the record it takes (bound to this exact response)
            raise errors.NeedsHuman(f"{human.HUMAN_ONLY['iam-apply']}; without an interactive terminal it needs --approval "
                             "naming the record bound to this IT response (subject iam-apply:<packageId>:<first 16 "
                             "hex of the response's sha256>, scope [iam-apply]); `vra --dry-run iam apply` prints "
                             "the exact subject as approvalSubject", {"humanOnly": "iam-apply"})
        out.append(human.require(ctx, action, env, getattr(ns, "human_approval", None), tty=tty))
    return out


def credential_process(ns: argparse.Namespace) -> int:
    """Print fresh AssumeRole credentials in credential_process format. Secrets go to stdout only,
    as the protocol requires; nothing is logged."""
    from vra import aws as aws_mod
    if not aws_mod.check_credproc_nonce(ns.nonce_file):
        util.eprint("credential-process is an internal helper of vra's own child processes (cdk, migration "
                    "scripts); it does not run on its own")
        return errors.USAGE_ERROR
    aws_mod.restore_base_env()
    ledger = Ledger(Path(ns.ledger or os.environ.get("VRA_LEDGER") or "./ledger"))
    factory = aws_mod.AwsFactory(ledger, ns.session_run_id, base_profile=ns.base_profile)
    factory.source_identity_value = ns.source_identity
    fetch = factory.assume_fetcher(ns.env, ns.role)
    if fetch is None:
        util.eprint(f"envs/{ns.env} has no role ARN for role {ns.role!r}")
        return errors.USAGE_ERROR
    try:
        print(aws_mod.credential_process_output(fetch()))
    except Exception as exc:  # noqa: BLE001
        util.eprint(f"credential-process: AssumeRole failed: {util.redact(str(exc))}")
        return errors.OTHER_ERROR
    return 0


def make_context(ns: argparse.Namespace) -> Context:
    from vra import home as home_mod
    target = ns.home or os.environ.get("VRA_HOME")
    if ns.cmd in ("setup", "home") and target and not (Path(target).expanduser() / home_mod.HOME_FILE).exists():
        # `vra setup --home <new>` / `vra home migrate --to <new>` create it; nothing to open yet
        work = Path(ns.work_dir or os.environ.get("VRA_WORK_DIR") or Path(target).expanduser() / ".vra-local" / "work")
        h = home_mod.Home.legacy(Path(target).expanduser() / "ledger", work)
        return Context(ledger=Ledger(h.ledger_dir), work_dir=work, dry_run=bool(ns.dry_run), json=bool(ns.json),
                       non_interactive=bool(ns.non_interactive) or not sys.stdin.isatty(), run_id=ns.run_id, home=h)
    h, work = home_mod.resolve(ns.home, ns.ledger, ns.work_dir)
    return Context(ledger=Ledger(h.ledger_dir), work_dir=work, dry_run=bool(ns.dry_run), json=bool(ns.json),
                   non_interactive=bool(ns.non_interactive) or not sys.stdin.isatty(), run_id=ns.run_id, home=h,
                   takeover=bool(getattr(ns, "takeover", False)),
                   lock_wait_seconds=int(getattr(ns, "lock_wait", None) or 0) * 60)


def dispatch(ctx: Context, ns: argparse.Namespace) -> runner.Outcome:
    c = ns.cmd
    authorized = human_gate(ctx, ns)
    if authorized:
        ctx.human_authorizations = authorized
    if c not in ("setup", "home"):
        ctx.home.pull()
    if getattr(ns, "handler", None):  # command groups registered from their own module (pipeline_cli.register)
        return ns.handler(ctx, ns)
    if c == "setup":
        from vra import setup as setup_mod
        if ns.join:
            from vra import home as home_mod
            res = home_mod.join(ns.join, ns.home or os.environ.get("VRA_HOME"), ns.branch)
            ok = res["validation"]["ok"]
            return runner.Outcome(None, 0 if ok else errors.USAGE_ERROR, res,
                                  f"joined {res['home']}" + ("" if ok else " (ledger validation failed)"))
        res = setup_mod.run_setup(ctx, ns.answers, ns.force, ns.skip_preflight,
                                  home_arg=ns.home or os.environ.get("VRA_HOME"),
                                  legacy=bool(ns.ledger or os.environ.get("VRA_LEDGER")))
        val = res.get("validation")
        if val and not val["ok"]:
            return runner.Outcome(None, errors.USAGE_ERROR, res, "ledger validation failed")
        if ctx.dry_run or ns.skip_preflight:
            return runner.Outcome(None, 0, res, "ledger written" + (" (dry-run)" if ctx.dry_run else ""))
        out = runner.start(ctx, "preflight", {"envs": None})
        out.result = {"setup": res, "preflight": out.result}
        return out
    if c == "ledger":
        return ledger_cmd(ctx, ns)
    if c == "home":
        return home_cmd(ctx, ns)
    if c == "history":
        from vra import report
        if ns.history_cmd == "show":
            return runner.Outcome(None, 0, {"env": ns.env, "records": ctx.ledger.history(ns.env)})
        res = report.history_verify(ctx, ns.env)
        return runner.Outcome(None, 0 if res["ok"] else errors.NEEDS_HUMAN, res,
                              "history verified" if res["ok"] else f"{len(res['problems'])} problem(s)")
    if c == "iam":
        return iam_cmd(ctx, ns)
    if c == "preflight":
        if not ns.all and not ns.env:
            raise UsageError("preflight needs --env E or --all")
        args = {"env": None if ns.all else ns.env, "workflow": ns.workflow}
        return runner.start(ctx, "preflight", args, env=None if ns.all else ns.env)
    if c == "sync":
        return runner.start(ctx, "sync", {})
    if c == "merge":
        if ns.cont or ns.abort:
            from vra.state import Run
            run = Run.load(ctx.state_dir, ctx.run_id, store=ctx.store, home=ctx.home) if ctx.run_id else (
                Run.latest(ctx.state_dir, status=errors.S_NEEDS_HUMAN, workflow="merge", home=ctx.home,
                           store=ctx.store)
                or Run.latest(ctx.state_dir, status=errors.S_NEEDS_HUMAN, workflow="watch", home=ctx.home,
                              store=ctx.store))
            if run is None:
                raise UsageError("no merge run waiting; pass --run-id")
            if ns.abort:
                from vra import gitops
                return runner.Outcome(run, 0, gitops.abort_merge(ctx, run), "merge aborted")
            return runner.resume(ctx, run.run_id)
        if not ns.lane:
            raise UsageError("merge needs --lane (or --continue / --abort)")
        return runner.start(ctx, "merge", {"lane": ns.lane, "ref": ns.ref})
    if c == "build":
        return runner.start(ctx, "build", {"lane": ns.lane, "ref": ns.ref, "suites": _csv(ns.suites),
                                           "envs": _csv(ns.envs), "allowMissingScanners": ns.allow_missing_scanners})
    if c == "deploy":
        return runner.start(ctx, "deploy", {"env": ns.env, "build": ns.build, "approval": ns.approval, "mode": ns.mode,
                                            "emergency": ns.emergency, "allowReplacements": ns.allow_replacements,
                                            "acceptDrift": ns.accept_drift,
                                            "abortWorkflowExecutions": ns.abort_workflow_executions},
                            env=ns.env, build=ns.build)
    if c == "unlock":
        return runner.unlock(ctx, ns.env, ns.lock_run, ns.reason)
    if c == "test":
        return runner.start(ctx, "test", {"env": ns.env, "suites": _csv(ns.suites)}, env=ns.env)
    if c == "promote":
        return runner.start(ctx, "promote", {"from": ns.from_env, "to": ns.to_env, "build": ns.build,
                                             "approval": ns.approval, "mode": ns.mode, "emergency": ns.emergency,
                                             "allowReplacements": ns.allow_replacements,
                                             "acceptDrift": ns.accept_drift,
                                             "abortWorkflowExecutions": ns.abort_workflow_executions},
                            env=ns.to_env, build=ns.build)
    if c == "backup":
        return runner.start(ctx, "backup", {"env": ns.env}, env=ns.env)
    if c == "restore":
        return runner.start(ctx, "restore", {"env": ns.env, "checkpoint": ns.checkpoint, "approval": ns.approval,
                                             "writeFreeze": ns.write_freeze, "writesStopped": ns.writes_stopped,
                                             "abortWorkflowExecutions": ns.abort_workflow_executions,
                                             "includeAuthTables": ns.include_auth_tables}, env=ns.env)
    if c == "migrate":
        return runner.start(ctx, "migrate", {"env": ns.env, "from": ns.from_v, "to": ns.to_v, "build": ns.build,
                                             "source": ns.source, "dryRun": ctx.dry_run,
                                             "approval": ns.approval}, env=ns.env)
    if c == "rollback":
        return runner.start(ctx, "rollback", {"env": ns.env, "toBuild": ns.to_build,
                                              "restoreCheckpoint": ns.restore_checkpoint, "approval": ns.approval,
                                              "restoreApproval": ns.restore_approval,
                                              "writeFreeze": ns.write_freeze, "writesStopped": ns.writes_stopped,
                                              "abortWorkflowExecutions": ns.abort_workflow_executions}, env=ns.env)
    if c == "reindex":
        return runner.start(ctx, "reindex", {"env": ns.env, "mode": ns.mode, "clearIndexes": ns.clear_indexes,
                                             "approval": ns.approval}, env=ns.env)
    if c == "data-copy":
        return runner.start(ctx, "data-copy", {"from": ns.from_env, "to": ns.to_env, "approval": ns.approval,
                                               "sourceApproval": ns.source_approval,
                                               "databases": _csv(ns.databases), "maskHook": ns.mask_hook,
                                               "method": ns.method,
                                               "writeFreeze": ns.write_freeze, "writesStopped": ns.writes_stopped,
                                              "abortWorkflowExecutions": ns.abort_workflow_executions}, env=ns.to_env)
    if c == "watch":
        if ns.print_cron:
            from vra import watch
            return runner.Outcome(None, 0, {"cron": watch.cron_line(str(ctx.ledger.root), str(ctx.work_dir),
                                                                     home=None if ctx.home.legacy else str(ctx.home.root))})
        return runner.start(ctx, "watch", {"lanes": _csv(ns.lanes), "suites": _csv(ns.suites),
                                           "notify": not ns.no_notify})
    if c == "resume":
        return runner.resume(ctx, ns.resume_id, confirm=ns.confirm)
    if c == "status":
        from vra import report
        if ns.status_all:
            return runner.Outcome(None, 0, report.status_all(ctx))
        return runner.Outcome(None, 0, report.status(ctx, ns.status_id))
    if c == "report":
        from vra import report
        from vra.state import Run
        run = Run.load(ctx.state_dir, ns.report_id, store=ctx.store, home=ctx.home) if ns.report_id else \
            Run.latest(ctx.state_dir, home=ctx.home, store=ctx.store)
        if run is None:
            raise UsageError("no runs yet")
        rep = report.write(ctx, run)
        res: dict[str, Any] = {"report": str(run.dir / "report.md"), "status": rep["status"]}
        if ns.notify:
            res["notification"] = report.notify(ctx, run)
        return runner.Outcome(run, 0, res)
    raise UsageError(f"unknown command {c}")


def ledger_cmd(ctx: Context, ns) -> runner.Outcome:
    lc = ns.ledger_cmd
    L = ctx.ledger
    if lc == "graph":
        return runner.Outcome(None, 0, ledger_graph(L))
    if lc == "validate":
        res = L.validate()
        if ctx.home.legacy and res.get("ok") is not None:
            res["warnings"].append("no shared home: run state, locks and history live in this machine's work dir "
                                   f"({ctx.state_dir}); other machines and CI jobs do not see them. Use `vra home "
                                   "migrate` (or `stateStore: s3`) before a second machine operates this ledger")
        return runner.Outcome(None, 0 if res["ok"] else errors.USAGE_ERROR, res,
                              "ledger valid" if res["ok"] else f"{len(res['errors'])} error(s)")
    if lc == "show":
        return runner.Outcome(None, 0, L.show(ns.env))
    if lc == "diff":
        return runner.Outcome(None, 0, L.diff(ns.env, ctx.artifacts_dir))
    if lc == "import":
        return runner.Outcome(None, 0, L.import_config(ns.env, Path(ns.from_path), ctx.dry_run))
    if lc == "upgrade-config":
        from vra import gitops
        g = gitops.ensure_repo(ctx)
        g.run("fetch", "upstream", "--tags", "--prune")
        rel = L.template_path(ns.env)
        text = None
        for ref in (ns.to_ref, f"refs/tags/{ns.to_ref}", f"refs/remotes/upstream/{ns.to_ref}"):
            text = g.show(ref, rel)
            if text:
                break
        if not text:
            raise UsageError(f"template {rel} not found at {ns.to_ref}")
        res = L.upgrade_config(ns.env, text, ns.to_ref)
        from vra.ledger import CUSTOM_CONFIG
        custom_text = None
        for ref in (ns.to_ref, f"refs/tags/{ns.to_ref}", f"refs/remotes/upstream/{ns.to_ref}"):
            custom_text = g.show(ref, CUSTOM_CONFIG)
            if custom_text:
                break
        if custom_text:
            res["customConfig"] = L.upgrade_custom_config(ns.env, custom_text, ns.to_ref)
        if ns.write and not ctx.dry_run:
            util.write_json(L.overlay_dir(ns.env) / "infra/config/config.json", res["proposed"])
            res["written"] = ["infra/config/config.json"]
            cc = res.get("customConfig")
            if cc and cc["newKeys"]:
                util.write_json(L.overlay_dir(ns.env) / CUSTOM_CONFIG, cc["proposed"])
                res["written"].append(CUSTOM_CONFIG)
        return runner.Outcome(None, 0, res)
    raise UsageError(lc)


def iam_cmd(ctx: Context, ns) -> runner.Outcome:
    from vra import iam
    if ns.iam_cmd == "plan":
        modes = iam.parse_modes(ns.mode) if ns.mode else None
        return runner.start(ctx, "iam-plan", {"env": ns.env, "modes": modes, "ref": ns.ref, "report": ns.report},
                            env=ns.env)
    if ns.iam_cmd == "apply":
        res = iam.apply(ctx, ns.env, Path(ns.from_path), ns.approval, ns.package,
                        authorization=runner.human_authorization(ctx, "iam-apply"))
        return runner.Outcome(None, 0, res, f"{res['packageId']} applied to envs/{ns.env}/overlay"
                              + (" (dry-run)" if ctx.dry_run else ""))
    if ns.iam_cmd == "verify":
        res = iam.verify(ctx, ns.env)
        ok = not res["problems"]
        return runner.Outcome(None, 0 if ok else errors.BLOCKED_ON_IT, res,
                              "roles verified" + (f"; {len(res['notChecked'])} item(s) could not be checked"
                                                  if res["notChecked"] else "") if ok else
                              f"{len(res['problems'])} problem(s) for IT")
    raise UsageError(ns.iam_cmd)


def ledger_graph(L: Ledger) -> dict:
    """Topology for CI renderers: every env with its parent, gate and tier (no CI file restates it)."""
    from vra.ledger import is_prod_class, required_suites, tier
    envs = {}
    for n in L.env_names():
        e = L.env(n)
        envs[n] = {"lane": e["lane"], "promotesFrom": e.get("promotesFrom"), "requiresApproval": e["requiresApproval"],
                   "autoDeploy": e["autoDeploy"], "tier": tier(L, n), "isProdClass": is_prod_class(L, n),
                   "requiredSuites": required_suites(L, n), "account": e["account"], "region": e["region"],
                   "children": sorted(x for x in L.env_names() if L.env(x).get("promotesFrom") == n)}
    roots = sorted(n for n, v in envs.items() if not v["promotesFrom"])
    return {"envs": envs, "roots": roots}


def home_cmd(ctx: Context, ns) -> runner.Outcome:
    from vra import home as home_mod
    if ns.home_cmd == "show":
        return runner.Outcome(None, 0, ctx.home.describe())
    if ns.home_cmd == "sync":
        if ctx.home.legacy:
            raise UsageError("no shared home configured (pass --home or set VRA_HOME)")
        return runner.Outcome(None, 0, home_mod.sync_now(ctx.home), "home synced")
    if ns.home_cmd == "migrate":
        target = ns.to_home or ns.home or os.environ.get("VRA_HOME")
        if not target:
            raise UsageError("home migrate needs --to <new home folder> (or --home / VRA_HOME)")
        res = home_mod.migrate(Path(ns.from_ledger), Path(ns.old_work) if ns.old_work else None, Path(target),
                               {"type": ns.sync}, dry_run=ctx.dry_run)
        return runner.Outcome(None, 0, res, "home created" if not ctx.dry_run else "migration plan")
    raise UsageError(ns.home_cmd)


def emit(ctx: Context | None, outcome: runner.Outcome, as_json: bool) -> None:
    d = outcome.to_dict()
    if as_json:
        print(json.dumps(d, indent=2, default=str))
        return
    if outcome.run is not None:
        print(f"{d.get('workflow')} {d.get('runId')}: {d.get('status')} (exit {outcome.exit_code})")
        if d.get("message"):
            print(f"  {d['message']}")
        print(f"  report: {outcome.run.dir / 'report.md'}")
        for it in d.get("itRequests", []):
            print(f"  IT request: {it['path']}")
    else:
        if d.get("message"):
            print(d["message"])
        if d.get("result") is not None:
            print(json.dumps(d["result"], indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    try:
        ns = ap.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else errors.USAGE_ERROR
    for k in ("ledger", "work_dir", "run_id", "home", "human_approval", "lock_wait"):
        if not hasattr(ns, k):
            setattr(ns, k, None)
    for k in ("json", "dry_run", "non_interactive"):
        if not hasattr(ns, k):
            setattr(ns, k, False)
    if ns.cmd == "credential-process":
        return credential_process(ns)
    if ns.cmd == "mcp":
        from vra import mcp_server
        mcp_server.main()
        return 0
    ctx = None
    try:
        agent_guard(ns)
        ctx = make_context(ns)
        outcome = dispatch(ctx, ns)
    except VraError as exc:
        outcome = runner.Outcome(None, exc.exit_code, exc.detail or None, exc.message)
    except KeyboardInterrupt:
        util.eprint("interrupted")
        return errors.OTHER_ERROR
    except Exception as exc:  # noqa: BLE001 - agents parse stdout: never leave it empty
        import traceback
        util.eprint(util.redact(traceback.format_exc()))
        outcome = runner.Outcome(None, errors.OTHER_ERROR, {"errorType": type(exc).__name__},
                                 f"{type(exc).__name__}: {util.redact(str(exc))[:500]}")
    emit(ctx, outcome, bool(ns.json))
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
