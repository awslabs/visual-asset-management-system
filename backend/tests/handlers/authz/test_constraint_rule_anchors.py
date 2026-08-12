# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""ABAC constraint rules must not be evadable with a newline in the object's field value.

Two properties of Python's regex dialect made the generated rules unsafe, and they pull in OPPOSITE
directions -- which is why the fix is per-operator rather than one uniform edit:

* ``'$'`` also matches immediately BEFORE a trailing newline. So ``'^Secret$'`` matched both
  ``"Secret"`` and ``"Secret\\n"``, and an ``equals``-Secret ALLOW rule granted a second, distinct
  stored value. ``'\\Z'`` is the true end of string.
* ``'.'`` does NOT cross a newline. So ``'.*Secret.*'`` did not see ``"pre\\nSecret"``. On a
  containment DENY that is a bypass: the inner match fails, the negation returns True, access is
  granted. The generated wildcards therefore use the SCOPED group ``'(?s:.*)'`` -- scoped, because a
  leading ``'(?s)'`` would apply to the caller's own value too and a value containing ``'.'`` would
  start spanning lines, widening the rule.

Reachable rather than theoretical: ``object_name_pattern`` accepts ``\\s``, which includes ``\\n``, so
``assetName = "PublicDoc\\n"`` is a storable name.

These tests drive the REAL casbin Enforcer over the REAL ``PERMISSION_CONSTRAINT_POLICY`` and the REAL
rule builder. Asserting on the generated regex string instead would only restate the implementation --
the question is what the enforcer DECIDES.
"""

import os
import sys
import tempfile

import pytest

# The rule builder and the policy text are pure; the module they live in creates boto3 clients at
# import, so a region must exist before the import is attempted.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("AWS_REGION", "us-west-2")

casbin = pytest.importorskip("casbin", reason="casbin is required to evaluate real policy decisions")


_REAL = {}


def _real_modules():
    """Load the REAL ``handlers.authz`` and ``common.constants`` from disk, by path.

    ``backend/conftest.py`` installs MagicMock stand-ins for ``handlers.*`` and ``common.*`` in
    ``sys.modules``, so a plain ``from handlers.authz import ...`` here raises ImportError from
    "(unknown location)" -- and a laxer test would instead bind a MagicMock and assert nothing at all.
    Loading by file path bypasses the harness for these two modules only, leaving every other test's
    stubs untouched. Cached so the enforcer's import-time boto3 clients are built once.
    """
    if _REAL:
        return _REAL

    import importlib.util

    backend_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
    )
    # The real modules import their siblings as `common.*` / `models.*`, which only resolve when the
    # package root is importable. Prepending is safe: the stubs live in sys.modules, which wins over
    # sys.path for anything already imported.
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    def _load(name, relative_path):
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(backend_root, *relative_path)
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    _REAL["constants"] = _load("_real_constants", ["common", "constants.py"])

    # The authz module cannot be exec'd here: its own imports (`request_to_claims` from the stubbed
    # `handlers.auth`, boto3 clients at module scope) resolve against the harness. Extract just the
    # rule builder instead -- it is a pure @staticmethod-style method over its arguments, so compiling
    # the class body in isolation exercises the SAME source lines the Lambda runs, without the
    # module's runtime dependencies. Anything the method needed from module scope would raise here
    # rather than pass silently.
    import ast
    import textwrap

    authz_source = open(
        os.path.join(backend_root, "handlers", "authz", "__init__.py"), encoding="utf-8"
    ).read()
    tree = ast.parse(authz_source)
    wanted = ("_generate_criteria_object_rules", "_escape_rule_value")
    methods = [
        node
        for cls in tree.body
        if isinstance(cls, ast.ClassDef) and cls.name == "CasbinEnforcerService"
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    assert len(methods) == len(wanted), (
        f"expected {wanted} on CasbinEnforcerService, found "
        f"{[m.name for m in methods]} -- the method was renamed or moved"
    )
    segments = [textwrap.dedent(ast.get_source_segment(authz_source, m)) for m in methods]
    namespace = {
        "PERMISSION_CONSTRAINT_FIELDS": _REAL["constants"].PERMISSION_CONSTRAINT_FIELDS,
        "get_constraint_fields_for_object_type": (
            _REAL["constants"].get_constraint_fields_for_object_type
        ),
        "logger": type("_Quiet", (), {"info": staticmethod(lambda *a, **k: None)})(),
        "staticmethod": staticmethod,
    }
    exec(compile("\n\n".join(segments), "<authz-rule-builder>", "exec"), namespace)
    _REAL["build_rules"] = namespace["_generate_criteria_object_rules"]
    _REAL["escape"] = namespace["_escape_rule_value"]
    return _REAL


class _RuleBuilder:
    """Minimal host for the extracted methods (they only use ``self._escape_rule_value``)."""

    def __init__(self, build, escape):
        self._build = build
        self._escape_rule_value = escape

    def _generate_criteria_object_rules(self, criteria, object_type=None):
        return self._build(self, criteria, object_type)


def _service():
    """The real rule-generation code, hosted so it can be called directly."""
    real = _real_modules()
    escape = real["escape"]
    # _escape_rule_value is a @staticmethod in the class; the extracted function is plain, so bind it
    # unbound-style.
    return _RuleBuilder(real["build_rules"], escape)


class _Obj:
    """Stand-in for the entity dict Casbin receives as ``r.obj``."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)


def _rule(operator, value, field="assetName"):
    return _service()._generate_criteria_object_rules(
        [{"field": field, "operator": operator, "value": value}]
    )[0]


def _decide(rule, name, effect="allow"):
    """Run one generated rule through a real Enforcer and return the decision."""
    PERMISSION_CONSTRAINT_POLICY = _real_modules()["constants"].PERMISSION_CONSTRAINT_POLICY

    with tempfile.TemporaryDirectory() as d:
        model_path = os.path.join(d, "model.conf")
        policy_path = os.path.join(d, "policy.csv")
        with open(model_path, "w") as fh:
            fh.write(PERMISSION_CONSTRAINT_POLICY)
        with open(policy_path, "w") as fh:
            fh.write(f"p, role::v, {rule}, GET, {effect}\ng, alice, role::v\n")
        enforcer = casbin.Enforcer(model_path, policy_path)
        return enforcer.enforce("alice", _Obj(assetName=name, object__type="asset"), "GET")


@pytest.mark.unit
class TestEqualsIsAnchoredToEndOfString:
    """`equals` must grant exactly its value."""

    def test_grants_the_exact_value(self):
        """POSITIVE CONTROL. Without this, the newline assertion below could pass because the rule
        matches nothing at all."""
        assert _decide(_rule("equals", "Secret"), "Secret") is True

    def test_does_not_grant_a_trailing_newline_variant(self):
        # The escalation: a distinct stored value satisfying an equals rule written for another.
        assert _decide(_rule("equals", "Secret"), "Secret\n") is False

    def test_does_not_grant_a_longer_name(self):
        assert _decide(_rule("equals", "Secret"), "SecretPlan") is False

    def test_a_value_containing_a_dot_does_not_span_lines(self):
        """The scoped-vs-global flag choice, pinned.

        A global `(?s)` prefix would make the caller's own '.' match a newline, so an equals rule for
        'Part.4' would start granting 'Part\\n4'. The wildcards must stay scoped.
        """
        rule = _rule("equals", "Part.4")
        assert _decide(rule, "Part.4") is True, "positive control: exact value must match"
        assert _decide(rule, "Part\n4") is False


@pytest.mark.unit
class TestContainmentSeesAcrossNewlines:
    """`contains` / `does_not_contain` must see a value that sits on another line."""

    def test_contains_matches_across_a_newline(self):
        rule = _rule("contains", "Secret")
        assert _decide(rule, "mySecretFile") is True, "positive control: same-line match"
        assert _decide(rule, "pre\nSecret") is True
        assert _decide(rule, "\nSecret") is True

    def test_contains_does_not_match_an_absent_value(self):
        assert _decide(_rule("contains", "Secret"), "Public") is False

    def test_does_not_contain_is_not_evaded_by_a_newline(self):
        """THE DENY BYPASS. Expressed as an allow-if-not-contains rule so a False decision means the
        containment test correctly saw the value.
        """
        rule = _rule("does_not_contain", "Secret")
        assert _decide(rule, "Public") is True, "positive control: a clean name is still granted"
        assert _decide(rule, "mySecretFile") is False, "positive control: same-line match is caught"
        assert _decide(rule, "pre\nSecret") is False
        assert _decide(rule, "\nSecret") is False

    def test_a_value_containing_a_dot_does_not_span_lines(self):
        """The scoped-vs-global flag choice on the CONTAINMENT operators.

        `equals` has the same assertion, but reverting only `contains` to a leading `(?s)` leaves that
        one passing -- so without this the widening would go uncaught here. A global flag applies to
        the caller's value too, and `contains 'Part.4'` would begin matching 'xPart\\n4y', granting
        names the constraint author never described.
        """
        rule = _rule("contains", "Part.4")
        assert _decide(rule, "xPart.4y") is True, "positive control: literal match"
        assert _decide(rule, "xPart44y") is True, "positive control: '.' is a live metacharacter"
        assert _decide(rule, "xPart\n4y") is False

    def test_does_not_contain_value_dot_does_not_span_lines(self):
        """Same property on the deny operator: widening here would make a deny fire on names it was
        not written for, which is an availability failure rather than a security one -- still wrong."""
        rule = _rule("does_not_contain", "Part.4")
        assert _decide(rule, "clean") is True, "positive control: unrelated name still granted"
        assert _decide(rule, "xPart.4y") is False, "positive control: literal match is caught"
        assert _decide(rule, "xPart\n4y") is True

    def test_the_admin_wildcard_still_matches_everything(self):
        """`contains '.*'` is the documented way to grant broadly; wrapping the wildcards must not
        narrow it, including for a value that spans lines."""
        rule = _rule("contains", ".*")
        for name in ("anything", "x\ny", ""):
            assert _decide(rule, name) is True, f"wildcard stopped matching {name!r}"


@pytest.mark.unit
class TestEndsWithCarriesBothFixes:
    """`ends_with` has both an anchor and a leading-wildcard defect."""

    def test_matches_a_name_that_ends_with_the_value(self):
        assert _decide(_rule("ends_with", "Plan"), "MyPlan") is True

    def test_does_not_match_a_trailing_newline_variant(self):
        assert _decide(_rule("ends_with", "Plan"), "MyPlan\n") is False

    def test_matches_when_earlier_text_contains_a_newline(self):
        # The leading wildcard must span newlines, or an ends_with DENY misses this name.
        assert _decide(_rule("ends_with", "Plan"), "x\nMyPlan") is True

    def test_does_not_match_when_the_value_is_not_at_the_end(self):
        assert _decide(_rule("ends_with", "Plan"), "Planx") is False


@pytest.mark.unit
class TestStartsWithIsUnchangedOnPurpose:
    """`starts_with` needed no change; these pin that its behavior did not drift."""

    def test_matches_a_prefix(self):
        assert _decide(_rule("starts_with", "Secret"), "SecretPlan") is True

    def test_does_not_match_when_the_prefix_is_not_at_the_start(self):
        # re.match anchors at offset 0, so a value pushed off the start cannot match — which is why
        # the trailing '.*' is a boolean no-op and needs no newline-crossing form.
        assert _decide(_rule("starts_with", "Secret"), "\nSecretPlan") is False


@pytest.mark.unit
class TestGeneratedRulesAreWellFormed:
    """The rule text itself must be a valid regex and must not depend on a lenient warning filter."""

    def test_every_regex_operator_emits_a_compilable_pattern(self):
        import re

        for operator in ("equals", "contains", "does_not_contain", "starts_with", "ends_with"):
            rule = _rule(operator, "Secret")
            pattern = re.search(r"'(.*)'\)", rule).group(1)
            re.compile(pattern)  # raises re.error on a malformed pattern

    def test_the_end_anchor_survives_python_string_escaping(self):
        r"""The anchor must reach the regex engine as ``\Z``, not as a literal 'Z'.

        The rule text is built in a non-raw f-string and then parsed a second time by the matcher, so
        the backslash has to survive two levels. Two backslashes in the rule text is the correct
        count: one would emit a SyntaxWarning (and raise under ``-W error::SyntaxWarning``, which
        makes the enforcer deny everything), while the decision assertions above would still pass.
        """
        backslash = chr(92)
        for operator in ("equals", "ends_with"):
            rule = _rule(operator, "Secret")
            assert rule.count(backslash) == 2, f"{operator}: expected 2 backslashes, got {rule!r}"
            assert rule.endswith(f"{backslash}{backslash}Z')"), rule
