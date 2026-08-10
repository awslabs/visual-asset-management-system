# CLAUDE.md - VamsMCP (tools/VamsMCP/)

> Steering document for Claude Code / Kiro when working in the VAMS MCP server.
> Auto-loaded when the working context is within `tools/VamsMCP/`.

---

## Project Overview

VamsMCP is a [Model Context Protocol](https://modelcontextprotocol.io/) server
that exposes the VAMS REST API as agent-callable tools. It is built with the
`mcp` SDK (`FastMCP`) and **reuses the `vamscli` package** for API access and
authentication.

-   **Entry point**: `vams_mcp/server.py` (`main()` -> `mcp.run()`, stdio transport)
-   **Stores no credentials**: authenticates via the user's existing `vamscli`
    profile (`vamscli setup` + `vamscli auth login`).
-   **Reused dependency**: `vamscli.utils.api_client.APIClient` and
    `vamscli.utils.profile.ProfileManager`.

---

## Architecture

### Directory Structure

> **Maintenance note:** Update this tree when adding modules or tool groups.

```
tools/VamsMCP/
  pyproject.toml             # package + deps (mcp, vamscli, requests)
  .env.example               # documents every supported env var
  vams_mcp/
    __init__.py              # __version__ (roll with pyproject.toml)
    config.py                # env-based config (profile, feature gates, pagination)
    client.py                # VamsClient: wraps vamscli APIClient + ProfileManager
    server.py                # FastMCP app, response-shape helpers, tool definitions, logging guard
  tests/
    test_config.py           # env parsing + the writes/destructive gate interaction
    test_client_helpers.py   # paginate(), unwrap_message(), trim_search_results()
    test_server_tools.py     # tool behavior + the source-layout assertions of Mandatory Rule 4
```

### Layers

1. `config.py` — reads env vars into a frozen `Config`. No secrets/URLs.
2. `client.py` — `VamsClient` resolves the vamscli profile, discovers the API
   Gateway URL, verifies auth, and builds an `APIClient`. Adds `paginate()`,
   `get_json()/post_json()`, and `trim_search_results()`.
3. `server.py` — defines `@mcp.tool()` functions that call `CLIENT`. Reads are
   always registered; writes/destructive tools are registered conditionally.

`client.py` helpers worth knowing: `paginate()` (follows `NextToken`, normalizes
any list field onto `Items`, flags `truncated`), `unwrap_message()` (strips the
legacy `message` envelope), and `trim_search_results()` (compacts an OpenSearch
response to `total` / `returned` / `results`).

`server.py` adds two response-shape helpers of its own, deliberately kept out of
`VamsClient` because they describe how the tool surface presents a response
rather than the client's contract with the CLI (whose `message`-envelope
asymmetry other callers rely on):

-   `_unwrap_message_with_warnings(page)` — unwraps the envelope while keeping a
    sibling top-level `warnings` array. Used by `create_pipeline` /
    `update_pipeline` (and `unarchive_pipeline`, which routes through the
    update), whose response model has no warnings field, so that array is the
    only copy.
-   `_paginate_with_page_metadata(fetch_page, passthrough_keys=..., ...)` —
    paginates while collecting each page's `warnings` (deduplicated, in order)
    and named echo fields onto the result, marking it `truncated` when any page
    withheld rows. `list_executions` uses it to carry `warnings` plus the applied
    `filterStartDate` / `filterEndDate` window.

---

## Mandatory Rules

1. **stdout is sacred.** MCP stdio transport uses stdout for JSON-RPC. Never
   `print()` to stdout and never let logging reach stdout. `server.py` calls
   `_force_logging_to_stderr()` at startup — keep it, and route any new logs to
   stderr.
2. **No credential storage.** Do not add API-key or password env vars that get
   persisted. Auth must come from the vamscli profile. This server is
   distributed publicly for bring-your-own-account use.
3. **Reuse `APIClient` methods.** Prefer existing `vamscli` `APIClient` methods
   over hand-rolled requests so retries/backoff/typed-errors are inherited. Use
   `VamsClient.get_json/post_json` only for endpoints without a dedicated method.
4. **Gate mutations.** New create/update tools go inside the
   `if CONFIG.enable_writes:` block. Destructive tools (delete/archive) go inside
   `if CONFIG.enable_destructive:` and must never be in `autoApprove`.
   `enable_destructive` is AND-ed with `enable_writes` in `Config.from_env()`, so
   `VAMS_ENABLE_DESTRUCTIVE=true` alone registers nothing.
   `execute_workflow` and `rerun_execution` start real AWS compute, so keep them
   out of `autoApprove` too even though they are write-tier, not destructive.
   Because the tools are module-level `def`s, misplacement fails **silently**: a
   duplicate name shadows the earlier definition, and a `def` after the
   `if __name__` entrypoint or outside its gate block never executes, so the tool
   is simply absent with no import error. `tests/test_server_tools.py` asserts the
   source layout (uniqueness, position relative to each gate and the entrypoint,
   and that every `CLIENT.api.*` call resolves on `APIClient`) — keep it passing.
5. **Return data, not exceptions.** Wrap tool bodies with `@tool_result` so
   failures return `{"error": ..., "error_type": ...}`.
6. **API Gateway URL only.** The server rejects CloudFront URLs implicitly (the
   profile stores the API Gateway URL). Never point at the CloudFront web URL.
7. **Match the endpoint's list field.** `paginate()` normalizes results onto
   `Items`, but it reads the source list from `items_key` — which differs per
   endpoint (`Items` for databases/assets/workflows, `items` for `listFiles`,
   `versions` for `getVersions`, `metadata` for the metadata APIs). It also
   unwraps the legacy `message` envelope used by tags, tag types, workflows, and
   workflow executions. Verify both against the handler's response model before
   adding a list tool; a mismatch silently returns zero items.

    **Unwrap the envelope on non-paginated pipeline/workflow/execution tools.**
    Every `APIClient` method in that domain returns the handler's raw
    `{"message": ...}` body, because `_pwe_body()` leaves the envelope intact and
    lets callers decide. The asset and database methods return unwrapped data, so
    a tool that forwards the envelope hands agents a nesting level no other tool
    has. Wrap single-object and write calls in `CLIENT.unwrap_message(...)`;
    `paginate()` already does it.

    **Repeat a filter-pinned endpoint's filters on every page.** Some continuation
    tokens are only valid alongside the filters they were issued with — the paged
    execution-detail metadata read (`page_execution_detail_metadata`) pins its
    token to the `collection` and `pipelineId` of the request that produced it, and
    the handler answers a mismatch with a 400. `paginate()` passes only `pageSize`
    and `startingToken`, so merge the filters into the params inside the
    `fetch_page` callable rather than sending them on the first request alone.

8. **Never let a bounded response read as a complete one.** A VAMS handler can
   answer successfully while withholding rows, and it reports that out of band: a
   top-level `warnings` array (a page that hit its distinct-asset permission-check
   cap), a `truncatedCollections` list (a bounded execution-detail collection), or
   an echoed filter window (`filterStartDate` on the executions list). Because
   `paginate()` rebuilds its result from the accumulated items alone, every one of
   those is dropped by default, and the agent reports an understated count or
   concludes an object does not exist.

    Use `_paginate_with_page_metadata()` instead of `CLIENT.paginate()` for any
    list endpoint that can report a `warnings` array or echo its applied filters,
    and `_unwrap_message_with_warnings()` for any single-call endpoint that returns
    `warnings` as a sibling of `message`. Then say in the docstring what the flag
    means for the agent's conclusion, not just that the field exists — a tool
    description is the only place an agent learns not to trust a short list.

9. **Forward every narrowing parameter the endpoint supports.** A tool that omits
   one silently pins the agent to the server default: `get_execution_logs` without
   `limit`/`next_token` caps a container's output at 100 events with no way past
   the first page, and `list_workflows` without `include_archived` makes an
   archived workflow's id — the argument `unarchive_workflow` requires —
   undiscoverable. Read the matching `vamscli` command in
   `tools/VamsCLI/vamscli/commands/` for the full parameter set the endpoint
   accepts, and forward a parameter only in the mode that acts on it (the log
   paging parameters are sent in `full` mode only, since truncated mode returns
   one joined blob and no continuation token).

10. **Support both `mcp` major versions.** `mcp` 1.x exposes `FastMCP` from
    `mcp.server.fastmcp`; `mcp` 2.x renamed it to `MCPServer` in
    `mcp.server.mcpserver` and removed the old module. `server.py` imports it
    through a `try`/`except ImportError` alias (`McpServer`) — keep that shim and
    the `mcp>=1.2.0,<3.0.0` pin, and test against both before widening it.

---

## Adding a New Tool

1. Confirm the underlying operation exists on `vamscli` `APIClient`
   (`tools/VamsCLI/vamscli/utils/api_client.py`); prefer it. Read the method's
   real signature — several take required positional arguments the endpoint
   demands (for example `execute_workflow` requires `workflow_database_id`).
2. Add an `@mcp.tool()` + `@tool_result` function in `server.py`. Use type hints
   and a clear docstring (both feed the tool's MCP schema/description).
3. Place it in the correct section: read (top), write (`enable_writes`), or
   destructive (`enable_destructive`).
4. For list endpoints, use `CLIENT.paginate(...)` with the correct `items_key`
   (see Mandatory Rule 7), or `_paginate_with_page_metadata(...)` when the
   endpoint reports `warnings` or echoes its applied filters (Rule 8). For search,
   use `CLIENT.trim_search_results(...)`.
5. Check the request payload against the backend Pydantic model in
   `backend/backend/models/` — required fields, minimum lengths, and exact key
   names (for example `createFolder` takes `relativeKey` and it must end in `/`).
6. Forward the endpoint's full narrowing/paging parameter set (Rule 9), and state
   in the docstring what a bound or a warning means for the agent's conclusion.
7. Add the tool to the README's tool list, and — if it is a safe read — to the
   `autoApprove` array of the sample MCP host config in that same README. Any tool
   whose parameters or response fields change also needs its README paragraph
   updated: the tool list is the only place the parameter set is documented
   outside the docstring.
8. Add a unit test in `tests/` mocking `CLIENT`. Assert the params that reach the
   `APIClient`, not just that the call happened: a dropped optional parameter is a
   silent server-default, which no assertion on the return value catches.
9. When the server's contract with the CLI changes, roll the version in **both**
   `pyproject.toml` and `vams_mcp/__init__.py`, alongside
   `tools/VamsCLI/vamscli/version.py` (root `CLAUDE.md` Pattern 7 rule 6).

## Upstream Dependency on the CLI

This server is **downstream of `tools/VamsCLI`** — it calls `APIClient` methods
directly rather than the REST API. Any change to a `vamscli` `APIClient` method
signature or response handling can break a tool here without an error at import
time. Root `CLAUDE.md` Pattern 7 defines the propagation chain; when a CLI change
reveals a missing or wrong `APIClient` method, fix it in the CLI instead of
hand-rolling raw requests here.

---

## Keeping Steering Documents in Sync

VAMS maintains two parallel steering families: `CLAUDE.md` files for Claude Code
and `.kiro/steering/` workflow documents for Kiro. Synchronization is
**bidirectional** (root `CLAUDE.md` Rule 11) — a rule authored in either family
must be carried into the other in the same change, or one agent scaffolds
outdated code.

This file's Kiro counterpart is the **MCP propagation section of
`.kiro/steering/CLI_DEVELOPMENT_WORKFLOW.md`** — the MCP server shares that
workflow document with the CLI because it is downstream of it. A change to a rule
in this file, or to the propagation chain itself, must land in:

1. `CLAUDE.md` (root) — Pattern 7, the canonical chain
2. `tools/VamsCLI/CLAUDE.md` — the MCP step in "Adding a New Command"
3. `tools/VamsMCP/CLAUDE.md` — this file
4. `.kiro/steering/CLI_DEVELOPMENT_WORKFLOW.md` — the Step 9 MCP checklist

Also update `documentation/docusaurus-site/docs/developer/agentic-development.md`
when the user-facing description of the MCP server or the propagation chain
changes.

---

## Testing

Always work in this server's own virtual environment. The `mcp` SDK requires
**Pydantic v2**, while the VAMS backend pins **Pydantic 1.10.13** (root
`CLAUDE.md` Rule 1) — installing this server into a shared interpreter upgrades
Pydantic and breaks the whole backend suite at collection time, with the failure
surfacing in `backend/` far from its cause. `vamscli` is not published to PyPI, so
install it from the local path first or the install fails resolving it.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv/Scripts/activate
pip install -e ../VamsCLI        # must come first
pip install -e '.[dev]'
pytest
```

Tests mock `vams_mcp.server.CLIENT` so no live VAMS deployment is required.

---

## Gold Standard References

-   Reused client: `tools/VamsCLI/vamscli/utils/api_client.py`
-   Profile/auth: `tools/VamsCLI/vamscli/utils/profile.py`
-   Tool patterns: `vams_mcp/server.py`
