---
sidebar_label: Network and Configuration
title: Network and Configuration Troubleshooting
---

# Network and Configuration Troubleshooting

This page covers connectivity, proxy, SSL/certificate, performance, and configuration problems encountered when running VamsCLI against a Visual Asset Management System (VAMS) deployment.

---

## Connectivity Issues

### SSL Certificate Verification Failed

**Symptoms:**

-   `API Error: SSL certificate verification failed`
-   Requests fail before any response is received from Amazon API Gateway

**Cause:**

The local certificate store cannot validate the Amazon API Gateway certificate chain. This is most common behind a corporate firewall or TLS-inspecting proxy that substitutes its own certificate.

**Resolution:**

1. Update your operating system's trusted certificate store so it includes current root and intermediate authorities.
2. If a TLS-inspecting proxy is in use, install your organization's signing certificate in the system store.
3. Confirm the API Gateway endpoint presents a valid certificate, and consult your network administrator if interception is suspected.

### Request Timeouts

**Symptoms:**

-   `API Unavailable: VAMS API is not responding`
-   Commands hang and then fail after the default 30-second request timeout

**Cause:**

The VAMS API Gateway endpoint is unreachable, the deployment is temporarily unavailable, or network latency exceeds the request timeout.

**Resolution:**

1. Verify general internet connectivity and that the endpoint resolves and responds.
2. Confirm the VAMS deployment is running and that you are targeting the correct Amazon API Gateway URL.
3. Retry after a short wait; transient latency or a brief outage often resolves on its own.

### Rate Limiting and Throttling

**Symptoms:**

-   `Rate Limit Exceeded: ... All retry attempts exhausted`
-   Bulk operations fail intermittently with HTTP 429 responses

**Cause:**

Amazon API Gateway throttles requests when the configured rate or burst limit is exceeded. VamsCLI automatically retries HTTP 429 responses with exponential backoff and jitter, honoring any server-provided `Retry-After` header, but the retry budget can still be exhausted under sustained throttling.

**Resolution:**

1. Wait a few minutes and run the command again.
2. Reduce parallelism if multiple VamsCLI instances run concurrently.
3. Increase the retry budget for sustained bulk work:

    ```bash
    export VAMS_CLI_MAX_RETRY_ATTEMPTS=10
    export VAMS_CLI_INITIAL_RETRY_DELAY=2.0
    export VAMS_CLI_MAX_RETRY_DELAY=180.0
    ```

4. If throttling persists, ask your VAMS administrator about the deployment's API rate and burst limits.

:::tip
Retry behavior is fully configurable through environment variables. See [General Troubleshooting and Debugging](./general.md) for the complete list of retry variables and their bounds.
:::

### Connection Refused

**Symptoms:**

-   `Connection Error: Connection refused to API Gateway`

**Cause:**

Nothing is listening at the target address, the URL is incorrect, or a firewall is rejecting the connection.

**Resolution:**

1. Verify the Amazon API Gateway URL configured for the active profile.
2. Confirm the VAMS service is running and reachable.
3. Open the URL in a web browser to confirm it responds, and check for blocking firewall rules.

### DNS Resolution Failures

**Symptoms:**

-   `Network Error: Failed to resolve hostname`

**Cause:**

The endpoint hostname cannot be resolved, typically due to incorrect DNS settings, an incorrect hostname, or a corporate network that restricts name resolution.

**Resolution:**

1. Verify the hostname in your configured API Gateway URL is correct.
2. Check your DNS configuration, and test an alternate resolver if appropriate.
3. Confirm name resolution is permitted on your network.

---

## Proxy Configuration

VamsCLI honors standard proxy environment variables. Configure them before running commands when you are behind a corporate proxy.

```bash
export HTTP_PROXY=http://proxy.company.com:8080
export HTTPS_PROXY=http://proxy.company.com:8080
export NO_PROXY=localhost,127.0.0.1
```

On Windows PowerShell:

```powershell
$env:HTTP_PROXY = "http://proxy.company.com:8080"
$env:HTTPS_PROXY = "http://proxy.company.com:8080"
$env:NO_PROXY = "localhost,127.0.0.1"
```

If the proxy is also required to install VamsCLI, pass it to pip:

```bash
pip install --proxy http://proxy.company.com:8080 .
```

### Proxy Authentication Required

**Symptoms:**

-   `Proxy Error: Proxy authentication required`

**Cause:**

The proxy requires credentials that have not been supplied.

**Resolution:**

1. Include credentials in the proxy URL:

    ```bash
    export HTTPS_PROXY=http://username:password@proxy.company.com:8080
    ```

2. Confirm the proxy host, port, and credentials with your network administrator.
3. Where policy permits, add the VAMS API Gateway domain to `NO_PROXY` to bypass the proxy for VAMS traffic.

---

## SSL and Certificate Issues

### Self-Signed or Untrusted Certificate

**Symptoms:**

-   `SSL Error: Certificate verification failed (self-signed certificate)`

**Cause:**

The endpoint presents a certificate that is not anchored to a trusted authority in the local store.

**Resolution:**

1. Install the certificate provided by your administrator in the system certificate store.
2. Confirm the Amazon API Gateway endpoint is configured with a valid, publicly trusted certificate.

### Certificate Chain Incomplete

**Symptoms:**

-   `SSL Error: Certificate chain verification failed`

**Cause:**

One or more intermediate certificates are missing from the presented chain or absent from the local trust store.

**Resolution:**

1. Ensure intermediate certificates are correctly configured on the endpoint.
2. Update the local certificate store, and verify the chain end to end with your network administrator.

### Certificate Expired

**Symptoms:**

-   `SSL Error: Certificate has expired`

**Cause:**

The endpoint certificate is past its validity period, or the local system clock is incorrect.

**Resolution:**

1. Confirm the system clock and time zone are accurate.
2. Ask your administrator to renew the endpoint certificate, and verify you are using the current API Gateway URL.

---

## Performance Issues

### Slow API Responses

**Symptoms:**

-   Commands complete successfully but take noticeably longer than expected

**Cause:**

Constrained network bandwidth, peak-load conditions, or limited capacity in the VAMS deployment.

**Resolution:**

1. Check local connection speed and try again during off-peak hours.
2. Confirm the VAMS deployment is adequately provisioned with your administrator.

### Slow or Timing-Out File Uploads

**Symptoms:**

-   Uploads progress slowly or fail intermittently on unstable connections

**Cause:**

Bandwidth limits, connection instability, or terminal rendering overhead from progress display.

**Resolution:**

1. Reduce concurrency for slow or unstable links:

    ```bash
    vamscli file upload --parallel-uploads 3 -d my-db -a my-asset ./files/
    ```

2. Increase retry attempts for unreliable connections:

    ```bash
    vamscli file upload --retry-attempts 5 -d my-db -a my-asset ./files/
    ```

3. Upload smaller batches, and use `--hide-progress` to reduce terminal overhead in scripts.

### Memory Pressure with Large Files

**Symptoms:**

-   High memory usage during large-file uploads

**Cause:**

Very large files combined with high upload concurrency.

**Resolution:**

VamsCLI automatically chunks large files. Reduce `--parallel-uploads` for very large files, ensure sufficient free disk space for temporary chunks, and close other memory-intensive applications.

---

## Configuration Issues

### Configuration Failed to Load

**Symptoms:**

-   `Configuration Error: Failed to load configuration`

**Cause:**

The profile's configuration file is missing or corrupted.

**Resolution:**

Re-run setup to rewrite the configuration. Add `--profile <name>` to target a non-default profile:

```bash
vamscli setup <your-api-gateway-url> --force
```

If the problem persists, remove the configuration directory and start over. See [General Troubleshooting and Debugging](./general.md) for configuration file locations.

### Cannot Write to or Create the Configuration Directory

**Symptoms:**

-   `Permission Error: Cannot write to configuration directory`
-   `Configuration Error: Cannot create configuration directory`

**Cause:**

The configuration directory is not writable, the parent directory does not exist, or free disk space is exhausted.

**Resolution:**

1. Verify the configuration directory and its parent are writable by your user.
2. Confirm sufficient free disk space.
3. Run with appropriate permissions, or contact your system administrator.

---

## Network Diagnostics

Test connectivity outside VamsCLI to isolate network problems from CLI configuration. Replace the example hostnames with your deployment's values.

```bash
# Resolve the hostname
nslookup your-api-gateway-domain.com

# Confirm HTTPS reachability and inspect headers
curl -I https://your-api-gateway.com/api/version

# Inspect the presented certificate chain
openssl s_client -connect your-api-gateway-domain.com:443 -servername your-api-gateway-domain.com
```

Then exercise the same path through VamsCLI in verbose mode to capture request and response detail:

```bash
vamscli --verbose setup https://your-api-gateway.com --force
vamscli --verbose auth status
vamscli --verbose database list
```

To test through a proxy:

```bash
HTTP_PROXY=http://proxy.company.com:8080 vamscli --verbose setup https://your-api-gateway.com
```

---

## Firewall and Security Software

**Symptoms:**

-   `Connection Error: Connection blocked by firewall`
-   `Connection Error: Connection blocked by security software`
-   Commands succeed from one network (for example, home) but fail from another (for example, an office)

**Cause:**

A corporate firewall, endpoint protection agent, or antivirus is blocking outbound HTTPS traffic to the VAMS endpoint.

**Resolution:**

1. Ask your network administrator to allow outbound HTTPS (port 443) to the VAMS API Gateway domain and to Amazon API Gateway service endpoints.
2. Add VamsCLI to the allowlist of any endpoint-protection or antivirus software.
3. When behavior differs by network, treat it as a corporate network restriction and engage your IT team about firewall and proxy policy.

---

## Regional and Geographic Restrictions

**Symptoms:**

-   `Regional Error: API not available in this region`
-   `Access Error: Geographic access restrictions apply`

**Cause:**

The targeted Amazon API Gateway endpoint is in a different AWS Region than expected, or organizational location-based access policies restrict the connection.

**Resolution:**

1. Confirm you are connecting to the correct regional API Gateway URL and that the VAMS deployment is in the expected AWS Region.
2. Verify your location and any VPN requirements comply with your organization's access policies, and confirm regional deployment details with your administrator.

---

## Related Pages

-   [Setup and Authentication Troubleshooting](./setup-auth.md)
-   [General Troubleshooting and Debugging](./general.md)
