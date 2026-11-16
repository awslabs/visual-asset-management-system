# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The audit search request validation and the Logs Insights query it builds.

The caller never sends query text, and nothing from the caller reaches the query except a validated,
escaped literal. The injection corpus below is run against every value class.
"""

import importlib
import re

import pytest
from auditSearchRecords import GROUPS

queryBuilder = importlib.import_module("common.auditSearch.queryBuilder")
containsFilter = importlib.import_module("common.auditSearch.containsFilter")
Error = queryBuilder.AuditSearchValidationError

START, END = 1763000000, 1763003600
ALL_GROUPS = list(GROUPS.audit_log_group_keys())

INJECTIONS = [
    '"', "'", "\\", "`", "/", "|", ";", "\n", "\r\n", "\x00", "a b", " a", "a ", "a\tb", "(?i)", ".*", "(a+)+$",
    "a]|filter @message like /.*/", '" | filter 1=1 //', "x/ | limit 100000 #", "$(whoami)", "\u202eevil",
    "a\u0000b", "{{x}}", "${x}", "../../etc/passwd", "a" * 300,
]


def request(**overrides):
    body = {"startTime": START, "endTime": END}
    body.update(overrides)
    return body


def validate(**overrides):
    return queryBuilder.validate_filters(request(**overrides), ALL_GROUPS)


def build(**overrides):
    return queryBuilder.build_query(validate(**overrides))


def lines(plan):
    return plan.query_string.split("\n| ")


@pytest.mark.unit
class TestRequestShape:
    def test_a_minimal_request_searches_every_allowed_group(self):
        filters = validate()
        assert filters.group_keys == tuple(ALL_GROUPS) and filters.sort == "desc" and filters.include_probes

    def test_a_query_text_field_is_refused(self):
        for name in ("query", "queryString", "regex", "pattern", "filter", "fields", "limit", "logGroupNames"):
            with pytest.raises(Error) as caught:
                validate(**{name: "fields @message"})
            assert caught.value.code == "unknownFilter"

    def test_the_error_names_no_value(self):
        with pytest.raises(Error) as caught:
            validate(users=["x" * 300])
        assert "xxx" not in str(caught.value) and str(caught.value) == "Invalid audit search request"

    def test_a_body_that_is_not_an_object_is_refused(self):
        for body in (None, [], "x", 5):
            with pytest.raises(Error):
                queryBuilder.validate_filters(body, ALL_GROUPS)

    def test_the_window_is_required_ordered_and_bounded(self):
        with pytest.raises(Error):
            queryBuilder.validate_filters({}, ALL_GROUPS)
        with pytest.raises(Error):
            validate(startTime=END, endTime=START)
        with pytest.raises(Error) as caught:
            validate(startTime=START, endTime=START + 32 * 86400)
        assert caught.value.code == "window:tooLong"
        validate(startTime=START, endTime=START + 31 * 86400)

    def test_the_window_limit_is_configurable_within_its_ceiling(self):
        limits = queryBuilder.SearchLimits(max_window_days=1000)
        assert limits.clamped().max_window_days == 366
        queryBuilder.validate_filters(request(endTime=START + 366 * 86400), ALL_GROUPS, limits)
        with pytest.raises(Error):
            queryBuilder.validate_filters(request(endTime=START + 367 * 86400), ALL_GROUPS, limits)

    def test_times_may_be_iso_strings(self):
        filters = validate(startTime="2026-11-16T10:00:00Z", endTime="2026-11-16T11:00:00.500Z")
        assert filters.end_time - filters.start_time == 3600

    @pytest.mark.parametrize("value", [True, 1.5, "yesterday", "2026-11-16 10:00:00", -1, 10 ** 12, [], None])
    def test_bad_times_are_refused(self, value):
        with pytest.raises(Error):
            validate(startTime=value)

    def test_more_than_twenty_values_are_refused(self):
        with pytest.raises(Error) as caught:
            validate(actions=[f"action{i}" for i in range(21)])
        assert caught.value.code == "actions:count"
        validate(actions=[f"action{i}" for i in range(20)])

    def test_a_filter_must_be_text_or_a_list_of_text(self):
        for value in (5, {"a": 1}, [5], [["x"]], [None]):
            with pytest.raises(Error):
                validate(actions=value)

    def test_a_single_string_is_one_value(self):
        assert validate(actions="assetUpdate").actions == ("assetUpdate",)

    def test_duplicates_collapse(self):
        assert validate(actions=["assetUpdate", "assetUpdate"]).actions == ("assetUpdate",)

    def test_outcome_is_an_enum(self):
        assert validate(outcomes=["denied", "failed"]).outcomes == ("denied", "failed")
        with pytest.raises(Error):
            validate(outcomes=["DENIED"])

    def test_flags_and_sort(self):
        assert validate(includeProbes=False).include_probes is False
        assert validate(sort="asc").sort == "asc"
        for field, value in (("includeProbes", "no"), ("sort", "sideways")):
            with pytest.raises(Error):
                validate(**{field: value})

    def test_unknown_and_unauthorized_groups_are_distinct_codes_without_names(self):
        with pytest.raises(Error) as unknown:
            validate(logGroups=["nope"])
        with pytest.raises(Error) as denied:
            queryBuilder.validate_filters(request(logGroups=["auditActions"]), ["auditErrors"])
        assert unknown.value.code == "groupUnknown" and denied.value.code == "groupNotAllowed"
        assert "auditActions" not in str(denied.value)

    def test_no_allowed_group_is_an_error(self):
        with pytest.raises(Error):
            queryBuilder.validate_filters(request(), [])

    def test_user_values_are_nfkc_normalized_and_unicode_ids_are_allowed(self):
        assert validate(users=["\uff41lice"]).users == ("alice",)
        users = ["auth0|123", "j\u00fcrgen@example.com"]
        assert validate(users=users).users == tuple(users)

    @pytest.mark.parametrize("user", ["", "a\nb", "a\u202eb", "a\x00b", "x" * 257])
    def test_bad_users_are_refused(self, user):
        with pytest.raises(Error):
            validate(users=[user])

    @pytest.mark.parametrize("field,value", [
        ("databaseIds", "ab"), ("databaseIds", "a b"), ("databaseIds", "x" * 65), ("assetIds", "a/b"),
        ("apiKeyIds", "short"), ("apiKeyIds", "a" * 65), ("requestIds", "bad id!!!"),
        ("actions", "1abc"), ("actions", "a-b"), ("entityTypes", "a_b"), ("entityTypes", "a b"),
    ])
    def test_ids_actions_and_types_are_pattern_checked(self, field, value):
        with pytest.raises(Error):
            validate(**{field: [value]})

    def test_a_trailing_newline_does_not_pass_a_pattern(self):
        for field in ("databaseIds", "actions", "entityTypes", "requestIds"):
            with pytest.raises(Error):
                validate(**{field: ["valid-value-12\n"]})


@pytest.mark.unit
class TestInjectionCorpus:
    FIELDS = ["actions", "entityTypes", "entityIds", "users", "apiKeyIds", "databaseIds", "assetIds",
              "requestIds", "outcomes", "logGroups"]

    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("value", INJECTIONS)
    def test_a_hostile_value_is_refused_or_kept_out_of_the_query(self, field, value):
        try:
            plan = build(**{field: [value]})
        except Error:
            return
        # Accepted values (an entity id or user may be any printable text) never reach the query text
        # unless they are in the safe-literal set, and then only escaped.
        for line in lines(plan)[1:]:
            assert queryBuilder._FILTER_LINE.match(line) or queryBuilder._SORT_LINE.match(line) \
                or queryBuilder._LIMIT_LINE.match(line), line
        if len(value) > 3 and not queryBuilder.SAFE_LITERAL.match(value):
            assert value not in plan.query_string

    def test_the_query_has_a_fixed_shape(self):
        plan = build(actions=["assetUpdate"], users=["alice"], databaseIds=["db-one"])
        assert lines(plan)[0] == queryBuilder._FIELDS_LINE
        assert lines(plan)[1].startswith("filter @message like /^\\[(")
        assert lines(plan)[-2] == "sort @timestamp desc" and lines(plan)[-1] == "limit 1000"
        assert "filter @message like /assetUpdate/" in lines(plan)
        assert "filter @message like /alice/" in lines(plan)
        assert "filter @message like /db\\-one/" in lines(plan)

    def test_every_filter_line_matches_the_grammar(self):
        plan = build(actions=["assetUpdate", "x_y"], users=["a.b@c.d", "auth0|1"], entityIds=["id:1+2"],
                     contains=["Report.usd", "a/b c"])
        for line in lines(plan)[1:]:
            assert re.match(r"(filter @message like /.*/|sort @timestamp (asc|desc)|limit \d+)\Z", line)
            assert queryBuilder._FILTER_LINE.match(line) or not line.startswith("filter")

    def test_a_value_with_a_regex_metacharacter_is_escaped(self):
        plan = build(users=["a.b+c@d.e|f=g:h-i"])
        assert "filter @message like /a\\.b\\+c\\@d\\.e\\|f\\=g\\:h\\-i/" in lines(plan)

    def test_several_values_are_an_alternation_of_escaped_literals(self):
        plan = build(users=["alice", "bob.smith"])
        assert "filter @message like /(alice|bob\\.smith)/" in lines(plan)

    def test_an_unsafe_value_is_not_pushed_down_and_warns(self):
        plan = build(users=["alice smith"], entityIds=["dir/file name.glb"])
        assert plan.warnings == ("widerScan",) and plan.prefilter_complete is False
        assert "smith" not in plan.query_string and "file" not in plan.query_string

    def test_one_unsafe_value_keeps_the_whole_filter_out_of_the_query(self):
        plan = build(users=["alice", "bob smith"])
        assert "alice" not in plan.query_string

    def test_actions_also_prefilter_on_their_legacy_type(self):
        plan = build(actions=["constraintUpdate"])
        assert "filter @message like /(constraintUpdate|constraintCreateUpdate)/" in lines(plan)

    def test_group_prefixes_are_anchored_and_escaped(self):
        plan = build(logGroups=["auditActions", "auditFileDownloadStreamed"])
        assert lines(plan)[1] == "filter @message like /^\\[(ACTIONS|FILEDOWNLOAD\\-STREAMED)\\]/"

    def test_outcome_entity_type_and_probes_are_server_side_only(self):
        plan = build(outcomes=["denied"], entityTypes=["asset"], includeProbes=False)
        assert "denied" not in plan.query_string and "asset" not in plan.query_string
        assert plan.prefilter_complete is False

    def test_a_complete_prefilter_is_reported(self):
        assert build(users=["alice"]).prefilter_complete is True

    def test_the_limit_and_sort_come_from_validated_values(self):
        validated = queryBuilder.validate_filters(request(sort="asc"), ALL_GROUPS)
        plan = queryBuilder.build_query(validated, queryBuilder.SearchLimits(max_rows=50))
        assert lines(plan)[-2:] == ["sort @timestamp asc", "limit 50"]
        assert queryBuilder.build_query(validated, queryBuilder.SearchLimits(max_rows=10 ** 9)).limit == 10000

    def test_a_forged_filters_object_cannot_inject_a_line(self):
        validated = queryBuilder.validate_filters(request(), ALL_GROUPS)
        forged = validated.__class__(**{**validated.__dict__, "users": ('x/ | limit 99999 //',)})
        plan = queryBuilder.build_query(forged)
        assert "limit 99999" not in plan.query_string and plan.warnings == ("widerScan",)

    def test_a_line_outside_the_grammar_is_refused(self):
        with pytest.raises(Error):
            queryBuilder._check_lines([queryBuilder._FIELDS_LINE, "filter @message like /a/ | stats count(*)"])
        with pytest.raises(Error):
            queryBuilder._check_lines([queryBuilder._FIELDS_LINE, 'filter @message like /a"b/'])
        with pytest.raises(Error):
            queryBuilder._check_lines([queryBuilder._FIELDS_LINE, "filter @message like /a/b/"])

    def test_escape_literal_refuses_unsafe_values(self):
        for value in ("a b", "a/b", 'a"b', "a\\b", "a`b", "a\nb", "", "x" * 257):
            with pytest.raises(ValueError):
                queryBuilder.escape_literal(value)

    def test_the_head_bound_covers_the_action_record_cap(self):
        auditLogging = importlib.import_module("backend.backend.customLogging.auditLogging")
        assert queryBuilder.HEAD_BYTES > auditLogging.AUDIT_RECORD_MAX_BYTES

    def test_the_summary_for_an_audit_record_holds_no_contains_text(self):
        summary = queryBuilder.filters_summary(validate(contains=["secret-term"], users=["alice"]))
        assert "secret" not in str(summary) and summary["containsTermCount"] == 1
        assert summary["containsTermLength"] == len("secret-term")
