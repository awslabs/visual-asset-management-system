# CLAUDE.md - VamsCLI (tools/VamsCLI/)

> Steering document for Claude Code when working in the VamsCLI Python CLI tool.
> Auto-loaded when the working context is within `tools/VamsCLI/`.

---

## Project Overview

VamsCLI is a Python command-line interface built with the **Click** framework for interacting with the Visual Asset Management System (VAMS) deployed on AWS. It provides authentication, configuration management, multi-profile support, and full CRUD operations against the VAMS API Gateway.

-   **Entry point**: `vamscli/main.py`
-   **Version**: Defined in `vamscli/version.py` (`__version__` and `CLI_VERSION`)
-   **Minimum API version**: `2.2` (constant `MINIMUM_API_VERSION` in `constants.py`)

---

## Architecture

### Directory Structure

> **Maintenance note:** Update this tree when adding new command groups, utility modules, or auth providers. See root `CLAUDE.md` Rule 11.

```
tools/VamsCLI/
  vamscli/
    main.py                  # CLI entry point, @click.group, command registration (+ ADDON_COMMANDS, CUSTOM_COMMANDS)
    version.py               # __version__ and CLI_VERSION
    constants.py             # All API endpoints, limits, config constants (re-exports addon_constants)
    addon_constants.py       # Add-on API_* paths, FEATURE_* names (mirror infra/common/addonAppFeatures.ts) and shared messages
    custom_constants.py      # FORK EXTENSION POINT: a fork's API_* paths + FEATURE_CUSTOM_* (shipped empty)
    auth/
      base.py                # BaseAuthenticator abstract class
      cognito.py             # CognitoAuthenticator (SRP, USER_PASSWORD_AUTH)
    commands/
      setup.py               # Initial CLI configuration
      auth.py                # Login, change-password, forgot-password, logout, status, refresh, set-override, routes (API route listing)
      apiKey.py              # API key management (admin: list/get/create/update/delete) + 'user' sub-group (self-service own keys)
      assets.py              # Asset CRUD operations + lifecycle history lookup (history)
      assetsExport.py        # 'assets export' sub-command, registered on the assets group in assets.py
      asset_version.py       # Asset version management (list, get, create, update, archive, unarchive, revert)
      asset_links.py         # Asset relationship/link management
      file.py                # File management (upload, download, move, copy)
      profile.py             # Multi-profile management
      database.py            # Database CRUD operations
      tag.py                 # Tag management
      tag_type.py            # Tag type management
      metadata.py            # Metadata operations (unified API)
      metadata_schema.py     # Metadata schema management (list, get, create, update, delete)
      comment.py             # Asset version comments (list, get, add, update, delete)
      subscription.py        # Asset event subscriptions (list, create, update, delete, unsubscribe, check)
      features.py            # Feature switch inspection
      search.py              # Search (OpenSearch integration)
      sync.py                # Directory sync (sync file push/pull)
      pipeline.py            # Pipeline CRUD + template + tag-schema sub-groups
      workflow.py            # Workflow CRUD + trigger sub-group + asset-less execute + per-asset execution list
      execution.py           # Execution ops: list (global), details, details-metadata (paged), logs, abort, rerun, permanent-delete
      user.py                # Cognito user management
      roleUserConstraints.py # Roles, constraints, user-role assignment
      addon/
        __init__.py          # Add-on lane: ADDON_COMMANDS, registered by main.py after the core commands
        edh.py               # EDH Workstations add-on: workstation, catalog, admin (images, ami-recipe, instances, settings, capacity-reservation), container (job) sub-groups
      custom/__init__.py     # FORK EXTENSION POINT: CUSTOM_COMMANDS, registered on the root group by main.py
      industry/
        industry.py          # Industry command group
        engineering/
          engineering.py     # Engineering sub-commands
          bom/               # Bill of Materials (Dynamic_BOM.py)
          plm/               # Product Lifecycle Management (plm.py)
        spatial/
          glb.py             # GLB file combination operations
    utils/
      api_client.py          # APIClient class (HTTP, retries, error mapping); inherits AddonAPIClientMixin, CustomAPIClientMixin
      addon_api_client.py    # Add-on lane: AddonAPIClientMixin, aggregating one mixin per add-on
      addon/
        edh_api_client.py    # EdhAPIClientMixin (EDH Workstations add-on methods)
        edh_exceptions.py    # EdhError family
      custom_api_client.py   # FORK EXTENSION POINT: CustomAPIClientMixin (shipped empty)
      profile.py             # ProfileManager (multi-profile, config dirs)
      exceptions.py          # Two-tier exception hierarchy (~140 classes)
      global_exceptions.py   # @handle_global_exceptions() decorator
      decorators.py          # @requires_setup_and_auth, @requires_feature
      json_output.py         # output_result(), output_error(), output_status()
      logging.py             # Rotating file logger, verbose mode
      retry_config.py        # Retry settings with env var overrides
      features.py            # Feature switch utilities
      upload_manager.py      # Multi-part upload orchestration
      download_manager.py    # Parallel download orchestration (atomic writes, size verify, mtime preservation)
      file_processor.py      # File validation and processing
      sync_engine.py         # Sync plan computation (local/remote diff)
      vamsignore.py          # .vamsignore gitignore-style pattern matching
      glb_combiner.py        # GLB binary file combination
  tests/
    conftest.py              # Shared fixtures (mock_logging, cli_runner, generic_command_mocks)
    test_*.py                # One file per command group or behavior area
    industry/                # Engineering BOM and PLM command tests (engineering/bom/, engineering/plm/)
    addon/                   # Add-on command and client tests (test_edh.py, test_edh_client.py, test_edh_capacity_reservations.py, test_edh_start_status.py)
```

### Command Groups (24 core + add-on groups)

The core groups are registered in `main.py` via `cli.add_command()`:

```
setup, auth, assets, asset-version, asset-links, file, profile, database,
tag, tag-type, metadata, metadata-schema, comment, subscription, features,
search, sync, workflow, pipeline, execution, industry, user, role, api-key
```

The add-on groups are registered from `commands/addon/` (`ADDON_COMMANDS`): `edh` (EDH Workstations). See [Add-on Commands](#add-on-commands).

Sync has a nested sub-command group:

-   `sync file push` / `sync file pull` -- directory synchronization with an asset (S3-sync-style size+mtime diff, `.vamsignore` support, archive/permanent-delete safeguards)

Pipeline / workflow / execution cover the overhauled pipeline/workflow/execution APIs:

-   `pipeline create|get|list|update|delete|unarchive`, `pipeline template create|get|list|update|delete`, `pipeline tag-schema get|set`
-   `workflow create|get|list|update|delete|unarchive`, `workflow trigger list|get|set|delete`, `workflow execute` (asset-less multi-file), `workflow list-executions` (per-asset history)
-   `execution list` (global, permission-filtered, filterable), `execution details`, `execution details-metadata` (pages one metadata collection of the detail view past the bound `details` applies), `execution logs`, `execution abort` (single or `--group-id`; `--json-output` carries every top-level sibling of the handler's `message` — `warnings`, `notAbortable` — through `_payload_with_warnings`, so a field added upstream reaches scripts, the MCP server and the connectors without a CLI change), `execution rerun`, `execution permanent-delete`

Comment and subscription cover the comments and subscriptions APIs:

-   `comment list|get|add|update|delete` -- `list` takes `-v/--asset-version-id` to switch from the asset-wide route to the version-scoped one; `get`, `add`, `update` and `delete` address a comment by asset, asset version and comment ID
-   `subscription list|create|update|delete|unsubscribe|check` -- `delete` removes the whole subscription (and, for an asset, its notification topic); `unsubscribe` removes one subscriber and is a different route

Industry has nested sub-command groups:

-   `industry engineering bom <command>`
-   `industry engineering plm <command>`
-   `industry spatial glb <command>`

---

## Critical Rules

### 1. Exception Hierarchy - Two Tiers

The exception system in `utils/exceptions.py` has a strict two-tier design:

```
VamsCLIError (base)
  GlobalInfrastructureError    --> handled by @handle_global_exceptions() in main.py
    SetupRequiredError
    AuthenticationError
    APIUnavailableError
    ProfileError
    InvalidProfileNameError
    ConfigurationError
    OverrideTokenError
    TokenExpiredError
    PermissionDeniedError
    RequestBlockedError
    VersionMismatchError
    RetryExhaustedError
    RateLimitExceededError
  BusinessLogicError           --> handled by individual commands
    APIError (+ InvalidRequestPathError)
    AssetError (+ 5 subclasses)
    DatabaseError (+ 5 subclasses)
    FileError (+ 14 subclasses)
    SyncError (+ 5 subclasses)
    TagError (+ 7 subclasses)
    AssetVersionError (+ 5 subclasses, includes AssetVersionArchiveError)
    AssetLinkError (+ 7 subclasses)
    CommentError (+ 2 subclasses)
    SubscriptionError (+ 3 subclasses)
    MetadataSchemaError (+ 3 subclasses)
    SearchError (+ 5 subclasses)
    WorkflowError (+ 4 subclasses)
    CognitoUserError (+ 4 subclasses)
    RoleError (+ 4 subclasses)
    ConstraintError (+ 5 subclasses)
    UserRoleError (+ 4 subclasses)
    EdhError (+ 12 subclasses; add-on, in utils/addon/edh_exceptions.py)
    ProfileAlreadyExistsError
```

**Rules**:

1. Global infrastructure exceptions are **never** caught in commands -- they propagate to the global handler
2. Business logic exceptions are **always** caught and handled within the command that raises them
3. New exception classes must inherit from the correct tier
4. Every domain area has a base class (e.g., `AssetError`) and specific subclasses

### 2. Command Structure Pattern

Every command follows this exact pattern (full skeleton in [Templates](#templates)):

```python
@domain.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, json_output: bool):
    """List all items."""
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Retrieving items...", json_output)
    try:
        result = api_client.some_method()
        output_result(result, json_output, success_message="Items retrieved successfully")
    except DomainSpecificException as e:
        output_error(e, json_output, error_type="Domain Error")
        raise click.ClickException(str(e))
```

**Rules**:

1. Always use `@click.pass_context` before `@requires_setup_and_auth`
2. Always accept `json_output: bool` parameter on commands that produce output
3. Always use `get_profile_manager_from_context(ctx)` -- never construct `ProfileManager()` directly in commands
4. Always use `output_status()`, `output_result()`, `output_error()` from `json_output.py`
5. Never print directly with `print()` or bare `click.echo()` in commands that support `--json-output`
6. Comment `# Setup/auth already validated by decorator` at top of command body
7. Catch only `BusinessLogicError` subclasses in commands; never catch `GlobalInfrastructureError`

### 3. API Client Patterns

The `APIClient` class in `utils/api_client.py`:

```python
api_client = APIClient(config['api_gateway_url'], profile_manager)
```

**Key behaviors**:

-   Wraps `requests.Session` with `DEFAULT_TIMEOUT = 30`
-   Sets headers: `Content-Type: application/json`, `User-Agent: vamscli/{version}`
-   Bearer token from `ProfileManager.load_auth_profile()`
-   Pre-flight token validation for override tokens (checks expiry before request)
-   Refuses an endpoint whose path has an empty, `.` or `..` segment (raw or percent-encoded) with `InvalidRequestPathError` before anything is sent: the URL join drops or resolves such a segment, so a path value carrying one would address another route (`tests/test_api_request_path.py`)
-   HTTP 429: Exponential backoff with jitter, respects `Retry-After` header
-   HTTP 401: Auto-refresh token (Cognito only), fails immediately for override tokens
-   HTTP 403: Classified by the party that answered it (see the table below); only the authorizer's deny and an unclassified (`unknown`) 403 are refreshed and resent
-   HTTP status to exception mapping in response handlers:
    -   `404` --> `NotFoundError` variants
    -   `409` --> `AlreadyExistsError` variants
    -   `403` --> `RequestBlockedError`, `PermissionDeniedError`, `TokenExpiredError` or `APIError`, raised by `_make_request` itself
    -   `429` --> `RateLimitExceededError` / `RetryExhaustedError`

**HTTP 403 classification.** `_classify_403(response)` (module level in `api_client.py`, never raises) names the party that answered from the response alone, and `_make_request` raises before the `raise_http_errors` re-raise. A 403 from another host that a followed redirect reached (`_redirected_host`: a non-empty `response.history` and a final host other than the request's, such as the presigned Amazon S3 URL of a staged `assets export` payload) is not classified: it is `APIError` (`_REDIRECT_TARGET_REFUSED_MESSAGE`) naming that host, never the URL, after one send. Only the authorizer's deny can be cured by a fresh token, so only it and an unclassified (`unknown`) 403, which may be one, are refreshed and resent:

| Kind (first match)                                                       | Raised                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `waf`: JSON `blockedBy == "waf"`, or CloudFront's "request blocked" text | `RequestBlockedError(confirmed=True)`, with or without auth; one send                                                                                                                                                                              |
| `waf_or_stage`: the body is exactly `{"message": "Forbidden"}`           | With auth, `RequestBlockedError(confirmed=False)`, one send; without auth, `APIError` + `_STAGE_HINT` naming both causes (the original `HTTPError` with `raise_http_errors=True`)                                                                  |
| `no_route`: `Missing Authentication Token`, or its `x-amzn-ErrorType`    | `APIError` naming the method and URL path, never the body; one send                                                                                                                                                                                |
| `resource_policy`: `execute-api:invoke` in `Message` or `message`, auth  | `PermissionDeniedError(_RESOURCE_POLICY_MESSAGE)`; one send                                                                                                                                                                                        |
| `handler`: any other lowercase `message`, with auth                      | `PermissionDeniedError("Access forbidden. " + message)`; one send                                                                                                                                                                                  |
| `authorizer`, `unknown`, with auth                                       | The override-token checks, then a Cognito refresh up to `MAX_AUTH_RETRIES`; a failed refresh is `TokenExpiredError` or `PermissionDeniedError` as before, and a deny that outlasts the retries `PermissionDeniedError(_AUTHORIZER_DENIED_MESSAGE)` |

`RequestBlockedError` carries `json_fields` `{"blockedBy": "waf" | "waf-or-stage", "requestId"?}` (the marker body's request ID, else the first non-blank `x-amz-apigw-id`, `x-amz-cf-id` or `x-amzn-RequestId` header: the IDs the regional web ACL log, the CloudFront web ACL log and the API Gateway access log record), never clears stored credentials (`call_login_profile` re-raises global errors), and is printed under `Request Blocked`, ahead of the `AuthenticationError` branch, with `WAF_ADMIN_HINT` (`WAF_OR_STAGE_ADMIN_HINT`, which also names the stage-path check, when `confirmed` is false) and the request ID. Its texts carry no sign-in wording (`auth login`, `401`, `token has expired`, `Authentication failed`), which the IsaacSim connector would class as a credential failure; `tests/test_api_403_classification.py::TestBlockWording` holds them to that. `check_api_availability` raises `RequestBlockedError` for the marker and `APIUnavailableError` naming both causes for a bare `Forbidden`, and `requires_api_access` re-raises both, so a blocked version check stops `auth login`, `auth change-password` and `auth forgot-password` before any Cognito call; `setup` refuses a URL whose stage probe the web ACL blocked.

**Rules**:

1. Always pass `profile_manager` to `APIClient` constructor
2. Never make raw `requests` calls -- always go through `APIClient`
3. API endpoint constants are in `constants.py` as format strings (e.g., `API_DATABASE_ASSETS = "/database/{databaseId}/assets"`)
4. New API methods go in `api_client.py` (an add-on's in its mixin under `utils/addon/`), not in command files
5. In an `APIClient` method, every broad handler that raises -- a bare `except`, or one naming `Exception`, `BaseException` or `VamsCLIError` -- is preceded by `except GlobalInfrastructureError: raise` in the same `try`, and a handler naming `GlobalInfrastructureError` or a subclass re-raises with a bare `raise`; a handler that swallows the error or returns a value is exempt. The ratchet `tests/test_api_client_global_error_guards.py` fails otherwise. This keeps a web ACL block, a permission denial or an expired token typed through every `APIClient` method; `utils/upload_manager.py`, `utils/download_manager.py` and a few command wrappers still record one per file, upload sequence or item, with its message but without its heading or `json_fields`

### 4. JSON Output Contract

All commands must support `--json-output` for machine-readable output:

```python
# Use output helpers from utils/json_output.py
output_status("Processing...", json_output)    # Suppressed in JSON mode
output_result(data, json_output)               # Pure JSON in JSON mode
output_error(exception, json_output)           # JSON error + sys.exit(1) in JSON mode
output_warning("Caution!", json_output)        # Suppressed in JSON mode
output_info("Hint text", json_output)          # Suppressed in JSON mode
```

**Rules**:

1. In JSON mode, `output_error()` calls `sys.exit(1)` -- the `raise click.ClickException()` after it only runs in CLI mode
2. Never use `click.echo()` directly in commands that support `--json-output` -- it would pollute JSON output
3. Status/warning/info messages are suppressed when `json_output=True`
4. CLI mode uses colored output via `click.secho()` with `fg='green'`, `fg='red'`, etc.
5. The JSON error object is `{"error", "error_type"}` plus the exception's `json_fields`, merged without replacing those two and only when it is a dict, whether it is set on the class or on the instance (any other value is left out) -- `RequestBlockedError` carries `{"blockedBy": "waf" | "waf-or-stage", "requestId"?}` on the instance, and an add-on's exceptions set theirs on the class ([Add-on Commands](#add-on-commands)); `tests/test_json_output_error_fields.py` holds `output_error` to that. A global error's document (`_global_error_data` in `utils/global_exceptions.py`) is `{"error": "<Heading>: <message>", "error_type", ...json_fields, "additional_info"?}`, also on stdout; in text mode its `✗ <Heading>: <message>` line goes to stderr and its `additional_info` lines to stdout. Both exit 1

### 5. Decorator Usage

Three decorators in `utils/decorators.py` and `utils/global_exceptions.py`:

| Decorator                         | Purpose                                  | Use When                                                                                            |
| --------------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `@requires_setup_and_auth`        | Validates setup, checks API, logs timing | All authenticated commands (default)                                                                |
| `@requires_feature(feature_name)` | Gates behind feature switches            | Commands that run only when a feature switch is enabled (e.g., `vamscli features example-govcloud`) |
| `@handle_global_exceptions()`     | Top-level infrastructure error handler   | Only on `cli()` group and `main()` in main.py                                                       |

**Decorator stacking order** (bottom of stack executes first):

```python
@domain.command()
@click.option(...)
@click.pass_context
@requires_setup_and_auth       # Validates setup/auth before command runs
def my_command(ctx, ...):
```

For feature-gated commands, pass the switch as a `FEATURE_*` constant from `constants.py`:

```python
from ..constants import FEATURE_GOVCLOUD

@domain.command()
@click.option(...)
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_GOVCLOUD, "GovCloud features are not enabled for this environment.")
def my_feature_command(ctx, ...):
```

**Rules**:

1. `@handle_global_exceptions()` is ONLY applied at the top level (`cli()` and `main()`)
2. `@requires_setup_and_auth` goes on every command that needs API access
3. `@requires_feature()` goes AFTER `@requires_setup_and_auth` in the stack (closer to function)
4. Never add `@requires_api_access` to new commands -- it is legacy, use `@requires_setup_and_auth`
5. `@requires_feature()` takes a `FEATURE_*` constant from `constants.py`, never a string literal; `vamscli features example-govcloud` and `vamscli features example-location` (`commands/features.py`) are the reference uses. The `user cognito` commands carry no feature gate: a deployment with Cognito disabled answers them with a `400`, which `APIClient` reports as `Cognito not enabled: ...` (`_is_cognito_unavailable` in `utils/api_client.py`)

### 6. Profile Management

`ProfileManager` in `utils/profile.py` manages multi-profile configuration:

-   Platform-specific config directories:
    -   Windows: `%APPDATA%/vamscli/`
    -   macOS: `~/Library/Application Support/vamscli/`
    -   Linux: `~/.config/vamscli/`
-   Files per profile: `config.json`, `auth_profile.json`, `credentials.json`
-   Active profile tracked in `active_profile.json`
-   Profile name validation: 3-50 chars, `[a-zA-Z0-9_-]`, reserved names: `help`, `version`, `list`

**Profile resolution order**: an explicit `--profile` wins; otherwise the profile recorded in
`active_profile.json` by `profile switch` is used; only when no marker exists does the default profile
apply. `read_active_profile_name()` (module level in `utils/profile.py`) performs that lookup — it is
module level because a caller has to resolve the name _before_ it can construct a `ProfileManager`,
and it is best-effort (any read failure degrades to the default) because it runs on every invocation.

**Rules**:

1. Always obtain `ProfileManager` via `get_profile_manager_from_context(ctx)` in commands
2. Never hardcode profile paths -- use `ProfileManager` methods
3. The default profile name is `"default"` (constant `DEFAULT_PROFILE_NAME`), and it is a **last**
   resort, not the no-flag default. Never fall back to it directly when resolving which profile to
   use — call `read_active_profile_name()`. Never give the global `--profile` option a Click
   `default=`, either: a default makes Click pass that name even when the flag is absent, so the
   callback cannot distinguish "omitted" from "explicitly asked for the default profile", and
   `profile switch` silently becomes a no-op for every command. Guarded by
   `tests/test_active_profile_resolution.py`.
4. A bare `ProfileManager()` / `APIClient(url)` targets the **default** profile. Pass the resolved
   profile (`ProfileManager(read_active_profile_name())`) when no manager is supplied.
5. **A hint names a command Click accepts.** `--profile`, `--verbose` and `--version` are
   root-group options and go **before** the subcommand: `vamscli --profile <name> setup <url>`;
   placed after the subcommand, Click rejects them with "No such option". Token-override guidance names
   `auth set-override -u <user-id> --token-stdin` or
   `auth login --user-id <user-id> --token-override-stdin`; there is no root `--token-override`.
   `tests/test_setup_check.py` scans the CLI, MCP and connector sources for any
   `vamscli --<opt>` that is not a root option.
6. **The setup gate exempts the recovery commands.** `check_setup_required` (`main.py`) reads the
   invoked top-level command off `sys.argv` (skipping root options and the value of `--profile`)
   and lets `setup`, `version` and the `profile` group run without a configured profile — those
   are the commands its own hint sends the user to. Any new command that must work before setup
   is added to `_SETUP_EXEMPT_COMMANDS`, not special-cased in the callback.

### 7. Constants and Endpoints

All API endpoints live in `constants.py` as format strings:

```python
API_DATABASE_ASSETS = "/database/{databaseId}/assets"
API_DOWNLOAD_ASSET = "/database/{databaseId}/assets/{assetId}/download"
API_ASSET_VERSION_BY_ID = "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}"
API_ASSET_VERSION_ARCHIVE = "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/archive"
API_ASSET_VERSION_UNARCHIVE = "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/unarchive"
```

**Rules**:

1. New endpoints go in `constants.py` (an add-on's in `addon_constants.py`; in a fork: `custom_constants.py`), never hardcoded in commands or api_client
2. Upload/download limits are constants, not magic numbers
3. Feature switch names are constants (e.g., `FEATURE_GOVCLOUD = "GOVCLOUD"`; in a fork, `FEATURE_CUSTOM_*` in `custom_constants.py`)
4. Retry config defaults are constants, overridable via env vars
5. **Every `API_*` path constant must name a route the API actually registers.** `constants.py` is
   the authority a new consumer trusts, so a path the backend never declared is a trap that fails
   only at runtime against a live deployment — API Gateway rejects an unmatched path before any
   handler runs, and neither import nor build catches it. `tests/test_constants_contract.py`
   cross-checks every constant against the `ApiRoute` declarations in
   `backend/backend/common/apiRoutes.py` (and `common/addonApiRoutes.py` and `custom/customApiRoutes.py`
   for the lane constants). Two such constants shipped in this release: one formatted
   by a live `APIClient` method, and one imported but never formatted, which read like working code.
   A constant used as a path **prefix** (concatenated at the call site rather than formatted) needs
   an entry in `_PREFIX_ONLY_CONSTANTS` naming why; prefer a format-string constant matching a
   registered route, since the exemption list is the one place this check can be widened.

6. **One route cannot be reached with `str.format`.** `API_COMMENTS_ASSET_VERSION_COMMENT` ends in
   `{assetVersionId:commentId}` — the API's composite sort key, which is two values joined by a
   colon inside a single path segment. `str.format` reads everything after the colon as a format
   spec, so formatting that constant raises `ValueError`. `build_comment_path()` (module level in
   `utils/api_client.py`) substitutes the parts instead, and is the only place that should: it also
   rejects a colon in either part, because the handlers split the segment and read element `[1]` as
   the commentId — an extra colon shifts which value is validated, and a missing one makes that
   index raise, which surfaces as a `500` rather than a rejected request.

### 8. Authentication Flow

Authentication is managed in `auth/`:

-   `base.py`: `BaseAuthenticator` abstract class
-   `cognito.py`: `CognitoAuthenticator` with SRP and USER_PASSWORD_AUTH flows

Login flow:

1. `CognitoAuthenticator.authenticate()` (handles MFA, new password challenges)
2. Save auth profile via `ProfileManager`
3. Call `/auth/loginProfile/{userId}` to get user profile
4. Call `/secure-config` for feature switches
5. Store feature switches in profile config

Password changes (Cognito only):

-   `authenticate()` accepts `new_password` and `interactive`. A `NEW_PASSWORD_REQUIRED` challenge is answered with `new_password` when provided; otherwise it prompts (interactive) or raises `AuthenticationError` (non-interactive, e.g. `--json-output`).
-   `vamscli auth login --new-password` completes a forced password change; the command passes `interactive=not json_output`.
-   `CognitoAuthenticator.change_password(access_token, previous_password, proposed_password)` wraps the Cognito `ChangePassword` API. `vamscli auth change-password` signs in with the current password (`interactive=False`) and then changes it, also satisfying a forced change in one step.
-   `CognitoAuthenticator.forgot_password(username)` and `confirm_forgot_password(username, code, new_password)` wrap the Cognito `ForgotPassword` / `ConfirmForgotPassword` APIs (self-service reset, no current password needed). `vamscli auth forgot-password` is a single two-phase command: with no `--code` it requests an emailed code; with `--code` + `--new-password` it confirms. Interactive mode prompts through both phases; `--json-output` requests-only or confirms when both are supplied.
-   These flows call the `cognito-idp` client directly (boto3), not a VAMS API route, so they have no `constants.py` endpoint entry.
-   **The `cognito-idp` client is built with `signature_version=UNSIGNED` and must stay that way.** Every operation these flows reach — `initiate_auth`, `respond_to_auth_challenge`, `forgot_password`, `confirm_forgot_password`, `change_password` — is an unauthenticated Cognito user-pool API that takes no SigV4 signature, and botocore skips credential resolution entirely for an unsigned client. A signed client walks the AWS credential chain inside `create_client`, so a configured-but-failing provider (an expired SSO session, a `credential_process` helper that now returns non-zero) aborts `vamscli auth login` with a raw `CredentialRetrievalError` before any Cognito call — the CLI's front door, blocked by an AWS configuration it does not use. A machine with no AWS configuration at all resolves to `None` and proceeds, which is why CI never sees it. Pinned by `tests/test_auth_cognito_unsigned.py`.
-   A test building a `CognitoAuthenticator` must patch `vamscli.auth.cognito.boto3.client` **around** the constructor, not assign over `.client` afterwards: the client is built in `__init__`, so a later assignment leaves a real one already constructed and makes the test depend on ambient AWS configuration. `tests/test_auth_password.py::_make_authenticator` is the reference shape, and `tests/conftest.py::isolate_aws_credentials` sets dummy credentials suite-wide as a second line of defence.

Override tokens (external auth):

-   Set via `vamscli auth set-override --token <jwt>`
-   Pre-flight expiry check before each API request
-   No auto-refresh -- fails immediately on 401

### 9. The CLI Sets Its Own Output Encoding — Never Rely on the Ambient Locale

VamsCLI uses Unicode characters (e.g., `✓`, `✗`, `●`) in CLI output for status indicators — several
hundred occurrences across 33 modules. Python selects stdout's encoding from the locale whenever stdout
is **not** a console, which on a default Windows install is the ANSI code page (`cp1252`). None of those
characters exist in `cp1252`, so every redirect and every pipe raised `UnicodeEncodeError`:
`vamscli profile list > profiles.txt` wrote one line naming a charmap error and no profile data.

`main._use_utf8_output()` therefore reconfigures `sys.stdout` and `sys.stderr` to UTF-8 with
`errors="replace"` at the entry point, before Click parses anything.

**Rules**:

1. Unicode characters in CLI output are intentional and must not be replaced with ASCII. The encoding
   is set once at the entry point; do not work around a rendering problem by editing glyphs out of a
   command, because the next added glyph reintroduces the failure across all 33 modules.
2. **Never make output correctness depend on `PYTHONIOENCODING`.** It is no longer required, and the
   user-facing docs no longer ask for it — `docs/cli/installation.md`, `getting-started.md`,
   `troubleshooting/general.md`, `troubleshooting/database-tags.md` and `tools/VamsCLI/README.md` all
   describe the CLI as setting its own encoding.
3. `errors="replace"` is deliberate: a stream that genuinely cannot represent a character substitutes it
   rather than turning a working command into a failed one. A legacy console drawing a box instead of a
   check mark is a font limitation, not an error.
4. A stream lacking `reconfigure` (a `StringIO` under `CliRunner`, for instance) must be left alone —
   reconfiguring unconditionally would raise `AttributeError` at entry and fail every command.
5. **Do not put `PYTHONIOENCODING=utf-8` in a test fixture.** That is what hid this defect for a whole
   release: `tests/test_json_output_purity.py` set it so the glyphs would encode under its own
   subprocesses, which fixed the symptom for the suite and left it in production. A test covering output
   encoding must force a legacy code page instead — see `tests/test_output_encoding.py`, which sets
   `PYTHONIOENCODING=cp1252`, clears `PYTHONUTF8`, and carries a positive control asserting a
   non-`cp1252` character actually reaches stdout so the case cannot pass vacuously.
6. `CliRunner` cannot see this class of defect at all: it replaces `sys.stdout` with an in-memory buffer
   that has no code page. Encoding behaviour must be tested by spawning `python -m vamscli.main`
   (Testing rule 9 covers the config-home requirement that comes with a subprocess).

### 10. Never Log a Whole Request or Response Payload Unredacted

The CLI writes to a rotating log file (`get_config_dir()/logs/vamscli.log`, up to 6 files) at DEBUG level,
regardless of whether `--verbose` is set. Anything written there **outlives the command** and, on a shared
host or build agent, is readable by other local users. Several API payloads carry live credentials:

| Payload                                       | Credential                                                         |
| --------------------------------------------- | ------------------------------------------------------------------ |
| `auth login` response                         | Amazon Cognito access, refresh, and id tokens                      |
| `auth login` / `auth change-password` request | The plaintext password                                             |
| `api-key create` response                     | The one-time plaintext `vams_…` API key                            |
| Any download response                         | Presigned Amazon S3 URLs (a bearer credential in the query string) |

**Every log call that receives a whole request or response payload MUST route it through the redactor in
`utils/logging.py`:**

```python
from .logging import redact_to_text   # returns a redacted string
# or
from .logging import redact_sensitive # returns a redacted copy of the object

logger.debug(f"Response body: {redact_to_text(response_data)}")
```

The five existing sinks are already wired: `output_result` (`utils/json_output.py`), `log_api_request`
and `log_api_response` (`utils/logging.py`), and the command-result lines in `@requires_setup_and_auth`
(`utils/decorators.py`) and `@handle_global_exceptions()` (`utils/global_exceptions.py`). **Adding a sixth
sink means adding the redactor call** — there is no automatic interception, because the payload reaches the
logger as an already-formatted string. `tests/test_log_redaction_sink_inventory.py` scans the package for a
log call that interpolates an unredacted payload, so a sixth sink added without the redactor fails there
rather than at an audit. Redaction runs before truncation, so the length cut cannot reveal anything.

**How the redactor decides:**

-   **By key name** — a key whose normalized form (separators stripped, lowercased) contains `password`,
    `passwd`, `secret`, `token`, `credential`, `apikey`, `authorization`, `signature`, `privatekey`,
    `cookie`, or `connectionurl` is replaced. Applied recursively through dicts and sequences.
-   **By descriptive suffix (an exception)** — a key that also _ends_ in `id`, `name`, `arn`, `type`,
    `count`, `expiry`, `expiration`, `enabled`, or `status` is treated as naming a credential rather than
    carrying one, so `apiKeyId`, `tokenType`, and `credentialsSecretArn` survive. `apiKeyHash` and
    `passwordHash` deliberately do **not** survive.
-   **By value shape** — `vams_…` keys, JWTs, `Bearer …`, and presigned-URL `X-Amz-Signature` /
    `X-Amz-Security-Token` parameters are scrubbed even when the key name gives no hint, which covers a
    payload that was already rendered to a string.

**Two properties that must not be broken** (both pinned by `tests/test_log_redaction.py`):

1. **Redaction applies to the log file only — never to console or `--json-output`.** A newly created API
   key is displayed exactly once and cannot be retrieved again, so leaking redaction into the output path
   would make `api-key create` useless. `redact_sensitive` returns a **copy** and never mutates its input.
2. **Non-secret keys must survive.** This CLI logs S3 object keys constantly (`s3Key`, `objectKey`,
   `keyName`, `bucketExistingKey`). Over-redaction destroys the diagnostic value the log exists for, which
   is why the key-fragment list spells out credential names instead of matching a bare `key`.

**One predicate, every per-key filter.** `redact_mapping_for_log()` wraps `_is_sensitive_key` and is
what `log_command_start`, `log_api_request`'s header filter, `log_config_info`,
`log_config_diagnostic`, and `log_auth_diagnostic` all use. A new per-key log filter must call it
rather than keep its own list: each of those sites previously matched with
`key.lower() in ['password', 'token', 'secret', 'key']`, which missed every parameter name the CLI
actually declares — `new_password`, `old_password`, `token_override`, `access_token` — while the
redaction visibly fired for `password` on the same line. Pinned by
`tests/test_log_redaction.py::TestPerKeyLogFiltersShareOnePredicate`, which asserts both directions
(the secrets are masked, and `starting_token` / `tokenType` / `apiKeyId` survive).

### 11. Every Credential Option Needs a Non-Argv Alternative

The OS process table publishes every argument of a running process — `/proc/<pid>/cmdline` and `ps -ef`
on Linux, Task Manager's command-line column on Windows — to any other local account, including one with
no VAMS entitlement. A command that accepts a password, token, or API key as an **option value only**
therefore has no safe non-interactive form, and the CLI's two external connectors are non-interactive by
construction.

`auth login` pairs each credential option with a stdin flag: `-p/--password` with `--password-stdin`, and
`--token-override` with `--token-override-stdin`. Follow that shape for any new credential input:

-   Read the secret with `read_secret_from_stdin()` (`commands/auth.py`). It reads `sys.stdin.buffer` and
    decodes UTF-8, because the writer is usually another process and a text-mode read would use the
    console code page, which differs on the two ends. Only CR and LF are stripped, so a credential
    ending in a space survives.
-   Reject the stdin flag combined with its option form, and reject an empty payload — an empty pipe
    otherwise authenticates with an empty secret or saves an override token that 401s on every call.
-   **Keep the option form working.** Existing scripts and integrations depend on it. Say in its `help`
    text that it is discouraged and why, and document the stdin form as the recommended one in
    `documentation/docusaurus-site/docs/cli/commands/setup-and-auth.md`.

Pinned by `tests/test_auth_secret_not_in_argv.py` (CLI side) and
`tools/ExternalIntegrations/isaacsim_vams_integration/tests/test_vams_cli_service_secrets.py` (connector
side). The two suites together are what catches a flag rename: the connector does not import this
package.

### 12. Never Call a Builtin a Command Shadows — Use `builtin_list`

Click commands are bound to module-scope names, so a command named `list` produces a module attribute
`list` that is a `click.Command`, **shadowing the builtin for the whole module**. A `Command` is
callable — `Command.__call__` runs `main()` — so `list(x)` does not build a list; it executes that CLI
command as a nested program, parsing `x` as its argv:

```python
# In commands/assets.py, which defines `def list(...)` as `assets list`:
list([])                 # runs `assets list` with no args -> prints the asset listing, SystemExit(0)
list({'dup.txt'})        # runs `assets list dup.txt`      -> "unexpected extra argument", SystemExit(2)
```

Both failures are hard to attribute. The empty-iterable case is the worse one: the command prints an
unrelated asset listing in place of its own output **and exits 0**, so it reads as a successful run of
something else entirely. Neither raises `TypeError`, so nothing points at the call site.

**Any module defining a command named after a builtin must import that builtin under an alias and use
it:**

```python
from builtins import list as builtin_list  # Avoid namespace collision with the 'list' command

unique = builtin_list(set(conflicts))
```

`commands/metadata.py` established the idiom; `commands/assets.py` follows it. The names at risk are
the command names this CLI actually uses — `list`, `get`, `set`, `delete`, `update`, `create` — of which
`list`, `get` and `set` are builtins. Prefer a comprehension or `[*a, *b]` where one will do, since
neither names the builtin at all.

There is no linter for this: the module-scope rebinding is legitimate Python and the call type-checks.
Treat a command whose output includes another command's output as this bug until proven otherwise.

---

## Testing

### Framework and Configuration

-   **Framework**: pytest with Click's `CliRunner`
-   **Test files**: `tests/test_*.py`, one per command group or behavior area; the `industry engineering` (BOM, PLM) command tests are under `tests/industry/engineering/`, and the add-on command and client tests under `tests/addon/`
-   **Shared fixtures**: `tests/conftest.py`

### Key Fixtures (conftest.py)

| Fixture                    | Scope    | Purpose                                        |
| -------------------------- | -------- | ---------------------------------------------- |
| `isolate_logging_globals`  | autouse  | Restores `_verbose_mode` / `_logger` per test  |
| `isolate_aws_credentials`  | autouse  | Dummy AWS credentials; clears `AWS_PROFILE`    |
| `redirect_log_dir`         | autouse  | Points `get_log_dir` at a temp directory       |
| `CoroutineClosingMock`     | class    | `asyncio.run` mock that closes the coroutine   |
| `mock_logging`             | autouse  | Prevents file system operations during tests   |
| `cli_runner`               | function | Pre-configured `CliRunner` instance            |
| `mock_profile_manager`     | function | ProfileManager mock with `has_config()=True`   |
| `mock_api_client`          | function | APIClient mock with `check_api_availability()` |
| `no_setup_profile_manager` | function | ProfileManager mock with `has_config()=False`  |
| `generic_command_mocks`    | function | Factory for comprehensive command mocks        |
| `no_setup_command_mocks`   | function | Factory for no-setup scenario mocks            |

### The generic_command_mocks Pattern

This is the standard pattern for testing commands. It patches 5 injection points:

```python
def test_my_command(self, cli_runner, generic_command_mocks):
    with generic_command_mocks('database') as mocks:
        # Configure API response
        mocks['api_client'].list_databases.return_value = {
            'Items': [{'databaseId': 'db1', 'description': 'Test DB'}]
        }

        # Invoke command
        result = cli_runner.invoke(cli, ['database', 'list'])

        # Assert
        assert result.exit_code == 0
        assert 'db1' in result.output
```

The `generic_command_mocks(command_module)` context manager patches:

1. `vamscli.main.ProfileManager` (main entry)
2. `vamscli.utils.decorators.get_profile_manager_from_context` (decorator layer)
3. `vamscli.commands.{command_module}.get_profile_manager_from_context` (command layer)
4. `vamscli.utils.decorators.APIClient` (decorator layer)
5. `vamscli.commands.{command_module}.APIClient` (command layer)

**Rules**:

1. Always use `generic_command_mocks` for command tests -- do not manually patch these 5 points
2. The `command_module` parameter must match the filename in `commands/` (e.g., `'database'`, `'assets'`, `'tag_type'`); an add-on module is named with its package (`'addon.edh'`)
3. For nested modules like `roleUserConstraints`, use the actual module name: `'roleUserConstraints'`
4. Use `no_setup_command_mocks` for testing setup-required error paths
5. To disable the autouse `mock_logging`, mark the test: `@pytest.mark.no_mock_logging`
6. Never leave `vamscli.utils.logging._verbose_mode` or `._logger` mutated. `main.py` binds `initialize_logging` at import, so `mock_logging`'s patch does not intercept the CLI group's call — every `cli_runner.invoke` writes those globals for real. In verbose mode each `log_*` call also writes to stderr, `CliRunner` merges stderr into `result.output`, and any later `json.loads(result.output)` fails on text wrapped around its JSON. The autouse `isolate_logging_globals` fixture restores both; `tests/test_logging_isolation.py` guards it in ordered pairs.
7. Patch a command's `asyncio.run` with `new_callable=CoroutineClosingMock` (from `tests/conftest.py`). Commands call `asyncio.run(some_coro())`; Python evaluates the argument first, so the coroutine object is always built and a plain `MagicMock` then discards it un-awaited. The "coroutine ... was never awaited" `RuntimeWarning` surfaces whenever that object is later garbage collected, attributed to an unrelated test. `CoroutineClosingMock` closes the coroutine and otherwise behaves as a normal `MagicMock`, so `return_value`, `side_effect`, and call assertions are unaffected. Do not use `AsyncMock` here — it returns a coroutine instead of the canned value and leaks two coroutines instead of one.
8. `_is_verbose_mode()` reads only the `_verbose_mode` module global, which `initialize_logging()` sets from Click's parsed `--verbose` (registered in `main.py`). Keep it that way. While it also matched the literal `--verbose` anywhere in `sys.argv`, the suite's result depended on how pytest was invoked: pytest's own flag turned on stderr logging session-wide and broke ~113 tests that parse `result.output` as JSON, and no fixture could prevent it because the helper is consulted per call rather than per test — `tests/conftest.py` had to strip the argument out of `sys.argv` at import, which is a process global no test owns. The same sniff fired on an option **value**, so `vamscli search assets -q "--verbose"` turned on full request/response logging. Both are pinned by `tests/test_logging_isolation.py::test_verbosity_does_not_depend_on_process_argv` and `tests/test_logging.py::TestVerboseMode`.

9. **A test that runs the CLI as a SUBPROCESS must supply its own config home.** `CliRunner` bypasses `main()`, so behavior that lives there (the `standalone_mode=False` call and its `UsageError`/`ClickException` → JSON handling) can only be tested by spawning `python -m vamscli.main`. That subprocess has no pytest loaded, so `check_setup_required`'s `if 'pytest' in sys.modules` escape hatch does **not** apply and the setup gate is live: on a developer machine with a real profile the gate passes and the test reaches the behavior it meant to exercise, while on a clean checkout or in CI it fires first and every case sees a `SetupRequired` payload (no `error_type` key, no `Usage:` text). Point `HOME`, `USERPROFILE` and `APPDATA` at a `tmp_path` holding one `profiles/default/config.json` — see the `cli_env` fixture in `tests/test_json_output_purity.py`, which also keeps the suite independent of whatever profiles the developer happens to have. Include a control asserting `"Setup Required" not in output`, so a fixture that stops satisfying the gate fails loudly instead of silently testing the wrong error.

    Corollary for reproducing a CI failure locally: blanking `HOME` to simulate a clean runner also hides `~/.aws`, so any test using botocore fails with `ProfileNotFound` for whatever `AWS_PROFILE` your shell exports, and on Windows the temp `APPDATA` has no `vamscli/logs` directory so the rotating file handler raises `FileNotFoundError`. Both are artifacts of the simulation, not defects — preserve `.aws`, `env -u AWS_PROFILE`, and pre-create the log directory, or you will chase 18 phantom failures.

### Test Class Pattern

```python
class TestDatabaseList:
    """Tests for database list command."""

    def test_list_databases_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_databases.return_value = {
                'Items': [{'databaseId': 'db1'}]
            }
            result = cli_runner.invoke(cli, ['database', 'list'])
            assert result.exit_code == 0

    def test_list_databases_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_databases.return_value = {
                'Items': [{'databaseId': 'db1'}]
            }
            result = cli_runner.invoke(cli, ['database', 'list', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert 'Items' in data

    def test_list_databases_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_databases.side_effect = DatabaseNotFoundError("Not found")
            result = cli_runner.invoke(cli, ['database', 'list'])
            assert result.exit_code != 0

    def test_list_databases_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('database') as mocks:
            result = cli_runner.invoke(cli, ['database', 'list'])
            assert result.exit_code != 0
            assert 'Setup Required' in result.output or 'setup' in result.output.lower()
```

### Running Tests

```bash
# Run all VamsCLI tests
cd tools/VamsCLI
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_database_commands.py -v

# Run a specific test class or method
python -m pytest tests/test_database_commands.py::TestDatabaseList -v
python -m pytest tests/test_database_commands.py::TestDatabaseList::test_list_databases_success -v

# Run with coverage
python -m pytest tests/ --cov=vamscli --cov-report=term-missing

# List every test that pins one past change rather than a durable rule (root CLAUDE.md Rule 13)
python -m pytest -m temporary --collect-only
```

### Mark a Temporary Test with `@pytest.mark.temporary`

A test written to prove one specific change landed — a deleted branch, a removed helper, a reworded
message — carries `@pytest.mark.temporary` (registered in `pyproject.toml`) plus a line saying what it
pins. This suite runs `--strict-markers`, so the marker must stay registered or every test using it
fails.

The marker exists because a temporary test and a durable guardrail cannot be told apart by reading them
later: both scan source, both assert an absence, both explain themselves. Do **not** mark a test whose
forbidden construct is still writable — `test_api_error_body.py::test_every_error_path_in_the_client_uses_the_helper`
and the `test_log_redaction.py` guards must keep holding — nor a control whose subject is deliberately
a string that does not exist. Full criterion: root `CLAUDE.md` Rule 13.

---

## Development Workflow

### Adding a New Command

Follow this checklist:

1. **Define API endpoint constant** in `constants.py`:

    ```python
    API_MY_RESOURCE = "/my-resource"
    API_MY_RESOURCE_BY_ID = "/my-resource/{resourceId}"
    ```

2. **Add exception classes** in `utils/exceptions.py`:

    ```python
    # Under BusinessLogicError section
    class MyResourceError(BusinessLogicError):
        """Base class for my-resource errors."""
        pass

    class MyResourceNotFoundError(MyResourceError):
        """Raised when resource is not found."""
        pass

    class MyResourceAlreadyExistsError(MyResourceError):
        """Raised when resource already exists."""
        pass

    class InvalidMyResourceDataError(MyResourceError):
        """Raised when resource data is invalid."""
        pass
    ```

3. **Add API methods** in `utils/api_client.py`:

    ```python
    def list_my_resources(self, **params):
        """List my resources."""
        response = self._make_request('GET', API_MY_RESOURCE, params=params)
        return self._handle_response(response)

    def get_my_resource(self, resource_id):
        """Get a specific resource."""
        endpoint = API_MY_RESOURCE_BY_ID.format(resourceId=resource_id)
        response = self._make_request('GET', endpoint)
        return self._handle_response(response)
    ```

4. **Create command file** at `commands/my_resource.py` following the command pattern above

5. **Register command** in `main.py`:

    ```python
    from .commands.my_resource import my_resource
    # ...
    cli.add_command(my_resource)
    ```

6. **Write tests** in `tests/test_my_resource.py` following the test class pattern above

7. **Update user-facing documentation**. The official Docusaurus site (`documentation/docusaurus-site/docs/cli/`) is the **single source of truth** for CLI documentation. The legacy in-repo docs under `tools/VamsCLI/docs/` are deprecated — do not add or update content there.

    - Update the Docusaurus CLI reference page at `documentation/docusaurus-site/docs/cli/commands/` for the relevant command group
    - Update the matching troubleshooting page at `documentation/docusaurus-site/docs/cli/troubleshooting/` if behavior or error scenarios changed (CLI troubleshooting lives under the CLI section, not the top-level `troubleshooting/`)
    - Update `documentation/docusaurus-site/docs/cli/command-reference.md` index if a new command group was added
    - Update `documentation/docusaurus-site/sidebars.ts` if a new CLI command or troubleshooting page was added
    - Update `tools/VamsCLI/README.md` only for basic install/quick-start changes (it points to the official site for everything else)
    - Update `documentation/VAMS_API.yaml` with new/modified API endpoints and schemas
    - Update `documentation/docusaurus-site/docs/concepts/permissions-model.md` with new API route permissions
    - Run `cd documentation/docusaurus-site && npm run build` to verify links and MDX

    **Documentation style**: Follow Docusaurus format with `:::note`/`:::warning` admonitions, escape `\{curly braces\}` outside code blocks, use `bash` language tags on code blocks. See `documentation/CLAUDE.md` for full style guide.

8. **Update CHANGELOG.md** with the new command under the appropriate version section

9. **Propagate to the VAMS MCP server** (`tools/VamsMCP/`). The MCP server imports this package's `APIClient` and `ProfileManager` directly, so it is downstream of every change here. A renamed method, a new required parameter, or a changed response shape breaks its tools silently — the failure only appears at agent runtime.

    - Check whether `tools/VamsMCP/vams_mcp/server.py` calls the `APIClient` method you changed, and update the call site
    - Add an `@mcp.tool()` + `@tool_result` function for a new method agents should be able to use, in the correct gate section (read at top, writes under `if CONFIG.enable_writes:`, destructive under `if CONFIG.enable_destructive:`)
    - Confirm the pagination `items_key` still matches the endpoint's list field. Read the handler's response model rather than checking against a list of names: the names in use today are `Items`, `items`, `versions` and `metadata`. `VamsClient.paginate()` also unwraps the legacy `message` envelope
    - Verify the new `def` is unique and correctly positioned. The tools are module-level functions, so a duplicate name silently shadows the earlier one and a `def` placed after the `if __name__` entrypoint or outside its gate block never executes — the tool goes missing with no import error. `tests/test_server_tools.py` asserts the source layout for this
    - Add the tool to the `tools/VamsMCP/README.md` tool list (and the `autoApprove` sample if it is a safe read)
    - Run `cd tools/VamsMCP && pytest` in that server's own virtual environment — tests mock the client, so no live deployment is needed, but the `mcp` SDK needs Pydantic v2 and installing it into a shared environment breaks the Pydantic-v1 backend suite
    - Review `tools/VamsAgentSkill/SKILL.md` only if a **structural** rule changed (entity creation/deletion ordering, identifier semantics, permission scoping, or a new mutating command category); the skill self-discovers commands via `vamscli --help`, so ordinary additions need no edit

    See root `CLAUDE.md` Pattern 7 for the full propagation chain. If MCP work reveals a missing or wrong `APIClient` method, fix it here rather than hand-rolling raw requests in the MCP server.

10. **Validate the external tool integrations.** Whenever a command name, subcommand, option/flag, or `--json-output` response shape changes, the external connectors at `tools/ExternalIntegrations/` must be checked in the same change. Unlike the MCP server, they do not import `APIClient` — they build CLI argument strings and parse JSON keys, so **nothing catches drift at build or import time**. A renamed flag fails at connector runtime with a non-zero CLI exit; a renamed or removed JSON key silently produces a blank field, which is worse.

    - `isaacsim_vams_integration/vams/connector/isaacsim/vams_cli_service.py` -- Python subprocess wrapper. Check the argument lists passed to `subprocess.run` and each `@dataclass` field's `item.get("jsonKey", ...)` mapping.
    - `arcgispro-connector-for-vams/Services/VamsCliService.cs` -- C# subprocess wrapper. Check the interpolated argument strings plus the `[JsonPropertyName("jsonKey")]` attributes in `Models/VamsModels.cs`.

    Failure modes to watch for:

    - **Map each key to the command that actually returns it.** `file list` items and the `file info` response are different shapes: a listing item carries `dateCreatedCurrentVersion` and no `contentType`/`lastModified`, while `file info` carries `contentType`/`lastModified` and no `dateCreatedCurrentVersion`. A key mapped onto the wrong command is permanently empty with no error.
    - **ArcGIS computed properties need `[JsonIgnore]`** when their name matches a mapped JSON field (for example `Key` alongside `[JsonPropertyName("key")]`). Deserialization uses `PropertyNameCaseInsensitive`, so the collision throws `InvalidOperationException` while building type metadata and fails the whole response.
    - **Every invocation names the configured profile, and `--profile` is PREPENDED.** An omitted `--profile` resolves to whatever `profile switch` last recorded (Rule 6), so a connector that skips the flag follows another deployment. It is a group-level option, so a flag appended after the subcommand is rejected with Click's "no such option" — an argv-membership assertion passes and the command still fails at runtime. Both connectors prepend it centrally, in `_execute_command` and `ExecuteCommandAsync`; add it there, not per call site. `profile info <name>` also takes the profile as a **positional**, which must track the configured name too.
    - **Credentials go to the CLI's stdin, never onto argv** (Rule 11). The connectors pipe the password and the override token to `--password-stdin` / `--token-override-stdin`.

    To validate the command surface, walk `cli.commands[group].commands[cmd].params` for every group/subcommand/flag the connectors pass, then spot-check a live `--json-output` response for the keys each connector parses.

### Adding Commands in a Fork

An add-on is optional, upstream-maintained code enabled by configuration: it either integrates VAMS with an external solution deployed and run separately (Garnet Framework, Physna), supplying the VAMS-side components, or is a stand-alone non-core component that VAMS deploys in full when enabled (the licensed VNTANA and VEERUM viewers). Custom is fork code, which upstream ships empty. Upstream adding an integration with an external solution uses the add-on lane; a fork uses the custom lane. Definition and current add-ons: `documentation/docusaurus-site/docs/developer/addons-overview.md`.

A fork adds commands without editing `main.py`, `constants.py` or `utils/api_client.py`; each splices in a fork-owned file that upstream ships empty and never edits:

| What                     | Fork-owned file                                                                                   | Spliced in by                                                                                                                 |
| ------------------------ | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Endpoint paths, switches | `vamscli/custom_constants.py` (`API_*`, `FEATURE_CUSTOM_*`)                                       | read by the fork's client methods and commands (Rule 7)                                                                       |
| API client methods       | `vamscli/utils/custom_api_client.py` (`CustomAPIClientMixin`)                                     | `APIClient` inherits it, so the methods get retries and typed errors and the MCP server reaches them as `CLIENT.api.<method>` |
| Command groups           | `vamscli/commands/custom/<group>.py`, listed in `commands/custom/__init__.py` (`CUSTOM_COMMANDS`) | `main.py` registers every entry on the root `cli` group                                                                       |

-   Command files follow the same patterns as core ones (`roleUserConstraints.py`: decorators, `--json-output`, typed errors).
-   `custom_constants.py` imports nothing from `vamscli`, and `custom_api_client.py` must not import from `vamscli.commands` (which imports `APIClient`): either would create an import cycle.
-   `tests/test_constants_contract.py` checks every custom `API_*` path against the backend route lists (including `backend/backend/custom/customApiRoutes.py`) and every `FEATURE_*` in `custom_constants.py` against `infra/common/customAppFeatures.ts`.
-   A custom command never replaces a core one: `main.py` registers `CUSTOM_COMMANDS` after every core command and raises at import on a name that is already registered (`tests/test_custom_commands.py`).
-   A `CustomAPIClientMixin` method with the name of an `APIClient` method is never called, because `APIClient` comes first in the MRO; `tests/test_custom_api_client.py` fails on such a name.
-   Command names prefixed `custom-` are recommended, not enforced. Pattern 7 still applies: a custom command an agent should reach gets an MCP tool in `tools/VamsMCP/vams_mcp/custom_tools.py`.

### Add-on Commands

An add-on's commands use an add-on lane that mirrors the fork lane above. Upstream edits these files when it ships an add-on with commands; a fork does not:

| What                                      | Add-on file                                                                                                                                    | Spliced in by                                                                                                                             |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Endpoint paths, switches, shared messages | `vamscli/addon_constants.py` (`API_*`, `FEATURE_*`, the messages the commands and MCP tools share)                                             | `constants.py` re-exports every name                                                                                                      |
| API client methods                        | one mixin per add-on in `vamscli/utils/addon/<addon>_api_client.py`, aggregated by `vamscli/utils/addon_api_client.py` (`AddonAPIClientMixin`) | `class APIClient(AddonAPIClientMixin, CustomAPIClientMixin)`: a name resolves to the core method, then an add-on's, then a fork's         |
| Exceptions                                | `vamscli/utils/addon/<addon>_exceptions.py`, `BusinessLogicError` subclasses                                                                   | imported by the add-on's commands and client mixin                                                                                        |
| Command groups                            | `vamscli/commands/addon/<group>.py`, listed in `commands/addon/__init__.py` (`ADDON_COMMANDS`)                                                 | `main.py` registers them after every core command and before `CUSTOM_COMMANDS`, and raises at import on a name that is already registered |

-   `commands/addon/__init__.py` imports each module whole (`from . import edh`), so `vamscli.commands.addon.edh` names the module that `generic_command_mocks('addon.edh')` patches rather than its command group.
-   A client mixin imports from `..exceptions` and `...addon_constants`, never from `vamscli.commands`; a helper that needs a function of `api_client.py` imports it inside the function, since `api_client.py` imports the mixin.
-   Guards: `tests/test_addon_commands.py` (registration; a core name refused; a custom command named like an add-on command refused), `tests/test_addon_api_client.py` (MRO order; no add-on method shadowed by a core one or shared with another add-on mixin or `CustomAPIClientMixin`), `tests/test_constants_contract.py` (add-on paths against `backend/backend/common/addonApiRoutes.py`, switches against `infra/common/addonAppFeatures.ts`), and `tests/test_api_client_global_error_guards.py`, which parses every class in `APIClient.__mro__`.

#### EDH Workstations

-   `edh` (`commands/addon/edh.py`) has the `workstation`, `catalog`, `admin` (`images`, `ami-recipe`, `instances`, `capacity`, `health`, `settings`, `capacity-reservation`) and `container` sub-groups. Every command stacks `@requires_feature(FEATURE_EDH_WORKSTATIONS, ...)` after `@requires_setup_and_auth` (`container` uses `FEATURE_EDH_CONTAINERS`, `admin ami-recipe` uses `FEATURE_EDH_AMI_RECIPES`); the switch is a login-time snapshot. The three refusal sentences (`EDH_*_DISABLED_MESSAGE` in `addon_constants.py`) are the ones the MCP server's EDH pre-flight reports.
-   `EdhAPIClientMixin` (`utils/addon/edh_api_client.py`): every method passes `raise_http_errors=True`, because with the default `_make_request` converts a 404/400/5xx into a generic `APIError` before the method's own handler runs. `_edh_error` maps a 403 the methods read themselves to `PermissionDeniedError("Access forbidden. " + message)` and a 401 to `AuthenticationError`, and `_edh_session_id` checks a workstation id (one path segment, the service's `id_pattern`) before any request, as `_edh_capacity_reservation_id` checks a capacity reservation id (`cr-` and 17 hex digits). A 409 whose body carries `"code": "RESERVATION_FULL"` raises `EdhReservationFullError` (a launch's; a start answers 202 and the worker records a full reservation on the row). The list routes return every row in one `{"Items": [...]}` page, so the commands take no paging options.
-   `utils/addon/edh_exceptions.py` holds the `EdhError` family; the class names are the `error_type` that `--json-output` and the MCP tools report. `EdhOutcomeUnknownError` carries `json_fields` `{"outcomeUnknown": True}`, `EdhStartInProgressError` `{"startInProgress": True}` and `EdhReservationFullError` `{"code": "RESERVATION_FULL"}` on the class, the fields the API's own error body sets. `EdhStartFailedError` (`{"waitOutcome": "failed"}`) and `EdhStartWaitTimeoutError` (`{"waitOutcome": "timedOut"}`) are raised by `start --wait` alone, and an instance adds the record's `state`, `activity`, `lastError` and `startFailure` to its `json_fields`.
-   Start status: a start answers 202 and runs in the background. `state_label` gives the `list` State column (`STARTING`/`STOPPING` from the record's `activity`, else `state`), `format_workstation` prints `Activity:` and, for a STOPPED record whose start failed, `Start Failure:` with the sentence and the `AWS: <code> in <AZ>` line from `describe_start_failure`, and `start --wait` polls through `wait_for_start` every `START_WAIT_POLL_SECONDS` (10) for up to `START_WAIT_TIMEOUT_SECONDS` (600), exiting 0 only on ACTIVE with no activity; the tests replace the module's `_sleep` and `_monotonic`. The words match the web's badge and start-failure lines: `tests/addon/test_edh_start_status.py` reads the web's fixture `web/src/addons/edh/workstationStatusCases.json`, as the web test does.
-   `edh workstation connect` prints only the DCV host in CLI mode; the `connectionUrl`, which carries a session auth token, is returned only with `--json-output`. `--show-windows-password` adds a Windows workstation's sign-in, which no MCP tool reads.
-   `tests/addon/test_edh_client.py` holds the client to the backend: the route constants, the copied fields of `ami-recipe duplicate` (`_EDH_AMI_RECIPE_COPIED_FIELDS` against `AmiRecipeRequestModel`) and the 404 messages that tell a missing recipe from a missing build.
-   Option semantics, output and error texts: `documentation/docusaurus-site/docs/developer/edh-workstations/cli-commands.md` and `cli-troubleshooting.md`.

### Adding a New Exception Class

1. Choose the correct tier:

    - `GlobalInfrastructureError` for system-wide issues (auth, setup, connectivity)
    - `BusinessLogicError` for domain-specific command failures

2. Add the class in the correct section of `utils/exceptions.py`

3. If `GlobalInfrastructureError`: add a branch in `utils/global_exceptions.py` (`_handle_global_error("<Heading>", str(e), <info>, exc=e)`) ahead of any branch for a class it subclasses; `exc=e` puts its class name in `error_type` and merges its `json_fields`

4. If `BusinessLogicError`: catch and handle in the relevant command file

5. If the API client should raise it: add the mapping in `api_client.py`

---

## Templates

### New Command File Template

```python
"""<Domain> management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional

from ..constants import API_MY_RESOURCE, API_MY_RESOURCE_BY_ID
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    MyResourceNotFoundError,
    MyResourceAlreadyExistsError,
    InvalidMyResourceDataError,
)


@click.group()
def my_resource():
    """<Domain> management commands."""
    pass


@my_resource.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, json_output: bool):
    """List all resources.

    Examples:
        vamscli my-resource list
        vamscli my-resource list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Retrieving resources...", json_output)

    try:
        result = api_client.list_my_resources()
        items = result.get('Items', [])
        output_result(
            result,
            json_output,
            success_message=f"Found {len(items)} resource(s)",
            cli_formatter=lambda r: format_list_output(r),
        )
    except MyResourceNotFoundError as e:
        output_error(e, json_output, error_type="Resource Not Found")
        raise click.ClickException(str(e))


@my_resource.command()
@click.argument('resource_id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, resource_id: str, json_output: bool):
    """Get a specific resource."""
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status(f"Retrieving resource '{resource_id}'...", json_output)

    try:
        result = api_client.get_my_resource(resource_id)
        output_result(result, json_output, success_message="Resource retrieved successfully")
    except MyResourceNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Resource Not Found",
            helpful_message="Use 'vamscli my-resource list' to see available resources.",
        )
        raise click.ClickException(str(e))


def format_list_output(result: Dict[str, Any]) -> str:
    items = result.get('Items', [])
    if not items:
        return "No resources found."
    return '\n'.join(
        f"  {item.get('resourceId', 'N/A')} - {item.get('description', 'N/A')}"
        for item in items
    )
```

### New Test File Template

```python
"""Tests for my_resource commands."""

import json
import pytest
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import MyResourceNotFoundError


class TestMyResourceList:
    def test_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('my_resource') as mocks:
            mocks['api_client'].list_my_resources.return_value = {
                'Items': [{'resourceId': 'res-1', 'description': 'Test resource'}]
            }
            result = cli_runner.invoke(cli, ['my-resource', 'list'])
            assert result.exit_code == 0
            assert 'res-1' in result.output

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('my_resource') as mocks:
            mocks['api_client'].list_my_resources.return_value = {'Items': [{'resourceId': 'res-1'}]}
            result = cli_runner.invoke(cli, ['my-resource', 'list', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['Items'][0]['resourceId'] == 'res-1'

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('my_resource'):
            result = cli_runner.invoke(cli, ['my-resource', 'list'])
            assert result.exit_code != 0


class TestMyResourceGet:
    def test_get_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('my_resource') as mocks:
            mocks['api_client'].get_my_resource.return_value = {'resourceId': 'res-1'}
            result = cli_runner.invoke(cli, ['my-resource', 'get', 'res-1'])
            assert result.exit_code == 0

    def test_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('my_resource') as mocks:
            mocks['api_client'].get_my_resource.side_effect = MyResourceNotFoundError("Not found")
            result = cli_runner.invoke(cli, ['my-resource', 'get', 'bad-id'])
            assert result.exit_code != 0
```

### New Exception Class Template

Add in the correct tier section of `utils/exceptions.py`. Global tier for system-wide conditions; business tier for domain failures.

```python
# Global tier (system-wide)
class MyNewGlobalError(GlobalInfrastructureError):
    """Raised when <global infrastructure condition>."""

# Business tier (domain-specific): base class + specific subclasses
class MyDomainError(BusinessLogicError):
    """Base class for my-domain errors."""

class MyDomainNotFoundError(MyDomainError): ...
class MyDomainAlreadyExistsError(MyDomainError): ...
class InvalidMyDomainDataError(MyDomainError): ...
```

---

## Anti-Patterns

Each item duplicates a Critical Rule; the rule is authoritative. Do NOT:

1. Use `print()` / bare `click.echo()` in commands with `--json-output` — pollutes JSON output. Use `output_status/result/error` (Rule 4).
2. Construct `ProfileManager()` directly in commands — ignores `--profile` **and** the active profile, so the command silently runs against the default profile's deployment. Use `get_profile_manager_from_context(ctx)` (Rule 6).
3. Catch `GlobalInfrastructureError` in commands — must propagate to the global handler (Rule 1).
4. Hardcode API endpoints in commands or `api_client` — define a format-string constant in `constants.py` (Rule 7).
5. Make raw `requests` calls — always route through `APIClient` (Rule 3).
6. Manually patch `ProfileManager`/`APIClient` injection points in tests — use `generic_command_mocks(module)` (Testing section).
7. Use the legacy `@requires_api_access` decorator on new commands — use `@requires_setup_and_auth` (Rule 5).
8. Use magic numbers for size/count limits — import the named constant from `constants.py` (Rule 7, Key Constants).
9. Ship a command that produces output without `--json-output` support — every output-producing command accepts `json_output: bool` (Rule 4).
10. Forget the `output_error(...); raise click.ClickException(str(e))` pair — `output_error` exits in JSON mode; the raise handles CLI mode (Rule 4).
11. Spawn the CLI as a subprocess in a test without giving it its own config home — the setup gate is live there (no pytest in `sys.modules`), so the test passes only on a machine that happens to have a configured profile and fails in CI (Testing rule 9).
12. Call `list()`, `get()` or `set()` in a module that defines a command of that name — the name is a `click.Command`, and calling it runs that command as a nested CLI program instead of the builtin. Use `builtin_list` (Rule 12).

---

## Key Constants Reference

### Upload/Download Limits

| Constant                      | Value  | Purpose                    |
| ----------------------------- | ------ | -------------------------- |
| `DEFAULT_CHUNK_SIZE_SMALL`    | 150 MB | Small file chunk size      |
| `DEFAULT_CHUNK_SIZE_LARGE`    | 1 GB   | Large file chunk size      |
| `MAX_FILE_SIZE_SMALL_CHUNKS`  | 15 GB  | Threshold for large chunks |
| `MAX_SEQUENCE_SIZE`           | 3 GB   | Max sequence size          |
| `MAX_PREVIEW_FILE_SIZE`       | 5 MB   | Preview image limit        |
| `MAX_FILES_PER_REQUEST`       | 50     | Files per upload request   |
| `MAX_TOTAL_PARTS_PER_REQUEST` | 200    | Total parts per request    |
| `MAX_PART_SIZE`               | 5 GB   | S3 part size limit         |

### Retry Configuration

| Constant                           | Default | Env Var Override               |
| ---------------------------------- | ------- | ------------------------------ |
| `DEFAULT_MAX_RETRY_ATTEMPTS`       | 5       | `VAMS_CLI_MAX_RETRY_ATTEMPTS`  |
| `DEFAULT_INITIAL_RETRY_DELAY`      | 1.0s    | `VAMS_CLI_INITIAL_RETRY_DELAY` |
| `DEFAULT_MAX_RETRY_DELAY`          | 60.0s   | `VAMS_CLI_MAX_RETRY_DELAY`     |
| `DEFAULT_RETRY_BACKOFF_MULTIPLIER` | 2.0     | -                              |
| `DEFAULT_RETRY_JITTER`             | 0.1     | -                              |

### Feature Switches

One constant per published feature switch, lane by lane: `constants.py` declares one per member of
`VAMS_APP_FEATURES` (`infra/common/vamsAppFeatures.ts`), `addon_constants.py` one per member of
`ADDON_APP_FEATURES` (`infra/common/addonAppFeatures.ts`) and `custom_constants.py` one per member of a
fork's `CUSTOM_APP_FEATURES`. `constants.py` re-exports every add-on name, so
`from vamscli.constants import FEATURE_PHYSNA_ADDON` keeps working. Together they are the set a deployment
publishes through `/secure-config`, and each lane must equal its enum in both directions — a missing
constant is a gate no command can name, and an extra one names a gate no deployment publishes.
`tests/test_constants_contract.py` parses the three enums and fails on either drift.

| Constant                                          | Value                             | Meaning                  |
| ------------------------------------------------- | --------------------------------- | ------------------------ |
| `FEATURE_GOVCLOUD`                                | `"GOVCLOUD"`                      | Restricted partition     |
| `FEATURE_ALLOWUNSAFEEVAL`                         | `"ALLOWUNSAFEEVAL"`               | CSP allows `unsafe-eval` |
| `FEATURE_LOCATIONSERVICES`                        | `"LOCATIONSERVICES"`              | Location Service enabled |
| `FEATURE_ALBDEPLOY`                               | `"ALBDEPLOY"`                     | ALB deployment mode      |
| `FEATURE_CLOUDFRONTDEPLOY`                        | `"CLOUDFRONTDEPLOY"`              | CloudFront deployment    |
| `FEATURE_NOOPENSEARCH`                            | `"NOOPENSEARCH"`                  | OpenSearch disabled      |
| `FEATURE_AUTHPROVIDER_COGNITO`                    | `"AUTHPROVIDER_COGNITO"`          | Cognito auth enabled     |
| `FEATURE_AUTHPROVIDER_COGNITO_SAML`               | `"AUTHPROVIDER_COGNITO_SAML"`     | Cognito SAML auth        |
| `FEATURE_AUTHPROVIDER_COGNITO_OIDC`               | `"AUTHPROVIDER_COGNITO_OIDC"`     | Cognito OIDC auth        |
| `FEATURE_AUTHPROVIDER_EXTERNALOAUTHIDP`           | `"AUTHPROVIDER_EXTERNALOAUTHIDP"` | External OAuth IDP       |
| `FEATURE_PHYSNA_ADDON` (`addon_constants.py`)     | `"PHYSNA_ADDON"`                  | Physna add-on features   |
| `FEATURE_DEADLINECLOUD_PIPELINES`                 | `"DEADLINECLOUD_PIPELINES"`       | Deadline Cloud exec type |
| `FEATURE_EDH_WORKSTATIONS` (`addon_constants.py`) | `"EDH_WORKSTATIONS"`              | EDH Workstations add-on  |
| `FEATURE_EDH_CONTAINERS` (`addon_constants.py`)   | `"EDH_CONTAINERS"`                | EDH container jobs       |
| `FEATURE_EDH_AMI_RECIPES` (`addon_constants.py`)  | `"EDH_AMI_RECIPES"`               | EDH AMI recipe builds    |

---

## File Reference

| File                                 | Purpose                                                |
| ------------------------------------ | ------------------------------------------------------ |
| `vamscli/main.py`                    | CLI entry point, command registration, global options  |
| `vamscli/version.py`                 | Version constants (`__version__`, `CLI_VERSION`)       |
| `vamscli/constants.py`               | All API endpoints, limits, config constants            |
| `vamscli/auth/base.py`               | BaseAuthenticator abstract class                       |
| `vamscli/auth/cognito.py`            | Cognito SRP + USER_PASSWORD_AUTH implementation        |
| `vamscli/utils/api_client.py`        | APIClient: HTTP, retries, error mapping                |
| `vamscli/utils/profile.py`           | ProfileManager: multi-profile config management        |
| `vamscli/utils/exceptions.py`        | Two-tier exception hierarchy (~140 classes)            |
| `vamscli/utils/global_exceptions.py` | `@handle_global_exceptions()` decorator                |
| `vamscli/utils/decorators.py`        | `@requires_setup_and_auth`, `@requires_feature`        |
| `vamscli/utils/json_output.py`       | `output_result()`, `output_error()`, `output_status()` |
| `vamscli/utils/logging.py`           | Rotating file logger, verbose mode support             |
| `vamscli/utils/retry_config.py`      | Retry config with env var overrides                    |
| `vamscli/utils/features.py`          | Feature switch utilities                               |
| `vamscli/utils/upload_manager.py`    | Multi-part upload orchestration                        |
| `vamscli/utils/download_manager.py`  | Parallel download orchestration                        |
| `vamscli/utils/file_processor.py`    | File validation and processing                         |
| `vamscli/utils/sync_engine.py`       | Sync plan computation (local/remote diff)              |
| `vamscli/utils/vamsignore.py`        | `.vamsignore` gitignore-style pattern matching         |
| `vamscli/utils/glb_combiner.py`      | GLB binary file combination                            |
| `tests/conftest.py`                  | Shared fixtures: mock_logging, generic_command_mocks   |
