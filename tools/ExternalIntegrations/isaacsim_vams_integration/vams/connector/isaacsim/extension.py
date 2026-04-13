"""
VAMS Connector Isaac Sim Extension.

Registers the VAMS Connector as an Omniverse Kit extension with a UI window
for authenticating, browsing databases/assets/files, downloading, uploading,
and executing workflows.
"""

import logging

import carb
import omni.ext
import omni.ui as ui

from .connector import IsaacVAMSConnector
from .vams_cli_service import (
    VamsCliError,
    VamsNotInstalledError,
    VamsProfileNotSetupError,
)

logger = logging.getLogger(__name__)

# Extension settings keys
SETTING_PROFILE = "/exts/vams.connector.isaacsim/profile"
SETTING_VAMSCLI_PATH = "/exts/vams.connector.isaacsim/vamscli_path"


class VamsConnectorExtension(omni.ext.IExt):
    """
    Omniverse Kit extension that provides VAMS asset management integration.

    Lifecycle:
        on_startup: Reads settings, creates the connector, and builds the UI window.
        on_shutdown: Destroys the UI window and cleans up the connector.
    """

    def on_startup(self, ext_id: str):
        logger.info("VAMS Connector extension starting: %s", ext_id)

        # Read settings from extension.toml / Carbonite settings
        settings = carb.settings.get_settings()
        profile = settings.get(SETTING_PROFILE) or "default"
        vamscli_path = settings.get(SETTING_VAMSCLI_PATH) or None

        # Initialize the connector (will raise if CLI not installed)
        self._connector = None
        self._window = None

        try:
            self._connector = IsaacVAMSConnector(
                profile=profile, vamscli_path=vamscli_path
            )
        except VamsNotInstalledError as e:
            logger.error("VAMS CLI not installed: %s", e)
            self._show_error_window(
                "VAMS CLI Not Found",
                "The VAMS CLI (vamscli) is not installed or not on PATH.\n\n"
                "Install it with:\n"
                "  pip install vamscli\n\n"
                "Or set the path in extension settings:\n"
                f"  {SETTING_VAMSCLI_PATH}",
            )
            return

        self._build_window()

    def on_shutdown(self):
        logger.info("VAMS Connector extension shutting down")

        if self._connector and self._connector._authenticated:
            try:
                self._connector.logout()
            except Exception:
                pass

        self._connector = None

        if self._window:
            self._window.destroy()
            self._window = None

    # -------------------------------------------------------------------------
    # UI
    # -------------------------------------------------------------------------

    def _build_window(self):
        """Build the main VAMS Connector UI window."""
        self._window = ui.Window("VAMS Connector", width=420, height=500)

        # UI state
        self._username_model = ui.SimpleStringModel()
        self._credential_model = ui.SimpleStringModel()
        self._status_label = None
        self._content_stack = None

        # Selection state
        self._databases = []
        self._assets = []
        self._files = []
        self._workflows = []
        self._selected_db = None
        self._selected_asset = None

        with self._window.frame:
            with ui.VStack(spacing=6):
                # --- Auth section ---
                ui.Label("Authentication", style={"font_size": 16})
                with ui.HStack(height=24):
                    ui.Label("Username:", width=80)
                    ui.StringField(model=self._username_model)
                with ui.HStack(height=24):
                    ui.Label("Password/Token:", width=100)
                    ui.StringField(model=self._credential_model, password_mode=True)
                with ui.HStack(height=24, spacing=4):
                    ui.Button("Login (Cognito)", clicked_fn=self._on_login)
                    ui.Button("Login with Token/API Key", clicked_fn=self._on_login_token)
                    ui.Button("Logout", clicked_fn=self._on_logout)

                ui.Separator(height=4)

                # --- Status ---
                self._status_label = ui.Label("Not authenticated", height=20,
                                               style={"color": 0xFFAAAAAA})

                ui.Separator(height=4)

                # --- Browse buttons ---
                with ui.HStack(height=24, spacing=4):
                    ui.Button("Databases", clicked_fn=self._on_list_databases)
                    ui.Button("Assets", clicked_fn=self._on_list_assets)
                    ui.Button("Files", clicked_fn=self._on_list_files)
                    ui.Button("Workflows", clicked_fn=self._on_list_workflows)

                ui.Separator(height=4)

                # --- Content area ---
                with ui.ScrollingFrame(height=ui.Fraction(1)):
                    self._content_stack = ui.VStack(spacing=2)

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = text

    def _clear_content(self):
        if self._content_stack:
            self._content_stack.clear()

    # -------------------------------------------------------------------------
    # Auth handlers
    # -------------------------------------------------------------------------

    def _on_login(self):
        username = self._username_model.get_value_as_string()
        credential = self._credential_model.get_value_as_string()
        if not username or not credential:
            self._set_status("Enter username and password/token")
            return

        self._set_status("Authenticating...")
        if self._connector.login(username, credential):
            self._set_status(f"Authenticated as {username}")
        else:
            self._set_status("Authentication failed")

    def _on_login_token(self):
        user_id = self._username_model.get_value_as_string()
        token = self._credential_model.get_value_as_string()
        if not user_id or not token:
            self._set_status("Enter user ID and JWT token or VAMS API key (vams_...)")
            return

        self._set_status("Authenticating with token override...")
        if self._connector.login_with_token(user_id, token):
            kind = "API key" if token.startswith("vams_") else "token"
            self._set_status(f"Authenticated as {user_id} ({kind})")
        else:
            self._set_status("Token/API key authentication failed")

    def _on_logout(self):
        self._connector.logout()
        self._set_status("Logged out")
        self._clear_content()

    # -------------------------------------------------------------------------
    # Browse handlers
    # -------------------------------------------------------------------------

    def _on_list_databases(self):
        try:
            self._set_status("Loading databases...")
            self._databases = self._connector.list_databases()
            self._clear_content()
            with self._content_stack:
                if not self._databases:
                    ui.Label("No databases found")
                    return
                ui.Label(f"{len(self._databases)} database(s):",
                         style={"font_size": 14})
                for db in self._databases:
                    with ui.HStack(height=22, spacing=4):
                        ui.Button(
                            f"{db.database_id}  ({db.asset_count} assets)",
                            clicked_fn=lambda d=db: self._select_database(d),
                        )
            self._set_status(f"Loaded {len(self._databases)} databases")
        except VamsCliError as e:
            self._set_status(f"Error: {e}")

    def _select_database(self, db):
        self._selected_db = db
        self._set_status(f"Selected database: {db.database_id}")
        self._on_list_assets()

    def _on_list_assets(self):
        if not self._selected_db:
            self._set_status("Select a database first")
            return
        try:
            self._set_status(f"Loading assets from {self._selected_db.database_id}...")
            self._assets = self._connector.list_assets(self._selected_db.database_id)
            self._clear_content()
            with self._content_stack:
                if not self._assets:
                    ui.Label("No assets found")
                    return
                ui.Label(f"{len(self._assets)} asset(s) in {self._selected_db.database_id}:",
                         style={"font_size": 14})
                for asset in self._assets:
                    with ui.HStack(height=22, spacing=4):
                        ui.Button(
                            f"{asset.asset_name}  [{asset.asset_id}]",
                            clicked_fn=lambda a=asset: self._select_asset(a),
                        )
            self._set_status(f"Loaded {len(self._assets)} assets")
        except VamsCliError as e:
            self._set_status(f"Error: {e}")

    def _select_asset(self, asset):
        self._selected_asset = asset
        self._set_status(f"Selected asset: {asset.asset_name}")
        self._on_list_files()

    def _on_list_files(self):
        if not self._selected_db or not self._selected_asset:
            self._set_status("Select a database and asset first")
            return
        try:
            db_id = self._selected_db.database_id
            asset_id = self._selected_asset.asset_id
            self._set_status(f"Loading files from {self._selected_asset.asset_name}...")
            self._files = self._connector.list_files(db_id, asset_id)
            self._clear_content()
            with self._content_stack:
                if not self._files:
                    ui.Label("No files found")
                    return
                ui.Label(f"{len(self._files)} file(s) in {self._selected_asset.asset_name}:",
                         style={"font_size": 14})
                for f in self._files:
                    label = f.relative_path or f.file_name
                    if f.is_folder:
                        label += "/"
                    else:
                        label += f"  ({f.size:,} bytes)"
                    ui.Label(label, height=18)
            self._set_status(f"Loaded {len(self._files)} files")
        except VamsCliError as e:
            self._set_status(f"Error: {e}")

    def _on_list_workflows(self):
        try:
            db_id = self._selected_db.database_id if self._selected_db else None
            self._set_status("Loading workflows...")
            self._workflows = self._connector.list_workflows(db_id)
            self._clear_content()
            with self._content_stack:
                if not self._workflows:
                    ui.Label("No workflows found")
                    return
                ui.Label(f"{len(self._workflows)} workflow(s):",
                         style={"font_size": 14})
                for wf in self._workflows:
                    desc = wf.description or wf.workflow_id
                    with ui.HStack(height=22, spacing=4):
                        ui.Label(f"{desc}  [db: {wf.database_id}]")
                        if self._selected_db and self._selected_asset:
                            ui.Button(
                                "Execute",
                                width=60,
                                clicked_fn=lambda w=wf: self._on_execute_workflow(w),
                            )
            self._set_status(f"Loaded {len(self._workflows)} workflows")
        except VamsCliError as e:
            self._set_status(f"Error: {e}")

    def _on_execute_workflow(self, wf):
        if not self._selected_db or not self._selected_asset:
            self._set_status("Select a database and asset first")
            return
        try:
            self._set_status(
                f"Executing {wf.workflow_id} on {self._selected_asset.asset_name}..."
            )
            result = self._connector.execute_workflow(
                database_id=self._selected_db.database_id,
                asset_id=self._selected_asset.asset_id,
                workflow_id=wf.workflow_id,
                workflow_database_id=wf.database_id,
            )
            exec_id = result.get("message", "unknown")
            self._set_status(f"Workflow started: {exec_id}")
        except VamsCliError as e:
            self._set_status(f"Workflow error: {e}")

    # -------------------------------------------------------------------------
    # Error window (shown when CLI is missing)
    # -------------------------------------------------------------------------

    def _show_error_window(self, title: str, message: str):
        self._window = ui.Window(title, width=400, height=200)
        with self._window.frame:
            with ui.VStack(spacing=8):
                ui.Label(message, word_wrap=True)
