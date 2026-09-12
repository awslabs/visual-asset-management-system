#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""constructPipeline: the first state of the sub-state-machine.

Two halves. The configuration validator interprets config_schema.json (no jsonschema in the Lambda
runtime) and must say, in one sentence per violation, which value broke which bound. The handler
reads the manifest envelope itself, validates the rendered template configuration, writes
definition.json to the aux pointer, re-emits the token and job name (its outputPath REPLACES the
state), and reports every failure on the external token before re-raising."""

import copy
import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A configuration the full template renders with every tag at its default.
_VALID_FULL_CONFIG = {
    "mode": "full", "languageCode": "en-US", "videoOrder": "selection", "productName": "",
    "contributors": "", "maxKeyFrames": 60, "partLevelBase": "0", "generateLabSummary": True,
    "additionalInstructions": "",
}
_VALID_TRANSCRIPT_CONFIG = {"mode": "transcript", "languageCode": "en-US", "videoOrder": "selection"}


def _load(name):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


@pytest.mark.unit
class TestConfigSchemaValidator:
    def _schema(self):
        return _load("configSchema").load_config_schema()

    def test_the_shipped_schema_uses_only_supported_keywords(self):
        # The validator interprets a subset of JSON Schema. A keyword outside it would be silently
        # ignored here and enforced only by the container — half-validated, which is what this pins.
        mod = _load("configSchema")
        assert mod.unsupported_keywords(self._schema()) == []

    def test_the_planted_keyword_is_reported(self):
        # Positive control for the assertion above (an empty list is what a broken walker returns).
        mod = _load("configSchema")
        assert mod.unsupported_keywords({"properties": {"x": {"type": "string", "pattern": "^a"}}}) == ["pattern"]

    def test_the_conditional_is_walked_and_a_keyword_inside_it_is_reported(self):
        # The top-level if/then/const conditional (the shape of the "mode == full requires these
        # keys" rule) is interpreted, so it is not reported; the walker descends into both nodes, so
        # a keyword inside them this validator would not enforce still turns the shipped-schema test
        # red instead of being validated only in the container. `else` is not interpreted either.
        mod = _load("configSchema")
        schema = {"type": "object", "properties": {"mode": {"type": "string"}},
                  "if": {"properties": {"mode": {"const": "full"}}, "required": ["mode"]},
                  "then": {"required": ["maxKeyFrames"]}}
        assert mod.unsupported_keywords(schema) == []
        planted = copy.deepcopy(schema)
        planted["then"]["properties"] = {"maxKeyFrames": {"type": "integer", "multipleOf": 5}}
        planted["else"] = {"required": ["videoOrder"]}
        assert mod.unsupported_keywords(planted) == ["else", "multipleOf"]

    def test_a_union_type_is_reported_as_unsupported(self):
        # `type` is a supported keyword, but only the six names _type_ok tests; a union or unknown
        # type would be skipped here and enforced only in the container.
        mod = _load("configSchema")
        assert mod.unsupported_keywords({"properties": {"x": {"type": ["string", "null"]}}}) == \
            ["type:['string', 'null']"]

    def test_the_schema_declares_the_registry_keys(self):
        schema = self._schema()
        assert set(schema["properties"]) == set(_VALID_FULL_CONFIG)
        assert schema.get("additionalProperties") is False
        # The transcript-only template renders only these three, so nothing else may be required.
        assert "mode" in schema.get("required", [])
        assert set(schema.get("required", [])) <= {"mode", "languageCode", "videoOrder"}
        # The typed full-mode tags are required only when mode is "full" (registry "Config JSON Schema").
        assert schema["if"] == {"properties": {"mode": {"const": "full"}}, "required": ["mode"]}
        assert schema["then"] == {"required": ["maxKeyFrames", "partLevelBase", "generateLabSummary"]}

    def test_both_template_default_configs_validate(self):
        mod = _load("configSchema")
        schema = self._schema()
        assert mod.validate_config(_VALID_FULL_CONFIG, schema) == []
        assert mod.validate_config(_VALID_TRANSCRIPT_CONFIG, schema) == []

    @pytest.mark.parametrize("override,expected_fragments", [
        ({"partLevelBase": "7"}, ('partLevelBase is "7"', 'allowed values are', '"0"', '"1"')),
        ({"maxKeyFrames": 500}, ("maxKeyFrames is 500", "at most 200")),
        ({"maxKeyFrames": 0}, ("maxKeyFrames is 0", "at least 1")),
        ({"additionalInstructions": "x" * 5000},
         ("additionalInstructions is 5000 characters", "at most 4000")),
        ({"productName": "p" * 257}, ("productName is 257 characters", "at most 256")),
        ({"contributors": "c" * 257}, ("contributors is 257 characters", "at most 256")),
        ({"generateLabSummary": "yes"}, ('generateLabSummary is "yes"', "expected a boolean")),
        ({"maxKeyFrames": "60"}, ('maxKeyFrames is "60"', "expected an integer")),
        ({"maxKeyFrames": True}, ("maxKeyFrames is true", "expected an integer")),
        ({"mode": "quick"}, ('mode is "quick"', 'allowed values are', '"full"', '"transcript"')),
        ({"languageCode": "xx-XX"}, ('languageCode is "xx-XX"', 'allowed values are', '"en-US"')),
        ({"videoOrder": "random"},
         ('videoOrder is "random"', 'allowed values are', '"selection"', '"filename"')),
        ({"unexpectedKey": 1}, ('unknown configuration key "unexpectedKey"', "allowed keys are")),
    ])
    def test_each_violation_names_the_value_and_the_bound(self, override, expected_fragments):
        mod = _load("configSchema")
        config = dict(_VALID_FULL_CONFIG, **override)
        errors = mod.validate_config(config, self._schema())
        assert len(errors) == 1, errors
        for fragment in expected_fragments:
            assert fragment in errors[0], (fragment, errors[0])
        # One sentence, operator-readable: ends with a period, never starts with an error code.
        assert errors[0].endswith(".")
        assert not errors[0].startswith("VideoSopBom")

    def test_a_missing_required_key_is_reported(self):
        mod = _load("configSchema")
        config = dict(_VALID_FULL_CONFIG)
        del config["mode"]
        errors = mod.validate_config(config, self._schema())
        assert errors == ["mode is required but the rendered configuration does not set it."]

    def test_full_mode_requires_the_three_typed_tags(self):
        # An allowCustomTemplateOverride body may omit a tag the full template always renders; the
        # sentence names the key so the operator can fix the template body.
        mod = _load("configSchema")
        schema = self._schema()
        for key in ("maxKeyFrames", "partLevelBase", "generateLabSummary"):
            config = {k: v for k, v in _VALID_FULL_CONFIG.items() if k != key}
            assert mod.validate_config(config, schema) == [
                f'the rendered configuration is missing "{key}", which is required when mode is "full"; '
                f"the template body must reference every typed tag."], key
        # The free-text tags stay optional in full mode, and transcript mode needs only the base keys.
        for key in ("productName", "contributors", "additionalInstructions"):
            assert mod.validate_config({k: v for k, v in _VALID_FULL_CONFIG.items() if k != key}, schema) == []
        assert mod.validate_config(dict(_VALID_TRANSCRIPT_CONFIG, maxKeyFrames=60), schema) == []
        # Without mode the conditional does not fire: one sentence, not four.
        config = dict(_VALID_FULL_CONFIG)
        del config["mode"]
        assert mod.validate_config(config, schema) == [
            "mode is required but the rendered configuration does not set it."]

    def test_a_const_rule_on_a_property_is_enforced(self):
        # `const` is interpreted wherever it appears, not only inside the conditional's `if` node.
        mod = _load("configSchema")
        schema = {"type": "object", "properties": {"schemaVersion": {"type": "integer", "const": 1}}}
        assert mod.validate_config({"schemaVersion": 1}, schema) == []
        assert mod.validate_config({"schemaVersion": 2}, schema) == [
            "schemaVersion is 2; the only allowed value is 1."]

    def test_a_non_object_configuration_is_reported(self):
        mod = _load("configSchema")
        errors = mod.validate_config(["not", "an", "object"], self._schema())
        assert errors == ["the rendered configuration is a list, not a JSON object."]

    def test_every_violation_is_listed_not_only_the_first(self):
        mod = _load("configSchema")
        config = dict(_VALID_FULL_CONFIG, maxKeyFrames=500, partLevelBase="7")
        errors = mod.validate_config(config, self._schema())
        assert len(errors) == 2


_MODULE = "constructPipeline"
_TOKEN = "tok-123"
# A value that exists only inside the metadata envelope, so its presence anywhere in the written
# document means metadata content was forwarded.
_METADATA_ONLY_VALUE = "survey-notes-never-forwarded"
_MANIFEST_KEY = "pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json"
_CONFIG_KEY = "pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
_METADATA_KEY = "pipelines/workflowExecutionInputs/E1/metadata.json"
_AUX_PREFIX = "pipelines/genai-video-sop-bom/E1/"
_JOB_NAME = "VideoSopBom_9e8d7c6b5a4f_20260909_120000_a1b2c3"


def _input_files():
    return [
        {"relativePath": "/part1.mp4", "databaseId": "dbV", "assetId": "xidV",
         "assetRootS3Key": "xidV/", "auxPreviewPrefix": "dbV/xidV/part1.mp4/preview",
         "bucket": "abkt", "key": "xidV/part1.mp4", "versionId": "v1"},
        {"relativePath": "/part2.MOV", "databaseId": "dbV", "assetId": "xidV",
         "assetRootS3Key": "xidV/", "auxPreviewPrefix": "dbV/xidV/part2.MOV/preview",
         "bucket": "abkt", "key": "xidV/part2.MOV", "versionId": ""},
    ]


def _manifest(**overrides):
    manifest = {
        "schemaVersion": 1,
        "inputFiles": _input_files(),
        "inputMetadataS3Location": f"s3://abkt/{_METADATA_KEY}",
        "outputs": {"bucket": "abkt",
                    "files": "pipelines/genai-video-sop-bom/JOB/output/E1/files/",
                    "previews": "pipelines/genai-video-sop-bom/JOB/output/E1/previews/",
                    "metadata": "pipelines/genai-video-sop-bom/JOB/output/E1/metadata/",
                    "results": "pipelines/genai-video-sop-bom/JOB/output/E1/results/"},
        "outputTarget": {"locationType": "asset", "assetId": "xidV", "databaseId": "dbV",
                         "fileBaseExecutionPathExtension": "/E1/"},
        "auxBucket": "aux", "auxTempPrefix": _AUX_PREFIX, "auxPreviewPipelineSuffix": "",
        "systemConfig": {"orchestrationBusArn": "arn:bus",
                         "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
    }
    manifest.update(overrides)
    return manifest


def _metadata_envelope():
    return {"schemaVersion": 2, "assets": [
        {"databaseId": "dbV", "assetId": "xidV",
         "assetData": {"assetName": "Cognex In-Sight 7000", "description": "d" * 300},
         "files": [{"fileKey": "/", "metadata": {"surveyNotes": _METADATA_ONLY_VALUE}},
                   {"fileKey": "/part1.mp4", "metadata": {}, "attributes": {}}]},
        {"databaseId": "dbOut", "assetId": "xidOut", "assetData": {"assetName": "Target asset"},
         "files": [{"fileKey": "/", "metadata": {}}]},
    ]}


def _event(**overrides):
    event = {
        "batchJobName": _JOB_NAME, "executionId": "E1", "pipelineExecutionId": "P1",
        "inputFiles": _input_files(), "assetId": "xidV", "databaseId": "dbV",
        "inputManifestS3Location": f"s3://abkt/{_MANIFEST_KEY}",
        "inputMetadataS3Location": f"s3://abkt/{_METADATA_KEY}",
        "inputConfigurationS3Location": f"s3://abkt/{_CONFIG_KEY}",
        "externalSfnTaskToken": _TOKEN,
        "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
    }
    event.update(overrides)
    return event


def _s3(config=None, manifest=None, metadata=None, omit=()):
    """get_object serves the three input documents by key; `omit` names keys that are missing."""
    bodies = {
        _MANIFEST_KEY: _manifest() if manifest is None else manifest,
        _CONFIG_KEY: _VALID_FULL_CONFIG if config is None else config,
        _METADATA_KEY: _metadata_envelope() if metadata is None else metadata,
    }
    for key in omit:
        bodies.pop(key, None)

    def get_object(Bucket, Key):
        if Key not in bodies:
            raise Exception("NoSuchKey")
        return {"Body": MagicMock(read=lambda: json.dumps(bodies[Key]).encode("utf-8"))}

    s3 = MagicMock()
    s3.get_object.side_effect = get_object
    return s3


def _run(mod, event=None, s3=None):
    """(result, s3 mock, send_task_failure mock) for a handler call that completes."""
    s3 = s3 or _s3()
    send_failure = MagicMock()
    with patch.object(mod, "s3", s3), patch.object(mod.sfn, "send_task_failure", send_failure):
        result = mod.lambda_handler(event or _event(), MagicMock())
    return result, s3, send_failure


def _refused(mod, expected, event=None, s3=None, send_failure=None):
    """(exception, send_task_failure mock) for a handler call that raises `expected`."""
    send_failure = send_failure or MagicMock()
    with patch.object(mod, "s3", s3 or _s3()), \
            patch.object(mod.sfn, "send_task_failure", send_failure), \
            pytest.raises(expected) as excinfo:
        mod.lambda_handler(event or _event(), MagicMock())
    return excinfo.value, send_failure


def _written_definition(s3):
    assert s3.put_object.call_count == 1
    kwargs = s3.put_object.call_args.kwargs
    return kwargs, json.loads(kwargs["Body"].decode("utf-8"))


def _reported(send_failure, code):
    """The single send_task_failure call's cause, with the contract asserted; the budget is read
    from the loaded module and its value pinned once, in TestFailurePathReportsTheExternalToken."""
    assert send_failure.call_count == 1
    kwargs = send_failure.call_args.kwargs
    assert kwargs["taskToken"] == _TOKEN
    assert kwargs["error"] == code
    assert not kwargs["cause"].startswith(kwargs["error"])
    assert len(kwargs["cause"]) <= sys.modules[_MODULE].MAX_CAUSE_CHARS
    return kwargs["cause"]


@pytest.mark.unit
class TestDefinitionDocument:
    def test_the_definition_is_written_to_the_aux_pointer(self):
        mod = _load(_MODULE)
        result, s3, send_failure = _run(mod)
        send_failure.assert_not_called()
        kwargs, _ = _written_definition(s3)
        assert kwargs["Bucket"] == "aux"
        assert kwargs["Key"] == f"{_AUX_PREFIX}definition.json"
        assert kwargs["ContentType"] == "application/json"
        assert result["definitionCommand"] == [
            "--definition-s3-uri", f"s3://aux/{_AUX_PREFIX}definition.json"]

    def test_the_document_shape_matches_the_registry(self):
        mod = _load(_MODULE)
        _, s3, _ = _run(mod)
        _, definition = _written_definition(s3)
        assert set(definition) == {
            "schemaVersion", "batchJobName", "pipelineExecutionId", "executionId", "assetId",
            "databaseId", "assetName", "inputFiles", "outputs", "outputTarget", "auxBucket",
            "auxTempPrefix", "kmsKeyArn", "bedrockModelId", "limits", "config"}
        assert definition["schemaVersion"] == 1
        assert definition["batchJobName"] == _JOB_NAME
        assert definition["pipelineExecutionId"] == "P1" and definition["executionId"] == "E1"
        assert definition["assetId"] == "xidV" and definition["databaseId"] == "dbV"
        assert definition["assetName"] == "Cognex In-Sight 7000"
        assert definition["inputFiles"] == [
            {"bucket": "abkt", "key": "xidV/part1.mp4", "versionId": "v1", "relativePath": "/part1.mp4"},
            {"bucket": "abkt", "key": "xidV/part2.MOV", "versionId": "", "relativePath": "/part2.MOV"}]
        assert definition["outputs"] == _manifest()["outputs"]
        assert definition["outputTarget"] == {
            "assetId": "xidV", "databaseId": "dbV", "fileBaseExecutionPathExtension": "/E1/"}
        assert definition["auxBucket"] == "aux" and definition["auxTempPrefix"] == _AUX_PREFIX
        assert definition["kmsKeyArn"] == ""
        assert definition["bedrockModelId"] == "global.anthropic.claude-sonnet-5"
        assert definition["limits"] == {
            "maxVideoFiles": 4, "maxVideoFileSizeMb": 4096, "maxTotalInputSizeMb": 16384,
            "maxTotalDurationMinutes": 240, "maxKeyFramesCeiling": 200}
        assert definition["config"] == _VALID_FULL_CONFIG

    def test_the_document_carries_no_metadata_content_and_no_token(self):
        mod = _load(_MODULE)
        _, s3, _ = _run(mod)
        kwargs, _ = _written_definition(s3)
        body = kwargs["Body"].decode("utf-8")
        assert _METADATA_ONLY_VALUE not in body
        assert "d" * 300 not in body
        assert _TOKEN not in body

    def test_the_return_re_emits_the_token_and_job_name(self):
        # This task's outputPath is $.Payload, which REPLACES the state: anything pipelineEnd or
        # the Batch task reads has to be re-emitted here.
        mod = _load(_MODULE)
        result, _, _ = _run(mod)
        assert result == {
            "batchJobName": _JOB_NAME,
            "definitionCommand": ["--definition-s3-uri", f"s3://aux/{_AUX_PREFIX}definition.json"],
            "externalSfnTaskToken": _TOKEN,
            "status": "STARTING",
        }

    def test_the_execution_id_falls_back_to_the_aux_prefix_leaf(self):
        mod = _load(_MODULE)
        _, s3, _ = _run(mod, event=_event(executionId=""))
        _, definition = _written_definition(s3)
        assert definition["executionId"] == "E1"

    def test_the_pipeline_execution_id_falls_back_to_the_event_prefix(self):
        mod = _load(_MODULE)
        _, s3, _ = _run(mod, event=_event(pipelineExecutionId=""))
        _, definition = _written_definition(s3)
        assert definition["pipelineExecutionId"] == "P1"

    def test_the_write_back_identity_is_the_output_target(self):
        # process-output writes to outputTarget, not to the first input file's asset; the two
        # coincide for every accepted run, and the document records the one that is written to.
        mod = _load(_MODULE)
        manifest = _manifest(outputTarget={"locationType": "asset", "assetId": "xidOut",
                                           "databaseId": "dbOut",
                                           "fileBaseExecutionPathExtension": "/E1/"})
        _, s3, _ = _run(mod, s3=_s3(manifest=manifest))
        _, definition = _written_definition(s3)
        assert definition["assetId"] == "xidOut" and definition["databaseId"] == "dbOut"
        assert definition["assetName"] == "Target asset"

    def test_the_kms_key_arn_is_the_environment_value(self):
        mod = _load(_MODULE)
        with patch.object(mod, "KMS_KEY_ARN", "arn:aws:kms:us-east-1:1:key/abc"):
            _, s3, _ = _run(mod)
        _, definition = _written_definition(s3)
        assert definition["kmsKeyArn"] == "arn:aws:kms:us-east-1:1:key/abc"

    def test_a_bare_aux_prefix_gains_its_trailing_slash(self):
        mod = _load(_MODULE)
        _, s3, _ = _run(mod, s3=_s3(manifest=_manifest(auxTempPrefix="pipelines/genai-video-sop-bom/E1")))
        kwargs, definition = _written_definition(s3)
        assert kwargs["Key"] == f"{_AUX_PREFIX}definition.json"
        assert definition["auxTempPrefix"] == _AUX_PREFIX

    def test_the_metadata_envelope_is_best_effort(self):
        mod = _load(_MODULE)
        _, s3, send_failure = _run(mod, s3=_s3(omit=(_METADATA_KEY,)))
        send_failure.assert_not_called()
        _, definition = _written_definition(s3)
        assert definition["assetName"] == ""

    def test_both_document_shapes_validate_against_the_container_definition_schema(self):
        # WP03's validate_definition applies WP00's definition_schema.json when the container loads
        # the document; a document that fails it would surface only after the Batch job started.
        # Both shapes are checked: a workflow run, and a direct invocation without an orchestration
        # prefix (empty pipelineExecutionId, `VideoSopBom__…` job name), which WP00's pattern
        # ({0,12}) and unconstrained ids admit. jsonschema is a dev-interpreter dependency of this
        # test alone, never of a handler (Task 3). The canonical path is asserted to EXIST: a skip
        # would pass while proving nothing.
        import jsonschema
        mod = _load(_MODULE)
        schema_path = os.path.join(
            os.path.dirname(_LAMBDA_DIR), "container", "video_sop_bom_pipeline", "schemas",
            "definition_schema.json")
        assert os.path.isfile(schema_path), f"{schema_path} is missing; WP00 ships it"
        with open(schema_path, encoding="utf-8") as handle:
            schema = json.load(handle)
        _, s3, _ = _run(mod)
        _, definition = _written_definition(s3)
        jsonschema.validate(definition, schema)
        direct = _event(batchJobName="VideoSopBom__20260909_120000_a1b2c3", pipelineExecutionId="",
                        orchestrationEventPrefix="")
        _, s3, _ = _run(mod, event=direct)
        _, definition = _written_definition(s3)
        assert definition["pipelineExecutionId"] == ""
        jsonschema.validate(definition, schema)


@pytest.mark.unit
class TestConfigRejections:
    @pytest.mark.parametrize("override,expected_fragments", [
        ({"partLevelBase": "7"}, ('partLevelBase is "7"', '"0"', '"1"')),
        ({"maxKeyFrames": 500}, ("maxKeyFrames is 500", "at most 200")),
        ({"additionalInstructions": "x" * 5000}, ("5000 characters", "at most 4000")),
        ({"generateLabSummary": "yes"}, ('generateLabSummary is "yes"', "expected a boolean")),
        ({"unexpectedKey": 1}, ('unknown configuration key "unexpectedKey"',)),
    ])
    def test_an_invalid_configuration_is_reported_with_both_values_then_raised(
            self, override, expected_fragments):
        mod = _load(_MODULE)
        s3 = _s3(config=dict(_VALID_FULL_CONFIG, **override))
        error, send_failure = _refused(mod, mod.PipelineRejection, s3=s3)
        cause = _reported(send_failure, "VideoSopBomInputRejected")
        for fragment in expected_fragments:
            assert fragment in cause, (fragment, cause)
        assert cause == error.cause
        s3.put_object.assert_not_called()

    def test_the_deployment_ceiling_applies_below_the_schema_maximum(self):
        mod = _load(_MODULE)
        with patch.object(mod, "MAX_KEY_FRAMES_CEILING", 50):
            _, send_failure = _refused(mod, mod.PipelineRejection, s3=_s3(config=dict(_VALID_FULL_CONFIG, maxKeyFrames=60)))
        cause = _reported(send_failure, "VideoSopBomInputRejected")
        assert cause == "maxKeyFrames is 60; this deployment allows at most 50."

    def test_a_full_mode_configuration_missing_a_typed_tag_is_refused_before_the_definition_is_written(self):
        # An allowCustomTemplateOverride body can omit a typed tag the full template always renders;
        # the schema's mode conditional refuses it here, on the external token, before any Batch job.
        mod = _load(_MODULE)
        without = {k: v for k, v in _VALID_FULL_CONFIG.items() if k != "generateLabSummary"}
        s3 = _s3(config=without)
        error, send_failure = _refused(mod, mod.PipelineRejection, s3=s3)
        cause = _reported(send_failure, "VideoSopBomInputRejected")
        assert cause == ('the rendered configuration is missing "generateLabSummary", which is required '
                         'when mode is "full"; the template body must reference every typed tag.')
        assert cause == error.cause
        s3.put_object.assert_not_called()
        # Positive control: the same body with the key present is accepted and written.
        _, s3, send_failure = _run(mod, s3=_s3(config=dict(without, generateLabSummary=True)))
        send_failure.assert_not_called()
        _, definition = _written_definition(s3)
        assert definition["config"]["generateLabSummary"] is True

    def test_an_empty_configuration_is_refused(self):
        mod = _load(_MODULE)
        _, send_failure = _refused(mod, mod.PipelineRejection, s3=_s3(config={}))
        cause = _reported(send_failure, "VideoSopBomInputRejected")
        assert "requires a template" in cause

    def test_the_transcript_only_configuration_is_accepted(self):
        mod = _load(_MODULE)
        _, s3, send_failure = _run(mod, s3=_s3(config=_VALID_TRANSCRIPT_CONFIG))
        send_failure.assert_not_called()
        _, definition = _written_definition(s3)
        assert definition["config"] == _VALID_TRANSCRIPT_CONFIG


@pytest.mark.unit
class TestFailurePathReportsTheExternalToken:
    def test_a_missing_manifest_location_fails_the_token(self):
        mod = _load(_MODULE)
        error, send_failure = _refused(mod, ValueError, event=_event(inputManifestS3Location=""))
        cause = _reported(send_failure, "VideoSopBomPipelineError")
        assert "inputManifestS3Location" in cause and cause == str(error)

    def test_an_unreadable_manifest_fails_the_token(self):
        mod = _load(_MODULE)
        _, send_failure = _refused(mod, Exception, s3=_s3(omit=(_MANIFEST_KEY,)))
        cause = _reported(send_failure, "VideoSopBomPipelineError")
        assert "manifest" in cause

    def test_a_downstream_failure_also_fails_the_token(self):
        mod = _load(_MODULE)
        s3 = _s3()
        s3.put_object.side_effect = RuntimeError("kms key disabled")
        _, send_failure = _refused(mod, RuntimeError, s3=s3)
        assert "kms key disabled" in _reported(send_failure, "VideoSopBomPipelineError")

    def test_no_token_means_no_callback_attempted(self):
        mod = _load(_MODULE)
        _, send_failure = _refused(
            mod, mod.PipelineRejection, event=_event(externalSfnTaskToken=""),
            s3=_s3(config=dict(_VALID_FULL_CONFIG, maxKeyFrames=500)))
        send_failure.assert_not_called()

    def test_a_callback_denial_does_not_mask_the_original_error(self):
        mod = _load(_MODULE)
        denied = MagicMock(side_effect=Exception("AccessDeniedException"))
        error, _ = _refused(mod, mod.PipelineRejection,
                            s3=_s3(config=dict(_VALID_FULL_CONFIG, maxKeyFrames=500)),
                            send_failure=denied)
        assert "maxKeyFrames is 500" in str(error)
        # The callback was ATTEMPTED and its denial swallowed — a handler that raised before
        # reaching the abort would also propagate the original error.
        denied.assert_called_once()
        assert denied.call_args.kwargs["taskToken"] == _TOKEN

    def test_failure_for_truncates_to_the_stored_budget(self):
        # SendTaskFailure rejects a cause over 32768 characters (ValidationException) and the stored
        # executionError budget is 16384: a Lambda traceback must be cut, or the callback itself
        # fails and the workflow task waits out its taskTimeout.
        mod = _load(_MODULE)
        assert mod.MAX_CAUSE_CHARS == 16384
        assert mod.failure_for(RuntimeError("y" * 40000)) == ("VideoSopBomPipelineError", "y" * 16384)
        rejection = mod.PipelineRejection("VideoSopBomInputRejected", "z" * 40000)
        assert mod.failure_for(rejection) == ("VideoSopBomInputRejected", "z" * 16384)


@pytest.mark.unit
class TestModuleContract:
    """The registry rule: every cap is read at import with int(os.environ[...]) and no default, and
    BEDROCK_MODEL_ID with os.environ[...], so a builder that omits or mistypes one fails at import
    rather than when the first definition document is written."""

    @pytest.mark.parametrize("name", [
        "VIDEO_SOP_BOM_MAX_VIDEO_FILES", "VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB",
        "VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB", "VIDEO_SOP_BOM_MAX_TOTAL_DURATION_MINUTES",
        "VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING", "BEDROCK_MODEL_ID",
    ])
    def test_a_missing_required_variable_fails_at_import(self, name, monkeypatch):
        monkeypatch.delenv(name)
        try:
            with pytest.raises(KeyError, match=name):
                _load(_MODULE)
        finally:
            monkeypatch.undo()
            _load(_MODULE)

    def test_a_non_integer_cap_fails_at_import(self, monkeypatch):
        monkeypatch.setenv("VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING", "two hundred")
        try:
            with pytest.raises(ValueError):
                _load(_MODULE)
        finally:
            monkeypatch.undo()
            _load(_MODULE)
