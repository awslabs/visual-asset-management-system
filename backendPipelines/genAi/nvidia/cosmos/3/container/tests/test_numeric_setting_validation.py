#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The numeric generation settings are coerced deliberately, and rejected before the expensive work.

Seed, frame count, guidance, control weight and control guidance reach the container as whatever
their source carried: a typed template tag as a JSON number, an asset-metadata value as a string.
Three of those coercions were silent before this:

*   `cosmosSeed = true` became seed **1** and the run succeeded,
*   `cosmosNumFrames = 3.9` became **3** frames and the run succeeded,
*   `cosmosControlWeight = "abc"` became weight **1.0** and the run succeeded,

while `cosmosGuidance = "high"` raised `could not convert string to float: 'high'` -- a message naming
neither the setting nor the metadata key an operator would have to correct.

A silent coercion is worse than the rejection it replaces: the run bills a GPU node, reports SUCCESS,
and delivers a generation nobody asked for. So each setting has a stated verdict:

| value                    | verdict                                                      |
| ------------------------ | ------------------------------------------------------------ |
| absent, blank, whitespace | the documented default                                      |
| `true` / `false`          | rejected -- a boolean in a numeric field is a type error     |
| `3.9` for a frame count   | rejected -- truncating to 3 changes what was asked for       |
| `93`, `"93"`, `93.0`      | accepted as 93                                               |
| `"high"`, `"nan"`, `"inf"` | rejected, naming the setting                                |
| a frame count below 1     | rejected                                                     |

Range checks stay minimal on purpose: only the frame count has a floor (a run of zero frames produces
nothing), and no setting has a ceiling here -- the templates document that too-long a sequence fails
during generation on a given instance type, which is a capacity question this container cannot settle.
"""

import pytest

from conftest import base_definition, transfer_definition


# ============================ the helper's verdicts ============================

class TestBlankAndAbsentMeanTheDefault:

    @pytest.mark.parametrize("raw", [None, "", "   ", "\t"])
    def test_a_missing_or_blank_value_yields_the_default(self, container, raw):
        assert container.parse_number_setting(raw, "COSMOS3_SEED", 7, integer=True) == 7

    def test_zero_is_a_value_not_a_blank(self, container):
        """`or default` idioms treat 0 as absent; an explicit seed of 0 must survive."""
        assert container.parse_number_setting("0", "COSMOS3_SEED", 42, integer=True) == 0

    def test_a_blank_guidance_stays_none_so_the_framework_uses_its_own_default(self, container):
        assert container.parse_number_setting("", "COSMOS3_GUIDANCE", None) is None


class TestAcceptedForms:

    @pytest.mark.parametrize("raw", [93, "93", " 93 ", 93.0, "93.0"])
    def test_a_whole_number_in_any_form_is_accepted(self, container, raw):
        assert container.parse_number_setting(
            raw, "COSMOS3_NUM_FRAMES", 189, integer=True, minimum=1) == 93

    @pytest.mark.parametrize("raw", [7.5, "7.5", "7", 7])
    def test_a_fractional_value_is_accepted_where_the_setting_is_not_an_integer(self, container, raw):
        assert container.parse_number_setting(raw, "COSMOS3_GUIDANCE", None) == float(raw)

    def test_a_negative_seed_is_accepted(self, container):
        """No floor on the seed: it is an opaque generator input, and rejecting negatives would refuse
        a value that reproduces a previous run."""
        assert container.parse_number_setting("-1", "COSMOS3_SEED", 0, integer=True) == -1


class TestRejectedForms:

    @pytest.mark.parametrize("raw", [True, False])
    def test_a_boolean_is_rejected_rather_than_read_as_one_or_zero(self, container, raw):
        """`bool` is a subclass of `int` in Python, so `int(True)` is 1 and a plain coercion accepts
        this. A JSON `true` in a numeric field is a template error, not a seed of 1."""
        with pytest.raises(ValueError, match="COSMOS3_SEED"):
            container.parse_number_setting(raw, "COSMOS3_SEED", 0, integer=True)

    @pytest.mark.parametrize("raw", [3.9, "3.9", -2.5])
    def test_a_fractional_value_for_an_integer_setting_is_rejected(self, container, raw):
        with pytest.raises(ValueError, match="whole number"):
            container.parse_number_setting(raw, "COSMOS3_NUM_FRAMES", 189, integer=True)

    @pytest.mark.parametrize("raw", ["high", "abc", "1,5", "93 frames", [], {}, "0x10"])
    def test_a_non_numeric_value_is_rejected(self, container, raw):
        with pytest.raises(ValueError, match="COSMOS3_GUIDANCE"):
            container.parse_number_setting(raw, "COSMOS3_GUIDANCE", None)

    @pytest.mark.parametrize("raw", ["nan", "inf", "-inf", float("nan"), float("inf")])
    def test_a_non_finite_value_is_rejected(self, container, raw):
        """`float("nan")` succeeds, so without this check the framework would be handed a NaN
        guidance."""
        with pytest.raises(ValueError, match="finite"):
            container.parse_number_setting(raw, "COSMOS3_GUIDANCE", None)

    @pytest.mark.parametrize("raw", [0, "0", -1])
    def test_a_frame_count_below_one_is_rejected(self, container, raw):
        with pytest.raises(ValueError, match="at least 1"):
            container.parse_number_setting(
                raw, "COSMOS3_NUM_FRAMES", 189, integer=True, minimum=1)

    def test_the_message_names_the_setting_an_operator_would_have_to_correct(self, container):
        """The raised text is what the execution record shows, so it has to name the metadata key
        rather than only the Python conversion that failed."""
        with pytest.raises(ValueError, match="COSMOS3_NUM_FRAMES"):
            container.parse_number_setting("many", "COSMOS3_NUM_FRAMES", 189, integer=True)


# ============================ the settings as main() reads them ============================

class TestSettingsReachInferenceCoerced:

    def test_the_shipped_defaults_are_used_when_nothing_is_supplied(self, run_container):
        record = run_container(base_definition())
        kwargs = record.inference_kwargs
        assert (kwargs["seed"], kwargs["num_frames"], kwargs["guidance"]) == (0, 189, None)

    def test_string_metadata_values_are_coerced_to_numbers(self, run_container):
        """The metadata route delivers strings; the framework arguments are numbers."""
        record = run_container(base_definition(
            cosmosSeed="7", cosmosNumFrames="93", cosmosGuidance="4.5"))
        kwargs = record.inference_kwargs
        assert (kwargs["seed"], kwargs["num_frames"], kwargs["guidance"]) == (7, 93, 4.5)
        assert isinstance(kwargs["seed"], int) and isinstance(kwargs["num_frames"], int)

    def test_a_transfer_run_coerces_its_control_weight_and_guidance(self, run_container):
        record = run_container(transfer_definition(
            cosmosControlWeight="0.75", cosmosControlGuidance="2"))
        kwargs = record.inference_kwargs
        assert kwargs["control_blocks"]["edge"]["weight"] == 0.75
        assert kwargs["control_guidance"] == 2.0

    def test_a_blank_control_weight_and_guidance_use_the_documented_defaults(self, run_container):
        record = run_container(transfer_definition(
            cosmosControlWeight="", cosmosControlGuidance=""))
        kwargs = record.inference_kwargs
        assert kwargs["control_blocks"]["edge"]["weight"] == 1.0
        assert kwargs["control_guidance"] == 1.5

    def test_weights_are_aligned_positionally_across_a_blend(self, run_container):
        record = run_container(transfer_definition(
            cosmosControlType="edge,blur", cosmosControlWeight="0.25,0.5"))
        blocks = record.inference_kwargs["control_blocks"]
        assert (blocks["edge"]["weight"], blocks["blur"]["weight"]) == (0.25, 0.5)


class TestBadValuesStopTheRunBeforeItCosts:
    """Each of these used to run to completion, with a silently substituted value."""

    @pytest.mark.parametrize("definition, setting", [
        (base_definition(cosmosSeed=True), "COSMOS3_SEED"),
        (base_definition(cosmosNumFrames=3.9), "COSMOS3_NUM_FRAMES"),
        (base_definition(cosmosNumFrames=0), "COSMOS3_NUM_FRAMES"),
        (base_definition(cosmosGuidance="high"), "COSMOS3_GUIDANCE"),
        (transfer_definition(cosmosControlWeight="abc"), "COSMOS3_CONTROL_WEIGHT"),
        (transfer_definition(cosmosControlGuidance="strong"), "COSMOS3_CONTROL_GUIDANCE"),
    ])
    def test_the_run_fails_naming_the_setting(self, run_container, definition, setting):
        with pytest.raises(ValueError, match=setting):
            run_container(definition)

    @pytest.mark.parametrize("definition", [
        base_definition(cosmosSeed=True),
        base_definition(cosmosNumFrames=3.9),
        transfer_definition(cosmosControlWeight="abc"),
        transfer_definition(cosmosControlGuidance="strong"),
    ])
    def test_nothing_downstream_of_the_check_runs(self, run_container, definition):
        """The point of validating early: no model restore, no inference, no upload. The control
        settings in particular were previously read in step 2b, AFTER the restore."""
        with pytest.raises(ValueError):
            run_container(definition)
        assert run_container.last.model_restores == []
        assert run_container.last.inference == []
        assert run_container.last.uploads == []

    def test_a_good_run_still_reaches_inference_and_upload(self, run_container):
        """Over-restriction control: a validator that rejected ordinary values would satisfy every
        test above and make the pipeline unusable."""
        record = run_container(base_definition(
            cosmosSeed=7, cosmosNumFrames=93, cosmosGuidance=4.5))
        assert len(record.model_restores) == 1
        assert len(record.inference) == 1
        assert len(record.uploads) == 1
