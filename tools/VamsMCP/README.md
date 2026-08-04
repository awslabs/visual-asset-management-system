# VAMS MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes
the Visual Asset Management System (VAMS) REST API as agent-callable tools. It
lets any MCP-capable host (Kiro, Claude Desktop, Bedrock agents, internal
orchestrators) search, inspect, and manage VAMS databases, assets, files,
metadata, versions, and workflows through natural language.

## No credentials stored

This server **does not store any keys, tokens, or URLs**. It reuses the
[`vamscli`](../VamsCLI) profile you already configured on your machine — the
same one you use for the CLI. Each user runs the server against **their own**
VAMS account using **their own** vamscli login, which makes it safe to
distribute publicly.

```
MCP host ──stdio──> vams-mcp (FastMCP) ──> vamscli APIClient + ProfileManager ──HTTPS──> your VAMS API
                                                        │
                                          reads URL + auth from your vamscli profile
```

The server reuses the CLI's `APIClient`, so it inherits retries, 429 backoff,
typed errors, and automatic token refresh.

## Requirements

-   Python 3.12+
-   [`vamscli`](../VamsCLI) installed and configured (see below)
-   `mcp` 1.2+ or 2.x — both SDK generations are supported

## Setup

**1. Install and configure vamscli** (if you haven't already):

```bash
pip install ./tools/VamsCLI
vamscli setup https://<your-api-id>.execute-api.<region>.amazonaws.com
vamscli auth login -u you@example.com
```

> Use the **API Gateway** URL, not the CloudFront web URL.

**2. Install the MCP server:**

```bash
# from tools/VamsMCP
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ../VamsCLI     # provides the reused APIClient + ProfileManager
pip install -e .
```

## Run

```bash
vams-mcp        # stdio transport; uses your active vamscli profile
```

## Configure (optional)

All configuration is via environment variables — none are required (see `.env.example`):

| Variable                  | Description                                                          |
| ------------------------- | -------------------------------------------------------------------- |
| `VAMS_PROFILE`            | vamscli profile to use (default: active profile)                     |
| `VAMS_ENABLE_WRITES`      | `true` to expose create/update tools (default off)                   |
| `VAMS_ENABLE_DESTRUCTIVE` | `true` to expose archive/delete tools (needs writes on; default off) |
| `VAMS_MAX_PAGES`          | Max pages auto-followed for list endpoints (default 20)              |
| `VAMS_PAGE_SIZE`          | Page size per paginated call (default 100)                           |

## Register with Kiro / Claude Desktop

Because auth comes from your vamscli profile, the MCP config contains **no
secrets** — just the command:

```json
{
    "mcpServers": {
        "vams": {
            "command": "/absolute/path/to/tools/VamsMCP/.venv/bin/vams-mcp",
            "env": {},
            "disabled": false,
            "autoApprove": [
                "list_allowed_api_routes",
                "list_databases",
                "get_database",
                "list_assets",
                "get_asset",
                "search_assets",
                "find_and_summarize"
            ]
        }
    }
}
```

To use a non-default profile, add `"env": { "VAMS_PROFILE": "myprofile" }`.

## Tools

### Read / search (always available)

`list_allowed_api_routes`, `list_databases`, `get_database`, `list_buckets`,
`list_assets`, `get_asset`, `list_asset_files`, `get_asset_metadata`,
`get_database_metadata`, `list_asset_versions`, `get_asset_version`,
`get_asset_history`, `get_asset_links`, `search_assets`, `search_files`,
`get_search_fields`, `list_workflows`, `list_workflow_executions`, `list_tags`,
`list_tag_types`, `list_metadata_schemas`, `generate_download_url`,
`find_and_summarize`.

Pipelines, workflows, and executions: `list_pipelines`, `get_pipeline`,
`list_pipeline_templates`, `get_pipeline_template`,
`get_pipeline_template_tag_schema`, `get_workflow`, `list_workflow_triggers`,
`get_workflow_trigger`, `list_executions`, `get_execution_details`,
`get_execution_logs`.

Call `list_allowed_api_routes` first — it reports what the authenticated user is
actually authorized to do, so an agent can scope its plan instead of discovering
a 403 mid-task. `search_assets` and `search_files` accept a `geo_search` filter
(point + radius, bounding box, or GeoJSON) against the `geo_MD_location` field.

### Write (require `VAMS_ENABLE_WRITES=true`)

`create_database`, `create_asset`, `update_asset`, `set_asset_metadata`,
`create_folder`, `create_asset_version`.

Pipelines, workflows, and executions: `create_pipeline`, `update_pipeline`,
`create_pipeline_template`, `update_pipeline_template`,
`set_pipeline_template_tag_schema`, `create_workflow`, `update_workflow`,
`set_workflow_trigger`, `execute_workflow`, `rerun_execution`,
`abort_execution`.

`execute_workflow`, `rerun_execution`, and the pipelines they launch start real
AWS compute and can incur cost. Keep them out of `autoApprove`.

### Destructive (require `VAMS_ENABLE_DESTRUCTIVE=true`)

`archive_asset`, `unarchive_asset`, `delete_asset`, `delete_database`.

Pipelines, workflows, and executions: `archive_pipeline`, `unarchive_pipeline`,
`archive_workflow`, `unarchive_workflow`, `delete_pipeline_template`,
`delete_workflow_trigger`, `permanent_delete_execution`.

Archiving a pipeline or workflow is a soft delete, reversible through the
matching `unarchive_*` tool. `delete_pipeline_template`,
`delete_workflow_trigger`, and `permanent_delete_execution` are not reversible.

## Security notes

-   Authorization is exactly your vamscli user's VAMS permissions (RBAC/ABAC).
-   Writes and destructive tools are **off by default**. Keep destructive tools
    out of `autoApprove`.
-   The server persists nothing; revoking access is just `vamscli auth logout`
    (or letting your session expire).

## License

Apache-2.0.
