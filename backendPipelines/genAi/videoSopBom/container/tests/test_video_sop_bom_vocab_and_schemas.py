# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Vocabulary constants, JSON Schemas and prompt files of the Video SOP/BOM Extraction container.

The schemas are validated at unit-test time and at runtime against the same files, and every enum a
schema carries is asserted equal to the Python constant the renderer validates with, so the prompt
list, the schema enum and the CSV writer cannot drift apart.
"""

import hashlib
import re

import pytest
from jsonschema import Draft202012Validator

from video_sop_bom_pipeline import (
    OUTPUT_FOLDER,
    PROMPT_NAMES,
    PROMPT_TOKENS,
    REPORTER,
    SCHEMA_NAMES,
    load_prompt,
    load_schema,
)
from video_sop_bom_pipeline import vocab


def _errors(schema, document):
    """Sorted error messages for `document` against `schema` (empty list when valid)."""
    validator = Draft202012Validator(schema)
    return sorted(error.message for error in validator.iter_errors(document))


@pytest.mark.unit
class TestPackageLoaders:
    def test_schema_names_are_the_registry_list(self):
        assert SCHEMA_NAMES == (
            "config_schema", "definition_schema", "sop_schema", "bom_row_schema",
            "lab_summary_schema", "frames_schema", "timeline_schema",
            "analysis_report_schema", "summary_schema",
            "window_extraction_schema", "vision_schema", "finalize_schema",
        )

    def test_prompt_names_are_the_registry_list(self):
        assert PROMPT_NAMES == ("window_extraction", "vision_verification", "finalize", "system_boundary")
        assert set(PROMPT_TOKENS) == set(PROMPT_NAMES)
        assert PROMPT_TOKENS["system_boundary"] == ()

    def test_reporter_marker_and_output_folder_are_the_registry_values(self):
        assert REPORTER == "video_sop_bom_pipeline"
        assert OUTPUT_FOLDER == "sop-bom/"
        assert OUTPUT_FOLDER.endswith("/") and "/" not in OUTPUT_FOLDER[:-1], "one flat folder, no leading slash"

    def test_unknown_names_raise_key_error(self):
        with pytest.raises(KeyError):
            load_schema("not_a_schema")
        with pytest.raises(KeyError):
            load_prompt("not_a_prompt")


LCA_HEADER_BYTES = (
    b"part_level,part_type,lab_part_number,manufacturer_part_number,alternative,part_description,qty,"
    b"material_or_component_type,material_notes,material_composition,material_percent_recycled_content,"
    b"mass_g_per_unit,manufacturing_country,supplier_carbon_footprint_kgCO2e_per_unit,defect_loss,"
    b"method_for_weight,primary_manufacturing_process,primary_mfg_yield_loss,primary_mfg_ref_unit,"
    b"primary_mfg_elect_consumption_kwh_per_unit,primary_elect_from_RE_purchased_percent,"
    b"primary_RE_wind_percent,primary_RE_solar_percent,primary_elect_from_RE_onsite_percent,"
    b"primary_mfg_natural_gas_consumption_kwh_per_unit,primary_mfg_f_gas_ghg_emissions_kgCO2e_per_unit,"
    b"primary_mfg_other_direct_ghg_emissions_kgCO2e_per_unit,primary_mfg_water_consumption_m3,"
    b"secondary_manufacturing_process,secondary_mfg_yield_loss,secondary_mfg_ref_unit,"
    b"secondary_mfg_elect_consumption_kwh_per_unit,secondary_elect_from_RE_purchased_percent,"
    b"secondary_RE_wind_percent,secondary_RE_solar_percent,secondary_elect_from_RE_onsite_percent,"
    b"secondary_mfg_natural_gas_consumption_kwh_per_unit,secondary_mfg_f_gas_ghg_emissions_kgCO2e_per_unit,"
    b"secondary_mfg_other_direct_ghg_emissions_kgCO2e_per_unit,secondary_mfg_water_consumption_m3,"
    b"ic_type,ic_process_node_primary,ic_process_node_secondary,ic_die_size_mm2,ic_mfg_abatement,"
    b"ic_package_type,ic_package_length_mm,ic_package_width_mm,ic_package_depth_mm,ic_yield_loss,"
    b"ic_frontend_elect_consumption_kwh_per_unit,ic_backend_elect_consumption_kwh_per_unit,"
    b"ic_f_gas_per_unit_kg_CO2e,ic_other_direct_ghg_per_unit_kg_CO2e,pcb_board_area_cm2,pcb_type,"
    b"pcb_layers,pcb_shipping_panel_utilization,pcb_material_utilization,pcb_board_finish,"
    b"pcb_elect_consumption_kwh_per_unit,display_type,display_active_area_cm2,"
    b"display_elect_consumption_kwh_per_unit,battery_type,battery_capacity_wh\n"
)
LCA_HEADER_SHA256 = "cef84900440d2923a066489d542d83595a4562733987cc9f12b098c8928dac0a"

PART_TYPES_FROM_CSV = (
    "Camera or Light Sensing Device", "Chemicals", "Protective Materials", "Connector", "Enclosure", "FPC",
    "Product Assembly Accessory", "Product Assembly Device", "Packaged Product Assembly", "Cable",
    "Electronics", "IC", "Mid-Frame", "Soft Goods", "Packaging", "Threaded Fasteners", "Mechanical Hardware",
    "Display", "Packout Assembly", "ODM Part", "Label", "Frame", "PCB", "PCBA", "Print Materials",
    "Passive Electrical Device", "Sub-Assembly", "Electromechanical Device", "Raw Material",
    "Battery Assembly",
)

PRIMARY_TECHNIQUES_FROM_CSV = (
    "Assembly- FATP-Electronics", "Assembly - General", "Assembly - FATP - Touch & Display",
    "Molding - Plastics", "R2R Circuit Processing", "Forming - Metalwork", "SMT", "Assembly - FATP",
    "Assembly - Electro-Mechanical", "Assembly - Electrical Component", "Electrode Coating and Winding",
    "Assembly - Electromechanical Component", "Cable Processing", "Passive Electronics Processing",
    "Thin-Film Deposition", "Glass Making Process", "Semiconductor Device Fabrication",
    "Optical Film Processing", "Chemical Mixing", "Forming - Ceramics", "Chemical",
    "Polymer Synthesis - Bio-Based Polymers", "Biomass Processing", "Corrugator", "Tanning",
    "Natural Fiber Textile Processing", "Woodworking", "Papermaking",
)

SECONDARY_TECHNIQUES_FROM_CSV = (
    "Smelting", "Polymer Synthesis - Bio-Based Polymers", "Polymer Synthesis - Thermoplastic",
    "Polymer Synthesis - Thermoset", "Polymer Synthesis - Fluoropolymer", "Polymer Compounding",
    "Polymer Foaming",
)


def _iter_vocab_strings():
    """Every string held by a public tuple/dict constant of `vocab`."""
    for name in dir(vocab):
        if name.startswith("_") or not name.isupper() or name == "LCA_BOM_HEADER_LINE":
            continue
        value = getattr(vocab, name)
        if isinstance(value, str):
            yield name, value
        elif isinstance(value, tuple):
            for item in value:
                if isinstance(item, str):
                    yield name, item
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, str):
                            yield name, sub
        elif isinstance(value, dict):
            for key, item in value.items():
                yield name, key
                if isinstance(item, str):
                    yield name, item


@pytest.mark.unit
class TestVocab:
    def test_lca_columns_are_exactly_66(self):
        assert len(vocab.LCA_BOM_COLUMNS) == 66
        assert vocab.LCA_BOM_COLUMNS[0] == "part_level"
        assert vocab.LCA_BOM_COLUMNS[16] == "primary_manufacturing_process"
        assert vocab.LCA_BOM_COLUMNS[40] == "ic_type"
        assert vocab.LCA_BOM_COLUMNS[65] == "battery_capacity_wh"

    def test_lca_header_bytes_are_the_contract(self):
        assert vocab.LCA_BOM_HEADER_LINE == ",".join(vocab.LCA_BOM_COLUMNS) + "\n"
        assert vocab.LCA_BOM_HEADER_BYTES == LCA_HEADER_BYTES
        assert not vocab.LCA_BOM_HEADER_BYTES.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM forbidden"
        assert b"\r" not in vocab.LCA_BOM_HEADER_BYTES, "LF only"
        assert hashlib.sha256(vocab.LCA_BOM_HEADER_BYTES).hexdigest() == LCA_HEADER_SHA256

    def test_lca_header_line_is_exactly_one_line(self):
        assert vocab.LCA_BOM_HEADER_LINE.count("\n") == 1
        assert vocab.LCA_BOM_HEADER_LINE.endswith("\n")

    def test_lca_columns_unique_snake_case_and_required_subset(self):
        assert len(set(vocab.LCA_BOM_COLUMNS)) == 66
        assert all(re.fullmatch(r"[A-Za-z0-9_]+", column) for column in vocab.LCA_BOM_COLUMNS)
        assert vocab.LCA_BOM_REQUIRED_COLUMNS == vocab.LCA_BOM_COLUMNS[:8]

    def test_part_types_are_the_thirty_from_part_types_csv(self):
        assert vocab.PART_TYPES == PART_TYPES_FROM_CSV
        assert "Battery" not in vocab.PART_TYPES, "the reference bom_schema.json's bogus value"

    def test_part_type_descriptions_cover_every_part_type(self):
        assert tuple(vocab.PART_TYPE_DESCRIPTIONS) == vocab.PART_TYPES
        assert vocab.PART_TYPE_DESCRIPTIONS["FPC"] == "Flexible printed circuit"
        assert vocab.PART_TYPE_DESCRIPTIONS["Product Assembly Device"] == "Fully assembled device"

    def test_material_types_are_132_distinct_in_table_order(self):
        assert len(vocab.MATERIAL_TYPES) == 132
        assert len(set(vocab.MATERIAL_TYPES)) == 132
        assert vocab.MATERIAL_TYPES[0] == "Battery - Assembly"
        assert vocab.MATERIAL_TYPES[-1] == "TPU"
        assert "Bio-PU" in vocab.MATERIAL_TYPES and "Bio - PU" in vocab.MATERIAL_TYPES

    def test_every_material_has_one_primary_technique(self):
        assert tuple(vocab.MATERIAL_PRIMARY_TECHNIQUE) == vocab.MATERIAL_TYPES
        assert set(vocab.MATERIAL_PRIMARY_TECHNIQUE.values()) == set(vocab.PRIMARY_TECHNIQUES)
        assert set(vocab.MATERIAL_SECONDARY_TECHNIQUE.values()) == set(vocab.SECONDARY_TECHNIQUES)
        assert set(vocab.MATERIAL_SECONDARY_TECHNIQUE) < set(vocab.MATERIAL_TYPES)

    def test_primary_techniques_are_28_and_secondary_7(self):
        assert vocab.PRIMARY_TECHNIQUES == PRIMARY_TECHNIQUES_FROM_CSV
        assert vocab.SECONDARY_TECHNIQUES == SECONDARY_TECHNIQUES_FROM_CSV

    def test_every_vocabulary_constant_is_trimmed_and_ascii(self):
        offenders = [(name, value) for name, value in _iter_vocab_strings()
                     if value != value.strip() or not value.isascii()]
        assert offenders == []
        assert any(value == "Aluminum" for _, value in _iter_vocab_strings())

    def test_normalize_vocab_value_trims_collapses_and_asciifies(self):
        assert vocab.normalize_vocab_value("  HDPE \u00a0") == "HDPE"
        assert vocab.normalize_vocab_value("Thermal Management\u2013 Assembly") == "Thermal Management- Assembly"
        assert vocab.normalize_vocab_value("Phillips\u2019s  head") == "Phillips's head"
        assert vocab.normalize_vocab_value(None) == ""

    def test_match_part_type_is_case_insensitive_and_trimmed(self):
        assert vocab.match_part_type(" threaded fasteners ") == "Threaded Fasteners"
        assert vocab.match_part_type("pcba") == "PCBA"
        assert vocab.match_part_type("Battery") is None

    def test_match_material_type_handles_trailing_space_source_values(self):
        assert vocab.match_material_type("HDPE ") == "HDPE"
        assert vocab.match_material_type("corrugated paper") == "Corrugated Paper"
        assert vocab.match_material_type("Cardboard") is None
        # `Wood pulp ` and `Flex ` carry a trailing space in the source CSVs; the constants are trimmed.
        assert "Wood pulp" in vocab.MATERIAL_TYPES and "Wood pulp " not in vocab.MATERIAL_TYPES
        assert vocab.PCB_TYPES[0] == "Flex" and vocab.match_material_type("Wood pulp ") == "Wood pulp"

    def test_technique_lookups(self):
        assert vocab.primary_technique_for_material("Corrugated Paper") == "Corrugator"
        assert vocab.primary_technique_for_material("PCBA - Assembly") == "SMT"
        assert vocab.secondary_technique_for_material("Aluminum") == "Smelting"
        assert vocab.secondary_technique_for_material("Paper") is None
        assert vocab.primary_technique_for_material("Cardboard") is None

    def test_component_specific_lists(self):
        assert vocab.PCB_TYPES == ("Flex", "Rigid")
        assert vocab.PCB_BOARD_FINISHES == ("ENIG", "OSP")
        assert vocab.DISPLAY_TYPES == ("LCD-INCELL", "LCD-OUTCELL", "LCD-TV", "LCD", "EPD", "OLED")
        assert len(vocab.BATTERY_TYPES) == 8 and vocab.BATTERY_TYPES[0] == "Battery-Alkaline"
        assert vocab.IC_TYPES == ("Logic", "DRAM", "NAND")
        assert len(vocab.IC_PACKAGE_TYPES) == 13 and "WLP CSP" in vocab.IC_PACKAGE_TYPES
        assert len(vocab.IC_PROCESS_NODE_PRIMARY) == 29
        assert vocab.IC_PROCESS_NODE_SECONDARY == ("EUV", "EUV_HPC", "HPC", "None", "1D_CoA", "2D_CuA", "2D_CoA", "3D_CuA")
        assert vocab.METHOD_FOR_WEIGHT == ("Measured", "Derived from CAD")
        assert vocab.YES_NO == ("Yes", "No")
        assert vocab.FORCE_AMOUNTS == ("light", "moderate", "firm")

    def test_bom_md_columns_are_the_reference_seven(self):
        assert vocab.BOM_MD_COLUMNS == (
            "Part Level", "Part Type", "Description", "Qty", "Material", "Mass (g)", "Manufacturing Process")

    def test_lab_summary_sections_are_1_1_to_1_9(self):
        assert [number for number, _ in vocab.LAB_SUMMARY_SECTIONS] == [f"1.{i}" for i in range(1, 10)]
        titles = dict(vocab.LAB_SUMMARY_SECTIONS)
        assert titles["1.1"] == "Background"
        assert titles["1.6"] == "Verified BOM Data Summary"
        assert titles["1.7"] == "Comparative Analysis: Supplier BOM vs Verified BOM"
        assert titles["1.9"] == "Key Observations"

    def test_lab_summary_default_sentences_verbatim(self):
        assert vocab.LAB_SUMMARY_DEFAULT_SAFETY == (
            "No safety procedures or precautions were needed for this teardown other than standardized "
            "laboratory best practices.")
        assert vocab.LAB_SUMMARY_DEFAULT_EXISTING_BOM == "No BOM was provided by the supplier for this request."
        assert vocab.LAB_SUMMARY_DEFAULT_COMPARATIVE == (
            "No BOM was provided so comparative analysis could not be performed.")

    def test_language_codes_and_config_enums(self):
        assert vocab.LANGUAGE_CODES == (
            "auto", "en-US", "en-GB", "en-AU", "de-DE", "fr-FR", "es-US", "es-ES", "it-IT", "pt-BR",
            "ja-JP", "ko-KR", "zh-CN")
        assert vocab.DEFAULT_LANGUAGE_CODE == "en-US"
        assert vocab.MODES == ("full", "transcript")
        assert vocab.VIDEO_ORDERS == ("selection", "filename")
        assert vocab.PART_LEVEL_BASES == ("0", "1")
        assert vocab.MAX_KEY_FRAMES_CEILING == 200
        assert vocab.ADDITIONAL_INSTRUCTIONS_MAX_CHARS == 4000
        assert vocab.TAG_STRING_MAX_CHARS == 256


FULL_CONFIG_EXAMPLE = {
    "mode": "full", "languageCode": "en-US", "videoOrder": "selection", "productName": "",
    "contributors": "", "maxKeyFrames": 60, "partLevelBase": "0", "generateLabSummary": True,
    "additionalInstructions": "",
}
TRANSCRIPT_CONFIG_EXAMPLE = {"mode": "transcript", "languageCode": "en-US", "videoOrder": "selection"}

_EXEC_ID = "fedcba9876543210fedcba9876543210"
_RUN_PREFIX = f"pipelines/genai-video-sop-bom/VideoSopBom_0123456789ab_20260909_120000_a1b2c3/output/{_EXEC_ID}/"
DEFINITION_EXAMPLE = {
    "schemaVersion": 1,
    "batchJobName": "VideoSopBom_0123456789ab_20260909_120000_a1b2c3",
    "pipelineExecutionId": "0123456789abcdef0123456789abcdef",
    "executionId": _EXEC_ID,
    "assetId": "x1234567-89ab-4cde-8f01-234567890abc",
    "databaseId": "smoke-db",
    "assetName": "Cognex DataMan 80 teardown",
    "inputFiles": [
        {"bucket": "vams-assets", "key": "smoke-db/x1234567-89ab-4cde-8f01-234567890abc/teardown-part1.mp4",
         "versionId": "", "relativePath": "/teardown-part1.mp4"},
        {"bucket": "vams-assets", "key": "smoke-db/x1234567-89ab-4cde-8f01-234567890abc/teardown-part2.MP4",
         "versionId": "3HL4kqtJlcpXroDTDmJ+rmSpXd3dIbrHY", "relativePath": "/teardown-part2.MP4"},
    ],
    "outputs": {
        "bucket": "vams-assets",
        "files": _RUN_PREFIX + "files/",
        "previews": _RUN_PREFIX + "previews/",
        "metadata": _RUN_PREFIX + "metadata/",
        "results": _RUN_PREFIX + "results/",
    },
    "outputTarget": {
        "assetId": "x1234567-89ab-4cde-8f01-234567890abc", "databaseId": "smoke-db",
        "fileBaseExecutionPathExtension": f"/{_EXEC_ID}/",
    },
    "auxBucket": "vams-aux",
    "auxTempPrefix": f"pipelines/genai-video-sop-bom/{_EXEC_ID}/",
    "kmsKeyArn": "",
    "bedrockModelId": "global.anthropic.claude-sonnet-5",
    "limits": {"maxVideoFiles": 4, "maxVideoFileSizeMb": 4096, "maxTotalInputSizeMb": 16384,
               "maxTotalDurationMinutes": 240, "maxKeyFramesCeiling": 200},
    "config": dict(FULL_CONFIG_EXAMPLE),
}


@pytest.mark.unit
class TestConfigAndDefinitionSchemas:
    def test_config_schema_is_a_valid_draft_2020_12_schema(self):
        schema = load_schema("config_schema")
        Draft202012Validator.check_schema(schema)
        assert schema["$id"] == "vams:videoSopBom/config_schema.json"
        assert schema["additionalProperties"] is False

    def test_config_schema_accepts_the_full_template_example(self):
        assert _errors(load_schema("config_schema"), FULL_CONFIG_EXAMPLE) == []

    def test_config_schema_accepts_the_transcript_only_example(self):
        assert _errors(load_schema("config_schema"), TRANSCRIPT_CONFIG_EXAMPLE) == []

    def test_config_schema_rejects_max_key_frames_0_and_201(self):
        schema = load_schema("config_schema")
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "maxKeyFrames": 0}) == ["0 is less than the minimum of 1"]
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "maxKeyFrames": 201}) == ["201 is greater than the maximum of 200"]
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "maxKeyFrames": 1}) == []
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "maxKeyFrames": 200}) == []

    def test_config_schema_rejects_additional_instructions_4001_chars(self):
        schema = load_schema("config_schema")
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "additionalInstructions": "x" * 4000}) == []
        errors = _errors(schema, {**FULL_CONFIG_EXAMPLE, "additionalInstructions": "x" * 4001})
        assert len(errors) == 1 and errors[0].endswith("is too long")

    def test_config_schema_rejects_unknown_keys(self):
        errors = _errors(load_schema("config_schema"), {**FULL_CONFIG_EXAMPLE, "sopDetail": "full"})
        assert errors == ["Additional properties are not allowed ('sopDetail' was unexpected)"]

    def test_config_schema_rejects_wrong_typed_fields(self):
        schema = load_schema("config_schema")
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "maxKeyFrames": "60"}) == ["'60' is not of type 'integer'"]
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "generateLabSummary": "true"}) == ["'true' is not of type 'boolean'"]
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "partLevelBase": 0}) == [
            "0 is not of type 'string'", "0 is not one of ['0', '1']"]
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "mode": "summary"}) == ["'summary' is not one of ['full', 'transcript']"]
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "languageCode": "xx-XX"}) == [
            f"'xx-XX' is not one of {list(vocab.LANGUAGE_CODES)!r}"]
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "videoOrder": "random"}) == [
            "'random' is not one of ['selection', 'filename']"]

    def test_config_schema_language_enum_equals_vocab(self):
        schema = load_schema("config_schema")
        assert tuple(schema["properties"]["languageCode"]["enum"]) == vocab.LANGUAGE_CODES
        assert tuple(schema["properties"]["videoOrder"]["enum"]) == vocab.VIDEO_ORDERS
        assert tuple(schema["properties"]["mode"]["enum"]) == vocab.MODES
        assert tuple(schema["properties"]["partLevelBase"]["enum"]) == vocab.PART_LEVEL_BASES
        assert schema["properties"]["maxKeyFrames"]["maximum"] == vocab.MAX_KEY_FRAMES_CEILING
        assert schema["properties"]["additionalInstructions"]["maxLength"] == vocab.ADDITIONAL_INSTRUCTIONS_MAX_CHARS

    def test_config_schema_string_caps_and_supported_keywords_only(self):
        schema = load_schema("config_schema")
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "productName": "p" * 256}) == []
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "productName": "p" * 257})[0].endswith("is too long")
        assert _errors(schema, {**FULL_CONFIG_EXAMPLE, "contributors": "c" * 257})[0].endswith("is too long")
        # constructPipeline validates this file with a stdlib validator that knows exactly these
        # keywords (a top-level if/then with a const inside it included); `required` is the three
        # keys both templates render.
        supported = {"type", "properties", "required", "additionalProperties", "enum", "const", "minimum",
                     "maximum", "minLength", "maxLength", "if", "then",
                     "$schema", "$id", "title", "description", "default", "examples"}
        used = set()
        for node in (schema, schema["if"], schema["then"]):
            used |= set(node) | {key for prop in node.get("properties", {}).values() for key in prop}
        assert used <= supported, used - supported
        assert schema["required"] == ["mode", "languageCode", "videoOrder"]

    def test_config_schema_requires_the_typed_full_mode_keys_only_in_full_mode(self):
        schema = load_schema("config_schema")
        assert schema["if"] == {"properties": {"mode": {"const": "full"}}, "required": ["mode"]}
        assert schema["then"] == {"required": ["maxKeyFrames", "partLevelBase", "generateLabSummary"]}
        for key in schema["then"]["required"]:
            without = {k: v for k, v in FULL_CONFIG_EXAMPLE.items() if k != key}
            assert _errors(schema, without) == [f"'{key}' is a required property"], key
        # The three free-text keys stay optional in full mode (an absent value means "").
        for key in ("productName", "contributors", "additionalInstructions"):
            assert _errors(schema, {k: v for k, v in FULL_CONFIG_EXAMPLE.items() if k != key}) == [], key
        # Transcript mode needs only the three base keys, with or without the full-mode ones.
        assert _errors(schema, TRANSCRIPT_CONFIG_EXAMPLE) == []
        assert _errors(schema, {**TRANSCRIPT_CONFIG_EXAMPLE, "maxKeyFrames": 60}) == []
        # A body without mode fails only on mode: the conditional does not fire.
        assert _errors(schema, {k: v for k, v in FULL_CONFIG_EXAMPLE.items() if k != "mode"}) == [
            "'mode' is a required property"]

    def test_definition_schema_accepts_the_registry_example(self):
        schema = load_schema("definition_schema")
        Draft202012Validator.check_schema(schema)
        assert schema["$id"] == "vams:videoSopBom/definition_schema.json"
        assert _errors(schema, DEFINITION_EXAMPLE) == []

    def test_definition_schema_requires_every_registry_key(self):
        schema = load_schema("definition_schema")
        assert tuple(schema["required"]) == tuple(DEFINITION_EXAMPLE), "the registry document has no optional key"
        for key in schema["required"]:
            without = {k: v for k, v in DEFINITION_EXAMPLE.items() if k != key}
            assert _errors(schema, without) == [f"'{key}' is a required property"], key

    def test_definition_schema_patterns_reject_folders_and_bad_prefixes(self):
        schema = load_schema("definition_schema")
        folder = {**DEFINITION_EXAMPLE, "inputFiles": [{**DEFINITION_EXAMPLE["inputFiles"][0], "relativePath": "/videos/"}]}
        errors = _errors(schema, folder)
        assert len(errors) == 1 and errors[0].startswith("'/videos/' does not match")
        for member in ("files", "previews", "metadata", "results"):
            bad = {**DEFINITION_EXAMPLE, "outputs": {**DEFINITION_EXAMPLE["outputs"], member: _RUN_PREFIX + member}}
            assert len(_errors(schema, bad)) == 1 and "does not match" in _errors(schema, bad)[0], member
        assert "does not match" in _errors(schema, {**DEFINITION_EXAMPLE, "auxTempPrefix": "pipelines/x"})[0]
        assert "does not match" in _errors(schema, {**DEFINITION_EXAMPLE, "kmsKeyArn": "not-an-arn"})[0]
        govcloud_key = "arn:aws-us-gov:kms:us-gov-west-1:123456789012:key/0f1e2d3c"
        assert _errors(schema, {**DEFINITION_EXAMPLE, "kmsKeyArn": govcloud_key}) == []

    def test_definition_schema_rejects_a_task_token_key(self):
        schema = load_schema("definition_schema")
        for token_key in ("externalSfnTaskToken", "taskToken", "TASK_TOKEN"):
            errors = _errors(schema, {**DEFINITION_EXAMPLE, token_key: "AQCE..."})
            assert errors == [f"Additional properties are not allowed ('{token_key}' was unexpected)"]

    def test_definition_schema_rejects_wrong_typed_limits_and_shapes(self):
        schema = load_schema("definition_schema")
        bad_limits = {**DEFINITION_EXAMPLE, "limits": {**DEFINITION_EXAMPLE["limits"], "maxVideoFiles": "4"}}
        assert _errors(schema, bad_limits) == ["'4' is not of type 'integer'"]
        assert _errors(schema, {**DEFINITION_EXAMPLE, "schemaVersion": 2}) == ["1 was expected"]
        assert _errors(schema, {**DEFINITION_EXAMPLE, "inputFiles": []}) == ["[] should be non-empty"]
        five = {**DEFINITION_EXAMPLE, "inputFiles": DEFINITION_EXAMPLE["inputFiles"] * 3}
        assert len(_errors(schema, five)) == 1 and _errors(schema, five)[0].endswith("is too long")
        bad_name = {**DEFINITION_EXAMPLE, "batchJobName": "VideoSopBom-0123456789ab"}
        assert len(_errors(schema, bad_name)) == 1 and "does not match" in _errors(schema, bad_name)[0]
        # openPipeline invoked without an orchestration prefix mints an empty middle segment and empty
        # ids (registry formula `VideoSopBom_{pipeline_execution_id[:12]}_…`); the document stays valid.
        no_prefix = {**DEFINITION_EXAMPLE, "batchJobName": "VideoSopBom__20260909_120000_a1b2c3",
                     "pipelineExecutionId": "", "executionId": ""}
        assert _errors(schema, no_prefix) == []

    def test_definition_example_config_validates_against_config_schema(self):
        assert _errors(load_schema("config_schema"), DEFINITION_EXAMPLE["config"]) == []
        assert load_schema("definition_schema")["properties"]["config"]["type"] == "object"


# --- end of vocab-and-schemas tests ---
