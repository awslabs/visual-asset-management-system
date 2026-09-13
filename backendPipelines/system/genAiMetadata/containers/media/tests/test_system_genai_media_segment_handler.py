#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Map child for one video window: three frames read over a presigned URL (downloaded once when the URL
fails), one Converse request with the window's context, the window's text embedded and written as a videoTime
document beside the whole-file document, one vector.embedding.ready event, and a results-prefix record. A
caught Bedrock failure (a guardrail intervention included), and a PutEvents failure, are recorded through
execution.status.json and the window's .failed.json and the child returns normally; a window without a readable
frame is skipped without an execution failure. With a guardrail configured, every Converse call carries
guardrailConfig and the file-derived prompt parts travel in a guardContent block.

The handler is loaded by file path under a suite-private name with boto3.client patched, so its three
module-level clients are the fakes for every test."""

import hashlib
import importlib.util
import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from system_genai_media_fixtures import FakeBedrockRuntime, FakeEvents, FakeFfmpeg, FakeS3, client_error, converse_response

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), *([".."] * 6)))
_HANDLER_PATH = os.path.join(_CONTAINER_DIR, "segment_handler.py")
_FILE_CLASSIFIER = os.path.join(_REPO_ROOT, "backendPipelines", "system", "genAiMetadata", "lambda", "fileClassifier.py")
_AUX = "aux-bucket"
_ASSETS = "asset-bucket"
_RUN = "run-bucket"
_PREFIX = "pipelines/system-genai-metadata/E1/"
_RESULTS = "pipelines/sgm/sgm/output/E1/results/"
_METADATA_KEY = "pipelines/workflowExecutionInputs/E1/metadata.json"
_META_FILE_KEY = "pipelines/sgm/sgm/output/E1/metadata/clips/tour.mp4.metadata.json"
_PLAN_KEY = _PREFIX + "segments/plan.json"
_INPUT_KEY = "a1/clips/tour.mp4"
ANALYSIS_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
VECTOR = [0.123456789123, -0.5, 0.25, 1.0]
_ENV = {"BEDROCK_ANALYSIS_MODEL_ID": ANALYSIS_MODEL, "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
        "EMBEDDING_DIMENSIONS": "4", "ORCHESTRATION_BUS_NAME": "vams-orchestration", "AWS_REGION": "us-east-1",
        "BEDROCK_GUARDRAIL_IDENTIFIER": "", "BEDROCK_GUARDRAIL_VERSION": ""}
GUARDRAIL_ENV = {"BEDROCK_GUARDRAIL_IDENTIFIER": "gr-abc123", "BEDROCK_GUARDRAIL_VERSION": "2"}
_LABEL = "00:00:10.000\u201300:00:20.000"
_SEGMENT = {"segmentKey": "t0000010000", "index": 1, "startMs": 10000, "endMs": 20000, "label": _LABEL}
_STATE = {
    "assetId": "a1", "databaseId": "db1", "bucketId": "bkt-01",
    "inputS3AssetFilePath": f"s3://{_ASSETS}/{_INPUT_KEY}", "relativePath": "/clips/tour.mp4", "versionId": "v1",
    "etag": "e1", "fileSize": 1234, "contentType": "video/mp4", "fileClass": "video", "fileExt": ".mp4",
    "inputOutputS3AssetAuxiliaryFilesPath": f"s3://{_AUX}/{_PREFIX}",
    "outputS3AssetResultsPath": f"s3://{_RUN}/{_RESULTS}",
    "inputMetadataS3Location": f"s3://{_RUN}/{_METADATA_KEY}",
    "metadataFileS3Location": f"s3://{_RUN}/{_META_FILE_KEY}",
    "videoSegmentPlanS3Location": f"s3://{_AUX}/{_PLAN_KEY}",
    "videoSegmentItemsS3Location": f"s3://{_AUX}/{_PREFIX}segments/items.json",
    "videoSegmentItemsBucket": _AUX, "videoSegmentItemsKey": _PREFIX + "segments/items.json",
    "videoSegmentResultsBucket": _AUX, "videoSegmentResultsPrefix": _PREFIX + "segments/results/", "videoSegmentCount": 10,
    "pipelineExecutionId": "P1", "workflowExecutionId": "E1",
    "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
    "vectorSearchEnabled": True, "analysisStatus": "SUCCEEDED", "embeddingStatus": "SUCCEEDED",
}
_MODEL_REPLY = {"description": "A worker inspects a red hat on a conveyor.", "keywords": ["conveyor", "red hat", "inspection"],
                "objects": ["hat", "conveyor belt", "worker"], "actions": ["inspecting"], "textSeen": "LINE 3"}


def _file_classifier():
    spec = importlib.util.spec_from_file_location("system_genai_media_file_classifier_for_segments", _FILE_CLASSIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(s3, asset_name="Factory Tour"):
    s3.objects[(_ASSETS, _INPUT_KEY)] = b"\x00"
    s3.objects[(_AUX, _PLAN_KEY)] = json.dumps({
        "schemaVersion": 1, "videoSegmentSeconds": 10, "effectiveIntervalSeconds": 9.248, "durationSeconds": 92.48,
        "count": 10, "segments": [dict(_SEGMENT)]}).encode()
    s3.objects[(_RUN, _METADATA_KEY)] = json.dumps({
        "schemaVersion": 2,
        "assets": [{"databaseId": "db1", "assetId": "a1", "assetData": {"assetName": asset_name, "description": "", "tags": []},
                    "files": []}],
        "databases": []}).encode()
    s3.objects[(_RUN, _META_FILE_KEY)] = json.dumps({"type": "metadata", "updateType": "update", "metadata": [
        {"metadataKey": "genai_title", "metadataValue": "Factory floor tour", "metadataValueType": "string"},
        {"metadataKey": "genai_category", "metadataValue": "Industrial Equipment", "metadataValueType": "string"},
        {"metadataKey": "genai_description", "metadataValue": "A walk through the assembly hall.",
         "metadataValueType": "multiline_string"},
        {"metadataKey": "genai_model", "metadataValue": ANALYSIS_MODEL, "metadataValueType": "string"}]}).encode()
    return s3


def _load(monkeypatch, s3, bedrock, events, env=None):
    for key, value in {**_ENV, **(env or {})}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "ffmpeg-under-test")
    fakes = {"s3": s3, "bedrock-runtime": bedrock, "events": events}
    with patch("boto3.client", side_effect=lambda service, **kwargs: fakes[service]) as factory:
        spec = importlib.util.spec_from_file_location("system_genai_media_segment_handler_under_test", _HANDLER_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    # Three clients, each built once at import with the retry configuration (backendPipelines/CLAUDE.md).
    assert factory.call_args_list, "no boto3 client was built at import"
    assert {call.args[0] for call in factory.call_args_list} == set(fakes)
    assert all(call.kwargs["config"] is module.retry_config for call in factory.call_args_list)
    module.ffmpeg_run = FakeFfmpeg()
    monkeypatch.setattr(module.embeddings, "embed_text", MagicMock(return_value=list(VECTOR)))
    module.time = MagicMock()
    return module


def _reply(**over):
    body = dict(_MODEL_REPLY)
    body.update(over)
    return converse_response(json.dumps(body))


def _run(module, segment=None, state=None):
    return module.lambda_handler({"segment": segment or dict(_SEGMENT), "state": state or dict(_STATE)}, None)


def _document(s3):
    keys = [key for bucket, key, _content_type in s3.puts if bucket == _AUX and key.startswith(_PREFIX + "embedding/")]
    assert len(keys) == 1, s3.puts
    return keys[0], json.loads(s3.objects[(_AUX, keys[0])])


def _embedding_puts(s3):
    return [key for _bucket, key, _content_type in s3.puts if key.startswith(_PREFIX + "embedding/")]


def _status(s3):
    return json.loads(s3.objects[(_RUN, _RESULTS + "execution.status.json")])


def _failed_record(s3):
    return json.loads(s3.objects[(_RUN, _RESULTS + "segments/t0000010000.failed.json")])


def _ss_calls(module):
    calls = [call for call in module.ffmpeg_run.calls if "-ss" in call]
    assert calls, "no frame was requested from ffmpeg"
    return calls


@pytest.mark.unit
class TestHappyPath:
    def test_three_frames_are_read_over_the_presigned_url(self, monkeypatch):
        s3 = _seed(FakeS3())
        module = _load(monkeypatch, s3, FakeBedrockRuntime([_reply()]), FakeEvents())
        response = _run(module)
        assert response["status"] == "SUCCEEDED"
        calls = _ss_calls(module)
        assert [call[call.index("-ss") + 1] for call in calls] == ["11.000", "15.000", "19.000"]
        for call in calls:
            source = call[call.index("-i") + 1]
            assert source.startswith("https://") and "versionId=v1" in source
            assert call.index("-ss") < call.index("-i")
            assert call[call.index("-vf") + 1] == "scale='min(1024,iw)':-2" and "-frames:v" in call
        assert s3.presigns == [("get_object", {"Bucket": _ASSETS, "Key": _INPUT_KEY, "VersionId": "v1"}, 900)]
        assert s3.downloads == []

    def test_document_shape_and_location(self, monkeypatch):
        s3 = _seed(FakeS3())
        module = _load(monkeypatch, s3, FakeBedrockRuntime([_reply()]), FakeEvents())
        response = _run(module)
        key, document = _document(s3)
        expected_hash = hashlib.sha256("/clips/tour.mp4#v1#t0000010000".encode("utf-8")).hexdigest()
        assert key == f"{_PREFIX}embedding/{expected_hash}.json"
        assert response == {"segmentKey": "t0000010000", "status": "SUCCEEDED", "documentS3Location": f"s3://{_AUX}/{key}"}
        assert set(document) == {
            "schemaVersion", "databaseId", "assetId", "filePath", "versionId", "contentEtag", "bucketId",
            "fileClass", "fileExt", "fileSize", "contentType", "embeddingModelId", "embeddingDimensions",
            "analysisModelId", "embedding", "sourceText", "sourceModalities", "pipelineExecutionId",
            "workflowExecutionId", "generatedAt",
            "segmentKey", "segmentKind", "segmentLabel", "segmentStartMs", "segmentEndMs", "segmentCount",
        }
        assert len(document) == 26
        assert (document["segmentKey"], document["segmentKind"], document["segmentLabel"]) == ("t0000010000", "videoTime", _LABEL)
        assert (document["segmentStartMs"], document["segmentEndMs"], document["segmentCount"]) == (10000, 20000, 10)
        assert (document["databaseId"], document["assetId"], document["filePath"], document["versionId"]) == (
            "db1", "a1", "/clips/tour.mp4", "v1")
        assert (document["fileClass"], document["fileExt"], document["fileSize"], document["contentType"]) == (
            "video", "mp4", 1234, "video/mp4")
        assert document["contentEtag"] == "e1" and document["bucketId"] == "bkt-01"
        assert document["embeddingModelId"] == "amazon.titan-embed-text-v2:0" and document["embeddingDimensions"] == 4
        assert document["analysisModelId"] == ANALYSIS_MODEL
        assert document["embedding"] == [0.123456789, -0.5, 0.25, 1.0]
        assert (document["pipelineExecutionId"], document["workflowExecutionId"]) == ("P1", "E1")
        assert document["schemaVersion"] == 1 and document["generatedAt"].endswith("Z")
        assert len(document["sourceText"]) <= module.SOURCE_TEXT_STORED_MAX_CHARS == 8000

    def test_event_detail_and_the_results_record(self, monkeypatch):
        s3 = _seed(FakeS3())
        events = FakeEvents()
        module = _load(monkeypatch, s3, FakeBedrockRuntime([_reply()]), events)
        response = _run(module)
        key, document = _document(s3)
        assert len(events.entries) == 1
        entry = events.entries[0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["Source"] == "vams.prod.execution.E1.pipeline.P1"
        assert entry["DetailType"] == "vector.embedding.ready"
        detail = json.loads(entry["Detail"])
        expected = {field: value for field, value in document.items() if field not in ("embedding", "sourceText")}
        expected["documentS3Location"] = f"s3://{_AUX}/{key}"
        assert detail == expected and len(detail) == 25
        record = json.loads(s3.objects[(_RUN, _RESULTS + "segments/t0000010000.json")])
        assert record["description"] == "A worker inspects a red hat on a conveyor."
        assert record["keywords"] == ["conveyor", "red hat", "inspection"] and record["actions"] == ["inspecting"]
        assert (record["startMs"], record["endMs"], record["label"], record["segmentKind"]) == (10000, 20000, _LABEL, "videoTime")
        assert record["textSeen"] == "LINE 3" and record["analysisModelId"] == ANALYSIS_MODEL
        assert record["documentS3Location"] == response["documentS3Location"]
        assert (_RUN, _RESULTS + "execution.status.json") not in s3.objects

    def test_converse_request_shape_without_a_guardrail(self, monkeypatch):
        s3 = _seed(FakeS3())
        bedrock = FakeBedrockRuntime([_reply()])
        module = _load(monkeypatch, s3, bedrock, FakeEvents())
        _run(module)
        assert len(bedrock.calls) == 1
        call = bedrock.calls[0]
        assert module.GUARDRAIL_CONFIG is None and "guardrailConfig" not in call
        assert call["modelId"] == ANALYSIS_MODEL and call["system"] == [{"text": module.SEGMENT_SYSTEM_PROMPT}]
        assert call["inferenceConfig"]["maxTokens"] == 1024
        content = call["messages"][0]["content"]
        assert [list(block) for block in content] == [["text"], ["image"], ["image"], ["image"]]
        for block in content[1:]:
            assert block["image"]["format"] == "png" and block["image"]["source"]["bytes"].startswith(b"\x89PNG")
        text = content[0]["text"]
        for fragment in ("Factory Tour", "video (footage)", "/clips/tour.mp4", f"Segment {_LABEL} of 1 min 32 s",
                         "Factory floor tour", "Industrial Equipment", "A walk through the assembly hall.", "3 frames"):
            assert fragment in text, fragment

    def test_source_text_order_and_modalities(self, monkeypatch):
        s3 = _seed(FakeS3())
        module = _load(monkeypatch, s3, FakeBedrockRuntime([_reply()]), FakeEvents())
        _run(module)
        embedded = module.embeddings.embed_text.call_args.args[0]
        assert embedded.split("\n") == [
            "Factory Tour", "video (footage)", "/clips/tour.mp4", f"Segment {_LABEL} of 1 min 32 s",
            "A worker inspects a red hat on a conveyor.", "conveyor, red hat, inspection", "hat, conveyor belt, worker",
            "inspecting", "LINE 3", "Factory floor tour", "Industrial Equipment",
        ]
        assert module.embeddings.embed_text.call_args.kwargs == {
            "model_id": "amazon.titan-embed-text-v2:0", "dimensions": 4, "purpose": "index", "client": module.bedrock_runtime}
        _key, document = _document(s3)
        assert document["sourceText"] == embedded
        assert document["sourceModalities"] == ["asset-metadata", "file-identity", "segment-frames", "genai-metadata"]

    def test_a_missing_asset_name_drops_the_asset_label(self, monkeypatch):
        s3 = _seed(FakeS3(), asset_name="")
        module = _load(monkeypatch, s3, FakeBedrockRuntime([_reply()]), FakeEvents())
        _run(module)
        _key, document = _document(s3)
        assert document["sourceModalities"] == ["file-identity", "segment-frames", "genai-metadata"]
        assert document["sourceText"].split("\n")[0] == "video (footage)"


@pytest.mark.unit
class TestGuardrail:
    def test_a_configured_guardrail_is_applied_and_the_untrusted_content_is_guarded(self, monkeypatch):
        """The asset name, the file path and the whole video's genai_* values come from the file and its
        metadata and travel in a guardContent block; the instruction, the file-type phrase, the window sentence
        and the frame note stay in the plain block. The same lines reach the model as without a guardrail."""
        s3 = _seed(FakeS3())
        bedrock = FakeBedrockRuntime([_reply()])
        module = _load(monkeypatch, s3, bedrock, FakeEvents(), env=GUARDRAIL_ENV)
        response = _run(module)
        assert response["status"] == "SUCCEEDED"
        assert bedrock.calls, "no Converse call was made"
        call = bedrock.calls[0]
        assert call["guardrailConfig"] == {"guardrailIdentifier": "gr-abc123", "guardrailVersion": "2", "trace": "enabled"}
        content = call["messages"][0]["content"]
        assert [list(block) for block in content] == [["text"], ["guardContent"], ["image"], ["image"], ["image"]]
        plain = content[0]["text"]
        guarded = content[1]["guardContent"]["text"]
        assert guarded["qualifiers"] == ["guard_content"]
        for fragment in ("Factory Tour", "/clips/tour.mp4", "Factory floor tour", "Industrial Equipment",
                         "A walk through the assembly hall."):
            assert fragment in guarded["text"] and fragment not in plain, fragment
        for fragment in ("video (footage)", f"Segment {_LABEL} of 1 min 32 s", "3 frames"):
            assert fragment in plain and fragment not in guarded["text"], fragment
        bedrock_plain = FakeBedrockRuntime([_reply()])
        unguarded = _load(monkeypatch, _seed(FakeS3()), bedrock_plain, FakeEvents())
        _run(unguarded)
        assert bedrock_plain.calls, "no Converse call was made without a guardrail"
        assert set(bedrock_plain.calls[0]["messages"][0]["content"][0]["text"].split("\n")) == set(
            plain.split("\n") + guarded["text"].split("\n"))

    def test_an_intervention_is_a_caught_failure_under_its_own_code(self, monkeypatch):
        s3 = _seed(FakeS3())
        events = FakeEvents()
        blocked = converse_response("Blocked by the guardrail.", stop_reason="guardrail_intervened",
                                    trace={"guardrail": {"inputAssessment": {"gr-abc123": {"contentPolicy": {"filters": [
                                        {"type": "PROMPT_ATTACK", "confidence": "HIGH", "action": "BLOCKED"}]}}}}})
        module = _load(monkeypatch, s3, FakeBedrockRuntime([blocked]), events, env=GUARDRAIL_ENV)
        response = _run(module)
        assert response == {"segmentKey": "t0000010000", "status": "FAILED", "error": "BedrockGuardrailIntervened",
                            "documentS3Location": None}
        status = _status(s3)
        assert status["status"] == "FAILED" and status["error"] == "BedrockGuardrailIntervened"
        assert status["cause"].startswith("segment t0000010000: ")
        assert "PROMPT_ATTACK" in status["cause"] and "Blocked by the guardrail." in status["cause"]
        assert len(status["cause"]) <= 1024
        assert _failed_record(s3)["error"] == "BedrockGuardrailIntervened"
        assert events.entries == [] and _embedding_puts(s3) == []
        module.embeddings.embed_text.assert_not_called()

    @pytest.mark.parametrize("env", [{"BEDROCK_GUARDRAIL_IDENTIFIER": "gr-abc123", "BEDROCK_GUARDRAIL_VERSION": ""},
                                     {"BEDROCK_GUARDRAIL_IDENTIFIER": "", "BEDROCK_GUARDRAIL_VERSION": "DRAFT"}])
    def test_one_guardrail_variable_without_the_other_is_a_configuration_error(self, monkeypatch, env):
        with pytest.raises(ValueError, match="BEDROCK_GUARDRAIL_IDENTIFIER and BEDROCK_GUARDRAIL_VERSION"):
            _load(monkeypatch, FakeS3(), FakeBedrockRuntime([_reply()]), FakeEvents(), env=env)

    def test_the_guardrail_helper_is_the_vendored_lambda_module(self, monkeypatch):
        module = _load(monkeypatch, FakeS3(), FakeBedrockRuntime([_reply()]), FakeEvents())
        assert os.path.dirname(os.path.abspath(module.bedrockGuardrail.__file__)) == _CONTAINER_DIR
        assert module.ERROR_BEDROCK_GUARDRAIL_INTERVENED == "BedrockGuardrailIntervened"


@pytest.mark.unit
class TestFrameFallbacks:
    def test_a_url_failure_falls_back_to_one_download(self, monkeypatch):
        s3 = _seed(FakeS3())
        bedrock = FakeBedrockRuntime([_reply()])
        module = _load(monkeypatch, s3, bedrock, FakeEvents())
        module.ffmpeg_run = FakeFfmpeg(fail_when_url=True)
        response = _run(module)
        assert response["status"] == "SUCCEEDED"
        calls = _ss_calls(module)
        assert len(calls) == 6
        assert all(call[call.index("-i") + 1].startswith("https://") for call in calls[:3])
        assert all(not call[call.index("-i") + 1].startswith("http") for call in calls[3:])
        assert s3.downloads == [(_ASSETS, _INPUT_KEY, "v1")]
        assert len(bedrock.calls[0]["messages"][0]["content"]) == 4

    def test_no_readable_frame_skips_the_window(self, monkeypatch):
        s3 = _seed(FakeS3())
        bedrock = FakeBedrockRuntime([_reply()])
        events = FakeEvents()
        module = _load(monkeypatch, s3, bedrock, events)
        module.ffmpeg_run = FakeFfmpeg(fail_when_url=True, fail_at=(11.0, 15.0, 19.0))
        response = _run(module)
        assert response == {"segmentKey": "t0000010000", "status": "SKIPPED", "error": "SegmentFramesUnavailable",
                            "documentS3Location": None}
        failed = _failed_record(s3)
        assert failed["status"] == "SKIPPED" and failed["error"] == module.ERROR_NO_FRAMES == "SegmentFramesUnavailable"
        assert "no frame" in failed["cause"] and (failed["startMs"], failed["endMs"]) == (10000, 20000)
        assert (_RUN, _RESULTS + "execution.status.json") not in s3.objects
        assert bedrock.calls == [] and events.entries == [] and _embedding_puts(s3) == []
        module.embeddings.embed_text.assert_not_called()

    def test_one_failed_frame_still_analyses(self, monkeypatch):
        s3 = _seed(FakeS3())
        bedrock = FakeBedrockRuntime([_reply()])
        module = _load(monkeypatch, s3, bedrock, FakeEvents())
        module.ffmpeg_run = FakeFfmpeg(fail_at=(15.0,))
        response = _run(module)
        assert response["status"] == "SUCCEEDED"
        assert len(bedrock.calls[0]["messages"][0]["content"]) == 3
        assert s3.downloads == []


@pytest.mark.unit
class TestCaughtFailures:
    def test_bedrock_access_denied_records_the_segment_error(self, monkeypatch):
        s3 = _seed(FakeS3())
        events = FakeEvents()
        module = _load(monkeypatch, s3, FakeBedrockRuntime([client_error("AccessDeniedException", "no model access")]), events)
        response = _run(module)
        assert response == {"segmentKey": "t0000010000", "status": "FAILED", "error": "BedrockSegmentError",
                            "documentS3Location": None}
        status = _status(s3)
        assert status["status"] == "FAILED" and status["error"] == "BedrockSegmentError"
        assert status["cause"].startswith("segment t0000010000: ") and "AccessDeniedException" in status["cause"]
        assert len(status["cause"]) <= 1024
        failed = _failed_record(s3)
        assert (failed["status"], failed["error"], failed["segmentKey"]) == ("FAILED", "BedrockSegmentError", "t0000010000")
        assert events.entries == [] and _embedding_puts(s3) == []
        assert (_RUN, _RESULTS + "segments/t0000010000.json") not in s3.objects

    def test_throttling_backs_off_then_succeeds(self, monkeypatch):
        s3 = _seed(FakeS3())
        bedrock = FakeBedrockRuntime([client_error("ThrottlingException", "slow down"),
                                      client_error("ThrottlingException", "slow down"), _reply()])
        module = _load(monkeypatch, s3, bedrock, FakeEvents())
        response = _run(module)
        assert response["status"] == "SUCCEEDED" and len(bedrock.calls) == 3
        assert [call.args[0] for call in module.time.sleep.call_args_list] == [2, 4]

    def test_three_unparsable_replies_fail_the_window(self, monkeypatch):
        s3 = _seed(FakeS3())
        bedrock = FakeBedrockRuntime([converse_response("I cannot see the frames.")] * 3)
        module = _load(monkeypatch, s3, bedrock, FakeEvents())
        response = _run(module)
        assert response["status"] == "FAILED" and len(bedrock.calls) == 3
        assert _status(s3)["error"] == "BedrockSegmentError" and "3 attempts" in _status(s3)["cause"]

    def test_an_embedding_failure_is_a_segment_error(self, monkeypatch):
        s3 = _seed(FakeS3())
        events = FakeEvents()
        module = _load(monkeypatch, s3, FakeBedrockRuntime([_reply()]), events)
        monkeypatch.setattr(module.embeddings, "embed_text",
                            MagicMock(side_effect=module.embeddings.EmbeddingModelError("input too long")))
        response = _run(module)
        assert response["status"] == "FAILED"
        assert _status(s3)["error"] == "BedrockSegmentError" and "input too long" in _status(s3)["cause"]
        assert events.entries == [] and _embedding_puts(s3) == []
        assert (_RUN, _RESULTS + "segments/t0000010000.json") not in s3.objects

    def test_a_failed_put_events_entry_is_a_caught_publish_failure(self, monkeypatch):
        s3 = _seed(FakeS3())
        events = FakeEvents(fail_count=1)
        module = _load(monkeypatch, s3, FakeBedrockRuntime([_reply()]), events)
        response = _run(module)
        # The document was written and is named in the return; it reached no indexer, so the run is FAILED.
        key, _document_body = _document(s3)
        assert response == {"segmentKey": "t0000010000", "status": "FAILED", "error": "SegmentPublishError",
                            "documentS3Location": f"s3://{_AUX}/{key}"}
        status = _status(s3)
        assert status["status"] == "FAILED" and status["error"] == module.ERROR_SEGMENT_PUBLISH == "SegmentPublishError"
        assert status["cause"].startswith("segment t0000010000: ") and "FailedEntryCount" in status["cause"]
        assert len(status["cause"]) <= 1024
        failed = _failed_record(s3)
        assert (failed["status"], failed["error"], failed["segmentKey"]) == ("FAILED", "SegmentPublishError", "t0000010000")
        assert len(events.calls) == 1
        assert (_RUN, _RESULTS + "segments/t0000010000.json") not in s3.objects

    def test_a_put_events_exception_is_a_caught_publish_failure(self, monkeypatch):
        s3 = _seed(FakeS3())
        events = FakeEvents(error=client_error("InternalException", "bus unavailable", "PutEvents"))
        module = _load(monkeypatch, s3, FakeBedrockRuntime([_reply()]), events)
        response = _run(module)
        assert response["status"] == "FAILED" and response["error"] == "SegmentPublishError"
        key, _document_body = _document(s3)
        assert response["documentS3Location"] == f"s3://{_AUX}/{key}"
        assert _status(s3)["error"] == "SegmentPublishError" and "InternalException" in _status(s3)["cause"]
        assert _failed_record(s3)["error"] == "SegmentPublishError" and events.entries == []

    def test_no_bus_writes_the_document_without_publishing(self, monkeypatch):
        s3 = _seed(FakeS3())
        events = FakeEvents()
        module = _load(monkeypatch, s3, FakeBedrockRuntime([_reply()]), events, env={"ORCHESTRATION_BUS_NAME": ""})
        response = _run(module)
        assert response["status"] == "SUCCEEDED" and response["documentS3Location"]
        _document(s3)
        assert events.entries == []


@pytest.mark.unit
class TestHelpers:
    def test_frame_times(self, monkeypatch):
        module = _load(monkeypatch, FakeS3(), FakeBedrockRuntime([_reply()]), FakeEvents())
        assert module.frame_times(10000, 20000) == [11.0, 15.0, 19.0]
        assert module.frame_times(0, 9248) == [0.925, 4.624, 8.323]

    def test_parse_segment_json_bounds_and_requires_a_description(self, monkeypatch):
        module = _load(monkeypatch, FakeS3(), FakeBedrockRuntime([_reply()]), FakeEvents())
        parsed = module.parse_segment_json(json.dumps({
            "description": "d" * 500, "keywords": [f"k{i}" for i in range(20)] + ["K1"],
            "objects": ["o"] * 3 + [f"o{i}" for i in range(20)], "actions": [f"a{i}" for i in range(9)],
            "textSeen": "t" * 300}))
        assert len(parsed["description"]) == 400
        assert len(parsed["keywords"]) == 10 and parsed["keywords"][0] == "k0"
        assert len(parsed["objects"]) == 10 and parsed["objects"][0] == "o"
        assert len(parsed["actions"]) == 5 and len(parsed["textSeen"]) == 200
        assert module.parse_segment_json('{"description": "ok", "keywords": [], "textSeen": null}') == {
            "description": "ok", "keywords": [], "objects": [], "actions": [], "textSeen": None}
        assert module.parse_segment_json("```json\n{\"description\": \"fenced\"}\n```")["description"] == "fenced"
        for bad in ('{"keywords": []}', "[1]", "no braces", ""):
            with pytest.raises(module.ModelResponseError):
                module.parse_segment_json(bad)

    def test_constants_and_pins(self, monkeypatch):
        module = _load(monkeypatch, FakeS3(), FakeBedrockRuntime([_reply()]), FakeEvents())
        assert module.SEGMENT_FRAMES == 3 and module.SEGMENT_FRAME_POSITIONS == (0.1, 0.5, 0.9)
        assert module.SEGMENT_PRESIGN_SECONDS == 900 and module.SEGMENT_MAX_LONG_EDGE_PX == 1024
        assert module.SEGMENT_JSON_KEYS == ("description", "keywords", "objects", "actions", "textSeen")
        assert module.ERROR_BEDROCK_SEGMENT == "BedrockSegmentError" and module.SEGMENT_KIND == "videoTime"
        assert module.ERROR_SEGMENT_PUBLISH == "SegmentPublishError" and module.ERROR_NO_FRAMES == "SegmentFramesUnavailable"
        classifier = _file_classifier()
        # Restated, not imported: the phrases the whole-file document embeds are the ones the window embeds.
        assert module.FILE_CLASS_PHRASES == classifier.FILE_CLASS_PHRASES
        assert set(module.FILE_CLASS_PHRASES) == set(classifier.FILE_CLASSES)

    def test_the_handler_never_touches_step_functions(self):
        source = io.open(_HANDLER_PATH, encoding="utf-8").read()
        assert "stepfunctions" not in source
        assert "send_task_success" not in source and "send_task_failure" not in source

    def test_status_literals_match_the_pipeline(self, monkeypatch):
        module = _load(monkeypatch, FakeS3(), FakeBedrockRuntime([_reply()]), FakeEvents())
        # Pinned to the backend constant on the lambda side (analysisCommon); the same literals here.
        assert module.EXECUTION_STATUS_RESULTS_FILENAME == "execution.status.json"
        assert module.STATUS_CAUSE_MAX_CHARS == 1024
