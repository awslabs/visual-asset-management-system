# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every read of a bucket registration's `baseAssetsPrefix` in backend code goes through the shared
normalizer, `common.assetLocations.normalize_base_prefix` (or `derive_location`, which applies it).

Registrations spell one prefix as `assets`, `assets/` or `/assets/`, and a prefix normalized by hand in
one place and not in another resolves a file to the wrong folder. A read counts as normalized when it is
an argument of the normalizer, or when it is assigned to a name that the same function then passes to
the normalizer. Any other read must be listed below with the reason it is safe; a new read fails this
test until it is normalized or reviewed."""

import ast
import os
from typing import Dict, List

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
FIELD = "baseAssetsPrefix"
NORMALIZERS = {"normalize_base_prefix", "derive_location", "shared_normalize_base_prefix",
               # osFileIndexer: the shared normalizer, keeping None for an unknown prefix.
               "normalize_bucket_prefix"}

# file -> (reads, why they are safe). Each read below takes the value from a bucket-details dict its
# own module already built with the normalizer, or passes the stored value through unchanged.
_OWN_DETAILS = "reads the bucket details its module's get_default_bucket_details built with the normalizer"
REVIEWED: Dict[str, tuple] = {
    "common/indexing/fileEnumeration.py": (1, "a sort key of the registration walk order, never a key prefix"),
    "common/workflows/defaultBucket.py": (1, "returns the stored row; default_bucket_key normalizes it before any key"),
    "handlers/addon/garnetFramework/garnetDataIndexAsset.py": (
        2, "copied into the Garnet record as the module's bucket details hold it; no key is built from it"),
    "handlers/addon/garnetFramework/garnetDataIndexDatabase.py": (2, _OWN_DETAILS + ", copied into the Garnet record"),
    "handlers/assets/assetExportService.py": (1, _OWN_DETAILS),
    "handlers/assets/assetFiles.py": (1, _OWN_DETAILS),
    "handlers/assets/createAsset.py": (1, "passed to prefix_roots_overlap, which normalizes both prefixes"),
    "handlers/assets/uploadFile.py": (3, _OWN_DETAILS),
    "handlers/databases/databaseService.py": (3, "returned in bucket listings and responses as stored"),
    "handlers/indexing/crReindexer.py": (1, "passed to _process_bucket, which normalizes it before listing"),
    "handlers/osSemanticSearch/osAssetIndexer.py": (1, "stored on the search document as the bucket details hold it"),
    "handlers/osSemanticSearch/osFileIndexer.py": (1, "stored on the search document as the bucket details hold it"),
    "handlers/workflows/sfn/processWorkflowExecutionOutput.py": (2, _OWN_DETAILS),
}


def _call_name(node) -> str:
    func = getattr(node, "func", None)
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_read(node) -> bool:
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value == FIELD
    if isinstance(node, ast.Call) and _call_name(node) == "get" and node.args:
        first = node.args[0]
        return isinstance(first, ast.Constant) and first.value == FIELD
    return False


def _unnormalized_reads(source: str) -> List[int]:
    tree = ast.parse(source)
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def enclosing(node, kinds):
        node = parents.get(node)
        while node is not None and not isinstance(node, kinds):
            node = parents.get(node)
        return node

    def normalized_later(name: str, scope) -> bool:
        return any(isinstance(n, ast.Call) and _call_name(n) in NORMALIZERS
                   and any(isinstance(a, ast.Name) and a.id == name for a in n.args)
                   for n in ast.walk(scope))

    lines = []
    for node in ast.walk(tree):
        if not _is_read(node):
            continue
        wrapped, up = False, parents.get(node)
        while up is not None and not isinstance(up, (ast.stmt,)):
            if isinstance(up, ast.Call) and _call_name(up) in NORMALIZERS:
                wrapped = True
                break
            up = parents.get(up)
        if wrapped:
            continue
        statement = up
        scope = enclosing(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or tree
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 \
                and isinstance(statement.targets[0], ast.Name) \
                and normalized_later(statement.targets[0].id, scope):
            continue
        lines.append(node.lineno)
    return lines


def _all_reads() -> Dict[str, List[int]]:
    found = {}
    for root, _dirs, files in os.walk(BACKEND):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if FIELD not in source:
                continue
            lines = _unnormalized_reads(source)
            if lines:
                found[os.path.relpath(path, BACKEND).replace(os.sep, "/")] = lines
    return found


@pytest.mark.unit
class TestTheSharedNormalizer:
    @pytest.mark.parametrize("raw,expected", [
        (None, ""), ("", ""), ("/", ""), ("//", ""), ("assets", "assets/"), ("assets/", "assets/"),
        ("/assets/", "assets/"), ("//assets//", "assets/"), ("a/b", "a/b/")])
    def test_every_spelling_has_one_form(self, raw, expected):
        from common.assetLocations import normalize_base_prefix
        assert normalize_base_prefix(raw) == expected

    def test_the_run_io_and_default_bucket_helpers_use_it(self):
        from common.workflows.defaultBucket import default_bucket_key
        from common.workflows.executionRecords import normalize_base_prefix as records_normalizer
        assert records_normalizer("//vams//") == "vams/"
        assert default_bucket_key("/vams", "/pipelines/x.json") == "vams/pipelines/x.json"
        assert default_bucket_key({"baseAssetsPrefix": "/"}, "pipelines/x.json") == "pipelines/x.json"

    @pytest.mark.parametrize("key,asset_id,expected", [
        ("vams/a1/sub/x.glb", "a1", "vams/a1/"), ("a1/x.glb", "a1", "a1/"),
        ("vams/other/x.glb", "a1", ""), ("vams/a10/x.glb", "a1", "")])
    def test_a_legacy_row_is_located_by_its_folder_in_the_key(self, key, asset_id, expected):
        from common.assetLocations import legacy_location_in_key
        assert legacy_location_in_key(key, asset_id) == expected


@pytest.mark.unit
class TestBasePrefixReadsAreNormalized:
    def test_the_detector_sees_both_read_shapes_and_the_normalized_forms(self):
        source = (
            "def f(bucket):\n"
            "    a = bucket.get('baseAssetsPrefix')\n"
            "    b = bucket['baseAssetsPrefix']\n"
            "    c = normalize_base_prefix(bucket.get('baseAssetsPrefix'))\n"
            "    d = bucket.get('baseAssetsPrefix') or ''\n"
            "    e = bucket.get('baseAssetsPrefix')\n"
            "    e = normalize_base_prefix(e)\n"
            "    return a, b, c, d, e\n")
        assert _unnormalized_reads(source) == [2, 3, 5]

    def test_no_read_bypasses_the_shared_normalizer(self):
        reads = _all_reads()
        unreviewed = {f: lines for f, lines in reads.items()
                      if f not in REVIEWED or len(lines) > REVIEWED[f][0]}
        assert not unreviewed, (
            "baseAssetsPrefix read without common.assetLocations.normalize_base_prefix: normalize it, or "
            f"review it and list it in REVIEWED with its reason: {unreviewed}")

    def test_the_reviewed_list_is_exact(self):
        reads = _all_reads()
        stale = {f: (count, len(reads.get(f, []))) for f, (count, _why) in REVIEWED.items()
                 if len(reads.get(f, [])) != count}
        assert not stale, f"update REVIEWED, these counts no longer match (listed, found): {stale}"
