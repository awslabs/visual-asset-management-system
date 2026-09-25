# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Identity helpers shared by the OpenSearch indexers, the vector store and the NLP search route.

Pure functions over the standard library: importable without any OpenSearch, SSM or boto3 side
effect. The index and delete paths, the indexers and the search handlers all address the same
document, so none of these ids may be re-derived inline by a caller.
"""

import hashlib
from typing import Optional

# OpenSearch refuses a document _id longer than 512 bytes.
MAX_OPENSEARCH_DOCUMENT_ID_BYTES = 512

# DynamoDB refuses a sort-key value longer than 1024 bytes.
SORT_KEY_MAX_BYTES = 1024

# A segment item's key suffix (a video time window or a text chunk) is at most this many bytes.
SEGMENT_KEY_MAX_BYTES = 32

# Byte budget for the file-path part of a vector item's sort key. The key is ``{keyPath}#{versionId}``
# or ``{keyPath}#{versionId}#{segmentKey}``, an S3 version id is at most 64 bytes in practice and a
# segment key at most SEGMENT_KEY_MAX_BYTES, so the path part may use 1024 - 1 - 64 - 1 - 32 bytes.
FILE_PATH_KEY_BUDGET = 926

# The closed set of segment kinds. ``none`` is the whole-file item; ``animationTime`` is reserved for
# frame windows of an animated 3D file and shares the ``videoTime`` key shape.
SEGMENT_KINDS = ("none", "videoTime", "textChunk", "animationTime")

_SHA256_HEX_LENGTH = 64
_KEY_SEPARATOR = "#"
# The key path percent-encodes the escape character and then the separator, so the separator never
# occurs inside a path component of a sort key and a literal ``%23`` in a path stays distinct from ``#``.
_KEY_PATH_ENCODING = (("%", "%25"), ("#", "%23"))
_VIDEO_SEGMENT_KEY_DIGITS = 10
_TEXT_CHUNK_KEY_DIGITS = 6


def _shorten_utf8(value: str, budget: int) -> str:
    """``value`` unchanged when it fits ``budget`` UTF-8 bytes; otherwise a byte-truncated prefix,
    ``#`` and the SHA-256 hex digest of the full value, which together fill the budget exactly."""
    encoded = value.encode("utf-8")
    if len(encoded) <= budget:
        return value
    digest = hashlib.sha256(encoded).hexdigest()
    prefix_budget = budget - _SHA256_HEX_LENGTH - 1
    prefix = encoded[:prefix_budget].decode("utf-8", errors="ignore")
    return f"{prefix}#{digest}"


def build_file_document_id(database_id: str, asset_id: str, file_path: str) -> str:
    """The OpenSearch _id of a file document: ``{databaseId}#{assetId}#{filePath}``, shortened to a
    prefix plus digest when it exceeds MAX_OPENSEARCH_DOCUMENT_ID_BYTES. The path is not encoded."""
    return _shorten_utf8(f"{database_id}#{asset_id}#{file_path}", MAX_OPENSEARCH_DOCUMENT_ID_BYTES)


def build_asset_document_id(database_id: str, asset_id: str, archived: bool) -> str:
    """The OpenSearch _id of an asset document: ``{databaseId}#{assetId}``, with ``#deleted`` appended
    to the database id of an archived asset unless the id already carries it."""
    normalized_database_id = database_id
    if archived and "#deleted" not in normalized_database_id:
        normalized_database_id = f"{normalized_database_id}#deleted"
    return f"{normalized_database_id}#{asset_id}"


def _encode_key_path(file_path: str) -> str:
    """``file_path`` with ``%`` and ``#`` percent-encoded (``%25``, ``%23``)."""
    encoded = file_path
    for plain, escaped in _KEY_PATH_ENCODING:
        encoded = encoded.replace(plain, escaped)
    return encoded


def vector_file_key_path(file_path: str) -> str:
    """The file-path part of a vector item's sort key; identical for every version of the file. The
    percent-encoded path when that fits FILE_PATH_KEY_BUDGET bytes, otherwise a byte-truncated prefix
    of the encoded path, ``#`` and the SHA-256 hex digest of the plain path, which together fill the
    budget. The separator therefore occurs in a key path only in front of a digest, so
    ``begins_with(SK, key_path + "#")`` matches exactly one file."""
    encoded = _encode_key_path(file_path)
    encoded_utf8 = encoded.encode("utf-8")
    if len(encoded_utf8) <= FILE_PATH_KEY_BUDGET:
        return encoded
    digest = hashlib.sha256(file_path.encode("utf-8")).hexdigest()
    prefix = encoded_utf8[: FILE_PATH_KEY_BUDGET - _SHA256_HEX_LENGTH - 1].decode("utf-8", errors="ignore")
    return f"{prefix}{_KEY_SEPARATOR}{digest}"


def build_vector_file_version_key(file_path: str, version_id: Optional[str], segment_key: str = "") -> str:
    """The vector item's sort key: ``{keyPath}#{versionId}`` for the whole-file item, followed by
    ``#{segmentKey}`` for a segment item. An absent version id is stored as ``null``. A segment key
    over SEGMENT_KEY_MAX_BYTES or containing the ``#`` separator raises ValueError, as does an
    assembled key over SORT_KEY_MAX_BYTES."""
    key = f"{vector_file_key_path(file_path)}{_KEY_SEPARATOR}{version_id or 'null'}"
    if segment_key:
        if len(segment_key.encode("utf-8")) > SEGMENT_KEY_MAX_BYTES:
            raise ValueError(f"segment key {segment_key!r} exceeds {SEGMENT_KEY_MAX_BYTES} bytes")
        if _KEY_SEPARATOR in segment_key:
            raise ValueError(f"segment key {segment_key!r} contains the key separator {_KEY_SEPARATOR!r}")
        key = f"{key}{_KEY_SEPARATOR}{segment_key}"
    key_bytes = len(key.encode("utf-8"))
    if key_bytes > SORT_KEY_MAX_BYTES:
        raise ValueError(f"sort key of {key_bytes} bytes for {file_path!r} exceeds {SORT_KEY_MAX_BYTES} bytes")
    return key


def _fixed_width_key(prefix: str, value: int, digits: int, what: str) -> str:
    if value < 0:
        raise ValueError(f"{what} must not be negative, got {value}")
    if value >= 10**digits:
        raise ValueError(f"{what} {value} does not fit {digits} digits")
    return f"{prefix}{value:0{digits}d}"


def build_video_segment_key(start_ms: int) -> str:
    """A video time window's segment key: ``t`` and its start millisecond zero-padded to ten digits
    (``t0000083456``), so the keys of one version sort by start time."""
    return _fixed_width_key("t", start_ms, _VIDEO_SEGMENT_KEY_DIGITS, "segment start millisecond")


def build_text_chunk_key(index: int) -> str:
    """A text chunk's segment key: ``c`` and its 1-based ordinal zero-padded to six digits
    (``c000012`` is chunk 12 of the label), so the keys of one version sort by chunk order."""
    return _fixed_width_key("c", index, _TEXT_CHUNK_KEY_DIGITS, "chunk index")
