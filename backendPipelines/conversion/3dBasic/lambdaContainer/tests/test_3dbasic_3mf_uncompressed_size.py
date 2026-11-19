# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1743: a 3MF's memory follows the XML it holds, so its declared uncompressed size is checked after
the download and before anything is parsed."""

import os
import sys
import zipfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_3dbasic_size_check import FUNCTION_MEMORY_MB, OUTPUT, _client  # noqa: E402
from test_conversion_contract import _load  # noqa: E402

import size_check  # noqa: E402

MB = 1024 ** 2
SMALL_MEMORY_MB = 1024


def _zip_of(path, uncompressed_bytes):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", b"\0" * uncompressed_bytes)


def _run(mod, uncompressed_bytes, output=".glb", memory_mb=SMALL_MEMORY_MB):
    zip_size = []

    def _download(bucket, key, path, **kwargs):
        _zip_of(path, uncompressed_bytes)
        zip_size.append(os.path.getsize(path))
        return path

    worker = MagicMock(side_effect=AssertionError("the package was parsed"))
    with patch.object(mod, "_s3_client_for_bucket", lambda bucket: _client(1000)), \
            patch.object(mod, "download", MagicMock(side_effect=_download)), \
            patch.object(mod, "check_input_text", MagicMock()), \
            patch.object(mod, "run_worker", worker), \
            patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": str(memory_mb)}), \
            patch.object(size_check, "free_disk_bytes", lambda path: 100 * 1024 ** 3):
        try:
            mod.convert_input_output("s3://abkt/xid/part.3mf", OUTPUT, output, "", "")
        finally:
            assert zip_size and zip_size[0] < 1 * MB  # the zip itself is far under any limit
    return worker


@pytest.mark.unit
class TestDeclaredUncompressedSize:
    def test_a_small_zip_with_a_large_declared_size_is_refused_before_any_parse(self):
        mod = _load()
        with pytest.raises(size_check.InputTooLargeError) as excinfo:
            _run(mod, 40 * MB)
        message = str(excinfo.value)
        assert "part.3mf" in message and "40.0 MB uncompressed" in message
        assert "converting .3mf to .glb" in message and "function memory" in message

    def test_the_collada_output_has_its_own_lower_limit(self):
        assert size_check.uncompressed_expansion_factor(".3mf") == 26
        assert size_check.uncompressed_expansion_factor(".3mf", ".dae") == 37
        assert size_check.uncompressed_expansion_factor(".stl") is None

    def test_the_limit_is_the_usable_memory_over_the_factor(self):
        memory = FUNCTION_MEMORY_MB * MB
        limit = int((memory * size_check.MEMORY_USABLE_FRACTION - size_check.BASE_MEMORY_BYTES) / 26)
        assert size_check.check_uncompressed_size("a.3mf", ".3mf", limit, memory) == limit
        with pytest.raises(size_check.InputTooLargeError):
            size_check.check_uncompressed_size("a.3mf", ".3mf", limit + 1, memory)

    def test_a_normal_3mf_is_not_refused_by_the_check(self):
        mod = _load()
        # 8 MB declared is under the 23.9 MB limit at 1,024 MB; the run proceeds to the worker.
        with pytest.raises(AssertionError, match="the package was parsed"):
            _run(mod, 8 * MB)

    def test_a_file_that_is_not_a_zip_is_left_to_the_format_check(self, tmp_path):
        path = tmp_path / "bad.3mf"
        path.write_bytes(b"not a zip")
        assert size_check.declared_uncompressed_bytes(str(path)) is None
        assert size_check.check_uncompressed_size("bad.3mf", ".3mf", None, 1024 * MB) is None
