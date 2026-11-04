#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The SAM 3D Objects pipeline configuration is read from inside the weights snapshot.

The snapshot directory is named for the repo revision, so the configuration's path cannot be
constructed from the repo id -- it has to come from whatever ``snapshot_download`` resolved to.
Upstream's ``Inference`` then sets its workspace directory to the configuration file's own parent
and resolves every checkpoint the yaml names relative to it, so a path outside the snapshot loads
no weights.
"""

import pytest

from models.sam3d import Sam3dAdapter

REVISION = "2e73555018d2741ccd486e56c24fac41155a1dc6"  # pragma: allowlist secret


def _snapshot(tmp_path, *relpaths):
    """A stand-in for a resolved HuggingFace snapshot holding ``relpaths``."""
    root = tmp_path / "hub" / "models--facebook--sam-3d-objects" / "snapshots" / REVISION
    for relpath in relpaths:
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub", encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _adapter(snapshot_dir):
    adapter = Sam3dAdapter()
    adapter._snapshot_dirs[Sam3dAdapter.CONFIG_REPO] = str(snapshot_dir)
    return adapter


@pytest.mark.unit
class TestTheConfigurationComesFromTheSnapshot:

    def test_it_resolves_to_the_configuration_the_repo_ships(self, tmp_path):
        snapshot = _snapshot(tmp_path, "checkpoints/pipeline.yaml", "checkpoints/ss_encoder.yaml")

        resolved = _adapter(snapshot).resolve_config_path({})

        assert resolved == str(snapshot / "checkpoints" / "pipeline.yaml")

    def test_the_configuration_sits_beside_the_checkpoints_it_names(self, tmp_path):
        # Inference derives its workspace from the configuration's parent, so the checkpoints the
        # yaml references have to be siblings of it for the run to find them.
        snapshot = _snapshot(
            tmp_path, "checkpoints/pipeline.yaml", "checkpoints/slat_decoder_mesh.ckpt")
        from pathlib import Path

        resolved = Path(_adapter(snapshot).resolve_config_path({}))

        assert (resolved.parent / "slat_decoder_mesh.ckpt").is_file()

    def test_the_path_is_not_assembled_from_the_repo_id(self, tmp_path):
        # The bug this replaced: a path built from the repo checkout plus a made-up checkpoint tag,
        # which no snapshot ever contains.
        snapshot = _snapshot(tmp_path, "checkpoints/pipeline.yaml")

        resolved = _adapter(snapshot).resolve_config_path({})

        assert Sam3dAdapter.REPO_DIR not in resolved
        assert REVISION in resolved


@pytest.mark.unit
class TestARelocatedConfigurationIsRecoverable:

    def test_an_operator_can_point_at_another_path_in_the_repo(self, tmp_path):
        snapshot = _snapshot(tmp_path, "checkpoints/v2/pipeline.yaml")

        resolved = _adapter(snapshot).resolve_config_path(
            {"SAM3D_CONFIG_RELPATH": "checkpoints/v2/pipeline.yaml"})

        assert resolved == str(snapshot / "checkpoints" / "v2" / "pipeline.yaml")

    def test_a_missing_configuration_names_what_the_snapshot_does_carry(self, tmp_path):
        # The repo is gated, so an operator cannot list it without accepting the licence first: the
        # error has to report the layout rather than only the path that failed.
        snapshot = _snapshot(tmp_path, "checkpoints/renamed_pipeline.yaml",
                             "checkpoints/ss_encoder.yaml", "checkpoints/ss_encoder.ckpt")

        with pytest.raises(RuntimeError) as excinfo:
            _adapter(snapshot).resolve_config_path({})

        message = str(excinfo.value)
        assert "checkpoints/pipeline.yaml" in message
        assert "checkpoints/renamed_pipeline.yaml" in message.replace("\\", "/")
        assert "SAM3D_CONFIG_RELPATH" in message
        # A checkpoint is not a candidate configuration and would be noise in the listing.
        assert "ss_encoder.ckpt" not in message

    def test_an_empty_snapshot_says_so_rather_than_listing_nothing(self, tmp_path):
        snapshot = _snapshot(tmp_path)

        with pytest.raises(RuntimeError, match="none"):
            _adapter(snapshot).resolve_config_path({})


@pytest.mark.unit
class TestPrefetchSuppliesTheSnapshotDirectory:

    def test_the_directory_recorded_while_prefetching_is_what_generation_reads(self, tmp_path):
        # ensure_ready pre-fetches before generation and records where each repo landed; that record
        # is the only thing tying the run to the right revision.
        snapshot = _snapshot(tmp_path, "checkpoints/pipeline.yaml")
        adapter = Sam3dAdapter()
        adapter._snapshot_dirs[Sam3dAdapter.CONFIG_REPO] = str(snapshot)

        assert adapter.snapshot_dir(Sam3dAdapter.CONFIG_REPO) == str(snapshot)

    def test_the_depth_model_the_configuration_names_is_prefetched_too(self):
        # The configuration instantiates MoGe through hydra, so no import in this repo reaches it and
        # no gate here fails without it: an unlisted repo is downloaded partway through inference, on
        # a GPU instance, and surfaces as a hydra instantiation error.
        assert "Ruicheng/moge-vitl" in Sam3dAdapter.HF_REPOS

    def test_the_configuration_is_read_from_a_repo_that_is_actually_fetched(self):
        assert Sam3dAdapter.CONFIG_REPO in Sam3dAdapter.HF_REPOS

    def test_each_adapter_instance_starts_with_no_recorded_snapshots(self):
        # Recorded per instance rather than on the class: a stale directory shared between runs
        # would point a run at the previous revision.
        assert Sam3dAdapter()._snapshot_dirs == {}
