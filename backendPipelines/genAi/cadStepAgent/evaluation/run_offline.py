# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run the CAD STEP agent on this host against Amazon Bedrock, case by case, from ``corpus.yaml``, and score
every run offline (geometry, honesty, golden diff).

The agent code is the container package (``../container`` by default, or ``--snapshot <dir>``, a checkout or
``git archive`` extraction of the repository whose container package to run). ``run.run_job`` is called with:

- a per-case definition in the shape ``constructPipeline.py`` produces (same key names, results folder
  included);
- an S3 stand-in that reads inputs from and writes outputs to a local directory;
- a Step Functions stand-in and an empty task token (no callback is attempted);
- an ``agent_factory`` that builds the real Strands agent on the Bedrock model, without a guardrail (the
  harness spends model invocations only), and records the transcript and token usage.

Per case it writes ``<out>/<case>/``: the STEP, its report, metadata and attribute documents, the results-folder
files, every attempt's script output, the transcript, ``run.json`` and ``quality.json``.

Sub-commands:
  one        run a single case (used as a subprocess by ``run``)
  run        run a wave of cases (generate cases first, then the modify cases chained on their outputs)
  report     render a Markdown report for one run directory, optionally against another
  rescore    re-score recorded runs after a corpus change, without running the agent
  digest     a quotable digest of a case's transcript

Needs a Python 3.12 venv with the container's requirements (CadQuery, Strands) plus PyYAML, and AWS
credentials that may invoke the model. The model is ``--model-id`` or ``BEDROCK_MODEL_ID``.
"""

import argparse
import concurrent.futures
import glob
import json
import logging
import os
import re
import shutil
import subprocess  # nosec B404 - the runner starts this script per case with a fixed argv
import sys
import tempfile
import time
import types
from typing import Dict, List

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_goldens  # noqa: E402
import scoring  # noqa: E402
import stp_quality as sq  # noqa: E402

CONTAINER_REL = os.path.join("backendPipelines", "genAi", "cadStepAgent", "container")
DEFAULT_CONTAINER_DIR = os.path.join(os.path.dirname(HERE), "container")
ASSET_ID = "eval-asset"
INPUT_BUCKET = "local-input"
OUTPUT_BUCKET = "local-output"
MODEL_ID_ENV = "BEDROCK_MODEL_ID"
logger = logging.getLogger("run_offline")


# --------------------------------------------------------------------------- corpus

def load_corpus(path):
    with open(path, "r", encoding="utf-8") as fh:
        corpus = yaml.safe_load(fh)
    corpus["_dir"] = os.path.dirname(os.path.abspath(path))
    corpus["_by_id"] = {c["id"]: c for c in corpus["cases"]}
    return corpus


def resolve_input(case, corpus, out_root):
    """(local_path, file_name, source_label) of a modify case's input, or (None, '', '') for generate."""
    spec = case.get("input")
    if case["mode"] != "modify" or not spec:
        return None, "", ""
    if "fixture" in spec:
        path = os.path.join(corpus["_dir"], corpus["globals"].get("fixtures_dir", "fixtures"), spec["fixture"])
        return os.path.abspath(path), os.path.basename(path), f"fixture:{spec['fixture']}"
    if "case" in spec:
        src = spec["case"]
        run_json = os.path.join(out_root, src, "run.json")
        quality_json = os.path.join(out_root, src, "quality.json")
        if os.path.isfile(run_json) and os.path.isfile(quality_json):
            with open(run_json, encoding="utf-8") as fh:
                run = json.load(fh)
            with open(quality_json, encoding="utf-8") as fh:
                quality = json.load(fh)
            step = run.get("output_step")
            if step and os.path.isfile(step) and (quality.get("metrics") or {}).get("valid"):
                return step, os.path.basename(step), f"case:{src} ({quality.get('verdict')})"
        fallback = os.path.join(corpus["_dir"], spec["fallback"])
        return (os.path.abspath(fallback), os.path.basename(fallback),
                f"golden:{os.path.basename(fallback)} (fallback; {src} output unusable)")
    raise ValueError(f"case {case['id']}: unsupported input spec {spec}")


# --------------------------------------------------------------------------- stand-ins

class LocalS3:
    """The subset of the S3 client ``run.py`` calls, backed by local directories."""

    def __init__(self, inputs: Dict, out_root: str):
        self.inputs = inputs  # (bucket, key) -> local path
        self.out_root = out_root

    def _dest(self, bucket, key):
        dest = os.path.join(self.out_root, bucket, key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        return dest

    def download_file(self, bucket, key, local):
        shutil.copyfile(self.inputs[(bucket, key)], local)

    def upload_file(self, path, bucket, key):
        shutil.copyfile(path, self._dest(bucket, key))

    def put_object(self, Bucket, Key, Body, ContentType=None, **_):
        with open(self._dest(Bucket, Key), "wb") as fh:
            fh.write(Body if isinstance(Body, bytes) else str(Body).encode("utf-8"))


class NoSfn:
    """Never reached: the runner passes an empty task token, so run.py makes no callback."""

    def send_task_success(self, **kwargs):
        raise AssertionError("send_task_success must not be called without a task token")

    def send_task_failure(self, **kwargs):
        raise AssertionError("send_task_failure must not be called without a task token")

    def send_task_heartbeat(self, **kwargs):
        raise AssertionError("send_task_heartbeat must not be called without a task token")


class NoGuardrail:
    """Stand-in for ``guardrail.Guardrail``: nothing is screened and the model has no guardrail attached. The
    guardrail path is the deployed agent's, exercised by run_live.py."""

    guardrail_id = "evaluation-no-guardrail"
    version = "0"

    def screen(self, text, what="input"):
        return text


class ScriptedAgent:
    """A stand-in for the model that drives the real tools once: inspect the input, run one fixed script, call
    finish. ``one --fake-script <file>`` uses it to check the runner and scorers end to end with no Bedrock call."""

    def __init__(self, bound_tools, code):
        self.tools = {getattr(t, "tool_name", getattr(t, "__name__", str(t))): t for t in bound_tools}
        self.code = code
        self.messages = []

    def __call__(self, text):
        self.messages.append({"role": "user", "content": [{"text": str(text)}]})
        self.tools["inspect_input_step"]()
        result = json.loads(self.tools["run_cad_script"](code=self.code, intent="scripted check"))
        geometry = result.get("geometry") or {}
        self.tools["finish"](summary=f"scripted run; bbox {geometry.get('bounding_box_mm')}, volume {geometry.get('volume_mm3')}",
                            unresolved=[], status="succeeded" if result.get("ok") else "partial",
                            checks=["scripted: expected a valid solid - measured " + ("a valid solid - ok" if result.get("ok")
                                                                                    else "none - mismatch")])


class AgentRecorder:
    """Wraps the Strands agent so the runner can read its transcript and metrics afterwards."""

    def __init__(self, agent):
        self.agent = agent
        self.result = None
        self.error = None

    def __call__(self, text):
        if isinstance(text, list):  # guardContent-tagged turn; without a guardrail the model gets the plain text
            text = "\n".join(
                block.get("text") or block.get("guardContent", {}).get("text", {}).get("text", "") for block in text)
        try:
            self.result = self.agent(text)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            raise
        return self.result

    def transcript(self):
        try:
            return json.loads(json.dumps(self.agent.messages, default=str))
        except Exception as exc:  # pragma: no cover
            return [{"error": f"transcript unavailable: {exc}"}]

    def usage(self):
        out = {}
        try:
            m = self.result.metrics if self.result is not None else self.agent.event_loop_metrics
            out["accumulated_usage"] = dict(m.accumulated_usage)
            out["cycle_count"] = int(m.cycle_count)
            if self.result is not None:
                out["stop_reason"] = str(self.result.stop_reason)
        except Exception as exc:
            out["error"] = f"metrics unavailable: {exc}"
        return out


# --------------------------------------------------------------------------- one case

def build_definition(case, globals_, input_name, naming_module, model_id):
    mode = case["mode"]
    prompt = str(case["prompt"]).strip()
    input_file = None
    if mode == "modify":
        input_file = {"bucketName": INPUT_BUCKET, "objectKey": f"{ASSET_ID}/{input_name}",
                      "fileExtension": os.path.splitext(input_name)[1]}
    file_name = naming_module.resolve_output_filename(
        mode, input_name if mode == "modify" else "", "", "", case.get("designName", ""), prompt)
    return {
        "jobName": f"local-{case['id']}",
        "stageType": "CAD_STEP_AGENT",
        "mode": mode,
        "inputFile": input_file,
        "outputFiles": {"bucketName": OUTPUT_BUCKET, "objectDir": f"{ASSET_ID}/", "relativeSubdir": "",
                        "fileName": file_name},
        "outputMetadata": {"bucketName": OUTPUT_BUCKET, "objectDir": f"{ASSET_ID}-metadata/"},
        "outputResults": {"bucketName": OUTPUT_BUCKET, "objectDir": f"{ASSET_ID}-results/"},
        "auxiliary": {"bucketName": "", "objectDir": ""},
        "assetId": ASSET_ID,
        "databaseId": "eval-db",
        "agent": {
            "prompt": prompt,
            "allowInternetResearch": bool(case.get("research", False)),
            "modelProvider": globals_.get("modelProvider", "bedrock"),
            "modelId": model_id,
            "maxAttempts": int(globals_.get("maxAttempts", 4)),
            "maxRunSeconds": int(globals_.get("maxRunSeconds", 1200)),
            "scriptTimeoutSeconds": int(globals_.get("scriptTimeoutSeconds", 300)),
            # The honesty scorer reads the cadAgent* file metadata, which the templates leave off by default.
            "writeAgentMetadata": True,
        },
    }


def bedrock_region(globals_, environ=os.environ):
    """The Region the run uses: an exported ``AWS_REGION``, else the corpus's ``globals.region``, else
    us-east-1. It is set as ``AWS_REGION`` too, so the model and the container package agree on it."""
    return environ.setdefault("AWS_REGION", globals_.get("region") or "us-east-1")


def run_one(case_id, corpus_path, container_dir, out_root, model_id, fake_script=None):
    corpus = load_corpus(corpus_path)
    case = corpus["_by_id"][case_id]
    g = corpus["globals"]
    case_dir = os.path.join(out_root, case_id)
    if os.path.isdir(case_dir):
        shutil.rmtree(case_dir)
    os.makedirs(case_dir)
    work_dir = os.path.join(case_dir, "work")
    os.makedirs(work_dir)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    region = bedrock_region(g)
    os.environ[MODEL_ID_ENV] = model_id
    sys.path.insert(0, container_dir)
    from cad_step_agent import agent as agent_module  # noqa: E402
    from cad_step_agent import cad_step_naming, run as run_module, sandbox  # noqa: E402

    # Keep every attempt: route the run's temp root into the case dir and disable run.py's cleanup.
    tempfile.tempdir = work_dir
    run_module.shutil = types.SimpleNamespace(rmtree=lambda *a, **k: None)
    original_run_script = sandbox.run_script

    def persisting_run_script(code, wd, **kwargs):
        started = time.time()
        result = original_run_script(code, wd, **kwargs)
        with open(os.path.join(wd, "stdout.txt"), "w", encoding="utf-8") as fh:
            fh.write(result.output_tail)
        with open(os.path.join(wd, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"returncode": result.returncode, "timed_out": result.timed_out,
                       "output_exists": result.output_exists, "seconds": round(time.time() - started, 2)}, fh)
        return result
    sandbox.run_script = persisting_run_script

    input_path, input_name, input_source = resolve_input(case, corpus, out_root)
    definition = build_definition(case, g, input_name, cad_step_naming, model_id)
    with open(os.path.join(case_dir, "definition.json"), "w", encoding="utf-8") as fh:
        json.dump(definition, fh, indent=2)
    inputs = {(INPUT_BUCKET, definition["inputFile"]["objectKey"]): input_path} if input_path else {}
    s3 = LocalS3(inputs, os.path.join(case_dir, "s3"))
    holder: Dict[str, AgentRecorder] = {}

    def agent_factory(_model, bound_tools):
        if fake_script:
            with open(fake_script, encoding="utf-8") as fh:
                holder["rec"] = AgentRecorder(ScriptedAgent(bound_tools, fh.read()))
            return holder["rec"]
        from strands.models import BedrockModel  # noqa: WPS433 - only a model-driven run needs it

        model = BedrockModel(model_id=model_id, region_name=region,
                             temperature=agent_module.TEMPERATURE, max_tokens=agent_module.MAX_TOKENS)
        holder["rec"] = AgentRecorder(agent_module.build_agent(model, bound_tools))
        return holder["rec"]

    started = time.time()
    payload, error = None, None
    try:
        # Transport shape: constructPipeline returns "definition": [json.dumps(definition)].
        payload = run_module.run_job([json.dumps(definition)], "", s3=s3, sfn=NoSfn(), agent_factory=agent_factory,
                                     guardrail=NoGuardrail())
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:800]}"
    wall = time.time() - started

    out_key = run_module.output_key(definition)
    step_path = os.path.join(case_dir, "s3", OUTPUT_BUCKET, out_key)
    report_path = step_path + ".cad-agent-report.md"
    meta_key = run_module.metadata_key(definition)
    meta_path = os.path.join(case_dir, "s3", OUTPUT_BUCKET, meta_key) if meta_key else None
    attempts_dir = os.path.join(case_dir, "attempts")
    os.makedirs(attempts_dir, exist_ok=True)
    for d in sorted(glob.glob(os.path.join(work_dir, "cad-agent-*", "attempt-*"))):
        shutil.move(d, os.path.join(attempts_dir, os.path.basename(d)))
    attempts = []
    for d in sorted(glob.glob(os.path.join(attempts_dir, "attempt-*"))):
        info = {"dir": os.path.basename(d)}
        result_json = os.path.join(d, "result.json")
        if os.path.isfile(result_json):
            with open(result_json, encoding="utf-8") as fh:
                info.update(json.load(fh))
        step_out = os.path.join(d, "output.step")
        info["step_metrics"] = sq.key_metrics_text(sq.analyze_step(step_out)) if os.path.isfile(step_out) else "no output"
        attempts.append(info)
    rec = holder.get("rec")
    if rec is not None:
        with open(os.path.join(case_dir, "transcript.json"), "w", encoding="utf-8") as fh:
            json.dump(rec.transcript(), fh, indent=1, default=str)
    metadata = {}
    if meta_path and os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as fh:
            metadata = {e["metadataKey"]: e["metadataValue"] for e in json.load(fh).get("metadata", [])}

    run_record = {
        "case": case_id, "mode": case["mode"], "research": bool(case.get("research", False)),
        "model_label": f"bedrock:{model_id}", "status": (payload or {}).get("status", "failed"),
        "payload": payload, "error": error, "failure_cause": error, "wall_s": round(wall, 1),
        "attempt_count": len(attempts), "attempts": attempts,
        "output_step": step_path if os.path.isfile(step_path) else None,
        "report_path": report_path if os.path.isfile(report_path) else None,
        "metadata": metadata, "input_path": input_path, "input_source": input_source,
        "usage": rec.usage() if rec is not None else {}, "agent_error": rec.error if rec is not None else None,
    }
    with open(os.path.join(case_dir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(run_record, fh, indent=2, default=str)
    quality = scoring.score(case, run_record, run_record["output_step"], input_path, corpus["_dir"])
    quality["input_source"] = input_source
    with open(os.path.join(case_dir, "quality.json"), "w", encoding="utf-8") as fh:
        json.dump(quality, fh, indent=2, default=str)
    print(f"[{case_id}] status={run_record['status']} geometry={quality['verdict']} ({quality['passed']}/{quality['total']}) "
          f"honesty={quality['honesty_verdict']} golden={(quality.get('golden_diff') or {}).get('verdict', '-')} "
          f"attempts={len(attempts)} wall={wall:.0f}s usage={run_record['usage'].get('accumulated_usage')} err={error}")
    return 0


# --------------------------------------------------------------------------- waves

def run_wave(args, wave):
    if wave == "all":  # modify cases chain on generate outputs, so the waves must not overlap
        return run_wave(args, "generate") or run_wave(args, "modify")
    corpus = load_corpus(args.corpus)
    os.makedirs(args.out, exist_ok=True)
    make_goldens.build(os.path.join(corpus["_dir"], corpus["globals"].get("golden_dir", "goldens")))
    wanted = set(args.cases.split(",")) if args.cases else None
    selected = []
    for case in corpus["cases"]:
        if case["mode"] != wave or (wanted and case["id"] not in wanted):
            continue
        if args.skip_existing and os.path.isfile(os.path.join(args.out, case["id"], "quality.json")):
            print(f"[{case['id']}] skip (exists)")
            continue
        selected.append(case["id"])
    if not selected:
        return 0
    per_case_timeout = int(corpus["globals"].get("maxRunSeconds", 1200)) + 180

    def launch(case_id):
        log_path = os.path.join(args.out, f"{case_id}.stdout.log")
        cmd = [args.python, os.path.abspath(__file__), "one", "--corpus", args.corpus, "--container", args.container,
               "--out", args.out, "--case", case_id, "--model-id", args.model_id]
        started = time.time()
        with open(log_path, "w", encoding="utf-8") as log:
            try:
                # Argument list, no shell: this script's own path and its parsed arguments.
                proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=per_case_timeout, check=False)  # nosec B603 - argument list, no shell
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                rc = "timeout"
        return case_id, rc, round(time.time() - started)

    print(f"running {len(selected)} case(s) with {args.workers} worker(s): {selected}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for case_id, rc, secs in pool.map(launch, selected):
            print(f"[{case_id}] rc={rc} {secs}s", flush=True)
    return 0


# --------------------------------------------------------------------------- report and rescore

def _load(out_root, case_id):
    def read(name):
        path = os.path.join(out_root, case_id, name)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return read("run.json"), read("quality.json")


def _tokens(run):
    usage = ((run or {}).get("usage") or {}).get("accumulated_usage") or {}
    return int(usage.get("inputTokens", 0) or 0), int(usage.get("outputTokens", 0) or 0)


REPORT_HEADER = ("| case | mode | geometry (checks) | honesty | golden | attempts | wall s | key metrics | failed checks |\n"
                 "|---|---|---|---|---|---|---|---|---|")


def render_report(corpus_path, out_root, title, compare_root=None):
    corpus = load_corpus(corpus_path)
    lines = [f"# {title}", "", f"Run directory: `{out_root}`  ", f"Corpus: `{corpus_path}` ({len(corpus['cases'])} cases)  ",
             f"Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", "", REPORT_HEADER]
    totals = {"runs": 0, "in": 0, "out": 0, "pass": 0, "false_success": 0}
    for case in corpus["cases"]:
        run, quality = _load(out_root, case["id"])
        if not quality:
            lines.append(f"| {case['id']} | {case['mode']} | (not run) | | | | | | |")
            continue
        totals["runs"] += 1
        tokens_in, tokens_out = _tokens(run)
        totals["in"] += tokens_in
        totals["out"] += tokens_out
        totals["pass"] += quality["verdict"] == sq.VERDICT_PASS
        totals["false_success"] += quality.get("honesty_verdict") == "FALSE-SUCCESS"
        reason = (quality.get("reason") or "")[:200].replace("|", "\\|")
        metrics = sq.key_metrics_text(quality.get("metrics") or {}).replace("|", "\\|")
        golden = (quality.get("golden_diff") or {})
        golden_text = golden.get("verdict", "") + (f" ({', '.join(golden.get('failed_fields') or [])})"
                                                    if golden.get("failed_fields") else "")
        lines.append(f"| {case['id']} | {case['mode']} | **{quality['verdict']}** ({quality['passed']}/{quality['total']}) | "
                     f"{quality.get('honesty_verdict', '')} | {golden_text} | {(run or {}).get('attempt_count', '')} | "
                     f"{round((run or {}).get('wall_s') or 0)} | {metrics} | {reason} |")
    lines += ["", f"{totals['runs']} runs: geometry PASS {totals['pass']}, false successes {totals['false_success']}, "
              f"{totals['in']:,} input / {totals['out']:,} output tokens.", ""]
    if compare_root:
        lines += ["## Against the baseline", "", "| case | baseline | candidate | honesty b -> c |", "|---|---|---|---|"]
        for case in corpus["cases"]:
            _, base = _load(compare_root, case["id"])
            _, cand = _load(out_root, case["id"])
            if base and cand:
                lines.append(f"| {case['id']} | {base['verdict']} ({base['passed']}/{base['total']}) | "
                             f"**{cand['verdict']}** ({cand['passed']}/{cand['total']}) | "
                             f"{base.get('honesty_verdict')} -> {cand.get('honesty_verdict')} |")
        lines.append("")
    return "\n".join(lines)


def rescore(corpus_path, out_root, cases_filter=None):
    """Re-derive quality.json for recorded runs (after a corpus or scorer change) without running the agent."""
    corpus = load_corpus(corpus_path)
    for case in corpus["cases"]:
        if cases_filter and case["id"] not in cases_filter:
            continue
        run, old = _load(out_root, case["id"])
        if not run:
            continue
        quality = scoring.score(case, run, run.get("output_step"), run.get("input_path"), corpus["_dir"])
        quality["input_source"] = run.get("input_source")
        with open(os.path.join(out_root, case["id"], "quality.json"), "w", encoding="utf-8") as fh:
            json.dump(quality, fh, indent=2, default=str)
        print(f"[{case['id']}] {old['verdict'] if old else '?'} -> {quality['verdict']} ({quality['passed']}/{quality['total']}) "
              f"honesty={quality['honesty_verdict']} {quality['reason'][:160]}")
    return 0


def transcript_digest(out_root, case_id, max_chars=900):
    """A compact, quotable digest of one case: per attempt the intent, the result, and the model's text."""
    path = os.path.join(out_root, case_id, "transcript.json")
    if not os.path.isfile(path):
        return f"[{case_id}] no transcript"
    with open(path, encoding="utf-8") as fh:
        messages = json.load(fh)
    out = [f"### {case_id}"]
    for message in messages:
        for block in message.get("content", []):
            if "text" in block and message.get("role") == "assistant":
                text = re.sub(r"\s+", " ", block["text"]).strip()
                if text:
                    out.append(f"- assistant: {text[:max_chars]}")
            elif "toolUse" in block:
                use = block["toolUse"]
                inputs = use.get("input", {})
                if use.get("name") == "run_cad_script":
                    out.append(f"- run_cad_script intent=\"{inputs.get('intent', '')[:200]}\" "
                               f"({len(inputs.get('code', '').splitlines())} lines)")
                elif use.get("name") == "finish":
                    out.append(f"- finish status={inputs.get('status')} unresolved={inputs.get('unresolved')} "
                               f"summary=\"{str(inputs.get('summary', ''))[:max_chars]}\"")
                else:
                    out.append(f"- {use.get('name')} {json.dumps(inputs)[:300]}")
    return "\n".join(out)


# --------------------------------------------------------------------------- cli

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("one", "run"):
        p = sub.add_parser(name)
        p.add_argument("--corpus", default=os.path.join(HERE, "corpus.yaml"))
        p.add_argument("--out", required=True)
        p.add_argument("--container", help="the container package directory to run (default: this repository's)")
        p.add_argument("--snapshot", help="a repository checkout or git-archive extraction whose container to run")
        p.add_argument("--model-id", default=os.environ.get(MODEL_ID_ENV, ""))
    sub.choices["one"].add_argument("--case", required=True)
    sub.choices["one"].add_argument("--fake-script", help="drive the tools with this CadQuery script, no model call")
    run_p = sub.choices["run"]
    run_p.add_argument("--wave", choices=["generate", "modify", "all"], default="all")
    run_p.add_argument("--cases", help="comma-separated case ids")
    run_p.add_argument("--workers", type=int, default=3)
    run_p.add_argument("--skip-existing", action="store_true")
    run_p.add_argument("--python", default=sys.executable)
    rep = sub.add_parser("report")
    rep.add_argument("--corpus", default=os.path.join(HERE, "corpus.yaml"))
    rep.add_argument("--out", required=True)
    rep.add_argument("--md", required=True)
    rep.add_argument("--title", default="CAD STEP agent evaluation")
    rep.add_argument("--compare", help="baseline run directory")
    res = sub.add_parser("rescore")
    res.add_argument("--corpus", default=os.path.join(HERE, "corpus.yaml"))
    res.add_argument("--out", required=True)
    res.add_argument("--cases")
    dig = sub.add_parser("digest")
    dig.add_argument("--out", required=True)
    dig.add_argument("--cases", required=True)
    args = parser.parse_args(argv)

    if args.cmd in ("one", "run"):
        if not args.model_id and not getattr(args, "fake_script", None):
            parser.error(f"set --model-id or {MODEL_ID_ENV}")
        args.corpus, args.out = os.path.abspath(args.corpus), os.path.abspath(args.out)
        args.container = os.path.abspath(args.container or (os.path.join(args.snapshot, CONTAINER_REL)
                                                            if args.snapshot else DEFAULT_CONTAINER_DIR))
        if args.cmd == "one":
            return run_one(args.case, args.corpus, args.container, args.out, args.model_id or "scripted",
                           os.path.abspath(args.fake_script) if args.fake_script else None)
        return run_wave(args, args.wave)
    if args.cmd == "report":
        text = render_report(os.path.abspath(args.corpus), os.path.abspath(args.out), args.title,
                             os.path.abspath(args.compare) if args.compare else None)
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(text)
        return 0
    if args.cmd == "rescore":
        return rescore(os.path.abspath(args.corpus), os.path.abspath(args.out),
                       set(args.cases.split(",")) if args.cases else None)
    for case_id in args.cases.split(","):
        print(transcript_digest(os.path.abspath(args.out), case_id))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
