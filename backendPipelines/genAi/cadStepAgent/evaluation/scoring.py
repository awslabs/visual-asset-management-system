# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Score one recorded run on the harness's offline axes: geometry (stp_quality), honesty (honesty) and, for a
case with a golden, the golden diff (golden_diff). Shared by run_offline.py and run_live.py. Needs CadQuery.

The intent axis (llm_judge.py) is separate and opt-in: it calls Amazon Bedrock.
"""

import os
from typing import Dict, Optional

import golden_diff
import honesty
import stp_quality as sq

HERE = os.path.dirname(os.path.abspath(__file__))


def score(case: Dict, run: Dict, step_path: Optional[str], input_path: Optional[str],
          evaluation_dir: str = HERE) -> Dict:
    """The ``quality.json`` body for one run: the geometry result with the report checks folded in, the
    honesty verdict, and the golden diff when the case names a golden and the run produced a file."""
    if step_path and os.path.isfile(step_path):
        quality = sq.evaluate(step_path, case.get("expect"), input_path)
    else:
        quality = {"verdict": sq.VERDICT_FAIL, "passed": 0, "total": 0, "checks": [], "reason": "no output STEP",
                   "metrics": {}}
    if quality.get("checks"):
        quality = honesty.report_checks(quality, case, run)
    verdict, reason = honesty.honesty_verdict(case, run, quality)
    quality.update({"case": case["id"], "honesty_expect": case.get("honesty_expect", "succeeded"),
                    "honesty_verdict": verdict, "honesty_reason": reason})
    golden = case.get("golden")
    if golden and step_path and os.path.isfile(step_path) and (quality.get("metrics") or {}).get("solid_count"):
        diff = golden_diff.diff_files(step_path, os.path.join(evaluation_dir, golden), case.get("golden_tol"))
        quality["golden_diff"] = {k: diff[k] for k in ("verdict", "failed_fields", "skipped", "checks")}
        quality["golden_diff"]["golden"] = golden
    return quality
