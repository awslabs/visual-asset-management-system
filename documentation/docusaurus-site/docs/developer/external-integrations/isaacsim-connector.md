# Isaac Sim Connector (EXPERIMENTAL)

The VAMS Connector for Isaac Sim is an [Omniverse Kit](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/extensions_basic.html) extension that integrates NVIDIA Isaac Sim with VAMS for 3D asset management. It enables robotics and simulation engineers to browse, download, upload, and manage assets directly from within Isaac Sim.

:::warning[Experimental]
This integration is in **experimental** status and may still have issues. Verify with your organization before deploying to any production environment.
:::

## Features

-   **Database and Asset Browsing** -- Navigate VAMS databases, assets, and files in a dockable UI panel
-   **Asset Download and Import** -- Download individual files or full assets; USD, URDF, and MJCF files can be imported directly into the Isaac Sim stage from the file list UI. USD files can be opened as new stages or added as prim references. URDF and MJCF files are converted to USD on import using Isaac Sim's built-in asset importers.
-   **General File Download** -- Other files can be downloaded to `~/vams_downloads`, keeping their path within the asset
-   **Scene Export and Upload** -- Export the current Isaac Sim USD stage and upload it to VAMS as a new or existing asset
-   **Workflow Execution** -- List available processing workflows, view execution history, and trigger new workflow runs on assets; **Run** is offered only for files the workflow and its first pipeline accept, with a template choice for a pipeline that requires one
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

Kit treats each child of an extension folder as an extension named after that child, so the connector directory goes into a folder of its own under the extension's name, `vams.connector.isaacsim`. Copy it there, and run the same commands again after updating the connector; they remove the earlier copy first, since `cp -r` would otherwise place the new one inside it:

```bash
mkdir -p /path/to/kit-exts
rm -rf /path/to/kit-exts/vams.connector.isaacsim
cp -r /path/to/tools/ExternalIntegrations/isaacsim_vams_integration /path/to/kit-exts/vams.connector.isaacsim
```

Kit loads the copy, so the `extension.toml` settings and the certificate bundle described below go into `/path/to/kit-exts/vams.connector.isaacsim`, and are made again after each update.

### Option A: Extension search path (recommended)

In Isaac Sim, go to **Window > Extensions**, click the gear icon, and add the folder that contains `vams.connector.isaacsim` as a search path:

```
/path/to/kit-exts
```

Then search for "VAMS" and enable `vams.connector.isaacsim`.

### Option B: Command line

```bash
isaac-sim --ext-folder /path/to/kit-exts --enable vams.connector.isaacsim
```

### Option C: App .kit file

```toml
[dependencies]
"vams.connector.isaacsim" = {}

[settings.app.exts]
folders = ["/path/to/kit-exts"]
```

## Ubuntu / EC2 Setup

When running Isaac Sim on Ubuntu (e.g., on an Amazon EC2 instance), the VAMS CLI should be installed in an isolated Python virtual environment to avoid conflicts with Isaac Sim's bundled Python.

### Virtual environment installation

```bash
python3 -m venv ~/vamscli-venv
~/vamscli-venv/bin/pip install -e /path/to/vams/tools/VamsCLI
~/vamscli-venv/bin/vamscli setup https://your-api-gateway-url.amazonaws.com
```

Then set the explicit CLI path in `/path/to/kit-exts/vams.connector.isaacsim/config/extension.toml`:

```toml
[settings]
exts."vams.connector.isaacsim".vamscli_path = "/home/ubuntu/vamscli-venv/bin/vamscli"
```

### SSL certificate bundle

Isaac Sim may override the `SSL_CERT_FILE` environment variable with its own certificate bundle that does not include Amazon's CA certificates, causing S3 download failures. The extension never passes that value to the CLI subprocess. It names the operating system's CA bundle instead, merged with an Amazon CA certificate bundle placed in the `certs/` directory when one is present. With neither, the CLI uses its own default trust store.

To set up the certificate bundle:

1.  Download the Amazon root CA certificates from [https://www.amazontrust.com/repository/](https://www.amazontrust.com/repository/)
2.  Concatenate the PEM files into a single file named `amazon-ca-bundle.pem`
3.  Place it at `/path/to/kit-exts/vams.connector.isaacsim/certs/amazon-ca-bundle.pem`, in the copy Kit loads

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
4.  **Files** -- List files in the selected asset; select a file to show the actions for its type:
    -   **USD files** (`.usd`, `.usda`, `.usdc`, `.usdz`): **Open Stage (replace entire scene)** downloads the file and opens it as a new stage. **Add USD Reference (add to current scene)** adds it as a reference under `/World/<name>`, the file name without its extension and with each character other than a letter, digit or underscore replaced by `_`, or under `<name>_1`, `<name>_2`, ... when that prim exists, so each add gets a prim of its own; the status line names the prim used. The reference brings in the file's default prim, or its one root prim when it has none, counting the root prims of the layers it sublayers; a file with neither is refused, the stage is left as it was, and the status line gives the reason (`Reference error: <file> has no default prim (<n> root prims), so a reference would bring in nothing. Open it with Open Stage, or set a default prim on the file.`)
    -   **URDF files** (`.urdf`): **Import URDF (add to current scene)** downloads the robot description and imports it into the current stage, under the prim the stage's default prim names, or at the stage root when it names none, and under `<name>_0` when `<name>` is taken there; the stage's default prim is left as it was, and the status line names the prim used (`Imported URDF as <prim>`) or reads `URDF import failed`
    -   **MJCF files** (`.mjcf`, `.xml`): **Import MJCF (add to current scene)** downloads the MuJoCo model and imports it into the current stage under `/World/<name>`, with `<name>` made from the file name as for a USD reference, or under `<name>_1`, `<name>_2`, ... when that prim exists; the stage's default prim is left as it was, and the status line names the prim used (`Imported MJCF <file> as <prim>`). A file that is not well-formed XML is refused before the importer runs (`MJCF error: <file> is not well-formed XML: <parser message>`), as is a file that declares a document type, which MJCF does not use (`MJCF error: <file> is refused: it declares a document type at line <n>, ...`), and an import that brings in nothing from the file is removed again, together with a `/World` it created, and the status line reads `MJCF import failed: <file>`
    -   **Any file**: **Download Only (save to disk)** saves it to `~/vams_downloads`, under its path within the asset
5.  **Workflows** -- Lists the enabled workflows of `GLOBAL` and the selected database, and the asset's execution history, where each execution's input file is shown by its path within the asset and a value too long for its column is shortened, with the workflow ID and the file's full key in tooltips. Choosing a workflow opens its Execute view: **Run on Entire Asset** where the workflow takes a whole asset, **Run** for each file the workflow and its first pipeline accept, and a template choice for a pipeline that requires one

A `.usd`, `.usda` or `.usdc` layer, a URDF file and a MJCF file name other files of the asset by relative path (sublayers, references, textures, meshes). Opening or importing one downloads the whole asset, laid out as it is in VAMS, into a new private temporary directory so those paths resolve; the status line reports how many of the asset's other files did not download, and a requested file that did not download with the reason the download gave, for example `Reference error: /robot.usd did not download: <error>`. A `.usdz` package carries its dependencies and is downloaded alone.

**Export & Upload** writes the current stage, flattened into a single layer, to the root of the selected asset. A texture, reference or sublayer path to a file downloaded with that same asset is rewritten relative to the asset root, so the export resolves in VAMS and in a whole-asset download. A path to any other local file, including a file of another asset or one written into a download directory afterwards (such as importer output), is left as it is and resolves only on the machine that exported the stage; the status line reports how many such paths the export contains, and `export_and_upload_scene` returns the count as `unportablePaths`.

The Execute view offers **Run** only for the files that pass the input-file filters of the workflow and of its first pipeline, as the template in use sets them, and **Run on Entire Asset** reports that no file passes, instead of running, when none of the asset's files does. A later pipeline's filters do not limit **Run**: that pipeline also receives the files the earlier pipelines write and VAMS checks it when the run reaches it, so a workflow whose second pipeline reads only the GLB its first pipeline converts an OBJ to offers **Run** for the OBJ. A pipeline that requires a template and names no default (no `defaultTemplateId` on the workflow's reference to it and no default template) gets a template choice; **Run** is refused until one is chosen, and the run passes it as that pipeline's `templateId`. Choosing one replaces the status line's `Choose a template for <pipeline> before running`, or an earlier choice's status, with `Template for <pipeline>: <template>`. Template tags are not collected: a run the service refuses for a required tag shows the refusal with a hint to run the workflow from the VAMS web UI. The view reads each pipeline once when it opens; a pipeline that cannot be read does not gate **Run**, and the status line says so.

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

# Download (a file that does not download raises VamsCliError naming the download's error)
connector.download_file("my-db", "my-asset", "/model.usd", "/tmp/downloads")
result = connector.download_asset("my-db", "my-asset", "/tmp/full_asset")

# Upload
connector.upload_file("my-db", "my-asset", "/local/model.usd")
connector.create_and_upload("my-db", "new-asset", "/local/model.usd")

# Workflows
workflows = connector.list_workflows()
connector.execute_workflow("my-db", "my-asset", "wf-id", "wf-db-id")
pipeline = connector.get_pipeline("GLOBAL", "my-pipeline")
templates = connector.list_pipeline_templates("GLOBAL", "my-pipeline")
connector.execute_workflow("my-db", "my-asset", "wf-id", "wf-db-id", file_key="/model.stl",
                           pipeline_parameters={"my-pipeline": {"templateId": "my-template"}})

# Isaac Sim stage operations
connector.export_and_upload_scene("my-db", "scene_v1")
connector.download_and_import_asset("my-db", "my-asset", "/scene.usd")
connector.download_and_add_reference("my-db", "my-asset", "/robot.usd", "/World/Robot")

# Reference a USD file already on disk
import omni.usd
connector.add_reference_to_stage(omni.usd.get_context().get_stage(), "/World/Robot", "/tmp/robot.usd")

# URDF import (requires isaacsim.asset.importer.urdf extension)
prim_path = connector.download_and_import_urdf("my-db", "my-asset", "/robot.urdf")

# MJCF import (requires isaacsim.asset.importer.mjcf extension)
prim_path = connector.download_and_import_mjcf("my-db", "my-asset", "/ant.xml")

# URDF or MJCF import of a file already on disk
prim_path = connector.import_urdf_file("/tmp/robot/robot.urdf")
prim_path = connector.import_mjcf_file("/tmp/ant/ant.xml")
```

:::note
The `download_and_import_asset` and `download_and_add_reference` methods validate that the file has a USD extension (`.usd`, `.usda`, `.usdc`, `.usdz`) before attempting to open or reference it. `download_and_add_reference` returns `False`, with the reason logged, for a file that did not download or has no prim a reference could bring in, and `add_reference_to_stage` raises `UnreferenceableLayerError`, a `VamsCliError` defined in `vams.connector.isaacsim.connector`, for such a file: the reference names the file's default prim, or its one root prim when it has none. A prim already at the given path keeps its references and gets this one as well. The URDF and MJCF import methods similarly validate their expected file extensions (`.urdf` for URDF; `.mjcf` or `.xml` for MJCF). They return the prim path of the import, or `None` when it fails, and leave the stage's default prim as it was: URDF goes under the prim the default prim names, or at the stage root when it names none, and MJCF under `/World`, where an import that brings in nothing from the file is removed again. `import_mjcf_file` raises `MalformedXmlError`, a `VamsCliError` defined in `vams.connector.isaacsim.connector`, for a file that is not well-formed XML, or that declares a document type (MJCF does not use one, and one can declare entities), before the importer runs, and `download_and_import_mjcf` returns `None`, with the reason logged, for such a file. The URDF and MJCF importers are optional -- if the corresponding Isaac Sim importer extension is not enabled, the import will fail with an import error.
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
