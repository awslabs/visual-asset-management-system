"""The 'vamscli audit' commands: filters reaching the APIClient, the long-poll wait, paging, export,
stop, the generic errors and --json-output purity."""

import json
from unittest.mock import patch

import pytest

from vamscli.main import cli
from vamscli.utils.exceptions import (
    AuditQueryNotFoundError,
    AuditSearchError,
    AuditSearchNotSupportedError,
    InvalidAuditRequestError,
    PermissionDeniedError,
)

QID = "11111111-2222-3333-4444-555555555555"


def _row(n, **extra):
    row = {"timestamp": f"2026-11-17T01:00:0{n}.000Z", "groupKey": "auditActions", "action": "assetUpdate",
           "entity": {"type": "asset", "id": f"a-{n}"}, "user": "carol", "onBehalfOf": None,
           "outcome": "success", "recordId": f"rec-{n}"}
    row.update(extra)
    return row


def _complete(items, token=None, **extra):
    page = {"queryId": QID, "status": "Complete",
            "statistics": {"recordsScanned": 10, "bytesScanned": 100, "rowCount": len(items)},
            "Items": items, "warnings": []}
    if token:
        page["NextToken"] = token
    page.update(extra)
    return page


def _invoke(cli_runner, *args):
    return cli_runner.invoke(cli, ["audit", *args])


@pytest.fixture(autouse=True)
def _fresh_limits_cache():
    from vamscli.utils import audit_search
    audit_search._limits_cache.clear()
    yield
    audit_search._limits_cache.clear()


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("vamscli.utils.audit_search.time.sleep"):
        yield


class TestDeploymentWithoutAuditSearch:
    """With the deployment's feature list lacking AUDITLOGSEARCH every audit command says so and
    makes no API call, as the other feature-gated commands do."""

    COMMANDS = [
        ["groups"],
        ["search"],
        ["results", "--handle", QID],
        ["stop", "--handle", QID],
        ["export", "--handle", QID],
        ["show", "--handle", QID, "--record-id", "r1"],
    ]

    @pytest.mark.parametrize("args", COMMANDS, ids=lambda a: a[0])
    def test_not_enabled_message_and_no_api_call(self, cli_runner, generic_command_mocks, args):
        with generic_command_mocks("audit") as mocks:
            mocks["profile_manager"].has_feature_switch.side_effect = lambda name: name != "AUDITLOGSEARCH"
            mocks["profile_manager"].get_feature_switches_info.return_value = {
                "has_feature_switches": True, "enabled": ["GOVCLOUD"]}
            result = _invoke(cli_runner, *args)
        assert result.exit_code != 0
        assert "Audit Log Search is not enabled on this deployment" in result.output
        assert mocks["api_client"].method_calls == []

    def test_enabled_deployment_runs_the_command(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["profile_manager"].has_feature_switch.side_effect = lambda name: name == "AUDITLOGSEARCH"
            mocks["api_client"].list_audit_log_groups.return_value = {"logGroups": [], "limits": {}}
            result = _invoke(cli_runner, "groups", "--json-output")
        assert "not enabled on this deployment" not in result.output


class TestGroups:
    def test_json_shape_is_the_api_answer(self, cli_runner, generic_command_mocks):
        answer = {"logGroups": [{"key": "auditActions", "label": "Actions", "prefix": "ACTIONS",
                                 "description": "d", "sensitivity": []}],
                  "actions": [], "entityTypes": ["asset"], "outcomes": ["success"],
                  "limits": {"maxRows": 1000}}
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].list_audit_log_groups.return_value = answer
            result = _invoke(cli_runner, "groups", "--json-output")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == answer

    def test_text_view_and_empty_list(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].list_audit_log_groups.return_value = {"logGroups": [], "limits": {"maxRows": 5}}
            result = _invoke(cli_runner, "groups")
        assert result.exit_code == 0, result.output
        assert "may not search any audit log group" in result.output
        assert "maxRows: 5" in result.output

    def test_not_supported_is_a_clear_json_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].list_audit_log_groups.side_effect = AuditSearchNotSupportedError(
                "Audit Log Search is not available on this deployment")
            result = _invoke(cli_runner, "groups", "--json-output")
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["error_type"] == "AuditSearchNotSupportedError"
        assert "not available on this deployment" in data["error"]


class TestSearch:
    def test_filters_reach_the_api_and_rows_are_read(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running",
                                                  "warnings": []}
            api.get_audit_query.return_value = _complete([_row(1), _row(2)])
            result = _invoke(
                cli_runner, "search", "--group", "auditActions", "--group", "auditAuth",
                "--action", "assetUpdate", "--entity-type", "asset", "--entity-id", "a-1",
                "--user", "carol", "--api-key-id", "k1", "--database", "db", "--asset", "a-1",
                "--outcome", "success", "--outcome", "denied", "--request-id", "req-1",
                "--contains", "report.usd", "--contains", "x y z", "--from", "2026-11-01",
                "--to", "2026-11-02T00:00:00Z", "--include-probes", "--order", "asc", "--json-output")
        assert result.exit_code == 0, result.output
        body = api.start_audit_query.call_args.args[0]
        assert body == {
            "startTime": 1793491200, "endTime": 1793577600,
            "logGroups": ["auditActions", "auditAuth"], "actions": ["assetUpdate"],
            "entityTypes": ["asset"], "entityIds": ["a-1"], "users": ["carol"], "apiKeyIds": ["k1"],
            "databaseIds": ["db"], "assetIds": ["a-1"], "outcomes": ["success", "denied"],
            "requestIds": ["req-1"], "contains": ["report.usd", "x y z"], "includeProbes": True,
            "sort": "asc",
        }
        data = json.loads(result.output)
        assert [r["recordId"] for r in data["Items"]] == ["rec-1", "rec-2"]
        assert data["queryId"] == QID and data["status"] == "Complete"

    def test_defaults_send_a_24h_window_and_hide_probes(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            api.get_audit_query.return_value = _complete([])
            result = _invoke(cli_runner, "search", "--json-output")
        assert result.exit_code == 0, result.output
        body = api.start_audit_query.call_args.args[0]
        assert set(body) == {"startTime", "endTime", "includeProbes"}
        assert body["includeProbes"] is False
        assert 86390 <= body["endTime"] - body["startTime"] <= 86410

    def test_waits_with_long_poll_until_complete(self, cli_runner, generic_command_mocks):
        running = {"queryId": QID, "status": "Running", "statistics": {"recordsScanned": 1}, "Items": []}
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            api.get_audit_query.side_effect = [running, running, _complete([_row(1)])]
            result = _invoke(cli_runner, "search", "--json-output")
        assert result.exit_code == 0, result.output
        waits = [c.kwargs.get("wait") for c in api.get_audit_query.call_args_list]
        assert waits == [20, 20, 20]
        assert json.loads(result.output)["Items"][0]["recordId"] == "rec-1"

    def test_no_wait_prints_the_handle_only(self, cli_runner, generic_command_mocks):
        started = {"queryId": QID, "status": "Running", "warnings": ["widerScan"]}
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.return_value = started
            result = _invoke(cli_runner, "search", "--no-wait", "--json-output")
            mocks["api_client"].get_audit_query.assert_not_called()
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == started

    def test_no_wait_text_names_the_next_command(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            result = _invoke(cli_runner, "search", "--no-wait")
        assert f"vamscli audit results --handle {QID}" in result.output

    def test_max_rows_caps_the_rows_and_drops_the_resume_token(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            api.get_audit_query.side_effect = [
                _complete([_row(1), _row(2)], token="2"), _complete([_row(3), _row(4)], token="4")]
            result = _invoke(cli_runner, "search", "--max-rows", "3", "--page-size", "2", "--json-output")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["Items"]) == 3 and data["truncatedByMaxRows"] is True
        assert "NextToken" not in data

    @pytest.mark.parametrize("final", ["Failed", "Timeout", "Cancelled"])
    def test_a_search_that_ends_badly_is_an_error_with_the_handle(self, cli_runner, generic_command_mocks, final):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            mocks["api_client"].get_audit_query.return_value = {"queryId": QID, "status": final, "Items": []}
            result = _invoke(cli_runner, "search", "--json-output")
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["error_type"] == "AuditSearchFailedError"
        assert data["queryId"] == QID and data["status"] == final

    def test_client_timeout_reports_the_handle(self, cli_runner, generic_command_mocks):
        running = {"queryId": QID, "status": "Running", "Items": []}
        ticks = iter([0, 0, 5, 5, 10, 10, 15, 15, 20, 20])
        with generic_command_mocks("audit") as mocks, \
                patch("vamscli.utils.audit_search.time.monotonic", side_effect=lambda: next(ticks)):
            mocks["api_client"].start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            mocks["api_client"].get_audit_query.return_value = running
            result = _invoke(cli_runner, "search", "--timeout", "8", "--json-output")
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["error_type"] == "AuditSearchTimeoutError" and data["queryId"] == QID

    @pytest.mark.parametrize("argv", [
        ["--contains", "ab"],
        ["--contains", "a" * 65],
        ["--contains", "bad;term"],
        ["--contains", "abc", "--contains", "def", "--contains", "ghi", "--contains", "jkl"],
        ["--from", "yesterday-ish"],
        ["--from", "1h", "--to", "2h"],
        ["--action", ""],
        ["--user", "a\x01b"],
    ])
    def test_values_the_server_would_refuse_never_leave_the_machine(self, cli_runner, generic_command_mocks, argv):
        with generic_command_mocks("audit") as mocks:
            result = _invoke(cli_runner, "search", *argv, "--json-output")
            mocks["api_client"].start_audit_query.assert_not_called()
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["error_type"] == "InvalidAuditRequestError"

    def test_a_refused_contains_term_is_not_echoed(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            result = _invoke(cli_runner, "search", "--contains", "secret;value", "--json-output")
            mocks["api_client"].start_audit_query.assert_not_called()
        assert "secret;value" not in result.output

    def test_too_many_filter_values(self, cli_runner, generic_command_mocks):
        argv = []
        for n in range(21):
            argv += ["--action", f"a{n}"]
        with generic_command_mocks("audit") as mocks:
            result = _invoke(cli_runner, "search", *argv, "--json-output")
            mocks["api_client"].start_audit_query.assert_not_called()
        assert result.exit_code == 1

    def test_bad_outcome_and_order_are_click_errors(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit"):
            assert _invoke(cli_runner, "search", "--outcome", "maybe").exit_code == 2
            assert _invoke(cli_runner, "search", "--order", "sideways").exit_code == 2
            assert _invoke(cli_runner, "search", "--page-size", "501").exit_code == 2

    def test_the_generic_403_is_a_permission_denial(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.side_effect = PermissionDeniedError(
                "Access forbidden. Not Authorized")
            result = _invoke(cli_runner, "search", "--group", "auditAuth")
        # A permission denial is a global error: it passes the command and the entry point prints it.
        assert isinstance(result.exception, PermissionDeniedError)
        assert "Not Authorized" in str(result.exception)

    def test_a_400_names_nothing_but_the_generic_message(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.side_effect = InvalidAuditRequestError(
                "Audit request refused: Invalid audit search request")
            result = _invoke(cli_runner, "search", "--json-output")
        data = json.loads(result.output)
        assert result.exit_code == 1
        assert data["error_type"] == "InvalidAuditRequestError"
        assert data["error"] == "Audit request refused: Invalid audit search request"

    def test_throttled_start(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.side_effect = InvalidAuditRequestError(
                "Audit request refused: Too many audit searches are running. Try again later.")
            result = _invoke(cli_runner, "search")
        assert result.exit_code == 1
        assert "Too many audit searches" in result.output

    def test_candidate_limit_warning_is_shown_in_text(self, cli_runner, generic_command_mocks):
        page = _complete([_row(1)])
        page["statistics"]["candidateLimitReached"] = True
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            mocks["api_client"].get_audit_query.return_value = page
            result = _invoke(cli_runner, "search")
        assert "candidate limit" in result.output
        assert "1 matching records" in result.output


class TestResults:
    def test_one_page_with_a_token_hint(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.get_audit_query.return_value = _complete([_row(1)], token="100")
            result = _invoke(cli_runner, "results", "--handle", QID, "--page-token", "100", "--order", "asc")
        assert result.exit_code == 0, result.output
        assert "--page-token 100" in result.output
        kwargs = api.get_audit_query.call_args.kwargs
        assert kwargs["starting_token"] == "100" and kwargs["sort"] == "asc"

    def test_all_follows_tokens_to_the_cap(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.get_audit_query.side_effect = [
                _complete([_row(1), _row(2)], token="2"), _complete([_row(3), _row(4)], token="4"),
                _complete([_row(5)])]
            result = _invoke(cli_runner, "results", "--handle", QID, "--all", "--max-rows", "4",
                             "--page-size", "2", "--json-output")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert [r["recordId"] for r in data["Items"]] == ["rec-1", "rec-2", "rec-3", "rec-4"]
        assert api.get_audit_query.call_count == 2
        assert data["NextToken"] == "4"

    def test_all_reads_to_the_end(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.get_audit_query.side_effect = [_complete([_row(1)], token="1"), _complete([_row(2)])]
            result = _invoke(cli_runner, "results", "--handle", QID, "--all", "--json-output")
        data = json.loads(result.output)
        assert len(data["Items"]) == 2 and "NextToken" not in data

    def test_running_search_has_no_rows_and_says_so(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].get_audit_query.return_value = {
                "queryId": QID, "status": "Running", "statistics": {"recordsScanned": 3}, "Items": []}
            result = _invoke(cli_runner, "results", "--handle", QID)
        assert result.exit_code == 0
        assert "Status: Running" in result.output and "still running" in result.output

    def test_wait_follows_a_running_search_to_the_end(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.get_audit_query.side_effect = [
                {"queryId": QID, "status": "Running", "Items": []}, _complete([_row(1)])]
            result = _invoke(cli_runner, "results", "--handle", QID, "--wait", "--json-output")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["Items"][0]["recordId"] == "rec-1"

    def test_stopped_search_is_an_error_and_shows_no_rows(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].get_audit_query.return_value = {
                "queryId": QID, "status": "Cancelled", "Items": []}
            result = _invoke(cli_runner, "results", "--handle", QID, "--json-output")
        assert result.exit_code == 1
        assert json.loads(result.output)["status"] == "Cancelled"

    def test_expired_handle_is_one_generic_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].get_audit_query.side_effect = AuditQueryNotFoundError(
                "Search not found. The handle is unknown, has expired or is not yours.")
            result = _invoke(cli_runner, "results", "--handle", QID, "--json-output")
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["error_type"] == "AuditQueryNotFoundError"

    def test_a_non_numeric_page_token_is_refused_locally(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            result = _invoke(cli_runner, "results", "--handle", QID, "--page-token", "abc")
            mocks["api_client"].get_audit_query.assert_not_called()
        assert result.exit_code == 1

    def test_handle_is_required(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit"):
            assert _invoke(cli_runner, "results").exit_code == 2


class TestShow:
    def test_finds_a_record_on_a_later_page(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].get_audit_query.side_effect = [
                _complete([_row(1)], token="1"), _complete([_row(2, details={"k": "v"})])]
            result = _invoke(cli_runner, "show", "--handle", QID, "--record-id", "rec-2", "--json-output")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["details"] == {"k": "v"}

    def test_missing_record_is_a_generic_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].get_audit_query.return_value = _complete([_row(1)])
            result = _invoke(cli_runner, "show", "--handle", QID, "--record-id", "nope", "--json-output")
        assert result.exit_code == 1
        assert json.loads(result.output)["error_type"] == "AuditQueryNotFoundError"


class TestStop:
    def test_stop_needs_no_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].stop_audit_query.return_value = {"queryId": QID, "status": "Cancelled"}
            result = _invoke(cli_runner, "stop", "--handle", QID, "--json-output")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"queryId": QID, "status": "Cancelled"}
        mocks["api_client"].stop_audit_query.assert_called_once_with(QID)

    def test_stop_text(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].stop_audit_query.return_value = {"queryId": QID, "status": "Complete"}
            result = _invoke(cli_runner, "stop", "--handle", QID)
        assert f"Search {QID}: Complete" in result.output


class TestExport:
    def _chunks(self, api, *contents, fmt="csv"):
        pages = []
        for i, content in enumerate(contents):
            chunk = {"queryId": QID, "format": fmt, "content": content, "rowCount": 2, "warnings": []}
            if i < len(contents) - 1:
                chunk["NextToken"] = str((i + 1) * 2)
            pages.append(chunk)
        api.export_audit_query.side_effect = pages

    def test_chunks_are_joined_in_order_and_written_verbatim(self, cli_runner, generic_command_mocks, tmp_path):
        out = tmp_path / "audit.csv"
        hostile = "a,\"'=HYPERLINK(\"\"http://x\"\")\",b\r\n"
        with generic_command_mocks("audit") as mocks:
            self._chunks(mocks["api_client"], "h1,h2\n" + hostile, "c,d\n")
            result = _invoke(cli_runner, "export", "--handle", QID, "--format", "csv",
                             "-o", str(out), "--json-output")
        assert result.exit_code == 0, result.output
        assert out.read_bytes() == ("h1,h2\n" + hostile + "c,d\n").encode()
        assert json.loads(result.output) == {"queryId": QID, "format": "csv", "output": str(out),
                                             "rowCount": 4, "warnings": []}
        calls = mocks["api_client"].export_audit_query.call_args_list
        assert [c.args for c in calls] == [(QID, "csv", None), (QID, "csv", "2")]

    def test_ndjson_to_stdout_carries_nothing_else(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            self._chunks(mocks["api_client"], '{"a":1}\n', '{"a":2}\n', fmt="ndjson")
            result = _invoke(cli_runner, "export", "--handle", QID, "--format", "ndjson")
        assert result.exit_code == 0, result.output
        assert result.output == '{"a":1}\n{"a":2}\n'

    def test_json_output_needs_a_file(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            result = _invoke(cli_runner, "export", "--handle", QID, "--json-output")
            mocks["api_client"].export_audit_query.assert_not_called()
        assert result.exit_code == 1

    def test_an_existing_file_is_not_replaced_without_overwrite(self, cli_runner, generic_command_mocks, tmp_path):
        out = tmp_path / "audit.csv"
        out.write_text("keep")
        with generic_command_mocks("audit") as mocks:
            self._chunks(mocks["api_client"], "x\n")
            refused = _invoke(cli_runner, "export", "--handle", QID, "-o", str(out))
            assert refused.exit_code == 1 and out.read_text() == "keep"
            self._chunks(mocks["api_client"], "x\n")
            allowed = _invoke(cli_runner, "export", "--handle", QID, "-o", str(out), "--overwrite")
        assert allowed.exit_code == 0 and out.read_text() == "x\n"

    def test_format_json_is_not_offered(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit"):
            assert _invoke(cli_runner, "export", "--handle", QID, "--format", "json").exit_code == 2

    def test_export_of_an_unfinished_search_is_a_refusal(self, cli_runner, generic_command_mocks, tmp_path):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].export_audit_query.side_effect = InvalidAuditRequestError(
                "Audit request refused: The search has not finished")
            result = _invoke(cli_runner, "export", "--handle", QID, "--json-output", "-o", str(tmp_path / "x.csv"))
        assert result.exit_code == 1
        assert "has not finished" in json.loads(result.output)["error"]
        assert list(tmp_path.iterdir()) == []  # no partial file left behind


class TestHelpCrossReference:
    def test_audit_help_points_at_compliance_audit_and_back(self):
        from click.testing import CliRunner
        runner = CliRunner()
        audit_help = runner.invoke(cli, ["audit", "--help"]).output
        compliance_help = runner.invoke(cli, ["compliance", "audit", "--help"]).output
        assert "vamscli compliance audit" in audit_help
        assert "vamscli audit" in compliance_help


class TestExitStatusThroughTheEntryPoint:
    def test_a_failed_search_exits_nonzero_through_main(self, generic_command_mocks):
        from vamscli.main import main
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.side_effect = AuditSearchError("boom")
            with patch("sys.argv", ["vamscli", "audit", "search", "--json-output"]):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 1


class TestContainsLimitsOfTheDeployment:
    def _run(self, cli_runner, generic_command_mocks, limits, *terms):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.list_audit_log_groups.return_value = {"logGroups": [], "limits": limits}
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            args = ["search", "--no-wait", "--json-output"]
            for term in terms:
                args += ["--contains", term]
            return _invoke(cli_runner, *args), api

    def test_five_terms_of_128_characters_are_sent_when_the_deployment_allows_them(
            self, cli_runner, generic_command_mocks):
        terms = ["a" * 128] + ["bcd"] * 4
        result, api = self._run(cli_runner, generic_command_mocks,
                                {"maxContainsTerms": 5, "maxContainsTermLength": 128}, *terms)
        assert result.exit_code == 0, result.output
        assert api.start_audit_query.call_args.args[0]["contains"] == terms

    def test_a_deployment_with_lower_limits_is_enforced_before_sending(self, cli_runner,
                                                                         generic_command_mocks):
        result, api = self._run(cli_runner, generic_command_mocks,
                                {"maxContainsTerms": 1, "maxContainsTermLength": 5}, "abc", "def")
        assert result.exit_code == 1
        assert "At most 1" in json.loads(result.output)["error"]
        api.start_audit_query.assert_not_called()

    def test_defaults_apply_when_audit_groups_is_not_available(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.list_audit_log_groups.side_effect = AuditSearchNotSupportedError("no routes")
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            ok = _invoke(cli_runner, "search", "--no-wait", "--json-output", "--contains", "a" * 64)
            too_long = _invoke(cli_runner, "search", "--no-wait", "--json-output", "--contains", "a" * 65)
        assert ok.exit_code == 0 and too_long.exit_code == 1

    def test_a_search_without_contains_makes_no_extra_request(self, cli_runner, generic_command_mocks):
        result, api = self._run(cli_runner, generic_command_mocks, {})
        assert result.exit_code == 0
        api.list_audit_log_groups.assert_not_called()


class TestExportFolderCheck:
    def test_a_missing_folder_is_a_clear_error_before_any_request(self, cli_runner, generic_command_mocks,
                                                                  tmp_path):
        target = tmp_path / "no" / "such" / "x.csv"
        with generic_command_mocks("audit") as mocks:
            result = _invoke(cli_runner, "export", "--handle", QID, "-o", str(target), "--json-output")
            mocks["api_client"].export_audit_query.assert_not_called()
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["error_type"] == "InvalidAuditRequestError"
        assert f"The folder for '{target}' does not exist" in data["error"]
        assert ".part" not in data["error"]


class TestShowWithoutRecordIds:
    def test_says_how_many_rows_cannot_be_addressed(self, cli_runner, generic_command_mocks):
        legacy = {"timestamp": "t", "groupKey": "auditActions", "legacyType": "x", "user": "u"}
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].get_audit_query.return_value = _complete([legacy, legacy, _row(1)])
            result = _invoke(cli_runner, "show", "--handle", QID, "--record-id", "nope", "--json-output")
        assert result.exit_code == 1
        assert "2 rows of this search have no recordId" in json.loads(result.output)["error"]


class TestNotFoundHintNamesTheContainsRule:
    def test_text_hint_after_a_contains_search_was_exported(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].export_audit_query.side_effect = AuditQueryNotFoundError("gone")
            result = _invoke(cli_runner, "export", "--handle", QID)
        assert result.exit_code == 1
        assert "--contains cannot be read after it has been exported or stopped" in result.output


class TestHandleSurvivesAFailureWhileWaiting:
    def _start(self, api):
        api.start_audit_query.return_value = {"queryId": QID, "status": "Running", "warnings": []}

    def test_json_error_carries_the_handle(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            self._start(api)
            api.get_audit_query.side_effect = AuditSearchError("The audit search was throttled.")
            result = _invoke(cli_runner, "search", "--json-output")
        assert result.exit_code == 1
        assert json.loads(result.output)["queryId"] == QID

    def test_text_mode_prints_the_handle_at_the_start_and_after_the_failure(self, cli_runner,
                                                                            generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            self._start(api)
            api.get_audit_query.side_effect = AuditSearchError("The audit search was throttled.")
            result = _invoke(cli_runner, "search")
        assert result.exit_code == 1
        assert f"Search handle: {QID}" in result.output
        assert f"vamscli audit stop --handle {QID}" in result.output

    def test_ctrl_c_prints_the_handle_with_the_stop_command(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            self._start(api)
            api.get_audit_query.side_effect = KeyboardInterrupt()
            result = _invoke(cli_runner, "search", "--json-output")
        assert result.exit_code != 0
        assert f"vamscli audit stop --handle {QID}" in result.output


RECHECK = "recheckTimeBudget"


def _chunk(content, warnings=(), token=None, rows=2, fmt="csv"):
    chunk = {"queryId": QID, "format": fmt, "content": content, "rowCount": rows,
             "warnings": list(warnings)}
    if token:
        chunk["NextToken"] = token
    return chunk


class TestWarningsReachEveryOutput:
    """The API sets `recheckTimeBudget` on the page or export chunk whose server-side recheck ran
    out of time, so rows may be missing. Every read keeps the warnings of every page and chunk,
    once."""

    def test_results_all_keeps_a_later_page_s_warning(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].get_audit_query.side_effect = [
                _complete([_row(1)], token="1"), _complete([_row(2)], warnings=[RECHECK])]
            result = _invoke(cli_runner, "results", "--handle", QID, "--all", "--json-output")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["Items"]) == 2 and data["warnings"] == [RECHECK]

    def test_search_text_prints_a_later_page_s_warning_once(self, cli_runner,
                                                            generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running",
                                                  "warnings": []}
            api.get_audit_query.side_effect = [
                _complete([_row(1)], token="1"), _complete([_row(2)], warnings=[RECHECK])]
            result = _invoke(cli_runner, "search", "--page-size", "1", "--max-rows", "5")
        assert result.exit_code == 0, result.output
        assert result.output.count(f"Warning: {RECHECK}") == 1

    def test_search_json_keeps_the_start_and_the_page_warnings(self, cli_runner,
                                                               generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running",
                                                  "warnings": ["widerScan"]}
            api.get_audit_query.return_value = _complete([_row(1)], warnings=["widerScan", RECHECK])
            result = _invoke(cli_runner, "search", "--json-output")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["warnings"] == ["widerScan", RECHECK]

    def test_search_json_keeps_a_start_warning_the_page_does_not_repeat(self, cli_runner,
                                                                        generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running",
                                                  "warnings": ["widerScan"]}
            api.get_audit_query.return_value = _complete([_row(1)], warnings=[RECHECK])
            result = _invoke(cli_runner, "search", "--json-output")
        assert json.loads(result.output)["warnings"] == ["widerScan", RECHECK]

    def test_export_text_prints_a_chunk_warning_after_the_wrote_line(
            self, cli_runner, generic_command_mocks, tmp_path):
        out = tmp_path / "audit.csv"
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].export_audit_query.side_effect = [
                _chunk("h\na\n", token="2"), _chunk("b\n", warnings=[RECHECK])]
            result = _invoke(cli_runner, "export", "--handle", QID, "-o", str(out))
        assert result.exit_code == 0, result.output
        lines = result.output.strip().splitlines()
        assert lines[-2:] == [f"Wrote 4 records to {out}", f"Warning: {RECHECK}"]

    def test_export_to_stdout_keeps_the_text_clean_and_warns_on_stderr(self, cli_runner,
                                                                       generic_command_mocks):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].export_audit_query.side_effect = [
                _chunk('{"a":1}\n', token="2", fmt="ndjson"),
                _chunk('{"a":2}\n', warnings=[RECHECK], fmt="ndjson")]
            result = _invoke(cli_runner, "export", "--handle", QID, "--format", "ndjson")
        assert result.exit_code == 0, result.output
        assert result.stdout == '{"a":1}\n{"a":2}\n'
        assert f"Warning: {RECHECK}" in result.stderr

    def test_export_json_carries_each_chunk_warning_once(self, cli_runner, generic_command_mocks,
                                                         tmp_path):
        out = tmp_path / "audit.csv"
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].export_audit_query.side_effect = [
                _chunk("h\na\n", warnings=[RECHECK], token="2"), _chunk("b\n", warnings=[RECHECK])]
            result = _invoke(cli_runner, "export", "--handle", QID, "-o", str(out), "--json-output")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["warnings"] == [RECHECK]


class TestEveryAuditCommandReturnsItsResult:
    """Rule 16: a command returns what it printed, so `ctx.invoke` gets the result, not a
    sentinel."""

    @pytest.mark.parametrize("argv, expected", [
        (["groups"], {"logGroups": []}),
        (["search", "--no-wait"], {"queryId": QID, "status": "Running", "warnings": []}),
        (["search"], "page"),
        (["results", "--handle", QID], "page"),
        (["show", "--handle", QID, "--record-id", "rec-1"], "row"),
        (["stop", "--handle", QID], {"queryId": QID, "status": "Cancelled"}),
        (["export", "--handle", QID], "export"),
    ])
    def test_the_result_is_returned(self, cli_runner, generic_command_mocks, argv, expected):
        with generic_command_mocks("audit") as mocks:
            api = mocks["api_client"]
            api.list_audit_log_groups.return_value = {"logGroups": []}
            api.start_audit_query.return_value = {"queryId": QID, "status": "Running",
                                                  "warnings": []}
            api.get_audit_query.return_value = _complete([_row(1)])
            api.stop_audit_query.return_value = {"queryId": QID, "status": "Cancelled"}
            api.export_audit_query.side_effect = [_chunk("h\na\n", warnings=[RECHECK])]
            result = cli_runner.invoke(cli, ["audit", *argv], standalone_mode=False)
        assert result.exception is None, result.output
        returned = result.return_value
        if expected == "page":
            assert returned["queryId"] == QID
            assert [r["recordId"] for r in returned["Items"]] == ["rec-1"]
        elif expected == "row":
            assert returned == _row(1)
        elif expected == "export":
            assert returned == {"queryId": QID, "format": "csv", "output": None, "rowCount": 2,
                                "warnings": [RECHECK]}
        else:
            assert returned == expected
