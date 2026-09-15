#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""`kubernetes_utils` must define each function once, and its functions must not call in a cycle.

`cleanup_completed_job` was defined twice. Python keeps the LAST definition, and that one delegated to
`delete_job`, which delegates back to `cleanup_completed_job` — so cleaning up a finished job recursed
forever, making a `check_job_status` call to the Kubernetes API on every turn. PIPELINE_END therefore ran
until its five-minute Step Functions task timeout and the state machine failed with `States.Timeout`,
after the pipeline had already converted the file successfully.

Nothing about the source reads as wrong at either site. Each function is individually reasonable; the
defect exists only in the pair, several hundred lines apart, and a duplicate definition is not a syntax
error, not a lint error by default, and produces no warning at import.

Two assertions, both written in their general form rather than naming the two functions involved, because
the file has 2,000 lines and this is a mistake that can recur anywhere in it:

  * no module-level function is defined more than once, so no definition silently shadows another;
  * the module-level call graph is acyclic, so no pair or longer chain can recurse without a base case.

Asserted statically with `ast`. Calling `cleanup_completed_job` to observe the recursion would need a
live cluster, and the recursion is not a `RecursionError` that a test could catch quickly — each turn
performs network I/O, which is exactly why it presented as a timeout rather than as a crash.
"""

import ast
import os

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SOURCE_PATH = os.path.join(_LAMBDA_DIR, "kubernetes_utils.py")

with open(_SOURCE_PATH, encoding="utf-8") as _fh:
    TREE = ast.parse(_fh.read())

MODULE_FUNCTIONS = [n for n in TREE.body if isinstance(n, ast.FunctionDef)]
FUNCTION_NAMES = {n.name for n in MODULE_FUNCTIONS}


def _direct_calls(node):
    """Module-level functions this function calls by bare name, excluding itself."""
    return {
        c.func.id
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in FUNCTION_NAMES
    } - {node.name}


class TestModuleShapeIsAsserted:
    def test_the_module_defines_functions_to_analyse(self):
        """The control. Both assertions below are vacuously true for a module with no functions, and a
        parse that silently produced an empty body would make this file prove nothing. Only
        module-level definitions count — most of the file's functions are nested helpers, which are
        not reachable by name from another function and so cannot take part in the cycle under test."""
        assert len(MODULE_FUNCTIONS) >= 12, (
            f"only found {len(MODULE_FUNCTIONS)} module-level functions"
        )
        assert "cleanup_completed_job" in FUNCTION_NAMES
        assert "delete_job" in FUNCTION_NAMES


class TestNoDefinitionShadowsAnother:
    def test_no_module_level_function_is_defined_twice(self):
        seen, duplicated = {}, {}
        for node in MODULE_FUNCTIONS:
            if node.name in seen:
                duplicated.setdefault(node.name, [seen[node.name]]).append(node.lineno)
            seen[node.name] = node.lineno
        assert duplicated == {}, (
            "a later definition silently replaces an earlier one, so which body runs is decided by "
            f"file order rather than by anything at the call site: {duplicated}"
        )


class TestTheCallGraphTerminates:
    def test_no_cycle_exists_among_module_level_functions(self):
        graph = {n.name: _direct_calls(n) for n in MODULE_FUNCTIONS}

        # Iterative depth-first search with an explicit stack; the recursion under test is in the
        # subject, and using Python recursion to find it would cap the detectable chain length.
        #
        # The sibling scan must CONTINUE past a child that is already finished (BLACK) or that closes a
        # cycle (GREY), and pop only once the iterator is exhausted. A version that popped as soon as
        # one child failed to advance the search abandoned every remaining child of that node — and
        # because `check_job_status` sorts first among `cleanup_completed_job`'s callees and was already
        # BLACK from an earlier root, the `delete_job` edge that closes the cycle was never examined.
        # That version passed against the restored defect, which is how the flaw was found.
        cycles = []
        WHITE, GREY, BLACK = 0, 1, 2
        colour = dict.fromkeys(graph, WHITE)
        for root in graph:
            if colour[root] != WHITE:
                continue
            colour[root] = GREY
            stack = [(root, iter(sorted(graph[root])), [root])]
            while stack:
                name, children, path = stack[-1]
                pushed = False
                for child in children:
                    if colour.get(child) == GREY:
                        cycles.append(" -> ".join(path[path.index(child) :] + [child]))
                        continue
                    if colour.get(child, BLACK) == WHITE:
                        colour[child] = GREY
                        stack.append((child, iter(sorted(graph[child])), path + [child]))
                        pushed = True
                        break
                if not pushed:
                    colour[name] = BLACK
                    stack.pop()

        assert cycles == [], (
            "these functions call each other in a loop with no base case; each turn performs "
            f"Kubernetes API calls, so it presents as a timeout rather than a RecursionError: {cycles}"
        )

    def test_the_cleanup_pair_specifically_runs_one_way(self):
        """Named explicitly as well, because this is the pair that was broken and the direction is the
        part that matters: delete_job is documented as delegating to the enhanced cleanup, so the
        dependency is allowed in that direction only."""
        cleanup = next(n for n in MODULE_FUNCTIONS if n.name == "cleanup_completed_job")
        delete = next(n for n in MODULE_FUNCTIONS if n.name == "delete_job")
        assert "cleanup_completed_job" in _direct_calls(delete)
        assert "delete_job" not in _direct_calls(cleanup)

    def test_the_surviving_cleanup_deletes_the_job_itself(self):
        """The positive half. Breaking the cycle must not have left a cleanup that deletes nothing —
        the copy that was removed only ever delegated, so the one kept has to perform the deletion."""
        cleanup = next(n for n in MODULE_FUNCTIONS if n.name == "cleanup_completed_job")
        attribute_calls = {
            c.func.attr
            for c in ast.walk(cleanup)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
        }
        assert "delete_namespaced_job" in attribute_calls
