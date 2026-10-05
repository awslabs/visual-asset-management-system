# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The metadata-schema request models accept a `fileKeyTypeRestriction` entry only in a form a file
can match.

`get_aggregated_schemas` (common/metadataSchemaValidation.py) applies a restricted fileMetadata or
fileAttribute schema to a file when one stored entry, stripped and lowercased, equals the file's
extension as `extract_file_extension` reads it: the text after the LAST dot of the file's name, with
that dot (`part.CATProduct` -> `.catproduct`). An entry that can equal no such value -- one with no
leading dot, a bare dot, a second dot or a path separator -- would leave the schema applying to no
file, with nothing to report it. Both request models therefore refuse those entries, and this file
pins the rule from both sides:

* every refused sample is refused by the create model AND the update model;
* every accepted sample is one the matcher can match (accepted => matchable), and the extension the
  matcher reads from an ordinary CAD, scan or model file name is accepted (matchable => accepted).
  The second direction is the control for a per-entry length cap: `.CATProduct` and `.CATDrawing`
  are 11 characters, and the field's own `max_length` is the only bound on size.

A restriction already stored in a refused form is not rewritten, and the matcher still ignores it;
`tests/common/test_metadataSchemaValidation_file_type_restriction.py` pins that side.
"""

import re
from pathlib import Path

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError

from backend.backend.common.metadataSchemaValidation import extract_file_extension

SCHEMA_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

# (restriction, a fragment of the refusal). Each list holds an entry no file name can produce as its
# extension, alone or beside a valid entry.
_UNMATCHABLE = [
    ("stp", "must start with a dot"),
    (".stp,step", "must start with a dot"),
    ("stp,.step", "must start with a dot"),
    (".", "at least one character after the dot"),
    # A file is matched by the text after its LAST dot, so `archive.tar.gz` reads as `.gz`.
    (".tar.gz", "only one dot"),
    (".a/b", "path separator"),
    (".a\\b", "path separator"),
    (".glb,,.usd", "must be non-empty"),
]
_UNMATCHABLE_IDS = [
    "undotted", "second-entry-undotted", "first-entry-undotted", "bare-dot", "second-dot",
    "slash", "backslash", "empty-entry",
]

_MATCHABLE = [
    ".stp,.step",
    # The matcher strips and lowercases each entry, so padding and case are harmless.
    " .glb , .USD ",
    ".all",
    ".ALL,.glb",
    ".CATProduct",
    ".CATDrawing,.CATPart",
    ".safetensors",
    # Parasolid text; an underscore is part of a real extension.
    ".x_t",
    # 16 characters: nothing bounds one entry below the field's max_length.
    "." + "a" * 15,
]

# Ordinary file names; the extension the matcher reads from each has to be writable as an entry.
_FILE_NAMES = [
    "part.CATProduct", "drawing.CATDrawing", "model.safetensors", "body.x_t", "scan.e57", "a.glb",
]

# The CDK seeds a restricted GLOBAL fileAttribute schema on every deployment, and the web editor
# re-sends the stored restriction on every save, so a seeded value the models refused would leave
# that schema uneditable in the UI.
_SEEDED_DEFAULTS = (Path(__file__).resolve().parents[3] / "infra" / "lib" / "nestedStacks"
                    / "apiLambda" / "constructs" / "dynamodb-metadataschema-defaults-construct.ts")


def _create_body(restriction):
    return {
        "databaseId": "test-db",
        "metadataSchemaEntityType": "fileMetadata",
        "schemaName": "Test Schema",
        "fields": {"fields": [{"metadataFieldKeyName": "partNumber",
                               "metadataFieldValueType": "string"}]},
        "fileKeyTypeRestriction": restriction,
    }


def _parse_create(restriction):
    from models.metadataSchema import CreateMetadataSchemaRequestModel
    return parse(_create_body(restriction), model=CreateMetadataSchemaRequestModel)


def _parse_update(restriction, **extra):
    from models.metadataSchema import UpdateMetadataSchemaRequestModel
    body = {"metadataSchemaId": SCHEMA_ID, "fileKeyTypeRestriction": restriction}
    body.update(extra)
    return parse(body, model=UpdateMetadataSchemaRequestModel)


_PARSERS = [_parse_create, _parse_update]
_PARSER_IDS = ["create", "update"]


@pytest.mark.unit
class TestUnmatchableEntriesAreRefused:
    @pytest.mark.parametrize("parse_restriction", _PARSERS, ids=_PARSER_IDS)
    @pytest.mark.parametrize("restriction, reason", _UNMATCHABLE, ids=_UNMATCHABLE_IDS)
    def test_an_entry_no_file_can_match_is_refused(self, parse_restriction, restriction, reason):
        with pytest.raises(ValidationError, match=reason):
            parse_restriction(restriction)


@pytest.mark.unit
class TestMatchableEntriesAreAccepted:
    @pytest.mark.parametrize("parse_restriction", _PARSERS, ids=_PARSER_IDS)
    @pytest.mark.parametrize("restriction", _MATCHABLE)
    def test_a_matchable_list_is_accepted_as_written(self, parse_restriction, restriction):
        """Stored as given: the matcher, not the model, normalises padding and case."""
        assert parse_restriction(restriction).fileKeyTypeRestriction == restriction

    @pytest.mark.parametrize("restriction", _MATCHABLE)
    def test_every_accepted_entry_is_one_the_matcher_can_match(self, restriction):
        for entry in (part.strip().lower() for part in restriction.split(",")):
            if entry == ".all":
                continue
            assert extract_file_extension("folder/part" + entry) == entry, entry

    @pytest.mark.parametrize("parse_restriction", _PARSERS, ids=_PARSER_IDS)
    @pytest.mark.parametrize("file_name", _FILE_NAMES)
    def test_the_extension_the_matcher_reads_can_be_written(self, parse_restriction, file_name):
        extension = extract_file_extension(file_name)
        assert extension, f"{file_name} has no extension, so it tests nothing"

        assert parse_restriction(extension).fileKeyTypeRestriction == extension

    @pytest.mark.parametrize("parse_restriction", _PARSERS, ids=_PARSER_IDS)
    def test_the_restriction_seeded_on_every_deployment_is_accepted(self, parse_restriction):
        seeded = re.findall(r'fileKeyTypeRestriction:\s*\{\s*S:\s*"([^"]*)"',
                            _SEEDED_DEFAULTS.read_text(encoding="utf-8"))
        assert seeded, f"no seeded fileKeyTypeRestriction found in {_SEEDED_DEFAULTS.name}"

        for restriction in seeded:
            assert parse_restriction(restriction).fileKeyTypeRestriction == restriction


@pytest.mark.unit
class TestUpdateClearsTheRestriction:
    @pytest.mark.parametrize("blank", ["", None, "   "], ids=["empty", "null", "whitespace"])
    def test_a_blank_restriction_beside_another_field_parses(self, blank):
        """The handler reads a blank value as "remove the restriction", so the model lets it through
        unchecked."""
        model = _parse_update(blank, enabled=False)

        assert model.fileKeyTypeRestriction == blank
        assert model.enabled is False

    @pytest.mark.parametrize("blank", ["", None], ids=["empty", "null"])
    def test_a_clear_on_its_own_is_not_a_field_to_update(self, blank):
        """A clear travels beside another field: the "at least one field" rule reads an empty or
        null restriction as absent."""
        with pytest.raises(ValidationError, match="At least one field must be provided"):
            _parse_update(blank)


@pytest.mark.unit
class TestRefusalsDoNotEchoTheEntry:
    """A refusal describes the rule and never repeats the submitted entry (backend Rule 11)."""

    @pytest.mark.parametrize("parse_restriction", _PARSERS, ids=_PARSER_IDS)
    @pytest.mark.parametrize("restriction", ["SECRETab", ".SECRET.cd"],
                             ids=["undotted", "second-dot"])
    def test_the_message_names_the_rule_not_the_entry(self, parse_restriction, restriction):
        with pytest.raises(ValidationError) as refused:
            parse_restriction(restriction)

        messages = [error["msg"] for error in refused.value.errors()]
        assert messages, "the refusal carried no message, so the check below is vacuous"
        assert not any("SECRET" in message for message in messages), messages

    @pytest.mark.parametrize("parse_restriction", _PARSERS, ids=_PARSER_IDS)
    def test_the_same_marker_in_the_dotted_form_is_accepted(self, parse_restriction):
        """Positive control: the marker text is not what is refused, the form is."""
        assert parse_restriction(".SECRETab").fileKeyTypeRestriction == ".SECRETab"
