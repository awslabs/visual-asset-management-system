# Isaac Sim Connector (EXPERIMENTAL)

The VAMS Connector for Isaac Sim is an [Omniverse Kit](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/extensions_basic.html) extension that integrates NVIDIA Isaac Sim with VAMS for 3D asset management. It enables robotics and simulation engineers to browse, download, upload, and manage assets directly from within Isaac Sim.

:::warning[Experimental]
This integration is in **experimental** status and may still have issues. Verify with your organization before deploying to any production environment.
:::

## Features

-   **Database and Asset Browsing** -- Navigate VAMS databases, assets, and files in a dockable UI panel
-   **Asset Download and Import** -- Download individual files or full assets, open USD files as new stages, or add them as references to existing stages
-   **Scene Export and Upload** -- Export the current Isaac Sim USD stage and upload it to VAMS as a new or existing asset
-   **Workflow Execution** -- List available processing workflows, view execution history, and trigger new workflow runs on assets
-   **Dual Authentication** -- Cognito username/password login and token override authentication (IDP JWT tokens and VAMS API keys)

## Prerequisites

-   **NVIDIA Isaac Sim 5.1+**
-   **Python 3.10+** (included with Isaac Sim)
-   **VAMS CLI** installed and configured

### VAMS CLI setup

Install the VAMS CLI on the machine running Isaac Sim:

```bash
pip install -e /path/to/vams/tools/VamsCLI
```

Configure a profile with your VAMS API Gateway URL:

```bash
vamscli setup https://your-api-gateway-url.amazonaws.com
```

Verify the CLI is working:

```bash
vamscli auth login -u your-email@example.com
vamscli database list --json-output
```

## Installation

### Option A: Extension search path (recommended)

In Isaac Sim, go to **Window > Extensions**, click the gear icon, and add the path to the extension directory as a search path:

```
/path/to/tools/ExternalIntegrations/isaacsim_vams_integration
```

Then search for "VAMS" and enable `vams.connector.isaacsim`.

### Option B: Command line

```bash
isaac-sim --ext-folder /path/to/tools/ExternalIntegrations/isaacsim_vams_integration --enable vams.connector.isaacsim
```

### Option C: App .kit file

```toml
[dependencies]
"vams.connector.isaacsim" = {}

[settings.app.exts]
folders = ["/path/to/tools/ExternalIntegrations/isaacsim_vams_integration"]
```

## Authentication

The connector supports two authentication methods:

### Cognito authentication

For VAMS deployments using AWS Cognito as the identity provider:

```python
connector.login("user@example.com", "password")
```

CLI equivalent:

```bash
vamscli auth login -u user@example.com -p yourpassword
```

### Token override authentication

For external identity providers or VAMS API keys (prefixed `vams_`):

```python
connector.login_with_token("user@example.com", "vams_your-api-key")
connector.login_with_token("user@example.com", "eyJhbGciOiJSUzI1NiIs...")
```

CLI equivalent:

```bash
vamscli auth login --user-id user@example.com --token-override "vams_your-api-key"
```

## Usage

### Extension UI

Once enabled, the extension opens a **VAMS Connector** window with:

1. **Authentication** -- Enter credentials and click the appropriate login button
2. **Databases** -- List and select databases
3. **Assets** -- List assets in the selected database
4. **Files** -- List files in the selected asset
5. **Workflows** -- List and execute available workflows

### Scripting API

The connector can be used programmatically from the Isaac Sim Python console:

```python
from vams.connector.isaacsim import IsaacVAMSConnector

connector = IsaacVAMSConnector()
connector.login("user@example.com", "password")

# Browse
databases = connector.list_databases()
assets = connector.list_assets(databases[0].database_id)
files = connector.list_files(databases[0].database_id, assets[0].asset_id)

# Download
connector.download_file("my-db", "my-asset", "/model.usd", "/tmp/downloads")
result = connector.download_asset("my-db", "my-asset", "/tmp/full_asset")

# Upload
connector.upload_file("my-db", "my-asset", "/local/model.usd")
connector.create_and_upload("my-db", "new-asset", "/local/model.usd")

# Workflows
workflows = connector.list_workflows()
connector.execute_workflow("my-db", "my-asset", "wf-id", "wf-db-id")

# Isaac Sim stage operations
connector.export_and_upload_scene("my-db", "scene_v1")
connector.download_and_import_asset("my-db", "my-asset", "/scene.usd")
connector.download_and_add_reference("my-db", "my-asset", "/robot.usd", "/World/Robot")
```

## Configuration

Extension settings can be configured in `config/extension.toml` or overridden at runtime:

| Setting                                       | Default     | Description                                           |
| :-------------------------------------------- | :---------- | :---------------------------------------------------- |
| `exts."vams.connector.isaacsim".profile`      | `"default"` | VAMS CLI profile name                                 |
| `exts."vams.connector.isaacsim".vamscli_path` | `""`        | Explicit path to vamscli executable (empty uses PATH) |

### Multiple VAMS deployments

Use VAMS CLI profiles to connect to different deployments:

```bash
vamscli --profile dev setup https://dev-api.example.com
vamscli --profile prod setup https://prod-api.example.com
```

```python
dev_connector = IsaacVAMSConnector(profile="dev")
prod_connector = IsaacVAMSConnector(profile="prod")
```

## Architecture

```
isaacsim_vams_integration/
├── config/
│   └── extension.toml                 # Extension metadata, dependencies, settings
├── vams/
│   └── connector/
│       └── isaacsim/
│           ├── __init__.py            # Package exports
│           ├── extension.py           # VamsConnectorExtension(omni.ext.IExt)
│           ├── connector.py           # IsaacVAMSConnector
│           └── vams_cli_service.py    # VamsCliService (CLI subprocess wrapper)
├── README.md
├── LICENSE
└── requirements.txt
```

| Component   | File                  | Purpose                                                                   |
| :---------- | :-------------------- | :------------------------------------------------------------------------ |
| Extension   | `extension.py`        | `omni.ext.IExt` subclass with `on_startup`/`on_shutdown` lifecycle and UI |
| Connector   | `connector.py`        | High-level API for all VAMS operations and Isaac Sim stage integration    |
| CLI Service | `vams_cli_service.py` | Subprocess wrapper around `vamscli` with JSON output parsing              |

## Source Location

```
tools/ExternalIntegrations/isaacsim_vams_integration/
```

## Related Pages

-   [External Tool Integrations Overview](overview.md) -- All available integrations
-   [CLI Installation](../../cli/installation.md) -- Installing the VAMS CLI
-   [Workflows](../../api/workflows.md) -- Workflow API reference
