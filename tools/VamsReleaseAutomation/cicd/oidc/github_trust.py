#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""One source for the GitHub OIDC trust of every vra role: the per-environment CI roles (vra-ci-oidc-role.yaml)
and the CodePipeline hand-off role (cicd/pipeline ActionsHandoffRole, lib/config.ts githubSubject).

IAM condition keys are the issuer host (``<issuer>:aud``, ``<issuer>:sub``), and CloudFormation cannot compute
a key, so the template is rendered for the issuer:

  github_trust.py issuer   --server https://github.com | https://<tenant>.ghe.com | https://<ghes-host>
                           [--enterprise SLUG]                       prints the issuer host
  github_trust.py policy   --provider-arn ARN --issuer HOST --repo OWNER/REPO --environment ENV
                           [--audience A] [--owner-id N --repository-id N]   prints the trust policy JSON
  github_trust.py template --issuer HOST [--in vra-ci-oidc-role.yaml] [--out FILE]
                                                                     the CI role template with HOST in its keys

Issuers: github.com and GHEC with data residency (``<tenant>.ghe.com``) ``token.actions.githubusercontent.com``
(``token.actions.<tenant>.ghe.com`` for data residency); the GHEC enterprise issuer
``token.actions.githubusercontent.com/<enterprise>`` (the enterprise's "unique token URL" setting); GitHub
Enterprise Server ``<host>/_services/token``.

Subjects: ``repo:<owner>/<repo>:environment:<env>`` by default. Repositories that send immutable subjects
(``--owner-id`` / ``--repository-id``; the template's RepositoryOwnerId / RepositoryId) use
``repo:<owner>@<owner-id>/<repo>@<repo-id>:environment:<env>``: a renamed or re-created repository of the
same name no longer matches.

Python standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_ISSUER = "token.actions.githubusercontent.com"
NAME_SUBJECT = "repo:{repo}:environment:{env}"
IMMUTABLE_SUBJECT = "repo:{owner}@{owner_id}/{name}@{repository_id}:environment:{env}"
TEMPLATE = Path(__file__).with_name("vra-ci-oidc-role.yaml")


def issuer_host(server: str, enterprise: str | None = None) -> str:
    host = re.sub(r"^https?://", "", server.strip()).rstrip("/").lower()
    if host in ("github.com", "www.github.com"):
        base = DEFAULT_ISSUER
    elif host.endswith(".ghe.com"):
        base = f"token.actions.{host}"
    else:
        if enterprise:
            raise ValueError("--enterprise applies to github.com (GHEC), not to a GitHub Enterprise Server host")
        return f"{host}/_services/token"
    return f"{base}/{enterprise}" if enterprise else base


def issuer_of_provider(provider_arn: str) -> str:
    m = re.match(r"^arn:aws[a-z-]*:iam::\d{12}:oidc-provider/(.+)$", provider_arn)
    if not m:
        raise ValueError(f"{provider_arn!r} is not an IAM OIDC provider ARN")
    return m.group(1)


def subject(env: str, repo: str | None = None, owner_id: str | None = None, repository_id: str | None = None) -> str:
    if not repo or not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        raise ValueError("--repo OWNER/REPO is required")
    if owner_id or repository_id:
        if not (owner_id and repository_id and owner_id.isdigit() and repository_id.isdigit()):
            raise ValueError("--owner-id and --repository-id are both required and numeric")
        owner, name = repo.split("/", 1)
        return IMMUTABLE_SUBJECT.format(owner=owner, owner_id=owner_id, name=name, repository_id=repository_id,
                                        env=env)
    return NAME_SUBJECT.format(repo=repo, env=env)


def trust_policy(provider_arn: str, subjects: list[str], audience: str = "sts.amazonaws.com",
                 issuer: str | None = None) -> dict:
    iss = issuer or issuer_of_provider(provider_arn)
    if issuer and issuer != issuer_of_provider(provider_arn):
        raise ValueError(f"the provider {provider_arn} is for {issuer_of_provider(provider_arn)}, not {issuer}")
    if not subjects or any(s in ("", "*") for s in subjects):
        raise ValueError("at least one subject, never a bare *")
    return {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Federated": provider_arn}, "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {"StringEquals": {f"{iss}:aud": audience}, "StringLike": {f"{iss}:sub": subjects}}}]}


def render_template(issuer: str, text: str | None = None) -> str:
    """The CI role template with ``issuer`` in its GitHub condition keys (and its created provider's URL)."""
    text = TEMPLATE.read_text(encoding="utf-8") if text is None else text
    if not re.match(r"^[a-z0-9.-]+(/[A-Za-z0-9_./-]+)?$", issuer):
        raise ValueError(f"{issuer!r} is not an issuer host")
    if issuer == DEFAULT_ISSUER:
        return text
    out = text.replace(f"{DEFAULT_ISSUER}:aud:", f"{issuer}:aud:").replace(f"{DEFAULT_ISSUER}:sub:", f"{issuer}:sub:")
    out = out.replace(f"https://{DEFAULT_ISSUER}", f"https://{issuer}")
    return f"# Rendered by github_trust.py template --issuer {issuer}\n" + out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("issuer")
    s.add_argument("--server", required=True)
    s.add_argument("--enterprise")
    s = sub.add_parser("policy")
    s.add_argument("--provider-arn", required=True)
    s.add_argument("--issuer")
    s.add_argument("--repo")
    s.add_argument("--environment", required=True, action="append")
    s.add_argument("--audience", default="sts.amazonaws.com")
    s.add_argument("--owner-id")
    s.add_argument("--repository-id")
    s = sub.add_parser("template")
    s.add_argument("--issuer", required=True)
    s.add_argument("--in", dest="src", type=Path, default=TEMPLATE)
    s.add_argument("--out", type=Path)
    ns = ap.parse_args(argv)
    try:
        if ns.cmd == "issuer":
            print(issuer_host(ns.server, ns.enterprise))
        elif ns.cmd == "policy":
            subs = [subject(e, ns.repo, ns.owner_id, ns.repository_id) for e in ns.environment]
            print(json.dumps(trust_policy(ns.provider_arn, subs, ns.audience, ns.issuer), indent=2))
        else:
            out = render_template(ns.issuer, ns.src.read_text(encoding="utf-8"))
            if ns.out:
                ns.out.write_text(out, encoding="utf-8")
            else:
                sys.stdout.write(out)
    except ValueError as exc:
        print(f"github_trust: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
