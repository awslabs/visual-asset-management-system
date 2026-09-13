# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Test doubles shared by the handler tests of the Blender Lambda image.

`load_handler` loads `handler.py` by file path with `boto3.client` patched, so the module-level `s3_client`
IS the fake (asserted, because patching a module that resolves its client differently would leave the
tests asserting against nothing). `FakeS3` records every call and serves objects from a dict; its
`download_file` refuses to write outside `sandbox` so the confinement tests stay meaningful against an
unconfined handler without ever touching the machine.
"""

import importlib.util
import io
import os
from unittest.mock import patch

from botocore.exceptions import ClientError

_HERE = os.path.dirname(os.path.abspath(__file__))
CONTAINER_DIR = os.path.dirname(_HERE)
HANDLER_PATH = os.path.join(CONTAINER_DIR, "handler.py")
RENDER_SCENE_PATH = os.path.join(CONTAINER_DIR, "renderScene.py")


class FakeS3:
    def __init__(self, listing=None, objects=None):
        self.listing = [dict(entry) for entry in (listing or [])]  # [{"Key": str, "Size": int}]
        self.objects = dict(objects or {})                          # key -> bytes
        self.downloads = []
        self.uploads = []
        self.puts = []
        self.list_calls = []
        self.sandbox = None

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2", operation_name
        fake = self

        class _Paginator:
            def paginate(self, **kwargs):
                fake.list_calls.append(kwargs)
                prefix = kwargs.get("Prefix", "")
                matching = [entry for entry in fake.listing if entry["Key"].startswith(prefix)]
                # Two pages, so a loop that reads only the first page misses half the siblings.
                half = (len(matching) + 1) // 2
                yield {"Contents": matching[:half]}
                yield {"Contents": matching[half:]}

        return _Paginator()

    def download_file(self, bucket, key, filename, ExtraArgs=None):
        self.downloads.append({"bucket": bucket, "key": key, "path": filename, "extra": ExtraArgs})
        if self.sandbox is not None:
            inside = os.path.realpath(filename).startswith(os.path.realpath(self.sandbox) + os.sep)
            if not inside:
                return
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "wb") as handle:
            handle.write(self.objects[key])

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        with open(filename, "rb") as handle:
            body = handle.read()
        self.uploads.append({"bucket": bucket, "key": key, "bytes": len(body), "extra": ExtraArgs})

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]


class FakeContext:
    """A Lambda context whose remaining time follows a scripted sequence (the last value repeats)."""

    def __init__(self, remaining_ms_sequence):
        self._values = list(remaining_ms_sequence)

    def get_remaining_time_in_millis(self):
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]


def load_handler(fake_s3, module_name="genai_blender_handler_under_test"):
    with patch("boto3.client", return_value=fake_s3):
        spec = importlib.util.spec_from_file_location(module_name, HANDLER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    assert module.s3_client is fake_s3, "the handler's module-level S3 client must be the fake"
    return module
