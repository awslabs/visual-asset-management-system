#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Refresh the CDK bootstrap roles `vra iam plan` models IT's bootstrap roles on.

Reads ``aws-cdk/lib/api/bootstrap/bootstrap-template.yaml`` of the CDK CLI a VAMS release pins (its
``infra/node_modules`` after ``npm ci``) and writes the reviewed copy ``iam/it-roles/cdk-bootstrap-roles.json``
and the engine copy ``engine/vra/data/cdk-bootstrap-roles.json`` (byte-identical; ``scripts/check.sh`` enforces it).
`vra iam plan` prefers the template of the fork it synthesizes and falls back to this copy (``--report``).
Format both files with the VAMS prettier configuration afterwards (they stay byte-identical).

Usage: extract_cdk_bootstrap_roles.py <path to node_modules/aws-cdk>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[2]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    cli = Path(argv[1])
    sys.path.insert(0, str(PKG / "engine"))
    from vra import iam
    tpl = cli / "lib" / "api" / "bootstrap" / "bootstrap-template.yaml"
    version = json.loads((cli / "package.json").read_text())["version"]
    doc = iam.extract_bootstrap_roles(iam.load_cfn_yaml(tpl.read_text()))
    doc["source"] = f"aws-cdk {version} bootstrap template version {doc['bootstrapVersion']}"
    text = json.dumps(doc, indent=4, sort_keys=True) + "\n"
    for rel in ("iam/it-roles/cdk-bootstrap-roles.json", "engine/vra/data/cdk-bootstrap-roles.json"):
        (PKG / rel).parent.mkdir(parents=True, exist_ok=True)
        (PKG / rel).write_text(text)
    print(doc["source"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
