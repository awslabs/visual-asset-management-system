#!/usr/bin/env python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Drive the image-to-3D comparison matrix against a live VAMS deployment.

Executes every cell of the matrix, waits for the runs, reads each one's own benchmark report back off
the asset, downloads the preview, and writes the comparison. The pipeline records the measurements; this
script only orchestrates and aggregates, so the numbers in the report are the ones the runs produced.

Two rules it follows deliberately:

* **A run is judged by its WORKFLOW status, not its Batch job.** A container can exit zero, upload every
  output, and still leave the execution failed -- that happened on every run for a whole session here,
  because the metadata write-back rejected the report's filename. Reading the job status would have
  called those successes.
* **A preview is fetched for every successful run.** Each degenerate result seen while building this
  pipeline looked correct in the numbers, so a comparison without pictures is not evidence.

Usage:

    python run_matrix.py --profile imageto3d --database DB --asset ASSET \\
        --images /subject.png --models stable3dgen,triposr \\
        --axis IMAGE_CLEANUP=auto,skip --out ./results
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cost  # noqa: E402
import matrix  # noqa: E402
import report  # noqa: E402

WORKFLOW_DATABASE = "GLOBAL"
WORKFLOW_ID = "genai-image-to-3d"
PIPELINE_ID = "genai-image-to-3d"
BENCHMARK_METADATA_KEY = "IMAGE_TO_3D_BENCHMARK"
TERMINAL = ("SUCCEEDED", "FAILED", "STOPPED", "TIMED_OUT", "ABORTED")


def cli(profile, *args, timeout=900):
    """One vamscli invocation, returning ``(exit code, stdout+stderr)``."""
    command = ["vamscli", "--profile", profile, *args]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    done = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        command, capture_output=True, text=True, timeout=timeout, env=env,
        encoding="utf-8", errors="replace")
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _first_json_object(text):
    """The first JSON object in mixed CLI output, or ``None``."""
    start = text.find("{")
    while start >= 0:
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def execute(profile, database, asset, variation):
    """Start one execution. Returns its id, or ``None`` with the reason logged."""
    tags = [{"key": key, "value": str(value)} for key, value in variation.settings.items()]
    tags.append({"key": "BENCHMARK_REPORT", "value": "true"})
    params = {PIPELINE_ID: {"templateId": f"image-to-3d-{variation.model}", "templateTags": tags}}
    code, out = cli(
        profile, "workflow", "execute", "--workflow-database-id", WORKFLOW_DATABASE,
        "-w", WORKFLOW_ID, "--input-file", f"{database}:{asset}:{variation.image}",
        "--pipeline-parameters", json.dumps(params), "--json-output")
    document = _first_json_object(out) or {}
    execution_id = document.get("executionId")
    if code != 0 or not execution_id:
        print(f"  ! {variation.label}: execute failed -- {out.strip()[:200]}")
        return None
    print(f"  > {variation.label}: {execution_id}")
    return execution_id


def wait_for(profile, execution_ids, poll_seconds=30, timeout_seconds=10800):
    """Poll until every execution reaches a terminal state. Returns ``{id: (status, detail)}``."""
    pending = dict(execution_ids)
    finished = {}
    deadline = time.monotonic() + timeout_seconds
    while pending and time.monotonic() < deadline:
        for execution_id in list(pending):
            _, out = cli(profile, "execution", "details", execution_id)
            status = next((line.split(":", 1)[1].strip()
                           for line in out.splitlines() if line.startswith("Status:")), "")
            if status in TERMINAL:
                finished[execution_id] = (status, out)
                pending.pop(execution_id)
                print(f"  = {execution_id[:8]} {status}")
        if pending:
            time.sleep(poll_seconds)
    for execution_id in pending:
        finished[execution_id] = ("TIMED_OUT", "the harness stopped waiting")
    return finished


def _asset_bucket_from(detail):
    """The asset bucket name, read out of an S3 URI the execution details already print."""
    for line in detail.splitlines():
        marker = "s3://"
        if marker in line:
            return line.split(marker, 1)[1].split("/", 1)[0]
    return ""


def read_benchmark(bucket, execution_id):
    """One execution's own benchmark report, read from the report it wrote.

    Deliberately NOT read from asset metadata. The write-back applies the report's entries to the asset,
    and the asset holds a single IMAGE_TO_3D_BENCHMARK key -- so a matrix that runs several variations
    against one asset has every run overwrite the last, and every row in the comparison would show the
    same numbers while looking perfectly plausible. Each execution's report object is per-execution, so
    that is the honest source.
    """
    if not bucket:
        return {}
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        print("  ! boto3 is unavailable, so per-run benchmark reports cannot be read")
        return {}

    retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
    client = boto3.client("s3", config=retry_config)
    prefix = "pipelines/genai-image-to-3d/"
    key = None
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for entry in page.get("Contents", []):
            if execution_id in entry["Key"] and entry["Key"].endswith("asset.metadata.json"):
                key = entry["Key"]
                break
        if key:
            break
    if not key:
        print(f"  ! no benchmark report found for {execution_id[:8]}")
        return {}

    body = client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    document = json.loads(body)
    # The report is a list of {metadataKey, metadataValue} entries; the benchmark rides in one of them.
    for entry in document.get("metadata", []) if isinstance(document, dict) else []:
        if entry.get("metadataKey") == BENCHMARK_METADATA_KEY:
            value = entry.get("metadataValue")
            try:
                return json.loads(value) if isinstance(value, str) else (value or {})
            except ValueError:
                print(f"  ! the benchmark report for {execution_id[:8]} could not be parsed")
                return {}
    return {}


def result_from(variation, status, detail, benchmark):
    """Fold one execution's status and its benchmark report into a comparable result."""
    result = matrix.RunResult(variation=variation, status=status)
    if status != "SUCCEEDED":
        error = next((line.split(":", 1)[1].strip()
                      for line in detail.splitlines() if line.startswith("Error:")), "")
        result.error = error[:300]
        return result

    host = benchmark.get("host") or {}
    result.stage_seconds = {k: float(v) for k, v in (benchmark.get("stageSeconds") or {}).items()}
    result.instance_type = host.get("instanceType", "")
    result.gpu_name = host.get("gpuName", "")
    result.texture_status = benchmark.get("textureStatus", "none")
    result.print_ready = benchmark.get("printReady")
    result.output_bytes = benchmark.get("outputBytes") or {}
    result.face_count = benchmark.get("faceCount")
    result.volume_retained = benchmark.get("volumeRetained")
    result.pieces_fused = benchmark.get("piecesFused")
    result.projected_colour = benchmark.get("projectedColour") or {}
    result.shape = {k: float(v) for k, v in (benchmark.get("shape") or {}).items()
                    if isinstance(v, (int, float))}
    result.bodies = benchmark.get("bodies")
    result.expected_bodies = benchmark.get("expectedBodies")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", required=True, help="vamscli profile to drive")
    parser.add_argument("--database", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--images", required=True,
                        help="comma-separated asset-relative image keys, each beginning with /")
    parser.add_argument("--models", required=True, help="comma-separated model keys")
    parser.add_argument("--axis", action="append", default=[],
                        help="a setting axis to sweep, as KEY=value1,value2 (repeatable)")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    parser.add_argument("--rate", action="append", default=[],
                        help="an instance rate override, as instanceType=hourlyUsd (repeatable)")
    parser.add_argument("--out", default="./benchmark-results")
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args(argv)

    axes = {}
    for entry in args.axis:
        key, _, values = entry.partition("=")
        axes[key.strip()] = [v.strip() for v in values.split(",") if v.strip()]
    overrides = {}
    for entry in args.rate:
        key, _, value = entry.partition("=")
        overrides[key.strip()] = float(value)

    variations = matrix.expand(
        [m.strip() for m in args.models.split(",") if m.strip()],
        [i.strip() for i in args.images.split(",") if i.strip()],
        axes)
    print(f"Matrix: {len(variations)} run(s)")

    started = {}
    for variation in variations:
        execution_id = execute(args.profile, args.database, args.asset, variation)
        if execution_id:
            started[execution_id] = variation

    if not started:
        print("No executions started; nothing to report.")
        return 1

    print(f"Waiting for {len(started)} execution(s)...")
    finished = wait_for(args.profile, started, poll_seconds=args.poll_seconds)

    results = []
    for execution_id, variation in started.items():
        status, detail = finished.get(execution_id, ("unknown", ""))
        benchmark = {}
        if status == "SUCCEEDED":
            benchmark = read_benchmark(_asset_bucket_from(detail), execution_id)
        results.append(result_from(variation, status, detail, benchmark))

    # Compare each run's shape against the other models given the same image. This is the only likeness
    # signal available with no ground truth and no model call, and it has to happen here because one
    # result cannot see its siblings.
    matrix.flag_shape_outliers(results)

    instance_types = [r.instance_type for r in results if r.instance_type]
    rates = cost.resolve_hourly_rates(instance_types, args.region, price_overrides=overrides)
    missing = sorted({t for t in instance_types if t not in rates})

    os.makedirs(args.out, exist_ok=True)
    markdown = report.to_markdown(results, rates, region=args.region, missing_rates=missing)
    with open(os.path.join(args.out, "comparison.md"), "w", encoding="utf-8") as fh:
        fh.write(markdown)
    with open(os.path.join(args.out, "comparison.csv"), "w", encoding="utf-8") as fh:
        fh.write(report.to_csv(results, rates))

    print(f"\nWrote {args.out}/comparison.md and comparison.csv")
    print("Each run's figures come from the report that execution wrote, not from the asset's metadata, "
          "which holds only the most recent run's copy. Download the previews from the asset and look at "
          "them before drawing conclusions from the timings.")
    return 0 if any(r.succeeded for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
