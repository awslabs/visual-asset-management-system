# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Parsing audit records into rows: contract v1 records written by the real formatter, frozen legacy
records of the format before the record contract, and the forgery corpus (test plan section 2).

None of the hostile inputs may change the matched user, database, entity or outcome, and the parser
never raises.
"""

import importlib
import json

import pytest
from auditSearchRecords import (
    LEGACY_ACTIONS,
    LEGACY_AUTH_CHANGES,
    LEGACY_AUTH_OTHER,
    LEGACY_AUTHENTICATION,
    LEGACY_AUTHENTICATION_FAILED,
    LEGACY_AUTHORIZATION_ALLOWED,
    LEGACY_AUTHORIZATION_DENIED,
    LEGACY_ERRORS,
    LEGACY_FILE_DOWNLOAD,
    LEGACY_FILE_DOWNLOAD_STREAMED,
    LEGACY_FILE_UPLOAD,
    LEGACY_FILE_UPLOAD_DENIED,
    LEGACY_GATEWAY_DENIAL,
    LEGACY_ROLES_WITH_BRACKETS,
    LEGACY_SEARCH,
    api_event,
    auditLogging,
    contract_payload,
    descriptor,
    one_action_message,
    written,
)

parsers = importlib.import_module("common.auditSearch.parsers")
base = importlib.import_module("common.auditSearch.parsers.base")
rowModel = importlib.import_module("common.auditSearch.rowModel")

TS = "2026-11-16T12:00:00.000Z"


def parse(message, group="auditActions", length=None):
    return parsers.parse_record(message, descriptor(group), TS, len(message) if length is None else length)


# ---------------------------------------------------------------------------------------------------
# Contract v1: records written by the real formatter
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestContractRecords:
    def test_a_write_record_round_trips(self, writer):
        message = one_action_message(writer, "assetUpdate", database_id="db1", asset_id="a1",
                                     changes={"assetName": "New"}, previous={"assetName": "Old"})
        row = parse(message)
        assert (row.parseStatus, row.formatVersion, row.action, row.outcome) == ("parsed", "contract-v1",
                                                                                 "assetUpdate", "success")
        assert row.changes == {"assetName": "New"} and row.previous == {"assetName": "Old"}
        assert row.entity == {"type": "asset", "id": "a1"} and row.prefix == "ACTIONS"
        assert row.request["route"] == "/database/{databaseId}/assets/{assetId}"
        assert row.timestamp.startswith("20") and row.timestamp.endswith("Z")

    def test_a_read_record_has_request_parameters_and_item_count(self, writer):
        message = one_action_message(
            writer, "fileList", database_id="db1", asset_id="a1", item_count=25,
            event=api_event(path="/database/db1/assets/a1/listFiles", method="GET"),
            request_parameters={"maxItems": 100, "prefix": "dir/"})
        row = parse(message)
        assert row.requestParameters == {"maxItems": 100, "prefix": "dir/"} and row.itemCount == 25
        assert row.changes is None

    def test_api_key_and_cross_call_and_on_behalf_of_are_read(self, writer):
        message = one_action_message(writer, "assetUpdate", event=api_event(api_key_id="key-12345678"))
        row = parse(message)
        assert row.apiKeyId == "key-12345678" and row.authMethod == "apiKey"
        payload = contract_payload(message)
        payload["actor"].update({"onBehalfOf": "carol", "crossCall": True})
        record = message[:message.index("{")] + json.dumps(payload)
        row = parse(record)
        assert row.onBehalfOf == "carol" and row.crossCall is True and row.user == "alice"

    def test_a_denied_authorization_record_carries_the_checked_object(self, writer):
        auditLogging.log_authorization({"tokens": ["bob"], "roles": ["viewer"], "mfaEnabled": True}, False,
                                       {"action": "DELETE", "obj": {"object__type": "database",
                                                                    "databaseId": "db-1"}})
        (group, message), = written(writer)
        row = parse(message, group)
        assert group == "auditAuthorization"
        assert (row.outcome, row.user, row.formatVersion) == ("denied", "bob", "legacy")
        assert row.details == {"obj": {"object__type": "database", "databaseId": "db-1"}}

    def test_a_probe_record_is_marked(self):
        record = auditLogging.format_authorization_denial_record(
            {"tokens": ["bob"], "roles": ["viewer"], "mfaEnabled": True}, "GET /auth/routes/api/allowed",
            {"action": "DELETE", "obj": {"object__type": "api", "route__path": "/database/vams:any"}})
        row = parse(record, "auditAuthorization")
        assert row.probe is True and row.outcome == "denied" and row.user == "bob"

    def test_a_summary_probe_row_has_a_generic_label_and_names_no_group(self):
        record = auditLogging.format_authorization_denial_record(
            {"tokens": ["bob"], "roles": ["viewer"], "mfaEnabled": True}, "POST /audit/queries",
            {"action": "GET", "obj": {"object__type": "auditLog",
                                      "auditLogGroup": "auditAuthChanges,auditFileUpload"}})
        row = parse(record, "auditAuthorization")
        assert row.probe is True and row.outcome == "denied"
        assert row.action == "permission probe (summary)"
        assert row.entity == {"type": "auditLog", "id": None}
        assert "method" not in row.request
        text = json.dumps(row.to_dict())
        assert "auditAuthChanges" not in text and "auditFileUpload" not in text and "GET" not in text

    def test_a_probe_of_another_object_keeps_its_row_unchanged(self):
        record = auditLogging.format_authorization_denial_record(
            {"tokens": ["bob"], "roles": [], "mfaEnabled": True}, "GET /auth/routes/api/allowed",
            {"action": "DELETE", "obj": {"object__type": "api", "route__path": "/x"}})
        row = parse(record, "auditAuthorization")
        assert row.action is None and row.request.get("method") == "DELETE"

    def test_an_unmarked_denial_is_not_a_probe(self, writer):
        auditLogging.log_authorization({"tokens": ["bob"], "roles": [], "mfaEnabled": True}, False,
                                       {"action": "DELETE", "obj": {"object__type": "api"}})
        (group, message), = written(writer)
        assert parse(message, group).probe is False

    def test_a_probe_marker_inside_the_payload_does_not_mark_the_record(self, writer):
        message = one_action_message(writer, "assetUpdate", changes={"assetName": "[probe: GET /x] ok"})
        assert parse(message).probe is False

    def test_the_gateway_authorizer_record_with_an_unknown_user(self, writer):
        auditLogging.log_authorization_gateway(
            {"requestContext": {"http": {"sourceIp": "203.0.113.5", "method": "GET", "path": "/database"}}},
            False, "invalidToken")
        (group, message), = written(writer)
        row = parse(message, group)
        assert (row.user, row.outcome, row.groupKey) == ("unknown", "denied", "auditAuthorization")
        assert row.reason == "invalidToken" and row.request["sourceIp"] == "203.0.113.5"

    def test_file_records_have_no_type_segment(self, writer):
        auditLogging.log_file_upload(api_event(), "db-1", "a-1", "dir/f.glb", False)
        auditLogging.log_file_download(api_event(), "db-1", "a-1", "dir/f.glb")
        auditLogging.log_file_download_streamed(api_event(), "db-1", "a-1", "dir/f.glb")
        records = written(writer)
        assert {group for group, _ in records} == {"auditFileUpload", "auditFileDownload",
                                                   "auditFileDownloadStreamed"}
        for group, message in records:
            row = parse(message, group)
            assert row.parseStatus == "parsed" and row.databaseId == "db-1" and row.filePath == "dir/f.glb"
            assert row.outcome == "success" and row.legacyType is None

    def test_authentication_record(self, writer):
        auditLogging.log_authentication(api_event(), True, {"method": "cognito"})
        (group, message), = written(writer)
        row = parse(message, group)
        assert (group, row.outcome, row.user) == ("auditAuthentication", "success", "alice")

    def test_error_and_auth_records_by_the_real_writers(self, writer):
        auditLogging.log_errors(api_event(), "validation_error", {"error": "bad", "assetId": "a-1"})
        auditLogging.log_auth_changes(api_event(), "roleCreate", {"roleName": "r1", "operation": "create"})
        auditLogging.log_auth_other(api_event(), "userProfileUpdate", {"userId": "alice"})
        auditLogging.log_actions(api_event(), "edhAmiRecipe", {"operation": "create", "outcome": "in-progress"})
        rows = {group: parse(message, group) for group, message in written(writer)}
        assert rows["auditErrors"].outcome == "failed" and rows["auditErrors"].assetId == "a-1"
        assert rows["auditAuthChanges"].legacyType == "roleCreate"
        assert rows["auditAuthOther"].legacyType == "userProfileUpdate"
        assert rows["auditActions"].legacyType == "edhAmiRecipe" and rows["auditActions"].outcome == "success"

    def test_a_search_record_hides_what_was_typed(self, writer):
        message = one_action_message(
            writer, "search", event=api_event(path="/search", method="POST"),
            request_parameters={"from": 0, "size": 10, "searchText": {"query": "SECRET-TERM-777"}})
        row = parse(message)
        assert "SECRET-TERM-777" not in json.dumps(row.to_dict())

    def test_the_echo_never_reaches_the_row(self, writer):
        message = one_action_message(writer, "assetUpdate")
        serialized = json.dumps(parse(message).to_dict())
        for marker in ("header-marker", "query-marker", "body-marker", "secret-token-value", "/database/db1"):
            assert marker not in serialized

    def test_event_time_is_the_records_own(self, writer):
        message = one_action_message(writer, "assetUpdate")
        assert parse(message).timestamp == contract_payload(message)["eventTime"]


# ---------------------------------------------------------------------------------------------------
# Legacy: frozen records of the format before the record contract
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestLegacyRecords:
    @pytest.mark.parametrize("message,group,outcome,user", [
        (LEGACY_AUTHENTICATION, "auditAuthentication", "success", "alice"),
        (LEGACY_AUTHENTICATION_FAILED, "auditAuthentication", "failed", "unknown"),
        (LEGACY_AUTHORIZATION_ALLOWED, "auditAuthorization", "success", "alice"),
        (LEGACY_AUTHORIZATION_DENIED, "auditAuthorization", "denied", "bob"),
        (LEGACY_GATEWAY_DENIAL, "auditAuthorization", "denied", "unknown"),
        (LEGACY_FILE_UPLOAD, "auditFileUpload", "success", "carol"),
        (LEGACY_FILE_UPLOAD_DENIED, "auditFileUpload", "denied", "carol"),
        (LEGACY_FILE_DOWNLOAD, "auditFileDownload", "success", "dave"),
        (LEGACY_FILE_DOWNLOAD_STREAMED, "auditFileDownloadStreamed", "success", "dave"),
        (LEGACY_AUTH_CHANGES, "auditAuthChanges", "success", "admin"),
        (LEGACY_AUTH_OTHER, "auditAuthOther", "success", "alice"),
        (LEGACY_ERRORS, "auditErrors", "failed", "john.doe"),
        (LEGACY_ACTIONS, "auditActions", "success", "admin"),
    ])
    def test_legacy_outcome_and_user(self, message, group, outcome, user):
        row = parse(message, group)
        assert (row.parseStatus, row.formatVersion, row.outcome, row.user) == ("parsed", "legacy", outcome, user)
        assert row.action is None and row.groupKey == group

    def test_legacy_request_facts_come_from_the_echo_by_allowlist(self):
        row = parse(LEGACY_AUTHORIZATION_DENIED, "auditAuthorization")
        assert row.request == {"requestId": "req-legacy-1", "method": "PUT",
                               "route": "/database/{databaseId}/assets/{assetId}", "sourceIp": "198.51.100.9"}
        serialized = json.dumps(row.to_dict())
        assert "x-custom" not in serialized and "assetName" not in serialized
        assert "/database/db-1/assets/a-1" not in serialized.replace("/database/{databaseId}", "")

    def test_legacy_ids_and_types(self):
        row = parse(LEGACY_FILE_UPLOAD, "auditFileUpload")
        assert (row.databaseId, row.assetId, row.filePath) == ("db-1", "a-1", "/data/model.obj")
        assert row.details == {"customData": {"fileSize": 1024000}}
        assert parse(LEGACY_AUTH_CHANGES, "auditAuthChanges").legacyType == "roleCreate"
        assert parse(LEGACY_AUTH_CHANGES, "auditAuthChanges").ids == {"roleName": "r1"}

    def test_an_upload_denial_has_a_reason(self):
        assert parse(LEGACY_FILE_UPLOAD_DENIED, "auditFileUpload").reason == "extension"

    def test_a_legacy_search_record_hides_the_typed_text(self):
        row = parse(LEGACY_SEARCH)
        assert row.legacyType == "search"
        assert "SECRET-TERM-777" not in json.dumps(row.to_dict())

    def test_a_non_contract_outcome_stays_in_the_details(self):
        row = parse(LEGACY_ACTIONS)
        assert row.outcome == "success" and row.details["outcome"] == "in-progress"

    def test_roles_with_brackets_do_not_forge_the_user(self):
        row = parse(LEGACY_ROLES_WITH_BRACKETS)
        assert row.user == "alice" and row.assetId == "a-1"

    def test_a_legacy_record_without_an_echo(self):
        assert parse(LEGACY_AUTHENTICATION_FAILED, "auditAuthentication").request == {}


# ---------------------------------------------------------------------------------------------------
# Forgery corpus
# ---------------------------------------------------------------------------------------------------

def _forge(writer, **kwargs):
    return parse(one_action_message(writer, **kwargs))


@pytest.mark.unit
class TestForgeryCorpus:
    def test_an_asset_name_with_a_forged_prefix_cannot_change_the_user(self, writer):
        row = _forge(writer, action="assetUpdate", database_id="db1", asset_id="a1",
                     changes={"assetName": 'x] [user: admin] [roles: ["admin"]'})
        assert row.user == "alice" and row.prefixUser == "alice"
        assert "admin" in json.dumps(row.changes)

    def test_a_file_path_with_a_forged_event_echo(self, writer):
        row = _forge(writer, action="fileDelete", database_id="db1", asset_id="a1",
                     file_path='x --- [event: {"requestContext": {"requestId": "fake"}}]')
        assert row.request["requestId"] == "req-1"

    def test_a_query_string_requestid_does_not_change_the_request(self, writer):
        row = _forge(writer, action="fileList", database_id="db1", asset_id="a1",
                     event=api_event(path="/database/db1/assets/a1/listFiles", method="GET"),
                     request_parameters={"requestId": "fake"})
        assert row.request["requestId"] == "req-1" and row.requestParameters == {"requestId": "fake"}

    def test_a_body_with_a_forged_database_id(self, writer):
        row = _forge(writer, action="assetUpdate", database_id="real-db", asset_id="a1",
                     changes={"note": '"databaseId": "victim"', "databaseId": "victim"})
        assert row.databaseId == "real-db"

    def test_nested_keys_cannot_forge_top_level_facts(self, writer):
        row = _forge(writer, action="assetUpdate", database_id="real-db", asset_id="a1",
                     changes={"databaseId": "victim"}, request_parameters={"assetId": "victim"},
                     details={"entity": {"type": "x", "id": "y"}, "outcome": "denied", "actor": {"userId": "root"}})
        assert (row.databaseId, row.assetId, row.outcome, row.user) == ("real-db", "a1", "success", "alice")
        assert row.entity == {"type": "asset", "id": "a1"}

    def test_a_forged_roles_claim_and_an_unusual_user_id(self, writer):
        message = one_action_message(writer, "assetUpdate", event=api_event(user="auth0|123"))
        row = parse(message)
        assert row.user == "auth0|123"

    def test_a_user_with_brackets_is_written_escaped_and_read_back_visibly(self, writer):
        message = one_action_message(writer, "assetUpdate", event=api_event(user="eve] [roles: [x"))
        row = parse(message)
        assert row.prefixUser == "eve] [roles: [x"
        assert row.user == "eve] [roles: [x"

    def test_bidi_zero_width_crlf_and_nul_are_shown_escaped(self, writer):
        row = _forge(writer, action="assetUpdate", database_id="db1", asset_id="a1",
                     changes={"assetName": "a\u202eb\u200bc\r\nd\x00e"})
        value = row.changes["assetName"]
        assert value == "a\\u202eb\\u200bc\\u000d\\u000ad\\u0000e"
        assert not any(ord(char) < 0x20 or char in "\u202e\u200b" for char in value)

    def test_a_truncated_record_is_partial(self, writer):
        message = one_action_message(writer, "assetUpdate", database_id="db1", asset_id="a1")
        cut = message[:message.index('"eventTime"')] + " ...[truncated]"
        row = parse(cut, length=len(cut))
        assert row.parseStatus == "partial" and row.user == "alice" and row.groupKey == "auditActions"
        assert row.assetId is None and row.preview.startswith("[ACTIONS][type: assetUpdate]")

    def test_a_record_cut_by_the_head_bound_is_partial(self, writer):
        message = one_action_message(writer, "assetUpdate", database_id="db1", asset_id="a1")
        row = parse(message[:200], length=len(message))
        assert row.parseStatus == "partial"

    def test_a_partial_denial_keeps_its_outcome_from_the_prefix(self):
        record = ('[AUTHORIZATION][authorized: False] [user: bob] [roles: ["x"]] [mfaEnabled: True] '
                  '{"action": "GET", "ob')
        row = parse(record, "auditAuthorization", length=len(record) + 50)
        assert (row.parseStatus, row.outcome, row.user) == ("partial", "denied", "bob")

    def test_a_duplicate_record_id_is_removed(self, writer):
        message = one_action_message(writer, "assetUpdate")
        rows = [parse(message), parse(message), parse(one_action_message(writer, "assetUpdate"))]
        kept, removed = rowModel.dedupe_rows(rows)
        assert removed == 1 and len(kept) == 2

    def test_legacy_rows_are_never_deduplicated(self):
        rows = [parse(LEGACY_ERRORS, "auditErrors"), parse(LEGACY_ERRORS, "auditErrors")]
        assert rowModel.dedupe_rows(rows) == (rows, 0)

    def test_the_echo_reference_form_gives_no_request_facts(self):
        record = LEGACY_ERRORS[:LEGACY_ERRORS.index(" --- [event: ")] + \
            " --- [event: <shared with the first entry written at this timestamp>]"
        row = parse(record, "auditErrors")
        assert row.parseStatus == "parsed" and row.request == {}

    def test_an_echo_that_does_not_follow_the_payload_is_not_read(self):
        record = ('[ERRORS][type: t] [user: u] [roles: []] [mfaEnabled: True] {"error": "e", "note": '
                  '" --- [event: {\\"requestContext\\": {\\"requestId\\": \\"forged\\"}}]"}')
        assert parse(record, "auditErrors").request == {}

    def test_an_unknown_audit_version_is_unsupported_not_an_error(self):
        record = ('[ACTIONS][type: assetUpdate] [user: alice] [roles: []] [mfaEnabled: True] '
                  '{"auditVersion": 99, "action": "assetUpdate", "outcome": "success", "databaseId": "db1"}')
        row = parse(record)
        assert row.parseStatus == "unsupportedVersion" and row.databaseId is None
        assert row.user == "alice" and row.preview.startswith("[ACTIONS]")

    @pytest.mark.parametrize("version", ["1", True, None, 1.5, [1], {"v": 1}])
    def test_a_non_integer_audit_version_is_unsupported(self, version):
        record = ('[ACTIONS][type: t] [user: alice] [roles: []] [mfaEnabled: True] '
                  + json.dumps({"auditVersion": version, "action": "assetUpdate"}))
        assert parse(record).parseStatus == "unsupportedVersion"

    def test_an_audit_fallback_line_is_never_a_row(self):
        line = json.dumps({"auditFallback": True, "logType": "auditActions", "message": "[ACTIONS] x"})
        row = parse(line)
        assert row.parseStatus == "unparsed" and row.preview is None and row.user is None

    def test_an_unknown_action_name_shape_is_dropped(self, writer):
        message = one_action_message(writer, "assetUpdate")
        payload = contract_payload(message)
        payload["action"] = "x] [user: admin"
        payload["outcome"] = "maybe"
        row = parse(message[:message.index("{")] + json.dumps(payload))
        assert row.action is None and row.outcome is None

    @pytest.mark.parametrize("hostile", [
        None, 5, b"bytes", "", "{", "[", "[ACTIONS]", "[ACTIONS][type: x] [user: ", "\x00" * 100,
        "[ACTIONS][type: x] [user: u] [roles: [[[[[[[[[[",
        "[ACTIONS][type: x] [user: u] [roles: []] [mfaEnabled: True] " + "[" * 5000,
        "[ACTIONS][type: x] [user: u] [roles: []] [mfaEnabled: True] {" + '"a": {' * 3000,
        "[ACTIONS][type: x] [user: u] [roles: []] [mfaEnabled: True] [1, 2]",
        "[ACTIONS][type: x] [user: u] [roles: []] [mfaEnabled: True] not json at all",
        "[ACTIONS][type: x] [user: u] [roles: {\"a\": 1}] [mfaEnabled: True] {}",
    ])
    def test_the_parser_never_raises_on_hostile_input(self, hostile):
        row = parsers.parse_record(hostile, descriptor("auditActions"), TS, 10 ** 6)
        assert row.groupKey == "auditActions"
        assert row.parseStatus in rowModel.PARSE_STATUSES

    def test_a_group_whose_adapter_is_missing_is_unparsed(self):
        group = descriptor("auditErrors")._replace(legacy_parser="noSuchAdapter")
        row = parsers.parse_record(LEGACY_ERRORS, group, TS, len(LEGACY_ERRORS))
        assert row.parseStatus == "unparsed" and row.user == "john.doe"

    def test_a_parser_that_fails_is_contained(self, monkeypatch):
        monkeypatch.setitem(parsers.CONTRACT_PARSERS, 1, lambda *args: 1 / 0)
        row = parse('[ACTIONS][type: t] [user: u] [roles: []] [mfaEnabled: True] {"auditVersion": 1}')
        assert row.parseStatus == "unparsed"

    def test_nested_value_bounds_and_credential_shapes(self, writer):
        deep = current = {}
        for _ in range(12):
            current["n"] = {}
            current = current["n"]
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
        note = (f"see Bearer {token} and AKIAABCDEFGHIJKLMNOP and "
                "https://b.s3.amazonaws.com/k?X-Amz-Signature=abc123&x=1")
        row = _forge(writer, action="assetUpdate", database_id="db1", asset_id="a1",
                     changes={"deep": deep, "note": note})
        text = json.dumps(row.to_dict())
        assert token not in text and "AKIAABCDEFGHIJKLMNOP" not in text and "abc123" not in text
        assert row.truncated is True

    def test_base_never_reads_the_echo_when_the_payload_did_not_decode(self):
        record = base.split_record('[ERRORS][type: t] [user: u] [roles: []] [mfaEnabled: True] nope --- [event: {}]')
        assert record.payload is None and base.echo_request_facts(record) == {}
