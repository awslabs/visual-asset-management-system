# Audit Logging

VAMS provides a comprehensive audit logging system that captures security-sensitive operations across all API handlers. All audit events are written to dedicated Amazon CloudWatch Log Groups with long-term retention and optional AWS KMS encryption.

## Overview

The audit logging system focuses on authorization decisions, file operations, and system changes that occur after successful authentication. Sign-in itself is recorded by Amazon Cognito or your external identity provider, not by the VAMS audit system; the API Gateway custom Lambda authorizer records the outcome of each token verification as an authorization event.

:::info[Coverage Note]
Authorization decisions are recorded for every handler, because the entries are emitted from shared Casbin enforcement rather than from each handler. The remaining event types are emitted by the individual handlers, so a handler that does not call the matching function contributes no entry of that type.
:::

## Amazon CloudWatch Log Groups

VAMS creates nine dedicated log groups for different event types. Each log group name includes a unique hash derived from the stack name and account ID to prevent naming conflicts across deployments.

| Log Group                | Name Pattern                                           | Purpose                                                                                                                                |
| ------------------------ | ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| Authentication           | `/aws/vendedlogs/VAMSAuditAuthentication-{hash}`       | Token validation results and session creation determined by a VAMS handler                                                             |
| Authorization            | `/aws/vendedlogs/VAMSAuditAuthorization-{hash}`        | API-level permission checks, and data-level permission checks that fail (only failures are captured at the data tier, for performance) |
| Auth Other               | `/aws/vendedlogs/VAMSAuditAuthOther-{hash}`            | User profile reads and updates, and other authentication-adjacent operations                                                           |
| Auth Changes             | `/aws/vendedlogs/VAMSAuditAuthChanges-{hash}`          | Role, user-role, constraint, API key, and Amazon Cognito user changes                                                                  |
| File Upload              | `/aws/vendedlogs/VAMSAuditFileUpload-{hash}`           | File uploads to Amazon S3, upload validations                                                                                          |
| File Download            | `/aws/vendedlogs/VAMSAuditFileDownload-{hash}`         | Direct file downloads, bulk downloads, presigned URL generation                                                                        |
| File Download (Streamed) | `/aws/vendedlogs/VAMSAuditFileDownloadStreamed-{hash}` | Streaming downloads, large file transfers                                                                                              |
| Actions                  | `/aws/vendedlogs/VAMSAuditActions-{hash}`              | CRUD operations, workflow executions, pipeline runs                                                                                    |
| Errors                   | `/aws/vendedlogs/VAMSAuditErrors-{hash}`               | Application errors, validation failures, system exceptions                                                                             |

### Log Group Configuration

-   **Retention**: 10 years (3,653 days)
-   **Encryption**: AWS KMS encryption when `config.app.useKmsCmkEncryption.enabled` is `true`
-   **Removal Policy**: `DESTROY` (log groups are deleted with stack deletion)

## Log Format

All audit events follow a structured format with bracketed metadata fields followed by a JSON payload. Each entry ends with a `--- [event: {...}]` suffix carrying the masked echo of the triggering API event.

### Authentication Events

```text
[AUTHENTICATION][authenticated: True] [user: john.doe] [roles: ["admin"]] [mfaEnabled: True] {"method": "cognito", "ip": "192.168.1.1"}
```

### Authorization Events

```text
[AUTHORIZATION][authorized: False] [user: jane.smith] [roles: ["viewer"]] [mfaEnabled: True] {"resource": "database:db-123", "action": "DELETE", "reason": "insufficient permissions"}
```

### File Operation Events

Uploads and downloads use the `[FILEUPLOAD]`, `[FILEDOWNLOAD]`, and `[FILEDOWNLOAD-STREAMED]` types.

```text
[FILEUPLOAD] [user: john.doe] [roles: ["editor"]] [mfaEnabled: True] {"databaseId": "db-123", "assetId": "asset-456", "filePath": "/data/model.obj", "uploadDenied": false, "customData": {"fileSize": 1024000}}
```

### Error Events

```text
[ERRORS][type: validation_error] [user: john.doe] [roles: ["editor"]] [mfaEnabled: True] {"error": "Invalid asset ID format", "assetId": "invalid-id"}
```

The `[AUTHOTHER]`, `[AUTHCHANGES]`, and `[ACTIONS]` types follow the same shape as `[ERRORS]`, with the caller-supplied `secondary_type` in the `[type: ...]` field.

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

Audit log group names are resolved at runtime from AWS Systems Manager Parameter Store, the same mechanism that resolves Amazon DynamoDB table and Amazon S3 bucket names. CDK publishes each name as an SSM parameter under the deployment's resource-name prefix, and `globalLambdaEnvironmentsAndPermissions()` gives every Lambda function the `VAMS_RESOURCE_PARAM_PREFIX` environment variable plus read access to the parameters beneath it. The audit logging module calls `get_log_group_name(ResourceKeys.*)` from `backend/backend/common/resourceNames.py`, which fetches all resource names in one paginated `GetParametersByPath` request and caches them in the module for 60 minutes.

| Resource Key                      | SSM Parameter Key                               | Log Group                |
| --------------------------------- | ----------------------------------------------- | ------------------------ |
| `AUDIT_LOG_AUTHENTICATION`        | `cloudwatchLogGroups/auditAuthentication`       | Authentication events    |
| `AUDIT_LOG_AUTHORIZATION`         | `cloudwatchLogGroups/auditAuthorization`        | Authorization events     |
| `AUDIT_LOG_FILEUPLOAD`            | `cloudwatchLogGroups/auditFileUpload`           | File upload events       |
| `AUDIT_LOG_FILEDOWNLOAD`          | `cloudwatchLogGroups/auditFileDownload`         | File download events     |
| `AUDIT_LOG_FILEDOWNLOAD_STREAMED` | `cloudwatchLogGroups/auditFileDownloadStreamed` | Streamed download events |
| `AUDIT_LOG_AUTHOTHER`             | `cloudwatchLogGroups/auditAuthOther`            | Other auth events        |
| `AUDIT_LOG_AUTHCHANGES`           | `cloudwatchLogGroups/auditAuthChanges`          | Auth change events       |
| `AUDIT_LOG_ACTIONS`               | `cloudwatchLogGroups/auditActions`              | Action events            |
| `AUDIT_LOG_ERRORS`                | `cloudwatchLogGroups/auditErrors`               | Error events             |

The keys are defined in `ResourceKeys` in `backend/backend/common/resourceNames.py` and mirrored in `RESOURCE_PARAM_KEYS.cloudwatchLogGroups` in `infra/common/resourceParamKeys.ts`; the two lists must stay identical.

Each key also accepts an environment variable of the same name as an override. When the variable is set, its value is used directly and no SSM lookup occurs. This is how the test suite and local utilities point the audit functions at a log group of their choosing, and it serves as a break-glass path for a deployed function; the deployment itself sets no `AUDIT_LOG_*` variables.

The `setupSecurityAndLoggingEnvironmentAndPermissions()` CDK security helper grants each Lambda function `logs:CreateLogStream` and `logs:PutLogEvents` on all nine audit log groups, along with read access to the authorization tables.

## Available Logging Functions

The audit logging module is located at `backend/backend/customLogging/auditLogging.py`.

### log_authentication

Log authentication-related events such as token validation results.

```python
from customLogging.auditLogging import log_authentication

log_authentication(
    event=event,
    authenticated=True,
    custom_data={"method": "cognito", "ip": "192.168.1.1"}
)
```

### log_authorization and log_authorization_api

Log authorization decisions. Shared Casbin enforcement calls these on every API-level check and on data-level authorization failures (failures only, for performance), so a handler that enforces through `CasbinEnforcer` needs no call of its own. `log_authorization()` takes the claims dictionary returned by `request_to_claims()`; `log_authorization_api()` takes the full API Gateway event.

```python
from customLogging.auditLogging import log_authorization, log_authorization_api

log_authorization(
    claims_and_roles,
    authorized=False,
    custom_data={
        "action": "DELETE",
        "obj": {"object__type": "database", "databaseId": "db-123"}
    }
)

log_authorization_api(
    event,
    authorized=False,
    custom_data={
        "action": "DELETE",
        "obj": {"object__type": "api", "route__path": "/database/db-123"}
    }
)
```

### log_file_upload

Log file upload events including denied uploads.

```python
from customLogging.auditLogging import log_file_upload

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

### log_file_download, log_file_download_bulk, and log_file_download_streamed

Log file download events, distinguishing between direct downloads and streaming transfers. `log_file_download_bulk()` records one entry per file for a multi-file download and writes them to the file download log group in a single Amazon CloudWatch call.

```python
from customLogging.auditLogging import (
    log_file_download, log_file_download_bulk, log_file_download_streamed
)

log_file_download(
    event=event,
    database_id="db-123",
    asset_id="asset-456",
    file_path="/data/model.obj",
    custom_data={"downloadMethod": "direct"}
)

log_file_download_bulk(
    event=event,
    database_id="db-123",
    asset_id="asset-456",
    file_entries=[{"filePath": "/data/model.obj"}, {"filePath": "/data/texture.png"}],
    custom_data_base={"downloadType": "archive"}
)
```

### log_auth_other, log_auth_changes, log_actions, log_errors

Log additional event types with a `secondary_type` field for categorization.

```python
from customLogging.auditLogging import log_auth_changes, log_actions, log_errors

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
from customLogging.auditLogging import log_authorization_api, log_errors

def lambda_handler(event, context):
    try:
        authorized = check_permissions(event)
        log_authorization_api(event, authorized=authorized, custom_data={"resource": "asset:123"})

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

To modify audit log retention, update the `retention` property on the audit log groups in `storageBuilder-nestedStack.ts`.

:::note[Cost Consideration]
10-year retention for audit logs may incur significant Amazon CloudWatch storage costs. Review retention settings based on your organization's compliance requirements. Consider archiving logs to Amazon S3 for long-term storage beyond 10 years.
:::

## Querying Logs with Amazon CloudWatch Logs Insights

Amazon CloudWatch Logs Insights provides a powerful query language for analyzing audit logs.

### Find All Failed Authorization Attempts

```text
fields @timestamp, @message
| filter @message like /\[AUTHORIZATION\]\[authorized: False\]/
| sort @timestamp desc
| limit 100
```

### Track File Uploads by User

```text
fields @timestamp, @message
| filter @message like /\[FILEUPLOAD\]/
| parse @message /\[user: (?<user>[^\]]+)\]/
| stats count() by user
```

### Group Authorizer Denials by Reason

```text
fields @timestamp, @message
| filter @message like /\[AUTHORIZATION\]\[authorized: False\]/
| parse @message /"failureReason": "(?<reason>[^"]+)"/
| stats count() by reason
| sort count() desc
```

### Find Errors by Type

```text
fields @timestamp, @message
| filter @message like /\[ERRORS\]/
| parse @message /\[type: (?<errorType>[^\]]+)\]/
| stats count() by errorType
| sort count() desc
```

### Track Auth Changes (Role Assignments)

```text
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
-   Pipeline template bodies -- stored or supplied inline as an execute-time override -- their form definitions and input instructions, and the tag values substituted into them

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

Every audit entry carries an echo of the triggering API event, and the `mask_sensitive_data()` function filters that echo before it is written to Amazon CloudWatch. Matching is case-insensitive on the key name and applies at every nesting level, walking both objects and arrays. Two key families are replaced with `<redacted>`, and the distinction between them is why the second family exists at all: a credential key holds a value that is never safe to log, while a content key holds a document the caller authored, whose free-form text can contain a prompt, a file path, or a credential-shaped string that no key-name rule could find once it is inside the body.

**Credential keys** -- the value is never safe to log

-   `authorization` headers
-   `idJwtToken` fields
-   `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken` objects

**Content keys** -- caller-authored payloads. Every field that can deliver a template body belongs here, whichever request carries it:

-   `configBody` -- a pipeline template body, which carries free-form content such as generative-AI prompts and model configuration
-   `customTemplateOverride` -- the same kind of body supplied inline on an execute request instead of stored as a template
-   `webFormJson` -- the form definition stored alongside a template body
-   `inputInstructions` -- the authored guidance stored alongside a template body
-   `templateTags` and `tagValues` -- the caller-supplied values substituted into a template body

The field name is preserved when the value is redacted, so the audit trail still records that a template body, form definition, or tag value was submitted with the request. A request `body` is filtered whether it arrives as a JSON object or as a JSON string: the string is parsed, filtered, and re-serialized, and a string payload that names a redacted key but does not parse as JSON is dropped in full.

The two families are declared as `SENSITIVE_KEYS` and `CONTENT_KEYS` in `backend/backend/customLogging/logger.py`. A new request field that carries a caller-authored body is protected only when its key is added to `CONTENT_KEYS`.

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

```text
Failed to write audit log to CloudWatch log group
Failed to log [event_type] audit event
AUDIT_LOG_[TYPE] resource name not resolved
Failed loading resource name parameters from SSM
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

| Function                       | When to Use                                                                                                                                                                                                                                                                           |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `log_authentication()`         | Authentication results a VAMS handler determines itself -- token validation outcomes and session creation. The identity provider records sign-in itself, so most deployments emit no entries here.                                                                                    |
| `log_authorization()`          | Permission checks using claims and roles directly. Called inside Casbin enforcement logic where the `claims_and_roles` dictionary is available (not the full API Gateway event). Only log on data-level authorization failures for performance.                                       |
| `log_authorization_api()`      | API-level permission checks using the full API Gateway event. Called in handler entry points where `enforceAPI()` is evaluated.                                                                                                                                                       |
| `log_authorization_gateway()`  | Authorization events from the API Gateway custom Lambda authorizer. Uses enhanced security: never logs JWT tokens, uses generic failure categories only, and extracts user IDs only after successful JWT verification.                                                                |
| `log_file_upload()`            | File uploads to Amazon S3, multipart uploads, and upload validation results (including denied uploads with reasons).                                                                                                                                                                  |
| `log_file_download()`          | Direct file downloads and presigned URL generation.                                                                                                                                                                                                                                   |
| `log_file_download_bulk()`     | Multi-file downloads, where one entry per file is written to the file download log group in a single Amazon CloudWatch call.                                                                                                                                                          |
| `log_file_download_streamed()` | Streaming downloads and large file transfers via chunked protocols.                                                                                                                                                                                                                   |
| `log_auth_other()`             | Authentication-adjacent reads and writes that do not change the authorization model, such as a user profile fetch or update.                                                                                                                                                          |
| `log_auth_changes()`           | Role and user-role changes, permission constraint and constraint-template changes, API key lifecycle events, Amazon Cognito user management, and any other change to the authorization model.                                                                                         |
| `log_actions()`                | Orchestration write operations: launching, aborting, re-running, and permanently deleting an execution, and creating, updating, archiving, or deleting a workflow, pipeline, pipeline template, or workflow trigger. See [Orchestration action events](#orchestration-action-events). |
| `log_errors()`                 | Application errors, input validation failures, system exceptions, and any unhandled error conditions.                                                                                                                                                                                 |

:::note[Function signature differences]
`log_authorization()` accepts `claims_and_roles` as its first parameter (the claims dictionary from `request_to_claims()`), not the full API Gateway event. Use `log_authorization_api()` when you have the full event object, and `log_authorization_gateway()` in the API Gateway authorizer Lambda.
:::

### Orchestration action events

Every write on the pipeline, workflow, and execution surface records an `ACTIONS` entry. The
`secondary_type` names the operation, and the payload carries identifiers, counts, and flags:

| `secondary_type`                                                               | Recorded when                                              | Payload highlights                                                                      |
| ------------------------------------------------------------------------------ | ---------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `workflowExecute`                                                              | An execution is launched and its state machine has started | Workflow and execution ids, trigger type, input-file count, output target               |
| `workflowExecutionAbort`                                                       | A single execution is aborted                              | Execution and workflow ids                                                              |
| `workflowExecutionGroupAbort`                                                  | An execution group is aborted                              | Group id, how many this pass aborted, how many were withheld for access, more-remaining |
| `workflowExecutionRerun`                                                       | An execution is re-run                                     | The replayed execution id and the new execution id                                      |
| `workflowExecutionPermanentDelete`                                             | An execution's records are permanently deleted             | Execution and workflow ids                                                              |
| `workflowCreate` / `workflowUpdate` / `workflowArchive`                        | A workflow is created, updated, or archived                | Database and workflow ids, pipeline count                                               |
| `pipelineCreate` / `pipelineUpdate` / `pipelineArchive`                        | A pipeline is created, updated, or archived                | Database and pipeline ids, execution type, whether the execution config changed         |
| `pipelineTemplateCreate` / `pipelineTemplateUpdate` / `pipelineTemplateDelete` | A template is created, updated, or deleted                 | Database, pipeline, and template ids, configuration format, default flag                |
| `workflowTriggerSet` / `workflowTriggerDelete`                                 | A trigger is set or removed                                | Database and workflow ids, trigger type, enabled flag                                   |

Three properties hold across all of them:

-   **The entry follows the write.** It is emitted only after the operation succeeded, so a write that
    failed — or a request rejected as not found — never appears as a completed action.
-   **Configuration bodies and tag values are never recorded.** A rendered configuration body or a tag
    value can carry a model prompt or a credential-shaped string, so the entry records the template id,
    the format, and counts instead.
-   **A trigger-launched execution is attributed to `SYSTEM_USER`.** A user may upload a file without
    holding permission to run the workflow the upload fires, so the execution runs as the system
    identity by design; the `triggerType` field records that the run was automatic rather than manual.

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
-   Log group name resolution failure -- an unreachable AWS Systems Manager Parameter Store, a missing `cloudwatchLogGroups/*` parameter, or an unset `VAMS_RESOURCE_PARAM_PREFIX` (the function logs the failure and returns without writing)
-   Log group does not exist in Amazon CloudWatch
-   Log stream creation failure
-   Network timeouts when writing to Amazon CloudWatch
-   IAM permission errors on `logs:PutLogEvents`
-   Invalid or non-serializable data formats in the `custom_data` parameter

### Monitoring for Failures

While silent failure prevents API disruption, you should monitor for audit logging issues. Check Lambda Amazon CloudWatch logs for these error patterns:

```text
Failed to write audit log to CloudWatch log group
Failed to log [event_type] audit event
AUDIT_LOG_[TYPE] resource name not resolved
Failed loading resource name parameters from SSM
CloudWatch Logs client not initialized
```

## API Gateway Authorizer Security

The API Gateway custom Lambda authorizer (`apiGatewayAuthorizerRest.py`) uses a dedicated logging function, `log_authorization_gateway()`, that implements additional security measures beyond the standard audit logging functions.

### Security Controls

1. **No token logging** -- JWT tokens are never logged, even in failure cases. The authorizer processes tokens in memory but strips them from all audit output.
2. **Generic failure categories** -- Failure reasons use safe, non-descriptive categories that do not expose token structure or validation details:
    - "IP address not authorized"
    - "Token missing or invalid format"
    - "Token verification failed"
    - "API key is disabled", "API key has expired", and the other API key denial categories
3. **User context after verification only** -- User IDs are only extracted and logged after successful JWT verification. For failed authorization attempts, the user field is set to `"unknown"`.
4. **Source IP only** -- The authorizer logs only the requesting IP address, not request headers or other potentially sensitive fields.

### Why a Separate Function

The API Gateway authorizer runs before normal request processing and handles raw JWT tokens. A separate `log_authorization_gateway()` function ensures that:

-   The full API Gateway event (which contains the `Authorization` header) is passed to the standard `mask_sensitive_data()` filter before any Amazon CloudWatch write.
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

1. Verify the Lambda function has `VAMS_RESOURCE_PARAM_PREFIX` set by checking the function configuration in the AWS Management Console.
2. Confirm the nine `cloudwatchLogGroups/*` parameters exist beneath that prefix in AWS Systems Manager Parameter Store and that the execution role can read them (`ssm:GetParametersByPath`).
3. Confirm IAM permissions for `logs:CreateLogStream` and `logs:PutLogEvents` are present on the Lambda execution role.
4. Check the Lambda function's standard Amazon CloudWatch log stream for audit logging error messages.
5. Ensure the audit log group exists in the correct AWS Region.

### Performance Impact

-   Audit logging is synchronous but lightweight, with minimal latency impact (under 50 milliseconds per log entry).
-   The silent failure pattern prevents any cascading impact on API response times.
-   Amazon CloudWatch Logs uses batched writes internally, optimizing throughput for high-volume scenarios.

## Next Steps

-   [Backend Development](backend.md) -- Handler patterns that integrate with audit logging
-   [CDK Infrastructure](cdk.md) -- How audit log groups are provisioned and secured
-   [Local Development Setup](setup.md) -- Testing audit logging locally
