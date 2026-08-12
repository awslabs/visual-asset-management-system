# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mock of common.s3 for tests.

The real module validates file extensions and MIME types against blocklists.
Tests default these to "valid" unless explicitly mocked.
"""

# Mirror the real module's page-size constants for import parity.
S3_VERSIONS_PAGE_SIZE = 1000
S3_OBJECTS_PAGE_SIZE = 1000


def validateUnallowedFileExtensionAndContentType(keyPath, contentType):
    """Mock: always returns True (valid)."""
    return True


def is_object_version_archived(bucket, key, version_id=None, client=None):
    """Mock: head-based archive check using the provided client.

    Mirrors the real helper's head_object-based contract so handlers under test
    behave the same. Returns False when no client is supplied.
    """
    if client is None:
        return False
    try:
        if version_id:
            try:
                client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
                return False
            except Exception as e:
                code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
                if code == "MethodNotAllowed":
                    return True
                if code in ("NoSuchKey", "404", "NotFound"):
                    return False
                raise
        else:
            try:
                response = client.head_object(Bucket=bucket, Key=key)
                return response.get("DeleteMarker", False)
            except Exception as e:
                code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
                if code in ("NoSuchKey", "404", "NotFound"):
                    versions_response = client.list_object_versions(Bucket=bucket, Prefix=key, MaxKeys=1)
                    return len(versions_response.get("DeleteMarkers", [])) > 0
                raise
    except Exception:
        return False


def validateS3AssetExtensionsAndContentType(bucket, prefixKey):
    """Mock: always returns True (valid)."""
    return True


def list_all_object_versions(bucket, prefix, client=None, max_keys=None,
                             key_marker=None, version_id_marker=None):
    """Mock: page through the provided client's list_object_versions, or empty.

    Mirrors the real helper's contract (aggregated Versions + DeleteMarkers) so
    handlers under test behave the same. When a client is supplied it makes a
    single call (sufficient for test fakes that return all data at once). The
    max_keys / marker params are accepted for signature parity and ignored.
    """
    if client is None:
        return {"Versions": [], "DeleteMarkers": []}
    response = client.list_object_versions(Bucket=bucket, Prefix=prefix)
    return {
        "Versions": response.get("Versions", []),
        "DeleteMarkers": response.get("DeleteMarkers", []),
    }


def list_all_objects(bucket, prefix, client=None, max_objects=None):
    """Mock: return objects under a prefix via the provided client, or empty.

    Honors max_objects so callers that cap the listing (e.g. asset-type
    detection) behave the same as the real helper.
    """
    if client is None:
        return []
    objects = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects.extend(page.get("Contents", []))
        if max_objects is not None and len(objects) >= max_objects:
            return objects[:max_objects]
    return objects
