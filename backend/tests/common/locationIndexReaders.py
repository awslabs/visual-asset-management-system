# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Which Lambda handlers can query the asset table's location index (`AssetLocationGSI`).

A static reading of the backend source, standard library only, so the infra unit tests can run it
(`python3 backend/tests/common/locationIndexReaders.py` prints the JSON list of every reader handler) and
check that the role of every function whose handler is listed holds `dynamodb:Query` on that index
(`infra/test/storage/assetLocationIndexReaderGrants.test.ts`). The backend test
`test_location_index_readers.py` keeps the reading honest.

The reading: a function that names `LOCATION_INDEX_NAME` (or the index name as a string) queries the index;
so does every function that calls or refers to one that does, through a name defined in its module, a
name it imports, or an attribute of a module it imports. A method call on any other object is matched to
the methods of that name in the same module. A handler `<module>.<function>` reads the index when its
function, or its module's top-level code, reaches one of those functions. It over-approximates (a branch
that only runs before the index is ready counts too), which is what a grant needs.
"""

import ast
import functools
import json
import os
import sys
from typing import Dict, FrozenSet, Iterable, Optional, Set, Tuple

BACKEND_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
INDEX_CONSTANT = "LOCATION_INDEX_NAME"
INDEX_NAME = "AssetLocationGSI"
MODULE_BODY = "<module>"

Node = Tuple[str, str]  # (module, function or Class.method or MODULE_BODY)


def _module_name(path: str, root: str) -> str:
    rel = os.path.relpath(path, root)[:-3]
    parts = rel.split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@functools.lru_cache(maxsize=4)
def _modules(root: str = BACKEND_ROOT) -> Dict[str, ast.Module]:
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as handle:
                    try:
                        found[_module_name(path, root)] = ast.parse(handle.read(), path)
                    except SyntaxError:
                        continue
    return found


class _Scope:
    """The names one module defines and imports."""

    def __init__(self, module: str, tree: ast.Module, known: Set[str]):
        self.module = module
        self.functions: Set[str] = set()
        self.methods: Dict[str, Set[str]] = {}
        self.imported: Dict[str, Node] = {}
        self.module_aliases: Dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    target = f"{node.module}.{alias.name}"
                    if target in known:
                        self.module_aliases[alias.asname or alias.name] = target
                    else:
                        self.imported[alias.asname or alias.name] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in known:
                        if alias.asname:
                            self.module_aliases[alias.asname] = alias.name
                        else:
                            self.module_aliases[alias.name] = alias.name
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions.add(node.name)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.methods.setdefault(item.name, set()).add(f"{node.name}.{item.name}")

    def _dotted(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._dotted(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def targets(self, body: ast.AST) -> Set[Node]:
        out: Set[Node] = set()
        for node in ast.walk(body):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in self.functions:
                    out.add((self.module, node.id))
                elif node.id in self.imported:
                    out.add(self.imported[node.id])
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                dotted = self._dotted(node.value)
                if dotted is not None and dotted in self.module_aliases:
                    out.add((self.module_aliases[dotted], node.attr))
                else:
                    out.update((self.module, m) for m in self.methods.get(node.attr, ()))
        return out


def _bodies(tree: ast.Module) -> Iterable[Tuple[str, ast.AST]]:
    """Each function and method body, and the module's top-level code without them."""
    top = ast.Module(body=[], type_ignores=[])
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{item.name}", item
        else:
            top.body.append(node)
    yield MODULE_BODY, top


def _names_the_index(body: ast.AST) -> bool:
    for node in ast.walk(body):
        if isinstance(node, ast.Name) and node.id == INDEX_CONSTANT:
            return True
        if isinstance(node, ast.Constant) and node.value == INDEX_NAME:
            return True
    return False


@functools.lru_cache(maxsize=4)
def index_readers(root: str = BACKEND_ROOT) -> FrozenSet[Node]:
    """Every function (module, name) that can query the location index."""
    modules = _modules(root)
    known = set(modules)
    edges: Dict[Node, Set[Node]] = {}
    readers: Set[Node] = set()
    for module, tree in modules.items():
        scope = _Scope(module, tree, known)
        for name, body in _bodies(tree):
            node = (module, name)
            edges[node] = scope.targets(body)
            if _names_the_index(body) and name != MODULE_BODY:
                readers.add(node)
    changed = True
    while changed:
        changed = False
        for node, targets in edges.items():
            if node not in readers and targets & readers:
                readers.add(node)
                changed = True
    return frozenset(readers)


def reader_handlers(handlers: Iterable[str], root: str = BACKEND_ROOT) -> Set[str]:
    """The Lambda handler strings (`module.function`) whose function or module body reaches the index."""
    readers = index_readers(root)
    out = set()
    for handler in handlers:
        module, _, function = handler.rpartition(".")
        if (module, function) in readers or (module, MODULE_BODY) in readers:
            out.add(handler)
    return out


def handler_entry_points(root: str = BACKEND_ROOT) -> Set[str]:
    """Every `module.function` a Lambda could name as its handler: the top-level functions of the handler
    modules."""
    out = set()
    for module, tree in _modules(root).items():
        if module.startswith("handlers."):
            out.update(f"{module}.{n.name}" for n in tree.body if isinstance(n, (ast.FunctionDef,
                                                                                 ast.AsyncFunctionDef)))
    return out


if __name__ == "__main__":
    # With handler strings on stdin (a JSON list), the readers among them; without, every handler module
    # function that reaches the index.
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    candidates = json.loads(raw) if raw.strip() else sorted(handler_entry_points())
    print(json.dumps(sorted(reader_handlers(candidates))))
