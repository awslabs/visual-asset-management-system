# Setup and Authentication Issues

This document covers common setup, authentication, and profile-related issues in VamsCLI.

## Setup Issues

### Invalid API Gateway URL

**Error:**

```
✗ Setup failed: Invalid API Gateway URL. Please provide a valid HTTPS URL.
```

**Solutions:**

1. Ensure the URL is a valid HTTPS URL
2. Include the full API Gateway endpoint
3. Check for typos in the URL
4. Verify the URL is accessible from your network

**Example of correct URL:**

```bash
vamscli setup https://XXXXX.execute-api.us-west-2.amazonaws.com
```

### Failed to Get Amplify Configuration

**Error:**

```
✗ API Error: Failed to get Amplify configuration
```

**Solutions:**

1. Verify the API Gateway URL is correct
2. Ensure the `/api/amplify-config` endpoint is accessible
3. Check your network connection
4. Verify the VAMS deployment is running

### Version Mismatch

**Warning:**

```
⚠️  Version mismatch detected:
   CLI version: 2.2.0
   API version: 2.1.0
   This may cause compatibility issues.
```

**Solutions:**

1. Update VamsCLI to match your API version: `pip install --upgrade vamscli`
2. Contact your administrator about API version
3. Continue with caution if versions are close

## Authentication Issues

### Authentication Failed - Invalid Credentials

**Error:**

```
✗ Authentication failed: Invalid username or password
```

**Solutions:**

1. Verify your username (email address) is correct
2. Check your password is correct
3. Ensure your account is confirmed and active
4. Try resetting your password through the VAMS web interface
5. Contact your administrator if account issues persist

### User Account Not Confirmed

**Error:**

```
✗ Authentication failed: User account is not confirmed
```

**Solutions:**

1. Check your email for a confirmation link
2. Complete account confirmation through the VAMS web interface
3. Contact your administrator to confirm your account

### MFA Code Invalid

**Error:**

```
✗ Authentication failed: Invalid MFA code
```

**Solutions:**

1. Ensure you're entering the correct 6-digit code
2. For TOTP: Wait for a new code from your authenticator app
3. For SMS: Check your phone for the SMS message
4. Ensure your device's time is synchronized (for TOTP)

### MFA Setup Required

**Error:**

```
✗ Authentication failed: MFA setup required
```

**Solutions:**

1. Complete MFA setup in the VAMS web interface
2. Configure SMS or TOTP authentication
3. Contact your administrator for MFA setup assistance

### Token Refresh Failed

**Error:**

```
✗ Token refresh failed: Refresh token expired
```

**Solutions:**

1. Run `vamscli auth login` to re-authenticate
2. If you have saved credentials, they will be used automatically
3. Check if your account is still active

### Override Token Issues

**Error:**

```
✗ Override Token Error: Override token has expired
```

**Solutions:**

1. Provide a new override token: `vamscli auth set-override -u user@example.com --token "new_token"`
2. Use one-time override: `vamscli -u user@example.com --token-override "new_token" <command>`
3. Clear override and use Cognito: `vamscli auth clear-override`

## Profile Issues

### Invalid Profile Name

**Error:**

```
✗ Invalid profile name 'invalid name'. Profile names must be 3-50 characters, alphanumeric with hyphens and underscores only.
```

**Solutions:**

1. Use valid characters: letters, numbers, hyphens, underscores only
2. Ensure name is 3-50 characters long
3. Avoid reserved names: "help", "version", "list"

### Profile Does Not Exist

**Error:**

```
✗ Profile 'nonexistent' does not exist or is not configured.
```

**Solutions:**

1. Check available profiles: `vamscli profile list`
2. Create the profile: `vamscli setup <api-gateway-url> --profile <profile-name>`
3. Switch to existing profile: `vamscli profile switch <existing-profile>`

### Cannot Delete Default Profile

**Error:**

```
✗ Cannot delete the default profile
```

**Solution:**
The default profile cannot be deleted as it serves as the fallback profile. Create and switch to a different profile if needed.

### Configuration Not Found

**Error:**

```
✗ Setup Required: Configuration not found. Please run 'vamscli setup <api-gateway-url>' first.
```

**Solutions:**

1. Run the setup command: `vamscli setup <your-api-gateway-url>`
2. If using profiles: `vamscli setup <your-api-gateway-url> --profile <profile-name>`
3. Verify the setup completed successfully
4. Check configuration file exists in the profile directory

### Profile Configuration Not Found

**Error:**

```
✗ Configuration not found for profile 'production'. Please run 'vamscli setup <api-gateway-url> --profile production' first.
```

**Solutions:**

1. Run setup for the specific profile: `vamscli setup <your-api-gateway-url> --profile production`
2. Check if profile exists: `vamscli profile list`
3. Switch to an existing profile: `vamscli profile switch <existing-profile>`

### Corrupted Configuration

**Error:**

```
✗ Configuration Error: Failed to load configuration
```

**Solutions:**

1. Re-run setup: `vamscli setup <your-api-gateway-url> --force`
2. For specific profile: `vamscli setup <your-api-gateway-url> --profile <profile-name> --force`
3. Delete configuration directory and start over
4. Check file permissions in the profile directory

## Configuration Management

### Profile Locations

**Windows:**

```
%APPDATA%\vamscli\
├── config.json           # Main configuration
├── auth_profile.json     # Authentication tokens
└── credentials.json      # Saved credentials (optional)
```

**macOS:**

```
~/Library/Application Support/vamscli/
├── config.json           # Main configuration
├── auth_profile.json     # Authentication tokens
└── credentials.json      # Saved credentials (optional)
```

**Linux:**

```
~/.config/vamscli/
├── config.json           # Main configuration
├── auth_profile.json     # Authentication tokens
└── credentials.json      # Saved credentials (optional)
```

### Reset Configuration

If you need to completely reset VamsCLI:

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

Then run setup again:

```bash
vamscli setup <your-api-gateway-url>
```

## API Connectivity Issues

### API Unavailable

**Error:**

```
✗ API Unavailable: VAMS API is not currently available
```

**Solutions:**

1. Check your network connection
2. Verify the API Gateway URL is correct
3. Ensure the VAMS deployment is running
4. Try again after a few minutes
5. Contact your administrator

### API Version Incompatible

**Error:**

```
✗ API Unavailable: VAMS API version 2.0.0 detected. VamsCLI requires VAMS version 2.2.0 or higher.
```

**Solutions:**

1. Update your VAMS deployment to version 2.2.0 or higher
2. Use an older version of VamsCLI compatible with your API version
3. Contact your administrator about upgrading VAMS

### Authentication Required

**Error:**

```
✗ Authentication failed: Not authenticated. Please run 'vamscli auth login' to authenticate.
```

**Solutions:**

1. Run `vamscli auth login -u <username>` to authenticate
2. Check if your tokens have expired: `vamscli auth status`
3. Try refreshing tokens: `vamscli auth refresh`

## Network and Connectivity Issues

### SSL Certificate Errors

**Error:**

```
✗ API Error: SSL certificate verification failed
```

**Solutions:**

1. Ensure your system has up-to-date certificates
2. Check if you're behind a corporate firewall
3. Verify the API Gateway URL uses valid SSL certificates
4. Contact your network administrator

### Timeout Issues

**Error:**

```
✗ API Unavailable: VAMS API is not responding. The service may be temporarily unavailable.
```

**Solutions:**

1. Check your internet connection
2. Try again after a few minutes
3. Verify the VAMS service is running
4. Contact your administrator

### Proxy Issues

If you're behind a corporate proxy:

1. Set environment variables for proxy:

    ```bash
    export HTTP_PROXY=http://proxy.company.com:8080
    export HTTPS_PROXY=http://proxy.company.com:8080
    ```

2. Install VamsCLI from source or wheel file with proxy settings:

    ```bash
    # From source
    cd path/to/visual-asset-management-system/tools/VamsCLI
    pip install --proxy http://proxy.company.com:8080 .

    # Or from wheel file
    pip install --proxy http://proxy.company.com:8080 path/to/vamscli-X.X.X-py3-none-any.whl
    ```

## Debug Mode

For detailed error information, use debug mode:

```bash
vamscli --debug <command>
```

This provides:

-   Full stack traces for errors
-   Detailed API request/response information
-   Internal state information
-   Timing information

**Example:**

```bash
vamscli --debug auth login -u user@example.com
vamscli --debug setup https://api.example.com
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
```

### Check API Connectivity

```bash
# Test basic connectivity (this will show version mismatch if any)
vamscli setup <your-api-url> --force
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

## Common Troubleshooting Workflows

### Setup and Authentication Workflow

```bash
# 1. Check current status
vamscli auth status

# 2. If not setup, run setup
vamscli setup https://your-api-gateway.execute-api.region.amazonaws.com

# 3. If not authenticated, login
vamscli auth login -u your-username@example.com

# 4. Verify authentication
vamscli auth status

# 5. Test basic functionality
vamscli database list
```

### Profile Management Workflow

```bash
# 1. List available profiles
vamscli profile list

# 2. Check current profile
vamscli profile current

# 3. Switch profile if needed
vamscli profile switch <profile-name>

# 4. Setup new profile if needed
vamscli setup <api-url> --profile <new-profile>

# 5. Authenticate to new profile
vamscli auth login -u <username> --profile <new-profile>
```

### Token Management Workflow

```bash
# 1. Check token status
vamscli auth status

# 2. Refresh tokens if needed
vamscli auth refresh

# 3. If refresh fails, re-authenticate
vamscli auth login -u <username>

# 4. For override tokens
vamscli auth set-override -u <username> --token <token>

# 5. Clear override when done
vamscli auth clear-override
```

## Frequently Asked Questions

### Q: Why do I get "Setup Required" errors?

**A:** VamsCLI requires initial setup with your API Gateway URL. Run `vamscli setup <url>` first.

### Q: Can I use VamsCLI without internet?

**A:** No, VamsCLI requires internet access to communicate with the VAMS API Gateway.

### Q: How do I change my API Gateway URL?

**A:** Run `vamscli setup <new-url> --force` to update your configuration.

### Q: Can I use multiple VAMS environments?

**A:** Yes, use profiles. Run `vamscli setup <url> --profile <name>` for each environment.

### Q: How do I automate VamsCLI in scripts?

**A:** Use override tokens and JSON input/output. See the authentication guide for examples.

### Q: My tokens keep expiring, what should I do?

**A:** Use `--save-credentials` when logging in, or set up override tokens for automation.

### Q: How do I know if my VAMS deployment is compatible?

**A:** VamsCLI will show version mismatch warnings. Ensure VAMS is version 2.2.0 or higher.

### Q: Can I use VamsCLI with SAML authentication?

**A:** Yes, but you'll need to use override tokens. Contact your administrator for token setup.
