---
title: Web Overhaul Design — Pipelines, Workflows, Executions
description: Design spec for the final website overhaul of the pipeline / workflow / execution UI, built on the fully-tested V2 backend APIs.
---

# Web Overhaul Design — Pipelines, Workflows, Executions

> **Status:** Design spec, pending implementation-plan generation. Authored 2026-07-18.
> **Scope:** The web (`web/`) UI for pipelines, workflows, and executions, rebuilt against the
> already-shipped-and-tested V2 backend APIs. Backend/API/CDK work is **verification + minor
> registration only** — no new API endpoints (see §9).

This is the final website overhaul for the pipeline/workflow/execution domain. The backend, CLI,
and infrastructure for the V2 pipeline/workflow/execution model are complete and were live-tested
end-to-end (parameter matrix, deep abort, Deadline Cloud, async/multi-pipeline/failure scenarios,
template-tag substitution, two-tier authorization). The web UI was explicitly deferred through those
phases and is built here.

---

## 1. Goals and non-goals

### Goals

1. Replace the current pipeline/workflow/execution UI with state-of-the-art React components that
   present the full V2 capability surface: 4 execution types (Lambda / SQS / EventBridge /
   DeadlineCloud), the 5-case template-resolution contract, per-pipeline templates + tag schemas,
   the multi-file/multi-granularity execute model, and the full execution lifecycle
   (list / details / logs / abort / rerun / permanent-delete).
2. A **dynamic execute wizard** (in-place modal) that adapts to each workflow's settings and each
   pipeline's config, mirroring backend validation so users see errors before launch.
3. A reusable **executions board** usable in three contexts (global, workflow-filtered,
   asset-embedded) with live status, filtering/sorting/grouping, and right-click actions.
4. **Pipelines** and **Workflows** pages with advanced filtering, category-grouped rows, full
   CRUD/archive, per-pipeline template editing, workflow pipeline-ordering, and trigger management.
5. **Permission-aware UI** that grays/hides actions the user cannot perform (Tier-1), leaving
   Tier-2 object filtering to the backend.
6. Modern components that **blend with the existing site** yet are **reusable** as the seed
   design-system for the eventual full-site refactor away from Cloudscape.

### Non-goals (this phase)

-   No full-site refactor of non-orchestration pages; existing Cloudscape pages stay as-is.
-   No client-side S3 upload for templates/config (backend handles S3 transparently; inline only).
-   No new backend API endpoints, no new execution types, no new trigger types beyond `fileUpload`
    (structure the UI to accept them later).
-   No arbitrary-DAG workflow authoring — the data model is a linear ordered pipeline list; the DAG
    view is visual-only.

---

## 2. Key decisions (locked during brainstorming)

| Decision              | Choice                                                                     | Rationale                                                                                                                                                             |
| --------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| React version         | **Upgrade to React 18.3 first**                                            | Verified small–medium, mostly mechanical; unlocks current-gen libraries and avoids building new components against soon-obsolete R17-only versions.                   |
| Styling               | **Tailwind CSS** (new pages) + **Radix UI** headless primitives            | Modern look, fast to build; scoped so it never affects existing Cloudscape pages.                                                                                     |
| Server-state          | **TanStack Query v5**                                                      | Caching, background refetch, polling, invalidation-after-mutation — ideal for the live execution board. Calls still route through `src/services/`.                    |
| Live status           | **Smart polling**                                                          | `refetchInterval` active only while a visible row is non-terminal (~5s); off when all terminal. Matches backend throttled-sync.                                       |
| Permission graying    | **Tier-1 only** (from `GET /auth/routes/api/allowed`)                      | Tier-2 is fully handled by the backend filtering returned data; inaccessible objects simply don't appear. A per-object action that 403s shows a clean inline message. |
| Dynamic tag form      | **@rjsf/core v5** (react-jsonschema-form)                                  | tagSchema → JSON Schema; `webFormJson` → uiSchema; outputs the `{key,value}[]` the execute API wants; handles all 6 field types + required/default/enum.              |
| Config / raw editor   | **@monaco-editor/react** (lazy)                                            | IAM-policy-editor feel; JSON/YAML/OpenJD/XML highlight + read-only + diff.                                                                                            |
| Workflow builder      | **@dnd-kit ordered card list + reactflow v11 read-only DAG preview**       | Matches the linear ordered model; DAG is a live visual, not an editor.                                                                                                |
| DAG library           | **reactflow v11** (replaces EOL `react-flow-renderer@9`)                   | Required regardless (v9 is EOL and excludes React 18).                                                                                                                |
| Forms                 | **react-hook-form v7 + zod**                                               | Create/edit forms with schema validation.                                                                                                                             |
| Pipeline ordering DnD | **@dnd-kit** (already in the app)                                          | Reuse existing dependency.                                                                                                                                            |
| Results view          | **Quick-view (in-place modal/drawer) + full-detail route**                 | Fast in-context peek plus deep, deep-linkable traceability.                                                                                                           |
| Wizard                | **In-place modal (Radix Dialog); never navigates**                         | Launches over the current page; closes back to it (which refetches).                                                                                                  |
| Navigation            | **Three top-level items**: Pipelines, Workflows, Executions                | Plus the embedded executions component in the asset tab.                                                                                                              |
| Test-data scale       | **Moderate** (~60–100 pipelines, ~40–60 workflows, few hundred executions) | Enough to force pagination + grouping + all filter/sort paths.                                                                                                        |

---

## 3. Architecture and foundation

### 3.1 Target stack (React 18)

-   **React 18.3** with `createRoot` (keep `StrictMode`), `@types/react@18` (both `devDependencies`
    **and** the `overrides` pin in `web/package.json`), `@testing-library/react` 13+,
    `react-test-renderer` 18, `@testing-library/user-event` 14, remove the deprecated (and unused)
    `@testing-library/react-hooks`, and swap the `react-dom/test-utils` `act` import in the 3 test
    files that use it.
-   **`react-flow-renderer@9` → `reactflow@11`** in `WorkflowEditor.tsx` (v9 `elements` prop →
    v11 `nodes`/`edges`/`onNodesChange`/`onInit`).
-   New libraries: **TanStack Query v5**, **Tailwind CSS**, **Radix UI**, **@rjsf/core v5**,
    **@monaco-editor/react**, **react-hook-form v7**, **zod**. (`@dnd-kit` already present.)

### 3.2 Tailwind integration (must not affect existing pages)

-   `corePlugins.preflight: false` — do **not** ship Tailwind's global reset (it would restyle
    Cloudscape pages). Rely on component-level utility classes.
-   `darkMode: ['selector', '.awsui-dark-mode']` — ride the existing theme toggle so new pages track
    the app's dark/light mode automatically.
-   Tailwind's content globs scoped to `src/features/orchestration/**` so unused utilities are purged
    and the surface is contained.
-   A dedicated `src/styles/tailwind.css` entry imported once at the app root (after Cloudscape
    styles) — verified in testing to cause no visual regression on existing pages.

### 3.3 Directory layout

```
web/src/
  features/orchestration/           # NEW — overhaul home (reusable for the later site refactor)
    api/
      pipelines.ts workflows.ts executions.ts templates.ts triggers.ts   # service fns (import apiClient)
      queries.ts                    # TanStack Query hooks (useQuery/useMutation wrappers)
    permissions/
      useAllowedRoutes.ts           # cached GET /auth/routes/api/allowed -> can(method, path)
    components/                     # SHARED, Cloudscape-free primitives (design-system seed)
      DataTable.tsx                 # TanStack Table v8 + Tailwind (filter/sort/paginate/group)
      ContextMenu.tsx               # Radix context-menu + kebab
      StatusBadge.tsx               # execution/pipeline status pill (ABORTED vs FAILED vs ...)
      ConfigEditor.tsx              # Monaco wrapper (lazy; language + readOnly)
      DynamicTagForm.tsx            # @rjsf/core wrapper (tagSchema -> form -> {key,value}[])
      CategoryGroupedList.tsx       # collapsible category rows
      QuickView.tsx                 # Radix drawer/dialog quick-view shell
      FilterBar.tsx Dialog.tsx ...  # shared Radix + Tailwind primitives
    pipelines/                      # PipelinesPage, PipelineForm, TemplateEditor, TagSchemaBuilder
    workflows/                      # WorkflowsPage, WorkflowBuilder, PipelineOrderList, DagPreview, TriggersEditor
    executions/                     # ExecutionsBoard, ExecutionQuickView, ExecutionDetailPage, row actions
    wizard/                         # ExecuteWizard (stepper), stage components
    types.ts                        # V2 TypeScript contracts (pipeline/workflow/execution/template)
  pages/                            # thin lazy-loaded route wrappers (existing convention)
  styles/tailwind.css               # scoped Tailwind entry
```

Everything under `features/orchestration/components/` is built **Cloudscape-free** (Tailwind + Radix)
so it can seed the later full-site design system.

### 3.4 Service layer and data flow

-   **Rule 3 preserved:** components never import `apiClient`; all calls go through
    `features/orchestration/api/*` service functions (which may import `apiClient`).
-   Add the **missing** service functions (none of these endpoints are wired in the frontend today):
    templates CRUD, tag-schema get/set, triggers CRUD, execution details/logs/abort/rerun/
    permanent-delete, and the V2 execute route `POST /workflows/{workflowDatabaseId}/{workflowId}/execute`.
-   **Normalize the legacy bare-array / error-string fetchers** (`fetchAllPipelines`,
    `fetchDatabaseWorkflows`, etc.) to the `[boolean, data]` tuple behind the query hooks so the new
    code has one consistent shape. (The old functions stay for legacy callers; new hooks wrap
    normalized variants.)
-   **TanStack Query keys** are structured (`['pipelines', {databaseId, filters}]`,
    `['executions', {scope, filters}]`, `['execution', id]`, `['allowedRoutes']`) so mutations can
    invalidate precisely.
-   **Pagination:** the query hooks page the `NextToken` cursor to exhaustion (or use
    infinite-query for very large lists); the board's client-side refinement sits on top of the
    fetched set. Filtering/sorting/hiding that the backend supports is pushed to the API.

### 3.5 Permission model (graying)

-   `useAllowedRoutes()` fetches and caches `GET /auth/routes/api/allowed` (the API routes + methods
    the current user may call) and exposes `can(method, pathTemplate)`.
-   Actions (Create, Edit, Delete/Archive, Execute, Abort, Rerun, Logs, Permanent-Delete) are
    **hidden or disabled** when the corresponding route/method is not allowed — notably the
    **admin-only** Logs (`GET /workflows/executions/{id}/logs`) and Permanent-Delete
    (`DELETE /workflows/executions/{id}/permanent`).
-   **Tier-2 is not pre-checked client-side.** The backend filters lists to what the user can access,
    so inaccessible objects never appear; a per-object action that returns 403 surfaces a clean inline
    "You don't have access to this item" message.

---

## 4. Execute wizard (request section 1)

An **in-place modal** (Radix Dialog) — it never navigates away from the page it was launched from
(Workflows page, Executions page, or asset Workflows tab). Its stages are steps within the single
dialog (stepper with Back/Next). On finish/cancel/Esc it closes and the underlying page refetches
(TanStack invalidation) to show the new `RUNNING` execution. Monaco lazy-loads only when a
config/override stage is reached.

The wizard is **dynamically shaped** by the selected workflow's `systemConfig` and each pipeline's
config, and mirrors the backend's **5-case template resolution** and cross-entity validation.

### 4.1 Entry contexts

-   **From an asset:** input files auto-filter to the current asset; workflow selection scoped.
-   **From the Workflows / Executions page:** no asset pre-filter; workflow chosen first, then inputs
    via cross-asset search when the workflow allows it.

### 4.2 Stages (rendered only when relevant)

**Stage 0 — Workflow and inputs.**

-   Workflow picker (skipped when launched pre-selected).
-   **Requirements banner:** if the workflow or any included pipeline is `disabled`/`archived`, block
    launch and name the offender (backend comment 5c).
-   **Input selection honoring `inputFileArity`:** `none` → no picker (results-only); `one` → single
    file / whole-asset (`/`) / folder (`/folder/`); `multi` → up to 1000. Each input supports the
    three granularities + **per-file version selection** (latest when blank).
-   **Output target:** locked to the input asset unless `outputTarget.allowOverride`, in which case an
    output-asset search/picker appears (only assets the user can POST to). Special case: when inputs
    resolve to 0 or multiple assets, an explicit output target is required regardless of
    `allowOverride`. Results-only (`locationType:none`) shows no output picker.

**Stage 1..N — one per pipeline (IAM-wizard style).**

-   **System/execution variables** the pipeline exposes, pre-filled with config-time defaults,
    overridable here.
-   **Template dropdown** with the workflow's `defaultTemplateId` preselected;
    `requireTemplate=true` forces a choice.
-   **Dynamic tag form** (`DynamicTagForm` / @rjsf) from the template's `tagSchema` (+ `webFormJson`
    layout) → produces `templateTags: [{key,value}]`, with required/type/enum validation client-side.
-   **Config view (Monaco):** the resolved config body; read-only by default; **editable only when
    `allowCustomEdit`** (final-config hand-edit) via an inline toggle.
-   **Custom override path:** when `allowCustomTemplateOverride`, a "Use custom config" mode swaps to
    an editable Monaco body (case 2: with template; case 3: template-less, offered only when
    `requireTemplate=false`).
-   **Live 5-case validation:** missing-required tags, unmatched `{{tag}}` in the body, reserved
    system-tag-key collisions — surfaced inline before submit.

**Review and launch.**

-   Summary of inputs, output target, per-pipeline template/tags/override.
-   **Hard-error gate (comment 9):** if chosen inputs don't satisfy any included pipeline's/template's
    minimum requirement (e.g. an `X→Y` conversion template but the input is type `Z`), launch is
    **blocked** and the unsatisfied pipelines/templates are listed.
-   On launch → `POST /workflows/{db}/{wid}/execute`; surface returned `warnings[]`; refetch the
    underlying page.

### 4.3 Payload

```jsonc
{
    "inputFiles": [
        { "databaseId": "…", "assetId": "…", "relativeFileKey": "/…", "versionId": "…?" }
    ],
    "outputAssetId": "…?", // honored only if outputTarget.allowOverride (or 0/multi-asset case)
    "outputDatabaseId": "…?",
    "pipelineExecutionParameters": {
        // keyed by pipelineId
        "<pipelineId>": {
            "templateId": "…?",
            "templateTags": [{ "key": "…", "value": "…" }],
            "customTemplateOverride": "…?"
        }
    },
    "executionGroupId": "…?",
    "triggerType": "manual" // default
}
```

### 4.4 The 5-case template-resolution contract (wizard core logic)

Per pipeline, `pipelineExecutionParameters[pipelineId]` = `{templateId?, templateTags, customTemplateOverride?}`:

1. **templateId + tags** — validate tags against the schema; render stored `configBody`.
2. **templateId + override** — only if `allowCustomTemplateOverride`; tags still validated; render
   the override body.
3. **override, no templateId** — only if `allowCustomTemplateOverride` **and** `requireTemplate=false`;
   tags taken as-is; every `{{tag}}` must resolve.
4. **no template, no override** — only if `requireTemplate=false`; system/execution vars only.
5. **`allowCustomEdit`** — per-template flag gating whether the final rendered config may be
   hand-edited at execute (the raw editable Monaco view).

The wizard enables/disables each path per these flags and runs the same validation client-side.

---

## 5. Executions board and results (request section 2 + results)

### 5.1 `<ExecutionsBoard>` — one reusable component, three contexts

-   **Global** (Executions page) — all executions the user can see, grouped by workflow (collapsible)
    or flat.
-   **Workflow-filtered** — pre-filtered to one workflow (deep-linked from the Workflows page).
-   **Asset-embedded** — in the asset Workflows tab, filtered to executions where the asset was input
    or output (replaces the old `WorkflowTab`).

### 5.2 Table (TanStack Table v8 + Tailwind)

-   Columns: status, workflow (name/category), input asset(s), trigger + user, start/stop, duration,
    execution group.
-   **Current-first ordering:** non-terminal (`NEW`/`RUNNING`) pinned on top; terminal below; old
    ones collapsible/hidden by default — driven mostly by API filtering/sorting.
-   **Smart polling:** active only while any visible row is non-terminal (~5s).
-   **Filter/sort/group:** status, workflow, category, trigger type, user, execution group, date range,
    `includeArchived` toggle — API-side where supported, client-side refinement on top.
-   **Distinct terminal states:** `ABORTED` (abort) vs `FAILED` (error) vs `TIMED_OUT` rendered
    differently.

### 5.3 Row actions (Radix context-menu + kebab; Tier-1 gated)

| Action            | Endpoint                                       | Gating / notes                                                            |
| ----------------- | ---------------------------------------------- | ------------------------------------------------------------------------- |
| View results      | (client)                                       | Opens quick-view drawer.                                                  |
| Abort             | `DELETE /workflows/executions/{id}[?groupId=]` | Only when non-terminal; group variant; surfaces sub-process warnings.     |
| Rerun / re-drive  | `POST /workflows/executions/{id}/rerun`        | Optional reuse of `executionGroupId`.                                     |
| Logs              | `GET /workflows/executions/{id}/logs`          | **Hidden unless allowed** (admin-only); opens read-only log viewer.       |
| Permanent delete  | `DELETE /workflows/executions/{id}/permanent`  | **Hidden unless allowed**; confirm dialog enforcing `confirmDelete=true`. |
| Open full details | (route `/executions/{id}`)                     | Navigates to the detail page.                                             |

### 5.4 Results — two tiers

1. **Quick-view** (in-place drawer/modal from the list, no navigation): overall status/timing/
   trigger, error if any, a compact per-pipeline status strip, results text
   (`resultsContent`, truncation-aware), and links to outputs.
2. **Full detail** (`/executions/{id}` route — deep-linkable): header (status/timing/trigger/error);
   **Inputs** (files + versions); **per-Pipeline timeline** (status + the exact rendered config body
   that ran in Monaco read-only + the `templateId`/`templateTags`/`customTemplateOverrideUsed`
   snapshot, so it shows what actually ran even after a template later changes/archives);
   **Outputs** (files/metadata/results with download); **Logs** (admin-gated, read-only). Sub-process
   registration warnings shown where abort/log coverage is partial.

---

## 6. Pipelines page and template editor (request section 4 + 4a)

### 6.1 `<PipelinesPage>`

-   **Category-grouped** collapsible rows; each row a compact card (name, id, execution-type badge,
    enabled/archived, template count, database/GLOBAL).
-   **Advanced filter bar:** free-text (name/id/description), execution type, category, database,
    enabled/archived (`includeArchived`, archived hidden by default).
-   Tier-1 gating hides Create/Edit/Delete when routes aren't allowed. **Delete = archive** (soft;
    no hard delete of pipelines).

### 6.2 Create/Edit (react-hook-form + zod, Radix Dialog)

-   Top-level: `pipelineId` (optional; GUID if blank; `^[-_a-zA-Z0-9]{3,63}$`), `pipelineName`,
    `category`, `description`, `enabled`, database (GLOBAL allowed).
-   **`executionType` 4-way selector** with conditional (`appearsWhen`) sub-blocks: - **Lambda** — `lambda.resourceId`; **auto-provision disclosure** when blank (a new Lambda is
    created; naming/role/VPC noted). - **SQS** — `sqs.queueUrl` (validated). - **EventBridge** — `eventBridge.{busArn, source, detailType}` (validated). - **DeadlineCloud** — `deadlineCloud.{farmId, queueId, storageProfileId, priority,
maxRetriesPerTask, maxFailedTasksCount, templateType}`; **forces `waitForCallback=Enabled`**;
    the whole option is **hidden unless the `DEADLINECLOUD_PIPELINES` feature switch is present** and
    **hidden in GovCloud**. - Common: `waitForCallback`, `taskTimeout`, `taskHeartbeatTimeout` (client-validated 1–604800s).
-   **`systemConfig`** (admin fields): `inputFileArity` (none/one/multi), `assetScope` flags,
    `metadataInputs` checkboxes, `requireTemplate`, `allowCustomTemplateOverride`,
    `auxPreviewPipelineSuffix`, `inputFileFilters` (allow/exclude list editors).

### 6.3 Template editor (4a)

A per-pipeline templates panel with full CRUD of the pipeline's templates:

-   Fields: `templateName`, `description`, `configFormat` (json/yaml/openjd/xml/raw),
    **`configBody` in Monaco** (language = configFormat), `inputInstructions`, `allowCustomEdit`,
    and `overrides` (per-template overrides of `inputFileArity`/`metadataInputs`/`assetScope`/
    `inputFileFilters` — the conversion-matrix case).
-   **Tag-schema builder:** add/edit/remove fields, each
    `{tagKey, type∈(string|integer|number|boolean|string-list|enum), required, default, label,
description, enumValues}`; rejects reserved system-tag keys.
-   **`webFormJson`** layout authoring (RJSF uiSchema) with a **live preview** rendered by the same
    `DynamicTagForm` the wizard uses (reuse).
-   `configBody`/`webFormJson` sent/received **inline** (backend handles S3 transparently); guard the
    ~6 MB combined cap.

---

## 7. Workflows page, builder, triggers (request section 5, 5a, 5b, 5c)

### 7.1 `<WorkflowsPage>`

-   Same category-grouped card + filter-bar pattern as Pipelines; Tier-1 gating; Delete = archive.
-   Each card shows name/id/category, enabled/archived, pipeline count, **execution count**, a
    **"Dashboard" link** (opens `subDashboardUrl` in a new tab when set), and **Execute** +
    **View executions** actions.

### 7.2 Create/Edit workflow (full-page builder route)

-   Top-level: `workflowId` (optional GUID), `workflowName`, `category`, `description`,
    `subDashboardUrl`, `enabled`.
-   **`systemConfig`:** `inputFileArity`, `assetScope`, `metadataInputs`, `inputFileFilters`,
    `concurrencyRestriction` (none/perAsset/perInputFile), and **`outputTarget`** — `locationType`
    (asset/none) + `allowOverride`, with the **hard coupling enforced in the form**: `locationType:none`
    (results-only) locks `inputFileArity:none` (linked controls + validation message).
-   **Pipeline ordering builder (5a):** a **@dnd-kit drag-to-reorder list** of pipeline cards
    (min 1), each with a pipeline picker (DB + GLOBAL), per-pipeline **`defaultTemplateId`** and
    optional `jobName`; add/remove/reorder. Alongside, a **reactflow v11 read-only DAG preview** that
    re-renders live as order changes. **Advanced error checking** = inline per-card warnings + a
    save-time validation panel that mirrors the backend cross-entity + workflow-save checks
    (arity/metadata/filter-shadowing/trigger-default mismatches, disabled/archived pipeline in the
    set) and displays the save response `warnings[]`.
-   **5b:** Execute (opens the in-place wizard) and View executions (navigates to the Executions page
    filtered to that workflow); the execution count shows on the card.

### 7.3 Triggers sub-page (per workflow)

-   Trigger editor managing the workflow's triggers via the trigger CRUD API. `triggerType` currently
    `fileUpload`; config = `inputFileFilters` + `defaultTemplateIds`
    (`{"<pipelineDatabaseId>:<pipelineId>": templateId}`) + `enabled`. Assign/enable/disable a trigger
    and set per-pipeline default templates for auto-runs — **not** the old flat comma-list. Structured
    so future trigger types slot in.

### 7.4 Executions as a separate page (5c)

-   The `<ExecutionsBoard>` from §5: global, grouped by workflow, or filtered to a specific workflow
    (deep-linked from Workflows). Current executions on top, old ones hidden/collapsible — handled
    mostly by the API.
-   The **asset tab keeps** the embedded `<ExecutionsBoard>` (asset-scoped) so per-asset live
    executions stay visible in place (request section 3).

---

## 8. Asset view tab (request section 3)

-   Replace `WorkflowTab.tsx` with the embedded asset-scoped `<ExecutionsBoard>` + an **Execute**
    button opening the in-place wizard (pre-scoped to the asset).
-   Live status via smart polling; row actions (results/abort/rerun/logs/delete) gated as in §5.
-   Post-execution the tab refetches automatically (TanStack invalidation) — no manual trigger
    plumbing needed.

---

## 9. Routes, permissions, backend, and CDK alignment

**No new backend API endpoints are required.** Every pipeline/workflow/execution/template/trigger
endpoint the UI calls already exists in `backend/backend/common/apiRoutes.py` (`ALL_API_ROUTES`) and
`documentation/VAMS_API.yaml` (built + live-tested in the backend phases). The work is web-route
registration + permission wiring + doc sync:

1. **Web routes (client):** add to `web/src/routes.tsx` `routeTable` the **new `/executions` prefix**
   (`/executions`, `/executions/:executionId`) and any workflow trigger sub-route
   (`/databases/:databaseId/workflows/:workflowId/triggers`). `/pipelines` and `/workflows` already
   exist. All lazy-loaded and auto permission-filtered via `POST /auth/routes` (Casbin `web`
   objectType).
2. **Navigation:** add the **Executions** nav link in `web/src/layout/Navigation.tsx` (the
   "Orchestrate & Automate" section; Pipelines/Workflows already present).
3. **Permission templates** (`documentation/permissionsTemplates/*.json`): the `web` constraints
   currently list `['/pipelines','/workflows']` but **`/executions` is missing in every template**.
   Add the `/executions` **web** route prefix to the relevant templates (admin/user/readonly/global)
   and ensure the **api** prefixes cover `/workflows/executions` consistently (today only
   `database-user.json` has it).
4. **Seed default constraints (verify):** confirm the seed constraint constructs
   (`infra/.../auth/constructs/dynamodb-authdefaults-{admin,ro}`) do not hardcode a web-route
   allowlist that omits `/executions`; if they do, add it so the seeded admin/RO roles see the new
   page out-of-the-box.
5. **CDK:** no API-Gateway change — web routes are client `routeTable` + Casbin `web` objectType,
   not CDK-registered resources. The only CDK-adjacent touch is item 4.
6. **Backend verification (no code change expected):** confirm `GET /auth/routes/api` and the
   category-group arrays already list the workflow/pipeline/execution routes (they do).

---

## 10. Implementation phases

Each phase is independently verifiable (build + tests + targeted smoke-test).

1. **React 18 upgrade.** `createRoot`; `@types/react@18` (devDeps + `overrides`); testing-library
   13+, `react-test-renderer` 18, remove `@testing-library/react-hooks`, `user-event` 14; swap
   `react-dom/test-utils` `act` in 3 test files; `react-flow-renderer@9` → `reactflow@11` in
   `WorkflowEditor.tsx`. **Gate:** `npm run build` + `npm test` green + dev StrictMode smoke-test.
2. **Foundation.** Install Tailwind (preflight off, `.awsui-dark-mode` selector), TanStack Query
   provider, Radix, @rjsf, Monaco (lazy), zod/RHF; create `features/orchestration/` skeleton +
   `types.ts` + `useAllowedRoutes()`; extend `src/services/` (all missing endpoints + normalized
   tuple fetchers); build shared primitives (`DataTable`, `ContextMenu`, `StatusBadge`,
   `ConfigEditor`, `DynamicTagForm`, `CategoryGroupedList`, `QuickView`, `FilterBar`).
3. **Pipelines.** Page + create/edit (4 exec types incl. DeadlineCloud gating) + template/tag-schema
   editor.
4. **Workflows.** Page + full-page builder (dnd ordering + DAG preview + validation) + triggers
   sub-page.
5. **Executions + wizard.** `<ExecutionsBoard>` (global/workflow/asset variants), quick-view,
   full-detail route, row actions; the in-place execute wizard.
6. **Asset tab.** Migrate `WorkflowTab` to the embedded `<ExecutionsBoard>` + wire the in-place
   wizard.
7. **Routes/nav + cleanup.** Add Pipelines/Workflows/Executions routes (lazy, permission-filtered);
   **delete (not deprecate) the entire old-design orchestration UI cluster** once the new pages own
   the routes and the asset tab uses the new board — the old files are referenced only by each other
   and by the repointed entry points (`routes.tsx`, `ViewAsset.tsx`), so they delete together
   (see the implementation plan's dead-code task for the exact file list); permission-template
   updates (§9).

---

## 11. Testing (Playwright, live, against prod14)

-   **Bulk seed** (reuse the Phase-4/5/6 harnesses): ~60–100 pipelines across several categories,
    ~40–60 workflows, a few hundred executions across statuses/groups/assets — enough to force
    multi-page pagination, category grouping, and every filter/sort/group path.
-   **Flows tested through the UI:**
    -   Pipeline create/edit/archive (each exec type + template + tag-schema editor).
    -   Workflow create/edit/archive (dnd order + validation warnings + trigger assignment).
    -   Execute via wizard: arity none/one/multi, output override, template + tags, custom override,
        `allowCustomEdit` raw edit, and the hard-error gate.
    -   Executions board: filtering/sorting/grouping/pagination + live status transitions.
    -   Row actions: abort (single + group), rerun, permanent-delete, logs.
    -   Results: quick-view + full detail (inputs/outputs/config bodies/logs render correctly).
    -   Permission graying: run as the constrained `smoke-ro` user (from the backend Phase 6) and verify
        admin-only actions are hidden.
    -   Asset-tab embedded executions + in-place wizard.
    -   Dark-mode correctness and **no Tailwind bleed** into existing Cloudscape pages.

---

## 12. Documentation updates

-   **`web/CLAUDE.md`** — currently stale (says React 17, TS 4.4.4, Vite 6). Update to React 18,
    TS 5, Vite 8, the new libraries (TanStack Query, Tailwind, Radix, @rjsf, Monaco, reactflow 11),
    the `features/orchestration/` map, the new service functions, the `useAllowedRoutes` permission
    pattern, and the Tailwind-scoping rules.
-   **Kiro** `WEB_DEVELOPMENT_WORKFLOW.md` / `WEB_FRONTEND.md` — mirror the `web/CLAUDE.md` changes
    (bidirectional-sync rule).
-   **Docusaurus** — user-facing docs for the new Pipelines/Workflows/Executions pages; update
    `permissions-model.md` (the new `/executions` web route + Tier-1 graying note); confirm
    `VAMS_API.yaml` and `api/{domain}.md` reflect the endpoints (already present from backend phases).
-   Note the new web dependencies wherever dependency inventories are tracked.

---

## 13. Risks and mitigations

| Risk                                                            | Mitigation                                                                                     |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Tailwind preflight restyles existing Cloudscape pages           | `preflight: false` + scoped content globs; verified in the Playwright dark-mode/no-bleed test. |
| React 18 StrictMode double-invoke surfaces dev-only effect bugs | Dev smoke-test pass in phase 1; standard effect-cleanup patterns already present.              |
| `reactflow@11` migration in `WorkflowEditor.tsx`                | Isolated to one file; new builder uses it read-only, reducing surface.                         |
| Monaco bundle size                                              | Lazy-loaded only in the wizard/template-editor/detail views.                                   |
| Legacy fetchers return bare arrays/error strings                | Normalized behind the query hooks; old functions untouched for legacy callers.                 |
| Backend Tier-2 nuance leaking into UI                           | UI relies on backend-filtered data; only Tier-1 graying is client-side; 403s handled inline.   |

---

## 14. Open items for the implementation plan

-   Exact TanStack Query key taxonomy + invalidation map per mutation.
-   Precise Tailwind theme-token bridge to Cloudscape design tokens for visual blend.
-   The RJSF ↔ tagSchema/webFormJson mapping details (uiSchema conventions).
-   Whether the Executions detail page reuses or forks the quick-view sub-components.
-   The final list of new `routeTable` entries and their `active` nav mapping.
