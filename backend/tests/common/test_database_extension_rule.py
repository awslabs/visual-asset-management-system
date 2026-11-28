# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A database's file extension restriction (restrictFileUploadsToExtensions, issue #371 IF-044).

An entry may hold '_' (.x_t, .x_b) and several parts (.tar.gz). A file is allowed when its name
ends with a listed entry, case-insensitively, and the part of the name before it holds a
character other than a dot (as for os.path.splitext). The upload checks in uploadFile.py and
ingestAsset.py all call common.assetUploads.validate_file_extension_against_database; the web and
CLI pre-checks mirror it (fileExtensionValidation.test.ts, VamsCLI test_file_upload.py).
"""

import uuid

import pytest

from common.assetUploads import validate_file_extension_against_database

REFUSAL = "Database does not allow this file extension"


def _allowed(file_path, restriction):
    is_valid, message = validate_file_extension_against_database(file_path, restriction)
    if is_valid:
        assert message is None
    else:
        assert message.startswith(REFUSAL), message
    return is_valid


@pytest.mark.unit
class TestMultiPartAndUnderscoreExtensions:
    @pytest.mark.parametrize("file_path,restriction", [
        ("/part.x_t", ".x_t,.x_b"),
        ("/PART.X_B", ".x_t,.x_b"),
        ("/archive.tar.gz", ".tar.gz"),
        ("/data/ARCHIVE.TAR.GZ", ".tar.gz"),
        ("/archive.tar.gz", ".TAR.GZ"),
        ("/scan.v2.tar.gz", ".zip, .tar.gz"),
    ])
    def test_a_name_ending_with_a_listed_extension_is_allowed(self, file_path, restriction):
        # On the base the last suffix (.gz, .x_t) was compared alone: a .tar.gz entry never matched
        assert _allowed(file_path, restriction) is True

    @pytest.mark.parametrize("file_path,restriction", [
        ("/archive.gz", ".tar.gz"),
        ("/archive.tgz", ".tar.gz"),
        ("/archivetar.gz", ".tar.gz"),
        ("/part.x_b", ".x_t"),
        ("/part.xt", ".x_t"),
    ])
    def test_a_name_that_only_shares_part_of_an_extension_is_refused(self, file_path, restriction):
        assert _allowed(file_path, restriction) is False

    def test_a_single_part_entry_still_matches_the_last_suffix(self):
        """POSITIVE CONTROL: .gz keeps allowing a .tar.gz file, as it did."""
        assert _allowed("/archive.tar.gz", ".gz") is True


@pytest.mark.unit
class TestNamesWithoutAnExtension:
    @pytest.mark.parametrize("file_path", [
        "/README", "/folder.v2/README", "/.", "/..", "/...", "/file.", "/.pdf", "/..pdf", "/dir/",
    ])
    def test_a_name_without_the_extension_is_refused(self, file_path):
        assert _allowed(file_path, ".pdf,.v2") is False

    @pytest.mark.parametrize("file_path", ["/.hidden.pdf", "/a..pdf", "/dir.d/report.PDF"])
    def test_a_name_with_text_before_the_extension_is_allowed(self, file_path):
        assert _allowed(file_path, ".pdf") is True

    @pytest.mark.parametrize("entry", ["pdf", "..", ".tar."])
    def test_an_entry_that_names_no_extension_matches_nothing(self, entry):
        assert _allowed("/x.pdf", entry) is False
        assert _allowed("/x..", entry) is False
        assert _allowed("/x.tar.", entry) is False


@pytest.mark.unit
class TestNoRestriction:
    @pytest.mark.parametrize("restriction", ["", "   ", None, ".all", ".glb,.ALL"])
    def test_every_name_is_allowed(self, restriction):
        assert _allowed("/README", restriction) is True


def _database(restriction):
    return {"databaseId": "db01", "description": "a valid description",
            "defaultBucketId": str(uuid.uuid4()), "restrictFileUploadsToExtensions": restriction}


@pytest.mark.unit
class TestTheDatabaseModelAcceptsTheEntries:
    @pytest.mark.parametrize("restriction", [".x_t,.x_b", ".X_T", ".tar.gz", ".step,.x_t,.tar.gz"])
    def test_create_and_update_accept_underscores_and_parts(self, restriction):
        import models.databases as md
        # On the base '.x_t' was refused: "Extensions may contain only letters, digits, and dots"
        assert md.CreateDatabaseRequestModel(**_database(restriction)) is not None
        assert md.UpdateDatabaseRequestModel(restrictFileUploadsToExtensions=restriction) is not None

    @pytest.mark.parametrize("restriction", [".glb,.gltf,.obj", ".all", ".e57,.las,.laz,.ply"])
    def test_a_restriction_valid_before_stays_valid(self, restriction):
        """UPGRADE: a value the API accepted before is still accepted."""
        import models.databases as md
        assert md.CreateDatabaseRequestModel(**_database(restriction)) is not None
        assert md.UpdateDatabaseRequestModel(restrictFileUploadsToExtensions=restriction) is not None

    @pytest.mark.parametrize("restriction", [".x-t", ".a b", ".x/t", "x_t", ".pdf,"])
    def test_other_characters_are_still_refused(self, restriction):
        from pydantic import ValidationError
        import models.databases as md
        with pytest.raises(ValidationError):
            md.CreateDatabaseRequestModel(**_database(restriction))
        with pytest.raises(ValidationError):
            md.UpdateDatabaseRequestModel(restrictFileUploadsToExtensions=restriction)
