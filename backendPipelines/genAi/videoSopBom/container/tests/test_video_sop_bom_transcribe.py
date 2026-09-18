# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 6: Amazon Transcribe start/poll/download/delete through the injected clients.

Run from the container directory:  python -m pytest tests/test_video_sop_bom_transcribe.py -q

Outputs are read from the job description (TranscriptFileUri, SubtitleFileUris), never computed:
Transcribe writes no subtitle file for speechless audio, so a computed key would 404 on exactly the
silent-clip run. DeleteTranscriptionJob is called only on the terminal job.
"""

import json
import os
import re
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import (  # noqa: E402
    EMPTY_TRANSCRIPT,
    FakeS3,
    FakeTranscribe,
    client_error,
    completed_job,
    failed_job,
    in_progress_job,
    make_clients,
    make_definition,
    make_transcript,
)

AUDIO_URI = "s3://aux-bucket/pipelines/genai-video-sop-bom/exec-0001/audio/combined.flac"
SPEECH = make_transcript([(0.5, 0.9, "Remove"), (1.0, 1.5, "the"), (1.6, 2.0, "cover.")])


def _seeding_states(s3, transcript, subtitles=True, leading=()):
    """Job states for FakeTranscribe that also place the output objects in FakeS3 at the OutputKey."""

    def states(request):
        bucket, key = request["OutputBucketName"], request["OutputKey"]
        s3.put_bytes(bucket, key, json.dumps(transcript).encode("utf-8"))
        if subtitles:
            s3.put_bytes(bucket, key[:-len(".json")] + ".vtt", b"WEBVTT\n\n1\n00:00:00.500 --> 00:00:02.000\nRemove the cover.\n")
            s3.put_bytes(bucket, key[:-len(".json")] + ".srt", b"1\n00:00:00,500 --> 00:00:02,000\nRemove the cover.\n")
        return list(leading) + [completed_job(request, subtitles=subtitles)]

    return states


def _run(definition=None, transcript=SPEECH, subtitles=True, leading=(), start_errors=None, rng=None, state=None, tmp_path=None):
    from video_sop_bom_pipeline import transcribe

    definition = definition or make_definition()
    s3 = FakeS3()
    s3.put_bytes("aux-bucket", definition["auxTempPrefix"] + "audio/0.flac", b"fLaC")
    s3.put_bytes("aux-bucket", definition["auxTempPrefix"] + "audio/combined.flac", b"fLaC")
    fake = FakeTranscribe(job_states=_seeding_states(s3, transcript, subtitles, leading), start_errors=start_errors)
    sleeps = []
    result = transcribe.run_transcription(
        make_clients(s3=s3, transcribe=fake), definition, AUDIO_URI, str(tmp_path),
        state=state, sleep=sleeps.append, rng=rng or (lambda low, high: 45.0), clock=lambda: 0.0,
    )
    return result, fake, s3, sleeps


class TestStartRequest:
    def test_start_kwargs_follow_the_d7_contract(self, tmp_path):
        result, fake, _, _ = _run(tmp_path=tmp_path)
        request = fake.start_calls[0]
        name = request["TranscriptionJobName"]
        assert re.fullmatch(r"vams-video-sop-bom-pexid1234567-0000-4000-8000-000000000000-[0-9a-f]{8}", name)
        assert request["Media"] == {"MediaFileUri": AUDIO_URI}
        assert request["MediaFormat"] == "flac"
        assert request["LanguageCode"] == "en-US" and "IdentifyLanguage" not in request
        assert request["OutputBucketName"] == "aux-bucket"
        assert request["OutputKey"] == f"pipelines/genai-video-sop-bom/exec-0001/transcribe/{name}.json"
        assert request["OutputEncryptionKMSKeyId"] == "arn:aws-fake:kms:us-east-1:123456789012:key/test-key"
        assert request["Subtitles"] == {"Formats": ["vtt", "srt"], "OutputStartIndex": 1}
        assert result.job_name == name

    def test_auto_language_uses_identify_language(self, tmp_path):
        _, fake, _, _ = _run(make_definition(config={"languageCode": "auto"}), tmp_path=tmp_path)
        request = fake.start_calls[0]
        assert request["IdentifyLanguage"] is True and "LanguageCode" not in request

    def test_no_kms_key_means_no_encryption_parameter(self, tmp_path):
        _, fake, _, _ = _run(make_definition(kmsKeyArn=""), tmp_path=tmp_path)
        assert "OutputEncryptionKMSKeyId" not in fake.start_calls[0]


class TestPollingAndOutputs:
    def test_polls_every_30_seconds_until_terminal(self, tmp_path):
        from video_sop_bom_pipeline import transcribe

        s3 = FakeS3()

        def states(request):
            return [in_progress_job(request), in_progress_job(request)] + _seeding_states(s3, SPEECH)(request)

        fake = FakeTranscribe(job_states=states)
        sleeps = []
        transcribe.run_transcription(make_clients(s3=s3, transcribe=fake), make_definition(), AUDIO_URI, str(tmp_path), sleep=sleeps.append)
        assert sleeps == [30, 30]
        assert len(fake.get_calls) == 3

    def test_transcript_and_subtitles_are_downloaded_from_the_job_description_uris(self, tmp_path):
        result, fake, s3, _ = _run(tmp_path=tmp_path)
        assert result.transcript_json_path == os.path.join(str(tmp_path), "transcript.json")
        assert set(result.subtitle_paths) == {"vtt", "srt"}
        assert open(result.subtitle_paths["vtt"], "rb").read().startswith(b"WEBVTT")
        assert result.transcript_json["results"]["items"][0]["type"] == "pronunciation"
        assert result.language_code == "en-US"
        # Positive control for the next test: the subtitle keys were fetched by URI, not by a computed key.
        fetched = [key for (_, key, _) in s3.downloads]
        assert any(key.endswith(".vtt") for key in fetched) and any(key.endswith(".srt") for key in fetched)

    def test_absent_subtitle_uris_mean_no_subtitle_files_and_no_error(self, tmp_path):
        result, _, s3, _ = _run(transcript=EMPTY_TRANSCRIPT, subtitles=False, tmp_path=tmp_path)
        assert result.subtitle_paths == {}
        assert all(not key.endswith((".vtt", ".srt")) for (_, key, _) in s3.downloads)

    def test_audio_prefix_is_deleted_once_the_job_is_terminal_and_transcribe_prefix_kept(self, tmp_path):
        _, _, s3, _ = _run(tmp_path=tmp_path)
        keys = s3.keys("aux-bucket")
        assert not any("/audio/" in key for key in keys), keys
        assert any("/transcribe/" in key for key in keys)

    def test_the_terminal_job_is_deleted_and_the_state_no_longer_names_it(self, tmp_path):
        from video_sop_bom_pipeline.signals import RunState

        state = RunState()
        result, fake, _, _ = _run(state=state, tmp_path=tmp_path)
        assert fake.delete_calls == [result.job_name]
        assert state.transcribe_job_name is None

    def test_state_names_the_job_while_it_is_in_flight(self, tmp_path):
        """The SIGTERM handler reads `state.transcribe_job_name`; it must be set between start and terminal."""
        from video_sop_bom_pipeline import transcribe
        from video_sop_bom_pipeline.signals import RunState

        state = RunState()
        s3 = FakeS3()
        observed = []

        class Observing(FakeTranscribe):
            def get_transcription_job(self, TranscriptionJobName):
                observed.append(state.transcribe_job_name)
                return super().get_transcription_job(TranscriptionJobName)

        fake = Observing(job_states=_seeding_states(s3, SPEECH))
        transcribe.run_transcription(make_clients(s3=s3, transcribe=fake), make_definition(), AUDIO_URI, str(tmp_path), state=state, sleep=lambda s: None)
        assert observed == [fake.start_calls[0]["TranscriptionJobName"]]
        assert state.transcribe_job_name is None


class TestFailures:
    def test_failed_job_maps_the_failure_reason(self, tmp_path):
        from video_sop_bom_pipeline import transcribe
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake = FakeTranscribe(job_states=lambda request: [failed_job(request, "Invalid file size: file size too large")])
        with pytest.raises(PipelineRejection) as raised:
            transcribe.run_transcription(make_clients(transcribe=fake), make_definition(), AUDIO_URI, str(tmp_path), sleep=lambda s: None)
        assert raised.value.code == "VideoSopBomTranscribeFailed"
        assert raised.value.cause == "Amazon Transcribe job failed: Invalid file size: file size too large"
        assert fake.delete_calls == [], "a FAILED job is not deleted by the pipeline"

    def test_language_identification_failure_adds_the_language_code_hint(self, tmp_path):
        from video_sop_bom_pipeline import transcribe
        from video_sop_bom_pipeline.errors import PipelineRejection

        fake = FakeTranscribe(job_states=lambda request: [failed_job(request, "Language identification failed: not enough speech")])
        with pytest.raises(PipelineRejection) as raised:
            transcribe.run_transcription(make_clients(transcribe=fake), make_definition(), AUDIO_URI, str(tmp_path), sleep=lambda s: None)
        assert raised.value.cause.endswith("— set LANGUAGE_CODE explicitly for media with little or no speech")

    def test_limit_exceeded_is_retried_with_jittered_backoff_then_succeeds(self, tmp_path):
        errors = [client_error("LimitExceededException", "You've sent too many requests", "StartTranscriptionJob")] * 2
        result, fake, _, sleeps = _run(start_errors=errors, rng=lambda low, high: 77.0, tmp_path=tmp_path)
        assert sleeps == [77.0, 77.0]
        assert len(fake.start_calls) == 1 and result.job_name

    def test_limit_exceeded_budget_is_bounded(self, tmp_path):
        from video_sop_bom_pipeline import transcribe
        from video_sop_bom_pipeline.errors import PipelineRejection

        errors = [client_error("LimitExceededException", "too many requests", "StartTranscriptionJob")] * 25
        fake = FakeTranscribe(start_errors=errors)
        sleeps = []
        with pytest.raises(PipelineRejection) as raised:
            transcribe.run_transcription(make_clients(transcribe=fake), make_definition(), AUDIO_URI, str(tmp_path),
                                         sleep=sleeps.append, rng=lambda low, high: 30.0, clock=lambda: 0.0)
        assert raised.value.cause == transcribe.QUOTA_EXHAUSTED_CAUSE
        assert "L-58D7221C" in raised.value.cause
        assert len(sleeps) == transcribe.LIMIT_RETRY_MAX_ATTEMPTS - 1 == 19

    def test_limit_exceeded_wall_clock_is_bounded(self, tmp_path):
        """The clock ticks 0 s at the start, 0 s after the first refusal (120 s fits under 1800 s, one
        sleep), then 1700 s after the second (1700 + 120 > 1800: the quota cause, no further sleep)."""
        from video_sop_bom_pipeline import transcribe
        from video_sop_bom_pipeline.errors import PipelineRejection

        errors = [client_error("LimitExceededException", "too many requests", "StartTranscriptionJob")] * 25
        fake = FakeTranscribe(start_errors=errors)
        ticks = iter([0.0, 0.0, 1700.0, 1700.0, 1790.0, 1790.0])
        sleeps = []
        with pytest.raises(PipelineRejection) as raised:
            transcribe.run_transcription(make_clients(transcribe=fake), make_definition(), AUDIO_URI, str(tmp_path),
                                         sleep=sleeps.append, rng=lambda low, high: 120.0, clock=lambda: next(ticks))
        assert sleeps == [120.0], "exactly one backoff before the wall-clock budget tripped"
        assert raised.value.code == "VideoSopBomTranscribeFailed"
        assert raised.value.cause == transcribe.QUOTA_EXHAUSTED_CAUSE
        assert fake.start_calls == [], "no job was ever accepted"

    def test_limit_exceeded_naming_file_length_fails_fast(self, tmp_path):
        from video_sop_bom_pipeline import transcribe
        from video_sop_bom_pipeline.errors import PipelineRejection

        errors = [client_error("LimitExceededException", "Your input file is too long", "StartTranscriptionJob")]
        sleeps = []
        with pytest.raises(PipelineRejection) as raised:
            transcribe.run_transcription(make_clients(transcribe=FakeTranscribe(start_errors=errors)), make_definition(), AUDIO_URI, str(tmp_path), sleep=sleeps.append)
        assert sleeps == []
        assert raised.value.cause == "Amazon Transcribe rejected the audio: Your input file is too long"

    def test_other_start_errors_are_readable(self, tmp_path):
        from video_sop_bom_pipeline import transcribe
        from video_sop_bom_pipeline.errors import PipelineRejection

        errors = [client_error("BadRequestException", "Invalid OutputKey", "StartTranscriptionJob")]
        with pytest.raises(PipelineRejection) as raised:
            transcribe.run_transcription(make_clients(transcribe=FakeTranscribe(start_errors=errors)), make_definition(), AUDIO_URI, str(tmp_path), sleep=lambda s: None)
        assert raised.value.code == "VideoSopBomTranscribeFailed"
        assert "BadRequestException" in raised.value.cause and "Invalid OutputKey" in raised.value.cause


class TestHelpers:
    @pytest.mark.parametrize("uri", [
        "s3://aux-bucket/pipelines/x/transcribe/job.json",
        "https://s3.us-east-1.amazonaws.com/aux-bucket/pipelines/x/transcribe/job.json",
        "https://aux-bucket.s3.us-east-1.amazonaws.com/pipelines/x/transcribe/job.json",
        "https://aux-bucket.s3.amazonaws.com/pipelines/x/transcribe/job.json",
    ])
    def test_parse_transcribe_uri_handles_every_documented_shape(self, uri):
        from video_sop_bom_pipeline.transcribe import parse_transcribe_uri

        assert parse_transcribe_uri(uri) == ("aux-bucket", "pipelines/x/transcribe/job.json")

    def test_parse_transcribe_uri_rejects_garbage(self):
        from video_sop_bom_pipeline.transcribe import parse_transcribe_uri

        with pytest.raises(ValueError):
            parse_transcribe_uri("https://s3.us-east-1.amazonaws.com/")

    def test_has_speech(self):
        from video_sop_bom_pipeline.transcribe import has_speech

        assert has_speech(SPEECH) is True
        assert has_speech(EMPTY_TRANSCRIPT) is False
        assert has_speech({"results": {"items": [{"type": "punctuation", "alternatives": [{"content": "."}]}]}}) is False

    def test_delete_prefix_removes_only_that_prefix(self):
        from video_sop_bom_pipeline.transcribe import delete_prefix

        s3 = FakeS3()
        s3.put_bytes("aux-bucket", "p/exec/audio/0.flac", b"a")
        s3.put_bytes("aux-bucket", "p/exec/audio/combined.flac", b"b")
        s3.put_bytes("aux-bucket", "p/exec/transcribe/job.json", b"c")
        assert delete_prefix(s3, "aux-bucket", "p/exec/audio/") == 2
        assert s3.keys("aux-bucket") == ["p/exec/transcribe/job.json"]
        assert delete_prefix(s3, "aux-bucket", "p/exec/audio/") == 0
