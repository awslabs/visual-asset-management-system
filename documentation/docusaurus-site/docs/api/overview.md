# API Overview

This section describes the VAMS REST API, which provides programmatic access to manage databases, assets, files, metadata, and search operations within the Visual Asset Management System.

---

## Base URL

All API endpoints are served through an Amazon API Gateway V2 HTTP API. The base URL is determined by your deployment and follows this format:

```
https://{api-id}.execute-api.{region}.amazonaws.com
```

When deployed behind CloudFront or an Application Load Balancer (ALB), the base URL corresponds to your distribution or ALB domain.

:::info[Discovering the Base URL]
After deployment, retrieve the API endpoint from the CDK stack outputs or from the `/api/amplify-config` endpoint, which returns the full API URL.
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

All API responses follow the API Gateway V2 proxy response format:

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

| Status Code | Description                                                                                |
| ----------- | ------------------------------------------------------------------------------------------ |
| `200`       | The request succeeded.                                                                     |
| `400`       | Bad request. The request contains invalid parameters or fails validation.                  |
| `401`       | Unauthorized. The asset is not distributable (download-specific).                          |
| `403`       | Forbidden. The authenticated user does not have permission for the requested action.       |
| `404`       | Not found. The requested resource does not exist.                                          |
| `500`       | Internal server error. An unexpected error occurred on the server.                         |
| `503`       | Service unavailable. The requested feature is not enabled (e.g., Cognito user management). |

---

## Pagination

Many list endpoints support pagination using a token-based pattern. The following query parameters control pagination:

| Parameter       | Type    | Default | Description                                                             |
| --------------- | ------- | ------- | ----------------------------------------------------------------------- |
| `maxItems`      | integer | `100`   | Maximum number of items to return in a single response.                 |
| `pageSize`      | integer | `100`   | Number of items per page (equivalent to `maxItems` for most endpoints). |
| `startingToken` | string  | --      | Base64-encoded continuation token from a previous response.             |

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

These values are configurable at deployment time through the `app.api.globalRateLimit` and `app.api.globalBurstLimit` configuration settings.

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
-   **Asset streaming** (`GET /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}`) -- Streams file content through the API Gateway with byte-range support.

:::warning[Presigned URL Expiration]
Presigned URLs have a configurable timeout controlled by the `PRESIGNED_URL_TIMEOUT_SECONDS` environment variable. Plan to use generated URLs promptly after receiving them.
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

| Category           | Description                                                 | Documentation                       |
| ------------------ | ----------------------------------------------------------- | ----------------------------------- |
| **Authentication** | Auth configuration, route authorization, user management    | [Authentication](authentication.md) |
| **Assets**         | Asset CRUD, archive/unarchive, download                     | [Assets](assets.md)                 |
| **Files**          | File listing, operations, upload, streaming                 | [Files](files.md)                   |
| **Metadata**       | Metadata CRUD for assets, files, databases, and asset links | [Metadata](metadata.md)             |
| **Search**         | Full-text and structured search across assets and files     | [Search](search.md)                 |

Additional endpoint groups not covered in detail here include databases, pipelines, workflows, tags, tag types, roles, user roles, comments, subscriptions, and asset links.
