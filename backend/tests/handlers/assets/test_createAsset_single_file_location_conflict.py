# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The create-time location conflict check knows which keys a single-file location holds.

A single-file location (no trailing ``/``) holds its object, its companion folder ``{key}/`` and its own
previews ``{key}.previewFile.{ext}``. ``keys_conflict`` compared both keys as folders, so a
``bucketExistingKey`` naming the own preview of an existing single-file asset (or a single-file asset
created over an existing asset whose location is one of its previews) was accepted, and two assets then
held the same object. The pairs run in both orders, directly and through the ownership check the
``bucketExistingKey`` branch runs.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load

CONFLICTS = [
    ("p/model.glb", "p/model.glb.previewFile.png"),
    ("p/model.glb", "p/model.glb"),
    ("p/model.glb", "p/model.glb/"),
    ("p/model.glb", "p/model.glb/sub/x.obj"),
    ("p/", "p/model.glb"),
    ("p/a/", "p/a/b/"),
    ("p/a/", "p/a"),
    ("/p/model.glb", "p/model.glb.previewFile.gif"),
]
NO_CONFLICT = [
    ("p/model.glb", "p/model.glb.bak"),
    ("p/model.glb", "p/model.glbx/inner.txt"),
    ("p/model.glb", "p/model.glb.previewFile.png/"),
    ("p/a/", "p/a-other/"),
    ("p/model.glb/", "p/model.glb.previewFile.png"),
]


def _ordered(pairs):
    return [pair for a, b in pairs for pair in ((a, b), (b, a))]


@pytest.mark.unit
class TestKeysConflict:
    @pytest.mark.parametrize("existing,target,expected",
                             [(a, b, True) for a, b in _ordered(CONFLICTS)]
                             + [(a, b, False) for a, b in _ordered(NO_CONFLICT)])
    def test_locations_conflict_when_they_share_a_key_in_either_order(self, existing, target, expected):
        assert _load(fresh=True).keys_conflict(existing, target) is expected


@pytest.mark.unit
class TestBucketExistingKeyOwnership:
    def _check(self, existing, target):
        m = _load(fresh=True)
        m.asset_table = MagicMock()
        m.asset_table.query.return_value = {"Items": [
            {"databaseId": "db-b", "assetId": "b1", "assetLocation": {"Key": existing}}]}
        m.assert_existing_key_not_owned(["bucket-1"], target)
        return m

    @pytest.mark.parametrize("existing,target", [
        ("p/model.glb", "p/model.glb.previewFile.png"),
        ("p/model.glb.previewFile.png", "p/model.glb"),
    ])
    def test_an_import_onto_a_single_file_asset_or_its_preview_is_refused(self, existing, target):
        # A key that only shares the characters is accepted
        self._check(existing, existing + ".bak")
        with pytest.raises(Exception) as e:
            self._check(existing, target)
        assert "already in use by another asset" in str(e.value)
