"""
Isaac Sim VAMS Connector - Integrates NVIDIA Isaac Sim with VAMS via the VAMS CLI.

This module provides the IsaacVAMSConnector class for browsing, downloading,
and uploading assets between Isaac Sim and a VAMS deployment. It uses the
VAMS CLI (vamscli) for all operations including authentication.

Prerequisites:
    - VAMS CLI installed: pip install vamscli (or pip install -e <path-to-VamsCLI>)
    - Profile configured: vamscli setup <api-gateway-url>

Usage in Isaac Sim:
    from vams.connector.isaacsim import IsaacVAMSConnector

    connector = IsaacVAMSConnector()
    connector.login("user@example.com", "password")

    databases = connector.list_databases()
    assets = connector.list_assets(databases[0].database_id)
    connector.download_asset(databases[0].database_id, assets[0].asset_id, "/tmp/output")
"""

import logging
import os
import tempfile
from typing import Dict, List, Optional, Any

from .vams_cli_service import (
    VamsCliService,
    VamsCliError,
    VamsAuthenticationError,
    VamsNotInstalledError,
    VamsProfileNotSetupError,
    AuthStatus,
    Database,
    Asset,
    AssetFile,
    DownloadResult,
    Workflow,
    WorkflowExecution,
)

logger = logging.getLogger(__name__)


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
            logger.info("Authenticated with token for user %s", user_id)
            return True
        except VamsCliError as e:
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
        """
        try:
            return self._cli.download_file(local_path, database_id, asset_id, file_key)
        except VamsCliError as e:
            logger.error("Download failed for %s: %s", file_key, e)
            return False

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

    def list_workflows(self, database_id: Optional[str] = None) -> List[Workflow]:
        """
        List available workflows.

        Args:
            database_id: Optional database ID to filter workflows.
                         If None, lists workflows across all databases.

        Returns:
            List of Workflow objects.
        """
        return self._cli.list_workflows(database_id)

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
    ) -> Dict[str, Any]:
        """
        Execute a workflow on an asset.

        Args:
            database_id: The database ID containing the asset.
            asset_id: The asset ID to run the workflow on.
            workflow_id: The workflow ID to execute.
            workflow_database_id: The database ID that owns the workflow.
            file_key: Optional file key within the asset. If not specified,
                      the workflow runs on the top-level asset ("/").

        Returns:
            Dict with execution result (includes execution ID in 'message' field).
        """
        return self._cli.execute_workflow(
            database_id, asset_id, workflow_id, workflow_database_id, file_key
        )

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

        Args:
            database_id: The database ID to upload to.
            asset_name: Name for the asset (used if creating new).
            export_path: Local path to export the USD file to.
                         If None, uses a temp file.
            asset_id: Optional existing asset ID to upload into.
            description: Optional asset description (for new assets).

        Returns:
            Dict with asset info.
        """
        import omni.usd

        if export_path is None:
            export_path = os.path.join(tempfile.gettempdir(), f"{asset_name}.usd")

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise VamsCliError("No active USD stage in Isaac Sim")

        logger.info("Exporting stage to %s", export_path)
        stage.Export(export_path)

        if not os.path.isfile(export_path):
            raise VamsCliError(f"Stage export failed: file not created at {export_path}")

        if asset_id:
            self._cli.upload_file(export_path, database_id, asset_id)
            return {"assetId": asset_id, "uploaded": export_path}
        else:
            return self.create_and_upload(database_id, asset_name, export_path, description)

    def download_and_import_asset(self, database_id: str, asset_id: str,
                                   file_key: str,
                                   local_dir: Optional[str] = None) -> bool:
        """
        Download a file from VAMS and open it as a new stage in Isaac Sim.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_key: File key of the USD/USDZ file within the asset.
            local_dir: Local directory to download to. Uses temp dir if None.

        Returns:
            True if the file was downloaded and stage opened successfully.
        """
        import omni.usd

        if local_dir is None:
            local_dir = tempfile.mkdtemp(prefix="vams_")

        success = self.download_file(database_id, asset_id, file_key, local_dir)
        if not success:
            return False

        # Use the full relative path (not just basename) to match the CLI's
        # download layout, which preserves subdirectory structure.
        local_file = os.path.abspath(os.path.join(local_dir, file_key.lstrip("/")))

        if not os.path.isfile(local_file):
            logger.error("Downloaded file not found at %s", local_file)
            return False

        # Validate that the file is a supported USD format before opening as a stage
        _USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
        if not local_file.lower().endswith(_USD_EXTENSIONS):
            logger.error(
                "Cannot open non-USD file as stage: %s (supported: %s)",
                local_file, ", ".join(_USD_EXTENSIONS),
            )
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
            prim_path: Prim path in the current stage to add the reference to.
            local_dir: Local directory to download to. Uses temp dir if None.

        Returns:
            True if the reference was added successfully.
        """
        import omni.usd
        from pxr import Sdf  # noqa: F401 - ensures pxr is available

        if local_dir is None:
            local_dir = tempfile.mkdtemp(prefix="vams_")

        success = self.download_file(database_id, asset_id, file_key, local_dir)
        if not success:
            return False

        # Use the full relative path (not just basename) to match the CLI's
        # download layout, which preserves subdirectory structure.
        local_file = os.path.abspath(os.path.join(local_dir, file_key.lstrip("/")))

        if not os.path.isfile(local_file):
            logger.error("Downloaded file not found at %s", local_file)
            return False

        # Validate that the file is a supported USD format before adding as a reference
        _USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
        if not local_file.lower().endswith(_USD_EXTENSIONS):
            logger.error(
                "Cannot add non-USD file as reference: %s (supported: %s)",
                local_file, ", ".join(_USD_EXTENSIONS),
            )
            return False

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            raise VamsCliError("No active USD stage in Isaac Sim")

        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            prim = stage.DefinePrim(prim_path)

        references = prim.GetReferences()
        references.AddReference(local_file)
        logger.info("Added reference %s at prim %s", local_file, prim_path)
        return True

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
        Download a URDF file from VAMS and import it into the current Isaac Sim stage.

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

        success = self.download_file(database_id, asset_id, file_key, local_dir)
        if not success:
            return None

        local_file = os.path.abspath(os.path.join(local_dir, file_key.lstrip("/")))
        if not os.path.isfile(local_file):
            logger.error("Downloaded URDF file not found at %s", local_file)
            return None

        import omni.kit.commands
        from isaacsim.asset.importer.urdf import _urdf

        import_config = _urdf.ImportConfig()
        import_config.set_fix_base(fix_base)
        import_config.set_merge_fixed_joints(merge_fixed_joints)
        import_config.set_import_inertia_tensor(import_inertia_tensor)

        logger.info("Importing URDF from %s", local_file)
        status, prim_path = omni.kit.commands.execute(
            "URDFParseAndImportFile",
            urdf_path=local_file,
            import_config=import_config,
        )
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
    ) -> bool:
        """
        Download a MJCF file from VAMS and import it into the current Isaac Sim stage.

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
            True if the import succeeded.
        """
        _MJCF_EXTENSIONS = (".mjcf", ".xml")
        if not file_key.lower().endswith(_MJCF_EXTENSIONS):
            logger.error("Not a MJCF file: %s", file_key)
            return False

        if local_dir is None:
            local_dir = tempfile.mkdtemp(prefix="vams_mjcf_")

        success = self.download_file(database_id, asset_id, file_key, local_dir)
        if not success:
            return False

        local_file = os.path.abspath(os.path.join(local_dir, file_key.lstrip("/")))
        if not os.path.isfile(local_file):
            logger.error("Downloaded MJCF file not found at %s", local_file)
            return False

        import omni.kit.commands
        from isaacsim.asset.importer.mjcf import _mjcf

        import_config = _mjcf.ImportConfig()
        import_config.set_fix_base(fix_base)
        import_config.set_merge_fixed_joints(merge_fixed_joints)
        import_config.set_import_inertia_tensor(import_inertia_tensor)

        import re
        _base = os.path.splitext(os.path.basename(file_key))[0]
        _safe = re.sub(r"[^a-zA-Z0-9_]", "_", _base)
        if _safe and _safe[0].isdigit():
            _safe = "_" + _safe
        prim_name = "/" + (_safe or "imported")
        logger.info("Importing MJCF from %s as %s", local_file, prim_name)
        omni.kit.commands.execute(
            "MJCFCreateAsset",
            mjcf_path=local_file,
            import_config=import_config,
            prim_path=prim_name,
        )
        logger.info("Imported MJCF as prim %s", prim_name)
        return True
