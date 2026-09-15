#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the isaacLab container's output prefix resolution.

The workflow hands the container a per-execution output-files prefix
(``pipelines/{p}/{j}/output/{executionId}/files/``) and the write-back step maps whatever relative
path the container writes below it onto the output asset, so outputs must hang directly off that
prefix rather than under a further execution-id folder."""

import json
import os
import re
import importlib.util

import pytest

_OUTPUT_PREFIX = "s3://run-bucket/pipelines/isaacLab/JOB/output/3f2c9a10/files/"

_PIPELINE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_PIPELINE_DIR)))

# The run configuration the container saves is named from the mode, so the only two names it can
# ever write are `train-config.json` and `evaluate-config.json`. `training-config.json` and
# `evaluation-config.json` name the INPUT configuration an operator authors, which is a different
# artefact -- so those spellings are legitimate only as part of a longer hyphenated name
# (`*-training-config.json`, `cartpole-evaluation-config.json`).
_WRONG_OUTPUT_NAME = re.compile(r"(?<![-\w])(?:training|evaluation)-config\.json")

_DOC_SOURCES = (
    os.path.join(_PIPELINE_DIR, "USER_GUIDE.md"),
    os.path.join(_PIPELINE_DIR, "README.md"),
    os.path.join(_PIPELINE_DIR, "container", "__main__.py"),
    os.path.join(_REPO_ROOT, "documentation", "docusaurus-site", "docs", "pipelines",
                 "nvidia-isaac-lab.md"),
)


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file (its name is ``__main__.py``)."""
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "isaaclab_container_main", os.path.join(container_dir, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestResolveOutputBasePath:
    def test_execution_prefix_is_used_verbatim(self, main_module):
        assert main_module.resolve_output_base_path(_OUTPUT_PREFIX) == _OUTPUT_PREFIX

    def test_missing_trailing_slash_is_added(self, main_module):
        assert main_module.resolve_output_base_path(_OUTPUT_PREFIX.rstrip("/")) == _OUTPUT_PREFIX

    def test_empty_path_stays_empty(self, main_module):
        assert main_module.resolve_output_base_path("") == ""

    def test_execution_id_is_not_appended(self, main_module):
        base = main_module.resolve_output_base_path(_OUTPUT_PREFIX)
        execution_id = main_module.get_job_uuid_from_output_path(_OUTPUT_PREFIX)
        assert execution_id == "3f2c9a10"
        assert not base.endswith(f"{execution_id}/{execution_id}/")
        assert f"{base}checkpoints/model_1500.pt".endswith(
            "output/3f2c9a10/files/checkpoints/model_1500.pt")


class _RecordingS3:
    """An S3 client that RECORDS what it was asked to upload.

    `upload_config` wraps its upload in a bare `except Exception` and only prints a warning, so a
    stub that raised — or a MagicMock, which returns a truthy result for anything — would let the
    test pass with nothing uploaded. Asserting on the recorded destination is what makes it fail.
    """

    def __init__(self):
        self.uploads = []

    def upload_file(self, local_path, s3_path):
        self.uploads.append((local_path, s3_path))


@pytest.fixture
def redirect_tmp(monkeypatch, tmp_path):
    """Send the container's `/tmp/...` writes into pytest's tmp_path.

    `upload_config` writes to a literal `/tmp/` path, which is not a directory on every platform the
    suite runs on. `open` is resolved through the module's globals before the builtins, so patching
    it there redirects the write without touching the code under test.
    """
    real_open = open

    def _open(path, *args, **kwargs):
        text = str(path)
        if text.startswith("/tmp/"):
            path = str(tmp_path / text[len("/tmp/"):])
        return real_open(path, *args, **kwargs)

    def _apply(module):
        monkeypatch.setattr(module, "open", _open, raising=False)
        return tmp_path

    return _apply


@pytest.mark.unit
class TestUploadConfig:
    """The saved run configuration is named from the mode, which is what the docs must state."""

    def _upload(self, main_module, mode, tmp_dir):
        config = main_module.PipelineConfig(
            job_name="isaaclab-job",
            mode=mode,
            task="Isaac-Cartpole-v0",
            rl_library="rsl_rl",
            input_s3_path="s3://in/asset/config.json",
            output_s3_path=_OUTPUT_PREFIX,
        )
        s3 = _RecordingS3()
        main_module.upload_config(s3, config, {"trainingConfig": {"mode": mode}})
        return s3

    def test_train_mode_saves_train_config_json(self, main_module, redirect_tmp):
        tmp_dir = redirect_tmp(main_module)
        s3 = self._upload(main_module, "train", tmp_dir)

        assert len(s3.uploads) == 1
        local_path, s3_path = s3.uploads[0]
        assert s3_path == f"{_OUTPUT_PREFIX}train-config.json"
        assert local_path == "/tmp/train-config.json"
        # The body really was written, so the recorded key is not the only thing that is right
        assert json.loads((tmp_dir / "train-config.json").read_text())["trainingConfig"]["mode"] \
            == "train"

    def test_evaluate_mode_saves_evaluate_config_json(self, main_module, redirect_tmp):
        tmp_dir = redirect_tmp(main_module)
        s3 = self._upload(main_module, "evaluate", tmp_dir)

        assert len(s3.uploads) == 1
        assert s3.uploads[0][1] == f"{_OUTPUT_PREFIX}evaluate-config.json"
        assert (tmp_dir / "evaluate-config.json").exists()

    def test_the_two_modes_do_not_share_a_filename(self, main_module, redirect_tmp):
        """Positive control: a hardcoded name would satisfy one of the two cases above."""
        redirect_tmp(main_module)
        train = self._upload(main_module, "train", None).uploads[0][1]
        evaluate = self._upload(main_module, "evaluate", None).uploads[0][1]
        assert train != evaluate


@pytest.mark.unit
class TestSavedConfigNameIsDocumentedCorrectly:
    """A ratchet on the four places that name the saved run configuration.

    `upload_config` writes `{mode}-config.json`, so `training-config.json` and
    `evaluation-config.json` are names it cannot produce. Both spellings are still correct for the
    INPUT configuration an operator authors, which is why the pattern only rejects them when they are
    not part of a longer hyphenated name.
    """

    @pytest.mark.parametrize("path", _DOC_SOURCES, ids=lambda p: os.path.basename(p))
    def test_no_source_claims_a_name_the_container_cannot_write(self, path):
        assert os.path.isfile(path), f"{path} is missing — the ratchet would pass vacuously"
        text = open(path, encoding="utf-8").read()
        offenders = [
            line.strip()
            for line in text.split("\n")
            if _WRONG_OUTPUT_NAME.search(line)
        ]
        assert not offenders, (
            f"{os.path.basename(path)} names the saved run configuration as "
            f"training-config.json / evaluation-config.json; upload_config writes "
            f"{{mode}}-config.json, i.e. train-config.json or evaluate-config.json:\n"
            + "\n".join(offenders)
        )

    def test_the_input_config_naming_is_not_swept_up(self):
        """Positive control: the pattern must still allow `*-training-config.json` and friends."""
        assert not _WRONG_OUTPUT_NAME.search("### Training Config (`*-training-config.json`)")
        assert not _WRONG_OUTPUT_NAME.search("cartpole-training-config.json")
        assert not _WRONG_OUTPUT_NAME.search("cartpole-evaluation-config.json")
        # ...and must still catch the bare spellings it exists to catch
        assert _WRONG_OUTPUT_NAME.search("| `{uuid}/training-config.json` |")
        assert _WRONG_OUTPUT_NAME.search("- {output_s3_path}evaluation-config.json")
        assert _WRONG_OUTPUT_NAME.search("    ├── training-config.json  # Copy of input")
