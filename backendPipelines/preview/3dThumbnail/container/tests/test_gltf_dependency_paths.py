#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the GLTF external-resource URIs the container downloads beside a .gltf input.

A .gltf file is ordinary asset content, so its buffer and image URIs carry whatever the uploader
wrote. Each URI is joined to the container's download directory and to the input file's own S3
prefix, so an absolute or parent-relative value escapes both: the local join reaches any path in the
container filesystem (the download opens it "wb", creating or truncating it as root) and the S3 join
reads outside the input file's directory.

Guards S4-PIPELINES-016 (CWE-22)."""

import json
import os

from unittest.mock import MagicMock, patch

import pytest

from preview_pipeline import core

BUCKET = "asset-bucket"
OBJECT_KEY = "xid/models/scene.gltf"
S3_DIR = "xid/models"

TRAVERSALS = [
    "/etc/ld.so.preload",
    "//etc/ld.so.preload",
    "../../../../etc/ld.so.preload",
    "../sibling-asset/buffer.bin",
    "..",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "%2Fetc%2Fld.so.preload",
    "textures/../../../../etc/ld.so.preload",
    "..\\..\\Windows\\System32\\drivers\\etc\\hosts",
    "file:///etc/ld.so.preload",
    "http://169.254.169.254/latest/meta-data/",
]


def _write_gltf(local_dir, uris, key="images"):
    """Write the .gltf the container has already downloaded, declaring uris as external resources."""
    gltf = {"buffers": [], "images": []}
    gltf[key] = [{"uri": uri} for uri in uris]
    with open(os.path.join(local_dir, os.path.basename(OBJECT_KEY)), "w", encoding="utf-8") as f:
        json.dump(gltf, f)


def _download_dependencies(tmp_path, uris, key="images"):
    """Run the dependency download against stubbed S3 + makedirs.

    Returns (download mock, makedirs mock). makedirs is stubbed so an escaping path cannot create a
    directory outside tmp_path while this test runs against unfixed code."""
    local_dir = str(tmp_path)
    _write_gltf(local_dir, uris, key=key)
    download = MagicMock(side_effect=lambda bucket, key_, path: path)
    makedirs = MagicMock()
    with patch.object(core.s3, "download", download), \
            patch.object(core.os, "makedirs", makedirs):
        core._download_gltf_dependencies(BUCKET, OBJECT_KEY, local_dir)
    return download, makedirs


def _assert_confined(tmp_path, download, makedirs):
    """Every local path written to, and every directory created, stays inside tmp_path."""
    base = os.path.realpath(str(tmp_path))
    for call in download.call_args_list:
        target = os.path.realpath(call.args[2])
        assert target.startswith(base + os.sep), f"download escaped the local directory: {target}"
    for call in makedirs.call_args_list:
        target = os.path.realpath(call.args[0])
        assert target == base or target.startswith(base + os.sep), \
            f"makedirs escaped the local directory: {target}"


def _assert_within_s3_prefix(download):
    """Every S3 key read stays under the .gltf file's own directory."""
    for call in download.call_args_list:
        key = call.args[1]
        assert key.startswith(S3_DIR + "/"), f"S3 key escaped the input prefix: {key}"
        assert ".." not in key.split("/"), f"S3 key carries a parent segment: {key}"


@pytest.mark.unit
class TestTraversalUrisAreRejected:
    @pytest.mark.parametrize("uri", TRAVERSALS)
    def test_a_traversal_uri_downloads_nothing(self, tmp_path, uri):
        """The positive control: unfixed, os.path.join puts the local path at the traversal target
        and s3_utils.download opens it "wb", so the container writes the file as root."""
        download, makedirs = _download_dependencies(tmp_path, [uri])
        _assert_confined(tmp_path, download, makedirs)
        download.assert_not_called()

    @pytest.mark.parametrize("uri", TRAVERSALS)
    def test_a_traversal_buffer_uri_downloads_nothing(self, tmp_path, uri):
        """Buffers are read from the same untrusted document as images."""
        download, makedirs = _download_dependencies(tmp_path, [uri], key="buffers")
        _assert_confined(tmp_path, download, makedirs)
        download.assert_not_called()

    def test_a_legitimate_sibling_still_downloads_when_a_traversal_is_present(self, tmp_path):
        """A rejected URI is skipped rather than failing the whole document, so the resources that
        do resolve are still fetched."""
        download, makedirs = _download_dependencies(
            tmp_path, ["/etc/ld.so.preload", "textures/wood.png"])
        _assert_confined(tmp_path, download, makedirs)
        _assert_within_s3_prefix(download)
        assert download.call_count == 1
        assert download.call_args.args[1] == f"{S3_DIR}/textures/wood.png"


@pytest.mark.unit
class TestLegitimateUrisStillResolve:
    def test_a_sibling_resource(self, tmp_path):
        download, makedirs = _download_dependencies(tmp_path, ["buffer.bin"])
        _assert_confined(tmp_path, download, makedirs)
        assert download.call_args.args[1] == f"{S3_DIR}/buffer.bin"
        assert os.path.realpath(download.call_args.args[2]) == \
            os.path.realpath(os.path.join(str(tmp_path), "buffer.bin"))

    def test_a_nested_resource(self, tmp_path):
        download, makedirs = _download_dependencies(tmp_path, ["textures/nested/wood.png"])
        _assert_confined(tmp_path, download, makedirs)
        assert download.call_args.args[1] == f"{S3_DIR}/textures/nested/wood.png"
        assert makedirs.call_count == 1

    def test_a_percent_encoded_name_resolves_to_the_object_it_names(self, tmp_path):
        """The GLTF spec percent-encodes a URI, so the S3 object is the DECODED name. Reading the
        encoded spelling looks for an object nobody uploaded."""
        download, _ = _download_dependencies(tmp_path, ["my%20texture.png"])
        assert download.call_args.args[1] == f"{S3_DIR}/my texture.png"

    def test_a_current_directory_prefix_is_normalized_away(self, tmp_path):
        """S3 keys are literal, so "./buffer.bin" would name a different object than "buffer.bin"."""
        download, _ = _download_dependencies(tmp_path, ["./buffer.bin"])
        assert download.call_args.args[1] == f"{S3_DIR}/buffer.bin"

    def test_an_embedded_data_uri_is_not_downloaded(self, tmp_path):
        download, _ = _download_dependencies(
            tmp_path, ["data:application/octet-stream;base64,AAAA"])
        download.assert_not_called()


@pytest.mark.unit
class TestResolveGltfDependency:
    @pytest.mark.parametrize("uri", TRAVERSALS)
    def test_rejected(self, tmp_path, uri):
        assert core._resolve_gltf_dependency(uri, str(tmp_path)) is None

    @pytest.mark.parametrize("uri", ["", "   ", None])
    def test_a_blank_uri_is_rejected(self, tmp_path, uri):
        assert core._resolve_gltf_dependency(uri, str(tmp_path)) is None

    def test_accepted_paths_keep_their_relative_shape(self, tmp_path):
        relative, local = core._resolve_gltf_dependency("textures/wood.png", str(tmp_path))
        assert relative == "textures/wood.png"
        assert os.path.realpath(local).startswith(os.path.realpath(str(tmp_path)) + os.sep)
