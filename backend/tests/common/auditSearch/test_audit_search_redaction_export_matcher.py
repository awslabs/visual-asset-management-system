# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-side redaction, output encoding, CSV and JSON export, the row model and the filter recheck."""

import csv
import importlib
import io
import json

import pytest
from auditSearchRecords import (
    LEGACY_AUTHORIZATION_DENIED,
    LEGACY_ERRORS,
    LEGACY_SEARCH,
    api_event,
    descriptor,
    one_action_message,
)

redaction = importlib.import_module("common.auditSearch.redaction")
exportEncoding = importlib.import_module("common.auditSearch.exportEncoding")
queryBuilder = importlib.import_module("common.auditSearch.queryBuilder")
recordMatcher = importlib.import_module("common.auditSearch.recordMatcher")
rowModel = importlib.import_module("common.auditSearch.rowModel")
parsers = importlib.import_module("common.auditSearch.parsers")
GROUPS = importlib.import_module("common.auditLogGroups")

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


def parse(message, group="auditActions"):
    return parsers.parse_record(message, descriptor(group), "2026-11-16T12:00:00.000Z", len(message))


def filters(**overrides):
    body = {"startTime": 1763000000, "endTime": 1763003600}
    body.update(overrides)
    return queryBuilder.validate_filters(body, list(GROUPS.audit_log_group_keys()))


@pytest.mark.unit
class TestRedaction:
    @pytest.mark.parametrize("text,gone", [
        (f"Authorization: Bearer {TOKEN}", TOKEN),
        (f"token {TOKEN} end", TOKEN),
        ("key AKIAABCDEFGHIJKLMNOP end", "AKIAABCDEFGHIJKLMNOP"),
        ("key ASIAABCDEFGHIJKLMNOP end", "ASIAABCDEFGHIJKLMNOP"),
        ("https://b.s3.amazonaws.com/k?X-Amz-Signature=deadbeef&X-Amz-Date=1", "deadbeef"),
        ("https://b.s3.amazonaws.com/k?X-Amz-Credential=AKIA%2F2026&a=1", "AKIA%2F2026"),
        ("https://b.s3.amazonaws.com/k?X-Amz-Security-Token=zzz999&a=1", "zzz999"),
        ("https://b.s3.amazonaws.com/k?AWSAccessKeyId=abc&Signature=sig123", "sig123"),
        ("bearer lowercasetoken123", "lowercasetoken123"),
    ])
    def test_credential_shapes_are_replaced(self, text, gone):
        value, _ = redaction.redact_value({"note": text})
        assert gone not in json.dumps(value) and "<redacted>" in json.dumps(value)

    def test_ordinary_text_is_untouched(self):
        value, truncated = redaction.redact_value({"note": "a plain sentence", "n": 3, "ok": True, "none": None})
        assert value == {"note": "a plain sentence", "n": 3, "ok": True, "none": None} and truncated is False

    def test_key_name_redaction_still_applies(self):
        value, _ = redaction.redact_value({"password": "p4ss", "configBody": "PROMPT", "searchText": {"q": "x"},
                                           "nested": {"secretAccessKey": "k"}})
        text = json.dumps(value)
        for secret in ("p4ss", "PROMPT", '"q"', '"k"'):
            assert secret not in text

    def test_the_search_text_of_a_search_record_is_hidden(self):
        section = {"query": "TERM", "metadataQuery": "TERM", "metadataKey": "TERM", "metadataValue": "TERM",
                   "tokens": [{"propertyKey": "all", "operator": "=", "value": "TERM"}],
                   "filters": [{"query_string": {"query": "TERM", "default_field": "TERM"}, "other": "kept"}],
                   "from": 0, "size": 5}
        value, _ = redaction.redact_value(section, search_text="search")
        assert "TERM" not in json.dumps(value)
        assert value["from"] == 0 and value["size"] == 5 and value["filters"][0]["other"] == "kept"
        assert value["tokens"][0]["propertyKey"] == "all" and value["filters"][0]["query_string"] == "<redacted>"

    def test_the_text_filters_of_a_read_are_hidden_but_ids_stay(self):
        value, _ = redaction.redact_value({"text": "TERM", "filterPattern": "TERM", "databaseId": "db1"},
                                          search_text="read")
        assert value == {"text": "<redacted>", "filterPattern": "<redacted>", "databaseId": "db1"}

    def test_other_actions_keep_generic_names(self):
        value, _ = redaction.redact_value({"query": "kept", "value": "kept", "text": "kept"})
        assert value == {"query": "kept", "value": "kept", "text": "kept"}

    @pytest.mark.parametrize("char,shown", [
        ("\x00", "\\u0000"), ("\n", "\\u000a"), ("\r", "\\u000d"), ("\t", "\\u0009"), ("\x7f", "\\u007f"),
        ("\x85", "\\u0085"), ("\u202e", "\\u202e"), ("\u2066", "\\u2066"), ("\u200b", "\\u200b"),
        ("\u2028", "\\u2028"), ("\ufeff", "\\ufeff"), ("\ud800", "\\ud800"), ("\U000e0041", "\\u{e0041}"),
    ])
    def test_invisible_and_control_characters_are_shown_escaped(self, char, shown):
        value, _ = redaction.redact_value({f"k{char}": f"a{char}b"})
        assert value == {f"k{shown}": f"a{shown}b"}
        json.dumps(value).encode("utf-8")

    def test_a_lone_surrogate_cannot_break_utf8_output(self):
        value, _ = redaction.redact_value({"a": "x\udc80y"})
        json.dumps(value, ensure_ascii=False).encode("utf-8")

    def test_ordinary_non_ascii_text_is_kept(self):
        assert redaction.redact_value({"a": "caf\u00e9 \u65e5\u672c"})[0] == {"a": "caf\u00e9 \u65e5\u672c"}

    def test_a_long_string_is_cut_and_flagged(self):
        value, truncated = redaction.redact_value({"a": "x" * 5000})
        assert len(value["a"]) == 2048 and truncated is True

    def test_depth_and_node_bounds(self):
        deep = current = {}
        for _ in range(10):
            current["n"] = {}
            current = current["n"]
        value, truncated = redaction.redact_value(deep)
        assert truncated is True and "truncated" in json.dumps(value)
        wide, truncated = redaction.redact_value({f"k{i}": i for i in range(5000)})
        assert truncated is True and len(wide) < 2100

    def test_a_structure_that_cannot_be_processed_is_redacted(self, monkeypatch):
        monkeypatch.setattr(redaction, "mask_sensitive_data", lambda value: 1 / 0)
        assert redaction.redact_value({"a": 1}) == ("<redacted>", True)

    def test_writer_truncation_markers_pass_through_as_structure(self):
        marker = {"truncated": True, "sha256": "ab" * 32, "bytes": 5000, "preview": "head"}
        assert redaction.redact_value({"description": marker})[0] == {"description": marker}

    def test_sections_are_bounded_together_at_16_kb(self):
        sections = {"changes": {"a": ["x" * 2000] * 9}, "details": {"b": ["y" * 2000] * 4}}
        redacted, truncated = redaction.redact_sections(sections)
        assert truncated is True
        assert len(json.dumps(redacted)) <= redaction.MAX_SECTIONS_BYTES + 200
        assert any(value == {"truncated": True, "bytes": value.get("bytes")} for value in redacted.values()
                   if isinstance(value, dict))

    def test_absent_sections_are_left_out(self):
        assert redaction.redact_sections({"changes": None, "details": {"a": 1}})[0] == {"details": {"a": 1}}

    def test_search_text_redaction_applies_to_the_request_sections_only(self):
        redacted, _ = redaction.redact_sections({"requestParameters": {"query": "T"}, "changes": {"query": "T"}},
                                                search_text="search")
        assert redacted == {"requestParameters": {"query": "<redacted>"}, "changes": {"query": "T"}}

    def test_a_scalar_is_encoded(self):
        assert redaction.encode_scalar("a\u202eb") == "a\\u202eb"
        assert redaction.encode_scalar(5) == 5 and redaction.encode_scalar(None) is None
        assert redaction.encode_scalar(float("nan")) is None

    def test_an_email_in_an_auth_change_is_shown_in_full(self, writer):
        # D20: Amazon Cognito emails and phone numbers an auth change sets are recorded and shown in full.
        message = one_action_message(writer, "cognitoUserCreate", changes={"email": "carol@example.com",
                                                                           "phone": "+15555550100"},
                                     event=api_event(path="/user/cognito", method="POST"))
        row = parse(message, "auditAuthChanges")
        assert row.changes == {"email": "carol@example.com", "phone": "+15555550100"}


@pytest.mark.unit
class TestExport:
    def row(self, **overrides):
        base = rowModel.AuditRow(timestamp="2026-11-16T12:00:00.000Z", groupKey="auditActions",
                                 groupLabel="Actions", action="assetUpdate", user="alice", outcome="success",
                                 entity={"type": "asset", "id": "a1"}, request={"requestId": "r-1"},
                                 requestParameters={"k": "v"})
        for key, value in overrides.items():
            setattr(base, key, value)
        return base.to_dict()

    @pytest.mark.parametrize("value", ["=1+1", "+1", "-1", "@SUM(A1)", "\t=1", "\r=1", "=HYPERLINK(\"http://x\")",
                                       "\\u0009=1", "\\u000d=1"])
    def test_a_formula_cell_gets_a_leading_apostrophe(self, value):
        # control characters are shown escaped first, so a leading tab or CR reads as its escape
        assert exportEncoding.csv_cell(value) == "'" + redaction.encode_text(value)

    @pytest.mark.parametrize("value", ["abc", "a=b", "x-1", "", "a@b"])
    def test_other_text_is_unchanged(self, value):
        assert exportEncoding.csv_cell(value) == value

    def test_numbers_and_flags_are_not_prefixed(self):
        assert exportEncoding.csv_cell(-5) == "-5" and exportEncoding.csv_cell(True) == "true"
        assert exportEncoding.csv_cell(None) == "" and exportEncoding.csv_cell(1.5) == "1.5"

    def test_nested_values_are_one_json_cell_with_the_same_rule(self):
        assert exportEncoding.csv_cell({"a": [1, 2]}) == '{"a":[1,2]}'

    def test_csv_quotes_every_cell_uses_crlf_and_reads_back(self):
        text = exportEncoding.rows_to_csv([self.row(user='say "hi", ok', action="=cmd|' /C calc'!A0")])
        assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
        rows = list(csv.reader(io.StringIO(text, newline="")))
        assert rows[0] == list(exportEncoding.CSV_COLUMNS) and len(rows) == 2
        record = dict(zip(rows[0], rows[1]))
        assert record["user"] == 'say "hi", ok' and record["action"].startswith("'=cmd")
        assert record["entityId"] == "a1" and record["requestId"] == "r-1"
        assert all(line.startswith('"') for line in text.split("\r\n") if line)

    def test_the_header_is_on_the_first_chunk_only(self):
        first = exportEncoding.encode_chunk([self.row()], "csv", True).decode()
        second = exportEncoding.encode_chunk([self.row()], "csv", False).decode()
        assert first.startswith('"timestamp"') and not second.startswith('"timestamp"')
        assert not first.startswith("\ufeff") and not exportEncoding.encode_chunk([], "csv", False)

    def test_control_and_bidi_characters_never_reach_a_cell_raw(self):
        encoded, _ = redaction.redact_value({"a": "x\u202ey\r\nz"})
        text = exportEncoding.rows_to_csv([self.row(details=encoded)])
        assert "\u202e" not in text and text.count("\r\n") == 2
        assert "\u202e" not in exportEncoding.rows_to_csv([self.row(user="a\u202eb")])

    def test_json_array_for_one_complete_list_is_ascii(self):
        text = exportEncoding.rows_to_json([self.row(user="caf\u00e9")])
        assert json.loads(text)[0]["user"] == "caf\u00e9" and text.isascii()

    def test_chunked_json_is_ndjson_and_the_chunks_form_one_valid_file(self):
        chunks = [
            exportEncoding.encode_chunk([self.row(user="caf\u00e9"), self.row(user="a\nb")], "ndjson", True),
            exportEncoding.encode_chunk([self.row(user="c")], "ndjson", False),
            exportEncoding.encode_chunk([], "ndjson", False),
            exportEncoding.encode_chunk([self.row(user="d")], "ndjson", False),
        ]
        document = b"".join(chunks).decode("ascii")
        lines = document.split("\n")
        assert lines[-1] == "" and len(lines) == 5          # every row is one whole line
        assert [json.loads(line)["user"] for line in lines[:-1]] == ["caf\u00e9", "a\nb", "c", "d"]
        assert all(chunk.endswith(b"\n") for chunk in chunks if chunk)

    def test_there_is_no_chunked_json_array_format(self):
        with pytest.raises(ValueError):
            exportEncoding.encode_chunk([self.row()], "json", True)

    def test_an_unsupported_format_is_refused(self):
        with pytest.raises(ValueError):
            exportEncoding.encode_chunk([], "xml", True)

    def test_every_csv_column_is_a_row_key_or_a_flattened_one(self):
        flat = exportEncoding._flatten(self.row())
        assert set(exportEncoding.CSV_COLUMNS) <= set(flat)


@pytest.mark.unit
class TestRowModel:
    def test_the_row_has_a_fixed_key_set(self):
        assert set(rowModel.AuditRow().to_dict()) == {
            "timestamp", "groupKey", "groupLabel", "prefix", "action", "legacyType", "entity", "user", "onBehalfOf",
            "authMethod", "apiKeyId", "crossCall", "outcome", "reason", "databaseId", "assetId", "filePath", "ids",
            "request", "bytes", "fileCount", "itemCount", "durationMs", "requestParameters", "details", "changes",
            "previous", "truncated", "formatVersion", "parseStatus", "probe", "recordId", "preview"}

    def test_the_row_never_carries_the_raw_text_or_the_echo(self, writer):
        message = one_action_message(writer, "assetUpdate")
        assert "event:" not in json.dumps(parse(message).to_dict())

    def test_statuses_and_versions(self):
        assert rowModel.PARSE_STATUSES == ("parsed", "partial", "unparsed", "unsupportedVersion")
        assert (rowModel.FORMAT_LEGACY, rowModel.FORMAT_CONTRACT_V1) == ("legacy", "contract-v1")


@pytest.mark.unit
class TestRecheck:
    @pytest.mark.parametrize("stored", ["\uff21lice", "Alice", "A\u006c\u0069ce", "cafe\u0301@example.com"])
    def test_the_user_filter_normalizes_the_stored_user_like_the_filter(self, stored):
        row = rowModel.AuditRow(groupKey="auditActions", user=stored)
        wanted = __import__("unicodedata").normalize("NFKC", stored)
        assert recordMatcher.row_matches(row, filters(users=[stored]))
        assert recordMatcher.row_matches(row, filters(users=[wanted]))

    def test_the_user_filter_is_case_sensitive(self):
        row = rowModel.AuditRow(groupKey="auditActions", user="Alice")
        assert not recordMatcher.row_matches(row, filters(users=["alice"]))

    def test_a_forged_user_in_a_name_cannot_match_a_user_filter(self, writer):
        row = parse(one_action_message(writer, "assetUpdate", database_id="db1", asset_id="a1",
                                       changes={"assetName": "x] [user: admin] [roles: [\"admin\"]"}))
        assert not recordMatcher.row_matches(row, filters(users=["admin"]))
        assert recordMatcher.row_matches(row, filters(users=["alice"]))

    def test_nested_ids_cannot_match_id_filters(self, writer):
        row = parse(one_action_message(writer, "assetUpdate", database_id="real-db", asset_id="asset1",
                                       changes={"databaseId": "victim"}, details={"assetId": "victim-asset"}))
        assert not recordMatcher.row_matches(row, filters(databaseIds=["victim"]))
        assert not recordMatcher.row_matches(row, filters(assetIds=["victim-asset"]))
        assert recordMatcher.row_matches(row, filters(databaseIds=["real-db"], assetIds=["asset1"]))

    def test_the_outcome_comes_from_the_record_only(self, writer):
        row = parse(one_action_message(writer, "assetUpdate", details={"outcome": "denied"}))
        assert recordMatcher.row_matches(row, filters(outcomes=["success"]))
        assert not recordMatcher.row_matches(row, filters(outcomes=["denied"]))

    def test_filters_combine_with_and_and_values_with_or(self, writer):
        row = parse(one_action_message(writer, "assetUpdate", database_id="db1", asset_id="a1"))
        assert recordMatcher.row_matches(row, filters(actions=["assetCreate", "assetUpdate"], databaseIds=["db1"]))
        assert not recordMatcher.row_matches(row, filters(actions=["assetUpdate"], databaseIds=["db2"]))

    def test_entity_and_request_filters(self, writer):
        row = parse(one_action_message(writer, "assetUpdate", database_id="db1", asset_id="a1"))
        assert recordMatcher.row_matches(row, filters(entityTypes=["asset"], entityIds=["a1"]))
        assert not recordMatcher.row_matches(row, filters(entityTypes=["file"]))
        assert not recordMatcher.row_matches(row, filters(requestIds=["req-nope-123"]))

    def test_the_request_id_filter_matches_the_records_request(self, writer):
        event = api_event()
        event["requestContext"]["requestId"] = "11111111-2222-3333-4444-555555555555"
        row = parse(one_action_message(writer, "assetUpdate", event=event))
        assert recordMatcher.row_matches(row, filters(requestIds=["11111111-2222-3333-4444-555555555555"]))

    def test_the_user_filter_covers_the_acting_user_the_prefix_user_and_on_behalf_of(self, writer):
        message = one_action_message(writer, "assetUpdate")
        payload = json.loads(message[message.index("{"):message.index(" --- [event")])
        payload["actor"]["onBehalfOf"] = "carol"
        row = parse(message[:message.index("{")] + json.dumps(payload))
        assert recordMatcher.row_matches(row, filters(users=["alice"]))
        assert recordMatcher.row_matches(row, filters(users=["carol"]))
        assert not recordMatcher.row_matches(row, filters(users=["bob"]))

    def test_the_api_key_filter(self, writer):
        row = parse(one_action_message(writer, "assetUpdate", event=api_event(api_key_id="key-12345678")))
        assert recordMatcher.row_matches(row, filters(apiKeyIds=["key-12345678"]))
        assert not recordMatcher.row_matches(row, filters(apiKeyIds=["key-87654321"]))

    def test_probes_can_be_hidden(self):
        from backend.backend.customLogging import auditLogging
        record = auditLogging.format_authorization_denial_record(
            {"tokens": ["bob"], "roles": [], "mfaEnabled": True}, "GET /auth/routes", {"action": "GET"})
        row = parse(record, "auditAuthorization")
        assert recordMatcher.row_matches(row, filters(includeProbes=True))
        assert not recordMatcher.row_matches(row, filters(includeProbes=False))

    def test_a_row_of_another_group_never_matches(self, writer):
        row = parse(one_action_message(writer, "assetUpdate"))
        narrowed = queryBuilder.validate_filters({"startTime": 1763000000, "endTime": 1763003600,
                                                  "logGroups": ["auditErrors"]}, ["auditErrors", "auditActions"])
        assert not recordMatcher.row_matches(row, narrowed)

    def test_legacy_rows_match_by_legacy_type_and_by_the_registry_alias(self):
        row = parse(LEGACY_ERRORS, "auditErrors")
        assert recordMatcher.row_matches(row, filters(actions=["validation_error"]))
        auth = parse('[AUTHCHANGES][type: constraintCreateUpdate] [user: admin] [roles: []] [mfaEnabled: True] '
                     '{"constraintId": "c1"}', "auditAuthChanges")
        assert recordMatcher.row_matches(auth, filters(actions=["constraintUpdate"]))
        assert recordMatcher.row_matches(auth, filters(actions=["constraintCreate"]))
        assert not recordMatcher.row_matches(auth, filters(actions=["roleCreate"]))

    def test_a_contract_row_matches_its_action_not_the_legacy_alias_of_another(self, writer):
        row = parse(one_action_message(writer, "constraintUpdate", event=api_event(path="/auth/constraints/c1",
                                                                                   method="PUT")),
                    "auditAuthChanges")
        assert recordMatcher.row_matches(row, filters(actions=["constraintUpdate"]))
        assert not recordMatcher.row_matches(row, filters(actions=["constraintCreate"]))

    def test_legacy_outcome_and_ids_filter(self):
        row = parse(LEGACY_AUTHORIZATION_DENIED, "auditAuthorization")
        assert recordMatcher.row_matches(row, filters(outcomes=["denied"], users=["bob"]))
        assert not recordMatcher.row_matches(row, filters(outcomes=["success"]))

    def test_an_unread_row_matches_only_through_its_prefix(self):
        row = parse('[ACTIONS][type: t] [user: alice] [roles: []] [mfaEnabled: True] {"auditVersion": 9, '
                    '"databaseId": "db1"}')
        assert row.parseStatus == "unsupportedVersion"
        assert recordMatcher.row_matches(row, filters(users=["alice"]))
        assert not recordMatcher.row_matches(row, filters(databaseIds=["db1"]))

    def test_recheck_returns_the_matching_rows_only(self, writer):
        rows = [parse(one_action_message(writer, "assetUpdate", database_id=f"db{i}")) for i in (1, 2, 3)]
        kept = recordMatcher.recheck(rows, filters(databaseIds=["db2"]))
        assert isinstance(kept, list) and len(kept) == 1 and kept[0].databaseId == "db2"

    def test_recheck_gives_no_signal_about_a_term_that_only_a_hidden_field_holds(self, writer):
        # A record whose hidden field (changes) holds the term, and one that holds nothing: the
        # recheck result is the same for both, so the caller cannot tell them apart.
        hidden = parse(one_action_message(writer, "assetUpdate", database_id="db1",
                                          changes={"assetName": "ChangedSecretValue"}))
        empty = parse(one_action_message(writer, "assetUpdate", database_id="db1"))
        wanted = filters(contains=["changedsecretvalue"])
        assert recordMatcher.recheck([hidden], wanted) == recordMatcher.recheck([empty], wanted) == []
        assert not isinstance(recordMatcher.recheck([hidden], wanted), tuple)

    def test_contains_applies_on_the_redacted_row(self):
        row = parse(LEGACY_SEARCH)
        assert not recordMatcher.row_matches(row, filters(contains=["secret-term-777"]))
        assert recordMatcher.row_matches(row, filters(contains=["search"]))


INVISIBLE = [
    "\u034f", "\u0600", "\u0605", "\u06dd", "\u070f", "\u0890", "\u0891", "\u08e2", "\u115f", "\u1160",
    "\u17b4", "\u180b", "\u180e", "\u2800", "\u3164", "\uffa0", "\ufe00", "\ufe0f", "\U000e0100",
    "\U000e01ef", "\u00a0", "\u202f", "\u205f", "\u3000", "\u2009", "\u200b", "\u200d", "\u2060",
    "\u2066", "\u2069", "\ufeff", "\u00ad", "\ue000", "\U000f0000", "\ufdd0", "\uffff", "\u2028",
    "\u2029", "\u1680", "\u061c", "\u202e", "\U000e0041",
]


@pytest.mark.unit
class TestInvisibleCharacters:
    @pytest.mark.parametrize("char", INVISIBLE)
    def test_an_invisible_character_is_shown_as_an_escape(self, char):
        encoded = redaction.encode_text("a" + char + "b")
        assert char not in encoded and encoded.startswith("a\\u") and encoded.endswith("b")

    def test_the_rule_covers_every_code_point_of_the_unsafe_categories(self):
        import sys
        import unicodedata
        missing = [hex(code) for code in range(sys.maxunicode + 1)
                   if code != 0x20 and unicodedata.category(chr(code)) in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp", "Zs")
                   and not redaction.UNSAFE_CHARS.match(chr(code))]
        assert missing == []

    @pytest.mark.parametrize("text", ["plain ascii 123 ~!@#", "caf\u00e9 \u00fcber \u65e5\u672c\u8a9e \u0645\u0631\u062d\u0628\u0627",
                                      "emoji \U0001f600 \u2603", "a b"])
    def test_ordinary_text_and_the_plain_space_are_unchanged(self, text):
        assert redaction.encode_text(text) == text

    def test_a_name_made_of_invisible_characters_cannot_pass_as_empty(self):
        value, _ = redaction.redact_value({"name": "\u3164\u2800\u115f"})
        assert "\u3164" not in json.dumps(value, ensure_ascii=False)


@pytest.mark.unit
class TestMoreCredentialShapes:
    @pytest.mark.parametrize("text,gone", [
        ("token ghp_" + "a1B2c3D4e5" * 4 + " end", "ghp_a1B2c3D4e5"),
        ("github_pat_" + "11ABCDEFG0_" * 4 + " end", "github_pat_11ABCDEFG0_"),
        ("slack xoxb-1234567890-abcdefghij end", "xoxb-1234567890"),
        ("slack xapp-1-A0123456789-abcdef end", "xapp-1-A0123456789"),
        ("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpwYXNzd29yZDEyMw"),
        ("basic QWxhZGRpbjpvcGVuIHNlc2FtZQ", "QWxhZGRpbjpvcGVuIHNlc2FtZQ"),
        ("key AIza" + "A1b2C3d4E5" * 3 + "abcde end", "AIzaA1b2C3d4E5"),
        ("sk_live_" + "A1b2C3d4E5f6G7h8", "sk_live_A1b2"),
        ("npm_" + "A1b2C3d4E5" * 3 + "abcdef", "npm_A1b2C3d4E5"),
        ("clone https://deploy:s3cr3tPassw0rd@git.example.com/org/repo.git", "s3cr3tPassw0rd"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA"),
        ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq", "MIIEvQIBADANBgkq"),
    ])
    def test_the_credential_is_replaced(self, text, gone):
        value, _ = redaction.redact_value({"note": text})
        assert gone not in json.dumps(value) and "<redacted>" in json.dumps(value)

    @pytest.mark.parametrize("text", [
        "Basic settings", "Basic configuration", "basic authentication method", "Basic", "a basic idea",
        "ghp_ short", "ghost_" + "x" * 40, "xoxo hugs", "https://example.com:8080/path", "user@example.com",
        "https://example.com/a?b=c@d", "the PRIVATE KEY field", "-----BEGIN CERTIFICATE-----\nMIIC\n-----END CERTIFICATE-----",
        "AIzaShort", "sk_test_" + "a" * 20, "npm_install is a command", "see https://x.example/a:b",
    ])
    def test_ordinary_text_is_not_redacted(self, text):
        assert redaction.redact_string_shapes(text) == text

    def test_a_credential_after_a_long_prefix_is_still_replaced(self):
        text = "x " * 3000 + "ghp_" + "a1B2c3D4e5" * 4
        assert "ghp_a1B2" not in redaction.redact_string_shapes(text)

    def test_hostile_text_does_not_blow_up_the_scan(self):
        import time
        hostile = ["Basic " + "A" * 100000, "-----BEGIN " + "A " * 20000, "xoxb-" + "-" * 100000,
                   "https://" + "a" * 100000 + ":", "ghp_" + "a" * 100000, "Basic " + "+/" * 50000,
                   "://" * 30000, "-----BEGIN PRIVATE KEY-----" * 2000]
        started = time.monotonic()
        for text in hostile:
            redaction.redact_string_shapes(text)
        assert time.monotonic() - started < 3
