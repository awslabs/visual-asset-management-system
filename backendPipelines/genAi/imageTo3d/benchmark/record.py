#!/usr/bin/env python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Record a round of live runs as a baseline, and diff a later round against it.

A model comparison is only worth anything if a later one can be held against it, and the numbers are
easy to lose: each execution writes its own report to S3 under its own id, and the asset's metadata
holds only the most recent run's copy. This harvests a named set of executions into one file that goes
into the repository beside the code, and compares two such files.

What a recording carries, and why each part is needed to make a later comparison meaningful:

*   the **git commit** the container was built from -- a difference between two rounds is otherwise
    unattributable, and this pipeline's post-generation behaviour has changed repeatedly;
*   the **instance type and GPU** per run -- measured on this pipeline, two stochastic models produced
    materially different geometry on different GPU types with identical seeds and settings, so a
    timing or shape difference across rounds may be a hardware difference;
*   the **settings actually resolved**, not the ones requested, since defaults fill in the rest;
*   per-stage **seconds**, the **gate verdict**, the **shape descriptors** and the **texture status**.

Usage:

    # Record a round. The runs file is "name executionId" per line, as the smoke tests produce.
    python record.py --runs smoke-runs.txt --out baselines/2026-08-24-four-models.json \\
        --note "four models plus the two print-feature configurations, all on g5.8xlarge"

    # Diff a later round against it.
    python record.py --compare baselines/2026-08-24-four-models.json --runs new-runs.txt

    # Or diff two recordings that already exist.
    python record.py --compare baselines/old.json --against baselines/new.json
"""

import argparse
import json
import os
import subprocess

# Where a run's own report lands: pipelines/{pipelineId}/{jobName}/output/{executionId}/metadata/.
# The jobName segment is derived per workflow record, so it is NOT a constant -- a round launched against
# a re-registered workflow lands under a different one. Hardcoding it made a whole round of runs report
# "no report found" while every execution had in fact written its report.
PIPELINE_ROOT = "pipelines/genai-image-to-3d"
# Two places to look, newest first. A run now writes its report to its own results/ prefix, whose
# contents the workflow reads onto the EXECUTION; before that it went to asset metadata, so an
# earlier round is only readable there. Both are tried so a recording can still be diffed against
# a round taken before the move.
REPORT_RELATIVES = ("results/imageTo3d_report.json", "metadata/asset.metadata.json")
BENCHMARK_KEY = "IMAGE_TO_3D_BENCHMARK"
GATE_KEY = "IMAGE_TO_3D_PRINT_GATE_REPORT"

STAGE_ORDER = ("weightCache", "inputDownload", "preprocess", "generate", "meshLoad", "cleanup",
               "projectColour", "printFeatures", "export", "upload")

# Fields whose movement between rounds is worth reporting, and how much movement counts. A timing that
# drifts by a tenth is noise on a shared GPU; a gate verdict that flips is never noise.
NUMERIC_TOLERANCE = {
    "shape.fillRatio": 0.02,
    "shape.footprintAspect": 0.02,
    "shape.heightOverWidth": 0.02,
    "faceCount": 0.05,        # relative
    "totalRunSeconds": 0.25,  # relative; a shared queue moves this legitimately
}


def _aws_options(profile, region):
    """The AWS CLI's profile and Region options; no ``--profile`` means the default credential chain."""
    return (["--profile", profile] if profile else []) + ["--region", region]


def _s3_text(bucket, key, profile, region):
    out = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "cp", f"s3://{bucket}/{key}", "-", *_aws_options(profile, region)],
        capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def _job_name_prefixes(bucket, profile, region):
    """Every ``{jobName}`` prefix under the pipeline root, newest first.

    Listed rather than assumed, so a round launched after the workflow was re-registered is still found.
    """
    out = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "ls", f"s3://{bucket}/{PIPELINE_ROOT}/", *_aws_options(profile, region)],
        capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [line.split()[-1].rstrip("/") for line in out.stdout.splitlines() if line.strip().endswith("/")]


def _find_report(bucket, execution_id, profile, region):
    """One execution's report text, searched across the jobName prefixes. ``("", "")`` when absent."""
    for job_name in _job_name_prefixes(bucket, profile, region):
        for relative in REPORT_RELATIVES:
            key = f"{PIPELINE_ROOT}/{job_name}/output/{execution_id}/{relative}"
            body = _s3_text(bucket, key, profile, region)
            if body:
                return body, key
    return "", ""


def _parse(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def harvest(execution_id, bucket, profile, region):
    """One execution's report, read from the object that execution wrote.

    Deliberately not from asset metadata: several runs against one asset overwrite each other there, so
    every row of a recorded round would show the last run's numbers while looking entirely plausible.
    """
    body, key = _find_report(bucket, execution_id, profile, region)
    if not body:
        return None
    print(f"  read {key}")
    document = json.loads(body) or {}
    if "metadata" in document and isinstance(document.get("metadata"), list):
        # The asset-metadata envelope: entries as {metadataKey, metadataValue}, every value a string.
        fields = {e["metadataKey"]: e["metadataValue"]
                  for e in document["metadata"] if "metadataKey" in e}
        benchmark = _parse(fields.get(BENCHMARK_KEY)) or {}
        gate = _parse(fields.get(GATE_KEY)) or {}
    else:
        # The execution report: the data itself, keys lower-cased with the namespacing prefix dropped
        # and nested parts already parsed.
        fields = {f"IMAGE_TO_3D_{k.upper()}": v for k, v in document.items()}
        benchmark = _parse(fields.get(BENCHMARK_KEY)) or {}
        gate = _parse(fields.get(GATE_KEY)) or {}
    if not benchmark:
        return None
    host = benchmark.get("host") or {}
    return {
        "executionId": execution_id,
        "model": benchmark.get("model") or fields.get("IMAGE_TO_3D_MODEL"),
        "instanceType": host.get("instanceType"),
        "gpu": host.get("gpuName"),
        "settings": benchmark.get("settings") or {},
        "stageSeconds": {k: benchmark["stageSeconds"][k]
                         for k in STAGE_ORDER if k in (benchmark.get("stageSeconds") or {})},
        "totalRunSeconds": benchmark.get("totalRunSeconds"),
        "textureStatus": benchmark.get("textureStatus"),
        "printReady": benchmark.get("printReady"),
        "faceCount": benchmark.get("faceCount"),
        "piecesFused": benchmark.get("piecesFused"),
        "projectedColour": benchmark.get("projectedColour") or {},
        "shape": benchmark.get("shape") or {},
        "printFeatures": benchmark.get("printFeatures") or {},
        "gate": {k: gate.get(k) for k in
                 ("isWatertight", "nonManifoldEdges", "nonManifoldVertices", "brokenFaceCount",
                  "bodies", "expectedBodies", "passed", "backendsApplied", "volumeRetained")},
    }


def _git(*args):
    out = subprocess.run(["git", *args], capture_output=True, text=True,  # nosemgrep
                         cwd=os.path.dirname(os.path.abspath(__file__)))
    return out.stdout.strip() if out.returncode == 0 else ""


def record(runs, bucket, profile, region, note="", recorded_at=""):
    """A recording of a round: every run's numbers plus what produced them."""
    entries = {}
    for name, execution_id in runs:
        harvested = harvest(execution_id, bucket, profile, region)
        if harvested is None:
            print(f"  ! {name}: no report found for {execution_id[:8]}, so it is left out")
            continue
        entries[name] = harvested
        print(f"  = {name}: {harvested['model']} on {harvested['instanceType']}")
    return {
        "note": note,
        "recordedAt": recorded_at,
        "commit": _git("rev-parse", "HEAD"),
        "commitSubject": _git("log", "-1", "--pretty=%s"),
        "runs": entries,
    }


def _flatten(entry):
    """The comparable fields of one run, flattened to dotted keys."""
    flat = {}
    for key in ("textureStatus", "printReady", "faceCount", "piecesFused", "totalRunSeconds",
                "instanceType", "gpu"):
        flat[key] = entry.get(key)
    for key, value in (entry.get("shape") or {}).items():
        flat[f"shape.{key}"] = value
    for key, value in (entry.get("gate") or {}).items():
        if key == "backendsApplied":
            flat["gate.backendsApplied"] = ",".join(value or [])
        else:
            flat[f"gate.{key}"] = value
    for stage, seconds in (entry.get("stageSeconds") or {}).items():
        flat[f"stage.{stage}"] = seconds
    return flat


def _moved(key, before, after):
    """Whether a field moved enough to report, given the tolerances."""
    if before is None or after is None:
        return before != after
    if isinstance(before, bool) or isinstance(after, bool) or isinstance(before, str):
        return before != after
    tolerance = NUMERIC_TOLERANCE.get(key)
    if tolerance is None and key.startswith("stage."):
        tolerance = 0.25  # relative; stage timings move with instance load
    if tolerance is None:
        return before != after
    if key in ("faceCount", "totalRunSeconds") or key.startswith("stage."):
        scale = max(abs(before), 1e-9)
        return abs(after - before) / scale > tolerance
    return abs(after - before) > tolerance


def compare(before, after):
    """``{run: [differences]}`` between two recordings, and the runs present in only one."""
    differences = {}
    names = sorted(set(before.get("runs", {})) | set(after.get("runs", {})))
    for name in names:
        first, second = before["runs"].get(name), after["runs"].get(name)
        if first is None:
            differences[name] = ["present only in the later recording"]
            continue
        if second is None:
            differences[name] = ["missing from the later recording"]
            continue
        moved = []
        flat_before, flat_after = _flatten(first), _flatten(second)
        for key in sorted(set(flat_before) | set(flat_after)):
            if _moved(key, flat_before.get(key), flat_after.get(key)):
                moved.append(f"{key}: {flat_before.get(key)} -> {flat_after.get(key)}")
        if moved:
            differences[name] = moved
    return differences


def to_markdown(recording) -> str:
    """The recording as a table, so the numbers are readable without parsing JSON."""
    runs = recording.get("runs") or {}
    out = ["# Image to 3D -- recorded round", ""]
    if recording.get("note"):
        out += [recording["note"], ""]
    out += [f"Container built from `{(recording.get('commit') or '')[:12]}` "
            f"({recording.get('commitSubject', '')}).", ""]

    out += ["## Outcome", "",
            "| run | model | instance | texture | ready | bodies/exp | fused | fill | fpAspect | h/w | faces |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, entry in runs.items():
        gate, shape = entry.get("gate") or {}, entry.get("shape") or {}
        out.append(
            f"| {name} | {entry.get('model')} | {entry.get('instanceType')} | "
            f"{entry.get('textureStatus')} | {entry.get('printReady')} | "
            f"{gate.get('bodies')}/{gate.get('expectedBodies')} | {entry.get('piecesFused') or 0} | "
            f"{shape.get('fillRatio', '')} | {shape.get('footprintAspect', '')} | "
            f"{shape.get('heightOverWidth', '')} | {entry.get('faceCount')} |")

    out += ["", "## Stage seconds", "",
            "| run | " + " | ".join(STAGE_ORDER) + " | total |",
            "| --- | " + " | ".join("---" for _ in STAGE_ORDER) + " | --- |"]
    for name, entry in runs.items():
        stages = entry.get("stageSeconds") or {}
        cells = " | ".join(f"{stages.get(s, 0):.1f}" for s in STAGE_ORDER)
        out.append(f"| {name} | {cells} | {entry.get('totalRunSeconds') or 0:.1f} |")
    out.append("")
    return "\n".join(out)


def _read_runs(path):
    runs = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            name, _, execution_id = line.strip().partition(" ")
            if name and execution_id.strip():
                runs.append((name, execution_id.strip()))
    return runs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", help="'name executionId' per line")
    parser.add_argument("--out", help="write the recording here (JSON); a .md is written beside it")
    parser.add_argument("--note", default="", help="what this round was")
    parser.add_argument("--recorded-at", default="", help="a date for the record; not derived, so a "
                                                          "recording is reproducible")
    parser.add_argument("--compare", help="an earlier recording to diff against")
    parser.add_argument("--against", help="a later recording; omit to harvest --runs instead")
    parser.add_argument("--bucket", help="the VAMS asset bucket the runs wrote to; required with --runs")
    parser.add_argument("--profile", help="AWS CLI profile; omit for the default credential chain")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"),
                        help="the deployment's Region; defaults to AWS_REGION")
    args = parser.parse_args(argv)
    if args.runs and not (args.bucket and args.region):
        parser.error("--runs needs --bucket and --region (or AWS_REGION)")

    if args.compare:
        with open(args.compare, encoding="utf-8") as handle:
            before = json.load(handle)
        if args.against:
            with open(args.against, encoding="utf-8") as handle:
                after = json.load(handle)
        elif args.runs:
            after = record(_read_runs(args.runs), args.bucket, args.profile, args.region)
        else:
            parser.error("--compare needs either --against or --runs")
        differences = compare(before, after)
        if not differences:
            print(f"\nnothing moved against {args.compare}")
            return 0
        print(f"\nagainst {args.compare} "
              f"(recorded from {(before.get('commit') or '')[:12]}):")
        for name, moved in differences.items():
            print(f"\n  {name}")
            for line in moved:
                print(f"    {line}")
        return 1

    if not args.runs:
        parser.error("--runs is required to record a round")
    recording = record(_read_runs(args.runs), args.bucket, args.profile, args.region,
                       note=args.note, recorded_at=args.recorded_at)
    text = json.dumps(recording, indent=2, sort_keys=True)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        markdown = os.path.splitext(args.out)[0] + ".md"
        with open(markdown, "w", encoding="utf-8") as handle:
            handle.write(to_markdown(recording))
        print(f"\nwrote {args.out} and {markdown}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
