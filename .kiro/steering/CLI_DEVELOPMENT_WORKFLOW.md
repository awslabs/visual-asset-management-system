# VamsCLI Development Workflow & Rules

This document provides comprehensive guidelines for developing and extending VamsCLI. Follow these rules to ensure consistency, quality, and maintainability across all implementations.

## 🏗️ **Architecture Overview**

### **File Structure Standards**

```
tools/VamsCLI/
├── vamscli/
│   ├── main.py              # CLI entry point with global options
│   ├── version.py           # Version management
│   ├── constants.py         # ALL API endpoints and configuration constants
│   ├── auth/                # Authentication providers
│   │   ├── __init__.py
│   │   ├── base.py          # Base interfaces
│   │   └── cognito.py       # Specific implementations
│   ├── commands/            # Command implementations (one file per command group)
│   │   ├── __init__.py
│   │   ├── setup.py         # Setup commands
│   │   └── auth.py          # Authentication commands
│   └── utils/               # Shared utilities
│       ├── __init__.py
│       ├── api_client.py    # HTTP client and API methods
│       ├── profile.py       # Configuration and profile management
│       └── exceptions.py    # Custom exception hierarchy
├── tests/                   # Test files (mirror command structure)
├── setup.py                 # Package configuration
├── pyproject.toml          # Modern Python packaging
└── README.md               # User documentation
```

## 📋 **Development Workflow Checklist**

### **Phase 1: Pre-Implementation**

-   [ ] **Analyze Requirements**: Understand the new feature/command requirements
-   [ ] **Check Architecture**: Ensure the new feature fits existing patterns
-   [ ] **Plan API Integration**: Identify any new API endpoints needed
-   [ ] **Review Dependencies**: Check if new dependencies are needed

### **Phase 2: Implementation**

#### **Step 1: Constants & Configuration**

-   [ ] **Add API Endpoints**: Add all new API endpoints to `constants.py`
-   [ ] **Add Configuration**: Add any new configuration constants
-   [ ] **Add Feature Switch Constants**: Add feature switch constants if referencing features
-   [ ] **Update Version**: Update version if this is a new release

#### **Step 2: Exception Handling**

-   [ ] **Classify Exception Type**: Determine if exception is global infrastructure or command-specific business logic
-   [ ] **Add Custom Exceptions**: Create specific exceptions in `utils/exceptions.py` under appropriate category
-   [ ] **Update Exception Imports**: Add new exceptions to `utils/__init__.py`
-   [ ] **Plan Error Messages**: Design user-friendly error messages
-   [ ] **Follow Exception Hierarchy**: Use `GlobalInfrastructureError` or `BusinessLogicError` base classes

#### **Step 3: API Client Enhancement**

-   [ ] **Add API Methods**: Add new API methods to `utils/api_client.py`
-   [ ] **Follow Naming Convention**: Use descriptive method names (e.g., `call_login_profile`)
-   [ ] **Include Error Handling**: Handle HTTP errors appropriately
-   [ ] **Add Documentation**: Include docstrings with examples

#### **Step 4: Command Implementation**

-   [ ] **Create/Update Command File**: Add commands to appropriate file in `commands/`
-   [ ] **Apply Decorators**: Use `@requires_setup_and_auth` for new API-dependent commands (or `@requires_api_access` for backward compatibility)
-   [ ] **Remove Duplicated Setup Checks**: Don't duplicate setup/config validation (handled by decorators)
-   [ ] **Handle Only Business Logic Exceptions**: Focus on domain-specific error handling
-   [ ] **Include Help Text**: Comprehensive help with examples
-   [ ] **Handle User Input**: Validate and sanitize user inputs

#### **Step 5: Main CLI Integration**

-   [ ] **Register Commands**: Add new commands to `main.py`
-   [ ] **Update Global Options**: Add any new global options if needed
-   [ ] **Update Exception Handling**: Include new exceptions in global handler

### **Phase 3: Quality Assurance**

#### **Step 6: Testing**

-   [ ] **Write Unit Tests**: Create tests in `tests/` directory
-   [ ] **Test Success Cases**: Test normal operation flows
-   [ ] **Test Error Cases**: Test all error scenarios
-   [ ] **Test CLI Interface**: Use `CliRunner` for command testing
-   [ ] **Run All Tests**: Ensure `pytest` passes

#### **Step 7: Documentation**

-   [ ] **Update README**: Add new commands to README.md
-   [ ] **Update Help Text**: Ensure all help text is comprehensive
-   [ ] **Add Examples**: Include usage examples in documentation
-   [ ] **Update Installation Guide**: If installation process changes

#### **Step 8: Code Quality**

-   [ ] **Run Black**: Format code with `black vamscli/`
-   [ ] **Run MyPy**: Type check with `mypy vamscli/`
-   [ ] **Check Imports**: Ensure all imports are properly organized
-   [ ] **Review Error Messages**: Ensure user-friendly error messages

## 🔧 **Implementation Standards**

### **API Endpoint Management**

#### **Rule 1: All API Endpoints in Constants**

```python
# ✅ CORRECT - Add to constants.py
API_LOGIN_PROFILE = "/auth/loginProfile"
API_USER_ASSETS = "/api/user/assets"
API_ASSET_UPLOAD = "/api/assets/upload"

# ❌ INCORRECT - Don't hardcode in methods
def get_user_assets(self):
    return self.get("/api/user/assets")  # BAD
```

#### **Rule 2: Import Constants in API Client**

```python
# ✅ CORRECT - Import from constants
from ..constants import API_LOGIN_PROFILE, API_USER_ASSETS

# ✅ CORRECT - Use constants in methods
def call_login_profile(self, user_id: str):
    endpoint = f"{API_LOGIN_PROFILE}/{user_id}"
    return self.get(endpoint)
```

### **Command Implementation Standards**

#### **Rule 3: Command File Organization**

```python
# ✅ CORRECT - One command group per file
# commands/assets.py
@click.group()
def assets():
    """Asset management commands."""
    pass

@assets.command()
@requires_setup_and_auth
def list_assets():
    """List user assets."""
    pass
```

#### **Rule 4: Required Decorators and Imports**

```python
# ✅ CORRECT - Always import decorators from utils.decorators
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context

# ✅ CORRECT - Always use @requires_setup_and_auth for API commands
@auth.command()
@click.option('-u', '--username', required=True)
@click.pass_context
@requires_setup_and_auth
def login(ctx: click.Context, username: str):
    """Authenticate with VAMS."""
    # Use the imported helper function
    profile_manager = get_profile_manager_from_context(ctx)
    pass

# ❌ INCORRECT - Don't create new decorator functions in command files
def requires_setup_and_auth(func):  # VIOLATION - use existing decorator
    pass

# ❌ INCORRECT - Don't create new helper functions in command files
def get_profile_manager_from_context(ctx):  # VIOLATION - use existing helper
    pass

# ✅ CORRECT - Include comprehensive help
@click.command()
def my_command():
    """
    Brief description of what the command does.

    Longer description with details about the command's functionality.

    Examples:
        vamscli my-command --option value
        vamscli my-command --help
    """
    pass
```

### **Error Handling Standards**

#### **Rule 5: Custom Exception Hierarchy**

```python
# ✅ CORRECT - Create specific exceptions
class AssetNotFoundError(VamsCLIError):
    """Raised when an asset is not found."""
    pass

# ✅ CORRECT - Use specific exceptions
if not asset_exists:
    raise AssetNotFoundError(f"Asset '{asset_id}' not found")
```

#### **Rule 6: User-Friendly Error Messages**

```python
# ✅ CORRECT - Clear, actionable error messages with JSON output support
from ..utils.json_output import output_error

@command.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
def my_command(json_output: bool):
    try:
        result = api_client.get_asset(asset_id)
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Error",
            helpful_message="Use 'vamscli assets list' to see available assets."
        )
        raise click.ClickException(str(e))

# Note: For styled output, use click.secho instead of click.echo(click.style(...))
# ✅ CORRECT - Using click.secho
click.secho("✓ Success!", fg='green', bold=True)
click.secho("⚠️  Warning!", fg='yellow', bold=True)
click.secho("✗ Error!", fg='red', bold=True, err=True)

# ❌ INCORRECT - Don't use click.echo with click.style
click.echo(click.style("✓ Success!", fg='green', bold=True))  # Use click.secho instead
```

### **Testing Standards**

#### **Rule 7: Comprehensive Test Coverage**

```python
# ✅ CORRECT - Test class organization
class TestAssetCommands:
    """Test asset management commands."""

    def test_list_assets_success(self):
        """Test successful asset listing."""
        runner = CliRunner()
        result = runner.invoke(cli, ['assets', 'list'])
        assert result.exit_code == 0

    def test_list_assets_no_auth(self):
        """Test asset listing without authentication."""
        runner = CliRunner()
        result = runner.invoke(cli, ['assets', 'list'])
        assert result.exit_code == 1
        assert 'Authentication required' in result.output
```

## 📝 **Development Templates**

### **New Command Template**

```python
"""[Command group] commands for VamsCLI."""

import click

from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import [SpecificBusinessLogicError]


@click.group()
def [command_group]():
    """[Command group description]."""
    pass


@[command_group].command()
@click.option('-u', '--user-id', help='User ID if required')
@click.option('--option-name', help='Option description')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def [command_name](ctx: click.Context, user_id: str, option_name: str, json_output: bool):
    """
    Brief command description.

    Detailed description of what the command does and how to use it.

    Examples:
        vamscli [command_group] [command_name] --option-name value
        vamscli [command_group] [command_name] --option-name value --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Status messages only in CLI mode
        output_status("Processing request...", json_output)

        # Focus on business logic only
        result = api_client.[api_method]()

        # Output result (JSON or CLI formatted)
        output_result(
            result,
            json_output,
            success_message="✓ Operation successful!",
            cli_formatter=lambda r: f"Details: {r}"
        )

        return result

    except [SpecificBusinessLogicError] as e:
        # Only handle command-specific business logic errors
        output_error(
            e,
            json_output,
            error_type="[Error Type]",
            helpful_message="Use 'vamscli [related-command]' for more information."
        )
        raise click.ClickException(str(e))
```

### **New API Method Template**

```python
def [api_method_name](self, [parameters]) -> [ReturnType]:
    """
    [Method description].

    Args:
        [parameter]: [Description]

    Returns:
        [Description of return value]

    Raises:
        [SpecificError]: [When this error occurs]
        APIError: [When API calls fail]
    """
    try:
        endpoint = f"{[API_CONSTANT]}/{[parameter]}"
        response = self.[http_method](endpoint, include_auth=True)

        return response.json()

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise [SpecificError](f"[Resource] not found: {[parameter]}")
        elif e.response.status_code in [401, 403]:
            # Handle authentication errors
            raise AuthenticationError(f"Authentication failed: {e}")
        else:
            raise APIError(f"[API operation] failed: {e}")

    except Exception as e:
        raise APIError(f"Failed to [operation]: {e}")
```

### **Test Template**

```python
"""Test [command group] functionality."""

import json
import pytest
import click
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    [SpecificError], AuthenticationError, APIError
)


# File-level fixtures for command-group-specific testing patterns
@pytest.fixture
def command_group_command_mocks(generic_command_mocks):
    """Provide command-group-specific command mocks.

    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for command-group command testing.

    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('command_module')


@pytest.fixture
def command_group_no_setup_mocks(no_setup_command_mocks):
    """Provide command-group command mocks for no-setup scenarios.

    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('command_module')


class Test[CommandGroup]Command:
    """Test [command group] [command] command."""

    def test_[command]_help(self, cli_runner):
        """Test [command] command help."""
        result = cli_runner.invoke(cli, ['[command-group]', '[command]', '--help'])
        assert result.exit_code == 0
        assert '[Expected help text]' in result.output
        assert '--option' in result.output

    def test_[command]_success(self, cli_runner, command_group_command_mocks):
        """Test successful [command] execution."""
        with command_group_command_mocks as mocks:
            mocks['api_client'].api_method.return_value = {
                'success': True,
                'message': 'Operation completed successfully'
            }

            result = cli_runner.invoke(cli, [
                '[command-group]', '[command]',
                '--option', 'value'
            ])

            assert result.exit_code == 0
            assert '✓ [Expected success message]' in result.output

            # Verify API call
            mocks['api_client'].api_method.assert_called_once()

    def test_[command]_no_setup(self, cli_runner, command_group_no_setup_mocks):
        """Test [command] without setup."""
        with command_group_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                '[command-group]', '[command]',
                '--option', 'value'
            ])

            assert result.exit_code == 1
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output

    def test_[command]_error_handling(self, cli_runner, command_group_command_mocks):
        """Test [command] error handling."""
        with command_group_command_mocks as mocks:
            mocks['api_client'].api_method.side_effect = [SpecificError]("Specific error occurred")

            result = cli_runner.invoke(cli, [
                '[command-group]', '[command]',
                '--option', 'value'
            ])

            assert result.exit_code == 1
            assert '✗ [Error Type]' in result.output
            assert 'Specific error occurred' in result.output


class Test[CommandGroup]UtilityFunctions:
    """Test [command group] utility functions."""

    def test_utility_function(self):
        """Test utility function."""
        from vamscli.commands.[command_module] import utility_function

        result = utility_function(['input', 'data'])
        assert result == ['expected', 'output']


if __name__ == '__main__':
    pytest.main([__file__])
```

## 🚨 **Mandatory Rules**

### **Rule 1: API Endpoints MUST be in Constants**

```python
# ✅ ALWAYS DO THIS - Add to constants.py
API_USER_ASSETS = "/api/user/assets"
API_ASSET_UPLOAD = "/api/assets/upload"
API_WORKFLOW_STATUS = "/api/workflows/status"

# ❌ NEVER DO THIS - Hardcoded endpoints
def get_assets(self):
    return self.get("/api/user/assets")  # VIOLATION
```

### **Rule 2: Commands MUST Use @requires_setup_and_auth**

```python
# ✅ CORRECT - API commands must have decorator
@assets.command()
@requires_setup_and_auth
def list_assets():
    """List user assets."""
    pass

# ❌ INCORRECT - Missing decorator for API command
@assets.command()
def list_assets():  # VIOLATION - needs @requires_setup_and_auth
    pass
```

### **Rule 3: Authentication/Setup Checks MUST be Included**

```python
# ✅ CORRECT - Always check setup
def my_command():
    profile_manager = ProfileManager()

    if not profile_manager.has_config():
        raise click.ClickException(
            "Configuration not found. Please run 'vamscli setup <api-gateway-url>' first."
        )
```

### **Rule 4: Error Handling MUST be Comprehensive**

```python
# ✅ CORRECT - Handle specific errors with JSON output support
from ..utils.json_output import output_error

@command.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
def my_command(json_output: bool):
    try:
        result = api_client.get_asset(asset_id)
    except AssetNotFoundError as e:
        output_error(e, json_output, error_type="Asset Error")
        raise click.ClickException(str(e))
    except AuthenticationError as e:
        output_error(e, json_output, error_type="Auth Error")
        raise click.ClickException(str(e))
    except Exception as e:
        output_error(e, json_output, error_type="Unexpected Error")
        raise click.ClickException(str(e))
```

### **Rule 5: Tests MUST be Written**

```python
# ✅ CORRECT - Test both success and failure
def test_command_success(self):
    """Test successful command execution."""
    # Test implementation

def test_command_no_setup(self):
    """Test command without setup."""
    # Test implementation

def test_command_auth_failure(self):
    """Test command with authentication failure."""
    # Test implementation
```

### **Rule 6: Profile Support MUST be Implemented for New Commands**

All new commands that require API access or configuration MUST support the profile system:

#### **Profile Integration Requirements:**

-   [ ] **Add @click.pass_context**: All profile-aware commands need context access
-   [ ] **Use get_profile_manager_from_context()**: Get ProfileManager from context
-   [ ] **Update Error Messages**: Include profile name in error messages
-   [ ] **Test Profile Integration**: Test commands with different profiles

#### **Profile-Aware Command Pattern:**

```python
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.json_output import output_status, output_result, output_error

@command_group.command()
@click.option('--required-param', required=True, help='Required parameter')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def new_command(ctx: click.Context, required_param: str, json_output: bool):
    """Command description with profile support."""
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Status messages only in CLI mode
        output_status("Processing request...", json_output)

        # Focus on business logic only
        result = api_client.some_operation(required_param)

        # Output result (JSON or CLI formatted)
        output_result(result, json_output, success_message="✓ Operation successful!")

    except SomeBusinessLogicError as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="Business Logic Error")
        raise click.ClickException(str(e))
```

#### **Profile System Architecture:**

```
~/.config/vamscli/
├── profiles/
│   ├── default/           # Default profile name
│   ├── production/        # Production environment profile (example)
│   └── staging/          # Staging environment profile (example)
└── active_profile.json   # Tracks current active profile
```

### **Rule 7: Documentation MUST be Updated Across All Files**

When making changes, update the appropriate documentation files:

#### **Documentation File Mapping:**

All CLI documentation lives in the Docusaurus documentation site at `documentation/docusaurus-site/docs/cli/`.

**Docusaurus documentation site** (`documentation/docusaurus-site/docs/cli/`):

-   **Command changes** → Update the relevant page in `documentation/docusaurus-site/docs/cli/commands/`
-   **New command group** → Create new page in `cli/commands/`, update `cli/command-reference.md`, and add to `sidebars.ts`
-   **Installation/auth changes** → Update `cli/getting-started.md` and `cli/installation.md`
-   **Automation patterns** → Update `cli/automation.md`
-   **API changes** → Update relevant `documentation/docusaurus-site/docs/api/` page and `documentation/VAMS_API.yaml`
-   **Permission changes** → Update `documentation/docusaurus-site/docs/concepts/permissions-model.md`

**Docusaurus documentation style**: Use `:::note`/`:::warning` admonitions, escape `\{curly braces\}` outside code blocks, `bash` language tags on code blocks. See `documentation/CLAUDE.md` for the full documentation style guide.

#### **Documentation Update Checklist:**

-   [ ] **Update Docusaurus CLI docs**: Add/modify in `documentation/docusaurus-site/docs/cli/commands/`
-   [ ] **Update CLI command reference index**: Update `documentation/docusaurus-site/docs/cli/command-reference.md` if new command group
-   [ ] **Update main README**: Update `tools/VamsCLI/README.md` if needed
-   [ ] **Update sidebars.ts**: If new pages were added to Docusaurus CLI section
-   [ ] **Build verification**: Run `cd documentation/docusaurus-site && npm run build` to verify
-   [ ] **Cross-Reference Check**: Verify all internal documentation links work
-   [ ] **Accuracy Check**: Ensure all documented features actually exist in code

#### **New Documentation Structure:**

```
tools/VamsCLI/
├── docs/
│   ├── commands/
│   │   ├── setup-auth.md          # Setup, auth, profile commands
│   │   ├── asset-management.md    # Assets, asset-version, asset-links commands
│   │   ├── file-operations.md     # File management commands
│   │   ├── database-admin.md      # Database commands
│   │   ├── tag-management.md      # Tag, tag-type commands
│   │   └── global-options.md      # Global options and JSON usage
│   │   └── ........md             # Any others that were generated afterwards
│   ├── troubleshooting/
│   │   ├── setup-auth-issues.md   # Setup and authentication problems
│   │   ├── asset-file-issues.md   # Asset and file operation problems
│   │   ├── database-tag-issues.md # Database and tag management problems
│   │   ├── network-config-issues.md # Network, proxy, SSL issues
│   │   └── general-troubleshooting.md # Debug mode, performance, etc.
│   │   └── .......md              # Any others that were generated afterwards
│   ├── INSTALLATION.md            # Installation methods and setup details
│   ├── AUTHENTICATION.md          # Authentication system details
│   └── DEVELOPMENT.md             # Development guidelines
└── README.md                      # Main entry point with overview and quick start
```

#### **Documentation Update Process:**

1. **Identify Command Group**: Determine which command group your changes affect
2. **Update Command Documentation**: Add/modify examples in appropriate `docs/commands/` file
3. **Update Troubleshooting**: Add error scenarios to appropriate `docs/troubleshooting/` file
4. **Update Cross-References**: Ensure internal links work across the new structure
5. **Test Documentation**: Verify all examples and links are accurate

#### **Documentation Structure Benefits:**

-   **Focused Content**: Each file covers a specific functional area
-   **Easier Maintenance**: Smaller files are easier to update and review
-   **Better Navigation**: Users can find relevant information faster
-   **Scalable**: Easy to add new command groups without restructuring
-   **Reduced Conflicts**: Multiple developers can work on different areas simultaneously

#### **When to Update CLI_DEVELOPMENT_WORKFLOW.md:**

-   Adding new mandatory rules for all commands
-   Changing system-wide patterns or standards
-   Adding new architectural requirements
-   Modifying core development processes
-   Adding new quality assurance requirements
-   Changing testing standards or patterns
-   Adding new security or compliance requirements

### **Rule 8: Documentation Structure MUST Follow New Organization**

When updating VamsCLI documentation, use the new organized structure:

#### **Documentation Update Guidelines:**

-   **Command changes** → Update or create relevant command guides in `docs/commands/` directory
-   **Troubleshooting changes** → Update or create relevant troubleshooting guides in `docs/troubleshooting/` directory
-   **Installation/setup process** → Update `docs/INSTALLATION.md`
-   **Authentication system** → Update `docs/AUTHENTICATION.md`
-   **Development process** → Update `docs/DEVELOPMENT.md`
-   **Major feature additions** → Update main `README.md`
-   **System-wide rule changes** → Update `CLI_DEVELOPMENT_WORKFLOW.md` (this file)

#### **Documentation Organization:**

-   **Command Documentation** (`docs/commands/`): Organize by CLI command groups (setup/auth, assets, files, database, tags, global options)
-   **Troubleshooting Documentation** (`docs/troubleshooting/`): Organize by problem categories (setup/auth issues, asset/file issues, database/tag issues, network/config issues, general troubleshooting)
-   **Supporting Documentation** (`docs/`): Installation, authentication, and development guides

### **Rule 9: Decorators MUST be Imported from utils.decorators**

All command files MUST use existing decorators and helper functions from the utils.decorators module:

#### **Decorator Import Requirements:**

-   [ ] **Import from utils.decorators**: Always import `requires_api_access` and `get_profile_manager_from_context`
-   [ ] **Never Create New Decorators**: Don't duplicate decorator functionality in command files
-   [ ] **Use Existing Helpers**: Use existing helper functions for common operations

#### **Correct Decorator Usage:**

```python
# ✅ CORRECT - Import existing decorators
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context

@command_group.command()
@click.pass_context
@requires_setup_and_auth
def my_command(ctx: click.Context):
    """Command with proper decorator usage."""
    profile_manager = get_profile_manager_from_context(ctx)
    # Command implementation...

# ❌ INCORRECT - Don't create new decorators in command files
def requires_setup_and_auth(func):  # VIOLATION
    """Don't do this - use existing decorator."""
    pass

# ❌ INCORRECT - Don't create new helper functions in command files
def get_profile_manager_from_context(ctx):  # VIOLATION
    """Don't do this - use existing helper."""
    pass
```

### **Rule 10: Test Mocking MUST Follow Established Patterns**

All tests MUST properly mock ProfileManager instances to avoid setup check failures:

#### **Test Mocking Requirements:**

-   [ ] **Use Fixtures First**: Prefer global fixtures from `conftest.py` for ProfileManager/APIClient mocking
-   [ ] **Use Patches When Needed**: Use `@patch` decorators for specific scenarios not covered by fixtures
-   [ ] **Mock Main ProfileManager**: Always mock `vamscli.main.ProfileManager` for ALL CLI tests
-   [ ] **Mock Decorator ProfileManager**: Mock `vamscli.utils.decorators.ProfileManager` for API command tests
-   [ ] **Mock Command ProfileManager**: Mock command-specific ProfileManager imports when needed
-   [ ] **Comprehensive Mocking**: Ensure all ProfileManager instances are mocked in tests

#### **Test Mocking Patterns:**

##### **Preferred Pattern - Using Fixtures:**

```python
# ✅ PREFERRED - Use global fixtures for standard scenarios
def test_command_success(self, cli_runner, generic_command_mocks):
    """Test successful command execution."""
    with generic_command_mocks('command_module') as mocks:
        mocks['api_client'].api_method.return_value = {'Items': []}
        result = cli_runner.invoke(cli, ['command-group', 'command'])
        assert result.exit_code == 0

# ✅ PREFERRED - Use no-setup fixtures for setup validation tests
def test_command_no_setup(self, cli_runner, no_setup_command_mocks):
    """Test command without setup."""
    with no_setup_command_mocks('command_module') as mocks:
        result = cli_runner.invoke(cli, ['command-group', 'command'])
        assert result.exit_code == 1
        assert 'Configuration not found' in result.output

# ✅ PREFERRED - Use basic fixtures for simple tests
def test_command_help(self, cli_runner):
    """Test command help."""
    result = cli_runner.invoke(cli, ['command-group', 'command', '--help'])
    assert result.exit_code == 0
    assert 'Expected help text' in result.output
```

##### **Alternative Pattern - Using Patches When Fixtures Don't Apply:**

```python
# ✅ ACCEPTABLE - Use patches for complex scenarios not covered by fixtures
@patch('vamscli.main.ProfileManager')
@patch('vamscli.utils.decorators.get_profile_manager_from_context')
@patch('vamscli.commands.database.get_profile_manager_from_context')
@patch('vamscli.utils.decorators.APIClient')
@patch('vamscli.commands.database.APIClient')
@patch('vamscli.commands.database.prompt_bucket_selection')
def test_create_with_bucket_prompt(self, mock_prompt, mock_cmd_api_client, mock_dec_api_client, mock_cmd_get_profile_manager, mock_dec_get_profile_manager, mock_main_profile_manager):
    """Test database creation with bucket selection prompt."""
    # Setup mocks for profile manager
    mock_profile_manager = Mock()
    mock_profile_manager.has_config.return_value = True
    mock_profile_manager.load_config.return_value = {'api_gateway_url': 'https://api.example.com'}
    mock_profile_manager.profile_name = 'default'
    mock_dec_get_profile_manager.return_value = mock_profile_manager
    mock_cmd_get_profile_manager.return_value = mock_profile_manager
    mock_main_profile_manager.return_value = mock_profile_manager

    # Mock APIClient instances
    mock_dec_client = Mock()
    mock_dec_client.check_api_availability.return_value = {'available': True}
    mock_dec_api_client.return_value = mock_dec_client

    mock_cmd_client = Mock()
    mock_cmd_api_client.return_value = mock_cmd_client
    mock_cmd_client.check_api_availability.return_value = {'available': True}
    mock_cmd_client.create_database.return_value = {'success': True}

    # Additional specific mocking
    mock_prompt.return_value = 'selected-bucket-uuid'

    # Test implementation...

# ✅ ACCEPTABLE - Use patches for simple parameter validation tests
@patch('vamscli.main.ProfileManager')
def test_commands_require_parameters(self, mock_main_profile_manager):
    """Test that commands require parameters where appropriate."""
    mock_profile_manager = Mock()
    mock_profile_manager.has_config.return_value = True
    mock_main_profile_manager.return_value = mock_profile_manager

    runner = CliRunner()
    result = runner.invoke(cli, ['command-group', 'command'])
    assert result.exit_code == 2  # Click parameter error
    assert 'Missing option' in result.output or 'required' in result.output.lower()

# ❌ INCORRECT - Don't use patches when fixtures are available
@patch('vamscli.main.ProfileManager')
@patch('vamscli.utils.decorators.get_profile_manager_from_context')
@patch('vamscli.commands.database.get_profile_manager_from_context')
@patch('vamscli.utils.decorators.APIClient')
@patch('vamscli.commands.database.APIClient')
def test_simple_list_command(self, ...):  # VIOLATION - use fixtures instead
    # 15+ lines of standard mock setup that fixtures already provide
```

#### **When to Use Fixtures vs Patches:**

##### **Use Fixtures When:**

-   Testing standard command success/failure scenarios
-   Testing no-setup scenarios
-   Testing basic CLI functionality
-   The test follows common ProfileManager/APIClient patterns

##### **Use Patches When:**

-   Testing complex scenarios requiring additional mocks (e.g., `click.prompt`, `builtins.open`)
-   Testing parameter validation that doesn't require full command execution
-   Testing specific edge cases that need custom mock configurations
-   The test requires mocking modules/functions not covered by fixtures

#### **Test Mocking Guidelines:**

-   **Help Tests**: Use `cli_runner` fixture for simple help tests
-   **Command Tests**: Use `generic_command_mocks` fixture for standard API command tests
-   **No-Setup Tests**: Use `no_setup_command_mocks` fixture for setup validation tests
-   **Complex Tests**: Use `@patch` decorators when additional mocking is needed beyond fixtures
-   **Integration Tests**: Use fixtures combined with patches as needed

#### **Critical Test Mocking Rule:**

**ALWAYS ensure `vamscli.main.ProfileManager` is mocked for ANY test that invokes CLI commands**, either through fixtures or explicit patches, because the main CLI has setup checks that will fail without proper mocking.

#### **Why This Mocking is Required:**

1. **Setup Check**: The main CLI has a setup check that validates configuration exists
2. **API Availability Check**: The `@requires_api_access` decorator checks API availability
3. **Profile Management**: Commands use profile managers for configuration and authentication
4. **Test Isolation**: Proper mocking ensures tests don't depend on external systems

### **Rule 11: CLI_DEVELOPMENT_WORKFLOW.md MUST be Updated for System-Wide Changes**

This workflow document itself MUST be updated whenever system-wide standards, implementations, or requirements change:

#### **System-Wide Changes Requiring Workflow Updates:**

-   [ ] **New Mandatory Rules**: Adding rules that all future commands must follow
-   [ ] **Architecture Changes**: Modifications to core system architecture or patterns
-   [ ] **Standard Implementation Changes**: Updates to how common patterns should be implemented
-   [ ] **New Quality Requirements**: Additional quality assurance or testing standards
-   [ ] **Security Standard Changes**: Updates to security patterns or requirements
-   [ ] **New Development Tools**: Addition of new required development tools or processes
-   [ ] **Template Updates**: Changes to command, API method, or test templates
-   [ ] **Exception Handling Changes**: Updates to error handling patterns or standards
-   [ ] **Documentation Standard Changes**: Modifications to documentation requirements or structure

#### **Workflow Update Process:**

1. **Identify Impact**: Determine which existing and future implementations are affected
2. **Update Rules**: Add or modify rules in this document
3. **Update Templates**: Modify code templates to reflect new standards
4. **Update Checklists**: Add new items to quality assurance checklists
5. **Update Examples**: Provide examples of new patterns or standards
6. **Communicate Changes**: Ensure all developers are aware of new requirements
7. **Validate Existing Code**: Review existing code against new standards if needed

#### **Examples of System-Wide Changes:**

-   **Profile System Addition**: Required all commands to support profiles (Rule 6)
-   **Decorator Standardization**: Required all commands to use existing decorators (Rule 9)
-   **Test Mocking Standards**: Required comprehensive ProfileManager mocking (Rule 10)
-   **New Decorator Requirements**: If a new decorator becomes mandatory for all commands
-   **Authentication Pattern Changes**: If the authentication validation pattern changes
-   **Error Handling Standards**: If new error handling patterns are required

#### **Cross-Steering File Synchronization:**

When updating this workflow document, also update the corresponding files:

1. Update **both** `.kiro/steering/CLI_DEVELOPMENT_WORKFLOW.md` and `.clinerules/workflows/CLI_DEVELOPMENT_WORKFLOW.md` (they must stay in sync)
2. Update **all** affected CLAUDE.md files (root, web/, backend/, infra/, tools/VamsCLI/, documentation/)
3. If the change affects other components, also update the relevant workflow files (BACKEND_CDK_DEVELOPMENT_WORKFLOW.md, CDK_DEVELOPMENT_WORKFLOW.md, WEB_DEVELOPMENT_WORKFLOW.md, DOCUMENTATION_WORKFLOW.md)

-   **Testing Framework Changes**: If testing patterns or requirements change
-   **Documentation Structure Changes**: If the documentation file structure changes

#### **Workflow Maintenance Responsibility:**

-   **Lead Developers**: Responsible for identifying when workflow updates are needed
-   **All Contributors**: Must follow the current workflow and suggest improvements
-   **Code Reviewers**: Must ensure new code follows the established workflow
-   **Documentation Maintainers**: Must keep workflow documentation current and accurate

This ensures that the development workflow remains current and that all developers follow the same standards and patterns.

### **Rule 12: Feature Switch Constants MUST be Defined in Constants File**

When referencing feature switches in commands or logic, these MUST be defined as constants:

#### **Feature Switch Requirements:**

-   [ ] **Add Feature Constants**: Add all feature switch names to `constants.py`
-   [ ] **Import Feature Constants**: Import constants in command files
-   [ ] **Use @requires_feature Decorator**: For commands that require specific features
-   [ ] **Manual Feature Checks**: Use `is_feature_enabled()` for conditional logic
-   [ ] **Test Feature Dependencies**: Test commands with and without required features

#### **Feature Switch Implementation Pattern:**

```python
# ✅ CORRECT - Add to constants.py
FEATURE_GOVCLOUD = "GOVCLOUD"
FEATURE_LOCATIONSERVICES = "LOCATIONSERVICES"
FEATURE_AUTHPROVIDER_COGNITO = "AUTHPROVIDER_COGNITO"

# ✅ CORRECT - Feature-dependent command
from ..utils.decorators import requires_feature
from ..constants import FEATURE_GOVCLOUD

@command_group.command()
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_GOVCLOUD, "GovCloud features are not enabled for this environment.")
def govcloud_command(ctx: click.Context):
    """Command that requires GovCloud feature."""
    pass

# ✅ CORRECT - Conditional feature logic
from ..utils.features import is_feature_enabled
from ..utils.json_output import output_info
from ..constants import FEATURE_LOCATIONSERVICES

@command_group.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def location_aware_command(ctx: click.Context, json_output: bool):
    """Command with location-aware functionality."""
    profile_manager = get_profile_manager_from_context(ctx)

    if is_feature_enabled(FEATURE_LOCATIONSERVICES, profile_manager):
        # Location services available
        output_info("Location services enabled - using enhanced features", json_output)
    else:
        # Fallback behavior
        output_info("Location services disabled - using basic features", json_output)

# ❌ INCORRECT - Don't hardcode feature names
@requires_feature("GOVCLOUD")  # VIOLATION - use FEATURE_GOVCLOUD constant
def bad_command():
    pass

if profile_manager.has_feature_switch("LOCATIONSERVICES"):  # VIOLATION - use constant
    pass
```

#### **Feature Switch Authentication Integration:**

Feature switches are automatically fetched and stored during authentication:

-   `vamscli auth login` - Fetches feature switches after successful Cognito authentication
-   `vamscli auth set-override` - Fetches feature switches after successful token validation
-   Feature switches are stored in the authentication profile JSON file
-   Use `vamscli auth status` to view current feature switches
-   Use `vamscli features list` to list all enabled features

#### **Authentication Profile Structure with Feature Switches:**

```json
{
    "access_token": "...",
    "user_id": "user@example.com",
    "expires_at": 1234567890,
    "feature_switches": {
        "raw": "GOVCLOUD,LOCATIONSERVICES,AUTHPROVIDER_COGNITO",
        "enabled": ["GOVCLOUD", "LOCATIONSERVICES", "AUTHPROVIDER_COGNITO"],
        "fetched_at": "2024-01-01T12:00:00Z"
    }
}
```

#### **Available Feature Switch Constants:**

Available constants are in `infra/common/vamsAppFeatures.ts`

#### **Feature Switch Development Workflow Location:**

This workflow document is located at `.clinerules/workflows/CLI_DEVELOPMENT_WORKFLOW.md` and should be referenced for all VamsCLI development tasks.

### **Rule 13: CLI Integration Testing MUST Use Main CLI Entry Point**

All CLI command tests MUST use the main CLI entry point to ensure proper integration and avoid namespace collisions:

#### **CLI Integration Requirements:**

-   [ ] **Import Main CLI**: Always import `cli` from `vamscli.main`
-   [ ] **Use CLI Entry Point**: Invoke commands through `cli_runner.invoke(cli, ['command-group', 'command'])`
-   [ ] **Avoid Direct Command Imports**: Don't import and invoke command groups directly
-   [ ] **Handle Namespace Collisions**: Be aware of Python built-in function shadowing

#### **CLI Integration Patterns:**

```python
# ✅ CORRECT - Use main CLI entry point
from vamscli.main import cli

def test_command_success(self, cli_runner, command_mocks):
    """Test successful command execution."""
    with command_mocks as mocks:
        result = cli_runner.invoke(cli, ['command-group', 'command', '--param', 'value'])
        assert result.exit_code == 0

# ❌ INCORRECT - Don't import command groups directly
from vamscli.commands.command_group import command_group

def test_command_success(self, cli_runner, command_mocks):
    """Test command execution - WRONG APPROACH."""
    with command_mocks as mocks:
        result = cli_runner.invoke(command_group, ['command', '--param', 'value'])  # VIOLATION
        assert result.exit_code == 0
```

#### **Namespace Collision Prevention:**

When implementing commands, be careful of Python built-in function shadowing:

```python
# ✅ CORRECT - Avoid namespace collisions
def create_command():
    # Use explicit reference to built-in list function
    tags_list = __builtins__['list'](tags)
    # OR import at module level to avoid collision
    from builtins import list as builtin_list
    tags_list = builtin_list(tags)

# ❌ INCORRECT - Can cause namespace collision
def create_command():
    tags_list = list(tags)  # May conflict if 'list' command exists in same module
```

#### **CLI Integration Benefits:**

-   **Proper Routing**: Ensures commands are routed correctly through the main CLI
-   **Setup Validation**: Leverages the main CLI's setup and validation logic
-   **Global Options**: Properly handles global CLI options and context
-   **Error Handling**: Uses the main CLI's error handling and exception management
-   **Integration Testing**: Tests the actual user experience through the main entry point

### **Rule 14: Exception Handling MUST Follow New Architecture**

All VamsCLI commands MUST follow the new exception handling architecture that separates global infrastructure concerns from command-specific business logic:

#### **Exception Handling Requirements:**

-   [ ] **Use Proper Decorator**: Use `@requires_setup_and_auth` for new commands (or `@requires_api_access` for backward compatibility)
-   [ ] **Remove Duplicated Setup Checks**: Don't duplicate setup/config validation in commands
-   [ ] **Handle Only Business Logic Exceptions**: Commands should only catch domain-specific errors
-   [ ] **Let Global Exceptions Bubble Up**: Allow infrastructure exceptions to be handled by main.py
-   [ ] **Follow Exception Hierarchy**: Use appropriate base classes (GlobalInfrastructureError vs BusinessLogicError)

#### **New Exception Handling Pattern:**

```python
# ✅ CORRECT - New streamlined pattern with JSON output support
from ..utils.json_output import output_status, output_result, output_error

@assets.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth  # Handles all global validations
def create(ctx: click.Context, json_output: bool, ...):
    """Create an asset."""
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Status messages only in CLI mode
        output_status("Creating asset...", json_output)

        # Focus on business logic only
        result = api_client.create_asset(data)

        # Output result (JSON or CLI formatted)
        output_result(result, json_output, success_message="✓ Asset created!")

    except AssetAlreadyExistsError as e:
        # Only handle command-specific business logic errors
        output_error(
            e,
            json_output,
            error_type="Asset Already Exists",
            helpful_message="Use 'vamscli assets get' to view the existing asset."
        )
        raise click.ClickException(str(e))

# ❌ INCORRECT - Old duplicated pattern
@assets.command()
@click.pass_context
@requires_api_access
def create(ctx: click.Context, ...):
    """Create an asset."""
    profile_manager = get_profile_manager_from_context(ctx)

    # VIOLATION - Duplicated setup check (handled by decorator now)
    if not profile_manager.has_config():
        profile_name = profile_manager.profile_name
        raise click.ClickException(f"Configuration not found...")

    try:
        # Business logic
        result = api_client.create_asset(data)

    except AssetAlreadyExistsError as e:
        # VIOLATION - Using click.echo instead of output_error
        click.echo(f"✗ Asset Already Exists: {e}")
        raise click.ClickException(str(e))
    except AuthenticationError as e:
        # VIOLATION - Global error handling in command (should be in main.py)
        click.echo(f"✗ Authentication Error: {e}")
        click.echo("Please run 'vamscli auth login' to re-authenticate.")
        raise click.ClickException(str(e))
```

#### **Exception Classification Guidelines:**

##### **Global Infrastructure Exceptions (handled in main.py):**

-   `SetupRequiredError`, `AuthenticationError`, `APIUnavailableError`
-   `ProfileError`, `ConfigurationError`, `OverrideTokenError`
-   `RetryExhaustedError`, `RateLimitExceededError`, `VersionMismatchError`

##### **Command-Specific Business Logic Exceptions (handled in commands):**

-   `AssetNotFoundError`, `AssetAlreadyExistsError`, `DatabaseNotFoundError`
-   `TagNotFoundError`, `FileUploadError`, `SearchQueryError`
-   Any domain-specific validation or operation errors

#### **Benefits of New Architecture:**

-   **90% Reduction in Code Duplication**: Common patterns handled once in decorators/main.py
-   **Clear Separation of Concerns**: Global vs. command-specific error handling
-   **Consistent User Experience**: Standardized error messages and handling
-   **Maintainability**: Changes to global patterns require updates in one place
-   **Testability**: Easier to test exception handling in isolation

### **Rule 15: Tests MUST Use Pytest Fixtures for Common Patterns**

All VamsCLI tests MUST use pytest fixtures to reduce code duplication for ProfileManager and APIClient mocking:

A caveat is for core logic unit tests or unit tests where the existing fixtures don't apply based on what is being tested.

#### **Fixture Location Priority:**

-   [ ] **Check Global Fixtures First**: Look for existing fixtures in these locations:
    -   `tools/VamsCLI/tests/conftest.py` (primary global fixtures)
    -   `tools/VamsCLI/conftest.py` (project-level fixtures)
    -   `tools/VamsCLI/tests/fixtures.py` (custom fixtures module)
-   [ ] **Use Existing Global Fixtures**: If ProfileManager/APIClient fixtures exist globally, use them
-   [ ] **Check File-Level Fixtures**: Look for existing fixtures within the specific test file
-   [ ] **Create New Fixtures**: If no suitable fixtures exist, create them following the established patterns

#### **Fixture Implementation Priority:**

1. **Use Global Fixtures**: Always prefer existing global fixtures from `conftest.py`
2. **Use File-Level Fixtures**: Use existing fixtures within the test file
3. **Create File-Level Fixtures**: Create new fixtures in the test file if none exist
4. **Consider Global Promotion**: If fixtures are used across multiple files, consider moving to `conftest.py`

#### **Available Global Fixtures:**

The following fixtures are available in `tools/VamsCLI/tests/conftest.py`:

-   `cli_runner`: Pre-configured CliRunner instance
-   `mock_profile_manager`: Standard ProfileManager mock with valid configuration
-   `mock_api_client`: Standard APIClient mock with API availability
-   `no_setup_profile_manager`: ProfileManager mock for no-setup scenarios
-   `generic_command_mocks`: Factory for creating comprehensive command mocks
-   `no_setup_command_mocks`: Factory for creating no-setup command mocks

#### **Fixture Usage Patterns:**

```python
# ✅ CORRECT - Use global fixtures for clean, maintainable tests
def test_command_success(self, cli_runner, generic_command_mocks):
    """Test successful command execution."""
    with generic_command_mocks('command_module') as mocks:
        mocks['api_client'].api_method.return_value = {'Items': []}
        result = cli_runner.invoke(cli, ['command-group', 'command'])
        assert result.exit_code == 0

# ✅ CORRECT - Use no-setup fixtures for setup validation tests
def test_command_no_setup(self, cli_runner, no_setup_command_mocks):
    """Test command without setup."""
    with no_setup_command_mocks('command_module') as mocks:
        result = cli_runner.invoke(cli, ['command-group', 'command'])
        assert result.exit_code == 1
        assert 'Configuration not found' in result.output

# ✅ CORRECT - Create file-level fixtures for command-specific patterns
@pytest.fixture
def command_group_command_mocks(generic_command_mocks):
    """Provide command-group-specific command mocks."""
    return generic_command_mocks('command_module')

def test_command_specific(self, cli_runner, command_group_command_mocks):
    """Test command-specific functionality."""
    with command_group_command_mocks as mocks:
        mocks['api_client'].api_method.return_value = {'success': True}
        result = cli_runner.invoke(cli, ['command-group', 'command', '--param', 'value'])
        assert result.exit_code == 0

# ❌ INCORRECT - Don't duplicate mock setup in every test
@patch('vamscli.main.ProfileManager')
@patch('vamscli.utils.decorators.APIClient')
def test_command_success(self, ...):  # VIOLATION - use fixtures instead
    # 15+ lines of repetitive mock setup code
```

#### **Fixture Benefits:**

-   **Reduce Code Duplication**: Eliminate repetitive ProfileManager and APIClient mock setup
-   **Improve Maintainability**: Single source of truth for mock configurations
-   **Enhance Readability**: Focus test methods on actual test logic rather than setup
-   **Standardize Patterns**: Consistent mock behavior across all tests
-   **Faster Development**: Pre-configured fixtures speed up test writing

#### **Global Fixture File Management:**

-   **`tools/VamsCLI/tests/conftest.py`**: Primary location for shared fixtures used across multiple test files
-   **Individual test files**: File-specific fixtures that are only used within that test file
-   **Fixture Documentation**: Document fixture purpose and usage in docstrings

### **Rule 16: Commands MUST Return Their Result Data**

All VamsCLI commands MUST explicitly return their result data after calling `output_result()`:

#### **Return Statement Requirements:**

-   [ ] **Always Return Result**: Add `return result` after `output_result()` call
-   [ ] **Enable Programmatic Use**: Allows commands to be invoked via `ctx.invoke()`
-   [ ] **Cross-Platform Consistency**: Prevents Click Sentinel object issues
-   [ ] **Standard Pattern**: Follows established VamsCLI command architecture

#### **Why This Is Critical:**

When commands are invoked programmatically using `ctx.invoke()`, Click needs an explicit return value to pass back to the caller. Without it, `ctx.invoke()` returns a Click `Sentinel` object instead of the actual result dictionary, causing errors like:

```
✗ Unexpected error: the JSON object must be str, bytes or bytearray, not Sentinel
```

This issue appears intermittently across different machines due to variations in Click/Python versions.

#### **Correct Return Pattern:**

```python
# ✅ CORRECT - Always return result after output_result()
@command.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def my_command(ctx: click.Context, json_output: bool):
    """Command that can be used both via CLI and programmatically."""
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        result = api_client.some_operation()

        output_result(
            result,
            json_output,
            success_message="✓ Operation successful!",
            cli_formatter=format_output
        )

        return result  # ✅ CRITICAL: Always return for programmatic use

    except SomeBusinessLogicError as e:
        output_error(e, json_output, error_type="Error")
        raise click.ClickException(str(e))

# ❌ INCORRECT - Missing return statement
@command.command()
def bad_command(ctx, json_output):
    """Command without return - causes Sentinel object issues."""
    result = api_client.some_operation()

    output_result(result, json_output, success_message="✓ Success!")
    # VIOLATION: No return statement - ctx.invoke() will return Sentinel
```

#### **Programmatic Command Invocation:**

Commands with explicit return statements can be safely invoked programmatically:

```python
# ✅ CORRECT - Programmatic invocation works reliably
@command.command()
def wrapper_command(ctx):
    """Command that invokes other commands."""
    # This works because export_command returns its result
    export_result = ctx.invoke(
        export_command,
        database_id='my-db',
        asset_id='my-asset',
        json_output=True
    )

    # export_result is a dict, not a Sentinel object
    assets = export_result.get('assets', [])
    # ... process data ...
```

#### **Benefits of Explicit Returns:**

1. **Programmatic Reusability**: Commands can be invoked by other commands
2. **Cross-Platform Reliability**: Works consistently across all Python/Click versions
3. **No Sentinel Objects**: Eliminates intermittent JSON parsing errors
4. **Clean Architecture**: Commands return data, callers decide how to use it
5. **Testing**: Easier to test command return values directly

### **Rule 17: JSON Output MUST Be Pure**

All commands with `--json-output` parameter MUST output ONLY valid JSON with no additional messages:

#### **JSON Output Requirements:**

-   [ ] **No Status Messages**: Suppress all status messages when `--json-output` is enabled
-   [ ] **No CLI Formatting**: No colored text, emojis, or CLI-specific formatting in JSON mode
-   [ ] **Pure JSON Only**: Only `json.dumps()` output should be sent to stdout
-   [ ] **JSON Error Format**: Errors must be JSON-formatted when `--json-output` is enabled
-   [ ] **No Interactive Prompts**: Never use `click.confirm()`, `click.prompt()`, or any interactive input in JSON mode
-   [ ] **Use JSON Utilities**: Use `vamscli.utils.json_output` helper functions

#### **Correct JSON Output Pattern:**

```python
from ..utils.json_output import output_status, output_result, output_error

@command_group.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def my_command(ctx: click.Context, json_output: bool):
    """Command with proper JSON output handling."""
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Status messages only in CLI mode
        output_status("Processing request...", json_output)

        # Business logic
        result = api_client.some_operation()

        # Output result (JSON or CLI formatted)
        output_result(
            result,
            json_output,
            success_message="✓ Operation successful!",
            cli_formatter=lambda r: format_cli_output(r)
        )

        return result

    except SomeBusinessLogicError as e:
        # Error output (JSON or CLI formatted)
        output_error(
            e,
            json_output,
            error_type="Business Logic Error",
            helpful_message="Use 'vamscli related-command' for more info."
        )
        raise click.ClickException(str(e))
```

#### **Correct Confirmation Handling Pattern (for commands with --confirm flag):**

```python
@command_group.command()
@click.option('--confirm', is_flag=True, help='Confirm deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, confirm: bool, json_output: bool):
    """Delete a resource (requires confirmation)."""
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Require confirmation for deletion
        if not confirm:
            if json_output:
                # For JSON output, return error in JSON format
                error_result = {
                    "error": "Confirmation required",
                    "message": "Deletion requires the --confirm flag",
                    "resourceId": resource_id
                }
                output_result(error_result, json_output=True)
                raise click.ClickException("Confirmation required for deletion")
            else:
                # For CLI output, show helpful message
                click.secho("⚠️  Deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("Use --confirm flag to proceed with deletion.")
                raise click.ClickException("Confirmation required for deletion")

        # Additional confirmation prompt for safety (skip in JSON mode)
        if not json_output:
            click.secho(f"⚠️  You are about to delete '{resource_id}'", fg='red', bold=True)
            click.echo("This action cannot be undone!")

            if not click.confirm("Are you sure you want to proceed?"):
                click.echo("Deletion cancelled.")
                return None

        output_status(f"Deleting '{resource_id}'...", json_output)

        # Perform deletion
        result = api_client.delete_resource(resource_id)

        output_result(
            result,
            json_output,
            success_message="✓ Resource deleted successfully!",
            cli_formatter=lambda r: f"Resource: {resource_id}"
        )

        return result

    except ResourceNotFoundError as e:
        output_error(e, json_output, error_type="Resource Not Found")
        raise click.ClickException(str(e))
```

#### **Correct Interactive Prompt Pattern (for delete commands without --confirm flag):**

```python
@command_group.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, json_output: bool):
    """Delete a resource (interactive confirmation)."""
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Confirmation prompt for safety (skip in JSON mode)
        if not json_output:
            click.secho(f"⚠️  You are about to delete '{resource_id}'", fg='yellow', bold=True)
            click.echo("This will remove the resource and all associated data.")

            if not click.confirm("Are you sure you want to proceed?"):
                click.echo("Deletion cancelled.")
                return None

        output_status(f"Deleting '{resource_id}'...", json_output)

        # Perform deletion
        result = api_client.delete_resource(resource_id)

        output_result(
            result,
            json_output,
            success_message="✓ Resource deleted successfully!",
            cli_formatter=lambda r: f"Resource: {resource_id}"
        )

        return result

    except ResourceNotFoundError as e:
        output_error(e, json_output, error_type="Resource Not Found")
        raise click.ClickException(str(e))
```

#### **Anti-Patterns to Avoid:**

```python
# ❌ INCORRECT - Status message pollutes JSON output
@command.command()
@click.option('--json-output', is_flag=True)
def bad_command(json_output: bool):
    click.echo("Processing...")  # VIOLATION - always outputs
    result = api_call()
    if json_output:
        click.echo(json.dumps(result, indent=2))

# ❌ INCORRECT - Error message pollutes JSON output
@command.command()
@click.option('--json-output', is_flag=True)
def bad_error_handling(json_output: bool):
    try:
        result = api_call()
        if json_output:
            click.echo(json.dumps(result, indent=2))
    except Exception as e:
        click.echo(f"Error: {e}")  # VIOLATION - not JSON formatted
        raise

# ❌ INCORRECT - Mixed output in JSON mode
@command.command()
@click.option('--json-output', is_flag=True)
def bad_mixed_output(json_output: bool):
    result = api_call()
    if json_output:
        click.echo("Success!")  # VIOLATION - text before JSON
        click.echo(json.dumps(result, indent=2))

# ❌ INCORRECT - Interactive prompt in JSON mode
@command.command()
@click.option('--json-output', is_flag=True)
def bad_interactive_prompt(json_output: bool):
    # VIOLATION - click.confirm() runs even in JSON mode
    if not click.confirm("Are you sure?"):
        return
    result = api_call()
    if json_output:
        click.echo(json.dumps(result, indent=2))

# ❌ INCORRECT - Missing JSON error for confirmation
@command.command()
@click.option('--confirm', is_flag=True)
@click.option('--json-output', is_flag=True)
def bad_confirm_handling(confirm: bool, json_output: bool):
    if not confirm:
        # VIOLATION - No JSON error format
        click.echo("Use --confirm flag")
        raise click.ClickException("Confirmation required")

    # VIOLATION - click.confirm() runs even when --confirm is provided with --json-output
    if not click.confirm("Are you sure?"):
        return

    result = api_call()
    if json_output:
        click.echo(json.dumps(result, indent=2))
```

#### **JSON Output Testing Requirements:**

```python
# ✅ CORRECT - Test JSON output purity
def test_command_json_output_pure(self, cli_runner, command_mocks):
    """Test that JSON output contains ONLY valid JSON."""
    with command_mocks as mocks:
        mocks['api_client'].api_method.return_value = {'success': True}

        result = cli_runner.invoke(cli, [
            'command-group', 'command',
            '--param', 'value',
            '--json-output'
        ])

        assert result.exit_code == 0

        # Verify output is pure JSON (no extra text)
        try:
            parsed = json.loads(result.output)
            assert isinstance(parsed, dict)
            assert parsed.get('success') == True
        except json.JSONDecodeError:
            pytest.fail(f"Output is not valid JSON: {result.output}")

def test_command_json_error_format(self, cli_runner, command_mocks):
    """Test that errors are JSON-formatted in JSON mode."""
    with command_mocks as mocks:
        mocks['api_client'].api_method.side_effect = SomeError("Test error")

        result = cli_runner.invoke(cli, [
            'command-group', 'command',
            '--param', 'value',
            '--json-output'
        ])

        assert result.exit_code == 1

        # Verify error output is pure JSON
        try:
            parsed = json.loads(result.output)
            assert 'error' in parsed
            assert parsed['error'] == "Test error"
        except json.JSONDecodeError:
            pytest.fail(f"Error output is not valid JSON: {result.output}")
```

#### **Why JSON Output Purity Matters:**

1. **Downstream Parsing**: Applications parsing CLI output expect pure JSON
2. **Automation**: Scripts and CI/CD pipelines rely on machine-readable output
3. **API Integration**: JSON output enables CLI-to-API bridging
4. **Error Handling**: Consistent error format enables programmatic error handling
5. **Testing**: Pure JSON output is easier to validate and test

#### **JSON Output Utility Functions:**

The `vamscli.utils.json_output` module provides:

-   `output_result()`: Output results in JSON or CLI format
-   `output_error()`: Output errors in JSON or CLI format
-   `output_status()`: Output status messages (CLI only)
-   `output_warning()`: Output warning messages (CLI only)
-   `output_info()`: Output informational messages (CLI only)

These utilities ensure consistent JSON output handling across all commands.

## 📚 **Detailed Implementation Guide**

### **Adding New API Endpoints**

#### **Step 1: Add to Constants**

```python
# constants.py
API_NEW_ENDPOINT = "/api/new/endpoint"
API_ANOTHER_ENDPOINT = "/api/another/endpoint"
```

#### **Step 2: Import in API Client**

```python
# utils/api_client.py
from ..constants import API_VERSION, API_NEW_ENDPOINT, API_ANOTHER_ENDPOINT
```

#### **Step 3: Implement API Method**

```python
# utils/api_client.py
def call_new_endpoint(self, param: str) -> Dict[str, Any]:
    """
    Call new API endpoint.

    Args:
        param: Parameter description

    Returns:
        API response data

    Raises:
        SpecificError: When specific condition occurs
        APIError: When API call fails
    """
    try:
        endpoint = f"{API_NEW_ENDPOINT}/{param}"
        response = self.get(endpoint, include_auth=True)
        return response.json()

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise SpecificError(f"Resource not found: {param}")
        elif e.response.status_code in [401, 403]:
            raise AuthenticationError(f"Authentication failed: {e}")
        else:
            raise APIError(f"API call failed: {e}")

    except Exception as e:
        raise APIError(f"Failed to call endpoint: {e}")
```

### **Adding New Commands**

#### **Step 1: Create Command File (if new group)**

```python
# commands/new_group.py
"""New command group for VamsCLI."""

import click

from ..utils.profile import ProfileManager
from ..utils.api_client import APIClient
from ..utils.exceptions import SpecificError, APIUnavailableError


def requires_api_access(func):
    """Decorator to check API availability before command execution."""
    # Copy from existing implementation
    pass


@click.group()
def new_group():
    """New command group description."""
    pass
```

#### **Step 2: Implement Commands**

```python
from ..utils.json_output import output_status, output_result, output_error

@new_group.command()
@click.option('-u', '--user-id', help='User ID if required')
@click.option('--param', required=True, help='Required parameter')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def new_command(ctx: click.Context, user_id: str, param: str, json_output: bool):
    """
    Brief command description.

    Detailed description with usage information.

    Examples:
        vamscli new-group new-command --param value
        vamscli new-group new-command -u user@example.com --param value --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Status messages only in CLI mode
        output_status("Processing request...", json_output)

        # Focus on business logic only
        result = api_client.call_new_endpoint(param)

        # Output result (JSON or CLI formatted)
        output_result(result, json_output, success_message="✓ Operation successful!")

    except SpecificBusinessLogicError as e:
        # Only handle command-specific business logic errors
        output_error(
            e,
            json_output,
            error_type="Specific Error",
            helpful_message="Use 'vamscli [related-command]' for more information."
        )
        raise click.ClickException(str(e))
```

#### **Step 3: Register Command in Main**

```python
# main.py
from .commands.new_group import new_group

# Add to CLI group
cli.add_command(new_group)
```

#### **Step 4: Update Imports**

```python
# commands/__init__.py
from .new_group import new_group
__all__ = ['setup', 'auth', 'new_group']
```

### **Adding New Exceptions**

#### **Step 1: Add to Exceptions File**

```python
# utils/exceptions.py
class NewSpecificError(VamsCLIError):
    """Raised when new specific condition occurs."""
    pass
```

#### **Step 2: Update Utils Init**

```python
# utils/__init__.py
from .exceptions import (
    # ... existing exceptions
    NewSpecificError
)

__all__ = [
    # ... existing exports
    'NewSpecificError'
]
```

#### **Step 3: Update Main Exception Handler**

```python
# main.py
from .utils.exceptions import NewSpecificError

# Add to exception handler in main.py
# Note: main.py exception handlers don't need JSON output support
# as they handle global infrastructure errors before command execution
except NewSpecificError as e:
    click.echo(
        click.style(f"✗ New Error: {e}", fg='red', bold=True),
        err=True
    )
    sys.exit(1)
```

## ✅ **Quality Assurance Checklist**

### **Before Implementation**

-   [ ] Requirements clearly understood
-   [ ] Architecture impact assessed
-   [ ] API endpoints identified
-   [ ] Error scenarios planned
-   [ ] Test cases outlined

### **During Implementation**

-   [ ] API endpoints added to `constants.py`
-   [ ] Custom exceptions created in `utils/exceptions.py`
-   [ ] API methods added to `utils/api_client.py`
-   [ ] Commands implemented with proper decorators
-   [ ] Error handling comprehensive
-   [ ] Help text comprehensive with examples

### **After Implementation**

-   [ ] All tests written and passing
-   [ ] Code formatted with Black
-   [ ] Type checking passes with MyPy
-   [ ] Documentation updated (README.md)
-   [ ] CLI help tested manually
-   [ ] Error scenarios tested
-   [ ] Integration tested end-to-end

## 🎯 **Common Patterns**

### **New Streamlined Command Pattern with JSON Output**

```python
# ✅ NEW PATTERN - Using @requires_setup_and_auth decorator with JSON output support
from ..utils.json_output import output_status, output_result, output_error

@command_group.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth  # Handles all global validations automatically
def my_command(ctx: click.Context, json_output: bool, ...):
    """Command with streamlined exception handling and JSON output support."""
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Status messages only in CLI mode
        output_status("Processing request...", json_output)

        # Focus on business logic only
        result = api_client.api_method(parameters)

        # Output result (JSON or CLI formatted)
        output_result(
            result,
            json_output,
            success_message="✓ Success message",
            cli_formatter=lambda r: format_output(r)
        )

        return result

    except SpecificBusinessLogicError as e:
        # Only handle command-specific business logic errors
        output_error(
            e,
            json_output,
            error_type="Specific Error",
            helpful_message="Use 'vamscli [related-command]' for more information."
        )
        raise click.ClickException(str(e))
```

### **Legacy Authentication Validation Pattern (Deprecated)**

```python
# ❌ OLD PATTERN - Don't use this approach anymore
# For commands requiring authentication
profile_manager = ProfileManager()

if not profile_manager.has_config():
    raise click.ClickException("Configuration not found. Please run 'vamscli setup' first.")

if not profile_manager.has_auth_profile():
    raise click.ClickException("Not authenticated. Please run 'vamscli auth login' first.")
```

### **User Input Validation Pattern**

```python
# Validate user inputs
if not validate_input(user_input):
    raise click.BadParameter("Invalid input format. Expected: [format description]")

# Prompt for missing required inputs
if not required_param:
    required_param = click.prompt("Required parameter")
```

## 🔍 **Code Review Checklist**

### **Architecture Compliance**

-   [ ] API endpoints in constants.py
-   [ ] Proper file segregation
-   [ ] Consistent import patterns
-   [ ] Exception hierarchy followed

### **Functionality**

-   [ ] Authentication checks implemented
-   [ ] Setup validation included
-   [ ] Error handling comprehensive
-   [ ] User feedback clear and helpful

### **Code Quality**

-   [ ] Type hints included
-   [ ] Docstrings comprehensive
-   [ ] Variable names descriptive
-   [ ] Code formatted and linted

### **Testing**

-   [ ] Unit tests written
-   [ ] Integration tests included
-   [ ] Error scenarios tested
-   [ ] CLI interface tested

### **Documentation**

-   [ ] README.md updated
-   [ ] Help text comprehensive
-   [ ] Examples included
-   [ ] Installation guide updated if needed

## 🚀 **Release Checklist**

### **Pre-Release**

-   [ ] Version updated in `version.py`
-   [ ] All tests passing
-   [ ] Code quality checks passed
-   [ ] Documentation complete
-   [ ] Manual testing completed

### **Build & Distribution**

-   [ ] Clean build: `rm -rf build/ dist/ *.egg-info/`
-   [ ] Build wheel: `python -m build`
-   [ ] Test installation: `pip install dist/vamscli-*.whl`
-   [ ] Verify CLI functionality
-   [ ] Create distribution package

### **Post-Release**

-   [ ] Update changelog
-   [ ] Tag release in git
-   [ ] Update documentation
-   [ ] Notify users of new features

## 📖 **Best Practices Summary**

1. **Always** add API endpoints to `constants.py`
2. **Always** use `@requires_setup_and_auth` for new API commands (or `@requires_api_access` for backward compatibility)
3. **Always** let decorators handle setup and authentication validation
4. **Always** handle only business logic exceptions in commands
5. **Always** write tests for new functionality
6. **Always** update documentation
7. **Always** use type hints
8. **Always** include comprehensive help text
9. **Always** validate user inputs
10. **Always** provide clear error messages
11. **Always** follow the new exception handling architecture
12. **Always** let global exceptions bubble up to main.py

## 🛠️ **Development Commands**

```bash
# Setup development environment
cd tools/VamsCLI
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"

# Code quality checks
black vamscli/                    # Format code
mypy vamscli/                     # Type checking
pytest                            # Run tests
pytest --cov=vamscli             # Run tests with coverage

# Build and test
python -m build                   # Build wheel
pip install dist/vamscli-*.whl   # Test installation

# Manual testing
python -c "from vamscli.main import main; import sys; sys.argv = ['vamscli', '--help']; main()"
```

This workflow ensures that all VamsCLI development follows established patterns and maintains the high quality standards of the codebase.
