"""
VAMS CLI Service - Python wrapper around the VAMS CLI tool (vamscli).

Provides typed Python methods for VAMS operations by executing CLI commands
via subprocess and parsing their JSON output.

Prerequisites:
    - VAMS CLI installed and on PATH (pip install vamscli or pip install -e path/to/VamsCLI)
    - Profile configured via: vamscli setup <api-gateway-url>
"""

import json
import logging
import os
import subprocess
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class VamsCliError(Exception):
    """Raised when a VAMS CLI command fails."""

    def __init__(self, message: str, error_type: str = "Error", exit_code: int = 1):
        self.error_type = error_type
        self.exit_code = exit_code
        super().__init__(message)


class VamsAuthenticationError(VamsCliError):
    """Raised when authentication fails or token is expired."""
    pass


class VamsNotInstalledError(VamsCliError):
    """Raised when the vamscli executable is not found."""
    pass


class VamsProfileNotSetupError(VamsCliError):
    """Raised when the VAMS CLI profile is not configured."""
    pass


@dataclass
class AuthStatus:
    """Parsed authentication status from vamscli auth status."""
    authenticated: bool = False
    authentication_type: str = ""
    user_id: str = ""
    is_expired: bool = True
    expires_at: str = ""
    web_deployed_url: str = ""
    success: bool = False


@dataclass
class Database:
    """Parsed database entry from vamscli database list."""
    database_id: str = ""
    description: str = ""
    date_created: str = ""
    asset_count: int = 0


@dataclass
class Asset:
    """Parsed asset entry from vamscli assets list."""
    asset_id: str = ""
    asset_name: str = ""
    database_id: str = ""
    description: str = ""
    is_distributable: bool = False
    status: str = ""
    tags: List[str] = field(default_factory=list)
    current_version: Optional[Dict[str, Any]] = None


@dataclass
class AssetFile:
    """Parsed file entry from vamscli file list."""
    file_name: str = ""
    relative_path: str = ""
    size: int = 0
    is_folder: bool = False
    is_archived: bool = False
    primary_type: str = ""
    content_type: str = ""
    last_modified: str = ""
    preview_file: str = ""


@dataclass
class DownloadResult:
    """Parsed download response from vamscli assets download."""
    overall_success: bool = False
    total_files: int = 0
    successful_files: int = 0
    failed_files: int = 0
    total_size: int = 0
    total_size_formatted: str = ""
    successful_downloads: List[Dict[str, Any]] = field(default_factory=list)
    failed_downloads: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Workflow:
    """Parsed workflow entry from vamscli workflow list."""
    workflow_id: str = ""
    database_id: str = ""
    description: str = ""
    workflow_arn: str = ""
    auto_trigger_extensions: str = ""


@dataclass
class WorkflowExecution:
    """Parsed workflow execution entry from vamscli workflow list-executions."""
    execution_id: str = ""
    workflow_id: str = ""
    workflow_database_id: str = ""
    execution_status: str = ""
    start_date: str = ""
    stop_date: str = ""
    input_file_key: str = ""


class VamsCliService:
    """
    Service wrapper around the VAMS CLI tool (vamscli).

    Executes CLI commands via subprocess, parses JSON output, and provides
    typed Python methods for all VAMS operations.
    """

    def __init__(self, profile: str = "default", vamscli_path: Optional[str] = None):
        """
        Initialize the VAMS CLI service.

        Args:
            profile: VAMS CLI profile name (default: "default").
            vamscli_path: Optional explicit path to the vamscli executable.
                          If not provided, expects 'vamscli' to be on PATH.
        """
        self._profile = profile
        self._vamscli = vamscli_path or "vamscli"
        self._cached_username: Optional[str] = None
        self._cached_credential: Optional[str] = None
        self._cached_is_token_auth: bool = False
        self._cached_auth_type: Optional[str] = None
        self._web_deployed_url: Optional[str] = None

        self._verify_cli_installed()

    def _verify_cli_installed(self) -> None:
        """Verify that the vamscli executable is available."""
        if shutil.which(self._vamscli) is None and not os.path.isfile(self._vamscli):
            raise VamsNotInstalledError(
                f"VAMS CLI not found at '{self._vamscli}'. "
                "Install it with: pip install vamscli (or pip install -e path/to/VamsCLI)"
            )

    def _execute_command(self, args: List[str]) -> str:
        """
        Execute a vamscli command and return its stdout.

        Args:
            args: List of command arguments (e.g., ["auth", "status", "--json-output"]).
                  No shell escaping needed — arguments are passed directly to the process.

        Returns:
            The stdout output of the command.

        Raises:
            VamsCliError: If the command exits with a non-zero code.
        """
        cmd = [self._vamscli]
        if self._profile != "default":
            cmd += ["--profile", self._profile]
        cmd += args

        logger.debug("Executing: %s", cmd)

        try:
            # nosemgrep: dangerous-subprocess-use-audit
            result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise VamsCliError("Command timed out after 600 seconds")
        except FileNotFoundError:
            raise VamsNotInstalledError(
                f"VAMS CLI not found: '{self._vamscli}'. "
                "Install it with: pip install vamscli"
            )

        logger.debug("Exit code: %d", result.returncode)
        logger.debug("stdout: %s", result.stdout[:500] if result.stdout else "")
        logger.debug("stderr: %s", result.stderr[:500] if result.stderr else "")

        if result.returncode != 0:
            error_msg, error_type = self._try_parse_error(result.stdout)
            if error_msg:
                raise VamsCliError(error_msg, error_type=error_type, exit_code=result.returncode)
            raise VamsCliError(
                f"Command failed (exit {result.returncode}): {result.stderr.strip()}",
                exit_code=result.returncode,
            )

        # Check successful output for embedded error fields
        error_msg, error_type = self._try_parse_error(result.stdout)
        if error_msg:
            raise VamsCliError(error_msg, error_type=error_type)

        return result.stdout

    def _try_parse_error(self, output: str) -> tuple:
        """
        Try to parse a JSON error response from CLI output.

        Returns:
            Tuple of (error_message, error_type) or (None, None) if no error found.
        """
        if not output or not output.strip():
            return None, None

        try:
            data = json.loads(output)
            if isinstance(data, dict) and "error" in data and data["error"]:
                return data["error"], data.get("error_type", "Error")
        except (json.JSONDecodeError, KeyError):
            pass

        return None, None

    def _parse_json(self, output: str) -> Any:
        """Parse JSON output from a CLI command."""
        try:
            return json.loads(output.strip())
        except json.JSONDecodeError as e:
            raise VamsCliError(f"Failed to parse CLI JSON output: {e}\nOutput: {output[:200]}")

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------

    def get_auth_type(self) -> str:
        """
        Get the authentication type from the active profile.

        Returns:
            "Cognito" or "External".

        Raises:
            VamsProfileNotSetupError: If the profile is not configured.
        """
        if self._cached_auth_type:
            return self._cached_auth_type

        try:
            output = self._execute_command(
                ["profile", "info", self._profile, "--json-output"]
            )
            data = self._parse_json(output)
            profile_info = data.get("profile_info", {})
            self._cached_auth_type = profile_info.get("auth_type", "Cognito")

            web_url = profile_info.get("web_deployed_url")
            if web_url:
                self._web_deployed_url = web_url

            return self._cached_auth_type

        except VamsCliError:
            raise VamsProfileNotSetupError(
                "VAMS CLI profile may not be configured. "
                f"Run 'vamscli setup <api-gateway-url>' to configure the '{self._profile}' profile."
            )

    def login(self, username: str, credential: str) -> str:
        """
        Authenticate with VAMS using Cognito (username/password).

        Args:
            username: VAMS username (email).
            credential: Password for Cognito authentication.

        Returns:
            The authenticated user ID.
        """
        output = self._execute_command(
            ["auth", "login", "--json-output", "-u", username, "-p", credential]
        )

        data = self._parse_json(output)

        if data.get("success") or data.get("authenticated") or "successful" in output.lower():
            self._cached_username = username
            self._cached_credential = credential
            self._cached_is_token_auth = False

            web_url = data.get("web_deployed_url")
            if web_url:
                self._web_deployed_url = web_url

            if not self._web_deployed_url:
                try:
                    self.check_authentication()
                except VamsCliError:
                    pass

            return data.get("user_id", username)

        raise VamsAuthenticationError("Authentication failed")

    def login_with_token(self, user_id: str, token: str, expires_at: Optional[str] = None) -> str:
        """
        Authenticate with a token override via ``auth login --token-override``.

        Use this for external IDP JWT tokens or VAMS API keys (prefixed ``vams_``).
        The token is validated against the VAMS API during login.

        Args:
            user_id: User ID associated with the token.
            token: IDP JWT token or VAMS API key (starts with ``vams_``).
            expires_at: Optional expiration (Unix timestamp, ISO 8601, or +seconds).

        Returns:
            The authenticated user ID.
        """
        cmd = ["auth", "login", "--user-id", user_id,
               "--token-override", token, "--json-output"]
        if expires_at:
            cmd += ["--expires-at", expires_at]

        output = self._execute_command(cmd)
        data = self._parse_json(output)

        self._cached_username = user_id
        self._cached_credential = token
        self._cached_is_token_auth = True

        web_url = data.get("web_deployed_url")
        if web_url:
            self._web_deployed_url = web_url

        return data.get("user_id", user_id)

    def check_authentication(self) -> AuthStatus:
        """
        Check current authentication status.

        Returns:
            AuthStatus dataclass with current auth state.
        """
        try:
            output = self._execute_command(["auth", "status", "--json-output"])
            data = self._parse_json(output)

            status = AuthStatus(
                authenticated=data.get("authenticated", False),
                authentication_type=data.get("authentication_type", ""),
                user_id=data.get("user_id", ""),
                is_expired=data.get("is_expired", True),
                expires_at=data.get("expires_at", ""),
                web_deployed_url=data.get("web_deployed_url", ""),
                success=data.get("success", False),
            )

            if status.web_deployed_url:
                self._web_deployed_url = status.web_deployed_url

            return status

        except VamsCliError:
            return AuthStatus()

    def is_authenticated(self) -> bool:
        """Check if currently authenticated and token is not expired."""
        status = self.check_authentication()
        return status.authenticated and not status.is_expired

    def ensure_authenticated(self) -> None:
        """
        Ensure the session is authenticated, attempting auto-reauthentication
        with cached credentials if the token has expired.

        Raises:
            VamsAuthenticationError: If not authenticated and cannot auto-reauth.
        """
        if self.is_authenticated():
            return

        if self._cached_username and self._cached_credential:
            try:
                if self._cached_is_token_auth:
                    self.login_with_token(self._cached_username, self._cached_credential)
                else:
                    self.login(self._cached_username, self._cached_credential)
                return
            except VamsCliError:
                raise VamsAuthenticationError(
                    "Authentication token expired and auto-reauthentication failed. "
                    "Please login again."
                )

        raise VamsAuthenticationError("Not authenticated. Please login first.")

    def logout(self) -> None:
        """Log out and clear cached credentials."""
        try:
            self._execute_command(["auth", "logout"])
        except VamsCliError:
            pass
        self._cached_username = None
        self._cached_credential = None
        self._cached_is_token_auth = False

    @property
    def web_deployed_url(self) -> Optional[str]:
        """The VAMS web UI URL, if available."""
        return self._web_deployed_url

    # -------------------------------------------------------------------------
    # Database Operations
    # -------------------------------------------------------------------------

    def list_databases(self) -> List[Database]:
        """
        List all accessible databases.

        Returns:
            List of Database objects.
        """
        self.ensure_authenticated()

        output = self._execute_command(
            ["database", "list", "--auto-paginate", "--json-output"]
        )
        data = self._parse_json(output)

        items = data.get("Items", [])
        return [
            Database(
                database_id=item.get("databaseId", ""),
                description=item.get("description", ""),
                date_created=item.get("dateCreated", ""),
                asset_count=item.get("assetCount", 0),
            )
            for item in items
        ]

    def get_database(self, database_id: str) -> Dict[str, Any]:
        """
        Get details for a specific database.

        Args:
            database_id: The database ID.

        Returns:
            Raw JSON dict with database details.
        """
        self.ensure_authenticated()

        output = self._execute_command(
            ["database", "get", "-d", database_id, "--json-output"]
        )
        return self._parse_json(output)

    # -------------------------------------------------------------------------
    # Asset Operations
    # -------------------------------------------------------------------------

    def list_assets(self, database_id: str) -> List[Asset]:
        """
        List all assets in a database.

        Args:
            database_id: The database ID.

        Returns:
            List of Asset objects.
        """
        self.ensure_authenticated()

        output = self._execute_command(
            ["assets", "list", "--database-id", database_id, "--auto-paginate", "--json-output"]
        )
        data = self._parse_json(output)

        items = data.get("Items", [])
        return [
            Asset(
                asset_id=item.get("assetId", ""),
                asset_name=item.get("assetName", ""),
                database_id=item.get("databaseId", ""),
                description=item.get("description", ""),
                is_distributable=item.get("isDistributable", False),
                status=item.get("status", ""),
                tags=item.get("tags", []),
                current_version=item.get("currentVersion"),
            )
            for item in items
        ]

    def get_asset(self, database_id: str, asset_id: str) -> Dict[str, Any]:
        """
        Get details for a specific asset.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.

        Returns:
            Raw JSON dict with asset details.
        """
        self.ensure_authenticated()

        # assets get: asset_id is a positional argument, -d is the database option
        output = self._execute_command(
            ["assets", "get", asset_id, "-d", database_id, "--json-output"]
        )
        return self._parse_json(output)

    def create_asset(self, database_id: str, asset_name: str,
                     description: str = "", is_distributable: bool = True) -> Dict[str, Any]:
        """
        Create a new asset in a database.

        Args:
            database_id: The database ID.
            asset_name: Name for the new asset.
            description: Optional asset description.
            is_distributable: Whether the asset is distributable.

        Returns:
            Raw JSON dict with created asset details (includes assetId).
        """
        self.ensure_authenticated()

        json_input = json.dumps({
            "assetName": asset_name,
            "description": description,
            "isDistributable": is_distributable,
        })

        output = self._execute_command(
            ["assets", "create", "-d", database_id, "--json-input", json_input, "--json-output"]
        )
        return self._parse_json(output)

    # -------------------------------------------------------------------------
    # File Operations
    # -------------------------------------------------------------------------

    def list_files(self, database_id: str, asset_id: str) -> List[AssetFile]:
        """
        List all files in an asset.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.

        Returns:
            List of AssetFile objects.
        """
        self.ensure_authenticated()

        output = self._execute_command(
            ["file", "list", "-d", database_id, "-a", asset_id,
             "--basic", "--auto-paginate", "--json-output"]
        )
        data = self._parse_json(output)

        items = data.get("items", data.get("Items", []))
        return [
            AssetFile(
                file_name=item.get("fileName", ""),
                relative_path=item.get("relativePath", ""),
                size=item.get("size", 0),
                is_folder=item.get("isFolder", False),
                is_archived=item.get("isArchived", False),
                primary_type=item.get("primaryType", ""),
                content_type=item.get("contentType", ""),
                last_modified=item.get("lastModified", ""),
                preview_file=item.get("previewFile", ""),
            )
            for item in items
        ]

    def get_file_info(self, database_id: str, asset_id: str,
                      file_path: str) -> Dict[str, Any]:
        """
        Get detailed info for a specific file.

        Args:
            database_id: The database ID.
            asset_id: The asset ID.
            file_path: Relative file path within the asset.

        Returns:
            Raw JSON dict with file details.
        """
        self.ensure_authenticated()

        output = self._execute_command(
            ["file", "info", "-d", database_id, "-a", asset_id,
             "-p", file_path, "--json-output"]
        )
        return self._parse_json(output)

    def download_file(self, local_path: str, database_id: str,
                      asset_id: str, file_key: str) -> bool:
        """
        Download a single file from an asset.

        Args:
            local_path: Local directory or file path to save to.
            database_id: The database ID.
            asset_id: The asset ID.
            file_key: The file key (relative path) within the asset.

        Returns:
            True if the download succeeded.
        """
        self.ensure_authenticated()

        os.makedirs(local_path if os.path.isdir(local_path) or not os.path.splitext(local_path)[1]
                     else os.path.dirname(local_path), exist_ok=True)

        # local_path is the first positional argument to assets download
        output = self._execute_command(
            ["assets", "download", local_path,
             "-d", database_id, "-a", asset_id,
             "--file-key", file_key, "--json-output"]
        )
        data = self._parse_json(output)
        return data.get("overall_success", data.get("success", True))

    def download_asset(self, local_path: str, database_id: str,
                       asset_id: str) -> DownloadResult:
        """
        Download all files from an asset recursively.

        Args:
            local_path: Local directory to save files to.
            database_id: The database ID.
            asset_id: The asset ID.

        Returns:
            DownloadResult with details about the download.
        """
        self.ensure_authenticated()
        os.makedirs(local_path, exist_ok=True)

        # local_path is the first positional argument to assets download
        output = self._execute_command(
            ["assets", "download", local_path,
             "-d", database_id, "-a", asset_id,
             "--file-key", "/", "--recursive", "--json-output"]
        )
        data = self._parse_json(output)

        return DownloadResult(
            overall_success=data.get("overall_success", False),
            total_files=data.get("total_files", 0),
            successful_files=data.get("successful_files", 0),
            failed_files=data.get("failed_files", 0),
            total_size=data.get("total_size", 0),
            total_size_formatted=data.get("total_size_formatted", ""),
            successful_downloads=data.get("successful_downloads", []),
            failed_downloads=data.get("failed_downloads", []),
        )

    def upload_file(self, file_path: str, database_id: str,
                    asset_id: str) -> Dict[str, Any]:
        """
        Upload a file to an asset.

        Args:
            file_path: Local path to the file to upload.
            database_id: The database ID.
            asset_id: The asset ID.

        Returns:
            Raw JSON dict with upload result.

        Raises:
            VamsCliError: If the file doesn't exist or upload fails.
        """
        if not os.path.isfile(file_path):
            raise VamsCliError(f"File not found: {file_path}")

        self.ensure_authenticated()

        output = self._execute_command(
            ["file", "upload", file_path,
             "-d", database_id, "-a", asset_id,
             "--json-output", "--hide-progress"]
        )
        return self._parse_json(output)

    def upload_directory(self, directory_path: str, database_id: str,
                         asset_id: str, recursive: bool = True) -> Dict[str, Any]:
        """
        Upload all files in a directory to an asset.

        Args:
            directory_path: Local directory path.
            database_id: The database ID.
            asset_id: The asset ID.
            recursive: Whether to include subdirectories.

        Returns:
            Raw JSON dict with upload result.
        """
        if not os.path.isdir(directory_path):
            raise VamsCliError(f"Directory not found: {directory_path}")

        self.ensure_authenticated()

        cmd = ["file", "upload", "--directory", directory_path,
               "-d", database_id, "-a", asset_id,
               "--json-output", "--hide-progress"]
        if recursive:
            cmd.append("--recursive")

        output = self._execute_command(cmd)
        return self._parse_json(output)

    # -------------------------------------------------------------------------
    # Workflow Operations
    # -------------------------------------------------------------------------

    def list_workflows(self, database_id: Optional[str] = None) -> List[Workflow]:
        """
        List available workflows, optionally filtered by database.

        Args:
            database_id: Optional database ID to filter workflows.
                         If None, lists all workflows across all databases.

        Returns:
            List of Workflow objects.
        """
        self.ensure_authenticated()

        cmd = ["workflow", "list", "--auto-paginate", "--json-output"]
        if database_id:
            cmd += ["-d", database_id]

        output = self._execute_command(cmd)
        data = self._parse_json(output)

        items = data.get("Items", [])
        return [
            Workflow(
                workflow_id=item.get("workflowId", ""),
                database_id=item.get("databaseId", ""),
                description=item.get("description", ""),
                workflow_arn=item.get("workflow_arn", ""),
                auto_trigger_extensions=item.get("autoTriggerOnFileExtensionsUpload", ""),
            )
            for item in items
        ]

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
            asset_id: The asset ID to list executions for.
            workflow_id: Optional workflow ID to filter by.
            workflow_database_id: Optional workflow's database ID to filter by.

        Returns:
            List of WorkflowExecution objects.
        """
        self.ensure_authenticated()

        cmd = ["workflow", "list-executions",
               "-d", database_id, "-a", asset_id,
               "--auto-paginate", "--json-output"]
        if workflow_id:
            cmd += ["-w", workflow_id]
        if workflow_database_id:
            cmd += ["--workflow-database-id", workflow_database_id]

        output = self._execute_command(cmd)
        data = self._parse_json(output)

        items = data.get("Items", [])
        return [
            WorkflowExecution(
                execution_id=item.get("executionId", ""),
                workflow_id=item.get("workflowId", ""),
                workflow_database_id=item.get("workflowDatabaseId", ""),
                execution_status=item.get("executionStatus", ""),
                start_date=item.get("startDate", ""),
                stop_date=item.get("stopDate", ""),
                input_file_key=item.get("inputAssetFileKey", ""),
            )
            for item in items
        ]

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
            file_key: Optional file key within the asset. Defaults to "/"
                      (top-level asset) if not specified.

        Returns:
            Raw JSON dict with execution result (includes execution ID).
        """
        self.ensure_authenticated()

        cmd = ["workflow", "execute",
               "-d", database_id, "-a", asset_id,
               "-w", workflow_id,
               "--workflow-database-id", workflow_database_id,
               "--json-output"]
        if file_key:
            cmd += ["--file-key", file_key]

        output = self._execute_command(cmd)
        return self._parse_json(output)
