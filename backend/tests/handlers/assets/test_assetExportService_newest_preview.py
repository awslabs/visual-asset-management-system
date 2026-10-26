# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset export attaches each file's newest ``.previewFile.<ext>`` (#466).

A 3D Preview Thumbnail run with another output format writes ``<file>.previewFile.png`` beside an
existing ``<file>.previewFile.gif``. The export used to attach the first allowed preview in S3
listing order (``.gif`` before ``.png``), so after a format change it kept exporting the old
preview while the file listing showed the new one. The export now picks with the same rule as the
listings, ``common.s3PathPatterns.select_newest_preview_file``.
"""

import datetime

import pytest

from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
)
from tests.handlers.assets.test_assetExportService_file_paging import (  # noqa: E402
    _FakeS3,
    _CountingQueryClient,
    _PREFIX,
    _asset_item,
    _run_export,
)

_OLD = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)
_NEW = datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc)


class _DatedS3(_FakeS3):
    """The paging suite's fake, with a LastModified per key instead of one shared value."""

    def __init__(self, modified_by_key):
        super().__init__({_PREFIX: list(modified_by_key)})
        self.modified_by_key = modified_by_key

    def list_object_versions(self, **kwargs):
        response = super().list_object_versions(**kwargs)
        for version in response['Versions']:
            version['LastModified'] = self.modified_by_key[version['Key']]
        return response


def _exported_previews(m, modified_by_key):
    response = _run_export(m, _DatedS3(modified_by_key), _CountingQueryClient(), [_asset_item()])
    return {file['relativePath']: file['previewFile']
            for asset in response['assets'] for file in asset['files']}


@pytest.mark.unit
class TestTheExportAttachesTheNewestPreview:
    def test_a_newer_png_wins_over_an_older_gif_listed_first(self):
        """The distinguishing case: key order puts the old .gif first."""
        m = _load_asset_export_service()
        previews = _exported_previews(m, {
            f"{_PREFIX}model.glb": _OLD,
            f"{_PREFIX}model.glb.previewFile.gif": _OLD,
            f"{_PREFIX}model.glb.previewFile.png": _NEW,
        })
        assert previews == {'/model.glb': '/model.glb.previewFile.png'}, previews

    def test_a_newer_gif_still_wins_when_it_is_also_first_in_key_order(self):
        """Control: the rule is newest, not 'last in key order'."""
        m = _load_asset_export_service()
        previews = _exported_previews(m, {
            f"{_PREFIX}model.glb": _OLD,
            f"{_PREFIX}model.glb.previewFile.gif": _NEW,
            f"{_PREFIX}model.glb.previewFile.png": _OLD,
        })
        assert previews == {'/model.glb': '/model.glb.previewFile.gif'}, previews

    def test_equal_times_keep_the_first_in_key_order(self):
        m = _load_asset_export_service()
        previews = _exported_previews(m, {
            f"{_PREFIX}model.glb": _OLD,
            f"{_PREFIX}model.glb.previewFile.gif": _NEW,
            f"{_PREFIX}model.glb.previewFile.png": _NEW,
        })
        assert previews == {'/model.glb': '/model.glb.previewFile.gif'}, previews

    def test_a_disallowed_extension_is_never_exported_as_the_preview(self):
        m = _load_asset_export_service()
        previews = _exported_previews(m, {
            f"{_PREFIX}model.glb": _OLD,
            f"{_PREFIX}model.glb.previewFile.exe": _NEW,
            f"{_PREFIX}model.glb.previewFile.jpg": _OLD,
        })
        assert previews == {'/model.glb': '/model.glb.previewFile.jpg'}, previews
