#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The shared guardrail helper both Converse callers of this pipeline use (generateMetadata and the media image's
segment function, which carries a byte-identical copy): the configuration is read from the environment with a
both-or-neither rule, the untrusted prompt parts (text and images) travel in guardContent blocks only when a
guardrail is configured, an intervention is recognised by its stopReason, and its cause carries the reply and
the filters of the trace but never the text a filter matched."""

import json

import pytest

import sysgenai_harness as h

gr = h.load_local("bedrockGuardrail")

CONFIG = {"guardrailIdentifier": "gr", "guardrailVersion": "1", "trace": "enabled"}
PNG = {"image": {"format": "png", "source": {"bytes": b"\x89PNG\r\n\x1a\n" + b"\x01" * 16}}}


@pytest.mark.unit
class TestConfiguration:
    def test_no_variables_means_no_guardrail(self):
        assert gr.guardrail_config_from_env({}) is None
        assert gr.guardrail_config_from_env({"BEDROCK_GUARDRAIL_IDENTIFIER": " ", "BEDROCK_GUARDRAIL_VERSION": ""}) is None

    def test_both_variables_make_a_traced_config(self):
        config = gr.guardrail_config_from_env({"BEDROCK_GUARDRAIL_IDENTIFIER": " gr-abc123 ",
                                               "BEDROCK_GUARDRAIL_VERSION": "2"})
        assert config == {"guardrailIdentifier": "gr-abc123", "guardrailVersion": "2", "trace": "enabled"}

    @pytest.mark.parametrize("env", [{"BEDROCK_GUARDRAIL_IDENTIFIER": "gr-abc123"},
                                     {"BEDROCK_GUARDRAIL_VERSION": "DRAFT"}])
    def test_one_variable_without_the_other_is_an_error(self, env):
        with pytest.raises(ValueError, match="BEDROCK_GUARDRAIL_IDENTIFIER and BEDROCK_GUARDRAIL_VERSION"):
            gr.guardrail_config_from_env(env)

    def test_the_unconfigured_warning_names_both_variables(self):
        """The line a caller logs once at cold start when no guardrail is configured."""
        assert "BEDROCK_GUARDRAIL_IDENTIFIER" in gr.GUARDRAIL_UNCONFIGURED_WARNING
        assert "BEDROCK_GUARDRAIL_VERSION" in gr.GUARDRAIL_UNCONFIGURED_WARNING
        assert "prompt-attack" in gr.GUARDRAIL_UNCONFIGURED_WARNING


@pytest.mark.unit
class TestBlocks:
    def test_without_a_guardrail_one_text_block_carries_both_parts_in_order(self):
        assert gr.user_content_blocks("instruction", "untrusted", None) == [{"text": "instruction\nuntrusted"}]
        assert gr.user_content_blocks("instruction", "", None) == [{"text": "instruction"}]

    def test_with_a_guardrail_the_untrusted_part_is_a_guard_content_block(self):
        blocks = gr.user_content_blocks("instruction", "untrusted", CONFIG)
        assert blocks == [{"text": "instruction"},
                          {"guardContent": {"text": {"text": "untrusted", "qualifiers": ["guard_content"]}}}]
        assert gr.user_content_blocks("instruction", "", CONFIG) == [{"text": "instruction"}]
        assert gr.GUARD_CONTENT_QUALIFIERS == ["guard_content"]

    def test_guard_image_block_wraps_a_converse_image_block(self):
        """The guardContent image form is format plus source bytes (no qualifiers, unlike text), and the bytes
        are the image's own."""
        block = gr.guard_image_block(PNG)
        assert block == {"guardContent": {"image": {"format": "png", "source": {"bytes": PNG["image"]["source"]["bytes"]}}}}
        assert block["guardContent"]["image"]["source"] is not PNG["image"]["source"]

    def test_without_a_guardrail_the_images_pass_through_and_with_one_every_image_is_guarded(self):
        """Once any block of a message is tagged the guardrail evaluates the tagged blocks only, so with a guardrail
        no image may stay an untagged image block."""
        second = {"image": {"format": "png", "source": {"bytes": b"\x02" * 8}}}
        assert gr.user_image_blocks([PNG, second], None) == [PNG, second]
        guarded = gr.user_image_blocks([PNG, second], CONFIG)
        assert [list(block) for block in guarded] == [["guardContent"], ["guardContent"]]
        assert [block["guardContent"]["image"]["source"]["bytes"] for block in guarded] == [
            PNG["image"]["source"]["bytes"], second["image"]["source"]["bytes"]]
        assert gr.user_image_blocks([], CONFIG) == [] and gr.user_image_blocks([], None) == []


@pytest.mark.unit
class TestIntervention:
    def test_stop_reason_and_cause(self):
        response = {"output": {"message": {"content": [{"text": "Blocked."}]}}, "stopReason": "guardrail_intervened",
                    "trace": {"guardrail": {"inputAssessment": {"gr": {"contentPolicy": {"filters": [
                        {"type": "PROMPT_ATTACK", "action": "BLOCKED"}]}}}}}}
        assert gr.GUARDRAIL_STOP_REASON == "guardrail_intervened"
        assert gr.intervened(response) is True and gr.intervened({"stopReason": "end_turn"}) is False
        cause = gr.guardrail_cause(response)
        assert cause.startswith("guardrail intervened: Blocked.") and "PROMPT_ATTACK" in cause
        assert gr.guardrail_cause({"stopReason": "guardrail_intervened"}) == "guardrail intervened:"

    def test_the_cause_carries_each_filter_s_policy_type_action_and_confidence_and_nothing_it_matched(self):
        """A word, PII or regex filter's `match` is the prompt text it matched (file content) and a regex filter's
        `regex` is the operator's pattern; the model output the trace echoes is the blocked reply. None of these
        reaches the cause, which is written to the execution record and the logs."""
        matched = "Jane Q. Public, card 4111-1111-1111-1111"
        response = {
            "output": {"message": {"content": [{"text": "Blocked."}]}}, "stopReason": "guardrail_intervened",
            "trace": {"guardrail": {
                "modelOutput": ["The pump belongs to " + matched],
                "actionReason": "Guardrail blocked.",
                "inputAssessment": {"gr": {
                    "topicPolicy": {"topics": [{"name": "Competitors", "type": "DENY", "action": "BLOCKED", "detected": True}]},
                    "contentPolicy": {"filters": [
                        {"type": "PROMPT_ATTACK", "confidence": "HIGH", "filterStrength": "HIGH", "action": "BLOCKED",
                         "detected": True}]},
                    "wordPolicy": {"customWords": [{"match": "Jane Q. Public", "action": "BLOCKED", "detected": True}],
                                   "managedWordLists": [{"match": "damn", "type": "PROFANITY", "action": "BLOCKED",
                                                         "detected": True}]},
                    "sensitiveInformationPolicy": {
                        "piiEntities": [{"match": matched, "type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZED",
                                         "detected": True}],
                        "regexes": [{"name": "part-number", "match": "GP-100", "regex": r"GP-\d+", "action": "BLOCKED",
                                     "detected": True}]},
                    "invocationMetrics": {"guardrailProcessingLatency": 12, "usage": {"topicPolicyUnits": 1}}}},
                "outputAssessments": {"gr": [{"contextualGroundingPolicy": {"filters": [
                    {"type": "GROUNDING", "threshold": 0.7, "score": 0.1, "action": "BLOCKED", "detected": True}]}}]},
            }},
        }
        cause = gr.guardrail_cause(response)
        assert cause.startswith("guardrail intervened: Blocked. ")
        summary = json.loads(cause[len("guardrail intervened: Blocked. "):])
        assert summary == {
            "input": [
                {"policy": "topicPolicy", "type": "DENY", "action": "BLOCKED"},
                {"policy": "contentPolicy", "type": "PROMPT_ATTACK", "action": "BLOCKED", "confidence": "HIGH"},
                {"policy": "wordPolicy", "action": "BLOCKED"},
                {"policy": "wordPolicy", "type": "PROFANITY", "action": "BLOCKED"},
                {"policy": "sensitiveInformationPolicy", "type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZED"},
                {"policy": "sensitiveInformationPolicy", "action": "BLOCKED"},
            ],
            "output": [{"policy": "contextualGroundingPolicy", "type": "GROUNDING", "action": "BLOCKED"}],
        }
        for leaked in ("Jane", "4111", "damn", "GP-100", r"GP-\d+", "match", "modelOutput", "Competitors",
                       "actionReason", "invocationMetrics"):
            assert leaked not in cause, leaked

    def test_a_trace_without_filters_adds_nothing_to_the_cause(self):
        response = {"output": {"message": {"content": [{"text": "Blocked."}]}}, "stopReason": "guardrail_intervened",
                    "trace": {"guardrail": {"inputAssessment": {"gr": {"invocationMetrics": {"guardrailProcessingLatency": 3}}}}}}
        assert gr.guardrail_cause(response) == "guardrail intervened: Blocked."
        assert gr.assessment_summary(None) == {} and gr.assessment_summary({"guardrail": {}}) == {}
