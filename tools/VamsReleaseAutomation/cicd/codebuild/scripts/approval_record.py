#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Turn a CI platform's own approval into a vra approval record.

One writer for every CI lane. Python standard library only (runs before the vra tool env exists).

  approval_record.py codepipeline --executions FILE --stage S --action A --env E --build B --out OUT
      FILE = `aws codepipeline list-action-executions --pipeline-name P
              --filter pipelineExecutionId=<VRA_PIPELINE_EXECUTION_ID>` (JSON). Only the approval action of
      THIS execution counts; the approver is its `updatedBy` (the IAM identity that approved).
  approval_record.py github --approvals FILE --env E --build B --out OUT
      FILE = `gh api repos/<repo>/actions/runs/<run_id>/approvals` (JSON). Environment reviewers of THIS run.
  approval_record.py gitlab --env E --build B --out OUT [--deployment FILE]
      Reads the protected-environment deployment approvals of THIS job's deployment from the GitLab API
      (VRA_GITLAB_API_TOKEN, read_api) unless --deployment gives the deployment JSON. The approver is a user
      who APPROVED the deployment, never the user who played the job (GITLAB_USER_LOGIN).
  approval_record.py generic --approver A --approved-at T --env E --build B --out OUT [--comment C]
      Any other CI (VRA_CI_PROVIDER=generic): the CI's own approval step (Jenkins `input` submitter, Azure
      Pipelines environment approval) names the approver. The record is bound to VRA_RUN_REF and records
      VRA_APPROVAL_REF (the CI's id of that approval); an approver equal to VRA_INITIATOR is refused.

Every record is bound to the CI run (`source`, the same string vra.identity derives for the run; for generic
CI exactly $VRA_RUN_REF) and expires after --hours (default 24). Scope: --scope (default promote; a gated env
deployed without promotion needs deploy). The approval comment may add
`scope: emergency,accept-drift,allow-replacements` (only those three). vra rejects a record whose source is
another run, whose approver is the initiator, or that an earlier run already consumed.

Human-only actions run from CI (`vra unlock`, `vra resume --takeover` / `--confirm`, `--abort-workflow-executions`,
`--writes-stopped`, `--allow-missing-scanners`, ...): add `--human <action>` to any writer. The record's subject
(`build`) is `human:<action>`, its scope exactly `[<action>]` (no comment scopes), --build is not needed, and it
expires after --hours (default 2). Pass it to vra with `--human-approval <record>` (run-vra.sh:
VRA_HUMAN_APPROVAL=<record>). Single use, like every approval.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: urllib copies the request headers, the token included, to the redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def no_redirect_opener() -> urllib.request.OpenerDirector:
    """An opener for token-bearing requests: a 3xx answer is an HTTPError, never followed."""
    return urllib.request.build_opener(_NoRedirect)


def run_ref(provider: str, environ=None) -> str:
    """Must equal vra.identity.resolve(...).run_ref for the same job."""
    env = os.environ if environ is None else environ
    if provider == "codepipeline":
        return f"codepipeline:{env.get('VRA_PIPELINE_NAME', '')}/{env.get('VRA_PIPELINE_EXECUTION_ID', '')}"
    if provider == "github":
        return f"github:{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{env.get('GITHUB_RUN_ID', '')}"
    if provider == "gitlab":
        return f"gitlab:{env.get('CI_PROJECT_PATH', '')}/pipelines/{env.get('CI_PIPELINE_ID', '')}"
    if provider == "generic":
        return env.get("VRA_RUN_REF", "")
    raise ValueError(provider)


# Scopes an approver may add in the approval comment ("scope: emergency"). Restores, data copies,
# standalone migrations and rollbacks are operator workflows with their own records, never added from a CI
# approval; that includes restore-auth-tables, the scope a restore with --include-auth-tables needs on a gated env.
COMMENT_SCOPES = ("emergency", "accept-drift", "allow-replacements")
BASE_SCOPES = ("deploy", "promote")
OPERATOR_SCOPES = ("restore", "data-copy", "migrate", "rollback", "reindex", "restore-auth-tables")
# Human-only actions (engine/vra/human.py HUMAN_ONLY): record subject human:<action>, scope [<action>].
HUMAN_ACTIONS = ("confirm", "allow-missing-scanners", "writes-stopped", "unlock", "takeover",
                 "abort-workflow-executions", "ledger-import", "upgrade-config-write", "setup-force", "home-migrate",
                 "iam-apply")
# Human-only only in some cases, decided by the engine (not in HUMAN_ONLY, never blocked by the agent guard):
# `vra pipeline deploy` of a first deploy, IAM changes or removed approvals (engine/vra/pipeline.py).
CONDITIONAL_HUMAN_ACTIONS = ("pipeline-deploy",)
# Every scope a record may carry (engine/vra/data/schemas/approval.schema.json scope enum).
ALL_SCOPES = BASE_SCOPES + COMMENT_SCOPES + OPERATOR_SCOPES + HUMAN_ACTIONS + CONDITIONAL_HUMAN_ACTIONS


def _comment_scopes(comment: str) -> list[str]:
    m = re.search(r"scope\s*:\s*([A-Za-z0-9 ,-]+)", comment or "", re.I)
    if not m:
        return []
    asked = [s.strip().lower() for s in m.group(1).split(",") if s.strip()]
    bad = [s for s in asked if s not in COMMENT_SCOPES + BASE_SCOPES]
    if any(s in OPERATOR_SCOPES for s in bad):
        raise Refused(f"approval comment asks for scope(s) {bad}: restores (also --include-auth-tables, scope "
                      f"restore-auth-tables), data copies and rollbacks are operator workflows with their own "
                      f"records; a CI approval may add only {list(COMMENT_SCOPES)}")
    if bad:
        raise Refused(f"approval comment asks for scope(s) {bad}; a CI approval may add only {list(COMMENT_SCOPES)}")
    return [s for s in asked if s in COMMENT_SCOPES]


def _parse_comment(comment: str) -> tuple[str, str]:
    ticket = ""
    m = re.search(r"ticket\s*:\s*([^;\s]+)", comment or "", re.I)
    if m:
        ticket = m.group(1)
    notes = comment or ""
    m = re.search(r"notes\s*:\s*(.*)$", notes, re.I)
    if m:
        notes = m.group(1).strip()
    return ticket, notes


def _iso(value) -> str:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _record(env: str, build: str, approver: str, approved_at: str, comment: str, source: str, binding: dict,
            hours: int, now: dt.datetime | None = None, scope: str = "promote", human: str | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    if not source:
        raise Refused("the CI run reference is empty; the approval cannot be bound to this run")
    ticket, notes = _parse_comment(comment)
    if human:
        if human not in HUMAN_ACTIONS + CONDITIONAL_HUMAN_ACTIONS:
            raise Refused(f"--human must be one of {list(HUMAN_ACTIONS + CONDITIONAL_HUMAN_ACTIONS)}")
        if re.search(r"scope\s*:", comment or "", re.I):
            raise Refused(f"a human:{human} record covers exactly [{human}]; remove the scope from the comment")
        build, scopes = f"human:{human}", [human]
    else:
        if scope not in BASE_SCOPES:
            raise Refused(f"--scope must be one of {list(BASE_SCOPES)}")
        if not build:
            raise Refused("--build is required (the build the approval is for)")
        scopes = [scope, *[s for s in _comment_scopes(comment) if s != scope]]
    return {"build": build, "env": env, "approver": approver, "approvedAt": approved_at,
            "expiresAt": (now + dt.timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "scope": scopes, "ticket": ticket or None, "notes": notes or None, "source": source,
            "binding": binding}


class Refused(Exception):
    pass


def from_codepipeline(executions: dict, stage: str, action: str, env: str, build: str, environ=None,
                      hours: int = 24, now=None, scope: str = "promote", human: str | None = None) -> dict:
    environ = os.environ if environ is None else environ
    exe = environ.get("VRA_PIPELINE_EXECUTION_ID")
    if not exe:
        raise Refused("VRA_PIPELINE_EXECUTION_ID is not set; the approval cannot be bound to this execution")
    found = [d for d in executions.get("actionExecutionDetails", [])
             if d.get("pipelineExecutionId") == exe and d.get("stageName") == stage and d.get("actionName") == action]
    if not found:
        raise Refused(f"no execution of approval action {stage}/{action} in pipeline execution {exe}")
    d = sorted(found, key=lambda x: str(x.get("lastUpdateTime", "")))[-1]
    if d.get("status") != "Succeeded":
        raise Refused(f"approval {stage}/{action} of execution {exe} is {d.get('status')}, not Succeeded")
    approver = d.get("updatedBy") or ""
    if not approver:
        raise Refused(f"approval {stage}/{action} of execution {exe} does not name who approved (updatedBy)")
    summary = (((d.get("output") or {}).get("executionResult") or {}).get("externalExecutionSummary")) or ""
    binding = {"pipelineExecutionId": exe, "actionExecutionId": d.get("actionExecutionId"),
               "codebuildBuildId": environ.get("CODEBUILD_BUILD_ID")}
    return _record(env, build, approver, _iso(d.get("lastUpdateTime", "")), summary, run_ref("codepipeline", environ),
                   binding, hours, now, scope, human)


def from_github(approvals: list, env: str, build: str, environ=None, hours: int = 24, now=None,
                scope: str = "promote", human: str | None = None) -> dict:
    environ = os.environ if environ is None else environ
    initiator = environ.get("GITHUB_TRIGGERING_ACTOR") or environ.get("GITHUB_ACTOR") or ""
    ok = [a for a in approvals if a.get("state") == "approved"
          and any(e.get("name") == env for e in a.get("environments", []))]
    if not ok:
        raise Refused(f"no approval for environment {env} on this run")
    others = [a for a in ok if (a.get("user") or {}).get("login", "").lower() != initiator.lower()]
    if not others:
        raise Refused(f"the only approval for {env} is by the run initiator {initiator!r} (separation of duties); "
                      f"enable 'Prevent self-review' on the environment")
    a = others[-1]
    binding = {"runId": environ.get("GITHUB_RUN_ID"), "runAttempt": environ.get("GITHUB_RUN_ATTEMPT")}
    return _record(env, build, a["user"]["login"], a.get("submitted_at", ""), a.get("comment") or "",
                   run_ref("github", environ), binding, hours, now, scope, human)


def from_gitlab(deployment: dict, env: str, build: str, environ=None, hours: int = 24, now=None,
                scope: str = "promote", human: str | None = None) -> dict:
    environ = os.environ if environ is None else environ
    clicker = environ.get("GITLAB_USER_LOGIN") or ""
    approvals = [a for a in deployment.get("approvals", []) or [] if a.get("status") == "approved"]
    if not approvals:
        raise Refused(f"deployment {deployment.get('id')} of {env} has no approvals; configure protected-environment "
                      f"deployment approvals (GitLab Premium) - gated envs are refused in this lane without them")
    others = [a for a in approvals if (a.get("user") or {}).get("username", "").lower() != clicker.lower()]
    if not others:
        raise Refused(f"the only approval of deployment {deployment.get('id')} is by {clicker!r}, who runs the job "
                      f"(separation of duties)")
    a = others[-1]
    binding = {"deploymentId": deployment.get("id"), "jobId": environ.get("CI_JOB_ID"),
               "pipelineId": environ.get("CI_PIPELINE_ID")}
    return _record(env, build, a["user"]["username"], a.get("created_at", ""), a.get("comment") or "",
                   run_ref("gitlab", environ), binding, hours, now, scope, human)


def from_generic(approver: str, approved_at: str, comment: str, env: str, build: str, environ=None,
                 hours: int = 24, now=None, scope: str = "promote", human: str | None = None) -> dict:
    """Approval asserted by a CI without a built-in writer (VRA_CI_PROVIDER=generic); source = $VRA_RUN_REF."""
    environ = os.environ if environ is None else environ
    if environ.get("VRA_CI_PROVIDER") != "generic":
        raise Refused("VRA_CI_PROVIDER is not 'generic'; use the writer of your CI platform")
    approver = (approver or "").strip()
    if not approver:
        raise Refused("--approver is empty: take it from the CI's approval step (who approved), never from input")
    initiator = environ.get("VRA_INITIATOR") or ""
    if initiator and approver.lower() == initiator.lower():
        raise Refused(f"the approver {approver!r} is the run initiator (separation of duties)")
    binding = {"approvalRef": environ.get("VRA_APPROVAL_REF") or None, "runRef": environ.get("VRA_RUN_REF")}
    return _record(env, build, approver, approved_at, comment, run_ref("generic", environ), binding, hours, now,
                   scope, human)


def gitlab_deployment_of_job(environ=None) -> dict:
    """The deployment GitLab created for this job (deployable.id == CI_JOB_ID), with its approvals."""
    env = os.environ if environ is None else environ
    api, project, token = env.get("CI_API_V4_URL"), env.get("CI_PROJECT_ID"), env.get("VRA_GITLAB_API_TOKEN")
    if not (api and project and token):
        raise Refused("CI_API_V4_URL, CI_PROJECT_ID and VRA_GITLAB_API_TOKEN (read_api) are required")

    if not api.startswith("https://"):
        raise Refused("CI_API_V4_URL must be an https:// URL")

    def get(path: str):
        req = urllib.request.Request(f"{api}/projects/{project}{path}", headers={"PRIVATE-TOKEN": token})
        # the URL is checked for https:// above
        # no redirects: the PRIVATE-TOKEN header would go to the redirect target
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with no_redirect_opener().open(req, timeout=30) as r:  # noqa: S310 # nosec B310 - https enforced above
            return json.load(r)
    name = env.get("CI_ENVIRONMENT_NAME") or env.get("VRA_ENV")
    for d in get(f"/deployments?environment={urllib.parse.quote(name)}&order_by=id&sort=desc&per_page=50"):
        if str((d.get("deployable") or {}).get("id")) == str(env.get("CI_JOB_ID")):
            return get(f"/deployments/{d['id']}")
    raise Refused(f"no deployment of environment {name} belongs to job {env.get('CI_JOB_ID')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="kind", required=True)
    for k in ("codepipeline", "github", "gitlab", "generic"):
        s = sub.add_parser(k)
        s.add_argument("--env", required=True)
        s.add_argument("--build", default="", help="the build approved (not with --human)")
        s.add_argument("--out", required=True)
        s.add_argument("--hours", type=int, default=None, help="validity (default 24, with --human 2)")
        s.add_argument("--scope", default="promote", choices=list(BASE_SCOPES),
                       help="promote (default) or deploy (a gated env deployed without promotion)")
        s.add_argument("--human", choices=list(HUMAN_ACTIONS + CONDITIONAL_HUMAN_ACTIONS),
                       help="record for a human-only action: subject human:<action>, scope [<action>]")
        if k == "codepipeline":
            s.add_argument("--executions", required=True)
            s.add_argument("--stage", required=True)
            s.add_argument("--action", required=True)
        elif k == "github":
            s.add_argument("--approvals", required=True)
        elif k == "gitlab":
            s.add_argument("--deployment")
        else:
            s.add_argument("--approver", required=True)
            s.add_argument("--approved-at", default="")
            s.add_argument("--comment", default="")
    ns = ap.parse_args(argv)
    kw = {"hours": ns.hours if ns.hours is not None else (2 if ns.human else 24), "scope": ns.scope,
          "human": ns.human}
    try:
        if ns.kind == "codepipeline":
            rec = from_codepipeline(json.load(open(ns.executions)), ns.stage, ns.action, ns.env, ns.build, **kw)
        elif ns.kind == "github":
            rec = from_github(json.load(open(ns.approvals)), ns.env, ns.build, **kw)
        elif ns.kind == "gitlab":
            dep = json.load(open(ns.deployment)) if ns.deployment else gitlab_deployment_of_job()
            rec = from_gitlab(dep, ns.env, ns.build, **kw)
        else:
            when = ns.approved_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z")
            rec = from_generic(ns.approver, when, ns.comment, ns.env, ns.build, **kw)
    except Refused as exc:
        print(f"approval refused: {exc}", file=sys.stderr)
        return 20
    os.makedirs(os.path.dirname(os.path.abspath(ns.out)), exist_ok=True)
    with open(ns.out, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)  # JSON is valid YAML; name the file *.yaml for `vra --approval`
    print(f"approval recorded for {ns.env} by {rec['approver']} ({rec['source']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
