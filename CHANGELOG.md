# Changelog

All notable changes to this project will be documented in this file. See [standard-version](https://github.com/conventional-changelog/standard-version) for commit guidelines.

## [2.6.0] (2026-08-30)

### Major Change Summary:

### ⚠ BREAKING CHANGES

-   OpenSearch index names rolled forward to `vams-assets-v3` and `vams-files-v3`. The schema-deploy custom resource creates the empty v3 indexes; the previous v2 indexes are abandoned and left in place until you delete them manually. A reindex is required to populate v3 for all OpenSearch deployments.
-   `OPENSEARCH_VERSION` switched from `OPENSEARCH_2_7` to `OPENSEARCH_3_5`. Provisioned OpenSearch domains will perform a major-version engine upgrade. Serverless collections are unaffected.
-   OpenSearch Serverless now deploys the collection into a collection group with configurable OCU capacity and adds `nextGen` and `allowPublic` options. This requires existing OpenSearch serverless deployments to be removed, re-deployed, and re-indexed. See the [v2.5 to v2.6 migration guide](https://awslabs.github.io/visual-asset-management-system/deployment/update-the-solution#v25-to-v26).
-   A VPC is no longer auto-enabled. If a VPC-requiring feature (ALB, OpenSearch Provisioned, or any container-based pipeline) is enabled while `app.useGlobalVpc.enabled` is `false`, configuration validation now fails with an explicit error listing the offending features instead of silently turning the VPC on. **Existing config files may need updating:** if you hit this error on upgrade, set `app.useGlobalVpc.enabled` to `true` (the value the deployment was implicitly using before) or disable the listed features. See the [v2.5 to v2.6 migration guide](https://awslabs.github.io/visual-asset-management-system/deployment/update-the-solution#v25-to-v26).
-   Provisioned OpenSearch `availabilityZoneCount` now defaults to `2`, and the VPC is now built with exactly that many Availability Zones. Previously the VPC builder always provisioned **3** Availability Zones for provisioned OpenSearch even though the OpenSearch domain only ever used 2 of them, so the third AZ's subnet was created but unused. VAMS now deploys 2 AZs by default (or 3 only when `availabilityZoneCount` is set to `3`) and uses them consistently. On upgrade this is a VPC downgrade: the previously-unused third Availability Zone's subnet is deleted. Because that subnet can still hold elastic network interfaces (the shared interface VPC endpoints, and VPC-attached Lambda ENIs when `useForAllLambdas` is set), AWS CloudFormation may fail to delete it. See the [v2.5 to v2.6 migration guide](https://awslabs.github.io/visual-asset-management-system/deployment/update-the-solution#v25-to-v26) and the [networking troubleshooting procedure](https://awslabs.github.io/visual-asset-management-system/troubleshooting/common-issues).
-   API Gateway HTTP API → REST API migration changes the API endpoint. The backend API is now an API Gateway REST API (v1) served under a stage path (default `/api`) instead of the previous HTTP API (v2). The API Gateway identifier and invoke URL change on deployment. Any client registered directly against the old API Gateway endpoint URL must be re-setup against the new endpoint — this includes the VAMS CLI (re-run `vamscli setup`) and any external integrations or scripts that stored the API base URL. Clients that reach the API through the CloudFront or ALB front (the web application, and CLIs configured with the front's `/api` URL) continue to work without change. See the v2.6.0 entry in [Update the solution](deployment/update-the-solution.md).
-   The workflow, pipeline, and execution overhaul is a breaking change for customer built-in pipelines or externally registered pipelines. A pipeline written against v2.5 does not run unchanged on v2.6: inputs arrive through a resolved manifest rather than on the invocation payload, an asynchronous pipeline must return a Step Functions task token for the workflow to advance, and a pipeline must register its sub-processes and log locations for abort and log retrieval to reach them. Registration also moves — a definition is declared in a file-based `vamsSchema` bundle imported through the schema importer instead of created ad hoc. The data migration reshapes stored **definitions** but cannot change a pipeline's **code**, so every externally maintained pipeline needs porting. Deployments running only VAMS built-in pipelines need nothing beyond the deployment steps. See [Migrating custom pipelines from v2.5 to v2.6](https://awslabs.github.io/visual-asset-management-system/pipelines/migrating-pipelines-v25-to-v26) for the porting order and checklist.
-   **The authorizer claims context shape changed, which can break customized MFA, claims, and login-profile logic.** Deployments that have edited the customization hooks under `backend/backend/customConfigCommon/` must review them against the new shape before upgrading. Stock (unedited) deployments need no action — VAMS ships working defaults for all three hooks. Three related changes drive this:
    -   **The authorizer context is now a flat string map.** Under the HTTP API (v2), a Lambda authorizer's claims arrived nested at `requestContext.authorizer.jwt.claims` or `requestContext.authorizer.lambda`. The REST API (v1) REQUEST authorizer delivers them as a **flat map of string values** directly under `requestContext.authorizer` (alongside a `principalId` key). Custom logic that branches on `'jwt' in ...` / `'lambda' in ...` and falls through to an empty dict now silently reads **no claims** rather than raising — the failure is a quiet behavior change, not an error. Read claims through `request_to_claims(event)` (which handles all three shapes and normalizes the event) instead of indexing `requestContext.authorizer` directly. Note that every context value is a **string**, so JSON-valued claims such as `vams:tokens` and `vams:roles` must be `json.loads`-ed, and `vams:mfaEnabled` is the string `"true"`/`"false"` rather than a boolean.
        -   The shipped `customAuthProfileLoginWriteOverride` default in `customAuthLoginProfile.py` still carries the old nested-shape branches, so under the REST API its email-from-claims override is inert. It is harmless as shipped (the handler already persists the correct `userId`, and a stored profile email set at creation is unaffected), but a deployment that relies on populating profile fields from token claims in that hook must update the extraction to the flat shape.
    -   **`customMFATokenScopeCheckOverride` takes a new argument and no longer extracts claims itself.** The signature changed from `(user, lambdaRequest)` to `(user, authorizerJwtClaims, lambdaRequest)` — the verified claims are now passed in directly, so the hook no longer digs them out of the event. It is also called from the **authorizer** rather than from each handler, and the Cognito default now resolves MFA with `admin_get_user` (`UserPoolId` + `Username`, requiring the new `USER_POOL_ID` environment variable) instead of `get_user` with the caller's access token. A customized hook written against the two-argument signature will fail to be called correctly and its result is caught and defaulted to `false`, silently disabling MFA-gated roles. The external OAuth IDP branch remains a customization slot that returns `false` until implemented.
    -   **MFA state is resolved once at authorization time.** The result is passed to handlers as the `vams:mfaEnabled` authorizer context value and is consumed by `request_to_claims` before `customAuthClaimsCheckOverride` runs, so that hook should read `claims_and_roles["mfaEnabled"]` rather than re-deriving MFA. Handler Lambda functions no longer call an identity provider themselves. Because the authorizer must reach Amazon Cognito for this check, it is disabled when Lambda functions run inside the VPC (`app.useGlobalVpc.useForAllLambdas`), in which case `mfaRequired` on a role has no effect.

**Recommended Upgrade Path:** Run the upgrade script to redindex opensearch data if using OpenSearch serverless or provisioned: `infra\deploymentDataMigration\v2.5_to_v2.6\upgrade`

### Features

-   **Workflows & Pipelines** Workflow, pipeline, and execution system overhaul — a ground-up revamp of how VAMS defines pipelines and workflows, resolves their run-time configuration, executes them, and records execution history. Backend, CDK, use-case pipelines, the VAMS CLI, and the web UI (new Pipelines, Workflows, and Executions pages plus the in-place execute wizard) are all delivered in this release.
    -   **New pipeline & workflow data model.** Pipelines and workflows are now database-scoped definitions (`PipelineStorageTableV2`, `WorkflowStorageTableV2`) with a typed `executionConfig` (per execution type — Lambda, SQS, EventBridge, or Deadline Cloud — replacing the loose `userProvidedResource` JSON string) and an admin-only `systemConfig` (input-file arity, asset scope, metadata inputs, input-file filters, template requirements). A workflow references its pipelines by composite `pipelineDatabaseId:pipelineId` so references resolve unambiguously across databases.
    -   **Pipeline templates + tag schemas.** A pipeline can define reusable configuration templates (`PipelineTemplatesStorageTable`) — a named, versioned configuration body (JSON/YAML/OpenJD/XML/raw) with an optional per-template tag schema (`PipelineTemplateTagSchemaStorageTable`) that validates and fills the tags substituted into the body at launch. Templates can override the pipeline's input-file arity, metadata inputs, asset scope, and input-file filters, so one pipeline serves multiple conversion matrices (for example, a single 3D conversion pipeline with one template per output format). Template bodies and web-form definitions store inline and transparently offload to the artefacts bucket above a size threshold; clients never touch S3.
    -   **Asset-less multi-file execution.** Workflow execution takes an input-file object array (files may span multiple assets), an output-target asset, and per-pipeline execution parameters (`templateId` + tag values, or a custom template override). Run-time I/O (the metadata envelope, per-pipeline configuration, resolved manifest, and output staging) lands in the default asset bucket; input files are read from their own asset buckets and final outputs are written to the output asset's bucket.
    -   **Grouped-by-asset metadata envelope.** The shared input-metadata file is grouped by asset (`{schemaVersion: 2, assets: [{databaseId, assetId, assetData, files: [{fileKey, metadata, attributes}]}]}`); every use-case pipeline that reads metadata resolves records for its specific `(databaseId, assetId, fileKey)`.
    -   **New execution operations.** Global (asset-less) execution list with rich filters and pagination; execution details and logs; abort (single execution or an entire execution group via `executionGroupId`); re-run (reconstruct and relaunch from the stored execution records); and permanent delete (remove the execution's DynamoDB rows only — admin-only).
    -   **Workflow triggers.** An extensible typed trigger structure (currently `fileUpload`) fires a workflow when a matching file is uploaded, matched by the workflow's input-file filters and dispatched through the VAMS orchestration Amazon EventBridge bus.
    -   **Standardized built-in pipeline registration.** Built-in pipelines register into the new data model at deploy time from a file-based `vamsSchema` bundle (a `pipeline.json`, an optional `workflow.json`, and optional templates) uploaded to the artefacts bucket by the CDK and imported through SYSTEM_USER cross-calls, so registration is idempotent (re-deploy overwrites and unarchives) and carries no hard-coded ARNs. External solutions can self-register through the same importer. The former per-output-format built-ins are consolidated into single pipelines with per-format templates (for example, the 3D basic conversion, RapidPipeline, VNTANA ModelOps, and Gaussian Splat Toolbox built-ins).
    -   **API** The pipeline, workflow, and execution APIs are the single overhauled shape (database-scoped CRUD, asset-less execute, execution operations, and triggers); the API is not backward-compatible with the pre-overhaul pipeline/workflow/execution endpoints. A Lambda-type pipeline created through the API without an existing function reference has one provisioned automatically (seeded from the sample pipeline package). File-upload auto-execution is delivered solely through the VAMS orchestration Amazon EventBridge bus.
    -   **CDK** New execution-overhaul data-model tables (pipeline/workflow/template/tag-schema/triggers and the workflow-keyed execution tables), their SSM resource-name parameters, the `vamsSchema` import custom resource, and the `VamsSchemaRegistration` construct used by every built-in pipeline. All pipeline, workflow, and execution Lambda functions and API routes are built in the secondary API nested stack (`apiBuilder2`). The workflow orchestration Amazon EventBridge (`events`) interface VPC endpoint is created whenever VPC endpoints are enabled.
    -   **CDK** The v2.5→v2.6 data migration reshapes legacy workflow execution history into the workflow-keyed model and migrates user-database (non-`GLOBAL`) pipeline and workflow **definitions** into the new tables (`--steps pipelineWorkflowDefinitions`). `GLOBAL` built-ins are skipped — they are re-created by the `vamsSchema` importer — and references to consolidated built-in ids are remapped, so the migration never clobbers a freshly registered built-in.
    -   **CLI** New `pipeline`, `workflow` (refactored), and `execution` command groups covering the full overhauled API surface: `pipeline` CRUD + `pipeline template` CRUD + `pipeline tag-schema` get/set; `workflow` CRUD + `workflow trigger` list/get/set/delete + asset-less multi-file `workflow execute` + per-asset `workflow list-executions`; and `execution` list (global, filterable)/details/logs/abort (single or `--group-id`)/rerun/permanent-delete. The pre-overhaul asset-scoped `workflow execute` and `autoTriggerOnFileExtensionsUpload` surface is removed.
    -   **Web** Pipelines, Workflows, and Executions UI overhaul — new top-level Pipelines, Workflows, and Executions pages plus an in-place execute wizard, built on React 18 with a modern module stack (TanStack Query/Table, Tailwind + Radix, @rjsf/core, Monaco, reactflow v11). Category-grouped, server-paginated lists (Load-more; search auto-loads the full set), Tier-1 permission graying, a live-polling executions board with status/trigger/group filters and a quick-view drawer, a full execution-detail page, and a DAG-preview workflow builder. Workflow cards show a per-workflow execution count from the list response. The React 18 upgrade preserves all existing Cloudscape pages and viewer plugins.
-   **Web** Preview files directly from file search results — each viewable file search row now carries an eye icon that opens the file in a popup visualizer without navigating to its asset. A multi-select viewer mode accumulates several files and opens them together in a single viewer, per-file asset and database context flows through so each file loads from its own asset, and files route to the appropriate registered viewer by extension.
-   **Tags & Tag Types** Per-database tag namespacing. Tags and tag types can now be created as either **GLOBAL** (available in every database) or scoped to a specific database, so the same tag or tag-type name can exist independently in different databases (for example, a `Status` tag in a manufacturing database and a separate `Status` tag in a media database).
    -   Tag and tag-type names are unique **per database**, not globally — the same name may be reused across different databases. A name may not exist as both a GLOBAL entry and a database-specific entry, so every tag name an asset references resolves unambiguously within the asset's own database plus GLOBAL.
    -   All tags and tag types that existed before the upgrade are treated as GLOBAL. The `tagsNamespacing` data-migration step copies them into the new tables under the `GLOBAL` partition; no manual data changes are required and asset tag lists are left unchanged.
    -   The Casbin `tag` and `tagType` permission constraints gain a `databaseId` field, scoping tag and tag-type administration to GLOBAL or to specific databases.
    -   A database can no longer be created with the reserved `databaseId` value `GLOBAL`.
    -   **CDK** New composite-key Amazon DynamoDB tables `TagStorageTableV2` and `TagTypeStorageTableV2` (partition key `databaseId` — the literal `GLOBAL` for global entries — sort key `tagName`/`tagTypeName`), each with a name GSI (`tagNameIndex`/`tagTypeNameIndex`) for cross-database name lookups. The former single-key `TagStorageTable`/`TagTypeStorageTable` are retained (`RETAIN`) as legacy migration sources. Run the `tagsNamespacing` step of the v2.5→v2.6 data migration (`infra/deploymentDataMigration/v2.5_to_v2.6/`) to populate the new tables.
    -   **API** `GET /tags` and `GET /tag-types` accept optional `databaseId` and `scope` (`global`/`all`) query parameters, and the create/update/delete operations accept a `databaseId`; request and response shapes are otherwise unchanged.
-   **Web** Geospatial search and map view across both assets and files. New map selectors for metadata geospatial types and search filtering.
    -   The web search sidebar exposes a Geospatial filter panel, and the map view (including mini-map thumbnails) now works for both assets and files and renders polygon/multi-polygon shapes in addition to points.
    -   The asset and file OpenSearch indexes now declare a derived `geo_MD_location` field of type `geo_shape`.
    -   The asset and file indexers populate it from a `location` metadata key (GeoPoint, GeoJSON or `{latitude, longitude, altitude}` payload) or from individual `latitude`/`longitude`/`altitude` metadata fields.
    -   Search APIs (`POST /search` and `POST /search/simple`) and the `vamscli search assets|files|simple` commands accept a new `geoSearch` payload (point + radius, bounding box, or arbitrary GeoJSON) with `intersects`/`within`/`contains`/`disjoint` relations.
    -   Open search index names rolled to new version suffixes; run `infra/deploymentDataMigration/v2.5_to_v2.6/upgrade` to repopulate the new indexes from source data after deploying.
-   **Web** Minor adjustments made to asset and file search page to help further streamline component placement and use
-   New Physna Sync add-on (Phase 1) — Optional one-way synchronization of supported VAMS files, file metadata, file attributes, and asset metadata to a Physna tenant. Enable via `app.addons.usePhysnaSync` in `infra/config/config.json`. See the [Physna Integration documentation](documentation/docusaurus-site/docs/developer/physna-integration.md) for details.
-   **Web** Physna Viewer frontend plugin — New VAMS viewer plugin that embeds the Physna-hosted 3D/CAD viewer inside VAMS asset pages for files that have been synced to Physna. Backed by a new `GET /addon/physna/viewer` proxy endpoint that enforces VAMS two-tier authorization and keeps Physna credentials off the client. Enabled automatically whenever the Physna Sync add-on is deployed.
-   **Web** New IFC BIM File Viewer (ThatOpen) frontend plugin — New VAMS viewer plugin that renders IFC (Industry Foundation Classes) Building Information Models in the browser using the open-source That Open Engine (`web-ifc`, MIT / MPL-2.0). Supports `.ifc` and `.ifczip`, with a spatial model tree, element property inspection, hide/isolate, section planes, and measurements. Vendored as a self-contained `customInstalls/thatopenwebifc` UMD bundle (nothing added to the core web dependencies) and enabled by default. Uses the multithreaded `web-ifc-mt.wasm` build when cross-origin isolation is available (COI service worker) and falls back to single-thread otherwise; does not require `ALLOWUNSAFEEVAL`.
-   **Web** New SuperSplat Editor (PlayCanvas) frontend plugin — New VAMS viewer plugin that embeds the full open-source PlayCanvas SuperSplat Gaussian-splat editor (MIT) for viewing and editing 3D Gaussian Splats. Supports `.lcc` (XGRIDS multi-LOD, not previously viewable in VAMS), `.ply`, `.sog`, and `.splat`, and becomes the default viewer for splat formats. Unlike other viewers, this is the first iframe-embedded viewer. Editing/export tools operate in the browser only — edited content is not currently saved back to VAMS although can be supported in the future.
    -   The asset S3 bucket CORS now exposes range headers (`Accept-Ranges`, `Content-Range`, `Content-Length`, `Content-Encoding`) to support future progressive splat streaming.
-   **Web** Continue to add additional retry/skip steps to the various web file upload stages if certain network calls fail
-   **Web** BabylonJS and PlayCanvas Gaussian Splat viewers now ship a floating 3-tab control panel that has similar functionality of the ThreeJS UI controls panel, but appropriate for gaussian splats.
-   **Web** `View Asset` page now tracks the selected file in the manager as `?filePath=` query param and search now uses this path instead of sending state. This helps with opening assets in new tabs, direct navigations, or view asset page refreshes.
-   **Web** Asset and file search now sort filter drop-downs alphabetically
-   New Coordinate Transform pipeline — Reprojects point cloud files between coordinate reference systems (CRS). Supports E57, LAS, LAZ, and PLY inputs and outputs LAZ, LAS, E57, or PLY, running as an AWS Batch Fargate container with PDAL- and pyproj-based transformation. Source/target CRS accept EPSG codes, PROJ strings, WKT, or custom named local grids, and the position-dependent local scale factor of well-defined projections (e.g., OSGB36 `EPSG:27700`) is handled automatically. Parameters can be set as pipeline defaults or overridden per asset via VAMS metadata keys (`sourceCrs`, `targetCrs`, `outputFormats`, scale factors, and more). Enable via `app.pipelines.useConversionCoordinateTransform` in `infra/config/config.json`; the container image builds via AWS CodeBuild + Amazon ECR. See the [Coordinate Transform pipeline documentation](documentation/docusaurus-site/docs/pipelines/coordinate-transform.md) for details.
-   NVIDIA Isaac Lab Training pipeline now supports a configuration building its container image via AWS CodeBuild + ECR instead of a local Docker build, matching the existing NVIDIA Cosmos and Gr00t pipelines.
-   New Physical AI NVIDIA Cosmos 3 (omni) Inference Pipeline — GPU-accelerated generation built on NVIDIA's omnimodal Cosmos 3 world foundation models (Mixture-of-Transformers), served from the `NVIDIA/cosmos-framework` repository. A single shared container image serves all model variants; the variant is selected by checkpoint and the task by `model_mode` at runtime. Supports **Cosmos3-Nano (16B)** on a single-GPU tier (`g6e.4xlarge`, L40S) and three **Cosmos3-Super (64B)** variants — Super (Text2Video), Super-Text2Image, and Super-Image2Video — on a multi-GPU tier (`p5.48xlarge`/`p5e.48xlarge` 8×H100/H200, or `p4de.24xlarge` 8×A100-80GB) using multi-GPU FSDP-sharded inference. Metadata-driven prompts and generation parameters via `COSMOS3_PROMPT`, `COSMOS3_NEGATIVE_PROMPT`, `COSMOS3_SEED`, `COSMOS3_GUIDANCE`, and `COSMOS3_NUM_FRAMES` (asset-scope for text modes, file-scope for image-to-video). Reuses the shared Cosmos EFS model cache + S3 backup (lazy-loaded from HuggingFace on first run) and a dedicated HuggingFace token. Configurable per-variant GPU instance types; each variant can be enabled independently. Enable via `app.pipelines.useNvidiaCosmos3` in `infra/config/config.json`; the container image builds via AWS CodeBuild + Amazon ECR. See the [NVIDIA Cosmos 3 pipeline documentation](documentation/docusaurus-site/docs/pipelines/nvidia-cosmos-3.md) for details.
-   The Application Load Balancer (ALB) web access logs bucket is now removed (with its contents) on stack teardown instead of being retained. Because this bucket carries a fixed name derived from the configured domain host under ALB deployments, a retained bucket previously orphaned on `cdk destroy` and blocked a subsequent redeploy with the same configuration. It now matches the web app bucket's `autoDeleteObjects` + `DESTROY` behavior.
-   **CDK** DynamoDB table names, non-asset S3 bucket names, and audit CloudWatch log group names are now resolved from AWS Systems Manager Parameter Store instead of being injected as Lambda environment variables. A new `ResourceNamesBuilder` nested stack publishes SSM String parameters under the deployment prefix (29 DynamoDB tables, 2 S3 buckets, 9 audit log groups). Non-pipeline backend Lambda functions receive the SSM parameter prefix (`VAMS_RESOURCE_PARAM_PREFIX`) and ssm:GetParameter/GetParameters/GetParametersByPath IAM grants; handlers call `get_table_name(ResourceKeys.*)` / `get_bucket_name(ResourceKeys.*)` / `get_log_group_name(ResourceKeys.*)` from `backend.common.resourceNames` at module level, which caches the full parameter set for 60 minutes and falls back to legacy environment variables for testing and local utilities. The resource-name constants are defined in `infra/common/resourceParamKeys.ts` (TypeScript) and mirrored in `backend/backend/common/resourceNames.py` (Python `ResourceKeys` class). Deprecated tables retained for data migration are also published (under `dynamoTables/legacy/`), along with the reindexer Lambda function name (`lambdaFunctions/crOsReindexer`), for consumption by data-migration tooling. The core stack exposes the base prefix as the CloudFormation output `ResourceNamesSSMParamPrefixOutput`; the v2.5-to-v2.6 data migration script resolves the reindexer function name from SSM via the new shared utility `infra/deploymentDataMigration/tools/ssm_resource_lookup.py`, so operators only fill in the base prefix and region (explicit per-resource config values remain supported as optional overrides). Pipeline Lambda functions (`backendPipelines/`) continue to use legacy environment variables and are excluded from SSM resolution. VPC deployments with `useForAllLambdas` enabled and `addVpcEndpoints` disabled now require an operator-managed SSM Systems Manager interface VPC endpoint — all Lambda functions resolve resource names at cold start and will fail without SSM API access.
-   **CDK** Advanced IAM role customization for restricted environments — Two optional, opt-in mechanisms let deployers who cannot create IAM roles map pre-created roles instead. A new `app.iamRoleConfig` config section toggles `useCustomBootstrapRoles` (replace the CDK bootstrap roles via a custom stack synthesizer, or use `CliCredentialsStackSynthesizer` for no bootstrap roles at all) and `useCustomVamsStackRoles`. The actual mappings (role ARNs, construct-path-to-role-name maps) live in a separate `infra/config/policy/iamRoleConfig.json` file, keeping the verbose values out of the main config. Both options default to disabled, preserving the existing behavior where VAMS manages all IAM roles. See the [configuration reference](documentation/docusaurus-site/docs/deployment/configuration-reference.md) for the full workflow.
-   Asset/File search APIs now include additional fields when passing general search filter queries (asset id, database id, s3 bucket id/name, s3 bucket prefix)
-   Outbound external-system sync tracking — VAMS now records every outbound synchronization to an external system (Physna, Garnet Framework) in a new `SyncTrackingOutboundStorageTable` Amazon DynamoDB table. Foundation for future sync-status APIs and inbound-sync tracking.
-   Asset lifecycle history — VAMS now keeps a permanent per-asset audit history of lifecycle operations (create, edit, archive, unarchive, permanent delete) in a new `AssetHistoryStorageTable` Amazon DynamoDB table. Each record captures the operation, the acting user, the change origin (API vs. S3 bucket-sync ingestion, recorded as `create`/`createDirect`, `unarchive`/`unarchiveDirect`, etc.), and an open-schema snapshot of the asset fields after the operation (name, description, distributable flag, tags, bucket, location key, archive/unarchive reasons). History records survive asset permanent deletion; recreating an asset with the same asset ID continues the same history trail.
    -   New paged API endpoint `GET /database/{databaseId}/assets/{assetId}/assetHistory` returns records newest first with `pageSize`/`startingToken` pagination and two-tier authorization against the asset (history of a permanently deleted, non-recreated asset ID returns 404).
    -   **CLI** New `vamscli assets history` command with the standard pagination options (`--page-size`, `--starting-token`, `--auto-paginate`, `--max-items`) and `--json-output`.
    -   **Web** New Asset History modal on the Asset View File Manager details panel — a `(History)` link on the asset root node's Type row opens a server-side paged history table with per-record snapshot details.
    -   **CDK** The v2.5-to-v2.6 data migration script gains an asset history backfill phase that infers `create` records from each asset's v0 version record and `archive`/`unarchive` records from the asset's archive fields; backfilled records are flagged `migratedRecord: true` and are idempotent on re-run.
-   Asset file change history — VAMS now tracks per-version change provenance (how a file version was created and by whom) for uploads, workflow executions, copies, moves, renames, archives, and direct S3 changes. Provenance is stamped as `vams-change*` S3 object metadata when a version is created and recorded into a new `assetFileVersionHistoryStorageTable` Amazon DynamoDB table on ingest. File list/detail responses surface the current version's change source and modifying user, and file version history surfaces the full per-version provenance (including workflow and source-location details). Exposed through the file GET APIs, the `vamscli file` commands, and the web file manager (details panel and version history view). Versions created before this release report blank provenance.
-   File listing responses now include each file's S3 `etag`. The `listFiles` API returns it for every file in basic and full mode, matching the `fileInfo` API which already returns it for the requested file. Surfaced by the `vamscli file list` and `vamscli file info` commands.
-   API route listing endpoints — New `GET /auth/routes/api` returns the full list of VAMS API routes (paths, methods, categories) from a new master route definition file, and `GET /auth/routes/api/allowed` returns the routes and methods the requesting user is authorized to call (evaluated through Casbin). Exposed through new `vamscli auth routes list` and `vamscli auth routes allowed` CLI commands.
    -   **Web** The web constraints editor now fetches the full route list and offers an autosuggest of valid API routes when authoring `api` constraints (`route__path` values). Backend handlers now dispatch API requests against the master route definitions (`backend/backend/common/apiRoutes.py`) instead of hard-coded path checks.
-   Archiving, unarchiving, and permanently deleting assets with many files is significantly faster.
-   Bulk presigned-URL generation for file downloads. The download API (`POST /database/{databaseId}/assets/{assetId}/download`) now accepts a `keys` array (up to 1,500 file keys of the same asset per request) alongside the existing single `key` field, returning a per-file entry array (`files`: key, downloadUrl, versionId, success, error). File paths that do not exist or are not downloadable are skipped and reported per file with a warning in the response message; the request errors only when no URL can be generated. Single-file requests are unchanged and fully backwards compatible.
    -   **CLI** Multi-file downloads (`assets download` folder/whole-asset/`--shareable-links-only` flows and `sync file pull`) generate presigned URLs through the bulk API, removing most of the per-file API round-trip time on large assets. The CLI pages locally above the 1,500-key limit. `assets download` with `--asset-version-id`/`--asset-version-alias` now lists and downloads the files as they existed in that asset version snapshot, and a new `--version-id` option downloads a specific S3 version of a single `--file-key`.
    -   **Web** The shareable-URL dialog and the multi-file/folder download page generate presigned URLs through the bulk API with the same local paging, speeding up multi-select downloads and share-link generation for large selections.
-   **CLI** New `sync` command group with `vamscli sync file push` and `vamscli sync file pull` — S3-sync-style directory synchronization between a local directory and an asset (or an asset subdirectory). Compares files by size and modified timestamp (or size alone) and transfers only the differences, reusing the existing multipart upload and parallel download machinery. Ability to control modified/delete files and set `.vamsignore` files, similar to `.gitignore`. Basic ability to see upload/download conflicts based on non-synced changed. See CLI documentation for more full functionality and parameter options.
    -   **CLI** Downloads (sync pull, `assets download`, `assets export`) now write each file to a temporary file, verify the received size, atomically move it into place, and stamp it with the remote modified timestamp — interrupted downloads no longer leave partial files, and preserved timestamps keep repeated syncs stable.
-   **CLI** Amazon Cognito password change support. `vamscli auth login` adds a `--new-password` option so a forced password change (for example, on a new account's first sign-in) can be completed non-interactively, including under `--json-output`; without it, interactive logins still prompt for the new password and JSON-output logins return a clear error instead of hanging. A new `vamscli auth change-password` command lets a Cognito user change their own password by supplying the current and new passwords (prompted in interactive mode, required with `--json-output`), and also satisfies a forced change. A new `vamscli auth forgot-password` command provides a self-service reset for a forgotten password using a verification code emailed by Cognito: run with `--username` to request a code, then re-run with `--code` and `--new-password` to confirm (interactive mode prompts for both after the code is sent; `--json-output` requests a code only, or confirms when both are supplied). All are Cognito-only and fully backwards compatible with existing login commands and parameters.
-   **CLI/Docs** CLI documentation consolidated into the official documentation site as the single source of truth. The CLI section (`documentation/docusaurus-site/docs/cli/`) now carries the full command reference brought to parity with the code, the authentication/installation/automation flows, and a new CLI-specific troubleshooting sub-section (`cli/troubleshooting/`). The legacy in-repo docs under `tools/VamsCLI/docs/` are deprecated (banner added, retained temporarily for validation), and `tools/VamsCLI/README.md` now keeps basic installation plus quick start and points to the live documentation site. Steering docs (`tools/VamsCLI/CLAUDE.md`, `.kiro/steering/CLI_DEVELOPMENT_WORKFLOW.md`, `documentation/CLAUDE.md`, `.kiro/steering/DOCUMENTATION_WORKFLOW.md`) updated to direct all CLI documentation changes to the official site.
-   Constraint permission objects listing — New `GET /auth/constraints/permissionObjects` returns the constraint object types (with the fields valid on each), the criteria operators, the permissions (HTTP actions), and the permission types from a backend master mapping (`backend/backend/common/constants.py`), making this metadata API-driven rather than defined in the web client. Exposed through a new `vamscli role constraint permission-objects` CLI command. The per-object-type field matrix is authoritative: a constraint criterion whose field is not valid for its object type is rejected at create/update and template import, and out-of-matrix or deprecated fields are ignored (never error) during Casbin policy compilation and object evaluation.
    -   **Web** The web constraints editor now loads object types, fields, operators, permissions, and permission types from this endpoint instead of local maps, and offers an autosuggest of the deployment's web routes when authoring `web` constraints (`route__path` values).
    -   **CDK** The auth constraints service and its routes are consolidated in the secondary API stack (`apiBuilder2-nestedStack.ts`) to keep the primary API stack under the CloudFormation per-stack resource limit.
-   Authorization constraints now combine AND and OR criteria within the same policy — when a constraint defines both `criteriaAnd` and `criteriaOr`, access requires all AND criteria to match and at least one OR criterion to match (previously the two groups were evaluated as alternatives). Combined constraints also generate fewer Casbin policy rules, improving authorization-check performance.
-   **Web** The user's allowed API routes are now fetched at login and cached with a 15-minute periodic renewal (cache key `allowedApiRoutes`); this list will drive enabling/disabling web functionality based on API access in a future release.
-   **Web** Feature switches (`/secure-config`) are now refetched on every login instead of being cached in browser storage indefinitely.
-   User-level (self-service) API keys — New `/auth/user/api-keys` routes let users manage their own API keys without administrative access: all operations are scoped server-side to keys owned by the requesting user, created keys are always tied to the authenticated caller, an expiration date is required at creation (max 365 days), and later edits cannot extend the expiration beyond 365 days from the key's original creation date (rotate by creating a new key after that).
    -   The existing admin routes (`/auth/api-keys`) are unchanged and fully backwards compatible.
    -   Exposed through new `vamscli api-key user list|create|update|delete` CLI commands.
    -   **Web** The web API Key Management page now supports two modes driven by the cached allowed-API-routes list: "All Keys (Admin)" and "My Keys" (self-service); users with access to both see a mode toggle.
    -   **Web** The page moved from the "Admin - Auth" navigation section to a new "User" section, which is hidden for users without access to the API Key Management web route.
    -   Default read-only role and the permission templates now grant self-service API key access and the API key management web route.
-   New VAMS EventBridge orchestration bus — A top-level custom Amazon EventBridge event bus created as a foundation for future event-driven VAMS features (email/subscription events, pipeline registration and success/error events, audit event logging). Note: EventBridge does not yet support KMS customer-managed-key encryption on event buses in GovCloud / EU Sovereign Cloud, so bus CMK encryption is kept off in those partitions for now (the bus uses EventBridge's default AWS-owned-key encryption at rest).
-   **CDK** VPC subnet availability-zone count is now stable across feature toggles. The VPC builder provisions every subnet type across a fixed baseline of 2 Availability Zones instead of varying the count (1, 2, or 3) by feature, which previously caused AWS CloudFormation subnet-deletion failures when features were disabled. Public and private (egress) subnets remain created only when an internet-facing pipeline or the public-subnet ALB requires them. See the [networking troubleshooting guide](documentation/docusaurus-site/docs/troubleshooting/common-issues.md) for recovering a stack that hit subnet-deletion errors.
-   **CDK** Several Amazon OpenSearch Service servless and provisioned domain upgrades - Breaking Changes
    -   Amazon OpenSearch Service provisioned domains now support a configurable Availability Zone count via `app.openSearch.useProvisioned.availabilityZoneCount` (`2` or `3`, default `2`), with one data node per zone. At `2` the domain runs Multi-AZ without Standby (single index copy). At `3` the domain runs Multi-AZ with Standby, and the asset/file indexes are created with two replicas (three copies) to meet Standby's multiple-of-three requirement. The default of `2` matches the historical domain layout, so existing provisioned deployments are unchanged. A 3-AZ Standby domain must be created fresh — switching an existing 2-AZ (single-copy) domain to `3` in place is rejected by the service, so disable and re-enable OpenSearch and reindex to migrate. Keep `2` for Regions/partitions that expose only two Availability Zones.
    -   Amazon OpenSearch Service provisioned domains now support a configurable primary shard count via `app.openSearch.useProvisioned.numberOfShards` (default `1`). Large indexes — as a guideline, those expected to exceed roughly 60 GB (about 3 million asset or file records) — should increase the shard count. Like the replica count, the shard count is fixed at index creation, so changing it requires re-creating the index (disable and re-enable OpenSearch, then reindex).
    -   Provisioned OpenSearch default node instance type changed from `r6g.large.search` to `r7g.large.search` for both data and master nodes (the newer Graviton generation). The defaults in `config.ts` and all config templates are updated; `dataNodeInstanceType` / `masterNodeInstanceType` remain configurable. Changing the instance type on an existing domain triggers a blue/green update.
    -   Provisioned OpenSearch engine version is now selected by partition. Most partitions use `OPENSEARCH_VERSION` (OpenSearch 3.x); the AWS European Sovereign Cloud (partition `aws-eusc`, Region `eusc-de-east-1`) uses the new `OPENSEARCH_VERSION_EUSOVEREIGN` (OpenSearch 2.x) because OpenSearch 3.x is not yet supported there. The selection is automatic based on the deployment partition and requires no configuration.
    -   The Amazon OpenSearch Provisioned Service service-linked role (`AWSServiceRoleForAmazonOpenSearchService`) is now created idempotently during deployment via a check-or-create custom resource. This resolves intermittent _"Before you can proceed, you must enable a service-linked role to give Amazon OpenSearch Service permissions to access your VPC"_ failures on provisioned deployments in accounts where AWS had not already auto-created the role. The role is created if missing and left unchanged if it already exists (the create call ignores the `InvalidInput`/already-exists response), and it is account-wide so it is not removed on stack teardown.
    -   Amazon OpenSearch Serverless upgraded to next-generation Serverless. The collection is now deployed into a collection group whose generation is controlled by `app.openSearch.useServerless.nextGen` (default `true` for commercial partitions, `false` for GovCloud/EU Sovereign Cloud). The next-generation (`NEXTGEN`) generation brings three benefits over the classic generation: scale-to-zero — indexing and search compute can scale down to 0 OpenSearch Compute Units (OCU) when idle (set `minIndexingOcu`/`minSearchOcu` to `0`), so an idle deployment incurs near-zero OpenSearch compute cost; higher performance — faster autoscaling and resource provisioning that respond to workload spikes more quickly than the classic generation; and **better cost pricing** — the combination of scale-to-zero and configurable OCU ceilings (`maxIndexingOcu`/`maxSearchOcu`, default `16`; each OCU value must be `0`, `2`, `4`, `8`, `16`, or any multiple of `16`) lets a deployment pay for the capacity it actually uses rather than a fixed always-on minimum. Scale-to-zero trades an approximately 10–20 second cold start on the first request after about 10 minutes of inactivity for the cost savings; keep the minimum OCUs at `1` or greater for consistently low latency. New `app.openSearch.useServerless` options also include `allowPublic` (default `true`; set `false` to place the collection behind a VPC endpoint, recommended for production), `enableStandbyReplicas` (defaults to the value of `nextGen`; required to be `true` for `NEXTGEN`, optional for `CLASSIC`), and the four OCU bounds. A private collection (`allowPublic=false`) requires `app.useGlobalVpc.enabled` and, like provisioned OpenSearch, places only the OpenSearch-facing Lambda functions in the VPC (`app.useGlobalVpc.useForAllLambdas` is not required). A fully network-isolated deployment (`app.useGlobalVpc.enabled` + `app.useGlobalVpc.useForAllLambdas` both `true`) must use a private collection — configuration validation rejects a public collection in that topology.
    -   **Tooling** The OpenSearch reindex utility (`infra/deploymentDataMigration/tools/reindex_utility.py`) now supports a `--mode` flag with `lambda` (default, unchanged behavior — invokes the deployed reindexer Lambda) and `direct` (runs the backend reindexer handler locally with no execution-time limit). Direct mode targets large asset repositories where the Lambda would exceed its 15-minute maximum and leave records unindexed.
-   **CDK** New AWS European Sovereign Cloud deployment template (`infra/config/config.template.eusovereign.json`) targeting Region `eusc-de-east-1` (partition `aws-eusc`). It mirrors the GovCloud guardrails (set `app.govCloud.enabled = true`; VPC required, no Amazon CloudFront, no Amazon Location Service) and sets OpenSearch provisioned `availabilityZoneCount` to `2`. Configuration validation rejects an `availabilityZoneCount` greater than `2` for `eusc-de-east-1`. European Sovereign Cloud deployments not yet validated or supported by default.
-   **Docs** New interactive Configuration Builder on the documentation site (Deployment → Configuration builder). A fully client-side, browser-based form that generates and validates a VAMS `infra/config/config.json` from a Commercial, GovCloud, or EU Sovereign Cloud starting template, letting operators assemble a valid config without hand-editing deeply nested JSON, then download or copy the result. The builder mirrors the cross-field validation `getConfig()` enforces at deploy time (for example, GovCloud requiring ALB instead of CloudFront) and is a helper rather than a gate — `cdk synth` remains the source of truth. The component is a hand-maintained mirror of `infra/config/config.ts`; a new `infra/test/configBuilderSync.test.ts` drift check keeps its schema metadata and per-partition template defaults in sync with the `ConfigPublic` interface and the `config.template.*.json` files.
-   **CDK** Partition-aware service endpoint table (`infra/lib/helper/const.ts`) regenerated from the upstream botocore endpoints file. Added the `aws-eusc` (AWS European Sovereign Cloud) partition across existing services, filled in services added to the `aws`, `aws-cn`, `aws-us-gov`, `aws-iso`, `aws-iso-b`, `aws-iso-e`, and `aws-iso-f` partitions over time, and added newly published services. The generator (`infra/gen/genEndpoints.ts`) now performs a non-destructive merge that preserves existing entries and hand-tuned values.
-   **CDK** Enabling a feature that requires a VPC while `app.useGlobalVpc.enabled` is `false` now fails configuration validation with an explicit error that lists the offending features, instead of silently auto-enabling the VPC. This removes a confusing implicit topology change where the VPC turned on without the operator setting it.
-   **CDK** Updated external S3 deployment configuration logic and documentation to better support cross-AWS account S3 buckets. This includes new additional CDK config fields to define accountId, region, and optionalKms keys for those external S3 buckets.
-   Metadata GET APIs (asset, file, database, asset link) now implement true request/response pagination. Responses return a page of records plus a `NextToken`; `maxItems`, `pageSize`, and `startingToken` request parameters are supported and default to named constants, keeping the response payload under the AWS Lambda and Amazon API Gateway response-size limits. Schema enrichment and ordering are applied to the full record set before paging so ordering is stable across pages.
    -   The VamsCLI `metadata asset|file|asset-link|database list` commands and the web metadata views automatically follow `NextToken` to retrieve the complete metadata set. Direct API consumers that do not follow `NextToken` receive only the first page.
-   The asset listing API now flags truncated responses. When more assets remain than the per-response ceiling, the response includes `truncated: true` alongside the `NextToken` so callers that do not paginate can detect an incomplete result.
-   Intentional upload limits are now named constants with documented rationale. The **5,000 total-parts-per-upload-request** cap is documented as also bounding the presigned-URL response payload (one URL per part) under the AWS Lambda (6 MB) and Amazon API Gateway response-size limits, not only as an init-Lambda guard. See [Known Limitations](documentation/docusaurus-site/docs/troubleshooting/known-limitations.md).
-   New `vamscli assets unarchive` CLI command to restore a soft-deleted (archived) asset, wrapping the existing `PUT /database/{databaseId}/assets/{assetId}/unarchiveAsset` endpoint. Supports `--reason`, `--json-input`, and `--json-output`, and restores the asset's files and preview (removing their S3 delete markers).
-   API Gateway Migration: Migrated from API Gateway HTTP API (v2) to REST API (v1) using a cross-stack route registry and a single `SpecRestApi` with an inline OpenAPI spec. Routes are registered via `attachFunctionToApi` (defined in `apiRouteRegistry`) across nested stacks (`apiBuilder`, `apiBuilder2`, `searchBuilder`, addon stacks) and materialized into the OpenAPI spec by the API nested stack (`ApiNestedStack`), which selects an implementation construct (`RestApiGatewayConstruct`) so alternative entry points can be added in the future.
    -   The custom Lambda authorizer is now a REST REQUEST authorizer that returns an IAM policy with a wildcard resource (for cache correctness). It validates JWT tokens from Cognito or external OAuth providers, API keys, and optional IP allowlists. Client-IP resolution is per-request adaptive: it uses the CloudFront/ALB-forwarded client IP when a request arrives through the front and the direct source IP when a client calls the execute-api endpoint directly, so both fronted and direct callers are authorized correctly on the same deployment.
    -   MFA-login logic now consolidated to authorizer lambda for cognito / external IDP checks. Removes logic from each handler on processing this check.
    -   Restructured the `app.api` configuration block to support multiple API implementation types:
        -   `apiType`: Selects the backend API implementation. Fixed to `"APIGATEWAY_REST"` (the only supported value); any other value fails configuration validation.
        -   `apiGatewayRest`: A sub-block holding the API Gateway REST settings (the previous flat `app.api` fields now live here):
            -   `endpointType`: `"REGIONAL"` (default, public — does not route through any VPC endpoint) or `"PRIVATE"` (reachable only through an execute-api VPC interface endpoint; requires `useGlobalVpc.enabled` plus either `useGlobalVpc.addVpcEndpoints` or `externalPrivateAPIGatewayVPCEId`; incompatible with CloudFront).
            -   `globalRateLimit` (default 50) / `globalBurstLimit` (default 100): API Gateway throttling.
            -   `apiGatewayTimeoutTime` (default 29): Integration timeout in seconds — how long API Gateway waits for a handler Lambda before returning a `504`. Accepts a whole number from `29` to `300` and applies to every route, for both `"REGIONAL"` and `"PRIVATE"` endpoint types. Values above `29` require an approved account-level increase to the API Gateway **Integration timeout** quota (`L-E5AE38E3`) in the deployment Region **before** deploying — API Gateway rejects an integration timeout above the approved quota and the deployment fails. The increase may require a compensating reduction in the account's Region-level request throttle quota. Configuration validation rejects out-of-range values and emits a synthesis-time warning whenever the value exceeds `29`. Raising this lets operations on assets with many files or relationships complete within one synchronous request rather than returning a `504` while the Lambda keeps working; the 15-minute Lambda timeout remains the outer bound. Defaulting to `29` keeps existing deployments unchanged and requiring no quota increase.
            -   `externalPrivateAPIGatewayVPCEId`: For a `"PRIVATE"` endpoint when `useGlobalVpc.addVpcEndpoints` is `false`, the id of a pre-existing execute-api interface VPC endpoint to use (required in that case). Applies only to `"PRIVATE"`; ignored for `"REGIONAL"` with a configuration warning.
        -   The REST API deployment stage name is **not** a configuration option — it is the fixed constant `API_GATEWAY_STAGE_NAME` (`"api"`) in `infra/config/config.ts`, shared with the VamsCLI endpoint constants and the web `/api/*` fronting. The stage path is absorbed by CloudFront's originPath and ALB's redirect so client URLs remain `/api/*`.
        -   **Migration note:** existing `config.json` files using the flat `app.api` layout must move `globalRateLimit`, `globalBurstLimit`, and `endpointType` under `app.api.apiGatewayRest` and add `app.api.apiType: "APIGATEWAY_REST"`.
    -   Only a `"PRIVATE"` endpoint uses an execute-api interface VPC endpoint. The VPC builder creates it (gated on `apiType` being `"APIGATEWAY_REST"` and `endpointType` being `"PRIVATE"`) when `useGlobalVpc.addVpcEndpoints` is `true`; otherwise the operator supplies one via `externalPrivateAPIGatewayVPCEId`. A `"REGIONAL"` endpoint is public and does not use a VPC endpoint. The API does not require a CloudFront or ALB front — it is publicly addressable on its own.
    -   A REGIONAL Web ACL is associated with the REST API stage only when WAF is enabled and CloudFront is not used (a CloudFront-scoped ACL protects the front when CloudFront is enabled).
    -   CloudFront's `/api/*` behavior uses `originPath: "/api"`, and ALB redirects `/api*` and `/secure-config*` paths by prepending `/api` (the fixed REST API stage), so the browser/CLI base URL is unchanged.
    -   The file streaming/download APIs (`GET /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}` and `GET /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}`) now always deliver files by returning a `307` redirect to a short-lived S3 presigned URL, instead of streaming smaller files inline as a base64-encoded body. This is required under the API Gateway REST API. Redirecting adds a second request hop (the redirect to S3) to every file fetch, which can slow clients that issue many small requests — for example octree or 3D tile streaming viewers that make numerous small metadata/tile fetches. This is an accepted trade-off as a result of the API migration for now.
-   **CDK** Add optional network restrictions for S3 presigned URLs (`app.assetBuckets.presignedUrlNetworkRestrictions`). Configured `allowedIpRanges` (IPv4/IPv6 CIDRs) or `allowedVpceIds` (S3 interface or gateway VPC endpoint IDs; mutually exclusive with IP ranges) are enforced as bucket policy deny statements on the VAMS-created asset and auxiliary buckets, applying only to presigned (query-string authenticated) requests — backend operations and presigned URL lifetimes are unaffected. Restriction changes apply on redeploy, including to already-issued URLs. For externally imported asset buckets, an equivalent manual bucket policy is documented in the external S3 setup guide.
-   **Web** Cesium 3D Tileset viewer upgraded to the widget-less `@cesium/engine` package (v26) from the full CesiumJS distribution (v1.118). Security posture improvement: the engine-only bundle contains no Knockout.js and no dynamic JavaScript code generation on its load and render paths, removing the viewer's dependency on the `unsafe-eval` CSP directive — the viewer no longer requires the `ALLOWUNSAFEEVAL` feature flag and is now available on all deployments. The bundle is also ~3x smaller (~4MB vs ~13MB minified). Known content-type limitations under a strict CSP: KTX2/Basis compressed textures and `.spz` Gaussian splats still require `unsafe-eval` (Emscripten embind).
    -   Cesium viewer UI standardized to match the other 3D viewers with scene graph panels, the controls panel now uses the same tabbed layout ( Scene Graph / Controls tabs).
-   Asset archive/unarchive file-state independence — Asset unarchive now restores the asset record only by default; the asset's files stay archived. A new opt-in (`unarchiveFiles` API field, `--unarchive-files` CLI flag, web modal toggle) also restores exactly the files that the asset archive operation archived, tracked via new `assetArchive`/`assetUnarchive` provenance records in the file version history. Files archived individually before an asset archive always stay archived on asset unarchive and are restored through the file unarchive API. Assets archived before provenance tracking restore the record only. Additionally, uploading a file directly to S3 under an archived asset's prefix now auto-restores the asset record (DynamoDB-only, attributed to SYSTEM_USER) while preserving the archived state of its older files.
-   **CDK** WAF now blocks by default via a dedicated policy file. WAF rules are defined in `infra/config/policy/wafPolicyConfig.json`. The shipped file enables the AWS Common Rule Set, Known Bad Inputs, and Amazon IP Reputation List in block mode plus a rate-based rule for L7 DDoS / brute-force throttling; the web ACL default action remains `allow` (not deny-all). An empty or absent file falls back to the prior behavior (a single AWS Common Rule Set in count-only mode). See the WAF section of the configuration reference.
-   **CDK** WAF now always protects the Amazon API Gateway API when enabled. A regional-scoped web ACL is always created in the deployment Region and associated with the API Gateway stage — for both `REGIONAL` and `PRIVATE` endpoint types, and regardless of whether Amazon CloudFront or an Application Load Balancer fronts the application. Previously, when CloudFront was enabled the only web ACL was CloudFront-scoped (`us-east-1`) and the API Gateway stage received no WAF, leaving the directly-reachable `execute-api` endpoint unprotected. CloudFront deployments now create **two** web ACLs from the same policy file — a regional ACL for the API (and ALB) and a `CLOUDFRONT` ACL in `us-east-1` for the distribution — because AWS WAF does not allow a CloudFront-associated web ACL to be shared with any other resource type. ALB-only and no-front deployments are unchanged (a single regional web ACL, same stack name as before). **Backwards compatible:** existing WAF-on deployments keep their current web ACL and stack; CloudFront deployments gain an additional regional WAF stack (`{name}-waf-regional-{baseStackName}`) with no change to the existing CloudFront WAF stack.
-   Database creation now rejects a `databaseId` that matches a reserved S3 keyword (`pipeline`, `pipelines`, `preview`, `previews`, `temp-upload`, `temp-uploads`, `workspace`, `workspaces`), matched case-insensitively, in addition to the existing `GLOBAL` restriction. The identifier can become a path segment inside the asset or aux buckets, so a reserved name would collide with the folders VAMS reserves for system use.
-   **CDK** Gaussian Splat Toolbox container images can be built in the cloud with AWS CodeBuild instead of locally during a CDK deploy, matching the option already available for other pipelines
-   **Pipelines** Gaussian Splat Toolbox upgraded to the upstream Open Source 3D Reconstruction Toolbox for Gaussian Splats on AWS **v1.0.0** release (pinned commit `73133959c04fb0f9f002e95b4d2a722de2d18722`), bringing that release's reconstruction and export capabilities into VAMS. Output now includes mesh and interchange formats alongside the splat formats — `.usdz` for USD pipelines and a collision mesh (`.ply`) suitable for simulation and physics use — in addition to `.ply`, `.spz`, and `.sog` splats, plus `.mp4`/`.png` renders.
-   New **VAMS agent skill** (`tools/VamsAgentSkill/SKILL.md`) — A portable agent skill for operating a live VAMS deployment at runtime through the installed `vamscli` tool (research, inventory/audit, locating files, bulk metadata updates, cross-linking, and running processing workflows). The skill hardcodes no commands: it self-discovers the deployment's current commands via `vamscli --help` and caches a per-session command map, so it stays correct as VAMS evolves. It authenticates per session, pulls the user's allowed API routes (`GET /auth/routes/api/allowed`) to scope what it will attempt, and operates **read-only by default** — mutating commands (create, delete, edit, execute, upload) require explicit user authorization, with confirmation for destructive and bulk operations. Surfaced in Claude Code as the `/vams-agent` slash command; the skill itself is host-agnostic and can also be deployed to Amazon Bedrock AgentCore or another managed runtime.
-   New **VAMS MCP server** (`tools/VamsMCP/`) — A [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes the VAMS API as agent-callable tools, so any MCP-capable host (Kiro, Claude Desktop, Amazon Bedrock agents, internal orchestrators) can search, inspect, and manage VAMS databases, assets, files, metadata, versions, tags, asset links, and workflows through natural language. It stores **no credentials, keys, or URLs**: it reuses the `vamscli` profile the user already configured on their own machine, so each user runs it against their own VAMS account under their own login and effective permissions are exactly that user's two-tier (RBAC/ABAC) VAMS permissions. See [Agentic Development](documentation/docusaurus-site/docs/developer/agentic-development.md) and `tools/VamsMCP/README.md`.

### Deprecations

-   AI Steering Documents: Cline agent steering (`.clinerules/workflows/`) has been deprecated and removed. **Kiro** (`.kiro/steering/`) and **Claude Code** (`CLAUDE.md` files + `.claude/commands/`) are the two currently maintained AI-assisted development agents. A new `.kiro/steering/WEB_FRONTEND.md` front-end steering file mirrors `web/CLAUDE.md` for Kiro.
-   **CLI** Old CLI documentation pages deprecated in favor of the main documentation. Installation, authenticaiton, and development pages left for now in `/tools/vamscli/docs` but commands and troubleshooting pages are migrated to `documentation\docusaurus-site\docs\cli\*`.

### Bug Fixes

-   **Security / Networking** AWS WAF rate-based rule now aggregates on the forwarded client IP (`X-Forwarded-For`) instead of the immediate source, so CloudFront/ALB and shared NAT/VPN deployments rate-limit real end users rather than a shared upstream address; the per-IP limit was raised to accommodate VAMS's normal request volume (live status polling, uploads, viewer streaming). Rate-limited requests now return HTTP `429 Too Many Requests` (distinct from the `403` used for authorization denials), and both the web client and VAMS CLI treat it as a retryable throttle.
-   **Web** Fixed all viewers rendering ~20-30 pixels under the modal header in the file manager's pop-up file viewer; the viewer container's page-layout negative top margin no longer applies inside the modal.
-   Comment deletion is now atomic (a single Amazon DynamoDB `TransactWriteItems`), so a partial failure can no longer lose the comment; the add-comment error handler no longer masks non-`ClientError` exceptions as a 502; and the edit-comment 500 path now returns the correct message instead of a stale "Record not found".
-   `checkSubscription` returns a 400 for a malformed JSON body instead of a 502.
-   Asset-relationship (asset link) queries page through all results, so relationship trees and the alias-uniqueness check no longer silently truncate at one page.
-   Workflow and pipeline creation use a conditional write to prevent a concurrent create of the same identifier from clobbering an existing record.
-   Concurrent OAuth2 token refreshes are coalesced onto a single request, avoiding spurious re-logins when an identity provider rotates refresh tokens.
-   The `validate()` input dispatcher no longer skips validation of fields that follow an empty optional field, and the `BOOL` validator now rejects non-boolean values.
-   Subscription, comment, config, and email handlers copy the shared response template per invocation instead of mutating a module-global dict, preventing status/body leakage across warm Lambda invocations.
-   RapidPipeline writes its per-execution config to a namespaced Amazon S3 key so concurrent runs no longer collide on a shared `rp_config.json`.
-   Pipeline command construction hardened against injection. The RapidPipeline and ModelOps pipeline definition Lambdas now shell-quote every asset-filename / S3-key / parameter value interpolated into the container command (the ModelOps config JSON is passed as a single `printf '%s'` literal), preventing arbitrary command execution in the processing container via a crafted file name.
-   Two-tier authorization fails closed on missing identity. The asset-create, database-create, asset-ingest, download, and upload handlers now deny when no authenticated identity is present instead of falling through to the operation.
-   User-controlled metadata field names are escaped before interpolation into OpenSearch `query_string` queries, and the query-string escaper no longer double-escapes backslashes.
-   CDK Nag suppressions in the search/indexing stack are scoped to the specific IAM wildcards and custom-resource-provider runtimes they cover, replacing a stack-wide match-all IAM5 suppression.
-   **Web** Fixed Cesium viewer camera and rendering bugs by adapting all camera controls and scene rendering to the loaded content type. For tilesets in local (non-geo-located) coordinates: the model no longer disappears when zooming with the mouse wheel (terrain collision detection disabled), camera controls orbit around the model itself instead of moving globe-relative (turntable-style controls, also fixing camera view buttons starting at movement boundaries), and the globe, atmosphere, and starfield/sun skybox are hidden so the background color setting works. For geo-referenced tilesets: free globe-level camera controls with terrain collision detection and globe-scale zoom-out limits, and the globe, atmosphere, and skybox are shown for geographic context (with a neutral globe surface color when no Cesium Ion imagery token is configured).
-   **Web** Fixed expired auth sessions leaving the app in a broken state where the UI still rendered as logged in (from cached tokens) while every API call returned 403. A new mode-agnostic session manager validates the session on page load, refreshes the token on an expiry-aligned timer, and revalidates when the tab regains focus (covering laptop sleep/backgrounded tabs). When the session can no longer be refreshed the user is returned to the login screen with a "session expired" notice and is sent back to the page they were on after signing in again. Works for both Cognito (including federated) and external OAuth2/IDP sign-in.
-   **Web** Fixed duplicate `POST /auth/routes` calls on page load. The route table and side navigation web-route permission checks are now batched into a single API call with per-session caching.
-   **Web** Fixed the asset relationships (asset links) component silently swallowing API errors. Failed GETs now surface an error alert with retry instead of rendering an empty relationship tree, and failed link create/delete operations now report errors instead of counting as successes.
-   **Web** Fixed the broken web Jest test infrastructure. Updated stale tests that no longer matched current component behavior (Navigation web-route permission filtering, FileMetadata MetadataContainer rendering, RoleGroupPermissionsTable role loading via APIService, WorkflowEditor element generation, removed Jest 30 deprecated matcher aliases).
-   Fixed some broken cross-reference links in the documentation
-   Fixed Potree pipeline PDAL pipeline container build failures due to internal dependency upgrades that caused test failures
-   **Web** Fixed the Potree viewer leaving orphaned color-picker and profile-window DOM elements on other pages after the viewer closed, which appeared as stray input fields at the bottom of the application until a page refresh
-   Added additional checks for metadata GeoJSON saving to account for different "bad" shape combiniations that may cause issues with OpenSearch indexing
-   Fixed bug in S3 bucket indexing function where filenames with certain special characters were not getting properly processed
-   Fixed bug in `CustomFeatureEnabledConfigNestedStack` where feature flags removed between deployments (e.g., disabling Physna Sync, switching off CloudFront in favor of ALB) remained in the `appFeatureEnabledStorageTable` DynamoDB table indefinitely. The custom resource now defines an `onDelete` handler that issues a `DeleteItem` for the feature when its CloudFormation resource is removed from the synthesized template.
-   Fixed Gaussian Splat open pipeline function to properly have permissions send task failure callbacks if an error is caught during initialization
-   Fixed NVIDIA Comos Transfer and Predict pipeline build pipeline failure due to Cosmos python upgrade to 3.13 which breaks build since v2.5.0. Pin docker python version to 3.10 to prevent this.
-   Fixed NVIDIA Comos and Gr00t pipelines that may result in container deadlock due to output buffer overflows during hugging face model downloads
-   Fixed NVIDIA Comos Predict v1 reference in CDK that was still forcing a local Docker build of predict v1 instead of using only v2.5 deployments (wasn't being used but caused longer CDK deployment times)
-   Fixed NVIDIA IsaacLabs pipeline to now properly lookup asset input locations to allow relative pathing in the submitted configuration files
-   Fixed NVIDIA IsaacLabs pipeline to have a unique SFN name per deployment configuration to avoid same-region multi-vams instance deployments
-   Programatically stripping NVIDIA Cosmos, Gr00t, and IsaacLab CRLF line endings on entrypoint files to account for different deployment machine OSs (Windows vs Linux/Mac) that could cause pipeline failures
-   **Web** Pipeline create/update and other components using ListPages now approrpriately displays API errors to the user
-   **Web** Fixed main landing page (after login) image not always loading correctly on first page load
-   Fixed hardcoded `aws` partition in workflow Step Functions ASL generation (createWorkflow, pipeline update) and the import-pipeline ARNs in CDK pipeline constructs, which broke deployments to non-commercial partitions (GovCloud, etc.). The deployment partition is now threaded through to the generated service-integration ARNs.
-   Fixed bug in various backend handlers with how it handles reserved S3 prefix names (preview, temp-upload, etc) and `*.previewFile.*` patterns where it overly excluded or didn't exclude certain files from indexing. This caused files that started with reserved names to be skipped or some previewFiles to be added for indexing and various sync processes.
-   Fixed bug in the OpenSearch reindexer (`crReindexer`) where files that did not yet have `assetid`/`databaseid` S3 object metadata (e.g. files never processed by the `sqsBucketSync` bucket-sync flow) were silently skipped during file reindexing, this includes syncing new buckets added to VAMS. Files whose key does not resolve to a valid, active asset location for the bucket are ignored.
-   Standardize the system user across the backend to the single ID `SYSTEM_USER`, previously a combination of `SYSTEM` and other lowercase/uppercase variations.
-   The S3 file versions in the file versions API call now properly show up for past archived file versions, previously they wouldn't show up anymore if looking at a file that was unarchived
-   Hardened various backend asset and file fetch/operation logic for correctness, large-scale behavior, and performance. This addresses minor logic issues, scaling limits that surfaced when an asset contains many files or a file has many versions, and slow operations that could approach the API timeout. Highlights:
    -   No more silent truncation on large assets. File version history, archive-status detection, preview-file discovery, and asset-version file listings now page through all Amazon S3 object versions/objects instead of reading only the first page (previously capped around 100–1,000 entries), so files with very large version histories and assets with thousands of files report complete, accurate results. Per-page batch sizes and concurrency caps are named constants.
    -   Faster, constant-time archive checks. Determining whether a single file (or a specific version) is archived now uses one Amazon S3 `HeadObject` call rather than listing object versions, removing a path that could misreport archive status for files with more than 1,000 versions. Centralized in a shared helper used across the upload, asset, asset-version, file, and bucket-sync handlers.
    -   Asset version create/revert no longer slow down on large assets. Building the file set for a version now uses a single paginated listing instead of multiple per-file S3 calls, and the per-file copy work during revert and the temp→final move during upload completion run in parallel, substantially reducing the time these operations take on assets with many files.
    -   Correct version-snapshot file listings. The detailed (non-basic) listing of a specific asset version no longer duplicates files that were archived and later unarchived, and presents snapshot files according to the captured version rather than their current state.
    -   Asset preview unarchive reliability. Unarchiving an asset now reliably removes the preview file's delete marker (a previous narrow lookup could miss it and leave the preview archived).
    -   Upload asset-type detection samples up to 1,000 files to classify an asset (empty / single file / folder) rather than scanning the entire asset, avoiding added latency on very large uploads.
-   **CDK** Fixed configuration check bug that didn't allow you to deploy without a ALB or Cloudfront (despite the error saying you can)
-   Fixed `vamscli assets archive` failing with "Request body is required" when no `--reason` was provided. The CLI now always sends a request body (`confirmArchive`) so archiving without a reason works.
-   Fixed the login profile API (`GET /auth/loginProfile/{userId}`) returning a 500 for users without a stored profile record (for example, a user not yet assigned any roles). The handler now returns an empty result set instead of erroring, so these users can sign in and retrieve their profile.
-   Fixed cross-origin (CORS) failures introduced by the REST API migration. The REST API integration returns each Lambda response verbatim (unlike the previous HTTP API, which injected the `Access-Control-Allow-Origin` header automatically), so handlers that hand-build their response headers must set the allow-origin header themselves. The shared response helpers now include it, and the handlers that construct headers directly — database delete, workflow delete, pipeline delete and enable, Physna Viewer, config, asset export, and file upload — now emit `Access-Control-Allow-Origin`. Previously, on a cross-origin (default CloudFront) deployment the browser blocked reading these responses even though the operation succeeded server-side.
-   **CLI** Fixed `vamscli tag create`/`tag update` and `tag-type create`/`tag-type update` failing with a validation error against the API due to improper input formatting from the CLI.
-   Asset records can no longer be corrupted by concurrent operations racing an archive or delete.
    -   Fixed a race where the S3 bucket-sync indexer (`sqsBucketSync`) could recreate an empty "ghost" asset after an asset was permanently deleted or archived. A stale or at-least-once-redelivered S3 `ObjectCreated` (upload) event for the asset's file could be processed after the asset's DynamoDB record was already removed, causing the sync to recreate the asset for a now-empty folder. The sync now verifies the S3 object still exists before (re)creating an asset, and asset creation uses a conditional write so a duplicate/concurrent create cannot overwrite an existing record. Legitimate ingestion of genuinely new files into a bucket is unaffected.
-   **Web** Asset comment rendering sanitization hardened — the comment HTML sanitizer now enforces an explicit URL scheme allowlist (`http`, `https`, `mailto`) on links and forces `rel="noopener noreferrer"` on all anchor tags, preventing `javascript:`-style URLs and reverse-tabnabbing from links in user-authored comments.
-   **Web** Asset and file search now properly update preview thumbnail caches when files/assets change their preview images on a new search without a full page refresh
-   Previous Cognito MFA checks were erroring, defaulting MFA validation to false. Cognito MFA checks now use the AdminGetUser to cognito to properly fetch MFA status for when cognito is enabled.
-   Fixed FIPS configuration endpoints using the FIPS endpoints for AWS data plane operations instead of just the control plane.
-   Updated the GenAI Metadata Labeling Use-case Pipeline primary lambda to timeout after `15` minutes instead of `5`. This should give more breathing room for larger 3D models that take more time to process.
-   **Web** Asset file components on the website no longer offer to export or view files when an asset is marked as not distributable. The file manager hides its Export menu, View File button, and viewer popup links, the asset preview thumbnail and its enlarged view are omitted, and the file view page renders only versions and metadata in place of the visualizer. The API already refuses download, streaming, and preview requests for such an asset, so these controls previously led only to an error.
-   **Web / CDK** The `web` and `infra` lockfiles now record the rolldown, esbuild, and lightningcss native bindings for Linux and macOS alongside Windows. npm captures only the binaries matching the platform that generated the lockfile, and a later `npm install` on another platform does not add the missing one, so a Linux build failed with `Cannot find native binding` once Vite began loading rolldown's bundler binary and lightningcss for CSS minification. The bindings are declared as `optionalDependencies` and carry their own `os`/`cpu` constraints, so each machine still installs only its own.
-   **CDK** The ALB web deployment now issues a temporary (`302`) redirect for the `/api` routes instead of a permanent (`301`) one. No `Cache-Control` accompanied the redirect, so browsers cached the `301` indefinitely and kept sending returning users to the API Gateway hostname recorded at the time of their first visit. That hostname is regenerated whenever the API is replaced, so after a redeploy the cached target no longer resolved and the web application failed at startup with a `Failed to fetch` error that only a manual browser cache clear recovered from.

### Chores

-   **CDK** AWS Batch GPU compute environments now specify the Amazon Linux 2023 NVIDIA-accelerated AMI (`ECS_AL2023_NVIDIA`). AWS Batch blocks creation of new Amazon ECS compute environments that use Batch-provided Amazon Linux 2 AMIs, so the Gaussian Splat Toolbox, NVIDIA Cosmos (Predict, Reason, Transfer), Cosmos 3, GR00T, and Isaac Lab compute environments previously failed to create on a new deployment with `Amazon Linux 2 is end-of-life`. Isaac Lab now sets its image type explicitly rather than relying on the AWS CDK default, which selects an Amazon Linux 2 GPU AMI. All configured GPU instance families (G5, G6, G6E, P4DE, P5, P5E) are supported by the AL2023 NVIDIA AMI. **On upgrade this replaces each GPU compute environment**, so drain in-flight GPU pipeline jobs before deploying.
-   Backend and web frontend work to breakout reserved keywords or variables like S3 prefixes or extension names to common constants location files
-   **Web** All plugin viewers with at custom install that use a dynamic NPM package install will now perform a `npm audit fix` before building the packages to implement easy real-time security patches
-   Pipelines and Workflow backend logic now checks to make sure IDs are unique across all databases (and GLOBAL), this help prevent overlap of IDs for old references that don't include the pipeline/workflow database Id as a secondary index
-   **CDK** Added a second backend API nested stack (`ApiBuilder2NestedStack`) and moved the self-contained Tags and Tag Types API functions and routes into it. `ApiBuilderNestedStack` was approaching the CloudFormation per-stack resource limit; the move frees headroom (primary stack ~186 resources, secondary ~17) and new API endpoints can be added to the secondary stack going forward. Only domains whose Lambda functions are self-contained — no cross-stack function references and no IAM role ARNs persisted into long-lived resources — are moved; the pipeline and workflow functions intentionally stay in `ApiBuilder` because the workflow IAM role's path-derived name is baked into existing Step Functions state machines.
-   **CDK** Replaced the three stack-wide Lambda CDK Nag suppressions (`AWSLambdaBasicExecutionRole`, `AWSLambdaVPCAccessExecutionRole`, and wildcard KMS actions) in `CoreVAMSStack` with a per-Lambda `suppressCdkNagLambda()` helper called from every Lambda builder, plus a targeted `suppressCdkNagLambdaFrameworkResources()` pass. Scoping the suppressions to the resources that actually need them reduces the per-resource metadata footprint (the unused execution-role suppression reason dropped from 873 to 425 occurrences across the synthesized templates).
-   **Web** Removed the unused legacy `web-ifc` dependency from the core `web/package.json`
-   **Web** Removed legacy Asset Selector component and file type constants that were no longer used in the code
-   **Web** Removed the "Limited Search Mode" informational alert on the asset search page when OpenSearch is not deployed
-   OpenSearch engine version bumped from `OPENSEARCH_2_7` to `OPENSEARCH_3_5` (provisioned deployments only; serverless is unaffected). The reindex required by the engine upgrade is bundled with the v2.5 → v2.6 migration.
-   Bumped minimum supported Node.js version for development and build tooling from 20.18.1 to 22.22.3 to address the AWS SDK for JavaScript v3 `NodeVersionSupportWarning` (versions published after the first week of January 2027 will require Node 22+). Updated `web/.nvmrc`, root/`web`/`infra`/`documentation/docusaurus-site` `package.json` engines, `@types/node` in `web` (^18 → ^22), and all README/documentation references.
-   Role lookups for claims (logging only) are now performed entirely in the REST API custom Lambda authorizer. Authorization logic always separately loks up roles for Casbin checks. See the new [Authentication and Authorization Flow](documentation/docusaurus-site/docs/developer/security.md) developer guide section.
-   Updated authLoginProfile backend handler to now conform to the latest standard for how API handlers are implemented with request/response models, validation, error handling, etc.
-   Updated configService backend handler to now conform to the latest standard for how API handlers are implemented with request/response models, validation, error handling, etc.
-   Bumped Lambda Node.js runtime from `NODEJS_20_X` to `NODEJS_22_X` (`infra/config/config.ts` `LAMBDA_NODE_RUNTIME`). Affects all Node-based Lambdas (Schema Deploy custom resource, etc.).
-   **Web** Bumped Vite `build.target` and `optimizeDeps.esbuildOptions.target` from `es2020` to `es2022`.
-   **Web** Updated the PlayCanvas Gaussian Splat viewer's bundled PlayCanvas engine (`customInstalls/playcanvas`) from `2.17.2` to `2.19.6`.
-   **CDK** DynamoDB tables are now set to be `RETAINED` on a stack teardown instead of deleted to prevent accidental deletion. DynamoDB tables are uniquely named per stack deployment so conflicts don't appear.
-   **CDK** Updated S3 asset bucket config and bucket config DynamoDB tables to store default bucket values. Default buckets are used for pipeline/workflow/execution storage information.
-   **Web** The application footer now displays the backend VAMS version, fetched from the anonymous `/api/version` endpoint (no authorization required).
-   Documentation updated to inventory the named Amazon CloudWatch log groups (`/aws/vendedlogs/...`) VAMS creates and the Amazon S3 web access logs bucket, and the uninstall procedure now covers deleting retained, deterministically named log groups (including conditional ones such as AWS CloudTrail and VPC flow logs) that must be removed before redeploying the CDK stack with the same configuration name and account.
-   Bumped backend and base Lambda layer `boto3`/`botocore` from `1.34.x` to `1.43.45` to support the new `aws-eusc` (EU Sovereign Cloud, region `eusc-de-east-1`) partition.
-   Update documentation to point to new physical-ai blog locations (AWS spatial blogs migrated to physical-ai tag)
-   Updated root README to now point all documentation to the documentation website and not the source markdown files
-   Update various package dependencies across the solution

### Known Outstanding Issues

-   With multiple S3 bucket support, identical assetIds across different buckets/prefixes in different databases can cause lookup conflicts in comments and subscriptions. This only occurs with manual S3 changes, as VAMS-generated assetIds use unique GUIDs.
-   Pipeline metadata inputs have a size limit when sent to ECS pipelines. Assets or files with extensive metadata may exceed the 8K character ECS JSON input limit. A future pipeline overhaul will convert metadata input to a file-based approach.
-   For assets with hundreds to thousands of files or very large files (TB-size), some API operations may time out while the Lambda continues processing (up to 15 minutes). The API Gateway integration timeout is configurable via `app.api.apiGatewayRest.apiGatewayTimeoutTime` (default 29 seconds, maximum 300), which raises this ceiling on accounts that have an approved **Integration timeout** quota increase.
-   The Amazon Cognito MFA check requires the API Gateway authorizer to run outside the VPC. VAMS does not create Amazon Cognito VPC interface endpoints, so when Lambda functions run in the VPC (`useForAllLambdas`) the authorizer has no path to Amazon Cognito; the Cognito MFA check is disabled (`COGNITO_AUTH_ENABLED = FALSE`) and `mfaRequired` on a role has no effect. The MFA check and MFA-aware role enforcement apply only when the authorizer runs outside the VPC.

### Troubleshooting

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
    -   Added `tools/PermissionsSetup/apply_template.py` tool for automating deployment of roles and constraint templates, useful for setting up permission structures when new databases are created
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
-   New addon feature and configuration which allows pushing database, asset, and file changes to a Garnet Framework solution (Knowledge graphs) deployed in the same AWS account. Visit [garnet-framework.dev](https://garnet-framework.dev/) for more information on the garnet framework solution. See the [ConfigurationGuide.md](./documentation/ConfigurationGuide.md) on how to turn this addon feature on.
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
    -   Note: Authentication events are handled through Cognito or external IDP event logs currently. See [AuditLoggingGuide.md](./documentation/AuditLoggingGuide.md) for more details.

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
