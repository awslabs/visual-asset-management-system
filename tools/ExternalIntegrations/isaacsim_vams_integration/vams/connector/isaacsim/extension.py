"""
VAMS Connector Isaac Sim Extension.

Registers the VAMS Connector as an Omniverse Kit extension with a UI window
for browsing VAMS databases/assets/files, importing 3D assets (USD, URDF,
MJCF), executing processing workflows, and uploading scenes.
"""

import logging
import os
import tempfile

import carb
import omni.ext
import omni.kit.app
import omni.ui as ui

from .connector import IsaacVAMSConnector
from .vams_cli_service import (
    VamsCliError,
    VamsNotInstalledError,
    VamsProfileNotSetupError,
)

logger = logging.getLogger(__name__)

SETTING_PROFILE = "/exts/vams.connector.isaacsim/profile"
SETTING_VAMSCLI_PATH = "/exts/vams.connector.isaacsim/vamscli_path"

_USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
_URDF_EXTENSIONS = (".urdf",)
_MJCF_EXTENSIONS = (".mjcf", ".xml")

LABEL_WIDTH = 120
VERTICAL_SPACING = 5
HORIZONTAL_SPACING = 4
_CHECK = "* "
_NOCHECK = "  "

try:
    from isaacsim.gui.components.style import get_style as _get_nvidia_style
    _HAS_ISAAC_STYLE = True
except ImportError:
    _HAS_ISAAC_STYLE = False


def _get_style():
    return _get_nvidia_style() if _HAS_ISAAC_STYLE else {}


def _defer(fn):
    sub = None
    def _on_update(_e):
        sub.unsubscribe()
        fn()
    sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
        _on_update, name="vams_defer"
    )


def _section(title, collapsed=False):
    return ui.CollapsableFrame(
        title=title, height=0, collapsed=collapsed,
        style=_get_style(), style_type_name_override="CollapsableFrame",
        horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
        vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
    )


def _status_color(s):
    sl = s.lower()
    if "running" in sl or "pending" in sl or "started" in sl:
        return 0xFF44AAFF
    if "succeeded" in sl or "completed" in sl:
        return 0xFF44CC44
    return 0xFFDD6644


def _file_type(name):
    ll = name.lower()
    if any(ll.endswith(e) for e in _USD_EXTENSIONS):
        return "usd"
    if any(ll.endswith(e) for e in _URDF_EXTENSIONS):
        return "urdf"
    if any(ll.endswith(e) for e in _MJCF_EXTENSIONS):
        return "mjcf"
    return "other"


class VamsConnectorExtension(omni.ext.IExt):

    def on_startup(self, ext_id: str):
        logger.info("VAMS Connector extension starting: %s", ext_id)
        self._ext_id = ext_id

        settings = carb.settings.get_settings()
        profile = settings.get(SETTING_PROFILE) or "default"
        vamscli_path = settings.get(SETTING_VAMSCLI_PATH) or None

        self._connector = None
        self._window = None

        try:
            self._connector = IsaacVAMSConnector(profile=profile, vamscli_path=vamscli_path)
        except VamsNotInstalledError as e:
            logger.error("VAMS CLI not installed: %s", e)
            self._show_error_window(
                "VAMS CLI Not Found",
                "The VAMS CLI (vamscli) is not installed or not on PATH.\n\n"
                "Install it with:\n  pip install vamscli\n\n"
                "Or set the path in extension settings:\n"
                f"  {SETTING_VAMSCLI_PATH}",
            )
            return

        self._init_state()
        self._build_window()
        _defer(self._check_existing_session)

    def _init_state(self):
        self._databases = []
        self._assets = []
        self._files = []
        self._workflows = []
        self._workflow_executions = []
        self._selected_db = None
        self._selected_asset = None
        self._selected_file = None
        self._selected_workflow = None
        self._export_name_model = ui.SimpleStringModel()
        self._export_desc_model = ui.SimpleStringModel()

        # Frame / stack references (set during _build_window)
        self._auth_frame = None
        self._db_frame = None
        self._asset_frame = None
        self._file_frame = None
        self._action_frame = None
        self._wf_frame = None
        self._export_frame = None
        self._status_label = None
        self._db_stack = None
        self._asset_stack = None
        self._file_stack = None
        self._action_stack = None
        self._wf_stack = None
        self._export_stack = None

    def _check_existing_session(self):
        try:
            status = self._connector.get_auth_status()
            if status.user_id and self._username_model:
                self._username_model.set_value(status.user_id)
            if status.authenticated and not status.is_expired:
                self._connector._authenticated = True
                self._set_status(f"Authenticated as {status.user_id}")
                if self._auth_frame:
                    self._auth_frame.collapsed = True
                self._load_databases()
        except Exception:
            pass

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

    # =========================================================================
    # Window
    # =========================================================================

    def _build_window(self):
        self._window = ui.Window(
            title="VAMS Connector", width=500, height=640, visible=True,
            dockPreference=ui.DockPreference.LEFT_BOTTOM,
        )
        self._username_model = ui.SimpleStringModel()
        self._credential_model = ui.SimpleStringModel()

        with self._window.frame:
            with ui.ScrollingFrame():
                with ui.VStack(spacing=VERTICAL_SPACING, height=0):
                    self._build_auth()
                    self._build_status_bar()
                    self._build_db_browser()
                    self._build_asset_browser()
                    self._build_file_browser()
                    self._build_actions()
                    self._build_workflows()
                    self._build_export()

    def _build_auth(self):
        self._auth_frame = _section("Authentication", collapsed=False)
        with self._auth_frame:
            with ui.VStack(spacing=VERTICAL_SPACING, height=0):
                with ui.HStack(height=22):
                    ui.Label("Username", width=LABEL_WIDTH)
                    ui.StringField(model=self._username_model)
                with ui.HStack(height=22):
                    ui.Label("Password / Token", width=LABEL_WIDTH)
                    ui.StringField(model=self._credential_model, password_mode=True)
                with ui.HStack(height=24, spacing=HORIZONTAL_SPACING):
                    ui.Spacer(width=LABEL_WIDTH)
                    ui.Button("Login (Cognito)", clicked_fn=lambda: _defer(self._on_login),
                              tooltip="Authenticate with Cognito username/password")
                    ui.Button("Login (Token/Key)", clicked_fn=lambda: _defer(self._on_login_token),
                              tooltip="Authenticate with IDP JWT token or VAMS API key")
                    ui.Button("Logout", width=55, clicked_fn=lambda: _defer(self._on_logout))

    def _build_status_bar(self):
        with ui.ZStack(height=20):
            ui.Rectangle(style={"background_color": 0xFF1A1A2E, "border_radius": 3})
            self._status_label = ui.Label(
                "  Not authenticated", style={"color": 0xFFAAAAAA, "font_size": 12}
            )

    def _build_db_browser(self):
        self._db_frame = _section("Database Browser", collapsed=True)
        with self._db_frame:
            with ui.VStack(spacing=2, height=0):
                ui.Button("Refresh Databases", height=22,
                          clicked_fn=lambda: _defer(self._load_databases))
                self._db_stack = ui.VStack(spacing=2, height=0)

    def _build_asset_browser(self):
        self._asset_frame = _section("Asset Browser", collapsed=True)
        with self._asset_frame:
            with ui.VStack(spacing=2, height=0):
                ui.Button("Refresh Assets", height=22,
                          clicked_fn=lambda: _defer(self._reload_assets))
                self._asset_stack = ui.VStack(spacing=2, height=0)

    def _build_file_browser(self):
        self._file_frame = _section("File Browser", collapsed=True)
        with self._file_frame:
            with ui.VStack(spacing=2, height=0):
                ui.Button("Refresh Files", height=22,
                          clicked_fn=lambda: _defer(self._reload_files))
                self._file_stack = ui.VStack(spacing=2, height=0)

    def _build_actions(self):
        self._action_frame = _section("Actions", collapsed=True)
        with self._action_frame:
            self._action_stack = ui.VStack(spacing=VERTICAL_SPACING, height=0)

    def _build_workflows(self):
        self._wf_frame = _section("Workflows", collapsed=True)
        with self._wf_frame:
            self._wf_stack = ui.VStack(spacing=VERTICAL_SPACING, height=0)

    def _build_export(self):
        self._export_frame = _section("Export / Upload", collapsed=True)
        with self._export_frame:
            self._export_stack = ui.VStack(spacing=VERTICAL_SPACING, height=0)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _set_status(self, text):
        if self._status_label:
            self._status_label.text = f"  {text}"

    # =========================================================================
    # Auth
    # =========================================================================

    def _on_login(self):
        username = self._username_model.get_value_as_string()
        credential = self._credential_model.get_value_as_string()
        if not username or not credential:
            self._set_status("Enter username and password/token")
            return
        self._set_status("Authenticating...")
        if self._connector.login(username, credential):
            self._set_status(f"Authenticated as {username}")
            if self._auth_frame:
                self._auth_frame.collapsed = True
            _defer(self._load_databases)
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
            if self._auth_frame:
                self._auth_frame.collapsed = True
            _defer(self._load_databases)
        else:
            self._set_status("Token/API key authentication failed")

    def _on_logout(self):
        self._connector.logout()
        self._selected_db = None
        self._selected_asset = None
        self._selected_file = None
        if self._auth_frame:
            self._auth_frame.collapsed = False
        for frame in (self._db_frame, self._asset_frame, self._file_frame,
                      self._action_frame, self._wf_frame, self._export_frame):
            if frame:
                frame.collapsed = True
        for stack in (self._db_stack, self._asset_stack, self._file_stack,
                      self._action_stack, self._wf_stack, self._export_stack):
            if stack:
                stack.clear()
        self._set_status("Logged out")

    # =========================================================================
    # Database Browser
    # =========================================================================

    def _load_databases(self):
        self._set_status("Loading databases...")
        try:
            self._databases = self._connector.list_databases()
        except VamsCliError as e:
            self._set_status(f"Error: {e}")
            return
        self._populate_db_list()
        if self._db_frame:
            self._db_frame.collapsed = False
        self._set_status(f"{len(self._databases)} database(s)")

    def _populate_db_list(self):
        if not self._db_stack:
            return
        self._db_stack.clear()
        with self._db_stack:
            for db in self._databases:
                is_sel = self._selected_db and self._selected_db.database_id == db.database_id
                with ui.HStack(height=24, spacing=HORIZONTAL_SPACING):
                    ui.Button(
                        f"  {_CHECK if is_sel else _NOCHECK}{db.database_id}",
                        clicked_fn=lambda d=db: _defer(lambda: self._select_database(d)),
                        tooltip=db.description or "",
                        style={"Button": {"background_color": 0xFF3A4A6A}} if is_sel else {},
                    )
                    ui.Label(f"{db.asset_count}", width=40,
                             style={"font_size": 11, "color": 0xFF888888})

    def _select_database(self, db):
        self._selected_db = db
        self._selected_asset = None
        self._selected_file = None
        self._populate_db_list()
        self._set_status(f"Database: {db.database_id}")
        # Collapse DB browser, open Asset browser
        if self._db_frame:
            self._db_frame.collapsed = True
        self._load_assets()

    # =========================================================================
    # Asset Browser
    # =========================================================================

    def _load_assets(self):
        if not self._selected_db:
            return
        self._set_status(f"Loading assets from {self._selected_db.database_id}...")
        try:
            self._assets = self._connector.list_assets(self._selected_db.database_id)
        except VamsCliError as e:
            self._set_status(f"Error: {e}")
            return
        self._populate_asset_list()
        if self._asset_frame:
            self._asset_frame.collapsed = False
        self._set_status(f"{len(self._assets)} asset(s) in {self._selected_db.database_id}")

    def _reload_assets(self):
        if not self._selected_db:
            self._set_status("Select a database first")
            return
        self._load_assets()

    def _populate_asset_list(self):
        if not self._asset_stack:
            return
        self._asset_stack.clear()
        with self._asset_stack:
            for asset in self._assets:
                is_sel = self._selected_asset and self._selected_asset.asset_id == asset.asset_id
                ui.Button(
                    f"  {_CHECK if is_sel else _NOCHECK}{asset.asset_name}",
                    height=24,
                    clicked_fn=lambda a=asset: _defer(lambda: self._select_asset(a)),
                    tooltip=f"ID: {asset.asset_id}\n{asset.description}" if asset.description else asset.asset_id,
                    style={"Button": {"background_color": 0xFF3A4A6A}} if is_sel else {},
                )

    def _select_asset(self, asset):
        self._selected_asset = asset
        self._selected_file = None
        self._populate_asset_list()
        self._set_status(f"Asset: {asset.asset_name}")
        # Collapse Asset browser, open File browser
        if self._asset_frame:
            self._asset_frame.collapsed = True
        self._load_files()

    # =========================================================================
    # File Browser
    # =========================================================================

    def _load_files(self):
        if not self._selected_db or not self._selected_asset:
            return
        self._set_status(f"Loading files from {self._selected_asset.asset_name}...")
        try:
            self._files = self._connector.list_files(
                self._selected_db.database_id, self._selected_asset.asset_id
            )
        except VamsCliError as e:
            self._set_status(f"Error: {e}")
            return
        self._populate_file_list()
        if self._file_frame:
            self._file_frame.collapsed = False
        if self._action_frame:
            self._action_frame.collapsed = False
        self._refresh_action_panel()
        self._refresh_export_panel()
        self._show_wf_placeholder()
        self._set_status(f"{len(self._files)} file(s) in {self._selected_asset.asset_name}")

    def _reload_files(self):
        if not self._selected_db or not self._selected_asset:
            self._set_status("Select a database and asset first")
            return
        self._load_files()

    def _populate_file_list(self):
        if not self._file_stack:
            return
        self._file_stack.clear()
        with self._file_stack:
            for f in self._files:
                label = (f.relative_path or f.file_name).strip("/")
                if not label:
                    continue
                if f.is_folder:
                    ui.Label(f"    {label}/", height=20, style={"font_size": 12, "color": 0xFF999999})
                    continue
                ft = _file_type(label)
                is_sel = self._selected_file is f
                icon = ">" if ft != "other" else "-"
                color = 0xFFAAFFAA if ft != "other" else 0xFFCCCCCC
                ui.Button(
                    f"  {_CHECK if is_sel else _NOCHECK}{icon} {label}",
                    height=24,
                    clicked_fn=lambda fobj=f: _defer(lambda: self._select_file(fobj)),
                    tooltip=f"{f.size:,} B" if f.size else "",
                    style={"Button": {"background_color": 0xFF3A4A6A}} if is_sel else {},
                )

    def _select_file(self, f):
        self._selected_file = f
        self._populate_file_list()
        label = (f.relative_path or f.file_name).strip("/")
        self._set_status(f"File: {label}")
        self._refresh_action_panel()

    # =========================================================================
    # Actions (context-sensitive based on selection)
    # =========================================================================

    def _refresh_action_panel(self):
        if not self._action_stack:
            return
        self._action_stack.clear()
        with self._action_stack:
            if self._selected_file and not self._selected_file.is_folder:
                self._build_file_actions()
            elif self._selected_asset:
                ui.Label("  Select a file above for import/download actions.",
                         height=22, style={"color": 0xFF888888})

    def _build_file_actions(self):
        f = self._selected_file
        label = (f.relative_path or f.file_name).strip("/")
        ft = _file_type(label)
        size_str = f"{f.size:,} B" if f.size else ""
        ui.Label(f"  {label}  ({size_str})", height=20, style={"font_size": 13})
        ui.Label(f"  Type: {ft.upper()}", height=16,
                 style={"font_size": 11, "color": 0xFF999999})

        with ui.VStack(spacing=3, height=0):
            if ft == "usd":
                ui.Button("Open Stage (replace entire scene)",
                          height=26,
                          clicked_fn=lambda: _defer(lambda: self._on_load_file(f)),
                          tooltip="Download this USD file and open it as a new stage, replacing the current scene")
                ui.Button("Add USD Reference (add to current scene)",
                          height=26,
                          clicked_fn=lambda: _defer(lambda: self._on_add_usd_reference(f)),
                          tooltip="Download this USD file and add it as a prim reference into the current stage")
            elif ft == "urdf":
                ui.Button("Import URDF (add to current scene)",
                          height=26,
                          clicked_fn=lambda: _defer(lambda: self._on_import_urdf(f)),
                          tooltip="Download and import this URDF robot description into the current stage")
            elif ft == "mjcf":
                ui.Button("Import MJCF (add to current scene)",
                          height=26,
                          clicked_fn=lambda: _defer(lambda: self._on_import_mjcf(f)),
                          tooltip="Download and import this MuJoCo model into the current stage")
            ui.Button("Download Only (save to disk)",
                      height=26,
                      clicked_fn=lambda: _defer(lambda: self._on_download_file(f)),
                      tooltip="Download file to a local temporary directory without importing")

    # =========================================================================
    # Workflows
    # =========================================================================

    def _wf_header_text(self):
        """Return the header label for the workflow section."""
        if not self._selected_db or not self._selected_asset:
            return ""
        return f"  {self._selected_asset.asset_name}  [{self._selected_db.database_id}]"

    def _show_wf_placeholder(self):
        """Show the workflow section with a Refresh button but no data loaded yet."""
        if not self._wf_stack:
            return
        self._wf_stack.clear()
        with self._wf_stack:
            if not self._selected_db or not self._selected_asset:
                ui.Label("  Select an asset first.", height=22, style={"color": 0xFF888888})
                return
            ui.Label(self._wf_header_text(), height=20, style={"font_size": 13})
            ui.Button("Refresh Workflows & Executions", height=24,
                      clicked_fn=lambda: _defer(self._refresh_wf_panel))

    def _refresh_wf_panel(self):
        if not self._wf_stack:
            return
        if not self._selected_db or not self._selected_asset:
            self._show_wf_placeholder()
            return
        self._set_status("Loading workflows...")
        db_id = self._selected_db.database_id
        asset_id = self._selected_asset.asset_id

        # Fetch workflows from GLOBAL + selected database
        all_wfs, seen = [], set()
        for qdb in ["GLOBAL"] + ([db_id] if db_id != "GLOBAL" else []):
            try:
                for wf in self._connector.list_workflows(qdb):
                    key = (wf.workflow_id, wf.database_id)
                    if key not in seen:
                        seen.add(key)
                        all_wfs.append(wf)
            except VamsCliError:
                pass
        self._workflows = all_wfs

        try:
            self._workflow_executions = self._connector.list_workflow_executions(db_id, asset_id)
        except VamsCliError:
            self._workflow_executions = []

        self._selected_workflow = None
        self._wf_stack.clear()
        with self._wf_stack:
            ui.Label(self._wf_header_text(), height=20, style={"font_size": 13})
            ui.Label(f"  Fetched from: GLOBAL, {db_id}", height=16,
                     style={"font_size": 10, "color": 0xFF888888})
            if not self._workflows:
                ui.Label("  No workflows found.", height=20, style={"color": 0xFF888888})
            else:
                for wf in self._workflows:
                    desc = f"{wf.workflow_id} - {wf.description}" if wf.description else wf.workflow_id
                    with ui.HStack(height=24, spacing=HORIZONTAL_SPACING):
                        ui.Button(
                            f"  {desc}",
                            clicked_fn=lambda w=wf: _defer(lambda: self._show_wf_execute(w)),
                            tooltip=f"ID: {wf.workflow_id}  |  DB: {wf.database_id}",
                        )
                        ui.Label(wf.database_id, width=80,
                                 style={"font_size": 10, "color": 0xFF777777})

            ui.Spacer(height=4)
            with ui.HStack(height=22, spacing=HORIZONTAL_SPACING):
                ui.Label(f"  Execution History ({len(self._workflow_executions)})",
                         height=20, style={"font_size": 13})
                ui.Button("Refresh", width=60, height=20,
                          clicked_fn=lambda: _defer(self._refresh_wf_panel))
            if not self._workflow_executions:
                ui.Label("  No executions.", height=20, style={"color": 0xFF888888})
            else:
                with ui.HStack(height=16):
                    ui.Label("  Status", width=85, style={"font_size": 10, "color": 0xFF666666})
                    ui.Label("Workflow", width=110, style={"font_size": 10, "color": 0xFF666666})
                    ui.Label("File", width=90, style={"font_size": 10, "color": 0xFF666666})
                    ui.Label("Started", style={"font_size": 10, "color": 0xFF666666})
                for ex in self._workflow_executions:
                    s = ex.execution_status or "Unknown"
                    fk = ex.input_file_key if ex.input_file_key and ex.input_file_key != "/" else "-"
                    with ui.HStack(height=18):
                        ui.Label(f"  {s}", width=85,
                                 style={"font_size": 11, "color": _status_color(s)})
                        ui.Label(ex.workflow_id or "", width=110,
                                 style={"font_size": 11, "color": 0xFFAAAAAA})
                        ui.Label(fk, width=90, style={"font_size": 11, "color": 0xFFAAAAAA})
                        ui.Label(ex.start_date or "", style={"font_size": 10, "color": 0xFF777777})

        self._set_status(f"{len(self._workflows)} workflow(s), {len(self._workflow_executions)} execution(s)")

    def _show_wf_execute(self, wf):
        if not self._selected_db or not self._selected_asset:
            return
        db_id = self._selected_db.database_id
        asset_id = self._selected_asset.asset_id
        asset_name = self._selected_asset.asset_name
        self._selected_workflow = wf
        desc = wf.description or wf.workflow_id
        self._wf_stack.clear()
        with self._wf_stack:
            ui.Label(f"  Execute: {desc}", height=20, style={"font_size": 13})
            ui.Label(f"  Asset: {asset_name}", height=18, style={"font_size": 11, "color": 0xFF999999})
            ui.Button("  Run on Entire Asset", height=26,
                      clicked_fn=lambda: _defer(lambda: self._execute_wf(wf, db_id, asset_id, None)))

            non_folders = [f for f in self._files
                           if not f.is_folder and (f.relative_path or f.file_name).strip("/")]
            if non_folders:
                ui.Label("  Or run on a file:", height=18,
                         style={"font_size": 11, "color": 0xFF888888})
                for f in non_folders:
                    fkey = (f.relative_path or f.file_name).strip("/")
                    with ui.HStack(height=22, spacing=HORIZONTAL_SPACING):
                        ui.Label(f"    {fkey}", style={"font_size": 12})
                        ui.Button("Run", width=40,
                                  clicked_fn=lambda k=fkey: _defer(
                                      lambda: self._execute_wf(wf, db_id, asset_id, k)
                                  ))
            ui.Spacer(height=4)
            ui.Button("  Back to workflow list", height=22,
                      clicked_fn=lambda: _defer(self._refresh_wf_panel),
                      style={"Button": {"background_color": 0xFF333333}})

    def _execute_wf(self, wf, db_id, asset_id, file_key):
        target = file_key or "entire asset"
        self._set_status(f"Executing {wf.description or wf.workflow_id} on {target}...")
        try:
            result = self._connector.execute_workflow(
                database_id=db_id, asset_id=asset_id,
                workflow_id=wf.workflow_id, workflow_database_id=wf.database_id,
                file_key=file_key,
            )
            self._set_status(f"Workflow started: {result.get('message', '?')}")
        except VamsCliError as e:
            self._set_status(f"Workflow error: {e}")

    # =========================================================================
    # Export / Upload
    # =========================================================================

    def _refresh_export_panel(self):
        if not self._export_stack:
            return
        self._export_stack.clear()
        with self._export_stack:
            if not self._selected_db or not self._selected_asset:
                ui.Label("  Select an asset first.", height=22, style={"color": 0xFF888888})
                return
            asset_name = self._selected_asset.asset_name
            ui.Label(f"  Export current stage to: {asset_name}",
                     height=20, style={"font_size": 13})
            with ui.HStack(height=22):
                ui.Label("Filename (.usd)", width=LABEL_WIDTH)
                ui.StringField(model=self._export_name_model)
            ui.Button(f"  Export & Upload to \"{asset_name}\"", height=28,
                      clicked_fn=lambda: _defer(self._on_export),
                      tooltip="Export current Isaac Sim stage and upload to the selected asset")

    def _on_export(self):
        filename = self._export_name_model.get_value_as_string().strip()
        if not filename:
            filename = f"{self._selected_asset.asset_name}.usd"
        if not filename.lower().endswith((".usd", ".usda", ".usdc")):
            filename += ".usd"
        asset_name = self._selected_asset.asset_name
        self._set_status(f"Exporting {filename} to {asset_name}...")
        try:
            export_path = os.path.join(tempfile.gettempdir(), filename)
            self._connector.export_and_upload_scene(
                self._selected_db.database_id, asset_name,
                export_path=export_path,
                asset_id=self._selected_asset.asset_id,
            )
            self._set_status(f"Uploaded {filename} to {asset_name}")
        except Exception as e:
            self._set_status(f"Export error: {e}")

    # =========================================================================
    # File actions
    # =========================================================================

    def _file_context(self, f):
        file_key = f.relative_path or f.file_name
        return self._selected_db.database_id, self._selected_asset.asset_id, file_key

    def _on_load_file(self, f):
        db_id, asset_id, file_key = self._file_context(f)
        self._set_status(f"Downloading {file_key}...")
        try:
            local_dir = tempfile.mkdtemp(prefix="vams_")
            if not self._connector.download_file(db_id, asset_id, file_key, local_dir):
                self._set_status(f"Download failed: {file_key}")
                return
            local_file = os.path.abspath(os.path.join(local_dir, file_key.lstrip("/")))
            if not os.path.isfile(local_file):
                self._set_status(f"File not found: {local_file}")
                return
            import omni.usd
            omni.usd.get_context().open_stage(local_file)
            self._set_status(f"Opened {file_key}")
        except Exception as e:
            self._set_status(f"Load error: {e}")

    @staticmethod
    def _sanitize_prim_name(name):
        """Make a string safe for use as a USD prim name (alphanumeric + underscore only)."""
        import re
        # Strip extension, replace invalid chars with underscore
        base = os.path.splitext(os.path.basename(name))[0]
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", base)
        # Prim names cannot start with a digit
        if sanitized and sanitized[0].isdigit():
            sanitized = "_" + sanitized
        return sanitized or "imported"

    def _on_add_usd_reference(self, f):
        db_id, asset_id, file_key = self._file_context(f)
        prim_name = self._sanitize_prim_name(file_key)
        prim_path = f"/World/{prim_name}"
        self._set_status(f"Downloading {file_key}...")
        try:
            local_dir = tempfile.mkdtemp(prefix="vams_")
            if not self._connector.download_file(db_id, asset_id, file_key, local_dir):
                self._set_status(f"Download failed: {file_key}")
                return
            local_file = os.path.abspath(os.path.join(local_dir, file_key.lstrip("/")))
            if not os.path.isfile(local_file):
                self._set_status(f"File not found: {local_file}")
                return
            import omni.usd
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                self._set_status("No active USD stage")
                return
            prim = stage.GetPrimAtPath(prim_path)
            if not prim.IsValid():
                prim = stage.DefinePrim(prim_path)
            prim.GetReferences().AddReference(local_file)
            self._set_status(f"Added reference at {prim_path}")
        except Exception as e:
            self._set_status(f"Reference error: {e}")

    def _on_import_urdf(self, f):
        db_id, asset_id, file_key = self._file_context(f)
        self._set_status(f"Importing URDF {file_key}...")
        try:
            prim = self._connector.download_and_import_urdf(db_id, asset_id, file_key)
            self._set_status(f"Imported URDF as {prim}" if prim else "URDF import failed")
        except Exception as e:
            self._set_status(f"URDF error: {e}")

    def _on_import_mjcf(self, f):
        db_id, asset_id, file_key = self._file_context(f)
        self._set_status(f"Importing MJCF {file_key}...")
        try:
            ok = self._connector.download_and_import_mjcf(db_id, asset_id, file_key)
            self._set_status(f"Imported MJCF {file_key}" if ok else "MJCF import failed")
        except Exception as e:
            self._set_status(f"MJCF error: {e}")

    def _on_download_file(self, f):
        db_id, asset_id, file_key = self._file_context(f)
        local_path = os.path.join(tempfile.gettempdir(), "vams_downloads")
        self._set_status(f"Downloading {file_key}...")
        try:
            if self._connector.download_file(db_id, asset_id, file_key, local_path):
                self._set_status(f"Downloaded to {local_path}")
            else:
                self._set_status(f"Download failed: {file_key}")
        except Exception as e:
            self._set_status(f"Download error: {e}")

    # =========================================================================
    # Error window
    # =========================================================================

    def _show_error_window(self, title, message):
        self._window = ui.Window(title, width=400, height=200)
        with self._window.frame:
            with ui.VStack(spacing=8):
                ui.Label(message, word_wrap=True)
