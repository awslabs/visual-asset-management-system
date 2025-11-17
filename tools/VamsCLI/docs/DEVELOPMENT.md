# VamsCLI Development Guide

This guide provides information for developers who want to contribute to VamsCLI or extend its functionality.

## Development Environment Setup

### Prerequisites

-   **Python**: 3.12 or higher
-   **Git**: For version control
-   **pip**: Python package installer

### Setup Steps

1. **Clone the repository:**

    ```bash
    git clone https://github.com/awslabs/visual-asset-management-system.git
    cd visual-asset-management-system/tools/VamsCLI
    ```

2. **Create virtual environment:**

    ```bash
    python -m venv venv

    # Activate virtual environment
    # On Windows:
    venv\Scripts\activate
    # On macOS/Linux:
    source venv/bin/activate
    ```

3. **Install development dependencies:**

    ```bash
    pip install -e ".[dev]"
    ```

4. **Verify installation:**
    ```bash
    vamscli --version
    pytest --version
    ```

## Development Dependencies

VamsCLI includes the following development dependencies:

-   **pytest**: Testing framework
-   **pytest-cov**: Test coverage reporting
-   **black**: Code formatting
-   **flake8**: Code linting
-   **mypy**: Type checking

## Code Quality Standards

### Handling Click Sentinel Objects (Critical for Programmatic Command Invocation)

When commands are invoked programmatically using `ctx.invoke()`, Click may pass `Sentinel.UNSET` objects for optional parameters that weren't provided. All `parse_json_input()` functions MUST handle these Sentinel objects to prevent JSON parsing errors.

#### Required Pattern for parse_json_input()

```python
def parse_json_input(json_input: str) -> Dict[str, Any]:
    """Parse JSON input from string or file."""
    # Handle None, empty string, or Click Sentinel objects
    if not json_input or (hasattr(json_input, '__class__') and 'Sentinel' in json_input.__class__.__name__):
        return {}

    try:
        # Try to parse as JSON string first
        return json.loads(json_input)
    except json.JSONDecodeError:
        # If that fails, try to read as file path
        try:
            with open(json_input, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, IOError):
            raise click.BadParameter(
                f"Invalid JSON input: '{json_input}' is neither valid JSON nor a readable file path"
            )
```

#### Why This Is Critical

1. **Programmatic Invocation**: Commands like `glbassetcombine` invoke other commands via `ctx.invoke()`
2. **Sentinel Objects**: Click passes `Sentinel.UNSET` for unset optional parameters
3. **JSON Parsing Failure**: Without the check, `json.loads(Sentinel.UNSET)` raises TypeError
4. **Cross-Platform Issues**: Appears intermittently across different Python/Click versions

#### When to Apply This Pattern

-   **All `parse_json_input()` functions** in command files
-   **Any function** that may receive Click Sentinel objects as parameters
-   **Commands** that may be invoked programmatically by other commands

### Code Formatting

Use Black for consistent code formatting:

```bash
# Format all code
black vamscli/

# Check formatting without changes
black --check vamscli/
```

**Configuration:** Black is configured in `pyproject.toml` with:

-   Line length: 100 characters
-   Target Python version: 3.12+

### Type Checking

Use MyPy for static type checking:

```bash
# Type check all code
mypy vamscli/

# Type check specific file
mypy vamscli/commands/assets.py
```

**Configuration:** MyPy is configured in `pyproject.toml` with strict settings.

### Linting

Use Flake8 for code linting:

```bash
# Lint all code
flake8 vamscli/

# Lint specific file
flake8 vamscli/commands/assets.py
```

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run tests with coverage
pytest --cov=vamscli

# Run specific test file
pytest tests/test_assets.py

# Run specific test
pytest tests/test_assets.py::TestAssetCommands::test_create_asset
```

### Test Structure

Tests are organized to mirror the command structure:

```
tests/
├── test_assets.py          # Asset command tests
├── test_auth.py           # Authentication command tests
├── test_file_upload.py    # File upload tests
├── test_setup.py          # Setup command tests
└── conftest.py            # Test configuration and fixtures
```

### Writing Tests

Follow these patterns when writing tests:

#### Command Tests

```python
"""Test asset commands."""

import pytest
from click.testing import CliRunner

from vamscli.commands.assets import create


class TestAssetCommands:
    """Test asset management commands."""

    def test_create_help(self):
        """Test create command help."""
        runner = CliRunner()
        result = runner.invoke(create, ['--help'])
        assert result.exit_code == 0
        assert 'Create a new asset' in result.output

    def test_create_success(self):
        """Test successful asset creation."""
        runner = CliRunner()
        with patch('vamscli.commands.assets.APIClient') as mock_client:
            mock_client.return_value.create_asset.return_value = {"assetId": "test"}

            result = runner.invoke(create, [
                '-d', 'test-db', '--name', 'Test Asset', '--description', 'Test'
            ])

            assert result.exit_code == 0
            assert 'Asset created successfully' in result.output
```

#### Unit Tests

```python
"""Test utility functions."""

from vamscli.utils.file_processor import calculate_file_parts


def test_calculate_file_parts():
    """Test file part calculation."""
    parts = calculate_file_parts(100 * 1024 * 1024)  # 100MB

    assert len(parts) == 1
    assert parts[0]["size"] == 100 * 1024 * 1024
```

## Architecture Guidelines

### File Organization

Follow the established file structure:

```
vamscli/
├── main.py                 # CLI entry point
├── version.py             # Version management
├── constants.py           # API endpoints and constants
├── auth/                  # Authentication providers
├── commands/              # Command implementations
└── utils/                 # Shared utilities
```

### Adding New Commands

1. **Create command file** in `commands/` directory
2. **Add API endpoints** to `constants.py`
3. **Add exceptions** to `utils/exceptions.py`
4. **Add API methods** to `utils/api_client.py`
5. **Register command** in `main.py`
6. **Write tests** in `tests/` directory
7. **Update documentation** in appropriate files

### Code Patterns

#### Error Handling Pattern

```python
try:
    result = api_client.some_method()
    click.echo(click.style("✓ Success!", fg='green', bold=True))
except SpecificError as e:
    click.echo(click.style(f"✗ Error: {e}", fg='red', bold=True), err=True)
    raise click.ClickException(str(e))
```

#### API Client Pattern

```python
def api_method(self, param: str) -> Dict[str, Any]:
    """API method with proper error handling."""
    try:
        endpoint = f"{API_CONSTANT}/{param}"
        response = self.get(endpoint, include_auth=True)
        return response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise SpecificError(f"Resource not found: {param}")
        # ... handle other errors
    except Exception as e:
        raise APIError(f"Failed to call API: {e}")
```

## Building and Distribution

### Building Packages

```bash
# Install build dependencies
pip install build

# Clean previous builds
rm -rf build/ dist/ *.egg-info/

# Build wheel and source distribution
python -m build
```

This creates:

-   `dist/vamscli-X.X.X-py3-none-any.whl` (wheel file)
-   `dist/vamscli-X.X.X.tar.gz` (source distribution)

### Testing Distribution

```bash
# Test wheel installation
pip install dist/vamscli-*.whl

# Verify CLI works
vamscli --version
vamscli --help
```

### Version Management

Update version in `vamscli/version.py`:

```python
__version__ = "2.2.0"
```

## Development Workflow

### Daily Development

1. **Pull latest changes:**

    ```bash
    git pull origin main
    ```

2. **Create feature branch:**

    ```bash
    git checkout -b feature/new-feature
    ```

3. **Make changes following the patterns**

4. **Run quality checks:**

    ```bash
    black vamscli/
    mypy vamscli/
    pytest
    ```

5. **Commit and push:**
    ```bash
    git add .
    git commit -m "Add new feature"
    git push origin feature/new-feature
    ```

### Pre-commit Checklist

Before committing code:

-   [ ] Code formatted with Black
-   [ ] Type checking passes with MyPy
-   [ ] All tests pass
-   [ ] New tests written for new functionality
-   [ ] Documentation updated
-   [ ] Help text comprehensive
-   [ ] Error handling comprehensive

## Contributing Guidelines

### Code Style

-   **Follow PEP 8**: Use Black for formatting
-   **Type Hints**: Include type hints for all functions
-   **Docstrings**: Use Google-style docstrings
-   **Variable Names**: Use descriptive names
-   **Comments**: Explain complex logic

### Commit Messages

Use clear, descriptive commit messages:

```
Add file upload command with chunking support

- Implement three-stage upload process
- Add progress monitoring and retry logic
- Support both assetFile and assetPreview uploads
- Include comprehensive error handling
```

### Pull Request Process

1. **Create feature branch** from main
2. **Implement changes** following guidelines
3. **Add tests** for new functionality
4. **Update documentation** as needed
5. **Run quality checks** and ensure they pass
6. **Create pull request** with detailed description
7. **Address review feedback** if any

### Documentation Requirements

When adding new features, update the appropriate documentation files:

-   **New commands** → Update [COMMANDS.md](COMMANDS.md)
-   **Authentication changes** → Update [AUTHENTICATION.md](AUTHENTICATION.md)
-   **Installation changes** → Update [INSTALLATION.md](INSTALLATION.md)
-   **New troubleshooting** → Update [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
-   **Development changes** → Update this file
-   **Major features** → Update main [README.md](README.md)

## Debugging

### Debug Mode

VamsCLI supports debug mode for development:

```bash
vamscli --debug <command>
```

### Adding Debug Information

```python
import sys

# Check for debug mode
if '--debug' in sys.argv:
    print(f"Debug: {debug_information}")
```

### Logging

Use Python's logging module for debug information:

```python
import logging

logger = logging.getLogger(__name__)

def some_function():
    logger.debug("Debug information")
    logger.info("Informational message")
    logger.warning("Warning message")
```

## Manual Testing

### Test CLI Installation

```bash
# Test from wheel
pip install dist/vamscli-*.whl
vamscli --version

# Test development installation
pip install -e .
vamscli --version
```

### Test Command Functionality

```bash
# Test help system
vamscli --help
vamscli auth --help
vamscli assets --help

# Test basic commands
vamscli setup https://api.example.com --force
vamscli auth status
```

### Test Error Scenarios

```bash
# Test without setup
vamscli auth login -u test@example.com

# Test with invalid arguments
vamscli assets create --invalid-option

# Test with missing required arguments
vamscli assets create
```

## Release Process

### Pre-release Checklist

-   [ ] Version updated in `version.py`
-   [ ] All tests passing
-   [ ] Code quality checks passed
-   [ ] Documentation complete and accurate
-   [ ] Manual testing completed
-   [ ] CHANGELOG.md updated

### Release Steps

1. **Update version** in `vamscli/version.py`
2. **Run full test suite:** `pytest`
3. **Run quality checks:** `black vamscli/ && mypy vamscli/`
4. **Build distribution:** `python -m build`
5. **Test installation:** `pip install dist/vamscli-*.whl`
6. **Manual testing** of key functionality
7. **Create git tag:** `git tag v2.2.0`
8. **Push changes:** `git push origin main --tags`

## Troubleshooting Development Issues

### Import Errors

**Issue**: Import errors when running tests or CLI
**Solution**: Ensure you're in the virtual environment and dependencies are installed

### Test Failures

**Issue**: Tests fail unexpectedly
**Solutions:**

1. Run tests individually to isolate issues
2. Check for missing test dependencies
3. Verify test data and mocks are correct

### Build Failures

**Issue**: `python -m build` fails
**Solutions:**

1. Ensure `build` package is installed: `pip install build`
2. Check `pyproject.toml` syntax
3. Verify all required files are present

## Getting Help

### Development Questions

1. **Check existing code** for similar patterns
2. **Review documentation** in this guide
3. **Check development workflow** in [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)
4. **Create GitHub issue** for development questions

### Code Review

All contributions go through code review:

1. **Follow guidelines** in this document
2. **Include tests** for new functionality
3. **Update documentation** as needed
4. **Respond to feedback** promptly

## Resources

-   **Main Repository**: [Visual Asset Management System](https://github.com/awslabs/visual-asset-management-system)
-   **Issues**: [GitHub Issues](https://github.com/awslabs/visual-asset-management-system/issues)
-   **Development Workflow**: [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)
-   **Python Packaging**: [Python Packaging Guide](https://packaging.python.org/)
-   **Click Documentation**: [Click Framework](https://click.palletsprojects.com/)

## License

VamsCLI is licensed under the Apache License 2.0. See [LICENSE](../../LICENSE) for details.

All contributions must be compatible with this license.
