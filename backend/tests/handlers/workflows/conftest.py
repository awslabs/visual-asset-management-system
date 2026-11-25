# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest


@pytest.fixture(autouse=True)
def _named_external_upload_source(request, monkeypatch):
    """The workflow output step's end-to-end suites call the real external completion with a run bucket
    of their own (see tests/externalUploadSource.py); the source-bucket decision has its own tests."""
    from backend.tests.externalUploadSource import patch_external_upload_source
    patch_external_upload_source(request, monkeypatch)
