# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Trailing-dot, trailing-whitespace, dot-only, leading-dot and trailing-slash basenames on the executable
blocklist (issue #420).

common.s3.validateUnallowedFileExtensionAndContentType is the one executable blocklist behind every
upload, download, stream and workflow-output path. It used to read the extension straight from
``os.path.splitext(keyPath)[1]``, which is a literal suffix split: ``payload.exe.`` gave ``.``,
``payload.exe `` gave ``.exe ``, ``.exe`` and ``..exe`` gave nothing and ``payload.exe/`` has an empty
basename, so none of them was ever compared with the list. Windows drops a trailing dot or space when a
non-browser client writes the file to disk -- ``payload.exe.`` lands as ``payload.exe`` -- a file named
``.exe`` or ``..exe`` is already executable there, and the CLI builds the local path with ``pathlib``,
which drops a trailing slash.

The helper now takes the extension from the last dot of the key's final non-empty segment, after every
trailing dot and whitespace character has been removed (``str.isspace``, so U+00A0 and U+3000 count).
``.gitignore`` is therefore inspected as ``.gitignore`` and stays allowed, ``archive.tar.gz`` as ``.gz``,
and a folder whose own name ends in a blocked extension (``builds.exe/``) is refused.

NEGATIVE CONTROL: the ``TestNormalizedBasename`` rejections FAIL when this file is run against
8c42a676f (the #412 base this branch is stacked on), because that helper still splits the raw key; the
leading-dot and trailing-slash cases also FAIL against 7d57156f0, the first #420 commit.
``TestUnchangedBehaviour`` passes on both; it is the positive control that the normalization did not
narrow what was accepted before.

Loading follows tests/common/test_s3_blocklist_case_insensitive.py: tests/conftest.py replaces
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

NBSP = "\u00a0"
IDEOGRAPHIC_SPACE = "\u3000"


@pytest.mark.unit
class TestNormalizedBasename:
    """Negative control: every rejection in this class is accepted by the helper at 8c42a676f."""

    @pytest.mark.parametrize(
        "key",
        [
            "payload.exe.",
            "payload.exe ",
            "payload.exe\t",
            f"payload.exe{NBSP}",
            f"payload.exe{IDEOGRAPHIC_SPACE}",
            "payload.exe..  ",
            "payload.exe. . ",
            "uploads/payload.exe.",
        ],
    )
    def test_trailing_dots_and_whitespace_do_not_hide_the_extension(self, validate, key):
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", [".exe", "dir/.exe", "/dir/.EXE", ".bat", "uploads/.dll"])
    def test_a_dot_only_basename_is_its_own_extension(self, validate, key):
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", ["..exe", "...bat", "..exe.", "..EXE", "dir/..exe"])
    def test_leading_dots_do_not_hide_the_extension(self, validate, key):
        # The extension starts at the LAST dot, so "..exe" is ".exe" exactly as Windows reads it.
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", ["payload.exe/", "dir/payload.EXE/", "/payload.exe/", "payload.exe./"])
    def test_a_trailing_slash_does_not_hide_the_extension(self, validate, key):
        # The last non-empty segment is the name: a client that builds a local path with pathlib drops
        # the trailing slash, so "payload.exe/" is "payload.exe" on disk.
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    def test_case_and_trailing_dot_together(self, validate):
        # #412's lower-casing applies after the basename is normalized.
        assert validate(".EXE.", BENIGN_CONTENT_TYPE) is False
        assert validate("Payload.EXE. ", BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize(
        "key, expected",
        [
            ("payload.exe.", "Unallowed file extension detected in asset: payload.exe."),
            ("dir/.exe", "Unallowed file extension detected in asset: dir/.exe"),
        ],
    )
    def test_rejection_logs_the_key_as_given_not_the_normalized_name(
        self, validate, logger, key, expected
    ):
        validate(key, BENIGN_CONTENT_TYPE)
        assert logger.errors == [expected]


@pytest.mark.unit
class TestUnchangedBehaviour:
    """Positive control: these pass before and after the normalization."""

    @pytest.mark.parametrize(
        "key", [".gitignore", "dir/.env.example", ".tar.gz", "archive.tar.gz", "..gitignore", "a..txt"]
    )
    def test_dotfiles_and_multi_suffix_names_stay_allowed(self, validate, key):
        # The extension starts at the last dot: ".gitignore" and "..gitignore" are inspected as
        # ".gitignore" (not listed), ".tar.gz" yields ".gz" and "a..txt" yields ".txt".
        assert validate(key, BENIGN_CONTENT_TYPE) is True

    @pytest.mark.parametrize("key", ["assets/", "docs.v2/", "dir/sub/", "/"])
    def test_a_folder_name_without_a_blocked_extension_is_allowed(self, validate, key):
        # A key ending in '/' is inspected by its last non-empty segment, so an ordinary folder name
        # passes; one that ends in a blocked extension does not (TestNormalizedBasename).
        assert validate(key, BENIGN_CONTENT_TYPE) is True

    @pytest.mark.parametrize("key", ["payload.exe", "payload.EXE", "script.bAt", "library.DLL"])
    def test_blocklisted_extension_is_still_rejected(self, validate, key):
        # From the #412 suite: the plain forms are unaffected.
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", ["notes.txt", "model.GLB", "texture.Png", "assets/README"])
    def test_allowed_names_are_still_accepted(self, validate, key):
        assert validate(key, BENIGN_CONTENT_TYPE) is True

    def test_only_the_final_suffix_counts(self, validate):
        assert validate("archive.exe.txt", BENIGN_CONTENT_TYPE) is True
        assert validate("archive.txt.EXE", BENIGN_CONTENT_TYPE) is False

    def test_a_trailing_dot_inside_the_path_is_not_the_basename(self, validate):
        # Only the basename is normalized; 'dir.' as a folder name has nothing to do with the file.
        assert validate("dir./notes.txt", BENIGN_CONTENT_TYPE) is True
        assert validate("dir./payload.exe", BENIGN_CONTENT_TYPE) is False

    def test_content_type_is_still_checked_first(self, validate, logger):
        assert validate("payload.exe.", "application/x-msdownload") is False
        assert logger.errors[0].startswith("Unallowed file content type")
