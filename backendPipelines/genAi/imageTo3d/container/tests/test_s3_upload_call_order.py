#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every ``s3.upload`` call passes (bucket, key, local path), in that order.

The helper takes ``(bucket_name, object_key, file_path)``. One call passed ``(file_path, bucket, key)``, and
Python has no way to notice: all three are strings. It surfaced as ``FileNotFoundError: pipelines/.../
results/agent/DONE.json`` -- the object KEY being opened as a local file -- on the first run of the Blender
agent that had actually produced a model, and it failed that run AFTER the model, its textures and the report
had all been uploaded. A finished, textured result was reported as a failure by the code that was meant to
attach the agent's notes to it.

A static check, because a unit test of the upload helper would not have caught a caller: the call sites are
read out of the module's syntax tree and each argument is checked against what its position must hold.
"""

import ast
import inspect
import os

import pytest

from vams_utils.aws import s3

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _upload_calls(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "upload" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "s3"):
            yield node


def _describe(node):
    return ast.unparse(node)


@pytest.mark.unit
def test_the_helper_signature_is_the_one_this_test_assumes():
    assert list(inspect.signature(s3.upload).parameters) == ["bucket_name", "object_key", "file_path"]


@pytest.mark.unit
def test_every_upload_call_in_main_passes_bucket_key_then_local_path():
    calls = list(_upload_calls(os.path.join(_CONTAINER_DIR, "__main__.py")))
    assert calls, "no s3.upload calls found; the scan is broken"
    for call in calls:
        assert len(call.args) == 3, f"positional (bucket, key, path) expected: {_describe(call)}"
        bucket, key, path = call.args
        # The local path is always wrapped in str(): it is a Path object everywhere in this module.
        assert isinstance(path, ast.Call) and getattr(path.func, "id", "") == "str", (
            f"third argument must be the LOCAL path as str(...): {_describe(call)}")
        # The bucket is a plain name that says so; never a str(...) of a path.
        assert isinstance(bucket, ast.Name) and "bucket" in bucket.id.lower(), (
            f"first argument must be the bucket: {_describe(call)}")
        # The key is built from a prefix -- an f-string or a name -- and is never str(a path).
        assert not (isinstance(key, ast.Call) and getattr(key.func, "id", "") == "str"), (
            f"second argument must be the object KEY, not a local path: {_describe(call)}")
