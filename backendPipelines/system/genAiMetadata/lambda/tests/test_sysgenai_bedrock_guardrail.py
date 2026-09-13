#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The shared guardrail helper both Converse callers of this pipeline use (generateMetadata and the media image's
segment function, which carries a byte-identical copy): the configuration is read from the environment with a
both-or-neither rule, the untrusted prompt parts travel in a guardContent block only when a guardrail is
configured, an intervention is recognised by its stopReason, and its cause carries the reply and the trace."""

import pytest

import sysgenai_harness as h

gr = h.load_local("bedrockGuardrail")


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


@pytest.mark.unit
class TestBlocks:
    def test_without_a_guardrail_one_text_block_carries_both_parts_in_order(self):
        assert gr.user_content_blocks("instruction", "untrusted", None) == [{"text": "instruction\nuntrusted"}]
        assert gr.user_content_blocks("instruction", "", None) == [{"text": "instruction"}]

    def test_with_a_guardrail_the_untrusted_part_is_a_guard_content_block(self):
        config = {"guardrailIdentifier": "gr", "guardrailVersion": "1", "trace": "enabled"}
        blocks = gr.user_content_blocks("instruction", "untrusted", config)
        assert blocks == [{"text": "instruction"},
                          {"guardContent": {"text": {"text": "untrusted", "qualifiers": ["guard_content"]}}}]
        assert gr.user_content_blocks("instruction", "", config) == [{"text": "instruction"}]
        assert gr.GUARD_CONTENT_QUALIFIERS == ["guard_content"]


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
