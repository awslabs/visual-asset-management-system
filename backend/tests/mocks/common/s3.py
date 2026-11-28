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
                if code in ("405", "MethodNotAllowed"):
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
                    # Single-entry, exact-key, IsLatest match, per common.s3's docstring: the
                    # key leads its own prefix page and its versions come newest first.
                    versions_response = client.list_object_versions(
                        Bucket=bucket, Prefix=key, MaxKeys=1)
                    return any(marker.get("Key") == key and marker.get("IsLatest")
                               for marker in versions_response.get("DeleteMarkers", []))
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


def key_has_any_version(bucket, key, client=None):
    """Mirror of the real helper: one ``MaxKeys=1`` ListObjectVersions call, matched on the exact key.

    Without a client, or with a client whose listing is not a dict (a bare MagicMock), the key is taken to
    exist, which keeps a single-file location on its earlier path mapping.
    """
    if client is None:
        return True
    response = client.list_object_versions(Bucket=bucket, Prefix=key, MaxKeys=1)
    if not isinstance(response, dict):
        return True
    entries = list(response.get("Versions", []) or []) + list(response.get("DeleteMarkers", []) or [])
    return any(isinstance(entry, dict) and entry.get("Key") == key for entry in entries)


def asset_file_paths(bucket, location, client=None):
    """Mirror of the real helper: the real ``AssetFilePaths`` with ``key_has_any_version`` as its S3 read."""
    from common.assetLocations import AssetFilePaths

    return AssetFilePaths(location, key_exists=lambda key: key_has_any_version(bucket, key, client=client))


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


# Same rule as the real module: presigned asset/preview downloads of an SVG key carry the SVG
# response type and an attachment disposition; a download URL (attachment=True) carries an
# attachment disposition naming the file; every other key is unchanged.
SVG_RESPONSE_CONTENT_TYPE = 'image/svg+xml'
SVG_RESPONSE_CONTENT_DISPOSITION = 'attachment'
ATTACHMENT_DISPOSITION_MAX_LENGTH = 640
SIGNED_QUERY_SAFE_CHARS = '-._~'


def attachment_content_disposition(key):
    """Mirror of the real helper (pure function, no AWS calls)."""
    from urllib.parse import quote
    name = str(key or '').rsplit('/', 1)[-1]
    if not name:
        return 'attachment'
    fallback = ''.join(ch if ' ' <= ch <= '~' else '_' for ch in name)
    fallback = fallback.replace('\\', '\\\\').replace('"', '\\"')
    value = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (fallback, quote(name, safe=''))
    if len(quote(value, safe=SIGNED_QUERY_SAFE_CHARS)) > ATTACHMENT_DISPOSITION_MAX_LENGTH:
        return 'attachment'
    return value


def presigned_get_object_params(params, attachment=False):
    """Mirror of the real helper (pure function, no AWS calls)."""
    signed = dict(params)
    key = str(signed.get('Key') or '')
    if key.lower().endswith('.svg'):
        signed['ResponseContentType'] = SVG_RESPONSE_CONTENT_TYPE
        signed['ResponseContentDisposition'] = SVG_RESPONSE_CONTENT_DISPOSITION
    if attachment:
        signed['ResponseContentDisposition'] = attachment_content_disposition(key)
    return signed
