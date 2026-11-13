# VAMS Backend + CDK Development Workflow & Rules

This document provides comprehensive guidelines for developing and extending VAMS backend APIs and CDK infrastructure. Follow these rules to ensure consistency, quality, and maintainability across all backend and infrastructure implementations.

> **Steering Document Sync (bidirectional):** This document mirrors the Claude Code steering in `backend/CLAUDE.md` and `infra/CLAUDE.md` (and cross-cutting rules in the root `CLAUDE.md`). Its testing sections are also the Kiro counterpart for `backend/tests/CLAUDE.md`. Whenever you change a rule, pattern, or convention here, make the equivalent change in the matching `CLAUDE.md` file(s) in the same change — and whenever those `CLAUDE.md` files change, reflect it back here. Keep the two sets of documents saying the same thing.

> **Add-on steering:** an add-on's own steering lives in `.kiro/steering/ADDON_*.md` (`inclusion: fileMatch`), which loads when you edit that add-on's files; this document covers core and the lanes an add-on plugs into.

## 🏗️ **Architecture Overview**

### **File Structure Standards**

```
backend/
├── backend/
│   ├── handlers/                # Lambda function handlers (one per API domain)
│   │   ├── assets/             # Asset-related handlers
│   │   │   ├── assetService.py # GOLD STANDARD implementation
│   │   │   ├── createAsset.py  # Asset creation handler
│   │   │   └── uploadFile.py   # File upload handler
│   │   ├── databases/          # Database-related handlers
│   │   └── [domain]/           # Other domain handlers
│   ├── models/                 # Pydantic request/response models
│   │   ├── assetsV3.py        # Asset API models (GOLD STANDARD)
│   │   ├── common.py          # Common response models
│   │   ├── addon/             # Add-on models (physnaViewer.py, edh.py)
│   │   ├── custom/            # FORK EXTENSION POINT: fork models (shipped empty)
│   │   └── [domain].py        # Domain-specific models
│   ├── common/                # Shared utilities
│   │   ├── constants.py       # Constants and configuration (object types, fields, blocklists: core + add-on + custom)
│   │   ├── addonConstants.py  # Add-on object types, constraint fields, blocklist entries, log keys
│   │   ├── addonResourceNames.py  # Add-on resource-name keys (AddonResourceKeys)
│   │   ├── validators.py      # Input validation functions
│   │   └── dynamodb.py        # DynamoDB utilities (query_all_items, query_has_match, batch_get_items, …)
│   ├── custom/                # FORK EXTENSION POINT: customApiRoutes.py, customAuditActions.py, customResourceNames.py, customConstants.py
│   ├── customResources/       # CloudFormation custom-resource handlers (getVpcEndpointIps.py)
│   └── customLogging/         # Logging utilities
├── tests/                     # Test files (mirror handler structure)
│   ├── handlers/              # Handler tests
│   ├── models/                # Model tests
│   └── conftest.py           # Test configuration
└── requirements.txt          # Python dependencies

infra/
├── lib/
│   ├── nestedStacks/
│   │   ├── apiLambda/         # API Gateway and Lambda definitions
│   │   │   ├── apiBuilder-nestedStack.ts  # API route definitions
│   │   │   └── constructs/    # Custom constructs
│   │   └── storage/           # Storage resource definitions
│   │       └── storageBuilder-nestedStack.ts  # DynamoDB, S3, SNS
│   ├── lambdaBuilder/         # Lambda function builders
│   │   ├── assetFunctions.ts  # Asset lambda builders
│   │   └── [domain]Functions.ts  # Domain lambda builders
│   └── helper/                # CDK helper utilities
└── config/                   # Configuration files
```

### **Handler Domains (`backend/backend/handlers/`)**

One folder per domain. The current domains:

-   `assets/` — Asset handlers (`assetService.py` is the GOLD STANDARD; `assetVersions.py` covers version CRUD + archive/unarchive + update; `assetHistory.py` serves the paged asset lifecycle history lookup)
-   `auth/` — Auth handlers (authorizer, constraints, cognito, preTokenGen, apiKeyService)
-   `authz/` — Casbin ABAC/RBAC enforcer (`CasbinEnforcer` proxy)
-   `assetLinks/` — Asset relationship management
-   `comments/` — Comment CRUD
-   `compliance/` — Compliance (schemas, bindings, evaluation, quarantine, cascades, audit). API handlers `complianceSchemaService`, `complianceSchemaBindingService`, `complianceEvaluateService`, `complianceQuarantineService`, `complianceCascadeService`, `complianceAuditService`; event-driven `complianceTrigger` (its own SQS queue subscribed to the asset and file indexer topics), `complianceWorkflowCallback` (EventBridge `workflow.execution.completed`; then launches the pipeline rules waiting for that workflow from `PipelineRuleWaitIndex` through the execute Lambda) and `complianceCascadeExecutor` (async Lambda invoke from `complianceCascadeService`, event `{"cascadeId"}`); shared `complianceEvaluationStore` (all AWS access + `run_evaluation`; it reads the asset row, the asset's current-file listing (one `list_object_versions` walk through the Region-routing S3 client) and the asset file-versions table only when a rule of a type that uses them is present, and the file-metadata, file-attribute and asset-links-metadata tables only for a metadata rule of the `files`, `fileAttributes` or `links` scope), `complianceNotifications`. Pure rule logic for the six `vams-rules-v1` rule types (`pipeline`, `metadata`, `relationship`, `fileStructure`, `versionCoverage`, `assetAttributes`) lives in `common/compliance/evaluationEngine.py`; creating or reverting to an asset version stamps `lastChangeSource: assetVersion` on the asset row (`common/assetProvenance.py`), an asset edit that changes its name, description or tags stamps `assetEdit`, a metadata write and an asset-link create or delete are stamped `metadataEdit` / `linkChange` by the trigger itself from the metadata, file-attribute, asset-link and asset-link-metadata stream records (`record_indexed_change`, one conditional UpdateItem with the change's own instant, in every database), so those requests carry no compliance work and the stamp's MODIFY re-evaluates the asset (a workflow execution's metadata write-back marks the rows it writes or deletes with its execution id, which the trigger stamps `workflowExecution` and skips), the quarantine-blocks-download guard shared by `downloadAsset`, `streamAsset`, `assetExportService` and `streamAuxiliaryPreviewAsset` in `common/compliance/quarantineGuard.py` (called after Tier-1 + Tier-2 authorization), the one audit-row builder `build_audit_item` (sets the constant `allListPartition` that `AuditByDateGSI` is partitioned on; every audit `put_item` goes through it, guard-tested) in `common/compliance/auditRecord.py`, models in `models/compliance.py`. A failed pipeline rule records its execution's failure reason (`executionFailureReason`, and `quarantineExecutionFailures` on the asset-state row) beside the generic message; every compliance read that returns one passes it through `common/compliance/executionFailureAccess.py`, which keeps it only for a caller the execution details route would serve — that route's Tier-1 check plus the shared execution read rule in `common/workflows/executionAccess.py`, which the execution routes also decide through. Casbin object types `complianceSchema` (`databaseId` = the schema's scope or `GLOBAL`, `complianceSchemaName`), `complianceEvaluation` (`databaseId`, `complianceState`), `complianceCascade` (`databaseId` = the parent asset's database, `cascadeId`), `complianceBinding` (`databaseId`); every check passes `databaseId`. Tests in `tests/handlers/compliance/`
-   `config/` — System configuration
-   `databases/` — Database CRUD
-   `indexing/` — OpenSearch indexing (DynamoDB/S3 streams)
-   `metadata/` — Metadata CRUD
-   `metadataschema/` — Metadata schema management
-   `pipelines/` — Pipeline management (Pydantic models; Lambda/SQS/EventBridge/DeadlineCloud execution types — `PIPELINE_EXECUTION_TYPES` in `models/pipelines.py`. DeadlineCloud is async-only with a mandatory task-token callback: `waitForCallback` must be `Enabled` or create returns 400, and it is gated by `app.pipelines.deadlineCloudExecutionTypeEnabled` in the commercial `aws` partition only)
-   `roles/` — Role CRUD
-   `search/` — OpenSearch search handlers
-   `sendEmail/` — Email notification Lambda
-   `subscription/` — Asset subscription management
-   `tags/` — Tag CRUD
-   `tagTypes/` — Tag type management
-   `userRoles/` — User-role assignment
-   `workflows/` — Step Functions workflow management (Pydantic models, builder pattern for ASL generation: Lambda/SQS/EventBridge/DeadlineCloud task states; `sfn/deadlineCloudJobCallback` resolves Deadline Cloud job task tokens from default-bus events)
-   `addon/` — Add-on integrations (`garnetFramework/` Garnet NGSI-LD indexer Lambdas; `physna/` Physna Sync Lambdas: physnaFileSync, physnaAssetSync, physnaViewer; physnaCommon.py holds shared client/auth helpers; `edh/` EDH Workstations add-on Lambdas, shared modules in `edh/common/`, steered by `backend/backend/handlers/addon/edh/CLAUDE.md` and `.kiro/steering/ADDON_EDH_WORKSTATIONS.md`)
-   `custom/` — Fork extension point: handlers a fork adds (`handlers.custom.<name>.lambda_handler`); shipped empty

#### **Fork Extension Points (backend + CDK)**

An add-on is optional, upstream-maintained code enabled by configuration: it either integrates VAMS with an external solution deployed and run separately (Garnet Framework, Physna), supplying the VAMS-side components, or is a stand-alone non-core component that VAMS deploys in full when enabled (the licensed VNTANA and VEERUM viewers). Custom is fork code, which upstream ships empty. Upstream functionality goes in the add-on lane when it integrates an external solution, depends on an AWS service outside the tier-1 set, or implements a use-case lens for an industry; viewers and pipelines (with the pipeline system that runs them) keep their own lanes, and a fork uses the custom lane. The tier-1 AWS services are Amazon S3, DynamoDB, API Gateway, Lambda, IAM, Cognito, CloudFront, Application Load Balancer, Route 53, Amazon VPC and its endpoints, CloudFormation, AWS KMS, Step Functions, EventBridge, SQS, SNS, Secrets Manager, Systems Manager Parameter Store, CloudWatch, CloudTrail, AWS WAF and Certificate Manager. Search (Amazon OpenSearch Service) and the maps (Amazon Location Service) are core today and move to the add-on lane later, and the AWS Deadline Cloud pipeline execution type (`app.pipelines.deadlineCloudExecutionTypeEnabled`) stays core as part of the optional pipeline system; no new feature that needs one of those services joins them in core. Every add-on has an on/off toggle in configuration and is off by default. Definition, exemptions and current add-ons: `documentation/docusaurus-site/docs/developer/addons-overview.md`.

Every list VAMS aggregates is `core + add-on + custom`. The custom part is fork-owned: upstream ships it empty and never edits it, so a fork adds net-new endpoints and resources without conflicting on upgrade. Each such file opens with the "Fork extension point" header. Full walkthrough: `documentation/docusaurus-site/docs/developer/customizing-vams.md`.

| Layer                                       | Add-on file (upstream-owned)                                                                              | Custom file (fork-owned, shipped empty)                                                                     |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| API routes                                  | `backend/backend/common/addonApiRoutes.py` (`ADDON_ROUTES`)                                               | `backend/backend/custom/customApiRoutes.py` (`CUSTOM_ROUTES`)                                               |
| Audit actions                               | `backend/backend/common/addonAuditActions.py` (`ADDON_AUDIT_ROUTE_ACTIONS`)                               | `backend/backend/custom/customAuditActions.py` (`CUSTOM_AUDIT_ROUTE_ACTIONS`)                               |
| Handlers                                    | `backend/backend/handlers/addon/`                                                                         | `backend/backend/handlers/custom/`                                                                          |
| Models                                      | `backend/backend/models/addon/`                                                                           | `backend/backend/models/custom/`                                                                            |
| Object types, constraint fields, deny lists | `backend/backend/common/addonConstants.py`                                                                | `backend/backend/custom/customConstants.py`, merged by `common/constants.py`                                |
| Log redaction keys                          | `backend/backend/common/addonConstants.py` (`ADDON_LOG_SENSITIVE_KEYS`)                                   | `backend/backend/custom/customConstants.py` (`CUSTOM_LOG_SENSITIVE_KEYS`), merged by `common/constants.py`  |
| Resource names                              | `backend/backend/common/addonResourceNames.py` ↔ `infra/common/addonResourceParamKeys.ts`                 | `backend/backend/custom/customResourceNames.py` ↔ `infra/common/customResourceParamKeys.ts`                 |
| CDK resources                               | `infra/lib/nestedStacks/addon/addonBuilder-nestedStack.ts`                                                | `infra/lib/nestedStacks/custom/customResources.ts` (built inside the `CustomBuilder` stack)                 |
| Feature switches                            | `infra/common/addonAppFeatures.ts`, pushed by `AddonBuilderNestedStack.enabledFeatures`                   | `infra/common/customAppFeatures.ts`, returned by `buildCustomResources()`                                   |
| CSP / default routes                        | `addon/addonCsp.ts`, `addon/addonDefaultRoutes.ts`                                                        | CSP: the existing `infra/config/csp/cspAdditionalConfig.json`                                               |
| Default constraints, VPC endpoints          | `addon/addonDefaultConstraints.ts`, `addon/addonVpcEndpoints.ts`                                          | no lane: a fork type gets no seeded constraint, and a fork builds its own resources in `customResources.ts` |
| WAF rule exclusions                         | `addon/addonWafExclusions.ts`, passed by `buildWafStacks()` (`infra/lib/cf-waf-stack.ts`)                 | none; a fork edits `infra/config/policy/wafPolicyConfig.json`                                               |
| Fork settings                               | `app.addons.*` in `config.json`, declared through `infra/config/addon/addonConfig.ts` (`ConfigAddonLane`) | `infra/config/custom/customConfig.json`, read with `loadCustomConfig()`                                     |

An add-on route (`ApiRoute.addon_feature`) and an add-on object type (`ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES`) name the feature switch they are deployed under (`backend/backend/common/addonFeatures.py`, mirroring `ADDON_APP_FEATURES`). The core stack sets `ADDON_FEATURES_ENABLED` (the enabled add-on switches) on the routes, constraints and constraint-template Lambdas, so a disabled add-on's routes and object types are not listed or probed, and a new constraint or template naming its types is refused; stored ones still read. The web route table filters add-on routes by their `featuresEnabledRestriction`.

Custom routes go through `attachFunctionToApi` with the shared `RouteRegistry` (so they get the `VamsAuthorizer` scheme) and cannot be anonymous: `CustomBuilderNestedStack` fails synth on an `allowAnonymous` custom route and on an earlier route made anonymous, and `RouteRegistry` stores frozen descriptors. It also fails synth when `buildCustomResources()` returns a core `VAMS_APP_FEATURES` or add-on `ADDON_APP_FEATURES` value. `publishCustomResourceName()` (`custom/customStackHelpers.ts`) and `publishAddonResourceName()` (`addon/addonStackHelpers.ts`) fail synth on a key equal to a core key or one the other lane declares; the parameters hold names only, never a secret. The backend constants lanes are merged by `common/constants.py` at import and fail closed on a broken rule (names `^[A-Za-z][A-Za-z0-9]{1,63}$`, no reserved key, no field named as a Python keyword or an `EVALUATOR_RESERVED_ATTRIBUTES` name, no redefinition, fields that exist, samples `""`/`[]` with list-valued fields taking only `is_one_of` / `is_not_one_of`, append-only lower-case deny entries). Type and field names are compared ignoring letter case when checking for a repeat, so `Asset` repeats `asset` and `DatabaseId` repeats `databaseId`; authorization still matches a name exactly. The log-key lists follow the same shape: `CORE_LOG_SENSITIVE_KEYS` (the structured logger) and `CORE_LOG_TEXT_SENSITIVE_KEYS` (the free-text redactor) become `LOG_SENSITIVE_KEYS` and `LOG_TEXT_SENSITIVE_KEYS`, both extended by the one lane list `ADDON_LOG_SENSITIVE_KEYS` / `CUSTOM_LOG_SENSITIVE_KEYS`, append-only; a log key matches `^[A-Za-z][A-Za-z0-9_-]{1,63}$` and repeats no core or other-lane key in any letter case, because both redactors match keys case-insensitively. An extension object type gets no seeded constraint, so even admin is denied on it until the fork adds one, and the `dynamodb-authdefaults-*` constructs are never extended for it. The one exception is an add-on, which may seed constraints for its own object types while it is enabled, through `infra/lib/nestedStacks/addon/addonDefaultConstraints.ts`; the constructs append those after their core items, so the index-based ids of the core custom resources do not move. Naming conventions (`/custom/` routes, `custom/` keys, `CUSTOM_*` switches) are recommended, not enforced. `customConfigCommon/` is the separate place for overriding **existing** behaviour (authorizer MFA check, login profile).

#### **Workflow Execution Storage**

Workflow executions are workflow-keyed: the `executionId` is a VAMS GUID passed as the Step Functions execution name, so `$$.Execution.Name == executionId`. Asset/database linkage is not on the main row — it lives in `WorkflowExecutionInputsStorageTable`, queried via the `WorkflowExecInputsByAssetGSI` GSI for the asset-scoped execution listing. `executeWorkflow` writes the V2 main execution row plus the workflow inputs/configuration rows, one `PipelineExecutions` row per pipeline in the workflow, and the first-pipeline input rows (files/metadata/configuration). `processWorkflowExecutionOutput` writes the end-state pipeline's output/metadata/log rows and the completion status back to the main row. The pure record-building logic (key construction, S3 prefix derivation, record-dict builders, text truncation) lives in `common/workflows/executionRecords.py` and is unit-tested in isolation. The execution details/logs read side keeps its pure logic in the same package: `common/workflows/subExecutionStages.py` folds a sub-state-machine's ASL definition and execution history into per-stage status, and `common/workflows/availableLogs.py` identifies a pipeline execution's log sources by location (`logId`), plans each read and classifies its outcome; `executionService.py` holds only the memoised `DescribeStateMachine`, `DescribeExecution`/`DescribeJobs`/SageMaker `Describe*`, paged `GetExecutionHistory` and `FilterLogEvents` calls around them. The sub-process stop helper and `ABORTABLE_RESOURCE_TYPES` (Step Functions executions, Batch jobs, Deadline Cloud jobs, SageMaker training jobs and pipeline executions) live in `common/workflows/executionOutputs.py` and are shared by the abort API and the failure path (`sfn/handleExecutionError.py`); a registered type outside the set is displayed with `abortable: false` and listed under `notAbortable` on the abort response rather than stopped.

A workflow's `perAsset`, `perInputFile` and `perInputFileVersion` concurrency restrictions are lock rows in `WorkflowExecutionLocksStorageTable` (`common/workflows/executionLocks.py`; mirrors `backend/CLAUDE.md` "Workflow Execution Locks"). `executeWorkflow` alone acquires them: conditional `PutItem`s in sorted key order, all or none, immediately before the launch's first side effect, answering `400` on a conflict and taking over a row whose holder has no main execution row once it is older than `EXECUTE_WORKFLOW_TIMEOUT_SECONDS` plus `LOCK_TAKEOVER_MARGIN_SECONDS`. The end-state step, the error handler, the abort route and the executions read path release them through `release_locks_for_execution` (the keys rebuilt from the input rows plus those the `WorkflowExecutionLocksByHolderGSI` holder index lists, each delete conditional on the holder); a launch that fails after acquiring releases from memory. The abort and a failed launch release only when `StopExecution` succeeded or `sfn_execution_has_ended` shows the execution ended. Each lock handler's grants sit in its own `ExecutionLockPolicy` (`attachExecutionLockPolicy`), never its role's default policy. Skip records (mirrors `backend/CLAUDE.md` "Workflow Execution Locks"): A conflict on a `fileUpload` trigger launch (`triggerType` File-Upload, executing user `SYSTEM_USER`) also writes a `SKIPPED` execution (`executeWorkflow._record_skipped_launch`), best-effort and with the same `400`; a manual or API launch writes nothing. The id is `er.skip_record_execution_id(lockKey, holder)` (uuid5), so every refusal by one holder on one key lands on one record. Rows are written in this order, so the main row (which lists and details read) exists only once its authorization rows do: (1) the configuration row (`build_workflow_configuration_record` with the trigger's output target and `pipelineExecutionParameters`, the trigger's template parameters a re-run replays), a put conditional on `attribute_not_exists`, whose existing item supplies the first-skip date to later refusals; (2) the workflow-input rows (`er.build_workflow_execution_input_record`, never pinned, dated with that first-skip date) while the main row's `skippedLaunchCount` is below `MAX_SKIP_RECORD_INPUT_ROWS` (100); (3) the main row, `er.build_skip_record_update`: one `UpdateItem` that sets the identity fields `if_not_exists`, overwrites `lastSkippedDate` and `ADD`s one to `skippedLaunchCount`. A failure in (1) or (2) writes no main row. No pipeline or lock row is written, and a conflict with no known holder records nothing. `SKIPPED` is in both `TERMINAL_STATUSES` tuples, so no reconcile or terminal writer rewrites it. Reads: the lists add `er.skip_list_fields` (count, scope, reason; never the holder) to SKIPPED rows only; the details view adds `skip` (`skip_details_block`), whose `blockingExecutionId` is set only when `_caller_may_read_execution` passes the same Tier-2 GET check on the holder (missing and denied look the same); the logs route answers `SKIPPED_EXECUTION_LOG_MESSAGE` without reading CloudWatch; a single abort answers `400` `SKIPPED_EXECUTION_ABORT_MESSAGE` after authorization and writes nothing, and a group abort reports the member as `skipped-terminal`; re-run uses the configuration row's `pipelineExecutionParameters` when the record has no pipeline rows. Both list routes take `excludeStatus` (`models.executions.parse_list_status_filter`: known statuses only, no conflict with `status`, fixed 400s): the global list adds it to the FilterExpression (no signal beyond the `status` filter); the asset list drops a candidate only after it has passed the per-execution read check (`_authorize_candidate`, memoized and shared with the authorization pass), so a candidate the caller may not read is handled exactly as without the parameter; at most `MAX_EXECUTIONS_INSPECTED` drops per page (a spent budget stops with a cursor and a warning), re-applied after reconcile. The execute role's main-table `GetItem`/`UpdateItem` and configuration/input `PutItem` cover the writer (`test_execute_workflow_dynamodb_calls.py`).

#### **Workflow State Machine Deployment**

A workflow's Step Functions definition encodes each referenced pipeline's execution settings, so it is rebuilt whenever one changes (mirrors `backend/CLAUDE.md` "Workflow State Machine Deployment" and "Internal Actions"):

-   **One deploy routine.** Create, update, the pipeline-change refresh and the launch-time check all deploy through `workflowService._deploy_fields`, which writes `workflow_arn`, `workflowVersionArn` (every deploy publishes a version; older ones past `MAX_RETAINED_STATE_MACHINE_VERSIONS` are deleted), `jobNames` (reused while the steps are unchanged), `pipelineFingerprints` (sha256 of `workflowAsl.to_asl_pipeline_dict` minus `name`, keyed `pipelineDatabaseId:pipelineId`) and `deploymentRevision` together. The refresh writes conditionally on the revision it read.
-   **Everything the ASL builders read from a pipeline record goes through `to_asl_pipeline_dict`**, because that dict is what is fingerprinted.
-   **A pipeline change refreshes its workflows.** `pipelineService.update_pipeline` invokes the workflow service's `refreshPipelineReferences` internal action asynchronously when the fingerprint changes; the service walks every non-archived referencing workflow to exhaustion and records a failed redeploy as `deploymentStale` + `deploymentStaleReason`.
-   **The redeploy path reads strongly consistent.** `workflowService.redeploy_workflow` (behind both internal actions) reads the workflow and each pipeline with `ConsistentRead=True` via the `consistent_read` parameter of `get_workflow_item`, `_get_pipeline_record` and `_resolve_snapshot_pipeline_records`: the refresh runs right after the pipeline write, and the re-read after a lost conditional write must see the other writer's deployment. API reads keep the default.
-   **A launch never runs a stale definition.** `executeWorkflow._ensure_current_deployment` compares stored and live fingerprints, invokes `redeployWorkflow` synchronously on a mismatch, starts on `workflowVersionArn`, and returns `409` without starting when the deployment cannot be made current.
-   **Internal actions are not API-reachable.** They dispatch on a top-level `vamsInternalAction` key only for a `SYSTEM_USER` `lambdaCrossCall` with no `requestContext`; callers hold `lambda:InvokeFunction` on the workflow service ARN only (`grantWorkflowServiceInternalInvoke`).
-   **System records rest on the same envelope.** `common/workflows/systemRecords.is_schema_import_call` is true only for a `lambdaCrossCall` with `userName` `SYSTEM_USER` **and** `source` `vamsSchemaImport`, which only `importGlobalPipelineWorkflow` sends; that caller alone may set `isSystem` (a JSON boolean, read from the raw body) or change a system record. `pipelineService`, `pipelineTemplateService`, `workflowService` and `workflowTriggerService` run the guard after Tier-2 and before any write, refuse with a `400` naming the field or action, and refuse a mixed allowed-and-refused request as a whole. A new `lambda:InvokeFunction` grant on any of the four, or on the import function, widens who can register or change system records.

## 📋 **Development Workflow Checklist**

### **Phase 1: Pre-Implementation**

-   [ ] **Analyze Requirements**: Understand the new API/feature requirements
-   [ ] **Review Gold Standard**: Study `assetService.py` for implementation patterns
-   [ ] **Plan API Design**: Design request/response models and endpoints
-   [ ] **Plan CDK Changes**: Identify required infrastructure changes
-   [ ] **Plan Authorization**: Determine permission requirements and object types
-   [ ] **Plan Frontend Integration**: Identify frontend service changes needed
-   [ ] **Plan CLI Integration**: Identify CLI command changes needed
-   [ ] **Plan Documentation**: Identify documentation updates required

### **Phase 2: Implementation**

#### **Step 1: Backend Models (Pydantic)**

-   [ ] **Create Request Models**: Add Pydantic models in `models/[domain].py`
-   [ ] **Create Response Models**: Add response models with proper typing
-   [ ] **Add Validation Logic**: Include `@root_validator` for complex validation
-   [ ] **Follow Gold Standard**: Use `assetsV3.py` patterns for validation
-   [ ] **Import in Models**: Add new models to appropriate `__init__.py`

#### **Step 2: Backend Handler Implementation**

-   [ ] **Create Handler File**: Add handler in `handlers/[domain]/[handler].py`
-   [ ] **Follow Gold Standard**: Use `assetService.py` patterns for structure
-   [ ] **Normalize the REST event**: Call `request_to_claims(event)` as the first event access (it normalizes internally). Only if the handler reads `requestContext['http']` _before_ claims, `import normalize_event` from `common.auth.apiEvent` and call it as the first statement of `lambda_handler` (see Rule 1)
-   [ ] **Implement Error Handling**: Use comprehensive try/catch with proper exceptions
-   [ ] **Add Authorization**: Include Casbin enforcement with object-type checking
-   [ ] **Add Logging**: Use `safeLogger` for structured logging. It redacts credential keys (`authorization`, `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`, `apiKey`, `apiKeySecret`, `rawKey`, the credential headers `x-vams-origin-verify`, `cookie`, `set-cookie`, `x-api-key`, `proxy-authorization`, `x-amz-security-token`, plus the keys the add-on and fork constants lanes register in `ADDON_LOG_SENSITIVE_KEYS` and `CUSTOM_LOG_SENSITIVE_KEYS`) and caller-authored content keys (`configBody`, `templateTags`, `tagValues`, `customTemplateOverride`, `webFormJson`, `inputInstructions`), at every nesting level in dicts, lists, and tuples, and inside a request `body` that arrives as a JSON string. `common/logRedaction.py`, which redacts the free-text log lines returned to callers, reads `LOG_TEXT_SENSITIVE_KEYS`: its own core keys plus the same lane keys. Redaction is key-driven, so an f-string interpolating a payload value bypasses it — log identifiers and counts, never rendered template bodies or tag values
-   [ ] **Resolve Resource Names**: Use `get_table_name(ResourceKeys.*)`, `get_bucket_name(ResourceKeys.*)` from `common.resourceNames` at module level in try/except
-   [ ] **Add AWS Clients**: Configure AWS clients with retry configuration
-   [ ] **Implement Business Logic**: Separate business logic from request handling
-   [ ] **Add Response Enhancement**: Include version info and bucket details where applicable

#### **Step 3: CDK Infrastructure**

-   [ ] **Update Storage Resources**: Add new DynamoDB tables/S3 buckets in `storageBuilder-nestedStack.ts`
-   [ ] **Register Resource Names**: Add constants to `infra/common/resourceParamKeys.ts`, `backend/backend/common/resourceNames.py`, AND `infra/deploymentDataMigration/tools/ssm_resource_lookup.py` (data-migration scripts resolve table/log-group names from these SSM parameters); register descriptor in `resourceNameRegistry`. An add-on table is created in the add-on's own nested stack with `addonDynamoDbTableDefaults()` (`infra/lib/nestedStacks/addon/addonStackHelpers.ts`), and its key goes in the add-on lane instead: `infra/common/addonResourceParamKeys.ts`, mirrored by `AddonResourceKeys` in `backend/backend/common/addonResourceNames.py` and published by the add-on stack with `publishAddonResourceName()`; `ssm_resource_lookup.py` carries no add-on key
-   [ ] **Create Lambda Builder**: Add lambda function builder in `lambdaBuilder/[domain]Functions.ts`
-   [ ] **Configure Environment Variables**: Add handler-specific env vars only (resource names resolved from SSM via `globalLambdaEnvironmentsAndPermissions`)
-   [ ] **Configure Permissions**: Grant appropriate DynamoDB/S3/SNS permissions
-   [ ] **Configure VPC**: Add VPC/subnet configuration based on config flags
-   [ ] **Add KMS Permissions**: Include KMS key permissions for encryption
-   [ ] **Add API Routes**: Register routes in `apiBuilder2-nestedStack.ts` (preferred; the two API stacks stay split so each keeps its own CloudFormation per-template budget — see Rule 6)
-   [ ] **Follow Naming Conventions**: Use consistent naming patterns

#### **Step 4: API Gateway Integration**

-   [ ] **Add Route Definitions**: Use `attachFunctionToApi` for route registration
-   [ ] **Configure HTTP Methods**: Set appropriate HTTP methods for each endpoint
-   [ ] **Add Security**: Confirm the route resolves through the custom VAMS Lambda authorizer (never a built-in CDK authorizer); set `allowAnonymous` only for a deliberately unauthenticated path
-   [ ] **Test Route Paths**: Verify route paths match API documentation

### **Phase 3: Quality Assurance**

#### **Step 5: Testing**

-   [ ] **Write Unit Tests**: Create tests in `tests/handlers/[domain]/`
-   [ ] **Test Success Cases**: Test normal operation flows
-   [ ] **Test Error Cases**: Test all error scenarios and exception handling
-   [ ] **Test Authorization**: Test Casbin enforcement scenarios
-   [ ] **Test Validation**: Test Pydantic model validation
-   [ ] **Mock AWS Services**: Use proper mocking for DynamoDB, S3, SNS
-   [ ] **Run All Tests**: Ensure `pytest` passes with coverage

The add-on and custom lane modules (`common/addonConstants.py`, `common/addonResourceNames.py`, `custom/`) are pure data and need no mock under `tests/mocks/`. `common` is a `MagicMock` in `tests/conftest.py`, so the conftests register `common.addonConstants` in `sys.modules` by path before `constants.py` loads, as they do for `common.apiRouteTypes` and `common.addonApiRoutes`; `tests/conftest.py` also registers `common.addonFeatures`, which both route modules import. `common/addonFeatures.py` reads `ADDON_FEATURES_ENABLED` on every call, and with the variable unset the route and object-type listings include every add-on entry, so a test of a listing that leaves a disabled add-on out sets or deletes the variable with `monkeypatch` (`tests/handlers/auth/test_addon_listings_follow_deployment.py`). The `handlers.auth` mock carries `AUTH_METHOD_JWT` and `AUTH_METHOD_API_KEY`, pinned to the real module's values by `tests/handlers/auth/test_request_to_claims_authMethod.py`. A test that needs populated lanes loads a private copy of `constants.py` with stand-in lane modules (`tests/common/test_constants_extension_lanes.py`). Tests that pin the constraint lists pin the `CORE_*` lists, so they hold in a fork that fills the lanes. `customLogging/logger.py` and `common/logRedaction.py` import their merged key lists from `common.constants`, so `tests/conftest.py` also loads the real `constants.py` once by path and pins `LOG_SENSITIVE_KEYS` and `LOG_TEXT_SENSITIVE_KEYS` on the mock `common.constants`; a redactor imported under the mock then redacts the real keys.

An add-on's tests sit under `tests/handlers/addon/<add-on>/`, and its own test-suite rules (harness modules, captured fixtures) live in that add-on's `CLAUDE.md` and Kiro `ADDON_*.md` file. An add-on that ships routes records, per shipped permission template, the methods each template allows on them as `ROUTE_GRANTS` in `tests/handlers/addon/<add-on>/addonRouteGrants.py`: `tests/handlers/auth/test_permission_templates_addon_routes.py` merges every such file, refuses a route two sources decide, and evaluates each decision through the real policy generation and Casbin evaluation.

#### **Step 6: Frontend Integration**

-   [ ] **Update API Service**: Add methods to `web/src/services/APIService.ts` (or update existing API Paths that may not be always in this file)
-   [ ] **Follow Frontend Patterns**: Use boolean/message return patterns
-   [ ] **Handle Response Formats**: Support both legacy and new response formats
-   [ ] **Add Error Handling**: Include proper error message extraction
-   [ ] **Test Frontend Integration**: Verify frontend can consume new APIs

#### **Step 7: CLI Integration**

-   [ ] **Update API Client**: Add methods to `tools/VamsCLI/vamscli/utils/api_client.py`
-   [ ] **Add Constants**: Add API endpoints to `constants.py`
-   [ ] **Add Exceptions**: Create specific exceptions for new error scenarios
-   [ ] **Add Commands**: Create CLI commands if needed
-   [ ] **Test CLI Integration**: Verify CLI can consume new APIs

#### **Step 8: Documentation Updates**

-   [ ] **Update API docs in BOTH places**: API documentation lives in two independent sources that must be kept in sync — (1) the OpenAPI spec `documentation/VAMS_API.yaml` (paths + component schemas), and (2) the Docusaurus reference page `documentation/docusaurus-site/docs/api/{domain}.md` (e.g. `api/auth.md` for `/auth/*`). Add/rename/change the endpoint in **both**; updating only one leaves the docs inconsistent.
-   [ ] **Update Docusaurus developer docs (`documentation/docusaurus-site/docs/developer/`)**: Add architecture and usage information
-   [ ] **Update Docusaurus permissions docs (`documentation/docusaurus-site/docs/concepts/permissions-model.md`)**: Add authorization mappings for new endpoints
-   [ ] **Update README**: Update overview if major features added
-   [ ] **Add Code Examples**: Include usage examples in documentation

#### **Step 9: Code Quality**

-   [ ] **Run Black**: Format code with `black backend/`
-   [ ] **Run MyPy**: Type check backend code
-   [ ] **Run Flake8**: Lint backend code
-   [ ] **Check CDK Lint**: Run CDK linting on infrastructure code
-   [ ] **Review Error Messages**: Ensure user-friendly error messages
-   [ ] **Review Logging**: Ensure proper structured logging

## 🚨 **Mandatory Rules**

### **Rule 1: Follow Gold Standard Implementation (assetService.py)**

### **Rule 2: Page S3 and DynamoDB listings to exhaustion when the full set is needed**

S3 `list_object_versions` / `list_objects_v2` and DynamoDB queries cap a single
call (`MaxKeys`, `Limit`, one page). When the result must be complete, page to
exhaustion — a bare `list_object_versions(..., MaxKeys=N)` silently drops versions
beyond `N`, producing wrong archive status and truncated history. For S3
versions/objects use the shared helpers `common.s3.list_all_object_versions()` /
`list_all_objects()` (page-size constants `S3_VERSIONS_PAGE_SIZE` /
`S3_OBJECTS_PAGE_SIZE`; both accept an optional `max_keys` / `max_objects` cap for
best-effort sampling). Existence-only checks (`MaxKeys=1`) are the allowed
exception.

To check whether a single key or a specific `versionId` is archived, do **not**
list versions — use `common.s3.is_object_version_archived()`, which issues one
`HeadObject` (405 MethodNotAllowed = delete marker, 200 = live, 404 = missing) and
is O(1) regardless of version count. Handler-local `is_file_archived` helpers must
delegate to it.

**A DynamoDB `FilterExpression` is applied AFTER the page is read, so a single filtered
call is not a lookup and not an existence check.** DynamoDB reads up to 1 MB (or `Limit`
items) and only then discards the non-matching ones. Empty `Items` alongside a present
`LastEvaluatedKey` is the normal shape for "the match is on a later page" — it does not
mean "no such item". Three consequences, each of which has occurred in this codebase:

-   `len(response['Items']) > 0` as an existence test is a **false negative** once the
    table outgrows one page. The caller then accepts a duplicate, accepts a link that
    closes a cycle, or treats a node with children as a leaf.
-   A filtered `scan` used to fetch **one** row by a non-key attribute returns `None` for a
    row that exists. Query a GSI on that attribute instead of scanning.
-   `Limit=N` bounds items **evaluated**, not items returned, so a narrow filter over a wide
    window legitimately returns an empty page plus a `NextToken`.

This is the one place the S3 exception above does not carry over: S3 applies `Prefix`
server-side before `MaxKeys`, so `MaxKeys=1` genuinely answers "does this exist".

What a `Prefix` + `MaxKeys=1` listing answers is "does anything under this prefix exist" —
not "does this exact key exist". `Prefix` is a prefix match, so the listing also returns
longer keys (`file.glb` matches `file.glb.previewFile.png`). Counting the returned entries
therefore attributes a sibling's state to the requested key; match each entry's `Key` back
to the key being asked about.

When the **handler** needs every row (cascade delete, cycle check, existence test,
descendant walk), reach for the shared helpers in `common.dynamodb` first:
`query_all_items(table, **query_kwargs)` returns every matching item and
`query_has_match(table, **query_kwargs)` answers an existence check, stopping at the first page
that yields one. Hand-roll the loop only for a `scan`, or where the walk needs its own bound —
loop on `LastEvaluatedKey`:

```python
from boto3.dynamodb.conditions import Key

rows = []
query_kwargs = {'KeyConditionExpression': Key('databaseId').eq(database_id)}
while True:
    response = table.query(**query_kwargs)
    rows.extend(response.get('Items', []))
    if 'LastEvaluatedKey' not in response:       # presence, not truthiness
        break
    query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
```

Two details decide whether that loop is correct:

-   **Test key presence, not truthiness.** DynamoDB omits `LastEvaluatedKey` when the walk
    is complete and never returns a falsy one, so both forms agree in production but not
    under test: `response.get('LastEvaluatedKey')` against a bare `MagicMock` is truthy on
    every iteration and the loop never terminates, while `in` resolves `False` and exits.
-   **Assign any "drained"/"complete" flag on every exit path.** A flag set only on the
    `LastEvaluatedKey`-absent branch reports the opposite of the truth whenever an early
    `break` (a page cap, a found-it short-circuit) leaves the loop first.
-   **An existence check fails the OPPOSITE way under a stub, so converting one to
    `query_has_match` inverts what an unpatched table does to its test.** `MagicMock.__len__`
    defaults to `0` while `__bool__` defaults to `True`, so
    `len(response.get('Items', [])) > 0` answers `False` against an under-stubbed reader
    (failing safe), whereas `query_has_match` — which returns on the first non-empty page
    via `if response.get('Items')` — answers `True` after one read. A test asserting
    `assert has_children is True` then **passes having read nothing**, retiring the
    assertion rather than failing inconclusively. Defend with both: assert a read count or
    `Pager.assert_paged_to_exhaustion()` in-band on your own double, and patch the object
    the function's `__globals__` actually resolves (`patch.object` is effective only when
    `function.__globals__ is module.__dict__`; one source file loaded twice yields two
    module objects sharing a `__file__` but owning separate globals).

**Batch reads.** Resolving many rows by full primary key (the assets a page of executions
references, the pipelines a page of workflows references, the assets in a link tree, the
current-version rows of a page of listed assets) goes through
`common.dynamodb.batch_get_items(dynamodb, table_name, keys, *, max_retries=3,
backoff_seconds=0.05)`, never a hand-rolled `batch_get_item` loop. It chunks at the 100-key
per-call limit, deduplicates keys (DynamoDB rejects a chunk carrying a duplicate), and
re-requests `UnprocessedKeys` with exponential backoff — the shape a partial throttle or a
16 MB response takes, which arrives as HTTP 200 and so sits outside the botocore retry config.

It returns `(rows, unresolved_keys)`. A key absent from both is an item that does not exist;
a key in `unresolved_keys` is one the read did not complete within the budget. Keep the two
apart in the caller — a throttled read must not be reported as a deleted asset or, after
authorization, as a denial — and decide what to do with the leftover: read it per-item
(`executionService`, `assetExportService`, `assetService` listings), report it as unresolved
(`assetLinksService`), or log and degrade with `max_retries=0` where the data is a display-only
hint (`workflowService`). A `ClientError` propagates; the caller owns its fallback.

**Targeted updates.** `common.dynamodb.to_update_expr(record)` returns the names map, values map and
`SET` expression for an `update_item`. The helper names its placeholders `#u<i>` / `:u<i>`, so a boto3
condition object (`ConditionExpression=Attr("rev").eq(n)`) is safe in the same call: boto3 renders it with
`#n<i>` / `:v<i>` and merges those into the maps, replacing any entry of the same name. A hand-built update
expression combined with a condition object must avoid `#n<i>` / `:v<i>` for the same reason.

### **Rule 3: Paginate large GET responses; never return an unbounded in-memory set**

A response that can exceed the AWS Lambda synchronous response limit (6 MB) must
page externally: accept `maxItems`/`pageSize`/`startingToken` and return
`NextToken`, defaulting the sizes to named constants (mirror the asset-listing and
metadata-listing handlers). Do not use DynamoDB `paginator.build_full_result()` to
accumulate every record for a user-facing GET. When ordering or enrichment requires
the full set first (e.g. metadata schema injection/ordering), enrich the full set,
then offset-slice to the page. Limits that bound response size or protect Lambda
runtime (e.g. `MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST`, worker-pool caps) stay as named
constants with a rationale comment — keep them. A count cap does not bound a response
whose items vary in size: upload initialization (`uploadFile.py` `initialize_upload`)
also estimates its presigned-URL response from each file's part count and key
(`MAX_UPLOAD_INITIALIZE_RESPONSE_BYTES` and the overhead constants in `models/assetsV3.py`)
and refuses an over-budget request with a generic `400` before it creates any multipart
upload. CLI and web clients that consume a paginated GET must follow `NextToken` to
retrieve the complete set.

**The token must round-trip, and nothing checks that for you.** The value the handler emits
has to be something the request model accepts back, on the parameter the handler actually
reads. Emitting the raw `LastEvaluatedKey` **dict** as `NextToken` while the request model
declares `startingToken: Optional[str]` yields a token no client can return: page one looks
correct, every later page is unreachable, and no error is raised anywhere. Base64-encode the
key so it is a string and opaque (clients then cannot depend on its interior), and pin it
with a test that takes the token from page one, feeds it back, and asserts page two begins
where page one stopped — asserting only that `NextToken` is present passes on a token that
cannot be used.

### **Rule 4: Keep handlers portable across AWS partitions**

Handlers run in `aws`, `aws-us-gov`, `aws-eusc` (EU Sovereign, region
`eusc-de-east-1`), and potentially `aws-cn` / `aws-iso*`. A partition defect is
invisible in commercial tests and surfaces as a runtime 500 or a validation
rejection that reproduces **only** in the affected partition. There is deliberately
no central partition helper in the backend — these four rules are the contract.

1. **Never build an ARN from a hardcoded partition.** Either parameterize it
   (`common/workflows/stepfunctions_builder.py` threads a `partition` argument
   through every state-machine integration ARN, sourced from the `AWS_PARTITION`
   Lambda env var read in `common/workflows/workflowAsl.py`), or parse an ARN you
   already hold (`handlers/workflows/executionService.py` splits a resource ARN,
   falling back to the execution log-group ARN, to recover
   partition/region/account). Prefer parsing when an ARN is in hand — no env var,
   cannot drift. `AWS_PARTITION` is set on the `workflowService` Lambda only, so a
   handler needing it must have its CDK builder updated or derive it from an ARN.

2. **Never hardcode an endpoint hostname or DNS suffix.** Suffixes differ per
   partition — `amazonaws.com`, `amazonaws.com.cn`, **`amazonaws.eu`** (EU
   Sovereign), `c2s.ic.gov`, `sc2s.sgov.gov`, `cloud.adc-e.uk`, `csp.hci.ic.gov`.
   Construct plain boto3 clients with **no `endpoint_url`** and let the SDK resolve
   per region; read service endpoints (e.g. the OpenSearch host) from SSM. Region
   comes from `os.environ["AWS_REGION"]` — avoid a `"us-east-1"` default, which
   silently points at the wrong partition if the variable is ever missing.

    **Asset buckets may be in another Region, so every S3 call on an asset bucket
    goes through `common.s3`'s Region-routing client**, never a module-level
    `boto3.client('s3', ...)` of the handler's own: `s3 = region_routing_s3_client()`
    (or `region_routing_s3_resource()`) signs each call for the Region of the
    `Bucket` it names, resolved from the bucket details the handler read
    (`bucket_region_fields(row)` spreads `bucketRegion`/`bucketAccountId` into every
    bucket-details dict) or from the buckets table's `bucketNameGSI`.
    `copy_s3_object()` is the one copy path: a managed copy by the destination-Region
    client, or a streamed GET→PUT when `VAMS_LAMBDAS_IN_VPC` is `true` and the
    Regions differ, since an S3 interface endpoint does not serve cross-Region
    `CopyObject`. Ratchet: `backend/tests/handlers/databases/test_buckets_listing_region.py`;
    mock parity: `backend/tests/common/test_s3_region_routing.py`.

3. **New ARN/URL validators must accept every partition.** Compose them from
   `aws_partition_group` (`aws`, `-us-gov`, `-cn`, `-eusc`, `-iso[-x]`) and
   `aws_dns_suffix_group` in `common/validators.py`; never inline `arn:aws:`. A
   commercial-only pattern passes every commercial test and rejects legitimate
   input only in the affected partition — the hardest failure to attribute.
   `infra/lib/helper/const.ts` (`SERVICE_LOOKUP`) is the authoritative partition +
   suffix list; keep these groups in step with it.

4. **A service or model may not exist everywhere.** Bedrock model ids differ (both
   restricted config templates pin an older Sonnet), OpenSearch engine versions
   differ, and some services are absent. Take such values from configuration the
   CDK layer supplies per partition rather than hard-coding them.

The `GOVCLOUD` feature switch is present in `featuresEnabled` for **both** GovCloud
and EU Sovereign (both templates set `app.govCloud.enabled: true`), so treat it as
"restricted partition" rather than literally GovCloud. The CDK-side rules —
including the `AWS::Lambda::EventSourceMapping` tag restriction that fails a
GovCloud deploy outright — are in `.kiro/steering/CDK_DEVELOPMENT_WORKFLOW.md`.

## 🔐 **Security Guidelines for Exception Handling**

### **Critical Security Rule: Secure Exception Handling**

**ALL inner raises of custom exception types (not the final catch wrappers) MUST NOT contain:**

-   Input parameters that reveal system internals
-   `str(e)` exception information from caught exceptions
-   Function names, file paths, or AWS resource details
-   Database schema, table names, or configuration details
-   Lambda function names or internal service identifiers
-   S3 bucket names, keys, or path structures
-   Any information that could aid in system reconnaissance

### **Secure Exception Patterns**

#### **✅ SECURE Pattern - Inner Business Logic:**

```python
# Inner business logic exceptions - Generic messages only
if not bucket_name or not base_assets_prefix:
    raise VAMSGeneralErrorResponse("Database configuration invalid")

if not asset:
    raise VAMSGeneralErrorResponse("Resource not found")

# The empty-token guard is a separate statement before enforce(), never a wrapper
# around it — see "Two-Level Authorization" below.
if len(claims_and_roles["tokens"]) == 0:
    raise VAMSGeneralErrorResponse("Access denied")

if not casbin_enforcer.enforce(resource, "GET"):
    raise VAMSGeneralErrorResponse("Access denied")

# Validation failures - No input parameter details
if len(asset_id) < 3:
    raise VAMSGeneralErrorResponse("Invalid resource identifier")
```

#### **✅ SECURE Pattern - Final Catch Wrappers:**

```python
# Final catch wrappers - Log details internally, return generic messages
try:
    # Business logic here
    result = process_asset(asset_data)
    return result
except VAMSGeneralErrorResponse as e:
    # Re-raise VAMS exceptions as-is (already secure)
    raise e
except Exception as e:
    # Log full details for debugging (internal only)
    logger.exception(f"Error processing asset {asset_id}: {e}")
    # Return generic message to user
    raise VAMSGeneralErrorResponse("Error processing request")
```

#### **❌ INSECURE Patterns - DO NOT USE:**

```python
# ❌ NEVER expose internal exception details
raise VAMSGeneralErrorResponse(f"Error getting bucket details: {e}")

# ❌ NEVER expose input parameters in exceptions
raise ValueError(f"Database {database_id} does not exist")

# ❌ NEVER expose function or service names
raise Exception(f"Error invoking lambda function {function_name}: {e}")

# ❌ NEVER expose file paths or system details
raise Exception(f"Error getting database default bucket details: missing bucket_name")

# ❌ NEVER expose AWS resource details
raise VAMSGeneralErrorResponse(f"S3 bucket {bucket_name} access denied: {str(e)}")
```

### **Validation and Model Guidelines**

#### **Validator Integration**

-   **Validators Path**: `backend/backend/common/validators.py`
-   **Models Path**: `backend/backend/models/`
-   **Use existing validators** where a validation type exists (see available types below)
-   **Add new validator types** for repetitive validations instead of duplicating logic
-   **Request model verification** for complex business logic that cannot be handled by simple validators

#### **Available Validator Types** (from `backend/backend/common/validators.py`):

```python
# Identity and Reference Validators
'ID'                    # General IDs (3-63 chars, alphanumeric + hyphens/underscores)
'ASSET_ID'              # Asset identifiers (up to 256 chars)
'UUID'                  # Standard UUID format
'EMAIL'                 # Email address format
'USERID'                # User identifier format

# String Length Validators
'STRING_30'             # Max 30 characters
'STRING_256'            # Max 256 characters
'STRING_16384'          # Max 16384 characters (free-form caller text, e.g. commentBody)
'STRING_256_ARRAY'      # Array of strings, each max 256 chars

# File and Path Validators
'FILE_NAME'             # Valid filename format
'FILE_EXTENSION'        # File extension format (.ext)
'RELATIVE_FILE_PATH'    # Relative file path format
'ASSET_PATH'            # Asset path format (with isFolder option)
'ASSET_PATH_PIPELINE'   # Pipeline-specific asset paths
'ASSET_AUXILIARYPREVIEW_PATH'  # Auxiliary preview paths

# Object and Content Validators
'OBJECT_NAME'           # Object name format
'OBJECT_NAME_ARRAY'     # Array of object names
'STRING_JSON'           # Valid JSON string format
'REGEX'                 # Valid regex pattern
'NUMBER'                # Numeric value
'BOOL'                  # Boolean value

# Array Validators
'ID_ARRAY'              # Array of IDs
'UUID_ARRAY'            # Array of UUIDs
'EMAIL_ARRAY'           # Array of email addresses
'USERID_ARRAY'          # Array of user IDs

# Specialized Validators
'SAGEMAKER_NOTEBOOK_ID' # SageMaker notebook naming

# Sub-Process Registration Validators (partition-aware where they name a resource)
'ARN'                       # Any AWS resource ARN
'CLOUDWATCH_LOG_GROUP_ARN'  # Log-group ARN
'CLOUDWATCH_LOG_GROUP_NAME' # 1-512 chars, -_./# + alnum
'LOG_STREAM_NAME'           # 1-512 chars, no ':' or '*' (stream names and prefixes)
'SFN_STATE_NAME'            # 1-80 printable chars, no control characters (an ASL state name)
'DISPLAY_LABEL'             # 1-128 printable chars, no control characters
'LOG_SOURCE_TYPE'           # stateMachine | lambda | batch | ecs | container | custom (unknown -> custom)
```

A new validator name is one `_VALIDATOR_DISPATCH` entry **plus** one `LEGITIMATE_VALUES` row in
`backend/tests/common/test_validator_name_dispatch.py` (the table-equality test
`test_the_table_covers_every_implemented_name` fails otherwise) and a negative sample in
`backend/tests/common/test_mock_validators_delegates.py`.

The file-key validators (`RELATIVE_FILE_PATH`, `RELATIVE_FILE_PATH_ARRAY`, `DOWNLOAD_KEY_ARRAY`,
`ASSET_PATH`, `ASSET_AUXILIARYPREVIEW_PATH`) also refuse a key whose **UTF-8 encoding** exceeds
`MAX_S3_OBJECT_KEY_BYTES` (1024, the Amazon S3 object-key limit) — bytes, not characters. The
validator sees only the asset-relative part, so the handlers that resolve a key against the asset prefix
re-check the RESOLVED key before their first S3 call and answer a generic `400` that does not echo the
key: `streamAsset.py`, `streamAuxiliaryPreviewAsset.py`, `downloadAsset.py`, `uploadFile.py`
`initialize_upload` (final and `temp-uploads/` key, in the pre-loop block so a refused batch creates no
multipart upload) and `assetFiles.py` `create_folder` / `move_file` / `copy_file` (destination key).
Every guard calls `common/validators.exceeds_s3_object_key_limit(key)` rather than re-implementing the
byte comparison; an over-long key is a `400`, never an S3 `ClientError` reported as a `500` (issue #407).

#### **Request/Response Model Strategy**:

1. **Use validators first** where a validation type exists
2. **Add custom `@root_validator`** for complex business logic validation
3. **Create new validator types** for repetitive validation patterns — a new name is **one entry in
   `_VALIDATOR_DISPATCH`** in `common/validators.py`, and nothing else. That mapping is the list of
   valid names, so there is no second set to keep in step with it. A name with no entry has no rule
   to apply, so `validate()` raises rather than reporting the field valid unchecked. The name is
   resolved **after** the empty/optional short-circuits, so an optional field left empty is skipped
   before its validator is consulted — naming one is not required to say there is nothing to check.
4. **Keep validation logic in models**, not in business logic functions

#### **User IDs: Unicode, Normalized Before Validation and Storage**

`USERID` keeps the Unicode-aware `\w` class, so an external IDP's non-Latin username is accepted.
Call `normalize_userid()` / `normalize_userid_array()` from `common.validators` on a caller-supplied
user id **before** validating it and before storing or looking it up. NFKC folds two compatibility
spellings of one name together, and normalizing on only some paths is worse than not normalizing at
all — the mismatch makes a lookup miss a row that exists. `request_to_claims()` normalizes the
caller's own identity, so a handler working from `claims_and_roles["tokens"]` needs no further call.

`confusable_skeleton()` / `find_confusable_userid()` compare how two ids **look** (Cyrillic `а` folds
onto `a`) and belong at **user creation only** — `create_cognito_user` refuses an id that reads the
same as one already in the pool, while an id already stored keeps working. The mapping covers the
Cyrillic and Greek lookalikes and is deliberately partial.

#### **`REGEX` Values Are Checked for Complexity, and Only on Save**

`validate_regex` rejects three things on top of the compile check: a repeating quantifier applied to a
group that itself repeats or alternates (`(a+)+`, `(a|a)*`), a backreference, and more quantifier
ambiguity than one evaluation can afford — the estimated worst-case backtracking search space against a
256-character subject must stay under `MAX_REGEX_SEARCH_SPACE`, which admits at most three unbounded
quantifiers (`.*`, `+`, `{n,}`). The budget is calibrated by measurement: three unbounded quantifiers
separated by literals match in ~16 ms, four in ~1 s, and five do not finish in 25 s. A value that runs
against longer subjects is checked at its own length: `validate_regex(subject_length=...)`, or
`subjectLength` in the `REGEX` spec (`models/compliance.py` checks path patterns at
`MAX_S3_OBJECT_KEY_BYTES`, 1024, which admits two). A value evaluated with `re.search` passes
`searched` (`searched: True` in the spec): unless it begins with `^` (`_anchored_at_start`), the
start positions the search tries count as one more unbounded quantifier; every compliance rule
pattern is searched. This matters because
a constraint criterion value becomes a Casbin `regexMatch(...)` pattern re-evaluated for every policy line
on every authorization decision. General regex stays accepted (literals, character classes, anchors, `.*`, `|`,
un-quantified groups). The check runs on the **save** paths — `ConstraintCriteriaModel`, plus
`validate_substituted_criteria_values()` in the template importer, which re-checks the value produced
by variable substitution because that is what gets stored — and deliberately not on read, where
`ConstraintResponseModel` uses the rule-free `ConstraintCriteriaResponseModel` so a constraint stored
by an earlier release keeps reading back as stored.

The policy builder writes each criterion value into its line's `obj_rule` as source escapes, one
`\xNN` / `\uNNNN` / `\UNNNNNNNN` per character (`CasbinEnforcerService._escape_rule_value`), never as
plain text: Casbin's policy reader splits lines on commas outside brackets, and pycasbin rewrites `p.` /
`r.` and `&&` / `||` / `!` throughout the rule text, string literals included, before compiling it. Only
the object-type check every line opens with, `regexMatch(r.obj.object__type, '^<type>\Z')`, carries a
registered type name as plain text (`_object_type_rule`).

#### **Example: Secure Model with Validator Integration**

```python
class CreateAssetRequestModel(BaseModel, extra='ignore'):
    """Secure request model with proper validation"""
    assetId: str = Field(min_length=1, max_length=256)
    assetName: str = Field(min_length=1, max_length=256, regex=object_name_pattern)
    databaseId: str = Field(min_length=4, max_length=63, regex=id_pattern)

    @root_validator
    def validate_fields(cls, values):
        # Use existing validators where possible
        (valid, message) = validate({
            'assetId': {
                'value': values.get('assetId'),
                'validator': 'ASSET_ID'
            },
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID'
            },
            'assetName': {
                'value': values.get('assetName'),
                'validator': 'OBJECT_NAME'
            }
        })
        if not valid:
            # Validator messages are safe to return - they explain restrictions without exposing input values
            logger.error(message)
            raise ValueError(message)
        return values
```

## 🚨 **Mandatory Rules**

### **Rule 1: Follow Gold Standard Implementation (assetService.py)**

All backend handlers MUST follow the patterns established in `assetService.py`:

```python
# ✅ CORRECT - Follow assetService.py patterns
import os
import boto3
import json
from datetime import datetime
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from backend.common.resourceNames import get_table_name, get_bucket_name, ResourceKeys
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.[domain] import [RequestModel], [ResponseModel]

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="[ServiceName]")

# Global variables for claims and roles
claims_and_roles = {}

# Load resource names and environment variables
try:
    # Resolve DynamoDB table names from SSM Parameter Store
    required_table_name = get_table_name(ResourceKeys.REQUIRED_STORAGE_TABLE)
    required_bucket = get_bucket_name(ResourceKeys.REQUIRED_BUCKET)
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for [service] APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Route to appropriate handler
        if method == 'GET':
            return handle_get_request(event)
        elif method == 'POST':
            return handle_post_request(event)
        # ... other methods

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
```

**Event normalization (`normalize_event`):** The REST API (v1) proxy event differs from the
HTTP API v2 layout handlers are written against in **two** ways that `normalize_event(event)`
(from `common.auth.apiEvent`) reconciles. It mutates in place, is idempotent, and no-ops on
`lambdaCrossCall` events.

1.  **`requestContext.http` block.** Handlers read
    `event['requestContext']['http']['path']` / `['method']` / `['sourceIp']`; the REST event
    exposes these as top-level `path` / `httpMethod` and `requestContext.identity.sourceIp`.
    `normalize_event` injects the v2-style `requestContext.http` block. The injected path is
    stage-relative on every host: through the optional REST API custom domain the event's
    `path` keeps the base path (`/api/database` where execute-api delivers `/database`), and
    `normalize_event` removes that one segment when `requestContext.domainName` is the
    configured domain (`API_CUSTOM_DOMAIN_NAME` / `API_CUSTOM_DOMAIN_BASE_PATH`, set by the CDK
    only when the domain is enabled). The top-level `path` is left as delivered, so dispatch
    on `requestContext.http.path`, never on `event['path']`.
2.  **Null `pathParameters` / `queryStringParameters`.** The REST event sends these as an
    explicit JSON `null` when empty (HTTP API v2 omitted them), so `event.get('pathParameters', {})`
    / `event.get('queryStringParameters', {})` returns `None` — the default applies only when
    the **key is absent**, not present-but-`null`. A handler that then does `params['id']`,
    `'id' in params`, or `int(params['maxItems'])` crashes with `TypeError: 'NoneType' object
is not subscriptable/iterable` → **500**. `normalize_event` coerces a present-but-`null`
    value of either key to `{}`.

-   **`request_to_claims(event)` calls `normalize_event(event)` internally** (first line),
    so the Gold Standard order above — `request_to_claims(event)` as the handler's first
    event access, _then_ read `path`/`method`/params — is already covered for both
    normalizations and needs **no** import or explicit call.
-   **Only when a handler must read `requestContext['http']`, `pathParameters`, or
    `queryStringParameters` _before_ `request_to_claims`** does it
    `from common.auth.apiEvent import normalize_event` and call `normalize_event(event)` as the
    first statement of `lambda_handler`. Reading those before normalization raises
    `KeyError`/`TypeError` → 500 on a real REST request — a failure invisible to CDK synth and
    to unit tests that hand-build a v2-shaped event, so cover the REST-shaped event (including
    `null` params) in tests.
-   **`claims_and_roles["roles"]` comes from the `vams:roles` authorizer context value**, which
    the authorizer (`common/auth/authorizerCore.py`) resolves from the user roles table with a
    60-second per-user cache. Resolving it there — rather than in the Cognito
    pre-token-generation trigger, which only runs for Cognito — is what makes roles available
    for every auth mode (Cognito, external OAuth IDP, API key) and lets a role change take
    effect without re-issuing a token. The value is informational for handlers and audit logs:
    `CasbinEnforcer` re-reads a user's roles from DynamoDB when it builds policy, so
    authorization does not depend on it. Do not reintroduce a role lookup in `preTokenGen`.
-   **The authorizer owns the `vams:` context namespace**: `authenticate_request` copies a
    verified token's claims into the context except every key under the reserved `vams:` prefix
    (`RESERVED_CONTEXT_KEY_PREFIX`, matched without regard to case), then sets `vams:tokens` to
    `[username]` (`cognito:username`, then `username`, then `sub` -- the value roles and MFA are
    resolved for), `vams:roles`, `vams:mfaEnabled` and `vams:externalAttributes` (`[]`). A
    verified token with no username is unauthenticated (401). A new value the authorizer passes
    to handlers takes the `vams:` prefix and is written after the claim copy; never treat a token
    claim as identity, role, MFA or credential-type input.
-   **Accepted token audiences (#1337)**: the authorizer accepts a Cognito token issued to
    `APP_CLIENT_ID` or to one of `ADDITIONAL_APP_CLIENT_IDS`, and an external IdP token whose
    `aud` is `JWT_AUDIENCE` or one of `JWT_ADDITIONAL_AUDIENCES` (JSON arrays CDK sets only when
    `app.authProvider.<provider>.additionalTokenAudiences` is non-empty; parsed fail-closed,
    matched exactly). Signature, issuer, expiry and `token_use` checks never depend on the
    audience, and the user's roles still decide. A token that names no user (a Cognito access
    token without `username`, or an external token accepted only through an additional audience
    whose identity is missing or equals its client id, or that carries a machine-token marker:
    `gty` = `client-credentials`, `idtyp` = `app`, `preferred_username` starting `service-account-`)
    is rejected. Primary-audience tokens are not checked, and the markers do not cover every IdP,
    so only user sign-in clients may be listed. The accepted audience reaches the authorization
    audit record as `tokenAudience`; it is not put in the authorizer context.
-   **`claims_and_roles["authMethod"]`** is `"apiKey"` when the authorizer context carries
    `vams:authMethod = apiKey` and `"jwt"` otherwise (JWT callers and Lambda cross-calls alike).
    Handlers that mint or seed a credential refuse `"apiKey"` with 403 so one key cannot be chained
    into another; compare against `handlers.auth.AUTH_METHOD_API_KEY`, never a bare string.
    `claims_and_roles["apiKeyId"]` is the id of the key an API-key caller authenticated with (the
    authorizer's `vams:apiKeyId`), for a handler that decides by the key itself, and `None` for every
    other caller.
-   **A temporary session key cannot mint or extend a key.** A temporary session key is an API key
    named with `TEMPORARY_SESSION_KEY_NAME_PREFIX` (`temporary-session-`, `common/constants.py`) that
    a backend component mints for one session through a Lambda cross-call. API-key create and update
    read the caller's key record through `apiKeyId` and answer `403` in every scope to a caller
    authenticated with one; the refusal runs in addition to Tier 1, after `enforceAPI` has allowed
    the route, and a caller whose own key record cannot be read is answered the generic `500`, not
    that refusal. The prefix is reserved: an API-key create that names a key under it is answered
    `400` unless it arrives as a Lambda cross-call
    (`tests/handlers/auth/test_apiKeyService_temporary_session_key_callers.py`).

### **Rule 2: Pydantic Models MUST Follow assetsV3.py Patterns**

**`Field()` silently swallows unknown kwargs.** Pydantic v1 collects any keyword it does not
recognize into `FieldInfo.extra` instead of raising, so a v2 spelling like `pattern=` becomes
an inert annotation that validates **nothing** — the model imports cleanly, tests pass, and
the field is unconstrained. `regex=` is the v1 spelling. `strip_whitespace=` is likewise inert
on `Field()` (it is a `class Config` / `constr` option, not a field constraint), so a padded
value reaches the length and regex checks — and storage — exactly as submitted. A name or id
field trims through `common.validators.trim_name` wired as a `pre=True` validator, which removes
the surrounding whitespace run and preserves interior whitespace. A `description` or `comment` on
a REQUEST model trims through the same validator, declared separately as `_trim_text` so the source
keeps signalling which fields are ids and which are prose; a response or record model does not,
because trimming there rewrites a stored row on the way out. Do not reach for
`anystr_strip_whitespace = True` on the model's `class Config` instead: it strips every string on
the model, including S3 keys and asset-relative paths, where a trailing space is a legitimate part
of the key. `backend/tests/models/test_no_dead_field_kwargs.py` enforces all of this across every
model — including the free-text partition as a rule, so a new `description` must either trim or be
named in its `NO_TRIM_FREE_TEXT` map with a reason — so scaffolding the v2 spelling breaks the suite
rather than shipping an unconstrained field.

```python
# ✅ CORRECT - Follow assetsV3.py patterns
from typing import Dict, List, Optional, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate, trim_name, id_pattern, object_name_pattern

class [Domain]RequestModel(BaseModel, extra='ignore'):
    """Request model for [operation] [domain]"""
    requiredField: str = Field(min_length=3, max_length=63, regex=id_pattern)
    optionalField: Optional[str] = Field(None, min_length=1, max_length=256)

    _trim_names = validator('requiredField', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        # Custom validation logic
        (valid, message) = validate({
            'optionalField': {
                'value': values.get('optionalField'),
                'validator': 'STRING_256',
                'optional': True
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        return values

class [Domain]ResponseModel(BaseModel, extra='ignore'):
    """Response model for [domain] data"""
    id: str
    name: str
    status: Optional[str] = "active"
    timestamp: str
```

### **Rule 3: CDK Lambda Functions MUST Follow assetFunctions.ts Patterns**

```typescript
// ✅ CORRECT - Follow assetFunctions.ts patterns
export function build[Domain]Service(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "[domainService]";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.[domain].${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas ? vpc : undefined,
        vpcSubnets: config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas ? { subnets: subnets } : undefined,
        environment: {
            // Handler-specific env vars only (resource names resolved from SSM)
            REQUIRED_SETTING: config.app.requiredSetting,
        },
    });

    // Grant permissions
    storageResources.dynamo.requiredTable.grantReadWriteData(fun);
    storageResources.s3.requiredBucket.grantReadWrite(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.constraintsStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);  // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
```

### **Rule 4: Authorization MUST Include Casbin Enforcement**

Both tiers must deny when `claims_and_roles["tokens"]` is empty. An empty token list is not
"an anonymous user" — it means no identity could be established, so there is nothing to
evaluate policy against. There are exactly three permitted shapes, and each makes the empty
case deny structurally rather than by remembering to check:

```python
# ── Tier 1: API-level (in lambda_handler) ─────────────────────────────────────
# The flag is pre-set False and only flipped INSIDE the token check, so the empty-token
# case cannot reach the dispatch below.
method_allowed_on_api = False
if len(claims_and_roles["tokens"]) > 0:
    if CasbinEnforcer(claims_and_roles).enforceAPI(event):
        method_allowed_on_api = True
if not method_allowed_on_api:
    return authorization_error()

# ── Tier 2: single resource ───────────────────────────────────────────────────
# The token guard is a SEPARATE statement before enforce(), never a wrapper around it.
resource.update({"object__type": "[objectType]"})
if len(claims_and_roles["tokens"]) == 0:
    return authorization_error()
if not CasbinEnforcer(claims_and_roles).enforce(resource, "GET"):
    return authorization_error()

# ── Tier 2: list filtering ────────────────────────────────────────────────────
# The one shape that may test tokens as a condition: it APPENDS on success, so empty
# tokens yield an empty result rather than an unfiltered one.
allowed = []
for item in items:
    item.update({"object__type": "[objectType]"})
    if len(claims_and_roles["tokens"]) > 0 and casbin_enforcer.enforce(item, "GET"):
        allowed.append(item)
```

```python
# ❌ VIOLATION - single-resource enforce() gated on tokens with no else that denies.
# When tokens is empty the whole block is skipped and execution falls through to the
# mutation below, so the request succeeds unauthorized. This reads as a guard and is the
# opposite of one.
if len(claims_and_roles["tokens"]) > 0:
    if not casbin_enforcer.enforce(resource, "PUT"):
        return authorization_error()

table.put_item(Item=resource)          # reached with NO authorization when tokens == []
return success(body=resource)
```

```python
# ✅ CORRECT - Include proper authorization checks
def handle_get_request(event):
    """Handle GET requests with proper authorization"""
    path_parameters = event.get('pathParameters', {})

    try:
        # Validate parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_parameters['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_parameters['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)

        # Get the resource
        resource = get_resource_details(path_parameters['databaseId'], path_parameters['assetId'])

        # Check authorization
        if resource:
            resource.update({"object__type": "[objectType]"})
            # Fail closed: an empty token list means no authenticated identity, so deny
            # rather than fall through to returning the resource. Never gate the enforce
            # inside `if len(tokens) > 0` without an else that denies.
            if len(claims_and_roles["tokens"]) == 0:
                return authorization_error()
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(resource, "GET"):
                return authorization_error()

            # Convert to response model
            try:
                response_model = [Domain]ResponseModel(**resource)
                return success(body=response_model.dict())
            except ValidationError as v:
                logger.exception(f"Error converting to response model: {v}")
                return success(body={"message": resource})
        else:
            return general_error(body={"message": "Resource not found"}, status_code=404, event=event)

    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)
```

**A check walks only the policy lines that can match its object type.**
`ObjectTypeRoutingEnforcer` (`handlers/authz/__init__.py`, the enforcer
`_create_casbin_enforcer_helper` builds) reads each line's compiled matcher expression when the
policy loads and lists the line under the one object type its
`regexMatch(r.obj.object__type, '^<type>\\Z')` gate names. A line that is not provably gated on one
literal type (regex syntax in the type name, a gate that is not the first conjunct, a top-level
`||`, an unparsable rule, a wrong field count, the deny-all line) is shared by every type in its
original position. A request of type T walks T's lines plus the shared ones in policy order; a type
no line names is denied without a walk. A skipped line would have evaluated `g()` and a false gate
and added only `INDETERMINATE`, so no decision changes.
`tests/handlers/authz/test_casbin_object_type_routing.py` compares it with the all-lines
`ExpressionCachingEnforcer` over every shipped permission template, the seeded roles and synthetic
and hand-written policy sets, and fails when a deny line is dropped or a type misrouted. Keep the
object-type gate as the first criterion of every generated line; a line built otherwise is still
correct but is walked for every type.

**An archived record must pass on its stored id and on its database id.** Archiving an asset and deleting a
database rewrite the record under the partition key `<databaseId>#deleted`; `CasbinEnforcerService.enforce`
allows such a record only when both the stored `databaseId` and the id with the suffix removed pass. A deny on
the database therefore covers its archived records, and an allow written only as `databaseId equals <id>` does
not reach them; to grant archived records, a rule must also name `<databaseId>#deleted` (for example
`criteriaOr`: `equals <id>` OR `equals <id>#deleted`). Pass the stored record to `enforce()` unchanged; do not
strip or add the suffix in a handler for authorization.

**Compiled matcher expressions are cached per user enforcer, never decisions.**
`ExpressionCachingEnforcer` (`handlers/authz/__init__.py`, the parent of
`ObjectTypeRoutingEnforcer`) keeps an **instance-level** dict of compiled `SimpleEval` expressions
keyed on the full inlined rule string, so each distinct policy line is parsed once per enforcer
while every `enforce()` still evaluates every line it walks. The enforcer is owned by one
`CasbinEnforcerService`, so the cache and the routes are per user and are discarded with the
service on the 60-second TTL / MFA invalidation. Never move the cache to class or module level: the
compiled expression holds the enforcer's function map whose `g` is bound to that user's role
manager, so a shared cache would evaluate one user's role links for another.
`tests/handlers/authz/test_casbin_expression_cache.py` pins the instance-level shape, decision
parity against a plain `casbin.Enforcer`, cross-user isolation and TTL/MFA freshness.

#### **System User (`SYSTEM_USER`)**

`SYSTEM_USER` is the **only** valid user ID for system-process actions — never use `SYSTEM`, `system`, or any other variant. It is seeded into the user and user-roles tables during CDK deployment and assigned to the `admin` role, so actions attributed to it pass Casbin authorization. Use it consistently for:

-   **Lambda cross-calls**: `{'lambdaCrossCall': {'userName': 'SYSTEM_USER'}}` — and it is the default in `request_to_claims()` when a cross-call omits `userName`
-   **Username fallbacks**: `claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]` when no user context exists
-   **Provenance / audit values**: `createdBy`, `modifiedBy`, `changeUserId` fallbacks (`user_id or "SYSTEM_USER"`)
-   **Identity comparisons**: e.g. `skip_schema_validation = (username == "SYSTEM_USER")` in `metadataService.py`, and the pipeline-execution bypass in `processWorkflowExecutionOutput.py`

Because handlers compare against this exact string, a mismatched variant silently fails the comparison (or attributes records to a user ID that has no admin role). IAM permissions on direct Lambda invocation are the security boundary for who can inject a `lambdaCrossCall` event.

#### **Authorization Decisions and Permission-Check Endpoints**

Every allow-or-deny decision is made in the API, by the authorizer and `CasbinEnforcer`. The web app may only show or hide a control based on a decision the API returned, such as the route listing from `GET /auth/routes/api/allowed`. The API still enforces each call. Never derive a permission in the client: no capability flag computed from roles, no constraint matching in the browser, and no trust in a role, database or attribute the client sends.

An endpoint that answers "may this user do X" (a permission check, as opposed to the operation itself) follows these practices, from the [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) plus the VAMS rules:

-   **Deny by default.** An unknown object type, an unknown route or a failed lookup returns "not allowed", never an error that hints why.
-   **No existence leaks.** "Doesn't exist" and "not allowed" give the same answer, so the endpoint can't be used to enumerate assets or databases.
-   **Generic reasons only.** The UI may get a reason code for its tooltip ("not permitted"). The response never includes policy details or the constraint that matched.
-   **The result is advisory.** The real call is still enforced on the server, so a stale answer can't grant anything. Cache the answer only briefly in the page, never across users.
-   **Bounded cost.** Use a single-item read per object (no scan or query fan-out; the paging rules, `backend/CLAUDE.md` Rules 14-15), validate the ids with the existing validators, and rely on API Gateway throttling.
-   **Audit.** Record denials through the existing batched probe audit (`_DenialAuditBatch` in `handlers/auth/routes.py`, with its probe marker).
-   **Reachability.** Put the endpoint under `/auth/routes/...` (for example `POST /auth/routes/api/evaluate`), behind the custom authorizer. Every shipped template, `database-readonly` included, already grants `POST` on `/auth/routes`, so no template changes are needed.

The check identifies the user from the token, loads the object itself from the identifiers it is given, and evaluates the same Tier 1 and Tier 2 checks as the endpoint it answers for.

**A stored reference that the system later acts on is authorized when it is saved.** A compliance pipeline rule names a workflow that every evaluation launches as the system user, so `complianceSchemaService.pipeline_rules_refused` decides each new or changed `pipelineRef` on schema create and update the way the execute route decides a run (`may_run_workflow`: Tier 1 `POST` on `/workflows/{databaseId}/{workflowId}/execute` through `enforcer.enforce` on an `api` object, Tier 2 `GET` on the workflow, its pipelines and the rule's own pipeline, each row read with one `GetItem`). It is deny by default: a missing workflow or pipeline answers the same `403` (`Not authorized to run the workflow of pipeline rule '<name>'`, `ruleName`) as a refused one and is logged through `log_authorization`. Unchanged rules are not re-checked and `SYSTEM_USER` is not checked. Follow the same shape for any new field that makes the system act later on an object the saver names.

#### **Single-asset routes: Tier 2 before existence**

`models/common.py` defines two `VAMSGeneralErrorResponse` subclasses for single-asset routes that decide Tier 2 before existence (the assetFiles, assetVersions and assetExportService `get_asset_with_permissions` helpers): `VAMSAuthorizationDeniedResponse` (refused) and `VAMSAssetNotFoundResponse` (authorized, asset missing). A read route answers them with `authorization_error()` (403) and `general_error(status_code=404, body={'message': 'Asset not found'})`, as the asset GET does, by catching them ahead of `VAMSGeneralErrorResponse`. Every assetFiles and assetVersions route answers `VAMSAuthorizationDeniedResponse` with 403; the write routes catch only it and keep their 400 for a missing asset. A route that does not catch them keeps its 400 with the same message. The assetFiles and assetVersions helpers take `include_archived` (set only by read routes called with `showArchived`): the record comes from `common.dynamodb.get_asset_record` and the Tier 2 object from `asset_authorization_object`, whose stand-in for a missing id carries the archived `<databaseId>#deleted` id, so a caller refused that database's archived records gets the same 403 for an archived asset as for a missing one. Write routes never pass it.

### **Rule 5: Storage Resources MUST Be Added to storageBuilder-nestedStack.ts**

```typescript
// ✅ CORRECT - Add new storage resources
export interface storageResources {
    // ... existing resources
    dynamo: {
        // ... existing tables
        [newDomain]StorageTable: dynamodb.Table;
    };
    s3: {
        // ... existing buckets
        [newDomain]Bucket?: s3.Bucket;
    };
}

// In storageResourcesBuilder function:
const [newDomain]StorageTable = new dynamodb.Table(scope, "[NewDomain]StorageTable", {
    ...dynamodbDefaultProps,
    partitionKey: {
        name: "primaryKey",
        type: dynamodb.AttributeType.STRING,
    },
    sortKey: {
        name: "sortKey",
        type: dynamodb.AttributeType.STRING,
    },
});

// Add GSI if needed
[newDomain]StorageTable.addGlobalSecondaryIndex({
    indexName: "RequiredGSI",
    partitionKey: {
        name: "gsiPartitionKey",
        type: dynamodb.AttributeType.STRING,
    },
});

// Return in storageResources
return {
    // ... existing resources
    dynamo: {
        // ... existing tables
        [newDomain]StorageTable: [newDomain]StorageTable,
    },
};
```

### **Rule 6: API Routes MUST Be Registered in an apiBuilder Nested Stack**

Prefer `apiBuilder2-nestedStack.ts` for new endpoints. Place a function in `apiBuilder` only when it must share a directly-referenced function instance defined there. `attachFunctionToApi` records a descriptor in the cross-stack `RouteRegistry` (passed as `registry`) and creates no API resource itself; the API implementation, built last, renders the whole registry into one OpenAPI document. Registering the same method + path twice throws at synth. This rule governs core endpoints. `apiBuilder-nestedStack.ts` and `apiBuilder2-nestedStack.ts` hold most routes, but a feature stack registers the routes of the Lambdas it builds into the same registry: `searchBuilder-nestedStack.ts` registers `/search` and `/search/simple`, and add-on stacks such as `physnaSyncBuilder-nestedStack.ts` register theirs only when the add-on is enabled. A fork registers its routes from `infra/lib/nestedStacks/custom/customResources.ts`, built inside the `CustomBuilder` nested stack, which the REST API stack depends on. `rest-api-gateway-construct.ts` registers the anonymous `/api/amplify-config` and `/api/version` with `registry.register(` directly. `infra/test/api/apiRouteBackendCdkParity.test.ts` checks the synthesized routes against `apiRoutes.py` and the route lists it splices in (`addonApiRoutes.py`, `custom/customApiRoutes.py`).

**Do not consolidate the two API stacks.** They stay split so each carries its own budget against the two per-template CloudFormation ceilings — 500 resources and a 1 MB template body, neither adjustable. In the commercial template `apiBuilder` emits 116 resources in a ~0.51 MB template and `apiBuilder2` emits 139 in ~0.61 MB, so body size fills well ahead of resource count and is what the split buys headroom against.

A third limit is not relieved by the split: **API Gateway resources per REST API** (300 by default, adjustable). Routes from both stacks land in one `RouteRegistry` and are materialized on one `SpecRestApi`, so the path tree — 122 nodes from 100 OpenAPI paths — is a whole-deployment figure. It counts nodes, not routes: `/database/{databaseId}/assets` is three nodes, and a sibling path sharing that prefix adds only its own leaf. `infra/test/api/apiStackCeilings.test.ts` asserts every figure here against the synthesized templates.

```typescript
// ✅ CORRECT - Register API routes
const [domain]Service = build[Domain]Service(
    scope,
    lambdaCommonBaseLayer,
    storageResources,
    config,
    vpc,
    subnets
);

// Attach routes following existing patterns
attachFunctionToApi(this, [domain]Service, {
    routePath: "/[domain]",
    method: apigateway.HttpMethod.GET,
    registry: registry,
});

attachFunctionToApi(this, [domain]Service, {
    routePath: "/[domain]/{[domain]Id}",
    method: apigateway.HttpMethod.GET,
    registry: registry,
});

attachFunctionToApi(this, [domain]Service, {
    routePath: "/[domain]",
    method: apigateway.HttpMethod.POST,
    registry: registry,
});
```

### **Rule 7: Frontend Integration MUST Follow APIService.ts Patterns**

```javascript
// ✅ CORRECT - Add to web/src/services/APIService.ts
/**
 * [Operation description]
 * @param {Object} params - Parameters object
 * @param {string} params.requiredParam - Required parameter description
 * @param {boolean} params.optionalParam - Optional parameter description
 * @returns {Promise<boolean|{message}|any>}
 */
export const [operationName] = async (
    { requiredParam, optionalParam = false },
    api = API
) => {
    try {
        if (!requiredParam) {
            return [false, "Required parameter is missing"];
        }

        const response = await api.[method]("api", `[endpoint]`, {
            body: {
                requiredParam,
                optionalParam,
            },
        });

        if (response?.success === false) {
            return [false, response?.message ?? response];
        }
        return [true, response?.message ?? response];
    } catch (error) {
        console.log("Error in [operationName]:", error);
        return [false, error?.message || "Failed to [operation]"];
    }
};
```

### **Rule 8: CLI Integration MUST Follow api_client.py Patterns**

```python
# ✅ CORRECT - Add to tools/VamsCLI/vamscli/utils/api_client.py

# First add constants to constants.py
API_[DOMAIN] = "/[domain]"
API_[DOMAIN]_BY_ID = "/[domain]/{[domain]Id}"

# Then add exceptions to exceptions.py
class [Domain]NotFoundError(VamsCLIError):
    """Raised when [domain] is not found."""
    pass

class [Domain]AlreadyExistsError(VamsCLIError):
    """Raised when [domain] already exists."""
    pass

# Then add API methods to api_client.py
def [operation_name](self, [params]) -> Dict[str, Any]:
    """
    [Operation description] using the [endpoint] [method] endpoint.

    Args:
        [param]: [Description]

    Returns:
        API response data with [description]

    Raises:
        [Domain]NotFoundError: When [domain] is not found
        Invalid[Domain]DataError: When [domain] data is invalid
        APIError: When API call fails
    """
    try:
        endpoint = API_[DOMAIN].format([param]=[param])
        response = self.[method](endpoint, data=[data], include_auth=True)
        return response.json()

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 400:
            error_data = e.response.json() if e.response.content else {}
            error_message = error_data.get('message', str(e))
            raise Invalid[Domain]DataError(f"Invalid [domain] data: {error_message}")

        elif e.response.status_code == 404:
            raise [Domain]NotFoundError(f"[Domain] not found")
        elif e.response.status_code == 401:
            # _make_request raises a 403 itself as RequestBlockedError, PermissionDeniedError,
            # TokenExpiredError or, for an unrouted path, APIError, so only a 401 reaches this
            # handler as an HTTPError
            raise AuthenticationError(f"Authentication failed: {e}")
        else:
            raise APIError(f"[Operation] failed: {e}")

    except GlobalInfrastructureError:
        raise

    except Exception as e:
        raise APIError(f"Failed to [operation]: {e}")
```

### **Rule 9: Documentation MUST Be Updated Across All Files**

When making API changes, update the appropriate documentation files:

#### **Documentation File Mapping:**

-   **API changes** → Update `VAMS_API.yaml` with new endpoints, schemas, responses
-   **Authorization changes** → Update Docusaurus permissions docs with new permission mappings
-   **Architecture changes** → Update Docusaurus developer docs with component information
-   **Major features** → Update main `README.md`

#### **Comment & Documentation Style (Match Surrounding Code):**

Comments and documentation must be commensurate with the surrounding material — match the level of detail, density, and tone of the file you are editing.

-   **Code comments**: Match the comment density and style already present in the file (the CDK stacks use brief single-line `//` notes and short `/** ... */` section headers). Describe **what** a piece of code is, not the history of why it was added.
-   **No changelog/process narration in code**: Never write comments that reference "upgrades", "new in vX", "added for", migrations, or the change request that prompted the edit. Changelog narration belongs in `CHANGELOG.md` and the docs revision history, not in source comments.

#### **VAMS_API.yaml Update Pattern:**

```yaml
# ✅ CORRECT - Add comprehensive API documentation
/[domain]/{[domain]Id}:
    get:
        summary: "Get a [domain]."
        responses:
            "200":
                description: OK
                content:
                    application/json:
                        schema:
                            $ref: "#/components/schemas/[domain]Response"
            "400":
                description: Invalid parameters.
                content:
                    application/json:
                        schema:
                            $ref: '#/components/schemas/error'
            "403":
                description: Not authorized to access [domain].
                content:
                    application/json:
                        schema:
                            $ref: '#/components/schemas/error'
            "404":
                description: [Domain] not found.
                content:
                    application/json:
                        schema:
                            $ref: '#/components/schemas/error'
            "500":
                description: Error processing request.
                content:
                    application/json:
                        schema:
                            $ref: '#/components/schemas/error'
        parameters:
            - name: "[domain]Id"
              in: "path"
              description: "Unique identifier for [domain]."
              required: true
              schema:
                  $ref: '#/components/schemas/id_regex'
        security:
            - DefaultCognitoAuthorizer: []

components:
    schemas:
        [domain]Request:
            type: object
            properties:
                requiredField:
                    $ref: '#/components/schemas/id_regex'
                optionalField:
                    $ref: '#/components/schemas/string256Param'
            required:
                - requiredField

        [domain]Response:
            type: object
            properties:
                id:
                    $ref: '#/components/schemas/id_regex'
                name:
                    type: string
                status:
                    type: string
                timestamp:
                    type: string
                    format: date-time
            required:
                - id
                - name
                - timestamp
```

#### **Permissions Documentation Update Pattern (`documentation/docusaurus-site/docs/concepts/permissions-model.md`):**

```markdown
# ✅ CORRECT - Add authorization mapping

-   `/[domain]` - GET/POST
    -   `[Domain]` ([domainId], [field1], [field2]) - GET (api: GET)
    -   `[Domain]` ([domainId], [field1], [field2]) - POST (api: POST)
-   `/[domain]/{[domain]Id}` - GET/PUT/DELETE
    -   `[Domain]` ([domainId], [field1], [field2]) - GET (api: GET)
    -   `[Domain]` ([domainId], [field1], [field2]) - PUT (api: PUT)
    -   `[Domain]` ([domainId], [field1], [field2]) - DELETE (api: DELETE)
```

#### **Docusaurus Documentation Updates:**

When making backend or CDK changes, update the corresponding Docusaurus documentation pages at `documentation/docusaurus-site/docs/`:

-   **New or changed API endpoint (incl. path renames)** → Update **both** the OpenAPI spec `VAMS_API.yaml` **and** the matching Docusaurus reference page under `api/` (e.g. `api/auth.md`) — two separate sources of truth that must stay in sync — plus the CLI command reference if applicable
-   **New config option** → Update `deployment/configuration-reference.md`
-   **New config option** → Also mirror it into the interactive **ConfigBuilder** component (`documentation/docusaurus-site/src/components/ConfigBuilder/`) so the config generator stays in sync — see the component `README.md` for which files to touch (`schema.ts`, `defaults.ts`, `validation.ts`, `derived.ts`), then confirm the `infra/test/config/configBuilderSync.test.ts` drift check passes. An add-on field in the add-on config lane (`infra/config/addon/`) goes in its ConfigBuilder module `ConfigBuilder/addon/<addon>.ts` instead (fields, presets, rules, VPC-requiring features and normalizations, joined by `ConfigBuilder/addon/index.ts`), which the drift check covers with the files it splices into. The drift check only verifies `schema.ts` fields and `defaults.ts` presets — it covers **neither** `validation.ts` nor `derived.ts`, so both are kept in sync by review, not by the test. New/changed `getConfig()` validation logic must be hand-ported into `validation.ts` (an add-on module's rule into its ConfigBuilder module): a missing rule leaves the ConfigBuilder approving a config that then fails `cdk synth`, which is worse than no validation because the operator was told it was valid. `getConfig()` auto-mutations — assignments that rewrite the operator's config — must be mirrored into `derived.ts` when added or changed, and **deleted** from it when removed from `getConfig()`; a leftover mutation makes the builder keep rewriting the downloaded `config.json` in a way the deployment does not. Any `getConfig()` normalization is mirrored in `derived.ts`, or in the add-on's ConfigBuilder module for an add-on field (`applyDerived()` runs each one through `applyAddonDerived()`); nothing else may be. The core `getConfig()` performs no auto-mutation today, so the core part of `applyDerived()` is a pass-through; an add-on's steering names any normalization its module mirrors. Where `getConfig()` rejects a feature combination instead of assigning, the mirror is an error rule in `validation.ts` — a feature added to a constraint list such as the VPC-requiring set goes into that file's `VPC_REQUIRING_FEATURES` table (or the add-on module's list that `ADDON_VPC_REQUIRING_FEATURES` joins in), not into `derived.ts`. Two exclusions: rules reading a value the browser cannot see are out of scope — notably the `app.iamRoleConfig` checks, which validate the contents of `infra/config/policy/iamRoleConfig.json`. When checking the port, compare the config FIELD PATHS each rule references; the two files word the same rule differently, so matching on message text under-reports drift.
-   **New pipeline** → Create page in `pipelines/`, update `pipelines/overview.md`, update `overview/features.md`, update `sidebars.ts`
-   **New DynamoDB table** → Update `architecture/aws-resources.md`, `architecture/data-model.md`
-   **Permission changes** → Update `concepts/permissions-model.md`, `user-guide/permissions.md`

#### **Cross-Steering File Updates:**

When making changes that affect development standards, architecture patterns, or quality requirements:

1. Update **all** affected CLAUDE.md files (root, web/, backend/, infra/, tools/VamsCLI/, documentation/)
2. Update the `.kiro/steering/` version of this file
3. If the change affects frontend patterns, also update `WEB_DEVELOPMENT_WORKFLOW.md` and `WEB_FRONTEND.md`
4. If the change affects documentation standards, also update `DOCUMENTATION_WORKFLOW.md`
5. Update any Claude Code skills in `.claude/commands/` that scaffold or reference the changed rule, pattern, checklist, or file path (see root `CLAUDE.md` Rule 12 for the skill-to-steering mapping) — a stale skill actively scaffolds outdated code

### **Rule 10: Tests MUST Follow Comprehensive Patterns**

```python
# ✅ CORRECT - Comprehensive test coverage
import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from moto import mock_dynamodb, mock_s3
from handlers.[domain].[handler] import lambda_handler
from models.[domain] import [RequestModel], [ResponseModel]

@pytest.fixture
def mock_environment():
    """Mock environment variables"""
    with patch.dict('os.environ', {
        'VAMS_RESOURCE_PARAM_PREFIX': '/test/resourceNames',
        'REQUIRED_STORAGE_TABLE_NAME': 'test-table',  # Env var override for testing
        'REQUIRED_BUCKET_NAME': 'test-bucket',
    }):
        yield

@pytest.fixture
def mock_claims_and_roles():
    """Mock claims and roles for authorization"""
    return {
        "tokens": ["test-user@example.com"],
        "roles": ["test-role"],
        "username": "test-user@example.com"
    }

class Test[Domain]Handler:
    """Test [domain] handler functionality."""

    @mock_dynamodb
    @mock_s3
    def test_[operation]_success(self, mock_environment, mock_claims_and_roles):
        """Test successful [operation] execution."""
        # Setup mocks
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer_instance.enforce.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                # Create test event
                event = {
                    'requestContext': {
                        'http': {
                            'path': '/[domain]/test-id',
                            'method': 'GET'
                        }
                    },
                    'pathParameters': {
                        '[domain]Id': 'test-id'
                    }
                }

                # Execute handler
                response = lambda_handler(event, {})

                # Verify response
                assert response['statusCode'] == 200
                body = json.loads(response['body'])
                assert 'message' in body or '[expectedField]' in body

    def test_[operation]_validation_error(self, mock_environment, mock_claims_and_roles, event=event):
        """Test [operation] with validation error."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            # Create invalid event
            event = {
                'requestContext': {
                    'http': {
                        'path': '/[domain]/invalid-id',
                        'method': 'GET'
                    }
                },
                'pathParameters': {
                    '[domain]Id': 'invalid'  # Too short for ID validation
                }
            }

            response = lambda_handler(event, {})

            assert response['statusCode'] == 400
            body = json.loads(response['body'])
            assert 'message' in body

    def test_[operation]_authorization_error(self, mock_environment, mock_claims_and_roles):
        """Test [operation] with authorization error."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = False
                mock_enforcer.return_value = mock_enforcer_instance

                event = {
                    'requestContext': {
                        'http': {
                            'path': '/[domain]/test-id',
                            'method': 'GET'
                        }
                    },
                    'pathParameters': {
                        '[domain]Id': 'test-id'
                    }
                }

                response = lambda_handler(event, {})

                assert response['statusCode'] == 403
```

### **Rule 11: Poetry-Managed Requirements Files Are Generated — Never Edit Directly**

Wherever a `pyproject.toml` sits next to a `requirements*.txt`, the requirements file is a **generated artifact** exported from `poetry.lock` — never edit it by hand. Poetry-managed projects: `backend/`, `backend/lambdaLayers/base/`, `backend/lambdaLayers/authorizer/`, and `backendPipelines/multi/rapidPipelineEKS/lambdaLayer/`.

To change a dependency version:

1. Edit the constraint in `pyproject.toml` only if the current constraint excludes the target version (exact pins like `urllib3 = "2.6.3"` must be edited; ranges like `^2.12.1` that already admit the target need no edit).
2. Re-resolve the lock without installing: `poetry update --lock <package> [<package>...]`
3. Re-export the requirements file(s):

    ```bash
    # Lambda layers and pipeline layers (single requirements.txt):
    poetry export --without-hashes -f requirements.txt -o requirements.txt

    # backend/ (split main vs dev):
    poetry export --only main --without-hashes -f requirements.txt -o requirements.txt
    poetry export --with dev --without-hashes -f requirements.txt -o requirements-dev.txt
    ```

4. Commit `pyproject.toml`, `poetry.lock`, and the exported requirements file(s) together — a requirements file that drifts from its lock will be silently overwritten by the next export. The layer bundling build never reads the committed file: `layerBundlingCommand()` (`infra/lib/helper/lambda.ts`) re-exports the layer's `poetry.lock` inside the build container and installs that export, so a layer ships what its lock resolves to. The command installs pinned versions of pip, Poetry and `poetry-plugin-export`, with Poetry pinned to the version that writes the layer lock files; regenerate the locks with that Poetry version, or move the pin with them (`infra/test/security/layerBundlingToolPins.test.ts` asserts every tool is pinned).

`backend/` and `backend/lambdaLayers/base/` lock every package they share at the same version: handler Lambdas load their third-party packages from the base layer, while the backend suite installs `backend/requirements-dev.txt`. Change a shared package in both trees in the same change. To match a transitive package to a version other than its newest release, add a temporary exact constraint to `pyproject.toml`, run `poetry lock`, remove the constraint, and run `poetry lock` again — Poetry keeps the locked version. `backend/tests/test_base_layer_dependency_parity.py` fails on any difference. The authorizer layer (`backend/lambdaLayers/authorizer/`) is a separate dependency set and is not compared.

Requirements files with **no** side-by-side `pyproject.toml` (e.g. `backendPipelines/multi/rapidPipelineEKS/lambda/requirements.txt`) are hand-maintained pip files and are edited directly.

## 📝 **Development Templates**

### **New Backend Handler Template**

```python
"""[Domain] service handler for VAMS API."""

import os
import boto3
import json
from datetime import datetime
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.[domain] import (
    [RequestModel], [ResponseModel], [OperationResponseModel]
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="[ServiceName]")

# Global variables for claims and roles
claims_and_roles = {}

# Load resource names and environment variables
try:
    # Resolve DynamoDB table names from SSM Parameter Store
    required_table_name = get_table_name(ResourceKeys.REQUIRED_STORAGE_TABLE)
    required_bucket_name = get_bucket_name(ResourceKeys.REQUIRED_BUCKET)
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

# Initialize resources
required_table = dynamodb.Table(required_table_name)

#######################
# Business Logic Functions
#######################

def get_[domain]_details([domain]_id):
    """Get [domain] details from DynamoDB

    Args:
        [domain]_id: The [domain] ID

    Returns:
        The [domain] details or None if not found
    """
    try:
        response = required_table.get_item(Key={'[domain]Id': [domain]_id})
        return response.get('Item')
    except Exception as e:
        logger.exception(f"Error getting [domain] details: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving resource")

def create_[domain]([domain]_data, claims_and_roles):
    """Create a new [domain]

    Args:
        [domain]_data: Dictionary with [domain] creation data
        claims_and_roles: User claims and roles for authorization

    Returns:
        Created [domain] data
    """
    try:
        # Check authorization
        [domain]_data.update({"object__type": "[domain]"})
        # Fail closed: an empty token list means no authenticated identity, so deny
        # rather than fall through to the mutation. Never gate the enforce inside
        # `if len(tokens) > 0` without an else that denies.
        if len(claims_and_roles["tokens"]) == 0:
            return authorization_error()
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce([domain]_data, "POST"):
            return authorization_error()

        # Create the [domain]
        logger.info(f"Creating [domain] {[domain]_data['[domain]Id']}")

        # Add metadata
        now = utc_now().isoformat()  # from common.timestamps import utc_now
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        [domain]_data['dateCreated'] = now
        [domain]_data['createdBy'] = username

        # Save to database
        required_table.put_item(Item=[domain]_data)

        # Return success response
        return [Domain]OperationResponseModel(
            success=True,
            message=f"[Domain] {[domain]_data['[domain]Id']} created successfully",
            [domain]Id=[domain]_data['[domain]Id'],
            operation="create",
            timestamp=now
        )
    except Exception as e:
        logger.exception(f"Error creating [domain]: {e}")
        raise VAMSGeneralErrorResponse("Error creating resource")

#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests for [domain]

    Args:
        event: API Gateway event

    Returns:
        APIGatewayProxyResponseV2 response
    """
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}

    try:
        # Get body from event with default empty dict (Pattern 2: Optional Body)
        body = event.get('body', {})

        # If body exists, parse it safely
        if body:
            # Parse JSON body safely
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError as e:
                    logger.exception(f"Invalid JSON in request body: {e}")
                    return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
            elif isinstance(body, dict):
                body = body
            else:
                logger.error("Request body is not a string or dict")
                return validation_error(body={'message': "Request body cannot be parsed"}, event=event)

        # Case 1: Get a specific [domain]
        if '[domain]Id' in path_parameters:
            logger.info(f"Getting [domain] {path_parameters['[domain]Id']}")

            # Validate parameters
            (valid, message) = validate({
                '[domain]Id': {
                    'value': path_parameters['[domain]Id'],
                    'validator': 'ID'
                },
            })
            if not valid:
                logger.error(message)
                return validation_error(body={'message': message}, event=event)

            # Parse query parameters if needed
            try:
                request_model = parse(query_parameters, model=[Domain]RequestModel)
            except ValidationError as v:
                logger.exception(f"Validation error in query parameters: {v}")
                return validation_error(body={'message': str(v)}, event=event)

            # Get the [domain]
            [domain] = get_[domain]_details(path_parameters['[domain]Id'])

            # Check if [domain] exists and user has permission
            if [domain]:
                [domain].update({"object__type": "[domain]"})
                # Fail closed: an empty token list means no authenticated identity, so
                # deny rather than fall through to returning the resource. Never gate the
                # enforce inside `if len(tokens) > 0` without an else that denies.
                if len(claims_and_roles["tokens"]) == 0:
                    return authorization_error()
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not casbin_enforcer.enforce([domain], "GET"):
                    return authorization_error()

                # Convert to response model
                try:
                    response_model = [Domain]ResponseModel(**[domain])
                    return success(body=response_model.dict())
                except ValidationError as v:
                    logger.exception(f"Error converting [domain] to response model: {v}")
                    return success(body={"message": [domain]})
            else:
                return general_error(body={"message": "[Domain] not found"}, status_code=404, event=event)

        # Case 2: List all [domain]s
        else:
            logger.info("Listing all [domain]s")

            # Parse and validate query parameters
            try:
                request_model = parse(query_parameters, model=[Domain]ListRequestModel)
                query_params = {
                    'maxItems': request_model.maxItems,
                    'pageSize': request_model.pageSize,
                    'startingToken': request_model.startingToken
                }
            except ValidationError as v:
                logger.exception(f"Validation error in query parameters: {v}")
                validate_pagination_info(query_parameters)
                query_params = query_parameters

            # Get all [domain]s with authorization filtering
            [domain]s_result = get_all_[domain]s(query_params)

            # Convert to response models
            formatted_items = []
            for item in [domain]s_result.get('Items', []):
                try:
                    [domain]_model = [Domain]ResponseModel(**item)
                    formatted_items.append([domain]_model.dict())
                except ValidationError:
                    formatted_items.append(item)

            response = {"Items": formatted_items}
            if 'NextToken' in [domain]s_result:
                response['NextToken'] = [domain]s_result['NextToken']

            return success(body=response)

    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)

def handle_post_request(event):
    """Handle POST requests to create [domain]

    Args:
        event: API Gateway event

    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
        # Parse request body with enhanced error handling (Pattern 1: Required Body)
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)

        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)

        # Parse and validate the request model
        request_model = parse(body, model=[Domain]CreateRequestModel)

        # Create the [domain]
        result = create_[domain](
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )

        # Return success response
        return success(body=result.dict())

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for [domain] service APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Route to appropriate handler
        if method == 'GET':
            return handle_get_request(event)
        elif method == 'POST':
            return handle_post_request(event)
        elif method == 'PUT':
            return handle_put_request(event)
        elif method == 'DELETE':
            return handle_delete_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
```

### **New Pydantic Models Template**

```python
"""[Domain] API models for VAMS."""

from typing import Dict, List, Optional, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate, id_pattern, object_name_pattern
from customLogging.logger import safeLogger

logger = safeLogger()  # shared module: logs under the calling handler's service name

######################## [Domain] API Models ##########################

class [Domain]RequestModel(BaseModel, extra='ignore'):
    """Request model for getting a [domain]"""
    includeDeleted: Optional[bool] = False

class [Domain]ListRequestModel(BaseModel, extra='ignore'):
    """Request model for listing [domain]s"""
    maxItems: Optional[int] = Field(default=30000, ge=1)
    pageSize: Optional[int] = Field(default=3000, ge=1)
    startingToken: Optional[str] = None
    includeDeleted: Optional[bool] = False

class [Domain]CreateRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a [domain]"""
    [domain]Id: str = Field(min_length=4, max_length=63, regex=id_pattern)
    [domain]Name: str = Field(min_length=1, max_length=256, regex=object_name_pattern)
    description: str = Field(min_length=4, max_length=256)
    tags: Optional[List[str]] = []

    @root_validator
    def validate_fields(cls, values):
        # Validate tags if provided
        if values.get('tags'):
            logger.info("Validating tags")
            (valid, message) = validate({
                'tags': {
                    'value': values.get('tags'),
                    'validator': 'STRING_256_ARRAY',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        return values

class [Domain]UpdateRequestModel(BaseModel, extra='ignore'):
    """Request model for updating a [domain]"""
    [domain]Name: Optional[str] = Field(None, min_length=1, max_length=256, regex=object_name_pattern)
    description: Optional[str] = Field(None, min_length=4, max_length=256)
    tags: Optional[List[str]] = None

    @root_validator
    def validate_fields(cls, values):
        # Validate tags if provided
        if values.get('tags') is not None:
            logger.info("Validating tags")
            (valid, message) = validate({
                'tags': {
                    'value': values.get('tags'),
                    'validator': 'STRING_256_ARRAY',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)

        # Ensure at least one field is provided for update
        if not any(values.get(field) is not None for field in ['[domain]Name', 'description', 'tags']):
            raise ValueError("At least one field must be provided for update")

        return values

class [Domain]DeleteRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting a [domain]"""
    confirmDelete: bool = Field(default=False)
    reason: Optional[str] = Field(None, max_length=256)

    @validator('confirmDelete')
    def validate_confirmation(cls, v):
        """Ensure confirmation is provided for deletion"""
        if not v:
            raise ValueError("confirmDelete must be true for deletion")
        return v

class [Domain]ResponseModel(BaseModel, extra='ignore'):
    """Response model for [domain] data"""
    [domain]Id: str
    [domain]Name: str
    description: str
    tags: Optional[List[str]] = []
    status: Optional[str] = "active"
    dateCreated: Optional[str] = None
    createdBy: Optional[str] = None

class [Domain]OperationResponseModel(BaseModel, extra='ignore'):
    """Response model for [domain] operations (create, update, delete)"""
    success: bool
    message: str
    [domain]Id: str
    operation: Literal["create", "update", "delete"]
    timestamp: str
```

### **New CDK Lambda Builder Template**

```typescript
/*
 * [Domain] Lambda functions for VAMS CDK infrastructure.
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import {
    suppressCdkNagErrorsByGrantReadWrite,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";

export function build[Domain]Service(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "[domain]Service";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.[domain].${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,

        environment: {
            // Handler-specific env vars only (resource names resolved from SSM)
            PRESIGNED_URL_TIMEOUT_SECONDS: config.app.authProvider.presignedUrlTimeoutSeconds.toString(),
        },
    });

    // Grant permissions
    storageResources.dynamo.[domain]StorageTable.grantReadWriteData(fun);
    // SSM resource name parameters grant via globalLambdaEnvironmentsAndPermissions

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);  // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildCreate[Domain]Function(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "create[Domain]";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.[domain].${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,

        environment: {
            // Handler-specific env vars only (resource names resolved from SSM)
            PRESIGNED_URL_TIMEOUT_SECONDS: config.app.authProvider.presignedUrlTimeoutSeconds.toString(),
        },
    });

    // Grant permissions
    storageResources.dynamo.[domain]StorageTable.grantReadWriteData(fun);
    // SSM resource name parameters grant via globalLambdaEnvironmentsAndPermissions

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);  // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
```

### **New Test Template**

```python
"""Test [domain] functionality."""

import json
import pytest
from unittest.mock import Mock, patch
from moto import mock_dynamodb, mock_s3

from handlers.[domain].[handler] import lambda_handler
from models.[domain] import [RequestModel], [ResponseModel]


@pytest.fixture
def mock_environment():
    """Mock environment variables"""
    with patch.dict('os.environ', {
        'VAMS_RESOURCE_PARAM_PREFIX': '/test/resourceNames',
        '[DOMAIN]_STORAGE_TABLE_NAME': 'test-[domain]-table',  # Env var override for testing
    }):
        yield

@pytest.fixture
def mock_claims_and_roles():
    """Mock claims and roles for authorization"""
    return {
        "tokens": ["test-user@example.com"],
        "roles": ["test-role"],
        "username": "test-user@example.com"
    }

@pytest.fixture
def sample_[domain]_data():
    """Sample [domain] data for testing"""
    return {
        '[domain]Id': 'test-[domain]-id',
        '[domain]Name': 'Test [Domain]',
        'description': 'Test [domain] description',
        'tags': ['test-tag'],
        'dateCreated': '2024-01-01T00:00:00Z',
        'createdBy': 'test-user@example.com'
    }

class Test[Domain]Handler:
    """Test [domain] handler functionality."""

    @mock_dynamodb
    @mock_s3
    def test_get_[domain]_success(self, mock_environment, mock_claims_and_roles, sample_[domain]_data):
        """Test successful [domain] retrieval."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer_instance.enforce.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                with patch('handlers.[domain].[handler].get_[domain]_details') as mock_get:
                    mock_get.return_value = sample_[domain]_data

                    event = {
                        'requestContext': {
                            'http': {
                                'path': '/[domain]/test-[domain]-id',
                                'method': 'GET'
                            }
                        },
                        'pathParameters': {
                            '[domain]Id': 'test-[domain]-id'
                        },
                        'queryStringParameters': {}
                    }

                    response = lambda_handler(event, {})

                    assert response['statusCode'] == 200
                    body = json.loads(response['body'])
                    assert '[domain]Id' in body or 'message' in body

    def test_get_[domain]_not_found(self, mock_environment, mock_claims_and_roles):
        """Test [domain] not found scenario."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                with patch('handlers.[domain].[handler].get_[domain]_details') as mock_get:
                    mock_get.return_value = None

                    event = {
                        'requestContext': {
                            'http': {
                                'path': '/[domain]/nonexistent-id',
                                'method': 'GET'
                            }
                        },
                        'pathParameters': {
                            '[domain]Id': 'nonexistent-id'
                        },
                        'queryStringParameters': {}
                    }

                    response = lambda_handler(event, {})

                    assert response['statusCode'] == 404
                    body = json.loads(response['body'])
                    assert 'message' in body

    def test_create_[domain]_success(self, mock_environment, mock_claims_and_roles):
        """Test successful [domain] creation."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer_instance.enforce.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                with patch('handlers.[domain].[handler].create_[domain]') as mock_create:
                    mock_create.return_value = Mock(
                        dict=lambda: {
                            'success': True,
                            'message': '[Domain] created successfully',
                            '[domain]Id': 'test-[domain]-id',
                            'operation': 'create',
                            'timestamp': '2024-01-01T00:00:00Z'
                        }
                    )

                    event = {
                        'requestContext': {
                            'http': {
                                'path': '/[domain]',
                                'method': 'POST'
                            }
                        },
                        'body': json.dumps({
                            '[domain]Id': 'test-[domain]-id',
                            '[domain]Name': 'Test [Domain]',
                            'description': 'Test [domain] description',
                            'tags': ['test-tag']
                        })
                    }

                    response = lambda_handler(event, {})

                    assert response['statusCode'] == 200
                    body = json.loads(response['body'])
                    assert body['success'] == True

    def test_authorization_failure(self, mock_environment, mock_claims_and_roles):
        """Test authorization failure."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = False
                mock_enforcer.return_value = mock_enforcer_instance

                event = {
                    'requestContext': {
                        'http': {
                            'path': '/[domain]/test-id',
                            'method': 'GET'
                        }
                    },
                    'pathParameters': {
                        '[domain]Id': 'test-id'
                    }
                }

                response = lambda_handler(event, {})

                assert response['statusCode'] == 403

    def test_validation_error(self, mock_environment, mock_claims_and_roles, event=event):
        """Test validation error handling."""
        with patch('handlers.[domain].[handler].request_to_claims') as mock_claims:
            mock_claims.return_value = mock_claims_and_roles

            with patch('handlers.[domain].[handler].CasbinEnforcer') as mock_enforcer:
                mock_enforcer_instance = Mock()
                mock_enforcer_instance.enforceAPI.return_value = True
                mock_enforcer.return_value = mock_enforcer_instance

                event = {
                    'requestContext': {
                        'http': {
                            'path': '/[domain]/invalid',
                            'method': 'GET'
                        }
                    },
                    'pathParameters': {
                        '[domain]Id': 'invalid'  # Too short for ID validation
                    },
                    'queryStringParameters': {}
                }

                response = lambda_handler(event, {})

                assert response['statusCode'] == 400
                body = json.loads(response['body'])
                assert 'message' in body


if __name__ == '__main__':
    pytest.main([__file__])
```

## ✅ **Quality Assurance Checklist**

### **Before Implementation**

-   [ ] Requirements clearly understood
-   [ ] Gold standard patterns reviewed (`assetService.py`, `assetsV3.py`)
-   [ ] API endpoints and methods planned
-   [ ] Authorization requirements identified
-   [ ] Storage resources requirements identified
-   [ ] Frontend integration points identified
-   [ ] CLI integration points identified
-   [ ] Documentation updates planned

### **During Implementation**

-   [ ] Pydantic models created with proper validation
-   [ ] Backend handlers follow gold standard patterns
-   [ ] AWS clients configured with retry configuration
-   [ ] Environment variables loaded with error handling
-   [ ] Authorization checks implemented with Casbin
-   [ ] Error handling comprehensive with proper exceptions
-   [ ] CDK lambda builders created with proper permissions
-   [ ] Storage resources added to interface and builder
-   [ ] API routes registered in apiBuilder2-nestedStack.ts (or apiBuilder for a shared function instance)
-   [ ] Frontend service methods added with proper patterns
-   [ ] CLI API client methods added with proper exceptions

### **After Implementation**

-   [ ] All tests written and passing
-   [ ] Authorization tests included
-   [ ] Validation tests included
-   [ ] Error scenario tests included
-   [ ] Code formatted with Black
-   [ ] Code linted with Flake8
-   [ ] Type checking passes with MyPy
-   [ ] CDK code linted
-   [ ] VAMS_API.yaml updated with new endpoints and schemas
-   [ ] Docusaurus permissions docs updated with authorization mappings
-   [ ] Docusaurus developer docs updated if architecture changes
-   [ ] Frontend integration tested
-   [ ] CLI integration tested
-   [ ] End-to-end testing completed

## 🎯 **Common Implementation Patterns**

### **Event Body Validation Patterns**

All backend handlers MUST follow standardized event body validation patterns based on whether the request body is required or optional:

#### **Pattern 1: Required Event Body (POST/PUT/DELETE operations)**

```python
# ✅ CORRECT - Required body validation pattern (from createAsset.py)
def handle_post_request(event):
    """Handle POST requests with required body"""
    try:
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)

        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)

        # Optional: Validate required fields in the request body
        required_fields = ['databaseId', 'assetName', 'description', 'isDistributable']
        for field in required_fields:
            if field not in body:
                return validation_error(body={'message': f"Missing required field: {field}"}, event=event)

        # Parse and validate the request model
        request_model = parse(body, model=CreateAssetRequestModel)

        # Process the request...

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)
```

#### **Pattern 2: Optional Event Body (GET operations or optional body)**

```python
# ✅ CORRECT - Optional body validation pattern
def handle_get_request(event):
    """Handle GET requests with optional body"""
    try:
        # Get body from event with default empty dict
        body = event.get('body', {})

        # If body exists, parse it safely
        if body:
            # Parse JSON body safely
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError as e:
                    logger.exception(f"Invalid JSON in request body: {e}")
                    return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
            elif isinstance(body, dict):
                body = body
            else:
                logger.error("Request body is not a string or dict")
                return validation_error(body={'message': "Request body cannot be parsed"}, event=event)

        # Now body is always a dict (either parsed or empty)
        # Parse request model (works with both empty and populated body)
        request_model = parse(body, model=RequestModel)

        # Process the request...

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)
```

#### **Key Validation Rules:**

1. **Always check for body existence** when required using `event.get('body')`
2. **Use consistent error messages** for missing body, invalid JSON, and parsing errors
3. **Handle both string and dict body types** safely
4. **Always use try/catch blocks** around JSON parsing
5. **Log exceptions** with appropriate detail level
6. **Return proper HTTP status codes** (400 for validation errors)
7. **Use Pydantic parse()** for model validation after body parsing
8. **Validate required fields** explicitly when needed before Pydantic parsing
9. **Ensure body is always a dict** before passing to Pydantic models
10. **Follow the same error handling pattern** across all handlers

#### **Common Error Messages:**

```python
# Standard error messages to use consistently
"Request body is required"
"Invalid JSON in request body"
"Request body cannot be parsed"
"Missing required field: {field_name}"
```

### **Resource Name and Environment Variable Loading Pattern**

```python
# Standard resource name resolution with environment variable loading
from backend.common.resourceNames import get_table_name, get_bucket_name, ResourceKeys

try:
    # Resolve DynamoDB table names from SSM Parameter Store (with env var overrides)
    required_table_name = get_table_name(ResourceKeys.REQUIRED_STORAGE_TABLE)
    auxiliary_bucket = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)

    # Optional resource names -- catch KeyError if not registered
    try:
        optional_table_name = get_table_name(ResourceKeys.OPTIONAL_TABLE)
    except KeyError:
        optional_table_name = None

    # Handler-specific env vars (direct from os.environ)
    optional_setting = os.environ.get("OPTIONAL_SETTING", "default_value")
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

# Initialize resources using resolved names
required_table = dynamodb.Table(required_table_name)
optional_table = dynamodb.Table(optional_table_name) if optional_table_name else None
```

**Resolution order:** `get_table_name(ResourceKeys.*)` first checks for legacy environment variable overrides (e.g., `REQUIRED_STORAGE_TABLE_NAME`), then consults a 60-minute in-module cache, then a short-lived negative record of keys a completed sweep did not carry — so an unpublished parameter costs one sweep per window rather than one per call — then fetches all resource name parameters from SSM via one paginated GetParametersByPath call. A later sweep that does carry the key clears its negative record. This allows pytest tests and local utilities to inject names directly as environment variables while deployed handlers use SSM.

**Pipeline handlers** in `backendPipelines/` continue to use legacy environment variables and do not call `get_table_name()`.

**`PRESIGNED_URL_TIMEOUT_SECONDS` is not global.** Only `infra/lib/lambdaBuilder/assetFunctions.ts` sets it, for the five asset handlers that mint presigned URLs (`downloadAsset`, `streamAsset`, `streamAuxiliaryPreviewAsset`, `uploadFile`, `assetExportService`), and all five index it (`os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]`) at module level. A handler built by any other lambda builder that copies that idiom raises `KeyError` during module import and returns `500` on every request from cold start — CDK synth, lint, and env-patching unit tests all pass. Add the variable to the handler's own builder before reading it.

**Every path that hands out asset file content applies three controls on top of Casbin.** A presigned GET URL or a streamed body — in `downloadAsset`, `streamAsset`, `streamAuxiliaryPreviewAsset`, and `assetExportService` — is refused (or, for the export's per-asset entries, withheld) when the asset's `isDistributable` is false; is refused (export: withheld, the file stays in the listing) when `validateUnallowedFileExtensionAndContentType` rejects the file's key or the `ContentType` a `head_object` of the version about to be served reports, or when that head fails; and every URL or body actually issued is written to the file-download audit log (`log_file_download` / `log_file_download_bulk` / `log_file_download_streamed` in `customLogging/auditLogging.py`). A new download surface gets all three; `downloadAsset.py`'s `lambda_handler` is the reference call shape, and `assetExportService.py` `file_type_allowed_for_download` is the withhold-not-refuse form. A surface that checks many files in one request runs the heads through a bounded pool (`downloadAsset.py`'s bulk branch, `assetExportService.py` `mark_files_allowed_for_download`): serial per-file heads over an asset of thousands of files exceed the API Gateway integration timeout.

**Presigned GETs pass through `presigned_get_object_params()`.** Every presigned `get_object` URL those four surfaces mint passes its `Params` through `common.s3` `presigned_get_object_params()`. For a key ending in `.svg` (any case) it signs `ResponseContentType=image/svg+xml` and `ResponseContentDisposition=attachment`; every other key is signed unchanged. An SVG is stored as `application/octet-stream`, a browser `<img>` renders it only as `image/svg+xml`, and the attachment disposition keeps a directly opened link a download so no SVG script runs at the bucket origin (#1201). Never sign an SVG inline. `tests/handlers/assets/test_presigned_svg_response_overrides.py` pins each path.

**Every path that writes an object under a new name checks that name first.** Upload initialize and completion (`uploadFile.py`) and the move, rename and copy destination (`assetFiles.py` `move_file` / `copy_file`) call `validateUnallowedFileExtensionAndContentType` on the key before any S3 write. A new write path gets the same check.

**A multipart file completed with no parts is cancelled.** `complete_upload` (`uploadFile.py`) aborts that file's multipart upload, stores nothing and reports the file with `success=False`; `sqsUploadFileLarge.py` `complete_multipart_upload_for_large_file` does the same and returns `False`. Only a file initialized as `zero-byte` completes with an empty `parts` list.

**Upload records expire on `expiresAt`.** `AssetUploadsStorageTable` has a DynamoDB TTL on `expiresAt` (epoch seconds, set by every writer: 7 days from upload initialize, 1 day for workflow-output records). TTL removes an expired record only some time after it expires, so `complete_upload` still refuses a record whose `expiresAt` has passed. A new writer to the table sets `expiresAt` as a Number.

### **AWS Client Configuration Pattern**

```python
# Standard AWS client configuration with retry
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
sns = boto3.client('sns', config=retry_config)
```

### **Authorization Check Pattern**

```python
# Standard authorization check pattern (single resource)
if resource:
    resource.update({"object__type": "[objectType]"})
    # Fail closed: an empty token list means no authenticated identity, so deny rather
    # than fall through. Never gate the enforce inside `if len(tokens) > 0` without an
    # else that denies — that silently skips authorization when tokens are empty.
    # (List-filtering handlers are the exception: they append only when enforce passes,
    # so an empty token list yields an empty result set, which is already fail-closed.)
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    if not casbin_enforcer.enforce(resource, "[ACTION]"):
        return authorization_error()
```

### **Audit Logging Pattern**

Write audit records only through the `log_*` functions in `customLogging/auditLogging.py`; never build a record string in a handler. A record is one line: the bracketed prefix (`[ACTIONS][type: <secondary type>] [user: <id>] [roles: [...]] [mfaEnabled: <bool>]`), the JSON payload, then the ` --- [event: <masked request echo>]` suffix.

-   **Prefix values are encoded.** Every value placed inside a `[...]` segment passes through `_encode_prefix_value` (the roles list through `_encode_roles`, the leading segments through `_event_type`): `[`, `]`, `\`, CR, LF, every other C0/C1 control and the bidirectional and zero-width characters are written as `\uXXXX`. A value made of the characters `userid_pattern` admits is written unchanged. A record that needs an extra segment (the permission-check probe marker) gets a formatter in `auditLogging.py` (`format_authorization_denial_record`) rather than an f-string at the call site.
-   **Payload and echo are single-line ASCII JSON** (`json.dumps(..., ensure_ascii=True, default=str)`), so a value JSON cannot encode is written as its string form instead of dropping the record.
-   **The echo is masked by key name.** `mask_sensitive_data` redacts `CORE_LOG_SENSITIVE_KEYS` (`common/constants.py`) plus the add-on and fork lanes, whole-name and case-insensitive, in `headers` and `multiValueHeaders` alike: `authorization`, `cookie`, `set-cookie`, `x-api-key`, `proxy-authorization`, `x-amz-security-token`, `x-vams-origin-verify`, the token/credential fields and the credential field names `password`, `secret`, `clientSecret`, `client_secret`, `accessToken`, `access_token`, `refreshToken`, `refresh_token`, `privateKey`, `private_key` (`passwordPolicy`, `secretArn` and other names that merely contain the word stay readable). A new header or field that carries a credential is added there in the same change.
-   **An entry is never silently dropped.** A write path that cannot deliver an entry calls `report_undelivered_audit_records(log_type, reason, records)`, which writes each entry to the function's own log as an `auditFallback` JSON line and emits the `VAMS/Audit` `AuditWriteFailures` embedded metric (dimension `AuditLogType`). It never raises, adds nothing to the success path and needs no new IAM.
-   **Every route-method is in the audit action registry.** `common/auditActions.py` (core; add-on lane `common/addonAuditActions.py`, fork lane `custom/customAuditActions.py`, merged core, add-on, custom) maps each route-method of `ALL_API_ROUTES` to an `AuditSpec` (action, group, entity type, kind, legacy type) or names why it records none in `AUDIT_EXEMPT`. A new route gets its entry in the same change; `tests/common/test_audit_registry.py` fails on a route-method that has neither, on a literal type a handler passes to `log_actions`/`log_auth_changes`/`log_auth_other`/`log_action` that the registry does not know, and on a registered action that no handler records and that is not on `AUDIT_PENDING_ACTIONS`. A handler that starts recording a pending action removes it from that list.
-   **Record an action with `log_action` after the write succeeds** (`workflowService.py` is the reference placement): `log_action(event, "assetUpdate", database_id=..., asset_id=..., changes={field: new value}, previous={field: prior value})`. A write records what it wrote or changed (`changes`, plus `previous` only when the handler already loaded the record: never read a record only to fill it); a read, search or download records what was asked (`request_parameters`: ids, filters, page size), never what was returned; `log_action` enforces this from the registry kind by dropping `changes` and `previous` for those kinds. A read or search may pass `item_count` (the number returned, never the items), which the single switch `AUDIT_READ_ITEM_COUNT` controls. A deployment turns successful read and search records off with `app.auditLogging.recordReads: false` (env `AUDIT_RECORD_READS`, absent = on); `log_action` applies it, so call sites never check it. Sizes and counts (`bytes`, `file_count`, `item_count`) come only from data the handler already holds. `outcome="failed"` with a generic `reason` (`notFound`, `validation`, `conflict`, `internal`) records an authorized attempt that failed; `outcome="denied"` goes to the authorization group. Never pass file content. Redaction (`SENSITIVE_KEYS`, `CONTENT_KEYS`) applies to every value section; a value over 2 KB is cut to its SHA-256 and a preview, and a whole record (prefix, payload and echo) is bounded to 32 KB, the echo cut first.
-   **The compliance handlers record every call.** The six compliance API handlers open the request audit context in `lambda_handler` and record their route's action in each dispatch branch once the service function answered: `log_action(event, "complianceSchemaCreate", **audit_outcome(response), ...)` from `common/compliance/routeAudit.py` (a 2xx is a success, a 403 a denial written to the authorization group, anything else a failure with a generic reason). A call the API-level check refuses, or that raises, records the route's own action (`log_action(event, **failure_outcome(status))`). A compliance write never records a schema body, and a write that creates something names it from the service's answer (`created_value`). A compliance request that works across databases records a denial for each database the caller may not act on, as the core listings do. The four compliance Lambdas that serve no API route (trigger, workflow callback, cascade executor, jobs) carry `@audited_handler` and record their system actions (`CORE_AUDIT_SYSTEM_ACTIONS` in `common/auditActions.py`) through a module `_record(action, ...)` helper. The default-schema seed (a CloudFormation custom resource) lives with them in `handlers/compliance/` and records `complianceSchemaSeed`. Claude Code counterpart: `backend/CLAUDE.md` "Action records and the registry".
-   **Enter the request audit context in every entrypoint**: decorate it with `@audited_handler` (or `with audit_request(event, context):`); `tests/common/test_audit_registry.py` fails on an entrypoint that does neither and is not in `AUDIT_ENTRYPOINT_EXEMPT`. The decorator records the route's action as failed (generic reason from the status code) for an error response the handler did not record, and nothing for a 401 or 403, so a handler records only its successes. Pass `log_action` the entrypoint's own event object and the action as a string literal (or through a helper that forwards it), and capture `previous` before setting the new values on the record you read. It resolves the actor (auth method, API key id, cross-call, on-behalf-of) and the request facts once, buffers successful action records and allowed authorization records (the route check from `enforceAPI`, unchanged record bytes) and writes them when the handler returns, one parallel write per group under a 1.5 s deadline with a pre-timeout flush; denials (authorization denials included), failures and errors are written at once. Outside a context every `log_*` function writes exactly as before. A record written from a worker thread is written at once, since the context belongs to the thread that opened it.

### **Response Model Conversion Pattern**

```python
# Standard response model conversion with fallback
try:
    response_model = [Domain]ResponseModel(**resource)
    return success(body=response_model.dict())
except ValidationError as v:
    logger.exception(f"Error converting to response model: {v}")
    return success(body={"message": resource})
```

### **Pagination Handling Pattern**

```python
# Standard pagination handling
try:
    request_model = parse(query_parameters, model=[Domain]ListRequestModel)
    query_params = {
        'maxItems': request_model.maxItems,
        'pageSize': request_model.pageSize,
        'startingToken': request_model.startingToken
    }
except ValidationError as v:
    logger.exception(f"Validation error in query parameters: {v}")
    validate_pagination_info(query_parameters)
    query_params = query_parameters
```

### **DynamoDB Query Pattern (for API responses)**

```python
# Standard DynamoDB query with proper pagination using LastEvaluatedKey
# NOTE: This pattern is for main API query results. For internal data fetching to construct
# larger query sets, use the regular paginator as larger datasets are required.

# Build query parameters
query_params_dict = {
    'TableName': table_name,
    'KeyConditionExpression': 'partitionKey = :pkValue',
    'ExpressionAttributeValues': {
        ':pkValue': {'S': partition_value}
    },
    'ScanIndexForward': False,
    'Limit': int(query_params['pageSize'])
}

# Add ExclusiveStartKey if startingToken provided (decode base64)
if query_params.get('startingToken'):
    try:
        decoded_token = base64.b64decode(query_params['startingToken']).decode('utf-8')
        query_params_dict['ExclusiveStartKey'] = json.loads(decoded_token)
    except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
        logger.exception(f"Invalid startingToken format: {e}")
        raise VAMSGeneralErrorResponse("Invalid pagination token")

# Single query call with pagination
response = dynamodb_client.query(**query_params_dict)

# Process items with authorization filtering
authorized_items = []
deserializer = TypeDeserializer()
for item in response.get('Items', []):
    # Deserialize the item
    deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}

    # Add object type for Casbin enforcement
    deserialized_item.update({"object__type": "[objectType]"})

    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(deserialized_item, "GET"):
            authorized_items.append(deserialized_item)

# Build response with nextToken
result = {"Items": authorized_items}

# Return LastEvaluatedKey as nextToken if present (base64 encoded)
if 'LastEvaluatedKey' in response:
    json_str = json.dumps(response['LastEvaluatedKey'])
    result["NextToken"] = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
```

### **DynamoDB Scan Pattern (for API responses)**

```python
# Standard DynamoDB scan with proper pagination using LastEvaluatedKey
# NOTE: This pattern is for main API scan results. For internal data fetching to construct
# larger query sets, use the regular paginator as larger datasets are required.

# Build scan parameters
scan_params = {
    'TableName': table_name,
    'Limit': int(query_params['pageSize'])
}

# Add filter if needed
if filter_expression:
    scan_params['ScanFilter'] = filter_expression

# Add ExclusiveStartKey if startingToken provided (decode base64)
if query_params.get('startingToken'):
    try:
        decoded_token = base64.b64decode(query_params['startingToken']).decode('utf-8')
        scan_params['ExclusiveStartKey'] = json.loads(decoded_token)
    except (json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError) as e:
        logger.exception(f"Invalid startingToken format: {e}")
        raise VAMSGeneralErrorResponse("Invalid pagination token")

# Single scan call with pagination
response = dynamodb_client.scan(**scan_params)

# Process items with authorization filtering
authorized_items = []
deserializer = TypeDeserializer()
for item in response.get('Items', []):
    # Deserialize the item
    deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}

    # Add object type for Casbin enforcement
    deserialized_item.update({"object__type": "[objectType]"})

    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(deserialized_item, "GET"):
            authorized_items.append(deserialized_item)

# Build response with nextToken
result = {"Items": authorized_items}

# Return LastEvaluatedKey as nextToken if present (base64 encoded)
if 'LastEvaluatedKey' in response:
    json_str = json.dumps(response['LastEvaluatedKey'])
    result["NextToken"] = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
```

### **Internal Data Fetching Pattern (for constructing larger datasets)**

```python
# Pattern for internal data fetching where complete datasets are needed
# Use this when you need to fetch ALL items to construct response data, not for API pagination
# Examples: Getting bucket details for each database, fetching related metadata, etc.

# For Query operations - fetch all items
paginator = dynamodb.meta.client.get_paginator('query')
page_iterator = paginator.paginate(
    TableName=table_name,
    KeyConditionExpression=Key('partitionKey').eq(partition_value),
    ScanIndexForward=False
).build_full_result()

all_items = []
for item in page_iterator.get('Items', []):
    all_items.append(item)

# For Scan operations - fetch all items
paginator = dynamodb_client.get_paginator('scan')
page_iterator = paginator.paginate(
    TableName=table_name,
    ScanFilter=filter_expression
).build_full_result()

all_items = []
for item in page_iterator.get('Items', []):
    deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
    all_items.append(deserialized_item)

# For S3 operations - fetch all objects under prefix
paginator = s3_client.get_paginator('list_objects_v2')
all_objects = []
for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
    if 'Contents' in page:
        for obj in page['Contents']:
            all_objects.append(obj)

# IMPORTANT: Only use this pattern when you genuinely need ALL items for internal processing.
# For API responses that return lists to users, always use the LastEvaluatedKey pattern above.
```

### **Choosing a File's `.previewFile.<ext>`**

A file can hold several `<file>.previewFile.<ext>` objects (a pipeline re-run in another format writes a `.png` beside an existing `.gif`; nothing deletes the other). Every path that attaches a file's preview goes through `select_newest_preview_file((key, lastModified, isArchived) …)` (`backend/common/s3PathPatterns.py`): in `handlers/assets/assetFiles.py` the current listing, the version-snapshot listing in basic and detailed mode, and the single-file info that set `previewFile`, and in `handlers/assets/assetExportService.py` the export's per-file preview path. A live preview beats an archived one, then the latest `lastModified` wins, ties keep key order. Do not pick "the first in key order" in a new path. Tests: `tests/handlers/assets/test_assetFiles_newest_preview.py`, `tests/handlers/assets/test_assetExportService_newest_preview.py`.

## 🔍 **Code Review Checklist**

### **Backend Handler Compliance**

-   [ ] Follows `assetService.py` gold standard patterns
-   [ ] Uses AWS Lambda Powertools for logging and parsing
-   [ ] Includes comprehensive error handling with proper exceptions
-   [ ] Implements Casbin authorization enforcement
-   [ ] Uses Pydantic models for request/response validation
-   [ ] Configures AWS clients with retry configuration
-   [ ] Resolves resource names via `get_table_name(ResourceKeys.*)` in module-level `try/except`
-   [ ] Separates business logic from request handling
-   [ ] Includes proper logging with structured messages

### **CDK Infrastructure Compliance**

-   [ ] Follows `assetFunctions.ts` patterns for lambda builders
-   [ ] Updates `storageBuilder-nestedStack.ts` for new resources
-   [ ] Registers routes in `apiBuilder2-nestedStack.ts` (or `apiBuilder` for a shared function instance)
-   [ ] Configures proper IAM permissions
-   [ ] Includes KMS key permissions
-   [ ] Configures VPC/subnet based on config flags
-   [ ] Uses consistent naming conventions
-   [ ] Applies CDK Nag suppressions appropriately

### **Integration Compliance**

-   [ ] Frontend service methods follow `APIService.ts` patterns
-   [ ] CLI API client methods follow `api_client.py` patterns
-   [ ] Constants added to appropriate files
-   [ ] Exceptions added to exception hierarchy
-   [ ] Error handling consistent across all layers

### **Documentation Compliance**

-   [ ] `documentation/VAMS_API.yaml` updated with comprehensive schemas
-   [ ] `documentation/docusaurus-site/docs/concepts/permissions-model.md` updated with authorization mappings
-   [ ] `documentation/docusaurus-site/docs/developer/` updated with architecture information
-   [ ] `documentation/docusaurus-site/docs/api/` updated with API reference pages
-   [ ] `documentation/docusaurus-site/sidebars.ts` updated if new pages added
-   [ ] Code examples included in documentation
-   [ ] Error responses documented properly

## 🚀 **Development Commands**

### **Backend Development**

```bash
# Setup backend development environment
cd backend
python -m venv venv
# Windows PowerShell:
venv\Scripts\Activate.ps1
# Linux/Mac:
source venv/bin/activate

pip install -r requirements-dev.txt

# Code quality checks
black backend/                    # Format code
flake8 backend/                   # Lint code
mypy backend/                     # Type checking
pytest                            # Run tests
pytest -n auto --dist loadfile    # Run tests in parallel, one test file per worker (what CI runs)
pytest --cov=backend             # Run tests with coverage

# Run specific test files
pytest tests/handlers/[domain]/   # Test specific domain
pytest -v tests/handlers/[domain]/test_[handler].py  # Test specific handler

# List tests that pin one past change rather than a durable rule (root CLAUDE.md Rule 13)
pytest -m temporary --collect-only
```

**Parallel runs (`pytest-xdist`).** CI runs the backend suite with `-n auto --dist loadfile`: each test file
runs whole, in order, in one worker, and only the order of files changes. A backend test must pass whichever
files ran before it in the same process: set env vars with `monkeypatch.setenv`, patch globals and
`sys.modules` entries so they are restored, reset module-level caches in the fixture that fills them, and
write files only under `tmp_path`. Fix an order-dependent test rather than skipping it. Details:
`backend/tests/CLAUDE.md`, "Running in parallel".

#### **Mark a Temporary Test with `@pytest.mark.temporary`**

A test written to prove one specific change landed — a deleted file, a removed test seam, a dead branch —
carries `@pytest.mark.temporary` (registered in `backend/pytest.ini`) plus a line naming what it pins.
`backend`, `tools/VamsCLI`, and `tools/ExternalIntegrations/isaacsim_vams_integration` run
`--strict-markers`, so the marker must stay registered there or every test using it fails;
`backendPipelines` has no pytest configuration file, so it only warns.

The marker is required because **a temporary test and a durable guardrail are indistinguishable by reading
them afterwards** — both scan source, both assert an absence, both explain themselves. Applying it up front
is the only way release cleanup can separate them.

Do **not** mark, and do not remove, a test whose forbidden construct is still writable: a broader IAM
wildcard, a `cdk-nag` suppression, a mutating call from a read-only path, a validator regex hardcoding
`arn:aws:`. Nor a positive/negative **control** whose subject is deliberately a string that does not exist.

The shortcut "the forbidden literal appears nowhere in the source, so the test is spent" is **wrong** — a
forbid-forever guardrail also has zero occurrences, and that absence is the guard working. Full criterion:
root `CLAUDE.md` Rule 13; Claude Code counterpart: `backend/tests/CLAUDE.md`.

### **CDK Development**

```bash
# Setup CDK development environment
cd infra
npm install

# CDK commands
npx cdk diff                         # Show changes
npx cdk synth                        # Synthesize CloudFormation
npx cdk deploy --all                 # Deploy all stacks
npx cdk destroy --all                # Destroy all stacks

# Code quality checks
npm run lint                     # Lint TypeScript code
npm run test                     # Run CDK tests
```

### **Integration Testing**

```bash
# Test backend with local development
cd backend
USE_LOCAL_MOCKS=true python3 backend/localDev_api_server.py

# Test frontend integration
cd web
npm run start

# Test CLI integration
cd tools/VamsCLI
pip install -e ".[dev]"
vamscli --help
```

## 📚 **Detailed Implementation Guide**

### **Adding New API Domain**

#### **Step 1: Create Pydantic Models**

```python
# models/[domain].py
"""[Domain] API models for VAMS."""

from typing import Dict, List, Optional, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate, id_pattern, object_name_pattern
from customLogging.logger import safeLogger

logger = safeLogger()  # shared module: logs under the calling handler's service name

# Add all request/response models following assetsV3.py patterns
```

#### **Step 2: Create Backend Handler**

```python
# handlers/[domain]/[handler].py
"""[Domain] service handler for VAMS API."""

import os
import boto3
import json
from datetime import datetime
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from backend.common.resourceNames import get_table_name, get_bucket_name, ResourceKeys
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.[domain] import [RequestModel], [ResponseModel]

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="[ServiceName]")

# Global variables for claims and roles
claims_and_roles = {}

# Load resource names and environment variables
try:
    # Resolve DynamoDB table names from SSM Parameter Store
    required_table_name = get_table_name(ResourceKeys.REQUIRED_STORAGE_TABLE)
    required_bucket = get_bucket_name(ResourceKeys.REQUIRED_BUCKET)
    # Handler-specific env vars (direct from os.environ)
    presigned_url_timeout = os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

# Initialize resources
required_table = dynamodb.Table(required_table_name)

# Follow complete assetService.py patterns
```

#### **Step 3: Add Storage Resources**

```typescript
// infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts
// Add new table to interface and builder function
```

#### **Step 4: Create Lambda Builder**

```typescript
// infra/lib/lambdaBuilder/[domain]Functions.ts
// Follow assetFunctions.ts patterns
```

#### **Step 5: Register API Routes**

```typescript
// infra/lib/nestedStacks/apiLambda/apiBuilder2-nestedStack.ts
// Add route registrations using attachFunctionToApi (pass the cross-stack `registry`)
```

#### **Step 6: Add Frontend Integration**

```javascript
// web/src/services/APIService.ts
// Add service methods following existing patterns. Check for other files that may already implement the API route but aren't in APIService.
```

#### **Step 7: Add CLI Integration**

```python
# tools/VamsCLI/vamscli/constants.py - Add API endpoints
# tools/VamsCLI/vamscli/utils/exceptions.py - Add exceptions
# tools/VamsCLI/vamscli/utils/api_client.py - Add API methods
# tools/VamsCLI/vamscli/commands/[domain].py - Add commands if needed
```

#### **Step 8: Update Documentation**

```yaml
# documentation/VAMS_API.yaml - Add comprehensive API documentation
```

```markdown
# documentation/docusaurus-site/docs/concepts/permissions-model.md - Add authorization mappings

# documentation/docusaurus-site/docs/api/ - Add/update API reference page

# documentation/docusaurus-site/docs/deployment/configuration-reference.md - Add new config options
```

### **Modifying Existing API**

#### **Step 1: Update Models**

-   Add new fields to existing Pydantic models
-   Update validation logic if needed
-   Maintain backward compatibility

#### **Step 2: Update Handler**

-   Add new business logic functions
-   Update request handlers for new functionality
-   Maintain existing error handling patterns

#### **Step 3: Update CDK**

-   Add new environment variables if needed
-   Update permissions if accessing new resources
-   Add new storage resources if required

#### **Step 4: Update Integration**

-   Update frontend service methods
-   Update CLI API client methods
-   Update constants and exceptions

#### **Step 5: Update Documentation**

-   Update API schemas in `documentation/VAMS_API.yaml`
-   Update permission mappings in `documentation/docusaurus-site/docs/concepts/permissions-model.md`
-   Update API reference pages in `documentation/docusaurus-site/docs/api/`
-   Update examples and usage information

## 🛠️ **Best Practices Summary**

### **Backend Development**

1. **Always** follow `assetService.py` gold standard patterns
2. **Always** use AWS Lambda Powertools for logging and parsing
3. **Always** implement comprehensive error handling
4. **Always** include Casbin authorization enforcement
5. **Always** use Pydantic models for validation
6. **Always** configure AWS clients with retry configuration
7. **Always** load environment variables with error handling
8. **Always** separate business logic from request handling
9. **Always** include proper structured logging
10. **Always** write comprehensive tests

### **CDK Development**

1. **Always** follow `assetFunctions.ts` patterns for lambda builders
2. **Always** update `storageBuilder-nestedStack.ts` for new resources
3. **Always** register routes in `apiBuilder2-nestedStack.ts` (or `apiBuilder` for a shared function instance)
4. **Always** configure proper IAM permissions
5. **Always** include KMS key permissions
6. **Always** configure VPC/subnet based on config flags
7. **Always** use consistent naming conventions
8. **Always** apply CDK Nag suppressions appropriately
9. **Always** include proper resource dependencies
10. **Always** test CDK synthesis and deployment

### **Integration Development**

1. **Always** update frontend service methods (check for where backend end-points are used)
2. **Always** update CLI API client methods
3. **Always** add constants to appropriate files
4. **Always** add exceptions to exception hierarchy
5. **Always** maintain consistent error handling
6. **Always** test integration points
7. **Always** update documentation
8. **Always** verify end-to-end functionality
9. **Always** maintain backward compatibility
10. **Always** follow existing patterns

### **Documentation Development**

1. **Always** update `VAMS_API.yaml` with comprehensive schemas
2. **Always** update Docusaurus permissions docs (`documentation/docusaurus-site/docs/concepts/permissions-model.md`) with authorization mappings
3. **Always** update Docusaurus developer docs (`documentation/docusaurus-site/docs/developer/`) with architecture changes
4. **Always** include code examples and usage information
5. **Always** document error responses properly
6. **Always** maintain consistency with existing documentation
7. **Always** verify all links and references work
8. **Always** include security requirements
9. **Always** document breaking changes clearly
10. **Always** update version information appropriately

## 🔧 **Troubleshooting Guide**

### **Common Backend Issues**

-   **Import Errors**: Ensure all imports follow the project structure
-   **Environment Variable Errors**: Check CDK environment variable configuration
-   **Authorization Failures**: Verify Casbin object-type and action mappings
-   **Validation Errors**: Check Pydantic model field definitions and validators
-   **AWS Client Errors**: Verify IAM permissions and retry configuration

### **Common CDK Issues**

-   **Permission Errors**: Check IAM role permissions and resource grants
-   **Environment Variable Issues**: Verify storage resources are passed correctly
-   **Route Registration Issues**: Check API Gateway route path and method configuration
-   **Resource Dependency Issues**: Verify resource dependencies and initialization order

### **Common Integration Issues**

-   **Frontend API Errors**: Check response format handling and error extraction
-   **CLI API Errors**: Check endpoint constants and exception handling
-   **Documentation Sync Issues**: Verify all documentation files are updated consistently

This workflow ensures that all VAMS backend API and CDK development follows established patterns and maintains consistency across the entire system ecosystem.
