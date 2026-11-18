# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The vector indexer never indexes a preview object the Media Preview Thumbnail workflow writes.

The workflow writes `<file>.previewFile.<ext>` beside an image, a TGA/TIFF file or a video (a JPEG, PNG or
animated GIF; `.webp` and `.bmp` are also allowed preview formats since #1550/#1583). Each is ignored before
any lookup, like the 3D thumbnail's previews (`test_vectorIndexer_s3_lifecycle.py`), on create and on delete.
"""

import pytest

from tests.handlers.osVectorSearch.test_vectorIndexer_s3_lifecycle import _record, indexer  # noqa: F401

KEYS = [
    "prefix-a/a1/photos/site.png.previewFile.jpg",
    "prefix-a/a1/scans/texture.tif.previewFile.jpg",
    "prefix-a/a1/clips/walkthrough.mp4.previewFile.gif",
    "prefix-a/a1/photos/large.webp.previewFile.webp",
    "prefix-a/a1/photos/old.bmp.previewFile.bmp",
]


@pytest.mark.unit
@pytest.mark.parametrize("event", ["ObjectCreated:Put", "ObjectCreated:CompleteMultipartUpload",
                                   "ObjectRemoved:Delete"])
@pytest.mark.parametrize("key", KEYS)
def test_a_media_preview_object_is_ignored_without_any_lookup(indexer, key, event):  # noqa: F811
    outcome = indexer.handle_s3_record(_record(event, key=key), "assets", "prefix-a/")
    assert outcome.ok and outcome.action == "ignore" and outcome.detail == "preview file"
    assert indexer.vector_store.calls == []
    indexer.s3_client.head_object.assert_not_called()


@pytest.mark.unit
def test_the_image_itself_is_not_ignored_as_a_preview(indexer):  # noqa: F811
    outcome = indexer.handle_s3_record(_record("ObjectCreated:Put", key="prefix-a/a1/photos/site.png"),
                                       "assets", "prefix-a/")
    assert outcome.detail != "preview file"
    indexer.s3_client.head_object.assert_called()
