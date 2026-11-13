# Remote MCP Server

The VAMS MCP server can be hosted as a remote server, for MCP clients that connect over HTTP instead of starting a local process. Amazon Quick, for example, connects only to remote MCP servers. This page is the self-hosting recipe: how the remote mode works, how to register the OAuth client that remote MCP clients sign users in with, how to run the server behind an HTTPS front end, and how to connect a client.

VAMS does not deploy a remote MCP server. You host it yourself, in your own account and network, next to your VAMS deployment. For a server that runs on your own machine for your own MCP client, use the local server instead (see [MCP Server](mcp-server.md)).

## How the remote mode works

```
MCP client (Amazon Quick, Claude Code, ...)
   │ HTTPS, Authorization: Bearer <the user's access token>
   ▼
HTTPS front end (TLS on port 443, AWS WAF)
   │ HTTP inside your private network
   ▼
vams-mcp (VAMS_MCP_TRANSPORT=streamable-http)
   │ verifies the token, then calls the VAMS API with that same token
   ▼
VAMS API (authorizer and two-tier Casbin authorization decide every call)
```

-   **Each user signs in with OAuth.** The MCP client signs the user in to the deployment's identity provider (the VAMS Amazon Cognito user pool, or the external OAuth IdP) through an OAuth client you register for remote MCP clients, and sends the user's access token with every request.
-   **The server verifies every token itself** before any tool runs: the signature against the issuer's published keys, the issuer, the audience (the token must be issued to one of the clients in the server's auth configuration), the expiry, the token use, and that the token names a user. A request without a valid token gets `401` with `WWW-Authenticate: Bearer resource_metadata="..."`. When the issuer's keys cannot be read, the server refuses every request with `503` until they can.
-   **Each request acts as its own user.** The server builds a VAMS API client for each request from that request's token and never reads a CLI profile. The VAMS API's authorizer and two-tier Casbin authorization decide every call for that user, exactly as for the web application. The server makes no permission decision of its own.
-   **OAuth discovery.** The server publishes the OAuth 2.0 Protected Resource Metadata (RFC 9728) at `/.well-known/oauth-protected-resource/mcp`, naming the deployment's authorization server and scopes. It is the only route answered without a token, and it holds public discovery data only.
-   **Tool sets.** A remote server registers the read-only `consumer` tool set by default. Each named set holds at most 100 tools, so it fits clients that list at most 100 tools per connection (see [Tool sets](#tool-sets)).
-   **Time budget.** Each tool call has a time budget (240 seconds by default). A list stops early and returns a `NextToken` to continue from, rather than run past the budget, so every call finishes well inside common client limits, such as Amazon Quick's 5 minutes per operation. A write, delete or compute call that is still running at the budget may still complete, so its answer says the outcome is unknown, names the read tool that shows whether it took effect (`checkWith`), and tells the agent not to call it again.
-   **Fair share.** Token verification runs apart from tool calls, and one user may have at most 8 tool calls in progress (`VAMS_MCP_MAX_CALLS_PER_USER`). A call past that limit is answered `TooManyConcurrentCalls` without running, so one user's slow calls cannot delay other users.
-   **No token logging.** No token is logged, returned in a tool result, or written to disk. The CLI's shared request log is turned off in remote mode, because one process serves many users. The server logs one line per request and one per tool call: the user, the client, the tool, the outcome and the duration.

## Prerequisites

| Requirement                         | Notes                                                                                                                           |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| A VAMS 2.7.0 or later deployment    | The API accepts tokens of extra clients through `additionalTokenAudiences` from 2.7.0                                           |
| Python 3.12+ or a container runtime | The server and the CLI install into one environment, as for the [local server](mcp-server.md#install)                           |
| An HTTPS front end                  | For example an Application Load Balancer with an AWS Certificate Manager certificate, or a reverse proxy such as nginx or Caddy |
| A DNS name for the endpoint         | The URL MCP clients connect to, for example `https://mcp.example.com/mcp`                                                       |
| An OAuth client                     | Registered by you for remote MCP clients (step 1). VAMS does not create it                                                      |

## Step 1: Register the OAuth client

Remote MCP clients sign users in through an OAuth client of their own, separate from the VAMS web application's client. The client must sign users in: the **authorization code** grant with PKCE. Never register a machine-to-machine client (the client credentials grant): its tokens name no user, and both the VAMS API and the remote server refuse them.

### With Amazon Cognito

1. **Give the user pool a domain** if it has none. A VAMS deployment with native Cognito sign-in creates no user pool domain, and the authorization code grant needs one. In the Amazon Cognito console, open the VAMS user pool and add a domain in its domain settings (a Cognito domain prefix or your own domain), or use the AWS CLI:

    ```bash
    aws cognito-idp create-user-pool-domain --user-pool-id <user-pool-id> --domain <domain-prefix>
    ```

    A deployment that federates the pool to SAML or OIDC already has a domain; use it. Adding a domain does not open hosted sign-in for the web application's own app client: that client gets it only if OAuth flows are enabled on it.

    The domain's form depends on the partition. Copy it from the user pool's domain settings in the Cognito console rather than building it: `<prefix>.auth.<region>.amazoncognito.com` in commercial Regions, `<prefix>.auth-fips.<region>.amazoncognito.com` in AWS GovCloud (US), and the form the console shows in the AWS European Sovereign Cloud. The authorization and token URLs, which the MCP client needs, are `https://<domain>/oauth2/authorize` and `https://<domain>/oauth2/token` on that same domain (in a commercial Region, `https://<prefix>.auth.<region>.amazoncognito.com/oauth2/authorize` and `.../oauth2/token`).

2. **Create an app client** in the same user pool:

    - Application type: a web application (with a client secret) when the MCP client asks for one, as Amazon Quick does; otherwise a public client.
    - Allowed callback URLs: the MCP client's redirect URL. For Amazon Quick it is `https://<quick-region>.quicksight.aws.amazon.com/sn/oauthcallback`, where `<quick-region>` is the Region of the Amazon Quick account (the Region in the Amazon Quick console's address, for example `us-east-1`), not the Region of the VAMS deployment. For another client, use the redirect URL it shows when you configure it (step 5).
    - OAuth grant type: **Authorization code grant** only.
    - OpenID Connect scopes: `openid`, `email` and `profile`.
    - Identity providers: Cognito user pool, plus the federated IdP when the pool is federated.

    With the AWS CLI:

    ```bash
    aws cognito-idp create-user-pool-client --user-pool-id <user-pool-id> \
        --client-name vams-mcp-remote --generate-secret \
        --allowed-o-auth-flows code --allowed-o-auth-flows-user-pool-client \
        --allowed-o-auth-scopes openid email profile \
        --callback-urls <mcp-client-redirect-url> \
        --supported-identity-providers COGNITO \
        --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH
    ```

    Note the client id and the client secret. Enter the secret in the MCP client only; the server never needs it.

3. **Let the VAMS API accept its tokens.** Add the client id to `app.authProvider.useCognito.additionalTokenAudiences` in the deployment configuration and deploy VAMS (see [Additional token audiences](../deployment/configuration-reference.md#additional-token-audiences)):

    ```json
    "useCognito": {
        "enabled": true,
        "additionalTokenAudiences": ["<app-client-id>"]
    }
    ```

### With an external OAuth IdP

1. Register an application (client) in the IdP for remote MCP clients, with the authorization code grant and PKCE, the MCP client's redirect URL, and the scopes the MCP client requests (for example `openid profile`).
2. Find the audience (`aud`) the IdP puts in that client's access tokens. With some IdPs it is the client id; with others it is an API identifier you define (for example `api://vams-mcp`).
3. Add that audience to `app.authProvider.useExternalOAuthIdp.additionalTokenAudiences` and deploy VAMS.

The tokens must name a user (`username` or `sub`, not the client's own id) and carry no app-only marker. See [Tokens issued to other clients](../api/authentication.md) for the exact checks.

## Step 2: Write the server's auth configuration

On the machine or image the server runs from, install the server as for the [local server](mcp-server.md#install), then run `vams-mcp setup-remote`. It reads the deployment's public auth settings (`/api/amplify-config`, no sign-in), resolves the issuer and its key set, writes the auth configuration file, and prints the values the MCP client needs.

```bash
vams-mcp setup-remote \
    --vams-url https://<your-vams-web-or-api-url> \
    --resource-url https://mcp.example.com/mcp \
    --client-id <app-client-id> \
    --cognito-domain <domain-prefix>.auth.<region>.amazoncognito.com \
    --output ./vams-mcp-remote-auth.json
```

| Option                | Meaning                                                                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--vams-url`          | The VAMS web application URL or API Gateway URL                                                                                                                    |
| `--resource-url`      | The public HTTPS URL MCP clients connect to. Its path is `/mcp`                                                                                                    |
| `--client-id`         | The client from step 1. It cannot be the web application's own client                                                                                              |
| `--audience`          | Another accepted audience, repeatable (at most 10 in all). For an external IdP whose audience is not the client id, pass it here                                   |
| `--cognito-domain`    | Cognito: the user pool domain as the Cognito console shows it, for the authorization and token URLs. Without it the command reads them from the issuer when it can |
| `--issuer`            | External IdP: the issuer of its access tokens, when it differs from the discovery document                                                                         |
| `--scopes`            | The scopes the MCP client requests. Default: `openid email profile` (Cognito), or the deployment's configured scope (external IdP)                                 |
| `--output`, `--force` | The file to write, and whether to replace an existing one                                                                                                          |
| `--json`              | Print the summary as JSON                                                                                                                                          |

The command prints:

```
Values for the remote MCP client:
  Endpoint:          https://mcp.example.com/mcp
  Client ID:         <app-client-id>
  Authorization URL: https://<domain-prefix>.auth.<region>.amazoncognito.com/oauth2/authorize
  Token URL:         https://<domain-prefix>.auth.<region>.amazoncognito.com/oauth2/token
  Scopes:            openid email profile
```

The URLs in this example are the commercial form; the command prints the ones for your domain. The issuer and key set come from the deployment's own `/api/amplify-config` (its user pool endpoint, which follows the partition), so they are right in AWS GovCloud (US) and the AWS European Sovereign Cloud too.

The file holds URLs, client ids and scopes, and the web application's own client id, which the server then refuses to accept as an audience. It holds no secret and no user token, and the command handles none. The command refuses the web application's client as `--client-id` or `--audience`.

## Step 3: Build and run the container image

A minimal image. Save it as `Dockerfile.vams-mcp` at the repository root and build from there:

```dockerfile
FROM python:3.12-slim
WORKDIR /opt/vams
COPY tools/VamsCLI ./VamsCLI
COPY tools/VamsMCP ./VamsMCP
RUN pip install --no-cache-dir ./VamsCLI ./VamsMCP \
    && useradd --system --uid 10001 vams
USER vams
ENV VAMS_MCP_TRANSPORT=streamable-http \
    VAMS_MCP_AUTH_CONFIG=/etc/vams-mcp/auth.json \
    VAMS_MCP_HOST=0.0.0.0 \
    VAMS_MCP_PORT=8000
EXPOSE 8000
CMD ["vams-mcp"]
```

`VAMS_MCP_HOST=0.0.0.0` makes the server reachable on the container's own network, so that the front end can forward to it. Outside a container keep the default, `127.0.0.1`. The server refuses a bind beyond this machine unless the resource URL is `https`, that is, unless a front end terminates TLS. Mount the auth configuration file read-only, or bake it into a derived image.

To try the image on one machine, publish the port on the loopback interface only, and put the front end on the same machine:

```bash
docker build -f Dockerfile.vams-mcp -t vams-mcp .
docker run --rm -p 127.0.0.1:8000:8000 \
    -v "$PWD/vams-mcp-remote-auth.json:/etc/vams-mcp/auth.json:ro" vams-mcp
```

Never publish the port on all interfaces (`-p 8000:8000`): clients reach the server only through the HTTPS front end.

### Settings

| Variable                           | Default     | Meaning                                                                                          |
| ---------------------------------- | ----------- | ------------------------------------------------------------------------------------------------ |
| `VAMS_MCP_TRANSPORT`               | `stdio`     | `streamable-http` runs the remote server                                                         |
| `VAMS_MCP_AUTH_CONFIG`             | (none)      | The file `vams-mcp setup-remote` wrote. Required in remote mode                                  |
| `VAMS_MCP_HOST`                    | `127.0.0.1` | The listen address. Wider only inside a container or private network, behind the HTTPS front end |
| `VAMS_MCP_PORT`                    | `8000`      | The listen port                                                                                  |
| `VAMS_MCP_TOOLSETS`                | `consumer`  | The [tool sets](#tool-sets) to register                                                          |
| `VAMS_MCP_TOOL_TIMEOUT`            | `240`       | Seconds one tool call may take (10 to 1800). Keep it below the client's own limit                |
| `VAMS_MCP_MAX_CALLS_PER_USER`      | `8`         | Tool calls one user may have in progress at once (1 to 64)                                       |
| `VAMS_ENABLE_WRITES`               | `false`     | `true` registers the write tools of the selected sets                                            |
| `VAMS_ENABLE_DESTRUCTIVE`          | `false`     | `true` (with writes) registers the archive and delete tools of the selected sets                 |
| `VAMS_MAX_PAGES`, `VAMS_PAGE_SIZE` | `20`, `100` | Paging bounds, as for the local server                                                           |

`VAMS_PROFILE` is refused in remote mode: the server never acts as one CLI identity.

### Tool sets

| Set          | Tools                                                                                                                      |
| ------------ | -------------------------------------------------------------------------------------------------------------------------- |
| `consumer`   | Read-only: databases, assets, files, metadata, versions, links, search, download links, workflows and runs, tags, comments |
| `operator`   | `consumer` plus the writes and deletes of asset content, comments, subscriptions, and running workflows                    |
| `admin`      | Databases, tags, metadata schemas, pipelines, templates, workflows, triggers, subscriptions and API keys                   |
| `compliance` | Compliance: schemas, bindings, evaluations and sweeps, quarantine and exceptions, cascades and the compliance audit trail  |
| `edh`        | EDH Workstations add-on: workstations, the catalog and container jobs                                                      |
| `edh-admin`  | EDH Workstations add-on: images, AMI recipes, capacity reservations, instances and settings                                |
| `all`        | Every tool the gates allow. More than 100 tools; the server warns at start                                                 |

Sets combine (`VAMS_MCP_TOOLSETS=consumer,edh`), and the write and destructive gates still apply inside a set. Each named set holds at most 100 tools; a combination can hold more, and the server logs a warning at start when it does, because some clients drop the tools past 100 without saying so. Every user still needs the VAMS permissions for what a tool does: a tool set decides which tools a client sees, never what a user may do.

## Step 4: Put an HTTPS front end in front of it

The front end terminates TLS, exposes only port 443, and forwards to the server's port on your private network.

**Application Load Balancer and Amazon ECS on AWS Fargate (an example):**

-   Run the image as an ECS service on Fargate in private subnets. The task's security group allows the server port (8000) only from the load balancer's security group.
-   An internet-facing (or internal, see below) Application Load Balancer with an HTTPS listener on 443 and an ACM certificate for the endpoint's DNS name. Its security group allows 443 only. Redirect or drop port 80.
-   A target group on HTTP port 8000, with the health check path `/.well-known/oauth-protected-resource/mcp` (it answers `200` without a token).
-   An idle timeout above the tool time budget, for example 300 seconds.
-   An AWS WAF web ACL on the load balancer with a rate-based rule, and the managed common rule set.
-   Keep the `Host` header as the client sent it. The server's DNS rebinding protection accepts the endpoint's own host name and the loopback names only.

**Amazon ECS Express Mode (the simplest example):** Express Mode runs the image as an ECS service on AWS Fargate behind an internet-facing Application Load Balancer that it creates, with an HTTPS listener on 443 only and a generated HTTPS endpoint.

1. Push the image to Amazon ECR, and create two IAM roles: a task execution role with the AWS managed policy `AmazonECSTaskExecutionRolePolicy`, and an infrastructure role with the AWS managed policy `AmazonECSInfrastructureRoleforExpressGatewayServices`.
2. Create the service with container port 8000, the environment `VAMS_MCP_TRANSPORT=streamable-http`, `VAMS_MCP_HOST=0.0.0.0` and `VAMS_MCP_AUTH_CONFIG`, and the health check path `/.well-known/oauth-protected-resource/mcp`:

    ```bash
    aws ecs create-express-gateway-service \
        --service-name vams-mcp \
        --execution-role-arn <task-execution-role-arn> \
        --infrastructure-role-arn <infrastructure-role-arn> \
        --primary-container '{"image": "<account>.dkr.ecr.<region>.amazonaws.com/vams-mcp:<tag>", "containerPort": 8000, "environment": [{"name": "VAMS_MCP_TRANSPORT", "value": "streamable-http"}, {"name": "VAMS_MCP_HOST", "value": "0.0.0.0"}, {"name": "VAMS_MCP_AUTH_CONFIG", "value": "/etc/vams-mcp/auth.json"}, {"name": "VAMS_MCP_TOOL_TIMEOUT", "value": "50"}]}' \
        --health-check-path /.well-known/oauth-protected-resource/mcp
    ```

3. The endpoint, `https://<generated-name>.ecs.<region>.on.aws`, is known only after the service exists. Run `vams-mcp setup-remote` with `--resource-url https://<generated-name>.ecs.<region>.on.aws/mcp`, put the file in the image (or write it at container start from environment variables), and update the service.
4. **Idle timeout.** The load balancer's idle timeout is 60 seconds by default, shorter than the default tool time budget (240 seconds). Set `VAMS_MCP_TOOL_TIMEOUT` below it (for example `50`, as above), or raise the load balancer's idle timeout above the budget.
5. **AWS WAF.** Attach a web ACL with a rate-based rule to the load balancer. The ECS service does not report which load balancer it uses: find it through the listener rule that matches the service's host name. A load balancer created moments before can briefly answer `WAFUnavailableEntityException`; try again.
6. **Check the gate anonymously** before you connect a client: the metadata document answers `200`, and `POST /mcp` without a token answers `401` with `WWW-Authenticate: Bearer resource_metadata="..."` (see [Check that it works](#check-that-it-works)).

**A reverse proxy on the same host:** run the server with the default `127.0.0.1` bind and forward `https://mcp.example.com/mcp` and `https://mcp.example.com/.well-known/oauth-protected-resource/mcp` to `http://127.0.0.1:8000`, keeping the paths and the `Host` header.

### Deployments that allow API access only from a VPC

When the VAMS API is reachable only from your VPC (a private API endpoint, or an IP allow list), run the server inside that VPC so that it reaches the API through the same path as your other clients. The MCP client still needs to reach the endpoint: a client that connects over the internet, such as a hosted SaaS client, needs an internet-facing front end, while a client inside your network can use an internal load balancer. The server's own calls to the VAMS API never leave your network either way.

## Step 5: Connect a client

### Amazon Quick

Amazon Quick is available in some AWS Regions and partitions only; check its Region availability before planning on it, in particular for AWS GovCloud (US) and the AWS European Sovereign Cloud.

Check the gate anonymously first (see [Check that it works](#check-that-it-works)). Then, in the Amazon Quick console:

1. Open **Connectors**, choose **Create for your team**, and choose **Model Context Protocol**.
2. **MCP server endpoint:** `https://mcp.example.com/mcp`.
3. **Connection type:** **Public network**.
4. **Authentication:** **User authentication (OAuth)**, with the **Client ID** and **Client secret** of the app client from step 1, and the **Authorization URL** and **Token URL** that `vams-mcp setup-remote` printed (`https://<domain>/oauth2/authorize` and `https://<domain>/oauth2/token`). Amazon Quick has no scopes field: it reads the scopes from the server's Protected Resource Metadata (`scopes_supported`), which `setup-remote` fills with the client's scopes (`openid email profile` for Cognito).
5. Create the connector, and sign in when Amazon Quick asks. Any VAMS user of the user pool can sign in, and each acts with their own VAMS permissions.
6. Amazon Quick lists the server's tools.

The app client's callback URL must be Amazon Quick's redirect URL for the Region of the Quick account (step 1).

After you upgrade the server or change its tool sets, refresh the integration's tool list (**Sync**). Amazon Quick keeps the list it read when the integration was created.

### Other clients

Any MCP client that supports streamable HTTP with OAuth can connect. It discovers the authorization server from the `401` challenge and the Protected Resource Metadata. For example, Claude Code:

```bash
claude mcp add --transport http vams https://mcp.example.com/mcp
```

A client that cannot register itself dynamically (Amazon Cognito does not support dynamic client registration) needs the client ID entered by hand.

## Check that it works

```bash
# The metadata document answers without a token:
curl -s https://mcp.example.com/.well-known/oauth-protected-resource/mcp

# Anything else without a token is refused with the challenge:
curl -si https://mcp.example.com/mcp -X POST | grep -i '^www-authenticate'
```

In the client, ask the agent to call `vams_status`. In remote mode it returns `mode` `remote`, `userId` (the signed-in user), `clientId` (the client the token was issued to), `apiUrl` and `gates.toolsets`.

## Security

-   **Per-user identity.** Every tool call runs as the user whose token the request carries, under that user's VAMS roles. The server holds no identity of its own and no shared credential.
-   **TLS.** Clients reach the server only through the HTTPS front end. The server itself speaks plain HTTP on the private network or the loopback interface.
-   **No token logging.** Tokens are never logged, returned or stored. Keep the front end's own access logs free of headers.
-   **AWS WAF.** Put a web ACL with a rate-based rule on the front end.
-   **Keep write and destructive tools off unless they are needed.** The default is the read-only `consumer` set with both gates off. Turn on `VAMS_ENABLE_WRITES` and `VAMS_ENABLE_DESTRUCTIVE` only on a server whose users need them.
-   **Download links are credentials.** `generate_download_url` and `generate_download_urls_bulk` are reads, but each URL they return lets anyone holding it download the file until it expires, and it is stored in the client's conversation history.

### Threat model

| Concern                   | What protects against it                                                                                                                                                                                                                                                                                                                                      |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Who can reach what        | Anyone who can reach the endpoint can fetch the metadata document (public data) and nothing else without a valid token. Every tool call needs a token the issuer signed for one of the configured clients, and the VAMS API checks every call again (authorizer and Casbin) for that user.                                                                    |
| Token theft               | A stolen access token works only until it expires, and only within the user's own permissions. TLS on the front end protects tokens in transit; the server never logs or stores them. Keep access token lifetimes short in the IdP, and revoke a user's sessions in the IdP when needed.                                                                      |
| Confused deputy           | The server accepts only tokens issued to its own configured clients, not the web application's tokens or another client's, and forwards each token only to the VAMS API the deployment's authorizer protects, for that same request. It never uses one user's token for another user's request.                                                               |
| Machine-to-machine tokens | Tokens that name no user (client credentials) are refused by the server and by the VAMS API.                                                                                                                                                                                                                                                                  |
| Denial of service         | Request bodies are limited to 1 MiB, each tool call has a time budget, one user may have at most 8 calls in progress, token verification never waits behind tool calls, lists are paged and bounded (`VAMS_MAX_PAGES`, `VAMS_PAGE_SIZE`), and unknown signing keys cannot trigger a key download per request. Add a rate-based AWS WAF rule on the front end. |
| Identity provider outage  | When the issuer's keys cannot be read, no request passes (`503`); the server never falls back to accepting unverified tokens.                                                                                                                                                                                                                                 |
| DNS rebinding             | The server accepts only the endpoint's own host name and the loopback names in the `Host` header.                                                                                                                                                                                                                                                             |

## Troubleshooting

| Symptom                                                                | Cause and fix                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Every request gets `401` with `error="invalid_token"`                  | The token was issued to a client that is not in the auth configuration (for example the web application's client), has expired, or names no user. Check `--client-id` and `--audience` in `vams-mcp setup-remote`. The server logs the reason category.                                                                                                                                                                                          |
| Tools return `NotAuthenticated` saying VAMS did not accept the sign-in | The server accepted the token but the VAMS API refused it: the client id is not yet in `additionalTokenAudiences`, or the deployment with it is not deployed yet. Sign in again if the token expired.                                                                                                                                                                                                                                            |
| Sign-in shows Amazon Cognito's `error=redirect_mismatch` page          | The app client's allowed callback URLs do not include the redirect URL the client sent, for example Amazon Quick's URL for another Region. Add the exact `redirect_uri` of the authorize request (read it from the browser's network panel, or the address of the error page) to the app client's callback URLs. For Amazon Quick it is `https://<quick-region>.quicksight.aws.amazon.com/sn/oauthcallback` for the Region of the Quick account. |
| Long calls fail after about 60 seconds behind a load balancer          | The load balancer's idle timeout (60 seconds by default) is shorter than the tool time budget. Set `VAMS_MCP_TOOL_TIMEOUT` below the idle timeout, or raise the idle timeout above the budget.                                                                                                                                                                                                                                                   |
| Every request gets `503`                                               | The server cannot read the issuer's keys. Check that the server can reach the issuer's `jwks_uri` (in the auth configuration file) over HTTPS.                                                                                                                                                                                                                                                                                                   |
| `421` or a host error from the server                                  | The front end does not forward the client's `Host` header, or the endpoint's host name differs from `--resource-url`. Keep the host, or run `vams-mcp setup-remote` again with the right URL.                                                                                                                                                                                                                                                    |
| The server stops at start with `cannot start`                          | The message names the setting: a missing or invalid auth configuration file, `VAMS_PROFILE` set, or a wide bind with a non-`https` resource URL.                                                                                                                                                                                                                                                                                                 |
| The client lists fewer tools than expected                             | Check `VAMS_MCP_TOOLSETS` and the write and destructive gates (`vams_status` reports them). A combination of sets above 100 tools can be cut by the client. After a change, refresh the client's tool list (Sync in Amazon Quick).                                                                                                                                                                                                               |
| A long list stops with a `NextToken` and a note about the time budget  | The call reached its time budget. Call the tool again with `starting_token`, or narrow the request.                                                                                                                                                                                                                                                                                                                                              |
| A tool answers `ToolTimeout` with `outcomeUnknown`                     | A write, delete or compute call ran past its time budget and may still complete. Check with the tool named in `checkWith` before calling it again.                                                                                                                                                                                                                                                                                               |
| A tool answers `TooManyConcurrentCalls`                                | The user already has `VAMS_MCP_MAX_CALLS_PER_USER` calls in progress. Nothing ran; call again when they finish.                                                                                                                                                                                                                                                                                                                                  |

## Upgrade

Upgrade the server as for the local server (`git pull`, then `pip install -e ../VamsCLI` and `pip install --upgrade -e .`), or rebuild the image. Restart it, and refresh each client's tool list. Run `vams-mcp setup-remote` again only when the deployment's auth settings, the endpoint or the client change.

## Related topics

-   [MCP Server](mcp-server.md) covers the local server, every tool and the tool tiers.
-   [Authentication](../api/authentication.md) covers the token checks of the VAMS API.
-   [Additional token audiences](../deployment/configuration-reference.md#additional-token-audiences) covers the deployment setting step 1 needs.
