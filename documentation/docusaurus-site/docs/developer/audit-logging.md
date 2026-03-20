# Audit Logging

VAMS provides a comprehensive audit logging system that captures security-sensitive operations across all API handlers. All audit events are written to dedicated Amazon CloudWatch Log Groups with long-term retention and optional AWS KMS encryption.

## Overview

The audit logging system focuses on authorization decisions, file operations, and system changes that occur after successful authentication. Authentication events (user login, JWT token validation) are logged by Amazon Cognito or your external identity provider, not by the VAMS audit system.

:::info[Coverage Note]
Audit logging is implemented in API handlers that use the refactored patterns introduced in VAMS v2.2. Some older handlers that have not yet been refactored may not emit audit events. As these handlers are updated, audit coverage will expand.
:::

## Amazon CloudWatch Log Groups

VAMS creates nine dedicated log groups for different event types. Each log group name includes a unique hash derived from the stack name and account ID to prevent naming conflicts across deployments.

| Log Group                         | Name Pattern                                           | Purpose                                                                                                   |
| --------------------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| Authentication                    | `/aws/vendedlogs/VAMSAuditAuthentication-{hash}`       | Login attempts, token validation, session creation                                                        |
| Authorization (API)               | `/aws/vendedlogs/VAMSAuditAuthorization-{hash}`        | API-level and data-level permission checks                                                                |
| Authorization (Data-Unauthorized) | Subset of Authorization log                            | Data-level authorization failures (logged for performance -- only failures are captured at the data tier) |
| Auth Other                        | `/aws/vendedlogs/VAMSAuditAuthOther-{hash}`            | Token refresh, session management, MFA challenges                                                         |
| Auth Changes                      | `/aws/vendedlogs/VAMSAuditAuthChanges-{hash}`          | Role assignments, permission updates, user modifications                                                  |
| File Upload                       | `/aws/vendedlogs/VAMSAuditFileUpload-{hash}`           | File uploads to Amazon S3, upload validations                                                             |
| File Download                     | `/aws/vendedlogs/VAMSAuditFileDownload-{hash}`         | Direct file downloads, presigned URL generation                                                           |
| File Download (Streamed)          | `/aws/vendedlogs/VAMSAuditFileDownloadStreamed-{hash}` | Streaming downloads, large file transfers                                                                 |
| Actions                           | `/aws/vendedlogs/VAMSAuditActions-{hash}`              | CRUD operations, workflow executions, pipeline runs                                                       |
| Errors                            | `/aws/vendedlogs/VAMSAuditErrors-{hash}`               | Application errors, validation failures, system exceptions                                                |

### Log Group Configuration

-   **Retention**: 10 years (3,653 days)
-   **Encryption**: AWS KMS encryption when `config.app.useKmsCmkEncryption.enabled` is `true`
-   **Removal Policy**: `DESTROY` (log groups are deleted with stack deletion)

## Log Format

All audit events follow a structured format with bracketed metadata fields followed by a JSON payload.

### Authentication Events

```
[AUTHENTICATION][authenticated: true][user: john.doe][roles: ["admin"]][mfaEnabled: true] {"method": "cognito", "ip": "192.168.1.1"}
```

### Authorization Events

```
[AUTHORIZATION][authorized: false][user: jane.smith][roles: ["viewer"]][mfaEnabled: true] {"resource": "database:db-123", "action": "DELETE", "reason": "insufficient permissions"}
```

### File Operation Events

```
[FILEUPLOAD][user: john.doe][roles: ["editor"]][mfaEnabled: true] {"databaseId": "db-123", "assetId": "asset-456", "filePath": "/data/model.obj", "uploadDenied": false, "customData": {"fileSize": 1024000}}
```

### Error Events

```
[ERRORS][type: validation_error][user: john.doe][roles: ["editor"]][mfaEnabled: true] {"error": "Invalid asset ID format", "assetId": "invalid-id"}
```

## How Audit Logging Works

### Silent Failure Pattern

All audit logging functions implement a silent failure design. If writing an audit event fails, the error is logged to the Lambda function's standard Amazon CloudWatch log stream, but Lambda execution continues without disruption. This ensures that audit logging failures never cause API requests to fail.

```python
# Simplified illustration of the silent failure pattern
def log_authorization(event, authorized, custom_data=None):
    try:
        # Extract user context from JWT claims
        # Format structured log message
        # Write to CloudWatch log group
        pass
    except Exception as e:
        # Log failure locally but do not re-raise
        print(f"Failed to write audit log: {e}")
```

### Infrastructure Integration

Every Lambda function automatically receives audit log group names as environment variables via the `setupSecurityAndLoggingEnvironmentAndPermissions()` CDK security helper:

| Environment Variable              | Log Group                |
| --------------------------------- | ------------------------ |
| `AUDIT_LOG_AUTHENTICATION`        | Authentication events    |
| `AUDIT_LOG_AUTHORIZATION`         | Authorization events     |
| `AUDIT_LOG_FILEUPLOAD`            | File upload events       |
| `AUDIT_LOG_FILEDOWNLOAD`          | File download events     |
| `AUDIT_LOG_FILEDOWNLOAD_STREAMED` | Streamed download events |
| `AUDIT_LOG_AUTHOTHER`             | Other auth events        |
| `AUDIT_LOG_AUTHCHANGES`           | Auth change events       |
| `AUDIT_LOG_ACTIONS`               | Action events            |
| `AUDIT_LOG_ERRORS`                | Error events             |

Lambda functions are granted `logs:CreateLogStream` and `logs:PutLogEvents` permissions on all audit log groups.

## Available Logging Functions

The audit logging module is located at `backend/backend/customLogging/auditLogging.py`.

### log_authentication

Log authentication-related events such as token validation results.

```python
from backend.customLogging.auditLogging import log_authentication

log_authentication(
    event=event,
    authenticated=True,
    custom_data={"method": "cognito", "ip": "192.168.1.1"}
)
```

### log_authorization

Log authorization decisions. Should be called on all API-level checks and on data-level authorization failures (failures only, for performance).

```python
from backend.customLogging.auditLogging import log_authorization

log_authorization(
    event=event,
    authorized=False,
    custom_data={
        "resource": "database:db-123",
        "action": "DELETE",
        "reason": "insufficient permissions"
    }
)
```

### log_file_upload

Log file upload events including denied uploads.

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

### log_file_download and log_file_download_streamed

Log file download events, distinguishing between direct downloads and streaming transfers.

```python
from backend.customLogging.auditLogging import log_file_download, log_file_download_streamed

log_file_download(
    event=event,
    database_id="db-123",
    asset_id="asset-456",
    file_path="/data/model.obj",
    custom_data={"downloadMethod": "direct"}
)
```

### log_auth_other, log_auth_changes, log_actions, log_errors

Log additional event types with a `secondary_type` field for categorization.

```python
from backend.customLogging.auditLogging import log_auth_changes, log_actions, log_errors

log_auth_changes(
    event=event,
    secondary_type="role_assignment",
    custom_data={"targetUser": "jane.smith", "role": "editor", "action": "added"}
)

log_actions(
    event=event,
    secondary_type="database_created",
    custom_data={"databaseId": "db-789", "databaseName": "Production Assets"}
)

log_errors(
    event=event,
    secondary_type="validation_error",
    custom_data={"error": "Invalid asset ID format", "assetId": "invalid-id"}
)
```

## Handler Integration Example

The following example shows the standard pattern for integrating audit logging into a Lambda handler:

```python
from backend.customLogging.auditLogging import (
    log_authentication, log_authorization, log_errors
)

def lambda_handler(event, context):
    try:
        log_authentication(event, authenticated=True, custom_data={"method": "jwt"})

        authorized = check_permissions(event)
        log_authorization(event, authorized=authorized, custom_data={"resource": "asset:123"})

        if not authorized:
            return authorization_error()

        result = process_request(event)
        return success(body=result)

    except Exception as e:
        log_errors(event, secondary_type="handler_exception", custom_data={"error": str(e)})
        return internal_error(event=event)
```

## Configuring Log Retention

Log retention is controlled at two levels:

1. **Audit Log Groups** -- 10-year retention (3,653 days), configured in the storage nested stack
2. **All Other Log Groups** -- 1-year retention, enforced by the `LogRetentionAspect` CDK aspect that applies to all `CfnLogGroup` resources in the stack

To modify audit log retention, update the `retentionDays` parameter in `storageBuilder-nestedStack.ts` where the audit log groups are created.

:::note[Cost Consideration]
10-year retention for audit logs may incur significant Amazon CloudWatch storage costs. Review retention settings based on your organization's compliance requirements. Consider archiving logs to Amazon S3 for long-term storage beyond 10 years.
:::

## Querying Logs with Amazon CloudWatch Logs Insights

Amazon CloudWatch Logs Insights provides a powerful query language for analyzing audit logs.

### Find All Failed Authorization Attempts

```
fields @timestamp, @message
| filter @message like /\[AUTHORIZATION\]\[authorized: false\]/
| sort @timestamp desc
| limit 100
```

### Track File Uploads by User

```
fields @timestamp, @message
| filter @message like /\[FILEUPLOAD\]/
| parse @message /\[user: (?<user>[^\]]+)\]/
| stats count() by user
```

### Monitor Authentication Failures

```
fields @timestamp, @message
| filter @message like /\[AUTHENTICATION\]\[authenticated: false\]/
| sort @timestamp desc
| limit 100
```

### Find Errors by Type

```
fields @timestamp, @message
| filter @message like /\[ERRORS\]/
| parse @message /\[type: (?<errorType>[^\]]+)\]/
| stats count() by errorType
| sort count() desc
```

### Track Auth Changes (Role Assignments)

```
fields @timestamp, @message
| filter @message like /\[AUTHCHANGES\]/
| parse @message /\[type: (?<changeType>[^\]]+)\]/
| sort @timestamp desc
| limit 50
```

## Data Protection

### What Is Never Logged

The audit logging system is designed with security-first principles and never logs:

-   JWT tokens (raw or decoded)
-   Authorization headers or bearer tokens
-   Passwords or secrets
-   API keys or access tokens
-   AWS credentials (access keys, secret keys, session tokens)
-   Token signatures or detailed token validation errors

### What Is Logged

The system logs only non-sensitive operational data:

-   User IDs (from verified JWT claims only)
-   Authorization results (boolean success/failure)
-   Generic failure reasons (safe categories)
-   Resource identifiers (database IDs, asset IDs, file paths)
-   Operation types (GET, POST, DELETE)
-   Source IP addresses (API Gateway authorizer only)
-   Timestamps
-   MFA status

### Automatic Data Masking

The `mask_sensitive_data()` function filters all audit log entries before writing to Amazon CloudWatch. It removes:

-   `authorization` headers
-   `idJwtToken` fields
-   `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken` objects

## Integration with SIEM Systems

VAMS audit logs can be forwarded to Security Information and Event Management (SIEM) systems using standard AWS patterns:

1. **Amazon CloudWatch Logs Subscription Filters** -- Stream log events to Amazon Kinesis Data Firehose, AWS Lambda, or Amazon OpenSearch Service
2. **Amazon S3 Export** -- Export log data to Amazon S3 for ingestion by external SIEM tools
3. **Amazon EventBridge** -- Create rules to forward specific log patterns to third-party integrations

### Key Considerations

-   Logs are stored in the same AWS Region as the VAMS deployment
-   AWS KMS keys are Region-specific
-   No cross-Region log replication is configured by default
-   Amazon CloudWatch logs are immutable once written
-   Log stream names use date-based format (YYYY/MM/DD)

## Monitoring Audit Logging Health

Check Lambda Amazon CloudWatch logs for these error patterns to verify audit logging is functioning correctly:

```
Failed to write audit log to CloudWatch log group
Failed to log [event_type] audit event
AUDIT_LOG_[TYPE] environment variable not set
CloudWatch Logs client not initialized
```

:::tip[Set Up Alarms]
Create Amazon CloudWatch alarms on the patterns above to detect audit logging failures. While the silent failure pattern prevents API disruption, you should monitor for gaps in the audit trail.
:::

## Security Best Practices

1. **Enable AWS KMS encryption** for audit log groups in production deployments
2. **Restrict read access** to audit logs using IAM policies
3. **Review logs regularly** for suspicious patterns (repeated authorization failures, unusual file downloads)
4. **Set up Amazon CloudWatch alarms** for critical events
5. **Archive to Amazon S3** for retention beyond 10 years
6. **Monitor audit logging health** to detect failures in the logging system itself

## When to Use Each Function

The following table provides guidance on which logging function to call in different operational contexts:

| Function                       | When to Use                                                                                                                                                                                                                                     |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `log_authentication()`         | User login attempts, token validation results, session creation. Called by the custom Lambda authorizer and authentication-related handlers.                                                                                                    |
| `log_authorization()`          | Permission checks using claims and roles directly. Called inside Casbin enforcement logic where the `claims_and_roles` dictionary is available (not the full API Gateway event). Only log on data-level authorization failures for performance. |
| `log_authorization_api()`      | API-level permission checks using the full API Gateway event. Called in handler entry points where `enforceAPI()` is evaluated.                                                                                                                 |
| `log_authorization_gateway()`  | Authorization events from the API Gateway custom Lambda authorizer. Uses enhanced security: never logs JWT tokens, uses generic failure categories only, and extracts user IDs only after successful JWT verification.                          |
| `log_file_upload()`            | File uploads to Amazon S3, multipart uploads, and upload validation results (including denied uploads with reasons).                                                                                                                            |
| `log_file_download()`          | Direct file downloads and presigned URL generation.                                                                                                                                                                                             |
| `log_file_download_streamed()` | Streaming downloads and large file transfers via chunked protocols.                                                                                                                                                                             |
| `log_auth_other()`             | Token refresh events, session management operations, and MFA challenge events.                                                                                                                                                                  |
| `log_auth_changes()`           | Role assignments, permission constraint updates, user-role modifications, and any change to the authorization model.                                                                                                                            |
| `log_actions()`                | CRUD operations on databases, assets, files, and metadata. Also covers workflow executions and pipeline runs.                                                                                                                                   |
| `log_errors()`                 | Application errors, input validation failures, system exceptions, and any unhandled error conditions.                                                                                                                                           |

:::note[Function signature differences]
`log_authorization()` accepts `claims_and_roles` as its first parameter (the claims dictionary from `request_to_claims()`), not the full API Gateway event. Use `log_authorization_api()` when you have the full event object, and `log_authorization_gateway()` in the API Gateway authorizer Lambda.
:::

## Error Handling Details

### Silent Failure Design

All audit logging functions implement a consistent silent failure pattern with the following behavior:

1. **Try-except wrapper** -- Each logging function is wrapped in a top-level `try-except` block that catches all exceptions.
2. **Local logging** -- When audit logging fails, the error is logged to the Lambda function's standard Amazon CloudWatch log stream using the `safeLogger` module.
3. **No re-raise** -- Exceptions are caught but never re-raised, ensuring the calling handler continues execution.
4. **Graceful degradation** -- The application continues processing the API request even if the audit trail has a gap.

### Error Scenarios Handled

The following failure scenarios are handled silently without disrupting Lambda execution:

-   Amazon CloudWatch Logs client initialization failure (for example, missing IAM permissions at cold start)
-   Missing `AUDIT_LOG_*` environment variables (function logs an error and returns immediately)
-   Log group does not exist in Amazon CloudWatch
-   Log stream creation failure
-   Network timeouts when writing to Amazon CloudWatch
-   IAM permission errors on `logs:PutLogEvents`
-   Invalid or non-serializable data formats in the `custom_data` parameter

### Monitoring for Failures

While silent failure prevents API disruption, you should monitor for audit logging issues. Check Lambda Amazon CloudWatch logs for these error patterns:

```
Failed to write audit log to CloudWatch log group
Failed to log [event_type] audit event
AUDIT_LOG_[TYPE] environment variable not set
CloudWatch Logs client not initialized
```

## API Gateway Authorizer Security

The API Gateway custom Lambda authorizer (`apiGatewayAuthorizerHttp.py`) uses a dedicated logging function, `log_authorization_gateway()`, that implements additional security measures beyond the standard audit logging functions.

### Security Controls

1. **No token logging** -- JWT tokens are never logged, even in failure cases. The authorizer processes tokens in memory but strips them from all audit output.
2. **Generic failure categories** -- Failure reasons use safe, non-descriptive categories that do not expose token structure or validation details:
    - "IP address not authorized"
    - "Token missing or invalid format"
    - "Token verification failed"
3. **User context after verification only** -- User IDs are only extracted and logged after successful JWT verification. For failed authorization attempts, the user field is set to `"unknown"`.
4. **Source IP only** -- The authorizer logs only the requesting IP address, not request headers or other potentially sensitive fields.

### Why a Separate Function

The API Gateway authorizer runs before normal request processing and handles raw JWT tokens. A separate `log_authorization_gateway()` function ensures that:

-   The full API Gateway event (which contains the `Authorization` header) is passed to the standard `mask_sensitive_data()` filter before any CloudWatch write.
-   Only the `context` field (populated after successful JWT verification) is used for user identity extraction.
-   MFA status is read from verified claims only, never from unverified token contents.

## Compliance Considerations

### Data Residency

-   Audit logs are stored in the same AWS Region as your VAMS deployment.
-   AWS KMS keys used for log encryption are Region-specific.
-   No cross-Region log replication is configured by default.

### Audit Trail Integrity

-   Amazon CloudWatch logs are immutable once written and cannot be modified after creation.
-   Log stream names use a date-based format (`YYYY/MM/DD`) for chronological organization.
-   Each log entry includes a millisecond-precision timestamp for chronological ordering.
-   The silent failure design ensures no API disruption due to logging errors, though gaps in the audit trail should be monitored.

### Privacy and Data Protection

-   User IDs are logged for audit purposes (legitimate interest for security monitoring).
-   No personally identifiable information (PII) beyond user IDs is logged.
-   Logs can be exported for data subject access requests.
-   The 10-year retention period aligns with common compliance requirements (for example, FedRAMP, SOC 2).
-   Automatic data masking removes all authorization tokens, credentials, and secrets before writing.

## Maintenance and Troubleshooting

### Log Group Management

| Aspect         | Details                                                                                                                    |
| -------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Retention**  | Automatically managed by Amazon CloudWatch (10 years / 3,653 days).                                                        |
| **Encryption** | Managed by the VAMS AWS KMS key when `useKmsCmkEncryption.enabled` is `true`. Key rotation is automatic.                   |
| **Cleanup**    | Log groups are destroyed with stack deletion (`RemovalPolicy.DESTROY`).                                                    |
| **Naming**     | Uses a unique 10-character hash derived from the stack name and account ID to prevent naming conflicts across deployments. |

### Cost Optimization

-   Use Amazon CloudWatch Logs Insights for ad-hoc querying instead of exporting all logs.
-   Consider archiving to Amazon S3 for long-term storage beyond 10 years at lower cost.
-   Monitor ingestion rates and adjust retention if the 10-year period exceeds your compliance requirements.
-   For high-volume deployments, set up Amazon CloudWatch Logs subscription filters to stream only critical events to downstream systems.

### Logs Not Appearing

If audit events are not appearing in the expected log groups:

1. Verify the Lambda function has the correct `AUDIT_LOG_*` environment variables set by checking the function configuration in the AWS Management Console.
2. Confirm IAM permissions for `logs:CreateLogStream` and `logs:PutLogEvents` are present on the Lambda execution role.
3. Check the Lambda function's standard Amazon CloudWatch log stream for audit logging error messages.
4. Ensure the audit log group exists in the correct AWS Region.

### Performance Impact

-   Audit logging is synchronous but lightweight, with minimal latency impact (under 50 milliseconds per log entry).
-   The silent failure pattern prevents any cascading impact on API response times.
-   Amazon CloudWatch Logs uses batched writes internally, optimizing throughput for high-volume scenarios.

## Next Steps

-   [Backend Development](backend.md) -- Handler patterns that integrate with audit logging
-   [CDK Infrastructure](cdk.md) -- How audit log groups are provisioned and secured
-   [Local Development Setup](setup.md) -- Testing audit logging locally
