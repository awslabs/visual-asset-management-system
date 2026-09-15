"""The compliance tools.

Two response shapes, neither of which the older helpers fit: the schema / quarantine / cascade /
audit listings return their WHOLE list in one response under a route-specific field with no
``message`` envelope and no continuation token (``paginate()`` would find zero rows and
``unwrap_message`` has nothing to unwrap), while the evaluation history is a real
``maxItems`` / ``startingToken`` / ``NextToken`` page that reads its page size from ``maxItems``
rather than ``pageSize``. Each is asserted against a literal payload, not a MagicMock's default.

The write and destructive BODIES run through the reload fixture from ``test_gated_tools.py`` (the
gates are decided at import); the payload key names are asserted because they are what the backend
Pydantic models validate, and a rename there surfaces only as a 400 against a live deployment.
"""

import importlib
import inspect
import os
from unittest.mock import MagicMock

import pytest

from vams_mcp import server
from vams_mcp import server as server_module


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(server, "CLIENT", client)
    return client


@pytest.fixture
def real_paginate_client(mock_client):
    """A mock client whose paginate/unwrap_message are the REAL implementations."""
    mock_client.config = server.CONFIG
    mock_client.unwrap_message = server.VamsClient.unwrap_message
    mock_client.paginate = lambda *args, **kwargs: server.VamsClient.paginate(
        mock_client, *args, **kwargs
    )
    return mock_client


def _docstring_of(name):
    """The named function's docstring, from the source, collapsed to single-spaced text.

    Read from the file because the gated tools are not registered at the default settings.
    """
    import ast
    from pathlib import Path

    source = Path(server.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return " ".join((ast.get_docstring(node) or "").split())
    raise AssertionError(f"no function named {name!r} in server.py")


SCHEMA = {
    "schemaName": "cad-quality",
    "databaseId": "GLOBAL",
    "description": "",
    "schemaBody": {"schemaFormat": "vams-rules-v1", "rules": {}},
    "version": 1,
    "createdAt": "2026-09-01T00:00:00+00:00",
}


# --- Single-response listings ---------------------------------------------------


def test_list_compliance_schemas_lifts_the_schemas_field_onto_items(mock_client):
    mock_client.api.list_compliance_schemas.return_value = {"schemas": [SCHEMA]}
    result = server.list_compliance_schemas()
    assert result["Items"] == [SCHEMA]
    assert result["count"] == 1
    assert "truncated" not in result
    mock_client.api.list_compliance_schemas.assert_called_once_with(database_id=None)


def test_list_compliance_schemas_forwards_the_database_filter(mock_client):
    mock_client.api.list_compliance_schemas.return_value = {"schemas": []}
    result = server.list_compliance_schemas(database_id="db1")
    assert result == {"Items": [], "count": 0}
    mock_client.api.list_compliance_schemas.assert_called_once_with(database_id="db1")


def test_list_quarantined_assets_lifts_its_field(mock_client):
    rows = [{"databaseId": "db1", "assetId": "a1", "complianceState": "quarantined"}]
    mock_client.api.list_quarantined_assets.return_value = {"quarantinedAssets": rows}
    result = server.list_quarantined_assets()
    assert result["Items"] == rows
    assert result["count"] == 1


def test_list_compliance_cascades_lifts_its_field(mock_client):
    rows = [{"cascadeId": "c1", "state": "pending_approval"}]
    mock_client.api.list_compliance_cascades.return_value = {"cascades": rows}
    result = server.list_compliance_cascades()
    assert result["Items"] == rows
    assert result["count"] == 1


def test_single_response_list_keeps_the_other_top_level_fields():
    """The helper carries a page's non-list fields through, so nothing a route reports is lost."""
    result = server._single_response_list(
        {"entries": [{"entryId": "e1"}], "databaseId": "db1"}, "entries", "audit entry"
    )
    assert result == {"databaseId": "db1", "Items": [{"entryId": "e1"}], "count": 1}


def test_single_response_list_tolerates_a_missing_or_non_list_field():
    assert server._single_response_list({}, "entries", "audit entry") == {"Items": [], "count": 0}
    assert server._single_response_list({"entries": None}, "entries", "audit entry")["Items"] == []


# --- The audit trail is bounded with no token -----------------------------------


def test_query_compliance_audit_forwards_every_filter(mock_client):
    mock_client.api.query_compliance_audit.return_value = {"entries": []}
    server.query_compliance_audit(
        event_type="compliance_check", start_date="2026-09-01", end_date="2026-09-30", limit=10
    )
    mock_client.api.query_compliance_audit.assert_called_once_with(
        event_type="compliance_check", start_date="2026-09-01", end_date="2026-09-30", limit=10
    )


def test_query_compliance_audit_flags_a_result_that_reached_the_limit(mock_client):
    mock_client.api.query_compliance_audit.return_value = {
        "entries": [{"entryId": f"e{i}"} for i in range(3)]
    }
    result = server.query_compliance_audit(limit=3)
    assert result["count"] == 3
    assert result["truncated"] is True
    assert "no continuation token" in result["note"]


def test_query_compliance_audit_flags_the_handler_default_when_no_limit_was_given(mock_client):
    """The bound is knowable without a token: the handler applies its own default when the caller
    narrowed nothing, so a result of exactly that many rows is still incomplete."""
    mock_client.api.query_compliance_audit.return_value = {
        "entries": [{"entryId": f"e{i}"} for i in range(server.COMPLIANCE_AUDIT_DEFAULT_LIMIT)]
    }
    result = server.query_compliance_audit()
    assert result["truncated"] is True
    assert str(server.COMPLIANCE_AUDIT_DEFAULT_LIMIT) in result["note"]


def test_query_compliance_audit_does_not_flag_a_short_result(mock_client):
    mock_client.api.query_compliance_audit.return_value = {"entries": [{"entryId": "e1"}]}
    result = server.query_compliance_audit(limit=3)
    assert "truncated" not in result
    assert "note" not in result


def test_get_asset_compliance_audit_forwards_the_window_and_flags_the_bound(mock_client):
    mock_client.api.get_asset_compliance_audit.return_value = {
        "entries": [{"entryId": "e1"}, {"entryId": "e2"}]
    }
    result = server.get_asset_compliance_audit("db1", "a1", start_date="2026-09-01", limit=2)
    mock_client.api.get_asset_compliance_audit.assert_called_once_with(
        "db1", "a1", start_date="2026-09-01", end_date=None, limit=2
    )
    assert result["truncated"] is True


def test_get_asset_compliance_audit_exposes_no_event_type_parameter():
    """The per-asset route ignores eventType, so the tool must not offer it (Mandatory Rule 9)."""
    parameters = inspect.signature(server.get_asset_compliance_audit).parameters
    assert "event_type" not in parameters
    assert "event-type" in _docstring_of("get_asset_compliance_audit").lower().replace("_", "-")


@pytest.mark.parametrize("tool", ["query_compliance_audit", "get_asset_compliance_audit"])
def test_audit_tools_take_no_starting_token(tool):
    """A token the route never issues would be a parameter the agent can never fill."""
    assert "starting_token" not in inspect.signature(getattr(server, tool)).parameters


@pytest.mark.parametrize("tool", ["query_compliance_audit", "get_asset_compliance_audit"])
def test_audit_docstrings_say_the_route_is_bounded_without_a_token(tool):
    docstring = _docstring_of(tool)
    assert "truncated" in docstring
    assert "continuation token" in docstring


@pytest.mark.parametrize(
    "tool", ["list_compliance_schemas", "list_quarantined_assets", "list_compliance_cascades"]
)
def test_whole_list_tools_take_no_paging_parameters(tool):
    """These routes return everything in one response; a paging knob would silently do nothing."""
    parameters = inspect.signature(getattr(server, tool)).parameters
    assert "starting_token" not in parameters
    assert "max_items" not in parameters


# --- Evaluation history is a real page keyed on maxItems -------------------------


def test_list_compliance_evaluations_reads_the_evaluations_field(real_paginate_client):
    real_paginate_client.api.list_compliance_evaluations.return_value = {
        "evaluations": [{"evaluationId": "ev-1", "executionId": "exec-1"}]
    }
    result = server.list_compliance_evaluations("db1", "a1")
    assert result["Items"] == [{"evaluationId": "ev-1", "executionId": "exec-1"}]
    assert result["count"] == 1
    assert "truncated" not in result


def test_list_compliance_evaluations_sends_the_page_size_as_max_items(real_paginate_client):
    """The route reads its page size from `maxItems`, so paginate()'s pageSize is renamed."""
    real_paginate_client.api.list_compliance_evaluations.return_value = {"evaluations": []}
    server.list_compliance_evaluations("db1", "a1", starting_token="resume-here")
    kwargs = real_paginate_client.api.list_compliance_evaluations.call_args.kwargs
    assert kwargs["max_items"] == min(
        server.CONFIG.page_size, server.MAX_COMPLIANCE_EVALUATIONS_PAGE_SIZE
    )
    assert kwargs["starting_token"] == "resume-here"


def test_list_compliance_evaluations_never_asks_for_more_than_the_route_cap(mock_client):
    mock_client.config = MagicMock(page_size=10_000, max_pages=1)
    mock_client.unwrap_message = server.VamsClient.unwrap_message
    mock_client.paginate = lambda *args, **kwargs: server.VamsClient.paginate(
        mock_client, *args, **kwargs
    )
    mock_client.api.list_compliance_evaluations.return_value = {"evaluations": []}
    server.list_compliance_evaluations("db1", "a1")
    assert (
        mock_client.api.list_compliance_evaluations.call_args.kwargs["max_items"]
        == server.MAX_COMPLIANCE_EVALUATIONS_PAGE_SIZE
    )


def test_list_compliance_evaluations_follows_next_token(real_paginate_client):
    real_paginate_client.api.list_compliance_evaluations.side_effect = [
        {"evaluations": [{"evaluationId": "ev-2"}], "NextToken": "page-2"},
        {"evaluations": [{"evaluationId": "ev-1"}]},
    ]
    result = server.list_compliance_evaluations("db1", "a1")
    assert [row["evaluationId"] for row in result["Items"]] == ["ev-2", "ev-1"]
    assert result["pages"] == 2
    assert "NextToken" not in result


# --- Single-object reads -------------------------------------------------------


def test_get_compliance_schema_calls_the_api(mock_client):
    mock_client.api.get_compliance_schema.return_value = SCHEMA
    assert server.get_compliance_schema("cad-quality") == SCHEMA
    mock_client.api.get_compliance_schema.assert_called_once_with("cad-quality")


def test_state_tools_route_to_the_asset_and_database_readers(mock_client):
    mock_client.api.get_compliance_state.return_value = {"complianceState": "unknown"}
    mock_client.api.get_database_compliance_state.return_value = {"totalAssets": 0}
    assert server.get_asset_compliance_state("db1", "a1") == {"complianceState": "unknown"}
    assert server.get_database_compliance_overview("db1") == {"totalAssets": 0}
    mock_client.api.get_compliance_state.assert_called_once_with("db1", "a1")
    mock_client.api.get_database_compliance_state.assert_called_once_with("db1")


def test_bindings_and_cascade_reads_call_the_api(mock_client):
    mock_client.api.get_compliance_bindings.return_value = {"databaseSchema": None}
    mock_client.api.get_compliance_cascade.return_value = {"state": "executing"}
    assert server.get_compliance_bindings("db1") == {"databaseSchema": None}
    assert server.get_compliance_cascade("c1") == {"state": "executing"}


def test_compliance_read_tool_errors_are_returned_as_data(mock_client):
    mock_client.api.get_compliance_schema.side_effect = RuntimeError("boom")
    result = server.get_compliance_schema("gone")
    assert result == {"error": "boom", "error_type": "RuntimeError"}


@pytest.mark.asyncio
async def test_compliance_read_tools_are_registered_at_the_default_gates():
    names = {tool.name for tool in await server.mcp.list_tools()}
    for expected in (
        "list_compliance_schemas",
        "get_compliance_schema",
        "get_compliance_bindings",
        "get_asset_compliance_state",
        "get_database_compliance_overview",
        "list_compliance_evaluations",
        "list_quarantined_assets",
        "list_compliance_cascades",
        "get_compliance_cascade",
        "query_compliance_audit",
        "get_asset_compliance_audit",
    ):
        assert expected in names


@pytest.mark.asyncio
async def test_compliance_mutating_tools_are_gated_off_by_default():
    names = {tool.name for tool in await server.mcp.list_tools()}
    for gated in (
        "create_compliance_schema",
        "bind_compliance_schema",
        "evaluate_asset_compliance",
        "sweep_compliance_schema",
        "release_quarantine",
        "approve_compliance_cascade",
        "delete_compliance_schema",
    ):
        assert gated not in names


# --- Write and destructive bodies (module reload with both gates on) ----------------

GATE_VARS = ("VAMS_ENABLE_WRITES", "VAMS_ENABLE_DESTRUCTIVE")


@pytest.fixture
def gated(monkeypatch):
    """Reimport the server with both gates on and a mocked client; restore the default module after."""
    for name in GATE_VARS:
        os.environ[name] = "true"
    importlib.reload(server_module)
    try:
        assert server_module.CONFIG.enable_writes is True
        assert server_module.CONFIG.enable_destructive is True
        client = MagicMock()
        client.unwrap_message = server_module.VamsClient.unwrap_message
        client.config = server_module.CONFIG
        monkeypatch.setattr(server_module, "CLIENT", client)
        yield server_module, client
    finally:
        for name in GATE_VARS:
            os.environ.pop(name, None)
        importlib.reload(server_module)


def test_create_compliance_schema_payload_keys(gated):
    srv, client = gated
    body = {"schemaFormat": "vams-rules-v1", "rules": {}}
    srv.create_compliance_schema("cad-quality", body, database_id="db1", description="CAD checks")
    client.api.create_compliance_schema.assert_called_once_with({
        "schemaName": "cad-quality",
        "schemaBody": body,
        "databaseId": "db1",
        "description": "CAD checks",
    })


def test_create_compliance_schema_omits_scope_and_description_when_absent(gated):
    """An absent scope must stay absent so the handler's GLOBAL default applies."""
    srv, client = gated
    srv.create_compliance_schema("cad-quality", {"rules": {}})
    assert client.api.create_compliance_schema.call_args.args[0] == {
        "schemaName": "cad-quality", "schemaBody": {"rules": {}}}


def test_update_compliance_schema_sends_only_the_arguments_given(gated):
    srv, client = gated
    srv.update_compliance_schema("cad-quality", description="Tightened")
    client.api.update_compliance_schema.assert_called_once_with(
        "cad-quality", {"description": "Tightened"})


def test_update_compliance_schema_refuses_an_empty_update(gated):
    srv, client = gated
    result = srv.update_compliance_schema("cad-quality")
    assert result["error_type"] == "ValueError"
    client.api.update_compliance_schema.assert_not_called()


def test_bind_compliance_schema_database_forwards_auto_eval(gated):
    srv, client = gated
    srv.bind_compliance_schema("db1", "cad-quality", auto_eval=False)
    client.api.bind_compliance_schema.assert_called_once_with(
        "db1", "cad-quality", asset_id=None, auto_eval=False)


def test_bind_compliance_schema_asset_drops_auto_eval(gated):
    """The asset route never reads complianceAutoEval; sending it would look honoured and be ignored."""
    srv, client = gated
    srv.bind_compliance_schema("db1", "cad-quality", asset_id="a1", auto_eval=False)
    client.api.bind_compliance_schema.assert_called_once_with(
        "db1", "cad-quality", asset_id="a1", auto_eval=None)


def test_unbind_compliance_schema_routes_database_and_asset(gated):
    srv, client = gated
    srv.unbind_compliance_schema("db1")
    srv.unbind_compliance_schema("db1", asset_id="a1")
    assert [c.kwargs["asset_id"] for c in client.api.unbind_compliance_schema.call_args_list] == [None, "a1"]


def test_evaluate_and_sweep_forward_their_arguments(gated):
    srv, client = gated
    srv.evaluate_asset_compliance("db1", "a1", schema_name="cad-quality")
    srv.sweep_compliance_schema("cad-quality")
    client.api.evaluate_asset_compliance.assert_called_once_with("db1", "a1", schema_name="cad-quality")
    client.api.sweep_compliance_schema.assert_called_once_with("cad-quality")


def test_quarantine_tools_forward_the_reason(gated):
    srv, client = gated
    srv.release_quarantine("db1", "a1")
    srv.release_quarantine("db1", "a1", reason="fixed")
    srv.grant_quarantine_exception("db1", "a1", "waived")
    assert [c.kwargs["reason"] for c in client.api.release_quarantine.call_args_list] == [None, "fixed"]
    client.api.grant_quarantine_exception.assert_called_once_with("db1", "a1", "waived")


def test_create_compliance_cascade_defaults_to_requiring_approval(gated):
    srv, client = gated
    srv.create_compliance_cascade("db1", "a1")
    client.api.create_compliance_cascade.assert_called_once_with(
        "db1", "a1", reason=None, require_approval=True)


def test_create_compliance_cascade_can_run_at_once(gated):
    srv, client = gated
    srv.create_compliance_cascade("db1", "a1", reason="revised", require_approval=False)
    client.api.create_compliance_cascade.assert_called_once_with(
        "db1", "a1", reason="revised", require_approval=False)


def test_cascade_decisions_forward_the_reason(gated):
    srv, client = gated
    srv.approve_compliance_cascade("c1", reason="ok")
    srv.reject_compliance_cascade("c1")
    client.api.approve_compliance_cascade.assert_called_once_with("c1", reason="ok")
    client.api.reject_compliance_cascade.assert_called_once_with("c1", reason=None)


def test_delete_compliance_schema_calls_the_api(gated):
    srv, client = gated
    srv.delete_compliance_schema("cad-quality")
    client.api.delete_compliance_schema.assert_called_once_with("cad-quality")


# --- Docstring contracts -------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    ["evaluate_asset_compliance", "sweep_compliance_schema", "create_compliance_cascade",
     "approve_compliance_cascade"],
)
def test_compute_starting_compliance_tools_warn_about_auto_approve(tool):
    """A pipeline rule runs a workflow execution per evaluated asset; the docstring is the only place
    an agent learns that from the tool itself."""
    docstring = _docstring_of(tool)
    assert "compute" in docstring
    assert "autoApprove" in docstring


def test_unbind_docstring_names_the_record_removal():
    docstring = _docstring_of("unbind_compliance_schema")
    assert "removedComplianceRecords" in docstring
    assert "audit trail" in docstring


def test_delete_compliance_schema_docstring_names_the_bound_refusal_and_irreversibility():
    docstring = _docstring_of("delete_compliance_schema")
    assert "Irreversible" in docstring
    assert "unbind_compliance_schema" in docstring


def test_get_asset_compliance_state_docstring_explains_unknown():
    docstring = _docstring_of("get_asset_compliance_state")
    assert "unknown" in docstring
    assert "not \"the asset does not exist\"" in docstring


def test_readme_lists_every_compliance_read_tool():
    from pathlib import Path

    import vams_mcp

    readme = (Path(vams_mcp.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for tool in (
        "list_compliance_schemas", "get_compliance_schema", "get_compliance_bindings",
        "get_asset_compliance_state", "get_database_compliance_overview",
        "list_compliance_evaluations", "list_quarantined_assets", "list_compliance_cascades",
        "get_compliance_cascade", "query_compliance_audit", "get_asset_compliance_audit",
    ):
        assert f"`{tool}`" in readme, f"{tool} is missing from the README tool list"
