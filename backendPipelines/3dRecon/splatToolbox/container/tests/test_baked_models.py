#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the segmentation models the splatToolbox image carries and how a run reaches them.

Background removal and object removal used to have no model in the image at all: the launch sequence
created a deliberately EMPTY ``models.tar.gz`` and left ``backgroundremover`` and the SAM2 wrapper to
fetch their weights themselves. That is a download on the data path in a deployment whose Batch
subnets have egress, and an option that fails after the GPU node is up in one that does not.
``vams_bake_models.py --bake`` puts them in the image at build time; ``stage_baked_models`` makes the
SAM2 checkpoint reachable under the MODEL_PATH a run actually uses, which the launch sequence
repoints at the volume it unpacks inputs on.
"""

import os
import shutil
import sys

import pytest

import vams_bake_models


@pytest.fixture
def recorded_download(monkeypatch):
    """``download`` replaced by a writer that really creates each file and records each URL.

    A stub that recorded without writing would leave every "the model is in the image" assertion below
    passing against a directory that is empty.
    """
    requested = []

    def fake_download(url, dest):
        requested.append((url, str(dest)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"bytes-of:{os.path.basename(str(dest))}".encode("utf-8"))

    monkeypatch.setattr(vams_bake_models, "download", fake_download)
    return requested


@pytest.fixture
def baked_image_dir(tmp_path):
    """An image directory holding the baked SAM2 checkpoint."""
    baked_dir = tmp_path / "image-model"
    baked_dir.mkdir()
    (baked_dir / vams_bake_models.SAM2_CHECKPOINT_NAME).write_bytes(b"checkpoint-bytes")
    return baked_dir


@pytest.fixture
def run_model_dir(tmp_path):
    """The MODEL_PATH a run uses, which the launch sequence points at the input volume."""
    model_path = tmp_path / "run-model"
    model_path.mkdir()
    return model_path


@pytest.mark.unit
class TestBakeIntoImage:
    def test_the_u2net_weights_land_in_the_directory_u2net_path_names(
            self, tmp_path, recorded_download):
        """``backgroundremover`` reads ``U2NET_PATH`` directly, so the assembled ``.pth`` files have to
        be at that path in the image rather than inside an archive."""
        u2net_dir = tmp_path / "root" / ".u2net"

        vams_bake_models.bake_into_image(u2net_dir, tmp_path / "model")

        assert (u2net_dir / "u2net.pth").exists()
        assert (u2net_dir / "u2netp.pth").exists()
        assert (u2net_dir / "u2net_human_seg.pth").exists()

    def test_the_split_parts_are_assembled_and_removed(self, tmp_path, recorded_download):
        """u2net.pth is published as four parts; what is left behind must be the joined file."""
        u2net_dir = tmp_path / ".u2net"

        vams_bake_models.bake_into_image(u2net_dir, tmp_path / "model")

        assert list(u2net_dir.glob("u2net.pth.*")) == []
        assert (u2net_dir / "u2net.pth").read_bytes().count(b"bytes-of:") == 4

    def test_the_sam2_checkpoint_lands_in_the_model_directory(self, tmp_path, recorded_download):
        """``remove_background_sam2.py`` composes ``{MODEL_PATH}/sam2.1_hiera_large.pt``."""
        model_dir = tmp_path / "model"

        vams_bake_models.bake_into_image(tmp_path / ".u2net", model_dir)

        assert (model_dir / vams_bake_models.SAM2_CHECKPOINT_NAME).exists()
        assert vams_bake_models.SAM2_CHECKPOINT_NAME == "sam2.1_hiera_large.pt"

    def test_it_downloads_the_declared_sam2_checkpoint_url(self, tmp_path, recorded_download):
        vams_bake_models.bake_into_image(tmp_path / ".u2net", tmp_path / "model")

        assert vams_bake_models.SAM2_CHECKPOINT_URL in [url for url, _dest in recorded_download]

    def test_the_archive_build_and_the_bake_share_one_sam2_source(self):
        """One home for the checkpoint URL, so an archive built for a local debug run and the image
        cannot end up on different checkpoints."""
        with open(vams_bake_models.__file__, "r", encoding="utf-8") as handle:
            source = handle.read()

        assert source.count("dl.fbaipublicfiles.com") == 1


@pytest.mark.unit
class TestStageBakedModels:
    def test_the_baked_checkpoint_becomes_readable_under_the_runs_model_path(
            self, container_main, baked_image_dir, run_model_dir):
        staged = container_main.stage_baked_models(str(run_model_dir), str(baked_image_dir))

        assert staged == [vams_bake_models.SAM2_CHECKPOINT_NAME]
        target = run_model_dir / vams_bake_models.SAM2_CHECKPOINT_NAME
        assert target.read_bytes() == b"checkpoint-bytes"

    def test_a_model_missing_from_the_image_is_skipped_rather_than_fatal(
            self, container_main, tmp_path, run_model_dir):
        """Only the options that consume the model are affected; the reconstruction itself is not."""
        staged = container_main.stage_baked_models(str(run_model_dir), str(tmp_path / "absent"))

        assert staged == []
        assert list(run_model_dir.iterdir()) == []

    def test_a_filesystem_that_refuses_a_link_falls_back_to_a_copy(
            self, container_main, monkeypatch, baked_image_dir, run_model_dir):
        """The image path and the run's MODEL_PATH are on different mounts, and a platform may refuse
        the link outright. ``os`` here is the module the container code calls into."""
        def refuse(*_args, **_kwargs):
            raise OSError("symlinks are not supported here")

        monkeypatch.setattr(os, "symlink", refuse)

        staged = container_main.stage_baked_models(str(run_model_dir), str(baked_image_dir))

        target = run_model_dir / vams_bake_models.SAM2_CHECKPOINT_NAME
        assert staged == [vams_bake_models.SAM2_CHECKPOINT_NAME]
        assert not target.is_symlink()
        assert target.read_bytes() == b"checkpoint-bytes"

    def test_a_link_is_preferred_over_copying_a_multi_hundred_megabyte_file(
            self, container_main, monkeypatch, baked_image_dir, run_model_dir):
        """Copying the checkpoint would be paid at the start of every job."""
        linked = []

        def record_link(source, target):
            linked.append((source, target))
            with open(target, "wb") as handle:
                handle.write(b"checkpoint-bytes")

        def refuse_copy(*_args, **_kwargs):
            raise AssertionError("copyfile was used even though the link succeeded")

        monkeypatch.setattr(os, "symlink", record_link)
        monkeypatch.setattr(shutil, "copyfile", refuse_copy)

        container_main.stage_baked_models(str(run_model_dir), str(baked_image_dir))

        assert len(linked) == 1

    def test_an_already_staged_model_is_left_alone(
            self, container_main, baked_image_dir, run_model_dir):
        """A warm container, or a real models archive that supplied the same file."""
        (run_model_dir / vams_bake_models.SAM2_CHECKPOINT_NAME).write_bytes(b"already-there")

        staged = container_main.stage_baked_models(str(run_model_dir), str(baked_image_dir))

        assert staged == [vams_bake_models.SAM2_CHECKPOINT_NAME]
        assert (run_model_dir / vams_bake_models.SAM2_CHECKPOINT_NAME
                ).read_bytes() == b"already-there"

    def test_the_staged_names_are_the_ones_the_bake_writes(self, container_main):
        assert vams_bake_models.SAM2_CHECKPOINT_NAME in container_main.BAKED_MODEL_FILES
        assert container_main.BAKED_MODEL_DIR == vams_bake_models.DEFAULT_IMAGE_MODEL_DIR


@pytest.mark.unit
class TestRembgWarmUp:
    """The rembg ONNX models, which are a different set of files from the .pth weights above.

    ``BACKGROUND_REMOVAL_MODEL=u2net`` reads the baked ``.pth`` files through backgroundremover, but
    ``u2net_human_seg`` — which ``REMOVE_OBJECT`` selects — routes to rembg instead, and rembg opens
    ONNX models it fetches itself. Both of the models that branch opens have to be present: a bake
    covering only ``u2net_human_seg`` still downloads ``birefnet-portrait`` mid-run.
    """

    @staticmethod
    def _writing_factory(u2net_dir, opened):
        """A session factory that records the name and really writes the file rembg would write.

        A factory that only recorded would leave every "the model is in the image" assertion passing
        against an empty directory.
        """

        def factory(name):
            opened.append(name)
            (u2net_dir / f"{name}.onnx").write_bytes(f"onnx-of:{name}".encode("utf-8"))
            return object()

        return factory

    def test_a_session_is_opened_for_every_model_the_human_seg_branch_needs(self, tmp_path):
        u2net_dir = tmp_path / ".u2net"
        opened = []

        vams_bake_models.warm_rembg_sessions(
            u2net_dir, session_factory=self._writing_factory(u2net_dir, opened)
        )

        assert opened == ["u2net", "u2net_human_seg", "birefnet-portrait"]

    def test_the_onnx_models_land_beside_the_pth_weights(self, tmp_path):
        """One directory holds both, because U2NET_PATH's parent is also rembg's own default."""
        u2net_dir = tmp_path / ".u2net"
        opened = []

        written = vams_bake_models.warm_rembg_sessions(
            u2net_dir, session_factory=self._writing_factory(u2net_dir, opened)
        )

        assert [path.name for path in written] == [
            "u2net.onnx",
            "u2net_human_seg.onnx",
            "birefnet-portrait.onnx",
        ]
        for path in written:
            assert path.exists()
            assert path.parent == u2net_dir

    def test_rembg_is_pointed_at_that_directory(self, tmp_path, monkeypatch):
        """rembg resolves its model home from ``U2NET_HOME``, so the bake sets it rather than
        assuming the process already has it right. Seeded with a wrong value first, so the assertion
        distinguishes "set correctly" from "happened to be correct already"."""
        monkeypatch.setenv("U2NET_HOME", str(tmp_path / "somewhere-else"))
        u2net_dir = tmp_path / ".u2net"

        vams_bake_models.warm_rembg_sessions(
            u2net_dir, session_factory=self._writing_factory(u2net_dir, [])
        )

        assert os.environ["U2NET_HOME"] == str(u2net_dir)

    def test_the_default_factory_resolves_the_model_through_rembgs_own_api(self):
        """A guessed release URL would be worse than not baking: rembg verifies each model's MD5 and
        re-downloads on a mismatch, so a wrong-hash file bakes silently and is ignored at run time.
        Asserted on the source because rembg is installed by the image build, not by this test env."""
        with open(vams_bake_models.__file__, "r", encoding="utf-8") as handle:
            source = handle.read()

        assert "from rembg import new_session" in source
        assert 'providers=["CPUExecutionProvider"]' in source
        # No hand-written model URL anywhere: the installed rembg version supplies its own. Asserted
        # as "no line carries both a URL scheme and .onnx" rather than as the absence of the substring
        # ".onnx", which prose describing the models also satisfies.
        onnx_urls = [
            line
            for line in source.splitlines()
            if ".onnx" in line and ("http://" in line or "https://" in line)
        ]
        assert onnx_urls == [], onnx_urls
        # Control: the same predicate DOES find the URLs this module legitimately holds, so an empty
        # result above is a measurement rather than a predicate that never matches anything.
        pth_urls = [
            line
            for line in source.splitlines()
            if ".pth" in line and ("http://" in line or "https://" in line)
        ]
        assert pth_urls != []


@pytest.mark.unit
class TestTorchHubCheckpoints:
    def test_every_pinned_checkpoint_is_downloaded_into_the_given_directory(
            self, tmp_path, recorded_download):
        torch_dir = tmp_path / "hub" / "checkpoints"

        written = vams_bake_models.download_torch_hub_checkpoints(torch_dir)

        assert [path.name for path in written] == [
            name for _url, name in vams_bake_models.TORCH_HUB_CHECKPOINTS
        ]
        for path in written:
            assert path.exists()

    def test_the_urls_carry_the_content_hash_torch_matches_on(self):
        """These are pinned by filename rather than by a separate revision: torch derives the cache
        filename from the URL, so a URL change is a filename change."""
        for url, name in vams_bake_models.TORCH_HUB_CHECKPOINTS:
            assert url.endswith(name)
            assert "-" in name

    def test_the_archive_and_the_bake_share_one_checkpoint_list(self):
        """A local debug archive and the image must not end up on different checkpoints."""
        with open(vams_bake_models.__file__, "r", encoding="utf-8") as handle:
            source = handle.read()

        assert source.count("download.pytorch.org/models/vgg16-397923af.pth") == 1


@pytest.mark.unit
class TestVerifyBakedModels:
    @staticmethod
    def _complete_bake(tmp_path):
        u2net_dir = tmp_path / ".u2net"
        model_dir = tmp_path / "model"
        torch_dir = tmp_path / "torch"
        for directory in (u2net_dir, model_dir, torch_dir):
            directory.mkdir(parents=True)
        for name in vams_bake_models.U2NET_WEIGHT_FILES:
            (u2net_dir / name).write_bytes(b"x")
        for name in vams_bake_models.REMBG_MODEL_NAMES:
            (u2net_dir / f"{name}.onnx").write_bytes(b"x")
        (model_dir / vams_bake_models.SAM2_CHECKPOINT_NAME).write_bytes(b"x")
        for _url, name in vams_bake_models.TORCH_HUB_CHECKPOINTS:
            (torch_dir / name).write_bytes(b"x")
        return u2net_dir, model_dir, torch_dir

    def test_a_complete_bake_is_accepted(self, tmp_path, capsys):
        """The positive control. Without it, a verifier that raised unconditionally would satisfy the
        failure assertions below."""
        u2net_dir, model_dir, torch_dir = self._complete_bake(tmp_path)

        vams_bake_models.verify_baked_models(u2net_dir, model_dir, torch_dir)

        assert "Verified" in capsys.readouterr().out

    def test_a_missing_onnx_model_fails_the_build(self, tmp_path, capsys):
        """rembg re-downloads a model it cannot find, so the image would build and push clean and the
        fetch would reappear on the data path — a hard failure where there is no egress."""
        u2net_dir, model_dir, torch_dir = self._complete_bake(tmp_path)
        (u2net_dir / "birefnet-portrait.onnx").unlink()

        with pytest.raises(SystemExit) as exit_info:
            vams_bake_models.verify_baked_models(u2net_dir, model_dir, torch_dir)

        assert exit_info.value.code == 1
        assert "birefnet-portrait.onnx" in capsys.readouterr().out

    def test_a_missing_torch_checkpoint_fails_the_build(self, tmp_path, capsys):
        """torch's failure mode is the softest of all: a silent re-download at run time, with no
        error at build. Nothing but this check distinguishes baked-at-the-right-path from absent."""
        u2net_dir, model_dir, torch_dir = self._complete_bake(tmp_path)
        (torch_dir / "vgg16-397923af.pth").unlink()

        with pytest.raises(SystemExit) as exit_info:
            vams_bake_models.verify_baked_models(u2net_dir, model_dir, torch_dir)

        assert exit_info.value.code == 1
        assert "vgg16-397923af.pth" in capsys.readouterr().out


@pytest.mark.unit
class TestBakeWiring:
    """What ``--bake`` actually performs. Each step above is independently correct and inert unless
    the entry point calls it, and the torch destination is the one the whole checkpoint half turns on:
    written to MODEL_PATH/.cache instead, nothing ever reads it and torch re-downloads at run time."""

    @pytest.fixture
    def recorded_steps(self, monkeypatch):
        calls = {}
        monkeypatch.setattr(
            vams_bake_models, "bake_into_image",
            lambda u2net_dir, model_dir: calls.update(bake=(u2net_dir, model_dir)))
        monkeypatch.setattr(
            vams_bake_models, "warm_rembg_sessions",
            lambda u2net_dir: calls.update(rembg=u2net_dir))
        monkeypatch.setattr(
            vams_bake_models, "download_torch_hub_checkpoints",
            lambda torch_dir: calls.update(torch=torch_dir))
        monkeypatch.setattr(
            vams_bake_models, "verify_baked_models",
            lambda u2net_dir, model_dir, torch_dir: calls.update(verified=True))
        return calls

    def test_bake_runs_every_step(self, tmp_path, monkeypatch, recorded_steps):
        monkeypatch.setattr(sys, "argv", [
            "vams_bake_models.py",
            "--u2net-dir", str(tmp_path / ".u2net"),
            "--model-dir", str(tmp_path / "model"),
            "--torch-dir", str(tmp_path / "torch"),
        ])

        vams_bake_models.main()

        assert recorded_steps["bake"] == (tmp_path / ".u2net", tmp_path / "model")
        assert recorded_steps["rembg"] == tmp_path / ".u2net"
        assert recorded_steps["torch"] == tmp_path / "torch"
        assert recorded_steps["verified"] is True

    def test_the_torch_checkpoints_default_to_the_directory_torch_reads(
            self, monkeypatch, recorded_steps):
        """``torch.hub`` loads from ``$TORCH_HOME/hub/checkpoints``, defaulting to ~/.cache/torch. The
        image declares no USER, so that is /root/.cache/torch at build and at run time."""
        monkeypatch.delenv("TORCH_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["vams_bake_models.py"])

        vams_bake_models.main()

        # as_posix(), not str(): the constant is a container path, and str() on a Windows
        # workstation yields backslashes, so the comparison fails where the image is fine.
        assert recorded_steps["torch"].as_posix() == vams_bake_models.DEFAULT_IMAGE_TORCH_HUB_DIR
        assert recorded_steps["torch"].name == "checkpoints"

    def test_a_torch_home_override_is_honoured(self, tmp_path, monkeypatch, recorded_steps):
        monkeypatch.setenv("TORCH_HOME", str(tmp_path / "cache" / "torch"))
        monkeypatch.setattr(sys, "argv", ["vams_bake_models.py"])

        vams_bake_models.main()

        assert recorded_steps["torch"] == tmp_path / "cache" / "torch" / "hub" / "checkpoints"
