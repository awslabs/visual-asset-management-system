# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A Windows alternate-data-stream suffix on the executable blocklist (issue #423).

common.s3.validateUnallowedFileExtensionAndContentType reads the extension from the last dot of the
key's final non-empty segment, so ``payload.exe::$DATA`` gave ``.exe::$DATA`` and ``payload.exe:evil``
gave ``.exe:evil``, neither of which is on the list. On NTFS the ``::$DATA`` suffix names the default
data stream of ``payload.exe`` -- the file itself -- and ``:stream`` writes a named stream on it, so a
Windows client that writes such a key to disk produces ``payload.exe``.

The helper now cuts the segment at its first ``:`` before the trailing dots and whitespace are
removed and the extension is read. A ``:`` in an ordinary name (``notes:v2.txt``) is still accepted
here, for an object already stored under such a key; only a key a request CREATES refuses it
(tests/models/test_assetsV3_ads_suffix_write_paths.py).

NEGATIVE CONTROL: the ``TestStreamSuffix`` rejections FAIL when this file is run against 976b6362f
(the #422 tip this branch is stacked on), because that helper reads the extension from the raw
segment. ``TestUnchangedBehaviour`` passes on both; it is the positive control that the cut did not
narrow what was accepted before.

Loading follows tests/common/test_s3_blocklist_trailing_dot.py: tests/conftest.py replaces
``common.s3`` with a stand-in and ``common.constants`` with a MagicMock, so the function is loaded
from source into a private namespace and the real lists are read by package path.
"""

import os

import pytest

from backend.backend.common.constants import (
    UNALLOWED_FILE_EXTENSION_LIST,
    UNALLOWED_MIME_LIST,
)

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "common", "s3.py"
)


class _RecordingLogger:
    """Stand-in for the module's safeLogger; the helper only reports through ``error``."""

    def __init__(self):
        self.errors = []

    def error(self, message, *args, **kwargs):
        self.errors.append(message)


def _load_helper(logger):
    """Extract validateUnallowedFileExtensionAndContentType from the real source."""
    with open(_MODULE_PATH, encoding="utf-8") as f:
        source = f.read()

    start = source.index("def validateUnallowedFileExtensionAndContentType(")
    end = source.index("\ndef validateS3AssetExtensionsAndContentType(", start)
    segment = source[start:end]

    namespace = {
        "os": os,
        "logger": logger,
        "UNALLOWED_MIME_LIST": UNALLOWED_MIME_LIST,
        "UNALLOWED_FILE_EXTENSION_LIST": UNALLOWED_FILE_EXTENSION_LIST,
    }
    exec(compile(segment, _MODULE_PATH, "exec"), namespace)
    return namespace["validateUnallowedFileExtensionAndContentType"]


@pytest.fixture
def logger():
    return _RecordingLogger()


@pytest.fixture
def validate(logger):
    return _load_helper(logger)


# A content type that is on no blocklist, so the extension branch is the one deciding.
BENIGN_CONTENT_TYPE = "application/octet-stream"


@pytest.mark.unit
class TestStreamSuffix:
    """Negative control: every rejection in this class is accepted by the helper at 976b6362f."""

    @pytest.mark.parametrize(
        "key",
        [
            "payload.exe::$DATA",
            "payload.exe:evil",
            "payload.exe:",
            "uploads/payload.exe::$DATA",
            "/dir/payload.bat:stream",
        ],
    )
    def test_a_stream_suffix_does_not_hide_the_extension(self, validate, key):
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", ["payload.EXE::$DATA", "Payload.Exe:Evil", "script.BAT::$data"])
    def test_case_and_stream_suffix_together(self, validate, key):
        # #412's lower-casing applies to the name that is left after the cut.
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", ["dir/payload.exe::$DATA/", "payload.exe:evil/", "/payload.EXE::$DATA/"])
    def test_a_trailing_slash_then_a_stream_suffix(self, validate, key):
        # #422's trailing-slash rule and the cut compose: the last non-empty segment is cut.
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", ["payload.exe. ::$DATA", "payload.exe.:evil", "payload.exe :$DATA"])
    def test_trailing_dots_and_whitespace_before_the_colon_are_still_removed(self, validate, key):
        # The strip runs on the cut name, so "payload.exe. " resolves to ".exe" as it did without
        # the suffix (#420).
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    def test_only_the_first_colon_counts(self, validate):
        assert validate("payload.exe:a:b", BENIGN_CONTENT_TYPE) is False
        assert validate("payload.exe::$DATA:extra", BENIGN_CONTENT_TYPE) is False

    def test_the_last_suffix_before_the_colon_is_the_one_compared(self, validate):
        # "archive.txt.exe::$DATA" is cut to "archive.txt.exe" and its last suffix is ".exe".
        assert validate("archive.txt.exe::$DATA", BENIGN_CONTENT_TYPE) is False

    def test_rejection_logs_the_key_as_given_not_the_cut_name(self, validate, logger):
        validate("payload.exe::$DATA", BENIGN_CONTENT_TYPE)
        assert logger.errors == ["Unallowed file extension detected in asset: payload.exe::$DATA"]


@pytest.mark.unit
class TestUnchangedBehaviour:
    """Positive control: these pass before and after the cut."""

    @pytest.mark.parametrize("key", ["notes:v2.txt", "dir/notes:v2.txt", "a:b.glb", "notes.txt:v2"])
    def test_a_colon_in_an_ordinary_name_is_accepted(self, validate, key):
        # The cut leaves "notes" (no extension) or "notes.txt" (.txt, not listed); such an object
        # may already exist in a bucket and the helper is not the place that refuses it.
        assert validate(key, BENIGN_CONTENT_TYPE) is True

    @pytest.mark.parametrize("key", ["readme.txt", ".gitignore", "archive.tar.gz", "model.GLB"])
    def test_allowed_names_are_still_accepted(self, validate, key):
        assert validate(key, BENIGN_CONTENT_TYPE) is True

    @pytest.mark.parametrize("key", ["assets/", "docs.v2/", "dir/sub/", "/"])
    def test_a_folder_name_without_a_blocked_extension_is_allowed(self, validate, key):
        assert validate(key, BENIGN_CONTENT_TYPE) is True

    @pytest.mark.parametrize("key", ["payload.exe", "payload.exe.", "payload.exe/", "..exe", ".EXE"])
    def test_the_earlier_rules_still_reject(self, validate, key):
        # From the #412, #420 and #422 suites: the plain forms are unaffected.
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    def test_the_extension_before_the_colon_is_the_one_compared(self, validate):
        # "archive.exe.txt::$DATA" is cut to "archive.exe.txt" and its last suffix is ".txt".
        assert validate("archive.exe.txt::$DATA", BENIGN_CONTENT_TYPE) is True

    def test_a_colon_inside_the_path_is_not_the_segment(self, validate):
        # Only the final segment is cut; "C:" as a folder name has nothing to do with the file.
        assert validate("C:/notes.txt", BENIGN_CONTENT_TYPE) is True
        assert validate("C:/payload.exe", BENIGN_CONTENT_TYPE) is False

    def test_content_type_is_still_checked_first(self, validate, logger):
        assert validate("payload.exe::$DATA", "application/x-msdownload") is False
        assert logger.errors[0].startswith("Unallowed file content type")
