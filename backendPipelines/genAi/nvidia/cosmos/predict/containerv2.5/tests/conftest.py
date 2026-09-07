#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Loading and stubbing for the Cosmos Predict 2.5 container tests.

The container is a Batch entrypoint, not a package: it is `__main__.py` plus three sibling modules
whose names (`inference`, `model_manager`, `manifest_io`) are the same names every other NVIDIA
container uses for its own copies. Loading is therefore hermetic in both directions -- a sibling
container's cached `inference` is not inherited, and this one's is not left behind for a sibling that
loads later in the same pytest process.
"""

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The sibling modules `__main__.py` imports by bare name, which every NVIDIA container also has.
_SIBLING_MODULES = ("inference", "model_manager", "manifest_io")


def load_container_entrypoint():
    """The container entrypoint and its siblings, loaded under an alias.

    `__main__.py` cannot be imported under its own name -- that name belongs to the running
    interpreter -- and loading it from its path also leaves its `if __name__ == "__main__"` guard
    inert, so importing it does not start a pipeline. Returns `(entrypoint, siblings)`; the siblings
    are captured before `sys.modules` is restored, since that is the only way to reach THIS
    container's copy afterwards.
    """
    saved = {name: sys.modules.get(name) for name in _SIBLING_MODULES}
    sys.path.insert(0, CONTAINER_DIR)
    try:
        for name in _SIBLING_MODULES:
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(
            "predict25_container_entrypoint", os.path.join(CONTAINER_DIR, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        siblings = {name: sys.modules[name] for name in _SIBLING_MODULES if name in sys.modules}
    finally:
        sys.path.remove(CONTAINER_DIR)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module, siblings


ASSET_BUCKET = "vams-assets-bucket"
ASSET_ID = "xASSET1"
FILES_PREFIX = "pipelines/predict/JOB/output/E1/files/"


def base_definition(**overrides):
    """A pipeline definition as `constructPipeline` builds it, for text2world 2B."""
    definition = {
        "modelType": "text2world",
        "modelSize": "2B",
        "assetId": ASSET_ID,
        "cosmosPrompt": "a robot arm on a workbench",
        "outputS3AssetFilesPath": f"s3://{ASSET_BUCKET}/{FILES_PREFIX}",
        "inputConfigurationS3Location": "",
    }
    definition.update(overrides)
    return definition


def video2world_definition(**overrides):
    """A video2world definition: the one mode that consumes an input file."""
    definition = base_definition(
        modelType="video2world",
        inputS3AssetFilePath=f"s3://{ASSET_BUCKET}/{ASSET_ID}/source.mp4")
    definition.update(overrides)
    return definition


class ContainerRun:
    """What one stubbed `main()` did."""

    def __init__(self):
        self.model_restores = []
        self.downloads = []
        self.uploads = []
        self.inference = []
        self.cache_backups = []

    @property
    def upload_uris(self):
        return [uri for _, uri in self.uploads]

    @property
    def uploaded_files(self):
        return [Path(local).name for local, _ in self.uploads]

    @property
    def output_keys(self):
        prefix = f"s3://{ASSET_BUCKET}/{FILES_PREFIX}"
        assert all(uri.startswith(prefix) for uri in self.upload_uris), self.upload_uris
        return [uri[len(prefix):] for uri in self.upload_uris]


@pytest.fixture(scope="session")
def loaded():
    return load_container_entrypoint()


@pytest.fixture(scope="session")
def container(loaded):
    return loaded[0]


@pytest.fixture(scope="session")
def inference_module(loaded):
    """This container's own `inference` module, for the constant it shares with the entrypoint."""
    return loaded[1]["inference"]


@pytest.fixture
def run_container(container, monkeypatch, tmp_path):
    """Run `main()` with the S3 and GPU work stubbed, and return the record of what it did.

    `artifacts` are the files the inference stub creates in the output directory, in the order named
    and with increasing modification times. The stub also writes the framework's own sidecars
    (`{sample}.json`, `config.yaml`), so a test cannot pass by their absence.
    """

    def _run(definition, artifacts=("vams_inference.mp4",)):
        record = ContainerRun()
        _run.last = record
        monkeypatch.setattr(container, "INPUT_DIR", tmp_path / "input")
        monkeypatch.setattr(container, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(container, "load_pipeline_definition", lambda: definition)
        monkeypatch.setattr(container, "ensure_models_cached",
                            lambda **kwargs: record.model_restores.append(kwargs))
        monkeypatch.setattr(container, "fetch_input_configuration", lambda *args, **kwargs: {})

        def download(s3_uri, local_path):
            # The stub WRITES: main() reads the downloaded file's size, and a recorder alone would
            # leave the input-file assertions satisfied by nothing.
            record.downloads.append(s3_uri)
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_bytes(b"input bytes")

        def upload(local_path, s3_uri):
            assert Path(local_path).is_file(), f"uploaded a path that does not exist: {local_path}"
            record.uploads.append((str(local_path), s3_uri))

        def inference(**kwargs):
            record.inference.append(kwargs)
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            for index, name in enumerate(artifacts):
                artifact = output_dir / name
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(b"generated bytes")
                os.utime(artifact, (1_700_000_000 + index, 1_700_000_000 + index))
            # The framework's own sidecars, which are not videos and must never be selected.
            (output_dir / f"{container.INFERENCE_SAMPLE_NAME}.json").write_text(
                "{}", encoding="utf-8")
            (output_dir / "config.yaml").write_text("model: x\n", encoding="utf-8")
            return str(output_dir)

        monkeypatch.setattr(container, "download_from_s3", download)
        monkeypatch.setattr(container, "upload_to_s3", upload)
        monkeypatch.setattr(container, "run_inference", inference)
        monkeypatch.setattr(container, "generate_preview_gif",
                            lambda video_path, output_path: Path(output_path).write_bytes(b"gif"))

        # `main()` imports backup_cache_to_s3 lazily, so the stub has to be a module in sys.modules
        # rather than an attribute on this one.
        stub_model_manager = types.ModuleType("model_manager")
        stub_model_manager.backup_cache_to_s3 = (
            lambda **kwargs: record.cache_backups.append(kwargs))
        monkeypatch.setitem(sys.modules, "model_manager", stub_model_manager)

        monkeypatch.setenv("S3_MODEL_BUCKET", "model-cache-bucket")

        container.main()
        return record

    _run.last = None
    return _run
