# CLAUDE.md - VamsMCP (tools/VamsMCP/)

> Steering document for Claude Code / Kiro when working in the VAMS MCP server.
> Auto-loaded when the working context is within `tools/VamsMCP/`.

---

## Project Overview

VamsMCP is a [Model Context Protocol](https://modelcontextprotocol.io/) server
that exposes the VAMS REST API as agent-callable tools. It is built with the
`mcp` SDK (`FastMCP`) and **reuses the `vamscli` package** for API access and
authentication.

-   **Entry point**: the `vams-mcp` console script is `vams_mcp/cli.py` `main()`: `vams-mcp
setup-remote ...` runs `setup_remote.py`; anything else imports `vams_mcp/server.py` and runs its
    `main()` (`mcp.run()` over stdio, or `http_app.run()` in remote mode).
-   **Stores no credentials**: over stdio it authenticates via the user's existing `vamscli`
    profile (`vamscli setup` + `vamscli auth login`). In remote mode
    (`VAMS_MCP_TRANSPORT=streamable-http`) it reads no profile: each HTTP request carries its user's
    OAuth access token, which the server verifies itself and sends to the VAMS API for that request
    only (Mandatory Rule 16).
-   **Reused dependency**: `vamscli.utils.api_client.APIClient` (in remote mode with its per-call
    `bearer_token`) and `vamscli.utils.profile.ProfileManager` (stdio only).

---

## Architecture

### Directory Structure

> **Maintenance note:** Update this tree when adding modules or tool groups.

```
tools/VamsMCP/
  pyproject.toml             # package + deps (mcp, vamscli, requests)
  .env.example               # reference list of every supported env var (NOT a dotenv template -
                             # nothing loads a .env; set them in the MCP host's `env` block)
  vams_mcp/
    __init__.py              # __version__ (roll with pyproject.toml)
    cli.py                   # the `vams-mcp` console script: `setup-remote` or the server
    config.py                # env-based config (profile, feature gates, pagination, transport, tool
                             # sets, remote host/port/timeout/auth file); ConfigError carries `reason`
                             # (for the agent) and `user_step` (the user's command)
    toolsets.py              # the named tool sets (VAMS_MCP_TOOLSETS) and their limits (Rule 17)
    request_context.py       # remote mode: the request's verified identity and the call's time budget
                             # (context variables; never set over stdio)
    remote_auth.py           # remote mode: RemoteAuthSettings (the auth file) and TokenVerifier
                             # (the server's own token check, before any tool runs)
    http_app.py              # remote mode: RemoteGate (RFC 9728 metadata, the 401 challenge, token
                             # check, per-request identity) in front of the SDK's streamable HTTP app
    setup_remote.py          # `vams-mcp setup-remote`: writes the auth file from /api/amplify-config
    annotations.py           # the MCP ToolAnnotations constant of each gate tier (Mandatory Rule 12)
    results.py               # typed results of the most-used reads -> outputSchema (Rule 13)
    client.py                # VamsClient: wraps vamscli APIClient + ProfileManager; remote mode's
                             # TokenVamsClient (one per request), RequestScopedClient (CLIENT in
                             # remote mode), FeatureSwitchCache, silence_cli_request_log()
    server.py                # FastMCP app + SERVER_INSTRUCTIONS, vams_status, the NotAuthenticated
                             # degraded start (Rule 14), response-shape helpers, tool definitions,
                             # logging guard;
                             # calls register_addon_tools() then register_custom_tools(), once each,
                             # after the destructive block
    addon_tools/             # ADD-ON LANE (upstream-owned): one module per add-on's tools
      __init__.py            # register_addon_tools(): calls register() of each ADDON_TOOL_MODULES entry
      edh.py                 # EDH Workstations tools (see "Add-on Tools")
    custom_tools.py          # FORK EXTENSION POINT: register_custom_tools() (shipped as a no-op)
  evals/
    agent-scenarios.md       # agent-behaviour scenarios + pass criteria, run live before a release
                             # that changes tools, descriptions or instructions (Rule 11)
    check_transcript.py      # applies the tool criteria to a recorded session (offline)
  tests/
    conftest.py              # clears VAMS_ENABLE_* / VAMS_PROFILE / pagination vars at import,
                             # BEFORE vams_mcp.server reads them at module level
    test_config.py           # env parsing + the writes/destructive gate interaction
    test_client_helpers.py   # paginate(), unwrap_message(), trim_search_results(), has_feature(),
                             # require_feature()
    test_server_tools.py     # tool behavior + the source-layout assertions of Mandatory Rule 4
    test_gated_tools.py      # the write/destructive tool bodies, via a module reload with both gates on
    test_custom_tools.py     # the custom-tools hook: called once after the destructive block, and a
                             # probe hook's tools are registered under the server's own gates
    test_addon_tools.py      # the add-on lane: the hook's placement and order, and every
                             # addon_tools module's layout (see "Add-on Tools")
    test_agent_surface.py    # what a client sees: instructions, description budgets, titles,
                             # annotations per gate (core + add-on), outputSchema and structured
                             # content, NotAuthenticated and vams_status, the README autoApprove sample
    test_agent_scenarios.py  # evals/check_transcript.py
    test_toolsets.py         # every named set within 100 tools and 30,000 description characters,
                             # every tool in a set, a selection registers exactly its tools
    test_metadata_and_file_role_tools.py
                             # file / asset-link metadata reads, file metadata, primary file and file
                             # revert writes, asset metadata delete: the requests they send
    remote_support.py        # remote test support: keys, tokens, a fake issuer + VAMS API, uvicorn
    test_remote_http.py      # remote mode end to end over loopback HTTP: 401 + challenge, metadata,
                             # bad tokens, per-user results under concurrency, fail closed, no token
                             # in logs or on disk, DNS rebinding, body limit
    test_remote_auth.py      # TokenVerifier rules (Cognito, external IdP, keys) and the auth file
    test_remote_mode.py      # remote start-up rules, per-request client, CLI log silenced, budget
    test_setup_remote.py     # `vams-mcp setup-remote`
    api_coverage.py          # COVERAGE MANIFEST: every public APIClient method -> the tools that call
                             # it, an exclusion with its reason, or a not-yet-exposed gap (Rule 15)
    test_api_coverage.py     # the manifest against APIClient and the tools' client.api calls
    test_coverage_tools.py   # the file, export, feature-switch, asset-link and tag tools' requests
    test_comment_subscription_tools.py
                             # the comment / subscription / metadata-schema / API-key tools: the three
                             # response shapes they span, and their docstring contracts
    addon/
      test_edh_tools.py      # the EDH tools on a recording fake mcp, one Config per gate setting:
                             # payloads, the pre-flight switch of each tool, the connect tool with
                             # the writes, no desktop sign-in, and the README's EDH section
      test_edh_capacity_reservation_tools.py
                             # the capacity reservation tools and create's capacity_reservation_id:
                             # requests, passthrough (launchAllowed, edhSubnetAvailable and the
                             # fallback reason, from web/src/addons/edh/capacityDisplayCases.json),
                             # the RESERVATION_FULL and RESERVATION_NOT_PERMITTED errors, the README
      test_edh_start_status_tools.py
                             # the asynchronous start: start's 202 record passed through unchanged,
                             # `activity` / `startFailure` passthrough, the descriptions and README
      test_edh_image_tools.py
                             # the image fields (`imageName` ... `imageLatest`) passed through by every
                             # workstation tool, named in one sentence of each description and the README
```

**No dotenv.** `config.py` reads `os.environ` only, and there is no `python-dotenv` dependency. That
is the right mechanism for this transport — a host spawns the `vams-mcp` console script from an
arbitrary working directory, so a cwd-upward `.env` search would not reliably find this one — but it
means `.env.example` is a reference list, not a template to copy. Never write documentation that
tells a reader to copy it: a `.env` beside the server is silently ignored, the gates stay off with
nothing reporting why, and the usual next step is to delete the `if CONFIG.enable_writes:` guard in
source. Document a new variable in `.env.example` **and** in the README's `env` sample.

### Layers

1. `config.py` — reads env vars into a frozen `Config`. No secrets/URLs.
2. `client.py` — `VamsClient` resolves the vamscli profile, discovers the API
   Gateway URL, verifies auth, and builds an `APIClient`. Adds `paginate()`,
   `get_json()/post_json()`, `trim_search_results()`, `has_feature()` and
   `require_feature()`.
3. `server.py` — defines `@mcp.tool()` functions that call `CLIENT`. Reads are
   always registered; writes/destructive tools are registered conditionally, and only the tools of
   the selected tool sets are registered (`_ToolSetServer.tool`, Rule 17).
4. Remote mode only: `remote_auth.py` verifies each request's token, `http_app.py` serves the SDK's
   stateless streamable HTTP app behind `RemoteGate`, and `CLIENT` is a `RequestScopedClient` whose
   attributes come from the current request's `TokenVamsClient` (Rule 16).

`client.py` helpers worth knowing: `paginate()` (follows `NextToken`, normalizes
any list field onto `Items`, flags `truncated`), `unwrap_message()` (strips the
legacy `message` envelope), `trim_search_results()` (compacts an OpenSearch
response to `total` / `total_is_lower_bound` / `total_display` / `returned` /
`results`; `total` stays numeric, and `total_display` is `"N+"` when the API
reports `hits.total.relation == "gte"`), `has_feature()` (the
feature-switch snapshot the profile stored at its last `vamscli auth login`), and
`require_feature()` (raises `FeatureDisabledError` with the given message when that
snapshot lacks the switch: the pre-flight of an add-on tool).

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
    withheld rows. `list_executions` and `list_workflow_executions` both use it to
    carry `warnings` plus the applied `filterStartDate` / `filterEndDate` window.
-   `_bounded_message_list(page, max_items, page_size, noun)` — lifts a **bare
    array** nested under `message` onto `Items`, and flags the bound the route
    applied. Used by the two asset-scoped comment listings (Mandatory Rule 7).

---

## Mandatory Rules

1. **stdout is sacred over stdio.** MCP stdio transport uses stdout for JSON-RPC. Never
   `print()` to stdout and never let logging reach stdout. `server.py` calls
   `_force_logging_to_stderr()` at startup — keep it, and route any new logs to
   stderr. The rule is about the stdio transport; remote mode logs to stderr too, and
   `vams-mcp setup-remote` (which runs no server) prints its summary to stdout.
2. **No credential storage.** Do not add API-key or password env vars that get
   persisted. Auth must come from the vamscli profile over stdio, and from each request's own
   verified token in remote mode (Rule 16). No shared API key or service identity for remote mode,
   no secret the server mints itself. This server is distributed publicly for
   bring-your-own-account use.
3. **Reuse `APIClient` methods.** Prefer existing `vamscli` `APIClient` methods
   over hand-rolled requests so retries/backoff/typed-errors are inherited. Use
   `VamsClient.get_json/post_json` only for endpoints without a dedicated method.
4. **Gate mutations.** New create/update tools go inside the
   `if CONFIG.enable_writes:` block. Destructive tools (delete/archive) go inside
   `if CONFIG.enable_destructive:` and must never be in `autoApprove`.
   `enable_destructive` is AND-ed with `enable_writes` in `Config.from_env()`, so
   `VAMS_ENABLE_DESTRUCTIVE=true` alone registers nothing.
   `execute_workflow`, `rerun_execution` and `abort_execution` are write-tier, not
   destructive — they remove no stored data — but all three act on real AWS
   compute (the first two start it, `abort_execution` irreversibly stops it and
   fans out across a group), so keep all three out of `autoApprove`. The tier is
   about stored data; that list is about compute. Three places state this and must
   agree: this rule, the README's caution paragraph, and the tool's own docstring.
   An add-on tool that starts, resumes, extends the lease of or interrupts billed
   compute stays out of `autoApprove` the same way, and its add-on's subsection of
   "Add-on Tools" below names it.
   The same three-place rule holds for the tools that RETURN a credential:
   `create_api_key` and `create_user_api_key` (write tier) hand back the one-time
   API key value, a bearer token with the acting user's permissions, so both stay out
   of `autoApprove` and their docstrings say where the value ends up. `update_*_api_key`
   with `is_active=False` is the reversible revoke and is what the delete tools point at
   first. An API-key tool takes `is_active` as a bool and sends the model's `"true"` /
   `"false"` string — the request model rejects a raw boolean.

    **A required-true request field is not a confirmation to re-expose.**
    `delete_asset` sends `confirmPermanentDelete=True` because
    `DeleteAssetRequestModel` declares an `always=True` validator that rejects any
    other value — it is part of the request contract, not an optional interlock, so
    surfacing it as a tool parameter would give the agent a boolean whose only
    non-erroring value is `True`. What actually controls these tools is the
    destructive gate (which `Config.from_env()` additionally requires
    `enable_writes` for), the tool name, the docstring, and keeping them out of
    `autoApprove`.
    Because the tools are module-level `def`s, misplacement fails **silently**: a
    duplicate name shadows the earlier definition, and a `def` after the
    `if __name__` entrypoint or outside its gate block never executes, so the tool
    is simply absent with no import error. `tests/test_server_tools.py` asserts the
    source layout (uniqueness, position relative to each gate and the entrypoint,
    and that every `CLIENT.api.*` call resolves on `APIClient`) — keep it passing.
    `tests/test_addon_tools.py` holds the modules of `addon_tools/` to the same layout.

5. **Return data, not exceptions.** Wrap tool bodies with `@tool_result` so
   failures return `{"error": ..., "error_type": ...}` plus the exception's
   `json_fields`, merged without replacing those two, as the CLI's
   `--json-output` carries them: for example `blockedBy` (`waf`, or `waf-or-stage`
   for a bare API Gateway `Forbidden`) and `requestId` of a `RequestBlockedError`,
   a request the deployment's AWS WAF web ACL blocked before VAMS handled it. A new
   machine-readable fact belongs on the CLI exception's `json_fields`, so the CLI
   and every tool carry it alike.
6. **The profile supplies the URL.** The server takes no URL of its own; it reads
   `api_gateway_url` from the `vamscli` profile (in remote mode, `apiUrl` of the auth file that
   `vams-mcp setup-remote` wrote from the same `/api/amplify-config`). `vamscli setup` stores there the
   API Gateway invoke URL that `GET /api/amplify-config` returns, whether it was
   given the web application URL or the API Gateway URL.
7. **Match the endpoint's list field.** `paginate()` normalizes results onto
   `Items`, but it reads the source list from `items_key` — which differs per
   endpoint (`Items` for databases/assets/workflows, `items` for `listFiles`,
   `versions` for `getVersions`, `metadata` for the metadata APIs). It also
   unwraps the legacy `message` envelope used by tags, tag types, workflows, and
   workflow executions. Verify both against the handler's response model before
   adding a list tool; a mismatch silently returns zero items.

    **A `message` envelope can hold a BARE ARRAY, and then neither helper finds the
    rows.** `unwrap_message()` returns the page untouched when `message` is not a
    dict, and `paginate()` reads `items_key` off whatever it gets — so for
    `{"message": [row, ...]}` both hand back zero rows on a successful call, which
    reads as an empty thread rather than a wiring defect. The asset-scoped comment
    listings are that shape; use `_bounded_message_list()`. Three variants now
    exist across the API, so check which one a route returns before choosing a
    helper: a bare array under `message`, an `Items`/`NextToken` page under
    `message` (subscriptions), and no envelope at all (metadata schemas, API keys).

    **A route can accept the pagination parameters and discard the token.** The
    comment listings apply `maxItems`/`pageSize` and return no `NextToken` — the
    service returns `response["Items"]` and drops it — so rows past the bound are
    unreachable through the API. Do not paper over it with a client-side walk, and
    do not offer a `starting_token` that could never be filled. Report the bound
    instead: it is knowable without a token, because the handler takes `maxItems`
    as given, falls back to `pageSize`, and otherwise applies
    `COMMENT_LIST_DEFAULT_BOUND`, so a result that reached the effective bound can
    be flagged even when the caller narrowed nothing.

    **Unwrap the envelope on non-paginated pipeline/workflow/execution tools.**
    Every `APIClient` method in that domain returns the handler's raw
    `{"message": ...}` body, because `_pwe_body()` leaves the envelope intact and
    lets callers decide. The asset and database methods return unwrapped data, so
    a tool that forwards the envelope hands agents a nesting level no other tool
    has. Wrap single-object and write calls in `CLIENT.unwrap_message(...)`;
    `paginate()` already does it.

    **Repeat a filter-pinned endpoint's filters on every page.** Some continuation
    tokens are only valid alongside the filters they were issued with — the paged
    execution-detail metadata read (`page_execution_detail_metadata`) and the paged
    execution-detail FILES read (`page_execution_detail_files`) pin their token to
    the `collection` and `pipelineId` of the request that produced it, and the
    handler answers a mismatch with a 400. `paginate()` passes only `pageSize`
    and `startingToken`, so merge the filters into the params inside the
    `fetch_page` callable rather than sending them on the first request alone.
    (`get_execution_results` pages by `startingToken` alone — no filters — and
    checks its 5 MiB budget, counted as sent, before adding each page, so one
    answer never exceeds it; it returns `NextToken` + `note` when it stops.)

8. **Never let a bounded response read as a complete one.** A VAMS handler can
   answer successfully while withholding rows, and it reports that out of band: a
   top-level `warnings` array (a page that hit its distinct-asset permission-check
   cap, or spent its per-request work budget before filling the page — the array
   can carry more than one entry, so collect them all rather than the first), a
   `truncatedCollections` list (a bounded execution-detail collection), or an
   echoed filter window (`filterStartDate` on the executions list). Because
   `paginate()` rebuilds its result from the accumulated items alone, every one of
   those is dropped by default, and the agent reports an understated count or
   concludes an object does not exist.

    Use `_paginate_with_page_metadata()` instead of `CLIENT.paginate()` for any
    list endpoint that can report a `warnings` array or echo its applied filters,
    and `_unwrap_message_with_warnings()` for any single-call endpoint that returns
    `warnings` as a sibling of `message`. Then say in the docstring what the flag
    means for the agent's conclusion, not just that the field exists — a tool
    description is the only place an agent learns not to trust a short list.

    **`paginate()`'s own bound is one of those, and every paginated tool docstring
    must state it.** The walk stops at `max_items` or at `max_pages`, whichever
    comes first, and sets `truncated` plus a `note` naming which fired — including
    the case where a single page returned more rows than `max_items` and carried no
    token at all, which a token-only check misses. It returns the outstanding
    `NextToken`, and every paginated read tool takes a `starting_token` that
    forwards it as the first page's `startingToken`; without that pair the ceiling
    is a wall and rows past it are unreachable through this server. `max_pages` is
    a per-call work bound on the server process, so a larger `max_items`
    deliberately does NOT raise it — the resumption token is the way past it.

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

    **The converse is equally a defect: never expose a parameter the endpoint does
    not act on.** A knob that silently does nothing is worse than its absence,
    because the agent draws a conclusion from having set it. Two shapes to refuse:
    a parameter the handler forwards and the service ignores (`showDeleted` on the
    asset-scoped comment routes), and one whose only valid value can never be
    obtained (`starting_token` on a route that returns no token). Say in the
    docstring that the capability is absent, so the agent stops looking for it —
    and pin the absence with a test, or the next reader adds it back as a fix.

10. **Support both `mcp` major versions.** `mcp` 1.x exposes `FastMCP` from
    `mcp.server.fastmcp`; `mcp` 2.x renamed it to `MCPServer` in
    `mcp.server.mcpserver` and removed the old module. `server.py` imports it
    through a `try`/`except ImportError` alias (`McpServer`) — keep that shim and
    the `mcp>=1.10.0,<3.0.0` pin, and test against both before widening it (CI installs the
    newest release the pin allows; run the newest 1.x and the floor yourself,
    `pip install "mcp==1.10.0" "pydantic<2.12"`, when you
    touch `@mcp.tool(...)` arguments or return types). 1.10.0 is the oldest release with tool
    titles and structured output; 1.9.x cannot register these tools. mcp 1.10.0 to 1.21.1 fail
    at import with pydantic 2.12 or later (`eval_type_backport` was removed from a pydantic
    internal they use), so those releases need `pydantic<2.12`; 1.21.2+ and 2.x do not. mcp 2.x spells `ToolAnnotations` fields in snake_case in Python
    (`read_only_hint`) and camelCase on the wire; a test reads a tool through
    `tool.model_dump(by_alias=True)` so it holds on both.

11. **Descriptions are written for the agent, within a budget.** A tool's docstring is its MCP
    description: every connected agent reads every description at the start of every session.
    Line 1 says when to use the tool (core tools start `Use ...`; `vams_status` starts `Check
...`), then the parameters that matter, then what the result means for a conclusion (bounds,
    truncation, credential and compute warnings — Rules 4 and 8 still require those). Handler
    quirks, why a helper exists and other maintainer rationale go in code comments or this file,
    never in the docstring. Each description stays within 1,200 characters, except the short named
    list in `tests/test_agent_surface.py` (`DESCRIPTION_EXCEPTIONS`, each with its reason and
    ceiling), and each tool set's descriptions together within 30,000 (`toolsets.MAX_DESCRIPTION_CHARS_PER_SET`,
    Rule 17; the full core surface, which only `all` over stdio lists, within 32,000). Before adding text,
    cut text. Many older
    contract tests pin phrases in docstrings; keep the phrase, shorten around it. A release that
    changes tools, descriptions or `SERVER_INSTRUCTIONS` runs the scenarios of
    `evals/agent-scenarios.md` against a test deployment in Kiro and Claude Code.

12. **Every tool carries a title and the annotation of its gate.** Decorate with
    `@mcp.tool(title="...", annotations=<CONST>)` then `@tool_result`, using the constants in
    `vams_mcp/annotations.py`: `READ` in the read block, `WRITE` (or `COMPUTE`) under
    `enable_writes`, `DESTRUCTIVE` (or `DESTRUCTIVE_COMPUTE`) under `enable_destructive`. The
    `*_COMPUTE` constants (`openWorldHint: true`) are for tools that start, resume, extend, stop or
    terminate billed compute — the same tools Rule 4 keeps out of `autoApprove`. The tier decides,
    not what the tool seems to do: `get_edh_workstation_connect` changes nothing but sits in the
    write tier, so it is `WRITE`. `tests/test_agent_surface.py` checks every core and add-on tool
    as a client lists it; `tests/test_addon_tools.py` checks an add-on module's source.

13. **A stable read shape is typed.** A read tool whose response shape is stable returns a model
    from `vams_mcp/results.py` (`-> results.AssetPage`), so the SDK publishes an `outputSchema` and
    returns structured content. The model only describes the dict the tool already returns:
    `extra="allow"`, every field optional, loose types, and `ToolResult.model_dump` dumps only the
    keys that were set, so the structured copy equals the text JSON (pinned for success and error
    results). A new list tool usually subclasses `Page`. Do not type a tool whose body passes an
    unmodelled handler response through unless every field you name is certain.

14. **Errors never tell the agent to run a CLI, and the server starts without a sign-in.** An
    agent told "run `vamscli auth login`" goes to the shell and stays there. Without a usable
    profile `CLIENT` is `None`, the server still starts, and `@tool_result` returns
    `{"error", "error_type": "NotAuthenticated", "user_action"}` for every tool except those in
    `_WORKS_WITHOUT_SIGN_IN` (`vams_status`). `error` states the fact and asks the agent to have the
    user sign in; `user_action` names the command as the USER's step. `ConfigError` carries the
    two parts (`reason`, `user_step`), and the CLI's sign-in exceptions (`AuthenticationError`,
    `TokenExpiredError`, `OverrideTokenError`) raised mid-session map to the same error. Each call
    retries building the client, so a later sign-in needs no restart (add-on tools do). Keep
    `SERVER_INSTRUCTIONS` (the MCP `instructions`, at most 2,000 characters) current when a tool
    family is added or a start point changes; a test checks that every tool it names exists. The
    instructions also say what has no tool (uploads, sync, user, role and constraint
    administration) so the agent tells the user instead of looking for a way round.
15. **Every public `APIClient` method has an MCP decision, in the coverage manifest.**
    `tests/api_coverage.py` lists each public method of the CLI's `APIClient`, its add-on mixins
    and its fork mixin in exactly one table: `TOOLS` (method -> the tools that call it, core and
    add-on), `EXCLUDED` (no tool by decision, with a one-line reason: HTTP verb helpers, setup and
    sign-in, which the USER does, upload, user/role/constraint administration, the EDH desktop
    sign-in) or `NOT_YET_EXPOSED` (an agent-usable operation without a tool yet, recorded so the
    gap stays visible). `tests/test_api_coverage.py` fails on a public method in no table, an
    entry for a method that no longer exists, a method in two tables, a `TOOLS` tool that is not
    registered with every gate and add-on on, and any difference between `TOOLS` and the
    `client.api.<method>` calls the tools make. So a new CLI method fails CI until its MCP side is
    decided, and a new tool fails until the manifest names what it calls. A fork's own
    `CustomAPIClientMixin` method counts as covered when `custom_tools.py` calls it; a fork that
    keeps one CLI-only lists it in `EXCLUDED`. Upload and administration stay CLI-only unless
    planning decides otherwise: moving one to `TOOLS` changes the server instructions too.
16. **Remote mode: per-request identity, verified first, never logged.** With
    `VAMS_MCP_TRANSPORT=streamable-http` the server serves many users from one process, so:
    - **The server verifies the token before any tool or resource runs.** `http_app.RemoteGate`
      checks every request except the RFC 9728 metadata routes with `remote_auth.TokenVerifier`
      (RS256 against the issuer's JWKS, `iss`, an audience in the auth file, `exp`, `token_use`, a
      named user, the machine-token rules of the API authorizer). No token or a bad one: `401` with
      `WWW-Authenticate: Bearer resource_metadata="..."`. Keys that cannot be read: `503`, nothing
      passes. Detecting a header is not verifying it; never add a path around the gate, and never a
      test bypass (the tests sign real tokens with keys a loopback fake issuer serves).
    - **Per-request identity only.** `CLIENT` is a `RequestScopedClient`; every attribute a tool
      reads comes from the request's own `TokenVamsClient` (`APIClient(bearer_token=...)`), so the
      VAMS API's authorizer and Casbin decide each call for that user. A tool never decides a
      permission itself, never reads a profile, and keeps nothing between calls: no module-level
      state written per request (stdio's `_SIGN_IN_FAILURE` is not touched in remote mode), and the
      only shared cache is the deployment's feature switches (`FeatureSwitchCache`), which are not
      per user. Mixing remote mode with `VAMS_PROFILE` is refused at start.
    - **No token in logs, errors, metrics or results.** `VerifiedIdentity` hides its token from
      `repr`; remote tool errors pass through `vamscli`'s `scrub_text`; the CLI's shared request log
      is silenced (`silence_cli_request_log`). Log the user, client, tool, outcome and duration
      (the `vams_mcp.remote` logger), never a header, body or query string.
    - **Loopback by default.** The listener binds to `127.0.0.1` unless `VAMS_MCP_HOST` says
      otherwise, and a wider bind needs an `https` resource URL (`http_app.check_exposure`). No
      example publishes the port on all interfaces.
    - **Remote tool calls run in a worker thread within a time budget** (`VAMS_MCP_TOOL_TIMEOUT`,
      `request_context.time_budget`). A loop that issues several API requests checks
      `request_context.budget_low()` before the next one and stops with a resumable answer, as
      `paginate()`, `get_execution_results` and `find_and_summarize` do; a call past its budget is
      answered `ToolTimeout`. Over stdio no budget applies. The worker thread of a call answered at its
      budget runs on, so a non-read tool's timeout never invites a retry: it says the outcome is
      unknown (`outcomeUnknown`) and names the read tool that checks it (`checkWith`, from
      `server._OUTCOME_CHECKS`); a new non-read tool must resolve to a registered read tool there
      (`tests/test_remote_mode.py`).
    - **One user cannot starve the others.** Tool bodies run on their own thread limiter, token
      verification on another (`http_app._verify_limiter`), and the verifier never holds its cache lock
      across a key-set download. A user may have at most `VAMS_MCP_MAX_CALLS_PER_USER` (8) calls in
      progress, counted until each worker thread really ends; a call past it is answered
      `TooManyConcurrentCalls` without running.
    - **The request body is read whole in the gate** (at most 1 MiB, declared or chunked; `413`
      otherwise) before the SDK sees the request.
    - The gate is ours, not the SDK's auth middleware, because the SDK's resource-server wiring
      differs across the supported `mcp` range (1.10.x serves the metadata at a path its own
      challenge does not name). The SDK still carries the protocol and its DNS rebinding check,
      which `transport_security_settings` configures for the resource's host and loopback only.
17. **Every tool is in a tool set, and every set fits a remote client.** `toolsets.py` names the sets
    (`consumer`, `operator`, `admin`, `edh`, `edh-admin`; plus `custom` for a fork's tools and `all`).
    `_ToolSetServer.tool` registers only the selected sets' tools, under the gates as before. A new
    core or add-on tool goes into at least one set (`consumer` takes reads only, never a write), and
    each set stays within `MAX_TOOLS_PER_SET` (100, the most Amazon Quick lists per connection) and
    `MAX_DESCRIPTION_CHARS_PER_SET` (30,000) with every gate and add-on on; `tests/test_toolsets.py`
    fails otherwise. When a set would pass a limit, split it rather than raise the limit. The stdio
    default is `all` (every tool, as before tool sets); the remote default is `consumer`.

---

## Adding a New Tool

1. Confirm the underlying operation exists on `vamscli` `APIClient`
   (`tools/VamsCLI/vamscli/utils/api_client.py`); prefer it. Read the method's
   real signature — several take required positional arguments the endpoint
   demands (for example `execute_workflow` requires `workflow_database_id`).
2. Add an `@mcp.tool(title="...", annotations=<CONST>)` + `@tool_result` function in
   `server.py`. Use type hints and an agent-facing docstring (both feed the tool's MCP
   schema/description): when to use it, the parameters that matter, what the result means, within
   the budget of Mandatory Rule 11. Type the return with a `results.*` model when the shape is
   stable (Rule 13).
3. Place it in the correct section: read (top), write (`enable_writes`), or
   destructive (`enable_destructive`), and give it that tier's annotation constant (Rule 12).
   Name a new tool family or start point in `SERVER_INSTRUCTIONS` (Rule 14).
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
   Then record the method in `tests/api_coverage.py` (Mandatory Rule 15): move it from
   `NOT_YET_EXPOSED` to `TOOLS` with the new tool's name, or add it there. Write and
   destructive tools also go in the `WRITE_TOOLS` / `DESTRUCTIVE_TOOLS` tuples of
   `tests/test_gated_tools.py`, and a safe read in the README's `autoApprove` sample and
   the docs-site MCP page's auto-approval list. Place the tool in a tool set in
   `vams_mcp/toolsets.py` (Mandatory Rule 17).
9. When the server's contract with the CLI changes, roll the version in **both**
   `pyproject.toml` and `vams_mcp/__init__.py`, alongside
   `tools/VamsCLI/vamscli/version.py` (root `CLAUDE.md` Pattern 7 rule 6).

### Adding Tools in a Fork

A fork adds tools in `vams_mcp/custom_tools.py`, never in `server.py`. Upstream ships
`register_custom_tools(mcp, client, config, tool_result)` as a no-op and never edits
it; `server.py` calls it exactly once, at module level after the destructive block
and before `def main()` (`tests/test_custom_tools.py`). Inside it, define each tool
with `@mcp.tool(title=..., annotations=...)` then `@tool_result` (the constants of
`vams_mcp/annotations.py`, Rule 12), put write tools under
`if config.enable_writes:` and destructive tools under `if config.enable_destructive:`,
and call `client.api.<method>` — a method added to the CLI's `CustomAPIClientMixin`
(`tools/VamsCLI/vamscli/utils/custom_api_client.py`) is an ordinary `APIClient`
method here. Steps 4–8 above apply, except that the README entry goes in the fork's
own documentation (the README tool-list test covers the upstream tools only). Tool
names prefixed `custom_` are recommended, not enforced. A custom tool with the name of
a core or add-on tool is not registered: the MCP SDK keeps the first registration (the
core or add-on tool) and logs `Tool already exists: <name>` (pinned by
`tests/test_custom_tools.py` and `tests/test_addon_tools.py`). A fork tests its tools by
calling `register_custom_tools` with a fake `mcp` and client, or with the reload
pattern of `tests/test_custom_tools.py`.

### Add-on Tools

An add-on's tools use an add-on lane that mirrors the fork lane above. Upstream edits it when it
ships an add-on with tools; a fork does not.

-   Each add-on keeps its tools in one module of `vams_mcp/addon_tools/`, listed in
    `ADDON_TOOL_MODULES` (`addon_tools/__init__.py`). The module's
    `register(mcp, client, config, tool_result)` defines each tool with
    `@mcp.tool(title="...", annotations=<CONST>)` then `@tool_result` (Rule 12; `from ..annotations
import ...`): the reads directly in its body, write tools under `if config.enable_writes:`
    and destructive tools under `if config.enable_destructive:`. It calls `client.api.<method>`,
    whose add-on methods live in the CLI's add-on mixin
    (`tools/VamsCLI/vamscli/utils/addon/<addon>_api_client.py`).
-   `server.py` calls `register_addon_tools(mcp, CLIENT, CONFIG, tool_result)` once, at module
    level after the destructive block and before `register_custom_tools(...)`. The MCP SDK keeps
    the first registration of a name, so a core tool wins over an add-on tool and an add-on tool
    over a custom one.
-   **No add-on tool name appears in core server code**, lookup tables included (`infra/test/addon/addonCoreSeparation.test.ts`). An add-on module declares what the core needs to know about its tools, such as `OUTCOME_CHECKS` (the read tool that checks a timed-out non-read call), and `addon_tools/__init__.py` merges it in (`addon_outcome_checks()`).
-   **An add-on's tools exist only on a deployment with the add-on.** Each module declares its
    add-on's switch in `FEATURE` (a `FEATURE_*` of `vamscli.addon_constants`), and
    `register_addon_tools` registers the module only when `addon_deployed(client, module)`: the
    profile's feature-switch snapshot, read at server start, carries it (no client registers
    nothing). Enabling the add-on later takes `vamscli auth login` and a server restart; the README
    says so. Tests that look at a server's add-on tools reload it through the `server_with_switches`
    fixture (`tests/conftest.py`), never over the developer's own profile
    (`tests/test_addon_tool_registration.py`).
-   **A feature-gated add-on tool opens with a pre-flight.** Its first statement is
    `client.require_feature(<FEATURE_*>, <*_DISABLED_MESSAGE>)`, both names from
    `vamscli.addon_constants`: the MCP counterpart of the CLI's `@requires_feature`. It reads the
    profile's login-time feature-switch snapshot and raises `FeatureDisabledError` with the CLI's
    own sentence, which `@tool_result` returns as data. Without it, a deployment that never
    created the add-on's routes answers API Gateway's 403, which the client reports only as a
    request no deployed route matches, naming no feature switch.
-   The README lists an add-on's tools in its subsection of "Add-on tools", with the safe reads in
    that subsection's `autoApprove` addition, never in the core sample. Steps 4–9 of "Adding a New
    Tool" apply. Its tests go under `tests/addon/` and register the module on a recording fake
    `mcp`, with a mocked client and one `Config(...)` per gate setting.
-   `tests/test_addon_tools.py` checks the hook's placement and, for every module: each tool sits
    directly in `register` or directly under one of its two gates, with the decorator order and
    the pre-flight as its first statement; the read section calls no mutating `client.api` method,
    raw verb or `client.post_json`; every `client.api.<method>` exists on `APIClient`; no tool
    takes a name a core tool or another add-on uses; and the module is listed in
    `ADDON_TOOL_MODULES`.

#### EDH Workstations

-   `addon_tools/edh.py` holds 18 read, 17 write and 8 destructive tools. The container tools
    pre-flight `FEATURE_EDH_CONTAINERS`, the AMI recipe tools `FEATURE_EDH_AMI_RECIPES`, and the
    rest `FEATURE_EDH_WORKSTATIONS`. The add-on's list routes return every row in one
    `{"Items": [...]}` page and no `message` envelope, so the tools return the body as it comes,
    without `paginate()` or `unwrap_message()`. `EdhOutcomeUnknownError`,
    `EdhStartInProgressError`, `EdhReservationFullError`, `EdhReservationNotPermittedError` and
    `EdhDisplaySessionEndedError` carry `outcomeUnknown` / `startInProgress` / `code`
    (`RESERVATION_FULL`, `RESERVATION_NOT_PERMITTED`, `DISPLAY_SESSION_ENDED`) in their `json_fields`, which `@tool_result` passes through
    (Mandatory Rule 5).
-   Capacity reservations: the catalog items carry `launchAllowed` (false: the role may see the
    reservation but not launch into it; create answers 403 `RESERVATION_NOT_PERMITTED`) and both
    listings `edhSubnetAvailable` (false only on the admin list: the user catalog leaves such a
    reservation out); workstation records carry `capacityReservationFallbackReason` (`full` |
    `ended`). The tools pass them through, and the catalog, admin list, create, list and start
    descriptions name them.
-   Start status: `start_edh_workstation` returns the record the route accepts the start with
    (HTTP 202, still STOPPED with `activity` `starting`) and does not poll; the agent follows it
    with `list_edh_workstations`. Every workstation record and admin instance item carries
    `activity` (`starting` | `stopping` | null) and `startFailure` (why the last start left a
    STOPPED row stopped, beside `lastError` `CAPACITY_UNAVAILABLE`, `RESERVATION_FULL`,
    `RESERVATION_ENDED` or `START_FAILED`); the tools pass both through, and the workstation
    tools' descriptions name them.
    A CREATING row with `activity` null and `lastError` `START_STALLED` is a start EDH accepted and
    has not finished after 30 minutes; the list, get, admin-instances, start and stop
    descriptions name the code, and stop's says it accepts that row.
-   Image name: every workstation record and admin instance item also carries `imageName`,
    `recipeId`, `recipeDisplayName`, `recipeVersion`, `latestRecipeVersion` and `imageLatest`
    (`false` only when a newer AVAILABLE build of the recipe exists; each null when not known). The
    tools pass them through, and each workstation tool's description (list, get, admin instances,
    create, start, stop, renew, delete) names them in one sentence.
-   Capacity reservations: `list_edh_catalog_capacity_reservations` (read, in the README's
    `autoApprove` addition) and `list_edh_admin_capacity_reservations` (read, admin),
    `register_edh_capacity_reservation` and `update_edh_capacity_reservation` (write) and
    `unregister_edh_capacity_reservation` (destructive) change VAMS's registry only, never EC2;
    `create_edh_workstation` takes an optional `capacity_reservation_id`, sent only when given.
-   Compute: `create_edh_workstation`, `renew_edh_workstation`, `start_edh_workstation`,
    `stop_edh_workstation` and `stop_edh_admin_instance` (write tier) start, resume, extend the
    lease of, or interrupt a GPU desktop, `submit_edh_container_job` (write tier) starts a
    Batch/EKS job, `start_edh_ami_recipe_build` (write tier) starts an EC2 Image Builder build
    instance, and `deactivate_edh_admin_instance` and `kill_edh_container_job` (destructive tier)
    terminate one — all nine stay out of `autoApprove`.
-   `get_edh_workstation_connect` changes no stored data but issues a DCV `connectionUrl` carrying
    a session auth token — the same class as the presigned-URL tools — so it is the one read
    placed in the write tier (behind `VAMS_ENABLE_WRITES`, not always available), it never enters
    `autoApprove`, and its docstring and the README security note both say so.
-   No tool calls `get_edh_workstation_os_sign_in`: the desktop sign-in, a Windows desktop's password
    included, is shown to its owner in the web app and behind the CLI's
    `vamscli edh workstation connect --show-sign-in` only, never returned to an agent. The workstation
    tools reach the CLI methods that refuse an id which is not one path segment (`_edh_session_id`),
    and beneath every client method
    `_make_request` refuses a request path with an empty, `.` or `..` segment (raw or
    percent-encoded), so an id cannot address the sign-in route either.
    `tests/addon/test_edh_tools.py` holds the tools to the absent call and to `_edh_session_id`;
    `tools/VamsCLI/tests/test_api_request_path.py` holds the `_make_request` check.

## Upstream Dependency on the CLI

This server is **downstream of `tools/VamsCLI`** — it calls `APIClient` methods
directly rather than the REST API. Any change to a `vamscli` `APIClient` method
signature or response handling can break a tool here without an error at import
time. Root `CLAUDE.md` Pattern 7 defines the propagation chain; when a CLI change
reveals a missing or wrong `APIClient` method, fix it in the CLI instead of
hand-rolling raw requests here. A new `APIClient` method also needs its entry in
`tests/api_coverage.py` (Mandatory Rule 15), or this suite fails.

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

Tests mock `vams_mcp.server.CLIENT`, or pass a mocked client to an add-on module's
`register`, so no live VAMS deployment is required.

The suite is hermetic: `tests/conftest.py` points `HOME` at an empty directory before the server is
imported, so the developer's own vamscli profile is never read and the server always starts signed
out, as in CI. Its autouse `signed_in_by_default` fixture then puts a mocked `CLIENT` in place for
every test; a test of the signed-out path requests `signed_out` or `no_client` instead.
`test_the_suite_runs_without_the_hosts_vamscli_profile` fails if that isolation is removed. CI runs
Python 3.12, which keeps docstring indentation that 3.13 strips; `tool_result` cleans each
docstring, so descriptions and their budgets are the same on both.

CI runs this suite in the `vamsmcp-test` job of `.github/workflows/ci.yml`, on Python 3.12 in a job of
its own (the same two installs, then `pytest -q --tb=short`), so a broken tool fails the pull request.

The remote-mode tests (`tests/test_remote_*.py`, `tests/test_setup_remote.py`) start real HTTP servers
on `127.0.0.1` only: a fake issuer and VAMS API (`tests/remote_support.py`) and the remote server under
uvicorn. They sign tokens with RSA keys generated per run, so no deployment or network is needed. They
reload `vams_mcp.server` in remote mode and back, and restore the CLI's logger afterwards. Run them on
the `mcp` floor (1.10.0), the newest 1.x and 2.x when touching `http_app.py`: the SDK's streamable HTTP
app differs across that range (for example 1.10.x mounts its handler at `/mcp/`).

### Mark a Temporary Test with `@pytest.mark.temporary`

A test written to prove one specific change landed — a removed tool, a renamed call site — carries
`@pytest.mark.temporary` (registered in `pyproject.toml`) plus a line saying what it pins, so release
cleanup can find it with `pytest -m temporary --collect-only`.

Do **not** mark the source-layout guards in `tests/test_server_tools.py` and `tests/test_addon_tools.py`.
They are durable: a duplicate `def` silently shadows an earlier tool, a `def` placed outside its gate
block never executes, and a read tool calling a mutating `APIClient` method is a permission defect — each
stays writable, so each guard can still fire. Full criterion: root `CLAUDE.md` Rule 13.

---

## Gold Standard References

-   Reused client: `tools/VamsCLI/vamscli/utils/api_client.py`
-   Profile/auth: `tools/VamsCLI/vamscli/utils/profile.py`
-   Tool patterns: `vams_mcp/server.py`
