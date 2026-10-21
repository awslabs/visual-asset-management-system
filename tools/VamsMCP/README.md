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

> `vamscli setup` takes the web application URL or the API Gateway URL. Either way the profile
> stores the API Gateway invoke URL, which the MCP server reads.

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

| Variable                  | Description                                                                          |
| ------------------------- | ------------------------------------------------------------------------------------ |
| `VAMS_PROFILE`            | vamscli profile to use (default: active profile)                                     |
| `VAMS_ENABLE_WRITES`      | `true` to expose create/update tools and tools that issue a credential (default off) |
| `VAMS_ENABLE_DESTRUCTIVE` | `true` to expose archive/delete tools (needs writes on; default off)                 |
| `VAMS_MAX_PAGES`          | Max pages auto-followed for list endpoints (default 20)                              |
| `VAMS_PAGE_SIZE`          | Page size per paginated call (default 100, clamped to 1000)                          |

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
                "list_api_routes",
                "list_constraint_permission_objects",
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

A tool returns a failure as data rather than raising it: `{"error", "error_type"}`
plus the machine-readable fields the CLI's `--json-output` carries for the same
error. An `error_type` of `RequestBlockedError` is a request the deployment's AWS
WAF web ACL blocked before VAMS handled it, ahead of both authorization tiers. It
carries `blockedBy` (`waf`, or `waf-or-stage` for a bare API Gateway `Forbidden`,
which a base URL that names no deployed stage also produces) and, when known,
`requestId`. Signing in again does not change it, and neither does retrying it:
report the `requestId` to the VAMS administrator, who finds the rule that matched
in the web ACL's sampled requests or its `aws-waf-logs-vams-*` log group.

### Read / search (always available)

`list_allowed_api_routes`, `list_api_routes`, `list_constraint_permission_objects`,
`list_databases`, `get_database`, `list_buckets`, `list_assets`, `get_asset`,
`list_asset_files`, `get_asset_metadata`, `get_database_metadata`,
`list_asset_versions`, `get_asset_version`, `get_asset_history`, `get_asset_links`,
`search_assets`, `search_files`, `get_search_fields`, `list_workflows`,
`list_workflow_executions`, `list_tags`, `list_tag_types`, `list_metadata_schemas`,
`generate_download_url`, `generate_download_urls_bulk`, `find_and_summarize`.

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
`check_subscription`, `get_api_key`, `get_user_api_key`, `list_api_keys`, `list_user_api_keys`.

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
`renderedConfigTruncated` is set. Every step also lists its log sources in
`availableLogs`, each with the `logId` that `get_execution_logs` reads one source
by, and `include_sub_executions=True` adds `subExecutions` — each registered
sub-process (a nested state machine or a container job) with per-stage status
derived from its own execution history. The flag is off by default because that
derivation reads Step Functions history.

`get_execution_logs` full mode takes `limit` (default 100, server cap 1000),
`next_token`, `filter_pattern`, `start_time`, and `end_time` (epoch
milliseconds), plus — with `pipeline_execution_id` — `log_id` and `stage_name`.
A pipeline container emits thousands of lines, so raise `limit` and walk the
returned `nextToken` with the same parameters rather than concluding anything from
the first page. A step-scoped full-mode result carries `logSources` (each known
source with a read status: `read` with an event count, `denied`, `notFound`,
`error`, `empty`, `skipped`, or `unscoped`) and every `subProcessEvents` item names its
source by `logId`; `log_id` reads one source alone, and `stage_name` keeps only the
sources and history of one sub-state-machine stage.

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

`list_api_routes` and `list_constraint_permission_objects` are the two
vocabularies a permission constraint is written in, and neither is scoped to the
caller: the first returns every API route (`path`, `methods`, `category`,
`unauthenticated`) that an `api` constraint can name, the second the
`objectTypes` (with the `fields` a criterion may test), `operators`,
`permissions`, and `permissionTypes` as `{label, value}` pairs — send the
`value`. Both are fixed per deployment. `list_allowed_api_routes` remains the
one that says what this session may do.

`generate_download_urls_bulk` signs up to 1500 files of one asset in a single
request. `file_keys` entries are either a relative path (latest version) or
`{"key", "versionId"}`; `asset_version_id` / `asset_version_alias` pin every key
to an asset version instead, and cannot be combined with per-key versionIds. The
response is per file: each `files[]` entry carries `success` and either
`downloadUrl` + `versionId` or `error`. A missing or non-downloadable path is
skipped with `success: false` while the call itself succeeds, so read every
entry rather than the top-level `downloadUrl`, which is only the first signed
URL.

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
`abort_execution` returns `warnings` and `notAbortable` beside `message` when a
registered sub-process could not be stopped: each `notAbortable` entry
(`resourceType`, `label`, `stageName`, `locator`) is compute of a type VAMS has no
stop API for — an externally owned pipeline's own job, say — that keeps running
after the execution reads ABORTED. Treat a non-empty list as "not everything
stopped", not as a failed abort. A group abort returns the group's results with
both arrays beside them, gathered from its members: each warning is prefixed with
its member's execution id and each `notAbortable` entry carries its member's
`executionId`.

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

API keys: `create_api_key`, `create_user_api_key`, `update_api_key`,
`update_user_api_key`.

`create_api_key` and `create_user_api_key` return the key VALUE (`apiKey`) in
their response, and that is the only time VAMS ever shows it — a hash is stored,
the value is not. The value is a bearer credential carrying every permission of
the user the key acts as, and because it is returned as tool output it lands in
the host's conversation log and telemetry. Keep both create tools out of
`autoApprove`, and relay the value to the user once rather than repeating it.
`create_api_key` (administrative) mints a key acting as ANY `user_id`;
`create_user_api_key` (self-service) always binds the key to the authenticated
user and requires an expiry within 365 days. `update_*_api_key` with
`is_active=False` is the reversible revoke: the key stops authenticating until it
is re-enabled, and nothing is destroyed. The API Gateway authorizer caches its
decision for about 30 seconds in both directions: a key that authenticated within
that window keeps working until it expires, a key refused within it stays refused
for the same time after re-enabling, and a disabled key not in use is refused on
its next call.

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

API keys: `delete_api_key`, `delete_user_api_key`. Both are permanent and lock
out every client still presenting the key at once; `update_api_key` /
`update_user_api_key` with `is_active=False` revokes the same access reversibly
and is the better first move.

## Security notes

-   Authorization is exactly your vamscli user's VAMS permissions (RBAC/ABAC).
-   Writes and destructive tools are **off by default**. Keep destructive tools
    out of `autoApprove`.
-   `generate_download_url` and `generate_download_urls_bulk` return presigned Amazon S3 URLs
    (one per requested file for the bulk tool), each a bearer credential: it
    needs no further authentication and anyone holding it can download the
    object until it expires: at most `app.authProvider.presignedUrlTimeoutSeconds`
    (24 hours by default), sooner if the credentials that signed it expire first.
    Because they are returned as tool output they also land in
    the host's conversation log and telemetry, so neither tool is in the
    `autoApprove` sample above. Where a URL can be used is bounded only by the
    deployment's `app.assetBuckets.presignedUrlNetworkRestrictions`, which is
    unset by default.
-   `get_api_key`, `get_user_api_key`, `list_api_keys`, `list_user_api_keys`, and
    `list_subscriptions` are reads, but they return credential inventory and user
    identifiers respectively — API key names and the users they act as, or the
    subscriber lists behind a deployment's notifications. None returns a usable
    credential, and all are bound by the caller's own route permissions, but their
    output lands in the host's conversation log like any other, so they are
    deliberately absent from the `autoApprove` sample above.
-   `create_api_key` and `create_user_api_key` (write tier) return a real
    credential: the one-time key VALUE, a bearer token carrying every permission
    of the user it acts as. It is returned as tool output, so it is recorded
    wherever the host records tool output. Never place either in `autoApprove`,
    and treat a transcript that contains one as holding a live secret until the
    key is disabled (`update_api_key` / `update_user_api_key` with
    `is_active=False`, reversible) or deleted (`delete_api_key` /
    `delete_user_api_key`, permanent). The administrative create can mint a key
    acting as any user the caller's role may name — it is only as safe as the
    caller's own API-key route permissions.
-   The server persists nothing; revoking access is just `vamscli auth logout`
    (or letting your session expire).

## Add-on tools

The optional add-ons' tools are part of this server, in `vams_mcp/addon_tools/`. They are
registered whether or not the deployment enables the add-on, under the same `VAMS_ENABLE_WRITES`
and `VAMS_ENABLE_DESTRUCTIVE` gates as the tools above. Each one first checks the feature switches
the vamscli profile recorded at its last `vamscli auth login`, and on a deployment that has not
enabled the add-on answers the sentence the CLI prints, with `error_type` `FeatureDisabledError`,
without making a request; log in again if the add-on was enabled since. The `autoApprove` sample
above names no add-on tool: each add-on below lists the reads that are safe to add to it.

### EDH Workstations

The EDH Workstations add-on's tools manage its GPU virtual desktops, their images, the AMI recipes
that build them and its container jobs. The add-on's guide is in
`documentation/docusaurus-site/docs/developer/edh-workstations/`.

#### Read (always available)

`list_edh_workstations`, `get_edh_workstation`, `list_edh_catalog_images`,
`list_edh_catalog_instance_types`, `get_edh_catalog_lease`, `list_edh_admin_images`,
`list_edh_admin_instances`, `list_edh_stale_instances`, `get_edh_capacity`,
`get_edh_health`, `get_edh_admin_settings`, `list_edh_container_catalog`, `list_edh_container_jobs`,
`list_edh_ami_recipes`, `get_edh_ami_recipe`, `list_edh_ami_recipe_builds`. On a
deployment that has not enabled the add-on, every EDH tool answers
`The EDH Workstations add-on is not enabled for this deployment ...`. The
two container reads answer only when the deployment also enables the add-on's
container jobs, and otherwise answer `EDH container jobs are not enabled for
this deployment ...` the same way. The three AMI recipe reads answer only when
the deployment also enables the add-on's AMI recipe builds, and otherwise answer
`EDH AMI recipes are not enabled for this deployment ...` the same way. `list_edh_container_jobs` takes
an optional `backend` (`batch`, the server default, or the experimental `eks`,
which also takes `cluster` — required — and `namespace`, server default
`default`) and lists that backend's jobs only; each row's `jobId` is the value
`kill_edh_container_job` takes.

The EDH lists are not paginated — each returns every row in one `Items` page,
so none takes `max_items` or `starting_token` and none is ever `truncated`.
`list_edh_workstations` and `get_edh_workstation` cover the authenticated
user's own desktops; a workstation owned by someone else is reported as not
found. `list_edh_catalog_images` marks each image with `category`: `amiRecipe`
for an image an AMI recipe build registered, which also carries `recipeId`,
`recipeDisplayName` and `recipeVersion`, and `baseline` for every other image;
recipe images come first, each group sorted by name. Each image also carries
`architecture`, its CPU architecture as EDH reports it (`x86_64`, `arm64`, ...),
or null when EDH reports none.
`list_edh_catalog_instance_types` returns `Items` of `{instanceType, architectures}`,
sorted by name with numbers in numeric order (`g5.2xlarge` before `g5.12xlarge`);
`architectures` names the desktop profile's architecture groups that list the
type, and is `[]` when the profile does not group its types. A type runs an
image when the image's `architecture` is null, the type's `architectures` is
empty, or it contains the image's architecture; pass only such a type to
`create_edh_workstation`.
`get_edh_catalog_lease` returns the user-readable lease policy (no audit fields).
A row's `session_id` is its `sessionId`, or its `bootstrapId` while a launch has no
session id yet (`create_edh_workstation` answers 202 with `sessionId` null and
`state` CREATING; `lastError` names why a launch ended ORPHANED).
The `admin` reads require the EDH admin routes;
`get_edh_capacity` and `get_edh_health` are in the `autoApprove` addition below, while
`list_edh_admin_instances` and `list_edh_stale_instances` name other users,
`list_edh_admin_images` returns the cluster's whole image inventory, and
`get_edh_admin_settings` returns the admin lease settings including `updatedBy`,
and the three AMI recipe reads name the administrators who created, changed or
built each recipe (`createdBy`, `updatedBy`, `requestedBy`), so those seven are
not. Each `list_edh_admin_images` item also carries `architecture`, the CPU
architecture EDH reports for the image, such as `x86_64` or `arm64`, or null
when EDH reports none. `list_edh_ami_recipes` returns each recipe with `latestBuild` (its newest
build, or null before the first); `get_edh_ami_recipe` returns one recipe the same
way, not its build history; `list_edh_ami_recipe_builds` returns the builds newest
first, each with a `status` of QUEUED, BUILDING, REGISTERING, AVAILABLE,
REGISTER_FAILED, FAILED or RETIRED. `list_edh_stale_instances` takes an optional `idle_minutes`
(server default 120). `get_edh_health` reports whether the proxy can reach the
EDH API and is the first call to make when an edh tool says the cluster did not
answer. An edh write or destructive tool whose error carries `error_type`
`EdhOutcomeUnknownError` and `outcomeUnknown: true` (`... not confirmed`) is not
a health problem: EDH may have carried the request out, so call the matching list tool
(`list_edh_workstations`, `list_edh_admin_instances`, `list_edh_admin_images` or
`list_edh_container_jobs`) before retrying.

Add the safe EDH reads to the sample's `autoApprove` array when the deployment
enables the add-on:

```json
[
    "list_edh_workstations",
    "get_edh_workstation",
    "list_edh_catalog_images",
    "list_edh_catalog_instance_types",
    "get_edh_catalog_lease",
    "get_edh_capacity",
    "get_edh_health"
]
```

#### Write (require `VAMS_ENABLE_WRITES=true`)

`get_edh_workstation_connect`, `create_edh_workstation`,
`renew_edh_workstation`, `start_edh_workstation`, `stop_edh_workstation`, `register_edh_vdi_image`,
`register_edh_simulation_image`, `stop_edh_admin_instance`, `update_edh_admin_settings`,
`submit_edh_container_job`, `create_edh_ami_recipe`, `update_edh_ami_recipe`,
`duplicate_edh_ami_recipe`, `start_edh_ami_recipe_build`, `register_edh_ami_recipe_build`.

`get_edh_workstation_connect` changes nothing, but it issues the DCV
`connectionUrl` of one of the caller's ACTIVE workstations — a URL carrying a
session auth token (see the security notes below) — so it is exposed with the writes rather
than always available, and it is never in `autoApprove`. A workstation that is
not connectable (CREATING, STOPPED, or without an EDH session) is refused with a 409.
Each call makes EDH issue at most one connection token per request EDH answers,
for that desktop; VAMS repeats a read once after a transport failure or a gateway
error, which can make a second. `list_edh_workstations` and `get_edh_workstation`
make EDH issue no connection token; a row's `connectionAvailable` is true while
EDH reports the desktop running.
`create_edh_workstation` and `start_edh_workstation` start real GPU compute that
is billed until the desktop is stopped or deleted; `stop_edh_workstation` and
`stop_edh_admin_instance` interrupt whatever is running on a desktop (the admin
form on ANOTHER user's); `renew_edh_workstation` extends a leased workstation's
`leaseExpiresAt` to the full policy window from now, thereby extending the runtime
of a GPU desktop. Keep all five out of `autoApprove` and confirm each with the user
first, as for `execute_workflow`. `update_edh_admin_settings` is a partial patch of
the lease policy: only the arguments you pass are changed.
`create_edh_workstation` returns the record with `state` CREATING and `sessionId`
null (HTTP 202), plus `sessionBootstrap` (`provisioned`, `skipped`, `unavailable`
or `disabled`, the outcome of placing a session key for the desktop). The launch
runs asynchronously — poll `list_edh_workstations` and select the row by its
`bootstrapId` until it reports ACTIVE and a `sessionId`, because
`get_edh_workstation` accepts a `bootstrapId` only while the row has no
`sessionId` and reports it not found once EDH assigns one; read `lastError` on a
row that ended ORPHANED. A repeat with the same `bootstrap_id` by the same user
returns the stored record (HTTP 200) and launches nothing, while one that another
user's launch holds is refused (409).
Its `os_family` (`linux` or `windows`) is the chosen catalog image's `osFamily`
and is required; its `bootstrap_id` is the idempotency key — re-send the one from
a call that timed out rather than launching again. Its optional `lease_hours` (1 up to the
policy's `leaseHours`, see `get_edh_catalog_lease`) stamps a lease on the launch; it is
refused (400) when leases are disabled and ignored when the policy is `forced`. An
`instance_type` the image's profile lists only under architectures of another family
than the image's (`x86_64` and `i386` are one family) is refused (400,
`instanceType does not match the image's architecture`). A launch
is refused (403) for a session authenticated with a VAMS API key.
`start_edh_workstation` returns the updated record plus `sessionBootstrap`, as for
a launch; while EDH still lists the desktop stopped, a start is refused (409,
`error_type` `EdhStartInProgressError`, `startInProgress: true`) when another start
of it is being processed,
within 3 minutes of one EDH did not confirm, while the reconciler revokes the stopped
desktop's key, or while a lease renewal re-issues its key (up to 5 minutes); if the
list still shows it stopped after 5 minutes, start it again.
`renew_edh_workstation` adds `sessionBootstrap` when it re-issued, or tried to
re-issue, the desktop's session key: for an ACTIVE workstation with no start in
progress, renewed by a session that did not authenticate with a VAMS API key;
otherwise the field is absent and the renew extends the lease only. The two `register_*` tools
take the model's
fields by name (`ami_id`, `stack_name`, `root_size`, plus `base_os` and
`virtual_desktop_profile_id` for desktops or `target_node_profile_id` — and an
optional `target_node_user_data_id`, the EDH target-node user-data template,
sent only when given so the server keeps its default of template 1 — for
simulation nodes) and require the EDH admin routes. Their `imageId` is the new
stack's id, read back from the catalog; a read-back that does not find the stack,
or a project assignment that fails, is returned as the error with the stack left
registered.
`submit_edh_container_job` starts a Batch or EKS job through EDH that is billed
until it finishes or is killed; like the workstation tools it stays out of
`autoApprove`. Its `job_queue` is required (the Batch job queue, or the EKS
cluster for the experimental `eks` backend), its `command` is a token list and
`environment` a name→value map, sent under the request model's `command` /
`environment` keys, and `memory` is in MiB (at least 512). It answers 202 with
the job's `jobId` and `backend`, the pair `kill_edh_container_job` takes.
`start_edh_ami_recipe_build` starts an EC2 Image Builder build instance that is
billed until the image completes; like the other compute tools it stays out of
`autoApprove`.
`create_edh_ami_recipe` and `update_edh_ami_recipe` take the recipe as a dict in
the shape of the CLI's recipe file, with inline components only — each `{arn}` or
`{name, data}`; the `{file}` components the CLI reads from disk are not resolved
here. `update_edh_ami_recipe` replaces the whole definition of an `admin` recipe;
a `config` recipe is refused (409), and neither tool starts a build. Both refuse
(409) a `stackName` another recipe already uses. Both, and
`duplicate_edh_ami_recipe`, refuse (400) a `buildInstanceType` or `parentImage`
that EC2 reports as running another architecture family than the recipe's
`architecture` (`x86_64` and `i386` are one family), with
`buildInstanceType does not match the recipe's architecture` or
`parentImage does not match the recipe's architecture`; a lookup that fails
refuses nothing.
`duplicate_edh_ami_recipe` takes `source_recipe_id` (a `config` or `admin`
recipe) and the new `recipe_id`, plus an optional `display_name` (default: the
source's plus ` (copy)`), `stack_name` (default: the source's plus ` Copy`) and
`keep_versions` (the number of AMIs pruning keeps for the copy, 1-20; default:
the source's `keepVersions`).
It reads the source and creates an `admin` recipe with its definition and
`duplicateOf`; the service copies the source's bundled files (`assets`), which
an update of the copy keeps and cannot change, and records `duplicatedFrom`. It
starts no build.
`start_edh_ami_recipe_build` answers 202 with the build row and an `outcome` of
`started` (status BUILDING) or `queued` (status QUEUED, behind the recipe's
running build of other content), or 200 with `outcome: in-progress` and the
build already QUEUED, BUILDING or REGISTERING for the recipe's current content,
starting nothing; registration in EDH, retirement of the recipe's other AVAILABLE
builds' stacks and pruning of AMIs beyond `keepVersions` follow automatically. A build
that starts is refused (409), with nothing created, for a parent AMI EC2 does not
find (`the parent image could not be found`) and for a parent or build instance
type EC2 reports as running another architecture family than the recipe's
(`the parent image's architecture does not match the recipe's architecture`,
`buildInstanceType does not match the recipe's architecture`). A build is registered
automatically when its image completes, so `register_edh_ami_recipe_build` is the
retry for a REGISTER_FAILED build — any other status is refused (409) — and a
successful retry retires the recipe's other AVAILABLE builds.

#### Destructive (require `VAMS_ENABLE_DESTRUCTIVE=true`)

`delete_edh_workstation`, `delete_edh_vdi_image`,
`delete_edh_simulation_image`, `deactivate_edh_admin_instance`,
`kill_edh_container_job`, `delete_edh_ami_recipe`, `retire_edh_ami_recipe_build`.
Deleting a
workstation removes the desktop and its session; stopping it first
(`stop_edh_workstation`) releases the GPU reversibly. The two image deletes
unregister a software stack for a specific kind — a desktop id sent to the
simulation delete is not found, and vice versa. `deactivate_edh_admin_instance`
terminates ANOTHER user's desktop and, like `abort_execution`, acts on running
compute; `stop_edh_admin_instance` is the reversible form.
`kill_edh_container_job` stops a running job's compute and cannot be undone; pass
the listing's `jobId` and the `backend` the job was listed under (server default
`batch`; `eks` also needs its `cluster` and `namespace`), because a job on the
other backend is reported as not found. `delete_edh_ami_recipe` removes an `admin`
recipe, the EC2 Image Builder resources its last build recorded and every build
row, answering `recipeId`, `deleted` and `warnings`; the AMIs, snapshots, EC2 Image
Builder images and EDH stacks its builds still hold stay, and without the build
rows VAMS cannot retire or prune them, so first retire the AVAILABLE builds, and
the REGISTER_FAILED builds whose EDH stack exists, that should go. A `config`
recipe, one with a QUEUED, BUILDING or REGISTERING build, or one another delete
is already removing, is refused (409), and so is a recipe while a build's AMI
removal is pending: a RETIRED build with `amiRemovalPending`, or a RETIRED,
REGISTER_FAILED or FAILED build outside the recipe's `keepVersions` newest AMIs
that still holds its AMI, as while an Amazon EC2 instance uses the AMI. Delete
the recipe once the sweep has deregistered those AMIs (`amiDeregisteredAt`).
`retire_edh_ami_recipe_build` deactivates an
AVAILABLE or REGISTER_FAILED build's EDH stack, then deregisters its AMI and
snapshots and deletes its Image Builder image once no Amazon EC2 instance uses the
AMI; while one does, the build stays RETIRED with `amiRemovalPending` and
`amiInUseBy`, and VAMS removes the AMI after the last instance is terminated.
Retiring the AVAILABLE build leaves the recipe with no launchable image. Any other
status, or a build whose stack cannot be found, is refused (409).

#### Security notes

-   `get_edh_workstation_connect` (write tier, for this reason) returns a DCV
    `connectionUrl` carrying a session auth token: anyone holding it can open that
    desktop until the token expires, and it needs no further authentication. It is
    returned as tool output, so it lands in the host's conversation log and
    telemetry — the same exposure as the presigned-URL tools, which is why it is
    absent from the `autoApprove` addition above. Relay it to the user once; do not
    repeat it. Each call makes EDH issue at most one connection token per request
    EDH answers, for that desktop; VAMS repeats a read once after a transport
    failure or a gateway error, which can make a second. `list_edh_workstations`
    and `get_edh_workstation` make EDH issue none.
-   No tool returns the Windows sign-in of an EDH workstation: its password is a
    credential for the desktop, so VAMS shows it to the owner only in the web app
    and with `vamscli edh workstation connect --show-windows-password`. The EDH
    workstation tools pass their `session_id` through vamscli's client, which
    refuses, before any request, an id that is not a single path segment and
    any request path with an empty, `.` or `..` segment, so no workstation id
    can address the sign-in route.

## Adding tools in a fork

A fork of VAMS adds its own tools in `vams_mcp/custom_tools.py`, which upstream ships
as a no-op and does not modify, so the fork's tools do not conflict when it merges an
upstream release. `server.py` calls `register_custom_tools(mcp, client, config,
tool_result)` once, after its own tools are defined:

```python
# vams_mcp/custom_tools.py (in the fork)
from typing import Any, Dict, Optional


def register_custom_tools(mcp, client, config, tool_result) -> None:
    @mcp.tool()
    @tool_result
    def custom_list_widgets(
        database_id: str, max_items: Optional[int] = None, starting_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """List the widgets of one database. The walk is bounded: `truncated` means rows were not
        seen, and `NextToken` continues it when passed back as `starting_token`."""
        return client.paginate(
            lambda params: client.api.list_custom_widgets(database_id, params=params),
            max_items=max_items,
            starting_token=starting_token,
        )

    if config.enable_writes:

        @mcp.tool()
        @tool_result
        def custom_create_widget(database_id: str, name: str) -> Dict[str, Any]:
            """Create a widget in a database."""
            return client.api.create_custom_widget(database_id, name)
```

-   Gate write tools on `config.enable_writes` and destructive tools on
    `config.enable_destructive`, as the upstream tools are; the `VAMS_ENABLE_*`
    variables then govern them too.
-   `client.api` is the `vamscli` `APIClient`, which inherits the fork's methods from
    `vamscli/utils/custom_api_client.py`, so a custom tool calls the fork's API
    through the same authentication and retry path as the upstream tools, and
    `client.paginate` follows `NextToken` with the same bounds as the upstream list tools.
-   Prefix tool names with `custom_` so a later upstream tool does not collide with
    one of yours, and list your tools (and any `autoApprove` choice) in your own
    documentation. A tool whose name an upstream tool already uses is not registered:
    the server keeps the upstream tool and logs `Tool already exists: <name>`.

The "Extending VAMS in a fork" developer guide has the end-to-end example.

## License

Apache-2.0.
