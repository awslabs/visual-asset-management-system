# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Who is running this run: the separation-of-duties input.

The initiator is never taken from a value the caller can choose freely:

- GitHub Actions: ``GITHUB_TRIGGERING_ACTOR`` (falls back to ``GITHUB_ACTOR``), bound to the workflow run
  (``github:<repo>/actions/runs/<id>``). Environment reviewers are the approvers.
- GitLab CI: ``GITLAB_USER_LOGIN`` (the user who started the pipeline or played the job), bound to the
  pipeline (``gitlab:<project>/pipelines/<id>``). Approvers come from the deployment approvals API.
- CodeBuild: the trigger of the CodePipeline execution (``codepipeline:GetPipelineExecution``; a human ARN
  for ``StartPipelineExecution``), else ``CODEBUILD_INITIATOR``; bound to the pipeline execution id
  (``codepipeline:<pipeline>/<executionId>``) or the build id.
- Local runs: ``VRA_INITIATOR`` / git ``user.email`` / the OS user are only *asserted*. Before an
  approval-gated action the asserted value is checked against the STS caller identity; when they disagree
  the STS principal becomes the initiator (and the asserted value is kept as an alias, so SoD compares
  against both).

In CI ``VRA_INITIATOR`` is ignored unless it names the same person as the CI identity.
"""

from __future__ import annotations

import getpass
import os
import subprocess
from dataclasses import dataclass, field
from typing import Mapping

from vra.errors import NeedsHuman


@dataclass
class Identity:
    initiator: str
    source: str                      # explicit | github | gitlab | codebuild | codepipeline | sts | asserted
    verified: bool
    aliases: list[str] = field(default_factory=list)
    run_ref: str | None = None       # CI run an approval record must be bound to (approval `source`)
    attributes: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def all_names(self) -> list[str]:
        return [self.initiator, *[a for a in self.aliases if a and a != self.initiator]]

    def as_dict(self) -> dict:
        return {"initiator": self.initiator, "source": self.source, "verified": self.verified,
                "aliases": self.aliases, "runRef": self.run_ref, "attributes": self.attributes, "notes": self.notes}


def _asserted(environ: Mapping[str, str]) -> str:
    if environ.get("VRA_INITIATOR"):
        return environ["VRA_INITIATOR"]
    try:
        out = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return getpass.getuser()


def ci_provider(environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    if env.get("GITHUB_ACTIONS") == "true":
        return "github"
    if env.get("GITLAB_CI") == "true":
        return "gitlab"
    if env.get("CODEBUILD_BUILD_ID"):
        return "codebuild"
    if env.get("VRA_CI_PROVIDER") == "generic":
        return "generic"
    return None


GENERIC_VARS = ("VRA_INITIATOR", "VRA_RUN_REF")


def _generic(env: Mapping[str, str]) -> Identity:
    """Any other CI (Jenkins, Azure DevOps, Buildkite, ...): the CI asserts who started the run and which run it is.

    Contract: ``VRA_CI_PROVIDER=generic``, ``VRA_INITIATOR`` (the person who started or approved the pipeline run,
    as the CI knows them), ``VRA_RUN_REF`` (a stable id of this pipeline run, e.g. ``jenkins:<job>/<build>``; an
    approval record must carry it in ``source``), optional ``VRA_APPROVAL_REF`` (the CI's approval step id, recorded),
    and optionally ``VRA_CI_VERIFIED_BY`` (what vouches for the variables, recorded). Approval-gated envs accept a
    generic run only with ``allowGenericCi: true``; SoD still compares the approver against the asserted initiator
    and the STS caller."""
    initiator = env.get("VRA_INITIATOR") or ""
    ident = Identity(initiator, "generic", bool(initiator and env.get("VRA_RUN_REF")),
                     run_ref=env.get("VRA_RUN_REF") or None,
                     attributes={k: env[k] for k in ("VRA_APPROVAL_REF", "VRA_CI_VERIFIED_BY", "VRA_CI_NAME")
                                 if env.get(k)})
    if not ident.verified:
        ident.notes.append("VRA_CI_PROVIDER=generic needs VRA_INITIATOR and VRA_RUN_REF")
    return ident


def resolve(explicit: str | None = None, environ: Mapping[str, str] | None = None) -> Identity:
    """Identity from the environment, without AWS calls (CodeBuild is refined later by :func:`verify`)."""
    env = os.environ if environ is None else environ
    if explicit:
        return Identity(explicit, "explicit", True)
    provider = ci_provider(env)
    ident: Identity
    if provider == "github":
        actor = env.get("GITHUB_TRIGGERING_ACTOR") or env.get("GITHUB_ACTOR") or ""
        ident = Identity(actor, "github", bool(actor),
                         aliases=[env["GITHUB_ACTOR"]] if env.get("GITHUB_ACTOR") not in (None, "", actor) else [],
                         run_ref=f"github:{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{env.get('GITHUB_RUN_ID', '')}",
                         attributes={"runAttempt": env.get("GITHUB_RUN_ATTEMPT", "")})
    elif provider == "gitlab":
        login = env.get("GITLAB_USER_LOGIN") or ""
        ident = Identity(login, "gitlab", bool(login),
                         run_ref=f"gitlab:{env.get('CI_PROJECT_PATH', '')}/pipelines/{env.get('CI_PIPELINE_ID', '')}",
                         attributes={"jobId": env.get("CI_JOB_ID", "")})
    elif provider == "codebuild":
        pipe, exe = env.get("VRA_PIPELINE_NAME"), env.get("VRA_PIPELINE_EXECUTION_ID")
        ref = f"codepipeline:{pipe}/{exe}" if pipe and exe else f"codebuild:{env.get('CODEBUILD_BUILD_ID')}"
        ident = Identity(env.get("CODEBUILD_INITIATOR") or "codebuild", "codebuild", True, run_ref=ref,
                         attributes={"codebuildBuildId": env.get("CODEBUILD_BUILD_ID", ""),
                                     **({"pipelineExecutionId": exe} if exe else {})})
    elif provider == "generic":
        return _generic(env)  # unverified (variables missing): verify() falls back to the STS caller
    else:
        return Identity(_asserted(env), "asserted", False)
    asserted = env.get("VRA_INITIATOR")
    if asserted:
        from vra.approvals import same_person
        if same_person(asserted, ident.initiator):
            ident.aliases.append(asserted)
        else:
            ident.notes.append(f"VRA_INITIATOR={asserted!r} ignored: CI identity is {ident.initiator!r}")
    if not ident.initiator:
        ident.verified = False
        ident.notes.append(f"{provider}: no actor variable set")
    return ident


def _refine_codebuild(ctx, ident: Identity, environ: Mapping[str, str]) -> None:
    """Replace ``codepipeline/<name>`` with the pipeline execution's trigger (a human ARN when started by hand)."""
    pipe, exe = environ.get("VRA_PIPELINE_NAME"), environ.get("VRA_PIPELINE_EXECUTION_ID")
    if not (pipe and exe) or ident.attributes.get("triggerType"):
        return
    try:
        cp = ctx.aws.tools_client("codepipeline")
        trig = (cp.get_pipeline_execution(pipelineName=pipe, pipelineExecutionId=exe)
                .get("pipelineExecution", {}).get("trigger") or {})
    except Exception as exc:  # noqa: BLE001 - keep CODEBUILD_INITIATOR, note why
        ident.notes.append(f"pipeline trigger not read ({type(exc).__name__}); initiator = CODEBUILD_INITIATOR")
        return
    ttype, detail = trig.get("triggerType", ""), trig.get("triggerDetail", "")
    ident.attributes["triggerType"] = ttype
    prev = ident.initiator
    if ttype == "StartPipelineExecution" and detail:
        ident.initiator = detail            # the IAM principal who started the execution
    else:
        ident.initiator = f"codepipeline-trigger:{ttype}:{detail}"[:256]
    if prev and prev not in ident.aliases:
        ident.aliases.append(prev)
    ident.source = "codepipeline"


def verify(ctx, environ: Mapping[str, str] | None = None) -> Identity:
    """Make the run's identity verifiable before an approval-gated action. Idempotent; updates ``ctx``."""
    env = os.environ if environ is None else environ
    ident: Identity = ctx.identity
    if ident.source == "generic" and not ident.verified:
        raise NeedsHuman("VRA_CI_PROVIDER=generic needs VRA_INITIATOR and VRA_RUN_REF set by the CI before a gated "
                         "action")
    if ident.source == "codebuild":
        _refine_codebuild(ctx, ident, env)
    if ident.verified and ident.source == "generic" and not ident.attributes.get("stsCaller"):
        # the CI's own principal also counts for SoD: an approver can never be the CI identity either
        try:
            caller = ctx.aws.tools_client("sts").get_caller_identity()["Arn"]
            ident.attributes["stsCaller"] = caller
            ident.aliases.append(caller)
        except Exception as exc:  # noqa: BLE001
            ident.notes.append(f"STS caller of the generic CI not read ({type(exc).__name__})")
    if ident.verified:
        _apply(ctx, ident)
        return ident
    try:
        caller = ctx.aws.tools_client("sts").get_caller_identity()["Arn"]
    except Exception as exc:  # noqa: BLE001
        raise NeedsHuman("cannot verify who runs this gated action: STS GetCallerIdentity failed "
                         f"({type(exc).__name__}); run with AWS credentials of your own identity") from exc
    from vra.approvals import same_person
    asserted = ident.initiator
    if same_person(asserted, caller):
        ident.aliases.append(caller)
    else:
        ident.notes.append(f"asserted initiator {asserted!r} does not match the STS caller; using {caller}")
        ident.initiator = caller
        ident.aliases.append(asserted)
    ident.source, ident.verified = "sts", True
    _apply(ctx, ident)
    return ident


def _apply(ctx, ident: Identity) -> None:
    ctx.initiator = ident.initiator
    if hasattr(ctx.aws, "source_identity_value"):
        ctx.aws.source_identity_value = ident.initiator


def record(run, ident: Identity) -> None:
    run.state["identity"] = ident.as_dict()
    run.state["initiator"] = ident.initiator
    run.save()


__all__ = ["Identity", "resolve", "verify", "ci_provider", "record"]
