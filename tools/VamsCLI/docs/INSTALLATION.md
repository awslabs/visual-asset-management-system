# VamsCLI Installation Guide

This guide provides detailed instructions for installing and setting up VamsCLI on your system.

## System Requirements

-   **Python**: 3.12 or higher
-   **Operating System**: Windows, macOS, or Linux
-   **Network**: HTTPS access to your VAMS API Gateway
-   **Dependencies**: Automatically installed with VamsCLI

## Installation Methods

### Method 1: Install from Source

If you want to install from the source code:

```bash
git clone https://github.com/awslabs/visual-asset-management-system.git
cd visual-asset-management-system/tools/VamsCLI
pip install .
```

### Method 2: Development Installation

For development or contributing to VamsCLI:

```bash
git clone https://github.com/awslabs/visual-asset-management-system.git
cd visual-asset-management-system/tools/VamsCLI
pip install -e ".[dev]"
```

This installs VamsCLI in "editable" mode with development dependencies.

### Method 3: Build and install from wheel

See see the [Installation Guide](docs/INSTALLATION.md).##Building Distribution Packages below for more information

## Verify Installation

After installation, verify VamsCLI is working:

```bash
vamscli --version
```

You should see output like:

```
VamsCLI version 2.2.0
```

## Initial Setup

### 1. Configure API Gateway URL

Before using VamsCLI, you must configure it with your VAMS API Gateway URL:

```bash
vamscli setup https://your-api-gateway-url.execute-api.region.amazonaws.com
```

**Example:**

```bash
vamscli setup https://7bx3w05l79.execute-api.us-west-2.amazonaws.com
```

### 2. Setup Options

The setup command supports the following options:

-   **`--force, -f`**: Force setup even if configuration already exists

**Example:**

```bash
vamscli setup https://api.example.com --force
```

### 3. What Setup Does

The setup command:

1. Validates the API Gateway URL format
2. Checks API version compatibility
3. Fetches Amplify configuration from `/api/amplify-config`
4. Stores configuration in profile-specific location
5. Sets the profile as active
6. Clears any existing authentication profiles (with `--force`)

### 4. Profile Setup

VamsCLI supports multiple profiles for different environments or users:

```bash
# Setup different environments
vamscli setup https://prod-api.example.com --profile production
vamscli setup https://staging-api.example.com --profile staging

# Setup for different users
vamscli setup https://api.example.com --profile alice
vamscli setup https://api.example.com --profile bob
```

**Profile Features:**

-   Each profile has completely separate configuration
-   Profiles are isolated from each other
-   Default profile is used if no --profile specified
-   Existing installations automatically migrate to "default" profile

## Configuration Storage

VamsCLI stores configuration and authentication data in OS-appropriate locations:

### Windows

```
%APPDATA%\vamscli\
├── config.json           # Main configuration
├── auth_profile.json     # Authentication tokens
└── credentials.json      # Saved credentials (optional)
```

### macOS

```
~/Library/Application Support/vamscli/
├── config.json           # Main configuration
├── auth_profile.json     # Authentication tokens
└── credentials.json      # Saved credentials (optional)
```

### Linux

```
~/.config/vamscli/
├── config.json           # Main configuration
├── auth_profile.json     # Authentication tokens
└── credentials.json      # Saved credentials (optional)
```

### Configuration Files

-   **`config.json`**: Main configuration including API Gateway URL and Amplify config
-   **`auth_profile.json`**: Authentication tokens and session data
-   **`credentials.json`**: Saved credentials (only created if explicitly requested)

## Building Distribution Packages

### Prerequisites

Install build dependencies:

```bash
pip install build
```

### Build Wheel File

To create a distributable wheel file:

```bash
# Navigate to VamsCLI directory
cd tools/VamsCLI

# Build the wheel file
python -m build
```

This creates files in the `dist/` directory:

-   `vamscli-X.X.X-py3-none-any.whl` (wheel file)
-   `vamscli-X.X.X.tar.gz` (source distribution)

### Install from Wheel File

Once you have the wheel file, you can install it on any system:

```bash
# Install from local wheel file
pip install dist/vamscli-X.X.X-py3-none-any.whl

# Or install from a remote wheel file
pip install https://example.com/path/to/vamscli-X.X.X-py3-none-any.whl
```

### Alternative Build Methods

**Using setuptools directly:**

```bash
# Build wheel using setuptools
python setup.py bdist_wheel

# Build source distribution
python setup.py sdist
```

**Clean build (removes previous builds):**

```bash
# Remove previous builds
rm -rf build/ dist/ *.egg-info/

# Build fresh wheel
python -m build
```

## Virtual Environment Setup

For isolated installations, use a virtual environment:

### Using venv (Python 3.3+)

```bash
# Create virtual environment
python -m venv vamscli-env

# Activate virtual environment
# On Windows:
vamscli-env\Scripts\activate
# On macOS/Linux:
source vamscli-env/bin/activate

# Install VamsCLI
pip install vamscli

# Verify installation
vamscli --version
```

### Using conda

```bash
# Create conda environment
conda create -n vamscli python=3.9

# Activate environment
conda activate vamscli

# Install VamsCLI
pip install vamscli

# Verify installation
vamscli --version
```

## Upgrading VamsCLI

### From PyPI

```bash
pip install --upgrade vamscli
```

### From Source

```bash
cd visual-asset-management-system/tools/VamsCLI
git pull origin main
pip install --upgrade .
```

## Uninstalling VamsCLI

### Remove Package

```bash
pip uninstall vamscli
```

### Remove Configuration

To completely remove VamsCLI including configuration files:

**Windows:**

```powershell
Remove-Item -Recurse -Force "$env:APPDATA\vamscli"
```

**macOS/Linux:**

```bash
rm -rf ~/.config/vamscli
# or on macOS:
rm -rf ~/Library/Application\ Support/vamscli
```

## Troubleshooting Installation

### Common Issues

**Issue**: `pip install vamscli` fails with permission errors
**Solution**: Use `pip install --user vamscli` or install in a virtual environment

**Issue**: `vamscli` command not found after installation
**Solution**: Ensure Python's script directory is in your PATH, or use `python -m vamscli`

**Issue**: Import errors when running VamsCLI
**Solution**: Ensure all dependencies are installed: `pip install --upgrade vamscli`

**Issue**: SSL certificate errors during setup
**Solution**: Ensure your system has up-to-date certificates and can access HTTPS URLs

### Getting Help

If you encounter installation issues:

1. Check the [Troubleshooting Guide](TROUBLESHOOTING.md)
2. Verify your Python version: `python --version`
3. Check pip version: `pip --version`
4. Try installing in a fresh virtual environment
5. Create an issue on GitHub with your system details and error messages

## Next Steps

After successful installation and setup:

1. **Authenticate**: Run `vamscli auth login -u <username>` to authenticate
2. **Explore Commands**: Use `vamscli --help` to see available commands
3. **Read Documentation**: Check the [Command Reference](COMMANDS.md) for detailed usage
4. **Get Support**: See [Troubleshooting](TROUBLESHOOTING.md) if you encounter issues

For complete usage instructions, see the [Command Reference](COMMANDS.md).
