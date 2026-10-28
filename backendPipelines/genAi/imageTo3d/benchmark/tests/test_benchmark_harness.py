# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The harness has to refuse to let a degenerate result look good.

Every "concern" asserted here is a failure that actually happened while building this pipeline, where
the run reported success, satisfied the print gate, and produced something unusable. The point of these
tests is that the comparison surfaces those cases rather than ranking them at the top.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cost  # noqa: E402
import matrix  # noqa: E402
import report  # noqa: E402


def _result(**kwargs):
    variation = kwargs.pop("variation", None) or matrix.Variation(
        model=kwargs.pop("model", "triposr"), image=kwargs.pop("image", "subject.png"),
        settings=kwargs.pop("settings", {}))
    defaults = dict(
        status="SUCCEEDED",
        stage_seconds={"weightCache": 15.0, "preprocess": 2.0, "generate": 20.0, "cleanup": 5.0,
                       "export": 1.0, "upload": 2.0},
        instance_type="g5.4xlarge", gpu_name="NVIDIA A10G",
        texture_status="reprojected", print_ready=True, volume_retained=0.99,
        face_count=36000, preview_path="previews/triposr.gif",
    )
    defaults.update(kwargs)
    return matrix.RunResult(variation=variation, **defaults)


class TestTunnelsAreFlagged:
    """A tunnel through a closed surface passes every print-gate criterion, so the benchmark is where it
    has to surface. Measured on one photograph of a person, all five models produced them: 16, 9, 7, 5 and
    8 -- which is also why this is not a comparison against the group median."""

    def test_a_tunnel_is_reported_as_a_concern(self):
        notes = " ".join(_result(shape={"genus": 16}).concerns())

        assert "tunnel" in notes
        assert "16" in notes

    def test_a_closed_surface_with_no_tunnels_is_not_flagged(self):
        notes = " ".join(_result(shape={"genus": 0}).concerns())

        assert "tunnel" not in notes

    def test_a_run_that_did_not_report_a_genus_is_not_flagged(self):
        """An open surface has no genus, and the field is absent rather than zero. Absent must not read as
        a defect, and must not raise either."""
        notes = " ".join(_result(shape={"fillRatio": 0.4}).concerns())

        assert "tunnel" not in notes

    def test_genus_is_not_compared_against_the_group_median(self):
        """A median test flags nobody when every model carries the defect, which is the measured case."""
        assert "genus" not in matrix.SHAPE_COMPARISONS


class TestMatrixExpansion:
    def test_the_cross_product_covers_every_combination(self):
        variations = matrix.expand(["a", "b"], ["one.png", "two.png"],
                                  {"IMAGE_CLEANUP": ["auto", "skip"]})
        assert len(variations) == 2 * 2 * 2
        assert len({v.label for v in variations}) == 8, "labels must be unique per cell"

    def test_no_setting_axes_still_yields_one_run_per_model_and_image(self):
        assert len(matrix.expand(["a", "b"], ["one.png"])) == 2

    def test_an_empty_axis_is_ignored_rather_than_zeroing_the_matrix(self):
        """A misconfigured axis must not silently reduce the matrix to nothing."""
        assert len(matrix.expand(["a"], ["one.png"], {"SEED": []})) == 1

    @pytest.mark.parametrize("models,images", [([], ["a.png"]), (["m"], [])])
    def test_an_empty_model_or_image_set_is_rejected(self, models, images):
        with pytest.raises(ValueError):
            matrix.expand(models, images)


class TestModelTimeExcludesOverhead:
    def test_model_seconds_excludes_cache_and_transfer(self):
        """A cold weight cache is not the model being slow, and must not be charged to it."""
        result = _result()
        assert result.total_seconds == 45.0
        assert result.model_seconds == 27.0  # preprocess + generate + cleanup

    def test_a_cold_cache_does_not_change_the_model_time(self):
        warm = _result(stage_seconds={"weightCache": 0.5, "generate": 20.0})
        cold = _result(stage_seconds={"weightCache": 600.0, "generate": 20.0})
        assert warm.model_seconds == cold.model_seconds == 20.0
        assert cold.total_seconds > warm.total_seconds


class TestConcernsCatchDegenerateResults:
    def test_a_geometry_only_result_is_flagged_against_the_colour_requirement(self):
        assert any("no colour" in c for c in _result(texture_status="none").concerns())

    def test_a_placeholder_texture_does_not_count_as_colour(self):
        """'sidecarsOnly' means the maps shipped beside the mesh, not on it."""
        assert not _result(texture_status="sidecarsOnly").has_colour

    def test_a_destructive_repair_is_flagged_even_when_the_gate_passed(self):
        """Observed live: a repair kept 2.5% of the volume and passed every gate criterion."""
        concerns = _result(volume_retained=0.025).concerns()
        assert any("removed" in c and "volume" in c for c in concerns)

    def test_a_gate_pass_on_a_trivial_mesh_is_flagged(self):
        """Observed live: a featureless blob was trivially watertight."""
        assert any("only" in c and "faces" in c for c in _result(face_count=1200).concerns())

    def test_a_run_with_no_preview_is_flagged(self):
        """Every degenerate result was caught by looking; an unviewed run is an unverified one."""
        assert any("never looked at" in c for c in _result(preview_path="").concerns())

    def test_a_healthy_run_carries_no_concerns(self):
        assert _result().concerns() == []


class TestStageCost:
    def test_cost_is_duration_at_the_instance_rate(self):
        costs = cost.stage_costs({"generate": 3600.0}, 2.0)
        assert costs["generate"] == pytest.approx(2.0)

    def test_the_breakdown_separates_model_work_from_overhead(self):
        breakdown = cost.cost_breakdown(
            {"weightCache": 3600.0, "generate": 3600.0}, 1.0)
        assert breakdown["modelCost"] == pytest.approx(1.0)
        assert breakdown["overheadCost"] == pytest.approx(1.0)
        assert breakdown["totalCost"] == pytest.approx(2.0)

    def test_stages_are_reported_in_execution_order(self):
        breakdown = cost.cost_breakdown({"export": 1.0, "generate": 1.0, "weightCache": 1.0}, 1.0)
        assert list(breakdown["perStage"]) == ["weightCache", "generate", "export"]

    def test_a_negative_rate_is_rejected(self):
        with pytest.raises(ValueError):
            cost.stage_costs({"generate": 1.0}, -1.0)

    def test_an_override_wins_over_a_lookup(self):
        """A deployment on a Savings Plan must be able to report what it actually pays."""
        rates = cost.resolve_hourly_rates(["g5.4xlarge"], "us-west-2",
                                          price_overrides={"g5.4xlarge": 0.42})
        assert rates == {"g5.4xlarge": 0.42}


class TestReport:
    def test_the_csv_carries_one_row_per_run(self):
        text = report.to_csv([_result(), _result(model="stable3dgen")], {"g5.4xlarge": 2.0})
        assert len(text.strip().splitlines()) == 3  # header plus two rows

    def test_an_unpriced_instance_reports_duration_without_a_cost(self):
        """A blank cost must not read as free, so the report says why it is blank."""
        text = report.to_markdown([_result()], rates={}, region="us-west-2",
                                  missing_rates=["g5.4xlarge"])
        assert "no on-demand rate could be resolved" in text
        assert "g5.4xlarge" in text

    def test_the_markdown_surfaces_concerns_and_previews(self):
        text = report.to_markdown([_result(volume_retained=0.02)], {"g5.4xlarge": 2.0})
        assert "carry a concern" in text
        assert "## Previews" in text
        assert "Look at these before trusting the table above." in text

    def test_an_all_geometry_matrix_says_the_colour_requirement_is_unmet(self):
        text = report.to_markdown([_result(texture_status="none")], {"g5.4xlarge": 2.0})
        assert "No run produced a coloured mesh" in text

    def test_failures_are_listed_with_their_errors(self):
        text = report.to_markdown([_result(status="FAILED", error="ModelNotEnabled")],
                                  {"g5.4xlarge": 2.0})
        assert "## Failures" in text and "ModelNotEnabled" in text

    def test_the_report_does_not_rank_runs_on_a_single_score(self):
        """A single score is how a degenerate result wins; the axes stay side by side."""
        text = report.to_markdown([_result(), _result(model="stable3dgen")], {"g5.4xlarge": 2.0})
        assert "there is no single ranking" in text


class TestABaselineRunMeasuresTheBareModel:
    """The baseline comparison is between models with nothing added to them. A loop or a
    base is an optional post-process that costs the same whichever model produced the mesh, and each one
    joined by concat is a separate shell, so the gate's body count would no longer describe the model."""

    def test_every_baseline_variation_turns_print_features_off(self):
        for variation in matrix.expand(["stable3dgen", "triposr"], ["/mug.png"]):
            assert variation.settings["PRINT_FEATURES"] == "none"

    def test_the_feature_dimensions_are_zeroed_too(self):
        settings = matrix.expand(["triposr"], ["/mug.png"])[0].settings
        for key in ("LOOP_DIAMETER_MM", "LOOP_THICKNESS_MM", "BASE_DIAMETER_MM", "BASE_HEIGHT_MM"):
            assert settings[key] == "0", f"{key} should not size a feature a baseline run does not add"

    def test_a_swept_axis_wins_over_the_baseline(self):
        """The follow-up feature comparison is a sweep, not a reason to disable the baseline."""
        variations = matrix.expand(["triposr"], ["/mug.png"], matrix.FEATURE_AXIS)

        assert sorted(v.settings["PRINT_FEATURES"] for v in variations) == [
            "base", "loop", "loop+base", "none"]

    def test_the_baseline_can_be_turned_off_wholesale(self):
        assert matrix.expand(["triposr"], ["/mug.png"], baseline=False)[0].settings == {}

    def test_a_label_names_only_what_differs_from_the_baseline(self):
        """Otherwise every row of the report repeats five identical settings."""
        plain = matrix.expand(["triposr"], ["/mug.png"])[0]
        assert plain.label == "triposr | /mug.png"

        with_loop = matrix.expand(["triposr"], ["/mug.png"], {"PRINT_FEATURES": ["loop"]})[0]
        assert with_loop.label == "triposr | /mug.png | PRINT_FEATURES=loop"


class TestVertexColourCountsAsColour:
    """SAM 3D Objects returns colour per vertex rather than as an atlas. Scoring that as no colour would
    disqualify a model that meets the colour requirement."""

    def test_per_vertex_colour_satisfies_the_colour_requirement(self):
        assert _result(texture_status="fromVertexColors").has_colour

    def test_it_ranks_below_an_atlas_carried_across_correctly(self):
        """Below a carried atlas, because its detail is the vertex spacing. But ABOVE a reprojected one:
        that path copies each rebuilt vertex the uv of its nearest pre-cleanup vertex, which is invalid
        for a coordinate into a discontinuous chart layout, and measured 43.4 mean colour error against
        the model's own output where re-baking measured 7.9."""
        assert (matrix.TEXTURE_RANK["reprojected"]
                < matrix.TEXTURE_RANK["fromVertexColors"]
                < matrix.TEXTURE_RANK["resampledFromAtlas"]
                < matrix.TEXTURE_RANK["rebakedFromAtlas"]
                < matrix.TEXTURE_RANK["preserved"])

    def test_its_limit_is_reported_as_a_concern(self):
        notes = " ".join(_result(texture_status="fromVertexColors").concerns())
        assert "vertex spacing" in notes

    def test_sidecars_alone_still_do_not_count_as_colour(self):
        assert not _result(texture_status="sidecarsOnly").has_colour


class TestADetachedSubjectIsFlagged:
    def test_more_pieces_than_expected_is_a_concern(self):
        notes = " ".join(_result(bodies=2, expected_bodies=1).concerns())
        assert "2 pieces" in notes and "detached" in notes

    def test_the_shells_a_print_feature_adds_are_not_flagged(self):
        assert _result(bodies=3, expected_bodies=3).concerns() == []


class TestAJoinedSubjectIsFlagged:
    """Joining pieces assembles a torn reconstruction and also absorbs a detached feature. Nothing
    measurable separates the two, so the comparison surfaces it for review against the preview."""

    def test_a_joined_result_carries_a_concern(self):
        notes = " ".join(_result(pieces_fused=2).concerns())
        assert "joined 2 piece" in notes and "preview" in notes

    def test_a_run_that_joined_nothing_is_not_flagged(self):
        assert _result(pieces_fused=0).concerns() == []


class TestShapeIsComparedAcrossTheModelsOnOneImage:
    """The only likeness signal available with no ground truth and no model call: every model in a group
    reconstructed the same photograph, so a descriptor where one departs from the group points at it.

    The numbers below are the four real baselines on one mug:

        model         fillRatio   footprintAspect   heightOverWidth
        stable3dgen     0.217          1.224             0.758        faithful, handle present
        sam3d           0.435          1.325             1.334        faithful, handle present
        trellis2        0.436          1.004             0.808        no handle
        triposr         0.936          0.964             0.890        no handle, and a solid billet
    """

    REAL = {
        "stable3dgen": {"fillRatio": 0.217, "footprintAspect": 1.224, "heightOverWidth": 0.758},
        "sam3d": {"fillRatio": 0.435, "footprintAspect": 1.325, "heightOverWidth": 1.334},
        "trellis2": {"fillRatio": 0.436, "footprintAspect": 1.004, "heightOverWidth": 0.808},
        "triposr": {"fillRatio": 0.936, "footprintAspect": 0.964, "heightOverWidth": 0.890},
    }

    def _group(self, shapes=None, image="/mug.png"):
        shapes = shapes or self.REAL
        results = []
        for model, shape in shapes.items():
            variation = matrix.Variation(model=model, image=image, settings={})
            results.append(matrix.RunResult(
                variation=variation, status="SUCCEEDED", texture_status="reprojected",
                print_ready=True, preview_path="p.gif", shape=dict(shape)))
        return matrix.flag_shape_outliers(results)

    def _notes(self, results, model):
        return " ".join(r for res in results if res.variation.model == model for r in res.peer_notes)

    def test_the_billet_is_flagged_on_fill(self):
        results = self._group()
        assert "fillRatio" in self._notes(results, "triposr")
        assert "solid lump" in self._notes(results, "triposr")

    def test_the_faithful_hollow_model_is_not_flagged(self):
        """It is MORE hollow than the group, which for a vessel is fidelity. A symmetric test on fill
        flagged the best model in the group, which is worse than saying nothing."""
        results = self._group()
        assert "fillRatio" not in self._notes(results, "stable3dgen")

    def test_a_model_disagreeing_on_proportions_is_flagged(self):
        results = self._group()
        assert "heightOverWidth" in self._notes(results, "sam3d")
        assert "proportions" in self._notes(results, "sam3d")

    def test_a_defect_half_the_group_shares_is_NOT_flagged(self):
        """The honest limitation. Two of these four models lost the handle, so the footprint median sits
        between the two populations and neither side reads as an outlier. The values are reported as
        columns for exactly this reason -- a reader comparing them down the page can see what a
        median-based flag structurally cannot."""
        results = self._group()
        assert "footprintAspect" not in self._notes(results, "trellis2")
        assert "footprintAspect" not in self._notes(results, "triposr")

    def test_a_lone_rounder_footprint_IS_flagged(self):
        """When only one model in the group has lost the feature, the comparison does catch it."""
        shapes = dict(self.REAL)
        shapes["triposr"] = {"fillRatio": 0.44, "footprintAspect": 1.30, "heightOverWidth": 0.85}
        results = self._group(shapes)

        assert "footprintAspect" in self._notes(results, "trellis2")
        assert "handle" in self._notes(results, "trellis2")

    def test_a_group_of_two_is_not_compared(self):
        """Two runs have no median worth the name; a disagreement says nothing about which is wrong."""
        results = self._group({k: self.REAL[k] for k in ("stable3dgen", "triposr")})

        assert all(not r.peer_notes for r in results)

    def test_models_that_agree_produce_no_notes(self):
        agreed = {m: {"fillRatio": 0.43, "footprintAspect": 1.2, "heightOverWidth": 0.8}
                  for m in ("a", "b", "c", "d")}
        results = self._group(agreed)

        assert all(not r.peer_notes for r in results)

    def test_runs_on_different_images_are_not_compared_against_each_other(self):
        """Different subjects have different shapes; comparing across them is meaningless."""
        first = self._group({k: self.REAL[k] for k in self.REAL}, image="/a.png")
        second = self._group({k: self.REAL[k] for k in self.REAL}, image="/b.png")
        mixed = matrix.flag_shape_outliers(
            [matrix.RunResult(variation=matrix.Variation(model="x", image="/a.png", settings={}),
                              status="SUCCEEDED", shape={"fillRatio": 0.43}),
             matrix.RunResult(variation=matrix.Variation(model="y", image="/b.png", settings={}),
                              status="SUCCEEDED", shape={"fillRatio": 9.9})])

        assert all(not r.peer_notes for r in mixed), "one run per image cannot be compared"
        assert len(first) == len(second) == 4

    def test_a_failed_run_is_left_out_of_the_median(self):
        results = self._group()
        failed = matrix.RunResult(
            variation=matrix.Variation(model="broken", image="/mug.png", settings={}),
            status="FAILED", shape={"fillRatio": 99.0})
        matrix.flag_shape_outliers(results + [failed])

        assert not failed.peer_notes

    def test_the_notes_reach_the_concerns_column(self):
        results = self._group()
        billet = next(r for r in results if r.variation.model == "triposr")

        assert any("fillRatio" in note for note in billet.concerns())
