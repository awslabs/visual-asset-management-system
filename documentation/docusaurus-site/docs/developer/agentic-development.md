# Agentic Development

:::warning[Use at Your Own Risk]
AI-assisted coding tools are used at your own risk. Configure agent permissions according to your organizational security standards. All AI-generated code, configuration, and infrastructure changes must be reviewed and validated by qualified personnel before deploying to any production environment.
:::

VAMS supports AI-assisted development through a layered system of steering documents that guide AI coding agents to follow project conventions, architecture patterns, and quality standards. These documents ensure that AI agents produce code consistent with VAMS patterns regardless of which developer or agent is performing the work.

Two AI coding agents are supported: Claude Code and Kiro. Each reads from dedicated steering file locations, but the underlying guidance is consistent across both agents — the two steering families are kept synchronized so either agent produces the same result.

VAMS also ships an MCP server and an agent skill for **operating** a deployed VAMS instance with agents, rather than writing VAMS code. See [Operating a Deployment with Agents](#operating-a-deployment-with-agents).

:::note[Cline support deprecated]
The Cline agent (`.clinerules/workflows/`) is no longer supported. Its steering files have been removed. Use Claude Code or Kiro instead.
:::

## Supported Agents

| Agent       | Steering Location                       | Description                                                                                                                                  |
| ----------- | --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Claude Code | `CLAUDE.md` files + `.claude/commands/` | Component-level steering documents placed in each major directory (auto-loaded), plus reusable slash commands for common multi-step tasks.   |
| Kiro        | `.kiro/steering/`                       | Workflow-based development guides with checklists, templates, and mandatory rules, plus a front-end steering file mirroring `web/CLAUDE.md`. |

## Steering File Architecture

VAMS uses a layered approach to steering documents. Each layer provides progressively more specific guidance.

### Layer 1: Root-Level Context

The root `CLAUDE.md` file provides project-wide context that applies across all components. It defines:

-   Project overview, version information, and technology stack
-   Cross-component patterns (such as adding a new API endpoint or feature switch)
-   Critical rules that apply everywhere (Pydantic v1 only, no hardcoded table names, AWS KMS encryption for all storage)
-   Gold standard reference files for each component
-   Git workflow and naming conventions

### Layer 2: Component-Specific Steering

Each major component directory contains its own `CLAUDE.md` with patterns specific to that component. These documents cover directory structure, coding standards, key files, anti-patterns, and component-specific checklists.

### Layer 3: Workflow Documents

The `.kiro/steering/` directory contains detailed development workflow guides. These documents provide step-by-step checklists, code templates, and mandatory rules for complex multi-file tasks such as adding a new backend API endpoint or building a new AWS CDK nested stack.

## Available Steering Documents

### CLAUDE.md Files

| File                      | Scope                  | Key Topics                                                                                                  |
| ------------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| `CLAUDE.md`               | Project-wide           | Architecture overview, cross-component patterns, critical rules, gold standard references, deployment modes |
| `web/CLAUDE.md`           | React frontend         | Cloudscape components, HashRouter, Synonyms system, service-layer pattern, viewer plugins, feature switches |
| `backend/CLAUDE.md`       | Python Lambda backend  | Pydantic v1 models, Casbin authorization, DynamoDB patterns, Lambda handler structure, logging and testing  |
| `infra/CLAUDE.md`         | AWS CDK infrastructure | Nested stacks, Lambda builders, security helpers, configuration system, multi-partition support             |
| `tools/VamsCLI/CLAUDE.md` | Python CLI tool        | Click framework, profile management, command groups, constants pattern, JSON output mode                    |
| `tools/VamsMCP/CLAUDE.md` | MCP server             | Tool definitions and gating tiers, pagination and response shapes, stdout discipline, CLI reuse rules       |
| `documentation/CLAUDE.md` | Documentation site     | Docusaurus conventions, writing style, sidebar configuration, cross-reference sources                       |

### Workflow Documents

The following workflow documents live in `.kiro/steering/`.

| File                                  | Scope              | Key Topics                                                                                                                             |
| ------------------------------------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `BACKEND_CDK_DEVELOPMENT_WORKFLOW.md` | Backend + CDK      | End-to-end API endpoint development: Pydantic models, Lambda handlers, CDK Lambda builders, API Gateway routes, security helpers       |
| `CDK_DEVELOPMENT_WORKFLOW.md`         | CDK infrastructure | Nested stack patterns, configuration management, feature switches, Lambda builder templates, security compliance, pipeline development |
| `CLI_DEVELOPMENT_WORKFLOW.md`         | CLI tool           | Click command structure, profile support, constants pattern, error handling, JSON output, testing                                      |
| `WEB_DEVELOPMENT_WORKFLOW.md`         | React frontend     | Service-layer pattern, Cloudscape imports, HashRouter, Synonyms, lazy loading, Context + useReducer, theme system, viewer plugins      |
| `WEB_FRONTEND.md`                     | React frontend     | Front-end steering mirroring `web/CLAUDE.md`: directory structure, mandatory rules, viewer plugin system, testing (Jest), conventions  |
| `DOCUMENTATION_WORKFLOW.md`           | Documentation site | Docusaurus conventions, admonition syntax, sidebar updates, writing style, cross-references, build commands                            |

## How Steering Documents Guide Development

The steering documents enforce consistent patterns across the codebase. The following examples illustrate the type of guidance they provide.

### Example 1: Cross-Component API Endpoint Development

Adding a new API endpoint in VAMS requires coordinated changes across as many as ten files, spanning the backend, infrastructure, front end, client tooling, and documentation. The root `CLAUDE.md` defines this pattern explicitly:

| Step | File                                                          | Action                                                           |
| ---- | ------------------------------------------------------------- | ---------------------------------------------------------------- |
| 1    | `backend/backend/common/apiRoutes.py`                         | Define the route constant and add it to its category group array |
| 2    | `backend/backend/handlers/\{domain\}/\{handler\}.py`          | Implement Lambda handler with Casbin enforcement                 |
| 3    | `backend/backend/models/\{domain\}.py`                        | Define request/response models (Pydantic v1)                     |
| 4    | `infra/lib/lambdaBuilder/\{domain\}Functions.ts`              | Build Lambda with environment variables, permissions, VPC config |
| 5    | `infra/lib/nestedStacks/apiLambda/apiBuilder2-nestedStack.ts` | Attach Lambda to API Gateway route                               |
| 6    | `web/src/services/APIService.ts`                              | Add API call method                                              |
| 7    | `tools/VamsCLI/vamscli/commands/\{group\}.py`                 | Add CLI command, and the endpoint path to `constants.py`         |
| 8    | `tools/VamsMCP/vams_mcp/server.py`                            | Expose as an MCP tool if agents should reach it                  |
| 9    | `documentation/VAMS_API.yaml`                                 | Add the path and its component schemas to the OpenAPI spec       |
| 10   | `documentation/docusaurus-site/docs/api/\{domain\}.md`        | Add the human-readable endpoint reference                        |

Without steering documents, an AI agent might create a handler without the corresponding API Gateway route (resulting in dead code), add a route without a handler (resulting in 500 errors), or stop at the backend and leave the client tooling and documentation describing an API that no longer matches the deployment.

### Example 2: Synonyms System for Customizable Display Names

The frontend steering document (`web/CLAUDE.md`) enforces the use of VAMS Synonyms for all user-visible text. The Synonyms system allows deployers to customize display names for core entities such as "Asset", "Database", and "Comment".

```typescript
// INCORRECT - hardcoded strings
<Header>Assets</Header>
<p>Select a Database</p>

// CORRECT - use Synonyms for customizable display names
import Synonyms from "../../synonyms";
<Header>{Synonyms.Assets}</Header>
<p>Select a {Synonyms.Database}</p>
```

The steering document specifies that Synonyms must be used in headers, labels, descriptions, placeholders, alt text, error messages, success messages, button text, modal titles, and empty state text. It also specifies that Synonyms must not be used in API request body values, variable names, route paths, or log messages.

### Example 3: Required Security Calls for Lambda Builders

The CDK workflow documents mandate that every Lambda builder function includes five security-related calls, followed by the domain-specific resource grants. Omitting any of these calls results in deployment failures (CDK Nag violations) or security gaps.

```typescript
// Every Lambda builder must include these five calls, in order:

// 1. AWS KMS key permissions for encryption/decryption
kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

// 2. Authorization table read grants + Amazon CloudWatch audit log group write grants
setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);

// 3. VAMS_RESOURCE_PARAM_PREFIX environment variable + AWS Systems Manager Parameter Store grant
globalLambdaEnvironmentsAndPermissions(fun, config);

// 4. Per-Lambda CDK Nag suppressions (IAM4/IAM5, wildcard AWS KMS)
suppressCdkNagLambda(fun);

// 5. CDK Nag suppression for grant-based permissions (only when using grantRead/grantReadWrite)
suppressCdkNagErrorsByGrantReadWrite(scope);

// Plus the domain-specific Amazon DynamoDB table grants the handler needs
storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
```

`setupSecurityAndLoggingEnvironmentAndPermissions()` carries the two-tier authorization and audit-logging grants: read access to the constraints, user-roles, and roles tables that `CasbinEnforcer` reads, and `logs:CreateLogStream` and `logs:PutLogEvents` on the nine VAMS audit log groups. A builder that omits it synthesizes and deploys cleanly, then returns 403 on every request and writes no audit events.

These patterns are documented with complete code templates in both `CDK_DEVELOPMENT_WORKFLOW.md` and `BACKEND_CDK_DEVELOPMENT_WORKFLOW.md`, ensuring that AI agents produce compliant Lambda builders on the first attempt.

## Claude Code Slash Commands

In addition to steering documents, Claude Code supports **slash commands** — reusable skill prompts stored in `.claude/commands/`. These commands automate common multi-step development tasks and can be invoked from the Claude Code CLI with `/<command-name>`.

| Command                  | File                                        | Description                                                                                                                                  |
| :----------------------- | :------------------------------------------ | :------------------------------------------------------------------------------------------------------------------------------------------- |
| `/add-api-endpoint`      | `.claude/commands/add-api-endpoint.md`      | Scaffold a new backend API endpoint across all required files (handler, model, Lambda builder, API route, frontend service)                  |
| `/add-pipeline`          | `.claude/commands/add-pipeline.md`          | Scaffold a new processing pipeline with container, Lambda, CDK stack, and configuration                                                      |
| `/deploy-check`          | `.claude/commands/deploy-check.md`          | Run pre-deployment validation checklist (config, CDK synth, lint, security)                                                                  |
| `/generate-permissions`  | `.claude/commands/generate-permissions.md`  | Generate VAMS permission constraint JSON templates                                                                                           |
| `/refresh-steering-docs` | `.claude/commands/refresh-steering-docs.md` | Update CLAUDE.md directory structures and key file references                                                                                |
| `/update-changelog`      | `.claude/commands/update-changelog.md`      | Generate changelog entries from git commits                                                                                                  |
| `/update-docs`           | `.claude/commands/update-docs.md`           | Update Docusaurus documentation pages based on recent code changes                                                                           |
| `/verify-docs`           | `.claude/commands/verify-docs.md`           | Cross-check documentation accuracy against source code                                                                                       |
| `/vams-agent`            | `.claude/commands/vams-agent.md`            | Operate a VAMS deployment at runtime via `vamscli` (search, inspect, bulk-update, cross-link); self-discovers commands, read-only by default |

These commands encode the cross-component patterns from the steering documents into executable workflows. For example, `/add-api-endpoint` automates the six-file change pattern described in Example 1 above.

## Operating a Deployment with Agents

The steering documents above guide agents that **write VAMS code**. VAMS also ships two components for agents that **operate a running VAMS deployment** — searching, inspecting, and managing assets on a user's behalf. Both authenticate through the user's existing VAMS credentials and inherit exactly that user's two-tier (RBAC/ABAC) permissions, so an agent can never reach data the user could not reach through the web application or the CLI.

| Component        | Location                        | Interface                         | Use when                                                                   |
| :--------------- | :------------------------------ | :-------------------------------- | :------------------------------------------------------------------------- |
| VAMS MCP server  | `tools/VamsMCP/`                | Model Context Protocol over stdio | The agent host supports MCP and you want typed, structured tools           |
| VAMS agent skill | `tools/VamsAgentSkill/SKILL.md` | Shell commands via `vamscli`      | The host has no MCP support, or you want the agent to use the CLI directly |

### VAMS MCP Server

The MCP server exposes the VAMS API as agent-callable tools for any MCP-capable host (Kiro, Claude Desktop, Amazon Bedrock agents, or an internal orchestrator). It is built on the `mcp` SDK and reuses the VamsCLI `APIClient`, inheriting its retries, throttling backoff, typed errors, and automatic token refresh.

The server stores no keys, tokens, or URLs. It reads the API Gateway URL and authentication from the local `vamscli` profile, so the MCP host configuration contains no secrets and every user runs the server against their own account:

```bash
pip install ./tools/VamsCLI
vamscli setup https://<your-api-id>.execute-api.<region>.amazonaws.com
vamscli auth login -u you@example.com
```

Tools are organized into three tiers, gated by environment variable:

| Tier            | Environment variable                                 | Contents                                                                                                                                                                                                                                                             |
| :-------------- | :--------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Read and search | Always available                                     | Databases, assets, files, metadata, versions, history, asset links, tags, metadata schemas, full-text and geospatial search, allowed API routes; pipelines, pipeline templates and their tag schemas, workflows and their triggers, executions with details and logs |
| Write           | `VAMS_ENABLE_WRITES=true`                            | Create databases, assets, folders, and version snapshots; update assets and metadata; create and update pipelines, pipeline templates, workflows, and workflow triggers; execute workflows, re-run and abort executions                                              |
| Destructive     | `VAMS_ENABLE_DESTRUCTIVE=true` (plus writes enabled) | Archive, unarchive, and permanently delete assets; delete databases; archive and unarchive pipelines and workflows; delete pipeline templates and workflow triggers; permanently delete executions                                                                   |

Both mutation tiers are **off by default**, and a gated tool is not registered with the host at all — an agent cannot invoke a tool it never receives. Keep destructive tools out of the host's auto-approve list.

Executing a workflow or re-running an execution launches the pipelines it references, which run real AWS compute and can incur cost. Keep those tools out of the auto-approve list as well, even though they sit in the write tier rather than the destructive one.

The `list_allowed_api_routes` tool reports the routes the authenticated user is authorized to call. Calling it at the start of a session lets an agent scope its plan to what the user can actually do rather than discovering an authorization failure mid-task.

See `tools/VamsMCP/README.md` for installation, host registration, and the full tool list.

### VAMS Agent Skill

The agent skill drives a deployment through the installed `vamscli` tool. Rather than hardcoding a command list that would drift as VAMS evolves, the skill treats `vamscli --help` as the source of truth: it discovers the deployment's current command groups, arguments, and flags at runtime and caches them for the session.

The skill's operating rules are:

-   **Authenticate per session** — verify the CLI is installed and authenticated before doing any work, treating API keys and tokens as secrets.
-   **Scope to allowed routes** — fetch the routes the authenticated user may call and only offer commands within that boundary, rather than attempting an action and handling the rejection.
-   **Read-only by default** — mutating commands (create, delete, edit, execute, upload) require explicit authorization from the user, and destructive or bulk operations require confirmation even when authorized.
-   **Never fabricate** — no invented commands, flags, identifiers, or results.

The skill also documents VAMS order-of-operations rules that agents otherwise learn by failure: asset identifiers are generated by VAMS and not caller-chosen, a bucket precedes a database which precedes an asset which precedes files, metadata schemas can restrict which keys are writable, search indexing and workflow execution are asynchronous, and list results are paged.

An optional section points at this documentation site for agents with internet access, for concept questions that `--help` cannot answer. Command syntax always comes from `--help`, because the published documentation tracks the latest release and may differ from the deployed version.

The skill is host-agnostic. Claude Code invokes it as the `/vams-agent` slash command; a managed runtime such as Amazon Bedrock AgentCore needs `vamscli` installed and a way to set the profile to the session user's token, which the skill's authentication routine accommodates.

:::warning[Review agent actions before authorizing changes]
Granting an agent write or destructive access lets it modify or delete VAMS data under your identity. Start read-only, enable mutations only for the specific task at hand, and review what an agent proposes before authorizing bulk or destructive operations. Workflow execution in particular runs real AWS compute and can incur significant cost on GenAI and 3D processing pipelines.
:::

### Keeping the CLI, MCP Server, and Skill Aligned

The MCP server sits downstream of the CLI: it imports the VamsCLI `APIClient` and `ProfileManager` directly rather than calling the REST API itself. Changes therefore propagate in one direction, and each hop must be updated together:

```
backend API → tools/VamsCLI → tools/VamsMCP → tools/VamsAgentSkill
```

A change to a `vamscli` command or `APIClient` method — a renamed method, a new required parameter, or a changed response shape — breaks the corresponding MCP tool without any error at import time; the failure appears only when an agent calls the tool.

This chain is documented in both steering families, and a change to the rules must be made in all of them:

| Document                                     | Contents                                                                         |
| :------------------------------------------- | :------------------------------------------------------------------------------- |
| `CLAUDE.md` (root), Pattern 7                | The canonical propagation chain and its rules                                    |
| `tools/VamsCLI/CLAUDE.md`                    | The MCP propagation step in the "Adding a New Command" checklist                 |
| `tools/VamsMCP/CLAUDE.md`                    | The upstream dependency on the CLI, and the response-shape and SDK-version rules |
| `.kiro/steering/CLI_DEVELOPMENT_WORKFLOW.md` | The equivalent MCP propagation checklist for Kiro                                |

The agent skill is the exception: because it self-discovers commands, ordinary command additions require no skill edit. It changes only when a structural rule changes, such as entity ordering, identifier semantics, permission scoping, or a new category of mutating command.

## Keeping Steering Documents in Sync

VAMS maintains two parallel families of steering documents — `CLAUDE.md` files for Claude Code and `.kiro/steering/` workflow documents for Kiro — that describe the same standards for two different agents. A rule that lands in only one family means one agent scaffolds outdated code. The synchronization rules are as follows:

-   **`CLAUDE.md` files**: These files provide the canonical component-level steering. When a component-level standard changes, update the relevant `CLAUDE.md` and the corresponding Kiro steering document in the same change.
-   **`.kiro/steering/`**: This directory is committed to version control and shared across all developers. The `WEB_FRONTEND.md` file mirrors `web/CLAUDE.md`; when front-end standards change, update both locations.
-   **Synchronization is bidirectional**: A change made first in a Kiro steering document must be carried back into the matching `CLAUDE.md`, exactly as a `CLAUDE.md` change must be carried into Kiro steering. Neither family is downstream of the other.
-   **System-wide standard changes**: When a cross-cutting standard changes (such as a new security pattern or a new required step in the API endpoint workflow), all affected steering files must be updated. The root `CLAUDE.md` Rule 11 ("Keep CLAUDE.md Files Updated") provides a mapping of change types to the files that must be updated.
-   **Claude Code slash commands**: The commands in `.claude/commands/` restate steering-document rules, checklists, and file paths in order to scaffold work. When a steering document changes a rule, pattern, or path that a command references, update the affected command in the same change — root `CLAUDE.md` Rule 12 maps each command to the steering content it depends on.

### CLAUDE.md to Kiro Steering Mapping

| `CLAUDE.md` file          | Corresponding Kiro steering document(s)                                                         |
| :------------------------ | :---------------------------------------------------------------------------------------------- |
| `CLAUDE.md` (root)        | The workflow document(s) for the changed area; cross-cutting rules go in all affected documents |
| `web/CLAUDE.md`           | `WEB_DEVELOPMENT_WORKFLOW.md`, `WEB_FRONTEND.md`                                                |
| `backend/CLAUDE.md`       | `BACKEND_CDK_DEVELOPMENT_WORKFLOW.md`                                                           |
| `infra/CLAUDE.md`         | `CDK_DEVELOPMENT_WORKFLOW.md`, `BACKEND_CDK_DEVELOPMENT_WORKFLOW.md`                            |
| `tools/VamsCLI/CLAUDE.md` | `CLI_DEVELOPMENT_WORKFLOW.md`                                                                   |
| `tools/VamsMCP/CLAUDE.md` | `CLI_DEVELOPMENT_WORKFLOW.md` (MCP propagation section)                                         |
| `documentation/CLAUDE.md` | `DOCUMENTATION_WORKFLOW.md`                                                                     |

Because the MCP server is downstream of the CLI, the CLI and MCP steering documents share a single Kiro workflow document rather than each having their own. A change to the CLI-to-MCP propagation rules therefore has four destinations, listed in [Keeping the CLI, MCP Server, and Skill Aligned](#keeping-the-cli-mcp-server-and-skill-aligned).

The agent skill (`tools/VamsAgentSkill/SKILL.md`) sits outside both families. It is a runtime operating instruction rather than a development steering document, and it is agent-agnostic — the Claude Code `/vams-agent` command is a thin entry point that loads the same file. Update the skill directly; there is no Kiro mirror to keep in sync.

## Adding New Steering Documents

Add a new steering document when:

-   A new major component is added to the project (such as a new backend service or a new frontend application)
-   An existing component grows complex enough to warrant dedicated workflow guidance
-   A cross-component workflow emerges that is not covered by existing documents

### Structure

Follow the established pattern for new steering documents:

1. **Architecture Overview**: Directory structure, key files, technology stack
2. **Development Workflow Checklist**: Phased checklist covering pre-implementation, implementation, testing, and documentation
3. **Mandatory Rules**: Numbered rules with correct and incorrect code examples
4. **Templates**: Complete code templates for common tasks (handler skeleton, model skeleton, test skeleton)

### File Placement

Create the workflow document in `.kiro/steering/\{WORKFLOW_NAME\}.md`.

For component-level steering, create a `CLAUDE.md` file in the component's root directory. Reference the root `CLAUDE.md` for the standard sections and conventions to include. If the component warrants a mirrored Kiro front-end-style steering file, copy the `CLAUDE.md` content into `.kiro/steering/` and keep the two in sync.

A new component needs coverage in **both** steering families, so complete all of the following in the same change:

1. Create the component `CLAUDE.md`, and add it to the `CLAUDE.md` files table above.
2. Create or extend the matching `.kiro/steering/` workflow document, and add it to the Workflow Documents table above.
3. Add the pair to the [CLAUDE.md to Kiro Steering Mapping](#claudemd-to-kiro-steering-mapping) table, so future changes to either one have a defined counterpart. A component that shares an existing workflow document (as the MCP server shares `CLI_DEVELOPMENT_WORKFLOW.md`) still needs its own mapping row.
4. Add the component's directory to the root `CLAUDE.md` directory tree and the Rule 11 change-area table.

Leaving out the mapping row is the failure mode worth guarding against: the two documents exist, but nothing records that they describe the same standard, so they drift apart silently.
