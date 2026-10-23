# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The rembg session weights the salient-object backends read, fetched in the cache stage.

rembg keeps its ONNX files outside the HuggingFace cache and downloads a session's file on first use, from
inside preprocessing, where the shared cache's backup never saw it: a run that selected BiRefNet fetched 973 MB
on every run. The cache stage points U2NET_HOME under the model's cache directory, fetches the selected
session's file there under the same retry as a HuggingFace repository, seeds the default session from the copy
the image bakes, and counts the file when it asks whether the cache is warm."""

import os
import shutil
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import model_manager
import preprocess

pytestmark = pytest.mark.unit

CONTAINER = Path(model_manager.__file__).resolve().parent
WEIGHT = 16  # bytes: the size a complete weight has to reach in these tests


def _write(path, size=WEIGHT):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"w" * size)
    return path


def _hf_cache(root, repo, weight="model.safetensors", revision="abc123"):
    """A HuggingFace-shaped cache holding one repo's snapshot, and its blobs beside it."""
    repo_dir = root / "hub" / ("models--" + repo.replace("/", "--"))
    snapshot = repo_dir / "snapshots" / revision
    snapshot.mkdir(parents=True, exist_ok=True)
    blobs = repo_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    (blobs / "deadbeef").write_bytes(b"weights")
    (snapshot / weight).write_bytes(b"weights")
    return root


@pytest.fixture
def fake_rembg(monkeypatch, tmp_path):
    """A rembg whose two session classes lay their weights out the way the installed releases do: one under
    ``models/<name>/`` with ``model_dir()``, one flat under U2NET_HOME with only ``u2net_home()``. Like the
    installed ``download_models``, the fake returns a file already at either layout's name, whatever its
    size, before downloading; otherwise it consumes the next of its ``side_effects``: an exception is raised,
    ``"short"`` writes a truncated file, anything else a complete one."""
    home = str(tmp_path / "rembg_home")
    monkeypatch.setenv("U2NET_HOME", home)
    monkeypatch.setattr(model_manager, "MIN_SESSION_WEIGHT_BYTES", WEIGHT)
    monkeypatch.setattr(model_manager, "FETCH_RETRY_BACKOFF_SECONDS", 0)
    downloads = []

    class Session:
        side_effects = []

        @classmethod
        def u2net_home(cls):
            return os.environ["U2NET_HOME"]

        @classmethod
        def weight_dir(cls):
            return cls.model_dir() if hasattr(cls, "model_dir") else cls.u2net_home()

        @classmethod
        def download_models(cls):
            fname = f"{cls.name()}.onnx"
            for existing in (os.path.join(cls.weight_dir(), fname), os.path.join(cls.u2net_home(), fname)):
                if os.path.exists(existing):
                    return existing
            downloads.append(cls.name())
            effect = cls.side_effects.pop(0) if cls.side_effects else None
            target = os.path.join(cls.weight_dir(), fname)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if isinstance(effect, BaseException):
                raise effect
            return _write(target, 4 if effect == "short" else WEIGHT)

    class ModelsLayout(Session):
        @classmethod
        def name(cls):
            return "birefnet-general"

        @classmethod
        def model_dir(cls):
            return os.path.join(cls.u2net_home(), "models", cls.name())

    class FlatLayout(Session):
        @classmethod
        def name(cls):
            return "u2net"

    sessions = types.ModuleType("rembg.sessions")
    sessions.sessions_class = [ModelsLayout, FlatLayout]
    rembg = types.ModuleType("rembg")
    rembg.sessions = sessions
    rembg.new_session = mock.Mock(side_effect=lambda name: SimpleNamespace(session_name=name))
    saved = []
    rembg.remove = mock.Mock(side_effect=lambda image, session=None: SimpleNamespace(
        session=session, save=lambda path: saved.append(path) or _write(path, 1)))
    monkeypatch.setitem(sys.modules, "rembg", rembg)
    monkeypatch.setitem(sys.modules, "rembg.sessions", sessions)
    return SimpleNamespace(home=home, downloads=downloads, models=ModelsLayout, flat=FlatLayout,
                           module=rembg, saved=saved)


def _partials(*directories):
    return [p for d in directories if os.path.isdir(d) for p in os.listdir(d)
            if p.startswith("tmp") or p.endswith(".partial")]


class TestFetchingASessionsWeights:
    def test_a_complete_weight_at_the_installed_layout_costs_no_download(self, fake_rembg):
        cached = _write(os.path.join(fake_rembg.home, "models", "birefnet-general", "birefnet-general.onnx"))

        path = model_manager.fetch_rembg_session("birefnet-general", "birefnet-general.onnx", fake_rembg.home)

        assert path == cached
        assert fake_rembg.downloads == []

    def test_the_images_baked_copy_seeds_the_default_session_without_a_download(self, fake_rembg, tmp_path):
        baked = _write(str(tmp_path / "opt_rembg" / "u2net.onnx"))

        path = model_manager.fetch_rembg_session("u2net", "u2net.onnx", fake_rembg.home,
                                                 seed_from=str(tmp_path / "opt_rembg"))

        assert path == os.path.join(fake_rembg.home, "u2net.onnx"), "the flat layout this release reads"
        assert Path(path).read_bytes() == Path(baked).read_bytes()
        assert fake_rembg.downloads == []
        assert _partials(fake_rembg.home) == []

    def test_two_jobs_seeding_at_once_write_through_different_siblings(self, fake_rembg, tmp_path, monkeypatch):
        """Every container's python is PID 1, so a sibling named after the pid would be the same file in two
        jobs sharing the cache; each copy goes through a sibling of its own."""
        baked = _write(str(tmp_path / "opt_rembg" / "u2net.onnx"))
        siblings = []
        real_copy = shutil.copyfile
        monkeypatch.setattr(model_manager.shutil, "copyfile",
                            lambda source, dest: siblings.append(dest) or real_copy(source, dest))
        target = os.path.join(fake_rembg.home, "u2net.onnx")

        model_manager._seed_session_weight(baked, target)
        model_manager._seed_session_weight(baked, target)

        assert len(set(siblings)) == 2 and all(s.endswith(".partial") for s in siblings)
        assert Path(target).read_bytes() == Path(baked).read_bytes()
        assert _partials(fake_rembg.home) == []

    def test_a_seed_copy_cut_by_the_mount_is_retried_without_a_download(self, fake_rembg, tmp_path, monkeypatch):
        """The seed is the one long write on the shared mount on the default cold path; a stale handle there
        is retried through a fresh sibling, the way a cut download is."""
        baked = _write(str(tmp_path / "opt_rembg" / "u2net.onnx"))
        real_copy = shutil.copyfile
        cuts = iter([OSError(116, "Stale file handle")])

        def copy(source, dest):
            cut = next(cuts, None)
            if cut is not None:
                raise cut
            return real_copy(source, dest)

        monkeypatch.setattr(model_manager.shutil, "copyfile", copy)

        path = model_manager.fetch_rembg_session("u2net", "u2net.onnx", fake_rembg.home,
                                                 seed_from=str(tmp_path / "opt_rembg"), attempts=3)

        assert path == os.path.join(fake_rembg.home, "u2net.onnx")
        assert Path(path).read_bytes() == Path(baked).read_bytes()
        assert fake_rembg.downloads == []
        assert _partials(fake_rembg.home) == []

    def test_a_weight_in_the_other_layout_is_copied_into_the_installed_one(self, fake_rembg):
        flat = _write(os.path.join(fake_rembg.home, "birefnet-general.onnx"))

        path = model_manager.fetch_rembg_session("birefnet-general", "birefnet-general.onnx", fake_rembg.home)

        assert path == os.path.join(fake_rembg.home, "models", "birefnet-general", "birefnet-general.onnx")
        assert Path(path).read_bytes() == Path(flat).read_bytes()
        assert fake_rembg.downloads == []

    def test_a_truncated_file_is_not_a_weight_and_is_replaced(self, fake_rembg):
        """rembg returns an existing file at the weight's name without downloading, so a truncated one has
        to go before the download is asked for, and the download then writes a complete one."""
        target = _write(os.path.join(fake_rembg.home, "models", "birefnet-general", "birefnet-general.onnx"), 4)

        path = model_manager.fetch_rembg_session("birefnet-general", "birefnet-general.onnx", fake_rembg.home)

        assert path == target
        assert fake_rembg.downloads == ["birefnet-general"]
        assert os.path.getsize(path) >= WEIGHT

    def test_a_broken_connection_is_retried(self, fake_rembg):
        fake_rembg.models.side_effects = [OSError(116, "Stale file handle")]

        path = model_manager.fetch_rembg_session("birefnet-general", "birefnet-general.onnx", fake_rembg.home)

        assert fake_rembg.downloads == ["birefnet-general", "birefnet-general"]
        assert os.path.getsize(path) >= WEIGHT

    def test_what_a_dead_job_left_is_swept_and_a_live_jobs_file_is_not(self, fake_rembg):
        """Containers share the cache: a pooch temporary or seed sibling younger than an hour may belong to
        another job's download in flight, so only an older one, whose job is gone, is removed."""
        weight_dir = os.path.join(fake_rembg.home, "models", "birefnet-general")
        old = time.time() - 2 * 3600
        dead = _write(os.path.join(weight_dir, "tmpdead01"), 4)
        os.utime(dead, (old, old))
        stale_seed = _write(os.path.join(fake_rembg.home, "u2net.onnx.a1b2c3.partial"), 4)
        os.utime(stale_seed, (old, old))
        live = _write(os.path.join(weight_dir, "tmplive01"), 4)

        model_manager.fetch_rembg_session("birefnet-general", "birefnet-general.onnx", fake_rembg.home)

        assert not os.path.exists(dead) and not os.path.exists(stale_seed)
        assert os.path.exists(live)
        assert fake_rembg.downloads == ["birefnet-general"]

    def test_a_hash_mismatch_is_retried(self, fake_rembg):
        fake_rembg.models.side_effects = [ValueError("MD5 hash of downloaded file does not match")]

        model_manager.fetch_rembg_session("birefnet-general", "birefnet-general.onnx", fake_rembg.home)

        assert fake_rembg.downloads == ["birefnet-general", "birefnet-general"]

    def test_a_download_that_returns_a_truncated_file_is_retried(self, fake_rembg):
        fake_rembg.flat.side_effects = ["short"]

        path = model_manager.fetch_rembg_session("u2net", "u2net.onnx", fake_rembg.home)

        assert fake_rembg.downloads == ["u2net", "u2net"]
        assert os.path.getsize(path) >= WEIGHT

    def test_a_disk_full_error_is_not_retried(self, fake_rembg):
        fake_rembg.models.side_effects = [OSError(28, "No space left on device")]

        with pytest.raises(OSError, match="No space left"):
            model_manager.fetch_rembg_session("birefnet-general", "birefnet-general.onnx", fake_rembg.home)

        assert fake_rembg.downloads == ["birefnet-general"]

    def test_exhausting_the_attempts_names_the_last_failure(self, fake_rembg):
        fake_rembg.models.side_effects = [OSError(116, "Stale file handle")] * 3

        with pytest.raises(RuntimeError, match="after 3 attempts") as failure:
            model_manager.fetch_rembg_session("birefnet-general", "birefnet-general.onnx", fake_rembg.home,
                                              attempts=3)

        assert isinstance(failure.value.__cause__, OSError)
        assert fake_rembg.downloads == ["birefnet-general"] * 3

    def test_a_session_the_installed_rembg_does_not_offer_fails_the_run(self, fake_rembg):
        with pytest.raises(RuntimeError, match="no session named 'isnet-anime'"):
            model_manager.fetch_rembg_session("isnet-anime", "isnet-anime.onnx", fake_rembg.home)

        assert fake_rembg.downloads == []


class TestTheBackendsNameTheirSession:
    def test_every_backend_says_where_its_weights_come_from(self):
        declared = set(preprocess.BACKGROUND_REMOVAL_SESSIONS) | set(preprocess.BACKGROUND_REMOVAL_REPOS) | {"none"}
        assert declared == set(preprocess.BACKGROUND_REMOVAL_BACKENDS)
        assert not set(preprocess.BACKGROUND_REMOVAL_SESSIONS) & set(preprocess.BACKGROUND_REMOVAL_REPOS)

    def test_backend_session_answers_like_backend_repos(self):
        assert preprocess.backend_session("birefnet") == ("birefnet-general", "birefnet-general.onnx")
        assert preprocess.backend_session(" REMBG ") == ("u2net", "u2net.onnx")
        assert preprocess.backend_session("rmbg2") == () and preprocess.backend_session(None) == ()

    @pytest.mark.parametrize("backend, session", [("rembg", "u2net"), ("birefnet", "birefnet-general")])
    def test_the_backend_runs_the_session_the_cache_stage_fetched(self, fake_rembg, tmp_path, backend, session):
        """rembg's default session is whichever the library picks; the backend names its own so the file
        fetched into the cache is the file read."""
        image = tmp_path / "photo.png"
        from PIL import Image
        if isinstance(Image, mock.MagicMock):
            pytest.skip("needs a real PIL to open the photograph")
        Image.new("RGB", (4, 4), (200, 100, 50)).save(image)

        preprocess.remove_background(str(image), str(tmp_path / "work"), backend)

        fake_rembg.module.new_session.assert_called_once_with(session)
        _, kwargs = fake_rembg.module.remove.call_args
        assert kwargs["session"].session_name == session
        assert fake_rembg.saved, "the cutout was written"


class TestTheCacheCountsTheSessionWeight:
    @pytest.fixture
    def offline(self, monkeypatch):
        """No S3 and a shared-looking mount, so the check's answer is the only thing that varies."""
        monkeypatch.setattr(model_manager, "warn_if_cache_is_not_shared", lambda mount=None: True)
        looked = []
        monkeypatch.setattr(model_manager, "_s3_prefix_exists", lambda bucket, prefix: looked.append(prefix) or False)
        return looked

    def test_a_cache_holding_the_models_weights_but_not_the_sessions_is_a_miss(self, tmp_path, offline):
        hf_home = _hf_cache(tmp_path / "hf", "facebook/sam-3d-objects")
        rembg_home = model_manager.resolve_rembg_home(str(hf_home))
        weights = (model_manager.session_weight_candidates("u2net", "u2net.onnx", rembg_home),)

        model_manager.ensure_models_cached(str(hf_home), "bucket", "prefix",
                                           repos=("facebook/sam-3d-objects",), required_files=weights)
        assert offline == ["prefix"], "missing session weights send the check to S3"

        _write(os.path.join(rembg_home, "u2net.onnx"), model_manager.MIN_SESSION_WEIGHT_BYTES)
        model_manager.ensure_models_cached(str(hf_home), "bucket", "prefix",
                                           repos=("facebook/sam-3d-objects",), required_files=weights)
        assert offline == ["prefix"], "with the session weights present the cache is warm"

    def test_a_model_with_no_weights_of_its_own_is_answered_by_the_session_file(self, tmp_path, offline):
        hf_home = tmp_path / "hf"
        rembg_home = model_manager.resolve_rembg_home(str(hf_home))
        weights = (model_manager.session_weight_candidates("u2net", "u2net.onnx", rembg_home),)

        model_manager.ensure_models_cached(str(hf_home), "bucket", "prefix", repos=(), required_files=weights)
        assert offline == ["prefix"]

        _write(os.path.join(rembg_home, "models", "u2net", "u2net.onnx"), model_manager.MIN_SESSION_WEIGHT_BYTES)
        model_manager.ensure_models_cached(str(hf_home), "bucket", "prefix", repos=(), required_files=weights)
        assert offline == ["prefix"], "either layout's file answers the check"

    def test_the_backup_runs_for_a_directory_holding_only_the_session_weight(self, tmp_path, monkeypatch):
        synced = []
        monkeypatch.setattr(model_manager.subprocess, "run",
                            lambda cmd, **kwargs: synced.append(cmd) or SimpleNamespace(returncode=0, stderr=""))
        hf_home = tmp_path / "hf"
        rembg_home = model_manager.resolve_rembg_home(str(hf_home))
        weights = (model_manager.session_weight_candidates("u2net", "u2net.onnx", rembg_home),)
        hf_home.mkdir()

        model_manager.backup_cache_to_s3(str(hf_home), "bucket", "prefix", required_files=weights)
        assert synced == [], "nothing under the directory is worth an upload"

        _write(os.path.join(rembg_home, "u2net.onnx"), model_manager.MIN_SESSION_WEIGHT_BYTES)
        model_manager.backup_cache_to_s3(str(hf_home), "bucket", "prefix", required_files=weights)
        assert len(synced) == 1 and synced[0][:3] == ["aws", "s3", "sync"]

    def test_rembg_home_lies_inside_the_models_cache_directory(self):
        hf_home = model_manager.resolve_hf_home("stable3dgen", base="/mnt/efs/imageto3d-models")
        assert model_manager.resolve_rembg_home(hf_home) == os.path.join(hf_home, "rembg")


class TestTheEntryPointWiresItIn:
    def test_the_cache_stage_points_rembg_at_the_cache_before_fetching(self):
        text = (CONTAINER / "__main__.py").read_text(encoding="utf-8")

        def position(literal):
            found = text.find(literal)
            assert found >= 0, f"__main__.py no longer carries {literal!r}"
            return found

        baked = position('image_rembg_home = os.environ.get("U2NET_HOME")')
        pointed = position('os.environ["U2NET_HOME"] = rembg_home')
        fetched = position("fetch_rembg_session(")
        seeded = position("seed_from=image_rembg_home")
        assert baked < pointed < fetched < seeded, \
            "the baked copy's location is read before U2NET_HOME is moved, and seeds the fetch"
        assert text.count("required_files=segmentation_weights") == 2, \
            "the cache check and the backup both count the session file"

    def test_every_image_bakes_the_default_session_where_the_cache_stage_seeds_from(self):
        _, fname = preprocess.BACKGROUND_REMOVAL_SESSIONS["rembg"]
        dockerfiles = sorted(CONTAINER.glob("Dockerfile.*"))
        assert dockerfiles, "the container directory holds the model images"
        for dockerfile in dockerfiles:
            text = dockerfile.read_text(encoding="utf-8")
            assert "ENV U2NET_HOME=/opt/rembg" in text, dockerfile.name
            assert f"/opt/rembg/{fname}" in text, dockerfile.name


def test_the_session_names_are_ones_the_installed_rembg_offers():
    """The names and file names in BACKGROUND_REMOVAL_SESSIONS against a real rembg, where one is installed."""
    rembg = pytest.importorskip("rembg")
    if isinstance(rembg, mock.MagicMock):
        pytest.skip("rembg is stubbed here")
    for name, fname in preprocess.BACKGROUND_REMOVAL_SESSIONS.values():
        cls = model_manager._session_class(name)
        assert fname == f"{cls.name()}.onnx"
