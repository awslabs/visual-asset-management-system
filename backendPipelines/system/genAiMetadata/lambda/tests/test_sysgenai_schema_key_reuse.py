#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Schema-aware key reuse: the input envelope's ``metadataSchemas`` section types the empty schema keys the analysis
fills, a field that lands in a controlled-list key is asked for one of the list's values through the
structured-output tool, and a tool answer is screened by the guardrail before it is written.

The envelope is built with the backend's own envelope builders (the fixtures of the opt-in envelope suite), so the
section's shape is the one executeWorkflow writes."""

import json
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h
import test_sysgenai_opt_in_envelope_sections as base

er = base.er
kr = h.load_local("keyReuse")

GUARDRAIL_ENV = {"BEDROCK_GUARDRAIL_IDENTIFIER": "gr-test", "BEDROCK_GUARDRAIL_VERSION": "3"}
FILE_METADATA_KEY = base.META_PREFIX + "models/pump.glb.metadata.json"
ASSET_METADATA_KEY = base.META_PREFIX + "asset.metadata.json"
SUMMARY_KEY = base.RESULTS_PREFIX + "analysis-summary.json"
STATUS_KEY = base.RESULTS_PREFIX + "execution.status.json"
# keyReuse.FILE_TARGETS_KEY, spelled out so the tests run (and fail) on a module without it.
TARGETS_KEY = "genai_targets"


def _field(name, value_type="string", controlled=None, extensions=None):
    return er.build_metadata_schema_field(
        name, {"metadataFieldValueType": value_type, "controlledListKeys": controlled or []},
        file_key_type_restriction=extensions)


def _schema_entry(database_id="dbM", asset_category=("Industrial Equipment", "Vehicle")):
    """dbM's aggregated schema: Material is a controlled list with one entry per extension reach, Weight a number for
    .glb files, Notes a string for .txt files only, and the asset's Category a controlled list."""
    return er.build_metadata_schema_entry(
        database_id, restrict_metadata_outside_schemas=True,
        asset_metadata=[_field("Category", "inline_controlled_list", list(asset_category))],
        file_metadata=[_field("Description", extensions=[]),
                       _field("Material", "inline_controlled_list", ["brass", "steel"], [".glb"]),
                       _field("Material", "inline_controlled_list", ["paper"], [".pdf"]),
                       _field("Colour", extensions=[]),
                       _field("Weight", "number", extensions=[".glb"]),
                       _field("Notes", extensions=[".txt"])],
        file_attributes=[])


def _envelope(schemas=None):
    envelope = base._envelope(False)
    if schemas is not None:
        envelope["metadataSchemas"] = schemas
    return envelope


def _run(envelope, script=None, config=None, env=None, guardrail_script=None):
    s3 = h.FakeS3()
    s3.put_json(base.AUX, base.MANIFEST_KEY, json.loads(json.dumps(base.MANIFEST)))
    s3.put_json("abkt", base.CONFIG_KEY, dict(base.CONFIG, **(config or {})))
    s3.put_json("abkt", base.METADATA_KEY, envelope)
    s3.objects[(base.AUX, base.AUX_PREFIX + "renders/view0.png")] = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    mod = h.load_handler("generateMetadata", env)
    mod.s3_client = s3
    bedrock = h.FakeBedrock(script or [h.converse_tool_response(dict(base.MODEL_REPLY))], guardrail_script)
    mod.bedrock_runtime = bedrock
    mod.time = MagicMock()
    state = mod.lambda_handler(base._state(), MagicMock())
    return mod, s3, bedrock, state


def _rows(s3, key):
    if ("abkt", key) not in s3.objects:
        return None
    return {row["metadataKey"]: (row["metadataValue"], row["metadataValueType"])
            for row in s3.json_at("abkt", key)["metadata"]}


def _tool_schema(call):
    return call["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["properties"]


@pytest.mark.unit
class TestSchemaFieldPick:
    def setup_method(self):
        self.mod = h.load_handler("generateMetadata")
        self.envelope = _envelope([_schema_entry()])

    def test_a_glb_file_takes_its_extension_entry_and_the_unrestricted_ones(self):
        fields = self.mod.metadata_schema_fields(self.envelope, "fileMetadata", "dbM", "/models/pump.glb")
        assert sorted(fields) == ["Colour", "Description", "Material", "Weight"]
        assert fields["Material"] == {"type": "inline_controlled_list", "controlledList": ["brass", "steel"]}
        assert fields["Weight"]["type"] == "number"

    def test_a_pdf_file_takes_the_entry_listing_its_extension(self):
        fields = self.mod.metadata_schema_fields(self.envelope, "fileMetadata", "dbM", "/docs/manual.PDF")
        assert fields["Material"]["controlledList"] == ["paper"]
        assert "Weight" not in fields and "Notes" not in fields

    def test_a_file_without_an_extension_takes_only_the_unrestricted_entries(self):
        fields = self.mod.metadata_schema_fields(self.envelope, "fileMetadata", "dbM", "/docs/README")
        assert sorted(fields) == ["Colour", "Description"]

    def test_the_asset_takes_its_own_fields(self):
        fields = self.mod.metadata_schema_fields(self.envelope, "assetMetadata", "dbM")
        assert fields == {"Category": {"type": "inline_controlled_list",
                                       "controlledList": ["Industrial Equipment", "Vehicle"]}}

    def test_a_database_left_out_of_the_section_has_no_schema(self):
        other = _envelope([_schema_entry("dbOther")])
        assert self.mod.metadata_schema_fields(other, "fileMetadata", "dbM", "/models/pump.glb") is None
        assert self.mod.metadata_schema_fields(_envelope(), "assetMetadata", "dbM") is None


@pytest.mark.unit
class TestSchemaAwareAnalysis:
    def test_an_empty_controlled_list_key_receives_a_listed_value_with_its_type(self):
        _mod, s3, _bedrock, state = _run(_envelope([_schema_entry()]))
        assert state["analysisStatus"] == "SUCCEEDED"
        rows = _rows(s3, FILE_METADATA_KEY)
        assert rows["Material"] == ("brass", "inline_controlled_list")
        assert "genai_materials" not in rows
        summary = s3.json_at("abkt", SUMMARY_KEY)
        assert summary["keyReuse"]["targets"]["file"]["materials"] == "Material"
        assert summary["keyReuse"]["schemaRead"] is True
        # The written schema key joins the digest record, so a later run may write it again.
        attributes = {row["metadataKey"]: row["metadataValue"]
                      for row in s3.json_at("abkt", base.META_PREFIX + "models/pump.glb.attribute.json")["metadata"]}
        assert kr.value_digest("brass") == json.loads(attributes[kr.FILE_DIGEST_KEY])["Material"]

    def test_the_request_asks_for_the_listed_values_through_the_tool(self):
        _mod, _s3, bedrock, _state = _run(_envelope([_schema_entry()]))
        call = bedrock.calls[0]
        properties = _tool_schema(call)
        assert properties["materials"]["items"]["enum"] == ["brass", "steel"]
        assert "enum" not in properties["colors"]["items"]
        assert properties["complexity"]["enum"] == ["low", "medium", "high"]
        assert len(call["system"]) == 2 and "record_analysis" in call["system"][1]["text"]
        prompt = json.dumps(call["messages"], default=str)
        assert "materials: one of brass, steel" in prompt

    def test_a_value_outside_the_list_stays_in_the_pipeline_key(self):
        reply = dict(base.MODEL_REPLY, materials=["copper"])
        _mod, s3, _bedrock, _state = _run(_envelope([_schema_entry()]), [h.converse_tool_response(reply)])
        rows = _rows(s3, FILE_METADATA_KEY)
        assert "Material" not in rows and rows["genai_materials"] == ("copper", "string")
        conflicts = s3.json_at("abkt", SUMMARY_KEY)["keyConflicts"]
        assert {"field": "materials", "key": "Material", "reason": "notInControlledList", "entity": "file"} in conflicts

    def test_a_schema_key_of_another_type_is_never_filled(self):
        mapping = dict(kr.FIELD_MAPPING_DEFAULT, sizeEstimate=["weight"])
        envelope = _envelope([_schema_entry()])
        envelope["assets"][0]["files"][1]["metadata"]["Weight"] = ""
        _mod, s3, _bedrock, _state = _run(envelope, config={"fieldMapping": mapping})
        rows = _rows(s3, FILE_METADATA_KEY)
        assert "Weight" not in rows and rows["genai_size_estimate"][0] == "about 30 cm long"
        conflicts = s3.json_at("abkt", SUMMARY_KEY)["keyConflicts"]
        assert {"field": "sizeEstimate", "key": "Weight", "reason": "typeUnsupported", "entity": "file"} in conflicts

    def test_a_value_a_person_set_is_left_alone(self):
        _mod, s3, _bedrock, _state = _run(_envelope([_schema_entry()]))
        assert "Colour" not in _rows(s3, FILE_METADATA_KEY)
        conflicts = s3.json_at("abkt", SUMMARY_KEY)["keyConflicts"]
        assert {"field": "colors", "key": "Colour", "reason": "edited", "entity": "file"} in conflicts

    def test_the_asset_target_is_constrained_by_the_asset_schema(self):
        _mod, s3, bedrock, _state = _run(_envelope([_schema_entry()]), config={"metadataWriteTarget": "asset"})
        assert _tool_schema(bedrock.calls[0])["category"]["enum"] == ["Industrial Equipment", "Vehicle"]
        assert _rows(s3, ASSET_METADATA_KEY)["Category"] == ("Industrial Equipment", "inline_controlled_list")

    def test_both_targets_are_constrained_by_the_file_schema_only(self):
        _mod, s3, bedrock, _state = _run(_envelope([_schema_entry(asset_category=("Pump", "Valve"))]))
        properties = _tool_schema(bedrock.calls[0])
        assert "enum" not in properties["category"] and properties["materials"]["items"]["enum"] == ["brass", "steel"]
        # The asset key's list misses the answer, so the asset's category stays in its pipeline key.
        assert _rows(s3, ASSET_METADATA_KEY)["genai_asset_category"][0] == "Industrial Equipment"
        assert "Category" not in _rows(s3, ASSET_METADATA_KEY)

    def test_without_the_section_the_request_is_unchanged_and_the_summary_says_why(self):
        _mod, s3, bedrock, _state = _run(_envelope(), [h.converse_response(json.dumps(base.MODEL_REPLY))])
        assert "toolConfig" not in bedrock.calls[0] and len(bedrock.calls[0]["system"]) == 1
        assert "Material" not in _rows(s3, FILE_METADATA_KEY)
        summary = s3.json_at("abkt", SUMMARY_KEY)
        assert summary["keyReuse"]["schemaRead"] is False
        assert any("No metadata schema of database dbM reached this run" in w for w in summary["warnings"])
        assert {"field": "materials", "key": "Material", "reason": "typeUnknown",
                "entity": "file"} in summary["keyConflicts"]

    def test_a_database_left_out_of_the_section_is_read_as_no_schema(self):
        _mod, s3, bedrock, _state = _run(_envelope([_schema_entry("dbOther")]),
                                         [h.converse_response(json.dumps(base.MODEL_REPLY))])
        assert "toolConfig" not in bedrock.calls[0]
        assert any("database dbM" in w for w in s3.json_at("abkt", SUMMARY_KEY)["warnings"])

    def test_reuse_off_reads_no_schema(self):
        _mod, s3, bedrock, _state = _run(_envelope([_schema_entry()]), [h.converse_response(
            json.dumps(base.MODEL_REPLY))], config={"reuseExistingKeys": False})
        assert "toolConfig" not in bedrock.calls[0]
        assert "Material" not in _rows(s3, FILE_METADATA_KEY)
        assert not any("No metadata schema" in w for w in s3.json_at("abkt", SUMMARY_KEY)["warnings"])


def _second_run_envelope(material="brass"):
    """The envelope of a later run: an earlier run filled the file's Material from its controlled list and
    recorded the digest."""
    envelope = _envelope([_schema_entry()])
    record = envelope["assets"][0]["files"][1]
    record["metadata"]["Material"] = material
    record["attributes"][kr.FILE_DIGEST_KEY] = kr.encode_digest_record({"Material": kr.value_digest(material)})
    return envelope


def _attributes(s3):
    return {row["metadataKey"]: row["metadataValue"]
            for row in s3.json_at("abkt", base.META_PREFIX + "models/pump.glb.attribute.json")["metadata"]}


@pytest.mark.unit
class TestSecondRun:
    """A key the pipeline filled from the schema on an earlier run is typed by the schema again (D39)."""

    def test_the_tool_asks_for_the_list_and_the_key_takes_its_entry(self):
        _mod, s3, bedrock, _state = _run(_second_run_envelope())
        assert _tool_schema(bedrock.calls[0])["materials"]["items"]["enum"] == ["brass", "steel"]
        rows = _rows(s3, FILE_METADATA_KEY)
        assert rows["Material"] == ("brass", "inline_controlled_list") and "genai_materials" not in rows
        summary = s3.json_at("abkt", SUMMARY_KEY)
        assert summary["keyReuse"]["targets"]["file"]["materials"] == "Material"
        assert not [c for c in summary["keyConflicts"] if c["key"] == "Material"]

    def test_an_answer_outside_the_list_lands_in_the_pipeline_key_and_empties_the_earlier_value(self):
        reply = dict(base.MODEL_REPLY, materials=["copper"])
        _mod, s3, _bedrock, _state = _run(_second_run_envelope(), [h.converse_tool_response(reply)])
        rows = _rows(s3, FILE_METADATA_KEY)
        assert rows["Material"] == ("", "inline_controlled_list")
        assert rows["genai_materials"] == ("copper", "string")
        summary = s3.json_at("abkt", SUMMARY_KEY)
        assert {"field": "materials", "key": "Material", "reason": "notInControlledList",
                "entity": "file"} in summary["keyConflicts"]
        assert "Material" in summary["keyReuse"]["cleared"]
        assert "Material" not in json.loads(_attributes(s3)[kr.FILE_DIGEST_KEY])


def _mapped_keys_envelope(stored_targets=None):
    """The subject carries empty schema keys named like the analysis fields, so key reuse writes the title,
    description and materials there and clears the genai_* keys."""
    envelope = _envelope([_schema_entry()])
    record = envelope["assets"][0]["files"][1]
    record["metadata"].update({"Title": "", "Description": ""})
    if stored_targets is not None:
        record["attributes"][TARGETS_KEY] = kr.encode_digest_record(stored_targets)
    return envelope


@pytest.mark.unit
class TestTargetsRecord:
    """D44: the file's targets record names the key each field landed in outside its genai_* key, so the asset
    summary can read a description key reuse wrote into a schema key."""

    def test_the_record_names_each_field_written_outside_its_genai_key(self):
        _mod, s3, _bedrock, _state = _run(_mapped_keys_envelope())
        rows = _rows(s3, FILE_METADATA_KEY)
        assert rows["Description"][0] == "A brass gear pump with an inlet flange."
        assert rows["genai_title"] == ("", "string")  # moved to Title, so cleared
        assert json.loads(_attributes(s3)[TARGETS_KEY]) == {
            "description": "Description", "materials": "Material", "title": "Title"}

    def test_an_unchanged_record_is_not_written_again(self):
        stored = {"description": "Description", "materials": "Material", "title": "Title"}
        _mod, s3, _bedrock, _state = _run(_mapped_keys_envelope(stored))
        assert TARGETS_KEY not in _attributes(s3)

    def test_a_field_back_in_its_genai_key_leaves_the_record(self):
        stored = {"description": "Description", "keywords": "tags"}
        envelope = _envelope([_schema_entry()])
        envelope["assets"][0]["files"][1]["attributes"][TARGETS_KEY] = kr.encode_digest_record(stored)
        _mod, s3, _bedrock, _state = _run(envelope)
        assert json.loads(_attributes(s3)[TARGETS_KEY]) == {"materials": "Material"}

    def test_reuse_off_writes_no_record(self):
        _mod, s3, _bedrock, _state = _run(_mapped_keys_envelope(), [h.converse_response(
            json.dumps(base.MODEL_REPLY))], config={"reuseExistingKeys": False})
        assert TARGETS_KEY not in _attributes(s3)

    def test_the_asset_summary_reads_what_the_per_file_run_wrote(self):
        """The chain the review reproduced: a per-file run writes the description into the schema's Description
        key; the summary of the asset, reading the file as the envelope then carries it, finds it."""
        envelope = _mapped_keys_envelope()
        record = envelope["assets"][0]["files"][1]
        _mod, s3, _bedrock, _state = _run(envelope)
        written = {row["metadataKey"]: row["metadataValue"]
                   for row in s3.json_at("abkt", FILE_METADATA_KEY)["metadata"]}
        after = er.build_metadata_file_record(base.SUBJECT, metadata=dict(record["metadata"], **written),
                                              attributes=dict(record["attributes"], **_attributes(s3)))
        assert not after["metadata"].get("genai_description") and not after["metadata"].get("genai_title")
        summary = h.load_handler("summarizeAsset")
        [entry] = summary.file_entries({"files": [after]}, "/")
        assert (entry["title"], entry["description"]) == ("Brass gear pump",
                                                          "A brass gear pump with an inlet flange.")


@pytest.mark.unit
class TestStructuredOutput:
    def test_the_tool_answer_is_written(self):
        _mod, s3, _bedrock, _state = _run(_envelope([_schema_entry()]))
        assert _rows(s3, FILE_METADATA_KEY)["genai_title"] == ("Brass gear pump", "string")
        assert s3.json_at("abkt", SUMMARY_KEY)["replyFormat"] == "tool"

    def test_a_text_answer_to_a_tool_request_is_read_as_json(self):
        _mod, s3, _bedrock, _state = _run(_envelope([_schema_entry()]),
                                          [h.converse_response(json.dumps(base.MODEL_REPLY))])
        assert _rows(s3, FILE_METADATA_KEY)["Material"] == ("brass", "inline_controlled_list")
        assert s3.json_at("abkt", SUMMARY_KEY)["replyFormat"] == "text"

    def test_a_key_the_tool_input_adds_never_becomes_a_row(self):
        reply = dict(base.MODEL_REPLY, Manufacturer="ACME", genai_secret="x", PART_NO="override")
        _mod, s3, _bedrock, _state = _run(_envelope([_schema_entry()]), [h.converse_tool_response(reply)])
        rows = _rows(s3, FILE_METADATA_KEY)
        assert not {"Manufacturer", "genai_secret", "PART_NO"} & set(rows)

    def test_a_model_that_refuses_the_tool_is_asked_in_prompt_json_and_remembered(self):
        refusal = h.client_error("ValidationException", "This model doesn't support tool use.")
        mod, s3, bedrock, state = _run(_envelope([_schema_entry()]),
                                       [refusal, h.converse_response(json.dumps(base.MODEL_REPLY))])
        assert state["analysisStatus"] == "SUCCEEDED"
        assert [("toolConfig" in call) for call in bedrock.calls] == [True, False]
        assert _rows(s3, FILE_METADATA_KEY)["Material"] == ("brass", "inline_controlled_list")
        # The same process skips the tool for that model from now on.
        mod.bedrock_runtime = later = h.FakeBedrock([h.converse_response(json.dumps(base.MODEL_REPLY))])
        mod.analyze([{"text": "describe"}], [], tool=mod.analysis_tool({}))
        assert "toolConfig" not in later.calls[0]

    def test_another_validation_error_is_a_failure(self):
        error = h.client_error("ValidationException", "The provided model identifier is invalid.")
        _mod, s3, bedrock, state = _run(_envelope([_schema_entry()]), [error])
        assert state["analysisStatus"] == "FAILED" and len(bedrock.calls) == 1
        assert s3.json_at("abkt", STATUS_KEY)["error"] == "BedrockModelError"

    def test_the_guardrail_screens_the_tool_answer_as_output(self):
        masked = json.dumps(dict(base.MODEL_REPLY, description="A pump made by {NAME}."))
        guardrail = [h.apply_guardrail_response(outputs=[masked], assessments=[h.pii_assessment("NAME")])]
        _mod, s3, bedrock, _state = _run(_envelope([_schema_entry()]), env=GUARDRAIL_ENV,
                                         guardrail_script=guardrail)
        assert [call["source"] for call in bedrock.guardrail_calls] == ["OUTPUT"]
        screened = bedrock.guardrail_calls[0]["content"][0]["text"]["text"]
        assert json.loads(screened)["title"] == "Brass gear pump"
        assert _rows(s3, FILE_METADATA_KEY)["genai_description"][0] == "A pump made by {NAME}."
        summary = s3.json_at("abkt", SUMMARY_KEY)
        assert summary["guardrailMasked"] is True and summary["guardrailMaskedTypes"] == ["NAME"]
        assert "Jane Q. Public" not in json.dumps(summary)

    def test_a_blocked_tool_answer_fails_the_analysis_and_writes_nothing_of_it(self):
        guardrail = [h.apply_guardrail_response(assessments=[h.prompt_attack_assessment()])]
        _mod, s3, _bedrock, state = _run(_envelope([_schema_entry()]), env=GUARDRAIL_ENV,
                                         guardrail_script=guardrail)
        assert state["analysisStatus"] == "FAILED"
        assert s3.json_at("abkt", STATUS_KEY)["error"] == "BedrockGuardrailIntervened"
        assert _rows(s3, FILE_METADATA_KEY) is None or "Material" not in _rows(s3, FILE_METADATA_KEY)

    def test_a_text_answer_is_not_screened_again(self):
        _mod, _s3, bedrock, _state = _run(_envelope([_schema_entry()]),
                                          [h.converse_response(json.dumps(base.MODEL_REPLY))], env=GUARDRAIL_ENV)
        # The Converse call's own guardrail evaluates a text reply; only a tool input needs ApplyGuardrail.
        assert bedrock.guardrail_calls == []
