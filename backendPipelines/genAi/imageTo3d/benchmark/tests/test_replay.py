# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The offline replay runs the container's OWN post-generation chain, not a reproduction of it.

That distinction is the whole point. A hand-written reproduction of the same stages did not match the
live run, because the pipeline decimates the TEXTURED mesh and welds its uv seams first while the
reproduction had loaded an STL with no uv field. So the replay calls ``postprocess`` in the container's
``__main__.py``, and these tests assert it keeps doing that rather than drifting into a copy.

Measured against live runs: replaying a stored TripoSR mesh reproduced the live volume retention to four
decimal places (0.9931) and the shape descriptors to three, with an identical gate verdict and an
identical repair ladder, in 13 seconds against roughly fifteen minutes for a rebuild plus a GPU job.
"""

import json
import os
import sys

import pytest
import trimesh

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import replay  # noqa: E402


@pytest.fixture(scope="module")
def main_module():
    return replay.load_container_main()


def _hollow_mug():
    outer = trimesh.creation.cylinder(radius=20.0, height=40.0, sections=48)
    inner = trimesh.creation.cylinder(radius=17.0, height=40.0, sections=48)
    inner.apply_translation((0, 0, 4.0))
    return trimesh.boolean.difference([outer, inner])


class TestItUsesTheContainersOwnChain:

    def test_the_container_entry_module_loads(self, main_module):
        assert hasattr(main_module, "postprocess")

    def test_the_replay_calls_postprocess_rather_than_reimplementing_the_order(self, main_module,
                                                                              monkeypatch, tmp_path):
        """A reproduction of the stage order drifts from the pipeline; this must not be one."""
        called = {}

        def _spy(mesh, settings, **kwargs):
            called["settings"] = settings
            called["kwargs"] = kwargs
            return mesh, None, "none", {}, None

        monkeypatch.setattr(main_module, "postprocess", _spy)
        path = tmp_path / "m.stl"
        _hollow_mug().export(path)

        replay.replay(str(path), {"MESH_CLEANUP": "false"}, main_module=main_module)

        assert called, "the replay did not go through postprocess"
        assert called["kwargs"]["run_cleanup"] is False

    def test_settings_come_from_the_pipelines_own_allow_list(self, main_module):
        """A hand-written settings dict silently omits every default and measures something else."""
        settings = replay.resolve_settings(main_module, "triposr")

        assert settings["MODEL"] == "triposr"
        for key in ("TARGET_FACE_COUNT", "REPAIR_BACKENDS", "PRINT_GATE_POLICY", "MESH_CLEANUP"):
            assert key in settings, key
        assert "TRIPOSR_MC_RESOLUTION" in settings, "the model's own block should resolve too"

    def test_a_setting_belonging_to_another_model_is_not_resolved(self, main_module):
        settings = replay.resolve_settings(main_module, "triposr")

        assert "TRELLIS2_SPARSE_STEPS" not in settings

    def test_an_override_wins_over_the_default(self, main_module):
        settings = replay.resolve_settings(main_module, "triposr", overrides=["TARGET_FACE_COUNT=1234"])

        assert settings["TARGET_FACE_COUNT"] == "1234"

    def test_pipeline_parameter_shaped_settings_are_accepted(self, main_module, tmp_path):
        """The same file shape the CLI takes, so a replay can use the settings a live run used."""
        path = tmp_path / "params.json"
        path.write_text(json.dumps({"genai-image-to-3d": {
            "templateId": "image-to-3d-triposr",
            "templateTags": [{"key": "TARGET_SIZE_MM", "value": 60},
                             {"key": "PRINT_FEATURES", "value": "loop"}]}}), encoding="utf-8")

        settings = replay.resolve_settings(main_module, "triposr", settings_file=str(path))

        assert settings["TARGET_SIZE_MM"] == "60"
        assert settings["PRINT_FEATURES"] == "loop"


class TestTheSummaryCarriesWhatAVerificationNeeds:

    @pytest.fixture(scope="class")
    def summary(self, tmp_path_factory):
        path = tmp_path_factory.mktemp("replay") / "mug.stl"
        _hollow_mug().export(path)
        main_module = replay.load_container_main()
        settings = replay.resolve_settings(
            main_module, "stable3dgen",
            overrides=["TARGET_FACE_COUNT=0", "PRINT_FEATURES=none", "TARGET_SIZE_MM=0"])
        return replay.replay(str(path), settings, main_module=main_module)

    def test_it_reports_the_gate_verdict(self, summary):
        assert summary["gate"] is not None
        for key in ("isWatertight", "nonManifoldEdges", "bodies", "passed"):
            assert key in summary["gate"], key

    def test_it_reports_the_shape_descriptors(self, summary):
        assert summary["shape"]["fillRatio"] < 0.5, "a hollow mug should not read as filled"
        assert "footprintAspect" in summary["shape"]

    def test_it_reports_what_went_in_and_what_came_out(self, summary):
        assert summary["loaded"]["faces"] > 0
        assert summary["shipped"]["faces"] > 0

    def test_cleanup_can_be_turned_off(self, tmp_path):
        path = tmp_path / "m.stl"
        _hollow_mug().export(path)
        main_module = replay.load_container_main()

        summary = replay.replay(str(path), {"MESH_CLEANUP": "false", "TARGET_SIZE_MM": "0",
                                            "PRINT_FEATURES": "none"}, main_module=main_module)

        assert summary["gate"] is None, "no gate should be measured when the cleanup did not run"


class TestRegressionComparison:
    """The replay's second job: asserting a stored gate verdict has not moved."""

    def test_identical_verdicts_compare_equal(self):
        summary = {"gate": {"passed": True, "bodies": 1}, "textureStatus": "none"}

        assert replay.compare(summary, dict(summary)) == []

    def test_a_moved_verdict_is_reported_with_both_values(self):
        got = {"gate": {"passed": False, "bodies": 2}, "textureStatus": "none"}
        want = {"gate": {"passed": True, "bodies": 1}, "textureStatus": "none"}

        differences = replay.compare(got, want)

        assert any("gate.passed" in d and "True" in d and "False" in d for d in differences)
        assert any("gate.bodies" in d for d in differences)

    def test_a_changed_texture_status_is_reported(self):
        got = {"gate": {}, "textureStatus": "none"}
        want = {"gate": {}, "textureStatus": "fromVertexColors"}

        assert any("textureStatus" in d for d in replay.compare(got, want))

    def test_prose_and_provenance_do_not_count_as_a_moved_verdict(self):
        """Failure wording and the rungs walked move with unrelated changes; the verdict is the
        contract. A comparison that trips on either would be ignored within a week."""
        got = {"gate": {"passed": True, "failures": ["reworded"], "backendsApplied": ["trimesh"]},
               "textureStatus": "none"}
        want = {"gate": {"passed": True, "failures": ["old wording"], "backendsApplied": []},
                "textureStatus": "none"}

        assert replay.compare(got, want) == []
