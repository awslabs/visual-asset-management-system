# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The vector-search family and the OpenSearch family of ``backend/backend`` do not import each other.

Vector search (``handlers/vectorsearch``, ``common/vectorsearch``, ``models/vectorsearch``) is an
add-on: gated on ``app.vectorSearch.enabled``, stored in its own DynamoDB table, fed by its own SQS
queue and EventBridge rule, and removed from a deployment wholesale when the flag is off. OpenSearch
(``handlers/indexing``, ``handlers/search``) is gated on the OpenSearch flags and is likewise absent
from a deployment without an OpenSearch mode. Either family can be deployed without the other, so a
module-level import across the boundary makes the surviving family fail at COLD START -- a ``500`` on
every request, invisible to synth and to a test suite that imports both.

The CDK side of the same boundary (no cross-wired queue, no cross-gated function) is pinned by
``infra/test/platform/t1VectorIndexingWiring.test.ts``. This guard covers the Python side, which
that synth cannot see.

Shared ground is deliberate and stays out of scope: both families import ``common/indexing``
(document ids, file enumeration -- "what is an asset file" defined once), ``common/databaseAccess``,
and the rest of ``common``. Only an import that names the OTHER family's own packages is a hit.

**One admitted crossing, and it is function-scoped.** ``vectorSearchService._opensearch_step``
imports ``handlers.search.search`` INSIDE the function, after ``OPENSEARCH_DISABLED`` has been ruled
out, to enrich hits from OpenSearch when an OpenSearch mode is on. A deployment without OpenSearch
never executes that import, so ``opensearch-py`` is never loaded there. The exemption below names
the function and admits one site, so the same import at module level -- or a second function doing
it -- fails here.
"""

import ast
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"

VECTOR_FAMILY = ("handlers.vectorsearch", "common.vectorsearch", "models.vectorsearch")
OPENSEARCH_FAMILY = ("handlers.indexing", "handlers.search", "opensearchpy")

# The directory that OWNS each family. A file under one of these is a member of that family.
VECTOR_DIRS = ("handlers/vectorsearch", "common/vectorsearch")
VECTOR_FILES = ("models/vectorsearch.py",)
OPENSEARCH_DIRS = ("handlers/indexing", "handlers/search")

# (relative file, function) -> admitted count of imports of the other family INSIDE that function.
# An entry names the function, never the file, and is an upper bound (converting a site keeps the
# guard green).
_ADMITTED_CROSSINGS: Dict[Tuple[str, str], int] = {
    ("handlers/vectorsearch/vectorSearchService.py", "_opensearch_step"): 1,
}


def _imported_names(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def _crosses(name: str, other_family: Tuple[str, ...]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in other_family)


def _is_vector_member(rel: str) -> bool:
    return rel in VECTOR_FILES or any(rel.startswith(d + "/") for d in VECTOR_DIRS)


def _is_opensearch_member(rel: str) -> bool:
    return any(rel.startswith(d + "/") for d in OPENSEARCH_DIRS)


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


def _members(predicate) -> List[str]:
    return sorted(
        str(p.relative_to(BACKEND_ROOT)).replace("\\", "/")
        for p in BACKEND_ROOT.rglob("*.py")
        if predicate(str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"))
    )


@pytest.mark.unit
class TestIndexerFamiliesDoNotImportEachOther:
    def test_the_walk_found_both_families(self):
        vector = _members(_is_vector_member)
        opensearch = _members(_is_opensearch_member)
        assert any(f.endswith("vectorIndexer.py") for f in vector), vector
        assert any(f.endswith("fileIndexer.py") for f in opensearch), opensearch

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

    def test_vector_family_imports_opensearch_only_inside_admitted_functions(self):
        module_level = []
        unadmitted = []
        counts: Dict[Tuple[str, str], int] = {}
        for rel in _members(_is_vector_member):
            for func, name, line in _crossings(rel, OPENSEARCH_FAMILY):
                if func is None:
                    module_level.append((rel, name, line))
                    continue
                key = (rel, func)
                counts[key] = counts.get(key, 0) + 1
                if key not in _ADMITTED_CROSSINGS:
                    unadmitted.append((rel, func, name, line))
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

    def test_the_admitted_crossing_is_still_present(self):
        # Positive control: the exemption names a real site, so the detector is shown to fire.
        found = {
            (rel, func): 1
            for rel in _members(_is_vector_member)
            for func, _name, _line in _crossings(rel, OPENSEARCH_FAMILY)
            if func is not None
        }
        for key in _ADMITTED_CROSSINGS:
            assert key in found, (
                f"{key} is exempted but no such import exists any more; drop the entry so the "
                "exemption cannot hide a future crossing"
            )
