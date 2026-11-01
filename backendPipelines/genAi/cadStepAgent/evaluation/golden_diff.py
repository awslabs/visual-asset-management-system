# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Golden diffs for the GenAI CAD STEP agent evaluation harness: an output STEP against a reviewed golden STEP,
compared geometrically and never byte for byte (two correct exports of one part differ in every byte).

Compared, each against a tolerance (``DEFAULT_TOL``, overridden per case by the corpus ``golden_tol``):

- ``solids`` (exact), ``faces`` and ``edges`` (within ``count_pct`` percent of the golden's, at least 0);
- ``extents_sorted`` (each extent within ``dim`` mm) and ``volume`` (within ``volume_pct`` percent);
- the hole feature set: ``holes.count``, ``holes.radii`` (sorted, pairwise within ``radius`` mm),
  ``holes.axes`` and ``holes.through`` (the multiset of hole axes and of through/blind flags);
- ``fillets.radii``: the set of fillet radii (by kind) within ``radius`` mm, and their face count;
- ``symmetric_difference``: when both files hold one solid and their unsorted extents agree, the volume of
  (output - golden) + (golden - output) from Open CASCADE boolean cuts after the output is translated onto
  the golden's bounding-box minimum corner, within ``symdiff_pct`` percent of the golden's volume. A part
  in another orientation skips this check (recorded under ``skipped``) rather than failing it.

``diff_files`` returns ``{"verdict": "PASS" | "FAIL", "checks": [...], "failed_fields": [...], "skipped": [...]}``;
every failed check names its field. CLI: ``golden_diff.py <output.step> <golden.step> [--tol JSON] [--out f]``.
"""

import argparse
import json
import math
import sys
from collections import Counter
from typing import Dict, List, Optional

import stp_quality as sq

DEFAULT_TOL = {
    "dim": 0.5,          # mm, sorted extents
    "radius": 0.25,      # mm, hole and fillet radii
    "volume_pct": 1.0,   # percent of the golden's volume
    "count_pct": 10.0,   # percent of the golden's face / edge count
    "symdiff_pct": 0.5,  # percent of the golden's volume
}
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"


def _check(field, ok, detail):
    return {"field": field, "ok": bool(ok), "detail": detail}


def _hole_through(hole):
    return bool(hole.get("through") or hole.get("through_with_counterbore"))


def compare_metrics(out: Dict, gold: Dict, tol: Optional[Dict] = None) -> Dict:
    """The checks for two ``stp_quality.analyze_step`` results; no geometry kernel needed."""
    tol = {**DEFAULT_TOL, **(tol or {})}
    checks: List[Dict] = []
    for name, metrics in (("output", out), ("golden", gold)):
        if not (metrics.get("import_ok") and metrics.get("solid_count")):
            reason = metrics.get("error") or "no solids"
            return {"verdict": VERDICT_FAIL, "checks": [_check(f"{name}.valid", False, reason)],
                    "failed_fields": [f"{name}.valid"], "skipped": []}

    checks.append(_check("solids", out["solid_count"] == gold["solid_count"],
                         f"got {out['solid_count']}, golden {gold['solid_count']}"))
    for field in ("face_count", "edge_count"):
        allowed = math.floor(gold[field] * tol["count_pct"] / 100.0)
        checks.append(_check(field.split("_")[0] + "s", abs(out[field] - gold[field]) <= allowed,
                             f"got {out[field]}, golden {gold[field]} (+-{allowed})"))
    got, want = out["extents_sorted"], gold["extents_sorted"]
    checks.append(_check("extents_sorted", all(abs(g - w) <= tol["dim"] for g, w in zip(got, want)),
                         f"got {got}, golden {want} (+-{tol['dim']} mm)"))
    checks.append(_check("volume", abs(out["volume"] - gold["volume"]) <= abs(gold["volume"]) * tol["volume_pct"] / 100.0,
                         f"got {out['volume']}, golden {gold['volume']} (+-{tol['volume_pct']} %)"))

    oh, gh = out.get("holes") or [], gold.get("holes") or []
    checks.append(_check("holes.count", len(oh) == len(gh), f"got {len(oh)}, golden {len(gh)}"))
    radii_o, radii_g = sorted(h["radius"] for h in oh), sorted(h["radius"] for h in gh)
    radii_ok = len(radii_o) == len(radii_g) and all(abs(a - b) <= tol["radius"] for a, b in zip(radii_o, radii_g))
    checks.append(_check("holes.radii", radii_ok, f"got {radii_o}, golden {radii_g} (+-{tol['radius']} mm)"))
    axes_o, axes_g = Counter(h.get("axis_index") for h in oh), Counter(h.get("axis_index") for h in gh)
    checks.append(_check("holes.axes", axes_o == axes_g, f"got {dict(axes_o)}, golden {dict(axes_g)} (axis index: count)"))
    thr_o, thr_g = Counter(_hole_through(h) for h in oh), Counter(_hole_through(h) for h in gh)
    checks.append(_check("holes.through", thr_o == thr_g, f"got {dict(thr_o)}, golden {dict(thr_g)} (through: count)"))

    fo, fg = out.get("fillets") or [], gold.get("fillets") or []
    unmatched = list(fo)
    missing = []
    for want_f in fg:
        for i, got_f in enumerate(unmatched):
            if got_f["kind"] == want_f["kind"] and abs(got_f["radius"] - want_f["radius"]) <= tol["radius"]:
                unmatched.pop(i)
                break
        else:
            missing.append(want_f)
    faces_o, faces_g = sum(f["count"] for f in fo), sum(f["count"] for f in fg)
    allowed = math.floor(faces_g * tol["count_pct"] / 100.0)
    checks.append(_check("fillets.radii", not missing and not unmatched and abs(faces_o - faces_g) <= allowed,
                         f"got {fo}, golden {fg}; missing {missing}, extra {unmatched}"))
    failed = [c["field"] for c in checks if not c["ok"]]
    return {"verdict": VERDICT_PASS if not failed else VERDICT_FAIL, "checks": checks, "failed_fields": failed,
            "skipped": []}


def symmetric_difference(out_path: str, gold_path: str, out_metrics: Dict, gold_metrics: Dict) -> Dict:
    """Volume of (output - golden) + (golden - output) after aligning bounding-box minimum corners, or a
    ``skipped`` reason when the comparison is not meaningful."""
    if out_metrics.get("solid_count") != 1 or gold_metrics.get("solid_count") != 1:
        return {"skipped": "symmetric difference needs one solid in each file"}
    if any(abs(a - b) > DEFAULT_TOL["dim"] * 4 for a, b in zip(out_metrics["extents"], gold_metrics["extents"])):
        return {"skipped": f"orientation differs from the golden (extents {out_metrics['extents']} vs "
                           f"{gold_metrics['extents']})"}
    import cadquery as cq  # noqa: WPS433 - lazy import: compare_metrics runs without the kernel

    out_solid = cq.importers.importStep(str(out_path)).solids().vals()[0]
    gold_solid = cq.importers.importStep(str(gold_path)).solids().vals()[0]
    ob, gb = out_metrics["bbox"], gold_metrics["bbox"]
    out_solid = out_solid.translate(cq.Vector(gb[0] - ob[0], gb[1] - ob[1], gb[2] - ob[2]))
    extra = out_solid.cut(gold_solid).Volume()
    missing = gold_solid.cut(out_solid).Volume()
    return {"volume": round(extra + missing, 3), "output_minus_golden": round(extra, 3),
            "golden_minus_output": round(missing, 3)}


def diff_files(out_path: str, gold_path: str, tol: Optional[Dict] = None) -> Dict:
    """Golden diff of two STEP files (needs CadQuery)."""
    tol = {**DEFAULT_TOL, **(tol or {})}
    out_metrics, gold_metrics = sq.analyze_step(out_path), sq.analyze_step(gold_path)
    result = compare_metrics(out_metrics, gold_metrics, tol)
    if not any(c["field"].endswith(".valid") for c in result["checks"]):
        sym = symmetric_difference(out_path, gold_path, out_metrics, gold_metrics)
        if "skipped" in sym:
            result["skipped"].append({"field": "symmetric_difference", "reason": sym["skipped"]})
        else:
            allowed = abs(gold_metrics["volume"]) * tol["symdiff_pct"] / 100.0
            check = _check("symmetric_difference", sym["volume"] <= allowed,
                           f"{sym['volume']} mm^3 (output-golden {sym['output_minus_golden']}, golden-output "
                           f"{sym['golden_minus_output']}), allowed {round(allowed, 3)} ({tol['symdiff_pct']} %)")
            result["checks"].append(check)
            if not check["ok"]:
                result["failed_fields"].append("symmetric_difference")
                result["verdict"] = VERDICT_FAIL
    result["output"], result["golden"] = str(out_path), str(gold_path)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output")
    parser.add_argument("golden")
    parser.add_argument("--tol", help="JSON object overriding DEFAULT_TOL")
    parser.add_argument("--out", help="write the result JSON here")
    args = parser.parse_args(argv)
    result = diff_files(args.output, args.golden, json.loads(args.tol) if args.tol else None)
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0 if result["verdict"] == VERDICT_PASS else 1


if __name__ == "__main__":
    sys.exit(main())
