# Authentication

This page documents the authentication and authorization endpoints in the VAMS API. These endpoints handle authentication configuration, route authorization, user login profiles, and user management.

For general authentication concepts, see the [API Overview](overview.md).

---

## Authentication Methods

VAMS supports three authentication methods:

### Cognito JWT

When Cognito is enabled (`app.authProvider.useCognito.enabled`), users authenticate through Amazon Cognito and receive JWT tokens. The ID token is sent in the `Authorization` header:

```
Authorization: Bearer eyJraWQiOiJ...
```

**Cognito supports multiple authentication modes:**

1. **Native authentication**: Username and password validated directly by Amazon Cognito
2. **Federated authentication (OIDC)**: Users authenticate via an external OpenID Connect identity provider (for example, Okta, Auth0, Azure AD). Amazon Cognito exchanges the external tokens for Cognito session tokens. Enable with `app.authProvider.useCognito.useOidc: true` and configure provider details in `infra/config/oidc-config.ts`.
3. **Federated authentication (SAML)**: Users authenticate via a SAML 2.0 identity provider. Enable with `app.authProvider.useCognito.useSaml: true` and configure provider details in `infra/config/saml-config.ts`.

Only one Cognito federation method (OIDC or SAML) can be active at a time. Both native and federated Cognito users receive the same JWT token format and follow the same authorization model.

### External OAuth JWT

When an external OAuth identity provider is configured (`app.authProvider.useExternalOAuthIdp`), users authenticate through the external provider. The ID token format follows the same pattern:

```
Authorization: Bearer eyJraWQiOiJ...
```

:::note[Cognito federation vs External OAuth]
VAMS supports three approaches to external identity providers:

-   **Cognito OIDC federation** (`app.authProvider.useCognito.useOidc`, configured in `infra/config/oidc-config.ts`): External OIDC provider authenticates users, Amazon Cognito issues session tokens. Users appear in the Cognito user pool. Both native and federated users can coexist.
-   **Cognito SAML federation** (`app.authProvider.useCognito.useSaml`, configured in `infra/config/saml-config.ts`): External SAML 2.0 provider authenticates users, Amazon Cognito issues session tokens. Same coexistence model as OIDC federation.
-   **External OAuth** (`app.authProvider.useExternalOAuthIdp`): Bypasses Amazon Cognito entirely. All users authenticate via the external provider. Cannot be combined with Cognito.

Choose Cognito federation (OIDC or SAML) when you need to support both corporate SSO and native username/password accounts. Choose external OAuth when you want to completely replace Cognito with an enterprise identity provider.
:::

:::info[Tokens issued to other clients]
By default the authorizer accepts only tokens issued to the VAMS web application: a Cognito ID token whose `aud`, or access token whose `client_id`, is the web app client, or an external IdP token whose `aud` is `lambdaAuthorizorJWTAudience`. A deployment can accept tokens issued to further clients, such as a remotely hosted MCP server, by listing their app client ids (Cognito) or audiences (external IdP) in `additionalTokenAudiences`. Signature, issuer, expiry and token-use checks are unchanged, and the user's roles decide each request as usual. A listed client must be a user sign-in client (for example the authorization code grant). A machine-to-machine client (the client credentials grant) issues tokens that name no VAMS user, and the API rejects them with `401`. See [Additional token audiences](../deployment/configuration-reference.md#additional-token-audiences).
:::

### API Key

VAMS supports API key authentication for programmatic access. API keys are sent directly in the `Authorization` header:

```
Authorization: vams_ak_abc123...
```

:::info[Token Refresh]
When using Cognito or external OAuth, tokens expire after a configured period. The frontend client automatically refreshes tokens using the refresh token grant. API keys do not refresh: a key can carry an optional expiration date, and can be disabled or revoked through the API key management endpoints.
:::

### When a Credential Is Not Accepted: 401 and 403

The custom Lambda authorizer answers a request it refuses with one of two status codes. The response body is always API Gateway's generic message; the reason is recorded in the authorization audit log, never returned.

| Status | Body                                                                                  | Meaning                                                                                                                                                                                                                                 | What a client does                                                                           |
| ------ | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `401`  | `{"message": "Unauthorized"}`                                                         | The credential did not authenticate: the `Authorization` header is missing or malformed, the token is expired, badly signed or issued for another audience or issuer, or the API key is unknown, disabled or expired.                   | Refresh the token (or sign in again) and resend once. Use a new API key.                     |
| `403`  | `{"message": "User is not authorized to access this resource with an explicit deny"}` | The credential was accepted and the request refused: the source IP address is outside the deployment's allowed ranges, or an API key's user has no roles. The authorizer also answers `403` when it cannot verify tokens at the moment. | Do not refresh. Contact the administrator, or retry later if the identity provider was down. |

A `403` can also come from the two-tier authorization below or from AWS WAF (see [Error Codes](overview.md#error-codes)); signing in again does not change any of them.

:::note[Changed in v2.7.0]
Before v2.7.0 the authorizer answered an expired or invalid token with `403` too. A script that refreshes its token only on `403` must also refresh on `401`. The VAMS web application and CLI handle both.
:::

---

## Authorization Model

VAMS uses a two-tier authorization system enforced by a custom Lambda authorizer:

1. **Tier 1 (API-level)**: Controls which API routes a user's role can access.
2. **Tier 2 (Object-level)**: Controls which specific data entities (databases, assets, pipelines) a user can access.

Both tiers must allow the request for it to succeed.

---

## Endpoints

### Get Amplify Configuration

`GET /api/amplify-config`

Returns the client-side authentication and application configuration. This endpoint is **unauthenticated** and is used by the frontend to bootstrap the authentication flow.

:::note[No Authentication Required]
This endpoint does not require an `Authorization` header.
:::

**Request Parameters:**

None.

**Response:**

```json
{
    "region": "us-east-1",
    "api": "https://abc123.execute-api.us-east-1.amazonaws.com/api/",
    "cognitoUserPoolId": "us-east-1_AbCdEfGhI",
    "cognitoAppClientId": "1a2b3c4d5e6f7g8h9i0j",
    "cognitoIdentityPoolId": "us-east-1:12345678-abcd-efgh-ijkl-123456789012",
    "cognitoUserPoolEndpoint": "https://cognito-idp.us-east-1.amazonaws.com",
    "contentSecurityPolicy": "",
    "bannerHtmlMessage": ""
}
```

**Response Fields:**

| Field                     | Type   | Description                                                                                                                                                                                                                                            |
| ------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `region`                  | string | Deployment Region.                                                                                                                                                                                                                                     |
| `api`                     | string | Stage-inclusive API Gateway invoke URL with a trailing slash; every other path is relative to it. With the API custom domain enabled (`app.api.apiGatewayRest.customDomain`), it is `https://{api-domain}/api/`. See [Base URL](overview.md#base-url). |
| `cognitoUserPoolId`       | string | Amazon Cognito user pool identifier; `"undefined"` when Cognito is not the authentication provider.                                                                                                                                                    |
| `cognitoAppClientId`      | string | Amazon Cognito app client identifier; `"undefined"` when Cognito is not the authentication provider.                                                                                                                                                   |
| `cognitoIdentityPoolId`   | string | Amazon Cognito identity pool identifier; `"undefined"` when Cognito is not the authentication provider.                                                                                                                                                |
| `cognitoUserPoolEndpoint` | string | Partition-aware Amazon Cognito user pool (IDP) endpoint URL; `"undefined"` when Cognito is not the authentication provider. See the note below.                                                                                                        |
| `contentSecurityPolicy`   | string | Always empty. The Content Security Policy is set by the Amazon CloudFront response headers or the ALB listener, not by the web application.                                                                                                            |
| `bannerHtmlMessage`       | string | Optional banner HTML rendered by the web application; empty when not configured.                                                                                                                                                                       |

When an external OAuth identity provider is the authentication provider, the response instead carries `externalOAuthIdpURL`, `externalOAuthIdpClientId`, `externalOAuthIdpScope`, `externalOAuthIdpScopeMfa`, `externalOAuthIdpTokenEndpoint`, `externalOAuthIdpAuthorizationEndpoint`, and `externalOAuthIdpDiscoveryEndpoint`.

:::note[Partition-aware Cognito endpoint]
`cognitoUserPoolEndpoint` is supplied because the AWS Amplify JavaScript library resolves only the `aws` and `aws-cn` partitions and would otherwise build a `.amazonaws.com` host in every Region. In the AWS European Sovereign Cloud the correct DNS suffix is `.amazonaws.eu`, so the web application uses this value rather than deriving the host itself. In the commercial and AWS GovCloud partitions it resolves to the usual `.amazonaws.com` host, so behavior there is unchanged.
:::

**Error Responses:**

| Status | Description                                     |
| ------ | ----------------------------------------------- |
| `500`  | Internal server error generating configuration. |

---

### Get API Version

`GET /api/version`

Returns the current VAMS version. This endpoint is **unauthenticated** and can be used for health checks or version verification.

:::note[No Authentication Required]
This endpoint does not require an `Authorization` header.
:::

**Request Parameters:**

None.

**Response:**

```json
{
    "version": "<current-version>"
}
```

**Error Responses:**

| Status | Description            |
| ------ | ---------------------- |
| `500`  | Internal server error. |

---

### Get Runtime Configuration

`GET /secure-config`

Returns the runtime configuration for the authenticated user, including enabled feature flags and application settings. The frontend reads this endpoint at startup to determine which features to display.

**Request Parameters:**

None.

**Response:**

```json
{
    "featuresEnabled": "CLOUDFRONTDEPLOY,LOCATIONSERVICES,AUTHPROVIDER_COGNITO",
    "locationServiceApiUrl": "https://maps.geo.us-east-1.amazonaws.com/v2/styles/Standard/descriptor?key=<apiKey>",
    "webDeployedUrl": "https://example.cloudfront.net"
}
```

`featuresEnabled` is a comma-separated string of the enabled feature flags. `locationServiceApiUrl` and `webDeployedUrl` are empty strings when Amazon Location Service or the web deployment URL is not configured.

**Error Responses:**

| Status | Description            |
| ------ | ---------------------- |
| `403`  | Not authorized.        |
| `500`  | Internal server error. |

---

### Get Allowed Web Routes

`POST /auth/routes`

Returns the subset of the submitted web application routes that the current user is authorized to access. The frontend uses this to conditionally render navigation items and gate route access.

**Request Body:**

Each entry in `routes` is an object carrying the HTTP method and the web route path. At least one entry is required, and at most 500 may be submitted per request.

| Field         | Type   | Required | Description                                              |
| ------------- | ------ | -------- | -------------------------------------------------------- |
| `method`      | string | Yes      | HTTP method to check: `GET`, `PUT`, `POST`, or `DELETE`. |
| `route__path` | string | Yes      | Web route path, up to 512 characters.                    |

```json
{
    "routes": [
        { "method": "GET", "route__path": "/databases" },
        { "method": "GET", "route__path": "/assets" },
        { "method": "GET", "route__path": "/admin/roles" }
    ]
}
```

**Response:**

`allowedRoutes` contains only the submitted routes the user may access; `userId` is the requesting user's id. `email` is deprecated: it carries the same user id, which need not be an email address, and is kept for older clients.

```json
{
    "allowedRoutes": [
        { "method": "GET", "route__path": "/databases", "object__type": "web" },
        { "method": "GET", "route__path": "/assets", "object__type": "web" }
    ],
    "email": "jdoe",
    "userId": "jdoe"
}
```

**Error Responses:**

| Status | Description                 |
| ------ | --------------------------- |
| `400`  | Invalid request parameters. |
| `500`  | Internal server error.      |

---

### Get User Login Profile

`GET /auth/loginProfile/{userId}`

Retrieves the requesting user's stored login profile. `userId` must be the caller's own identity, and the caller's roles must allow the route: a user whose roles do not grant `/auth/loginProfile` receives a `403`, as does an authenticated user with no roles at all unless `app.authProvider.authorizerOptions.defaultUserRoleName` names a role that grants it. An authorized user whose profile record has not been written yet receives an identity-only profile (just `userId`). The profile may also include organization-specific fields.

**Request Parameters:**

| Parameter | Location | Type   | Required | Description                                       |
| --------- | -------- | ------ | -------- | ------------------------------------------------- |
| `userId`  | path     | string | Yes      | User identifier. Pattern: `^[\w\-\.\+\@]{3,256}$` |

**Response:**

```json
{
    "userId": "user@example.com",
    "email": "user@example.com"
}
```

**Error Responses:**

| Status | Description                                 |
| ------ | ------------------------------------------- |
| `400`  | Invalid request parameters.                 |
| `403`  | Not authorized to view this user's profile. |
| `500`  | Internal server error.                      |

---

### Update User Login Profile

`POST /auth/loginProfile/{userId}`

Updates the login profile for a user. This is the primary endpoint for refreshing user profiles from JWT claims or organizational-specific logic. The request body is optional and may be overridden by organizational profile settings.

**Request Parameters:**

| Parameter | Location | Type   | Required | Description                                       |
| --------- | -------- | ------ | -------- | ------------------------------------------------- |
| `userId`  | path     | string | Yes      | User identifier. Pattern: `^[\w\-\.\+\@]{3,256}$` |

**Request Body:**

Optional. Body contents may be overridden by internal organizational profile logic.

```json
{
    "email": "user@example.com"
}
```

**Response:**

```json
{
    "userId": "user@example.com",
    "email": "user@example.com"
}
```

**Error Responses:**

| Status | Description                                   |
| ------ | --------------------------------------------- |
| `400`  | Invalid request parameters.                   |
| `403`  | Not authorized to update this user's profile. |
| `500`  | Internal server error.                        |

---

## Cognito user management

Amazon Cognito user pool management -- listing, creating, updating, and deleting users, and resetting a user's password -- is documented in the [Authorization API](auth.md#cognito-user-management). These endpoints are only available when Cognito authentication is enabled (`app.authProvider.useCognito.enabled`).

---

## API key management

API key issuance and lifecycle management is documented in the [Authorization API](auth.md#api-keys). Two variants exist: the administrative `/auth/api-keys` routes, which operate across every user's keys, and the self-service [`/auth/user/api-keys`](auth.md#user-self-service-api-keys) routes, through which a user manages their own keys.

---

## Related resources

-   [API Overview](overview.md) -- Authentication methods, headers, and unauthenticated endpoints
-   [Authorization API](auth.md) -- Constraints, roles, user-role assignments, Cognito users, and API keys
