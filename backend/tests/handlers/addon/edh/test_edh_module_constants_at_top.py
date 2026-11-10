# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every module-level constant of the EDH add-on's backend code and backend tests is declared before the
module's first function or class definition.

A constant is a module-level assignment, plain or annotated, to a name matching ``^_?[A-Z][A-Z0-9_]*$``,
including one directly in the body of a module-level ``if``, ``try`` or ``with``. The one exception is a
constant whose value reads a function or class its module defines when the module is imported, directly or
through other module-level names: it cannot precede that definition, so it directly follows the last
definition it needs, with no other definition between them. A lambda's body is read only when the lambda is
called, so a constant that names a definition only there is not excepted; a function the constant calls at
import needs the definitions its body reads too.

The scope is the EDH source (``backend/handlers/addon/edh/``, its ``common/`` package included, and
``backend/models/addon/edh.py``) and the EDH tests (``tests/handlers/addon/edh/``); the rest of the backend is
not held to the rule.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
BACKEND_SOURCE = REPO_ROOT / "backend" / "backend"
BACKEND_TESTS = REPO_ROOT / "backend" / "tests"
# The EDH source: the add-on's handler package, common/ among it, and its models.
EDH_SOURCE_DIR = BACKEND_SOURCE / "handlers" / "addon" / "edh"
EDH_MODELS = BACKEND_SOURCE / "models" / "addon" / "edh.py"
# The EDH tests: the add-on's test directory.
EDH_TESTS_DIR = BACKEND_TESTS / "handlers" / "addon" / "edh"
# Floors under the module counts, so a scan that reads too little cannot pass: the source holds 8 entry
# points and 9 common modules beside the models and the package files.
MIN_SOURCE_MODULES = 17
MIN_TEST_MODULES = 60
CONSTANT_NAME = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# Module-level compound statements whose direct bodies count as module level.
BLOCKS = (ast.If, ast.Try, ast.TryStar, ast.With)
# Expressions whose own variables shadow module names.
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
# Modules written for the rule, each with the constants it reports and the constants it admits.
SYNTHETIC_MODULES = {
    "a late constant that reads no definition is reported": (
        "import os\n\n\ndef helper():\n    return 1\n\n\nLATE = os.sep\n",
        ["synthetic.py:8 LATE"], []),
    "a late constant built by a function above it is admitted": (
        "def helper():\n    return 1\n\n\nBUILT = helper()\n",
        [], ["synthetic.py:5 BUILT"]),
    "a late constant reading a definition through another constant is admitted": (
        "class Shape:\n    pass\n\n\nSHAPES = (Shape(),)\nFIRST = SHAPES[0]\n",
        [], ["synthetic.py:5 SHAPES", "synthetic.py:6 FIRST"]),
    "a late constant reading a definition through a lowercase module name is admitted": (
        "def build():\n    return object()\n\n\nbuilt = build()\nFIELD = built\n",
        [], ["synthetic.py:6 FIELD"]),
    "a late constant reading only an imported name is reported": (
        "from os import path\n\n\ndef helper():\n    return 1\n\n\nJOINED = path.join('a', 'b')\nfrom os import sep\n"
        "SEPARATOR = sep\n",
        ["synthetic.py:8 JOINED", "synthetic.py:10 SEPARATOR"], []),
    "a constant directly in a module-level if, elif, else, try, except, finally or with is checked, one nested "
    "deeper is not": (
        "def helper():\n    return 1\n\n\nif True:\n    FLAG = 1\nelif False:\n    OTHER_FLAG = 2\nelse:\n"
        "    FALLBACK_FLAG = 3\ntry:\n    OPTIONAL = 2\nexcept ImportError:\n    OPTIONAL = None\nelse:\n"
        "    LOADED = 5\nfinally:\n    CLEANED = 6\nwith context():\n    OPENED = 3\nif True:\n    if True:\n"
        "        NESTED = 4\nelse:\n    if True:\n        DEEPER = 7\n",
        ["synthetic.py:6 FLAG", "synthetic.py:8 OTHER_FLAG", "synthetic.py:10 FALLBACK_FLAG",
         "synthetic.py:12 OPTIONAL", "synthetic.py:14 OPTIONAL", "synthetic.py:16 LOADED", "synthetic.py:18 CLEANED",
         "synthetic.py:20 OPENED"],
        []),
    "a definition directly in a module-level if or try is a definition": (
        "try:\n    from os import fspath\nexcept ImportError:\n    def fspath(path):\n        return path\n\n\n"
        "LATE = 1\n",
        ["synthetic.py:8 LATE"], []),
    "an annotated or unpacked constant is checked, a lowercase name is not": (
        "def helper():\n    return 1\n\n\nTYPED: int = 1\nLOW, HIGH = 1, 2\nlowercase = 3\n",
        ["synthetic.py:5 TYPED", "synthetic.py:6 LOW", "synthetic.py:6 HIGH"], []),
    "a constant before the first definition is not reported": (
        "TOP = 1\n\n\ndef helper():\n    return TOP\n",
        [], []),
    "a late constant naming a definition only in a lambda's body is reported": (
        "def helper():\n    return 1\n\n\nBUILDERS = {'one': lambda: helper()}\n",
        ["synthetic.py:5 BUILDERS"], []),
    "a late constant reading a definition in a lambda's default is admitted": (
        "def helper():\n    return 1\n\n\nBOUND = lambda value=helper(): value\n",
        [], ["synthetic.py:5 BOUND"]),
    "a comprehension's variable is not the definition it is named like": (
        "def row():\n    return [1]\n\n\nROWS = [row for row in range(3)]\n",
        ["synthetic.py:5 ROWS"], []),
    "a comprehension's first iterable is read outside it": (
        "def row():\n    return [1]\n\n\nROWS = [row for row in row()]\n",
        [], ["synthetic.py:5 ROWS"]),
    "a module name rebound to a definition's result after the constant reads it is not read": (
        "def helper():\n    return 1\n\n\nvalue = 1\nLATE = value\nvalue = helper()\nLATER = LATE\n",
        ["synthetic.py:6 LATE", "synthetic.py:8 LATER"], []),
    "a late constant with an unrelated definition after the one it needs is reported": (
        "class Shape:\n    pass\n\n\ndef unrelated():\n    return 2\n\n\nSHAPES = (Shape,)\n",
        ["synthetic.py:9 SHAPES"], []),
    "a late constant directly after a module name it needs is admitted": (
        "def build():\n    return object()\n\n\ndef unrelated():\n    return 2\n\n\nbuilt = build()\nFIELD = built\n",
        [], ["synthetic.py:10 FIELD"]),
    "a function a late constant calls needs the definitions its body reads": (
        "def outer():\n    return inner()\n\n\ndef inner():\n    return 1\n\n\nBUILT = outer()\n",
        [], ["synthetic.py:9 BUILT"]),
    "a function a late constant names without calling it needs nothing its body reads": (
        "def outer():\n    return inner()\n\n\ndef inner():\n    return 1\n\n\nHANDLERS = (outer,)\n",
        ["synthetic.py:9 HANDLERS"], []),
}


def _module_level(tree):
    """The module's statements, followed by those directly in the body of each module-level if, try or with."""
    for stmt in tree.body:
        yield stmt
        if isinstance(stmt, BLOCKS):
            yield from _block_body(stmt)


def _block_body(stmt):
    yield from stmt.body
    for handler in getattr(stmt, "handlers", ()):
        yield from handler.body
    orelse = getattr(stmt, "orelse", [])
    if (isinstance(stmt, ast.If) and len(orelse) == 1 and isinstance(orelse[0], ast.If)
            and orelse[0].col_offset == stmt.col_offset):
        yield from _block_body(orelse[0])  # an elif branch, which starts in its if's column
    else:
        yield from orelse
    yield from getattr(stmt, "finalbody", ())


def _assigned_names(stmt):
    """The names a plain or annotated assignment binds; nothing for any other statement."""
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        targets = [stmt.target]
    else:
        return []
    return [node.id for target in targets for node in ast.walk(target)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)]


def _import_time_reads(expression):
    """The module names an expression reads when it is evaluated, and those of them it calls. Of a lambda only
    the defaults are evaluated; a comprehension's variables are its own, and only its first iterable is
    evaluated outside it."""
    reads, calls = set(), set()

    def visit(node, local):
        if isinstance(node, ast.Lambda):
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                visit(default, local)
            return
        if isinstance(node, COMPREHENSIONS):
            first, *rest = node.generators
            visit(first.iter, local)
            inner = local | {name.id for generator in node.generators for name in ast.walk(generator.target)
                             if isinstance(name, ast.Name)}
            results = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            for part in [*first.ifs, *(part for other in rest for part in (other.iter, *other.ifs)), *results]:
                visit(part, inner)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in local:
            reads.add(node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id not in local:
            calls.add(node.func.id)
        for child in ast.iter_child_nodes(node):
            visit(child, local)

    visit(expression, frozenset())
    return reads, calls


def _body_reads(definition):
    """The names a definition's body reads, each counted as called: a constant that calls the definition at
    import may run any of it."""
    names = {node.id for node in ast.walk(definition) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    return names, names


def check_source(source, label):
    """The constants of one module declared after its first definition, as ``label:line NAME``: those the
    rule reports, and those the exception admits because their value reads a definition of the module at
    import and no definition lies between the constant and the last definition or module name it needs."""
    statements = list(_module_level(ast.parse(source, filename=label)))
    definitions = {}
    for stmt in statements:
        if isinstance(stmt, DEFINITIONS):
            definitions.setdefault(stmt.name, []).append(stmt.lineno)
    if not definitions:
        return [], []
    definition_lines = sorted(line for lines in definitions.values() for line in lines)
    bindings = {}
    constants = []
    for stmt in statements:
        for name in _assigned_names(stmt):
            bindings.setdefault(name, []).append((stmt.lineno, stmt.value))
            if CONSTANT_NAME.match(name):
                constants.append((stmt.lineno, name, stmt.value))

    def needs(value, line):
        """The lines of the definitions and module-level bindings ``value``, evaluated at ``line``, needs,
        and whether a definition is among them. A name is the binding it has when it is read."""
        needed, reads_a_definition = set(), False
        pending, seen = [(*_import_time_reads(value), line)], set()
        while pending:
            reads, calls, at = pending.pop()
            for name in reads:
                called = name in calls
                if (name, called, at) in seen:
                    continue
                seen.add((name, called, at))
                for defined_at in definitions.get(name, ()):
                    if defined_at < at:
                        needed.add(defined_at)
                        reads_a_definition = True
                        if called:
                            definition = next(stmt for stmt in statements
                                              if isinstance(stmt, DEFINITIONS) and stmt.lineno == defined_at)
                            pending.append((*_body_reads(definition), at))
                for bound_at, bound_value in bindings.get(name, ()):
                    if bound_at < at:
                        needed.add(bound_at)
                        pending.append((*_import_time_reads(bound_value), bound_at))
        return needed, reads_a_definition

    reported, admitted = [], []
    for line, name, value in constants:
        if line > definition_lines[0]:
            needed, reads_a_definition = needs(value, line)
            last = max(needed, default=0)
            close = not any(last < defined_at < line for defined_at in definition_lines)
            (admitted if reads_a_definition and close else reported).append(f"{label}:{line} {name}")
    return reported, admitted


def _python_files(directory):
    return sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


def _source_modules():
    return _python_files(EDH_SOURCE_DIR) + [EDH_MODELS]


def _test_modules():
    return _python_files(EDH_TESTS_DIR)


def _scan(paths):
    reported, admitted = [], []
    for path in paths:
        found = check_source(path.read_text(encoding="utf-8"), path.relative_to(REPO_ROOT).as_posix())
        reported += found[0]
        admitted += found[1]
    return reported, admitted


def _failure(reported):
    return ("Declare these constants before the module's first function or class definition; only a constant "
            "whose value reads a definition of its module at import may follow it, directly after the last "
            "definition it needs:\n" + "\n".join(reported))


@pytest.mark.unit
class TestEdhModuleConstantsPrecedeTheFirstDefinition:
    def test_every_constant_of_the_edh_source_precedes_the_first_definition(self):
        reported, _admitted = _scan(_source_modules())
        assert reported == [], _failure(reported)

    def test_every_constant_of_the_edh_tests_precedes_the_first_definition(self):
        reported, _admitted = _scan(_test_modules())
        assert reported == [], _failure(reported)


@pytest.mark.unit
class TestTheScanReadsItsScope:
    """Positive controls: the scan reads every module it is meant to, and the exception is exercised, so the
    rule tests cannot pass by reading nothing."""

    def test_the_source_scope_holds_the_handler_package_its_common_modules_and_the_models(self):
        modules = _source_modules()
        assert len(modules) >= MIN_SOURCE_MODULES, [path.name for path in modules]
        assert EDH_MODELS.is_file()
        assert any(path.parent.name == "common" for path in modules)

    def test_the_test_scope_holds_the_edh_test_directory(self):
        modules = _test_modules()
        assert len(modules) >= MIN_TEST_MODULES, len(modules)
        assert Path(__file__).resolve() in modules

    def test_the_exception_admits_a_constant_in_scope(self):
        _reported, admitted = _scan(_source_modules() + _test_modules())
        assert len(admitted) > 0, ("No constant in scope is admitted by the exception, so it is no longer "
                                   "exercised: edhClient.py's _PROFILES, built from the _Profile class above "
                                   "it, was one.")


@pytest.mark.unit
class TestTheRuleOnSyntheticModules:
    """Negative controls: on modules written for it, the checker reports a late constant that needs no
    definition of its module at import, or that an unneeded definition separates from what it needs, and
    admits one that directly follows the last definition it needs."""

    @pytest.mark.parametrize("case", sorted(SYNTHETIC_MODULES))
    def test_the_checker_reports_and_admits_what_the_rule_says(self, case):
        source, reported, admitted = SYNTHETIC_MODULES[case]
        assert check_source(source, "synthetic.py") == (reported, admitted)
