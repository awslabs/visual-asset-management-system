# Agentic Development

:::warning[Use at Your Own Risk]
AI-assisted coding tools are used at your own risk. Configure agent permissions according to your organizational security standards. All AI-generated code, configuration, and infrastructure changes must be reviewed and validated by qualified personnel before deploying to any production environment.
:::

VAMS supports AI-assisted development through a layered system of steering documents that guide AI coding agents to follow project conventions, architecture patterns, and quality standards. These documents ensure that AI agents produce code consistent with VAMS patterns regardless of which developer or agent is performing the work.

Three AI coding agents are supported: Claude Code, Cline, and Kiro. Each reads from dedicated steering file locations, but the underlying guidance is consistent across all agents.

## Supported Agents

| Agent       | Steering Location                       | Description                                                                                                                                |
| ----------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Claude Code | `CLAUDE.md` files + `.claude/commands/` | Component-level steering documents placed in each major directory (auto-loaded), plus reusable slash commands for common multi-step tasks. |
| Cline       | `.clinerules/workflows/`                | Workflow-based development guides with checklists, templates, and mandatory rules.                                                         |
| Kiro        | `.kiro/steering/`                       | Workflow-based development guides mirrored from `.clinerules/workflows/` for Kiro compatibility.                                           |

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

The `.kiro/steering/` and `.clinerules/workflows/` directories contain detailed development workflow guides. These documents provide step-by-step checklists, code templates, and mandatory rules for complex multi-file tasks such as adding a new backend API endpoint or building a new AWS CDK nested stack.

## Available Steering Documents

### CLAUDE.md Files

| File                      | Scope                  | Key Topics                                                                                                  |
| ------------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| `CLAUDE.md`               | Project-wide           | Architecture overview, cross-component patterns, critical rules, gold standard references, deployment modes |
| `web/CLAUDE.md`           | React frontend         | Cloudscape components, HashRouter, Synonyms system, service-layer pattern, viewer plugins, feature switches |
| `backend/CLAUDE.md`       | Python Lambda backend  | Pydantic v1 models, Casbin authorization, DynamoDB patterns, Lambda handler structure, logging and testing  |
| `infra/CLAUDE.md`         | AWS CDK infrastructure | Nested stacks, Lambda builders, security helpers, configuration system, multi-partition support             |
| `tools/VamsCLI/CLAUDE.md` | Python CLI tool        | Click framework, profile management, command groups, constants pattern, JSON output mode                    |
| `documentation/CLAUDE.md` | Documentation site     | Docusaurus conventions, writing style, sidebar configuration, cross-reference sources                       |

### Workflow Documents

The following workflow documents exist in both `.kiro/steering/` and `.clinerules/workflows/`. The content is identical across both locations.

| File                                  | Scope              | Key Topics                                                                                                                             |
| ------------------------------------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `BACKEND_CDK_DEVELOPMENT_WORKFLOW.md` | Backend + CDK      | End-to-end API endpoint development: Pydantic models, Lambda handlers, CDK Lambda builders, API Gateway routes, security helpers       |
| `CDK_DEVELOPMENT_WORKFLOW.md`         | CDK infrastructure | Nested stack patterns, configuration management, feature switches, Lambda builder templates, security compliance, pipeline development |
| `CLI_DEVELOPMENT_WORKFLOW.md`         | CLI tool           | Click command structure, profile support, constants pattern, error handling, JSON output, testing                                      |
| `WEB_DEVELOPMENT_WORKFLOW.md`         | React frontend     | Service-layer pattern, Cloudscape imports, HashRouter, Synonyms, lazy loading, Context + useReducer, theme system, viewer plugins      |
| `DOCUMENTATION_WORKFLOW.md`           | Documentation site | Docusaurus conventions, admonition syntax, sidebar updates, writing style, cross-references, build commands                            |

## How Steering Documents Guide Development

The steering documents enforce consistent patterns across the codebase. The following examples illustrate the type of guidance they provide.

### Example 1: Cross-Component API Endpoint Development

Adding a new API endpoint in VAMS requires coordinated changes across five to six files. The root `CLAUDE.md` defines this pattern explicitly:

| Step | File                                                         | Action                                                           |
| ---- | ------------------------------------------------------------ | ---------------------------------------------------------------- |
| 1    | `backend/backend/handlers/\{domain\}/\{handler\}.py`         | Implement Lambda handler with Casbin enforcement                 |
| 2    | `backend/backend/models/\{domain\}.py`                       | Define request/response models (Pydantic v1)                     |
| 3    | `infra/lib/lambdaBuilder/\{domain\}Functions.ts`             | Build Lambda with environment variables, permissions, VPC config |
| 4    | `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts` | Attach Lambda to API Gateway route                               |
| 5    | `web/src/services/APIService.ts`                             | Add API call method                                              |
| 6    | `tools/VamsCLI/vamscli/commands/\{group\}.py`                | Add CLI command (if applicable)                                  |

Without steering documents, an AI agent might create a handler without the corresponding API Gateway route (resulting in dead code) or add a route without a handler (resulting in 500 errors).

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

The CDK workflow documents mandate that every Lambda builder function includes four security-related calls. Omitting any of these calls results in deployment failures (CDK Nag violations) or security gaps.

```typescript
// Every Lambda builder must include these four calls:

// 1. KMS key permissions for encryption/decryption
kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

// 2. Global environment variables and permissions
globalLambdaEnvironmentsAndPermissions(fun, config);

// 3. CDK Nag suppression for S3 grant-based permissions
suppressCdkNagErrorsByGrantReadWrite(scope);

// 4. Domain-specific DynamoDB table grants
storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
```

These patterns are documented with complete code templates in both `CDK_DEVELOPMENT_WORKFLOW.md` and `BACKEND_CDK_DEVELOPMENT_WORKFLOW.md`, ensuring that AI agents produce compliant Lambda builders on the first attempt.

## Claude Code Slash Commands

In addition to steering documents, Claude Code supports **slash commands** — reusable skill prompts stored in `.claude/commands/`. These commands automate common multi-step development tasks and can be invoked from the Claude Code CLI with `/<command-name>`.

| Command                  | File                                        | Description                                                                                                                 |
| :----------------------- | :------------------------------------------ | :-------------------------------------------------------------------------------------------------------------------------- |
| `/add-api-endpoint`      | `.claude/commands/add-api-endpoint.md`      | Scaffold a new backend API endpoint across all required files (handler, model, Lambda builder, API route, frontend service) |
| `/add-pipeline`          | `.claude/commands/add-pipeline.md`          | Scaffold a new processing pipeline with container, Lambda, CDK stack, and configuration                                     |
| `/deploy-check`          | `.claude/commands/deploy-check.md`          | Run pre-deployment validation checklist (config, CDK synth, lint, security)                                                 |
| `/generate-permissions`  | `.claude/commands/generate-permissions.md`  | Generate VAMS permission constraint JSON templates                                                                          |
| `/refresh-steering-docs` | `.claude/commands/refresh-steering-docs.md` | Update CLAUDE.md directory structures and key file references                                                               |
| `/update-changelog`      | `.claude/commands/update-changelog.md`      | Generate changelog entries from git commits                                                                                 |
| `/update-docs`           | `.claude/commands/update-docs.md`           | Update Docusaurus documentation pages based on recent code changes                                                          |
| `/verify-docs`           | `.claude/commands/verify-docs.md`           | Cross-check documentation accuracy against source code                                                                      |

These commands encode the cross-component patterns from the steering documents into executable workflows. For example, `/add-api-endpoint` automates the six-file change pattern described in Example 1 above.

## Keeping Steering Documents in Sync

:::warning[Synchronization Requirement]
The `.kiro/steering/` and `.clinerules/workflows/` directories must contain identical content. When updating a workflow document, apply the same change to both locations.
:::

The synchronization rules are as follows:

-   **`.kiro/steering/` and `.clinerules/workflows/`**: These directories are committed to version control and shared across all developers. Changes to workflow documents must be applied to both locations simultaneously.
-   **`CLAUDE.md` files**: These files are gitignored and maintained locally by each developer. They are not committed to the repository. Each developer may customize these files for their environment, but the canonical content is defined by project convention.
-   **System-wide standard changes**: When a cross-cutting standard changes (such as a new security pattern or a new required step in the API endpoint workflow), all affected steering files must be updated. The root `CLAUDE.md` Rule 11 ("Keep CLAUDE.md Files Updated") provides a mapping of change types to the files that must be updated.

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

Create the workflow document in both locations:

-   `.kiro/steering/\{WORKFLOW_NAME\}.md`
-   `.clinerules/workflows/\{WORKFLOW_NAME\}.md`

For component-level steering, create a `CLAUDE.md` file in the component's root directory. Reference the root `CLAUDE.md` for the standard sections and conventions to include.
