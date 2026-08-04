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
  vams_mcp/
    __init__.py
    config.py                # env-based config (profile, feature gates, pagination)
    client.py                # VamsClient: wraps vamscli APIClient + ProfileManager
    server.py                # FastMCP app, tool definitions, logging guard
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

8. **Support both `mcp` major versions.** `mcp` 1.x exposes `FastMCP` from
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
   (see Mandatory Rule 7). For search, use `CLIENT.trim_search_results(...)`.
5. Check the request payload against the backend Pydantic model in
   `backend/backend/models/` — required fields, minimum lengths, and exact key
   names (for example `createFolder` takes `relativeKey` and it must end in `/`).
6. If it's a safe read tool, add it to `autoApprove` in the sample
   `.kiro/settings/mcp.json` and the README tool list.
7. Add a unit test in `tests/` mocking `CLIENT`.

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
