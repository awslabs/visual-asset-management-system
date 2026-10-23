# VAMS Connector for Isaac Sim (EXPERIMENTAL)

[![Isaac Sim](https://img.shields.io/badge/Isaac%20Sim-5.1%20%7C%206.x-green.svg)](https://developer.nvidia.com/isaac-sim)
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
-   **Stage Import**: Download a USD file and open it as a new Isaac Sim stage (via UI button or scripting API)
-   **Reference Import**: Download a USD file and add it as a reference to the current stage: from the UI at a prim of its own under `/World`, named after the file, and from the scripting API at the prim path given. The reference brings in the file's default prim, or its one root prim when it has none; a file with neither is refused and leaves the stage as it was
-   **URDF Import**: Download a URDF file and import it into the current stage using Isaac Sim's URDF importer (requires `isaacsim.asset.importer.urdf`), under the prim the stage's default prim names, or at the stage root when it names none. The stage's default prim is left as it was
-   **MJCF Import**: Download a MJCF/MuJoCo XML file and import it into the current stage using Isaac Sim's MJCF importer (requires `isaacsim.asset.importer.mjcf`), at a prim of its own under `/World`, named after the file. A file that is not well-formed XML is refused before the importer runs, an import that brings in nothing from the file is removed again, and the stage's default prim is left as it was
-   **General Download**: Download any other file to `~/vams_downloads`, keeping its path within the asset

A `.usd`, `.usda` or `.usdc` layer, a URDF file and a MJCF file name other files of the asset by relative path (sublayers, references, textures, meshes), so opening or importing one downloads the whole asset, laid out as it is in VAMS, into a new private temporary directory. The status line reports how many of the asset's other files did not download, and a requested file that did not download with the reason the download gave, for example `Reference error: /robot.usd did not download: <error>`. A `.usdz` package carries its dependencies and is downloaded alone.

URDF and MJCF import work on Isaac Sim 5.1 and on Isaac Sim 6.x (6.0, 6.1 and 6.2; the EDH Workstations Isaac Sim desktop ships 6.2.0). The connector uses whichever importer API the running Isaac Sim provides. On 6.x the importers convert a file into a USD file of their own instead of writing into the open stage: the connector has them write it to a new private temporary directory (`vams_urdf_usd_*` or `vams_mjcf_usd_*`), never next to the downloaded file, and adds that file to the current stage as a reference, at the same prim as on 5.1. The prims under it follow the 6.x importers' own layout. The directory is kept, since the stage refers to it, so a stage saved with the robot resolves only while it is there; **Export & Upload** flattens the robot into the export, and counts a file path the robot names in that directory among the paths that resolve only on this machine. On 6.x:

-   `fix_base` is passed as fixed or mobile, never left to the file, and `fix_base=False` also removes a fixed joint to the world that the file itself declares
-   The importers have no `import_inertia_tensor` option, and the MJCF importer has no `merge_fixed_joints`; a call that sets one to other than its default imports without it and logs a warning
-   The MJCF importer reads only `.xml` files, so a `.mjcf` file is imported from a temporary `.xml` copy beside it, removed again afterwards
-   An import that fails leaves the stage as it was, and Isaac Sim's log carries the importer's reason

### Scene Export & Upload

-   **Scene Export**: Export the current Isaac Sim USD stage to a local file
-   **Upload to VAMS**: Upload files or directories to existing assets
-   **Create & Upload**: Create a new VAMS asset and upload a file in a single operation

Export & Upload writes the current stage, flattened into a single layer, to the root of the selected asset. A texture, reference or sublayer path to a file downloaded with that same asset is rewritten relative to the asset root, so the export resolves in VAMS and in a whole-asset download. A path to any other local file, including a file of another asset or one written into a download directory afterwards (such as importer output), is left as it is and resolves only on the machine that exported the stage; the status line reports how many such paths the export contains, and `export_and_upload_scene` returns the count as `unportablePaths`.

### Workflow Execution

-   **Browse Workflows**: List all available processing workflows
-   **Execution History**: View past workflow executions on any asset, each with its input file's path within the asset
-   **Trigger Workflows**: Execute workflows on assets directly from the UI or scripting API

The Execute view offers **Run** only for the files that pass the input-file filters of the workflow and of its first pipeline, as the template in use sets them, and **Run on Entire Asset** reports that no file passes, instead of running, when none of the asset's files does. A later pipeline's filters do not limit **Run**: that pipeline also receives the files the earlier pipelines write and VAMS checks it when the run reaches it, so a workflow whose second pipeline reads only the GLB its first pipeline converts an OBJ to offers **Run** for the OBJ. A pipeline that requires a template and names no default (no `defaultTemplateId` on the workflow's reference to it and no default template) gets a template choice; **Run** is refused until one is chosen, and the run passes it as that pipeline's `templateId`. Choosing one replaces the status line's `Choose a template for <pipeline> before running`, or an earlier choice's status, with `Template for <pipeline>: <template>`. Template tags are not collected: a run the service refuses for a required tag shows the refusal with a hint to run the workflow from the VAMS web UI. The view reads each pipeline once when it opens; a pipeline that cannot be read does not gate **Run**, and the status line says so.

---

## Getting Started

### Prerequisites

-   **NVIDIA Isaac Sim 5.1 or 6.x** (6.0, 6.1, 6.2; the EDH Workstations Isaac Sim desktop ships 6.2.0)
-   **Python 3.10+** (included with Isaac Sim)
-   **VAMS CLI** installed and configured (see below)

### VAMS CLI Setup

The connector uses the **VAMS CLI** (`vamscli`) for all VAMS operations. The CLI must be installed and configured before using the extension.

#### 1. Install the VAMS CLI

```bash
# From the VAMS repository; VamsCLI is not published on PyPI
pip install -e /path/to/vams/tools/VamsCLI
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

Kit treats each child of an extension folder as an extension named after that child, so this directory goes into a folder of its own under the extension's name, `vams.connector.isaacsim`. Copy it there, and run the same commands again after updating the connector; they remove the earlier copy first, since `cp -r` would otherwise place the new one inside it:

```bash
mkdir -p /path/to/kit-exts
rm -rf /path/to/kit-exts/vams.connector.isaacsim
cp -r /path/to/isaacsim_vams_integration /path/to/kit-exts/vams.connector.isaacsim
```

Kit loads the copy, so the `extension.toml` settings and the certificate bundle described below go into `/path/to/kit-exts/vams.connector.isaacsim`, and are made again after each update.

### Option A: Extension search path (recommended)

In Isaac Sim, go to **Window > Extensions**, click the gear icon, and add the folder that contains `vams.connector.isaacsim` as an extension search path:

```
/path/to/kit-exts
```

Then search for "VAMS" and enable `vams.connector.isaacsim`.

### Option B: Command line

### Ubuntu Example

```bash
/opt/IsaacSim/isaac-sim.sh --ext-folder /path/to/kit-exts --enable vams.connector.isaacsim
```

### Option C: In your app .kit file

```toml
[dependencies]
"vams.connector.isaacsim" = {}

[settings.app.exts]
folders = ["/path/to/kit-exts"]
```

---

## Ubuntu / EC2 Setup

When running Isaac Sim on Ubuntu (e.g., on an EC2 instance), the VAMS CLI should be installed in an isolated Python virtual environment to avoid conflicts with Isaac Sim's bundled Python.

### 1. Create a virtual environment and install the CLI

```bash
python3 -m venv ~/vamscli-venv
~/vamscli-venv/bin/pip install -e /path/to/vams/tools/VamsCLI
```

### 2. Configure a VAMS CLI profile

```bash
~/vamscli-venv/bin/vamscli setup https://your-api-gateway-url.amazonaws.com
```

### 3. Set the vamscli path in extension.toml

Point the extension to the venv executable so it does not depend on `PATH`, in `/path/to/kit-exts/vams.connector.isaacsim/config/extension.toml`:

```toml
[settings]
exts."vams.connector.isaacsim".vamscli_path = "/home/ubuntu/vamscli-venv/bin/vamscli"
```

### 4. Verify the CLI works

```bash
~/vamscli-venv/bin/vamscli auth login -u your-email@example.com -p yourpassword
~/vamscli-venv/bin/vamscli database list --json-output
```

### 5. Load the extension in Isaac Sim

Using the command line, with the folder that contains `vams.connector.isaacsim` (adjust paths for your installation):

```bash
/opt/IsaacSim/isaac-sim.sh --ext-folder /path/to/kit-exts --enable vams.connector.isaacsim
```

Or via **Window > Extensions** in the Isaac Sim UI (see [Installation](#installation) above).

### SSL Certificate Bundle (Optional)

Isaac Sim may set its own `SSL_CERT_FILE` environment variable that does not include Amazon's CA certificates, causing S3 download failures. The extension never passes that value to the CLI subprocess: it names the operating system's CA bundle instead, merged with a certificate bundle placed in `certs/` when one is present.

To set up the CA certificate bundle:

1.  Download the Amazon Root CA certificates from [https://www.amazontrust.com/repository/](https://www.amazontrust.com/repository/)
2.  Concatenate the root certificates into a single PEM file named `amazon-ca-bundle.pem`
3.  Place it in `/path/to/kit-exts/vams.connector.isaacsim/certs/`, the `certs/` directory of the copy Kit loads:

```
/path/to/kit-exts/vams.connector.isaacsim/
├── certs/
│   └── amazon-ca-bundle.pem    # <-- place the file here
├── config/
│   └── extension.toml
├── vams/
│   └── ...
```

If the certificate bundle file is present, the extension will automatically merge it with the system CA bundle (if available at `/etc/ssl/certs/ca-certificates.crt`) and configure the CLI subprocess accordingly. If the file is not present, the extension skips this step and relies on the system's default SSL configuration.

---

## Usage

### Extension UI

Once enabled, the extension opens a **VAMS Connector** window with:

1.  **Authentication** - Enter username + password (Cognito) or user ID + token/API key, then click the appropriate login button
2.  **Databases** - Click to list all databases, then click one to select it
3.  **Assets** - Lists assets in the selected database, click to select
4.  **Files** - Lists files in the selected asset; select a file to show the actions for its type:
    -   **USD files** (`.usd`, `.usda`, `.usdc`, `.usdz`): **Open Stage (replace entire scene)** downloads the file and opens it as a new stage. **Add USD Reference (add to current scene)** adds it as a reference under `/World/<name>`, the file name without its extension and with each character other than a letter, digit or underscore replaced by `_`, or under `<name>_1`, `<name>_2`, ... when that prim exists, so each add gets a prim of its own; the status line names the prim used. The reference brings in the file's default prim, or its one root prim when it has none, counting the root prims of the layers it sublayers; a file with neither is refused, the stage is left as it was, and the status line gives the reason (`Reference error: <file> has no default prim (<n> root prims), so a reference would bring in nothing. Open it with Open Stage, or set a default prim on the file.`)
    -   **URDF files** (`.urdf`): **Import URDF (add to current scene)** downloads the robot description and imports it into the current stage, under the prim the stage's default prim names, or at the stage root when it names none, and under `<name>_0` when `<name>` is taken there (on Isaac Sim 6.x `<name>` is made from the file name as for a USD reference, followed by `<name>_1`, `<name>_2`, ...); the stage's default prim is left as it was, and the status line names the prim used (`Imported URDF as <prim>`) or reads `URDF import failed`
    -   **MJCF files** (`.mjcf`, `.xml`): **Import MJCF (add to current scene)** downloads the MuJoCo model and imports it into the current stage under `/World/<name>`, with `<name>` made from the file name as for a USD reference, or under `<name>_1`, `<name>_2`, ... when that prim exists; the stage's default prim is left as it was, and the status line names the prim used (`Imported MJCF <file> as <prim>`). A file that is not well-formed XML is refused before the importer runs (`MJCF error: <file> is not well-formed XML: <parser message>`), and an import that brings in nothing from the file is removed again, together with a `/World` it created, and the status line reads `MJCF import failed: <file>`
    -   **Any file**: **Download Only (save to disk)** saves it to `~/vams_downloads`, under its path within the asset
5.  **Workflows** - Lists the enabled workflows of `GLOBAL` and the selected database, and the asset's execution history, where each execution's input file is shown by its path within the asset and a value too long for its column is shortened, with the workflow ID and the file's full key in tooltips. Choosing a workflow opens its Execute view: **Run on Entire Asset** where the workflow takes a whole asset, **Run** for each file the workflow and its first pipeline accept, and a template choice for a pipeline that requires one (see [Workflow Execution](#workflow-execution))

When the extension's CLI profile is already signed in, as on a desktop that signs the profile in when it starts, the window takes that session over at startup, and **Refresh Databases** takes over a sign-in that completes after Isaac Sim started. Closing Isaac Sim leaves a session taken over this way signed in, and signs out one entered under **Authentication**. A request refused on a session taken over this way reads `access refused - if this desktop was just started, its VAMS sign-in may not have completed; …` on the status line, followed by the CLI's error. A key taken over this way lasts as long as whatever signed the profile in allows, and once the profile is signed in again with a new key the window uses it from its next request. For the sign-in of an EDH Workstations desktop, see [The workstation's VAMS sign-in](../../../documentation/docusaurus-site/docs/developer/edh-workstations/user-guide.md#the-workstations-vams-sign-in).

### Viewing a loaded asset

After a stage opens, if the viewport appears empty:

1.  **Add a light**: **Create > Light > Dome Light** (downloaded assets may have no lights)
2.  **Frame the camera**: Select the root prim in the Stage panel, then press **F** in the viewport

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
# Download a single file; one that does not download raises VamsCliError naming the download's error
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
    print(f"{wf.workflow_id} ({wf.workflow_name}): {wf.description}")

# Check execution history on an asset
executions = connector.list_workflow_executions("my-db", "my-asset")
for ex in executions:
    print(f"{ex.execution_id}: {ex.execution_status}")

# Execute a workflow on the whole asset, or pass file_key for a single file
result = connector.execute_workflow("my-db", "my-asset", "wf-id", "wf-db-id")
print(f"Started execution: {result.get('executionId')}")
for warning in result.get("warnings", []):
    print(f"  warning: {warning}")

# Read a pipeline and its templates; a pipeline that requires a template and names no default is
# run with one named for it, keyed by pipelineId
pipeline = connector.get_pipeline("GLOBAL", "my-pipeline")
templates = connector.list_pipeline_templates("GLOBAL", "my-pipeline")
print(pipeline.requires_template, [(t.template_id, t.is_default) for t in templates])
result = connector.execute_workflow(
    "my-db", "my-asset", "wf-id", "wf-db-id", file_key="/model.stl",
    pipeline_parameters={"my-pipeline": {"templateId": "my-template"}},
)
```

#### Isaac Sim Stage Operations

```python
# Export the current stage and upload to VAMS
connector.export_and_upload_scene("my-db", "scene_v1", description="Exported from Isaac Sim")

# Download a USD file and open it as a new stage
connector.download_and_import_asset("my-db", "my-asset", "/scene.usd")

# Download a USD file and add it as a reference to the current stage; returns False, with the reason
# logged, for a file that did not download or has no prim a reference could bring in; a prim
# already at the path keeps its references and gets this one as well
connector.download_and_add_reference("my-db", "my-asset", "/robot.usd", "/World/Robot")

# Reference a USD file already on disk; raises UnreferenceableLayerError (a VamsCliError, imported
# from vams.connector.isaacsim.connector) for a file with no default prim and no single root prim
import omni.usd
connector.add_reference_to_stage(omni.usd.get_context().get_stage(), "/World/Robot", "/tmp/robot.usd")
```

#### URDF & MJCF Import

Requires the `isaacsim.asset.importer.urdf` and/or `isaacsim.asset.importer.mjcf` extensions to be enabled in Isaac Sim. The same calls work on Isaac Sim 5.1 and 6.x; see [Asset Download & Import](#asset-download--import) for what differs on 6.x.

```python
# Import a URDF robot description into the current stage
prim_path = connector.download_and_import_urdf(
    "my-db", "my-asset", "/robot.urdf",
    fix_base=True, import_inertia_tensor=True,
)

# Import a MJCF/MuJoCo model into the current stage, under /World; returns the prim path, or None,
# with the reason logged, for a file that did not download, is not well-formed XML or imports nothing
prim_path = connector.download_and_import_mjcf(
    "my-db", "my-asset", "/ant.xml",
    fix_base=True, import_inertia_tensor=True,
)

# Import a URDF or MJCF file already on disk; both return the prim path, or None. import_mjcf_file
# raises MalformedXmlError (a VamsCliError, imported from vams.connector.isaacsim.connector) for a
# file that is not well-formed XML. On Isaac Sim 6.x, import_inertia_tensor (and, for MJCF,
# merge_fixed_joints) have no effect, and fix_base=False makes the robot mobile
prim_path = connector.import_urdf_file("/tmp/robot/robot.urdf", fix_base=True)
prim_path = connector.import_mjcf_file("/tmp/ant/ant.xml")
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
├── certs/
│   └── amazon-ca-bundle.pem           # Optional: Amazon CA certs for SSL (see Ubuntu setup)
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

UI button handlers are deferred via `omni.kit.app`'s update event stream to avoid blocking the Omniverse Kit draw pass, which can cause deadlocks or crashes when performing blocking I/O (subprocess calls) during rendering.

---

## Troubleshooting

| Issue                               | Solution                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **"VAMS CLI not found"**            | Install the CLI from the VAMS repository, `pip install -e /path/to/vams/tools/VamsCLI`, and ensure it is on PATH, or set `vamscli_path` in extension.toml                                                                                                                                                                                                                                                                                                                                                                             |
| **"Profile may not be configured"** | Run `vamscli setup <api-gateway-url>` to configure the default profile                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Login fails (Cognito)**           | Verify credentials with `vamscli auth login -u <email>` in a terminal                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Login fails (token/API key)**     | Verify with `vamscli auth login --user-id <email> --token-override <token>`                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **"access refused" status**         | The session taken over from the CLI profile holds a key VAMS no longer accepts: a previous key while the desktop's own sign-in is still running, or a key that has expired. Select **Refresh Databases** once the sign-in completes, or sign in under **Authentication**; an expired key stays refused until the profile is signed in again. On an EDH Workstations desktop, see [The workstation's VAMS sign-in](../../../documentation/docusaurus-site/docs/developer/edh-workstations/user-guide.md#the-workstations-vams-sign-in) |
| **Extension not found**             | Ensure the extension search path names the folder that contains the connector as `vams.connector.isaacsim` (see [Installation](#installation)), not the `isaacsim_vams_integration/` directory itself                                                                                                                                                                                                                                                                                                                                 |
| **omni.usd import errors**          | Stage operations (`export_and_upload_scene`, etc.) must be called from within Isaac Sim                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **SSL/S3 download errors**          | See [SSL Certificate Bundle](#ssl-certificate-bundle-optional) in the Ubuntu setup section                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Viewport empty after load**       | Add a light (Create > Light > Dome Light) and press F to frame the camera                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **URDF/MJCF import fails**          | Ensure `isaacsim.asset.importer.urdf` or `isaacsim.asset.importer.mjcf` is enabled in Isaac Sim. On Isaac Sim 6.x, Isaac Sim's log names the importer's reason (`URDF import of <file> failed: <reason>`, and likewise for MJCF)                                                                                                                                                                                                                                                                                                      |
| **"is not well-formed XML" status** | The MJCF file cannot be parsed as XML, so the importer did not run and the stage is unchanged. Correct the file at the line and column the parser message gives, and upload it again                                                                                                                                                                                                                                                                                                                                                  |
| **"MJCF import failed" status**     | The MJCF importer brought in nothing from the file, for example from an `.xml` file that is not a MuJoCo model, and the prim the import created was removed again; on Isaac Sim 6.x, the importer failed or wrote nothing a reference could bring in, and the stage is unchanged. Isaac Sim's log carries the importer's own messages                                                                                                                                                                                                 |
| **UI freezes on button click**      | This should not happen with the deferred execution pattern; if it does, file an issue                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

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
