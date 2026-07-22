---
title: Workflow / Pipeline / Execution Overhaul — Phase 2 Plan (backend, API, CDK, use-case pipelines)
description: The V2 pipeline + workflow data model, template/template-tag system, execution API overhaul, CDK vamsSchema ingestion, and use-case pipeline conversion — planning artifact, spans multiple sessions.
---

# Workflow / Pipeline / Execution Overhaul — Phase 2 Plan

> **Status: PLANNING (no implementation yet).** This document is the resumable plan for Phase 2.
> Phase 1 (the execution-side overhaul: manifest contract, template-tag rendering, input existence
> checks, Cosmos3 + all use-case pipelines on the Stage-3 standard) is **complete and merged**. This
> plan builds the V2 pipeline + workflow data model and the template system on top of it.
>
> **Scope of the implementation phase this plan feeds:** backend handlers/models, REST APIs, CDK
> (tables + custom-resource ingestion), and use-case pipeline `vamsSchema` + SFN adjustments only.
> **Web and VAMSCLI are explicitly deferred** — every web/CLI-facing requirement is captured in
> [Deferred to the Web + CLI phases](#deferred-to-the-web--cli-phases), not built now.
>
> See the [Resume checkpoint](#resume-checkpoint) at the bottom for where we are.

## How this plan is organized

1. [Locked decisions (Q/A log)](#locked-decisions-qa-log) — every answered clarifying question.
2. [Guiding principles](#guiding-principles) — separation of concerns, backward-compat, defaults.
3. [Data model — V2 tables](#data-model--v2-tables) — pipeline, workflow, template, execution tables.
4. [Template + template-tag system](#template--template-tag-system).
5. [Workflow system model](#workflow-system-model) — file rules, triggers, output target, metadata.
6. [Pipeline system model](#pipeline-system-model) — system vs system/execution vars, file filters.
7. [Execution overhaul](#execution-overhaul) — new inputs, validator, re-run, delete, group id.
8. [Cross-entity validation](#cross-entity-validation-hard-vs-soft) — the workflow↔pipeline checks.
9. [Metadata file format v2](#metadata-file-format-v2) — grouped-by-asset restructure.
10. [API surface](#api-surface) — new/changed routes + request/response models.
11. [Permissions](#permissions) — new Casbin object attributes.
12. [CDK + vamsSchema ingestion](#cdk--vamsschema-ingestion).
13. [Use-case pipeline conversion](#use-case-pipeline-conversion).
14. [Data migration](#data-migration).
15. [Documentation](#documentation-updates).
16. [Open questions still needing answers](#open-questions--resolved-log--remaining).
17. [Deferred to the Web + CLI phases](#deferred-to-the-web--cli-phases).
18. [Future backlog](#future-backlog).
19. [Requirement → design coverage map](#requirement--design-coverage-map).
20. [Implementation work breakdown](#implementation-work-breakdown).
21. [Resume checkpoint](#resume-checkpoint).

---

## Locked decisions (Q/A log)

Decisions confirmed with the requester (Session 1). These are binding for the implementation phase.

1. **V1→V2 cutover:** New V2 tables (`PipelineStorageTableV2`, `WorkflowStorageTableV2`, plus
   sub-tables), migrate V1 rows in the v2.5→v2.6 script, repoint all handlers to V2. V1 kept
   read-only for migration then abandoned. Mirrors the execution V1→V2 approach.
2. **Template storage (hybrid, S3 transparent to clients — revised Session 1):**
    - **API contract is inline.** Web/CLI **never deal with S3** for templates/config — they always
      send and receive the full `configBody` / `webFormJson` / `customTemplateOverride` JSON in the
      request/response body. The lambda does all S3 work.
    - **Storage is hybrid, decided by the lambda.** Persist `configBody` + `webFormJson` **inline** in
      the DynamoDB row when their combined size ≤ 390 KB; **above 390 KB, offload both to S3** under
      the **default asset bucket** `pipelines/` prefix (see
      [default asset bucket](#default-asset-bucket-for-template--config-s3)) with S3 keys + hashes + a
      `bodyStorage: inline|s3` discriminator on the row. On read the lambda **rehydrates from S3
      transparently** and returns the full inline body to the client. Same on execute output —
      responses/details carry the full body inline (backend fetches from S3 as needed).
    - **Absolute upper cap.** There is still a hard ceiling on the **combined `webFormJson` +
      `configBody`** size (driven by the API Gateway request / Lambda payload limits — target ~**6 MB**
      combined, final number in [open questions](#open-questions--resolved-log--remaining) Q7). The
      **same check is enforced in two places**: the create/update template API and the CDK vamsSchema
      upload. Beyond the cap → reject.
    - **Execute override.** `customTemplateOverride` may exceed 390 KB (up to the same absolute cap);
      it is validated, rendered, then **written to S3** for the per-execution config-tracking record
      (the pipeline already receives its config via S3). See
      [template config storage + override flow](#template-config-storage--override-flow).
3. **Tag-schema type system:** Reuse the **metadata-schema primitive types** (subset: primitives +
   lists, minus specialized XYZ/matrix), adding `required` + `default`. **Commonize the shared
   backend** and clearly document what is reused (see [shared validator](#shared-tag-schema-validator)).
4. **Execution query GSIs:** Purpose-built GSIs **now** — by input asset, by output asset, by
   workflow, by `triggeredByUserId`, by `executionGroupId`, by status+startDate. Category/name
   filtering via a cached workflow-lookup join in the handler. No OpenSearch.
5. **Metadata format:** New **grouped-by-asset** envelope (schemaVersion bumped); update every
   pipeline's metadata read. No back-compat shim (clean cutover, all pipelines redeployed).
6. **Cross-check validator:** Standalone `common/workflows/executionValidation.py`.
7. **Filter-to-empty:** Hard error when a pipeline requires ≥1 file but its allow/exclude filters
   remove all selected inputs.
8. **Edit-time warnings:** Create/Update Workflow returns a non-fatal `warnings[]` array (never
   blocks save); hard conflicts still error at execution.
9. **Execute route:** **Remove** the old asset-scoped `POST /database/{db}/assets/{assetId}/
workflows/{workflowId}` route entirely as part of the refactor; replace with a new asset-less
   execute route taking the multi-file object array + output target + per-pipeline exec params.
10. **New execution APIs (all in this phase):** re-run, permanent-delete (DDB-only), `executionGroupId`
    plumbing + abort-by-group, and global (asset-less) execution list/details/logs/abort.
11. **Permission fields:** Add `category` + `name` to **both** pipeline and workflow Casbin objects,
    in all enforcer/mapping locations. **~~No changes to existing permission templates~~ — SUPERSEDED
    by Session-2 decision S13:** the shipped permission templates (esp. non-admin) **are** updated for
    the new APIs. Two-tier Casbin (action + data) is enforced on every new API.
12. **Disabled vs archived:** Two independent flags — `enabled` (gates execution) + `archived`
    (soft-delete, hidden from default lists). CDK re-register unarchives + re-enables per vamsSchema.
13. **CDK vamsSchema ingest:** CDK uploads `vamsSchema/*.json` to S3 and passes S3 keys to the import
    custom-resource lambda (avoids CFN property / lambda payload size limits). **Must be seamless +
    transparent to the developer running CDK** — no manual upload steps. External callers may pass
    inline JSON or S3 keys.
14. **Shared tag validator:** A shared `common/` module does primitive-type + required/default
    validation, referenced by both template-tag validation and metadata-schema; documented as shared.
15. **Trigger model:** Extensible typed-trigger structure (`triggers[]` keyed by type with per-type
    config); only `fileUpload` implemented now; execution records a `triggerType`.
16. **Plan persistence:** This dedicated doc + a memory pointer with the locked decisions.

### Session-2 decisions (answers to Q1–Q9 + comments 1–9)

These refine or extend the Session-1 decisions above and are equally binding.

-   **S1 — Retain all tables:** ALL `storageResources` DynamoDB tables switch to `RemovalPolicy.RETAIN`
    (auto-named → no redeploy collision; audit for any explicit `tableName` first). See
    [RETAIN on all tables](#retain-on-all-dynamodb-tables-comment-1).
-   **S2 — Composite PK for uniqueness:** `PipelineStorageTableV2` and `WorkflowStorageTableV2` use a
    **composite PK `db:id`** so overridable ids can't collide across databases; template + trigger
    tables share the composite. Workflow `specifiedPipelines` refs store `pipelineDatabaseId +
pipelineId` together.
-   **S3 — `schemaVersion` kept** on pipeline + workflow tables (record-shape version, distinct from
    `aslSchemaVersion`).
-   **S4 — Field renames:** `inputArity` → **`inputFileArity`** everywhere (pipeline + workflow).
-   **S5 — `requireTemplate` consolidation:** drop `allowNoTemplate`; keep only `requireTemplate`
    (its logical inverse).
-   **S6 — `inputInstructions` moves to the template**; templates may **override** the pipeline's
    `inputFileArity`, `metadataInputs`, `assetScope`, `inputFileFilters` (conversion-matrix use case).
-   **S7 — Tag schema is its own table** (`PipelineTemplateTagSchemaTable`); `isTriggerDefault` removed
    from the template (trigger defaults live on `WorkflowTriggersTable`); `configFormat` gains
    `xml` + `raw`; Deadline Cloud (OpenJD/YAML/raw) is fully supported now.
-   **S8 — Triggers via the orchestration EventBridge bus**, filters use full `inputFileFilters`
    (not flat extensions); SQS retained as a buffer target behind EventBridge for high-fan-out uploads
    (confirm volume). See [auto-trigger](#auto-trigger-execution-eventbridge-bus-driven).
-   **S9 — Execution config snapshot:** `PipelineExecutionInputConfigurationStorageTable` snapshots the
    final rendered config (+ override, + tags, + template/tag-schema versions) inline-or-S3-pointer so a
    run is faithfully traceable after templates change.
-   **S10 — Disabled OR archived pipeline in a workflow → execution error**; reserved system tag keys
    can't be user-supplied.
-   **S11 — Pipeline filtering + workflow-save validation implemented now** (Q6); see
    [workflow-save validation](#workflow-save-validation-comment-6--q6).
-   **S12 — Default asset bucket** houses all pipeline template data AND all execution-time run I/O
    under `pipelines/`; forward-only (Q8); `isDefault` flag on external buckets for all-imports (Q9).
-   **S13 — Permission templates updated** for new APIs; detailed logs = admin; any permanent-delete =
    admin; two-tier Casbin on every API. See [permissions](#permissions).
-   **S14 — vamsSchema minimal-required ingestion:** only `pipeline.json` + `workflow.json` required;
    templates/webforms/tag-schemas optional.
-   **S15 — Use-case consolidation:** combine variant pipelines into one pipeline + per-variant
    templates where it fits — primarily file-conversion (drop `outputType`, read `configBody`); AI
    model/instance variants only if a runtime instance×container matrix is feasible, else leave.

---

## Guiding principles

-   **Separation of concerns (hard requirement).** Pipelines, workflows, and executions are three
    self-contained domains with narrow, explicit hand-off contracts — the same isolation Phase 1
    established for the manifest. Cross-logic is limited to: (a) workflow create/update reading
    pipeline configs to compute compatibility warnings + regenerate the SFN when pipelines change;
    (b) the execution-time [cross-entity validator](#cross-entity-validation-hard-vs-soft). No handler
    reaches into another domain's internals beyond these.
-   **V2 tables mirror the execution-table patterns.** Single-entity PK, `db:id` composite SK where
    the entity is database-scoped, GSIs for every non-scan query path, truncate-to-limit for free-form
    text, typed record builders in a pure `common/workflows/` module (no AWS deps) so they unit-test in
    isolation — exactly as `executionRecords.py` does today.
-   **Backward-compatible growth.** V2 record builders take keyword args with defaults; new fields are
    additive; readers tolerate missing fields. This is a system that keeps expanding.
-   **Defaults everywhere.** Creating a use-case pipeline/workflow must stay easy: every system field
    has a sensible default so a minimal `vamsSchema` (or a minimal create request) works. The rich
    option set is opt-in.
-   **GLOBAL + database-scoped** pipelines/workflows preserved exactly as today (reserved `GLOBAL`
    database id; global workflows may only reference global pipelines; database workflows may reference
    both GLOBAL and same-database pipelines).
-   **Never delete workflows/pipelines/executions** — archive (soft-delete) instead; permanent-delete
    is an explicit, guarded, execution-only operation.

---

## Data model — V2 tables

All new tables follow the execution-V2 conventions: `RETAIN` removal policy, KMS-encrypted with the
shared key, PITR on, registered in the `resourceNameRegistry` (three-way SSM constants: `ResourceKeys`
in `resourceNames.py` ↔ `RESOURCE_PARAM_KEYS` in `resourceParamKeys.ts` ↔ `ResourceParamKeys` in the
migration `ssm_resource_lookup.py`), and granted per-lambda.

### Pipeline tables

**`PipelineStorageTableV2`** — one row per pipeline definition.

-   **PK `databaseId`, SK `pipelineId`** (database-scoped, matching the V1 pipeline table + the
    metadata-schema paradigm — one paradigm across VAMS). The (databaseId, pipelineId) pair is unique
    even when `pipelineId` is CDK-overridden to a known value, and "list a database's pipelines" is a
    native Query on the partition (comment 2c/3c: uniqueness solved by the key itself, not a stored
    pair). No hot-partition/10 GB-collection concern: these are low-volume _definition_ rows, on-demand
    billing repartitions transparently, and there are no LSIs (the 10 GB item-collection limit applies
    only to LSI tables). `pipelineId` is a GUID by default; CDK-overridable.
-   Attributes: `pipelineName` (display-only, non-unique), `databaseId`, `databaseId:category` (GSI PK),
    `category`, `description`, `enabled`, `archived`, `dateCreated`, `dateModified`, `createdBy`,
    `modifiedBy`, `schemaVersion`.
-   **`schemaVersion` (comment 2a): YES, keep it.** It is the record-shape version for
    backward-compatible V2-generation growth (a new field/shape bump lets readers/migrations detect old
    rows), distinct from `aslSchemaVersion` (the workflow's deployed SFN definition version). Cheap and
    consistent with the execution tables.
-   **Execution-type config block** (replaces the loose `userProvidedResource` JSON string): a typed
    `executionConfig` map — `{ executionType: Lambda|SQS|EventBridge|DeadlineCloud, waitForCallback,
taskTimeout, taskHeartbeatTimeout, lambda:{resourceId}, sqs:{queueUrl}, eventBridge:{busArn,source,
detailType}, deadlineCloud:{farmId, queueId, storageProfileId, priority, maxRetriesPerTask,
maxFailedTasksCount, templateType} }`. (DeadlineCloud fields align with the Phase-A execution work - [Deadline Cloud creation enablement](./workflow-execution-pipeline-refactor-plan.md#deadline-cloud-creation-enablement-with-the-pipelineworkflow-table-overhaul).)
-   **Pipeline system variables** (`systemConfig` map — admin-only, see
    [pipeline system model](#pipeline-system-model)): `inputFileArity` (none|one|multi),
    `assetScope` (crossAsset|singleAsset|wholeAssetAllowed|folderAllowed flags), `metadataInputs`
    (assetMetadata|fileMetadata|fileAttributes booleans), `requireTemplate` (see
    [requireTemplate semantics](#requiretemplate-and-template-usage-modes)),
    `allowCustomTemplateOverride`, `auxPreviewPipelineSuffix`, `inputFileFilters`
    ({allow:[…], exclude:[…]} of ext/path/name/wildcard). **These are pipeline-level defaults; a
    template may override `inputFileArity`, `metadataInputs`, `assetScope`, and `inputFileFilters`**
    (comment 2e — the conversion-matrix use case). `inputInstructions` moves to the template
    (comment 2d).
-   **Removed vs V1:** `outputType`, `pipelineType` (standardFile/previewFile), and `allowNoTemplate`
    (folded into `requireTemplate`; see below) — no longer needed (aux-preview suffix is passed to every
    pipeline that wants it; output typing moves into template config where a pipeline needs it).
-   GSIs (as built): `PipelinesByDatabaseGSI` (PK `databaseId`, SK `dateModified`),
    `PipelinesByCategoryGSI` (PK `databaseId:category`, SK `pipelineId`). Archived filtering is done in
    the handler (list default = not archived) rather than a dedicated GSI.

**`PipelineTemplatesStorageTable`** — one row per (pipeline, template).

-   PK `pipelineDatabaseId:pipelineId`, SK `templateId` (GUID; unique per pipeline). (This composite PK
    binds a template to its owning database-scoped pipeline unambiguously.)
-   Attributes: `templateName`, `description`, `configBody` (the preloaded config text),
    `configFormat` (**json | yaml | openjd | xml | raw** — comment 2f-i; `raw`/free-text is the basic
    tag-replacement mode for arbitrary formats, incl. OpenJD/YAML for Deadline Cloud),
    `webFormJson` (web form-builder markup, opaque to backend), `allowCustomEdit` (may the final
    rendered config be manually edited at execute), `inputInstructions` (shown-to-user guidance — moved
    here from the pipeline, comment 2d), **per-template system overrides** `inputFileArity`,
    `metadataInputs`, `assetScope`, `inputFileFilters` (optional — when set, override the pipeline
    defaults for executions using this template; comment 2e).
-   **`tagSchema` is its OWN table** (`PipelineTemplateTagSchemaTable`, comment 2f-iii) — tag schemas
    can be extensive and would eat into the record size limit. See below.
-   **`isTriggerDefault` removed** (comment 2f-ii) — the trigger→default-template mapping lives on the
    `WorkflowTriggersTable` per workflow, not on the pipeline template.
-   **Hybrid body storage:** `bodyStorage` (inline|s3); when inline, `configBody`+`webFormJson` live on
    the row (combined ≤ 390 KB); when their combined size exceeds 390 KB the lambda offloads both to the
    **default asset bucket** under `pipelines/` (`configBodyS3Key`, `configBodyHash`, `webFormS3Key`,
    `webFormHash`) and the row stores keys+hashes instead. **Absolute combined cap** (target ~6 MB with
    a **best-practice buffer** reserved for the other row fields incl. the (separate) tag schema —
    [Q7](#open-questions--resolved-log--remaining)) enforced identically at this API and at CDK upload.
    Clients always see the full inline body (lambda rehydrates from S3) — see
    [storage + override flow](#template-config-storage--override-flow) and
    [default asset bucket](#default-asset-bucket-for-template--config-s3).

**`PipelineTemplateTagSchemaTable`** — the template-tag schema, separated so it does not consume the
template row's size budget (comment 2f-iii). **Mirrors the `MetadataSchemaStorageTableV2` paradigm
exactly** (Q10 answer — keep one paradigm across VAMS): **one row per schema, all tag-field
definitions stored inline as a JSON string** (the metadata-schema table stores `fields =
json.dumps(fieldList)` in a single `put_item` per schema — `metadataSchemaService.py`; not row-per-field,
which is only its deprecated legacy table).

-   PK `tagSchemaId` (UUID), SK `pipelineDatabaseId:pipelineId:templateId` (composite owner key) — the
    same PK-UUID + composite-SK shape as `MetadataSchemaStorageTableV2`. Add a GSI on the owner key
    (`TagSchemaByTemplateGSI`, PK `pipelineDatabaseId:pipelineId:templateId`, SK `tagSchemaId`) for the
    fetch-by-template lookup.
-   Attributes: `tagSchemaId`, the owner composite, and `fields` — a **JSON string** holding the array
    of tag definitions `{ tagKey, type, required, default, label, description, + type constraints (enum
values, list item type) }`. Handler `json.loads` on read, `json.dumps` on write, exactly as
    metadata schema does.
-   The `fields` string is still subject to hybrid inline/S3 offload + the shared size cap if it grows
    large (its own row budget, separate from the template config row).

### Workflow tables

**`WorkflowStorageTableV2`** — one row per workflow definition.

-   **PK `databaseId`, SK `workflowId`** (database-scoped, same paradigm/rationale as the pipeline
    table; comment 3c uniqueness solved by the key). `workflowId` is a GUID by default; CDK-overridable.
-   Attributes: `workflowName` (display-only, non-unique), `databaseId`, `databaseId:category` (GSI PK),
    `category`, `description`, `enabled`, `archived`, `workflow_arn`, `aslSchemaVersion`,
    `specifiedPipelines` snapshot (ordered pipeline refs — **each ref stores `pipelineDatabaseId` +
    `pipelineId` together** so the pipeline key resolves unambiguously; comment 3c — plus per-pipeline
    job names, as today), `subDashboardUrl` (optional URL string), `dateCreated`, `dateModified`,
    `createdBy`, `modifiedBy`, `schemaVersion`.
-   **`schemaVersion` (comment 3a): YES, keep it** — same rationale as the pipeline table (record-shape
    version, distinct from `aslSchemaVersion`).
-   **Workflow system variables** (`systemConfig` map — see [workflow system model](#workflow-system-model)):
    `inputFileArity`, `assetScope` flags, `metadataInputs`, `inputFileFilters` ({allow, exclude}),
    `concurrencyRestriction` (none|perAsset|perInputFile), `outputTarget`
    ({locationType: asset, allowOverride: bool}).
-   GSIs (as built): `WorkflowsByDatabaseGSI` (PK `databaseId`, SK `dateModified`),
    `WorkflowsByCategoryGSI` (PK `databaseId:category`, SK `workflowId`); archived filtering in handler.

**`WorkflowTriggersStorageTable`** — one row per (workflow, trigger).

-   PK `workflowDatabaseId:workflowId`, SK `triggerType` (fileUpload today).
-   Attributes: `triggerConfig` (per-type; fileUpload: `{inputFileFilters: {allow:[…], exclude:[…]},
defaultTemplateIds: {"<pipelineDatabaseId>:<pipelineId>": templateId}}`), `enabled`. **The upload
    trigger matches on the full `inputFileFilters` structure — ext/path/name/wildcard — not just a flat
    extension list** (comment 3d, aligns with Q2 free-text/dynamic filter support). The
    `defaultTemplateIds` map is where the per-included-pipeline default template for a trigger lives
    (this is why `isTriggerDefault` was removed from the template row — comment 2f-ii).
-   GSI: `TriggersByTypeGSI` (PK `triggerType`, SK `workflowDatabaseId:workflowId`) so the upload
    auto-trigger dispatcher can find candidate workflows by type without scanning; filter evaluation
    runs against `inputFileFilters` after the candidate set is fetched.

### Execution tables (extend the existing V2 execution tables)

The execution V2 tables from Phase 1 are extended (additive) rather than replaced:

-   **`WorkflowExecutionsStorageTableV2`**: add `executionGroupId`, `triggerType` (already present),
    `outputAssetId`/`outputDatabaseId` on the main row (today on the config row) for GSI keying. New
    GSIs: `ByUserGSI` (PK `triggeredByUserId`, SK `executionStartDate`), `ByGroupGSI`
    (PK `executionGroupId`, SK `executionStartDate`), `ByStatusGSI` (PK `executionStatus`, SK
    `executionStartDate`).
-   **`WorkflowExecutionInputsStorageTable`**: already has `ByAsset` GSI (input side). Add a **secondary
    output-asset index table** (`WorkflowExecutionOutputsIndex`) keyed `databaseId:assetId` →
    executionId so "executions that wrote to this asset" resolves without a scan (Q3 answer: use the
    secondary index table, not a second GSI — keeps the input table's write path clean and mirrors the
    V2 one-purpose-per-table pattern).
-   **`PipelineExecutionInputConfigurationStorageTable`**: extend the record to **snapshot exactly what
    went into the run** (comment 4) so the execution is fully traceable and re-runnable even after the
    source template + tag schema later change or are archived. It stores:
    -   the `templateId` used + `templateSchemaVersion`/`tagSchemaVersion` at run time,
    -   the **template tags passed** (resolved values),
    -   the **final rendered configuration** (the config body actually sent to the pipeline) — combined
        with the `configBodyOverride` when one was supplied,
    -   **the snapshot itself follows the same hybrid inline/S3 rule**: inline when small, else a
        **pointer (`snapshotConfigS3Key` + hash) to the per-execution S3 file already generated** in the
        default bucket under `pipelines/` — the execution already writes the config to S3 to hand to the
        pipeline, so the snapshot reuses that object rather than duplicating it,
    -   `customTemplateOverrideUsed` (bool).
        System/execution-level variables passed are stored on the `WorkflowExecutionConfigurationRecord`
        (workflow-level) and per-pipeline where pipeline-specific.

### Record builders

New pure modules mirroring `common/workflows/executionRecords.py`:

-   `common/workflows/pipelineRecords.py` — pipeline + template row builders.
-   `common/workflows/workflowRecords.py` — workflow + trigger row builders.
    Extend `executionRecords.py` for the new execution fields (group id, final config, template tags).

---

## Template + template-tag system

### Template-tag schema (reuses metadata-schema primitives; stored in its own table)

Each template's tag schema is a list of tag definitions
`{ key, type, required, default, label, description }` where `type` is drawn from the **shared
primitive type set** (string, integer, number, boolean, string-list, enum, …; **not** the specialized
metadata types like XYZ/matrix). **The schema lives in the separate `PipelineTemplateTagSchemaTable`**
(comment 2f-iii) rather than inline on the template row, because it can be lengthy and would otherwise
consume the template row's ~6 MB budget. This pairs with the existing `{{tagName}}` renderer from
Phase 1 (`common/workflows/templateRender.py` + `templateTags.py`): the schema-declared tags are the
user-defined dynamic tags, layered on top of the built-in system tags.

**Reserved system tag keys (comment 5d).** A user-declared tag `key` may **not** collide with any
**system-defined template tag key** (the built-in system inputs established in Phase 1 — the
`templateTags.py` reserved set). The shared validator rejects a `tagSchema` (at template
create/update _and_ at CDK ingestion) that redefines a reserved key, and the execute handler rejects a
provided tag whose key is reserved-but-user-supplied. System tags are resolved by the engine, never by
the caller.

### Shared tag-schema validator

`common/templateTagSchema.py` (shared, documented as reused by metadata-schema where it makes sense):

-   `validate_tags(tag_schema, provided_tags) -> (errors[], filled_tags)` — checks every `required`
    tag is present, fills `default`s, type-checks each value, rejects unknown types.
-   **Rejects reserved system tag keys** in a declared schema and rejects caller-supplied values for
    reserved keys (comment 5d).
-   **Extra provided tags (Q1 answer): ignored, not an error.** Providing a tag with no matching
    `{{tag}}` in the body is silently dropped; the **only** tag error at render is an **unmatched
    `{{tag}}` in the body** with no provided/default value. (Applies to schema-driven and raw modes.)
-   **TODO (implementation):** first read the existing metadata-schema type/validation module
    (`backend/backend/models/` + `handlers/metadata*`) to decide precisely what is extracted vs newly
    written, then commonize. This plan commits to the _shape_; the exact shared surface is a first
    implementation step.

### Execute-time template resolution (the core contract)

Per pipeline in the workflow, the execute request carries a `pipelineExecutionParameters[pipelineId]`
block: `{ templateId?, templateTags: [{key,value}], customTemplateOverride? }`. Resolution:

1. **templateId + tags (no override):** validate tags against the template's tag schema (required +
   types + defaults), render the template's `configBody` with the built-in system tags + provided
   tags → final config. Missing-required and reserved-key violations error; **extra provided tags are
   ignored** (Q1); an unmatched `{{tag}}` in the body errors.
2. **templateId + customTemplateOverride:** allowed only if the pipeline's
   `allowCustomTemplateOverride` is true. Tags still validated against the schema; the override body
   is rendered instead of the stored `configBody`.
3. **customTemplateOverride, no templateId:** allowed only if `allowCustomTemplateOverride` is true
   **and** the pipeline does not `requireTemplate` (see below). Tags taken as-is (string values, no
   schema); every `{{tag}}` present in the override must have a provided tag or default (else error);
   **provided tags with no match in the body are ignored** (Q1 answer — not an error).
4. **No template, no override (pipeline does not `requireTemplate`):** only system/execution variables
   apply; free-form config entry with tag key/values replacing blocks (same engine). This is the mode
   `allowNoTemplate` used to gate — now expressed as `requireTemplate = false`.
5. **allowCustomEdit** (per-template) gates whether the _final_ config may be hand-edited at execute
   (web raw-editor). Backend enforces: a hand-edited final config is only accepted when the resolved
   template allows it.

**`requireTemplate` / template-usage modes (comment 2c — consolidation).** The two V1-era flags
`allowNoTemplate` and `requireTemplate` are logical inverses, so the plan keeps **only
`requireTemplate`** (boolean) on the pipeline:

-   `requireTemplate = true` → every execution of this pipeline **must** name a `templateId`
    (cases 1–2). Template-less and override-only-without-template runs (cases 3–4) are rejected.
-   `requireTemplate = false` → template optional; cases 3 and 4 become available (subject to
    `allowCustomTemplateOverride` for the override path).

All of the above run in a **template-resolution phase** in the execute handler, before launch, and
the resolved final config + tags + templateId are persisted for traceability (see the config-body
snapshot on `PipelineExecutionInputConfigurationStorageTable`).

### Template config storage + override flow

The design goal: **clients (web/CLI) never touch S3** for templates or config; the lambda handles all
S3 offload/rehydration transparently, within the API Gateway / Lambda payload budget.

**Template create/update (API):**

-   Client sends full `configBody` + `webFormJson` inline.
-   Handler validates the **combined size** against the absolute cap ([Q7](#open-questions--resolved-log--remaining));
    reject beyond.
-   If combined ≤ 390 KB → store inline (`bodyStorage=inline`). If > 390 KB → write both to S3
    (**default asset bucket** under `pipelines/`, deterministic key per
    `pipelineDatabaseId/pipelineId/templateId`), store keys+hashes (`bodyStorage=s3`).
-   The tag schema (in `PipelineTemplateTagSchemaTable`) is a separate row/table and is size-checked
    independently; the **absolute combined cap** budget reserves headroom for it and the other row
    fields (Q7 — best-practice buffers).

**Template read (API):**

-   Handler loads the row; if `bodyStorage=s3`, fetches the bodies from S3 and returns them **inline**
    in the response. Client is unaware of S3.

**CDK vamsSchema upload:** the import CR lambda applies the **same combined-size cap check** and the
same inline-vs-S3 decision when upserting templates, so built-in and API-created templates behave
identically.

**Execute-time `customTemplateOverride`:**

-   Client sends the override inline (may exceed 390 KB, up to the absolute cap — the larger
    Lambda/API-GW input budget accommodates it).
-   Handler validates + renders it (tags), then **writes the final rendered config to the default asset
    bucket under `pipelines/…`** as the per-pipeline execution input configuration (the pipeline already
    receives its config via S3 per the Phase-1 manifest contract). The
    `PipelineExecutionInputConfigurationRecord` stores the S3 key (and inline text when small enough,
    truncated flag otherwise) so the execution-details API returns the full config **inline** by
    rehydrating from S3 — again transparent to clients.
-   Same principle for details/logs outputs: any stored-in-S3 config/override is rehydrated by the
    backend and returned inline in the API response.

Net: one absolute size ceiling (enforced at API + CDK), a 390 KB inline/S3 threshold the lambda
manages, and no S3 semantics leak to web/CLI on input or output.

### Default asset bucket for template + config S3

> **Canonical rule:** the **default asset bucket houses ALL future pipeline data** — both the
> **pipeline template data** (offloaded `configBody`/`webFormJson`) and **all execution-time
> input/output/scratch data** for pipeline runs (manifests, per-pipeline config files, the shared
> output folder, aux temp/preview), all under the `pipelines/` prefix. This is the single home for
> VAMS-managed pipeline S3 artifacts going forward.

**Change from today (revised Session 1).** Currently execution-time pipeline I/O (the `pipelines/`
prefix: input manifests, per-pipeline config files, the shared output folder, aux temp/preview) is
written to the **input asset's own bucket** (`workflowExecutionS3InputOutputBucket` = the asset
bucket, threaded through the Phase-1 manifest). Going forward, **all execution-time pipeline-run S3
I/O uses a single "default" asset bucket** rather than the input asset's bucket, and template
`configBody`/`webFormJson` offload writes to that same default bucket under `pipelines/`.

Design:

-   **Default bucket concept in the buckets table.** The `S3AssetBucketsStorageTable` gains an
    `isDefault` flag (exactly one row default). The default is chosen by an **`isDefault` boolean on each
    imported external bucket entry** (`config.app.assetBuckets.externalAssetBuckets[].isDefault`) — not a
    separate id/ARN field. `getConfig()` enforces: **at most one** external may set `isDefault`; when
    `createNewBucket=false` **exactly one** external must set it; when `createNewBucket=true` an external
    marked default **overrides** the created bucket, otherwise the created bucket is the default. The flag
    is threaded through the `s3AssetBuckets` registry to the populate custom resource, which writes
    `isDefault` onto the matching bucket row(s) at deploy — no synth-time ARN/name matching. The backend
    `resolve_default_bucket` helper reads the `isDefault=true` row.
-   **Execution I/O rebased to the default bucket.** The execute handler resolves the default bucket
    and uses it for `workflowExecutionS3InputOutputBucket` (manifests, config files, output folder, aux
    prefixes) instead of the input asset's bucket. **Input files are still read from their own asset
    buckets** (the manifest already carries each input file's own `bucket` per Phase 1 — unchanged);
    only the VAMS-managed run scratch/output/config/manifest area moves to the default bucket.
-   **Ramifications to reconcile (implementation):** the Phase-1 manifest `outputs.bucket`, `auxBucket`,
    and the interim lambda's `wf_exec_bucket` derivation currently assume the input asset bucket. These
    move to the default bucket. Output-target write-back to an asset (fileIngestion) still targets the
    **output asset's** bucket — so there is a distinction between the _run I/O bucket_ (default) and the
    _output-target asset bucket_ (where final files land). The end-state process-output lambda already
    resolves the output asset independently, so this separation is clean; verify the
    `filesPathKey`/`metadataPathKey` prefixes are read from the default run bucket and the final ingest
    targets the output asset bucket.
-   **Template/config offload** writes to `pipelines/` in this same default bucket (consistent home for
    all VAMS-managed pipeline S3 artifacts).
-   **Open item [Q8](#open-questions--resolved-log--remaining):** confirm whether _older_ executions (whose
    run I/O lived in the input asset bucket) need any read-compat, or whether this is forward-only
    (new executions only) — recommended forward-only since run scratch is ephemeral.

---

## Workflow system model

`systemConfig` on the workflow (admin-set at create; some fields are "system/execution" and
overridable at execute — see [system vs execution fields](#system-vs-systemexecution-fields)):

-   **File rules (hard restrictions — error at execution):**
    -   `inputFileArity`: none | one | multi.
    -   `assetScope`: `crossAssetAllowed`, `singleAssetOnly`, `wholeAssetAllowed` (`"/"`),
        `folderAllowed` (`"/folder/"`) — booleans.
    -   `inputFileFilters`: `{ allow: [ext/path/name/wildcard], exclude: [...] }` (exclusions checked
        after allow). **Hard** at the workflow level (errors on mismatch at execution).
    -   `concurrencyRestriction`: none (default) | perAsset | perInputFile — restricts simultaneous
        running executions for this workflow keyed on input asset / input file path.
-   **Metadata inputs (multi-select):** `assetMetadata`, `fileMetadata`, `fileAttributes` — if none
    selected, no metadata ingested even when available. See [metadata format](#metadata-file-format-v2).
-   **Output target:** `outputTarget: { locationType: asset, allowOverride: bool }`. When
    `allowOverride` is false, the handler **locks** output asset/db to the incoming file asset/db and
    ignores any output params. When true (only meaningful with multi + crossAsset), the execute request
    must supply the output asset (may default to the incoming asset when all inputs share one asset).
-   **Triggers:** `WorkflowTriggersTable` rows; `fileUpload` implemented now (extensions +
    default template ids per pipeline).
-   **subDashboardUrl:** optional URL string (web renders a "Dashboard" link opening a new tab).

### Workflow default (on create when unspecified)

`inputFileArity=one`, `assetScope`: no whole-asset, no folders, no cross-asset (single-asset implied),
`metadataInputs`: asset + file + fileAttributes all on, `outputTarget.allowOverride=false`
(locked to incoming), `concurrencyRestriction=none`. Category defaults to a general/uncategorized
value. These match the requirement; unspecified extras default to the most permissive-safe choice and
are called out in the plan doc for review.

---

## Pipeline system model

-   **Pipeline system variables** (admin-only, `systemConfig`): `inputFileArity`, `assetScope` flags,
    `metadataInputs`, `requireTemplate`, `allowCustomTemplateOverride`, `auxPreviewPipelineSuffix` (now
    a first-class pipeline system var — feeds the Phase-1 manifest field), `inputFileFilters` ({allow,
    exclude}), typeable `category`.
-   **`inputInstructions` moved to the template** (comment 2d) — it is guidance about what to provide
    for a given template's config, so it belongs per-template, not per-pipeline.
-   **Template-level overrides of pipeline defaults (comment 2e).** A template may override the
    pipeline's `inputFileArity`, `metadataInputs`, `assetScope`, and `inputFileFilters`. This supports
    **versatile pipelines** — e.g. a single file-conversion pipeline with one template per from→to type,
    each declaring the arity/filters/scope appropriate to that conversion. When a template sets an
    override, it wins over the pipeline default for executions using that template; otherwise the
    pipeline default applies.
-   **Pipeline file filters are soft** — they _filter_ which selected inputs reach that pipeline step's
    manifest (vs the workflow's hard restriction). Filter-to-empty on a pipeline that requires files is
    a **hard error** (locked decision 7). (When a template overrides the filters, its filters are the
    ones evaluated.)
-   **`requireTemplate` replaces the `allowNoTemplate`/`requireTemplate` pair** (comment 2c) — see
    [requireTemplate semantics](#execute-time-template-resolution-the-core-contract).
-   **Removed:** `outputType`, `pipelineType`, `allowNoTemplate`.
-   **Execution-type fields** richly modeled (see `executionConfig` above), incl. DeadlineCloud.

### System vs system/execution fields

Two visibility tiers on both pipelines and workflows:

-   **System fields:** shown only to admins configuring the pipeline/workflow + its templates/schema.
-   **System/execution fields:** shown at config time (default value) **and** again at execution time,
    where the execute request may override. If an execute request passes `null` for one, the stored
    default applies. (Web pre-fills the default on the execute view — web phase.)

The record model tags each configurable field with its tier so the execute handler knows which fields
accept an override and which are admin-only. **Implementation note:** represent as two maps
(`systemConfig`, `systemExecutionConfig`) rather than per-field flags, for clean override merging.

---

## Execution overhaul

### New execute request shape

```
POST /workflows/{workflowDatabaseId}/{workflowId}/execute        (asset-less; old route removed)
{
  "inputFiles": [ { "databaseId", "assetId", "relativeFileKey", "versionId" } ],  // 0..N
  "outputAssetId": "...", "outputDatabaseId": "...",             // honored only if allowOverride
  "workflowVariables": { ... },                                  // system/execution overrides
  "pipelineExecutionParameters": {
     "<pipelineId>": { "templateId"?, "templateTags":[{key,value}], "customTemplateOverride"? }
  },
  "executionGroupId": "..."?,                                    // optional bulk grouping
  "triggerType": "manual"                                        // set by trigger dispatch otherwise
}
```

-   **Input existence** (Phase 1's `verify_inputs_exist_in_s3`) extended to the multi-file object array
    incl. `versionId` (head specific version).
-   **Output-target locking** enforced at the handler per workflow `outputTarget.allowOverride`.
-   **Disabled/archived-pipeline gate (comment 5c):** if any pipeline included in the workflow is
    `enabled=false` **or** `archived=true`, the execution errors before launch (the workflow itself must
    also be enabled + not archived).
-   **Reserved system tag keys rejected** — a `templateTags` entry whose key is a system-defined tag key
    errors (comment 5d).
-   **Template resolution + tag validation** phase (above), then the
    [cross-entity validator](#cross-entity-validation-hard-vs-soft), then launch.
-   Persist final rendered config + tags + templateId + system/exec vars for full traceability.

### New execution operations (all this phase)

-   **Re-run:** `POST /workflows/executions/{executionId}/rerun` — reconstruct the execute request from
    stored records + re-validate the caller's permissions on every referenced asset/db/workflow/pipeline,
    then launch a new execution. New `executionId`; optionally same `executionGroupId`.
-   **Permanent delete:** `DELETE /workflows/executions/{executionId}/permanent` — remove only the
    DynamoDB rows across all execution sub-tables; guarded (execution must not be in progress); does not
    touch the SFN execution history.
-   **executionGroupId:** stored on records + `ByGroupGSI`; abort accepts a `groupId` to stop all active
    executions in the group. (Bulk CSV ingestion deferred — [future backlog](#future-backlog).)
-   **Global list/details/logs/abort:** asset-less, permission-filtered by the caller's data access to
    the input and/or output assets.

### Auto-trigger execution (EventBridge-bus driven)

On a matched `fileUpload` trigger, the default template per included pipeline comes from the
`WorkflowTriggersTable` row's `defaultTemplateIds` map (keyed `pipelineDatabaseId:pipelineId` →
templateId) — **not** from a flag on the template (comment 2f-ii). Those templates must have every
required tag defaulted (validated at pipeline registration / vamsSchema ingest). If a pipeline has no
entry in `defaultTemplateIds`, no config is set for it beyond system/exec vars. Execution records
`triggerType=fileUpload`.

**Trigger delivery via the VAMS orchestration EventBridge bus (comment 5a).** File-upload triggers
publish to the existing VAMS orchestration EventBridge bus (`storageResources.eventBridge.orchestrationBus`,
created in a prior update and, today, used only for pipeline sub-execution registration). The upload
event → EventBridge rule (match on the deployment event-source prefix + upload detail-type) → target.
Design decisions to settle in implementation:

-   **Do we still need an SQS queue after EventBridge?** Today the upload path enqueues to
    `storageResources.sqs.workflowAutoExecuteQueue`. With EventBridge in front, evaluate whether a queue
    is still required. **Recommendation:** keep an SQS target on the rule (EventBridge → SQS →
    dispatcher lambda) because a single upload action can fan out to **hundreds–thousands** of files;
    SQS gives buffering, batching, retry/DLQ, and throttled concurrency that a direct lambda target does
    not. EventBridge replaces the _direct enqueue_ (producers publish events, not queue messages) while
    SQS remains the durable buffer. Confirm the volume assumption; if trigger volume is provably low the
    queue can be dropped for a direct lambda target. Either way the filter evaluation (matching
    `inputFileFilters`) runs in the dispatcher after the candidate-workflow lookup.
-   Filter matching uses the full `inputFileFilters` structure (ext/path/name/wildcard), not a flat
    extension list (comment 3d).

---

## Cross-entity validation (hard vs soft)

`common/workflows/executionValidation.py` —
`validate_execution(workflow_cfg, pipeline_cfgs, selected_inputs, output_target)`:

**Workflow-level hard errors (fail execution):**

-   Input count violates workflow `inputFileArity` (0 when none-not-allowed; >1 when single; etc.).
-   Input files span multiple assets when `singleAssetOnly`.
-   Whole-asset (`"/"`) or folder (`"/folder/"`) selected when not allowed.
-   Any input file fails the workflow `inputFileFilters` (allow/exclude).
-   Concurrency restriction violated (running execution exists on the same asset / input file).
-   Output target override supplied when `allowOverride=false` → ignored (locked to incoming), not an
    error; but a _required_ output (multi+crossAsset) missing → error.

**Workflow↔pipeline cross-cases (the enumerated matrix):**
| Workflow | Pipeline | Result |
| --- | --- | --- |
| any arity | requires file, none available after filter | **hard error** |
| single/multi file | pipeline `none` (no file) | pass no files to that pipeline (soft) |
| multi-file | pipeline `one` (single) | **hard error (for now)** |
| none (no file) | pipeline requires file | **hard error** |
| files present | pipeline filters exclude all | **hard error** (locked decision 7) |
| files present | pipeline filters subset | pass filtered subset into that pipeline's manifest |

Returns `(errors[], per_pipeline_filtered_inputs)`. Edge cases err toward erroring for now; the
matrix is the single source of truth and will be refined over time.

### Workflow-save validation (comment 6 / Q6)

Beyond execute-time validation, **workflow create/update performs basic consistency checks** across
the workflow's system config and its included pipelines, returning `warnings[]` (and hard `errors[]`
for unsatisfiable combinations) on the save response:

-   **Metadata mismatch:** an included pipeline whose `metadataInputs` requires a metadata type
    (e.g. `fileMetadata`) that the workflow's `metadataInputs` gate has turned off → warning (the
    pipeline will run without that metadata) or error if the pipeline hard-requires it.
-   **Arity mismatch:** workflow `inputFileArity=multi` with an included pipeline that is `one`
    (single-file) — surfaces the same case flagged in the execute matrix, at save time.
-   **Filter shadowing:** workflow `inputFileFilters` that exclude everything an included pipeline needs
    → warning.
-   **Trigger default sanity:** a `defaultTemplateIds` entry pointing at a template whose required tags
    are not all defaulted → warning (auto-trigger would fail for that pipeline).

This gives authors immediate feedback rather than surfacing only at execution. The check reuses the
same `executionValidation` primitives where possible.

---

## Metadata file format v2

New grouped-by-asset envelope (bump `METADATA_SCHEMA_VERSION`):

```json
{
  "schemaVersion": 2,
  "assets": [
    {
      "databaseId": "db1", "assetId": "xid1",
      "assetData": { "assetName": "...", "description": "...", "tags": [] },
      "files": [
        { "fileKey": "/",           "metadata": { ... } },   // asset-level metadata
        { "fileKey": "/a.glb",      "metadata": { ... }, "attributes": { ... } },
        { "fileKey": "/folder/",    "metadata": null }        // folder: record, no metadata
      ]
    }
  ]
}
```

-   **Asset metadata** is the `fileKey:"/"` record; **file metadata/attributes** only for selected
    files (none selected → none included). Multi-asset → one `assets[]` entry per involved asset.
-   **Uniform record shape** for asset + file records. Folders get a record with null metadata.
-   **All 14 use-case pipelines** update their metadata read (via `manifestHelper.fetch_metadata` +
    container `manifest_io.py`) to the new grouped shape. `fetch_metadata` gains a helper to pull a
    specific asset/file record so pipeline code stays simple. (No back-compat shim per locked
    decision 5.)

---

## API surface

New/changed REST routes (each needs: `apiRoutes.py` constant + group array; handler with two-tier
Casbin; Pydantic v1 models; `apiBuilder2` attachment; `VAMS_API.yaml` + `api/*.md` docs — per root
Pattern 1). **Old asset-scoped execute route removed.**

**Pipelines:**

-   `GET/PUT /pipelines` (V2 model: system + exec config, category, name, enabled, archived).
-   `GET /pipelines/{db}/{pipelineId}` (details incl. templates).
-   `GET/PUT/DELETE /pipelines/{db}/{pipelineId}/templates[/{templateId}]` (template CRUD + tag schema).
-   `DELETE /pipelines/{db}/{pipelineId}` → archive (soft). `?includeArchived` on list.
-   Enable/disable via PUT.

**Workflows:**

-   `GET/PUT /workflows` (V2 model: system config, triggers, category, name, subDashboardUrl, enabled,
    archived; returns `warnings[]`).
-   `GET /workflows/{db}/{workflowId}` (details).
-   `DELETE /workflows/{db}/{workflowId}` → archive.
-   Trigger sub-resources as needed.

**Executions (global, asset-less):**

-   `POST /workflows/{workflowDatabaseId}/{workflowId}/execute` (new shape).
-   `GET /workflows/executions` (global list, permission-filtered, rich filters: asset in/out, workflow,
    user, status, group, category via join).
-   `GET /workflows/executions/{executionId}/details` | `/logs`.
-   `DELETE /workflows/executions/{executionId}` (abort) — add `?groupId=` variant.
-   `POST /workflows/executions/{executionId}/rerun`.
-   `DELETE /workflows/executions/{executionId}/permanent`.

All list endpoints: default hides archived; `NextToken` pagination; GSI-backed filters.

---

## Permissions

-   Add `category` + `name` to the pipeline and workflow Casbin object attribute construction (backend
    enforcer object dicts + any authz object registries/mappings).
-   **Two-tier Casbin on every new/changed API (comment 5b).** Every pipeline, workflow, and execution
    endpoint enforces **both** Tier-1 (API route / action) **and** Tier-2 (data entity) checks for the
    permission fields available on that entity type:
    -   **Pipeline APIs:** Tier-2 on the pipeline object (`database`, `category`, `name`, id).
    -   **Workflow APIs:** Tier-2 on the workflow object (`database`, `category`, `name`, id).
    -   **Execution APIs:** execution rows carry no first-class permission fields of their own, so Tier-2
        **relies on the referenced pipeline/workflow + input/output asset access** — see the global rule
        below. Tier-1 still gates the route.
-   **Global execution access rule:** a caller may list/execute/get-details/abort/rerun/logs/delete an
    execution iff they have data access to the input asset(s) it reads from **or** the output asset it
    writes to (Tier-2 on those assets), plus Tier-1 on the API route. New helper to resolve "assets
    this execution touched" from the input + output records for the permission check.

### Permission-template updates (comment 6)

Update the shipped permission templates (`documentation/permissionsTemplates/`), **especially the
non-admin templates**, to cover the new pipeline/workflow/execution APIs. Use best judgment against
existing template patterns, with these firm rules:

-   **Detailed-log fetching is an admin action** — the execution `/logs` (detailed logs) route is
    granted to admin templates only; non-admin execution access stops at list/details.
-   **Any permanent-delete is admin-by-default** — `DELETE …/permanent` (execution record hard-delete)
    and any other hard-delete route are admin-only. Soft actions (archive, disable) follow the existing
    per-entity template patterns.
-   Everything else (create/read/update/execute/rerun/abort of pipelines, workflows, executions) is
    mapped into the appropriate existing templates by analogy to how the current pipeline/workflow
    routes are templated. Mirror into `concepts/permissions-model.md` + `user-guide/permissions.md`
    (permission-model doc rule).

---

## CDK + vamsSchema ingestion

-   **Per-pipeline `backendPipelines/<pipeline>/vamsSchema/`** directory:
    -   `pipeline.json` — pipeline system config + `executionConfig` + IDs (pipelineId/databaseId
        overridable) + category + name.
    -   `workflow.json` — the workflow system config + triggers + file rules + IDs + category + name
        (one built-in workflow per pipeline, as today).
    -   `templates/<templateId>.json` — template `configBody` + `configFormat` + `tagSchema` + flags.
    -   `templates/<templateId>.webform.json` — web form-builder markup (opaque to backend).
-   **Minimal-required ingestion (comment 7):** the importer requires **only the very basic
    `pipeline.json` + `workflow.json`** to register a pipeline/workflow. **Everything else is
    optional** — `templates/*.json`, `templates/*.webform.json`, and tag schemas may be absent, and the
    pipeline still registers and is executable (template-less runs when `requireTemplate=false`). The
    importer skips any missing optional artifact rather than failing. This keeps external/self-registering
    pipelines lightweight.
-   **CDK flow (must be seamless/transparent to the developer):** the pipeline construct points at the
    `vamsSchema/` dir; CDK uploads whatever files are present to the deployment/artefacts bucket
    (BucketDeployment or asset) and passes the **S3 keys** to the import custom-resource lambda, which
    fetches + parses + upserts into the V2 tables. No manual upload; changing a template file +
    redeploying just works.
-   **Re-register semantics:** on deploy, if the built-in pipeline/workflow row exists and is archived
    → unarchive + overwrite from vamsSchema (preserves execution history); if values changed since last
    deploy → update + **re-deploy the workflow SFN**. ID overrides let built-ins keep known IDs for
    external references (viewers, etc.).
-   **External callable:** the import custom-resource lambda remains invocable outside VAMS (inline JSON
    or S3 keys) so external solutions can self-register.
-   **Default asset bucket:** add the `isDefault` flag to `S3AssetBucketsStorageTable` and set the
    default at deploy (the VAMS-created bucket by default; an external bucket entry with
    `isDefault=true` overrides it; exactly one required for all-imports deployments — validated in
    `getConfig()`). This default bucket is where all pipeline template S3 offload and all
    execution-time run I/O live (see
    [default asset bucket](#default-asset-bucket-for-template--config-s3)). Ensure the execution +
    pipeline/template lambda roles have read/write to it.
-   Config additions in `config.ts` validated in `getConfig()`; mirror into ConfigBuilder + run
    `configBuilderSync.test.ts` (per Rule 11 / configuration-system change).

### RETAIN on ALL DynamoDB tables (comment 1)

Switch **every** DynamoDB table in `storageResources` (not just the new pipeline/workflow/execution
V2 tables — all of them, including tables outside this feature) from `RemovalPolicy.DESTROY` to
`RemovalPolicy.RETAIN`. Rationale: retained tables survive `cdk destroy` and protect data, and — since
VAMS DynamoDB tables are **auto-named** (no explicit `tableName`), per `infra/CLAUDE.md` "Retained +
auto-named … survive teardown but do not block redeploy" — a fresh deployment generates new logical
names and **does not collide** with retained orphans. **Caveat / precondition:** confirm no VAMS table
sets an explicit `tableName`; any that does must be excluded (or would collide on redeploy) — audit
`storageBuilder-nestedStack.ts` before flipping. Update `architecture/aws-resources.md` +
`deployment/uninstall.md` removal-policy notes for all tables (the storage-resources documentation
rule). This also contradicts the current `infra/CLAUDE.md` Rule 4-step guidance ("use
`RemovalPolicy.DESTROY` (current pattern)") — that steering line must be updated to RETAIN in the same
change (Rule 11 bidirectional sync).

---

## Use-case pipeline conversion

For each of the 14 use-case pipelines:

1. **SFN adjustments:** re-check whether the new system/execution variables change the SFN body;
   update where they do.
2. **Add `vamsSchema/`:** author `pipeline.json` + `workflow.json` with a meaningful `category`,
   sensible system-config defaults, and IDs.
3. **Convert `inputParameters` → template(s):** deep-analyze each pipeline's existing `inputParameters`
   and metadata usage; where it makes sense, express them as a template `configBody` + tag schema
   with defaults/required, and (for metadata-driven ones like Cosmos) map the extracted fields to
   template tags. Only convert where it genuinely fits.
4. **Metadata-format v2:** update the pipeline's metadata read to the grouped envelope.

### Consolidation analysis (comment 8)

A required up-front step is a **full analysis to combine pipelines/workflows that were previously
deployed as separate variants** but are the same processing engine differing only by configuration.
Where combinable, collapse them into one pipeline with multiple **templates** (different configs) —
which also consolidates the use-case pipeline **config records** in `config.ts`.

-   **(i) File-conversion pipeline (primary target).** The conversion pipelines that today deploy as
    distinct pipelines per conversion type should become **one conversion pipeline with one template per
    from→to type** (each template carrying that conversion's config, arity, and filters via the
    per-template overrides — comment 2e). This is the flagship demonstration of the template/override
    model.
-   **(ii) Conversion reads `configBody`, not `outputType`.** The conversion pipelines refactor to read
    their conversion to/from parameters from the resolved **`configBody`** (template tags) instead of the
    now-removed pipeline `outputType` field. Container + `vamsExecute` boundary code that keyed on
    `outputType` is rewired to read the config body values.
-   **(iii) AI model/instance-type variants — only if runtime-dynamic.** AI pipelines that today deploy
    as separate pipelines to select a different **model + instance type + container** should be combined
    **only if** the instance-type + container matrix can be defined and selected **dynamically at
    runtime** (per-execution/per-template) rather than at deploy time. If that is complex (e.g. Batch
    compute-environment/job-definition per instance type baked at deploy), **leave those pipelines as
    they are** — do not force a combination. Where a clean runtime matrix is achievable, combining them
    also consolidates their config records.

**Deliverable of the analysis:** a per-pipeline decision (combine / leave separate) with the target
template set for each combined pipeline, produced before the conversion work so the vamsSchema
authoring reflects the consolidated shape.

Pipelines to review (from Phase-1 categorization): `3dRecon/splatToolbox`, `conversion/3dBasic`,
`conversion/coordinateTransform`, `conversion/meshCadMetadataExtraction`, `genAi/metadata3dLabeling`,
`genAi/nvidia/cosmos/{predict,reason,transfer,3}`, `genAi/nvidia/gr00t`, `multi/{modelOps,rapidPipeline,
rapidPipelineEKS}`, `preview/{3dThumbnail,pcPotreeViewer}`, `simulation/isaacLabTraining`.

---

## Data migration

Extend the `v2.5_to_v2.6` migration (new step `pipelineWorkflowV2`):

-   Migrate `PipelineStorageTable` → `PipelineStorageTableV2` (map `userProvidedResource` → typed
    `executionConfig`; synthesize a default single template from `inputParameters` where present;
    set `enabled` from V1, `archived=false`, default category; generate/keep IDs).
-   Migrate `WorkflowStorageTable` → `WorkflowStorageTableV2` (map `autoTriggerOnFileExtensionsUpload`
    → a `fileUpload` trigger row; default system config; `specifiedPipelines` snapshot preserved).
-   **Idempotent + don't overwrite CDK-managed built-ins:** if a target ID already exists (e.g. the CDK
    deploy already re-added a built-in), skip/merge rather than clobber — same guard style as the
    execution migration's deterministic-id approach.
-   Three-way SSM constants added for every new table; migration `ssm_resource_lookup.py` updated.

---

## Documentation updates

-   New/updated: `pipelines/*`, `concepts/pipelines.md`, `api/pipelines.md` + `api/workflows.md`,
    `VAMS_API.yaml`, `architecture/aws-resources.md` + `data-model.md` (new tables + removal policy),
    `deployment/configuration-reference.md` + ConfigBuilder, `deployment/uninstall.md`.
-   **Guide:** a step-by-step "create a new pipeline/workflow" guide (built-in via vamsSchema **and**
    externally-deployed-then-registered). Extend `pipelines/custom-pipelines.md`.
-   Kiro steering mirrors + skills (`/add-pipeline`) updated per Rules 11–12.
-   CHANGELOG.md entry.
-   Keep this plan doc's [resume checkpoint](#resume-checkpoint) current across sessions.

---

## Open questions — resolved log + remaining

Q1–Q9 were answered by the requester (Session 2); their resolutions are folded into the design above.
Recorded here as the decision log:

1. **Q1 — Template-tag override "extra tags": RESOLVED — ignore extras.** Providing a tag with no
   `{{match}}` in the body is silently ignored; the only render-time tag error is an **unmatched
   `{{tag}}` in the body**. Missing-required (schema mode) still errors.
2. **Q2 — `configFormat` set: RESOLVED — JSON + YAML + OpenJD + free-text/raw, from the start.**
   `configFormat ∈ {json, yaml, openjd, xml, raw}`. Raw/free-text is the basic tag-replacement mode
   for arbitrary formats. Deadline Cloud (OpenJD/YAML) is **fully supported now**, not deferred.
3. **Q3 — Output-asset execution index: RESOLVED — dedicated `WorkflowExecutionOutputsIndex` table**
   (not a second GSI on the inputs table).
4. **Q4 — `name` uniqueness: RESOLVED — display-only, non-unique.** The composite `db:id` key is the
   uniqueness guarantee.
5. **Q5 — Multi-asset + asset-metadata: RESOLVED — keep metadata separate per asset** (grouped
   envelope, one `assets[]` entry per asset; no cross-asset dedup/merge).
6. **Q6 — pipeline vs workflow metadata + filtering: RESOLVED — implement pipeline filtering right
   away** (workflow gates ingestion; pipeline `metadataInputs` further-filters what that pipeline
   sees). Additionally, **basic workflow-save warnings/errors** are in scope now — e.g. a pipeline
   that requires metadata included in a workflow whose metadata input for that type is off produces a
   save-time warning/error (see [workflow-save validation](#workflow-save-validation-comment-6--q6)).
7. **Q7 — Absolute template/config size cap: RESOLVED — ~6 MB target with best-practice buffers.**
   Confirm the effective sync-invoke ceiling and set the combined cap just under it, **reserving
   headroom** for the other row fields including the tag schema (which can be extensive and now lives
   in its own table/row). Enforced identically at API + CDK upload. Bodies needing more than the sync
   limit become the future presigned-upload flow (backlog).
8. **Q8 — Default-bucket rebase read-compat: RESOLVED — forward-only.** Existing S3 `/pipelines/` data
   in other (asset) buckets is left in place; new executions use the default bucket. No compat read
   path for old run scratch.
9. **Q9 — All-imports default bucket: RESOLVED — `isDefault` boolean on each external bucket entry**
   (`externalAssetBuckets[].isDefault`), validated in `getConfig()` (at most one across the
   deployment; exactly one required when no bucket is VAMS-created; an external default overrides the
   created bucket); execution/pipeline lambda roles granted read/write to it.

**Remaining:** none — all resolved.

10. **Q10 — Tag-schema table row shape: RESOLVED — mirror the metadata-schema paradigm exactly.** The
    requester's rule: don't introduce a different paradigm across VAMS. The metadata **schema** table
    (`MetadataSchemaStorageTableV2`) is **one row per schema** (PK `metadataSchemaId` UUID, SK
    `databaseId:metadataEntityType`) with all field definitions stored **inline as a JSON string**
    (`fields = json.dumps(...)`, single `put_item` per schema — `metadataSchemaService.py`;
    row-per-field is only the deprecated legacy table). `PipelineTemplateTagSchemaTable` matches this:
    **one row per template**, `tagSchemaId` UUID PK + `pipelineDatabaseId:pipelineId:templateId`
    composite SK + owner GSI, tag definitions inline in a `fields` JSON string. See the
    [tag-schema table](#pipeline-tables).

---

## Deferred to the Web + CLI phases

**Explicitly NOT built in this backend phase** — captured so nothing is lost:

### Web phase (after backend + CLI)

-   Dynamic React form system driven by the template `tagSchema` + `webFormJson` (evaluate **JSON
    Forms / react-jsonschema-form / FormEngine** — pick simplest for authoring + storing schemas,
    building the layout, and rendering at execute). Form output = the array of template-tag key/values.
-   Pipeline **system template editor** (author templates, tag schemas, web form, flags).
-   Execute **wizard modal**: page 1 = workflow file/asset selection honoring workflow file rules +
    workflow system/exec vars (auto-filter to current asset when launched from an asset; no asset
    pre-filter when launched from the workflows page; cross-asset search when allowed; **file-version /
    asset-version-id selection**); subsequent stages = one per pipeline (exec vars + template dropdown
    with default preselect + rendered React form + **raw config view (IAM-policy-editor style)**, raw
    editable only when `allowCustomEdit`).
-   Output-asset search/filter on the execute screen when override allowed; locked otherwise.
-   **Requirement-state / hard-error display in the execute wizard (comment 9).** The wizard must show a
    clear hard error / unsatisfied-requirement state when the chosen input files do not satisfy the
    minimum input requirement of **any** included pipeline, the chosen pipeline template, or the overall
    workflow rules — e.g. a conversion `X→Y` template is chosen but the selected input file is type `Z`,
    so that pipeline's requirement is unmet → the wizard blocks launch and displays which
    pipelines/templates are not satisfied by the current selection. This mirrors the backend
    cross-entity validator + workflow-save checks in the UI so the user cannot submit an invalid run.
-   **Executions sub-page** under workflows: global execution list with advanced filtering + grouping
    by category/asset/user/status/group, state-of-the-art progress UI, input/output/log views, and
    right-click modal actions (details, logs, abort, re-drive). Reusable component embedded in the asset
    view's workflow tab (executions where the asset was input or output).
-   "Dashboard" link rendering `subDashboardUrl` (new tab).
-   Category-grouped expandable lists for pipelines + workflows.
-   Web-side defaults pre-fill of system/execution variables.
-   Web-side archived toggle.

### CLI phase (before web; used for live smoke tests)

-   VAMSCLI command groups: `workflows`, `pipelines`, `executions` — create/edit (incl. templates + tag
    schemas + system config), execute (multi-file object array, output target, per-pipeline template
    ids + tags + override), list/details/logs/abort/rerun/permanent-delete, enable/disable, archive.
-   All endpoint paths in `constants.py`; follow CLI standards; tests; smoke-test the full API surface.

---

## Future backlog

-   Presigned-upload flow for template bodies / overrides that would exceed the absolute sync-payload
    cap ([Q7](#open-questions--resolved-log--remaining)) — this phase already offloads >390 KB bodies to
    S3 transparently, but the client-side transfer is still bounded by the sync payload limit; a
    presigned direct-to-S3 path removes that ceiling in the future.
-   Bulk executions (500+ from CSV import) — `executionGroupId` + GSIs are prepped now; the ingestion
    path, performance tuning, and group-level result summaries are future. Analyze grouping/performance
    before building.
-   Output `locationType` beyond `asset`: **personal user workspaces** (input files also selectable from
    a user workspace) — model `outputTarget.locationType` as an enum now to allow it later.
-   Output type **`newAsset`**: create a new asset at execution completion (requires a run-time new-asset
    config). Not implemented; model the output-target structure to accommodate it (a `locationType`
    value + a `newAssetConfig` slot).
-   Additional triggers: **assetVersion**, **assetEdit**, **fileUploadGroup** (the group needs a
    file-upload change to specify a grouping id across upload ids; when all uploads in a group finish,
    trigger one execution over all involved files within the file cap).
-   Whole-asset selection with an **asset version id** passed (today: per-file version id only).
-   Group-level abort result summaries; group-level dashboards.

---

## Requirement → design coverage map

Every requirement block from the source, mapped to a section (✔ = covered here; → = deferred/backlog
with a home). Used to verify nothing is missed.

**PIPELINES:** input-config template tags (system + user-defined) ✔ [template system]; reserved
system-tag-key rejection ✔ [tag schema + execute]; execute-time config pass-in + templateId + tag
validation ✔ [execute-time resolution]; input instructions field ✔ **now on the template** [template
table, comment 2d]; system vs system/execution fields ✔ [system vs exec]; pipeline system vars
(**inputFileArity**, same-asset, whole-asset, folder, metadata, **requireTemplate** (allowNoTemplate
folded in), auxPreviewPipelineSuffix, category, remove outputType/pipelineType, exec-type fields incl.
Deadline) ✔ [pipeline tables + model]; input file allow/exclude filters ✔ [pipeline system model +
cross-validator]; require-template + per-template allow-custom-edit ✔ [template system]; **per-template
overrides of arity/metadata/assetScope/filters** ✔ [pipeline system model, comment 2e]; multiple
templates w/ preloaded config (json/yaml/openjd/xml/raw) + **tag schema in its own table** +
required/default ✔ [templates + tag-schema tables]; trigger-default **on triggers table, not template**
✔ [triggers, comment 2f-ii]; dynamic React form + webReactFormJson → [web phase]; execute wizard +
**requirement-state hard error** → [web phase, comment 9]; backend execute contract (templateId + tags
array + customTemplateOverride + validation + store final config/tags) ✔ [execute overhaul + config
snapshot record]; no-template free-form ✔ [execute-time resolution #4].

**WORKFLOWS:** system vars (file rules a–g, concurrency) ✔ [workflow system model]; hard-vs-soft note

-   cross-checks ✔ [cross-entity validator]; different output asset + locking + defaulting + newAsset
    future ✔/→ [workflow system model + backlog]; workflow create defaults ✔ [workflow default]; triggers
-   trigger file types + execution triggerType ✔ [triggers]; metadata input allowed + grouped format +
    pipeline updates + folder-no-metadata ✔ [metadata format v2]; subDashboardUrl ✔; future output/input
    = user workspace → [backlog]; future triggers (assetVersion/edit/uploadGroup) → [backlog]; workflow/
    pipeline id + name + db + CDK id override ✔ [tables + CDK]; global execution lookup w/ permission +
    execute/details/abort/rerun/logs ✔ [API + permissions]; executions sub-page + asset-tab reuse →
    [web]; category on pipeline/workflow + grouping ✔/→ [tables ✔, web grouping →]; vamsSchema JSON +
    re-register + SFN redeploy on change ✔ [CDK]; never delete / archive + includeArchived + unarchive on
    re-register ✔ [disabled vs archived + CDK]; GLOBAL + db-scoped preserved ✔; new permission fields
    (category + name) ✔ [permissions]; V2 tables + sub-tables ✔ [data model]; easy-to-create defaults ✔
    [guiding principles].

**EXECUTION:** multi-file object array + output asset + workflow exec vars + per-pipeline exec vars ✔
[execute request]; output-asset vs input-asset match check ✔ [validator]; **disabled OR archived
pipeline gate** ✔ [execute overhaul, comment 5c]; two-tier Casbin on all exec APIs ✔ [permissions,
comment 5b]; re-run API ✔; permanent-delete API (admin) ✔; auto-trigger default templates (from
triggers table) + required defaults ✔; **triggers via EventBridge bus + SQS-buffer eval** ✔
[auto-trigger, comment 5a]; **per-execution config snapshot** ✔ [config record, comment 4]; change
execute APIs ✔ [API]; bulk/group id + abort-by-group ✔ (ingestion → backlog); execution
filtering/grouping via GSIs (no OpenSearch) ✔ [data model GSIs] / → [web grouping].

**CDK:** vamsSchema files + S3 pass-through + large-file handling ✔; **minimal-required ingestion
(pipeline+workflow only; rest optional)** ✔ [comment 7]; external-callable CR ✔; system/exec var +
workflow JSON files ✔; default asset bucket (buckets-table `isDefault` + `externalAssetBuckets[].isDefault` config) ✔
[default asset bucket]; **RETAIN on all DynamoDB tables** ✔ [comment 1].

**STORAGE (revised Session 1):** hybrid inline/S3 template bodies (320 KB inline threshold) ✔; clients never
touch S3 in/out (lambda rehydrates inline) ✔; absolute combined cap at API + CDK ✔ [Q7]; override may
exceed 390 KB, written to S3 for tracking ✔; all pipeline template + execution run I/O rebased to the
default asset bucket under `pipelines/` ✔ [default asset bucket, Q8–Q9].

**USE-CASE PIPELINES:** SFN re-check ✔; vamsSchema per pipeline ✔; inputParameters/metadata →
template conversion ✔; **consolidation analysis (combine variant pipelines → templates)** ✔;
**conversion drops `outputType`, reads `configBody`** ✔; **AI model/instance variants combined only if
runtime-dynamic, else leave** ✔ [use-case pipeline conversion, comment 8].

**PERMISSIONS:** two-tier Casbin (action + data) on all APIs ✔; **shipped permission templates updated
for new APIs** ✔; detailed-logs = admin, permanent-delete = admin ✔ [permissions, comment 6].

**VAMSCLI / DOCS / MIGRATION:** CLI → [CLI phase]; docs ✔ [documentation]; data migration ✔.

---

## Implementation work breakdown

Suggested phasing for the implementation sessions (each is an agent-parallelizable unit; keep the
Phase-1 separation discipline):

-   **WB1 — Data model + default bucket: ✅ DONE (unstaged, reviewed).** V2 tables — database-scoped
    **`PK databaseId, SK <entity>Id`** (matches V1 + metadata-schema paradigm; guarantees (db,id)
    uniqueness; native "list by database" Query; on-demand billing + GSI-only design means no
    hot-partition/10 GB-collection concern for these low-volume _definition_ tables) — `PipelineStorageTableV2`,
    `PipelineTemplatesStorageTable`, `PipelineTemplateTagSchemaStorageTable`, `WorkflowStorageTableV2`,
    `WorkflowTriggersStorageTable`, `WorkflowExecutionOutputsIndexStorageTable`, all with GSIs; **flipped
    ALL DynamoDB tables to `RemovalPolicy.RETAIN`** on the shared `dynamodbDefaultProps` (audit confirmed
    zero explicit `tableName` → collision-safe); three-way SSM constants + registration; record-builder
    modules (`pipelineRecords`, `workflowRecords`, extended `executionRecords` config snapshot); V2
    models (`pipelinesV2`, `workflowsV2`); **`isDefault` boolean on each `externalAssetBuckets[]` entry**
    (at most one; overrides created bucket; required for all-imports) threaded through the `s3AssetBuckets`
    registry → populate CR row flag + backend `defaultBucket.resolve_default_bucket` helper +
    `getConfig()`/ConfigBuilder validation; hybrid body S3 offload helper (`templateBodyStorage`, **320 KB
    inline threshold** for DynamoDB-item headroom, ~6 MB absolute cap) writing to `pipelines/` in the
    default bucket. **Deferred to WB5:** the live execute-handler run-I/O bucket rebase (Phase-1 manifest
    `outputs.bucket`/`auxBucket`/interim `wf_exec_bucket`) — the V2 execute handler doesn't exist yet.
-   **WB2 — Shared schema validator:** `common/templateTagSchema.py` (+ commonize with metadata-schema
    after reading it; reserved system-tag-key rejection); `executionValidation.py` cross-entity
    validator + workflow-save checks; metadata-format-v2 helpers.
-   **WB3 — Pipeline domain:** models + handlers (CRUD, templates + tag schema, enable/disable/archive)
    -   Casbin fields + **two-tier enforcement** + apiRoutes + apiBuilder2 + models + docs.
-   **WB4 — Workflow domain:** models + handlers (CRUD, triggers, save-warnings/errors, archive) + SFN
    (re)generation w/ new system vars + Casbin fields + **two-tier enforcement** + routes + docs.
-   **WB5 — Execution domain:** new execute route + shape; template resolution phase; cross-validator
    wiring; disabled/archived-pipeline gate; global list/details/logs/abort; re-run; permanent-delete
    (admin); group id + abort-by-group; config-snapshot record; remove old asset-scoped route.
-   **WB5b — Trigger delivery:** EventBridge orchestration-bus rule for `fileUpload` +
    filter-based dispatcher + SQS-buffer decision (comment 5a); `inputFileFilters` matching.
-   **WB6 — CDK ingestion:** vamsSchema S3 upload + import CR lambda parse/upsert (**minimal-required:
    pipeline+workflow only**) + re-register/unarchive + SFN redeploy-on-change; config.ts + ConfigBuilder.
-   **WB7 — Use-case pipelines:** **consolidation analysis first** (combine → templates, esp.
    conversion; drop `outputType`→`configBody`; AI variants only if runtime-dynamic — comment 8), then
    per-pipeline vamsSchema + inputParameters→template conversion + SFN adjustments + metadata-v2 reads
    (all pipelines, container `manifest_io` updates).
-   **WB8 — Data migration:** `pipelineWorkflowV2` migration step (idempotent, don't-clobber-built-ins).
-   **WB9 — Permissions + docs:** update shipped permission templates (comment 6) +
    `concepts/permissions-model.md` + `user-guide/permissions.md`; all doc sections + create-a-pipeline
    guide + Kiro (incl. `infra/CLAUDE.md` RETAIN steering flip) + skills + CHANGELOG.

WB1–WB2 are prerequisites for WB3–WB5b. WB6–WB7 depend on WB3–WB4. WB8–WB9 last.

---

## Resume checkpoint

**Session 1:** Requirements reviewed against the current implementation; 16 clarifying questions
answered (see [locked decisions](#locked-decisions-qa-log)); this plan authored; storage architecture
revised to hybrid inline/S3 + default asset bucket. **No code written.** Grounded in:
`models/pipelines.py`, `models/workflows.py`, `models/executions.py`, the execution V2 table + GSI
patterns in `storageBuilder-nestedStack.ts`, current `apiRoutes.py` pipeline/workflow routes, and the
Phase-1 refactor plan + template-tag system.

**Session 2 (this session):** Requester answered all of Q1–Q9 and gave 9 additional comments; every
answer/comment is now folded into the plan (see [Session-2 decisions](#session-2-decisions-answers-to-q1q9--comments-19)
S1–S15). Notable changes: composite `db:id` PK for uniqueness; `schemaVersion` kept; `inputArity`→
`inputFileArity`; `allowNoTemplate`+`requireTemplate` consolidated to `requireTemplate`;
`inputInstructions` moved to template + per-template overrides of arity/metadata/scope/filters; tag
schema split into its own table; `isTriggerDefault` removed (trigger defaults on triggers table);
`configFormat` gains xml/raw (Deadline fully supported now); config-snapshot on the execution config
table; EventBridge-bus triggers (+ SQS-buffer eval); RETAIN on ALL tables; disabled/archived pipeline
→ execution error; reserved system-tag-key rejection; two-tier Casbin + permission-template updates;
minimal-required vamsSchema ingestion; use-case consolidation (conversion drops `outputType`).
The two questions posed back to the requester (schemaVersion, allowNoTemplate vs requireTemplate) are
answered inline (keep schemaVersion; consolidate to requireTemplate). **Still no code written.**

**All open questions resolved.** Q10 (tag-schema row shape) closed: mirror `MetadataSchemaStorageTableV2`
— one row per template, tag definitions inline as a `fields` JSON string, `tagSchemaId` UUID PK +
composite owner SK (confirmed against `metadataSchemaService.py`). No paradigm divergence across VAMS.

**Next session should:**

1. Confirm this revised plan with the requester — then begin implementation.
2. Read the existing **metadata-schema** module(s) to finalize the shared tag-schema validator surface
   (the one true implementation TODO called out in [shared validator](#shared-tag-schema-validator));
   audit `storageBuilder-nestedStack.ts` for any explicit `tableName` before the RETAIN flip.
3. Begin **WB1 (data model + default bucket + RETAIN)** — it unblocks everything else.

**Do not start implementation until the requester confirms this plan.** This plan is the contract for
the implementation phase; keep it and the coverage map updated as decisions evolve.
