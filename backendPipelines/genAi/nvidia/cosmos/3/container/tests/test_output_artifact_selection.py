#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every artifact a run produces is uploaded, and which one is PRIMARY is a stated rule.

`find_output_file` returned the first `rglob` hit. `rglob` yields directory order, which is
filesystem-dependent rather than sorted, so with more than one candidate the upload was whichever file
the walk reached first. With `control_edge.mp4` and `generated_video.mp4` in the output directory it
uploaded **control_edge.mp4** under the output name and discarded the generated video, reporting
success. For a generative pipeline whose artifact set
varies by mode -- a transfer run gets the control video the framework computed alongside the generated
one -- that is a run that looks fine and delivered the wrong file.

Two things replace it. Every candidate is uploaded, so nothing is silently dropped; and the primary --
the one carrying the asset-facing name and the preview -- is chosen by an explicit order (newest, then
largest, then path) that is logged along with every candidate.

Uploading the extras rather than failing on a count above one is deliberate: the framework's artifact
set is not fixed, so failing would break the modes that legitimately leave several files, while
uploading loses nothing in either case.
"""

import os

import pytest

from conftest import ASSET_BUCKET, ASSET_ID, base_definition, transfer_definition

FILES_PREFIX = "pipelines/cosmos3/JOB/output/E1/files/"


def keys(record):
    prefix = f"s3://{ASSET_BUCKET}/{FILES_PREFIX}"
    assert all(uri.startswith(prefix) for uri in record.upload_uris), record.upload_uris
    return [uri[len(prefix):] for uri in record.upload_uris]


# ============================ nothing is discarded ============================

class TestEveryArtifactIsUploaded:

    def test_a_single_artifact_produces_a_single_upload(self, run_container):
        record = run_container(base_definition(), artifacts=("video.mp4",))
        assert len(keys(record)) == 1
        assert keys(record)[0].startswith("cosmos3-nano-")

    def test_two_artifacts_both_reach_the_asset(self, run_container):
        """The case that used to lose a file: one of these two was uploaded, the other dropped."""
        record = run_container(
            base_definition(), artifacts=("control_edge.mp4", "generated_video.mp4"))
        assert len(record.uploads) == 2
        assert set(record.uploaded_files) == {"control_edge.mp4", "generated_video.mp4"}

    def test_the_extra_artifact_is_named_after_itself(self, run_container):
        """So an operator looking at the asset can tell which file was which."""
        record = run_container(
            base_definition(), artifacts=("control_edge.mp4", "generated_video.mp4"))
        extra = [key for key in keys(record) if key.endswith("_control_edge.mp4")]
        assert len(extra) == 1, keys(record)

    def test_artifacts_in_subdirectories_are_flattened_into_the_name(self, run_container):
        """The workflow's own path extension is what separates runs, so a container-side folder would
        show up as a stray level inside every asset."""
        record = run_container(
            base_definition(), artifacts=("runs/1/first.mp4", "runs/2/second.mp4"))
        assert len(record.uploads) == 2
        assert not any("/" in key for key in keys(record)), keys(record)
        assert any(key.endswith("_runs_1_first.mp4") for key in keys(record)), keys(record)

    def test_a_sole_artifact_in_a_subdirectory_keeps_the_flat_primary_name(self, run_container):
        record = run_container(base_definition(), artifacts=("runs/1/only.mp4",))
        assert keys(record) == [keys(record)[0]] and "/" not in keys(record)[0]
        assert keys(record)[0].startswith("cosmos3-nano-")

    def test_no_artifact_at_all_still_fails_the_run(self, run_container):
        with pytest.raises(RuntimeError, match="No output file generated"):
            run_container(base_definition(), artifacts=())
        assert run_container.last.uploads == []


# ============================ the primary is chosen by the stated rule ============================

class TestPrimarySelectionIsDeterministic:
    """The harness gives the artifacts increasing modification times in the order named and identical
    sizes, so the newest is the LAST one named -- which is what makes these assertions about the
    ordering rule rather than about directory order or alphabetical luck."""

    def test_the_newest_artifact_is_the_primary(self, run_container):
        record = run_container(
            base_definition(), artifacts=("control_edge.mp4", "generated_video.mp4"))
        assert record.uploaded_files[0] == "generated_video.mp4"

    def test_the_newer_of_two_names_wins_when_it_sorts_last(self, run_container):
        """Paired with the test below: the same two names in the opposite write order. An
        alphabetical implementation returns `a.mp4` in both, so only the pair is conclusive."""
        record = run_container(base_definition(), artifacts=("a.mp4", "b.mp4"))
        assert record.uploaded_files[0] == "b.mp4"

    def test_the_newer_of_two_names_wins_when_it_sorts_first(self, run_container):
        record = run_container(base_definition(), artifacts=("b.mp4", "a.mp4"))
        assert record.uploaded_files[0] == "a.mp4"

    def test_the_full_candidate_list_is_returned_in_that_order(self, container, tmp_path):
        """`find_output_files` itself, so the order is pinned independently of what main() does with
        it."""
        for index, name in enumerate(("one.mp4", "two.mp4", "three.mp4")):
            artifact = tmp_path / name
            artifact.write_bytes(b"x")
            os.utime(artifact, (1_700_000_000 + index, 1_700_000_000 + index))
        found = container.find_output_files(tmp_path, (".mp4",))
        assert [f.name for f in found] == ["three.mp4", "two.mp4", "one.mp4"]

    def test_a_file_of_another_extension_is_not_a_candidate(self, container, tmp_path):
        (tmp_path / "notes.txt").write_bytes(b"x")
        (tmp_path / "video.mp4").write_bytes(b"x")
        assert [f.name for f in container.find_output_files(tmp_path, (".mp4",))] == ["video.mp4"]

    def test_a_directory_named_like_an_artifact_is_not_a_candidate(self, container, tmp_path):
        """`rglob("*")` yields directories too, so the file check is load-bearing."""
        (tmp_path / "frames.mp4").mkdir()
        (tmp_path / "frames.mp4" / "real.mp4").write_bytes(b"x")
        assert [f.name for f in container.find_output_files(tmp_path, (".mp4",))] == ["real.mp4"]

    def test_an_extension_match_is_case_insensitive(self, container, tmp_path):
        (tmp_path / "video.MP4").write_bytes(b"x")
        assert len(container.find_output_files(tmp_path, (".mp4",))) == 1


# ============================ image output and input-file modes ============================

class TestImageAndInputFileModes:

    def test_an_image_mode_uploads_the_image_and_leaves_a_stray_video(self, run_container):
        record = run_container(
            base_definition(taskMode="text2image"), artifacts=("video.mp4", "frame.png"))
        assert record.uploaded_files == ["frame.png"]
        assert keys(record)[0].endswith(".png")

    def test_an_image_mode_uploads_every_image_it_finds(self, run_container):
        record = run_container(
            base_definition(taskMode="text2image"), artifacts=("first.webp", "second.png"))
        assert record.uploaded_files == ["second.png", "first.webp"]

    def test_an_input_file_mode_names_the_primary_after_the_input(self, run_container):
        record = run_container(transfer_definition(), artifacts=("out.mp4",))
        assert keys(record)[0].startswith("source_Cosmos3_nano_")

    def test_an_input_file_modes_extras_stay_beside_the_primary(self, run_container):
        record = run_container(
            transfer_definition(), artifacts=("control_edge.mp4", "generated.mp4"))
        assert len(record.uploads) == 2
        assert all(key.startswith("source_Cosmos3_nano_") for key in keys(record)), keys(record)

    def test_the_inputs_relative_subdirectory_is_preserved_for_every_artifact(self, run_container):
        """Asset files live at {assetId}/{relative}/{name}, and process-output expects an output at the
        same relative location -- for the extras as much as for the primary."""
        record = run_container(
            transfer_definition(
                inputS3AssetFilePath=f"s3://{ASSET_BUCKET}/{ASSET_ID}/scans/day1/source.mp4"),
            artifacts=("control_edge.mp4", "generated.mp4"))
        assert len(record.uploads) == 2
        assert all(key.startswith("scans/day1/") for key in keys(record)), keys(record)
