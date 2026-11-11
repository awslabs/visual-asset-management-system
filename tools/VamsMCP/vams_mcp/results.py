"""Typed results of the most-used read tools, so each publishes an MCP ``outputSchema``.

A tool annotated with one of these models still returns the plain dict it always returned: the MCP
SDK validates that dict into the model to build the call's ``structuredContent``, and serializes the
dict itself as the text content. So the JSON an agent reads in the text content is unchanged.

Three rules keep the structured copy equal to that dict (``tests/test_typed_results.py``):

* ``extra="allow"``: a field the API adds later, or one this module does not name, is carried
  through rather than dropped.
* every field is optional with a ``None`` default, and :meth:`ToolResult.model_dump` dumps only the
  fields that were SET, so a key the API did not send does not appear as ``null``.
* field types stay loose (``Any`` where the API's type can vary), because a validation failure would
  turn a successful API call into a tool error.

Every model also names the error fields, because ``@tool_result`` returns failures as data
(Mandatory Rule 5) through the same output model. Pydantic here is v2 — the ``mcp`` SDK's own
dependency, in this server's own virtual environment — and never the backend's v1 (root
``CLAUDE.md`` Rule 1).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ToolResult(BaseModel):
    """Base of every typed result: extra keys kept, only the keys that were set are dumped."""

    model_config = ConfigDict(extra="allow")

    error: Optional[str] = Field(None, description="Set when the call failed; then no other data field is set.")
    error_type: Optional[str] = Field(
        None, description="The failure's class, e.g. NotAuthenticated, PermissionDeniedError, APIError."
    )
    user_action: Optional[str] = Field(
        None, description="With NotAuthenticated: the step to ask the USER to take. Do not run it yourself."
    )

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:  # noqa: D102 - see the class docstring
        # The SDK dumps the validated model to build structuredContent. Dumping only the fields that
        # were set keeps that copy equal to the dict the tool returned (no added nulls).
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(**kwargs)


class Page(ToolResult):
    """The shape of every auto-paginated list tool (``VamsClient.paginate``)."""

    Items: Optional[List[Any]] = Field(None, description="The rows this call returned.")
    count: Optional[int] = Field(None, description="Rows in Items. NOT a total when truncated is true.")
    pages: Optional[int] = Field(None, description="API pages read by this call.")
    NextToken: Optional[str] = Field(None, description="Pass back as starting_token to read the next rows.")
    truncated: Optional[bool] = Field(
        None,
        description="True: rows exist that this result does not hold. Do not report count as a total "
        "or conclude something is absent.",
    )
    note: Optional[str] = Field(None, description="Which bound stopped the walk, when truncated.")
    warnings: Optional[List[Any]] = Field(
        None, description="Rows the API withheld (a cap or work budget); the result is then truncated."
    )


class DatabasePage(Page):
    Items: Optional[List[Any]] = Field(
        None, description="Databases: databaseId, description, assetCount, defaultBucketId, dateCreated."
    )


class Database(ToolResult):
    databaseId: Optional[str] = None
    description: Optional[str] = None
    dateCreated: Optional[str] = None
    assetCount: Optional[Any] = Field(None, description="Active assets in the database.")
    defaultBucketId: Optional[str] = None
    bucketName: Optional[str] = None
    baseAssetsPrefix: Optional[str] = None
    restrictMetadataOutsideSchemas: Optional[Any] = Field(
        None, description="True: metadata keys outside a schema are refused."
    )
    restrictFileUploadsToExtensions: Optional[Any] = Field(
        None, description="Comma-separated allowed extensions; empty allows every file."
    )


class AssetPage(Page):
    Items: Optional[List[Any]] = Field(
        None,
        description="Assets: databaseId, assetId, assetName, description, isDistributable, tags, status, "
        "currentVersion.",
    )


class Asset(ToolResult):
    databaseId: Optional[str] = None
    assetId: Optional[str] = None
    assetName: Optional[str] = None
    description: Optional[str] = None
    isDistributable: Optional[Any] = Field(None, description="False: the asset's files may not be downloaded.")
    tags: Optional[Any] = None
    assetType: Optional[Any] = None
    status: Optional[str] = Field(None, description="'active' or 'archived'.")
    currentVersion: Optional[Any] = Field(None, description="The current asset version: Version, Comment, DateModified.")
    assetLocation: Optional[Any] = None
    previewLocation: Optional[Any] = None
    archivedAt: Optional[Any] = None
    archivedBy: Optional[Any] = None
    archivedReason: Optional[Any] = None


class FilePage(Page):
    Items: Optional[List[Any]] = Field(
        None,
        description="Files and folders: fileName, key, relativePath (pass as file_key), isFolder, size, "
        "versionId, isArchived, previewFile, dateCreatedCurrentVersion.",
    )


class VersionPage(Page):
    Items: Optional[List[Any]] = Field(
        None,
        description="Versions: Version (the asset_version_id), Comment, DateModified, createdBy, isCurrent, "
        "fileCount, versionAlias, isArchived.",
    )


class AssetVersion(ToolResult):
    assetId: Optional[str] = None
    assetVersionId: Optional[str] = None
    databaseId: Optional[str] = None
    dateCreated: Optional[str] = None
    comment: Optional[Any] = None
    createdBy: Optional[Any] = None
    files: Optional[List[Any]] = Field(None, description="The version's files: relativeKey, versionId, size.")
    versionedMetadata: Optional[List[Any]] = None


class MetadataPage(Page):
    Items: Optional[List[Any]] = Field(
        None, description="Metadata rows: metadataKey, metadataValue, metadataValueType."
    )


class SearchResult(ToolResult):
    total: Optional[Any] = Field(None, description="Matches the caller may read (numeric).")
    total_is_lower_bound: Optional[bool] = Field(
        None, description="True: the real total is at least total; report total_display ('120+')."
    )
    total_display: Optional[Any] = None
    returned: Optional[int] = Field(None, description="Hits in this page. Page on with from_offset.")
    results: Optional[List[Any]] = Field(
        None, description="Hits: id, score, source (str_databaseid, str_assetid, str_assetname, MD_ ...)."
    )


class ExecutionPage(Page):
    Items: Optional[List[Any]] = Field(
        None,
        description="Executions: workflowExecutionId, workflowId, workflowDatabaseId, executionStatus, "
        "executionStartDate, executionStopDate, triggerType, executionGroupId, outputAssetId, outputTypes.",
    )
    filterStartDate: Optional[Any] = Field(
        None, description="The start-date window applied; older executions are not listed."
    )
    filterEndDate: Optional[Any] = None


class ExecutionDetails(ToolResult):
    executionId: Optional[str] = None
    executionStatus: Optional[str] = None
    executionStartDate: Optional[Any] = None
    executionStopDate: Optional[Any] = None
    executionError: Optional[Any] = Field(None, description="The failure message of a run that did not succeed.")
    triggerType: Optional[Any] = None
    triggeredByUserId: Optional[Any] = None
    workflowVersion: Optional[Any] = None
    workflowVersionCurrent: Optional[Any] = Field(
        None, description="False: the workflow was redeployed since this run."
    )
    pipelines: Optional[List[Any]] = Field(
        None,
        description="Per step: status, error, renderedConfig, renderedConfigLocation, availableLogs "
        "(logId for get_execution_logs), subExecutions.",
    )
    inputFiles: Optional[List[Any]] = None
    inputMetadata: Optional[List[Any]] = None
    inputDatabaseMetadata: Optional[List[Any]] = None
    truncatedCollections: Optional[List[Any]] = Field(
        None, description="Collections cut by the server bound; page them before counting."
    )
    skip: Optional[Any] = Field(None, description="On a SKIPPED execution: why it never ran.")


class ExecutionResults(ToolResult):
    resultContent: Optional[str] = None
    resultContentTruncated: Optional[bool] = None
    resultRowCount: Optional[int] = None
    resultContentBytes: Optional[int] = None
    pageCount: Optional[int] = None
    NextToken: Optional[str] = Field(None, description="More result text remains; pass as starting_token.")
    note: Optional[str] = None


class DownloadUrl(ToolResult):
    downloadUrl: Optional[str] = Field(
        None, description="Presigned URL: a bearer credential until it expires. Bulk: the FIRST success only."
    )
    expiresIn: Optional[Any] = Field(None, description="Seconds until the URL expires.")
    downloadType: Optional[Any] = None
    versionId: Optional[Any] = None
    files: Optional[List[Any]] = Field(
        None, description="Bulk only, one per key: key, success, downloadUrl + versionId or error."
    )
    message: Optional[Any] = None


class Status(ToolResult):
    authenticated: Optional[bool] = Field(None, description="False: ask the user to sign in (see user_action).")
    profile: Optional[str] = None
    apiUrl: Optional[str] = None
    userId: Optional[str] = None
    tokenExpired: Optional[Any] = None
    featureSwitches: Optional[List[Any]] = Field(None, description="Feature switches the profile's last login saw.")
    gates: Optional[Dict[str, Any]] = Field(None, description="Which tool tiers this server registered.")
    mcpVersion: Optional[str] = None
    cliVersion: Optional[str] = None
    reason: Optional[str] = None
    note: Optional[str] = None
