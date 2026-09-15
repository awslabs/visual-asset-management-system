# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Save-time rules on a pipeline template's `overrides` block and its `tagSchema` entries.

Three rules, each paired with a legitimate-input test so a bound cannot be tightened into rejecting
what the system stores today — including the built-in templates, which the CDK registration replays
through these same models as SYSTEM_USER cross-calls:

  - OVERRIDES SIZE. `overrides` is stored whole on the template row beside an inline body of up to
    templateBodyStorage.INLINE_THRESHOLD_BYTES, so it carries the same serialized-size budget its
    systemConfig sibling does. Without it the caps on inputFileFilters multiply out to ~257 KB and the
    item is unpersistable — a 500 from put_item on a request that passed validation.
  - UNKNOWN KEY IN A TAG DEFINITION. The definition is persisted verbatim and every reader resolves a
    named key with .get(), so a misspelled key is neither read nor reported: `requried` leaves the tag
    optional and a run supplying no value renders it empty, and `Type` leaves it a string.
  - BODY / TAG-SCHEMA CORRESPONDENCE. Deliberately not checked in either direction. A declared tag the
    body never references is simply not substituted, and a `{{tag}}` the schema does not declare is
    left alone rather than rejected at save; the JSON-shape gate still applies to the structure around
    such a placeholder.
"""

import glob
import json
import os

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.common.workflows import templateBodyStorage as tbs
from backend.backend.models import pipelines as pm

# The CDK pipeline-registration schemas, whose templates go through these models on import.
_PIPELINES_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backendPipelines"))

# An overrides block whose two filter lists sit exactly at the per-list and per-pattern caps.
_MAXED_FILTERS = {"inputFileFilters": {"allow": ["a" * pm.MAX_INPUT_FILE_FILTER_PATTERN_LENGTH]
                                       * pm.MAX_INPUT_FILE_FILTER_PATTERNS,
                                       "exclude": ["b" * pm.MAX_INPUT_FILE_FILTER_PATTERN_LENGTH]
                                       * pm.MAX_INPUT_FILE_FILTER_PATTERNS}}

# The largest overrides block the size bound admits, for measuring a row at every declared maximum.
_MAXED_FILTERS_UNDER_BOUND = {"inputFileFilters": {
    "allow": ["a" * pm.MAX_INPUT_FILE_FILTER_PATTERN_LENGTH]
    * (pm.MAX_CONFIG_BLOCK_BYTES // (pm.MAX_INPUT_FILE_FILTER_PATTERN_LENGTH + 4))}}


def _create(**kw):
    base = {"templateName": "t", "configFormat": "yaml", "configBody": "x: 1"}
    base.update(kw)
    return pm.CreateTemplateRequestModel(**base)


# ==================== overrides serialized-size bound ====================

@pytest.mark.unit
class TestTemplateOverridesSizeBound:
    def test_maxed_filter_lists_exceed_the_bound(self):
        # The premise of the tests below: every per-list and per-pattern cap is satisfied, so nothing
        # else in the model rejects this block.
        assert len(json.dumps(_MAXED_FILTERS).encode("utf-8")) > pm.MAX_CONFIG_BLOCK_BYTES

    def test_oversized_overrides_rejected_on_create(self):
        with pytest.raises(ValidationError) as excinfo:
            _create(overrides=_MAXED_FILTERS)
        message = str(excinfo.value)
        assert "overrides" in message and str(pm.MAX_CONFIG_BLOCK_BYTES) in message

    def test_oversized_overrides_rejected_on_update(self):
        with pytest.raises(ValidationError):
            pm.UpdateTemplateRequestModel(overrides=_MAXED_FILTERS)

    def test_the_bound_matches_the_system_config_sibling(self):
        # overrides sets a subset of systemConfig's keys and is snapshotted alongside it per execution,
        # so a block the pipeline could not declare must not be reachable through a template either.
        over = {"inputFileFilters": {"allow": ["p" * 500] * 200}}
        assert len(json.dumps(over).encode("utf-8")) > pm.MAX_CONFIG_BLOCK_BYTES
        with pytest.raises(ValidationError):
            _create(overrides=over)
        with pytest.raises(ValidationError):
            pm.CreatePipelineRequestModel(
                databaseId="mydb1", pipelineName="P",
                executionConfig={"executionType": "Lambda"}, systemConfig=over)

    def test_a_realistic_overrides_block_still_passes(self):
        # POSITIVE CONTROL. The shape a template author writes, and the shape the built-in
        # cosmos3 templates register.
        request = _create(overrides={
            "inputFileArity": "multi",
            "assetScope": {"crossAssetAllowed": True, "singleAssetOnly": False},
            "metadataInputs": {"assetMetadata": True},
            "inputFileFilters": {"allow": ["*.glb", "*.usdz"], "exclude": ["*.tmp"]},
        })
        assert request.overrides["inputFileArity"] == "multi"

    def test_the_full_pattern_count_still_passes_at_a_realistic_pattern_length(self):
        # POSITIVE CONTROL for the bound's own rationale: 64 KB admits every one of the 250 permitted
        # patterns, so the size cap narrows nothing an author would write — only the arithmetic
        # extreme where each pattern is hundreds of characters long.
        request = _create(overrides={
            "inputFileFilters": {"allow": ["*.ext%03d" % i
                                           for i in range(pm.MAX_INPUT_FILE_FILTER_PATTERNS)]}})
        assert len(request.overrides["inputFileFilters"]["allow"]) == \
            pm.MAX_INPUT_FILE_FILTER_PATTERNS

    def test_overrides_just_under_the_bound_still_passes(self):
        # POSITIVE CONTROL on the boundary: the cap is inclusive, so a block at it is accepted.
        filler = "f" * (pm.MAX_INPUT_FILE_FILTER_PATTERN_LENGTH - 1)
        over = {"inputFileFilters": {"allow": [filler] * 100}}
        assert len(json.dumps(over).encode("utf-8")) < pm.MAX_CONFIG_BLOCK_BYTES
        assert _create(overrides=over).overrides["inputFileFilters"]["allow"][0] == filler

    def test_the_bounded_row_fields_still_fit_one_dynamodb_item(self):
        # The other half of what the size bound is for: templateBodyStorage's inline threshold reserves
        # headroom for the co-resident fields rather than measuring them, so the reserve holds only as
        # long as every one of those fields is itself bounded and the caps still sum under the item
        # limit. Derived from the live declarations, so raising any one of them fails here instead of
        # at put_item — the 500 this bound exists to prevent.
        # max_length counts CODE POINTS and DynamoDB counts UTF-8 BYTES, and none of these fields
        # carries a regex restricting it to ASCII (validate_no_control_characters bars only C0/C1),
        # so each declared length is multiplied by the 4-byte worst case a code point can encode to.
        fields = pm.CreateTemplateRequestModel.__fields__
        declared = {name: fields[name].field_info.max_length
                    for name in ("templateId", "templateName", "description", "inputInstructions")}
        assert all(v for v in declared.values()), declared
        worst_case_row = (
            tbs.INLINE_THRESHOLD_BYTES          # configBody + webFormJson, measured combined
            + pm.MAX_CONFIG_BLOCK_BYTES         # overrides
            + 4 * sum(declared.values())
            + 2 * 64                            # configBodyHash + webFormHash (sha256 hex)
            + 2 * 63 + 1                        # the pipelineDatabaseId:pipelineId partition key
            + 4 * 2 * 256                       # createdBy + modifiedBy (USERID max, unicode \w)
            + 1024                              # attribute names, timestamps, flags, storage keys
        )
        # DynamoDB's per-item ceiling, which counts attribute names and values together.
        assert worst_case_row < 400 * 1024, worst_case_row

    def test_an_assembled_row_over_the_item_limit_is_rejected_before_the_write(self):
        # The runtime half of the same invariant: the sum above holds only for the fields it
        # enumerates, so the assembled row is also measured. A row over the limit raises rather than
        # reaching put_item, whose ValidationException would be reported as a server fault.
        row = {"pipelineDatabaseId:pipelineId": "db:pipe", "templateId": "t1",
               "configBody": "x" * (tbs.MAX_ITEM_BYTES + 1)}
        with pytest.raises(tbs.TemplateRowTooLargeError):
            tbs.assert_row_within_item_limit(row)

    def test_a_row_at_the_declared_maxima_is_accepted(self):
        # POSITIVE CONTROL: the guard above must not fire on a row every declared bound permits —
        # an at-threshold inline body, a full overrides block, and the free-text fields at their
        # declared maxima in 4-byte characters.
        fields = pm.CreateTemplateRequestModel.__fields__
        row = {
            "pipelineDatabaseId:pipelineId": "d" * 63 + ":" + "p" * 63,
            "templateId": "t" * fields["templateId"].field_info.max_length,
            "templateName": "\U0001f600" * fields["templateName"].field_info.max_length,
            "description": "\U0001f600" * fields["description"].field_info.max_length,
            "inputInstructions": "\U0001f600" * fields["inputInstructions"].field_info.max_length,
            "configBody": "x" * tbs.INLINE_THRESHOLD_BYTES,
            "webFormJson": "",
            "overrides": _MAXED_FILTERS_UNDER_BOUND,
            "configBodyHash": "0" * 64, "webFormHash": "0" * 64,
            "createdBy": "\U0001f600" * 256, "modifiedBy": "\U0001f600" * 256,
            "bodyStorage": "inline", "configBodyS3Key": "", "webFormS3Key": "",
            "isDefault": False, "allowCustomEdit": False, "schemaVersion": 1,
            "dateCreated": "2026-01-01T00:00:00Z", "dateModified": "2026-01-01T00:00:00Z",
        }
        assert tbs.assert_row_within_item_limit(row) < tbs.MAX_ITEM_BYTES


# ==================== unknown key inside a tag definition ====================

@pytest.mark.unit
class TestTagSchemaEntryKeys:
    """TemplateTagFieldModel is extra='ignore' (the model convention), so the unrecognized key has to
    be rejected while the caller's raw dicts are still in hand — on the template create/update bodies
    and, pre-parse, on the tag-schema PUT."""

    def test_misspelled_required_rejected_on_create(self):
        with pytest.raises(ValidationError) as excinfo:
            _create(tagSchema=[{"tagKey": "PROMPT", "type": "string", "requried": True}])
        message = str(excinfo.value)
        # Names the offending key and the allowed set, the way the sibling unknown-key errors do.
        assert "requried" in message and "tagKey" in message

    def test_misspelled_type_rejected_on_create(self):
        # `Type` is dropped, normalize_tag_type(None) returns "string", and an integer tag silently
        # becomes text.
        with pytest.raises(ValidationError):
            _create(tagSchema=[{"tagKey": "STEPS", "Type": "integer", "required": True}])

    def test_misspelled_default_rejected_on_update(self):
        with pytest.raises(ValidationError):
            pm.UpdateTemplateRequestModel(
                tagSchema=[{"tagKey": "PROMPT", "type": "string", "defualt": "x"}])

    def test_misspelled_required_rejected_on_the_set_tag_schema_path(self):
        # This path persists the PARSED models (json.loads(f.json())), so extra='ignore' has already
        # discarded the key by the time the typed list exists.
        with pytest.raises(ValidationError) as excinfo:
            pm.SetTagSchemaRequestModel(
                fields=[{"tagKey": "PROMPT", "type": "string", "requried": True}])
        assert "requried" in str(excinfo.value)

    def test_every_declared_key_is_accepted(self):
        # POSITIVE CONTROL. All seven declared keys on one entry, on both paths.
        entry = {"tagKey": "MODE", "type": "enum", "required": True, "default": "fast",
                 "label": "Mode", "description": "Generation mode.", "enumValues": ["fast", "slow"]}
        assert set(entry) == set(pm.TemplateTagFieldModel.__fields__)
        request = _create(tagSchema=[dict(entry)])
        # The entries stay plain dicts: templateTagSchema.validate_tag_schema inspects dicts and the
        # handler persists them verbatim.
        assert request.tagSchema[0] == entry
        assert pm.SetTagSchemaRequestModel(fields=[dict(entry)]).fields[0].tagKey == "MODE"

    def test_a_partial_definition_is_still_accepted(self):
        # POSITIVE CONTROL. Every key but tagKey is optional; the shipped templates declare five of
        # the seven.
        assert _create(tagSchema=[{"tagKey": "PROMPT"}]).tagSchema[0]["tagKey"] == "PROMPT"
        assert _create(tagSchema=[
            {"tagKey": "PROMPT", "type": "string", "required": True, "label": "Prompt",
             "description": "The generation prompt."},
            {"tagKey": "STEPS", "type": "integer", "default": 300},
        ]).tagSchema[1]["default"] == 300

    def test_the_field_count_cap_still_applies_on_the_set_tag_schema_path(self):
        # POSITIVE CONTROL on the pre-parse hook's blast radius: the count cap and the at-cap
        # acceptance both still hold on the path the hook was added to.
        with pytest.raises(ValidationError):
            pm.SetTagSchemaRequestModel(
                fields=[{"tagKey": "k%d" % i} for i in range(pm.MAX_TAG_SCHEMA_FIELDS + 1)])
        at_cap = pm.SetTagSchemaRequestModel(
            fields=[{"tagKey": "k%d" % i} for i in range(pm.MAX_TAG_SCHEMA_FIELDS)])
        assert len(at_cap.fields) == pm.MAX_TAG_SCHEMA_FIELDS

    def test_a_malformed_fields_value_is_still_rejected(self):
        # POSITIVE CONTROL: the pre-parse hook must not swallow a type error. A non-list reaches the
        # typed field; a non-object entry is named with its own index.
        with pytest.raises(ValidationError):
            pm.SetTagSchemaRequestModel(fields="notalist")
        with pytest.raises(ValidationError):
            pm.SetTagSchemaRequestModel(fields=["notadict"])
        assert pm.SetTagSchemaRequestModel().fields == []

    def test_an_entry_supplied_as_a_parsed_model_is_still_accepted(self):
        # POSITIVE CONTROL on the pre-parse hook's blast radius. `fields` is typed
        # List[TemplateTagFieldModel], so a caller may hand it models rather than dicts; the hook reads
        # the raw dicts and must not turn that shape into a rejection. A parsed model carries only
        # declared keys, so there is nothing for the unknown-key rule to find on one anyway.
        request = pm.SetTagSchemaRequestModel(
            fields=[pm.TemplateTagFieldModel(tagKey="MODE", type="integer", default=3),
                    {"tagKey": "PROMPT", "type": "string"}])
        assert [f.tagKey for f in request.fields] == ["MODE", "PROMPT"]
        assert request.fields[0].default == 3

    def test_the_reported_index_names_the_entry_the_caller_wrote(self):
        # The hook reports an index into the list it was handed, so normalizing an entry must preserve
        # position — otherwise a mixed list points the operator at the wrong tag definition.
        with pytest.raises(ValidationError) as excinfo:
            pm.SetTagSchemaRequestModel(
                fields=[pm.TemplateTagFieldModel(tagKey="MODE"),
                        {"tagKey": "PROMPT", "requried": True}])
        assert "tagSchema[1]" in str(excinfo.value)

    def test_every_built_in_template_still_saves_with_its_overrides_and_tag_schema(self):
        # POSITIVE CONTROL for both rules on the path that matters most: the CDK registration replays
        # each shipped template through CreateTemplateRequestModel, so a rejection here fails the
        # stack's import custom resource rather than surfacing in any API test.
        files = sorted(set(glob.glob(
            os.path.join(_PIPELINES_ROOT, "**", "templates", "*.json"), recursive=True)))
        # A floor rather than an exact count: a glob silently matching nothing would pass the loop
        # while validating nothing.
        assert len(files) >= 25, f"expected the shipped template schemas, found {len(files)}"
        rejected = []
        with_overrides = 0
        with_tag_schema = 0
        for path in files:
            body = json.load(open(path, encoding="utf-8"))
            if body.get("overrides"):
                with_overrides += 1
            if body.get("tagSchema"):
                with_tag_schema += 1
            try:
                pm.CreateTemplateRequestModel(
                    templateName=body.get("templateName") or "t",
                    configFormat=body.get("configFormat", "json"),
                    configBody=body.get("configBody", ""),
                    webFormJson=body.get("webFormJson") or "",
                    inputInstructions=body.get("inputInstructions") or "",
                    overrides=body.get("overrides") or {},
                    tagSchema=body.get("tagSchema"))
            except Exception as e:
                rejected.append(f"{os.path.relpath(path, _PIPELINES_ROOT)}: {e}")
        assert not rejected, rejected
        # Positive control for the two arguments above: without a shipped overrides block and a
        # shipped tagSchema, neither rule is exercised here and this test would pass with both
        # checks disabled.
        assert with_overrides >= 1, "no shipped template declares overrides"
        assert with_tag_schema >= 1, "no shipped template declares a tagSchema"


# ==================== body / tag-schema correspondence (deliberately absent) ====================

@pytest.mark.unit
class TestTemplateBodyTagSchemaCorrespondence:
    """Neither direction of correspondence is required at save.

    A declared tag the body never references is ignored — it is collected at execute time and simply
    not substituted — and a `{{tag}}` the schema does not declare is left in the body rather than
    rejected. Both save with a 200."""

    _SCHEMA = [{"tagKey": "PROMPT", "type": "string", "required": True}]

    def _json(self, body, **kw):
        return pm.CreateTemplateRequestModel(
            templateName="t", configFormat="json", configBody=body, **kw)

    def test_a_declared_tag_absent_from_the_body_is_accepted(self):
        request = self._json('{"x": 1}', tagSchema=self._SCHEMA)
        assert request.tagSchema[0]["tagKey"] == "PROMPT"
        assert "PROMPT" not in request.configBody

    def test_an_undeclared_body_tag_is_accepted(self):
        # A misspelled placeholder, and a body whose only tags are undeclared: both save.
        assert self._json('{"p": "{{PROMT}}"}', tagSchema=self._SCHEMA)
        assert self._json('{"p": "{{ANYTHING}}", "q": "{{OTHER}}"}')

    def test_an_undeclared_body_tag_is_accepted_on_update(self):
        assert pm.UpdateTemplateRequestModel(
            configFormat="json", configBody='{"p": "{{PROMT}}"}', tagSchema=self._SCHEMA)

    def test_a_declared_and_referenced_tag_is_accepted(self):
        # POSITIVE CONTROL: the ordinary case still saves.
        request = self._json('{"p": "{{PROMPT}}"}', tagSchema=self._SCHEMA)
        assert "{{PROMPT}}" in request.configBody

    def test_the_json_shape_gate_still_applies_around_an_undeclared_tag(self):
        # Accepting an undeclared name does not relax the structure check: a text stand-in parses only
        # inside the template's own quotes, so the bare-value form is still rejected — for the shape of
        # the surrounding JSON, not for the tag being undeclared.
        with pytest.raises(ValidationError) as excinfo:
            self._json('{"p": {{PROMT}}}', tagSchema=self._SCHEMA)
        assert "belongs inside the JSON string it fills" in str(excinfo.value)
