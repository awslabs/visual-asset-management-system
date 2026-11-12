# MCP Server

The VAMS MCP server exposes the VAMS API to an AI agent as a set of [Model Context Protocol](https://modelcontextprotocol.io/) tools. With it, an agent in Kiro, Claude Code, Claude Desktop or another MCP client can search for assets, read their files, metadata and versions, generate download links, and look into workflow executions. You ask in natural language and the agent gathers the answer. This page covers installing, registering, configuring, checking and upgrading the server.

## When to use the MCP server

| Use                                   | When                                                                                                                                                  |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| VAMS MCP server                       | An agent answers questions about a deployment, or carries out tasks in it, on your behalf.                                                            |
| [VAMS CLI](../cli/getting-started.md) | You or a script run commands directly, or you need what the server does not expose: file uploads, sync, and user, role and constraint administration. |

The server runs on your machine as a local process that the MCP client starts and talks to over standard input and output (stdio). It stores no credentials and no URLs of its own. It reuses the VAMS CLI profile on the same machine: the deployment URL comes from `vamscli setup`, and the identity from `vamscli auth login`. Every tool call therefore acts as the signed-in user, and the user's VAMS permissions bound every answer.

When the server is connected, agents use its tools rather than running `vamscli` in a shell or reading the VAMS source code. The server tells the client so when it connects.

## Prerequisites

| Requirement                        | Version | Notes                                                                                                                                                                                             |
| ---------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python                             | 3.12+   | The server and the CLI install into the same virtual environment                                                                                                                                  |
| `mcp` (Model Context Protocol SDK) | 1.10.0+ | Installed automatically. 1.10.0 is the oldest release with tool titles and structured output. Releases 1.10.0 to 1.21.1 also need `pydantic` below 2.12 (see [Troubleshooting](#troubleshooting)) |
| `pydantic`                         | 2.11+   | Installed automatically, with `mcp`                                                                                                                                                               |
| A VAMS deployment                  | Any     | Reachable from your machine, and a VAMS user account (or a VAMS API key) on it                                                                                                                    |

:::warning[Use a virtual environment of its own]
The MCP SDK requires Pydantic v2. Install the server into its own virtual environment, never into an environment that also holds the VAMS backend or another Pydantic v1 project.
:::

## Install

VAMS ships the MCP server and the CLI as source in the repository, under `tools/VamsMCP` and `tools/VamsCLI`. Install the CLI first: the server depends on the `vamscli` package, which is not published to PyPI.

```bash
git clone https://github.com/awslabs/visual-asset-management-system.git
cd visual-asset-management-system/tools/VamsMCP

python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ../VamsCLI        # the VAMS CLI, which the server reuses
pip install -e .                 # the MCP server (vams-mcp)
```

The virtual environment now holds two commands, `vamscli` and `vams-mcp`. Note the absolute path of `vams-mcp` (for example `/home/you/visual-asset-management-system/tools/VamsMCP/.venv/bin/vams-mcp`); the client configuration below needs it.

### Point the CLI at your deployment and sign in

```bash
vamscli setup https://<your-vams-web-or-api-url>
vamscli auth login -u you@example.com
```

`vamscli setup` accepts the web application URL or the API Gateway URL; see [Setup and Authentication](../cli/commands/setup-and-auth.md#setup).

To sign in with a VAMS API key instead of a password, pass the key on standard input (see [API Keys](../user-guide/api-keys.md#with-the-vams-cli)):

```bash
echo "$VAMS_API_KEY" | vamscli auth login --user-id you@example.com --token-override-stdin
```

The same form signs in with a token from an external identity provider. The server picks up whichever sign-in the profile holds.

### Use a non-default profile

To keep the server on a separate deployment or identity, create a named profile and set `VAMS_PROFILE` in the client configuration:

```bash
vamscli --profile staging setup https://<staging-url>
vamscli --profile staging auth login -u you@example.com
```

```json
"env": { "VAMS_PROFILE": "staging" }
```

## Register the server with a client

The client starts `vams-mcp` itself. The configuration holds only the command and optional environment variables, never a credential.

### Kiro (IDE and kiro-cli)

Kiro reads MCP servers from `~/.kiro/settings/mcp.json` (all workspaces) or `.kiro/settings/mcp.json` (one workspace). The Kiro IDE and `kiro-cli` read the same files.

```json
{
    "mcpServers": {
        "vams": {
            "command": "/absolute/path/to/tools/VamsMCP/.venv/bin/vams-mcp",
            "env": {},
            "disabled": false,
            "autoApprove": ["vams_status", "list_databases", "get_asset", "search_assets"]
        }
    }
}
```

The `autoApprove` list above is shortened. The full recommended list is in [Auto-approval](#auto-approval).

### Claude Code

Add the server for your user:

```bash
claude mcp add vams --scope user -- /absolute/path/to/tools/VamsMCP/.venv/bin/vams-mcp
```

Add `-e VAMS_PROFILE=staging` (or another variable) before `--` to pass configuration. To share the server with a project instead, commit a `.mcp.json` at the project root:

```json
{
    "mcpServers": {
        "vams": {
            "command": "/absolute/path/to/tools/VamsMCP/.venv/bin/vams-mcp",
            "env": {}
        }
    }
}
```

Claude Code names the tools `mcp__vams__<tool>`. To approve reads without a prompt, list them under `permissions.allow` in `.claude/settings.json`, for example `"mcp__vams__search_assets"`.

### Claude Desktop

Edit `claude_desktop_config.json` (**Settings** > **Developer** > **Edit Config**), then restart Claude Desktop:

```json
{
    "mcpServers": {
        "vams": {
            "command": "/absolute/path/to/tools/VamsMCP/.venv/bin/vams-mcp",
            "env": {}
        }
    }
}
```

### Auto-approval

Clients ask before each tool call unless the tool is on an approval list. The recommended list is every read tool that returns no credential, no presigned URL and no other user's identifiers:

`vams_status`, `list_allowed_api_routes`, `list_api_routes`, `list_constraint_permission_objects`, `list_databases`, `get_database`, `list_buckets`, `list_assets`, `get_asset`, `list_asset_files`, `get_asset_metadata`, `get_database_metadata`, `list_asset_versions`, `get_asset_version`, `get_asset_history`, `get_asset_links`, `search_assets`, `search_files`, `get_search_fields`, `list_workflows`, `list_workflow_executions`, `list_tags`, `list_tag_types`, `list_metadata_schemas`, `find_and_summarize`, `list_pipelines`, `get_pipeline`, `list_pipeline_templates`, `get_pipeline_template`, `get_pipeline_template_tag_schema`, `get_workflow`, `list_workflow_triggers`, `get_workflow_trigger`, `list_executions`, `get_execution_details`, `page_execution_detail_metadata`, `page_execution_detail_files`, `get_execution_results`, `get_execution_logs`, `list_asset_comments`, `list_asset_version_comments`, `get_comment`, `check_subscription`.

Keep these tools off the list:

-   `generate_download_url` and `generate_download_urls_bulk` return presigned Amazon S3 URLs. Each URL is a bearer credential: anyone holding it can download the file until it expires (24 hours by default), and it is stored wherever the client keeps its conversation log.
-   `get_api_key`, `get_user_api_key`, `list_api_keys`, `list_user_api_keys` and `list_subscriptions` return API key inventory or other users' identifiers.
-   Every write and destructive tool, and above all the tools that start, stop or extend billed compute (`execute_workflow`, `rerun_execution`, `abort_execution`). `create_api_key` and `create_user_api_key` return a usable API key.

:::warning[Approval by hint]
Every tool carries MCP annotations (see [Tool tiers and annotations](#tool-tiers-and-annotations)). The presigned-URL tools change nothing, so they are marked read-only. A client set to approve every read-only tool automatically would approve them too. Use an explicit approval list instead.
:::

## Configure

The server is configured only through environment variables in the client's `env` block. It reads no `.env` file. None is required.

| Variable                  | Default                | Description                                                                                        |
| ------------------------- | ---------------------- | -------------------------------------------------------------------------------------------------- |
| `VAMS_PROFILE`            | The active CLI profile | The `vamscli` profile whose deployment and sign-in the server uses                                 |
| `VAMS_ENABLE_WRITES`      | `false`                | `true` registers the create and update tools, and the tools that issue a credential                |
| `VAMS_ENABLE_DESTRUCTIVE` | `false`                | `true` registers the archive and delete tools. Has no effect unless `VAMS_ENABLE_WRITES` is `true` |
| `VAMS_MAX_PAGES`          | `20`                   | The most pages one list call follows                                                               |
| `VAMS_PAGE_SIZE`          | `100`                  | Rows requested per page; values above 1000 are reduced to 1000                                     |

### Tool tiers and annotations

| Tier        | Registered when                                              | MCP annotation                                  |
| ----------- | ------------------------------------------------------------ | ----------------------------------------------- |
| Read        | Always                                                       | `readOnlyHint: true`                            |
| Write       | `VAMS_ENABLE_WRITES=true`                                    | `readOnlyHint: false`, `destructiveHint: false` |
| Destructive | `VAMS_ENABLE_WRITES=true` and `VAMS_ENABLE_DESTRUCTIVE=true` | `destructiveHint: true`                         |

Tools that start, stop or extend billed compute also carry `openWorldHint: true`. A tool of a tier that is off is not registered at all, so the agent never sees it. Every tool also has a title, and the most-used reads (databases, assets, files, versions, metadata, search, executions, download URLs) publish an output schema and return structured content alongside the same JSON text.

The annotations are advice to the client. What a call may do is decided by the tiers above and by the user's VAMS permissions.

### Add-on tools

The tools of an optional add-on are registered only when the deployment enables that add-on. The server reads the deployment's feature switches from the profile's last sign-in, once, when it starts. After an administrator enables an add-on, sign in again with `vamscli auth login` and restart the server in the client to see its tools. See [Add-ons](../developer/addons-overview.md).

## Check that it works

Ask the agent to call `vams_status`, or call it from the client's tool panel. It answers without calling the VAMS API, even when nobody is signed in.

| Field                      | Meaning                                                                                                                              |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `authenticated`            | `true` when the tools can call VAMS. `false` when the profile is not set up, nobody is signed in, or VAMS refused the stored sign-in |
| `profile`                  | The CLI profile in use                                                                                                               |
| `apiUrl`                   | The deployment's API URL stored in that profile                                                                                      |
| `userId`                   | The VAMS user the tools act as                                                                                                       |
| `tokenExpired`             | `true` when the stored access token has expired. The next call renews it, or answers `NotAuthenticated` if VAMS refuses the renewal  |
| `featureSwitches`          | The deployment's feature switches as of the last sign-in                                                                             |
| `gates`                    | `reads`, `writes` and `destructive` (which tiers are registered) and `addons` (the add-ons whose tools are registered)               |
| `mcpVersion`, `cliVersion` | The installed server and CLI versions                                                                                                |
| `reason`, `user_action`    | Present when `authenticated` is `false`: why, and the command you run to sign in                                                     |
| `note`                     | Present when the stored token has expired but has not been refused yet                                                               |

### Signed-out behavior

The server starts even when the profile is not set up or nobody is signed in. Every tool except `vams_status` then returns an error of type `NotAuthenticated`. Its `error` asks the agent to have you sign in, and its `user_action` names the command for you to run, such as `vamscli auth login -u <user>`. The agent does not sign in itself. The same error is returned when a sign-in expires during a session and cannot be renewed. The next tool call after you sign in works without restarting the server.

## Upgrade from 2.6.x

### What changes

-   **Usage instructions.** The server sends the client a short guide when it connects: how VAMS identifies assets and files, where to start, and what a bounded result means.
-   **Tool descriptions** are rewritten for agents and are shorter. Tool names, parameters and returned JSON are unchanged.
-   **Titles and annotations** on every tool, and **structured output** for the most-used reads.
-   **Signed-out start.** In 2.6.x the server exited at start when nobody was signed in, so the client showed no tools. It now starts, and `vams_status` reports the state.
-   **New tool** `vams_status`.
-   **Minimum versions.** The server requires `mcp` 1.10.0 or later and `pydantic` 2.11 or later.
-   **A longer auto-approval list.** See [Auto-approval](#auto-approval).

### Upgrade steps

From the repository checkout, with the server's virtual environment active:

```bash
git pull
cd tools/VamsMCP
pip install -e ../VamsCLI
pip install --upgrade -e .
```

The last two commands also install the new minimum versions of `mcp` and `pydantic`. Then:

1. Update the client's approval list if you use one (see [Auto-approval](#auto-approval)).
2. Restart the server in the client: reload or restart its entry in Kiro, run `/mcp` in Claude Code, or restart Claude Desktop. A client keeps the tool list it read when it connected.
3. A client that caches a server's tool list until you refresh it, such as a custom connector in Amazon Quick, needs that list refreshed (**Sync**) to pick up the new titles, annotations and descriptions.
4. Call `vams_status` and check `mcpVersion`, `cliVersion` and `authenticated`.

## Troubleshooting

| Symptom                                                                               | Cause and fix                                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Every tool returns `NotAuthenticated`                                                 | Nobody is signed in on the profile, the profile is not set up, or the sign-in has expired. Run the command in `user_action` (for example `vamscli auth login -u you@example.com`), then call the tool again. Check `VAMS_PROFILE` if you use a named profile.                               |
| A tool returns `error_type` `RequestBlockedError` with `blockedBy`                    | The deployment's AWS WAF web ACL blocked the request before VAMS handled it. Signing in again or retrying does not change it. Give the `requestId` to your VAMS administrator, who finds the matching rule in the web ACL's sampled requests or logs.                                       |
| A tool returns `error_type` `FeatureDisabledError`                                    | The deployment has not enabled the feature the tool needs. An administrator enables it; then sign in again and restart the server.                                                                                                                                                          |
| A tool returns a `403` error                                                          | Your VAMS user is not permitted to do this. Call `list_allowed_api_routes` to see what the user may call, or ask an administrator for the permission.                                                                                                                                       |
| The server fails at start with `ImportError: cannot import name 'eval_type_backport'` | An `mcp` release from 1.10.0 to 1.21.1 is installed with `pydantic` 2.12 or later. Upgrade `mcp` (`pip install --upgrade "mcp>=1.21.2"`), or keep that `mcp` release and install `pydantic` below 2.12 (`pip install "pydantic>=2.11,<2.12"`).                                              |
| `pip install -e .` fails resolving `vamscli`                                          | Install the CLI first (`pip install -e ../VamsCLI`); it is not on PyPI.                                                                                                                                                                                                                     |
| The client lists no VAMS tools, or still shows old descriptions                       | The client did not start the server, or kept the tool list from an earlier connection. Check that `command` is the absolute path of `vams-mcp` in the virtual environment, then restart the server in the client. The server logs to standard error, which the client shows in its MCP log. |

## Remote hosting

The server currently runs only locally, as a process the client starts over stdio, with the identity of the CLI profile on that machine. A remotely hosted mode, with each user signing in through OAuth, is planned ([#1328](https://github.com/awslabs/visual-asset-management-system/issues/1328)). Clients that support only remote MCP servers cannot connect to the VAMS MCP server yet.

## Related topics

-   [Automating VAMS](../user-guide/automating-vams.md) compares the MCP server, the CLI and the REST API.
-   [Agentic Development](../developer/agentic-development.md#vams-mcp-server) covers how the server is built and how to maintain its tools.
-   [API Keys](../user-guide/api-keys.md) covers creating a key to sign the CLI in with.
-   The package README, `tools/VamsMCP/README.md`, lists every tool with its parameters.
