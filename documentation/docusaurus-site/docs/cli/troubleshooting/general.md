---
sidebar_label: General and Debugging
title: General Troubleshooting and Debugging
---

# General Troubleshooting and Debugging

This page covers logging, verbose output, exit codes, retry configuration, recovery procedures, and how to gather the information needed to report a VamsCLI issue.

---

## Logging and Verbose Mode

VamsCLI writes all errors and warnings to a rotating log file automatically, regardless of whether verbose mode is enabled. The log captures command invocations and timing, exceptions with stack traces, and API requests and responses with sensitive data redacted.

### Log File Location

The log file location depends on your operating system:

| Platform | Path                                                     |
| -------- | -------------------------------------------------------- |
| Windows  | `%APPDATA%\vamscli\logs\vamscli.log`                     |
| macOS    | `~/Library/Application Support/vamscli/logs/vamscli.log` |
| Linux    | `~/.config/vamscli/logs/vamscli.log`                     |

Logs rotate at 10 MB with up to five backups (`vamscli.log`, `vamscli.log.1`, and so on). The log file and each rotated backup are created readable only by their owner, on platforms whose filesystem honors that mode.

### What Redaction Covers

Redaction works two ways on every command argument list, request header set, request body, and response body written to the log. Fields whose name identifies a credential are replaced with `***REDACTED***`, matched on the normalized name so variants such as `new_password`, `access_token`, and `client-secret` are covered. Credential-shaped values are then replaced wherever they appear in already-rendered text, which catches VAMS API keys, JSON Web Tokens, `Bearer` header values, and the `X-Amz-Signature` and `X-Amz-Security-Token` parameters of a presigned URL.

Two categories are left in the clear on purpose, because they carry diagnostic value and no credential: pagination cursors (`startingToken`, `NextToken`), and fields that describe a credential rather than contain one (`apiKeyId`, `apiKeyName`, `tokenType`, `credentialsSecretArn`).

:::note
Redaction is name-based and shape-based, not exhaustive. A secret that sits under a field name VAMS does not recognize as credential-bearing, and does not match one of the credential value shapes — a password pasted into a free-form description, for example — is written to the log as given.
:::

### Verbose Output

Add `--verbose` to any command for detailed console output, including the active profile, API Gateway URL, CLI version, per-request timing, and full stack traces on failure:

```bash
vamscli --verbose assets get my-asset -d my-db
```

:::tip
Verbose log messages are written to the rotating log file and do not interfere with `--json-output`, so both flags can be combined safely in scripts.
:::

### Viewing the Log

```bash
# macOS / Linux: tail the most recent entries
tail -50 ~/.config/vamscli/logs/vamscli.log

# macOS / Linux: search for errors
grep "ERROR" ~/.config/vamscli/logs/vamscli.log
```

```powershell
# Windows PowerShell: tail the most recent entries
Get-Content "$env:APPDATA\vamscli\logs\vamscli.log" -Tail 50

# Windows PowerShell: search for errors
Select-String -Path "$env:APPDATA\vamscli\logs\vamscli.log" -Pattern "ERROR"
```

---

## Terminal Encoding on Windows

VamsCLI prints Unicode status indicators (for example, `✓` and `✗`) and sets its own output encoding to UTF-8, so the system code page does not affect whether a command succeeds. This applies to redirected output as well as to a console.

**Symptoms:**

-   `UnicodeEncodeError` or `charmap codec can't encode character` in place of a command's output

**Cause:**

The installed VamsCLI leaves the output encoding to the operating system. On Windows that resolves to the ANSI code page (typically `cp1252`) whenever output is not going to a console, so redirecting or piping a command that prints a status indicator fails instead of producing output — `vamscli profile list > profiles.txt` writes a single line naming a codec error.

**Resolution:**

Update VamsCLI to the release that matches your deployment (see [Updating VamsCLI](../installation.md#updating-vamscli)); it sets the encoding itself. Until you can update, set the encoding before invoking the CLI:

```bash
export PYTHONIOENCODING=utf-8
```

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

:::note
A legacy Command Prompt may draw a character its font does not contain as a box or a question mark. That is a font limitation rather than an error, and the command still completes with its normal exit code.
:::

---

## Exit Codes

VamsCLI returns standard process exit codes, which scripts can use for control flow:

| Exit Code | Meaning                                                            |
| --------- | ------------------------------------------------------------------ |
| `0`       | Command completed successfully                                     |
| `1`       | Command failed (authentication, API, validation, or other error)   |
| `2`       | Invalid command usage (missing required options, unknown commands) |

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

```powershell
vamscli assets create -d my-db --name "Test"
if ($LASTEXITCODE -eq 0) {
    Write-Host "Asset created successfully"
} else {
    Write-Host "Asset creation failed"
    exit 1
}
```

:::note
When `--json-output` is enabled, errors are emitted as a JSON object to stdout alongside the non-zero exit code. See [Automation and Scripting](../automation.md) for the JSON error format.
:::

---

## Retry Configuration

VamsCLI automatically retries requests that receive HTTP 429 (Too Many Requests) responses using exponential backoff with jitter, honoring any server-provided `Retry-After` header. Customize the behavior through environment variables:

| Environment Variable                | Default | Description                                            |
| ----------------------------------- | ------- | ------------------------------------------------------ |
| `VAMS_CLI_MAX_RETRY_ATTEMPTS`       | `5`     | Maximum retry attempts per request                     |
| `VAMS_CLI_INITIAL_RETRY_DELAY`      | `1.0`   | Initial delay in seconds before the first retry        |
| `VAMS_CLI_MAX_RETRY_DELAY`          | `60.0`  | Maximum delay in seconds between retries               |
| `VAMS_CLI_RETRY_BACKOFF_MULTIPLIER` | `2.0`   | Multiplier applied to the delay on each attempt        |
| `VAMS_CLI_RETRY_JITTER`             | `0.1`   | Random jitter fraction to prevent synchronized retries |

For bulk operations that may trigger throttling, raise the retry budget:

```bash
export VAMS_CLI_MAX_RETRY_ATTEMPTS=10
export VAMS_CLI_INITIAL_RETRY_DELAY=2.0
export VAMS_CLI_MAX_RETRY_DELAY=180.0
```

:::note[Values are clamped to safe bounds]
VamsCLI validates and bounds each setting: retry attempts to 0–20, the initial delay to 0.1–30 seconds, the maximum delay to at most 300 seconds, the backoff multiplier to 1.0–5.0, and jitter to 0.0–0.5. Out-of-range or non-numeric values fall back to the defaults shown above.
:::

---

## Request Blocked (AWS WAF)

**Symptoms:**

-   A command fails with `✗ Request Blocked: The deployment's AWS WAF web ACL blocked this request before it reached VAMS (HTTP 403). A managed rule matched something in the request, most often a value in the request body. Signing in again does not change the result.`, followed by a line asking the administrator to look the request up, with its request ID.
-   With `--json-output`, the error document's `error_type` is `RequestBlockedError`, its `blockedBy` is `waf`, and its `requestId` is API Gateway's extended request ID.
-   `vamscli setup` refuses the URL because the probe `was blocked by the deployment's AWS WAF web ACL`.
-   `vamscli auth login`, `auth change-password` or `auth forgot-password` stops with `Request Blocked` before it contacts Amazon Cognito.

**Cause:**

The deployment enables AWS WAF, and a rule of its web ACL blocked the request at the API Gateway stage before VAMS evaluated it. API Gateway answers such a request with HTTP 403 and a body that names AWS WAF and carries the request ID, which the CLI reports. Most often a managed rule matched a value in the request body: an asset description or a metadata value can resemble an attack signature. When setup or the version check is blocked, a rule matched the source address or the request as a whole, so every command from that network is blocked the same way.

Signing in again does not help: the web ACL evaluates the request before the VAMS authorizer, so a fresh token does not change the result. The CLI therefore sends the request once, does not refresh the token or retry, and keeps the stored credentials.

**Resolution:**

Give the VAMS administrator the request ID and the time of the command. The request ID is API Gateway's extended request ID, the value of the `x-amz-apigw-id` response header. The administrator looks it up in the web ACL's `aws-waf-logs-vams-*` log group, where the request's record carries it as `httpRequest.requestId` and names the rule that matched. The web ACL's sampled requests carry no request ID, so a request is matched there by its time, URI and client IP. The administrator then changes the request (for example, the value that matched), or overrides the rule to `count` in `infra/config/policy/wafPolicyConfig.json` and redeploys; see the [WAF rule policy](../../deployment/configuration-reference.md#waf-rule-policy-configpolicywafpolicyconfigjson).

**A bare `Forbidden` (`waf-or-stage`):**

API Gateway also answers a block with a bare `403` `{"message": "Forbidden"}` where the deployment's API does not return the web ACL body, and it answers the same way for a base URL that names no deployed stage. On a signed-in command the CLI reports this as `Request Blocked` too, with `blockedBy` `waf-or-stage`, the message `Amazon API Gateway refused this request with a bare 403 "Forbidden". ...`, and a line that names both checks: the web ACL's logs when the deployment has one, otherwise the stored URL's stage path. Check the stored API Gateway URL with `vamscli profile info`: it must end in the stage path, `/api`. If it does, treat the error as a web ACL block. A request the CLI sends without credentials reports the same bare `Forbidden` as an API error whose message names both causes, and the version check as `API Unavailable`.

---

## Recovery Procedures

### Reset Authentication Only

```bash
vamscli auth logout
vamscli auth login -u <username>
```

### Reset Configuration Only

```bash
vamscli setup <your-api-gateway-url> --force
```

Target a specific profile by adding `--profile <name>` to both commands.

### Complete Reset

If VamsCLI is in an unrecoverable state, reinstall and reconfigure:

1. Uninstall the package:

    ```bash
    pip uninstall vamscli
    ```

2. Remove the configuration directory:

    ```bash
    # macOS / Linux
    rm -rf ~/.config/vamscli

    # Windows PowerShell
    Remove-Item -Recurse -Force "$env:APPDATA\vamscli"
    ```

3. Reinstall from source, then re-run setup and login:

    ```bash
    cd tools/VamsCLI && pip install .
    vamscli setup <your-api-gateway-url>
    vamscli auth login -u <username>
    ```

### Interrupted Operations

Commands can be interrupted safely with Ctrl+C. For file uploads, VamsCLI aborts the current upload sequence and cleans up temporary resources, so an interrupted upload can simply be retried — no manual cleanup is required.

---

## Configuration File Locations

VamsCLI stores its configuration under a platform-specific directory:

| Platform | Configuration Directory                  |
| -------- | ---------------------------------------- |
| Windows  | `%APPDATA%\vamscli\`                     |
| macOS    | `~/Library/Application Support/vamscli/` |
| Linux    | `~/.config/vamscli/`                     |

Each profile lives under `profiles/<profile-name>/` and contains:

-   `config.json` — Amazon API Gateway URL, CLI version, and Amplify configuration
-   `auth_profile.json` — authentication tokens and expiry
-   `credentials.json` — optionally saved credentials

The active profile is tracked in `active_profile.json` at the root of the configuration directory.

:::warning
The `auth_profile.json` and `credentials.json` files contain sensitive tokens and credentials. Protect the configuration directory with appropriate file permissions and exclude it from backups that are shared or stored insecurely.
:::

---

## Diagnostic Checklist

When a command fails, gather state systematically before escalating:

```bash
# 1. Capture environment and version information
vamscli --version
python --version

# 2. Confirm setup, profile, and authentication state
vamscli profile current
vamscli auth status

# 3. Re-run the failing command in verbose mode
vamscli --verbose <failing-command>

# 4. Confirm raw connectivity to the endpoint
curl -I https://your-api-gateway.com/api/version
```

For connectivity, proxy, SSL, or throttling failures surfaced by these steps, see [Network and Configuration Troubleshooting](network-config.md).

---

## Reporting a Bug

If a problem persists, search the project's [GitHub issues](https://github.com/awslabs/visual-asset-management-system/issues) first. When opening a new issue, include:

-   VamsCLI version (`vamscli --version`)
-   Python version (`python --version`)
-   Operating system and version
-   The exact command that failed
-   The complete error message
-   Verbose output (`vamscli --verbose <command>`)
-   Clear steps to reproduce

Enterprise users should also contact their VAMS administrator, who may maintain deployment-specific configuration and network requirements.

---

## Related Pages

-   [Command Reference](../command-reference.md)
-   [Automation and Scripting](../automation.md)
-   [Setup and Authentication Troubleshooting](./setup-auth.md)
