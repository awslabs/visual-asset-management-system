"""
Isaac Sim VAMS Connector - Integrates NVIDIA Isaac Sim with VAMS via the VAMS CLI.

This module provides the IsaacVAMSConnector class for browsing, downloading,
and uploading assets between Isaac Sim and a VAMS deployment. It uses the
VAMS CLI (vamscli) for all operations including authentication.

Prerequisites:
    - VAMS CLI installed from the VAMS repository: pip install -e <path-to-VAMS>/tools/VamsCLI
      (VamsCLI is not published on PyPI)
    - Profile configured: vamscli setup <api-gateway-url>

Usage in Isaac Sim:
    from vams.connector.isaacsim import IsaacVAMSConnector

    connector = IsaacVAMSConnector()
    connector.login("user@example.com", "password")

    databases = connector.list_databases()
    assets = connector.list_assets(databases[0].database_id)
    connector.download_asset(databases[0].database_id, assets[0].asset_id, "/tmp/output")
"""

import contextlib
import logging
import os
import re
import tempfile
import urllib.parse
import urllib.request
from typing import Dict, FrozenSet, List, NamedTuple, Optional, Any, Tuple
from xml.etree import ElementTree

from .vams_cli_service import (
    VamsCliService,
    VamsCliError,
    VamsAuthenticationError,
    VamsNotInstalledError,
    VamsProfileNotSetupError,
    VamsTransferError,
    AuthStatus,
    Database,
    Asset,
    AssetFile,
    DownloadResult,
    Pipeline,
    PipelineTemplate,
    Workflow,
    WorkflowExecution,
)

logger = logging.getLogger(__name__)

# USD layer formats resolve their sublayers, references, payloads and textures as separate files
# relative to the layer; a .usdz package carries its dependencies inside it.
_LAYERED_USD_EXTENSIONS = (".usd", ".usda", ".usdc")
_USD_EXTENSIONS = _LAYERED_USD_EXTENSIONS + (".usdz",)
# Formats that name other files of the asset by relative path: USD layers, and URDF and MJCF meshes.
_DEPENDENT_FILE_EXTENSIONS = _LAYERED_USD_EXTENSIONS + (".urdf", ".mjcf", ".xml")
# An asset path with a URL scheme names a resolver location, not a local file. A Windows drive letter
# reads as a one-character scheme, so a scheme here has at least two.
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")


class UnreferenceableLayerError(VamsCliError):
    """Raised when a USD file has no prim a reference could bring in whole: it has no default prim,
    and not exactly one root prim."""


class MalformedXmlError(VamsCliError):
    """Raised when a file an importer would read is not well-formed XML; ``reason`` is the XML
    parser's message."""

    def __init__(self, file_name: str, reason: str):
        super().__init__(f"{file_name} is not well-formed XML: {reason}")
        self.reason = reason


# What MJCFCreateAsset leaves at its prim path for a file it imports nothing from: these, empty.
_MJCF_EMPTY_IMPORT = frozenset({"Looks", "joints", "worldBody"})


def _prim_name(file_name: str) -> str:
    """A USD prim name made from a file name: its base name without extension, with each character
    other than a letter, digit or underscore replaced by an underscore."""
    base = os.path.splitext(os.path.basename(file_name))[0]
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", base)
    # Prim names cannot start with a digit
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "imported"


def _free_prim_path(stage, prim_path: str) -> str:
    """``prim_path``, or the first of ``<prim_path>_1``, ``<prim_path>_2``, ... with no prim."""
    candidate, suffix = prim_path, 0
    while stage.GetPrimAtPath(candidate).IsValid():
        suffix += 1
        candidate = f"{prim_path}_{suffix}"
    return candidate


@contextlib.contextmanager
def _default_prim_kept(stage):
    """Puts the stage's defaultPrim back as it was, or clears it again, when the block ends. The URDF
    and MJCF importers set it to the name of the prim they create."""
    layer = stage.GetRootLayer()
    default_prim = layer.defaultPrim if layer.HasDefaultPrim() else None
    try:
        yield
    finally:
        if default_prim is None:
            layer.ClearDefaultPrim()
        else:
            layer.defaultPrim = default_prim


def _mjcf_imported_nothing(prim) -> bool:
    """Whether an MJCF import left nothing from its file at ``prim``."""
    if not prim.IsValid():
        return True
    return all(child.GetName() in _MJCF_EMPTY_IMPORT and not child.GetChildren()
               for child in prim.GetChildren())


def _referenced_prim_path(file_name: str, default_prim: str,
                          root_prims: List[str]) -> Optional[str]:
    """
    The prim a reference to a USD file names: None for the file's default prim, or its one root
    prim when it has no default prim.

    A reference with no prim path brings in the default prim, so without one it brings in nothing.
    Naming one of several root prims would drop the others, such as a sibling ``/Materials`` its
    material bindings point at.

    Raises:
        UnreferenceableLayerError: If the file has no default prim and not exactly one root prim.
    """
    if default_prim:
        return None
    if len(root_prims) == 1:
        return f"/{root_prims[0]}"
    raise UnreferenceableLayerError(
        f"{file_name} has no default prim ({len(root_prims)} root prims), so a reference would "
        "bring in nothing. Open it with Open Stage, or set a default prim on the file.")


def _root_prim_names(layer) -> List[str]:
    """The root prims of a layer and of the layers it sublayers, which a reference composes too."""
    from pxr import Sdf

    names, seen, pending = {}, set(), [layer]
    while pending:
        current = pending.pop()
        if current is None or current.identifier in seen:
            continue
        seen.add(current.identifier)
        names.update(dict.fromkeys(prim.name for prim in current.rootPrims))
        pending.extend(Sdf.Layer.FindOrOpen(Sdf.ComputeAssetPathRelativeToLayer(current, path))
                       for path in current.subLayerPaths)
    return list(names)


class _Download(NamedTuple):
    """What ``fetch_local_copy`` left in one directory."""
    # The (database ID, asset ID) downloaded into it; None once it has held two assets.
    owner: Optional[Tuple[str, str]]
    # The downloaded files, by '/'-separated path relative to the directory.
    files: FrozenSet[str]


def _files_under(root: str) -> FrozenSet[str]:
    """The files under ``root``, by '/'-separated path relative to it."""
    return frozenset(
        os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/")
        for directory, _subdirectories, names in os.walk(root)
        for name in names
    )


def _download_error(file_key: str, failed_downloads: List[Dict[str, Any]]) -> Optional[str]:
    """The error a download report gives for ``file_key``, or None if it gives none."""
    key = file_key.lstrip("/")
    for entry in failed_downloads or []:
        if isinstance(entry, dict) and str(entry.get("relative_key") or "").lstrip("/") == key:
            return entry.get("error") or None
    return None


def _local_filesystem_path(asset_path: str) -> Optional[str]:
    """The absolute local path an asset path names, or None for a URL, a relative path or ''."""
    if asset_path.lower().startswith("file:"):
        url = urllib.parse.urlparse(asset_path)
        if url.netloc not in ("", "localhost"):
            return None
        asset_path = urllib.request.url2pathname(url.path)
    elif _URL_SCHEME.match(asset_path):
        return None
    return asset_path if os.path.isabs(asset_path) else None


def _portable_asset_path(asset_path: str, roots: Dict[str, _Download],
                         target: Tuple[str, Optional[str]]) -> Tuple[str, bool]:
    """
    The asset path to author in a stage export uploaded to ``target``.

    The export lands at the root of the target asset, so a file the target asset's own download left
    becomes ``./<its path within the asset>``, which resolves in VAMS and in a whole-asset download.
    Any other local file, including one written into a download directory after the download, keeps
    its path, which resolves only on this machine. A URL or a relative path is left as it is.

    Args:
        asset_path: An asset path as authored in the exported layer.
        roots: Each download directory, mapped to what was downloaded into it.
        target: The (database ID, asset ID) the export is uploaded to; the asset ID is None for a new
                asset.

    Returns:
        The path to author, and whether it resolves on another machine.
    """
    local = _local_filesystem_path(asset_path)
    if local is None:
        return asset_path, True
    local = os.path.normpath(local)

    # The innermost download directory holding the file is the download it came from.
    owner_root, download = None, None
    for root, root_download in roots.items():
        root = os.path.normpath(root)
        try:
            inside = local != root and os.path.commonpath([root, local]) == root
        except ValueError:  # on another drive
            continue
        if inside and (owner_root is None or len(root) > len(owner_root)):
            owner_root, download = root, root_download

    if owner_root is None or download.owner != target:
        return asset_path, False
    relative = os.path.relpath(local, owner_root).replace(os.sep, "/")
    # A path into a package names the package file, then a path inside it in brackets.
    package = relative.split("[", 1)[0] if relative.endswith("]") else None
    if relative not in download.files and package not in download.files:
        return asset_path, False
    return "./" + relative, True


class IsaacVAMSConnector:
    """
    Isaac Sim connector for VAMS asset management via the VAMS CLI.

    Provides methods for authentication, browsing (databases/assets/files),
    downloading assets to local disk, uploading scenes from Isaac Sim, and
    importing downloaded assets into Isaac Sim stages.
    """

    def __init__(self, profile: str = "default", vamscli_path: Optional[str] = None):
        """
        Initialize the connector.

        Args:
            profile: VAMS CLI profile name. Use "default" unless you have
                     multiple VAMS deployments configured.
            vamscli_path: Optional explicit path to the vamscli executable.
        """
        self._cli = VamsCliService(profile=profile, vamscli_path=vamscli_path)
        self._authenticated = False
        # Each directory fetch_local_copy downloaded into, and what the download left there.
        self._download_roots: Dict[str, _Download] = {}

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------

    def login(self, username: str, password: str) -> bool:
        """
        Authenticate with VAMS using Cognito username and password.

        Args:
            username: VAMS username (email).
            password: Cognito password.

        Returns:
            True if authentication succeeded.
        """
        try:
            self._cli.login(username, password)
            self._authenticated = True
            logger.info("Authenticated with VAMS as %s", username)
            return True
        except VamsCliError as e:
            logger.error("Authentication failed: %s", e)
            self._authenticated = False
            return False

    def login_with_token(self, user_id: str, token: str,
                         expires_at: Optional[str] = None) -> bool:
        """
        Authenticate with a token override (IDP JWT token or VAMS API key).

        Uses ``auth login --token-override`` which validates the token against
        the VAMS API. VAMS API keys are prefixed with ``vams_``.

        Args:
            user_id: User ID associated with the token.
            token: IDP JWT token or VAMS API key (prefixed ``vams_``).
            expires_at: Optional expiration (Unix timestamp, ISO 8601, or +seconds).

        Returns:
            True if authentication succeeded.
        """
        try:
            self._cli.login_with_token(user_id, token, expires_at)
            self._authenticated = True
            # Logs only the user id; the token itself is passed to the CLI over stdin and never
            # appears in this message.
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.info("Authenticated with token for user %s", user_id)
            return True
        except VamsCliError as e:
            # Logs the CLI's error text, which is a fixed message plus exit status; the token is
            # passed over stdin and the CLI never echoes it.
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.error("Token authentication failed: %s", e)
            self._authenticated = False
            return False

    def logout(self) -> None:
        """Log out of the current VAMS session."""
        self._cli.logout()
        self._authenticated = False

    @property
    def is_authenticated(self) -> bool:
        """Check if the current session is authenticated."""
        return self._authenticated and self._cli.is_authenticated()

    def get_auth_status(self) -> AuthStatus:
        """Get detailed authentication status."""
        return self._cli.check_authentication()

    # -------------------------------------------------------------------------
    # Browse: Databases, Assets, Files
    # -------------------------------------------------------------------------

    def list_databases(self) -> List[Database]:
        """
        List all accessible VAMS databases.

        Returns:
            List of Database objects.
        """
        return self._cli.list_databases()

    def list_assets(self, database_id: str) -> List[Asset]:
        """
        List all assets in a database.

        Args:
            database_id: The database ID.

        Returns:
            List of Asset objects.
        """
        return self._cli.list_assets(database_id)

    def get_asset(self, database_id: str, asset_id: str) -> Dict[str, Any]:
        """
        Get detailed info for a specific asset.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.

        Returns:
            Dict with asset details.
        """
        return self._cli.get_asset(database_id, asset_id)

    def list_files(self, database_id: str, asset_id: str) -> List[AssetFile]:
        """
        List all files in an asset.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.

        Returns:
            List of AssetFile objects.
        """
        return self._cli.list_files(database_id, asset_id)

    def get_file_info(self, database_id: str, asset_id: str,
                      file_path: str) -> Dict[str, Any]:
        """
        Get detailed info for a specific file within an asset.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_path: Relative path of the file within the asset.

        Returns:
            Dict with file details.
        """
        return self._cli.get_file_info(database_id, asset_id, file_path)

    # -------------------------------------------------------------------------
    # Download
    # -------------------------------------------------------------------------

    def download_file(self, database_id: str, asset_id: str,
                      file_key: str, local_path: str) -> bool:
        """
        Download a single file from a VAMS asset.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_key: File key (relative path) within the asset.
            local_path: Local directory or file path to save to.

        Returns:
            True if the download succeeded.

        Raises:
            VamsCliError: If the download failed; a ``VamsAccessDeniedError`` when VAMS refused the
                          credential, and a ``VamsTransferError`` naming the error the download
                          report gives for the file when it gives one.
        """
        try:
            return self._cli.download_file(local_path, database_id, asset_id, file_key)
        except VamsTransferError as e:
            reason = _download_error(file_key, e.payload.get("failed_downloads"))
            logger.error("Download failed for %s: %s", file_key, reason or e)
            if reason is None:
                raise
            raise VamsTransferError(f"{file_key} did not download: {reason}", payload=e.payload,
                                    exit_code=e.exit_code) from e
        except VamsCliError as e:
            logger.error("Download failed for %s: %s", file_key, e)
            raise

    def download_asset(self, database_id: str, asset_id: str,
                       local_path: str) -> DownloadResult:
        """
        Download all files from a VAMS asset recursively.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            local_path: Local directory to save files into.

        Returns:
            DownloadResult with download statistics.
        """
        return self._cli.download_asset(local_path, database_id, asset_id)

    def fetch_local_copy(self, database_id: str, asset_id: str, file_key: str,
                         local_dir: Optional[str] = None) -> Tuple[str, int]:
        """
        Download a file for Isaac Sim to open, with the files it depends on.

        A file whose format names other files by relative path (a USD layer, URDF, MJCF) brings the
        whole asset, laid out as it is in VAMS, so those paths resolve locally. Any other file,
        including a .usdz package, is downloaded alone. The directory is recorded with the asset and
        the files the download left there, so a stage export can name those files relative to the
        asset (see ``export_and_upload_scene``).

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_key: File key (relative path) within the asset.
            local_dir: Local directory to download to. Uses a new private temp dir if None.

        Returns:
            The local path of ``file_key`` and the number of the asset's other files that did not
            download, whose references therefore do not resolve.

        Raises:
            VamsCliError: If ``file_key`` itself was not downloaded, naming the error the download
                          gave for it when it gave one.
        """
        if local_dir is None:
            local_dir = tempfile.mkdtemp(prefix="vams_")
        root = os.path.abspath(local_dir)
        local_file = os.path.abspath(os.path.join(root, file_key.lstrip("/")))
        if os.path.commonpath([root, local_file]) != root:
            raise VamsCliError(f"File key resolves outside the download directory: {file_key}")

        missing = 0
        if file_key.lower().endswith(_DEPENDENT_FILE_EXTENSIONS):
            result = self._cli.download_asset(root, database_id, asset_id)
            reason = _download_error(file_key, result.failed_downloads)
            if reason is not None:
                raise VamsCliError(f"{file_key} did not download: {reason}")
            missing = result.failed_files
            if missing:
                logger.warning("%d of %d files of asset %s did not download; references to them "
                               "from %s will not resolve", missing, result.total_files, asset_id,
                               file_key)
        elif not self.download_file(database_id, asset_id, file_key, root):
            raise VamsCliError(f"Download failed: {file_key}")

        if not os.path.isfile(local_file):
            raise VamsCliError(f"Downloaded file not found: {file_key}")

        owner, files = (database_id, asset_id), _files_under(root)
        recorded = self._download_roots.get(root)
        if recorded is not None:
            owner = owner if recorded.owner == owner else None
            files |= recorded.files
        self._download_roots[root] = _Download(owner, files)
        return local_file, missing

    # -------------------------------------------------------------------------
    # Upload
    # -------------------------------------------------------------------------

    def upload_file(self, database_id: str, asset_id: str,
                    file_path: str) -> Dict[str, Any]:
        """
        Upload a local file to a VAMS asset.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_path: Local path to the file to upload.

        Returns:
            Dict with upload result.
        """
        return self._cli.upload_file(file_path, database_id, asset_id)

    def upload_directory(self, database_id: str, asset_id: str,
                         directory_path: str, recursive: bool = True) -> Dict[str, Any]:
        """
        Upload a directory of files to a VAMS asset.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            directory_path: Local directory to upload.
            recursive: Whether to include subdirectories.

        Returns:
            Dict with upload result.
        """
        return self._cli.upload_directory(directory_path, database_id, asset_id, recursive)

    def create_and_upload(self, database_id: str, asset_name: str,
                          file_path: str, description: str = "") -> Dict[str, Any]:
        """
        Create a new asset and upload a file to it in one step.

        Args:
            database_id: The database ID.
            asset_name: Name for the new asset.
            file_path: Local file to upload.
            description: Optional asset description.

        Returns:
            Dict with created asset info including assetId.
        """
        asset_info = self._cli.create_asset(database_id, asset_name, description)
        asset_id = asset_info.get("assetId")
        if not asset_id:
            raise VamsCliError("Failed to create asset: no assetId returned")

        self._cli.upload_file(file_path, database_id, asset_id)
        return asset_info

    # -------------------------------------------------------------------------
    # Workflows
    # -------------------------------------------------------------------------

    def list_workflows(self, database_id: Optional[str] = None,
                       include_unrunnable: bool = False) -> List[Workflow]:
        """
        List available workflows.

        Args:
            database_id: Optional database ID to filter workflows.
                         If None, lists workflows across all databases.
            include_unrunnable: Keep disabled workflows in the result. They are dropped by default
                                because executing one is rejected; archived workflows are already
                                absent from the listing.

        Returns:
            List of Workflow objects.
        """
        return self._cli.list_workflows(database_id, include_unrunnable=include_unrunnable)

    def list_workflow_executions(
        self,
        database_id: str,
        asset_id: str,
        workflow_id: Optional[str] = None,
        workflow_database_id: Optional[str] = None,
    ) -> List[WorkflowExecution]:
        """
        List workflow executions for an asset.

        Args:
            database_id: The database ID containing the asset.
            asset_id: The asset ID.
            workflow_id: Optional workflow ID to filter by.
            workflow_database_id: Optional workflow's database ID to filter by.

        Returns:
            List of WorkflowExecution objects.
        """
        return self._cli.list_workflow_executions(
            database_id, asset_id, workflow_id, workflow_database_id
        )

    def execute_workflow(
        self,
        database_id: str,
        asset_id: str,
        workflow_id: str,
        workflow_database_id: str,
        file_key: Optional[str] = None,
        pipeline_parameters: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a workflow on one file of an asset, or on the whole asset.

        Args:
            database_id: The database ID containing the asset.
            asset_id: The asset ID to run the workflow on.
            workflow_id: The workflow ID to execute.
            workflow_database_id: The database ID that owns the workflow.
            file_key: Optional relative file key within the asset. If not specified,
                      the workflow runs on the whole asset ("/").
            pipeline_parameters: Optional per-pipeline execution parameters, keyed by
                                 pipelineId, such as ``{"my-pipeline": {"templateId": "t1"}}``.

        Returns:
            Dict with execution result (includes 'executionId', and 'warnings' when
            the run started with a bounded metadata capture).
        """
        return self._cli.execute_workflow(
            database_id, asset_id, workflow_id, workflow_database_id, file_key,
            pipeline_parameters=pipeline_parameters,
        )

    def get_pipeline(self, database_id: str, pipeline_id: str) -> Pipeline:
        """
        Get a pipeline record.

        Args:
            database_id: The database ID that owns the pipeline.
            pipeline_id: The pipeline ID.

        Returns:
            A Pipeline.
        """
        return self._cli.get_pipeline(database_id, pipeline_id)

    def list_pipeline_templates(self, database_id: str,
                                pipeline_id: str) -> List[PipelineTemplate]:
        """
        List a pipeline's templates.

        Args:
            database_id: The database ID that owns the pipeline.
            pipeline_id: The pipeline ID.

        Returns:
            List of PipelineTemplate objects.
        """
        return self._cli.list_pipeline_templates(database_id, pipeline_id)

    # -------------------------------------------------------------------------
    # Isaac Sim Integration
    # -------------------------------------------------------------------------

    def export_and_upload_scene(self, database_id: str, asset_name: str,
                                export_path: Optional[str] = None,
                                asset_id: Optional[str] = None,
                                description: str = "") -> Dict[str, Any]:
        """
        Export the current Isaac Sim USD stage and upload it to VAMS.

        If asset_id is provided, uploads to that existing asset.
        Otherwise, creates a new asset with asset_name.

        The export is flattened into one layer whose asset paths point at the files this machine
        opened. A path to a file a download of the target asset left (see ``fetch_local_copy``) is
        rewritten relative to the asset root, where the export is uploaded, so it resolves in VAMS
        and in a whole-asset download. Any other local file path is left as it is and counted.

        Args:
            database_id: The database ID to upload to.
            asset_name: Name for the asset (used if creating new).
            export_path: Local path to export the USD file to.
                         If None, uses a temp file.
            asset_id: Optional existing asset ID to upload into.
            description: Optional asset description (for new assets).

        Returns:
            Dict with asset info, and ``unportablePaths``: the number of distinct local file paths in
            the export that are outside the target asset, so resolve only on this machine.
        """
        import omni.usd
        from pxr import Sdf, UsdUtils

        if export_path is None:
            export_path = os.path.join(tempfile.mkdtemp(prefix="vams_export_"), f"{asset_name}.usd")

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise VamsCliError("No active USD stage in Isaac Sim")

        logger.info("Exporting stage to %s", export_path)
        if not stage.Export(export_path) or not os.path.isfile(export_path):
            raise VamsCliError(f"Stage export failed: file not created at {export_path}")

        # A layer already open under the export path holds what it held before the export.
        layer = Sdf.Layer.FindOrOpen(export_path)
        if layer is None or not layer.Reload(force=True):
            raise VamsCliError(f"Stage export failed: cannot open {export_path}")
        target = (database_id, asset_id)
        unportable = set()

        def _remap(asset_path):
            new_path, portable = _portable_asset_path(asset_path, self._download_roots, target)
            if not portable:
                unportable.add(asset_path)
            return new_path

        UsdUtils.ModifyAssetPaths(layer, _remap)
        if not layer.Save():
            raise VamsCliError(f"Stage export failed: cannot save {export_path}")
        if unportable:
            logger.warning("%d file path(s) in %s are outside the asset and resolve only on this "
                           "machine: %s", len(unportable), export_path, ", ".join(sorted(unportable)))

        if asset_id:
            self._cli.upload_file(export_path, database_id, asset_id)
            return {"assetId": asset_id, "uploaded": export_path,
                    "unportablePaths": len(unportable)}
        else:
            result = self.create_and_upload(database_id, asset_name, export_path, description)
            return {**result, "unportablePaths": len(unportable)}

    def download_and_import_asset(self, database_id: str, asset_id: str,
                                   file_key: str,
                                   local_dir: Optional[str] = None) -> bool:
        """
        Download a file from VAMS and open it as a new stage in Isaac Sim.

        A USD layer brings the whole asset so its sublayers, references and textures resolve (see
        ``fetch_local_copy``).

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_key: File key of the USD/USDZ file within the asset.
            local_dir: Local directory to download to. Uses temp dir if None.

        Returns:
            True if the file was downloaded and stage opened successfully.
        """
        import omni.usd

        if not file_key.lower().endswith(_USD_EXTENSIONS):
            logger.error(
                "Cannot open non-USD file as stage: %s (supported: %s)",
                file_key, ", ".join(_USD_EXTENSIONS),
            )
            return False

        try:
            local_file, _missing = self.fetch_local_copy(database_id, asset_id, file_key, local_dir)
        except VamsCliError as e:
            logger.error("Download failed for %s: %s", file_key, e)
            return False

        logger.info("Opening stage from %s", local_file)
        result = omni.usd.get_context().open_stage(local_file)
        return result

    def download_and_add_reference(self, database_id: str, asset_id: str,
                                    file_key: str, prim_path: str,
                                    local_dir: Optional[str] = None) -> bool:
        """
        Download a file from VAMS and add it as a reference to the current stage.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_key: File key of the USD file within the asset.
            prim_path: Prim path in the current stage to add the reference to. A prim already
                       there keeps its references and gets this one as well.
            local_dir: Local directory to download to. Uses temp dir if None.

        Returns:
            True if the reference was added. False, with the reason logged, if the file did not
            download or has no prim a reference could bring in (see ``add_reference_to_stage``).
        """
        import omni.usd
        from pxr import Sdf  # noqa: F401 - ensures pxr is available

        if not file_key.lower().endswith(_USD_EXTENSIONS):
            logger.error(
                "Cannot add non-USD file as reference: %s (supported: %s)",
                file_key, ", ".join(_USD_EXTENSIONS),
            )
            return False

        try:
            local_file, _missing = self.fetch_local_copy(database_id, asset_id, file_key, local_dir)
        except VamsCliError as e:
            logger.error("Download failed for %s: %s", file_key, e)
            return False

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise VamsCliError("No active USD stage in Isaac Sim")

        try:
            self.add_reference_to_stage(stage, prim_path, local_file)
        except VamsCliError as e:
            logger.error("Cannot add %s as a reference: %s", file_key, e)
            return False
        return True

    def add_reference_to_stage(self, stage, prim_path: str, local_file: str) -> None:
        """
        Reference a local USD file at ``prim_path`` of ``stage``, defining the prim if it is not
        there. A prim already at ``prim_path`` keeps its references and gets this one as well.

        The reference names the file's default prim, or its one root prim when it has no default
        prim. The prim is defined only once that is settled, so a file that is refused leaves the
        stage as it was.

        Args:
            stage: The USD stage to add the reference to.
            prim_path: Prim path in the stage to add the reference to.
            local_file: Local path of the USD file.

        Raises:
            UnreferenceableLayerError: If the file has no default prim and not exactly one root
                                       prim, so a reference would bring in nothing, or would drop
                                       the other root prims.
            VamsCliError: If the file does not open as a USD layer.
        """
        from pxr import Sdf

        file_name = os.path.basename(local_file)
        layer = Sdf.Layer.FindOrOpen(local_file)
        if layer is None:
            raise VamsCliError(f"Cannot open {file_name} as a USD layer")
        target = _referenced_prim_path(file_name, layer.defaultPrim, _root_prim_names(layer))

        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            prim = stage.DefinePrim(prim_path)
        if target:
            prim.GetReferences().AddReference(local_file, Sdf.Path(target))
        else:
            prim.GetReferences().AddReference(local_file)
        logger.info("Added reference %s%s at prim %s", local_file,
                    f"<{target}>" if target else "", prim_path)

    def download_and_import_urdf(
        self,
        database_id: str,
        asset_id: str,
        file_key: str,
        local_dir: Optional[str] = None,
        fix_base: bool = True,
        merge_fixed_joints: bool = False,
        import_inertia_tensor: bool = True,
    ) -> Optional[str]:
        """
        Download a URDF file from VAMS and import it into the current Isaac Sim stage, under the
        prim the stage's defaultPrim names (see ``import_urdf_file``).

        Requires the ``isaacsim.asset.importer.urdf`` extension to be enabled in
        Isaac Sim.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_key: File key of the ``.urdf`` file within the asset.
            local_dir: Local directory to download to. Uses temp dir if None.
            fix_base: Create a fixed joint for the base link.
            merge_fixed_joints: Consolidate links connected by fixed joints.
            import_inertia_tensor: Import inertia tensor data from the URDF.

        Returns:
            The prim path of the imported robot, or None on failure.
        """
        _URDF_EXTENSIONS = (".urdf",)
        if not file_key.lower().endswith(_URDF_EXTENSIONS):
            logger.error("Not a URDF file: %s", file_key)
            return None

        if local_dir is None:
            local_dir = tempfile.mkdtemp(prefix="vams_urdf_")

        try:
            local_file, _missing = self.fetch_local_copy(database_id, asset_id, file_key, local_dir)
        except VamsCliError as e:
            logger.error("URDF download failed for %s: %s", file_key, e)
            return None

        return self.import_urdf_file(local_file, fix_base, merge_fixed_joints,
                                     import_inertia_tensor)

    def import_urdf_file(self, local_file: str, fix_base: bool = True,
                         merge_fixed_joints: bool = False,
                         import_inertia_tensor: bool = True) -> Optional[str]:
        """
        Import a local URDF file into the current Isaac Sim stage.

        The importer puts the robot under the prim the stage's defaultPrim names, or at the stage
        root when it names none, and at ``<name>_0`` when ``<name>`` is taken. The stage's
        defaultPrim is left as it was.

        Args:
            local_file: Local path of the ``.urdf`` file.
            fix_base: Create a fixed joint for the base link.
            merge_fixed_joints: Consolidate links connected by fixed joints.
            import_inertia_tensor: Import inertia tensor data from the URDF.

        Returns:
            The prim path of the imported robot, or None on failure.

        Raises:
            VamsCliError: If there is no active USD stage.
        """
        import omni.kit.commands
        import omni.usd
        from isaacsim.asset.importer.urdf import _urdf

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise VamsCliError("No active USD stage in Isaac Sim")

        import_config = _urdf.ImportConfig()
        import_config.set_fix_base(fix_base)
        import_config.set_merge_fixed_joints(merge_fixed_joints)
        import_config.set_import_inertia_tensor(import_inertia_tensor)

        logger.info("Importing URDF from %s", local_file)
        with _default_prim_kept(stage):
            status, prim_path = omni.kit.commands.execute(
                "URDFParseAndImportFile",
                urdf_path=local_file,
                import_config=import_config,
            )
        prim_path = prim_path if status else None
        if prim_path:
            logger.info("Imported URDF as prim %s", prim_path)
        else:
            logger.error("URDF import failed for %s", local_file)
        return prim_path

    def download_and_import_mjcf(
        self,
        database_id: str,
        asset_id: str,
        file_key: str,
        local_dir: Optional[str] = None,
        fix_base: bool = True,
        merge_fixed_joints: bool = False,
        import_inertia_tensor: bool = True,
    ) -> Optional[str]:
        """
        Download a MJCF file from VAMS and import it into the current Isaac Sim stage, under
        ``/World`` (see ``import_mjcf_file``).

        Requires the ``isaacsim.asset.importer.mjcf`` extension to be enabled in
        Isaac Sim.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_key: File key of the ``.mjcf`` or ``.xml`` file within the asset.
            local_dir: Local directory to download to. Uses temp dir if None.
            fix_base: Create a fixed joint for the base link.
            merge_fixed_joints: Consolidate links connected by fixed joints.
            import_inertia_tensor: Import inertia tensor data from the MJCF.

        Returns:
            The prim path of the imported model, or None on failure.
        """
        _MJCF_EXTENSIONS = (".mjcf", ".xml")
        if not file_key.lower().endswith(_MJCF_EXTENSIONS):
            logger.error("Not a MJCF file: %s", file_key)
            return None

        if local_dir is None:
            local_dir = tempfile.mkdtemp(prefix="vams_mjcf_")

        try:
            local_file, _missing = self.fetch_local_copy(database_id, asset_id, file_key, local_dir)
        except VamsCliError as e:
            logger.error("MJCF download failed for %s: %s", file_key, e)
            return None

        try:
            return self.import_mjcf_file(local_file, fix_base, merge_fixed_joints,
                                         import_inertia_tensor)
        except MalformedXmlError as e:
            logger.error("Cannot import %s: %s", file_key, e)
            return None

    def import_mjcf_file(self, local_file: str, fix_base: bool = True,
                         merge_fixed_joints: bool = False,
                         import_inertia_tensor: bool = True) -> Optional[str]:
        """
        Import a local MJCF file into the current Isaac Sim stage, under ``/World`` at a prim named
        after the file: ``<name>``, or the first of ``<name>_1``, ``<name>_2``, ... that is free.

        A file that is not well-formed XML is refused before the importer runs. An import that
        brings in nothing from the file is removed again, with the ``/World`` it created for it.
        The stage's defaultPrim is left as it was.

        Args:
            local_file: Local path of the ``.mjcf`` or ``.xml`` file.
            fix_base: Create a fixed joint for the base link.
            merge_fixed_joints: Consolidate links connected by fixed joints.
            import_inertia_tensor: Import inertia tensor data from the MJCF.

        Returns:
            The prim path of the imported model, or None if the import brought in nothing.

        Raises:
            MalformedXmlError: If the file is not well-formed XML; the stage is not touched.
            VamsCliError: If there is no active USD stage.
        """
        import omni.kit.commands
        import omni.usd
        from isaacsim.asset.importer.mjcf import _mjcf

        try:
            ElementTree.parse(local_file)
        except ElementTree.ParseError as e:
            raise MalformedXmlError(os.path.basename(local_file), str(e)) from e
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise VamsCliError("No active USD stage in Isaac Sim")

        import_config = _mjcf.ImportConfig()
        import_config.set_fix_base(fix_base)
        import_config.set_merge_fixed_joints(merge_fixed_joints)
        import_config.set_import_inertia_tensor(import_inertia_tensor)

        world_existed = stage.GetPrimAtPath("/World").IsValid()
        prim_path = _free_prim_path(stage, f"/World/{_prim_name(local_file)}")
        logger.info("Importing MJCF from %s as %s", local_file, prim_path)
        with _default_prim_kept(stage):
            # The importer reports success, and creates the prim, whether or not it imports anything.
            status, _result = omni.kit.commands.execute(
                "MJCFCreateAsset",
                mjcf_path=local_file,
                import_config=import_config,
                prim_path=prim_path,
            )
            if status and not _mjcf_imported_nothing(stage.GetPrimAtPath(prim_path)):
                logger.info("Imported MJCF as prim %s", prim_path)
                return prim_path
            stage.RemovePrim(prim_path if world_existed else "/World")
        logger.error("MJCF import of %s brought in nothing", local_file)
        return None
