# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The search permission filters bound the per-hit Casbin decision, and are that decision when exact.

``common.searchAuthzFilter`` turns a caller's asset GET policy lines into an ``upper`` and a ``lower``
clause per index. For every document the search processor's own per-hit check would decide:

* ``lower`` admits it  =>  the check allows it  =>  ``upper`` admits it;
* when the filter is ``exact``, ``upper`` admits it exactly when the check allows it.

The matrix runs the real policy generation and Casbin evaluation (only the role and constraint reads
and the asset-table reads are stubbed) over every constraint field, operator and value shape, allow and
deny lines, mixed roles, lines the caller does not hold, the shipped permission templates and the
seeded roles, against asset documents (live and archived, with missing, empty, null, newline, case and
wildcard-character values) and file documents whose parent asset is live or archived and whose copied
name and tags are stale. Each case also states whether its filter should be exact, so a translation
that silently gave up everywhere would fail here rather than pass.
"""

import copy
import itertools
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.authz.opensearch_dsl_oracle import matches
from backend.tests.handlers.authz.search_authz_harness import (
    ALLOW_ALL, ARCHIVED, CLAIMS, OTHER_ROLE, ROLE, USER_ID, constraint, crit, load_search_module, policies,
)


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_authz_filter")


# ---------------------------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------------------------
DATABASES = ("db-a", "db-a-2", "db-b")
NAMES = ("proj-a", "proj-ab", "Proj-a", "misc", "", None, "proj-a\nx", "x proj-a", "a*b?c\\d", "#deleted",
         "proj\na", "proj.a")
TYPES = ("typeX", "typeY", "", None, ".obj", "xobj", "\nobj", ".OBJ")
TAGS = ([], ["t1"], ["t1", "secret"], [""], None)
_MISSING = object()


def _asset_source(n, database_id, name, asset_type, tags, archived):
    source = {"_id": f"a{n}", "str_rectype": "asset", "str_databaseid": database_id, "str_assetid": f"a{n}",
              "bool_archived": archived}
    for key, value in (("str_assetname", name), ("str_assettype", asset_type), ("list_tags", tags)):
        if value is not _MISSING:
            source[key] = value
    return source


def _asset_universe():
    sources, n = [], 0
    for database_id, archived in itertools.product(DATABASES, (False, True)):
        for i, (name, tags) in enumerate(itertools.product(NAMES, TAGS)):
            asset_type = TYPES[(i + n) % len(TYPES)]
            # None stands for a missing field on even ids and an explicit null on odd ones
            name_value = _MISSING if name is None and n % 2 == 0 else name
            sources.append(_asset_source(n, database_id, name_value, asset_type, tags, archived))
            n += 1
    return sources


def _legacy_asset_sources():
    """Asset documents indexed before a field existed: no archived flag (a live asset) and no asset
    type key at all."""
    sources = []
    for n, database_id in enumerate(DATABASES):
        no_flag = _asset_source(f"legacy-flag-{n}", database_id, "proj-a", "typeX", ["t1"], False)
        del no_flag["bool_archived"]
        no_type = _asset_source(f"legacy-type-{n}", database_id, "proj-a", _MISSING, ["t1"], False)
        sources += [no_flag, no_type]
    return sources


ASSET_SOURCES = _asset_universe() + _legacy_asset_sources()


def _row_for(source):
    """The asset row the asset document stands for, as stored."""
    database_id = source["str_databaseid"] + (ARCHIVED if source.get("bool_archived") else "")
    return {"databaseId": database_id, "assetId": source["str_assetid"],
            "assetName": source.get("str_assetname") or "", "assetType": source.get("str_assettype") or "",
            "tags": source.get("list_tags") if source.get("list_tags") is not None else []}


def _file_universe():
    """File documents of every third asset: live and archived files, with stale copied fields."""
    sources = []
    for k, asset in enumerate(ASSET_SOURCES[::3]):
        sources.append({"_id": f"f{k}", "str_rectype": "file", "str_databaseid": asset["str_databaseid"],
                        "str_assetid": asset["str_assetid"], "str_key": f"k{k}.bin",
                        "str_assetname": "stale-name", "list_tags": ["stale"], "bool_archived": k % 2 == 1})
    return sources


FILE_SOURCES = _file_universe()
ROWS = {(row["databaseId"], row["assetId"]): row for row in (_row_for(s) for s in ASSET_SOURCES)}


def _hit(source, index_type):
    return {"_id": source["_id"], "_index_type": index_type,
            "_source": {k: v for k, v in source.items() if k != "_id"}}


@pytest.fixture
def processor(search_module, monkeypatch):
    def batch_get_items(dynamodb_resource, table_name, keys, **kwargs):
        return [copy.deepcopy(ROWS[(k["databaseId"], k["assetId"])]) for k in keys
                if (k["databaseId"], k["assetId"]) in ROWS], []

    monkeypatch.setattr(search_module, "batch_get_items", batch_get_items)
    table = MagicMock()
    table.get_item.side_effect = lambda Key: (
        {"Item": copy.deepcopy(ROWS[(Key["databaseId"], Key["assetId"])])}
        if (Key["databaseId"], Key["assetId"]) in ROWS else {})
    monkeypatch.setattr(search_module, "asset_storage_table", table)
    result = search_module.DualIndexResponseProcessor(search_module.DatabaseAccessManager())
    result._load_parent_assets([_hit(s, "file") for s in FILE_SOURCES])
    return result


def _check(search_module, processor, constraints, roles):
    """Assert the bounds hold for every document; return the filters."""
    with policies(constraints, roles):
        filters = search_module.build_search_authz_filters(CLAIMS)
        assert filters is not None
        for index_type, sources in (("asset", ASSET_SOURCES), ("file", FILE_SOURCES)):
            f = filters[index_type]
            for source in sources:
                truth = processor._is_hit_authorized(_hit(source, index_type), CLAIMS)
                lower, upper = matches(f.lower, source), matches(f.upper, source)
                assert not lower or truth, (index_type, source, "lower admits a refused document")
                assert not truth or upper, (index_type, source, "upper withholds an allowed document")
                if f.exact:
                    assert upper == truth, (index_type, source, "exact filter disagrees")
    return filters


# ---------------------------------------------------------------------------------------------
# Every field x operator x value shape, as an allow line and as a deny line next to allow-all
# ---------------------------------------------------------------------------------------------
OPERATORS = ("equals", "contains", "does_not_contain", "starts_with", "ends_with", "is_one_of", "is_not_one_of")
REGEX_OPERATORS = ("equals", "contains", "does_not_contain", "starts_with", "ends_with")

# (field, value, translates on the asset index for the regex operators except where noted)
STRING_VALUES = {
    "databaseId": ["db-a", ".*", "db-.*", "", "db-a#deleted", "#del", "deleted", "a#deleted", "db-a#d.*",
                   "db-a-2", "d.-a", "db-(a|b)", "db\\-a"],
    "assetName": ["proj-a", ".*", "proj-.*", "", "a\nx", "proj-a\n.*", "proj.a", "[pP]roj-a", "Proj",
                  "proj\\.a", "p.oj.*", "proj.a\nx", ".", "a\\*b\\?c\\\\d", "pro.*a"],
    "assetType": ["typeX", "type.*", "", ".*", "X", ".obj", "\\.obj", ".ob.*", "t.pe.", "obj"],
}
# Values a membership operator compares literally; wildcard characters must not act as wildcards.
MEMBERSHIP_VALUES = {
    "databaseId": ["db-a", "", "#deleted", "a-2#del"],
    "assetName": ["proj", "*b?c\\", "", "a\nx"],
    "assetType": ["type", ""],
}
TAG_VALUES = ["t1", "secret", "", ["t1", "secret"], "absent"]


_META = set(".^$*+?{}[]\\|()")


def _shape(value):
    """'literal', 'any', 'prefix' (literal + '.*'), 'dotted' (holds an unescaped '.') or None, reading
    '\\<metacharacter>' as a literal character."""
    stem, trailing = (value[:-2], True) if value.endswith(".*") and not value.endswith("\\.*") else (value, False)
    i, dotted, literal = 0, False, ""
    while i < len(stem):
        if stem[i] == "\\":
            if i + 1 < len(stem) and stem[i + 1] in _META:
                literal += stem[i + 1]
                i += 2
                continue
            return None, ""
        if stem[i] == ".":
            dotted = True
        elif stem[i] in _META:
            return None, ""
        literal += stem[i]
        i += 1
    if dotted:
        return "dotted", ""
    if trailing:
        return ("any", "") if literal == "" else ("prefix", literal)
    return "literal", literal


def _untranslatable(field, operator, value):
    if operator in ("is_one_of", "is_not_one_of"):
        return False
    shape, literal = _shape(value)
    if shape is None:
        return True
    if shape == "dotted":
        # The '#deleted' form of a patterned database rule is left to the per-hit check
        return field == "databaseId"
    if shape == "prefix":
        return operator == "ends_with" or (operator == "equals" and "\n" in literal)
    return False


def _single_criterion_cases():
    cases = []
    for field, values in STRING_VALUES.items():
        for operator in REGEX_OPERATORS:
            for value in values:
                cases.append((field, operator, value, not _untranslatable(field, operator, value)))
    for field, values in MEMBERSHIP_VALUES.items():
        for operator in ("is_one_of", "is_not_one_of"):
            for value in values:
                cases.append((field, operator, value, True))
    for operator in ("is_one_of", "is_not_one_of"):
        for value in TAG_VALUES:
            cases.append(("tags", operator, value, value != ""))
    return cases


SINGLE_CASES = _single_criterion_cases()


@pytest.mark.unit
class TestEveryCriterionShape:
    @pytest.mark.parametrize("field,operator,value,exact", SINGLE_CASES,
                             ids=[f"{f}-{o}-{v!r}" for f, o, v, _ in SINGLE_CASES])
    def test_as_an_allow_line(self, search_module, processor, field, operator, value, exact):
        filters = _check(search_module, processor, [constraint("c", [crit(field, operator, value)])], (ROLE,))
        assert filters["asset"].exact is exact

    @pytest.mark.parametrize("field,operator,value,exact", SINGLE_CASES,
                             ids=[f"{f}-{o}-{v!r}" for f, o, v, _ in SINGLE_CASES])
    def test_as_a_deny_line_beside_allow_all(self, search_module, processor, field, operator, value, exact):
        filters = _check(search_module, processor,
                         [ALLOW_ALL, constraint("d", [crit(field, operator, value)], effect="deny")], (ROLE,))
        assert filters["asset"].exact is exact

    def test_most_translatable_cases_separate_documents(self, search_module, processor):
        """A filter that admitted all or nothing would pass the bound checks trivially; most exact
        cases must split the asset documents into allowed and refused ones."""
        mixed = 0
        for field, operator, value, exact in SINGLE_CASES:
            if not exact:
                continue
            with policies([constraint("c", [crit(field, operator, value)])], (ROLE,)):
                f = search_module.build_search_authz_filters(CLAIMS)["asset"]
                admitted = {matches(f.upper, s) for s in ASSET_SOURCES}
            mixed += admitted == {True, False}
        assert mixed >= len([c for c in SINGLE_CASES if c[3]]) // 2


@pytest.mark.unit
class TestFileIndex:
    """File hits are decided on the parent asset row, so only database-id rules translate there."""

    @pytest.mark.parametrize("criterion,exact", [
        (crit("databaseId", "contains", ".*"), True),
        (crit("databaseId", "starts_with", "db-"), True),
        (crit("databaseId", "equals", "db-a"), False),     # an archived parent is refused
        (crit("assetType", "equals", "typeX"), False),     # file documents carry no asset type
        (crit("assetName", "equals", "proj-a"), False),    # copied name may be stale
        (crit("tags", "is_one_of", "t1"), False),          # copied tags may be stale
    ])
    def test_exactness(self, search_module, processor, criterion, exact):
        filters = _check(search_module, processor, [constraint("c", [criterion])], (ROLE,))
        assert filters["file"].exact is exact

    @pytest.mark.parametrize("template", ["database-readonly.json", "database-user.json", "database-admin.json"])
    @pytest.mark.parametrize("doc_archived,parent_archived", [(False, True), (True, False)])
    def test_a_file_document_whose_flag_disagrees_with_its_parent(self, search_module, template,
                                                                  doc_archived, parent_archived):
        """A file document's own archived flag is not its parent's state: it stays live when an asset
        archive left the file without a delete marker (or before the marker's event is indexed), and
        stays archived after an unarchive that keeps the files archived. The lower bound never counts
        the first, and the upper bound still fetches the second when the parent allows it."""
        constraints = shipped_constraints.template_constraints(
            template, "tpl", {"DATABASE_ID": "db-a", "TAG_VALUE": "secret"})
        source = {"_id": "fs", "str_rectype": "file", "str_databaseid": "db-a", "str_assetid": "p1",
                  "str_key": "/x.glb", "bool_archived": doc_archived}
        processor = search_module.DualIndexResponseProcessor(search_module.DatabaseAccessManager())
        processor._parent_assets = {("db-a", "p1"): {
            "databaseId": "db-a" + (ARCHIVED if parent_archived else ""), "assetId": "p1",
            "assetName": "n", "assetType": ".glb", "tags": []}}
        with policies(constraints, ("tpl",)):
            f = search_module.build_search_authz_filters(CLAIMS)["file"]
            truth = processor._is_hit_authorized(_hit(source, "file"), CLAIMS)
        assert not matches(f.lower, source) or truth
        assert not truth or matches(f.upper, source)

    def test_stale_copied_fields_do_not_decide(self, search_module, processor):
        """Every file document copies the name 'stale-name'; a rule on that name grants nothing."""
        filters = _check(search_module, processor,
                         [constraint("c", [crit("assetName", "equals", "stale-name")])], (ROLE,))
        assert not any(matches(filters["file"].lower, s) for s in FILE_SOURCES)


# ---------------------------------------------------------------------------------------------
# Line and effect structure, roles and subjects
# ---------------------------------------------------------------------------------------------
STRUCTURE_CASES = {
    "and of two criteria": ([constraint("c", [crit("databaseId", "equals", "db-a"),
                                              crit("assetType", "equals", "typeX")])], (ROLE,), True),
    "or criteria": ([constraint("c", [], [crit("assetName", "starts_with", "proj-"),
                                         crit("tags", "is_one_of", "t1")])], (ROLE,), True),
    "and plus or": ([constraint("c", [crit("databaseId", "starts_with", "db-a")],
                                [crit("assetType", "equals", "typeY"), crit("tags", "is_one_of", "secret")])],
                    (ROLE,), True),
    "multi-value equals": ([constraint("c", [crit("assetName", "equals", ["proj-a", "misc"])])], (ROLE,), True),
    "multi-value does_not_contain": ([constraint("c", [crit("assetName", "does_not_contain", ["proj", "misc"])])],
                                     (ROLE,), True),
    "empty value list": ([constraint("c", [crit("assetName", "equals", [])])], (ROLE,), True),
    "empty negated value list": ([constraint("c", [crit("assetName", "is_not_one_of", [])])], (ROLE,), True),
    "deny only": ([constraint("d", [crit("databaseId", "contains", ".*")], effect="deny")], (ROLE,), True),
    "no lines": ([], (ROLE,), True),
    "two allow lines": ([constraint("c1", [crit("assetType", "equals", "typeX")]),
                         constraint("c2", [crit("tags", "is_one_of", "t1")])], (ROLE,), True),
    "one role allows, another denies": ([
        constraint("c1", [crit("databaseId", "equals", "db-a")]),
        constraint("c2", [crit("tags", "is_one_of", "secret")], effect="deny", group=OTHER_ROLE),
    ], (ROLE, OTHER_ROLE), True),
    "a role the caller does not hold": ([
        constraint("c1", [crit("databaseId", "equals", "db-a")]),
        constraint("c2", [crit("databaseId", "contains", ".*")], group="not-held"),
    ], (ROLE,), True),
    "a deny for a role the caller does not hold": ([
        ALLOW_ALL, constraint("c2", [crit("databaseId", "contains", ".*")], effect="deny", group="not-held"),
    ], (ROLE,), True),
    "a PUT-only allow": ([constraint("c1", [crit("databaseId", "contains", ".*")], permission="PUT")],
                         (ROLE,), True),
    "a database-type allow": ([constraint("c1", [crit("databaseId", "contains", ".*")],
                                          object_type="database")], (ROLE,), True),
    "a direct user permission": ([constraint("c1", [crit("assetType", "equals", "typeY")], group=None,
                                             users=(USER_ID,))], (), True),
    "a user permission for someone else": ([ALLOW_ALL, constraint("c1", [crit("databaseId", "contains", ".*")],
                                                                  effect="deny", group=None,
                                                                  users=("someone-else",))], (ROLE,), True),
    "an untranslatable allow next to a translatable one": ([
        constraint("c1", [crit("assetType", "equals", "typeX")]),
        constraint("c2", [crit("assetName", "contains", "proj[.-]a")]),
    ], (ROLE,), False),
    "an untranslatable deny": ([ALLOW_ALL, constraint("d", [crit("assetName", "contains", "x|y")], effect="deny")],
                               (ROLE,), False),
    "an untranslatable criterion under negation": ([
        constraint("c", [crit("assetName", "does_not_contain", "p[.r]oj")])], (ROLE,), False),
    "a pattern operator on tags (stored before validation)": ([
        ALLOW_ALL, constraint("d", [crit("tags", "equals", "t1")], effect="deny")], (ROLE,), False),
    "an invalid pattern (stored before validation)": ([
        constraint("c1", [crit("assetType", "equals", "typeX")]),
        constraint("c2", [crit("assetName", "contains", "(")])], (ROLE,), False),
    "an unknown operator and field are skipped": ([
        constraint("c", [crit("assetType", "equals", "typeX"), crit("assetName", "matches", "x"),
                         crit("color", "equals", "red")])], (ROLE,), True),
    "an out-of-matrix field is skipped": ([
        constraint("c", [crit("assetType", "equals", "typeX"), crit("roleName", "equals", "nobody")])],
        (ROLE,), True),
}


@pytest.mark.unit
class TestPolicyStructure:
    @pytest.mark.parametrize("name", sorted(STRUCTURE_CASES))
    def test_bounds_hold(self, search_module, processor, name):
        constraints, roles, exact = STRUCTURE_CASES[name]
        filters = _check(search_module, processor, constraints, roles)
        assert filters["asset"].exact is exact

    def test_a_poisoned_policy_counts_nothing(self, search_module, processor):
        """A criterion Casbin cannot evaluate denies the whole check, so no document is a sure read."""
        constraints, roles, _ = STRUCTURE_CASES["an invalid pattern (stored before validation)"]
        filters = _check(search_module, processor, constraints, roles)
        assert filters["asset"].lower == {"match_none": {}}
        assert filters["file"].lower == {"match_none": {}}


# ---------------------------------------------------------------------------------------------
# What VAMS ships
# ---------------------------------------------------------------------------------------------
def _shipped_sets():
    sets = {f"template {name}": (shipped_constraints.template_constraints(
        name, "tpl", {"DATABASE_ID": "db-a", "TAG_VALUE": "secret"}), ("tpl",))
        for name in shipped_constraints.template_names()}
    sets["seeded admin"] = (shipped_constraints.seeded_constraints("admin"), ("admin",))
    sets["seeded basicReadOnly"] = (shipped_constraints.seeded_constraints("basicReadOnly"), ("basicReadOnly",))
    return sets


SHIPPED = _shipped_sets()


@pytest.mark.unit
class TestShippedRoles:
    @pytest.mark.parametrize("name", sorted(SHIPPED))
    def test_bounds_hold_and_the_asset_filter_is_exact(self, search_module, processor, name):
        constraints, roles = SHIPPED[name]
        filters = _check(search_module, processor, constraints, roles)
        assert filters["asset"].exact, name

    def test_the_admin_role_adds_no_filter(self, search_module):
        with policies(*SHIPPED["seeded admin"]):
            filters = search_module.build_search_authz_filters(CLAIMS)
        assert all(f.unrestricted for f in filters.values())


# ---------------------------------------------------------------------------------------------
# When the lines cannot be given
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestUnavailablePolicy:
    def test_no_caller_identity(self, search_module):
        assert search_module.build_search_authz_filters({"tokens": []}) is None

    def test_a_failed_policy_build(self, search_module):
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        try:
            with patch.object(authz_module.CasbinEnforcerService, "_create_policy_text_helper",
                              side_effect=RuntimeError("table read failed")), \
                    patch.object(authz_module, "CASBIN_GET_POLICY_RETRY_DELAY_SECONDS", 0):
                assert search_module.build_search_authz_filters(CLAIMS) is None
        finally:
            authz_module.casbin_user_policy_map = {}
            authz_module.casbin_user_enforcer_map = {}

    def test_policy_text_served_from_the_shared_cache(self, search_module):
        """The lines are known only to the service that built the policy text."""
        authz_module.casbin_user_enforcer_map = {}
        authz_module.casbin_user_policy_map = {USER_ID: authz_module.POLICY_TEXT_DENY_ALL}
        try:
            assert search_module.build_search_authz_filters(CLAIMS) is None
        finally:
            authz_module.casbin_user_policy_map = {}
            authz_module.casbin_user_enforcer_map = {}

    def test_a_policy_the_enforcer_could_not_load(self, search_module):
        with policies([ALLOW_ALL], (ROLE,)), patch.object(
            authz_module.CasbinEnforcerService, "_create_casbin_enforcer_helper",
            side_effect=[RuntimeError("bad policy"), MagicMock()],
        ):
            assert search_module.build_search_authz_filters(CLAIMS) is None

    def test_a_caller_without_roles_reads_nothing(self, search_module):
        with policies([ALLOW_ALL], ()):
            filters = search_module.build_search_authz_filters(CLAIMS)
        assert filters["asset"].exact and filters["asset"].upper == {"match_none": {}}


@pytest.mark.unit
class TestEffectiveObjectPolicies:
    def test_only_lines_the_matcher_can_select(self):
        constraints = [
            constraint("held", [crit("assetType", "equals", "typeX")]),
            constraint("not-held", [crit("assetType", "equals", "typeY")], group="not-held"),
            constraint("put", [crit("assetType", "equals", "typeZ")], permission="PUT"),
            constraint("db", [crit("databaseId", "equals", "db-a")], object_type="database"),
            constraint("mine", [crit("assetName", "equals", "x")], group=None, users=(USER_ID,)),
            constraint("theirs", [crit("assetName", "equals", "y")], group=None, users=("someone-else",)),
            constraint("odd-effect", [crit("assetName", "equals", "z")], effect="Allow"),
        ]
        with policies(constraints, (ROLE,)):
            lines = authz_module.CasbinEnforcer(CLAIMS).effective_object_policies("asset", "GET")
        assert lines == [
            {"effect": "allow", "criteriaAnd": [crit("assetType", "equals", "typeX")], "criteriaOr": []},
            {"effect": "allow", "criteriaAnd": [crit("assetName", "equals", "x")], "criteriaOr": []},
        ]

    def test_roles_removed_by_the_mfa_filter_contribute_no_line(self):
        claims = dict(CLAIMS, mfaEnabled=False)
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        try:
            with patch.object(authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
                              return_value=[{"userId": USER_ID, "roleName": ROLE}]), \
                    patch.object(authz_module.CasbinEnforcerService, "_read_role_from_table",
                                 return_value={"roleName": ROLE, "mfaRequired": True}), \
                    patch.object(authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
                                 return_value=[copy.deepcopy(ALLOW_ALL)]):
                lines = authz_module.CasbinEnforcer(claims).effective_object_policies("asset", "GET")
        finally:
            authz_module.casbin_user_policy_map = {}
            authz_module.casbin_user_enforcer_map = {}
        assert lines == []
