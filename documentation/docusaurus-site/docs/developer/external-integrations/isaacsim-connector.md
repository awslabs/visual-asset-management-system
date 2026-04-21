# Isaac Sim Connector (EXPERIMENTAL)

The VAMS Connector for Isaac Sim is an [Omniverse Kit](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/extensions_basic.html) extension that integrates NVIDIA Isaac Sim with VAMS for 3D asset management. It enables robotics and simulation engineers to browse, download, upload, and manage assets directly from within Isaac Sim.

:::warning[Experimental]
This integration is in **experimental** status and may still have issues. Verify with your organization before deploying to any production environment.
:::

## Features

-   **Database and Asset Browsing** -- Navigate VAMS databases, assets, and files in a dockable UI panel
-   **Asset Download and Import** -- Download individual files or full assets; USD, URDF, and MJCF files can be imported directly into the Isaac Sim stage from the file list UI. USD files can be opened as new stages or added as prim references. URDF and MJCF files are converted to USD on import using Isaac Sim's built-in asset importers.
-   **General File Download** -- Other files can be downloaded to a local temporary directory
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

## Ubuntu / EC2 Setup

When running Isaac Sim on Ubuntu (e.g., on an Amazon EC2 instance), the VAMS CLI should be installed in an isolated Python virtual environment to avoid conflicts with Isaac Sim's bundled Python.

### Virtual environment installation

```bash
python3 -m venv ~/vamscli-venv
~/vamscli-venv/bin/pip install vamscli
~/vamscli-venv/bin/vamscli setup https://your-api-gateway-url.amazonaws.com
```

Then set the explicit CLI path in `config/extension.toml`:

```toml
[settings]
exts."vams.connector.isaacsim".vamscli_path = "/home/ubuntu/vamscli-venv/bin/vamscli"
```

### SSL certificate bundle

Isaac Sim may override the `SSL_CERT_FILE` environment variable with its own certificate bundle that does not include Amazon's CA certificates, causing S3 download failures. The extension handles this automatically if an Amazon CA certificate bundle is placed in the `certs/` directory.

To set up the certificate bundle:

1.  Download the Amazon root CA certificates from [https://www.amazontrust.com/repository/](https://www.amazontrust.com/repository/)
2.  Concatenate the PEM files into a single file named `amazon-ca-bundle.pem`
3.  Place it at `isaacsim_vams_integration/certs/amazon-ca-bundle.pem`

If the certificate bundle is present, the extension merges it with the system CA bundle (at `/etc/ssl/certs/ca-certificates.crt` on Ubuntu) and configures the CLI subprocess accordingly. If the file is absent, the extension uses the system's default SSL configuration.

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

1.  **Authentication** -- Enter credentials and click the appropriate login button
2.  **Databases** -- List and select databases
3.  **Assets** -- List assets in the selected database
4.  **Files** -- List files in the selected asset with context-aware actions:
    -   **USD files** (`.usd`, `.usda`, `.usdc`, `.usdz`): Click the play button to download and open as a new stage, or click **Add Ref** to add as a reference under `/World/<filename>`
    -   **URDF files** (`.urdf`): Click the play button to download and import the robot description into the current stage
    -   **MJCF files** (`.mjcf`, `.xml`): Click the play button to download and import the MuJoCo model into the current stage
    -   **Other files**: Click the download button to save to a local temporary directory
5.  **Workflows** -- List and execute available workflows

:::tip
After loading a USD asset, if the viewport appears empty, add a light (**Create > Light > Dome Light**) and press **F** to frame the camera on the root prim.
:::

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

# URDF import (requires isaacsim.asset.importer.urdf extension)
prim_path = connector.download_and_import_urdf("my-db", "my-asset", "/robot.urdf")

# MJCF import (requires isaacsim.asset.importer.mjcf extension)
connector.download_and_import_mjcf("my-db", "my-asset", "/ant.xml")
```

:::note
The `download_and_import_asset` and `download_and_add_reference` methods validate that the file has a USD extension (`.usd`, `.usda`, `.usdc`, `.usdz`) before attempting to open or reference it. The URDF and MJCF import methods similarly validate their expected file extensions (`.urdf` for URDF; `.mjcf` or `.xml` for MJCF). The URDF and MJCF importers are optional -- if the corresponding Isaac Sim importer extension is not enabled, the import will fail with an import error.
:::

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
├── certs/
│   └── amazon-ca-bundle.pem           # Optional: Amazon CA certs for SSL (see Ubuntu setup)
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

UI button handlers are deferred via `omni.kit.app`'s update event stream to avoid blocking the Omniverse Kit draw pass, which can cause deadlocks or crashes when performing blocking I/O during rendering.

## Source Location

```
tools/ExternalIntegrations/isaacsim_vams_integration/
```

## Related Pages

-   [External Tool Integrations Overview](overview.md) -- All available integrations
-   [CLI Installation](../../cli/installation.md) -- Installing the VAMS CLI
-   [Workflows](../../api/workflows.md) -- Workflow API reference
