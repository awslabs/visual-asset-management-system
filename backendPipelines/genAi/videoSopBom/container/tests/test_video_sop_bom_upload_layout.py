# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 14 layout (flat under sop-bom/, metadata and results names, collected failures) and the
end-to-end `run()` through fakes: full mode, transcript mode, rejections (a speechless input included).

Run from the container directory:  python -m pytest tests/test_video_sop_bom_upload_layout.py -q
"""

import json
import logging
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import (  # noqa: E402
    EMPTY_TRANSCRIPT,
    FakeBedrock,
    FakeS3,
    FakeSfn,
    FakeTranscribe,
    bare_response,
    client_error,
    completed_job,
    failed_job,
    fake_run_factory,
    make_clients,
    make_definition,
    make_transcript,
    text_response,
    tool_use_response,
)


def _stage_outputs(work_dir, files, metadata=True, results=True):
    from video_sop_bom_pipeline.upload import output_dirs

    dirs = output_dirs(str(work_dir))
    for name in files:
        with open(os.path.join(dirs["files"], name), "wb") as handle:
            handle.write(b"data")
    if metadata:
        with open(os.path.join(dirs["metadata"], "asset.metadata.json"), "w", encoding="utf-8") as handle:
            handle.write("{}")
    if results:
        with open(os.path.join(dirs["results"], "summary.json"), "w", encoding="utf-8") as handle:
            handle.write("{}")
    return dirs


class TestUploadLayout:
    FILES = ["transcript.json", "sop.json", "bom.csv", "keyframe-0000-00h01m15s.jpg"]

    def test_files_are_flat_under_sop_bom_and_metadata_results_are_named_exactly(self, tmp_path):
        from video_sop_bom_pipeline import upload

        s3, definition = FakeS3(), make_definition()
        _stage_outputs(tmp_path, self.FILES)
        report = upload.upload_outputs(make_clients(s3=s3), definition, str(tmp_path))
        keys = s3.keys("run-bucket")
        prefix = definition["outputs"]["files"] + "sop-bom/"
        file_keys = [key for key in keys if key.startswith(definition["outputs"]["files"])]
        assert file_keys == sorted(prefix + name for name in self.FILES)
        assert all(key[len(prefix):].count("/") == 0 for key in file_keys), "no folder below sop-bom/"
        assert definition["outputs"]["metadata"] + "asset.metadata.json" in keys
        assert definition["outputs"]["results"] + "summary.json" in keys
        assert not any(key.endswith("execution.status.json") for key in keys)
        assert not any("/previews/" in key for key in keys)
        assert report.file_count == 4
        assert report.summary_key == definition["outputs"]["results"] + "summary.json"
        assert report.failed == []

    def test_failed_uploads_are_collected_into_one_readable_pipeline_error(self, tmp_path):
        from video_sop_bom_pipeline import upload
        from video_sop_bom_pipeline.errors import PipelineRejection

        s3, definition = FakeS3(), make_definition()
        prefix = definition["outputs"]["files"] + "sop-bom/"
        s3.fail_upload_keys = {prefix + "bom.csv", prefix + "sop.json"}
        _stage_outputs(tmp_path, self.FILES)
        with pytest.raises(PipelineRejection) as raised:
            upload.upload_outputs(make_clients(s3=s3), definition, str(tmp_path))
        assert raised.value.code == "VideoSopBomPipelineError"
        assert raised.value.cause == "2 of 6 output uploads failed; first: bom.csv"
        assert prefix + "transcript.json" in s3.keys("run-bucket"), "the remaining uploads are still attempted"

    def test_a_folder_below_sop_bom_is_refused(self, tmp_path):
        from video_sop_bom_pipeline import upload
        from video_sop_bom_pipeline.errors import PipelineRejection

        dirs = _stage_outputs(tmp_path, ["transcript.json"])
        os.makedirs(os.path.join(dirs["files"], "extra"))
        with pytest.raises(PipelineRejection) as raised:
            upload.upload_outputs(make_clients(), make_definition(), str(tmp_path))
        assert "flat" in raised.value.cause


FULL_FILES = {
    "transcript.json", "transcript.txt", "transcript.vtt", "transcript.srt", "video-timeline.json", "sop.json", "sop.md",
    "bom.json", "bom.md", "bom.csv", "lab-summary.json", "lab-summary.md", "frames.json", "analysis-report.json",
}
TRANSCRIPT_FILES = {"transcript.json", "transcript.txt", "transcript.vtt", "transcript.srt", "video-timeline.json", "analysis-report.json"}
SUBTITLE_FILES = {"transcript.vtt", "transcript.srt"}
SPEECH = make_transcript([(0.5, 0.9, "Remove"), (1.0, 1.5, "the"), (1.6, 2.0, "cover."), (99.0, 99.5, "Lift"), (99.6, 100.5, "the"), (100.6, 101.0, "board.")])


def _window_payload():
    step = {
        "tools": [], "fasteners": [], "locations": [], "dependencies": [], "motion": {"allowed": [], "restricted": []},
        "force": {"amount": None, "indicator": None}, "failure_modes": [], "notes": "",
    }
    return {
        "product_name_from_narration": "In-Sight",
        "steps": [
            dict(step, step=1, timestamp_seconds=0.5, action="remove", component="cover"),
            dict(step, step=2, timestamp_seconds=100.0, action="lift", component="board", dependencies=[{"step": 1, "text": "cover off"}]),
        ],
        "components": [{
            "part_level": 1, "part_type": "Enclosure", "part_description": "rear cover", "qty": 1, "material_or_component_type": "PC/ABS",
            "mass_g_per_unit": 42.0, "primary_manufacturing_process": "Molding - Plastics", "first_seen_timestamp_seconds": 0.5,
        }],
        "key_moments": [
            {"timestamp_seconds": 1.0, "reason": "cover reveal", "expected_content": "rear cover"},
            {"timestamp_seconds": 100.0, "reason": "board lifted", "expected_content": "PCBA"},
        ],
    }


def _finalize_payload():
    return {
        "bom_rows": [{
            "part_level": 1, "part_type": "Enclosure", "part_description": "rear cover", "qty": 1, "material_or_component_type": "PC/ABS",
            "mass_g_per_unit": 42.0, "primary_manufacturing_process": "Molding - Plastics", "manufacturing_country": "US", "alternative": "No",
            "manufacturer_part_number": "", "material_notes": None,
        }],
        "step_bom_refs": [{"step": 1, "row_indexes": [0]}],
        "dependency_edges": [],
        "summary": "Two steps.",
        "safety_notes": ["Unplug first."],
        "lab_summary": {
            "product_description": "A smart camera.", "product_source_url": None, "background": "b",
            "materials_methodology": "m", "safety_considerations": "s", "existing_bom_provided": False,
            "existing_bom_notes": "No BOM was provided by the supplier for this request.", "primary_manufacturing_processes": "p",
            "key_observations": ["k"], "comparative_analysis": "No BOM was provided so comparative analysis could not be performed.",
        },
    }


def _bedrock_script(finalize=None):
    """The happy-path responder; `finalize` overrides the answer to the finalize call (a response dict or an Exception)."""
    from video_sop_bom_pipeline import bedrock

    def responder(request, n):
        name = request["toolConfig"]["toolChoice"]["tool"]["name"]
        if name == bedrock.WINDOW_TOOL:
            return tool_use_response(name, _window_payload())
        if name == bedrock.VISION_TOOL:
            indexes = [int(block["text"].split("momentIndex=", 1)[1].split()[0])
                       for block in request["messages"][0]["content"] if "momentIndex=" in block.get("text", "")]
            return tool_use_response(name, {"frame_analyses": [
                {"momentIndex": i, "confirmed": True, "components_seen": ["cover"], "corrections": None, "additional_details": ""} for i in indexes
            ]})
        return finalize if finalize is not None else tool_use_response(name, _finalize_payload())

    return responder


def _skipping_run_factory(durations):
    """A media._run whose ffmpeg refuses the second key frame (basename keyframe-0001-...), so stage 10 skips it."""
    inner = fake_run_factory(durations)

    def _run(argv, timeout):
        if argv[0] == "ffmpeg" and os.path.basename(argv[-1]).startswith("keyframe-0001"):
            return 1, "ffmpeg: invalid seek"
        return inner(argv, timeout)

    return _run


def _seeding_states(s3, transcript, subtitles):
    def states(request):
        bucket, key = request["OutputBucketName"], request["OutputKey"]
        s3.put_bytes(bucket, key, json.dumps(transcript).encode("utf-8"))
        if subtitles:
            s3.put_bytes(bucket, key[:-5] + ".vtt", b"WEBVTT\n")
            s3.put_bytes(bucket, key[:-5] + ".srt", b"1\n")
        return [completed_job(request, subtitles=subtitles)]

    return states


def _run(tmp_path, monkeypatch, definition=None, transcript=SPEECH, subtitles=True, responder=None, run_factory=None, job_states=None):
    from video_sop_bom_pipeline import media, pipeline

    definition = definition or make_definition()
    s3 = FakeS3()
    for entry, size in zip(definition["inputFiles"], (1000, 2000, 3000, 4000)):
        s3.put_bytes(entry["bucket"], entry["key"], b"v" * size)
    transcribe = FakeTranscribe(job_states=job_states or _seeding_states(s3, transcript, subtitles))
    bedrock = FakeBedrock(responder or _bedrock_script())
    probe_bedrock = FakeBedrock(lambda request, n: text_response("pong"))
    sfn = FakeSfn()
    clients = make_clients(s3=s3, transcribe=transcribe, bedrock=bedrock, sfn=sfn, preflight_bedrock=probe_bedrock)
    # The video containers report 10 s each while the extracted tracks report 75 s + 80 s: the duration cap sums the
    # audio tracks, so a sum over the container probes (20 s) could neither trip the 1-minute cap in the
    # rejection test nor produce the 155.0 s figure asserted below.
    durations = {"0_part1.mp4": 10.0, "1_part2.MP4": 10.0, "0.flac": 75.0, "1.flac": 80.0, "combined.flac": 155.0}
    monkeypatch.setattr(media, "_run", (run_factory or fake_run_factory)(durations))
    status = pipeline.run(definition, media, clients, work_dir=str(tmp_path / "work"), sleep=lambda seconds: None)
    return status, s3, transcribe, bedrock, sfn


def _deliverables(s3, definition):
    prefix = definition["outputs"]["files"] + "sop-bom/"
    names = {key[len(prefix):] for key in s3.keys("run-bucket") if key.startswith(prefix)}
    assert all("/" not in name for name in names), names
    return names


def _load(s3, bucket, key):
    return json.loads(s3.objects[(bucket, key)].decode("utf-8"))


class TestRunFullMode:
    def test_full_mode_writes_the_full_set_flat_and_reports_success(self, tmp_path, monkeypatch, caplog, vsb_task_token):
        with caplog.at_level(logging.INFO):
            status, s3, transcribe, bedrock, sfn = _run(tmp_path, monkeypatch)
        definition = make_definition()
        assert status == "SUCCEEDED"
        names = _deliverables(s3, definition)
        assert names == FULL_FILES | {"keyframe-0000-00h00m01s.jpg", "keyframe-0001-00h01m40s.jpg"}

        # Preflight ran before anything else touched Transcribe or the inputs.
        assert transcribe.get_calls[0] == "vams-video-sop-bom-preflight"
        assert s3.downloads[0][2] == {"VersionId": "v1"} and s3.downloads[1][2] == {}

        payload = json.loads(sfn.successes[0]["output"])
        assert payload["reporter"] == "video_sop_bom_pipeline" and payload["status"] == "SUCCEEDED"
        assert payload["fileCount"] == len(names)
        assert payload["summaryKey"] == definition["outputs"]["results"] + "summary.json"
        assert sfn.failures == []

        summary = _load(s3, "run-bucket", definition["outputs"]["results"] + "summary.json")
        assert summary["mode"] == "full" and summary["status"] == "SUCCEEDED"
        assert summary["bedrock"]["calls"] == len(bedrock.calls) == 3, "one window, one vision batch, one finalize"
        assert summary["limits"]["configured"] == {"maxVideoFiles": 4, "maxVideoFileSizeMb": 4096, "maxTotalInputSizeMb": 16384, "maxTotalDurationMinutes": 240}
        assert "maxKeyFramesCeiling" not in summary["limits"]["configured"], "limits.configured carries the four caps only"
        assert summary["limits"]["observed"]["totalDurationSeconds"] == 155.0, "the extracted tracks (75 + 80), not the 10 s containers"
        assert summary["limits"]["observed"]["subtitles"] == ["srt", "vtt"]
        assert summary["warnings"] == []
        assert summary["config"] == definition["config"]
        assert summary["paths"]["sop"] == "/sop-bom/exec-0001/sop.json"
        markers = [record.getMessage() for record in caplog.records if record.getMessage().startswith("BEDROCK_CALL ")]
        assert len(markers) == len(bedrock.calls)
        stage_lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("STAGE ")]
        assert any(line.startswith("STAGE 14 upload end") for line in stage_lines)
        assert all(line.split()[3] in ("start", "end") for line in stage_lines), "the registry's two marker states only"

        files_prefix = definition["outputs"]["files"] + "sop-bom/"
        csv_bytes = s3.objects[("run-bucket", files_prefix + "bom.csv")]
        from video_sop_bom_pipeline.vocab import LCA_BOM_COLUMNS

        assert csv_bytes.split(b"\n", 1)[0] + b"\n" == (",".join(LCA_BOM_COLUMNS) + "\n").encode("utf-8")
        assert csv_bytes.count(b"\n") == 2
        bom = _load(s3, "run-bucket", files_prefix + "bom.json")
        assert set(bom) == {"product_name", "part_level_base", "rows"} and bom["part_level_base"] == "0", "the config's partLevelBase string, echoed"
        assert bom["rows"][0]["lab_part_number"] == "cognex-in-sight-2800-001" and bom["rows"][0]["manufacturing_country"] == "US"
        assert bom["rows"][0]["material_composition"] is None, "a blank cell is null in the JSON row"

        metadata = _load(s3, "run-bucket", definition["outputs"]["metadata"] + "asset.metadata.json")
        entries = {entry["metadataKey"]: entry["metadataValue"] for entry in metadata["metadata"]}
        assert set(entries) == {
            "sopBom_latestExecutionId", "sopBom_mode", "sopBom_videoCount", "sopBom_totalDurationSeconds", "sopBom_language",
            "sopBom_transcriptPath", "sopBom_productName", "sopBom_componentCount", "sopBom_stepCount", "sopBom_totalMassG",
            "sopBom_sopPath", "sopBom_bomPath", "sopBom_labSummaryPath",
        }
        assert entries["sopBom_transcriptPath"] == "/sop-bom/exec-0001/transcript.json"
        assert entries["sopBom_bomPath"] == "/sop-bom/exec-0001/bom.csv"
        assert entries["sopBom_totalDurationSeconds"] == "155.0"
        assert entries["sopBom_productName"] == "Cognex In-Sight 2800", "PRODUCT_NAME tag empty -> asset name, never the previous run's metadata"
        assert entries["sopBom_stepCount"] == "2" and entries["sopBom_componentCount"] == "1"

        sop = _load(s3, "run-bucket", files_prefix + "sop.json")
        assert sop["title"] == "Cognex In-Sight 2800 — teardown SOP"
        assert sop["product_name_from_narration"] == "In-Sight"
        assert sop["steps"][0]["frame_ref"] == "/sop-bom/exec-0001/keyframe-0000-00h00m01s.jpg"
        assert sop["steps"][1]["video_index"] == 1 and sop["steps"][1]["local_timestamp_seconds"] == 25.0
        assert sop["steps"][1]["dependencies"] == [{"step": 1, "text": "cover off"}]
        frames = _load(s3, "run-bucket", files_prefix + "frames.json")
        assert set(frames) == {"executionId", "frames", "skipped", "video_timeline"} and frames["executionId"] == "exec-0001"
        assert [frame["momentIndex"] for frame in frames["frames"]] == [0, 1] and frames["skipped"] == []
        assert frames["frames"][1]["path"] == "/sop-bom/exec-0001/keyframe-0001-00h01m40s.jpg"
        assert frames["frames"][1]["file"] == "keyframe-0001-00h01m40s.jpg"
        assert frames["frames"][1]["video_index"] == 1 and frames["frames"][1]["local_timestamp"] == 25.0
        assert "frame_key" not in frames["frames"][0] and frames["frames"][0]["path"].startswith("/sop-bom/"), \
            "path is the asset path; the scratch path never leaves the container"
        report = _load(s3, "run-bucket", files_prefix + "analysis-report.json")
        assert report["windows"] == 1 and report["framesExtracted"] == 2 and report["framesSkipped"] == []
        assert report["limits"]["configured"] == summary["limits"]["configured"]
        assert report["modelId"] == "global.anthropic.claude-sonnet-5"
        assert [stage["name"] for stage in report["stages"]][:3] == ["preflight", "disk", "download"]
        assert s3.keys("aux-bucket") == [], "the whole aux prefix is deleted on success"

    def test_a_skipped_frame_is_listed_logged_and_leaves_its_step_without_a_frame_ref(self, tmp_path, monkeypatch, caplog, vsb_task_token):
        with caplog.at_level(logging.INFO):
            status, s3, _, bedrock, sfn = _run(tmp_path, monkeypatch, run_factory=_skipping_run_factory)
        definition = make_definition()
        assert status == "SUCCEEDED"
        names = _deliverables(s3, definition)
        assert names == FULL_FILES | {"keyframe-0000-00h00m01s.jpg"}
        prefix = definition["outputs"]["files"] + "sop-bom/"
        frames = _load(s3, "run-bucket", prefix + "frames.json")
        assert [frame["momentIndex"] for frame in frames["frames"]] == [0]
        assert len(frames["skipped"]) == 1 and frames["skipped"][0]["momentIndex"] == 1
        assert "ffmpeg exit 1" in frames["skipped"][0]["reason"] and set(frames["skipped"][0]) == {"momentIndex", "timestamp_seconds", "reason"}
        report = _load(s3, "run-bucket", prefix + "analysis-report.json")
        assert report["framesRequested"] == 2 and report["framesExtracted"] == 1
        assert report["framesSkipped"][0]["momentIndex"] == 1 and report["framesSkipped"][0]["timestamp_seconds"] == 100.0
        assert any(record.getMessage().startswith("frame skipped momentIndex=1 ") for record in caplog.records)
        sop = _load(s3, "run-bucket", prefix + "sop.json")
        assert sop["steps"][0]["frame_ref"] == "/sop-bom/exec-0001/keyframe-0000-00h00m01s.jpg" and sop["steps"][1]["frame_ref"] is None
        assert len(bedrock.calls) == 3, "the surviving frame still goes through one vision batch"
        assert json.loads(sfn.successes[0]["output"])["fileCount"] == len(FULL_FILES) + 1

    def test_generate_lab_summary_false_omits_the_files_and_the_metadata_key(self, tmp_path, monkeypatch, vsb_task_token):
        definition = make_definition(config={"generateLabSummary": False})
        status, s3, _, _, _ = _run(tmp_path, monkeypatch, definition=definition)
        assert status == "SUCCEEDED"
        names = _deliverables(s3, definition)
        assert "lab-summary.json" not in names and "lab-summary.md" not in names
        metadata = _load(s3, "run-bucket", definition["outputs"]["metadata"] + "asset.metadata.json")
        assert "sopBom_labSummaryPath" not in {entry["metadataKey"] for entry in metadata["metadata"]}

    def test_product_name_tag_beats_the_asset_name(self, tmp_path, monkeypatch, vsb_task_token):
        definition = make_definition(config={"productName": "Widget X"})
        status, s3, _, _, _ = _run(tmp_path, monkeypatch, definition=definition)
        assert status == "SUCCEEDED"
        sop = _load(s3, "run-bucket", definition["outputs"]["files"] + "sop-bom/sop.json")
        assert sop["title"] == "Widget X — teardown SOP" and sop["steps"][0]["bom_refs"] == ["widget-x-001"]

    def test_filename_order_sorts_naturally_and_selection_order_is_kept(self, tmp_path, monkeypatch, vsb_task_token):
        inputs = [
            {"bucket": "asset-bucket", "key": "db-1/asset-1/GX010010.MP4", "versionId": "", "relativePath": "/GX010010.MP4"},
            {"bucket": "asset-bucket", "key": "db-1/asset-1/GX010002.MP4", "versionId": "", "relativePath": "/GX010002.MP4"},
        ]
        durations = {"0_GX010002.MP4": 10.0, "1_GX010010.MP4": 10.0, "0_GX010010.MP4": 10.0, "1_GX010002.MP4": 10.0,
                     "0.flac": 75.0, "1.flac": 80.0, "combined.flac": 155.0}
        factory = lambda _durations: fake_run_factory(durations)  # noqa: E731

        definition = make_definition(inputFiles=inputs, config={"videoOrder": "filename"})
        status, s3, _, _, _ = _run(tmp_path / "a", monkeypatch, definition=definition, run_factory=factory)
        assert status == "SUCCEEDED"
        timeline = _load(s3, "run-bucket", definition["outputs"]["files"] + "sop-bom/video-timeline.json")
        assert timeline["video_keys"] == ["/GX010002.MP4", "/GX010010.MP4"]

        definition = make_definition(inputFiles=inputs, config={"videoOrder": "selection"})
        status, s3, _, _, _ = _run(tmp_path / "b", monkeypatch, definition=definition, run_factory=factory)
        timeline = _load(s3, "run-bucket", definition["outputs"]["files"] + "sop-bom/video-timeline.json")
        assert timeline["video_keys"] == ["/GX010010.MP4", "/GX010002.MP4"]


class TestRunTranscriptMode:
    def test_transcript_mode_writes_only_the_transcript_set_with_zero_bedrock_calls(self, tmp_path, monkeypatch, caplog, vsb_task_token):
        definition = make_definition(config={"mode": "transcript"})
        with caplog.at_level(logging.INFO):
            status, s3, _, bedrock, sfn = _run(tmp_path, monkeypatch, definition=definition)
        assert status == "SUCCEEDED"
        assert _deliverables(s3, definition) == TRANSCRIPT_FILES
        assert bedrock.calls == []
        summary = _load(s3, "run-bucket", definition["outputs"]["results"] + "summary.json")
        assert summary["mode"] == "transcript" and summary["bedrock"] == {"calls": 0, "inputTokens": 0, "outputTokens": 0}
        assert not any(record.getMessage().startswith("BEDROCK_CALL ") for record in caplog.records)
        metadata = _load(s3, "run-bucket", definition["outputs"]["metadata"] + "asset.metadata.json")
        assert {entry["metadataKey"] for entry in metadata["metadata"]} == {
            "sopBom_latestExecutionId", "sopBom_mode", "sopBom_videoCount", "sopBom_totalDurationSeconds", "sopBom_language", "sopBom_transcriptPath",
        }
        assert json.loads(sfn.successes[0]["output"])["fileCount"] == 6


class TestSubtitlesAlwaysOnSuccess:
    """Subtitles are not conditional on detected speech: a speechless input is rejected,
    so every SUCCEEDED run carries transcript.vtt and transcript.srt, and a COMPLETED Transcribe job that
    wrote no subtitle files is a Transcribe failure rather than a variant of success."""

    @pytest.mark.parametrize("mode", ["full", "transcript"])
    def test_every_successful_run_carries_both_subtitle_files(self, tmp_path, monkeypatch, vsb_task_token, mode):
        definition = make_definition(config={"mode": mode})
        status, s3, _, _, _ = _run(tmp_path, monkeypatch, definition=definition)
        assert status == "SUCCEEDED"
        assert SUBTITLE_FILES <= _deliverables(s3, definition)
        summary = _load(s3, "run-bucket", definition["outputs"]["results"] + "summary.json")
        assert summary["limits"]["observed"]["subtitles"] == ["srt", "vtt"]

    def test_a_completed_job_with_speech_but_no_subtitle_uris_is_a_transcribe_failure(self, tmp_path, monkeypatch, vsb_task_token):
        status, s3, _, bedrock, sfn = _run(tmp_path, monkeypatch, transcript=SPEECH, subtitles=False)
        assert status == "FAILED"
        assert len(sfn.failures) == 1 and sfn.successes == []
        failure = sfn.failures[0]
        assert failure["error"] == "VideoSopBomTranscribeFailed"
        assert "without the subtitle file(s) srt, vtt" in failure["cause"]
        assert bedrock.calls == [] and s3.keys("run-bucket") == []


class TestRunRejections:
    def _failure(self, sfn):
        assert len(sfn.failures) == 1 and sfn.successes == []
        return sfn.failures[0]

    def test_no_speech_is_an_input_rejection_naming_the_files_and_ingests_nothing(self, tmp_path, monkeypatch, vsb_task_token):
        """The silent fixture is rejected on the task token; no deliverable, no metadata,
        no results document and no Amazon Bedrock call — there is no empty-deliverable success."""
        definition = make_definition()
        status, s3, transcribe, bedrock, sfn = _run(tmp_path, monkeypatch, definition=definition, transcript=EMPTY_TRANSCRIPT, subtitles=False)
        assert status == "FAILED"
        failure = self._failure(sfn)
        assert failure["error"] == "VideoSopBomInputRejected"
        assert failure["cause"] == "no speech detected in part1.mp4, part2.MP4; a narrated video is required."
        assert not failure["cause"].startswith("VideoSopBom")
        assert s3.keys("run-bucket") == [], "a FAILED run ingests nothing: no files, no metadata, no results"
        assert bedrock.calls == []
        assert len(transcribe.start_calls) == 1, "the rejection is decided after transcription, from the transcript"
        keys = s3.keys("aux-bucket")
        assert any("/transcribe/" in key for key in keys), "the transcript stays in the aux prefix for diagnosis"
        assert not any("/audio/" in key for key in keys)

    def test_duration_cap_is_a_limit_rejection_and_nothing_is_ingested(self, tmp_path, monkeypatch, vsb_task_token):
        """The containers probe as 10 s each (20 s total, under the 1-minute cap); only the extracted
        tracks (75 s + 80 s) exceed it — so this rejection proves the audio tracks were the ones summed."""
        definition = make_definition(limits={"maxTotalDurationMinutes": 1})
        status, s3, transcribe, _, sfn = _run(tmp_path, monkeypatch, definition=definition)
        assert status == "FAILED"
        failure = self._failure(sfn)
        assert failure["error"] == "VideoSopBomLimitExceeded"
        assert failure["cause"] == "total video duration 0h02m exceeds this deployment's limit of 0h01m (1 minutes)."
        assert s3.keys("run-bucket") == [] and transcribe.start_calls == []

    def test_a_disk_budget_rejection_happens_before_any_download(self, tmp_path, monkeypatch, vsb_task_token):
        import shutil

        from video_sop_bom_pipeline import preflight

        gib = 2**30
        monkeypatch.setattr(preflight.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(2 * gib, gib, gib))
        status, s3, transcribe, _, sfn = _run(tmp_path, monkeypatch)
        assert status == "FAILED"
        failure = self._failure(sfn)
        assert failure["error"] == "VideoSopBomLimitExceeded"
        assert "VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB" in failure["cause"]
        assert s3.downloads == [] and transcribe.start_calls == [] and s3.keys("run-bucket") == []

    def test_no_audio_stream_is_an_input_rejection(self, tmp_path, monkeypatch, vsb_task_token):
        status, _, _, _, sfn = _run(tmp_path, monkeypatch, run_factory=lambda durations: fake_run_factory(durations, has_audio=False))
        assert status == "FAILED"
        failure = self._failure(sfn)
        assert failure["error"] == "VideoSopBomInputRejected" and failure["cause"] == "part1.mp4 has no audio stream; a narrated video is required."

    def test_a_failed_preflight_downloads_nothing(self, tmp_path, monkeypatch, vsb_task_token):
        responder = lambda request, n: client_error("AccessDeniedException", "no model access", "Converse")  # noqa: E731
        from video_sop_bom_pipeline import media, pipeline

        definition = make_definition()
        s3 = FakeS3()
        for entry in definition["inputFiles"]:
            s3.put_bytes(entry["bucket"], entry["key"], b"v" * 1000)
        sfn = FakeSfn()
        clients = make_clients(s3=s3, bedrock=FakeBedrock(responder), sfn=sfn)
        monkeypatch.setattr(media, "_run", fake_run_factory())
        assert pipeline.run(definition, media, clients, work_dir=str(tmp_path / "work"), sleep=lambda s: None) == "FAILED"
        assert self._failure(sfn)["error"] == "VideoSopBomConnectivityError"
        assert s3.downloads == []

    def test_transcribe_failure_drops_the_aux_audio_and_ingests_nothing(self, tmp_path, monkeypatch, vsb_task_token):
        """A FAILED job seeds no transcribe/ object, so the surviving-prefix assertion lives in the next test."""
        status, s3, _, _, sfn = _run(tmp_path, monkeypatch, job_states=lambda request: [failed_job(request, "Invalid file size: file size too large")])
        assert status == "FAILED"
        assert self._failure(sfn)["error"] == "VideoSopBomTranscribeFailed"
        assert not any("/audio/" in key for key in s3.keys("aux-bucket"))
        assert s3.keys("run-bucket") == []

    def test_an_unexpected_exception_is_a_readable_pipeline_error_naming_the_stage(self, tmp_path, monkeypatch, vsb_task_token):
        def exploding(argv, timeout):
            if argv[0] == "ffprobe":
                raise RuntimeError("disk on fire")
            return fake_run_factory()(argv, timeout)

        status, _, _, _, sfn = _run(tmp_path, monkeypatch, run_factory=lambda durations: exploding)
        assert status == "FAILED"
        failure = self._failure(sfn)
        assert failure["error"] == "VideoSopBomPipelineError"
        assert failure["cause"].startswith("unexpected RuntimeError at stage 3 (probe): disk on fire")

    def test_a_model_output_failure_at_finalize_keeps_the_transcribe_and_analysis_prefixes(self, tmp_path, monkeypatch, vsb_task_token):
        """Window and vision succeed (so analysis/ holds their artefacts); the finalize call is filtered."""
        status, s3, _, bedrock, sfn = _run(tmp_path, monkeypatch, responder=_bedrock_script(finalize=bare_response("content_filtered")))
        assert status == "FAILED"
        assert self._failure(sfn)["error"] == "VideoSopBomModelOutputInvalid"
        assert len(bedrock.calls) == 3, "one window, one vision batch, the failed finalize"
        keys = s3.keys("aux-bucket")
        assert any("/transcribe/" in key for key in keys), "diagnostics stay on a handled failure"
        assert any(key.endswith("/analysis/window-0.json") for key in keys)
        assert any(key.endswith("/analysis/merged.json") for key in keys)
        assert any(key.endswith("/analysis/vision.json") for key in keys)
        assert not any("/audio/" in key for key in keys) and s3.keys("run-bucket") == []


class TestMain:
    def test_exit_codes_follow_the_run_status(self, tmp_path, monkeypatch, vsb_task_token):
        from video_sop_bom_pipeline import __main__ as entry

        path = tmp_path / "definition.json"
        path.write_text(json.dumps(make_definition()), encoding="utf-8")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setattr(entry, "build_clients", lambda region: make_clients())
        monkeypatch.setattr(entry, "install_sigterm_handler", lambda state: None)
        monkeypatch.setattr(entry, "run", lambda definition, media, clients, state=None: "SUCCEEDED")
        assert entry.main(["--definition-file", str(path)]) == 0
        monkeypatch.setattr(entry, "run", lambda definition, media, clients, state=None: "FAILED")
        assert entry.main(["--definition-file", str(path)]) == 1

    def test_argument_errors_exit_2_and_a_bad_definition_is_reported_on_the_token(self, tmp_path, monkeypatch, vsb_task_token):
        from video_sop_bom_pipeline import __main__ as entry

        with pytest.raises(SystemExit) as raised:
            entry.main([])
        assert raised.value.code == 2
        sfn = FakeSfn()
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setattr(entry, "build_clients", lambda region: make_clients(sfn=sfn))
        monkeypatch.setattr(entry, "install_sigterm_handler", lambda state: None)
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert entry.main(["--definition-file", str(bad)]) == 1
        assert sfn.failures[0]["error"] == "VideoSopBomPipelineError" and "not valid JSON" in sfn.failures[0]["cause"]
