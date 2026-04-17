# External Tool Integrations

VAMS provides open-source connector plugins for integrating with third-party desktop applications. These connectors enable users to browse, download, upload, and manage VAMS assets directly from within their preferred tools -- without leaving the application.

All external integrations use the **VAMS CLI** (`vamscli`) as the communication layer between the host application and the VAMS API. This approach provides a consistent interface across all integrations, supports all VAMS authentication methods, and avoids embedding AWS SDK dependencies in each plugin.

:::warning[Experimental]
All external tool integrations are currently in **experimental** status and may still have issues. Verify with your organization before deploying to any production environment.
:::

## Available Integrations

| Integration                                    | Host Application      | Language      | Status       | Description                                                                 |
| :--------------------------------------------- | :-------------------- | :------------ | :----------- | :-------------------------------------------------------------------------- |
| [Isaac Sim Connector](isaacsim-connector.md)   | NVIDIA Isaac Sim 5.1+ | Python        | Experimental | Omniverse Kit extension for robotics and simulation asset management        |
| [ArcGIS Pro Connector](arcgispro-connector.md) | Esri ArcGIS Pro 3.5+  | C# (.NET 8.0) | Experimental | ArcGIS Pro add-in for GIS professionals to reference and manage VAMS assets |

## Architecture

All external tool integrations follow the same layered architecture:

```mermaid
graph LR
    A[Host Application Plugin] --> B[VAMS CLI Service Layer]
    B --> C[vamscli subprocess]
    C --> D[VAMS API Gateway]
    D --> E[VAMS Backend]
```

1. **Host Application Plugin** -- UI and application-specific logic (e.g., Omniverse Kit extension, ArcGIS Pro add-in)
2. **VAMS CLI Service Layer** -- Wraps `vamscli` commands via subprocess, parses `--json-output` responses into typed objects
3. **VAMS CLI** -- Installed separately, handles authentication, token management, and HTTP communication with the VAMS API

## Prerequisites

All external integrations require:

-   **VAMS CLI** installed and on PATH (`pip install vamscli`)
-   **VAMS CLI profile** configured (`vamscli setup <api-gateway-url>`)
-   **VAMS credentials** -- Cognito username/password, IDP JWT token, or VAMS API key (prefixed `vams_`)

For CLI installation and setup details, see [CLI Installation](../../cli/installation.md).

## Source Location

External integration source code is located at:

```
tools/ExternalIntegrations/
├── isaacsim_vams_integration/     # Isaac Sim connector
└── arcgispro-connector-for-vams/  # ArcGIS Pro connector
```

## Related Pages

-   [CLI Getting Started](../../cli/getting-started.md) -- VAMS CLI overview
-   [CLI Installation](../../cli/installation.md) -- Installing and configuring the VAMS CLI
-   [API Keys](../../user-guide/api-keys.md) -- Creating VAMS API keys for non-interactive authentication
