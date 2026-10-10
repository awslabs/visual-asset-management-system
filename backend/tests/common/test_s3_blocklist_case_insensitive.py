# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Case handling of the executable blocklist, common.s3.validateUnallowedFileExtensionAndContentType.

The helper is the one executable blocklist behind every upload, download, stream and
workflow-output path (uploadFile, sqsUploadFileLarge, downloadAsset, streamAsset,
streamAuxiliaryPreviewAsset, processWorkflowExecutionOutput), so the comparison it makes IS the
control. Both inputs are case-insensitive to the systems that produce them -- Windows preserves
``.EXE`` and an uploader chooses the case freely; RFC 2045 makes ``Application/X-Msdownload`` the
same media type as its lower-case spelling -- while the blocklists in common/constants.py are
written lower-case. A plain ``in`` test therefore admitted every upper- or mixed-case variant
(issue #412), and admitted S3's ``application/x-msdownload; charset=binary`` as well, because the
parameter defeats an exact match. The helper lower-cases the extension and the media-type part of
the content type before the membership test; the lists stay lower-case, and the hygiene tests here
are what keep them that way, so a future upper-case entry cannot silently never match.

tests/conftest.py replaces ``common.s3`` with the tests/mocks/common/s3.py stand-in (the real
module builds a boto3 client at import) and ``common.constants`` with a MagicMock, so the function
under test is loaded from source into a private namespace -- the way
tests/common/test_s3_archive_status.py loads is_object_version_archived -- and the lists come from
the real constants module by package path, as tests/common/test_constants_no_shadowed_definitions.py
reads them.
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
# A key that is on no blocklist, so the content-type branch is the one deciding.
BENIGN_KEY = "assets/model.glb"


@pytest.mark.unit
class TestExtensionCase:
    @pytest.mark.parametrize(
        "key",
        ["payload.exe", "payload.EXE", "payload.Exe", "script.bAt", "library.DLL"],
    )
    def test_blocklisted_extension_is_rejected_in_any_case(self, validate, key):
        assert validate(key, BENIGN_CONTENT_TYPE) is False

    @pytest.mark.parametrize("key", ["notes.txt", "model.GLB", "texture.Png"])
    def test_allowed_extension_is_accepted_in_any_case(self, validate, key):
        assert validate(key, BENIGN_CONTENT_TYPE) is True

    def test_key_without_extension_is_accepted(self, validate):
        assert validate("assets/README", BENIGN_CONTENT_TYPE) is True

    def test_only_the_final_suffix_counts(self, validate):
        # Unchanged from the case-sensitive behaviour: os.path.splitext yields ".txt".
        assert validate("archive.exe.txt", BENIGN_CONTENT_TYPE) is True
        assert validate("archive.txt.EXE", BENIGN_CONTENT_TYPE) is False

    def test_rejection_logs_the_key_as_given(self, validate, logger):
        validate("uploads/Payload.EXE", BENIGN_CONTENT_TYPE)
        assert logger.errors == [
            "Unallowed file extension detected in asset: uploads/Payload.EXE"
        ]


@pytest.mark.unit
class TestContentTypeCase:
    @pytest.mark.parametrize(
        "content_type",
        [
            "application/x-msdownload",
            "Application/X-Msdownload",
            "APPLICATION/X-MSDOWNLOAD",
            "application/x-msdownload; charset=binary",
            "application/x-msdownload ;charset=binary",
            # The IANA-registered spelling of the macro-enabled Word type carries a capital E;
            # it is what clients send, so the lower-cased list entry has to catch it.
            "application/vnd.ms-word.document.macroEnabled.12",
        ],
    )
    def test_blocklisted_media_type_is_rejected_in_any_case_or_with_parameters(
        self, validate, content_type
    ):
        assert validate(BENIGN_KEY, content_type) is False

    @pytest.mark.parametrize(
        "content_type",
        ["model/gltf-binary", "Model/GLTF-Binary", "model/gltf-binary; charset=binary", ""],
    )
    def test_allowed_media_type_is_accepted(self, validate, content_type):
        assert validate(BENIGN_KEY, content_type) is True

    def test_missing_content_type_is_accepted(self, validate):
        # head_object omits ContentType for some objects; callers pass None through.
        assert validate(BENIGN_KEY, None) is True

    def test_rejection_logs_the_key_as_given(self, validate, logger):
        validate("uploads/Payload.GLB", "Application/X-Msdownload")
        assert logger.errors == [
            "Unallowed file content type detected in asset: uploads/Payload.GLB"
        ]

    def test_content_type_is_checked_before_the_extension(self, validate, logger):
        # Both branches reject; the first log line names the content type, as before.
        assert validate("payload.EXE", "Application/X-Msdownload") is False
        assert logger.errors[0].startswith("Unallowed file content type")


# The blocklist as documented in backend/CLAUDE.md "File Security" and
# documentation/docusaurus-site/docs/architecture/security.md "Blocked File Types".
DOCUMENTED_EXTENSIONS = {
    ".jar", ".java", ".com", ".php", ".reg", ".pif", ".bak", ".dll", ".exe", ".nat",
    ".cmd", ".lnk", ".docm", ".vbs", ".bat",
}
DOCUMENTED_MIME_TYPES = {
    "application/java-archive",
    "application/x-python-code",
    "text/x-python-source",
    "text/x-java-source",
    "application/x-sh",
    "application/java-vm",
    "application/x-msdownload",
    "application/x-php",
    "application/x-ms-dos-executable",
    "application/x-ini",
    "application/x-inf",
    "application/x-sql",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-ms-shortcut",
    "application/x-bat-script",
    "application/vnd.ms-word.document.macroenabled.12",
    "application/javascript",
    "application/x-vbs",
    "application/x-powershell",
    "application/x-msdos-program",
    "application/vbscript",
    "application/powershell",
}


@pytest.mark.unit
class TestBlocklistHygiene:
    """The helper lower-cases its inputs, so an entry that is not lower-case can never match."""

    @pytest.mark.parametrize(
        "name, entries",
        [
            ("UNALLOWED_FILE_EXTENSION_LIST", UNALLOWED_FILE_EXTENSION_LIST),
            ("UNALLOWED_MIME_LIST", UNALLOWED_MIME_LIST),
        ],
    )
    def test_every_entry_is_lower_case(self, name, entries):
        not_lower = [entry for entry in entries if entry != entry.lower()]
        assert not not_lower, f"{name} entries that can never match a lower-cased input: {not_lower}"

    @pytest.mark.parametrize(
        "name, entries",
        [
            ("UNALLOWED_FILE_EXTENSION_LIST", UNALLOWED_FILE_EXTENSION_LIST),
            ("UNALLOWED_MIME_LIST", UNALLOWED_MIME_LIST),
        ],
    )
    def test_no_duplicate_entries(self, name, entries):
        duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
        assert not duplicates, f"{name} lists these entries more than once: {duplicates}"

    def test_every_extension_starts_with_a_dot(self):
        # os.path.splitext returns the suffix with its dot, so a dotless entry never matches.
        assert all(entry.startswith(".") for entry in UNALLOWED_FILE_EXTENSION_LIST)

    @pytest.mark.temporary  # pins the one-time removal of the duplicated .java / .exe entries from the lists
    def test_dedup_added_and_removed_nothing(self):
        # Positive control for the list hygiene: the same set of entries as before, only once each.
        assert set(UNALLOWED_FILE_EXTENSION_LIST) == DOCUMENTED_EXTENSIONS
        assert set(UNALLOWED_MIME_LIST) == DOCUMENTED_MIME_TYPES
