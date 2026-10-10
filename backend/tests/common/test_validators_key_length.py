# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #407: the path validators bound a key at the Amazon S3 object-key limit, in bytes.

S3 refuses an object key longer than 1024 bytes of UTF-8. The five validators that admit a caller-
supplied file key -- ``RELATIVE_FILE_PATH``, ``RELATIVE_FILE_PATH_ARRAY``, ``DOWNLOAD_KEY_ARRAY``,
``ASSET_PATH`` and ``ASSET_AUXILIARYPREVIEW_PATH`` -- enforced a shape and a minimum length but no
maximum, so an over-long key passed validation, reached S3, and the resulting ``ClientError`` was
reported as a ``500``. Until the WAF change in #400 the edge rule ``SizeRestrictions_URIPATH`` masked
this with a ``403``; on ``release/2.6.5`` the backend is the first point of rejection.

The bound is measured on the UTF-8 encoding, not the character count: a 350-character key of 3-byte
characters is 1050 bytes and S3 refuses it, so a character-count check would let it through.

Every validator also keeps its existing rules (minimum length, ``..`` refusal) -- the regression
class at the end proves the new check did not displace them.
"""

import pytest

from common.validators import (
    MAX_S3_OBJECT_KEY_BYTES,
    validate,
    validate_asset_auxiliarypreview_path,
    validate_asset_path,
    validate_download_key_array,
    validate_relative_file_path,
    validate_relative_file_path_array,
)

LENGTH_MESSAGE = "exceeds the maximum S3 object key length of 1024 bytes"

# A 3-byte UTF-8 character (U+4E2D). 350 of them are 1050 bytes but only 350 characters.
THREE_BYTE_CHAR = "\u4e2d"
assert len(THREE_BYTE_CHAR.encode("utf-8")) == 3


def _ascii_key(prefix, total_bytes, suffix=""):
    """An ASCII key of exactly ``total_bytes`` bytes shaped ``<prefix><filler><suffix>``."""
    filler = total_bytes - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
    assert filler >= 0
    key = prefix + ("a" * filler) + suffix
    assert len(key.encode("utf-8")) == total_bytes
    return key


def _multibyte_key(prefix, char_count, suffix=""):
    """A key of ``char_count`` 3-byte characters between ``prefix`` and ``suffix``."""
    return prefix + (THREE_BYTE_CHAR * char_count) + suffix


@pytest.mark.unit
class TestTheLimitItself:
    def test_the_constant_is_the_s3_object_key_limit(self):
        assert MAX_S3_OBJECT_KEY_BYTES == 1024


@pytest.mark.unit
class TestRelativeFilePath:
    def test_a_key_at_the_limit_passes(self):
        valid, message = validate_relative_file_path("key", _ascii_key("/", 1024))
        assert (valid, message) == (True, "")

    def test_a_key_one_byte_over_fails_with_the_length_message(self):
        valid, message = validate_relative_file_path("key", _ascii_key("/", 1025))
        assert valid is False
        assert message == "key " + LENGTH_MESSAGE

    def test_the_bound_is_bytes_not_characters(self):
        # 1 + 350 * 3 = 1051 bytes, 351 characters: well inside a character count, over the byte limit.
        key = _multibyte_key("/", 350)
        assert len(key) < MAX_S3_OBJECT_KEY_BYTES
        assert len(key.encode("utf-8")) > MAX_S3_OBJECT_KEY_BYTES

        valid, message = validate_relative_file_path("key", key)
        assert valid is False
        assert message == "key " + LENGTH_MESSAGE

    def test_a_multibyte_key_inside_the_byte_limit_passes(self):
        # 1 + 341 * 3 = 1024 bytes exactly.
        key = _multibyte_key("/", 341)
        assert len(key.encode("utf-8")) == MAX_S3_OBJECT_KEY_BYTES
        assert validate_relative_file_path("key", key) == (True, "")


@pytest.mark.unit
class TestRelativeFilePathArray:
    def test_every_element_at_the_limit_passes(self):
        keys = [_ascii_key("/", 1024), "/ok.txt", _ascii_key("/b/", 1024)]
        assert validate_relative_file_path_array("keys", keys) == (True, "")

    def test_one_over_long_element_fails_the_array(self):
        keys = ["/ok.txt", _ascii_key("/", 1025), "/also-ok.txt"]
        valid, message = validate_relative_file_path_array("keys", keys)
        assert valid is False
        assert message == "keys " + LENGTH_MESSAGE

    def test_one_multibyte_over_long_element_fails_the_array(self):
        valid, message = validate_relative_file_path_array("keys", ["/ok.txt", _multibyte_key("/", 350)])
        assert valid is False
        assert message == "keys " + LENGTH_MESSAGE


@pytest.mark.unit
class TestDownloadKeyArray:
    @pytest.mark.parametrize("shape", ["/", "asset1/"], ids=["asset-relative", "asset-prefixed"])
    def test_a_key_at_the_limit_passes_in_both_accepted_shapes(self, shape):
        assert validate_download_key_array("keys", [_ascii_key(shape, 1024)]) == (True, "")

    @pytest.mark.parametrize("shape", ["/", "asset1/"], ids=["asset-relative", "asset-prefixed"])
    def test_a_key_one_byte_over_fails(self, shape):
        valid, message = validate_download_key_array("keys", [_ascii_key(shape, 1025)])
        assert valid is False
        assert message == "keys " + LENGTH_MESSAGE

    def test_the_bound_is_bytes_not_characters(self):
        valid, message = validate_download_key_array("keys", [_multibyte_key("/", 350)])
        assert valid is False
        assert message == "keys " + LENGTH_MESSAGE

    def test_one_over_long_element_fails_the_array(self):
        valid, message = validate_download_key_array("keys", ["/ok.txt", _ascii_key("/", 1025)])
        assert valid is False
        assert message == "keys " + LENGTH_MESSAGE


@pytest.mark.unit
class TestAssetPath:
    def test_a_file_path_at_the_limit_passes(self):
        assert validate_asset_path("path", _ascii_key("asset1/", 1024), False) == (True, "")

    def test_a_file_path_one_byte_over_fails(self):
        valid, message = validate_asset_path("path", _ascii_key("asset1/", 1025), False)
        assert valid is False
        assert message == "path " + LENGTH_MESSAGE

    def test_a_folder_path_at_the_limit_passes(self):
        assert validate_asset_path("path", _ascii_key("asset1/", 1024, suffix="/"), True) == (True, "")

    def test_a_folder_path_one_byte_over_fails(self):
        valid, message = validate_asset_path("path", _ascii_key("asset1/", 1025, suffix="/"), True)
        assert valid is False
        assert message == "path " + LENGTH_MESSAGE

    def test_the_bound_is_bytes_not_characters(self):
        valid, message = validate_asset_path("path", _multibyte_key("asset1/", 350), False)
        assert valid is False
        assert message == "path " + LENGTH_MESSAGE


@pytest.mark.unit
class TestAssetAuxiliaryPreviewPath:
    def test_a_key_at_the_limit_passes(self):
        key = _ascii_key("model.e57/preview/", 1024)
        assert validate_asset_auxiliarypreview_path("path", key) == (True, "")

    def test_a_key_one_byte_over_fails(self):
        valid, message = validate_asset_auxiliarypreview_path("path", _ascii_key("model.e57/preview/", 1025))
        assert valid is False
        assert message == "path " + LENGTH_MESSAGE

    def test_the_bound_is_bytes_not_characters(self):
        valid, message = validate_asset_auxiliarypreview_path("path", _multibyte_key("model.e57/preview/", 350))
        assert valid is False
        assert message == "path " + LENGTH_MESSAGE


@pytest.mark.unit
class TestTheDispatcherCarriesTheBound:
    """The handlers reach these rules through validate(); the bound must hold on that path too."""

    @pytest.mark.parametrize("validator_name,value", [
        ("RELATIVE_FILE_PATH", _ascii_key("/", 1025)),
        ("RELATIVE_FILE_PATH_ARRAY", ["/ok.txt", _ascii_key("/", 1025)]),
        ("DOWNLOAD_KEY_ARRAY", ["/ok.txt", _ascii_key("/", 1025)]),
        ("ASSET_AUXILIARYPREVIEW_PATH", _ascii_key("model.e57/preview/", 1025)),
    ])
    def test_an_over_long_key_is_refused_through_validate(self, validator_name, value):
        valid, message = validate({"field": {"value": value, "validator": validator_name}})
        assert valid is False
        assert message == "field " + LENGTH_MESSAGE

    def test_asset_path_is_refused_through_validate(self):
        valid, message = validate({
            "field": {"value": _ascii_key("asset1/", 1025), "validator": "ASSET_PATH", "isFolder": False}
        })
        assert valid is False
        assert message == "field " + LENGTH_MESSAGE

    def test_the_client_message_does_not_echo_the_key(self):
        """The refusal names the field and the limit, never the (very long) key itself."""
        key = _ascii_key("/secret-", 1025)
        _, message = validate({"assetFilePathKey": {"value": key, "validator": "RELATIVE_FILE_PATH"}})
        assert "secret-" not in message
        assert len(message) < 128


@pytest.mark.unit
class TestExistingRulesStillFire:
    """Regression: the pre-existing minimum-length and traversal rules were not displaced."""

    def test_relative_file_path_minimum_length_still_fires(self):
        valid, message = validate_relative_file_path("key", "/a")
        assert valid is False
        assert "at least 3 characters" in message

    def test_relative_file_path_traversal_still_fires(self):
        valid, message = validate_relative_file_path("key", "/dir/../etc/passwd")
        assert valid is False
        assert "'.' in sequence" in message

    def test_relative_file_path_shape_still_fires(self):
        valid, message = validate_relative_file_path("key", "no-leading-slash.txt")
        assert valid is False
        assert "Must follow the regexp" in message

    def test_download_key_array_traversal_still_fires(self):
        valid, message = validate_download_key_array("keys", ["/dir/../other.txt"])
        assert valid is False
        assert "'..' path segments" in message

    def test_download_key_array_empty_entry_still_fires(self):
        valid, message = validate_download_key_array("keys", ["   "])
        assert valid is False
        assert "non-empty strings" in message

    def test_asset_path_minimum_length_still_fires(self):
        valid, message = validate_asset_path("path", "a/b", False)
        assert valid is False
        assert "at least 4 characters" in message

    def test_asset_path_traversal_still_fires(self):
        valid, message = validate_asset_path("path", "asset1/../other/file.txt", False)
        assert valid is False
        assert "'.' in sequence" in message

    def test_asset_folder_path_double_slash_still_fires(self):
        valid, message = validate_asset_path("path", "asset1//dir/", True)
        assert valid is False
        assert "consecutive forward slashes" in message

    def test_auxiliary_preview_minimum_lengths_still_fire(self):
        valid, message = validate_asset_auxiliarypreview_path("path", "abc/preview/x")
        assert valid is False
        assert "at least 4 characters" in message

        valid, message = validate_asset_auxiliarypreview_path("path", "model.e57/preview/x")
        assert valid is False
        assert "at least 2 characters" in message

    def test_auxiliary_preview_traversal_still_fires(self):
        valid, message = validate_asset_auxiliarypreview_path("path", "model.e57/preview/../x.bin")
        assert valid is False
        assert "'.' in sequence" in message

    @pytest.mark.parametrize("validator,args", [
        (validate_relative_file_path, ("/scans/pump.glb",)),
        (validate_asset_path, ("asset1/scans/pump.glb", False)),
        (validate_asset_path, ("asset1/scans/", True)),
        (validate_asset_auxiliarypreview_path, ("scans/pump.e57/preview/r/octree.bin",)),
    ])
    def test_an_ordinary_key_still_passes(self, validator, args):
        """Paired control: every rejection above is also satisfied by a validator that rejects everything."""
        assert validator("key", *args) == (True, "")

    def test_an_ordinary_key_array_still_passes(self):
        assert validate_relative_file_path_array("keys", ["/scans/pump.glb", "/docs/readme.md"]) == (True, "")
        assert validate_download_key_array("keys", ["/scans/pump.glb", "asset1/docs/readme.md"]) == (True, "")
