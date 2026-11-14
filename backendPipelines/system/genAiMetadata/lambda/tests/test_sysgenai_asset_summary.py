#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The SYSTEM GenAI asset summary workflow: its bundle, its entry Lambda (whole-asset and folder selections only),
its openPipeline instance, and the summary step that describes an asset from its files' genai_* metadata.

The input envelope is built with the backend's own envelope builders, with the per-file records a whole-asset or
folder selection is expanded into (metadataInputs.assetFileMetadata) and the asset's schema entry."""

import datetime
import json
import os
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h

er = h.load_backend_execution_records()
kr = h.load_local("keyReuse")
cv = h.load_local("classificationVocabulary")

BUNDLE_DIR = os.path.join(h.VAMS_SCHEMA_DIR, "assetSummary")
TEMPLATE_PATH = os.path.join(BUNDLE_DIR, "templates", "system-genai-asset-summary-default.json")
pipeline = h.read_json_file(os.path.join(BUNDLE_DIR, "pipeline.json"))
workflow = h.read_json_file(os.path.join(BUNDLE_DIR, "workflow.json"))
template = h.read_json_file(TEMPLATE_PATH)

MANIFEST_KEY = "pipelines/workflowExecutionInputs/E2/pipeline1/manifest.json"
MANIFEST_URI = f"s3://abkt/{MANIFEST_KEY}"
CONFIG_KEY = "pipelines/workflowExecutionInputs/E2/pipeline1/config.json"
METADATA_KEY = "pipelines/workflowExecutionInputs/E2/metadata.json"
META_PREFIX = "pipelines/sgas/sgas/output/E2/metadata/"
RESULTS_PREFIX = "pipelines/sgas/sgas/output/E2/results/"
SUMMARY_KEY = RESULTS_PREFIX + "analysis-summary.json"
STATUS_KEY = RESULTS_PREFIX + "execution.status.json"
UNDESCRIBED_KEY = RESULTS_PREFIX + "files-without-description.json"
ASSET_METADATA_KEY = META_PREFIX + "asset.metadata.json"
TEXT_MODEL = "us.amazon.nova-pro-v1:0"
GUARDRAIL_ENV = {"BEDROCK_GUARDRAIL_IDENTIFIER": "gr-test", "BEDROCK_GUARDRAIL_VERSION": "3"}

SUMMARY_REPLY = {
    "title": "Gear pump assembly", "description": "A gear pump scene: housing, gears and an inlet flange.",
    "keywords": ["pump", "gear pump", "assembly"], "category": "Industrial Equipment", "subcategory": "Pump",
    "style": "technical/CAD", "materials": ["metal"], "colors": ["gray"],
}


def _file(key, title="", description="", keywords="", category="", loads=None, expanded=True):
    metadata = {}
    for name, value in (("genai_title", title), ("genai_description", description),
                        ("genai_keywords", keywords), ("genai_category", category)):
        if value:
            metadata[name] = value
    attributes = {}
    if loads is not None:
        attributes["sys_dependencies"] = json.dumps({
            "scope": "asset", "resolved": len(loads), "versions": {rel: f"v-{rel}" for rel in loads}})
    return er.build_metadata_file_record(key, metadata=metadata, attributes=attributes, expanded=expanded)


SCENE_FILES = [
    _file("/parts/gear.usda", "Gear", "A spur gear with twenty teeth.", "gear, spur gear", "Industrial Equipment"),
    _file("/parts/housing.usda", "Pump housing", "A cast pump housing with an inlet flange.", "housing, flange",
          "Industrial Equipment"),
    _file("/scene.usda", "Gear pump scene", "A scene composing the pump housing and two gears.", "pump, scene",
          "Industrial Equipment", loads=["parts/gear.usda", "parts/housing.usda"]),
    _file("/docs/notes.txt"),
    _file("/textures/steel.png", "Steel texture", "A brushed steel texture.", "steel, texture", "Media"),
]


def _envelope(files=None, omitted=0, exact=True, asset_metadata=None, schemas=None, selections=("/",)):
    files = SCENE_FILES if files is None else files
    records = [er.build_metadata_file_record("/", metadata=dict(asset_metadata or {"PROJECT": "Alpha"}))]
    records += [dict(record) for record in files]
    expansion = er.build_metadata_file_expansion(list(selections), len(files), omitted_file_count=omitted,
                                                 omitted_file_count_exact=exact)
    group = er.build_metadata_asset_group(
        "dbM", "xidM", asset_data={"assetName": "Gear Pump", "description": "A pump", "tags": ["pump"]},
        files=records, file_expansion=expansion)
    return er.build_grouped_metadata_envelope([group], metadata_schemas=schemas)


def _rendered_config(**over):
    body = template["configBody"]
    for tag in template["tagSchema"]:
        value = tag["default"]
        body = body.replace("{{" + tag["tagKey"] + "}}", json.dumps(value) if tag["type"] != "string" else value)
    config = json.loads(body)
    config.update(over)
    return config


def _state(relative_path="/", **over):
    state = {
        "jobName": "PipelineJob_20261104_101010_123_abcdef01", "externalSfnTaskToken": h.TASK_TOKEN,
        "inputS3AssetFilePath": "s3://abkt/xidM/" + relative_path.lstrip("/"),
        "outputS3AssetFilesPath": "s3://abkt/pipelines/sgas/sgas/output/E2/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/sgas/sgas/output/E2/previews/",
        "outputS3AssetMetadataPath": f"s3://abkt/{META_PREFIX}",
        "outputS3AssetResultsPath": f"s3://abkt/{RESULTS_PREFIX}",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/system-genai-asset-summary/E2/",
        "inputMetadataS3Location": f"s3://abkt/{METADATA_KEY}",
        "inputConfigurationS3Location": f"s3://abkt/{CONFIG_KEY}",
        "assetId": "xidM", "databaseId": "dbM", "bucketId": "bkt-01", "relativePath": relative_path,
        "versionId": "", "workflowExecutionId": "E2", "orchestrationEventPrefix": "vams.prod.execution.E2.pipeline.P1",
        "assetRootS3Key": "xidM/",
    }
    state.update(over)
    return state


def _summarize(envelope=None, config=None, script=None, env=None, relative_path="/", guardrail_script=None):
    s3 = h.FakeS3()
    s3.put_json("abkt", CONFIG_KEY, config if config is not None else _rendered_config())
    s3.put_json("abkt", METADATA_KEY, envelope if envelope is not None else _envelope())
    mod = h.load_handler("summarizeAsset", dict({"BEDROCK_TEXT_MODEL_ID": TEXT_MODEL}, **(env or {})))
    mod.s3_client = s3
    bedrock = h.FakeBedrock(script or [h.converse_response(json.dumps(SUMMARY_REPLY))], guardrail_script)
    mod.bedrock_runtime = bedrock
    mod.gm.time = MagicMock()
    state = mod.lambda_handler(_state(relative_path), MagicMock())
    return mod, s3, bedrock, state


def _rows(s3):
    if ("abkt", ASSET_METADATA_KEY) not in s3.objects:
        return None
    return {row["metadataKey"]: (row["metadataValue"], row["metadataValueType"])
            for row in s3.json_at("abkt", ASSET_METADATA_KEY)["metadata"]}


def _prompt(bedrock):
    return json.dumps(bedrock.calls[0]["messages"], default=str)


@pytest.mark.unit
class TestBundle:
    def test_the_records_are_system_records_of_the_genai_category(self):
        for record, name in ((pipeline, "pipelineName"), (workflow, "workflowName")):
            assert record["isSystem"] is True
            assert record["category"] == "SYSTEM - GenAI"
            assert record[name] == "SYSTEM - GenAI Asset Summary"
        assert "pipelineId" not in pipeline and "workflowId" not in workflow  # the CDK idOverrides supply them

    def test_it_takes_a_whole_asset_or_a_folder_of_one_asset(self):
        scope = {"crossAssetAllowed": False, "singleAssetOnly": True, "wholeAssetAllowed": True,
                 "folderAllowed": True}
        assert pipeline["systemConfig"]["assetScope"] == scope
        assert workflow["systemConfig"]["assetScope"] == scope
        assert pipeline["systemConfig"]["inputFileArity"] == workflow["systemConfig"]["inputFileArity"] == "one"

    def test_both_gates_ask_for_the_file_records_and_the_schema(self):
        expected = {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True, "databaseMetadata": False,
                    "assetFileMetadata": True, "metadataSchema": True}
        assert pipeline["systemConfig"]["metadataInputs"] == expected
        assert workflow["systemConfig"]["metadataInputs"] == expected

    def test_one_summary_per_asset_at_a_time_and_no_trigger(self):
        assert workflow["systemConfig"]["concurrencyRestriction"] == "perAsset"
        assert workflow["systemConfig"]["outputTarget"] == {"locationType": "asset", "allowOverride": False}
        assert workflow["systemConfig"]["allowWorkflowTriggerChaining"] is False
        assert "triggers" not in workflow

    def test_the_execution_waits_on_the_callback_within_the_state_machine_bound(self):
        assert pipeline["executionConfig"] == {"executionType": "Lambda", "waitForCallback": "Enabled",
                                               "taskTimeout": "3600", "taskHeartbeatTimeout": "", "lambda": {}}

    def test_the_layout_and_the_one_default_template(self):
        assert sorted(os.listdir(BUNDLE_DIR)) == ["pipeline.json", "templates", "workflow.json"]
        assert os.listdir(os.path.dirname(TEMPLATE_PATH)) == ["system-genai-asset-summary-default.json"]
        assert template["templateId"] == "system-genai-asset-summary-default"
        assert template["isDefault"] is True and template["configFormat"] == "json"

    def test_every_tag_is_referenced_and_the_defaults_render_the_configuration_the_step_reads(self):
        for tag in template["tagSchema"]:
            assert "{{" + tag["tagKey"] + "}}" in template["configBody"], tag["tagKey"]
        config = _rendered_config()
        assert set(config) == {"seedWithExistingMetadata", "reuseExistingKeys", "fieldMapping",
                               "classificationVocabulary"}
        assert config["fieldMapping"] == kr.FIELD_MAPPING_DEFAULT
        assert cv.normalize_vocabulary(config["classificationVocabulary"]) == cv.normalize_vocabulary(
            cv.DEFAULT_VOCABULARY)
        for key in config:
            assert key in template["inputInstructions"] or key in ("seedWithExistingMetadata",), key

    def test_the_template_names_no_model(self):
        assert h.load_local("analysisCommon").model_key_paths(_rendered_config()) == []


@pytest.mark.unit
class TestSummary:
    def test_scenes_come_first_and_their_parts_are_marked(self):
        _mod, _s3, bedrock, _state = _summarize()
        prompt = _prompt(bedrock)
        order = [prompt.index(key) for key in ("/scene.usda", "/parts/gear.usda", "/parts/housing.usda",
                                                "/textures/steel.png")]
        assert order == sorted(order)
        assert "/scene.usda (scene, loads 2 files)" in prompt
        assert "/parts/gear.usda (part of /scene.usda)" in prompt

    def test_the_text_model_answers_with_the_summary_system_prompt(self):
        mod, _s3, bedrock, _state = _summarize()
        call = bedrock.calls[0]
        assert call["modelId"] == TEXT_MODEL
        assert call["system"][0]["text"] == mod.SYSTEM_PROMPT
        assert not any("image" in block for block in call["messages"][0]["content"])

    def test_without_a_text_model_the_analysis_model_answers(self):
        _mod, _s3, bedrock, _state = _summarize(env={"BEDROCK_TEXT_MODEL_ID": ""})
        assert bedrock.calls[0]["modelId"] == h.DEFAULT_ENV["BEDROCK_ANALYSIS_MODEL_ID"]

    def test_the_asset_metadata_file_carries_the_summary_and_its_provenance(self):
        _mod, s3, _bedrock, state = _summarize()
        assert state["analysisStatus"] == "SUCCEEDED"
        rows = _rows(s3)
        assert rows["genai_asset_title"] == ("Gear pump assembly", "string")
        assert rows["genai_asset_description"][1] == "multiline_string"
        assert rows["genai_asset_source"] == ("aggregate:/", "string")
        assert rows["genai_asset_source_version"] == ("", "string")
        assert rows["genai_asset_file_count"] == ("4", "number")
        assert rows["genai_asset_generated_at"][1] == "date"
        assert s3.json_at("abkt", ASSET_METADATA_KEY)["updateType"] == "update"

    def test_files_without_a_description_are_listed_in_the_results(self):
        _mod, s3, _bedrock, _state = _summarize()
        report = s3.json_at("abkt", UNDESCRIBED_KEY)
        assert report["files"] == ["/docs/notes.txt"] and report["count"] == 1
        assert report["selectionCapped"] is False
        summary = s3.json_at("abkt", SUMMARY_KEY)
        assert (summary["describedCount"], summary["undescribedCount"], summary["fileCount"]) == (4, 1, 5)
        assert summary["undescribedFile"].endswith(UNDESCRIBED_KEY)

    def test_a_capped_selection_is_said_in_the_prompt_the_summary_and_the_results(self):
        _mod, s3, bedrock, _state = _summarize(_envelope(omitted=37, exact=False))
        assert "The selection has at least 37 more file(s) that are not listed here." in _prompt(bedrock)
        summary = s3.json_at("abkt", SUMMARY_KEY)
        assert summary["fileExpansion"] == {"expandedFileCount": 5, "omittedFileCount": 37,
                                            "omittedFileCountExact": False}
        assert any("at least 37 more file(s)" in warning for warning in summary["warnings"])
        report = s3.json_at("abkt", UNDESCRIBED_KEY)
        assert report["selectionCapped"] is True and report["omittedFileCount"] == 37

    def test_a_folder_selection_summarises_the_files_under_it(self):
        _mod, s3, bedrock, _state = _summarize(relative_path="/parts/")
        prompt = _prompt(bedrock)
        assert "/parts/gear.usda" in prompt and "/scene.usda" not in prompt and "steel.png" not in prompt
        assert "Summarise: the folder /parts/" in prompt
        assert _rows(s3)["genai_asset_source"] == ("aggregate:/parts/", "string")

    def test_the_file_lines_and_the_asset_data_are_guarded(self):
        _mod, _s3, bedrock, _state = _summarize(env=GUARDRAIL_ENV)
        content = bedrock.calls[0]["messages"][0]["content"]
        assert "guardContent" in content[1]
        guarded = content[1]["guardContent"]["text"]["text"]
        assert "A spur gear with twenty teeth." in guarded and "Asset name: Gear Pump" in guarded
        assert "spur gear" not in content[0]["text"]
        assert bedrock.calls[0]["guardrailConfig"]["guardrailIdentifier"] == "gr-test"

    def test_a_long_file_list_is_cut_to_the_budget_and_counted(self):
        files = [_file(f"/f{i:04d}.glb", f"Part {i}", "x" * 400) for i in range(150)]
        mod, s3, bedrock, _state = _summarize(_envelope(files))
        summary = s3.json_at("abkt", SUMMARY_KEY)
        assert 0 < summary["filesListed"] < 150
        assert f"{150 - summary['filesListed']} more described file(s) are not listed here." in _prompt(bedrock)
        assert len(_prompt(bedrock)) < mod.FILE_SECTION_MAX_CHARS * 2

    def test_no_described_file_fails_without_a_model_call(self):
        _mod, s3, bedrock, state = _summarize(_envelope([_file("/a.glb"), _file("/b.glb")]))
        assert bedrock.calls == [] and state["analysisStatus"] == "FAILED"
        assert s3.json_at("abkt", STATUS_KEY)["error"] == "AssetSummaryNoFileDescriptions"
        assert _rows(s3) is None
        assert s3.json_at("abkt", UNDESCRIBED_KEY)["count"] == 2

    def test_a_bedrock_failure_is_recorded_and_the_step_returns(self):
        error = h.client_error("AccessDeniedException", "no access")
        _mod, s3, _bedrock, state = _summarize(script=[error])
        assert state["analysisStatus"] == "FAILED"
        assert s3.json_at("abkt", STATUS_KEY)["error"] == "BedrockAccessDenied"
        assert _rows(s3) is None

    def test_an_envelope_without_expanded_records_is_said(self):
        envelope = _envelope()
        envelope["assets"][0].pop("fileExpansion")
        _mod, s3, _bedrock, _state = _summarize(envelope)
        assert any("does not ask for assetFileMetadata" in w for w in s3.json_at("abkt", SUMMARY_KEY)["warnings"])

    def test_the_asset_schema_constrains_and_types_the_summary(self):
        schema = er.build_metadata_schema_entry(
            "dbM", asset_metadata=[er.build_metadata_schema_field(
                "Category", {"metadataFieldValueType": "inline_controlled_list",
                             "controlledListKeys": ["Industrial Equipment", "Vehicle"]})])
        envelope = _envelope(asset_metadata={"PROJECT": "Alpha", "Category": ""}, schemas=[schema])
        _mod, s3, bedrock, _state = _summarize(envelope, script=[h.converse_tool_response(dict(SUMMARY_REPLY))])
        properties = bedrock.calls[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["properties"]
        assert "enum" not in properties["category"]
        assert ('lists these values for this field: "Industrial Equipment", "Vehicle".'
                in properties["category"]["description"])
        assert "when none fits, give the value that does" in _prompt(bedrock)
        assert set(properties) == {"title", "description", "keywords", "category", "subcategory", "style",
                                   "materials", "colors"}
        rows = _rows(s3)
        assert rows["Category"] == ("Industrial Equipment", "inline_controlled_list")
        assert "genai_asset_category" not in rows
        assert s3.json_at("abkt", SUMMARY_KEY)["replyFormat"] == "tool"

    @pytest.mark.parametrize("tool", [True, False])
    def test_a_placeholder_word_in_the_answer_writes_no_row(self, tool):
        """The summary reads its answer through the same normalisation as the per-file analysis."""
        reply = dict(SUMMARY_REPLY, style="null", subcategory=" None ", colors=["n/a"], keywords=["pump", "null"])
        response = h.converse_tool_response(reply) if tool else h.converse_response(json.dumps(reply))
        envelope = _envelope(schemas=[er.build_metadata_schema_entry("dbM")]) if tool else _envelope()
        _mod, s3, _bedrock, state = _summarize(envelope, script=[response])
        assert state["analysisStatus"] == "SUCCEEDED"
        rows = _rows(s3)
        assert not {"genai_asset_style", "genai_asset_subcategory", "genai_asset_colors"} & set(rows)
        assert rows["genai_asset_keywords"][0] == "pump"

    def test_the_assessment_line_carries_the_output_screen_of_the_tool_answer(self):
        """The summary's request is screened and logged like the per-file analysis."""
        masked = json.dumps(dict(SUMMARY_REPLY, title="Pump line of {NAME}"))
        guardrail = [h.apply_guardrail_response(outputs=[masked], assessments=[h.pii_assessment("NAME")])]
        envelope = _envelope(schemas=[er.build_metadata_schema_entry("dbM")])
        mod, s3, _bedrock, state = _summarize(envelope, script=[h.converse_tool_response(dict(SUMMARY_REPLY))],
                                              env=GUARDRAIL_ENV, guardrail_script=guardrail)
        assert state["analysisStatus"] == "SUCCEEDED"
        lines = [call.args[0] for call in mod.gm.logger.info.call_args_list
                 if str(call.args[0]).startswith("Guardrail assessment: ")]
        assert [json.loads(line[len("Guardrail assessment: "):]) for line in lines] == [
            {"output": [{"policy": "sensitiveInformationPolicy", "type": "NAME", "action": "ANONYMIZED"}]}]
        assert "Jane" not in lines[0]
        assert _rows(s3)["genai_asset_title"][0] == "Pump line of {NAME}"

    def test_a_blocked_tool_answers_cause_names_the_output_side(self):
        """The summary's tool answer is screened as OUTPUT like the per-file analysis's, and a block's cause
        lists the filters under the output side."""
        guardrail = [h.apply_guardrail_response(outputs=["Blocked by the guardrail."],
                                                assessments=[h.prompt_attack_assessment()])]
        envelope = _envelope(schemas=[er.build_metadata_schema_entry("dbM")])
        _mod, s3, bedrock, state = _summarize(envelope, script=[h.converse_tool_response(dict(SUMMARY_REPLY))],
                                              env=GUARDRAIL_ENV, guardrail_script=guardrail)
        assert state["analysisStatus"] == "FAILED"
        assert [call["source"] for call in bedrock.guardrail_calls] == ["OUTPUT"]
        status = s3.json_at("abkt", STATUS_KEY)
        assert status["error"] == "BedrockGuardrailIntervened"
        prefix = "guardrail intervened: Blocked by the guardrail. "
        assert status["cause"].startswith(prefix)
        assert json.loads(status["cause"][len(prefix):]) == {"output": [
            {"policy": "contentPolicy", "type": "PROMPT_ATTACK", "action": "BLOCKED", "confidence": "HIGH"}]}
        assert _rows(s3) is None

    def test_a_model_error_in_the_tool_call_is_asked_again_in_prompt_json(self):
        """The summary's one request falls back like the per-file analysis."""
        error = h.client_error("ModelErrorException", "Model produced invalid sequence as part of ToolUse.")
        envelope = _envelope(schemas=[er.build_metadata_schema_entry("dbM")])
        _mod, s3, bedrock, state = _summarize(envelope, script=[error, h.converse_response(json.dumps(SUMMARY_REPLY))])
        assert state["analysisStatus"] == "SUCCEEDED"
        assert [("toolConfig" in call) for call in bedrock.calls] == [True, False]
        assert s3.json_at("abkt", SUMMARY_KEY)["replyFormat"] == "text"

    def test_without_the_schema_the_summary_says_why_and_asks_in_prompt_json(self):
        _mod, s3, bedrock, _state = _summarize()
        assert "toolConfig" not in bedrock.calls[0]
        warnings = s3.json_at("abkt", SUMMARY_KEY)["warnings"]
        assert any("No metadata schema of database dbM" in w for w in warnings)

    def test_a_rerun_keeps_a_person_edited_key_and_reuses_its_own(self):
        own = "Old summary"
        digests = kr.encode_digest_record({"Title": kr.value_digest(own)})
        envelope = _envelope(asset_metadata={"Title": own, "Description": "Written by a person",
                                             kr.ASSET_DIGEST_KEY: digests, "genai_asset_title": "stale"},
                             schemas=[er.build_metadata_schema_entry("dbM")])
        _mod, s3, bedrock, _state = _summarize(envelope)
        rows = _rows(s3)
        assert rows["Title"] == ("Gear pump assembly", "string")
        assert rows["genai_asset_title"] == ("", "string")  # moved to the reused key, so cleared
        assert "Description" not in rows and rows["genai_asset_description"][0].startswith("A gear pump")
        assert "Title: Old summary" in _prompt(bedrock)  # the asset's existing metadata seeds the prompt

    @pytest.mark.parametrize("listed, expected", [
        (("Industrial Equipment", "Vehicle"), {"Category": ("Industrial Equipment", "inline_controlled_list")}),
        (("Pump", "Valve"), {"Category": ("", "inline_controlled_list"),
                             "genai_asset_category": ("Industrial Equipment", "string")}),
    ])
    def test_a_rerun_types_the_key_an_earlier_summary_filled_from_the_schema(self, listed, expected):
        """The asset's Category was filled from its controlled list by an earlier run; this run writes it in
        the list's type, or, when the answer is outside the list, empties it and keeps the answer in the
        pipeline's key."""
        schema = er.build_metadata_schema_entry(
            "dbM", asset_metadata=[er.build_metadata_schema_field(
                "Category", {"metadataFieldValueType": "inline_controlled_list", "controlledListKeys": list(listed)})])
        earlier = listed[-1]
        envelope = _envelope(asset_metadata={"Category": earlier, kr.ASSET_DIGEST_KEY: kr.encode_digest_record(
            {"Category": kr.value_digest(earlier)})}, schemas=[schema])
        _mod, s3, _bedrock, _state = _summarize(envelope, script=[h.converse_tool_response(dict(SUMMARY_REPLY))])
        rows = _rows(s3)
        assert {key: rows.get(key) for key in expected} == expected


# The file attribute the per-file run records its targets in (keyReuse.FILE_TARGETS_KEY, pinned below), and the
# keys it wrote each field to under key reuse with the shipped field mapping, when the file carried schema keys of
# those names (and a tags key for the keywords).
TARGETS_KEY = "genai_targets"
MAPPED_KEYS = {"title": "Title", "description": "Description", "keywords": "tags", "category": "Category"}


def _mapped_file(key, title="", description="", keywords="", category="", keys=None):
    """A file record as key reuse leaves it: each value in the key the per-file run wrote it to, the genai_* key
    cleared, and the file's targets record naming the keys."""
    keys = dict(MAPPED_KEYS, **(keys or {}))
    metadata = {"genai_title": "", "genai_description": "", "genai_keywords": "", "genai_category": ""}
    for field, value in (("title", title), ("description", description), ("keywords", keywords),
                         ("category", category)):
        metadata[keys[field]] = value
    attributes = {TARGETS_KEY: kr.encode_digest_record(keys),
                  kr.FILE_DIGEST_KEY: kr.encode_digest_record({keys[f]: kr.value_digest(v) for f, v in (
                      ("title", title), ("description", description), ("keywords", keywords),
                      ("category", category)) if v})}
    return er.build_metadata_file_record(key, metadata=metadata, attributes=attributes, expanded=True)


MAPPED_FILES = [
    _mapped_file("/pump.glb", "Pump housing", "A cast pump housing with flanges.", "pump, housing",
                 "Industrial Equipment"),
    _mapped_file("/valve.glb", "Gate valve", "A flanged gate valve.", "valve, flange", "Industrial Equipment"),
]


@pytest.mark.unit
class TestMappedFileKeys:
    """With key reuse the per-file run may write a file's description into a key the file already carries
    (a schema ``Description``, a ``tags`` key) and clear its genai_* key; the summary reads it there."""

    def test_the_summary_reads_the_record_the_per_file_run_writes(self):
        assert kr.FILE_TARGETS_KEY == TARGETS_KEY

    def test_a_file_whose_description_sits_in_a_mapped_key_is_described(self):
        files = [MAPPED_FILES[0], _file("/docs/notes.txt")]
        _mod, s3, bedrock, state = _summarize(_envelope(files))
        assert state["analysisStatus"] == "SUCCEEDED"
        summary = s3.json_at("abkt", SUMMARY_KEY)
        assert (summary["describedCount"], summary["undescribedCount"]) == (1, 1)
        assert "A cast pump housing with flanges." in _prompt(bedrock)
        assert s3.json_at("abkt", UNDESCRIBED_KEY)["files"] == ["/docs/notes.txt"]

    def test_a_summary_of_only_such_files_succeeds(self):
        _mod, s3, bedrock, state = _summarize(_envelope(MAPPED_FILES))
        assert state["analysisStatus"] == "SUCCEEDED" and len(bedrock.calls) == 1
        assert ("abkt", STATUS_KEY) not in s3.objects
        assert _rows(s3)["genai_asset_file_count"] == ("2", "number")

    def test_the_title_keywords_and_category_in_mapped_keys_are_in_the_file_line(self):
        mod = h.load_handler("summarizeAsset")
        group = {"files": [MAPPED_FILES[0]]}
        [entry] = mod.ordered_entries(mod.file_entries(group, "/"))
        assert mod.file_line(entry) == ("- /pump.glb: Pump housing | A cast pump housing with flanges. | "
                                        "keywords: pump, housing | category: Industrial Equipment")

    def test_a_value_a_person_edited_in_the_mapped_key_is_read_as_it_is_now(self):
        edited = _mapped_file("/pump.glb", "Pump housing", "A cast pump housing with flanges.")
        edited["metadata"]["Description"] = "Pump housing, reviewed by QA."  # no longer matches its digest
        _mod, _s3, bedrock, _state = _summarize(_envelope([edited]))
        assert "Pump housing, reviewed by QA." in _prompt(bedrock)

    def test_the_genai_key_comes_first_and_an_unnamed_key_is_not_read(self):
        mod = h.load_handler("summarizeAsset")
        # A later run without key reuse wrote genai_description again; the targets record still names the key.
        stale = _mapped_file("/a.glb", description="Earlier value in the reused key.")
        stale["metadata"]["genai_description"] = "Current analysis."
        # A key the targets record does not name is the file's own, whatever its name.
        own = _file("/b.glb")
        own["metadata"]["Description"] = "A person's own description."
        entries = {e["key"]: e for e in mod.file_entries({"files": [stale, own, SCENE_FILES[0]]}, "/")}
        assert entries["/a.glb"]["description"] == "Current analysis."
        assert entries["/b.glb"]["description"] == ""
        assert entries["/parts/gear.usda"]["description"] == "A spur gear with twenty teeth."

    def test_an_unreadable_targets_record_is_ignored(self):
        mod = h.load_handler("summarizeAsset")
        record = _mapped_file("/a.glb", description="In the reused key.")
        record["attributes"][TARGETS_KEY] = "not json"
        assert mod.file_entries({"files": [record]}, "/")[0]["description"] == ""


def _manifest(relative_path="/"):
    return {
        "schemaVersion": 1,
        "inputFiles": [{"relativePath": relative_path, "databaseId": "dbM", "assetId": "xidM",
                        "assetRootS3Key": "xidM/", "auxPreviewPrefix": "dbM/xidM/preview", "bucket": "abkt",
                        "bucketId": "bkt-01", "key": "xidM/" + relative_path.lstrip("/"), "versionId": ""}],
        "inputMetadataS3Location": f"s3://abkt/{METADATA_KEY}",
        "outputs": {"bucket": "abkt", "files": "pipelines/sgas/sgas/output/E2/files/",
                    "previews": "pipelines/sgas/sgas/output/E2/previews/", "metadata": META_PREFIX,
                    "results": RESULTS_PREFIX},
        "auxBucket": "aux", "auxTempPrefix": "pipelines/system-genai-asset-summary/E2/",
        "systemConfig": {"orchestrationEventPrefix": "vams.prod.execution.E2.pipeline.P1"},
    }


def _execute(manifest, config=None):
    mod = h.load_handler("vamsExecuteSystemGenAiAssetSummary")
    s3 = h.FakeS3()
    s3.put_json("abkt", MANIFEST_KEY, manifest)
    s3.put_json("abkt", CONFIG_KEY, config if config is not None else _rendered_config())
    mod.s3_client = s3
    mod.sfn_client = MagicMock()
    mod.lambda_client = MagicMock()
    mod.lambda_client.invoke = MagicMock(return_value={"StatusCode": 200})
    body = {"TaskToken": h.TASK_TOKEN, "workflowExecutionId": "E2", "inputManifestS3Location": MANIFEST_URI,
            "inputConfigurationS3Location": f"s3://abkt/{CONFIG_KEY}", "executingUserName": "user@x"}
    return mod, mod.lambda_handler({"body": json.dumps(body)}, MagicMock())


@pytest.mark.unit
class TestEntry:
    @pytest.mark.parametrize("selection", ["/", "/parts/"])
    def test_a_whole_asset_or_folder_selection_starts_the_summary(self, selection):
        mod, response = _execute(_manifest(selection))
        assert response["statusCode"] == 200
        payload = json.loads(mod.lambda_client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["relativePath"] == selection
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/" + selection.lstrip("/")
        assert payload["outputS3AssetMetadataPath"] == f"s3://abkt/{META_PREFIX}"
        assert payload["assetRootS3Key"] == "xidM/"
        assert mod.sfn_client.send_task_failure.call_count == 0

    def test_a_single_file_is_refused_and_fails_the_token(self):
        mod, response = _execute(_manifest("/scene.usda"))
        assert response["statusCode"] == 400
        assert "whole asset or a folder" in json.loads(response["body"])["message"]
        assert mod.lambda_client.invoke.call_count == 0
        assert mod.sfn_client.send_task_failure.call_args.kwargs["error"] == "SystemGenAiAssetSummaryError"

    def test_a_template_model_key_is_refused(self):
        mod, response = _execute(_manifest("/"), dict(_rendered_config(), textModelId="x"))
        assert response["statusCode"] == 400 and mod.lambda_client.invoke.call_count == 0

    def test_a_vocabulary_category_named_like_a_model_key_starts(self):
        """The vocabulary's category names are data, not configuration keys."""
        config = _rendered_config()
        config["classificationVocabulary"] = {"categories": {"Product Model ID": {
            "description": "Product model identifiers", "subcategories": []}}}
        mod, response = _execute(_manifest("/"), config)
        assert response["statusCode"] == 200 and mod.lambda_client.invoke.call_count == 1

    def test_a_manifest_without_a_results_prefix_fails_the_token(self):
        manifest = _manifest("/")
        manifest["outputs"].pop("results")
        mod, response = _execute(manifest)
        assert response["statusCode"] == 500 and mod.sfn_client.send_task_failure.call_count == 1


def _open(env, path):
    mod = h.load_handler("openPipeline", env)
    mod.sfn = MagicMock()
    mod.sfn.start_execution = MagicMock(return_value={"executionArn": "arn:aws:states:us-east-1:1:execution:S:x",
                                                      "startDate": datetime.datetime(2026, 1, 1)})
    mod.events_client = MagicMock()
    response = mod.lambda_handler({"inputS3AssetFilePath": path, "workflowExecutionId": "E2",
                                   "orchestrationEventPrefix": "vams.prod.execution.E2.pipeline.P1",
                                   "sfnExternalTaskToken": h.TASK_TOKEN, "relativePath": "/"}, MagicMock())
    return mod, response


SUMMARY_OPEN_ENV = {
    "ALLOWED_INPUT_FILEEXTENSIONS": "*", "ALLOW_FOLDER_INPUT": "true",
    "SUB_EXECUTION_LABEL": "GenAI asset summary", "STATE_MACHINE_LOG_LABEL": "GenAI asset summary state machine",
    "AWS_LAMBDA_FUNCTION_NAME": "vams-core-sbx-SystemGenAiAssetSummaryOpenPipel-Ab1",
    "ANALYSIS_FUNCTION_NAMES": json.dumps({"SummarizeAssetTask": "vams-core-sbx-SummarizeAsset-Cd2",
                                           "PipelineEndTask": "vams-core-sbx-AssetSummaryPipelineEnd-Ef3"}),
}


@pytest.mark.unit
class TestOpenPipelineInstance:
    def test_the_summary_instance_starts_on_a_folder_input(self):
        mod, response = _open(SUMMARY_OPEN_ENV, "s3://abkt/xidM/parts/")
        assert response["statusCode"] == 200 and mod.sfn.start_execution.call_count == 1

    def test_the_per_file_instance_still_refuses_a_folder(self):
        mod, response = _open({"ALLOWED_INPUT_FILEEXTENSIONS": "*"}, "s3://abkt/xidM/parts/")
        assert response["statusCode"] == 400 and mod.sfn.start_execution.call_count == 0

    def test_the_summary_states_and_labels_are_registered(self):
        mod, _response = _open(SUMMARY_OPEN_ENV, "s3://abkt/xidM/")
        detail = json.loads(mod.events_client.put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert detail["subExecution"]["label"] == "GenAI asset summary"
        stages = [log.get("stageName") for log in detail["logs"] if log["sourceType"] == "lambda"]
        assert stages == [None, "PipelineEndTask", "SummarizeAssetTask"]
        assert [log["label"] for log in detail["logs"] if log["sourceType"] == "stateMachine"] == [
            "GenAI asset summary state machine"]


# A model with extended thinking (Claude Sonnet 5.5 among them) opens its answer with a reasoning block
# before the tool use or the text. Converse returns it as ``reasoningContent``; the native Messages shape is
# a ``thinking`` block. Neither carries a top-level ``text`` key, and the reply is read from the blocks after
# it, so the answer is never lost to the first block.
_REASONING_BLOCKS = {
    "converse-reasoningContent": {"reasoningContent": {"reasoningText": {
        "text": "The views show a gear housing; the answer follows.", "signature": "sig-1"}}},
    "native-thinking": {"type": "thinking", "thinking": "The views show a gear housing.", "signature": "sig-1"},
}


def _reasoning_first(response, block):
    content = response["output"]["message"]["content"]
    response["output"]["message"]["content"] = [dict(block)] + content
    return response


@pytest.mark.unit
class TestReasoningBlockBeforeTheSummary:
    @pytest.mark.parametrize("shape", sorted(_REASONING_BLOCKS))
    @pytest.mark.parametrize("tool", [True, False])
    def test_the_asset_summary_reads_the_answer_after_a_reasoning_block(self, tool, shape):
        response = (h.converse_tool_response(dict(SUMMARY_REPLY)) if tool
                    else h.converse_response(json.dumps(SUMMARY_REPLY)))
        _mod, s3, _bedrock, state = _summarize(script=[_reasoning_first(response, _REASONING_BLOCKS[shape])])
        assert state["analysisStatus"] == "SUCCEEDED"
        assert _rows(s3)["genai_asset_title"] == ("Gear pump assembly", "string")
