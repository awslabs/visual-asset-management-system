#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The JSON writers in ``utils/s3_utils.py`` run for real, against a stubbed S3 client.

``put_json`` writes bytes the pipeline core already built (``preview-incomplete.json`` and the
no-preview result document); ``put_json_payload`` serialises a payload (the Render3D analysis
manifest). The two were once both named ``put_json``, the second shadowed the first, and the core's
writes raised a TypeError that was only logged. The other suites replace ``put_json`` with a mock, so
only these tests reach the S3 client call. The AST guard keeps a module from defining a name twice.
"""

import ast
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from preview_pipeline import core, dependencies, media_preview
from preview_pipeline.utils import s3_utils

RESULTS = {"bucketName": "run-bucket", "objectDir": "xid/results/"}


def _put_object_calls(client):
    return [call.kwargs for call in client.put_object.call_args_list]


@pytest.mark.unit
class TestPutJsonWritesTheBytesItIsGiven:
    def test_bytes_are_written_as_given_with_the_json_content_type(self):
        client = MagicMock()
        client.meta.region_name = "us-east-1"
        body = b'{"z": 1,   "a": [2]}'
        with patch.object(s3_utils, "client", client):
            assert s3_utils.put_json("b", "k.json", body) == "k.json"
        assert _put_object_calls(client) == [
            {"Bucket": "b", "Key": "k.json", "Body": body, "ContentType": "application/json"}]

    def test_a_refused_write_returns_none(self):
        client = MagicMock()
        client.meta.region_name = "us-east-1"
        client.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        with patch.object(s3_utils, "client", client):
            assert s3_utils.put_json("b", "k.json", b"{}") is None

    def test_the_write_uses_the_buckets_regional_client(self):
        default, regional = MagicMock(), MagicMock()
        default.meta.region_name = "us-east-1"
        with patch.object(s3_utils, "client", default), \
                patch.object(s3_utils, "client_for_bucket", return_value=regional):
            s3_utils.put_json("remote", "k.json", b"{}")
        regional.put_object.assert_called_once()
        default.put_object.assert_not_called()


@pytest.mark.unit
class TestPutJsonPayloadSerialisesThePayload:
    def test_a_dict_is_written_sorted_and_indented(self):
        client = MagicMock()
        with patch.object(s3_utils, "client", client):
            assert s3_utils.put_json_payload("b", "m.json", {"b": 1, "a": {"c": [1]}}) == "m.json"
        (call,) = _put_object_calls(client)
        assert call["Bucket"] == "b" and call["Key"] == "m.json"
        assert call["ContentType"] == "application/json"
        assert call["Body"] == json.dumps({"b": 1, "a": {"c": [1]}}, indent=2, sort_keys=True).encode("utf-8")

    def test_a_failed_write_raises(self):
        client = MagicMock()
        client.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        with patch.object(s3_utils, "client", client):
            with pytest.raises(ClientError):
                s3_utils.put_json_payload("b", "m.json", {})


@pytest.mark.unit
class TestTheCoreDocumentsReachTheS3Client:
    """core.py writes through the real ``s3.put_json``; only the S3 client is replaced."""

    def _client(self):
        client = MagicMock()
        client.meta.region_name = "us-east-1"
        return client

    def test_the_incomplete_preview_document_reaches_put_object(self):
        incomplete = dependencies.incomplete_preview(dependencies.FetchReport(), RuntimeError("x"))
        client = self._client()
        with patch.object(s3_utils, "client", client):
            core._write_incomplete_result(RESULTS, incomplete, "scene/model.obj")
        (call,) = _put_object_calls(client)
        assert call["Bucket"] == "run-bucket"
        assert call["Key"] == RESULTS["objectDir"] + dependencies.INCOMPLETE_RESULT_FILENAME
        assert call["Body"] == dependencies.incomplete_result_document(incomplete, "scene/model.obj")
        assert call["ContentType"] == "application/json"

    def test_the_no_preview_document_reaches_put_object(self):
        client = self._client()
        stage = MagicMock()
        with patch.object(s3_utils, "client", client):
            core._complete_without_preview(stage, RESULTS, "photo.png", "No preview.", "image")
        (call,) = _put_object_calls(client)
        assert call["Key"] == RESULTS["objectDir"] + media_preview.NO_PREVIEW_RESULT_FILENAME
        assert call["Body"] == media_preview.no_preview_result_document("photo.png", "No preview.", "image")
        assert call["ContentType"] == "application/json"


@pytest.mark.unit
class TestNoTopLevelNameIsDefinedTwice:
    def test_s3_utils_defines_each_top_level_function_and_class_once(self):
        path = os.path.join(os.path.dirname(s3_utils.__file__), "s3_utils.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = [node.name for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        assert duplicates == [], f"defined more than once in s3_utils.py (the later one shadows): {duplicates}"
