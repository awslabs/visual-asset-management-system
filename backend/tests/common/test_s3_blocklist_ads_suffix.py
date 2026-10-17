# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A Windows alternate-data-stream suffix on the executable blocklist (issue #423).

common.s3.validateUnallowedFileExtensionAndContentType reads the extension from the last dot of the
key's final non-empty segment, so ``payload.exe::$DATA`` gave ``.exe::$DATA`` and ``payload.exe:evil``
gave ``.exe:evil``, neither of which is on the list. On NTFS the ``::$DATA`` suffix names the default
data stream of ``payload.exe`` -- the file itself -- and ``:stream`` writes a named stream on it, so a
Windows client that writes such a key to disk produces ``payload.exe``.

The helper now splits the segment on ``:`` and checks EVERY piece, each with trailing dots and
whitespace removed before its extension is read: ``payload.exe::$DATA`` and ``a:b.exe`` are both
rejected, because a Windows client materializes whichever piece survives (NTFS keeps the first as the
file and the rest as a stream name; a browser that cannot write ``:`` substitutes it and keeps the
whole name, so ``a:b.exe`` lands as ``a_b.exe``). A ``:`` in an ordinary name (``notes:v2.txt``) is
still accepted here, for an object already stored under such a key; only a key a request CREATES
refuses it (tests/models/test_assetsV3_ads_suffix_write_paths.py).

NEGATIVE CONTROL: the ``TestStreamSuffix`` rejections FAIL when this file is run against 976b6362f
(the #422 tip this branch is stacked on), because that helper reads the extension from the raw
segment; the ``TestEveryPieceIsChecked`` rejections FAIL against 3e6b5d362 (this branch's first
commit), which cut the segment at its first ``:`` and so allowed ``a:b.exe``. ``TestUnchangedBehaviour``
passes on all three; it is the positive control that the rule did not narrow what was accepted before.

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
        # #412's lower-casing applies to each piece of the split name.
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", ["dir/payload.exe::$DATA/", "payload.exe:evil/", "/payload.EXE::$DATA/"])
    def test_a_trailing_slash_then_a_stream_suffix(self, validate, key):
        # #422's trailing-slash rule and the split compose: the last non-empty segment is split.
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", ["payload.exe. ::$DATA", "payload.exe.:evil", "payload.exe :$DATA"])
    def test_trailing_dots_and_whitespace_before_the_colon_are_still_removed(self, validate, key):
        # The strip runs on each piece, so "payload.exe. " resolves to ".exe" as it did without
        # the suffix (#420).
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    def test_a_blocked_first_piece_is_rejected_whatever_follows(self, validate):
        assert validate("payload.exe:a:b", BENIGN_CONTENT_TYPE) is False
        assert validate("payload.exe::$DATA:extra", BENIGN_CONTENT_TYPE) is False

    def test_the_last_suffix_of_a_piece_is_the_one_compared(self, validate):
        # "archive.txt.exe::$DATA" splits to "archive.txt.exe" whose last suffix is ".exe".
        assert validate("archive.txt.exe::$DATA", BENIGN_CONTENT_TYPE) is False

    def test_rejection_logs_the_key_as_given_not_the_piece_name(self, validate, logger):
        validate("payload.exe::$DATA", BENIGN_CONTENT_TYPE)
        assert logger.errors == ["Unallowed file extension detected in asset: payload.exe::$DATA"]


@pytest.mark.unit
class TestEveryPieceIsChecked:
    """Negative control: every rejection in this class is accepted by the helper at 3e6b5d362.

    That commit kept only the text before the first ``:``, so a blocked extension AFTER a colon was
    never read. 976b6362f blocked ``a:b.exe`` (it read ``.exe`` from the raw segment) and this class
    restores that, while ``x:payload.exe:evil`` was allowed by both.
    """

    @pytest.mark.parametrize(
        "key",
        [
            "a:b.exe",
            "report.txt:payload.exe",
            "dir/a:b.exe",
        ],
    )
    def test_a_blocked_extension_after_the_colon_is_rejected(self, validate, key):
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    def test_a_blocked_middle_piece_is_rejected(self, validate):
        # Neither 976b6362f (read ".exe:evil") nor 3e6b5d362 (read "x") examined the middle piece.
        assert validate("x:payload.exe:evil", BENIGN_CONTENT_TYPE) is False

    def test_colon_case_and_trailing_dot_together(self, validate):
        # The #420 strip and the #412 lower-casing apply to each piece: "B.EXE." -> ".exe".
        assert validate("a:B.EXE.", BENIGN_CONTENT_TYPE) is False

    def test_rejection_logs_the_key_as_given_not_the_piece(self, validate, logger):
        validate("a:b.exe", BENIGN_CONTENT_TYPE)
        assert logger.errors == ["Unallowed file extension detected in asset: a:b.exe"]


@pytest.mark.unit
class TestUnchangedBehaviour:
    """Positive control: these pass before and after the split rule."""

    @pytest.mark.parametrize(
        "key", ["notes:v2.txt", "dir/notes:v2.txt", "a:b.glb", "notes.txt:v2", "v1:v2:notes.txt"]
    )
    def test_a_colon_in_an_ordinary_name_is_accepted(self, validate, key):
        # No piece carries a listed extension ("notes", "v2.txt", "b.glb", ...); such an object may
        # already exist in a bucket and the helper is not the place that refuses it.
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

    def test_the_extension_of_each_piece_is_its_last_suffix(self, validate):
        # "archive.exe.txt::$DATA" splits to "archive.exe.txt" (.txt), "" and "$DATA" (no dot).
        assert validate("archive.exe.txt::$DATA", BENIGN_CONTENT_TYPE) is True

    def test_a_colon_inside_the_path_is_not_the_segment(self, validate):
        # Only the final segment is split; "C:" as a folder name has nothing to do with the file.
        assert validate("C:/notes.txt", BENIGN_CONTENT_TYPE) is True
        assert validate("C:/payload.exe", BENIGN_CONTENT_TYPE) is False

    def test_content_type_is_still_checked_first(self, validate, logger):
        assert validate("payload.exe::$DATA", "application/x-msdownload") is False
        assert logger.errors[0].startswith("Unallowed file content type")
