# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Loads the vectorsearch handlers by file path with the real vector/indexing modules bound.

The root conftest registers a MagicMock `common` package and a mock `common.indexing` package, so a plain
import of a handler yields mocks. Handlers are loaded by path (`backend/tests/CLAUDE.md`), and the shared
modules they import from `common.indexing` / `common.vectorsearch` are bound to the REAL files first, so
the tests exercise the store/key contracts the registry names rather than a mock attribute.

`boto3.resource(...)` is one MagicMock for every table, so `dynamodb.Table(a)` and `dynamodb.Table(b)` are
the SAME child mock; fixtures replace each table attribute on the loaded module with its own MagicMock so a
stubbed `get_item` on the asset table cannot answer a workflow-table read.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
_HANDLERS = os.path.join(_BACKEND, "handlers", "vectorsearch")

os.environ.setdefault("VECTOR_EMBEDDINGS_STORAGE_TABLE_NAME", "test-vector-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_V2_NAME", "test-workflow-table-v2")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_V2_NAME", "test-pipeline-table-v2")
os.environ.setdefault("WORKFLOW_TRIGGERS_STORAGE_TABLE_NAME", "test-workflow-triggers-table")
os.environ.setdefault("VECTOR_INDEX_NAME", "vec-test-4")
os.environ.setdefault("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
os.environ.setdefault("EMBEDDING_DIMENSIONS", "4")
os.environ.setdefault("AUX_BUCKET_NAME", "test-aux-bucket")
os.environ.setdefault("VECTOR_INDEXER_QUEUE_URL",
                      "https://sqs.us-east-1.amazonaws.com/123456789012/indexer-queue")
os.environ.setdefault("SYSTEM_WORKFLOW_LAUNCH_QUEUE_URL",
                      "https://sqs.us-east-1.amazonaws.com/123456789012/launch-queue")
os.environ.setdefault("SYSTEM_GENAI_WORKFLOW_ID", "system-genai-metadata")
os.environ.setdefault("SYSTEM_GENAI_WORKFLOW_DATABASE_ID", "GLOBAL")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "executeWorkflowV2-test")
os.environ.setdefault("AWS_LAMBDA_FUNCTION_NAME", "vectorReindexer-test")

_REAL_MODULES = {
    "common.indexing.documentIds": os.path.join(_BACKEND, "common", "indexing", "documentIds.py"),
    "common.indexing.fileEnumeration": os.path.join(_BACKEND, "common", "indexing", "fileEnumeration.py"),
    "common.vectorsearch.embeddings": os.path.join(_BACKEND, "common", "vectorsearch", "embeddings.py"),
    "common.vectorsearch.vectorStore": os.path.join(_BACKEND, "common", "vectorsearch", "vectorStore.py"),
}


def bind_real_modules():
    """Register the real shared modules the handlers import, once per process."""
    if "common.vectorsearch" not in sys.modules:
        package = types.ModuleType("common.vectorsearch")
        package.__path__ = [os.path.join(_BACKEND, "common", "vectorsearch")]
        sys.modules["common.vectorsearch"] = package
        sys.modules["common"].vectorsearch = package
    for name, file_path in _REAL_MODULES.items():
        current = sys.modules.get(name)
        if current is not None and os.path.abspath(getattr(current, "__file__", "") or "") == file_path:
            continue
        spec = importlib.util.spec_from_file_location(name, file_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)


def load_handler(module_name, boto_client_factory=None):
    """The real handler module loaded by file path with boto3 patched.

    `boto_client_factory(service_name, *args, **kwargs)` supplies the client per service; the default
    returns a fresh MagicMock for every service.
    """
    bind_real_modules()
    factory = boto_client_factory or (lambda name, *args, **kwargs: MagicMock())
    handler_path = os.path.join(_HANDLERS, f"{module_name}.py")
    with patch("boto3.client", side_effect=factory), patch("boto3.resource", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location(
            f"vectorsearch_{module_name}_under_test", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    assert os.path.abspath(module.__file__) == os.path.abspath(handler_path)
    return module


# A video-window segment key (spec §3.4: `t` + a 10-digit start millisecond); the segment fixtures of the
# lifecycle tests hang one of these under the whole-file item.
SEGMENT_KEY = "t0000083456"


def seeded_item(pk, file_version_key, version_id, is_latest=True, is_archived=False,
                segment_kind="none", pipeline_execution_id="pe-1"):
    """A stored vector item reduced to the attributes the lifecycle rules read (registry §3.2 names).

    The two flags are the bools `VectorItem` carries (registry §3.3); their `"true"`/`"false"` wire form is
    the store's to write and the Stubber contract tests assert it, so nothing in this fake spells a flag as
    a string.
    """
    return {
        "databaseId:assetId": pk,
        "fileVersionKey": file_version_key,
        "versionId": version_id,
        "isLatest": is_latest,
        "isArchived": is_archived,
        "segmentKind": segment_kind,
        "pipelineExecutionId": pipeline_execution_id,
    }


class FakeVectorStore:
    """Records every store call; the store contract itself is WP00's to test.

    `seed` is an optional list of stored items (`seeded_item`). The per-file and per-asset methods apply
    the registry §3.3 reach to it — `begins_with(fileVersionKey, key_path + "#")` for a file, the whole
    partition for an asset — and return the number of items they changed, so a test can show WHICH items
    a call reaches (a segment item `…#v1#t0000083456` sits under its file's prefix) without asserting the
    store's expression text. Unseeded, the methods record the call and return 0.

    The asset- and file-wide methods take the registry §3.3 keywords (`start_key`, `time_remaining_fn`,
    `min_remaining_ms`) and return `(count, next_key)`. `hand_off` maps a method name to the `next_key` it
    returns on its FIRST page (a call with `start_key=None`); the entry is consumed, so the resumed call
    completes. Every paging call is recorded in `paging` as `(method, start_key, time_remaining_fn)` beside
    the positional `calls` tuple, so a test can show a rule resumed from the key it was handed and passed
    the invocation's time budget through.

    `scan_pages` maps a start key (None for the first page) to `(keys, next_key)`, given as a list of
    `(start_key, (keys, next_key))` pairs since a DynamoDB key is a dict and cannot itself key a dict;
    a continuation that resumes from a cursor it was never handed raises instead of serving page one twice.
    """

    def __init__(self, scan_pages=None, seed=None, hand_off=None):
        self.calls = []
        self.items = []
        self.paging = []
        self.seeded = [dict(item) for item in (seed or [])]
        self._hand_off = dict(hand_off or {})
        pairs = scan_pages.items() if isinstance(scan_pages, dict) else (scan_pages or [])
        self._scan_pages = {json.dumps(k, sort_keys=True): v for k, v in pairs}

    def _file_items(self, pk, key_path):
        prefix = key_path + "#"
        return [item for item in self.seeded
                if item["databaseId:assetId"] == pk and item["fileVersionKey"].startswith(prefix)]

    def _asset_items(self, pk):
        return [item for item in self.seeded if item["databaseId:assetId"] == pk]

    @staticmethod
    def _flip(items, attribute, target):
        changed = [item for item in items if item.get(attribute) != target]
        for item in changed:
            item[attribute] = target
        return len(changed)

    def _remove(self, doomed):
        self.seeded = [item for item in self.seeded if item not in doomed]
        return len(doomed)

    def _paged(self, method, count, start_key, time_remaining_fn):
        """`(count, next_key)` for an asset- or file-wide method: the configured hand-off key on the first
        page of a method the test told to hand off, else None."""
        self.paging.append((method, start_key, time_remaining_fn))
        next_key = self._hand_off.pop(method, None) if start_key is None else None
        return count, next_key

    def put_item(self, item):
        self.calls.append(("put_item", item))
        self.items.append(item)

    def put_latest_item(self, item):
        self.calls.append(("put_latest_item", item))
        self.items.append(item)
        return 1

    def delete_other_run_segments(self, pk, key_path, version_id, pipeline_execution_id):
        self.calls.append(("delete_other_run_segments", pk, key_path, version_id, pipeline_execution_id))
        prefix = f"{key_path}#{version_id}#"
        doomed = [item for item in self._asset_items(pk)
                  if item["fileVersionKey"].startswith(prefix)
                  and item.get("pipelineExecutionId") != pipeline_execution_id]
        return self._remove(doomed)

    def set_archived_for_file(self, pk, key_path, archived, *, start_key=None, time_remaining_fn=None,
                              min_remaining_ms=60_000):
        self.calls.append(("set_archived_for_file", pk, key_path, archived))
        changed = self._flip(self._file_items(pk, key_path), "isArchived", archived)
        return self._paged("set_archived_for_file", changed, start_key, time_remaining_fn)

    def set_archived_for_asset(self, pk, archived, *, start_key=None, time_remaining_fn=None,
                               min_remaining_ms=60_000):
        self.calls.append(("set_archived_for_asset", pk, archived))
        changed = self._flip(self._asset_items(pk), "isArchived", archived)
        return self._paged("set_archived_for_asset", changed, start_key, time_remaining_fn)

    def set_not_latest_for_file_except(self, pk, key_path, version_id, *, start_key=None,
                                       time_remaining_fn=None, min_remaining_ms=60_000):
        self.calls.append(("set_not_latest_for_file_except", pk, key_path, version_id))
        others = [item for item in self._file_items(pk, key_path) if item.get("versionId") != version_id]
        changed = self._flip(others, "isLatest", False)
        return self._paged("set_not_latest_for_file_except", changed, start_key, time_remaining_fn)

    def delete_file(self, pk, key_path, *, start_key=None, time_remaining_fn=None, min_remaining_ms=60_000):
        self.calls.append(("delete_file", pk, key_path))
        removed = self._remove(self._file_items(pk, key_path))
        return self._paged("delete_file", removed, start_key, time_remaining_fn)

    def delete_asset(self, pk, *, start_key=None, time_remaining_fn=None, min_remaining_ms=60_000):
        self.calls.append(("delete_asset", pk))
        removed = self._remove(self._asset_items(pk))
        return self._paged("delete_asset", removed, start_key, time_remaining_fn)

    def scan_keys(self, start_key=None, limit=1000, pk_prefix=None):
        self.calls.append(("scan_keys", start_key, limit, pk_prefix))
        cursor = json.dumps(start_key, sort_keys=True)
        if cursor not in self._scan_pages:
            raise AssertionError(f"scan resumed from a cursor it was never handed: {start_key!r}")
        keys, next_key = self._scan_pages[cursor]
        if pk_prefix is not None:
            # A FilterExpression narrows the page after the read; the page cursor still walks the whole table.
            keys = [key for key in keys if key["databaseId:assetId"]["S"].startswith(pk_prefix)]
        return keys, next_key

    def delete_keys(self, keys):
        self.calls.append(("delete_keys", list(keys)))
        return len(keys)

    def search(self, vector, *, top_k, filters):
        raise AssertionError("the indexing handlers never search")

    def names(self):
        return [call[0] for call in self.calls]
