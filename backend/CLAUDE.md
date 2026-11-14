# CLAUDE.md -- VAMS Python Lambda Backend

> Auto-loaded when Claude Code works within `backend/`. Authoritative guide for Lambda handler development, Pydantic model creation, authorization enforcement, DynamoDB access, and validators. Testing details: `backend/tests/CLAUDE.md`. Copy-paste skeletons for a new handler / model / test file: `backend/HANDLER_TEMPLATES.md`.

---

## Quick Reference

-   **Runtime**: Python 3.12 (AWS Lambda); Python 3.13+ (local dev/tests)
-   **Framework**: AWS Lambda + API Gateway REST API (v1)
-   **Validation**: Pydantic **1.10.13** (NOT v2) via aws-lambda-powertools
-   **Auth**: Casbin ABAC/RBAC with DynamoDB policy storage
-   **ORM**: boto3 DynamoDB resource + client APIs
-   **Search**: OpenSearch (opensearch-py 2.7.1)
-   **Logging**: aws-lambda-powertools Logger with custom redaction
-   **Tests**: pytest 9.0.3 + moto 5.1.0 (see `backend/tests/CLAUDE.md`)
-   **Gold Standard**: `backend/handlers/assets/assetService.py` (handler), `backend/models/assetsV3.py` (model)

---

## Directory Structure

> Update this tree when adding new handler domains, model files, or test directories (root `CLAUDE.md` Rule 11).

```
backend/
├── conftest.py, pytest.ini              # See backend/tests/CLAUDE.md
├── pyproject.toml, poetry.lock          # Poetry-managed deps
├── requirements.txt, requirements-dev.txt  # Exported from poetry.lock
├── backend/
│   ├── common/                                     # Shared utilities (no AWS bootstrap)
│   │   ├── auth/apiEvent.py                        #   normalize_event: REST→HTTP-API-v2 shape
│   │   ├── auth/authorizerCore.py                  #   JWT/API-key/IP validation
│   │   ├── auth/clientIp.py                        #   True client-IP resolution
│   │   ├── auth/originVerify.py                    #   CloudFront origin verification value (secret, cache)
│   │   ├── apiRoutes.py                            # MASTER API route registry: ApiRoute constants,
│   │   │                                           #   category group arrays, ALL_API_ROUTES
│   │   │                                           #   (core + ADDON_ROUTES + CUSTOM_ROUTES).
│   │   │                                           #   Handlers dispatch via ApiRoute.matches().
│   │   ├── apiRouteTypes.py                        # ApiRoute + GET/POST/... (re-exported by apiRoutes.py)
│   │   ├── auditActions.py                         # Audit action registry: AuditSpec per route-method, exemptions,
│   │   │                                           #   system actions (core + add-on + custom)
│   │   ├── auditActionTypes.py                     # AuditSpec, audit groups and kinds (re-exported by auditActions.py)
│   │   ├── addonApiRoutes.py                       # Add-on routes (Physna viewer, EDH Workstations) → ADDON_ROUTES
│   │   ├── addonAuditActions.py                    # Add-on audit actions → merged into auditActions.py
│   │   ├── addonConstants.py                       # Add-on object types, constraint fields, blocklist entries, log keys
│   │   ├── addonFeatures.py                        # Add-on feature switches; which ones the deployment enabled
│   │   ├── addonResourceNames.py                   # AddonResourceKeys (mirrors infra/common/addonResourceParamKeys.ts)
│   │   ├── compliance/auditRecord.py               # build_audit_item(): the ONE builder of audit rows;
│   │   │                                           #   sets allListPartition for AuditByDateGSI (guard test
│   │   │                                           #   fails any audit put_item that bypasses it); leaves
│   │   │                                           #   out an empty databaseId (DatabaseIndex key, D173)
│   │   ├── compliance/routeAudit.py                # audit_outcome() / failure_outcome(): the VAMS action
│   │   │                                           #   record outcome of a compliance API call (2xx
│   │   │                                           #   success, 403 denied, else failed + reason);
│   │   │                                           #   request_parameters(), response_count(),
│   │   │                                           #   created_value() (the id a 2xx write created,
│   │   │                                           #   from the service's answer, never the request)
│   │   ├── compliance/listing.py                   # Compliance listings: query_page() (bounded page
│   │   │                                           #   fill, ≤200 rows, token = last row's index key;
│   │   │                                           #   ≤5 reads AND ≤1 s spent in `accept`, then a
│   │   │                                           #   short/empty page + token; `prepare` sees each
│   │   │                                           #   read's rows first, for batched reads),
│   │   │                                           #   merge_query_page() (one newest-first query per
│   │   │                                           #   partition merged into one page, same budgets;
│   │   │                                           #   the audit trail of each database a reader may
│   │   │                                           #   read, D173), once_per_key() (a
│   │   │                                           #   listing over many databases asks the engine
│   │   │                                           #   once per database id, never once per row),
│   │   │                                           #   count_items() (Select=COUNT), query_ids() (the
│   │   │                                           #   same reads, returning the ids), name attributes,
│   │   │                                           #   asset_text_filter() (no key attr in a filter),
│   │   │                                           #   state_record_answer(): EVERY answer returning
│   │   │                                           #   asset-state records drops the listing-only
│   │   │                                           #   PRIVATE_STATE_ATTRIBUTES (assetNameSort,
│   │   │                                           #   assetSearchText) through it (D163)
│   │   ├── compliance/assetAccess.py               # THE asset rule for compliance records, live
│   │   │                                           #   and archived (every compliance read, action
│   │   │                                           #   and sweep uses it): the compliance object,
│   │   │                                           #   and, for a caller whose policy has asset
│   │   │                                           #   rules (CasbinEnforcer.
│   │   │                                           #   policy_has_object_type("asset")), the asset
│   │   │                                           #   GET on the asset's row as stored, as the
│   │   │                                           #   asset routes check it; an archived asset
│   │   │                                           #   also the compliance object on the stored id
│   │   │                                           #   `<db>#deleted`. No asset rules: no asset
│   │   │                                           #   check, live or archived. Decided once per
│   │   │                                           #   database when the caller's rules read no
│   │   │                                           #   asset field but databaseId (CasbinEnforcer.
│   │   │                                           #   policy_object_fields), else per distinct value of
│   │   │                                           #   the fields read (single record: GetItem;
│   │   │                                           #   listing: rows noted via query_page's
│   │   │                                           #   `prepare` = expect_items(), BatchGetItem per
│   │   │                                           #   100; whole database: live partition once);
│   │   │                                           #   access() (visible / denied / archived
│   │   │                                           #   hidden) / decide() / live_withheld() /
│   │   │                                           #   live_denied() / withheld() / withholds() /
│   │   │                                           #   archived_asset_visible(); asset checks run
│   │   │                                           #   on the engine and their denials go
│   │   │                                           #   out in one batch: handlers call flush_denials()
│   │   │                                           #   in lambda_handler's finally. A deleted
│   │   │                                           #   database's history (<db>#deleted,
│   │   │                                           #   names_a_deleted_asset) is decided the same
│   │   │                                           #   way on its archive rows (D168). A live record
│   │   │                                           #   with no asset row: decided on the database
│   │   │                                           #   per database, refused when decided per row
│   │   │                                           #   (live_withheld().readable is the allowlist
│   │   │                                           #   the overview counts and bindings use). left_out() /
│   │   │                                           #   listed() leave archived assets out of the
│   │   │                                           #   overview, its counts (withheld_state_counts())
│   │   │                                           #   and the quarantine list for every caller.
│   │   │                                           #   Handlers pass retirements= (a databaseRetirement
│   │   │                                           #   .Presenter): an archived row archived before the
│   │   │                                           #   id's last delete is the deleted database's, not
│   │   │                                           #   the live id's (archived_before_delete, D136)
│   │   ├── compliance/cascadeAuditSummary.py       # cascade_completed / cascade_auto_triggered audit
│   │   │                                           #   details: archived descendants (children) are
│   │   │                                           #   recorded per database, by asset id, outside the
│   │   │                                           #   shared summary / childCount the notifications
│   │   │                                           #   carry (completion_details, opened_details); the
│   │   │                                           #   audit service counts each only for a reader who
│   │   │                                           #   may see that archived asset (details_for_reader).
│   │   │                                           #   Descendants outside the parent's database are
│   │   │                                           #   recorded per database (summaryByDatabase) and
│   │   │                                           #   counted only for a reader of that database, the
│   │   │                                           #   rest as unauthorizedNodes (the cascade service's
│   │   │                                           #   _NodeVisibility applies the same rule to GET and
│   │   │                                           #   listings). Live descendants are also recorded by
│   │   │                                           #   id (liveNodesByDatabase, <= MAX_LIVE_IDS_BYTES)
│   │   │                                           #   and each counted as the cascade read counts it
│   │   │                                           #   (live_outcome = AssetAccess.access: seen by
│   │   │                                           #   status, refused in unauthorizedNodes, archived-
│   │   │                                           #   since and hidden left out); ids never returned
│   │   ├── compliance/evaluationEngine.py          # Pure vams-rules-v1 rule logic (no AWS access):
│   │   │                                           #   rule parsing/inheritance, the six rule types
│   │   │                                           #   (pipeline, metadata, relationship, fileStructure,
│   │   │                                           #   versionCoverage, assetAttributes), the metadata
│   │   │                                           #   rule scopes (asset|files|fileAttributes|links,
│   │   │                                           #   SCOPE_ENTITY_TYPE binds each to a metadata-schema
│   │   │                                           #   entity type), tolerances, verdict → state,
│   │   │                                           #   execution_failure_reason() (a failed pipeline
│   │   │                                           #   rule's reason from its execution's error text)
│   │   ├── compliance/executionFailureAccess.py    # THE gate on a failed pipeline rule's
│   │   │                                           #   executionFailureReason (rule results, the
│   │   │                                           #   asset-state quarantineExecutionFailures): every
│   │   │                                           #   compliance read returning one passes it through
│   │   │                                           #   rule_results_for_caller / evaluation_for_caller
│   │   │                                           #   / state_record_for_caller with one scope per
│   │   │                                           #   request; kept only for a caller the execution
│   │   │                                           #   details route would serve (Tier-1 on its path +
│   │   │                                           #   workflows/executionAccess), ≤50 per request
│   │   ├── compliance/knownDatabase.py             # database_known(): whether an id names a database
│   │   │                                           #   (GLOBAL, a database row, or a deleted one's
│   │   │                                           #   `<db>#deleted` row or retirement record); the
│   │   │                                           #   database-level reads (overview; quarantine,
│   │   │                                           #   cascade, audit listings by databaseId) answer
│   │   │                                           #   400 database_not_found() after their own check
│   │   │                                           #   on the id; the quarantine and audit listings
│   │   │                                           #   make that check before any read of the id and
│   │   │                                           #   answer a refused caller one empty page with
│   │   │                                           #   no token, as for an unknown id (D175);
│   │   │                                           #   decoded_scope()/validate_scope() let
│   │   │                                           #   the history reads take `<db>%23deleted`
│   │   ├── compliance/databaseRetirement.py        # A deleted database's compliance data moves under
│   │   │                                           #   `<db>#deleted` (D115): begin() (inside the delete:
│   │   │                                           #   retirement record `databaseRetirement#<db>` in the
│   │   │                                           #   evaluation table, aborts pending cascades, ends
│   │   │                                           #   waiting pipeline rules), run() (paged, resumable,
│   │   │                                           #   idempotent move, phase by phase, driven by
│   │   │                                           #   move_message on the job queue; begin_run()
│   │   │                                           #   counts runs against run_limit(), from the
│   │   │                                           #   tables' item counts; a run past it is
│   │   │                                           #   stopped, stop_at_run_limit()),
│   │   │                                           #   move_in_progress()/moving_response() (the 409 every
│   │   │                                           #   compliance action on the id answers meanwhile),
│   │   │                                           #   Presenter (listings show moving rows under #deleted;
│   │   │                                           #   prime()/retirement_states() read a page's records
│   │   │                                           #   with one BatchGetItem, D133; deleted_at() /
│   │   │                                           #   last_deleted_at(): the id's last delete, D136)
│   │   ├── compliance/quarantineGuard.py           # quarantine-blocks-download guard shared by the four
│   │   │                                           #   asset download Lambdas; resolves the asset-state
│   │   │                                           #   table only when COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD=true;
│   │   │                                           #   refuses with QuarantineBlockedResponse (400, body
│   │   │                                           #   exactly QUARANTINE_BLOCK_MESSAGE, no "VAMS General
│   │   │                                           #   Error: " prefix, D169); each handler catches it
│   │   │                                           #   ahead of VAMSGeneralErrorResponse and answers with
│   │   │                                           #   quarantine_refusal(): one INFO line (database,
│   │   │                                           #   asset, route), no traceback (D174); the single-
│   │   │                                           #   object routes pass the resolved key and
│   │   │                                           #   is_preview_object() serves preview images (asset
│   │   │                                           #   preview, current {file}.previewFile.{ext}) (#1495)
│   │   ├── workflows/executionAccess.py            # THE execution read rule (workflow GET, captured
│   │   │                                           #   metadata-source databases, every asset read and
│   │   │                                           #   the output asset, deleted asset → its database)
│   │   │                                           #   over injected readers, plus the record readers;
│   │   │                                           #   the execution routes and the compliance
│   │   │                                           #   failure-reason gate both decide through it
│   │   ├── constants.py                            # ABAC policy, allowed values, file blocklists:
│   │   │                                           #   CORE_* lists merged with the add-on + custom lanes
│   │   ├── dynamodb.py                             # query_all_items, query_has_match, batch_get_items,
│   │   │                                           #   to_update_expr, get_asset_object_from_id
│   │   ├── resourceNames.py                        # SSM resource-name resolver + ResourceKeys
│   │   ├── databaseAccess.py                       # DatabaseAccessManager (accessible-database scan + Casbin)
│   │   ├── indexing/documentIds.py                 # OpenSearch doc ids + vector fileVersionKey / segment keys
│   │   ├── indexing/fileEnumeration.py             # enumerate_latest_live_files (reindexers)
│   │   ├── vectorsearch/embeddings.py              # Bedrock embedding adapters; vendored byte-identical into the system pipeline
│   │   ├── vectorsearch/vectorStore.py             # VectorStore protocol + DynamoDbVectorStore (SearchVectors, MAX_TOP_K)
│   │   ├── vectorsearch/fileClassIntent.py         # FILE_CLASSES phrases + query file-type intent (soft boost)
│   │   ├── s3.py                                   # S3 file validation + paged list helpers; per-Region S3 client
│   │   │                                           #   factory and the Region-routing client the asset handlers hold;
│   │   │                                           #   presigned_get_object_params (SVG response overrides)
│   │   ├── s3MetadataKeys.py, s3PathPatterns.py    # Canonical S3 keys, .previewFile. patterns + select_newest_preview_file (mirror web/src/common/constants/fileFormats.ts)
│   │   ├── searchAuthzFilter.py                    # Asset GET policy lines -> OpenSearch upper/lower bound filters per search index (pure)
│   │   ├── dynamoDbMetadataKeys.py                 # Reserved DynamoDB metadata keys
│   │   ├── assetHistory.py, syncTracking.py        # Best-effort history / outbound-sync writers
│   │   ├── assetProvenance.py                      # lastChangeSource/lastChangeWorkflowExecutionId/lastChangeAt
│   │   │                                           #   written on the asset row by every upload completion;
│   │   │                                           #   `assetVersion` stamp from asset-version create/revert;
│   │   │                                           #   `assetEdit` from an asset edit of name/description/tags;
│   │   │                                           #   record_indexed_change (conditional UpdateItem the
│   │   │                                           #   compliance trigger writes from the indexer events):
│   │   │                                           #   `metadataEdit` / `linkChange`, `workflowExecution` for
│   │   │                                           #   a row a workflow write-back marked (metadata and link
│   │   │                                           #   requests write no stamp themselves);
│   │   │                                           #   completion instants lastUploadAt (user upload) and
│   │   │                                           #   lastAssetVersionAt (create/revert), moved by no other writer
│   │   ├── indexerEvents.py                        # s3_records_from_indexer_message: SQS→SNS→S3 unwrapping of a
│   │   │                                           #   file-indexer message, shared by fileIndexer and complianceTrigger
│   │   ├── timestamps.py                           # utc_now(): naive UTC "now" (stored-string format); use instead of datetime.utcnow()
│   │   ├── validators.py                           # validate() dispatcher + regex patterns
│   │   └── workflows/                              # Execution/pipeline/workflow shared helpers (pure); incl. subExecutionStages.py (ASL frame + history → per-stage status), availableLogs.py (log-source identity, dedup, read planning) and executionLogCapture.py (the bounded, redacted execution log stored on an execution record)
│   │       ├── executionRecords.py                 #   storage record builders, keys, S3 prefixes,
│   │       │                                       #   workflow.execution.completed event builder
│   │       ├── executionOutputs.py                 #   output attribution + resolved manifest build; the shared
│   │       │                                       #   sub-process stop helper + ABORTABLE_RESOURCE_TYPES (SFN,
│   │       │                                       #   Batch, Deadline Cloud, SageMaker training job / pipeline
│   │       │                                       #   execution) used by both the abort API and the failure path;
│   │       │                                       #   terminal-status guard + the shared completion-event emitter
│   │       ├── systemRecords.py                    #   isSystem: importer cross-call check + read-only guard rules/messages;
│   │       │                                       #   is_system_record / names_system_record (no compliance pipeline rule on one)
│   │       ├── executionLocks.py                   #   concurrency lock rows for perAsset / perInputFile / perInputFileVersion (sorted all-or-none acquire, holder-conditional release, TTL)
│   │       ├── executeWorkflowInvoke.py            #   the executeWorkflowV2 Invoke of the trigger dispatcher and the reindex launcher: one attempt per call, sent again (at most twice) only after a connection failure before delivery
│   │       └── stepfunctions_builder.py            #   partition-aware ASL builder (Lambda/SQS/EventBridge/DeadlineCloud)
│   ├── customLogging/
│   │   ├── auditLogging.py                         # CloudWatch audit (9 event types, silent-fail)
│   │   └── logger.py                               # safeLogger with sensitive-data redaction
│   ├── customConfigCommon/                         # Override hooks for EXISTING behaviour (MFA check, login profile)
│   ├── custom/                                     # FORK EXTENSION POINT (shipped empty, never edited upstream):
│   │   ├── customApiRoutes.py                      #   CUSTOM_ROUTES, spliced into ALL_API_ROUTES
│   │   ├── customAuditActions.py                   #   audit actions of the fork's routes, merged into common/auditActions.py
│   │   ├── customConstants.py                      #   object types, constraint fields, blocklist entries, log keys
│   │   └── customResourceNames.py                  #   CustomResourceKeys (mirrors infra/common/customResourceParamKeys.ts)
│   ├── customResources/                            # CloudFormation custom-resource handlers (cr.Provider)
│   │   └── getVpcEndpointIps.py                    #   ALB deploy: unique VPC endpoint IPs
│   ├── handlers/                                   # Lambda handlers, one folder per domain
│   │   ├── assets/                                 # assetService.py (GOLD STANDARD),
│   │   │                                           #   assetVersions, assetHistory, etc.
│   │   ├── auth/                                   # apiGatewayAuthorizerRest, apiKeyService,
│   │   │                                           #   constraints, cognito, preTokenGen,
│   │   │                                           #   apiOriginVerifyRotation (secret rotation);
│   │   │                                           #   __init__: request_to_claims() → claims
│   │   ├── authz/__init__.py                       # CasbinEnforcer proxy (ABAC/RBAC)
│   │   ├── compliance/                             # Compliance. API handlers (one Lambda each):
│   │   │                                           #   complianceSchemaService (schemas CRUD incl. DELETE),
│   │   │                                           #   complianceSchemaBindingService (db/asset bindings;
│   │   │                                           #   a database bind/change/unbind saves the binding,
│   │   │                                           #   answers 202 and is applied in the background by
│   │   │                                           #   complianceBindingApply: apply record
│   │   │                                           #   `bindingApply#<db>` in the evaluation table, message
│   │   │                                           #   with ids only, run() pages from the job Lambda
│   │   │                                           #   in a records phase (409 while it runs, queues no
│   │   │                                           #   evaluation) then an evaluations phase (shares on
│   │   │                                           #   the trigger queue, at most
│   │   │                                           #   EVALUATION_OUTSTANDING_LIMIT of its own sent
│   │   │                                           #   and not taken: evaluationsSent counted by the
│   │   │                                           #   job, evaluationsTaken by the trigger per batch,
│   │   │                                           #   count_taken(); D164; holds nothing); stalled is
│   │   │                                           #   derived from its own messages (stop_reason:
│   │   │                                           #   queued step, run in flight, redelivery) and its
│   │   │                                           #   run limit (run_limit(), stopReason), never from
│   │   │                                           #   elapsed time; resume repeats the change; D161),
│   │   │                                           #   complianceEvaluateService (evaluate/sweep/evaluations/
│   │   │                                           #   state), complianceQuarantineService, complianceCascadeService,
│   │   │                                           #   complianceAuditService. Event-driven: complianceJobs
│   │   │                                           #   (the job queue: a deleted database's move and a
│   │   │                                           #   binding change applied in the background, each
│   │   │                                           #   continuing itself; recursive-loop detection off,
│   │   │                                           #   each job's run limit instead), complianceTrigger
│   │   │                                           #   (own SQS queue on the asset + file indexer topics;
│   │   │                                           #   stamps metadata/link changes, evaluates, removes a
│   │   │                                           #   deleted asset's record; automatic_evaluation_target()
│   │   │                                           #   decides live/archived/removed for the trigger,
│   │   │                                           #   the sweeps and the cascade executor), complianceWorkflowCallback (EventBridge
│   │   │                                           #   workflow.execution.completed; then launches the pipeline
│   │   │                                           #   rules waiting for that workflow, PipelineRuleWaitIndex,
│   │   │                                           #   through the execute Lambda), complianceCascadeExecutor
│   │   │                                           #   (async invoke from complianceCascadeService, event {"cascadeId"}),
│   │   │                                           #   complianceDefaultSchemaSeed (cr.Provider custom resource:
│   │   │                                           #   the GLOBAL default schema, a conditional put with
│   │   │                                           #   registeredAt stamped at write, Delete keeps the row;
│   │   │                                           #   records complianceSchemaSeed, D200).
│   │   │                                           #   Shared modules (not Lambdas): complianceEvaluationStore (all
│   │   │                                           #   DynamoDB/S3/Lambda access + run_evaluation; the asset row,
│   │   │                                           #   current-file listing and file-versions table are read only
│   │   │                                           #   for the rule types that use them, and the file-metadata,
│   │   │                                           #   file-attribute and link-metadata rows only for a metadata
│   │   │                                           #   rule of that scope). Asset-state record writes
│   │   │                                           #   increment recordRevision (update_asset_state does
│   │   │                                           #   it; only the name refresh skips it), and one
│   │   │                                           #   computed from a read passes computed_from=<the
│   │   │                                           #   read> to update_asset_state/delete_asset_state
│   │   │                                           #   (conditional on that revision; on a lost
│   │   │                                           #   condition re-read and recompute,
│   │   │                                           #   STATE_WRITE_ATTEMPTS tries, never blind; audit
│   │   │                                           #   from ALL_OLD). A release / exception grant made after an
│   │   │                                           #   evaluation began (manualDecision/-At) outranks
│   │   │                                           #   its outcome (D159). complianceNotifications
│   │   ├── pipelines/                              # Pipeline CRUD (pipelineService = full CRUD;
│   │   │                                           #   pipelineTemplateService = templates + tagSchema)
│   │   ├── workflows/                              # Step Functions workflow mgmt. API-facing:
│   │   │                                           #   workflowService (CRUD), executeWorkflow
│   │   │                                           #   (asset-less multi-file execute), executionService
│   │   │                                           #   (list/abort/details/logs/rerun/permanent; two
│   │   │                                           #   functions: EXECUTION_SERVICE_ROUTES=service
│   │   │                                           #   serves all but permanent delete, =permanentDelete
│   │   │                                           #   only that route, on the one role that deletes rows),
│   │   │                                           #   workflowTriggerService, importGlobalPipelineWorkflow.
│   │   │                                           #   sfn/ (SFN/EventBridge-invoked): interimPipelineTracking,
│   │   │                                           #   handleExecutionError, processWorkflowExecutionOutput,
│   │   │                                           #   registerPipelineExecution, workflowTriggerDispatch,
│   │   │                                           #   deadlineCloudJobCallback
│   │   ├── addon/edh/                              # EDH Workstations add-on Lambdas; shared modules in common/ (see handlers/addon/edh/CLAUDE.md)
│   │   ├── indexing/                               # CORE indexing: sqsBucketSync (S3 bucket sync), snsQueuing
│   │   │                                           #   (SNS→SQS shims), crReindexer (row-rewriting reindexer that
│   │   │                                           #   feeds every indexer family). Imports no search family.
│   │   ├── osSemanticSearch/                       # OpenSearch family: search (GET/POST /search), osFileIndexer +
│   │   │                                           #   osAssetIndexer (stream consumers writing the OpenSearch indexes)
│   │   ├── osVectorSearch/                         # Vector family: vectorIndexer (single writer of the vector table),
│   │   │                                           #   vectorReindexer (clear/enqueue/both; also a CDK custom resource),
│   │   │                                           #   systemWorkflowLauncher (SQS → executeWorkflow, one reindex slot per run),
│   │   │                                           #   vectorSearchService (POST /search/nlp)
│   │   ├── addon/garnetFramework/                  # Garnet NGSI-LD indexer Lambdas
│   │   ├── addon/physna/                           # Physna Sync Lambdas (physnaCommon.py shared)
│   │   ├── custom/                                 # FORK EXTENSION POINT: fork handlers (handlers.custom.<name>)
│   │   └── assetLinks, comments, config, databases, metadata,
│   │       metadataschema, roles, sendEmail, subscription, tags,
│   │       tagTypes, userRoles                     # Domain handlers (folder per domain)
│   └── models/                                     # Pydantic v1 models, one file per domain
│       ├── assetsV3.py                             # GOLD STANDARD model file
│       ├── addon/                                  # Add-on models (physnaViewer.py, edh.py)
│       ├── common.py                               # Response helpers, APIGatewayProxyResponseV2
│       ├── compliance.py                           # vams-rules-v1 schema body + compliance request models
│       ├── custom/                                 # FORK EXTENSION POINT: fork models
│       └── apiKeys, pipelines, workflows, executions, assetHistory, [domain].py
├── lambdaLayers/                                   # Lambda layer definitions
└── tests/                                          # See backend/tests/CLAUDE.md
```

---

## Critical Rules

1.  **ALWAYS use Pydantic v1 syntax.** This project uses `pydantic==1.10.13`. Never call
    v2 APIs (`model_validate`, `model_dump`, `ConfigDict`). Use `@root_validator`,
    `@validator`, `Field(...)`, `extra='ignore'`.

2.  **ALWAYS import BaseModel from aws_lambda_powertools**, not from pydantic directly:
    `from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError`.

3.  **ALWAYS use the `validate()` dispatcher** from `common.validators` for complex validation
    in `@root_validator` methods. Never write raw regex validation inline.

4.  **ALWAYS enforce two-level authorization**: `enforceAPI()` for route access, then
    `enforce()` for object-level access inside method handlers. **Fail closed on an empty
    token list** — empty `claims_and_roles["tokens"]` means no authenticated identity, so
    authorization cannot be evaluated and must deny.

    -   **Tier 1**: pre-set `method_allowed_on_api = False`, then only flip it inside
        `if len(tokens) > 0: ...enforceAPI()...`; deny if the flag is still `False`. The
        empty-token case naturally denies.
    -   **Tier 2, single resource**: guard with an explicit
        `if len(claims_and_roles["tokens"]) == 0: return authorization_error()` **before** the
        `enforce()` call. **Never** wrap a single-resource `enforce()` in `if len(tokens) > 0:`
        without an `else` that denies — that silently skips authorization and falls through to
        the response/mutation when tokens are empty.
    -   **Tier 2, list filtering**: the exception. Handlers that _append_ an item only when
        `enforce()` passes are fail-closed by construction (empty tokens → empty result).

5.  **ALWAYS use safeLogger** from `customLogging.logger`. Never `print()` or raw `logging.getLogger()`.

6.  **ALWAYS wrap AWS clients with retry config** at module level:
    `retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})`.

7.  **ALWAYS raise `VAMSGeneralErrorResponse`** for business logic errors. Never return raw dicts with status codes.

8.  **ALWAYS use `extra='ignore'`** on every Pydantic model class to silently drop unexpected fields.

9.  **NEVER log sensitive data.** `safeLogger` auto-redacts the credential keys `authorization`, `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`, `apiKey`, `apiKeySecret`, `rawKey`, the credential headers `x-vams-origin-verify`, `cookie`, `set-cookie`, `x-api-key`, `proxy-authorization`, `x-amz-security-token`, the authorizer context key `vams:originVerifyDigest`, the credential field names `password`, `secret`, `clientSecret`, `client_secret`, `accessToken`, `access_token`, `refreshToken`, `refresh_token`, `privateKey`, `private_key`, the credential keys the add-on and fork constants lanes register (`ADDON_LOG_SENSITIVE_KEYS`, `CUSTOM_LOG_SENSITIVE_KEYS`; see "Fork Extension Points"), and the caller-authored content keys `configBody`, `templateTags`, `tagValues`, `customTemplateOverride`, `webFormJson`, `inputInstructions` (also inside a JSON-string request `body`), at every nesting level in dicts and lists. Do not circumvent this. The redaction is key-driven, so an f-string that interpolates a payload value bypasses it — log identifiers and counts, never rendered bodies or tag values.

10. **ALWAYS resolve resource names at module level** via `get_table_name()`, `get_bucket_name()`, or `get_log_group_name()` from `common.resourceNames` inside a `try/except`. Never read `os.environ["TABLE_NAME"]` for resource names in non-pipeline handlers — SSM resolution provides centralized name management with env-var overrides for testing.

11. **NEVER echo request input into error messages returned to the client.** Keep response
    messages generic — no user-supplied values (IDs, names, paths, etc.) and no internal
    details (other databases' IDs, ARNs, stack traces). Log specifics via `logger` for
    debugging; the caller gets a generic message. Example: `logger.info(f"pipelineId {pipeline_id} conflicts with database {other_db}")` before
    `return validation_error(body={'message': "Pipeline ID is already in use by another database. Choose a different ID."}, event=event)`.

12. **ALWAYS dispatch API requests via the master route constants.** Handlers routing on
    `event['requestContext']['http']['path']` must match against the `ApiRoute` constants
    from `common/apiRoutes.py` (e.g. `API_LIST_FILES.matches(path)`), never against
    hard-coded path fragments (`path.endswith('/listFiles')`). When adding or renaming an
    API path, define the `ApiRoute` constant in `common/apiRoutes.py` AND add it to the
    matching category group array (`ASSET_FILE_ROUTES`, `AUTH_ROUTES`, …) so it is included
    in `ALL_API_ROUTES` and served by the `GET /auth/routes/api` listing. Keep route
    templates in sync with the routes attached in the CDK api builder stacks. An add-on's
    routes go in `common/addonApiRoutes.py` (`ADDON_ROUTES`); a fork's go in
    `custom/customApiRoutes.py` (`CUSTOM_ROUTES`) and never in `apiRoutes.py` (see "Fork
    extension points" below). Route lists import `ApiRoute` and the method constants from
    `common/apiRouteTypes.py`, never from `apiRoutes.py`, which imports them.

13. **ALWAYS use a leading `/` for normalized asset-relative file paths.** Normalized file
    paths (DynamoDB composite keys like `databaseId:assetId:filePath`, the `filePath`
    attribute, file-path provenance values) are asset-relative and begin with a single `/`
    (e.g. `/folder/file.txt`). Normalize inputs before storing or comparing:
    `file_path = "/" + raw_path.lstrip("/")`.

14. **NEVER read only the first page of an S3 or DynamoDB listing when the full set is
    needed.** S3 `list_object_versions` / `list_objects_v2` and DynamoDB queries cap a
    single call (`MaxKeys`, `Limit`, one page). When completeness matters, page to
    exhaustion via the shared helpers `common.s3.list_all_object_versions()` /
    `list_all_objects()` (page-size constants `S3_VERSIONS_PAGE_SIZE` /
    `S3_OBJECTS_PAGE_SIZE`; both accept an optional `max_keys` / `max_objects` cap for
    best-effort sampling). A bare `list_object_versions(..., MaxKeys=N)` silently drops
    versions beyond `N` (wrong archive status, truncated history). Existence-only checks
    (`MaxKeys=1`) are the allowed exception.

    **`MaxKeys=1` is sufficient for an existence check only because of three
    `ListObjectVersions` properties, and each one is load-bearing.** `max-keys` caps
    ENTRIES rather than distinct keys and will truncate mid-key; keys come back
    ascending, so a key precedes every longer sibling the same `Prefix` returns; and
    within one key the newest version comes first. Together those make the single entry
    the queried key's CURRENT version — which is why matching `entry['Key'] == key` (not
    merely "the page is non-empty") is what makes the answer conclusive, and why a
    sibling can only take the slot when the key itself has no entries, where a negative
    answer is correct anyway. Two ways to void this without touching the `MaxKeys`:
    passing a `Delimiter` (a `CommonPrefixes` entry can then occupy the slot), or passing
    `EncodingType='url'` (the returned `Key` is percent-encoded, so the equality test
    silently stops matching and **every** archived key reports not-archived). Neither is
    passed today; add either and the predicate needs rewriting, not adjusting.

    **A DynamoDB `FilterExpression` is applied AFTER the page is read, so a single
    filtered call is not a lookup and not an existence check.** DynamoDB reads up to
    1 MB (or `Limit` items), then discards the non-matching ones. Empty `Items`
    alongside a present `LastEvaluatedKey` is therefore the normal shape for "the match
    is on a later page" — it does not mean "no such item". Three consequences, each of
    which has occurred in this codebase:

    -   `len(response['Items']) > 0` as an existence test is a **false negative** once
        the table outgrows one page. The caller then accepts a duplicate, accepts a link
        that closes a cycle, or treats a node with children as a leaf.
    -   A filtered `scan` used to fetch **one** row by a non-key attribute returns `None`
        for a row that exists. Query a GSI on that attribute instead of scanning.
    -   `Limit=N` bounds items **evaluated**, not items returned, so a narrow filter over
        a wide window legitimately returns an empty page plus a `NextToken`. A caller that
        stops at the first empty page loses everything after it.

    This is the one place the S3 exception above does not carry over: S3 applies `Prefix`
    server-side before `MaxKeys`, so `MaxKeys=1` genuinely answers "does this exist".
    DynamoDB's filter does not work that way. See the paging patterns under
    [DynamoDB Patterns](#pagination-patterns) for the two correct shapes.

    What a `Prefix` + `MaxKeys=1` listing answers is "does anything under this prefix
    exist" — not "does this exact key exist". `Prefix` is a prefix match, so the listing
    also returns longer keys (`file.glb` matches `file.glb.previewFile.png`). Counting the
    returned entries therefore attributes a sibling's state to the requested key; match
    each entry's `Key` back to the key being asked about.

            **To check whether a single key or specific `versionId` is archived, do NOT list
            versions** — use `common.s3.is_object_version_archived(bucket, key, version_id,

        client=...)`. It issues one `HeadObject`(delete-marker → 405, live → 200, missing →

    404), O(1) regardless of version count. Handler-local`is_file_archived` helpers must
    delegate to it, never re-implement a version scan.

15. **Paginate large GET responses; never return an unbounded in-memory set.** A response
    that can exceed the AWS Lambda synchronous response limit (6 MB) must page externally:
    accept `maxItems`/`pageSize`/`startingToken` and return `NextToken`, defaulting sizes
    to named constants (mirror the asset-listing and metadata-listing handlers). Do not
    use DynamoDB `paginator.build_full_result()` to accumulate every record for a
    user-facing GET. When response ordering or enrichment requires the full set first
    (e.g. metadata-schema injection/ordering), enrich the full set, then offset-slice to
    the page. Limits that bound response size or protect Lambda runtime (e.g.
    `MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST`, worker-pool caps) stay as named constants with
    a rationale comment — keep them. A count cap does not bound a response whose items vary
    in size: upload initialization (`uploadFile.py` `initialize_upload`) also estimates its
    presigned-URL response from each file's part count and key
    (`MAX_UPLOAD_INITIALIZE_RESPONSE_BYTES` and the overhead constants in `models/assetsV3.py`)
    and refuses an over-budget request with a generic `400` before it creates any multipart
    upload.

16. **Normalize the event before reading `requestContext['http']`, `pathParameters`, or
    `queryStringParameters`.** The REST API (v1) proxy event differs from the HTTP API v2
    layout handlers are written against in two ways that `normalize_event(event)` (from
    `common.auth.apiEvent`) reconciles — it mutates the event in place, is idempotent, and
    is a no-op for internal `lambdaCrossCall` events:

    1.  **`requestContext.http` block.** Handlers read `event['requestContext']['http']['path']` / `['method']` / `['sourceIp']`, but the REST event exposes these as top-level `path` / `httpMethod` and `requestContext.identity.sourceIp`. `normalize_event` injects the v2-style block. The injected path is stage-relative on every host: through the optional REST API custom domain the event's `path` keeps the base path (`/api/database` where execute-api delivers `/database`), and `normalize_event` removes that one segment when `requestContext.domainName` is the configured domain (`API_CUSTOM_DOMAIN_NAME` / `API_CUSTOM_DOMAIN_BASE_PATH`, set by the CDK only when the domain is enabled). The top-level `path` is left as delivered, so dispatch on `requestContext.http.path`, never on `event['path']`.
    2.  **Null `pathParameters` / `queryStringParameters`.** The REST event sends explicit JSON `null` when there are none, so `event.get('pathParameters', {})` returns `None` (the default applies only when the key is **absent**, not when it is present-but-`null`). A handler that then does `path_params['id']` crashes with `TypeError` → 500. `normalize_event` coerces present-but-`null` to `{}`.

    `request_to_claims(event)` calls `normalize_event(event)` internally, so a handler whose **first** event access is `request_to_claims(event)` — the Gold Standard pattern — is already covered. A handler that reads `requestContext['http']`, `pathParameters`, or `queryStringParameters` **before** calling `request_to_claims` MUST call `normalize_event(event)` as the first statement of `lambda_handler`. Skipping it makes the handler 500 on a real REST request — a failure invisible to CDK synth and to unit tests that hand-build a v2-shaped event. Cover the REST-shaped event, including `null` params, in tests.

    ```python
    # ✅ Gold Standard — claims first; normalize is implicit
    def lambda_handler(event, context):
        claims_and_roles = request_to_claims(event)      # normalizes internally
        path = event['requestContext']['http']['path']   # safe — already normalized

    # ✅ When http MUST be read before claims — call normalize_event first
    from common.auth.apiEvent import normalize_event
    def lambda_handler(event, context):
        normalize_event(event)
        path = event['requestContext']['http']['path']
        claims_and_roles = request_to_claims(event)
    ```

---

## Gold Standard Handler Pattern

Reference: `backend/handlers/assets/assetService.py`

Every new Lambda handler MUST follow this exact structure. For a fill-in-the-blanks skeleton, see `backend/HANDLER_TEMPLATES.md`.

### 1. Module-Level Setup

Instantiate AWS clients (with `retry_config`), the logger, and every DynamoDB table at
import time — never inside the request path. Resolve resource names in one `try/except`
block, wrapping optional resources in inner `try/except KeyError`. Handler-specific env
vars read directly from `os.environ`. Full boilerplate lives in `backend/HANDLER_TEMPLATES.md`; the module-load contract is:

```python
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="YourServiceName")
claims_and_roles = {}

try:
    your_table_name = get_table_name(ResourceKeys.YOUR_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

your_table = dynamodb.Table(your_table_name)
```

### 2. Lambda Handler Entry Point

`lambda_handler` extracts claims (which also normalizes the event, Rule 16), runs Tier-1 auth, then dispatches by HTTP method inside a `try/except` that maps error types to the response functions in the table below. The Tier-1 block below is load-bearing: the pre-set `method_allowed_on_api = False` ensures the empty-token case denies (Rule 4).

```python
def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    try:
        method = event['requestContext']['http']['method']

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                method_allowed_on_api = True
        if not method_allowed_on_api:
            return authorization_error()

        if method == 'GET':    return handle_get_request(event)
        elif method == 'PUT':  return handle_put_request(event)
        elif method == 'DELETE': return handle_delete_request(event)
        else: return validation_error(body={'message': "Method not allowed"}, event=event)

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

### 3. Method Handlers

Route by path (via `ApiRoute.matches()`) and delegate to per-operation functions. Use `event.get('queryStringParameters', {}) or {}` to defensively coerce a REST `null` (Rule 16).

### 4. Business Logic Functions

Each per-operation function runs the same four steps: (1) validate params with the
`validate()` dispatcher; (2) query DynamoDB; (3) annotate `object__type`, fail-close on
empty tokens, then run Tier-2 `casbin_enforcer.enforce(event, item)`; (4)
`return success(body=...)`.

```python
def get_single_item(event, item_id):
    (valid, message) = validate({'itemId': {'value': item_id, 'validator': 'ID'}})
    if not valid:
        return validation_error(body={'message': message}, event=event)

    item = your_table.get_item(Key={'itemId': item_id}).get('Item')
    if not item:
        return general_error(body={'message': 'Item not found'}, event=event)

    item['object__type'] = 'yourObjectType'
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not CasbinEnforcer(claims_and_roles).enforce(event, item):
        return authorization_error()
    return success(body=item)
```

### 5. Error Handling Hierarchy

| Exception                    | Response Function       | Status Code |
| ---------------------------- | ----------------------- | ----------- |
| `ValidationError` (Pydantic) | `validation_error()`    | 400         |
| `VAMSGeneralErrorResponse`   | `general_error()`       | 400         |
| `Exception` (catch-all)      | `internal_error()`      | 500         |
| Authorization failure        | `authorization_error()` | 403         |

All response functions accept an optional `event=` parameter for audit logging.

`models/common.py` also defines two `VAMSGeneralErrorResponse` subclasses for single-asset routes that
decide Tier-2 before existence (the assetFiles, assetVersions and assetExportService
`get_asset_with_permissions` helpers): `VAMSAuthorizationDeniedResponse` (refused) and
`VAMSAssetNotFoundResponse` (authorized, asset missing). A read route answers them with
`authorization_error()` (403) and `general_error(status_code=404, body={'message': 'Asset not found'})`,
as the asset GET does, by catching them ahead of `VAMSGeneralErrorResponse`. Every assetFiles and
assetVersions route answers `VAMSAuthorizationDeniedResponse` with 403; the write routes catch only it
and keep their 400 for a missing asset. A route that does not catch them keeps its 400 with the same
message. The assetFiles and assetVersions helpers take `include_archived` (set only by read routes
called with `showArchived`): the record comes from `common.dynamodb.get_asset_record` and the Tier-2
object from `asset_authorization_object`, whose stand-in for a missing id carries the archived
`<databaseId>#deleted` id, so a caller refused that database's archived records gets the same 403
for an archived asset as for a missing one. Write routes never pass it.

---

## Pydantic v1 Model Patterns

Reference: `backend/models/assetsV3.py`

### CORRECT Model Definition

Import `BaseModel` from `aws_lambda_powertools.utilities.parser`; declare `extra='ignore'` on the class; use `Field(...)` with `regex=` (loaded from `common.validators`); attach a `@root_validator` for cross-field logic that calls the `validate()` dispatcher.

```python
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
from pydantic import Field
from common.validators import validate, trim_name, id_pattern, object_name_pattern

class CreateItemRequestModel(BaseModel, extra='ignore'):
    databaseId: str = Field(min_length=4, max_length=63, regex=id_pattern)
    itemName:   str = Field(min_length=1, max_length=256, regex=object_name_pattern)
    tags: Optional[list[str]] = []

    # Names and ids trim their surrounding whitespace; `strip_whitespace=` on Field() does nothing
    _trim_names = validator('databaseId', 'itemName', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'tags': {'value': values.get('tags'), 'validator': 'STRING_256_ARRAY', 'optional': True},
        })
        if not valid:
            raise ValueError(message)
        return values
```

### v1 vs v2 API Mapping

| v2 (WRONG)                               | v1 (CORRECT)                                                         |
| ---------------------------------------- | -------------------------------------------------------------------- |
| `from pydantic import BaseModel`         | `from aws_lambda_powertools.utilities.parser import BaseModel`       |
| `model_config = ConfigDict(extra='...')` | `class MyModel(BaseModel, extra='ignore'):`                          |
| `MyModel.model_validate(data)`           | `parse(body, model=MyModel)` (from `aws_lambda_powertools...parser`) |
| `@field_validator('name')`               | `@validator('name')`                                                 |
| `item.model_dump()`                      | `item.dict()`                                                        |
| `Field(pattern=...)`                     | `Field(regex=...)`                                                   |

Every model class must declare `extra='ignore'`.

**`Field()` silently swallows unknown kwargs.** Pydantic v1 collects any keyword it does not
recognize into `FieldInfo.extra` instead of raising, so a v2 spelling like `pattern=` becomes an
inert annotation that validates **nothing** — the model imports cleanly, tests pass, and the
field is unconstrained. `regex=` is the v1 spelling. `strip_whitespace=` is likewise inert on
`Field()` (it is a `class Config` / `constr` option, not a field constraint), so a padded value
reaches the length and regex checks — and storage — exactly as submitted. To confirm a constraint
is live, assert on the parsed field rather than reading the declaration:

```python
assert MyModel.__fields__['databaseId'].field_info.regex is not None   # live
assert not MyModel.__fields__['databaseId'].field_info.extra          # nothing swallowed
```

`tests/models/test_no_dead_field_kwargs.py` enforces this across every model.

### Field Validation Patterns

Common shapes:

-   String with regex: `Field(min_length=4, max_length=63, regex=id_pattern)`
-   Name, id, or free text: wire `common.validators.trim_name` as a `pre=True` validator — `_trim_names = validator('itemName', pre=True, allow_reuse=True)(trim_name)`. It removes the surrounding whitespace run and preserves interior whitespace, and runs before the length and regex checks. On a field that also carries a control-character rule (`pipelineName`, `category`, `workflowName`, `templateName`), declare `models.pipelines.reject_control_characters` as a `pre=True` validator BEFORE the trim — `.strip()` removes a trailing newline, tab or NEL, so a trim declared first turns that rejection into a silent normalization. Never `strip_whitespace=` on the field (inert), and not `anystr_strip_whitespace = True` on the model's `class Config` either — that strips every string on the model, including S3 keys and asset-relative paths, where a trailing space is a legitimate part of the key. A `description` or `comment` on a REQUEST model trims through the same validator, declared separately as `_trim_text` so the source keeps signalling which fields are ids and which are prose; a response or record model does not, because trimming there rewrites a stored row on the way out. `tests/models/test_no_dead_field_kwargs.py` asserts that partition as a rule — a new free-text field must either trim or be named in its `NO_TRIM_FREE_TEXT` map with the reason it keeps its whitespace
-   Optional with default: `Optional[list[str]] = []`, `Optional[str] = None`
-   Numeric constraints: `Field(None, ge=0)`, `Field(None, ge=0, le=10000)`
-   Nested models: `Optional[CurrentVersionModel] = None`

### Parsing Request Bodies

```python
from aws_lambda_powertools.utilities.parser import parse
body = json.loads(event.get('body', '{}'))
request = parse(body, model=CreateItemRequestModel)
```

---

## Authorization System (Casbin ABAC/RBAC)

Reference: `backend/handlers/authz/__init__.py`

### Two-Level Enforcement

Both tiers must deny when `claims_and_roles["tokens"]` is empty (Rule 4). An empty token list is
not "an anonymous user" — it means no identity could be established, so there is nothing to
evaluate policy against and `enforce()` must not be the only thing standing between the request
and the mutation. Copy these three shapes exactly; each makes the empty case deny structurally
rather than by remembering to check.

```python
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims

# ── Tier 1: API-level authorization (in lambda_handler) ────────────────────────
# The flag is pre-set False and only ever flipped INSIDE the token check, so the
# empty-token case cannot reach the dispatch below.
claims_and_roles = request_to_claims(event)

method_allowed_on_api = False
if len(claims_and_roles["tokens"]) > 0:
    if CasbinEnforcer(claims_and_roles).enforceAPI(event):
        method_allowed_on_api = True
if not method_allowed_on_api:
    return authorization_error()

# ── Tier 2: single resource (in method handlers) ───────────────────────────────
# The token guard is a SEPARATE statement before enforce(), never a wrapper around it.
item['object__type'] = 'asset'          # MUST annotate object type before enforce()
if len(claims_and_roles["tokens"]) == 0:
    return authorization_error()
if not CasbinEnforcer(claims_and_roles).enforce(event, item):
    return authorization_error()

# ── Tier 2: list filtering ────────────────────────────────────────────────────
# The one shape that may test tokens as a condition: it APPENDS on success, so empty
# tokens yield an empty result rather than an unfiltered one.
allowed = []
for item in items:
    item['object__type'] = 'asset'
    if len(claims_and_roles["tokens"]) > 0 and casbin_enforcer.enforce(event, item):
        allowed.append(item)
```

```python
# ❌ VIOLATION — the single-resource enforce() gated on tokens with no else that denies.
# When tokens is empty the whole block is skipped and execution falls through to the
# mutation/response below, so the request succeeds unauthorized. This reads as a guard
# and is the opposite of one; it is the exact defect Rule 4 exists to prevent.
if len(claims_and_roles["tokens"]) > 0:
    if not casbin_enforcer.enforce(event, item):
        return authorization_error()

your_table.put_item(Item=item)          # reached with NO authorization when tokens == []
return success(body=item)
```

### Key Concepts

-   **CasbinEnforcer** is a proxy with a **60-second policy cache TTL** per user; policy is stored in DynamoDB (`ConstraintsStorageTable`).
-   **A check walks only the policy lines that can match its object type.** `ObjectTypeRoutingEnforcer` (the enforcer `_create_casbin_enforcer_helper` builds) reads each line's compiled matcher expression when the policy loads and lists the line under the one object type its `regexMatch(r.obj.object__type, '^<type>\\Z')` gate names. A line that is not provably gated on one literal type -- a type name with regex syntax, a gate that is not the first conjunct, a top-level `||`, a rule that does not parse, a wrong field count, the deny-all line -- is shared by every type in its original position. A request of type T walks T's lines plus the shared ones in policy order, and a type no line names is denied without a walk. A skipped line would have evaluated `g()` and a false gate and added only `INDETERMINATE`, so no decision changes; `tests/handlers/authz/test_casbin_object_type_routing.py` compares it with the all-lines `ExpressionCachingEnforcer` over every shipped permission template, the seeded roles and synthetic and hand-written policy sets, and fails when a deny line is dropped or a type misrouted. Keep the object-type gate as the first criterion of every generated line: a line built otherwise is still correct but is walked for every type. A request that is not a `str` subject with a plain-`dict` object carrying a `str` `object__type` walks every line.
-   **Compiled matcher expressions are cached per user, never decisions.** The matcher contains `eval(p.obj_rule)`, so pycasbin re-parses every policy line's inlined rule on every `enforce()` (`core_enforcer.enforce_ex` -> `_get_expression` -> `SimpleEval.__init__` -> `ast.parse`). `ExpressionCachingEnforcer` (the parent of `ObjectTypeRoutingEnforcer`) overrides `_get_expression` with an **instance-level** dict keyed on the full inlined rule string, so each distinct line is parsed once per enforcer and every `enforce()` still evaluates every line it walks. The enforcer is owned by one `CasbinEnforcerService`, so the cache and the routes are per user and die with the service on TTL / MFA invalidation. Keep it that way: the compiled expression holds the enforcer's function map whose `g` is bound to that user's role manager, so a class-level or module-level cache would evaluate one user's role links for another (`tests/handlers/authz/test_casbin_expression_cache.py` pins the instance-level shape, decision parity against a plain `casbin.Enforcer`, cross-user isolation and TTL/MFA freshness). `enforce_ex` is serialized per instance because a compiled `SimpleEval` stores the request's parameters on itself while evaluating; routing swaps the walked list in under the same lock.
-   `request_to_claims(event)` returns `{"tokens": ["userId", ...], "roles": [...], "externalAttributes": [...], "mfaEnabled": bool, "authMethod": "jwt" | "apiKey", "apiKeyId": str | None}`. `authMethod` is `"apiKey"` when the authorizer context carries `vams:authMethod = apiKey` and `"jwt"` otherwise (JWT callers and Lambda cross-calls alike); handlers that mint or seed a credential refuse `"apiKey"` with 403 so one key cannot be chained into another. `apiKeyId` is the id of the key an API-key caller authenticated with (the authorizer's `vams:apiKeyId`), for a handler that decides by the key itself, and `None` for every other caller. `roles` comes from the `vams:roles` authorizer context value, which the authorizer (`common/auth/authorizerCore.py`) resolves from the user roles table with a 60-second per-user cache — so it is populated for every auth mode (Cognito, external OAuth IDP, API key), not only where a Cognito pre-token-generation trigger runs. It is informational for handlers and audit logs: `CasbinEnforcer` re-reads a user's roles from DynamoDB when building policy, so authorization does not depend on it.
-   **Authorizer 401 versus 403 (#1212).** `authenticate_request` tags every unauthorized result with a `failure` kind. `FAILURE_UNAUTHENTICATED` (missing or malformed header; a JWT that is malformed, expired, badly signed, or has the wrong aud/iss/token_use or an unknown kid; an API key that is unknown, disabled, expired, has an unreadable expiry or no userId) makes `apiGatewayAuthorizerRest.lambda_handler` write the audit record and then raise `Exception("Unauthorized")` outside its catch-all, which API Gateway answers with 401. Everything else, `FAILURE_FORBIDDEN` or no kind at all, stays a Deny policy (403): IP not allowed, an API key whose user has no roles, a misconfigured `AUTH_MODE` or verifier, a JWKS/discovery outage (`TokenVerificationUnavailableError`), an API key table outage (`ApiKeyLookupUnavailableError`), any unexpected error. Keep three rules: every API-key "not usable" case gets the same kind as an unknown key (no existence leak); an outage or configuration fault is never `FAILURE_UNAUTHENTICATED` (it must not tell clients to sign in again); the raised message is exactly `Unauthorized` (anything else is a 500) and the internal reason never reaches the client. Tests: `tests/common/auth/test_authorizerCore_failure_kinds.py`, `tests/handlers/auth/test_apiGatewayAuthorizerRest_unauthorized.py`.
-   **The authorizer owns the `vams:` context namespace.** `authenticate_request` copies a verified token's claims into the authorizer context except every key under `RESERVED_CONTEXT_KEY_PREFIX` (`vams:`, matched without regard to case), then sets `vams:tokens` to `[username]` -- `cognito:username`, then `username`, then `sub`, the same value `_lookup_user_roles` and `resolve_mfa_enabled` receive -- together with `vams:roles`, `vams:mfaEnabled` and `vams:externalAttributes` (`[]`). A verified token with no username is `FAILURE_UNAUTHENTICATED`. The API-key path builds its context from the key record alone. A new value the authorizer passes to handlers takes the `vams:` prefix and is written by the authorizer after the claim copy; handlers and `request_to_claims` never treat a token claim as identity, role, MFA or credential-type input. Tests: `tests/common/auth/test_authorizerCore_reserved_claims.py` (context) and `tests/handlers/auth/test_authorizer_identity_decisions.py` (Casbin decisions, self-service API key owner).
-   **Accepted token audiences (#1337).** `verify_cognito_jwt` accepts a token whose `aud` (ID token) or `client_id` (access token) is exactly `APP_CLIENT_ID` or one of `ADDITIONAL_APP_CLIENT_IDS`; `verify_external_jwt` passes `[JWT_AUDIENCE, *JWT_ADDITIONAL_AUDIENCES]` to `pyjwt.decode(audience=...)`. Both additional lists are JSON arrays set by CDK only when `app.authProvider.<provider>.additionalTokenAudiences` is non-empty, parsed fail-closed by `parse_additional_audiences` (malformed value, non-string, blank or `*` entry dropped), and compared by exact equality, never as a pattern. Signature (RS256 only), issuer, expiry and `token_use` checks do not depend on the audience. A Cognito access token without a non-blank `username` (the client_credentials machine-to-machine shape, `sub` = client id) is rejected for every client, and an external token accepted only through an additional audience must carry a user identity (`cognito:username`, `username` or `sub`) that is not its own `client_id`/`azp`/`cid`, and must carry no machine-token marker (`_MACHINE_TOKEN_CLAIM_MARKERS`: `gty` = `client-credentials` (Auth0), `idtyp` = `app` (Entra ID); `_MACHINE_TOKEN_USERNAME_PREFIXES`: `preferred_username` starting `service-account-` (Keycloak)); both are `FAILURE_UNAUTHENTICATED`. Tokens with the primary audience are not checked. The markers are a backstop, not a guarantee: other IdPs' machine tokens may carry none, so only user sign-in clients may be listed. Identity comes from `cognito:username`, then `username` (a Cognito access token), then `sub`, and roles from the user roles table, so a token from an additional client resolves the same user and the same Casbin decision as a web token. `authenticate_request` returns the accepted audience as `tokenAudience` (`token_audience_for_audit`), which the REST authorizer passes to `log_authorization_gateway(..., token_audience=...)`; the audit record carries it as `tokenAudience` on a success only, and it is not added to the authorizer context. Tests: `tests/common/auth/test_authorizerCore_additional_audiences.py`, `tests/handlers/auth/test_apiGatewayAuthorizerRest_token_audience.py`, `tests/common/test_auditLogging_token_audience.py`.
-   **MFA-aware**: roles with `mfaRequired=True` are only active when `mfaEnabled=True` in claims.
-   **Object annotation**: set `item['object__type']` before every `enforce()` call.
-   **Policy lines as data.** `CasbinEnforcer.effective_object_policies(object_type, action)` returns the caller's applicable lines (`effect`, usable `criteriaAnd`/`criteriaOr`) built by the same `_usable_criteria` filter as the policy text, or `None` when the enforcer was not built from them (shared-cache hit, failed build, unloadable text). Search turns them into OpenSearch filters with `common/searchAuthzFilter.py`: an `upper` bound that fetches every readable document and a `lower` bound used for totals and aggregations that cannot be exact, so counts never include a record the caller cannot open. The per-hit `enforce()` stays authoritative. A new operator, constraint field, or change to a rule's regex shape must be mirrored in `searchAuthzFilter.py`; `tests/handlers/authz/test_search_authz_filter.py` compares both bounds with `enforce()` over every field, operator and value shape and fails otherwise. A future search mode (for example vector search) reuses `build_search_authz_filters` / `apply_search_authz_filter` in `handlers/osSemanticSearch/search.py` instead of computing counts from unfiltered results.
-   **Natural-language window signals never depend on records the caller cannot open.** `vectorSearchService._databases_read_in_full` decides which searched databases the caller may open in full: every asset GET policy line constrains `databaseId` alone, and the user's enforcer allows an asset object of the database (also as `<databaseId>#deleted` with `includeArchived`), asked directly so no denial audit entry is written. Every other target is searched with `MAX_TOP_K` whatever `size` and always reported as possibly incomplete (`nlp.truncated` true, `relation` `gte`, `truncated:window`, never `segments:window_full`); only the windows of targets open in full are checked for filling. A new answer field derived from the vector items before `_authorize` follows the same split (`tests/handlers/osVectorSearch/test_vectorSearchService_partial_access_window.py`).
-   **Search fills the page.** `_search_until_page_full` in `handlers/osSemanticSearch/search.py` wraps every dual-index search (POST and simple search): while fewer than `from + size` hits pass the per-hit check and OpenSearch holds more candidates, it fetches a window up to `SEARCH_FILL_GROWTH_FACTOR` times larger, bounded by `OPENSEARCH_MAX_RESULT_WINDOW` per index and `SEARCH_FILL_MAX_SECONDS`; the window count is not a bound (each window is at least twice the last, so the loop ends within a few fetches), so small pages reach candidates ranked anywhere below the per-index cap. The time bound covers the fetches and every per-hit check (`DualIndexResponseProcessor.decide_in_order` stops at the deadline; `_affordable_window` sizes the next window from the measured fetch and check time per hit); a search stopped by a bound answers with the hits checked so far and reports `relation: gte`. For a search over both indexes only the merged prefix in true order is answered (`_ordered_prefix_length`: cut at the earliest last hit of an index whose window was full), so pages neither skip nor repeat records. Larger windows drop `aggs` and keep the first window's aggregations; per-hit decisions are cached per request in `DualIndexResponseProcessor._decide`. Both keyword search handlers and natural-language search (`vectorSearchService.handle_nlp_search`) run inside `deferred_authorization_audit()`, so the refused-hit audit entries are written in batches when the search ends. A new search mode that post-filters hits routes through it instead of slicing one window.
-   **Search sorts are total.** Every keyword search sort, each larger fill window included, ends with `SORT_TIEBREAKER_FIELDS` (`handlers/osSemanticSearch/search.py`: database ID, asset ID and archive state, plus the file path on the file index), appended by `with_unique_tiebreaker`, so records equal on every key the caller gives keep one order from page to page. The merge of a search over both indexes applies the file query's sort, whose keys extend the asset keys, and orders a missing value last and a list by its smallest (ascending) or largest (descending) value, as OpenSearch does. A new sort path appends the keys too. Do not sort on `_id`: it needs field data for the id field, which a domain can turn off.
-   **Indexers re-read the source after a write.** A late or re-sent write must not leave a document for a record deleted or archived meanwhile. `process_asset_index_request` (`osAssetIndexer.py`) re-reads the asset row with a strongly consistent read after a successful write and deletes the document when the row is gone; `reconcile_written_file_document` (`osFileIndexer.py`) re-reads the S3 state and the asset row, then deletes the document or writes it once more from the current state. A failed re-read reports the record for redrive. Conditional or versioned writes are not used, because OpenSearch Serverless and provisioned domains do not offer the same document versioning. A test that stubs the file write also patches `reconcile_written_file_document`.
-   **A query keeps each word in its field.** `query_string` splits its text at unescaped whitespace, so never put a raw value with spaces into it. The general keyword query uses `FieldClassifier.any_word_anywhere_query` (a record matches when any word appears anywhere inside a value); a field filter with wildcards on an analyzed text field uses `every_word_in_field_query` (every word, in that field); a wildcard pattern on a keyword-typed field uses `whole_value_pattern` (one pattern for the whole value, whitespace escaped); a contains search of metadata values uses `every_word_anywhere_in_values_query`. A value the caller wrapped in exactly one pair of double quotes is that exact value on every metadata value and simple field filter entry point (#1299): `FieldClassifier.quoted_exact_value` recognizes it, `exact_phrase` escapes it with `*` and `?` literal, and `split_metadata_query` keeps an AND or OR separator inside it in the value. Never escape the caller's quotes into the searched value.
-   **Flat-object value fields depend on the query, not the engine.** `MD_` and `AB_` are `flat_object` fields. Name only the root (`MD_`, `AB_`) or a key (`MD_.<key>`) for an exact or prefix term; both engines rewrite those onto the values. Neither engine rewrites any other wildcard pattern (a leading `*`, a `?`, an inner `*`): on the root it matches the key paths and on a key nothing, so it names `<root>._value` for a value and `<root>._valueAndPath` (`MD_._valueAndPath:MD_.<key>\=<pattern>`) for one key's value. `FieldClassifier.flat_object_value_fields` and `flat_object_key_pattern_query` make that choice; use them, and do not branch on `OPENSEARCH_TYPE` or the engine version. Confirmed on OpenSearch Serverless and on a provisioned OpenSearch 3.5 domain; the 2.19 engine of the EU Sovereign Cloud has not been checked. Never hardcode `MD_._value`: an exact or prefix query on it matches nothing on either engine.
-   Valid object types (`ALLOWED_CONSTRAINT_OBJECT_TYPES` in `common/constants.py`, and the only values `GET /auth/constraints/permissionObjects` offers): `database`, `asset`, `api`, `web`, `tag`, `tagType`, `role`, `userRole`, `pipeline`, `workflow`, `metadataSchema`, `complianceSchema`, `complianceEvaluation`, `complianceCascade`, `complianceBinding`, plus any type the add-on or fork constants lane registers (see "Fork Extension Points"). Anything else is rejected at constraint create/update with a `400`. There is no `apiKey` object type — API key access is governed at Tier 1 through `api` route constraints on `/auth/api-keys*` and `/auth/user/api-keys*`. One refusal runs in addition to Tier 1, after `enforceAPI` has allowed the route: a caller authenticating with a temporary session key — an API key named with `TEMPORARY_SESSION_KEY_NAME_PREFIX` (`temporary-session-`, `common/constants.py`) that a backend component mints for one session through a Lambda cross-call, identified through the `apiKeyId` that `request_to_claims` surfaces — is answered `403` on API-key create and update in every scope, so a session credential cannot mint or extend a key (a caller whose own key record cannot be read is answered the generic `500`, not that refusal). The prefix is reserved: an API-key create that names a key under it is answered `400` unless it arrives as a Lambda cross-call (`tests/handlers/auth/test_apiKeyService_temporary_session_key_callers.py`).

### System User (`SYSTEM_USER`)

`SYSTEM_USER` is the **only** valid user ID for system-process actions — never `SYSTEM`, `system`, or any other variant. It is seeded into the user and user-roles tables during CDK deployment and assigned to the `admin` role, so actions attributed to it pass Casbin authorization. Use it for: Lambda cross-calls (`{'lambdaCrossCall': {'userName': 'SYSTEM_USER'}}` — also the default in `request_to_claims()` when `userName` is omitted); username fallbacks (`claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]`); provenance/audit values (`createdBy`, `modifiedBy`, `changeUserId` fallbacks); and identity comparisons (`skip_schema_validation = (username == "SYSTEM_USER")` in `metadataService.py`, the pipeline-execution bypass in `processWorkflowExecutionOutput.py`). Off-schema keys a pipeline stores this way pass `restrictMetadataOutsideSchemas` on later user writes: every enforcing write path hands `validate_metadata_keys_against_schema` the record's keys as stored before the write (`stored_keys`), so only a key new to the record and absent from the schema is refused. Value type and controlled-list checks follow the same rule: every user write path passes `validate_metadata_against_schema` `value_check_keys=keys_set_by_write(incoming, stored)`, so a stored value the schema refuses blocks no write that leaves it unchanged, while required-field and dependsOn checks still read the merged record.

Handlers compare against this exact string, so a mismatched variant silently fails the comparison (or attributes records to a user ID with no admin role). IAM permissions on direct Lambda invocation are the security boundary for who can inject a `lambdaCrossCall` event.

### Internal Actions (Direct Invokes No API Route Reaches)

A handler that serves another VAMS Lambda an operation with no API route dispatches it from a top-level event key before any API handling: `workflowService` serves `vamsInternalAction` (`refreshPipelineReferences`, `redeployWorkflow`; names and the event builder in `common/workflows/workflowRecords.py`). The action runs only when the event is a `SYSTEM_USER` `lambdaCrossCall` with **no** `requestContext` — API Gateway builds every proxy event with a `requestContext` and cannot add top-level keys, so no route reaches it — and its payload ids pass the `ID` validator. Derive the targets server-side from the payload's own entity (the refresh takes a pipeline and finds its workflows) rather than accepting a target list, and grant callers `lambda:InvokeFunction` on the handler's function ARN only.

The same top-level-envelope rule carries the system-record exemption. `common/workflows/systemRecords.is_schema_import_call` is true only for a `lambdaCrossCall` whose `userName` is `SYSTEM_USER` **and** whose `source` is `vamsSchemaImport`, which only `importGlobalPipelineWorkflow` sends; that caller alone may set `isSystem` (a JSON boolean, read from the raw body because the request models ignore the key) or change a system record. The four guarded services (`pipelineService`, `pipelineTemplateService`, `workflowService`, `workflowTriggerService`) run the guard after Tier-2 authorization and before any write, refuse with a `400` that names the field or action, and refuse a request that mixes an allowed change with a refused one as a whole. Because the exemption rests on who can invoke these functions directly, a new `lambda:InvokeFunction` grant on any of them, or on the import function, widens who can register or change system records.

`executeWorkflow` applies the same envelope rule to the trigger type. A `triggerType` other than `manual` (`fileUpload`, `systemReindex`) is accepted only on a `SYSTEM_USER` `lambdaCrossCall` (`_is_system_cross_call`), which the upload trigger dispatcher and the vector reindex launcher send; any other request that names one is refused with `400`.

### Workflow State Machine Deployment

A workflow's AWS Step Functions definition encodes each referenced pipeline's execution settings, so it must be rebuilt when any of them changes. The contract:

-   **One deploy routine.** Create, update, the pipeline-change refresh and the launch-time check all deploy through `workflowService._deploy_fields`, which calls `workflowAsl.deploy_state_machine` and returns the attributes written together: `workflow_arn`, `workflowVersionArn` (every deploy publishes a version; older ones past `MAX_RETAINED_STATE_MACHINE_VERSIONS` are deleted), `jobNames` (reused while the steps are unchanged, so output prefixes do not move), `pipelineFingerprints` (`{"pipelineDatabaseId:pipelineId": sha256}` of `workflowAsl.to_asl_pipeline_dict` minus `name`) and `deploymentRevision`. The refresh writes conditionally on the revision it read.
-   **Everything the ASL builders read from a pipeline record goes through `to_asl_pipeline_dict`.** That dict is what is fingerprinted; a setting read around it would change the definition without changing the fingerprint, and no workflow would be redeployed for it.
-   **A pipeline change refreshes its workflows.** `pipelineService.update_pipeline` compares fingerprints before and after the save and, on a difference, invokes `refreshPipelineReferences` asynchronously; the workflow service walks every non-archived referencing workflow to exhaustion. A failed redeploy sets `deploymentStale` + `deploymentStaleReason` on the workflow.
-   **The redeploy path reads strongly consistent.** `workflowService.redeploy_workflow` (behind both `refreshPipelineReferences` and `redeployWorkflow`) reads the workflow and each pipeline with `ConsistentRead=True` (the `consistent_read` parameter of `get_workflow_item`, `_get_pipeline_record` and `_resolve_snapshot_pipeline_records`). The refresh is invoked right after the pipeline write, and the re-read after a lost conditional write must see the other writer's deployment; an eventually consistent read can return the previous row in either place, so the walk finds every workflow "current" and redeploys nothing. API reads keep the default.
-   **A launch never runs a stale definition.** `executeWorkflow._ensure_current_deployment` compares the stored fingerprints with the live pipeline records (a record without a version or fingerprints never matches), invokes `redeployWorkflow` synchronously on a mismatch, and starts on `workflowVersionArn`; when the deployment cannot be made current it returns `409` and starts nothing. The main execution row records `workflowVersionArn`.
-   **Settings read at launch need no redeploy.** `systemConfig` (filters, arity, metadata gates, viewer suffix), templates, and enabled/archived state reach each run through the Step Functions input or the execute handler's own reads; keep it that way rather than baking them into the ASL.

### Workflow Execution Locks

A workflow's `perAsset`, `perInputFile` and `perInputFileVersion` concurrency restrictions are lock rows in `WorkflowExecutionLocksStorageTable` (PK `lockKey`, TTL `expiresAt`, holder index `WorkflowExecutionLocksByHolderGSI` on `workflowExecutionId`), one row per distinct asset, file or file version a launch selects. `common/workflows/executionLocks.py` holds the contract:

-   **Acquire.** `executeWorkflow` alone takes locks (`acquire_locks`): conditional `PutItem`s in sorted key order, all or none, immediately before the launch's first side effect. A conflict answers `400` with the scope's fixed message; the contested key and its holder go to the service log only. A conflicting row whose holder has no main execution row and whose `acquiredAt` is older than the function's `EXECUTE_WORKFLOW_TIMEOUT_SECONDS` plus `LOCK_TAKEOVER_MARGIN_SECONDS` is taken over with a put conditional on that same holder.
-   **Release.** The end-state step, the error handler, the abort route and the executions read path (for a run stopped outside VAMS) call `release_locks_for_execution`, which deletes the union of the keys rebuilt from the execution's input rows and the rows the holder index lists, each delete conditional on the holder, and never raises. A launch that fails after acquiring releases from memory with `release_locks`.
-   **Release, then announce.** Every terminal writer releases before it publishes `workflow.execution.completed` (the end-state step's `record_execution_outputs` emits after its `finally` release, the error handler and the abort after theirs, the read-path reconcilers after `_settle_reconciled_terminal`), so a consumer that relaunches on the event (the compliance callback's waiting pipeline rules) is not refused again by the run that ended. An abort whose stop is unconfirmed announces while the run keeps its locks; a rule refused then waits for the next completion over its asset or its wait bound.
-   **Only a run that has stopped releases.** The abort and a failed launch release only when `StopExecution` succeeded or `sfn_execution_has_ended` shows the execution ended; otherwise the rows wait for a terminal step or the TTL (the sum of the pipelines' timeouts, at least a day, plus 30 minutes).
-   **Reindex slots.** A launch by the reindex launcher (`SYSTEM_USER` cross-call, `triggerType` `systemReindex`, `lambdaCrossCall.reindexSlots` `{count, candidates}`, which API Gateway cannot set) also claims one of the workflow's `count` slot rows, `{workflowDatabaseId}:{workflowId}|reindexSlot|{n}` (`acquire_reindex_slot`), after its locks and before the first side effect; with every candidate held it releases those locks and answers `429` `REINDEX_SLOTS_BUSY_MESSAGE`, which the launcher treats as a wait (it sends the message again, delayed 30 s doubling to 900 s, and lets the original be deleted). The slot is released with the locks: from memory on a failed launch, and by every release site through the holder index. A slot row expires after `reindex_slot_ttl_seconds` (the pipelines' timeouts plus 30 minutes, no day floor); a held one older than the takeover age is taken over when `_holder_still_running` finds no record, an ended record, or an ended state machine. Uploads, manual and API launches never claim one.
-   **Grants** sit in each lock handler's own `ExecutionLockPolicy` (`attachExecutionLockPolicy` in `infra/lib/lambdaBuilder/workflowFunctions.ts`), never its role's default policy.
-   **Skip records.** A conflict on a `fileUpload` trigger launch (`triggerType` File-Upload, executing user `SYSTEM_USER`) also writes a `SKIPPED` execution (`executeWorkflow._record_skipped_launch`), best-effort and with the same `400`; a manual or API launch writes nothing. The id is `er.skip_record_execution_id(lockKey, holder)` (uuid5), so every refusal by one holder on one key lands on one record. Rows are written in this order, so the main row (which lists and details read) exists only once its authorization rows do: (1) the configuration row (`build_workflow_configuration_record` with the trigger's output target and `pipelineExecutionParameters`, the trigger's template parameters a re-run replays), a put conditional on `attribute_not_exists`, whose existing item supplies the first-skip date to later refusals; (2) the workflow-input rows (`er.build_workflow_execution_input_record`, never pinned, dated with that first-skip date) while the main row's `skippedLaunchCount` is below `MAX_SKIP_RECORD_INPUT_ROWS` (100); (3) the main row, `er.build_skip_record_update`: one `UpdateItem` that sets the identity fields `if_not_exists`, overwrites `lastSkippedDate` and `ADD`s one to `skippedLaunchCount`. A failure in (1) or (2) writes no main row. No pipeline or lock row is written, and a conflict with no known holder records nothing. `SKIPPED` is in both `TERMINAL_STATUSES` tuples, so no reconcile or terminal writer rewrites it. Reads: the lists add `er.skip_list_fields` (count, scope, reason; never the holder) to SKIPPED rows only; the details view adds `skip` (`skip_details_block`), whose `blockingExecutionId` is set only when `_caller_may_read_execution` passes the same Tier-2 GET check on the holder (missing and denied look the same); the logs route answers `SKIPPED_EXECUTION_LOG_MESSAGE` without reading CloudWatch; a single abort answers `400` `SKIPPED_EXECUTION_ABORT_MESSAGE` after authorization and writes nothing, and a group abort reports the member as `skipped-terminal`; re-run uses the configuration row's `pipelineExecutionParameters` when the record has no pipeline rows. Both list routes take `excludeStatus` (`models.executions.parse_list_status_filter`: known statuses only, no conflict with `status`, fixed 400s): the global list adds it to the FilterExpression (no signal beyond the `status` filter); the asset list drops a candidate only after it has passed the per-execution read check (`_authorize_candidate`, memoized and shared with the authorization pass), so a candidate the caller may not read is handled exactly as without the parameter; at most `MAX_EXECUTIONS_INSPECTED` drops per page (a spent budget stops with a cursor and a warning), re-applied after reconcile. The execute role's main-table `GetItem`/`UpdateItem` and configuration/input `PutItem` cover the writer (`test_execute_workflow_dynamodb_calls.py`).

### Workflow Execution Input Metadata: Opt-in Sections

`metadataInputs` carries the four metadata types (default ON) and two opt-in keys (default OFF): `assetFileMetadata` and `metadataSchema`. `METADATA_INPUT_DEFAULTS` and `METADATA_INPUT_OPT_IN_KEYS` in `common/workflows/executionRecords.py` define what an omitted key means; `models/pipelines._METADATA_INPUT_KEYS` and `executionValidation._METADATA_KEYS` list all six, and a new key goes into all three in the same change.

-   **Off means unchanged.** With both keys absent or `false` the grouped envelope is byte-identical to the one built before they existed (`tests/handlers/workflows/golden/`). Never default an opt-in key ON.
-   **Gathered only when some step asks.** `executeWorkflow._intake_metadata_inputs` turns an opt-in key on at intake only when the workflow enables it AND a step's effective gate (systemConfig + chosen template overrides) does. `narrow_metadata_envelope` drops the section per step on the step's own gate, and returns the same object when the envelope carries neither section.
-   **`assetFileMetadata`** (`_expand_asset_selections`): a `/` or folder selection is listed with `ListObjectsV2` (current objects only, so archived files never appear), skipping folder markers and `PREVIEW_FILE_PATTERN`; at most `MAX_ASSET_FILE_EXPANSION_PER_SELECTION` (500) per selection, explicit + expanded file records within `MAX_INPUT_FILES_PER_EXECUTION`, and the listing stops counting at `MAX_ASSET_FILE_EXPANSION_SCAN_KEYS`. Each file's reads go through `_fetch_file_metadata` with the request's identity (`_metadata_service_payload`) in the shared worker pool. Records carry `"expanded": true`; the group carries `fileExpansion`. Expanded rows sit last in the total-budget order and are recorded for the step that received the covering selection (`pipeline_metadata_envelope_rows`). The persisted input-metadata row stores `expanded: True` only on such a row (`build_input_metadata_record(expanded=)`), and the details rows report `expanded` on every row (`_scrub_input_metadata`, false when absent), which the CLI table marks `(expanded)` and the web execution page badges.
-   **`metadataSchema`** (`_build_metadata_schemas`): Tier-1 on `GET /metadataschema` for the request's identity, Tier-2 on a provisional schema object (no name, no entity type) in the database and in `GLOBAL`, then the per-schema GET the list endpoint filters by. A database left out for any reason gets one generic warning whose text does not depend on whether a schema exists. The schema records come from `metadataSchemaValidation.load_enabled_schemas` (uncached, so a per-caller filtered aggregate is never cached) and `aggregate_schema_field_catalog`, which resolves each file field per file-extension reach the way `get_aggregated_schemas` filters a single file (one entry per distinct reach; the `[]` entry covers every file no other entry lists), so a conflict is reported only for files that two differing schemas both reach; asset metadata is resolved over every schema (`by_file_extension=False`). The execute role's reads sit in their own `MetadataSchemaReadPolicy` (Query on the schema index, GetItem on the database table).
-   Every cut or omission is an execute warning; neither section ever fails a launch.

### Casbin Policy Model

Defined in `common/constants.py`. Request: `(sub, obj, act)`; policy line: `(sub, obj_rule, act, eft)`; role mapping: `g = _, _`; effect: `some(allow) && !some(deny)`; matcher: `g(r.sub, p.sub) && eval(p.obj_rule) && r.act == p.act`.

Each criterion value is written into its line's `obj_rule` as source escapes, one `\xNN` / `\uNNNN` / `\UNNNNNNNN` per character (`CasbinEnforcerService._escape_rule_value`), never as plain text: Casbin's policy reader splits lines on commas outside brackets, and pycasbin rewrites `p.` / `r.` and `&&` / `||` / `!` throughout the rule text, string literals included, before compiling it. Only the object-type check every line opens with, `regexMatch(r.obj.object__type, '^<type>\Z')`, carries a registered type name as plain text (`_object_type_rule`). `tests/handlers/authz/test_constraint_value_as_written.py` and `test_constraint_value_escape_parity.py` hold this.

### Constraint Fields (`PERMISSION_CONSTRAINT_FIELDS`)

Fields that can be referenced in ABAC policy rules: `databaseId`, `assetName`, `assetType`, `tags`, `tagName`, `tagTypeName`, `roleName`, `userId`, `pipelineId`, `pipelineExecutionType`, `workflowId`, `category`, `name`, `metadataSchemaName`, `metadataSchemaEntityType`, `complianceSchemaName`, `complianceState`, `cascadeId`, `object__type`, `route__path`, plus any field the add-on or fork constants lane registers. The compliance handlers annotate `complianceSchema` objects with `complianceSchemaName`, `complianceEvaluation` objects with `databaseId` + `complianceState`, `complianceCascade` objects with `cascadeId`, and `complianceBinding` objects with `databaseId`. A schema bind or unbind checks `complianceBinding` `PUT`/`DELETE` on the database (an asset binding uses the asset's database), the schema, and a `GET` of the target `database`/`asset` row; it never checks a write on the target, because a binding writes compliance records only.

### Authorization Decisions and Permission-Check Endpoints

Every allow-or-deny decision is made in the API, by the authorizer and `CasbinEnforcer`. The web app may only show or hide a control based on a decision the API returned, such as the route listing from `GET /auth/routes/api/allowed`. The API still enforces each call. Never derive a permission in the client: no capability flag computed from roles, no constraint matching in the browser, and no trust in a role, database or attribute the client sends.

An endpoint that answers "may this user do X" (a permission check, as opposed to the operation itself) follows these practices, from the [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) plus the VAMS rules:

-   **Deny by default.** An unknown object type, an unknown route or a failed lookup returns "not allowed", never an error that hints why.
-   **No existence leaks.** "Doesn't exist" and "not allowed" give the same answer, so the endpoint can't be used to enumerate assets or databases.
-   **Generic reasons only.** The UI may get a reason code for its tooltip ("not permitted"). The response never includes policy details or the constraint that matched.
-   **The result is advisory.** The real call is still enforced on the server, so a stale answer can't grant anything. Cache the answer only briefly in the page, never across users.
-   **Bounded cost.** Use a single-item read per object (no scan or query fan-out, Rules 14-15), validate the ids with the existing validators, and rely on API Gateway throttling.
-   **Audit.** Record denials through the existing batched probe audit (`_DenialAuditBatch` in `handlers/auth/routes.py`, with its probe marker).
-   **Reachability.** Put the endpoint under `/auth/routes/...` (for example `POST /auth/routes/api/evaluate`), behind the custom authorizer. Every shipped template, `database-readonly` included, already grants `POST` on `/auth/routes`, so no template changes are needed.

The check identifies the user from the token, loads the object itself from the identifiers it is given, and evaluates the same Tier 1 and Tier 2 checks as the endpoint it answers for.

**A stored reference that the system later acts on is authorized when it is saved.** A compliance pipeline rule names a workflow that every evaluation launches as the system user, so `complianceSchemaService.pipeline_rules_refused` decides each new or changed `pipelineRef` on schema create and update the way the execute route decides a run (`may_run_workflow`: Tier 1 `POST` on `/workflows/{databaseId}/{workflowId}/execute` through `enforcer.enforce` on an `api` object, Tier 2 `GET` on the workflow, its pipelines and the rule's own pipeline, each row read with one `GetItem`). It is deny by default: a missing workflow or pipeline answers the same `403` (`Not authorized to run the workflow of pipeline rule '<name>'`, `ruleName`) as a refused one and is logged through `log_authorization`. Unchanged rules are not re-checked and `SYSTEM_USER` is not checked. After it, `system_pipeline_rule_refused` refuses (400, `systemRecords.SYSTEM_RECORD_PIPELINE_RULE_MESSAGE`, `ruleName`) a new or changed rule whose workflow, own pipeline or a pipeline its workflow runs is a system record (`systemRecords.names_system_record`), for every caller including `SYSTEM_USER`; `complianceEvaluationStore` never launches such a rule from a schema stored before the check (error result with the same message, and a waiting one is closed). Follow the same shape for any new field that makes the system act later on an object the saver names.

---

## Validators (`common/validators.py`)

### `validate()` Dispatcher

The `validate()` function is the standard way to validate inputs in both `@root_validator`
methods and handler code. Per-field entries take `value`, `validator` (from the table
below), and optional `optional: True` (skip when `None`/empty) and
`allowGlobalKeyword: True` (accept the literal `"GLOBAL"`):

```python
from common.validators import validate

(valid, message) = validate({
    'databaseId': {'value': database_id, 'validator': 'ID', 'allowGlobalKeyword': True},
    'assetId':    {'value': asset_id,    'validator': 'ASSET_ID'},
    'tags':       {'value': tag_list,    'validator': 'STRING_256_ARRAY', 'optional': True},
})
if not valid:
    raise ValueError(message)                                    # In @root_validator
    # OR
    return validation_error(body={'message': message}, event=event)  # In handler
```

The `validator` name must be one the dispatcher implements. `validate()` resolves the check from
`_VALIDATOR_DISPATCH` in `common/validators.py`, so **that mapping is the list of valid names** —
there is no second set to keep in step with it. A name with no entry has no rule, so it raises, which
surfaces as a `500`: it is a code defect, not caller input. Adding a validator means adding one entry
to the mapping, and nothing else.

The name is resolved **after** the empty/optional short-circuits, so an optional field left empty is
skipped before its validator name is consulted — naming a validator is not required to say "there is
nothing here to check". Each entry receives the field name and the whole spec, not just the value,
because a few checks read another key off it (`ASSET_PATH` reads `isFolder`).

### Available Validator Types

Scalar validators: `ID` (`^[-_a-zA-Z0-9]{3,63}$` — databaseId, pipelineId, etc.),
`ASSET_ID` (filename pattern, max 256 chars), `UUID`, `FILE_NAME`,
`OBJECT_NAME` (`^[a-zA-Z0-9\-._\s]{1,256}$` — assetName, dbName, etc.),
`EMAIL`, `USERID` (`^[\w\-\.\+\@]{3,256}$`), `REGEX`, `NUMBER`, `BOOL`,
`RELATIVE_FILE_PATH` (`^\/.*$`), `ASSET_PATH` (`^.+\/.+$`),
`ASSET_PATH_PIPELINE` (`^pipelines\/.+\/.+\/output\/.+\/$`),
`STRING_30`, `STRING_256`, `STRING_16384` (free-form caller text, e.g. `commentBody`), `STRING_JSON`,
`FILE_EXTENSION` (`^[\\.]([a-zA-Z0-9]){1,7}$`).

The file-key validators — `RELATIVE_FILE_PATH`, `RELATIVE_FILE_PATH_ARRAY`, `DOWNLOAD_KEY_ARRAY`,
`ASSET_PATH`, `ASSET_AUXILIARYPREVIEW_PATH` — also refuse a key whose **UTF-8 encoding** is over
`MAX_S3_OBJECT_KEY_BYTES` (1024, the Amazon S3 object-key limit), measured in bytes rather than
characters so a 350-character key of 3-byte characters is refused as S3 would refuse it. The validator
only sees the asset-relative part; the handlers that resolve a key against the asset prefix re-check the
RESOLVED key before their first S3 call and answer a generic `400` ("File key exceeds the maximum S3
object key length", the key is not echoed): the stream and download handlers (`streamAsset.py`,
`streamAuxiliaryPreviewAsset.py`, `downloadAsset.py`), upload initialization (`uploadFile.py`
`initialize_upload`, which checks the final AND the `temp-uploads/` key in its pre-loop validation block
so no multipart upload is created for any file of a refused batch), and `create_folder`, `move_file` and
`copy_file` in `assetFiles.py` (destination key). Every guard calls the one predicate
`common/validators.exceeds_s3_object_key_limit(key)` — do not re-implement the byte comparison in a
handler. An over-long key is therefore a `400` and never an S3 `ClientError` reported as a `500`
(issue #407). `ASSET_ID` keeps its separate 256-character cap.

Partition-aware AWS-resource validators (used by pipeline sub-process registration):
`ARN` (any AWS resource ARN), `CLOUDWATCH_LOG_GROUP_ARN`, `CLOUDWATCH_LOG_GROUP_NAME`
(1-512 chars, `-_./#`+alnum), `LOG_STREAM_NAME` (1-512 chars, no `:`/`*`),
plus `EVENTBRIDGE_BUS_ARN`, `EVENTBRIDGE_SOURCE`, `EVENTBRIDGE_DETAIL_TYPE`, `SQS_QUEUE_URL`.
Registration descriptors: `SFN_STATE_NAME` (1-80 printable chars, no control characters — an ASL
state name), `DISPLAY_LABEL` (same class, 1-128), `LOG_SOURCE_TYPE` (closed enum `stateMachine`,
`lambda`, `batch`, `ecs`, `container`, `custom`; an unknown value is stored as `custom`). A new name
is one `_VALIDATOR_DISPATCH` entry **plus** one `LEGITIMATE_VALUES` row in
`tests/common/test_validator_name_dispatch.py` (its table-equality test fails otherwise) and a
negative sample in `tests/common/test_mock_validators_delegates.py`.

Array validators (each element runs the scalar rule): `ID_ARRAY`, `UUID_ARRAY`,
`STRING_256_ARRAY`, `EMAIL_ARRAY`, `USERID_ARRAY`, `OBJECT_NAME_ARRAY`,
`RELATIVE_FILE_PATH_ARRAY`, `DOWNLOAD_KEY_ARRAY`.

### User IDs Are Unicode, and Are Normalized Before Validation and Storage

`USERID` keeps the Unicode-aware `\w` class, so an external IDP's non-Latin username is accepted.
Call `normalize_userid()` (or `normalize_userid_array()`) from `common.validators` on a caller-supplied
user id **before** validating it and before storing or looking it up: NFKC folds two compatibility
spellings of one name together (a fullwidth character for its ASCII counterpart, a decomposed accent
for a composed one), and normalizing on only some paths is worse than not normalizing at all — the
mismatch makes a lookup miss a row that exists. `request_to_claims()` normalizes the caller's own
identity, so a handler working from `claims_and_roles["tokens"]` needs no further call. A new route
that accepts a user id normalizes it at the same point every existing one does;
`tests/common/test_userid_identity_normalization.py` walks the tree and fails on one that does not.

`confusable_skeleton()` / `find_confusable_userid()` compare how two ids **look** (Cyrillic `а` and
Greek `ο` fold onto `a` and `o`) and belong at **user creation only** — `create_cognito_user` refuses
an id that reads the same as one already in the pool. An id already stored keeps working, so the
comparison never runs on a read. The mapping covers the Cyrillic and Greek lookalikes and is
deliberately partial.

### A `REGEX` Value Is Checked for Complexity, and Only on Save

`validate_regex` rejects three things on top of the compile check: a repeating quantifier applied to a
group that itself repeats or alternates (`(a+)+`, `(a|a)*`), a backreference, and more quantifier
ambiguity than one evaluation can afford — the estimated worst-case backtracking search space against
a 256-character subject must stay under `MAX_REGEX_SEARCH_SPACE`, which admits at most three unbounded
quantifiers (`.*`, `+`, `{n,}`). The budget is calibrated by measurement: three unbounded quantifiers
separated by literals match in ~16 ms, four in ~1 s, and five do not finish in 25 s. A value that runs
against longer subjects is checked at its own length: `validate_regex(subject_length=...)`, or
`subjectLength` in the `REGEX` spec (`models/compliance.py` checks path patterns at
`MAX_S3_OBJECT_KEY_BYTES`, 1024, which admits two). A value evaluated with `re.search` passes
`searched` (`searched: True` in the spec): unless it begins with `^` (`_anchored_at_start`), the
start positions the search tries count as one more unbounded quantifier; every compliance rule
pattern is searched. A constraint criterion value
becomes a Casbin `regexMatch(...)` pattern that `re.match` re-evaluates for every policy line on every
authorization decision, so such a value hangs authorization for the affected role. General regex stays
accepted — literals, character classes, anchors, `.*`, `|` and un-quantified groups.

The check runs where a constraint is **saved**: `ConstraintCriteriaModel` (every request path), plus
`validate_substituted_criteria_values()` in the template importer, which re-checks the value produced
by variable substitution because that — not the submitted template body — is what gets stored. It
deliberately does **not** run on read: `ConstraintResponseModel` uses
`ConstraintCriteriaResponseModel`, which carries no criteria-value rules, because a constraint written
by an earlier release was never checked and a failing response model degrades the listing to the raw
DynamoDB item shape.

### Importing Regex Patterns for Pydantic Fields

```python
from common.validators import (
    id_pattern,              # r'^[-_a-zA-Z0-9]{3,63}$'
    filename_pattern,        # For asset IDs and file names
    object_name_pattern,     # r'^[a-zA-Z0-9\-._\s]{1,256}$'
    relative_file_path_pattern,  # r'^\/.*$'
)
```

---

## DynamoDB Patterns

### Table Initialization

Resource + client APIs at module level, table names resolved via SSM:

```python
from backend.common.resourceNames import get_table_name, ResourceKeys

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)   # for low-level scans/pagination
your_table = dynamodb.Table(get_table_name(ResourceKeys.YOUR_STORAGE_TABLE))
```

### Common Operations

Standard boto3 resource-API calls (`query` with `KeyConditionExpression`, `get_item`,
`put_item` with `ConditionExpression`, `update_item`). For updates, build the update
expression via `common.dynamodb.to_update_expr(update_dict)`:

```python
from common.dynamodb import to_update_expr
keys_map, values_map, expr = to_update_expr(update_dict)
your_table.update_item(
    Key={'itemId': item_id},
    UpdateExpression=expr,
    ExpressionAttributeNames=keys_map,
    ExpressionAttributeValues=values_map,
)
```

The helper names its placeholders `#u<i>` / `:u<i>`, so a boto3 condition object
(`ConditionExpression=Attr("rev").eq(n)`) is safe in the same call: boto3 renders it with
`#n<i>` / `:v<i>` and merges those into the maps, replacing any entry of the same name. A
hand-built update expression combined with a condition object must avoid `#n<i>` / `:v<i>`
for the same reason.

### Pagination Patterns

Two different jobs with two different shapes. Decide which by asking who needs the complete
set — the handler, or the caller. Never let a single un-looped `query`/`scan` stand in for
either (Rule 14): DynamoDB caps one call at 1 MB and applies any `FilterExpression` only to
what that call already read.

**A. Read to exhaustion — the handler needs every row** (cycle checks, cascade deletes,
existence tests, descendant walks). Two shared helpers in `common.dynamodb` already implement
this loop and are the first choice for a `query`: `query_all_items(table, **query_kwargs)`
returns every matching item, and `query_has_match(table, **query_kwargs)` answers an existence
check, stopping at the first page that yields one. Hand-roll the loop only for a `scan`, or where
the walk needs its own bound; the shape is: loop on `LastEvaluatedKey`, feeding it back as
`ExclusiveStartKey`:

```python
from boto3.dynamodb.conditions import Key

items = []
query_kwargs = {'KeyConditionExpression': Key('databaseId').eq(database_id)}
while True:
    response = your_table.query(**query_kwargs)
    items.extend(response.get('Items', []))
    if 'LastEvaluatedKey' not in response:       # presence, not truthiness — see below
        break
    query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
```

Three details that decide whether this loop is correct:

-   **Test for presence (`'LastEvaluatedKey' not in response`), not truthiness.** DynamoDB
    omits the key entirely when the walk is complete and never returns a falsy one, so the
    two forms agree in production but not under test: `response.get('LastEvaluatedKey')`
    against a bare `MagicMock` returns a truthy `Mock` on every iteration and the loop never
    terminates, while `in` resolves to `False` and exits. A hanging test is the good outcome
    here; the same shape in a `while` loop with a page cap silently returns one page.
-   **Assign any "drained"/"complete" flag on every exit path.** A flag set only on the
    `LastEvaluatedKey`-absent branch reports the opposite of the truth when an early `break`
    (a cap, a found-it short-circuit) leaves the loop first.
-   **Rule 15 still applies to the response.** Reading to exhaustion internally is fine;
    returning that set to the caller is not. Enrich the full set, then slice to the page.

**B. External paging — the caller needs every row.** Return one page plus an opaque
Base64 `NextToken` wrapping `LastEvaluatedKey`:

```python
import base64, json
from common.dynamodb import validate_pagination_info

max_items = int(query_params.get('maxItems', '100'))
starting_token = query_params.get('startingToken')

scan_kwargs = {'Limit': max_items}
if starting_token:
    scan_kwargs['ExclusiveStartKey'] = json.loads(base64.b64decode(starting_token).decode('utf-8'))

response = your_table.scan(**scan_kwargs)
result = {'Items': response.get('Items', [])}
if 'LastEvaluatedKey' in response:
    result['NextToken'] = base64.b64encode(
        json.dumps(response['LastEvaluatedKey']).encode('utf-8')
    ).decode('utf-8')
return success(body=result)
```

**The token must round-trip, and nothing checks that for you.** The value the handler emits
has to be something the request model accepts back on the parameter the handler actually
reads. Emitting the raw `LastEvaluatedKey` **dict** as `NextToken` while the request model
declares `startingToken: Optional[str]` yields a token no client can return: page one looks
perfect, every later page is unreachable, and no error is raised anywhere. The Base64 wrap
above exists for exactly that reason — it makes the key a string, and opaque, so clients
cannot come to depend on its interior. Pin it with a test that takes the token from page one,
feeds it back, and asserts page two begins where page one stopped; asserting only that
`NextToken` is present passes on a token that cannot be used.

### Batch Reads

Resolving many rows by full primary key (the assets a page of executions references, the
pipelines a page of workflows references, the assets in a link tree, the current-version rows
of a page of listed assets) goes through
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

### Archived Assets Pattern

Archived assets live under a `databaseId + "#deleted"` partition-key suffix. Archive =
rewrite items with the suffixed partition key; query archived items with
`KeyConditionExpression=Key('databaseId').eq(f"{database_id}#deleted")`.

An archived record (asset or deleted database) is authorized in two forms:
`CasbinEnforcerService.enforce` allows it only when it passes on its stored `databaseId`
(`<databaseId>#deleted`) AND on its database id (the suffix removed). A deny on the database
therefore covers its archived records, and an allow written only as `databaseId equals <id>`
does not reach them; to grant archived records, a rule must also name `<databaseId>#deleted`
(for example `criteriaOr`: `equals <id>` OR `equals <id>#deleted`). Pass the stored record to
`enforce()` unchanged; do not strip or add the suffix in a handler for authorization.

### TypeDeserializer for Low-Level Responses

Low-level client responses use DynamoDB's typed dict shape. Convert with
`boto3.dynamodb.types.TypeDeserializer`:

```python
from boto3.dynamodb.types import TypeDeserializer
deserializer = TypeDeserializer()
python_dict = {k: deserializer.deserialize(v) for k, v in item.items()}
```

---

## Logging

### safeLogger

```python
from customLogging.logger import safeLogger
logger = safeLogger(service_name="YourServiceName")
logger.info(...); logger.warning(...); logger.error(...); logger.exception(...)  # exception adds stack trace
```

**Service name.** Every logger in a Lambda process is one Powertools logger, so the name labels every line the process writes, shared-module lines included. The handler names it: each line carries `service_name` (the key to query in CloudWatch Logs Insights) and the Powertools `service` key, both set to the handler's name. Shared modules (`common/`, `models/`, `customLogging/`, `customConfigCommon/`) and handler package `__init__.py` files call `safeLogger()` with no name and log under the calling handler's. Each Lambda entrypoint module declares `logger = safeLogger(service_name="<Name>")` at module level with a string literal, never a raw Powertools `Logger()`: `safeLogger` reads that declaration from the entrypoint's source (the runtime's `_HANDLER`) on its first call, so lines logged while the handler's imports run already carry its name, and another handler module imported as a library does not relabel them. `tests/common/test_logger_handler_service_name.py` enforces all of this.

`safeLogger` auto-redacts two key families at every nesting level, walking dicts, lists, and tuples (`customLogging.logger.mask_sensitive_data`, case-insensitive on the key name):

-   **Credential keys** (`SENSITIVE_KEYS`, which is `LOG_SENSITIVE_KEYS` from `common/constants.py`) — `authorization`, `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`, `apiKey`, `apiKeySecret`, `rawKey`, the credential-carrying headers `x-vams-origin-verify`, `cookie`, `set-cookie`, `x-api-key`, `proxy-authorization`, `x-amz-security-token`, the authorizer context key `vams:originVerifyDigest`, and the credential field names `password`, `secret`, `clientSecret`, `client_secret`, `accessToken`, `access_token`, `refreshToken`, `refresh_token`, `privateKey`, `private_key` (whole-name match, so `headers` and `multiValueHeaders` are both covered in any letter case, and `passwordPolicy` or `secretArn` stay readable), plus the keys the add-on and fork lanes register (`ADDON_LOG_SENSITIVE_KEYS` in `common/addonConstants.py`, `CUSTOM_LOG_SENSITIVE_KEYS` in `custom/customConstants.py`). `common/logRedaction.py`, which redacts the free-text log lines returned to callers, reads `LOG_TEXT_SENSITIVE_KEYS`: its own core keys (including the same credential field names) plus the same lane keys. Action records hold written values in full, so a handler builds `changes` from the validated model fields it set, never from the raw body, and a credential-bearing field it accepts goes on a redaction list, with a test.
-   **Content keys** (`CONTENT_KEYS`) — `configBody`, `templateTags`, `tagValues`, `customTemplateOverride`, `webFormJson`, `inputInstructions`. A pipeline template body or tag value carries free-form caller content (prompts, model configuration). Every field that can carry a template body belongs here whichever request delivers it: an execute request supplies one as `customTemplateOverride`, a template record as `configBody`. The value is replaced with `<redacted>` and the key is kept, so the record still shows that the field was submitted.

A request `body` is masked whether it arrives as a dict or as a JSON string — the string is parsed, masked, and re-serialized. Redaction is key-driven, so an f-string that interpolates a payload value (`logger.info(f"template {body}")`) bypasses it entirely: log identifiers, counts, and flags instead.

### Audit Logging

`backend/customLogging/auditLogging.py` exposes `log_authentication`, `log_authorization`, `log_authorization_api`, `log_file_upload`, `log_file_download`, `log_errors`, and other event-type functions — writing to **9 CloudWatch log groups** whose names resolve from SSM via `get_log_group_name(ResourceKeys.*)`. All audit functions extract user context via `request_to_claims(event)` (inside a request audit context, once per request; see "Request audit context" below). Every entry carries a `--- [event: ...]` echo. Authorization, error, auth change and auth other records (`_SHORT_ECHO_LOG_TYPES`) echo `_short_echo_event(event)`: request id and time, method, path, route, path parameters (masked, each capped like a record value) and connection addresses, never headers, query string or body. An action record written by `log_action` (and an auth change or auth other record inside a context) carries the compact echo. Authentication, file and `log_actions` records still echo the whole event after `mask_sensitive_data`, which keeps everything that is not a redacted key -- headers, the verified token claims (email included) and the request `body` -- so those records can hold personal data a user submitted; `docs/developer/audit-logging.md` "What Is Logged" states this and must stay accurate when an echo changes. `deferred_authorization_audit()` collects the `log_authorization` entries of a block and writes them with `_write_batch_to_cloudwatch` when it ends (every entry kept, also on an exception; nested blocks join the outer one) -- for handlers that decide many objects one by one. **Silent failure**: a failed audit write is logged locally, and Lambda execution continues — `mask_sensitive_data` upholds that contract by returning `<redacted>` rather than raising on a structure it cannot walk.

**Undelivered entries.** Every path that loses an entry (put_log_events raising, no client, stream creation failing, the log group name not resolving, formatting failing, any other exception) calls `report_undelivered_audit_records(log_type, reason, records)`: one `{"auditFallback": true, "logGroupKey", "reason", "record"}` JSON line per entry on standard output (the function's own log; the record capped by `_fallback_record`), and one CloudWatch embedded-metric-format line per call (namespace `VAMS/Audit`, metric `AuditWriteFailures`, dimension `AuditLogType` = the log type such as `auditActions`, value = entries lost). It never raises, needs no IAM beyond the function's log delivery, and nothing runs on a successful write. A batch write reports each failed batch's entries with one metric line per batch. A new write path reports its own losses the same way (`handlers/auth/routes.py` `_DenialAuditBatch.flush` reports the denials it could not hand to the writer). The CDK alarm on the metric is not part of the stack; `docs/developer/audit-logging.md` gives an example.

**Record format.** A record is one line: bracketed prefix segments, the JSON payload, then the event echo. Every value inside a `[...]` segment (user, roles, the secondary type, the probe endpoint) is encoded by `_encode_prefix_value` / `_encode_roles` / `_event_type`: `[`, `]`, `\`, CR, LF, other C0/C1 controls and bidirectional or zero-width characters become `\uXXXX`, and a value made of `userid_pattern` characters is unchanged. The payload and the echo are `json.dumps(..., ensure_ascii=True, default=str)`. Build records only through these helpers; a record with an extra segment gets its own formatter in `auditLogging.py` (`format_authorization_denial_record` for the probe marker), and `tests/mocks/customLogging/auditLogging.py` gets a matching stand-in.

**Action records and the registry.** `log_action(event, action, ...)` writes a record in the record contract (`auditVersion` 1; `docs/developer/audit-logging.md` "Action Records"): `recordId`, `action`, `outcome` (`success` / `failed` / `denied`, a generic `reason` on the last two), `eventTime`, `actor` (`userId`, `authMethod` `cognito`/`externalIdp`/`jwt`/`apiKey`/`system`, `apiKeyId`, `crossCall`, `onBehalfOf`), `request` (`requestId`, `method`, `route` template, `sourceIp`, `lambdaRequestId`), `entity`, the top-level ids (`AUDIT_ID_FIELDS`), `bytes`/`fileCount`/`itemCount`/`durationMs`, and the value sections `changes` (each field a write set, with its new value), `previous` (prior values the handler already loaded; never read a record only to fill it), `requestParameters` (what a read, search or download asked for; never what was returned: `log_action` drops `changes` and `previous` for an action of kind `read`, `search` or `download`, and `itemCount` on a read or search, the number returned, is the one field `AUDIT_READ_ITEM_COUNT` controls) and `details`. A key that does not apply is left out, never `null`. Every value section passes through `mask_sensitive_data`; a value over `AUDIT_VALUE_MAX_BYTES` (2 KB) becomes `{"truncated": true, "sha256", "bytes", "preview"}`, and `_bounded_action_record` bounds the whole formatted record (prefix, payload and echo) to `AUDIT_RECORD_MAX_BYTES` (32 KB): the echo is replaced by `AUDIT_ECHO_TRUNCATED` first, then `_cap_record` cuts payload values, and a prefix too long to leave room cuts the record with `AUDIT_EVENT_TRUNCATION_MARKER`. Never pass file content. The echo of an action record is compact (request id, method, path, route key). The action's group, `[type: ...]` value (its `legacy_type` when it has one) and entity type come from `common/auditActions.py`; when several routes register one action name (the four metadata routes), `log_action` uses the spec of the request's matched route (`_action_spec`), so the entity type is that route's. `common/auditActions.py` maps every route-method of `ALL_API_ROUTES` to an `AuditSpec` or an `AUDIT_EXEMPT` reason (add-on lane `common/addonAuditActions.py`, fork lane `custom/customAuditActions.py`; types in `common/auditActionTypes.py`); a denial is written to the authorization group with the `[AUTHORIZATION][authorized: False]` prefix, and a file group's record carries no `[type: ...]` segment. `tests/common/test_audit_registry.py` fails on an unregistered route-method, on a literal type passed to `log_actions` / `log_auth_changes` / `log_auth_other` / `log_action` that the registry does not know, and on a registered action no handler records, on a Lambda entrypoint (`lambda_handler`, `lambda_handler_<name>`) that neither carries `@audited_handler` nor enters `audit_request` and is not in `AUDIT_ENTRYPOINT_EXEMPT`, and on a system action its own handler module does not record. The scan counts the action as a string literal passed to `log_action` (or a typed legacy function), or to a module helper that forwards one of its parameters as that action (`_log_per_file(event, "fileDelete", ...)`); an action held in a variable or a dict is not seen, so pass the literal. Call `log_action` after the write succeeds, as `workflowService.py` places `log_actions`, and pass the entrypoint's own event object: the context is matched by identity, so a copy, `event["body"]` or one SQS record is written as outside the context (a queue or state machine handler that reassigns `event` keeps the original in a variable for its records; a helper deep in a call chain may use `current_audit_context().event`). Capture `previous` before the handler sets the new values on the record it read. The six compliance API handlers open the context in `lambda_handler` and record their route's action in each dispatch branch once the service function answered (`log_action(event, "complianceSchemaCreate", **audit_outcome(response), ...)`, `common/compliance/routeAudit.py`: a 2xx is a success, a 403 a denial, anything else a failure with a generic reason); a call the API-level check refuses, or that raises, records the route's own action (`log_action(event, **failure_outcome(status))`). A compliance write never records a schema body; a write that creates something names it from the service's answer (`created_value`: the `cascadeId` of a cascade create, the `evaluationId` of an asset evaluation, the schema `internalVersion` written, a database binding's `details.applyId`), and Evaluate All and a sweep record the count of evaluations started, never their ids; `tests/handlers/compliance/test_complianceActionRecords.py` checks every compliance route-method. A compliance request that works across databases (cascade reach, sweep, audit partitions, schema create scopes) decides each database with `enforce()`, so it records a denial for each database the caller may not act on, as the core listings do (`tests/handlers/compliance/test_complianceDenialsAcrossDatabases.py`). The four compliance Lambdas that serve no API route (`complianceTrigger`, `complianceWorkflowCallback`, `complianceCascadeExecutor`, `complianceJobs`) carry `@audited_handler` and record their system actions (`CORE_AUDIT_SYSTEM_ACTIONS`: `complianceAutoEvaluate`, `complianceRecordRemove`, `complianceCascadeAutoCreate`, `compliancePipelineRuleComplete`, `complianceCascadeRun`, `complianceBindingApply`, `complianceDatabaseRetire`) through a module `_record(action, ...)` helper on `current_audit_context().event` (`tests/handlers/compliance/test_complianceSystemActionRecords.py`). The deploy-time seed of the GLOBAL default schema lives with them (`handlers/compliance/complianceDefaultSchemaSeed.py`, a CloudFormation custom resource, as core's `crReindexer` and `importGlobalPipelineWorkflow` are deploy-time writers under `handlers/`) and records `complianceSchemaSeed` as the system user: the schema name and the version it wrote, or `details.written` false with `alreadyPresent` (`tests/handlers/compliance/test_complianceDefaultSchemaSeed.py`).

**Request outcomes.** `@audited_handler` records the route's action as `outcome="failed"` (generic `reason` from `audit_failure_reason(status)`; the path's id fields) when the handler returned a status of 400 or more, or raised, and recorded nothing for that action; a 401 or 403 adds nothing (the authorization record is the denial's record). On an auth-change or auth-other route the failed record carries the route's `[type: ...]` value (its `legacy_type`, e.g. `constraintCreateUpdate`) as the prefix and as `legacyType`, as the success records do. A handler therefore records only its successes. An error answer that only asks the caller to try again, and that the caller always retries, is no outcome: the handler calls `skip_route_outcome(event)` before returning it, and the decorator records nothing for it; the handler also builds that answer without `general_error(..., event=event)`, whose errors-group entry carries the action and `outcome: failed` (executeWorkflow's 429 to a vector reindex launch that finds every reindex slot held, `_reindex_slot_wait_response`; the launch that claims a slot records `workflowExecute`, and one refused for good records its failure). Only the outermost entrypoint records an outcome, and that record joins the request's flush (`log_action(..., flush_with_request=True)`; the request has ended, so holding it loses nothing and saves a write). `log_actions(..., outcome="failed")` marks an earlier-style record failed (written at once). `log_action(..., on_behalf_of=user)` names the user a system action ran for.

**Reads switch.** `app.auditLogging.recordReads` (default `true`, absent = `true`) reaches every Lambda as `AUDIT_RECORD_READS`; `audit_records_reads()` reads it on each call and only `"false"` turns it off, so a function deployed without the variable records reads. When it is off, `log_action` writes nothing for a successful action of kind `read` or `search`; downloads, exports, writes, denials and failures are recorded, and the earlier `log_*` functions are unaffected. Tests set it with `monkeypatch.setenv`.

**Request audit context.** `with audit_request(event, context):` (or `@audited_handler`) opens one request's `AuditContext`: claims are resolved once (`_extract_user_context` reuses them, so `request_to_claims` and the claims hook run once per request, not once per record), and the actor and request facts are shared by every record. Successful `log_action` records, and `log_actions` / `log_file_*` records, are buffered and written when the block ends by `_deliver_records`: one `_put_audit_records` per group on a pooled thread, all groups in parallel, bounded by `_flush_deadline_seconds`: `AUDIT_FLUSH_DEADLINE_SECONDS` (1.5 s), cut to the remaining Lambda time less `AUDIT_FALLBACK_RESERVE_MS` (250 ms) so the fallback lines are written before the timeout, and written with the bounded `audit_flush_client` (1 s connect, 2 s read, 3 attempts). A group not confirmed by the deadline is reported with reason `flushDeadlineExceeded` (delivery is at least once; `recordId` identifies a duplicate). A timer flushes `AUDIT_PRE_TIMEOUT_MARGIN_MS` (750 ms) before the function times out, after which records are written at once. Denials, failures and `log_errors` records are written at once. An allowed authorization record (`log_authorization_api` for the context's own event, `log_authorization` on the context's thread, `authorized is True` only) is buffered the same way by `_buffer_allowed_authorization`, formatted as an immediate write in the same context formats it (only its write time moves, so its CloudWatch `@timestamp` is the flush time while `eventTime` and the echo's `requestContext.requestTimeEpoch` keep the check's time; its `recordId` identifies a duplicate after a missed deadline), so a request whose only record is its route check makes one write at the flush; an authorization denial is written at once (inside `deferred_authorization_audit()`, at the block's end), and outside a context both are written at once as before. Inside a context `log_actions`, `log_file_*`, `log_errors`, authorization (`log_authorization_api` for the context's own event, `log_authorization` on the context's thread: `_authorization_keys` adds the route's action and entity, `outcome` `success`/`denied`, actor, request, `recordId`, `eventTime`; the checked method moves to `legacy.action`; the decision never reads any of it), `log_auth_changes` and `log_auth_other` records (`_log_auth_record`: the masked legacy payload plus `build_audit_payload` keys, `legacyType`, the ids, `changes`/`previous`/`requestParameters` from keyword arguments, bounded by `_bounded_action_record`; a success is buffered, any other outcome written at once) keep their payload keys and gain the contract keys (the contract keys win: a legacy key of the same name with a different value, such as an AMI recipe build's `outcome`, moves under `legacy` (`AUDIT_LEGACY_KEY`); `action` is the route's action when the record is the route's own, by legacy type, name or legacy file function); `log_authentication` and `log_authorization_gateway` (the authorizer runs outside a context) keep their records unchanged. Auth handlers pass what a write changed through the keyword arguments and never a secret (API key value or hash, password, temporary password); redaction still applies. `log_authorization_gateway(..., verified_context=res["context"])` names the verified user, roles and MFA state on an allowed record (`_gateway_user_context`, the `request_to_claims` claim order); a denial and an ignored-path allow name `unknown`. A record for a different event object, or from another thread, is written as outside a context. Outside a context every function writes exactly as before. A handler that forwards a call to another VAMS Lambda with its caller's authorizer context sets `AUDIT_INTERNAL_CALL_KEY` (`vamsAuditInternalCall`) on the forwarded event to `internal_call_marker()` (the current request's `requestId` and user); `_resolve_actor` then records `crossCall: true` and `onBehalfOf`, and `_request_facts` takes the marker's `requestId` when the event has none. `_request_facts` sets `sourceIp` from `common/auth/viewerAddress.viewer_ip_for_audit(event)` first: the CloudFront viewer, read from the forwarding headers only when the request's `x-vams-origin-verify` header hashes to the `vams:originVerifyDigest` the REST authorizer put in its context (`clientIp.origin_verify_audit_context`, CloudFront deployments, accepted value only, added after the decision); otherwise the connection address. Audit only: no decision reads the digest, and a handler never holds the origin verification secret (`executeWorkflow._metadata_service_payload`, `ingestAsset`). The key is attribution only: authorization still reads the forwarded authorizer context, and a `lambdaCrossCall` payload stays unmarked. The context is a `ContextVar`, so a worker pool that builds forwarded payloads runs each job in its own `contextvars.copy_context()` (`executeWorkflow._run_bounded`); a job run without one sees no context and its marker is empty.

**Every path that hands out asset file content** — a presigned GET URL or a streamed body, in `downloadAsset`, `streamAsset`, `streamAuxiliaryPreviewAsset`, and `assetExportService` — applies three controls on top of Casbin: it refuses (or, for the export's per-asset entries, withholds the URL) when the asset's `isDistributable` is false; it runs `validateUnallowedFileExtensionAndContentType` on the file's key and the `ContentType` a `head_object` of the version about to be served reports, and refuses (export: withholds the URL, keeps the file in the listing) when the file is blocklisted or the head fails; and it writes a file-download audit entry (`log_file_download` / `log_file_download_bulk` / `log_file_download_streamed`) for every URL or body it actually issued. A new download surface gets all three; `downloadAsset.py`'s `lambda_handler` is the reference call shape, and `assetExportService.py` `file_type_allowed_for_download` is the withhold-not-refuse form. A surface that checks many files in one request runs the heads through a bounded pool (`downloadAsset.py`'s bulk branch, `assetExportService.py` `mark_files_allowed_for_download`): serial per-file heads over an asset of thousands of files exceed the API Gateway integration timeout. Every presigned `get_object` URL these surfaces mint passes its `Params` through `common.s3` `presigned_get_object_params()`, which signs `ResponseContentType=image/svg+xml` and `ResponseContentDisposition=attachment` for a key ending in `.svg` (any case) and leaves every other key unchanged: an SVG is stored as `application/octet-stream`, a browser `<img>` renders it only as `image/svg+xml`, and the attachment disposition keeps a directly opened link a download so no SVG script runs at the bucket origin (#1201). Never sign an SVG inline. `tests/handlers/assets/test_presigned_svg_response_overrides.py` pins each path.

---

## Response Functions (`models/common.py`)

-   `success(body=...)` → 200
-   `validation_error(body={'message': ...}, event=event)` → 400
-   `general_error(body={'message': ...}, event=event)` → 400
-   `authorization_error()` → 403
-   `internal_error(event=event)` → 500
-   `service_unavailable(body={'message': ...}, event=event)` → 503 — a dependency cannot answer right now (throttling, a timeout, an OpenSearch domain that cannot be reached). The body is a generic try-again message; the cause stays in the log.
-   `VAMSGeneralErrorResponse(...)` — exception class raised by business logic; caught in the handler `try/except` and re-emitted via `general_error(body={'message': str(v)}, event=event)`.

Pass `event=event` where accepted so the audit-logging hook can capture the caller. All responses follow `APIGatewayProxyResponseV2`: `isBase64Encoded=False`, `statusCode`, `headers` (`Content-Type: application/json`, `Cache-Control: no-cache, no-store`), and a JSON-string `body`.

---

## Environment Variables

### Loading Pattern

Resolve resource names at module load inside one `try/except`; wrap optional resources in an inner `try/except KeyError`; read handler-specific env vars directly from `os.environ`:

```python
try:
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    auxiliary_bucket = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
    try:
        optional_table_name = get_table_name(ResourceKeys.OPTIONAL_TABLE)
    except KeyError:
        optional_table_name = None
    send_email_function = os.environ.get("SEND_EMAIL_FUNCTION_NAME")
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

asset_table = dynamodb.Table(asset_table_name)
```

**Resolution order:** `get_table_name(ResourceKeys.*)` first checks for legacy env-var overrides (e.g. `ASSET_STORAGE_TABLE_NAME`), then a 60-minute in-module cache, then a short-lived negative record of keys a completed sweep did not carry, so an unpublished parameter costs one sweep per window rather than one per call, then fetches all resource-name parameters from SSM via one paginated `GetParametersByPath` call. Pytest and local utilities can inject names as env vars while deployed handlers use SSM. **Pipeline handlers** in `backendPipelines/` still use legacy env vars and do not call `get_table_name()`.

### Common Environment Variables

`VAMS_RESOURCE_PARAM_PREFIX` (required, non-pipeline handlers): SSM parameter prefix for resource-name resolution. `AWS_REGION` (auto, set by Lambda runtime). `COGNITO_AUTH_ENABLED` (authorizer Lambda only): whether the Cognito MFA-preference check is reachable. Handler-specific vars like `SEND_EMAIL_FUNCTION_NAME` are read directly from `os.environ`. `WORKFLOW_SERVICE_V2_FUNCTION_NAME` is set on `pipelineService`, `executeWorkflow` and `importGlobalPipelineWorkflow`; the first two read it with `os.environ.get` and degrade (the refresh is not scheduled; a stale launch returns `409`) when it is absent.

Compliance-related handler-specific vars: `EXECUTE_WORKFLOW_FUNCTION_NAME` (the evaluate-service, cascade-service and trigger Lambdas — read by `complianceEvaluationStore` to launch a pipeline rule's workflow execution through a `lambdaCrossCall`); `COMPLIANCE_TRIGGER_QUEUE_URL` (the trigger — its own queue, on which it holds an upload completion's record back with ChangeMessageVisibility until the completion is `UPLOAD_COMPLETION_SETTLE_SECONDS` old, so the requests of one multi-request upload are evaluated once; unset, completions are evaluated at once. Also set on `complianceSchemaBindingService`, which queues an asset override's `{databaseId, assetId}` evaluation under `complianceAutoEval`, and on `complianceJobs`, which queues a database binding change's evaluations there, paced by the apply record's own counts of evaluations sent and taken, never by the queue's approximate message count; unset, nothing is queued); `COMPLIANCE_JOB_QUEUE_URL` (the compliance job queue: `databaseService` queues a delete's `moveDeletedDatabaseCompliance` message, `complianceSchemaBindingService` a binding change's first `applyDatabaseBinding` message, and `complianceJobs` each continuation; `complianceTrigger` does not have it: a job message found on its own queue is logged with one WARNING and dropped, `drop_job_message`); `COMPLIANCE_JOB_DLQ_URL` (`complianceJobs` only: a move stopped at its run limit is put in the job queue's dead-letter queue, from which a redrive resumes it); `COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD` (the four asset download Lambdas `downloadAsset`, `streamAsset`, `assetExportService` and `streamAuxiliaryPreviewAsset`, set from `app.compliance.quarantineBlocksDownload`; read by the shared `common/compliance/quarantineGuard.py`, which resolves the compliance asset-state table only when it is `true` and whose `check_quarantine_block()` the handlers call only after Tier-1 + Tier-2 authorization); `ORCHESTRATION_BUS_ARN` + `ORCHESTRATION_EVENT_SOURCE_PREFIX` (set on the workflow Lambdas by `workflowFunctions.ts`; `sfn/processWorkflowExecutionOutput`, `sfn/handleExecutionError` and `executionService` publish the `workflow.execution.completed` event only when both are set). The compliance Lambdas receive **no** table-name env vars: every table resolves through SSM (`ResourceKeys.COMPLIANCE_*_STORAGE_TABLE`; legacy override `COMPLIANCE_*_STORAGE_TABLE_NAME`), and the workflow callback resolves its output locations from the V2 execution records rather than a bucket env var.

`PRESIGNED_URL_TIMEOUT_SECONDS` (S3 presigned URL TTL) is **not** set for every handler — only `infra/lib/lambdaBuilder/assetFunctions.ts` sets it, covering the five asset handlers that mint presigned URLs (`downloadAsset`, `streamAsset`, `streamAuxiliaryPreviewAsset`, `uploadFile`, `assetExportService`). All five index it (`os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]`) at module level, so a handler built by any other lambda builder that copies that idiom raises `KeyError` during module import and returns `500` on every request from cold start. Add the variable to the handler's own builder before reading it.

**Legacy env-var overrides** (for pipeline handlers and testing): `ASSET_STORAGE_TABLE_NAME`, `DATABASE_STORAGE_TABLE_NAME`, `S3_ASSET_AUXILIARY_BUCKET`, `AUDIT_LOG_*`, etc. Non-pipeline handlers resolve these via SSM unless the legacy env var is explicitly set.

**Vector-search handler variables** (`handlers/osVectorSearch/`, all indexed with `os.environ[...]` at module level, so each is set only by the builder named — copying the read into a handler built elsewhere raises `KeyError` at import): `VECTOR_INDEX_NAME`, `EMBEDDING_MODEL_ID` (`app.pipelines.useSystemGenAiMetadata.bedrockModels.embeddingModelId`) and `EMBEDDING_DIMENSIONS` (`app.pipelines.useSystemGenAiMetadata.bedrockModels.embeddingDimensions`, read with `int()`) come from `vectorIndexEnvironment()` in `infra/lib/lambdaBuilder/osVectorSearchFunctions.ts` on `vectorIndexer`, `vectorReindexer` and `vectorSearchService`; `VECTOR_INDEXER_QUEUE_URL` (`vectorIndexer`) is the indexer's own SQS queue, and `VECTOR_INDEXER_DLQ_URL` (`vectorIndexer`) its dead-letter queue, where a continuation past `MAX_CONTINUATION_HOPS` is parked. `GENAI_METADATA_WORKFLOW_ID` and `GENAI_METADATA_WORKFLOW_DATABASE_ID` name the SYSTEM GenAI metadata workflow the reindexer re-launches (the shared literals of `infra/common/systemPipelines.ts`, `SYSTEM_GENAI_METADATA_WORKFLOW_ID` / `SYSTEM_WORKFLOW_DATABASE_ID`) and are set on `vectorReindexer` and `systemWorkflowLauncher` by `vectorIndexing-construct.ts`; `WORKFLOW_LAUNCH_QUEUE_URL` (`vectorReindexer`, `vectorIndexer`) is the launch queue the reindexer enqueues one message per file version onto, and onto which the indexer queues a restored file that has no vector item, and `EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME` (`systemWorkflowLauncher`) is the function that queue's consumer cross-calls. The search Lambda also carries `OPENSEARCH_DISABLED` (`"true"` when no OpenSearch engine is deployed — `/search/nlp` then skips its OpenSearch enrichment step and reports OpenSearch-only filter fields under an `opensearch:fields_ignored` warning) alongside the `OPENSEARCH_*` variables the keyword search Lambda uses.

---

## File Security

Uploads must be validated against **both** `UNALLOWED_FILE_EXTENSION_LIST` (`.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat`) and `UNALLOWED_MIME_LIST` (Java archives, MS-executable, shell/JS/PowerShell/VBScript, etc.). Both lists are built in `common/constants.py` from the core lists plus the add-on (`common/addonConstants.py`) and fork (`custom/customConstants.py`) lanes, append-only; extend the core list there or a lane list, never bypass or remove an entry. The one helper that applies them, `common/s3.py` `validateUnallowedFileExtensionAndContentType`, compares case-insensitively: the file extension and the media-type part of the content type (parameters such as `; charset=binary` stripped) are lower-cased before the membership test, so `.EXE` and `Application/X-Msdownload` are rejected like their lower-case forms. The extension is **everything from the last dot of the key's final non-empty segment, read after every trailing dot and whitespace character is removed** (`str.isspace`, so a non-ASCII space counts): `payload.exe.`, `payload.exe `, `.exe`, `..exe` and `payload.exe/` are all checked as `.exe`, while `.gitignore` is checked as `.gitignore` and stays allowed; because the trailing `/` is ignored, a folder whose own name ends in a blocked extension (`builds.exe/`) is refused for prefix download. The segment is split on `:` and every colon-separated piece of the name is checked against the list, each read after its own trailing dots and whitespace are removed and lower-cased (on NTFS a `:` opens a stream name, so `payload.exe::$DATA` and `payload.exe:evil` write `payload.exe`; a browser that cannot write `:` substitutes it and keeps the whole name, so `a:b.exe` lands as `a_b.exe` — both shapes, and `x:payload.exe:evil`, are checked as `.exe`), while a `:` in an ordinary name (`notes:v2.txt`) is still accepted by the helper for an object already in a bucket. The request models complement this on the paths that CREATE a key: `models/assetsV3.py` `validate_asset_file_path(..., new_object=True)` refuses a final path segment ending in a dot or whitespace for upload initialize/completion `relativeKey` and move/copy `destinationPath` (Windows drops that character on download, so `payload.exe.` would land as `payload.exe`), and `file_only=True` additionally refuses a `relativeKey` ending in `/` on the three upload paths (a file key is never a folder; `CreateFolderRequestModel` keeps its own trailing-slash rule); the read paths — download `key`, file info, archive, move/copy `sourcePath` — set neither flag so an object already stored under such a key stays reachable. `new_object=True` also refuses a `:` anywhere in the value, on every created-key path including `CreateFolderRequestModel` (Windows forbids it in any path segment, so no legitimate name is lost), while the read paths keep accepting it. Keep every list entry lower-case and listed once — `tests/common/test_s3_blocklist_case_insensitive.py` fails on an entry of the merged lists that is not (a lane entry that is not is refused at import first); `tests/common/test_s3_blocklist_trailing_dot.py` and `tests/models/test_assetsV3_trailing_dot_write_paths.py` pin the basename rule and the write-path refusal, `tests/common/test_s3_blocklist_ads_suffix.py` and `tests/models/test_assetsV3_ads_suffix_write_paths.py` pin the per-piece `:` check and the created-key `:` refusal, and `tests/models/test_validate_asset_file_path_call_sites.py` fails on a `validate_asset_file_path` call whose field neither passes `new_object=True` nor is declared a read path.

Move, rename and copy (`handlers/assets/assetFiles.py` `move_file` / `copy_file`) run the same helper on the resolved destination key, with an empty content type, right after the key-length check and before any S3 call, and refuse a blocked name with a `400`; `tests/handlers/assets/test_assetFiles_destination_extension_check.py` pins it.

Upload completion (`handlers/assets/uploadFile.py` `complete_upload` / `complete_external_upload`) runs the name-level blocklist (and the preview-extension rule) on every object it creates — zero-byte and regular multipart alike — before anything is written (a multipart file completed with no parts is a cancelled file: its multipart upload is aborted, nothing is stored, and its result is `success=False`; `sqsUploadFileLarge.py` `complete_multipart_upload_for_large_file` does the same and returns `False`), and accepts only the `relativeKey`s the initialize step recorded on the upload record (`AssetUploadTableModel.files`); a record without that attribute predates the binding and is completed with the blocklist checks but without the membership check, logged as a warning, until it expires: `complete_upload` refuses a record whose `expiresAt` (epoch seconds; 7 days from initialize, 1 day for workflow-output records) has passed, and refuses an external (`isExternalUpload`) record outright — the mirror of the check `complete_external_upload` makes — so the one record class designed without the list cannot be completed by the route that skips membership. `AssetUploadsStorageTable` has a DynamoDB TTL on `expiresAt`, which removes an expired record some time after it expires; until then this check is what refuses it.

### Choosing a file's `.previewFile.<ext>`

A file can hold several `<file>.previewFile.<ext>` objects (a pipeline re-run in another format writes a `.png` beside an existing `.gif`; nothing deletes the other). Every path that attaches a file's preview goes through `select_newest_preview_file((key, lastModified, isArchived) …)`: in `handlers/assets/assetFiles.py` the current listing, the version-snapshot listing in basic and detailed mode, and the single-file info that set `previewFile`, in `handlers/assets/assetExportService.py` the export's per-file preview path, and in `handlers/osVectorSearch/vectorIndexer.py` `find_preview_file_key`, the preview key a vector item carries. Natural-language search's OpenSearch step keeps the file index's preview key over the vector item's. A live preview beats an archived one, then the latest `lastModified` wins, ties keep key order. Do not pick "the first in key order" in a new path. Tests: `tests/handlers/assets/test_assetFiles_newest_preview.py`, `tests/handlers/assets/test_assetExportService_newest_preview.py`, `tests/handlers/osVectorSearch/test_vectorIndexer_embedding_ready.py`, and `TestPreviewKey` in `tests/handlers/osVectorSearch/test_vectorSearchService_final_review.py`.

---

## Feature-Gated Families Do Not Import Each Other

A capability that a deployment can switch off wholesale is a **family**: its handlers, its `common/` package, and its models, all absent from a deployment with the flag off. Two exist today — the OpenSearch semantic search family (`handlers/osSemanticSearch`: the keyword `/search` route plus the `osFileIndexer` / `osAssetIndexer` stream consumers, on the OpenSearch mode flags) and the vector family (`handlers/osVectorSearch`, `common/vectorsearch`, `models/vectorsearch`, on `app.vectorSearch.enabled`). Between them sits `handlers/indexing`, the **core** that runs in every deployment: `sqsBucketSync`, the `snsQueuing` shims, and `crReindexer`, which re-writes rows so every indexer family's stream consumers re-process them. Either family can be deployed without the other, so a module-level import across a boundary fails the surviving code at cold start — a `500` on every request that synth cannot see and a test suite importing everything never reaches.

The rules a family follows, each of which the vector family models:

-   **It owns its packages and imports only `common/`.** Shared behaviour both families need is extracted into a neutral `common/` module rather than imported from either family: `common/indexing/documentIds.py` and `fileEnumeration.py` ("what is an asset file", defined once for the core reindexer and the vector reindexer), `common/databaseAccess.py`.
-   **Core never imports it, and it never imports core.** Core touches a family through the registry seams every table and route already uses — a `ResourceKeys` constant, an `ApiRoute`, a feature switch — and through events: `sqsBucketSync` publishes to the storage stack's SNS fan-out topics that both families' queues subscribe to, and the pipeline publishes `vector.embedding.ready` on the orchestration bus for the vector indexer to consume, so no side imports another.
-   **A cross-group read at runtime is function-scoped and gated.** Two are admitted: `vectorSearchService._opensearch_step` imports `handlers.osSemanticSearch.search` inside the function, after `OPENSEARCH_DISABLED` is ruled out, to constrain and enrich hits when both families are on: a file answer on the file index, and an asset answer on the asset index alone, where `_asset_index_step` applies the OpenSearch-only constraints built for the asset index (as keyword search does) and reads each returned asset's location through the same client, so it is still one crossing; and `crReindexer.ReindexUtility.clear_opensearch_indexes` imports `opensearchpy` inside the method, so the core reindexer's row-rewriting path never loads the client where OpenSearch is off. Both are named, with a site count, in `tests/common/test_indexer_families_import_boundary.py`, which walks the tree and fails on a module-level import in any direction, an unlisted function-scoped one, or an exemption whose site no longer exists. The CDK half of the same boundary — no cross-gated function, no cross-wired queue — is `infra/test/platform/t1VectorIndexingWiring.test.ts`.

**Each family also has its own reindexer, because the two re-index by different mechanisms.** The core `crReindexer` re-writes DynamoDB metadata rows so the stream consumers re-process them — enough for the OpenSearch indexers, whose documents are derived from those rows. A vector row is the output of a Bedrock analysis run, so `vectorReindexer` instead enqueues one system-workflow launch per latest live file version. Both are invoked the same two ways: directly, and as a CDK custom resource on deploy (`app.openSearch.reindexOnCdkDeploy` / `app.vectorSearch.reindexOnCdkDeploy`). Two of the vector family's handlers also run with vector search off: `vectorReindexer` and `systemWorkflowLauncher` are deployed whenever the system GenAI metadata pipeline is on and registered (`systemWorkflowReindexerEnabled` in `config.ts`), as the bulk path that analyzes existing files. The reindexer then gets `VECTOR_SEARCH_ENABLED=false`, reads no vector index environment, builds no vector store and refuses `clear` and `both` with a `400`; `enqueue` is unchanged.

A third family follows the same shape: its own `handlers/<family>` and `common/<family>` packages, its own queue subscribed to the storage stack's SNS fan-out topics (as the Garnet and Physna add-ons and the vector indexer do), its own tables registered through `ResourceKeys`, its own reindexer if its rows are not derivable by re-writing metadata, and an entry in that guard's family table.

---

## Partition Portability (Commercial / GovCloud / EU Sovereign)

Handlers run in `aws`, `aws-us-gov`, `aws-eusc` (EU Sovereign, region `eusc-de-east-1`), and potentially `aws-cn` / `aws-iso*`. A partition defect here is invisible in commercial tests and surfaces as a runtime 500 or a validation rejection that only reproduces in the affected partition. There is deliberately **no central partition helper** in the backend — the four rules below are the whole contract.

1. **Never build an ARN from a hardcoded partition.** Derive it, or take it as a parameter. Two patterns are already established, and new code should reuse one:

    - **Parameterize.** `common/workflows/stepfunctions_builder.py` threads a `partition` argument (defaulting to `"aws"`) through every state-machine integration ARN. Its value originates as the `AWS_PARTITION` Lambda env var, read once in `common/workflows/workflowAsl.py` and passed down.
    - **Parse an ARN you already hold.** `handlers/workflows/executionService.py` splits a resource ARN (falling back to the execution log-group ARN) to recover partition/region/account, so nothing is assumed. Prefer this when an ARN is already in hand — it needs no env var and cannot drift.

    `AWS_PARTITION` is currently set on the `workflowService` Lambda only (`infra/lib/lambdaBuilder/workflowFunctions.ts`). A handler that needs the partition either gets the env var added in its CDK builder or derives it from an ARN — do not read `os.environ["AWS_PARTITION"]` in a handler whose builder does not set it.

2. **Never hardcode an endpoint hostname or DNS suffix.** Suffixes differ per partition — `amazonaws.com`, `amazonaws.com.cn`, **`amazonaws.eu`** (EU Sovereign), `c2s.ic.gov`, `sc2s.sgov.gov`, `cloud.adc-e.uk`, `csp.hci.ic.gov`. Construct plain boto3 clients (`boto3.client("s3", config=retry_config)`) with **no `endpoint_url`** and let the SDK resolve per region; take service endpoints (e.g. the OpenSearch host) from SSM rather than composing them. Region comes from `os.environ["AWS_REGION"]`, which the Lambda runtime always sets — avoid a `"us-east-1"` default, which silently points at the wrong partition when the variable is missing.

    **Asset buckets may be in another Region, so every S3 call on an asset bucket goes through `common.s3`'s Region-routing client**, never a module-level `boto3.client('s3', ...)` of the handler's own. `region_routing_s3_client()` / `region_routing_s3_resource()` return the module-level client/resource a handler holds (`s3 = region_routing_s3_client()`); each call is signed for the Region of the `Bucket` it names (`Params['Bucket']` for a presigned URL), resolved from the bucket details the handler read (`bucket_region_fields(row)` spreads `bucketRegion`/`bucketAccountId` into every bucket-details dict and registers the name) or, for a name-only call, from the buckets table's `bucketNameGSI`. `copy_s3_object()` is the one copy path: a managed copy by the destination-Region client, or a streamed GET→PUT when `VAMS_LAMBDAS_IN_VPC` is `true` and the Regions differ, because an S3 interface endpoint does not serve `CopyObject` across Regions. The client config is `S3_ASSET_CLIENT_CONFIG` (SigV4, path style, adaptive retries, the regional `us-east-1` endpoint). `tests/handlers/databases/test_buckets_listing_region.py` holds the listed handlers at zero plain S3 clients, and the test mock `tests/mocks/common/s3.py` carries the same surface (`tests/common/test_s3_region_routing.py` asserts the parity).

3. **New ARN/URL validators must accept every partition.** `common/validators.py` defines `aws_partition_group` (`aws`, `-us-gov`, `-cn`, `-eusc`, `-iso[-x]`) and `aws_dns_suffix_group` (all seven suffixes). Compose new patterns from these groups — never inline `arn:aws:`. A pattern that hardcodes the commercial partition passes every commercial test and rejects legitimate input **only** in the affected partition, which is the hardest failure mode to attribute.

    ```python
    # ✅ CORRECT — composed from the shared groups
    my_arn_pattern = r'^arn:(' + aws_partition_group + r'):lambda:[a-z0-9\-]+:[0-9]{12}:function:.+$'

    # ❌ INCORRECT — rejects GovCloud and EU Sovereign at runtime
    my_arn_pattern = r'^arn:aws:lambda:.+$'
    ```

    `infra/lib/helper/const.ts` (`SERVICE_LOOKUP`) is the authoritative partition + suffix list; keep these groups in step with it.

4. **A service or model may not exist in every partition.** Bedrock model ids differ (both restricted config templates pin an older Sonnet), OpenSearch engine versions differ, and some services are absent entirely. A handler that hard-codes a model id or assumes a service is reachable fails only where it is not — prefer taking such values from configuration that the CDK layer supplies per partition.

Feature switches are the runtime signal for partition-conditional behavior: `GOVCLOUD` is present in `featuresEnabled` for **both** GovCloud and EU Sovereign (both templates set `app.govCloud.enabled: true`), so treat it as "restricted partition" rather than literally GovCloud. See `infra/CLAUDE.md` "Partition Portability" for the CDK-side rules, including the `AWS::Lambda::EventSourceMapping` tag restriction.

---

## Testing

Run `pytest` from `backend/` after `pip install -r requirements-dev.txt`; CI runs it in parallel with `pytest -n auto --dist loadfile` (`pytest-xdist`), so every test must pass in any file order (isolation rules: `backend/tests/CLAUDE.md`, "Running in parallel"). Tests live under `backend/tests/[domain]/`; markers are `unit`, `integration`, `slow`, `aws`, `temporary` (root CLAUDE.md Rule 13). Full configuration, mock module hierarchy, `conftest.py` layering, and event-shape conventions: see `backend/tests/CLAUDE.md`.

## Templates

New-handler / model / test skeletons: `backend/HANDLER_TEMPLATES.md`. Gold Standard: `backend/handlers/assets/assetService.py`.

## Fork Extension Points

Upstream VAMS ships these files empty and never edits them again, so a fork that adds net-new
endpoints writes only here and merges upstream releases without conflicts. Each opens with the same
"Fork extension point" header. Full walkthrough: `documentation/docusaurus-site/docs/developer/customizing-vams.md`.

| What a fork adds                                   | Fork-owned file                                                       | Spliced in by                                                               |
| -------------------------------------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| API routes                                         | `custom/customApiRoutes.py` (`CUSTOM_ROUTES`)                         | `common/apiRoutes.py` → `ALL_API_ROUTES`                                    |
| Audit actions of those routes                      | `custom/customAuditActions.py` (`CUSTOM_AUDIT_ROUTE_ACTIONS`)         | `common/auditActions.py` → `AUDIT_ROUTE_ACTIONS`                            |
| Resource-name keys                                 | `custom/customResourceNames.py` (`CustomResourceKeys`)                | resolved with `get_resource_name()` like a core key                         |
| Handlers                                           | `handlers/custom/<name>.py` (`handlers.custom.<name>.lambda_handler`) | the shared `backend/backend` code asset                                     |
| Models                                             | `models/custom/<name>.py`                                             | imported by the fork's handlers                                             |
| Object types, constraint fields, deny-list entries | `custom/customConstants.py`                                           | `common/constants.py` (merged at import)                                    |
| Log redaction keys                                 | `custom/customConstants.py` (`CUSTOM_LOG_SENSITIVE_KEYS`)             | `common/constants.py` → `customLogging/logger.py`, `common/logRedaction.py` |

An add-on is optional, upstream-maintained code enabled by configuration: it either integrates VAMS with an external solution deployed and run separately (Garnet Framework, Physna), supplying the VAMS-side components, or is a stand-alone non-core component that VAMS deploys in full when enabled (the licensed VNTANA and VEERUM viewers). Custom is fork code, which upstream ships empty. Upstream functionality goes in the add-on lane when it integrates an external solution, depends on an AWS service outside the tier-1 set, or implements a use-case lens for an industry; viewers and pipelines keep their own lanes, and a fork uses the custom lane. The tier-1 services, the exemptions and the toggle rule: root `CLAUDE.md` "Core, add-on and custom". Definition and current add-ons: `documentation/docusaurus-site/docs/developer/addons-overview.md`.

The add-on lane sits beside each core list and is upstream-owned: `common/addonApiRoutes.py`,
`common/addonAuditActions.py`, `common/addonResourceNames.py` (`AddonResourceKeys`), `common/addonConstants.py`, `handlers/addon/`
and `models/addon/` (the Physna viewer and EDH models). Every list merges in the order core, add-on, custom.

**A disabled add-on is not listed.** Each add-on route names its feature switch in
`ApiRoute.addon_feature` (the add-on's, or that of the optional feature that registers it), and each add-on
object type has one in `ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES` (`common/addonConstants.py`). The switch names
are in `common/addonFeatures.py`, mirroring `infra/common/addonAppFeatures.ts`. The core stack passes the
enabled add-on switches, comma-separated, in `ADDON_FEATURES_ENABLED` to the routes, constraints and
constraint-template Lambdas only. With a switch absent, `get_public_api_routes()` leaves its routes out
(`GET /auth/routes/api` lists none, `GET /auth/routes/api/allowed` evaluates and audits none),
`permissionObjects` leaves its object types out, and `deployed_object_type_error()` (`models/roleConstraints.py`)
refuses a new constraint or permission template naming one ("belongs to an add-on, or an optional add-on feature,
that is not enabled on this deployment"). A constraint already stored with such a type still reads, lists and deletes, and Casbin still
loads it. Without the variable (local dev server, a test that does not set it) everything is listed. A new
add-on route or object type gets its tag (`tests/common/test_addon_features.py`;
`tests/handlers/auth/test_addon_listings_follow_deployment.py` holds the behaviour).

-   Fork handlers follow every rule above, including both Casbin tiers with the fail-closed
    pre-set flag (Rule 4) and dispatch on the `ApiRoute` constants from `custom/customApiRoutes.py`
    (Rule 12). Tier 2 uses a core entity type or a permission object type the fork registers in
    `custom/customConstants.py` (`CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS` + `CUSTOM_PERMISSION_CONSTRAINT_FIELDS`).
-   **Constants lanes.** `common/constants.py` keeps the core lists as `CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES`,
    `CORE_PERMISSION_CONSTRAINT_FIELDS`, `CORE_CONSTRAINT_OBJECT_TYPE_FIELDS`, `CORE_UNALLOWED_FILE_EXTENSION_LIST`
    and `CORE_UNALLOWED_MIME_LIST`, and builds the public names (no `CORE_` prefix) as core + add-on + custom at
    import. Consumers import the public names only (`tests/common/test_constants_extension_lanes.py` checks).
    A lane entry that breaks a rule raises `ConstantsExtensionError` naming the file and entry, which fails
    every Lambda's import (fail closed): names match `^[A-Za-z][A-Za-z0-9]{1,63}$` (no underscore, so no
    `x__y` shape and nothing that leaves the `r.obj.<field>` position in a rule), are not `object__type`,
    `route__path` or `method`, not a Python keyword or `EVALUATOR_RESERVED_ATTRIBUTES` name, and not a core or
    other-lane entry; a type's fields exist in the merged field set; samples are `""` or `[]` (list fields
    take only `is_one_of` / `is_not_one_of`). Type and field names are compared ignoring letter case when
    checking for a repeat, so `Asset` repeats `asset` and `DatabaseId` repeats `databaseId`; authorization
    still matches a name exactly. Deny-list entries are append-only, lower-case, `.ext` / `type/subtype`.
    The log-key lists follow the same shape: `CORE_LOG_SENSITIVE_KEYS` (the structured logger) and
    `CORE_LOG_TEXT_SENSITIVE_KEYS` (the free-text redactor) become `LOG_SENSITIVE_KEYS` and
    `LOG_TEXT_SENSITIVE_KEYS`, both extended by the one lane list `ADDON_LOG_SENSITIVE_KEYS` /
    `CUSTOM_LOG_SENSITIVE_KEYS`, append-only. A log key matches `^[A-Za-z][A-Za-z0-9_-]{1,63}$` and repeats
    no core or other-lane key in any letter case, because both redactors match keys case-insensitively.
-   **No automatic grant.** An extension object type gets no seeded constraint: the
    `dynamodb-authdefaults-*` constructs are not extended, so every role, admin included, is denied on it until
    the fork adds a constraint (`tests/handlers/authz/test_extension_object_types.py`). Never add one.
    An add-on may seed constraints for its own types, gated on its flag, through
    `infra/lib/nestedStacks/addon/addonDefaultConstraints.ts`.
-   Tests that pin constants must hold for any lane content: pin the `CORE_*` lists, or load a private copy of
    `constants.py` with stand-in lane modules; never assert that the merged lists equal core.
-   `custom/` modules import `ApiRoute` from `common/apiRouteTypes.py` and `ResourceParamKey` from
    `common/resourceNames.py`; nothing in core imports a fork handler or model.
-   `customConfigCommon/` is different: it holds override hooks for **existing** behaviour (the
    authorizer MFA check, the login profile), which a fork edits in place.
-   Recommended (not enforced) names: routes under `/custom/`, resource keys under `custom/`.
    An exact duplicate of a core route or key is rejected (`tests/common/test_custom_extension_points.py`,
    and at CDK synth). A resource-name key resolves a name, never a secret; a handler reads a secret
    from AWS Secrets Manager at runtime.

---

## Key Dependencies

Runtime: `aws-lambda-powertools` 2.36.0 (Logger, Parser, BaseModel, typing), `boto3` 1.43.89 / `botocore` 1.43.89 (botocore **≥1.36** is required for the `aws-eusc` EU Sovereign Cloud partition — older releases resolve `eusc-de-east-1` endpoints to the wrong `.amazonaws.com` suffix; botocore **≥1.43.89** is the floor for the DynamoDB `SearchVectors` operation, pinned by `tests/common/test_vector_api_floor.py`), `casbin` 1.36.0 (ABAC/RBAC), `pydantic` 1.10.13 (v1 ONLY), `opensearch-py` 2.7.1, `simpleeval` 1.0.7 (safe expression evaluation in Casbin matchers), `locked-dict` 2023.10.22 (thread-safe Casbin cache).

Dev only: `moto` 5.1.0 (AWS mocks), `pytest` 9.0.3, `mypy` 1.0.0, `flake8` 6.0.0.

---

## Updating Python Dependencies (Poetry-Managed)

Wherever a `pyproject.toml` sits next to a `requirements*.txt`, the requirements file is a **generated artifact** exported from `poetry.lock` — never edit it by hand. Poetry-managed projects: `backend/`, `backend/lambdaLayers/base/`, `backend/lambdaLayers/authorizer/`, and `backendPipelines/multi/rapidPipelineEKS/lambdaLayer/`.

To change a dependency version:

1. Edit the constraint in `pyproject.toml` only if the current constraint excludes the target version (exact pins like `urllib3 = "2.6.3"` must be edited; ranges like `^2.12.1` that already admit the target need no edit).
2. Re-resolve without installing: `poetry update --lock <package> [<package>...]`
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

Requirements files with **no** side-by-side `pyproject.toml` (e.g. `backendPipelines/multi/rapidPipelineEKS/lambda/requirements.txt`) are hand-maintained pip files, edited directly.

---

## Anti-Patterns -- What NOT to Do

Most anti-patterns are the inverse of a Critical Rule above. The compact list cites the rule each violates:

-   Raw dict responses with status codes — Rule 7. Use `success` / `validation_error` / `general_error` / `internal_error`.
-   Skipping the `enforceAPI()` check in `lambda_handler` — Rule 4.
-   Missing `object__type` annotation before `enforce()` — Rule 4.
-   Gating a single-resource `enforce()` on `if len(tokens) > 0:` without an `else` that denies — Rule 4. Fails open on empty tokens; the list-filtering "append only when `enforce()` passes" shape is the one exception (fail-closed by construction).
-   Treating a single filtered `query`/`scan` as a lookup or an existence check — Rule 14. The `FilterExpression` runs after the 1 MB page read, so empty `Items` with a `LastEvaluatedKey` present means "the match is on a later page", not "absent". Loop to exhaustion, or query a GSI on the attribute.
-   Naming a key attribute of the table or index a `query` reads in its `FilterExpression` — DynamoDB refuses it ("Filter Expression can only contain non-primary key attributes", a 500), and moto does not, so unit tests pass. Put the key in the `KeyConditionExpression`, match a non-key copy (the compliance `assetSearchText`), or match in code. `tests/handlers/compliance/test_complianceQueryFilterKeys.py` checks every compliance read against the key schemas the CDK defines (`tests/handlers/compliance/_cdkTables.py`).
-   Terminating a paging loop on `response.get('LastEvaluatedKey')` truthiness instead of key presence — see Pagination Patterns. Equivalent in production, divergent under test.
-   Emitting a pagination token the request model cannot accept back (raw `LastEvaluatedKey` dict vs a declared `str`) — see Pagination Patterns. Caps the listing at page one with no error.
-   Inline regex validation — Rule 3. Use the `validate()` dispatcher.
-   `print()` for logging — Rule 5.
-   Creating boto3 clients inside functions — Rule 6.
-   Pydantic v2 imports or syntax (`ConfigDict`, `field_validator`, `model_validate`, `model_dump`, or `from pydantic import BaseModel`) — Rules 1 and 2.
-   Resolving resource names inside handler functions — Rule 10.
-   Returning raw error strings (`{'statusCode': 400, 'body': 'bad request'}`) — Rules 7 and 11.
-   Echoing user input or internal details in client error messages — Rule 11.
-   Hardcoding `arn:aws:` in an ARN string or a validator regex — see Partition Portability. Compose from `aws_partition_group` / `aws_dns_suffix_group`, or derive the partition from an ARN already in hand; a commercial-only pattern fails only in GovCloud / EU Sovereign.
-   Passing `endpoint_url` or a hardcoded hostname to a boto3 client — see Partition Portability. Let the SDK resolve per region; read service endpoints from SSM.

### Swallowing exceptions silently

A bare `except Exception: pass` hides real failures — no log line, no metric, no re-raise. Log the error, then decide whether to raise, translate to a `VAMSGeneralErrorResponse`, or continue with a documented best-effort behavior:

```python
try:
    do_something()
except Exception as e:
    logger.exception(f"Error in do_something: {e}")
    raise VAMSGeneralErrorResponse("Operation failed")
```

The best-effort helpers `common.assetHistory` and `common.syncTracking` are the deliberate exceptions: they catch and log their own failures so a history-write failure never breaks the primary operation. New handlers should not replicate that shape without an equally explicit rationale.

---

## Development Checklist

When creating or modifying a handler:

-   [ ] Imports in standard order (stdlib, boto3, powertools, common, handlers, models)
-   [ ] Module-level: AWS clients with `retry_config`; `safeLogger(service_name=...)`; resource names via `get_table_name(ResourceKeys.*)` inside one `try/except`; DynamoDB tables initialized from resolved names
-   [ ] `lambda_handler` extracts claims via `request_to_claims(event)` first
-   [ ] Tier-1 auth via `casbin_enforcer.enforceAPI(event)` with the fail-closed pre-set flag pattern
-   [ ] Routes dispatch via `ApiRoute.matches()` (not hardcoded fragments)
-   [ ] Request bodies parsed with `parse(body, model=ModelClass)`; params validated with the `validate()` dispatcher
-   [ ] Tier-2 auth via `casbin_enforcer.enforce(event, item)` with `object__type` set; empty-token case fails closed with an explicit `authorization_error()` before any single-resource `enforce()`
-   [ ] Every `query`/`scan` either pages to exhaustion or returns a round-tripped `NextToken` — no single filtered call standing in for a lookup, existence check, or full listing (Rule 14, Pagination Patterns)
-   [ ] Business logic errors raise `VAMSGeneralErrorResponse`; error handling maps ValidationError→400, VAMSGeneralErrorResponse→400, Exception→500
-   [ ] Client error messages are generic (no echoed input or internal details); response functions pass `event=event` for audit logging
-   [ ] No `print()`, no Pydantic v2 syntax; models declare `extra='ignore'`
-   [ ] Tests exist with proper mocking via `conftest.py` (see `backend/tests/CLAUDE.md`)
