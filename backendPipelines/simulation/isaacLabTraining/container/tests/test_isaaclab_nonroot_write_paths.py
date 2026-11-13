#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Isaac Lab image's non-root user can write everywhere Isaac Lab writes (issue #1312).

The image runs as uid 10000 (issue #327) and ``__main__.py`` starts Isaac Lab's ``train.py`` and
``play.py`` from ``/workspace/isaaclab``, which the base image (``nvcr.io/nvidia/isaac-lab:2.3.0``)
copies root-owned ``0755`` with no ``logs`` or ``outputs`` folder in it (Isaac Lab's ``.dockerignore``
excludes both). Isaac Lab writes relative to that working directory, so each write below raises
``PermissionError: [Errno 13]`` unless the image gives the runtime user somewhere to write it.

What Isaac Lab v2.3.0 writes, and where:

- Hydra's run folder. ``hydra.run.dir`` defaults to ``outputs/${now:%Y-%m-%d}/${now:%H-%M-%S}``
  (hydra-core 1.3, ``hydra/conf/hydra/output/default.yaml``), relative to the working directory, and
  holds ``.hydra/`` and the job log. Every ``train.py`` and ``play.py`` runs through
  ``@hydra_task_config``.
- ``train.py``'s run folder, ``os.path.abspath(os.path.join("logs", <library>, <experiment>))`` plus
  a timestamp, in all three libraries: checkpoints, TensorBoard events, ``params/*.yaml``, git diffs.
- Files a task writes straight into the working directory while its environment is built. The
  AutoMate tasks (``Isaac-AutoMate-Assembly-Direct-v0``, ``-Disassembly-Direct-v0``) download their
  grasp, distance and trajectory JSON and their plug and socket meshes with
  ``retrieve_file_path(<Nucleus URL>, download_dir="./")`` and open them by basename
  (``direct/automate/assembly_env.py``, ``disassembly_env.py``, ``industreal_algo_utils.py``); a
  custom environment that writes a relative path does the same.
- ``play.py``'s run folder, derived from ``--checkpoint``: the checkpoint's folder for rsl_rl, the
  folder above it for rl_games and skrl. The video goes to ``<run folder>/videos/play`` (gymnasium
  1.2.0's ``RecordVideo`` creates it with ``os.makedirs``), and rsl_rl exports ``policy.pt`` and
  ``policy.onnx`` to ``<checkpoint folder>/exported``.

No container build runs here (the image is over 15 GB), so the ownership half is a static parse of
the Dockerfile (the ``image`` fixture in ``conftest.py``), the approach
``infra/test/pipelines/fargateBatchContainerUser.test.ts`` takes for ``USER``. The paths themselves
are not restated: the working directory is read from the ``subprocess.run`` call the pipeline makes,
and the checkpoint path from the download it performs. The write layout above is Isaac Lab's, so
``TestModelledIsaacLabVersion`` pins it to the base image version it was read from.
"""

import glob as real_glob
import importlib.util
import os
import re
from types import SimpleNamespace

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_CONTAINER_DIR))))
_OUTPUT_PREFIX = "s3://run-bucket/pipelines/isaacLab/JOB/output/3f2c9a10/files/"

# The Isaac Lab release whose train.py, play.py, Hydra configuration and AutoMate tasks the write
# paths in this module were read from, and that the Dockerfile builds on.
_MODELLED_ISAAC_LAB_VERSION = "2.3.0"

# Files that build the image without passing a build argument to it.
_IMAGE_BUILD_SOURCES = (
    os.path.join(_CONTAINER_DIR, "buildspec.yml"),
    os.path.join(_REPO_ROOT, "infra", "lib", "nestedStacks", "pipelines", "simulation",
                 "isaacLabTraining", "constructs", "isaacLabCodeBuild-construct.ts"),
    os.path.join(_REPO_ROOT, "infra", "lib", "nestedStacks", "pipelines", "simulation",
                 "isaacLabTraining", "constructs", "isaacLabTraining-construct.ts"),
)

# Folders Isaac Lab creates relative to the working directory of train.py and play.py.
_CWD_RELATIVE_WRITE_ROOTS = ("outputs", "logs")

# Files the AutoMate tasks download into the working directory while the environment is built.
_CWD_FILE_WRITES = ("plug_grasps.json", "disassembly_dist.json", "disassemble_traj.json",
                    "plug.obj", "socket.obj")

_LIBRARIES = ("rsl_rl", "rl_games", "skrl")

# Folders the image ships for any user to write in (the ``image`` fixture models the rest).
_SHIPPED_WRITABLE_FOLDERS = ("/tmp",)


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file under a suite-private name."""
    spec = importlib.util.spec_from_file_location(
        "isaaclab_nonroot_container_main", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _under(path, root):
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _config(main_module, mode, library="rsl_rl", record_video=False):
    return main_module.PipelineConfig(
        job_name="isaaclab-job",
        mode=mode,
        task="Isaac-Cartpole-Direct-v0",
        rl_library=library,
        input_s3_path="s3://run-bucket/asset/config.json",
        output_s3_path=_OUTPUT_PREFIX,
        policy_s3_uri="s3://run-bucket/asset/checkpoints/model_300.pt",
        record_video=record_video,
    )


def _isaac_lab_cwd(main_module, mode, monkeypatch):
    """The working directory the pipeline runs train.py (``train``) or play.py (``evaluate``) in."""
    calls = []

    def _run(cmd, cwd=None, **kwargs):
        calls.append(cwd)
        return SimpleNamespace(returncode=0)

    # Undone on return, so a test can go on to call the real download and upload functions.
    with monkeypatch.context() as m:
        m.setattr(main_module, "subprocess", SimpleNamespace(run=_run))
        m.setattr(main_module, "setup_checkpoint_dir", lambda config: "/unused")
        m.setattr(main_module, "upload_training_results", lambda *args: None)
        m.setattr(main_module, "download_policy", lambda s3, config: "/unused/policy.pt")
        m.setattr(main_module, "upload_evaluation_results", lambda *args: None)
        if mode == "train":
            main_module.run_training(_config(main_module, mode), None, {})
        else:
            main_module.run_evaluation(_config(main_module, mode), None, {})
    assert len(calls) == 1 and calls[0], "the Isaac Lab script ran without an explicit working directory"
    return calls[0]


class _NoDiskOs:
    """The module's ``os`` with ``makedirs`` recorded in ``events`` instead of performed."""

    def __init__(self, events):
        self.events = events

    def makedirs(self, path, *args, **kwargs):
        self.events.append(("makedirs", os.path.normpath(path)))

    def __getattr__(self, name):
        return getattr(os, name)


def _download_policy_events(main_module, monkeypatch):
    """The policy path ``download_policy`` returns, and its folder and download calls in order."""
    events = []

    class _Client:
        def download_file(self, bucket, key, local_path):
            events.append(("download", os.path.normpath(local_path)))

    monkeypatch.setattr(main_module, "os", _NoDiskOs(events))
    s3 = SimpleNamespace(client_for_bucket=lambda bucket: _Client())
    returned = main_module.download_policy(s3, _config(main_module, "evaluate"))
    return returned, events


def _assert_policy_folder_exists_before_download(returned, events):
    """The policy's folder is one the image ships (``/tmp``), or ``download_policy`` creates it first."""
    folder = os.path.dirname(os.path.normpath(returned))
    download = ("download", os.path.normpath(returned))
    assert download in events, f"the policy was not downloaded to {returned}: {events}"
    if folder in _SHIPPED_WRITABLE_FOLDERS:
        return
    made = ("makedirs", folder)
    assert made in events and events.index(made) < events.index(download), (
        f"download_policy downloads to {returned} without creating {folder} first: {events}")


def _downloaded_policy_path(main_module, monkeypatch):
    """Where the pipeline puts the evaluation policy it passes to play.py as ``--checkpoint``."""
    returned, events = _download_policy_events(main_module, monkeypatch)
    _assert_policy_folder_exists_before_download(returned, events)
    return returned


def _play_write_dirs(library, checkpoint, cwd):
    """The folders Isaac Lab v2.3.0's play.py creates for a ``--checkpoint`` (module docstring)."""
    checkpoint = os.path.normpath(checkpoint)
    if library == "rsl_rl":
        run_dir = os.path.dirname(checkpoint)
        return [os.path.join(run_dir, "videos", "play"),
                os.path.join(os.path.dirname(checkpoint), "exported")]
    run_dir = os.path.dirname(os.path.dirname(checkpoint))
    if library == "rl_games":
        # rl_games joins the absolute run folder onto its log root, which the absolute path replaces.
        log_root = os.path.join(cwd, "logs", "rl_games", "cartpole_direct")
        return [os.path.join(log_root, run_dir, "videos", "play")]
    return [os.path.join(run_dir, "videos", "play")]


def _glob_regex(pattern):
    """``glob.glob(pattern, recursive=True)`` as a regular expression over absolute file paths."""
    parts = pattern.split("/")
    regex = ""
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            regex += ".*" if last else "(?:[^/]+/)*"
            continue
        regex += "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in part)
        if not last:
            regex += "/"
    return re.compile(regex + r"\Z")


class _VirtualGlob:
    """A ``glob`` module over a fixed list of files, so nothing is written under ``/workspace``."""

    def __init__(self, files):
        self.files = files

    def glob(self, pattern, recursive=False):
        assert recursive, f"{pattern} is searched without recursive=True"
        return [f for f in self.files if _glob_regex(pattern).match(f)]


class _RecordingS3:
    def __init__(self):
        self.uploads = []

    def upload_file(self, local_path, s3_path):
        self.uploads.append((local_path, s3_path))


@pytest.mark.unit
class TestIsaacLabWorkingDirectory:
    @pytest.mark.parametrize("mode", ["train", "evaluate"])
    def test_isaac_lab_write_roots_belong_to_the_runtime_user(self, main_module, image, mode,
                                                              monkeypatch):
        """Hydra's ``outputs`` and Isaac Lab's ``logs`` sit in the working directory the run uses."""
        cwd = _isaac_lab_cwd(main_module, mode, monkeypatch)
        unwritable = [os.path.join(cwd, root) for root in _CWD_RELATIVE_WRITE_ROOTS
                      if not image.writable(os.path.join(cwd, root))]
        assert not unwritable, (
            f"{mode} runs Isaac Lab with cwd={cwd}, but the image does not give "
            f"{image.runtime_user} these folders it writes there: {unwritable}")

    @pytest.mark.parametrize("mode", ["train", "evaluate"])
    def test_a_task_can_write_files_into_the_working_directory(self, main_module, image, mode,
                                                               monkeypatch):
        """The AutoMate tasks download their assets into ``./`` while the environment is built."""
        cwd = _isaac_lab_cwd(main_module, mode, monkeypatch)
        unwritable = [os.path.join(cwd, name) for name in _CWD_FILE_WRITES
                      if not image.writable(os.path.join(cwd, name))]
        assert not unwritable, (
            f"{mode} runs Isaac Lab with cwd={cwd}, where {image.runtime_user} cannot create "
            f"the files a task downloads there: {unwritable}")

    def test_the_working_directory_keeps_isaac_lab_root_owned(self, main_module, image,
                                                              monkeypatch):
        """The user may add entries to the working directory, not replace Isaac Lab's own.

        Group write lets the user create files there; the sticky bit stops it renaming or deleting
        the root-owned ``isaaclab.sh``, ``scripts/`` and ``source/`` (the ``/tmp`` model). Owning the
        folder, or a recursive change over it, would hand those sources to the user.
        """
        cwd = os.path.normpath(_isaac_lab_cwd(main_module, "train", monkeypatch))
        assert image.group_writable(cwd), f"the runtime user's group cannot create files in {cwd}"
        assert image.sticky(cwd), f"{cwd} is group-writable without the sticky bit"
        assert not image.owns(cwd), f"{cwd} is owned by the runtime user"

        def reaches_sources(target):
            if _under(cwd, target):  # the working directory itself, or a folder above it
                return True
            if _under(target, cwd):  # inside it: only an entry the base image ships matters
                return os.path.relpath(target, cwd).split(os.sep)[0] in image.base_entries[cwd]
            return False

        covered = [t for t in image.recursive_changes if reaches_sources(t)]
        assert not covered, f"a recursive chown/chgrp/chmod reaches Isaac Lab's sources: {covered}"

    def test_the_log_upload_root_is_where_train_py_writes(self, main_module, monkeypatch):
        """Checkpoints, metrics and diffs are uploaded from the folder train.py logs to."""
        cwd = _isaac_lab_cwd(main_module, "train", monkeypatch)
        assert os.path.normpath(main_module.LOCAL_LOG_PATH) == os.path.join(cwd, "logs")


@pytest.mark.unit
class TestEvaluationWritePaths:
    def test_the_policy_folder_is_created_before_the_download(self, main_module, monkeypatch):
        """A policy folder the image does not ship is created before the file is written to it."""
        returned, events = _download_policy_events(main_module, monkeypatch)
        _assert_policy_folder_exists_before_download(returned, events)

    @pytest.mark.parametrize("library", _LIBRARIES)
    def test_play_writes_where_the_runtime_user_can(self, main_module, image, library,
                                                    monkeypatch):
        cwd = _isaac_lab_cwd(main_module, "evaluate", monkeypatch)
        checkpoint = _downloaded_policy_path(main_module, monkeypatch)
        unwritable = [d for d in _play_write_dirs(library, checkpoint, cwd)
                      if not image.writable(d)]
        assert not unwritable, (
            f"{library} play.py with --checkpoint {checkpoint} writes {unwritable}, which "
            f"{image.runtime_user} cannot create")

    @pytest.mark.parametrize("library", _LIBRARIES)
    def test_the_recorded_video_is_uploaded(self, main_module, library, monkeypatch):
        cwd = _isaac_lab_cwd(main_module, "evaluate", monkeypatch)
        checkpoint = _downloaded_policy_path(main_module, monkeypatch)
        video = os.path.join(_play_write_dirs(library, checkpoint, cwd)[0], "rl-video-step-0.mp4")
        monkeypatch.setattr(main_module, "glob", _VirtualGlob([video]))
        monkeypatch.setattr(main_module, "upload_config", lambda *args: None)
        s3 = _RecordingS3()

        main_module.upload_evaluation_results(
            s3, _config(main_module, "evaluate", library, record_video=True), {})

        assert (video, f"{_OUTPUT_PREFIX}videos/rl-video-step-0.mp4") in s3.uploads, (
            f"{library} play.py records {video}, which no video search path uploads")


@pytest.mark.unit
class TestModelledIsaacLabVersion:
    """The write paths above are Isaac Lab's; a different base image needs them read again."""

    def test_the_image_builds_on_the_modelled_isaac_lab_release(self, image):
        assert image.args.get("ISAAC_LAB_VERSION") == _MODELLED_ISAAC_LAB_VERSION, (
            f"The Dockerfile builds on Isaac Lab {image.args.get('ISAAC_LAB_VERSION')}, but this "
            f"module models the write paths of Isaac Lab {_MODELLED_ISAAC_LAB_VERSION}. Re-read that "
            "release's scripts/reinforcement_learning/*/train.py and play.py, its Hydra output "
            "configuration, the files its tasks write to the working directory (direct/automate), "
            "and the entries its image copies into /workspace/isaaclab, update this module, then "
            "_MODELLED_ISAAC_LAB_VERSION.")

    def test_the_base_image_follows_that_version(self, image):
        assert image.args.get("BASE_IMAGE") == "nvcr.io/nvidia/isaac-lab:${ISAAC_LAB_VERSION}"

    @pytest.mark.parametrize("path", _IMAGE_BUILD_SOURCES, ids=os.path.basename)
    def test_no_build_overrides_the_base_image(self, path):
        assert os.path.isfile(path), f"{path} is missing; the check would pass vacuously"
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "ACCEPT_EULA" in text, f"{os.path.basename(path)} no longer builds the image"
        assert not re.search(r"\b(ISAAC_LAB_VERSION|BASE_IMAGE)\b", text), (
            f"{os.path.basename(path)} passes the base image or its version as a build argument, "
            "so the Dockerfile default this module is pinned to is not what gets built")


@pytest.mark.unit
class TestControls:
    """The checks above are not vacuous: the parser and the virtual glob behave as assumed."""

    def test_the_parser_reads_the_runtime_user_and_its_folders(self, image):
        assert image.runtime_user == "10000:10000"
        assert "vams" in image.names
        assert "vams" in image.groups
        # Custom environment installs: pip writes into Isaac Sim's Python, which the user owns.
        assert image.writable("/isaac-sim/kit/python/lib/python3.11/site-packages/pkg")
        assert image.writable("/home/vams/.cache/ov")

    def test_the_parser_reports_root_owned_folders(self, image):
        assert not image.writable("/videos/play")
        assert not image.writable("/workspace/isaaclab/source/isaaclab")
        # Entries the base image ships in the working directory stay root-owned.
        assert not image.writable("/workspace/isaaclab/isaaclab.sh")
        assert not image.writable("/workspace/isaaclab/scripts")
        assert not image.writable("/workspace/isaaclab/scripts/reinforcement_learning/train.py")

    def test_the_mode_parser_matches_chmod(self, chmod_mode):
        assert chmod_mode(0o755, "1775") == 0o1775
        assert chmod_mode(0o755, "g+w") == 0o775
        assert chmod_mode(0o755, "g+w,+t") == 0o1775
        assert chmod_mode(0o755, "o+t") == 0o1755
        assert chmod_mode(0o1775, "-t") == 0o775
        assert chmod_mode(0o640, "a+rX") == 0o755

    def test_the_virtual_glob_matches_like_glob(self, tmp_path):
        files = [tmp_path / "logs" / "evaluation" / "videos" / "play" / "a.mp4",
                 tmp_path / "logs" / "evaluation" / "checkpoints" / "videos" / "play" / "b.mp4",
                 tmp_path / "logs" / "rsl_rl" / "run" / "model_10.pt",
                 tmp_path / "videos" / "c.mp4"]
        for f in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"")
        virtual = _VirtualGlob([str(f) for f in files])
        for pattern in ("{r}/logs/**/videos/**/*.mp4", "{r}/videos/**/*.mp4",
                        "{r}/logs/**/model_*.pt", "{r}/**/*.mp4"):
            pattern = pattern.format(r=tmp_path)
            assert sorted(virtual.glob(pattern, recursive=True)) == \
                sorted(real_glob.glob(pattern, recursive=True)), pattern
