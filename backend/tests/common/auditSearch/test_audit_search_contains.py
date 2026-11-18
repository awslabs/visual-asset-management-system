# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The ``contains`` free-text filter (test plan section 3): charset, escaping, ReDoS bounds, and what
it can and cannot see."""

import importlib
import re
import time

import pytest
from auditSearchRecords import (
    LEGACY_SEARCH,
    descriptor,
    one_action_message,
)

containsFilter = importlib.import_module("common.auditSearch.containsFilter")
queryBuilder = importlib.import_module("common.auditSearch.queryBuilder")
recordMatcher = importlib.import_module("common.auditSearch.recordMatcher")
parsers = importlib.import_module("common.auditSearch.parsers")
rowModel = importlib.import_module("common.auditSearch.rowModel")
Error = containsFilter.ContainsValidationError

REFUSED = [
    '"', "'", "\\", "|", "`", ";", "\n", "\x00", "(?i)", "(a+)+$", ".*", "[", "{", "caf\u00e9 menu",
    "\uff21\uff22\uff23", "ab", "a" * 65, "a--", ".. ..", " abc", "abc ", "abc\n", "a b\tc", "abc\r",
    "<script>", "a&b", "a=b", "a%20b", "a#b", "a*b", "a?b", "a^b", "a$b", "(abc)", "a,b", "a!b",
]


def terms(*values, **limits):
    return containsFilter.validate_terms(list(values), **limits)


def row_for(**fields):
    row = rowModel.AuditRow(groupKey="auditActions", **fields)
    return row


@pytest.mark.unit
class TestTermValidation:
    @pytest.mark.parametrize("value", REFUSED)
    def test_a_term_outside_the_rules_is_refused(self, value):
        with pytest.raises(Error) as caught:
            terms(value)
        assert str(caught.value) == "Invalid contains filter" and value not in str(caught.value)

    @pytest.mark.parametrize("value", ["abc", "Report.USD", "a/b c", "user@example.com", "x-y_z", "a:b+c",
                                       "a" * 64, "1.2.3"])
    def test_a_term_inside_the_rules_is_accepted_and_case_folded(self, value):
        assert terms(value) == (value.casefold(),)

    def test_a_string_is_one_term_and_a_list_several(self):
        assert containsFilter.validate_terms("Alpha") == ("alpha",)
        assert containsFilter.validate_terms(["Alpha", "Beta"]) == ("alpha", "beta")
        assert containsFilter.validate_terms(None) == ()
        assert containsFilter.validate_terms([]) == ()

    def test_a_repeated_term_counts_once(self):
        assert terms("Alpha", "alpha") == ("alpha",)

    def test_the_term_count_is_bounded(self):
        assert len(terms("aaa", "bbb", "ccc")) == 3
        with pytest.raises(Error) as caught:
            terms("aaa", "bbb", "ccc", "ddd")
        assert caught.value.code == "count"
        assert len(terms("aaa", "bbb", "ccc", "ddd", "eee", max_terms=5)) == 5
        with pytest.raises(Error):
            terms("aaa", "bbb", "ccc", "ddd", "eee", "fff", max_terms=50)

    def test_the_term_length_is_configurable_within_its_ceiling(self):
        assert terms("a" * 100, max_term_length=128) == ("a" * 100,)
        with pytest.raises(Error):
            terms("a" * 129, max_term_length=1000)

    def test_a_term_needs_three_letters_or_digits(self):
        with pytest.raises(Error) as caught:
            terms("..a..b")
        assert caught.value.code == "tooFewCharacters"

    @pytest.mark.parametrize("value", [5, None, b"abc", ["abc"], {"a": "b"}])
    def test_a_non_text_term_is_refused(self, value):
        with pytest.raises(Error):
            containsFilter.validate_terms([value])

    def test_a_non_list_value_is_refused(self):
        with pytest.raises(Error):
            containsFilter.validate_terms({"abc": 1})


@pytest.mark.unit
class TestLiteralBuilder:
    @pytest.mark.parametrize("term", ["a.c", "a+c", "a-c", "a/c", "a c", "a:c", "a@c", "user.name@example.com"])
    def test_every_non_word_character_is_escaped(self, term):
        literal = containsFilter.escape_regex_literal(term)
        assert re.sub(r"\\.", "", literal) == re.sub(r"[^A-Za-z0-9_]", "", term)
        assert re.fullmatch(r"(?:[A-Za-z0-9_]|\\[ -/:-@\[-`{-~])*", literal)

    def test_the_escaped_literal_matches_only_the_text_itself(self):
        # Python's re agrees with the escaping the same way an RE2 or Java engine reads it.
        for term in ("a.c", "a+c", "a c", "a/c", "a-c", "a:c"):
            pattern = containsFilter.escape_regex_literal(term)
            assert re.fullmatch(pattern, term)
        assert not re.fullmatch(containsFilter.escape_regex_literal("a.c"), "abc")
        assert not re.fullmatch(containsFilter.escape_regex_literal("a+c"), "aac")

    def test_underscore_stays_plain_because_re2_refuses_an_escaped_underscore(self):
        assert containsFilter.escape_regex_literal("a_b") == "a_b"

    def test_a_literal_is_never_built_from_non_ascii_or_control_text(self):
        for text in ("caf\u00e9", "a\nb", "a\x00b", "a\x7fb", "\u202e"):
            with pytest.raises(ValueError):
                containsFilter.escape_regex_literal(text)

    def test_the_prefilter_is_case_insensitive_and_escaped(self):
        assert containsFilter.prefilter_term("report.usd") == "(?i)report\\.usd"

    def test_the_query_carries_each_term_as_an_escaped_filter(self):
        filters = queryBuilder.validate_filters(
            {"startTime": 1763000000, "endTime": 1763003600, "contains": ["Report.USD", "a/b c"]},
            list(importlib.import_module("common.auditLogGroups").audit_log_group_keys()))
        plan = queryBuilder.build_query(filters)
        assert "filter @message like /(?i)report\\.usd/" in plan.query_string
        assert "filter @message like /(?i)a\\/b\\ c/" in plan.query_string


@pytest.mark.unit
class TestMatcher:
    def test_no_regular_expression_is_built_from_a_term(self, monkeypatch):
        # Every re.compile, re.search and re.match goes through re._compile; the matcher must not reach it.
        calls = []
        real = re._compile

        def spy(pattern, flags):
            calls.append(pattern)
            return real(pattern, flags)

        monkeypatch.setattr(re, "_compile", spy)
        row = row_for(user="alice", details={"note": "the quick brown fox"})
        assert containsFilter.row_matches_terms(row, ("quick",))
        assert not containsFilter.row_matches_terms(row, ("q.ick",))
        assert not containsFilter.row_matches_terms(row, ("qu(ick",))
        assert calls == []

    def test_the_matcher_source_calls_no_re_function(self):
        import ast
        import inspect
        source = inspect.getsource(containsFilter)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            checked = ("row_matches_terms", "searchable_strings", "_walk_value")
            if isinstance(node, ast.FunctionDef) and node.name in checked:
                names = {sub.value.id for sub in ast.walk(node)
                         if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)}
                assert "re" not in names, node.name

    def test_matching_is_a_case_folded_substring(self):
        row = row_for(details={"fileName": "Report.USD"})
        assert containsFilter.row_matches_terms(row, ("report.usd",))
        assert containsFilter.row_matches_terms(row, ("t.us",))
        assert not containsFilter.row_matches_terms(row, ("report.usdz",))

    def test_every_term_must_match_somewhere(self):
        row = row_for(user="alice", details={"note": "hello world"})
        assert containsFilter.row_matches_terms(row, ("alice", "world"))
        assert not containsFilter.row_matches_terms(row, ("alice", "mars"))

    def test_dots_and_pluses_are_literal(self):
        row = row_for(details={"v": "abc", "w": "a+c"})
        assert not containsFilter.row_matches_terms(row, ("a.c",))
        assert containsFilter.row_matches_terms(row, ("a+c",))

    def test_the_searched_strings_are_the_listed_fields(self):
        row = row_for(action="assetUpdate", legacyType="legacyT", user="alice", prefixUser="pfx", onBehalfOf="obo",
                      apiKeyId="key-1", databaseId="db1", assetId="a1", filePath="dir/f.glb",
                      entity={"type": "asset", "id": "ent-9"}, ids={"workflowId": "wf-3"},
                      requestParameters={"nestedKey": {"inner": "rp-value"}}, details={"d": "det-value"},
                      changes={"c": "chg-value"}, previous={"p": "prev-value"})
        strings = containsFilter.searchable_strings(row)
        for expected in ("assetUpdate", "legacyT", "alice", "pfx", "obo", "key-1", "db1", "a1", "dir/f.glb", "ent-9",
                         "workflowId", "wf-3", "nestedKey", "inner", "rp-value", "d", "det-value"):
            assert expected in strings, expected
        for unsearched in ("chg-value", "prev-value", "c", "p"):
            assert unsearched not in strings

    def test_changes_and_previous_are_not_searched(self):
        row = row_for(changes={"description": "needle-in-changes"}, previous={"description": "needle-in-previous"})
        assert not containsFilter.row_matches_terms(row, ("needle-in-changes",))
        assert not containsFilter.row_matches_terms(row, ("needle-in-previous",))

    def test_a_redacted_value_is_not_searched_and_cannot_be_probed(self):
        row = row_for(details={"token": "<redacted>"}, requestParameters={"query": "<redacted>"})
        assert "<redacted>" not in containsFilter.searchable_strings(row)
        # the search term that equals the hidden value finds nothing
        parsed = parsers.parse_record(LEGACY_SEARCH, descriptor("auditActions"), None, len(LEGACY_SEARCH))
        assert not containsFilter.row_matches_terms(parsed, ("secret-term-777",))
        assert not containsFilter.row_matches_terms(parsed, ("secret",))
        none = parsers.parse_record(LEGACY_SEARCH.replace("SECRET-TERM-777", "other"), descriptor("auditActions"))
        assert not containsFilter.row_matches_terms(none, ("secret-term-777",))

    def test_a_current_search_record_hides_the_text_the_same_way(self, writer):
        message = one_action_message(writer, "search", request_parameters={
            "size": 10, "searchText": {"query": "SECRET-TERM-777"}})
        row = parsers.parse_record(message, descriptor("auditActions"), None, len(message))
        assert not containsFilter.row_matches_terms(row, ("secret-term-777",))
        assert containsFilter.row_matches_terms(row, ("search",))

    def test_a_term_matches_a_value_that_was_json_escaped_in_the_record(self, writer):
        message = one_action_message(writer, "assetUpdate", database_id="db1", asset_id="a1",
                                     request_parameters=None, details={"label": 'say "hi" caf\u00e9'})
        assert "\\u00e9" in message
        row = parsers.parse_record(message, descriptor("auditActions"), None, len(message))
        assert containsFilter.row_matches_terms(row, ("caf", "say"))

    def test_the_node_depth_and_string_bounds(self):
        wide = {f"k{i}": f"v{i}" for i in range(2000)}
        row = row_for(details=wide)
        assert len(containsFilter.searchable_strings(row)) <= containsFilter.MAX_NODES * 2 + 20
        assert not containsFilter.row_matches_terms(row, ("v1999",))
        deep = current = {}
        for _ in range(10):
            current["n"] = {}
            current = current["n"]
        current["leaf"] = "deep-needle"
        assert not containsFilter.row_matches_terms(row_for(details=deep), ("deep-needle",))
        long = row_for(details={"s": "x" * 3000 + "tail-needle"})
        assert not containsFilter.row_matches_terms(long, ("tail-needle",))
        assert containsFilter.row_matches_terms(long, ("xxx",))

    def test_an_adversarial_record_stays_within_the_budget(self):
        # 16 KB of one character, a depth-6 payload of 500 nodes, the longest term and the most terms.
        payload = {"a" * 10: "a" * 2048 for _ in range(8)}
        nested = {f"n{i}": {f"m{j}": "a" * 30 for j in range(10)} for i in range(50)}
        row = row_for(details={**payload, "nested": nested})
        long_terms = ("a" * 127 + "b", "a" * 126 + "bb", "a" * 125 + "bbb", "a" * 124 + "bbbb", "a" * 123 + "bbbbb")
        started = time.monotonic()
        for _ in range(200):
            containsFilter.row_matches_terms(row, long_terms)
        assert time.monotonic() - started < 5

    def test_ten_thousand_candidate_rows_recheck_within_a_second_or_two(self):
        rows = [row_for(user=f"user{i}", details={"note": f"event number {i}", "n": {"m": "x" * 100}})
                for i in range(10000)]
        started = time.monotonic()
        kept = [row for row in rows if containsFilter.row_matches_terms(row, ("number 9999",))]
        assert len(kept) == 1 and time.monotonic() - started < 5

    def test_the_time_budget_cuts_the_recheck_and_reports_it(self):
        budget = containsFilter.MatchBudget(deadline=time.monotonic() - 1)
        rows = [row_for(user="alice") for _ in range(5)]
        filters = queryBuilder.validate_filters(
            {"startTime": 1763000000, "endTime": 1763003600, "contains": ["alice"]}, ["auditActions"])
        kept = recordMatcher.recheck(rows, filters, budget)
        assert kept == [] and budget.exceeded is True

    def test_without_a_deadline_the_budget_never_trips(self):
        budget = containsFilter.MatchBudget()
        assert budget.check() and not budget.exceeded
