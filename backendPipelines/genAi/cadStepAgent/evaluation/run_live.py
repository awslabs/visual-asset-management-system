# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run corpus cases through a DEPLOYED VAMS stack's CAD STEP agent workflows with ``vamscli``, and score them.

Per case it uploads the modify input to the asset, executes the generate or modify workflow with the case's
template tags, polls the execution to a terminal state, downloads the output STEP and its report, reads the
``cadAgent*`` file metadata, and writes ``<out>/<case>/run.json`` and ``quality.json`` in the shape
run_offline.py writes, so both runners' directories render, rescore and judge the same way.

Opt-in: every case is a real execution that spends Amazon Bedrock tokens and runtime time on the stack.

    python run_live.py --out <dir> --profile <vamscli profile> --db <database id> --asset <asset id> \\
        --gen-workflow <generate workflow id> --mod-workflow <modify workflow id> [--cases G1,FM4] \\
        [--log-group <agent runtime log group>]

``vamscli`` must be logged in for the profile (``vamscli --profile <p> auth login``). ``--log-group`` adds a
per-run scan of the agent's runtime log (ERROR, traceback and WARNING counts) and needs AWS credentials
that may read it.
"""

import argparse
import json
import os
import subprocess  # nosec B404 - vamscli is run with an argument list, never through a shell
import sys
import time

import boto3
import yaml
from botocore.config import Config

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scoring  # noqa: E402

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

PIPELINE_ID = "genai-cad-step-agent"
TEMPLATE_GENERATE = "cad-step-agent-generate"
TEMPLATE_MODIFY = "cad-step-agent-modify"
TERMINAL = {"succeeded", "success", "failed", "failure", "aborted", "cancelled", "canceled", "timed_out", "error",
            "complete", "completed"}
REPORT_SUFFIX = ".cad-agent-report.md"


def sh(args, log, check=True):
    started = time.time()
    proc = subprocess.run(args, capture_output=True, text=True, check=False)  # nosec B603 - argument list
    log.write(f"\n$ {' '.join(a if len(a) < 200 else a[:200] + '...' for a in args)}\n"
              f"[rc={proc.returncode} {time.time() - started:.1f}s]\n{proc.stdout[-6000:]}\n{proc.stderr[-3000:]}\n")
    log.flush()
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed rc={proc.returncode}: {' '.join(args[:6])}\n{proc.stderr[-2000:]}")
    return proc


def vjson(ctx, args, log, check=True):
    """A vamscli call with --json-output, parsed (vamscli may print a line before the JSON)."""
    proc = sh([ctx["vamscli"], "--profile", ctx["profile"]] + args + ["--json-output"], log, check=check)
    out = proc.stdout.strip()
    starts = [i for i in (out.find("{"), out.find("[")) if i >= 0]
    try:
        return json.loads(out[min(starts):] if starts else out), proc
    except json.JSONDecodeError:
        return {"_raw": out[:2000]}, proc


def deep_find(doc, key):
    """Every value stored under ``key`` anywhere in a nested document."""
    found = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k == key:
                found.append(v)
            found += deep_find(v, key)
    elif isinstance(doc, list):
        for v in doc:
            found += deep_find(v, key)
    return found


def execution_status(details):
    if not isinstance(details, dict):
        return ""
    for key in ("executionStatus", "status", "workflowStatus", "state"):
        if isinstance(details.get(key), str):
            return details[key].lower()
    for key in ("execution", "data", "message"):
        if isinstance(details.get(key), dict):
            status = execution_status(details[key])
            if status:
                return status
    return ""


def list_files(ctx, log):
    doc, _ = vjson(ctx, ["file", "list", "-d", ctx["db"], "-a", ctx["asset"], "--page-size", "1000"], log, check=False)
    items = []
    for value in deep_find(doc, "items") or deep_find(doc, "files"):
        if isinstance(value, list):
            items += value
    return {f["relativePath"]: f for f in items
            if isinstance(f, dict) and f.get("relativePath") and not f.get("isFolder")}


def download(ctx, key, dest_dir, log):
    os.makedirs(dest_dir, exist_ok=True)
    vjson(ctx, ["assets", "download", dest_dir, "-d", ctx["db"], "-a", ctx["asset"], "--file-key", key,
                "--flatten-download-tree"], log, check=False)
    name = os.path.basename(key)
    for root, _, files in os.walk(dest_dir):
        if name in files:
            return os.path.join(root, name)
    return None


def file_metadata(ctx, key, log):
    doc, _ = vjson(ctx, ["metadata", "file", "list", "-d", ctx["db"], "-a", ctx["asset"], "--file-path", key.lstrip("/"),
                         "--type", "metadata"], log, check=False)
    flat = {}
    for rows in deep_find(doc, "metadata") + deep_find(doc, "items"):
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and "metadataKey" in row:
                flat[row["metadataKey"]] = row.get("metadataValue")
    return flat


def template_tags(case, globals_):
    # WRITE_AGENT_METADATA defaults off; the run record and the honesty scorer read the cadAgent* metadata.
    tags = [{"key": "PROMPT", "value": str(case["prompt"]).strip()},
            {"key": "ALLOW_INTERNET_RESEARCH", "value": bool(case.get("research", False))},
            {"key": "MAX_ATTEMPTS", "value": int(globals_.get("maxAttempts", 4))},
            {"key": "WRITE_AGENT_METADATA", "value": True}]
    if case["mode"] == "generate":
        tags.append({"key": "DESIGN_NAME", "value": case.get("designName", case["id"])})
    return tags


def resolve_input(case, corpus, out_root, ctx, log):
    """(input key on the asset, local input path, source label) for a modify case; (None, None, '') otherwise."""
    spec = case.get("input")
    if case["mode"] != "modify" or not spec:
        return None, None, ""
    if "case" in spec:
        run_json = os.path.join(out_root, spec["case"], "run.json")
        if os.path.isfile(run_json):
            with open(run_json, encoding="utf-8") as fh:
                prior = json.load(fh)
            if prior.get("output_key") and prior.get("output_step") and os.path.isfile(prior["output_step"]):
                return prior["output_key"], prior["output_step"], f"case:{spec['case']}"
        local = os.path.join(corpus["_dir"], spec["fallback"])
        source = f"golden:{os.path.basename(local)} (fallback)"
    else:
        local = os.path.join(corpus["_dir"], corpus["globals"].get("fixtures_dir", "fixtures"), spec["fixture"])
        source = f"fixture:{spec['fixture']}"
    location = f"/inputs/{case['id']}/"
    vjson(ctx, ["file", "upload", "-d", ctx["db"], "-a", ctx["asset"], "--asset-location", location, "--hide-progress",
                os.path.abspath(local)], log)
    return location + os.path.basename(local), os.path.abspath(local), source


def runtime_log_scan(log_group, t_start, t_end, out_path):
    """ERROR / traceback / WARNING counts in the agent's runtime log for the run's window."""
    logs = boto3.client("logs", config=retry_config)
    events = []
    streams = logs.describe_log_streams(logGroupName=log_group, orderBy="LastEventTime", descending=True, limit=4)
    for stream in streams.get("logStreams", []):
        response = logs.get_log_events(logGroupName=log_group, logStreamName=stream["logStreamName"],
                                       startTime=int((t_start - 10) * 1000), endTime=int((t_end + 30) * 1000),
                                       startFromHead=True, limit=2000)
        events += response.get("events", [])
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(sorted(events, key=lambda e: e["timestamp"]), fh, indent=1)
    lines = [e["message"] for e in events]
    return {"events": len(lines), "error_lines": sum(" ERROR " in line for line in lines),
            "tracebacks": sum("Traceback" in line for line in lines),
            "warning_lines": sum(" WARNING " in line for line in lines),
            "uploaded_step": sum("uploaded STEP" in line for line in lines)}


def run_case(case, corpus, args, ctx):
    case_dir = os.path.join(args.out, case["id"])
    os.makedirs(case_dir, exist_ok=True)
    with open(os.path.join(case_dir, "driver.log"), "a", encoding="utf-8") as log:
        input_key, input_local, input_source = resolve_input(case, corpus, args.out, ctx, log)
        params = {PIPELINE_ID: {"templateId": TEMPLATE_GENERATE if case["mode"] == "generate" else TEMPLATE_MODIFY,
                                "templateTags": template_tags(case, corpus["globals"])}}
        params_file = os.path.join(case_dir, "pipeline-parameters.json")
        with open(params_file, "w", encoding="utf-8") as fh:
            json.dump(params, fh, indent=1)
        cmd = ["workflow", "execute", "--workflow-database-id", "GLOBAL", "--pipeline-parameters-file", params_file]
        if case["mode"] == "generate":
            cmd += ["-w", ctx["gen_workflow"], "--output-asset-id", ctx["asset"], "--output-database-id", ctx["db"],
                    "--output-path-prefix", f"/{case['id']}/"]
        else:
            cmd += ["-w", ctx["mod_workflow"], "--input-file", f"{ctx['db']}:{ctx['asset']}:{input_key}"]
        before = list_files(ctx, log)
        t_exec = time.time()
        response, proc = vjson(ctx, cmd, log, check=False)
        exec_ids = deep_find(response, "executionId") or deep_find(response, "workflowExecutionId")
        exec_id = exec_ids[0] if exec_ids else None
        status, details, error = "", {}, None
        if proc.returncode != 0 or not exec_id:
            error = f"workflow execute failed rc={proc.returncode}: {json.dumps(response)[:600]}"
        while exec_id and time.time() - t_exec < args.timeout:
            details, _ = vjson(ctx, ["execution", "details", exec_id], log, check=False)
            status = execution_status(details)
            if status in TERMINAL:
                break
            time.sleep(args.poll)
        with open(os.path.join(case_dir, "execution-details.json"), "w", encoding="utf-8") as fh:
            json.dump(details, fh, indent=1, default=str)
        after = list_files(ctx, log)
        new_keys = sorted(k for k in after if k not in before)
        scope = f"/{case['id']}/" if case["mode"] == "generate" else os.path.dirname(input_key) + "/"
        steps = [k for k in new_keys if k.lower().endswith((".step", ".stp")) and k.startswith(scope)]
        step_key = steps[0] if steps else (input_key if case["mode"] == "modify" else None)
        report_key = step_key + REPORT_SUFFIX if step_key and step_key + REPORT_SUFFIX in after else None
        downloads = os.path.join(case_dir, "download")
        step_local = download(ctx, step_key, downloads, log) if step_key else None
        report_local = download(ctx, report_key, downloads, log) if report_key else None
        metadata = file_metadata(ctx, step_key, log) if step_key else {}
        failure = details.get("executionError") if isinstance(details, dict) else None
        run = {"case": case["id"], "mode": case["mode"], "execution_id": exec_id, "pipeline_status": status,
               "status": metadata.get("cadAgentStatus") or ("failed" if status in ("failed", "failure") else "unknown"),
               "error": error, "failure_cause": failure, "pipeline_wall_s": round(time.time() - t_exec, 1),
               "output_key": step_key, "output_step": step_local, "report_key": report_key, "report_path": report_local,
               "metadata": metadata, "input_key": input_key, "input_path": input_local, "input_source": input_source,
               "attempt_count": int(metadata["cadAgentAttempts"]) if str(metadata.get("cadAgentAttempts", "")).isdigit() else 0,
               "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_exec))}
        with open(os.path.join(case_dir, "run.json"), "w", encoding="utf-8") as fh:
            json.dump(run, fh, indent=2, default=str)
        quality = scoring.score(case, run, step_local, input_local, corpus["_dir"])
        if args.log_group:
            quality["runtime_log"] = runtime_log_scan(args.log_group, t_exec, time.time(),
                                                      os.path.join(case_dir, "runtime-log.json"))
        with open(os.path.join(case_dir, "quality.json"), "w", encoding="utf-8") as fh:
            json.dump(quality, fh, indent=2, default=str)
    print(f"[{case['id']}] exec={exec_id} pipeline={status} agent={run['status']} geometry={quality['verdict']} "
          f"({quality['passed']}/{quality['total']}) honesty={quality['honesty_verdict']} "
          f"golden={(quality.get('golden_diff') or {}).get('verdict', '-')} err={error}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", default=os.path.join(HERE, "corpus.yaml"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--vamscli", default="vamscli")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--gen-workflow", required=True)
    parser.add_argument("--mod-workflow", required=True)
    parser.add_argument("--cases", help="comma-separated case ids (default: every case, corpus order)")
    parser.add_argument("--poll", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=1500)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--log-group", help="the agent runtime's CloudWatch log group, for a per-run log scan")
    args = parser.parse_args(argv)
    with open(args.corpus, encoding="utf-8") as fh:
        corpus = yaml.safe_load(fh)
    corpus["_dir"] = os.path.dirname(os.path.abspath(args.corpus))
    ctx = {"vamscli": args.vamscli, "profile": args.profile, "db": args.db, "asset": args.asset,
           "gen_workflow": args.gen_workflow, "mod_workflow": args.mod_workflow}
    os.makedirs(args.out, exist_ok=True)
    wanted = [c.strip() for c in args.cases.split(",")] if args.cases else None
    for case in corpus["cases"]:
        if wanted and case["id"] not in wanted:
            continue
        if args.skip_existing and os.path.isfile(os.path.join(args.out, case["id"], "quality.json")):
            continue
        try:
            run_case(case, corpus, args, ctx)
        except Exception as exc:  # one failing case must not stop the corpus pass; it is recorded
            case_dir = os.path.join(args.out, case["id"])
            os.makedirs(case_dir, exist_ok=True)
            with open(os.path.join(case_dir, "run.json"), "w", encoding="utf-8") as fh:
                json.dump({"case": case["id"], "mode": case["mode"], "status": "driver-error",
                           "error": f"driver exception: {exc}"}, fh, indent=2)
            print(f"[{case['id']}] DRIVER ERROR: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
