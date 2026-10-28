#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the check that decides whether a model's weights are already cached.

This check runs before any GPU work on every run, and it had two defects that a live round exposed.

It was **slow**: a recursive walk of the whole per-model cache tree, once per weight extension,
collecting every match only to test the list for emptiness -- 477 seconds of a 606-second run on the
network mount, more than the model's own generation took.

It was also **wrong**: it asked whether the cache held *any* weight file rather than whether *this
model's* weights were there. A cache holding one of the two repositories a model needs reported a hit,
the restore from Amazon S3 was skipped, and the missing weights were downloaded during generation
instead -- where the cost reads as a slow model rather than as a cache miss. That second defect is the
one worth pinning with tests, because it is invisible: the run succeeds either way.

The layout under test is the HuggingFace cache's own:
``hub/models--{org}--{name}/snapshots/{revision}/`` holding files that are symlinks into ``blobs/``.
"""

import os
import sys

import pytest

import model_manager


def _cache(root, repo, weight="model.safetensors", revision="abc123"):
    """A HuggingFace-shaped cache holding one repo's snapshot, and its blobs beside it."""
    repo_dir = root / "hub" / ("models--" + repo.replace("/", "--"))
    snapshot = repo_dir / "snapshots" / revision
    snapshot.mkdir(parents=True, exist_ok=True)
    blobs = repo_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    if weight:
        (blobs / "deadbeef").write_bytes(b"weights")
        (snapshot / weight).write_bytes(b"weights")
    return root


@pytest.mark.unit
class TestItAsksAboutTheRightModel:
    def test_a_declared_repo_that_is_cached_is_a_hit(self, tmp_path):
        _cache(tmp_path, "stepfun-ai/Step1X-3D")

        assert model_manager._has_cached_models(str(tmp_path), ("stepfun-ai/Step1X-3D",))

    def test_another_models_weights_are_not_this_models_weights(self, tmp_path):
        """The defect this closes: a hit here skipped the S3 restore and downloaded during generation."""
        _cache(tmp_path, "Stable-X/trellis-normal-v0-1")

        assert not model_manager._has_cached_models(str(tmp_path), ("stepfun-ai/Step1X-3D",))

    def test_every_declared_repo_has_to_be_present(self, tmp_path):
        """A model needing two repositories is not cached when only one of them is."""
        _cache(tmp_path, "facebook/sam-3d-objects")

        assert not model_manager._has_cached_models(
            str(tmp_path), ("facebook/sam-3d-objects", "Ruicheng/moge-vitl"))

        _cache(tmp_path, "Ruicheng/moge-vitl")

        assert model_manager._has_cached_models(
            str(tmp_path), ("facebook/sam-3d-objects", "Ruicheng/moge-vitl"))

    def test_a_snapshot_with_no_weight_file_is_a_miss(self, tmp_path):
        """An interrupted first download leaves exactly this: the directories, and no weights."""
        _cache(tmp_path, "stepfun-ai/Step1X-3D", weight=None)

        assert not model_manager._has_cached_models(str(tmp_path), ("stepfun-ai/Step1X-3D",))

    def test_a_repo_directory_with_no_snapshots_is_a_miss(self, tmp_path):
        repo_dir = tmp_path / "hub" / "models--stepfun-ai--Step1X-3D"
        repo_dir.mkdir(parents=True)

        assert not model_manager._has_cached_models(str(tmp_path), ("stepfun-ai/Step1X-3D",))

    @pytest.mark.parametrize("extension", [".safetensors", ".bin", ".pth", ".ckpt"])
    def test_every_weight_extension_counts(self, tmp_path, extension):
        _cache(tmp_path, "some/model", weight=f"weights{extension}")

        assert model_manager._has_cached_models(str(tmp_path), ("some/model",))

    def test_a_snapshot_holding_only_a_pt_checkpoint_is_a_hit(self, tmp_path):
        """MoGe-2, the Blender agent's depth model, ships its weights as one ``model.pt``."""
        _cache(tmp_path, "Ruicheng/moge-2-vitl-normal", weight="model.pt")

        assert model_manager._has_cached_models(str(tmp_path), ("Ruicheng/moge-2-vitl-normal",))


@pytest.mark.unit
class TestWithNoDeclaredRepos:
    def test_it_falls_back_to_asking_whether_anything_is_cached(self, tmp_path):
        """All that can be asked without knowing the model -- and the old question."""
        _cache(tmp_path, "any/model")

        assert model_manager._has_cached_models(str(tmp_path))

    def test_an_empty_hub_is_still_a_miss(self, tmp_path):
        (tmp_path / "hub").mkdir()

        assert not model_manager._has_cached_models(str(tmp_path))


@pytest.mark.unit
class TestTheAbsentCases:
    def test_a_missing_hf_home_is_a_miss(self, tmp_path):
        assert not model_manager._has_cached_models(str(tmp_path / "nothing-here"), ("a/b",))

    def test_an_hf_home_without_a_hub_directory_is_a_miss(self, tmp_path):
        assert not model_manager._has_cached_models(str(tmp_path), ("a/b",))


@pytest.mark.unit
class TestItStopsEarly:
    def test_the_first_weight_file_ends_the_search(self, tmp_path):
        """The cost defect: the old check collected every match before deciding.

        Counted through os.walk, which is what the search uses: a snapshot holding many files must not
        be walked past the directory that answers the question.
        """
        root = _cache(tmp_path, "some/model")
        snapshot = root / "hub" / "models--some--model" / "snapshots" / "abc123"
        deeper = snapshot / "unet" / "nested"
        deeper.mkdir(parents=True)
        for index in range(50):
            (deeper / f"extra-{index}.safetensors").write_bytes(b"x")

        walked = []
        real_walk = os.walk

        def counting_walk(top, *args, **kwargs):
            for entry in real_walk(top, *args, **kwargs):
                walked.append(entry[0])
                yield entry

        model_manager.os.walk = counting_walk
        try:
            found = model_manager._first_weight_file(snapshot)
        finally:
            model_manager.os.walk = real_walk

        assert found is not None
        # The weight at the top of the snapshot answers it, so the nested directories are never reached.
        assert len(walked) == 1, walked


@pytest.mark.unit
class TestFetchingARepoSurvivesTheSharedFilesystem:
    """The cold download writes onto an NFS mount, and that write has failed mid-stream three times.

    `OSError: [Errno 116] Stale file handle` ended two concurrent jobs fetching one repository and one
    job running alone, and left a partial blob behind that a later resume read as valid -- which is how a
    neighbouring model's state_dict was corrupted. So a retry has to discard the partial download rather
    than continue it, and only the transient faults may be retried: a gated repository or a full disk is a
    real answer, and retrying it just delays the error by a minute.
    """

    def _partial(self, cache_dir, repo, name="deadbeef.incomplete"):
        blobs = cache_dir / ("models--" + repo.replace("/", "--")) / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        (blobs / name).write_bytes(b"half a tensor")
        return blobs / name

    def _patched_download(self, monkeypatch, side_effects):
        """Install a fake snapshot_download that yields each side effect in turn."""
        calls = []
        remaining = list(side_effects)

        def fake_snapshot_download(repo_id, cache_dir, token=None, **kwargs):
            calls.append(repo_id)
            outcome = remaining.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        module = type(sys)("huggingface_hub")
        module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", module)
        monkeypatch.setattr(model_manager, "FETCH_RETRY_BACKOFF_SECONDS", 0)
        return calls

    def test_a_stale_handle_is_retried_and_the_partial_download_discarded(
            self, tmp_path, monkeypatch):
        stale = OSError(116, "Stale file handle")
        calls = self._patched_download(monkeypatch, [stale, "/cache/snapshots/abc"])
        partial = self._partial(tmp_path, "facebook/sam-vit-huge")

        assert model_manager.fetch_repo(
            "facebook/sam-vit-huge", cache_dir=str(tmp_path)) == "/cache/snapshots/abc"
        assert len(calls) == 2
        assert not partial.exists(), "resuming into a partial download is what corrupted weights before"

    def test_a_failure_that_is_not_transient_is_raised_immediately(self, tmp_path, monkeypatch):
        no_space = OSError(28, "No space left on device")
        calls = self._patched_download(monkeypatch, [no_space])

        with pytest.raises(OSError):
            model_manager.fetch_repo("facebook/sam-vit-huge", cache_dir=str(tmp_path))
        assert len(calls) == 1, "a full disk is a real answer, not something to wait out"

    def test_exhausting_the_attempts_names_the_filesystem_rather_than_the_repo(
            self, tmp_path, monkeypatch):
        stale = OSError(116, "Stale file handle")
        calls = self._patched_download(monkeypatch, [stale] * model_manager.FETCH_ATTEMPTS)

        with pytest.raises(RuntimeError, match="filesystem failing mid-write"):
            model_manager.fetch_repo("facebook/sam-vit-huge", cache_dir=str(tmp_path))
        assert len(calls) == model_manager.FETCH_ATTEMPTS

    def test_a_repo_that_downloads_first_time_costs_one_call(self, tmp_path, monkeypatch):
        calls = self._patched_download(monkeypatch, ["/cache/snapshots/abc"])

        assert model_manager.fetch_repo(
            "briaai/RMBG-2.0", cache_dir=str(tmp_path)) == "/cache/snapshots/abc"
        assert calls == ["briaai/RMBG-2.0"]
