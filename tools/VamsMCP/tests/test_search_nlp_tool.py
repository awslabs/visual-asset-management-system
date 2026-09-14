"""Source-layout, registration and documentation contract of the `search_nlp` read tool.

Tools are module-level defs, so a def in the wrong gate block or after the entrypoint is silently
absent (MCP CLAUDE.md Rule 4); the docstring is the only place an agent learns the size ceiling and
that a `gte` total is a lower bound (Rule 8).
"""

import re
from pathlib import Path

import pytest

from vams_mcp import server

SERVER_PATH = Path(server.__file__)
SOURCE_TEXT = SERVER_PATH.read_text(encoding="utf-8")
README_TEXT = (SERVER_PATH.parents[1] / "README.md").read_text(encoding="utf-8")
CLAUDE_TEXT = (SERVER_PATH.parents[1] / "CLAUDE.md").read_text(encoding="utf-8")
REPO_ROOT = SERVER_PATH.parents[3]

WARNING_CODES = ("truncated:window", "truncated:targets", "databases:none_accessible",
                 "opensearch:fields_ignored", "opensearch:enrichment_failed", "segments:window_full")
STORED_TRIGGER_VALUES = ("Manual", "File-Upload", "System-Reindex")
# The write/destructive pipeline and workflow tools whose docstrings say system records are
# read-only. They are defined INSIDE the `if CONFIG.enable_writes:` / `if CONFIG.enable_destructive:`
# blocks, hence indented. Confirm the names with
#   grep -n '^    def \(archive\|update\|delete\|unarchive\|set\)_\(pipeline\|workflow\)' vams_mcp/server.py
# and report a mismatch rather than trimming the list.
SYSTEM_RECORD_WRITE_TOOLS = (
    "update_pipeline", "archive_pipeline", "unarchive_pipeline",
    "create_pipeline_template", "update_pipeline_template", "delete_pipeline_template",
    "set_pipeline_template_tag_schema",
    "update_workflow", "archive_workflow", "unarchive_workflow",
    "set_workflow_trigger", "delete_workflow_trigger",
)


def _line_of(text: str) -> int:
    for number, line in enumerate(SOURCE_TEXT.splitlines(), start=1):
        if line == text:
            return number
    raise AssertionError(f"line not found in server.py: {text!r}")


def _def_line_of(name: str) -> int:
    match = re.search(rf"^[ \t]*def {re.escape(name)}\(", SOURCE_TEXT, re.M)
    assert match, f"no def {name}( in server.py"
    return SOURCE_TEXT.count("\n", 0, match.start()) + 1


def _docstring_of(name: str) -> str:
    match = re.search(
        rf'^[ \t]*def {re.escape(name)}\(.*?\)\s*->[^:]*:\s*"""(.*?)"""', SOURCE_TEXT, re.S | re.M
    )
    assert match, f"def {name}( has no docstring"
    return match.group(1)


def _function_source(name: str) -> str:
    """The body of a module-level def, up to the next module-level statement (a decorator, def or
    assignment — the signature's own closing paren also sits in column 0 and must not end it)."""
    match = re.search(rf"^def {re.escape(name)}\(.*?(?=^(?:@|def |\w))", SOURCE_TEXT, re.S | re.M)
    assert match, f"no def {name}( in server.py"
    return match.group(0)


@pytest.mark.asyncio
async def test_search_nlp_is_registered_with_both_gates_off():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert "search_nlp" in tools
    schema = getattr(tools["search_nlp"], "input_schema", None) or getattr(tools["search_nlp"], "inputSchema")
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "database_ids", "entity_type", "size",
                                         "include_archived", "file_classes", "file_extensions",
                                         "metadata_query", "geo_search", "tags", "include_segments"}


def test_search_nlp_forwards_every_field_of_the_route_model():
    """MCP CLAUDE.md Rule 9: the route's narrowing fields, read from the backend model, must all be
    reachable — `enrich` is excluded because it is not a narrowing field and the tool always wants
    the enriched `_source`; `filters` because raw OpenSearch clauses are not a tool-level knob."""
    model_source = (REPO_ROOT / "backend/backend/models/vectorsearch.py").read_text(encoding="utf-8")
    model = re.search(r"class NlpSearchRequestModel\(BaseModel\):\n(.*?)\n\n    class Config",
                      model_source, re.S)
    assert model, "NlpSearchRequestModel not found in the backend model module"
    route_fields = set(re.findall(r"^    (\w+): ", model.group(1), re.M))
    assert route_fields >= {"query", "entityTypes", "databaseIds", "includeArchived", "includeSegments",
                            "fileClasses", "fileExtensions", "size", "metadataQuery",
                            "metadataSearchMode", "geoSearch", "tags"}, route_fields
    not_tool_knobs = {"enrich", "filters", "metadataSearchMode"}  # mode is fixed to "both" beside metadataQuery
    forwarded = set(re.findall(r'request\["(\w+)"\] = ', _function_source("_build_nlp_search_request")))
    forwarded |= {"query", "entityTypes", "size", "includeArchived"}  # the literal dict
    assert route_fields - not_tool_knobs <= forwarded, route_fields - not_tool_knobs - forwarded


def test_search_nlp_file_class_ids_match_the_backend_classifier():
    """The tool validates `file_classes` against a copy of the backend's FILE_CLASSES (the route
    itself does not validate them); the copy must not drift from the source."""
    backend_source = (REPO_ROOT / "backend/backend/common/vectorsearch/fileClassIntent.py").read_text(
        encoding="utf-8")
    phrases = re.search(r"FILE_CLASS_PHRASES: Dict\[str, str\] = \{(.*?)\n\}", backend_source, re.S)
    assert phrases, "FILE_CLASS_PHRASES not found in the backend classifier module"
    backend_ids = re.findall(r'^\s+"([a-z0-9]+)":', phrases.group(1), re.M)
    assert len(backend_ids) == 14
    assert list(server._NLP_FILE_CLASSES) == backend_ids
    doc = " ".join(_docstring_of("search_nlp").split())  # the list wraps across docstring lines
    assert ", ".join(backend_ids) in doc, "the docstring must list the exact ids an agent may pass"
    assert "3d-model" not in doc


def test_search_nlp_sits_in_the_read_section_beside_the_keyword_search_tools():
    assert _def_line_of("search_files") < _def_line_of("search_nlp") < _def_line_of("get_search_fields")
    assert _def_line_of("search_nlp") < _line_of("if CONFIG.enable_writes:")
    assert SOURCE_TEXT.count("\ndef search_nlp(") == 1


def test_search_nlp_docstring_states_the_bounds_and_the_closed_warning_set():
    doc = _docstring_of("search_nlp")
    assert "1..100" in doc and "1..1000" in doc and "100 ids" in doc
    assert "gte" in doc and "lower bound" in doc.lower()
    for code in WARNING_CODES:
        assert code in doc, code
    assert "no offset paging" in doc.lower() or "no from_offset" in doc.lower()
    assert "Bedrock" in doc


def test_readme_lists_search_nlp_and_keeps_it_out_of_autoapprove():
    assert "`search_nlp`" in README_TEXT
    auto = re.search(r'"autoApprove":\s*\[(.*?)\]', README_TEXT, re.S)
    assert auto and "search_nlp" not in auto.group(1)
    assert "search_nlp" in CLAUDE_TEXT, "Rule 4's three-place list must name the tool"


@pytest.mark.parametrize("name", ["list_executions", "list_workflow_executions"])
def test_the_execution_listings_name_the_stored_trigger_values(name):
    doc = _docstring_of(name)
    for value in STORED_TRIGGER_VALUES:
        assert value in doc, f"{name}: {value}"


@pytest.mark.parametrize("name", SYSTEM_RECORD_WRITE_TOOLS)
def test_the_write_tools_say_system_records_are_read_only(name):
    doc = _docstring_of(name).lower()
    assert "system" in doc and "read-only" in doc, name
    if name != "set_workflow_trigger":
        assert 'set_workflow_trigger' in doc, f"{name} must point at the enabled toggle"
