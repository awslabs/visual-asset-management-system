# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Make the `video_sop_bom_pipeline` package importable in the shared interpreter.

The container directory is the package root (`ENTRYPOINT ["python", "-m", "video_sop_bom_pipeline"]`),
so it goes on `sys.path` and the package is imported by name. The contracts under test (vocabulary,
JSON Schemas, prompt files, the vendored path helper) need only `jsonschema`, which the repository's
interpreter already carries. Nothing here reaches AWS. The container's runtime tests append their fake
clients and media seams below this block; the path setup and the marker registration stay as they are.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(_HERE)
if _CONTAINER not in sys.path:
    sys.path.insert(0, _CONTAINER)


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")


# --- Container runtime fakes (WP03) -------------------------------------------------------------------
# The container reaches AWS only through the `Clients` dataclass it is handed and reaches ffmpeg/ffprobe
# only through `media._run`, so every runtime test substitutes plain objects for those two seams.
# Stubber is deliberately not used: it is in-order and not thread-safe, and the fakes here need to answer
# the same call several times in whichever order the stage sequence makes them.

import io  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402


def client_error(code, message="", operation="Op"):
    """A botocore ClientError with the given error code, as the real SDK would raise it."""
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


class FakeS3:
    """In-memory S3 keyed by (bucket, key) -> bytes; records uploads, deletes and head_bucket calls."""

    def __init__(self):
        self.objects = {}
        self.uploads = []
        self.downloads = []
        self.deleted = []
        self.head_bucket_calls = []
        self.fail_upload_keys = set()

    def put_bytes(self, bucket, key, data):
        self.objects[(bucket, key)] = data

    def get_object(self, Bucket, Key, **kwargs):
        if (Bucket, Key) not in self.objects:
            raise client_error("NoSuchKey", f"s3://{Bucket}/{Key}", "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key, **kwargs):
        if (Bucket, Key) not in self.objects:
            raise client_error("404", "Not Found", "HeadObject")
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def head_bucket(self, Bucket):
        self.head_bucket_calls.append(Bucket)
        return {}

    def download_file(self, Bucket, Key, Filename, ExtraArgs=None):
        if (Bucket, Key) not in self.objects:
            raise client_error("404", "Not Found", "HeadObject")
        self.downloads.append((Bucket, Key, dict(ExtraArgs or {})))
        os.makedirs(os.path.dirname(Filename), exist_ok=True)
        with open(Filename, "wb") as handle:
            handle.write(self.objects[(Bucket, Key)])

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        if Key in self.fail_upload_keys:
            raise client_error("AccessDenied", "Access Denied", "PutObject")
        with open(Filename, "rb") as handle:
            self.objects[(Bucket, Key)] = handle.read()
        self.uploads.append((Bucket, Key))

    def put_object(self, Bucket, Key, Body, **kwargs):
        data = Body if isinstance(Body, bytes) else str(Body).encode("utf-8")
        self.objects[(Bucket, Key)] = data
        self.uploads.append((Bucket, Key))
        return {}

    def list_objects_v2(self, Bucket, Prefix="", **kwargs):
        keys = sorted(k for (b, k) in self.objects if b == Bucket and k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False, "KeyCount": len(keys)}

    def delete_objects(self, Bucket, Delete):
        removed = []
        for entry in Delete["Objects"]:
            if (Bucket, entry["Key"]) in self.objects:
                del self.objects[(Bucket, entry["Key"])]
                removed.append(entry["Key"])
        self.deleted.extend(removed)
        return {"Deleted": [{"Key": k} for k in removed]}

    def keys(self, bucket):
        return sorted(k for (b, k) in self.objects if b == bucket)


PREFLIGHT_JOB_NAME = "vams-video-sop-bom-preflight"


class FakeTranscribe:
    """Scripted Transcribe: `job_states(request) -> list[dict]` supplies the successive GetTranscriptionJob bodies."""

    def __init__(self, job_states=None, start_errors=None, preflight_error=None):
        self.start_calls = []
        self.get_calls = []
        self.delete_calls = []
        self.start_errors = list(start_errors or [])
        self.preflight_error = preflight_error or client_error(
            "BadRequestException", "The requested job couldn't be found.", "GetTranscriptionJob"
        )
        self.job_states = job_states or (lambda request: [completed_job(request)])
        self.jobs = {}

    def start_transcription_job(self, **request):
        if self.start_errors:
            raise self.start_errors.pop(0)
        self.start_calls.append(request)
        name = request["TranscriptionJobName"]
        self.jobs[name] = list(self.job_states(request))
        return {"TranscriptionJob": {"TranscriptionJobName": name, "TranscriptionJobStatus": "IN_PROGRESS"}}

    def get_transcription_job(self, TranscriptionJobName):
        self.get_calls.append(TranscriptionJobName)
        if TranscriptionJobName == PREFLIGHT_JOB_NAME:
            raise self.preflight_error
        states = self.jobs[TranscriptionJobName]
        job = states.pop(0) if len(states) > 1 else states[0]
        return {"TranscriptionJob": job}

    def delete_transcription_job(self, TranscriptionJobName):
        self.delete_calls.append(TranscriptionJobName)
        return {}


def completed_job(request, subtitles=True, language_code="en-US"):
    """A COMPLETED job description whose URIs point at the aux bucket in the path-style https form."""
    name = request["TranscriptionJobName"]
    bucket = request["OutputBucketName"]
    key = request["OutputKey"]
    base = f"https://s3.us-east-1.amazonaws.com/{bucket}/{key}"
    job = {
        "TranscriptionJobName": name,
        "TranscriptionJobStatus": "COMPLETED",
        "LanguageCode": language_code,
        "Transcript": {"TranscriptFileUri": base},
    }
    if subtitles:
        stem = base[: -len(".json")]
        job["Subtitles"] = {
            "Formats": ["vtt", "srt"],
            "OutputStartIndex": 1,
            "SubtitleFileUris": [stem + ".vtt", stem + ".srt"],
        }
    return job


def failed_job(request, reason):
    return {
        "TranscriptionJobName": request["TranscriptionJobName"],
        "TranscriptionJobStatus": "FAILED",
        "FailureReason": reason,
    }


def in_progress_job(request):
    return {"TranscriptionJobName": request["TranscriptionJobName"], "TranscriptionJobStatus": "IN_PROGRESS"}


class FakeBedrock:
    """`responder(request, call_number) -> response dict | Exception` decides each Converse call."""

    def __init__(self, responder):
        self.calls = []
        self.responder = responder

    def converse(self, **request):
        self.calls.append(request)
        result = self.responder(request, len(self.calls))
        if isinstance(result, Exception):
            raise result
        return result


def tool_use_response(name, payload, in_tokens=100, out_tokens=50, stop_reason="tool_use"):
    """A Converse response carrying one toolUse block; `payload` is the tool input as a dict."""
    return {
        "stopReason": stop_reason,
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "tu-1", "name": name, "input": payload}}],
            }
        },
        "usage": {"inputTokens": in_tokens, "outputTokens": out_tokens, "totalTokens": in_tokens + out_tokens},
    }


def text_response(text, stop_reason="end_turn", in_tokens=10, out_tokens=5):
    """A Converse response with text only (no toolUse block)."""
    return {
        "stopReason": stop_reason,
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "usage": {"inputTokens": in_tokens, "outputTokens": out_tokens, "totalTokens": in_tokens + out_tokens},
    }


def bare_response(stop_reason, in_tokens=10, out_tokens=0):
    """A Converse response with no output message at all (the shape a filtered/stopped call can have)."""
    return {
        "stopReason": stop_reason,
        "usage": {"inputTokens": in_tokens, "outputTokens": out_tokens, "totalTokens": in_tokens + out_tokens},
    }


class FakeSfn:
    def __init__(self, fail_with=None):
        self.successes = []
        self.failures = []
        self.fail_with = fail_with

    def send_task_success(self, **kwargs):
        if self.fail_with is not None:
            raise self.fail_with
        self.successes.append(kwargs)
        return {}

    def send_task_failure(self, **kwargs):
        if self.fail_with is not None:
            raise self.fail_with
        self.failures.append(kwargs)
        return {}


def make_clients(s3=None, transcribe=None, bedrock=None, sfn=None, preflight_transcribe=None,
                 preflight_bedrock=None, sfn_signal=None):
    """A Clients dataclass built from fakes; unspecified members default to fresh fakes."""
    from video_sop_bom_pipeline.clients import Clients

    s3 = s3 if s3 is not None else FakeS3()
    transcribe = transcribe if transcribe is not None else FakeTranscribe()
    bedrock = bedrock if bedrock is not None else FakeBedrock(lambda request, n: text_response("unused"))
    sfn = sfn if sfn is not None else FakeSfn()
    return Clients(
        s3=s3,
        transcribe=transcribe,
        bedrock=bedrock,
        sfn=sfn,
        preflight_transcribe=preflight_transcribe if preflight_transcribe is not None else transcribe,
        preflight_bedrock=preflight_bedrock if preflight_bedrock is not None else bedrock,
        sfn_signal=sfn_signal if sfn_signal is not None else sfn,
    )


RUN_PREFIX = "pipelines/genai-video-sop-bom/VideoSopBom_pexid1234567_20260909_010203_abc123/output/exec-0001/"


def make_definition(**overrides):
    """The registry definition document with two inputs; `config`/`limits` overrides merge, others replace."""
    definition = {
        "schemaVersion": 1,
        "batchJobName": "VideoSopBom_pexid1234567_20260909_010203_abc123",
        "pipelineExecutionId": "pexid1234567-0000-4000-8000-000000000000",
        "executionId": "exec-0001",
        "assetId": "asset-1",
        "databaseId": "db-1",
        "assetName": "Cognex In-Sight 2800",
        "inputFiles": [
            {"bucket": "asset-bucket", "key": "db-1/asset-1/part1.mp4", "versionId": "v1", "relativePath": "/part1.mp4"},
            {"bucket": "asset-bucket", "key": "db-1/asset-1/part2.MP4", "versionId": "", "relativePath": "/part2.MP4"},
        ],
        "outputs": {
            "bucket": "run-bucket",
            "files": RUN_PREFIX + "files/",
            "previews": RUN_PREFIX + "previews/",
            "metadata": RUN_PREFIX + "metadata/",
            "results": RUN_PREFIX + "results/",
        },
        "outputTarget": {"assetId": "asset-1", "databaseId": "db-1", "fileBaseExecutionPathExtension": "/exec-0001/"},
        "auxBucket": "aux-bucket",
        "auxTempPrefix": "pipelines/genai-video-sop-bom/exec-0001/",
        "kmsKeyArn": "arn:aws-fake:kms:us-east-1:123456789012:key/test-key",
        "bedrockModelId": "global.anthropic.claude-sonnet-5",
        "limits": {
            "maxVideoFiles": 4,
            "maxVideoFileSizeMb": 4096,
            "maxTotalInputSizeMb": 16384,
            "maxTotalDurationMinutes": 240,
            "maxKeyFramesCeiling": 200,
        },
        "config": {
            "mode": "full",
            "languageCode": "en-US",
            "videoOrder": "selection",
            "productName": "",
            "contributors": "",
            "maxKeyFrames": 60,
            "partLevelBase": "0",
            "generateLabSummary": True,
            "additionalInstructions": "",
        },
    }
    for key, value in overrides.items():
        if key in ("config", "limits") and isinstance(value, dict):
            definition[key].update(value)
        else:
            definition[key] = value
    return definition


def make_transcript(words, language_code="en-US"):
    """Transcribe batch output: `words` is a list of (start_s, end_s, text); a '.' closes each sentence
    whose word ends with '.'. The `pronunciation` items carry start_time/end_time as strings."""
    items = []
    for index, (start, end, text) in enumerate(words):
        content = text.rstrip(".")
        items.append({
            "id": len(items),
            "start_time": f"{start:.2f}",
            "end_time": f"{end:.2f}",
            "alternatives": [{"confidence": "0.99", "content": content}],
            "type": "pronunciation",
        })
        if text.endswith("."):
            items.append({"id": len(items), "alternatives": [{"confidence": "0.0", "content": "."}], "type": "punctuation"})
    transcript = " ".join(w[2] for w in words)
    return {
        "jobName": "vams-video-sop-bom-test",
        "accountId": "123456789012",
        "status": "COMPLETED",
        "results": {
            "language_code": language_code,
            "transcripts": [{"transcript": transcript}],
            "items": items,
        },
    }


EMPTY_TRANSCRIPT = make_transcript([])


def write_png(path, width=1920, height=1080):
    from PIL import Image

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    Image.new("RGB", (width, height), (40, 90, 160)).save(path, "PNG")


def fake_run_factory(durations=None, has_audio=True, fail_basenames=(), png_size=(1920, 1080)):
    """A `media._run` stand-in. ffprobe answers from `durations` (basename -> seconds, default 60.0);
    ffmpeg writes its output file (a real PNG when the target ends in .png). Returns (rc, tail)."""
    durations = dict(durations or {})
    calls = []

    def _run(argv, timeout):
        calls.append(list(argv))
        target = argv[-1]
        base = os.path.basename(target)
        if base in fail_basenames:
            return 1, f"{argv[0]}: {base}: Invalid data found when processing input"
        if argv[0] == "ffprobe":
            duration = durations.get(base, 60.0)
            streams = []
            if not target.endswith(".flac"):
                streams.append({"index": 0, "codec_type": "video", "codec_name": "h264"})
            if has_audio:
                streams.append({"index": len(streams), "codec_type": "audio", "codec_name": "aac" if not target.endswith(".flac") else "flac"})
            return 0, json.dumps({"streams": streams, "format": {"duration": f"{duration:.6f}"}})
        if argv[0] == "ffmpeg":
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            if target.endswith(".png"):
                write_png(target, *png_size)
            else:
                with open(target, "wb") as handle:
                    handle.write(b"fLaC" + b"\x00" * 60)
            return 0, ""
        return 127, f"{argv[0]}: not found"

    _run.calls = calls
    return _run


@pytest.fixture
def vsb_fake_run():
    # Container durations deliberately differ from the extracted tracks: the duration cap must sum the
    # .flac probes (spec D5), and identical figures would let a container-duration sum pass unnoticed.
    return fake_run_factory({"part1.mp4": 10.0, "part2.MP4": 10.0, "0.flac": 75.0, "1.flac": 80.0, "combined.flac": 155.0})


@pytest.fixture
def vsb_task_token(monkeypatch):
    monkeypatch.setenv("TASK_TOKEN", "AAAAKgAAAAIAAAAAAAAAAfaketoken")
    return os.environ["TASK_TOKEN"]
