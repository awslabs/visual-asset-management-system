#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the check that decides whether a run's model weights are already cached.

This runs before any GPU work on every NVIDIA pipeline run, and it had three defects that only showed up
once the shared filesystem was actually mounted.

It asked whether the cache held **any** weight file rather than whether **this run's** model was there.
Cosmos Predict serves 2B and 14B, Cosmos Reason 2B and 8B, Cosmos 3 Nano and Super -- all from one cache
directory -- so running one variant after another found the other's weights, reported a hit, skipped the
restore from Amazon S3, and let HuggingFace download the wanted variant during inference instead. On
billed GPU time, on every run, and reading as a slow model rather than as a cache miss. That is the defect
worth pinning with tests, because the run succeeds either way.

It was also **slow**: ``list(hub_dir.rglob(f"*{ext}"))`` walked the whole tree once per weight extension
and built a list of every match only to test it for emptiness, over a network filesystem. Measured on the
Image to 3D pipeline, which had the same check: 477 seconds of a 606-second run.

And all four Cosmos pipelines wrote to **one** cache directory while each restored from a different S3
prefix, so the first pipeline to run made every other one read a false hit.

Every pipeline's copy is exercised, because the copies have diverged and a fix applied to four of five is
the same defect with a smaller blast radius.
"""

import pathlib

import pytest


@pytest.mark.unit
class TestItAsksAboutTheRunsOwnModel:
    def test_a_declared_repo_that_is_cached_is_a_hit(self, model_manager, hf_cache):
        root = hf_cache("nvidia/Cosmos-Reason2-2B")

        assert model_manager._has_cached_models(str(root), ("nvidia/Cosmos-Reason2-2B",))

    def test_another_variants_weights_are_not_this_variants_weights(self, model_manager, hf_cache):
        """The defect this closes: a hit here skipped the restore and downloaded during inference."""
        root = hf_cache("nvidia/Cosmos-Reason2-2B")

        assert not model_manager._has_cached_models(str(root), ("nvidia/Cosmos-Reason2-8B",))

    def test_a_sibling_pipelines_weights_are_not_this_pipelines_weights(self, model_manager, hf_cache):
        """The four Cosmos pipelines shared one directory; each restores from its own S3 prefix."""
        root = hf_cache("nvidia/Cosmos-Transfer2.5-2B")

        assert not model_manager._has_cached_models(str(root), ("nvidia/Cosmos-Predict2.5-2B",))

    def test_every_declared_repo_has_to_be_present(self, model_manager, hf_cache):
        root = hf_cache("nvidia/Cosmos-Predict2.5-2B")
        wanted = ("nvidia/Cosmos-Predict2.5-2B", "nvidia/Cosmos-Reason1-7B")

        assert not model_manager._has_cached_models(str(root), wanted)

        hf_cache("nvidia/Cosmos-Reason1-7B")

        assert model_manager._has_cached_models(str(root), wanted)

    def test_a_snapshot_with_no_weight_file_is_a_miss(self, model_manager, hf_cache):
        """An interrupted first download leaves exactly this: the directories, and no weights."""
        root = hf_cache("nvidia/Cosmos3-Nano", weight=None)

        assert not model_manager._has_cached_models(str(root), ("nvidia/Cosmos3-Nano",))

    def test_a_repo_directory_with_no_snapshots_is_a_miss(self, model_manager, tmp_path):
        (tmp_path / "hub" / "models--nvidia--Cosmos3-Super").mkdir(parents=True)

        assert not model_manager._has_cached_models(str(tmp_path), ("nvidia/Cosmos3-Super",))

    @pytest.mark.parametrize("extension", [".safetensors", ".bin", ".pth", ".ckpt"])
    def test_every_weight_extension_counts(self, model_manager, hf_cache, extension):
        root = hf_cache("nvidia/GR00T-N1.5-3B", weight=f"weights{extension}")

        assert model_manager._has_cached_models(str(root), ("nvidia/GR00T-N1.5-3B",))

    def test_a_weight_nested_inside_a_snapshot_counts(self, model_manager, hf_cache):
        """Sharded weights sit in subdirectories of the snapshot, not at its top level."""
        root = hf_cache("nvidia/Cosmos3-Nano", weight=None)
        nested = root / "hub" / "models--nvidia--Cosmos3-Nano" / "snapshots" / "abc123" / "transformer"
        nested.mkdir(parents=True)
        (nested / "diffusion_pytorch_model.safetensors").write_bytes(b"weights")

        assert model_manager._has_cached_models(str(root), ("nvidia/Cosmos3-Nano",))


@pytest.mark.unit
class TestWithNoDeclaredRepo:
    def test_it_falls_back_to_asking_whether_anything_is_cached(self, model_manager, hf_cache):
        """All that can be asked without knowing the model -- and the question it used to ask always."""
        root = hf_cache("any/model")

        assert model_manager._has_cached_models(str(root))

    def test_an_empty_hub_is_still_a_miss(self, model_manager, tmp_path):
        (tmp_path / "hub").mkdir()

        assert not model_manager._has_cached_models(str(tmp_path))


@pytest.mark.unit
class TestTheAbsentCases:
    def test_a_missing_hf_home_is_a_miss(self, model_manager, tmp_path):
        assert not model_manager._has_cached_models(str(tmp_path / "nothing-here"), ("a/b",))

    def test_an_hf_home_without_a_hub_directory_is_a_miss(self, model_manager, tmp_path):
        assert not model_manager._has_cached_models(str(tmp_path), ("a/b",))


@pytest.mark.unit
class TestTheCostDefect:
    def test_the_search_stops_inside_the_declared_repo(self, model_manager, hf_cache):
        """The old check walked the whole cache per extension; this one opens one repo's snapshots.

        Asserted by leaving a large unrelated repo in the cache and requiring that the declared repo's
        answer does not depend on it: the walk is scoped to the repo, so an unrelated tree cannot be
        visited whatever its size.
        """
        root = hf_cache("nvidia/Cosmos-Reason2-2B")
        noise = root / "hub" / "models--unrelated--enormous" / "snapshots" / "rev"
        noise.mkdir(parents=True)
        for index in range(200):
            (noise / f"shard-{index}.safetensors").write_bytes(b"x")

        assert model_manager._has_cached_models(str(root), ("nvidia/Cosmos-Reason2-2B",))
        assert not model_manager._has_cached_models(str(root), ("nvidia/Cosmos-Reason2-8B",))


@pytest.mark.unit
class TestTheCopiesAgree:
    """The five copies have diverged, so the shared logic is asserted to be identical text.

    Without this, the behaviour above is proven for each copy as it stands today and nothing stops the
    next edit landing in one of them. It is the same rule the vendored ``manifestHelper.py`` copies carry.
    """

    def test_the_per_repo_check_is_identical_in_every_pipeline(self):
        from conftest import MODEL_MANAGERS  # noqa: PLC0415 -- the fixture module holds the map

        def extract(path: pathlib.Path) -> str:
            text = path.read_text(encoding="utf-8")
            start = text.index("def _repo_cache_dir(")
            end = text.index("def _has_cached_models(")
            return text[start:end]

        bodies = {name: extract(path) for name, path in MODEL_MANAGERS.items()}
        distinct = set(bodies.values())
        assert len(distinct) == 1, (
            "the per-repository cache check has diverged between pipelines: "
            + ", ".join(sorted(bodies))
        )

    def test_every_pipeline_declares_its_own_s3_prefix(self):
        """One shared filesystem, one directory per pipeline -- so the prefixes must all differ."""
        from conftest import MODEL_MANAGERS, load_model_manager  # noqa: PLC0415

        prefixes = {name: load_model_manager(path).S3_HF_CACHE_PREFIX
                    for name, path in MODEL_MANAGERS.items()}
        assert len(set(prefixes.values())) == len(prefixes), f"prefixes collide: {prefixes}"
        for name, prefix in prefixes.items():
            assert prefix.startswith(name + "/"), f"{name} backs up to {prefix}"
