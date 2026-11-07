# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request and response models of Download as Zip (handlers/zipDownloads/zipDownloadService.py).

Pydantic v1 only (root CLAUDE.md Rule 1). Error text never echoes a caller-supplied value (#1134):
a refusal names the field and the rule, not the value.
"""

from typing import List, Literal, Optional

from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from pydantic import Field

from common.validators import trim_name


MAX_SELECTION_ENTRIES = 1000
MAX_RELATIVE_PATH_CHARS = 1024

# An S3 VersionId is URL-safe ([A-Za-z0-9._-], plus the literal "null" on an unversioned object);
# the same charset as models.executions.ExecuteInputFileModel.versionId.
VERSION_ID_PATTERN = r"^[A-Za-z0-9._\-]+$"
ASSET_VERSION_ID_PATTERN = r"^[A-Za-z0-9._\-]+$"


def _has_control_characters(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


class ZipSelectionEntryModel(BaseModel, extra='ignore'):
    """One requested entry: a file, a folder (path ends with '/'), or the whole asset ('/')."""
    type: Literal["file", "folder", "asset"]
    relativePath: str = Field(..., min_length=1, max_length=MAX_RELATIVE_PATH_CHARS)
    versionId: Optional[str] = Field(None, min_length=1, max_length=256, regex=VERSION_ID_PATTERN)

    @root_validator(skip_on_failure=True)
    def validate_entry(cls, values):
        path = values.get("relativePath") or ""
        entry_type = values.get("type")
        if not path.startswith("/"):
            raise ValueError("relativePath must be asset-relative and begin with '/'")
        if "\\" in path:
            raise ValueError("relativePath must not contain backslashes")
        if _has_control_characters(path):
            raise ValueError("relativePath must not contain control characters")
        if any(segment in ("..", ".") for segment in path.split("/")):
            raise ValueError("relativePath must not contain '.' or '..' segments")
        if "//" in path:
            raise ValueError("relativePath must not contain empty segments")
        if entry_type == "asset" and path != "/":
            raise ValueError("an asset entry must have relativePath '/'")
        if entry_type == "folder" and (path == "/" or not path.endswith("/")):
            raise ValueError("a folder entry must end with '/' and must not be the asset root")
        if entry_type == "file" and path.endswith("/"):
            raise ValueError("a file entry must not end with '/'")
        if values.get("versionId") and entry_type != "file":
            raise ValueError("versionId is only allowed on file entries")
        return values


class CreateZipDownloadRequestModel(BaseModel, extra='ignore'):
    """POST /database/{databaseId}/assets/{assetId}/download/zip.

    Version pinning: either per-file ``versionId`` on file entries, or ONE asset-version pin
    (``assetVersionId`` or ``assetVersionIdAlias``) that resolves every selected file, folder or the
    whole asset to the file versions recorded in that asset version. The forms are mutually
    exclusive."""
    selection: List[ZipSelectionEntryModel] = Field(..., min_items=1, max_items=MAX_SELECTION_ENTRIES)
    assetVersionId: Optional[str] = Field(None, min_length=1, max_length=256,
                                          regex=ASSET_VERSION_ID_PATTERN)
    assetVersionIdAlias: Optional[str] = Field(None, min_length=1, max_length=256)
    archiveFormat: Literal["zip", "tar", "tar.gz"] = "zip"
    compression: Literal["store", "deflate"] = "store"
    archiveFileName: Optional[str] = Field(None, min_length=1, max_length=255)

    # The archive name trims its surrounding whitespace before the length check (the sanitizer
    # strips again when it builds the stored name).
    _trim_names = validator("archiveFileName", pre=True, allow_reuse=True)(trim_name)

    @validator("assetVersionIdAlias")
    def validate_alias(cls, v):
        if v is not None and _has_control_characters(v):
            raise ValueError("assetVersionIdAlias must not contain control characters")
        return v

    @validator("archiveFileName")
    def validate_archive_file_name(cls, v):
        if v is not None and _has_control_characters(v):
            raise ValueError("archiveFileName must not contain control characters")
        return v

    @root_validator(skip_on_failure=True)
    def validate_request(cls, values):
        selection = values.get("selection") or []
        if any(e.type == "asset" for e in selection) and len(selection) > 1:
            raise ValueError("a whole-asset selection must be the only entry")
        if values.get("assetVersionId") and values.get("assetVersionIdAlias"):
            raise ValueError("assetVersionId and assetVersionIdAlias are mutually exclusive")
        pinned = bool(values.get("assetVersionId") or values.get("assetVersionIdAlias"))
        if pinned and any(e.versionId for e in selection):
            raise ValueError("per-file versionId cannot be combined with an asset version pin")
        if values.get("compression") == "deflate" and values.get("archiveFormat") != "zip":
            raise ValueError("compression 'deflate' applies to the zip format only")
        # Collapse exact duplicates, keeping the first occurrence.
        seen, unique = set(), []
        for entry in selection:
            marker = (entry.type, entry.relativePath, entry.versionId or "")
            if marker not in seen:
                seen.add(marker)
                unique.append(entry)
        values["selection"] = unique
        return values


class ListZipDownloadsQueryModel(BaseModel, extra='ignore'):
    """Paging of the list routes (VAMS pagination pattern)."""
    maxItems: int = Field(100, ge=1, le=1000)
    pageSize: int = Field(100, ge=1, le=1000)
    startingToken: Optional[str] = Field(None, min_length=1, max_length=4096)


class ZipSelectionSummaryModel(BaseModel, extra='ignore'):
    files: int = 0
    folders: int = 0
    wholeAsset: bool = False


class ZipProgressModel(BaseModel, extra='ignore'):
    filesDone: int = 0
    filesTotal: int = 0
    bytesDone: int = 0
    bytesTotal: int = 0


class ZipSelectionPreviewEntryModel(BaseModel, extra='ignore'):
    type: str
    relativePath: str
    versionId: Optional[str] = None


class ZipDownloadJobModel(BaseModel, extra='ignore'):
    """One job as the API returns it (common.zipDownloads.to_job_item)."""
    requestId: str
    databaseId: str
    assetId: str
    assetName: str = ""
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    archiveFormat: str
    compression: str
    archiveFileName: str
    archiveSizeBytes: Optional[int] = None
    selectionSummary: ZipSelectionSummaryModel
    selectionPreview: List[ZipSelectionPreviewEntryModel] = []
    assetVersionId: Optional[str] = None
    progress: ZipProgressModel
    errorMessage: Optional[str] = None
    cancelledBy: Optional[str] = None
    createdAt: str
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None
    expiresAt: str = ""


class ZipDownloadListResponseModel(BaseModel, extra='ignore'):
    Items: List[ZipDownloadJobModel] = []
    NextToken: Optional[str] = None


class ZipDownloadUrlResponseModel(BaseModel, extra='ignore'):
    url: str
    expiresIn: int
    archiveFileName: str
    archiveSizeBytes: Optional[int] = None


class ZipDownloadDeleteResponseModel(BaseModel, extra='ignore'):
    message: str
    action: Literal["cancelled", "deleted"]

