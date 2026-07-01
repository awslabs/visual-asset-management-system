# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_UPLOAD,
)

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401


@pytest.mark.unit
def test_large_upload_change_metadata():
    from backend.backend.handlers.assets import sqsUploadFileLarge
    md = sqsUploadFileLarge.build_upload_change_metadata("bob@corp")
    assert md[VAMS_CHANGE_SOURCE_METADATA_KEY] == VAMS_CHANGE_SOURCE_UPLOAD
    assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "bob@corp"


@pytest.mark.unit
def test_large_upload_change_metadata_defaults_system():
    from backend.backend.handlers.assets import sqsUploadFileLarge
    md = sqsUploadFileLarge.build_upload_change_metadata(None)
    assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "SYSTEM_USER"


@pytest.mark.unit
def test_large_upload_change_metadata_with_changeUserId_from_message():
    """Verify that when file_info contains changeUserId, it is used in metadata."""
    from backend.backend.handlers.assets import sqsUploadFileLarge
    file_info_with_user = {"changeUserId": "alice@company"}
    change_user_id = file_info_with_user.get("changeUserId")
    md = sqsUploadFileLarge.build_upload_change_metadata(change_user_id)
    assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "alice@company"


@pytest.mark.unit
def test_large_upload_change_metadata_without_changeUserId_from_message():
    """Verify that when file_info lacks changeUserId, SYSTEM is used."""
    from backend.backend.handlers.assets import sqsUploadFileLarge
    file_info_no_user = {}
    change_user_id = file_info_no_user.get("changeUserId")
    md = sqsUploadFileLarge.build_upload_change_metadata(change_user_id)
    assert md[VAMS_CHANGE_USER_ID_METADATA_KEY] == "SYSTEM_USER"
