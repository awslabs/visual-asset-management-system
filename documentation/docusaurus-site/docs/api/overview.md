# API Overview

This section describes the VAMS REST API, which provides programmatic access to manage databases, assets, files, metadata, and search operations within the Visual Asset Management System.

---

## Base URL

All API endpoints are served through an Amazon API Gateway REST API deployed to a stage named `api`. The base URL includes that stage:

```
https://{api-id}.execute-api.{region}.amazonaws.com/api
```

This is the value of the `APIGatewayEndpointOutput` stack output. The paths documented on the following pages are appended to it, so `GET /database` is `https://{api-id}.execute-api.{region}.amazonaws.com/api/database`.

When deployed behind CloudFront, the distribution forwards `/api/*` on the web domain to that stage, so the same request is `https://{web-domain}/api/database`. An Application Load Balancer (ALB) deployment answers `/api/*` on the web domain with a redirect to the execute-api URL above instead.

The two unauthenticated routes carry `/api` in their own path, so on the execute-api host the stage segment appears twice:

| Route                     | On the execute-api host                                                      | On the web domain                         |
| ------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------- |
| `GET /api/version`        | `https://{api-id}.execute-api.{region}.amazonaws.com/api/api/version`        | `https://{web-domain}/api/version`        |
| `GET /api/amplify-config` | `https://{api-id}.execute-api.{region}.amazonaws.com/api/api/amplify-config` | `https://{web-domain}/api/amplify-config` |

:::info[Discovering the Base URL]
After deployment, read `APIGatewayEndpointOutput` from the CDK stack outputs, or request `https://{web-domain}/api/amplify-config` from the web application's domain (`WebAppCloudFrontDistributionDomainName` or `webDistributionUrl` in the outputs): its `api` field is the base URL.
:::

---

## Authentication

All API endpoints require authentication unless explicitly noted. VAMS supports three authentication methods:

| Method             | Header                            | Description                                           |
| ------------------ | --------------------------------- | ----------------------------------------------------- |
| Cognito JWT        | `Authorization: Bearer {idToken}` | ID token from Amazon Cognito user pool authentication |
| External OAuth JWT | `Authorization: Bearer {idToken}` | ID token from an external OAuth identity provider     |
| API Key            | `Authorization: {apiKey}`         | VAMS-issued API key for programmatic access           |

:::note[Unauthenticated Endpoints]
The following endpoints do not require authentication:

-   `GET /api/amplify-config` -- Returns client-side authentication configuration
-   `GET /api/version` -- Returns the current VAMS version
    :::

For detailed authentication information, see the [Authentication](authentication.md) page.

---

## Content Type

All request and response bodies use JSON format:

```
Content-Type: application/json
```

---

## Common Response Format

All API responses follow the API Gateway proxy response format:

```json
{
    "statusCode": 200,
    "headers": {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache, no-store"
    },
    "body": "{...}"
}
```

The `body` field contains a JSON-encoded string. When successful, the body contains the response data. When an error occurs, the body contains an error message.

### Success Response

```json
{
    "message": "Success",
    "data": { ... }
}
```

### Error Response

```json
{
    "message": "Description of the error"
}
```

---

## Error Codes

VAMS uses standard HTTP status codes to indicate the result of an API request.

| Status Code | Description                                                                                                                                                                                                     |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200`       | The request succeeded.                                                                                                                                                                                          |
| `400`       | Bad request. The request contains invalid parameters, fails validation, or targets a feature that is disabled (for example, Cognito user management when Cognito is not enabled).                               |
| `401`       | Unauthorized. No VAMS handler returns this code; API Gateway returns it when a request carries no credential.                                                                                                   |
| `403`       | Forbidden. The authenticated user does not have permission for the requested action, or the deployment's AWS WAF web ACL blocked the request (see [Requests Blocked by AWS WAF](#requests-blocked-by-aws-waf)). |
| `404`       | Not found. The requested resource does not exist, or a feature-gated resource is unavailable (for example, search when the OpenSearch feature is not enabled).                                                  |
| `500`       | Internal server error. An unexpected error occurred on the server.                                                                                                                                              |

### Requests Blocked by AWS WAF

When the deployment enables AWS WAF (`app.useWaf`), a request that a web ACL rule blocks at the API Gateway stage never reaches VAMS. API Gateway answers it with HTTP `403` and this body, which names AWS WAF as the source and carries API Gateway's extended request ID:

```json
{
    "message": "Request blocked by the deployment's AWS WAF web ACL",
    "blockedBy": "waf",
    "requestId": "E7BBKGcCvHcEXAMPLE="
}
```

A `403` from an authorization denial carries no `blockedBy` field. Signing in again does not change a blocked request's result: a managed rule matched something in the request, most often a value in its body. An administrator looks the request ID up in the web ACL's `aws-waf-logs-vams-*` log group, where the request's record carries it as `httpRequest.requestId` and names the rule that matched; the response carries the same ID in its `x-amz-apigw-id` header. The web ACL's sampled requests carry no request ID; a request is matched there by its time, URI and client IP. A rate-based rule answers `429` with a `Retry-After` header instead (see [AWS WAF and Rate Limiting](../architecture/security.md#aws-waf-and-rate-limiting)).

---

## Pagination

Many list endpoints support pagination using a token-based pattern. The following query parameters control pagination:

| Parameter       | Type    | Description                                                             |
| --------------- | ------- | ----------------------------------------------------------------------- |
| `maxItems`      | integer | Maximum number of items to return in a single response.                 |
| `pageSize`      | integer | Number of items per page (equivalent to `maxItems` for most endpoints). |
| `startingToken` | string  | Base64-encoded continuation token from a previous response.             |

Default and maximum values for `maxItems` and `pageSize` vary by listing:

| Listing                                      | `maxItems` default | `pageSize` default            | Maximum                                                                                                        |
| -------------------------------------------- | ------------------ | ----------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Assets (all, or within a database)           | 30,000             | 3,000                         | `maxItems` 30,000; `pageSize` 10,000                                                                           |
| Asset files                                  | 10,000             | 100 (1,500 with `basic=true`) | `maxItems` 30,000; `pageSize` 10,000                                                                           |
| Asset versions                               | 1,000              | 1,000                         | 1,000 for each                                                                                                 |
| Databases                                    | 10,000             | 10,000                        | `maxItems` 30,000; `pageSize` 10,000                                                                           |
| Bucket configurations                        | 3,000              | 3,000                         | `maxItems` 30,000; `pageSize` 10,000                                                                           |
| Metadata (asset, file, database, asset link) | 1,000              | 100                           | 1,000 for each                                                                                                 |
| Metadata schemas                             | 30,000             | 3,000                         | `maxItems` 30,000; `pageSize` 10,000                                                                           |
| Constraints                                  | 30,000             | 3,000                         | `maxItems` 30,000; `pageSize` 10,000                                                                           |
| Roles, tags, tag types, and user roles       | 30,000             | 3,000                         | `maxItems` 30,000; `pageSize` 10,000; roles, tags and tag types reduce a larger value rather than rejecting it |
| Pipelines and workflows                      | 100                | 100                           | 500 for each; a larger value is reduced to 500 rather than rejected                                            |
| Comments                                     | 10,000             | 10,000                        | `maxItems` 30,000; `pageSize` 10,000; a larger value is reduced, not rejected                                  |
| Subscriptions                                | 10,000             | 10,000                        | None                                                                                                           |

Unless the table says otherwise, a listing with a maximum rejects a larger value with `400` rather than reducing it to the maximum, so a caller asking for more than one response can hold learns that from the answer instead of reading a shortened page as the complete set. A listing with no maximum accepts the value as given. Amazon Cognito user listings are capped at 60 items per page. Rely on the `NextToken` in each response rather than assuming a fixed page size — the whole set is reachable by following it whatever the page size.

### Paginated Response

When more results are available, the response includes a `NextToken` field:

```json
{
    "Items": [ ... ],
    "NextToken": "eyJkYXRhYmFzZUlkIjoibXktZGIiLCAiYXNzZXRJZCI6Im15LWFzc2V0In0="
}
```

To retrieve the next page, pass the `NextToken` value as the `startingToken` query parameter in the subsequent request.

:::tip[Pagination Best Practice]
Always check for the presence of `NextToken` in the response. If it is absent, you have retrieved all available results.
:::

---

## Rate Limiting

The API Gateway enforces rate limits to protect the system from excessive traffic.

| Setting            | Default            | Description                                      |
| ------------------ | ------------------ | ------------------------------------------------ |
| `globalRateLimit`  | 50 requests/second | Steady-state request rate across all clients.    |
| `globalBurstLimit` | 100 requests       | Maximum burst capacity for short traffic spikes. |

These values are configurable at deployment time through the `app.api.apiGatewayRest.globalRateLimit` and `app.api.apiGatewayRest.globalBurstLimit` configuration settings.

When rate limits are exceeded, the API returns an HTTP `429 Too Many Requests` response.

---

## CORS Configuration

The API Gateway is configured with permissive CORS settings to support browser-based clients:

| Setting         | Value                                                 |
| --------------- | ----------------------------------------------------- |
| Allowed Origins | `*` (all origins)                                     |
| Allowed Methods | `GET`, `POST`, `PUT`, `DELETE`, `HEAD`, `OPTIONS`     |
| Allowed Headers | `Authorization`, `Content-Type`, and standard headers |
| Credentials     | Not included (`false`)                                |

---

## Presigned URLs

Several operations return presigned S3 URLs for direct file access. These include:

-   **Asset downloads** (`POST /database/{databaseId}/assets/{assetId}/download`) -- Returns a time-limited presigned URL for downloading a file.
-   **File uploads** (`POST /uploads`) -- Returns presigned URLs for uploading files directly to S3.
-   **Asset streaming** (`GET /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}`) -- Answers with a `307` redirect to a short-lived presigned URL; the client fetches the bytes directly from Amazon S3, with byte-range support. Do not forward the `Authorization` header to the redirect target.

:::warning[Presigned URL Expiration]
Presigned URLs remain valid for at most the deployment's `app.authProvider.presignedUrlTimeoutSeconds` (24 hours by default), and stop working earlier if the Lambda role credentials that signed them expire first. Plan to use generated URLs promptly after receiving them.
:::

---

## API Versioning

The current VAMS API does not use explicit version prefixes in the URL path. All endpoints are accessed at their base paths (e.g., `/database/{databaseId}/assets`).

Version information can be retrieved from the `GET /api/version` endpoint:

```json
{
    "version": "<current-version>"
}
```

---

## API Endpoint Categories

The VAMS API is organized into the following functional groups:

| Category           | Description                                                                                       | Documentation                       |
| ------------------ | ------------------------------------------------------------------------------------------------- | ----------------------------------- |
| **Authentication** | Authentication providers, runtime configuration, web route authorization, login profiles          | [Authentication](authentication.md) |
| **Databases**      | Database CRUD and the bucket configurations a database is created against                         | [Databases](databases.md)           |
| **Assets**         | Asset CRUD, archive/unarchive, download, export, history, single-call ingest                      | [Assets](assets.md)                 |
| **Asset Versions** | Asset version creation, retrieval, revert, archive/unarchive                                      | [Asset Versions](asset-versions.md) |
| **Files**          | File listing, operations, upload, streaming, preview management                                   | [Files](files.md)                   |
| **Metadata**       | Metadata CRUD for assets, files, databases, and asset links, plus metadata schemas                | [Metadata](metadata.md)             |
| **Search**         | Full-text and structured search across assets and files                                           | [Search](search.md)                 |
| **Pipelines**      | Pipeline CRUD, configuration templates, and template tag schemas                                  | [Pipelines](pipelines.md)           |
| **Workflows**      | Workflow CRUD, triggers, and execution launch, listing, detail, logs, re-run, and abort           | [Workflows](workflows.md)           |
| **Asset Links**    | Relationships between assets, with optional tags and tree views                                   | [Asset Links](asset-links.md)       |
| **Comments**       | Review comments attached to an asset version                                                      | [Comments](comments.md)             |
| **Tags**           | Tags and tag types, global or scoped to a database                                                | [Tags](tags.md)                     |
| **Subscriptions**  | Event subscriptions and notification opt-in                                                       | [Subscriptions](subscriptions.md)   |
| **Authorization**  | Permission constraints, API route listings, roles, user-role assignments, Cognito users, API keys | [Authorization](auth.md)            |
| **Add-ons**        | Endpoints contributed by optional add-ons                                                         | [Add-ons](addon.md)                 |
