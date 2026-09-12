# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Amazon Bedrock Converse tool-use: request shape, stopReason branching, the bounded retry budget,
the BEDROCK_CALL marker, prompt rendering from WP00's templates, the WP00 tool schemas, window
splitting, vision batching and finalize compaction.

Run from the container directory:  python -m pytest tests/test_video_sop_bom_bedrock.py -q

Every response here is a plain dict shaped like boto3's Converse return value; `toolUse.input` is
already a dict (boto3 deserialises it), `usage.inputTokens/outputTokens` are the counters. The fake
payloads are validated against WP00's shipped schemas, so they are the contract rather than a mirror
of this module's expectations.
"""

import json
import logging
import os
import sys

import jsonschema
import pytest
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import FakeBedrock, bare_response, client_error, make_clients, text_response, tool_use_response  # noqa: E402
from video_sop_bom_pipeline import load_prompt, load_schema  # noqa: E402

MODEL = "global.anthropic.claude-sonnet-5"
TOOL = "answer_tool"
STAGE = "window-0"
FENCE = "`" * 3  # built from parts so this source carries no literal triple backtick
CONFIG = {
    "mode": "full", "languageCode": "en-US", "videoOrder": "selection", "productName": "", "contributors": "",
    "maxKeyFrames": 60, "partLevelBase": "0", "generateLabSummary": True, "additionalInstructions": "",
}


def _schema():
    return {"type": "object", "additionalProperties": False, "required": ["answer"], "properties": {"answer": {"type": "string"}}}


def _harness(responder, clock=None):
    from video_sop_bom_pipeline import bedrock

    fake = FakeBedrock(responder)
    usage = bedrock.BedrockUsage()
    sleeps = []

    def invoke():
        return bedrock.converse_tool(
            make_clients(bedrock=fake), MODEL, "SYSTEM", [{"role": "user", "content": [{"text": "hello"}]}],
            TOOL, _schema(), 16000, stage=STAGE, usage=usage, sleep=sleeps.append,
            rng=lambda low, high: 1.0, clock=clock or (lambda: 0.0),
        )

    return fake, usage, sleeps, invoke


def _ok(request, n):
    return tool_use_response(TOOL, {"answer": "ok"})


class TestRequestShape:
    def test_forced_tool_use_request(self):
        fake, _, _, invoke = _harness(_ok)
        assert invoke() == {"answer": "ok"}
        request = fake.calls[0]
        assert request["modelId"] == MODEL
        assert request["system"] == [{"text": "SYSTEM"}]
        assert request["inferenceConfig"] == {"maxTokens": 16000}
        assert "additionalModelRequestFields" not in request
        spec = request["toolConfig"]["tools"][0]["toolSpec"]
        assert spec["name"] == TOOL and spec["inputSchema"] == {"json": _schema()}
        assert request["toolConfig"]["toolChoice"] == {"tool": {"name": TOOL}}

    def test_a_string_tool_input_is_not_parsed_but_retried(self):
        fake, _, _, invoke = _harness(lambda request, n: tool_use_response(TOOL, '{"answer": "ok"}') if n == 1 else _ok(request, n))
        assert invoke() == {"answer": "ok"}
        assert len(fake.calls) == 2

    def test_one_marker_line_per_response_and_the_usage_counters(self, caplog):
        fake, usage, _, invoke = _harness(lambda request, n: tool_use_response(TOOL, {"answer": "ok"}, in_tokens=1234, out_tokens=56))
        with caplog.at_level(logging.INFO):
            invoke()
        lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("BEDROCK_CALL")]
        assert lines == [f"BEDROCK_CALL stage={STAGE} model={MODEL} in=1234 out=56"]
        assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 1234, 56)
        assert usage.as_dict() == {"calls": 1, "inputTokens": 1234, "outputTokens": 56}
        assert usage.per_stage == {STAGE: {"calls": 1, "inputTokens": 1234, "outputTokens": 56}}


class TestStopReasons:
    def test_the_enum_is_the_converse_one_without_refusal(self):
        from video_sop_bom_pipeline import bedrock

        assert set(bedrock.KNOWN_STOP_REASONS) == {
            "end_turn", "tool_use", "max_tokens", "stop_sequence", "guardrail_intervened", "content_filtered",
            "malformed_model_output", "malformed_tool_use", "model_context_window_exceeded",
        }
        assert "refusal" not in bedrock.KNOWN_STOP_REASONS

    def test_end_turn_with_text_gets_one_retry_with_the_reason_folded_into_the_user_message(self):
        fake, usage, _, invoke = _harness(lambda request, n: text_response("I cannot.") if n == 1 else _ok(request, n))
        assert invoke() == {"answer": "ok"}
        assert len(fake.calls) == 2 and usage.calls == 2
        second = fake.calls[1]["messages"]
        assert [message["role"] for message in second] == ["user"], "no assistant echo: a toolUse without a toolResult is rejected by the API"
        assert any("not accepted" in block.get("text", "") for block in second[-1]["content"])

    @pytest.mark.parametrize("stop", ["malformed_tool_use", "malformed_model_output"])
    def test_malformed_twice_is_model_output_invalid(self, stop):
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake, _, _, invoke = _harness(lambda request, n: bare_response(stop))
        with pytest.raises(PipelineRejection) as raised:
            invoke()
        assert raised.value.code == "VideoSopBomModelOutputInvalid"
        assert len(fake.calls) == 2
        assert stop in raised.value.cause and "after one retry" in raised.value.cause

    @pytest.mark.parametrize("stop", ["content_filtered", "guardrail_intervened", "model_context_window_exceeded"])
    def test_fatal_stop_reasons_fail_at_once_naming_the_reason(self, stop):
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake, _, _, invoke = _harness(lambda request, n: bare_response(stop))
        with pytest.raises(PipelineRejection) as raised:
            invoke()
        assert raised.value.code == "VideoSopBomModelOutputInvalid"
        assert stop in raised.value.cause
        assert len(fake.calls) == 1

    def test_refusal_is_unknown_on_converse_and_never_parsed(self):
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake, _, _, invoke = _harness(lambda request, n: tool_use_response(TOOL, {"answer": "looks valid"}, stop_reason="refusal"))
        with pytest.raises(PipelineRejection) as raised:
            invoke()
        assert "unknown stopReason 'refusal'" in raised.value.cause
        assert len(fake.calls) == 1

    def test_a_different_tool_name_is_retried(self):
        fake, _, _, invoke = _harness(lambda request, n: tool_use_response("other_tool", {"answer": "ok"}) if n == 1 else _ok(request, n))
        assert invoke() == {"answer": "ok"} and len(fake.calls) == 2

    def test_schema_violation_is_retried_with_the_path_in_the_feedback(self):
        fake, _, _, invoke = _harness(lambda request, n: tool_use_response(TOOL, {"answer": 5}) if n == 1 else _ok(request, n))
        assert invoke() == {"answer": "ok"}
        feedback = fake.calls[1]["messages"][-1]["content"][-1]["text"]
        assert "answer" in feedback

    def test_schema_violation_twice_is_model_output_invalid(self):
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake, _, _, invoke = _harness(lambda request, n: tool_use_response(TOOL, {"answer": 5}))
        with pytest.raises(PipelineRejection) as raised:
            invoke()
        assert len(fake.calls) == 2 and "after one retry" in raised.value.cause

    def test_max_tokens_raises_max_tokens_stop_for_the_caller(self):
        from video_sop_bom_pipeline import bedrock

        fake, _, _, invoke = _harness(lambda request, n: bare_response("max_tokens"))
        with pytest.raises(bedrock.MaxTokensStop):
            invoke()
        assert len(fake.calls) == 1


class TestRetryBudget:
    def test_throttling_is_retried_with_backoff_and_calls_count_responses_only(self):
        fake, usage, sleeps, invoke = _harness(
            lambda request, n: client_error("ThrottlingException", "slow down", "Converse") if n <= 2 else _ok(request, n)
        )
        assert invoke() == {"answer": "ok"}
        assert len(fake.calls) == 3 and usage.calls == 1
        assert sleeps == [15.0, 30.0]
        assert usage.retry_wait_s == 45.0

    @pytest.mark.parametrize("code", ["ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
                                      "ModelNotReadyException", "ModelTimeoutException", "InternalServerException"])
    def test_every_retryable_code_is_retried(self, code):
        fake, _, sleeps, invoke = _harness(lambda request, n: client_error(code, "x", "Converse") if n == 1 else _ok(request, n))
        assert invoke() == {"answer": "ok"} and len(fake.calls) == 2 and len(sleeps) == 1

    @pytest.mark.parametrize("code", ["ValidationException", "AccessDeniedException", "ResourceNotFoundException", "ModelErrorException"])
    def test_fatal_codes_fail_at_once_with_a_readable_cause(self, code):
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake, _, sleeps, invoke = _harness(lambda request, n: client_error(code, "boom", "Converse"))
        with pytest.raises(PipelineRejection) as raised:
            invoke()
        assert raised.value.code == "VideoSopBomPipelineError"
        assert raised.value.cause == f"Amazon Bedrock {code} at stage window-0: boom"
        assert len(fake.calls) == 1 and sleeps == []

    def test_the_attempt_budget_is_six(self):
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake, _, sleeps, invoke = _harness(lambda request, n: client_error("ThrottlingException", "x", "Converse"))
        with pytest.raises(PipelineRejection) as raised:
            invoke()
        assert len(fake.calls) == 6 and len(sleeps) == 5
        assert raised.value.code == "VideoSopBomPipelineError"
        assert raised.value.cause == "Amazon Bedrock ThrottlingException persisted for 1 min at stage window-0"

    def test_the_wall_clock_budget_per_call_is_45_minutes(self):
        from video_sop_bom_pipeline.errors import PipelineRejection

        ticks = iter([0.0, 2690.0, 2690.0])
        fake, _, sleeps, invoke = _harness(lambda request, n: client_error("ServiceUnavailableException", "x", "Converse"), clock=lambda: next(ticks))
        with pytest.raises(PipelineRejection) as raised:
            invoke()
        assert len(fake.calls) == 1 and sleeps == []
        assert "persisted for 45 min" in raised.value.cause

    def test_the_cumulative_budget_across_calls_is_90_minutes(self):
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake, usage, sleeps, invoke = _harness(lambda request, n: client_error("ThrottlingException", "x", "Converse") if n == 1 else _ok(request, n))
        usage.retry_wait_s = 5390.0
        with pytest.raises(PipelineRejection):
            invoke()
        assert sleeps == [] and len(fake.calls) == 1

    def test_connection_errors_are_retryable(self):
        from botocore.exceptions import ReadTimeoutError

        fake, _, sleeps, invoke = _harness(
            lambda request, n: ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/") if n == 1 else _ok(request, n)
        )
        assert invoke() == {"answer": "ok"} and len(sleeps) == 1


class TestToolSchemas:
    NAMES = (
        ("window_extraction_schema", "WINDOW_TOOL_SCHEMA"),
        ("vision_schema", "VISION_TOOL_SCHEMA"),
        ("finalize_schema", "FINALIZE_TOOL_SCHEMA"),
    )

    @pytest.mark.parametrize("shipped_name,constant", NAMES)
    def test_tool_schemas_are_wp00_documents_with_metadata_dropped_and_refs_inlined(self, shipped_name, constant):
        from video_sop_bom_pipeline import bedrock

        shipped = load_schema(shipped_name)
        schema = getattr(bedrock, constant)
        assert schema is not shipped and schema["type"] == "object"
        assert set(schema) == set(shipped) - {"$schema", "$id", "title", "description", "$defs"}
        assert schema["required"] == shipped["required"]
        assert "$ref" not in json.dumps(schema), "a Converse inputSchema must be self-contained"
        assert "$ref" in json.dumps(shipped), "positive control: the shipped document carries local $refs to inline"
        jsonschema.Draft202012Validator.check_schema(schema)
        assert bedrock.tool_schema(shipped_name) == schema

    def test_fake_payloads_are_the_wp00_contract_and_the_stale_keys_are_rejected(self):
        """The fixtures below validate against WP00's shipped files (not only against this module's
        constants), and the pre-WP00 key spellings fail — so a drift between the two is visible here."""
        from video_sop_bom_pipeline import bedrock

        vision = {"frame_analyses": [{"momentIndex": 0, "confirmed": True, "components_seen": ["part"], "corrections": None, "additional_details": ""}]}
        for payload, shipped_name, constant in (
            (_window_payload(), "window_extraction_schema", bedrock.WINDOW_TOOL_SCHEMA),
            (vision, "vision_schema", bedrock.VISION_TOOL_SCHEMA),
            (_finalize_payload(), "finalize_schema", bedrock.FINALIZE_TOOL_SCHEMA),
        ):
            jsonschema.validate(payload, load_schema(shipped_name))
            jsonschema.validate(payload, constant)
        stale_window = _window_payload()
        stale_window["components"][0]["first_seen_seconds"] = stale_window["components"][0].pop("first_seen_timestamp_seconds")
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(stale_window, bedrock.WINDOW_TOOL_SCHEMA)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"frames": vision["frame_analyses"]}, bedrock.VISION_TOOL_SCHEMA)
        stale_final = _finalize_payload()
        stale_final["step_bom_refs"] = [{"step": 1, "bom_row_indexes": [0]}]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(stale_final, bedrock.FINALIZE_TOOL_SCHEMA)

    def test_part_type_is_not_enum_constrained_so_vocabulary_misses_are_kept_raw(self):
        from video_sop_bom_pipeline import bedrock

        component = bedrock.WINDOW_TOOL_SCHEMA["properties"]["components"]["items"]["properties"]
        assert "enum" not in component["part_type"]
        assert "enum" not in component["material_or_component_type"]
        row = bedrock.FINALIZE_TOOL_SCHEMA["properties"]["bom_rows"]["items"]["properties"]
        assert row["alternative"] == {"type": "string", "enum": ["Yes", "No"]}
        assert "lab_part_number" not in row and "source_step_numbers" not in row


def _window(segment_texts=("Remove the cover.", "Next step.", "Lift the board.", "Unplug the cable.")):
    from video_sop_bom_pipeline.windows import Segment, Window

    segments = [Segment(start_s=float(i), end_s=float(i) + 0.8, text=text) for i, text in enumerate(segment_texts)]
    return Window(index=0, start_s=0.0, end_s=900.0, segments=segments)


def _step(step=1, t=0.5, action="remove", component="cover"):
    return {
        "step": step, "timestamp_seconds": t, "action": action, "component": component, "tools": ["spudger"],
        "fasteners": ["4x Phillips #00 screws"], "locations": ["rear"], "dependencies": [], "motion": {"allowed": ["lift"], "restricted": []},
        "force": {"amount": "light", "indicator": None}, "failure_modes": [], "notes": "long note about the cover",
    }


def _component(description="rear cover", t=0.5):
    return {
        "part_level": 1, "part_type": "Enclosure", "part_description": description, "qty": 1,
        "material_or_component_type": "PC/ABS", "mass_g_per_unit": 42.0, "primary_manufacturing_process": "Molding - Plastics",
        "first_seen_timestamp_seconds": t,
    }


def _window_payload():
    return {
        "product_name_from_narration": "Cognex In-Sight",
        "steps": [_step()],
        "components": [_component()],
        "key_moments": [{"timestamp_seconds": 1.0, "reason": "cover reveal", "expected_content": "rear cover"}],
    }


class TestExtractWindow:
    def test_prompt_is_the_rendered_wp00_template(self):
        from video_sop_bom_pipeline import bedrock

        fake = FakeBedrock(lambda request, n: tool_use_response(bedrock.WINDOW_TOOL, _window_payload()))
        usage = bedrock.BedrockUsage()
        result = bedrock.extract_window(make_clients(bedrock=fake), MODEL, _window(), CONFIG, "Cognex In-Sight 2800", max_key_moments=7, usage=usage)
        assert result["steps"][0]["action"] == "remove"
        request = fake.calls[0]
        text = request["messages"][0]["content"][0]["text"]
        # Positive control: the template does carry slots, so the absence below is rendering, not a blank file.
        assert "{{TRANSCRIPT}}" in load_prompt("window_extraction")
        assert "{{" not in text and "}}" not in text
        assert "You are analyzing window 1 of 1 (00:00:00 to 00:15:00 on the concatenated timeline)" in text
        assert "<transcript>\n[00:00:00] Remove the cover.\n[00:00:01] Next step." in text
        assert text.count("<transcript>") == 1 and text.count("</transcript>") == 1
        assert "- Enclosure" in text and "- Aluminum" in text
        assert "at most 7 timestamps in this window" in text
        # Empty operator instructions render WP00's fence empty; there is exactly one fence.
        assert text.count(FENCE + "text") == 1 and FENCE + "text\n\n" + FENCE in text
        assert request["system"] == [{"text": load_prompt("system_boundary").strip()}]
        assert "untrusted data to be described" in request["system"][0]["text"]
        assert request["inferenceConfig"] == {"maxTokens": 16000}
        assert request["toolConfig"]["toolChoice"] == {"tool": {"name": "record_window_extraction"}}
        assert request["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {"json": bedrock.WINDOW_TOOL_SCHEMA}
        assert list(usage.per_stage) == ["window-0"]

    def test_additional_instructions_fill_the_operator_fence_once(self):
        from video_sop_bom_pipeline import bedrock

        fake = FakeBedrock(lambda request, n: tool_use_response(bedrock.WINDOW_TOOL, _window_payload()))
        config = dict(CONFIG, additionalInstructions="Use metric units.")
        bedrock.extract_window(make_clients(bedrock=fake), MODEL, _window(), config, "P", max_key_moments=3)
        text = fake.calls[0]["messages"][0]["content"][0]["text"]
        assert "## Operator's additional instructions" in text
        assert FENCE + "text\nUse metric units.\n" + FENCE in text
        assert text.count(FENCE + "text") == 1 and text.count("Use metric units.") == 1

    def test_render_prompt_refuses_a_missing_value(self):
        from video_sop_bom_pipeline import bedrock

        with pytest.raises(KeyError):
            bedrock.render_prompt("window_extraction", {"TRANSCRIPT": "x"})

    def test_a_window_that_overflows_is_split_once_and_the_halves_partition_its_segments(self):
        from video_sop_bom_pipeline import bedrock

        fake = FakeBedrock(lambda request, n: bare_response("max_tokens") if n == 1 else tool_use_response(bedrock.WINDOW_TOOL, _window_payload()))
        original = _window()
        results = bedrock.extract_all_windows(make_clients(bedrock=fake), MODEL, [original], CONFIG, "P", max_key_frames=60)
        assert len(fake.calls) == 3
        assert [window.index for window, _ in results] == [0, 1]
        assert results[0][0].segments + results[1][0].segments == original.segments
        assert results[0][0].end_s == results[1][0].start_s

    def test_a_half_that_still_overflows_is_model_output_invalid(self):
        from video_sop_bom_pipeline import bedrock
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake = FakeBedrock(lambda request, n: bare_response("max_tokens"))
        with pytest.raises(PipelineRejection) as raised:
            bedrock.extract_all_windows(make_clients(bedrock=fake), MODEL, [_window()], CONFIG, "P", max_key_frames=60)
        assert raised.value.code == "VideoSopBomModelOutputInvalid"
        assert raised.value.cause == "window 0 output exceeded 16K tokens even after splitting"
        assert len(fake.calls) == 2

    def test_key_moment_bound_is_spread_over_the_windows(self):
        from video_sop_bom_pipeline import bedrock

        fake = FakeBedrock(lambda request, n: tool_use_response(bedrock.WINDOW_TOOL, _window_payload()))
        windows = [_window(), _window(), _window()]
        for index, window in enumerate(windows):
            window.index = index
        bedrock.extract_all_windows(make_clients(bedrock=fake), MODEL, windows, CONFIG, "P", max_key_frames=10)
        assert all("at most 4 timestamps in this window" in call["messages"][0]["content"][0]["text"] for call in fake.calls)
        assert "window 2 of 3 (" in fake.calls[1]["messages"][0]["content"][0]["text"]


def _frames(tmp_path, count, size=(64, 48)):
    frames = []
    for index in range(count):
        path = tmp_path / f"keyframe-{index:04d}-00h00m{index:02d}s.jpg"
        # One solid colour for every frame so the JPEGs are byte-identical in size (the byte-cap test relies on it).
        Image.new("RGB", size, (30, 100, 50)).save(str(path), "JPEG", quality=85)
        frames.append({"momentIndex": index, "path": str(path), "timestamp_seconds": 10.0 * index, "expected_content": f"part {index}", "reason": "reveal"})
    return frames


def _vision_responder(stop_first=0):
    """Answers with one entry per image in the request; the first `stop_first` calls hit max_tokens.
    Frames are recognised by the `momentIndex=<n>` text block that precedes each image (the rendered
    FRAME_LIST in the prompt writes `momentIndex <n>` without `=`, so it is not double-counted)."""
    from video_sop_bom_pipeline import bedrock

    def responder(request, n):
        if n <= stop_first:
            return bare_response("max_tokens")
        indexes = []
        for block in request["messages"][0]["content"]:
            text = block.get("text", "")
            marker = "momentIndex="
            if marker in text and "at " in text:
                indexes.append(int(text.split(marker, 1)[1].split()[0]))
        payload = {"frame_analyses": [{"momentIndex": i, "confirmed": True, "components_seen": ["part"], "corrections": None, "additional_details": ""} for i in indexes]}
        return tool_use_response(bedrock.VISION_TOOL, payload)

    return responder


class TestVerifyFrames:
    def test_batches_are_time_ordered_and_capped_at_12(self, tmp_path):
        from video_sop_bom_pipeline import bedrock

        batches = bedrock.batch_frames(list(reversed(_frames(tmp_path, 30))))
        assert [len(batch) for batch in batches] == [12, 12, 6]
        assert [frame["momentIndex"] for frame in batches[0]] == list(range(12))

    def test_the_byte_cap_closes_a_batch(self, tmp_path, monkeypatch):
        from video_sop_bom_pipeline import bedrock

        frames = _frames(tmp_path, 5)
        one = os.path.getsize(frames[0]["path"])
        monkeypatch.setattr(bedrock, "VISION_BATCH_MAX_BYTES", one * 3 + 1)
        assert [len(batch) for batch in bedrock.batch_frames(frames)] == [3, 2]

    def test_images_are_raw_bytes_named_by_momentIndex_with_window_context(self, tmp_path):
        from video_sop_bom_pipeline import bedrock

        frames = _frames(tmp_path, 2)
        fake = FakeBedrock(_vision_responder())
        usage = bedrock.BedrockUsage()
        results = bedrock.verify_frames(make_clients(bedrock=fake), MODEL, frames, steps=[_step(t=5.0)], components=[_component(t=5.0)],
                                        additional_instructions="Name colours.", usage=usage)
        assert [result["momentIndex"] for result in results] == [0, 1]
        request = fake.calls[0]
        content = request["messages"][0]["content"]
        images = [block["image"] for block in content if "image" in block]
        assert len(images) == 2
        assert all(isinstance(image["source"]["bytes"], bytes) and image["format"] == "jpeg" for image in images)
        texts = [block["text"] for block in content if "text" in block]
        intro = texts[0]
        assert "{{FRAME_LIST}}" in load_prompt("vision_verification") and "{{" not in intro
        assert "- momentIndex 1 (t=10.0s): expected part 1" in intro
        assert '"action": "remove"' in intro, "the steps within the batch's window are given as context"
        assert '"first_seen_timestamp_seconds": 5.0' in intro
        assert intro.count(FENCE + "text") == 1 and FENCE + "text\nName colours.\n" + FENCE in intro
        assert any("momentIndex=1 at 10.0s" in text for text in texts[1:])
        assert request["system"] == [{"text": load_prompt("system_boundary").strip()}]
        assert request["inferenceConfig"] == {"maxTokens": 32000}
        assert list(usage.per_stage) == ["vision-0"]

    def test_a_batch_that_overflows_is_halved_once(self, tmp_path):
        from video_sop_bom_pipeline import bedrock

        fake = FakeBedrock(_vision_responder(stop_first=1))
        results = bedrock.verify_frames(make_clients(bedrock=fake), MODEL, _frames(tmp_path, 4), steps=[], components=[])
        assert len(fake.calls) == 3
        assert [result["momentIndex"] for result in results] == [0, 1, 2, 3]

    def test_a_halved_batch_that_still_overflows_is_model_output_invalid(self, tmp_path):
        from video_sop_bom_pipeline import bedrock
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake = FakeBedrock(lambda request, n: bare_response("max_tokens"))
        with pytest.raises(PipelineRejection) as raised:
            bedrock.verify_frames(make_clients(bedrock=fake), MODEL, _frames(tmp_path, 4), steps=[], components=[])
        assert raised.value.code == "VideoSopBomModelOutputInvalid"
        assert "vision batch" in raised.value.cause and "32K" in raised.value.cause
        assert len(fake.calls) == 2


def _finalize_payload():
    return {
        "bom_rows": [{
            "part_level": 1, "part_type": "Enclosure", "part_description": "rear cover", "qty": 1,
            "material_or_component_type": "PC/ABS", "mass_g_per_unit": 42.0, "primary_manufacturing_process": "Molding - Plastics",
            "manufacturing_country": None, "alternative": "No", "manufacturer_part_number": "", "material_notes": None,
        }],
        "step_bom_refs": [{"step": 1, "row_indexes": [0]}],
        "dependency_edges": [{"step": 1, "depends_on_step": None, "text": "bench cleared"}],
        "summary": "A short teardown.",
        "safety_notes": ["Unplug first."],
        "lab_summary": {
            "product_description": "A smart camera.", "product_source_url": None,
            "background": "b", "materials_methodology": "m", "safety_considerations": "s", "existing_bom_provided": False,
            "existing_bom_notes": "No BOM was provided by the supplier for this request.", "primary_manufacturing_processes": "p",
            "key_observations": ["k"], "comparative_analysis": "No BOM was provided so comparative analysis could not be performed.",
        },
    }


class TestFinalize:
    def test_request_carries_the_merged_draft_and_returns_the_validated_result(self):
        from video_sop_bom_pipeline import bedrock

        fake = FakeBedrock(lambda request, n: tool_use_response(bedrock.FINALIZE_TOOL, _finalize_payload()))
        usage = bedrock.BedrockUsage()
        vision = [{"momentIndex": 0, "confirmed": True, "components_seen": ["cover"], "corrections": None, "additional_details": "black"}]
        result = bedrock.finalize(make_clients(bedrock=fake), MODEL, [_step()], [_component()], vision, dict(CONFIG, partLevelBase="1"), "Cognex In-Sight 2800", usage=usage)
        assert result["bom_rows"][0]["part_description"] == "rear cover"
        text = fake.calls[0]["messages"][0]["content"][0]["text"]
        assert "{{MERGED_STEPS}}" in load_prompt("finalize") and "{{" not in text
        assert text.startswith("Merge the extracted steps and component observations with the visual verification results to create the final outputs for the teardown of Cognex In-Sight 2800.")
        assert '"action": "remove"' in text and "long note about the cover" in text
        assert '"additional_details": "black"' in text
        assert "the report adds 1)" in text
        assert "- Enclosure: External housing or casing" in text, "PART_TYPES_WITH_DESCRIPTIONS renders the vocab descriptions"
        assert "- Molding - Plastics" in text and "- Aluminum" in text
        assert text.count(FENCE + "text") == 1
        assert fake.calls[0]["system"] == [{"text": load_prompt("system_boundary").strip()}]
        assert fake.calls[0]["inferenceConfig"] == {"maxTokens": 32000}
        assert list(usage.per_stage) == ["finalize"]

    def test_max_tokens_retries_once_with_a_compacted_draft_then_fails_readably(self):
        from video_sop_bom_pipeline import bedrock
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake = FakeBedrock(lambda request, n: bare_response("max_tokens"))
        with pytest.raises(PipelineRejection) as raised:
            bedrock.finalize(make_clients(bedrock=fake), MODEL, [_step()], [_component()], [], CONFIG, "P")
        assert len(fake.calls) == 2
        first = fake.calls[0]["messages"][0]["content"][0]["text"]
        second = fake.calls[1]["messages"][0]["content"][0]["text"]
        assert "long note about the cover" in first and "long note about the cover" not in second
        assert raised.value.code == "VideoSopBomModelOutputInvalid"
        assert raised.value.cause == "finalize output exceeded 32K tokens for 1 components / 1 steps"
