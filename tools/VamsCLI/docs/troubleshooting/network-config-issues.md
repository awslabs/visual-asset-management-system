# Network and Configuration Issues

This document covers network connectivity, SSL certificate, proxy configuration, and performance issues in VamsCLI.

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

### Connection Refused

**Error:**

```
✗ Connection Error: Connection refused to API Gateway
```

**Solutions:**

1. Verify the API Gateway URL is correct
2. Check if the service is running
3. Ensure you're not blocked by firewall rules
4. Try accessing the URL in a web browser

### DNS Resolution Issues

**Error:**

```
✗ Network Error: Failed to resolve hostname
```

**Solutions:**

1. Check your DNS settings
2. Try using a different DNS server (8.8.8.8, 1.1.1.1)
3. Verify the hostname is correct
4. Check if you're behind a corporate firewall

## Proxy Configuration Issues

### Corporate Proxy Setup

If you're behind a corporate proxy, configure VamsCLI to work with your proxy:

#### Environment Variables Method

```bash
# Set proxy environment variables
export HTTP_PROXY=http://proxy.company.com:8080
export HTTPS_PROXY=http://proxy.company.com:8080
export NO_PROXY=localhost,127.0.0.1

# For Windows PowerShell
$env:HTTP_PROXY = "http://proxy.company.com:8080"
$env:HTTPS_PROXY = "http://proxy.company.com:8080"
$env:NO_PROXY = "localhost,127.0.0.1"
```

#### Pip Proxy Configuration

```bash
# Configure pip to use proxy for VamsCLI installation
pip install --proxy http://proxy.company.com:8080 vamscli

# Or configure pip globally
pip config set global.proxy http://proxy.company.com:8080
```

#### Proxy Authentication

```bash
# If proxy requires authentication
export HTTP_PROXY=http://username:password@proxy.company.com:8080
export HTTPS_PROXY=http://username:password@proxy.company.com:8080
```

### Proxy-Related Errors

**Error:**

```
✗ Proxy Error: Proxy authentication required
```

**Solutions:**

1. Include credentials in proxy URL
2. Configure proxy authentication
3. Contact your network administrator for proxy settings
4. Try bypassing proxy for VAMS API if allowed

## SSL and Certificate Issues

### Self-Signed Certificate Issues

**Error:**

```
✗ SSL Error: Certificate verification failed (self-signed certificate)
```

**Solutions:**

1. Install the certificate in your system's certificate store
2. Contact your administrator for the correct certificate
3. Verify the API Gateway is using proper SSL certificates

### Certificate Chain Issues

**Error:**

```
✗ SSL Error: Certificate chain verification failed
```

**Solutions:**

1. Ensure intermediate certificates are properly configured
2. Update your system's certificate store
3. Contact your network administrator
4. Verify the API Gateway SSL configuration

### Certificate Expired

**Error:**

```
✗ SSL Error: Certificate has expired
```

**Solutions:**

1. Contact your administrator to renew the certificate
2. Verify your system clock is correct
3. Check if there's a newer API Gateway URL

## Performance Issues

### Slow API Responses

**Solutions:**

1. Check your network connection speed
2. Try during off-peak hours
3. Verify the VAMS deployment has adequate resources
4. Contact your administrator about API performance

### Slow File Uploads

**Solutions:**

1. Reduce parallel uploads: `--parallel-uploads 5`
2. Check your network bandwidth
3. Try uploading smaller batches of files
4. Use `--hide-progress` to reduce terminal overhead

### Memory Issues with Large Files

**Solutions:**

1. VamsCLI automatically chunks large files
2. Reduce parallel uploads for very large files
3. Ensure sufficient disk space for temporary files
4. Close other applications to free memory

### Upload Timeout Issues

**Solutions:**

1. Increase retry attempts: `--retry-attempts 5`
2. Reduce parallel uploads for unstable connections
3. Check network stability
4. Try uploading during off-peak hours

## Configuration Issues

### Configuration File Corruption

**Error:**

```
✗ Configuration Error: Failed to load configuration
```

**Solutions:**

1. Re-run setup: `vamscli setup <your-api-gateway-url> --force`
2. For specific profile: `vamscli setup <your-api-gateway-url> --profile <profile-name> --force`
3. Delete configuration directory and start over
4. Check file permissions in the profile directory

### Configuration File Permissions

**Error:**

```
✗ Permission Error: Cannot write to configuration directory
```

**Solutions:**

1. Check file permissions on the configuration directory
2. Run VamsCLI with appropriate user permissions
3. Ensure the configuration directory is writable
4. Contact your system administrator

### Configuration Directory Issues

**Error:**

```
✗ Configuration Error: Cannot create configuration directory
```

**Solutions:**

1. Check if the parent directory exists and is writable
2. Verify disk space is available
3. Check file system permissions
4. Try running with elevated permissions if necessary

## Network Diagnostics

### Basic Connectivity Tests

```bash
# Test DNS resolution
nslookup your-api-gateway-domain.com

# Test basic connectivity
ping your-api-gateway-domain.com

# Test HTTPS connectivity
curl -I https://your-api-gateway.com/api/version

# Test specific endpoint
curl https://your-api-gateway.com/api/amplify-config
```

### Advanced Network Diagnostics

```bash
# Test with verbose curl
curl -v https://your-api-gateway.com/api/version

# Test with specific proxy
curl --proxy http://proxy.company.com:8080 https://your-api-gateway.com/api/version

# Test SSL certificate
openssl s_client -connect your-api-gateway-domain.com:443 -servername your-api-gateway-domain.com
```

### VamsCLI Network Testing

```bash
# Test API connectivity with debug mode
vamscli --debug setup https://your-api-gateway.com --force

# Test authentication with debug
vamscli --debug auth status

# Test basic API call with debug
vamscli --debug database list
```

## Firewall and Security Issues

### Corporate Firewall

**Error:**

```
✗ Connection Error: Connection blocked by firewall
```

**Solutions:**

1. Contact your network administrator to allow access to:
    - Your VAMS API Gateway domain
    - AWS API Gateway service endpoints
    - Required ports (443 for HTTPS)
2. Request firewall rules for VAMS API access
3. Verify outbound HTTPS traffic is allowed

### Security Software Interference

**Error:**

```
✗ Connection Error: Connection blocked by security software
```

**Solutions:**

1. Add VamsCLI to security software whitelist
2. Configure antivirus to allow VamsCLI network access
3. Temporarily disable security software for testing
4. Contact your IT department for security software configuration

## Regional and Geographic Issues

### Regional API Access

**Error:**

```
✗ Regional Error: API not available in this region
```

**Solutions:**

1. Verify you're connecting to the correct regional API Gateway
2. Check if your VAMS deployment is in the expected region
3. Ensure your network allows access to the target AWS region
4. Contact your administrator about regional deployment

### Geographic Restrictions

**Error:**

```
✗ Access Error: Geographic access restrictions apply
```

**Solutions:**

1. Verify your location is allowed to access the VAMS deployment
2. Check if VPN or location-based restrictions apply
3. Contact your administrator about geographic access policies
4. Ensure compliance with organizational access policies

## Troubleshooting Workflows

### Network Connectivity Troubleshooting

```bash
# 1. Test basic connectivity
ping your-api-gateway-domain.com

# 2. Test HTTPS access
curl -I https://your-api-gateway.com/api/version

# 3. Test with VamsCLI debug mode
vamscli --debug setup https://your-api-gateway.com --force

# 4. Check proxy settings if behind corporate firewall
echo $HTTP_PROXY $HTTPS_PROXY

# 5. Test specific API endpoints
curl https://your-api-gateway.com/api/amplify-config
```

### SSL Certificate Troubleshooting

```bash
# 1. Check certificate details
openssl s_client -connect your-api-gateway-domain.com:443 -servername your-api-gateway-domain.com

# 2. Verify certificate chain
curl -v https://your-api-gateway.com/api/version

# 3. Check system certificate store
# Windows: certmgr.msc
# macOS: Keychain Access
# Linux: /etc/ssl/certs/

# 4. Test with VamsCLI
vamscli --debug setup https://your-api-gateway.com
```

### Proxy Configuration Troubleshooting

```bash
# 1. Check current proxy settings
echo $HTTP_PROXY $HTTPS_PROXY $NO_PROXY

# 2. Test proxy connectivity
curl --proxy http://proxy.company.com:8080 https://www.google.com

# 3. Test with VamsCLI through proxy
HTTP_PROXY=http://proxy.company.com:8080 vamscli --debug setup https://your-api-gateway.com

# 4. Verify proxy authentication
curl --proxy http://username:password@proxy.company.com:8080 https://your-api-gateway.com/api/version
```

## Performance Optimization

### Network Performance Optimization

```bash
# For slow connections, reduce parallel operations
vamscli file upload --parallel-uploads 3 <files>

# For fast connections, increase parallel operations
vamscli file upload --parallel-uploads 15 <files>

# Increase retry attempts for unreliable connections
vamscli file upload --retry-attempts 5 <files>

# Use compression if available
# (VamsCLI automatically handles compression where possible)
```

### Configuration Optimization

```bash
# Use JSON output for faster parsing in scripts
vamscli assets list --json-output

# Hide progress for automation
vamscli file upload --hide-progress <files>

# Use appropriate profiles for different environments
vamscli assets list --profile production
```

## Recovery Procedures

### Network Configuration Reset

```bash
# Clear proxy settings
unset HTTP_PROXY HTTPS_PROXY NO_PROXY

# Reset network configuration (system-specific)
# Windows: ipconfig /flushdns
# macOS: sudo dscacheutil -flushcache
# Linux: sudo systemctl restart systemd-resolved
```

### SSL Configuration Reset

```bash
# Update system certificates
# Windows: Windows Update
# macOS: Software Update
# Linux: sudo apt update && sudo apt upgrade ca-certificates

# Clear SSL cache (browser-specific)
# This may help if certificates were recently updated
```

## Frequently Asked Questions

### Q: Why do I get SSL certificate errors?

**A:** Usually due to corporate firewalls, outdated certificates, or proxy interference. Check with your network administrator.

### Q: How do I configure VamsCLI for corporate proxy?

**A:** Set HTTP_PROXY and HTTPS_PROXY environment variables with your proxy details.

### Q: Why are my uploads slow?

**A:** Check network bandwidth, reduce parallel uploads, and ensure stable connectivity.

### Q: How do I troubleshoot connection issues?

**A:** Use debug mode, test basic connectivity with ping/curl, and verify firewall settings.

### Q: Why do I get timeout errors?

**A:** Usually due to network issues, server load, or firewall restrictions. Try again later or contact your administrator.

### Q: How do I test if my network configuration is correct?

**A:** Use curl to test API endpoints directly, then try VamsCLI with debug mode.

### Q: What should I do if VamsCLI works from home but not from office?

**A:** This indicates corporate network restrictions. Contact your IT department about firewall and proxy configuration.

### Q: How do I optimize VamsCLI for my network?

**A:** Adjust parallel upload settings, use appropriate retry counts, and configure proxy settings if needed.
