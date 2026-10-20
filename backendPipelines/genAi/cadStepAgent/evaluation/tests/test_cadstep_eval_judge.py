# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The intent judge (llm_judge.py) with a stubbed Bedrock client: prompt assembly and its bounds, and the
defensive parsing of the answer. No network."""

import json
import os
import sys

import pytest

EVALUATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EVALUATION_DIR not in sys.path:
    sys.path.insert(0, EVALUATION_DIR)
import llm_judge as lj  # noqa: E402

RUBRIC = ["Four M2.5 holes near the 58 x 49 mm pattern.", "A mount for a 2020 extrusion."]
CASE = {"id": "FG1", "prompt": "a bracket to hold a Raspberry Pi 4 to a 2020 aluminium extrusion",
        "honesty_expect": "partial-with-unresolved-named-figures", "intent_rubric": RUBRIC}
REPORT = ("# CAD STEP agent run abc\n\n- Status: **partial**\n\n## Summary\n\nBracket 70 x 60 x 3 mm.\n\n"
          "## Attempts\n\n### Attempt 1 - ok\n\n```text\n" + "SCRIPT OUTPUT " * 2000 + "\n```\n")
RUN = {"status": "partial", "report_text": REPORT,
       "metadata": {"cadAgentSummary": "Bracket 70 x 60 x 3 mm.", "cadAgentUnresolved": json.dumps(["Pi 4 pattern"])}}
QUALITY = {"verdict": "PASS", "passed": 5, "total": 5, "reason": "",
           "metrics": {"import_ok": True, "valid": True, "solid_count": 1, "extents": [70, 60, 3],
                       "extents_sorted": [3, 60, 70], "volume": 11000.0,
                       "holes": [{"diameter": 2.75, "axis_index": 2, "through": True, "pos2d_from_bbox_min": [6, 5],
                                  "center": [1, 2, 3], "faces": 2}] * 4,
                       "face_count": 40}}


def _answer(**overrides):
    body = {"criteria": [{"criterion": RUBRIC[0], "score": 5}, {"criterion": RUBRIC[1], "score": 4}],
            "overall": 4, "report_consistent_with_geometry": True, "rationale": "Pattern right; extrusion mount present."}
    body.update(overrides)
    return json.dumps(body)


class _StubClient:
    def __init__(self, text, usage=None):
        self.text, self.calls = text, []
        self.usage = usage or {"inputTokens": 2000, "outputTokens": 150}

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"role": "assistant", "content": [{"text": self.text}]}},
                "usage": self.usage, "stopReason": "end_turn"}


def test_the_prompt_carries_the_bounded_run_view_and_the_rubric_in_order():
    system, user = lj.build_messages(CASE, RUN, QUALITY)
    assert "Grade INTENT only" in system and "Do not re-measure" in system
    assert CASE["prompt"] in user and "partial" in user
    assert user.index("1. " + RUBRIC[0]) < user.index("2. " + RUBRIC[1])
    assert "SCRIPT OUTPUT" not in user, "the per-attempt output stays out of the judge's view"
    assert '"solid_count": 1' in user and '"faces"' not in user
    assert "geometry scorer: PASS (5/5 checks)" in user and "honesty check: PASS" in user
    long_case = dict(CASE, prompt="p" * 50000)
    long_run = dict(RUN, report_text="r" * 50000)
    _, bounded = lj.build_messages(long_case, long_run, QUALITY)
    assert len(bounded) < lj.PROMPT_MAX_CHARS + lj.REPORT_MAX_CHARS + lj.GEOMETRY_MAX_CHARS + 3000


def test_a_run_without_output_is_presented_as_such():
    refusal = {"status": "unknown", "pipeline_status": "failed", "metadata": {},
               "failure_cause": "No valid STEP file was produced after 4 attempt(s)"}
    _, user = lj.build_messages(dict(CASE, honesty_expect="partial-or-failed-or-declared-approximation"), refusal,
                                {"verdict": "FAIL", "passed": 0, "total": 0, "metrics": {}})
    assert "failed - No valid STEP file" in user and "(no report: the run produced no file)" in user
    assert '"produced": false' in user


def test_a_case_without_a_rubric_cannot_be_judged():
    with pytest.raises(lj.JudgeError):
        lj.build_messages(dict(CASE, intent_rubric=[]), RUN, QUALITY)


def test_a_valid_answer_is_parsed_and_passes_at_four():
    client = _StubClient("```json\n" + _answer() + "\n```")
    result = lj.judge_run(client, "model-under-test", CASE, RUN, QUALITY, price_in=3.0, price_out=15.0)
    assert result["verdict"] == lj.VERDICT_PASS and result["overall"] == 4
    assert [c["score"] for c in result["criteria"]] == [5, 4]
    assert result["input_tokens"] == 2000 and result["output_tokens"] == 150
    assert result["estimated_cost_usd"] == pytest.approx(2000 / 1e6 * 3 + 150 / 1e6 * 15)
    call = client.calls[0]
    assert call["modelId"] == "model-under-test" and call["inferenceConfig"]["temperature"] == 0
    assert call["inferenceConfig"]["maxTokens"] == lj.MAX_TOKENS


def test_an_overall_below_four_fails_and_a_long_rationale_is_clipped():
    result = lj.parse_answer(_answer(overall=3, rationale="x" * 2000), RUBRIC)
    assert result["verdict"] == lj.VERDICT_FAIL and len(result["rationale"]) == lj.RATIONALE_MAX_CHARS


@pytest.mark.parametrize("text,why", [
    ("I think it is a good bracket.", "no JSON object"),
    ("{\"criteria\": [", "no JSON object"),
    ('{"criteria": [], "overall": 4, "report_consistent_with_geometry": true, "rationale": "r"}', "criteria"),
    (_answer(overall=7), "overall"),
    (_answer(overall=True), "overall"),
    (_answer(report_consistent_with_geometry="yes"), "boolean"),
    (json.dumps({"criteria": [{"score": 5}, {"score": 5}], "overall": 5, "rationale": "r"}),
     "report_consistent_with_geometry"),
    (json.dumps({"criteria": [{"score": 5}, {"note": "?"}], "overall": 5, "report_consistent_with_geometry": True,
                 "rationale": "r"}), "criterion 2"),
])
def test_a_malformed_answer_is_a_scoring_error_never_a_pass(text, why):
    with pytest.raises(lj.JudgeError, match=why):
        lj.parse_answer(text, RUBRIC)
    result = lj.judge_run(_StubClient(text), "m", CASE, RUN, QUALITY)
    assert result["verdict"] == lj.VERDICT_ERROR and why.split()[0] in result["error"]
    assert "overall" not in result


def test_the_model_id_comes_from_the_deployment_setting(monkeypatch, tmp_path):
    monkeypatch.delenv(lj.MODEL_ID_ENV, raising=False)
    with pytest.raises(SystemExit):
        lj.main(["--corpus", os.path.join(EVALUATION_DIR, "corpus.yaml"), "--out", str(tmp_path / "j.json")])


class _FailsOnThirdCall(_StubClient):
    def __init__(self, text, error):
        super().__init__(text)
        self.error = error

    def converse(self, **kwargs):
        if len(self.calls) == 2:
            raise self.error
        return super().converse(**kwargs)


@pytest.mark.parametrize("error", [RuntimeError("ThrottlingException: Rate exceeded"), KeyboardInterrupt()])
def test_verdicts_already_paid_for_are_on_disk_when_a_later_call_fails(monkeypatch, tmp_path, error):
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text(json.dumps({"cases": [dict(CASE, id=case_id) for case_id in ("A", "B", "C")]}))
    runs = []
    for case_id in ("A", "B", "C"):
        case_dir = tmp_path / case_id
        case_dir.mkdir()
        (case_dir / "run.json").write_text(json.dumps(RUN))
        (case_dir / "quality.json").write_text(json.dumps(QUALITY))
        runs += ["--run", f"{case_id}={case_dir}"]
    client = _FailsOnThirdCall(_answer(), error)
    monkeypatch.setattr(lj, "bedrock_client", lambda region: client)
    out = tmp_path / "judge.json"
    argv = ["--corpus", str(corpus), "--model-id", "m", "--out", str(out)] + runs
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            lj.main(argv)
    else:
        assert lj.main(argv) == 1
    written = json.loads(out.read_text())
    paid = written["results"][:2]
    assert [r["verdict"] for r in paid] == [lj.VERDICT_PASS, lj.VERDICT_PASS] and len(client.calls) == 2
    assert written["estimated_cost_usd"] == round(sum(r["estimated_cost_usd"] for r in paid), 6) > 0
    if isinstance(error, KeyboardInterrupt):
        assert len(written["results"]) == 2
    else:
        assert written["results"][2]["verdict"] == lj.VERDICT_ERROR
        assert "ThrottlingException" in written["results"][2]["error"]
    assert not os.path.exists(f"{out}.tmp")
