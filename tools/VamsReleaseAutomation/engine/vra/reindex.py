# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenSearch reindex: when a deploy needs one, and running it with a confirmed result.

Decision (``decide``) for a deploy from VAMS ``frm`` to ``to``:

- ``data/upgrade-matrix.yaml`` ``versions`` entries with ``frm < version <= to``: ``required`` or ``recommended``,
  with the release-note reason; an entry ``coveredBy`` a migration step that ran in this deploy is already done;
- the ``app.openSearch`` settings of the deployed build against the new one (search on or off, serverless versus
  provisioned) and the index names of the two synthesized assemblies: a change makes it ``required``.

``required`` runs the reindexer after the migrations and before the smoke test; ``recommended`` is a report note.
Skipped, with a note, when search is off in the new build, when the new build's
``app.openSearch.reindexOnCdkDeploy`` makes VAMS reindex during ``cdk deploy`` itself, or when a migration that
reindexes (v2.5_to_v2.6) ran in this deploy.

Run (``run``): ``lambda`` mode invokes the deployed reindexer synchronously (RequestResponse, read timeout above the
Lambda's 15 minutes) and reads ``FunctionError`` and the body's ``failures``; ``direct`` mode runs the bundle's
``tools/reindex_utility.py --mode direct`` locally (no 15-minute limit) with inputs from the deployed resource
names. A result that cannot be confirmed is ``unverified``, an error ``failed``; neither is success.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from vra import permissions, util
from vra.errors import NeedsHuman, VraError
from vra.runner import env_lock, workflow

LEVELS = {"none": 0, "recommended": 1, "required": 2}
MODES = ("lambda", "direct")
# the reindexer runs at most 15 minutes; wait a little longer for its answer, and never retry (a retry re-runs it)
READ_TIMEOUT = 16 * 60
INDEX_PARAMS = ("/aos/assetIndexName", "/aos/fileIndexName")
TABLE_KEYS = {"asset": "dynamoTables/assetStorage", "buckets": "dynamoTables/s3AssetBucketsStorage",
              "fileMetadata": "dynamoTables/assetFileMetadataStorage"}


def _vt(v: str) -> tuple[int, ...]:
    return util.version_tuple(v)


def matrix_entries(frm: str | None, to: str) -> list[dict]:
    """Upgrade-matrix ``versions`` entries a move from ``frm`` to ``to`` (an upgrade) passes."""
    if not frm or _vt(to) <= _vt(frm):
        return []
    from vra import migrate
    vs = (migrate.matrix().get("versions") or {})
    return [{"version": v, **(vs[v] or {})} for v in sorted(vs, key=_vt) if _vt(frm) < _vt(v) <= _vt(to)]


def reindexing_migrations() -> set[str]:
    """Migration transitions whose steps include the reindex (upgrade-matrix ``steps``)."""
    from vra import migrate
    return {k for k, v in (migrate.matrix().get("transitions") or {}).items() if "reindex" in ((v or {}).get("steps")
                                                                                              or [])}


def search_config(cfg: dict | None) -> dict | None:
    """``{"mode": off|serverless|provisioned, "reindexOnCdkDeploy": bool}`` of a VAMS config.json, None if unknown."""
    if not isinstance(cfg, dict) or not isinstance((cfg.get("app") or {}).get("openSearch"), dict):
        return None
    os_ = cfg["app"]["openSearch"]
    if (os_.get("useProvisioned") or {}).get("enabled"):
        mode = "provisioned"
    elif (os_.get("useServerless") or {}).get("enabled"):
        mode = "serverless"
    else:
        mode = "off"
    return {"mode": mode, "reindexOnCdkDeploy": bool(os_.get("reindexOnCdkDeploy"))}


def index_names(asm: Path | None) -> dict:
    """The asset and file index names a synthesized assembly writes to SSM (``.../aos/{asset,file}IndexName``)."""
    out: dict[str, str] = {}
    if not asm or not Path(asm).is_dir():
        return out
    for tf in sorted(Path(asm).glob("*.template.json")):
        try:
            res = (util.read_json(tf).get("Resources") or {}).values()
        except Exception:  # noqa: BLE001 - an unreadable template names no index
            continue
        for r in res:
            if r.get("Type") != "AWS::SSM::Parameter":
                continue
            p = r.get("Properties") or {}
            name, value = p.get("Name"), p.get("Value")
            if isinstance(name, str) and isinstance(value, str):
                for suffix in INDEX_PARAMS:
                    if name.endswith(suffix):
                        out[suffix.rsplit("/", 1)[1]] = value
    return out


def decide(frm: str | None, to: str, old_cfg: dict | None, new_cfg: dict | None, old_index: dict | None = None,
           new_index: dict | None = None, applied: list[str] | None = None) -> dict:
    """Whether a deploy from ``frm`` to ``to`` needs a reindex. See the module docstring."""
    applied = list(applied or [])
    reasons: list[dict] = []
    notes: list[str] = []
    new_s, old_s = search_config(new_cfg), search_config(old_cfg)
    level = "none"

    def raise_to(lvl: str, why: str, source: str) -> None:
        nonlocal level
        reasons.append({"level": lvl, "reason": why, "source": source})
        if LEVELS[lvl] > LEVELS[level]:
            level = lvl

    for ent in matrix_entries(frm, to):
        lvl = ent.get("reindex") or "none"
        if lvl == "none":
            continue
        if ent.get("coveredBy") and ent["coveredBy"] in applied:
            notes.append(f"{ent['version']}: reindex done by migration {ent['coveredBy']} in this deploy")
            continue
        raise_to(lvl, f"VAMS {ent['version']}: {ent.get('reason') or 'reindex after the upgrade'}", "upgrade-matrix")
    if new_s and old_s:
        if old_s["mode"] != new_s["mode"]:
            raise_to("required", f"search changed from {old_s['mode']} to {new_s['mode']} (app.openSearch)",
                     "config")
    elif new_s and new_s["mode"] != "off":
        notes.append("the deployed build's search configuration is not known (no effective config in its bundle); "
                     "a search configuration change cannot be detected")
    if old_index and new_index and old_index != new_index:
        raise_to("required", f"index names changed ({old_index} -> {new_index})", "index-names")
    skip = None
    if new_s and new_s["mode"] == "off":
        skip = "search is off in the new build (app.openSearch)"
    elif new_s and new_s["reindexOnCdkDeploy"] and level != "none":
        skip = "app.openSearch.reindexOnCdkDeploy is true: VAMS reindexed during cdk deploy"
    elif level != "none" and set(applied) & reindexing_migrations():
        skip = f"migration {sorted(set(applied) & reindexing_migrations())[0]} reindexed in this deploy"
    return {"decision": level, "reasons": reasons, "skip": skip, "notes": notes,
            "search": new_s, "previousSearch": old_s}


# ===================================================================== run
def settings(e: dict) -> dict:
    r = e.get("reindex") or {}
    return {"mode": r.get("mode") or "lambda"}


def _parse_lambda_result(fn: str, resp: dict) -> dict:
    out: dict[str, Any] = {"mode": "lambda", "function": fn, "statusCode": resp.get("StatusCode")}
    body_raw = resp.get("Payload")
    text = body_raw.read().decode("utf-8", "replace") if hasattr(body_raw, "read") else (body_raw or "")
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if resp.get("FunctionError"):
        out.update({"status": "failed", "error": f"{resp['FunctionError']}: {util.redact(str(text))[:500]}"})
        return out
    try:
        payload = json.loads(text) if text else {}
        body = payload.get("body") if isinstance(payload, dict) else None
        body = json.loads(body) if isinstance(body, str) else (body or {})
    except (ValueError, TypeError):
        out.update({"status": "unverified", "error": "the reindexer's answer is not JSON"})
        return out
    code = payload.get("statusCode") if isinstance(payload, dict) else None
    if code is not None and int(code) != 200:
        out.update({"status": "failed", "error": str(body.get("error") or body)[:500]})
    elif body.get("failures"):
        out.update({"status": "failed", "error": "; ".join(map(str, body["failures"]))[:500]})
    elif code is None and not body:
        out.update({"status": "unverified", "error": "the reindexer returned no result"})
    else:
        out["status"] = "succeeded"
        res = body.get("results") or {}
        out["counts"] = {k: {kk: vv for kk, vv in (v or {}).items() if isinstance(vv, (int, float))}
                         for k, v in res.items() if isinstance(v, dict)}
    return out


def lambda_reindex(ctx, env: str, role: str = "deploy", clear_indexes: bool = True,
                   base: str | None = None) -> dict:
    """Invoke the deployed reindexer synchronously and confirm its result."""
    from botocore.config import Config
    from botocore.exceptions import ReadTimeoutError
    from vra import vams_resources
    names = vams_resources.discover(ctx, env, role, base)
    fn = names["lambdas"].get(vams_resources.REINDEXER_KEY)
    if not fn:
        return {"mode": "lambda", "status": "skipped", "note": "no reindexer Lambda published under the SSM prefix"}
    lam = ctx.aws.client(env, "lambda", role, config=Config(read_timeout=READ_TIMEOUT, connect_timeout=30,
                                                            retries={"max_attempts": 0}))
    # same direct-invoke payload the v2.5_to_v2.6 migration sends to crReindexer
    payload = {"operation": "both", "dry_run": False, "clear_indexes": clear_indexes}
    try:
        resp = lam.invoke(FunctionName=fn, InvocationType="RequestResponse", Payload=json.dumps(payload).encode())
    except ReadTimeoutError:
        return {"mode": "lambda", "function": fn, "status": "unverified",
                "error": f"no answer within {READ_TIMEOUT // 60} minutes; the reindexer may still be running or may "
                         f"have hit its 15-minute limit (use --mode direct for a large repository)"}
    except Exception as exc:  # noqa: BLE001
        if permissions.is_denied(exc):
            from vra.restore import _wrap
            _wrap(exc, "lambda", env, fn)
        raise
    return _parse_lambda_result(fn, resp)


def direct_inputs(ctx, env: str, names: dict, cfg: dict | None, base: str | None = None) -> list[str]:
    """Arguments of ``reindex_utility.py --mode direct`` from the deployed resource names (its README's inputs)."""
    tables = {k: names["tables"].get(v) for k, v in TABLE_KEYS.items()}
    missing = [TABLE_KEYS[k] for k, v in tables.items() if not v]
    if missing:
        raise NeedsHuman(f"direct reindex of {env}: the deployed resource names lack {missing}")
    prefix = names["prefix"]
    aos = prefix[: -len("/resourceNames")] if prefix.endswith("/resourceNames") else prefix
    s = search_config(cfg) or {}
    return ["--asset-storage-table-name", tables["asset"], "--s3-asset-buckets-storage-table-name",
            tables["buckets"], "--asset-file-metadata-storage-table-name", tables["fileMetadata"],
            "--opensearch-asset-index-ssm-param", f"{aos}/aos/assetIndexName",
            "--opensearch-file-index-ssm-param", f"{aos}/aos/fileIndexName",
            "--opensearch-endpoint-ssm-param", f"{aos}/aos/endPoint",
            "--opensearch-type", "provisioned" if s.get("mode") == "provisioned" else "serverless",
            "--region", ctx.ledger.env(env)["region"]]


def _direct_argv(ctx, env: str, build_id: str, clear_indexes: bool, base: str | None, cfg: dict | None):
    """The direct-mode command line, after the bundle and option checks (exit 20 when it cannot run)."""
    from vra import build as build_mod, vams_resources
    bdir = build_mod.build_dir(ctx, build_id)
    script = bdir / "migrations" / "tools" / "reindex_utility.py"
    backend = bdir / "reindexer" / "backend" / "backend"
    if not script.is_file() or not backend.is_dir():
        raise NeedsHuman(f"build {build_id} carries no reindex utility and backend source (bundles from before this "
                         f"engine version); rebuild, or use reindex.mode lambda")
    if clear_indexes and (search_config(cfg) or {}).get("mode") == "provisioned":
        raise NeedsHuman("direct mode cannot clear a provisioned domain's indexes (the endpoint is inside the VPC); "
                         "reindex without --clear-indexes, or use --mode lambda")
    names = vams_resources.discover(ctx, env, "deploy", base)
    argv = [os.environ.get("VRA_MIGRATION_PYTHON", sys.executable), str(script), "--mode", "direct",
            "--operation", "both", "--backend-path", str(backend), *direct_inputs(ctx, env, names, cfg, base)]
    if clear_indexes:
        argv.append("--clear-indexes")
    return script, argv


def direct_reindex(ctx, env: str, build_id: str, sc, clear_indexes: bool = False, base: str | None = None,
                   cfg: dict | None = None) -> dict:
    """Run the bundle's reindex utility in direct mode (local process, no 15-minute limit)."""
    script, argv = _direct_argv(ctx, env, build_id, clear_indexes, base, cfg)
    child_env = ctx.aws.subprocess_env(env, "deploy")
    p = ctx.shell(sc.log_path).run(argv, cwd=str(script.parent), env=child_env, check=False, mutating=True,
                                   input_text="", timeout=24 * 3600)
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        denials = permissions.classify_text(out, "reindex")
        if denials:
            from vra.errors import PermissionDenied
            raise PermissionDenied(f"direct reindex of {env} was denied", denials, env=env)
        return {"mode": "direct", "status": "failed", "error": f"exit {p.returncode}",
                "tail": util.redact(out[-1500:])}
    return {"mode": "direct", "status": "succeeded"}


def run(ctx, env: str, sc, mode: str | None = None, clear_indexes: bool = True, build_id: str | None = None,
        base: str | None = None, cfg: dict | None = None) -> dict:
    """Reindex ``env`` in the env's ``reindex.mode`` (or ``mode``). Never reports success it cannot confirm."""
    mode = mode or settings(ctx.ledger.env(env))["mode"]
    if mode not in MODES:
        raise VraError(f"reindex mode {mode!r} is not one of {MODES}")
    if ctx.dry_run:
        return {"dryRun": True, "mode": mode, "clearIndexes": clear_indexes}
    if mode == "direct":
        build_id = build_id or (ctx.ledger.current_deploy(env) or {}).get("build")
        if not build_id:
            raise NeedsHuman(f"direct reindex of {env} needs a deployed build bundle (none in the history)")
        return direct_reindex(ctx, env, build_id, sc, clear_indexes, base, cfg)
    return lambda_reindex(ctx, env, "deploy", clear_indexes, base)


def preview(ctx, env: str, mode: str, clear_indexes: bool = False, build_id: str | None = None,
            base: str | None = None, cfg: dict | None = None) -> dict:
    """What ``run`` would do, resolved read-only (the dry run of ``vra reindex``): the reindexer Lambda, or the
    direct-mode bundle and inputs. Stops (exit 20) where the real run would stop."""
    from vra import vams_resources
    out: dict[str, Any] = {"dryRun": True, "mode": mode, "clearIndexes": clear_indexes}
    if mode == "direct":
        if not build_id:
            raise NeedsHuman(f"direct reindex of {env} needs a deployed build bundle (none in the history)")
        script, argv = _direct_argv(ctx, env, build_id, clear_indexes, base, cfg)
        out.update({"build": build_id, "script": str(script), "argv": argv[1:]})
        return out
    names = vams_resources.discover(ctx, env, "deploy", base)
    fn = names["lambdas"].get(vams_resources.REINDEXER_KEY)
    out.update({"function": fn} if fn else {"status": "skipped",
                                            "note": "no reindexer Lambda published under the SSM prefix"})
    return out


def note_result(run_, env: str, res: dict, what: str) -> dict:
    """Add a report note for a reindex that did not succeed (failed, unverified)."""
    st = (res or {}).get("status")
    if st in ("failed", "unverified"):
        run_.state.setdefault("notes", []).append(
            f"{env}: {what} reindex {st}: {res.get('error') or ''} Re-run it with `vra reindex --env {env}` "
            f"(or --mode direct for a large repository)".strip())
        run_.save()
    return res


# ===================================================================== bundle configs
def bundle_config(ctx, build_id: str | None, target: str) -> dict | None:
    """The effective VAMS config.json the bundle synthesized ``target`` with (``effective-config/<target>.json``)."""
    if not build_id:
        return None
    from vra import build as build_mod
    p = build_mod.build_dir(ctx, build_id) / "effective-config" / f"{target}.json"
    try:
        return util.read_json(p) if p.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def deployed_config(ctx, env: str) -> dict | None:
    """The effective config of the env's deployed build (its A/B slot's target), else the ledger overlay."""
    from vra import build as build_mod
    cur = ctx.ledger.current_deploy(env) or {}
    target = build_mod.target_name(env, cur["slot"]) if cur.get("slot") else env
    return bundle_config(ctx, cur.get("build"), target) or ctx.ledger.vams_config(env)


def bundle_index_names(ctx, build_id: str | None, target: str) -> dict:
    if not build_id:
        return {}
    from vra import build as build_mod
    return index_names(build_mod.build_dir(ctx, build_id) / "cdk.out" / target)


def post_deploy(ctx, run_, env: str, sc, frm: str | None, to: str, new_build: str, target: str,
                old_build: str | None, old_target: str | None, applied: list[str], base: str | None = None,
                already_reindexed: str | None = None) -> dict:
    """The deploy's reindex step: decide, then run a ``required`` reindex; ``recommended`` becomes a report note.
    ``already_reindexed`` names a step of this deploy that reindexed the target already (the A/B slot copy)."""
    new_cfg = bundle_config(ctx, new_build, target)
    old_cfg = bundle_config(ctx, old_build, old_target or target)
    dec = decide(frm, to, old_cfg, new_cfg, bundle_index_names(ctx, old_build, old_target or target),
                 bundle_index_names(ctx, new_build, target), applied)
    if already_reindexed and dec["decision"] != "none" and not dec["skip"]:
        dec["skip"] = already_reindexed
    out = {**dec}
    why = "; ".join(r["reason"] for r in dec["reasons"])
    if dec["skip"] or dec["decision"] == "none":
        if dec["decision"] != "none" and dec["skip"]:
            run_.state.setdefault("notes", []).append(f"{env}: reindex {dec['decision']} ({why}), skipped: "
                                                      f"{dec['skip']}")
            run_.save()
        out["ran"] = False
        return out
    if dec["decision"] == "recommended":
        run_.state.setdefault("notes", []).append(
            f"{env}: a search reindex is recommended ({why}); run `vra reindex --env {env}` when convenient")
        run_.save()
        out["ran"] = False
        return out
    res = run(ctx, env, sc, clear_indexes=True, build_id=new_build, base=base, cfg=new_cfg)
    out.update({"ran": True, "result": note_result(run_, env, res, "the post-deploy")})
    return out


# ===================================================================== vra reindex
@workflow("reindex")
def wf_reindex(ctx, run_, args):
    env = args["env"]
    mode = args.get("mode") or settings(ctx.ledger.env(env))["mode"]
    run_.step("reindex.preflight", lambda sc: permissions.preflight(ctx, env, "reindex"), "preflight reindex")
    with env_lock(ctx, run_, env):
        from vra import approvals

        def gate(sc):
            rec = approvals.check(ctx, env, f"reindex:{env}", "reindex", args.get("approval"))
            approvals.record(run_, rec)
            return {"approval": rec and rec.get("path")}
        run_.step("reindex.gate", gate, "approval gate", revalidate=True)
        cur = ctx.ledger.current_deploy(env) or {}
        cfg = deployed_config(ctx, env)
        s = search_config(cfg)
        if s and s["mode"] == "off":
            return {"env": env, "skipped": "search is off in the deployed configuration (app.openSearch)"}

        def _run(sc):
            if ctx.dry_run:
                return preview(ctx, env, mode, bool(args.get("clearIndexes")), cur.get("build"), cfg=cfg)
            res = run(ctx, env, sc, mode=mode, clear_indexes=bool(args.get("clearIndexes")),
                      build_id=cur.get("build"), cfg=cfg)
            if res.get("status") == "failed":
                raise VraError(f"reindex of {env} failed: {res.get('error')}", res)
            if res.get("status") == "unverified":
                raise NeedsHuman(f"reindex of {env} could not be confirmed: {res.get('error')}; check the reindexer's "
                                 f"logs, or re-run with --mode direct", res)
            return res
        res = run_.step("reindex.run", _run, f"OpenSearch reindex ({mode})")
        if not ctx.dry_run:
            ctx.ledger.append_history(env, {"action": "reindex", "mode": mode, "result": "SUCCEEDED",
                                            "clearIndexes": bool(args.get("clearIndexes")), "runId": run_.run_id})
        return {"env": env, **res}


__all__ = ["decide", "index_names", "lambda_reindex", "matrix_entries", "post_deploy", "run", "search_config"]
