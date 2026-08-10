# Document Revision History

This page tracks the version history of the Visual Asset Management System (VAMS). Each release includes a summary of key changes, new features, and important upgrade notes.

---

## Revision History

| Version       | Date       | Key Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [2.6.0](#260) | 2026-08-30 | Workflow, pipeline, and execution overhaul — **breaking** for externally registered pipelines: inputs arrive through a resolved manifest, an asynchronous pipeline returns a task token, sub-processes and logs are registered, and a definition is declared in a `vamsSchema` bundle; port with the [v2.5 to v2.6 pipeline migration guide](../pipelines/migrating-pipelines-v25-to-v26.md). API Gateway migrated from HTTP API (v2) to REST API (v1) — **breaking**: clients registered directly against the old API endpoint must be re-setup (re-run `vamscli setup`); supports REGIONAL and PRIVATE endpoint types. Physna Sync add-on (Phase 1): one-way synchronization of supported VAMS files and metadata to a Physna tenant; geospatial search and map view across assets and files (new `geo_MD_location` field); OpenSearch index v3 + provisioned engine upgrade to 3.5 (OpenSearch 2.x in the AWS European Sovereign Cloud); next-generation OpenSearch Serverless upgrade (scale-to-zero, higher performance, better cost pricing); provisioned `r7g.large.search` default + automatic OpenSearch service-linked role creation; advanced IAM role customization for restricted environments; VAMS MCP server and agent skill for operating a deployment with AI agents |
| [2.5.3](#253) | 2026-08-03 | Fixed web `npm install` dependency override conflict; npm dependency updates across all packages, rich text editor security upgrade, AWS CDK CLI version alignment                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| [2.5.2](#252) | 2026-06-19 | Security fixes: Casbin authorization expression injection, createAsset S3 key location validation; backend test framework updates, authorization documentation clarifications, dependency updates                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| [2.5.1](#251) | 2026-04-23 | Bug fixes: upload subfolder paths, file version history cleanup on delete, S3 version pagination, authorization error handling, image viewer version switching, CLI download pagination, CLI upload progress display                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| [2.5.0](#250) | 2026-04-21 | Website overhaul (Vite, Amplify V6, dark/light theme), Needle USD viewer, Three.js CAD viewer, SQS/EventBridge pipeline support, 3D preview thumbnail pipeline, database metadata with location maps, enhanced asset versions, Cognito user management, API key management, permission templates                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| [2.4.1](#241) | 2026-01-30 | GovCloud deployment fixes, CloudFront KMS fix, metadata schema navigation fix, file manager UX improvements                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| [2.4.0](#240) | 2026-01-16 | Veerum viewer, NVIDIA Isaac Lab pipeline, Garnet Framework addon, metadata schema overhaul, metadata system overhaul, asset unarchiving, CloudFront custom domains, audit logging, EKS pipeline option                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| [2.3.2](#232) | 2026-01-12 | CLI documentation fixes, NPM dependency updates                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| [2.3.1](#231) | 2025-11-21 | CLI bug fixes, viewer install optimizations                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| [2.3.0](#230) | 2025-11-13 | VamsCLI tool, overhauled search system, plugin-based viewer architecture, CesiumJS/BabylonJS/PlayCanvas viewers, CAD metadata pipeline, Gaussian Splat Toolbox, IP restrictions, asset link enhancements                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| [2.2.0](#220) | 2025-09-31 | Asset/file separation, multi-file assets, S3 presigned uploads, external OAuth IDP, asset versioning, new pipelines, global workflows, VPC improvements                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

---

## Version Details

### 2.6.0

**Release date:** 2026-08-30

**Added:**

-   **Workflow, pipeline, and execution overhaul** — A ground-up revamp of how VAMS defines pipelines and workflows, resolves their run-time configuration, executes them, and records execution history. Delivered across the backend, the CDK, the built-in pipelines, the CLI, and the web UI.

    -   **New pipeline and workflow data model** — Pipelines and workflows are database-scoped definitions with a typed `executionConfig` (one block per execution type — AWS Lambda, Amazon SQS, Amazon EventBridge, or AWS Deadline Cloud — replacing the loose `userProvidedResource` JSON string) and an admin-only `systemConfig` covering input-file arity, asset scope, metadata inputs, input-file filters, and template requirements. A workflow references its pipelines by composite `pipelineDatabaseId:pipelineId`, so references resolve unambiguously across databases. See [Pipelines and workflows](../concepts/pipelines-and-workflows.md).
    -   **Pipeline configuration templates and tag schemas** — A pipeline defines reusable configuration templates: a named configuration body (JSON, YAML, OpenJD, XML, or raw) with an optional per-template tag schema that declares the typed `{{tagName}}` placeholders inside it, validates a run's values, and renders the execute form. A template overrides its pipeline's input-file arity, asset scope, metadata inputs, and input-file filters, so one pipeline serves several conversion matrices. Bodies store inline and offload transparently to the artefacts bucket above a size threshold. See [Building custom pipelines](../pipelines/custom-pipelines.md#configuration-templates-and-per-run-options).
    -   **Asset-less multi-file execution** — An execution takes an array of input files that may span several assets, an output-target asset, and per-pipeline execution parameters (a `templateId` with its tag values, or a custom template override). A pipeline reads its inputs, output locations, and asset identity from a resolved manifest, and asset, file, and database metadata arrive in one grouped envelope.
    -   **New execution operations** — A global execution list with filters and pagination, execution details and logs, abort of a single execution or of an entire execution group, re-run from the stored execution records, and admin-only permanent delete. See [Executions](../api/workflows.md).
    -   **Workflow triggers** — A typed trigger structure (currently `fileUpload`) fires a workflow when a matching file is uploaded, matched by the workflow's input-file filters and dispatched through the VAMS orchestration Amazon EventBridge bus.
    -   **Standardized built-in pipeline registration** — Built-in pipelines register from a file-based `vamsSchema` bundle uploaded to the artefacts bucket at deploy time, so registration is idempotent and carries no hard-coded ARNs. An external solution self-registers through the same importer. Per-output-format built-ins are consolidated into single pipelines with one template per format. See [Migrating custom pipelines from v2.5 to v2.6](../pipelines/migrating-pipelines-v25-to-v26.md).
    -   **CLI** — New `pipeline`, `workflow`, and `execution` command groups covering the full API surface: pipeline CRUD plus `pipeline template` and `pipeline tag-schema`; workflow CRUD, `workflow trigger`, and multi-file `workflow execute`; and `execution` list, details, logs, abort, rerun, and permanent-delete. See [Pipelines](../cli/commands/pipelines.md), [Workflows](../cli/commands/workflows.md), and [Executions](../cli/commands/executions.md).
    -   **Web** — New top-level Pipelines, Workflows, and Executions pages plus an execute wizard, built on React 18: category-grouped server-paginated lists, a live-polling executions board with status, trigger, and group filters, a full execution-detail page, and a DAG-preview workflow builder. Existing pages and viewer plugins are unchanged. See [Pipelines and workflows](../user-guide/pipelines-and-workflows.md).

-   **Physna Sync add-on (Phase 1)** — Optional one-way synchronization of supported VAMS files, file metadata, file attributes, and asset metadata to a Physna tenant for geometric and semantic 3D search. Enable via `app.addons.usePhysnaSync` in `infra/config/config.json`. See [Physna Integration](../developer/physna-integration.md).
-   **Physna Viewer plugin** — New viewer plugin that embeds the Physna-hosted 3D/CAD viewer directly inside VAMS asset pages. Enabled automatically whenever the Physna Sync add-on is deployed; uses a new VAMS-authorized proxy endpoint (`GET /addon/physna/viewer`) so Physna credentials never reach the browser. See [Physna Integration](../developer/physna-integration.md#physna-viewer).
-   **Geospatial search and map view across assets and files.** Asset and file OpenSearch documents now include a derived `geo_MD_location` field of type `geo_shape`, populated by the indexers from a `location` metadata key (GeoJSON or `{latitude, longitude, altitude}`) or from individual `latitude`/`longitude`/`altitude` metadata fields. The `POST /search` and `POST /search/simple` API endpoints, the `vamscli search` commands, and a new sidebar panel in the web UI accept point + radius, bounding box, and arbitrary GeoJSON filters with `intersects`/`within`/`contains`/`disjoint` relations. Map view (including mini-map thumbnails) now works for both assets and files, and renders polygon/multi-polygon shapes as well as points.
-   **EventBridge orchestration bus** — A top-level custom Amazon EventBridge event bus is now created as a foundation for future event-driven VAMS features (email/subscription events, pipeline registration and success/error events, audit event logging). Bus and event-source names are deployment-unique so multiple VAMS deployments can coexist in one AWS Region, and a starter audit rule routes all VAMS deployment events to a dedicated Amazon CloudWatch log group.
-   **Advanced IAM role customization** — Two optional, opt-in mechanisms for environments that restrict or centrally manage IAM role creation, controlled by a new `app.iamRoleConfig` config section. `useCustomBootstrapRoles` replaces the CDK bootstrap roles (or removes them entirely via the CLI-credentials synthesizer), and `useCustomVamsStackRoles` applies `iam.Role.customizeRoles` to generate an IAM policy report and substitute pre-created application roles across the WAF stack, core stack, and all nested stacks. The role mappings live in a separate `infra/config/policy/iamRoleConfig.json` file. Both options default to disabled, so VAMS manages all IAM roles unless explicitly opted out. See [Configuration reference](../deployment/configuration-reference.md#advanced-iam-role-customization-appiamroleconfig).
-   **Stable VPC Availability Zone count** — The VPC builder now provisions every subnet type across a fixed baseline of two Availability Zones rather than varying the count by feature, which removes a class of AWS CloudFormation subnet-deletion failures that occurred when features were later disabled. Public and private (egress) subnets are still created only when an internet-facing pipeline or the public-subnet ALB needs them. A new [networking troubleshooting procedure](../troubleshooting/common-issues.md) documents how to recover a stack that hit subnet-deletion or stuck-ENI errors.
-   **Configurable OpenSearch Availability Zone count** — Amazon OpenSearch Service provisioned domains accept a new `app.openSearch.useProvisioned.availabilityZoneCount` option (`2` or `3`, default `2`), with one data node per zone. At `2` the domain runs Multi-AZ without Standby; at `3` it runs Multi-AZ with Standby, and the asset/file indexes are created with two replicas (three copies) to satisfy Standby's multiple-of-three requirement. The default of `2` matches the historical domain layout, so existing provisioned deployments are unchanged. A 3-AZ Standby domain must be created fresh (an in-place 2→3 switch is rejected by the service). See [Configuration reference](../deployment/configuration-reference.md) and [Network architecture](../architecture/networking.md#availability-zone-configuration).
-   **Configurable OpenSearch shard count** — Provisioned domains accept a new `app.openSearch.useProvisioned.numberOfShards` option (default `1`) to set the primary shard count per index. Large indexes — as a guideline, those expected to exceed roughly 60 GB (about 3 million asset or file records) — should increase it. The shard count is fixed at index creation, so changing it requires re-creating the index (disable and re-enable OpenSearch, then reindex).
-   **Updated provisioned OpenSearch instance type** — The default node instance type for provisioned domains changed from `r6g.large.search` to `r7g.large.search` (newer Graviton generation) for both data and master nodes, across `config.ts` and all config templates. `dataNodeInstanceType` / `masterNodeInstanceType` remain configurable.
-   **Partition-based OpenSearch engine version** — The provisioned OpenSearch engine version is now selected by partition. Commercial AWS, AWS GovCloud, and other partitions use `OPENSEARCH_VERSION` (OpenSearch 3.x); the **AWS European Sovereign Cloud** (partition `aws-eusc`, Region `eusc-de-east-1`) uses the new `OPENSEARCH_VERSION_EUSOVEREIGN` (OpenSearch 2.x) because OpenSearch 3.x is not yet supported there. The selection is automatic based on the deployment partition and requires no configuration.
-   **Automatic OpenSearch service-linked role creation** — The `AWSServiceRoleForAmazonOpenSearchService` service-linked role is now created idempotently during deployment, resolving intermittent _"you must enable a service-linked role to give Amazon OpenSearch Service permissions to access your VPC"_ failures on provisioned deployments. The role is created if missing and left unchanged if it already exists; it is account-wide and not removed on stack teardown.
-   **AWS European Sovereign Cloud template and endpoints** — Added a deployment template (`infra/config/config.template.eusovereign.json`) for the AWS European Sovereign Cloud (Region `eusc-de-east-1`, partition `aws-eusc`). For now it reuses the GovCloud guardrails (`app.govCloud.enabled = true`) and sets OpenSearch provisioned `availabilityZoneCount` to `2`. The partition-aware service endpoint table was regenerated from the upstream botocore endpoints file to add the `aws-eusc` partition, fill in services added to existing partitions (`aws`, `aws-cn`, `aws-us-gov`, `aws-iso`, `aws-iso-b`, `aws-iso-e`, `aws-iso-f`) over time, and add newly published services.
-   **Next-generation OpenSearch Serverless upgrade** — Amazon OpenSearch Serverless is upgraded to next-generation Serverless, which brings **scale-to-zero** (indexing and search compute scale down to 0 OCUs when idle, so an idle deployment incurs near-zero OpenSearch compute cost), **higher performance** (faster autoscaling and resource provisioning in response to workload spikes), and **better cost pricing** (configurable OCU ceilings combined with scale-to-zero let a deployment pay for the capacity it uses instead of a fixed always-on minimum). The collection is deployed into a collection group whose generation is set by new `app.openSearch.useServerless` options: `nextGen` (default `true` for commercial partitions, `false` for GovCloud/EU Sovereign Cloud — sets the generation to `NEXTGEN` or `CLASSIC`), `allowPublic` (default `true`; `false` places the collection behind a VPC endpoint), `enableStandbyReplicas` (defaults to the value of `nextGen`; required to be `true` for `NEXTGEN`, optional for `CLASSIC`), and OCU capacity bounds `minIndexingOcu`/`maxIndexingOcu`/`minSearchOcu`/`maxSearchOcu` (defaults `2`/`16`/`2`/`16`; each must be one of `0`, `2`, `4`, `8`, `16`, or any multiple of `16`). Scale-to-zero (a minimum OCU of `0`) trades an approximately 10–20 second cold start after about 10 minutes of inactivity for the cost savings; keep the minimum OCUs at `1` or greater for consistently low latency. A private collection (`allowPublic=false`) requires `app.useGlobalVpc.enabled` and is reached over a VPC endpoint spanning two Availability Zones; as with provisioned OpenSearch, only the OpenSearch-facing Lambda functions are placed in the VPC (`app.useGlobalVpc.useForAllLambdas` is not required). See [Configuration reference](../deployment/configuration-reference.md#amazon-opensearch-service-appopensearch).
-   **Reindex utility direct-run mode** — The OpenSearch [reindex utility](../developer/utilities/reindex.md) added a `--mode` option: `lambda` (default, invokes the deployed reindexer Lambda — unchanged behavior) and `direct` (runs the backend reindexer handler locally with no execution-time limit). Direct mode is intended for very large asset repositories where the Lambda would exceed its 15-minute maximum runtime; it requires the handler's table-name and SSM-parameter inputs and local AWS credentials, while `--backend-path` defaults to the backend source resolved relative to the script (overridden only for non-standard checkouts). New guidance recommends monitoring the reindexer Lambda for timeouts on deployments above roughly 100,000 records.
-   **CLI Amazon Cognito password management** — `vamscli auth login` gained a `--new-password` option so a forced password change (for example, on a new account's first sign-in) can complete non-interactively, including under `--json-output`. New `vamscli auth change-password` (change a known password) and `vamscli auth forgot-password` (self-service reset via an emailed verification code) commands were added. All are Amazon Cognito-only and backward compatible with existing login commands. See [Setup and Authentication](../cli/commands/setup-and-auth.md).
-   **VAMS MCP server** — A Model Context Protocol server (`tools/VamsMCP/`) that exposes the VAMS API as agent-callable tools, letting any MCP-capable host search, inspect, and manage databases, assets, files, metadata, versions, tags, asset links, and workflows through natural language. It stores no credentials and reuses the local `vamscli` profile, so each user runs it against their own VAMS account and receives exactly that user's two-tier permissions. Read and search tools are always available; create and update tools require `VAMS_ENABLE_WRITES=true`, and archive and delete tools additionally require `VAMS_ENABLE_DESTRUCTIVE=true`. Both mutation tiers are off by default. See [Agentic Development](../developer/agentic-development.md).
-   **VAMS agent skill** — A portable agent skill (`tools/VamsAgentSkill/SKILL.md`, invoked in Claude Code as `/vams-agent`) that operates a live deployment at runtime through the installed CLI. The skill discovers the deployment's current commands through `vamscli --help` rather than hardcoding them, authenticates per session, scopes itself to the routes the authenticated user is allowed to call, and operates read-only unless the user explicitly authorizes changes. See [Agentic Development](../developer/agentic-development.md).

**Documentation:**

-   **CLI documentation consolidated into the official site.** The complete VamsCLI reference — every command and option, authentication and profile flows, installation, automation, and a new CLI-specific troubleshooting section — now lives under the CLI section of the documentation site (`cli/`). The legacy in-repo docs under `tools/VamsCLI/docs/` are deprecated; `tools/VamsCLI/README.md` retains basic installation and quick start and points to the official site. See [CLI Getting Started](../cli/getting-started.md).
-   **New developer security reference.** A new [Security: Developer Reference](../developer/security.md) page traces a request from the identity provider through the custom Lambda authorizer to both Casbin authorization tiers, documenting where claims and roles are produced and consumed at each hop, the authorizer's cache lifetimes, and the customizable authentication override hooks.

**Breaking changes:**

-   The workflow, pipeline, and execution overhaul breaks externally registered pipelines. A pipeline written against v2.5 does not run unchanged: it reads its inputs from a resolved manifest rather than from the invocation payload, an asynchronous pipeline returns an AWS Step Functions task token for the workflow to advance past it, and it registers its sub-processes and log locations so abort and log retrieval reach them. Registration moves as well — a definition is declared in a file-based `vamsSchema` bundle imported through the schema importer, and a pipeline is referenced by composite `pipelineDatabaseId:pipelineId`. The data migration reshapes stored definitions but cannot change a pipeline's code, so every externally maintained pipeline is ported with [Migrating custom pipelines from v2.5 to v2.6](../pipelines/migrating-pipelines-v25-to-v26.md). Deployments running only VAMS built-in pipelines need nothing beyond the update steps. The pipeline, workflow, and execution API is the overhauled shape and is not backward-compatible with the pre-overhaul endpoints.
-   Reading, aborting, or re-running an execution requires the operation's action on every asset the run read — each input file's asset plus each asset named as a metadata source — where earlier releases accepted any one of them for the global listing while requiring all of them for the details, so a listing could offer a row whose details then returned `403`. A role scoped to a subset of databases stops seeing cross-database runs it could previously list and stops being able to re-run them; widen its `asset` and `database` GET constraints to cover the databases those runs span to restore the earlier breadth. An asset that has been permanently deleted is authorized on the database it lived in, so the history of runs against a deleted asset remains reachable; an archived asset is unaffected and stays authorized on its own record. See [v2.5 to v2.6 update guide](../deployment/update-the-solution.md#execution-visibility).
-   OpenSearch index names rolled forward to `vams-assets-v3` and `vams-files-v3`. The schema-deploy custom resource creates the empty v3 indexes; the previous v2 indexes are abandoned and left in place until you delete them manually. A reindex is required to populate v3.
-   `OPENSEARCH_VERSION` switched from `OPENSEARCH_2_7` to `OPENSEARCH_3_5`. Provisioned OpenSearch domains will perform a major-version engine upgrade. Serverless collections are unaffected.
-   OpenSearch Serverless now deploys the collection into a collection group and adds `nextGen`, `allowPublic`, `enableStandbyReplicas`, and OCU capacity options. Because the collection is placed in a collection group, the public-access network-policy change is applied, and the group generation (`CLASSIC` or `NEXTGEN`) is set, enabling or changing Serverless requires a re-deployment: disable Serverless and deploy, then re-enable with the new settings and deploy, and reindex. A minimum OCU of `0` requires `nextGen=true`, and `nextGen=true` requires `enableStandbyReplicas=true` (NEXTGEN collection groups do not support disabled standby replicas); a private collection (`allowPublic=false`) requires `app.useGlobalVpc.enabled` (only the OpenSearch-facing Lambda functions are placed in the VPC, so `app.useGlobalVpc.useForAllLambdas` is not required). See [v2.5 to v2.6 update guide](../deployment/update-the-solution.md#v25-to-v26).
-   **The authorizer claims context shape changed, which can break customized MFA, claims, and login-profile logic.** Deployments that have edited the customization hooks under `backend/backend/customConfigCommon/` must review them before upgrading; stock deployments need no action. The REST API (v1) REQUEST authorizer delivers claims as a **flat map of string values** under `requestContext.authorizer`, rather than nested at `requestContext.authorizer.jwt.claims` or `requestContext.authorizer.lambda` as the HTTP API (v2) did — so custom logic that branches on those nested keys and falls through to an empty dict now silently reads no claims instead of raising. Every value is a string, so JSON-valued claims (`vams:tokens`, `vams:roles`) need `json.loads` and `vams:mfaEnabled` is `"true"`/`"false"`. Read claims through `request_to_claims(event)` instead of indexing the authorizer context directly. Additionally, `customMFATokenScopeCheckOverride` changed signature from `(user, lambdaRequest)` to `(user, authorizerJwtClaims, lambdaRequest)`, is now called from the authorizer instead of each handler, and its Cognito default uses `admin_get_user` with the new `USER_POOL_ID` environment variable; a hook still written against the old signature is caught and defaulted to `false`, silently disabling MFA-gated roles. MFA is resolved once at authorization time and delivered as `vams:mfaEnabled`, so `customAuthClaimsCheckOverride` should read `claims_and_roles["mfaEnabled"]` rather than re-deriving it. See [Authentication and Authorization Flow](../developer/security.md#authentication-and-authorization-flow) and [Authentication Override Hooks](../developer/security.md#authentication-override-hooks).
-   Enabling a VPC-requiring feature (ALB, OpenSearch Provisioned, or any container-based pipeline) while `app.useGlobalVpc.enabled` is `false` now fails configuration validation with an explicit error instead of silently auto-enabling the VPC. Configurations that previously relied on the implicit auto-enable must set `app.useGlobalVpc.enabled` to `true` explicitly. See [Configuration reference](../deployment/configuration-reference.md) and [Plan your deployment](../deployment/plan-your-deployment.md).
-   Provisioned OpenSearch `availabilityZoneCount` defaults to `2` and the VPC is built with that many Availability Zones. Earlier releases built the VPC across 3 Availability Zones for provisioned OpenSearch while the domain used only 2, so on upgrade the unused third AZ subnet is removed — a VPC downgrade that can fail subnet deletion when elastic network interfaces are still attached. Set `availabilityZoneCount` to `3` to preserve the existing VPC, or follow the drain-and-redeploy teardown to move to 2 AZs. See [v2.5 to v2.6 update guide](../deployment/update-the-solution.md#v25-to-v26).

:::warning[Upgrade Path]
Run the reindex migration at `infra/deploymentDataMigration/v2.5_to_v2.6/upgrade` after deploying the v2.6 stack to repopulate `vams-assets-v3` and `vams-files-v3` from source data. For provisioned deployments, if the OpenSearch 2.7 → 3.5 in-place engine upgrade fails during `cdk deploy`, deploy first with OpenSearch disabled in `config.json` to delete the existing domain, then re-enable and redeploy to create a fresh 3.5 domain before running the migration. See the [v2.5 to v2.6 update guide](../deployment/update-the-solution.md#v25-to-v26).
:::

### 2.5.3

**Release date:** 2026-08-03

**Key fixes:**

-   Fixed `npm install` failing in the `web/` directory with an `EOVERRIDE` error reporting that an override for `fast-xml-parser` conflicted with the direct dependency. The package was declared both as a direct dependency and as an override, which could prevent dependency resolution from completing when an existing lock file was present. Both declarations were removed, because the web application does not use the package directly and its dependents now supply a compatible version.

**Other changes:**

-   Updated package dependencies across all npm packages in the repository to resolve reported npm audit findings.
-   Upgraded the rich text editor used by the asset comments feature to address high-severity cross-site scripting and prototype pollution findings.
-   Raised the minimum AWS CDK command line interface version to match the AWS CDK library version, which is required for AWS CloudFormation template synthesis to succeed.
-   Aligned all Docusaurus documentation packages to a single matching version.

**Known issues:**

-   Pipelines that rely on the Amazon Linux 2 image type for Amazon Elastic Container Service and AWS Batch containers may not function correctly, because Amazon Linux 2 reached end of support on July 31, 2026. A fix is planned for version 2.6.0.
-   Eight npm audit findings remain in the web application (six low severity and two moderate severity) that cannot be resolved without breaking changes. The available fixes for the routing library require React version 18 or later, and the web application currently targets React 17. These are development and build-time dependencies and will be revisited in version 2.6.0.
-   Five npm audit findings remain in the VEERUM viewer installation package. Remediation requires authentication to the private package registry that hosts the viewer, and two findings have no fix available from the upstream maintainers. The VEERUM viewer is disabled by default.

### 2.5.2

**Release date:** 2026-06-19

**Key fixes:**

-   Fixed a Casbin authorization implementation defect that allowed additional policies to be injected through field values that were evaluated as regular expressions. Impact is low because Casbin policies can only be set by administrators by default. Additional backend tests were added to cover this case.
-   Fixed a `createAsset` API defect that allowed an optional Amazon S3 bucket key location to be specified without validating that the location belonged to the provided database identifier's default S3 bucket and prefix path, that no asset already existed at that S3 key path, and that the supplied path met validation requirements.
-   Fixed a latent defect in which the backend test framework had not been updated for changes introduced in version 2.5, which caused test failures.

**Other changes:**

-   Added default GitHub issue and pull request templates.
-   Updated the authorization documentation to reflect the preceding fixes and to clarify existing behavior.
-   Updated several package dependency versions to address npm audit findings.

### 2.5.1

**Release date:** 2026-04-23

**Key fixes:**

-   Fixed file upload path construction when uploading to a subfolder — files now correctly include the full folder path (e.g., `/textures/USD/texture.png` instead of `/texture.png`).
-   Fixed permanent file deletion not cleaning up Amazon DynamoDB version snapshot records — re-uploading a file at the same path no longer shows stale version history from previously deleted files.
-   Fixed Amazon S3 version deletion not paginating — permanent delete now removes all S3 object versions even when a file has more than 1000 versions.
-   Fixed permanent asset deletion not paginating Amazon DynamoDB queries for version files and metadata version cleanup — assets with large numbers of versions or files now fully delete all related records, using batch writes for efficiency.
-   Fixed `authorization_error()` being raised as an exception instead of returned as a response across multiple backend handlers (assetService, metadataSchemaService, userRolesService, createRole, tagService, createTag), which caused "exceptions must derive from BaseException" errors.
-   Fixed version switching across many viewer plugins — when switching to a different file version — the viewer now correctly fetches and displays the selected version instead of showing the latest only
-   Fixed CLI asset download command capping at approximately 100 files — now paginates through all API results when downloading whole assets or folders.
    -   Improved CLI asset download performance — presigned URL generation and file downloads now run concurrently via a streaming pipeline instead of sequentially.
    -   The `--recursive` flag on `assets download` now defaults `--file-key` to `/` when not specified, enabling whole-asset recursive downloads without explicitly providing `--file-key /`.
-   Fixed CLI file upload progress display erasing terminal scrollback history — progress now tracks and clears only the lines it actually printed.
-   Fixed NVIDIA pipeline CodeBuild Amazon ECR repositories failing to delete when disabling pipelines — added `emptyOnDelete` to Cosmos and Gr00t CodeBuild ECR repositories so container images are automatically cleared before AWS CloudFormation deletion.

### 2.5.0

**Release date:** 2026-04-21

**Major changes:**

-   Migrated the web application to Vite build framework with AWS Amplify V6 Gen2 SDK and dark/light theme support (dark default).
-   Added Needle USD 3D WASM viewer for `.usd`, `.usda`, `.usdc`, `.usdz` files.
-   Added Three.js 3D and CAD viewer for `.gltf`, `.glb`, `.obj`, `.fbx`, `.stl`, `.ply`, `.dae`, `.3ds`, `.3mf`, `.stp`, `.step`, `.iges`, `.brep` files with optional LGPL-licensed CAD support.
-   Added Amazon SQS and Amazon EventBridge pipeline execution types alongside AWS Lambda.
-   Added 3D Preview Thumbnail pipeline for CPU-based headless rendering of animated GIF or static image previews.
-   Added database metadata management with Amazon Location Service mini-map display.
-   Enhanced asset versioning with aliasing, archive/unarchive, version editing, and metadata versioning.
-   Added Amazon Cognito user management through web UI, API, and CLI.
-   Added API Key management system with user ID impersonation.
-   Added permission constraint template bulk import system with pre-built templates.

**Breaking changes:**

-   Asset version DynamoDB table changes require migration scripts.
-   Web overhaul causes significant merge conflicts for forked repositories.

:::warning[Upgrade Path]
Run the upgrade script at `infra/deploymentDataMigration/v2.4_to_v2.5/upgrade` to migrate permission constraints and asset version data.
:::

---

### 2.4.1

**Release date:** 2026-01-30

**Key fixes:**

-   Fixed CDK deployment errors for GovCloud environments (storage queue names, CloudFront KMS principals, metadata schema KMS permissions, Isaac Lab pipeline IAM).
-   Fixed metadata schema page blank state when re-navigating.
-   Improved Asset FileManager to remember expanded folders during lazy loading.
-   Added service worker for local WASM debugging.

---

### 2.4.0

**Release date:** 2026-01-16

**Major changes:**

-   Added Veerum 3D Viewer (licensed) for point clouds and 3D tilesets.
-   Added NVIDIA Isaac Lab reinforcement learning training pipeline with GPU acceleration.
-   Added Garnet Framework addon for knowledge graph data synchronization.
-   Overhauled metadata schema system: database-specific and global schemas, multi-schema overlay, new field types, CDK-deployable defaults.
-   Overhauled metadata system: multi-entity support (databases, assets, files, asset links), bulk editing with CSV, file attributes, metadata versioning.
-   Added Amazon EKS deployment option for RapidPipeline.
-   Added asset unarchiving, file renaming, CloudFront custom domain support.
-   Added Amazon CloudWatch audit logging for authorization, actions, and errors.
-   Refactored data indexing flow for Amazon OpenSearch and partner integrations.

**Breaking changes:**

-   Permission constraints migrated to a dedicated DynamoDB table.
-   Metadata and schema DynamoDB tables replaced with new tables.
-   Amazon OpenSearch indexes changed schema for metadata fields.

:::warning[Upgrade Path]
Run the upgrade script at `infra/deploymentDataMigration/v2.3_to_v2.4/upgrade` to migrate constraints, metadata, and re-index Amazon OpenSearch.
:::

---

### 2.3.2

**Release date:** 2026-01-12

**Key fixes:**

-   CLI documentation corrections.
-   NPM dependency security updates (`npm audit fix`).

---

### 2.3.1

**Release date:** 2025-11-21

**Key fixes:**

-   CLI sentinel object check, file upload exception handling, and pattern updates.
-   Optimized web viewer custom installers to skip disabled viewers.
-   Licensed viewers disabled by default in configuration.

---

### 2.3.0

**Release date:** 2025-11-13

**Major changes:**

-   Introduced VamsCLI command-line tool for automation, bulk operations, and CI/CD integration.
-   Overhauled asset and file search with new Amazon OpenSearch dual-index architecture.
-   Rewrote the viewer system as a plugin-based, dynamically-loaded architecture with 17 viewer plugins.
-   Added CesiumJS 3D tileset viewer, BabylonJS and PlayCanvas Gaussian Splat viewers, VNTANA licensed viewer, PDF viewer, and Text viewer.
-   Added CAD/Mesh Metadata Extraction pipeline using Trimesh and CadQuery.
-   Added Gaussian Splat Toolbox pipeline.
-   Replaced built-in API Gateway authorizers with custom Lambda authorizers supporting IP range restrictions.
-   Added support for additional asset link metadata types (WXYZ, Boolean, Date, Matrix4x4, GeoJSON, GeoPoint, LLA, JSON).
-   Refactored workflow creation to remove heavyweight step functions library dependency.

**Breaking changes:**

-   Custom Lambda authorizers replace built-in authorizers (may require cache reset).
-   AWS Batch/Fargate CDK construct naming changes require two-phase deployment for existing pipelines.
-   Amazon OpenSearch re-indexing required for new dual-index schema.

:::warning[Upgrade Path]
Run the upgrade script at `infra/deploymentDataMigration/v2.2_to_v2.3/upgrade` to re-index Amazon OpenSearch.
:::

---

### 2.2.0

**Release date:** 2025-09-31

**Major changes:**

-   Complete overhaul of asset management APIs separating assets from files.
-   Added multi-file asset support with folder structures.
-   Implemented Amazon S3 presigned URL uploads replacing scoped S3 access.
-   Added external OAuth 2.0 identity provider support as alternative to Amazon Cognito.
-   Added asset versioning with Amazon S3 version tracking.
-   Added global pipelines and workflows across databases.
-   Introduced new pipelines: GenAI Metadata 3D Labeling, Potree point cloud viewer, 3D basic conversion.
-   Relaxed naming conventions for databases, pipelines, workflows, and asset IDs.
-   Enhanced VPC support with additional endpoints.
-   Added multiple Amazon S3 bucket support with external bucket import.

**Breaking changes:**

-   CDK configuration file format changes required.
-   Asset and database DynamoDB table schema changes require migration scripts.
-   VPC subnet changes may break existing deployments (A/B deployment recommended).
-   New Amazon Cognito user pool may be generated (user migration may be needed).

:::warning[Upgrade Path]
Use A/B stack deployment with data migration scripts at `infra/deploymentDataMigration/v2.1_to_v2.2/upgrade`.
:::
