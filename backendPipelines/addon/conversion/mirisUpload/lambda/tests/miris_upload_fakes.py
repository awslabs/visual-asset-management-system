#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Shared fakes for the Miris upload lambda tests: a claim held by a given execution, and an
in-memory S3 client for the claim code."""


def claim_held_by(s3_mock, task_token):
    """Make a mocked S3 client return a claim recorded by the execution holding `task_token`."""
    import io
    import json

    import mirisClaim

    s3_mock.get_object.side_effect = lambda **_: {
        "Body": io.BytesIO(json.dumps({"owner": mirisClaim.owner_id(task_token)}).encode())
    }
    return s3_mock


class FakeS3:
    """An in-memory S3 client covering what the claim code calls: a paginated listing, a
    conditional put, get and delete. Objects are {(bucket, key): (body, etag)}."""

    def __init__(self):
        import types

        from botocore.exceptions import ClientError

        self.objects = {}
        self.exceptions = types.SimpleNamespace(ClientError=ClientError)
        self.list_calls = []

    def put_file(self, bucket, key, etag):
        self.objects[(bucket, key)] = (b"", f'"{etag}"')

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class _Paginator:
            def paginate(self, Bucket, Prefix=""):
                fake.list_calls.append((Bucket, Prefix))
                contents = [
                    {"Key": key, "ETag": etag, "Size": len(body)}
                    for (bucket, key), (body, etag) in sorted(fake.objects.items())
                    if bucket == Bucket and key.startswith(Prefix)
                ]
                return [{"Contents": contents}] if contents else [{}]

        return _Paginator()

    def put_object(self, Bucket, Key, Body=b"", IfNoneMatch=None, **_):
        if IfNoneMatch == "*" and (Bucket, Key) in self.objects:
            raise self.exceptions.ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": "exists"}}, "PutObject")
        self.objects[(Bucket, Key)] = (Body, '"put"')
        return {}

    def get_object(self, Bucket, Key):
        import io

        if (Bucket, Key) not in self.objects:
            raise self.exceptions.ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}

    def claim_keys(self, bucket):
        return sorted(key for (b, key) in self.objects if b == bucket and key.startswith("locks/"))
