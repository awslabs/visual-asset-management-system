"""`pipeline list` must offer the same auto-pagination contract as every sibling list command.

Every other list command in the CLI -- `assets list`, `database list`, `file list`, `workflow list`,
`execution list` -- accepts `--auto-paginate` / `--max-items`. Without them a script that walks
pipelines has to hand-roll a `NextToken` loop that is unnecessary everywhere else.

Two behaviours of the pipeline list make the walk non-obvious, and both are pinned below:

* An **empty page can still carry a `NextToken`**. The backend filters archived and unauthorized
  rows *after* the DynamoDB page limit, which is why the single-page formatter already prints "No
  pipelines on this page; more pages available." A walk that breaks on `if not items` therefore
  under-reports instead of failing loudly.
* The no-database form lists every accessible pipeline through the constant-partition ByDate GSI
  and re-pays the per-page authorization fan-out, so the walk must be bounded by a page cap and
  must hand back the outstanding `NextToken` on every early stop -- exactly as
  `execution list` does (`vamscli/commands/execution.py:185-215`).

The flags are documented on the `pipeline list` reference page, and the documentation guard below is
what keeps that true.

Guards FIX-073 (S6-TOOLS-018): ``pipeline list`` lacking ``--auto-paginate``, unlike every
sibling list command.
"""

import json
import re
from pathlib import Path

import pytest

from vamscli.main import cli


REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_PIPELINES = REPO_ROOT / "documentation/docusaurus-site/docs/cli/commands/pipelines.md"
DOCS_COMMAND_REFERENCE = REPO_ROOT / "documentation/docusaurus-site/docs/cli/command-reference.md"

# Upper bound on mock pages, so a walk with no page cap fails this suite instead of hanging it.
_RUNAWAY_PAGE_LIMIT = 1000


def _page(items, next_token=None):
    """A backend page in the enveloped shape `list_pipelines` returns."""
    message = {"Items": list(items)}
    if next_token:
        message["NextToken"] = next_token
    return {"message": message}


def _endless_pages(next_token="tok-forever", items=None):
    """A side_effect that keeps handing back a page with a NextToken, bounded so a walk with no
    page cap raises instead of looping forever."""
    calls = {"n": 0}

    def _call(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] > _RUNAWAY_PAGE_LIMIT:
            raise AssertionError(
                f"the auto-paginate walk made more than {_RUNAWAY_PAGE_LIMIT} requests: it has no "
                "page cap, so a large deployment would never terminate"
            )
        return _page(items if items is not None else [], next_token)

    return _call


class TestPipelineListAutoPaginate:
    def test_options_are_declared_on_the_command(self, cli_runner, generic_command_mocks):
        """The flags must exist on `pipeline list`, matching the CLI-wide convention."""
        with generic_command_mocks("pipeline"):
            result = cli_runner.invoke(cli, ["pipeline", "list", "--help"])
            assert result.exit_code == 0, result.output
            assert "--auto-paginate" in result.output
            assert "--max-items" in result.output

    def test_auto_paginate_returns_every_item_exactly_once(self, cli_runner, generic_command_mocks):
        """The walk must follow NextToken to the end and report totals like `execution list`."""
        with generic_command_mocks("pipeline") as mocks:
            mocks["api_client"].list_pipelines.side_effect = [
                _page([{"pipelineId": "p1"}], "tok-1"),
                _page([{"pipelineId": "p2"}], "tok-2"),
                _page([{"pipelineId": "p3"}]),
            ]
            result = cli_runner.invoke(
                cli, ["pipeline", "list", "--auto-paginate", "--json-output"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            ids = [item["pipelineId"] for item in data["Items"]]
            assert ids == ["p1", "p2", "p3"], "every page's items, in order, exactly once"
            assert data["totalItems"] == 3
            assert data["pageCount"] == 3
            # A token on a completed walk sends the caller after a page that does not exist.
            assert "NextToken" not in data
            assert mocks["api_client"].list_pipelines.call_count == 3
            # The second and third requests must carry the previous page's token, otherwise the
            # walk re-reads page one and "three pages" is an illusion.
            tokens = [
                call.kwargs["params"].get("startingToken")
                for call in mocks["api_client"].list_pipelines.call_args_list
            ]
            assert tokens == [None, "tok-1", "tok-2"]

    def test_auto_paginate_continues_past_an_empty_page(self, cli_runner, generic_command_mocks):
        """An empty page with a NextToken must not end the walk.

        This is the specific backend behaviour the existing single-page formatter already documents
        ("No pipelines on this page; more pages available") -- archived and unauthorized rows are
        filtered after the DynamoDB page limit. A `if not items: break` walk passes every other test
        in this class and fails only here.
        """
        with generic_command_mocks("pipeline") as mocks:
            mocks["api_client"].list_pipelines.side_effect = [
                _page([{"pipelineId": "p1"}], "tok-1"),
                _page([], "tok-2"),                       # filtered page, more still to come
                _page([{"pipelineId": "p3"}]),
            ]
            result = cli_runner.invoke(
                cli, ["pipeline", "list", "--auto-paginate", "--json-output"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            ids = [item["pipelineId"] for item in data["Items"]]
            assert ids == ["p1", "p3"], "the walk stopped on the empty page and under-reported"
            assert mocks["api_client"].list_pipelines.call_count == 3

    def test_max_items_stop_keeps_the_outstanding_token_and_names_starting_token(
            self, cli_runner, generic_command_mocks):
        """Matching `execution list`, an early stop returns the token plus a resume note.

        Without the token a caller chunking a large deployment has to re-walk every page already
        paid for, and each re-walked page re-pays the per-page authorization fan-out.
        """
        with generic_command_mocks("pipeline") as mocks:
            mocks["api_client"].list_pipelines.return_value = _page(
                [{"pipelineId": "p1"}], "tok-resume")
            result = cli_runner.invoke(cli, [
                "pipeline", "list", "--auto-paginate", "--max-items", "1", "--json-output"])
            assert result.exit_code == 0, result.output
            assert mocks["api_client"].list_pipelines.call_count == 1
            data = json.loads(result.output)
            assert data["NextToken"] == "tok-resume"
            assert "--starting-token tok-resume" in data["note"]

    def test_page_cap_stop_terminates_and_keeps_the_outstanding_token(
            self, cli_runner, generic_command_mocks):
        """The bare (no database) walk crosses the whole deployment, so it must be bounded.

        Every page here is empty but tokened, so the --max-items item limit can never trigger; only
        a page cap can end the walk. The mock raises past a runaway threshold, so an uncapped
        implementation fails this test rather than hanging the suite.
        """
        with generic_command_mocks("pipeline") as mocks:
            mocks["api_client"].list_pipelines.side_effect = _endless_pages("tok-still-more")
            result = cli_runner.invoke(
                cli, ["pipeline", "list", "--auto-paginate", "--json-output"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert mocks["api_client"].list_pipelines.call_count < _RUNAWAY_PAGE_LIMIT
            assert data["NextToken"] == "tok-still-more"
            assert "--starting-token tok-still-more" in data["note"]
            assert data["pageCount"] == mocks["api_client"].list_pipelines.call_count

    def test_the_page_cap_is_a_named_constant(self):
        """The cap must be a named constant, with the same rationale as
        MAX_EXECUTION_AUTO_PAGINATE_PAGES -- not a literal buried in the walk."""
        from vamscli.commands import pipeline as pipeline_module

        # Match by suffix rather than an exact name, so reusing the execution constant is accepted.
        named = [n for n in vars(pipeline_module) if n.endswith("_AUTO_PAGINATE_PAGES")]
        assert named, "expected an imported *_AUTO_PAGINATE_PAGES constant bounding the walk"

    def test_auto_paginate_json_output_stays_pure(self, cli_runner, generic_command_mocks):
        """Per-page progress must be gated on `not json_output`.

        `execution list` prints "Fetched N ... (page K)" during its walk via output_status; the same
        line added ungated to the pipeline walk would break every `--json-output` consumer, which is
        what tests/test_json_output_purity.py exists to catch.
        """
        with generic_command_mocks("pipeline") as mocks:
            mocks["api_client"].list_pipelines.side_effect = [
                _page([{"pipelineId": "p1"}], "tok-1"),
                _page([{"pipelineId": "p2"}]),
            ]
            result = cli_runner.invoke(
                cli, ["pipeline", "list", "--auto-paginate", "--json-output"])
            assert result.exit_code == 0, result.output
            json.loads(result.output)  # raises if any progress line leaked into stdout
            assert "Fetched" not in result.output
            assert "Listing pipelines" not in result.output


class TestPipelineListDefaultPathUnchanged:
    """Control for the tests above: the single-page contract must survive the new flags.

    They are the guard that the flags stay additive -- an auto-paginate walk that becomes the
    default, or that reshapes the single-page payload, breaks every existing consumer of
    `pipeline list`.
    """

    def test_single_page_json_payload_is_the_backend_page_verbatim(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks("pipeline") as mocks:
            mocks["api_client"].list_pipelines.return_value = _page(
                [{"pipelineId": "p1"}], "tok-abc")
            result = cli_runner.invoke(cli, ["pipeline", "list", "--json-output"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert data == {"Items": [{"pipelineId": "p1"}], "NextToken": "tok-abc"}
            assert "autoPaginated" not in data
            assert mocks["api_client"].list_pipelines.call_count == 1

    def test_empty_page_with_a_next_token_still_says_more_pages_available(
            self, cli_runner, generic_command_mocks):
        # The message exists because the backend filters after the page limit. An auto-paginate
        # refactor that routes the default path through the walk would lose it.
        with generic_command_mocks("pipeline") as mocks:
            mocks["api_client"].list_pipelines.return_value = _page([], "tok-abc")
            result = cli_runner.invoke(cli, ["pipeline", "list"])
            assert result.exit_code == 0, result.output
            assert "No pipelines on this page; more pages available." in result.output
            assert "Next token: tok-abc" in result.output

    def test_no_new_request_is_made_without_the_flag(self, cli_runner, generic_command_mocks):
        with generic_command_mocks("pipeline") as mocks:
            mocks["api_client"].list_pipelines.return_value = _page(
                [{"pipelineId": "p1"}], "tok-abc")
            result = cli_runner.invoke(cli, ["pipeline", "list"])
            assert result.exit_code == 0, result.output
            assert mocks["api_client"].list_pipelines.call_count == 1


class TestListPipelinesClientContractUnchanged:
    """MCP fence. `tools/VamsMCP` imports this APIClient method directly and drives it through
    `VamsClient.paginate()`, which keys off the `Items` field inside the `message` envelope. A
    signature or shape change here breaks the MCP `list_pipelines` tool only at agent runtime, with
    no import-time or build-time signal -- so it is pinned from this side.

    """

    def test_signature_is_still_database_id_include_archived_params(self):
        import inspect

        from vamscli.utils.api_client import APIClient

        params = list(inspect.signature(APIClient.list_pipelines).parameters)
        assert params == ["self", "database_id", "include_archived", "params"]

    def test_command_still_calls_the_client_with_keyword_arguments(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks("pipeline") as mocks:
            mocks["api_client"].list_pipelines.return_value = _page([])
            result = cli_runner.invoke(
                cli, ["pipeline", "list", "-d", "my-db", "--include-archived"])
            assert result.exit_code == 0, result.output
            kwargs = mocks["api_client"].list_pipelines.call_args.kwargs
            assert kwargs["database_id"] == "my-db"
            assert kwargs["include_archived"] is True
            assert isinstance(kwargs["params"], dict)


class TestPipelinePaginationDocumentation:
    """The flags are only discoverable if the command's own reference page lists them."""

    def test_docs_list_the_new_pagination_options_for_pipeline_list(self):
        """The reference page for the command must list the flags, not only the scripting guide that
        uses them."""
        assert DOCS_PIPELINES.is_file(), DOCS_PIPELINES
        assert DOCS_COMMAND_REFERENCE.is_file(), DOCS_COMMAND_REFERENCE

        text = DOCS_PIPELINES.read_text(encoding="utf-8")
        # Scope to the `## pipeline list` section: the flags appearing anywhere else on the page
        # (for example under `pipeline template list`) would not document this command.
        match = re.search(r"^## pipeline list$(.*?)^## ", text, re.MULTILINE | re.DOTALL)
        assert match, "could not find the '## pipeline list' section in pipelines.md"
        section = match.group(1)
        assert "--auto-paginate" in section
        assert "--max-items" in section

        reference = DOCS_COMMAND_REFERENCE.read_text(encoding="utf-8")
        assert any(
            "pipeline list" in line and "--auto-paginate" in line
            for line in reference.splitlines()
        ), "command-reference.md shows no `pipeline list --auto-paginate` usage"
