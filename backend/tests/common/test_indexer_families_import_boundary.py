# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The three indexing groups of ``backend/backend`` keep to their import boundaries.

``handlers/indexing`` is the CORE: bucket sync, the SNS-to-SQS queuing shims, and the row-rewriting
reindexer. It runs in every deployment and feeds both search families through the storage stack's
SNS fan-out, so it must import neither of them.

``handlers/osSemanticSearch`` is the OpenSearch family (the keyword ``/search`` route plus the
``osFileIndexer`` / ``osAssetIndexer`` stream consumers). It is gated on the OpenSearch flags and
absent from a deployment without an OpenSearch mode.

``handlers/osVectorSearch`` (with ``common/vectorsearch`` and ``models/vectorsearch``) is the vector
family: gated on ``app.vectorSearch.enabled``, stored in its own DynamoDB table, fed by its own SQS
queue and EventBridge rule, and removed wholesale when the flag is off.

Either search family can be deployed without the other, so a module-level import across a boundary
makes the surviving code fail at COLD START -- a ``500`` on every request, invisible to synth and to
a test suite that imports everything. The CDK side of the same boundary (no cross-wired queue, no
cross-gated function) is pinned by ``infra/test/platform/t1VectorIndexingWiring.test.ts``; this guard
covers the Python side, which that synth cannot see.

Shared ground is deliberate and stays out of scope: all three groups import ``common/indexing``
(document ids, file enumeration -- "what is an asset file" defined once), ``common/databaseAccess``,
and the rest of ``common``. Only an import that names ANOTHER group's own packages is a hit.

**Two admitted crossings, both function-scoped.**

* ``vectorSearchService._opensearch_step`` imports ``handlers.osSemanticSearch.search`` INSIDE the
  function, after ``OPENSEARCH_DISABLED`` has been ruled out, to enrich hits when an OpenSearch mode
  is on. A deployment without OpenSearch never executes that import.
* ``crReindexer.ReindexUtility.clear_opensearch_indexes`` imports ``opensearchpy`` INSIDE the
  method, so the core reindexer's row-rewriting path never loads the client on a deployment without
  OpenSearch.

Each exemption names the function and admits one site, so the same import at module level -- or a
second function doing it -- fails here.
"""

import ast
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"

# Module prefixes that NAME each group. An import of one of these from outside the group is a crossing.
CORE_FAMILY = ("handlers.indexing",)
OPENSEARCH_FAMILY = ("handlers.osSemanticSearch", "opensearchpy")
VECTOR_FAMILY = ("handlers.osVectorSearch", "common.vectorsearch", "models.vectorsearch")

# The directory that OWNS each group. A file under one of these is a member of that group.
CORE_DIRS = ("handlers/indexing",)
OPENSEARCH_DIRS = ("handlers/osSemanticSearch",)
VECTOR_DIRS = ("handlers/osVectorSearch", "common/vectorsearch")
VECTOR_FILES = ("models/vectorsearch.py",)

# (relative file, function) -> admitted count of imports of a foreign group INSIDE that function.
# An entry names the function, never the file, and is an upper bound (converting a site keeps the
# guard green).
_ADMITTED_CROSSINGS: Dict[Tuple[str, str], int] = {
    ("handlers/osVectorSearch/vectorSearchService.py", "_opensearch_step"): 1,
    ("handlers/indexing/crReindexer.py", "clear_opensearch_indexes"): 1,
}


def _imported_names(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def _crosses(name: str, other_family: Tuple[str, ...]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in other_family)


def _is_core_member(rel: str) -> bool:
    return any(rel.startswith(d + "/") for d in CORE_DIRS)


def _is_opensearch_member(rel: str) -> bool:
    return any(rel.startswith(d + "/") for d in OPENSEARCH_DIRS)


def _is_vector_member(rel: str) -> bool:
    return rel in VECTOR_FILES or any(rel.startswith(d + "/") for d in VECTOR_DIRS)


def _crossings(rel: str, other_family: Tuple[str, ...]):
    """Yield (function_name_or_None, imported_name, lineno) for every import of the other family.

    ``function_name`` is ``None`` for a module-level import and the enclosing ``def``'s name otherwise.
    """
    tree = ast.parse((BACKEND_ROOT / rel).read_text(encoding="utf-8"))
    parents: Dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if not _crosses(name, other_family):
                continue
            func = None
            cursor = node
            while cursor in parents:
                cursor = parents[cursor]
                if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func = cursor.name
                    break
            yield func, name, node.lineno


def _members(predicate: Callable[[str], bool]) -> List[str]:
    return sorted(
        str(p.relative_to(BACKEND_ROOT)).replace("\\", "/")
        for p in BACKEND_ROOT.rglob("*.py")
        if predicate(str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"))
    )


def _partition(members: List[str], foreign: Tuple[str, ...]):
    """Split a group's crossings into module-level hits, unadmitted function-scoped hits, and the
    per-(file, function) counts of the admitted ones."""
    module_level, unadmitted, counts = [], [], {}
    for rel in members:
        for func, name, line in _crossings(rel, foreign):
            if func is None:
                module_level.append((rel, name, line))
                continue
            key = (rel, func)
            counts[key] = counts.get(key, 0) + 1
            if key not in _ADMITTED_CROSSINGS:
                unadmitted.append((rel, func, name, line))
    return module_level, unadmitted, counts


@pytest.mark.unit
class TestIndexingGroupsDoNotImportEachOther:
    def test_the_walk_found_all_three_groups(self):
        core = _members(_is_core_member)
        opensearch = _members(_is_opensearch_member)
        vector = _members(_is_vector_member)
        assert any(f.endswith("sqsBucketSync.py") for f in core), core
        assert any(f.endswith("osFileIndexer.py") for f in opensearch), opensearch
        assert any(f.endswith("vectorIndexer.py") for f in vector), vector

    def test_core_indexing_never_imports_either_search_family_at_module_level(self):
        module_level, unadmitted, counts = _partition(
            _members(_is_core_member), OPENSEARCH_FAMILY + VECTOR_FAMILY)
        assert module_level == [], (
            "A core indexing handler imports a search family at module level. Core indexing runs in "
            f"every deployment, so this import fails at cold start wherever that family is off: {module_level}"
        )
        assert unadmitted == [], (
            "A core indexing function imports a search family without an entry in "
            f"_ADMITTED_CROSSINGS: {unadmitted}"
        )
        over = {k: (n, _ADMITTED_CROSSINGS[k]) for k, n in counts.items() if n > _ADMITTED_CROSSINGS[k]}
        assert over == {}, f"admitted crossing exceeded its bound (found, admitted): {over}"

    def test_opensearch_family_never_imports_vector_search(self):
        hits = [
            (rel, func, name, line)
            for rel in _members(_is_opensearch_member)
            for func, name, line in _crossings(rel, VECTOR_FAMILY)
        ]
        assert hits == [], (
            "An OpenSearch indexer or search handler imports the vector-search add-on. A deployment "
            "with vector search disabled still builds these functions, so this import fails at cold "
            f"start there: {hits}"
        )

    def test_neither_search_family_imports_core_indexing(self):
        # The core feeds the families through SNS/SQS; a family reaching back into the core handlers
        # would couple it to bucket-sync internals that the other family and the storage stack own.
        hits = [
            (rel, func, name, line)
            for rel in _members(_is_opensearch_member) + _members(_is_vector_member)
            for func, name, line in _crossings(rel, CORE_FAMILY)
        ]
        assert hits == [], f"A search family imports the core indexing handlers: {hits}"

    def test_vector_family_imports_opensearch_only_inside_admitted_functions(self):
        module_level, unadmitted, counts = _partition(_members(_is_vector_member), OPENSEARCH_FAMILY)
        assert module_level == [], (
            "A vector-search module imports the OpenSearch family at module level. A deployment "
            "without an OpenSearch mode still builds the vector functions, so this import fails at "
            f"cold start there. Move it inside the function that needs it and gate it: {module_level}"
        )
        assert unadmitted == [], (
            "A vector-search function imports the OpenSearch family without an entry in "
            f"_ADMITTED_CROSSINGS: {unadmitted}"
        )
        over = {k: (n, _ADMITTED_CROSSINGS[k]) for k, n in counts.items() if n > _ADMITTED_CROSSINGS[k]}
        assert over == {}, f"admitted crossing exceeded its bound (found, admitted): {over}"

    def test_every_admitted_crossing_is_still_present(self):
        # Positive control: each exemption names a real site, so the detector is shown to fire and a
        # stale entry cannot hide a future crossing.
        found = set()
        for rel in _members(_is_vector_member):
            found.update((rel, func) for func, _n, _l in _crossings(rel, OPENSEARCH_FAMILY) if func)
        for rel in _members(_is_core_member):
            found.update((rel, func) for func, _n, _l in _crossings(rel, OPENSEARCH_FAMILY + VECTOR_FAMILY) if func)
        for key in _ADMITTED_CROSSINGS:
            assert key in found, (
                f"{key} is exempted but no such import exists any more; drop the entry so the "
                "exemption cannot hide a future crossing"
            )
