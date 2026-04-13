# VAMS Connector for Isaac Sim (EXPERIMENTAL)

[![Isaac Sim](https://img.shields.io/badge/Isaac%20Sim-5.1+-green.svg)](https://developer.nvidia.com/isaac-sim)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Experimental-orange.svg)]()

> **EXPERIMENTAL**: This plugin is in experimental status and may still have issues. Verify with your organization before deploying to any production environment.

An [Omniverse Kit](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/extensions_basic.html) extension that integrates [NVIDIA Isaac Sim](https://developer.nvidia.com/isaac-sim) with [Visual Asset Management System on AWS (VAMS)](https://github.com/awslabs/visual-asset-management-system), a cloud-based visual data management platform. The extension enables robotics and simulation engineers to browse, download, upload, and manage 3D assets directly from within Isaac Sim.

## Key Features

### Database & Asset Browsing

-   **Hierarchical Browser**: Navigate VAMS databases, assets, and files in a dockable UI panel
-   **Real-time Authentication**: Cognito login and token/API key support via the VAMS CLI
-   **File Metadata**: View file names, sizes, types, and preview info

### Asset Download & Import

-   **Single File Download**: Download individual files from any asset
-   **Full Asset Download**: Recursively download all files in an asset with progress tracking
-   **Stage Import**: Download a USD file and open it as a new Isaac Sim stage
-   **Reference Import**: Download a USD file and add it as a reference to the current stage at a specified prim path

### Scene Export & Upload

-   **Scene Export**: Export the current Isaac Sim USD stage to a local file
-   **Upload to VAMS**: Upload files or directories to existing assets
-   **Create & Upload**: Create a new VAMS asset and upload a file in a single operation

### Workflow Execution

-   **Browse Workflows**: List all available processing workflows
-   **Execution History**: View past workflow executions on any asset
-   **Trigger Workflows**: Execute workflows on assets directly from the UI or scripting API

---

## Getting Started

### Prerequisites

-   **NVIDIA Isaac Sim 5.1+**
-   **Python 3.10+** (included with Isaac Sim)
-   **VAMS CLI** installed and configured (see below)

### VAMS CLI Setup

The connector uses the **VAMS CLI** (`vamscli`) for all VAMS operations. The CLI must be installed and configured before using the extension.

#### 1. Install the VAMS CLI

```bash
# From the VAMS repository
pip install -e /path/to/vams/tools/VamsCLI

# Or if published to a package registry
pip install vamscli
```

Verify the installation:

```bash
vamscli --version
```

#### 2. Configure a VAMS CLI profile

Run the setup command with your VAMS API Gateway URL (found in your CloudFormation stack outputs):

```bash
vamscli setup https://your-api-gateway-url.amazonaws.com
```

This creates a `default` profile that stores the API endpoint configuration. You only need to run this once per VAMS deployment.

#### 3. Verify the CLI works

```bash
vamscli auth login -u your-email@example.com
vamscli database list --json-output
```

### Authentication Methods

The connector supports two authentication methods:

#### Cognito Authentication (Username/Password)

For VAMS deployments using AWS Cognito as the identity provider. Use the `login()` method or enter your username and password in the extension UI.

CLI equivalent:

```bash
vamscli auth login -u user@example.com -p yourpassword
```

#### Token Override Authentication (IDP JWT Token or VAMS API Key)

For VAMS deployments using an external identity provider, or when authenticating with a VAMS API key. Use the `login_with_token()` method or enter your user ID and token in the extension UI.

-   **VAMS API keys** are prefixed with `vams_` (e.g., `vams_abc123...`)
-   **IDP JWT tokens** are standard JWT strings from your external identity provider

CLI equivalent:

```bash
vamscli auth login --user-id user@example.com --token-override "vams_your-api-key"
vamscli auth login --user-id user@example.com --token-override "eyJhbGciOiJSUzI1NiIs..."
```

---

## Installation

### Option A: Extension search path (recommended)

In Isaac Sim, go to **Window > Extensions**, click the gear icon, and add the path to this directory as an extension search path:

```
/path/to/isaacsim_vams_integration
```

Then search for "VAMS" and enable `vams.connector.isaacsim`.

### Option B: Command line

```bash
isaac-sim --ext-folder /path/to/isaacsim_vams_integration --enable vams.connector.isaacsim
```

### Option C: In your app .kit file

```toml
[dependencies]
"vams.connector.isaacsim" = {}

[settings.app.exts]
folders = ["/path/to/isaacsim_vams_integration"]
```

---

## Usage

### Extension UI

Once enabled, the extension opens a **VAMS Connector** window with:

1. **Authentication** - Enter username + password (Cognito) or user ID + token/API key, then click the appropriate login button
2. **Databases** - Click to list all databases, then click one to select it
3. **Assets** - Lists assets in the selected database, click to select
4. **Files** - Lists files in the selected asset
5. **Workflows** - Lists available workflows with an Execute button per workflow

### Scripting API

The connector can also be used programmatically from the Isaac Sim Python console or scripts:

#### Authentication

```python
from vams.connector.isaacsim import IsaacVAMSConnector

connector = IsaacVAMSConnector()

# Cognito username/password
connector.login("user@example.com", "password")

# Or with a VAMS API key (prefixed vams_)
connector.login_with_token("user@example.com", "vams_your-api-key-here")

# Or with an IDP JWT token
connector.login_with_token("user@example.com", "eyJhbGciOiJSUzI1NiIs...")
```

#### Browse Databases, Assets & Files

```python
databases = connector.list_databases()
for db in databases:
    print(f"{db.database_id}: {db.description} ({db.asset_count} assets)")

assets = connector.list_assets(databases[0].database_id)
for asset in assets:
    print(f"{asset.asset_id}: {asset.asset_name}")

files = connector.list_files(databases[0].database_id, assets[0].asset_id)
for f in files:
    print(f"{f.relative_path} ({f.size:,} bytes)")
```

#### Download Files & Assets

```python
# Download a single file
connector.download_file("my-db", "my-asset", "/model.usd", "/tmp/downloads")

# Download an entire asset (all files, recursively)
result = connector.download_asset("my-db", "my-asset", "/tmp/full_asset")
print(f"Downloaded {result.successful_files}/{result.total_files} files")
```

#### Upload Files

```python
# Upload a file to an existing asset
connector.upload_file("my-db", "my-asset", "/local/path/to/model.usd")

# Create a new asset and upload a file in one step
connector.create_and_upload("my-db", "new-asset", "/local/path/to/model.usd")

# Upload a directory
connector.upload_directory("my-db", "my-asset", "/local/path/to/scene_files")
```

#### Workflows

```python
# List available workflows
workflows = connector.list_workflows()
for wf in workflows:
    print(f"{wf.workflow_id}: {wf.description}")

# Check execution history on an asset
executions = connector.list_workflow_executions("my-db", "my-asset")
for ex in executions:
    print(f"{ex.execution_id}: {ex.execution_status}")

# Execute a workflow
result = connector.execute_workflow("my-db", "my-asset", "wf-id", "wf-db-id")
print(f"Started execution: {result.get('message')}")
```

#### Isaac Sim Stage Operations

```python
# Export the current stage and upload to VAMS
connector.export_and_upload_scene("my-db", "scene_v1", description="Exported from Isaac Sim")

# Download a USD file and open it as a new stage
connector.download_and_import_asset("my-db", "my-asset", "/scene.usd")

# Download a USD file and add it as a reference to the current stage
connector.download_and_add_reference("my-db", "my-asset", "/robot.usd", "/World/Robot")
```

---

## Configuration

### Extension Settings

Settings can be configured in `config/extension.toml` or overridden at runtime via Carbonite settings:

| Setting                                       | Default     | Description                                            |
| --------------------------------------------- | ----------- | ------------------------------------------------------ |
| `exts."vams.connector.isaacsim".profile`      | `"default"` | VAMS CLI profile name                                  |
| `exts."vams.connector.isaacsim".vamscli_path` | `""`        | Explicit path to vamscli executable (empty = use PATH) |

### Multiple VAMS Deployments

Use VAMS CLI profiles to connect to different VAMS deployments:

```bash
vamscli --profile dev setup https://dev-api.example.com
vamscli --profile prod setup https://prod-api.example.com
```

```python
dev_connector = IsaacVAMSConnector(profile="dev")
prod_connector = IsaacVAMSConnector(profile="prod")
```

---

## Architecture

```
isaacsim_vams_integration/
├── config/
│   └── extension.toml                 # Extension metadata, dependencies, settings
├── vams/
│   └── connector/
│       └── isaacsim/
│           ├── __init__.py            # Package exports (IExt class discovered here)
│           ├── extension.py           # VamsConnectorExtension(omni.ext.IExt)
│           ├── connector.py           # IsaacVAMSConnector (high-level API)
│           └── vams_cli_service.py    # VamsCliService (CLI subprocess wrapper)
├── README.md
├── LICENSE
└── requirements.txt
```

### Key Components

| Component       | File                  | Purpose                                                                                      |
| --------------- | --------------------- | -------------------------------------------------------------------------------------------- |
| **Extension**   | `extension.py`        | `omni.ext.IExt` subclass with `on_startup`/`on_shutdown` lifecycle, builds the UI window     |
| **Connector**   | `connector.py`        | High-level API for auth, browse, download, upload, workflows, and Isaac Sim stage operations |
| **CLI Service** | `vams_cli_service.py` | Subprocess wrapper around `vamscli` commands, parses JSON output into typed dataclasses      |

### How It Works

```
Isaac Sim UI / Python Console
  └── IsaacVAMSConnector (connector.py)
        └── VamsCliService (vams_cli_service.py)
              └── vamscli subprocess (--json-output)
                    └── VAMS API Gateway
```

All VAMS API interactions go through the VAMS CLI with `--json-output` for structured responses. No direct HTTP calls or AWS SDK usage in the extension.

---

## Troubleshooting

| Issue                               | Solution                                                                                                   |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **"VAMS CLI not found"**            | Install the CLI: `pip install vamscli` and ensure it is on PATH                                            |
| **"Profile may not be configured"** | Run `vamscli setup <api-gateway-url>` to configure the default profile                                     |
| **Login fails (Cognito)**           | Verify credentials with `vamscli auth login -u <email>` in a terminal                                      |
| **Login fails (token/API key)**     | Verify with `vamscli auth login --user-id <email> --token-override <token>`                                |
| **Extension not found**             | Ensure the extension search path points to the `isaacsim_vams_integration/` directory (not a subdirectory) |
| **omni.usd import errors**          | Stage operations (`export_and_upload_scene`, etc.) must be called from within Isaac Sim                    |

---

## Security

-   Credentials are managed by the VAMS CLI profile system, not stored in extension code
-   For headless or non-interactive environments, use VAMS API keys (prefixed `vams_`) or IDP JWT tokens via `login_with_token()`
-   The VAMS CLI handles Cognito token refresh automatically
-   The extension caches credentials in memory only for automatic re-authentication during the session

---

## Licensing

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.

A copy of the license is available in the repository's [LICENSE](LICENSE) file.
