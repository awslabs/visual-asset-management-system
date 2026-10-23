# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The geometry scorer (stp_quality.py) on parts built here with CadQuery. Needs the CadQuery venv; skipped
without the kernel."""

import math
import os
import sys

import pytest

cq = pytest.importorskip("cadquery")

EVALUATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EVALUATION_DIR not in sys.path:
    sys.path.insert(0, EVALUATION_DIR)
import stp_quality as sq  # noqa: E402

FIXTURE = os.path.join(EVALUATION_DIR, "fixtures", "plate-40x40x10.step")


@pytest.fixture(scope="module")
def four_hole_plate(tmp_path_factory):
    """40x40x10 plate with four 5 mm through-holes inset 6 mm from the corners."""
    path = str(tmp_path_factory.mktemp("gen") / "plate-4holes.step")
    plate = (cq.Workplane("XY").box(40, 40, 10).faces(">Z").workplane()
             .pushPoints([(-14, -14), (14, -14), (-14, 14), (14, 14)]).hole(5.0))
    cq.exporters.export(plate, path)
    return path


@pytest.fixture(scope="module")
def cbore_fillet_plate(tmp_path_factory):
    """100x60x10 plate: four 4.5 through-holes inset 8, one central 8x4 cbore over a 4.5 hole,
    R3 fillets on the four vertical edges."""
    path = str(tmp_path_factory.mktemp("gen") / "plate-cbore-fillet.step")
    plate = (cq.Workplane("XY").box(100, 60, 10).faces(">Z").workplane()
             .pushPoints([(-42, -22), (42, -22), (-42, 22), (42, 22)]).hole(4.5)
             .faces(">Z").workplane().cboreHole(4.5, 8, 4)
             .edges("|Z").fillet(3))
    cq.exporters.export(plate, path)
    return path


@pytest.fixture(scope="module")
def flanged_disc(tmp_path_factory):
    """Ø120 x 8 disc with a Ø30 bore and 6 x Ø6.6 holes on a Ø100 PCD."""
    path = str(tmp_path_factory.mktemp("gen") / "disc.step")
    disc = (cq.Workplane("XY").circle(60).extrude(8).faces(">Z").workplane()
            .hole(30).faces(">Z").workplane().polarArray(50, 0, 360, 6).hole(6.6))
    cq.exporters.export(disc, path)
    return path


def test_fixture_plate_metrics():
    m = sq.analyze_step(FIXTURE)
    assert m["import_ok"] and m["valid"] and m["shells_closed"]
    assert m["solid_count"] == 1
    assert m["extents_sorted"] == pytest.approx([10, 40, 40], abs=0.01)
    assert m["volume"] == pytest.approx(16000, rel=1e-6)
    assert m["area"] == pytest.approx(2 * 40 * 40 + 4 * 40 * 10, rel=1e-6)
    assert m["planar_faces"] == 6
    assert m["holes"] == [] and m["fillet_faces"] == [] and m["counterbores"] == []


def test_fixture_plate_passes_its_expect():
    res = sq.check_case(sq.analyze_step(FIXTURE),
                        {"solids": 1, "extents_sorted": [40, 40, 10], "volume": 16000, "holes_total": 0})
    assert res["verdict"] == sq.VERDICT_PASS, res
    assert res["passed"] == res["total"] == 5


def test_four_hole_plate_holes(four_hole_plate):
    m = sq.analyze_step(four_hole_plate)
    assert len(m["holes"]) == 4
    for h in m["holes"]:
        assert h["radius"] == pytest.approx(2.5, abs=1e-3)
        assert h["through"] is True
        assert h["axis_index"] == 2
        assert h["length"] == pytest.approx(10, abs=1e-3)
    positions = sorted(tuple(round(x, 2) for x in h["pos2d_from_bbox_min"]) for h in m["holes"])
    assert positions == [(6, 6), (6, 34), (34, 6), (34, 34)]
    assert m["volume"] == pytest.approx(16000 - 4 * math.pi * 2.5 ** 2 * 10, rel=1e-6)
    res = sq.check_case(m, {
        "solids": 1, "extents_sorted": [40, 40, 10], "volume": 15214.6,
        "holes": [{"radius": 2.5, "count": 4, "through": True,
                   "positions_from_bbox_min": [[6, 6], [34, 6], [6, 34], [34, 34]]}]})
    assert res["verdict"] == sq.VERDICT_PASS, res["reason"]


def test_counterbore_and_fillet_detection(cbore_fillet_plate):
    m = sq.analyze_step(cbore_fillet_plate)
    small = [h for h in m["holes"] if abs(h["radius"] - 2.25) < 0.01]
    big = [h for h in m["holes"] if abs(h["radius"] - 4.0) < 0.01]
    assert len(small) == 5 and len(big) == 1
    assert len(m["counterbores"]) == 1
    cb = m["counterbores"][0]
    assert cb["cbore_depth"] == pytest.approx(4, abs=1e-3) and cb["spans_body"]
    central = [h for h in small if h["pos2d_from_bbox_min"] and abs(h["pos2d_from_bbox_min"][0] - 50) < 0.01][0]
    assert central["through"] is False and central["through_with_counterbore"] is True
    assert m["fillets"] == [{"kind": "cylindrical", "radius": 3.0, "count": 4}]
    res = sq.check_case(m, {"fillets": {"radius": 3, "count": 4},
                            "counterbores": {"count": 1, "through_radius": 2.25, "cbore_radius": 4.0, "cbore_depth": 4},
                            "holes": [{"radius": 2.25, "count": 5, "through": True}],
                            "volume_below": 60000})
    assert res["verdict"] == sq.VERDICT_PASS, res["reason"]


def test_pcd_and_bore(flanged_disc):
    m = sq.analyze_step(flanged_disc)
    res = sq.check_case(m, {"solids": 1, "extents_sorted": [120, 120, 8],
                            "holes": [{"radius": 3.3, "count": 6, "through": True, "pcd_radius": 50},
                                      {"radius": 15, "count": 1, "through": True}],
                            "bosses": [{"radius": 60, "length": 8}]})
    assert res["verdict"] == sq.VERDICT_PASS, res["reason"]


def test_rect_pattern_check(four_hole_plate):
    m = sq.analyze_step(four_hole_plate)
    ok = sq.check_case(m, {"hole_pattern_rect": {"sides": [28, 28], "tol": 0.5, "count": 4}})
    assert ok["verdict"] == sq.VERDICT_PASS, ok["reason"]
    bad = sq.check_case(m, {"hole_pattern_rect": {"sides": [86, 58], "tol": 1.0, "count": 4}})
    assert bad["verdict"] == sq.VERDICT_PARTIAL  # valid solid, 1 of 2 checks


def test_delta_identical_and_changes(four_hole_plate):
    fix = sq.analyze_step(FIXTURE)
    same = sq.check_case(fix, {"delta": {"identical": True}}, input_metrics=sq.analyze_step(FIXTURE))
    assert same["verdict"] == sq.VERDICT_PASS
    holes = sq.analyze_step(four_hole_plate)
    d = sq.compare_to_input(fix, holes)
    assert d["holes_added"] == 4 and d["holes_removed"] == 0 and d["extents_sorted_same"]
    assert d["volume_change"] == pytest.approx(-4 * math.pi * 2.5 ** 2 * 10, rel=1e-4)
    res = sq.check_case(holes, {"delta": {"holes_added": 4, "extents_sorted_same": True,
                                          "volume_change": -785.4, "volume_change_tol": 5}}, input_metrics=fix)
    assert res["verdict"] == sq.VERDICT_PASS, res["reason"]
    notsame = sq.check_case(holes, {"delta": {"identical": True}}, input_metrics=fix)
    assert notsame["verdict"] == sq.VERDICT_PARTIAL  # valid solid, identical fails -> 1/2


def test_verdicts_for_missing_and_wrong(tmp_path):
    missing = sq.check_case(sq.analyze_step(str(tmp_path / "nope.step")), {"solids": 1})
    assert missing["verdict"] == sq.VERDICT_FAIL and "absent" in missing["reason"]
    garbage = tmp_path / "bad.step"
    garbage.write_text("ISO-10303-21; this is not a step file")
    assert sq.check_case(sq.analyze_step(str(garbage)), {"solids": 1})["verdict"] == sq.VERDICT_FAIL
    # valid solid but everything wrong -> FAIL (1 of 4 checks)
    wrong = sq.check_case(sq.analyze_step(FIXTURE), {"solids": 2, "extents_sorted": [1, 2, 3], "volume": 1})
    assert wrong["verdict"] == sq.VERDICT_FAIL and wrong["passed"] == 1 and wrong["total"] == 4


def test_markdown_row_and_key_metrics(four_hole_plate):
    res = sq.evaluate(four_hole_plate, {"solids": 1})
    row = sq.markdown_row("T1", "generate", res, attempts=1, wall_s=12.3)
    assert row.startswith("| T1 | generate | **PASS** (2/2) | 1 | 12 |")
    assert "holes 4×Ø5.0" in row


def test_slot_ends_fillets_absent_min_extent(tmp_path, cbore_fillet_plate):
    path = str(tmp_path / "slotted.step")
    leg = (cq.Workplane("XY").box(50, 40, 4).faces(">Z").workplane()
           .pushPoints([(0, -6), (0, 6)]).slot2D(20, 6, angle=0).cutThruAll())
    cq.exporters.export(leg, path)
    m = sq.analyze_step(path)
    assert len(m["partial_concave_cylinders"]) == 4  # two R3 half-cylinder ends per slot
    assert all(abs(p["sweep_deg"] - 180) < 0.5 for p in m["partial_concave_cylinders"])
    assert m["holes"] == []
    res = sq.check_case(m, {"partial_concave": {"radius": 3, "min_count": 2}, "min_extent": 4, "max_extent": 50})
    assert res["verdict"] == sq.VERDICT_PASS, res["reason"]
    fil = sq.check_case(sq.analyze_step(cbore_fillet_plate), {"fillets_absent": [3]})
    assert fil["verdict"] == sq.VERDICT_PARTIAL and "still at r=3" in fil["reason"]


def test_recompute_verdict_with_extra_check(four_hole_plate):
    res = sq.evaluate(four_hole_plate, {"solids": 1})
    res["checks"].append({"check": "report", "ok": False, "detail": "summary lacks volume"})
    res = sq.recompute_verdict(res)
    assert res["verdict"] == sq.VERDICT_PARTIAL and res["passed"] == 2 and res["total"] == 3
