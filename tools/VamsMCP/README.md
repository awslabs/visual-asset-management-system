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

> If the deployment authenticates through an external identity provider, or through an Amazon
> Cognito user pool federated to SAML or OIDC, the user has no password in the pool. Sign in with a
> token instead — `vamscli auth login --user-id you@example.com --token-override "<token>"` — and
> the MCP server picks it up from the profile like any other login.

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

All configuration is via environment variables — none are required. Set them in the `env` block
of your MCP host's server entry (see the `mcpServers` sample below); no `.env` file is read.
`.env.example` lists every supported variable for reference.

| Variable                  | Description                                                          |
| ------------------------- | -------------------------------------------------------------------- |
| `VAMS_PROFILE`            | vamscli profile to use (default: active profile)                     |
| `VAMS_ENABLE_WRITES`      | `true` to expose create/update tools (default off)                   |
| `VAMS_ENABLE_DESTRUCTIVE` | `true` to expose archive/delete tools (needs writes on; default off) |
| `VAMS_MAX_PAGES`          | Max pages auto-followed for list endpoints (default 20)              |
| `VAMS_PAGE_SIZE`          | Page size per paginated call (default 100, clamped to 1000)          |

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
                "find_and_summarize",
                "list_asset_comments",
                "list_asset_version_comments",
                "get_comment",
                "check_subscription"
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

`list_tags` and `list_tag_types` accept an optional `database` (return ONLY that
database's tags/tag types — global ones are not included) and `scope` (`global`
for global-only, `all` for every tag/tag type across databases). To see a
database's tags together with the global ones, call once per scope and merge.

Pipelines, workflows, and executions: `list_pipelines`, `get_pipeline`,
`list_pipeline_templates`, `get_pipeline_template`,
`get_pipeline_template_tag_schema`, `get_workflow`, `list_workflow_triggers`,
`get_workflow_trigger`, `list_executions`, `get_execution_details`,
`page_execution_detail_metadata`, `get_execution_logs`.

Comments, subscriptions, and API keys: `list_asset_comments`,
`list_asset_version_comments`, `get_comment`, `list_subscriptions`,
`check_subscription`, `get_api_key`, `get_user_api_key`.

The two comment listings take `max_items` and `page_size` but **no**
`starting_token`: the routes apply those bounds and then discard the pagination
token, so comments past the bound are unreachable through the API and there is
nothing to resume with. A result that reached the bound in force is marked
`truncated` with a `note` — its count is a floor, not a total. Narrow with
`list_asset_version_comments` rather than lowering `max_items`. Deleted comments
are never returned: these routes accept a `showDeleted` flag that the service
ignores, so neither tool offers one. `get_comment` reports a comment that does
not exist as an error, because the endpoint answers 200 with an empty object
rather than a 404.

`check_subscription` answers one asset without paging and returns `subscribed`
alongside the endpoint's raw `message`. Both real answers are HTTP 200 and the
verdict is carried only in that string, so a successful call is not on its own an
answer; `unrecognizedResponse` marks a message that is neither known value, which
means unknown rather than not subscribed.

`get_api_key` (any user's keys) and `get_user_api_key` (the caller's own) return
key METADATA only — name, the user the key acts as, expiry, enabled state. The
key value is shown once at creation and never again, and the stored hash is
stripped by the handler, so nothing usable as a credential is returned. There is
no list tool for API keys, so the `api_key_id` comes from `vamscli api-key list`
or `vamscli api-key user list`.

`list_workflow_triggers` is paged: the endpoint serves one bounded page and the
tool walks it, so the result carries `truncated` and `NextToken` like every other
paginated read and takes `max_items` / `starting_token`. A workflow may carry
several triggers of one type, and a trigger absent from a truncated result may
simply be past the bound. `get_workflow` embeds the same triggers unpaged, so use
that for a workflow whose trigger set is small.

`page_execution_detail_metadata` reads one metadata collection of the detail view
past the bound `get_execution_details` applies — use it when that response names
a metadata collection in `truncatedCollections`.

`get_execution_details` returns a step's configuration in two forms:
`renderedConfig` inline (pre-system-tag, and size-bounded) and
`renderedConfigLocation` whenever that S3 object exists — the fully substituted
body the pipeline read. Read the location to see what actually ran, not only when
`renderedConfigTruncated` is set.

`get_execution_logs` full mode takes `limit` (default 100, server cap 1000),
`next_token`, `filter_pattern`, `start_time`, and `end_time` (epoch
milliseconds). A pipeline container emits thousands of lines, so raise `limit` and
walk the returned `nextToken` with the same parameters rather than concluding
anything from the first page.

`list_workflow_executions` covers ONE asset's history; `list_executions` is the
global, cross-asset list. A workflow id is unique across every database including
`GLOBAL`, so the id identifies the workflow on its own — `workflow_database_id` is
an additional narrowing filter rather than a disambiguator, and a value that is not
the workflow's own database empties the result instead of erroring.

Both accept the same equality filters (`workflow_id`, `workflow_database_id`,
`status`, `trigger_type`, `group_id`, `triggered_by_user_id`) and the same date
window (`filter_start_date`, `filter_end_date`, UTC `YYYY-MM-DDTHH:MM:SSZ`). Both
are lower-bounded by start date at 90 days back by default and echo the applied
`filterStartDate` / `filterEndDate`, so an execution older than the window is
absent by design — widen `filter_start_date` to reach it. `group_id` is how a
group's members are enumerated, since `rerun_execution` re-runs one execution at a
time.

Both also surface a `warnings` array — plus `truncated` — when a page withheld
rows, so a short list is never mistaken for a complete one. On `list_executions` a
page is shortened by its cap on distinct assets resolved for permission checks or
by its per-request work budget; on `list_workflow_executions` by the cap on
executions inspected for the asset or the budget for re-checking runs an earlier
page listed. `list_executions` rows are also permission-filtered on the asset each
run WROTE to, so a run whose output landed somewhere the user cannot read is absent
even when its inputs are readable.

`list_workflows` takes `include_archived` (default off, matching `list_pipelines`
and `list_assets`); it is how an archived workflow's id is found in order to
restore it with `unarchive_workflow`.

Call `list_allowed_api_routes` first — it reports what the authenticated user is
actually authorized to do, so an agent can scope its plan instead of discovering
a 403 mid-task. `search_assets` and `search_files` accept a `geo_search` filter
(point + radius, bounding box, or GeoJSON) against the `geo_MD_location` field,
plus `from_offset` for paging past `size` hits and `sort_field` / `sort_desc` for
ordering by an indexed field instead of relevance (relevance order cannot answer
"the newest N"). `database_id` is matched on `str_databaseid.keyword`, so a
database whose id is a hyphen-token prefix of another (`proj` vs `proj-archive`)
does not leak into the results; `GLOBAL` is not an asset database and is treated
as unscoped.

**Every paginated list tool is BOUNDED.** The walk stops at `max_items`, or at the
`VAMS_MAX_PAGES` work bound, whichever comes first. The result then carries
`truncated: true`, a `note` naming which bound fired, and — when a continuation
token remains — `NextToken`. Pass that token back as the tool's `starting_token`
to continue. A `truncated` result must never be used to report a count or to
conclude that something does not exist. `find_and_summarize` issues one extra
paginated request per hit, so its `size` is clamped to 25; use
`search_assets(from_offset=...)` to page a larger result set.

### Write (require `VAMS_ENABLE_WRITES=true`)

`create_database`, `create_asset`, `update_asset`, `set_asset_metadata`,
`create_folder`, `create_asset_version`.

Pipelines, workflows, and executions: `create_pipeline`, `update_pipeline`,
`create_pipeline_template`, `update_pipeline_template`,
`set_pipeline_template_tag_schema`, `create_workflow`, `update_workflow`,
`set_workflow_trigger`, `execute_workflow`, `rerun_execution`,
`abort_execution`.

`execute_workflow`, `rerun_execution`, and the pipelines they launch start real
AWS compute and can incur cost. `abort_execution` irreversibly STOPS running
compute and, with `group_id`, fans out across every active execution in that group.
Keep all three out of `autoApprove`, and confirm a group abort with the user first.
`rerun_execution` re-runs ONE execution; its `execution_group_id` assigns the new
execution's group membership rather than selecting a group to re-run.

`create_pipeline` and `update_pipeline` can return a `warnings` array on a
successful save (for example a `requireTemplate` pipeline with no default template
chosen, or the stale-deployment notice after an `executionConfig` change).
The save succeeded; the warnings still need relaying.

`create_pipeline_template`, `update_pipeline_template`, and
`set_pipeline_template_tag_schema` reject an unrecognized key in a tag definition
instead of ignoring it, so an invented spelling fails the call rather than storing
a tag that is silently optional or untyped. A template's `overrides` block is
bounded at 64 KB serialized.

Comments, subscriptions, and metadata schemas: `add_comment`, `update_comment`,
`create_subscription`, `update_subscription`, `create_metadata_schema`,
`update_metadata_schema`.

`add_comment` returns the `commentId` it wrote, because the endpoint's
acknowledgement does not contain it. The id is the caller's to choose and the
write is unconditional, so passing an existing one REPLACES that comment with no
error; leave `comment_id` unset and a `uuid4` is generated. `update_comment` and
`delete_comment` are creator-only — anyone else gets a 403 whatever their VAMS
role.

`update_subscription` REPLACES the subscriber list rather than adding to it: every
user absent from the list is unsubscribed from the notification topic. Read the
current list with `list_subscriptions` and send it back with the addition
included; the tool deliberately does not do that read itself, because a list
assembled from a stale read silently unsubscribes whoever joined in between.
`create_subscription` treats an already-subscribed user as an error rather than a
no-op, rejecting the whole call.

`create_metadata_schema` takes `fields` nested as `{"fields": [ ... ]}`, not a
bare list, and `update_metadata_schema` replaces the whole field list rather than
merging into it — send the complete set.

### Destructive (require `VAMS_ENABLE_DESTRUCTIVE=true`)

`archive_asset`, `unarchive_asset`, `delete_asset`, `delete_database`.

Pipelines, workflows, and executions: `archive_pipeline`, `unarchive_pipeline`,
`archive_workflow`, `unarchive_workflow`, `delete_pipeline_template`,
`delete_workflow_trigger`, `permanent_delete_execution`.

Archiving a pipeline or workflow is a soft delete, reversible through the
matching `unarchive_*` tool. Archiving also disables the row, so the
`unarchive_*` tools re-enable it — pass `keep_disabled` to restore it archived-off
but still not runnable. `delete_pipeline_template`, `delete_workflow_trigger`, and
`permanent_delete_execution` are not reversible.

`delete_pipeline_template` can return a `warnings` array naming the auto-triggered
workflows whose trigger still picks the deleted template as a default. The delete
happened; those triggers need repointing and the warnings still need relaying.

Comments, subscriptions, and metadata schemas: `delete_comment`,
`delete_subscription`, `unsubscribe`, `delete_metadata_schema`.

`delete_subscription` and `unsubscribe` are different routes and are not
interchangeable. `delete_subscription` removes the WHOLE subscription and, for an
asset, its notification topic — every subscriber, not the ones named; its
`subscribers` argument is required by the endpoint, which validates it and then
ignores it. `unsubscribe` removes ONE subscriber and leaves the subscription
standing. `delete_comment` is a soft delete, but the listing tools never return
deleted comments and the routes' `showDeleted` flag is ignored, so it is
unrecoverable from an agent's position. `delete_metadata_schema` is not
reversible and has no archived state; metadata already stored against the schema
is left in place, unvalidated.

## Security notes

-   Authorization is exactly your vamscli user's VAMS permissions (RBAC/ABAC).
-   Writes and destructive tools are **off by default**. Keep destructive tools
    out of `autoApprove`.
-   `generate_download_url` returns a presigned Amazon S3 URL, which is a bearer
    credential: it needs no further authentication and anyone holding it can
    download the object until it expires (`app.authProvider.presignedUrlTimeoutSeconds`,
    24 hours by default). Because it is returned as tool output it also lands in
    the host's conversation log and telemetry. Where the URL can be used is
    bounded only by the deployment's
    `app.assetBuckets.presignedUrlNetworkRestrictions`, which is unset by default.
-   `get_api_key`, `get_user_api_key`, and `list_subscriptions` are reads, but they
    return credential inventory and user identifiers respectively — API key names
    and the users they act as, or the subscriber lists behind a deployment's
    notifications. Neither returns a usable credential, and both are bound by the
    caller's own route permissions, but their output lands in the host's
    conversation log like any other, so they are deliberately absent from the
    `autoApprove` sample above. Creating, updating, and revoking API keys is not
    exposed by this server at all.
-   The server persists nothing; revoking access is just `vamscli auth logout`
    (or letting your session expire).

## License

Apache-2.0.
