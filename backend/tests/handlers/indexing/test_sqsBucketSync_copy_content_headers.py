# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync's metadata stamp keeps the file's content type and other content headers (#2017).

`update_s3_metadata` stamps `databaseid` / `assetid` / `vams-changesource` onto an object that
arrived without them (a direct S3 write that bucket sync turns into an asset file) by copying the
object onto itself with `MetadataDirective: REPLACE`. A REPLACE copy takes every system-defined
header from the request, so a copy that restates only `Metadata` writes a new current version
whose content type is `binary/octet-stream`. The copy must restate the `ContentType` and the other
`COPY_PRESERVED_HEADER_FIELDS` that `head_object` returned for the key it copies.
"""

from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError

from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_DIRECT,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
)
from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

BUCKET = "asset-bucket"
KEY = "x-asset-1/model.glb"
EXPIRES = datetime(2030, 1, 1, tzinfo=timezone.utc)

# What head_object returns besides the content headers; none of it may be restated on the copy.
OBJECT_FIELDS = {
    "ContentLength": 10,
    "ETag": '"abc"',
    "VersionId": "v1",
    "LastModified": datetime(2026, 11, 27, tzinfo=timezone.utc),
    "ServerSideEncryption": "aws:kms",
    "StorageClass": "STANDARD",
}


class _FakeS3:
    """Fake S3 client and resource: head_object answers from `heads` by key, copies are recorded."""

    def __init__(self, heads):
        self._heads = heads
        self.head_calls = []
        self.copies = []
        fake = self

        class _Object:
            def __init__(self, bucket, key):
                self.bucket = bucket
                self.key = key

            def copy(self, CopySource, ExtraArgs=None, **kwargs):
                fake.copies.append({"CopySource": CopySource, "Bucket": self.bucket,
                                    "Key": self.key, "ExtraArgs": ExtraArgs})

        class _Resource:
            def Object(self, bucket, key):
                return _Object(bucket, key)

        self.resource = _Resource()

    def head_object(self, Bucket, Key, **kwargs):
        self.head_calls.append(Key)
        if Key not in self._heads:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return dict(self._heads[Key])


def _wire(monkeypatch, heads):
    m = _load()
    fake = _FakeS3(heads)
    monkeypatch.setattr(m, "s3_client", fake)
    monkeypatch.setattr(m, "s3_resource", fake.resource)
    return m, fake


def _stamped(metadata):
    return {
        **metadata,
        DATABASE_ID_METADATA_KEY: "db1",
        ASSET_ID_METADATA_KEY: "x-asset-1",
        VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_DIRECT,
    }


@pytest.mark.unit
class TestMetadataStampKeepsContentHeaders:
    def test_copy_restates_the_content_type_and_every_optional_header(self, monkeypatch):
        headers = {
            "ContentType": "model/gltf-binary",
            "ContentEncoding": "gzip",
            "ContentDisposition": 'attachment; filename="model.glb"',
            "ContentLanguage": "en",
            "CacheControl": "max-age=60",
            "Expires": EXPIRES,
        }
        m, fake = _wire(monkeypatch, {KEY: {**OBJECT_FIELDS, **headers, "Metadata": {"custom": "keep"}}})

        assert m.update_s3_metadata(BUCKET, KEY, "db1", "x-asset-1") is True

        assert len(fake.copies) == 1
        copy = fake.copies[0]
        assert copy["CopySource"] == {"Bucket": BUCKET, "Key": KEY}
        assert (copy["Bucket"], copy["Key"]) == (BUCKET, KEY)
        # Exactly the stamp, the directive and the content headers: nothing else from the head
        # (encryption, storage class, version) is added to the copy.
        assert copy["ExtraArgs"] == {
            "Metadata": _stamped({"custom": "keep"}),
            "MetadataDirective": "REPLACE",
            **headers,
        }

    def test_copy_without_optional_headers_restates_only_the_content_type(self, monkeypatch):
        m, fake = _wire(monkeypatch, {KEY: {**OBJECT_FIELDS, "ContentType": "image/png", "Metadata": {}}})

        assert m.update_s3_metadata(BUCKET, KEY, "db1", "x-asset-1") is True

        assert fake.copies[0]["ExtraArgs"] == {
            "Metadata": _stamped({}),
            "MetadataDirective": "REPLACE",
            "ContentType": "image/png",
        }

    def test_headers_come_from_the_key_the_copy_writes_after_the_key_fallback(self, monkeypatch):
        # The event delivered the form-encoded key; the object is stored under the decoded one.
        # The first head 404s, the second finds the object, and the copy writes that key with
        # that object's content type.
        stored_key = "x-asset-1/my model.glb"
        head = {**OBJECT_FIELDS, "ContentType": "model/gltf-binary", "Metadata": {}}
        m, fake = _wire(monkeypatch, {stored_key: head})

        assert m.update_s3_metadata(BUCKET, "x-asset-1/my+model.glb", "db1", "x-asset-1") is True

        assert fake.head_calls == ["x-asset-1/my+model.glb", stored_key]
        copy = fake.copies[0]
        assert copy["Key"] == stored_key
        assert copy["CopySource"] == {"Bucket": BUCKET, "Key": stored_key}
        assert copy["ExtraArgs"]["ContentType"] == "model/gltf-binary"

    def test_an_object_already_stamped_is_not_copied(self, monkeypatch):
        # Control: the "already matches" skip is unchanged and writes no new version.
        head = {**OBJECT_FIELDS, "ContentType": "model/gltf-binary", "Metadata": _stamped({})}
        m, fake = _wire(monkeypatch, {KEY: head})

        assert m.update_s3_metadata(BUCKET, KEY, "db1", "x-asset-1") is True

        assert fake.copies == []
