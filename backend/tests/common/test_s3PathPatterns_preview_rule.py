#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The one definition of "an image the web shows as its own preview" (#1550).

A file is its own preview when its extension is an allowed preview extension and its size is STRICTLY
under the 5 MiB preview limit. The backend list and limit are mirrored by the web
(`web/src/common/constants/fileFormats.ts`) and the 3D thumbnail container
(`image_utils.MAX_FILE_SIZE_BYTES`); these tests read the other files and fail when one drifts. TGA and
TIFF are never in the list: browsers cannot display them."""

import os
import re

import pytest

from common.s3PathPatterns import (
    ALLOWED_PREVIEW_FILE_EXTENSIONS,
    PREVIEW_FILE_SIZE_LIMIT_BYTES,
    is_self_previewable,
    preview_file_has_allowed_extension,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WEB = os.path.join(REPO, "web", "src", "common", "constants", "fileFormats.ts")
CONTAINER_IMAGE_UTILS = os.path.join(
    REPO, "backendPipelines", "preview", "3dThumbnail", "container", "preview_pipeline", "utils",
    "image_utils.py")
UPLOAD_HANDLERS = [
    os.path.join(REPO, "backend", "backend", "handlers", "assets", name)
    for name in ("uploadFile.py", "sqsUploadFileLarge.py")
]


def test_the_limit_is_5_mib():
    assert PREVIEW_FILE_SIZE_LIMIT_BYTES == 5 * 1024 * 1024 == 5242880


def test_webp_and_bmp_are_allowed_and_tga_and_tiff_are_not():
    assert {".webp", ".bmp"} <= set(ALLOWED_PREVIEW_FILE_EXTENSIONS)
    assert not {".tga", ".tif", ".tiff"} & set(ALLOWED_PREVIEW_FILE_EXTENSIONS)
    assert preview_file_has_allowed_extension("a.glb.previewFile.webp")
    assert preview_file_has_allowed_extension("a.glb.previewFile.bmp")
    assert not preview_file_has_allowed_extension("a.glb.previewFile.tif")


@pytest.mark.parametrize("name,size,expected", [
    ("a.png", 0, True),
    ("a.png", 4_999_999, True),
    ("a.PNG", 5_242_879, True),
    ("a.png", 5_242_880, False),
    ("a.png", 5_242_881, False),
    ("a.webp", 10, True),
    ("a.bmp", 10, True),
    ("a.jpeg", 10, True),
    ("a.tga", 10, False),
    ("a.tif", 10, False),
    ("a.tiff", 10, False),
    ("a.mp4", 10, False),
    ("a.png", None, False),
    ("noextension", 10, False),
    ("scan.png.tif", 10, False),
    ("scan.tif.png", 10, True),
])
def test_is_self_previewable(name, size, expected):
    assert is_self_previewable(name, size) is expected


def test_a_boolean_or_text_size_is_not_a_size():
    assert is_self_previewable("a.png", True) is False
    assert is_self_previewable("a.png", "10") is False


def test_the_upload_handlers_use_the_shared_limit():
    for path in UPLOAD_HANDLERS:
        with open(path) as handle:
            text = handle.read()
        assert "MAX_PREVIEW_FILE_SIZE = PREVIEW_FILE_SIZE_LIMIT_BYTES" in text, path
        assert "5 * 1024 * 1024" not in text, path


@pytest.mark.skipif(not os.path.isfile(WEB), reason="the web app is not checked out here")
def test_the_web_lists_and_limit_match():
    with open(WEB) as handle:
        text = handle.read()
    formats = re.search(r"export const previewFileFormats = \[([^\]]*)\]", text).group(1)
    assert re.findall(r'"(\.[a-z0-9]+)"', formats) == list(ALLOWED_PREVIEW_FILE_EXTENSIONS)
    limit = re.search(r"PREVIEW_FILE_SIZE_LIMIT_BYTES = (\d+) \* 1024 \* 1024", text)
    assert limit and int(limit.group(1)) * 1024 * 1024 == PREVIEW_FILE_SIZE_LIMIT_BYTES


@pytest.mark.skipif(not os.path.isfile(CONTAINER_IMAGE_UTILS), reason="the pipeline is not checked out here")
def test_the_container_limit_matches():
    with open(CONTAINER_IMAGE_UTILS) as handle:
        text = handle.read()
    limit = re.search(r"^MAX_FILE_SIZE_BYTES = (\d+) \* 1024 \* 1024", text, re.M)
    assert limit and int(limit.group(1)) * 1024 * 1024 == PREVIEW_FILE_SIZE_LIMIT_BYTES
