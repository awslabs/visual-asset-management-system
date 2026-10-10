# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A path a request CREATES may not end in a dot or whitespace, and a file path may not end in '/'
(issue #420).

``validate_asset_file_path`` accepted ``/payload.exe.``, ``/payload.exe `` and ``/payload.exe/`` on
every path, so a key whose basename Windows rewrites on download (``payload.exe.`` lands as
``payload.exe``) or whose trailing slash the CLI's ``pathlib`` path building drops (``payload.exe/``
lands as ``payload.exe``) could be created through the ordinary upload API. The validator now takes
``new_object`` and ``file_only``; the models whose path names a key the request will create pass the
first and refuse a final segment ending in a dot or whitespace, and the three whose path names a FILE
pass the second as well and refuse a trailing slash:

    UploadFileModel.relativeKey              upload initialize              new_object, file_only
    UploadFileCompletionModel.relativeKey    complete upload                new_object, file_only
    ExternalFileModel.relativeKey            complete external upload       new_object, file_only
    CreateFolderRequestModel.relativeKey     create folder (its own rule REQUIRES the slash)   new_object
    MoveFileRequestModel.destinationPath     move / rename                  new_object
    CopyFileRequestModel.destinationPath     copy                           new_object

Three things are deliberately NOT refused and are pinned here. A dot-only basename (``/.gitignore``)
is a legitimate file name -- common/s3.py validateUnallowedFileExtensionAndContentType reads the
extension from its last dot, so ``.exe`` is still blocked there. A trailing dot inside the path
(``/dir./file.txt``) is not the final segment. And the READ paths -- the download ``key``, file info,
archive, the source of a move or copy, the external completion's ``tempKey`` -- keep accepting such a
key, because an object stored under it may already exist in a bucket and has to stay reachable; the
blocklist helper covers the executable case on read.

NEGATIVE CONTROL: ``TestWritePathsRefuseTrailingDotOrWhitespace`` FAILS on 8c42a676f (the #412 base
this branch is stacked on): the model-level cases there are accepted by every model at the base.
``TestFilePathsRefuseTrailingSlash`` FAILS on 8c42a676f and on 7d57156f0 (the first #420 commit) the
same way. The model-level cases of the other classes pass on both and are the positive control. The
cases that call ``validate_asset_file_path`` with ``new_object=`` or ``file_only=`` directly cannot run
at the base at all (the parameter does not exist there, TypeError), so they are not part of either
control.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.assetsV3 import (
    ArchiveFileRequestModel,
    CopyFileRequestModel,
    CreateFolderRequestModel,
    DownloadAssetRequestModel,
    ExternalFileModel,
    FileInfoRequestModel,
    MoveFileRequestModel,
    UploadFileCompletionModel,
    UploadFileModel,
    validate_asset_file_path,
)

NBSP = "\u00a0"

# Every key here passes the pre-existing rules (no '..', no backslash, no control character -- a tab
# is one, so it is refused by that rule, not this one) and is refused only by the new check.
REFUSED_FILE_KEYS = [
    "/payload.exe.",
    "/payload.exe ",
    f"/payload.exe{NBSP}",
    "/payload.exe. . ",
    "/dir/notes.txt.",
    "notes.txt ",
    "assetId/dir/notes.txt.",
]

ACCEPTED_FILE_KEYS = [
    "/payload.exe",  # the blocklist helper refuses it later; the path shape itself is fine
    "/.gitignore",
    "/dir/.env.example",
    "/dir./file.txt",
    "/dir /file.txt",
    "/dir/mon dossier/café.glb",
    "/dir/v1.2/model.glb",
    "LICENSE",
]

# A file key that ends in '/' passes every pre-existing rule and the trailing-dot rule; only the
# file_only rule refuses it. Stored as given, it is a folder marker whose name the CLI's pathlib path
# building drops the slash from, so "/payload.exe/" would land as "payload.exe".
REFUSED_SLASH_KEYS = [
    "/payload.exe/",
    "/dir/payload.exe/",
    "/notes.txt/",
    "assetId/dir/",
]


def _upload_completion(key):
    return UploadFileCompletionModel(relativeKey=key, uploadIdS3="upload-1",
                                     parts=[{"PartNumber": 1, "ETag": "etag-1"}])


@pytest.mark.unit
class TestWritePathsRefuseTrailingDotOrWhitespace:
    """Negative control: every case here is accepted by the models at 8c42a676f."""

    @pytest.mark.parametrize("key", REFUSED_FILE_KEYS)
    def test_the_validator_refuses_a_new_object_key(self, key):
        with pytest.raises(ValueError) as exc:
            validate_asset_file_path("relativeKey", key, new_object=True)
        # Generic message, the key is not echoed (root CLAUDE.md Rule 11).
        assert str(exc.value) == "relativeKey cannot end with a dot or whitespace"
        assert key not in str(exc.value)

    @pytest.mark.parametrize("key", REFUSED_FILE_KEYS)
    def test_upload_initialize_relative_key(self, key):
        with pytest.raises(ValidationError):
            UploadFileModel(relativeKey=key, file_size=5)

    @pytest.mark.parametrize("key", ["/payload.exe.", "/payload.exe "])
    def test_upload_completion_relative_key(self, key):
        # complete_upload derives the final S3 key from this value and can create a zero-byte object
        # from it, so a key refused at initialize is refused at completion too.
        with pytest.raises(ValidationError):
            _upload_completion(key)

    @pytest.mark.parametrize("key", ["/payload.exe.", "/payload.exe "])
    def test_external_completion_relative_key(self, key):
        with pytest.raises(ValidationError):
            ExternalFileModel(relativeKey=key, tempKey="temp-uploads/x/payload.exe")

    @pytest.mark.parametrize("key", ["/payload.exe.", "/payload.exe "])
    def test_move_destination(self, key):
        with pytest.raises(ValidationError):
            MoveFileRequestModel(sourcePath="/model.glb", destinationPath=key)

    @pytest.mark.parametrize("key", ["/payload.exe.", "/payload.exe "])
    def test_copy_destination(self, key):
        with pytest.raises(ValidationError):
            CopyFileRequestModel(sourcePath="/model.glb", destinationPath=key)


@pytest.mark.unit
class TestFilePathsRefuseTrailingSlash:
    """Negative control: every model-level case here is accepted at 8c42a676f and at 7d57156f0."""

    @pytest.mark.parametrize("key", REFUSED_SLASH_KEYS)
    def test_the_validator_refuses_a_file_key_ending_in_a_slash(self, key):
        with pytest.raises(ValueError) as exc:
            validate_asset_file_path("relativeKey", key, new_object=True, file_only=True)
        # Generic message, the key is not echoed (root CLAUDE.md Rule 11).
        assert str(exc.value) == "relativeKey cannot end with a slash"
        assert key not in str(exc.value)

    @pytest.mark.parametrize("key", REFUSED_SLASH_KEYS)
    def test_upload_initialize_relative_key(self, key):
        with pytest.raises(ValidationError):
            UploadFileModel(relativeKey=key, file_size=5)

    @pytest.mark.parametrize("key", REFUSED_SLASH_KEYS)
    def test_upload_completion_relative_key(self, key):
        with pytest.raises(ValidationError):
            _upload_completion(key)

    @pytest.mark.parametrize("key", REFUSED_SLASH_KEYS)
    def test_external_completion_relative_key(self, key):
        with pytest.raises(ValidationError):
            ExternalFileModel(relativeKey=key, tempKey="temp-uploads/x/payload.exe")

    def test_file_only_is_independent_of_new_object(self):
        # The slash rule is the file rule, the dot rule is the created-key rule; each fires alone.
        with pytest.raises(ValueError):
            validate_asset_file_path("relativeKey", "/payload.exe/", file_only=True)
        assert validate_asset_file_path("relativeKey", "/payload.exe.", file_only=True) == "/payload.exe."
        assert validate_asset_file_path("relativeKey", "/payload.exe/", new_object=True) == "/payload.exe/"


@pytest.mark.unit
class TestWritePathsStillAcceptLegitimateNames:
    """Positive control: a gate that refuses everything would satisfy the class above on its own."""

    @pytest.mark.parametrize("key", ACCEPTED_FILE_KEYS)
    def test_the_validator_accepts_a_new_object_key(self, key):
        assert validate_asset_file_path("relativeKey", key, new_object=True) == key
        assert validate_asset_file_path("relativeKey", key, new_object=True, file_only=True) == key

    @pytest.mark.parametrize("key", ACCEPTED_FILE_KEYS)
    def test_upload_initialize_relative_key(self, key):
        assert UploadFileModel(relativeKey=key, file_size=5).relativeKey == key

    def test_a_dot_only_basename_is_a_file_name_not_a_trailing_dot(self):
        # ".gitignore" ends in 'e'; the rule is about the LAST character of the final segment.
        assert UploadFileModel(relativeKey="/.gitignore", file_size=5).relativeKey == "/.gitignore"
        assert _upload_completion("/.gitignore").relativeKey == "/.gitignore"

    def test_a_trailing_dot_in_a_directory_segment_is_not_the_final_segment(self):
        assert UploadFileModel(relativeKey="/dir./file.txt", file_size=5).relativeKey == "/dir./file.txt"
        assert MoveFileRequestModel(sourcePath="/a.glb", destinationPath="/dir./a.glb").destinationPath == "/dir./a.glb"

    @pytest.mark.parametrize("key", ["/sub/dir/", "/dir./", "/my folder /"])
    def test_create_folder_is_unaffected(self, key):
        # The folder key ends in '/', so its final segment is empty and the rule never fires; a folder
        # named 'dir.' stays creatable, consistent with '/dir./file.txt' being an accepted file path.
        assert CreateFolderRequestModel(relativeKey=key).relativeKey == key

    def test_move_and_copy_destinations_accept_ordinary_names(self):
        assert MoveFileRequestModel(sourcePath="/a.glb", destinationPath="/b.glb").destinationPath == "/b.glb"
        assert CopyFileRequestModel(sourcePath="/a.glb", destinationPath="/dir/b.glb").destinationPath == "/dir/b.glb"

    def test_external_completion_temp_key_keeps_the_read_rules(self):
        # tempKey is an object that already exists; only relativeKey is the created key.
        model = ExternalFileModel(relativeKey="/model.glb", tempKey="temp-uploads/x/model.glb ")
        assert model.tempKey == "temp-uploads/x/model.glb "


@pytest.mark.unit
class TestReadPathsAreNotTightened:
    """An object stored under such a key must stay downloadable, inspectable and movable."""

    @pytest.mark.parametrize("key", ["/payload.txt.", "/payload.txt ", "/dir/.exe"])
    def test_the_validator_default_accepts_the_key(self, key):
        assert validate_asset_file_path("key", key) == key

    @pytest.mark.parametrize("key", ["/payload.txt.", "/payload.txt "])
    def test_single_key_download(self, key):
        assert DownloadAssetRequestModel(downloadType="assetFile", key=key).key == key

    @pytest.mark.parametrize("key", ["/payload.txt.", "/payload.txt "])
    def test_file_info_and_archive(self, key):
        assert FileInfoRequestModel(filePath=key).filePath == key
        assert ArchiveFileRequestModel(filePath=key).filePath == key

    @pytest.mark.parametrize("key", ["/payload.txt.", "/payload.txt "])
    def test_move_and_copy_source(self, key):
        # Renaming an existing badly-named object to a good name is exactly what should work.
        assert MoveFileRequestModel(sourcePath=key, destinationPath="/payload.txt").sourcePath == key
        assert CopyFileRequestModel(sourcePath=key, destinationPath="/payload.txt").sourcePath == key

    def test_the_pre_existing_rejections_still_apply_on_both_paths(self):
        for new_object in (False, True):
            with pytest.raises(ValueError):
                validate_asset_file_path("key", "/dir/../x.txt", new_object=new_object)
            with pytest.raises(ValueError):
                validate_asset_file_path("key", "dir\\x.txt", new_object=new_object)
            with pytest.raises(ValueError):
                validate_asset_file_path("key", "/dir/x\n.txt", new_object=new_object)
