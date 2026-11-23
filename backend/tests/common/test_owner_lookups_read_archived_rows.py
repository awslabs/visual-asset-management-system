# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every owner lookup on the location index reads archived rows.

The deepest stored location that holds a key owns it, live or archived. `assetLocations.resolve_owner` and
`BucketLocations.owner` without `include_archived` skip an archived row and name a shallower live one, so a
file of an archived asset nested in a live asset's location would be attributed to the live asset. Each
caller therefore asks with `include_archived=True` and, when it attributes only to live assets, treats an
archived owner as no owner. This walk fails on a call in the backend source or the migration tools that
does not pass `include_archived=True`.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOTS = [REPO_ROOT / "backend" / "backend", REPO_ROOT / "infra" / "deploymentDataMigration" / "tools"]
LOOKUPS = {"resolve_owner", "resolve_by_location"}
# `.owner(...)` is `BucketLocations.owner` in a module that loads bucket locations.
BUCKET_LOCATIONS_MARKER = "load_bucket_locations"
# (file, enclosing function): a wrapper that passes its own `include_archived` through; its callers are checked.
PASS_THROUGH = {("handlers/indexing/sqsBucketSync.py", "resolve_by_location")}


def _relative(path):
    for root in SOURCE_ROOTS:
        if root in path.parents:
            return path.relative_to(root).as_posix()
    return path.as_posix()


def _lookup_calls():
    """(file, enclosing function, line, reads archived rows) of every owner lookup call."""
    found = []
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if not (any(name in source for name in LOOKUPS) or BUCKET_LOCATIONS_MARKER in source):
                continue
            tree = ast.parse(source)
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                is_owner = name == "owner" and isinstance(func, ast.Attribute) and BUCKET_LOCATIONS_MARKER in source
                if name not in LOOKUPS and not is_owner:
                    continue
                enclosing, cursor = "", node
                while cursor in parents:
                    cursor = parents[cursor]
                    if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        enclosing = cursor.name
                        break
                reads_archived = any(keyword.arg == "include_archived" and isinstance(keyword.value, ast.Constant)
                                     and keyword.value.value is True for keyword in node.keywords)
                found.append((_relative(path), enclosing, node.lineno, reads_archived))
    return found


@pytest.mark.unit
class TestOwnerLookupsReadArchivedRows:
    def test_control_the_walk_finds_the_known_lookups(self):
        sites = {(path, function) for path, function, _, _ in _lookup_calls()}
        assert ("common/fileAttribution.py", "attribute_object") in sites
        assert ("common/indexing/fileEnumeration.py", "enumerate_latest_live_files") in sites
        assert ("handlers/indexing/crReindexer.py", "_process_bucket") in sites
        assert ("tag_synced_objects.py", "tag_synced_objects") in sites
        assert sum(1 for path, _ in sites if path == "handlers/indexing/sqsBucketSync.py") >= 3

    def test_every_owner_lookup_reads_archived_rows(self):
        live_only = [f"{path}:{line} ({function or 'module'})" for path, function, line, reads_archived
                     in _lookup_calls() if not reads_archived and (path, function) not in PASS_THROUGH]
        assert live_only == [], (
            "These owner lookups skip archived rows, so an archived asset nested in a live one would hand its "
            "files to the live one; pass include_archived=True and treat an archived owner as no owner where "
            f"only live assets count: {live_only}")
