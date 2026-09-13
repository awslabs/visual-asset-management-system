# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Identity helpers shared by the OpenSearch indexers, the vector store and the NLP search route.

Two size ceilings shape these ids. OpenSearch refuses a document ``_id`` over 512 bytes while an S3
key may be 1024 bytes, so a long file id is shortened to a byte prefix plus a SHA-256 digest of the
full id -- and the index and delete paths must derive the SAME id, which is why the function is shared
rather than copied. DynamoDB caps a sort-key value at 1024 bytes; the vector item's sort key is
``{keyPath}#{versionId}`` for a whole-file item and ``{keyPath}#{versionId}#{segmentKey}`` for a
segment item, an S3 version id is at most 64 bytes in practice and a segment key at most 32, so the
path part has a 926-byte budget and, for a long path, every version of the file shares one shortened
prefix (a ``begins_with(SK, keyPath#)`` groups the file's items; ``begins_with(SK, keyPath#versionId#)``
one version's segments). The key path percent-encodes ``%`` and ``#`` so the separator never occurs
inside a path component: without that, the whole-file key of a file named ``/a#null`` on an
unversioned bucket sits under the version prefix of the file ``/a``.

The module is imported through its package path here: it is pure (hashlib + typing), so it needs no
boto3 stubbing and no by-path load.
"""

import hashlib
from urllib.parse import unquote

import pytest

from backend.backend.common.indexing.documentIds import (
    FILE_PATH_KEY_BUDGET,
    MAX_OPENSEARCH_DOCUMENT_ID_BYTES,
    SEGMENT_KEY_MAX_BYTES,
    SEGMENT_KINDS,
    SORT_KEY_MAX_BYTES,
    build_asset_document_id,
    build_file_document_id,
    build_text_chunk_key,
    build_vector_file_version_key,
    build_video_segment_key,
    vector_file_key_path,
)


def _path_of_bytes(n, file_name="model.glb"):
    """An asset-relative path of exactly ``n`` UTF-8 bytes (ASCII, so bytes == characters) that carries
    no character the key path encodes, so its encoded form is the path itself."""
    body = "/" + "d" * (n - 1 - len("/" + file_name)) + "/" + file_name
    assert len(body.encode("utf-8")) == n
    return body


@pytest.mark.unit
class TestConstants:
    def test_opensearch_id_ceiling(self):
        assert MAX_OPENSEARCH_DOCUMENT_ID_BYTES == 512

    def test_sort_key_ceiling(self):
        assert SORT_KEY_MAX_BYTES == 1024

    def test_file_path_key_budget_leaves_room_for_a_64_byte_version_id_and_a_32_byte_segment_key(self):
        assert FILE_PATH_KEY_BUDGET == 926 == SORT_KEY_MAX_BYTES - 1 - 64 - 1 - SEGMENT_KEY_MAX_BYTES

    def test_segment_key_cap(self):
        assert SEGMENT_KEY_MAX_BYTES == 32

    def test_segment_kinds_are_the_closed_set_with_the_reserved_kind_last(self):
        assert SEGMENT_KINDS == ("none", "videoTime", "textChunk", "animationTime")


@pytest.mark.unit
class TestFileDocumentId:
    def test_short_id_is_the_plain_triple(self):
        assert build_file_document_id("db-1", "asset-1", "/dir/model.glb") == "db-1#asset-1#/dir/model.glb"

    def test_the_opensearch_id_keeps_the_path_unencoded(self):
        """The algorithm is the one the live index was built with; existing documents keep their ids."""
        assert build_file_document_id("db-1", "asset-1", "/dir/a#b%c.glb") == "db-1#asset-1#/dir/a#b%c.glb"

    def test_long_id_fits_the_ceiling_and_ends_with_the_digest_of_the_full_id(self):
        path = _path_of_bytes(1000)
        full = f"db-1#asset-1#{path}"
        doc_id = build_file_document_id("db-1", "asset-1", path)
        assert len(doc_id.encode("utf-8")) == MAX_OPENSEARCH_DOCUMENT_ID_BYTES
        digest = hashlib.sha256(full.encode("utf-8")).hexdigest()
        assert doc_id.endswith("#" + digest)
        assert doc_id.startswith(full[: MAX_OPENSEARCH_DOCUMENT_ID_BYTES - 64 - 1])

    def test_two_long_paths_sharing_the_visible_prefix_get_distinct_ids(self):
        a = _path_of_bytes(1000, "a.glb")
        b = _path_of_bytes(1000, "b.glb")
        assert build_file_document_id("db", "asset", a) != build_file_document_id("db", "asset", b)

    def test_index_and_delete_paths_derive_the_same_id(self):
        path = _path_of_bytes(700)
        assert build_file_document_id("db", "asset", path) == build_file_document_id("db", "asset", path)

    def test_multibyte_prefix_is_cut_on_a_character_boundary(self):
        path = "/" + "é" * 600 + "/model.glb"  # 2 bytes per character
        doc_id = build_file_document_id("db", "asset", path)
        assert len(doc_id.encode("utf-8")) <= MAX_OPENSEARCH_DOCUMENT_ID_BYTES
        doc_id.encode("utf-8")  # a torn code point would have been dropped, never emitted


@pytest.mark.unit
class TestAssetDocumentId:
    def test_live_asset(self):
        assert build_asset_document_id("db-1", "asset-1", False) == "db-1#asset-1"

    def test_archived_asset_carries_the_deleted_marker_on_the_database_id(self):
        assert build_asset_document_id("db-1", "asset-1", True) == "db-1#deleted#asset-1"

    def test_marker_is_not_doubled_when_the_database_id_already_carries_it(self):
        assert build_asset_document_id("db-1#deleted", "asset-1", True) == "db-1#deleted#asset-1"

    def test_archived_partition_id_with_archived_false_is_left_as_is(self):
        """The delete path passes the stored partition id through unchanged."""
        assert build_asset_document_id("db-1#deleted", "asset-1", False) == "db-1#deleted#asset-1"


@pytest.mark.unit
class TestVectorFileVersionKey:
    def test_short_path_key_is_path_hash_version(self):
        assert build_vector_file_version_key("/dir/model.glb", "v1") == "/dir/model.glb#v1"

    @pytest.mark.parametrize("absent", [None, ""])
    def test_absent_version_id_is_stored_as_null(self, absent):
        assert build_vector_file_version_key("/dir/model.glb", absent) == "/dir/model.glb#null"

    def test_key_path_of_a_short_path_is_the_path(self):
        assert vector_file_key_path("/dir/model.glb") == "/dir/model.glb"

    def test_two_versions_of_a_1020_byte_path_share_the_prefix_and_fit_the_sort_key_limit(self):
        path = _path_of_bytes(1020)
        key_path = vector_file_key_path(path)
        assert len(key_path.encode("utf-8")) == FILE_PATH_KEY_BUDGET
        assert key_path.endswith("#" + hashlib.sha256(path.encode("utf-8")).hexdigest())
        v1 = build_vector_file_version_key(path, "A" * 32)
        v2 = build_vector_file_version_key(path, "B" * 32)
        assert v1 != v2
        for key in (v1, v2):
            assert key.startswith(key_path + "#")
            assert len(key.encode("utf-8")) <= SORT_KEY_MAX_BYTES

    def test_a_64_byte_version_id_and_a_32_byte_segment_key_on_a_maximal_path_fill_the_sort_key_limit(self):
        key = build_vector_file_version_key(_path_of_bytes(1024), "V" * 64, "s" * SEGMENT_KEY_MAX_BYTES)
        assert len(key.encode("utf-8")) == SORT_KEY_MAX_BYTES

    def test_a_whole_file_key_on_a_maximal_path_leaves_the_segment_room_unused(self):
        key = build_vector_file_version_key(_path_of_bytes(1024), "V" * 64)
        assert len(key.encode("utf-8")) == SORT_KEY_MAX_BYTES - 1 - SEGMENT_KEY_MAX_BYTES

    def test_a_key_one_byte_over_the_sort_key_limit_is_refused(self):
        """A version id longer than the 64 bytes the budget reserves pushes the key past 1024 bytes;
        the key of exactly 1024 bytes beside each refusal is the control."""
        path = _path_of_bytes(1024)  # a 926-byte key path
        segment = build_vector_file_version_key(path, "V" * 65, "s" * (SEGMENT_KEY_MAX_BYTES - 1))
        assert len(segment.encode("utf-8")) == SORT_KEY_MAX_BYTES
        with pytest.raises(ValueError):
            build_vector_file_version_key(path, "V" * 65, "s" * SEGMENT_KEY_MAX_BYTES)
        whole = build_vector_file_version_key(path, "V" * 97)
        assert len(whole.encode("utf-8")) == SORT_KEY_MAX_BYTES
        with pytest.raises(ValueError):
            build_vector_file_version_key(path, "V" * 98)

    def test_key_path_is_stable_across_versions(self):
        path = _path_of_bytes(1020)
        assert vector_file_key_path(path) == vector_file_key_path(path)

    def test_segment_key_is_appended_after_the_version(self):
        assert build_vector_file_version_key("/dir/clip.mp4", "v1", "t0000083456") == "/dir/clip.mp4#v1#t0000083456"

    def test_empty_segment_key_is_the_whole_file_key(self):
        assert build_vector_file_version_key("/dir/clip.mp4", "v1", "") == build_vector_file_version_key("/dir/clip.mp4", "v1")

    def test_absent_version_id_with_a_segment_key(self):
        assert build_vector_file_version_key("/dir/doc.pdf", None, "c000012") == "/dir/doc.pdf#null#c000012"

    def test_segment_key_at_the_cap_is_accepted_and_one_byte_over_is_refused(self):
        at_cap = "s" * SEGMENT_KEY_MAX_BYTES
        assert build_vector_file_version_key("/dir/clip.mp4", "v1", at_cap).endswith("#" + at_cap)
        with pytest.raises(ValueError):
            build_vector_file_version_key("/dir/clip.mp4", "v1", at_cap + "s")

    def test_segment_key_containing_the_separator_is_refused(self):
        with pytest.raises(ValueError):
            build_vector_file_version_key("/dir/clip.mp4", "v1", "t#1")

    def test_one_versions_segments_group_under_the_version_prefix(self):
        """``begins_with(SK, keyPath#)`` covers a file's whole-file and segment items of every version;
        ``begins_with(SK, keyPath#versionId#)`` covers exactly one version's segments -- not that
        version's whole-file item (no trailing ``#``) and nothing of another version. Chunk ordinals
        are 1-based, so the first chunk is ``c000001``."""
        path = "/dir/clip.mp4"
        whole_v1 = build_vector_file_version_key(path, "v1")
        segments_v1 = [
            build_vector_file_version_key(path, "v1", key)
            for key in (build_video_segment_key(0), build_video_segment_key(10_000), build_text_chunk_key(1))
        ]
        whole_v2 = build_vector_file_version_key(path, "v2")
        segments_v2 = [build_vector_file_version_key(path, "v2", build_video_segment_key(0))]
        everything = [whole_v1, *segments_v1, whole_v2, *segments_v2]
        file_prefix = vector_file_key_path(path) + "#"
        version_prefix = file_prefix + "v1#"
        assert all(key.startswith(file_prefix) for key in everything)
        assert [key for key in everything if key.startswith(version_prefix)] == segments_v1


@pytest.mark.unit
class TestKeyPathEncoding:
    """``%`` and ``#`` in a file path are percent-encoded in the key path, so the sort-key separator
    never occurs inside a path component and a prefix query selects exactly one file or one version."""

    def test_the_separator_is_encoded(self):
        assert vector_file_key_path("/dir/a#b.glb") == "/dir/a%23b.glb"

    def test_the_escape_character_is_encoded_first_so_a_literal_escape_sequence_stays_distinct(self):
        assert vector_file_key_path("/dir/a%b.glb") == "/dir/a%25b.glb"
        assert vector_file_key_path("/dir/a%23b.glb") == "/dir/a%2523b.glb"
        assert vector_file_key_path("/dir/a%23b.glb") != vector_file_key_path("/dir/a#b.glb")

    @pytest.mark.parametrize("path", ["/dir/a#b.glb", "/dir/a%b.glb", "/dir/a%23b.glb", "/dir/100%#1.glb"])
    def test_a_short_key_path_round_trips_through_percent_decoding(self, path):
        assert unquote(vector_file_key_path(path)) == path

    def test_a_file_named_like_a_sibling_key_cannot_match_the_siblings_version_prefix(self):
        """Unversioned bucket, files ``/a`` and ``/a#null``: the second file's whole-file key must fall
        neither under ``begins_with(SK, "/a#null#")``, the version prefix of the first file, nor under
        ``begins_with(SK, "/a#")``, the first file's own prefix."""
        first = build_vector_file_version_key("/a", None)
        lookalike = build_vector_file_version_key("/a#null", None)
        assert first == "/a#null"
        assert lookalike == "/a%23null#null"
        file_prefix = vector_file_key_path("/a") + "#"
        version_prefix = file_prefix + "null#"
        assert first.startswith(file_prefix)
        assert not lookalike.startswith(file_prefix)
        assert not lookalike.startswith(version_prefix)

    def test_the_digest_form_slices_the_encoded_path_and_digests_the_plain_one(self):
        """A path whose plain form fits the budget but whose encoded form does not is shortened: the
        prefix is a slice of the ENCODED bytes, so it carries no separator, and the digest is of the
        plain path (spec §3.4). The one ``#`` of a shortened key path sits in front of the digest."""
        path = "/" + "#" * 400 + "/model.glb"  # 411 plain bytes, 1211 encoded
        assert len(path.encode("utf-8")) <= FILE_PATH_KEY_BUDGET
        key_path = vector_file_key_path(path)
        assert len(key_path.encode("utf-8")) == FILE_PATH_KEY_BUDGET
        prefix, _, digest = key_path.rpartition("#")
        assert "#" not in prefix
        encoded = ("/" + "%23" * 400 + "/model.glb").encode("utf-8")
        assert prefix == encoded[: FILE_PATH_KEY_BUDGET - 64 - 1].decode("utf-8")
        assert digest == hashlib.sha256(path.encode("utf-8")).hexdigest()

    def test_the_budget_is_measured_on_the_encoded_form(self):
        """A path of exactly the budget in encoded bytes is kept whole; one more byte is shortened."""
        at_budget = "/" + "#" * 100 + "d" * 625  # 1 + 300 + 625 = 926 encoded bytes, 726 plain
        assert vector_file_key_path(at_budget) == "/" + "%23" * 100 + "d" * 625
        assert len(vector_file_key_path(at_budget).encode("utf-8")) == FILE_PATH_KEY_BUDGET
        over = at_budget + "d"
        shortened = vector_file_key_path(over)
        assert len(shortened.encode("utf-8")) == FILE_PATH_KEY_BUDGET
        assert shortened.endswith("#" + hashlib.sha256(over.encode("utf-8")).hexdigest())


@pytest.mark.unit
class TestSegmentKeys:
    def test_video_segment_key_is_t_plus_ten_zero_padded_digits(self):
        assert build_video_segment_key(83_456) == "t0000083456"
        assert build_video_segment_key(0) == "t0000000000"

    def test_video_segment_keys_sort_by_start_time(self):
        starts = [100_000, 5_000, 83_456, 0]
        assert sorted(build_video_segment_key(s) for s in starts) == [build_video_segment_key(s) for s in sorted(starts)]

    def test_negative_start_is_refused(self):
        with pytest.raises(ValueError):
            build_video_segment_key(-1)

    def test_start_wider_than_ten_digits_is_refused(self):
        build_video_segment_key(10**10 - 1)  # control: the widest ten-digit value is accepted
        with pytest.raises(ValueError):
            build_video_segment_key(10**10)

    def test_text_chunk_key_is_c_plus_six_zero_padded_digits(self):
        """Chunk ordinals are 1-based: ``c000012`` is chunk 12 of the label and ``c000001`` the first."""
        assert build_text_chunk_key(12) == "c000012"
        assert build_text_chunk_key(1) == "c000001"

    def test_text_chunk_keys_sort_by_index(self):
        indexes = [200, 7, 1_000, 1]
        assert sorted(build_text_chunk_key(i) for i in indexes) == [build_text_chunk_key(i) for i in sorted(indexes)]

    def test_negative_chunk_index_is_refused(self):
        with pytest.raises(ValueError):
            build_text_chunk_key(-1)

    def test_chunk_index_wider_than_six_digits_is_refused(self):
        build_text_chunk_key(10**6 - 1)  # control: the widest six-digit value is accepted
        with pytest.raises(ValueError):
            build_text_chunk_key(10**6)

    def test_segment_keys_fit_the_cap_and_carry_no_separator(self):
        for key in (build_video_segment_key(10**10 - 1), build_text_chunk_key(10**6 - 1)):
            assert len(key.encode("utf-8")) <= SEGMENT_KEY_MAX_BYTES
            assert "#" not in key
