# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra pipeline ...` command group (registered by vra.cli with one line; logic in vra.pipeline)."""

from __future__ import annotations

import argparse
from typing import Callable

from vra import errors, runner


def register(sub, globals_fn: Callable[[argparse.ArgumentParser, bool], None]) -> argparse.ArgumentParser:
    """Add `vra pipeline` to the top-level subparsers. ``globals_fn`` is vra.cli._globals."""
    s = sub.add_parser("pipeline", help="CodePipeline lane: plan, deploy, status, sync-ledger, push-source "
                                         "(automation.mode hybrid / pipeline / pipeline+actions)")
    globals_fn(s, True)
    s.set_defaults(handler=dispatch)
    ps = s.add_subparsers(dest="pipeline_cmd", required=True)

    x = ps.add_parser("plan", help="render the pipeline from `vra ledger graph`, synthesize, diff against the "
                                   "deployed stack and write the IT package for the pipeline's own roles")
    globals_fn(x, True)
    x.add_argument("--offline", action="store_true",
                   help="no tools-account reads (no diff, no first-deploy check); such a plan cannot be deployed")

    x = ps.add_parser("deploy", help="deploy the planned pipeline stack into the tools account (human-only for the "
                                     "first deploy, IAM changes and removed approvals)")
    globals_fn(x, True)
    x.add_argument("--plan", dest="plan_id", help="plan id (default: the latest plan)")
    x.add_argument("--approval", help="approval record (subject human:pipeline-deploy) when no terminal is available")

    x = ps.add_parser("status", help="stack, stage states, pending approvals; is the topology in step with the ledger")
    globals_fn(x, True)

    x = ps.add_parser("sync-ledger", help="plan again when the ledger's topology moved away from the deployed "
                                          "pipeline (exit 20 until it is deployed)")
    globals_fn(x, True)
    x.add_argument("--deploy", dest="do_deploy", action="store_true", help="deploy the new plan (same gates)")
    x.add_argument("--approval")

    x = ps.add_parser("push-source", help="S3 source fed by vra: upload the fork branch (or --package) when it "
                                          "changed and start the pipeline")
    globals_fn(x, True)
    x.add_argument("--lane", choices=["release", "preview"], default="release")
    x.add_argument("--package", action="store_true", help="upload this vams-release-automation package instead")
    x.add_argument("--bucket")
    x.add_argument("--key")
    x.add_argument("--pipeline", dest="pipeline_name", help="pipeline to start after an upload")
    return s


def dispatch(ctx, ns) -> runner.Outcome:
    from vra import pipeline
    c = ns.pipeline_cmd
    approval = getattr(ns, "approval", None) or getattr(ns, "human_approval", None)
    if c == "plan":
        rec = pipeline.plan(ctx, offline=ns.offline)
        who = "human-only: " + "; ".join(rec["humanOnly"]) if rec["humanOnly"] else "an agent may deploy it"
        return runner.Outcome(None, errors.OK, rec, f"plan {rec['planId']} ({who}); IT package: "
                                                    f"{rec['itPackage']['dir']}/IT-ROLES.md")
    if c == "deploy":
        res = pipeline.deploy(ctx, ns.plan_id, approval)
        return runner.Outcome(None, errors.OK, res, f"pipeline stack {res['stackName']} deployed from {res['planId']}"
                                                    + (" (dry-run)" if res["dryRun"] else ""))
    if c == "status":
        st = pipeline.status(ctx)
        msg = st.get("message") or ("in step with the ledger" if st.get("inSync") else
                                    "NOT in step with the ledger: run `vra pipeline sync-ledger`"
                                    if st.get("deployed") else "not deployed")
        return runner.Outcome(None, errors.OK, st, msg)
    if c == "sync-ledger":
        code, res, msg = pipeline.sync_ledger(ctx, ns.do_deploy, approval)
        return runner.Outcome(None, code, res, msg)
    if c == "push-source":
        res = pipeline.push_source(ctx, ns.lane, ns.bucket, ns.key, ns.pipeline_name, package=ns.package)
        return runner.Outcome(None, errors.OK, res, res.get("message") or f"uploaded {res['revision']}")
    raise errors.UsageError(f"unknown pipeline command {c}")
