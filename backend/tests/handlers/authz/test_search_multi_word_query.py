# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A keyword query of several words finds a record by its metadata (#1273).

The general query is a query_string. query_string splits its text at unescaped whitespace into terms
and, with default_operator OR, a value matches when any term does. A metadata or attribute value (the
flat-object ``_value`` subfield) and every ``.keyword`` subfield are keyword-typed: the whole value,
a sentence included, is one term, and a wildcard term must match all of it. Wrapping the whole query
(``*maintenance log*``) therefore asked for a value ending in ``maintenance`` or starting with ``log``,
and found the file whose keywords read "..., maintenance log, ..." by neither word. Each word is now
wrapped on its own. The model below applies those query_string rules to keyword values; the values are
the ones a test deployment's pipeline wrote on a file that each word alone found and the phrase did not.
"""

import re

import pytest

from backend.tests.handlers.authz.search_authz_harness import load_search_module

KEYWORDS = ("operator notes, night shift, maintenance log, conveyor belt, gearbox, robot gripper, pump, "
            "industrial equipment, shift log, equipment monitoring, maintenance record, facility operations")
TITLE = "Operator Notes - Night Shift 2026-10-29"


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_multi_word")


def _terms(text):
    """query_string's terms: the text split at whitespace that is not escaped."""
    terms, current, i = [], "", 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            current += text[i:i + 2]
            i += 2
            continue
        if ch.isspace():
            if current:
                terms.append(current)
            current = ""
        else:
            current += ch
        i += 1
    if current:
        terms.append(current)
    return terms


def _wildcard(term):
    out, i = [], 0
    while i < len(term):
        ch = term[i]
        if ch == "\\" and i + 1 < len(term):
            out.append(re.escape(term[i + 1]))
            i += 2
            continue
        out.append("(?s:.*)" if ch == "*" else "(?s:.)" if ch == "?" else re.escape(ch))
        i += 1
    return re.compile("".join(out))


def _matches_keyword_value(clause, value):
    """Whether a query_string clause with default_operator OR matches one keyword-typed value."""
    assert clause["default_operator"] == "OR"
    return any(_wildcard(term).fullmatch(value) for term in _terms(clause["query"]))


def _general_clauses(module, query, index_type="file", simple=False):
    if simple:
        builder = module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())
        request = module.SimpleSearchRequestModel(query=query)
        clause = builder._build_simple_query_clause(request, ["db1"], index_type)
        general = clause["bool"]["must"][0]
    else:
        builder = module.DualIndexQueryBuilder(module.DatabaseAccessManager())
        general = builder._build_general_search_query(query, True, index_type)
    return [entry["query_string"] for entry in general["bool"]["should"]]


@pytest.mark.unit
class TestAPhraseFindsTheValueItsWordsFind:
    @pytest.mark.parametrize("simple", [False, True])
    @pytest.mark.parametrize("query,value", [
        ("maintenance log", KEYWORDS),
        ("Operator Notes", TITLE),
        ("night conveyor", KEYWORDS),
    ])
    def test_the_phrase_matches(self, search_module, simple, query, value):
        clauses = _general_clauses(search_module, query, simple=simple)
        # each word alone matches, so the phrase must too
        for word in query.split():
            assert all(_matches_keyword_value(c, value) for c in _general_clauses(search_module, word, simple=simple))
        assert all(_matches_keyword_value(c, value) for c in clauses), [c["query"] for c in clauses]

    @pytest.mark.parametrize("simple", [False, True])
    def test_a_record_matching_any_one_word_is_found(self, search_module, simple):
        """The words are alternatives (default_operator OR): one matching word is enough."""
        clauses = _general_clauses(search_module, "zzz-not-there gearbox", simple=simple)
        assert all(_matches_keyword_value(c, KEYWORDS) for c in clauses)
        assert not any(_matches_keyword_value(c, KEYWORDS) for c in _general_clauses(search_module, "zzz qqq"))

    def test_each_word_is_wrapped_on_its_own(self, search_module):
        for simple in (False, True):
            for index_type in ("asset", "file"):
                for clause in _general_clauses(search_module, "maintenance  log", index_type, simple=simple):
                    assert clause["query"] == "*maintenance* *log*"
                    assert clause["default_operator"] == "OR"


@pytest.mark.unit
class TestTheWordsStayLiteral:
    def test_a_single_word_is_unchanged(self, search_module):
        assert search_module.FieldClassifier.any_word_anywhere_query("pump") == "*pump*"

    def test_wildcards_and_operators_are_literal(self, search_module):
        text = search_module.FieldClassifier.any_word_anywhere_query("a*b c? (x) AND")
        assert text == "*a\\*b* *c\\?* *\\(x\\)* *AND*"
        assert not _wildcard(_terms(text)[0]).fullmatch("aXXb")
        assert _wildcard(_terms(text)[0]).fullmatch("xa*by")

    def test_a_repeated_word_is_asked_once(self, search_module):
        assert search_module.FieldClassifier.any_word_anywhere_query("log log  pump log") == "*log* *pump*"

    def test_a_blank_query_matches_any_value_as_before(self, search_module):
        assert search_module.FieldClassifier.any_word_anywhere_query("   ") == "*"
