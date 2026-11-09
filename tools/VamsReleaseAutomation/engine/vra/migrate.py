# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""VAMS data migrations: discover ``infra/deploymentDataMigration/v<A>_to_v<B>`` in the checked-out ref
(or a build bundle), chain them by version, and run each script through its own CLI
(``--config <json> [--dry-run] [--confirm-account ID --yes]``), annotated by ``data/upgrade-matrix.yaml``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from vra import data as vra_data
from pathlib import Path
from typing import Any

import yaml

from vra import permissions, util
from vra.errors import NeedsHuman, UsageError, VraError
from vra.runner import env_lock, workflow

DIR_RE = re.compile(r"^v(\d+\.\d+)_to_v(\d+\.\d+)$")
PLACEHOLDER_RE = re.compile(r"(YOUR-STACK|<[A-Z0-9_]+>|XXXXX)")


def matrix() -> dict:
    return yaml.safe_load(vra_data.text("upgrade-matrix.yaml"))


def discover(root: Path) -> list[dict]:
    """``root`` is the deploymentDataMigration dir (bundle ``migrations/`` or ``<src>/infra/deploymentDataMigration``)."""
    out = []
    if not root.exists():
        return out
    mx = matrix()
    for d in sorted(root.iterdir()):
        m = DIR_RE.match(d.name)
        if not m or not d.is_dir():
            continue
        frm, to = m.group(1), m.group(2)
        up = d / "upgrade"
        script = up / f"{d.name}_migration.py"
        if not script.exists():
            cands = sorted(up.glob("*_migration.py")) if up.exists() else []
            script = cands[0] if cands else None
        cfg = up / f"{d.name}_migration_config.json"
        text = script.read_text(encoding="utf-8", errors="replace") if script else ""
        notes = {**mx.get("defaults", {}), **(mx.get("transitions", {}).get(d.name) or {})}
        out.append({"id": d.name, "from": frm, "to": to, "dir": str(up), "script": str(script) if script else None,
                    "configTemplate": str(cfg) if cfg.exists() else None,
                    "interface": {"confirmAccount": "--confirm-account" in text, "yes": "'--yes'" in text or
                                  '"--yes"' in text, "steps": "--steps" in text, "region": "--region" in text,
                                  "ssmPrefix": "resource_names_ssm_param_prefix" in (cfg.read_text() if cfg.exists()
                                                                                       else text)},
                    "requiresAB": bool(notes.get("requiresAB")), "manualSteps": notes.get("manualSteps", []),
                    "notes": notes.get("notes"), "resolvesViaSsm": notes.get("resolvesViaSsm", True)})
    return out


def chain(transitions: list[dict], frm: str, to: str) -> list[dict]:
    """Shortest path of transitions from minor ``frm`` to minor ``to`` (BFS)."""
    a, b = util.minor(frm), util.minor(to)
    if util.version_tuple(a) == util.version_tuple(b):
        return []
    if util.version_tuple(a) > util.version_tuple(b):
        raise NeedsHuman(f"downgrade {a} -> {b} has no migration path; restore a checkpoint instead")
    graph: dict[str, list[dict]] = {}
    for t in transitions:
        graph.setdefault(t["from"], []).append(t)
    q = deque([(a, [])])
    seen = {a}
    while q:
        cur, path = q.popleft()
        for t in sorted(graph.get(cur, []), key=lambda t: util.version_tuple(t["to"])):
            if util.version_tuple(t["to"]) > util.version_tuple(b):
                continue
            np = path + [t]
            if t["to"] == b:
                return np
            if t["to"] not in seen:
                seen.add(t["to"])
                q.append((t["to"], np))
    # versions between with no data changes (e.g. 2.6 -> 2.7 without a migration dir) are fine if no
    # transition starts at a version we pass through; report the gap otherwise
    raise NeedsHuman(f"no migration chain from {a} to {b}; available: {[t['id'] for t in transitions]}")


def plan(ctx, env: str, migrations_root: Path, frm: str | None, to: str) -> dict:
    if not frm:
        return {"from": None, "to": to, "steps": [], "note": "fresh install (no recorded version); no migrations"}
    ts = discover(migrations_root)
    try:
        steps = chain(ts, frm, to)
    except NeedsHuman:
        # allow gaps when there is simply no migration directory for the newer minors
        if not any(util.version_tuple(t["from"]) >= util.version_tuple(util.minor(frm)) for t in ts):
            steps = []
        else:
            raise
    skip = set((ctx.ledger.env(env).get("migrations") or {}).get("skip", []))
    steps = [s for s in steps if s["id"] not in skip]
    return {"from": frm, "to": to, "steps": steps, "requiresAB": any(s["requiresAB"] for s in steps),
            "manualSteps": [m for s in steps for m in s["manualSteps"]], "skipped": sorted(skip)}


def build_config(ctx, env: str, step: dict, out_path: Path, ssm_prefix: str | None) -> Path:
    e = ctx.ledger.env(env)
    cfg: dict[str, Any] = {}
    if step.get("configTemplate"):
        cfg = util.read_json(Path(step["configTemplate"]))
    if step["interface"].get("ssmPrefix") and ssm_prefix:
        cfg["resource_names_ssm_param_prefix"] = ssm_prefix
    cfg["aws_region"] = e["region"]
    cfg["aws_profile"] = None
    cfg["dry_run"] = False
    overrides = ((e.get("migrations") or {}).get("configOverrides") or {}).get(step["id"], {})
    cfg.update(overrides)
    unfilled = sorted(k for k, v in cfg.items() if isinstance(v, str) and not k.startswith("_") and k != "comments"
                      and PLACEHOLDER_RE.search(v))
    if unfilled and not step.get("resolvesViaSsm", True):
        raise NeedsHuman(f"migration {step['id']} needs explicit values for {unfilled}; set them under envs/{env}/"
                         f"env.yaml migrations.configOverrides.{step['id']}", {"keys": unfilled})
    if unfilled:
        # v2.6+ scripts resolve these from SSM when left null
        for k in unfilled:
            cfg[k] = None
    util.write_json(out_path, cfg)
    return out_path


def run_step(ctx, run, env: str, step: dict, mode: str, ssm_prefix: str | None, sc, extra_args: list[str] | None = None
             ) -> dict:
    if not step.get("script"):
        raise NeedsHuman(f"migration {step['id']} has no runnable *_migration.py; follow its README manually")
    cfg = build_config(ctx, env, step, run.dir / "migrations" / f"{step['id']}.config.json", ssm_prefix)
    e = ctx.ledger.env(env)
    py = os.environ.get("VRA_MIGRATION_PYTHON", sys.executable)
    argv = [py, step["script"], "--config", str(cfg)]
    if step["interface"].get("region"):
        argv += ["--region", e["region"]]
    if mode == "dry-run":
        argv.append("--dry-run")
    elif step["interface"].get("confirmAccount"):
        argv += ["--confirm-account", e["account"]]
        if step["interface"].get("yes", True):
            argv.append("--yes")
    argv += extra_args or []
    child_env = {}
    try:
        child_env = ctx.aws.subprocess_env(env, "deploy")
    except Exception as exc:  # noqa: BLE001
        sc.log(f"no AWS env for migration child: {exc}")
    if ctx.dry_run and mode != "dry-run":
        return {"dryRun": True, "argv": [str(a) for a in argv[1:]]}
    p = ctx.shell(sc.log_path).run(argv, cwd=step["dir"], env=child_env, check=False, mutating=False,
                                   input_text="", timeout=6 * 3600)  # never auto-answer prompts
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        denials = permissions.classify_text(out, "migration")
        if denials:
            from vra.errors import PermissionDenied
            raise PermissionDenied(f"migration {step['id']} ({mode}) was denied", denials, env=env)
        raise VraError(f"migration {step['id']} ({mode}) failed with exit {p.returncode}",
                       {"tail": util.redact(out[-2000:])})
    return {"id": step["id"], "mode": mode, "exit": 0}


def do_migrate(ctx, run, env: str, migrations_root: Path, frm: str | None, to: str, dry_run_only: bool = False,
               prefix: str = "", ssm_prefix: str | None = None, phase: str = "all",
               check_buckets: bool = True) -> dict:
    """phase: plan | dry-run | apply | all. ``check_buckets``: re-check the registered asset buckets before the
    first migration step (vra deploy checks them itself before cdk deploy and passes False)."""
    p = run.step(f"{prefix}migrate.plan", lambda sc: _json_plan(plan(ctx, env, migrations_root, frm, to)),
                 "migration plan")
    if phase == "plan":
        return p
    if p["steps"] and check_buckets:
        from vra import buckets
        buckets.require(ctx, run, env, ["migrate"], f"{prefix}migrate.registered-buckets",
                        f"data migrations on {env}")
    if ssm_prefix is None:
        try:
            ssm_prefix = ctx.ledger.ssm_prefix(env)
        except Exception:  # noqa: BLE001
            ssm_prefix = None
    applied = []
    for st in p["steps"]:
        if st.get("manualSteps") and phase in ("all", "apply") and not dry_run_only:
            def _manual(sc, st=st):
                return run.require_confirmation(
                    sc.step["id"], f"migration {st['id']} has manual steps the automation cannot do: "
                    + "; ".join(st["manualSteps"]), {"manualSteps": st["manualSteps"]})
            run.step(f"{prefix}migrate.{st['id']}.manual", _manual, f"{st['id']} manual steps")
        if phase in ("all", "dry-run"):
            run.step(f"{prefix}migrate.{st['id']}.dry-run",
                     lambda sc, st=st: run_step(ctx, run, env, st, "dry-run", ssm_prefix, sc), f"{st['id']} dry-run")
        if phase in ("all", "apply") and not dry_run_only:
            run.step(f"{prefix}migrate.{st['id']}.apply",
                     lambda sc, st=st: run_step(ctx, run, env, st, "apply", ssm_prefix, sc), f"{st['id']} apply")
            applied.append(st["id"])
    if applied:
        run.state.setdefault("migrationsApplied", [])
        for a in applied:
            if a not in run.state["migrationsApplied"]:
                run.state["migrationsApplied"].append(a)
        run.save()
    return {"plan": p, "applied": applied}


def _json_plan(p: dict) -> dict:
    return json.loads(json.dumps(p, default=str))


def source_tree_version(root: Path) -> str | None:
    """VAMS_VERSION of the tree a ``<src>/infra/deploymentDataMigration`` dir belongs to (``infra/config/config.ts``)."""
    from vra import build as build_mod
    cfg = root.parent / "config" / "config.ts"
    if not cfg.is_file():
        return None
    m = build_mod.VERSION_RE.search(cfg.read_text(encoding="utf-8", errors="replace"))
    return m.group(1) if m else None


def newest_build_for(ctx, env: str) -> str | None:
    """The newest build in the home with a synth for ``env`` (what the next ``vra deploy`` of it would use)."""
    best: tuple[str, str] | None = None
    d = ctx.artifacts_dir
    if not d.is_dir():
        return None
    for mp in d.glob("*/manifest.json"):
        try:
            m = util.read_json(mp)
        except Exception:  # noqa: BLE001
            continue
        if not any(t == env or (v or {}).get("env") == env for t, v in (m.get("targets") or {}).items()):
            continue
        key = (str(m.get("createdAt") or ""), str(m.get("buildId") or mp.parent.name))
        if best is None or key > best:
            best = key
    return best[1] if best else None


def resolve_versions(ctx, env: str, args: dict) -> dict:
    """Where standalone ``vra migrate`` reads from: migrations root, ``from`` and ``to``, each with its source.

    With vra deploy history: the deployed build, its version (``to``) and the version it replaced (``from``).
    Without it (an env imported with ``vra ledger import``): ``from`` is the deployed core stack's version (its
    description, as ``vra deploy`` reads it), ``to`` the version of the build / source tree. Refuses
    (NEEDS_HUMAN) only when a value cannot be determined, naming the flag to pass.
    """
    from vra import build as build_mod
    from vra.deploy import deployed_vams_version
    current = ctx.ledger.current_deploy(env) or {}
    out: dict[str, Any] = {"history": bool(current)}
    build_id, build_src = args.get("build"), "--build" if args.get("build") else None
    if not build_id and not args.get("source"):
        if current.get("build"):
            build_id, build_src = current["build"], "deploy history"
        else:
            build_id = newest_build_for(ctx, env)
            build_src = f"newest build in the home with a synth for {env}" if build_id else None
    tree_version = None
    if args.get("source"):
        root = Path(args["source"])
        if not root.is_dir():
            raise UsageError(f"--source {root} is not a directory (pass <VAMS tree>/infra/deploymentDataMigration)")
        tree_version, tree_src = source_tree_version(root), f"infra/config/config.ts next to {root}"
        out["source"] = str(root)
    elif build_id:
        root = build_mod.build_dir(ctx, build_id) / "migrations"
        tree_version, tree_src = build_mod.load_manifest(ctx, build_id).get("vamsVersion"), f"build {build_id}"
        out.update({"build": build_id, "buildSource": build_src})
    else:
        raise NeedsHuman(f"{env} has no vra deploy history and the home has no build with a synth for it; pass "
                         f"--build <build id> or --source <VAMS tree>/infra/deploymentDataMigration",
                         {"flags": ["--build", "--source"]})
    out["root"] = str(root)

    if args.get("to"):
        to, to_src = args["to"], "--to"
    elif tree_version:
        to, to_src = tree_version, tree_src
    elif current.get("vamsVersion"):
        to, to_src = current["vamsVersion"], "deploy history"
    else:
        raise NeedsHuman(f"cannot determine the target VAMS version for {env} ({tree_src} names none); pass --to "
                         f"X.Y.Z", {"flags": ["--to"]})

    if args.get("from"):
        frm, frm_src = args["from"], "--from"
    elif current:
        frm, frm_src = current.get("previousVamsVersion"), "deploy history"
        if not frm:
            raise NeedsHuman(f"the vra deploy history of {env} records no previous VAMS version (its first vra deploy "
                             f"was a fresh install or ran its migrations itself); pass --from X.Y.Z",
                             {"flags": ["--from"]})
    else:
        try:
            frm, frm_src = deployed_vams_version(ctx, env)
        except NeedsHuman as exc:
            raise NeedsHuman(f"{exc.message}; pass --from X.Y.Z", {**exc.detail, "flags": ["--from"]}) from exc
        if not frm:
            raise NeedsHuman(f"{env} has no vra deploy history and no deployed core stack "
                             f"({ctx.ledger.core_stack_name(env)}); nothing to migrate. Pass --from X.Y.Z if the "
                             f"data is from another deployment", {"flags": ["--from"]})
    out.update({"from": frm, "fromSource": frm_src, "to": to, "toSource": to_src})
    return out


@workflow("migrate")
def wf_migrate(ctx, run, args):
    env = args["env"]
    run.step("migrate.preflight", lambda sc: permissions.preflight(ctx, env, "migrate"), "preflight migrate")
    rv = run.step("migrate.versions", lambda sc: resolve_versions(ctx, env, args), "resolve versions")
    root, frm, to = Path(rv["root"]), rv["from"], rv["to"]
    dry = bool(args.get("dryRun")) or ctx.dry_run
    # the env lock first, then the gate (as deploy does): nothing changes between the check and the use
    with env_lock(ctx, run, env):
        if not dry:
            # a standalone migration writes env data, so an approval-gated env needs its own approval record
            from vra import approvals

            def gate(sc):
                rec = approvals.check(ctx, env, f"migrate:{frm}-{to}", "migrate", args.get("approval"))
                approvals.record(run, rec)
                return {"approval": rec and rec.get("path")}
            run.step("migrate.gate", gate, "approval gate", revalidate=True)
        res = do_migrate(ctx, run, env, root, frm, to, dry_run_only=bool(args.get("dryRun")) or ctx.dry_run)
        if res["applied"]:
            ctx.ledger.append_history(env, {"action": "migrate", "from": frm, "to": to, "applied": res["applied"],
                                            "runId": run.run_id, "result": "SUCCEEDED"}, dry_run=ctx.dry_run)
        return res
