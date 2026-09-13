#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""generateEmbedding composes one file version's sourceText in a fixed order — asset context, file
identity, the genai_* rows, the attribute facts, the existing metadata scope by scope (file, asset,
database, non-pipeline file attributes; always, whatever the prompt switch says), and the text excerpt
LAST so a model window cuts it first — embeds it through the vendored adapter with purpose "index",
writes the embedding document to the auxiliary bucket, and publishes vector.embedding.ready — the plugin
contract the vector indexer consumes. A caught Bedrock failure is recorded through execution.status.json;
nothing is published for a version that will be recorded FAILED."""

import hashlib
import io
import json
import os
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h

AUX = "aux"
AUX_PREFIX = "pipelines/system-genai-metadata/E1/"
MANIFEST_KEY = AUX_PREFIX + "analysis.json"
META_PREFIX = "pipelines/sgm/sgm/output/E1/metadata/"
RESULTS_PREFIX = "pipelines/sgm/sgm/output/E1/results/"
CONFIG_KEY = "pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
METADATA_KEY = "pipelines/workflowExecutionInputs/E1/metadata.json"
METADATA_FILE_KEY = META_PREFIX + "models/pump.glb.metadata.json"
STATUS_KEY = RESULTS_PREFIX + "execution.status.json"
ANALYSIS_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
VECTOR = [0.123456789123, -0.5, 0.25, 1.0]


def _state(**over):
    state = {
        "jobName": "PipelineJob_20260908_101010_123_abcdef01",
        "externalSfnTaskToken": h.TASK_TOKEN,
        "inputS3AssetFilePath": "s3://abkt/xidM/models/pump.glb",
        "outputS3AssetMetadataPath": f"s3://abkt/{META_PREFIX}",
        "outputS3AssetResultsPath": f"s3://abkt/{RESULTS_PREFIX}",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://{AUX}/{AUX_PREFIX}",
        "inputMetadataS3Location": f"s3://abkt/{METADATA_KEY}",
        "inputConfigurationS3Location": f"s3://abkt/{CONFIG_KEY}",
        "assetId": "xidM", "databaseId": "dbM", "bucketId": "bkt-01", "relativePath": "/models/pump.glb",
        "versionId": "v1",
        "workflowExecutionId": "E1", "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        "pipelineExecutionId": "P1", "etag": "abc123", "fileSize": 1024, "contentType": "model/gltf-binary",
        "fileClass": "mesh", "fileExt": ".glb", "renderBranch": "BLENDER", "renderSkipped": None,
        "analysisManifestS3Location": f"s3://{AUX}/{MANIFEST_KEY}", "vectorSearchEnabled": True,
        "analysisStatus": "SUCCEEDED", "metadataFileS3Location": f"s3://abkt/{METADATA_FILE_KEY}",
    }
    state.update(over)
    return state


def _manifest(**over):
    manifest = {"schemaVersion": 1, "fileClass": "mesh", "renderBranch": "BLENDER",
                "attributes": {"sys_file": {"name": "pump.glb"}},
                "renderImages": [], "textExcerpt": "Excerpt of the file text.",
                "facts": {"dimensions": "1.0 x 2.0 x 3.0 m", "triangles": "1,200"},
                "warnings": [], "renderSkipped": None}
    manifest.update(over)
    return manifest


def _metadata_file(**over):
    rows = {"genai_title": "Brass gear pump", "genai_description": "A brass gear pump with an inlet flange.",
            "genai_keywords": "pump, gear pump, brass", "genai_category": "mechanical part",
            "genai_subcategory": "Hydraulic Pump", "genai_style": "technical/CAD", "genai_materials": "brass, steel",
            "genai_colors": "gold, gray", "genai_objects": "pump housing, inlet flange",
            "genai_size_estimate": "about 30 cm long", "genai_orientation": "Z-up, front faces -Y",
            "genai_model": ANALYSIS_MODEL, "genai_generated_at": "2026-09-08T10:00:00Z",
            "genai_source_modalities": "file-attributes, renders"}
    rows.update(over)
    return {"type": "metadata", "updateType": "update",
            "metadata": [{"metadataKey": key, "metadataValue": value, "metadataValueType": "string"}
                         for key, value in rows.items()]}


def _envelope(description="A brass gear pump", file_metadata=None, database_metadata=None, file_attributes=None):
    """The v2 grouped envelope: every value the raw metadataValue string. The file record's attributes carry
    the previous run's sys_file (never embedded) and a user key (embedded like any other existing value)."""
    return {"schemaVersion": 2,
            "assets": [{"databaseId": "dbM", "assetId": "xidM",
                        "assetData": {"assetName": "Gear Pump", "description": description, "tags": ["pump", "brass"]},
                        "files": [{"fileKey": "/", "metadata": {"PROJECT": "Alpha", "genai_asset_keywords": "old"}},
                                  {"fileKey": "/models/pump.glb",
                                   "metadata": file_metadata if file_metadata is not None else {"PART_NO": "GP-100"},
                                   "attributes": file_attributes if file_attributes is not None
                                   else {"sys_file": '{"name":"pump.glb","etag":"stale-etag"}',
                                         "SOURCE_SCANNER": "Leica RTC360"}}]}],
            "databases": [{"databaseId": "dbM", "metadata": database_metadata if database_metadata is not None
                           else {"SITE": "Plant 7"}}]}


def _seed(s3, manifest=None, config=None, envelope=None, metadata_file=None):
    s3.put_json(AUX, MANIFEST_KEY, manifest if manifest is not None else _manifest())
    s3.put_json("abkt", CONFIG_KEY, config if config is not None else {"embeddingIncludeTextExcerpt": True})
    s3.put_json("abkt", METADATA_KEY, envelope if envelope is not None else _envelope())
    if metadata_file is not False:
        s3.put_json("abkt", METADATA_FILE_KEY, metadata_file if metadata_file is not None else _metadata_file())
    return s3


def _run(state, s3, embed=None, env=None, put_events=None, embed_error=None):
    """Load the handler, swap its clients for fakes, run it.

    ``embed_error`` is a callable ``mod -> Exception`` built AFTER the load, because every load
    re-imports the vendored ``vectorsearch.embeddings`` and an exception class from an earlier load
    would not be caught by the handler's ``except``."""
    mod = h.load_handler("generateEmbedding", dict({"EMBEDDING_DIMENSIONS": "4"}, **(env or {})))
    mod.s3_client = s3
    mod.events_client = MagicMock()
    mod.events_client.put_events = put_events or MagicMock(return_value={"FailedEntryCount": 0, "Entries": [{}]})
    if embed_error is not None:
        mod.embeddings.embed_text = MagicMock(side_effect=embed_error(mod))
    else:
        mod.embeddings.embed_text = embed or MagicMock(return_value=list(VECTOR))
    result = mod.lambda_handler(state, MagicMock())
    return mod, result


def _embedded_text(mod):
    return mod.embeddings.embed_text.call_args.args[0]


def _document(s3):
    keys = [key for bucket, key in s3.puts
            if bucket == AUX and key.startswith(AUX_PREFIX + "embedding/") and not key.endswith("/summary.json")]
    assert len(keys) == 1, s3.puts
    return keys[0], s3.json_at(AUX, keys[0])


FULL_TEXT_KEY = AUX_PREFIX + "text/full.txt"
PAGES_KEY = AUX_PREFIX + "text/pages.json"
SUMMARY_KEY = AUX_PREFIX + "embedding/summary.json"
SENTENCES = "".join(f"Sentence {i} of the manual. " for i in range(200))


def _text_manifest(chars):
    """A document manifest whose branch captured `chars` characters of text with page offsets."""
    return _manifest(fileClass="document", renderBranch="MEDIA", fullTextS3Location=f"s3://{AUX}/{FULL_TEXT_KEY}",
                     fullTextChars=chars, fullTextTruncated=False, pageOffsetsS3Location=f"s3://{AUX}/{PAGES_KEY}")


def _seed_text(s3, text=None, pages=3, **seed_kwargs):
    """The default text (4,000 characters of sentences) splits into exactly three chunks whatever the sentence
    snap chooses; `pages` evenly spaced page entries label them page 1, 2, 3."""
    text = SENTENCES[:4000] if text is None else text
    s3 = _seed(s3, manifest=_text_manifest(len(text)), **seed_kwargs)
    s3.objects[(AUX, FULL_TEXT_KEY)] = text.encode("utf-8")
    s3.put_json(AUX, PAGES_KEY, [{"page": n + 1, "start": n * (len(text) // pages)} for n in range(pages)] if pages else [])
    return s3


def _documents(s3):
    keys = [key for bucket, key in s3.puts
            if bucket == AUX and key.startswith(AUX_PREFIX + "embedding/") and not key.endswith("/summary.json")]
    return [(key, s3.json_at(AUX, key)) for key in keys]


def _details(mod):
    calls = mod.events_client.put_events.call_args_list
    assert calls, "no PutEvents call was made"
    return [json.loads(entry["Detail"]) for call in calls for entry in call.kwargs["Entries"]]


@pytest.mark.unit
class TestGuards:
    def test_skipped_when_vector_search_is_off(self):
        s3 = _seed(h.FakeS3())
        mod, state = _run(_state(vectorSearchEnabled=False), s3)
        assert state["embeddingStatus"] == "SKIPPED"
        assert s3.puts == [] and s3.gets == []
        mod.events_client.put_events.assert_not_called()
        mod.embeddings.embed_text.assert_not_called()

    def test_skipped_when_the_analysis_failed(self):
        """The Choice state already routes around this task; the handler guards the same rule so a
        direct invoke can never publish a vector for a version the workflow records FAILED."""
        s3 = _seed(h.FakeS3())
        mod, state = _run(_state(analysisStatus="FAILED"), s3)
        assert state["embeddingStatus"] == "SKIPPED"
        assert s3.puts == []
        mod.events_client.put_events.assert_not_called()

    def test_publishes_when_the_run_carries_no_bucket_id(self):
        """A manifest built from an earlier workflow step's outputs carries no bucketId. The embedding is
        still produced and published with the empty value; the indexer resolves the file's bucket from
        the asset row (registry §3.5), so a chained-step run is indexed like a first-step one."""
        s3 = _seed(h.FakeS3())
        mod, state = _run(_state(bucketId=""), s3)
        assert state["embeddingStatus"] == "SUCCEEDED" and state["embeddingEventPublished"] is True
        mod.embeddings.embed_text.assert_called_once()
        _, document = _document(s3)
        assert document["bucketId"] == ""
        detail = json.loads(mod.events_client.put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert detail["bucketId"] == ""

    def test_the_handler_never_touches_step_functions(self):
        source = io.open(os.path.join(h.LAMBDA_DIR, "generateEmbedding.py"), encoding="utf-8").read()
        assert "stepfunctions" not in source
        assert "send_task_success" not in source and "send_task_failure" not in source


@pytest.mark.unit
class TestSourceText:
    def test_order_and_modalities(self):
        """Composition order: asset context, file identity (database id, the file-type phrase, path and class),
        the genai_* rows, the attribute facts, the existing metadata scope by scope (file, asset, database,
        attributes), and the text excerpt LAST. The previous run's sys_file attribute and the asset's genai_*
        row are skipped; the modality labels follow the parts in order and form the closed set."""
        s3 = _seed(h.FakeS3())
        mod, _state_out = _run(_state(), s3)
        text = _embedded_text(mod)
        assert text.split("\n") == [
            "Gear Pump", "A brass gear pump", "pump", "brass",
            "dbM", "3D model (mesh)", "/models/pump.glb (mesh)",
            "Brass gear pump", "A brass gear pump with an inlet flange.", "pump, gear pump, brass", "mechanical part",
            "Hydraulic Pump", "technical/CAD", "brass, steel", "gold, gray", "pump housing, inlet flange",
            "about 30 cm long", "Z-up, front faces -Y",
            "dimensions: 1.0 x 2.0 x 3.0 m", "triangles: 1,200",
            "PART_NO: GP-100", "PROJECT: Alpha", "SITE: Plant 7", "SOURCE_SCANNER: Leica RTC360",
            "Excerpt of the file text.",
        ]
        assert "stale-etag" not in text and "sys_file" not in text and "genai_asset_keywords" not in text
        _key, document = _document(s3)
        assert document["sourceModalities"] == [
            "asset-metadata", "file-identity", "genai-metadata", "file-attributes",
            "existing-file-metadata", "existing-asset-metadata", "existing-database-metadata",
            "existing-file-attributes", "file-text"]
        assert mod.EXISTING_MODALITY_BY_SCOPE == {
            "fileMetadata": "existing-file-metadata", "assetMetadata": "existing-asset-metadata",
            "databaseMetadata": "existing-database-metadata", "fileAttributes": "existing-file-attributes"}
        # The closed set, by name: every MODALITY_* constant is one of the nine labels and seed-metadata is not one.
        assert {value for name, value in vars(mod).items() if name.startswith("MODALITY_")} == set(
            document["sourceModalities"])

    def test_the_file_type_phrase_is_embedded(self):
        """Spec §6.6: the file-identity part carries the words a user types for a kind of file, between the
        database id and the path line, so a type word in a query favours files of that kind. The phrase follows
        the manifest's class, not the state's."""
        s3 = _seed(h.FakeS3())
        mod, _state_out = _run(_state(), s3)
        lines = _embedded_text(mod).split("\n")
        assert lines[lines.index("dbM") + 1] == "3D model (mesh)"
        assert lines[lines.index("3D model (mesh)") + 1] == "/models/pump.glb (mesh)"
        s3 = _seed(h.FakeS3(), manifest=_manifest(fileClass="video", renderBranch="MEDIA"))
        mod, _state_out = _run(_state(fileClass="mesh"), s3)  # the state still says mesh; the manifest wins
        lines = _embedded_text(mod).split("\n")
        assert lines[lines.index("dbM") + 1] == "video (footage)"
        assert "3D model (mesh)" not in lines and "/models/pump.glb (video)" in lines
        assert "file-identity" in _document(s3)[1]["sourceModalities"]

    @pytest.mark.parametrize("attributes,file_class,expected_lines", [
        ({"sys_geometry": {"dimensions": {"width": 1.0, "height": 2.0, "depth": 3.0}, "extentMax": 3.0, "units": "m"}},
         "mesh", ["dimensions: 1 x 2 x 3 m", "units: m"]),
        ({"sys_image": {"exif": {"make": "Canon", "model": "EOS R5"}}}, "image", ["camera: Canon EOS R5"]),
        ({"sys_pointcloud": {"crs": {"epsg": 4326, "name": "WGS 84", "geographic": True}}}, "pointcloud",
         ["crs: EPSG:4326"]),
        ({"sys_statistics": {"vertices": 800, "faces": 1200, "triangles": 1200, "meshCount": 2}}, "mesh",
         ["vertices: 800", "faces: 1200", "triangles: 1200", "meshes: 2"]),
        ({"sys_media": {"kind": "video", "durationSeconds": 12.5, "width": 1920, "height": 1080}}, "video",
         ["duration: 12.5 s", "resolution: 1920x1080"]),
        ({"sys_document": {"pageCount": 7}}, "document", ["pages: 7"]),
    ])
    def test_attribute_facts_include_units_camera_and_crs(self, attributes, file_class, expected_lines):
        """Spec §6.6: the attribute facts carry dimensions with units, counts, duration, resolution, pages,
        camera and CRS. They come from the promoted ext_* items, so a branch that writes no `facts` still
        contributes every one of them."""
        manifest = _manifest(fileClass=file_class, attributes=dict({"sys_file": {"name": "f"}}, **attributes), facts={})
        s3 = _seed(h.FakeS3(), manifest=manifest)
        mod, _state_out = _run(_state(fileClass=file_class), s3)
        lines = _embedded_text(mod).split("\n")
        for line in expected_lines:
            assert line in lines, (line, lines)
        assert "file-attributes" in _document(s3)[1]["sourceModalities"]

    def test_manifest_facts_win_over_derived_labels(self):
        manifest = _manifest(attributes={"sys_file": {"name": "f"}, "sys_geometry": {"dimensions": [9, 9, 9], "units": "ft"}},
                             facts={"dimensions": "1.0 x 2.0 x 3.0 m"})
        s3 = _seed(h.FakeS3(), manifest=manifest)
        mod, _state_out = _run(_state(), s3)
        lines = _embedded_text(mod).split("\n")
        assert "dimensions: 1.0 x 2.0 x 3.0 m" in lines and "dimensions: 9 x 9 x 9 ft" not in lines
        assert "units: ft" in lines

    def test_duplicates_are_kept_once(self):
        s3 = _seed(h.FakeS3(), envelope=_envelope(description="A brass gear pump with an inlet flange."))
        mod, _state_out = _run(_state(), s3)
        assert _embedded_text(mod).count("A brass gear pump with an inlet flange.") == 1

    def test_the_text_excerpt_gate(self):
        s3 = _seed(h.FakeS3(), config={"embeddingIncludeTextExcerpt": False})
        mod, _state_out = _run(_state(), s3)
        assert "Excerpt of the file text." not in _embedded_text(mod)
        assert "file-text" not in _document(s3)[1]["sourceModalities"]

    def test_the_database_label_appears_iff_the_envelope_carries_database_metadata(self):
        s3 = _seed(h.FakeS3())
        mod, _state_out = _run(_state(), s3)
        assert "SITE: Plant 7" in _embedded_text(mod).split("\n")
        assert "existing-database-metadata" in _document(s3)[1]["sourceModalities"]
        s3 = _seed(h.FakeS3(), envelope=_envelope(database_metadata={}))
        mod, _state_out = _run(_state(), s3)
        lines = _embedded_text(mod).split("\n")
        assert "SITE: Plant 7" not in lines and "PART_NO: GP-100" in lines
        modalities = _document(s3)[1]["sourceModalities"]
        assert "existing-database-metadata" not in modalities and "existing-file-metadata" in modalities

    def test_a_file_whose_only_rows_are_the_pipelines_own_carries_neither_file_scoped_label(self):
        """An earlier run's ext_*/genai_* rows and sys_* attribute groups contribute nothing: neither file-scoped
        label is stored, while the asset and database scopes still contribute (positive control)."""
        s3 = _seed(h.FakeS3(), envelope=_envelope(
            file_metadata={"ext_units": "m", "genai_title": "Old title"},
            file_attributes={"sys_file": '{"etag":"stale-etag"}', "sys_text": '{"encoding":"utf-8"}'}))
        mod, _state_out = _run(_state(), s3)
        text = _embedded_text(mod)
        assert "Old title" not in text and "ext_units" not in text and "stale-etag" not in text
        modalities = _document(s3)[1]["sourceModalities"]
        assert "existing-file-metadata" not in modalities and "existing-file-attributes" not in modalities
        assert "existing-asset-metadata" in modalities and "existing-database-metadata" in modalities

    def test_a_scope_whose_lines_all_duplicate_an_earlier_scope_gets_no_label(self):
        """A label records a distinct line. The asset scope's PROJECT: Alpha (the envelope default) already stands
        in the file scope here and its genai_asset_keywords row is skipped, so the asset scope adds no line and
        carries no label; the database scope beside it does (positive control)."""
        s3 = _seed(h.FakeS3(), envelope=_envelope(file_metadata={"PROJECT": "Alpha"}))
        mod, _state_out = _run(_state(), s3)
        assert _embedded_text(mod).split("\n").count("PROJECT: Alpha") == 1
        modalities = _document(s3)[1]["sourceModalities"]
        assert "existing-file-metadata" in modalities and "existing-asset-metadata" not in modalities
        assert "existing-database-metadata" in modalities

    def test_existing_metadata_is_embedded_whatever_the_prompt_switch_says(self):
        """seedWithExistingMetadata gates the analysis prompt only; the embedding must represent what users
        already know about the file whether or not the model was allowed to see it."""
        s3 = _seed(h.FakeS3(), config={"embeddingIncludeTextExcerpt": True, "seedWithExistingMetadata": False})
        mod, _state_out = _run(_state(), s3)
        lines = _embedded_text(mod).split("\n")
        for line in ("PART_NO: GP-100", "PROJECT: Alpha", "SITE: Plant 7", "SOURCE_SCANNER: Leica RTC360"):
            assert line in lines, line
        modalities = _document(s3)[1]["sourceModalities"]
        assert modalities[4:8] == ["existing-file-metadata", "existing-asset-metadata",
                                   "existing-database-metadata", "existing-file-attributes"]

    def test_a_geojson_value_renders_as_its_type_without_coordinates(self):
        location = '{"type":"Point","coordinates":[8.54,47.37]}'
        s3 = _seed(h.FakeS3(), envelope=_envelope(file_metadata={"PART_NO": "GP-100", "location": location}))
        mod, _state_out = _run(_state(), s3)
        text = _embedded_text(mod)
        assert "location: Point geometry" in text.split("\n")
        assert "8.54" not in text and "coordinates" not in text

    def test_a_long_value_is_cut_at_the_value_limit(self):
        s3 = _seed(h.FakeS3(), envelope=_envelope(file_metadata={"NOTES": "n" * 1000}))
        mod, _state_out = _run(_state(), s3)
        text = _embedded_text(mod)
        assert "NOTES: " + "n" * 400 + "\u2026" in text.split("\n")
        assert "n" * 401 not in text

    def test_the_line_budget_keeps_whole_lines_and_never_starves_the_genai_rows_or_the_excerpt(self):
        """40 file-metadata lines of 409 characters against the 12,000-character budget: 29 whole lines are
        embedded, the 30th would cross it and it plus every later line — the remaining file lines and the
        asset, database and attribute scopes — are dropped (and logged); the genai_* rows and the excerpt
        are outside that budget and all still present."""
        notes = {f"NOTE_{i:02d}": "n" * 400 for i in range(40)}
        s3 = _seed(h.FakeS3(), envelope=_envelope(file_metadata=notes))
        mod, _state_out = _run(_state(), s3)
        lines = _embedded_text(mod).split("\n")
        note_lines = [line for line in lines if line.startswith("NOTE_")]
        assert len(note_lines) == 29 and all(len(line) == 409 for line in note_lines)
        for absent in ("PROJECT: Alpha", "SITE: Plant 7", "SOURCE_SCANNER: Leica RTC360"):
            assert absent not in lines, absent
        for row in _metadata_file()["metadata"]:
            if row["metadataKey"] in mod.GENAI_TEXT_KEYS:
                assert row["metadataValue"] in lines, row["metadataKey"]
        assert lines[-1] == "Excerpt of the file text."
        modalities = _document(s3)[1]["sourceModalities"]
        assert "existing-file-metadata" in modalities and "file-text" in modalities
        assert not any(label in modalities for label in ("existing-asset-metadata", "existing-database-metadata",
                                                         "existing-file-attributes"))
        assert any("14 dropped" in str(call) for call in mod.logger.warning.call_args_list)

    def test_the_excerpt_is_last_so_the_model_window_cuts_it_first(self):
        """With a 40,000-character excerpt the adapter truncates the text to the model window, and every
        existing-metadata line is still embedded: only the excerpt — the one unbounded part — was cut."""
        s3 = _seed(h.FakeS3(), manifest=_manifest(textExcerpt="y" * 40000))
        mod, _state_out = _run(_state(), s3)
        embedded = _embedded_text(mod)
        assert len(embedded) <= mod.embeddings.TITAN_V2_MAX_INPUT_CHARS < 40000
        lines = embedded.split("\n")
        for line in ("PART_NO: GP-100", "PROJECT: Alpha", "SITE: Plant 7", "SOURCE_SCANNER: Leica RTC360"):
            assert line in lines, line
        assert lines.index("SOURCE_SCANNER: Leica RTC360") == len(lines) - 2  # the excerpt is the only later line
        assert lines[-1].startswith("y") and len(lines[-1]) < 40000
        assert _document(s3)[1]["sourceModalities"][-1] == "file-text"

    def test_truncation_goes_through_the_adapter_constant(self):
        s3 = _seed(h.FakeS3(), manifest=_manifest(textExcerpt="y" * 40000))
        mod, _state_out = _run(_state(), s3)
        assert mod.embeddings.TITAN_V2_MAX_INPUT_CHARS == 30_000
        assert len(_embedded_text(mod)) <= mod.embeddings.TITAN_V2_MAX_INPUT_CHARS
        _key, document = _document(s3)
        assert len(document["sourceText"]) == mod.SOURCE_TEXT_STORED_MAX_CHARS == 8000

    def test_a_missing_metadata_file_still_embeds_the_asset_context(self):
        s3 = _seed(h.FakeS3(), metadata_file=False)
        mod, state = _run(_state(), s3)
        assert state["embeddingStatus"] == "SUCCEEDED"
        assert "Gear Pump" in _embedded_text(mod)
        _key, document = _document(s3)
        assert "genai-metadata" not in document["sourceModalities"]
        assert document["analysisModelId"] == ""


@pytest.mark.unit
class TestEmbedding:
    def test_embed_call_shape_and_rounding(self):
        s3 = _seed(h.FakeS3())
        mod, _state_out = _run(_state(), s3)
        kwargs = mod.embeddings.embed_text.call_args.kwargs
        assert kwargs == {"model_id": "amazon.titan-embed-text-v2:0", "dimensions": 4, "purpose": "index",
                          "client": mod.bedrock_runtime}
        _key, document = _document(s3)
        assert document["embedding"] == [0.123456789, -0.5, 0.25, 1.0]


@pytest.mark.unit
class TestDocumentAndEvent:
    def test_document_shape_and_location(self):
        s3 = _seed(h.FakeS3())
        mod, state = _run(_state(), s3)
        key, document = _document(s3)
        expected_hash = hashlib.sha256("/models/pump.glb#v1".encode("utf-8")).hexdigest()
        assert key == f"{AUX_PREFIX}embedding/{expected_hash}.json"
        assert state["embeddingDocumentS3Location"] == f"s3://{AUX}/{key}"
        assert state["embeddingStatus"] == "SUCCEEDED"
        assert state["embeddingEventPublished"] is True
        # Registry §3.6 key set verbatim: the Detail set minus documentS3Location plus embedding and sourceText —
        # 26 keys, no extra keys (a bucket NAME key is not part of the contract).
        assert set(document) == {
            "schemaVersion", "databaseId", "assetId", "filePath", "versionId", "contentEtag", "bucketId",
            "fileClass", "fileExt", "fileSize", "contentType", "embeddingModelId",
            "embeddingDimensions", "analysisModelId", "embedding", "sourceText", "sourceModalities",
            "pipelineExecutionId", "workflowExecutionId", "generatedAt",
            "segmentKey", "segmentKind", "segmentLabel", "segmentStartMs", "segmentEndMs", "segmentCount",
        }
        assert len(document) == 26
        assert document["schemaVersion"] == 1
        assert (document["databaseId"], document["assetId"], document["filePath"], document["versionId"]) == (
            "dbM", "xidM", "/models/pump.glb", "v1")
        assert document["contentEtag"] == "abc123"
        # The manifest's registration id is carried through as given.
        assert document["bucketId"] == "bkt-01"
        # fileExt is the un-dotted table form here (the state's ".glb" is the dotted form).
        assert (document["fileClass"], document["fileExt"], document["fileSize"], document["contentType"]) == (
            "mesh", "glb", 1024, "model/gltf-binary")
        assert document["embeddingModelId"] == "amazon.titan-embed-text-v2:0"
        assert document["embeddingDimensions"] == 4
        assert document["analysisModelId"] == ANALYSIS_MODEL
        assert (document["pipelineExecutionId"], document["workflowExecutionId"]) == ("P1", "E1")
        assert document["generatedAt"].endswith("Z")

    def test_event_detail_is_the_document_without_the_vector_plus_its_location(self):
        s3 = _seed(h.FakeS3())
        mod, _state_out = _run(_state(), s3)
        key, document = _document(s3)
        mod.events_client.put_events.assert_called_once()
        entries = mod.events_client.put_events.call_args.kwargs["Entries"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["Source"] == "vams.prod.execution.E1.pipeline.P1"
        assert entry["DetailType"] == "vector.embedding.ready"
        detail = json.loads(entry["Detail"])
        assert "embedding" not in detail and "sourceText" not in detail
        assert detail["documentS3Location"] == f"s3://{AUX}/{key}"
        expected = {field: value for field, value in document.items() if field not in ("embedding", "sourceText")}
        expected["documentS3Location"] = f"s3://{AUX}/{key}"
        assert detail == expected
        # Registry §3.6 Detail key set verbatim — 25 keys, no extra keys; WP07 asserts the same set.
        assert set(detail) == {
            "schemaVersion", "databaseId", "assetId", "filePath", "versionId", "contentEtag", "bucketId",
            "fileClass", "fileExt", "fileSize", "contentType", "embeddingModelId", "embeddingDimensions",
            "analysisModelId", "sourceModalities", "pipelineExecutionId", "workflowExecutionId", "generatedAt",
            "segmentKey", "segmentKind", "segmentLabel", "segmentStartMs", "segmentEndMs", "segmentCount",
            "documentS3Location",
        }
        assert len(detail) == 25

    def test_the_whole_file_document_and_detail_carry_the_segment_defaults(self):
        """One vector per file version: the six segment fields stand at their whole-file defaults on the document
        and on the event, so a consumer reads one key set whatever kind of document it receives."""
        s3 = _seed(h.FakeS3())
        mod, _state_out = _run(_state(), s3)
        _key, document = _document(s3)
        detail = json.loads(mod.events_client.put_events.call_args.kwargs["Entries"][0]["Detail"])
        defaults = {"segmentKey": "", "segmentKind": "none", "segmentLabel": "", "segmentStartMs": None,
                    "segmentEndMs": None, "segmentCount": 0}
        assert mod.WHOLE_FILE_SEGMENT_FIELDS == defaults
        assert {field: document[field] for field in defaults} == defaults
        assert {field: detail[field] for field in defaults} == defaults

    def test_an_unversioned_file_hashes_null(self):
        s3 = _seed(h.FakeS3())
        _mod, _state_out = _run(_state(versionId=""), s3)
        key, document = _document(s3)
        assert key.endswith(hashlib.sha256("/models/pump.glb#null".encode("utf-8")).hexdigest() + ".json")
        assert document["versionId"] == ""

    def test_no_bus_writes_the_document_without_publishing(self):
        s3 = _seed(h.FakeS3())
        mod, state = _run(_state(), s3, env={"ORCHESTRATION_BUS_NAME": ""})
        _document(s3)
        mod.events_client.put_events.assert_not_called()
        assert state["embeddingStatus"] == "SUCCEEDED" and state["embeddingEventPublished"] is False

    def test_file_ext_and_file_class_take_the_table_forms(self):
        """The vector table, WP07's filter attribute and WP08's fileExtensions push-down all use the
        un-dotted lower-case extension; fileClass is the manifest's post-branch value."""
        s3 = _seed(h.FakeS3(), manifest=_manifest(fileClass="tiles3d", renderBranch="MEDIA"))
        _mod, state = _run(_state(fileExt=".JSON", fileClass="text"), s3)
        _key, document = _document(s3)
        assert (document["fileExt"], document["fileClass"]) == ("json", "tiles3d")
        assert state["fileClass"] == "tiles3d"
        s3 = _seed(h.FakeS3())
        _run(_state(fileExt="none"), s3)
        assert _document(s3)[1]["fileExt"] == "none"


@pytest.mark.unit
class TestCaughtFailures:
    def test_an_access_denied_client_error_is_recorded_and_nothing_is_published(self):
        s3 = _seed(h.FakeS3())
        embed = MagicMock(side_effect=h.client_error("AccessDeniedException", "no embeddings access", "InvokeModel"))
        mod, state = _run(_state(), s3, embed=embed)
        assert state["embeddingStatus"] == "FAILED"
        assert "embeddingDocumentS3Location" not in state
        status = s3.json_at("abkt", STATUS_KEY)
        assert status == {"status": "FAILED", "error": "BedrockAccessDenied", "cause": status["cause"]}
        assert "AccessDeniedException" in status["cause"]
        assert not any(key.startswith(AUX_PREFIX + "embedding/") for _bucket, key in s3.puts)
        mod.events_client.put_events.assert_not_called()

    def test_the_adapter_access_denied_code_is_access_denied(self):
        """The vendored adapter turns AccessDeniedException into EmbeddingModelError with the Bedrock code
        on .code; the most common operator misconfiguration (Titan model access not granted) must surface
        under its own code in execution.status.json, not the generic one."""
        s3 = _seed(h.FakeS3())
        _mod, state = _run(_state(), s3, embed_error=lambda loaded: loaded.embeddings.EmbeddingModelError(
            "Bedrock rejected the embedding request", code="AccessDeniedException"))
        assert state["embeddingStatus"] == "FAILED"
        assert s3.json_at("abkt", STATUS_KEY)["error"] == "BedrockAccessDenied"

    def test_a_throttling_client_error_is_recorded_as_throttled(self):
        """The adapter lets a throttling ClientError propagate once its adaptive retries are spent."""
        s3 = _seed(h.FakeS3())
        embed = MagicMock(side_effect=h.client_error("ThrottlingException", "Too many requests", "InvokeModel"))
        mod, state = _run(_state(), s3, embed=embed)
        assert state["embeddingStatus"] == "FAILED"
        assert s3.json_at("abkt", STATUS_KEY)["error"] == "BedrockThrottled"
        mod.events_client.put_events.assert_not_called()

    def test_a_validation_error_is_an_embedding_error(self):
        s3 = _seed(h.FakeS3())
        _mod, state = _run(_state(), s3, embed_error=lambda loaded: loaded.embeddings.EmbeddingModelError(
            "Input is too long for requested model.", code="ValidationException"))
        assert state["embeddingStatus"] == "FAILED"
        assert s3.json_at("abkt", STATUS_KEY)["error"] == "BedrockEmbeddingError"

    def test_an_adapter_error_is_recorded_the_same_way(self):
        s3 = _seed(h.FakeS3())
        mod, state = _run(_state(), s3, embed_error=lambda loaded: loaded.embeddings.EmbeddingModelError(
            "input too long for the model"))
        assert state["embeddingStatus"] == "FAILED"
        assert s3.json_at("abkt", STATUS_KEY)["error"] == "BedrockEmbeddingError"
        mod.events_client.put_events.assert_not_called()

    def test_a_publish_exception_raises(self):
        s3 = _seed(h.FakeS3())
        with pytest.raises(Exception, match="events:PutEvents"):
            _run(_state(), s3, put_events=MagicMock(side_effect=RuntimeError("AccessDenied: events:PutEvents")))

    def test_a_failed_entry_raises(self):
        s3 = _seed(h.FakeS3())
        with pytest.raises(RuntimeError, match="FailedEntryCount"):
            _run(_state(), s3, put_events=MagicMock(return_value={
                "FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "x"}]}))


@pytest.mark.unit
class TestContentChunks:
    def test_the_whole_file_document_is_published_first_with_the_chunk_count(self):
        s3 = _seed_text(h.FakeS3())
        mod, state = _run(_state(), s3)
        details = _details(mod)
        assert len(details) == 4
        whole = details[0]
        assert whole["segmentKind"] == "none" and whole["segmentKey"] == "" and whole["segmentCount"] == 3
        expected_key = f"{AUX_PREFIX}embedding/{hashlib.sha256('/models/pump.glb#v1'.encode('utf-8')).hexdigest()}.json"
        assert whole["documentS3Location"] == f"s3://{AUX}/{expected_key}"
        # The whole-file document is written before the first chunk document.
        embedding_puts = [key for bucket, key in s3.puts if key.startswith(AUX_PREFIX + "embedding/") and not key.endswith("/summary.json")]
        assert embedding_puts[0] == expected_key and len(embedding_puts) == 4
        assert state["embeddingStatus"] == "SUCCEEDED" and state["embeddingEventPublished"] is True
        assert state["embeddingDocumentS3Location"] == f"s3://{AUX}/{expected_key}"

    def test_chunk_documents_and_events(self):
        s3 = _seed_text(h.FakeS3())
        mod, _state_out = _run(_state(), s3)
        chunks = [(key, document) for key, document in _documents(s3) if document["segmentKind"] == "textChunk"]
        assert [document["segmentKey"] for _key, document in chunks] == ["c000001", "c000002", "c000003"]
        for key, document in chunks:
            expected_hash = hashlib.sha256(f"/models/pump.glb#v1#{document['segmentKey']}".encode("utf-8")).hexdigest()
            assert key == f"{AUX_PREFIX}embedding/{expected_hash}.json"
            assert (document["segmentStartMs"], document["segmentEndMs"], document["segmentCount"]) == (None, None, 3)
            assert len(document) == 26 and len(document["sourceText"]) <= 8000
            # Everything but the vector, the text, the modalities and the six segment fields is the whole-file value.
            assert (document["databaseId"], document["assetId"], document["filePath"], document["versionId"],
                    document["bucketId"], document["contentEtag"], document["analysisModelId"]) == (
                "dbM", "xidM", "/models/pump.glb", "v1", "bkt-01", "abc123", ANALYSIS_MODEL)
            assert document["fileClass"] == "document" and document["sourceModalities"] == [
                "asset-metadata", "file-identity", "genai-metadata", "file-text"]
        labels = [document["segmentLabel"] for _key, document in chunks]
        assert labels == ["chunk 1/3 \u00b7 page 1", "chunk 2/3 \u00b7 page 2", "chunk 3/3 \u00b7 page 3"]
        first = chunks[0][1]
        lines = first["sourceText"].split("\n")
        assert lines[:5] == ["Gear Pump", "document (PDF or office)", "/models/pump.glb", "Brass gear pump",
                             "chunk 1/3 \u00b7 page 1"]
        assert lines[5].startswith("Sentence 0 of the manual.") and len(lines) == 6
        # One embed call per document, each through the adapter with purpose "index".
        assert mod.embeddings.embed_text.call_count == 4
        assert all(call.kwargs["purpose"] == "index" for call in mod.embeddings.embed_text.call_args_list)
        chunk_details = _details(mod)[1:]
        assert len(chunk_details) == 3
        for detail in chunk_details:
            document = dict(s3.json_at(AUX, detail["documentS3Location"][len(f"s3://{AUX}/"):]))
            expected = {field: value for field, value in document.items() if field not in ("embedding", "sourceText")}
            expected["documentS3Location"] = detail["documentS3Location"]
            assert detail == expected and len(detail) == 25

    def test_events_are_sent_ten_per_put_events_call(self):
        s3 = _seed_text(h.FakeS3(), text="x" * 35200, pages=0)
        mod, state = _run(_state(), s3)
        sizes = [len(call.kwargs["Entries"]) for call in mod.events_client.put_events.call_args_list]
        assert sizes == [1, 10, 10, 5]
        assert state["contentChunks"] == {"count": 25, "dropped": 0, "skipped": None}
        assert [document["segmentLabel"] for _key, document in _documents(s3)][1:3] == ["chunk 1/25", "chunk 2/25"]

    def test_content_chunks_state_summary_and_metadata_row(self):
        s3 = _seed_text(h.FakeS3())
        _mod, state = _run(_state(), s3)
        assert state["contentChunks"] == {"count": 3, "dropped": 0, "skipped": None}
        summary = s3.json_at(AUX, SUMMARY_KEY)
        assert summary["schemaVersion"] == 1 and summary["contentChunks"] == {"count": 3, "dropped": 0, "skipped": None}
        assert summary["wholeFileDocument"] == state["embeddingDocumentS3Location"]
        assert summary["videoSegmentCount"] == 0 and summary["embeddingModelId"] == "amazon.titan-embed-text-v2:0"
        assert summary["generatedAt"].endswith("Z")
        rows = {row["metadataKey"]: row for row in s3.json_at("abkt", METADATA_FILE_KEY)["metadata"]}
        assert rows["genai_content_chunk_count"] == {"metadataKey": "genai_content_chunk_count", "metadataValue": "3",
                                                     "metadataValueType": "number"}
        assert rows["genai_title"]["metadataValue"] == "Brass gear pump"  # the analysis step's rows are kept
        assert list(rows)[-1] == "genai_content_chunk_count"

    def test_dropped_chunks_are_counted(self):
        s3 = _seed_text(h.FakeS3())
        mod = h.load_handler("generateEmbedding", {"EMBEDDING_DIMENSIONS": "4"})
        mod.s3_client = s3
        mod.events_client = MagicMock()
        mod.events_client.put_events = MagicMock(return_value={"FailedEntryCount": 0, "Entries": [{}]})
        mod.embeddings.embed_text = MagicMock(return_value=list(VECTOR))
        mod.contentChunks.CONTENT_CHUNK_MAX = 2
        state = mod.lambda_handler(_state(), MagicMock())
        assert state["contentChunks"] == {"count": 2, "dropped": 1, "skipped": None}
        assert s3.json_at(AUX, SUMMARY_KEY)["contentChunks"] == {"count": 2, "dropped": 1, "skipped": None}
        assert {row["metadataKey"]: row["metadataValue"] for row in s3.json_at("abkt", METADATA_FILE_KEY)["metadata"]}[
            "genai_content_chunk_count"] == "2"
        documents = _documents(s3)
        assert len(documents) == 3
        assert all(document["segmentCount"] == 2 for _key, document in documents)
        assert any("1 dropped" in str(call) for call in mod.logger.warning.call_args_list)

    def test_chunking_off_publishes_the_whole_file_only(self):
        s3 = _seed_text(h.FakeS3(), config={"embeddingIncludeTextExcerpt": True, "contentChunking": False})
        mod, state = _run(_state(), s3)
        assert len(_details(mod)) == 1 and _details(mod)[0]["segmentCount"] == 0
        assert state["contentChunks"] == {"count": 0, "dropped": 0, "skipped": None}
        assert s3.json_at(AUX, SUMMARY_KEY)["contentChunks"] == {"count": 0, "dropped": 0, "skipped": None}
        assert "genai_content_chunk_count" not in {row["metadataKey"] for row in s3.json_at("abkt", METADATA_FILE_KEY)["metadata"]}

    def test_no_captured_text_means_no_chunks(self):
        s3 = _seed(h.FakeS3())
        mod, state = _run(_state(), s3)
        assert len(_documents(s3)) == 1 and len(_details(mod)) == 1
        assert state["contentChunks"] == {"count": 0, "dropped": 0, "skipped": None}
        assert s3.json_at(AUX, SUMMARY_KEY)["videoSegmentCount"] == 0

    def test_a_file_the_branch_skipped_for_size_publishes_the_whole_file_only(self):
        # The media branch captured nothing because the file exceeds CONTENT_EMBED_MAX_FILE_BYTES; the manifest
        # carries the reason and no text location.
        s3 = _seed(h.FakeS3(), manifest=_manifest(fileClass="document", renderBranch="MEDIA",
                                                   fullTextSkipped="size", fullTextChars=0))
        mod, state = _run(_state(), s3)
        assert len(_documents(s3)) == 1 and len(_details(mod)) == 1
        whole = _details(mod)[0]
        assert whole["segmentKind"] == "none" and whole["segmentCount"] == 0
        assert state["embeddingStatus"] == "SUCCEEDED" and state["embeddingEventPublished"] is True
        assert state["contentChunks"] == {"count": 0, "dropped": 0, "skipped": "size"}
        assert s3.json_at(AUX, SUMMARY_KEY)["contentChunks"] == {"count": 0, "dropped": 0, "skipped": "size"}
        assert "genai_content_chunk_count" not in {row["metadataKey"] for row in s3.json_at("abkt", METADATA_FILE_KEY)["metadata"]}
        assert mod.embeddings.embed_text.call_count == 1  # the whole-file vector only
        assert not any(key.startswith(AUX_PREFIX + "text/") for _bucket, key in s3.puts)

    def test_a_chunk_embedding_failure_is_recorded_as_an_embedding_error(self):
        s3 = _seed_text(h.FakeS3())
        embed = MagicMock(side_effect=[list(VECTOR), h.client_error("ValidationException", "input too long", "InvokeModel")])
        mod, state = _run(_state(), s3, embed=embed)
        assert state["embeddingStatus"] == "FAILED" and state["embeddingEventPublished"] is True
        assert state["contentChunks"] == {"count": 0, "dropped": 0, "skipped": None}
        status = s3.json_at("abkt", STATUS_KEY)
        assert status["error"] == "BedrockEmbeddingError" and status["cause"].startswith("chunk c000001: ")
        assert len(_documents(s3)) == 1 and len(_details(mod)) == 1  # the whole-file vector stands
        assert s3.json_at(AUX, SUMMARY_KEY)["contentChunks"] == {"count": 0, "dropped": 0, "skipped": None}
        assert "genai_content_chunk_count" not in {row["metadataKey"] for row in s3.json_at("abkt", METADATA_FILE_KEY)["metadata"]}
        assert "embeddingDocumentS3Location" in state

    def test_a_throttled_chunk_embedding_is_recorded_as_throttled(self):
        s3 = _seed_text(h.FakeS3())
        embed = MagicMock(side_effect=[list(VECTOR), h.client_error("ThrottlingException", "slow down", "InvokeModel")])
        mod, state = _run(_state(), s3, embed=embed)
        assert state["embeddingStatus"] == "FAILED" and state["contentChunks"] == {"count": 0, "dropped": 0, "skipped": None}
        status = s3.json_at("abkt", STATUS_KEY)
        assert status["error"] == "BedrockThrottled" and status["cause"].startswith("chunk c000001: ")
        assert len(_details(mod)) == 1  # the whole-file vector stands

    def test_an_access_denied_chunk_embedding_is_recorded_as_access_denied(self):
        s3 = _seed_text(h.FakeS3())
        embed = MagicMock(side_effect=[list(VECTOR), h.client_error("AccessDeniedException", "no model access", "InvokeModel")])
        mod, state = _run(_state(), s3, embed=embed)
        assert state["embeddingStatus"] == "FAILED" and state["contentChunks"] == {"count": 0, "dropped": 0, "skipped": None}
        status = s3.json_at("abkt", STATUS_KEY)
        assert status["error"] == "BedrockAccessDenied" and status["cause"].startswith("chunk c000001: ")
        assert "AccessDeniedException" in status["cause"] and len(_details(mod)) == 1

    def test_a_failure_after_the_first_chunk_publishes_what_was_written(self):
        s3 = _seed_text(h.FakeS3())
        embed = MagicMock(side_effect=[list(VECTOR), list(VECTOR),
                                      h.client_error("ValidationException", "input too long", "InvokeModel")])
        mod, state = _run(_state(), s3, embed=embed)
        assert state["embeddingStatus"] == "FAILED"
        assert state["contentChunks"] == {"count": 1, "dropped": 0, "skipped": None}
        assert len(_documents(s3)) == 2  # the whole-file document and chunk 1
        details = _details(mod)
        assert len(details) == 2 and details[1]["segmentKey"] == "c000001"
        status = s3.json_at("abkt", STATUS_KEY)
        assert status["error"] == "BedrockEmbeddingError" and status["cause"].startswith("chunk c000002: ")
        assert s3.json_at(AUX, SUMMARY_KEY)["contentChunks"] == {"count": 1, "dropped": 0, "skipped": None}
        rows = {row["metadataKey"]: row["metadataValue"] for row in s3.json_at("abkt", METADATA_FILE_KEY)["metadata"]}
        assert rows["genai_content_chunk_count"] == "1"

    @pytest.mark.parametrize("failure, marker", [
        ({"FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "try again"}]},
         "FailedEntryCount=1"),
        (h.client_error("InternalException", "bus unavailable", "PutEvents"), "InternalException"),
    ], ids=["rejected-entry", "raised"])
    def test_a_failed_chunk_event_batch_is_a_caught_publish_failure(self, failure, marker):
        # 25 chunks: the whole-file event, then batches of ten, ten and five; the second chunk batch fails.
        s3 = _seed_text(h.FakeS3(), text="x" * 35200, pages=0)
        accepted = {"FailedEntryCount": 0, "Entries": [{}]}
        mod, state = _run(_state(), s3, put_events=MagicMock(side_effect=[accepted, accepted, failure]))
        assert state["embeddingStatus"] == "FAILED" and state["embeddingEventPublished"] is True
        assert state["contentChunks"] == {"count": 10, "dropped": 0, "skipped": None}
        sizes = [len(call.kwargs["Entries"]) for call in mod.events_client.put_events.call_args_list]
        assert sizes == [1, 10, 10]  # nothing is sent after the failed batch
        details = _details(mod)
        assert [detail["segmentKey"] for detail in details[1:11]] == [f"c{n:06d}" for n in range(1, 11)]
        assert details[11]["segmentKey"] == "c000011"  # the failed batch's first key names the cause
        assert len(_documents(s3)) == 21  # the whole-file document and the twenty chunk documents written so far
        status = s3.json_at("abkt", STATUS_KEY)
        assert status["status"] == "FAILED" and status["error"] == "SegmentPublishError"
        assert status["cause"].startswith("chunk c000011: ") and marker in status["cause"]
        assert s3.json_at(AUX, SUMMARY_KEY)["contentChunks"] == {"count": 10, "dropped": 0, "skipped": None}
        rows = {row["metadataKey"]: row["metadataValue"] for row in s3.json_at("abkt", METADATA_FILE_KEY)["metadata"]}
        assert rows["genai_content_chunk_count"] == "10"

    def test_the_video_window_count_reaches_the_whole_file_document(self):
        s3 = _seed(h.FakeS3())
        mod, _state_out = _run(_state(videoSegmentCount=12), s3)
        _key, document = _document(s3)
        assert document["segmentCount"] == 12 and document["segmentKind"] == "none"
        assert _details(mod)[0]["segmentCount"] == 12
        assert s3.json_at(AUX, SUMMARY_KEY)["videoSegmentCount"] == 12
