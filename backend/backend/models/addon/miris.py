# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v1 models for the Miris Spatial Streaming add-on endpoints.

    GET  /addon/miris/asset-status?databaseId=...&assetId=...&filePath=...
    POST /addon/miris/viewer-key   body: {"databaseId": ..., "assetId": ..., "filePath": ...}
"""

from typing import Optional

from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from pydantic import Field

from common.validators import validate, trim_name
from models.metadata import MAX_FILE_PATH_LENGTH

# File-metadata keys that describe the Miris stream recorded on a USD file.
MIRIS_ASSET_UUID_KEY = "miris_asset_uuid"
MIRIS_SOURCE_VERSION_ID_KEY = "miris_source_version_id"
MIRIS_UPLOADED_AT_KEY = "miris_uploaded_at"
MIRIS_DISPLAY_NAME_KEY = "miris_display_name"
# SHA-256 id of the dependency manifest the upload pipeline wrote to the auxiliary bucket. Optional:
# hand-edited stream metadata may omit it.
MIRIS_DEPENDENCY_MANIFEST_KEY = "miris_dependency_manifest"

MIRIS_STREAM_METADATA_KEYS = (
    MIRIS_ASSET_UUID_KEY,
    MIRIS_SOURCE_VERSION_ID_KEY,
    MIRIS_UPLOADED_AT_KEY,
    MIRIS_DISPLAY_NAME_KEY,
    MIRIS_DEPENDENCY_MANIFEST_KEY,
)

# States the endpoints report themselves; any other value is a Miris processing state.
STATE_NOT_STREAMED = "notStreamed"
STATE_INVALID_UUID = "invalidUuid"
# The Miris asset is missing or lacks the `vams-asset-{assetId}` and `vams-database-{databaseId}`
# tags that bind it to the VAMS asset.
STATE_NOT_BOUND = "notBound"
STATE_UNKNOWN = "unknown"
# The viewer API answered 400 (static mode only).
STATE_PROCESSING = "processing"
# A Miris processing state reported for an asset Miris has deleted; reported as `notBound`.
MIRIS_STATE_DELETED = "deleted"

# How the deployment delivers viewer keys (`MIRIS_VIEWER_KEY_MODE`).
VIEWER_KEY_MODE_STATIC = "static"
VIEWER_KEY_MODE_PER_VIEW = "perView"


class MirisFileRequestModel(BaseModel, extra="ignore"):
    """The database, asset and file a Miris stream request addresses.

    ``filePath`` is validated and given a leading slash here. Mapping a full object key onto
    the asset-relative path needs the asset's root key, so it happens in the handler after the
    asset is loaded (see ``asset_relative_file_path``).
    """

    databaseId: str = Field(min_length=1, max_length=256)
    assetId: str = Field(min_length=1, max_length=256)
    # Not trimmed: a trailing space is a legitimate character in an asset-relative path.
    filePath: str = Field(min_length=1, max_length=MAX_FILE_PATH_LENGTH)

    _trim_ids = validator("databaseId", "assetId", pre=True, allow_reuse=True)(trim_name)

    @root_validator(skip_on_failure=True)
    def _validate_fields(cls, values):
        file_path = values.get("filePath", "")
        if not file_path.startswith("/"):
            file_path = "/" + file_path

        (valid, message) = validate(
            {
                "databaseId": {"value": values.get("databaseId"), "validator": "ID"},
                "assetId": {"value": values.get("assetId"), "validator": "ASSET_ID"},
                "filePath": {"value": file_path, "validator": "RELATIVE_FILE_PATH"},
            }
        )
        if not valid:
            raise ValueError(message)

        if file_path.endswith("/"):
            raise ValueError("filePath cannot be a folder (must not end with /)")

        values["filePath"] = file_path
        return values


def asset_relative_file_path(file_path: str, asset_id: str, asset_root_key: Optional[str]) -> str:
    """Map a validated ``filePath`` onto the asset-relative path file metadata is stored under.

    The metadata service resolves a relative path to its object key as
    ``{assetLocation.Key}/{path without leading slash}``, so a full object key is turned back
    into that relative path by removing this asset's root key. The ``{assetId}/`` form is
    also accepted. At most one prefix is removed; a path under neither prefix is already
    relative and is returned unchanged. Only the given asset's own root is ever removed.
    """
    key = file_path.lstrip("/")
    roots = []
    if isinstance(asset_root_key, str) and asset_root_key.strip("/"):
        roots.append(asset_root_key.lstrip("/").rstrip("/") + "/")
    roots.append(f"{asset_id}/")
    for root in roots:
        if key.startswith(root) and len(key) > len(root):
            return "/" + key[len(root):]
    return file_path


class GetMirisAssetStatusRequestModel(MirisFileRequestModel):
    """Query parameters of the status request."""


class MirisViewerKeyRequestModel(MirisFileRequestModel):
    """Body of the viewer key request."""


class MirisAssetStatusResponseModel(BaseModel, extra="ignore"):
    """Stream metadata recorded on the file, plus the stream's state."""

    filePath: str
    mirisAssetUuid: Optional[str] = None
    sourceVersionId: Optional[str] = None
    uploadedAt: Optional[str] = None
    displayName: Optional[str] = None
    state: str
    # True when a file the stream was built from (the root, its sublayers, references and textures)
    # has changed or been removed since the upload, false when none has, null when unknown.
    stale: Optional[bool] = None


class MirisViewerKeyResponseModel(BaseModel, extra="ignore"):
    """A viewer key that can stream the file's Miris asset.

    In per-view mode the key streams only that asset and ``expiresAt`` is its expiry. In static
    mode it is the deployment-wide viewer key, which has no expiry VAMS reports, so ``expiresAt``
    is null.
    """

    filePath: str
    mirisAssetUuid: str
    viewerKey: str
    expiresAt: Optional[str] = None
