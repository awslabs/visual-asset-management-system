# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Intent judge of the GenAI CAD STEP agent evaluation harness: a rubric-graded LLM judge on Amazon Bedrock.

The geometry scorer measures what a file IS; the judge grades whether that is what the instruction MEANT:
for each of the case's ``intent_rubric`` criteria (plain sentences such as "four holes near the board's
58 x 49 mm pattern") it gives a 1-5 score, then an overall 1-5, whether the agent's report is consistent with
the measured geometry, and a rationale of at most 600 characters. A run PASSES the intent axis at overall >= 4.

The judge sees a bounded view of one run: the instruction, the agent's status and report (without the
per-attempt script output), the measured geometry, and the geometry and honesty verdicts. It is told that the
measured figures are facts and that it grades intent only, never the measurement. One Converse call per run,
temperature 0, model from ``--model-id`` else ``BEDROCK_MODEL_ID`` (the deployment setting the agent uses). An
answer that is not the requested JSON is a scoring ERROR, never a PASS. Every call records its tokens and an
estimated cost at the ``--price-in`` / ``--price-out`` rates (USD per million tokens). ``--out`` is rewritten
after every call, so a run that stops part-way keeps the verdicts already paid for; a call that raises is
recorded as an ERROR for its case and ends the run.

Usage (opt-in; it calls Amazon Bedrock):

    python llm_judge.py --corpus corpus.yaml --run-dir <runner output dir> [--cases G1,FM4] --out judge.json
    python llm_judge.py --corpus corpus.yaml --run FG1=<case dir> --run FM9=<case dir> --out judge.json
"""

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional

import boto3
import yaml
from botocore.config import Config

import honesty

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

PASS_THRESHOLD = 4
SCORE_MIN, SCORE_MAX = 1, 5
RATIONALE_MAX_CHARS = 600
PROMPT_MAX_CHARS = 4000
REPORT_MAX_CHARS = 8000
GEOMETRY_MAX_CHARS = 6000
MAX_TOKENS = 1024
# Estimate only: list price of an Anthropic Claude Sonnet model on Amazon Bedrock, USD per million tokens.
DEFAULT_PRICE_IN = 3.0
DEFAULT_PRICE_OUT = 15.0
MODEL_ID_ENV = "BEDROCK_MODEL_ID"
VERDICT_PASS, VERDICT_FAIL, VERDICT_ERROR = "PASS", "FAIL", "ERROR"

SYSTEM_PROMPT = """You are a senior mechanical design reviewer grading the output of a CAD agent against the DESIGN INTENT of
the instruction it was given.

You receive: the instruction; the agent's status and report; the MEASURED geometry of the STEP file it produced
(measured by a geometry kernel - these figures are facts); the verdicts of an automated geometry scorer and of an
honesty check; and a rubric of intent criteria.

Rules:
- Grade INTENT only: would a competent engineer accept this part as what the instruction asked for? Do not re-measure
  or re-grade figures the scorer measured; take the measured geometry as true and judge whether it serves the intent.
- Score every rubric criterion from 1 (not met) to 5 (fully met), in the order given. A criterion the output cannot
  meet because no file was produced scores 1.
- "overall" is your 1-5 judgment of the whole output against the intent, not an average.
- "report_consistent_with_geometry" is false when the report claims figures or features the measured geometry
  contradicts (a different size, a hole count that does not match, a feature that is absent); true otherwise,
  including for a run that produced no file and says so.
- "rationale" is at most 600 characters: the decisive reasons.

Answer with ONE JSON object and nothing else:
{"criteria": [{"criterion": "<the rubric text>", "score": <1-5>}], "overall": <1-5>,
 "report_consistent_with_geometry": <true|false>, "rationale": "<at most 600 characters>"}"""


class JudgeError(ValueError):
    """The judge's answer is not the requested JSON."""


def _clip(text, limit):
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _report_without_attempts(text: str) -> str:
    """The report up to its per-attempt section: the script output there is noise for an intent grade."""
    return (text or "").split("\n## Attempts", 1)[0].strip()


def geometry_view(metrics: Optional[Dict]) -> Dict:
    """The measured figures the judge needs, without per-face detail."""
    m = metrics or {}
    if not m.get("import_ok"):
        return {"produced": False, "error": m.get("error") or "no output STEP file"}
    hole_keys = ("diameter", "axis_index", "through", "through_with_counterbore", "length", "pos2d_from_bbox_min")
    return {
        "produced": True,
        "valid": m.get("valid"), "solid_count": m.get("solid_count"),
        "extents_mm": m.get("extents"), "extents_sorted_mm": m.get("extents_sorted"),
        "volume_mm3": m.get("volume"), "declared_length_unit": m.get("declared_length_unit"),
        "holes": [{k: h.get(k) for k in hole_keys} for h in (m.get("holes") or [])],
        "counterbores": m.get("counterbores") or [],
        "bosses": [{"radius": b.get("radius"), "length": b.get("length")} for b in (m.get("bosses") or [])],
        "fillets": m.get("fillets") or [],
        "partial_round_cuts": len(m.get("partial_concave_cylinders") or []),
        "solids_detail": [{"volume": s.get("volume"), "extents_sorted": s.get("extents_sorted")}
                          for s in (m.get("solids_detail") or [])][:20],
    }


def build_messages(case: Dict, run: Dict, quality: Dict, honesty_result: Optional[tuple] = None):
    """(system, user text) for one judged run. Every part is bounded."""
    rubric = [str(c) for c in (case.get("intent_rubric") or [])]
    if not rubric:
        raise JudgeError(f"case {case.get('id')} has no intent_rubric")
    status = honesty.normalized_status(run)
    hv, hreason = honesty_result or honesty.honesty_verdict(case, run, quality)
    report = _report_without_attempts(honesty.report_text(run))
    failure = run.get("failure_cause") or run.get("error") or ""
    sections = [
        "## Instruction", _clip(str(case.get("prompt", "")).strip(), PROMPT_MAX_CHARS), "",
        f"## Agent status\n{status or 'unknown'}" + (f" - {_clip(failure, 400)}" if failure else ""), "",
        "## Agent report", _clip(report, REPORT_MAX_CHARS) if report else "(no report: the run produced no file)", "",
        "## Measured geometry (facts)",
        _clip(json.dumps(geometry_view(quality.get("metrics")), sort_keys=True), GEOMETRY_MAX_CHARS), "",
        "## Automated verdicts",
        f"geometry scorer: {quality.get('verdict')} ({quality.get('passed')}/{quality.get('total')} checks)"
        + (f"; failed: {_clip(quality.get('reason'), 600)}" if quality.get("reason") else ""),
        f"honesty check: {hv} - {_clip(hreason, 300)}", "",
        "## Intent rubric (score each, in order)",
    ] + [f"{i}. {c}" for i, c in enumerate(rubric, 1)]
    return SYSTEM_PROMPT, "\n".join(sections)


def _json_object(text: str) -> Dict:
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise JudgeError("the answer holds no JSON object")
    try:
        value = json.loads(stripped[start:end + 1])
    except json.JSONDecodeError as exc:
        raise JudgeError(f"the answer is not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise JudgeError("the answer is not a JSON object")
    return value


def _score(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value) \
            or not SCORE_MIN <= int(value) <= SCORE_MAX:
        raise JudgeError(f"{what} is not an integer from {SCORE_MIN} to {SCORE_MAX}: {value!r}")
    return int(value)


def parse_answer(text: str, rubric: List[str]) -> Dict:
    """The validated judgment; raises JudgeError on anything but the requested shape."""
    data = _json_object(text)
    for key in ("criteria", "overall", "report_consistent_with_geometry", "rationale"):
        if key not in data:
            raise JudgeError(f"the answer is missing '{key}'")
    criteria = data["criteria"]
    if not isinstance(criteria, list) or len(criteria) != len(rubric):
        raise JudgeError(f"the answer scores {len(criteria) if isinstance(criteria, list) else 'no'} criteria, "
                         f"the rubric has {len(rubric)}")
    scores = []
    for i, (entry, criterion) in enumerate(zip(criteria, rubric), 1):
        if not isinstance(entry, dict) or "score" not in entry:
            raise JudgeError(f"criterion {i} has no score")
        scores.append({"criterion": criterion, "score": _score(entry["score"], f"criterion {i} score")})
    if not isinstance(data["report_consistent_with_geometry"], bool):
        raise JudgeError("report_consistent_with_geometry is not a boolean")
    if not isinstance(data["rationale"], str):
        raise JudgeError("rationale is not a string")
    overall = _score(data["overall"], "overall")
    return {"criteria": scores, "overall": overall,
            "report_consistent_with_geometry": data["report_consistent_with_geometry"],
            "rationale": _clip(data["rationale"].strip(), RATIONALE_MAX_CHARS),
            "verdict": VERDICT_PASS if overall >= PASS_THRESHOLD else VERDICT_FAIL}


def estimated_cost(input_tokens: int, output_tokens: int, price_in: float, price_out: float) -> float:
    return round(input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out, 6)


def judge_run(client, model_id: str, case: Dict, run: Dict, quality: Dict, price_in=DEFAULT_PRICE_IN,
              price_out=DEFAULT_PRICE_OUT) -> Dict:
    """One Converse call; returns the judgment (verdict PASS / FAIL / ERROR) with tokens and cost."""
    system, user = build_messages(case, run, quality)
    response = client.converse(
        modelId=model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"temperature": 0, "maxTokens": MAX_TOKENS},
    )
    usage = response.get("usage") or {}
    tokens_in, tokens_out = int(usage.get("inputTokens", 0) or 0), int(usage.get("outputTokens", 0) or 0)
    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    accounting = {"case": case.get("id"), "model": model_id, "input_tokens": tokens_in, "output_tokens": tokens_out,
                  "estimated_cost_usd": estimated_cost(tokens_in, tokens_out, price_in, price_out),
                  "stop_reason": response.get("stopReason")}
    try:
        return {**parse_answer(text, [str(c) for c in case["intent_rubric"]]), **accounting}
    except JudgeError as exc:
        return {"verdict": VERDICT_ERROR, "error": str(exc), "raw_answer": _clip(text, 2000), **accounting}


def bedrock_client(region: Optional[str]):
    return boto3.client("bedrock-runtime", region_name=region, config=retry_config)


def write_results(path, model_id, spent, results):
    """Write the judge's output to ``path`` through a temporary file beside it, so a reader never sees a
    half-written file."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"model": model_id, "estimated_cost_usd": round(spent, 6), "results": results}, fh, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _case_runs(args, corpus) -> List[tuple]:
    by_id = {c["id"]: c for c in corpus["cases"]}
    pairs = []
    for spec in args.run or []:
        case_id, _, path = spec.partition("=")
        pairs.append((by_id[case_id], path))
    if args.run_dir:
        wanted = set(args.cases.split(",")) if args.cases else None
        for case in corpus["cases"]:
            path = os.path.join(args.run_dir, case["id"])
            if (wanted is None or case["id"] in wanted) and os.path.isdir(path):
                pairs.append((case, path))
    return pairs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--run-dir", help="a runner output directory with one sub-directory per case id")
    parser.add_argument("--cases", help="comma-separated case ids to judge from --run-dir")
    parser.add_argument("--run", action="append", metavar="CASE=DIR", help="judge one recorded case directory")
    parser.add_argument("--model-id", default=os.environ.get(MODEL_ID_ENV, ""))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"))
    parser.add_argument("--price-in", type=float, default=DEFAULT_PRICE_IN)
    parser.add_argument("--price-out", type=float, default=DEFAULT_PRICE_OUT)
    parser.add_argument("--max-cost", type=float, default=2.0,
                        help="stop before a call once this many USD are spent; the call that crosses the cap "
                             "still runs, so the total can exceed it by one call (about USD 0.03 at the default "
                             "prices)")
    parser.add_argument("--out", required=True, help="the verdicts as JSON, rewritten after every call")
    args = parser.parse_args(argv)
    if not args.model_id:
        parser.error(f"set --model-id or {MODEL_ID_ENV}")
    with open(args.corpus, encoding="utf-8") as fh:
        corpus = yaml.safe_load(fh)
    client = bedrock_client(args.region)
    results, spent = [], 0.0
    for case, case_dir in _case_runs(args, corpus):
        if spent >= args.max_cost:
            print(f"cost cap {args.max_cost} USD reached; {case['id']} not judged")
            break
        run, quality = honesty.load_case_dir(case_dir)
        try:
            result = judge_run(client, args.model_id, case, run, quality, args.price_in, args.price_out)
        except Exception as exc:  # throttling past the retries, access denied: the verdicts so far are kept
            error = f"{type(exc).__name__}: {_clip(str(exc), 500)}"
            results.append({"case": case.get("id"), "case_dir": case_dir, "verdict": VERDICT_ERROR, "error": error})
            write_results(args.out, args.model_id, spent, results)
            print(f"[{case['id']}] stopped: {error}; {len(results) - 1} run(s) judged, estimated cost "
                  f"{spent:.4f} USD -> {args.out}")
            return 1
        result["case_dir"] = case_dir
        spent += result["estimated_cost_usd"]
        results.append(result)
        write_results(args.out, args.model_id, spent, results)
        print(f"[{case['id']}] {result['verdict']} overall={result.get('overall')} "
              f"consistent={result.get('report_consistent_with_geometry')} tokens={result['input_tokens']}/"
              f"{result['output_tokens']} ~${result['estimated_cost_usd']:.4f} {result.get('error', '')}")
    write_results(args.out, args.model_id, spent, results)
    print(f"{len(results)} run(s) judged, estimated cost {spent:.4f} USD -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
