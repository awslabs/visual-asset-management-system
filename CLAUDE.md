# VAMS - Visual Asset Management System

This is the root-level Claude Code steering document for VAMS. It is auto-loaded in every session and provides the project-wide context that all agents need. For component-specific details, see the `CLAUDE.md` files in each subdirectory.

## 🏗️ **Project Overview**

VAMS is an AWS-native Visual Asset Management System for managing, visualizing, and processing 3D assets, point clouds, CAD files, and other visual content. It deploys as a CloudFormation/CDK stack with:

-   **React frontend** (`web/`) -- Cloudscape UI, 17 viewer plugins for 3D/media
-   **Python Lambda backend** (`backend/`) -- Casbin ABAC/RBAC auth, DynamoDB, S3
-   **CDK TypeScript infrastructure** (`infra/`) -- 10 nested stacks, multi-partition support
-   **Python CLI tool** (`tools/VamsCLI/`) -- Click framework, profile-based config
-   **Processing pipelines** (`backendPipelines/`) -- 3D conversion, GenAI labeling, Gaussian splatting, point cloud, 3D preview thumbnails

### **Version Info**

| Component            | Version/Runtime                                                                                             |
| -------------------- | ----------------------------------------------------------------------------------------------------------- |
| VAMS version         | 2.5.0 (`infra/config/config.ts`, `tools/VamsCLI/vamscli/version.py`)                                        |
| Python (Lambda)      | 3.12                                                                                                        |
| Python (development) | 3.13+                                                                                                       |
| Node (Lambda)        | 20.x                                                                                                        |
| React                | 17.0.2 (Vite build)                                                                                         |
| Pydantic             | **1.10.7 (v1, NOT v2)** -- uses `@root_validator`, `@validator`, `class Config`, not v2's `model_validator` |
| CDK                  | `aws-cdk-lib`                                                                                               |

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
├── documentation/             # User guides, API spec, permission templates
├── .clinerules/workflows/     # Detailed workflow docs (supplementary)
├── .claude/commands/          # Claude Code skills (slash commands)
└── infra/deploymentDataMigration/  # Data migration scripts (e.g., v2.4_to_v2.5)
```

---

## 🏛️ **Architecture Summary**

### **Request Flow**

```
User → CloudFront/ALB → API Gateway V2 HttpApi
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

VAMS supports three pipeline execution types: **Lambda** (sync or async invocation of a Lambda function), **SQS** (async message to an SQS queue), and **EventBridge** (async event to an EventBridge bus). SQS and EventBridge pipelines are async-only and support optional callback via Step Functions Task Tokens.

```
S3 event / API trigger → Lambda → Step Functions → Lambda / SQS / EventBridge → AWS Batch containers (optional)
  backendPipelines/{useCase}/lambda/   -- orchestration
  backendPipelines/{useCase}/container/ -- processing
```

#### **Pipeline S3 Output Paths**

The workflow ASL generates several S3 paths passed to each pipeline step. Pipelines must use the correct path for each output type:

| Path Variable                          | Bucket           | Purpose                                                         | Versioned |
| -------------------------------------- | ---------------- | --------------------------------------------------------------- | --------- |
| `outputS3AssetFilesPath`               | Asset bucket     | File-level outputs: new files, file previews (`.previewFile.X`) | Yes       |
| `outputS3AssetPreviewPath`             | Asset bucket     | Asset-level previews only (whole-asset representative image)    | Yes       |
| `outputS3AssetMetadataPath`            | Asset bucket     | Metadata files produced by the pipeline                         | Yes       |
| `inputOutputS3AssetAuxiliaryFilesPath` | Auxiliary bucket | Temporary working files or special non-versioned viewer data    | No        |

**Key distinction:** `outputS3AssetFilesPath` is for file-level outputs including `.previewFile.gif/.jpg/.png` thumbnails tied to specific files. `outputS3AssetPreviewPath` is only for asset-level preview images that represent the asset as a whole. Most pipelines producing file previews should write to `outputS3AssetFilesPath`.

**Rules for output path usage:**

-   **Always pass through** all output paths from the workflow payload in `vamsExecute` lambdas. Never hardcode empty strings for output paths — the workflow's process-output step relies on finding files at these locations.
-   **Use `outputS3AssetFilesPath`** for file-level outputs, including `.previewFile.X` thumbnail files generated by preview pipelines.
-   **Use `outputS3AssetPreviewPath`** only for asset-level preview images (not file-level previews).
-   **Use `inputOutputS3AssetAuxiliaryFilesPath`** only for temporary files during processing or for special non-versioned preview data (e.g., Potree octree viewer files) that the frontend reads directly from the auxiliary bucket.
-   The `constructPipeline` lambda should prefer the appropriate output path when provided, falling back to the auxiliary path only for direct/local invocations where workflow context is unavailable.

**Preserving relative paths for asset-adjacent outputs:**

When a pipeline writes output files that correspond to a specific input file (e.g., `.previewFile.X` thumbnails), the output **must preserve the input file's relative path within the asset**. The process-output step expects outputs at the same relative location as the input so it can move them to the correct final location in the asset bucket.

-   Asset files are stored at `{assetId}/{relative_path}/{filename}` — the relative path may include zero or more subdirectories between the asset ID and the filename.
-   The output paths (`outputS3AssetFilesPath`, etc.) point to the asset root: `s3://bucket/{assetId}/`.
-   Containers must include the relative subdirectory in the output S3 key: `{outputDir}{relative_subdir}/{filename}.previewFile.X`.

```
Input key:  xd130a6d6.../test/pump.e57
Output dir: xd130a6d6.../

✅ Correct output: xd130a6d6.../test/pump.e57.previewFile.gif
❌ Wrong output:   xd130a6d6.../pump.e57.previewFile.gif  (relative path lost)
```

**`assetId` is a workflow state variable — thread it, don't derive it.** The `assetId` is passed as a top-level field in the workflow event payload. It must be captured in the `vamsExecute` lambda, forwarded to `constructPipeline`, included in the pipeline definition, and used directly in the container. Never attempt to reverse-engineer the asset ID from S3 path segments.

To compute the relative subdirectory in container code using the explicit `assetId`:

```python
# assetId comes from the pipeline definition (threaded from workflow state)
input_parts = stage_input.objectKey.split("/")
asset_id_idx = input_parts.index(assetId)
relative_subdir = "/".join(input_parts[asset_id_idx + 1:-1])  # "" if file is at asset root
```

**Pipeline state threading pattern** (applies to all pipelines, not just preview):

```
Workflow event (assetId, databaseId, paths, ...)
  → vamsExecute lambda: capture assetId from event body, include in messagePayload
    → constructPipeline lambda: read assetId from event, include in definition dict
      → Container: read assetId from PipelineDefinition, use for relative path computation
```

### **Deployment Modes**

| Mode           | Distribution             | Notes                                              |
| -------------- | ------------------------ | -------------------------------------------------- |
| Commercial AWS | CloudFront + S3          | Default                                            |
| GovCloud       | ALB + S3                 | No CloudFront, no Location Service, FIPS endpoints |
| Air-gapped     | ALB + S3 + VPC endpoints | Full VPC isolation                                 |

---

## 🔑 **Cross-Component Patterns**

These are the critical patterns that span multiple directories. **Every developer must understand these.**

### **Pattern 1: Adding a New API Endpoint (4-5 files)**

Adding a new API endpoint requires coordinated changes across multiple components:

| Step                | File                                                         | What to do                                          |
| ------------------- | ------------------------------------------------------------ | --------------------------------------------------- |
| 1. Backend handler  | `backend/backend/handlers/{domain}/{handler}.py`             | Implement Lambda handler with Casbin enforcement    |
| 2. Pydantic model   | `backend/backend/models/{domain}.py`                         | Define request/response models (Pydantic **v1**)    |
| 3. Lambda builder   | `infra/lib/lambdaBuilder/{domain}Functions.ts`               | Build Lambda with env vars, permissions, VPC config |
| 4. API route        | `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts` | Attach Lambda to API Gateway route                  |
| 5. Frontend service | `web/src/services/APIService.ts`                             | Add API call method                                 |
| 6. CLI command      | `tools/VamsCLI/vamscli/commands/{group}.py`                  | Add CLI command (if applicable)                     |

**Never** add an endpoint without updating all required files. A handler without a route is dead code. A route without a handler will 500.

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

### **Pattern 3: Configuration Flows CDK -> DynamoDB -> Frontend**

1. CDK config (`infra/config/config.json`) drives deployment decisions
2. CDK custom resource writes feature switches to DynamoDB at deploy time
3. Frontend reads features from `/api/secure-config` at runtime
4. Feature switches are defined in `infra/common/vamsAppFeatures.ts`

```typescript
// ✅ CORRECT - Feature switch in CDK
export enum VAMS_APP_FEATURES {
    GOVCLOUD = "GOVCLOUD",
    LOCATIONSERVICES = "LOCATIONSERVICES",
    NEW_FEATURE = "NEW_FEATURE",
}

// Core stack pushes enabled features to DynamoDB
if (props.config.app.newFeature.enabled) {
    this.enabledFeatures.push(VAMS_APP_FEATURES.NEW_FEATURE);
}
```

```javascript
// ✅ CORRECT - Frontend reads features at runtime
const config = appCache.getItem("config");
if (config.featuresEnabled.includes("NEW_FEATURE")) {
    // Show feature-specific UI
}
```

### **Pattern 4: DynamoDB Table Names Are Environment Variables**

DynamoDB table names are **never** hardcoded. They are injected by CDK lambda builders as environment variables into every Lambda function.

```python
# ✅ CORRECT - Read table name from environment
import os
ASSET_STORAGE_TABLE_NAME = os.environ["ASSET_STORAGE_TABLE_NAME"]

# ❌ INCORRECT - Never hardcode table names
ASSET_STORAGE_TABLE_NAME = "vams-asset-storage"  # VIOLATION
```

```typescript
// ✅ CORRECT - CDK lambda builder injects table names
environment: {
    ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
    DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
}
```

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

When `config.app.govCloud.enabled` is true:

-   **No CloudFront** -- use ALB for static web distribution
-   **No Location Service** -- conditionally exclude
-   **FIPS endpoints required** -- use service-helper
-   **Certain VPC endpoints are conditional** -- check partition before creating
-   **No `unsafe-eval`** -- stricter CSP unless explicitly overridden

---

## 🚨 **Critical Rules**

These rules apply project-wide. Violations will cause deployment failures, security issues, or runtime errors.

### **Rule 1: Never Use Pydantic v2 Syntax**

The backend uses Pydantic **1.10.7**. Using v2 syntax will fail at import time in Lambda.

```python
# ✅ CORRECT - Pydantic v1
from pydantic import BaseModel, Field, root_validator, validator

class AssetRequest(BaseModel):
    assetName: str = Field(..., description="Name of the asset")

    @root_validator
    def validate_fields(cls, values):
        return values

    class Config:
        extra = "forbid"

# ❌ INCORRECT - Pydantic v2 (will fail)
from pydantic import model_validator  # DOES NOT EXIST in v1
class AssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # v2 syntax, VIOLATION
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

Every CDK Nag suppression requires a detailed reason explaining **why** it is acceptable in the VAMS context.

```typescript
// ✅ CORRECT
NagSuppressions.addResourceSuppressions(
    resource,
    [
        {
            id: "AwsSolutions-IAM5",
            reason: "Wildcard permissions required for dynamic S3 object access within VAMS asset buckets. Scope is limited to deployment-specific buckets.",
        },
    ],
    true
);

// ❌ INCORRECT
NagSuppressions.addResourceSuppressions(resource, [
    {
        id: "AwsSolutions-IAM5",
        reason: "Suppressed", // VIOLATION - no justification
    },
]);
```

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

When you make structural changes to the codebase, **you must update the relevant CLAUDE.md file(s)**. Structural changes include:

-   Adding or removing handler domains, components, commands, nested stacks, or pipeline directories
-   Adding new DynamoDB tables, S3 buckets, or environment variables
-   Adding new API routes or CLI commands
-   Adding or removing viewer plugins
-   Changing the configuration system (new config fields, feature switches)
-   Adding new dependencies that affect development patterns

**Which CLAUDE.md to update:**

| Change area                            | Update this file                                                                                                              |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| New backend handler/model domain       | `backend/CLAUDE.md` (directory structure, handler list)                                                                       |
| New CDK nested stack or lambda builder | `infra/CLAUDE.md` (directory structure, stack list)                                                                           |
| New frontend component/page/service    | `web/CLAUDE.md` (directory structure, key files)                                                                              |
| New CLI command group                  | `tools/VamsCLI/CLAUDE.md` (command list, directory structure)                                                                 |
| New pipeline                           | `CLAUDE.md` root (pipeline list), `documentation/docusaurus-site/docs/deployment/configuration-reference.md` (config options) |
| Cross-component pattern change         | `CLAUDE.md` root (cross-component patterns section)                                                                           |
| New skill                              | `CLAUDE.md` root (Available Claude Code Skills table)                                                                         |

**What to update:** Update the directory structure tree, key files tables, and any affected rules or patterns. Keep descriptions concise. You can also run `/refresh-steering-docs` for a comprehensive update.

---

## 🧰 **Development Commands**

### **Frontend**

```bash
cd web && npm install           # Install dependencies
cd web && npm run start         # Dev server
cd web && npm run build         # Production build
```

### **Backend**

```bash
cd backend && python -m pytest                              # All tests
cd backend && python -m pytest tests/handlers/assets/ -v    # Specific handler tests
```

### **CDK Infrastructure**

```bash
cd infra && npm install         # Install dependencies
cd infra && npx cdk synth       # Synthesize CloudFormation
cd infra && npx cdk diff        # Preview changes
cd infra && npx cdk deploy --all --require-approval never  # Deploy to dev environment
```

### **CLI**

```bash
cd tools/VamsCLI && pip install -e .    # Install in dev mode
cd tools/VamsCLI && python -m pytest    # Run CLI tests
```

### **Project-Wide (run from repo root)**

```bash
npm run lint                    # Lint check (web/src + infra/lib + infra/bin + infra/test)
npm run lint-fix                # Auto-fix lint issues
npm run prettier-check          # Check formatting
npm run prettier-fix            # Auto-fix formatting
```

> **Always run lint and prettier from the project root directory.** The root `package.json` scripts target `web/src`, `infra/lib`, `infra/bin`, and `infra/test` paths. Do not run these from individual subdirectories.

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

-   **Branch naming**: `release/X.Y.Z` for releases, `feature/description` for features
-   **Current branch**: `release/2.5.0`
-   **Main branch**: `main`
-   **Changelog format**: `standard-version` format in `CHANGELOG.md`
-   **Commit style**: Descriptive imperative mood ("Fix bugs", "Add cognito user management")

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

For deep-dive workflows, see the detailed guides in `.clinerules/workflows/`:

| Document                                                    | Covers                                                                                  |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `.clinerules/workflows/CDK_DEVELOPMENT_WORKFLOW.md`         | CDK nested stacks, constructs, lambda builders, security patterns, pipeline development |
| `.clinerules/workflows/BACKEND_CDK_DEVELOPMENT_WORKFLOW.md` | End-to-end API endpoint development across backend + CDK                                |
| `.clinerules/workflows/CLI_DEVELOPMENT_WORKFLOW.md`         | CLI commands, decorators, testing, profile support, JSON output                         |

For user-facing documentation:

| Document                                                                   | Covers                                                 |
| -------------------------------------------------------------------------- | ------------------------------------------------------ |
| `documentation/docusaurus-site/docs/concepts/permissions-model.md`         | Permission system concepts and ABAC/RBAC configuration |
| `documentation/docusaurus-site/docs/deployment/configuration-reference.md` | CDK deployment configuration reference                 |
| `documentation/docusaurus-site/docs/developer/setup.md`                    | Development environment setup and patterns             |
| `documentation/VAMS_API.yaml`                                              | OpenAPI specification for all endpoints                |

---

## 🔧 **Technology Stack Quick Reference**

### **Frontend (`web/`)**

| Technology               | Usage                                                            |
| ------------------------ | ---------------------------------------------------------------- |
| React 17.0.2             | UI framework                                                     |
| Cloudscape Design System | AWS UI component library                                         |
| AWS Amplify v6           | Auth integration                                                 |
| Custom apiClient         | Fetch-based API client with auto auth headers                    |
| HashRouter               | Client-side routing                                              |
| TypeScript               | All source `.tsx`/`.ts` (only `__mocks__/*.js` remain as JS)     |
| Viewer plugins (17)      | Three.js, Needle Engine, Potree, Gaussian Splat, GLTF, USD, etc. |

### **Backend (`backend/`)**

| Technology            | Usage                                 |
| --------------------- | ------------------------------------- |
| Python 3.12           | Lambda runtime                        |
| Pydantic 1.10.7       | Request/response validation (v1 only) |
| Casbin                | ABAC/RBAC authorization engine        |
| boto3                 | AWS SDK                               |
| AWS Lambda Powertools | Logging, tracing                      |

### **Infrastructure (`infra/`)**

| Technology               | Usage                         |
| ------------------------ | ----------------------------- |
| AWS CDK (TypeScript)     | Infrastructure as code        |
| 10 nested stacks         | Modular resource organization |
| CDK Nag                  | Security compliance checks    |
| API Gateway V2 HttpApi   | REST API layer                |
| Custom Lambda Authorizer | Unified JWT + IP auth         |

### **CLI (`tools/VamsCLI/`)**

| Technology      | Usage                           |
| --------------- | ------------------------------- |
| Python 3.13+    | CLI runtime                     |
| Click           | Command framework               |
| Profiles        | Multi-environment configuration |
| `--json-output` | Machine-readable output mode    |

---

## ⚙️ **Environment & Deployment Specifics**

### **Environment Variables (Backend)**

All Lambda handlers receive these common environment variables from CDK lambda builders:

```
ASSET_STORAGE_TABLE_NAME          # DynamoDB: asset storage
DATABASE_STORAGE_TABLE_NAME       # DynamoDB: database storage
AUTH_TABLE_NAME                   # DynamoDB: auth entities
CONSTRAINTS_TABLE_NAME            # DynamoDB: permission constraints
USER_ROLES_TABLE_NAME             # DynamoDB: user-role mappings
ROLES_TABLE_NAME                  # DynamoDB: role definitions
S3_ASSET_AUXILIARY_BUCKET          # S3: auxiliary/staging bucket
PRESIGNED_URL_TIMEOUT_SECONDS     # S3 presigned URL TTL
```

Domain-specific handlers receive additional env vars for their resources.

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

1. **All S3 buckets require TLS** -- enforced by bucket policy (deny `aws:SecureTransport=false`)
2. **KMS encryption everywhere** -- DynamoDB, S3, SNS, all use shared KMS key
3. **IAM least privilege** -- Lambda roles get only the permissions they need
4. **CSP headers** -- Content Security Policy generated dynamically based on config
5. **IP range restrictions** -- Optional IP-based access control via custom authorizer
6. **No secrets in code** -- Use SSM parameters or Secrets Manager
7. **CDK Nag enforcement** -- All stacks checked against AWS Solutions rules

---

## 🔄 **Common Cross-Component Workflows**

### **Adding a New Feature Switch**

1. Define constant in `infra/common/vamsAppFeatures.ts`
2. Add config option in `infra/config/config.ts` ConfigPublic interface
3. Add validation in `getConfig()` function
4. Push to `enabledFeatures` array in `infra/lib/core-stack.ts`
5. Read in frontend from `/api/secure-config` response
6. Gate UI components with feature check

### **Adding a New DynamoDB Table**

1. Create table in `infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts`
2. Export via `storageResources` interface
3. Pass table name as env var in lambda builder
4. Grant permissions (`grantReadData`, `grantReadWriteData`) in lambda builder
5. Read table name from `os.environ` in backend handler

### **Adding a New Viewer Plugin**

1. Create viewer component in `web/src/components/viewers/`
2. Register in viewer factory/registry
3. Add file extension mapping
4. Add any required npm dependencies to `web/package.json`
5. If viewer needs `unsafe-eval`, check `allowUnsafeEvalFeatures` config

### **Adding a New Processing Pipeline**

1. Create directory under `backendPipelines/{useCase}/`
2. Add Lambda handler in `lambda/` subdirectory
3. Add container if needed in `container/` subdirectory
4. Create CDK nested stack in `infra/lib/nestedStacks/pipelines/`
5. Add pipeline config to `config.ts` under `pipelines` section
6. Register in pipeline builder nested stack
7. Add feature switch if pipeline is optional
8. **Add pipeline flag to VPC builder** (`infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts`) in the "Pipeline-Only Required Endpoints" condition so that VPC endpoints for Batch, ECR, and ECR Docker are created when the pipeline is enabled. Pipelines that need internet access (e.g. AWS Marketplace) should also be added to the public/private subnet configuration condition and the ECS endpoint condition.
9. **Pass through all output paths** in the `vamsExecute` lambda — never hardcode empty strings for `outputS3AssetFilesPath`, `outputS3AssetPreviewPath`, or `outputS3AssetMetadataPath`. See [Pipeline S3 Output Paths](#pipeline-s3-output-paths) for conventions.
10. **Use the correct output path** in the `constructPipeline` lambda for the container's output target: `outputS3AssetFilesPath` for file-level outputs (including `.previewFile.X` thumbnails), `outputS3AssetPreviewPath` for asset-level previews only, `outputS3AssetMetadataPath` for metadata. Only use `inputOutputS3AssetAuxiliaryFilesPath` for temporary files or special non-versioned viewer data (e.g., Potree octree files).
11. **Preserve relative paths** in container output. When writing asset-adjacent files (e.g., `.previewFile.X`), the container must maintain the input file's relative subdirectory within the asset so process-output can locate outputs correctly. See [Pipeline S3 Output Paths](#pipeline-s3-output-paths) for the derivation pattern.
12. **Update `documentation/docusaurus-site/docs/deployment/configuration-reference.md`** with all new pipeline configuration options (`enabled`, `autoRegisterWithVAMS`, `autoRegisterAutoTriggerOnFileUpload`, and any pipeline-specific settings). Follow the existing format: `-   \`app.pipelines.{pipelineName}.{option}\` | default: {value} | #{description}`.

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
# ✅ CORRECT - Consistent response format
return {
    "statusCode": 200,
    "headers": {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
    },
    "body": json.dumps({"message": "Success", "data": result})
}
```

---

## 🚫 **Anti-Patterns to Avoid**

1. **Hardcoding AWS partition strings** (`arn:aws:...`) -- use `service-helper.ts`
2. **Importing Pydantic v2 APIs** (`model_validator`, `ConfigDict`) -- use v1
3. **Skipping Tier 2 auth checks** in backend handlers -- both tiers required
4. **Using BrowserRouter** in frontend -- must use HashRouter
5. **Hardcoding DynamoDB table names** -- always use env vars
6. **Creating Lambda without CDK Nag suppression review** -- all resources must pass checks
7. **Adding API routes without corresponding handler** -- causes 500 errors
8. **Deploying features without feature switches** -- breaks conditional deployment
9. **Using `HttpUserPoolAuthorizer`** -- must use custom Lambda authorizer
10. **Skipping config validation in `getConfig()`** -- leads to silent deployment failures
