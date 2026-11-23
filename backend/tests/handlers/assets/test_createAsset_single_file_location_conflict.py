# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The create-time location conflict check knows which keys a single-file location holds.

A single-file location (no trailing ``/``) holds its object, its companion folder ``{key}/`` and its own
previews ``{key}.previewFile.{ext}``. ``keys_conflict`` compared both keys as folders, so a
``bucketExistingKey`` naming the own preview of an existing single-file asset (or a single-file asset
created over an existing asset whose location is one of its previews) was accepted, and two assets then
held the same object. Comparing the two location keys with ``contains`` both ways still missed two
locations whose previews meet: ``p/model.glb`` and ``p/model.glb.previewFile`` both hold
``p/model.glb.previewFile.previewFile.png``. The pairs run in both orders, directly, through the ownership
check the ``bucketExistingKey`` branch runs, and through a whole second create.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load
from tests.handlers.assets.test_createAsset_cross_database_prefix_takeover import (
    _BASE_PREFIX, _OTHER_DB, _asset_record, _assert_nothing_persisted, _request_model, _wire)

CONFLICTS = [
    ("p/model.glb", "p/model.glb.previewFile.png"),
    ("p/model.glb", "p/model.glb"),
    ("p/model.glb", "p/model.glb/"),
    ("p/model.glb", "p/model.glb/sub/x.obj"),
    ("p/", "p/model.glb"),
    ("p/a/", "p/a/b/"),
    ("p/a/", "p/a"),
    ("/p/model.glb", "p/model.glb.previewFile.gif"),
    # Neither location key is held by the other, but both hold p/model.glb.previewFile.previewFile.png
    ("p/model.glb", "p/model.glb.previewFile"),
    ("p/model.glb", "p/model.glb.previewFile."),
]
NO_CONFLICT = [
    ("p/model.glb", "p/model.glb.bak"),
    ("p/model.glb", "p/model.glbx/inner.txt"),
    ("p/model.glb", "p/model.glb.previewFile.png/"),
    ("p/a/", "p/a-other/"),
    ("p/model.glb/", "p/model.glb.previewFile.png"),
    ("p/model.glb", "p/model.glb.previewFile/"),
    ("p/model.glb", "p/model.glb.previewFil"),
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


FILE_KEY = ("The specified bucketExistingKey names a file. It must name a folder (a key ending in /) that "
            "holds the asset's files.")


@pytest.mark.unit
class TestASecondCreateOverMeetingPreviews:
    """An asset at ``a/x`` and one at ``a/x.previewFile`` would both hold ``a/x.previewFile.previewFile.z``
    (a preview of each). A bucketExistingKey must name a folder, so the second single-file asset is never
    created: its file key is refused before the ownership check. A folder over the keys an existing
    single-file asset holds is refused by the conflict check, and a neighbour folder is created."""

    @pytest.mark.parametrize("existing,supplied", [
        ("a/x", "a/x.previewFile"),
        ("a/x.previewFile", "a/x"),
        ("a/x", "a/x.previewFile."),
        ("a/x.previewFile.", "a/x"),
    ])
    def test_the_second_single_file_create_is_refused(self, existing, supplied):
        m = _load(fresh=True)
        _wire(m, owned_records=[_asset_record(_OTHER_DB, "first-asset", _BASE_PREFIX + existing)])

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(_request_model(m, bucket_existing_key=supplied), {"tokens": ["user1"]})

        assert str(caught.value).endswith(FILE_KEY)
        _assert_nothing_persisted(m)

    @pytest.mark.parametrize("existing,supplied", [
        ("a/x", "a/x/"),
        ("a/x.previewFile", "a/x.previewFile/"),
    ])
    def test_a_folder_over_a_single_file_assets_keys_is_refused(self, existing, supplied):
        m = _load(fresh=True)
        _wire(m, owned_records=[_asset_record(_OTHER_DB, "first-asset", _BASE_PREFIX + existing)])

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(_request_model(m, bucket_existing_key=supplied), {"tokens": ["user1"]})

        assert "already in use by another asset" in str(caught.value)
        _assert_nothing_persisted(m)

    @pytest.mark.parametrize("existing,supplied", [
        ("a/x", "a/x.previewFile/"),
        ("a/x.previewFile", "a/x.bak/"),
    ])
    def test_a_neighbour_whose_keys_never_meet_is_created(self, existing, supplied):
        m = _load(fresh=True)
        _wire(m, owned_records=[_asset_record(_OTHER_DB, "first-asset", _BASE_PREFIX + existing)])

        m.create_asset(_request_model(m, bucket_existing_key=supplied), {"tokens": ["user1"]})

        assert m.save_asset_details.call_args.args[0]["assetLocation"]["Key"] == _BASE_PREFIX + supplied
