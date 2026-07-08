# VAMS - Visual Asset Management System

This is the root-level Claude Code steering document for VAMS. It is auto-loaded in every session and provides the project-wide context that all agents need. For component-specific details, see the `CLAUDE.md` files in each subdirectory.

## 🏗️ **Project Overview**

VAMS is an AWS-native Visual Asset Management System for managing, visualizing, and processing 3D assets, point clouds, CAD files, and other visual content. It deploys as a CloudFormation/CDK stack with:

-   **React frontend** (`web/`) — Cloudscape UI, many viewer plugins for 3D/media
-   **Python Lambda backend** (`backend/`) — Casbin ABAC/RBAC auth, DynamoDB, S3
-   **CDK TypeScript infrastructure** (`infra/`) — 11 nested stacks, multi-partition support
-   **Python CLI tool** (`tools/VamsCLI/`) — Click framework, profile-based config
-   **Processing pipelines** (`backendPipelines/`) — 3D conversion, GenAI labeling, Gaussian splatting, point cloud, 3D preview thumbnails, NVIDIA Cosmos Predict

### **Version Info**

VAMS version: see `infra/config/config.ts` and `tools/VamsCLI/vamscli/version.py`. Python 3.12 (Lambda), 3.13+ (dev). Node 20.x (Lambda). React 17.0.2 (Vite build). Pydantic **1.10.13 (v1, NOT v2)** — uses `@root_validator`, `@validator`, `class Config`. CDK: `aws-cdk-lib`.

---

## 📁 **Directory Structure**

> **Maintenance note:** Update this tree when adding or removing top-level directories, components, or tools. See Rule 11.

```
root/
├── CLAUDE.md                  # THIS FILE - project-wide guide
├── web/                       # React frontend (Cloudscape, TypeScript, Vite)
│   └── CLAUDE.md              # Frontend development guide
├── backend/                   # Python Lambda handlers
│   └── CLAUDE.md              # Backend development guide
├── infra/                     # CDK TypeScript infrastructure
│   └── CLAUDE.md              # CDK development guide
├── tools/
│   └── VamsCLI/               # Python CLI tool
│       └── CLAUDE.md          # CLI development guide
├── backendPipelines/          # Processing pipeline definitions (containers + Lambdas)
│   ├── CLAUDE.md              # Pipeline development guide (S3 output paths, assetId threading, new-pipeline checklist)
│   ├── genAi/
│   │   ├── cosmos/predict/    # NVIDIA Cosmos Predict (Text2World, Video2World)
│   │   └── metadata3dLabeling/
│   ├── conversion/, preview/, 3dRecon/, simulation/, multi/
├── documentation/             # User guides, API spec, permission templates
├── .kiro/steering/            # Detailed workflow docs (Kiro steering, supplementary)
├── .claude/commands/          # Claude Code skills (slash commands)
└── infra/deploymentDataMigration/  # Data migration scripts (e.g., v2.4_to_v2.5)
```

---

## 🏛️ **Architecture Summary**

### **Request Flow**

```
User → CloudFront/ALB → API Gateway REST API (v1)
  → Custom Lambda Authorizer (JWT validation + IP check)
    → Lambda Handler (Casbin two-tier enforcement)
      → DynamoDB / S3
```

### **Auth Flow**

```
Cognito/External OAuth → ID Token → Custom Lambda Authorizer
  → Tier 1: API route authorization (can user call this endpoint?)
  → Tier 2: Data entity authorization (can user access this specific resource?)
  → Both tiers MUST allow for access to succeed
```

### **Frontend Architecture**

```
React 17 + Cloudscape → HashRouter → apiClient (fetch-based)
  → Feature switches from /api/secure-config → conditional UI rendering
```

### **Configuration Flow**

```
CDK config (infra/config/config.json)
  → deploys to DynamoDB (feature switches, app settings)
    → Frontend reads from /api/secure-config at runtime
```

### **Pipeline Architecture**

Three execution types: **Lambda** (sync/async invoke), **SQS** (async queue), **EventBridge** (async event). SQS and EventBridge are async-only with optional Step Functions Task Token callback.

```
S3 event / API trigger → Lambda → Step Functions → Lambda / SQS / EventBridge → AWS Batch containers (optional)
  backendPipelines/{useCase}/lambda/    -- orchestration
  backendPipelines/{useCase}/container/ -- processing
```

See `backendPipelines/CLAUDE.md` for output path conventions, `assetId` threading, and the new-pipeline checklist.

### **Deployment Modes**

| Mode           | Distribution             | Notes                                              |
| -------------- | ------------------------ | -------------------------------------------------- |
| Commercial AWS | CloudFront + S3          | Default                                            |
| GovCloud       | ALB + S3                 | No CloudFront, no Location Service, FIPS endpoints |
| Air-gapped     | ALB + S3 + VPC endpoints | Full VPC isolation                                 |

---

## 🔑 **Cross-Component Patterns**

These are the critical patterns that span multiple directories. **Every developer must understand these.**

### **Pattern 1: Adding a New API Endpoint (multiple files)**

Adding a new API endpoint requires coordinated changes across multiple components:

| Step                      | File                                                          | What to do                                                                                                       |
| ------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 1. Master route (backend) | `backend/backend/common/apiRoutes.py`                         | Define the `ApiRoute` constant AND add it to the appropriate category group array                                |
| 2. Backend handler        | `backend/backend/handlers/{domain}/{handler}.py`              | Implement Lambda handler with Casbin enforcement; dispatch via `ApiRoute.matches()`                              |
| 3. Pydantic model         | `backend/backend/models/{domain}.py`                          | Define request/response models (Pydantic **v1**)                                                                 |
| 4. Lambda builder         | `infra/lib/lambdaBuilder/{domain}Functions.ts`                | Build Lambda with env vars, permissions, VPC config                                                              |
| 5. API route              | `infra/lib/nestedStacks/apiLambda/apiBuilder2-nestedStack.ts` | Attach Lambda to API Gateway route (prefer `apiBuilder2`; `apiBuilder` is near the CFN per-stack resource limit) |
| 6. Frontend service       | `web/src/services/APIService.ts`                              | Add API call method                                                                                              |
| 7. CLI command            | `tools/VamsCLI/vamscli/commands/{group}.py`                   | Add CLI command (if applicable)                                                                                  |
| 8. OpenAPI spec (docs)    | `documentation/VAMS_API.yaml`                                 | Add/update the path and its component schemas                                                                    |
| 9. API reference (docs)   | `documentation/docusaurus-site/docs/api/{domain}.md`          | Add/update the human-readable endpoint reference (e.g. `api/auth.md` for `/auth/*`)                              |

**Never** add an endpoint without updating all required files. A handler without a route is dead code; a route without a handler will 500. The route group arrays in `apiRoutes.py` feed handler dispatch and the `GET /auth/routes/api` listing, so a missing entry is invisible to constraint authoring and the CLI. **API documentation lives in two places — the OpenAPI `VAMS_API.yaml` AND the Docusaurus `api/{domain}.md` reference page — and both must be updated together.**

### **Pattern 2: Two-Tier Authorization**

Authorization is enforced at two tiers everywhere in VAMS:

-   **Tier 1 (API-level)**: Controls which API routes a role can access. Defined via `api` and `web` objectType constraints.
-   **Tier 2 (Object-level)**: Controls which data entities a role can access. Defined via entity-type constraints (`database`, `asset`, `pipeline`, etc.).

**Both tiers must allow for access to succeed.** This is defense-in-depth. The backend enforces via `CasbinEnforcer`, and the frontend gates UI routes via the `webRoutes()` API.

```python
# ✅ CORRECT - Backend handler with Casbin enforcement
enforcer = CasbinEnforcer(user_id, role_constraints)
if not enforcer.check_permission(object_type, resource_id, action):
    return {"statusCode": 403, "body": json.dumps({"error": "Forbidden"})}
```

**System user:** The reserved user ID `SYSTEM_USER` is the official identity for all system-process actions (lambda cross-calls, pipeline workflow executions, bucket-sync ingestion, seeded `createdBy`/`modifiedBy` values, `changeUserId` provenance fallbacks). It is seeded into the user and user-roles tables during CDK deployment and assigned to the `admin` role so system actions pass both authorization tiers. Never introduce other variants (`SYSTEM`, `system`, etc.) — handlers compare against the exact string. See `backend/CLAUDE.md`.

### **Pattern 3: Configuration Flows CDK -> DynamoDB -> Frontend**

CDK config (`infra/config/config.json`) drives deployment decisions; a CDK custom resource writes feature switches to DynamoDB at deploy time; the frontend reads features from `/api/secure-config` at runtime. Feature switches are defined in `infra/common/vamsAppFeatures.ts`.

```typescript
// CDK: feature switch enum + push to DynamoDB in core stack
export enum VAMS_APP_FEATURES {
    GOVCLOUD = "GOVCLOUD",
    LOCATIONSERVICES = "LOCATIONSERVICES",
    NEW_FEATURE = "NEW_FEATURE",
}
if (props.config.app.newFeature.enabled) {
    this.enabledFeatures.push(VAMS_APP_FEATURES.NEW_FEATURE);
}
```

```javascript
// Frontend reads at runtime
const config = appCache.getItem("config");
if (config.featuresEnabled.includes("NEW_FEATURE")) {
    // Show feature-specific UI
}
```

### **Pattern 4: Resource Names Resolve via SSM Parameter Store**

DynamoDB table names, non-asset S3 bucket names, and audit log group names are **never** hardcoded. Non-pipeline backend Lambdas resolve these from SSM Parameter Store at runtime, with environment variable overrides for development and testing.

```python
# ✅ CORRECT - Resolve at module level
from backend.common.resourceNames import get_table_name, ResourceKeys

try:
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    database_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
except Exception:
    logger.exception("Failed loading resource names")
    raise

# ❌ INCORRECT
ASSET_STORAGE_TABLE_NAME = "vams-asset-storage"  # VIOLATION
```

CDK publishes each resource name as an SSM parameter under `VAMS_RESOURCE_PARAM_PREFIX` (see `ResourceNamesBuilder` nested stack; Lambda env is set via `globalLambdaEnvironmentsAndPermissions`). Keys are defined in `infra/common/resourceParamKeys.ts` (`RESOURCE_PARAM_KEYS.*`) and mirrored in `backend/backend/common/resourceNames.py` (`ResourceKeys`).

**Resolution order:** environment variable override (break-glass for testing; also how pytest and local utilities work) → 60-minute in-module cache → one paginated `GetParametersByPath` fetch of all resource names. Pipeline Lambdas use legacy environment variables (excluded from SSM resolution).

### **Pattern 5: Multi-Partition Support**

VAMS runs on commercial AWS, GovCloud, and potentially ISO partitions. **Never hardcode AWS partition strings, service endpoints, or regional URLs.**

```typescript
// ✅ CORRECT - Use service-helper for partition-aware values
import { Service } from "../helper/service-helper";
const cognitoEndpoint = Service("COGNITO_IDP").Endpoint;

// ❌ INCORRECT - Hardcoded partition
const arn = `arn:aws:s3:::my-bucket`; // VIOLATION - breaks in GovCloud (arn:aws-us-gov)
```

### **Pattern 6: GovCloud Constraints**

When `config.app.govCloud.enabled` is true: no CloudFront (use ALB for static web distribution); no Location Service (conditionally exclude); FIPS endpoints required (use service-helper); certain VPC endpoints are conditional (check partition before creating); no `unsafe-eval` (stricter CSP unless explicitly overridden).

---

## 🚨 **Critical Rules**

These rules apply project-wide. Violations will cause deployment failures, security issues, or runtime errors.

### **Rule 1: Never Use Pydantic v2 Syntax**

The backend uses Pydantic **1.10.13**. v2 syntax fails at import time in Lambda.

```python
# ✅ v1
from pydantic import BaseModel, Field, root_validator, validator

class AssetRequest(BaseModel):
    assetName: str = Field(..., description="Name of the asset")

    @root_validator
    def validate_fields(cls, values):
        return values

    class Config:
        extra = "forbid"

# ❌ v2 (will fail)
from pydantic import model_validator             # not in v1
model_config = ConfigDict(extra="forbid")        # v2 syntax, VIOLATION
```

### **Rule 2: Never Hardcode Table Names or ARNs**

All AWS resource references come from environment variables (Lambda) or CDK constructs (infra). Hardcoding causes failures when stack names change or when deploying to different accounts.

### **Rule 3: Always Validate Configuration in CDK**

Every new configuration option in `config.ts` must include validation in the `getConfig()` function. Unvalidated config leads to silent deployment failures.

```typescript
// ✅ CORRECT - Validate in getConfig()
if (config.app.newFeature.enabled && !config.app.newFeature.requiredSetting) {
    throw new Error("Configuration Error: newFeature requires requiredSetting when enabled");
}
```

### **Rule 4: CDK Nag Suppressions Must Be Justified**

Every CDK Nag suppression requires a detailed reason explaining **why** it is acceptable in the VAMS context — e.g. "Wildcard permissions required for dynamic S3 object access within VAMS asset buckets. Scope is limited to deployment-specific buckets." A `reason: "Suppressed"` placeholder is a violation.

### **Rule 5: Use Custom Lambda Authorizer, Not Built-In**

VAMS uses a custom Lambda authorizer for all API Gateway endpoints. Never use built-in CDK authorizers (like `HttpUserPoolAuthorizer`).

### **Rule 6: Feature Switches Must Be Defined**

New features must have a feature switch in `vamsAppFeatures.ts` and be gated by config in the core stack. Never deploy features unconditionally.

### **Rule 7: All CLI API Endpoints in Constants**

CLI API endpoint paths must be defined in `tools/VamsCLI/vamscli/constants.py`. Never hardcode endpoint paths in command files or API client methods.

### **Rule 8: KMS Encryption for All Storage Resources**

All DynamoDB tables, S3 buckets, and other storage resources must use KMS encryption from the shared `storageResources.encryption.kmsKey`.

### **Rule 9: Explicit Stack Dependencies in CDK**

Always use `nestedStack.addDependency(otherStack)` when one nested stack depends on another. Implicit ordering through resource references alone is not sufficient.

### **Rule 10: Frontend Uses HashRouter**

The React app uses `HashRouter`, not `BrowserRouter`. All internal routes use hash-based navigation (`/#/path`). This is required for CloudFront/ALB compatibility where all paths serve the same `index.html`.

### **Rule 11: Keep CLAUDE.md Files Updated**

Structural changes to the codebase require updating the relevant `CLAUDE.md` file(s): adding/removing handler domains, components, commands, nested stacks, or pipeline directories; new DynamoDB tables, S3 buckets, or environment variables; new API routes or CLI commands; new/removed viewer plugins; configuration-system changes (new fields, feature switches); or new dependencies that affect development patterns.

| Change area                                                    | Update this file                                                                                                                                                                                                                                                                                                                                                   |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| New backend handler/model domain                               | `backend/CLAUDE.md` (directory structure, handler list)                                                                                                                                                                                                                                                                                                            |
| New CDK nested stack or lambda builder                         | `infra/CLAUDE.md` (directory structure, stack list)                                                                                                                                                                                                                                                                                                                |
| New frontend component/page/service                            | `web/CLAUDE.md` (directory structure, key files)                                                                                                                                                                                                                                                                                                                   |
| New CLI command group                                          | `tools/VamsCLI/CLAUDE.md` (command list, directory structure)                                                                                                                                                                                                                                                                                                      |
| Configuration system (new field, switch, changed default)      | `infra/CLAUDE.md`, `documentation/docusaurus-site/docs/deployment/configuration-reference.md`, and the **ConfigBuilder** component; then run `infra/test/configBuilderSync.test.ts`                                                                                                                                                                                |
| New/changed S3 bucket, DynamoDB table, or CloudWatch log group | `documentation/docusaurus-site/docs/architecture/aws-resources.md` and `documentation/docusaurus-site/docs/deployment/uninstall.md` — record removal policy (RETAIN vs DESTROY) and whether the resource has a custom/explicit name (custom-named resources can collide on redeploy). See `infra/CLAUDE.md` "Documentation Rule: Storage Resources and Log Groups" |
| New pipeline                                                   | `backendPipelines/CLAUDE.md`, root `CLAUDE.md` (pipeline list), `documentation/docusaurus-site/docs/deployment/configuration-reference.md`                                                                                                                                                                                                                         |
| Cross-component pattern change                                 | `CLAUDE.md` root (cross-component patterns section)                                                                                                                                                                                                                                                                                                                |
| New skill                                                      | `CLAUDE.md` root (Available Claude Code Skills table)                                                                                                                                                                                                                                                                                                              |

Update the directory structure tree, key files tables, and any affected rules or patterns; keep descriptions concise. Run `/refresh-steering-docs` for a comprehensive update.

**Keep Kiro steering in sync (bidirectional).** The `.kiro/steering/` documents mirror the `CLAUDE.md` guidance for the Kiro agent. A change to any `CLAUDE.md` rule, pattern, or convention must land in the corresponding Kiro steering document in the same commit — and vice versa. Mapping:

| CLAUDE.md file            | Corresponding Kiro steering document(s)                                                            |
| ------------------------- | -------------------------------------------------------------------------------------------------- |
| `CLAUDE.md` (root)        | The relevant workflow doc(s) for the changed area; cross-cutting rules go in all affected docs     |
| `infra/CLAUDE.md`         | `.kiro/steering/CDK_DEVELOPMENT_WORKFLOW.md`, `.kiro/steering/BACKEND_CDK_DEVELOPMENT_WORKFLOW.md` |
| `backend/CLAUDE.md`       | `.kiro/steering/BACKEND_CDK_DEVELOPMENT_WORKFLOW.md`                                               |
| `web/CLAUDE.md`           | `.kiro/steering/WEB_DEVELOPMENT_WORKFLOW.md`, `.kiro/steering/WEB_FRONTEND.md`                     |
| `tools/VamsCLI/CLAUDE.md` | `.kiro/steering/CLI_DEVELOPMENT_WORKFLOW.md`                                                       |
| `documentation/CLAUDE.md` | `.kiro/steering/DOCUMENTATION_WORKFLOW.md`                                                         |

### **Rule 12: Keep Claude Code Skills in Sync with Steering Documents**

The skills in `.claude/commands/` scaffold work by restating steering-document rules, patterns, checklists, and file paths. When a steering document changes a rule, pattern, workflow, or file path that a skill references, update the affected skill(s) in the same change — and vice versa. A stale skill actively scaffolds outdated code.

**Which skills depend on which steering content:**

| Skill                          | Sensitive to changes in                                                                                                                       |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `/add-api-endpoint`            | Root Pattern 1, `backend/CLAUDE.md` (handler/model patterns, apiRoutes), `infra/CLAUDE.md` (lambda builder, route registry, security helpers) |
| `/add-pipeline`                | `backendPipelines/CLAUDE.md` (output paths, assetId threading, lambda dirs), `infra/CLAUDE.md` (pipeline stack, VPC builder blocks, config)   |
| `/generate-permissions`        | Permission model docs, `documentation/permissionsTemplates/`, constraint fields in `backend/CLAUDE.md`                                        |
| `/deploy-check`                | Root development commands (lint/prettier from repo root), config validation rules                                                             |
| `/update-docs`, `/verify-docs` | `documentation/CLAUDE.md` (writing style, source-to-doc mappings, dual API doc sources)                                                       |
| `/update-changelog`            | Root git workflow / changelog format                                                                                                          |
| `/refresh-steering-docs`       | Root Rules 11–12                                                                                                                              |

---

## 🧰 **Development Commands**

```bash
# Frontend (web/)
cd web && npm install
cd web && npm run start                                    # Dev server
cd web && npm run build                                    # Production build

# Backend (backend/)
cd backend && python -m pytest                             # All tests
cd backend && python -m pytest tests/handlers/assets/ -v   # Specific handler tests

# CDK (infra/)
cd infra && npm install
cd infra && npx cdk synth                                  # Synthesize CloudFormation
cd infra && npx cdk diff                                   # Preview changes
cd infra && npx cdk deploy --all --require-approval never  # Deploy to dev

# CLI (tools/VamsCLI/)
cd tools/VamsCLI && pip install -e .                       # Install in dev mode
cd tools/VamsCLI && python -m pytest                       # Run CLI tests

# Project-wide (run from repo root — targets web/src, infra/lib, infra/bin, infra/test)
npm run lint
npm run lint-fix
npm run prettier-check
npm run prettier-fix
```

> **Always run lint and prettier from the project root directory** — the root `package.json` scripts target the correct paths. Do not run these from subdirectories.

---

## 📐 **Gold Standard Reference Files**

When implementing new features, follow the patterns in these files:

| Component           | Reference File                                               | What It Demonstrates                                           |
| ------------------- | ------------------------------------------------------------ | -------------------------------------------------------------- |
| Backend handler     | `backend/backend/handlers/assets/assetService.py`            | Lambda structure, Casbin auth, error handling, DynamoDB ops    |
| Pydantic model      | `backend/backend/models/assetsV3.py`                         | Request/response models, v1 validators, Field definitions      |
| Lambda builder      | `infra/lib/lambdaBuilder/assetFunctions.ts`                  | Env vars, permissions, VPC config, KMS, CDK Nag                |
| CDK nested stack    | `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts` | Route attachment, function integration, API Gateway setup      |
| CLI command         | `tools/VamsCLI/vamscli/commands/roleUserConstraints.py`      | Click decorators, profile support, JSON output, error handling |
| API service         | `web/src/services/APIService.ts`                             | apiClient calls, request/response patterns                     |
| Pipeline model      | `backend/backend/models/pipelines.py`                        | Pipeline Pydantic models, execution type enum, validation      |
| Workflow model      | `backend/backend/models/workflows.py`                        | Workflow Pydantic models, Step Functions ASL generation        |
| Permission template | `documentation/permissionsTemplates/database-admin.json`     | Two-tier constraint structure, variable placeholders           |

---

## 🔀 **Git Workflow**

-   **Branch naming**: `release/X.Y.Z` for releases, `feature/description` for features. Main branch is `main`.
-   **Current branch**: `release/2.X.0` based on version in `infra/config/config.ts` and `tools/VamsCLI/vamscli/version.py`
-   **Changelog format**: `standard-version` format in `CHANGELOG.md`
-   **Commit style**: descriptive imperative mood ("Fix bugs", "Add cognito user management")

---

## 🔌 **Available Claude Code Skills**

| Skill                    | Description                                                   |
| ------------------------ | ------------------------------------------------------------- |
| `/generate-permissions`  | Generate VAMS permission constraint JSON templates            |
| `/add-api-endpoint`      | Scaffold a new backend API endpoint across all required files |
| `/add-pipeline`          | Scaffold a new processing pipeline                            |
| `/update-changelog`      | Generate changelog entries from git commits                   |
| `/deploy-check`          | Pre-deployment validation checklist                           |
| `/refresh-steering-docs` | Update CLAUDE.md directory structures and key file references |
| `/update-docs`           | Update documentation pages based on recent code changes       |
| `/verify-docs`           | Cross-check documentation accuracy against source code        |

---

## 📚 **Supplementary Documentation**

Deep-dive workflow guides in `.kiro/steering/`:

-   `CDK_DEVELOPMENT_WORKFLOW.md` — CDK nested stacks, constructs, lambda builders, security patterns, pipeline development
-   `BACKEND_CDK_DEVELOPMENT_WORKFLOW.md` — End-to-end API endpoint development across backend + CDK
-   `CLI_DEVELOPMENT_WORKFLOW.md` — CLI commands, decorators, testing, profile support, JSON output

User-facing docs:

-   `documentation/docusaurus-site/docs/concepts/permissions-model.md` — permissions concepts and ABAC/RBAC configuration
-   `documentation/docusaurus-site/docs/deployment/configuration-reference.md` — CDK deployment configuration reference
-   `documentation/docusaurus-site/docs/developer/setup.md` — development environment setup and patterns
-   `documentation/VAMS_API.yaml` — OpenAPI specification for all endpoints

---

## 🔧 **Technology Stack Quick Reference**

Runtime versions are in the Project Overview version table.

-   **Frontend (`web/`)**: Cloudscape Design System, AWS Amplify v6 (auth), custom fetch-based `apiClient` (auto auth headers), HashRouter, TypeScript throughout (`__mocks__/*.js` remain JS). Viewer plugins: Three.js, Needle Engine, Potree, Gaussian Splat, GLTF, USD, IFC/BIM.
-   **Backend (`backend/`)**: Casbin ABAC/RBAC, boto3, AWS Lambda Powertools (logging, tracing). Pydantic v1 only.
-   **Infrastructure (`infra/`)**: AWS CDK (TypeScript), 11 nested stacks, CDK Nag security checks, REST API (v1), custom Lambda authorizer (unified JWT + IP).
-   **CLI (`tools/VamsCLI/`)**: Click command framework, profile-based multi-environment config, `--json-output` for machine-readable output.

---

## ⚙️ **Environment & Deployment Specifics**

### **Environment Variables (Backend)**

Non-pipeline handlers receive `VAMS_RESOURCE_PARAM_PREFIX` (SSM prefix, e.g. `/{config.name}-{baseStackName}/resourceNames`) and resolve DynamoDB table names, auxiliary/artefacts bucket names, and audit log group names from SSM using `get_table_name`, `get_bucket_name`, `get_log_group_name` in `backend.common.resourceNames` (60-minute in-module cache; legacy env vars provide a break-glass path). **Pipeline Lambdas** in `backendPipelines/` are excluded from SSM resolution and continue to use legacy table-name env vars.

All handlers additionally receive `AWS_REGION` (set by the Lambda runtime) and `PRESIGNED_URL_TIMEOUT_SECONDS` (S3 presigned URL TTL). The API Gateway authorizer Lambda also receives `COGNITO_AUTH_ENABLED` — the authorizer resolves the user's MFA status and passes it to handlers as the `vams:mfaEnabled` authorizer context value, so handler Lambdas make no Cognito calls. Domain-specific handlers receive additional env vars for their resources (e.g. `SEND_EMAIL_FUNCTION_NAME` for notification handlers).

### **DynamoDB Access Pattern**

VAMS uses single-table design with composite keys. Common patterns:

-   **PK**: Entity type + ID (e.g., `ASSET#uuid`)
-   **SK**: Sort key for queries (e.g., `VERSION#v1`)
-   **GSI**: Global secondary indexes for cross-entity queries

### **S3 Bucket Organization**

-   **Asset buckets**: One per database, auto-created, KMS encrypted
-   **Auxiliary bucket**: Staging, thumbnails, temp files
-   **Web bucket**: Built frontend static assets

---

## 🛡️ **Security Considerations**

-   **S3 TLS enforced** — bucket policy denies `aws:SecureTransport=false`
-   **KMS encryption everywhere** — DynamoDB, S3, SNS all use the shared KMS key
-   **IAM least privilege** — Lambda roles get only the permissions they need
-   **CSP headers** — dynamically generated from config
-   **IP range restrictions** — optional, via the custom authorizer
-   **No secrets in code** — use SSM parameters or Secrets Manager
-   **CDK Nag enforcement** — all stacks checked against AWS Solutions rules

---

## 🔄 **Common Cross-Component Workflows**

### **Adding a New Feature Switch**

1. Define constant in `infra/common/vamsAppFeatures.ts`
2. Add config option in `infra/config/config.ts` `ConfigPublic` interface
3. Add validation in `getConfig()`
4. Push to `enabledFeatures` array in `infra/lib/core-stack.ts`
5. Read in frontend from `/api/secure-config` response and gate UI with a feature check
6. Mirror the new option into the **ConfigBuilder** component (`documentation/docusaurus-site/src/components/ConfigBuilder/` — see its `README.md` for which files to touch: `schema.ts`, `defaults.ts`, `validation.ts`), then confirm `infra/test/configBuilderSync.test.ts` passes

### **Adding a New DynamoDB Table**

1. Create table in `infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts` and export via `storageResources`
2. Add constant to `ResourceKeys` in `backend/backend/common/resourceNames.py`
3. Add matching entry to `RESOURCE_PARAM_KEYS.dynamoTables` in `infra/common/resourceParamKeys.ts`
4. Add matching constant to `ResourceParamKeys` in `infra/deploymentDataMigration/tools/ssm_resource_lookup.py` (migration scripts resolve names from these SSM parameters)
5. Register descriptor in `resourceNameRegistry` in `storageBuilder-nestedStack.ts`
6. Grant permissions (`grantReadData`, `grantReadWriteData`) in the lambda builder
7. Resolve table name using `get_table_name(ResourceKeys.*)` at module level in the handler

The same three-way constants update applies to new audit CloudWatch log groups. Deprecated tables retained for migration move to `RESOURCE_PARAM_KEYS.dynamoTablesLegacy` (published under `dynamoTables/legacy/`).

### **Adding a New Viewer Plugin**

1. Create viewer component in `web/src/components/viewers/` and register in the viewer factory/registry
2. Add the file extension mapping
3. Add any required npm dependencies to `web/package.json`
4. If the viewer needs `unsafe-eval`, check `allowUnsafeEvalFeatures` config

### **Adding a New Processing Pipeline**

See `backendPipelines/CLAUDE.md` for the full 12-step checklist, S3 output-path conventions, and `assetId` threading pattern. In summary: create `backendPipelines/{useCase}/lambda/` (with the required `customLogging/` package) and optional `container/`, add a CDK nested stack under `infra/lib/nestedStacks/pipelines/`, wire config into `config.ts`, register in the pipeline builder, add a feature switch if optional, and — for Batch/ECS/Fargate pipelines — add the flag to all three condition blocks in `infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts`. Pass through all output paths in `vamsExecute`, use the correct output path in `constructPipeline`, preserve relative paths in container output, and update `documentation/docusaurus-site/docs/deployment/configuration-reference.md`.

---

## 📝 **Conventions**

### **Naming Conventions**

| Context                  | Convention            | Example                                |
| ------------------------ | --------------------- | -------------------------------------- |
| Backend handler file     | camelCase             | `assetService.py`, `createAsset.py`    |
| Backend handler function | `lambda_handler`      | Always `lambda_handler` as entry point |
| Pydantic model file      | camelCase             | `assetsV3.py`, `roleConstraints.py`    |
| CDK lambda builder       | `build{Name}Function` | `buildCreateAssetFunction()`           |
| CDK nested stack class   | `{Name}NestedStack`   | `ApiBuilderNestedStack`                |
| CLI command group        | kebab-case (Click)    | `vamscli role-constraint list`         |
| Frontend component       | PascalCase            | `AssetViewer.tsx`, `DatabaseList.tsx`  |
| Frontend service         | PascalCase            | `APIService.ts`                        |
| DynamoDB table env var   | UPPER_SNAKE_CASE      | `ASSET_STORAGE_TABLE_NAME`             |

### **Import Patterns**

```python
# ✅ CORRECT - Backend imports
from backend.common.validators import validate_input
from backend.models.assetsV3 import AssetRequest
from backend.handlers.auth.casbinEnforcer import CasbinEnforcer
```

```typescript
// ✅ CORRECT - CDK imports
import * as Config from "../../config/config";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import { kmsKeyLambdaPermissionAddToResourcePolicy } from "../helper/security";
```

### **Error Response Format (Backend)**

All backend handlers must return API Gateway-compatible responses:

```python
return {
    "statusCode": 200,
    "headers": {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
    },
    "body": json.dumps({"message": "Success", "data": result}),
}
```

### **Comment & Documentation Style (Match Surrounding Code)**

Comments and documentation must be **commensurate with the surrounding material** — match the detail, density, and tone of the file you are editing.

-   **Code comments**: Match the comment density and style already present. CDK stacks, for example, use brief single-line `//` notes and short `/** ... */` section headers. Describe **what** a piece of code is, not why it was added.
-   **No changelog/process narration in code**: Never reference "upgrades", "new in vX", "added for", migrations, or the change request that prompted the edit. Comments should read as if the code had always been there — changelog narration belongs in `CHANGELOG.md`.
-   **Documentation prose**: Match the concise, descriptive AWS-doc style of the page being edited (see `documentation/CLAUDE.md`). Describe how the system behaves. Do not introduce "requirement"/"must" checklists where the surrounding page uses descriptive prose, and do not reference "upgrades" unless the page is an upgrade/migration guide.

---

## 🚫 **Anti-Patterns to Avoid**

1. **Hardcoding AWS partition strings** (`arn:aws:...`) -- use `service-helper.ts`
2. **Importing Pydantic v2 APIs** (`model_validator`, `ConfigDict`) -- use v1
3. **Skipping Tier 2 auth checks** in backend handlers -- both tiers required
4. **Using BrowserRouter** in frontend -- must use HashRouter
5. **Hardcoding DynamoDB table names** -- always resolve via `common.resourceNames`
6. **Creating Lambda without CDK Nag suppression review** -- all resources must pass checks
7. **Adding API routes without corresponding handler** -- causes 500 errors
8. **Deploying features without feature switches** -- breaks conditional deployment
9. **Using `HttpUserPoolAuthorizer`** -- must use custom Lambda authorizer
10. **Skipping config validation in `getConfig()`** -- leads to silent deployment failures
11. **Over-documenting or narrating changes in comments** -- match surrounding comment density; never reference "upgrades", "new in vX", or the prompting change request in source comments (see Comment & Documentation Style)
