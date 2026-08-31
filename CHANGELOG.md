# Changelog

All notable changes to this project will be documented in this file. See [standard-version](https://github.com/conventional-changelog/standard-version) for commit guidelines.

## [2.6.0] (2026-08-30)

### Major Change Summary:

-   Workflow, Pipeline, and Execution Overhaul - Ground-up revamp of how VAMS defines pipelines and workflows, resolves run-time configuration, executes them, and records history. Database-scoped definitions with typed execution config, reusable pipeline templates with tag schemas, asset-less multi-file execution, execution abort/re-run/logs, and file-upload workflow triggers. Delivered across backend, CDK, built-in pipelines, CLI, and new web Pipelines/Workflows/Executions pages with an in-place execute wizard. New AWS Deadline Cloud pipeline type support for commercial cloud partition.
-   API Gateway REST API Migration - Backend API migrated from HTTP API (v2) to REST API (v1) served under a fixed `/api` stage, built from a cross-stack route registry and a single inline OpenAPI spec. Supports REGIONAL and PRIVATE endpoint types and a configurable integration timeout (29-300 seconds)
-   New AWS European Sovereign Cloud Support - VAMS now targets the `aws-eusc` partition (Region `eusc-de-east-1`) with a dedicated deployment template, a regenerated partition-aware service endpoint table, an AWS SDK baseline resolving the partition's `.amazonaws.eu` endpoints, automatic OpenSearch engine-version selection, and Region-specific Availability Zone validation. Not yet validated or supported by default
-   Per-Database Tag Namespacing - Tags and tag types can be GLOBAL or scoped to a specific database, so the same name can exist independently in different databases. Tag administration is scopable through new `databaseId` permission constraints
-   New Amazon Cognito OIDC Federation - Federated authentication through any OpenID Connect identity provider alongside existing SAML and native Cognito login, with a configurable SSO button name and an optional default role for federated users not yet assigned one
-   Amazon OpenSearch Upgrades - Engine upgraded to OpenSearch 3.x (2.x in the EU Sovereign Cloud), next-generation Serverless with scale-to-zero and configurable OCU ceilings, configurable provisioned shard and Availability Zone counts, and idempotent service-linked role creation
-   New Geospatial Search and Map View - Geospatial filtering and map view across both assets and files, including polygon/multi-polygon rendering, a derived `geo_shape` index field, and a `geoSearch` payload on the search APIs and CLI
-   New Physna Sync Add-on (Phase 1) - Optional one-way synchronization of supported files, file metadata/attributes, and asset metadata to a Physna tenant, plus an embedded Physna viewer plugin for synced files
-   New IFC BIM File Viewer - Open-source That Open Engine viewer for `.ifc`/`.ifczip` with spatial model tree, property inspection, hide/isolate, section planes, and measurements
-   New SuperSplat Editor Viewer - Embedded open-source PlayCanvas SuperSplat Gaussian-splat editor supporting `.lcc` (XGRIDS multi-LOD, not previously viewable), `.ply`, `.sog`, and `.splat`; now the default splat viewer
-   New Coordinate Transform Pipeline - Reprojects point clouds between coordinate reference systems (E57, LAS, LAZ, PLY in; LAZ, LAS, E57, PLY out) with EPSG/PROJ/WKT/custom-grid support and per-asset metadata overrides
-   New Physical AI NVIDIA Cosmos 3 (omni) Inference Pipeline - GPU-accelerated generation on NVIDIA's omnimodal Cosmos 3 world foundation models: Cosmos3-Nano (16B) on four GPUs and three Cosmos3-Super (64B) variants on eight, both with FSDP-sharded inference, with metadata-driven prompts
-   New Asset and File History - Permanent per-asset lifecycle audit history (create/edit/archive/unarchive/delete) and per-version file change provenance for uploads, workflow executions, copies, moves, renames, archives, and direct S3 changes, exposed through API, CLI, and web
-   New File Viewer Option from Search Results - Viewable file search rows open directly in a popup visualizer, including a multi-select mode that opens several files together in one viewer
-   New CLI Directory Sync - `vamscli sync file push|pull` performs S3-sync-style directory synchronization against an asset or subdirectory, with `.vamsignore` support, conflict detection, and atomic downloads
-   Enhanced Download Performance - Bulk presigned-URL generation (up to 1,500 keys per request) removes most per-file round trips for multi-file and folder downloads in both the CLI and web
-   New Agentic Development Support - A VAMS MCP server exposing the API as agent-callable tools (reusing the user's own `vamscli` profile, storing no credentials) and a portable VAMS agent skill that self-discovers commands and is read-only by default
-   New Self-Service API Keys - Users manage their own API keys without administrative access through new `/auth/user/api-keys` routes, CLI commands, and a dual-mode web page
-   Enhanced Security Posture - WAF blocks by default from a dedicated policy file and always protects the API Gateway stage; optional network restrictions for S3 presigned URLs; the Cesium viewer no longer requires `unsafe-eval`; plus hardened authorization, command construction, and comment sanitization
-   CDK Resource Names via AWS Systems Manager - DynamoDB table, non-asset S3 bucket, and audit log group names resolve from Parameter Store instead of Lambda environment variables
-   New IAM Role Customization for Restricted Environments - Two opt-in mechanisms let deployers who cannot create IAM roles map pre-created roles instead, for both the CDK bootstrap roles and the VAMS stack roles
-   New Interactive Configuration Builder - A browser-based form on the documentation site that generates and validates `infra/config/config.json` from a per-partition starting template

### ⚠ BREAKING CHANGES

-   **API Gateway HTTP API → REST API migration changes the API endpoint.** The API Gateway identifier and invoke URL change on deployment, so any client registered directly against the old endpoint must be re-setup — including the VAMS CLI (`vamscli setup`) and any external integration or script holding the API base URL. Clients reaching the API through the CloudFront or ALB front (the web application, and CLIs configured with the front's `/api` URL) are unaffected.
    -   **Migration note:** `config.json` loader restructures an existing flat `app.api` layout automatically — it defaults `app.api.apiType` to `"APIGATEWAY_REST"` and builds `app.api.apiGatewayRest` from any flat `globalRateLimit`, `globalBurstLimit`, `endpointType`, `apiGatewayTimeoutTime`, and `externalRegionalAPIGatewayVPCEId` (carried over as `optionalExternalPrivateApigVPCEId`, which applies only to a `PRIVATE` endpoint), so an existing `config.json` deploys unedited. Only custom APIs added in a fork outside this project need restructuring by hand. Declaring `app.api.apiGatewayRest` yourself replaces the carry-over, so a hand-written block must list every field it needs.
    -   CloudFront's `/api/*` behavior uses `originPath: "/api"`, and the ALB prepends `/api` to `/api*` and `/secure-config*` paths (the fixed REST API stage), so the browser and CLI base URLs are unchanged.
-   **The workflow, pipeline, and execution overhaul breaks custom and externally registered pipelines.** A pipeline written against v2.5 does not run unchanged: inputs arrive through a resolved manifest rather than on the invocation payload, an asynchronous pipeline must return a Step Functions task token for the workflow to advance, and a pipeline must register its sub-processes and log locations for abort and log retrieval to reach them. Registration also moves to a file-based `vamsSchema` bundle imported through the schema importer. The data migration reshapes stored **definitions** but cannot change a pipeline's **code**, so every externally maintained pipeline needs porting; deployments running only VAMS built-in pipelines need nothing beyond the deployment steps. See [Migrating custom pipelines from v2.5 to v2.6](https://awslabs.github.io/visual-asset-management-system/pipelines/migrating-pipelines-v25-to-v26).
    -   **Three API routes are removed, and any direct API client must be repointed.** `PUT /pipelines` (create a pipeline) is replaced by `POST /database/{databaseId}/pipelines`; `PUT /workflows` (create a workflow) by `POST /database/{databaseId}/workflows`; and `POST /database/{databaseId}/assets/{assetId}/workflows/{workflowId}` (run a workflow against one asset) by `POST /workflows/{workflowDatabaseId}/{workflowId}/execute`, which is asset-less and takes an input-file array plus an output-target asset instead of a path-bound asset. The bare `/pipelines` and `/workflows` paths now serve `GET` only. A removed route is absent from the API's OpenAPI spec, so a call to it is rejected by the authorizer with a `403` rather than a `404` — this affects scripts, CI jobs, and home-built clients even when the deployment runs no custom pipeline code.
-   **Role constraints that scope the `pipeline` objectType by `pipelineType` are no longer enforced as written, and a mandatory manual audit is required before upgrading.** `pipelineType` is not a constraint criteria field in v2.6 — the value it held now lives in `category` — and a criterion naming an unrecognized field is dropped when the permission policy is compiled. See [Permission constraint audit for `pipelineType`](https://awslabs.github.io/visual-asset-management-system/deployment/update-the-solution#permission-constraint-audit-for-pipelinetype).
-   **The authorizer claims context shape changed, which can break customized MFA, claims, and login-profile logic.** Deployments that edited the hooks under `backend/backend/customConfigCommon/` must review them before upgrading; stock deployments need no action.
    -   **The authorizer context is now a flat string map.** The REST API REQUEST authorizer delivers claims as a flat map of string values under `requestContext.authorizer` rather than nested under `jwt`/`lambda`, so custom logic branching on the old shape silently reads no claims instead of raising. Read claims through `request_to_claims(event)`; JSON-valued claims (`vams:tokens`, `vams:roles`) must be `json.loads`-ed, and `vams:mfaEnabled` is the string `"true"`/`"false"`.
    -   **`customMFATokenScopeCheckOverride` takes a new argument and no longer extracts claims itself.** It now takes a third argument (verified claims are passed in), is called from the authorizer rather than each handler, and resolves Cognito MFA with `admin_get_user`. A hook written against the old signature is defaulted to `false`, silently disabling MFA-gated roles.
    -   **MFA state is resolved once at authorization time** and passed to handlers as the `vams:mfaEnabled` authorizer context value, consolidating the Cognito and external-IDP MFA-login checks in the authorizer Lambda and removing them from every handler.
-   **The shipped `customAuthProfileLoginWriteOverride` default is inert under the REST API.** The default in `customAuthLoginProfile.py` still carries the old nested-shape branches, so its email-from-claims override no longer fires. It is harmless as shipped — the handler already persists the correct `userId`, and a stored profile email set at creation is unaffected — but a deployment that relies on that hook to populate profile fields from token claims must update the extraction to the flat shape.
-   **OpenSearch index names rolled forward** to `vams-assets-v3` and `vams-files-v3`. The schema-deploy custom resource creates the empty v3 indexes; the previous v2 indexes are abandoned in place until deleted manually. A reindex is required to populate v3 for all OpenSearch deployments.
    -   Run `infra/deploymentDataMigration/v2.5_to_v2.6/upgrade` after deploying to repopulate the new indexes from source data.
-   **`OPENSEARCH_VERSION` switched from `OPENSEARCH_2_7` to `OPENSEARCH_3_5`.** Provisioned domains perform a major-version engine upgrade; serverless collections are unaffected.
-   **OpenSearch Serverless now deploys into a collection group** with configurable OCU capacity plus new `nextGen` and `allowPublic` options. Existing serverless deployments must be removed, re-deployed, and re-indexed.
-   **`app.openSearch.useServerless.allowPublic` is new and defaults to `true`, which fails configuration validation on a fully VPC-isolated deployment.** A `config.json` carrying no `allowPublic` key resolves to a public collection, and a deployment with both `app.useGlobalVpc.enabled` and `app.useGlobalVpc.useForAllLambdas` set to `true` now throws at `cdk synth` because an all-Lambdas-in-VPC deployment cannot reach a public collection. Set `allowPublic` to `false` — that is the setting which reproduces the v2.5 private-collection behavior for this topology, where the collection was placed behind a VPC endpoint automatically.
-   **A VPC is no longer auto-enabled.** Enabling a VPC-requiring feature (ALB, provisioned OpenSearch, or any container-based pipeline) while `app.useGlobalVpc.enabled` is `false` now fails configuration validation with an explicit list of the offending features instead of silently turning the VPC on. If you hit this on upgrade, set `app.useGlobalVpc.enabled` to `true` (the value the deployment was implicitly using) or disable the listed features.
-   **Provisioned OpenSearch `availabilityZoneCount` now defaults to `2`, and the VPC is built with exactly that many Availability Zones.** The VPC builder previously always provisioned 3 Availability Zones while the domain used only 2, leaving the third subnet unused. On upgrade the unused third subnet is deleted, and because it can still hold elastic network interfaces (shared interface VPC endpoints, and VPC-attached Lambda ENIs when `useForAllLambdas` is set) AWS CloudFormation may fail to delete it. See the [networking troubleshooting procedure](https://awslabs.github.io/visual-asset-management-system/troubleshooting/common-issues).
-   **A VPC deployment with `useForAllLambdas` enabled and `addVpcEndpoints` disabled now requires an operator-managed AWS Systems Manager interface VPC endpoint.** Every non-pipeline Lambda function resolves resource names from Parameter Store at cold start and fails without SSM API access.
-   **AWS WAF changes from count-only monitoring to enforcement on a deployment with `app.useWaf` enabled.** v2.5 ran the AWS Common Rule Set in `count` mode, recording matches without rejecting them; the three rule groups now declared in `infra/config/policy/wafPolicyConfig.json` — Common Rule Set, Known Bad Inputs, and Amazon IP Reputation List — are in block mode. A request that previously only incremented a counter is now answered `403` by WAF before it reaches the authorizer or any Lambda, so it produces no VAMS log entry to correlate with the upgrade. Two Common Rule Set rules are already overridden back to `count` because VAMS traffic trips them (`SizeRestrictions_BODY` for multi-part upload bodies and `SizeRestrictions_QUERYSTRING` for the presigned URL the SuperSplat viewer passes in its `?load=` parameter). Review the WAF blocked-request metrics after upgrading; set `"block": false` on a group in that file to return it to monitor mode, or remove the file to restore the prior count-only behavior.
-   **CLI: bulk transfer commands now exit non-zero when any individual file fails.** `file upload`, `assets download` and `assets export --download-files` previously exited `0` after a partial — or total — transfer failure, printing a warning that only a human reads. A CI step written as `vamscli file upload ./models -d db -a asset && vamscli workflow execute ...`, or any `set -e` script, therefore treated an upload that transferred 900 of 1000 files as complete and proceeded against an asset missing the other 100. The commands now exit `1` whenever `overall_success` is false. Any script or job that branches on the exit code changes behavior: a partial transfer that used to report success now fails the step. That is the intended behavior — the previous exit code made silent data loss indistinguishable from success — but it is a behavioral break, so review any pipeline that invokes them. `sync file push` and `sync file pull` follow the same contract.
    -   **The response payload is unchanged** and is still written to stdout before the exit, so `--json-output` consumers keep every field: `overall_success`, `total_files`, `successful_files`, `failed_files`, the per-file `failed_files[]` / `failed_downloads[]` lists, and `downloadResults` for `assets export`. Nothing was renamed or removed, so a consumer can distinguish a partial transfer from a command that could not run by parsing stdout on the failure path.
    -   **Programmatic invocation is exempt.** The `industry engineering bom`, `industry engineering plm` and `industry spatial glb` commands call `file upload` and `assets export` through `ctx.invoke()` and read `overall_success` off the returned result themselves, so the exit is suppressed on that path and those flows are unaffected. Documented under "Partial Transfers Exit Non-Zero" in the CLI automation guide.
-   **CLI: `profile info` and `profile delete` exit non-zero for a profile that does not exist, and their `--json-output` shape on that path changed.** Both previously exited `0`. The response is now `{"error": "Profile 'x' does not exist", "error_type": "ProfileNotFoundError"}`; the `success`, `profile_name`, `message` and `exists` keys are gone from the not-found path. The response for a profile that exists is unchanged.
-   **CloudWatch log groups and the Isaac Lab training EFS now use the shared VAMS customer-managed KMS key, and the EFS is REPLACED on upgrade.** Applies only when `app.useKmsCmkEncryption.enabled` is `true`; with it `false` nothing changes. The two resource kinds behave differently and only one loses data:
    -   **Log groups update in place.** Attaching a key to `AWS::Logs::LogGroup` is an `AssociateKmsKey` call, not a replacement, so the group and its retained events survive. Events already ingested stay unencrypted — CloudWatch applies a key to new events only — so a group holding history ends up with a mix.
    -   **If one of these resources fails to update, disable the owning pipeline, deploy, then re-enable it.** Set the pipeline's `enabled` flag to `false` in `config.json`, run `cdk deploy`, set it back to `true`, and deploy again. That removes the resource and recreates it cleanly instead of leaving the stack in a failed update. The same procedure applies to the CloudTrail log group via `app.addStackCloudTrailLogs`.
    -   **That off/on cycle DELETES the log group, and its data with it.** Every pipeline log group and the CloudTrail log group are `RemovalPolicy.DESTROY`, so disabling the owning feature discards the group's retained events — audit and diagnostic history included. **To keep any of it, rename the log group in the AWS console (or copy its events out) before the disabling deploy**, so the deletion targets the VAMS-named group and your renamed copy is left behind. There is no way to preserve it in place, because the name is what ties the group to the stack resource.

**Recommended Upgrade Path:** Run the v2.5 → v2.6 migration to reshape execution history, migrate pipeline/workflow and tag definitions, backfill asset history, and reindex OpenSearch: `infra\deploymentDataMigration\v2.5_to_v2.6\upgrade`. See the [v2.5 to v2.6 migration guide](https://awslabs.github.io/visual-asset-management-system/deployment/update-the-solution#v25-to-v26) for the full ordered procedure.

### Features

-   **Workflows & Pipelines** Workflow, pipeline, and execution system overhaul — a ground-up revamp of how VAMS defines pipelines and workflows, resolves their run-time configuration, executes them, and records execution history.
    -   Support for AWS Deadline Cloud pipelines
    -   Pipelines and workflows are now database-scoped definitions with a typed `executionConfig` per execution type (Lambda, SQS, EventBridge, or AWS Deadline Cloud, replacing the loose `userProvidedResource` JSON string) and an admin-only `systemConfig` covering input-file arity, asset scope, metadata inputs, input-file filters, and template requirements.
    -   Pipelines can define reusable, versioned configuration templates (JSON/YAML/OpenJD/XML/raw) with an optional tag schema that validates and fills the tags substituted into the body at launch. Templates can override arity, metadata inputs, asset scope, and filters, so one pipeline serves multiple conversion matrices — for example one template per output format.
    -   Execution is asset-less: it takes an input-file array that may span multiple assets, an output-target asset, and per-pipeline parameters (`templateId` plus tag values, or a custom template override).
    -   New execution operations cover a global (asset-less) filterable and paginated execution list, execution details and logs, abort (a single execution or an entire execution group via `executionGroupId`), re-run from the stored execution records, and admin-only permanent delete.
    -   The shared input-metadata file passed to pipelines is grouped by asset (`{schemaVersion: 2, assets: [{databaseId, assetId, assetData, files: [{fileKey, metadata, attributes}]}]}`); every use-case pipeline that reads metadata resolves records for its specific `(databaseId, assetId, fileKey)`.
    -   An extensible typed trigger structure (currently `fileUpload`) fires a workflow when a matching file is uploaded, dispatched through the VAMS orchestration Amazon EventBridge bus.
    -   Built-in pipelines register from a file-based `vamsSchema` bundle at deploy time, so registration is idempotent and carries no hard-coded ARNs; external solutions self-register through the same importer. Former per-output-format built-ins are consolidated into single pipelines with per-format templates.
    -   **CLI** New `pipeline`, `execution`, and refactored `workflow` command groups cover the full API surface, including templates, tag schemas, triggers, and asset-less multi-file execute. The pre-overhaul asset-scoped execute and `autoTriggerOnFileExtensionsUpload` surface is removed.
    -   **External Plugin** The NVIDIA Isaac Sim and Esri ArcGIS Pro connectors are updated to the overhauled CLI surface: workflow records now carry `systemConfig`, `specifiedPipelines`, and `triggerCount` in place of `autoTriggerExtensions`, and file listings carry `dateCreatedCurrentVersion`, `key`, and `etag` in place of `lastModified`. Because the connectors shell out to `vamscli` and parse its `--json-output` rather than importing it, a v2.5 connector run against `vamscli` 2.6.0 renders the changed fields blank with no error and its workflow-execute call targets a removed route — so the connectors must be updated alongside the CLI.
    -   **Web** New top-level Pipelines, Workflows, and Executions pages plus an in-place execute wizard, built on React 18 with a modern module stack (TanStack Query/Table, Tailwind + Radix, Monaco, reactflow v11). Includes a live-polling executions board with filters and quick view, a full execution-detail page, and a DAG-preview workflow builder; all existing Cloudscape pages and viewer plugins are preserved.
    -   **CDK** New execution-overhaul data-model tables (pipeline, workflow, template, tag-schema, and trigger tables plus the workflow-keyed execution tables), their SSM resource-name parameters, the `vamsSchema` import custom resource, and the `VamsSchemaRegistration` construct used by every built-in pipeline. All pipeline, workflow, and execution Lambda functions and API routes are built in the secondary API nested stack (`apiBuilder2`), and the workflow orchestration EventBridge (`events`) interface VPC endpoint is created whenever VPC endpoints are enabled.
    -   **CDK** The v2.5→v2.6 data migration reshapes legacy workflow execution history into the workflow-keyed model and migrates user-database (non-`GLOBAL`) pipeline and workflow definitions into the new tables (`--steps pipelineWorkflowDefinitions`). `GLOBAL` built-ins are skipped — they are re-created by the `vamsSchema` importer — and references to consolidated built-in ids are remapped, so the migration never clobbers a freshly registered built-in.
-   **Tags & Tag Types** Per-database tag namespacing — tags and tag types can be **GLOBAL** or scoped to a specific database, so the same name can exist independently in different databases.
    -   Tag and tag-type names are unique **per database**, not globally. A name may not be both GLOBAL and database-specific, so every tag an asset references resolves unambiguously within its own database plus GLOBAL.
    -   Pre-upgrade tags are treated as GLOBAL and copied into new composite-key tables by the `tagsNamespacing` migration step; asset tag lists are unchanged.
    -   The Casbin `tag` and `tagType` constraints gain a `databaseId` field for scoping tag administration.
    -   A database can no longer be created with the reserved `databaseId` value `GLOBAL`.
    -   **CDK** New composite-key tables `TagStorageTableV2` and `TagTypeStorageTableV2` (partition key `databaseId` — the literal `GLOBAL` for global entries — sort key `tagName`/`tagTypeName`), each with a name GSI (`tagNameIndex`/`tagTypeNameIndex`) for cross-database name lookups. The former single-key `TagStorageTable`/`TagTypeStorageTable` are retained (`RETAIN`) as legacy migration sources.
    -   **API** `GET /tags` and `GET /tag-types` accept optional `databaseId` and `scope` (`global`/`all`) parameters, and create/update/delete accept a `databaseId`; shapes are otherwise unchanged.
-   **Auth** Amazon Cognito OIDC federation — federated authentication through any OpenID Connect identity provider alongside existing SAML and native Cognito login, configured in `infra/config/oidc-config.ts` with the client secret in AWS Secrets Manager. SAML and OIDC are mutually exclusive and both require the Cognito hosted UI (commercial partition only).
    -   A new `app.authProvider.authorizerOptions.defaultUserRoleName` grants a baseline role to an authenticated user with no role assignments, for federated logins not provisioned in VAMS. The login screen shows a configurable SSO button name for Cognito SAML, Cognito OIDC, and external IdP configurations.
-   **CDK** AWS European Sovereign Cloud support — a new deployment template targeting Region `eusc-de-east-1` in the `aws-eusc` partition, mirroring the GovCloud guardrails (VPC required, no Amazon CloudFront, no Amazon Location Service) with provisioned OpenSearch fixed at 2 Availability Zones.
    -   Partition support spans the stack: the service endpoint table carries `aws-eusc` entries so ARNs and endpoints resolve to the partition's `.amazonaws.eu` suffix, `boto3`/`botocore` are raised to a release that resolves them, the OpenSearch engine version is selected automatically because 3.x is unavailable there, and EventBridge bus CMK encryption is left off because the partition does not support it.
    -   European Sovereign Cloud deployments are not yet validated or supported by default.
-   **Web** Preview files directly from search results — each viewable file search row carries an eye icon that opens the file in a popup visualizer without navigating to its asset. A multi-select mode accumulates several files and opens them together, with per-file asset and database context so each file loads from its own asset.
-   **Web** Geospatial search and map view across both assets and files, with a Geospatial filter panel and polygon/multi-polygon rendering in the map view and mini-map thumbnails.
    -   The asset and file indexes declare a derived `geo_MD_location` `geo_shape` field, populated by the indexers from a `location` metadata key (GeoPoint, GeoJSON, or `{latitude, longitude, altitude}`) or individual latitude/longitude/altitude fields.
    -   `POST /search`, `POST /search/simple`, and the `vamscli search` commands accept a `geoSearch` payload (point + radius, bounding box, or arbitrary GeoJSON) with `intersects`/`within`/`contains`/`disjoint` relations.
-   **Web** Minor adjustments to the asset and file search pages to further streamline component placement and use.
-   New Physna Sync add-on (Phase 1) — optional one-way synchronization of supported files, file metadata, file attributes, and asset metadata to a Physna tenant. Enable via `app.addons.usePhysnaSync`.
-   **Web** New Physna viewer plugin embedding the Physna-hosted 3D/CAD viewer for synced files, backed by a `GET /addon/physna/viewer` proxy that enforces VAMS two-tier authorization and keeps Physna credentials off the client. Enabled automatically with the add-on.
-   **Web** New IFC BIM file viewer plugin rendering Industry Foundation Classes models with the open-source That Open Engine (`web-ifc`), supporting `.ifc` and `.ifczip` with a spatial model tree, property inspection, hide/isolate, section planes, and measurements. Vendored as a self-contained bundle and enabled by default. It uses the multithreaded `web-ifc-mt.wasm` build when cross-origin isolation is available and falls back to single-thread otherwise, and is gated on the `ALLOWUNSAFEEVAL` feature flag because its WASM loader needs `unsafe-eval` in the CSP.
-   **Web** New SuperSplat Editor viewer plugin embedding the open-source PlayCanvas SuperSplat Gaussian-splat editor, supporting `.lcc` (XGRIDS multi-LOD, not previously viewable in VAMS), `.ply`, `.sog`, and `.splat`, and now the default splat viewer. This is the first iframe-embedded viewer; its editing and export tools operate in the browser only and are not saved back to VAMS.
-   **Web** Cesium 3D Tileset viewer upgraded to the widget-less `@cesium/engine` package, which removes the viewer's dependency on the `unsafe-eval` CSP directive (no longer requiring the `ALLOWUNSAFEEVAL` flag) and is roughly 3x smaller. KTX2/Basis textures and `.spz` splats still require `unsafe-eval`; the UI is standardized to the tabbed Scene Graph / Controls layout used by the other 3D viewers.
-   **Web** BabylonJS and PlayCanvas Gaussian Splat viewers gained a floating 3-tab control panel comparable to the ThreeJS controls panel.
-   New Coordinate Transform pipeline — reprojects point clouds between coordinate reference systems, accepting E57, LAS, LAZ, and PLY and producing LAZ, LAS, E57, or PLY through an AWS Batch Fargate container using PDAL and pyproj. Source and target CRS accept EPSG codes, PROJ strings, WKT, or custom named local grids, set as pipeline defaults or per-asset metadata overrides; enable via `app.pipelines.useConversionCoordinateTransform`.
-   New Physical AI NVIDIA Cosmos 3 (omni) inference pipeline — GPU-accelerated generation on NVIDIA's omnimodal Cosmos 3 world foundation models from a single shared container image, where the variant is selected by checkpoint and the task by `model_mode`. Supports Cosmos3-Nano (16B) on a four-GPU tier and three Cosmos3-Super (64B) variants (Text2Video, Text2Image, Image2Video) on an eight-GPU tier, both FSDP-sharded so the checkpoint's parameters are spread across the devices a job reserves, each enabled independently via `app.pipelines.useNvidiaCosmos3`. Nano video templates expose the frame count as a per-run field, defaulting to 93 frames.
-   **Pipelines** Gaussian Splat Toolbox upgraded to the upstream Open Source 3D Reconstruction Toolbox for Gaussian Splats on AWS v1.0.0 release, adding mesh and interchange outputs (`.usdz` and a collision mesh `.ply` for simulation and physics) alongside the existing splat, video, and image outputs.
-   **CDK** The Gaussian Splat Toolbox and NVIDIA Isaac Lab Training pipelines can build their container images in the cloud with AWS CodeBuild and ECR instead of a local Docker build during a CDK deploy.
-   Asset lifecycle history — a permanent per-asset audit history of create, edit, archive, unarchive, and permanent delete, capturing the operation, acting user, change origin (API versus S3 bucket-sync ingestion), and a snapshot of the asset fields. Records survive permanent deletion, and recreating an asset with the same id continues the same trail.
    -   Exposed through a new paged API endpoint `GET /database/{databaseId}/assets/{assetId}/assetHistory`, which returns records newest first.
    -   **CLI** New `vamscli assets history` command with the standard pagination options (`--page-size`, `--starting-token`, `--auto-paginate`, `--max-items`) and `--json-output`.
    -   **Web** New Asset History modal on the Asset View File Manager details panel — a `(History)` link on the asset root node's Type row opens a server-side paged history table with per-record snapshot details.
    -   **CDK** The v2.5→v2.6 data migration backfills `create` records from each asset's v0 version record and archive/unarchive records from its archive fields, idempotent on re-run.
-   Asset file change history — per-version provenance (how a file version was created and by whom) for uploads, workflow executions, copies, moves, renames, archives, and direct S3 changes, stamped as S3 object metadata and recorded on ingest. Surfaced through the file APIs, the `vamscli file` commands, and the web file manager; versions created before this release report blank provenance.
-   Bulk presigned-URL generation for downloads — the download API accepts a `keys` array (up to 1,500 keys of one asset per request) alongside the existing single `key`, returning a per-file entry array and skipping unavailable paths with a per-file report. Single-file requests are fully backwards compatible.
    -   The CLI multi-file flows, `sync file pull`, the web shareable-URL dialog, and the multi-file/folder download page all use it with local paging, removing most per-file round trips on large assets. `assets download` with an asset version now downloads the files as they existed in that snapshot, and a new `--version-id` option fetches a specific S3 version of one file.
-   **CLI** New `sync` command group — `vamscli sync file push` and `vamscli sync file pull` perform S3-sync-style directory synchronization between a local directory and an asset or subdirectory, comparing by size and modified timestamp and transferring only differences, with `.vamsignore` support and basic conflict detection.
    -   Downloads now write to a temporary file, verify the received size, atomically move it into place, and stamp the remote modified timestamp, so interrupted downloads no longer leave partial files and repeated syncs stay stable.
-   **CLI** Amazon Cognito password management — `vamscli auth login` gains `--new-password` for a non-interactive forced password change, and new `vamscli auth change-password` and `vamscli auth forgot-password` commands support self-service change and code-based reset. A new `vamscli assets unarchive` command restores a soft-deleted asset including its files and preview.
-   New VAMS MCP server (`tools/VamsMCP/`) — a [Model Context Protocol](https://modelcontextprotocol.io/) server exposing the VAMS API as agent-callable tools, so any MCP-capable host can search, inspect, and manage databases, assets, files, metadata, versions, tags, asset links, and workflows through natural language. It stores no credentials, keys, or URLs, reusing the `vamscli` profile the user already configured so each user runs under their own login and permissions.
-   New VAMS agent skill (`tools/VamsAgentSkill/SKILL.md`) — a portable agent skill for operating a live deployment through the installed `vamscli`. It hardcodes no commands (self-discovering them via `vamscli --help`), scopes itself to the user's allowed API routes, and is read-only by default; mutating commands require explicit authorization. Surfaced in Claude Code as `/vams-agent`.
-   User-level (self-service) API keys — new `/auth/user/api-keys` routes let users manage their own keys without administrative access, scoped server-side to the requesting user, with a required expiration of at most 365 days that later edits cannot extend beyond 365 days from creation. The existing admin routes are unchanged.
    -   Exposed through new `vamscli api-key user list|create|update|delete` CLI commands.
    -   **Web** The API Key Management page now supports two modes driven by the cached allowed-API-routes list: "All Keys (Admin)" and "My Keys".
    -   **Web** The page moved from the "Admin - Auth" navigation section to a new "User" section, which is hidden for users without access to either mode.
    -   The default read-only role and the permission templates grant self-service API key access and the API key management web route.
-   API route and constraint metadata listing — `GET /auth/routes/api` returns all VAMS API routes (paths, methods, categories) from a new master route definition file, and `GET /auth/routes/api/allowed` returns the routes the requesting user may call. `GET /auth/constraints/permissionObjects` returns the constraint object types with the fields valid on each, plus operators, permissions, and permission types, making that metadata API-driven rather than defined in the web client.
    -   The per-object-type field matrix is authoritative: a criterion whose field is invalid for its object type is rejected at create/update and template import, and out-of-matrix or deprecated fields are ignored during policy compilation. Exposed through `vamscli auth routes list|allowed` and `vamscli role constraint permission-objects`.
    -   **Web** The web constraints editor fetches the full route list and offers an autosuggest of valid API routes when authoring `api` constraints (`route__path` values); backend handlers dispatch requests against the same master route definitions (`backend/backend/common/apiRoutes.py`).
    -   **Web** The web constraints editor loads object types, fields, operators, permissions, and permission types from the metadata endpoint instead of local maps, and offers an autosuggest of the deployment's web routes when authoring `web` constraints.
    -   **CDK** The auth constraints service and its routes are consolidated in the secondary API stack (`apiBuilder2-nestedStack.ts`), which keeps both API stacks clear of the per-template CloudFormation ceilings.
-   Authorization constraints now combine AND and OR criteria within the same policy — when a constraint defines both `criteriaAnd` and `criteriaOr`, access requires all AND criteria and at least one OR criterion (previously the groups were alternatives). Combined constraints also generate fewer Casbin rules, improving authorization performance.
-   Asset archive/unarchive file-state independence — unarchiving an asset restores the asset record only by default, leaving its files archived. An opt-in (`unarchiveFiles` API field, `--unarchive-files` CLI flag, web toggle) restores exactly the files the asset archive operation archived; files archived individually beforehand always stay archived, and a direct S3 upload under an archived asset's prefix now auto-restores the asset record.
-   Metadata GET APIs (asset, file, database, asset link) now implement true request/response pagination, returning a page plus a `NextToken` with `maxItems`, `pageSize`, and `startingToken`, keeping responses under the AWS Lambda and API Gateway size limits. Enrichment and ordering are applied to the full set before paging so ordering is stable across pages.
    -   The `vamscli metadata asset|file|asset-link|database list` commands and the web metadata views automatically follow `NextToken` to retrieve the complete set; direct API consumers that do not follow it receive only the first page.
-   The asset listing API now flags truncated responses with `truncated: true` alongside the `NextToken`, so a client can tell a partial page from a complete one.
-   Asset and file search APIs return additional fields on general filter queries (asset id, database id, S3 bucket id and name, and bucket prefix).
-   File listing responses now include each file's S3 `etag` in basic and full mode, matching the `fileInfo` API; surfaced by `vamscli file list` and `vamscli file info`.
-   Archiving, unarchiving, and permanently deleting assets with many files is significantly faster.
-   Intentional upload limits are now named constants with documented rationale. The 5,000 total-parts-per-upload-request cap is documented as also bounding the presigned-URL response payload (one URL per part) under the AWS Lambda (6 MB) and API Gateway response-size limits, not only as an init-Lambda guard. See [Known Limitations](documentation/docusaurus-site/docs/troubleshooting/known-limitations.md).
-   Outbound external-system sync tracking — VAMS records every outbound synchronization to an external system (Physna, Garnet Framework) in a new DynamoDB table, as the foundation for future sync-status APIs and inbound-sync tracking.
-   **CDK** API Gateway migration to REST API (v1) — routes are registered through a cross-stack route registry and materialized into a single `SpecRestApi` with an inline OpenAPI spec by an API nested stack that selects an implementation construct, so alternative entry points can be added later.
    -   The custom Lambda authorizer is now a REST REQUEST authorizer returning an IAM policy with a wildcard resource for cache correctness. It validates Cognito and external OAuth JWTs, API keys, and optional IP allowlists, resolves the client IP adaptively (forwarded IP through CloudFront/ALB, direct source IP for execute-api callers), and consolidates the MFA check that each handler previously repeated.
    -   The `app.api` block is restructured around `apiType` and an `apiGatewayRest` sub-block holding the previously flat settings:
        -   `apiType`: selects the backend API implementation, fixed to `"APIGATEWAY_REST"` (the only supported value).
        -   `endpointType`: `"REGIONAL"` (default, public — does not route through any VPC endpoint) or `"PRIVATE"` (reachable only through an execute-api interface VPC endpoint, and incompatible with CloudFront).
        -   `globalRateLimit` (default 50) and `globalBurstLimit` (default 100): API Gateway throttling.
        -   `optionalExternalPrivateApigVPCEId`: for a `"PRIVATE"` endpoint when `useGlobalVpc.addVpcEndpoints` is `false`, the id of an existing execute-api VPC endpoint to use.
        -   Only a `"PRIVATE"` endpoint uses an execute-api interface VPC endpoint; the VPC builder creates it only for that endpoint type.
        -   The deployment stage name is not a configuration option — it is the fixed constant `"api"`, absorbed by CloudFront's `originPath` and the ALB redirect so client URLs remain `/api/*`.
        -   `apiGatewayTimeoutTime` (default 29, maximum 300 seconds) sets the integration timeout on every route; values above 29 require an approved account-level increase to the API Gateway **Integration timeout** quota (`L-E5AE38E3`) in the Region **before** deploying. Raising it lets operations on assets with many files finish within one synchronous request instead of returning a `504` while the Lambda keeps working.
        -   The file streaming and download APIs now always return a `307` redirect to a short-lived presigned S3 URL rather than streaming smaller files inline as a base64 body, which the REST API requires. This adds a request hop per file fetch and can slow clients issuing many small requests, such as octree or 3D tile streaming viewers — an accepted trade-off of the migration.
-   The asset S3 bucket CORS configuration now exposes range headers (`Accept-Ranges`, `Content-Range`, `Content-Length`, `Content-Encoding`) to support future progressive splat streaming.
-   **CDK** DynamoDB table, non-asset S3 bucket, and audit CloudWatch log group names now resolve from AWS Systems Manager Parameter Store instead of Lambda environment variables. A new `ResourceNamesBuilder` nested stack publishes the parameters; non-pipeline Lambda functions resolve names at module level with a 60-minute cache and fall back to legacy environment variables for testing, while pipeline Lambda functions keep using environment variables.
-   **CDK** Several Amazon OpenSearch Service upgrades (see BREAKING CHANGES):
    -   Provisioned domains support a configurable Availability Zone count (`2` or `3`, default `2`) with one data node per zone — at `2` Multi-AZ without Standby, at `3` with Standby and two index replicas — and a configurable primary shard count (default `1`) that indexes beyond roughly 60 GB should raise. Both are fixed at index creation, so changing them requires re-creating the index.
    -   The default provisioned node instance type moved from `r6g.large.search` to `r7g.large.search`, the engine version is selected automatically per partition, and the OpenSearch service-linked role is created idempotently by a check-or-create custom resource, resolving intermittent "you must enable a service-linked role" failures.
    -   Serverless is upgraded to next-generation Serverless in a collection group controlled by `nextGen` (default `true` for commercial partitions). NEXTGEN adds scale-to-zero, faster autoscaling, and better cost pricing through configurable OCU ceilings, trading a 10-20 second cold start after about 10 minutes idle; new `allowPublic`, `enableStandbyReplicas`, and OCU bound options are included, and a fully network-isolated deployment must use a private collection.
    -   **Tooling** The reindex utility gains a `--mode` flag with `lambda` (default) and `direct`, the latter running the reindexer locally with no execution-time limit for repositories where the Lambda would exceed 15 minutes.
-   **CDK** WAF now blocks by default from a dedicated policy file (`infra/config/policy/wafPolicyConfig.json`) enabling the AWS Common Rule Set, Known Bad Inputs, and Amazon IP Reputation List in block mode plus a rate-based rule for L7 DDoS and brute-force throttling; the web ACL default action remains `allow`, and an absent file falls back to the prior count-only behavior.
    -   WAF now also always protects the API Gateway stage when enabled. Previously, with CloudFront enabled the only ACL was CloudFront-scoped, leaving the directly reachable execute-api endpoint unprotected; CloudFront deployments now create two ACLs from the same policy file because AWS WAF cannot share a CloudFront-associated ACL with another resource type.
-   **CDK** Optional network restrictions for S3 presigned URLs (`app.assetBuckets.presignedUrlNetworkRestrictions`) — configured `allowedIpRanges` or `allowedVpceIds` (mutually exclusive) are enforced as bucket policy deny statements on the VAMS-created asset and auxiliary buckets, applying only to presigned requests and taking effect on redeploy, including for already-issued URLs.
-   **CDK** Advanced IAM role customization for restricted environments — `app.iamRoleConfig` toggles `useCustomBootstrapRoles` (replace the CDK bootstrap roles via a custom stack synthesizer, or use `CliCredentialsStackSynthesizer` for none at all) and `useCustomVamsStackRoles`, with the mappings kept in `infra/config/policy/iamRoleConfig.json`. Both default to disabled, preserving VAMS-managed roles.
-   **CDK** VPC subnet Availability Zone count is now stable across feature toggles, provisioning every subnet type across a fixed baseline instead of varying by feature, which previously caused CloudFormation subnet-deletion failures when features were disabled. Public and private (egress) subnets are still created only when an internet-facing pipeline or public-subnet ALB requires them.
-   **CDK** New VAMS EventBridge orchestration bus — a top-level custom event bus as the foundation for future event-driven features (email and subscription events, pipeline registration and success/error events, audit logging).
-   **CDK** The ALB web access logs bucket is now removed with its contents on teardown instead of retained; because it carries a fixed name derived from the configured domain host, a retained bucket previously orphaned on `cdk destroy` and blocked a redeploy. External S3 bucket configuration also gained account ID, Region, and optional KMS key fields for cross-account buckets.
-   Database creation now rejects a `databaseId` matching a reserved S3 keyword (`pipeline`, `pipelines`, `preview`, `previews`, `temp-upload`, `temp-uploads`, `workspace`, `workspaces`), matched case-insensitively, in addition to `GLOBAL`, because the identifier can become a path segment that would collide with folders VAMS reserves for system use.
-   **Web** The user's allowed API routes are fetched at login and cached with a 15-minute renewal, which will drive enabling and disabling web functionality in a future release, and feature switches are refetched on every login instead of cached indefinitely.
-   **Web** Feature switches (`/secure-config`) are refetched on every login instead of being cached in browser storage indefinitely.
-   **Web** The View Asset page tracks the selected file as a `?filePath=` query parameter rather than passing state, so assets open correctly in new tabs, direct navigations, and refreshes; search uses the same parameter.
-   **Web** Asset and file search filter drop-downs now sort alphabetically.
-   **Web** Additional retry and skip steps were added to the web file upload stages for when network calls fail.
-   **Docs** New interactive Configuration Builder on the documentation site (Deployment → Configuration builder) — a fully client-side form that generates and validates a `config.json` from a Commercial, GovCloud, or EU Sovereign Cloud starting template, mirroring the cross-field validation `getConfig()` enforces. It is a helper rather than a gate (`cdk synth` remains the source of truth), and a drift check keeps its schema and template defaults in sync with the `ConfigPublic` interface.
-   **CLI/Docs** CLI documentation consolidated into the official documentation site as the single source of truth, now carrying the full command reference at parity with the code, the authentication/installation/automation flows, and a CLI-specific troubleshooting sub-section. The legacy in-repo docs under `tools/VamsCLI/docs/` are deprecated and retained temporarily for validation.
-   **CLI** `vamscli auth login` accepts a credential on standard input — `--password-stdin` for an Amazon Cognito password and `--token-override-stdin` for a pre-generated token — so a non-interactive login no longer exposes the credential in the operating system process table, where any other local account can read it for the lifetime of the command. `-p`/`--password` and `--token-override` continue to work for existing scripts and integrations, and are marked as discouraged in `--help` and in the documentation.

### Deprecations

-   AI Steering Documents: Cline agent steering (`.clinerules/workflows/`) has been deprecated and removed. **Kiro** (`.kiro/steering/`) and **Claude Code** (`CLAUDE.md` files + `.claude/commands/`) are the two currently maintained AI-assisted development agents. A new `.kiro/steering/WEB_FRONTEND.md` front-end steering file mirrors `web/CLAUDE.md` for Kiro.
-   **CLI** Old CLI documentation pages deprecated in favor of the main documentation site. Installation, authentication, and development pages remain for now under `tools/VamsCLI/docs/`, but command and troubleshooting pages are migrated to `documentation/docusaurus-site/docs/cli/`.
-   **Pipeline** Cosmos v1 (reason, transfer, predict) use-case pipeline are no longer supported with v2 and v3 support implemented. Backend files still remain as an example container implementation for the model.

### Bug Fixes

-   **Security / CDK** The Amazon Cognito web client no longer enables the custom authentication flow. Sign-in is unaffected: the browser uses SRP and the CLI uses SRP or, where enabled, `USER_PASSWORD`.
-   **Security / Web** CloudFront responses now carry `Referrer-Policy: strict-origin-when-cross-origin` and a `Permissions-Policy`. Asset and database identifiers appear in the hash route, so a full referrer disclosed them to any third-party host a page linked to or loaded from. The `Permissions-Policy` denies the hardware and payment APIs nothing in the product uses (`microphone`, `payment`, `usb`, `serial`, `bluetooth`, `hid`, `midi`, `idle-detection`). An Application Load Balancer exposes no listener attribute for either header, so an ALB deployment cannot carry them.
-   **Security / CDK** AWS WAF now logs requests durably. Each web ACL gains a `LoggingConfiguration` writing to an `aws-waf-logs-` prefixed CloudWatch log group in the ACL's own Region, with the `Authorization` header redacted.
-   **CDK** The EKS pipeline's `kubectl` layer now tracks the configured cluster version.
-   **CDK** `samlSettings` is validated before deploy, matching the new `oidcSettings` guards.
-   **CDK** The `useNvidiaCosmos` configuration error no longer describes `huggingFaceToken` as an AWS Systems Manager SecureString parameter path.
-   **Documentation** The upgrade guide covers switching between CloudFront and ALB distribution.
-   **CLI** `vamscli assets download` no longer reports success for a download that fetched nothing. Files the API declines to issue a download URL for were skipped one by one and left out of the result, so a request in which every file was declined still reported `overall_success: true`, zero total files, and exit code 0 — an empty output directory presented as a completed download. The most common cause is an asset that is not distributable: its files are listed but none can be fetched. Declined files are now reported as failures, with the reason, and the command exits non-zero; when nothing at all could be prepared, the error says why instead of implying the asset is empty.
-   **CLI** `vamscli assets download --file-key <file> --file-previews --flatten-download-tree` now reports the filename conflict between a file and its preview instead of printing usage text for `assets list`. The conflict handler called `list()`, which in that module names the `assets list` command rather than the built-in, so it ran that command with the conflicting filename as its argument and exited with a usage error for a command the user never invoked.
-   **Web** The asset download page no longer breaks when it is reloaded, bookmarked, or opened from a shared link. The folder being downloaded is chosen in the file manager and passed on navigation, so a direct visit carries none — which previously threw during render and left the error screen's Reload button repeating the same failure. The page now explains that the download starts from the asset's Files tab and links back to it.
-   **Accessibility / Web** Pinch-to-zoom and swipe-to-scroll now work on touch devices. The document root declared `touch-action: none`, and because a gesture's effective `touch-action` is the intersection of the values along the ancestor chain, that suppressed both gestures for every page — the root element is also the application's scroll container. A Firefox-only override had been added to restore scrolling in that browser alone, so behaviour differed by browser; the override is no longer needed and has been removed. The 3D, point-cloud and splat viewers still handle their own drag and pinch, which is now declared on the viewer canvas rather than on the whole document.
-   **Web** The BabylonJS Gaussian Splat viewer now returns its WebGL context when it closes. Disposing the rendering engine did not release the context, so opening several splats in one session reached the browser's live-context limit — from the eighth splat onward the browser discarded the oldest context, blanking viewers already open and making further ones unreliable until the page was reloaded.
-   **Web** The Three.js viewer no longer renders meshes dark and dull. The scene had only an ambient light at 0.5 and a directional light at 0.8, both capped at 2, and no environment map — and a physically-based material takes its ambient shading and its entire reflection from the environment, so a glTF mesh that declares no metallic-roughness values (metallic by the format's default, leaving almost no diffuse surface for a light to illuminate) stayed near-black however far the sliders were raised.
-   **Security** A third-party credential supplied in configuration — the HuggingFace token for the NVIDIA pipelines— no longer leaves a cleartext copy on the build host.
-   **Security / Web** The web route-permission lookup no longer logs its response to the browser console.
-   **CLI** VamsCLI sets its own output encoding to UTF-8, so human-readable output no longer depends on the console code page. On Windows, Python resolved the encoding from the locale (`cp1252`) whenever output was not going to a console, so the Unicode status indicators used throughout the CLI raised `UnicodeEncodeError` on any redirect or pipe — `vamscli profile list > profiles.txt` wrote a single line naming a codec error and no profile data. `PYTHONIOENCODING=utf-8` is no longer required.
-   **CLI** `profile list` and `profile current` now exit non-zero when they report a failure. Both printed the error and returned normally, leaving the exit code at `0`, so a script or CI step could not distinguish a failed listing from a successful one.
-   **Web** The Metadata Schema listing no longer shows a File Type Restriction column on the Database, Asset and Asset Link tabs. File type restrictions apply only to file metadata and file attributes — the create/edit form offers the field for those two entity types alone — so the column read `None` on every row for a property those schemas cannot hold. It remains on the File Metadata, File Attribute and All tabs.
-   **Indexing** Bucket sync no longer ingests objects into a deleted database (if the default). Nothing reported a problem: the sync logged success and the operator's only signal was the files not appearing. Objects already ingested into a deleted database are not moved by this change.
-   **Security** An Amazon SQS queue that receives Amazon SNS notifications no longer authorizes every SNS topic in every AWS account to send to it. The unconditioned statements are removed; delivery is unchanged because the scoped statement authorizes the real publisher and the AWS KMS key policy grants Amazon SNS its own access independently.
-   **Security** The Amazon OpenSearch Service schema-deploy custom resource is granted `ssm:PutParameter` on the three parameters it writes instead of `ssm:*` across every parameter matching the deployment name. The wider grant covered the whole resource-name tree that every backend Lambda resolves its table and bucket names from.
-   **Pipelines** A pre-Batch failure in an NVIDIA GPU pipeline now fails the parent workflow instead of leaving it running for hours.
-   **Deployment** The Amazon Location Service API key is deleted with the stack instead of retained, so a failed-and-rolled-back deployment no longer blocks its own retry. The key carries a deterministic name, so an orphan left by a rollback made every subsequent changeset fail validation with an already-exists error naming a resource the operator was not working on. A key orphaned by an earlier release still needs removing by hand — see [Uninstall the solution](https://awslabs.github.io/visual-asset-management-system/deployment/uninstall).
-   **Deployment** Changing `app.adminUserId` or `app.adminEmailAddress` after the first deployment no longer deletes the previous administrator. Synthesis also now rejects a username Amazon Cognito itself would refuse — whitespace, or over 128 characters — so that arrives as a configuration message instead of a deployment that rolls back every nested stack.
-   **Security** AWS WAF rate-based rules now aggregate on the forwarded client IP (`X-Forwarded-For`) rather than the immediate source, so CloudFront/ALB and shared NAT/VPN deployments rate-limit real end users instead of a shared upstream address. Rate-limited requests return `429` (distinct from the `403` used for authorization denials), which the web client and CLI treat as a retryable throttle.
-   **Security** Two-tier authorization now fails closed on missing identity — the asset-create, database-create, asset-ingest, asset-export, download, and upload handlers deny when no authenticated identity is present instead of falling through to the operation.
-   **Security** Asset version and file operations no longer fail open on an empty token list.
-   **Security** Resolving an asset from an `assetId` alone (comments and subscriptions) no longer fabricates an empty asset record.
-   `GET /subscriptions` resolves each referenced asset once per listing rather than running a filtered asset-table scan for every subscription row, and no longer builds a single unbounded `OR` filter expression that returned `500` past a few hundred accessible subscriptions. Rows whose asset no longer resolves are omitted from the listing instead of being authorized against an empty record.
-   **Security** Constraint get, update, and delete no longer address a constraint by an unanchored ID prefix. A `constraintId` supplied on `/auth/constraints/{constraintId}` was matched with a `begins_with` scan, so a shortened or truncated ID resolved to, overwrote, or deleted every constraint whose ID merely started with the same characters — a single-character ID could delete a large share of all constraints and the API still reported success. Constraint rows are now addressed by the exact ID plus its own `#group#`/`#user#` denormalized items only; human-readable constraint IDs, including the deployment-seeded `initial_admin_*` constraints, remain fully readable, editable, and deletable.
-   **Security** An API key whose `expiresAt` value cannot be evaluated is now denied instead of being treated as never expiring, and a date-only expiration (`2026-12-31`) is read as UTC so it is compared rather than silently ignored.
-   **Security** The shared API response helpers no longer render the response body into their log lines, closing a path that wrote plaintext API keys and response PII to CloudWatch on every successful API response, bypassing `safeLogger`'s key-driven redaction.
-   **Security** Pipeline command construction hardened against injection: the RapidPipeline and ModelOps definition Lambdas shell-quote every filename, S3 key, and parameter interpolated into the container command.
-   **Security** User-controlled metadata field names are escaped before interpolation into OpenSearch `query_string` queries, and the escaper no longer double-escapes backslashes.
-   **Security / Web** Comment sanitization hardened with an explicit URL scheme allowlist (`http`, `https`, `mailto`) and forced `rel="noopener noreferrer"` on anchors, preventing `javascript:` URLs and reverse tabnabbing.
-   **Security / Web** The Application Load Balancer web distribution sets the same security response headers as the CloudFront one. It sent only the Content Security Policy, so `Strict-Transport-Security`, `X-Content-Type-Options` and `X-Frame-Options` were absent from the only distribution. The load balancer's own `Server` header is suppressed as well.
-   **Security** The web bucket grants read access only to this deployment's CloudFront distribution. A redundant statement is removed; the conditioned one the origin already adds is what remains.
-   **Security** `http://localhost:3001` is registered as an Amazon Cognito callback and logout URL only when `app.webUi.allowLocalhostAuthCallbacks` is enabled, which is off by default. The URLs were registered unconditionally on the user pool that serves application users, making a redirect target on an end user's own machine a valid destination for an authorization code. Enable the setting on a development deployment where a locally run web front needs to complete a federated sign-in. The setting is read only when Cognito SAML or OIDC federation is enabled.
-   **Security / CDK** The Amazon S3 interface endpoint the ALB forwards to carries the security group that holds its ingress rules. The endpoint was created with the load balancer's own group — which admits the internet on 443 — while the two rules written to restrict it to the load balancer were added to a separate group attached to nothing, so they governed nothing and the unused group read as an active restriction.
-   **Security** The CDK Nag IAM wildcard check applies to the infrastructure again. Each wildcard a CDK grant unavoidably produces now has its own justified entry naming why that shape cannot be narrowed, the few AWS APIs that publish no resource at all are covered by named, opt-in suppressions, and synthesis passes with the check enabled and nothing outstanding.
-   **Security** The per-asset Amazon SNS topic grant is pinned to the deployment's own account and Region.
-   **Security** The Amazon Cognito user pool client suppresses user-existence errors. The setting was left unspecified, which selects Cognito's legacy behavior: an unauthenticated sign-in attempt returned `UserNotFoundException` for an unknown username and a different error for a known one, so the endpoint distinguished registered accounts from unregistered ones to any caller. It is set on both the client and the configuration-update path, so a subsequent deployment cannot revert it.
-   **Pipelines** The NVIDIA Cosmos and GR00T pipelines scope their Step Functions callback permission to the deployment's account and Region, matching every other pipeline, instead of granting it on all resources.
-   **Pipelines** A Fargate pipeline container that stops making progress is now terminated by AWS Batch. Each of the Fargate jobs now carries the same limit as the orchestration that encloses it, so no run that completes today is affected; the limit takes effect only once the orchestration has stopped watching.
-   **Pipelines** An NVIDIA Isaac Lab training run that times out now records why. The pipeline's state machine allowed exactly as long as the task inside it, and because a preparatory state runs first, the task's deadline always fell after the execution's — so the execution-level timeout fired, bypassing the task's error handling, and neither the failure record nor the cleanup step ran.
-   **Pipelines** An NVIDIA Isaac Lab run no longer fails while waiting for GPU capacity. The tolerance is now 45 minutes, and the evaluation pipeline's own tolerance is raised to match the training pipeline's so the two remain correctly ordered: the inner timeout must expire first, because that is the path that stops the Batch job and reports a cause. A capacity wait longer than 45 minutes still fails.
-   **Pipelines** A failed NVIDIA GPU pipeline records what went wrong instead of only an exit code.
-   **Search** A failed OpenSearch index creation fails the deployment. The custom resource recorded the error and then reported success. An index that already exists is still a success, and the deferred-index-creation path is unaffected.
-   **Search** The reindex trigger runs after the index schema is deployed. The two custom resources declared no relative order, and CloudFormation may run them in either — so the reindexer could read the previous release's index names and fill an index nothing searches, or fail against an index that did not exist yet and roll the deployment back.
-   **Deployment** A bucket whose versioning setting cannot be read fails the deployment instead of being registered as unversioned. The probe treated a permissions failure as an answer, so an external bucket that _is_ versioned was recorded as not versioned — silently disabling file version history for its assets, and re-read to the same wrong answer by every later deployment.
-   **Security** The configuration handler reads only the two AWS Systems Manager parameters it needs, named individually, instead of every parameter in the account whose path contains the deployment name. Its permission to describe Amazon Location Service API keys is now granted only when Location Service is enabled, rather than in every deployment.
-   **CDK** A boolean configuration flag set to "off" resolves to off. Five flags were resolved from CDK context and environment variables without parsing the value, and every non-empty string is true in JavaScript — so `-c useWaf=false` and `AWS_USE_WAF=false` both read as **true** and enabled the feature they were set to disable. It affected FIPS endpoint selection, AWS WAF creation, OpenSearch reindex-on-deploy, the deferred index-schema deploy, and the VPC-stack context skip. `true`/`1`/`yes`/`on` and `false`/`0`/`no`/`off` are now read as written in either case, and any other value is reported and read as false. How a flag set in more than one place resolves is unchanged.
-   **CDK** A pipeline enabled with an unedited `ecrContainerImageURI` is rejected at synthesis, naming the placeholder still in place. The value is passed straight to the container registry, so the shipped placeholder deployed cleanly and failed only when a job tried to pull the image. Applies to the RapidPipeline ECS and EKS variants and to ModelOps, and only when the pipeline is enabled — all three ship disabled with the placeholder present.
-   **CDK** The Amazon Bedrock model ID is validated against the deployment's partition. A cross-Region inference-profile prefix is partition-specific, and the GovCloud and EU Sovereign Cloud templates pinned a commercial `global.` profile, which produced both a model that does not exist there and an IAM grant derived from the wrong name. A commercial prefix outside the commercial partition is now rejected, an empty value is rejected when the pipeline is enabled, and the restricted templates leave the field empty for the operator to set from the models offered in their partition. The grant now derives the underlying model name from any of the documented profile prefixes.
-   **CDK** An over-long Content Security Policy is rejected at synthesis with its measured size. The ALB carries the whole policy in one listener attribute, which Elastic Load Balancing limits to 1 KB, where a CloudFront response-headers policy allows 1,783 bytes. Past the limit the deployment failed naming a listener attribute rather than the policy. The shipped policy measures 858 bytes, and the remaining margin is reported in the infrastructure test output.
-   **Security** CDK Nag suppressions in the search/indexing stack are scoped to the specific IAM wildcards and custom-resource runtimes they cover, replacing a stack-wide match-all suppression.
-   Fixed a race where the S3 bucket-sync indexer could recreate an empty "ghost" asset after a permanent delete or archive; the sync now verifies the S3 object still exists and asset creation uses a conditional write. Workflow and pipeline creation use the same protection.
-   Hardened backend asset and file fetch and operation logic for correctness, large-scale behavior, and performance; the notable items follow.
-   No more silent truncation on large assets — file version history, archive-status detection, preview-file discovery, and asset-version listings page through all S3 objects and versions instead of only the first page.
-   Faster, constant-time archive checks — determining whether a single file or a specific version is archived uses one `HeadObject` call rather than listing object versions, removing a path that could misreport status for files with more than 1,000 versions.
-   Asset version create and revert no longer slow down on large assets, using a single paginated listing with parallel per-file work.
-   Asset records can no longer be corrupted by concurrent operations racing an archive or delete.
-   Corrected version-snapshot file listings — the detailed (non-basic) listing of a specific asset version no longer duplicates files that were archived and later unarchived, and presents snapshot files as captured in that version rather than in their current state.
-   Fixed file details never reporting a current-asset-version file mismatch — an inverted folder check left the per-version out-of-sync indicator null for every file, and the version-snapshot lookup is now keyed on the asset-relative path derived from the resolved S3 key rather than on the raw request path, so every accepted file-path spelling (with, without, or duplicating the leading slash, and the asset-prefixed form) resolves to the stored file key. The file details and versions views can now show that a file has drifted from the current asset version.
-   Unarchiving an asset now reliably removes the preview file's S3 delete marker; a previous narrow lookup could miss it and leave the preview archived.
-   Upload asset-type detection samples up to 1,000 files to classify an asset (empty, single file, or folder) instead of scanning the entire asset, removing added latency on very large uploads.
-   Whole-asset (prefix) downloads validate every object's extension and content type over a bounded worker pool with a pooled, retry-configured S3 client, so a large asset finishes inside the API Gateway timeout without sampling or skipping any object.
-   Fixed hardcoded `aws` partition in workflow Step Functions ASL generation and CDK import-pipeline ARNs, which broke non-commercial partition deployments, and fixed FIPS configuration applying FIPS endpoints only to the control plane rather than data plane operations as well.
-   Previous Amazon Cognito MFA checks were erroring and defaulting MFA validation to false; the check now uses `AdminGetUser` to fetch MFA status properly.
-   Fixed the login profile API returning a 500 for users without a stored profile record (for example, a first sign-in that reads the profile before it is written).
-   Standardized the system user across the backend to the single ID `SYSTEM_USER`, previously a mix of `SYSTEM` and other casings.
-   Comment deletion is now atomic (a single DynamoDB `TransactWriteItems`), the add-comment handler no longer masks non-`ClientError` exceptions as a 502, and the edit-comment 500 path returns the correct message.
-   `checkSubscription` returns a 400 for a malformed JSON body instead of a 502.
-   Asset-relationship (asset link) queries page through all results, so relationship trees and the alias-uniqueness check no longer silently truncate at one page.
-   The `validate()` input dispatcher no longer skips fields following an empty optional field, and the `BOOL` validator rejects non-boolean values.
-   Subscription, comment, config, and email handlers copy the shared response template per invocation instead of mutating a module-global dict, preventing status and body leakage across warm Lambda invocations.
-   Concurrent OAuth2 token refreshes are coalesced onto a single request, avoiding spurious re-logins when an identity provider rotates refresh tokens.
-   Fixed reserved S3 prefix and `*.previewFile.*` handling across backend handlers, which previously over- or under-excluded files from indexing and sync, and fixed S3 bucket indexing for filenames with certain special characters.
-   Fixed the OpenSearch reindexer silently skipping files without `assetid`/`databaseid` S3 object metadata, including newly added buckets, and added checks for malformed metadata GeoJSON shapes that could break indexing.
-   S3 file versions now appear correctly for previously archived file versions when viewing an unarchived file.
-   Fixed feature flags remaining in DynamoDB indefinitely after a feature was disabled between deployments; the custom resource now deletes the item when its CloudFormation resource is removed.
-   **CDK** Fixed a configuration check that prevented deploying without either an ALB or CloudFront, despite the error message saying it was allowed.
-   **CDK** The ALB web deployment now issues a temporary (`302`) redirect for `/api` routes instead of a permanent (`301`). Browsers cached the uncacheable-by-omission `301` against the API Gateway hostname from their first visit, so after a redeploy the application failed at startup with `Failed to fetch` until the browser cache was cleared.
-   **Web / CDK** The `web` and `infra` lockfiles now record the rolldown, esbuild, and lightningcss native bindings for Linux and macOS alongside Windows. npm captures only the binaries matching the platform that generated the lockfile, so a Linux build failed with `Cannot find native binding`; the bindings are declared as `optionalDependencies` so each machine still installs only its own.
-   **Web** Fixed expired auth sessions leaving the app rendering as logged in while every API call returned 403. A mode-agnostic session manager validates on page load, refreshes on an expiry-aligned timer, and revalidates when the tab regains focus, returning the user to login with a notice and back to their page after signing in.
-   **Web** Fixed Cesium viewer camera and rendering behavior by adapting controls to the content type — local tilesets use turntable controls around the model with terrain collision and the globe/skybox disabled, while geo-referenced tilesets keep globe-level controls and geographic context.
-   **Web** Fixed all viewers rendering 20-30 pixels under the modal header in the pop-up file viewer, and the Potree viewer leaving orphaned color-picker and profile-window elements on other pages after closing.
-   **Web** Fixed duplicate `POST /auth/routes` calls on page load by batching the route table and side navigation permission checks into one call with per-session caching.
-   **Web** Fixed the asset relationships component silently swallowing API errors; failed reads now surface an error alert with retry and failed create/delete operations report errors. Pipeline create/update and other list-page components also display API errors now.
-   **Web** Asset and file search now refresh preview thumbnail caches when previews change without a full page reload, and the landing page image loads reliably on first load.
-   **Web** Asset file components no longer offer to export or view files for an asset marked not distributable — the Export menu, View File button, viewer links, and preview thumbnail are hidden, since the API already refused these requests.
-   **Web** Fixed the broken Jest test infrastructure and updated stale tests that no longer matched current component behavior.
-   **CLI** Fixed `vamscli assets archive` failing with "Request body is required" when no `--reason` was given, and `tag`/`tag-type` create and update failing API validation from improper input formatting.
-   **Pipelines** RapidPipeline writes its per-execution config to a namespaced S3 key so concurrent runs no longer collide on a shared `rp_config.json`.
-   **Pipelines** Fixed the Potree PDAL container build failure caused by internal dependency upgrades.
-   **Pipelines** Fixed NVIDIA Cosmos Transfer and Predict container build failures from the upstream Python 3.13 upgrade (pinned to 3.10).
-   **Pipelines** Fixed container deadlocks in the NVIDIA Cosmos and GR00T pipelines caused by output buffer overflows during HuggingFace downloads.
-   **Pipelines** Fixed a stale NVIDIA Cosmos Predict v1 reference in CDK that forced an unnecessary local Docker build instead of using the published image.
-   **Pipelines** CRLF line endings are now stripped programmatically from the NVIDIA Cosmos, GR00T, and Isaac Lab entrypoint files to account for different authoring platforms.
-   **Pipelines** Fixed the Gaussian Splat open pipeline function lacking permission to send task-failure callbacks when an error is caught.
-   **Pipelines** Fixed the NVIDIA Isaac Lab Training pipeline not resolving asset input locations, so relative paths in submitted configuration files now work.
-   **Pipelines** Fixed the NVIDIA Isaac Lab Training pipeline using a fixed Step Functions state machine name, which blocked multiple VAMS deployments in the same Region.
-   **Pipelines** Raised the GenAI Metadata Labeling pipeline's primary Lambda timeout from 5 to 15 minutes for larger 3D models, and fixed broken cross-reference links in the documentation.
-   **Pipelines** The RapidPipeline EKS pipeline can now read the files it is asked to process when KMS CMK encryption is turned on. Its Kubernetes pod was granted access to the asset buckets but not to the KMS CMK key those buckets are encrypted with, so every run failed while downloading its input — reported as a container error rather than a permissions one. The ECS variant of the same pipeline was unaffected.
-   **Pipelines** A RapidPipeline EKS job-submission failure now reports what actually went wrong.
-   **Security / CDK** The RapidPipeline EKS cluster's Kubernetes API endpoint is private, reachable only from within the deployment VPC rather than from the internet. Its only clients are the pipeline's own functions and its managed node group, all of which run in the same private subnets, and VAMS grants their security groups inbound access to the cluster's network interfaces. The Kubernetes client no longer treats the resulting private address as a misrouted request to be worked around, which had made the log during an unreachable endpoint point at DNS and NAT routing rather than at the actual cause.
-   **Security** The RapidPipeline EKS pipeline's Lambda function is no longer a Kubernetes cluster administrator.
-   **Security** The RapidPipeline EKS node group's instance role no longer carries access to asset data. The pipeline's pods use a dedicated service-account role that holds those permissions, so the node role now carries only what is needed to join the cluster, run networking, and pull the container image.
-   **Pipelines** The GPU pipelines' shared model cache on Amazon EFS is now actually mounted. Nothing failed and no job reported an error — every run simply restored its whole model cache from Amazon S3 and uploaded it again afterwards, on GPU instance time, and no instance could reuse another's weights. The environments now use the Batch service-linked role and refer to a pinned template version. Affects the Cosmos Predict, Cosmos Reason, Cosmos Transfer and GR00T fine-tuning pipelines.
-   **Pipelines** The four Cosmos pipelines no longer share one model-cache directory. Each pipeline now keeps its weights in its own directory, named for the same location it backs up to. The cache check also stops at the first weight file it finds instead of listing every file in the cache, and each run now records whether its cache is on shared storage.
-   **Pipelines** The Cosmos Reason pipelines accept video input only (`.mp4`, `.mov`). The model reads still images, but the upstream Cosmos Reason utilities fail when writing an image input back out after inference, so an image run loaded the model, completed its reasoning, and then failed. An image is now refused when the run is submitted rather than after a GPU instance has been provisioned. When the Reason container's model process does fail, the error it reports now names the cause instead of only an exit code.
-   **Security** The VAMS CLI no longer writes credentials to its rotating log file, and the files that can hold one are created readable only by their owner.
-   **Security** The web application's Content Security Policy allows the inline scripts in `index.html` by per-script SHA-256 hash instead of the `'unsafe-inline'` keyword, so an injected inline script is blocked. `'unsafe-inline'` is now added only when the Physna Sync add-on is enabled, because that viewer renders provider-authored HTML in a `blob:` iframe that inherits the page's policy. A policy cannot use hashes and `'unsafe-inline'` together, so enabling that add-on relaxes inline-script protection for the whole document — see the [Content Security Policy section](https://awslabs.github.io/visual-asset-management-system/architecture/security).
-   **Configuration** An external asset bucket registered in a Region other than the deployment Region is now rejected during configuration validation, naming both Regions. Amazon S3 requires an event-notification destination to be in the bucket's Region and VAMS creates its notification topics in the deployment Region, so this combination previously failed part-way through deployment with an opaque `PutBucketNotificationConfiguration` error and rolled the stack back. Cross-**account** external buckets remain supported.
-   **Security** The asset-bucket listing (`GET /buckets`) is scoped to administrator roles. The listing returns the whole registry — every bucket's name and prefix — and `bucket` is not a constraint object type, so no per-bucket constraint can narrow it and the API route grant is the only control available. The `database-user`, `database-readonly`, and `global-readonly` templates and the seeded default read-only role no longer grant the route; the `database-admin` template and the seeded default administrator role keep it. **Stored constraints are not reconciled on deployment**, so a non-administrator role authored from an earlier template keeps its grant until that constraint is re-authored or the template re-imported — see [Bucket listing route scoped to administrators](https://awslabs.github.io/visual-asset-management-system/deployment/update-the-solution#bucket-listing-route-scoped-to-administrators).
-   `GET /buckets` honours its pagination parameters. `maxItems`, `pageSize`, and `startingToken` were read from the wrong argument, so every request scanned a fixed page size and a supplied `startingToken` was discarded — a paginating client re-received page one with the same `NextToken` instead of advancing, which is why `vamscli database list-buckets --page-size`/`--starting-token` had no effect. The default page size is unchanged, and a malformed `startingToken` now reports the token rather than a generic listing failure.
-   **Security** The login profile route (`/auth/loginProfile/{userId}`) enforces API-level (Tier 1) authorization like every other route, so a role can withhold it and one user can no longer read another's login profile. The shipped permission templates and the seeded default roles grant it on both GET and POST — the web application POSTs the route during sign-in to record the user's email address and the CLI GETs it to validate a session, so a role granted only one of the two methods fails in one client.
-   **Security** Amazon Cognito app client settings are no longer reset by the post-deployment client update. `UpdateUserPoolClient` replaces the whole client configuration, and the update that writes the sign-in and sign-out URLs omitted the token lifetimes and authentication flows — so refresh token validity reverted from the intended 24 hours to the Amazon Cognito default of **30 days**, access and ID token validity reverted to 1 hour regardless of `credTokenTimeoutSeconds`, and `ALLOW_USER_PASSWORD_AUTH` was dropped where `useUserPasswordAuthFlow` was enabled. Both client-update paths now send those parameters from one shared definition. Existing deployments apply the intended lifetimes on the next deployment; already-issued refresh tokens keep the validity they were issued with.
-   **Search** The record-type discriminator is written to every indexed document as `str_rectype` (`"asset"` or `"file"`). It was declared on both OpenSearch document models with a leading underscore, which Pydantic v1 excludes from a model's fields entirely, so no indexed asset or file document carried a record type even though both index mappings and the API reference declared one. A `query_string` filter on the documented field was also discarded by the query builder rather than applied, returning the full unfiltered result set; a filter on `str_rectype` is now honoured (use `str_rectype.keyword` for an exact match). The field appears in `GET /search` and `GET /search/simple` hit sources, as a column in `vamscli search` table and CSV output, and backs the asset/file classification in the search map view. Existing indexes acquire the field on reindex.
-   **Security** Production web builds no longer emit JavaScript source maps. `build.sourcemap` in `web/vite.config.ts` is now `false`, so a build no longer publishes 308 `.map` files (62.9 MiB, about a quarter of the deployed bundle) alongside the chunks. Existing deployments have their previously published maps removed on the next deployment. Source maps remain available for local debugging via `vite build --sourcemap`.
-   **CDK** The website deployment Lambda function that uploads the built web application allocates 4 GiB of ephemeral (`/tmp`) storage instead of the 512 MiB Lambda default, on both the Amazon CloudFront and the Application Load Balancer hosting paths. That function downloads the web bundle archive and expands it under `/tmp`, so it holds both copies at once — a stock build is already about 255 MB, roughly half of it viewer plugins — and adding viewer plugins could exceed the default and fail `cdk deploy` inside the bucket deployment custom resource with an error that did not point at disk space.
-   **Pipelines** The RapidPipeline EKS `eksClusterVersion` configuration value is used to create the cluster. The Kubernetes version was hardcoded, so setting the field in `config.json` had no effect and reported no error; the value is also validated as an Amazon EKS minor version (`1.NN`) at configuration time rather than failing when the cluster is created.
-   **External Plugin** The NVIDIA Isaac Sim and Esri ArcGIS Pro connectors no longer pass the Amazon Cognito password or override token to the VAMS CLI as a command-line argument, where any other account on the machine could read it from the operating system process table for the lifetime of the login. Both now pipe the credential to the CLI's standard input.
    -   **Security / External Plugin** The Esri ArcGIS Pro connector no longer renders the CLI argument string into its trace and exception messages.
-   **Web** The metadata schema editor warns when an existing schema field is changed from optional to required, naming the affected fields and stating that metadata changes are rejected for records that do not yet hold a value for them. Adding a new field or leaving an already-required field unchanged produces no warning, and the warning does not block saving. Required-field enforcement remains retroactive by design; [Metadata and schemas](https://awslabs.github.io/visual-asset-management-system/concepts/metadata-and-schemas) documents the resulting block, the error returned, `GLOBAL` schema scope, and the remediation options.

### Chores

-   **CDK** CloudWatch log retention is stated consistently as one year. The `LogRetentionAspect` applies the period to every log group and overwrites any value declared on an individual construct, so the construct-level declarations now match what is applied rather than implying a longer period that never took effect. To retain audit records for longer — a common requirement in regulated environments — pass a longer `logs.RetentionDays` value at the single `new LogRetentionAspect(...)` call site in `core-stack.ts`; note that it applies to every log group, including high-volume Lambda execution logs. Documented in the [audit logging guide](https://awslabs.github.io/visual-asset-management-system/developer/audit-logging).
-   **CDK** The Content Security Policy inline-script hashes are generated rather than hand-maintained. `web/scripts/cspInlineScriptHashes.js` emits them from the built HTML, and a test recomputes them from `web/index.html` so that editing — or merely reformatting — an inline script block fails a test instead of silently breaking the page in the browser.
-   **Documentation** Corrected the external Amazon S3 bucket guide: VAMS **merges** its event-notification entries with any already on the bucket rather than replacing them (which is why the bucket policy must grant `s3:GetBucketNotification`), and the external AWS KMS key is granted to the VAMS Lambda and pipeline roles automatically when `bucketKmsKeyArn` is set. Both statements previously said the opposite, the second contradicting the same page's own setup steps.
-   **Documentation** Documented the `/auth/api-keys` routes as administrative and admin-equivalent in both API documentation sources. A presented API key acts as the VAMS user it is bound to and carries that user's roles, and `POST /auth/api-keys` binds the new key to any `userId` supplied in the request — including an administrator's — so access to those routes is equivalent to full administrative access. Of the default roles only `admin` reaches them; `basicReadOnly` is granted the self-service `/auth/user/api-keys` routes, which are restricted server-side to the caller's own keys.
-   **Documentation** Added a restricted-partition caveat to the NVIDIA Cosmos and GR00T pipeline pages and the configuration reference: configuration validation checks only that an enabled model variant names a non-empty `instanceTypes` array, so AWS GovCloud and AWS European Sovereign Cloud deployments should review the configured GPU instance types and evaluate the pipeline before enabling it.
-   **Documentation** The developer guide records how asset notification subscribers are resolved to email addresses and that a subscriber is **not** required to be a VAMS user — a shared mailbox or resource account is accepted when it is email-shaped — along with what the two authorization tiers do and do not cover for recipients, and where an arbitrary recipient list can be authored. See the [backend development guide](https://awslabs.github.io/visual-asset-management-system/developer/backend#notification-subscriptions).
-   **CDK** Deployments that authenticate with Amazon Cognito report a warning at synthesis time stating that the seeded bootstrap administrator role is created without MFA required, naming the account and the remediation. The seeded role's `mfaRequired: false` default is unchanged, because MFA enrollment for the bootstrap account is a larger operational step than this release takes on.
-   **CDK** AWS Batch GPU compute environments now specify the Amazon Linux 2023 NVIDIA-accelerated AMI (`ECS_AL2023_NVIDIA`), because AWS Batch blocks new Amazon ECS compute environments using Batch-provided Amazon Linux 2 AMIs and every GPU pipeline previously failed to create with `Amazon Linux 2 is end-of-life`. **On upgrade this replaces each GPU compute environment**, so drain in-flight GPU pipeline jobs before deploying.
-   **CDK** Added a second backend API nested stack (`ApiBuilder2NestedStack`) to keep both API stacks clear of the two per-template CloudFormation ceilings — 500 resources and a 1 MB template body, the latter of which the primary stack fills roughly twice as fast because its Lambda functions carry long inline IAM policies. New API endpoints should be added there. Only self-contained domains are moved — the workflow functions stay in the primary stack because the workflow IAM role's path-derived name is baked into existing state machines. The split does not relieve the API Gateway resources-per-REST-API quota, which both stacks share through one `SpecRestApi`.
-   **CDK** Replaced three stack-wide Lambda CDK Nag suppressions with a per-Lambda helper called from every builder plus a targeted framework-resource pass, cutting the per-resource metadata footprint substantially.
-   **CDK** DynamoDB tables are now `RETAIN` on teardown instead of deleted to prevent accidental data loss; tables are uniquely named per deployment so retained tables do not collide on redeploy. The VAMS-generated AWS KMS customer-managed key that encrypts them is retained the same way, so retained tables and Amazon S3 buckets stay decryptable after `cdk destroy`. The key carries no alias and is addressed only by its generated key id, so a retained key does not collide with the key a redeploy creates; deleting it is an explicit final operator step in the [uninstall guide](https://awslabs.github.io/visual-asset-management-system/deployment/uninstall).
-   **CDK** The S3 asset bucket config and bucket config DynamoDB tables now store default bucket values, used for pipeline, workflow, and execution storage information.
-   **Configuration** Removed `app.pipelines.usePreviewPcPotreeViewer.sqsAutoRunOnAssetModified`, which subscribed a per-bucket Amazon SQS queue to each asset bucket's object-created notifications and invoked the Potree point cloud conversion directly, outside the workflow model.
-   Bumped the minimum supported Node.js version for development and build tooling from 20.18.1 to 22.22.3 to address the AWS SDK for JavaScript v3 `NodeVersionSupportWarning`, updating `.nvmrc`, all `package.json` engines, `@types/node`, and documentation references.
-   Bumped the Lambda Node.js runtime from `NODEJS_20_X` to `NODEJS_22_X` (`LAMBDA_NODE_RUNTIME` in `infra/config/config.ts`), affecting all Node-based Lambdas.
-   Bumped backend and base Lambda layer `boto3`/`botocore` from 1.34.x to 1.43.45 to support the new `aws-eusc` partition.
-   Updated various package dependencies across the solution.
-   **Web** Bumped Vite `build.target` and `optimizeDeps.esbuildOptions.target` from `es2020` to `es2022`.
-   **Web** Updated the PlayCanvas Gaussian Splat viewer's bundled PlayCanvas engine (`customInstalls/playcanvas`) from `2.17.2` to `2.19.6`.
-   **Web** Removed the unused legacy `web-ifc` dependency from the core `web/package.json`.
-   **Web** Removed the legacy Asset Selector component and file type constants that were no longer used.
-   **Web** Removed the "Limited Search Mode" informational alert on the asset search page when OpenSearch is not deployed.
-   **Web** The application footer now displays the backend VAMS version, fetched from the anonymous `/api/version` endpoint (no authorization required).
-   Backend and web work to break reserved keywords and variables — S3 prefixes, extension names — out into shared constants files.
-   Pipeline and workflow backend logic now checks that IDs are unique across all databases including GLOBAL, preventing overlap with older references that omit the database id.
-   Role lookups for claims (logging only) are performed entirely in the REST API custom Lambda authorizer, while authorization separately looks up roles for Casbin checks. See the new [Authentication and Authorization Flow](documentation/docusaurus-site/docs/developer/security.md) developer guide section.
-   The `authLoginProfile` handler was updated to the current API handler standard for request/response models, validation, and error handling.
-   The `configService` handler was updated to the current API handler standard for request/response models, validation, and error handling.
-   Documentation updated to inventory the named CloudWatch log groups (`/aws/vendedlogs/...`) and the S3 web access logs bucket VAMS creates, with the uninstall procedure now covering retained, deterministically named log groups (including conditional ones such as CloudTrail and VPC flow logs) that must be removed before redeploying with the same configuration name and account.
-   Updated the root README to point all documentation references to the documentation website rather than the source markdown files.
-   Updated documentation links to the new AWS physical-ai blog locations (spatial blogs migrated to the physical-ai tag).

### Known Outstanding Issues

-   With multiple S3 bucket support, identical assetIds across different buckets/prefixes in different databases can cause lookup conflicts in comments and subscriptions. This only occurs with manual S3 changes, as VAMS-generated assetIds use unique GUIDs.
-   Pipeline metadata inputs have a size limit when sent to ECS pipelines. Assets or files with extensive metadata may exceed the 8K character ECS JSON input limit. A future pipeline overhaul will convert metadata input to a file-based approach.
-   For assets with hundreds to thousands of files or very large files (TB-size), some API operations may time out while the Lambda continues processing (up to 15 minutes). The API Gateway integration timeout is configurable via `app.api.apiGatewayRest.apiGatewayTimeoutTime` (default 29 seconds, maximum 300), which raises this ceiling on accounts that have an approved **Integration timeout** quota increase.
-   The Amazon Cognito MFA check requires the API Gateway authorizer to run outside the VPC. VAMS does not create Amazon Cognito VPC interface endpoints, so when Lambda functions run in the VPC (`useForAllLambdas`) the authorizer has no path to Amazon Cognito; the Cognito MFA check is disabled (`COGNITO_AUTH_ENABLED = FALSE`) and `mfaRequired` on a role has no effect.

### Troubleshooting

-   If AWS CloudFormation fails to delete a VPC subnet on upgrade (the previously unused third Availability Zone), the subnet still holds elastic network interfaces from shared interface VPC endpoints or VPC-attached Lambda functions. Follow the subnet-deletion recovery procedure in the [networking troubleshooting guide](https://awslabs.github.io/visual-asset-management-system/troubleshooting/common-issues).
-   If a client can no longer reach the API after upgrading, it is likely registered against the old HTTP API endpoint. Re-run `vamscli setup` (or update the stored base URL) against the new REST API endpoint, or point it at the CloudFront/ALB `/api` URL.
-   If the web application fails at startup with `Failed to fetch` after upgrading an ALB deployment, clear the browser cache once. Earlier releases issued a permanent (`301`) redirect for `/api` routes that browsers cached indefinitely against the previous API Gateway hostname.
-   If receiving web build or infra CDK errors in upgraded projects, re-run `npm install` in the `web` and `infra` directories. Persistent build errors may require clearing the `node_modules` cache.

## [2.5.3] (2026-08-03)

### Bug Fixes

-   **Web** Fixed `npm install` failing in `web/` with `npm error code EOVERRIDE / Override for fast-xml-parser@5.10.1 conflicts with direct dependency` ([#297](https://github.com/awslabs/visual-asset-management-system/issues/297)). `fast-xml-parser` was declared twice in `web/package.json` — once as a direct dependency and once in `overrides` — which could deadlock resolution against a stale lockfile. Both entries were removed: VAMS does not import `fast-xml-parser` directly, the AWS SDK no longer depends on it, and `@aws-amplify/storage` now requires `^5.7.2`, so the v4 pin was holding the package below its dependents' supported range.

### Chores

-   Updated package dependencies across all 11 npm packages (root, `web`, `infra`, documentation site, and the seven `web/customInstalls` viewer packages) to resolve npm audit findings. Root, `infra`, documentation site, and six of seven viewer packages are now clean; `web` went from 17 findings to 8 and no longer reports any high-severity findings.
-   **Web** Upgraded `jodit-react` to `^5.3.21`, resolving high-severity mutation XSS and prototype pollution findings in the `jodit` editor used by the asset comments feature. The direct `jodit` pin was dropped in favor of the transitive version supplied by `jodit-react`.
-   **Infra** Bumped the `aws-cdk` CLI floor to `^2.1134.0` to match `aws-cdk-lib` 2.263.0. The dependency update raised the cloud assembly schema to version 54, which the previously pinned CLI (`^2.1111.0`) could not read, causing `cdk synth` to fail.
-   **Documentation** Aligned all `@docusaurus/*` packages to 3.10.2 so the core, preset, theme, and type packages remain on a single matching version.
-   Bumped the base `package.json` version to 2.5.3 — it had remained at 2.1.0 across several releases and now tracks the VAMS release version.
-   Bumped `VAMS_VERSION` (`infra/config/config.ts`) and VamsCLI version (`tools/VamsCLI/vamscli/version.py`) to 2.5.3 — these were not updated during the 2.5.2 hotfix and remained at 2.5.1
-   Added the missing 2.5.2 entry to the documentation revision history

### Known Issues

-   Pipelines that rely on the Amazon Linux 2 (AL2) image type for Amazon ECS/AWS Batch containers may not work, as AL2 reached end of support on July 31st. This will be fixed in v2.6.0.

## [2.5.2] (2026-06-19)

### Bug Fixes

-   Fixed a Casbin authz implementation bug that could allow injecting additional policies through field values that would be regex evaluated. Low impact as Casbin policies are only able to be set by admins by default. Added additional backend tests for this case.
-   Fixed a createAsset API bug that allowed specifying an optional S3 bucket key location without proper checks that it belonged to the provided database IDs default S3 bucket and prefix path, that an asset didn't already exist with that S3 key path, and had weak validation checks on the path provided.
-   Fixed latent defect of backend test framework not being updated with changes from v2.5, causing some test failures.

### Chores

-   Added default GitHub issue and PR request templates
-   Updated documentation for authorization to account for bug fixes and clarifications
-   Update several package dependency versions to fix new npm audit findings

## [2.5.1] (2026-04-23)

### Bug Fixes

-   **Web** Fixed file upload path construction in ModifyAssetsUploads — files uploaded to a subfolder now correctly include the full folder path (e.g., `/textures/USD/texture.png` instead of `/texture.png`)
-   Fixed permanent file deletion not cleaning up DynamoDB version snapshot records — re-uploading a file at the same path no longer shows stale version history from previously deleted files
-   Fixed S3 version deletion not paginating — permanent delete now removes all S3 object versions even when a file has more than 1000 versions
-   Fixed permanent asset deletion not paginating DynamoDB queries for version files and metadata version cleanup — assets with large numbers of versions or files now fully delete all related records
-   Fixed `raise authorization_error()` across multiple backend handlers (assetService, metadataSchemaService, userRolesService, createRole, tagService, createTag) — was raising a dict instead of returning an API response, causing "exceptions must derive from BaseException" errors
-   **Web** Fixed version switching across many viewer plugins — when switching to a different file version — the viewer now correctly fetches and displays the selected version instead of showing the latest only
-   **CLI** Fixed CLI asset download command capping at ~100 files — now paginates through all API results when downloading whole assets or folders
    -   Improved CLI asset download performance — presigned URL generation and file downloads now run concurrently via streaming pipeline instead of sequentially
    -   `--recursive` flag on `assets download` now defaults `--file-key` to `/` when not specified, enabling `vamscli assets download /path -d db -a asset --recursive` to download all files
-   **CLI** Fixed CLI file upload progress display erasing terminal scrollback history — progress now tracks and clears only the lines it printed
-   **CLI** Fixed CLI API retry messages polluting outputs when using --json-output parameters
-   Fixed NVIDIA pipeline CodeBuild ECR repositories failing to delete when disabling pipelines — added `emptyOnDelete: true` to Cosmos and Gr00t CodeBuild ECR repos so images are automatically cleared before CloudFormation deletion

### Chores

-   AI Steering Documents: Fixed incorrect `raise authorization_error()` pattern in (Cline workflows, Kiro steering) to use `return authorization_error()` consistent with backend conventions
-   AI Steering Documents: Removed direct version references that were previosly hard-set to a particular version (like v2.5.0)
-   Updated README and documentation to add Finch and Podman alternatives to Docker for the CDK build process
-   Updated .gitleaksignore with false positive findings
-   Update several package dependency versions to fix new npm audit findings

## [2.5.0] (2026-04-21)

### Major Change Summary:

-   Documentation Overhaul - Entire documentation base refactored, implemented as markdown and static website
-   Website Overhaul - Migrated to Vite build framework, AWS Amplify V6 Gen2 SDK, and added dark/light theme support (dark is now the default)
-   New Experimental USD Web Viewer - Needle USD 3D WASM experimental viewer with dependency chain loading for .usd, .usda, .usdc, .usdz files
-   New ThreeJS 3D and CAD STP Web Viewer - Open-source ThreeJS viewer for .gltf, .glb, .obj, .fbx, .stl, .ply, .dae, .3ds, .3mf, .stp, .step, .iges, .brep files with dependency chain loading, scene graph support, and optional LGPL-licensed CAD support; now the primary viewer for common mesh types
-   New Pipeline Type Support - Pipelines and workflows now support SQS and EventBridge execution types alongside Lambda, enabling integration with external processing systems
-   New 3D/Point Cloud Preview Thumbnail Pipeline - CPU-based headless rendering pipeline generating animated GIF or static image previews from 3D mesh, point cloud, CAD, and USD files
-   New External Tool Integrations (Experimental) - Open-source VAMS connector plugins for NVIDIA Isaac Sim (Omniverse Kit extension) and Esri ArcGIS Pro (.NET add-in) via the VAMS CLI
-   New Physical AI NVIDIA Cosmos Inference Pipelines - GPU-accelerated world generation, video analysis, and video transformation using NVIDIA Cosmos foundation models with HuggingFace model integration and metadata-driven prompts. Predict 2.5 (Text2World/Video2World, 2B/14B), Reason v2 (VLM video/image analysis, 2B/8B), and Transfer v2.5 (control-signal video transformation, 2B). Configurable per-model GPU instance types across G and P EC2 instance families. AWS CodeBuild is an optional container deployment method for cloud-based builds.
-   New Physical AI NVIDIA GR00T Fine-Tuning Pipeline - GPU-accelerated fine-tuning of NVIDIA's GR00T-N1.5-3B embodied AI foundation model for robotics applications. Supports LoRA and full fine-tuning on user-provided datasets in LeRobot v2.1 format with configurable training hyperparameters. Model checkpoints stored back to VAMS assets. Configurable GPU instance types (g6e.4xlarge for LoRA, g6e.12xlarge for full fine-tuning).
-   New Database Metadata and Location Map Support - Database metadata management on the website with location service mini-map display option
-   Website Asset and File Page Refinement - Refined asset and file viewing page layouts; added asset preview thumbnail to top details section
-   Enhanced Asset Versions - Version aliasing, archive/unarchive, version details editing, metadata/attribute versioning, and revert with metadata restoration
-   Enhanced File and Download Functionality with Asset Versions - Version-aware download APIs with file and asset version query parameters, updated file viewers for versioned file retrieval, and web version selector filtering for files and metadata
-   Enhanced Cross-Database Support - Cross-database asset linking and file copying capability
-   New Cognito User Management - Web UI, API, and CLI for managing Cognito users without AWS Console access; includes add/update/remove/reset password operations and a new admin navigation page (enabled only when Cognito authentication is active)
-   New API Key Management - Complete API Key system with creation through API/CLI/web UI, user ID impersonation with role assignment, upstream/downstream application integration, and admin web interface for key management
-   New Permission Constraints Templating - Bulk-import permission constraints from JSON templates with server-side variable substitution, pre-built templates for common profiles (database-admin, database-user, database-readonly, global-readonly, deny-tagged-assets), CLI import command, automated deployment tool, and comprehensive Permissions Guide documentation

### ⚠ BREAKING CHANGES

Asset versions have database table changes that require running migration scripts to add new column data, preventing system-wide conflicts with assets that share similar IDs across databases.

The website overhaul may cause a high number of merge conflicts for forked repositories due to extensive file renames and refactors. Merging should be conducted cautiously.

**Recommended Upgrade Path:** Run the upgrade script to migrate permission constraints from the old table to the new one if custom constraints were added or modified beyond VAMS defaults: `infra\deploymentDataMigration\v2.4_to_v2.5\upgrade`

### Features

-   Overhauled all documentation files in `/documentation` to now implement much more information about VAMS in both markdown and Docusaurus framework website
    -   Now includes VAMS Core Concepts, User Guides, API documentation, Architecture breakdowns, and more
    -   CLI documentation is now converted to a combined documentation location
    -   Primary README overhauled for new documentation and formatting
    -   New Gitlab and Github workflow to publish documentation static website to pages feature
-   **Web** Overhauled website to use Vite build framework, AWS Amplify V6 Gen2 SDK, and dark/light theme support (dark is now the default). This required refactoring the API call and cache system across all web files.
    -   Added additional website customization configuration to `config.ts`
    -   Refactored most .js files to .ts or .tsx
    -   Consolidated all API calls into service files under web /services/ folder
    -   Fixed filenames that followed outdated naming conventions
    -   Removed deprecated pages/files that were no longer referenced
-   **Pipeline** Pipelines and workflows now support launching through SQS and EventBridge in addition to the existing Lambda option. See `DeveloperGuide.md` for implementation details.
-   **Pipeline** Added Preview 3D Thumbnail pipeline (`usePreview3dThumbnail`) that generates animated GIF or static image preview thumbnails from 3D files. Supports mesh formats (PLY, STL, OBJ, GLB, GLTF, FBX, DRC), point clouds (LAS, LAZ, E57, PTX, PCD, FLS, FWS), CAD files (STP, STEP), and USD files (USD, USDA, USDC, USDZ). Uses CPU-based headless rendering via PyVista/VTK with Xvfb in an AWS Batch Fargate container. Disabled by default due to restrictive library licenses [LGPL, etc.] (see `NOTICE.md`).
    -   100 GB maximum input file size with pre-download S3 size validation (can be extended but may require an EFS Fargate implementation)
    -   Configurable `overwriteExistingPreviewFiles` pipeline input parameter to control preview file overwrite behavior
    -   Auto-registration with VAMS pipelines and workflows via CDK custom resources
-   **Pipeline** Added Physical AI NVIDIA Cosmos Predict 2.5 inference pipeline for GPU-accelerated world generation
    -   Text2World: Generates videos from text prompts using asset metadata
    -   Video2World: Generates videos from image/video inputs using file metadata, with auto-detection of input frames (1 for images, 9 for videos)
    -   Supports 2B and 14B models (v2.5) for both Text2World and Video2World. 2B models run on g5/g6e.12xlarge instances; 14B models require g6e.48xlarge (8x L40S) or p5.48xlarge (8x H100) instances with 8-GPU context parallelism.
    -   Shared infrastructure: Common EFS model cache + S3 backup for all Cosmos pipelines, with lazy-load from HuggingFace on first run (shared with Transfer and Reason)
    -   CDK configuration with per-model enable/disable, configurable GPU instance types with BEST_FIT_PROGRESSIVE fallback, warm/cold instance support, and HuggingFace token stored in AWS Secrets Manager; additional pipeline input configuration for performance tuning available, see documentation.
-   **Pipeline** Added NVIDIA Cosmos Reason v2 inference pipeline for Vision Language Model (VLM) video and image analysis
    -   Analyzes video/image content and generates text-based analysis, captions, descriptions, and reasoning
    -   Supports Cosmos-Reason2-2B (~5GB) and Cosmos-Reason2-8B (~16GB) models based on Qwen3-VL architecture
    -   Supports spatial-temporal reasoning, physics understanding, temporal event localization, and embodied reasoning use cases
    -   Prompt-driven analysis via COSMOS_REASON_PROMPT file metadata or workflow inputParameters
    -   Output: JSON file with text analysis
    -   2B model runs on g5/g6e.12xlarge instances (24GB+ VRAM per GPU); 8B model requires g6e instances (32GB+ VRAM per GPU, g5 A10G 24GB is insufficient)
    -   Shared infrastructure: Uses common Cosmos EFS model cache and HuggingFace token (shared with Predict and Transfer)
-   **Pipeline** Added NVIDIA Cosmos Transfer v2.5 inference pipeline for video transformation with control signal conditioning
    -   Transforms videos with style transfer and content transformation using control signals
    -   Supports Cosmos-Transfer2.5-2B model (~20GB) for video-to-video transformation
    -   Control signals: edge (Canny detection), depth (VideoDepthAnything), segmentation (GroundDino+SAM2), visual blur (bilateral Gaussian)
    -   Auto-compute control signals from source video or provide pre-computed signals via COSMOS_TRANSFER_CONTROL_PATH metadata
    -   Prompt-driven transformation via COSMOS_TRANSFER_PROMPT file metadata or workflow inputParameters
    -   Output: Transformed MP4 video
    -   Requires g6e.48xlarge (8x L40S 48GB) or p5.48xlarge (8x H100 80GB) instances (65.4GB VRAM minimum). p4d instances are not supported due to CUDA driver incompatibilities.
    -   Shared infrastructure: Uses common Cosmos EFS model cache and HuggingFace token (shared with Predict and Reason)
-   **Pipeline** Added NVIDIA GR00T N1.5-3B fine-tuning pipeline for embodied AI and robotics applications
    -   Fine-tunes NVIDIA's GR00T-N1.5-3B foundation model on user-provided datasets in LeRobot v2.1 format
    -   Supports LoRA (parameter-efficient, single GPU) and full fine-tuning (multi-GPU) modes
    -   Configurable training hyperparameters via `gr00t_config.json` in the asset or pipeline inputParameters
    -   Output: Model checkpoints stored back to the VAMS asset for download and deployment
    -   Default instance types: g6e.4xlarge (1 GPU, LoRA) with g6e.12xlarge and g5.12xlarge as fallbacks
    -   Shared infrastructure: Uses common EFS model cache and HuggingFace token (shared with Cosmos pipelines)
-   **Pipeline** For NVIDIA Cosmos and GR00T Pipelines, AWS CodeBuild is a container deployment method (`useCodeBuild: true`), building containers in the cloud and pushing to ECR. DockerImageAsset local builds available as fallback (`useCodeBuild: false`). The default however is `false`. Read the documentation for more information before using this feature.
-   **Web** Added experimental Needle USD 3D WASM viewer to the plugin system for `.usd, .usda, .usdc, .usdz` files with full dependency chain loading. Needle WASM libraries have some limitations on supported USD features and dependency depth for textures.
    -   Note: Requires CloudFront deployment mode or the front-end service worker to set proper HTTPS headers for WASM loading. Will not load if organizational security restrictions prevent this. Safari is not currently supported.
    -   Note: Needle Viewer has issues loading dependencies from compressed (USDC) files as these cannot be reliably parsed ahead of time.
    -   Note: This viewer is experimental and some USDs may not load correctly or look correct
-   **External Plugin** Added experimental NVIDIA Isaac Sim connector (`tools/ExternalIntegrations/isaacsim_vams_integration/`) as an Omniverse Kit extension for managing VAMS assets from within Isaac Sim. Supports authentication (Cognito and token override), database/asset/file browsing, single and recursive file download, file and directory upload, workflow listing and execution, and Isaac Sim stage operations (export/upload scenes, download/import USD files, add references to stages). Includes a dockable UI panel and a Python scripting API. See documentation for more information.
    -   Uses the VAMS CLI (`vamscli`) as the communication layer, avoiding direct AWS SDK or VAMS API dependencies
-   **External Plugin** Added experimental Esri ArcGIS Pro connector (`tools/ExternalIntegrations/arcgispro-connector-for-vams/`) as a .NET add-in for managing VAMS assets from within ArcGIS Pro. Supports authentication (Cognito and token override), hierarchical database/asset/file browsing, file reference linking to GIS feature classes and tables, image preview with pan/zoom, single and recursive file download, and context menu integration for attribute tables. See documentation for more information.
    -   Uses the VAMS CLI (`vamscli`) as the communication layer, avoiding direct AWS SDK or VAMS API dependencies.
-   **Web** Added ThreeJS 3D viewer to the plugin system for `.gltf, .glb, .obj, .fbx, .stl, .ply, .dae, .3ds, .3mf, .stp, .step, .iges, .brep` files with full dependency chain loading and scene graph support. Now the primary viewer for most common mesh file types. Additional LGPL-licensed libraries are required for CAD file support (see `./web/customInstalls/threejs/README.md`).
    -   Note: CAD loading requires WASM support via CloudFront deployment mode or the front-end service worker. Without proper HTTPS headers, the viewer will not work for CAD extensions but will still function for other mesh formats. Safari is not currently supported for CAD WASM.
-   **Web** Online3DViewer configuration adjusted to only display for `.3dm, .amf, .bim, .off, .wrl` file types, which are not currently supported by the ThreeJS viewer.
-   Updated `/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}` GET API endpoint to support optional `?versionId=` and `?assetVersionId=` query parameters for specifying the file version or asset version being retrieved
    -   Updated documentation for using the download API and stream API with version parameters for downstream applications
-   **Web** Updated all viewer download APIs and viewers to include asset version ID (when selected) for automatic file version resolution through the API
-   **Web** Updated Veerum Viewer to use the streaming API endpoint `versionId` query parameter for proper file version viewing
-   Added API, web, and CLI functionality for Cognito user management, removing the need for AWS Console access to add/update/remove/reset password for users. Only enabled when Cognito authentication is active.
    -   **Web** New admin navigation page for `User Management`
    -   New API endpoints `/user/cognito` GET/POST, `/user/cognito/{userId}` PUT/DELETE, `/user/cognito/{userId}/resetPassword` POST
-   **CLI** Added commands for admin functionality including Cognito user management, user-role management, role management, and constraint management
-   Added `POST /auth/constraintsTemplateImport` API endpoint for bulk-importing permission constraints from JSON templates. Handles server-side variable substitution, UUID generation, groupId mapping, and constraint creation in DynamoDB, replacing the previous client-side XML parsing and one-by-one creation approach.
    -   **CLI** Added `vamscli role constraint template import` command for importing permission constraint templates
    -   Added `tools/permissionsSetup/apply_template.py` tool for automating deployment of roles and constraint templates, useful for setting up permission structures when new databases are created
    -   Added pre-built JSON permission templates in `documentation/permissionsTemplates/` for common profiles: `database-admin.json` (13 constraints), `database-user.json` (15 constraints), `database-readonly.json` (10 constraints), `global-readonly.json` (10 constraints), and `deny-tagged-assets.json` (1 constraint) with variable placeholders for database IDs and role names
    -   Added comprehensive Permissions Guide (`documentation/PermissionsGuide.md`) covering ABAC/RBAC constraint matrix, two-tier authorization, GLOBAL keyword usage, archive vs permanent delete enforcement, deny overlay patterns, and step-by-step examples
-   **Web** Added version selector on View Asset page for viewing files and metadata from a specific stored version
    -   APIs updated for asset file information and metadata retrieval to accept an optional asset version ID parameter
-   **Web** Added toggle for embedded auth presigned URLs as well as long-lasting URIs that require embedding the VAMS authorization token, available in the Share URLs component
-   **Web** Added asset version selection on View Asset page that filters the file manager and metadata components to a read-only view of the selected version
-   Added asset version archive/unarchive, version alias naming, and version editing (alias and comment). Asset versions in DynamoDB now properly store the asset's database ID to prevent cross-database conflicts. Includes new API routes, web UI, and CLI commands.
    -   Migration scripts required to update previous asset versions with the database ID field on asset versions and sub-tables
-   Added API Key system with creation through API, CLI, and web UI (`API Key Management`). API keys are assigned a user ID to impersonate (including that user's roles). See `DeveloperGuide.md` for usage details.
-   Moving or copying files now also copies/moves the associated metadata and attributes. When copying/moving to an existing file (where versioning rolls), metadata and attributes are merged with the existing records.
-   Added `str_previewfilekey` field to both asset and file OpenSearch indexes. An empty string indicates no preview file; absence of the field indicates the document predates this change and fallback API lookups should be used. Optionally re-index to populate all existing records immediately.
-   Added `str_assetlocationkey` field to the asset OpenSearch index. Populated on asset modifications; optionally re-index for existing records.
    -   **Web** Updated asset and file search page to check this field first before making additional API calls for preview file information, reducing per-record API calls when preview thumbnails are toggled
-   **Web** Split web navigation into `Admin - Auth` and `Admin - Data` sections. Removed "Asset Ingestion" from admin menus (page still accessible via direct navigation as permissions allow).
-   **Web** Database listing page updates:
    -   View/modify metadata on databases (backend/CLI implemented in v2.4)
    -   Mini-map views with display toggle (off by default) when LocationServices is enabled, based on database metadata (Location or Longitude+Latitude keys)
    -   Column-specific filters for S3 buckets, Restrict Metadata, and Restrict File Uploads
-   **Web** Added ability to specify destination file name when copying/moving single files; multi-file operations retain original filenames
-   **Web** Refined View Asset page with cleaner asset details containers and compressed layout spacing
-   **Web** View Asset page now displays the asset preview thumbnail in the top details section when a preview file is available
-   **Web** Refined View File page with cleaner component containers and compressed layout
-   **Web** Tag drop-down selections now have a new layout to group tags by tag types and sort by alphabetical order
-   **Web** Execute workflow modal in view asset page now has descriptions with workflows, a tree view now for selecting files, and search capability for both components.
-   **Web** Updated page bread crumbs to have a `Search` crumb after database and added bread crumbs to some missing "deep" pages.
-   **Web** Web titles of pages are now updated to properly reflect the page you are on. This helps with back/forward history and overall page history tracking in browsers.
-   Update asset and file general text search query to be a "AND" operation with the other filters, instead of an "OR" operation.
-   Added cross-database asset link/relationship and file copy support (requires user access to both databases and assets via the auth asset entity)
-   Added CLAUDE code steering files and commands

### Bug Fixes

-   Permission constraints now allow `GLOBAL` as an input for criteria field values (previously threw an API validation error)
-   Revised CDK deployment code for ALB website to fix a rare recurring error where ALB targets require a unique IP list (issue with how custom resources fetched subnet IPs)
-   **Web** Fixed initial Amplify config logic to properly error when the API config cannot be fetched, preventing errored config from being cached and reused on future page loads
-   **CLI** Continued fixes to ensure `--json-output` parameter returns only JSON output, including handling missing required inputs and parameter validation errors
-   **Web** Fixed file selector pop-up on asset upload for existing assets to work in Firefox; folder selection on Firefox is still not yet supported
-   **Web** Fixed table lists where single row selection would incorrectly select all rows in certain scenarios
-   **Web** Fixed various bugs in pipeline editor and workflow execution list paging
-   **Web** Pipeline listing page now properly shows database filter dropdown
-   **Web** Fixed text viewer to properly theme text window when toggling between dark and light themes
-   Fixed workflow creation and executions where assetId and databaseId were not being passed through. Only fixed for workflows that are re-created or edited; does not affect currently working pipelines.
-   Fixed assets and files search to show full result counts with proper paging functionality, including a backend API paging logic fix
-   Added createWorkflow API validation checks for edge cases and unauthorized pipeline access during workflow creation
-   Fixed typo in reserved S3 prefix list (`piplines` -> `pipelines`) which auto-created assets for reserved prefix folders
-   Pipelines can no longer be deleted if they are currently part of a workflow
-   Added proper error messaging when attempting to archive or unarchive an asset that is not in the correct state
-   Fixed edge cases where local web debugging caused CSP policy errors for some development users
-   Fixed GenAI MetadataLabeling pipeline CDK path case sensitivity issue on non-Windows builds
-   Fixed Gaussian Splat pipeline Docker build error by updating to the newest version of the 3D reconstruction toolkit
-   Fixed Gaussian Splat pipeline to re-pull latest changes from the 3D reconstruction toolkit repository on every deployment
-   Fixed VPC endpoint logic for ECS service in pipelines needing endpoints for both private and isolated VPC subnets; previously caused errors when enabling multiple pipelines with mixed subnet types. See troubleshooting section for CDK ECS VPC endpoint errors during re-deployments.
-   **Web** Fixed various minor bugs across the website including proper error reporting
-   **Web** Fixed many places throughout website displays to properly synonym words based on set constant variables (Asset, Database, etc.). This will not change CLI or API responses with these keywords.
-   **Web** The View Asset File Manager now properly sorts files alphabetically in the tree view

### Chores

-   Indexers now ignore `workspace` and `workspaces` asset bucket prefixes in preparation for personal workspaces functionality
-   **Web** Added default footer message and updated login page layout
-   **Web** Refined asset and file search UI with column resizing, shorter column names, and text wrapping
-   Refactored Pipelines and Workflows API backend with proper request/response models, improved input validation, and alignment with v2.2 backend standards. Preparation for a larger pipeline/workflow overhaul.
-   Added `CLOUDFRONTDEPLOY` feature enablement flag to indicate web deployment type to the front-end
-   **Web** Added service worker and proxy for setting header flags to enable WebAssembly (WASM) loading in both local debugging and deployed environments
-   Added additional workflow creation and execution API validation checks
-   Added featuresEnabled DynamoDB table deduplication check during CDK deployment to overwrite existing values
-   Updated viewer descriptions for those that do not support showing non-current version files (always show the latest file)
-   **Web** Removed progress bar and status columns from asset file tables on the pre-upload screen to avoid confusion about upload state
-   Further API performance improvements for listing asset files and gathering asset export data
-   Updated Gaussian Splat pipeline to the newest version of the 3D reconstruction toolkit
-   **Web** Updated PlayCanvas viewer to latest version; also fixed camera rotation bugs
-   Updated CLINE/KIRO workflows for clarifying CLI patterns with json-output
-   Updated NPM dependencies in web, web visualizers, and infra for audit fixes; refactored deprecated components (RelatedTable) replaced by newer packages

### Known Outstanding Issues

-   With multiple S3 bucket support, identical assetIds across different buckets/prefixes in different databases can cause lookup conflicts in comments and subscriptions. This only occurs with manual S3 changes, as VAMS-generated assetIds use unique GUIDs.
-   Using the same pipeline ID in both GLOBAL and non-GLOBAL databases causes overlap conflicts.
-   Pipeline metadata inputs have a size limit when sent to ECS pipelines. Assets or files with extensive metadata may exceed the 8K character ECS JSON input limit. A future pipeline overhaul will convert metadata input to a file-based approach.
-   For assets with hundreds to thousands of files or very large files (TB-size), some API operations may time out after 29 seconds while the Lambda continues processing (up to 15 minutes). OpenSearch re-indexing with hundreds of thousands to millions of files may not complete within the 15-minute Lambda timeout and may require local or containerized re-indexing. Asynchronous methods and optional containerized processing are being evaluated.

### Troubleshooting

-   If receiving ECS VPC interface endpoint errors during CDK deployment, disable IsaacSim and Gaussian Splat pipelines, re-deploy, then re-enable and deploy again. ECS endpoint changes combined with CloudFormation stack change restrictions can cause this issue.
-   If receiving web build or infra CDK errors in upgraded projects, re-run `npm install` in web and infra directories. Persistent build errors may require clearing the `node_modules` cache.

## [2.4.1] (2026-01-30)

### Bug Fixes

-   Fixed CDK deployment error with storage resources asset indexer queue names when deploying to GovCloud environments
-   Fixed CDK deployment error with Cloudfront KMS principal persmisions (should not be added) when deploying to non-cloudFront for web configurations or GovCloud environment
-   Fixed CDK deployment error with deploying metadata schema data when using KMS key (KMS key permissions were not being applied correctly to CDK custom resource role)
-   Fixed CDK deployment error with IsaacSim use-case pipeline which tried to set IAM permissions on invalid resource types
-   **Web** Fixed bug on metadata schema management where if navigating back to the same metadata schema page through the navigation bar (while on it), it won't show as blank or empty page anymore
-   **Web** Asset FileManager will now remember expanded folders in file tree while detailed data is still loading in for large file trees. Previously it would collapse folders every time a new page worth of data was loaded in.
-   **Web** Asset FileManager will now open all parent folders to a selected file in the tree when opened directly from an external page/source (ie. from asset/file search)

### Chores

-   **Web** Added service worker and proxy to manually set header flags for local debugging and/or attempt to set for CDN deployment. Currently verified to work for local debugging so web assembly (WASM) components can be viewed.
-   Fix readme instructions for v2.3 to v2.4 migration scripts to remove steps that shouldn't have been added

## [2.4.0] (2026-01-16)

### Major Change Summary:

• New Partner/Solution Integrations - Veerum 3D Viewer for 3D Tiles and Point-Clouds (licensed), NVIDIA IsaacSim use-case pipeline (reinforcement training/evaluation), Garnet Framework (knowledge graph) external data indexing
• Metadata Schema System Overhaul - Database-specific and global schemas, multi-schema overlay support with validation, new field value types, optional CDK-deployable default schemas
• Metadata System Overhaul - Multi-entity type metadata support (databases, assets, files, asset links), bulk editing with CSV import/export, separate file metadata and attributes storage, asset metadata versioning, enhanced metadata validations
• Enhanced Backend Infrastructure - Refactored data queues for easy indexing expansions and performance (ie. Garnet Framework), auto-workflow triggering on file upload, EKS deployment option for RapidPipeline, improved file streaming APIs
• Advanced Asset Management - Asset unarchiving, file renaming, database-level file upload restrictions option, asset search location mini-maps, concurrent workflow execution support for single asset
• Performance & Scale - Refactored UI/API/Storage for large/many file uploads and overall performance/security improvements, UI lazy loading, optimizations to support hundreds to thousands of files per asset, fine-tuned data caching, enhanced load times
• New Audit Logging - Amazon Cloudwatch separate audit logging for authorizations, VAMS actions, and errors/validations
• CLI & CDK Deployment - CLI workflow execution commands, CLI metadata operations, CLI BOM industry query example, custom CloudFront DNS/TLS configuration, API-only deployment option (no website)

### ⚠ BREAKING CHANGES

Permission authorization constraints now use a dedicated DynamoDB table (no longer shared with authEntities) to improve permission lookup performance. Existing custom constraints must be migrated. VAMS default constructs (Admin/RO) will be re-added automatically.

Metadata and metadataSchema DynamoDB tables have been replaced with new tables. The data migration script must be run to migrate data from the deprecated tables.

OpenSearch indexes have changed their schema for "MD\_" and "AB\_" fields (now flat-objects). A re-index with clearing of old indexes is required to apply the new schema. The migration script handles this process.

**Recommended Upgrade Path:** Run the upgrade script to migrate permission constraints from the old table to the new one if custom constraints were added or modified beyond VAMS defaults: `infra\deploymentDataMigration\v2.3_to_v2.4\upgrade`

### Features

-   (Breaking Change) Overhauled metadata schema to support multiple schemas per database (including "GLOBAL" database schemas) and entity types (database, asset links, assets, asset files). Asset files can be further restricted by file extension. File metadata and attributes are now supported; file attributes only support "string" field type.
    -   Support for both database specific and GLOBAL (all database) schemas. All schemas apply that are relevant.
    -   Supported field types for schemas and metadata across all entities: STRING, MULTILINE_STRING, INLINE_CONTROLLED_LIST, NUMBER, BOOLEAN, DATE, XYZ, WXYZ, MATRIX4X4, GEOPOINT, GEOJSON, LLA, JSON
    -   Schemas can be named, and multiple schemas can apply to entity type metadata with aggregation (e.g., a GLOBAL schema for a specific entity type will stack with a database-specific schema for the same entity type). Field name conflicts default metadata to `string` with no conditions applied.
    -   New CDK config options to auto-load default GLOBAL schemas. Options under `app.metadataSchema.X` are now available and enabled by default. See `infra\lib\nestedStacks\apiLambda\constructs\dynamodb-metadataschema-defaults-construct.ts` for default schemas.
    -   New permission constraint fields for modifying and retrieving metadataSchema: metadataSchemaName, metadataSchemaEntityType. Deprecated: field
    -   **Web** Updated to support new fields and APIs
    -   **CLI** Updated to support new fields and APIs. CLI currently supports only GET/LIST for metadata schema.
    -   Data migration scripts added to migrate old metadata schema to new DynamoDB tables
    -   Note: Schema rule restrictions are enforced only when updating metadata via API and some web validation checks. Metadata may not match schema requirements in some cases (e.g., new asset creation or pipeline returns). Schema validation is not foolproof for restricting metadata (e.g., new assets won't have required metadata until the first metadata API call validates requirements).
-   (Breaking Change) Overhauled metadata APIs, CLI, and Web interfaces to support metadata for multiple data entities and entity types (database, asset, asset file, asset links), improved validation and error handling, bulk metadata updates (including CSV import/export), and enhanced metadata schema overlays. Files now support "attributes" separately from metadata (only "string" value type for attributes). General file search includes file attribute fields, but specific metadata searching is limited to metadata fields.
    -   Supported field types for metadata: STRING, MULTILINE_STRING, INLINE_CONTROLLED_LIST (only with applied schema), NUMBER, BOOLEAN, DATE, XYZ, WXYZ, MATRIX4X4, GEOPOINT, GEOJSON, LLA, JSON
    -   MetadataSchema now enforced at API level with web support for schema overlays
    -   Updated workflow executions and return formats for metadata (and updated applicable use-case pipelines) to support new entity types and field value types
    -   Updated OpenSearch indexing to catalog new DynamoDB tables for metadata. File attributes are now stored separately in the file index as `AB_` fields. This creates new OpenSearch v2 indexes with a new name as new index schemas need to be applied. `MD_*` and `AB_*` fields are now flat object fields.
    -   Limit of 500 metadata and attributes per metadata entity type
    -   Updated relevant use-case pipelines that relied on metadata to properly function with the new system; the CAD3D metadata extraction pipeline now writes to file attributes instead of metadata
    -   **Web** Updated to support new fields and APIs. Web currently doesn't support displaying/updating database metadata (API/CLI functionality only).
    -   **CLI** Updated to support new fields and APIs.
    -   Data migration scripts added to migrate old asset and file metadata to new DynamoDB tables
-   (Breaking Change) Refactored permission constraints DynamoDB table, Casbin lookup logic, and authConstraints API for improved performance following new DynamoDB table refactor patterns. This improves solution performance for repeated data actions.
-   Updated databases, metadata, and file uploads to support new database config options (on database APIs) for optionally restricting file extension types on asset file upload and restricting additional metadata outside applicable schemas: `restrictMetadataOutsideSchemas` (bool, default: False) and `restrictFileUploadsToExtensions` (string, default: Empty (allow all), also supports `.all` to allow all)
    -   Added new PUT API path to update databases at `/database/{databaseId}`; POST API method no longer allows database updating
    -   Note: File extension restrictions apply only on file upload and are not checked on direct S3 bucket file manipulation
    -   **Web** Updated to support new fields and APIs
    -   **CLI** Updated to support new fields and APIs
-   Asset versions will now save all and view asset and file metadata and atrributes as part of versioning an asset; previously versioned asset will not have any metadata as part of the version
    -   There is now an option on reverting to a asset version to update and revert to the saved file and asset metadata (and file attributes)
    -   Asset versions can now be created, even if no files are in the asset
-   New addon feature and configuration which allows pushing database, asset, and file changes to a Garnet Framework solution (Knowledge graphs) deployed in the same AWS account. Visit [garnet-framework.dev](https://garnet-framework.dev/) for more information on the garnet framework solution. See the [Configuration Reference](https://awslabs.github.io/visual-asset-management-system/deployment/configuration-reference) on how to turn this addon feature on.
-   **Web** Added Veerum 3D Model licensed viewer to the viewer plugin system for `e57, las, laz, ply, and json (3D Tile)` files. Visit [veerum.com](https://www.veerum.com/) for license purchasing, then enable this viewer in `web\src\visualizerPlugin\config\viewerConfig.json`.
    -   Note: This viewer requires the Potree Auto-Processing pipeline to be enabled for PointCloud file loading.
-   Added new Amazon EKS pipeline option for RapidPipeline use-case pipeline (complementing existing Amazon ECS). This provides a pattern example for other use-case pipelines implementing Kubernetes (EKS) versus Elastic Container Service (ECS).
-   New reinforcement learning training use-case pipeline using NVIDIA Isaac Lab on AWS Batch with GPU acceleration. Train and evaluate RL policies for robotics simulation directly from VAMS assets.
    -   Supports training mode with configurable tasks, environments, and iterations using RSL-RL library
    -   Supports evaluation mode for testing trained policies with metrics export
    -   Uses AWS Batch with GPU instances (g6e.2xlarge/g5.xlarge) for compute
    -   EFS-backed checkpoint storage for training persistence
    -   Step Functions orchestration with async task token callbacks
    -   Auto-registers `isaaclab-training` and `isaaclab-evaluation` workflows when enabled
    -   Configurable warm instance option to reduce cold start times
    -   Outputs training logs (.txt), metrics (.csv), and model checkpoints (.pt) to VAMS
    -   Requires explicit NVIDIA EULA acceptance in config.json (`acceptNvidiaEula: true`)
-   **Web** Added API (`/database/{databaseId}/assets/{assetId}/unarchiveAsset`) and UI on Asset and File search for Unarchive Asset. Cleaned up UI logic for archived asset elements.
-   **Web** Added Rename File operation in asset details file manager when selecting single files. Uses existing file move API.
-   Added new CDK deployment configuration support for disabling both CloudFront and ALB static website deployment options to enable API-only VAMS deployments
-   Added new CDK deployment configuration support for CloudFront static website custom domains and TLS certificate imports
-   Refactored backend data indexing flow to support current OpenSearch indexing and enable easy expansion to other indexing solutions or partner integrations
-   **Web** Refactored web upload workflow for files to further parallelize uploads into batches, handle errors and retries, and manage backend throttling
-   Updated ./listFiles API with additional `basic` query parameter (boolean, default: false) for quick file listing without archival, version, or preview file data (much faster).
    -   **CLI** File listing command now has auto-paginate and basic parameter flags
-   **Web** Updated asset files manager to implement lazy loading approach for loading files via API calls, making page loads faster when accessing file information (especially helpful for assets with many files)
-   **CLI** Added --auto-paginate parameter (and adjusted other pagination parameters) to listing of databases, buckets, assets, and lists
-   **CLI** Updated CLI profile/auth/setup to pull in and display more environment configurations from the API across various commands
-   Workflow execution restrictions loosened to allow multiple running executions of the same workflow on an asset as long as different files are being processed (previously allowed only 1 running execution per workflow per asset without considering input files)
-   **Web** New workflow/pipeline auto-triggering execution system for file uploads. Workflows have a new property settable in the workflow editor; some have default configurations in deployed CDK use-case pipelines to auto-set this (`autoRegisterAutoTriggerOnFileUpload`). Parts of this system will be refactored in an upcoming pipeline overhaul.
    -   Trigger is set by specifying which file extensions should initiate the pipeline for each file uploaded to an asset (new or modified). This is a comma-delimited list of extensions. If ".all" is provided, it executes on all file extensions uploaded.
    -   Feature implemented with new indexing SNS where a new SQS queue subscribes to the system for file uploads to check executions per file. This enables high scalability for file uploads.
    -   PotreePipeline now defaults to auto-register in VAMS with the auto-trigger feature instead of its direct SQS tap-in, which previously bypassed the Workflow system
-   **Web** Workflow Executions on View Asset now lazy loads data; search bar temporarily removed
-   **CLI** Added new command grouping (`workflow`) and commands for workflow listing, asset workflow execution listing, and executing new workflows on assets
-   **CLI** Added new command sub-grouping (`bom`) under `industry engineering` which provides an example BOM query input command to to aggregate + file combine data across assets
    -   Note: Backend API not yet upgraded to new request/response model pattern; expected as part of pipeline/workflow overhaul development task
-   **Web** Web text viewer now additionally supports file types: `".inf", ".cfg", ".md", ".sh", ".csv", ".py", ".log", ".js", ".ts", ".sql", ".ps1"`
-   File type upload restrictions no longer restrict: `".ps1", ".sh", ".py", ".ini", ".inf", ".sql", ".js", ".docx"`
-   **Web** Asset Search now has a search mode option to show map thumbnails, similar to preview thumbnails, displaying a mini-map for each asset record in the regular search listing that has location or lat/long metadata defined. This is in addition to the existing map view for all assets with this data. Only shown if location services are enabled on the backend.
-   OpenSearch (OS) no longer indexes metadata fields as individual OS fields but instead groups metadata (and the new attributes) under single `MD_` and `AB_` flat-object fields for asset and file indexes. This may reduce future functionality to be able to do advanced searching on these fields but provides both better performance and prevents future errors when hitting OS max field limits.
-   **Web** Ability to now navigate directly to a file via URL path (to allow outside static references) `#/databases/<databaseId>/assets/<assetId>/file/<relative file path>`; previously file was passed only via web state
-   Added new CloudWatch event logs for specific VAMS audit logging. Currently Authorization (API-All, Data-UnauthorizedOnly), AuthOther, AuthChanges, FileUpload, FileDownload, FileDownload-Streamed, and Errors are logged to the special audit event logs.
    -   Note: Some errors may not be logged if the API still uses the non-refactored old patterns. These will be updated in the future.
    -   Note: Authentication events are handled through Cognito or external IDP event logs currently. See [Audit Logging](https://awslabs.github.io/visual-asset-management-system/developer/audit-logging) for more details.

### Bug Fixes

-   Permanently deleting an asset now also deletes associated asset links and asset link metadata in the database (previously caused inconsistencies when viewing asset links from related assets)
-   Fixed bug where archived assets were not properly reindexed in OpenSearch as archived
-   Fixed bug where archiving an asset caused the asset (or default database) to be re-created in some scenarios during S3 file re-indexing
-   S3 bucket sync processes to create assets from S3 objects now operate even when OpenSearch functionality is disabled (part of indexing flow refactor)
-   Fixed Casbin cache logic to properly enforce 60-second cache duration for updating constraints, roles, and user roles in lambda authorization logic
-   Fixed bug in move file API command that prevented moves (or renames) due to destination check logic issues
-   Fixed bug in many use-case pipelines where early errors or validation issues did not properly trigger external workflow error handling (caused workflows to run to prescribed timeout instead of failing early)
-   **Web** File previews provided as `.previewFile.` now display correctly in Asset/File search
-   **Web** File operations in asset details file manager now appropriately refresh the details panel during certain operations
-   **Web** Fixed UI where some delete operations did not refresh the page and/or did not show the correct record ID to be deleted (display issue only)
-   Fixed various API pagination issues with listing databases, assets, and files
-   **CLI** Fixed to ensure all errors return in proper JSON format when `--json-output` flag is set
-   Fixed assets and auxiliary assets streaming APIs to properly check payload sizes under 6MB and return presigned S3 URL redirects for larger payloads. This fixes issues with Potree and 3D Tile viewers where clients may fetch larger range sizes for tiled subsets.
-   **Web** Added tracking of asset file input for workflow execution history and display on the view asset page
-   **Web** Updated logic for file viewers (Potree viewer) that require fetching/passing JWT tokens for API header passing to fetch/refresh tokens as needed without page refresh and properly work with external OAuth2 tokens (non-Cognito)
-   **CLI** Fixed assets download command to properly download entire asset files at once from root or from different file folders
-   **Web** Fixed Workflow Execution on View Asset not auto-refreshing data when executing a new workflow; now shows proper execution counts
-   **Web** Fixed error when building/installing Potree Viewer and Pipeline on some OS build versions (e.g., Linux)
-   **Web** Fixed bug in "Execute Workflow" modal that prevented user from selecting the entire asset as input (previously required selecting an individual file)
-   Added back-off retries to OpenSearch file and asset indexing lambdas when 429 `too many requests` errors happens; this helps prevent files and assets from not getting indexed properly during heavy load or re-indexing operations
-   Workflow pipelines that output files to be written back to the asset now properly keep the relative key path how they should be stored in the asset (verses just storing all at the asset root currently)
-   **Web** Fixed asset version component / tab data paging issue and column sorting not working
-   **Web** Fix constraints editing form to allow selecting individual criteria (or/and) items to remove; it only allowed select all or nothing
-   Fixed default RO role constraint permission examples that get loaded during cdk deploy to work with changes that happened to APIs in v2.2+

### Chores

-   Refactored tag, tagType, roles, userRoles, authConstraints, and auxiliary asset stream API service backends to meet new API standards for error handling, validation, and request/response model usage
-   Refactored some API request/response models to replace deprecated Pydantic v1 "extra" field with proper v2 pattern
-   Refactored remaining CDK lambdabuilder functions to follow new naming pattern for table inputs and permissions
-   Further adjusted upload thresholds for throttling and file/part/sequence splitting across backend API, web, and CLI to optimize for both large files and many files
-   Updated ./listFiles API to default maxItems to 10000 and max page size to 1500 for basic mode and 100 for non-basic mode
-   API for `/secureConfig` now returns the website deployed URL (if a website is deployed)
-   File streaming APIs now support HEAD requests to check if a file exists before streaming its contents with GET
-   **Web** Consolidated auth token functions to a utility function, moved out of Auth.tsx
-   Updated logic for when fileIndexerSNS queue is published from S3 object changes to reduce calls for objects that should be skipped (e.g., folder objects, `init` files/folders, special exclusion folder prefixes and their objects). These still get processed by sqsBucketSync queue/lambda but will not be further re-published, reducing downstream processing where these objects are typically ignored.
-   **CLI** Removed API version check on all API commands to reduce CLI API calls and slightly increase performance. Only auth and setup commands now check CLI version against API version.
-   Updated all lambda memory to 5308 from 3003, increasing vCPU from 2 to 4 and improving API response performance
-   Updated authz criteria builder on backend to ignore fields in criteria that are not in the current constants file (e.g., deprecated authz fields)
-   Added warning on OS reindex utility when the lambda function times-outs that it doesn't return an error code. It returns a warning that the lambda may still be running and to check cloudwatch logs.
-   **Web** Added a note on the web navigation bar if no items show up that the user doesn't have permissions to view any web navigation pages
-   Updated the custom lambda authorizer for cognito to use `joserfc` library from jose to overcome critical security findings on the jose library

### Known Outstanding Issues

-   With multiple S3 bucket support, scenarios may occur where identical assetIds exist across different buckets/prefixes in different databases, causing lookup conflicts in Asset Versions, Comments, and subscriptions functionality. This can only occur with manual S3 changes, as assetIds generated from VAMS uploads use unique GUIDs.
-   Using the same pipeline ID in both GLOBAL and non-GLOBAL databases will cause overlap conflicts and issues.
-   Pipeline metadata inputs have a limit when sending to ECS pipelines. Assets and/or files with extensive metadata may exceed the ECS limit for JSON metadata input (8k characters). Future pipeline overhauls will convert metadata input to a file to resolve this.
-   When dealing with hundreds to thousands of files per asset or very large files (TB-size), some API asset/file operations may time-out on the request (after 29 seconds) however the lambda may still be processing the request and successfully complete the operation (up to 15 minutes). This also goes for OpenSearch indexing when there are hundreds of thousands to millions of files to re-index. The re-index may actually not finish after the 15 minute lambda time-out with millions of files and require different re-indexing technique locally or in a container. Asynchronous methods and optional containerized processing are being evaluated for the future for all API requests to prevent this.

## [2.3.2] (2026-01-12)

### Bug Fixes

-   **CLI** Fixed documentation issues with the CLI
-   Updated solution root and infrastructure NPM package dependency version (npm audit fix)

## [2.3.1] (2025-11-21)

### Bug Fixes

-   **CLI** Fixed bugs with sentinel object check, file upload exception returns, and pattern updates

### Chores

-   **Web** Added checks to web yarn install custom installers to look at which viewers are enabled/disabled before installing the dynamic libraries. This was mostly to reduce install and deployment times to not include viewer assets that are not enabled for the end-user.
-   **Web** Updated to disable licensed file viewers by default in their configuration file
-   **CLI** Updated CLI to require python 3.12 minimum and updated dependency versions (Click to 8.3.1 for Sentinel object changes for default parameters)
-   Updated documentation

## [2.3.0] (2025-11-13)

### Major Change Summary:

• New VAMS CLI Tool - Complete command-line interface with robust file handling for large-scale automation and integration workflows
• Overhauled Search & Asset Management - Redesigned asset and file search system with enhanced UI, advanced filtering, and improved location services integration
• Advanced File Visualization System - New plugin-based viewer architecture with new CesiumJS, BabylonJS, PlayCanvas, VNTANA, PDF, Video, and Text viewers plus modal popup access
• Enhanced Pipeline System - Auto-deployment registration capabilities, new CAD/Mesh extraction pipeline, Gaussian Splat toolkit, and streamlined backend dependencies
• Improved Asset Links & Metadata - Extended support for 4x4 Matrix, WXYZ, JSON, GEOJSON, GEOPOINT types with multiple parent/child relationships
• Performance & Security Improvements - Enhanced API Gateway authorizers with IP restrictions, asynchronous large file upload processing, and restored VPC lambda support
• AI-Assisted Development - Integrated CLINE and Kiro workflow rules for AI-powered coding assistance and improved developer experience

### ⚠ BREAKING CHANGES

All APIGateway authorizers were swapped for custom lambda authorizers to provide more flexibility in implementing additional functionality. This may cause issues with your organization so please review with your security teams. Authorizer changes may require forced cache resets on API gateways if new authorizations are not following new rules set. (https://docs.aws.amazon.com/cli/latest/reference/apigatewayv2/reset-authorizers-cache.html)

Changes to BatchFargate CDK construct naming for use-case pipeline naming may require you to deploy CDK without batch pipelines and then again with to properly re-deploy them. Not doing this with existing deployed pipelines (Metadata 3D Labeling and PcPotree) will result in a CDK deployment error within ECS Fargate. This may also require you to update your VAMS pipeline/workflow lambda function names after re-deployment.

In order to get lambdas to work behind a VPC again (broken as of V2.2), MFA for roles cannot be supported if Cognito is on and all lambdas are behind a VPC (CDK config flag) or OpenSearch provisioned is turned on (CDK config flag).

OpenSearch has new indexes and requires the data migration script or new re-indexing tool script to be run on existing assets and files to re-index open search with existing data.

**Recommended Upgrade Path:** Run upgrade script for the new OpenSearch indexes which will re-index content `infra\deploymentDataMigration\v2.2_to_v2.3\upgrade`

### Features

-   **CLI** VAMS now has a CLI tool that can be used to automate VAMS operations. It includes operations so far for authentication, database, asset, assetLinks, assetLinkMetadata, metadata, metadataSchema, tags, TagTypes, search, featureSwitch, and files. More operations to match API functionality to come in future releases such as more admin functionalities of VAMS.
    -   CLI has logic for asset uploading and downloading and optimized for many and large files
    -   CLI contains some experimental industry commands to help with automation of processing PLMXML files and doing asset-tree GLB combining
-   New asset export API `/database/{databaseId}/assets/{assetId}/export POST` to make it easier for downstream tool integration to have a single call to fetch all information about an asset, all its related data, and asset link sub-tree information (including auto-fetching pre-signed URLs). Integrated into CLI to support easy fetching and file download logic.
-   **Web** The website viewer system has been rewritten to support a plugin-based dynamically loaded viewing system which allows for much easier capability to add new viewers and adds more functionality. Documentation can be found at: `web\src\visualizerPlugin\README.md`
    -   Support for multiple viewers per file types which is now controlled with a drop-down as part of the viewer
    -   Support to define which viewers are for multiple files or single files
    -   Support for custom parameters as part of viewer plugin configuration which allows for token configuration for paid/ISV integrations
    -   Support for custom code, UI, and dependency management for each viewer. Also supports lazy loading of plugins when needed for a viewer.
    -   Viewer is now shown both on the View File page and as a modal pop-up from the file manager for easier quick access
    -   Added a PDF viewer for `.pdf` extension
    -   Added a text viewer for `.txt`, `.json`, `.xml`, `.html`, `.htm`, `.yaml`, `.yml`, `.toml`, `ipynb`, and `.ini` extensions
    -   Added the CesiumJS viewer for `.json` tileset files which can load subsequent other files referenced in the asset (even if not selected for viewing directly). This is an initial/basic CesiumJS viewer implementation with default options as part of this release. Note: Requires `allowUnsafeEvalFeatures` CDK `config.json` configuration flag to be turned on (off by default).
    -   Added BabylonJS-based Gaussian Splat viewer for `.ply` and `.spz` splat files
    -   Added PlayCanvas-based Gaussian Splat viewer for `.ply` and `.sog` splat files
    -   3D Online viewer now has additional UI added to support basic extra functionality
    -   3D Online Viewer once again will also support `.ply` file extensions for viewing (previously switched to PotreeViewer only)
    -   Added the VNTANA 3D Model licened viewer to the viewer plugin system for `glb` files. Head to [VNTANA.com](https://www.vntana.com/) for license purchasing and then enable this viewer in `web\src\visualizerPlugin\config\viewerConfig.json`.
-   Overhauled the file and asset OpenSearch system, APIs, indexing, and user interfaces
    -   Assets and files are now split into two separate OpenSearch indexes; the old index will remain and will not be deleted for auditing and/or migration purposes; this causes breaking changes that require a migration script to re-index all assets/files for search
    -   Asset link relationship data will now additionally be indexed (excluding asset link metadata for now)
    -   **UI** Assets (now "Assets and Files") has a completely new search page with many new filtering capabilities and options.
    -   **Web** Search map view will now allow for many more metadata fields to be used for adding map marker or area placement (any asset with `location` (GP/GS) and `longitude` (string or number) / `latitude` (string or number) combination metadata will show up)
    -   Search now has its original API of `/search` and a new `/search/simple` API for a simplified search input
    -   Implemented a new CDK config option in `config.app.openSearch.reindexOnCdkDeploy` that can trigger a complete index clear and re-index of assets and files. This can also be used as CDK context argument `reindexOnCdkDeploy` for the cdk deploy command. Note: Only use this after having CDK deploying at least once with v2.3 changes, otherwise the reindex may not work or error.
    -   A new CDK custom tool section and migration scripts have been added to help manually trigger a reindex outside of a CDK deploy
-   Maps on the backend and UI frontend are updated to use the new location service APIKey method and removes the older raster map and place functionality
    -   Note: This removes the last place that cognito identities are used which means the location services functionality can now be used for external IDP solutions. Cognito is no longer required to enable location services. Only requirement now is commercial cloud partition (GovCloud doesn't support APIKey implementation).
    -   Note: This change removes the cognito authenticatedRole and association with the identity pool. Unauthenticated role (no permissions assigned) still remains for now as it is needed for basic auth login by the web Amplify-SDK v1.
-   **Web** Added a draggable splitter in ViewAsset page between the file manager tree view and details panel
-   Added a new API endpoint for asset file streaming (similar to asset preview auxiliary files) at `GET /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}`
-   Added .clineRules and .kiro for AI workflows for AI-assisted development for VAMS backend API development, CDK development, and CLI development
-   All HTTP APIGateway authorizers were swapped for custom lambda authorizers.
    -   New Lambda Layer specifically with libraries for the lambda authorizers
    -   New support for CDK configured IP range restrictions for API Gateway calls that are managed in the authorizer
-   Added new uploadFile backend logic with an SQS queue to handle final processing of large >1GB files asynchronously. This prevented APIGateway->Lambda timeouts (30 seconds)
-   Added WXYZ, Boolean, Date, 4x4 Matrix, Geoshape, GeoPoint, LLA (Latitude Longitude, Altitude), and JSON asset link metadata value types.
    -   **Web** Added `Matrix` static asset link type metadata fields with relevent field types.
    -   **Web** Defaulted `rotation` static asset link metadata field to WXYZ field type (from XYZ)
-   Asset link parent-child relationships now support an additional key of `assetLinkAliasId` that can be added to allow multiple parent->child relationships of the same assets. This is common in scene or engineering assembly build-outs where a parent may contain multiple of the same type of asset below it (i.e. same screws on a panel or same trees in a forest scene).
-   **Web** Changed Pipeline Edit/Create to make Asset Type and Output Type a required string text field. This removes the last place that requires specific VAMS extensions to be preloaded. These fields usages are expected to be overhauled along with overall pipelines in a future release.
-   Refactored createWorkflow to not require the stepfunctions library anymore which entirely removes the additional heavyweight lambda layer created specifically for this function. This should speed up CDK deployments, reduce CDK package size, and reduce security posture by limiting backend libraries needed. Additionally, some other upgrades were done to createWorkflow as part of the refactor:
    -   Updating an existing workflow no longer creates a new AWS step function workflow but modifies the definition of the existing (preserves job history)
    -   Updated to the new backend error handling logic used since v2.2
    -   GovCloud configuration restrictions updated to not include a hard use requirement of openSearch provisioned. OpenSearch serverless is supported now in GovCloud environments.
-   Added backend and CDK capability to auto-register deployed pipelines as global pipelines and workflows in VAMS.
    -   Defaulted many pipelines to now have default entries added to make it easier out of the box to execute on those pipelines/workflows.
-   Added `SYSTEM_USER` to admin role during CDK deployment and enabled lambda cross-call logic during authorization checks. System user is used for authorized lambda cross-calls where a requesting user context may not be present (such as calling lambdas from CDK deployments or external side-car solutions). IAM permissions must be used in this case to control access to direct lambda calls that can to inject a `lambdaCrossCall` object into the event.
-   Added new asynchronous lambda-based `meshCadMetadataExtraction` pipeline and workflow that is `disabled by default` in all CDK configuration files. This pipeline can extract basic attributes and add them to the asset metadata for certain MESH and CAD file types selected. It uses Trimesh (MIT license) and cadQuery (Apache 2.0). Note: cadQuery further uses OpenCascade which is a LGPL-2.1 licensed.
-   Pipelines also now have `inputAssetLocationKey` data on execution to provide the asset root prefix of where the asset is located in the assets S3 bucket (used for generating relative paths as needed, such as for file-level metadata)
-   **Web** Metadata for individual files is now also shown and managed in the ViewAsset file manager when selecting a file, shown in the file details panel. This is on top of the existing location in the ViewFile page.

### Bug Fixes

-   **Web** Scrolling issues on browsers with MacOS should now hopefully be fixed. This was due to an issue with Potree libraries being loaded globally before.
-   **Web** Fixed UI screen issues with Upload Asset and Asset Link relationship
-   Fixed Asset Link Service GET API to properly return child trees that show full paths when duplicate nodes exist in different branches of the tree (previously trimmed the tree of duplicate nodes)
-   Updated BatchFargate CDK construct names to be unique for the stack (see breaking changes)
-   Fixed backend asset file operations and S3 indexing for files >5GB (introduced in v2.2)
-   Fixed Cognito unauthenticated role trust policy to switch the partition correctly. Cognito deployments were causing errors in GovCloud environments without this.
-   Fixed `PcPotreePipeline` to remove tags from SQS lambda event source as this is not supported in GovCloud environments.
-   Fixed When saving pipelines that lambda function names have whitespace trimmed to prevent workflow errors
-   Lambdas now work behind a VPC again however a compromise had to be made, Cognito MFA checks are currently not possible as a AWS VPC Endpoint doesn't exist for Cognito (BREAKING CHANGE).
    -   Additional VPC Endpoints were added to support missing functionality for lambdas behind a VPC (APIGateway, SSM, Lambda, STS, Cloudwatch Logs, SNS, SQS)
-   Updated SearchBuilder and PCPotreePipeline SQS queues to use new name format to prevent overlaps of stacks within a AWS region
-   Fix GenAIMetadataLabelingPipeline to now handle the v2.2 VAMS functionalities of multi-file assets with folders
-   Permanently deleting asset files via the API did not remove the files metadata records
-   Fix backend bug in `/upload` that was preventing multiple zero-byte files from being uploaded/completed in the same request.
-   Added additional check in create asset API to validate there is no forward slash in the assetId (if provided)
-   Added additional check in create asset API to validate assetId does not conflict with an existing key in the default S3 bucket or that a custom bucketKey provided does exist in S3 when provided
-   Added extra checks to create database/tag/tagType APIs to help prevent duplicate IDs being created

### Chores

-   **Web** Updated ViewAsset page to support passing in a state with a file path to load (used from links from the new search page)
-   **Web** Added a refresh icon for many of the VAMS entity listing pages (databases, pipelines, etc.)
-   **Web** Cleaned up Assets Workflow Executions table to only show workflows with past executions, shorten descriptions in the table, and default sort executions by `Started` column in descending order
-   Updated Cognito invitation and verification email messages to be more VAMS branded, show username where appropriate, and remove confusing period character directly after temporary passwords.
-   Update KMS key policy to support Cloudformation principal better for CustomResources when modifying S3 or DynamoDB tables that have a KMS encryption key. This should fix errors with setting default auth constraints and roles during CDK deployment that sometimes cropped up.
-   Updated pipeline CDK export names and job definition names to be variable per the stack deploying it to further reduce conflicts of same stack deployments in the same region
-   Update CDK ApiBuilder core logic to not be wrapped in a function anymore to make it easier to have global class variables in the CDK nested stack
-   Enforce S3 bucket object ownership on static website bucket
-   Update CSP to include workerSrc directives which are required for certain viewers to work
-   Updated `GenAIMetadataLabelingPipeline` to use the latest Claude Sonnet 4.5 GenAI model for commercial and Sonnet 4.0 in GovCloud (previously used 3.0) and pass model ID now from CDK configuration
-   Updated `conversion3dBasic` pipeline to use the latest Trimesh version (4.8.3)
-   Added a new `assetIdGSI` Global Secondary Index on the assets dynamoDB table with PK: assetId, SK: databaseId to allow for easier querying without scans when just assetId is provided.
-   Updated $inputMetadata for pipeline inputs to separate out asset and file metadata fields
-   Updated DeveloperGuide.md documentation for pipelines on all the input variables and their formats that are passed to pipelines.
-   Moved documentation files and diagrams to new Documentation folder, added Costs.md documentation to reduce main README.md size.
-   Update package dependencies and fixed any associated breaking changes

### Known Outstanding Issues

-   With updating to support multiple S3 buckets, there are scenarios that can occur where if there are multiple buckets/prefixes across different databases where the assetId are now the same, there will be lookup conflicts within Asset Versions, Comments and subscriptions functionality. This can only occur right now with manual changes/updates as done directly to S3 as assetIds generated from VAMS uploads still generate unique GUIDs.
-   Using the same pipeline ID in a GLOBAL and non-GLOBAL database will cause overlap conflicts and issues.

## [2.2.0] (2025-09-31)

This version includes significant enhancements to VAMS infrastructure, a complete overhaul of asset management APIs/Backend/UI, addition of supporting external IDP authentication, and various bug fixes. Key improvements include more flexible naming conventions, separation of assets and files, enhanced file management capabilities, new asset versioning, new use-case pipelines, global workflows/pipelines, and improved upload/download functionality.

### ⚠ BREAKING CHANGES

-   CDK Configuration files must be updated to include the new required fields. See ConfigurationGuide.md and template configuration files for new formats.
-   Asset and Database DynamoDB table fields and formats have changed, which require using the migration scripts after CDK deployment to update the new field values. See /infra/deploymentDataMigration/v2.1_to_v2.2/upgrade/v2.2_to_v2.3_migration_README.md for details on using the migration scripts to upgrade your DynamoDB databases after deployment.
-   Due to VPC subnet breakout changes, this may break existing deployments. It is recommended to use an A/B deployment if you run into subnet configuration issues.
-   Due to Cognito changes, a new Cognito user pool may be generated on stack deployment. To migrate existing users from the previous user pool, follow the following blog instructions: https://aws.amazon.com/blogs/security/approaches-for-migrating-users-to-amazon-cognito-user-pools/

**Recommended Upgrade Path:** A/B Stack Deployment with data migration using staging bucket configuration and upgrade migration scripts for DynamoDB tables in `./infra/upgradeMigrationScripts`

### Contributions

-   Lockheed Martin Corporation (LMCO) - LMCO has significantly contributed to this release with both external and internal pull requests (https://github.com/awslabs/visual-asset-management-system/pull/204)

### Features

-   Database, Pipeline, Workflow, Tag, Tag Types, Role, and Constraints id/names no longer need to follow as strict regex guidelines. New Regex: ^[-_a-zA-Z0-9]{3,63}$
-   AssetId no longer needs to follow as strict regex guidelines. New Regex (regular filename regex): ^(?!._[<>:"\/\\|?_])(?!.\*[.\s]$)[\w\s.,\'-]{1,254}[^.\s]$'
-   File paths no longer need to follow as strict regex guidelines and now allow for deep pathing. Some restrictions apply to specific input paths for auxiliary asset previews and pipeline output paths.
-   The asset upload API and backend along with many associated supporting asset API backends have been rewritten to support new features, security, and performance improvements.
    -   The old uploadAsset, uploadAssetWorkflow, and s3scoped access APIs and backend have been removed
    -   A new uploadFile (initialize, complete, createFolder), createAsset, and assetService (edit asset) have been created to support separation of assets and files. UploadFile now fully supports S3 Signed URL uploads for better security and performance (replaces providing UI with scoped S3 access).
    -   ScopedS3Access removal provides benefits as previous implementations had issues with scoped role timeouts, different authentication implementations in VAMS, parallelization issues, which prevented file validation, asset file overwrite issues, and more.
    -   New AssetUploads DynamoDB created to track uploads between initializations and completions
    -   IngestAsset API, intended for backend data system ingresses, wraps the new APIs as an all-in-one API caller.
    -   UploadFile is now split into two stages for upload, which allow for multiple files and multiple parts per file to be specified for better performant uploads of large files
    -   Assets now are better built to support a range of different files, including no files. The separation allows for better reliance on S3 functionalities to support file versioning.
    -   AssetType on assets are now specified as "none" (no files on asset), "folder" (multiple files on an asset), or single file extension (single file on asset and provides the extension, as before)
    -   File Uploads will go to a temporary S3 location on stage 1 while stage 2 upload completions performs checks, including for malicious file extensions or MIME types, before moving files into an asset for versioning
    -   File uploads restricted to 10 upload initializations (stage 1) per-user per-minute to minimize DDoS possibility and maximize system availability
    -   UploadFile now supports upload types for assetFiles and assetPreview to better support the separation of the uploads. This will allow for future enhancement support of adding filePreviews, separate from assets.
    -   Workflow execution final steps, which return files to an asset, are now rigged to use the new uploadFile lambda to support all file checks before versioning as part of an asset and to now support pipelines that return asset previews. This process follows an alternate external upload stage where presigned URLs are not needed due to the direct access nature of pipelines into the assets bucket (still uses temporary locations for security).
    -   AssetFiles API now brings back additional information for each file such as size, version, version created, and if the file is a versioned prefix folder or a file
    -   Support for empty asset creation and/or throughout life cycle of an asset (uploads no longer required during asset creation)
    -   Asset uploads in the UI now keep their original filenames and no longer change them to the asset name.
    -   The concept of "primary file" in an asset has been removed to support assets being truly multi-file
    -   New File URL Sharing action/modal for files in the file manager that generates presigned URLs for all files or folder selected
-   **Web** The front-end asset upload has been heavily modified to support the new backend asset changes
    -   Now supports choosing multiple files and/or entire folders
    -   Files now keep their original names and are no longer changed to the assetName
    -   Supports the presigned URL and multi-stage API calls needed now for an upload (including support for splitting large files into multiple parts for parallel upload)
    -   Supports stage and file error recovery options, including proceeding with certain failed uploads that will be discarded
    -   Comments are no longer a supported field as part of upload, as this functionality has been moved to creating asset versions
-   The assetFiles API now supports additional paths for functionality including `../fileInfo`, `../createFolder`, `../moveFile`, `../copyFile`, `../archiveFile`, `../unarchiveFile`, `../deleteFile`, `../deleteAssetPreview`, `../deleteAuxiliaryPreviewAssetFiles`,`../revertFileVersion`. ListFiles now provides additional data about each file.
-   **Web** The front-end asset download for multiple files has been updated to support downloading an entire folder's worth of files in parallel
    -   Note: This still fetches individual files based on their presigned URL for automation, it does not pre-ZIP files on a server and may still cause issues if hundreds or thousands of files need to be downloaded
-   **Web** The asset viewer file manager has been rewritten to support new features and richer user experience
    -   Instead of having a separate redundant icon view of files in the right pane of the file manager, it now shows file information such as file name, path, size, and any version information. For top-level asset nodes and image files, this will show the Preview file or actual file (image type files) now. This supports preview files now for both assets and files. See DeveloperGuide.md for documentation on preview file support (non-auxiliary).
    -   Added buttons for various downloads of files and folders
    -   Added ability and button to create sub-folders in an asset
    -   ViewAsset button still shown on files for asset 3D visualization, file-specific metadata, and file versioning
-   **Web** Execute Workflow in View Assets now allows the user to choose which file on the asset will be processed due to the new multifile support implementation of assets
-   **Web** Enhanced asset file management capabilities with comprehensive file operations:
    -   Added new API endpoints for file operations: fileInfo, moveFile, copyFile, archiveFile, unarchive, deleteFile, getVersion, getVersions, revertFileVersion
    -   Implemented file versioning with UI for showing files, knowing what version you are looking at, and reverting to a version
    -   Implemented file archiving which uses S3 delete markers (versus a permanent delete that removes the entire file)
    -   Added support for cross-asset file copying with proper permission validation (must stay within the same VAMS database)
    -   Implemented detailed file metadata retrieval including size, storage class, and version history
    -   Added permanent file deletion with safety confirmation to prevent accidental data loss
    -   Implemented proper error handling and validation for all file operations
    -   Asset files and versions will now show a flag for archived files and indicate if the asset is part of the current version files' version
-   **Web** (Breaking Change) All new asset versioning capability and version comparisons
    -   Asset versions must now be manually created and will no longer auto-create when editing the asset or uploading files
    -   New APIs are defined for asset versioning for create, get, and revert options
    -   Asset table has changed fields and new asset version and asset file versions tables are created, which require a database migration script to be used when upgrading from a previous VAMS version
    -   Displayed as Versions tab under Asset Viewer and labels throughout (such as on file versions) to show file versions included in the current asset version (or mismatched)
-   **Web** New tabbed design for Viewing Asset
    -   Moved comments page for assets to now be a tab under view assets
-   Assets as a whole now support both permanent deletion and archiving
    -   Note: currently unarchiving an asset as a whole doesn't exist yet
-   Turned off the wireframe view for the 3DOnlineViewer for viewing models
-   Disabled for now the ability to see/view assets in Workflow Editor and the ability to Execute Workflows from Workflow Editor (doesn't fit the current functionality implementation)
-   Updated API and associated viewers/files for aux asset streaming endpoint from /auxiliaryPreviewAssets/stream/{proxy+} to /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}
    -   Added additional validation checks to make sure users only stream assets that belong to the asset ID provided
-   Subscription emails for assets will now trigger any time an asset itself changes or versions, or one of its files changes
-   Added infra configuration option for basic GovCloud IL6 compliance checks for features/services enabled or disabled
-   Added presignedUrlTimeoutSeconds configuration to infra config, moved credTokenTimeoutSeconds under useCognito configuration option (previously used one configuration value for both)
-   Changed metadata API paths (to standardize) from '/metadata/{databaseId}/{assetId}/' to '/database/{databaseId}/assets/{assetId}/metadata'
-   Created new DynamoDB table for workflow executions, migration of old jobs will not occur. The new table has better fields for tracking of workflow database ID to now cause conflicts of same name between databases.
-   Added capability to support multiple S3 buckets for assets now for a solution
    -   (Breaking Change) New CDK configuration options are added and required for defining asset buckets (created with solution or external load), see DeveloperGuide and ConfigurationGuide for details
    -   **Web** Databases now allow you to select which bucket/prefix the database will use for its assets
    -   New DynamoDB table for S3 Asset Buckets is set up with CDK deployment to define available asset S3 buckets
    -   Direct changes to asset S3 buckets are allowed and will be synced back to VAMS. New asset prefix files will create new assets and databases based on configuration details defined. File changes within an asset will be indexed and pulled with any new API requests involving asset file operations
    -   **Web** Pipelines in the navigation menu is now under "Orchestrate and Automate"
-   Standardized API route paths that had /databases* (plural) to /database* (singular)
-   Changed VPC subnet to now break out subnets for isolated, private, and public. Previously, only private (which was actually isolated) and public existed.
    -   For those using external VPC and subnet import configuration, previously private subnet IDs should now be copied into isolated subnets configuration option.
-   Added a new use-case pipeline and configuration option for `RapidPipeline` that optimizes 3D assets using mesh decimation & remeshing, texture baking, UV aggregation, and more.
    -   RapidPipeline can also convert assets between GLTF, GLB, USD, OBJ, FBX, VRM, STL, and PLY file types.
    -   Pipeline can be called by registering 'vamsExecuteRapidPipeline' lambda function with VAMS pipelines / workflows.
-   Updated backend infrastructure configuration options and functionality to support External OAuth IDP systems besides AWS Cognito. Includes many additional infrastructure configuration settings.
-   **Web** Added web support for External OAuth IDP configuration.
-   Added configuration option `addStackCloudTrailLogs` for creating AWS CloudTrail log groups and trails for the stack. This is defaulted to `true`.
-   Added configuration option `useAlb.addAlbS3SpecialVpcEndpoint` for creating the special S3 VPC Interface Endpoint for ALB deployment configurations. This is defaulted to `true`. See documentation for this setting if turned off.
-   **Web** Added infrastructure configuration option `webUi.optionalBannerHtmlMessage` for adding a persistent banner (HTML) message at the top of the WebUI.
-   **Web** Added capability to define which tag types are required to be added to an asset. If tag types are required, at least one of the defined tags on the tag type must always be included on the asset.
-   The ingestAsset API now supports passing in tags (to support required tag types).
-   Changed UserId to no longer need to be an email, added a new LoginProfile table to track user emails for notification service which gets updated from JWT tokens or organization custom logic for retrieving user emails. New API for updating LoginProfile added to web login.
-   Enabled Cognito user pool optional Multi-Factor Authentication (MFA) for created accounts across TOTP or SMS. **Note:** SMS sending requires additional AWS Cognito / SNS setup to a SNS production account and origination identity (if sending to US phone #'s).
    -   Added backend broken out custom logic and flag to know if a user is logged in with MFA or not. For external OAuth IDP implementations, this logic must be tailored to the IDP system.
-   Enabled ability for a VAMS external IDP authentication system to report back if a user is logged in via MFA through an additional MFA IDP scope request. This can be configured via infrastructure configuration script by specifying a specific scope for MFA. Leaving this configuration null turns off external IDP MFA support.
    -   **Web** The external IDP login will show a MFA login button if a mfa scope configuration request is defined.
-   **Web** Added capability to set on a role if it requires the logged in user to have authenticated via MFA in order for any constraints against that role to take effect. If MFA is not turned on in the selected authentication system, this would effectively disable the role as no user would satisfy the criteria.
-   Added new feature that gives users the ability to edit pipelines after initial creation. Users also have the option to update all workflows that contain the edited pipeline. The EDIT feature can be found as a button on the Pipelines page.
-   **Web** Added a new file viewer for video files using the HTML5 video player component. You can now view and stream files of types: ".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v"
-   **Web** Added a new file viewer for audio files using the HTML5 audio player component. You can now view and stream files of types: ".mp3", ".wav", ".ogg", ".aac", ".flac", ".m4a"
-   Added a new use-case pipeline and configuration option for `ModelOps` complex tasks such as file format conversions, optimizations for 3D assets, and generating image captures of 3D models.
    -   VAMS pipeline registration `inputParameters` will define for each pipeline registration what the output file extension type(s) will be. ModelOps can output multiple file types in one execution. Pipeline can be called by registering 'vamsExecuteModelOps' lambda function with VAMS pipelines / workflows.
-   Pipelines and workflows can now be created under a GLOBAL database. GLOBAL database workflows can be executed across all assets across all databases. The GLOBAL database is a reserved keyword now which implies that an entity applies to all databases (right now only workflows/pipelines) This now allows for the capability of registering pipelines/workflows automatically as part of use-case pipeline deployments as a database no longer needs to exist (GLOBAL).
-   Asset links backend APIs were re-written to accommodate tracking databaseId with to/from assets, tracking tags on asset links, and now tracking metadata against asset links
    -   New DynamoDB tables are created for these new trackings, one of which requires a data migration script to move data from the old tables.
-   **Web** Asset Links under View Asset is now part of the tabbing window under "Relationships"
-   **Web** Asset links / relationships now has a new look similar to the new file manager to track relationships. This is in both View Assets and on Asset upload for new assets.
    -   Metadata key/pair values can now be tracked against asset links with String and XYZ type. Translation, Rotation, and Scale are hard- XYZ typed fields that can be used before adding custom metadata
    -   Ability to see child sub-trees from all child assets recursively down
-   **Web** Add file primary type attribute viewing and setting such as "primary", "lod1" - "lod5", and a custom primary type. These can be set on any file and are saved as metadata in S3 on the file. This is useful for identifying what the primary files are as part of an asset and if they are the prime or a particular level of detail (lod), or other designation. There is no logic tied to this value yet in VAMS but can be used for visual identification or in custom logic implementations.
-   **Web** Added option to show asset and file preview thumbnails on the Asset Search page.
-   Added a '/api/version' GET API path (NoOp authorizer) to get back the version of the current deployment of VAMS. This is stored in the config.ts file during CDK deployment and should be updated with VAMS version rollouts.
    -   Added '/database/{databaseId}/assets/{assetId}/setPrimaryFile' API endpoint to support this and returns this value as part of listing files and returning file information as part of those respective APIs.
-   Added feature in CDK configuration to allow for unsafe-eval web features. This is turned off by default as it may require an organization's security team to evaluate this before enabling. This is implemented to allow for future plugins and libraries that require this flag to be enabled in the web browser.
-   Add CDK Configuration options for API Rate and Burst limits to prevent denial of service situations. Adjust based on your traffic and your AWS account limits for both API Gateway and Lambda invocation allowances. Default configuration is set to 50 API requests per second and 100 bursts per second.
-   VendedLogs CloudWatch log groups have been set as a default retention of 1 year in the core CDK stack
-   (Draft Implementation) Started overhaul of lambda backend unit tests that were previously outdated and non-functioning. Unit tests as of 2.2 still have many non-functioning (skipped) tests that will need to be corrected. Passed tests will also need additional validation and coverage evaluation.

### Bug Fixes

-   Fixed permission caching in lambdas to actually reset caches after 30 seconds per lambda per user. Currently since v2.0 caches have been invalidating inconsistently.
-   Fixed opensearch lambda event source mapping for regions that don't support event source tagging yet (i.e., GovCloud) [bug introduced in v2.1.0 with CDK version upgrade].
-   Additional checks are made for valid parameter data in the asset deletion/archiving service.
-   Fixed local web local development support, updated documentation for new local development processes.
-   Fixed numerous lambda functions that were not adhering to the VPC/subnet configuration options for placing behind a VPC from v2.0 update.
-   Fixed more validation bugs to ensure API fields that take in arrays are actually arrays.
-   Miscellaneous minor bug fixes across web and backend components.
-   Fixed some multi-file/folder upload issues in UploadAssetWorkflow, Path Validation, and ScopedS3 retries
-   Fixed bug where asset search results using OpenSearch were not paginating correctly when total results went over 100
-   Fixed bug where asset search result filters for database may restrict what users can search on based on previous results returned
-   Fixed scrolling issues with Firefox browser
-   Fixed bug with PointClouder viewer / pipeline from executing and showing final outputs
-   Fixed various bugs with asset comments editing and deleting
-   Fixed various UI and backend bugs related to the asset management overhaul
-   Fixed fullscreen mode on visualizers not working on certain visualizers after exiting from a previous fullscreen session; removed compact mode as this had no benefit.
-   Fixed OpenSearch search to exclude `bool_` fields that may get added to dynamodb tables it indexes. Wildcard searches in query don't work on bool fields.
-   Added check to metadataschema creation API to make sure the database ID exists before creating the schema
-   Fixed bug with Asset Search that some columns were returning an API 500 error when trying to sort, fixed issue with deleted databases and records showing in aggregate results still
-   Fixed a bug where OpenSearch SSM parameter didn't factor in all config naming variables, causing issues with deploying multiple VAMS stack instances to the same region.

### Chores

-   Added more input variables for use in pipeline lambdas called such as bucketAssetAuxiliary, bucketAsset, and inputAssetFileKey. This is in addition to the predetermined "easy" paths setup for pipeline use
-   Added more error checks and outer workflow abort procedures for workflows/pipelines in use-case pipelines
-   Updated auxiliary asset handling to match the new asset location keys and handling
-   Updated workflow execution to handle new asset location keys, bucket, and handling
-   Created new DynamoDB workflow executions table (old one will remain as deprecated to not lose data) to store better format for lambda storage and retrieval
-   Modified workflow executions API to '/database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}' and also added '/database/{databaseId}/assets/{assetId}/workflows/executions/' to get all executions for an asset
-   Subscription SNS topics now store databaseId along with assetId in the topic name to prevent future conflicts
-   Create/Execute workflow backend update to support new asset management file/bucket/prefix locations, to be more dynamic based on the calling asset and file
-   Updated S3 asset bucket event notifications to be a SNS->SQS fan-out for bucket sync/indexing and other bucket subscriptions like for the PcPotreePreview pipeline
-   Cleaned up and removed backend and UI files and components that were no longer needed and/or deprecated related to assets
-   **Web** Cleaned up unused web files and consolidated functionalities for authentication and amplify configuration setting.
-   Upgraded lambda and all associated libraries (including use-case pipelines) to use Python 3.12 runtimes.
-   Upgraded infrastructure NPM package dependencies. Note: This switches CDK to use Node 20.x runtimes for Lambdas used for CustomResources or S3 Bucket deployments.
-   Optimized some backend lambda initialization code in various functions and globally in the casbin authorization functions for cold start performance improvement.
-   Updated S3 bucket name for WebAppAccessLogs for ALB deployment (to be based on the domain name used `<ALBDomainName>-webappaccesslogs`) to help with organization policy exceptions.
-   Added scripts and documentation for external oauth IDP and API local development servers.
-   **Web** Turned on amplify gen1 Secure Cookie storage option.
-   Updated GenAIMetadataLabeling pipeline container to use the latest blender version when deploying due to Alpine APK restrictions on holding earlier versions.
-   Switched web API calls to use Cognito user access token for all requests authorizations instead of Id token. Created separate parameter for scopedS3Access to pass in ID token for this specific API call that needs it.
-   Added logic to prefilter asset OpenSearch querying to only databases the user has access in order to increase performance for final asset permission checks for large asset databases
-   Updated CDK library dependencies to convert from alpha versions to regular implementations
-   Added Stack Formation Template descriptions
-   Added CSP header policies to ALB deployment listener on top of injecting into REACT front-end
-   When using lambdas behind VPC (`useForAllLambdas` setting), this now needs and sets up 2 subnets instead of 1 (best practice for Lambdas behind VPCs)
-   Updated documentation for developer deployment machines to use Node version 20.18.1
-   Updated README documentation with new application screenshots

### Known Outstanding Issues

-   With updating to support multiple S3 buckets, there are scenarios that can occur where if there are multiple buckets/prefixes across different databases where the assetId are now the same, there will be lookup conflicts within Comments and subscriptions functionality. This can only occur right now with manual changes/updates as done directly to S3 as assetIds generated from VAMS uploads still generate unique GUIDs.
-   Using the same pipeline ID in a GLOBAL and non-GLOBAL database will cause overlap conflicts and issues.
-   There is an issue with OpenSearch recognizing asset fields as numbers if they contain all numbers instead of strings. Future updates will provide utility script to clear OpenSearch index and rebuild with the new mapping schema. Avoid using asset names or descriptions with all numbers to avoid them showing blank in asset search.
-   There is an issue with using lambdas behind a VPC using the `useForAllLambdas` setting where API Gateway produces 503 service unavailable errors when using this setting. This needs to be tracked down if this is a VAMS issue or otherwise.

## [2.1.1] (2025-01-17)

This hotfix version includes bug fixes related to dependency tools and library updates.

This release may require a installation of the latest aws-cdk library to either your global npm or as part of your local VAMS infra folder. Please re-run "npm install" in VAMS infra to install the latest local dependencies for existing deployments.

### Bug Fixes

-   Fixed and added Poetry export plugin library used during Lambda layer building due to Poetry no longer including "export" as part of the core library.
-   Fixed Dockerfile container environment variable formats to no longer use the deprecated Docker format. `ENV KEY VALUE` -> `ENV KEY=VALUE`
-   Fixed 3D Metadata Labeling pipeline use-case to use the latest Blender version due to Alpine APK support deprecation for earlier specified versions.
-   Fixed 3D Metadata Labeling pipeline use-case state machine Lambda to not hard-code the `us-east-1` region for IAM role resource permission and use the stack-deployed region instead.
-   Updated aws-cdk dependency versions to the latest and updated GitHub CI/CD pipeline build checks

## [2.1.0] (2024-11-15)

This minor version includes changes to VAMS pipelines, use-case pipeline implementations, and v2.0 bug fixes.

Recommended Upgrade Path: A/B Stack Deployment with data migration using staging bucket configuration and upgrade migration scripts for DynamoDB tables in `./infra/upgradeMigrationScripts`

### ⚠ BREAKING CHANGES

-   Due to packaged library version upgrades in the solution, customers must make sure they are using the latest global installs of aws cli/CDK
-   Pipelines are now changed to support a new pipelineType meaning, and the old pipelineType was renamed to pipelineExecutionType.
-   Execution workflow input parameter names to pipelines have also changed, which can break existing workflows/pipelines.

Due to DynamoDB table structure changes, A/B Stack deployment with migration script is recommended if there are existing pipelines that need to be automatically brought over.

### Features

-   Re-worked infrastructure CDK components and project directory structure to split out use-case pipelines (i.e., PotreeViewer/Visualizer Pipelines) from the rest of the lambda backend and stack infrastructures. This will allow for future upgrades that will split these components completely out into their own open-source project.
-   `PotreeViewerPipeline` (previously VisualizerPipeline) is now baselined to the new standard use-case pipeline pattern to support external state machine callbacks (i.e., from VAMS pipeline workflows)
-   -   `PreviewPotreeViewerPipeline` (previously VisualizerPipeline) can now be registered and called from VAMS pipeline workflows (suggested to be called from a preview type pipeline) via the 'vamsExecutePreviewPcPotreeViewerPipeline' lambda function.
-   Added a new use-case pipeline and configuration option for `GenAiMetadata3dLabelingPipeline` that can take in OBJ, FBX, GLB, USD, STL, PLY, DAE, and ABC files from an asset and use generative AI to analyze the file through 2D renders what keywords, tags, or other metadata the file should be associated with. Pipeline can be called by registering 'vamsExecuteGenAiMetadata3dLabelingPipeline' lambda function with VAMS pipelines / workflows.
-   Added a new use-case pipeline and configuration option for `Conversion3dBasic` that can convert between STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, and XYZ file types. VAMS pipeline registration `outputType` will define for each pipeline registration what the output file extension type will be.
-   -   This pipeline for non-GovCloud deployments is enabled by default in the infrastructure configuration.
-   **Web** Added `pipelineExecutionType` to VAMS pipelines (previously `pipelineType`) and added a new context to `pipelineType`. Current pipeline types are `StandardFile` and `PreviewFile`. These are implemented to support future roadmap implementations of different pipeline types and auto-executions options on asset file uploads.
-   **Web** Added `inputParameters` to pipelines to allow the optional specification of a JSON object which can be used within a pipeline execution to set pipeline configuration options. This is set at the time of creating a VAMS pipeline.
-   Added `inputMetadata` to pipeline inputs which automatically pulls in asset name, description, tags, and all metadata fields of the asset to a pipeline execution. This can also be used in the future to pull through user-defined inputMetadata at the time of an execution with additional UI/UX.
-   Changed `inputPath` and `outputPath` of pipeline function execution inputs to `inputS3AssetFilePath` and `outputS3AssetFilesPath`
-   Added `outputS3AssetPreviewPath`, `outputS3AssetMetadataPath`, and `inputOutputS3AssetAuxiliaryFilesPath` pipeline execution parameter inputs to support different location paths for asset data outputs and writing to asset auxiliary temporary path locations
-   Added `outputType` for user-specified expected file extension output for pipelines based on the VAMS pipeline registration. OutputType is not enforced and is something pipelines need to work into their own business logic as appropriate.
-   -   All asset write-back locations are now temporary job execution specific to allow for better security, file checks, proper back-versioning into an asset, and to start abstracting pipelines from writing directly to assets. Once the UploadV2 process is completed in a future update, direct access by use-case pipelines to S3 asset buckets will be removed in favor of API uploads / presigned URLs for storage abstraction.
-   Updated `processWorkflowExecutionOutput` lambda function (previously `uploadAllAssets`) to also account for metadata data object outputs of pipelines to update against assets. Preview image output logic is stubbed out but will not be fully implemented until the new upload / storage process overhaul is completed in a future version.
-   Added `credTokenTimeoutSeconds` authProvider config on the infrastructure side to allow manual specification of access, ID, and pre-signed URL tokenExpiration. Extending this can fix upload timeouts for larger files or slower connections. Auth refresh tokens timeouts are fixed to 24 hours currently.
-   -   Implements a new approach for s3ScopedAccess for upload that allows tokens up to 12 hours using AssumeRoleWithWebIdentity.
-   **Web** Added PointCloud viewer and pipeline support for `.ply` file formats, moved from the 3D Mesh 3D Online Viewer
-   **Web** The asset file viewer now says `(primary)` next to the asset's main/primary associated file. The primary file is what gets used right now for pipeline ingestion when launching a workflow.
-   Changed access logs S3 bucket lifecycle policy to only remove logs after 90 days
-   Added lifecycle policies on asset and asset auxiliary bucket to remove incomplete upload parts after 14 days

### Bug Fixes

-   Fixed CreateWorkflow error seen in v2.0 (Mac/Linux builds) with updated library dependencies and setting a standardized docker platforms across the board to `linux/amd64`
-   Re-worked PreviewPotreeViewerPipeline (previously VisualizerPipeline) state machine and associated functions to properly handle errors
-   Fixed benign logger errors in OpenSearch indexing lambda function (streams)
-   Fixed existing functionality with `processWorkflowExecutionOutput` (previously `uploadAllAssets`) not working
-   Fixed pipeline execution to properly account for asset file primary key names that contain spaces. Previously, could cause pipelines to error on execution.
-   **Web** The asset file viewer now appropriately shows multiple files that are uploaded to the asset
-   **Web** Hid the `View %AssetName% Metadata` button for top-level root folder on asset details page file manager that led to a blank page. The metadata for this is already on the asset details page.
-   Fixed GovCloud deployments where v2 Lambda PreTokenGen for Cognito are not supported, reverted to v1 lambdas that only support Access Tokens (instead of both ID and Access token use for VAMS authorizers)
-   Fixed GovCloud deployments for erroneously including a GeoServices reference that is not supported in GovCloud partition
-   Fixed KMS key IAM policy principals (for non-externally imported key setting) to include OpenSearch when using OpenSearch deployment configurations
-   Added logic to look at other claims data if "vams:\*" claims are not in the original JWT token. This is in preparation for external IDP support and some edge case setups customers have.
-   Fixed CDK deployment bug not deploying the required VPC endpoints during particular configurations of OpenSearch Provisioned, Not using all Lambda's behind VPCs, and using the option to use VPC endpoints
-   **Web** Fixed bug where adding asset links had swapped the child/parent asset (WebUI only bug, API direct calls were not affected)
-   Fixed CDK deployment bug of encrypting the WebAppLogsBucket when deploying with ALB and KMS encryption. The WebAppLogsBucket cannot be KMS encrypted when used for ALB logging output.
-   Fixed bug for exceeding PolicyLimitSize of STS temporary role calls in S3ScopedAccess used during asset upload from the Web UI when KMS encryption is enabled.
-   Increased CustomResource lambda timeouts for OpenSearch schema deployment that caused issues intermittently during GovCloud deployments
-   Fixed bug in constraint service API that was saving constraints on POST/PUT properly but was erroring on generating a 200 response resulting in a 500 error
-   Fixed bug in OpenSearch indexing (bad logging method) during certain edge cases that prevented adding new data to the index
-   Fixed bug in CDK storageResource helper function where S3 buckets were not getting the proper resource policies applied

### Chores

-   VisualizerPipeline now re-named to PreviewPotreeViewerPipeline as the previous name was too generic and other "visualizer" or viewer pipelines may exist later
-   'visualizerAssets' S3 bucket renamed to 'assetAuxiliary'. This bucket will now be used for all pipeline or otherwise auto-generated files (previews/thumbnails) associated with assets that should not be versioned
-   'visualizerAssets/{proxy+}' API route and related function re-named to 'auxiliaryPreviewAssets/stream/{proxy+}'. This function is used for retrieving auto-generated preview files that should be rapidly streamed such as the PreviewPotreeViewerPipeline files.
-   Renamed and moved `uploadAllAssets` lambda function handler. It is now `processWorkflowExecutionOutput` and moved to the `workflows` backend folder
-   Updated Workflow ListExecutions to write stopDate, startDate, and executionStatus back to DynamoDB table after an SFN fetch where the execution has stopped. This is done for performance / caching reasons.
-   Workflow executions are now limited to only 1 active running execution per workflow per asset. This helps prevent workflows from clobbering each other and preventing other errors and race conditions
-   Updated a pipeline's default taskTimeout to 24 hours and taskHeartBeat to 1 hour unless otherwise specified. Previously, it defaulted to the service default which was up to a year. This helps prevent runaway asynchronous processes that never properly return and closeout workflow executions.
-   Added some external sfn token heartbeats into the new and existing use-case pipeline implementations at the end of a container run. These heartbeat locations can still be improved, but it is expected that these pipelines take longer to run.
-   Workflow executions now pass the originating execution caller's username and request context, which can be used for lambda cross-call logic
-   Created an additional Casbin API check abstraction function which can be used to consolidate API permission check logic and simplify lambda handlers. Applied to all existing API-gateway accessible lambda handlers
-   Added CDK Stack output to display all VAMS Pipeline Lambda function names for all activated use-case pipelines that can be registered within the VAMS.
-   Added error for all use-case pipeline lambdas if executed with the wrong task_token / call-back setup (synch vs asynch) in VAMS
-   Added draft lambda functions for the uploadV2 feature expected. Draft function not yet ingested into CDK for deployment.
-   Added security.txt file to website for AWS security reporting information.
-   Updated documentation on security, legal, and use notices.

### Known Outstanding Issues

-   Using s3ScopedAccess for Upload (currently in use by VAMS WebUI) can also cause synchronization issues due to race conditions between uploading and calling the asset upload APIs. Additionally handling very large file uploads and downloads (+1TB) can cause issues. Expect a future re-write to use solely pre-signed storage URLs for upload and a 3/4-step guided API call process for this to resolve this issue, similar to `ingestAsset` API used to test the core of this new method.

## [2.0.0] (2024-6-14)

This major version represents an overhaul to the CDK constructs to support more scalable deployment configurations with many additional CDK deployment features. It adds a new VAMS permission system with new Attribute-Based Access Control (ABAC) and Role-Based Access Control (RBAC) systems. Lastly, the overhaul has added business logic features to support new data structures around asset storage.

Recommended Upgrade Path: A/B Stack Deployment with data migration using staging bucket configuration and upgrade migration scripts for DynamoDB tables in `./infra/upgradeMigrationScripts`

### Highlights

1. **CDK Infrastructure Overhaul**: This release represents a major overhaul of the CDK constructs, splitting the core logic into multiple nested stacks to support more scalable deployment configurations.
2. **Configuration System**: A new CDK configuration system has been introduced using `config.json` and `cdk.json` files. Many previously implemented features, such as OpenSearch or Location Services, can now be turned on or off.
3. **New Configuration Options**: Numerous new configuration options have been added, such as VPC/subnet management, Application Load Balancer (ALB) static web support instead of CloudFront, KMS encryption, OpenSearch configurations (including the ability to turn off OpenSearch), and more. These options can be toggled based on specific deployment requirements.
4. **Security Controls**: A major aspect of this release focuses on security tightening and controls. Implementers will now be able to deploy across AWS partitions, including GovCloud, and have more control over WAF, FIPS, Lambdas in VPCs, and Docker SSL Proxy configurations.
5. **New Access Control System**: A new Attribute-Based Access Control (ABAC) and Role-Based Access Control (RBAC) system has been implemented, replacing the previous Cognito group-based access control. This provides fine-grained access control to various VAMS resources.
6. **Asset Tagging and Linking**: A new mechanism for adding tags and tag types to assets has been introduced, along with the ability to create parent/child and related-to links between assets within the same database.
7. **Image and PointCloud Viewers**: Support for Image and PointCloud file visualizations has been added, including an infrastructure data pipeline to support viewer conversions for LAS, LAZ, and E57 input formats.
8. **Upgraded File Manager**: The web assets viewer has a new file manager UI/UX for viewing asset files and provides functionality for uploading multiple asset files within folders.
9. **Email Subscription System**: A new email subscription system has been implemented which allows VAMS users to subscribe to various data changes. Asset data objects are the first to be implemented as part of this version to allow users to receive notifications when new asset file versions are uploaded.
10. **Performance and Bug Fixes**: Various performance improvements and bug fixes have been implemented, including API input validations, optimizations for OpenSearch indexing, log group naming, unique resource naming, and workflow execution handling.
11. **Deprecations and Removals**: SageMaker pipeline types have been removed to focus development efforts on Lambda pipelines.

### ⚠ BREAKING CHANGES

-   **Possible break** CDK configuration and feature switch system using `./infra/config/config.json` file. Some backwards compatibility with existing CDK deployment commands.
-   CDK overhaul to split core logic into 10+ nested stacks means that an in-place upgrade for existing stack deployments is not possible, use A/B deployment.
-   Lambdas converted into inline code functions with layers (away from Lambda ECR-backed containers).
-   (SEO breakage) Switch Web infrastructure to use React hash router instead of web router to support ALB configuration option, which breaks search engine optimizations (SEO).
-   New ABAC/RBAC systems will require new roles and constraints to be set up to allow application access. Existing Cognito groups will no longer be recognized, and user memberships must be transferred to the role and constraint mechanisms.
-   SageMaker is no longer a pipeline type available. Existing SageMaker pipelines should be converted to be executed from a lambda pipeline.
-   Restrict VAMS workflow pipelines to only have permission to lambdas that contain `vams` in the function name by default. If you have external pipeline lambdas, please add invoke permissions for them to the appropriate workflow execution role or update your lambda function name to contain `vams`.
-   Pipelines created using the default lambda artifact sample will now need to be re-created and re-inserted into workflows due to using different database fields to store the name of these.
-   `/assets/all` (PUT) API call is deprecated in favor of using the existing `/assets` (PUT) and the newer `/ingestAsset` (POST) API.
-   Previously created workflows of pipelines that had pipeline nodes that didn't use `wait_for_callback` need to be re-created/re-saved from the VAMS UI or modified in the AWS Console to remove `TaskToken.$` from node tasks parameters if there is no callback on that node.
-   API response bodies for data retrieval calls that return several records have been standardized to `responseBody: {message: {Items, NextToken}}`.

### Features

-   Implement CDK configuration system using `./infra/config/config.json` file.
-   -   Implement local Docker package build file configuration override to support customization in `./infra/config/docker/Dockerfile-customDependencyBuildConfig` (such as in cases of HTTPS SSL proxy certificate support).
-   -   Add default template files for various configuration environments (commercial (default- config.json), GovCloud).
-   Implement new CDK environment system variables using `./infra/cdk.json` file.
-   -   Add global stack resource tagging.
-   -   Add global new role permission boundary support.
-   -   Add global new role name prefix tagging.
-   Implement feature switch system and storage for Web feature toggling (new DynamoDB table).
-   -   **Web** Load/cache enabledFeatures as part of the backend web configuration load to the frontend.
-   Implement GovCloud feature switch which toggles other features on/off based on GovCloud service support and certain best practices.
-   Implement FIPS support configuration option.
-   Implement WAF configuration option (existing WAF functionality, ability to now toggle off).
-   Implement Global VPC configuration option used for particular configuration needs.
-   -   Support new VPC/Subnet generation.
-   -   Support an option for external VPC/subnet imports (instead of new VPC generation).
-   -   -   Added implementation of LoadContext Deployment configuration to support VPC context loading before main deployment.
-   -   Support an option for auto-adding*new VPC endpoints based on other configuration switches (*with some exceptions in particular configurations that will still auto-add regardless of this flag).
-   -   Support putting all deployed lambdas behind VPC (FedRamp best practices for GovCloud).
-   Implement ALB configuration option for static WebApp delivery (replaces CloudFront when enabled).
-   -   Requirement Note: ALB tied to a registered domain that must be provided.
-   -   Support WAF (if used) to deploy globally or regionally based on ALB/CloudFront deployments.
-   -   Support for using public private subnets for ALB.
-   -   Support/Requirement for SSL/TLS ACM certificate import for ALB.
-   -   Support for optional externally imported Route53 HostedZone updating for ALB deployment.
-   Implement KMS CMK encryption configuration option for all*at-rest storage (*with some S3 bucket exceptions in particular configurations such as ALB use).
-   -   Support new key generation on stack deploy.
-   -   Support option for external CMK key import instead of new key generation.
-   -   Disable all KMS CMK keys use implemented previously when configuration feature disabled (e.g., S3 bucket SNS notification queues). Uses default/AWS-managed encryption when KMS CMK disabled.
-   Implement OpenSearch provisioned, serverless, or no (neither serverless nor provisioned enabled) open search configuration options; No open search will disable VAMS asset search functionality.
-   Implement location service configuration option and feature switch (existing location service functionality, ability to now toggle off).
-   -   **Web** Hides Map view from Assets web page when turned off.
-   Implement point cloud visualization configuration option (existing pipeline functionality, ability to now toggle off through configuration file).
-   Add VAMS upgrade migration scripts to support A/B deployments and data migration between stack deployments in `./infra/deploymentDataMigration`.
-   (Future Full-Implementation) Implement authentication provider configuration option and feature switch. Note: Currently, only the Cognito `useSaml` configuration flag is observed (moved from `saml-config.ts` file), other auth types will cause an unimplemented error.
-   Implement new initial ABAC/RBAC access control systems to allow for fine-grained access to various VAMS resources (built on the Casbin open-source library).
-   -   ABAC defines the primary constraints and access controls.
-   -   -   ABAC currently supports resources of Databases, Assets, and "APIs".
-   -   -   **Note** Databases and Assets control primary VAMS storage resources. APIs control access to top-level system functionality (administrative pages, pipelines/workflows, etc.).
-   -   RBAC roles map to ABAC constraints to allow for backward compatibility with role/group-based access systems.
-   -   ABAC constraints can also map directly to users if organizations choose to go solely with the ABAC system.
-   -   Removed the previous Cognito group and constraint system.
-   -   -   **Note** Starts to reduce dependency on Cognito functionalities.
-   -   Created default admin role and constraint groups on new VAMS deployment. Stack deployment user will be auto-added to this new role group.
-   -   All lambdas now check access against the new ABAC constraints system.
-   -   **Web** Allowed Web routes controlled by ABAC constraints.
-   -   **Web** Administrative UI pages to support roles, role membership, constraints, and constraint membership modifications.
-   Implement new tag and tag type mechanism for adding additional information on assets (tags/tag types are currently global across all databases).
-   -   **Note** Requirement that Tags must have a tag type assigned.
-   -   **Web** Ability to search tags on assets on the asset search page.
-   -   **Web** Ability to assign/unassign tags to assets on asset creation and asset editing pages.
-   -   **Web** Administrative UI pages to support system tag and tag type modifications.
-   Implement asset linking functionality to support parent/child and related-to links between assets in the same database. Limit set to 500 of any asset link types per asset.
-   -   **Web** Ability to add/remove links to assets on asset creation and asset editing pages.
-   Implement asset email notification subscriptions on asset modification.
-   -   **Note** Users must confirm the subscription for each asset subscribed to in their inbox due to the current SNS topic implementation method.
-   -   **Web** Ability to add/remove user subscription to an asset on the asset viewing page.
-   -   **Web** Administrative UI pages to support global asset email list changes.
-   Enhance asset ingestion API to support better pushing of assets from external systems into VAMS.
-   -   **Note** The current implementation does not yet support API Key implementation for authentication and must still have a JWT authentication token to validate the calling system.
-   -   **Web** Administrative UI debug pages to allow organization administrators to call the API with various JSON payload inputs from the VAMS webpage.
-   **Web** Added PointCloud viewer support with Potree Viewer and an optional infrastructure pipeline configuration option for Potree conversions for .laz, .las, and .e57 file types uploads.
-   The AssetName field now has a new restriction to only support up to 256 characters with the following regex: `^[a-zA-Z0-9\-._\s]{1,256}$`.
-   Email user IDs now follow the new restriction to only support the following regex: `^[\w\-\.\+]+@([\w-]+\.)+[\w-]{2,4}$`.
-   Implement Cognito client USER_PASSWORD_AUTH configuration option as `useUserPasswordAuthFlow` for organizations who cannot perform SRP calculations on some of their VAMS integrations. By default, this configuration option is set to false.
-   Upgrade Cognito to insert VAMS claims tokens into both ID and Access tokens, which helps with confusion on 500 service errors when using the Cognito access token for API authentication.
-   **Web** Add a new file manager viewer on the view asset page to provide a better visualization and upload experience for multiple files and folders.
-   **Web** Add a new Image viewer for image type assets (non-preview files). Preview images are still supplemental on image asset files, which can be used for thumbnails, as an example.

### Bug Fixes

-   OpenSearch indexes now properly update when asset details are changed.
-   Change certain log group names to add the `/aws/vendedlogs` prefix to fix the issue of reaching the maximum CloudWatch policy character count on AWS accounts with many current/past resource deployments.
-   Fix the unique name generator for certain resources to fix character count limit issues and be more deterministic across VAMS (re-)deployments.
-   Added additional parameter input validations for API calls and fixed various 500 service errors based on malformed requests.
-   Fix workflow execution bug that caused errors across all workflow executions that didn't use the `wait_for_callback` flag in a lambda pipeline. This bug fix requires the re-creation/re-saving of all applicable workflows from the VAMS UI or manual adjustment in the AWS Console of created state machines to remove `TaskToken.$` from tasks parameters if there is no callback. This error was due to an AWS Step Functions service logic change.
-   Fixed OpenSearch query parameters to discard `#deleted` assets during the OpenSearch query and not just as a post-processing step. This should help prevent inconsistent results when wanting to limit search results to a single or a handful of total records.
-   Fixed OpenSearch asset searching to look at the passed-in searchbar 'query' value and properly search across all asset indexed fields (including all asset metadata). Previously, this did not work at all and just returned all results, all the time.

### Chores

-   Renamed VAMS stack to 'VAMS core' and changed the overall user-stack naming scheme, updated resource naming across the board to meet the new CDK construct rebuild
-   Upgraded lambdas and custom resources to use Python 3.10 and NodeJS 18_X runtimes
-   -   Consolidated runtime container deployment constant to the code configuration file
-   Broke up CDK constructs into 10+ nested stacks for scalability, compartmentalization, and fixing stack resource limit constraints
-   -   Restructured the infra folder to meet the new nested stack and constructs breakup
-   Converted lambdas into inline code functions with layers (away from Lambda ECR-backed containers)
-   -   Split lambdas into 2 layers depending on dependency package need. This reduces deployment sizes per lambda and improves runtime performance.
-   -   Added lambda layer package reduction logic to remove test/cache data in dependencies to further reduce layer MB size
-   -   Updated/Added backend folder structure and yarn packages to support new inline support and layer support
-   Used the 'esbuild' package library instead of docker for any NodeJS lambda deployment packaging
-   **Web** Switched Web infrastructure to use React hash router instead of web router to support the ALB configuration option
-   -   **Web** Added hash route deduplication code to help prevent/notify of possible link/navigate improper usage with `#` link prefixes
-   Switched CloudFront to use OAC instead of OAI for better security and functionality support for S3 origin support
-   Implemented Service ARN/Principal switcher and constants file to support different AWS partition, region, and FIPS use deployments
-   -   Introduced the genEndpoints script to update the service ARN/principal constants file. Note: Does not have all services, so some have to be added manually back to the constants file. Use with caution.
-   Switched Pipeline Visualizer lambdas to look at the "Add Lambdas to VPC" configuration flag to determine if they are in a VPC
-   Added all-around error checking and various deployment warnings on the CDK infrastructure configuration system flags
-   Modified the stagingBucket configuration tree entry to allow for future upgrades to support more different types of staging buckets
-   **Web** Removed file viewer options from the main web menu as they don't fit with the application web flow anymore
-   Updated CDK deployment outputs to match configuration options
-   Updated prettier/lint ignore files to ignore certain configuration and CloudFormation template files
-   Updated documentation/diagrams for configuration/environment/deployment modes along with different edge-case scenario deployments such as HTTPS SSL proxy certificate support
-   -   Updated pricing information for various configuration modes
-   Updated documentation to support the new outlined features
-   Added Casbin@1.34.0 (Apache-2 License) backend library package to dependency files
-   Restricted workflow pipelines to only have permission to lambdas that contain `vams` in the function name by default
-   Workflow pipelines created using the default lambda will now generate with a part-randomized string name to prevent same-name overlap
-   -   Note: Pipelines created using the default lambda artifact sample will now need to be re-created and re-inserted into workflows due to using different database fields to store the name of these.
-   Workflows created will now generate a state machine with a part-randomized string name to prevent same-name overlap
-   Added file extension and MIME content type checks on various upload and download file APIs. Currently checking for execution or script files which will be unallowed from VAMS.
-   Fixed the asset download API (and modified some of the parameters) which previously was not working, limited s3 scoped access STS call permissions to only be able to upload files. Expect the scoped s3 call to go away entirely as upload/download is revamped in future updates.
-   Added pagination query params and max limits to all API data fetches that don't return single item results. This also standardizes the response bodies to `responseBody: {message: {Items, NextToken}}`. This should allow VAMS to grow into a larger system that can support more than 1500 assets/records.
-   -   **Web** Added client-side pagination aggregation of total results. Full REACT page view with dynamic fetching not yet implemented.
-   **Web** Changed the front-end to use the download API for generating Presigned URLs instead of using the Amplify client logic with s3ScopedAccess
-   -   Starting to phase out s3ScopedAccess by reducing permissions and logic depending on it from the Amplify/client side. Expect full deprecation of this in the future.
-   **Web** Updated the 3D Model Viewer package to v0.12.0 and related dependencies
-   **Web** File model viewer now looks at a separate constants variable for file types to use with 3D Online Viewer (<https://github.com/kovacsv/Online3DViewer>)
-   -   **Note** This allows customers who wish to accept the opencascade LPGL license to view some CAD formats. These file types are excluded by default. See the documentation on how to enable.

### Deprecation / Feature Removal

-   SageMaker pipeline types have been removed from the available pipelines to run. Existing SageMaker pipelines should now be called via a lambda execution layer. This is due to better security implementation and the focusing of development efforts on lambda executions which can launch any other needed service.
-   The `/assets/all` (PUT) API call is deprecated in favor of using the existing `/assets` (PUT) and the newer `/ingestAsset` (POST) API. Backend business logic code for generating lambda components remains for use in the workflow API currently.
-   The S3 `bucket` field is no longer a needed input or response field for working with asset APIs. The bucket will now be fetched from environment variables instead, based on solution permissions.

### Known Outstanding Issues

-   Although v2 split the monolithic stack architecture from v1.5 and below into nested stacks, CDK deployment warnings may show up with certain configuration option combinations that the maximum resource count for the API nested stack is approaching the maximum limit (1000).
-   Uploading of asset files from the UI can cause time-outs if files are too large or networks are too slow due to the current hard limitation of 1 hour STS credentials using the s3ScopedAccess method. Using s3ScopedAccess can also cause synchronization issues due to race conditions between uploading and calling the asset upload APIs. Expect a future re-write to use solely pre-signed storage URLs for upload and a 3/4-step guided API call process for this to resolve this issue, similar to `ingestAsset` API used to test the core of this new method.

## [1.4.0](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/compare/v1.3.1...v1.4.0) (2023-07-28)

### ⚠ BREAKING CHANGES

-   Support uploading folders as assets (#92)

### Features

-   Easily replace terms Asset and Database ([#88](https://github.com/awslabs/visual-asset-management-system/issues/88)) ([ec54368](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/ec54368e68ad67d79b4bc129176a2ad486a6fbd7))
-   hiding sign up ([#104](https://github.com/awslabs/visual-asset-management-system/issues/104)) ([6d63177](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/6d631777fbb59d55d561e4f8827a46b0e2a240f0))
-   Support uploading folders as assets ([#92](https://github.com/awslabs/visual-asset-management-system/issues/92)) ([a5d768d](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a5d768d1e25508a48035e56f5353c760c1efdadd))
-   **web:** improvements to metadata component ([#110](https://github.com/awslabs/visual-asset-management-system/issues/110)) ([1ad3236](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/1ad32361a0981af971a36653b2a67f3c5e706338))

### Bug Fixes

-   dependency conflict was causing downloads to fail ([#94](https://github.com/awslabs/visual-asset-management-system/issues/94)) ([4cde458](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/4cde45874d099bf72cf4a69a5da8e17ab16ae81f))
-   download asset only if they are marked as distributable ([#106](https://github.com/awslabs/visual-asset-management-system/issues/106)) ([93f9c1b](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/93f9c1b89da9f1cd15e5eb8930c90150d80f1db4))
-   Release fixes ([#109](https://github.com/awslabs/visual-asset-management-system/issues/109)) ([d2060c2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/d2060c21dab0187d4231e5e0b66724bc561cd203))
-   repair first deployment with opensearch ([#107](https://github.com/awslabs/visual-asset-management-system/issues/107)) ([4e0ba30](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/4e0ba306295bd0bd254d3eb5ed74d4b8511b4ea2))
-   repair regression on createPipeline ([#93](https://github.com/awslabs/visual-asset-management-system/issues/93)) ([997241f](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/997241f39bed6ae9a5ce3e61a9cee80e136dad95))
-   simplify auth constraints screen ([#115](https://github.com/awslabs/visual-asset-management-system/issues/115)) ([463c8e7](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/463c8e7572d024ccc53d453d883dd55da14e2008))
-   single folder single file upload ([#95](https://github.com/awslabs/visual-asset-management-system/issues/95)) ([bb023ab](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/bb023ab5c5408a2fe219f1e7534489535626136f))

### Chores

-   **deps:** bump certifi from 2022.12.7 to 2023.7.22 in /backend ([#111](https://github.com/awslabs/visual-asset-management-system/issues/111)) ([95c2b7c](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/95c2b7c248e7cadc9cc6619bd9c2748575a961ff))
-   **deps:** bump semver from 5.7.1 to 5.7.2 ([#105](https://github.com/awslabs/visual-asset-management-system/issues/105)) ([c11edf2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/c11edf2aec5d09fe708a3fa955115a4333e0d791))

## [1.3.0](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/compare/v1.2.0...v1.3.0) (2023-06-13)

### Features

-   apigw authorizer for amplify config endpoint ([14062c7](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/14062c75ecfc27b9582f449e83cdff12bd94cb46))
-   enable cloudfront compression ([8459485](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/8459485e8bfa40644ab39ed46298df2ad687b1d2))
-   eslint now runs in ci for web and infra ([7985460](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/79854601eef67a991ec81bfe6ede6fb5feb76ff1))
-   Federated authentication using SAML ([6048fc0](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/6048fc0627d404e8dd0d6a8f7a75e3f32b190adb))
-   Fine grained authorization rule definition ([6d0646d](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/6d0646dde8e52edded01fa6ff31f2fb7c56c8915))
-   **infra:** consolidated settings for storage ([3309426](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/3309426e56e6b8805cee27784b57d5186682373a))
-   Role based access control scaffolding ([a0b57f2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a0b57f26c317386a8992a99cbd161b1a40ea4d7e))
-   Support long running pipelines with Step Functions' wait for callback feature. ([#76](https://github.com/awslabs/visual-asset-management-system/issues/76)) ([53d7c07](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/53d7c076923dd60ac49ac8b09c8df045516b7a28))
-   **web:** add new model visualizer supporting .obj, .gltf, .glb, .stl, .3ds, .ply, .fbx, .dae, .wrl, .3mf, .off, .bim file types ([b7f2686](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/b7f26869a0891304e6e85ee217da66003cb55265))

### Bug Fixes

-   automatically navigate to asset page once asset upload completes ([05d7bfe](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/05d7bfed1236499cb3d834caccbd8449094eca72))
-   cdk nag suppressions for python 3.9 and nodejs14.x ([#78](https://github.com/awslabs/visual-asset-management-system/issues/78)) ([926d159](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/926d159985b86541bcb5190167706cd64fea9e55))
-   ci.yml formatting ([46fd622](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/46fd62287f7af66c9dfa6bad631927099454f619))
-   congitoUsername --> cognitoUsername, added dependency to ([b2ca84f](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/b2ca84fab210ee9d1852f169fe9fc7c37d14fec4))
-   Hitting Execute Workflow button from the assets page doesn't work ([758902b](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/758902be9b78276bce30ba6ff54bd1c007cee10f))
-   **infra:** eslint fixes ([7c824c8](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/7c824c87b8859197b0b46b3fc9c97c80afafa92a))
-   renaming userpool causes failures in existing stack ([a798dec](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a798decd0c2fbeeda50933ba146b8890e0ae6abd))
-   resolve to fast-xml-parser 4.2.4 ([#89](https://github.com/awslabs/visual-asset-management-system/issues/89)) ([08a761c](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/08a761cfa39f5fb35f218cad00bbe11f269401a8))
-   resolves issue [#68](https://github.com/awslabs/visual-asset-management-system/issues/68), workflow editor added extra pipelines ([c390fe8](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/c390fe842577da65d253b884aefa35b9b66e850a))
-   saml callback url trailing slash variants ([51fe433](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/51fe433faa88e3c490a2315b828281a636bf5e6f))
-   Updated cdk-nag suppression ([46370a7](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/46370a779d9d10f06fa6c87334e8c5c7216b99e8))
-   updated the workflow editor ([#80](https://github.com/awslabs/visual-asset-management-system/issues/80)) ([78916ce](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/78916ced8bdae7e8a32bb44985347b6da9b6187e))
-   **web:** aligned grid definition with provided elements ([4ceb49b](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/4ceb49b3dd30cc369f73f7e7684d2233e2226268))
-   **web:** eslint eqeqeq ([d426baa](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/d426baa9ae75e523e60462aca1701a2bb1d7f626))
-   **web:** eslint fixes and exclusions ([d875f7e](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/d875f7e14c33ded5d7672f4326bda607193a8bef))
-   **web:** Fixed an event listener leak and Carousel radio buttons refactored to controlled components to reduce warnings. ([7ad8738](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/7ad8738ae288d3b8cd4cc7cbd51bcc472b55b9a6))
-   **web:** fixed event listener leak ([482bb48](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/482bb481525d7faffc6b7e07e6b4d34569c77a9f))
-   **web:** Handled undefined prop type with more grace. ([315abc9](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/315abc9d1074b67e8e194f0913a1d434132e6cf4))
-   **web:** Refactored input control to use refs. ([f91b8d7](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/f91b8d7f7f32fbc474fdb1c37c92dc48e979dbe0))
-   **web:** removed unused variables and imports ([6c3edd1](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/6c3edd10a2bdf3c40ee0b843ba063d6da054610d))
-   **web:** removed unused variables and updated useEffect dependencies. ([056a088](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/056a088eaca881a46320421f3fe303b80f4376aa))
-   **web:** Resolved a large stack trace logged to the console on the view asset screen. ([9e7fd81](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/9e7fd81a0ba1d62ee3e839807761603fa77c3475))
-   **web:** Suspense fallback requires a component rather than a function. ([a74a77c](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a74a77cf442b84998498e3f8a2d87d780867fadd))

### Chores

-   add lazy load for visualizers to view asset page ([5d3d8e2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/5d3d8e25d4fc1b51480c5ec46d6ce348108de031))
-   code split app, workflow editor, plotter ([03497f2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/03497f20194963c8e1207a3761bf31695f370af8))
-   **deps:** bump requests from 2.30.0 to 2.31.0 in /backend ([#82](https://github.com/awslabs/visual-asset-management-system/issues/82)) ([8347563](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/8347563e2b4ec6ec9a6759797c05f2978ee4d977))
-   made corrections to links in changelog ([bb7cec9](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/bb7cec9c411b6673c8090ac0b9aa79a13e6a377c))
-   prettier check added in github actions ([7337bf6](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/7337bf6169cbba65b72daa99a61382bf932f62ad))
-   prettier configuration and reformatting ([70971a9](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/70971a97272235f13f56c2379d2da41108171404))
-   prettier formatting ([a5947cb](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a5947cb7d98f73033ec6f5983ad31f538ddd8822))
-   testing ci build ([940882d](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/940882d706ad3861a8e33727f40d17a0abc168f7))
-   update yarn lock ([dc0e5fd](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/dc0e5fd238e561b45cd7eda817469dc49f350a39))
-   **web:** prettier formatting ([51f67b6](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/51f67b6823bc9fcb2c46927f0b48430e4083f2ac))

## 1.2.0 (2023-03-14)

### Features

-   Added uploadAssetWorkflow lambda function ([810bab7](https://github.com/awslabs/visual-asset-management-system/commit/810bab79e201f390bd990e195bee9ef69126d029))
-   Asset metadata feature ([7818b67](https://github.com/awslabs/visual-asset-management-system/commit/7818b67eda1e0a97f39baf13a137a92838480040))
-   updates to UploadAssetWorkflow stepFunction ([10d6955](https://github.com/awslabs/visual-asset-management-system/commit/10d6955934106c956f7a36d35b29d57b74a46103))
-   uploadAssetWorkflow stepfunction orchestration ([a4cfb25](https://github.com/awslabs/visual-asset-management-system/commit/a4cfb2579c71de366d34dd0405e308af898f55d4))
-   **web:** awsui css replaced with cloudscape css ([c67b06f](https://github.com/awslabs/visual-asset-management-system/commit/c67b06fde30cde0789f8a1788296f192d45e2b8c))
-   **web:** call uploadAssetWorkflow ([1a58383](https://github.com/awslabs/visual-asset-management-system/commit/1a58383aa86c897eaee5b6d763cdfe28570f893e))
-   **web:** metadata editing on the asset screen ([2dbdc8c](https://github.com/awslabs/visual-asset-management-system/commit/2dbdc8cf5f3c172e720d0db6a438623c41f389b9))
-   **web:** wizard ux for upload ([ff1b92e](https://github.com/awslabs/visual-asset-management-system/commit/ff1b92efb5aec551b94107a5bf53d5241773bc0f))

### Bug Fixes

-   added common aws security rules for WAF ([23155e9](https://github.com/awslabs/visual-asset-management-system/commit/23155e933f56c58204d7722548200548ce7b161f))
-   **backend:** return 404 when no metadata records exist ([199e422](https://github.com/awslabs/visual-asset-management-system/commit/199e4226bb3d9a3100dfe2eb87b1800667c96fa0))
-   **backend:** tests missing assetName ([5deca7c](https://github.com/awslabs/visual-asset-management-system/commit/5deca7c4d352cefa453a68842938cca58c71583c))
-   **backend:** tests missing assetName ([900d85e](https://github.com/awslabs/visual-asset-management-system/commit/900d85e0b9d76727b193458e5d85d63ea4b36886))
-   change all buckets to S3_MANAGED encryption ([97f0ac4](https://github.com/awslabs/visual-asset-management-system/commit/97f0ac45f403aadfad95ffa08ce00186fe0bbfd5))
-   change log s3 bucket encryption type to S3_MANAGED ([28f1bb9](https://github.com/awslabs/visual-asset-management-system/commit/28f1bb9e44f1b17b8ef8af792a266c351ff0316e))
-   display generated assets and assetName ([fda1767](https://github.com/awslabs/visual-asset-management-system/commit/fda176746f8a3d81679657484e944dc8e7440e2b))
-   downgrading default notebook platform ([8477e0d](https://github.com/awslabs/visual-asset-management-system/commit/8477e0d4d7bbe8b45c0520202b028606a49201e1))
-   **examples:** Example lambda pipeline defect repaired ([89c4f71](https://github.com/awslabs/visual-asset-management-system/commit/89c4f71450e1ad2a594a22c7999aa4ae2d1fce92))
-   fixing loader-utils security vulnerability ([2f2d02f](https://github.com/awslabs/visual-asset-management-system/commit/2f2d02f9639e8125963a0b713dc13355bc9eb590))
-   s3 copy_object calls include owner acct ids ([#32](https://github.com/awslabs/visual-asset-management-system/issues/32)) ([71f55d8](https://github.com/awslabs/visual-asset-management-system/commit/71f55d8a7a00d94eb162df36d019553b979ed7f6))
-   set arch to linux/amd64 for apple m1/m2 users ([d70d1b8](https://github.com/awslabs/visual-asset-management-system/commit/d70d1b85f3522965384cf0acd9cb300cf0667405))
-   staging bucket env variable name ([0d228c6](https://github.com/awslabs/visual-asset-management-system/commit/0d228c62900f045988adda855f638cd1bfb3301a))
-   statemachine execution fix ([75887dc](https://github.com/awslabs/visual-asset-management-system/commit/75887dc585da67233832d24e7cc1e892648b80e9))
-   updated the ssm-parameter-reader custom resource's lamdba runtime to nodejs18.x for cdk-nag: AwsSolutions-L1 ([8d3d90b](https://github.com/awslabs/visual-asset-management-system/commit/8d3d90ba57e5e0b6492d47e5a4eecbf61d9b23a5))
-   updating certifi version for critical vulnerability ([ad573b6](https://github.com/awslabs/visual-asset-management-system/commit/ad573b6d9365491635f0a4004913e87e6faa8c8c))
-   updating ci.yml ([24c541f](https://github.com/awslabs/visual-asset-management-system/commit/24c541ff8b54ca012ba3a6a2dd22a51f98f52bdf))
-   use provided preview image when the generated image fails to load ([3404dd0](https://github.com/awslabs/visual-asset-management-system/commit/3404dd05839ff56f32c94d6bb0362090935cd958))
-   using cdk 2.62.1 with croRegionReferences set to true to resolve cfn-nag ([94b4874](https://github.com/awslabs/visual-asset-management-system/commit/94b4874443e00c0d403fc4106b876c9e571239ca))
-   **web:** hamburger menu overlapping other elements ([e6cb8f4](https://github.com/awslabs/visual-asset-management-system/commit/e6cb8f491258e6283808beae4a0e15ff180a867e))
-   **web:** prevent word wrapping in the visualizer ([0e966e8](https://github.com/awslabs/visual-asset-management-system/commit/0e966e87841ae6e72ff064ec9819c325e4f45744))
-   **web:** update create asset buttons ([87bba93](https://github.com/awslabs/visual-asset-management-system/commit/87bba93d60c77596084598e6df6742171da21c52))

### Chores

-   adding fbx file formats for pipelines ([#35](https://github.com/awslabs/visual-asset-management-system/issues/35)) ([e4aad1f](https://github.com/awslabs/visual-asset-management-system/commit/e4aad1f27fd908f96201f36c73559bda81b3a7f8))
-   adding suppressions on notebook for ash ([9a8b96e](https://github.com/awslabs/visual-asset-management-system/commit/9a8b96e73029f92641d5aabd006a019301e63017))
-   cleaned up some code in infra-stack.ts ([2aa53e2](https://github.com/awslabs/visual-asset-management-system/commit/2aa53e2bc867c72b64069e52bb70e5dc09d15537))
-   **deps:** bump axios from 0.21.1 to 0.26.0 in /web ([1635f86](https://github.com/awslabs/visual-asset-management-system/commit/1635f8619b4cd814627b013847c099e4c373982e))
-   **deps:** bump certifi from 2022.9.24 to 2022.12.7 in /backend ([c0d8b3e](https://github.com/awslabs/visual-asset-management-system/commit/c0d8b3e4db34c038b663e97cb6f6b07004f46654))
-   **deps:** bump werkzeug from 2.2.2 to 2.2.3 in /backend ([#34](https://github.com/awslabs/visual-asset-management-system/issues/34)) ([74d547f](https://github.com/awslabs/visual-asset-management-system/commit/74d547fd5839c604312b107fcb03bdead32ad3a0))
-   fixes after running automated security helper ([ee48599](https://github.com/awslabs/visual-asset-management-system/commit/ee485999edc378eb7ddeb0192b8a83a14ed9dbcf))
-   prettier configuration ([1cef984](https://github.com/awslabs/visual-asset-management-system/commit/1cef984630bf325b9477daa3358e85dc07b5b286))
-   **release:** 1.0.0 ([ae61d15](https://github.com/awslabs/visual-asset-management-system/commit/ae61d152ba9ea84dba58d12a682f66db895d0b08))
-   **release:** 1.0.1 ([#21](https://github.com/awslabs/visual-asset-management-system/issues/21)) ([ec85772](https://github.com/awslabs/visual-asset-management-system/commit/ec85772f9dc7e1a13538ef0bd070d1be1bfa18ca))
-   remove unused resources ([#31](https://github.com/awslabs/visual-asset-management-system/issues/31)) ([0138bf1](https://github.com/awslabs/visual-asset-management-system/commit/0138bf104d3b5a4dd6c35c5983c55ee2596bb561))
-   removing unused files ([4d86f9b](https://github.com/awslabs/visual-asset-management-system/commit/4d86f9bea713625f71c8d662c6fef3c665394dd9))
-   Repair copyright headers ([#30](https://github.com/awslabs/visual-asset-management-system/issues/30)) ([dff7d76](https://github.com/awslabs/visual-asset-management-system/commit/dff7d768a4faa28829e215c559dde2c59285f018))
-   update broken links on DeveloperGuide ([0cccd0e](https://github.com/awslabs/visual-asset-management-system/commit/0cccd0ec1ceb3efc88918dfe95acac58afaefdbb))
-   update to list_objects_v2 ([#33](https://github.com/awslabs/visual-asset-management-system/issues/33)) ([a62a788](https://github.com/awslabs/visual-asset-management-system/commit/a62a7883ea97d9be85cbf4cf0c934651dcbe2b26))
-   **web:** copyright headers ([16b4f84](https://github.com/awslabs/visual-asset-management-system/commit/16b4f844f86a7c7d72b345f3d0647b5729f77ea2))
-   **web:** update to cloudscape from awsui ([450bffe](https://github.com/awslabs/visual-asset-management-system/commit/450bffe543464f0f01faa29debf0b28ed85e5c73))

### 1.0.1 (2023-02-10)

### Bug Fixes

-   change all buckets to S3_MANAGED encryption ([97f0ac4](https://github.com/awslabs/visual-asset-management-system/commit/97f0ac45f403aadfad95ffa08ce00186fe0bbfd5))
-   change log s3 bucket encryption type to S3_MANAGED ([28f1bb9](https://github.com/awslabs/visual-asset-management-system/commit/28f1bb9e44f1b17b8ef8af792a266c351ff0316e))
-   set arch to linux/amd64 for apple m1/m2 users ([d70d1b8](https://github.com/awslabs/visual-asset-management-system/commit/d70d1b85f3522965384cf0acd9cb300cf0667405))

### Chores

-   **release:** 1.0.0 ([ae61d15](https://github.com/awslabs/visual-asset-management-system/commit/ae61d152ba9ea84dba58d12a682f66db895d0b08))

## 1.0.0 (2022-11-09)
