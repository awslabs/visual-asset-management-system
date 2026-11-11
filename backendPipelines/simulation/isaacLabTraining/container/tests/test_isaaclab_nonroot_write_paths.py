#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Isaac Lab image's non-root user can write everywhere Isaac Lab writes (issue #1312).

The image runs as uid 10000 (issue #327) and ``__main__.py`` starts Isaac Lab's ``train.py`` and
``play.py`` from ``/workspace/isaaclab``, which the base image (``nvcr.io/nvidia/isaac-lab:2.3.0``)
copies root-owned ``0755`` with no ``logs`` or ``outputs`` folder in it (Isaac Lab's ``.dockerignore``
excludes both). The scripts write relative to that working directory, so every training run failed
in Hydra's setup with ``PermissionError: [Errno 13] Permission denied: 'outputs'``.

What Isaac Lab v2.3.0 writes, and where:

- Hydra's run folder. ``hydra.run.dir`` defaults to ``outputs/${now:%Y-%m-%d}/${now:%H-%M-%S}``
  (hydra-core 1.3, ``hydra/conf/hydra/output/default.yaml``), relative to the working directory, and
  holds ``.hydra/`` and the job log. Every ``train.py`` and ``play.py`` runs through
  ``@hydra_task_config``.
- ``train.py``'s run folder, ``os.path.abspath(os.path.join("logs", <library>, <experiment>))`` plus
  a timestamp, in all three libraries: checkpoints, TensorBoard events, ``params/*.yaml``, git diffs.
- ``play.py``'s run folder, derived from ``--checkpoint``: the checkpoint's folder for rsl_rl, the
  folder above it for rl_games and skrl. The video goes to ``<run folder>/videos/play`` (gymnasium
  1.2.0's ``RecordVideo`` creates it with ``os.makedirs``), and rsl_rl exports ``policy.pt`` and
  ``policy.onnx`` to ``<checkpoint folder>/exported``.

No container build runs here (the image is over 15 GB), so the ownership half is a static parse of
the Dockerfile, the approach ``infra/test/pipelines/fargateBatchContainerUser.test.ts`` takes for
``USER``. The paths themselves are not restated: the working directory is read from the
``subprocess.run`` call the pipeline makes, and the checkpoint path from the download it performs.
"""

import glob as real_glob
import importlib.util
import os
import re
import shlex
from types import SimpleNamespace

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCKERFILE = os.path.join(_CONTAINER_DIR, "Dockerfile")
_OUTPUT_PREFIX = "s3://run-bucket/pipelines/isaacLab/JOB/output/3f2c9a10/files/"

# The base image keeps Ubuntu's /tmp at mode 1777 (sticky, world-writable): the Isaac Lab 2.3.0 base
# only re-owns it (`chown -R isaac-sim /tmp`), so any user can create a folder there.
_WORLD_WRITABLE = ("/tmp",)

# Folders Isaac Lab creates relative to the working directory of train.py and play.py.
_CWD_RELATIVE_WRITE_ROOTS = ("outputs", "logs")

_LIBRARIES = ("rsl_rl", "rl_games", "skrl")


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


def _instructions(text):
    """``(KEYWORD, argument)`` per Dockerfile instruction, continuation lines joined."""
    instructions, pending = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#") or (not line and not pending):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        pending += line
        keyword, _, argument = pending.partition(" ")
        instructions.append((keyword.upper(), argument.strip()))
        pending = ""
    return instructions


def _commands(run_argument):
    """The simple commands of a shell-form RUN, split on ``&&``, ``||`` and ``;``."""
    for part in re.split(r"&&|\|\||;", run_argument):
        try:
            words = shlex.split(part)
        except ValueError:
            continue
        if words:
            yield words


class _Image:
    """What the Dockerfile hands the runtime user before its ``USER`` line."""

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            instructions = _instructions(f.read())
        user_lines = [i for i, (keyword, _) in enumerate(instructions) if keyword == "USER"]
        assert user_lines, f"{path} declares no USER"
        self.runtime_user = instructions[user_lines[-1]][1]
        uid = self.runtime_user.split(":")[0]
        runs = [argument for keyword, argument in instructions[:user_lines[-1]] if keyword == "RUN"]
        commands = [words for argument in runs for words in _commands(argument)]

        self.names = {uid}
        for words in commands:
            if words[0] == "useradd" and "-u" in words and words[words.index("-u") + 1] == uid:
                self.names.add(words[-1])

        self.created = []  # folders the build makes
        self.owned = []  # (folder, recursive) the build gives the runtime user
        for words in commands:
            arguments = [w for w in words[1:] if not w.startswith("-")]
            if words[0] == "mkdir":
                self.created.extend(os.path.normpath(p) for p in arguments)
            elif words[0] == "chown" and arguments:
                owner, targets = arguments[0].split(":")[0], arguments[1:]
                if owner in self.names:
                    recursive = "-R" in words[1:] or "--recursive" in words[1:]
                    self.owned.extend((os.path.normpath(t), recursive) for t in targets)

    def writable(self, path):
        """Whether the runtime user can create and write ``path`` when the container runs.

        True under a world-writable folder; at a folder the user owns; below a folder owned
        recursively, or below one the build created empty for the user; or directly inside a folder
        the user owns, which lets it create the entry.
        """
        path = os.path.normpath(path)
        if any(_under(path, root) for root in _WORLD_WRITABLE):
            return True
        for folder, recursive in self.owned:
            if path == folder or os.path.dirname(path) == folder:
                return True
            if _under(path, folder) and (recursive or folder in self.created):
                return True
        return False


@pytest.fixture(scope="module")
def image():
    return _Image(_DOCKERFILE)


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
    """The module's ``os`` with ``makedirs`` recorded instead of performed."""

    def __init__(self):
        self.made = []

    def makedirs(self, path, *args, **kwargs):
        self.made.append(path)

    def __getattr__(self, name):
        return getattr(os, name)


def _downloaded_policy_path(main_module, monkeypatch):
    """Where the pipeline puts the evaluation policy it passes to play.py as ``--checkpoint``."""
    destinations = []

    class _Client:
        def download_file(self, bucket, key, local_path):
            destinations.append(local_path)

    monkeypatch.setattr(main_module, "os", _NoDiskOs())
    s3 = SimpleNamespace(client_for_bucket=lambda bucket: _Client())
    returned = main_module.download_policy(s3, _config(main_module, "evaluate"))
    assert destinations == [returned]
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

    def test_the_log_upload_root_is_where_train_py_writes(self, main_module, monkeypatch):
        """Checkpoints, metrics and diffs are uploaded from the folder train.py logs to."""
        cwd = _isaac_lab_cwd(main_module, "train", monkeypatch)
        assert os.path.normpath(main_module.LOCAL_LOG_PATH) == os.path.join(cwd, "logs")


@pytest.mark.unit
class TestEvaluationWritePaths:
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
class TestControls:
    """The checks above are not vacuous: the parser and the virtual glob behave as assumed."""

    def test_the_parser_reads_the_runtime_user_and_its_folders(self, image):
        assert image.runtime_user == "10000:10000"
        assert "vams" in image.names
        # Custom environment installs: pip writes into Isaac Sim's Python, which the user owns.
        assert image.writable("/isaac-sim/kit/python/lib/python3.11/site-packages/pkg")
        assert image.writable("/home/vams/.cache/ov")

    def test_the_parser_reports_root_owned_folders(self, image):
        assert not image.writable("/videos/play")
        assert not image.writable("/workspace/isaaclab/source/isaaclab")

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
