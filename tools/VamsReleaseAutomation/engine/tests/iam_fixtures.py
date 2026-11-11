# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real VAMS v2.6.6 cloud assemblies (fixtures/iam/*.json.gz, see fixtures/iam/README.md) for the `vra iam` tests."""

from __future__ import annotations

import copy
from pathlib import Path

from vra import iam

FIXTURES = Path(__file__).parent / "fixtures" / "iam"
ACCOUNT = "111111111111"          # the synth account, replaced by this placeholder in the fixtures
STACK = {"commercial": "vams-core-prod-us-east-1", "govcloud": "vams-core-prod-us-gov-west-1"}
_CACHE: dict[str, iam.Assembly] = {}


def load(name: str) -> iam.Assembly:
    """``commercial-report``, ``govcloud-report``, ``commercial-precreated``, ``commercial-precreated-partial``,
    ``commercial-boundary``, ``commercial-boundary-fixed``, ``commercial-cli``, ``commercial-cdkflag-patched``,
    ``commercial-cdkboundary-patched`` (a fresh copy each call)."""
    if name not in _CACHE:
        _CACHE[name] = iam.Assembly.from_fixture(FIXTURES / f"vams-2.6.6-{name}.json.gz")
    a = _CACHE[name]
    return iam.Assembly(copy.deepcopy(a.report), copy.deepcopy(a.templates), copy.deepcopy(a.manifest), a.where)


def write(name: str, out: Path, mutate=None) -> Path:
    asm = load(name)
    if mutate:
        mutate(asm)
    asm.write(out)
    return out


def add_role(asm: iam.Assembly, suffix: str = "NewUpstreamHandler", missing: bool = True) -> str:
    """An upstream merge that adds a Lambda handler (a copy of an existing API handler role)."""
    src = next(r for r in asm.report["roles"] if r["roleConstructPath"].endswith("/assetService/ServiceRole"))
    new = copy.deepcopy(src)
    new["roleConstructPath"] = src["roleConstructPath"].replace("assetService", suffix)
    new["missing"] = missing
    new["roleName"] = "missing role" if missing else "it-vams-new"
    asm.report["roles"].append(new)
    return new["roleConstructPath"]


def add_action(asm: iam.Assembly, path_tail: str = "/assetService/ServiceRole", action: str = "s3:GetObjectAcl") -> None:
    r = next(x for x in asm.report["roles"] if x["roleConstructPath"].endswith(path_tail))
    r["identityPolicyStatements"].append({"Action": action, "Effect": "Allow", "Resource": "*"})
