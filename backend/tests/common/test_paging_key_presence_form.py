# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""No paged read in ``backend/backend`` decides whether to continue from LastEvaluatedKey's VALUE.

``if 'LastEvaluatedKey' not in response: break`` is both the accurate DynamoDB contract -- the key is
OMITTED on the response that exhausted the result set, never set empty -- and the only form that stays
finite against an under-stubbed reader. ``MagicMock.get('LastEvaluatedKey')`` answers with a truthy
child mock forever, while ``'LastEvaluatedKey' in mock`` answers ``False``, so the value form does not
fail, it HANGS. One such loop ran the backend suite past 600 s against a 167 s baseline, and because a
timeout raises no assertion it names no test (backend/tests/CLAUDE.md, "A MagicMock never ends a
paging loop").

The runtime tests next to each module prove that each loop threads its cursor and terminates. This
one guards the FORM, for two reasons the runtime tests cannot cover:

* the HYBRID form (``lek = resp.get(...)`` followed by ``if not isinstance(lek, dict) or not lek``)
  does terminate against a mock, so no runtime assertion distinguishes it -- yet it is value-driven
  and reads as a licence for the plain value form beside it;
* a NEW paged loop added anywhere gets no runtime test for free, and the value form is the one that a
  copy-paste from older code produces.

**Scope is the whole of ``backend/backend``, discovered by walking the tree.** This guard used to
carry a hand-written module list plus a docstring claiming the only value-form sites left in the tree
were the Physna handlers. That claim was wrong in both directions -- the Physna handlers page on
presence, while ``workflowService`` and ``roleService`` (converted alongside this change) and
``executionService`` did not -- and it mis-scoped the follow-up for the next reader. A hand-written
list cannot say anything about the file nobody remembered to add, so the sites are now enumerated by
search and the list that remains is a list of *exceptions*, which is far smaller and fails loudly.

**An exception names the FUNCTIONS it covers and how many sites it admits, never a bare file.** A
file-wide exemption is a hole with no upper bound, and it was demonstrated as one: a brand-new plain
value-form loop appended to the exempted module left this guard green, while the identical loop in a
non-exempt module turned it red with the right message. The exempted module is the largest and most
paging-dense one in the backend -- exactly where the next copy-paste lands. Naming the function makes
a new loop anywhere else in that file fail; carrying the site count makes a second loop inside the
same function fail. Both are UPPER bounds, so converting a site keeps the guard green and nothing here
penalises a fix.

What is asserted, per file:

1.  no read decides its continuation from the key's VALUE, except in the functions a
    ``_KNOWN_REMAINING`` entry names, and no more often than that entry admits;
2.  a file that threads a cursor (assigns ``ExclusiveStartKey`` inside a loop) contains at least one
    presence check. This is the structural converse of (1) and catches a value-form loop written in a
    shape the detector in (1) does not model.

Reading the key's value is NOT itself the defect, and the detector deliberately does not flag it: a
bounded walk that stops part-way through a page has to read the server's key to emit a continuation
token, and ``executionService`` does exactly that in several places while terminating on presence. The
defect is using the value as the loop's continuation decision. ``_TOKEN_VALUE_READS`` below is the
control that keeps those legitimate reads out of the result -- without it this guard would have to
allowlist a whole clean file, which is how an allowlist starts hiding real hits.

A read paged by the boto3 paginator (``paginator.paginate(...)``) is invisible here by construction:
the cursor is threaded inside botocore, so there is no ``LastEvaluatedKey`` decision in the tree for
this guard to inspect. Such reads need their own treatment -- a ``PaginationConfig`` whose
``MaxItems`` is capped -- guarded per module beside the module's own tests (for example
``tests/handlers/pipelines/test_pipelineService_paging.py``).
"""

import ast
import os

import pytest

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))

_MODULE_SCOPE = "<module>"

# Sites that still decide continuation from the key's value, each with the reason it is not converted
# here. ``functions`` is what the entry covers and ``sites`` is the most it admits; both are upper
# bounds, so converting a site keeps this guard green and nothing here penalises a fix. A value-form
# loop in any other function of the same file, or a second one inside a named function, FAILS. When a
# site is converted, lower ``sites``; when the last one goes, delete the entry.
_KNOWN_REMAINING = {
    "handlers/workflows/executionService.py": {
        "functions": frozenset({"page_detail_metadata"}),
        "sites": 1,
        "reason":
            "The detail-metadata walk continues on `if step_last_key is not None` and threads a "
            "per-step cursor through an encoded NextToken; converting it means reworking that "
            "token's step/cursor precedence, which is out of this change's scope. Tracked as an "
            "open item.",
    },
}

_KNOWN_BAD_FORMS = '''
def plain_value(table, kwargs):
    while True:
        response = table.query(**kwargs)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key


def hybrid_isinstance(table, kwargs):
    while True:
        resp = table.query(**kwargs) or {}
        lek = resp.get("LastEvaluatedKey")
        if not isinstance(lek, dict) or not lek:
            return None
        kwargs["ExclusiveStartKey"] = lek


def named_variable(table):
    last_evaluated_key = None
    while True:
        scan_response = table.scan()
        last_evaluated_key = scan_response.get('LastEvaluatedKey')
        if not last_evaluated_key:
            break
'''

_GOOD_FORM = '''
def presence(table, kwargs):
    while True:
        response = table.query(**kwargs)
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
'''

# Legitimate reads of the key's VALUE: a bounded walk emitting a continuation token. Both terminate on
# presence. The detector must leave these alone or the tree-wide scan is unusable.
_TOKEN_VALUE_READS = '''
def bounded_walk(table, kwargs, cap):
    served = []
    while True:
        resp = table.query(**kwargs)
        for row in resp.get("Items", []):
            served.append(row)
            if len(served) >= cap:
                return row_key(row) or resp.get("LastEvaluatedKey")
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return None


def emit_token(resp, stopped_mid_page, last_row_key):
    next_key = last_row_key if stopped_mid_page else resp.get("LastEvaluatedKey")
    if next_key is not None:
        return encode(next_key)
    return None
'''


def _reads_key_value(node):
    """True if this subtree reads ``LastEvaluatedKey``'s value via ``.get(...)``."""
    return any(
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Attribute)
        and inner.func.attr == "get"
        and inner.args
        and isinstance(inner.args[0], ast.Constant)
        and inner.args[0].value == "LastEvaluatedKey"
        for inner in ast.walk(node)
    )


def _nested_scopes(node, prefix):
    """Every function scope below ``node``, qualified by the functions (and classes) enclosing it."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified = f"{prefix}.{child.name}" if prefix else child.name
            yield child, qualified
            yield from _nested_scopes(child, qualified)
        elif isinstance(child, ast.ClassDef):
            qualified = f"{prefix}.{child.name}" if prefix else child.name
            yield from _nested_scopes(child, qualified)
        else:
            yield from _nested_scopes(child, prefix)


def _scopes(tree):
    """``(scope, qualified name)`` for the module scope plus every function scope.

    A name cannot leak between functions, and every finding carries the function that holds it: an
    exemption has to name the loop it covers, and a line number moves with every edit above it while a
    function name does not.
    """
    yield tree, _MODULE_SCOPE
    yield from _nested_scopes(tree, "")


def function_names(source):
    """Every function in the module, qualified -- so an exemption naming one can be checked live."""
    return sorted(name for _, name in _scopes(ast.parse(source)) if name != _MODULE_SCOPE)


def _cursor_names(scope):
    """Names bound in this scope to an expression that reads the key's value."""
    names = set()
    for node in ast.walk(scope):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        if node.value is None or not _reads_key_value(node.value):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
    return names


def _is_cursor_valued(test, names):
    """True if this test reads the key's value directly or through one of ``names``."""
    if _reads_key_value(test):
        return True
    return any(isinstance(node, ast.Name) and node.id in names for node in ast.walk(test))


def _walk_scope(scope, names, in_loop, visit, where):
    """Walk one scope's statements, tracking loop nesting and not entering nested scopes."""
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        visit(child, in_loop, names, where)
        _walk_scope(child, names, in_loop or isinstance(child, (ast.While, ast.For, ast.AsyncFor)),
                    visit, where)


def value_form_sites(source):
    """``(function, line)`` for every loop whose continuation decision reads the key's VALUE.

    Two shapes count: a ``while`` whose test is cursor-valued, and an ``if`` inside a loop whose test
    is cursor-valued and whose body leaves the loop (``break`` / ``continue`` / ``return``). A read of
    the value that is not a continuation decision -- a bounded walk building a NextToken -- is not a
    defect and is not reported (see ``_TOKEN_VALUE_READS``).

    The enclosing function travels with the line so an exemption can name the loop it covers instead
    of the file that happens to hold it.
    """
    tree = ast.parse(source)
    hits = set()

    def visit(node, in_loop, names, where):
        if isinstance(node, ast.While) and _is_cursor_valued(node.test, names):
            hits.add((where, node.lineno))
        elif isinstance(node, ast.If) and in_loop and _is_cursor_valued(node.test, names):
            if any(isinstance(inner, (ast.Break, ast.Continue, ast.Return))
                   for inner in ast.walk(node)):
                hits.add((where, node.lineno))

    for scope, where in _scopes(tree):
        _walk_scope(scope, _cursor_names(scope), False, visit, where)
    return sorted(hits)


def presence_checks(source):
    """Lines holding a ``'LastEvaluatedKey' (not) in <expr>`` test -- the required form."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], (ast.In, ast.NotIn))
        and isinstance(node.left, ast.Constant)
        and node.left.value == "LastEvaluatedKey"
    )


def loop_cursor_writes(source):
    """Lines assigning ``ExclusiveStartKey`` inside a loop -- i.e. "this file pages"."""
    tree = ast.parse(source)
    hits = set()

    def visit(node, in_loop, names, where):
        if not (in_loop and isinstance(node, ast.Assign)):
            return
        for target in node.targets:
            if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) \
                    and target.slice.value == "ExclusiveStartKey":
                hits.add(node.lineno)
            elif isinstance(target, ast.Name) and target.id == "ExclusiveStartKey":
                hits.add(node.lineno)

    for scope, where in _scopes(tree):
        _walk_scope(scope, set(), False, visit, where)
    return sorted(hits)


def scan_tree(root):
    """Every ``.py`` file under ``root``, mapped to its paging form findings.

    The enumeration is a search, not a list: a module added, renamed, or moved is covered the moment
    it lands, which a hand-written list cannot do.
    """
    findings = {}
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name != "__pycache__"]
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(directory, filename)
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            findings[relative] = {
                "value_sites": value_form_sites(source),
                "presence": presence_checks(source),
                "loop_cursor_writes": loop_cursor_writes(source),
                "functions": function_names(source),
            }
    return findings


def unexempted_value_form_sites(findings, exemptions):
    """Value-form sites that no exemption covers, per file.

    A site is covered only when its file has an entry AND the function holding it is one the entry
    names. An entry therefore cannot cover a loop somebody adds elsewhere in the same file, which is
    the hole a file-wide exemption left open.
    """
    offending = {}
    for path, found in findings.items():
        exemption = exemptions.get(path)
        covered = exemption["functions"] if exemption else frozenset()
        unexempted = sorted(site for site in found["value_sites"] if site[0] not in covered)
        if unexempted:
            offending[path] = unexempted
    return offending


def exemptions_over_their_bound(findings, exemptions):
    """Exempt files carrying MORE value-form sites than their entry admits.

    An UPPER bound: converting a site leaves this empty, while a second value-form loop written inside
    an already-named function -- which the function check alone cannot see -- does not.
    """
    over = {}
    for path, exemption in exemptions.items():
        sites = findings.get(path, {}).get("value_sites", [])
        if len(sites) > exemption["sites"]:
            over[path] = (sites, exemption["sites"])
    return over


_FINDINGS = scan_tree(_BACKEND_ROOT)
_PAGING_FILES = sorted(path for path, found in _FINDINGS.items() if found["loop_cursor_writes"])


def _write(tmp_path, name, source):
    target = tmp_path / "pkg" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return f"pkg/{name}"


@pytest.mark.unit
class TestTheDetectorFindsTheThing:
    """Positive controls. A "no value form anywhere" assertion is worthless without them."""

    def test_all_three_shipped_value_forms_are_detected(self):
        """The plain form, the hybrid isinstance form, and the named-variable form."""
        found = {function for function, _ in value_form_sites(_KNOWN_BAD_FORMS)}
        assert {"plain_value", "hybrid_isinstance", "named_variable"} <= found, found

    def test_a_value_form_is_not_mistaken_for_a_presence_check(self):
        assert presence_checks(_KNOWN_BAD_FORMS) == []

    def test_the_presence_form_is_recognised_and_flags_nothing(self):
        assert value_form_sites(_GOOD_FORM) == []
        assert len(presence_checks(_GOOD_FORM)) == 1

    def test_a_site_carries_the_function_that_holds_it(self):
        """Attribution is what lets an exemption name a loop instead of a whole file."""
        sites = dict(value_form_sites(_KNOWN_BAD_FORMS))
        assert "plain_value" in sites, sites
        # The line reported sits inside that function's body, so the pairing is not accidental.
        body = _KNOWN_BAD_FORMS.splitlines()
        assert "last_key" in body[sites["plain_value"] - 1], body[sites["plain_value"] - 1]

    def test_a_nested_function_is_not_attributed_to_its_parent(self):
        """A loop tucked inside the exempted function is a different site, not a covered one."""
        source = '''
def outer(table, kwargs):
    def inner():
        while True:
            resp = table.query(**kwargs)
            key = resp.get("LastEvaluatedKey")
            if not key:
                break
            kwargs["ExclusiveStartKey"] = key
    return inner
'''
        assert [function for function, _ in value_form_sites(source)] == ["outer.inner"]

    def test_reading_the_key_to_emit_a_token_is_not_a_termination(self):
        """The false-positive control: without it the tree scan would have to allowlist clean files."""
        # The fixture really does read the value -- otherwise it would prove nothing.
        assert _reads_key_value(ast.parse(_TOKEN_VALUE_READS))
        assert value_form_sites(_TOKEN_VALUE_READS) == []

    def test_a_paging_loop_is_recognised_as_paging(self):
        assert loop_cursor_writes(_GOOD_FORM) != []
        assert loop_cursor_writes(_KNOWN_BAD_FORMS) != []

    def test_the_tree_scan_reports_a_value_form_planted_in_a_nested_file(self, tmp_path):
        """Proves the whole path -- walk, parse, report -- and not just the AST predicate."""
        relative = _write(tmp_path, "offender.py", _KNOWN_BAD_FORMS)

        found = scan_tree(str(tmp_path))

        assert relative in found, sorted(found)
        reported = {function for function, _ in found[relative]["value_sites"]}
        assert {"plain_value", "hybrid_isinstance", "named_variable"} <= reported, found[relative]
        assert found[relative]["presence"] == []

    def test_the_tree_scan_reports_paging_that_never_checks_presence(self, tmp_path):
        relative = _write(tmp_path, "offender.py", _KNOWN_BAD_FORMS)

        found = scan_tree(str(tmp_path))

        assert found[relative]["loop_cursor_writes"] != []
        assert found[relative]["presence"] == []

    def test_the_tree_scan_reports_a_clean_file_as_clean(self, tmp_path):
        """The negative control for the two above: the same scan must clear the good form."""
        relative = _write(tmp_path, "clean.py", _GOOD_FORM)

        found = scan_tree(str(tmp_path))

        assert found[relative]["value_sites"] == []
        assert found[relative]["presence"] != []
        assert found[relative]["loop_cursor_writes"] != []


@pytest.mark.unit
class TestAnExemptionCannotCoverANewLoop:
    """The hole this guard shipped with: a whole-FILE exemption made the NEXT value-form loop in the
    backend's most paging-dense module invisible, while the same loop anywhere else failed. These run
    the classification over synthetic findings, so they keep holding as the real tree changes -- and
    they are what turns "the exemption is narrower now" into something that fails when it widens."""

    EXEMPT = "handlers/exempted.py"
    ENTRY = {EXEMPT: {"functions": frozenset({"covered_walk"}), "sites": 1, "reason": "tracked"}}

    def _findings(self, *sites):
        return {
            self.EXEMPT: {
                "value_sites": sorted(sites),
                "presence": [3],
                "loop_cursor_writes": [7],
                "functions": ["covered_walk", "added_later"],
            },
        }

    def test_the_named_loop_is_covered(self):
        findings = self._findings(("covered_walk", 10))

        assert unexempted_value_form_sites(findings, self.ENTRY) == {}
        assert exemptions_over_their_bound(findings, self.ENTRY) == {}

    def test_a_new_loop_elsewhere_in_the_exempt_file_is_reported(self):
        """The proven hole: a value-form loop appended to the exempted module must fail the guard."""
        findings = self._findings(("covered_walk", 10), ("added_later", 99))

        assert unexempted_value_form_sites(findings, self.ENTRY) == {
            self.EXEMPT: [("added_later", 99)]}

    def test_a_module_level_loop_in_the_exempt_file_is_reported(self):
        findings = self._findings(("<module>", 4))

        assert unexempted_value_form_sites(findings, self.ENTRY) == {self.EXEMPT: [("<module>", 4)]}

    def test_a_second_loop_inside_the_named_function_breaks_the_bound(self):
        findings = self._findings(("covered_walk", 10), ("covered_walk", 40))

        # The function check cannot see this one -- the entry names that function. The site count is
        # what fails, which is why the entry carries one.
        assert unexempted_value_form_sites(findings, self.ENTRY) == {}
        assert self.EXEMPT in exemptions_over_their_bound(findings, self.ENTRY)

    def test_converting_the_named_loop_penalises_nobody(self):
        """Both checks are upper bounds, so a fix keeps the guard green with the entry still in place."""
        findings = self._findings()

        assert unexempted_value_form_sites(findings, self.ENTRY) == {}
        assert exemptions_over_their_bound(findings, self.ENTRY) == {}

    def test_a_file_with_no_entry_is_covered_by_nobody_elses(self):
        findings = {
            "handlers/other.py": {
                "value_sites": [("covered_walk", 5)],
                "presence": [],
                "loop_cursor_writes": [6],
                "functions": ["covered_walk"],
            },
        }

        # Same function name as the exempt file's covered loop: an exemption is per FILE and function,
        # never a licence for that name anywhere in the tree.
        assert unexempted_value_form_sites(findings, self.ENTRY) == {
            "handlers/other.py": [("covered_walk", 5)]}


@pytest.mark.unit
class TestTheScanReachedTheTree:
    """Anti-vacuity: an empty result must mean "clean", never "nothing was read"."""

    def test_the_backend_tree_was_found(self):
        assert os.path.isdir(_BACKEND_ROOT), (
            f"{_BACKEND_ROOT} is not a directory; if the package moved, this guard is scanning "
            "nothing and every assertion below passes vacuously")

    def test_enough_modules_were_scanned(self):
        # A floor, not a count: files are added and removed constantly. It is far below the real
        # total and only catches a scan that stopped reading.
        assert len(_FINDINGS) >= 100, (
            f"only {len(_FINDINGS)} module(s) scanned under {_BACKEND_ROOT}")

    def test_the_scan_recursed_into_subpackages(self):
        depth = max(path.count("/") for path in _FINDINGS)
        assert depth >= 3, (
            f"the deepest module found is {depth} level(s) down, so the walk did not reach the "
            "nested handler packages")

    def test_paged_reads_were_actually_found(self):
        # Stated over files rather than loops so moving a loop between modules is not a failure.
        assert len(_PAGING_FILES) >= 20, (
            f"only {len(_PAGING_FILES)} module(s) thread an ExclusiveStartKey inside a loop: "
            f"{_PAGING_FILES}")

    def test_functions_were_attributed(self):
        # Without attribution every site would land on "<module>" and every exemption would be a
        # file-wide one again.
        attributed = sum(len(found["functions"]) for found in _FINDINGS.values())
        assert attributed >= 100, (
            f"only {attributed} function(s) were named across {len(_FINDINGS)} module(s), so site "
            "attribution is not working and an exemption cannot name a loop")


@pytest.mark.unit
class TestNoPagedReadContinuesOnTheValue:
    def test_no_module_outside_the_known_set_pages_on_the_value(self):
        offending = unexempted_value_form_sites(_FINDINGS, _KNOWN_REMAINING)

        assert offending == {}, (
            f"these reads decide their continuation from the VALUE of LastEvaluatedKey: {offending}. "
            "Page on the key's PRESENCE instead (if 'LastEvaluatedKey' not in response: break) -- the "
            "value form loops forever against an under-stubbed reader, which hangs the run rather "
            "than failing a test. Models: handlers/authz/__init__.py, handlers/auth/apiKeyService.py.")

    def test_no_exempt_file_gained_another_value_form_loop(self):
        over = exemptions_over_their_bound(_FINDINGS, _KNOWN_REMAINING)

        assert over == {}, (
            f"{over}: an exempted file carries more value-form sites than its _KNOWN_REMAINING entry "
            "admits, i.e. a NEW value-form loop was written inside a function the entry already "
            "names. Page on the key's PRESENCE instead; the entry covers the loop that was there, "
            "not the next one.")

    def test_the_known_remaining_entries_still_describe_real_code(self):
        """A stale exception silently stops covering whatever now lives at that path."""
        for path, exemption in _KNOWN_REMAINING.items():
            assert path in _FINDINGS, (
                f"{path} is listed in _KNOWN_REMAINING but was not found under {_BACKEND_ROOT}; if "
                "the module moved, update the entry -- an exception on a path that does not exist "
                "protects nothing and reads as if it did")
            missing = sorted(exemption["functions"] - set(_FINDINGS[path]["functions"]))
            assert not missing, (
                f"{path} no longer defines {missing}, which its _KNOWN_REMAINING entry names. Point "
                "the entry at the function that now holds the walk, or delete it -- an exemption on "
                "a function that does not exist covers nothing while reading as if it did")


@pytest.mark.unit
@pytest.mark.parametrize("relative_path", _PAGING_FILES)
class TestEveryPagingModuleChecksPresence:
    def test_a_module_that_threads_a_cursor_tests_for_the_key(self, relative_path):
        """The structural converse: paging with no presence check anywhere cannot be correct."""
        found = _FINDINGS[relative_path]

        assert found["presence"], (
            f"{relative_path} assigns ExclusiveStartKey inside a loop at line(s) "
            f"{found['loop_cursor_writes']} but never tests for LastEvaluatedKey's presence, so its "
            "continuation decision is made some other way -- almost always the value form.")
