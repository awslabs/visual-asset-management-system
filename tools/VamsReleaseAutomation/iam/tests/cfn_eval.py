# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline helpers for the IAM and CI tests: render a CloudFormation template's policy documents with given
parameters and conditions, and evaluate them as policy decisions.

The evaluator is deliberately small: explicit deny > allow > implicit deny; Action / NotAction / Resource with
`*` wildcards; condition operators StringEquals / StringLike / StringNotEquals / StringNotLike / ArnLike /
ArnNotLike / Bool / Null, each optionally ForAllValues: / ForAnyValue:. Every block of a Condition must hold.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import yaml

NOVALUE = object()


class _Loader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    name = "Ref" if suffix == "Ref" else ("Fn::GetAtt" if suffix == "GetAtt" else f"Fn::{suffix}")
    if isinstance(node, yaml.ScalarNode):
        val = loader.construct_scalar(node)
        if suffix == "GetAtt":
            val = val.split(".")
    elif isinstance(node, yaml.SequenceNode):
        val = loader.construct_sequence(node, deep=True)
    else:
        val = loader.construct_mapping(node, deep=True)
    return {name: val}


_Loader.add_multi_constructor("!", _tag)


def load_cfn(path: Path) -> dict:
    return yaml.load(Path(path).read_text(), Loader=_Loader)


def render(node, params: dict, conditions: dict, resources: dict | None = None):
    resources = resources or {}

    def sub(text: str, extra: dict) -> str:
        def one(m):
            key = m.group(1)
            if key in extra:
                return str(extra[key])
            if key in params:
                v = params[key]
                return ",".join(v) if isinstance(v, list) else str(v)
            if key.endswith(".Arn") and key[:-4] in resources:
                return str(resources[key[:-4]])
            return m.group(0)
        return re.sub(r"\$\{([^}]+)\}", one, text)

    def r(n):
        if isinstance(n, dict) and len(n) == 1:
            (k, v), = n.items()
            if k == "Ref":
                if v == "AWS::NoValue":
                    return NOVALUE
                return resources.get(v, params.get(v))
            if k == "Fn::Sub":
                if isinstance(v, list):
                    return sub(v[0], {kk: r(vv) for kk, vv in v[1].items()})
                return sub(v, {})
            if k == "Fn::If":
                return r(v[1] if conditions[v[0]] else v[2])
            if k == "Fn::Join":
                return v[0].join(str(x) for x in r(v[1]))
            if k == "Fn::Split":
                return r(v[1]).split(v[0])
            if k == "Fn::Select":
                return r(v[1])[int(v[0])]
            if k == "Fn::GetAtt":
                return resources.get(v[0] if isinstance(v, list) else v)
        if isinstance(n, dict):
            out = {k: r(v) for k, v in n.items()}
            return {k: v for k, v in out.items() if v is not NOVALUE}
        if isinstance(n, list):
            return [x for x in (r(i) for i in n) if x is not NOVALUE]
        return n
    return r(node)


def _as_list(v):
    return v if isinstance(v, list) else [v]


def _one(base: str, have: str, want: list[str]) -> bool:
    neg = "Not" in base
    if base.startswith("Bool"):
        hit = any(str(have).lower() == w.lower() for w in want)
    elif "Like" in base:
        hit = any(fnmatch.fnmatchcase(str(have), w) for w in want)
    else:
        hit = any(str(have) == w for w in want)
    return not hit if neg else hit


def cond_ok(cond: dict | None, ctx: dict) -> bool:
    for block_op, kv in (cond or {}).items():
        for key, want in kv.items():
            op = block_op
            want = [str(w) for w in _as_list(want)]
            have = ctx.get(key)
            if op == "Null":
                if (have is None) != (want[0] == "true"):
                    return False
                continue
            if op.endswith("IfExists"):
                if have is None:
                    continue  # ...IfExists: a missing key makes the condition true
                op = op[: -len("IfExists")]
            if op.startswith("ForAllValues:"):
                base = op.split(":", 1)[1]
                if have is not None and not all(_one(base, h, want) for h in _as_list(have)):
                    return False
                continue
            if op.startswith("ForAnyValue:"):
                base = op.split(":", 1)[1]
                if have is None or not any(_one(base, h, want) for h in _as_list(have)):
                    return False
                continue
            if have is None:
                if "Not" in op:
                    continue  # negated single-valued operators match a missing key
                return False
            vals = _as_list(have)
            ok = all(_one(op, h, want) for h in vals) if "Not" in op else any(_one(op, h, want) for h in vals)
            if not ok:
                return False
    return True


def matches(stmt: dict, action: str, resource: str, ctx: dict) -> bool:
    if "Action" in stmt and not any(fnmatch.fnmatchcase(action.lower(), a.lower()) for a in _as_list(stmt["Action"])):
        return False
    if "NotAction" in stmt and any(fnmatch.fnmatchcase(action.lower(), a.lower())
                                   for a in _as_list(stmt["NotAction"])):
        return False
    if "Resource" in stmt and not any(fnmatch.fnmatchcase(resource, r) for r in _as_list(stmt["Resource"])):
        return False
    return cond_ok(stmt.get("Condition"), ctx)


def denied(statements: list, action: str, resource: str, ctx: dict | None = None) -> bool:
    return any(s["Effect"] == "Deny" and matches(s, action, resource, ctx or {}) for s in statements)


def allowed(statements: list, action: str, resource: str, ctx: dict | None = None) -> bool:
    ctx = ctx or {}
    if denied(statements, action, resource, ctx):
        return False
    return any(s["Effect"] == "Allow" and matches(s, action, resource, ctx) for s in statements)
