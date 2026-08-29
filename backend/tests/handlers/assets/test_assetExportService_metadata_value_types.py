# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The exported bundle must report each metadata row's OWN value type, never a default.

Three sites in `process_asset_batch` built their exported block with a hardcoded
`'valueType': 'string'` — asset metadata, file metadata, and file attributes. Beside a value that had
been made nullable for legacy rows, that produced the one shape nothing downstream can detect: a row
of unknown type exported as an assertion that its type is `string`. The asset-link path never did
this (it emitted the stored type), so the bundle disagreed with itself depending on which block a
consumer read, and both disagreed with the metadata GET, which reports an absent type as null.

Why the getter-level tests next door are not enough: they cover what
`get_asset_metadata`/`get_file_metadata` RETURN. The defect lived in the transformation between that
return and the bundle, so a test of the getters passes with the mislabel fully intact. These tests
assert the bundle the caller actually receives. `get_file_attributes` had no coverage at either level.

The distinguishing assertion throughout is `valueType is None` — not merely `!= 'string'`. A fix that
swapped the default for a different literal would satisfy the weaker form.
"""

import pytest
from unittest.mock import MagicMock, patch

# Reuse the loader from the fail-closed suite: assetExportService cannot be imported normally
# because the root conftest registers a mock `handlers` package that shadows the real one.
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
    _DB,
    _ASSET,
)

LEGACY_KEY = "legacyKey"
TYPED_KEY = "typedKey"
FILE_PATH = "/folder/file.txt"


def _stored(value, value_type):
    """The shape the metadata getters now return: value and the row's own type, side by side."""
    return {"value": value, "valueType": value_type}


def _one_legacy_one_typed():
    """A row whose type was never recorded, and one that carries a non-default type.

    The typed row is deliberately NOT `string`: with a hardcoded default, a `string` row would be
    indistinguishable from the mislabel and the assertion could not tell them apart.
    """
    return {
        LEGACY_KEY: _stored("stored text", None),
        TYPED_KEY: _stored("42", "number"),
    }


def _file_row():
    """A listed S3 file as `list_s3_files` returns it.

    Every field the bundle SUBSCRIPTS is present. The bundle reads some fields with `.get` and others
    with `[...]`, and a missing subscripted one is swallowed by the per-asset `except` and surfaces
    only as an asset silently absent from the result — which is why `_run_batch` captures the log.
    """
    return {
        "relativePath": FILE_PATH,
        "key": f"prefix{FILE_PATH}",
        "versionId": "v-1",
        "isFolder": False,
        "isArchived": False,
        "fileName": "file.txt",
        "dateCreatedCurrentVersion": "2026-01-01T00:00:00Z",
        "storageClass": "STANDARD",
        "size": 12,
    }


def _request_model(m, **overrides):
    fields = {
        "includeAssetMetadata": True,
        "includeFileMetadata": True,
        "includeAssetLinkMetadata": False,
        "includeFolderFiles": False,
        "includeOnlyPrimaryTypeFiles": False,
        "fetchAssetRelationships": False,
        "fetchEntireChildrenSubtrees": False,
        "includeParentRelationships": False,
        "includeArchivedFiles": False,
        "generatePresignedUrls": False,
        "fileExtensions": None,
        "maxAssets": 100,
        "startingToken": None,
    }
    fields.update(overrides)
    model = MagicMock()
    for name, value in fields.items():
        setattr(model, name, value)
    return model


def _run_batch(*, asset_metadata=None, file_metadata=None, file_attributes=None, files=None):
    """Drive process_asset_batch for one authorized asset and return its exported entry."""
    m = _load_asset_export_service()

    asset = {
        "assetId": _ASSET,
        "databaseId": _DB,
        "assetName": "An Asset",
        "bucketId": "bucket-1",
        "currentVersionId": "1",
        "assetLocation": {"Key": "prefix/"},
    }

    enforcer_instance = MagicMock()
    enforcer_instance.enforce.return_value = True

    file_rows = [_file_row()] if files is None else files

    # `_process_single_asset` runs in a thread pool and reports a failure to the logger rather than
    # propagating it, so an unstubbed collaborator otherwise surfaces only as an empty result list.
    # Capturing the log makes the assertion below name the actual cause.
    log = MagicMock()

    patches = [
        patch.object(m, "logger", log),
        patch.object(m, "batch_get_assets",
                     MagicMock(return_value={f"{_DB}:{_ASSET}": asset})),
        patch.object(m, "CasbinEnforcer", MagicMock(return_value=enforcer_instance)),
        patch.object(m, "get_default_bucket_details", MagicMock(return_value={
            "bucketName": "bucket-name", "baseAssetsPrefix": "prefix/"})),
        patch.object(m, "get_asset_version_info", MagicMock(return_value=None)),
        patch.object(m, "get_asset_metadata",
                     MagicMock(return_value=asset_metadata or {})),
        patch.object(m, "list_s3_files", MagicMock(return_value=file_rows)),
        patch.object(m, "apply_file_filters", MagicMock(side_effect=lambda f, _r: f)),
        patch.object(m, "get_asset_file_versions", MagicMock(return_value=None)),
        patch.object(m, "get_file_metadata",
                     MagicMock(return_value=file_metadata or {})),
        patch.object(m, "get_file_attributes",
                     MagicMock(return_value=file_attributes or {})),
    ]

    for p in patches:
        p.start()
    try:
        result = m.process_asset_batch(
            [{"databaseId": _DB, "assetId": _ASSET, "isRoot": True}],
            _request_model(m),
            {"tokens": ["user@example.com"], "roles": [], "mfaEnabled": False},
        )
    finally:
        for p in reversed(patches):
            p.stop()

    def _log_text():
        lines = []
        for level in ("warning", "error", "exception"):
            for call in getattr(log, level).call_args_list:
                lines.append(f"{level}: {str(call)[:500]}")
        return "\n".join(lines) or "(the run logged nothing)"

    assert len(result) == 1, (
        f"expected one exported asset entry, got {result}\n{_log_text()}")
    entry = result[0]
    assert "unauthorizedAsset" not in entry, (
        f"the asset was not authorized, so no metadata block was built: {entry}")
    return entry


@pytest.mark.unit
class TestAssetMetadataValueTypeInTheBundle:
    def test_a_row_with_no_recorded_type_is_exported_with_a_null_type(self):
        entry = _run_batch(asset_metadata=_one_legacy_one_typed())
        block = entry["metadata"]

        assert LEGACY_KEY in block, f"the legacy row is missing from the bundle: {block}"
        assert block[LEGACY_KEY]["valueType"] is None, (
            "exporting a row of unrecorded type as 'string' asserts a type the row never had, and "
            "no consumer of the bundle can tell that claim from a real one"
        )

    def test_the_value_still_survives_alongside_the_null_type(self):
        """The two are independent: the null type must not take the value with it."""
        entry = _run_batch(asset_metadata=_one_legacy_one_typed())
        assert entry["metadata"][LEGACY_KEY]["value"] == "stored text"

    def test_a_recorded_type_is_carried_through_rather_than_flattened(self):
        """The positive control: a real type must reach the bundle unchanged.

        Without this, returning null for everything would satisfy the assertion above.
        """
        entry = _run_batch(asset_metadata=_one_legacy_one_typed())
        assert entry["metadata"][TYPED_KEY]["valueType"] == "number"
        assert entry["metadata"][TYPED_KEY]["value"] == "42"

    def test_a_hidden_key_is_still_excluded(self):
        """Control on the surrounding filter, which the changed loop also has to preserve."""
        m = _load_asset_export_service()
        hidden = f"{m.HIDDEN_FIELD_PREFIX}internal"
        entry = _run_batch(asset_metadata={
            hidden: _stored("secret", "string"),
            TYPED_KEY: _stored("42", "number"),
        })
        assert hidden not in entry["metadata"]
        assert TYPED_KEY in entry["metadata"]


@pytest.mark.unit
class TestFileMetadataAndAttributeValueTypesInTheBundle:
    """The two sibling sites, both of which carried the same hardcoded type."""

    def _file_entry(self, **kwargs):
        entry = _run_batch(**kwargs)
        files = entry.get("files") or []
        assert len(files) == 1, f"expected one exported file, got {files}"
        return files[0]

    def test_file_metadata_row_with_no_recorded_type_is_null(self):
        exported = self._file_entry(file_metadata=_one_legacy_one_typed())
        block = exported["metadata"]
        assert LEGACY_KEY in block, block
        assert block[LEGACY_KEY]["valueType"] is None
        assert block[LEGACY_KEY]["value"] == "stored text"

    def test_file_metadata_recorded_type_is_carried_through(self):
        exported = self._file_entry(file_metadata=_one_legacy_one_typed())
        assert exported["metadata"][TYPED_KEY]["valueType"] == "number"

    def test_file_attribute_with_no_recorded_type_is_null(self):
        """`get_file_attributes` had no coverage at all, at either level."""
        exported = self._file_entry(file_attributes=_one_legacy_one_typed())
        block = exported["attributes"]
        assert LEGACY_KEY in block, block
        assert block[LEGACY_KEY]["valueType"] is None
        assert block[LEGACY_KEY]["value"] == "stored text"

    def test_file_attribute_recorded_type_is_carried_through(self):
        exported = self._file_entry(file_attributes=_one_legacy_one_typed())
        assert exported["attributes"][TYPED_KEY]["valueType"] == "number"

    def test_no_site_emits_the_literal_string_default_anywhere(self):
        """The whole-bundle sweep: none of the three blocks may invent a type.

        Stated over the bundle rather than per block so a fourth block added later, built by copying
        one of these loops, is covered without editing this file.
        """
        exported_asset = _run_batch(
            asset_metadata=_one_legacy_one_typed(),
            file_metadata=_one_legacy_one_typed(),
            file_attributes=_one_legacy_one_typed(),
        )
        blocks = [exported_asset["metadata"]]
        for exported_file in exported_asset.get("files") or []:
            blocks.append(exported_file.get("metadata") or {})
            blocks.append(exported_file.get("attributes") or {})

        # Positive control: the sweep must actually be looking at populated blocks.
        assert sum(len(b) for b in blocks) >= 6, (
            f"the sweep found nothing to check, so it proves nothing: {blocks}")

        for block in blocks:
            assert block.get(LEGACY_KEY, {}).get("valueType") is None, block
