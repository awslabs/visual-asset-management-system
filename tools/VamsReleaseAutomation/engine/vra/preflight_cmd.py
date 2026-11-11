# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra preflight --env E [--workflow W] | --all`: IAM simulation as a resumable run."""

from __future__ import annotations

from vra import iam, permissions
from vra.errors import BlockedOnIT, PermissionDenied
from vra.runner import handle_permission, workflow

# workflows simulated by `preflight --all` per env (data-copy only for envs that accept copies)
DEFAULT_WORKFLOWS = ["deploy", "backup", "restore", "migrate", "test", "build", "rollback"]


def workflows_for(ctx, env: str) -> list[str]:
    e = ctx.ledger.env(env)
    wfs = list(DEFAULT_WORKFLOWS)
    if (e.get("dataCopy") or {}).get("allowedSources"):
        wfs.append("data-copy-target")
    if any(env in (ctx.ledger.env(o).get("dataCopy") or {}).get("allowedSources", [])
           for o in ctx.ledger.env_names() if o != env):
        wfs.append("data-copy-source")
    return wfs


@workflow("preflight")
def wf_preflight(ctx, run, args):
    envs = args.get("envs") or ([args["env"]] if args.get("env") else ctx.ledger.env_names())
    results = {}
    gaps: list[str] = []
    for env in envs:
        if iam.needs_package(iam.settings(ctx.ledger.env(env))):
            # strict-IAM envs: the IT role package is generated, applied and current (exit 10 hands it over)
            sid = f"preflight.{env}.iam"
            try:
                results[sid] = run.step(sid, lambda sc, env=env, sid=sid: iam.gate(ctx, run, env, sid),
                                        f"IAM package of {env}", revalidate=True)
            except BlockedOnIT as exc:
                if args.get("failFast"):
                    raise
                gaps.append(exc.detail.get("itRequest"))
                results[sid] = {"blocked": exc.message, "itRequest": exc.detail.get("itRequest")}
        wfs = [args["workflow"]] if args.get("workflow") else workflows_for(ctx, env)
        for wf in wfs:
            role = "dataCopy" if wf.startswith("data-copy") else None

            def _one(sc, env=env, wf=wf, role=role):
                return permissions.preflight(ctx, env, wf, role=role)
            sid = f"preflight.{env}.{wf}"
            try:
                results[sid] = run.step(sid, _one, f"simulate {wf} on {env}")
            except PermissionDenied as exc:
                if args.get("failFast"):
                    raise
                # one IT request per failing (env, workflow) so `vra resume` re-simulates each precisely
                req = handle_permission(ctx, run, sid, exc)
                gaps.append(req["id"])
                results[sid] = {"denied": len(exc.denials), "itRequest": req["id"]}
    run.state["result"] = results
    run.save()
    if gaps:
        raise BlockedOnIT(f"preflight found missing permissions; IT requests: {', '.join(gaps)}", {"itRequests": gaps})
    return results
