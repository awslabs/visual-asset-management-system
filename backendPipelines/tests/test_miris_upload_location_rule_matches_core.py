# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Miris upload pipeline decides which keys a nested asset holds with the core location rule.

A pipeline function and container cannot import ``backend/``, so ``lambda/assetNesting.py`` and
``container/asset_nesting.py`` repeat ``normalize_location``, ``_is_preview_of`` and ``contains`` from
``backend/backend/common/assetLocations.py``. This test compares each copy's code with the core
function (docstrings aside) and the preview marker with the core constant, so a change to the core
rule that is not made in the pipeline too fails here.
"""

import ast
import io
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CORE = os.path.join(_REPO_ROOT, "backend", "backend", "common", "assetLocations.py")
_CORE_PATTERNS = os.path.join(_REPO_ROOT, "backend", "backend", "common", "s3PathPatterns.py")
_PIPELINE = os.path.join(_REPO_ROOT, "backendPipelines", "addon", "conversion", "mirisUpload")
_COPIES = [
    os.path.join(_PIPELINE, "lambda", "assetNesting.py"),
    os.path.join(_PIPELINE, "container", "asset_nesting.py"),
]
_RULE = ["normalize_location", "_is_preview_of", "contains"]


def _tree(path):
    return ast.parse(io.open(path, encoding="utf-8").read())


def _function(path, name):
    found = [n for n in _tree(path).body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, f"{name} not defined once at the top of {path}"
    node = found[0]
    body = node.body[1:] if node.body and isinstance(node.body[0], ast.Expr) and isinstance(
        getattr(node.body[0], "value", None), ast.Constant) else node.body
    return ast.dump(ast.Module(body=body, type_ignores=[])), ast.dump(node.args)


def _constant(path, name):
    for node in _tree(path).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not assigned at the top of {path}")


@pytest.mark.parametrize("copy", _COPIES, ids=["lambda", "container"])
@pytest.mark.parametrize("name", _RULE)
def test_the_pipeline_copy_of_the_location_rule_is_the_core_rule(copy, name):
    assert _function(copy, name) == _function(_CORE, name)


@pytest.mark.parametrize("copy", _COPIES, ids=["lambda", "container"])
def test_the_preview_marker_is_the_core_marker(copy):
    assert _constant(copy, "PREVIEW_FILE_PATTERN") == _constant(_CORE_PATTERNS, "PREVIEW_FILE_PATTERN")


def test_control_the_core_contains_still_reads_previews_and_folders():
    # A core refactor that moves the rule elsewhere should fail here with a clear reason, not
    # compare the copies with an unrelated function.
    text = ast.unparse(_tree(_CORE))
    assert "def contains(" in text and "_is_preview_of(loc, object_key)" in text
