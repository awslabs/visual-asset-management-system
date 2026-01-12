# General Troubleshooting

This document covers debug mode usage, performance optimization, recovery procedures, and general VamsCLI troubleshooting.

## Logging and Verbose Mode

VamsCLI provides comprehensive logging capabilities for troubleshooting and debugging.

### Automatic Error Logging

**All errors and warnings are automatically logged to file**, regardless of verbose mode:

**Log File Location:**

-   **Windows**: `%APPDATA%\vamscli\logs\vamscli.log`
-   **macOS**: `~/Library/Application Support/vamscli/logs/vamscli.log`
-   **Linux**: `~/.config/vamscli/logs/vamscli.log`

**What Gets Logged:**

-   All command invocations (start/end, duration, success/failure)
-   All exceptions with full stack traces
-   All API requests and responses (with sensitive data redacted)
-   All warnings
-   Configuration details
-   Timing information

**Log Format:**

```
[2025-01-27 11:55:28] [INFO] [default] [assets.list] Command started: assets.list
[2025-01-27 11:55:29] [DEBUG] [default] [assets.list] API Request: GET /database/my-db/assets
[2025-01-27 11:55:29] [DEBUG] [default] [assets.list] API Response: 200 (0.23s)
[2025-01-27 11:55:29] [INFO] [default] [assets.list] Command completed successfully (duration: 0.25s)
```

**Log Rotation:**

-   Maximum file size: 10MB
-   Backup count: 5 files
-   Automatic rotation when size limit reached
-   Files: `vamscli.log`, `vamscli.log.1`, `vamscli.log.2`, etc.

### Verbose Mode

For detailed output in the console, use the `--verbose` flag:

```bash
vamscli --verbose <command>
```

**Verbose Mode Features:**

-   **Configuration Details**: Shows profile name, API gateway URL, CLI version
-   **API Request/Response Logging**: Displays all API calls with timing
-   **Full Stack Traces**: Shows complete error details for debugging
-   **Timing Information**: Shows command execution duration
-   **Warning Messages**: Displays all warnings in console

**Example Verbose Output:**

```bash
$ vamscli --verbose assets get my-db my-asset

📋 Using profile: default
📋 API Gateway: https://api.example.com
📋 CLI Version: 2.2.0

→ API Request: GET /database/my-db/assets/my-asset
← API Response: 200 (0.23s)

Asset Details:
  Asset ID: my-asset
  Database: my-db
  ...

✓ Command completed successfully in 0.25s
```

**Verbose Mode with Errors:**

```bash
$ vamscli --verbose assets get my-db nonexistent

📋 Using profile: default
📋 API Gateway: https://api.example.com
📋 CLI Version: 2.2.0

→ API Request: GET /database/my-db/assets/nonexistent
← API Response: 404 (0.18s)

✗ Asset Error: Asset 'nonexistent' not found

VERBOSE ERROR DETAILS:
Traceback (most recent call last):
  File "vamscli/commands/assets.py", line 123, in get
    result = api_client.get_asset(database_id, asset_id)
  File "vamscli/utils/api_client.py", line 456, in get_asset
    raise AssetNotFoundError(f"Asset '{asset_id}' not found")
vamscli.utils.exceptions.AssetNotFoundError: Asset 'nonexistent' not found

✗ Command failed after 0.20s
```

### Debug Mode (Legacy)

For backward compatibility, `--debug` mode is still supported:

```bash
vamscli --debug <command>
```

**Note**: `--verbose` is now the recommended approach for detailed output. The `--debug` flag provides similar functionality but with less structured output.

### Viewing Log Files

**View recent log entries:**

```bash
# Windows PowerShell
Get-Content "$env:APPDATA\vamscli\logs\vamscli.log" -Tail 50

# macOS/Linux
tail -50 ~/.config/vamscli/logs/vamscli.log
```

**Search log files for specific errors:**

```bash
# Windows PowerShell
Select-String -Path "$env:APPDATA\vamscli\logs\vamscli.log" -Pattern "ERROR"

# macOS/Linux
grep "ERROR" ~/.config/vamscli/logs/vamscli.log
```

**View logs for specific command:**

```bash
# Windows PowerShell
Select-String -Path "$env:APPDATA\vamscli\logs\vamscli.log" -Pattern "assets.create"

# macOS/Linux
grep "assets.create" ~/.config/vamscli/logs/vamscli.log
```

**View logs for specific profile:**

```bash
# Windows PowerShell
Select-String -Path "$env:APPDATA\vamscli\logs\vamscli.log" -Pattern "\[production\]"

# macOS/Linux
grep "\[production\]" ~/.config/vamscli/logs/vamscli.log
```

### Log File Management

**Check log file size:**

```bash
# Windows PowerShell
Get-Item "$env:APPDATA\vamscli\logs\vamscli.log" | Select-Object Length, LastWriteTime

# macOS/Linux
ls -lh ~/.config/vamscli/logs/vamscli.log
```

**Clear log files (if needed):**

```bash
# Windows PowerShell
Remove-Item "$env:APPDATA\vamscli\logs\vamscli.log*"

# macOS/Linux
rm ~/.config/vamscli/logs/vamscli.log*
```

**Archive log files:**

```bash
# Windows PowerShell
Compress-Archive -Path "$env:APPDATA\vamscli\logs" -DestinationPath "vamscli-logs-$(Get-Date -Format 'yyyy-MM-dd').zip"

# macOS/Linux
tar -czf "vamscli-logs-$(date +%Y-%m-%d).tar.gz" ~/.config/vamscli/logs/
```

## Getting Detailed Information

### Check System Information

```bash
# Check VamsCLI version
vamscli --version

# Check Python version
python --version

# Check pip version
pip --version

# Check installed packages
pip list | grep vamscli
```

### Check Configuration

```bash
# Check authentication status
vamscli auth status

# Check if setup is complete
vamscli --help  # Should not show setup required error

# Check current profile
vamscli profile current

# List all profiles
vamscli profile list
```

### Check API Connectivity

```bash
# Test basic connectivity (this will show version mismatch if any)
vamscli setup <your-api-url> --force

# Test authentication
vamscli auth status

# Test basic API functionality
vamscli database list
```

## Performance Optimization

### File Upload Optimization

```bash
# For slow connections
vamscli file upload --parallel-uploads 3 <files>

# For fast connections
vamscli file upload --parallel-uploads 15 <files>

# Increase retry attempts for unreliable connections
vamscli file upload --retry-attempts 5 <files>

# Use force skip for automation
vamscli file upload --force-skip <files>
```

### General Performance

1. **Use JSON Output**: Faster parsing for automation
2. **Hide Progress**: Reduces terminal overhead
3. **Batch Operations**: Group related operations together
4. **Profile Management**: Use appropriate profiles for different environments

### Memory Management

```bash
# For large file operations, monitor memory usage
# Close unnecessary applications
# Ensure sufficient disk space for temporary files

# Use smaller batch sizes for large operations
vamscli file upload --parallel-uploads 5 <large-files>
```

## Error Code Reference

VamsCLI uses standard exit codes:

-   **0**: Success
-   **1**: General error (authentication, API, validation, etc.)
-   **2**: Command line usage error (invalid arguments, missing options)

Use these in scripts:

```bash
#!/bin/bash
vamscli assets create -d my-db --name "Test"
if [ $? -eq 0 ]; then
    echo "Asset created successfully"
else
    echo "Asset creation failed"
    exit 1
fi
```

### PowerShell Exit Code Handling

```powershell
vamscli assets create -d my-db --name "Test"
if ($LASTEXITCODE -eq 0) {
    Write-Host "Asset created successfully"
} else {
    Write-Host "Asset creation failed"
    exit 1
}
```

## Command Completion and Interruption

### Safe Command Interruption

VamsCLI commands can be interrupted safely with Ctrl+C:

```
Operation cancelled by user.
```

For file uploads, interrupting will abort the current upload sequence and clean up temporary resources.

### Handling Interrupted Operations

```bash
# If an upload was interrupted, you can safely retry
vamscli file upload -d my-db -a my-asset /path/to/files/

# VamsCLI will handle cleanup of any partial uploads
# No manual cleanup is required
```

## Recovery Procedures

### Complete Reset

If VamsCLI is completely broken:

1. **Uninstall VamsCLI:**

    ```bash
    pip uninstall vamscli
    ```

2. **Remove configuration:**

    ```bash
    # Windows
    Remove-Item -Recurse -Force "$env:APPDATA\vamscli"

    # macOS/Linux
    rm -rf ~/.config/vamscli
    # or on macOS:
    rm -rf ~/Library/Application\ Support/vamscli
    ```

3. **Reinstall from source or wheel:**

    ```bash
    # From source
    cd path/to/visual-asset-management-system/tools/VamsCLI
    pip install .

    # Or from wheel file
    pip install path/to/vamscli-X.X.X-py3-none-any.whl
    ```

4. **Reconfigure:**
    ```bash
    vamscli setup <your-api-gateway-url>
    vamscli auth login -u <username>
    ```

### Partial Reset

To reset only authentication:

```bash
vamscli auth logout
vamscli auth login -u <username>
```

To reset only configuration:

```bash
vamscli setup <your-api-gateway-url> --force
```

To reset specific profile:

```bash
vamscli setup <your-api-gateway-url> --profile <profile-name> --force
vamscli auth login -u <username> --profile <profile-name>
```

## Configuration File Locations

### Profile Locations

**Windows:**

```
%APPDATA%\vamscli\
├── profiles/
│   ├── default/
│   │   ├── config.json           # Main configuration
│   │   ├── auth_profile.json     # Authentication tokens
│   │   └── credentials.json      # Saved credentials (optional)
│   ├── production/
│   └── staging/
└── active_profile.json           # Current active profile
```

**macOS:**

```
~/Library/Application Support/vamscli/
├── profiles/
│   ├── default/
│   │   ├── config.json           # Main configuration
│   │   ├── auth_profile.json     # Authentication tokens
│   │   └── credentials.json      # Saved credentials (optional)
│   ├── production/
│   └── staging/
└── active_profile.json           # Current active profile
```

**Linux:**

```
~/.config/vamscli/
├── profiles/
│   ├── default/
│   │   ├── config.json           # Main configuration
│   │   ├── auth_profile.json     # Authentication tokens
│   │   └── credentials.json      # Saved credentials (optional)
│   ├── production/
│   └── staging/
└── active_profile.json           # Current active profile
```

### Configuration File Contents

**config.json:**

```json
{
    "api_gateway_url": "https://your-api-gateway.execute-api.region.amazonaws.com",
    "cli_version": "2.2.0",
    "amplify_config": {
        "region": "us-west-2",
        "userPoolId": "us-west-2_XXXXXXXXX",
        "userPoolWebClientId": "XXXXXXXXXXXXXXXXXXXXXXXXXX"
    }
}
```

**auth_profile.json:**

```json
{
    "user_id": "user@example.com",
    "auth_type": "cognito",
    "access_token": "...",
    "id_token": "...",
    "refresh_token": "...",
    "expires_at": "2024-01-01T12:00:00Z"
}
```

## Validation and Testing

### Validate JSON Input

Test JSON input separately:

```bash
# Validate JSON syntax
echo '{"test": "value"}' | python -m json.tool

# Test JSON file
python -m json.tool < input.json

# Validate specific VamsCLI JSON input
cat asset-config.json | python -m json.tool
```

### Test VamsCLI Installation

```bash
# Test basic installation
vamscli --version

# Test help system
vamscli --help

# Test command groups
vamscli auth --help
vamscli assets --help

# Test specific commands
vamscli auth login --help
```

### Test API Connectivity

```bash
# Test setup process
vamscli setup https://your-api-gateway.com --force

# Test authentication
vamscli auth login -u test-user@example.com

# Test basic API calls
vamscli database list
vamscli assets list
```

## Common Troubleshooting Patterns

### Systematic Troubleshooting Approach

1. **Identify the Problem Area:**

    - Setup/Configuration
    - Authentication
    - Network/Connectivity
    - API Operations
    - File Operations

2. **Gather Information:**

    ```bash
    vamscli --version
    vamscli auth status
    vamscli profile current
    vamscli --debug <failing-command>
    ```

3. **Test Basic Functionality:**

    ```bash
    vamscli --help
    vamscli setup <api-url> --force
    vamscli auth login -u <username>
    vamscli database list
    ```

4. **Isolate the Issue:**

    - Test with different profiles
    - Test with different commands
    - Test with debug mode
    - Test network connectivity separately

5. **Apply Solutions:**
    - Follow specific error guidance
    - Use recovery procedures if needed
    - Contact support if issue persists

### Error Investigation Workflow

```bash
# 1. Capture the exact error
vamscli --debug <failing-command> 2>&1 | tee error-log.txt

# 2. Check system state
vamscli --version
vamscli auth status
vamscli profile list

# 3. Test basic connectivity
curl -I https://your-api-gateway.com/api/version

# 4. Test with minimal command
vamscli database list

# 5. Compare with working environment (if available)
vamscli database list --profile <working-profile>
```

## Logging and Monitoring

### Enable Comprehensive Logging

```bash
# Set environment variables for maximum logging
export VAMSCLI_DEBUG=1
export PYTHONPATH=.

# Run command with full logging
vamscli --debug <command> 2>&1 | tee full-debug.log
```

### Log Analysis

```bash
# Search for specific errors in logs
grep -i "error" full-debug.log
grep -i "failed" full-debug.log
grep -i "exception" full-debug.log

# Look for network-related issues
grep -i "connection" full-debug.log
grep -i "timeout" full-debug.log
grep -i "ssl" full-debug.log
```

## Getting Help

### Self-Help Resources

1. **Command Help**: Use `--help` with any command

    ```bash
    vamscli --help
    vamscli auth --help
    vamscli assets create --help
    ```

2. **Documentation**: Check the documentation files in the `docs/` directory

3. **Debug Mode**: Use `--debug` for detailed error information

### Community Support

1. **GitHub Issues**: Search existing [issues](https://github.com/awslabs/visual-asset-management-system/issues)
2. **Create New Issue**: If you can't find a solution, create a new issue with:
    - VamsCLI version (`vamscli --version`)
    - Python version (`python --version`)
    - Operating system
    - Complete error message
    - Steps to reproduce

### Enterprise Support

If you're using VAMS in an enterprise environment:

1. **Contact Your Administrator**: They may have specific configuration requirements
2. **Check Internal Documentation**: Your organization may have specific setup guides
3. **Network Requirements**: Verify firewall and proxy settings

## Reporting Bugs

When reporting bugs, please include:

1. **VamsCLI Version**: `vamscli --version`
2. **Python Version**: `python --version`
3. **Operating System**: Windows/macOS/Linux version
4. **Command Used**: Exact command that failed
5. **Error Message**: Complete error output
6. **Debug Output**: Run with `--debug` and include output
7. **Steps to Reproduce**: Clear steps to reproduce the issue

**Example Bug Report:**

```
VamsCLI Version: 2.2.0
Python Version: 3.9.7
OS: Windows 11
Command: vamscli assets create -d test-db --name "Test"
Error: ✗ Authentication failed: Token has expired
Debug Output: [include --debug output]
Steps: 1. Run setup, 2. Login, 3. Wait 1 hour, 4. Run command
```

## Advanced Troubleshooting Techniques

### Environment Isolation

```bash
# Create isolated environment for testing
python -m venv vamscli-test
source vamscli-test/bin/activate  # Windows: vamscli-test\Scripts\activate

# Install VamsCLI in isolated environment from source
cd path/to/visual-asset-management-system/tools/VamsCLI
pip install .

# Or from wheel file
# pip install path/to/vamscli-X.X.X-py3-none-any.whl

# Test in isolated environment
vamscli --version
vamscli setup <api-url>
```

### Configuration Comparison

```bash
# Compare configurations between working and non-working environments
vamscli profile info working-profile --json-output > working-config.json
vamscli profile info broken-profile --json-output > broken-config.json

# Compare the files to identify differences
# Windows: fc working-config.json broken-config.json
# macOS/Linux: diff working-config.json broken-config.json
```

### Network Path Analysis

```bash
# Trace network path to API Gateway
traceroute your-api-gateway-domain.com  # Linux/macOS
tracert your-api-gateway-domain.com     # Windows

# Test different network paths
curl --interface eth0 https://your-api-gateway.com/api/version  # Linux
curl --interface en0 https://your-api-gateway.com/api/version   # macOS
```

## Performance Monitoring

### Monitor Resource Usage

```bash
# Monitor CPU and memory usage during operations
# Windows: Task Manager or Resource Monitor
# macOS: Activity Monitor
# Linux: top, htop, or system monitor

# Monitor network usage during file uploads
# Use system network monitoring tools
```

### Benchmark Operations

```bash
# Time operations for performance analysis
time vamscli file upload -d my-db -a my-asset large-file.gltf

# Compare different settings
time vamscli file upload --parallel-uploads 5 -d my-db -a my-asset files/
time vamscli file upload --parallel-uploads 10 -d my-db -a my-asset files/
```

## System Integration Issues

### Python Environment Issues

**Error:**

```
✗ Import Error: No module named 'vamscli'
```

**Solutions:**

1. Ensure VamsCLI is installed from source or wheel file
2. Check if you're in the correct Python environment
3. Verify Python path: `python -c "import sys; print(sys.path)"`
4. Reinstall if necessary: `pip uninstall vamscli` then reinstall from source or wheel

### Path and Environment Issues

**Error:**

```
✗ Command not found: vamscli
```

**Solutions:**

1. Ensure VamsCLI is installed from source or wheel file
2. Check if pip install location is in PATH
3. Try running with full path: `python -m vamscli`
4. Verify Python scripts directory is in PATH

### Permission Issues

**Error:**

```
✗ Permission denied: Cannot access configuration directory
```

**Solutions:**

1. Check file permissions on configuration directory
2. Run with appropriate user permissions
3. Ensure configuration directory is writable
4. Contact system administrator if needed

## Automation and Scripting Issues

### Script Integration Problems

```bash
# Test exit codes in scripts
vamscli assets create -d test-db --name "Test"
echo "Exit code: $?"  # Should be 0 for success

# Handle errors in bash scripts
set -e  # Exit on any error
vamscli assets create -d test-db --name "Test" || {
    echo "Asset creation failed"
    exit 1
}
```

### JSON Processing Issues

```bash
# Validate JSON output
vamscli assets list --json-output | python -m json.tool

# Process JSON in scripts
result=$(vamscli assets get my-asset -d my-db --json-output)
echo "$result" | python -c "import sys, json; data=json.load(sys.stdin); print(data['assetName'])"
```

### Batch Operation Issues

```bash
# Handle batch operations with error checking
for asset in asset1 asset2 asset3; do
    echo "Processing $asset..."
    if ! vamscli assets get -d my-db "$asset" --json-output > /dev/null 2>&1; then
        echo "Warning: Asset $asset not found, skipping..."
        continue
    fi

    # Process the asset
    vamscli file upload -d my-db -a "$asset" "files/${asset}/"
done
```

## Recovery and Backup Procedures

### Configuration Backup

```bash
# Backup all profiles
vamscli profile list --json-output > profiles-backup.json

# Backup specific profile configuration
vamscli profile info default --json-output > default-profile-backup.json
vamscli profile info production --json-output > production-profile-backup.json

# Create full configuration backup
# Windows
xcopy /E /I "%APPDATA%\vamscli" "vamscli-backup"

# macOS/Linux
cp -r ~/.config/vamscli vamscli-backup
# or on macOS:
cp -r ~/Library/Application\ Support/vamscli vamscli-backup
```

### Configuration Restore

```bash
# Restore from backup
# Windows
xcopy /E /I "vamscli-backup" "%APPDATA%\vamscli"

# macOS/Linux
cp -r vamscli-backup ~/.config/vamscli
# or on macOS:
cp -r vamscli-backup ~/Library/Application\ Support/vamscli

# Verify restoration
vamscli profile list
vamscli auth status
```

### Selective Recovery

```bash
# Recover specific profile
vamscli setup <api-url> --profile <profile-name>
vamscli auth login -u <username> --profile <profile-name>

# Recover authentication only
vamscli auth logout
vamscli auth login -u <username>

# Recover configuration only
vamscli setup <api-url> --force
```

## Troubleshooting Workflows

### Comprehensive Diagnostic Workflow

```bash
#!/bin/bash
# VamsCLI Diagnostic Script

echo "=== VamsCLI Diagnostic Information ==="
echo "Date: $(date)"
echo "User: $(whoami)"
echo "Working Directory: $(pwd)"
echo

echo "=== System Information ==="
echo "OS: $(uname -a)"
echo "Python Version: $(python --version)"
echo "Pip Version: $(pip --version)"
echo

echo "=== VamsCLI Information ==="
echo "VamsCLI Version: $(vamscli --version 2>/dev/null || echo 'Not installed or not in PATH')"
echo "VamsCLI Location: $(which vamscli 2>/dev/null || echo 'Not found in PATH')"
echo

echo "=== Profile Information ==="
vamscli profile list 2>/dev/null || echo "Profile list failed"
echo "Current Profile: $(vamscli profile current 2>/dev/null || echo 'Failed to get current profile')"
echo

echo "=== Authentication Status ==="
vamscli auth status 2>/dev/null || echo "Auth status failed"
echo

echo "=== Network Connectivity ==="
echo "Testing API connectivity..."
curl -I https://your-api-gateway.com/api/version 2>/dev/null || echo "API connectivity test failed"
echo

echo "=== Configuration Files ==="
echo "Configuration directory contents:"
# Windows: dir "%APPDATA%\vamscli" /s
# macOS/Linux: find ~/.config/vamscli -type f 2>/dev/null || echo "Configuration directory not found"

echo "=== Recent Errors ==="
echo "Testing basic operations..."
vamscli --debug database list 2>&1 | head -20
```

### Quick Diagnostic Commands

```bash
# Quick system check
vamscli --version && vamscli auth status && vamscli profile current

# Quick connectivity check
curl -I https://your-api-gateway.com/api/version

# Quick functionality check
vamscli database list --json-output | head -5

# Quick configuration check
vamscli profile list
```

## Frequently Asked Questions

### Q: How do I enable maximum debugging?

**A:** Use `export VAMSCLI_DEBUG=1` and `vamscli --debug <command>` for maximum debugging information.

### Q: How do I reset VamsCLI completely?

**A:** Uninstall VamsCLI, delete configuration directory, reinstall, and reconfigure.

### Q: How do I backup my VamsCLI configuration?

**A:** Copy the entire configuration directory and use `vamscli profile list --json-output` for profile information.

### Q: Why do some operations work and others don't?

**A:** This usually indicates permission issues, network problems, or resource-specific errors. Use debug mode to investigate.

### Q: How do I optimize VamsCLI performance?

**A:** Use JSON output, hide progress for automation, adjust parallel upload settings, and use appropriate profiles.

### Q: What should I include when reporting bugs?

**A:** Version information, exact commands, complete error messages, debug output, and steps to reproduce.

### Q: How do I test if VamsCLI is working correctly?

**A:** Run the diagnostic workflow above, test basic operations, and verify with known-good commands.

### Q: How do I handle intermittent issues?

**A:** Use retry logic in scripts, increase retry attempts for uploads, and monitor network stability.

## Still Need Help?

If this guide doesn't resolve your issue:

1. **Check Documentation:**

    - Setup and Authentication Issues
    - Asset and File Operation Issues
    - Database and Tag Management Issues
    - Network and Configuration Issues

2. **Search GitHub Issues:**

    - [Existing Issues](https://github.com/awslabs/visual-asset-management-system/issues)

3. **Create New Issue:**

    - Include all diagnostic information
    - Follow the bug report template
    - Be specific about your environment and steps

4. **Contact Support:**
    - Enterprise users: Contact your administrator
    - Open source users: Create a GitHub issue

Remember to include your VamsCLI version, Python version, operating system, and complete error messages when seeking help.
