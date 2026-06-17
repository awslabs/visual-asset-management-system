# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mock of common.s3 for tests.

The real module validates file extensions and MIME types against blocklists.
Tests default these to "valid" unless explicitly mocked.
"""


def validateUnallowedFileExtensionAndContentType(keyPath, contentType):
    """Mock: always returns True (valid)."""
    return True


def validateS3AssetExtensionsAndContentType(bucket, prefixKey):
    """Mock: always returns True (valid)."""
    return True
