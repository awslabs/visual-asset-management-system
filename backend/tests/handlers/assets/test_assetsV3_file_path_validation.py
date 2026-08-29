# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S25-SEC-001 follow-up: the file-path models must reject traversal and control
characters, the way bucketExistingKey does.

`CreateFolderRequestModel.relativeKey` was the one caller-supplied file path that never ran
through `validate_asset_file_path`: it only checked for a trailing slash, so '..' segments,
backslashes and an unbounded length all reached `resolve_asset_file_path`, which concatenates
without a containment guard, and then `s3_client.put_object`.

Parity with `bucketExistingKey` is deliberately partial. `bucket_existing_key_pattern` allows
only `[a-zA-Z0-9._\\-/]`, but asset file paths legitimately carry spaces and unicode (an asset's
files are named by the user), so the charset rule applied to file paths rejects control
characters rather than everything outside ASCII alphanumerics.

Pydantic v1 collects an unrecognized `Field()` kwarg into `field_info.extra` instead of raising,
so a declaration can look like a constraint and validate nothing. These tests assert on parsed
behaviour and on `field_info`, never on the declaration text.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.assetsV3 import (
    MAX_S3_KEY_LENGTH,
    CreateFolderRequestModel,
    DownloadAssetRequestModel,
    UploadFileModel,
)


@pytest.mark.unit
class TestCreateFolderRelativeKey:
    """The folder path is written straight to S3, so it needs the same rules."""

    @pytest.mark.parametrize("relative_key", [
        "../escape/",
        "/../escape/",
        "sub/../../escape/",
        "sub/..%2f/",
    ])
    def test_traversal_segments_are_rejected(self, relative_key):
        with pytest.raises(ValidationError):
            CreateFolderRequestModel(relativeKey=relative_key)

    def test_backslash_is_rejected(self):
        with pytest.raises(ValidationError):
            CreateFolderRequestModel(relativeKey="sub\\escape/")

    @pytest.mark.parametrize("control", ["\x00", "\n", "\r", "\x1f", "\x7f"])
    def test_control_characters_are_rejected(self, control):
        with pytest.raises(ValidationError):
            CreateFolderRequestModel(relativeKey=f"sub{control}dir/")

    def test_length_is_bounded_to_the_s3_key_limit(self):
        assert (CreateFolderRequestModel.__fields__['relativeKey']
                .field_info.max_length == MAX_S3_KEY_LENGTH)
        with pytest.raises(ValidationError):
            CreateFolderRequestModel(relativeKey="a" * (MAX_S3_KEY_LENGTH + 1) + "/")

    def test_missing_trailing_slash_is_still_rejected(self):
        with pytest.raises(ValidationError):
            CreateFolderRequestModel(relativeKey="sub/dir")

    @pytest.mark.parametrize("relative_key", [
        "/sub/dir/",
        "sub/dir/",
        "/sub/my folder/",
        "/sub/dossier-café/",
        "/sub/v1.2/",
    ])
    def test_legitimate_folder_paths_are_accepted(self, relative_key):
        assert CreateFolderRequestModel(relativeKey=relative_key).relativeKey == relative_key

    def test_no_v2_constraint_spelling_was_swallowed(self):
        # A v2 spelling lands in field_info.extra and validates nothing, so the
        # constraint is asserted through field_info rather than the declaration.
        # (strip_whitespace= is the repo's known inert baseline, pinned by
        # tests/models/test_no_dead_field_kwargs.py, and stays out of scope here.)
        field_info = CreateFolderRequestModel.__fields__['relativeKey'].field_info
        assert "pattern" not in (field_info.extra or {})
        assert "max_length" not in (field_info.extra or {})
        assert field_info.max_length is not None


@pytest.mark.unit
class TestSharedFilePathCharsetRule:
    """The control-character rule reaches every field on the shared validator."""

    @pytest.mark.parametrize("model,field", [
        (UploadFileModel, "relativeKey"),
        (DownloadAssetRequestModel, "key"),
    ])
    def test_control_characters_are_rejected(self, model, field):
        payload = {field: "/dir/na\nme.glb"}
        if model is UploadFileModel:
            payload["file_size"] = 5
        if model is DownloadAssetRequestModel:
            payload["downloadType"] = "assetFile"
        with pytest.raises(ValidationError):
            model(**payload)

    def test_spaces_and_unicode_still_pass(self):
        model = UploadFileModel(relativeKey="/dir/mon dossier/café.glb", file_size=5)
        assert model.relativeKey == "/dir/mon dossier/café.glb"
