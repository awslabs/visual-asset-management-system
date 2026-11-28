#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Cosmos Predict 2.5: the container downloads its input file at the S3 version the run recorded at launch.

``constructPipeline`` puts ``inputVersionId`` on the definition. With a version the download is ``aws
s3api get-object --version-id`` (``aws s3 cp`` cannot take one); without one it is today's exact ``aws
s3 cp`` argv. A version deleted after launch fails the run naming the file and version. The container
is loaded from its path under an alias, with its bare-name sibling modules saved and restored, so the
load is hermetic against the other NVIDIA containers' modules of the same names."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIBLING_MODULES = ("inference", "model_manager", "manifest_io", "manifestHelper")


@pytest.fixture(scope="module")
def entrypoint():
    saved = {name: sys.modules.get(name) for name in _SIBLING_MODULES}
    sys.path.insert(0, CONTAINER_DIR)
    try:
        for name in _SIBLING_MODULES:
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location("cosmos_predict25_input_version_entrypoint", os.path.join(CONTAINER_DIR, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(CONTAINER_DIR)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    yield module
    module.manifest_io.register_bucket_regions(None)


URI = "s3://abkt/xidM/clips/source.mp4"


def test_without_a_version_the_download_is_unchanged(entrypoint, tmp_path):
    local = tmp_path / "in.mp4"
    assert entrypoint.input_download_command(URI, local) == ["aws", "s3", "cp", URI, str(local)]
    assert entrypoint.input_download_command(URI, local, "") == ["aws", "s3", "cp", URI, str(local)]


def test_with_a_version_the_input_is_read_at_it(entrypoint, tmp_path):
    local = tmp_path / "in.mp4"
    assert entrypoint.input_download_command(URI, local, "v-launch") == [
        "aws", "s3api", "get-object", "--bucket", "abkt", "--key", "xidM/clips/source.mp4",
        "--version-id", "v-launch", str(local)]


def test_the_bucket_region_is_kept(entrypoint, tmp_path):
    entrypoint.manifest_io.register_bucket_regions({"abkt": "eu-west-1"})
    try:
        assert entrypoint.input_download_command(URI, tmp_path / "x", "v-1")[-2:] == ["--region", "eu-west-1"]
        assert entrypoint.input_download_command(URI, tmp_path / "x")[-2:] == ["--region", "eu-west-1"]
    finally:
        entrypoint.manifest_io.register_bucket_regions(None)


def test_a_key_is_taken_verbatim(entrypoint, tmp_path):
    # `aws s3 cp` keeps a query-like or doubled-slash key as is; so does the versioned read.
    command = entrypoint.input_download_command("s3://abkt//xidM/a?b#c.mp4", tmp_path / "x", "v-1")
    assert command[command.index("--key") + 1] == "/xidM/a?b#c.mp4"


def _run_download(entrypoint, tmp_path, version_id, returncode=0, stderr=""):
    local = tmp_path / "in.mp4"

    def fake_run(argv, **kwargs):
        if returncode == 0:
            local.write_bytes(b"data")
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    run = MagicMock(side_effect=fake_run)
    with patch.object(entrypoint.subprocess, "run", run):
        entrypoint.download_from_s3(URI, local, version_id)
    return run.call_args.args[0]


def test_the_download_sends_the_version(entrypoint, tmp_path):
    assert _run_download(entrypoint, tmp_path, "v-launch")[:3] == ["aws", "s3api", "get-object"]
    assert _run_download(entrypoint, tmp_path, "")[:3] == ["aws", "s3", "cp"]


@pytest.mark.parametrize("stderr", [
    "An error occurred (NoSuchVersion) when calling the GetObject operation: The specified version does not exist.",
    "An error occurred (NoSuchKey) when calling the GetObject operation: The specified key does not exist.",
    "An error occurred (405) when calling the GetObject operation: Method Not Allowed",
])
def test_a_deleted_version_fails_naming_it(entrypoint, tmp_path, stderr):
    with pytest.raises(RuntimeError) as raised:
        _run_download(entrypoint, tmp_path, "v-gone", returncode=254, stderr=stderr)
    assert "xidM/clips/source.mp4" in str(raised.value) and "v-gone" in str(raised.value)
    assert "deleted" in str(raised.value)


@pytest.mark.parametrize("stderr", [
    "An error occurred (AccessDenied) when calling the GetObject operation",
    "An error occurred (AuthorizationHeaderMalformed) when calling the GetObject operation (400)",
    "An error occurred (InvalidArgument) when calling the GetObject operation: Invalid version id specified",
])
def test_another_failure_keeps_its_message(entrypoint, tmp_path, stderr):
    with pytest.raises(RuntimeError, match="S3 download failed: ") as raised:
        _run_download(entrypoint, tmp_path, "v-1", returncode=254, stderr=stderr)
    assert "deleted" not in str(raised.value)


def test_main_passes_the_definition_version_to_the_input_download():
    with open(os.path.join(CONTAINER_DIR, "__main__.py"), "r", encoding="utf-8") as f:
        source = f.read()
    assert 'input_version_id = definition.get("inputVersionId") or ""' in source
    assert "download_from_s3(input_s3_asset_file_path, input_file_path, input_version_id)" in source
