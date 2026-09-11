# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

# _load() caches the module across test files, so any attribute this file
# replaces with a mock must be restored after each test or the mock leaks
# into the other sqsBucketSync test suites.
_PATCHED_ATTRS = (
    "asset_bucket_name", "asset_bucket_prefix", "RESERVED_S3_PREFIX_FOLDERS",
    "get_bucket_id", "validate_asset_id", "lookup_asset",
    "delete_file_metadata_on_s3_delete", "update_asset_type",
    "publish_to_file_indexer_sns", "publish_to_orchestration_bus",
    "extract_asset_id_from_key", "parse_event", "process_s3_record",
    # Replaced by TestReservedSegmentIsPrefixAware. Omitting it left a MagicMock on the module
    # that _load() caches, so a LATER file asserting the real stamp behaviour saw the mock and
    # passed for the wrong reason.
    "update_s3_metadata",
)


@pytest.fixture(autouse=True)
def _restore_module_attrs():
    m = _load()
    saved = {name: getattr(m, name) for name in _PATCHED_ATTRS}
    yield
    for name, value in saved.items():
        setattr(m, name, value)


def _s3_delete_record(key="db/x-asset-1/file.glb", bucket="asset-bucket"):
    return {
        "eventSource": "aws:s3",
        "eventName": "ObjectRemoved:DeleteMarkerCreated",
        "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
    }


def _wire_delete_handler(m):
    """Common wiring so lambda_handler_deleted reaches per-record processing."""
    m.asset_bucket_name = "asset-bucket"
    m.asset_bucket_prefix = "db/"
    m.RESERVED_S3_PREFIX_FOLDERS = {"temp-uploads"}
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value={"databaseId": "db1", "assetId": "x-asset-1"})
    m.delete_file_metadata_on_s3_delete = MagicMock()
    m.update_asset_type = MagicMock(return_value=True)
    m.publish_to_file_indexer_sns = MagicMock()


@pytest.mark.unit
class TestDeleteHandlerForwardsToIndexers:
    """Deletes must reach the file indexer even when VAMS-side cleanup is skipped,
    otherwise OpenSearch and other registered indexers never remove their records."""

    def test_forwards_when_asset_record_missing(self):
        m = _load()
        _wire_delete_handler(m)
        m.lookup_asset = MagicMock(return_value=None)  # asset record already gone

        event = {"Records": [_s3_delete_record()]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()
        # No metadata cleanup attempted, but asset type update still runs
        m.delete_file_metadata_on_s3_delete.assert_not_called()

    def test_forwards_when_bucket_id_missing(self):
        m = _load()
        _wire_delete_handler(m)
        m.get_bucket_id = MagicMock(return_value=None)

        event = {"Records": [_s3_delete_record()]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()

    def test_forwards_when_cleanup_raises(self):
        m = _load()
        _wire_delete_handler(m)
        m.update_asset_type = MagicMock(side_effect=RuntimeError("boom"))

        event = {"Records": [_s3_delete_record()]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()

    def test_one_bad_record_does_not_block_others(self):
        # A cleanup exception on the first record must not prevent the second
        # record from being processed and forwarded.
        m = _load()
        _wire_delete_handler(m)
        m.update_asset_type = MagicMock(side_effect=[RuntimeError("boom"), True])

        event = {"Records": [
            _s3_delete_record(key="db/x-asset-1/a.glb"),
            _s3_delete_record(key="db/x-asset-1/b.glb"),
        ]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()
        published_event = m.publish_to_file_indexer_sns.call_args.args[0]
        assert len(published_event["Records"]) == 2

    def test_folder_markers_and_init_files_not_forwarded(self):
        m = _load()
        _wire_delete_handler(m)

        event = {"Records": [
            _s3_delete_record(key="db/x-asset-1/folder/"),
            _s3_delete_record(key="db/x-asset-1/init"),
        ]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_not_called()

    def test_reserved_folder_not_forwarded(self):
        m = _load()
        _wire_delete_handler(m)
        m.extract_asset_id_from_key = MagicMock(return_value="temp-uploads")

        event = {"Records": [_s3_delete_record(key="db/temp-uploads/file.glb")]}
        m.lambda_handler_deleted(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_not_called()


@pytest.mark.unit
class TestCreatedHandlerForwardsToIndexers:
    """Created events flagged for indexing must be published even when other
    records in the batch hard-errored."""

    def test_publishes_indexable_records_despite_hard_errors(self):
        m = _load()
        m.parse_event = MagicMock(side_effect=lambda e: e)
        record_ok = {"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"}, "object": {"key": "db/a1/ok.glb"}}}
        record_bad = {"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"}, "object": {"key": "db/a2/bad.glb"}}}
        # First record indexes fine; second hard-errors but is still flagged
        # for indexing (should_index=True on failure paths).
        m.process_s3_record = MagicMock(side_effect=[
            (True, True, "Successfully processed db/a1/ok.glb"),
            (False, True, "Failed to update metadata for db/a2/bad.glb"),
        ])
        m.publish_to_file_indexer_sns = MagicMock()
        m.publish_to_orchestration_bus = MagicMock()

        event = {"Records": [record_ok, record_bad]}
        m.lambda_handler_created(event, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()
        published_event = m.publish_to_file_indexer_sns.call_args.args[0]
        assert len(published_event["Records"]) == 2

    def test_stale_create_event_still_forwarded(self):
        # process_s3_record returns (True, True, ...) for a stale create whose
        # object is gone — the record must reach the indexers for reconciliation.
        m = _load()
        m.parse_event = MagicMock(side_effect=lambda e: e)
        record = {"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"}, "object": {"key": "db/a1/gone.glb"}}}
        m.process_s3_record = MagicMock(return_value=(True, True, "Skipped stale create event for db/a1/gone.glb"))
        m.publish_to_file_indexer_sns = MagicMock()
        m.publish_to_orchestration_bus = MagicMock()

        m.lambda_handler_created({"Records": [record]}, MagicMock())

        m.publish_to_file_indexer_sns.assert_called_once()

    def test_records_not_flagged_for_indexing_are_withheld(self):
        m = _load()
        m.parse_event = MagicMock(side_effect=lambda e: e)
        record = {"eventSource": "aws:s3", "s3": {"bucket": {"name": "b"}, "object": {"key": "db/a1/folder/"}}}
        m.process_s3_record = MagicMock(return_value=(True, False, "Processed folder marker db/a1/folder/"))
        m.publish_to_file_indexer_sns = MagicMock()
        m.publish_to_orchestration_bus = MagicMock()

        m.lambda_handler_created({"Records": [record]}, MagicMock())

        m.publish_to_file_indexer_sns.assert_not_called()
        m.publish_to_orchestration_bus.assert_not_called()


@pytest.mark.unit
class TestReservedPipelinePrefixIsExcluded:
    """A key under the reserved `pipelines/` prefix reaches neither the indexers nor the trigger bus.

    Pipeline run I/O is written INTO the default asset bucket -- outputs under
    `pipelines/{pipelineName}/{jobName}/output/{executionId}/...` and execution inputs under
    `pipelines/workflowExecutionInputs/{executionId}/` (see s3PathPatterns.PIPELINES_PREFIX). That
    bucket's `s3:ObjectCreated:*` notification feeds this handler, so every pipeline write arrives here
    and must be excluded, or a workflow's own output would be ingested as an asset file AND republished
    as `asset.file.uploaded` -- which is the fileUpload trigger's input.

    The pre-existing reserved-folder test covers `temp-uploads` on the DELETE handler only. These add
    the CREATED handler and the `pipelines` segment specifically, which is the prefix pipeline output
    actually uses, and assert BOTH sinks rather than just the indexer: the trigger bus is the one that
    could re-enter the pipeline that produced the file.
    """

    def _created_record(self, key):
        return {
            "eventSource": "aws:s3",
            "eventName": "ObjectCreated:Put",
            "s3": {"bucket": {"name": "asset-bucket"}, "object": {"key": key}},
        }

    def _wire(self, m):
        m.parse_event = MagicMock(side_effect=lambda e: e)
        m.asset_bucket_name = "asset-bucket"
        m.asset_bucket_prefix = ""
        m.get_bucket_id = MagicMock(return_value="bucket-1")
        m.validate_asset_id = MagicMock(return_value=True)
        m.lookup_asset = MagicMock(return_value={"databaseId": "db1", "assetId": "a1"})
        m.update_asset_type = MagicMock(return_value=True)
        m.update_s3_metadata = MagicMock(return_value=True)
        m.publish_to_file_indexer_sns = MagicMock()
        m.publish_to_orchestration_bus = MagicMock()

    @pytest.mark.parametrize("key", [
        "pipelines/workflowExecutionInputs/7324c89972d748a8ae3204ce71ed8d3f/metadata.json",
        "pipelines/preview-3d-thumbnail/job-1/output/abc123/files/model.glb.previewFile.gif",
        "pipeline/legacy-singular/output/abc123/files/model.glb",
    ])
    def test_pipeline_prefixed_key_reaches_neither_sink(self, key):
        m = _load()
        self._wire(m)
        m.lambda_handler_created({"Records": [self._created_record(key)]}, MagicMock())
        m.publish_to_file_indexer_sns.assert_not_called()
        m.publish_to_orchestration_bus.assert_not_called()

    def test_an_ordinary_asset_file_DOES_reach_both_sinks(self):
        """Control. Without it the assertions above pass on a handler that forwards nothing at all --
        which is exactly what a mis-wired fixture produces."""
        m = _load()
        self._wire(m)
        m.lambda_handler_created(
            {"Records": [self._created_record("a1/model.glb")]}, MagicMock())
        m.publish_to_file_indexer_sns.assert_called_once()
        m.publish_to_orchestration_bus.assert_called_once()

    def test_the_reserved_set_still_carries_both_pipeline_spellings(self):
        """The skip is a membership test on a path segment, so the SET is the real contract. Stated
        here because a rename of the write prefix without a matching set entry would silently start
        ingesting pipeline output, and nothing else in this file would notice."""
        # Read from the SOURCE file rather than importing: `common` is replaced by tests/mocks in this
        # suite, so `import common.s3PathPatterns` yields a MagicMock whose membership test answers
        # False for everything and `backend.common` does not resolve at all. Parsing the module keeps
        # the assertion about the shipped constant.
        import ast
        import os

        source_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "..", "backend", "common", "s3PathPatterns.py")
        with open(os.path.abspath(source_path), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        values = {}
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) and not isinstance(node, ast.Assign):
                continue
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            name = next((t.id for t in targets if isinstance(t, ast.Name)), None)
            if name in ("RESERVED_S3_PREFIX_FOLDERS", "PIPELINES_PREFIX") and node.value is not None:
                values[name] = node.value

        reserved_node = values.get("RESERVED_S3_PREFIX_FOLDERS")
        assert reserved_node is not None, "RESERVED_S3_PREFIX_FOLDERS not found in s3PathPatterns.py"
        # frozenset({...}) — the literal is the call's first argument.
        literal = reserved_node.args[0] if isinstance(reserved_node, ast.Call) else reserved_node
        reserved = {e.value for e in literal.elts}
        assert "pipelines" in reserved
        assert "pipeline" in reserved

        prefix_node = values.get("PIPELINES_PREFIX")
        assert prefix_node is not None, "PIPELINES_PREFIX not found in s3PathPatterns.py"
        # The prefix handlers actually WRITE to must be the one the set covers.
        assert prefix_node.value.strip("/").split("/")[0] in reserved


@pytest.mark.unit
class TestReservedSegmentIsPrefixAware:
    """A reserved segment is recognised wherever it sits, and whatever the bucket's prefix is.

    The check used to test only the EXTRACTED ASSET ID -- the first path segment after the configured
    `baseAssetsPrefix`. That is narrower than the contract `s3PathPatterns` documents ("any S3 key with
    one of these as a path segment is system data") and narrower than the two consumers downstream of
    this handler: `fileIndexer` and `workflowTriggerDispatch` both walk every segment. So bucket sync
    was the most permissive link in the chain.

    Two shapes it missed, both reachable once a bucket carries a non-root prefix:

      * a reserved folder BELOW an asset id (`{prefix}/{assetId}/preview/...`) -- the first segment is a
        real asset id, so the narrow test saw an ordinary file;
      * a key that does not start with the configured prefix -- `extract_asset_id_from_key` returns None
        there, so the narrow test never ran and the record fell through the "asset id unresolvable"
        branch instead of the system-data branch.

    Why it matters beyond wasted work: `update_s3_metadata` re-stamps anything it treats as an asset
    file by copying the object ONTO ITSELF, and that copy re-enters the same `s3:ObjectCreated:*`
    notification that triggered the handler. So mis-classifying system data adds an S3 write, another
    event, and another hop of the lineage depth AWS's recursive-loop detection counts.
    """

    def _created_record(self, key, bucket="asset-bucket"):
        return {
            "eventSource": "aws:s3",
            "eventName": "ObjectCreated:Put",
            "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
        }

    def _wire(self, m, prefix):
        m.parse_event = MagicMock(side_effect=lambda e: e)
        m.asset_bucket_name = "asset-bucket"
        m.asset_bucket_prefix = prefix
        m.get_bucket_id = MagicMock(return_value="bucket-1")
        m.validate_asset_id = MagicMock(return_value=True)
        m.lookup_asset = MagicMock(return_value={"databaseId": "db1", "assetId": "a1"})
        m.update_asset_type = MagicMock(return_value=True)
        m.update_s3_metadata = MagicMock(return_value=True)
        m.publish_to_file_indexer_sns = MagicMock()
        m.publish_to_orchestration_bus = MagicMock()

    @pytest.mark.parametrize("prefix,key,label", [
        # Pipeline run I/O under a bucket whose VAMS area is NOT the root.
        ("myprefix/", "myprefix/pipelines/wf/output/e1/files/model.glb", "prefixed pipelines"),
        # A reserved folder nested BELOW an asset id — invisible to a first-segment test.
        ("myprefix/", "myprefix/a1/preview/PotreeViewer/octree.bin", "reserved below the asset id"),
        ("", "a1/preview/PotreeViewer/octree.bin", "reserved below the asset id, root bucket"),
        # A key that does not line up with the configured prefix at all.
        ("myprefix/", "pipelines/wf/output/e1/files/model.glb", "key misaligned with the prefix"),
        # Singular spellings are reserved too.
        ("myprefix/", "myprefix/a1/workspace/scratch.bin", "singular workspace"),
    ])
    def test_reserved_segment_reaches_neither_sink(self, prefix, key, label):
        m = _load()
        self._wire(m, prefix)
        m.lambda_handler_created({"Records": [self._created_record(key)]}, MagicMock())
        m.publish_to_file_indexer_sns.assert_not_called(), label
        m.publish_to_orchestration_bus.assert_not_called(), label
        # And it is never re-stamped, which is the write that re-enters the notification.
        m.update_s3_metadata.assert_not_called(), label

    @pytest.mark.parametrize("prefix,key", [
        ("myprefix/", "myprefix/a1/model.glb"),
        ("", "a1/model.glb"),
        # A segment that merely CONTAINS a reserved name is not reserved.
        ("myprefix/", "myprefix/a1/pipelinesdata/model.glb"),
    ])
    def test_an_ordinary_file_still_reaches_both_sinks(self, prefix, key):
        """Control. Without it every assertion above is satisfied by a handler that forwards nothing,
        and the substring case guards against the segment test being loosened into a substring test."""
        m = _load()
        self._wire(m, prefix)
        m.lambda_handler_created({"Records": [self._created_record(key)]}, MagicMock())
        m.publish_to_file_indexer_sns.assert_called_once()
        m.publish_to_orchestration_bus.assert_called_once()

    def test_bucket_sync_agrees_with_its_downstream_consumers(self):
        """The three implementations must answer the same question.

        Stated as a property over the SAME keys rather than by reading each call site, because the
        defect was precisely that they disagreed and nothing noticed. `fileIndexer` and
        `workflowTriggerDispatch` walk every segment; this asserts the shared helper does too.
        """
        from common.s3PathPatterns import RESERVED_S3_PREFIX_FOLDERS, key_has_reserved_segment

        def downstream_says_reserved(s3_key):
            # The exact form fileIndexer and workflowTriggerDispatch use.
            return any(part in RESERVED_S3_PREFIX_FOLDERS for part in s3_key.split("/"))

        keys = [
            "pipelines/wf/output/e1/f.glb",
            "myprefix/pipelines/wf/output/e1/f.glb",
            "myprefix/a1/preview/PotreeViewer/octree.bin",
            "a1/model.glb",
            "myprefix/a1/model.glb",
            "myprefix/a1/pipelinesdata/model.glb",
            "a1/temp-upload/part.bin",
        ]
        disagreements = [
            k for k in keys
            if key_has_reserved_segment(k, "myprefix/") != downstream_says_reserved(k)
        ]
        assert disagreements == [], (
            "bucket sync and its downstream consumers disagree on these keys, which is how system "
            f"data reaches one and not the other: {disagreements}")
