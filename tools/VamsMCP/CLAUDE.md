# CLAUDE.md - VamsMCP (tools/VamsMCP/)

> Steering document for Claude Code / Kiro when working in the VAMS MCP server.
> Auto-loaded when the working context is within `tools/VamsMCP/`.

---

## Project Overview

VamsMCP is a [Model Context Protocol](https://modelcontextprotocol.io/) server
that exposes the VAMS REST API as agent-callable tools. It is built with the
`mcp` SDK (`FastMCP`) and **reuses the `vamscli` package** for API access and
authentication.

- **Entry point**: `vams_mcp/server.py` (`main()` -> `mcp.run()`, stdio transport)
- **Stores no credentials**: authenticates via the user's existing `vamscli`
  profile (`vamscli setup` + `vamscli auth login`).
- **Reused dependency**: `vamscli.utils.api_client.APIClient` and
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
5. **Return data, not exceptions.** Wrap tool bodies with `@tool_result` so
   failures return `{"error": ..., "error_type": ...}`.
6. **API Gateway URL only.** The server rejects CloudFront URLs implicitly (the
   profile stores the API Gateway URL). Never point at the CloudFront web URL.

---

## Adding a New Tool

1. Confirm the underlying operation exists on `vamscli` `APIClient`
   (`tools/VamsCLI/vamscli/utils/api_client.py`); prefer it.
2. Add an `@mcp.tool()` + `@tool_result` function in `server.py`. Use type hints
   and a clear docstring (both feed the tool's MCP schema/description).
3. Place it in the correct section: read (top), write (`enable_writes`), or
   destructive (`enable_destructive`).
4. For list endpoints, use `CLIENT.paginate(...)`. For search, use
   `CLIENT.trim_search_results(...)`.
5. If it's a safe read tool, add it to `autoApprove` in the sample
   `.kiro/settings/mcp.json` and the README tool list.
6. Add a unit test in `tests/` mocking `CLIENT`.

---

## Testing

```bash
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Tests mock `vams_mcp.server.CLIENT` so no live VAMS deployment is required.

---

## Gold Standard References

- Reused client: `tools/VamsCLI/vamscli/utils/api_client.py`
- Profile/auth: `tools/VamsCLI/vamscli/utils/profile.py`
- Tool patterns: `vams_mcp/server.py`
