#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""List the AWS services the VAMS CDK app can create, and the services it passes IAM roles to.

Reads every ``infra/lib`` and ``infra/bin`` TypeScript file of a VAMS ref (read-only ``git show``), collects the
``aws-cdk-lib`` modules they use (``aws-cdk-lib/aws-<module>`` imports, ``import { aws_<module> }`` and
``cdk.aws_<module>`` references), and maps each module to

- the IAM service prefixes CloudFormation calls to create its resources (what the CDK execution policy
  ``iam/cloudformation/vra-cdk-execution-policy.yaml`` must allow), and
- the service principals its roles are handed to (``iam:PassedToService`` values of the policy's PassRole
  statement).

Every module of every config option is counted, because the imports do not depend on the config. A module
that is not in ``MODULES`` stops the script, so a new service in VAMS cannot be missed silently.

    extract_vams_services.py <vams-clone> [--ref origin/development] [--out iam/vams-services.json] [--check]

``--check`` compares with ``--out`` instead of writing it (exit 1 on a difference).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# module -> (IAM prefixes CloudFormation needs, service principals roles are passed to)
MODULES: dict[str, tuple[list[str], list[str]]] = {
    "apigateway": (["apigateway"], ["apigateway"]),        # cloudWatchRole: the account's API Gateway logging role
    "apigatewayv2": (["apigateway"], []),
    "batch": (["batch"], ["batch", "ecs-tasks", "ec2"]),     # compute env service role, job roles, instance role
    "bedrock": (["bedrock"], []),
    "bedrockagentcore": (["bedrock-agentcore"], ["bedrock-agentcore"]),
    "certificatemanager": (["acm"], []),
    "cloudformation": (["cloudformation"], []),
    "cloudfront": (["cloudfront"], []),
    "cloudfront_origins": (["cloudfront"], []),
    "cloudtrail": (["cloudtrail"], ["cloudtrail"]),           # sendToCloudWatchLogs creates a role for CloudTrail
    "cloudwatch": (["cloudwatch"], []),                       # metric alarms (e.g. indexer dead-letter queues)
    "codeartifact": (["codeartifact"], []),
    "codebuild": (["codebuild"], ["codebuild"]),
    "cognito": (["cognito-idp", "cognito-identity"], ["cognito-idp", "cognito-identity"]),  # SMS role, identity pool roles
    "dynamodb": (["dynamodb"], []),
    "ec2": (["ec2"], ["ec2", "vpc-flow-logs"]),               # instance profiles, VPC flow logs to CloudWatch Logs
    "ecr": (["ecr"], []),
    "ecr_assets": ([], []),                                   # published by the CDK bootstrap roles, not CloudFormation
    "ecs": (["ecs"], ["ecs-tasks"]),
    "efs": (["elasticfilesystem"], []),
    "eks": (["eks"], ["eks", "lambda"]),                      # cluster and node roles, cluster-handler / kubectl Lambdas
    "elasticloadbalancingv2": (["elasticloadbalancing"], []),
    "elasticloadbalancingv2_targets": (["elasticloadbalancing"], []),
    "events": (["events"], []),
    "events_targets": (["events"], ["events"]),
    "gameliftstreams": (["gameliftstreams"], []),
    "iam": (["iam"], []),
    "kms": (["kms"], []),
    "lambda": (["lambda"], ["lambda"]),
    "lambda_event_sources": (["lambda"], []),
    "lambda_nodejs": (["lambda"], ["lambda"]),
    "lambda_python_alpha": (["lambda"], ["lambda"]),
    "location": (["geo"], []),
    "logs": (["logs"], []),
    "opensearchserverless": (["aoss"], []),
    "opensearchservice": (["es"], ["es", "opensearchservice"]),
    "route53": (["route53"], []),
    "route53_targets": (["route53"], []),
    "s3": (["s3"], []),
    "s3_assets": ([], []),                                    # published by the CDK bootstrap roles
    "s3_deployment": (["s3", "lambda"], ["lambda"]),
    "s3_notifications": (["s3", "lambda"], ["lambda"]),       # bucket-notifications handler Lambda
    "secretsmanager": (["secretsmanager"], []),
    "sns": (["sns"], []),
    "sns_subscriptions": (["sns"], []),
    "sqs": (["sqs"], []),
    "ssm": (["ssm"], []),
    "stepfunctions": (["states"], ["states"]),
    "stepfunctions_tasks": (["states"], []),
    "wafv2": (["wafv2"], []),
    "custom_resources": (["lambda"], ["lambda"]),             # AwsCustomResource / Provider framework Lambdas
}

_IMPORT_PATH = re.compile(r"""["'](?:aws-cdk-lib/(aws-[a-z0-9-]+|custom-resources)|@aws-cdk/(aws-[a-z0-9-]+)-alpha)["']""")
_IMPORT_NAMED = re.compile(r"import\s*\{([^}]*)\}\s*from\s*[\"']aws-cdk-lib[\"']", re.S)
_NAMESPACE = re.compile(r"\bcdk\.(aws_[a-z0-9_]+)\b")


def _norm(module: str) -> str:
    m = module[4:] if module.startswith(("aws-", "aws_")) else module
    return m.replace("-", "_")


def modules_in(text: str) -> set[str]:
    found: set[str] = set()
    for m in _IMPORT_PATH.finditer(text):
        name = m.group(1) or m.group(2)
        found.add(_norm(name) + ("_alpha" if m.group(2) else ""))
    for m in _IMPORT_NAMED.finditer(text):
        for part in m.group(1).split(","):
            name = part.strip().split(" as ")[0].strip()
            if name.startswith("aws_"):
                found.add(_norm(name))
    for m in _NAMESPACE.finditer(text):
        found.add(_norm(m.group(1)))
    return found


def read_ref(repo: Path, ref: str) -> tuple[str, dict[str, str]]:
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", ref], capture_output=True, text=True,
                         check=True).stdout.strip()
    names = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", sha, "infra/lib", "infra/bin"],
                           capture_output=True, text=True, check=True).stdout.split()
    files = {}
    for n in names:
        if n.endswith(".ts") and not n.endswith(".d.ts"):
            files[n] = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{n}"], capture_output=True,
                                      text=True, check=True).stdout
    return sha, files


def summarize(sha: str, files: dict[str, str]) -> dict:
    used: dict[str, list[str]] = {}
    for path, text in sorted(files.items()):
        for mod in modules_in(text):
            used.setdefault(mod, []).append(path)
    unknown = sorted(set(used) - set(MODULES))
    if unknown:
        raise SystemExit("aws-cdk-lib modules with no mapping in MODULES (add them, then rerun): "
                         + ", ".join(f"{u} ({used[u][0]})" for u in unknown))
    prefixes = sorted({p for m in used for p in MODULES[m][0]})
    passed = sorted({p for m in used for p in MODULES[m][1]})
    return {"vamsCommit": sha, "modules": sorted(used), "iamPrefixes": prefixes, "passRoleServices": passed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", type=Path)
    ap.add_argument("--ref", default="origin/development")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "vams-services.json")
    ap.add_argument("--check", action="store_true")
    ns = ap.parse_args(argv)
    sha, files = read_ref(ns.repo, ns.ref)
    data = summarize(sha, files)
    if ns.check:
        old = json.loads(ns.out.read_text())
        diff = {k: (old.get(k), data[k]) for k in ("modules", "iamPrefixes", "passRoleServices") if old.get(k) != data[k]}
        if diff:
            print(json.dumps(diff, indent=2))
            return 1
        print(f"{ns.out.name} matches {ns.ref} ({sha[:12]})")
        return 0
    ns.out.write_text(json.dumps(data, indent=4) + "\n")
    print(f"wrote {ns.out} from {ns.ref} ({sha[:12]}): {len(data['modules'])} modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
