# Getting Started with VamsCLI

VamsCLI is a Python command-line interface for interacting with the Visual Asset Management System (VAMS) deployed on AWS. It provides full access to the VAMS API for managing databases, assets, files, metadata, workflows, and user permissions from your terminal.

Use VamsCLI when you need to automate asset management workflows, integrate VAMS into CI/CD pipelines, perform bulk operations across large asset libraries, or script repetitive tasks that would be tedious through the web interface.

## System Requirements

| Requirement | Version |
|---|---|
| Python | 3.13 or later |
| pip | Latest recommended |
| Operating System | Windows, macOS, or Linux |
| Terminal | UTF-8 capable (Windows Terminal, VS Code terminal) |

:::note[Windows Terminal Encoding]
On Windows, use Windows Terminal or VS Code's integrated terminal. If you encounter encoding errors with Unicode characters, set the environment variable `PYTHONIOENCODING=utf-8` before running VamsCLI.
:::


## Quick Install

1. Clone or download the VAMS repository.
2. Navigate to the CLI directory and install in development mode:

```bash
cd tools/VamsCLI
pip install -e .
```

3. Verify the installation:

```bash
vamscli --version
```

You should see output similar to:

```
VamsCLI version <current-version>
```

## First-Time Setup

Before using VamsCLI, you must configure it to connect to your VAMS deployment. The `setup` command fetches the Amplify configuration from your VAMS instance and stores the API Gateway URL, Amazon Cognito User Pool details, and AWS Region automatically.

```bash
vamscli setup https://your-vams-url.example.com
```

The `setup` command accepts any HTTP or HTTPS URL that points to your VAMS deployment, including Amazon CloudFront distributions, Application Load Balancers (ALB), Amazon API Gateway endpoints, or custom domains.

:::tip[Multiple Environments]
Use the `--profile` flag to configure separate profiles for different environments:

```bash
vamscli --profile production setup https://prod-vams.example.com
vamscli --profile staging setup https://staging-vams.example.com
```
:::


## Authentication

After setup, authenticate with your VAMS instance. VamsCLI supports two authentication methods.

### Amazon Cognito Authentication

If your VAMS deployment uses Amazon Cognito for identity management:

```bash
vamscli auth login -u your.email@example.com
```

You will be prompted for your password. VamsCLI handles multi-factor authentication (MFA) challenges and forced password changes automatically through interactive prompts.

To save credentials for automatic re-authentication when tokens expire:

```bash
vamscli auth login -u your.email@example.com --save-credentials
```

### Token Override Authentication

If your VAMS deployment uses an external OAuth identity provider (IdP), use the token override mechanism:

```bash
vamscli auth set-override -u your.email@example.com --token "eyJhbGciOiJ..."
```

You can specify an expiration time for the override token using Unix timestamps, ISO 8601 format, or relative seconds:

```bash
vamscli auth set-override -u user@example.com --token "token..." --expires-at "+3600"
vamscli auth set-override -u user@example.com --token "token..." --expires-at "2025-12-31T23:59:59Z"
```

:::warning[Override Token Limitations]
Override tokens do not support automatic refresh. When an override token expires, you must set a new one manually.
:::


### API Key Authentication

If you have a VAMS API key (created through the web UI or API), you can use it directly without a separate login step. Pass the API key as a token override on any command:

```bash
vamscli --token-override "your-api-key-value" database list
```

Or set it as a persistent override for the session:

```bash
vamscli auth set-override -u api-key-user@example.com --token "your-api-key-value"
```

API keys impersonate the user ID they were created with and inherit that user's roles. See [API Key Management](../user-guide/api-keys.md) for details on creating and managing API keys.

## Verify Authentication

Check your current authentication status at any time:

```bash
vamscli auth status
```

This displays your user ID, token type, expiration time, and enabled feature switches.

## Global Options

Every VamsCLI command supports the following global options:

| Option | Description |
|---|---|
| `--profile <name>` | Use a named profile instead of the default profile |
| `--verbose` | Enable verbose output with detailed error information, API request/response logging, and timing |
| `--version` | Display the VamsCLI version |
| `--help` | Display help for any command or subcommand |

The `--profile` option must appear before the command name:

```bash
vamscli --profile production assets list -d my-database
```

## Quick Example

The following example demonstrates a typical workflow: listing databases, listing assets within a database, and uploading a file to an asset.

```bash
# List all databases
vamscli database list

# List assets in a specific database
vamscli assets list -d my-database

# Get details for a specific asset
vamscli assets get my-asset-id -d my-database

# Upload a file to an asset
vamscli file upload model.gltf -d my-database -a my-asset-id

# List files in an asset
vamscli file list -d my-database -a my-asset-id
```

## Next Steps

- [Installation and Profile Management](installation.md) -- Detailed installation, profile configuration, and environment variable overrides
- [Command Reference](command-reference.md) -- Overview and index of all VamsCLI command groups
- [Detailed Command Pages](commands/setup-and-auth.md) -- Full option tables, JSON examples, and workflow guides for each command group
- [Automation and Scripting](automation.md) -- Using VamsCLI in scripts, CI/CD pipelines, and bulk operations
