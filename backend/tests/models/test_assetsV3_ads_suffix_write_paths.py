# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A path a request CREATES may not contain ':' (issue #423).

``validate_asset_file_path`` accepted ``/payload.exe::$DATA`` and ``/payload.exe:evil`` on every path,
so a key whose ``:`` suffix a Windows client reads as an NTFS stream name (``payload.exe::$DATA`` is
the default data stream of ``payload.exe``, the file itself) could be created through the ordinary
upload API past the extension blocklist. Windows forbids ``:`` in any path segment, so no legitimate
file name is lost by refusing it on the created-key paths; the ``new_object`` rule now refuses a ``:``
anywhere in the value for the six models whose path names a key the request will create:

    UploadFileModel.relativeKey              upload initialize
    UploadFileCompletionModel.relativeKey    complete upload
    ExternalFileModel.relativeKey            complete external upload
    CreateFolderRequestModel.relativeKey     create folder
    MoveFileRequestModel.destinationPath     move / rename
    CopyFileRequestModel.destinationPath     copy

The READ paths -- the download ``key``, file info, archive, the source of a move or copy, the external
completion's ``tempKey`` -- keep accepting a key with ``:``, because an object stored under it may
already exist in a bucket and has to stay reachable; common/s3.py
validateUnallowedFileExtensionAndContentType covers the executable case on read by cutting the name
at the first ``:`` before it reads the extension.

NEGATIVE CONTROL: ``TestWritePathsRefuseColon`` FAILS on 976b6362f (the #422 tip this branch is
stacked on): every case there is accepted by every model at the base. The other classes pass on both
and are the positive control.
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

# Every key here passes the pre-existing rules (no '..', no backslash, no control character, no
# trailing dot, whitespace or slash) and is refused only by the new check.
REFUSED_FILE_KEYS = [
    "/payload.exe::$DATA",
    "/payload.exe:evil",
    "/dir/payload.exe::$DATA",
    "/a:b.txt",
    "notes:v2.txt",
    "/dir:sub/notes.txt",
    "assetId/dir/notes.txt:stream",
]

# The folder form: the colon sits before the trailing slash the folder rule requires.
REFUSED_FOLDER_KEYS = [
    "/payload.exe::$DATA/",
    "/a:b/",
    "/dir:sub/dir/",
]

ACCEPTED_FILE_KEYS = [
    "/payload.exe",  # the blocklist helper refuses it later; the path shape itself is fine
    "/notes.txt",
    "/dir/.gitignore",
    "/dir/mon dossier/café.glb",
    "/dir/v1.2/model.glb",
    "/dir/2026-10-10T22-30Z.log",  # a colon-free timestamp, the shape clients fall back to
    "LICENSE",
]


def _upload_completion(key):
    return UploadFileCompletionModel(relativeKey=key, uploadIdS3="upload-1",
                                     parts=[{"PartNumber": 1, "ETag": "etag-1"}])


@pytest.mark.unit
class TestWritePathsRefuseColon:
    """Negative control: every case here is accepted by the models at 976b6362f."""

    @pytest.mark.parametrize("key", REFUSED_FILE_KEYS)
    def test_the_validator_refuses_a_new_object_key(self, key):
        with pytest.raises(ValueError) as exc:
            validate_asset_file_path("relativeKey", key, new_object=True)
        # Generic message, the key is not echoed (root CLAUDE.md Rule 11).
        assert str(exc.value) == "relativeKey cannot contain a colon"
        assert key not in str(exc.value)

    @pytest.mark.parametrize("key", REFUSED_FILE_KEYS)
    def test_upload_initialize_relative_key(self, key):
        with pytest.raises(ValidationError):
            UploadFileModel(relativeKey=key, file_size=5)

    @pytest.mark.parametrize("key", ["/payload.exe::$DATA", "/a:b.txt"])
    def test_upload_completion_relative_key(self, key):
        # complete_upload derives the final S3 key from this value and can create a zero-byte object
        # from it, so a key refused at initialize is refused at completion too.
        with pytest.raises(ValidationError):
            _upload_completion(key)

    @pytest.mark.parametrize("key", ["/payload.exe::$DATA", "/a:b.txt"])
    def test_external_completion_relative_key(self, key):
        with pytest.raises(ValidationError):
            ExternalFileModel(relativeKey=key, tempKey="temp-uploads/x/payload.exe")

    @pytest.mark.parametrize("key", REFUSED_FOLDER_KEYS)
    def test_create_folder_relative_key(self, key):
        # Unlike the trailing-dot rule, the colon rule looks at the whole value, so a folder whose
        # name holds a colon is refused: Windows cannot materialize that directory either.
        with pytest.raises(ValidationError):
            CreateFolderRequestModel(relativeKey=key)

    @pytest.mark.parametrize("key", ["/payload.exe::$DATA", "/a:b.txt"])
    def test_move_destination(self, key):
        with pytest.raises(ValidationError):
            MoveFileRequestModel(sourcePath="/model.glb", destinationPath=key)

    @pytest.mark.parametrize("key", ["/payload.exe::$DATA", "/a:b.txt"])
    def test_copy_destination(self, key):
        with pytest.raises(ValidationError):
            CopyFileRequestModel(sourcePath="/model.glb", destinationPath=key)

    def test_the_colon_rule_is_the_created_key_rule_not_the_file_rule(self):
        # file_only alone (the slash rule) does not refuse a colon; new_object does.
        assert validate_asset_file_path("relativeKey", "/a:b.txt", file_only=True) == "/a:b.txt"
        with pytest.raises(ValueError):
            validate_asset_file_path("relativeKey", "/a:b.txt", new_object=True, file_only=True)


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

    @pytest.mark.parametrize("key", ["/sub/dir/", "/dir./", "/my folder /"])
    def test_create_folder_accepts_ordinary_names(self, key):
        assert CreateFolderRequestModel(relativeKey=key).relativeKey == key

    def test_move_and_copy_destinations_accept_ordinary_names(self):
        assert MoveFileRequestModel(sourcePath="/a.glb", destinationPath="/b.glb").destinationPath == "/b.glb"
        assert CopyFileRequestModel(sourcePath="/a.glb", destinationPath="/dir/b.glb").destinationPath == "/dir/b.glb"

    def test_external_completion_temp_key_keeps_the_read_rules(self):
        # tempKey is an object that already exists; only relativeKey is the created key.
        model = ExternalFileModel(relativeKey="/model.glb", tempKey="temp-uploads/x/model:v2.glb")
        assert model.tempKey == "temp-uploads/x/model:v2.glb"


@pytest.mark.unit
class TestReadPathsAreNotTightened:
    """An object stored under a key with ':' must stay downloadable, inspectable and movable."""

    @pytest.mark.parametrize("key", ["/notes:v2.txt", "/payload.txt::$DATA", "/dir:sub/notes.txt"])
    def test_the_validator_default_accepts_the_key(self, key):
        assert validate_asset_file_path("key", key) == key

    @pytest.mark.parametrize("key", ["/notes:v2.txt", "/payload.txt::$DATA"])
    def test_single_key_download(self, key):
        assert DownloadAssetRequestModel(downloadType="assetFile", key=key).key == key

    @pytest.mark.parametrize("key", ["/notes:v2.txt", "/payload.txt::$DATA"])
    def test_file_info_and_archive(self, key):
        assert FileInfoRequestModel(filePath=key).filePath == key
        assert ArchiveFileRequestModel(filePath=key).filePath == key

    @pytest.mark.parametrize("key", ["/notes:v2.txt", "/payload.txt::$DATA"])
    def test_move_and_copy_source(self, key):
        # Renaming an existing colon-named object to a Windows-safe name is exactly what should work.
        assert MoveFileRequestModel(sourcePath=key, destinationPath="/notes-v2.txt").sourcePath == key
        assert CopyFileRequestModel(sourcePath=key, destinationPath="/notes-v2.txt").sourcePath == key

    def test_the_pre_existing_rejections_still_apply_on_both_paths(self):
        for new_object in (False, True):
            with pytest.raises(ValueError):
                validate_asset_file_path("key", "/dir/../x.txt", new_object=new_object)
            with pytest.raises(ValueError):
                validate_asset_file_path("key", "dir\\x.txt", new_object=new_object)
            with pytest.raises(ValueError):
                validate_asset_file_path("key", "/dir/x\n.txt", new_object=new_object)
