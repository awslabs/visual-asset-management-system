# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The stage sequence: preflight -> media -> Amazon Transcribe -> Amazon Bedrock -> render -> upload -> callback."""

import datetime
import json
import logging
import os
import re
import shutil
import tempfile
import time

from botocore.exceptions import ClientError

from .bedrock import BedrockUsage, extract_all_windows, finalize, verify_frames
from .errors import INPUT_REJECTED, LIMIT_EXCEEDED, PIPELINE_ERROR, TRANSCRIBE_FAILED, PipelineRejection
from .merge import merge_windows
from .preflight import check_connectivity, check_disk_budget
from .render import (
    asset_path,
    build_bom_rows,
    build_sop,
    compute_lab_summary,
    write_analysis_report,
    write_asset_metadata,
    write_bom_csv,
    write_bom_md,
    write_json,
    write_lab_summary_md,
    write_sop_md,
    write_summary,
)
from .sfn import send_task_failure, send_task_success, success_payload
from .signals import RunState
from .timeline import build_timeline, map_global_to_local
from .transcribe import SUBTITLE_FORMATS, delete_prefix, has_speech, run_transcription
from .upload import output_dirs, upload_outputs
from .windows import build_windows, transcript_text

logger = logging.getLogger("video_sop_bom_pipeline.pipeline")

SCRATCH_DIR = "/app/tmp"
# Amazon Transcribe's non-adjustable per-job ceilings.
TRANSCRIBE_MAX_BYTES = 2 * 1024**3
TRANSCRIBE_MAX_SECONDS = 28800
FRAME_STEP_MATCH_S = 5.0
# Two states only: a stage that raises still logs `end`; the REJECTED line names the failure.
STAGE_MARKER = "STAGE %d %s %s %.1fs"
USAGE_STAGE_PREFIXES = {"extract": "window-", "vision": "vision-", "finalize": "finalize"}
# The four configured input caps reported as limits.configured; maxKeyFramesCeiling is a tag ceiling, not a run cap.
CONFIGURED_CAPS = ("maxVideoFiles", "maxVideoFileSizeMb", "maxTotalInputSizeMb", "maxTotalDurationMinutes")


def _gb(size):
    return f"{size / 1024**3:.1f}"


def _hm(seconds):
    total = int(seconds)
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def _hms(seconds):
    total = int(seconds)
    return f"{total // 3600:02d}h{(total % 3600) // 60:02d}m{total % 60:02d}s"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _natural_key(text):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def _version(entry):
    value = entry.get("versionId")
    return value if value and value != "null" else None


def _ordered_inputs(definition):
    """Operator selection order by default; natural filename order under VIDEO_ORDER=filename."""
    entries = [dict(entry) for entry in definition["inputFiles"]]
    if definition["config"].get("videoOrder") == "filename":
        entries.sort(key=lambda entry: _natural_key(entry["relativePath"]))
    for index, entry in enumerate(entries):
        entry["index"] = index
        entry["name"] = os.path.basename(entry["relativePath"])
    return entries


def _frame_refs(steps, frames):
    """step number -> frame_key of the extracted frame nearest in time (within FRAME_STEP_MATCH_S)."""
    refs = {}
    for step in steps:
        t = float(step.get("timestamp_seconds", 0.0))
        best = None
        for frame in frames:
            delta = abs(float(frame["timestamp_seconds"]) - t)
            if delta <= FRAME_STEP_MATCH_S and (best is None or delta < best[0]):
                best = (delta, frame["frame_key"])
        if best is not None:
            refs[step["step"]] = best[1]
    return refs


def _configured(limits):
    return {key: limits[key] for key in CONFIGURED_CAPS}


def _stage_tokens(usage):
    totals = {}
    for stage_name, prefix in USAGE_STAGE_PREFIXES.items():
        bucket = {"calls": 0, "inputTokens": 0, "outputTokens": 0}
        for key, counters in usage.per_stage.items():
            if key.startswith(prefix):
                for field in bucket:
                    bucket[field] += counters[field]
        totals[stage_name] = bucket
    return totals


class _Stage:
    def __init__(self, ctx, index, name):
        self.ctx, self.index, self.name = ctx, index, name

    def __enter__(self):
        self.t0 = time.monotonic()
        self.started_at = _now()
        self.ctx.state.enter(self.index, self.name)
        logger.info(STAGE_MARKER, self.index, self.name, "start", 0.0)
        return self

    def __exit__(self, exc_type, exc, tb):
        duration = time.monotonic() - self.t0
        logger.info(STAGE_MARKER, self.index, self.name, "end", duration)
        self.ctx.stages.append({"index": self.index, "name": self.name, "startedAt": self.started_at, "durationS": round(duration, 3)})
        return False


class _Context:
    def __init__(self, definition, media, clients, work_dir, state, sleep):
        self.definition, self.media, self.clients, self.work_dir, self.state, self.sleep = definition, media, clients, work_dir, state, sleep
        self.stages = []
        self.warnings = []
        self.frames_skipped = []
        self.observed = {}
        self.started = time.monotonic()

    def stage(self, index, name):
        return _Stage(self, index, name)

    def put_analysis(self, name, obj):
        """Intermediate model results go to the KMS-encrypted aux prefix, never to the log."""
        key = f"{self.definition['auxTempPrefix']}analysis/{name}"
        try:
            self.clients.s3.put_object(Bucket=self.definition["auxBucket"], Key=key, Body=json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        except ClientError as exc:
            logger.warning("analysis artefact not written (%s): %s", name, exc.response.get("Error", {}).get("Code"))


def _run_stages(ctx):
    definition, media, clients = ctx.definition, ctx.media, ctx.clients
    config, limits, mode = definition["config"], definition["limits"], definition["config"]["mode"]
    model_id = definition["bedrockModelId"]
    usage = BedrockUsage()
    dirs = output_dirs(ctx.work_dir)
    input_dir = os.path.join(ctx.work_dir, "input")
    audio_dir = os.path.join(ctx.work_dir, "audio")
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(audio_dir, exist_ok=True)
    inputs = _ordered_inputs(definition)

    with ctx.stage(0, "preflight"):
        check_connectivity(clients, definition)

    with ctx.stage(1, "disk"):
        total_bytes = 0
        for entry in inputs:
            kwargs = {"Bucket": entry["bucket"], "Key": entry["key"]}
            if _version(entry):
                kwargs["VersionId"] = entry["versionId"]
            total_bytes += int(clients.s3.head_object(**kwargs)["ContentLength"])
        check_disk_budget(ctx.work_dir, total_bytes)

    with ctx.stage(2, "download"):
        per_file_cap = limits["maxVideoFileSizeMb"] * 1024**2
        total_cap = limits["maxTotalInputSizeMb"] * 1024**2
        local_paths = []
        downloaded = 0
        for entry in inputs:
            dst = os.path.join(input_dir, f"{entry['index']}_{entry['name']}")
            extra = {"VersionId": entry["versionId"]} if _version(entry) else None
            clients.s3.download_file(entry["bucket"], entry["key"], dst, ExtraArgs=extra)
            size = os.path.getsize(dst)
            if size > per_file_cap:
                raise PipelineRejection(INPUT_REJECTED, f"{entry['name']} is {_gb(size)} GB; this deployment allows at most {_gb(per_file_cap)} GB per video.")
            downloaded += size
            local_paths.append(dst)
        if downloaded > total_cap:
            raise PipelineRejection(INPUT_REJECTED, f"the selected videos total {_gb(downloaded)} GB; this deployment allows at most {_gb(total_cap)} GB in total.")
        ctx.observed["totalInputBytes"] = downloaded
        logger.info("downloaded videos=%d bytes=%d", len(local_paths), downloaded)

    with ctx.stage(3, "probe"):
        for entry, path in zip(inputs, local_paths):
            if not media.probe(path).has_audio:
                raise PipelineRejection(INPUT_REJECTED, f"{entry['name']} has no audio stream; a narrated video is required.")

    with ctx.stage(4, "audio"):
        tracks = []
        for entry, path in zip(inputs, local_paths):
            flac = os.path.join(audio_dir, f"{entry['index']}.flac")
            duration = media.extract_audio_flac(path, flac)
            tracks.append({"video_key": entry["relativePath"], "duration": duration, "path": flac})
        total_seconds = sum(track["duration"] for track in tracks)
        cap_seconds = limits["maxTotalDurationMinutes"] * 60
        if total_seconds > cap_seconds:
            raise PipelineRejection(
                LIMIT_EXCEEDED,
                f"total video duration {_hm(total_seconds)} exceeds this deployment's limit of {_hm(cap_seconds)} ({limits['maxTotalDurationMinutes']} minutes).",
            )
        ctx.observed["totalDurationSeconds"] = round(total_seconds, 3)
        logger.info("audio tracks=%d total=%.1fs", len(tracks), total_seconds)

    with ctx.stage(5, "concat"):
        combined = os.path.join(audio_dir, "combined.flac")
        combined_duration = media.concat_flac([track["path"] for track in tracks], combined)
        combined_bytes = os.path.getsize(combined)
        if combined_bytes > TRANSCRIBE_MAX_BYTES or combined_duration > TRANSCRIBE_MAX_SECONDS:
            raise PipelineRejection(
                LIMIT_EXCEEDED,
                f"the concatenated audio is {_gb(combined_bytes)} GB / {_hm(combined_duration)}; Amazon Transcribe accepts at most 2 GB and 8h00m per job.",
            )
        timeline = build_timeline([{"video_key": track["video_key"], "duration": track["duration"]} for track in tracks])
        audio_key = f"{definition['auxTempPrefix']}audio/combined.flac"
        clients.s3.upload_file(combined, definition["auxBucket"], audio_key)
        audio_uri = f"s3://{definition['auxBucket']}/{audio_key}"
        write_json(os.path.join(dirs["files"], "video-timeline.json"), timeline, "timeline_schema")
        logger.info("combined audio bytes=%d duration=%.1fs", combined_bytes, combined_duration)

    with ctx.stage(6, "transcribe"):
        transcript = run_transcription(clients, definition, audio_uri, ctx.work_dir, state=ctx.state, sleep=ctx.sleep)
        speech = has_speech(transcript.transcript_json)
        ctx.observed["language"] = transcript.language_code
        ctx.observed["subtitles"] = sorted(transcript.subtitle_paths)

    with ctx.stage(7, "gate"):
        # A speechless transcript is an input the pipeline cannot work from: the run is rejected on the
        # task token and nothing is ingested. On success the subtitle files are part of the deliverable
        # set, so a COMPLETED job that wrote none is a Transcribe failure, not a variant.
        if not speech:
            names = ", ".join(entry["name"] for entry in inputs)
            raise PipelineRejection(INPUT_REJECTED, f"no speech detected in {names}; a narrated video is required.")
        missing = sorted(set(SUBTITLE_FORMATS) - set(transcript.subtitle_paths))
        if missing:
            raise PipelineRejection(
                TRANSCRIBE_FAILED,
                f"Amazon Transcribe completed job {transcript.job_name} without the subtitle file(s) {', '.join(missing)}.",
            )
        shutil.copyfile(transcript.transcript_json_path, os.path.join(dirs["files"], "transcript.json"))
        for extension, path in transcript.subtitle_paths.items():
            shutil.copyfile(path, os.path.join(dirs["files"], f"transcript.{extension}"))
        with open(os.path.join(dirs["files"], "transcript.txt"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(transcript_text(transcript.transcript_json))
        run_analysis = mode == "full"
        logger.info("gate mode=%s speech=%s analysis=%s", mode, speech, run_analysis)

    product_name = (config.get("productName") or "").strip() or definition["assetName"]
    merged = None
    final = None
    vision = []
    frames = []
    frames_requested = 0
    window_count = 0

    if run_analysis:
        with ctx.stage(8, "extract"):
            windows = build_windows(transcript.transcript_json)
            results = extract_all_windows(clients, model_id, windows, config, product_name,
                                          max_key_frames=config["maxKeyFrames"], usage=usage, sleep=ctx.sleep)
            for window, result in results:
                ctx.put_analysis(f"window-{window.index}.json", result)
            window_count = len(results)

        with ctx.stage(9, "merge"):
            merged = merge_windows(results)
            ctx.put_analysis("merged.json", {"steps": merged.steps, "components": merged.components, "key_moments": merged.key_moments})
            logger.info("merged steps=%d components=%d keyMoments=%d", len(merged.steps), len(merged.components), len(merged.key_moments))

        with ctx.stage(10, "frames"):
            moments = merged.key_moments[:config["maxKeyFrames"]]
            frames_requested = len(moments)
            for momentIndex, moment in enumerate(moments):
                t = float(moment["timestamp_seconds"])
                video_index, local_t = map_global_to_local(timeline, t)
                name = f"keyframe-{momentIndex:04d}-{_hms(t)}.jpg"
                dst = os.path.join(dirs["files"], name)
                try:
                    media.extract_frame(local_paths[video_index], local_t, dst)
                except media.FrameExtractionFailed as exc:
                    logger.warning("frame skipped momentIndex=%d t=%.1fs: %s", momentIndex, t, exc)
                    ctx.frames_skipped.append({"momentIndex": momentIndex, "timestamp_seconds": t, "reason": str(exc)})
                    continue
                frames.append({
                    "momentIndex": momentIndex, "path": dst, "frame_key": asset_path(definition, name),
                    "timestamp_seconds": t, "local_timestamp": round(local_t, 3),
                    "video_index": video_index, "video_key": timeline["video_keys"][video_index],
                    "reason": moment.get("reason", ""), "expected_content": moment.get("expected_content", ""),
                })
            logger.info("frames requested=%d extracted=%d skipped=%d", frames_requested, len(frames), len(ctx.frames_skipped))

        with ctx.stage(11, "vision"):
            vision = verify_frames(clients, model_id, frames, merged.steps, merged.components,
                                   additional_instructions=config.get("additionalInstructions", ""), usage=usage, sleep=ctx.sleep) if frames else []
            ctx.put_analysis("vision.json", vision)

        with ctx.stage(12, "finalize"):
            final = finalize(clients, model_id, merged.steps, merged.components, vision, config, product_name, usage=usage, sleep=ctx.sleep)
            ctx.put_analysis("final.json", final)

    with ctx.stage(13, "render"):
        paths = {"transcript": asset_path(definition, "transcript.json"), "timeline": asset_path(definition, "video-timeline.json")}
        values = {
            "sopBom_latestExecutionId": definition["executionId"],
            "sopBom_mode": mode,
            "sopBom_videoCount": len(inputs),
            "sopBom_totalDurationSeconds": round(total_seconds, 3),
            "sopBom_language": transcript.language_code,
            "sopBom_transcriptPath": paths["transcript"],
        }
        vocab_misses = []
        if run_analysis:
            part_level_base = int(config["partLevelBase"])
            bom_rows, vocab_misses = build_bom_rows(final["bom_rows"], config, product_name)
            sop = build_sop(merged.steps, final, product_name, merged.product_name_from_narration, timeline["video_keys"], timeline,
                            _frame_refs(merged.steps, frames), bom_rows)
            write_json(os.path.join(dirs["files"], "sop.json"), sop, "sop_schema")
            write_sop_md(os.path.join(dirs["files"], "sop.md"), sop)
            # bom.json echoes the config's partLevelBase string; the integer only indents bom.md.
            write_json(os.path.join(dirs["files"], "bom.json"),
                       {"product_name": product_name, "part_level_base": config["partLevelBase"], "rows": bom_rows}, "bom_row_schema")
            write_bom_csv(os.path.join(dirs["files"], "bom.csv"), bom_rows)
            write_bom_md(os.path.join(dirs["files"], "bom.md"), bom_rows, part_level_base=part_level_base)
            lab = compute_lab_summary(final["lab_summary"], bom_rows, config, product_name, merged.product_name_from_narration)
            if config["generateLabSummary"]:
                write_json(os.path.join(dirs["files"], "lab-summary.json"), lab, "lab_summary_schema")
                write_lab_summary_md(os.path.join(dirs["files"], "lab-summary.md"), lab)
                paths["labSummary"] = asset_path(definition, "lab-summary.json")
                values["sopBom_labSummaryPath"] = paths["labSummary"]
            # frames.json follows frames_schema.json: `file` is the bare JPEG name and `path` the asset
            # path; the scratch path stays inside the container.
            write_json(os.path.join(dirs["files"], "frames.json"), {
                "executionId": definition["executionId"],
                "frames": [dict({key: value for key, value in frame.items() if key not in ("path", "frame_key")},
                                file=os.path.basename(frame["path"]), path=frame["frame_key"]) for frame in frames],
                "skipped": ctx.frames_skipped,
                "video_timeline": timeline["video_timeline"],
            }, "frames_schema")
            paths.update({"sop": asset_path(definition, "sop.json"), "bom": asset_path(definition, "bom.json"),
                          "bomCsv": asset_path(definition, "bom.csv"), "frames": asset_path(definition, "frames.json")})
            values.update({
                "sopBom_productName": product_name,
                "sopBom_componentCount": len(bom_rows),
                "sopBom_stepCount": len(sop["steps"]),
                "sopBom_totalMassG": lab["total_mass_g"],
                "sopBom_sopPath": paths["sop"],
                "sopBom_bomPath": paths["bomCsv"],
            })
        stage_tokens = _stage_tokens(usage)
        report = {
            "mode": mode, "config": config, "modelId": model_id,
            "stages": [dict({key: value for key, value in stage.items() if key != "index"},
                            **stage_tokens.get(stage["name"], {"calls": 0, "inputTokens": 0, "outputTokens": 0})) for stage in ctx.stages],
            "windows": window_count, "framesRequested": frames_requested, "framesExtracted": len(frames), "framesSkipped": ctx.frames_skipped,
            "limits": {"configured": _configured(limits), "observed": ctx.observed}, "warnings": ctx.warnings, "vocabularyMisses": vocab_misses,
        }
        write_analysis_report(os.path.join(dirs["files"], "analysis-report.json"), report)
        paths["analysisReport"] = asset_path(definition, "analysis-report.json")
        write_asset_metadata(os.path.join(dirs["metadata"], "asset.metadata.json"), definition, values)
        summary = {
            "mode": mode, "status": "SUCCEEDED", "fileCount": len(os.listdir(dirs["files"])), "bedrock": usage.as_dict(),
            "limits": {"configured": _configured(limits), "observed": ctx.observed},
            "timings": {"stages": [{"name": stage["name"], "durationS": stage["durationS"]} for stage in ctx.stages],
                        "elapsedS": round(time.monotonic() - ctx.started, 3)},
            "warnings": ctx.warnings, "paths": paths, "config": config,
        }
        write_summary(os.path.join(dirs["results"], "summary.json"), summary)

    with ctx.stage(14, "upload"):
        upload_report = upload_outputs(clients, definition, ctx.work_dir)
        removed = delete_prefix(clients.s3, definition["auxBucket"], definition["auxTempPrefix"])
        logger.info("aux prefix deleted objects=%d", removed)

    with ctx.stage(15, "report"):
        send_task_success(clients, success_payload(upload_report.file_count, upload_report.summary_key))


def run(definition, media, clients, *, work_dir=None, state=None, sleep=time.sleep):
    """Run every stage; returns "SUCCEEDED" or "FAILED". Every failure is reported on the inner token with
    a code in `error` and a sentence in `cause`; a handled failure leaves the aux transcribe/ and
    analysis/ objects in place for diagnosis."""
    state = state if state is not None else RunState()
    state.clients = clients
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="videosopbom-", dir=SCRATCH_DIR if os.path.isdir(SCRATCH_DIR) else None)
    os.makedirs(work_dir, exist_ok=True)
    ctx = _Context(definition, media, clients, work_dir, state, sleep)
    logger.info("run executionId=%s mode=%s videos=%d", definition["executionId"], definition["config"]["mode"], len(definition["inputFiles"]))
    try:
        _run_stages(ctx)
    except PipelineRejection as rejection:
        logger.error("REJECTED stage=%d (%s) code=%s", state.stage_index, state.stage_name, rejection.code)
        send_task_failure(clients, rejection.code, rejection.cause)
        return "FAILED"
    except Exception as exc:  # the .sync task must still see a readable cause, not a bare exit
        logger.exception("unexpected failure at stage %d (%s)", state.stage_index, state.stage_name)
        send_task_failure(
            clients, PIPELINE_ERROR,
            f"unexpected {type(exc).__name__} at stage {state.stage_index} ({state.stage_name}): {str(exc)[:2000]}",
        )
        return "FAILED"
    return "SUCCEEDED"
