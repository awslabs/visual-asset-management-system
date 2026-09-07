#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The video this container uploads is identified by name, not by whichever .mp4 the walk reached first.

`find_output_video` returned the first `rglob("*.mp4")` hit. `rglob` yields directory order, which is
filesystem-dependent rather than sorted, so a second .mp4 anywhere under the output directory made the
choice arbitrary -- and the file it picks is uploaded as the run's generated video, exit 0, execution
SUCCESS. With `aux_debug.mp4` beside `vams_inference.mp4`, the old code uploaded **aux_debug.mp4**.

This container does not need a heuristic to resolve that, because it CHOOSES the name: it submits one
inference sample called `INFERENCE_SAMPLE_NAME`, and the framework writes each sample's video as
`{name}.mp4`. So the expected file is identified positively, and the search is a fallback for the case
the expectation is not met -- which matters because the upstream repository is cloned at build time
with no pinned revision, so a rename upstream must not read as "no video generated".

Two things deliberately NOT carried over from the Cosmos 3 container, whose defect was the same shape:

*   **The extras are not uploaded.** Cosmos 3's transfer mode legitimately leaves a computed control
    video beside the generated one, so uploading every candidate was the only way not to lose one.
    Predict writes exactly one video per sample and submits one sample, so a second .mp4 has no
    reachable source today; inventing an upload naming scheme for it would add a contract nobody
    calls. A candidate set larger than one is logged in full instead.
*   **The (mtime, size, path) ordering is not reused.** It was derived from Cosmos 3's output shape.
    Here the fallback is plain sorted order -- reproducible, and reached only in the abnormal case.

SCOPE, stated rather than implied: with the configuration this container builds, exactly one .mp4 is
written, so the defect is LATENT -- the tests below construct the two-file case. What makes it worth
closing anyway is that reaching it needs only an upstream change to a repository this image clones
unpinned.
"""

import pytest

from conftest import ASSET_BUCKET, ASSET_ID, base_definition, video2world_definition


# ============================ the expected video is identified by name ============================

class TestTheExpectedVideoIsChosen:

    def test_a_single_expected_video_is_uploaded(self, run_container, container):
        record = run_container(base_definition(),
                               artifacts=(f"{container.INFERENCE_SAMPLE_NAME}.mp4",))
        assert record.uploaded_files == [f"{container.INFERENCE_SAMPLE_NAME}.mp4"]
        assert len(record.output_keys) == 1

    def test_a_stray_video_sorting_first_does_not_win(self, run_container, container):
        """The measured old behaviour: `aux_debug.mp4` was uploaded as the generated video."""
        record = run_container(
            base_definition(),
            artifacts=("aux_debug.mp4", f"{container.INFERENCE_SAMPLE_NAME}.mp4"))
        assert record.uploaded_files == [f"{container.INFERENCE_SAMPLE_NAME}.mp4"]

    def test_a_stray_video_sorting_last_does_not_win(self, run_container, container):
        """Paired with the test above so the result is not an artifact of alphabetical luck: whichever
        side the stray falls on, the expected file is the one uploaded."""
        record = run_container(
            base_definition(),
            artifacts=(f"{container.INFERENCE_SAMPLE_NAME}.mp4", "zz_extra.mp4"))
        assert record.uploaded_files == [f"{container.INFERENCE_SAMPLE_NAME}.mp4"]

    def test_the_expected_video_is_found_inside_a_subdirectory(self, run_container, container):
        """The search stays recursive: the name is what identifies the file, not its depth."""
        record = run_container(base_definition(),
                               artifacts=(f"runs/{container.INFERENCE_SAMPLE_NAME}.mp4",))
        assert record.uploaded_files == [f"{container.INFERENCE_SAMPLE_NAME}.mp4"]

    def test_a_stray_at_the_root_loses_to_the_expected_video_in_a_subdirectory(
            self, run_container, container):
        """The case a depth-first walk gets wrong in the other direction."""
        record = run_container(
            base_definition(),
            artifacts=("aux_debug.mp4", f"runs/{container.INFERENCE_SAMPLE_NAME}.mp4"))
        assert record.uploaded_files == [f"{container.INFERENCE_SAMPLE_NAME}.mp4"]

    def test_only_one_object_is_uploaded_when_several_videos_exist(self, run_container, container):
        """The extras are logged, not published -- see this module's header for why."""
        record = run_container(
            base_definition(),
            artifacts=("aux_debug.mp4", f"{container.INFERENCE_SAMPLE_NAME}.mp4", "zz_extra.mp4"))
        assert len(record.uploads) == 1


# ============================ the sidecars are not videos ============================

class TestSidecarsAreNeverSelected:

    def test_the_frameworks_json_and_yaml_sidecars_are_not_candidates(
            self, run_container, container):
        """`{sample}.json` and `config.yaml` are written beside the video on every run, so a selection
        by anything other than extension would reach them."""
        record = run_container(base_definition(),
                               artifacts=(f"{container.INFERENCE_SAMPLE_NAME}.mp4",))
        assert record.uploaded_files == [f"{container.INFERENCE_SAMPLE_NAME}.mp4"]

    def test_a_directory_named_like_a_video_is_not_a_candidate(self, container, tmp_path):
        """`rglob("*.mp4")` yields directories too, so the is_file() check is load-bearing."""
        (tmp_path / "frames.mp4").mkdir()
        (tmp_path / "frames.mp4" / f"{container.INFERENCE_SAMPLE_NAME}.mp4").write_bytes(b"x")
        found = container.find_output_video(tmp_path)
        assert found is not None and found.name == f"{container.INFERENCE_SAMPLE_NAME}.mp4"
        assert found.is_file()


# ============================ the fallback, for upstream drift ============================

class TestFallbackWhenTheExpectedNameIsAbsent:
    """The upstream repository is cloned with no pinned revision, so its artifact naming can change
    under a rebuild. A rename must not turn a successful generation into a failed run."""

    def test_a_renamed_artifact_is_still_uploaded(self, run_container):
        record = run_container(base_definition(), artifacts=("generated_video.mp4",))
        assert record.uploaded_files == ["generated_video.mp4"]
        assert len(record.output_keys) == 1

    def test_the_fallback_is_sorted_and_not_directory_order(self, container, tmp_path):
        """Two unexpected names: the choice is the sorted first, so it is reproducible rather than
        whatever the filesystem enumerates first."""
        for name in ("b_second.mp4", "a_first.mp4"):
            (tmp_path / name).write_bytes(b"x")
        assert container.find_output_video(tmp_path).name == "a_first.mp4"

    def test_the_fallback_choice_does_not_depend_on_creation_order(self, container, tmp_path):
        """The same two names created in the opposite order still resolve to the same file."""
        for name in ("a_first.mp4", "b_second.mp4"):
            (tmp_path / name).write_bytes(b"x")
        assert container.find_output_video(tmp_path).name == "a_first.mp4"

    def test_no_video_at_all_still_fails_the_run(self, run_container):
        """A blocked guardrail writes the sidecar and no video; that must stay a failure rather than
        become a silent success."""
        with pytest.raises(RuntimeError, match="No output video generated"):
            run_container(base_definition(), artifacts=())
        assert run_container.last.uploads == []

    def test_an_empty_directory_returns_none(self, container, tmp_path):
        assert container.find_output_video(tmp_path) is None


# ============================ no drift between the name and the config ============================

class TestTheSampleNameIsOneConstant:
    """The whole selection rests on the container knowing the name it asked the framework to use. Two
    literals would drift silently: the config would name one sample and the search would look for
    another, sending every run down the fallback path."""

    def test_the_inference_config_carries_the_shared_constant(self, inference_module):
        config = inference_module.build_inference_config(
            inference_type="text2world", prompt="a robot arm")
        assert config["name"] == inference_module.INFERENCE_SAMPLE_NAME

    def test_the_entrypoint_and_the_inference_module_agree(self, container, inference_module):
        assert container.INFERENCE_SAMPLE_NAME == inference_module.INFERENCE_SAMPLE_NAME

    def test_the_constant_is_a_usable_file_stem(self, container):
        """It becomes a file name, so it may not be blank or carry a path separator."""
        name = container.INFERENCE_SAMPLE_NAME
        assert name and name.strip() == name
        assert "/" not in name and "\\" not in name and not name.endswith(".mp4")

    def test_a_video2world_config_carries_it_too(self, inference_module):
        config = inference_module.build_inference_config(
            inference_type="video2world", prompt="", input_file_path="/tmp/input/source.mp4")
        assert config["name"] == inference_module.INFERENCE_SAMPLE_NAME
        assert config["input_path"] == "/tmp/input/source.mp4"


# ============================ the ordinary runs are unchanged ============================

class TestOrdinaryRunsAreUnaffected:
    """A selection rule that rejected the normal case would satisfy every test above while making the
    pipeline unusable."""

    def test_text2world_names_the_output_after_the_pipeline(self, run_container):
        record = run_container(base_definition())
        assert record.output_keys[0].startswith("cosmos-predict2-text2world-")
        assert record.output_keys[0].endswith(".mp4")

    def test_video2world_names_the_output_after_the_input_file(self, run_container):
        record = run_container(video2world_definition())
        assert record.output_keys[0].startswith("source_CosmosPredictV2Video2World_")
        assert record.downloads == [f"s3://{ASSET_BUCKET}/{ASSET_ID}/source.mp4"]

    def test_video2world_preserves_the_inputs_relative_subdirectory(self, run_container):
        """Asset files live at {assetId}/{relative}/{name}, and process-output expects the output at
        the same relative location."""
        record = run_container(video2world_definition(
            inputS3AssetFilePath=f"s3://{ASSET_BUCKET}/{ASSET_ID}/scans/day1/source.mp4"))
        assert record.output_keys[0].startswith("scans/day1/source_CosmosPredictV2Video2World_")

    def test_the_run_reaches_the_model_restore_and_the_cache_backup(self, run_container):
        record = run_container(base_definition())
        assert len(record.model_restores) == 1
        assert len(record.cache_backups) == 1
