#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The analysis and embedding steps read the same subject from an input metadata envelope that carries the records
of the ``assetFileMetadata`` opt-in section as from one that does not.

The per-file registration does not ask for ``assetFileMetadata``, so executeWorkflow narrows the expansion out of
what this pipeline receives. These tests cover a run that receives it anyway (a workflow or template that opts
in): records the expansion added (``expanded``) and an asset group's ``fileExpansion`` block. Their values differ
from the subject's under the same keys, so a reader that took an expanded record for the subject would write or
embed different text. The envelope is built with the backend's own envelope builders, so a change to their shape
reaches this suite.

The other opt-in section, ``metadataSchemas``, does change the output (schema-aware key reuse); its tests are in
``test_sysgenai_schema_key_reuse.py``."""

import json
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h

er = h.load_backend_execution_records()
kr = h.load_local("keyReuse")
cv = h.load_local("classificationVocabulary")

AUX = "aux"
AUX_PREFIX = "pipelines/system-genai-metadata/E1/"
MANIFEST_KEY = AUX_PREFIX + "analysis.json"
META_PREFIX = "pipelines/sgm/sgm/output/E1/metadata/"
RESULTS_PREFIX = "pipelines/sgm/sgm/output/E1/results/"
CONFIG_KEY = "pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
METADATA_KEY = "pipelines/workflowExecutionInputs/E1/metadata.json"

CONFIG = {"seedWithExistingMetadata": True, "resolveDependencies": True, "renderViews": 8,
          "maxTextChars": 12000, "metadataWriteTarget": "both", "reuseExistingKeys": True,
          "embeddingIncludeTextExcerpt": True, "writeExtractedMetadata": True, "extractGeoLocation": True,
          "fieldMapping": kr.FIELD_MAPPING_DEFAULT, "classificationVocabulary": cv.DEFAULT_VOCABULARY}

MODEL_REPLY = {
    "title": "Brass gear pump", "description": "A brass gear pump with an inlet flange.",
    "keywords": ["pump", "gear pump", "brass"], "category": "Industrial Equipment", "subcategory": "Pump",
    "style": "technical/CAD", "materials": ["brass", "steel"], "colors": ["gold", "gray"],
    "objects": ["pump housing"], "complexity": "medium", "orientation": None, "sizeEstimate": "about 30 cm long",
    "textSummary": None,
}

MANIFEST = {
    "schemaVersion": 1, "fileClass": "mesh", "renderBranch": "BLENDER",
    "attributes": {"sys_file": {"name": "pump.glb", "ext": ".glb", "sizeBytes": 1024, "etag": "abc123",
                                "versionId": "v1"},
                   "sys_statistics": {"meshCount": 1, "vertices": 8, "faces": 12, "triangles": 1200}},
    "renderImages": [AUX_PREFIX + "renders/view0.png"], "textExcerpt": "Excerpt of the file text.",
    "facts": {"triangles": "1,200"}, "warnings": [], "renderSkipped": None,
}

# The subject file and asset carry person-named keys key reuse acts on, so a difference in what the step
# reads would show in the rows it writes.
ASSET_METADATA = {"PROJECT": "Alpha", "Category": "", "genai_asset_keywords": "old, pump"}
FILE_METADATA = {"PART_NO": "GP-100", "Material": "", "Colour": "red", "genai_title": "Old pump title"}
FILE_ATTRIBUTES = {"sys_file": '{"etag":"stale"}', "SOURCE_SCANNER": "Leica RTC360"}
# Records the expansion adds for the asset's other files: different values under the same keys, so a reader
# that took an expanded record for the subject would write or embed different text. Every sibling value
# carries the SIBLING_MARK, which no vocabulary or default contains.
SIBLING_MARK = "sibling-7731"
SIBLING_RECORDS = {
    "/models/housing.glb": ({"PART_NO": f"{SIBLING_MARK}-part", "Material": f"{SIBLING_MARK}-alloy",
                             "Colour": f"{SIBLING_MARK}-blue", "genai_title": f"{SIBLING_MARK} housing"},
                            {"SOURCE_SCANNER": f"{SIBLING_MARK} scanner"}),
    "/docs/manual.pdf": ({"PART_NO": f"{SIBLING_MARK}-manual", "Material": f"{SIBLING_MARK}-paper"}, {}),
}

SUBJECT = "/models/pump.glb"
TIMESTAMP_SUFFIXES = ("generated_at",)
TIMESTAMP_MARKER = "<generated-at>"


def _envelope(opt_in):
    """The envelope executeWorkflow writes for a one-file selection; with ``opt_in``, also what the
    ``assetFileMetadata`` key adds: the expansion's records for the asset's other files and its ``fileExpansion``
    block."""
    files = [
        er.build_metadata_file_record("/", metadata=dict(ASSET_METADATA)),
        er.build_metadata_file_record(SUBJECT, metadata=dict(FILE_METADATA), attributes=dict(FILE_ATTRIBUTES)),
    ]
    expansion = None
    if opt_in:
        for key, (metadata, attributes) in SIBLING_RECORDS.items():
            files.append(er.build_metadata_file_record(key, metadata=dict(metadata), attributes=dict(attributes),
                                                       expanded=True))
        expansion = er.build_metadata_file_expansion(["/models/"], len(SIBLING_RECORDS), omitted_file_count=3,
                                                     omitted_file_count_exact=False)
    group = er.build_metadata_asset_group(
        "dbM", "xidM", asset_data={"assetName": "Gear Pump", "description": "A brass gear pump",
                                   "tags": ["pump", "brass"]},
        files=files, file_expansion=expansion)
    databases = [er.build_metadata_database_group("dbM", metadata={"SITE": "Plant 7"})]
    return er.build_grouped_metadata_envelope([group], databases=databases)


def _state(**over):
    state = {
        "jobName": "PipelineJob_20260908_101010_123_abcdef01",
        "externalSfnTaskToken": h.TASK_TOKEN,
        "inputS3AssetFilePath": "s3://abkt/xidM/models/pump.glb",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/sgm/sgm/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/sgm/sgm/output/E1/previews/",
        "outputS3AssetMetadataPath": f"s3://abkt/{META_PREFIX}",
        "outputS3AssetResultsPath": f"s3://abkt/{RESULTS_PREFIX}",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://{AUX}/{AUX_PREFIX}",
        "inputMetadataS3Location": f"s3://abkt/{METADATA_KEY}",
        "inputConfigurationS3Location": f"s3://abkt/{CONFIG_KEY}",
        "assetId": "xidM", "databaseId": "dbM", "bucketId": "bkt-01", "relativePath": SUBJECT,
        "versionId": "v1",
        "workflowExecutionId": "E1", "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        "pipelineExecutionId": "P1", "etag": "abc123", "fileSize": 1024, "contentType": "model/gltf-binary",
        "fileClass": "mesh", "fileExt": ".glb", "renderBranch": "BLENDER", "renderSkipped": None,
        "analysisManifestS3Location": f"s3://{AUX}/{MANIFEST_KEY}", "vectorSearchEnabled": True,
        "status": "STARTING",
    }
    state.update(over)
    return state


def _seed(opt_in):
    s3 = h.FakeS3()
    s3.put_json(AUX, MANIFEST_KEY, json.loads(json.dumps(MANIFEST)))
    s3.put_json("abkt", CONFIG_KEY, dict(CONFIG))
    s3.put_json("abkt", METADATA_KEY, _envelope(opt_in))
    s3.objects[(AUX, AUX_PREFIX + "renders/view0.png")] = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    return s3


def _normalised(value):
    """``value`` with every ``*generated_at`` metadata row value replaced by a marker, recursively, so two runs a
    second apart compare equal."""
    if isinstance(value, dict):
        out = {k: _normalised(v) for k, v in value.items()}
        if str(out.get("metadataKey", "")).endswith(TIMESTAMP_SUFFIXES) and "metadataValue" in out:
            out["metadataValue"] = TIMESTAMP_MARKER
        for key in list(out):
            if key.endswith(("generatedAt", "generated_at")) and isinstance(out[key], str):
                out[key] = TIMESTAMP_MARKER
        return out
    if isinstance(value, list):
        return [_normalised(v) for v in value]
    return value


def _written(s3, inputs):
    """Every object the step wrote, decoded and normalised, keyed by (bucket, key); the seeded inputs left out."""
    out = {}
    for bucket, key in s3.puts:
        if (bucket, key) in inputs:
            continue
        raw = s3.objects[(bucket, key)]
        try:
            out[(bucket, key)] = _normalised(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, ValueError):
            out[(bucket, key)] = len(raw)
    return out


def _analyse(opt_in):
    s3 = _seed(opt_in)
    inputs = set(s3.objects)
    mod = h.load_handler("generateMetadata")
    mod.s3_client = s3
    bedrock = h.FakeBedrock([h.converse_response(json.dumps(MODEL_REPLY))])
    mod.bedrock_runtime = bedrock
    mod.time = MagicMock()
    state = mod.lambda_handler(_state(), MagicMock())
    return s3, state, bedrock, _written(s3, inputs)


def _embed(s3, state):
    inputs = set(s3.objects)
    mod = h.load_handler("generateEmbedding", {"EMBEDDING_DIMENSIONS": "4"})
    mod.s3_client = s3
    mod.events_client = MagicMock()
    mod.events_client.put_events = MagicMock(return_value={"FailedEntryCount": 0, "Entries": [{}]})
    mod.embeddings.embed_text = MagicMock(return_value=[0.1, -0.2, 0.3, 0.4])
    mod.lambda_handler(dict(state), MagicMock())
    texts = [call.args[0] for call in mod.embeddings.embed_text.call_args_list]
    return texts, _written(s3, inputs)


@pytest.mark.unit
class TestOptInEnvelopeSections:
    def test_the_backend_builders_produce_the_opt_in_sections(self):
        # Positive control: the envelope under test really carries every section, so an equality below is
        # not the comparison of two identical envelopes.
        envelope = _envelope(True)
        group = envelope["assets"][0]
        assert group["fileExpansion"]["expandedFileCount"] == len(SIBLING_RECORDS)
        assert [r["fileKey"] for r in group["files"] if r.get("expanded")] == list(SIBLING_RECORDS)
        plain = _envelope(False)
        assert "fileExpansion" not in plain["assets"][0]
        assert not any(r.get("expanded") for r in plain["assets"][0]["files"])

    def test_the_analysis_step_writes_the_same_rows_and_sends_the_same_prompt(self):
        _s3_plain, state_plain, bedrock_plain, written_plain = _analyse(False)
        _s3_opt, state_opt, bedrock_opt, written_opt = _analyse(True)
        assert state_opt["analysisStatus"] == state_plain["analysisStatus"] == "SUCCEEDED"
        assert written_opt == written_plain
        assert bedrock_opt.calls == bedrock_plain.calls
        # Nothing of an expanded record reaches the prompt, and the request asks for no structured output.
        prompt = json.dumps(bedrock_opt.calls[0]["messages"], default=str)
        assert SIBLING_MARK not in prompt
        assert "toolConfig" not in bedrock_opt.calls[0]

    def test_every_written_file_describes_the_subject_file_or_its_asset(self):
        s3, _state_out, _bedrock, written = _analyse(True)
        # The step writes for the subject file and for the asset (write target "both"), never for a file the
        # expansion added, and no value of one lands in what it writes.
        assert ("abkt", META_PREFIX + "models/pump.glb.metadata.json") in written
        assert ("abkt", META_PREFIX + "asset.metadata.json") in written
        assert not any("housing.glb" in key or "manual.pdf" in key for _bucket, key in written)
        assert SIBLING_MARK not in json.dumps(list(written.values()), default=str)
        # The subject's human-set Colour is left alone, as it is without the sections.
        file_rows = {r["metadataKey"]: r["metadataValue"]
                     for r in s3.json_at("abkt", META_PREFIX + "models/pump.glb.metadata.json")["metadata"]}
        assert "Colour" not in file_rows

    def test_the_embedding_step_embeds_the_same_text(self):
        s3_plain, state_plain, _b1, _w1 = _analyse(False)
        s3_opt, state_opt, _b2, _w2 = _analyse(True)
        texts_plain, written_plain = _embed(s3_plain, state_plain)
        texts_opt, written_opt = _embed(s3_opt, state_opt)
        assert texts_plain and texts_opt == texts_plain
        assert written_opt == written_plain
        assert not any(SIBLING_MARK in text for text in texts_opt)
