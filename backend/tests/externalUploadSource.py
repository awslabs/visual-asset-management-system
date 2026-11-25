# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A stand-in for `uploadFile.external_upload_source` for suites that exercise a workflow write-back
end to end but not how its source bucket is chosen.

The real function resolves the bucket a workflow write-back reads from with a scan of the bucket
registration table (the VAMS default asset bucket). These suites stage their outputs in a run bucket
of their own and hand its name to the completion, as the workflow output step does, and they hold no
registration table. The stand-in answers with the bucket the call names (or the asset's bucket), as a
registration with a fixed test bucketId, which is what the real function returns on a deployment whose
default bucket is that run bucket.

`tests/handlers/assets/test_upload_bucket_registration_identity.py` tests the real function; a module
that sets `USE_REAL_EXTERNAL_UPLOAD_SOURCE = True` keeps it.
"""

import sys

UPLOAD_FILE_MODULES = ("backend.backend.handlers.assets.uploadFile", "handlers.assets.uploadFile")
TEST_RUN_BUCKET_ID = "test-run-bucket-registration"


def named_external_upload_source(workflow_execution_id, asset_bucket, named_source_bucket=None):
    if not workflow_execution_id or not named_source_bucket or named_source_bucket == asset_bucket.get('bucketName'):
        return asset_bucket
    return {'bucketId': TEST_RUN_BUCKET_ID, 'bucketName': named_source_bucket, 'baseAssetsPrefix': ''}


def patch_external_upload_source(request, monkeypatch):
    """Patch every loaded uploadFile module unless the test module asks for the real function."""
    if getattr(request.module, "USE_REAL_EXTERNAL_UPLOAD_SOURCE", False):
        return
    for name in UPLOAD_FILE_MODULES:
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "external_upload_source"):
            monkeypatch.setattr(module, "external_upload_source", named_external_upload_source)
