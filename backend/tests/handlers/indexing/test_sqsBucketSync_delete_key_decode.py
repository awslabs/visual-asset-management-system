# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-016: the S3 delete path must decode the event object key.

S3 event notifications form-encode object keys (space -> '+', other specials ->
'%XX'), so a delete of ``my file name.glb`` arrives as ``my+file%20name.glb``.
``process_s3_record`` (the create path) runs the event key through
``decode_s3_event_key``; ``lambda_handler_deleted`` did not. The undecoded key was
used both to derive the asset-relative metadata path -- leaving the real file's
metadata and attribute rows orphaned, ready to resurrect onto a re-uploaded file of
the same name -- and to query S3 for remaining versions, where an encoding mismatch
reads as "no versions remain" and destroys the metadata of a merely ARCHIVED file.

Over-deletion is the expensive direction, so the archive cases are asserted as
controls. ``is_object_permanently_deleted`` fails closed on error by design (a delete
marker over live versions must preserve metadata so unarchive restores the file
intact), and a literal '+' in a filename is common in VAMS
(``BACC66K41F158AM+---.CATPart``): decoding one of those turns the '+' into a space,
so the permanence check has to clear BOTH spellings before anything is deleted.

The decode must also stay in a local. The same record dict is forwarded to the file
indexer, which applies its own ``unquote_plus`` (fileIndexer.py:1079) -- a mutated
key double-decodes there and the indexer removes the wrong document, and
``build_filtered_event`` matches SQS/SNS-wrapped records by dict equality against the
re-parsed body, so a mutated record is dropped from the forwarded event entirely.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

# _load() caches the module across test files, so every attribute replaced here must
# be restored or the mock leaks into the other sqsBucketSync suites.
_PATCHED_ATTRS = (
    "asset_bucket_prefix", "RESERVED_S3_PREFIX_FOLDERS", "get_bucket_id",
    "validate_asset_id", "lookup_asset", "update_asset_type",
    "delete_file_metadata_on_s3_delete", "is_object_permanently_deleted",
    "publish_to_file_indexer_sns", "s3_client",
    "asset_file_metadata_table", "file_attribute_table",
)

# 'my file name.glb' as an S3 event notification delivers it.
ENCODED_KEY = "assets/a1/my+file%20name.glb"
DECODED_KEY = "assets/a1/my file name.glb"
# A filename with a LITERAL '+', delivered unencoded (decode_s3_event_key's own
# docstring cites this shape). Decoding it yields a space form that is not a real key.
LITERAL_PLUS_KEY = "assets/a1/BACC66K41F158AM+---.CATPart"
LITERAL_PLUS_DECODED = "assets/a1/BACC66K41F158AM ---.CATPart"
PLAIN_KEY = "assets/a1/plain.glb"


@pytest.fixture(autouse=True)
def _restore_module_attrs():
    m = _load()
    saved = {name: getattr(m, name) for name in _PATCHED_ATTRS}
    yield
    for name, value in saved.items():
        setattr(m, name, value)


def _record(key):
    return {
        "eventSource": "aws:s3",
        "eventName": "ObjectRemoved:Delete",
        "s3": {"bucket": {"name": "asset-bucket"}, "object": {"key": key}},
    }


def _event(key):
    return {"Records": [_record(key)]}


def _wire(m):
    """Wire the module so lambda_handler_deleted reaches metadata cleanup.

    extract_asset_id_from_key and extract_relative_file_path stay REAL so the path
    the handler derives from the event key is the one under assertion.
    """
    m.asset_bucket_prefix = "assets/"
    m.RESERVED_S3_PREFIX_FOLDERS = set()
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value={"databaseId": "db1", "assetId": "a1"})
    m.update_asset_type = MagicMock(return_value=True)
    m.publish_to_file_indexer_sns = MagicMock()
    m.s3_client = MagicMock()
    return m


def _observe_deleter(m):
    """Replace delete_file_metadata_on_s3_delete so the paths handed to it are observable."""
    m.delete_file_metadata_on_s3_delete = MagicMock()
    return m


def _fake_metadata_tables(m):
    """Let the REAL delete_file_metadata_on_s3_delete run against stand-in tables.

    Each table reports one row for whatever composite key is queried, so the key the
    handler derived is observable on the resulting delete_item call -- the assertion
    is about rows disappearing, not about a helper being called.
    """
    m.asset_file_metadata_table = MagicMock()
    m.asset_file_metadata_table.query.return_value = {"Items": [{"metadataKey": "m1"}]}
    m.file_attribute_table = MagicMock()
    m.file_attribute_table.query.return_value = {"Items": [{"attributeKey": "a1"}]}


def _live_versions_for(*live_keys):
    """A list_object_versions stub where only `live_keys` still have versions.

    Mirrors S3: the object exists under exactly one key spelling, so querying with
    the wrong spelling returns nothing -- which is_object_permanently_deleted reads
    as "no versions remain". Live keys report the ARCHIVE shape: a delete marker as
    the latest version over a still-live version.
    """
    def _list(Bucket, Prefix, MaxKeys=None, **kwargs):
        if Prefix in live_keys:
            return {
                "Versions": [{"Key": Prefix, "VersionId": "v1", "IsLatest": False}],
                "DeleteMarkers": [{"Key": Prefix, "VersionId": "dm1", "IsLatest": True}],
            }
        return {}
    return _list


def _probed_keys(m):
    """Key spellings the permanence check asked S3 about."""
    return [call.kwargs["Prefix"] for call in m.s3_client.list_object_versions.call_args_list]


def _deleted_relative_paths(m):
    """Asset-relative paths handed to delete_file_metadata_on_s3_delete, leading-slash
    normalized (backend Rule 13) so the assertion is about the decode, not the slash."""
    return ["/" + call.args[2].lstrip("/")
            for call in m.delete_file_metadata_on_s3_delete.call_args_list]


def _deleted_composite_keys(table):
    """Composite keys of the rows actually removed from a stand-in table."""
    return [call.kwargs["Key"]["databaseId:assetId:filePath"]
            for call in table.delete_item.call_args_list]


@pytest.mark.unit
class TestDeletePathDecodesEventKey:
    """The missing decode: metadata must be cleared for the real (decoded) path."""

    def test_metadata_deleted_for_the_decoded_path(self):
        """A permanent delete of an encoded key clears the DECODED asset-relative path."""
        m = _observe_deleter(_wire(_load()))
        m.s3_client.list_object_versions.side_effect = _live_versions_for()  # nothing live

        m.lambda_handler_deleted(_event(ENCODED_KEY), MagicMock())

        deleted = _deleted_relative_paths(m)
        assert "/my file name.glb" in deleted, (
            f"metadata was deleted for {deleted}; the rows for the real file "
            f"'/my file name.glb' stay orphaned"
        )
        # Only the two spellings the event can legitimately refer to are touched --
        # no third, re-encoded or mangled path.
        assert set(deleted) <= {"/my file name.glb", "/my+file%20name.glb"}

    def test_permanence_check_asks_s3_about_the_decoded_key(self):
        """The S3 version lookup must use the decoded key, not only the raw one.

        Probing only the encoded spelling finds no versions for a file that is merely
        archived, which is how the undecoded key destroyed recoverable metadata.
        """
        m = _observe_deleter(_wire(_load()))
        m.s3_client.list_object_versions.side_effect = _live_versions_for()

        m.lambda_handler_deleted(_event(ENCODED_KEY), MagicMock())

        assert DECODED_KEY in _probed_keys(m)

    def test_metadata_rows_for_the_decoded_path_are_gone(self):
        """End-to-end through the real deleter: the rows removed are the decoded file's.

        Proves the composite key ('{databaseId}:{assetId}:/{path}') built from the
        event key names the real file, not its encoded spelling.
        """
        m = _wire(_load())
        _fake_metadata_tables(m)
        m.s3_client.list_object_versions.side_effect = _live_versions_for()

        m.lambda_handler_deleted(_event(ENCODED_KEY), MagicMock())

        assert "db1:a1:/my file name.glb" in _deleted_composite_keys(m.asset_file_metadata_table)
        assert "db1:a1:/my file name.glb" in _deleted_composite_keys(m.file_attribute_table)

    def test_unencoded_key_still_deletes_its_own_path(self):
        """Control: a key needing no decoding keeps resolving to the same path.

        Without this, a test that only feeds an encoded key cannot tell a working
        decode from one that mangles every key.
        """
        m = _observe_deleter(_wire(_load()))
        m.s3_client.list_object_versions.side_effect = _live_versions_for()

        m.lambda_handler_deleted(_event(PLAIN_KEY), MagicMock())

        assert _deleted_relative_paths(m) == ["/plain.glb"]
        # Raw and decoded coincide, so S3 is asked once.
        assert _probed_keys(m) == [PLAIN_KEY]


@pytest.mark.unit
class TestArchivedFileMetadataPreserved:
    """Over-deletion controls -- metadata must survive an archive.

    is_object_permanently_deleted distinguishes a permanent delete from an archive
    (delete marker over live versions). Reading it with the wrong key spelling, or
    loosening its fail-closed default, silently destroys the metadata of a file that
    unarchive will restore.
    """

    def test_encoded_key_archive_preserves_metadata(self):
        """NEGATIVE CONTROL: an archived file whose key arrived encoded keeps its metadata."""
        m = _observe_deleter(_wire(_load()))
        # The object lives in S3 under the DECODED key with a delete marker over a
        # live version -- the archive shape.
        m.s3_client.list_object_versions.side_effect = _live_versions_for(DECODED_KEY)

        m.lambda_handler_deleted(_event(ENCODED_KEY), MagicMock())

        m.delete_file_metadata_on_s3_delete.assert_not_called()

    def test_no_metadata_rows_touched_when_archived(self):
        """NEGATIVE CONTROL through the real deleter: no row is removed for an archive."""
        m = _wire(_load())
        _fake_metadata_tables(m)
        m.s3_client.list_object_versions.side_effect = _live_versions_for(DECODED_KEY)

        m.lambda_handler_deleted(_event(ENCODED_KEY), MagicMock())

        m.asset_file_metadata_table.delete_item.assert_not_called()
        m.file_attribute_table.delete_item.assert_not_called()

    def test_literal_plus_archive_preserves_metadata(self):
        """Control: a literal '+' filename that is merely archived keeps its metadata.

        decode_s3_event_key returns the unquote_plus form whenever it differs, which
        turns a literal '+' into a space. Clearing metadata on the decoded spelling
        alone would delete the rows of a file that still exists under the '+'.
        """
        m = _observe_deleter(_wire(_load()))
        m.s3_client.list_object_versions.side_effect = _live_versions_for(LITERAL_PLUS_KEY)

        m.lambda_handler_deleted(_event(LITERAL_PLUS_KEY), MagicMock())

        m.delete_file_metadata_on_s3_delete.assert_not_called()
        # The raw spelling has to be probed for the preserve decision to be reachable.
        assert LITERAL_PLUS_KEY in _probed_keys(m)

    def test_both_key_forms_erroring_preserves_metadata(self):
        """Control: the fail-closed default survives the decoded/raw pairing.

        is_object_permanently_deleted returns False on ANY error by design, and a
        spelling reporting False short-circuits the rest. If the pairing flipped that
        -- treating an unreadable spelling as "gone" so the other one could decide --
        an S3 outage during an archive would silently destroy file metadata.
        """
        m = _observe_deleter(_wire(_load()))
        m.s3_client.list_object_versions.side_effect = RuntimeError("S3 unavailable")

        m.lambda_handler_deleted(_event(ENCODED_KEY), MagicMock())

        m.delete_file_metadata_on_s3_delete.assert_not_called()

    def test_permanent_delete_of_an_unencoded_key_still_deletes(self):
        """Control: proves the preserve assertions above are not vacuous.

        With no versions remaining, the metadata MUST be deleted -- otherwise
        'assert_not_called' would be satisfied by a wiring mistake rather than by the
        permanence check.
        """
        m = _observe_deleter(_wire(_load()))
        m.s3_client.list_object_versions.side_effect = _live_versions_for()  # nothing live

        m.lambda_handler_deleted(_event(PLAIN_KEY), MagicMock())

        m.delete_file_metadata_on_s3_delete.assert_called_once()


@pytest.mark.unit
class TestLiteralPlusPermanentDelete:
    """A literal-'+' file that IS permanently gone still gets its rows cleaned.

    Neither spelling has versions left, so the raw ('+') spelling -- the real key
    when the event arrived unencoded -- must be cleaned too, otherwise the rows
    orphan exactly as they did before the decode.
    """

    def test_raw_plus_path_metadata_deleted(self):
        m = _observe_deleter(_wire(_load()))
        m.s3_client.list_object_versions.side_effect = _live_versions_for()  # nothing live

        m.lambda_handler_deleted(_event(LITERAL_PLUS_KEY), MagicMock())

        assert "/BACC66K41F158AM+---.CATPart" in _deleted_relative_paths(m)
        # Both spellings were cleared for a permanent delete, neither invented.
        assert set(_deleted_relative_paths(m)) == {
            "/BACC66K41F158AM+---.CATPart", "/BACC66K41F158AM ---.CATPart",
        }
        assert set(_probed_keys(m)) == {LITERAL_PLUS_KEY, LITERAL_PLUS_DECODED}


@pytest.mark.unit
class TestForwardedRecordKeepsRawKey:
    """The decode must go into a LOCAL, not into the record.

    The forwarded record reaches fileIndexer, which applies its own unquote_plus.
    Mutating record['s3']['object']['key'] to the decoded form double-decodes there:
    a literal '+' becomes a space and the indexer removes the wrong document (or
    none), and the Physna sync does the same.
    """

    @pytest.mark.parametrize("key", [ENCODED_KEY, LITERAL_PLUS_KEY, PLAIN_KEY])
    def test_indexer_receives_the_original_key_byte_for_byte(self, key):
        m = _observe_deleter(_wire(_load()))
        m.s3_client.list_object_versions.side_effect = _live_versions_for()
        event = _event(key)

        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()
        forwarded = m.publish_to_file_indexer_sns.call_args.args[0]
        assert forwarded["Records"][0]["s3"]["object"]["key"] == key
        # The record dict itself is untouched, so build_filtered_event's
        # dict-equality match against a re-parsed SQS/SNS body still finds it.
        assert event["Records"][0]["s3"]["object"]["key"] == key

    @pytest.mark.parametrize("key", [ENCODED_KEY, LITERAL_PLUS_KEY])
    def test_archived_file_is_still_forwarded_with_the_raw_key(self, key):
        """Preserving metadata must not withhold the record from the indexers."""
        m = _observe_deleter(_wire(_load()))
        m.s3_client.list_object_versions.side_effect = _live_versions_for(key)

        m.lambda_handler_deleted(_event(key), MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()
        forwarded = m.publish_to_file_indexer_sns.call_args.args[0]
        assert forwarded["Records"][0]["s3"]["object"]["key"] == key
