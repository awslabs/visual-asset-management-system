"""Contract tests for `vamscli/utils/system_records.py`.

The CLI recognises the API's system-record refusals by their exact text so that a command can show the
API's message without a generic hint after it. The text is the backend's
(`backend/backend/common/workflows/systemRecords.py`), so these tests read every refusal message the
backend module defines and require each to be recognised: a reworded backend message fails here
instead of silently bringing the misleading hint back.
"""

import ast
from pathlib import Path

import pytest

from vamscli.utils.system_records import is_system_record_refusal

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_SYSTEM_RECORDS = REPO_ROOT / "backend/backend/common/workflows/systemRecords.py"

SAMPLE_FIELD = "description"


def _render(node: ast.AST) -> str:
    """The text of a string constant or f-string, with `{field}` / `{field!r}` given SAMPLE_FIELD."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    assert isinstance(node, ast.JoinedStr), ast.dump(node)
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(value.value)
        else:
            assert isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name)
            assert value.value.id == "field", ast.dump(value)
            parts.append(SAMPLE_FIELD)
    return "".join(parts)


def _backend_refusal_messages() -> set:
    """Every refusal message systemRecords.py can return: its *_MESSAGE constants (a `{field}`
    template filled in) and the f-string each *_message(field) function returns."""
    tree = ast.parse(BACKEND_SYSTEM_RECORDS.read_text(encoding="utf-8"))
    messages = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.endswith("_MESSAGE"):
                messages.add(_render(node.value).format(field=SAMPLE_FIELD))
        elif isinstance(node, ast.FunctionDef) and node.name.endswith("_message"):
            returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
            assert len(returns) == 1, node.name
            messages.add(_render(returns[0].value))
    return messages


def test_the_backend_module_defines_the_refusals_the_cli_mirrors():
    # Five fixed messages (the fifth refuses a compliance pipeline rule on a system record), two
    # field-naming update templates and two field-naming functions. A count that moves means the
    # backend gained or lost a refusal: mirror it in system_records.py.
    assert len(_backend_refusal_messages()) == 9


@pytest.mark.parametrize("message", sorted(_backend_refusal_messages()))
def test_every_backend_refusal_is_recognised(message):
    assert is_system_record_refusal(message), message


def test_an_exception_carrying_the_message_is_recognised():
    from vamscli.utils.exceptions import InvalidPipelineTemplateDataError

    error = InvalidPipelineTemplateDataError("Templates of system pipelines cannot be added or deleted.")
    assert is_system_record_refusal(error)


@pytest.mark.parametrize("message", [
    # Other 400s from the same commands keep their hint.
    "A pipeline with this ID already exists.",
    "Pipeline ID is already in use by another database. Choose a different ID.",
    "Unsupported trigger type 'scheduled'.",
    "Invalid tag schema: field 'q' has no type.",
    # Free text that mentions system records is not a refusal.
    "System pipelines are great.",
    "Templates of system pipelines",
    # A refusal quoted inside other text, or with a field that is not one bare name.
    "Failed to create template: Templates of system pipelines cannot be added or deleted.",
    "Templates of system pipelines cannot be added or deleted. Also check the tag schema.",
    'System pipelines are read-only except "enabled"; "" cannot be changed.',
    'System pipelines are read-only except "enabled"; "a b" cannot be changed.',
    'System workflows are read-only except "enabled"; "x"; "y" cannot be changed.',
])
def test_other_messages_are_not_recognised(message):
    assert not is_system_record_refusal(message)
