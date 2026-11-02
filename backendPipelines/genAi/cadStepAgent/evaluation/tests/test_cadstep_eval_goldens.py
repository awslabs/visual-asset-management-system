# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The committed goldens against their corpus cases, and golden_diff.py on identical and perturbed parts. Needs
the CadQuery venv; skipped without the kernel."""

import os
import sys

import pytest
import yaml

cq = pytest.importorskip("cadquery")

EVALUATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EVALUATION_DIR not in sys.path:
    sys.path.insert(0, EVALUATION_DIR)
import golden_diff as gd  # noqa: E402
import make_goldens  # noqa: E402
import stp_quality as sq  # noqa: E402

with open(os.path.join(EVALUATION_DIR, "corpus.yaml"), encoding="utf-8") as _fh:
    CORPUS = yaml.safe_load(_fh)
FIXTURES_DIR = os.path.join(EVALUATION_DIR, CORPUS["globals"]["fixtures_dir"])
GOLDEN_CASES = [c for c in CORPUS["cases"] if c.get("golden")]


def _input_path(case):
    spec = case.get("input") or {}
    if "fixture" in spec:
        return os.path.join(FIXTURES_DIR, spec["fixture"])
    if "fallback" in spec:
        return os.path.join(EVALUATION_DIR, spec["fallback"])
    return None


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
def test_every_golden_scores_pass_against_its_case(case):
    """A golden is a reviewed known-good output: the scorer must call it PASS for the case that names it. A
    modify case's delta checks need its input; a fetched input that is absent skips the case."""
    golden = os.path.join(EVALUATION_DIR, case["golden"])
    input_path = _input_path(case)
    if input_path and not os.path.isfile(input_path):
        pytest.skip(f"{os.path.basename(input_path)} is fetched by fetch_fixtures.py, not committed")
    result = sq.evaluate(golden, case["expect"], input_path)
    assert result["verdict"] == sq.VERDICT_PASS, result["reason"]


def _plate(tmp_path, name, holes, size=(100, 60, 10)):
    path = str(tmp_path / name)
    part = cq.Workplane("XY").box(*size).faces(">Z").workplane().pushPoints(holes).hole(4.5)
    cq.exporters.export(part, path, exportType="STEP")
    return path


G1_HOLES = [(-42, -22), (42, -22), (-42, 22), (42, 22)]


def test_an_identical_part_passes_and_a_translated_one_too(tmp_path):
    golden = os.path.join(EVALUATION_DIR, "goldens", "g1-plate.step")
    same = gd.diff_files(_plate(tmp_path, "same.step", G1_HOLES), golden)
    assert same["verdict"] == gd.VERDICT_PASS and same["failed_fields"] == [], same
    assert any(c["field"] == "symmetric_difference" and c["ok"] for c in same["checks"])
    moved = str(tmp_path / "moved.step")
    cq.exporters.export(cq.importers.importStep(same["output"]).translate((50, 30, 5)), moved, exportType="STEP")
    assert gd.diff_files(moved, golden)["verdict"] == gd.VERDICT_PASS


def test_a_part_with_one_hole_removed_fails_on_the_hole_count(tmp_path):
    golden = os.path.join(EVALUATION_DIR, "goldens", "g1-plate.step")
    result = gd.diff_files(_plate(tmp_path, "three-holes.step", G1_HOLES[:3]), golden)
    assert result["verdict"] == gd.VERDICT_FAIL
    assert "holes.count" in result["failed_fields"]
    assert "extents_sorted" not in result["failed_fields"]


def test_a_scaled_part_fails_on_its_extents(tmp_path):
    golden = os.path.join(EVALUATION_DIR, "goldens", "g1-plate.step")
    result = gd.diff_files(_plate(tmp_path, "scaled.step", G1_HOLES, size=(110, 66, 11)), golden)
    assert result["verdict"] == gd.VERDICT_FAIL
    assert {"extents_sorted", "volume"} <= set(result["failed_fields"])
    assert "holes.count" not in result["failed_fields"]
    assert result["skipped"] and result["skipped"][0]["field"] == "symmetric_difference"


def test_a_blind_hole_where_the_golden_has_a_through_hole_names_the_through_field(tmp_path):
    golden = os.path.join(EVALUATION_DIR, "goldens", "g9-inch-plate.step")
    path = str(tmp_path / "blind.step")
    inch = 25.4
    part = cq.Workplane("XY").box(2 * inch, inch, 0.25 * inch).faces(">Z").workplane().hole(0.25 * inch, depth=3)
    cq.exporters.export(part, path, exportType="STEP")
    result = gd.diff_files(path, golden)
    assert result["verdict"] == gd.VERDICT_FAIL and "holes.through" in result["failed_fields"]


def test_make_goldens_builds_only_what_is_missing(tmp_path):
    assert make_goldens.build(str(tmp_path), only={"g9-inch-plate.step"}) == ["g9-inch-plate.step"]
    assert make_goldens.build(str(tmp_path), only={"g9-inch-plate.step"}) == []
    rebuilt = gd.diff_files(str(tmp_path / "g9-inch-plate.step"), os.path.join(EVALUATION_DIR, "goldens", "g9-inch-plate.step"))
    assert rebuilt["verdict"] == gd.VERDICT_PASS
