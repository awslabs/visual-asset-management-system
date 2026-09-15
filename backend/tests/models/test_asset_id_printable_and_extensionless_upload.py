# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Two owner rulings on what a caller may name: printable asset ids, extension-less files.

**Asset id must be printable (owner question 80, option A).** The create-time gate already refused a
non-ASCII id, but `isascii()` does not imply printable: the shared `filename_pattern` admits ``\\s``,
so ``"asset\\tname"`` satisfied both the pattern and the ASCII check. The id becomes an S3 prefix
component and part of a DynamoDB key, where a tab or newline is invisible to whoever later has to
identify the asset.

**An upload file needs a name, not an extension (owner question 83, option B).** The old check
required a ``.`` anywhere in ``relativeKey`` while reporting "Files must have a valid extension", so
the effective rule was "some component of the path contains a dot":

    LICENSE, /LICENSE, Dockerfile        -> rejected
    folder.v2/LICENSE                    -> accepted

Both directions are asserted in each case. A gate that rejects everything satisfies the negative arm
alone, and this one guards asset CREATION — over-rejecting is the more expensive failure, because it
refuses content the rest of the system already accepts through bucket-sync ingestion.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.assetsV3 import (
    InitializeUploadRequestModel,
    validate_ascii_asset_id,
)


@pytest.mark.unit
class TestAssetIdMustBePrintable:
    @pytest.mark.parametrize("bad", ["asset\tname", "asset\nname", "asset\rname", "asset\x0bname"])
    def test_a_control_character_is_refused(self, bad):
        """The gap the ASCII check left open: each of these IS valid ASCII."""
        assert bad.isascii(), "the fixture must be ASCII, or it would fail the earlier check instead"
        with pytest.raises(ValueError) as exc:
            validate_ascii_asset_id(bad)
        assert "printable" in str(exc.value).lower()

    @pytest.mark.parametrize("good", ["asset-name", "asset_name", "Asset.Name-1", "a b c", "x" * 60])
    def test_an_ordinary_id_is_still_accepted(self, good):
        """Paired arm. A gate that refused everything would satisfy the arm above on its own.

        A single space is deliberately included: `filename_pattern` admits it, ordinary asset names
        use it, and `isprintable()` treats it as printable -- so the new check must not narrow it.
        """
        assert validate_ascii_asset_id(good) == good

    def test_a_non_ascii_id_still_names_the_ascii_rule(self):
        """The two checks stay distinguishable, so an operator learns which one they tripped."""
        with pytest.raises(ValueError) as exc:
            validate_ascii_asset_id("asset-é")
        assert "ASCII" in str(exc.value)


def _upload(keys, upload_type="assetFile"):
    return InitializeUploadRequestModel(
        assetId="xtest-asset",
        databaseId="test-db",
        uploadType=upload_type,
        files=[{"relativeKey": k, "file_size": 10} for k in keys],
    )


@pytest.mark.unit
class TestExtensionlessUploadsAreAccepted:
    @pytest.mark.parametrize("key", ["LICENSE", "/LICENSE", "Dockerfile", "Makefile",
                                     "folder/LICENSE", "/nested/dir/NOTICE"])
    def test_a_file_with_no_extension_is_accepted(self, key):
        model = _upload([key])
        assert model.files[0].relativeKey == key

    @pytest.mark.parametrize("key", ["model.glb", "folder.v2/LICENSE", "/a/b/thing.tar.gz"])
    def test_a_file_that_was_already_accepted_still_is(self, key):
        """Regression arm: dropping the check must not disturb what previously passed.

        `folder.v2/LICENSE` is the case that made the old rule incoherent -- it was ACCEPTED while a
        bare `LICENSE` was refused. It has to keep working, so the change is purely a widening.
        """
        model = _upload([key])
        assert model.files[0].relativeKey == key

    def test_an_empty_relative_key_is_still_refused(self):
        """The half of the old check that was a real rule: a file must be named.

        Without this the change would have removed a genuine guard along with the incoherent one -- an
        empty key becomes an S3 object at the asset prefix itself.
        """
        with pytest.raises(ValidationError):
            _upload([""])

    def test_the_other_upload_rules_are_untouched(self):
        """Controls for the neighbouring validations in the same root_validator.

        They share one function with the check that was edited, so an edit that broke them would
        otherwise surface far from here -- as an upload accepting a duplicate key, or an asset-preview
        request carrying two files.
        """
        with pytest.raises(ValidationError):
            _upload(["dup.txt", "dup.txt"])            # duplicate relative keys
        with pytest.raises(ValidationError):
            _upload([], upload_type="assetFile")        # assetFile needs at least one file
        with pytest.raises(ValidationError):
            _upload(["a.png", "b.png"], upload_type="assetPreview")  # preview takes exactly one
        # And the accepted shape for a preview, so the three refusals above are not vacuous.
        assert _upload(["a.png"], upload_type="assetPreview").files[0].relativeKey == "a.png"
