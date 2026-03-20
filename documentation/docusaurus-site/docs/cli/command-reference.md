# Command Reference

This page provides an overview of all VamsCLI command groups with links to detailed documentation for each. All commands support the global options `--profile`, `--verbose`, and `--help`. Most commands that produce output also support `--json-output` for machine-readable JSON.

:::info[Common Options]
The following options appear on nearly every command and are not repeated in individual pages:

-   `--json-output` -- Output raw JSON response instead of formatted CLI display
-   `--json-input` -- Provide parameters as a JSON string or file path (where supported)
-   `--auto-paginate` -- Automatically fetch all pages of results
-   `--page-size` -- Number of items per API page
-   `--max-items` -- Maximum total items to fetch (used with `--auto-paginate`, default: 10,000)
-   `--starting-token` -- Pagination token for manual page navigation
    :::

---

## Command Groups

| Command Group                                                                | Description                                                                                    | Detailed Reference                                     |
| ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `setup`, `auth`, `features`, `profile`                                       | Initial configuration, authentication, feature switches, and profile management                | [Setup and Authentication](commands/setup-and-auth.md) |
| `database`                                                                   | Create, update, delete, and list databases and bucket configurations                           | [Database](commands/database.md)                       |
| `assets`, `asset-version`, `asset-links`, `assets export`, `assets download` | Asset CRUD, version management, relationship links, export, and download                       | [Assets](commands/assets.md)                           |
| `file`                                                                       | Upload, list, info, create-folder, move, copy, archive, unarchive, delete, revert, set-primary | [Files](commands/files.md)                             |
| `tag`, `tag-type`                                                            | Tag and tag type management for asset categorization                                           | [Tags](commands/tags.md)                               |
| `metadata`, `metadata-schema`                                                | Metadata CRUD for assets, files, links, and databases; schema inspection                       | [Metadata](commands/metadata.md)                       |
| `search`                                                                     | Search assets and files using Amazon OpenSearch Service                                        | [Search](commands/search.md)                           |
| `workflow`                                                                   | List, execute, and monitor processing workflows                                                | [Workflows](commands/workflows.md)                     |
| `role`, `role constraint`, `role user`                                       | Role management, permission constraints, user-role assignments, and template import            | [Permissions](commands/permissions.md)                 |
| `user cognito`, `api-key`                                                    | Amazon Cognito user management and API key management                                          | [Users and API Keys](commands/users-and-keys.md)       |
| `industry engineering bom`, `industry engineering plm`, `industry spatial`   | BOM assembly, PLM XML import, and spatial GLB combination                                      | [Industry](commands/industry.md)                       |

---

## Quick Reference

### Setup and Authentication

```bash
vamscli setup https://your-vams-url.example.com
vamscli auth login -u admin@example.com
vamscli auth status
vamscli profile list
```

### Database and Asset Management

```bash
vamscli database list
vamscli database create -d my-database --description "Production assets"
vamscli assets list -d my-database
vamscli assets create -d my-database --name "Bridge Model" --description "3D scan" --distributable
```

### File Operations

```bash
vamscli file upload model.gltf -d my-db -a my-asset
vamscli file list -d my-db -a my-asset --basic --auto-paginate
vamscli file info -d my-db -a my-asset -p "/model.gltf" --include-versions
```

### Search and Metadata

```bash
vamscli search simple -q "training" --entity-types asset
vamscli metadata asset list -d my-db -a my-asset
vamscli metadata-schema list -d my-database -e assetMetadata
```

### Workflows and Permissions

```bash
vamscli workflow list -d my-database
vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global
vamscli role list
vamscli role constraint template import -j ./database-admin.json
```

### Users and API Keys

```bash
vamscli user cognito list
vamscli api-key create --name "CI Pipeline" --user-id bot@example.com --description "Build pipeline"
```

### Industry Commands

```bash
vamscli industry engineering bom bomassemble --json-file bom.json -d my-db
vamscli industry engineering plm plmxml import -d my-db --plmxml-dir /data/plm/export
vamscli industry spatial glbassetcombine -d my-db -a root-asset-id
```

---

## Additional Resources

-   [Getting Started](getting-started.md) -- First-time setup and authentication
-   [Installation and Profile Management](installation.md) -- Detailed installation and profiles
-   [Automation and Scripting](automation.md) -- JSON output, scripting patterns, and CI/CD integration
