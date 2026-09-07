# Automating VAMS

The VAMS web interface is one of four ways to work with your assets. The command line, an AI agent, and the REST API reach the same data through the same permission checks, and are the right choice when a task is repetitive, needs to run unattended, or belongs inside another application.

This page helps you choose a surface and points you at the reference material for it. It does not teach any of them -- each has its own section of this documentation.

---

## Choosing a surface

| Surface             | Use it when                                                                                                            | Reference                                                              |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **Web interface**   | You are working interactively -- browsing, viewing 3D files, reviewing results, or making one-off changes.              | The rest of this user guide                                            |
| **VAMS CLI**        | You are scripting a repeatable task, operating on many assets at once, or running VAMS steps from a CI/CD pipeline.     | [CLI documentation](../cli/getting-started.md)                         |
| **VAMS MCP server** | You want an AI agent to search, inspect, and report on a deployment in response to questions asked in natural language. | [VAMS MCP Server](../developer/agentic-development.md#vams-mcp-server) |
| **REST API**        | You are building your own application, integration, or connector on top of VAMS.                                       | [API Overview](../api/overview.md)                                     |

:::note[Your permissions apply everywhere]
Every surface enforces the same two-tier authorization. Automating a task does not widen what you can reach -- if you cannot see an asset in the web interface, a script running as you cannot see it either. See [Permissions](permissions.md).
:::

---

## Credentials for programmatic access

Interactive sign-in works for the web interface, but a script or an integration needs a credential that does not involve a person at a keyboard. An **API key** is that credential. It is tied to a specific VAMS user and inherits that user's roles and permissions.

Create one before you start automating, and give it to the CLI, an agent, or your own application as needed. See [API Keys](api-keys.md) for how to create, scope, rotate, and revoke them.

:::tip[Give automation its own user]
Create a dedicated VAMS user for each automated process and assign it only the roles that process needs, then issue the API key against that user. A key scoped this way limits the damage if it leaks, and it makes the audit log show which process acted.
:::

---

## Using the command line

The VAMS CLI covers assets, files, databases, metadata, tags, search, permissions, pipelines, workflows, and executions. It is the surface most VAMS automation uses, and it supports a `--json-output` option throughout so its results can be parsed by other tools.

**Prerequisites**

-   Python installed on the machine that will run the CLI.
-   The URL of your VAMS deployment.
-   A VAMS account, or an API key for unattended use.

**Getting started**

1. Install the CLI. See [CLI Installation](../cli/installation.md).
2. Point it at your deployment:

    ```bash
    vamscli setup https://your-vams-url.example.com
    ```

3. Sign in:

    ```bash
    vamscli auth login -u your.email@example.com
    ```

4. Confirm the CLI is working:

    ```bash
    vamscli database list
    ```

From here, [CLI Getting Started](../cli/getting-started.md) covers profiles for working against more than one deployment and authenticating with an API key, [Command Reference](../cli/command-reference.md) lists every command group, and [Automation](../cli/automation.md) covers scripting patterns and running the CLI in CI/CD.

---

## Using an AI agent

The VAMS MCP server exposes the VAMS API to an AI agent as a set of tools, so you can ask questions about a deployment in natural language -- finding assets matching a description, summarizing what a workflow produced, or checking which assets lack required metadata -- and have the agent gather the answer.

It stores no credentials of its own. It reuses the CLI's profile and sign-in, so set up and authenticate the CLI first.

:::warning[Read-only until you enable more]
The MCP server exposes only read tools by default. Write tools (create and update) require `VAMS_ENABLE_WRITES`, and destructive tools (archive and delete) additionally require `VAMS_ENABLE_DESTRUCTIVE`. Leave both off unless an agent genuinely needs to change data, and turn on destructive tools only against a deployment you can afford to have modified.
:::

For setup, the tool list, and the gating options, see [VAMS MCP Server](../developer/agentic-development.md#vams-mcp-server).

---

## Using the REST API

Build against the REST API when you are writing your own application or connector rather than driving VAMS from a shell. Requests carry an `Authorization` header with a bearer token -- either an API key or a token from your identity provider.

**Prerequisites**

-   The base URL of your deployment's API.
-   An API key, or a token from your identity provider.

Start with [API Overview](../api/overview.md) for the base URL, response format, pagination, and error codes, then [Authentication](../api/authentication.md) for the supported credential types and the authorization model. Individual endpoint references are grouped by domain in the API Reference section.

:::tip[Consider the CLI first]
If you are automating a task rather than building an application, the CLI is usually less work than calling the API directly -- it already handles authentication, token refresh, pagination, and retries. Reach for the API when you need behavior the CLI does not offer.
:::

---

## Related topics

-   [API Keys](api-keys.md) -- Create and manage credentials for programmatic access
-   [Permissions](permissions.md) -- How authorization applies to every surface
-   [CLI Getting Started](../cli/getting-started.md) -- Install, configure, and authenticate the CLI
-   [API Overview](../api/overview.md) -- Base URL, response format, and conventions
