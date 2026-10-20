#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Change-provenance attributes written onto the asset record.

Shared by the synchronous upload completion (`handlers/assets/uploadFile.py`) and the
asynchronous large-file completion (`handlers/assets/sqsUploadFileLarge.py`) so both paths stamp
the asset row identically, by the asset-version create and revert paths
(`handlers/assets/assetVersions.py`), and by the asset edit (`handlers/assets/assetService.py`
`update_asset`); the compliance trigger reads them from the table's stream image and from the row.
`lastChangeSource` / `lastChangeAt` describe the latest change of any of these kinds (`upload`,
`workflowExecution`, `assetVersion`, `assetEdit`); `lastUploadAt` is written by a user upload's
completion only, so a workflow execution's output write, a new asset version or an asset edit —
each of which re-stamps `lastChangeAt` — leaves the instant of the last user upload readable.
"""

from datetime import datetime, timezone

from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_UPLOAD,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)

# `lastChangeSource` of an asset row whose latest change is a new asset version (created, or made by
# reverting to an earlier one). An asset-row value only: no S3 object carries it as its
# `vams-changesource`.
ASSET_CHANGE_SOURCE_ASSET_VERSION = "assetVersion"

# `lastChangeSource` of an asset row whose latest change is an edit of its name, description or tags
# through the asset API. An asset-row value only, like `assetVersion`.
ASSET_CHANGE_SOURCE_ASSET_EDIT = "assetEdit"

# The asset-row fields an edit of which `asset_edit_provenance` stamps: the fields an
# `assetAttributes` compliance rule reads.
ASSET_EDIT_STAMPED_FIELDS = ("assetName", "description", "tags")


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


def stamp_asset_version_provenance(asset):
    """Record a new asset version as the asset row's latest change, in place on the row dict the
    caller writes back whole: `lastChangeSource` `assetVersion`, `lastChangeAt` now, and no
    `lastChangeWorkflowExecutionId` (the latest change is not a workflow execution's).
    `lastUploadAt` is left as it is. The stream MODIFY that write raises is what makes the compliance
    trigger re-evaluate the asset, so a version-coverage verdict follows the new version."""
    asset['lastChangeSource'] = ASSET_CHANGE_SOURCE_ASSET_VERSION
    asset['lastChangeAt'] = datetime.now(timezone.utc).isoformat()
    asset.pop('lastChangeWorkflowExecutionId', None)
    return asset


def asset_edit_provenance(stored_asset, edited_attributes):
    """The change-provenance update an asset edit makes beside the attributes it edits, as the
    attributes to SET and the attribute names to REMOVE: `lastChangeSource` `assetEdit`,
    `lastChangeAt` now, and `lastChangeWorkflowExecutionId` removed (the latest change is not a
    workflow execution's). `lastUploadAt` is left as it is. Both are empty when `edited_attributes`
    changes none of ASSET_EDIT_STAMPED_FIELDS against `stored_asset`, the row as read before the
    edit, so an edit that resubmits the stored values or changes only `isDistributable` is not
    stamped; tags compare regardless of order, a missing tag list as an empty one. The stream MODIFY
    the stamped write raises is what makes the compliance trigger re-evaluate the asset, so an
    asset-attribute verdict follows the edit."""
    if not any(_edit_changes_field(stored_asset, edited_attributes, field)
               for field in ASSET_EDIT_STAMPED_FIELDS):
        return {}, []
    return ({'lastChangeSource': ASSET_CHANGE_SOURCE_ASSET_EDIT,
             'lastChangeAt': datetime.now(timezone.utc).isoformat()},
            ['lastChangeWorkflowExecutionId'])


def _edit_changes_field(stored_asset, edited_attributes, field):
    """Whether `edited_attributes` writes `field` with a value other than the stored one."""
    if field not in edited_attributes:
        return False
    if field == 'tags':
        return sorted(edited_attributes[field] or []) != sorted(stored_asset.get(field) or [])
    return edited_attributes[field] != stored_asset.get(field)
