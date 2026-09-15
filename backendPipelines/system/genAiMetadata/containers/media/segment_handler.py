# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Second function of the media image: the SegmentAnalyzeTask child of the VideoSegmentMap, one time window of
a video per invocation. Three frames are read at 10 %, 50 % and 90 % of the window with the bundled ffmpeg over
a presigned URL of the input (the object is downloaded once to /tmp when the URL read fails), the configured
Converse model describes the window, the window's text is embedded through the vendored adapter, the videoTime
document is written beside the whole-file document, one vector.embedding.ready event is published, and the
description is kept as a results-prefix record. A caught Bedrock failure (a guardrail intervention included),
and a PutEvents failure, are recorded through execution.status.json and the window's .failed.json and the child
returns normally; a window without a readable frame is skipped with a .failed.json record and no execution
failure.

When a Bedrock guardrail is configured (``BEDROCK_GUARDRAIL_IDENTIFIER`` and ``BEDROCK_GUARDRAIL_VERSION``, both
or neither), every Converse call carries it and the parts of the prompt that come from the file and its metadata
travel in a ``guardContent`` block so the guardrail's input filters evaluate them. A filter that blocks is the
intervention above; a reply the sensitive-information filter only masked is a success whose description carries
the filter's type tokens, and the window's record notes ``guardrailMasked`` with the masked entity types."""

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from customLogging.logger import safeLogger
from PIL import Image

import bedrockGuardrail
from media_extractors.common import human_duration
from media_extractors.imaging import normalise_for_vision, write_png
from media_extractors.video import run_ffmpeg
from vectorsearch import embeddings
from videoSegments import SEGMENT_KIND

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md: the child reads Amazon S3,
# calls Amazon Bedrock and publishes to EventBridge for the length of one window, so a bare client would surface
# a sustained burst as a throttling error instead of smoothing it.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="systemGenAiMetadataSegmentAnalyze")

s3_client = boto3.client('s3', config=retry_config)
bedrock_runtime = boto3.client('bedrock-runtime', config=retry_config)
events_client = boto3.client('events', region_name=os.environ["AWS_REGION"], config=retry_config)

BEDROCK_ANALYSIS_MODEL_ID = os.environ["BEDROCK_ANALYSIS_MODEL_ID"]
EMBEDDING_MODEL_ID = os.environ["EMBEDDING_MODEL_ID"]
EMBEDDING_DIMENSIONS = int(os.environ["EMBEDDING_DIMENSIONS"])
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")
# The guardrail applied to every Converse call (bedrockGuardrail: both variables or neither). Without one the
# calls run without prompt-attack filters; the one warning at cold start is the operator's signal.
GUARDRAIL_CONFIG = bedrockGuardrail.guardrail_config_from_env(os.environ)
if GUARDRAIL_CONFIG is None:
    logger.warning(bedrockGuardrail.GUARDRAIL_UNCONFIGURED_WARNING)

# ffmpeg runs through this callable so a test can stand in for it.
ffmpeg_run: Callable = subprocess.run

SEGMENT_FRAMES = 3
SEGMENT_FRAME_POSITIONS = (0.1, 0.5, 0.9)
SEGMENT_PRESIGN_SECONDS = 900
SEGMENT_MAX_LONG_EDGE_PX = 1024
# Per ffmpeg call; three URL reads, one download and three local reads must fit the function's 300 s.
SEGMENT_FFMPEG_TIMEOUT_SECONDS = 30
# ffmpeg reads the video over a presigned URL whose query string is the credential (X-Amz-Signature,
# X-Amz-Security-Token), and echoes that URL in its error lines. Every URL, and every stray X-Amz-… query
# parameter, is replaced before an ffmpeg message reaches a log line or the window's results record.
URL_PLACEHOLDER = "<url>"
_URL_TEXT = re.compile(r"https?://\S+")
_SIGNED_QUERY_TEXT = re.compile(r"X-Amz-[A-Za-z-]+=\S*")
SEGMENT_JSON_KEYS = ("description", "keywords", "objects", "actions", "textSeen")
ERROR_BEDROCK_SEGMENT = "BedrockSegmentError"
# A guardrail intervention is a caught failure recorded under its own code, like the whole-file analysis.
ERROR_BEDROCK_GUARDRAIL_INTERVENED = "BedrockGuardrailIntervened"
# A PutEvents failure is recorded like a Bedrock failure, never raised: a raise would fail the whole Map.
ERROR_SEGMENT_PUBLISH = "SegmentPublishError"
# A window with no readable frame is skipped, not failed: the whole-file vector already stands.
ERROR_NO_FRAMES = "SegmentFramesUnavailable"

STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"
# The reserved results file the process-output step reads, and its cause cap (the analysisCommon literals).
EXECUTION_STATUS_RESULTS_FILENAME = "execution.status.json"
STATUS_CAUSE_MAX_CHARS = 1024
SEGMENT_RESULTS_PREFIX = "segments/"

EMBEDDING_READY_DETAIL_TYPE = "vector.embedding.ready"
EMBEDDING_DOCUMENT_PREFIX = "embedding/"
EMBEDDING_DOCUMENT_SCHEMA_VERSION = 1
SOURCE_TEXT_STORED_MAX_CHARS = 8000

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2, 4)
MAX_TOKENS = 1024
TEMPERATURE = 0.2
MAX_DESCRIPTION_CHARS = 400
MAX_KEYWORDS = 10
MAX_OBJECTS = 10
MAX_ACTIONS = 5
MAX_TEXT_SEEN_CHARS = 200
_THROTTLE_ERROR_CODES = frozenset({"ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
                                   "ModelNotReadyException", "InternalServerException"})

MODALITY_ASSET_METADATA = "asset-metadata"
MODALITY_FILE_IDENTITY = "file-identity"
MODALITY_SEGMENT_FRAMES = "segment-frames"
MODALITY_GENAI_METADATA = "genai-metadata"

# The words a user types when asking for a kind of file, per class — the pipeline's fileClassifier vocabulary,
# restated because the image cannot import lambda/ modules; the test suite pins the two equal.
FILE_CLASS_PHRASES: Dict[str, str] = {
    "image": "image (photo or picture)",
    "video": "video (footage)",
    "audio": "audio recording",
    "document": "document (PDF or office)",
    "text": "text file",
    "data": "data table (spreadsheet)",
    "tiles3d": "3D Tiles tileset",
    "mesh": "3D model (mesh)",
    "usd": "3D model (USD scene)",
    "cad": "CAD model",
    "pointcloud": "point cloud (LiDAR scan)",
    "splat": "3D Gaussian splat",
    "ifc": "BIM building model (IFC)",
    "other": "file",
}

SEGMENT_SYSTEM_PROMPT = (
    "You are a digital asset librarian describing one time window of a video from an asset management system "
    "so the video can be found by what happens in that window. You receive the asset's name, the file's type "
    "and path, the window's time range, the title, category and description the whole video already carries, "
    "and frames taken inside the window. Return ONLY a JSON object with exactly these keys: "
    '"description" (string, at most 400 characters of plain prose describing what the frames show), '
    '"keywords" (array of at most 10 lowercase strings of one to three words each), '
    '"objects" (array of at most 10 strings naming the distinct objects or entities visible), '
    '"actions" (array of at most 5 strings naming what is happening), '
    '"textSeen" (string of at most 200 characters with any legible text in the frames, or null). '
    "Do not wrap the JSON in markdown fences and do not add any other text."
)


class SegmentAnalysisFailure(Exception):
    """A Bedrock failure the window records through execution.status.json rather than raising; ``code`` is the
    execution error code the record carries."""

    def __init__(self, cause: str, code: str = ERROR_BEDROCK_SEGMENT):
        super().__init__(cause)
        self.code = code


class SegmentPublishFailure(Exception):
    """A PutEvents failure — an exception from the call or a rejected entry — the window records rather than
    raises."""


class ModelResponseError(Exception):
    """A model reply that carries no usable JSON object."""


def parse_s3_uri(uri: Optional[str]) -> Tuple[str, str]:
    if not uri or not str(uri).startswith("s3://"):
        return "", ""
    bucket, _, key = str(uri)[len("s3://"):].partition("/")
    return bucket, key


def _join(prefix: str, name: str) -> str:
    return (prefix.rstrip("/") + "/" + name.lstrip("/")) if prefix else name.lstrip("/")


def read_json(uri: str) -> dict:
    bucket, key = parse_s3_uri(uri)
    body = json.loads(s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"{uri} does not hold a JSON object")
    return body


def _read_json_or_empty(uri: Optional[str]) -> dict:
    """A JSON object the window uses as context only; missing or unreadable yields an empty object."""
    if not uri:
        return {}
    try:
        return read_json(uri)
    except Exception as exc:  # noqa: BLE001 - context is optional; the window is still analysed without it
        logger.warning(f"Context object unreadable ({uri}): {exc}")
        return {}


def write_json(bucket: str, key: str, body: dict) -> str:
    s3_client.put_object(Bucket=bucket, Key=key, Body=json.dumps(body, default=str).encode("utf-8"),
                         ContentType="application/json")
    return f"s3://{bucket}/{key}"


def asset_name_from_envelope(envelope: dict, database_id: str, asset_id: str) -> str:
    """The asset name of the grouped execution envelope for the subject asset; "" when absent."""
    for asset in (envelope or {}).get("assets") or []:
        if not isinstance(asset, dict) or asset.get("assetId") != asset_id:
            continue
        if database_id and asset.get("databaseId") not in (None, database_id):
            continue
        return str((asset.get("assetData") or {}).get("assetName") or "")
    return ""


def genai_values(metadata_file_body: dict) -> Dict[str, str]:
    """``{metadataKey: metadataValue}`` from the .metadata.json body the analysis step wrote."""
    return {row.get("metadataKey"): row.get("metadataValue")
            for row in (metadata_file_body or {}).get("metadata") or [] if row.get("metadataKey")}


def presign_input(bucket: str, key: str, version_id: str) -> str:
    params = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    return s3_client.generate_presigned_url("get_object", Params=params, ExpiresIn=SEGMENT_PRESIGN_SECONDS)


def frame_times(start_ms: int, end_ms: int) -> List[float]:
    """Seconds at 10 %, 50 % and 90 % of the window."""
    return [round((start_ms + (end_ms - start_ms) * position) / 1000.0, 3) for position in SEGMENT_FRAME_POSITIONS]


def scrub_urls(text: str) -> str:
    """``text`` with every URL, and every stray ``X-Amz-…=`` query parameter, replaced by URL_PLACEHOLDER."""
    return _SIGNED_QUERY_TEXT.sub(URL_PLACEHOLDER, _URL_TEXT.sub(URL_PLACEHOLDER, text))


def extract_frames(source: str, times: List[float], work_dir: str, run: Callable) -> Tuple[List[str], List[str]]:
    """PNG paths for the frames that decoded from ``source`` (a URL or a local path), one warning per frame that
    did not; each frame is fitted to the segment's long edge and the vision byte cap. A warning carries the tail
    of ffmpeg's stderr with its URLs scrubbed, or a fixed timeout message: the source may be a presigned URL, and
    ``str(TimeoutExpired)`` would carry the whole argv."""
    paths: List[str] = []
    warnings: List[str] = []
    for index, seconds in enumerate(times, start=1):
        raw = os.path.join(work_dir, f"segment-{index:02d}-raw.png")
        try:
            proc = run_ffmpeg([
                "-ss", f"{seconds:.3f}", "-i", source, "-an", "-sn", "-frames:v", "1", "-update", "1",
                "-vf", f"scale='min({SEGMENT_MAX_LONG_EDGE_PX},iw)':-2", "-y", raw,
            ], run=run, timeout=SEGMENT_FFMPEG_TIMEOUT_SECONDS)
            if proc.returncode != 0 or not os.path.exists(raw):
                tail = scrub_urls(proc.stderr.decode("utf-8", "replace"))[-300:].strip()
                warnings.append(f"Frame at {seconds:.1f}s failed (exit {proc.returncode}): {tail}")
                continue
            with Image.open(raw) as frame:
                png, _ = normalise_for_vision(frame.copy(), max_long_edge=SEGMENT_MAX_LONG_EDGE_PX)
            paths.append(write_png(png, work_dir, f"segment-{index:02d}.png"))
        except subprocess.TimeoutExpired:
            warnings.append(f"Frame at {seconds:.1f}s failed: ffmpeg timed out after {SEGMENT_FFMPEG_TIMEOUT_SECONDS}s")
        except OSError as exc:
            warnings.append(f"Frame at {seconds:.1f}s failed: {scrub_urls(str(exc))}")
    return paths, warnings


def frames_for_window(bucket: str, key: str, version_id: str, start_ms: int, end_ms: int, work_dir: str,
                      run: Callable) -> Tuple[List[str], List[str]]:
    """The window's frames over a presigned URL; when none decoded, the object is downloaded once and read locally."""
    times = frame_times(start_ms, end_ms)
    paths, warnings = extract_frames(presign_input(bucket, key, version_id), times, work_dir, run)
    if paths:
        return paths, warnings
    local = os.path.join(work_dir, "input" + os.path.splitext(key)[1].lower())
    s3_client.download_file(bucket, key, local, ExtraArgs={"VersionId": version_id} if version_id else None)
    warnings.append("presigned read produced no frame; the object was downloaded once and read locally")
    paths, more = extract_frames(local, times, work_dir, run)
    return paths, warnings + more


def build_user_blocks(asset_name: str, phrase: str, relative_path: str, label: str, duration_seconds: float,
                      genai: Dict[str, str], frame_count: int) -> List[dict]:
    """The user message's text blocks. The instruction, the file-type phrase, the window sentence and the frame
    note are the pipeline's own words; the asset name, the file path and the whole video's genai_* values come
    from the file and its metadata and travel in the guardContent block when a guardrail is configured."""
    plain = ["Describe this time window of a video for search indexing.", f"File type: {phrase}",
             f"Segment {label} of {human_duration(duration_seconds)}",
             f"The {frame_count} frames that follow were taken inside the window, at 10 %, 50 % and 90 % of it."]
    guarded = []
    if asset_name:
        guarded.append(f"Asset name: {asset_name}")
    guarded.append(f"File: {relative_path}")
    for key, heading in (("genai_title", "Video title"), ("genai_category", "Video category"),
                         ("genai_description", "Video description")):
        if genai.get(key):
            guarded.append(f"{heading}: {genai[key]}")
    return bedrockGuardrail.user_content_blocks("\n".join(plain), "\n".join(guarded), GUARDRAIL_CONFIG)


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        cleaned = cleaned[first_newline + 1:] if first_newline >= 0 else cleaned[3:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned


def _clean_text(value, limit: Optional[int] = None) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:limit] if limit else text


def _string_list(values, limit: int) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values if isinstance(values, list) else []:
        item = _clean_text(value)
        if not item or item.lower() in seen:
            continue
        seen.add(item.lower())
        out.append(item)
        if len(out) >= limit:
            break
    return out


def parse_segment_json(text: str) -> dict:
    """The model reply as a normalised dict: fences stripped, the outermost JSON object extracted, the
    description required, lengths bounded, lists deduplicated."""
    cleaned = _strip_fences(text or "")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ModelResponseError("no JSON object in the model reply")
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except ValueError as exc:
        raise ModelResponseError(f"model reply is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        raise ModelResponseError("model reply is not a JSON object")
    description = _clean_text(parsed.get("description"), MAX_DESCRIPTION_CHARS)
    if description is None:
        raise ModelResponseError("model reply has no description")
    return {
        "description": description,
        "keywords": [keyword.lower() for keyword in _string_list(parsed.get("keywords"), MAX_KEYWORDS)],
        "objects": _string_list(parsed.get("objects"), MAX_OBJECTS),
        "actions": _string_list(parsed.get("actions"), MAX_ACTIONS),
        "textSeen": _clean_text(parsed.get("textSeen"), MAX_TEXT_SEEN_CHARS),
    }


def analyze_window(user_blocks: List[dict], image_blocks: List[dict]) -> Tuple[dict, dict, List[str]]:
    """``(result, usage, masked_types)`` after at most MAX_ATTEMPTS Converse calls: a throttle backs off and
    retries, an unparsable reply is re-asked; every other Bedrock error, a guardrail intervention and an exhausted
    attempt budget raise SegmentAnalysisFailure. With a guardrail configured the frames travel in ``guardContent``
    blocks, like the file-derived text. ``masked_types`` names the entity types the guardrail anonymized in the
    prompt or the reply (empty when none): a masked reply is a complete answer and is parsed like any other."""
    content = list(user_blocks) + bedrockGuardrail.user_image_blocks(image_blocks, GUARDRAIL_CONFIG)
    request = {
        "modelId": BEDROCK_ANALYSIS_MODEL_ID,
        "system": [{"text": SEGMENT_SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
    }
    if GUARDRAIL_CONFIG:
        request["guardrailConfig"] = dict(GUARDRAIL_CONFIG)
    last_parse_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = bedrock_runtime.converse(**request)
        except ClientError as exc:
            code = ((exc.response or {}).get("Error") or {}).get("Code", "")
            if code in _THROTTLE_ERROR_CODES and attempt < MAX_ATTEMPTS:
                logger.warning(f"Bedrock throttled on attempt {attempt}; retrying")
                time.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
                continue
            raise SegmentAnalysisFailure(str(exc))
        if bedrockGuardrail.intervened(response):
            raise SegmentAnalysisFailure(bedrockGuardrail.guardrail_cause(response), ERROR_BEDROCK_GUARDRAIL_INTERVENED)
        masked_types = bedrockGuardrail.masked_entity_types(response)
        message = ((response.get("output") or {}).get("message") or {})
        text = "".join(block.get("text", "") for block in (message.get("content") or []))
        try:
            return parse_segment_json(text), response.get("usage") or {}, masked_types
        except ModelResponseError as exc:
            last_parse_error = exc
            logger.warning(f"Attempt {attempt}: {exc}")
    raise SegmentAnalysisFailure(f"model returned no parsable JSON after {MAX_ATTEMPTS} attempts: {last_parse_error}")


def compose_source_text(asset_name: str, phrase: str, relative_path: str, segment_sentence: str, result: dict,
                        genai: Dict[str, str]) -> Tuple[str, List[str]]:
    """``(sourceText, sourceModalities)``: the asset name; the file-type phrase, path and segment sentence; the
    window's description, keywords, objects, actions and legible text; the whole video's title and category —
    one line per part, each distinct string once, and a modality label for each part that contributed."""
    parts: List[str] = []
    modalities: List[str] = []
    seen = set()

    def add(part, modality):
        text = " ".join(str(part).split()) if part is not None else ""
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        parts.append(text)
        if modality not in modalities:
            modalities.append(modality)

    add(asset_name, MODALITY_ASSET_METADATA)
    add(phrase, MODALITY_FILE_IDENTITY)
    add(relative_path, MODALITY_FILE_IDENTITY)
    add(segment_sentence, MODALITY_FILE_IDENTITY)
    add(result.get("description"), MODALITY_SEGMENT_FRAMES)
    for key in ("keywords", "objects", "actions"):
        add(", ".join(result.get(key) or []), MODALITY_SEGMENT_FRAMES)
    add(result.get("textSeen"), MODALITY_SEGMENT_FRAMES)
    add(genai.get("genai_title"), MODALITY_GENAI_METADATA)
    add(genai.get("genai_category"), MODALITY_GENAI_METADATA)
    return "\n".join(parts), modalities


def file_version_key(relative_path: str, version_id: str) -> str:
    return f"{relative_path}#{version_id or 'null'}"


def segment_document_key(aux_prefix: str, relative_path: str, version_id: str, segment_key: str) -> str:
    digest = hashlib.sha256(f"{file_version_key(relative_path, version_id)}#{segment_key}".encode("utf-8")).hexdigest()
    return _join(aux_prefix, EMBEDDING_DOCUMENT_PREFIX + digest + ".json")


def publish(detail: dict, source: str) -> bool:
    """Publishes the window's vector.embedding.ready event; a call that raises, or that reports a failed entry,
    raises SegmentPublishFailure for the handler to record."""
    if not ORCHESTRATION_BUS_NAME or not source:
        logger.warning("Orchestration bus or event source not configured; the segment document was written but "
                       "no vector.embedding.ready event is published")
        return False
    try:
        response = events_client.put_events(Entries=[{
            "EventBusName": ORCHESTRATION_BUS_NAME,
            "Source": source,
            "DetailType": EMBEDDING_READY_DETAIL_TYPE,
            "Detail": json.dumps(detail),
        }])
    except (ClientError, BotoCoreError) as exc:
        raise SegmentPublishFailure(f"PutEvents failed: {exc}")
    if response.get("FailedEntryCount"):
        raise SegmentPublishFailure(
            f"PutEvents FailedEntryCount={response['FailedEntryCount']}: {response.get('Entries')}")
    return True


def write_failure(results_uri: str, segment: dict, status: str, error: str, cause: str, generated_at: str,
                  write_status: bool) -> None:
    """The window's .failed.json under the results prefix and, for a caught failure, the reserved
    execution.status.json the process-output step reads."""
    bucket, prefix = parse_s3_uri(results_uri)
    write_json(bucket, _join(prefix, f"{SEGMENT_RESULTS_PREFIX}{segment['segmentKey']}.failed.json"), {
        "schemaVersion": 1, "segmentKey": segment["segmentKey"], "label": segment.get("label"),
        "startMs": segment.get("startMs"), "endMs": segment.get("endMs"), "status": status, "error": error,
        "cause": cause, "generatedAt": generated_at,
    })
    if write_status:
        write_json(bucket, _join(prefix, EXECUTION_STATUS_RESULTS_FILENAME),
                   {"status": STATUS_FAILED, "error": error, "cause": cause})


def lambda_handler(event, context):
    """
    SegmentAnalyzeTask
    Analyses one video window: three frames, one Converse request, one embedding, one document and one event.
    """

    segment = event["segment"]
    state = event["state"]
    segment_key = segment["segmentKey"]
    start_ms, end_ms = int(segment["startMs"]), int(segment["endMs"])
    label = str(segment.get("label") or "")
    logger.info({"message": "Segment analyze task", "segmentKey": segment_key, "assetId": state.get("assetId"),
                 "relativePath": state.get("relativePath"), "startMs": start_ms, "endMs": end_ms})

    bucket, key = parse_s3_uri(state["inputS3AssetFilePath"])
    version_id = str(state.get("versionId") or "")
    relative_path = str(state.get("relativePath") or "/" + key)
    aux_bucket, aux_prefix = parse_s3_uri(state["inputOutputS3AssetAuxiliaryFilesPath"])
    results_uri = state["outputS3AssetResultsPath"]
    plan = read_json(state["videoSegmentPlanS3Location"])
    duration_seconds = float(plan.get("durationSeconds") or 0.0)
    segment_count = int(plan.get("count") or 0)
    asset_name = asset_name_from_envelope(_read_json_or_empty(state.get("inputMetadataS3Location")),
                                         str(state.get("databaseId") or ""), str(state.get("assetId") or ""))
    genai = genai_values(_read_json_or_empty(state.get("metadataFileS3Location")))
    file_class = str(state.get("fileClass") or "video")
    phrase = FILE_CLASS_PHRASES.get(file_class, FILE_CLASS_PHRASES["other"])
    segment_sentence = f"Segment {label} of {human_duration(duration_seconds)}"
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    work_dir = tempfile.mkdtemp(prefix="segment-")
    try:
        frames, warnings = frames_for_window(bucket, key, version_id, start_ms, end_ms, work_dir, ffmpeg_run)
        if not frames:
            cause = f"segment {segment_key}: no frame could be read: " + "; ".join(warnings)
            logger.warning(cause)
            write_failure(results_uri, segment, STATUS_SKIPPED, ERROR_NO_FRAMES, cause[:STATUS_CAUSE_MAX_CHARS],
                          generated_at, write_status=False)
            return {"segmentKey": segment_key, "status": STATUS_SKIPPED, "error": ERROR_NO_FRAMES,
                    "documentS3Location": None}
        image_blocks = []
        for path in frames:
            with open(path, "rb") as handle:
                image_blocks.append({"image": {"format": "png", "source": {"bytes": handle.read()}}})
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    for warning in warnings:
        logger.warning(warning)

    user_blocks = build_user_blocks(asset_name, phrase, relative_path, label, duration_seconds, genai, len(image_blocks))
    try:
        result, usage, masked_types = analyze_window(user_blocks, image_blocks)
        source_text, modalities = compose_source_text(asset_name, phrase, relative_path, segment_sentence, result, genai)
        prepared = embeddings.truncate_for_model(source_text, EMBEDDING_MODEL_ID)
        vector = embeddings.round_vector(embeddings.embed_text(
            prepared, model_id=EMBEDDING_MODEL_ID, dimensions=EMBEDDING_DIMENSIONS, purpose="index",
            client=bedrock_runtime))
    except (SegmentAnalysisFailure, ClientError, embeddings.EmbeddingModelError) as exc:
        error = getattr(exc, "code", None) if isinstance(exc, SegmentAnalysisFailure) else ERROR_BEDROCK_SEGMENT
        cause = f"segment {segment_key}: {exc}"[:STATUS_CAUSE_MAX_CHARS]
        logger.error(f"Segment analysis failed ({error}): {cause}")
        write_failure(results_uri, segment, STATUS_FAILED, error, cause, generated_at, write_status=True)
        return {"segmentKey": segment_key, "status": STATUS_FAILED, "error": error, "documentS3Location": None}

    document_key = segment_document_key(aux_prefix, relative_path, version_id, segment_key)
    document = {
        "schemaVersion": EMBEDDING_DOCUMENT_SCHEMA_VERSION,
        "databaseId": state.get("databaseId", "") or "",
        "assetId": state.get("assetId", "") or "",
        "filePath": relative_path,
        "versionId": version_id,
        "contentEtag": state.get("etag", "") or "",
        "bucketId": state.get("bucketId", "") or "",
        "fileClass": file_class,
        # The vector table's form: un-dotted lower-case, "none" when the key has no extension.
        "fileExt": str(state.get("fileExt", "") or "").lstrip(".").lower() or "none",
        "fileSize": int(state.get("fileSize", 0) or 0),
        "contentType": state.get("contentType", "") or "",
        "embeddingModelId": EMBEDDING_MODEL_ID,
        "embeddingDimensions": EMBEDDING_DIMENSIONS,
        "analysisModelId": BEDROCK_ANALYSIS_MODEL_ID,
        "embedding": vector,
        "sourceText": source_text[:SOURCE_TEXT_STORED_MAX_CHARS],
        "sourceModalities": modalities,
        "pipelineExecutionId": state.get("pipelineExecutionId", "") or "",
        "workflowExecutionId": state.get("workflowExecutionId", "") or "",
        "generatedAt": generated_at,
        "segmentKey": segment_key,
        "segmentKind": SEGMENT_KIND,
        "segmentLabel": label,
        "segmentStartMs": start_ms,
        "segmentEndMs": end_ms,
        "segmentCount": segment_count,
    }
    document_uri = write_json(aux_bucket, document_key, document)
    detail = {field: value for field, value in document.items() if field not in ("embedding", "sourceText")}
    detail["documentS3Location"] = document_uri
    try:
        publish(detail, str(state.get("orchestrationEventPrefix") or ""))
    except SegmentPublishFailure as exc:
        cause = f"segment {segment_key}: {exc}"[:STATUS_CAUSE_MAX_CHARS]
        logger.error(f"Segment event not published ({ERROR_SEGMENT_PUBLISH}): {cause}")
        write_failure(results_uri, segment, STATUS_FAILED, ERROR_SEGMENT_PUBLISH, cause, generated_at, write_status=True)
        return {"segmentKey": segment_key, "status": STATUS_FAILED, "error": ERROR_SEGMENT_PUBLISH,
                "documentS3Location": document_uri}

    results_bucket, results_prefix = parse_s3_uri(results_uri)
    write_json(results_bucket, _join(results_prefix, f"{SEGMENT_RESULTS_PREFIX}{segment_key}.json"), {
        "schemaVersion": 1, "segmentKey": segment_key, "segmentKind": SEGMENT_KIND, "label": label,
        "startMs": start_ms, "endMs": end_ms, "description": result["description"], "keywords": result["keywords"],
        "objects": result["objects"], "actions": result["actions"], "textSeen": result["textSeen"],
        "analysisModelId": BEDROCK_ANALYSIS_MODEL_ID, "usage": {"inputTokens": int(usage.get("inputTokens", 0) or 0),
                                                                "outputTokens": int(usage.get("outputTokens", 0) or 0)},
        # The sensitive-information filter's masking, types only; the description carries its type tokens.
        "guardrailMasked": bool(masked_types), "guardrailMaskedTypes": masked_types,
        "generatedAt": generated_at, "documentS3Location": document_uri,
    })
    if masked_types:
        logger.info({"message": "Guardrail masked entities in the window analysis", "segmentKey": segment_key,
                     "maskedTypes": masked_types})
    logger.info({"message": "Segment analyzed", "segmentKey": segment_key, "documentS3Location": document_uri,
                 "frames": len(image_blocks)})
    return {"segmentKey": segment_key, "status": STATUS_SUCCEEDED, "documentS3Location": document_uri}
