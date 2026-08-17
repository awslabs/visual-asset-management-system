"""Tests for the MCP tool functions in vams_mcp.server.

Tools reference the module-global CLIENT, so we patch it with a MagicMock.
No live VAMS deployment or vamscli profile is required.
"""

from unittest.mock import MagicMock

import pytest

from vams_mcp import server


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(server, "CLIENT", client)
    return client


@pytest.fixture
def real_paginate_client(mock_client):
    """A mock client whose paginate/unwrap_message are the REAL implementations.

    The page-metadata helpers wrap paginate() rather than replacing it, so a MagicMock paginate
    cannot show what the assembled result holds.
    """
    mock_client.config = server.CONFIG
    mock_client.unwrap_message = server.VamsClient.unwrap_message
    mock_client.paginate = lambda *args, **kwargs: server.VamsClient.paginate(
        mock_client, *args, **kwargs
    )
    return mock_client


def test_list_databases_uses_pagination(mock_client):
    mock_client.paginate.return_value = {"Items": [{"databaseId": "db1"}], "count": 1}
    result = server.list_databases()
    assert result == {"Items": [{"databaseId": "db1"}], "count": 1}
    assert mock_client.paginate.called


def test_get_asset_calls_api(mock_client):
    mock_client.api.get_asset.return_value = {"assetId": "a1"}
    result = server.get_asset("db1", "a1")
    assert result == {"assetId": "a1"}
    mock_client.api.get_asset.assert_called_once_with("db1", "a1", show_archived=False)


def test_tool_result_wraps_errors(mock_client):
    mock_client.api.get_database.side_effect = RuntimeError("boom")
    result = server.get_database("missing")
    assert result["error"] == "boom"
    assert result["error_type"] == "RuntimeError"


def test_search_assets_builds_request(mock_client):
    mock_client.api.search_query.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    mock_client.trim_search_results.return_value = {"total": 0, "returned": 0, "results": []}

    server.search_assets(query="bridge", database_id="db1", size=5)

    assert mock_client.api.search_query.called
    request = mock_client.api.search_query.call_args.args[0]
    assert request["entityTypes"] == ["asset"]
    assert request["query"] == "bridge"
    assert request["size"] == 5
    # database_id becomes a filter
    assert any("db1" in str(f) for f in request.get("filters", []))


def test_search_files_entity_type(mock_client):
    mock_client.api.search_query.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    mock_client.trim_search_results.return_value = {"total": 0, "returned": 0, "results": []}
    server.search_files(query="texture")
    request = mock_client.api.search_query.call_args.args[0]
    assert request["entityTypes"] == ["file"]


def test_search_assets_passes_geo_search(mock_client):
    mock_client.api.search_query.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    mock_client.trim_search_results.return_value = {"total": 0, "returned": 0, "results": []}

    geo = {"point": {"lat": 47.6, "lon": -122.3, "radiusMeters": 500}, "relation": "within"}
    server.search_assets(query="bridge", geo_search=geo)

    request = mock_client.api.search_query.call_args.args[0]
    assert request["geoSearch"] == geo


def test_list_assets_without_database_lists_all(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_assets()
    fetch = mock_client.paginate.call_args.args[0]
    mock_client.get_json.return_value = {"Items": []}
    fetch({"pageSize": 10})
    assert mock_client.get_json.call_args.args[0] == "/assets"


def test_list_asset_versions_uses_versions_key(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_asset_versions("db1", "a1")
    assert mock_client.paginate.call_args.kwargs["items_key"] == "versions"


def test_list_asset_files_uses_lowercase_items_key(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_asset_files("db1", "a1")
    assert mock_client.paginate.call_args.kwargs["items_key"] == "items"


def test_list_workflow_executions_caps_page_size(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_workflow_executions("db1", "a1")
    assert mock_client.paginate.call_args.kwargs["page_size"] <= 50


def test_list_workflow_executions_forwards_the_workflow_filters(mock_client):
    """Both halves of the composite must reach the APIClient by KEYWORD.

    Its signature is (database_id, asset_id, workflow_database_id, workflow_id, params) -- database
    BEFORE id -- so passing these positionally in the caller's own argument order silently swaps
    them, and the filter then matches nothing.
    """
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_workflow_executions(
        "db1", "a1", workflow_id="wf-alpha", workflow_database_id="wdb-one")

    # paginate() is handed a lambda; invoke it to observe the real APIClient call.
    mock_client.paginate.call_args.args[0]({"pageSize": 50})
    kwargs = mock_client.api.list_workflow_executions.call_args.kwargs
    assert kwargs["workflow_id"] == "wf-alpha"
    assert kwargs["workflow_database_id"] == "wdb-one"


def test_list_workflow_executions_omits_absent_filters(mock_client):
    """Unfiltered stays unfiltered: passing empty strings would filter on "" if the route ever
    compared them literally."""
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_workflow_executions("db1", "a1")

    mock_client.paginate.call_args.args[0]({"pageSize": 50})
    kwargs = mock_client.api.list_workflow_executions.call_args.kwargs
    assert kwargs["workflow_id"] is None
    assert kwargs["workflow_database_id"] is None


# --- list_tags / list_tag_types database + scope --------------------------


def test_list_tags_forwards_database_and_scope(mock_client):
    """database/scope must reach get_tags by KEYWORD as database_id/scope, or the tag namespacing
    filter silently does nothing."""
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_tags(database="db1", scope="all")

    mock_client.paginate.call_args.args[0]({"pageSize": 100})
    kwargs = mock_client.api.get_tags.call_args.kwargs
    assert kwargs["database_id"] == "db1"
    assert kwargs["scope"] == "all"


def test_list_tags_omits_absent_database_and_scope(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_tags()

    mock_client.paginate.call_args.args[0]({"pageSize": 100})
    kwargs = mock_client.api.get_tags.call_args.kwargs
    assert kwargs["database_id"] is None
    assert kwargs["scope"] is None


def test_list_tag_types_forwards_database_and_scope(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_tag_types(database="db1", scope="global")

    mock_client.paginate.call_args.args[0]({"pageSize": 100})
    kwargs = mock_client.api.get_tag_types.call_args.kwargs
    assert kwargs["database_id"] == "db1"
    assert kwargs["scope"] == "global"


def test_list_tag_types_omits_absent_database_and_scope(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_tag_types()

    mock_client.paginate.call_args.args[0]({"pageSize": 100})
    kwargs = mock_client.api.get_tag_types.call_args.kwargs
    assert kwargs["database_id"] is None
    assert kwargs["scope"] is None


@pytest.mark.asyncio
async def test_read_tools_registered():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    for expected in (
        "list_databases",
        "get_asset",
        "search_assets",
        "find_and_summarize",
        "list_allowed_api_routes",
        "get_asset_history",
    ):
        assert expected in names


@pytest.mark.asyncio
async def test_write_tools_gated_off_by_default():
    # The test environment sets no VAMS_ENABLE_* vars, so mutating tools must
    # not be registered at all.
    names = {t.name for t in await server.mcp.list_tools()}
    for gated in ("create_asset", "execute_workflow", "delete_asset", "archive_asset"):
        assert gated not in names


def test_generate_download_url_is_non_mutating(mock_client):
    mock_client.api.download_asset_file.return_value = {"url": "https://example/presigned"}
    result = server.generate_download_url("db1", "a1", file_key="model.glb")
    assert result == {"url": "https://example/presigned"}
    mock_client.api.download_asset_file.assert_called_once()


# --- Source-structure checks ---------------------------------------------
#
# The tool functions are plain module-level defs, so two of the ways this file can
# break leave a valid, importable module and are invisible to the tests above:
# a repeated name silently shadows the earlier def, and a def placed past the
# `if __name__` entrypoint (or outside its gate block) is simply never executed.
# Neither shows up as a missing tool at the default gate settings, because the
# gated tools are not expected to be registered there anyway. So assert against
# the source layout.

import re  # noqa: E402
from pathlib import Path  # noqa: E402

SOURCE_PATH = Path(server.__file__)
SOURCE_LINES = SOURCE_PATH.read_text(encoding="utf-8").splitlines()


def _tool_definitions():
    """Every @mcp.tool()-decorated def, as (line_number, name)."""
    found = []
    for i, line in enumerate(SOURCE_LINES, start=1):
        match = re.match(r"^\s*def (\w+)\(", line)
        # The decorator order is @mcp.tool() then @tool_result, so the line
        # directly above the def is @tool_result.
        if match and i >= 2 and "tool_result" in SOURCE_LINES[i - 2]:
            found.append((i, match.group(1)))
    return found


def _line_of(prefix):
    for i, line in enumerate(SOURCE_LINES, start=1):
        if line.startswith(prefix):
            return i
    raise AssertionError(f"{prefix!r} not found in {SOURCE_PATH.name}")


def test_no_duplicate_tool_names():
    names = [name for _, name in _tool_definitions()]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"tool names defined more than once (later def wins): {duplicates}"


def test_every_tool_is_defined_before_the_entrypoint():
    # A def after `if __name__ == "__main__":` never runs on import, and if it is
    # indented under it, never runs as a script either.
    entrypoint = _line_of('if __name__ == "__main__":')
    stranded = [name for line, name in _tool_definitions() if line > entrypoint]
    assert not stranded, f"tools defined after the entrypoint, so never registered: {stranded}"


def test_mutating_tools_live_inside_their_gate_block():
    writes = _line_of("if CONFIG.enable_writes:")
    destructive = _line_of("if CONFIG.enable_destructive:")
    main = _line_of("def main()")

    by_name = {name: line for line, name in _tool_definitions()}
    # Representative tools that must be gated; a read tool must NOT be.
    for name in ("create_pipeline", "execute_workflow", "update_workflow"):
        assert writes < by_name[name] < destructive, f"{name} is outside the writes block"
    for name in ("archive_pipeline", "permanent_delete_execution", "delete_asset"):
        assert destructive < by_name[name] < main, f"{name} is outside the destructive block"
    for name in ("list_pipelines", "get_execution_details", "page_execution_detail_metadata"):
        assert by_name[name] < writes, f"{name} is a read tool but sits in a gated block"


def test_no_read_tool_calls_a_mutating_apiclient_method():
    """The read section is registered unconditionally, so a mutating call placed there is reachable
    with both gates off — a security defect, not a mis-filing. Checked over EVERY read tool rather
    than a representative few."""
    writes = _line_of("if CONFIG.enable_writes:")
    mutating_prefixes = (
        "create_", "update_", "delete_", "set_", "archive_", "unarchive_", "execute_", "rerun_",
        "abort_", "permanent_", "revert_", "move_", "copy_", "import_", "reset_", "initialize_",
        "complete_",
    )

    offenders = []
    for line, name in _tool_definitions():
        if line >= writes:
            continue
        source = _source_of(name)
        for called in re.findall(r"CLIENT\.api\.(\w+)\(", source):
            if called.startswith(mutating_prefixes):
                offenders.append(f"{name} -> APIClient.{called}")
        # A raw POST escape hatch would bypass the APIClient check above.
        assert "CLIENT.post_json" not in source, f"{name} POSTs raw from the read section"
    assert not offenders, f"read-section tools calling mutating APIClient methods: {offenders}"


@pytest.mark.asyncio
async def test_destructive_tools_gated_off_by_default():
    names = {t.name for t in await server.mcp.list_tools()}
    for gated in (
        "archive_pipeline",
        "unarchive_workflow",
        "delete_pipeline_template",
        "delete_workflow_trigger",
        "permanent_delete_execution",
    ):
        assert gated not in names


@pytest.mark.asyncio
async def test_orchestration_read_tools_registered():
    names = {t.name for t in await server.mcp.list_tools()}
    for expected in (
        "list_pipelines",
        "get_pipeline",
        "list_pipeline_templates",
        "get_pipeline_template",
        "get_pipeline_template_tag_schema",
        "get_workflow",
        "list_workflow_triggers",
        "get_workflow_trigger",
        "list_executions",
        "get_execution_details",
        "page_execution_detail_metadata",
        "get_execution_logs",
    ):
        assert expected in names


def test_every_tool_call_targets_a_real_apiclient_method():
    # Tools call APIClient methods directly, so a renamed or removed CLI method
    # only fails when the tool is invoked against a live deployment.
    from vamscli.utils.api_client import APIClient

    source = SOURCE_PATH.read_text(encoding="utf-8")
    called = set(re.findall(r"CLIENT\.api\.(\w+)\(", source))
    assert called, "no CLIENT.api.* calls found — the regex or the file changed"
    missing = sorted(name for name in called if not hasattr(APIClient, name))
    assert not missing, f"tools call APIClient methods that do not exist: {missing}"


def test_list_executions_caps_page_size(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_executions()
    assert mock_client.paginate.call_args.kwargs["page_size"] <= 50


# --- Page metadata a list must not swallow ---------------------------------
#
# paginate() rebuilds its result from the accumulated items alone. A `warnings` entry names rows a
# page WITHHELD, so dropping it turns a short list into one that reads as complete — the agent then
# reports an understated count or "no such execution".

_WITHHELD_WARNING = (
    "This page reached the limit of 200 distinct assets resolved for permission checks, so some "
    "executions were not evaluated and are not listed."
)


def test_list_executions_surfaces_page_warnings_and_flags_truncated(real_paginate_client):
    # The final page reports no NextToken, so nothing else would mark this result incomplete.
    real_paginate_client.api.list_executions.side_effect = [
        {"message": {"Items": [{"workflowExecutionId": "e1"}], "NextToken": "t1",
                     "warnings": [_WITHHELD_WARNING]}},
        {"message": {"Items": [{"workflowExecutionId": "e2"}], "warnings": [_WITHHELD_WARNING]}},
    ]

    result = server.list_executions()

    assert [row["workflowExecutionId"] for row in result["Items"]] == ["e1", "e2"]
    # Reported once even though both pages carried it.
    assert result["warnings"] == [_WITHHELD_WARNING]
    assert result["truncated"] is True


def test_list_executions_echoes_the_applied_date_window(real_paginate_client):
    real_paginate_client.api.list_executions.side_effect = [
        {"message": {"Items": [], "filterStartDate": "2026-05-11", "filterEndDate": "2026-08-09"}},
    ]

    result = server.list_executions()

    # Without the echo an agent is never told it saw only the last 90 days.
    assert result["filterStartDate"] == "2026-05-11"
    assert result["filterEndDate"] == "2026-08-09"


def test_list_executions_clean_walk_adds_no_warning_keys(real_paginate_client):
    real_paginate_client.api.list_executions.side_effect = [
        {"message": {"Items": [{"workflowExecutionId": "e1"}]}},
    ]

    result = server.list_executions()

    assert result["count"] == 1, "items must still be read from the page (one unwrap, not two)"
    assert "warnings" not in result
    assert result.get("truncated") is None


def test_list_executions_forwards_the_filters_on_every_page(real_paginate_client):
    real_paginate_client.api.list_executions.side_effect = [
        {"message": {"Items": [], "NextToken": "t1"}},
        {"message": {"Items": []}},
    ]

    server.list_executions(status="FAILED", workflow_id="wf1")

    for call in real_paginate_client.api.list_executions.call_args_list:
        assert call.kwargs["params"]["status"] == "FAILED"
        assert call.kwargs["params"]["workflowId"] == "wf1"


# --- list_workflows include_archived --------------------------------------


def test_list_workflows_forwards_include_archived(mock_client):
    """An archived workflow is filtered out server-side, so unarchive_workflow's required id is
    only discoverable through this flag."""
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_workflows(include_archived=True)

    mock_client.paginate.call_args.args[0]({"pageSize": 100})
    assert mock_client.api.list_workflows.call_args.kwargs["include_archived"] is True


def test_list_workflows_excludes_archived_by_default(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_workflows()

    mock_client.paginate.call_args.args[0]({"pageSize": 100})
    assert mock_client.api.list_workflows.call_args.kwargs["include_archived"] is False


# --- get_execution_logs full-mode paging ----------------------------------


def test_get_execution_logs_forwards_the_full_mode_paging_params(mock_client):
    mock_client.unwrap_message.side_effect = lambda page: page
    mock_client.api.get_execution_logs.return_value = {"events": []}

    server.get_execution_logs(
        "e1", mode="full", limit=1000, next_token="tok", filter_pattern="Traceback",
        start_time=1, end_time=2,
    )

    params = mock_client.api.get_execution_logs.call_args.kwargs["params"]
    assert params["limit"] == 1000
    assert params["nextToken"] == "tok"
    assert params["filterPattern"] == "Traceback"
    assert params["startTime"] == 1
    assert params["endTime"] == 2


def test_get_execution_logs_omits_full_mode_params_in_truncated_mode(mock_client):
    """Truncated mode serves stored text and acts on none of these, so they are not sent."""
    mock_client.unwrap_message.side_effect = lambda page: page
    mock_client.api.get_execution_logs.return_value = {}

    server.get_execution_logs("e1", mode="truncated", limit=500, next_token="tok")

    params = mock_client.api.get_execution_logs.call_args.kwargs["params"]
    assert params == {"mode": "truncated"}


def test_get_execution_logs_sends_only_mode_when_nothing_is_narrowed(mock_client):
    mock_client.unwrap_message.side_effect = lambda page: page
    mock_client.api.get_execution_logs.return_value = {}

    server.get_execution_logs("e1")

    assert mock_client.api.get_execution_logs.call_args.kwargs["params"] == {"mode": "full"}


# --- page_execution_detail_metadata ---------------------------------------


def test_page_execution_detail_metadata_defaults_to_the_input_collection(mock_client):
    mock_client.paginate.return_value = {"Items": [{"assetId": "a1"}], "count": 1}
    result = server.page_execution_detail_metadata("e1")

    fetch = mock_client.paginate.call_args.args[0]
    fetch({"pageSize": 100})
    params = mock_client.api.get_execution_details_metadata.call_args.kwargs["params"]
    assert params["collection"] == "input"
    # The collection is echoed back so an agent reading the result knows which half it holds.
    assert result["collection"] == "input"


def test_page_execution_detail_metadata_forwards_collection_and_pipeline_filter(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.page_execution_detail_metadata("e1", collection="output", pipeline_id="p1")

    fetch = mock_client.paginate.call_args.args[0]
    fetch({"pageSize": 100, "startingToken": "tok"})
    call = mock_client.api.get_execution_details_metadata.call_args
    assert call.args[0] == "e1"
    # Every page repeats the filters: the token is only valid alongside the collection and
    # pipelineId it was issued with.
    assert call.kwargs["params"] == {
        "pageSize": 100,
        "startingToken": "tok",
        "collection": "output",
        "pipelineId": "p1",
    }


def test_page_execution_detail_metadata_rejects_an_unknown_collection(mock_client):
    result = server.page_execution_detail_metadata("e1", collection="bogus")
    assert "collection must be one of" in result["error"]
    assert not mock_client.paginate.called


def test_page_execution_detail_metadata_caps_page_size(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.page_execution_detail_metadata("e1")
    assert mock_client.paginate.call_args.kwargs["page_size"] <= 500


def test_page_execution_detail_metadata_reads_the_items_field(mock_client):
    """paginate() defaults items_key to "Items", which is the list field this endpoint returns —
    a mismatch silently yields zero rows."""
    real_paginate = server.VamsClient.paginate
    mock_client.config = server.CONFIG
    mock_client.unwrap_message = server.VamsClient.unwrap_message
    mock_client.api.get_execution_details_metadata.side_effect = [
        {"message": {"Items": [{"assetId": "a1"}], "collection": "input", "NextToken": "t1"}},
        {"message": {"Items": [{"assetId": "a2"}], "collection": "input"}},
    ]
    mock_client.paginate = lambda *args, **kwargs: real_paginate(mock_client, *args, **kwargs)

    result = server.page_execution_detail_metadata("e1")

    assert [row["assetId"] for row in result["Items"]] == ["a1", "a2"]
    # NextToken absent on the second page ends the walk, and nothing is flagged truncated.
    assert result.get("truncated") is None


# --- Docstring contract checks -------------------------------------------
#
# A tool's docstring IS its agent-visible schema: `body`/`updates` params are opaque
# dicts, so a field absent from the docstring is a field no agent ever emits, and a
# response collection left undescribed is one no agent reads. Assert on the text.
#
# The gated tools are not registered at the default settings, so read their source
# docstring rather than the registered tool metadata.

SOURCE_TEXT = SOURCE_PATH.read_text(encoding="utf-8")


def _docstring_of(name):
    """The named function's docstring, collapsed to single-spaced text so an assertion is not
    sensitive to where a sentence happens to wrap."""
    import ast

    for node in ast.walk(ast.parse(SOURCE_TEXT)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return " ".join((ast.get_docstring(node) or "").split())
    raise AssertionError(f"no function named {name!r} in {SOURCE_PATH.name}")


@pytest.mark.parametrize(
    "fragment",
    [
        "metadataSourceAssets",
        "metadataSourceDatabaseId",
        # The constraints an agent cannot infer from the field names. Matched
        # case-insensitively; the docstring capitalizes several for emphasis.
        "rejected",
        "no input files",
        "optional",
        "warnings",
    ],
)
def test_execute_workflow_docstring_describes_the_metadata_source_fields(fragment):
    assert fragment.lower() in _docstring_of("execute_workflow").lower()


@pytest.mark.parametrize(
    "fragment",
    ["inputMetadata", "inputDatabaseMetadata", "metadataSourceDatabases", "truncatedCollections",
     # Where an agent goes when a metadata collection is flagged partial. Without this the
     # truncation flag reads as a dead end and the agent reports the shortened count.
     "page_execution_detail_metadata"],
)
def test_get_execution_details_docstring_describes_the_metadata_collections(fragment):
    assert fragment in _docstring_of("get_execution_details")


@pytest.mark.parametrize(
    "fragment",
    # The collection names an agent must pass verbatim, and the two row shapes it renders.
    ["inputDatabase", "truncatedCollections", "pipelineId", "targetFilePath", "metadataValue"],
)
def test_page_execution_detail_metadata_docstring_describes_collections_and_rows(fragment):
    assert fragment in _docstring_of("page_execution_detail_metadata")


@pytest.mark.parametrize(
    "tool", ["create_pipeline", "create_workflow", "create_pipeline_template"]
)
def test_config_tool_docstrings_list_every_metadata_input_key(tool):
    # A key missing here is a key an agent never sets; the backend rejects unknown ones,
    # so the list must match the model's exactly.
    docstring = _docstring_of(tool)
    for key in ("assetMetadata", "fileMetadata", "fileAttributes", "databaseMetadata"):
        assert key in docstring, f"{tool} docstring omits metadataInputs.{key}"


def test_rerun_execution_docstring_mentions_warnings():
    assert "warnings" in _docstring_of("rerun_execution")


def test_rerun_execution_docstring_says_the_group_id_assigns_rather_than_selects():
    """executionGroupId sets the NEW execution's group membership; exactly one execution is
    launched. A docstring promising a group re-run makes an agent report runs that never started."""
    docstring = _docstring_of("rerun_execution").lower()
    assert "assigns" in docstring
    assert "does not select" in docstring
    # And it must not still promise the inverted behaviour.
    assert "re-run every execution in the group" not in docstring


def test_get_execution_details_docstring_does_not_gate_the_config_location_on_truncation():
    """renderedConfigLocation is emitted whenever the S3 object exists. Describing it as
    truncation-only means an agent diagnoses from the pre-system-tag inline body instead."""
    docstring = _docstring_of("get_execution_details")
    assert "renderedConfigLocation" in docstring
    assert "NOT only on truncation" in docstring
    # The two fields are different stages of the same body, and the docstring must say which is which.
    assert "PRE-system-tag" in docstring
    assert "FULLY" in docstring
    assert "When `renderedConfigTruncated` is true the entry also carries" not in docstring


@pytest.mark.parametrize(
    "fragment",
    # An agent that cannot page cannot reach a real container's error, and reports a wrong
    # conclusion from the first 100 events rather than an incomplete read. Matched
    # case-insensitively; the docstring capitalizes the time unit for emphasis.
    ["limit", "nextToken", "1000", "milliseconds"],
)
def test_get_execution_logs_docstring_describes_full_mode_paging(fragment):
    assert fragment.lower() in _docstring_of("get_execution_logs").lower()


def test_get_execution_logs_docstring_scopes_the_token_to_the_events_list():
    """The token is CloudWatch's and continues `events` only; `sfnHistoryEvents` comes from the Step
    Functions history and is served on a first call. Promising the token carries both makes an agent
    page away from the one section that is always available."""
    docstring = _docstring_of("get_execution_logs")
    assert "sfnHistoryEvents" in docstring
    assert "continues only the `events` list" in docstring


@pytest.mark.parametrize(
    "fragment",
    # A page can withhold rows; the docstring is where an agent learns a short list may be one.
    ["warnings", "WITHHELD", "truncated", "filterStartDate"],
)
def test_list_executions_docstring_describes_withheld_rows(fragment):
    assert fragment in _docstring_of("list_executions")


@pytest.mark.parametrize("tool", ["create_pipeline", "update_pipeline"])
def test_pipeline_save_docstrings_tell_the_agent_to_relay_warnings(tool):
    assert "warnings" in _docstring_of(tool)


def test_list_workflows_docstring_mentions_archived_discovery():
    assert "include_archived" in _docstring_of("list_workflows")


# Every pipeline/workflow/execution APIClient method returns the handler's raw
# {"message": ...} envelope, unlike the asset/database methods. paginate() unwraps
# it for list tools; single-object and write tools must unwrap it themselves, or an
# agent receives a nesting level that no other tool has.


@pytest.mark.parametrize(
    "call",
    [
        lambda: server.get_pipeline("db1", "p1"),
        lambda: server.list_pipeline_templates("db1", "p1"),
        lambda: server.get_pipeline_template("db1", "p1", "t1"),
        lambda: server.get_pipeline_template_tag_schema("db1", "p1", "t1"),
        lambda: server.get_workflow("db1", "w1"),
        lambda: server.list_workflow_triggers("db1", "w1"),
        lambda: server.get_workflow_trigger("db1", "w1", "fileUpload"),
        lambda: server.get_execution_details("e1"),
        lambda: server.get_execution_logs("e1"),
    ],
)
def test_orchestration_reads_unwrap_the_message_envelope(mock_client, call):
    payload = {"pipelineId": "p1", "Items": [{"templateId": "t1"}]}
    # Whichever APIClient method the tool reaches for, return the wrapped shape.
    mock_client.api = MagicMock()
    mock_client.api.mock_add_spec([])  # no spec: any attribute is a child mock
    for attr in (
        "get_pipeline",
        "list_pipeline_templates",
        "get_pipeline_template",
        "get_pipeline_template_tag_schema",
        "get_workflow",
        "list_workflow_triggers",
        "get_workflow_trigger",
        "get_execution_details",
        "get_execution_logs",
    ):
        setattr(mock_client.api, attr, MagicMock(return_value={"message": payload}))
    mock_client.unwrap_message.side_effect = lambda page: (
        page.get("message") if isinstance(page, dict) and isinstance(page.get("message"), dict) else page
    )

    result = call()
    assert result == payload, "tool returned the raw envelope instead of the payload"
    assert mock_client.unwrap_message.called


# Archiving a pipeline or workflow also sets enabled=False, so an unarchive that clears
# only the archived flag restores a row that lists normally but cannot be executed.


def _source_of(name):
    """The named function's source, collapsed to single-spaced text."""
    import ast

    for node in ast.walk(ast.parse(SOURCE_TEXT)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return " ".join(ast.unparse(node).split())
    raise AssertionError(f"no function named {name!r} in {SOURCE_PATH.name}")


@pytest.mark.parametrize("tool", ["unarchive_pipeline", "unarchive_workflow"])
def test_unarchive_re_enables_unless_kept_disabled(tool):
    source = _source_of(tool)
    assert "'archived': False" in source, f"{tool} does not clear the archived flag"
    assert "'enabled'" in source, f"{tool} leaves the row disabled, so it stays unrunnable"
    assert "keep_disabled" in source, f"{tool} offers no way to restore without re-enabling"


@pytest.mark.parametrize(
    "tool",
    [
        "archive_pipeline",
        "unarchive_pipeline",
        "archive_workflow",
        "unarchive_workflow",
        "delete_pipeline_template",
        "delete_workflow_trigger",
        "permanent_delete_execution",
    ],
)
def test_orchestration_destructive_tools_unwrap_the_message_envelope(tool):
    assert "unwrap_message" in _source_of(tool), (
        f"{tool} returns the raw {{'message': ...}} envelope, a nesting level no other tool has"
    )


# A pipeline save reports its non-blocking warnings as a SIBLING of `message`, and the response model
# has no warnings field — so plain unwrapping discards the only copy and the agent reports a clean
# success for a pipeline that saved into a silently broken state.


@pytest.fixture
def sibling_warning_client(mock_client):
    mock_client.unwrap_message = server.VamsClient.unwrap_message
    return mock_client


def test_unwrap_message_with_warnings_keeps_the_sibling_array(sibling_warning_client):
    payload = server._unwrap_message_with_warnings(
        {"message": {"pipelineId": "p1"}, "warnings": ["no default template chosen"]}
    )
    assert payload == {"pipelineId": "p1", "warnings": ["no default template chosen"]}
    assert "message" not in payload, "the envelope must still be unwrapped exactly once"


def test_unwrap_message_with_warnings_matches_plain_unwrap_when_clean(sibling_warning_client):
    page = {"message": {"pipelineId": "p1"}}
    assert server._unwrap_message_with_warnings(page) == server.VamsClient.unwrap_message(page)


def test_unwrap_message_with_warnings_passes_unenveloped_payloads_through(sibling_warning_client):
    """Asset/database APIClient methods return already-unwrapped data. Re-unwrapping or copying one
    would change the shape those tools return."""
    asset = {"assetId": "a1", "warnings": ["ignored"]}
    assert server._unwrap_message_with_warnings(asset) is asset


def test_unwrap_message_with_warnings_handles_a_string_message(sibling_warning_client):
    """An abort answers {"message": "Execution aborted"} — a string is not an envelope, so the whole
    dict (warnings included) passes through."""
    abort = {"message": "Execution aborted", "warnings": ["sub-process left running"]}
    assert server._unwrap_message_with_warnings(abort) == abort


@pytest.mark.parametrize("tool", ["create_pipeline", "update_pipeline", "unarchive_pipeline"])
def test_pipeline_saves_preserve_sibling_warnings(tool):
    source = _source_of(tool)
    assert "_unwrap_message_with_warnings" in source, (
        f"{tool} drops the sibling `warnings` array, so a broken save reports a clean success"
    )


@pytest.mark.parametrize(
    "tool",
    # These either nest warnings inside the response model or answer with a string `message`, so
    # plain unwrapping loses nothing.
    ["create_workflow", "update_workflow", "execute_workflow", "rerun_execution", "abort_execution"],
)
def test_non_pipeline_saves_stay_on_plain_unwrap(tool):
    source = _source_of(tool)
    assert "_unwrap_message_with_warnings" not in source
    assert "unwrap_message" in source
