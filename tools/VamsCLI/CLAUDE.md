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
    main.py                  # CLI entry point, @click.group, command registration
    version.py               # __version__ and CLI_VERSION
    constants.py             # All API endpoints, limits, config constants
    auth/
      base.py                # BaseAuthenticator abstract class
      cognito.py             # CognitoAuthenticator (SRP, USER_PASSWORD_AUTH)
    commands/
      setup.py               # Initial CLI configuration
      auth.py                # Login, change-password, forgot-password, logout, status, refresh, set-override, routes (API route listing)
      apiKey.py              # API key management (admin) + 'user' sub-group (self-service own keys)
      assets.py              # Asset CRUD operations + lifecycle history lookup (history)
      asset_version.py       # Asset version management (list, get, create, update, archive, unarchive, revert)
      asset_links.py         # Asset relationship/link management
      file.py                # File management (upload, download, move, copy)
      profile.py             # Multi-profile management
      database.py            # Database CRUD operations
      tag.py                 # Tag management
      tag_type.py            # Tag type management
      metadata.py            # Metadata operations (unified API)
      metadata_schema.py     # Metadata schema management
      features.py            # Feature switch inspection
      search.py              # Search (OpenSearch integration)
      sync.py                # Directory sync (sync file push/pull)
      workflow.py            # Workflow execution
      user.py                # Cognito user management
      roleUserConstraints.py # Roles, constraints, user-role assignment
      industry/
        industry.py          # Industry command group
        engineering/
          engineering.py     # Engineering sub-commands
          bom/               # Bill of Materials (Dynamic_BOM.py)
          plm/               # Product Lifecycle Management (plm.py)
        spatial/
          glb.py             # GLB file combination operations
    utils/
      api_client.py          # APIClient class (HTTP, retries, error mapping)
      profile.py             # ProfileManager (multi-profile, config dirs)
      exceptions.py          # Two-tier exception hierarchy (~60 classes)
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
    test_*.py                # ~25 test files (includes test_asset_version_new_commands.py)
```

### Command Groups (20 top-level)

All registered in `main.py` via `cli.add_command()`:

```
setup, auth, assets, asset-version, asset-links, file, profile, database,
tag, tag-type, metadata, metadata-schema, features, search, sync, workflow,
industry, user, role, api-key
```

Sync has a nested sub-command group:

-   `sync file push` / `sync file pull` -- directory synchronization with an asset (S3-sync-style size+mtime diff, `.vamsignore` support, archive/permanent-delete safeguards)

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
    VersionMismatchError
    RetryExhaustedError
    RateLimitExceededError
  BusinessLogicError           --> handled by individual commands
    APIError
    AssetError (+ 5 subclasses)
    DatabaseError (+ 5 subclasses)
    FileError (+ 14 subclasses)
    SyncError (+ 5 subclasses)
    TagError (+ 7 subclasses)
    AssetVersionError (+ 5 subclasses, includes AssetVersionArchiveError)
    AssetLinkError (+ 7 subclasses)
    SearchError (+ 5 subclasses)
    WorkflowError (+ 4 subclasses)
    CognitoUserError (+ 4 subclasses)
    RoleError (+ 4 subclasses)
    ConstraintError (+ 5 subclasses)
    UserRoleError (+ 4 subclasses)
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
-   HTTP 429: Exponential backoff with jitter, respects `Retry-After` header
-   HTTP 401: Auto-refresh token (Cognito only), fails immediately for override tokens
-   HTTP 403: Distinguishes expired tokens vs. permission denied
-   HTTP status to exception mapping in response handlers:
    -   `404` --> `NotFoundError` variants
    -   `409` --> `AlreadyExistsError` variants
    -   `403` --> `PermissionDeniedError` or `TokenExpiredError`
    -   `429` --> `RateLimitExceededError` / `RetryExhaustedError`

**Rules**:

1. Always pass `profile_manager` to `APIClient` constructor
2. Never make raw `requests` calls -- always go through `APIClient`
3. API endpoint constants are in `constants.py` as format strings (e.g., `API_DATABASE_ASSETS = "/database/{databaseId}/assets"`)
4. New API methods go in `api_client.py`, not in command files

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

### 5. Decorator Usage

Three decorators in `utils/decorators.py` and `utils/global_exceptions.py`:

| Decorator                         | Purpose                                  | Use When                                               |
| --------------------------------- | ---------------------------------------- | ------------------------------------------------------ |
| `@requires_setup_and_auth`        | Validates setup, checks API, logs timing | All authenticated commands (default)                   |
| `@requires_feature(feature_name)` | Gates behind feature switches            | Feature-gated commands (e.g., Cognito user management) |
| `@handle_global_exceptions()`     | Top-level infrastructure error handler   | Only on `cli()` group and `main()` in main.py          |

**Decorator stacking order** (bottom of stack executes first):

```python
@domain.command()
@click.option(...)
@click.pass_context
@requires_setup_and_auth       # Validates setup/auth before command runs
def my_command(ctx, ...):
```

For feature-gated commands:

```python
@domain.command()
@click.option(...)
@click.pass_context
@requires_setup_and_auth
@requires_feature('AUTHPROVIDER_COGNITO')
def my_feature_command(ctx, ...):
```

**Rules**:

1. `@handle_global_exceptions()` is ONLY applied at the top level (`cli()` and `main()`)
2. `@requires_setup_and_auth` goes on every command that needs API access
3. `@requires_feature()` goes AFTER `@requires_setup_and_auth` in the stack (closer to function)
4. Never add `@requires_api_access` to new commands -- it is legacy, use `@requires_setup_and_auth`

### 6. Profile Management

`ProfileManager` in `utils/profile.py` manages multi-profile configuration:

-   Platform-specific config directories:
    -   Windows: `%APPDATA%/vamscli/`
    -   macOS: `~/Library/Application Support/vamscli/`
    -   Linux: `~/.config/vamscli/`
-   Files per profile: `config.json`, `auth_profile.json`, `credentials.json`
-   Active profile tracked in `active_profile.json`
-   Profile name validation: 3-50 chars, `[a-zA-Z0-9_-]`, reserved names: `help`, `version`, `list`

**Rules**:

1. Always obtain `ProfileManager` via `get_profile_manager_from_context(ctx)` in commands
2. Never hardcode profile paths -- use `ProfileManager` methods
3. The default profile name is `"default"` (constant `DEFAULT_PROFILE_NAME`)

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

1. New endpoints go in `constants.py`, never hardcoded in commands or api_client
2. Upload/download limits are constants, not magic numbers
3. Feature switch names are constants (e.g., `FEATURE_GOVCLOUD = "GOVCLOUD"`)
4. Retry config defaults are constants, overridable via env vars

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

Override tokens (external auth):

-   Set via `vamscli auth set-override --token <jwt>`
-   Pre-flight expiry check before each API request
-   No auto-refresh -- fails immediately on 401

### 9. Unicode and Terminal Encoding

VamsCLI uses Unicode characters (e.g., `✓`, `✗`) in CLI output for status indicators. On Windows, the default console encoding (`charmap`/`cp1252`) cannot render these characters and will raise encoding errors.

**Requirements**:

-   Use a UTF-8 capable terminal (Windows Terminal, VS Code terminal, etc.)
-   Or set `PYTHONIOENCODING=utf-8` environment variable before running the CLI
-   Linux/macOS terminals are typically UTF-8 by default and do not require additional configuration

**Rules**:

1. Unicode characters in CLI output are intentional and should not be replaced with ASCII
2. When testing CLI commands in bash/shell scripts, set `export PYTHONIOENCODING=utf-8`
3. Document the UTF-8 requirement in user-facing README and installation guides

---

## Testing

### Framework and Configuration

-   **Framework**: pytest with Click's `CliRunner`
-   **Test files**: `tests/test_*.py` (~24 files)
-   **Shared fixtures**: `tests/conftest.py`

### Key Fixtures (conftest.py)

| Fixture                    | Scope    | Purpose                                        |
| -------------------------- | -------- | ---------------------------------------------- |
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
2. The `command_module` parameter must match the filename in `commands/` (e.g., `'database'`, `'assets'`, `'tag_type'`)
3. For nested modules like `roleUserConstraints`, use the actual module name: `'roleUserConstraints'`
4. Use `no_setup_command_mocks` for testing setup-required error paths
5. To disable the autouse `mock_logging`, mark the test: `@pytest.mark.no_mock_logging`

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
```

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

9. **Evaluate external tool integrations** for required updates. When CLI commands, parameters, output formats, or authentication flows change, review and update the external connectors at `tools/ExternalIntegrations/` that wrap the CLI:
    - `isaacsim_vams_integration/` -- Python subprocess wrapper (`vams_cli_service.py`)
    - `arcgispro-connector-for-vams/` -- C# subprocess wrapper (`Services/VamsCliService.cs`)

### Adding a New Exception Class

1. Choose the correct tier:

    - `GlobalInfrastructureError` for system-wide issues (auth, setup, connectivity)
    - `BusinessLogicError` for domain-specific command failures

2. Add the class in the correct section of `utils/exceptions.py`

3. If `GlobalInfrastructureError`: add handler in `utils/global_exceptions.py`

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
2. Construct `ProfileManager()` directly in commands — ignores `--profile`. Use `get_profile_manager_from_context(ctx)` (Rule 6).
3. Catch `GlobalInfrastructureError` in commands — must propagate to the global handler (Rule 1).
4. Hardcode API endpoints in commands or `api_client` — define a format-string constant in `constants.py` (Rule 7).
5. Make raw `requests` calls — always route through `APIClient` (Rule 3).
6. Manually patch `ProfileManager`/`APIClient` injection points in tests — use `generic_command_mocks(module)` (Testing section).
7. Use the legacy `@requires_api_access` decorator on new commands — use `@requires_setup_and_auth` (Rule 5).
8. Use magic numbers for size/count limits — import the named constant from `constants.py` (Rule 7, Key Constants).
9. Ship a command that produces output without `--json-output` support — every output-producing command accepts `json_output: bool` (Rule 4).
10. Forget the `output_error(...); raise click.ClickException(str(e))` pair — `output_error` exits in JSON mode; the raise handles CLI mode (Rule 4).

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

| Constant                                | Value                             | Meaning              |
| --------------------------------------- | --------------------------------- | -------------------- |
| `FEATURE_GOVCLOUD`                      | `"GOVCLOUD"`                      | GovCloud deployment  |
| `FEATURE_ALBDEPLOY`                     | `"ALBDEPLOY"`                     | ALB deployment mode  |
| `FEATURE_NOOPENSEARCH`                  | `"NOOPENSEARCH"`                  | OpenSearch disabled  |
| `FEATURE_AUTHPROVIDER_COGNITO`          | `"AUTHPROVIDER_COGNITO"`          | Cognito auth enabled |
| `FEATURE_AUTHPROVIDER_COGNITO_SAML`     | `"AUTHPROVIDER_COGNITO_SAML"`     | Cognito SAML auth    |
| `FEATURE_AUTHPROVIDER_EXTERNALOAUTHIDP` | `"AUTHPROVIDER_EXTERNALOAUTHIDP"` | External OAuth IDP   |

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
| `vamscli/utils/exceptions.py`        | Two-tier exception hierarchy (~60 classes)             |
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
