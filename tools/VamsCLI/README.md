# VamsCLI - Visual Asset Management System Command Line Interface

VamsCLI is a command-line interface for the Visual Asset Management System (VAMS), providing programmatic access to your VAMS deployment running on AWS. It supports authentication, multi-profile configuration, and comprehensive asset/file operations through an intuitive CLI.

**[📖 Read the full CLI documentation](https://awslabs.github.io/visual-asset-management-system/cli/getting-started)**

This README covers basic installation and a quick start. The complete CLI reference — every command and option, authentication and profile flows, automation and CI/CD patterns, and troubleshooting — lives in the official documentation site (see [Documentation](#documentation) below).

## Installation

```bash
cd tools/VamsCLI
pip install .
```

> **Note:** VamsCLI writes UTF-8 output regardless of the system code page, so no encoding configuration is required. On Windows, Windows Terminal renders its status indicators as intended.

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

# Download an asset (all files)
vamscli assets download /local/path -d my-database -a my-asset

# Download an entire asset recursively
vamscli assets download /local/path -d my-database -a my-asset --recursive

# Archive an asset (soft delete)
vamscli assets archive my-asset -d my-database --reason "No longer needed"

# Unarchive an asset (restore from soft delete)
vamscli assets unarchive my-asset -d my-database

# Create a version
vamscli asset-version create -d my-database -a my-asset --comment "Release v1"

# Execute a workflow
vamscli workflow execute --workflow-database-id my-database -w my-workflow --input-file my-database:my-asset:/model.glb

# Use API key authentication (the key is read from stdin, so it never appears in the process table)
echo "$VAMS_API_KEY" | vamscli auth set-override -u service-user --token-stdin
vamscli database list

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
| `assets`           | Asset management, archive/unarchive, export  |
| `asset-version`    | Version management (create, archive, revert) |
| `asset-links`      | Asset relationship management                |
| `file`             | File upload, download, copy, move, delete    |
| `sync`             | Directory push/pull synchronization          |
| `metadata`         | Metadata for assets, files, databases, links |
| `metadata-schema`  | Schema management                            |
| `comment`          | Asset version comments                       |
| `subscription`     | Asset event subscriptions                    |
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

Global options belong to the root `vamscli` group and go **before** the command (`vamscli --profile staging assets list`). Placed after the command they are rejected with "No such option".

| Option           | Description                                       |
| ---------------- | ------------------------------------------------- |
| `--version`      | Show version                                      |
| `--verbose`      | Detailed output with API request/response logging |
| `--profile NAME` | Use a specific profile                            |

API keys and external tokens are supplied through `vamscli auth set-override -u <user> --token-stdin` or `vamscli auth login --user-id <user> --token-override-stdin`, not through a global option.

## Documentation

The official VAMS documentation site is the single source of truth for the CLI. It contains the full command reference, authentication and profile flows, automation and CI/CD patterns, and troubleshooting.

| Section                                                                                                    | Description                                          |
| ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| [Getting Started](https://awslabs.github.io/visual-asset-management-system/cli/getting-started)            | Installation, first-time setup, and authentication   |
| [Installation and Profiles](https://awslabs.github.io/visual-asset-management-system/cli/installation)     | Install methods, profiles, and configuration storage |
| [Command Reference](https://awslabs.github.io/visual-asset-management-system/cli/command-reference)        | Index of all command groups with per-command pages   |
| [Automation and Scripting](https://awslabs.github.io/visual-asset-management-system/cli/automation)        | JSON output, pagination, scripting, and CI/CD        |
| [Development](https://awslabs.github.io/visual-asset-management-system/cli/development)                    | Contributing, code quality, testing, and releasing   |
| [Troubleshooting](https://awslabs.github.io/visual-asset-management-system/cli/troubleshooting/setup-auth) | Problem-solving by domain                            |

A fork of VAMS adds its own commands in `vamscli/commands/custom/`, `vamscli/custom_constants.py` and `vamscli/utils/custom_api_client.py`, which upstream ships empty and does not modify; see [Extending VAMS in a fork](https://awslabs.github.io/visual-asset-management-system/developer/customizing-vams).

To view the documentation site locally:

```bash
cd documentation/docusaurus-site
npm install
npm run start
```

Then open `http://localhost:3000/visual-asset-management-system/cli/getting-started` in your browser. The Markdown sources live under [`documentation/docusaurus-site/docs/cli/`](../../documentation/docusaurus-site/docs/cli/).

## License

Apache License 2.0 — see [LICENSE](../../LICENSE) for details.
