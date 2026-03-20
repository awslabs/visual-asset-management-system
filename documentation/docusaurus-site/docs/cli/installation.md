# Installation and Profile Management

This page covers detailed installation steps, multi-profile configuration, configuration file locations, and environment variable overrides for VamsCLI.

## Installation

### Prerequisites

Ensure your system meets the following requirements:

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.13+ | Required for CLI runtime |
| pip | Latest | Python package installer |
| Git | Any | For cloning the repository |

### Install from Source

1. Clone the VAMS repository and navigate to the CLI directory:

    ```bash
    git clone <repository-url>
    cd tools/VamsCLI
    ```

2. Install VamsCLI in development mode:

    ```bash
    pip install -e .
    ```

3. Verify the installation:

    ```bash
    vamscli --version
    ```

:::tip[Virtual Environment]
It is recommended to install VamsCLI inside a Python virtual environment to avoid dependency conflicts:

```bash
python -m venv venv
source venv/bin/activate    # Linux/macOS
venv\Scripts\activate       # Windows
pip install -e .
```
:::


### Install from Pre-built Wheel

Organizations can distribute pre-built wheel files for VamsCLI. To install from a wheel file:

```bash
pip install path/to/vamscli-X.X.X-py3-none-any.whl
```

:::note
VamsCLI is not available on PyPI. Only install from trusted organizational sources.
:::

### Building Distribution Packages

To create a distributable wheel file for your organization:

```bash
cd tools/VamsCLI
pip install build
python -m build
```

This creates files in the `dist/` directory: `vamscli-X.X.X-py3-none-any.whl` (wheel) and `vamscli-X.X.X.tar.gz` (source distribution).

### Uninstalling VamsCLI

```bash
pip uninstall vamscli
```

To remove configuration files as well, delete the configuration directory for your platform (see [Configuration File Locations](#configuration-file-locations) below).

### Updating VamsCLI

To update VamsCLI after pulling new changes from the repository:

```bash
cd tools/VamsCLI
pip install -e .
```

The `pip install -e .` command links to the source directory, so in most cases pulling new code automatically updates the CLI. Re-running the install command ensures any new dependencies are resolved.

## Profile Management

VamsCLI supports multiple profiles, enabling you to work with separate VAMS environments (development, staging, production) from the same workstation. Each profile stores its own configuration, authentication tokens, and saved credentials independently.

### Default Profile

When you run `vamscli setup` without specifying a profile, the configuration is saved to the **default** profile. All commands use the default profile unless you specify otherwise with `--profile`.

### Creating Profiles

Create additional profiles by passing the `--profile` flag during setup:

```bash
vamscli --profile production setup https://prod-vams.example.com
vamscli --profile staging setup https://staging-vams.example.com
```

Each profile must be authenticated independently:

```bash
vamscli --profile production auth login -u admin@example.com
vamscli --profile staging auth login -u dev@example.com
```

### Switching Profiles

Switch the active profile so that subsequent commands use it by default:

```bash
vamscli profile switch production
```

### Listing Profiles

View all configured profiles, their authentication status, and which profile is currently active:

```bash
vamscli profile list
```

### Profile Information

View detailed information about a specific profile, including Amplify configuration, authentication type, and token expiration:

```bash
vamscli profile info production
```

### Viewing the Current Profile

Display the currently active profile and its status:

```bash
vamscli profile current
```

### Deleting Profiles

Remove a profile and all its associated configuration. The default profile cannot be deleted.

```bash
vamscli profile delete test-profile
vamscli profile delete test-profile --force    # Skip confirmation prompt
```

### Profile Name Rules

Profile names must satisfy the following constraints:

- Between 3 and 50 characters in length
- Alphanumeric characters, hyphens (`-`), and underscores (`_`) only
- Cannot be a reserved word: `help`, `version`, or `list`

## Configuration File Locations

VamsCLI stores configuration files in platform-specific directories.

### Storage Paths

| Platform | Base Directory |
|---|---|
| Windows | `%APPDATA%\vamscli\` |
| macOS | `~/Library/Application Support/vamscli/` |
| Linux | `~/.config/vamscli/` |

### Directory Structure

Within the base directory, each profile has its own subdirectory under `profiles/`:

```
vamscli/
  active_profile.json          # Tracks which profile is currently active
  profiles/
    default/
      config.json              # API Gateway URL, Amplify config, setup metadata
      auth_profile.json        # Authentication tokens, user ID, token type
      credentials.json         # Saved username/password (optional)
    production/
      config.json
      auth_profile.json
      credentials.json
    staging/
      config.json
      auth_profile.json
```

### Configuration Files

| File | Purpose | Created By |
|---|---|---|
| `config.json` | API Gateway URL, Amplify configuration, AWS Region, Amazon Cognito User Pool and Client ID, CLI version, setup timestamp | `vamscli setup` |
| `auth_profile.json` | ID token, access token, refresh token, user ID, token type (Cognito or override), expiration, feature switches | `vamscli auth login` or `vamscli auth set-override` |
| `credentials.json` | Saved username and password for automatic re-authentication | `vamscli auth login --save-credentials` |

:::warning[Credential Security]
The `credentials.json` file stores passwords in plain text on your filesystem. Only use `--save-credentials` on secure workstations. Consider using token override authentication with short-lived tokens for CI/CD environments instead.
:::


## Environment Variable Overrides

VamsCLI reads the following environment variables to override default retry behavior for API throttling (HTTP 429 responses):

| Environment Variable | Default | Description |
|---|---|---|
| `VAMS_CLI_MAX_RETRY_ATTEMPTS` | `5` | Maximum number of retry attempts for throttled requests |
| `VAMS_CLI_INITIAL_RETRY_DELAY` | `1.0` (seconds) | Initial delay before the first retry |
| `VAMS_CLI_MAX_RETRY_DELAY` | `60.0` (seconds) | Maximum delay between retries |

Example:

```bash
export VAMS_CLI_MAX_RETRY_ATTEMPTS=10
export VAMS_CLI_INITIAL_RETRY_DELAY=2.0
vamscli assets list -d my-database --auto-paginate
```

## UTF-8 Terminal Requirements

VamsCLI uses Unicode characters for status indicators in CLI output. If you see encoding errors, ensure your terminal supports UTF-8.

### Windows

Use one of the following approaches:

- **Windows Terminal** (recommended) -- UTF-8 by default
- **VS Code integrated terminal** -- UTF-8 by default
- **Legacy Command Prompt** -- Set the environment variable before running VamsCLI:

    ```bash
    set PYTHONIOENCODING=utf-8
    ```

### macOS and Linux

Most terminal emulators on macOS and Linux use UTF-8 encoding by default. No additional configuration is required.

## Next Steps

- [Getting Started](getting-started.md) -- Quick setup and first commands
- [Command Reference](command-reference.md) -- Complete reference for all commands
- [Automation and Scripting](automation.md) -- CI/CD integration, scripting patterns, and JSON output
