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

Preview images are not the asset's content and are still served for a quarantined asset (#1495):
the asset preview image and a file's ``{file}.previewFile.{ext}`` companion, each at its current
version, through the download and stream routes (``is_preview_object``). The files and their
versions, several files at once, export and the auxiliary viewer data stay blocked.

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
from common.s3PathPatterns import (
    ALLOWED_PREVIEW_FILE_EXTENSIONS,
    PREVIEW_FILE_PATTERN,
    preview_file_has_allowed_extension,
)

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


def is_preview_object(object_key, asset_preview=False):
    """Whether ``object_key``, the resolved S3 key a handler is about to serve, is a preview image,
    which a quarantined asset still serves (#1495).

    With ``asset_preview`` the key is the asset's preview image as the asset row records it
    (``previewLocation``, written by the server when a preview upload completes): its name must end
    in an allowed preview extension (``ALLOWED_PREVIEW_FILE_EXTENSIONS``). Otherwise the key must name
    a file's preview companion ``{file}.previewFile.{ext}``: the marker (``PREVIEW_FILE_PATTERN``)
    once in the key's last segment, after a base file name, followed by an allowed extension.

    The test is on the resolved key, not the request text, and fails closed: a key with a ``.`` or
    ``..`` segment, an empty or trailing segment, a ``%`` or a backslash is not a preview, so a marker
    in a folder name, an encoded or a traversal path never reaches another object. Matching is
    case-sensitive on the marker, as S3 keys are; the extension is compared in lower case, as
    ``preview_file_has_allowed_extension`` does."""
    if not isinstance(object_key, str) or not object_key or "%" in object_key or "\\" in object_key:
        return False
    segments = object_key.lstrip("/").split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return False
    name = segments[-1]
    if asset_preview:
        stem, dot, extension = name.rpartition(".")
        return bool(stem and dot) and ("." + extension).lower() in ALLOWED_PREVIEW_FILE_EXTENSIONS
    if name.count(PREVIEW_FILE_PATTERN) != 1 or name.startswith(PREVIEW_FILE_PATTERN):
        return False
    return preview_file_has_allowed_extension(name)


def check_quarantine_block(database_id, asset_id, object_key=None, asset_preview=False):
    """Raise ``QuarantineBlockedResponse`` when the asset is quarantined without a granted exception.

    A no-op when the quarantine block is off. Otherwise reads the asset's compliance state row:
    a ``complianceState`` of ``quarantined`` with no ``exceptionGranted`` raises; a missing row,
    any other state, or a granted exception returns normally. The refusal is logged once, by the
    handler that answers it (``quarantine_refusal``), where the route is known.

    ``object_key`` is the resolved key of the one object the request serves, passed only by the
    single-object routes (one file's download, the asset preview download, the stream routes) and
    only for the object's current version. When it is a preview image (``is_preview_object``, with
    ``asset_preview`` for the asset's preview location) the asset's state is not read and the
    object is served. Every other request (a file, a file version, several files, export, the
    auxiliary viewer data) passes no key and is guarded for the whole asset.
    """
    if not quarantine_blocks_download or compliance_asset_state_table is None:
        return
    if object_key is not None and is_preview_object(object_key, asset_preview=asset_preview):
        return
    response = compliance_asset_state_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    item = response.get("Item")
    if item and item.get("complianceState") == "quarantined" and not item.get("exceptionGranted"):
        raise QuarantineBlockedResponse(database_id, asset_id)
