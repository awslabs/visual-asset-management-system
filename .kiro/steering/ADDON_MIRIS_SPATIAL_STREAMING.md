---
inclusion: fileMatch
fileMatchPattern:
    [
        "backend/backend/handlers/addon/miris/**",
        "backend/backend/models/addon/miris*",
        "backend/tests/handlers/addon/miris/**",
        "infra/lib/nestedStacks/addon/miris/**",
        "infra/config/addon/miris*",
        "infra/test/addon/miris*",
        "infra/test/config/mirisConfig*",
        "backendPipelines/addon/conversion/mirisUpload/**",
        "web/src/visualizerPlugin/viewers/MirisStreamViewerPlugin/**",
        "web/src/services/addons/miris*",
        "web/customInstalls/miris/**",
        "documentation/docusaurus-site/src/components/ConfigBuilder/addon/mirisStreaming*",
    ]
---

# Miris Spatial Streaming add-on

The Kiro mirror of the Miris Spatial Streaming add-on's own `CLAUDE.md` files, one section per component. Every rule in the core steering documents applies as well; these sections add what is specific to Miris. Keep each section in step with the `CLAUDE.md` it mirrors (root `CLAUDE.md` Rule 11).

## CDK (`infra/lib/nestedStacks/addon/miris/`)

Mirrors `infra/lib/nestedStacks/addon/miris/CLAUDE.md`.

### Layout

| File                                                    | What it is                                                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mirisBuilder-nestedStack.ts`                           | `MirisBuilderNestedStack`, nested in `AddonBuilderNestedStack` while `app.addons.useMirisStreaming.enabled`; registers `GET /addon/miris/asset-status` and `POST /addon/miris/viewer-key` with `registry: props.registry`, builds the perView viewer key table, its TTL cleanup consumer and the two alarms |
| `lambdaBuilder/mirisFunctions.ts`                       | `buildGetMirisAssetStatusFunction`, `buildMintMirisViewerKeyFunction`, `buildMirisViewerKeyCleanupFunction`, `mirisMetricsNamespace()`; each grants `secretsmanager:GetSecretValue` on exactly `apiKeySecretArn`                                                                                            |
| `constructs/dynamodb-metadataschema-miris-construct.ts` | Seeds the GLOBAL "Miris stream" file metadata schema (`miris-stream-file-schema`) from `dynamodb-metadataschema-miris-stream.json`                                                                                                                                                                          |
| `upload/mirisUploadBuilder-nestedStack.ts`              | `MirisUploadBuilderNestedStack`, built by `AddonBuilderNestedStack` while `upload.enabled`; returns `pipelineVamsLambdaFunctionName` for registration                                                                                                                                                       |
| `upload/constructs/mirisUpload-construct.ts`            | The AWS Batch Fargate job, its state machine and the vamsSchema registration                                                                                                                                                                                                                                |
| `upload/lambdaBuilder/mirisUploadFunctions.ts`          | The pipeline's Lambdas, `MIRIS_DEPENDENCY_MANIFEST_PREFIX` (`pipelines/miris-upload/manifests`, auxiliary bucket)                                                                                                                                                                                           |

The configuration lives in `infra/config/addon/mirisStreamingConfig.ts` (interface, defaults, validation, `mirisVpcRequiringFeatures`), joined through `config/addon/addonConfig.ts`, and the ConfigBuilder mirror in `documentation/docusaurus-site/src/components/ConfigBuilder/addon/mirisStreaming.ts`. Keep the two in step by hand: `infra/test/config/configBuilderSync.test.ts` checks fields and defaults only.

### Configuration (`app.addons.useMirisStreaming`)

`enabled`, `viewerKeyMode` (`perView` default, or `static`), `viewerKey` (static mode only), `viewerKeyTtlMinutes` (1 to 60, default 15), `apiKeySecretArn` (the complete Secrets Manager ARN, suffix included), `mirisApiBaseUrl` (https, not a private or link-local host), and `upload` (`enabled`, `autoRegisterWithVAMS`, `autoRegisterAutoTriggerOnFileUpload`, `triggerExtensions`, `taskTimeoutSeconds`, `maxAssetSizeBytes`). `autoRegisterAutoTriggerOnFileUpload` defaults to `false`: when on, it sends every matching file uploaded to any database to Miris. `getConfig()` rejects the add-on outside the `aws` partition (on `config.env.partition`, whatever `govCloud.enabled` says), without `app.webUi.allowUnsafeEvalFeatures`, and with `app.useGlobalVpc.useForAllLambdas` (the add-on's Lambda functions would land in the isolated subnets, with no route to Miris); `upload.enabled` requires the add-on, the global VPC and, with an imported VPC, private subnet ids. `triggerExtensions` may list only extensions the stream schema accepts (`MIRIS_STREAM_SCHEMA_EXTENSIONS`). Tests: `infra/test/config/mirisConfig.test.ts`, which also checks each rule against the ConfigBuilder.

### Lane entries

-   **Feature switches.** `MIRIS_ADDON` (the add-on) and `MIRIS_UPLOAD` (the upload pipeline, which gates the viewer's Stream with Miris button), pushed by `AddonBuilderNestedStack`.
-   **Resource name.** The viewer key table is published as `addon/miris/viewerKeyStorage` (`infra/common/addonResourceParamKeys.ts`, mirrored in `backend/backend/common/addonResourceNames.py`).
-   **Read-only role.** `addonDefaultRoutes.ts` adds criterion `19_<role>_api_paths` (`equals` `/addon/miris/asset-status`) to the GET constraint and `17_<role>_api_paths_post` (`equals` `/addon/miris/viewer-key`) to the POST constraint; exact matches, so no later route under `/addon/miris/` is granted with them.
-   **CSP.** `addonCsp.ts` adds `blob:` to `script-src` (the SDK loads its engine glue from a `blob:` URL) and `https://*.miris.com` and `wss://*.miris.com` to `connect-src`. `'unsafe-eval'` comes only from `app.webUi.allowUnsafeEvalFeatures` (the SDK's generated bindings use `new Function`). Tests: `infra/test/addon/mirisAddonCsp.test.ts`, `addonCspScriptSrc.test.ts`.
-   **VPC.** `addonVpcEndpoints.ts`: `addonNeedsPrivateSubnets()` is true while `upload.enabled`, so a VPC VAMS creates gets private subnets and NAT gateways (the Fargate jobs reach `*.miris.com` through them), and `addonInterfaceEndpoints()` adds the Batch, ECR API and ECR Docker endpoints under the core blocks' ids, so a core pipeline's endpoint is not repeated. No ECS endpoint: that is the control plane an EC2-launch-type instance's agent uses, and Fargate tasks do not. Test: `infra/test/addon/mirisUploadVpcPlacement.test.ts` (synth).
-   **Quarantine block.** While `app.compliance.quarantineBlocksDownload` is on, the viewer key Lambda and the upload pipeline's `vamsExecute` Lambda get `COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD` and a read grant on the compliance asset state table (`vamsExecute` also gets `COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME`), as the core download builders do. Test: `infra/test/addon/mirisAddonStack.test.ts`.
-   **Stack order.** The upload pipeline's vamsSchema registration custom resources invoke the import Lambda built in ApiBuilder2, so `AddonBuilderNestedStack.invokesVamsSchemaImportFunction` is true and the core stack deploys AddonBuilder after ApiBuilder2.
-   **Route parity.** `enableRouteContributingAddons()` (`infra/test/addon/support/addonTestHooks.ts`) enables the add-on in perView mode, so `test/api/apiRouteBackendCdkParity.test.ts` sees both routes.

### Viewer keys and the Integration Key

perView mode creates a retained, auto-named DynamoDB table (one row per minted key, TTL on `expiresAt`) and a stream consumer, `viewerKeyCleanup`, that deletes a key from Miris when TTL removes its row, with a dead-letter queue. Static mode builds neither; it creates `MirisViewerKeySecret`, empty in the template, which `populateHuggingFaceTokenSecret` (a core helper that stores one plain string) fills at deploy time from its code asset, and gives the status and mint Lambdas `MIRIS_VIEWER_KEY_SECRET_ARN` with `secretsmanager:GetSecretValue` on exactly that secret. The key must never reach the template or a Lambda environment (`mirisAddonStack.test.ts` greps the synth for it). The status, mint and cleanup Lambdas read the Integration Key from Secrets Manager and get `MIRIS_METRICS_NAMESPACE` (`VAMS/<configuration name>-<baseStackName>/Miris`), where the backend publishes `MirisAuthFailures` in embedded metric format.

Two CloudWatch alarms, with no action attached: `<name>-<baseStackName>-mirisAuthFailures` (Sum over 5 minutes above 0) and, in perView mode, `<name>-<baseStackName>-mirisViewerKeyCleanupDLQ` (messages visible in the DLQ). Tests: `infra/test/addon/mirisAddonStack.test.ts`.

### Upload pipeline (CDK)

`AddonBuilderNestedStack` builds a `SecurityGroupGatewayPipelineConstruct` and passes `privateSubnets.pipeline` to `MirisUploadBuilderNestedStack`. The job is AWS Batch on Fargate (`awslogs` driver to a VAMS-owned `/aws/vendedlogs/Pipelines/...` group, counted with the core Fargate job definitions in `infra/test/pipelines/batchFargatePipelineProps.test.ts`).

The state machine starts at `ConstructPipelineTask`, whose `Catch` (`ResultPath: "$.error"`, keeping `externalSfnTaskToken`) leads to `MirisUploadEndFailure`, a Lambda invoke of `pipelineEnd` that reports on the parent workflow's callback token before its `Fail` state. Without it a timeout, out-of-memory kill or import error in that first state reports nothing and the parent task waits out its whole `taskTimeout` (`infra/test/pipelines/constructPipelineTaskErrorRoute.test.ts`).

Tests: `mirisUploadPipeline.test.ts` (harness `mirisUploadHarness.ts`), `mirisUploadNag.test.ts`, `mirisUploadNagRegexes.test.ts`, `mirisAddonNag.test.ts`, `mirisStreamSchemaSeed.test.ts`.

## Backend (`backend/backend/handlers/addon/miris/`)

Mirrors `backend/backend/handlers/addon/miris/CLAUDE.md`.

### Layout

| File                    | What it is                                                                                                                                                |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `getAssetStatus.py`     | `GET /addon/miris/asset-status`: the stream recorded on one file, its Miris state, and `stale` when a file the stream was built from has changed          |
| `mintViewerKey.py`      | `POST /addon/miris/viewer-key`: the deployment key (static) or a key minted for the file's one Miris asset (perView), recorded in the viewer key table    |
| `viewerKeyCleanup.py`   | DynamoDB stream consumer: deletes a perView key from Miris when TTL removes its row; reports failed records by `SequenceNumber`                           |
| `mirisIntegration.py`   | The Miris API client: Integration Key cache, bounded requests, no redirects                                                                               |
| `streamMetadata.py`     | Reads the `miris_*` file metadata (`miris_asset_uuid`, `miris_source_version_id`, `miris_uploaded_at`, `miris_display_name`, `miris_dependency_manifest`) |
| `dependencyManifest.py` | Reads the upload's dependency manifest from the auxiliary bucket for the `stale` check                                                                    |

Models: `backend/backend/models/addon/miris.py`. Routes: `common/addonApiRoutes.py` (`MIRIS_ROUTES`, `addon_feature=ADDON_FEATURE_MIRIS`). The switches `ADDON_FEATURE_MIRIS` (`MIRIS_ADDON`) and `ADDON_FEATURE_MIRIS_UPLOAD` (`MIRIS_UPLOAD`) are in `common/addonFeatures.py`.

### Rules

-   **Authorization.** Both routes check Tier 1 (`enforceAPI`) and then read the asset (Tier 2 GET). Neither request carries a Miris asset UUID; both read it from the file's metadata server side.
-   **Distribution rule.** After Tier 2 and before any metadata or Miris read, the viewer key route applies the core download routes' rule: `require_distributable_asset()` (`common/downloadEligibility.py`) with `check_quarantine_block` (`common/compliance/quarantineGuard.py`). A refusal is `400` with the user-facing message; the quarantine refusal goes through `quarantine_refusal`. The Lambda gets `COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD` and a read grant on the compliance asset state table only while `app.compliance.quarantineBlocksDownload` is on. The tests' `conftest.py` registers the real `common.compliance` package, as `tests/handlers/assets/conftest.py` does.
-   **Binding.** A key is returned only for a Miris asset tagged with both `vams-asset-{assetId}` and `vams-database-{databaseId}`; a Miris asset without both, or one Miris no longer has, is `notBound` (`403`). A Miris `404` is `notBound`, never a mint.
-   **Static viewer key.** Read from the secret named by `MIRIS_VIEWER_KEY_SECRET_ARN` with `mirisIntegration.load_static_viewer_key`, cached by ARN like the Integration Key. A failed read is `502` on the viewer key route and `unknown` on the status route.
-   **Integration Key.** Cached for up to an hour per execution environment and read again once when Miris answers `401` or `403`. A refusal after that re-read is logged as `miris_auth_failed` with the `MirisAuthFailures` metric (embedded metric format, in `MIRIS_METRICS_NAMESPACE` when it is set). Never log key material, a viewer key, or a Miris error body.
-   **Time.** Miris calls are bounded to fit the API Gateway integration timeout; when time runs out the status route reports `unknown` and the viewer key route answers `504`. The status route's staleness reads and the key table write use short timeouts and two attempts (`tests/common/test_shared_client_retry_config.py`).
-   **Audit.** The three entrypoints carry `@audited_handler`. Registered in `common/addonAuditActions.py`: `mirisAssetStatusGet` (read, recorded on a successful status request with named `request_parameters`, never the raw query), `mirisViewerKeyMint` (the viewer key route's action, auth-other group) and `mirisStaticViewerKeyIssue` (secondary, static mode). Records carry no key material.

### Tests

`backend/tests/handlers/addon/miris/`: `test_mirisGetAssetStatus.py`, `test_mirisAssetStatusStaleness.py`, `test_mirisMintViewerKey.py`, `test_mirisViewerKeyCleanup.py`, `test_mirisIntegration.py`. `addonRouteGrants.py` holds the permission template grants for both routes (read by `tests/handlers/auth/test_permission_templates_addon_routes.py`), and `test_miris_template_criteria.py` checks that each granting template spells them as `equals` criteria, as the read-only seed does.

## Upload pipeline (`backendPipelines/addon/conversion/mirisUpload/`)

Mirrors `backendPipelines/addon/conversion/mirisUpload/CLAUDE.md`.

-   **Root file.** The selected file on a manual run; otherwise the `miris_root_file` asset metadata, or the one USD layer no other layer references. Several candidates fail and are named; the pipeline never guesses, and its message never advises adding references. The root must define geometry.
-   **Pre-flight before any upload.** Text-USD bytes against the task's memory budget (`memory_budget.py`), unreadable or out-of-asset references, texture formats and stage metadata (`preflight.py`). Every download is pinned to one listing of the asset; a file that changes mid-run fails.
-   **Same Region only.** The container reads the asset and writes the stream metadata with a client for the deployment Region, so `vamsExecuteMirisUpload.py` calls `manifestHelper.enforce_same_region_io(resolved)` before `mirisUploadGate` claims the content (`backendPipelines/tests/test_pipeline_cross_region_inputs.py`).
-   **Distribution rule.** `vamsExecuteMirisUpload.py` reads the asset record before the gate and refuses a missing or non-distributable asset and, while `COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD` is on, a quarantined one without a granted exception (the backend's `check_quarantine_block` rule, which a pipeline Lambda cannot import). A refusal raises, so the run fails with the reason and nothing is claimed or uploaded.
-   **Claim.** A triggered run claims a fingerprint of the asset's USD files in the auxiliary bucket, so the per-layer triggers of a multi-file upload, or a repeat trigger on unchanged content, upload once. A manual run skips the claim. A claim is `pending` (with its `claimedAt`) until `pipelineEnd` marks it `completed` on success; a pending claim older than the inner execution's limit plus `MIRIS_UPLOAD_END_REPORT_MARGIN_SECONDS` (the gate's `MIRIS_UPLOAD_CLAIM_STALE_SECONDS`) is stale and the gate replaces it with a conditional write on its ETag. The outer workflow task times out at the same point (`executionConfigOverrides.taskTimeout`), and the execution-ended rule's target has a dead-letter queue with an alarm. The claim prefix is a constant that matches the IAM grants.
-   **Never delete a Miris asset.** A run links the file as soon as Miris has the upload, and exits before anything reaches Miris when too little task time is left.
-   **Container dependencies.** A uv project: `container/pyproject.toml` declares them, `uv.lock` pins them, and the Dockerfile installs the hashed export `requirements.txt` with `--require-hashes`. Change a pin with the uv version in `required-version` (`uvx --from uv==<version> uv lock`, then the export command in the file's header); never edit `requirements.txt` by hand.
-   **Shared copies.** `lambda/customLogging/` is a copy of the shared pipeline logging package and stays byte-identical to the others (`backendPipelines/tests/test_pipeline_logger_identity.py`). `openPipeline.py` follows the repo-wide extension gate and failure-route rules (`backendPipelines/tests/test_open_pipeline_extension_gates.py`, `test_open_pipeline_failure_route_returns.py`); its allow list comes from `upload.triggerExtensions`.

Tests: `container/tests/` (run with the container's own environment; `PYTHONPATH` must not point at a local USD build) and `lambda/tests/`.

## Viewer (`web/src/visualizerPlugin/viewers/MirisStreamViewerPlugin/`)

Mirrors `web/src/visualizerPlugin/viewers/MirisStreamViewerPlugin/CLAUDE.md`.

### Registration and install

The entry is in `config/addonViewerConfig.json` + `viewers/addonManifest.ts` (`.usd`, `.usda`, `.usdc`, `.usdz`; `featuresEnabledRestriction` `MIRIS_ADDON` and `ALLOWUNSAFEEVAL`) and ships disabled. To enable it, set `"enabled": true` on that entry; `web/customInstalls/miris/mirisInstall.js` (in `postinstall`) checks it through `customInstalls/utility/checkViewerEnabled.js`.

The SDK (`@miris-inc/components`, `@miris-inc/core`, `@miris-inc/three`) and its `three` are not web dependencies: the install bundles them with webpack into an ES module and copies it with the SDK's `.wasm` files to `public/viewers/miris/`, where `dependencies.ts` imports it at runtime. The exact pins live in `customInstalls/miris/package.json`; upgrade the `@miris-inc/*` packages together with `three`, which stays inside the SDK's peer range (`>=0.182.0 <0.186.0`).

### Behavior

The viewer streams the Miris asset recorded in a USD file's `miris_*` metadata. It reads the file's state from `GET /addon/miris/asset-status` and, only for a streamable file on a browser that grants a WebGPU adapter (the SDK renders only with WebGPU; `MirisStreamDependencyManager.isDeviceSupported()` runs before the key request), gets a viewer key from `POST /addon/miris/viewer-key` (held in memory for that open) and imports the SDK bundle from `/viewers/miris/miris-sdk.js`, bounded at 60 s. The entry has no `dependencyManagerMethod`, so the registry never loads the SDK up front, and `autoSelect` opens it ahead of the Needle USD viewer.

For a file with no recorded stream it shows "not yet streamed" guidance (the file manager's **Automation** action and the **Miris Spatial Streaming Upload** workflow) and a **Stream with Miris** button, gated on the `MIRIS_UPLOAD` feature switch, `can("POST", "/workflows/{workflowDatabaseId}/{workflowId}/execute")` and the USD extensions. `startMirisUpload` (`services/addons/mirisAPIService.ts`) starts a manual run of the GLOBAL `miris-upload` workflow on the status response's `filePath` with no template. When the user may also list the asset's workflow executions, `findRunningMirisUpload` first looks for an unfinished `miris-upload` run and shows an in-progress notice with a **View execution** link in place of the button; a failed lookup offers the button.

The viewer follows a found or started run until it ends: every 30 s through its execution details (`getMirisUploadRun`) or else the execution list, and every 90 s through the stream status alone; a source that fails twice in a row gives way to the next. The execution list is read for the followed run itself (`findRunningMirisUpload` with its `executionId`), so it reports that run's status and `executionError` as the details do. Success re-reads the status, failure shows the recorded `executionError` and offers the button again, and a `SKIPPED` run gets a neutral notice. A run the source still cannot see after 90 minutes is reported as unknown, and the button is offered again.

Only the camera carries `controls`: with `controls` on both the camera and the stream, one drag rotates twice. The canvas is transparent, so the viewer supplies its own backdrop.

Tests: `MirisStreamViewerComponent.test.tsx`, `dependencies.test.ts`, `streamStatus.test.ts`, `services/addons/mirisAPIService.test.ts`.
