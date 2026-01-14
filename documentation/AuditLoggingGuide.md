# VAMS Audit Logging System Guide

## Overview

The VAMS Audit Logging System provides comprehensive audit trail capabilities for security-sensitive operations. All audit events are logged to dedicated CloudWatch Log Groups with 10-year retention and optional KMS encryption.

**Important Note on Authentication Logging:**
Authentication events (user login, JWT token validation) are currently logged by AWS Cognito or your External Identity Provider (IDP), not by the VAMS audit logging system. These logs can be found in:

-   **Cognito**: CloudWatch Logs for your User Pool
-   **External IDP**: Your IDP's audit logging system

VAMS audit logging focuses on authorization (permission checks), file operations, and system changes that occur after successful authentication.

**Important Note on Action Logging:**
Action events (like creating a database, asset, etc.) are not yet fully implemented throughout the handlers. These events will show up in authorization however to see who is fetching/creating/updating/deleting data elements.

**Important Note on Error Logging:**
Error event logging is implemented in API handlers that are using the new patterns as of v2.2. Some older handlers that have not yet been refactored will not log errors yet. As these handlers become refactored they will be updated.

## Architecture

### Infrastructure Components

#### CloudWatch Log Groups

Nine dedicated log groups are created for different event types:

1. **Authentication** - `/aws/vendedlogs/VAMSAuditAuthentication-{uniqueHash}`
2. **Authorization** - `/aws/vendedlogs/VAMSAuditAuthorization-{uniqueHash}`
3. **File Upload** - `/aws/vendedlogs/VAMSAuditFileUpload-{uniqueHash}`
4. **File Download** - `/aws/vendedlogs/VAMSAuditFileDownload-{uniqueHash}`
5. **File Download Streamed** - `/aws/vendedlogs/VAMSAuditFileDownloadStreamed-{uniqueHash}`
6. **Auth Other** - `/aws/vendedlogs/VAMSAuditAuthOther-{uniqueHash}`
7. **Auth Changes** - `/aws/vendedlogs/VAMSAuditAuthChanges-{uniqueHash}`
8. **Actions** - `/aws/vendedlogs/VAMSAuditActions-{uniqueHash}`
9. **Errors** - `/aws/vendedlogs/VAMSAuditErrors-{uniqueHash}`

**Note:** The `{uniqueHash}` is a 10-character hash generated from the stack name and account ID to ensure uniqueness across deployments.

**Configuration:**

-   Retention: 10 years (3653 days)
-   Encryption: KMS encryption when enabled in config
-   Removal Policy: DESTROY (for development environments)

#### Lambda Environment Variables

All Lambda functions automatically receive these environment variables via `setupSecurityAndLoggingEnvironmentAndPermissions()`:

```
AUDIT_LOG_AUTHENTICATION
AUDIT_LOG_AUTHORIZATION
AUDIT_LOG_FILEUPLOAD
AUDIT_LOG_FILEDOWNLOAD
AUDIT_LOG_FILEDOWNLOAD_STREAMED
AUDIT_LOG_AUTHOTHER
AUDIT_LOG_AUTHCHANGES
AUDIT_LOG_ACTIONS
AUDIT_LOG_ERRORS
```

#### IAM Permissions

Lambda functions are granted the following CloudWatch Logs permissions:

-   `logs:CreateLogStream`
-   `logs:PutLogEvents`

## Python Audit Logging Module

### Location

`backend/backend/customLogging/auditLogging.py`

### Key Features

1. **Silent Failure**: All logging functions implement silent failure - if logging fails, the error is logged locally but the Lambda execution continues without disruption
2. **Automatic User Context Extraction**: Uses `request_to_claims()` to extract user, roles, and MFA status
3. **Sensitive Data Masking**: Uses `mask_sensitive_data()` to filter authorization tokens and credentials
4. **Structured Logging**: Consistent format across all event types

### Available Functions

#### 1. log_authentication()

```python
from backend.customLogging.auditLogging import log_authentication

log_authentication(
    event=event,
    authenticated=True,
    custom_data={"method": "cognito", "ip": "192.168.1.1"}
)
```

**Log Format:**

```
[AUTHENTICATION][authenticated: true][user: john.doe][roles: ["admin"]][mfaEnabled: true] {"method": "cognito", "ip": "192.168.1.1"}
```

#### 2. log_authorization()

Note: Should be on all API checks and only log on data check auth failures (for performance reasons)

```python
from backend.customLogging.auditLogging import log_authorization

log_authorization(
    event=event,
    authorized=False,
    custom_data={"resource": "database:db-123", "action": "DELETE", "reason": "insufficient permissions"}
)
```

**Log Format:**

```
[AUTHORIZATION][authorized: false][user: jane.smith][roles: ["viewer"]][mfaEnabled: true] {"resource": "database:db-123", "action": "DELETE", "reason": "insufficient permissions"}
```

#### 3. log_file_upload()

```python
from backend.customLogging.auditLogging import log_file_upload

log_file_upload(
    event=event,
    database_id="db-123",
    asset_id="asset-456",
    file_path="/data/model.obj",
    upload_denied=False,
    upload_denied_reason=None,
    custom_data={"fileSize": 1024000, "contentType": "model/obj"}
)
```

**Log Format:**

```
[FILEUPLOAD][user: john.doe][roles: ["editor"]][mfaEnabled: true] {"databaseId": "db-123", "assetId": "asset-456", "filePath": "/data/model.obj", "uploadDenied": false, "customData": {"fileSize": 1024000, "contentType": "model/obj"}}
```

#### 4. log_file_download()

```python
from backend.customLogging.auditLogging import log_file_download

log_file_download(
    event=event,
    database_id="db-123",
    asset_id="asset-456",
    file_path="/data/model.obj",
    custom_data={"downloadMethod": "direct"}
)
```

#### 5. log_file_download_streamed()

```python
from backend.customLogging.auditLogging import log_file_download_streamed

log_file_download_streamed(
    event=event,
    database_id="db-123",
    asset_id="asset-456",
    file_path="/data/large-file.bin",
    custom_data={"streamingProtocol": "chunked"}
)
```

#### 6. log_auth_other()

```python
from backend.customLogging.auditLogging import log_auth_other

log_auth_other(
    event=event,
    secondary_type="token_refresh",
    custom_data={"tokenType": "access", "expiresIn": 3600}
)
```

**Log Format:**

```
[AUTHOTHER][type: token_refresh][user: john.doe][roles: ["admin"]][mfaEnabled: true] {"tokenType": "access", "expiresIn": 3600}
```

#### 7. log_auth_changes()

```python
from backend.customLogging.auditLogging import log_auth_changes

log_auth_changes(
    event=event,
    secondary_type="role_assignment",
    custom_data={"targetUser": "jane.smith", "role": "editor", "action": "added"}
)
```

#### 8. log_actions()

```python
from backend.customLogging.auditLogging import log_actions

log_actions(
    event=event,
    secondary_type="database_created",
    custom_data={"databaseId": "db-789", "databaseName": "Production Assets"}
)
```

#### 9. log_errors()

```python
from backend.customLogging.auditLogging import log_errors

log_errors(
    event=event,
    secondary_type="validation_error",
    custom_data={"error": "Invalid asset ID format", "assetId": "invalid-id"}
)
```

## Implementation Guidelines

### When to Use Each Function

-   **log_authentication()**: User login attempts, token validation, session creation
-   **log_authorization()**: Permission checks, access control decisions, policy evaluations
-   **log_file_upload()**: File uploads to S3, multipart uploads, upload validations
-   **log_file_download()**: Direct file downloads, presigned URL generation
-   **log_file_download_streamed()**: Streaming downloads, large file transfers
-   **log_auth_other()**: Token refresh, session management, MFA challenges
-   **log_auth_changes()**: Role assignments, permission updates, user modifications
-   **log_actions()**: CRUD operations, workflow executions, pipeline runs
-   **log_errors()**: Application errors, validation failures, system exceptions

### Best Practices

1. **Call Early**: Log audit events as early as possible in the handler
2. **Include Context**: Provide meaningful custom_data to aid in troubleshooting
3. **Don't Rely on Success**: Audit logging uses silent failure - don't check return values
4. **Mask Sensitive Data**: The system automatically masks tokens and credentials
5. **Be Specific**: Use descriptive secondary_type values for categorization

### Example Handler Integration

```python
from backend.customLogging.auditLogging import log_authentication, log_authorization, log_errors

def lambda_handler(event, context):
    try:
        # Log authentication
        log_authentication(event, authenticated=True, custom_data={"method": "jwt"})

        # Perform authorization check
        authorized = check_permissions(event)
        log_authorization(event, authorized=authorized, custom_data={"resource": "asset:123"})

        if not authorized:
            return authorization_error()

        # Perform business logic
        result = process_request(event)

        return success(body=result)

    except Exception as e:
        # Log error
        log_errors(event, secondary_type="handler_exception", custom_data={"error": str(e)})
        return internal_error(event=event)
```

## Error Handling

### Silent Failure Design

All audit logging functions implement silent failure:

1. **Try-Except Wrapper**: Each function is wrapped in try-except
2. **Local Logging**: Failures are logged to the Lambda's CloudWatch log stream
3. **No Re-raise**: Exceptions are caught but not re-raised
4. **Graceful Degradation**: Application continues even if audit logging fails

### Error Scenarios Handled

-   CloudWatch Logs client initialization failure
-   Missing environment variables
-   Log group doesn't exist
-   Log stream creation failure
-   Network timeouts
-   Permission errors
-   Invalid data formats

### Monitoring Audit Logging Health

Check Lambda CloudWatch logs for these error patterns:

```
Failed to write audit log to CloudWatch log group
Failed to log [event_type] audit event
AUDIT_LOG_[TYPE] environment variable not set
CloudWatch Logs client not initialized
```

## Security Considerations

### Data Protection

#### What is NEVER Logged (Protected Data)

The audit logging system is designed with security-first principles and **NEVER** logs:

-   L **JWT Tokens** - Raw or decoded tokens
-   L **Authorization Headers** - Bearer tokens or credentials
-   L **Passwords** - User passwords or secrets
-   L **API Keys** - Third-party API keys or access tokens
-   L **AWS Credentials** - Access keys, secret keys, session tokens
-   L **Token Signatures** - Cryptographic signatures
-   L **Detailed Token Errors** - Validation errors that could expose token structure

#### What is Logged (Safe Data)

The system only logs non-sensitive operational data:

-    **User IDs** - Only from verified JWT claims after successful authentication
-    **Authorization Results** - Success/failure status (boolean)
-    **Generic Failure Reasons** - Safe categories like "Token verification failed"
-    **Resource Identifiers** - Database IDs, Asset IDs, File paths
-    **Operation Types** - Actions performed (GET, POST, DELETE, etc.)
-    **Source IP Addresses** - Requesting IP (for API Gateway authorizer only)
-    **Timestamps** - When operations occurred
-    **MFA Status** - Whether MFA was enabled

#### Automatic Data Masking

The `mask_sensitive_data()` function automatically filters:

-   `authorization` headers
-   `idJwtToken` fields
-   `Credentials` objects
-   `AccessKeyId` values
-   `SecretAccessKey` values
-   `SessionToken` values

All audit log entries pass through this filter before being written to CloudWatch.

#### API Gateway Authorizer Security

The API Gateway authorizer (`apiGatewayAuthorizerHttp.py`) implements special security measures:

1. **No Token Logging**: JWT tokens are never logged, even in failure cases
2. **Generic Failure Categories**: Uses safe failure reasons:
    - "IP address not authorized"
    - "Token missing or invalid format"
    - "Token verification failed"
3. **User Context After Verification**: User IDs are only extracted and logged AFTER successful JWT verification
4. **Source IP Only**: Only logs the requesting IP address, not request headers

### Encryption and Access Control

1. **KMS Encryption**: Log groups use KMS encryption when `config.app.useKmsCmkEncryption.enabled` is true
2. **IAM Permissions**: Only Lambda execution roles can write to log groups
3. **Read Access**: Controlled via CloudWatch Logs IAM policies
4. **Retention**: 10-year retention ensures compliance with audit requirements
5. **Immutability**: CloudWatch logs cannot be modified after creation
6. **Unique Naming**: Hash-based naming prevents conflicts across deployments

### Compliance Considerations

#### Data Residency

-   Logs are stored in the same AWS region as your VAMS deployment
-   KMS keys are region-specific
-   No cross-region log replication by default

#### Audit Trail Integrity

-   CloudWatch Logs are immutable once written
-   Log stream names use date-based format (YYYY/MM/DD)
-   Each log entry includes timestamp for chronological ordering
-   Silent failure ensures no gaps in audit trail due to logging errors

#### Privacy and GDPR

-   User IDs are logged for audit purposes (legitimate interest)
-   No PII beyond user IDs is logged
-   Logs can be exported for data subject access requests
-   10-year retention aligns with compliance requirements

### Security Best Practices

1. **Review Logs Regularly**: Monitor for suspicious patterns
2. **Set Up Alarms**: Create CloudWatch alarms for critical events
3. **Restrict Access**: Limit who can read audit logs
4. **Enable Encryption**: Always use KMS encryption in production
5. **Export for Long-term**: Archive logs to S3 for retention beyond 10 years
6. **Monitor Failures**: Watch for audit logging failures in Lambda logs

## Querying Audit Logs

### CloudWatch Insights Queries

**Find all failed authorization attempts:**

```
fields @timestamp, @message
| filter @message like /\[AUTHORIZATION\]\[authorized: false\]/
| sort @timestamp desc
```

**Track file uploads by user:**

```
fields @timestamp, @message
| filter @message like /\[FILEUPLOAD\]/
| parse @message /\[user: (?<user>[^\]]+)\]/
| stats count() by user
```

**Monitor authentication failures:**

```
fields @timestamp, @message
| filter @message like /\[AUTHENTICATION\]\[authenticated: false\]/
| sort @timestamp desc
| limit 100
```

## Maintenance

### Log Group Management

-   **Retention**: Automatically managed by CloudWatch (10 years)
-   **Encryption**: Managed by KMS key rotation
-   **Cleanup**: Log groups are destroyed with stack deletion (RemovalPolicy.DESTROY)
-   **Naming**: Uses unique hash to prevent conflicts across multiple deployments

### Cost Optimization

-   Use CloudWatch Insights for querying instead of exporting logs
-   Consider archiving to S3 for long-term storage beyond 10 years
-   Monitor ingestion rates and adjust retention if needed
-   10-year retention may incur higher costs - review based on compliance requirements

## Future Enhancements

The audit logging system is designed for easy extension:

1. **Add New Event Types**: Create new log groups and functions
2. **Custom Formatters**: Modify `_format_log_message()` for different formats
3. **External Integration**: Add SNS notifications for critical events
4. **Real-time Alerting**: Create CloudWatch alarms on specific patterns
5. **Compliance Reports**: Build automated compliance reporting from logs

## Troubleshooting

### Logs Not Appearing

1. Check Lambda has correct environment variables
2. Verify IAM permissions for CloudWatch Logs
3. Check Lambda CloudWatch logs for audit logging errors
4. Ensure log group exists in correct region

### Performance Impact

-   Audit logging is asynchronous and non-blocking
-   Silent failure prevents application disruption
-   Minimal latency impact (<50ms per log entry)

## Related Documentation

-   [AWS CloudWatch Logs Documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/)
-   [Lambda Powertools Logger](https://docs.powertools.aws.dev/lambda/python/latest/core/logger/)
-   [VAMS Security Guide](./PermissionsGuide.md)
