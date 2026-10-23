#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Quarantine-blocks-download guard shared by every handler that hands out asset file bytes:
downloadAsset, streamAsset, assetExportService and streamAuxiliaryPreviewAsset.

The guard is switched on by ``COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD`` (set on those four Lambdas
from ``app.compliance.quarantineBlocksDownload``). The compliance asset-state table is resolved at
import only when the switch is on, so a deployment with the block off resolves no extra resource
name and reads no extra table.

``check_quarantine_block`` runs AFTER Tier-1 and Tier-2 authorization have passed (backend Rule 4
ordering): a caller who is not allowed to read the asset receives the authorization denial and
never learns whether the asset is quarantined.
"""

import os
import boto3
from botocore.config import Config
from customLogging.logger import safeLogger
from models.common import VAMSGeneralErrorResponse
from common.resourceNames import ResourceKeys, get_table_name

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="QuarantineGuard")

# The one client-facing message every guarded handler returns (Rule 11: no asset identifiers).
QUARANTINE_BLOCK_MESSAGE = "Asset is quarantined and cannot be downloaded"

try:
    quarantine_blocks_download = os.environ.get(
        "COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD", "false"
    ).lower() == "true"
    # The compliance asset-state table is resolved only when the quarantine block is on.
    compliance_asset_state_table_name = (
        get_table_name(ResourceKeys.COMPLIANCE_ASSET_STATE_STORAGE_TABLE)
        if quarantine_blocks_download else None
    )
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

compliance_asset_state_table = (
    dynamodb.Table(compliance_asset_state_table_name) if compliance_asset_state_table_name else None
)


def check_quarantine_block(database_id, asset_id):
    """Raise ``VAMSGeneralErrorResponse`` when the asset is quarantined without a granted exception.

    A no-op when the quarantine block is off. Otherwise reads the asset's compliance state row:
    a ``complianceState`` of ``quarantined`` with no ``exceptionGranted`` raises; a missing row,
    any other state, or a granted exception returns normally.
    """
    if not quarantine_blocks_download or compliance_asset_state_table is None:
        return
    response = compliance_asset_state_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    item = response.get("Item")
    if item and item.get("complianceState") == "quarantined" and not item.get("exceptionGranted"):
        logger.info(f"Quarantine block applied to asset {asset_id} in database {database_id}")
        raise VAMSGeneralErrorResponse(QUARANTINE_BLOCK_MESSAGE)
