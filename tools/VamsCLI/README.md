# VamsCLI - Visual Asset Management System Command Line Interface

VamsCLI is a command-line interface for the Visual Asset Management System (VAMS), providing programmatic access to your VAMS deployment running on AWS. It supports authentication, multi-profile configuration, and comprehensive asset/file operations through an intuitive CLI.

## Installation

```bash
cd tools/VamsCLI
pip install .
```

> **Note:** On Windows, set `PYTHONIOENCODING=utf-8` or use Windows Terminal for proper Unicode support.

## Quick Start

```bash
# 1. Configure with your VAMS URL
vamscli setup https://your-vams-url.example.com

# 2. Authenticate
vamscli auth login -u your.email@example.com

# 3. Verify
vamscli auth status
```

## Common Commands

```bash
# List databases
vamscli database list

# List assets in a database
vamscli assets list -d my-database

# Upload a file
vamscli file upload -d my-database -a my-asset /path/to/file.gltf

# Search assets
vamscli search assets -q "training model"

# Download an asset
vamscli assets download /local/path -d my-database -a my-asset

# Create a version
vamscli asset-version create -d my-database -a my-asset --comment "Release v1"

# Execute a workflow
vamscli workflow execute -d my-database -a my-asset -w my-workflow

# Use API key authentication
vamscli --token-override "your-api-key" database list

# JSON output for scripting
vamscli assets list --json-output

# Get help
vamscli --help
vamscli assets --help
```

## Available Command Groups

| Command            | Description                                  |
| ------------------ | -------------------------------------------- |
| `setup`            | Configure CLI with VAMS URL                  |
| `auth`             | Authentication and session management        |
| `database`         | Database CRUD operations                     |
| `assets`           | Asset management and export                  |
| `asset-version`    | Version management (create, archive, revert) |
| `asset-links`      | Asset relationship management                |
| `file`             | File upload, download, copy, move, delete    |
| `metadata`         | Metadata for assets, files, databases, links |
| `metadata-schema`  | Schema management                            |
| `search`           | OpenSearch-powered search                    |
| `workflow`         | Workflow execution and monitoring            |
| `tag` / `tag-type` | Tag and tag type management                  |
| `role`             | Roles, constraints, user-role assignments    |
| `api-key`          | API key management                           |
| `user`             | Cognito user management                      |
| `features`         | Feature flag inspection                      |
| `profile`          | Multi-environment profile management         |
| `industry`         | Industry commands (BOM, PLM, spatial GLB)    |

## Global Options

| Option                   | Description                                       |
| ------------------------ | ------------------------------------------------- |
| `--version`              | Show version                                      |
| `--verbose`              | Detailed output with API request/response logging |
| `--profile NAME`         | Use a specific profile                            |
| `--token-override TOKEN` | Authenticate with API key or external token       |

## Documentation

For comprehensive CLI documentation including detailed command reference, automation patterns, and CI/CD integration:

**[VAMS CLI Reference Documentation](../../documentation/docusaurus-site/docs/cli/getting-started.md)**

To view the full documentation site locally:

```bash
cd documentation/docusaurus-site
npm install
npm run start
```

## License

Apache License 2.0 — see [LICENSE](../../LICENSE) for details.
