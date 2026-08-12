# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Canonical definitions of the special S3 object user-metadata keys VAMS writes
onto asset files.

VAMS stores a small set of system-owned fields in the user-metadata of every
asset object in S3 (the ``Metadata`` dict on the object). These fields are how
downstream consumers -- the indexers (OpenSearch/Garnet), workflow auto-trigger,
the Physna sync add-on, archive/download gating -- recover the asset/database
context for a raw S3 object and learn VAMS-managed status about it.

Naming conventions in play
---------------------------
Two distinct conventions exist for historical reasons and are preserved here for
backwards compatibility with already-stored objects:

  * **Bare lowercase** (``assetid``, ``databaseid``, ``uploadid``) -- the asset
    context keys written at upload time. S3 lowercases user-metadata keys on
    storage, so these are read back lowercase regardless of how they were sent.
  * **``vams-`` hyphen prefix** (``vams-primarytype``, ``vams-status``) -- the
    VAMS-semantic fields. The :data:`VAMS_METADATA_KEY_PREFIX` is the marker the
    indexers use to recognize VAMS-owned keys.

Note: this is a *separate* namespace from the asset-metadata fields sourced from
DynamoDB (which use the uppercase ``VAMS_`` / ``_`` prefix convention enforced in
``models/indexing.py``) and from the Physna ISV API tracking keys
(``__VAMS__*`` in ``handlers/addon/physna/physnaCommon.py``). Those are not S3
object metadata and are intentionally not defined here.
"""

from typing import FrozenSet

# ---------------------------------------------------------------------------
# Asset-context keys (bare lowercase).
#
# Written onto every uploaded asset object so downstream consumers can recover
# the owning asset/database (and the originating upload) from a raw S3 object.
# ---------------------------------------------------------------------------
ASSET_ID_METADATA_KEY = "assetid"
DATABASE_ID_METADATA_KEY = "databaseid"
UPLOAD_ID_METADATA_KEY = "uploadid"

# ---------------------------------------------------------------------------
# VAMS-semantic keys (``vams-`` hyphen prefix).
#
# VAMS_PRIMARY_TYPE: the user-assigned "primary type" of a file. Unlike the
#   other VAMS keys it IS surfaced to search indexing (see
#   SEARCHABLE_VAMS_METADATA_KEYS below).
# VAMS_STATUS: lifecycle status of the object ("archived" / "deleted"). Read to
#   gate downloads and indexing; the values are defined in VAMS_STATUS_* below.
# ---------------------------------------------------------------------------
VAMS_METADATA_KEY_PREFIX = "vams-"

VAMS_PRIMARY_TYPE_METADATA_KEY = "vams-primarytype"
VAMS_STATUS_METADATA_KEY = "vams-status"

# Recognized values for VAMS_STATUS_METADATA_KEY.
VAMS_STATUS_ARCHIVED = "archived"
VAMS_STATUS_DELETED = "deleted"

# ---------------------------------------------------------------------------
# Change-provenance keys (``vams-`` hyphen prefix).
#
# Stamped onto each new S3 object version by the creating action (upload, workflow,
# copy, move, rename, unarchive). sqsBucketSync reads them back and writes a single
# change-history record. Naming: ``vams-changesource`` / ``vams-changeuserid`` / etc.
# ---------------------------------------------------------------------------
VAMS_CHANGE_SOURCE_METADATA_KEY = "vams-changesource"
VAMS_CHANGE_USER_ID_METADATA_KEY = "vams-changeuserid"
VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY = "vams-changeworkflowexecutionid"
VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY = "vams-changeworkflowid"
VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY = "vams-changeassetidfrom"
VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY = "vams-changedatabaseidfrom"
VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY = "vams-changeassetfilepathfrom"
VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY = "vams-changeassetfileversionfrom"

# Recognized values for VAMS_CHANGE_SOURCE_METADATA_KEY.
VAMS_CHANGE_SOURCE_DIRECT = "direct"
VAMS_CHANGE_SOURCE_UPLOAD = "upload"
VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION = "workflowExecution"
VAMS_CHANGE_SOURCE_FILE_COPY = "fileCopy"
VAMS_CHANGE_SOURCE_FILE_MOVE = "fileMove"
VAMS_CHANGE_SOURCE_FILE_RENAME = "fileRename"
VAMS_CHANGE_SOURCE_FILE_ARCHIVE = "fileArchive"
VAMS_CHANGE_SOURCE_FILE_UNARCHIVE = "fileUnarchive"
VAMS_CHANGE_SOURCE_ASSET_ARCHIVE = "assetArchive"
VAMS_CHANGE_SOURCE_ASSET_UNARCHIVE = "assetUnarchive"
VAMS_CHANGE_SOURCE_FILE_REVERT = "fileRevert"

VAMS_CHANGE_SOURCE_VALUES: FrozenSet[str] = frozenset(
    {
        VAMS_CHANGE_SOURCE_DIRECT,
        VAMS_CHANGE_SOURCE_UPLOAD,
        VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
        VAMS_CHANGE_SOURCE_FILE_COPY,
        VAMS_CHANGE_SOURCE_FILE_MOVE,
        VAMS_CHANGE_SOURCE_FILE_RENAME,
        VAMS_CHANGE_SOURCE_FILE_ARCHIVE,
        VAMS_CHANGE_SOURCE_FILE_UNARCHIVE,
        VAMS_CHANGE_SOURCE_ASSET_ARCHIVE,
        VAMS_CHANGE_SOURCE_ASSET_UNARCHIVE,
        VAMS_CHANGE_SOURCE_FILE_REVERT,
    }
)

# All change-provenance keys. Used to exclude from search and reset on new versions.
CHANGE_PROVENANCE_METADATA_KEYS: FrozenSet[str] = frozenset(
    {
        VAMS_CHANGE_SOURCE_METADATA_KEY,
        VAMS_CHANGE_USER_ID_METADATA_KEY,
        VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
        VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
        VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY,
        VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY,
        VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY,
        VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY,
    }
)


def normalize_history_file_path(file_path: str) -> str:
    """Return the asset-relative file path with exactly one leading slash."""
    return "/" + (file_path or "").lstrip("/")

# ---------------------------------------------------------------------------
# Indexing classification sets.
#
# ASSET_CONTEXT_METADATA_KEYS are the bare lowercase context keys. Together with
# the VAMS_METADATA_KEY_PREFIX they identify the system-owned metadata that the
# file indexers exclude from generic search field extraction.
#
# SEARCHABLE_VAMS_METADATA_KEYS are the VAMS-prefixed keys that, despite the
# prefix, should still be surfaced to search.
# ---------------------------------------------------------------------------
ASSET_CONTEXT_METADATA_KEYS: FrozenSet[str] = frozenset(
    {
        ASSET_ID_METADATA_KEY,
        DATABASE_ID_METADATA_KEY,
        UPLOAD_ID_METADATA_KEY,
    }
)

SEARCHABLE_VAMS_METADATA_KEYS: FrozenSet[str] = frozenset(
    {
        VAMS_PRIMARY_TYPE_METADATA_KEY,
    }
)


def is_system_metadata_key(key: str) -> bool:
    """Return True if ``key`` is a VAMS system-owned S3 metadata key.

    A key is system-owned if it is one of the asset-context keys
    (:data:`ASSET_CONTEXT_METADATA_KEYS`) or carries the VAMS metadata prefix
    (:data:`VAMS_METADATA_KEY_PREFIX`). Use this to exclude system metadata from
    generic field extraction (e.g. search indexing) instead of duplicating the
    literal key list at each call site.
    """
    return key in ASSET_CONTEXT_METADATA_KEYS or key.startswith(VAMS_METADATA_KEY_PREFIX)
