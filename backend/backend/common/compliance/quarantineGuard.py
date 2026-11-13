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

A refusal is an expected answer of a control working as configured, not a failure. Each guarded
handler catches ``QuarantineBlockedResponse`` ahead of its ``VAMSGeneralErrorResponse`` handling
(which logs with a traceback) and answers it with ``quarantine_refusal``: one INFO line naming the
database, the asset and the route, no traceback, and the same ``400`` body as before (D174).
``general_error`` adds its own WARNING line, as for every 4xx answer.
"""

import os
import boto3
from botocore.config import Config
from customLogging.logger import safeLogger
from models.common import VAMSGeneralErrorResponse, general_error
from common.resourceNames import ResourceKeys, get_table_name

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger()

# The one client-facing message every guarded handler returns (Rule 11: no asset identifiers).
QUARANTINE_BLOCK_MESSAGE = "Asset is quarantined and cannot be downloaded"


class QuarantineBlockedResponse(VAMSGeneralErrorResponse):
    """The quarantine block's refusal: status 400, text exactly ``QUARANTINE_BLOCK_MESSAGE``.

    Every guarded handler answers a ``VAMSGeneralErrorResponse`` with ``{'message': str(error)}``.
    The base class prefixes its text with "VAMS General Error: ", so the refusal reached the web,
    the CLI and the MCP tools as "VAMS General Error: Asset is quarantined and cannot be
    downloaded" rather than the sentence the documentation quotes (D169). This subclass's text is
    the sentence alone, on every route the guard covers; every other ``VAMSGeneralErrorResponse``
    keeps its own text.

    It carries the database and asset it refused (never part of its text) for the one log line
    ``quarantine_refusal`` writes.
    """

    def __init__(self, database_id=None, asset_id=None):
        super().__init__(QUARANTINE_BLOCK_MESSAGE, status_code=400)
        self.database_id = database_id
        self.asset_id = asset_id

    def __str__(self):
        return QUARANTINE_BLOCK_MESSAGE


def _route(event):
    """`<METHOD> <path>` of the request, from either API Gateway event shape."""
    event = event if isinstance(event, dict) else {}
    http = (event.get("requestContext") or {}).get("http") or {}
    method = http.get("method") or event.get("httpMethod") or "?"
    path = http.get("path") or event.get("path") or "?"
    return f"{method} {path}"


def quarantine_refusal(refusal, event):
    """The answer to a ``QuarantineBlockedResponse``: logged once at INFO with the database, the
    asset and the route, without a traceback, and answered ``400`` with exactly
    ``{"message": QUARANTINE_BLOCK_MESSAGE}`` (``general_error``, which writes the error audit
    entry and its own WARNING line as for every 4xx)."""
    logger.info(
        f"Quarantine block: download refused for asset {refusal.asset_id} in database "
        f"{refusal.database_id} on {_route(event)}")
    return general_error(status_code=refusal.status_code, body={"message": str(refusal)},
                         event=event)

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
    """Raise ``QuarantineBlockedResponse`` when the asset is quarantined without a granted exception.

    A no-op when the quarantine block is off. Otherwise reads the asset's compliance state row:
    a ``complianceState`` of ``quarantined`` with no ``exceptionGranted`` raises; a missing row,
    any other state, or a granted exception returns normally. The refusal is logged once, by the
    handler that answers it (``quarantine_refusal``), where the route is known.
    """
    if not quarantine_blocks_download or compliance_asset_state_table is None:
        return
    response = compliance_asset_state_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    item = response.get("Item")
    if item and item.get("complianceState") == "quarantined" and not item.get("exceptionGranted"):
        raise QuarantineBlockedResponse(database_id, asset_id)
