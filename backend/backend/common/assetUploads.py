#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Record-level checks on a stored asset-upload record before it is completed.

An upload initialization writes one record per upload to the asset uploads table, keyed by
`uploadId` + `assetId`. The upload completion (`handlers/assets/uploadFile.py` `complete_upload`)
refuses a record that cannot be completed on the user completion route, and the ingest-asset
complete stage (`handlers/assets/ingestAsset.py`) asks the same question before it creates a
missing asset, so a completion that cannot proceed writes nothing. Both read the record
themselves and pass it here, so the two answer identically.

`completion_refusal` and `validate_file_extension_against_database` are pure: it reads no table and writes no log. The caller audits and raises.
"""

import os
import time
from typing import NamedTuple, Optional

# Generic client messages (Critical Rule 11: no request input is echoed back).
UPLOAD_RECORD_NOT_FOUND = "Upload record not found"
UPLOAD_RECORD_MISMATCH = "Upload details do not match request"
UPLOAD_TYPE_MISMATCH = "Upload type mismatch."
UPLOAD_EXPIRED = "Upload has expired"


class UploadCompletionRefusal(NamedTuple):
    """Why a record cannot be completed. `audited` marks a refusal the upload completion records
    in the file-upload audit log for every submitted file (expired and external records), as it
    did before these checks were shared; the other refusals are answered without that entry."""

    reason: str
    audited: bool


def completion_refusal(upload_record: dict, database_id: str, asset_id: str, upload_type: str,
                       now: Optional[int] = None) -> Optional[UploadCompletionRefusal]:
    """The refusal for completing `upload_record` on the user completion route, or None.

    Checked in this order, first refusal wins:

    1.  The record belongs to the requested database and asset.
    2.  The record was initialized for the requested upload type.
    3.  The record is not past its `expiresAt` (epoch seconds, set by every writer: initialize
        7 days, workflow output 1 day). The table's TTL removes an expired record only some time
        after it expires, so until then this check keeps it from completing. A missing or
        unreadable value counts as expired.
    4.  The record is not an external (workflow-output) record. Those carry no initialized file
        list by design and are completed only by the internal external-completion route.

    A completed record needs no check of its own: a completion whose files all land deletes the
    record, so it is then not found; a record left as `completed_with_errors` stays completable
    so the failed files can be completed again.
    """
    if upload_record.get('assetId') != asset_id or upload_record.get('databaseId') != database_id:
        return UploadCompletionRefusal(UPLOAD_RECORD_MISMATCH, audited=False)

    if upload_record.get('uploadType') != upload_type:
        return UploadCompletionRefusal(UPLOAD_TYPE_MISMATCH, audited=False)

    try:
        expires_at = int(upload_record.get('expiresAt', 0))
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at < (int(time.time()) if now is None else now):
        return UploadCompletionRefusal(UPLOAD_EXPIRED, audited=True)

    if upload_record.get('isExternalUpload', False):
        return UploadCompletionRefusal(UPLOAD_TYPE_MISMATCH, audited=True)

    return None


def validate_file_extension_against_database(file_path: str, allowed_extensions: str) -> tuple:
    """Validate file extension against database restrictions
    
    Args:
        file_path: The file path to validate
        allowed_extensions: Comma-delimited list of allowed extensions (e.g., ".pdf,.jpg,.png")
        
    Returns:
        Tuple of (is_valid, error_message)
        - is_valid: True if extension is allowed, False otherwise
        - error_message: Error message if validation fails, None otherwise
    """
    # Handle empty or None allowed_extensions (no restrictions)
    if not allowed_extensions or allowed_extensions.strip() == "":
        return True, None
    
    # Parse comma-delimited extensions and normalize to lowercase
    allowed_list = [ext.strip().lower() for ext in allowed_extensions.split(',') if ext.strip()]
    
    # Check for ".all" bypass
    if ".all" in allowed_list:
        return True, None
    
    # Extract file extension (case-insensitive)
    file_extension = os.path.splitext(file_path)[1].lower()
    
    # Validate extension
    if file_extension not in allowed_list:
        error_message = f"Database does not allow this file extension. Allowed extensions: {', '.join(allowed_list)}"
        return False, error_message
    
    return True, None
