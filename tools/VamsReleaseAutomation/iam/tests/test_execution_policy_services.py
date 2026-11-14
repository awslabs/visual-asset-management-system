# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The scoped CDK execution policy covers exactly the services the VAMS CDK app uses.

``iam/vams-services.json`` is extracted from the VAMS source (``iam/scripts/extract_vams_services.py``). The
policy must allow every IAM service prefix listed there, nothing else, and pass roles to exactly the listed
services. With ``VAMS_REPO=<clone>`` the extraction is re-run against ``VAMS_REF`` (default
``origin/development``) and must match the committed JSON.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
import yaml

IAM = Path(__file__).resolve().parents[1]
POLICY = IAM / "cloudformation" / "vra-cdk-execution-policy.yaml"
SERVICES = json.loads((IAM / "vams-services.json").read_text())

# Prefixes the policy may hold beyond the VAMS services: identity lookups CDK custom resources make.
EXTRA_ALLOWED = {"sts"}


class _Loader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_Loader.add_multi_constructor("!", _tag)


def _statements() -> list[dict]:
    loader = _Loader(POLICY.read_text())  # a yaml.SafeLoader subclass that only adds the CFN tags
    try:
        doc = loader.get_single_data()
    finally:
        loader.dispose()
    out: list[dict] = []

    def walk(node):
        # every branch of !If counts: a branch the stack can take is a grant the policy can make
        if isinstance(node, dict) and "Effect" in node:
            out.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc["Resources"]["ExecutionPolicy"]["Properties"]["PolicyDocument"]["Statement"])
    return out


def _actions(st: dict) -> list[str]:
    a = st.get("Action", [])
    return a if isinstance(a, list) else [a]


def test_policy_allows_every_vams_service_and_no_other():
    allowed = {a.split(":")[0] for st in _statements() if st["Effect"] == "Allow" for a in _actions(st)}
    required = set(SERVICES["iamPrefixes"])
    assert sorted(required - allowed) == [], "services VAMS creates resources in but the policy does not allow"
    assert sorted(allowed - required - EXTRA_ALLOWED) == [], "services the policy allows but VAMS does not use"


def test_policy_covers_the_cognito_identity_pool():
    """The identity pool (CfnIdentityPool + role attachment) is created for every Cognito deployment."""
    regional = next(st for st in _statements() if st.get("Sid") == "VamsServiceSurfaceRegional")
    assert "cognito-identity:*" in _actions(regional)
    assert "cognito-identity" in _passed_services()


def _passed_services() -> set[str]:
    st = next(s for s in _statements() if s.get("Sid") == "PassRoleToVamsServices")
    values = st["Condition"]["StringLike"]["iam:PassedToService"]
    return {v.removesuffix("*").removesuffix(".amazonaws.com") for v in values}


def test_passrole_targets_exactly_the_services_vams_passes_roles_to():
    assert sorted(_passed_services()) == SERVICES["passRoleServices"]


def test_extraction_maps_every_module_it_finds():
    spec = importlib.util.spec_from_file_location("extract_vams_services", IAM / "scripts" / "extract_vams_services.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = (
        'import * as cognito from "aws-cdk-lib/aws-cognito";\n'
        'import { aws_location, Stack } from "aws-cdk-lib";\n'
        "const d = new cdk.aws_opensearchservice.Domain(this, 'D', {});\n"
        'import * as py from "@aws-cdk/aws-lambda-python-alpha";\n'
    )
    assert mod.modules_in(text) == {"cognito", "location", "opensearchservice", "lambda_python_alpha"}
    data = mod.summarize("0" * 40, {"infra/lib/a.ts": text})
    assert {"cognito-identity", "geo", "es", "lambda"} <= set(data["iamPrefixes"])
    with pytest.raises(SystemExit, match="aws_newservice|newservice"):
        mod.summarize("0" * 40, {"infra/lib/b.ts": 'import * as n from "aws-cdk-lib/aws-newservice";'})


@pytest.mark.skipif(not os.environ.get("VAMS_REPO"), reason="set VAMS_REPO=<VAMS clone> to re-extract from source")
def test_committed_extraction_matches_vams_source():
    spec = importlib.util.spec_from_file_location("extract_vams_services", IAM / "scripts" / "extract_vams_services.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sha, files = mod.read_ref(Path(os.environ["VAMS_REPO"]), os.environ.get("VAMS_REF", "origin/development"))
    data = mod.summarize(sha, files)
    for key in ("modules", "iamPrefixes", "passRoleServices"):
        assert data[key] == SERVICES[key], key
