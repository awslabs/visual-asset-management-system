# ArcGIS Pro Connector (EXPERIMENTAL)

The VAMS Connector for ArcGIS Pro is a .NET add-in that integrates Esri ArcGIS Pro with VAMS for visual asset management. It enables GIS professionals to browse, explore, manage, and reference VAMS databases, assets, and files directly from within ArcGIS Pro.

:::warning[Experimental]
This integration is in **experimental** status and may still have issues. Verify with your organization before deploying to any production environment.
:::

## Features

-   **Hierarchical Database Browser** -- Navigate VAMS databases, assets, and files in a tree view with folder structure support
-   **File Reference System** -- Link VAMS files to GIS feature classes and table rows with automatic field creation and rich metadata storage
-   **Image Preview** -- Advanced image viewer with pan, zoom, and download capabilities
-   **Table Integration** -- Right-click context menu in attribute tables to open VAMS file links from selected rows
-   **File Downloads** -- Download individual files or entire assets with progress tracking and folder structure preservation
-   **Dual Authentication** -- Cognito username/password login and token override authentication (IDP JWT tokens and VAMS API keys)

## Prerequisites

-   **Esri ArcGIS Pro 3.5+**
-   **.NET 8.0** targeting Windows
-   **VAMS CLI** installed and configured

### VAMS CLI setup

Install the VAMS CLI:

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

1. Download the latest `.esriAddinX` file
2. Close ArcGIS Pro if it is running
3. Double-click the `.esriAddinX` file to install
4. Launch ArcGIS Pro and open any project
5. Find the VAMS tab in the ribbon or locate "Database Explorer" in the Add-In tab

## Authentication

The connector supports two authentication methods, automatically detected from the VAMS CLI profile configuration.

### Cognito authentication

For VAMS deployments using AWS Cognito as the identity provider. Enter your VAMS username (email) and password in the login dialog.

CLI equivalent:

```bash
vamscli auth login -u user@example.com -p yourpassword
```

### Token override authentication

For VAMS deployments using an external identity provider, or when authenticating with a VAMS API key. Enter your user ID and the token or API key in the login dialog.

-   **VAMS API keys** are prefixed with `vams_` (e.g., `vams_abc123...`)
-   **IDP JWT tokens** are standard JWT strings from your external identity provider

CLI equivalent:

```bash
vamscli auth login --user-id user@example.com --token-override "vams_your-api-key"
vamscli auth login --user-id user@example.com --token-override "eyJhbGciOiJSUzI1NiIs..."
```

## Usage

### Browsing VAMS content

1. Open the **VAMS Database Explorer** from the Add-In tab
2. Click the login button and authenticate
3. Navigate through databases, assets, and files in the hierarchical tree view

### Adding file references to GIS data

1. Select a target layer or table in the Contents pane
2. Navigate to the desired file in the VAMS Database Explorer
3. Right-click on the file and choose "Add Reference to Feature Class/Table..."
4. If VAMS reference fields do not exist, the system prompts to create them automatically
5. Select features or rows to associate with the file and confirm

### Accessing referenced files

1. Open the attribute table for a layer with VAMS references
2. Select rows containing VAMS file references (populated `Vams_FileLink` fields)
3. Right-click and choose "Open VAMS Links" from the context menu

### Downloading files

-   **From Database Explorer (single file)** -- Right-click a file and select "Download File"
-   **From Database Explorer (all asset files)** -- Right-click an asset and select "Download All Files"
-   **From Image Preview** -- Click the Download button in the preview window footer

## VAMS reference field schema

The connector automatically creates the following fields when adding VAMS references to a feature class or table:

| Field Name           | Type   | Length | Purpose                           |
| :------------------- | :----- | :----- | :-------------------------------- |
| `Vams_FileName`      | String | 255    | VAMS file display name            |
| `Vams_DatabaseId`    | String | 255    | VAMS database identifier          |
| `Vams_DatabaseName`  | String | 255    | VAMS database display name        |
| `Vams_AssetId`       | String | 255    | VAMS asset identifier             |
| `Vams_AssetName`     | String | 255    | VAMS asset display name           |
| `Vams_FileLink`      | String | 1000   | Clickable hyperlink URL           |
| `Vams_FileExtension` | String | 10     | File extension (e.g., .jpg, .pdf) |
| `Vams_AddedDate`     | Date   | --     | Timestamp of reference creation   |

## Architecture

```
arcgispro-connector-for-vams/
├── Commands/                    # UI commands (add reference, open link)
├── Handlers/                    # URL and event handlers
├── Helpers/                     # Tree view item models
├── Models/                      # Data transfer objects for CLI JSON output
├── Services/
│   ├── VamsCliService.cs        # VAMS CLI subprocess wrapper
│   ├── AwsCliService.cs         # AWS S3 operations
│   └── VamsReferenceService.cs  # GIS field creation and data population
├── Config.daml                  # ArcGIS Pro extension configuration
└── *.xaml                       # UI definitions (WPF)
```

| Component         | File                      | Purpose                                                            |
| :---------------- | :------------------------ | :----------------------------------------------------------------- |
| CLI Service       | `VamsCliService.cs`       | Wraps `vamscli` commands via `Process`, parses JSON output         |
| Reference Service | `VamsReferenceService.cs` | Manages VAMS field creation and population in GIS layers           |
| Tree View Items   | `Helpers/`                | Hierarchical data models for databases, assets, folders, and files |
| Data Models       | `VamsModels.cs`           | C# DTOs matching VAMS CLI JSON output structures                   |

## Technical Details

| Component                 | Details                                       |
| :------------------------ | :-------------------------------------------- |
| **Framework**             | .NET 8.0 targeting Windows (`net8.0-windows`) |
| **UI Framework**          | WPF with ArcGIS Pro SDK integration           |
| **Architecture**          | MVVM pattern with command binding             |
| **External Dependencies** | YamlDotNet for CLI output parsing             |

### File Deep Link URL Format

VAMS file reference URLs point directly to the file viewer in the VAMS web application using the HashRouter format:

```
https://<VAMS_WEBSITE>/#/databases/<databaseId>/assets/<assetId>/file/<encodedFilePath>
```

The file path is URL-encoded using `encodeURIComponent()`. Optional query parameters for versioning:

-   `?version=<fileVersionId>` — view a specific file version
-   `?assetVersion=<assetVersionId>` — view the file at a specific asset version (takes priority over `version`)

Example:

```
https://vams.example.com/#/databases/building/assets/x7150f39c-abc123/file/images%2Fphoto.jpg
```

## Development

### Building from Source

1. **Clone the repository**

    ```bash
    git clone <repository-url>
    cd tools/ExternalIntegrations/arcgispro-connector-for-vams
    ```

2. **Restore dependencies**

    ```bash
    dotnet restore
    ```

3. **Build the solution**

    ```bash
    dotnet build
    ```

4. **Debug in ArcGIS Pro**
    - Open `VamsConnector.sln` in Visual Studio 2022
    - Set ArcGIS Pro as the startup application
    - Press F5 to launch with debugger attached

### Development Environment

-   **Visual Studio 2022** (recommended)
-   **ArcGIS Pro SDK for .NET** (3.5+)
-   **Git** for version control

### Code Style

-   **MVVM Pattern**: ViewModels inherit from `DockPane` and use `SetProperty`
-   **Command Pattern**: `RelayCommand` for UI interactions
-   **Async/Await**: Proper async patterns with `QueuedTask.Run()`
-   **Error Handling**: Comprehensive try-catch with user feedback

### Debug Information

-   **Console Output**: Check ArcGIS Pro console for debug messages
-   **Field Verification**: Verify field creation in layer's attribute table
-   **URL Validation**: Ensure `Vams_FileLink` field contains valid URLs
-   **Selection State**: Confirm table rows are selected before using context menu

## Source Location

```
tools/ExternalIntegrations/arcgispro-connector-for-vams/
```

## Trademarks

ArcGIS, ArcGIS Pro, and the ArcGIS logo are trademarks, registered trademarks, or service marks of Esri in the United States, the European Community, or certain other jurisdictions. This project is not affiliated with, endorsed by, or sponsored by Esri.

## Related Pages

-   [External Tool Integrations Overview](overview.md) -- All available integrations
-   [CLI Installation](../../cli/installation.md) -- Installing the VAMS CLI
-   [API Keys](../../user-guide/api-keys.md) -- Creating VAMS API keys for non-interactive authentication
