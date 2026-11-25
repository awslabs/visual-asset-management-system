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
-   **Tests**: pytest 9.2.0 + moto 5.2.5 (see `backend/tests/CLAUDE.md`)
-   **Gold Standard**: `backend/handlers/assets/assetService.py` (handler), `backend/models/assetsV3.py` (model)

---

## Directory Structure

> Update this tree when adding new handler domains, model files, or test directories (root `CLAUDE.md` Rule 11).

```
backend/
├── conftest.py, pytest.ini              # See backend/tests/CLAUDE.md
├── pyproject.toml, uv.lock              # uv-managed deps
├── requirements.txt, requirements-dev.txt  # Exported from uv.lock
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
│   │   ├── auditActionTypes.py                     # AuditSpec, AuditLogGroup, audit group keys and kinds (re-exported by auditActions.py)
│   │   ├── auditLogGroups.py                       # Audit log group registry: one AuditLogGroup descriptor per group (core +
│   │   │                                           #   ADDON_AUDIT_LOG_GROUPS + optional CUSTOM_AUDIT_LOG_GROUPS). The writer's group
│   │   │                                           #   constants derive from it; the audit search reads it. See docs/developer/audit-logging.md
│   │   ├── auditSearch/                            # Audit search library (pure; no AWS call, route or config): queryBuilder
│   │   │                                           #   (validated filters -> Logs Insights query), containsFilter, parsers/ (contractV1 +
│   │   │                                           #   legacy adapters by auditVersion), rowModel, recordMatcher (recheck on parsed rows),
│   │   │                                           #   redaction, exportEncoding, registryCheck. Tests: tests/common/auditSearch/
│   │   │                                           #   API layer: the internal codes groupUnknown and groupNotAllowed must give ONE generic error response (no group-existence leak)
│   │   ├── auditSearchText.py                      # search_text_parameters(): the typed text of a search or text-filter
│   │   │                                           #   read (query, metadata text, filter options, typed tags) goes under the redacted `searchText` request
│   │   │                                           #   parameter; `tests/common/test_audit_search_text.py` pins the limits of its scan
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
│   │   ├── compliance/quarantineGuard.py           # quarantine-blocks-download guard shared by seven
│   │   │                                           #   Lambdas: the five asset download Lambdas
│   │   │                                           #   (zipDownloadService among them),
│   │   │                                           #   assetFiles (a copy to another asset) and
│   │   │                                           #   executeWorkflow (a caller's input files; not a
│   │   │                                           #   SYSTEM_USER cross-call, so pipeline rules run);
│   │   │                                           #   resolves the asset-state
│   │   │                                           #   table only when COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD=true;
│   │   │                                           #   refuses with QuarantineBlockedResponse (400, body
│   │   │                                           #   exactly its message: QUARANTINE_BLOCK_MESSAGE, or the
│   │   │                                           #   copy / workflow-input message, no "VAMS General
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
│   │   ├── assetLocations.py                       # What an assetLocation.Key covers: a key ending in '/' is a
│   │   │                                           #   folder, any other key a single-file asset (object, companion
│   │   │                                           #   folder, own previews): contains, relative_path_in,
│   │   │                                           #   object_key_for, names_own_object, folder_listing_prefix, in_folder,
│   │   │                                           #   locations_share_key (do two locations overlap);
│   │   │                                           #   the location index (location_attributes, derive_location,
│   │   │                                           #   resolve_owner, find_conflicts) and its readiness (index_ready)
│   │   ├── bucketRegistrations.py                  # A bucket registration row's state: retired, duplicateOfBucketId
│   │   │                                           #   (active_registrations, primary_registrations, same_registration)
│   │   ├── fileAttribution.py                      # Which asset an S3 object belongs to (attribute_object):
│   │   │                                           #   the record's vamsBinding, the location index, then (index
│   │   │                                           #   not ready) the stamp and the first folder, each with the
│   │   │                                           #   bucket and containment checks
│   │   ├── vamsObjectTags.py                       # The VAMS:databaseId / VAMS:assetId object tags bucket sync
│   │   │                                           #   writes on each synced version (merge, b64: values, limits)
│   │   ├── downloadEligibility.py                  # Download eligibility (asset distributable, key containment +
│   │   │                                           #   length, asset-version pin, delete marker 410, blocklist,
│   │   │                                           #   whole-prefix refusal, is_file_archived); caller injects S3
│   │   │                                           #   client, getters, resolvers; signing stays in the caller
│   │   ├── dynamodb.py                             # query_all_items, query_has_match, batch_get_items,
│   │   │                                           #   to_update_expr, get_asset_object_from_id
│   │   ├── resourceNames.py                        # SSM resource-name resolver + ResourceKeys
│   │   ├── ssmParameters.py                        # bounded, cached single-parameter Parameter Store lookup
│   │   ├── databaseAccess.py                       # DatabaseAccessManager (accessible-database scan + Casbin)
│   │   ├── indexing/documentIds.py                 # OpenSearch doc ids + vector fileVersionKey / segment keys
│   │   ├── indexing/fileEnumeration.py             # enumerate_latest_live_files (reindexers)
│   │   ├── vectorsearch/embeddings.py              # Bedrock embedding adapters; vendored byte-identical into the system pipeline
│   │   ├── vectorsearch/vectorStore.py             # VectorStore protocol + DynamoDbVectorStore (SearchVectors, MAX_TOP_K)
│   │   ├── vectorsearch/fileClassIntent.py         # FILE_CLASSES phrases + query file-type intent (soft boost)
│   │   ├── s3.py                                   # S3 file validation + paged list helpers; per-Region S3 client
│   │   │                                           #   factory and the Region-routing client the asset handlers hold;
│   │   │                                           #   presigned_get_object_params (SVG response overrides)
│   │   ├── largeObjectCopy.py                      # Large object copy engine: settings, part planner, job/batch rows, exactly-once batch report, cancel, ingestion hold
│   │   ├── zipDownloads.py                         # Download as Zip: job status machine (conditional terminal writes), keys under vams.preparedDownloads/, request ids, archive names, finalization rules, job-prefix cleanup
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
│   │   ├── assetUploads.py                         # completion_refusal: record-level checks on a stored upload record
│   │   │                                           #   (asset/database, upload type, expiry, workflow output), shared by
│   │   │                                           #   the upload completion and the ingest-asset complete stage, which
│   │   │                                           #   checks before it creates a missing asset (pure); also the database
│   │   │                                           #   extension rule, shared with the ingest initialize stage's name check
│   │   ├── indexerEvents.py                        # s3_records_from_indexer_message: SQS→SNS→S3 unwrapping of a
│   │   │                                           #   file-indexer message, shared by fileIndexer and complianceTrigger
│   │   ├── eventLogSummary.py                      # event_log_summary: one-line shape/count description of a Lambda
│   │   │                                           #   event for the entry log line (never a record body or image);
│   │   │                                           #   exception_log_summary: class + HTTP status of a search-service error
│   │   ├── timestamps.py                           # utc_now(): naive UTC "now" (stored-string format); use instead of datetime.utcnow()
│   │   ├── validators.py                           # validate() dispatcher + regex patterns
│   │   └── workflows/                              # Execution/pipeline/workflow shared helpers (pure); incl. subExecutionStages.py (ASL frame + history → per-stage status), availableLogs.py (log-source identity, dedup, read planning) and executionLogCapture.py (the bounded, redacted execution log stored on an execution record)
│   │       ├── executionRecords.py                 #   storage record builders, keys, S3 prefixes,
│   │       │                                       #   workflow.execution.completed event builder
│   │       ├── executionOutputs.py                 #   output attribution + resolved manifest build; the shared
│   │       │                                       #   sub-process stop helper + ABORTABLE_RESOURCE_TYPES (SFN,
│   │       │                                       #   Batch, Deadline Cloud, SageMaker training job / pipeline
│   │       │                                       #   execution) used by both the abort API and the failure path;
│   │       │                                       #   the end-state step's per-run ingest ledger (#1271)
│   │       │                                       #   terminal-status guard + the shared completion-event emitter
│   │       ├── systemRecords.py                    #   isSystem: importer cross-call check + read-only guard rules/messages;
│   │       │                                       #   is_system_record / names_system_record (no compliance pipeline rule on one)
│   │       ├── systemPipelines.py                  #   system pipeline runs: server-only trigger (System-<Identifier> format)/context from a marked cross-call, re-run refusal
│   │       ├── executionLocks.py                   #   concurrency lock rows for perAsset / perInputFile / perInputFileVersion (sorted all-or-none acquire, holder-conditional release, TTL)
│   │       ├── executeWorkflowInvoke.py            #   the executeWorkflowV2 Invoke of the trigger dispatcher and the system-pipeline launchers: one attempt per call, sent again (at most twice) only after a connection failure before delivery
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
│   │   │                                           #   assetVersions, assetHistory, etc.;
│   │   │                                           #   zipDownloadService (Download as Zip: the seven
│   │   │                                           #   /download/zip routes, the read-time
│   │   │                                           #   reconcile and the delayed sweep)
│   │   ├── audit/auditLogService.py                # audit log search: /audit routes, Logs Insights, handles,
│   │   │                                           #   caps; Tier 2 auditLog per log group (see common/auditSearch). Handle row TTL = handleRetentionMinutes after last use; contains terms in `containsTerms`, removed on stop/fail/export done. Round 2: no `recordsMatched` or any pre-recheck count in a response (rows are rechecked, then deduplicated); row time is one UTC ISO ms format (`rowModel.normalize_time`, sort on `time_ms`); a stopped search returns no rows; the concurrency caps are atomic counters (`slots#all`, `slots#user#<id>` items in the handle table, conditional increment on start, released by the closer that removes `openKey`, self-expiring); the hidden-group probe is one summary denial per call
│   │   ├── auth/                                   # apiGatewayAuthorizerRest, apiKeyService,
│   │   │                                           #   constraints, cognito, preTokenGen,
│   │   │                                           #   apiOriginVerifyRotation (secret rotation);
│   │   │                                           #   __init__: request_to_claims() → claims
│   │   ├── authz/__init__.py                       # CasbinEnforcer proxy (ABAC/RBAC)
│   │   ├── compliance/                             # Compliance. API handlers (one Lambda each):
│   │   │                                           #   complianceSchemaService (schemas CRUD incl. DELETE; the
│   │   │                                           #   listing pages LatestVersionIndex, a save moves the
│   │   │                                           #   latestVersion marker in one TransactWriteItems, and the first
│   │   │                                           #   listing marks pre-marker rows once, bounded and conditional,
│   │   │                                           #   _mark_latest_versions_once; a scope move that would leave a
│   │   │                                           #   binding outside the new scope is refused, _narrowing_refused),
│   │   │                                           #   complianceSchemaBindingService (db/asset bindings;
│   │   │                                           #   a database bind/change/unbind saves the binding,
│   │   │                                           #   answers 202 and is applied in the background by
│   │   │                                           #   complianceBindingApply: apply record
│   │   │                                           #   `bindingApply#<db>` in the evaluation table, message
│   │   │                                           #   with ids only, run() pages from the job Lambda
│   │   │                                           #   in a records phase (409 while it runs, queues no
│   │   │                                           #   evaluation; walks live then `<db>#deleted` records,
│   │   │                                           #   updating existing ones only, archived_partition)
│   │   │                                           #   then an evaluations phase (shares on
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
│   │   │                                           #   state; sweep and Evaluate All read bound records a page at a
│   │   │                                           #   time under the time budget, NextToken = phase + cursor,
│   │   │                                           #   _SweepWalk; assetsRemaining a lower bound), complianceQuarantineService, complianceCascadeService,
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
│   │   │                                           #   (async invoke from complianceCascadeService, event {"cascadeId"};
│   │   │                                           #   retryAttempts 0: a run with less than RUN_DEADLINE_MARGIN_MS left
│   │   │                                           #   records nodes/partialResults/heartbeatAt and invokes itself with
│   │   │                                           #   {"cascadeId", "run": n+1}, fenced on runNumber; a reader aborts
│   │   │                                           #   a cascade executing with no heartbeat for
│   │   │                                           #   CASCADE_RUN_STALE_SECONDS, expire_stopped_cascade),
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
│   │   │                                           #   its outcome (D159). A SUCCEEDED of the same
│   │   │                                           #   execution supersedes a recorded FAILED pipeline
│   │   │                                           #   rule (supersede_pipeline_rule_outcome, #1504:
│   │   │                                           #   reopen on the finalization read, replace only the
│   │   │                                           #   FAILED of that executionId, finalize again; never
│   │   │                                           #   SUCCEEDED->FAILED, never a closed evaluation).
│   │   │                                           #   Every binding write (database bind/change/unbind of a
│   │   │                                           #   quarantined record, asset override set/clear) stamps
│   │   │                                           #   bindingChangedAt on the record; evaluation_ownership_condition
│   │   │                                           #   refuses an evaluation that began at or before it (its verdict
│   │   │                                           #   stays on its evaluation row), writes after the claim also need
│   │   │                                           #   attribute_exists(databaseId), and a claim on a record naming
│   │   │                                           #   another schema is refused (CLAIM_REBOUND). A claim whose
│   │   │                                           #   evaluation raises before it launched a workflow or recorded a
│   │   │                                           #   wait (`launched` empty) is undone from the claim's own copy
│   │   │                                           #   (LandedClaim; _undo_claim, conditional on the claim's revision;
│   │   │                                           #   a row it wrote is marked notApplied notRecorded; after a
│   │   │                                           #   launch the claim stays); a fenced write marks the row
│   │   │                                           #   notApplied bindingChanged;
│   │   │                                           #   a pending_pipeline claim older than
│   │   │                                           #   EVALUATION_CLAIM_STALE_SECONDS with no evaluation row no longer
│   │   │                                           #   covers a change (claim_orphaned). An evaluation naming a schema
│   │   │                                           #   other than the bound one is dry_run_evaluation (no write, no
│   │   │                                           #   workflow). A rule that cannot be read or an unresolved extends
│   │   │                                           #   parent gives verdict error and launches no pipeline rule,
│   │   │                                           #   whatever TOOLING_FAILURES_APPLY_ENFORCEMENT says.
│   │   │                                           #   complianceNotifications
│   │   ├── pipelines/                              # Pipeline CRUD (pipelineService = full CRUD;
│   │   │                                           #   pipelineTemplateService = templates + tagSchema)
│   │   ├── workflows/                              # Step Functions workflow mgmt. API-facing:
│   │   │                                           #   workflowService (CRUD), executeWorkflow
│   │   │                                           #   (asset-less multi-file execute), executionService
│   │   │                                           #   (list/abort/details/logs/rerun/permanent; four
│   │   │                                           #   functions, EXECUTION_SERVICE_ROUTES: service =
│   │   │                                           #   lists/abort/rerun, details = details + its pages,
│   │   │                                           #   logs = logs, permanentDelete = that route on the
│   │   │                                           #   one role that deletes rows; other routes 404),
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
│   │   │                                           #   systemWorkflowLauncher (SQS → marked system call, one concurrency slot per run),
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
    `retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})`. A client that is
    bounded instead (shorter timeouts, fewer attempts, or none) is a recorded departure, listed with
    its reason in `DELIBERATE_DEPARTURES` in `tests/common/test_shared_client_retry_config.py` and
    beside the client: for example `common/resourceNames.py`'s `sweep_client_config`, whose sweep runs
    during Lambda init and retries under its own budget, and `common/ssmParameters.py`'s
    `parameter_client_config`, the single-parameter lookup's client. A handler builds no Parameter
    Store client of its own (see "Other Parameter Store values" under Environment Variables).

7.  **ALWAYS raise `VAMSGeneralErrorResponse`** for business logic errors. Never return raw dicts with status codes.

8.  **ALWAYS use `extra='ignore'`** on every Pydantic model class to silently drop unexpected fields.

9.  **NEVER log sensitive data.** `safeLogger` auto-redacts the credential keys `authorization`, `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`, `apiKey`, `apiKeySecret`, `rawKey`, the credential headers `x-vams-origin-verify`, `cookie`, `set-cookie`, `x-api-key`, `proxy-authorization`, `x-amz-security-token`, the authorizer context key `vams:originVerifyDigest`, the credential field names `password`, `secret`, `clientSecret`, `client_secret`, `accessToken`, `access_token`, `refreshToken`, `refresh_token`, `privateKey`, `private_key`, the credential keys the add-on and fork constants lanes register (`ADDON_LOG_SENSITIVE_KEYS`, `CUSTOM_LOG_SENSITIVE_KEYS`; see "Fork Extension Points"), and the caller-authored content keys `configBody`, `templateTags`, `tagValues`, `customTemplateOverride`, `webFormJson`, `inputInstructions` (also inside a JSON-string request `body`), at every nesting level in dicts and lists. Do not circumvent this. The redaction is key-driven, so an f-string that interpolates a payload value bypasses it — log identifiers and counts, never rendered bodies or tag values. Never log a whole Lambda event, stream record or DynamoDB image, even as a structured dict: a metadata row holds the field name as a value (`metadataKey`), so key redaction cannot see it. Log `common.eventLogSummary.event_log_summary(event)` at entry and the record's ids where it is handled (`tests/handlers/test_handlers_log_no_whole_events.py` fails on an interpolated event).

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

    **Exception: an asset version's file entries.** The asset file versions table's `fileKey`
    (sort key, read back as `relativeKey`) is the asset-relative path WITHOUT the leading `/`
    (`folder/file.txt`). `assetVersions.save_asset_file_versions` strips it whatever form the
    caller sent, and `CreateAssetVersionRequestModel` refuses `/a.txt` and `a.txt` together as
    duplicates. Entries a version built from a file list stored before #1795 can still carry
    the slash and are not migrated, so every reader compares with the slash stripped on both
    sides (`str(fileKey).lstrip('/')`), a sort-key lookup tries the unslashed form first and
    the slashed form on a miss, and a delete uses the key the row is stored under.

14. **NEVER read only the first page of an S3 or DynamoDB listing when the full set is
    needed.** S3 `list_object_versions` / `list_objects_v2` and DynamoDB queries cap a
    single call (`MaxKeys`, `Limit`, one page). When completeness matters, page to
    exhaustion via the shared helpers `common.s3.list_all_object_versions()` /
    `list_all_objects()` (page-size constants `S3_VERSIONS_PAGE_SIZE` /
    `S3_OBJECTS_PAGE_SIZE`; both accept an optional `max_keys` / `max_objects` cap for
    best-effort sampling). A bare `list_object_versions(..., MaxKeys=N)` silently drops
    versions beyond `N` (wrong archive status, truncated history). Existence-only checks
    (`MaxKeys=1`) are the allowed exception. To classify one key from its history (live,
    archived or permanently deleted), use `list_key_versions_to_newest_version()`: it pages
    until it reaches a version of the key, an entry of another key or the end, so a key with
    more newer delete markers than one page holds is not read as permanently deleted.

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
    versions** — use
    `common.s3.is_object_version_archived(bucket, key, version_id, client=...)`. It issues
    one `HeadObject` (delete-marker → 405, live → 200, missing → 404), O(1) regardless of
    version count. Handler-local `is_file_archived` helpers must delegate to it, never
    re-implement a version scan.

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

The file metadata writes (`metadataService.create_file_metadata`, `update_file_metadata` and
`delete_file_metadata`) authorize the asset before they check the file: they read the asset, decide
Tier-2 on it (403 when refused), then key the path (`file_record_path`) and check the file in S3
(`validate_file_exists`, create and update only). A missing asset answers 400 `Asset not found`
before authorization, the convention `authorize_asset_read` documents for the metadata routes.

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
-   `request_to_claims(event)` returns `{"tokens": ["userId", ...], "roles": [...], "externalAttributes": [...], "mfaEnabled": bool, "authMethod": "jwt" | "apiKey", "apiKeyId": str | None}`. `authMethod` is `"apiKey"` when the authorizer context carries `vams:authMethod = apiKey` and `"jwt"` otherwise (JWT callers and Lambda cross-calls alike); handlers that mint or seed a credential refuse `"apiKey"` with 403 so one key cannot be chained into another. `apiKeyId` is the id of the key an API-key caller authenticated with (the authorizer's `vams:apiKeyId`), for a handler that decides by the key itself, and `None` for every other caller. `roles` comes from the `vams:roles` authorizer context value, which the authorizer (`common/auth/authorizerCore.py`) resolves from the user roles table with a 60-second per-user cache — so it is populated for every auth mode (Cognito, external OAuth IDP, API key), not only where a Cognito pre-token-generation trigger runs. It is informational for handlers and audit logs: `CasbinEnforcer` re-reads a user's roles from DynamoDB when building policy, so authorization does not depend on it. When `customAuthClaimsCheckOverride` raises, the claims also carry `CLAIMS_CHECK_FAILED` (`claimsCheckFailed: True`) with `roles` emptied: `CasbinEnforcer` built from them decides on a deny-all service (`POLICY_TEXT_DENY_ALL`, never put in the per-user caches) and `enforceAPI` refuses when the event's own claims carry it, so every route answers 403; `POST /auth/routes`, which has no Tier 1 check, refuses it with the no-identity 403. Tests: `tests/handlers/authz/test_claims_check_failure_deny_all.py`.
-   **Authorizer 401 versus 403 (#1212).** `authenticate_request` tags every unauthorized result with a `failure` kind. `FAILURE_UNAUTHENTICATED` (missing or malformed header; a JWT that is malformed, expired, badly signed, or has the wrong aud/iss/token_use or an unknown kid; an API key that is unknown, disabled, expired, has an unreadable expiry or no userId) makes `apiGatewayAuthorizerRest.lambda_handler` write the audit record and then raise `Exception("Unauthorized")` outside its catch-all, which API Gateway answers with 401. Everything else, `FAILURE_FORBIDDEN` or no kind at all, stays a Deny policy (403): IP not allowed, an API key whose user has no roles, a misconfigured `AUTH_MODE` or verifier, a JWKS/discovery outage (`TokenVerificationUnavailableError`), an API key table outage (`ApiKeyLookupUnavailableError`), any unexpected error. Keep three rules: every API-key "not usable" case gets the same kind as an unknown key (no existence leak); an outage or configuration fault is never `FAILURE_UNAUTHENTICATED` (it must not tell clients to sign in again); the raised message is exactly `Unauthorized` (anything else is a 500) and the internal reason never reaches the client. Tests: `tests/common/auth/test_authorizerCore_failure_kinds.py`, `tests/handlers/auth/test_apiGatewayAuthorizerRest_unauthorized.py`.
-   **The authorizer owns the `vams:` context namespace.** `authenticate_request` copies a verified token's claims into the authorizer context except every key under `RESERVED_CONTEXT_KEY_PREFIX` (`vams:`, matched without regard to case), then sets `vams:tokens` to `[username]` -- the one id `resolve_user_id(claims)` returns (`cognito:username`, then `username`, then `sub`; in external mode with `JWT_USER_ID_CLAIM` set, that claim only), the same value `_lookup_user_roles`, `resolve_mfa_enabled` and the audit record receive; the configured claim name may not start with `vams:` -- together with `vams:roles`, `vams:mfaEnabled` and `vams:externalAttributes` (`[]`). A verified token with no username is `FAILURE_UNAUTHENTICATED`. The API-key path builds its context from the key record alone. A new value the authorizer passes to handlers takes the `vams:` prefix and is written by the authorizer after the claim copy; handlers and `request_to_claims` never treat a token claim as identity, role, MFA or credential-type input. Tests: `tests/common/auth/test_authorizerCore_reserved_claims.py` (context) and `tests/handlers/auth/test_authorizer_identity_decisions.py` (Casbin decisions, self-service API key owner).
-   **Accepted token audiences (#1337).** `verify_cognito_jwt` accepts a token whose `aud` (ID token) or `client_id` (access token) is exactly `APP_CLIENT_ID` or one of `ADDITIONAL_APP_CLIENT_IDS`; `verify_external_jwt` does not hand the audience to PyJWT (`verify_aud` is off, `exp` and `iss` are required): `verify_external_audience` / `matched_external_audience` read it through `JWT_AUDIENCE_CLAIMS` (`aud` and/or `client_id`, default `["aud"]`; #788) against `[JWT_AUDIENCE, *JWT_ADDITIONAL_AUDIENCES]`, for the primary and the additional audiences alike -- the first listed claim present in the token decides and must name an accepted audience, so a later claim cannot rescue a mismatch. A malformed `JWT_AUDIENCE_CLAIMS` / `JWT_USER_ID_CLAIM` raises `TokenVerificationUnavailableError` (403), never a fallback. Both additional lists are JSON arrays set by CDK only when `app.authProvider.<provider>.additionalTokenAudiences` is non-empty, parsed fail-closed by `parse_additional_audiences` (malformed value, non-string, blank or `*` entry dropped), and compared by exact equality, never as a pattern. Signature (RS256 only), issuer, expiry and `token_use` checks do not depend on the audience. A Cognito access token without a non-blank `username` (the client_credentials machine-to-machine shape, `sub` = client id) is rejected for every client, and an external token accepted only through an additional audience must carry a user identity (`resolve_user_id`: the configured `JWT_USER_ID_CLAIM`, else `cognito:username`, `username` or `sub`) that is not its own `client_id`/`azp`/`cid`, and must carry no machine-token marker (`_MACHINE_TOKEN_CLAIM_MARKERS`: `gty` = `client-credentials` (Auth0), `idtyp` = `app` (Entra ID); `_MACHINE_TOKEN_USERNAME_PREFIXES`: `preferred_username` starting `service-account-` (Keycloak)); both are `FAILURE_UNAUTHENTICATED`. Tokens that name the primary audience (through whichever audience claim is configured) are not checked. The markers are a backstop, not a guarantee: other IdPs' machine tokens may carry none, so only user sign-in clients may be listed. Identity comes from `resolve_user_id` (`cognito:username`, then `username` (a Cognito access token), then `sub`, or the configured user id claim), and roles from the user roles table, so a token from an additional client resolves the same user and the same Casbin decision as a web token. `authenticate_request` returns the accepted audience as `tokenAudience` (`token_audience_for_audit`), which the REST authorizer passes to `log_authorization_gateway(..., token_audience=...)`; the audit record carries it as `tokenAudience` on a success only, and it is not added to the authorizer context. Tests: `tests/common/auth/test_authorizerCore_additional_audiences.py`, `tests/common/auth/test_authorizerCore_external_claims.py` and `test_authorizerCore_external_claims_combined.py` (audience and user id claims, alone and with additional audiences), `tests/handlers/auth/test_apiGatewayAuthorizerRest_token_audience.py`, `tests/common/test_auditLogging_token_audience.py`.
-   **MFA-aware**: roles with `mfaRequired=True` are only active when `mfaEnabled=True` in claims.
-   **Object annotation**: set `item['object__type']` before every `enforce()` call.
-   **Policy lines as data.** `CasbinEnforcer.effective_object_policies(object_type, action)` returns the caller's applicable lines (`effect`, usable `criteriaAnd`/`criteriaOr`) built by the same `_usable_criteria` filter as the policy text, or `None` when the enforcer was not built from them (shared-cache hit, failed build, unloadable text). Search turns them into OpenSearch filters with `common/searchAuthzFilter.py`: an `upper` bound that fetches every readable document and a `lower` bound used for totals and aggregations that cannot be exact, so counts never include a record the caller cannot open. The per-hit `enforce()` stays authoritative. A new operator, constraint field, or change to a rule's regex shape must be mirrored in `searchAuthzFilter.py`; `tests/handlers/authz/test_search_authz_filter.py` compares both bounds with `enforce()` over every field, operator and value shape and fails otherwise. A future search mode (for example vector search) reuses `build_search_authz_filters` / `apply_search_authz_filter` in `handlers/osSemanticSearch/search.py` instead of computing counts from unfiltered results.
-   **Natural-language window signals never depend on records the caller cannot open.** `vectorSearchService._databases_read_in_full` decides which searched databases the caller may open in full: every asset GET policy line constrains `databaseId` alone, and the user's enforcer allows an asset object of the database (also as `<databaseId>#deleted` with `includeArchived`), asked directly so no denial audit entry is written. Every other target is searched with `MAX_TOP_K` whatever `size` and always reported as possibly incomplete (`nlp.truncated` true, `relation` `gte`, `truncated:window`, never `segments:window_full`); only the windows of targets open in full are checked for filling. A new answer field derived from the vector items before `_authorize` follows the same split (`tests/handlers/osVectorSearch/test_vectorSearchService_partial_access_window.py`).
-   **Search fills the page.** `_search_until_page_full` in `handlers/osSemanticSearch/search.py` wraps every dual-index search (POST and simple search): while fewer than `from + size` hits pass the per-hit check and OpenSearch holds more candidates, it fetches a window up to `SEARCH_FILL_GROWTH_FACTOR` times larger, bounded by `OPENSEARCH_MAX_RESULT_WINDOW` per index and `SEARCH_FILL_MAX_SECONDS`; the window count is not a bound (each window is at least twice the last, so the loop ends within a few fetches), so small pages reach candidates ranked anywhere below the per-index cap. The time bound covers the fetches and every per-hit check (`DualIndexResponseProcessor.decide_in_order` stops at the deadline; `_affordable_window` sizes the next window from the measured fetch and check time per hit); a search stopped by a bound answers with the hits checked so far and reports `relation: gte`. A hit decided in an earlier window is counted from its remembered decision without a deadline check, so a larger window whose fetch ends past the bound still answers, in order, the hits the earlier windows checked, and stops at its first hit not yet decided (#1773); never count a hit as checked without a decision of this request. The deadlines are `_check_deadlines`: the fill deadline, `SEARCH_FILL_MAX_SECONDS` from the start, bounds every larger window and the first window's checks past the requested page; the first window's checks until the requested page is full (`decide_in_order`'s `page_deadline` / `page_readable`) may run to the page deadline, the later of the fill deadline and `SEARCH_FIRST_WINDOW_CHECK_SHARE` of the bound (5 s) after the first fetch answers (by design not the whole API budget: the refused-hit audit writes after the checks grow with their number and must fit the reserve), so a slow first fetch still has its page checked (#1590) and a warm search keeps the fill deadline throughout. Neither passes the API deadline, `_api_check_deadline` (#1763): `API_GATEWAY_TIMEOUT_FLOOR_SECONDS` (29 s, the floor `getConfig()` enforces for `apiGatewayTimeoutTime`) after API Gateway received the request (`requestContext.requestTimeEpoch`, so a cold start and the authorizer count), less `SEARCH_RESPONSE_RESERVE_SECONDS` (2.5 s) for the work after the checks. `lambda_handler` takes it first and passes it to both handlers; without a usable receipt time (none, not a finite number, an integer too large for a float, more than `REQUEST_TIME_SKEW_TOLERANCE_SECONDS` ahead, or older than the floor) it is `SEARCH_REQUEST_MAX_SECONDS` (20 s) from the handler's start. Never measure it with `context.get_remaining_time_in_millis()`: the Lambda timeout is not the API's. When the bound leaves fewer than `from + size` hits passed (`checks_cut_short`: it stopped the checks, or below the per-index cap it left no time for a larger window), `process_dual_search_response` reports `gte` whatever the filters and adds the `truncated:time` warning (`SEARCH_TIME_BOUND_WARNING_CODE`, `models.search.SearchWarningModel`, the `{code, message}` shape of the natural-language warnings); the web Search notice (`KEYWORD_WARNING_CODES`), the CLI table output and the MCP keyword search results show it. For a search over both indexes only the merged prefix in true order is answered (`_ordered_prefix_length`: cut at the earliest last hit of an index whose window was full), so pages neither skip nor repeat records. Larger windows drop `aggs` and keep the first window's aggregations; per-hit decisions are cached per request in `DualIndexResponseProcessor._decide`. Both keyword search handlers and natural-language search (`vectorSearchService.handle_nlp_search`) run inside `deferred_authorization_audit()`, so the refused-hit audit entries are written in batches when the search ends. A new search mode that post-filters hits routes through it instead of slicing one window.
-   **Search sorts are total.** Every keyword search sort, each larger fill window included, ends with `SORT_TIEBREAKER_FIELDS` (`handlers/osSemanticSearch/search.py`: database ID, asset ID and archive state, plus the file path on the file index), appended by `with_unique_tiebreaker`, so records equal on every key the caller gives keep one order from page to page. The merge of a search over both indexes applies the file query's sort, whose keys extend the asset keys, and orders a missing value last and a list by its smallest (ascending) or largest (descending) value, as OpenSearch does. A new sort path appends the keys too. Do not sort on `_id`: it needs field data for the id field, which a domain can turn off.
-   **Indexers re-read the source after a write.** A late or re-sent write must not leave a document for a record deleted or archived meanwhile. `process_asset_index_request` (`osAssetIndexer.py`) re-reads the asset row with a strongly consistent read after a successful write and deletes the document when the row is gone; `reconcile_written_file_document` (`osFileIndexer.py`) re-reads the S3 state and the asset row, then deletes the document or writes it once more from the current state. A failed re-read reports the record for redrive. Conditional or versioned writes are not used, because OpenSearch Serverless and provisioned domains do not offer the same document versioning. A test that stubs the file write also patches `reconcile_written_file_document`.
-   **A query keeps each word in its field.** `query_string` splits its text at unescaped whitespace, so never put a raw value with spaces into it. The general keyword query uses `FieldClassifier.any_word_anywhere_query` (a record matches when any word appears anywhere inside a value); a field filter with wildcards on an analyzed text field uses `every_word_in_field_query` (every word, in that field); a wildcard pattern on a keyword-typed field uses `whole_value_pattern` (one pattern for the whole value, whitespace escaped); a contains search of metadata values uses `every_word_anywhere_in_values_query`. A value the caller wrapped in exactly one pair of double quotes is that exact value on every metadata value and simple field filter entry point (#1299): `FieldClassifier.quoted_exact_value` recognizes it, `exact_phrase` escapes it with `*` and `?` literal, and `split_metadata_query` keeps an AND or OR separator inside it in the value. Never escape the caller's quotes into the searched value.
-   **Flat-object value fields depend on the query, not the engine.** `MD_` and `AB_` are `flat_object` fields. Name only the root (`MD_`, `AB_`) or a key (`MD_.<key>`) for an exact or prefix term; both engines rewrite those onto the values. Neither engine rewrites any other wildcard pattern (a leading `*`, a `?`, an inner `*`): on the root it matches the key paths and on a key nothing, so it names `<root>._value` for a value and `<root>._valueAndPath` (`MD_._valueAndPath:MD_.<key>\=<pattern>`) for one key's value. `FieldClassifier.flat_object_value_fields` and `flat_object_key_pattern_query` make that choice; use them, and do not branch on `OPENSEARCH_TYPE` or the engine version. Confirmed on OpenSearch Serverless and on a provisioned OpenSearch 3.5 domain; the 2.19 engine of the EU Sovereign Cloud has not been checked. Never hardcode `MD_._value`: an exact or prefix query on it matches nothing on either engine.
-   Valid object types (`ALLOWED_CONSTRAINT_OBJECT_TYPES` in `common/constants.py`, and the only values `GET /auth/constraints/permissionObjects` offers): `database`, `asset`, `api`, `web`, `tag`, `tagType`, `role`, `userRole`, `pipeline`, `workflow`, `metadataSchema`, `complianceSchema`, `complianceEvaluation`, `complianceCascade`, `complianceBinding`, `auditLog` (one field, `auditLogGroup`, the audit log group key; validated with the usual operators, not a closed list, so a group registered later needs no change; a check that carries no `auditLogGroup`, or an empty one, matches every role whose criterion is `.*` (the `admin` seed and the default `audit-reader` import) and no restricted role, so every `/audit` handler must always pass a registered group key and its guard test asserts it; the seeded `admin` role holds it for every group and no other seeded role or shipped template except `audit-reader.json` does), plus any type the add-on or fork constants lane registers (see "Fork Extension Points"). Anything else is rejected at constraint create/update with a `400`. There is no `apiKey` object type — API key access is governed at Tier 1 through `api` route constraints on `/auth/api-keys*` and `/auth/user/api-keys*`. One refusal runs in addition to Tier 1, after `enforceAPI` has allowed the route: a caller authenticating with a temporary session key — an API key named with `TEMPORARY_SESSION_KEY_NAME_PREFIX` (`temporary-session-`, `common/constants.py`) that a backend component mints for one session through a Lambda cross-call, identified through the `apiKeyId` that `request_to_claims` surfaces — is answered `403` on API-key create and update in every scope, so a session credential cannot mint or extend a key (a caller whose own key record cannot be read is answered the generic `500`, not that refusal). The prefix is reserved: an API-key create that names a key under it is answered `400` unless it arrives as a Lambda cross-call (`tests/handlers/auth/test_apiKeyService_temporary_session_key_callers.py`).

### System User (`SYSTEM_USER`)

`SYSTEM_USER` is the **only** valid user ID for system-process actions — never `SYSTEM`, `system`, or any other variant. It is seeded into the user and user-roles tables during CDK deployment and assigned to the `admin` role, so actions attributed to it pass Casbin authorization. Use it for: Lambda cross-calls (`{'lambdaCrossCall': {'userName': 'SYSTEM_USER'}}` — also the default in `request_to_claims()` when `userName` is omitted); username fallbacks (`claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]`); provenance/audit values (`createdBy`, `modifiedBy`, `changeUserId` fallbacks); and identity comparisons (`skip_schema_validation = (username == "SYSTEM_USER")` in `metadataService.py`, the pipeline-execution bypass in `processWorkflowExecutionOutput.py`). Off-schema keys a pipeline stores this way pass `restrictMetadataOutsideSchemas` on later user writes: every enforcing write path hands `validate_metadata_keys_against_schema` the record's keys as stored before the write (`stored_keys`), so only a key new to the record and absent from the schema is refused. Value type and controlled-list checks follow the same rule: every user write path passes `validate_metadata_against_schema` `value_check_keys=keys_set_by_write(incoming, stored)`, so a stored value the schema refuses blocks no write that leaves it unchanged, while required-field and dependsOn checks still read the merged record.

Handlers compare against this exact string, so a mismatched variant silently fails the comparison (or attributes records to a user ID with no admin role). IAM permissions on direct Lambda invocation are the security boundary for who can inject a `lambdaCrossCall` event.

### Internal Actions (Direct Invokes No API Route Reaches)

A handler that serves another VAMS Lambda an operation with no API route dispatches it from a top-level event key before any API handling: `workflowService` serves `vamsInternalAction` (`refreshPipelineReferences`, `redeployWorkflow`; names and the event builder in `common/workflows/workflowRecords.py`). The action runs only when the event is a `SYSTEM_USER` `lambdaCrossCall` with **no** `requestContext` — API Gateway builds every proxy event with a `requestContext` and cannot add top-level keys, so no route reaches it — and its payload ids pass the `ID` validator. Derive the targets server-side from the payload's own entity (the refresh takes a pipeline and finds its workflows) rather than accepting a target list, and grant callers `lambda:InvokeFunction` on the handler's function ARN only.

The same top-level-envelope rule carries the system-record exemption. `common/workflows/systemRecords.is_schema_import_call` is true only for a `lambdaCrossCall` whose `userName` is `SYSTEM_USER` **and** whose `source` is `vamsSchemaImport`, which only `importGlobalPipelineWorkflow` sends; that caller alone may set `isSystem` (a JSON boolean, read from the raw body because the request models ignore the key) or change a system record. The four guarded services (`pipelineService`, `pipelineTemplateService`, `workflowService`, `workflowTriggerService`) run the guard after Tier-2 authorization and before any write, refuse with a `400` that names the field or action, and refuse a request that mixes an allowed change with a refused one as a whole. Because the exemption rests on who can invoke these functions directly, a new `lambda:InvokeFunction` grant on any of them, or on the import function, widens who can register or change system records.

`executeWorkflow` applies the same envelope rule to the trigger type. A `triggerType` other than `manual` (`fileUpload`) is accepted only on a `SYSTEM_USER` `lambdaCrossCall` (`_is_system_cross_call`), which the upload trigger dispatcher sends; any other request that names one is refused with `400`. A system trigger value is never a request value (System Pipeline Runs below).

### System Pipeline Runs (Server-Only Trigger and Context; No Terminal Callback)

A system feature launches its pipeline through `executeWorkflow` with the same envelope rule, in `common/workflows/systemPipelines.py` (no import-time AWS dependency; the core never imports, names or calls the feature):

-   **Server-only trigger and context.** `resolve_system_execution` reads only a `lambdaCrossCall` with `userName` `SYSTEM_USER` **and** `source` `vamsSystemPipeline`, plus the top-level `systemExecution` object (`{"triggerType": "<system trigger value>", "context": {...}}`). It returns the trigger value unchanged and the serialized context (a JSON object, at most `MAX_SYSTEM_EXECUTION_CONTEXT_BYTES` = 4096), raises `SystemExecutionError` (answered `400` with a fixed message before any read) for a malformed marked call, and returns `None` for every other event, whatever its body says.
-   **Generic trigger value contract.** The launching feature picks its own value; core keeps no list of them. `is_system_trigger` accepts `SYSTEM_TRIGGER_PREFIX` (`System-`) followed by ASCII alphanumeric words joined by single hyphens, at most `MAX_SYSTEM_TRIGGER_LENGTH` (64) characters (for example `System-MyFeature`), and the value is stored as given (request and stored spellings are the same). `models.executions.WorkflowExecutionRecord` accepts `Manual`, `File-Upload` or a value in that format; `EXECUTE_TRIGGER_TYPES` never includes one, so a public request naming a system value is refused by the request model. Anything that must tell system runs apart (re-run refusal, a list filter) keys on `is_system_trigger`, never on a named value.
-   **Where the context goes.** Pipeline 1's manifest (`systemExecutionContext`) and the main row (`systemExecutionContext`, serialized text), only when set, so every other run's manifest and row are unchanged. Never `systemConfig` / `effectiveSystemConfig`, never the Step Functions input (the workflow Lambdas log it), never a list or details projection, never a re-run.
-   **No terminal callback.** A feature that runs a workflow tracks it by storing the `workflowExecutionId` on its own record and reading the run status from the existing execution tracking (execution rows, read-time reconcile, the status-change rule) when its own record is read; it does not register a callback with the workflow core. Core workflow code never names or calls a specific pipeline. Clean up a feature's leftover artefacts with S3 lifecycle rules (e.g. abort incomplete multipart uploads) rather than a terminal hook. Every terminal writer (end state, error handler, abort, group abort, list and details reconcile, the status change push) writes a system run's main row exactly as it writes an ordinary run's, and none of them invokes another Lambda for it.
-   **Re-run refused.** `rerun_execution` answers `400` (`SYSTEM_RERUN_REFUSED_MESSAGE`) for a system trigger, after the visibility check so a caller who cannot see the run gets `403`.
-   **Status change push.** `executionService.lambda_handler` first checks `is_sfn_status_change_event` (top-level `source` `aws.states` + `detail-type` `Step Functions Execution Status Change`, which no API Gateway event carries) and hands it to `handle_sfn_status_change` **before** the request log line, since the event detail carries the execution's input and output. The handler ignores a non-terminal status, a `detail.name` that is not an execution id, a missing main row, and a row whose `workflow_execution_arn` differs from `detail.executionArn` (the `vams-` state machine prefix is shared by every deployment in the account and Region). Otherwise it runs `_reconcile_main_status(force=True)`: the details reconcile with the poll interval skipped, so the status comes from its own `DescribeExecution` under the shared terminal guard. It applies to every run (same write the next read would make; releases locks of a run stopped outside VAMS at once). The permanent-delete route set ignores the event. Unlike the read paths, the push passes `raise_on_failure=True`: a failed main-row read, `DescribeExecution` or guarded write raises so Lambda's asynchronous retries redeliver the event; a lost guard (`ConditionalCheckFailed`) never raises. Do not add a broad `try/except` around the push.
-   **Trust boundary.** `lambda:InvokeFunction` on `executeWorkflowV2` is the right to launch system runs (any holder can send the marked cross-call with any well-formed system trigger value). Holders today: `executionService` (re-run) and `workflowTriggerDispatch`, neither of which sends the marker, and two that do, each granted the exact function ARN: the vector-search `systemWorkflowLauncher` (`osVectorSearch`, its grant scoped to that one function), which launches the SYSTEM GenAI metadata workflow under `System-Reindex` with a context of its reindex run id and chunk and a concurrency slot request, and `zipDownloadService` (`handlers/assets/`, `assetFunctions.grantZipDownloadLaunch`, deployed only with `app.pipelines.useZipDownloads.enabled`; the same statement names the execution service and execution details functions for its abort and run check), which launches the zipArchive workflow under `System-ZipDownload` with a context naming the job and its request manifest and no slot request. Review any new invoke grant on it as a system-run grant.

Pinned by `tests/handlers/workflows/test_system_pipeline_hooks.py` (real guarded writes against moto for every writer and race, plus a source scan that fails when `common/workflows`, `handlers/workflows` or `infra/lib/lambdaBuilder/workflowFunctions.ts` names a feature-specific trigger or the removed callback registry).

### Workflow State Machine Deployment

A workflow's AWS Step Functions definition encodes each referenced pipeline's execution settings, so it must be rebuilt when any of them changes. The contract:

-   **One deploy routine.** Create, update, the pipeline-change refresh and the launch-time check all deploy through `workflowService._deploy_fields`, which calls `workflowAsl.deploy_state_machine` and returns the attributes written together: `workflow_arn`, `workflowVersionArn` (every deploy publishes a version; older ones past `MAX_RETAINED_STATE_MACHINE_VERSIONS` are deleted, but only those older than the version the record named when the deploy read it, so a burst of concurrent redeploys never prunes the version the winning write records), `jobNames` (reused while the steps are unchanged, so output prefixes do not move), `pipelineFingerprints` (`{"pipelineDatabaseId:pipelineId": sha256}` of `workflowAsl.to_asl_pipeline_dict` minus `name`) and `deploymentRevision`. The refresh writes conditionally on the revision it read.
-   **A builder change that alters every definition bumps `ASL_SCHEMA_VERSION`.** The workflow record stores the `aslSchemaVersion` (and `outputIngestionWaitHours`) its definition was generated with; `deployment_matches` requires `asl_schema_current`, so a workflow deployed under an older schema, or before the field existed, is redeployed at its next launch (and by any refresh). The fingerprint map stays per pipeline. Bump `workflowAsl.ASL_SCHEMA_VERSION` and `workflowAslBuilder.ASL_SCHEMA_VERSION` together (`test_process_output_retry_policy.py` checks they agree). Version 2: the wait for large outputs (#1123). Version 3: the process-output retrier (`workflowAslBuilder.PROCESS_OUTPUT_RETRY_ERRORS`) also lists `Sandbox.Timedout` (a Lambda timeout on current runtimes) and `EarlierIngestionInProgress` (#1290, #1271), next to the `LargeObjectCopyEngineBusy` and throttle retriers.
-   **Everything the ASL builders read from a pipeline record goes through `to_asl_pipeline_dict`.** That dict is what is fingerprinted; a setting read around it would change the definition without changing the fingerprint, and no workflow would be redeployed for it.
-   **A pipeline change refreshes its workflows.** `pipelineService.update_pipeline` compares fingerprints before and after the save and, on a difference, invokes `refreshPipelineReferences` asynchronously; the workflow service walks every non-archived referencing workflow to exhaustion. A failed redeploy sets `deploymentStale` + `deploymentStaleReason` on the workflow.
-   **The redeploy path reads strongly consistent.** `workflowService.redeploy_workflow` (behind both `refreshPipelineReferences` and `redeployWorkflow`) reads the workflow and each pipeline with `ConsistentRead=True` (the `consistent_read` parameter of `get_workflow_item`, `_get_pipeline_record` and `_resolve_snapshot_pipeline_records`). The refresh is invoked right after the pipeline write, and the re-read after a lost conditional write must see the other writer's deployment; an eventually consistent read can return the previous row in either place, so the walk finds every workflow "current" and redeploys nothing. API reads keep the default.
-   **A launch never runs a stale definition.** `executeWorkflow._ensure_current_deployment` compares the stored fingerprints with the live pipeline records (a record without a version or fingerprints never matches), invokes `redeployWorkflow` synchronously on a mismatch, and starts on `workflowVersionArn`; when the deployment cannot be made current it returns `409` and starts nothing. The main execution row records `workflowVersionArn`.
-   **Settings read at launch need no redeploy.** `systemConfig` (filters, arity, metadata gates, viewer suffix), templates, and enabled/archived state reach each run through the Step Functions input or the execute handler's own reads; keep it that way rather than baking them into the ASL.
-   **The scaffolding states wait out a refused resource-name lookup.** Process-output, the ingestion wait, finalize, the interim pipeline tracking and the error handler carry `workflowAslBuilder.RESOURCE_NAME_UNAVAILABLE_RETRIER` (`ResourceNameUnavailableError`, 15 s doubling to a 60 s cap, full jitter, 12 attempts), right after `THROTTLE_RETRIER` on the output states and ahead of the `States.ALL` retrier on the interim and error-handler states, since Step Functions uses the first matching retrier. It is safe because their handlers (`processWorkflowExecutionOutput`, `interimPipelineTracking`, `handleExecutionError`) resolve names at import (Rule 10), so their own lookups raise it only from init, before the handler runs; a name resolved inside a function in those handlers stays inside a `try` that catches it (`tests/common/workflows/test_resource_name_unavailable_retry.py`). Pipeline task states do not carry it: pipeline functions do not use `common.resourceNames`. A refusal a retry cannot fix, `ResourceNameAccessError` (for example `AccessDeniedException` when the role may not call `ssm:GetParametersByPath`), is deliberately not in its `ErrorEquals`: the output states send it to their Catch at once, and the interim and error-handler states retry it only through their own short `States.ALL` retrier.
-   **A refused synchronous callee ends the output step's attempt.** When the file ingestion function, the metadata service or the copy engine answers a `RequestResponse` invoke with an unhandled function error whose `errorType` is `ResourceNameUnavailableError` (`_raise_if_refused_at_start`; `EngineCallFailed.error_type` for the engine), the callee never ran its handler. `processWorkflowExecutionOutput` then releases that call's ingest-ledger claims (`_release_refused_call`: the version it listed of each object it stamped is deleted, since the stamped copy holds the content, then its `moving` rows are dropped) and raises `ResourceNameUnavailableError` for the state's retrier, instead of recording a failed write-back. The retried attempt records what earlier calls settled (`ingested`, `engine-copying`) and ingests the rest once (`tests/handlers/workflows/test_processOutput_refused_callee.py`). Any other callee error keeps the recorded write-back failure. Metadata writes are repeated with the same values on a retry.

### Large Object Copy Engine

Workflow outputs over 1 GiB (`common/largeObjectCopy.py` `LARGE_FILE_THRESHOLD_BYTES`, the one value `uploadFile.py` and the output step read) and API uploads over `API_UPLOAD_ENGINE_THRESHOLD_BYTES` (5 GiB; the engine values `MIN_PART_SIZE_BYTES`, `PARTS_PER_BATCH`, `THREADS_PER_BATCH`, `MAX_CONCURRENT_BATCHES` and `MAX_OUTPUT_INGESTION_WAIT_HOURS` are fixed constants in the same module, not deployment configuration or environment variables; `MAX_CONCURRENT_BATCHES` mirrors the construct's fixed Map `MaxConcurrency`, pinned by the infra test) are copied into their asset by the large object copy engine: the `LargeObjectCopy-<coreStackName>` Standard state machine (`infra/lib/lambdaBuilder/assetFunctions.ts`) running `handlers/assets/largeObjectCopy.py` (prepare / copyBatch / complete / fail / notify / reconcile). The batches run in child executions of `LargeObjectCopyParts-<coreStackName>` (`startExecution.sync:2`, `BATCHES_PER_CHUNK` = 50 per child, one child at a time) so no execution's history nears the 25,000-event quota; the arithmetic is in the construct and `infra/test/workflows/largeObjectCopyEngine.test.ts` recomputes it. Prepare returns `chunks` and still `batches` (an execution on the previous definition maps them inline). A refusal inside a child is recorded on the job row (`batchError`) so `fail` keeps the precise reason, and `fail` returns `refused`, which ends the execution in `LargeObjectCopyRefused` (Succeed) so the failed-copy alarm counts only breakage. A copy execution that ends outside its states (FAILED / TIMED_OUT / ABORTED other than the cancel path's `STOP_ERROR_CANCELLED` stop) is finalized by `reconcile`: a status change rule on the exact copy state machine ARN, and the execution details read (`_reconcile_ended_copies`, at most `MAX_RECONCILE_CHECKS_PER_READ` describes, async invoke) as the backstop, which a run claims at most once per `COPY_RECONCILE_CHECK_INTERVAL_SECONDS` with a conditional `lastCopyReconcileCheckDate` stamp on its main row; `notify` counts the job once: `count_job_terminal` sets the job's `counted` flag and adds 1 to the batch's `terminalCount` in one transaction, reads a cancelled transaction as already counted only when the job row's reason is `ConditionalCheckFailed`, tries one cancelled for `TransactionConflict` alone again (two jobs of a batch counted at once both update the batch row; `COUNT_TRANSACTION_ATTEMPTS`), and raises any other error for the notify task's retry. **The job status is monotonic:** every status writer conditions on the job being open (`OPEN_JOB_CONDITION`; `finalize_job` stamps `finalizedAt` once), because StopExecution does not stop a Lambda already running, so a Prepare or Complete of a stopped execution can outlive the reconcile. Prepare records its upload only while open (else aborts it and stops), Complete checks the job first and finalizes SUCCEEDED conditionally, `fail` keeps a stored outcome. One truth when the reconcile or the cancel path races Complete: a landed object wins. Both abort the multipart upload and then decide by the landed object WHATEVER the abort answered: S3 refuses a Complete that starts after an abort, but answers 204 (not NoSuchUpload) to an abort sent during or after a Complete that succeeds, and the object stays (#1123 D1; moto answers NoSuchUpload there, so tests use `_S3Abort204` in `tests/handlers/assets/test_largeObjectCopy_abort_204.py`). A Complete still in flight when a remover decided "not landed" supersedes the remover's FAILED / CANCELLED once with its own object (`loc.supersede_with_landed`: SUCCEEDED, `supersededStatus` keeps the overridden outcome, the rejection's `error` and `auditedAt` are removed so `_finish` writes the completion entry; the upload outcome is overwritten with `over_failed`). This is the one exception to the monotonic status. "Landed" is decided ONLY by the object this job's upload created, never by size or presence (an earlier object of the same size can sit at the key): Prepare records a fresh `destUploadToken` on the row in the same conditional write as the upload id and stamps it as object metadata (`s3MetadataKeys.VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY`, system-owned) on CreateMultipartUpload; Complete uses the VersionId CompleteMultipartUpload returned, and when it did not see that response (NoSuchUpload) it, the reconcile and the cancel path use `landed_object_version` (the key's current object carries the row's token; works on unversioned buckets; no token = not landed). The work after a terminal status (`_finish`) is safe to repeat: aborts and deletes are idempotent, the audit entry (`auditedAt`) and the asset type + email (`landedEffectsAt`) are claimed once on the job row, and the upload outcome (`uploadStatus.record_async_file_outcome`, monotonic PENDING -> one outcome, except `over_failed` for a superseded job) raises with `raise_errors=True` so a retry records it. The large-file queue consumer does the same with `finish_stored_file`: its audit entry is claimed on the file's upload-record entry (`claim_async_file_audit`), the outcome is written last and a failed write returns the message, and a redelivery that finds the file stored writes whatever is missing unless the engine may own the file (`copy_job_state`). Progress is the destination multipart upload (`ListParts` before each batch, so a retried task resumes); metadata, content headers and the `bucket-owner-full-control` ACL are set once on `CreateMultipartUpload`, so large outputs skip the provenance self-copy. State is `LargeObjectCopyJobsStorageTable` (job rows `{scope}#{hash}`, batch rows `batch#{executionId}`, index `WorkflowExecutionIdIndex`, TTL `expiresAt`). The output step (`processWorkflowExecutionOutput.py`) has three modes: `ingest` hands large files to jobs through `complete_external_upload` (`ingestionBatchId` = the execution id), defers file-level metadata targeting them, saves its state to the run bucket and leaves the row RUNNING with `executionPhase = INGESTING_OUTPUTS`; `await` (ASL v2 `AwaitOutputIngestion`, task token, timeout `MAX_OUTPUT_INGESTION_WAIT_HOURS`) registers the token; `finalize` records the outcome, naming each failed file. Exactly one caller reports a finished batch (`claim_notification`). A run on an ASL v1 definition is finalized by the engine (`legacy` batch) and the read-path reconcile holds it (`ingestion_hold`). Abort, group abort and the error handler call `cancel_execution_copy_jobs` (StopExecution + AbortMultipartUpload). **A stop leaves no part behind (#1123 D5):** StopExecution does not stop a running CopyBatch task, and S3 keeps a part whose UploadPartCopy was in flight during an abort, so every stopper (the cancel path, the reconcile of a console stop) first records `stopRequestedAt` on the open job (`loc.request_stop`, no status change), then aborts; each CopyBatch task reads the job before and after every part (`loc.copy_should_stop`), copies nothing further once stopped or finalized, and aborts again after its own last copy; `_finish` (FAILED / CANCELLED) and the reconcile of a CANCELLED job abort until ListParts answers NoSuchUpload (`loc.abort_until_gone`, at most `ABORT_SWEEP_ATTEMPTS`, waiting only while parts are still listed); never for a SUCCEEDED job. Tests use `_LateS3` (`tests/handlers/assets/test_largeObjectCopy_late_parts.py`), since moto removes the upload at once. **No immediate answer is final (#1123 D5, demo21 recheck):** S3 answered an abort, and ListParts right after it answered NoSuchUpload, yet the upload was listed again 2-8 minutes later with a part accepted before the stop. So the cancel path (every caller of `cancel_execution_copy_jobs`, the abort route included), `_finish` (FAILED / CANCELLED) and the reconcile of a CANCELLED job also start the job's **delayed sweep** (`loc.schedule_delayed_sweep`): an execution of the copy state machine itself whose input is `{"sweep": {"jobId"}}` (name `locsweep-<hash>`, same input from every scheduler, so one per job; `ExecutionAlreadyExists` counts as scheduled). Its sweep branch (`LargeObjectCopyCopyOrSweep` Choice -> Wait `SWEEP_FIRST_WAIT_SECONDS` 300 -> `sweep` -> Wait `SWEEP_SECOND_WAIT_SECONDS` 660 -> `sweep` final -> `LargeObjectCopySwept`) runs the engine's `sweep` action (`loc.sweep_recorded_upload`): reads the row, skips a SUCCEEDED job or an open one without `stopRequestedAt`, decides by the landed object first (raises when it cannot check, so the pass is retried), lists `ListMultipartUploads` with the exact key as prefix and aborts only the row's `uploadIdS3` when listed; never another writer's upload, never an object. The reconcile skips a sweep execution that ended unfinished. Tests: `tests/handlers/assets/test_largeObjectCopy_delayed_sweep.py` (`_GhostS3`). The job row carries `destRegion`, which the cancel path registers before its abort (`register_bucket_region`), and every stopper's coordination policy may query the asset buckets table's `bucketNameGSI` for a row without it (#1123 D6). The stoppers hold no s3:GetObject on the asset buckets, so a job whose upload is already gone and whose object they cannot read is left open, and the engine's reconcile of that stop (`_reconcile_cancelled_stop`: the cancel path's decision, plus the post-landing work for a landed job that the stopped Complete may never have done) decides it. That job is left open only when this call's StopExecution succeeded (`stopped`); with no stop made (no execution ARN, `ExecutionDoesNotExist`) no event comes, so it is written CANCELLED. **Every stop the cancel path left open, or finalized SUCCEEDED itself, is handed to the engine directly** (`request_reconcile`: async `{"action": "reconcile", "executionArn"}` invoke, best-effort, never fails the cancel) next to the stop's status change event, because every caller (abort, group abort, error handler, status change handler, output step of an ended run) acts on an ending run and the details read never checks an ended run's copies. Callers pass `lambda_client` + `loc.engine_function_name()` (env `LARGE_OBJECT_COPY_FUNCTION_NAME`). **The output step holds no copy grant (#1123, 2026-11-10):** it does its batch record, task-token registration, outcome read and cancel through the engine (`loc.call_engine`, a synchronous invoke of the engine's `outputStep` action, `largeObjectCopy.output_step`, each operation safe to repeat; a refused or failed call raises `EngineCallFailed`, so the step fails rather than report a run done; `AccessDeniedException` and an engine name not yet readable are retried for at most `ENGINE_CALL_DEPLOY_WAIT_SECONDS` while the deployment applies; a throttle (`TRANSIENT_ERROR_CODES` / 429, also as the engine's errorType; the set includes `TransactionConflictException`, which rejects a batch row write that met a job's count transaction and which botocore does not retry) is retried with jittered backoff within the invocation's remaining time minus `INVOCATION_SAFETY_MARGIN_MS`, then raises `LargeObjectCopyEngineBusy`, which the ASL retries on process-output, the wait and finalize (`ENGINE_BUSY_RETRIER`), so a throttle never fails a run; `start_copy_job` and `cancel_execution_copy_jobs` take `max_wait_seconds` and wait out throttles and the deploy window via `call_with_transient_retry`, #1123 H1/L1). The task token is still answered only by `claim_notification` once every job of the batch is counted terminal (`tests/handlers/workflows/test_processOutput_engine_routed_completion.py`; output-step tests route through the real engine with `tests/copyEngineRouting.py`). It resolves the engine name at import with `get_resource_name(loc.ENGINE_FUNCTION_RESOURCE_KEY)` (`lambdaFunctions/largeObjectCopyEngine` under the resource-name prefix), since an env reference would be a CloudFormation cycle with the engine: a refused lookup fails init with `ResourceNameUnavailableError`, which the workflow retries, and a name not published yet is `""` and is resolved again on the next engine call (`_copy_engine_function_name`). Because the resolver remembers that miss for `MISSING_KEY_TTL_SECONDS` (300 s), longer than `call_engine` waits (`ENGINE_CALL_DEPLOY_WAIT_SECONDS`, 120 s), a call-time `KeyError` reads the parameter itself with `common.ssmParameters.read_parameter_value()` (uncached, bounded): the name named by `LARGE_OBJECT_COPY_FUNCTION_NAME_PARAMETER`, which CDK sets on the output step (`loc.ENV_ENGINE_FUNCTION_NAME_PARAMETER`), else the prefix plus the key. The other callers take `LARGE_OBJECT_COPY_FUNCTION_NAME` (`loc.engine_function_name()`). The reconcile is idempotent, so the hand-over and a late event finish the job once (`tests/handlers/assets/test_largeObjectCopy_cancel_handoff.py`). `ended_copy_executions` also returns an open job of a cancelled stop (`is_cancelled_stop`) for a run still RUNNING; `LargeObjectCopyReconcileDLQAlarm` watches the reconcile DLQ (remedy: re-invoke the reconcile with the message's execution ARN; the queue has no source queue to redrive to). A completion whose files are all accepted asynchronously answers `overallSuccess` true (`accepted_asynchronously`), and only files copied synchronously update the asset type (`landed_synchronously`). `ASL_SCHEMA_VERSION` is 2; `deployment_matches` also requires the stored `aslSchemaVersion`, so a launch redeploys an older definition first. Audit (`common/auditActions.py`): the engine's entrypoint is `@audited_handler` and it records `fileUpload` (system action, `log_file_upload`) when a copy lands; the output step records `workflowOutputIngest` and `workflowExecutionEnd` once per run, from the finalize mode for a run whose large outputs were handed to copy jobs (never at the hand-off), for the entrypoint's own event; `GET /uploads/{uploadId}` records `uploadStatusGet`.

### Workflow Execution Locks

A workflow's `perAsset`, `perInputFile` and `perInputFileVersion` concurrency restrictions are lock rows in `WorkflowExecutionLocksStorageTable` (PK `lockKey`, TTL `expiresAt`, holder index `WorkflowExecutionLocksByHolderGSI` on `workflowExecutionId`), one row per distinct asset, file or file version a launch selects. `common/workflows/executionLocks.py` holds the contract:

-   **Acquire.** `executeWorkflow` alone takes locks (`acquire_locks`): conditional `PutItem`s in sorted key order, all or none, immediately before the launch's first side effect. A conflict answers `400` with the scope's fixed message; the contested key and its holder go to the service log only. A conflicting row whose holder has no main execution row and whose `acquiredAt` is older than the function's `EXECUTE_WORKFLOW_TIMEOUT_SECONDS` plus `LOCK_TAKEOVER_MARGIN_SECONDS` is taken over with a put conditional on that same holder.
-   **Release.** The end-state step, the error handler, the abort route and the executions read path (for a run stopped outside VAMS) call `release_locks_for_execution`, which deletes the union of the keys rebuilt from the execution's input rows and the rows the holder index lists, each delete conditional on the holder, and never raises. A launch that fails after acquiring releases from memory with `release_locks`.
-   **Release, then announce.** Every terminal writer releases before it publishes `workflow.execution.completed` (the end-state step's `record_execution_outputs` emits after its `finally` release, the error handler and the abort after theirs, the read-path reconcilers after `_settle_reconciled_terminal`), so a consumer that relaunches on the event (the compliance callback's waiting pipeline rules) is not refused again by the run that ended. An abort whose stop is unconfirmed announces while the run keeps its locks; a rule refused then waits for the next completion over its asset or its wait bound.
-   **Only a run that has stopped releases.** The abort and a failed launch release only when `StopExecution` succeeded or `sfn_execution_has_ended` shows the execution ended; otherwise the rows wait for a terminal step or the TTL (the sum of the pipelines' timeouts, at least a day, plus 30 minutes).
-   **Concurrency slots.** A marked system-pipeline launch (see System Pipeline Runs) may carry `systemExecution.concurrencySlots` `{"count": N, "candidates": [index, ...]}` (`systemPipelines.resolve_concurrency_slots`: `count` an integer from 1 to `MAX_CONCURRENCY_SLOTS` (1000), `candidates` at most `MAX_CONCURRENCY_SLOT_CANDIDATES` (16) distinct indexes below `count`; a malformed field is a `400` like any malformed marked call, refused before any read; any other event, the public request and an unmarked `SYSTEM_USER` call included, asks for none). Such a launch also claims one of the workflow's N slot rows, `{workflowDatabaseId}:{workflowId}|systemSlot|{n}` (`acquire_system_slot`, the candidates in order, or the first 16 slots with none), after its locks and before the first side effect; with every candidate held it releases those locks and answers `429` `SYSTEM_SLOTS_BUSY_MESSAGE` with no audit record of any group (`skip_route_outcome`, not `general_error`), which its launcher treats as a wait. The slot is released with the locks: from memory on a failed launch, and by every release site through the holder index, whose release is by key, so a row of an earlier key shape (the `{workflow}|reindexSlot|{n}` rows #316's earlier builds wrote) is freed the same way when its run ends. A slot row expires after `system_slot_ttl_seconds` (the pipelines' timeouts, plus the output-ingestion wait the definition allows after them, `MAX_OUTPUT_INGESTION_WAIT_HOURS` (48 hours, so a run waiting for outputs handed to the large object copy engine keeps its slot), plus 30 minutes, no day floor); a held one older than the takeover age is taken over when `_holder_still_running` finds no record, an ended record, or an ended state machine. Core keeps no list of the launches that ask for slots; the vector-search reindex launcher is one (`osVectorSearch/systemWorkflowLauncher.py`, `app.vectorSearch.indexingConcurrency` slots, which also reads the earlier rows when it picks candidates so the cap holds across an upgrade).
-   **Grants** sit in each lock handler's own `ExecutionLockPolicy` (`attachExecutionLockPolicy` in `infra/lib/lambdaBuilder/workflowFunctions.ts`), never its role's default policy, the execution service's included (owner decision 2026-11-14; a separate role-inline `AWS::IAM::Policy` is never split into CDK's overflow policies, which `workflowExecutionLockGrants.test.ts` pins in every shipped template, the customer-managed-key cell and the worst-case cell, and `workflowLambdaGrants.test.ts` pins per function).
-   **Skip records.** A conflict on a `fileUpload` trigger launch (`triggerType` File-Upload, executing user `SYSTEM_USER`) also writes a `SKIPPED` execution (`executeWorkflow._record_skipped_launch`), best-effort and with the same `400`; a manual or API launch writes nothing. The id is `er.skip_record_execution_id(lockKey, holder)` (uuid5), so every refusal by one holder on one key lands on one record. Rows are written in this order, so the main row (which lists and details read) exists only once its authorization rows do: (1) the configuration row (`build_workflow_configuration_record` with the trigger's output target and `pipelineExecutionParameters`, the trigger's template parameters a re-run replays), a put conditional on `attribute_not_exists`, whose existing item supplies the first-skip date to later refusals; (2) the workflow-input rows (`er.build_workflow_execution_input_record`, never pinned, dated with that first-skip date) while the main row's `skippedLaunchCount` is below `MAX_SKIP_RECORD_INPUT_ROWS` (100); (3) the main row, `er.build_skip_record_update`: one `UpdateItem` that sets the identity fields `if_not_exists`, overwrites `lastSkippedDate` and `ADD`s one to `skippedLaunchCount`. A failure in (1) or (2) writes no main row. No pipeline or lock row is written, and a conflict with no known holder records nothing. `SKIPPED` is in both `TERMINAL_STATUSES` tuples, so no reconcile or terminal writer rewrites it. Reads: the lists add `er.skip_list_fields` (count, scope, reason; never the holder) to SKIPPED rows only; the details view adds `skip` (`skip_details_block`), whose `blockingExecutionId` is set only when `_caller_may_read_execution` passes the same Tier-2 GET check on the holder (missing and denied look the same); the logs route answers `SKIPPED_EXECUTION_LOG_MESSAGE` without reading CloudWatch; a single abort answers `400` `SKIPPED_EXECUTION_ABORT_MESSAGE` after authorization and writes nothing, and a group abort reports the member as `skipped-terminal`; re-run uses the configuration row's `pipelineExecutionParameters` when the record has no pipeline rows. Both list routes take `excludeStatus` (`models.executions.parse_list_status_filter`: known statuses only, no conflict with `status`, fixed 400s): the global list adds it to the FilterExpression (no signal beyond the `status` filter); the asset list drops a candidate only after it has passed the per-execution read check (`_authorize_candidate`, memoized and shared with the authorization pass), so a candidate the caller may not read is handled exactly as without the parameter; at most `MAX_EXECUTIONS_INSPECTED` drops per page (a spent budget stops with a cursor and a warning), re-applied after reconcile. The execute role's main-table `GetItem`/`UpdateItem` and configuration/input `PutItem` cover the writer (`test_execute_workflow_dynamodb_calls.py`).

### System Workflow Runs in the Execution Lists

`executeWorkflow._persist_execution_records` copies the workflow row's stored `isSystem` onto the execution main row (`build_workflow_execution_record(is_system=)`; only a boolean `True` marks a run), and `_record_skipped_launch` onto a SKIPPED record (`build_skip_record_update(is_system=)`, set by the first skip). Both list routes in `executionService` hide those rows unless `includeSystem` applies (`INCLUDE_SYSTEM_PARAM`, the name `vamscli.constants.EXECUTION_LIST_INCLUDE_SYSTEM_PARAM` mirrors and a CLI contract test pins):

-   **Default.** `_resolve_include_system`: an explicit `true`/`false` wins (anything else is `400`); otherwise hidden, except a listing scoped to ONE workflow (both halves of its key) that is a system workflow the caller may GET, narrowed to a system trigger value (`sp.is_system_trigger`, by format, never a named value), or narrowed to one execution group (`groupId`). The definition read is the per-enforcer memo the row authorization uses. The applied value is echoed as `includeSystem`.
-   **Fail-visible.** `er.execution_row_is_system` is the one reader: a missing attribute (rows written before #1121, no migration) or any non-boolean reads as a user run. The global list's `FilterExpression` term is `attribute_not_exists(isSystem) OR isSystem <> true` for the same reason, and `_global_list_matches_filters` (`excludeSystem`) is the safety net.
-   **Paging.** A hidden row is a fully evaluated row: on the global list it advances the cursor like any filtered row, and on the asset list it is a post-filter after `build_execution_items`, so short or empty pages with a `NextToken` are possible there, as with the other filters. Token format unchanged.
-   **Not authorization.** It only chooses which rows a page includes; every row still passes `_execution_access_check`. Details, logs, abort, group abort and re-run do not read it.

### Download as Zip (Prepared Downloads)

`handlers/assets/zipDownloadService.py` serves `ZIP_DOWNLOAD_ROUTES` (create, per-asset, all-users and global list, get, cancel/delete, presigned URL), and in the same function the delayed sweep's passes. `lambda_handler` dispatches on the event envelope, never the body: an event with `requestContext` (every API Gateway event has one, and API Gateway cannot add top-level keys) always takes the API path (`handle_api_request`, Tier 1 then Tier 2); without one, a top-level `action` `sweep` goes to `handle_sweep`; any other shape (a terminal callback payload included) is logged and ignored with no read or write (`tests/handlers/zipDownloads/test_zipDownloadService_event_dispatch.py`). Only the sweep state machine invokes it directly. **No terminal callback:** the zip download service is not registered in the system-pipeline callback registry, and a run sends its job nothing when it ends. The job follows its run by the `workflowExecutionId` stored at launch, and the read-time reconcile (`_reconcile`, run by get, both lists, the URL route and the cap check) finalizes it from the run's main execution row on the next read (`tests/handlers/zipDownloads/test_zipDownloadService_run_end_reconcile.py`). An incomplete upload no reader or sweep aborted is removed by the `vams.preparedDownloads/` lifecycle rule (`AbortIncompleteMultipartUpload` after one day). The routes and `ZipDownloadRequestsStorageTable` deploy always; with `ZIPDOWNLOADS_ENABLED` false every route answers 404 `Zip downloads are not enabled for this deployment`, checked after Tier 1. The rules:

-   **Ownership.** Jobs are keyed `userId` (the authenticated caller, never a request value) + `requestId` (`common/zipDownloads.new_request_id`, time-sortable); `UserAssetIndex` (`userAssetKey` = `userId#databaseId#assetId`) serves the caller's asset list. On get, cancel and delete another user's job, or one named under another asset's path, answers the same 404 as a missing one (the URL route's shared view is below). The handler writes no record for such a miss: the request outcome records the 404 as the route's action, failed, `notFound`. Every route also re-checks Tier-2 asset `GET`, and an asset the caller may not read answers exactly as a missing one (#1125 D29): create and both asset lists give the same 404 `Asset not found` (`_asset_not_found`; only the record tells them apart: an existing asset is a denial of the route's action, a missing one the request outcome's failed `notFound`), and the job routes the job-level 404; a `startingToken` must name the caller's own partition.
-   **Shared view (the Download Queue's "Show all users").** `GET /database/{databaseId}/assets/{assetId}/download/zip-all-users` (`API_ZIP_DOWNLOADS_ASSET_ALL_USERS`, a separate path so a role can be granted or denied it) lists every user's unexpired jobs for one asset through `AssetIndex` (`assetKey` = `databaseId#assetId`, written on each job from creation; a row without it is listed only to its requester), one bounded page per call with a `startingToken` that must name the same asset. Tier 1 on the route, then Tier 2 asset `GET`; a caller who may not read the asset, or a missing asset, gets the same 404 `Asset not found`, recorded as for the asset list. Other users' rows (`zd.to_shared_job_item`) carry `requestedBy`, status, dates, size and the selection summary (no preview, no keys, no run id), plus the API's advisory flags for the caller, which every list returns from one definition, `_row_flags` (#1125 D36, D37; the per-user and per-asset lists carry them on the caller's own rows through `zd.to_own_job_item`): `canDownload` (SUCCEEDED, Tier 1 on the row's URL route, for another user's job Tier 1 on the asset's file download route `POST /database/{databaseId}/assets/{assetId}/download`, and not refused by the compliance quarantine block: `_quarantine_refuses_download` runs the create and URL routes' `check_quarantine_block` once per distinct asset per call, only for a row that could otherwise be downloaded, never per row; an unreadable state leaves the flag to the URL route) and `canRemove` (the requester only, Tier 1 on the row's DELETE route). The flags use `_route_allowed` (the enforcer's service object, no audit: advisory); every real call is still checked. `tests/handlers/zipDownloads/test_zip_own_row_flags.py`. The URL route signs another user's archive (`_shared_row`: one `AssetIndex` query on `assetKey` + `requestId`, then a consistent read of the row) only after Tier 2 asset `GET` and Tier 1 on the file download route (the file download path's own checks). Every archive, the requester's own included, then passes `require_distributable_asset` (with the quarantine block, so a quarantined asset answers its 400 before any member is read), the reconcile's 409 and `_recheck_members`: every member in the job's `resolved-manifest.jsonl` must still be present, not archived (delete marker, `vams-status`, archive storage class) and not blocklisted, head-checked on the head-check pool up to `HEAD_CHECK_MAX_FILES`, otherwise from `ListObjectsV2` listings (current objects only, no delimiter) of the sorted member keys in runs of `SHARED_RECHECK_LIST_RUN` (1,000), each under its run's common prefix, starting just below its first member and, when a page ends short of the next unconfirmed member, restarting just below that member instead of continuing (so the unselected files between members cost at most one page per stretch), also on the pool (`_member_run_ok`). The call count follows the member count, not the folder count or the version history; a delete marker leaves a member unlisted, and a listed key that sorts past an unconfirmed member refuses; the listing confirms presence, storage class and extension, while `vams-status` and the content type are read only by a head check. Never a whole listing of the asset root, and never its versions: each run starts just below its first member and stops at its last. A member that fails, or an unreadable member record, refuses with 400 `zd.MSG_ARCHIVE_FILE_UNAVAILABLE` (its own sentence: the archive is built, so it names no selection; create keeps `zd.MSG_ARCHIVED_IN_SELECTION`) (recorded as `zipDownloadUrl` failed, `validation`). Both checks run within `ZIP_DOWNLOAD_SHARED_RECHECK_MAX_SECONDS` (15 s): when it runs out (`MemberCheckIncomplete`) nothing is signed and the route answers 503 `zd.MSG_ARCHIVE_CHECK_INCOMPLETE` ("try again"), never the archived-or-deleted refusal (recorded as `zipDownloadUrl` failed, `internal`). `tests/handlers/zipDownloads/test_zip_large_archive_member_check.py`. A refused permission answers the same 404 and is recorded as a `zipDownloadUrl` denial. A URL issued for another user's archive is recorded as `zipDownloadUrl` with `shared`, `requestedBy` and `downloadedBy` in `details`. Get, cancel and delete stay requester-only (`_owned_row`). `tests/handlers/zipDownloads/test_zip_shared_view.py`, `test_zip_requester_member_recheck.py`.
-   **Audit records.** `lambda_handler` carries `@audited_handler`, and every route records its registered action (`common/auditActions.py`, entity `export`) with `log_action` on success: `zipDownloadList` / `zipDownloadListAllUsers` / `zipDownloadGet` (reads: `requestParameters` page bounds, never the token; `itemCount`), `zipDownloadCreate` (`changes`: the job fields written; `fileCount`, `bytes`, `executionId`), `zipDownloadUrl` (a download, in the file download group: `bytes` = the archive size), `zipDownloadDelete` (`previous` status) and the DELETE route's secondary `zipDownloadCancel` (`changes` / `previous` status, or `details.result` `ended` / `alreadyCancelled`). `_reconcile` records `zipDownloadReconcile` (secondary) for the request's own event (`current_audit_context()`), only when its finalize write took effect, and a delayed sweep pass that acted records the system action `zipDownloadSweep` with `on_behalf_of` the requester. Error responses record nothing in the handler; the request outcome records them. A data-level denial (`_denied`) is recorded under the route's own action, `denied`/`forbidden` (a denied DELETE as `zipDownloadDelete` with `details.intent`), and its 404 is built without the event, so it is the request's one outcome: no failed record from the decorator and none in the errors group (#1125 D34; `test_zip_denial_one_outcome.py`). `tests/handlers/zipDownloads/test_zip_audit_records.py`.
-   **Refuse the whole job.** Create refuses an asset whose location names one object (a single-file asset, `common/assetLocations.is_folder_location`), before any resolution. It refuses on any archived (`vams-status`, GLACIER, DEEP_ARCHIVE), delete-marker or blocklisted file in the resolved selection, through `common/downloadEligibility.py`. Folder and whole-asset entries are pre-scanned within a budget (`ZIP_DOWNLOAD_PRESCAN_MAX_OBJECTS` / `_SECONDS`); the zipArchive container checks every member again before writing, so run time enforces what the budget left. An archived file (its current version is a delete marker, which S3 answers `404` with `x-amz-delete-marker: true`, or `vams-status` archived) is refused with `zd.MSG_ARCHIVED_IN_SELECTION`, the web dialog's words, never `File not found` and never the caller's key. A refusal answers its message without the "VAMS General Error: " prefix (`_refusal_message`, reading `VAMSGeneralErrorResponse.user_message`), the sentence the zip pages quote, with its own status (#1638).
-   **Version pinning.** Per-file `versionId`, or one `assetVersionId` / `assetVersionIdAlias` resolved through `handlers/assets/assetVersions.py` into the manifest's `pinnedFiles`. The resolvers never echo the requested id or alias (#1134).
-   **Preview companions are left out (#1622).** `{file}.previewFile.{ext}` keys (`common/s3PathPatterns.PREVIEW_FILE_PATTERN`), which the file list hides and the export folds, are skipped by the create's pre-scan, explicit-file and asset-version resolution (an explicitly selected one is dropped, not refused; a selection left empty answers `zd.MSG_NO_FILES`), and the request manifest records `zd.INCLUDE_PREVIEW_FILES_KEY` (`includePreviewFiles`) as `zd.ZIP_DOWNLOAD_INCLUDE_PREVIEW_FILES` (`false`), which the container applies.
-   **Launch.** A `SYSTEM_USER` cross-call to the execute function carrying the system-pipeline marker and `systemExecution` (trigger `zd.ZIP_SYSTEM_TRIGGER` `System-ZipDownload`, the zip's own value, which core checks for format only and stores as given; nothing in the zip feature reads it back, so runs recorded under the earlier `Zip-Download` still reconcile by their stored `workflowExecutionId`; context `{v, requestId, userId, databaseId, assetId, manifestKey}`); the request manifest at `vams.preparedDownloads/<db>/<asset>/<userHash>/<requestId>/manifest.json` (auxiliary bucket) is the container's authority. The execute handler answers `{"message": {"executionId": ...}}` (the envelope the re-run also reads), so `_launch` reads the id from `message`. Every cross-call here (launch, abort, details read) is a synchronous invoke through `lambda_client` built on `invoke_config` (no botocore retry, 910 s read timeout above the invoked functions' 900 s timeout) and `_invoke_request_response`, which retries only a `TooManyRequestsException`: a repeated launch would start a second run the job row never records (#1205 rule; `tests/handlers/zipDownloads/test_zip_cross_call_client.py`). Do not give this module a retrying Lambda client. The zipArchive workflow is `GLOBAL/utility-zip-archive`, so every cross-call is tested end to end for GLOBAL with the real receiving handler, never a hand-built answer: launch, abort and details read, each followed by the real zip download service's read-time reconcile (`tests/handlers/zipDownloads/test_zip_global_cross_calls_end_to_end.py`), and a mode 2 archive through the output step's write-back into the real `uploadFile` (`tests/handlers/workflows/test_zip_archive_write_back_end_to_end.py`). Those tests read the zipArchive pipeline and workflow records from the SHIPPED bundle through the real importer and create models (`tests/handlers/zipDownloads/zipBundle.registered_records`), never a hand-written systemConfig; `test_zip_bundle_launch_matrix.py` launches every selection shape through the real execute handler (#1125 D9: a bundle workflow without `systemConfig` got the defaults and refused every folder, whole-asset and multi-file job). Not every 4xx from the execute handler means no run exists: `lambda_handler` also maps exceptions raised AFTER `start_execution` (a throttle on the record writes, the item-size `ValidationException`, a pydantic or `VAMSGeneralErrorResponse` error) to 4xx. `_classify_launch_answer` therefore reads "no run exists" only from answers the handler gives before the start: 403, 404, the fixed early-return texts in `PRE_START_REFUSAL_MESSAGES` (workflow disabled/archived/undeployed, lock conflict, ...), the `executionValidationErrors`/`templateResolutionErrors` envelopes (`ZipLaunchRefused`: create removes the job, row and working objects, and answers `400` `zd.MSG_LAUNCH_REFUSED`, the refusal kept for the service log only), and 409 stale deployment or `ExecutionLimitExceeded:` (`ZipLaunchRetryable`: retried ONCE, then the job is removed and create answers `503` `zd.MSG_LAUNCH_BUSY`). Every other answer (5xx, timeout, any other 4xx) is `ZipLaunchMayHaveStarted`: never retried, the row stays `FAILED` `MSG_LAUNCH_FAILED`, and create answers 500, or 503 `MSG_LAUNCH_BUSY` for a `ThrottlingException:`. The lists are pinned to `executeWorkflow.py` by `tests/handlers/zipDownloads/test_zip_launch_answers.py`: reword an early-return refusal there and update the list in the same change.
-   **Status.** `QUEUED` -> `RUNNING` -> `SUCCEEDED` / `FAILED` / `CANCELLED`; every terminal write is `common/zipDownloads.finalize_row` (conditional on a non-terminal status, and on the run id where known). The container writes progress and its own refusal reasons (`ZIPDOWNLOAD:`-prefixed). The read-time reconcile finalizes the rest with `finalize_ended_run` on the job's next read (a run that SUCCEEDED without the archive -> FAILED `The archive was not produced`; FAILED / TIMED_OUT the container did not report -> FAILED with its documented message); a run aborted from the executions views ends FAILED `Cancelled from workflow executions`. Raw execution errors are never shown. **Neither the cancel nor the reconcile waits on a status event.** The user's cancel records CANCELLED itself before it aborts the run. It first records the cancel (`zd.request_cancel`: `cancelRequestedAt`, `cancelRequestedBy` and the stop marker, before anything stops the run); from then on no writer records FAILED (`finalize_row(unless_cancel_requested=True)` in `finalize_ended_run` and the launch-failed reconcile, and the container's `StatusWriter.failed`, condition on the marker being absent) and `decide_finalization` records CANCELLED by the user for a job with the marker and no landed archive, so a reconcile of the run the cancel's own stop ended still ends the job CANCELLED (#1125 D22). A cancel never deletes a job: one that finished before the cancel was recorded keeps its outcome and the DELETE answers `action: ended` with its `status`; only a DELETE of an already finished job deletes it (`tests/handlers/zipDownloads/test_zip_cancel_reconcile_race.py`). The DELETE takes an additive `intent=cancel` (`DeleteZipDownloadQueryModel`): with it a job that is already terminal at read is never deleted (`ended`, or `cancelled` for a CANCELLED job), so a cancel confirmed after the job finished (an open dialog, the CLI's check then DELETE) keeps it; web, CLI and MCP cancels always send it, and without it the route is unchanged (#1125 D23). The container never finalizes a job on SIGTERM: `StatusWriter.stopped` records `stopReason` only, and the read-time reconcile decides from the run's status (ABORTED -> `Cancelled from workflow executions` with `cancelledBy` execution, TIMED_OUT -> timed out, FAILED -> the recorded stop). **A stop outside VAMS ends the run (#1125 D26):** on a SIGTERM no VAMS stopper recorded (no `stopRequestedAt`/`cancelRequestedAt`, job still open: a Batch terminate outside VAMS) the container answers its own task token with `ZipArchiveContainerStopped` after recording `stopReason`, so the run ends FAILED and the reconcile shows `zd.MSG_JOB_STOPPED`; it also heartbeats that token every 2 minutes and the `ZipArchiveBatchJob` state has a 30-minute heartbeat timeout, so a container killed without a SIGTERM ends the wait within 30 minutes. Step Functions records a heartbeat expiry as `States.Timeout`, the same error as the wait's 11-hour bound, so the zipArchive `pipelineEnd` looks the run's job up and names the end: `ZipArchiveHeartbeatLost` (ran inside the bound; mapped to `zd.MSG_JOB_LOST` `The archive job stopped responding`), `ZipArchiveJobQueued` (still queued) or `ZipArchiveJobNotStarted` (failed before start), or `States.Timeout` kept at the bound (`zd.MSG_JOB_TIMED_OUT`); it ends a job still queued or running. The three names are read before the timeout and the recorded stop; the queued and not-started ones map to `zd.MSG_JOB_QUEUED` / `zd.MSG_JOB_NOT_STARTED` (matched by the fixed error names only, never showing the raw error). With a VAMS stop recorded nothing is sent, as before, whichever acts first; `finalize_ended_run` removes the working objects whenever the job ended without its archive, also when another writer's final write won (#1125 D24, `tests/handlers/zipDownloads/test_zip_cancel_intent_and_abort_reason.py`). The read-time reconcile decides from the run's main row. A run that ended outside VAMS reaches that row only through its Step Functions status change event or the execution service's details read, so when a job's row has been idle `RUN_CHECK_IDLE_SECONDS` and the main row is not terminal, the reconcile claims `runCheckedAt` (once per `RUN_CHECK_INTERVAL_SECONDS` per job, at most `RUN_CHECKS_PER_CALL` per list) and makes that details read as SYSTEM_USER (`zipDownloadService._check_run_ended`, the abort's grant), then decides from the re-read row. That is the backstop for a lost event: without it the job would hold a cap slot until it expires. An abort from the executions views writes `ABORTED` on the main row, which the next read of the job finds; a create at the per-user cap reconciles the caller's active jobs before it refuses (`_cap_reached`, same run-check budget), so a job whose run ended unseen frees its slot on the next create. **The status is monotonic and a landed archive wins with one outcome**, the rule the large object copy engine follows: StopExecution and a Batch terminate do not stop a container still writing, so `finalize_ended_run` (the read-time reconcile) and `cancel_job` (the user's cancel) abort the job's open multipart uploads BEFORE they decide (S3 then refuses a CompleteMultipartUpload the container sends after the abort) and record SUCCEEDED whenever the key's current object is the one THIS job's upload created, whatever the run's status and **whatever the abort answered**: S3 answers 204, not NoSuchUpload, to an abort sent during or after a Complete that succeeds, and the object stays (#1123 D1). A Complete already in flight can still land after a "not landed" decision; the container then loses its SUCCEEDED write and deletes that archive, and if it was killed during the Complete the archive is never served (only a SUCCEEDED job gets a URL) and goes with the job's delete or the retention rule. `tests/handlers/zipDownloads/test_zip_abort_204.py` drives every finalizer through its real handler with an S3 double that answers 204. "Landed" is identity, never presence or size (an earlier same-size object can sit at the key): the container records a fresh `archiveUploadToken` on the row in its RUNNING write, before the upload exists, and stamps it as object metadata (`ARCHIVE_UPLOAD_TOKEN_METADATA`, the same `common/s3MetadataKeys.VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY` the large object copy engine stamps its `destUploadToken` under) on CreateMultipartUpload; `landed_archive` compares the two with the helper the engine's `landed_object_version` also uses (`s3MetadataKeys.is_own_upload_object`; `tests/handlers/assets/test_upload_token_shared.py`), and the finalizers re-read the row after the abort. A row without a token has no landed archive. A cancel that finds the archive landed records SUCCEEDED and keeps the job (`action: ended`). The container never writes FAILED over a completed upload and deletes an archive that landed after the job was finalized without it (`backendPipelines/CLAUDE.md`, archive). `backendPipelines/utility/zipArchive/container/tests/test_terminal_races.py` drives the real container and these finalizers through every ordering. **No part is left behind (#1125 D16, the #1123 D5 rule):** S3 keeps a part whose UploadPart was in flight during an abort, so every stopper first records `stopRequestedAt` on the open job (`zd.request_stop`, no status change; the container's sink reads the job before each part and uploads nothing further), then `abort_open_uploads` aborts each upload until ListParts answers NoSuchUpload (`zd.abort_until_gone`, at most `ABORT_SWEEP_ATTEMPTS`, waiting only while it is still listed; the zip download service's role holds `s3:ListMultipartUploadParts` on `vams.preparedDownloads/*`). `tests/handlers/zipDownloads/test_zip_late_parts.py`. **No immediate answer is final (#1125 D16 round 2, the #1123 D5 demo21 recheck):** an upload whose abort answered, and whose ListParts then answered NoSuchUpload, can be listed again minutes later. So `_abort_then_reread` (cancel_job, finalize_ended_run) and `delete_job` (before the row is removed) also start the job's **delayed sweep** (`zd.schedule_delayed_sweep`, a no-op unless `ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN` is set, which happens only with the feature on; a job stopped without it logs a warning): an execution of `ZipDownloadSweep-<coreStackName>` (`assetFunctions.buildZipDownloadSweep`). It is named `zipsweep-<hash(userId#requestId)>`, so there is one per job and `ExecutionAlreadyExists` counts as scheduled. Its input `{"zipSweep": zd.sweep_request(row)}` carries the identity plus a snapshot of `archiveKey` and the recorded upload ids for a row deleted before the passes run. It runs Wait 300 -> service `{"action": "sweep", "final": false}` -> Wait 600 -> final. Each pass (`zd.sweep_recorded_uploads`) reads the row when it exists: it skips SUCCEEDED and running jobs without `stopRequestedAt`, checks `landed_archive` first, then lists the uploads with the exact archive key as prefix and aborts only the ids in `archiveUploadIds`, which the container records right after CreateMultipartUpload (`StatusWriter.record_upload`). Without the row it uses the snapshot, confined to the job's prefix. Tests: `tests/handlers/zipDownloads/test_zip_delayed_sweep.py` (`GhostS3`). **Auxiliary bucket Region (#1125 D17):** the service registers the auxiliary bucket (the stack's own) for the deployment Region at load (`register_bucket_region`), so the region-routing client never queries `bucketNameGSI` for it on the reconcile and sweep paths.
-   **Cleanup.** Cancel aborts the run through the execution service's abort (cross-call) and the open multipart uploads; delete removes every version under the job prefix and the row. A job that ends without an archive (cancel, failure, refused or failed launch, an abort from the executions views) has its working objects (`manifest.json`, `resolved-manifest.jsonl`, every version) deleted at once by `zd.delete_working_objects`, from `cancel_job` and `finalize_ended_run`; a SUCCEEDED job keeps them until delete or retention, and a resolved manifest the container writes after the job ended stays (never served) until delete or retention. An incomplete upload left by a writer that finalized the job itself (the container's own FAILED) is aborted by the lifecycle rule after a day; a read of a finalized job writes nothing. `common/zipDownloads` refuses any cleanup prefix that is not exactly one job's. Retention is the table TTL plus the `vams.preparedDownloads/` lifecycle rule, and reads hide rows past `expiresAt`.
-   **No reserved database id.** Other auxiliary objects live under `{databaseId}/`; the prefix's first segment `vams.preparedDownloads` has a dot, which a database id (`common/validators.id_pattern`, `^[-_a-zA-Z0-9]{3,63}$`) never has, so the lifecycle rule can never match a database's data and no id is reserved (a database named `preparedDownloads` is ordinary; `tests/handlers/databases/test_databaseReservedKeyword.py`). Keep any new top-level auxiliary prefix dotted for the same reason.
-   **Never a file of an asset.** The archives stay in the auxiliary bucket, so no asset-bucket event, trigger or index sees them. The vector indexer also drops a `vector.embedding.ready` document location under `AUXILIARY_PREPARED_DOWNLOADS_PREFIX` unread and ignores an S3 record of the auxiliary bucket under it (a deployment that also registered that bucket as an asset bucket); the reindexer's bucket walk resolves such a key only to an asset whose id is `vams.preparedDownloads` (`tests/handlers/osVectorSearch/test_vectorIndexer_prepared_downloads.py`).

Retention is the fixed `common/zipDownloads.ZIP_DOWNLOAD_RETENTION_DAYS` (7; CDK mirror `ZIP_DOWNLOAD_RETENTION_DAYS` in `storageBuilder-nestedStack.ts`, which the `vams.preparedDownloads/` lifecycle rule uses). Environment: `ZIPDOWNLOADS_ENABLED`, `ZIP_DOWNLOAD_MAX_FILES_PER_REQUEST`, `ZIP_DOWNLOAD_MAX_TOTAL_BYTES_PER_REQUEST`, `ZIP_DOWNLOAD_MAX_ACTIVE_PER_USER`, `ZIP_DOWNLOAD_SHARED_RECHECK_MAX_SECONDS` (optional), `ZIP_ARCHIVE_WORKFLOW_DATABASE_ID`, `ZIP_ARCHIVE_WORKFLOW_ID`, `EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME`, `EXECUTION_SERVICE_FUNCTION_NAME`, `PRESIGNED_URL_TIMEOUT_SECONDS`; the zip table via `ResourceKeys.ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE`.

### Workflow Output Step Retries: the Ingest Ledger

The process-output state is retried on `States.Timeout` and transient Lambda faults (`workflowAslBuilder.create_retry_config`), and ingestion MOVES each output out of the staged prefix, so a retried attempt cannot learn from the listing what an earlier attempt ingested. `processWorkflowExecutionOutput` keeps a per-run ingest ledger for that (helpers in `common/workflows/executionOutputs.py`):

-   **Where.** Rows in `PipelineExecutionOutputFilesStorageTable` under the partition `ingest#<workflowExecutionId>` (`ingest_ledger_partition`), sort key `fileType:relativeFilePath`, the output-row shape plus `ingestState` and `uploadId`. Every reader of output rows queries by `pipelineExecutionId`, so ledger rows never show as outputs; the table has no TTL, and `permanent_delete_execution` deletes the partition with the run's other rows. No new table: the end-state role already reads and writes that table.
-   **States.** `moving` is a CLAIM: `eo.claim_ingest_rows` writes it with a conditional put (holder `ingestHolder` = the attempt's Lambda request id, `movingSince`, the upload record's id) before an output is handed to ingestion, and only claimed outputs are handed over. The put never succeeds over an `ingested` row or another live attempt's `moving` row; it takes over a row older than `INGEST_TAKEOVER_SECONDS` (15-minute timeout plus 5), a row without a holder (written before holders were recorded), or a row the reader found abandoned (`takeover`: its upload finished `EARLIER_INGESTION_SETTLE_GRACE_SECONDS` ago and it is unchanged). `ingested` is written once the response says it landed; a failed output's row is deleted only while it is still this holder's. Ledger reads and writes are best-effort: a failure falls back to handing every listed output over (the upload handler still writes no second version).
-   **On every attempt.** A state timeout ends an attempt for Step Functions while its Lambda keeps running, so attempts overlap. Before deciding anything, wait (`_await_earlier_ingestion`, bounded by `EARLIER_INGESTION_WAIT_MAX_SECONDS` and the remaining time) while another live attempt holds rows `moving`, re-reading the ledger each poll; then decide from the ledger as read after the wait (`_partition_for_ingest`): `ingested` -> record, not ingest; held by a live attempt -> leave to it; otherwise claim. Outputs left to another attempt are awaited until `ingested` (`_await_held_outputs`); if the budget runs out, or the holder released one un-ingested, the attempt raises `EarlierIngestionInProgress` instead of ingesting it a second time. Before recording, re-read the ledger and merge its outputs (`_merge_earlier_outputs`).
-   **No second version.** `uploadFile` calls `destination_written_by_execution` before a move: a destination whose current version carries this run's `vams-changeworkflowexecutionid` is already written, so the move only deletes the staged source.
-   **Overlapping attempts finish SUCCEEDED (D2/D3).** A listed staged key gone by the time `_head_listed_objects` HEADs it (404/NoSuchKey/NoSuchVersion) is skipped as already moved; any other HEAD error still fails the listing. An attempt removes its staged versions BEFORE it settles rows `ingested`, so a waiter never lists a prefix mid-delete. The end-state status writes mark a FAILED with `executionFailedBy = endStateStep` (`eo.end_state_status_write`); a later end-state SUCCEEDED may replace that FAILED (and removes the mark and `executionError`), never a FAILED/ABORTED from the error handler or the abort path, and a FAILED never replaces a SUCCEEDED. The wait margin and settle grace scale with the attempt's starting time (`_attempt_limits`: margin at most a third, grace at most a sixth), so a short process-output timeout still leaves a wait budget.
-   **A taken-over write that already landed is not re-ingested.** After claiming, `_landed_takeovers` checks each output this attempt took over from another holder: when the destination's current version carries this run's `vams-changeworkflowexecutionid` (`_written_by_execution`, the upload handler's own evidence), the output is recorded, its staged versions (the listed one and the version the holder listed) are removed and its row settled `ingested`, without calling ingestion. Only outputs with no such evidence are handed over, so an ingestion call that always outlasts the function timeout still lets a later attempt finish.
-   **Bounded time per attempt (D4).** The takeover's destination checks and the staged-version deletes run in the stamp step's bounded S3 pool (`_in_s3_pool`, `MAX_PARALLEL_S3_WORKERS`). `_remove_then_settle` removes staged versions and marks rows `ingested` batch by batch (`SETTLE_BATCH_SIZE`), delete before mark per batch, so a killed attempt keeps its finished batches. The post-record log refresh is skipped when less than its budget plus `EXECUTION_LOG_REFRESH_MARGIN_SECONDS` remains; the record is written before it.
-   **Nothing left staged (#1291).** The provenance stamp copies each staged object onto itself, which on a versioned run bucket writes a new version, and the move deletes only the current (stamped) one. After an output lands, `_remove_listed_staged_versions` deletes the version the output was LISTED at (its descriptor `s3VersionId`), by id, and nothing else: never a version a copy job pinned (an engine-handed output is not deleted here), another key, or a version this run did not list. An empty or `null` id (unversioned bucket, overwritten in place by the stamp) deletes nothing. Covered by the role's existing `s3:DeleteObject*` grant on the asset buckets.
-   **Outputs handed to the copy engine (#1123).** A file the completion call hands to a large object copy job (`ingestionJobId` in its result) moves from this attempt's `moving` row to `engine-copying` with `engineJobId` (`eo.mark_engine_copying`); it is not deleted from staging (the job pinned that version and deletes it once it lands) and not settled. An `engine-copying` row is never claimed, waited on or taken over (`_partition_for_ingest` puts it in its own bucket; `is_live_moving_row` and the claim condition see only `moving`), so a retry never hands it over again; its job is folded back into the batch from the jobs table (`_merge_earlier_attempts`). The job id is `job_id_for(workflowExecutionId, relativeKey)`, so a row left `moving` by a killed attempt and taken over is handed to the SAME job (`start_copy_job` is a no-op on a recorded job). FinalizeOutputs settles the rows from the batch outcomes (`eo.settle_engine_rows`): `ingested` for a SUCCEEDED copy, the row deleted otherwise (the run records the file failed). It also settles a `moving` row whose staged key is the source of one of the run's ended copy jobs (the attempt died after the engine accepted it, before it wrote `engine-copying`), conditional on the row being unchanged since read; a `moving` row no job reads from is a small-file move and is never touched. The takeover age applies to `moving` rows only; an engine copy is bounded by `MAX_OUTPUT_INGESTION_WAIT_HOURS`. Each completion chunk claims its outputs under its own upload record, so a takeover waits on the right upload. No workflow output reaches `sqsUploadFileLarge` any more (every async-size output of a run goes to the engine), so it holds no claim.

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

Fields that can be referenced in ABAC policy rules: `databaseId`, `assetName`, `assetType`, `tags`, `tagName`, `tagTypeName`, `roleName`, `userId`, `pipelineId`, `pipelineExecutionType`, `workflowId`, `category`, `name`, `metadataSchemaName`, `metadataSchemaEntityType`, `complianceSchemaName`, `complianceState`, `cascadeId`, `auditLogGroup`, `object__type`, `route__path`, plus any field the add-on or fork constants lane registers. The compliance handlers annotate `complianceSchema` objects with `complianceSchemaName`, `complianceEvaluation` objects with `databaseId` + `complianceState` (a real state only on the quarantine list, release and exception routes and the single-asset state GET; every other evaluation route passes `""`, and the docs say so), `complianceCascade` objects with `cascadeId`, and `complianceBinding` objects with `databaseId`. A schema bind or unbind checks `complianceBinding` `PUT`/`DELETE` on the database (an asset binding uses the asset's database), the schema, and a `GET` of the target `database`/`asset` row; it never checks a write on the target, because a binding writes compliance records only.

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

**A stored reference that the system later acts on is authorized when it is saved.** A compliance pipeline rule names a workflow that every evaluation launches as the system user, so `complianceSchemaService.pipeline_rules_refused` decides each new or changed `pipelineRef` on schema create and update the way the execute route decides a run (`may_run_workflow`: Tier 1 `POST` on `/workflows/{databaseId}/{workflowId}/execute` through `enforcer.enforce` on an `api` object, Tier 2 `GET` on the workflow, its pipelines and the rule's own pipeline, each row read with one `GetItem`). It is deny by default: a missing workflow or pipeline answers the same `403` (`Not authorized to run the workflow of pipeline rule rules[<n>]`, `rulePosition`: the rule's position in the body's `rules`, never its caller-chosen name) as a refused one and is logged through `log_authorization`. Unchanged rules are not re-checked and `SYSTEM_USER` is not checked. After it, `system_pipeline_rule_refused` refuses (400, `systemRecords.SYSTEM_RECORD_PIPELINE_RULE_MESSAGE`, `ruleName`) a new or changed rule whose workflow, own pipeline or a pipeline its workflow runs is a system record (`systemRecords.names_system_record`), for every caller including `SYSTEM_USER`; `complianceEvaluationStore` never launches such a rule from a schema stored before the check (error result with the same message, and a waiting one is closed). Follow the same shape for any new field that makes the system act later on an object the saver names.

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

**Any field that carries a workflow's or pipeline's database id must pass `allowGlobalKeyword: True`**
(the execute route's `workflowDatabaseId`, the execution and pipeline models' `databaseId`, and
`CompleteExternalUploadRequestModel.workflowDatabaseId`): every shipped pipeline registers a GLOBAL
workflow, so refusing the keyword breaks every customer run (#1123 D2). A new field carried on a
cross-call needs a test that sends it through the receiving handler's own model with `GLOBAL`
(e.g. `tests/handlers/workflows/test_processOutput_write_back_end_to_end.py`), not a model built by
hand with a database-scoped fixture.

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

**An asset is found by its database and asset ids, never by its id alone.** The same asset id can exist in more than one database, so a handler that answers for one asset reads it by key: `common.dynamodb.get_asset_object_from_id(databaseId, assetId, include_archived=False)` requires both ids and reads through `get_asset_record`; `assetIdGSI` serves only cross-database lookups that decide by registration and location (bucket sync, file attribution). Comments and subscriptions are keyed the same way (`common/commentSubscriptionKeys.py`): `CommentStorageTableV2` has partition `databaseId:assetId`, sort key `commentId` (generated by the create) and LSI `AssetVersionIdLSI` (sort key `assetVersionId`), and a soft delete moves the row to `databaseId:assetId#deleted`; `SubscriptionsStorageTableV2` has partition `eventName` and sort key `entityName:databaseId:entityId`. Handlers read only the V2 tables; the v2.6 tables' names are published under `dynamoTables/legacy/` for the `commentSubscriptionKeys` migration step only and are not in `ResourceKeys`. That step keeps one copy record per copied v2.6 comment and subscriber in `CommentStorageTableV2` under partitions starting with `#commentSubscriptionKeys:` (a database id never contains `#`); no handler reads them, and code that ever scans the comment table must skip them. Every comment and subscription handler decides Tier 2 with `asset_authorization_object(record, databaseId, assetId, include_archived)` before it answers 404. Within a database an id stays unique across live and archived rows: `createAsset` refuses an id the `databaseId` or `databaseId#deleted` partition holds, and unarchive refuses while a live row with the id exists (a consistent read, then a conditional put). Tests: `tests/handlers/comments/test_comment_database_keys.py`, `tests/handlers/subscription/test_subscription_database_keys.py`.

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
-   **Content keys** (`CONTENT_KEYS`) — `configBody`, `templateTags`, `tagValues`, `customTemplateOverride`, `webFormJson`, `inputInstructions`, `searchText`. A pipeline template body or tag value carries free-form caller content (prompts, model configuration). Every field that can carry a template body belongs here whichever request delivers it: an execute request supplies one as `customTemplateOverride`, a template record as `configBody`. The value is replaced with `<redacted>` and the key is kept, so the record still shows that the field was submitted. `searchText` is where a search or text-filter read records the text its caller typed (a search's `query`, `metadataQuery`, token values, filter `query_string` options and the simple search's text fields, the compliance listings' `text`, the execution log read's `filterPattern`): build the read's `request_parameters` with `search_text_parameters` (`common/auditSearchText.py`), which also records `searchTextFields` and `searchTextLength`. Generic names such as `query` or `value` are not content keys, so other records keep them. `tests/common/test_audit_search_text.py` fails when a registered read or search action records a free-text field outside that key.

A request `body` is masked whether it arrives as a dict or as a JSON string — the string is parsed, masked, and re-serialized. Redaction is key-driven, so an f-string that interpolates a payload value (`logger.info(f"template {body}")`) bypasses it entirely: log identifiers, counts, and flags instead. An event-source handler (indexer, bucket sync, stream consumer) logs `event_log_summary(event)` from `common/eventLogSummary.py` at entry, never the event: a stream image carries every field value of its row, and a metadata row's field name is itself a value (`metadataKey`), so not even a structured dict is redacted there. A search-service exception is the same case: its text quotes the rejected field value (an over-long metadata value, a bad geo shape), and `logger.exception` adds that text again in the traceback, so an indexing path logs `exception_log_summary(e)` (class and HTTP status) with `logger.error`. An API handler, an identity provider trigger or a state machine task logs `event_log_summary(event)` at entry too: the route template, the names of the path parameters and the sizes of the query string and body, never the typed text (search terms, filter patterns, comment text) they carry; a search path logs `query_clause_log_summary(clause)` for a generated query. `tests/handlers/test_handlers_log_no_whole_events.py` scans every module under `backend/backend` for log calls (`logger.*`, `self.logger.*`, `log.*`, `print`, including `extra=`, `%` and `.format` formatting, concatenation and f-strings) that pass a request-content name (`event`, `body`, `query_params`, `query_clause`, `item`, `asset_object`, `result`, a stream record or image) outside a summary helper, and fails on one that is not in its reviewed allow-list (each entry has a reason, and a stale entry fails too). A `name['key']`, `name.get('key')` or `name.key` of those names passes only when the key is on the test's reviewed list of identifier keys (ids, counts, status, table keys), so `item['commentBody']`, `event['headers']`, `event['requestContext']`, `record['dynamodb']['NewImage']` and `request_model.dict()` are flagged; `queryParameters`, `request_model`, `requestBody` and `payload` are content names too. The scan cannot see request content bound to a variable name outside that set (`typed = json.loads(event['body'])` then `logger.info(typed)`), content returned by a helper, a listed identifier key that holds text in one module, or a logger whose name does not contain "log"; review new log calls by hand and keep the live log search in the smoke plan. Comment handlers log the comment id and the length of its text, an identity provider trigger logs its trigger source, and a search that sends a query to the engine logs the error by class and status, since the engine's text can quote the query (#1552).

### Audit Logging

`backend/customLogging/auditLogging.py` exposes `log_authentication`, `log_authorization`, `log_authorization_api`, `log_file_upload`, `log_file_download`, `log_errors`, and other event-type functions — writing to **9 CloudWatch log groups** whose names resolve from SSM via `get_log_group_name(ResourceKeys.*)`. All audit functions extract user context via `request_to_claims(event)` (inside a request audit context, once per request; see "Request audit context" below). Every entry carries a `--- [event: ...]` echo. Authorization, error, auth change and auth other records (`_SHORT_ECHO_LOG_TYPES`) echo `_short_echo_event(event)`: request id and time, method, path, route, path parameters (masked, each capped like a record value) and connection addresses, never headers, query string or body. An action record written by `log_action` (and an auth change or auth other record inside a context) carries the compact echo. Authentication, file and `log_actions` records still echo the whole event after `mask_sensitive_data`, which keeps everything that is not a redacted key -- headers, the verified token claims (email included) and the request `body` -- so those records can hold personal data a user submitted; `docs/developer/audit-logging.md` "What Is Logged" states this and must stay accurate when an echo changes. `deferred_authorization_audit()` collects the `log_authorization` entries of a block and writes them with `_write_batch_to_cloudwatch` when it ends (every entry kept, also on an exception; nested blocks join the outer one) -- for handlers that decide many objects one by one. **Silent failure**: a failed audit write is logged locally, and Lambda execution continues — `mask_sensitive_data` upholds that contract by returning `<redacted>` rather than raising on a structure it cannot walk.

**Undelivered entries.** Every path that loses an entry (put_log_events raising, no client, stream creation failing, the log group name not resolving, formatting failing, any other exception) calls `report_undelivered_audit_records(log_type, reason, records)`: one `{"auditFallback": true, "logGroupKey", "reason", "record"}` JSON line per entry on standard output (the function's own log; the record capped by `_fallback_record`), and one CloudWatch embedded-metric-format line per call (namespace `VAMS/Audit`, metric `AuditWriteFailures`, dimension `AuditLogType` = the log type such as `auditActions`, value = entries lost). It never raises, needs no IAM beyond the function's log delivery, and nothing runs on a successful write. A batch write reports each failed batch's entries with one metric line per batch. A new write path reports its own losses the same way (`handlers/auth/routes.py` `_DenialAuditBatch.flush` reports the denials it could not hand to the writer). The CDK alarm on the metric is not part of the stack; `docs/developer/audit-logging.md` gives an example.

**Record format.** A record is one line: bracketed prefix segments, the JSON payload, then the event echo. Every value inside a `[...]` segment (user, roles, the secondary type, the probe endpoint) is encoded by `_encode_prefix_value` / `_encode_roles` / `_event_type`: `[`, `]`, `\`, CR, LF, other C0/C1 controls and bidirectional or zero-width characters become `\uXXXX`, and a value made of `userid_pattern` characters is unchanged. The payload and the echo are `json.dumps(..., ensure_ascii=True, default=str)`. Build records only through these helpers; a record with an extra segment gets its own formatter in `auditLogging.py` (`format_authorization_denial_record` for the probe marker), and `tests/mocks/customLogging/auditLogging.py` gets a matching stand-in.

**Audit log groups and the audit search library.** Which audit log groups exist is one registry, `common/auditLogGroups.py`: an `AuditLogGroup` descriptor per group (key, `ResourceKeys` attribute name, label, description, record prefix, type-segment flag, legacy parser name, `action_target`, sensitivity tags, add-on feature), merged core + `ADDON_AUDIT_LOG_GROUPS` + optional `CUSTOM_AUDIT_LOG_GROUPS` (read with `getattr`, so an older fork file imports); a key or prefix repeated across the lanes, or a `resource_key_name` found on none of `ResourceKeys`, `AddonResourceKeys` and `CustomResourceKeys` (looked up in that order), raises `AuditLogGroupConfigurationError` at import (fail closed: a skipped group would be unaudited). `customLogging/auditLogging.py` derives `_AUDIT_LOG_GROUP_KEYS`, `_GROUP_LABELS` and `_GROUPS_WITHOUT_TYPE_SEGMENT` from it and `common/auditActions.py` derives `AUDIT_GROUPS`; `tests/common/test_audit_log_group_writer_golden.py` pins the derived values to the earlier literals. `common/auditSearch/` reads the same registry: `queryBuilder` validates a request into enumerated filters and builds the Logs Insights query (no query text from the caller; only escaped safe literals reach it), `containsFilter` validates the `contains` terms (ASCII charset, bounded count and length, matched by case-folded `str.find`, never as a pattern), `parsers/` read a record by `auditVersion` (never raising; an unreadable record is a `partial`, `unparsed` or `unsupportedVersion` row), `recordMatcher` rechecks every filter on the parsed, redacted row and returns the matching rows only (never a count of dropped rows: the prefilter also matches hidden fields, so such a count would let a caller probe them; do not derive one into a response, header or metric), and `redaction` and `exportEncoding` redact, bound, encode and CSV-escape what is returned (invisible characters are escaped by Unicode category, credential shapes include GitHub, Slack, Basic, URL passwords and PEM keys, and a chunked JSON export is NDJSON). The per-group `auditLog` permission and the search Lambda (`handlers/audit/auditLogService.py`) use this library; the library itself calls no AWS service. A new group is a descriptor, its `ResourceKeys` entry and the CDK log group ("Add an Audit Group" in `docs/developer/audit-logging.md`); `tests/common/auditSearch/test_audit_search_registry_contract.py` fails for a group without a parser, label, valid key or log group. Free text a handler records in `request_parameters` or `details` beyond the `searchText` key is hidden on read by the key lists in `redaction.py` (`SEARCH_TEXT_KEYS`, `READ_TEXT_KEYS`).

**Action records and the registry.** `log_action(event, action, ...)` writes a record in the record contract (`auditVersion` 1; `docs/developer/audit-logging.md` "Action Records"): `recordId`, `action`, `outcome` (`success` / `failed` / `denied`, a generic `reason` on the last two), `eventTime`, `actor` (`userId`, `authMethod` `cognito`/`externalIdp`/`jwt`/`apiKey`/`system`, `apiKeyId`, `crossCall`, `onBehalfOf`), `request` (`requestId`, `method`, `route` template, `sourceIp`, `lambdaRequestId`), `entity`, the top-level ids (`AUDIT_ID_FIELDS`), `bytes`/`fileCount`/`itemCount`/`durationMs`, and the value sections `changes` (each field a write set, with its new value), `previous` (prior values the handler already loaded; never read a record only to fill it), `requestParameters` (what a read, search or download asked for; never what was returned: `log_action` drops `changes` and `previous` for an action of kind `read`, `search` or `download`, and `itemCount` on a read or search, the number returned, is the one field `AUDIT_READ_ITEM_COUNT` controls) and `details`. A key that does not apply is left out, never `null`. Every value section passes through `mask_sensitive_data`; a value over `AUDIT_VALUE_MAX_BYTES` (2 KB) becomes `{"truncated": true, "sha256", "bytes", "preview"}`, and `_bounded_action_record` bounds the whole formatted record (prefix, payload and echo) to `AUDIT_RECORD_MAX_BYTES` (32 KB): the echo is replaced by `AUDIT_ECHO_TRUNCATED` first, then `_cap_record` cuts payload values, and a prefix too long to leave room cuts the record with `AUDIT_EVENT_TRUNCATION_MARKER`. Never pass file content. The echo of an action record is compact (request id, method, path, route key). The action's group, `[type: ...]` value (its `legacy_type` when it has one) and entity type come from `common/auditActions.py`; when several routes register one action name (the four metadata routes), `log_action` uses the spec of the request's matched route (`_action_spec`), so the entity type is that route's. `common/auditActions.py` maps every route-method of `ALL_API_ROUTES` to an `AuditSpec` or an `AUDIT_EXEMPT` reason (add-on lane `common/addonAuditActions.py`, fork lane `custom/customAuditActions.py`; types in `common/auditActionTypes.py`); a denial is written to the authorization group with the `[AUTHORIZATION][authorized: False]` prefix, and a file group's record carries no `[type: ...]` segment. `tests/common/test_audit_registry.py` fails on an unregistered route-method, on a literal type passed to `log_actions` / `log_auth_changes` / `log_auth_other` / `log_action` that the registry does not know, and on a registered action no handler records, on a Lambda entrypoint (`lambda_handler`, `lambda_handler_<name>`) that neither carries `@audited_handler` nor enters `audit_request` and is not in `AUDIT_ENTRYPOINT_EXEMPT`, and on a system action its own handler module does not record. The scan counts the action as a string literal passed to `log_action` (or a typed legacy function), or to a module helper that forwards one of its parameters as that action (`_log_per_file(event, "fileDelete", ...)`); an action held in a variable or a dict is not seen, so pass the literal. Call `log_action` after the write succeeds, as `workflowService.py` places `log_actions`, and pass the entrypoint's own event object: the context is matched by identity, so a copy, `event["body"]` or one SQS record is written as outside the context (a queue or state machine handler that reassigns `event` keeps the original in a variable for its records; a helper deep in a call chain may use `current_audit_context().event`). Capture `previous` before the handler sets the new values on the record it read. The six compliance API handlers open the context in `lambda_handler` and record their route's action in each dispatch branch once the service function answered (`log_action(event, "complianceSchemaCreate", **audit_outcome(response), ...)`, `common/compliance/routeAudit.py`: a 2xx is a success, a 403 a denial, anything else a failure with a generic reason); a call the API-level check refuses, or that raises, records the route's own action (`log_action(event, **failure_outcome(status))`). A compliance write never records a schema body; a write that creates something names it from the service's answer (`created_value`: the `cascadeId` of a cascade create, the `evaluationId` of an asset evaluation, the schema `internalVersion` written, a database binding's `details.applyId`), and Evaluate All and a sweep record the count of evaluations started, never their ids; `tests/handlers/compliance/test_complianceActionRecords.py` checks every compliance route-method. A compliance request that works across databases (cascade reach, sweep, audit partitions, schema create scopes) decides each database with `enforce()`, so it records a denial for each database the caller may not act on, as the core listings do (`tests/handlers/compliance/test_complianceDenialsAcrossDatabases.py`). The four compliance Lambdas that serve no API route (`complianceTrigger`, `complianceWorkflowCallback`, `complianceCascadeExecutor`, `complianceJobs`) carry `@audited_handler` and record their system actions (`CORE_AUDIT_SYSTEM_ACTIONS`: `complianceAutoEvaluate`, `complianceRecordRemove`, `complianceCascadeAutoCreate`, `compliancePipelineRuleComplete`, `complianceCascadeRun`, `complianceBindingApply`, `complianceDatabaseRetire`) through a module `_record(action, ...)` helper on `current_audit_context().event` (`tests/handlers/compliance/test_complianceSystemActionRecords.py`). The deploy-time seed of the GLOBAL default schema lives with them (`handlers/compliance/complianceDefaultSchemaSeed.py`, a CloudFormation custom resource, as core's `crReindexer` and `importGlobalPipelineWorkflow` are deploy-time writers under `handlers/`) and records `complianceSchemaSeed` as the system user: the schema name and the version it wrote, or `details.written` false with `alreadyPresent` (`tests/handlers/compliance/test_complianceDefaultSchemaSeed.py`).

**Request outcomes.** `@audited_handler` records the route's action as `outcome="failed"` (generic `reason` from `audit_failure_reason(status)`; the path's id fields) when the handler returned a status of 400 or more, or raised, and recorded nothing for that action; a 401 or 403 adds nothing (the authorization record is the denial's record). On an auth-change or auth-other route the failed record carries the route's `[type: ...]` value (its `legacy_type`, e.g. `constraintCreateUpdate`) as the prefix and as `legacyType`, as the success records do. A handler therefore records only its successes. An error answer that only asks the caller to try again, and that the caller always retries, is no outcome: the handler calls `skip_route_outcome(event)` before returning it, and the decorator records nothing for it; the handler also builds that answer without `general_error(..., event=event)`, whose errors-group entry carries the action and `outcome: failed` (executeWorkflow's 429 to a vector reindex launch that finds every reindex slot held, `_reindex_slot_wait_response`; the launch that claims a slot records `workflowExecute`, and one refused for good records its failure). Only the outermost entrypoint records an outcome, and that record joins the request's flush (`log_action(..., flush_with_request=True)`; the request has ended, so holding it loses nothing and saves a write). `log_actions(..., outcome="failed")` marks an earlier-style record failed (written at once). `log_action(..., on_behalf_of=user)` names the user a system action ran for.

**Reads switch.** `app.auditLogging.recordReads` (default `true`, absent = `true`) reaches every Lambda as `AUDIT_RECORD_READS`; `audit_records_reads()` reads it on each call and only `"false"` turns it off, so a function deployed without the variable records reads. When it is off, `log_action` writes nothing for a successful action of kind `read` or `search`; downloads, exports, writes, denials and failures are recorded, and the earlier `log_*` functions are unaffected. Tests set it with `monkeypatch.setenv`.

**Audit log search switch.** `app.auditLogging.search.enabled` (default on; off in the EU Sovereign template, where CloudWatch Logs Insights is not available) deploys or omits the `/audit` routes, `auditLogService` and its table. The routes carry `ApiRoute.addon_feature=CORE_FEATURE_AUDIT_LOG_SEARCH` (`"AUDITLOGSEARCH"`, mirrors `VAMS_APP_FEATURES.AUDITLOGSEARCH`); the core stack adds it to `ADDON_FEATURES_ENABLED` when on, so `get_public_api_routes()` (`GET /auth/routes/api`, `/auth/routes/api/allowed`, the denial audit) leaves the routes out when off, with the same rule as add-on routes (unset variable = listed, for the local server and unit tests). Recording of audit events, the `auditLog` object type and the admin's seeded constraint do not depend on it. Tests: `tests/common/test_addon_features.py`, `tests/handlers/auth/test_addon_listings_follow_deployment.py`.

**Request audit context.** `with audit_request(event, context):` (or `@audited_handler`) opens one request's `AuditContext`: claims are resolved once (`_extract_user_context` reuses them, so `request_to_claims` and the claims hook run once per request, not once per record), and the actor and request facts are shared by every record. Successful `log_action` records, and `log_actions` / `log_file_*` records, are buffered and written when the block ends by `_deliver_records`: one `_put_audit_records` per group on a pooled thread, all groups in parallel, bounded by `_flush_deadline_seconds`: `AUDIT_FLUSH_DEADLINE_SECONDS` (1.5 s), cut to the remaining Lambda time less `AUDIT_FALLBACK_RESERVE_MS` (250 ms) so the fallback lines are written before the timeout, and written with the bounded `audit_flush_client` (1 s connect, 2 s read, 3 attempts). A group not confirmed by the deadline is reported with reason `flushDeadlineExceeded` (delivery is at least once; `recordId` identifies a duplicate). A timer flushes `AUDIT_PRE_TIMEOUT_MARGIN_MS` (750 ms) before the function times out, after which records are written at once. Denials, failures and `log_errors` records are written at once. An allowed authorization record (`log_authorization_api` for the context's own event, `log_authorization` on the context's thread, `authorized is True` only) is buffered the same way by `_buffer_allowed_authorization`, formatted as an immediate write in the same context formats it (only its write time moves, so its CloudWatch `@timestamp` is the flush time while `eventTime` and the echo's `requestContext.requestTimeEpoch` keep the check's time; its `recordId` identifies a duplicate after a missed deadline), so a request whose only record is its route check makes one write at the flush; an authorization denial is written at once (inside `deferred_authorization_audit()`, at the block's end), and outside a context both are written at once as before. Inside a context `log_actions`, `log_file_*`, `log_errors`, authorization (`log_authorization_api` for the context's own event, `log_authorization` on the context's thread: `_authorization_keys` adds the route's action and entity, `outcome` `success`/`denied`, actor, request, `recordId`, `eventTime`; the checked method moves to `legacy.action`; the decision never reads any of it), `log_auth_changes` and `log_auth_other` records (`_log_auth_record`: the masked legacy payload plus `build_audit_payload` keys, `legacyType`, the ids, `changes`/`previous`/`requestParameters` from keyword arguments, bounded by `_bounded_action_record`; a success is buffered, any other outcome written at once) keep their payload keys and gain the contract keys (the contract keys win: a legacy key of the same name with a different value, such as an AMI recipe build's `outcome`, moves under `legacy` (`AUDIT_LEGACY_KEY`); `action` is the route's action when the record is the route's own, by legacy type, name or legacy file function); `log_authentication` and `log_authorization_gateway` (the authorizer runs outside a context) keep their records unchanged. Auth handlers pass what a write changed through the keyword arguments and never a secret (API key value or hash, password, temporary password); redaction still applies. `log_authorization_gateway(..., verified_context=res["context"])` names the verified user, roles and MFA state on an allowed record (`_gateway_user_context`, the `request_to_claims` claim order); a denial and an ignored-path allow name `unknown`. A record for a different event object, or from another thread, is written as outside a context. Outside a context every function writes exactly as before. A handler that forwards a call to another VAMS Lambda with its caller's authorizer context sets `AUDIT_INTERNAL_CALL_KEY` (`vamsAuditInternalCall`) on the forwarded event to `internal_call_marker()` (the current request's `requestId` and user); `_resolve_actor` then records `crossCall: true` and `onBehalfOf`, and `_request_facts` takes the marker's `requestId` when the event has none. `_request_facts` sets `sourceIp` from `common/auth/viewerAddress.viewer_ip_for_audit(event)` first: the CloudFront viewer, read from the forwarding headers only when the request's `x-vams-origin-verify` header hashes to the `vams:originVerifyDigest` the REST authorizer put in its context (`clientIp.origin_verify_audit_context`, CloudFront deployments, accepted value only, added after the decision); otherwise the connection address. Audit only: no decision reads the digest, and a handler never holds the origin verification secret (`executeWorkflow._metadata_service_payload`, `ingestAsset`). The key is attribution only: authorization still reads the forwarded authorizer context, and a `lambdaCrossCall` payload stays unmarked. The context is a `ContextVar`, so a worker pool that builds forwarded payloads runs each job in its own `contextvars.copy_context()` (`executeWorkflow._run_bounded`); a job run without one sees no context and its marker is empty.

**Every path that hands out asset file content** — a presigned GET URL or a streamed body, in `downloadAsset`, `streamAsset`, `streamAuxiliaryPreviewAsset`, and `assetExportService` — applies three controls on top of Casbin: it refuses (or, for the export's per-asset entries, withholds the URL) when the asset's `isDistributable` is false; it runs `validateUnallowedFileExtensionAndContentType` on the file's key and the `ContentType` a `head_object` of the version about to be served reports, and refuses (export: withholds the URL, keeps the file in the listing) when the file is blocklisted or the head fails; and it writes a file-download audit entry (`log_file_download` / `log_file_download_bulk` / `log_file_download_streamed`) for every URL or body it actually issued. A new download surface gets all three; `downloadAsset.py`'s `lambda_handler` is the reference call shape, and `assetExportService.py` `file_type_allowed_for_download` is the withhold-not-refuse form. A surface that checks many files in one request runs the heads through a bounded pool (`downloadAsset.py`'s bulk branch, `assetExportService.py` `mark_files_allowed_for_download`): serial per-file heads over an asset of thousands of files exceed the API Gateway integration timeout. Every presigned `get_object` URL these surfaces mint passes its `Params` through `common.s3` `presigned_get_object_params()`, which signs `ResponseContentType=image/svg+xml` and `ResponseContentDisposition=attachment` for a key ending in `.svg` (any case) and leaves every other key unchanged: an SVG is stored as `application/octet-stream`, a browser `<img>` renders it only as `image/svg+xml`, and the attachment disposition keeps a directly opened link a download so no SVG script runs at the bucket origin (#1201). Never sign an SVG inline. `tests/handlers/assets/test_presigned_svg_response_overrides.py` pins each path.

**The refuse-before-sign checks live in `common/downloadEligibility.py`.** `downloadAsset.py` delegates to it; a new surface that hands out whole files of an asset (presigned URLs, archives) calls it rather than copying the logic. `get_distributable_asset_context` reads the asset (live table only, so an archived asset is "not found"), refuses a non-distributable one, then (with the caller's `check_quarantine`, compliance's `quarantineGuard.check_quarantine_block`, a no-op while `app.compliance.quarantineBlocksDownload` is off) a quarantined one, then one without `assetLocation`, and returns `(bucketName, baseKey)`; every caller that hands out file bytes passes `check_quarantine`. `check_file_eligibility` runs the per-file checks in a fixed order and returns `(final_key, version_id)` for the caller to sign. The order is: key containment (`resolve_asset_file_key`: a bucket-root key must sit under `'<base>/'`; a single-file base resolves the keys it holds: its object, its previews and its companion folder `{key}/`) and S3 key length; asset-version pin resolution (`resolve_pinned_version_id`, `assetVersionId` or alias, the alias wins); then, for a prefix, `validate_prefix_content_types` over EVERY object (one blocked object refuses the whole prefix, and a failed head raises) plus the prefix's own existence; for a file, one `head_object` of the requested version (404 refuses with "File not found in S3", a delete marker refuses with 410, then the blocklist). `is_file_archived(head)` reads `vams-status` archived/deleted or GLACIER/DEEP_ARCHIVE. The module builds no client and reads no table: the caller passes its Region-routing S3 client, its asset and bucket getters, and the `assetVersions.py` resolvers. `downloadAsset.py` keeps thin wrappers (`get_distributable_asset_context`, `check_s3_object_exists`, `resolve_and_sign_file_key`) that pass its own module globals at call time, so tests that patch `downloadAsset.s3`, `check_s3_object_exists`, `list_all_objects` or the resolvers still reach the shared checks. `streamAsset.py` (its own 403/404 statuses, CORS headers, `get_object`) and `assetExportService.py` (it withholds the URL rather than refusing, and lists its own keys) keep their own checks. A change to a rule in the module must hold for every caller. Refusals carry fixed generic messages (Rule 11).

**An asset location is a folder or a single file (`common/assetLocations.py`).** An `assetLocation.Key` that ends in `/` is a folder and holds every key under it. Any other key is a single-file asset (a `bucketExistingKey` that names an object). It holds exactly its object (asset-relative path `/{file name}`), everything under `{file key}/`, its companion folder (`{file key}/sub/x.obj` is `/sub/x.obj`), and its own previews `{file key}.previewFile.{ext}` (`/{file name}.previewFile.{ext}`). A single-file asset behaves like a folder asset and hides nothing: the companion entry named exactly like the object, `{file key}/{file name}`, and its previews are listed one level down, at `/{file name}/{file name}` and `/{file name}/{file name}.previewFile.{ext}`, and the rule runs along the chain of keys named only after the file (`{file key}/{file name}/{file name}` is `/{file name}/{file name}/{file name}`), so every key has one path and every path one key. S3 `Prefix` matching is not path-aware: a single-file key used as a prefix also lists keys that only share its characters (`model.glb.bak`, `model.glbx/x`), none of which belong to the asset. Code that lists, reads, archives or deletes an asset's objects filters what it listed with `contains(location, key)`, selects a folder path with `folder_listing_prefix` and `in_folder`, derives relative paths with `relative_path_in`, and resolves a caller's path with `object_key_for` (`None` for `/`, the single-file asset as a whole); never append `/` to a location key to make it a prefix. A listing or sample of a location reads `listing_prefixes(location)` through `held_objects(location, list_objects)`, which yields each held key once and keeps the one-key read only when it returns the location key itself: a single-file location lists its object with a one-key read, then `{file key}.previewFile.` and `{file key}/`, so keys that only share its characters are never read and cannot fill a page or a sample. Every file's own viewer data, the object's included, is `{databaseId}/{file key}/preview/...` (`common/assetLocations.viewer_data_prefix`); the rest of `{databaseId}/{file key}/` belongs to the files of a folder named like the file (the companion entries, another file of a folder asset, or an asset nested there), and inside `preview/` a key whose path has a `preview` folder segment belongs to a file in `{file key}/preview/`. `viewer_data_scope` returns that prefix and the `keep` predicate; the single-file delete, revert, move, rename and copy and the auxiliary preview delete in `assetFiles`, the partial cleanup of a permanent delete, and the asset version revert in `assetVersions` act through it (archive and unarchive change no viewer data). A folder delete keeps `aux_scope` and `aux_owner_filter` (the companion folder named `preview` of a single-file asset) with the nested-asset `keep_aux`, and the auxiliary stream route maps `/{file name}/preview/...` through `object_key_for` (`streamAuxiliaryPreviewAsset.resolve_aux_preview_key`). A folder key stored without its trailing `/` is fixed by the v2.6 to v2.7 migration step `assetLocationFolderKeys`. Uploads and workflow outputs land in the companion folder or at an own preview key; a user upload at the asset's own file path writes a new version of the object, and a workflow output there is refused (`names_own_object`). A workflow input of a single-file asset resolves to its object. A prefix delete or archive names a folder (`sub` is `sub/`). The file routes, stream, download, asset archive and unarchive, permanent delete, asset version snapshots, asset export, compliance's asset listing, uploads, workflow outputs and inputs, the copy engine's asset type and the metadata file check follow it. File metadata and file attributes are keyed by the path the file is listed at, whichever form names the file: `metadataService.file_record_path` takes a request path that is, without its leading `/`, a key the location `contains` as that key and keys it by `relative_path_in` (the web sends the file list's `key`, the CLI its `relativePath`), and takes any other path as the listed path with a leading `/{assetId}/` dropped. The read and the writes record that keyed path as the action record's file path (`get_file_metadata` returns it with the page, so recording it adds no read); the readers and writers of those records that start from an S3 key derive the path with `relative_path_in`. Download as Zip refuses a single-file asset, because the archive job derives member paths from a folder location. Whether two locations overlap is `locations_share_key(a, b)`, decided over the keys each holds, never `contains` of one location key in the other: `a/x` and `a/x.previewFile` both hold `a/x.previewFile.previewFile.z` while neither holds the other's key. `createAsset.keys_conflict` uses it, so a new asset whose location shares a key with an existing asset's is refused. `createAsset` refuses a `bucketExistingKey` with a reserved system folder or `vams.preparedDownloads` as any path segment (`is_reserved_existing_key`, read under the base prefix only) before any S3 read, refuses a chosen `assetId` equal to one of those names (`is_reserved_asset_id`; its derived location would be that folder) before any table read, and bucket sync skips `vams.preparedDownloads/` keys with the reserved folders, so no asset is created over system data. Known follow-up, not on these rules yet: the search file indexer (`osFileIndexer`), the vector indexer (`vectorIndexer`), the Garnet connector (`garnetDataIndexFile`), the Physna connectors (`physnaFileSync`, `physnaAssetSync`) and `s3PathPatterns.join_asset_location_key` still treat a single-file key as a prefix. Tests: `tests/common/test_assetLocations.py` and the `test_*single_file*` suites under `tests/handlers/`.

**The location index (`AssetLocationGSI`) is read only once `index_ready()` says the backfill is complete.** Every asset row, live and archived, carries `locationBucket` (its registration's bucket name) and `locationKey` (`assetLocation.Key` without a leading `/`); `AssetLocationGSI` on the asset table keys on them and projects `bucketId`. `createAsset` writes them through `location_attributes()`, and every writer that puts a whole row back (archive, unarchive, preview delete, the bucket-sync restore) passes it through `fill_location_attributes(row, bucket_name)` with its registration's bucket name: a row keeps the attributes it has, and a row the backfill has not reached gets them, because the backfill's consistent verification scan is not a snapshot. Archive, unarchive and preview delete already read the registration; the bucket-sync restore takes the event's bucket name. The restore re-reads the archived row `ConsistentRead` and copies that row, never the caller's cached image; with no archived row and no live row it returns `ARCHIVED_RECORD_GONE` and the key is handled like a key with no asset (#1799). `tests/handlers/assets/test_asset_row_location_attributes.py` lists every write on the asset table and fails on a new one until it is listed, and the only module that names the attributes is `assetLocations.py`. `resolve_owner(table, bucket, key)` answers which asset's stored location contains a key: the candidates (`candidate_locations`) are the key, each ancestor folder `X/` and the single file `X` whose companion folder it is, and an own preview's base file, one exact `Query` each in parallel, at most `MAX_CANDIDATE_LOCATIONS` (64, about two per folder level, so about 31 folder levels; a deeper key answers `TOO_DEEP`, and a caller keeps the checks that do not use the index for it); the deepest candidate whose row `contains` the key wins, two live rows on one location answer `AMBIGUOUS`, a hit is confirmed with a `ConsistentRead` `GetItem`, and a per-container cache keeps hits 60 s and misses 10 s. `find_conflicts(table, bucket, location)` answers which rows share a key with a location (`locations_share_key`, the rule `createAsset.keys_conflict` uses). That is more than one containing the other: a single file `S` and `S.previewFile` or `S.previewFile.` hold common preview keys that neither location names. It reads the candidates that contain the location, those preview partners (`_preview_partner_locations`), the first row under its folder form (`F/`, or a single file's companion folder `S/`) and, for a single file, the first location under its preview stem that shares a key with it; a location too deep raises `LocationTooDeepError`. The `assetLocationIndex` step's overlap report applies the same rule. Existing assets get the attributes only from the v2.6 to v2.7 migration step `assetLocationIndex`, which writes its completion marker last (the SSM String parameter `<VAMS_RESOURCE_PARAM_PREFIX>/dataMigrations/assetLocationIndex`, JSON with `version`, `status` and the `assetTable` it covers). The marker means every row whose location holds keys is indexed, a row at its registration's own prefix included (reported for review); rows with no location, at the bucket root (an empty location, which holds no key) or with no bucket registration carry no attributes. A v2.6 redeploy removes the marker custom resource, and its Delete deletes the marker, so the step runs again after the next upgrade. Before the marker exists the index is incomplete, so a decision that reads it (attribution, an ownership check) must first call `index_ready(asset_table_name)` and keep the logic that does not use the index while it is False; `assert_existing_key_not_owned` in `createAsset.py` keeps its `BucketIdGSI` walk for that reason. Index reads pass plain values to the table's `meta.client`, which serializes them itself. A function whose code reads the index needs `dynamodb:Query` on `<asset table>/index/AssetLocationGSI` (`ASSET_LOCATION_INDEX` in `storageBuilder-nestedStack.ts`); `grantReadData`/`grantReadWriteData` give it through `table/index/*`, and a hand-written asset-table statement must name the index (executeWorkflow, the large-copy engine). `infra/test/storage/assetLocationIndexReaderGrants.test.ts` checks every reader in the commercial and GovCloud templates and with Physna and Garnet on; which handlers read the index comes from `backend/tests/common/locationIndexReaders.py`, a static call-graph reading of the backend (`test_location_index_readers.py`), so a new reader is covered without a list to edit. The marker read is covered by the resource-name parameter grant. `index_ready()` reads the marker with `common.ssmParameters.read_parameter_value()` (bounded and uncached, since the marker is written after deployment) and caches its own answer per table for 300 s; a missing marker answers False without a warning, any other failed read answers False with one. Once the index is ready, `assert_existing_key_not_owned` (given the bucket name) checks `find_conflicts` instead of its walk. Bucket sync (`sqsBucketSync.py`) attributes a created or deleted file by the first folder (`BucketIdGSI`, through `lookup_asset_for_key`: the live row of the id in the registration whose stored location holds the key, the deepest when several do) when that asset's stored location `contains` the key and no other stored location holds it (`held_by_another_location`: one `nested_locations_of(..., include_shared=True, index_hits_any_time=True)` query on that location, reused for `NESTED_LOCATIONS_TTL_SECONDS`, fresh on a redelivery; a nested location, live or archived, or another live asset at the same location sends the key to `resolve_owner`, and a deeper row gone from the table or a key the index cannot answer for leaves it to the first folder; D10), else by `resolve_owner` with archived rows (the deepest stored location that contains the key, live or archived, one lookup; a hit counts at any time, an archived owner's included: the asset is restored from it and the file gets its ledger row, tags and binding before readiness as after, so bucket sync and the indexers, which read the same index, give a record one answer; a miss counts only once ready, before which the first-folder logic decides as before); `AMBIGUOUS` goes to the rejected-record queue (`BUCKET_SYNC_REJECTED_QUEUE_URL`); a key no location holds whose first folder is the id of an asset of the target database stored elsewhere (live or archived) goes there too (`assetIdInUse`) rather than auto-creating a second asset with that id in that database: `create_new_asset` raises `AssetIdInUseError` for an id the target database holds (`database_asset_id_rows`), and an asset with the id in another database or bucket does not stop the create; an archived first-folder asset is restored only when its location holds the key; a row of the registration that holds the key but that the lookups missed is restored when archived and looked up again on the next receive when live (`RecordRedrive`); an auto-create once ready needs a clear `find_conflicts` and is deferred once (`RecordRedrive`, 10 s visibility, batch item failure) when its folder already holds other objects. A key or location the index cannot answer for (`TOO_DEEP`, `LocationTooDeepError`, or the `RuntimeError` of a lookup past `MAX_PAGES_PER_LOOKUP`) keeps the pre-index logic for that key: bucket sync (`resolve_by_location` returns None) decides by the first folder, `auto_create_refusal` leaves the decision to createAsset, `attribute_object` goes on to the stamp and the first folder, and `assert_existing_key_not_owned` walks `BucketIdGSI`. Each forwarded record gains an additive `vamsBinding` (`databaseId`, `assetId`, `bucketId`, `relativePath`, `resolvedBy`: `fastPath` or `locationIndex`), and the history row of the event's own `versionId` records `etag`, `size` and `resolvedBy`. A new deployment writes the marker at deploy (`handlers/indexing/crAssetLocationIndexMarker.py`, a custom resource: Create only, a table created within the window and holding no row, never over an existing marker). Its Delete, when the stack is removed or a release without the resource is deployed over it, deletes the marker whoever wrote it, so rows written later without the attributes never meet a stale one; an Update does nothing and keeps the physical id, and the resource's logical id is pinned, so no update replaces it.

**A bucket registration is identified by its `bucketId` (`common/bucketRegistrations.py`).** A row of the S3 asset buckets table is one registration. The bucket name is used only to hand the bucket to a pipeline, in registration checks, and for S3 object reads, writes, listings and owner lookups (`assetLocations`, `fileAttribution`); a response may show `bucketName` beside `bucketId` for display. The deploy-time custom resource (`infra/lib/nestedStacks/storage/customResources/populateS3AssetBucketsTable.ts`) is the only writer and never deletes a row: it matches a configured registration on its bucket name and its prefix with one trailing `/`, read from the stored attributes (the stored item keeps its key, which is part of the primary key), pages its scan, and marks a row whose registration left the configuration `retired` (with `isDefault` false) on Create and Update, so assets and databases that name its `bucketId` still resolve. Two rows an earlier release wrote for one registration are both kept: the one assets reference (one `Query` per row on the asset table's `BucketIdGSI`, the custom resource's only asset-table grant) answers lookups and carries `isDefault`, the others name it in `duplicateOfBucketId`; when several are referenced or the references cannot be read, the row the configuration spells answers and the custom resource logs an error naming the others (no data migration). Lookups by bucket name and prefix leave retired rows out and put duplicates after the row they name (`active_registrations`, `primary_registrations`, `same_registration`): `sqsBucketSync.get_bucket_id`, `fileAttribution`'s first-folder step, `osFileIndexer.get_registered_bucket_prefixes`, `fileEnumeration` (a location is walked once, and an index owner of another registration is not listed for the walked one), `GET /buckets` and `createDatabase`. `resolve_default_bucket` counts flagged, non-retired registrations by `bucketId` and raises `DefaultBucketAmbiguousError` for more than one. Bucket sync caches and matches a registration's auto database by `bucketId` (the generated database id hash is unchanged). Every message bucket sync forwards carries `ASSET_BUCKET_ID` beside `ASSET_BUCKET_NAME`/`ASSET_BUCKET_PREFIX` (`common/indexerEvents.py`: `stamped_bucket_id`, `carry_bucket_id_stamp`), and the consumers pass it to `attribute_object(registration_bucket_id=...)`, which refuses an owner in another registration; a message without it checks nothing. The internal external-upload completion resolves the bucket a workflow write-back reads from itself (`uploadFile.external_upload_source`) and never uses the caller's `sourceBucket`; the large-file queue message carries `bucketId` and `sourceBucketId`, and `sqsUploadFileLarge.registration_refusal` refuses a message whose bucket names are not those registrations. A pipeline-execution row records `S3AssetPipelineBucketId` and `S3AssetPipelineBucketBasePrefix`, and the execution detail's `renderedConfigLocation` adds `bucketId` and `relativeKey`. Tests: `tests/common/test_bucket_registration_identity.py`, `tests/handlers/indexing/test_sqsBucketSync_registration_identity.py`, `infra/test/storage/populateS3AssetBucketsTableIdentity.test.ts`.

**Consumers of S3 records attribute a file with `common/fileAttribution.attribute_object`, never with the stamp alone.** A file belongs to the asset whose stored location contains it, in the same bucket. `attribute_object(bucket, key, asset_table=, buckets_table=, binding=binding_of(record), include_archived=, read_stamp=)` answers, in this order: the record's `vamsBinding` (accepted only when its `bucketId` is a registration of the event's bucket, the asset row exists and its stored location `contains` the key); `resolve_owner`, always asked with `include_archived=True` so the deepest owner decides live or archived (a hit at any time; without the caller's `include_archived` an archived deepest owner attributes nothing, so an archived asset nested in a live one never hands its file to the live one, and `tests/common/test_owner_lookups_read_archived_rows.py` fails on an owner lookup anywhere that skips archived rows); and only while `index_ready()` is False, the stamp (`databaseid`/`assetid`, read lazily through `read_stamp`) and then the first folder under a registered prefix (`assetIdGSI`), each with the same bucket and containment checks. A miss once the index is ready is final. `AMBIGUOUS` (two live assets on one location, or one asset id of the first folder in two databases of the registration) attributes nothing and is logged at ERROR. The answer carries the live `database_id`, `asset_id`, `relative_path` (from `relative_path_in`, so single-file locations follow their rule), `resolved_by` (`binding`, `locationIndex`, `stamp`, `firstFolder`), the row and whether it is archived. Callers with their own cached readers pass `get_row` and `get_registration` (the file indexer passes `get_asset_details_any_state` and `get_bucket_details`). A registration that cannot be read raises `RegistrationUnavailableError`, so the caller retries the record (the fileUpload trigger dispatcher reports the message as a batch item failure and launches nothing from it, and does the same for a retryable read error while attributing a file: a throttle, a service-side fault or a connection error, `is_retryable_read_error`; any other error drops only that file); a registration that does not exist attributes nothing and is logged at ERROR. The file indexer, vector indexer, both reindex walks (once the index is ready, `assetLocations.load_bucket_locations` reads a bucket's index partition once and `BucketLocations.owner(key, include_archived=True)` matches each key in memory to its deepest owner, live or archived; the vector walk and the `--tag-synced` tool keep a key only when that owner is live, so an archived asset nested in a live one never hands its files to the outer asset), the trigger dispatcher, the compliance trigger (no stamp read: the object is read only once its evaluation target is known; asset-table queries and rows are read once per batch), Physna and Garnet attribute through it, and every key they build from a relative path comes from `object_key_for`. `tests/common/test_stamp_metadata_readers.py` fails on any read of the stamp keys outside its reviewed list (the helper's fallback, the reindexer's fallback and bucket sync's echo check). The change source (`vams-changesource`) is still read where a consumer reads it, and never decides attribution. The file indexer records `indexedAt` on the sync-ledger row of the version it indexed (one conditional `UpdateItem` on the file version history table, `versionId` `null` for an object of a bucket without versioning; a missing row is logged). Tests: `tests/common/test_fileAttribution.py`, fakes in `tests/common/fileAttributionFakes.py`.

**Bucket sync never writes a directly written object; it tags the synced version.** The created-events handler makes no `CopyObject` or `PutObject` on the object an event names: the object keeps its version, ETag, LastModified, headers, storage class, tags and owner, and carries no `assetid`/`databaseid`/`vams-changesource` metadata unless VAMS wrote it. Once a version is attributed, `tag_version` writes the two VAMS object tags through `common/vamsObjectTags.tag_synced_version`: `GetObjectTagging` with the event's `VersionId`, then `PutObjectTagging` with the merged set only when `VAMS:databaseId` or `VAMS:assetId` is missing or names another asset (customer tags kept). A value S3 tag values do not accept is written as `b64:` plus base64url; a value still over 256 characters is left off (`valueTooLong`); more than 10 tags in the merged set skips (`tagLimit`, no customer tag removed); `AccessDenied` skips (`accessDenied`, WARNING once per bucket per hour); every skip emits the `VamsTagsSkipped` embedded metric (namespace `VAMS/BucketSync`, dimensions `Deployment` and `Reason`). `Deployment` is `vamsObjectTags.metric_deployment()`, the first segment of `VAMS_RESOURCE_PARAM_PREFIX` (`{name}-{baseStackName}`), so two deployments in one account and Region keep their own series; every bucket-sync metric carries both dimensions. A registration whose `tagSyncedObjects` is false gets no tag call; a registration without the field is tagged. The deploy custom resource (`populateS3AssetBucketsTable.ts`) writes `tagSyncedObjects` on every registration row from `app.assetBuckets.tagSyncedObjects` (the created bucket) or the external entry's own key, both default true and validated as booleans in `getConfig()` (`validateTagSyncedObjects`) and ConfigBuilder (`tag-synced-objects-not-boolean`). The ledger row records `vamsTags` (`written`, `already`, `skipped`) and `vamsTagsReason`. The tags are a marker for customers' own tools: attribution, triggers, indexing and authorization never read them. The echo check (`is_vams_metadata_stamp_echo`) stays for copies earlier releases made that may still be queued at upgrade. Tests: `tests/common/test_vamsObjectTags.py`, `tests/handlers/indexing/test_sqsBucketSync_no_copy_tags.py`.

**A folder's existing files are imported by bucket sync, from its own queue.** Files already in a folder when it becomes an asset's location raise no S3 event. An `init` marker in a first-level folder therefore sets the asset type and calls `import_folder`, which lists the asset's location with `list_object_versions` (current versions only; folder markers, VAMS system folders and keys `contains` rejects left out) and sends the objects to the function's own created-events queue (`BUCKET_SYNC_CREATED_QUEUE_URL`, its only send grant besides the rejected-record queue) as `ObjectCreated:Put` records marked `vamsImport` (the importing `databaseId`, `assetId`, `bucketId`), `IMPORT_RECORDS_PER_MESSAGE` per message, in the SNS notification shape. `process_imported_record` attributes such a record to that asset only when it is that asset's key: the record's `bucketId` and the row's must be this function's registration, the row is read ConsistentRead once per batch (`owner_rows`), its stored location must contain the key, and no deeper location of another asset may hold it. Once the index is ready `resolve_owner` decides (another asset or `AMBIGUOUS` skips the key); before, `import_folder` leaves out the keys held by the stored locations of the registration's other live assets inside the imported one (`nested_locations`, one `BucketIdGSI` walk per listing, also used for a key the index cannot answer for). There is no auto-create. It skips a version the ledger already has (`version_already_synced`), then tags it, writes the ledger row with `resolvedBy: import` and forwards it with its `vamsBinding`. Imported files reach the file-indexer topic but not the orchestration bus (`IMPORTED_FILES_START_UPLOAD_WORKFLOWS = False`), so they start no `fileUpload` workflow. A listing short of time (`IMPORT_LIST_TIME_RESERVE_MS`) sends a `vamsImportRequest` message with its markers, which `split_import_requests` hands to `continue_import`. An `init` import whose listing or send fails raises `RecordRedrive`, so the marker's record is received again: the marker delete then finds nothing, and the ledger skips the files the failed attempt already queued. `createAsset` starts the same import for a `bucketExistingKey` (folder or single file): `request_existing_files_import` sends one `vamsImportRequest` with `start: true` to the `syncCreatedQueueUrl` of the asset's registration row (absent on rows from earlier releases: nothing is sent, and a reindex reaches the files). `continue_import` trusts nothing in a request: `validate_import_request` requires a live asset row (ConsistentRead, not archived, not a `#deleted` partition) in the request's `bucketId`, that registration's bucket and prefix equal to this function's, and the request's location equal to the row's stored location; anything else is logged at WARNING and dropped. A `start` request first sets the asset type once from the location's files. `determine_asset_type` in bucket sync samples the location with `held_objects` (at most `ASSET_TYPE_DETECTION_SAMPLE_SIZE` per listing), as `uploadFile.determine_asset_type` does. The fan-out keeps a recursive-loop chain at two invocations plus one per continuation. Tests: `tests/handlers/indexing/test_sqsBucketSync_folder_import.py`, `tests/handlers/assets/test_createAsset_existing_files_import.py`; the send grants are pinned by `infra/test/storage/bucketSyncFolderImport.test.ts`.

**A nested asset's files are not the containing asset's.** A key belongs to the deepest stored location that contains it. `assetLocations` holds the pure rule: `is_nested_in(location, other)` (they share a key and `other` is longer), `deeper_holder(location, nested, key)` and `held_by(location, nested, key)`, and `NestedLocations` (`holder`, `holds`, `keep`, `keep_aux`, `is_shared_key`). The rule is ownership, not databases: a nested asset's files and their auxiliary (viewer and preview) data are its own whatever its database (`keep_aux`). `keep_aux` reads an auxiliary key as the viewer data of one file (`viewer_data_file_key`: `{databaseId}/{file key}/preview/...`, the file key ending at the last `/preview/` segment, the boundary `viewer_data_scope` uses), so a file of the containing asset keeps its own data when an asset is stored in a folder named like it (`{file key}/`). The lookup has two backends behind `nested_locations_of`: `nested_locations_in_index` (`AssetLocationGSI` `begins_with` on the location itself, which covers a folder, a single file's `{key}/` and `{key}.previewFile` partners, and an equal location; paged to `MAX_NESTED_LOCATION_PAGES`) once `index_ready()`, and `nested_locations_in_rows(registration_rows(...))` (the `BucketIdGSI` walk of every registration of the bucket) before it, for a `destructive` operation only (asset archive, unarchive, permanent delete; file and folder delete, archive and move); any other operation finds none before readiness. A page-cap error keeps that pre-index rule. A destructive lookup also names the other live assets stored at exactly the same location (`shared`, given the asset as `owner`; an archived one does not count, nor the asset itself): an equal location counts like a nested one, so archiving or permanently deleting one of two assets on one location leaves every version of the shared keys to the other, and a file operation on a shared key answers `SHARED_PATH_REFUSAL`. A write into a shared location (an upload's initialize and both completions, so workflow outputs too, a new folder, a copy into the asset, a revert, an unarchive, a primary-file change) is refused before anything is written with `SHARED_LOCATION_WRITE_REFUSAL`, which names the troubleshooting section, because bucket sync would send the written file to the rejected-record queue: `uploadFile.upload_path_refusal` and `assetFiles.refuse_unwritable_key` read the nested locations with `include_shared=True` (a read: from the index once it is ready, cached with the owner in the key). An asset unarchive is refused while another live asset is stored at exactly its location (`assetLocations.unarchive_conflicts`: `live_co_owners`, every index row at the location read to the end, live rows of other assets kept; the pair the shared-location rule names), before anything is written, with `UNARCHIVE_LOCATION_IN_USE_REFUSAL`. A nested pair is not refused: with the nested-asset rule each of its keys has one deepest owner, so an outer or inner asset of a legacy layout is unarchived as before; bucket sync does not restore such an archived owner for a direct write and sends the record to the rejected-record queue (`archivedOwnerLocationInUse`). Both unarchive as before until the index is ready or when it cannot answer for the location. The auxiliary data of a shared location is kept when the other asset is of the same database (both read it at one auxiliary key). Callers use `fileAttribution.nested_locations_of_asset(asset_table, buckets_table, asset, bucket, location, destructive=)`, which passes the owner; a read reuses its answer for `NESTED_LOCATIONS_TTL_SECONDS`. The asset type uses `nested_locations_for_asset_type` (a read, no registration walk; a failed lookup classifies every key the location holds, so the step that sets the type does not fail). Sites: `assetFiles` (list, info, single-file and folder operations, versions listing; `refuse_nested_key` answers as the operation's missing file), `assetService` (archive, unarchive, permanent delete, auxiliary data), `assetVersions` (create, revert and details skip nested files), `downloadEligibility`/`downloadAsset` and `streamAsset` (not found), `streamAuxiliaryPreviewAsset` (`viewer_data_of_nested_asset`: the viewer data of a nested asset's file answers 404 through the containing asset's path, GET and HEAD; the bucket is the row's `locationBucket`, so a row without it finds none), `zipDownloadService` (refusal, prescan, and the job manifest's `excludeLocations`, which the zipArchive pipeline's `resolve.held_by_excluded` applies), `uploadFile` (`upload_path_refusal`: `NESTED_PATH_REFUSAL` or `SHARED_LOCATION_WRITE_REFUSAL`, both user-correctable for workflow outputs), `executeWorkflow` (a nested input is missing; a folder or whole-asset input's manifest entry carries `excludeLocations`, the nested locations under it, which the zipArchive pipeline's `archiveRun` applies), `interimPipelineTracking` (steps 2+: `_get_original_input_entries` recomputes `excludeLocations` from each workflow-input row's asset bucket, asset root and key with `fileAttribution.folder_input_exclude_locations`, the rule step 1 uses, and `executionOutputs.resolve_manifest_input_files` carries it on shadowed and unshadowed entries; its role holds `dynamodb:Query` on `AssetLocationGSI` alone), `metadataService` (a nested file is not found), the asset type (bucket sync, both upload completions, `sqsUploadFileLarge` and the large-copy engine, whose role holds `dynamodb:Query` on `AssetLocationGSI` alone), and bucket sync's folder import. The guard `tests/common/test_nested_location_sites.py` fails on a module that reads `assetLocation` and lists or resolves keys without the nested locations, unless it is reviewed there. Responses never name the nested or the other asset.

**An auto-create refused for an overlapping location is not an error.** `createAsset` raises `models.common.AssetLocationInUseError` (a `VAMSGeneralErrorResponse` with `owner`, the asset that holds the location) from its three ownership checks and logs the refusal once at WARNING with that asset. Bucket sync lets it through `create_new_asset`, logs it at INFO, counts it in `AutoCreateRefused` (namespace `VAMS/BucketSync`, dimensions `Deployment` and `Reason`, `vamsObjectTags.emit_bucket_sync_count`) and forwards the record; the index-ready refusal (`auto_create_refusal`) is one WARNING and the same count. ERROR stays for the rejected-record queue cases (an ambiguous location, an archived owner's location in use, an asset id in use, a send that fails). An asset is addressed by its database and asset ids, so an auto-create never reuses an id its target database holds, live or archived: `create_new_asset` reads that database's two partitions (`database_asset_id_rows`, consistent, fails closed with `RecordRedrive`) and bucket sync rejects the record (`assetIdInUse`) instead. An asset with the id in another database does not stop the create. The first folder names the live asset of the registration whose stored location holds the key (`lookup_asset_for_key`: the first `BucketIdGSI` row when it holds the key, otherwise every live row of the id in the registration, the deepest holder; two at one location are rejected `ambiguousLocation`). Tests: `tests/handlers/indexing/test_sqsBucketSync_asset_id_in_use.py`.

**One base-prefix normalizer.** A registration's `baseAssetsPrefix` (spelled `assets`, `assets/`, `/assets/`, or `/` and `''` for the root) is normalized only by `common.assetLocations.normalize_base_prefix` (trailing `/`, no leading `/`, repeated end slashes dropped, `''` for the root); `derive_location` builds a new asset's folder with it, and `legacy_location_in_key` finds a legacy row's folder (no stored location) in one of its keys. Module helpers that keep their name delegate to it (`executionRecords.normalize_base_prefix`, `fileEnumeration.normalize_base_prefix`, `osFileIndexer.normalize_bucket_prefix`). `tests/common/test_base_prefix_normalizer_guard.py` fails on any read of `baseAssetsPrefix` that is neither passed to the normalizer nor assigned to a name the same function normalizes, unless it is in its reviewed list with the reason.

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

**Resolution order:** `get_table_name(ResourceKeys.*)` first checks for legacy env-var overrides (e.g. `ASSET_STORAGE_TABLE_NAME`), then a 60-minute in-module cache, then a short-lived negative record of keys a completed sweep did not carry, so an unpublished parameter costs one sweep per window rather than one per call, then refreshes the cache: it reads the resource-name chunk parameters (`<prefix>Chunks/0` .. `/<n-1>`, every registered name as JSON) with one `GetParameters` call per 10 chunks, taking `n` from chunk 0, and falls back to one paginated `GetParametersByPath` sweep of the prefix, logged once, when the chunks cannot be used (chunk 0 absent, a chunk below `n` missing, chunks of different `v`, `n` or layout id `l`, unreadable JSON, or a non-retryable error such as `AccessDeniedException`). A chunk numbered `n` or above is ignored. A key the chunks do not carry costs one `GetParameter`, cached; `ParameterNotFound` goes into the negative record. Only a sweep shows that a key is unpublished (`_cache_complete`). Pytest and local utilities can inject names as env vars while deployed handlers use SSM. **Pipeline handlers** in `backendPipelines/` still use legacy env vars and do not call `get_table_name()`.

**A refresh is bounded, and a failed one backs off.** The chunk read and a fallback sweep share one `SWEEP_BUDGET_SECONDS` budget and retry the same errors the same way; a chunk read still throttled when the budget runs out fails the refresh without falling back. A single-key `GetParameter` that follows a refresh runs on what remains of the refresh's budget, so one lookup spends one budget; when the refresh's last call ended past the budget, it still gets one call, bounded by the client's timeouts and not retried (`_call_within_budget(..., first_call_past_deadline=True)`); on a current chunk-loaded cache it has a budget of its own. Sweeps run one at a time per container (a lock taken only when a lookup needs a sweep, so a cache hit never waits) and each finishes or fails inside `SWEEP_BUDGET_SECONDS` (4 s): the page loop retries throttling, 5xx and the transport failures botocore's standard retry mode treats as transient (its `ConnectionError` and `HTTPClientError`: timeouts, a closed or unreachable connection, TLS and proxy failures) from the page's own `NextToken` with jittered sleeps that never pass the budget, and any other error ends the sweep at once. A refresh that failed on those retryable errors opens a back-off window (about 2 s, doubling up to 30 s, jittered) in which no sweep starts: a lookup is answered from the cache, stale or not, or refused with `ResourceNameUnavailableError` (a `RuntimeError`, never a `KeyError`, since the parameter may well exist). A failed refresh for a name the cache does not hold raises the same error, with the SSM error as its cause. A refresh that any other error ended (`AccessDeniedException` on the sweep, for one; a non-retryable error on the chunk read only makes the refresh fall back to the sweep) opens no window and, for a name the cache does not hold, raises `ResourceNameAccessError`, a separate `RuntimeError` that no workflow state waits out. Its message carries the error code; for an `AccessDenied*` code it also names the missing `ssm:GetParametersByPath` permission, and for any other (`ValidationException`, `UnrecognizedClientException`, missing credentials) it makes no IAM claim. A failed single-key `GetParameter` is split the same way: a retryable failure opens the window and raises `ResourceNameUnavailableError`, and any other opens none and raises `ResourceNameAccessError`, whose message for an `AccessDenied*` code names `ssm:GetParameter` on `<prefix>/<key>`. A successful one resets the failure count. An unset `VAMS_RESOURCE_PARAM_PREFIX` still raises `KeyError` and opens no window. Each sweep logs one line with its counts and timings, and the refused lookups of a window log one warning.

**The deploy-time values go through `common/deployTimeValues.py`.** The OpenSearch endpoint and index names, the deployed web URL and the Amazon Location Service map API key ARN are published under the prefix and in the chunks by the stacks that create them, each only when its feature is deployed (`DeployTimeKeys`, mirroring the `openSearch`, `web` and `location` categories of `infra/common/resourceParamKeys.ts`; none takes an environment override, because the `*_SSM_PARAM` variables hold parameter names, never values). `resolve(key, legacy_parameter_name, legacy_ttl_seconds=...)` answers from `get_resource_name()` and, only on a `KeyError` (the deployment that adds the keys, while its stacks update), reads the parameter that held the value before through `get_parameter_value()`, logging one INFO line per key per container; `resolve_together(keys, legacy_names)` does the same for values one stack publishes together, all from the resource names or all from the legacy parameters, with one missed lookup rather than one per key. Every other error propagates, so a throttled lookup adds no second read. Search's `opensearch_settings()`, the indexers' `load_opensearch_settings()` and `configService.py` (legacy TTL 300 s) use them. `configService.py` makes no lookup for a value whose feature is not deployed: the builder sets `WEB_DEPLOYED_URL_SSM_PARAM` only when a web distribution (CloudFront or an ALB, `Config.webDistributionEnabled()`) publishes the web URL, and leaves `LOCATION_SERVICE_URL_FORMAT` empty with Amazon Location Service off; an unset or empty variable answers "" with no Parameter Store call. The copy engine's name stays `ENGINE_FUNCTION_RESOURCE_KEY` in `common/largeObjectCopy.py`, now carried by the chunks too.

**Other Parameter Store values go through `common/ssmParameters.py`.** A resource name VAMS creates at deploy time is resolved with `get_resource_name()`: the chunks carry the registry names, the copy engine's name and the deploy-time values (through `common/deployTimeValues.py` above), and a late-bound key they do not carry (`LATE_BOUND_KEYS` in `infra/test/storage/resourceNameChunks.test.ts`, tooling-only names such as `lambdaFunctions/crOsReindexer`) is read with one `GetParameter` through the same resolver. A parameter read by its full name (from an environment variable the builder sets, such as `OPENSEARCH_ENDPOINT_SSM_PARAM` or `WEB_DEPLOYED_URL_SSM_PARAM`, on the legacy path above) is read with `get_parameter_value(name, ttl_seconds=...)`: String parameters only, never decrypted (secrets come from Secrets Manager). It follows the resolver's rules: each read is bounded by `PARAMETER_READ_BUDGET_SECONDS` (4 s) with the resolver's error classification (it imports `_is_retryable` and `_error_code`), a transient failure opens one back-off window for the container in which a cached value is served, stale or not, and an uncached one is refused with `ParameterUnavailableError` without a call; any other refusal raises `ParameterReadError` at once and opens no window; `ParameterNotFound` raises `ParameterNotFoundError` (a `KeyError`), remembered for 300 s. A value is cached per container for its TTL (default 3600 s; `configService.py` uses 300 s on the legacy path), a cache hit takes no lock and makes no call, and one read runs at a time. `read_parameter_value(name)` reads now, without the cache, for a value that must be current. Read on first use, never at import: search's `opensearch_settings()` and the indexers' `load_opensearch_settings()` (called before a batch is handled, so a refused read fails the whole batch with nothing written) are the pattern. A handler, in any lane, never builds an SSM client and never calls a Parameter Store operation, on its own client or one passed to it: resource names through `common.resourceNames`, deploy-time values through `common.deployTimeValues`, any other parameter (an AWS-published or add-on-owned one included, such as the EDH add-on's) through `common.ssmParameters`. `tests/common/test_shared_client_retry_config.py` enforces it over `backend/backend`, `backendPipelines/` and `backendAddons/`, and names each exception with its reason: the two deploy-time custom resources (`PARAMETER_STORE_DEPLOY_TIME_MODULES`, with the operations each may call) and the Systems Manager clients for other operations, such as Run Command (`SYSTEMS_MANAGER_CLIENT_MODULES`). Tests give the helper a fake client with `tests/ssmParametersStub.py`, and count a cold start's calls with the chunks published with `tests/resourceNameChunksStub.py`.

### Common Environment Variables

`VAMS_RESOURCE_PARAM_PREFIX` (required, non-pipeline handlers): SSM parameter prefix for resource-name resolution. `AWS_REGION` (auto, set by Lambda runtime). `COGNITO_AUTH_ENABLED` (authorizer Lambda only): whether the Cognito MFA-preference check is reachable. `JWT_ISSUER_URL`, `JWT_AUDIENCE`, `JWT_AUDIENCE_CLAIMS` and `JWT_USER_ID_CLAIM` (authorizer Lambda, external OAuth mode only): the issuer and audience the external token is verified against, the claims compared with the audience (JSON array, default `["aud"]`), and the claim whose value is the user id (`""` = `cognito:username` → `username` → `sub`); a malformed claim setting refuses every external token with a 403, and a user id claim under the reserved `vams:` prefix is malformed. The authorizer passes the resolved user id to handlers as `vams:tokens` in every JWT mode. Handler-specific vars like `SEND_EMAIL_FUNCTION_NAME` are read directly from `os.environ`. `WORKFLOW_SERVICE_V2_FUNCTION_NAME` is set on `pipelineService`, `executeWorkflow` and `importGlobalPipelineWorkflow`; the first two read it with `os.environ.get` and degrade (the refresh is not scheduled; a stale launch returns `409`) when it is absent.

Compliance-related handler-specific vars: `EXECUTE_WORKFLOW_FUNCTION_NAME` (the evaluate-service, cascade-service and trigger Lambdas — read by `complianceEvaluationStore` to launch a pipeline rule's workflow execution through a `lambdaCrossCall`); `COMPLIANCE_TRIGGER_QUEUE_URL` (the trigger — its own queue, on which it holds an upload completion's record back with ChangeMessageVisibility until the completion is `UPLOAD_COMPLETION_SETTLE_SECONDS` old, so the requests of one multi-request upload are evaluated once; unset, completions are evaluated at once. Also set on `complianceSchemaBindingService`, which queues an asset override's `{databaseId, assetId}` evaluation under `complianceAutoEval`, and on `complianceJobs`, which queues a database binding change's evaluations there, paced by the apply record's own counts of evaluations sent and taken, never by the queue's approximate message count; unset, nothing is queued); `COMPLIANCE_JOB_QUEUE_URL` (the compliance job queue: `databaseService` queues a delete's `moveDeletedDatabaseCompliance` message, `complianceSchemaBindingService` a binding change's first `applyDatabaseBinding` message, and `complianceJobs` each continuation; `complianceTrigger` does not have it: a job message found on its own queue is logged with one WARNING and dropped, `drop_job_message`); `COMPLIANCE_JOB_DLQ_URL` (the job queue's dead-letter queue, from which a redrive resumes a move: `complianceJobs` puts a move stopped at its run limit there, and `databaseService` a delete's move message the job queue did not take); `COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD` (seven Lambdas: the five asset download Lambdas `downloadAsset`, `streamAsset`, `assetExportService`, `streamAuxiliaryPreviewAsset` and `zipDownloadService`, plus `assetFiles`, which checks a file copy to another asset or database, and `executeWorkflow`, which checks the input files of a run a caller starts or re-runs but not a `SYSTEM_USER` cross-call; set from `app.compliance.quarantineBlocksDownload`; read by the shared `common/compliance/quarantineGuard.py`, which resolves the compliance asset-state table only when it is `true` and whose `check_quarantine_block()` the handlers call only after Tier-1 + Tier-2 authorization); `ORCHESTRATION_BUS_ARN` + `ORCHESTRATION_EVENT_SOURCE_PREFIX` (set on the workflow Lambdas by `workflowFunctions.ts`; `sfn/processWorkflowExecutionOutput`, `sfn/handleExecutionError` and `executionService` publish the `workflow.execution.completed` event only when both are set). The compliance Lambdas receive **no** table-name env vars: every table resolves through SSM (`ResourceKeys.COMPLIANCE_*_STORAGE_TABLE`; legacy override `COMPLIANCE_*_STORAGE_TABLE_NAME`), and the workflow callback resolves its output locations from the V2 execution records rather than a bucket env var.

`PRESIGNED_URL_TIMEOUT_SECONDS` (S3 presigned URL TTL) is **not** set for every handler — only `infra/lib/lambdaBuilder/assetFunctions.ts` sets it, covering the five asset handlers that mint presigned URLs (`downloadAsset`, `streamAsset`, `streamAuxiliaryPreviewAsset`, `uploadFile`, `assetExportService`). All five index it (`os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]`) at module level, so a handler built by any other lambda builder that copies that idiom raises `KeyError` during module import and returns `500` on every request from cold start. Add the variable to the handler's own builder before reading it.

**Legacy env-var overrides** (for pipeline handlers and testing): `ASSET_STORAGE_TABLE_NAME`, `DATABASE_STORAGE_TABLE_NAME`, `S3_ASSET_AUXILIARY_BUCKET`, `AUDIT_LOG_*`, etc. Non-pipeline handlers resolve these via SSM unless the legacy env var is explicitly set.

**Vector-search handler variables** (`handlers/osVectorSearch/`, all indexed with `os.environ[...]` at module level, so each is set only by the builder named — copying the read into a handler built elsewhere raises `KeyError` at import): `VECTOR_INDEX_NAME`, `EMBEDDING_MODEL_ID` (`app.pipelines.useSystemGenAiMetadata.bedrockModels.embeddingModelId`) and `EMBEDDING_DIMENSIONS` (`app.pipelines.useSystemGenAiMetadata.bedrockModels.embeddingDimensions`, read with `int()`) come from `vectorIndexEnvironment()` in `infra/lib/lambdaBuilder/osVectorSearchFunctions.ts` on `vectorIndexer`, `vectorReindexer` and `vectorSearchService`; `VECTOR_INDEXER_QUEUE_URL` (`vectorIndexer`) is the indexer's own SQS queue, and `VECTOR_INDEXER_DLQ_URL` (`vectorIndexer`) its dead-letter queue, where a continuation past `MAX_CONTINUATION_HOPS` is parked. `GENAI_METADATA_WORKFLOW_ID` and `GENAI_METADATA_WORKFLOW_DATABASE_ID` name the SYSTEM GenAI metadata workflow the reindexer re-launches (the shared literals of `infra/common/systemPipelines.ts`, `SYSTEM_GENAI_METADATA_WORKFLOW_ID` / `SYSTEM_WORKFLOW_DATABASE_ID`) and are set on `vectorReindexer` and `systemWorkflowLauncher` by `vectorIndexing-construct.ts`; `WORKFLOW_LAUNCH_QUEUE_URL` (`vectorReindexer`, `vectorIndexer`) is the launch queue the reindexer enqueues one message per file version onto, and onto which the indexer queues a restored file that has no vector item, and `EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME` (`systemWorkflowLauncher`) is the function that queue's consumer cross-calls. The search Lambda also carries `OPENSEARCH_DISABLED` (`"true"` when no OpenSearch engine is deployed — `/search/nlp` then skips its OpenSearch enrichment step and reports OpenSearch-only filter fields under an `opensearch:fields_ignored` warning) alongside the `OPENSEARCH_*` variables the keyword search Lambda uses.

---

## File Security

Uploads must be validated against **both** `UNALLOWED_FILE_EXTENSION_LIST` (`.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat`) and `UNALLOWED_MIME_LIST` (Java archives, MS-executable, shell/JS/PowerShell/VBScript, etc.). Both lists are built in `common/constants.py` from the core lists plus the add-on (`common/addonConstants.py`) and fork (`custom/customConstants.py`) lanes, append-only; extend the core list there or a lane list, never bypass or remove an entry. The one helper that applies them, `common/s3.py` `validateUnallowedFileExtensionAndContentType`, compares case-insensitively: the file extension and the media-type part of the content type (parameters such as `; charset=binary` stripped) are lower-cased before the membership test, so `.EXE` and `Application/X-Msdownload` are rejected like their lower-case forms. The extension is **everything from the last dot of the key's final non-empty segment, read after every trailing dot and whitespace character is removed** (`str.isspace`, so a non-ASCII space counts): `payload.exe.`, `payload.exe `, `.exe`, `..exe` and `payload.exe/` are all checked as `.exe`, while `.gitignore` is checked as `.gitignore` and stays allowed; because the trailing `/` is ignored, a folder whose own name ends in a blocked extension (`builds.exe/`) is refused for prefix download. The segment is split on `:` and every colon-separated piece of the name is checked against the list, each read after its own trailing dots and whitespace are removed and lower-cased (on NTFS a `:` opens a stream name, so `payload.exe::$DATA` and `payload.exe:evil` write `payload.exe`; a browser that cannot write `:` substitutes it and keeps the whole name, so `a:b.exe` lands as `a_b.exe` — both shapes, and `x:payload.exe:evil`, are checked as `.exe`), while a `:` in an ordinary name (`notes:v2.txt`) is still accepted by the helper for an object already in a bucket. The request models complement this on the paths that CREATE a key: `models/assetsV3.py` `validate_asset_file_path(..., new_object=True)` refuses a final path segment ending in a dot or whitespace for upload initialize/completion `relativeKey` and move/copy `destinationPath` (Windows drops that character on download, so `payload.exe.` would land as `payload.exe`), and `file_only=True` additionally refuses a `relativeKey` ending in `/` on the three upload paths (a file key is never a folder; `CreateFolderRequestModel` keeps its own trailing-slash rule); the read paths — download `key`, file info, archive, move/copy `sourcePath` — set neither flag so an object already stored under such a key stays reachable. `new_object=True` also refuses a `:` anywhere in the value, on every created-key path including `CreateFolderRequestModel` (Windows forbids it in any path segment, so no legitimate name is lost), while the read paths keep accepting it. Keep every list entry lower-case and listed once — `tests/common/test_s3_blocklist_case_insensitive.py` fails on an entry of the merged lists that is not (a lane entry that is not is refused at import first); `tests/common/test_s3_blocklist_trailing_dot.py` and `tests/models/test_assetsV3_trailing_dot_write_paths.py` pin the basename rule and the write-path refusal, `tests/common/test_s3_blocklist_ads_suffix.py` and `tests/models/test_assetsV3_ads_suffix_write_paths.py` pin the per-piece `:` check and the created-key `:` refusal, and `tests/models/test_validate_asset_file_path_call_sites.py` fails on a `validate_asset_file_path` call whose field neither passes `new_object=True` nor is declared a read path.

Move, rename and copy (`handlers/assets/assetFiles.py` `move_file` / `copy_file`) run the same helper on the resolved destination key, with an empty content type, right after the key-length check and before any S3 call, and refuse a blocked name with a `400`; `tests/handlers/assets/test_assetFiles_destination_extension_check.py` pins it.

Upload completion (`handlers/assets/uploadFile.py` `complete_upload` / `complete_external_upload`) runs the name-level blocklist (and the preview-extension rule) on every object it creates — zero-byte and regular multipart alike — before anything is written (a multipart file completed with no parts is a cancelled file: its multipart upload is aborted, nothing is stored, and its result is `success=False`; `sqsUploadFileLarge.py` `complete_multipart_upload_for_large_file` does the same and returns `False`), and accepts only the `relativeKey`s the initialize step recorded on the upload record (`AssetUploadTableModel.files`); a record without that attribute predates the binding and is completed with the blocklist checks but without the membership check, logged as a warning, until it expires: `complete_upload` refuses a record whose `expiresAt` (epoch seconds; 7 days from initialize, 1 day for workflow-output records) has passed, and refuses an external (`isExternalUpload`) record outright — the mirror of the check `complete_external_upload` makes — so the one record class designed without the list cannot be completed by the route that skips membership. `AssetUploadsStorageTable` has a DynamoDB TTL on `expiresAt`, which removes an expired record some time after it expires; until then this check is what refuses it.

The external (workflow-output) completion runs once per upload id. `claim_external_completion` moves the record from `initialized` to `processing` under a condition; the call that loses it runs nothing and `answer_repeated_external_completion` answers with the first call's stored result (`completionStatusCode` + zlib-compressed `completionResult`, at most 200 KB), polling while the first still runs and answering 409 when it does not finish within the invocation or stored no result. The record is therefore KEPT once completed (`completed` / `completed_with_errors` / `processing_async`, or `failed` when the completion raised) until the TTL removes it; `complete_upload` still deletes its own records. The caller, `processWorkflowExecutionOutput.py`, sends at most `MAX_FILES_PER_UPLOAD_REQUEST` (1,000) files per call (`_completion_chunks`, a `.previewFile.` in the same call as its base file, one upload record per call) and invokes the completion and the metadata service through `invoke_client`: no botocore retry and a 910 s read timeout, above the invoked functions' 900 s timeout; only a `TooManyRequestsException` is retried. Do not route a RequestResponse cross-call through the retrying `client` (Event invokes only).

The asynchronous >1GB completion (`sqsUploadFileLarge.py`, on `LargeFileProcessingQueue`: batchSize 1, maxReceiveCount 3, a dead-letter queue) splits failures in two. A rejection no retry can change (blocked type or preview extension, cancelled or S3-rejected multipart upload, upload ID mismatch, oversized preview) returns `False`: `process_large_file` aborts the multipart upload, deletes the staged object from its source bucket (`sourceBucketName`, else `bucketName`), writes a denied `log_file_upload` entry, and the message is consumed. Any other failure (S3 5xx, throttling, a failed copy or read) is raised, `lambda_handler` re-raises after its loop, and SQS redelivers and finally dead-letters the message; the multipart upload and staged object are kept so a later receive can finish the file (a `NoSuchUpload` with the staged object present means an earlier receive completed it), and only the final receive (`ApproximateReceiveCount >= LARGE_FILE_MAX_RECEIVE_COUNT`) aborts the multipart upload. Keep `LARGE_FILE_MAX_RECEIVE_COUNT` equal to the queue's maxReceiveCount in `apiBuilder-nestedStack.ts`. Helpers on this path must raise rather than return `False` for a transient error. A duplicate delivery (SQS is at-least-once) of a file an earlier receive stored finds the staged object gone; `is_large_file_already_stored` then checks the final object (`finalS3Key`) for this file's asset, database and change provenance and a `LastModified` no earlier than the message's `SentTimestamp`, and on a match `LargeFileAlreadyProcessed` is raised and `lambda_handler` consumes the message with no cleanup and no audit entry. An older version at the same key, other provenance, or a missing `SentTimestamp` is not a match, so a vanished upload is still rejected and audited as denied. What a dead-letter redrive can recover: a file whose multipart upload completed before the last receive (for example after a failed copy), from the kept staged object; it cannot finish a file whose multipart upload never completed, because the final receive aborted it, and that redrive is rejected (the file must be uploaded again).

### Choosing a file's `.previewFile.<ext>`

A file can hold several `<file>.previewFile.<ext>` objects (a pipeline re-run in another format writes a `.png` beside an existing `.gif`; nothing deletes the other). Every path that attaches a file's preview goes through `select_newest_preview_file((key, lastModified, isArchived) …)`: in `handlers/assets/assetFiles.py` the current listing, the version-snapshot listing in basic and detailed mode, and the single-file info that set `previewFile`, in `handlers/assets/assetExportService.py` the export's per-file preview path, and in `handlers/osVectorSearch/vectorIndexer.py` `find_preview_file_key`, the preview key a vector item carries. Natural-language search's OpenSearch step keeps the file index's preview key over the vector item's. A live preview beats an archived one, then the latest `lastModified` wins, ties keep key order. Do not pick "the first in key order" in a new path. Tests: `tests/handlers/assets/test_assetFiles_newest_preview.py`, `tests/handlers/assets/test_assetExportService_newest_preview.py`, `tests/handlers/osVectorSearch/test_vectorIndexer_embedding_ready.py`, and `TestPreviewKey` in `tests/handlers/osVectorSearch/test_vectorSearchService_final_review.py`.

---

## Feature-Gated Families Do Not Import Each Other

A capability that a deployment can switch off wholesale is a **family**: its handlers, its `common/` package, and its models, all absent from a deployment with the flag off. Two exist today — the OpenSearch semantic search family (`handlers/osSemanticSearch`: the keyword `/search` route plus the `osFileIndexer` / `osAssetIndexer` stream consumers, on the OpenSearch mode flags) and the vector family (`handlers/osVectorSearch`, `common/vectorsearch`, `models/vectorsearch`, on `app.vectorSearch.enabled`). Between them sits `handlers/indexing`, the **core** that runs in every deployment: `sqsBucketSync`, the `snsQueuing` shims, and `crReindexer`, which re-writes rows so every indexer family's stream consumers re-process them. Either family can be deployed without the other, so a module-level import across a boundary fails the surviving code at cold start — a `500` on every request that synth cannot see and a test suite importing everything never reaches.

The rules a family follows, each of which the vector family models:

-   **It owns its packages and imports only `common/`.** Shared behaviour both families need is extracted into a neutral `common/` module rather than imported from either family: `common/indexing/documentIds.py` and `fileEnumeration.py` ("what is an asset file", defined once for the core reindexer and the vector reindexer), `common/databaseAccess.py`.
-   **Core never imports it, and it never imports core.** Core touches a family through the registry seams every table and route already uses — a `ResourceKeys` constant, an `ApiRoute`, a feature switch — and through events: `sqsBucketSync` publishes to the storage stack's SNS fan-out topics that both families' queues subscribe to, and the pipeline publishes `vector.embedding.ready` on the orchestration bus for the vector indexer to consume, so no side imports another.
-   **A cross-group read at runtime is function-scoped and gated.** Two are admitted: `vectorSearchService._opensearch_module` imports `handlers.osSemanticSearch.search` inside the function, which its callers reach only after `OPENSEARCH_DISABLED` is ruled out, for keyword search's API deadline (`_api_deadline`, taken when the handler starts; every OpenSearch query of the step waits at most the time it leaves, on a client built once per container with no retry) and to constrain and enrich hits in `_opensearch_step` when both families are on: a file answer on the file index, and an asset answer on the asset index alone, where `_asset_index_step` applies the OpenSearch-only constraints built for the asset index (as keyword search does) and reads each returned asset's location through the same client, so it is still one crossing; and `crReindexer.ReindexUtility.clear_opensearch_indexes` imports `opensearchpy` inside the method, so the core reindexer's row-rewriting path never loads the client where OpenSearch is off. Both are named, with a site count, in `tests/common/test_indexer_families_import_boundary.py`, which walks the tree and fails on a module-level import in any direction, an unlisted function-scoped one, or an exemption whose site no longer exists. The CDK half of the same boundary — no cross-gated function, no cross-wired queue — is `infra/test/platform/t1VectorIndexingWiring.test.ts`.

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

    **S3 Transfer Acceleration is decided in `common.s3`, never in a handler.** A bucket row with `isAccelerated: true` (BOOL, a missing attribute is false) makes the router sign `get_object`, `upload_part` and `put_object` presigned URLs for `<bucket>.s3-accelerate.amazonaws.com`, in the commercial partition (`aws`) only: `_accelerated_presign` reads the partition from the ordinary client's `meta.partition` and ignores the flag everywhere else (a warning is logged once per cache window). Every other operation (head, get, put, list, copy, paginators, `generate_presigned_post`) keeps the ordinary client, and a bucket without the flag signs the same path-style URL as before. The accelerated presign client is `s3_accelerated_client_for_region(region)`: `S3_ASSET_CLIENT_CONFIG` plus `use_accelerate_endpoint`, one per Region, so the credential scope is the bucket's Region. An accelerate problem never fails a request: a name with a period, a name botocore cannot virtual-host (legacy underscore names, 1-2 characters), `AWS_USE_FIPS_ENDPOINT=true`, or any error building or signing with the accelerated client is logged once per bucket per cache window with the error class, and the ordinary client's URL is returned. `bucket_is_accelerated(name)` reads the row through `bucketNameGSI` and caches it for `BUCKET_ACCELERATE_CACHE_TTL_SECONDS` (600 s, so a toggle reaches warm Lambdas; the Region cache never expires). A failed read is logged once per window, keeps the last known value (false when there is none) and is retried after `BUCKET_ACCELERATE_FAILURE_TTL_SECONDS` (30 s). `bucket_region_fields(row)` registers the flag and adds `isAccelerated` to the details only when it is true. The auxiliary bucket has no row and follows the effective default asset bucket (the `isDefault` row, possibly external); the artefacts bucket and upload staging buckets are never accelerated. Server-side consumers (Lambda, Step Functions, Batch) never presign. `tests/common/test_s3_transfer_acceleration.py` covers it, asserts the mock parity, and holds a ratchet: every module under `backend/backend` that calls `generate_presigned_url` must get its client from `region_routing_s3_client()`. The infra half sets the row flag: the table populator writes `isAccelerated` (only when true) and fails the deploy when an external bucket is flagged but not `Enabled`; the storage stack enables `transferAcceleration` on the created and auxiliary buckets (see `infra/CLAUDE.md`, `documentation/docusaurus-site/docs/deployment/s3-transfer-acceleration.md`).

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

| What a fork adds                                   | Fork-owned file                                                       | Spliced in by                                                                                      |
| -------------------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| API routes                                         | `custom/customApiRoutes.py` (`CUSTOM_ROUTES`)                         | `common/apiRoutes.py` → `ALL_API_ROUTES`                                                           |
| Audit actions of those routes                      | `custom/customAuditActions.py` (`CUSTOM_AUDIT_ROUTE_ACTIONS`)         | `common/auditActions.py` → `AUDIT_ROUTE_ACTIONS`                                                   |
| Audit log groups                                   | `custom/customAuditActions.py` (`CUSTOM_AUDIT_LOG_GROUPS`, optional)  | `common/auditLogGroups.py` → `AUDIT_LOG_GROUPS` (needs its `ResourceKeys` entry and CDK log group) |
| Resource-name keys                                 | `custom/customResourceNames.py` (`CustomResourceKeys`)                | resolved with `get_resource_name()` like a core key                                                |
| Handlers                                           | `handlers/custom/<name>.py` (`handlers.custom.<name>.lambda_handler`) | the shared `backend/backend` code asset                                                            |
| Models                                             | `models/custom/<name>.py`                                             | imported by the fork's handlers                                                                    |
| Object types, constraint fields, deny-list entries | `custom/customConstants.py`                                           | `common/constants.py` (merged at import)                                                           |
| Log redaction keys                                 | `custom/customConstants.py` (`CUSTOM_LOG_SENSITIVE_KEYS`)             | `common/constants.py` → `customLogging/logger.py`, `common/logRedaction.py`                        |

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

Runtime: `aws-lambda-powertools` 2.36.0 (Logger, Parser, BaseModel, typing), `boto3` 1.43.126 / `botocore` 1.43.126 (botocore **≥1.36** is required for the `aws-eusc` EU Sovereign Cloud partition — older releases resolve `eusc-de-east-1` endpoints to the wrong `.amazonaws.com` suffix; botocore **≥1.43.89** is the floor for the DynamoDB `SearchVectors` operation, pinned by `tests/common/test_vector_api_floor.py`), `casbin` 1.36.0 (ABAC/RBAC), `pydantic` 1.10.13 (v1 ONLY), `opensearch-py` 2.7.1, `simpleeval` 1.0.7 (safe expression evaluation in Casbin matchers), `locked-dict` 2023.10.22 (thread-safe Casbin cache).

Dev only: `moto` 5.2.5 (AWS mocks), `pytest` 9.2.0, `mypy` 1.0.0, `flake8` 6.0.0.

---

## Updating Python Dependencies (uv-Managed)

Wherever a `pyproject.toml` sits next to a `requirements*.txt`, the requirements file is a **generated artifact** exported from the project's lock — never edit it by hand. The projects locked with [uv](https://docs.astral.sh/uv/) (`uv.lock`) are `backend/`, `backend/lambdaLayers/base/`, `backend/lambdaLayers/authorizer/`, `backendAddons/edh/rootKeySync/lambdaLayer/`, `backendPipelines/multi/rapidPipelineEKS/lambdaLayer/`, `backendPipelines/conversion/3dBasic/lambdaContainer/`, `backendPipelines/ci/`, `backendPipelines/multi/rapidPipelineEKS/lambda/` and every CPU pipeline container (`backendPipelines/*/*/container/`, `lambdaImage/` of the 3D thumbnail, `containers/blender` and `containers/media` of the GenAI metadata pipeline), plus two GPU images' own requirements: the Cosmos Transfer NCCL wheel (`backendPipelines/genAi/nvidia/cosmos/transfer/container/`) and the Isaac Lab training image (`backendPipelines/simulation/isaacLabTraining/container/`). Six containers install the hashed export under the name `requirements.lock` (zip archive, video SOP BOM, CAD STEP agent, coordinate transform, Potree, Isaac Lab training), and Cosmos Transfer installs it as `requirements-nccl.lock`; the audit treats a top-level `requirements*.lock` beside a `uv.lock` like a `requirements*.txt`. The 3D Basic conversion image's export keeps the hashes (`pip install --require-hashes`) and its optional STEP input is the `step` dependency group, exported to `requirements-step.txt`; the developer guide's "Add or change a dependency" section (`documentation/docusaurus-site/docs/developer/setup.md`) has the commands. Do not start a new project on any other tool.

uv is a developer and CI tool only. Deploys never run it: layer bundling installs the committed `requirements.txt` with pip. Every `pyproject.toml` pins the uv version it was locked with (`required-version`), so run that version (`uvx --from uv==<version> uv ...`); another version stops with an error instead of rewriting the lock. Each pyproject also sets `exclude-newer = "7 days"`, so a re-lock only picks releases older than seven days; a security fix younger than that is taken with a per-package override in `pyproject.toml` (see the CI audit paragraph below).

To change a dependency version:

1. Edit the constraint in `pyproject.toml` only if the current constraint excludes the target version (exact pins like `"urllib3==2.8.0"` must be edited; ranges like `>=2.12.1,<3.0.0` that already admit the target need no edit).
2. Re-resolve the lock: `uv lock --upgrade-package <package>` (add one `--upgrade-package` per package; edit the constraint first when it pins another version).
3. Re-export the requirements file(s) from the project directory:

    ```bash
    # Lambda layers (single requirements.txt):
    uv export --frozen --no-hashes --no-emit-project --no-dev --no-annotate -o requirements.txt

    # backend/ (main and dev):
    uv export --frozen --no-hashes --no-emit-project --no-dev --no-annotate -o requirements.txt
    uv export --frozen --no-hashes --no-emit-project --group dev --no-annotate -o requirements-dev.txt
    ```

4. Commit `pyproject.toml`, `uv.lock` and the exported requirements file(s) together; `uv lock --check` must pass in the project directory, and `backend/tests/test_base_layer_dependency_parity.py` checks that each committed export matches its lock. The layer bundling build never resolves anything: `layerBundlingCommand()` (`infra/lib/helper/lambda.ts`) installs a pinned pip, fails with a message when the layer directory has no `requirements.txt`, and runs `pip install -r requirements.txt`, so a layer ships exactly what its committed export names. `infra/test/security/layerBundlingToolPins.test.ts` asserts pip is pinned and that no resolver runs in the command.

Requirements files without a side-by-side `pyproject.toml` (for example, `backendPipelines/3dRecon/splatToolbox/container/requirements.txt`, which is upstream's) are hand-maintained pip files and are edited directly. Each one is listed in `.github/python-lock-exemptions.json` until it gets a lock.

**CI audit of every lock.** The `python-dependency-audit` job (`.github/workflows/ci.yml`, script `.github/scripts/python_dependency_audit.py`) finds every tracked `uv.lock` with `git ls-files` and, per project, runs `uv lock --check`, regenerates each committed export from the command in its header and fails on a difference, and runs pip-audit on the exports. Any advisory not on `.github/python-audit-allowlist.json` fails the job; an allowlist entry needs `id`, `package`, `projects` (the project directories it applies to, each with a tracked `uv.lock`), a `reason` and a `reviewBy` date; it silences its advisory only in those projects, and the job fails an entry whose date has passed, and fails it for each of its projects where it matches no current finding. Every top-level `requirements*.txt` beside a `uv.lock` must be a uv export (the job fails a file without the header). Fix an advisory by moving the package; a security fix does not wait for the seven-day rule. When the fixed release is younger than seven days, add `exclude-newer-package = { <package> = "<upload time>" }` under `[tool.uv]` in the project's `pyproject.toml`, with a comment naming the advisory and the date to remove it (also list any package the fix needs newer, such as `botocore` for `boto3`), run `uv lock --upgrade-package <package>`, re-export, and confirm `uv lock --check` passes. Never pass `--exclude-newer-package` to `uv lock` on the command line: it is recorded in `uv.lock` only and the next `uv lock --check` fails. Allowlist (with `projects`, `reason` and `reviewBy`) only when no fixed release exists, the fix breaks the pinned runtime, or a vendor base image ships the package and the image's lock keeps the base image's version. Allowlist package names compare PEP 503-normalised. The audit removes each pin's environment marker (`; python_full_version < '3.13'`, `; sys_platform == 'win32'`) from the copy it reads, because pip-audit drops a line whose marker is false for the job's Python without a finding or a warning, and it fails, naming the file, line and package, when a pin of an export is missing from pip-audit's report. The audit does not cover exempt files, Dockerfile-inline `pip install`s, upstream requirements inside GPU images, or base-image packages no lock lists (setup guide, "The CI dependency audit"). Run it locally from the repository root with the pinned uv on `PATH`: `PIP_AUDIT_VERSION=<version in ci.yml> python3 .github/scripts/python_dependency_audit.py`.

**A new layer, Lambda, pipeline container or package gets a uv project.** Add `pyproject.toml` (`requires-python` at the runtime's Python, `[tool.uv]` with `package = false` for a dependency set, the repository's `required-version`, `exclude-newer = "7 days"`, and `environments = ["sys_platform == 'linux'"]` on the deploy path), run `uv lock`, export `requirements.txt` (`requirements.lock` where the Dockerfile installs that name), and commit all three; the audit picks the project up with no workflow edit. `infra/test/ci/pythonDependencyLocks.test.ts` fails any Python dependency file the audit does not cover (a dependency-declaring `pyproject.toml` or top-level `requirements*.txt` or `requirements*.lock` with no `uv.lock` beside it, and anywhere a `Pipfile`, a `setup.py`/`setup.cfg` with `install_requires`, `*.in`, a pip-compile `requirements*.lock` with no `uv.lock` beside it, `constraints*.txt` or a `requirements/` folder) unless `.github/python-lock-exemptions.json` lists it with a `reason` and `plannedIn`, and a `requirements*.txt` or `requirements*.lock` beside a `uv.lock` without the uv export header; it also fails an exemption whose file is gone or already locked, and a locked project whose `required-version` differs from the uv version CI installs.

`backend/` and `backend/lambdaLayers/base/` lock every package they share at the same version: handler Lambdas load their third-party packages from the base layer, while the backend suite installs `backend/requirements-dev.txt`. Change a shared package in both trees in the same change. To match a transitive package to a version other than its newest release, add a temporary exact `constraint-dependencies` entry under `[tool.uv]` in `pyproject.toml`, run `uv lock`, remove the entry, and run `uv lock` again — uv keeps the locked version. `backend/tests/test_base_layer_dependency_parity.py` fails on any difference. The authorizer layer (`backend/lambdaLayers/authorizer/`) is a separate dependency set and is not compared; it loads in the API Gateway authorizer, so a version change there is its own change.

The RapidPipeline EKS Kubernetes layer (`backendPipelines/multi/rapidPipelineEKS/lambdaLayer/`) is attached to the pipeline Lambda together with the base layer, and both extract into `/opt/python`, so a package both layers carry at different versions ends up mixed from two releases. That layer therefore declares no AWS SDK package (`boto3`, `botocore` and `s3transfer` come from the base layer) and locks every other package it shares with the base layer at the base layer's version; re-lock it in the same change that moves a shared package in the base layer. `infra/test/pipelines/eksKubernetesLayerPackages.test.ts` fails on either, and on a function that attaches the Kubernetes layer without the base layer.

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
-   Building a Parameter Store client in a handler, or reading a parameter that is not a resource name at import — use `common.resourceNames` or `common.ssmParameters` ("Other Parameter Store values").
-   Pydantic v2 imports or syntax (`ConfigDict`, `field_validator`, `model_validate`, `model_dump`, or `from pydantic import BaseModel`) — Rules 1 and 2.
-   Resolving resource names inside handler functions — Rule 10.
-   Returning raw error strings (`{'statusCode': 400, 'body': 'bad request'}`) — Rules 7 and 11.
-   Echoing user input or internal details in client error messages — Rule 11.
-   Hardcoding `arn:aws:` in an ARN string or a validator regex — see Partition Portability. Compose from `aws_partition_group` / `aws_dns_suffix_group`, or derive the partition from an ARN already in hand; a commercial-only pattern fails only in GovCloud / EU Sovereign.
-   Passing `endpoint_url` or a hardcoded hostname to a boto3 client — see Partition Portability. Let the SDK resolve per region; read service endpoints from SSM.

`PREVIEW_FILE_SIZE_LIMIT_BYTES` (5 MiB) and `is_self_previewable(name, size)` in `s3PathPatterns.py` are the one backend definition of "the web shows this file as its own preview": an allowed preview extension (`.webp` and `.bmp` included, `.tga`/`.tif`/`.tiff` never) and a size STRICTLY under the limit. The upload handlers' `MAX_PREVIEW_FILE_SIZE` takes the same value and still rejects a preview LARGER than it (a preview of exactly 5 MiB can be uploaded). The web (`fileFormats.ts`) and the 3D thumbnail container (`image_utils.MAX_FILE_SIZE_BYTES`) mirror both; drift tests read those files.

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

A delete whose result decides whether records are removed reports a failure rather than an empty result. `assetFiles.delete_s3_prefix_all_versions` raises `S3PrefixDeleteIncomplete`, naming the keys it deleted and the keys it kept, when it cannot list the prefix or a key keeps a version or delete marker; a `NoSuchBucket` listing is empty (the bucket holds nothing), and every other listing error, `AccessDenied` included, is a failure. The asset permanent delete deletes the asset's S3 objects before any record and stops on it: the files that are gone lose their file records (`delete_files_metadata`: per-file reads up to `FILE_RECORDS_PER_FILE_QUERY_LIMIT` files, above that the asset's rows a bounded page at a time, each page deleted before the next is read) and their auxiliary data (`delete_auxiliary_files_of_keys`), and the asset keeps the rest for a retry. The keys a nested asset holds, and every key of a location another live asset shares, are left out of the listing (`nested`), so they are neither deleted nor reported as kept, and the partial cleanup keeps their auxiliary data. A single-file delete lists the file's previews and checks the file's own delete before it removes its previews and auxiliary data, and a preview S3 keeps fails the delete with a message of its own; the file is still found while such a preview remains, so a second delete removes it. The refusal messages say what to check and echo no input (`tests/handlers/assets/test_permanent_delete_s3_failures.py`).

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
