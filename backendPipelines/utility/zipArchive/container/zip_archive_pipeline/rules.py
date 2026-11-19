# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-file refusal rules the archive applies before writing a byte.

A zip download carries the deployment's merged blocklists in its request manifest (the API reads them
from backend ``common/constants.py``, add-on and custom entries included), so they are always current.
A zipArchive run started as an ordinary workflow has no such manifest and uses the core lists below;
``tests/test_runner.py`` fails when they drift from backend ``CORE_UNALLOWED_*``.

The matching mirrors backend ``common/s3.validateUnallowedFileExtensionAndContentType`` and
``common/downloadEligibility.is_file_archived``. Preview companions (``{file}.previewFile.{ext}``) are
left out of every archive unless the run configuration or the request manifest sets
``includePreviewFiles`` to true.
"""

import os

CORE_BLOCKED_EXTENSIONS = (
    ".jar", ".java", ".com", ".php", ".reg", ".pif", ".bak", ".dll", ".exe", ".nat", ".cmd", ".lnk",
    ".docm", ".vbs", ".bat",
)
CORE_BLOCKED_CONTENT_TYPES = (
    "application/java-archive", "application/x-python-code", "text/x-python-source",
    "text/x-java-source", "application/x-sh", "application/java-vm", "application/x-msdownload",
    "application/x-php", "application/x-ms-dos-executable", "application/x-ini", "application/x-inf",
    "application/x-sql", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-ms-shortcut", "application/x-bat-script",
    "application/vnd.ms-word.document.macroenabled.12", "application/javascript", "application/x-vbs",
    "application/x-powershell", "application/x-msdos-program", "application/vbscript",
    "application/powershell",
)

VAMS_STATUS_METADATA_KEY = "vams-status"
ARCHIVED_STATUSES = ("archived", "deleted")
ARCHIVED_STORAGE_CLASSES = ("GLACIER", "DEEP_ARCHIVE")

# The marker of a file-level preview companion, ``{file}.previewFile.{ext}``: a key that contains it is
# a preview of its base file. Mirrors backend ``common/s3PathPatterns.PREVIEW_FILE_PATTERN`` (the file
# list hides these keys and the export folds them into their base file's entry); tests/test_runner.py
# fails when the two drift. An archive leaves companions out unless ``includePreviewFiles`` is true.
PREVIEW_FILE_PATTERN = ".previewFile."
INCLUDE_PREVIEW_FILES_KEY = "includePreviewFiles"


class Refusal(Exception):
    """A user-safe reason the whole archive is refused."""


# A member that is archived (vams-status, an archive storage class) or whose current version is a
# delete marker: the same words, without naming the file, as the create's refusal (backend
# common/zipDownloads.MSG_ARCHIVED_IN_SELECTION), so a file archived between create and run reads
# the same as one archived before. Pinned by tests/test_runner.py.
ARCHIVED_IN_SELECTION = ("The selection includes an archived or deleted item. Remove it from the "
                         "selection, or restore it first.")


def is_blocked(key, content_type, blocked_extensions, blocked_content_types):
    """True when the key's extension or the object's media type is on a blocklist."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type and media_type in blocked_content_types:
        return True
    for name in os.path.basename(key.rstrip("/")).split(":"):
        while name and (name[-1] == "." or name[-1].isspace()):
            name = name[:-1]
        extension = name[name.rfind("."):].lower() if "." in name else ""
        if extension and extension in blocked_extensions:
            return True
    return False


def is_archived(head):
    status = (head.get("Metadata") or {}).get(VAMS_STATUS_METADATA_KEY, "")
    return status in ARCHIVED_STATUSES or head.get("StorageClass", "STANDARD") in ARCHIVED_STORAGE_CLASSES


def is_preview_companion(key):
    """True when the key is a file-level preview companion (it contains ``.previewFile.``)."""
    return PREVIEW_FILE_PATTERN in (key or "")


def include_preview_files(source):
    """The ``includePreviewFiles`` value of a run configuration or a request manifest: false when the key
    is missing; a value that is not a boolean refuses the archive."""
    value = (source or {}).get(INCLUDE_PREVIEW_FILES_KEY, False)
    if not isinstance(value, bool):
        raise Refusal(f"{INCLUDE_PREVIEW_FILES_KEY} must be true or false")
    return value
