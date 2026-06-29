---
sidebar_label: Development
title: VamsCLI Development
---

# VamsCLI Development

This page is for developers who want to contribute to VamsCLI or extend it with new commands. It covers the development environment, code quality tooling, testing, the command architecture, and the build and release process. For installing VamsCLI as an end user, see [Installation and Profile Management](installation.md).

## Development Environment

### Prerequisites

| Tool   | Version | Purpose                 |
| ------ | ------- | ----------------------- |
| Python | 3.13+   | Development and testing |
| pip    | Latest  | Package installer       |
| Git    | Latest  | Version control         |

:::note[Runtime vs. development Python]
VamsCLI supports Python 3.12 and later (`requires-python = ">=3.12"`). Local development and testing target Python 3.13+, matching the rest of the VAMS repository.
:::

### Setup

Clone the repository, create a virtual environment, and install in editable mode with the development extras:

```bash
git clone https://github.com/awslabs/visual-asset-management-system.git
cd visual-asset-management-system/tools/VamsCLI

python -m venv venv
source venv/bin/activate    # Linux/macOS
venv\Scripts\activate       # Windows

pip install -e ".[dev]"
```

Verify the install:

```bash
vamscli --version
pytest --version
```

The `[dev]` extra installs `pytest`, `pytest-cov`, `pytest-asyncio`, `black`, `flake8`, and `mypy`.

---

## Code Quality

VamsCLI is formatted with Black, type-checked with MyPy, and linted with Flake8. All three are configured in `pyproject.toml`.

```bash
# Format (line length 100, target py312)
black vamscli/
black --check vamscli/    # verify without modifying

# Type-check (strict settings)
mypy vamscli/

# Lint
flake8 vamscli/
```

### Handling Click Sentinel objects

When one command invokes another programmatically with `ctx.invoke()` (for example, `industry spatial glbassetcombine` invoking the upload path), Click passes a `Sentinel.UNSET` object for any optional parameter that was not supplied. Every `parse_json_input()` helper must detect and skip these objects, or `json.loads()` raises a `TypeError`.

```python
def parse_json_input(json_input: str) -> Dict[str, Any]:
    """Parse JSON input from a string or a file path."""
    # Handle None, empty string, or Click Sentinel objects
    if not json_input or (hasattr(json_input, "__class__") and "Sentinel" in json_input.__class__.__name__):
        return {}

    try:
        return json.loads(json_input)            # inline JSON string
    except json.JSONDecodeError:
        try:
            with open(json_input, "r") as f:     # otherwise treat as a file path
                return json.load(f)
        except (FileNotFoundError, IOError):
            raise click.BadParameter(
                f"Invalid JSON input: '{json_input}' is neither valid JSON nor a readable file path"
            )
```

:::warning[Apply to every JSON-input command]
Apply this pattern to all `parse_json_input()` functions and any helper that may receive a value from a programmatically invoked command. The failure is intermittent — it surfaces only when a command is invoked through `ctx.invoke()` with the optional argument omitted.
:::

---

## Testing

VamsCLI uses pytest with Click's `CliRunner`. Tests live in `tests/`, one file per command group (for example, `test_assets.py`, `test_auth_password.py`), with shared fixtures in `tests/conftest.py`.

```bash
pytest                                  # all tests
pytest --cov=vamscli                    # with coverage
pytest tests/test_assets.py             # a single file
pytest tests/test_assets.py::TestAssetCommands::test_create_success
pytest -m "not slow"                    # skip slow tests
```

### Registered markers

| Marker            | Purpose                                                            |
| ----------------- | ------------------------------------------------------------------ |
| `slow`            | Long-running tests (deselect with `-m "not slow"`)                 |
| `integration`     | Integration tests                                                  |
| `asyncio`         | Async tests                                                        |
| `no_mock_logging` | Opt out of the autouse logging mock when a test needs real logging |

### The `generic_command_mocks` fixture

Command tests use the `generic_command_mocks` factory from `conftest.py`, which patches the `ProfileManager` and `APIClient` injection points for a command module so tests do not touch the filesystem or network. Configure the mocked API response, invoke the command, and assert on the result:

```python
def test_list_success(self, cli_runner, generic_command_mocks):
    with generic_command_mocks("database") as mocks:
        mocks["api_client"].list_databases.return_value = {"Items": [{"databaseId": "db1"}]}
        result = cli_runner.invoke(cli, ["database", "list"])
        assert result.exit_code == 0
        assert "db1" in result.output
```

Always cover the `--json-output` path and the not-found / no-setup error paths for each command. See `tools/VamsCLI/CLAUDE.md` for the full testing reference.

---

## Architecture

VamsCLI is a [Click](https://click.palletsprojects.com/) application. Source is organized by responsibility:

```
vamscli/
  main.py        # CLI entry point and command-group registration
  version.py     # Version constants
  constants.py   # API endpoint paths and limit constants
  auth/          # Authentication providers (Cognito)
  commands/      # One module per command group
  utils/         # APIClient, ProfileManager, exceptions, decorators, JSON output
```

### Adding a new command

1. Define the API endpoint path constant in `constants.py`.
2. Add exception classes in `utils/exceptions.py` under the correct tier.
3. Add the API method in `utils/api_client.py`.
4. Implement the command in `commands/{group}.py` using the `@requires_setup_and_auth` decorator and the `output_status` / `output_result` / `output_error` helpers.
5. Register the command (or group) in `main.py`.
6. Write tests in `tests/test_{group}.py` using `generic_command_mocks`.
7. Update the documentation on this site — the command page in [`cli/commands/`](command-reference.md), the matching [`cli/troubleshooting/`](troubleshooting/general.md) page if error scenarios change, and `sidebars.ts` for a new page.
8. Add a `CHANGELOG.md` entry.
9. If CLI commands, parameters, output formats, or authentication flows change, review the external connectors under `tools/ExternalIntegrations/` that wrap the CLI.

:::tip[Conventions]
Match the established patterns: never `print()` directly in commands that support `--json-output`, obtain the profile manager with `get_profile_manager_from_context(ctx)`, keep all endpoint paths in `constants.py`, and catch only `BusinessLogicError` subclasses in commands (global infrastructure errors propagate to the top-level handler). The full rule set lives in `tools/VamsCLI/CLAUDE.md`.
:::

---

## Building Distribution Packages

VamsCLI is not published to PyPI. Organizations build and distribute their own wheel:

```bash
cd tools/VamsCLI
pip install build

# Optional: clean previous builds
rm -rf build/ dist/ *.egg-info/

python -m build
```

This produces `dist/vamscli-X.X.X-py3-none-any.whl` and `dist/vamscli-X.X.X.tar.gz`. Test the wheel in a clean environment before distributing:

```bash
pip install dist/vamscli-*.whl
vamscli --version
```

---

## Debugging

Use the global `--verbose` flag to print API request URLs, response status and timing, token validation details, and retry attempts. It is placed before the command group and writes to a rotating log file, so it is safe to combine with `--json-output`:

```bash
vamscli --verbose assets list -d my-database
```

See [General Troubleshooting and Debugging](troubleshooting/general.md) for log file locations and the diagnostic checklist.

---

## Release Process

1. Update the version in `vamscli/version.py`.
2. Run the full test suite: `pytest`.
3. Run the quality checks: `black --check vamscli/`, `mypy vamscli/`, `flake8 vamscli/`.
4. Build the distribution: `python -m build`.
5. Install the wheel in a clean environment and smoke-test key commands.
6. Update `CHANGELOG.md`.

---

## Related Pages

-   [Installation and Profile Management](installation.md)
-   [Command Reference](command-reference.md)
-   [Automation and Scripting](automation.md)
-   [Local Development Environment Setup](../developer/setup.md) — full-stack (frontend, backend, CDK, CLI) developer setup
