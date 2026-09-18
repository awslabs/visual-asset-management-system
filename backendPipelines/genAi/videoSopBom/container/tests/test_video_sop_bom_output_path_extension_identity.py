# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The vendored output-path helper is the canonical one.

`outputPathExtension.py` places an execution's `/{{executionId}}/` extension immediately before the
final file name; the container uses it to compute the post-extension asset paths it writes into
`asset.metadata.json` and `frames.json`. The canonical module lives in the backend and this package
carries a copy, so the copy is hashed against the original after universal-newline normalisation
(the working tree may hold CRLF, git holds LF) -- the pattern the Cosmos Transfer tests use for
`manifestHelper.py`. The canonical path is asserted to exist rather than skipped: a moved original
must turn this red, not vacuously green.
"""

import ast
import hashlib
import os

import pytest

from video_sop_bom_pipeline import outputPathExtension as vendored

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.abspath(os.path.join(_CONTAINER_DIR, "..", "..", "..", ".."))
CANONICAL = os.path.join(_REPO_ROOT, "backend", "backend", "common", "workflows", "outputPathExtension.py")
VENDORED = os.path.join(_CONTAINER_DIR, "video_sop_bom_pipeline", "outputPathExtension.py")


def _helper_digest(path):
    with open(path, "r", encoding="utf-8", newline=None) as fh:
        return hashlib.sha256(fh.read().encode("utf-8")).hexdigest()


@pytest.mark.unit
class TestVendoredOutputPathExtension:
    def test_canonical_exists_and_vendored_copy_is_identical(self):
        assert os.path.isfile(CANONICAL), f"canonical helper missing at {CANONICAL}"
        assert os.path.isfile(VENDORED), f"vendored helper missing at {VENDORED}"
        assert _helper_digest(VENDORED) == _helper_digest(CANONICAL), (
            f"vendored outputPathExtension.py drifted from {CANONICAL}")

    def test_vendored_module_places_the_extension_before_the_file_name(self):
        assert vendored.apply_output_path_extension("sop-bom/sop.json", "/abc123/") == "sop-bom/abc123/sop.json"
        assert vendored.apply_output_path_extension("/sop-bom/bom.csv", "/abc123/") == "sop-bom/abc123/bom.csv"
        assert vendored.apply_output_path_extension("sop-bom/sop.json", "/") == "sop-bom/sop.json"
        assert vendored.normalize_output_path_extension("/{{executionId}}/") == "/{{executionId}}/"
        assert vendored.normalize_output_path_extension(None) == vendored.NO_EXTENSION == "/"

    def test_vendored_module_is_pure(self):
        """No imports at all and no `os.environ` access: the helper's docstring promises exactly that,
        so the check reads the AST rather than grepping (the docstring itself contains 'boto3')."""
        with open(VENDORED, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        assert imports == [], f"the helper must stay import-free, found {len(imports)} import(s)"
        attribute_chains = {f"{node.value.id}.{node.attr}" for node in ast.walk(tree)
                            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)}
        assert "os.environ" not in attribute_chains
