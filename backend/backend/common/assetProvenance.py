#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Change-provenance attributes written onto the asset record by every upload completion.

Shared by the synchronous upload completion (`handlers/assets/uploadFile.py`) and the
asynchronous large-file completion (`handlers/assets/sqsUploadFileLarge.py`) so both paths stamp
the asset row identically; the compliance trigger reads them from the table's stream image and
from the row. `lastChangeSource` / `lastChangeAt` describe the latest completion of either kind;
`lastUploadAt` is written by a user upload's completion only, so a workflow execution's output
write — which re-stamps `lastChangeAt` — leaves the instant of the last user upload readable.
"""

from datetime import datetime, timezone

from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_UPLOAD,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)


def asset_change_provenance(workflow_execution_id=None):
    """The change-provenance attributes an upload completion writes onto the asset row.

    `lastChangeSource` is `workflowExecution` when the completion writes a workflow execution's
    outputs into the asset (the end-state Lambda's cross-call carries `workflowExecutionId`) and
    `upload` otherwise; `lastChangeWorkflowExecutionId` names that execution and is absent for an
    upload; `lastChangeAt` is the instant of the write. A user upload's completion also writes
    `lastUploadAt`, the same instant; a workflow execution's completion omits the key, so the SET
    the callers build from these attributes leaves the previous upload's instant on the row. The
    asset table's stream image carries them, which is how the compliance trigger tells a pipeline
    rule's own output apart from a user change and judges an uploaded object against the upload
    that completed it rather than against a later output write.
    """
    now = datetime.now(timezone.utc).isoformat()
    attributes = {
        'lastChangeSource': (VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION if workflow_execution_id
                             else VAMS_CHANGE_SOURCE_UPLOAD),
        'lastChangeAt': now,
    }
    if workflow_execution_id:
        attributes['lastChangeWorkflowExecutionId'] = workflow_execution_id
    else:
        attributes['lastUploadAt'] = now
    return attributes
