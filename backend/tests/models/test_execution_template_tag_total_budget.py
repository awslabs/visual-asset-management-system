# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The aggregate bound on one pipeline's templateTags list, at execute time.

The per-entry bounds multiply out far past what one DynamoDB item can hold (250 entries x 65536
characters), so they alone do not keep the tag list storable. The list is persisted verbatim on the
config-snapshot record beside the rendered config, and that record is written AFTER start_execution:
an oversized list is trimmed by the record builder's shared collection budget and flagged, which
leaves a re-run rendering a different configuration from the original. MAX_TEMPLATE_TAGS_TOTAL_LENGTH
is the request-side bound that keeps the rejection a 400 before launch, and
executionRecords.MAX_ITEM_COLLECTION_BYTES is sized as that bound plus the two config blocks so a
request the model accepts is stored whole. The two measures are not identical to the byte: the bound
sums the entries, the builder serializes the list, so the list's own punctuation (two bytes per entry)
is outside the bound and the stored copy is still trimmed if the tag list and both config blocks are
at their maximum at once.

Every tightening case is paired with a legitimate one: a tag may carry a long GenAI prompt, so the
bound must not reject ordinary authoring.
"""

import json

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.common.workflows import executionRecords as er
from backend.backend.models import executions as em
from backend.backend.models import pipelines as pm


def serialized_total(entries):
    """The measure the bound applies to: each entry as supplied, serialized."""
    return sum(len(json.dumps(e, default=str)) for e in entries)


def tag_list(count, value_length, extra_key_length=0):
    """`count` tag entries whose values are each well under MAX_TEMPLATE_TAG_VALUE_LENGTH, so only
    the aggregate bound can reject them."""
    entries = []
    for i in range(count):
        entry = {"key": "K%d" % i, "value": "v" * value_length}
        if extra_key_length:
            # A non-contract key is dropped by the model (extra='ignore') but IS persisted, because
            # the request's parameter blocks stay raw dicts on the way to the config snapshot.
            entry["junk"] = "x" * extra_key_length
        entries.append(entry)
    return entries


def tag_list_of_exact_length(total):
    """Three tag entries whose serialized total is exactly `total`, each value well under
    MAX_TEMPLATE_TAG_VALUE_LENGTH so only the aggregate bound can reject the list."""
    overhead = len(json.dumps({"key": "K0", "value": ""}, default=str)) * 3
    value_chars = total - overhead
    lengths = [value_chars // 3 + (1 if i < value_chars % 3 else 0) for i in range(3)]
    return [{"key": "K%d" % i, "value": "v" * lengths[i]} for i in range(3)]


@pytest.mark.unit
class TestTemplateTagAggregateBound:
    def test_an_over_budget_tag_list_is_rejected_on_the_execute_request(self):
        over = tag_list(5, 60000)
        assert serialized_total(over) > em.MAX_TEMPLATE_TAGS_TOTAL_LENGTH
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
                "pipe1": {"templateId": "tmpl-a", "templateTags": over}})

    def test_an_over_budget_tag_list_is_rejected_on_the_parameter_sub_model(self):
        # The blocks are typed Dict[str, Any] on the request, so the sub-model must carry the rule
        # itself rather than relying on the request path to apply it.
        with pytest.raises(ValidationError):
            em.PipelineExecutionParameters(templateTags=tag_list(3, 44000))

    def test_a_list_one_character_over_the_cap_is_rejected(self):
        # The boundary is the declared constant itself, not a round number near it: one character past
        # it is refused while every individual value stays far under the per-value cap, so only the
        # aggregate rule can be doing the work.
        over = tag_list_of_exact_length(em.MAX_TEMPLATE_TAGS_TOTAL_LENGTH + 1)
        assert serialized_total(over) == em.MAX_TEMPLATE_TAGS_TOTAL_LENGTH + 1
        assert max(len(e["value"]) for e in over) < em.MAX_TEMPLATE_TAG_VALUE_LENGTH
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(
                pipelineExecutionParameters={"pipe1": {"templateTags": over}})

    def test_non_contract_keys_count_against_the_budget(self):
        # Every value is tiny, so only the entries' non-contract keys can push the list over. Those
        # keys reach the stored row, so measuring the parsed entries instead would miss this.
        over = tag_list(3, 1, extra_key_length=60000)
        assert serialized_total(over) > em.MAX_TEMPLATE_TAGS_TOTAL_LENGTH
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(
                pipelineExecutionParameters={"pipe1": {"templateTags": over}})

    def test_an_over_budget_list_the_builder_would_trim_can_no_longer_reach_it(self):
        # The builder's shared collection budget trims the list and flags the row -- the divergent
        # re-run this bound exists to prevent. The request must not get that far.
        over = tag_list(5, 60000)
        record = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="{}",
            input_configuration_file_s3_key="k", template_tags=over)
        assert record["templateTagsTruncated"] is True
        with pytest.raises(ValidationError):
            em.ExecuteWorkflowRequestV2Model(
                pipelineExecutionParameters={"pipe1": {"templateTags": over}})


@pytest.mark.unit
class TestTemplateTagAggregateBoundAdmitsLegitimateInput:
    """Positive controls: the bound must not turn ordinary tag authoring into a 400."""

    def test_a_list_at_exactly_the_cap_is_accepted(self):
        # Paired with the one-character-over case, so the bound is a boundary rather than an outage:
        # the cap is inclusive, and the accepted maximum is the number the record budget is sized on.
        at_cap = tag_list_of_exact_length(em.MAX_TEMPLATE_TAGS_TOTAL_LENGTH)
        assert serialized_total(at_cap) == em.MAX_TEMPLATE_TAGS_TOTAL_LENGTH
        request = em.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
            "pipe1": {"templateId": "tmpl-a", "templateTags": at_cap}})
        assert len(request.pipelineExecutionParameters["pipe1"]["templateTags"]) == 3

    def test_a_realistic_tag_block_is_accepted(self):
        request = em.ExecuteWorkflowRequestV2Model(pipelineExecutionParameters={
            "pipe1": {"templateId": "tmpl-a", "templateTags": [
                {"key": "PROMPT", "value": "p" * 20000},
                {"key": "STEPS", "value": 300},
                {"key": "FLAG", "value": True},
                {"key": "RATE", "value": 1.5},
                {"key": "LIST", "value": ["a", "b"]},
                {"key": "NONE", "value": None},
                {"key": "UNICODE", "value": "éà中文 \U0001F389"},
            ]}})
        assert request.pipelineExecutionParameters["pipe1"]["templateId"] == "tmpl-a"

    def test_omitted_empty_and_null_tag_lists_are_unaffected(self):
        assert em.PipelineExecutionParameters().templateTags == []
        assert em.PipelineExecutionParameters(templateTags=[]).templateTags == []
        assert em.PipelineExecutionParameters(templateTags=None).templateTags is None

    def test_the_per_entry_bounds_still_fire(self):
        # The aggregate rule runs before the field's own validation, so it must not short-circuit
        # either per-entry bound: a tiny-but-numerous list is still capped by count, and one
        # oversized value is still capped on its own.
        with pytest.raises(ValidationError):
            em.PipelineExecutionParameters(templateTags=[
                {"key": "k%d" % i, "value": "v"}
                for i in range(em.MAX_TEMPLATE_TAGS_PER_PIPELINE + 1)])
        with pytest.raises(ValidationError):
            em.PipelineExecutionParameters(templateTags=[
                {"key": "K", "value": "v" * (em.MAX_TEMPLATE_TAG_VALUE_LENGTH + 1)}])

    def test_a_non_list_still_reaches_the_type_check(self):
        # The aggregate rule passes a non-list through untouched so the field's own type error is
        # what the caller sees, rather than a confusing size message.
        with pytest.raises(ValidationError):
            em.PipelineExecutionParameters(templateTags="notalist")


@pytest.mark.unit
class TestTemplateTagBoundIsWiredToTheRecordBudget:
    def test_the_record_collection_budget_admits_the_accepted_maximum(self):
        # executionRecords sizes MAX_ITEM_COLLECTION_BYTES as the tag cap plus the two config
        # blocks, so the request-side cap is the number that keeps a stored row whole. If either
        # constant moves alone, the builder starts trimming requests the model accepted.
        assert er.MAX_ITEM_COLLECTION_BYTES == (
            em.MAX_TEMPLATE_TAGS_TOTAL_LENGTH + 2 * pm.MAX_CONFIG_BLOCK_BYTES)
