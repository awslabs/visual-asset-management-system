#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The shared guardrail helper both Converse callers of this pipeline use (generateMetadata and the media image's
segment function, which carries a byte-identical copy): the configuration is read from the environment with a
both-or-neither rule, the untrusted prompt parts (text and images) travel in guardContent blocks only when a
guardrail is configured, an intervention is a BLOCKED filter on either side of the trace (a stop reason without
a trace counts as one; anonymized-only masking does not), the masked entity types are reported by type only, and
an intervention's cause carries the reply and the filters of the trace but never the text a filter matched."""

import json

import pytest

import sysgenai_harness as h

gr = h.load_local("bedrockGuardrail")

CONFIG = {"guardrailIdentifier": "gr", "guardrailVersion": "1", "trace": "enabled"}
PNG = {"image": {"format": "png", "source": {"bytes": b"\x89PNG\r\n\x1a\n" + b"\x01" * 16}}}


def _intervened(trace=None, text="Blocked."):
    response = {"output": {"message": {"content": [{"text": text}]}}, "stopReason": "guardrail_intervened"}
    if trace is not None:
        response["trace"] = trace
    return response


def _assessment(**policies):
    return {"guardrail": {"outputAssessments": {"gr": [dict(policies)]}}}


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
class TestVerdict:
    """Amazon Bedrock returns stopReason guardrail_intervened both when a filter blocks and when the
    sensitive-information filter anonymizes; the trace decides which happened."""

    def test_a_blocked_input_filter_is_an_intervention_with_a_cause(self):
        response = _intervened({"guardrail": {"inputAssessment": {"gr": {"contentPolicy": {"filters": [
            {"type": "PROMPT_ATTACK", "action": "BLOCKED", "confidence": "HIGH"}]}}}}})
        assert gr.intervened(response) is True
        assert gr.masked_entity_types(response) == []
        cause = gr.guardrail_cause(response)
        assert cause.startswith("guardrail intervened: Blocked.")
        assert json.loads(cause[len("guardrail intervened: Blocked. "):]) == {
            "input": [{"policy": "contentPolicy", "type": "PROMPT_ATTACK", "action": "BLOCKED", "confidence": "HIGH"}]}

    def test_an_anonymized_only_output_is_a_masked_success(self):
        """The message is the complete answer with {NAME}/{ADDRESS} tokens in place of the entities; the caller
        uses it as any other reply and records the masked types, never the matched values."""
        response = _intervened(_assessment(sensitiveInformationPolicy={"piiEntities": [
            {"match": "Jane Q. Public", "type": "NAME", "action": "ANONYMIZED", "detected": True},
            {"match": "John Public", "type": "NAME", "action": "ANONYMIZED", "detected": True},
            {"match": "Bay 4", "type": "ADDRESS", "action": "ANONYMIZED", "detected": True}]}),
            text='{"description": "Inspector {NAME} at Warehouse {ADDRESS} 4"}')
        assert gr.intervened(response) is False
        assert gr.masked_entity_types(response) == ["ADDRESS", "NAME"]
        assert "Jane" not in json.dumps(gr.masked_entity_types(response))

    def test_anonymized_on_the_input_side_is_a_masked_success_too(self):
        response = _intervened({"guardrail": {"inputAssessment": {"gr": {"sensitiveInformationPolicy": {"piiEntities": [
            {"match": "123-45-6789", "type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZED", "detected": True}],
            "regexes": [{"name": "badge", "match": "B-12", "regex": r"B-\d+", "action": "ANONYMIZED", "detected": True}]}}}}})
        assert gr.intervened(response) is False
        assert gr.masked_entity_types(response) == ["US_SOCIAL_SECURITY_NUMBER"]

    def test_a_blocked_filter_beside_anonymized_ones_is_an_intervention(self):
        response = _intervened({"guardrail": {
            "inputAssessment": {"gr": {"sensitiveInformationPolicy": {"piiEntities": [
                {"match": "x@example.com", "type": "EMAIL", "action": "ANONYMIZED", "detected": True}]}}},
            "outputAssessments": {"gr": [{"contentPolicy": {"filters": [
                {"type": "PROMPT_ATTACK", "action": "BLOCKED", "confidence": "MEDIUM"}]}}]}}})
        assert gr.intervened(response) is True
        assert gr.masked_entity_types(response) == ["EMAIL"]
        assert "BLOCKED" in gr.guardrail_cause(response) and "x@example.com" not in gr.guardrail_cause(response)

    @pytest.mark.parametrize("trace", [None, {}, {"guardrail": {}},
                                       {"guardrail": {"inputAssessment": {"gr": {"invocationMetrics": {"guardrailProcessingLatency": 3}}}}}])
    def test_the_stop_reason_without_a_filter_entry_is_an_intervention(self, trace):
        """Nothing says what the guardrail did, so the conservative reading holds."""
        response = _intervened(trace)
        assert gr.intervened(response) is True
        assert gr.masked_entity_types(response) == []

    def test_detect_only_entries_change_neither_verdict(self):
        """A filter in detect mode reports action NONE and takes none: beside an anonymized entry the response is
        still a masked success, alone with the stop reason it is still an intervention."""
        beside = _intervened(_assessment(sensitiveInformationPolicy={"piiEntities": [
            {"type": "PHONE", "action": "ANONYMIZED", "detected": True},
            {"type": "NAME", "action": "NONE", "detected": True}]}))
        assert gr.intervened(beside) is False and gr.masked_entity_types(beside) == ["PHONE"]
        alone = _intervened(_assessment(sensitiveInformationPolicy={"piiEntities": [
            {"type": "NAME", "action": "NONE", "detected": True}]}))
        assert gr.intervened(alone) is True

    def test_an_ordinary_reply_is_not_intervened(self):
        response = {"output": {"message": {"content": [{"text": "{}"}]}}, "stopReason": "end_turn"}
        assert gr.intervened(response) is False and gr.masked_entity_types(response) == []
        assert gr.intervened({}) is False and gr.intervened(None) is False
        assert gr.FILTER_ACTION_BLOCKED == "BLOCKED" and gr.FILTER_ACTION_ANONYMIZED == "ANONYMIZED"


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
