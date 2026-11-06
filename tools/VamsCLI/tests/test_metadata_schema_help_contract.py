"""Contract test for the value-type names in `metadata-schema` help text.

A command's help text is the authority a schema author trusts: it is where they read which
`metadataFieldValueType` values the API accepts. The API accepts exactly the members of
`MetadataValueType` (`backend/backend/models/metadata.py`), which are snake_case, so a help
text naming a camelCase spelling sends the author to a value the create request is refused
for -- and nothing else catches it. The CLI does not validate the value type (it passes the
field definitions through, and the API rejects them), the docs are a separate file, and the
help text is a docstring, so it is invisible to every import, lint and type check.

This is a durable guardrail rather than a temporary test (root CLAUDE.md Rule 13): the
docstring stays writable, so a future edit can reintroduce a camelCase name the same way.

Follows the source-parsing approach of `test_constants_contract.py` -- the docstring is read
out of the command module with `ast` rather than by importing the CLI, so the check holds for
the text as written in the file.
"""

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_METADATA_MODEL = REPO_ROOT / "backend/backend/models/metadata.py"
CLI_METADATA_SCHEMA = REPO_ROOT / "tools/VamsCLI/vamscli/commands/metadata_schema.py"


def _metadata_value_types() -> set:
    """The string values of `MetadataValueType` in the backend model."""
    tree = ast.parse(BACKEND_METADATA_MODEL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MetadataValueType":
            values = {
                stmt.value.value
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            }
            assert values, "MetadataValueType has no string members"
            return values
    raise AssertionError("MetadataValueType not found in the backend metadata model")


def _command_docstrings() -> dict:
    """Every function docstring in the metadata_schema command module, by function name."""
    tree = ast.parse(CLI_METADATA_SCHEMA.read_text(encoding="utf-8"))
    return {
        node.name: ast.get_docstring(node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _camel_case(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(part.capitalize() for part in rest)


class TestMetadataSchemaHelpValueTypes:
    def test_no_docstring_names_a_camelcase_value_type(self):
        """No help text spells a value type in camelCase.

        `inline_controlled_list` is the member this regressed on, but the check covers every
        member so the next multi-word type added cannot regress the same way.
        """
        value_types = _metadata_value_types()
        multiword = {v for v in value_types if "_" in v}
        assert multiword, "expected at least one multi-word value type to guard"

        offenders = []
        for func_name, doc in _command_docstrings().items():
            for snake in multiword:
                camel = _camel_case(snake)
                if re.search(rf"\b{re.escape(camel)}\b", doc):
                    offenders.append(f"{func_name}() names '{camel}'; the API accepts '{snake}'")

        assert not offenders, "camelCase value type in help text:\n  " + "\n  ".join(offenders)

    def test_create_help_names_the_accepted_inline_controlled_list_value(self):
        """Positive control: the create help still names the real value.

        Without this, the check above would pass vacuously if the sentence were deleted
        instead of corrected.
        """
        doc = _command_docstrings().get("create", "")
        assert doc, "metadata_schema create() has no docstring"
        assert "inline_controlled_list" in doc, (
            "create help no longer names inline_controlled_list, so the rule about "
            "controlledListKeys has lost the type it applies to"
        )

    def test_camel_case_helper_is_a_faithful_inverse(self):
        """Control for the helper itself, so a broken transform cannot hide an offender."""
        assert _camel_case("inline_controlled_list") == "inlineControlledList"
        assert _camel_case("multiline_string") == "multilineString"
        assert _camel_case("string") == "string"
