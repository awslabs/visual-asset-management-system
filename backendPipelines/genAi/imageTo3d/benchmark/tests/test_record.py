# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A recorded round is only useful if a later one can be held against it.

Each execution writes its own report under its own id, and the asset's metadata keeps only the most
recent copy -- so a round's numbers are lost as soon as the next run starts, and a comparison months
later has nothing to compare against. These tests cover what the comparison must and must not report:
a gate verdict flipping is never noise, a stage timing drifting on a shared queue always is.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import record  # noqa: E402


def _entry(**overrides):
    entry = {
        "executionId": "a" * 32,
        "model": "triposr",
        "instanceType": "g5.8xlarge",
        "gpu": "NVIDIA A10G",
        "settings": {"TARGET_SIZE_MM": "60"},
        "stageSeconds": {"generate": 176.4, "cleanup": 119.5, "weightCache": 14.5},
        "totalRunSeconds": 356.1,
        "textureStatus": "reprojected",
        "printReady": True,
        "faceCount": 190930,
        "piecesFused": 1,
        "shape": {"fillRatio": 0.936, "footprintAspect": 0.964, "heightOverWidth": 0.890},
        "printFeatures": {},
        "gate": {"isWatertight": True, "nonManifoldEdges": 0, "bodies": 1, "expectedBodies": 1,
                 "passed": True, "backendsApplied": ["trimesh", "manifold3d", "pymeshfix"],
                 "volumeRetained": 0.9931},
    }
    entry.update(overrides)
    return entry


def _recording(runs=None, commit="c0ffee"):
    return {"note": "a round", "commit": commit, "commitSubject": "s",
            "runs": runs if runs is not None else {"bare-triposr": _entry()}}


class TestARecordingCarriesWhatMakesAComparisonMeaningful:

    def test_it_records_the_commit_the_container_was_built_from(self):
        """Without it a difference between two rounds is unattributable, and this pipeline's
        post-generation behaviour has changed repeatedly."""
        recording = _recording()

        assert recording["commit"]

    def test_it_records_the_instance_and_gpu_per_run(self):
        """Two stochastic models produced materially different geometry on different GPU types with
        identical seeds, so a shape difference across rounds may be a hardware difference."""
        entry = _recording()["runs"]["bare-triposr"]

        assert entry["instanceType"] and entry["gpu"]

    def test_the_markdown_names_the_commit_and_every_run(self):
        text = record.to_markdown(_recording({"a": _entry(), "b": _entry(model="sam3d")}))

        assert "c0ffee" in text
        assert "| a |" in text and "| b |" in text
        for stage in ("generate", "cleanup", "weightCache"):
            assert stage in text

    def test_the_markdown_shows_the_shape_descriptors(self):
        text = record.to_markdown(_recording())

        assert "0.936" in text, "the fill ratio should be readable without parsing JSON"


class TestTheComparisonReportsWhatMatters:

    def test_an_identical_round_reports_nothing(self):
        assert record.compare(_recording(), _recording()) == {}

    def test_a_gate_verdict_flip_is_always_reported(self):
        before = _recording()
        after = _recording({"bare-triposr": _entry(gate=dict(_entry()["gate"], passed=False))})

        differences = record.compare(before, after)

        assert any("gate.passed" in d for d in differences["bare-triposr"])

    def test_a_body_count_change_is_reported(self):
        after = _recording({"bare-triposr": _entry(gate=dict(_entry()["gate"], bodies=2))})

        assert any("gate.bodies" in d for d in record.compare(_recording(), after)["bare-triposr"])

    def test_a_texture_status_change_is_reported(self):
        after = _recording({"bare-triposr": _entry(textureStatus="none")})

        assert any("textureStatus" in d
                   for d in record.compare(_recording(), after)["bare-triposr"])

    def test_a_stage_timing_drifting_on_a_shared_queue_is_not_reported(self):
        """A comparison that trips on a 10% timing wobble would be ignored within a week."""
        after = _recording({"bare-triposr": _entry(
            stageSeconds={"generate": 190.0, "cleanup": 119.5, "weightCache": 15.0})})

        assert record.compare(_recording(), after) == {}

    def test_a_stage_timing_doubling_IS_reported(self):
        after = _recording({"bare-triposr": _entry(
            stageSeconds={"generate": 400.0, "cleanup": 119.5, "weightCache": 14.5})})

        assert any("stage.generate" in d
                   for d in record.compare(_recording(), after)["bare-triposr"])

    def test_a_small_shape_change_is_tolerated_and_a_large_one_is_not(self):
        nudged = _recording({"bare-triposr": _entry(
            shape={"fillRatio": 0.945, "footprintAspect": 0.964, "heightOverWidth": 0.890})})
        moved = _recording({"bare-triposr": _entry(
            shape={"fillRatio": 0.500, "footprintAspect": 0.964, "heightOverWidth": 0.890})})

        assert record.compare(_recording(), nudged) == {}
        assert any("shape.fillRatio" in d
                   for d in record.compare(_recording(), moved)["bare-triposr"])

    def test_a_changed_instance_type_is_reported(self):
        """It may be the whole explanation for a shape difference, so it must never be silent."""
        after = _recording({"bare-triposr": _entry(instanceType="g6e.4xlarge", gpu="NVIDIA L40S")})

        differences = record.compare(_recording(), after)["bare-triposr"]
        assert any("instanceType" in d for d in differences)
        assert any("gpu" in d for d in differences)

    def test_a_run_added_or_missing_is_reported_rather_than_ignored(self):
        added = record.compare(_recording(), _recording({"bare-triposr": _entry(), "new": _entry()}))
        removed = record.compare(_recording({"bare-triposr": _entry(), "old": _entry()}), _recording())

        assert "new" in added and "only in the later" in added["new"][0]
        assert "old" in removed and "missing from the later" in removed["old"][0]

    def test_the_repair_ladder_changing_is_reported(self):
        after = _recording({"bare-triposr": _entry(
            gate=dict(_entry()["gate"], backendsApplied=["trimesh"]))})

        assert any("backendsApplied" in d
                   for d in record.compare(_recording(), after)["bare-triposr"])


class TestHarvestReadsThePerExecutionReport:

    def test_it_reads_the_object_the_execution_wrote_not_asset_metadata(self, monkeypatch):
        """Several runs against one asset overwrite each other in metadata, so every row of a recorded
        round would show the last run's numbers while looking entirely plausible."""
        seen = {}

        def _fake(bucket, key, profile, region):
            seen["key"] = key
            return json.dumps({"metadata": [
                {"metadataKey": "IMAGE_TO_3D_BENCHMARK",
                 "metadataValue": json.dumps({"model": "triposr", "stageSeconds": {"generate": 1.0},
                                              "host": {"instanceType": "g5.8xlarge"}})},
                {"metadataKey": "IMAGE_TO_3D_PRINT_GATE_REPORT",
                 "metadataValue": json.dumps({"passed": True, "bodies": 1})}]})

        monkeypatch.setattr(record, "_s3_text", _fake)
        # The jobName segment of the key is listed from S3 rather than hardcoded, because it is derived
        # per workflow record and a stale constant made a whole round report "no report found".
        monkeypatch.setattr(record, "_job_name_prefixes",
                            lambda bucket, profile, region: ["ab123-genai-image-to-3d"])
        harvested = record.harvest("b" * 32, "bucket", "profile", "us-west-2")

        assert "b" * 32 in seen["key"], "the per-execution object must be the source"
        assert "ab123-genai-image-to-3d" in seen["key"], "the discovered jobName must be used"
        assert harvested["model"] == "triposr"
        assert harvested["gate"]["passed"] is True

    def test_a_run_with_no_report_is_left_out_rather_than_recorded_empty(self, monkeypatch):
        monkeypatch.setattr(record, "_s3_text", lambda *a, **k: "")

        assert record.harvest("c" * 32, "bucket", "profile", "us-west-2") is None
