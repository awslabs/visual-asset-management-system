# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Canonical definitions of the special DynamoDB metadata keys and field-name
prefixes VAMS reserves in its metadata storage tables.

VAMS stores user metadata as one DynamoDB item per key in the
AssetFileMetadata table (composite key ``databaseId:assetId:filePath``,
attribute ``metadataKey``). A small set of key names and field-name prefixes
within that namespace are system-owned: the reindexer writes a marker record
to trigger stream processing, and the indexers / export service exclude
internal fields from user-facing output.

This module is the single source of truth for these values. Do not redefine
the literal key names or prefixes at call sites -- import them from here so
all usages can be found and changed in one place.

Note: this is a *separate* namespace from the S3 object user-metadata keys
(``assetid``, ``vams-*`` -- see ``common/s3MetadataKeys.py``) and from the
Physna ISV API tracking keys (``__VAMS__*`` in
``handlers/addon/physna/physnaCommon.py``). Those are not DynamoDB metadata
keys and are intentionally not defined here.
"""

from typing import FrozenSet

# ---------------------------------------------------------------------------
# Reindex marker record.
#
# The crReindexer "touches" the AssetFileMetadata table by creating and then
# immediately deleting an item with this metadataKey. The resulting DynamoDB
# stream events (INSERT then REMOVE) trigger the indexers to re-index the
# asset/file without changing any real metadata. Every consumer that reads
# metadata items (OpenSearch indexers, Garnet indexers, Physna sync) must
# skip records carrying this key -- it is a system marker, not user metadata.
# ---------------------------------------------------------------------------
REINDEX_METADATA_RECORD_KEY = "REINDEX_METADATA_RECORD"

# ---------------------------------------------------------------------------
# Excluded (system) metadata record keys.
#
# The full set of metadataKey values that are system records rather than user
# metadata. Readers iterating metadata items must check membership here (via
# :func:`is_excluded_metadata_record`) rather than comparing against
# individual key constants, so future system keys can be added in one place
# without touching every call site. Writers of a specific marker record
# (e.g. the crReindexer) still reference the individual key constant.
# ---------------------------------------------------------------------------
EXCLUDED_METADATA_RECORD_KEYS: FrozenSet[str] = frozenset(
    {
        REINDEX_METADATA_RECORD_KEY,
    }
)

# ---------------------------------------------------------------------------
# Internal field-name prefixes.
#
# Metadata field names carrying these prefixes are VAMS-internal and excluded
# from user-facing output:
#
# VAMS_INTERNAL_FIELD_PREFIX: uppercase ``VAMS_`` prefix on DynamoDB-sourced
#   asset-metadata fields. Excluded from OpenSearch field extraction (see
#   models/indexing.py).
# HIDDEN_FIELD_PREFIX: leading underscore. Excluded from both OpenSearch
#   field extraction and asset export output. OpenSearch additionally
#   reserves leading-underscore field names for system fields.
# ---------------------------------------------------------------------------
VAMS_INTERNAL_FIELD_PREFIX = "VAMS_"
HIDDEN_FIELD_PREFIX = "_"


def is_excluded_metadata_record(metadata_key) -> bool:
    """Return True if ``metadata_key`` is a system metadata record to skip.

    Use this when iterating metadata items instead of comparing against
    individual key constants, so future system keys added to
    :data:`EXCLUDED_METADATA_RECORD_KEYS` are picked up by every call site.
    """
    return metadata_key in EXCLUDED_METADATA_RECORD_KEYS


def is_internal_metadata_field(field_name: str) -> bool:
    """Return True if ``field_name`` is a VAMS-internal metadata field.

    A field is internal if it carries the ``VAMS_`` prefix or a leading
    underscore. Internal fields are excluded from search indexing and from
    user-facing metadata output (e.g. asset export).
    """
    return field_name.startswith(VAMS_INTERNAL_FIELD_PREFIX) or field_name.startswith(
        HIDDEN_FIELD_PREFIX
    )
