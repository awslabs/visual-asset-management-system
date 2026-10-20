# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workload profiles: a template's `profile` block replaces stage prompts (inputs placed as `[[TOKEN]]`),
appends to the fixed system boundary, lowers stage caps and names artifacts written from the finalize
output; RUN_VISUAL_VERIFICATION skips the frames and vision stages. The shipped profile templates are run
end to end through the same fakes as the built-in template, and the built-in path is pinned unchanged.

Run from the container directory:  python -m pytest tests/test_video_sop_bom_profiles.py -q
"""

import copy
import hashlib
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from conftest import FakeBedrock, bare_response, make_clients, make_definition, tool_use_response  # noqa: E402
from test_video_sop_bom_bedrock import CONFIG, MODEL, _component, _finalize_payload, _step, _window, _window_payload  # noqa: E402
from test_video_sop_bom_upload_layout import FULL_FILES, _bedrock_script, _deliverables, _load, _run  # noqa: E402
from video_sop_bom_pipeline import PROMPT_TOKENS, load_prompt  # noqa: E402

_TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "vamsSchema", "templates")
_TRANSCRIPT_STEMS = {"transcript.json", "transcript.txt", "transcript.vtt", "transcript.srt", "video-timeline.json",
                     "analysis-report.json"}

PROFILE = {
    "profileId": "test-profile", "profileVersion": "2",
    "prompts": {
        "window_extraction": "Window [[WINDOW_INDEX]]/[[WINDOW_COUNT]]. Log every observation.\n<transcript>\n[[TRANSCRIPT]]\n</transcript>\n[[ADDITIONAL_INSTRUCTIONS]]",
        "vision_verification": "Check these frames: [[FRAME_LIST]]",
        "finalize": "Report on [[PRODUCT_NAME]] from [[MERGED_STEPS]].",
        "system_boundary": "You are a site safety analyst.",
    },
    "stages": {"window_extraction": {"maxTokens": 12000}, "vision_verification": {"maxTokens": 8000, "batchMaxFrames": 1},
               "finalize": {"maxTokens": 9000}},
    "artifacts": [{"name": "site_report", "sourceField": "site_report", "renderer": "summary-card"},
                  {"name": "equipment_list", "sourceField": "equipment_list", "renderer": "table"},
                  {"name": "raw_notes", "sourceField": "raw_notes"}],
}
PROFILE_CONFIG = dict(CONFIG, generateLabSummary=False, profile=PROFILE)


def _profile_finalize_payload():
    return {
        "summary": "Walked the site.",
        "safety_notes": ["Guardrail missing on the west scaffold."],
        "dependency_edges": [],
        "site_report": {"report_title": "Site report", "executive_summary": "Busy | active site.",
                        "stats": [{"label": "Total Hazards", "value": "1"}],
                        "sections": [{"heading": "PPE Compliance", "content": "Partial."}],
                        "recommendations": [{"priority": "immediate", "recommendation": "Fit the guardrail."}]},
        "equipment_list": [{"equipment_name": "tower crane", "status": "operating"},
                           {"equipment_name": "excavator", "status": "idle", "notes": "parked | fenced"}],
        "raw_notes": {"inspector": "n/a"},
    }


def _render_template(stem):
    """The template body with every tag at its default, substituted the way VAMS does."""
    with open(os.path.join(_TEMPLATES, f"{stem}.json"), encoding="utf-8") as handle:
        template = json.load(handle)
    body = template["configBody"]
    for tag in template["tagSchema"]:
        value = tag.get("default")
        value = "" if value is None else value
        body = body.replace("{{" + tag["tagKey"] + "}}", json.dumps(value)[1:-1] if isinstance(value, str) else json.dumps(value))
    return json.loads(body)


def _definition(config):
    definition = make_definition()
    definition["config"] = copy.deepcopy(config)
    return definition


class TestProfileRulesTables:
    def test_the_configured_inputs_are_the_built_in_prompt_tokens(self):
        from video_sop_bom_pipeline import profileRules

        assert profileRules.PROMPT_INPUTS == PROMPT_TOKENS
        assert set(profileRules.PROMPT_STAGES) == set(PROMPT_TOKENS)

    def test_every_hyphen_free_built_in_deliverable_is_reserved(self):
        from video_sop_bom_pipeline import profileRules

        stems = {name.split(".", 1)[0] for name in FULL_FILES | {"keyframe-0000-00h00m01s.jpg"}}
        assert {stem for stem in stems if "-" not in stem} == set(profileRules.RESERVED_ARTIFACT_NAMES)


class TestDefinitionValidation:
    def test_a_profile_definition_is_accepted(self):
        from video_sop_bom_pipeline.definition import validate_definition

        assert validate_definition(_definition(PROFILE_CONFIG))["config"]["profile"] == PROFILE

    def test_a_model_key_is_an_input_rejection_naming_the_rule(self):
        from video_sop_bom_pipeline.definition import validate_definition
        from video_sop_bom_pipeline.errors import PipelineRejection

        config = copy.deepcopy(PROFILE_CONFIG)
        config["profile"]["stages"]["finalize"]["model_id"] = "us.anthropic.claude-x"
        with pytest.raises(PipelineRejection) as raised:
            validate_definition(_definition(config))
        assert raised.value.code == "VideoSopBomInputRejected"
        assert raised.value.cause == (
            "profile.stages.finalize.model_id names a model; this pipeline uses one Amazon Bedrock model per deployment, "
            "set in app.pipelines.useGenAiVideoSopBom.bedrockModelId, so a template or profile may not set one.")

    def test_a_profile_rule_violation_is_an_input_rejection(self):
        from video_sop_bom_pipeline.definition import validate_definition
        from video_sop_bom_pipeline.errors import PipelineRejection

        config = copy.deepcopy(PROFILE_CONFIG)
        config["profile"]["prompts"]["finalize"] = "Report on nothing."
        with pytest.raises(PipelineRejection) as raised:
            validate_definition(_definition(config))
        assert raised.value.code == "VideoSopBomInputRejected"
        assert "never places [[MERGED_STEPS]]" in raised.value.cause

    def test_a_schema_violation_inside_the_profile_is_still_the_schema_error(self):
        from video_sop_bom_pipeline.definition import validate_definition
        from video_sop_bom_pipeline.errors import PipelineRejection

        config = copy.deepcopy(PROFILE_CONFIG)
        config["profile"]["stages"]["finalize"]["maxTokens"] = 64000
        with pytest.raises(PipelineRejection) as raised:
            validate_definition(_definition(config))
        assert raised.value.code == "VideoSopBomPipelineError"
        assert raised.value.cause.startswith("definition document config block invalid at profile/stages/finalize/maxTokens")


class TestBuiltInPathUnchanged:
    """Without a profile every stage sees the shipped prompt, the fixed boundary, the built-in caps and
    the shipped finalize schema (the existing suites pin the rest)."""

    def test_the_helpers_return_the_built_ins(self):
        from video_sop_bom_pipeline import bedrock

        values = {"PRODUCT_NAME": "P", "PART_LEVEL_BASE": "0", "PART_TYPES_WITH_DESCRIPTIONS": "-", "MATERIAL_TYPES": "-",
                  "PRIMARY_TECHNIQUES": "-", "MERGED_STEPS": "[]", "MERGED_COMPONENTS": "[]", "VISION_RESULTS": "[]",
                  "ADDITIONAL_INSTRUCTIONS": ""}
        assert bedrock.stage_prompt("finalize", CONFIG, values) == bedrock.render_prompt("finalize", values)
        assert bedrock.system_prompt(CONFIG) == bedrock.SYSTEM_BOUNDARY == load_prompt("system_boundary").strip()
        assert bedrock.finalize_tool_schema(CONFIG) is bedrock.FINALIZE_TOOL_SCHEMA
        assert bedrock.stage_setting("finalize", "maxTokens", CONFIG, 32000) == 32000
        assert bedrock.stage_setting("vision_verification", "batchMaxFrames", None, 12) == 12


class TestConfiguredPrompts:
    def test_the_profile_prompt_reaches_the_converse_request(self):
        from video_sop_bom_pipeline import bedrock

        fake = FakeBedrock(lambda request, n: tool_use_response(bedrock.WINDOW_TOOL, _window_payload()))
        config = dict(PROFILE_CONFIG, additionalInstructions="Metric units.")
        bedrock.extract_window(make_clients(bedrock=fake), MODEL, _window(), config, "Site A", max_key_moments=3)
        request = fake.calls[0]
        text = request["messages"][0]["content"][0]["text"]
        assert text.startswith("Window 1/1. Log every observation.\n<transcript>\n[00:00:00] Remove the cover.")
        assert text.endswith("</transcript>\nMetric units.")
        assert "[[" not in text and "teardown" not in text, "the built-in window prompt is not sent"
        assert request["system"] == [{"text": load_prompt("system_boundary").strip() + "\n\nYou are a site safety analyst."}]
        assert request["inferenceConfig"] == {"maxTokens": 12000}
        assert request["modelId"] == MODEL, "the deployment's model, whatever the profile says"

    def test_rendering_is_one_pass_so_a_value_is_never_substituted_again(self):
        from video_sop_bom_pipeline import bedrock

        text = bedrock.render_configured_prompt(
            "window_extraction", "[[TRANSCRIPT]] / [[ADDITIONAL_INSTRUCTIONS]] / [[UNLISTED]]",
            {"TRANSCRIPT": "said [[ADDITIONAL_INSTRUCTIONS]] aloud", "ADDITIONAL_INSTRUCTIONS": "ops"})
        assert text == "said [[ADDITIONAL_INSTRUCTIONS]] aloud / ops / [[UNLISTED]]"

    def test_every_stage_input_is_rendered(self):
        from video_sop_bom_pipeline import bedrock

        for name, tokens in PROMPT_TOKENS.items():
            text = bedrock.render_configured_prompt(name, " ".join(f"[[{token}]]" for token in tokens),
                                                    {token: f"<{token.lower()}>" for token in tokens})
            assert "[[" not in text, name

    def test_the_vision_prompt_cap_and_batch_size_come_from_the_profile(self, tmp_path):
        from test_video_sop_bom_bedrock import _frames, _vision_responder
        from video_sop_bom_pipeline import bedrock

        fake = FakeBedrock(_vision_responder())
        results = bedrock.verify_frames(make_clients(bedrock=fake), MODEL, _frames(tmp_path, 3), steps=[], components=[],
                                        config=PROFILE_CONFIG)
        assert [result["momentIndex"] for result in results] == [0, 1, 2]
        assert len(fake.calls) == 3, "batchMaxFrames 1"
        assert fake.calls[0]["messages"][0]["content"][0]["text"].startswith("Check these frames: - momentIndex 0 (t=0.0s)")
        assert fake.calls[0]["inferenceConfig"] == {"maxTokens": 8000}

    def test_a_lowered_cap_is_named_in_the_overflow_cause(self):
        from video_sop_bom_pipeline import bedrock
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake = FakeBedrock(lambda request, n: bare_response("max_tokens"))
        with pytest.raises(PipelineRejection) as raised:
            bedrock.finalize(make_clients(bedrock=fake), MODEL, [_step()], [_component()], [], PROFILE_CONFIG, "P")
        assert raised.value.cause == "finalize output exceeded 9K tokens for 1 components / 1 steps"


class TestFinalizeContract:
    def test_a_profile_finalize_prompt_requires_summary_safety_notes_and_every_artifact(self):
        from video_sop_bom_pipeline import bedrock

        schema = bedrock.finalize_tool_schema(PROFILE_CONFIG)
        assert schema["required"] == ["summary", "safety_notes", "site_report", "equipment_list", "raw_notes"]
        assert schema["properties"]["site_report"] == {
            "description": "The site_report artifact, shaped as the prompt describes.", "type": ["object", "array"]}
        assert schema["properties"]["bom_rows"] == bedrock.FINALIZE_TOOL_SCHEMA["properties"]["bom_rows"], \
            "the teardown fields stay available, optional"
        assert schema["additionalProperties"] is False
        assert bedrock.FINALIZE_TOOL_SCHEMA["required"] == [
            "bom_rows", "step_bom_refs", "dependency_edges", "summary", "safety_notes", "lab_summary"], "not mutated"

    def test_artifacts_without_a_finalize_prompt_keep_the_teardown_contract(self):
        from video_sop_bom_pipeline import bedrock

        profile = {"profileId": "teardown-plus", "profileVersion": "1",
                   "artifacts": [{"name": "notes", "sourceField": "notes"}, {"name": "rows", "sourceField": "bom_rows"}]}
        schema = bedrock.finalize_tool_schema(dict(CONFIG, profile=profile))
        assert schema["required"] == bedrock.FINALIZE_TOOL_SCHEMA["required"] + ["notes"]

    def test_a_missing_artifact_field_fails_naming_the_key_after_one_retry(self):
        from video_sop_bom_pipeline import bedrock
        from video_sop_bom_pipeline.errors import PipelineRejection

        payload = _profile_finalize_payload()
        del payload["equipment_list"]
        fake = FakeBedrock(lambda request, n: tool_use_response(bedrock.FINALIZE_TOOL, payload))
        with pytest.raises(PipelineRejection) as raised:
            bedrock.finalize(make_clients(bedrock=fake), MODEL, [_step()], [_component()], [], PROFILE_CONFIG, "P")
        assert raised.value.code == "VideoSopBomModelOutputInvalid"
        assert raised.value.cause == ("Amazon Bedrock returned no valid record_final_outputs result at stage finalize "
                                      "after one retry (missing required key(s) equipment_list at <root>).")
        assert len(fake.calls) == 2
        assert "'equipment_list' is a required property" in fake.calls[1]["messages"][0]["content"][-1]["text"]

    def test_a_missing_intermediate_key_in_a_window_fails_naming_the_key(self):
        from video_sop_bom_pipeline import bedrock
        from video_sop_bom_pipeline.errors import PipelineRejection

        payload = _window_payload()
        del payload["key_moments"]
        fake = FakeBedrock(lambda request, n: tool_use_response(bedrock.WINDOW_TOOL, payload))
        with pytest.raises(PipelineRejection) as raised:
            bedrock.extract_window(make_clients(bedrock=fake), MODEL, _window(), PROFILE_CONFIG, "P", max_key_moments=3)
        assert raised.value.code == "VideoSopBomModelOutputInvalid"
        assert "(missing required key(s) key_moments at <root>)" in raised.value.cause


class TestArtifactMarkdown:
    def test_a_table_has_one_column_per_key_and_escapes_pipes(self):
        from video_sop_bom_pipeline.render import render_artifact_md

        text = render_artifact_md("equipment_list", _profile_finalize_payload()["equipment_list"], "table")
        assert text.splitlines()[:5] == [
            "# Equipment list", "", "| Equipment name | Status | Notes |", "| --- | --- | --- |",
            "| tower crane | operating |  |"]
        assert "| excavator | idle | parked \\| fenced |" in text

    def test_a_summary_card_lays_out_title_summary_stats_and_sections(self):
        from video_sop_bom_pipeline.render import render_artifact_md

        text = render_artifact_md("site_report", _profile_finalize_payload()["site_report"], "summary-card")
        lines = text.splitlines()
        assert lines[:5] == ["# Site report", "", "Busy | active site.", "", "- **Total Hazards:** 1"]
        assert "## PPE Compliance\n\nPartial." in text
        assert "## Recommendations\n\n| Priority | Recommendation |" in text

    def test_a_steps_timeline_numbers_the_entries_with_timestamps(self):
        from video_sop_bom_pipeline.render import render_artifact_md

        log = {"site_context": {"site_name": "A"}, "entries": [
            {"timestamp_seconds": 75.0, "description": "Scaffold check", "hazard_flag": True, "notes": ""},
            {"timestamp_seconds": 3700, "description": "Crane lift", "personnel_count": 3, "tools": ["crane", "slings"]}]}
        text = render_artifact_md("activity_log", log, "steps-timeline")
        assert "## 1. Scaffold check [00:01:15]\n\n- Hazard flag: true\n" in text
        assert "## 2. Crane lift [01:01:40]\n\n- Personnel count: 3\n- Tools: crane, slings\n" in text
        assert "## Site context\n\n**Site name:** A" in text
        assert text.endswith("\n") and not text.endswith("\n\n")


class TestRunWithProfiles:
    def test_a_profile_run_writes_its_artifacts_and_no_bom(self, tmp_path, monkeypatch, vsb_task_token):
        from video_sop_bom_pipeline import bedrock

        definition = _definition(PROFILE_CONFIG)
        responder = _bedrock_script(finalize=tool_use_response(bedrock.FINALIZE_TOOL, _profile_finalize_payload()))
        status, s3, _, fake, _ = _run(tmp_path, monkeypatch, definition=definition, responder=responder)
        assert status == "SUCCEEDED"
        names = _deliverables(s3, definition)
        assert names == (_TRANSCRIPT_STEMS | {"sop.json", "sop.md", "frames.json", "site_report.json", "site_report.md",
                                              "equipment_list.json", "equipment_list.md", "raw_notes.json",
                                              "keyframe-0000-00h00m01s.jpg", "keyframe-0001-00h01m40s.jpg"})
        tools = [call["toolConfig"]["toolChoice"]["tool"]["name"] for call in fake.calls]
        assert tools == [bedrock.WINDOW_TOOL, bedrock.VISION_TOOL, bedrock.VISION_TOOL, bedrock.FINALIZE_TOOL], "batchMaxFrames 1"
        finalize_call = fake.calls[-1]
        assert finalize_call["messages"][0]["content"][0]["text"].startswith("Report on Cognex In-Sight 2800 from [")
        assert finalize_call["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["required"][2:] == [
            "site_report", "equipment_list", "raw_notes"]
        assert all(call["modelId"] == definition["bedrockModelId"] for call in fake.calls)

        prefix = definition["outputs"]["files"] + "sop-bom/"
        assert _load(s3, "run-bucket", prefix + "site_report.json") == _profile_finalize_payload()["site_report"]
        sop = _load(s3, "run-bucket", prefix + "sop.json")
        assert sop["title"] == "Cognex In-Sight 2800 — SOP" and sop["summary"] == "Walked the site."
        assert all(step["bom_refs"] == [] for step in sop["steps"])

        summary = _load(s3, "run-bucket", definition["outputs"]["results"] + "summary.json")
        assert summary["paths"]["artifact_site_report"] == "/sop-bom/exec-0001/site_report.json"
        assert {"bom", "bomCsv", "labSummary"}.isdisjoint(summary["paths"])
        assert list(summary["paths"]) == ["transcript", "timeline", "sop", "frames", "artifact_site_report",
                                          "artifact_equipment_list", "artifact_raw_notes", "analysisReport"]
        digest = "sha256:" + hashlib.sha256(PROFILE["prompts"]["finalize"].encode("utf-8")).hexdigest()
        assert summary["config"]["profile"]["prompts"]["finalize"] == digest, "prompt bodies stay out of the 50 KB summary"
        assert summary["config"]["profile"]["artifacts"] == PROFILE["artifacts"]
        assert summary["warnings"] == []
        report = _load(s3, "run-bucket", prefix + "analysis-report.json")
        assert report["config"] == definition["config"], "the report keeps the full prompts"

        metadata = _load(s3, "run-bucket", definition["outputs"]["metadata"] + "asset.metadata.json")
        keys = {entry["metadataKey"] for entry in metadata["metadata"]}
        assert {"sopBom_productName", "sopBom_stepCount", "sopBom_sopPath"} <= keys
        assert {"sopBom_componentCount", "sopBom_totalMassG", "sopBom_bomPath", "sopBom_labSummaryPath"}.isdisjoint(keys)

    def test_run_visual_verification_false_skips_the_frames_and_vision_stages(self, tmp_path, monkeypatch, vsb_task_token):
        from video_sop_bom_pipeline import bedrock

        definition = _definition(dict(CONFIG, runVisualVerification=False))
        status, s3, _, fake, _ = _run(tmp_path, monkeypatch, definition=definition)
        assert status == "SUCCEEDED"
        names = _deliverables(s3, definition)
        assert names == FULL_FILES - {"frames.json"}, "the teardown set, BOM included, without frames"
        assert [call["toolConfig"]["toolChoice"]["tool"]["name"] for call in fake.calls] == [bedrock.WINDOW_TOOL, bedrock.FINALIZE_TOOL]
        assert "VISUAL VERIFICATION RESULTS (keyed by momentIndex):\n[]" in fake.calls[-1]["messages"][0]["content"][0]["text"]
        report = _load(s3, "run-bucket", definition["outputs"]["files"] + "sop-bom/analysis-report.json")
        assert [stage["name"] for stage in report["stages"]][8:] == ["extract", "merge", "finalize"]
        assert report["framesRequested"] == 0 and report["framesExtracted"] == 0
        summary = _load(s3, "run-bucket", definition["outputs"]["results"] + "summary.json")
        assert "frames" not in summary["paths"]
        sop = _load(s3, "run-bucket", definition["outputs"]["files"] + "sop-bom/sop.json")
        assert all(step["frame_ref"] is None for step in sop["steps"])

    def test_a_missing_artifact_field_ingests_nothing(self, tmp_path, monkeypatch, vsb_task_token):
        from video_sop_bom_pipeline import bedrock

        payload = _profile_finalize_payload()
        del payload["raw_notes"]
        responder = _bedrock_script(finalize=tool_use_response(bedrock.FINALIZE_TOOL, payload))
        status, s3, _, _, sfn = _run(tmp_path, monkeypatch, definition=_definition(PROFILE_CONFIG), responder=responder)
        assert status == "FAILED"
        assert sfn.failures[0]["error"] == "VideoSopBomModelOutputInvalid"
        assert "missing required key(s) raw_notes" in sfn.failures[0]["cause"]
        assert s3.keys("run-bucket") == []

    def test_generate_lab_summary_without_a_lab_summary_field_is_a_warning(self, tmp_path, monkeypatch, vsb_task_token):
        from video_sop_bom_pipeline import bedrock

        definition = _definition(dict(PROFILE_CONFIG, generateLabSummary=True))
        responder = _bedrock_script(finalize=tool_use_response(bedrock.FINALIZE_TOOL, _profile_finalize_payload()))
        status, s3, _, _, _ = _run(tmp_path, monkeypatch, definition=definition, responder=responder)
        assert status == "SUCCEEDED"
        assert "lab-summary.json" not in _deliverables(s3, definition)
        summary = _load(s3, "run-bucket", definition["outputs"]["results"] + "summary.json")
        assert summary["warnings"] == [
            "generateLabSummary is true but the finalize output carries no lab_summary, so no lab summary was written."]


class TestShippedProfileTemplates:
    """The two profile templates as deployed (every tag at its default), end to end."""

    def test_the_construction_site_template(self, tmp_path, monkeypatch, vsb_task_token):
        from video_sop_bom_pipeline import bedrock

        config = _render_template("video-sop-bom-construction-site")
        payload = {"summary": "s", "safety_notes": [], "dependency_edges": [],
                   "equipment_list": [{"equipment_name": "crane"}],
                   "activity_log": {"site_context": {}, "entries": [{"timestamp_seconds": 1.0, "description": "walk"}]},
                   "site_report": {"report_title": "Construction Site Walkthrough Report", "executive_summary": "ok"}}
        definition = _definition(config)
        status, s3, _, fake, _ = _run(tmp_path, monkeypatch, definition=definition,
                                      responder=_bedrock_script(finalize=tool_use_response(bedrock.FINALIZE_TOOL, payload)))
        assert status == "SUCCEEDED"
        names = _deliverables(s3, definition)
        assert {"equipment_list.json", "equipment_list.md", "activity_log.json", "activity_log.md", "site_report.json",
                "site_report.md", "frames.json", "sop.json"} <= names
        assert not any(name.startswith(("bom.", "lab-summary.")) for name in names)
        window_text = fake.calls[0]["messages"][0]["content"][0]["text"]
        assert "construction site walkthrough transcript" in window_text and "[[" not in window_text
        assert fake.calls[0]["system"] == [{"text": load_prompt("system_boundary").strip()}], "no profile system text"
        assert [call["toolConfig"]["toolChoice"]["tool"]["name"] for call in fake.calls][-1] == bedrock.FINALIZE_TOOL
        assert any(call["toolConfig"]["toolChoice"]["tool"]["name"] == bedrock.VISION_TOOL for call in fake.calls)

    def test_the_business_process_template_is_transcript_only_for_the_model(self, tmp_path, monkeypatch, vsb_task_token):
        from video_sop_bom_pipeline import bedrock

        config = _render_template("video-sop-bom-business-process")
        payload = {"summary": "s", "safety_notes": [], "dependency_edges": [],
                   "process_doc": {"title": "Close a case", "steps": [{"step_number": 1, "action": "Open the case"}]},
                   "overview": {"title": "Close a case", "summary": "ok", "stats": [], "sections": []}}
        definition = _definition(config)
        status, s3, _, fake, _ = _run(tmp_path, monkeypatch, definition=definition,
                                      responder=_bedrock_script(finalize=tool_use_response(bedrock.FINALIZE_TOOL, payload)))
        assert status == "SUCCEEDED"
        assert _deliverables(s3, definition) == _TRANSCRIPT_STEMS | {
            "sop.json", "sop.md", "process_doc.json", "process_doc.md", "overview.json", "overview.md"}
        assert [call["toolConfig"]["toolChoice"]["tool"]["name"] for call in fake.calls] == [bedrock.WINDOW_TOOL, bedrock.FINALIZE_TOOL]
        assert "business process" in fake.calls[0]["messages"][0]["content"][0]["text"]
