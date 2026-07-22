---
title: Web Overhaul Implementation Plan — Pipelines, Workflows, Executions
description: Phased, task-by-task implementation plan for the pipeline/workflow/execution web overhaul.
---

# Web Overhaul Implementation Plan — Pipelines, Workflows, Executions

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the VAMS pipeline / workflow / execution web UI on React 18 with a modern, reusable component set (Tailwind + Radix + TanStack Query), fully exercising the already-tested V2 backend APIs.

**Architecture:** A new self-contained `web/src/features/orchestration/` feature module (service layer → TanStack Query hooks → Cloudscape-free shared primitives → per-domain pages/components), reached through the existing lazy-loaded, permission-filtered route table. The design spec is `documentation/docusaurus-site/docs/developer/web-overhaul-design.md` and is the source of truth for behavior.

**Tech Stack:** React 18.3, TypeScript 5, Vite 8, Tailwind CSS (preflight-off), Radix UI, TanStack Query v5, TanStack Table v8, @rjsf/core v5, @monaco-editor/react, reactflow v11, react-hook-form v7, zod, @dnd-kit (existing). Existing: Cloudscape 3, Amplify v6, react-router 6.30, HashRouter.

## Global Constraints

-   **Copyright header** on every new source file (verbatim): `/*\n * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.\n * SPDX-License-Identifier: Apache-2.0\n */`
-   **npm only** — never yarn. Run npm commands from `web/`.
-   **HashRouter** — internal nav uses relative paths (`navigate("/executions")`); never `BrowserRouter`.
-   **Rule 3 (service layer):** components/pages NEVER import `apiClient`. Only files under `web/src/services/` and `web/src/features/orchestration/api/` may import it.
-   **apiClient signatures:** `apiClient.get(path, { queryStringParameters })`, `apiClient.post(path, { body })`, `apiClient.put(path, { body })`, `apiClient.del(path, {})`. It strips a leading `/`, so pass paths WITHOUT a leading slash (e.g. `"pipelines"`, `` `database/${databaseId}/pipelines` ``). It returns parsed JSON or throws `ApiError` (with `.status`, `.body`).
-   **Service return shape (new code):** all new service functions return the tuple `[boolean, data | errorMessage]` — `[true, data]` on success, `[false, message]` on error.
-   **Synonyms:** user-visible "Asset"/"Database" text uses `Synonyms` from `web/src/synonyms.tsx`. Pipeline/Workflow/Execution/Template are literal (not synonyms).
-   **Lazy-load pages** in `routes.tsx` via `React.lazy`.
-   **Tailwind scoping:** `preflight: false`; `darkMode: ['selector', '.awsui-dark-mode']`; content globs limited to `src/features/orchestration/**`. New styling must NOT change any existing Cloudscape page.
-   **React 18 forbidden-in-R17 APIs are now allowed** (createRoot, useId, useTransition, etc.) — but only inside `features/orchestration/`; do not refactor unrelated pages.
-   **Node ID validation:** pipeline/workflow/template/executionGroup ids match `^[-_a-zA-Z0-9]{3,63}$`. `outputAssetId` uses the asset-id (filename) rule, not the strict id rule.
-   **Value enums (verbatim):** executionType ∈ `Lambda|SQS|EventBridge|DeadlineCloud`; waitForCallback ∈ `Enabled|Disabled`; taskTimeout/taskHeartbeatTimeout integer seconds `1..604800`; inputFileArity ∈ `none|one|multi`; concurrencyRestriction ∈ `none|perAsset|perInputFile`; outputTarget.locationType ∈ `asset|none`; configFormat ∈ `json|yaml|openjd|xml|raw`; tag type ∈ `string|integer|number|boolean|string-list|enum`; triggerType ∈ `fileUpload`; execute triggerType ∈ `manual|fileUpload`.
-   **Hard coupling:** workflow `outputTarget.locationType==="none"` REQUIRES `inputFileArity==="none"`.
-   **DeadlineCloud** pipeline type is hidden unless feature switch `DEADLINECLOUD_PIPELINES` is in `config.featuresEnabled`, hidden in GovCloud (`GOVCLOUD` feature), and forces `waitForCallback==="Enabled"`.
-   **Permission graying = Tier-1 only** from `GET auth/routes/api/allowed`. Tier-2 handled by backend (inaccessible objects aren't returned; a 403 on an action shows a clean inline message).
-   **Commit frequently** — one commit per completed task minimum. Do NOT push. Branch off the current feature branch; never commit to `main`.

---

## File / responsibility map

```
web/src/features/orchestration/
  types.ts                          # V2 TS contracts (Pipeline, Workflow, Execution, Template, ...)
  api/
    client.ts                       # tiny helpers: toTuple(), normalizeList() (shared error handling)
    pipelines.ts                    # pipeline + template + tag-schema service fns
    workflows.ts                    # workflow + trigger service fns
    executions.ts                   # execution list/details/logs/abort/rerun/permanent/execute
    queries.ts                      # TanStack Query hooks + query-key factory + invalidation
  permissions/
    useAllowedRoutes.ts             # cached GET auth/routes/api/allowed -> can(method, path)
  components/                       # Cloudscape-free primitives (Tailwind + Radix)
    DataTable.tsx StatusBadge.tsx ContextMenu.tsx FilterBar.tsx Dialog.tsx Drawer.tsx
    ConfigEditor.tsx DynamicTagForm.tsx CategoryGroupedList.tsx QuickView.tsx Stepper.tsx
  pipelines/ PipelinesPage.tsx PipelineForm.tsx TemplateEditor.tsx TagSchemaBuilder.tsx
  workflows/ WorkflowsPage.tsx WorkflowBuilder.tsx PipelineOrderList.tsx DagPreview.tsx TriggersEditor.tsx
  executions/ ExecutionsBoard.tsx ExecutionRowActions.tsx ExecutionQuickView.tsx ExecutionDetailPage.tsx
  wizard/ ExecuteWizard.tsx WizardInputStage.tsx WizardPipelineStage.tsx WizardReviewStage.tsx resolveTemplate.ts
web/src/pages/                      # thin lazy wrappers: PipelinesPage2, WorkflowsPage2, ExecutionsPage, ExecutionDetail
web/src/styles/tailwind.css
web/tailwind.config.js  web/postcss.config.js
```

---

# Phase 1 — React 18 upgrade

**Deliverable:** app builds and tests pass on React 18; ReactFlow migrated. No feature work.

### Task 1.1: Bump React + types + entry point

**Files:**

-   Modify: `web/package.json` (dependencies, devDependencies, `overrides`)
-   Modify: `web/src/index.tsx`

**Interfaces:**

-   Produces: app rendered via `createRoot` on React 18.3.

-   [ ] **Step 1: Edit `web/package.json` dependency versions.** Set `"react": "^18.3.1"`, `"react-dom": "^18.3.1"`. In `devDependencies` set `"@types/react": "^18.3.12"`, `"@types/react-dom": "^18.3.1"`, `"react-test-renderer": "^18.3.1"`, `"@testing-library/react": "^14.3.1"`, `"@testing-library/user-event": "^14.5.2"`. In the `overrides` block change `"@types/react"` from `^17.x` to `"^18.3.12"`. Remove `"@testing-library/react-hooks"` entirely.

-   [ ] **Step 2: Install.** Run: `cd web && npm install`
        Expected: resolves without peer-dep errors for react/react-dom.

-   [ ] **Step 3: Migrate `web/src/index.tsx` to `createRoot`.** Replace `import ReactDOM from "react-dom"` with `import { createRoot } from "react-dom/client"`, and replace the `ReactDOM.render(<App/>, document.getElementById("root"))` call with:

```tsx
const container = document.getElementById("root");
const root = createRoot(container!);
root.render(
    <React.StrictMode>
        {/* keep the exact existing tree that was inside ReactDOM.render */}
    </React.StrictMode>
);
```

(Preserve every provider/wrapper that was already inside the render call.)

-   [ ] **Step 4: Build.** Run: `cd web && npm run build`
        Expected: build succeeds (TypeScript compiles against @types/react 18).

-   [ ] **Step 5: Commit.**

```bash
git add web/package.json web/package-lock.json web/src/index.tsx
git commit -m "chore(web): upgrade to React 18 (createRoot + type bumps)"
```

### Task 1.2: Fix test utilities for React 18

**Files:**

-   Modify: `web/src/components/interactive/WorkflowEditor.test.tsx:7`
-   Modify: `web/src/pages/ListPageNoDatabase.test.tsx:7`
-   Modify: `web/src/pages/auth/RoleGroupPermissionsTable.test.tsx:7`

**Interfaces:**

-   Consumes: React 18 from Task 1.1.

-   [ ] **Step 1: Replace deprecated act import in all three files.** Change `import { act } from "react-dom/test-utils"` to `import { act } from "@testing-library/react"` (or `import { act } from "react"`). Keep call sites identical.

-   [ ] **Step 2: Run the test suite.** Run: `cd web && npx jest`
        Expected: no failures caused by `act`/testing-library version (WorkflowEditor test may fail here — fixed in Task 1.3; note which fail).

-   [ ] **Step 3: Commit.**

```bash
git add web/src/components/interactive/WorkflowEditor.test.tsx web/src/pages/ListPageNoDatabase.test.tsx web/src/pages/auth/RoleGroupPermissionsTable.test.tsx
git commit -m "test(web): use non-deprecated act import for React 18"
```

### Task 1.3: Migrate `WorkflowEditor` from react-flow-renderer@9 to reactflow@11

**Files:**

-   Modify: `web/package.json`
-   Modify: `web/src/components/interactive/WorkflowEditor.tsx`
-   Modify: `web/src/components/interactive/WorkflowEditor.test.tsx`
-   Check: `web/src/styles/theme.css` (ReactFlow dark-mode CSS selectors)

**Interfaces:**

-   Produces: `WorkflowEditor` rendering the same linear pipeline visualization using reactflow v11 `nodes`/`edges`.

-   [ ] **Step 1: Swap the dependency.** In `web/package.json` remove `"react-flow-renderer"` and add `"reactflow": "^11.11.4"`. Run: `cd web && npm install`.

-   [ ] **Step 2: Update imports.** In `WorkflowEditor.tsx` change `import ReactFlow, { MiniMap, Controls, Background, Elements, Position } from "react-flow-renderer"` to:

```tsx
import ReactFlow, {
    MiniMap,
    Controls,
    Background,
    Position,
    Node,
    Edge,
    useNodesState,
    useEdgesState,
    ReactFlowProvider,
} from "reactflow";
import "reactflow/dist/style.css";
```

-   [ ] **Step 3: Split `elements` into `nodes` + `edges`.** Rewrite `workflowPipelineToElements(...)` to return `{ nodes: Node[]; edges: Edge[] }` (same node objects, but node type carries `position`, and edges use `{ id, source, target, type: "smoothstep" }`). Feed them to `<ReactFlow nodes={nodes} edges={edges} onInit={(inst)=>inst.fitView()} snapToGrid snapGrid={[15,15]} fitView>` (replace `elements={...}` and `onLoad` with `nodes`/`edges` and `onInit`). Wrap the component tree in `<ReactFlowProvider>`.

-   [ ] **Step 4: Keep dark-mode theming.** Preserve the `document.body.classList.contains("awsui-dark-mode")` logic; verify `theme.css` ReactFlow selectors still target `.react-flow__*` classes (reactflow 11 keeps these class names — no change expected; note if any differ).

-   [ ] **Step 5: Update the snapshot test.** In `WorkflowEditor.test.tsx`, delete the stale snapshot file `__snapshots__/WorkflowEditor.test.tsx.snap` if present, and re-run to regenerate. Run: `cd web && npx jest WorkflowEditor -u`
        Expected: PASS with a fresh snapshot.

-   [ ] **Step 6: Build + full test.** Run: `cd web && npm run build && npx jest`
        Expected: build succeeds; tests pass.

-   [ ] **Step 7: Commit.**

```bash
git add web/package.json web/package-lock.json web/src/components/interactive/WorkflowEditor.tsx web/src/components/interactive/WorkflowEditor.test.tsx web/src/components/interactive/__snapshots__ web/src/styles/theme.css
git commit -m "feat(web): migrate WorkflowEditor to reactflow v11 (React 18)"
```

### Task 1.4: Dev smoke-test (StrictMode double-invoke)

**Files:** none (verification task).

-   [ ] **Step 1: Start dev server.** Run: `cd web && npm run start` (port 3001).
-   [ ] **Step 2: Load the app** against a dev/prod14 API (`DEV_API_ENDPOINT` in `config.ts`), sign in, and click through Assets, Databases, Pipelines, Workflows, the 3D viewer, and the map. Watch the console for React errors, double-fetch loops, or viewer/map init failures caused by StrictMode double-invoke.
-   [ ] **Step 3: Record** any dev-only warnings in the PR description; fix any that cause functional breakage (e.g. an effect that starts a timer without cleanup). Do not chase benign dev double-logs.
-   [ ] **Step 4: Commit** any fixes with `fix(web): <effect> idempotent under StrictMode`.

---

# Phase 2 — Foundation (libraries, types, services, query hooks, permission hook, shared primitives)

**Deliverable:** the feature module scaffold compiles; services + hooks + primitives are unit-tested; nothing is wired into routes yet.

### Task 2.1: Install + configure Tailwind, Radix, TanStack, editors, forms

**Files:**

-   Modify: `web/package.json`
-   Create: `web/tailwind.config.js`, `web/postcss.config.js`, `web/src/styles/tailwind.css`
-   Modify: `web/src/index.tsx` (import tailwind.css once, after Cloudscape styles)
-   Modify: `web/vite.config.ts` (only if PostCSS not auto-detected)

**Interfaces:**

-   Produces: Tailwind utilities usable under `features/orchestration/**` without affecting other pages.

-   [ ] **Step 1: Add dependencies.** In `web/package.json` add: `"@tanstack/react-query": "^5.59.0"`, `"@tanstack/react-table": "^8.20.5"`, `"@rjsf/core": "^5.21.2"`, `"@rjsf/validator-ajv8": "^5.21.2"`, `"@rjsf/utils": "^5.21.2"`, `"@monaco-editor/react": "^4.6.0"`, `"react-hook-form": "^7.53.0"`, `"zod": "^3.23.8"`, `"@hookform/resolvers": "^3.9.0"`, `"@radix-ui/react-dialog": "^1.1.2"`, `"@radix-ui/react-dropdown-menu": "^2.1.2"`, `"@radix-ui/react-context-menu": "^2.2.2"`, `"@radix-ui/react-popover": "^1.1.2"`, `"@radix-ui/react-tabs": "^1.1.1"`, `"@radix-ui/react-tooltip": "^1.1.3"`. Dev: `"tailwindcss": "^3.4.14"`, `"postcss": "^8.4.47"`, `"autoprefixer": "^10.4.20"`. Run `cd web && npm install`.

-   [ ] **Step 2: Create `web/tailwind.config.js`.**

```js
/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["selector", ".awsui-dark-mode"],
    content: ["./src/features/orchestration/**/*.{ts,tsx}"],
    corePlugins: { preflight: false },
    theme: { extend: {} },
    plugins: [],
};
```

-   [ ] **Step 3: Create `web/postcss.config.js`.**

```js
module.exports = { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

-   [ ] **Step 4: Create `web/src/styles/tailwind.css`** with the three `@tailwind` layers (base, components, utilities) plus a copyright comment. Import it once in `web/src/index.tsx` AFTER any Cloudscape global-style import.

-   [ ] **Step 5: Build + verify no regression.** Run: `cd web && npm run build`. Then `npm run start`, open an existing Cloudscape page (Assets), and confirm no visual change (preflight off means no reset bleed).

-   [ ] **Step 6: Commit.**

```bash
git add web/package.json web/package-lock.json web/tailwind.config.js web/postcss.config.js web/src/styles/tailwind.css web/src/index.tsx
git commit -m "chore(web): add Tailwind (scoped) + TanStack/Radix/rjsf/monaco/rhf deps"
```

### Task 2.2: V2 domain types

**Files:**

-   Create: `web/src/features/orchestration/types.ts`

**Interfaces:**

-   Produces: exported types consumed by every later task: `ExecutionType`, `WaitForCallback`, `InputFileArity`, `ConcurrencyRestriction`, `OutputLocationType`, `ConfigFormat`, `TagType`, `Pipeline`, `PipelineExecutionConfig`, `PipelineSystemConfig`, `Template`, `TagSchemaField`, `Workflow`, `WorkflowSystemConfig`, `SpecifiedPipelineRef`, `WorkflowTrigger`, `Execution`, `ExecutionDetail`, `ExecuteRequest`, `PipelineExecutionParameters`, `ExecuteInputFile`.

-   [ ] **Step 1: Write the types file.** Define (exact names/enums per Global Constraints):

```ts
export type ExecutionType = "Lambda" | "SQS" | "EventBridge" | "DeadlineCloud";
export type WaitForCallback = "Enabled" | "Disabled";
export type InputFileArity = "none" | "one" | "multi";
export type ConcurrencyRestriction = "none" | "perAsset" | "perInputFile";
export type OutputLocationType = "asset" | "none";
export type ConfigFormat = "json" | "yaml" | "openjd" | "xml" | "raw";
export type TagType = "string" | "integer" | "number" | "boolean" | "string-list" | "enum";
export type ExecutionStatus =
    | "NEW"
    | "RUNNING"
    | "SUCCEEDED"
    | "FAILED"
    | "ABORTED"
    | "TIMED_OUT"
    | "COMPLETE";

export interface PipelineExecutionConfig {
    executionType: ExecutionType;
    waitForCallback?: WaitForCallback;
    taskTimeout?: string;
    taskHeartbeatTimeout?: string;
    lambda?: { resourceId?: string };
    sqs?: { queueUrl?: string };
    eventBridge?: { busArn?: string; source?: string; detailType?: string };
    deadlineCloud?: {
        farmId?: string;
        queueId?: string;
        storageProfileId?: string;
        priority?: number;
        maxRetriesPerTask?: number;
        maxFailedTasksCount?: number;
        templateType?: string;
    };
}
export interface PipelineSystemConfig {
    inputFileArity?: InputFileArity;
    assetScope?: Record<string, boolean>;
    metadataInputs?: Record<string, boolean>;
    requireTemplate?: boolean;
    allowCustomTemplateOverride?: boolean;
    auxPreviewPipelineSuffix?: string;
    inputFileFilters?: { allow?: string[]; exclude?: string[] };
}
export interface Pipeline {
    databaseId: string;
    pipelineId: string;
    pipelineName: string;
    category?: string;
    description?: string;
    enabled?: boolean;
    archived?: boolean;
    executionConfig: PipelineExecutionConfig;
    systemConfig?: PipelineSystemConfig;
}
export interface TagSchemaField {
    tagKey: string;
    type: TagType;
    required?: boolean;
    default?: any;
    label?: string;
    description?: string;
    enumValues?: any[];
}
export interface Template {
    databaseId: string;
    pipelineId: string;
    templateId: string;
    templateName: string;
    description?: string;
    configFormat: ConfigFormat;
    configBody?: string;
    webFormJson?: string;
    allowCustomEdit?: boolean;
    inputInstructions?: string;
    overrides?: Record<string, any>;
    tagSchema?: TagSchemaField[];
}
export interface SpecifiedPipelineRef {
    pipelineId: string;
    pipelineDatabaseId?: string;
    jobName?: string;
    defaultTemplateId?: string;
}
export interface WorkflowSystemConfig {
    inputFileArity?: InputFileArity;
    assetScope?: Record<string, boolean>;
    metadataInputs?: Record<string, boolean>;
    inputFileFilters?: { allow?: string[]; exclude?: string[] };
    concurrencyRestriction?: ConcurrencyRestriction;
    outputTarget?: { locationType?: OutputLocationType; allowOverride?: boolean };
}
export interface Workflow {
    databaseId: string;
    workflowId: string;
    workflowName: string;
    category?: string;
    description?: string;
    subDashboardUrl?: string;
    enabled?: boolean;
    archived?: boolean;
    specifiedPipelines: SpecifiedPipelineRef[];
    systemConfig?: WorkflowSystemConfig;
    workflow_arn?: string;
    aslSchemaVersion?: string;
    warnings?: string[];
}
export interface WorkflowTrigger {
    triggerType: "fileUpload";
    enabled?: boolean;
    inputFileFilters?: { allow?: string[]; exclude?: string[] };
    defaultTemplateIds?: Record<string, string>;
}
export interface ExecuteInputFile {
    databaseId: string;
    assetId: string;
    relativeFileKey: string;
    versionId?: string;
}
export interface PipelineExecutionParameters {
    templateId?: string;
    templateTags?: { key: string; value: any }[];
    customTemplateOverride?: string;
}
export interface ExecuteRequest {
    inputFiles: ExecuteInputFile[];
    outputAssetId?: string;
    outputDatabaseId?: string;
    pipelineExecutionParameters?: Record<string, PipelineExecutionParameters>;
    executionGroupId?: string;
    triggerType?: "manual" | "fileUpload";
}
export interface Execution {
    workflowExecutionId: string;
    workflowId: string;
    workflowDatabaseId: string;
    executionStatus: ExecutionStatus;
    triggeredByUserId?: string;
    triggerType?: string;
    executionStartDate?: string;
    executionStopDate?: string;
    executionGroupId?: string;
    executionError?: string;
}
export interface ExecutionDetail extends Execution {
    pipelines?: any[];
    inputFiles?: any[];
    outputs?: { files?: any[]; metadata?: any[]; results?: any[] };
    truncatedCollections?: string[];
}
```

(Add the copyright header. Loosely type `pipelines`/`outputs` as `any[]` — the detail view shapes them; mirror the existing codebase's pragmatic `any`.)

-   [ ] **Step 2: Typecheck.** Run: `cd web && npx tsc --noEmit`
        Expected: no errors in `types.ts`.

-   [ ] **Step 3: Commit.**

```bash
git add web/src/features/orchestration/types.ts
git commit -m "feat(web): V2 orchestration domain types"
```

### Task 2.3: Service helper (`toTuple` / `normalizeList`)

**Files:**

-   Create: `web/src/features/orchestration/api/client.ts`
-   Test: `web/src/features/orchestration/api/client.test.ts`

**Interfaces:**

-   Produces: `toTuple<T>(fn: () => Promise<any>): Promise<[boolean, T | string]>`; `unwrapMessage(resp: any): any` (returns `resp.message ?? resp`); `pageAll(fetchPage: (token?: string) => Promise<any>, itemsKey?: string): Promise<any[]>`.

-   [ ] **Step 1: Write the failing test.**

```ts
import { unwrapMessage, toTuple } from "./client";
describe("orchestration api client helpers", () => {
    it("unwrapMessage returns .message when present", () => {
        expect(unwrapMessage({ message: { a: 1 } })).toEqual({ a: 1 });
        expect(unwrapMessage({ a: 1 })).toEqual({ a: 1 });
    });
    it("toTuple returns [true, data] on success", async () => {
        const r = await toTuple(async () => ({ message: "ok" }));
        expect(r).toEqual([true, "ok"]);
    });
    it("toTuple returns [false, message] on throw", async () => {
        const r = await toTuple(async () => {
            const e: any = new Error("boom");
            throw e;
        });
        expect(r[0]).toBe(false);
        expect(r[1]).toBe("boom");
    });
});
```

-   [ ] **Step 2: Run to verify it fails.** Run: `cd web && npx jest api/client -v` — Expected: FAIL (module not found).

-   [ ] **Step 3: Implement `client.ts`.**

```ts
export function unwrapMessage(resp: any): any {
    return resp && typeof resp === "object" && "message" in resp ? resp.message : resp;
}
export async function toTuple<T = any>(fn: () => Promise<any>): Promise<[boolean, T | string]> {
    try {
        return [true, unwrapMessage(await fn()) as T];
    } catch (e: any) {
        console.log(e);
        return [false, e?.message || "Request failed"];
    }
}
export async function pageAll(
    fetchPage: (token?: string) => Promise<any>,
    itemsKey = "Items"
): Promise<any[]> {
    let out: any[] = [];
    let token: string | undefined = undefined;
    do {
        const resp = await fetchPage(token);
        const msg = unwrapMessage(resp);
        out = out.concat(msg?.[itemsKey] || []);
        token = msg?.NextToken || resp?.NextToken;
    } while (token);
    return out;
}
```

-   [ ] **Step 4: Run to verify it passes.** Run: `cd web && npx jest api/client -v` — Expected: PASS.
-   [ ] **Step 5: Commit.**

```bash
git add web/src/features/orchestration/api/client.ts web/src/features/orchestration/api/client.test.ts
git commit -m "feat(web): orchestration service helpers (toTuple/unwrapMessage/pageAll)"
```

### Task 2.4: Pipeline + template + tag-schema services

**Files:**

-   Create: `web/src/features/orchestration/api/pipelines.ts`
-   Test: `web/src/features/orchestration/api/pipelines.test.ts`

**Interfaces:**

-   Consumes: `apiClient` (`web/src/services/apiClient`), `toTuple`/`pageAll`, types.
-   Produces (all return `Promise<[boolean, any]>` unless noted):
    `listPipelines(databaseId?: string, includeArchived?: boolean)` → GET `pipelines` (no db) or `database/{db}/pipelines`; returns `[true, Pipeline[]]`.
    `getPipeline(databaseId, pipelineId)` → GET `database/{db}/pipelines/{id}`.
    `createPipeline(body: Pipeline)` → POST `database/{db}/pipelines`.
    `updatePipeline(databaseId, pipelineId, body)` → PUT `database/{db}/pipelines/{id}`.
    `archivePipeline(databaseId, pipelineId)` → DELETE `database/{db}/pipelines/{id}`.
    `listTemplates(databaseId, pipelineId)` → GET `database/{db}/pipelines/{id}/templates`.
    `getTemplate(databaseId, pipelineId, templateId)` → GET `.../templates/{templateId}`.
    `createTemplate(databaseId, pipelineId, body: Template)` → POST `.../templates`.
    `updateTemplate(databaseId, pipelineId, templateId, body)` → PUT `.../templates/{templateId}`.
    `archiveTemplate(databaseId, pipelineId, templateId)` → DELETE `.../templates/{templateId}`.
    `getTagSchema(databaseId, pipelineId, templateId)` → GET `.../templates/{templateId}/tagSchema`.
    `setTagSchema(databaseId, pipelineId, templateId, fields: TagSchemaField[])` → PUT `.../templates/{templateId}/tagSchema`.

-   [ ] **Step 1: Write the failing test** (mock `apiClient`; verify each fn calls the right path/verb and unwraps). Example:

```ts
jest.mock("../../../services/apiClient", () => ({
    apiClient: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn() },
}));
import { apiClient } from "../../../services/apiClient";
import { listPipelines, createPipeline, archiveTemplate } from "./pipelines";
describe("pipelines service", () => {
    beforeEach(() => jest.clearAllMocks());
    it("listPipelines(db) hits database/{db}/pipelines", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({
            message: { Items: [{ pipelineId: "p1" }] },
        });
        const r = await listPipelines("db1");
        expect(apiClient.get).toHaveBeenCalledWith("database/db1/pipelines", expect.anything());
        expect(r).toEqual([true, [{ pipelineId: "p1" }]]);
    });
    it("createPipeline posts to database/{db}/pipelines", async () => {
        (apiClient.post as jest.Mock).mockResolvedValue({ message: { pipelineId: "p1" } });
        const r = await createPipeline({ databaseId: "db1" } as any);
        expect(apiClient.post).toHaveBeenCalledWith("database/db1/pipelines", {
            body: { databaseId: "db1" },
        });
        expect(r[0]).toBe(true);
    });
    it("archiveTemplate deletes the template path", async () => {
        (apiClient.del as jest.Mock).mockResolvedValue({ message: "archived" });
        await archiveTemplate("db1", "p1", "t1");
        expect(apiClient.del).toHaveBeenCalledWith("database/db1/pipelines/p1/templates/t1", {});
    });
});
```

-   [ ] **Step 2: Run — Expected FAIL.** Run: `cd web && npx jest api/pipelines -v`
-   [ ] **Step 3: Implement `pipelines.ts`.** Each fn uses `toTuple(() => apiClient.<verb>(path, opts))`; list fns use `pageAll`. Use `queryStringParameters: { includeArchived: "true" }` when `includeArchived`. No leading slash on paths.
-   [ ] **Step 4: Run — Expected PASS.** Run: `cd web && npx jest api/pipelines -v`
-   [ ] **Step 5: Commit.**

```bash
git add web/src/features/orchestration/api/pipelines.ts web/src/features/orchestration/api/pipelines.test.ts
git commit -m "feat(web): pipeline/template/tag-schema services"
```

### Task 2.5: Workflow + trigger services

**Files:**

-   Create: `web/src/features/orchestration/api/workflows.ts`
-   Test: `web/src/features/orchestration/api/workflows.test.ts`

**Interfaces:**

-   Produces: `listWorkflows(databaseId?, includeArchived?)`, `getWorkflow(db,id)`, `createWorkflow(body: Workflow)` → POST `database/{db}/workflows`, `updateWorkflow(db,id,body)` → PUT `database/{db}/workflows/{id}`, `archiveWorkflow(db,id)` → DELETE same, `listTriggers(db,id)` → GET `database/{db}/workflows/{id}/triggers`, `setTrigger(db,id,triggerType,body)` → PUT `.../triggers/{triggerType}`, `deleteTrigger(db,id,triggerType)` → DELETE `.../triggers/{triggerType}`.

-   [ ] **Step 1: Write failing tests** (same mock pattern as 2.4, one per fn asserting path+verb).
-   [ ] **Step 2: Run — Expected FAIL.** `cd web && npx jest api/workflows -v`
-   [ ] **Step 3: Implement `workflows.ts`** with `toTuple`/`pageAll`.
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): workflow + trigger services`.

### Task 2.6: Execution services

**Files:**

-   Create: `web/src/features/orchestration/api/executions.ts`
-   Test: `web/src/features/orchestration/api/executions.test.ts`

**Interfaces:**

-   Produces:
    `executeWorkflow(workflowDatabaseId, workflowId, body: ExecuteRequest)` → POST `workflows/{wdb}/{wid}/execute`.
    `listExecutionsGlobal(params?: Record<string,string>)` → GET `workflows/executions` (queryStringParameters=params).
    `listExecutionsForAsset(databaseId, assetId, params?)` → GET `database/{db}/assets/{assetId}/workflows/executions`.
    `getExecutionDetails(executionId)` → GET `workflows/executions/{id}/details`.
    `getExecutionLogs(executionId, params?)` → GET `workflows/executions/{id}/logs`.
    `abortExecution(executionId, groupId?)` → DELETE `workflows/executions/{id}` (queryStringParameters `{ groupId }` when set).
    `rerunExecution(executionId, executionGroupId?)` → POST `workflows/executions/{id}/rerun` (body `{ executionGroupId }` when set).
    `permanentDeleteExecution(executionId)` → DELETE `workflows/executions/{id}/permanent` (body `{ confirmDelete: true }`).

-   [ ] **Step 1: Write failing tests** asserting each path/verb, plus:

```ts
it("abortExecution with groupId sends queryStringParameters", async () => {
    (apiClient.del as jest.Mock).mockResolvedValue({ message: "Execution aborted" });
    await abortExecution("e1", "g1");
    expect(apiClient.del).toHaveBeenCalledWith("workflows/executions/e1", {
        queryStringParameters: { groupId: "g1" },
    });
});
it("permanentDelete always sends confirmDelete true", async () => {
    (apiClient.del as jest.Mock).mockResolvedValue({ message: "deleted" });
    await permanentDeleteExecution("e1");
    expect(apiClient.del).toHaveBeenCalledWith("workflows/executions/e1/permanent", {
        body: { confirmDelete: true },
    });
});
```

-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement `executions.ts`.**
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): execution services (list/details/logs/abort/rerun/permanent/execute)`.

### Task 2.7: Permission hook `useAllowedRoutes`

**Files:**

-   Create: `web/src/features/orchestration/permissions/useAllowedRoutes.ts`
-   Test: `web/src/features/orchestration/permissions/useAllowedRoutes.test.tsx`

**Interfaces:**

-   Consumes: `fetchAllowedApiRoutes` from `web/src/services/APIService` (returns `[true, { routes: {path, methods, category}[], userId }]`).
-   Produces: `useAllowedRoutes()` → `{ loading: boolean; can: (method: string, pathTemplate: string) => boolean }`. `can` matches the concrete method against the allowed route whose `path` template matches `pathTemplate` by segment (`{param}` matches any single segment). Fail-closed: while loading or on error, `can` returns `false` EXCEPT it returns `true` when the allowed set genuinely contains the route. (Provide a `canOrDefault(method, path, dflt=false)` variant if needed.)

-   [ ] **Step 1: Write the failing test** (mock `APIService.fetchAllowedApiRoutes`; render the hook via `@testing-library/react` `renderHook`):

```ts
it("can() is true for an allowed method+path template and false otherwise", async () => {
    (APIService.fetchAllowedApiRoutes as jest.Mock).mockResolvedValue([
        true,
        {
            routes: [
                {
                    path: "/workflows/executions/{executionId}/logs",
                    methods: ["GET"],
                    category: "workflow",
                },
            ],
            userId: "u",
        },
    ]);
    const { result } = renderHook(() => useAllowedRoutes());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.can("GET", "/workflows/executions/{executionId}/logs")).toBe(true);
    expect(result.current.can("DELETE", "/workflows/executions/{executionId}/permanent")).toBe(
        false
    );
});
```

-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement the hook** with a module-level cache promise (fetch once per SPA session), a segment-matcher (`{x}` → wildcard segment), and `can(method, path)` comparing uppercase method membership.
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): useAllowedRoutes Tier-1 permission hook`.

### Task 2.8: TanStack Query hooks + key factory

**Files:**

-   Create: `web/src/features/orchestration/api/queries.ts`
-   Modify: `web/src/App.tsx` (wrap tree in `QueryClientProvider`)
-   Test: `web/src/features/orchestration/api/queries.test.tsx`

**Interfaces:**

-   Produces: `qk` (key factory: `qk.pipelines(db?, filters?)`, `qk.pipeline(db,id)`, `qk.templates(db,pid)`, `qk.workflows(db?, filters?)`, `qk.triggers(db,id)`, `qk.executions(scope, filters?)`, `qk.execution(id)`, `qk.allowedRoutes()`); hooks: `usePipelines`, `usePipeline`, `useCreatePipeline`, `useUpdatePipeline`, `useArchivePipeline`, `useTemplates`, `useTemplateMutations`, `useWorkflows`, `useWorkflow`, `useWorkflowMutations`, `useTriggers`, `useExecutions(scope, filters, { pollWhileRunning })`, `useExecutionDetails(id)`, `useExecuteWorkflow`, `useExecutionActions` (abort/rerun/permanentDelete). Each mutation `onSuccess` invalidates the relevant key(s).
-   Consumes: services from 2.4–2.6.

-   [ ] **Step 1: Wrap the app.** In `App.tsx` create a module-level `const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } })` and wrap the existing app tree in `<QueryClientProvider client={queryClient}>`.

-   [ ] **Step 2: Write the failing test** for smart polling:

```ts
it("useExecutions sets refetchInterval only while a row is non-terminal", () => {
    const rows = [{ executionStatus: "RUNNING" }];
    expect(computeRefetchInterval(rows)).toBe(5000);
    expect(computeRefetchInterval([{ executionStatus: "SUCCEEDED" }])).toBe(false);
});
```

(Export the pure helper `computeRefetchInterval(rows): number | false` from `queries.ts` for testability.)

-   [ ] **Step 3: Run — Expected FAIL.**
-   [ ] **Step 4: Implement `queries.ts`.** `computeRefetchInterval` returns `5000` if any row status ∈ `{NEW, RUNNING}` else `false`. Each `useQuery` uses `qk.*`; list hooks pass filters into the key. `useExecutions` sets `refetchInterval: (q) => computeRefetchInterval(q.state.data ?? [])`. Mutations call the service then `queryClient.invalidateQueries({ queryKey: qk.<domain>() })`.
-   [ ] **Step 5: Run — Expected PASS.** `cd web && npx jest api/queries -v`
-   [ ] **Step 6: Commit** `feat(web): TanStack Query hooks + key factory + QueryClientProvider`.

### Task 2.9: Shared primitives — StatusBadge, Dialog, Drawer, ContextMenu, FilterBar, Stepper

**Files:**

-   Create: `web/src/features/orchestration/components/StatusBadge.tsx`, `Dialog.tsx`, `Drawer.tsx`, `ContextMenu.tsx`, `FilterBar.tsx`, `Stepper.tsx`
-   Test: `web/src/features/orchestration/components/StatusBadge.test.tsx`

**Interfaces:**

-   Produces: `<StatusBadge status={ExecutionStatus}/>` (distinct colors/icons for SUCCEEDED/RUNNING/NEW/FAILED/ABORTED/TIMED_OUT/COMPLETE); `<Dialog>` (Radix Dialog wrapper, Tailwind); `<Drawer>` (Radix Dialog as side sheet); `<ContextMenu items={{label, onSelect, disabled, hidden}[]}>`; `<FilterBar>` (text + select facets, controlled); `<Stepper steps={{id,label}[]} current={string}>`.

-   [ ] **Step 1: Write the failing test** for StatusBadge:

```tsx
it("renders ABORTED distinctly from FAILED", () => {
    const { rerender } = render(<StatusBadge status="ABORTED" />);
    expect(screen.getByText(/aborted/i)).toBeInTheDocument();
    rerender(<StatusBadge status="FAILED" />);
    expect(screen.getByText(/failed/i)).toBeInTheDocument();
});
```

-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement the six primitives** (Tailwind classes + Radix; each with copyright header; `ContextMenu` filters out `hidden` items and disables `disabled` ones).
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): shared orchestration UI primitives`.

### Task 2.10: DataTable, CategoryGroupedList, ConfigEditor, DynamicTagForm, QuickView

**Files:**

-   Create: `web/src/features/orchestration/components/DataTable.tsx`, `CategoryGroupedList.tsx`, `ConfigEditor.tsx`, `DynamicTagForm.tsx`, `QuickView.tsx`
-   Test: `web/src/features/orchestration/components/DynamicTagForm.test.tsx`, `DataTable.test.tsx`

**Interfaces:**

-   Produces:
    `<DataTable columns rows onRowContextMenu getRowActions pageSize sorting filtering />` (TanStack Table v8 headless + Tailwind; column sort, client filter, pagination; exposes right-click via `onRowContextMenu`).
    `<CategoryGroupedList items groupBy renderItem />` (collapsible category sections).
    `<ConfigEditor value language readOnly onChange height />` (lazy Monaco; `language` maps `openjd`→`yaml`, `raw`→`plaintext`).
    `<DynamicTagForm schema uiSchema formData onChange onSubmit />` where `schema: TagSchemaField[]` is converted to JSON Schema internally; exports pure `tagSchemaToJsonSchema(fields): {schema, uiSchema}` and `formDataToTags(data): {key,value}[]`.
    `<QuickView open onClose title>` (Drawer-based).

-   [ ] **Step 1: Write failing tests.** For DynamicTagForm test the pure converters:

```ts
it("tagSchemaToJsonSchema maps the 6 types + required + enum", () => {
    const { schema } = tagSchemaToJsonSchema([
        { tagKey: "env", type: "enum", required: true, enumValues: ["a", "b"] },
        { tagKey: "n", type: "integer" },
        { tagKey: "flag", type: "boolean" },
        { tagKey: "list", type: "string-list" },
    ]);
    expect(schema.required).toContain("env");
    expect(schema.properties.env.enum).toEqual(["a", "b"]);
    expect(schema.properties.n.type).toBe("integer");
    expect(schema.properties.flag.type).toBe("boolean");
    expect(schema.properties.list.type).toBe("array");
});
it("formDataToTags flattens to {key,value}[]", () => {
    expect(formDataToTags({ env: "a", n: 3 })).toEqual([
        { key: "env", value: "a" },
        { key: "n", value: 3 },
    ]);
});
```

For DataTable, render 30 rows with pageSize 10 and assert only 10 visible + a next-page control.

-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement the five components.** `tagSchemaToJsonSchema`: `string`→`{type:"string"}`, `integer`→`{type:"integer"}`, `number`→`{type:"number"}`, `boolean`→`{type:"boolean"}`, `string-list`→`{type:"array",items:{type:"string"}}`, `enum`→`{type:"string",enum:enumValues}`; `required` array from `required===true`; `default` copied; `title`=label, `description`. `ConfigEditor` uses `React.lazy(() => import("@monaco-editor/react"))` wrapped in `<Suspense>`.
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): DataTable, ConfigEditor, DynamicTagForm, CategoryGroupedList, QuickView`.

---

# Phase 3 — Pipelines

**Deliverable:** a working Pipelines page (behind a temporary dev route) with CRUD + template/tag-schema editing, unit-tested.

### Task 3.1: PipelineForm (create/edit)

**Files:**

-   Create: `web/src/features/orchestration/pipelines/PipelineForm.tsx`
-   Create: `web/src/features/orchestration/pipelines/pipelineValidation.ts`
-   Test: `web/src/features/orchestration/pipelines/pipelineValidation.test.ts`

**Interfaces:**

-   Consumes: types, `useCreatePipeline`/`useUpdatePipeline`, `Dialog`, `ConfigEditor`, feature flags from `appCache.getItem("config").featuresEnabled`.
-   Produces: `<PipelineForm mode="create"|"edit" databaseId initial? onDone/>`; pure `pipelineSchema` (zod) + `validatePipeline(values): {ok, errors}` enforcing: id pattern when provided, taskTimeout/heartbeat 1..604800, per-type required sub-fields (SQS.queueUrl, EB.busArn/source/detailType, DeadlineCloud.farmId/queueId + forced waitForCallback=Enabled), and executionType ∈ the 4 enum.

-   [ ] **Step 1: Write the failing test** for `validatePipeline`:

```ts
it("rejects taskTimeout over one week", () => {
    const r = validatePipeline({
        executionConfig: { executionType: "Lambda", taskTimeout: "999999999" },
    } as any);
    expect(r.ok).toBe(false);
});
it("DeadlineCloud requires farmId+queueId and Enabled callback", () => {
    const r = validatePipeline({
        pipelineName: "x",
        executionConfig: { executionType: "DeadlineCloud", waitForCallback: "Disabled" },
    } as any);
    expect(r.ok).toBe(false);
});
it("SQS requires queueUrl", () => {
    const r = validatePipeline({
        pipelineName: "x",
        executionConfig: { executionType: "SQS" },
    } as any);
    expect(r.ok).toBe(false);
});
```

-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement `pipelineValidation.ts`** (zod + a `validatePipeline` wrapper returning `{ok, errors}`), then `PipelineForm.tsx` (react-hook-form + zodResolver; conditional sub-blocks by executionType; DeadlineCloud option hidden unless `DEADLINECLOUD_PIPELINES` present and `GOVCLOUD` absent; Lambda auto-provision disclosure text when `lambda.resourceId` blank).
-   [ ] **Step 4: Run — Expected PASS.** `cd web && npx jest pipelineValidation -v`
-   [ ] **Step 5: Component test** — render `<PipelineForm mode="create" databaseId="db1"/>`, select DeadlineCloud with the feature flag mocked present, assert farm/queue fields appear and waitForCallback is locked to Enabled. Commit.

```bash
git add web/src/features/orchestration/pipelines/PipelineForm.tsx web/src/features/orchestration/pipelines/pipelineValidation.ts web/src/features/orchestration/pipelines/pipelineValidation.test.ts
git commit -m "feat(web): pipeline create/edit form + validation"
```

### Task 3.2: TagSchemaBuilder + TemplateEditor

**Files:**

-   Create: `web/src/features/orchestration/pipelines/TagSchemaBuilder.tsx`
-   Create: `web/src/features/orchestration/pipelines/TemplateEditor.tsx`
-   Test: `web/src/features/orchestration/pipelines/TagSchemaBuilder.test.tsx`

**Interfaces:**

-   Consumes: `ConfigEditor`, `DynamicTagForm` (+ `tagSchemaToJsonSchema`), `useTemplates`/`useTemplateMutations`, types.
-   Produces: `<TagSchemaBuilder value={TagSchemaField[]} onChange/>` (add/edit/remove rows; rejects reserved system-tag keys via a passed-in `reservedKeys: Set<string>` or a bundled constant list); `<TemplateEditor databaseId pipelineId/>` (list templates; create/edit modal with templateName/description/configFormat/configBody(Monaco)/inputInstructions/allowCustomEdit/overrides + embedded TagSchemaBuilder + a live `DynamicTagForm` preview; archive).

-   [ ] **Step 1: Write the failing test** — render `<TagSchemaBuilder value={[]} onChange={fn}/>`, add a field with `tagKey="executionId"` (a reserved system key), assert a validation message appears and `onChange` is not called with the invalid row (or the row is flagged invalid).
-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** both components. Bundle the reserved system-tag key list (copy from `backend/backend/common/workflows/templateTags.py` `SYSTEM_TAG_NAMES` into a TS constant `RESERVED_TAG_KEYS`; also reject keys starting with `metadata_`).
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): template editor + tag-schema builder`.

### Task 3.3: PipelinesPage

**Files:**

-   Create: `web/src/features/orchestration/pipelines/PipelinesPage.tsx`
-   Create: `web/src/pages/PipelinesPage2.tsx` (thin wrapper)
-   Modify: `web/src/routes.tsx` (temporary dev route `/pipelines2` — final route swap in Phase 7)
-   Test: `web/src/features/orchestration/pipelines/PipelinesPage.test.tsx`

**Interfaces:**

-   Consumes: `usePipelines`, `CategoryGroupedList`, `FilterBar`, `ContextMenu`, `useAllowedRoutes`, `PipelineForm`, `TemplateEditor`.
-   Produces: `<PipelinesPage databaseId?/>` rendering category-grouped pipeline cards + filter bar + Create button (gated by `can("POST","/database/{databaseId}/pipelines")`) + per-card Edit/Templates/Archive (gated). Archived hidden unless the includeArchived toggle is on.

-   [ ] **Step 1: Write the failing test** — mock `usePipelines` to return 3 pipelines across 2 categories; render; assert 2 category groups and 3 cards; mock `useAllowedRoutes.can` to deny POST and assert the Create button is absent/disabled.
-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** the page + thin wrapper; add the temp lazy route `/pipelines2`.
-   [ ] **Step 4: Run — Expected PASS.** Also `cd web && npm run build`.
-   [ ] **Step 5: Commit** `feat(web): Pipelines page (grouped, filtered, gated CRUD)`.

---

# Phase 4 — Workflows

**Deliverable:** Workflows page + builder (dnd ordering + DAG preview + validation) + triggers editor, behind a dev route.

### Task 4.1: Workflow save-validation logic

**Files:**

-   Create: `web/src/features/orchestration/workflows/workflowValidation.ts`
-   Test: `web/src/features/orchestration/workflows/workflowValidation.test.ts`

**Interfaces:**

-   Produces: `validateWorkflow(wf: Workflow, pipelinesById: Record<string, Pipeline>): { errors: string[]; warnings: string[] }` enforcing: ≥1 pipeline; `outputTarget.locationType==="none"` ⇒ `inputFileArity==="none"` (error otherwise); id pattern; a warning when a referenced pipeline is `disabled` or `archived`; a warning when a pipeline's arity is incompatible with the workflow arity.

-   [ ] **Step 1: Write the failing test.**

```ts
it("errors when results-only without arity none", () => {
    const r = validateWorkflow(
        {
            specifiedPipelines: [{ pipelineId: "p" }],
            systemConfig: { inputFileArity: "one", outputTarget: { locationType: "none" } },
        } as any,
        {}
    );
    expect(r.errors.some((e) => /inputFileArity/i.test(e))).toBe(true);
});
it("warns when a referenced pipeline is archived", () => {
    const r = validateWorkflow(
        {
            specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
            systemConfig: {},
        } as any,
        { "db:p": { archived: true } as any }
    );
    expect(r.warnings.length).toBeGreaterThan(0);
});
```

-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement.**
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): workflow save-validation`.

### Task 4.2: PipelineOrderList (dnd) + DagPreview

**Files:**

-   Create: `web/src/features/orchestration/workflows/PipelineOrderList.tsx`
-   Create: `web/src/features/orchestration/workflows/DagPreview.tsx`
-   Test: `web/src/features/orchestration/workflows/PipelineOrderList.test.tsx`

**Interfaces:**

-   Consumes: `@dnd-kit/core` + `@dnd-kit/sortable` (existing), `reactflow` v11, types, `validateWorkflow`.
-   Produces: `<PipelineOrderList value={SpecifiedPipelineRef[]} pipelineOptions templatesByPipeline onChange/>` (drag reorder, add/remove, per-card pipeline picker + defaultTemplateId + jobName, inline per-card warnings); `<DagPreview refs={SpecifiedPipelineRef[]}/>` (read-only reactflow linear graph, re-renders on order change).

-   [ ] **Step 1: Write the failing test** — render with 3 refs, assert 3 cards render in order; simulate remove of index 1 and assert `onChange` called with 2 refs in the right order. (DnD reorder can be tested via the exposed `moveItem(from,to)` helper rather than simulating pointer drag.)
-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** both. Export a pure `moveItem(list, from, to)` for testability.
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): workflow pipeline-order list (dnd) + DAG preview`.

### Task 4.3: WorkflowBuilder + TriggersEditor

**Files:**

-   Create: `web/src/features/orchestration/workflows/WorkflowBuilder.tsx`
-   Create: `web/src/features/orchestration/workflows/TriggersEditor.tsx`
-   Test: `web/src/features/orchestration/workflows/WorkflowBuilder.test.tsx`

**Interfaces:**

-   Consumes: `PipelineOrderList`, `DagPreview`, `validateWorkflow`, `useWorkflow`/`useWorkflowMutations`, `useTriggers`/trigger mutations, `usePipelines` + `useTemplates`.
-   Produces: `<WorkflowBuilder mode databaseId workflowId?/>` (top-level fields, systemConfig incl. the linked locationType↔arity control, pipeline order builder + DAG, save-time validation panel showing `errors` (block save) + `warnings` + backend `warnings[]`); `<TriggersEditor databaseId workflowId pipelineRefs/>` (list/create/enable/disable a `fileUpload` trigger with inputFileFilters + per-pipeline defaultTemplateIds).

-   [ ] **Step 1: Write the failing test** — render builder in create mode; set locationType=none and arity=one; assert the save button is disabled and the coupling error is shown; set arity=none and assert save enables.
-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** both.
-   [ ] **Step 4: Run — Expected PASS.** `cd web && npm run build`.
-   [ ] **Step 5: Commit** `feat(web): workflow builder + triggers editor`.

### Task 4.4: WorkflowsPage

**Files:**

-   Create: `web/src/features/orchestration/workflows/WorkflowsPage.tsx`
-   Create: `web/src/pages/WorkflowsPage2.tsx`
-   Modify: `web/src/routes.tsx` (temp dev route `/workflows2`)
-   Test: `web/src/features/orchestration/workflows/WorkflowsPage.test.tsx`

**Interfaces:**

-   Consumes: `useWorkflows`, `CategoryGroupedList`, `FilterBar`, `ContextMenu`, `useAllowedRoutes`; navigates to the builder route + Executions page.
-   Produces: `<WorkflowsPage databaseId?/>` — category-grouped cards (name/id/category, enabled/archived, pipeline count, execution count, Dashboard link, Execute + View-executions actions), gated CRUD.

-   [ ] **Step 1: Write the failing test** — mock `useWorkflows` (2 categories), render, assert grouping + a Dashboard link when `subDashboardUrl` set (opens new tab: `target="_blank"`), and that "View executions" navigates to `/executions?workflowId=...`.
-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** page + wrapper + temp route.
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): Workflows page (grouped, gated, exec counts)`.

---

# Phase 5 — Executions + Execute wizard

**Deliverable:** the executions board (all three contexts), quick-view, full-detail route, row actions, and the in-place execute wizard.

### Task 5.1: Template-resolution logic (5-case) for the wizard

**Files:**

-   Create: `web/src/features/orchestration/wizard/resolveTemplate.ts`
-   Test: `web/src/features/orchestration/wizard/resolveTemplate.test.ts`

**Interfaces:**

-   Produces: `resolvePipelineParams(input): { errors: string[]; params: PipelineExecutionParameters; mode: 1|2|3|4|5 }` where `input = { pipeline: Pipeline; template?: Template; templateId?: string; tags: {key,value}[]; customTemplateOverride?: string; customEditedBody?: string }`. Enforces the exact 5-case rules from the design §4.4: override requires `allowCustomTemplateOverride`; template-less override requires `requireTemplate===false`; no-template requires `requireTemplate===false`; validates required tags present, unmatched `{{tag}}` in the effective body, reserved-key collisions. Also `findUnmatchedTags(body, providedKeys, systemKeys): string[]` and `missingRequiredTags(schema, tags): string[]`.

-   [ ] **Step 1: Write the failing tests** (one per case + the 3 rejection cases + unmatched-tag detection):

```ts
it("rejects override when allowCustomTemplateOverride is false", () => {
    const r = resolvePipelineParams({
        pipeline: { systemConfig: { allowCustomTemplateOverride: false } } as any,
        templateId: "t",
        customTemplateOverride: "{}",
        tags: [],
    });
    expect(r.errors.length).toBeGreaterThan(0);
});
it("rejects template-less override when requireTemplate is true", () => {
    const r = resolvePipelineParams({
        pipeline: {
            systemConfig: { allowCustomTemplateOverride: true, requireTemplate: true },
        } as any,
        customTemplateOverride: "{}",
        tags: [],
    });
    expect(r.errors.some((e) => /require/i.test(e))).toBe(true);
});
it("flags an unmatched {{tag}} in the body", () => {
    expect(
        findUnmatchedTags('{"a":"{{ missing }}"}', new Set(["provided"]), new Set(["executionId"]))
    ).toEqual(["missing"]);
});
it("case 1: templateId + tags is valid", () => {
    const r = resolvePipelineParams({
        pipeline: { systemConfig: {} } as any,
        template: { configBody: "{}", tagSchema: [] } as any,
        templateId: "t",
        tags: [],
    });
    expect(r.errors).toEqual([]);
    expect(r.mode).toBe(1);
});
```

-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** `resolveTemplate.ts` (regex `\{\{\s*([\w]+)\s*\}\}` for tags; reserved keys = the `RESERVED_TAG_KEYS` constant from Task 3.2).
-   [ ] **Step 4: Run — Expected PASS.** `cd web && npx jest resolveTemplate -v`
-   [ ] **Step 5: Commit** `feat(web): 5-case template-resolution logic`.

### Task 5.2: Execute wizard stages + wizard shell

**Files:**

-   Create: `web/src/features/orchestration/wizard/ExecuteWizard.tsx`, `WizardInputStage.tsx`, `WizardPipelineStage.tsx`, `WizardReviewStage.tsx`
-   Test: `web/src/features/orchestration/wizard/ExecuteWizard.test.tsx`

**Interfaces:**

-   Consumes: `Dialog`, `Stepper`, `ConfigEditor`, `DynamicTagForm`, `resolvePipelineParams`, `useExecuteWorkflow`, `useWorkflow`/`usePipelines`/`useTemplates`.
-   Produces: `<ExecuteWizard open onClose workflow databaseId presetAsset?/>` — in-place Radix Dialog; stage 0 inputs honoring `inputFileArity` (+ output-target when `allowOverride` or 0/multi-asset), one stage per pipeline (system vars + template select + DynamicTagForm + Monaco config with allowCustomEdit/override), review with the hard-error gate (block launch if any pipeline's `resolvePipelineParams` returns errors or inputs don't satisfy a required arity). On launch → `useExecuteWorkflow.mutateAsync` → close + surface `warnings[]`.

-   [ ] **Step 1: Write the failing test** — render with a workflow (arity `none`, one pipeline, template with a required tag); assert: opening shows the stepper; leaving the required tag empty blocks the Review "Launch" button; filling it enables it; clicking Launch calls `useExecuteWorkflow` with the expected `pipelineExecutionParameters`.
-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** the wizard + three stage components.
-   [ ] **Step 4: Run — Expected PASS.** `cd web && npm run build`.
-   [ ] **Step 5: Commit** `feat(web): in-place execute wizard (5-case aware)`.

### Task 5.3: ExecutionsBoard + row actions + quick-view

**Files:**

-   Create: `web/src/features/orchestration/executions/ExecutionsBoard.tsx`, `ExecutionRowActions.tsx`, `ExecutionQuickView.tsx`
-   Test: `web/src/features/orchestration/executions/ExecutionsBoard.test.tsx`

**Interfaces:**

-   Consumes: `DataTable`, `StatusBadge`, `ContextMenu`, `QuickView`, `useExecutions`, `useExecutionActions`, `useExecutionDetails`, `useAllowedRoutes`.
-   Produces: `<ExecutionsBoard scope={{ kind: "global" } | { kind: "workflow"; databaseId; workflowId } | { kind: "asset"; databaseId; assetId }} groupByWorkflow?/>` — current-first ordering, smart polling, filter/sort/group, row context-menu (results/abort/rerun/logs/permanent-delete gated by `can(...)`; abort only when non-terminal; group-abort variant), quick-view drawer showing overall results, and "Open full details" navigating to `/executions/{id}`.

-   [ ] **Step 1: Write the failing test** — mock `useExecutions` with mixed statuses; assert non-terminal rows sort first; mock `useAllowedRoutes.can` to deny the logs route and assert the "Logs" menu item is hidden; assert Abort is hidden for a SUCCEEDED row and shown for RUNNING.
-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** the three components. Permanent-delete opens a confirm dialog before calling the action.
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): executions board + row actions + quick-view`.

### Task 5.4: ExecutionDetailPage

**Files:**

-   Create: `web/src/features/orchestration/executions/ExecutionDetailPage.tsx`
-   Create: `web/src/pages/ExecutionDetail.tsx`
-   Modify: `web/src/routes.tsx` (temp dev route `/executions2/:executionId`)
-   Test: `web/src/features/orchestration/executions/ExecutionDetailPage.test.tsx`

**Interfaces:**

-   Consumes: `useExecutionDetails`, `ConfigEditor` (read-only), `StatusBadge`, `useAllowedRoutes`, `getExecutionLogs`.
-   Produces: `<ExecutionDetailPage executionId/>` — header (status/timing/trigger/error); tabs/sections: Inputs (files+versions), per-Pipeline timeline (status + rendered config body in read-only Monaco + template/tags/override snapshot), Outputs (files/metadata/results w/ download links), Logs (admin-gated). Shows sub-process warnings.

-   [ ] **Step 1: Write the failing test** — mock `useExecutionDetails` with 1 pipeline (rendered config + tags) and outputs; assert the config body renders (Monaco mocked), the tags snapshot shows, and the Logs tab is hidden when `can("GET",".../logs")` is false.
-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** + wrapper + temp route. Mock Monaco in the test via `jest.mock("@monaco-editor/react")`.
-   [ ] **Step 4: Run — Expected PASS.**
-   [ ] **Step 5: Commit** `feat(web): execution detail page`.

---

# Phase 6 — Asset tab migration

**Deliverable:** the asset Workflows tab uses the new asset-scoped ExecutionsBoard + in-place wizard.

### Task 6.1: Swap WorkflowTab to ExecutionsBoard

**Files:**

-   Modify: `web/src/components/asset/tabs/WorkflowTab.tsx`
-   Modify: `web/src/components/asset/ViewAsset.tsx` (Execute button opens the new wizard)
-   Test: `web/src/components/asset/tabs/WorkflowTab.test.tsx` (new)

**Interfaces:**

-   Consumes: `<ExecutionsBoard scope={{kind:"asset",databaseId,assetId}}/>`, `<ExecuteWizard presetAsset/>`.
-   Produces: the asset Workflows tab rendering the asset-scoped board (live polling) + an Execute button opening the wizard pre-scoped to the asset. Post-execute refetch handled by TanStack invalidation (remove the old `workflowRefreshTrigger` plumbing if no longer needed, or leave it inert).

-   [ ] **Step 1: Write the failing test** — render `WorkflowTab` with a mocked `useExecutions` (asset scope) returning 2 executions; assert both render and the Execute button opens the wizard.
-   [ ] **Step 2: Run — Expected FAIL.**
-   [ ] **Step 3: Implement** the swap. Keep the tab id `workflows` and the `isActive` gating. Ensure the QueryClientProvider (Task 2.8) wraps the asset page (it wraps the whole app, so yes).
-   [ ] **Step 4: Run — Expected PASS.** `cd web && npm run build`.
-   [ ] **Step 5: Commit** `feat(web): asset Workflows tab uses new ExecutionsBoard + wizard`.

---

# Phase 7 — Routes, navigation, permissions, cleanup, docs

**Deliverable:** final routes live, dead code removed, permission templates updated, docs synced.

### Task 7.1: Final routes + navigation

**Files:**

-   Modify: `web/src/routes.tsx`
-   Modify: `web/src/layout/Navigation.tsx`
-   Delete: `web/src/pages/Executions.tsx` (dead duplicate), and the temp `/pipelines2`,`/workflows2`,`/executions2` dev routes.

**Interfaces:**

-   Produces: final routes — `/pipelines` (+ `/databases/:databaseId/pipelines`) → new PipelinesPage2; `/workflows` (+ db variant + `/databases/:databaseId/workflows/create` + `/databases/:databaseId/workflows/:workflowId`) → new builder; `/executions` (global) + `/executions/:executionId` (detail) → new pages; workflow triggers sub-route. Executions nav link added in the "Orchestrate & Automate" section.

-   [ ] **Step 1: Point the existing `/pipelines` and `/workflows` routes at the new page wrappers**; add `/executions` and `/executions/:executionId` lazy routes; add the trigger sub-route. Remove temp dev routes and the dead `Executions.tsx`.
-   [ ] **Step 2: Add the Executions nav link** in `Navigation.tsx` (Pipelines/Workflows already present).
-   [ ] **Step 3: Build + run.** `cd web && npm run build` then `npm run start`; verify all three pages load, permission filtering still hides pages the mock/test user can't see, and the detail route deep-links.
-   [ ] **Step 4: Run tests.** `cd web && npx jest` — Expected: pass.
-   [ ] **Step 5: Commit** `feat(web): wire final Pipelines/Workflows/Executions routes + nav; remove dead code`.

### Task 7.1b: Delete dead old-design pages/components

Once the new pages own `/pipelines`, `/workflows`, `/executions` and the asset tab uses the new
board (Phases 3–7.1), the entire old orchestration UI cluster is dead. Reference analysis confirms
these files are referenced ONLY by each other or by the entry points the new plan repoints
(`routes.tsx`, `ViewAsset.tsx`) — so they delete together. **Delete (not deprecate) all of them.**

**Files (delete):**

-   `web/src/pages/Pipelines.tsx`, `web/src/pages/Workflows.tsx`, `web/src/pages/Executions.tsx`
-   `web/src/components/createupdate/CreatePipeline.tsx`, `web/src/components/createupdate/CreateUpdateWorkflow.tsx`
-   `web/src/components/single/ViewPipeline.tsx`
-   `web/src/components/interactive/WorkflowEditor.tsx` (+ `WorkflowEditor.test.tsx` + `__snapshots__/`) — **note:** this was migrated to reactflow v11 in Task 1.3 to keep the app building through Phases 1–6; it is deleted here now that the new `DagPreview` replaces it. (If nothing else imports reactflow after deletion, that's fine — the new `DagPreview` does.)
-   `web/src/components/selectors/WorkflowPipelineSelector.tsx`, `WorkflowSelectorWithModal.tsx`, `PipelineSelector.tsx`
-   `web/src/components/asset/tabs/WorkflowTab.tsx` (replaced in Phase 6 — if Phase 6 rewrote it in place rather than creating a new file, skip; otherwise delete the old one)
-   `web/src/context/WorkflowContext.ts`
-   `web/src/components/list/list-definitions/PipelineListDefinition.tsx`, `WorkflowListDefinition.tsx`, `WorkflowExecutionListDefinition.tsx`, `ExecutionListDefinition.tsx`
-   `web/src/components/createupdate/PipelineFormDefinition.ts` (if present)

**Interfaces:** removes the old cluster; no new exports.

-   [ ] **Step 1: Re-verify no live references remain.** For each file above run a grep and confirm the only importers are other files in this deletion list (or the already-repointed `routes.tsx`/`ViewAsset.tsx`):

```bash
cd web && for n in Pipelines Workflows Executions CreatePipeline CreateUpdateWorkflow ViewPipeline WorkflowEditor WorkflowPipelineSelector WorkflowSelectorWithModal PipelineSelector WorkflowTab WorkflowContext PipelineListDefinition WorkflowListDefinition WorkflowExecutionListDefinition ExecutionListDefinition; do echo "== $n =="; grep -rl "$n" src --include=*.tsx --include=*.ts | grep -viE "orchestration|$n\.(tsx|ts)"; done
```

Expected: only files that are themselves in the deletion list, or `routes.tsx`/`ViewAsset.tsx` (which no longer import them after 7.1/Phase 6). Resolve any straggler import before deleting.

-   [ ] **Step 2: Delete the files** (use `git rm`).
-   [ ] **Step 3: Remove now-orphaned imports** in `routes.tsx` (the old `ViewPipeline`, `CreateUpdateWorkflow` lazy imports) and anywhere the grep in Step 1 flagged.
-   [ ] **Step 4: Build + test.** Run: `cd web && npm run build && npx jest` — Expected: PASS with no unresolved-import errors. If `react-flow-renderer`/`reactflow` or `@dnd-kit` or a Cloudscape import becomes entirely unused app-wide, leave the dependency in `package.json` (harmless) unless it's `react-flow-renderer` (already removed in Task 1.3).
-   [ ] **Step 5: Commit.**

```bash
git rm <all listed files>
git commit -m "chore(web): delete dead old-design pipeline/workflow/execution UI"
```

### Task 7.2: Permission templates + seed-constraint verification

**Files:**

-   Modify: `documentation/permissionsTemplates/global-readonly.json`, `database-admin.json`, `database-user.json`, `database-readonly.json` (and any other template with a `web` constraint)
-   Check: `infra/lib/nestedStacks/auth/constructs/dynamodb-authdefaults-admin.*` and `-ro.*`

**Interfaces:**

-   Produces: `/executions` present in the `web` route-prefix lists of the relevant templates; `/workflows/executions` present in the `api` prefixes consistently.

-   [ ] **Step 1: Add `/executions`** to the `web` objectType constraint's `route__path` `starts_with` value list in each template that grants pipeline/workflow web access. Add `/workflows/executions` to the `api` prefix list where missing.
-   [ ] **Step 2: Inspect the seed default-constraint constructs** (`dynamodb-authdefaults-admin`/`-ro`). If they enumerate web-route prefixes, add `/executions`; if they reference the template files, no change needed. Record which.
-   [ ] **Step 3: Commit** `chore(auth): grant /executions web route in permission templates + seed constraints`.

### Task 7.3: Documentation

**Files:**

-   Modify: `web/CLAUDE.md`
-   Modify: `.kiro/steering/WEB_DEVELOPMENT_WORKFLOW.md`, `.kiro/steering/WEB_FRONTEND.md`
-   Modify/Create: `documentation/docusaurus-site/docs/` web pages for Pipelines/Workflows/Executions; update `permissions-model.md`
-   Modify: `documentation/docusaurus-site/sidebars.ts` if new pages added

**Interfaces:** docs reflect the shipped UI.

-   [ ] **Step 1: Update `web/CLAUDE.md`** — React 18, TS 5, Vite 8, the new libraries (TanStack Query/Table, Tailwind, Radix, @rjsf, Monaco, reactflow 11), the `features/orchestration/` map + service functions, the `useAllowedRoutes` pattern, Tailwind-scoping rules; remove the stale React-17/Vite-6/TS-4.4.4 claims and the "must use Cloudscape" rule's absolute phrasing (note: new orchestration pages use Tailwind+Radix; rest of app stays Cloudscape).
-   [ ] **Step 2: Mirror into the two Kiro `WEB_*` steering docs** (bidirectional-sync rule).
-   [ ] **Step 3: Update docusaurus** user docs for the three pages + `permissions-model.md` (`/executions` web route + Tier-1 graying note); update `sidebars.ts` if pages were added. Run: `cd documentation/docusaurus-site && npm run build` (expect success; a pre-existing unrelated broken-anchor warning is acceptable).
-   [ ] **Step 4: Commit** `docs(web): update CLAUDE.md/Kiro/docusaurus for the orchestration overhaul`.

---

# Phase 8 — Playwright live testing (moderate bulk seed)

**Deliverable:** end-to-end UI verification against prod14 with seeded data.

### Task 8.1: Bulk seed script

**Files:**

-   Create: `tools/smoketest/web_seed_bulk.py`

**Interfaces:** creates ~60–100 pipelines across several categories, ~40–60 workflows, and a few hundred executions across statuses/groups/assets, reusing the Phase-4/5/6 harness auth + endpoints.

-   [ ] **Step 1: Write the seed script** (reuse `overhaul_api_param_matrix.py`'s auth/token + `call()` helper; create pipelines in a loop across ~5 categories; create workflows referencing them; launch executions including some results-only, some multi-pipeline, some intentionally failing via `mock-fail`, and a couple of `executionGroupId` groups).
-   [ ] **Step 2: Run it.** `cd tools/smoketest && python web_seed_bulk.py` — Expected: prints counts created.
-   [ ] **Step 3: Commit** (unstaged per project preference — do NOT commit smoketest data unless asked; leave as untracked).

### Task 8.2: Playwright suite

**Files:**

-   Create: `web/e2e/orchestration.spec.ts` (+ `web/playwright.config.ts` if not present)

**Interfaces:** Playwright tests against the running app (`npm run start`) pointed at prod14.

-   [ ] **Step 1: Add Playwright** (`npm i -D @playwright/test && npx playwright install chromium`) + `playwright.config.ts` (baseURL = dev server, storageState for auth).
-   [ ] **Step 2: Write the specs** covering (one `test()` each): pipeline create/edit/archive per exec type + template + tag-schema; workflow create/edit/archive with dnd order + validation warnings + trigger assignment; execute wizard (arity none/one/multi, output override, template+tags, custom override, allowCustomEdit raw edit, hard-error gate blocks launch); executions board filter/sort/group/pagination + live status transition (RUNNING→terminal); abort (single + group), rerun, permanent-delete, logs; results quick-view + full detail (inputs/outputs/config bodies/logs visible); permission graying as the `smoke-ro` user (admin-only actions hidden); asset-tab embedded executions + wizard; dark-mode toggle + assert no Cloudscape page regressed (screenshot compare on Assets page).
-   [ ] **Step 3: Run.** `cd web && npx playwright test` — Expected: all pass (fix UI bugs found; re-run).
-   [ ] **Step 4: Commit** the specs `test(web): Playwright e2e for orchestration overhaul`.

---

## Self-review notes (author)

-   **Spec coverage:** §3 arch → Phase 1–2; §4 wizard → 5.1–5.2; §5 board/results → 5.3–5.4; §6 pipelines+templates → Phase 3; §7 workflows/triggers/executions-page → Phase 4 + 5.3; §8 asset tab → Phase 6; §9 routes/permissions/backend/CDK → 7.1–7.2; §10 phases → this structure; §11 testing → Phase 8; §12 docs → 7.3. All covered.
-   **Type consistency:** service fn names in Task 2.4–2.6 match the query-hook consumers in 2.8 and the pages; `resolvePipelineParams`/`findUnmatchedTags`/`missingRequiredTags` (5.1) match the wizard (5.2); `tagSchemaToJsonSchema`/`formDataToTags` (2.10) match TemplateEditor (3.2) and the wizard (5.2); `RESERVED_TAG_KEYS` defined in 3.2 and reused in 5.1; `computeRefetchInterval` (2.8) used by the board (5.3); `validateWorkflow` (4.1) used by builder (4.3); `validatePipeline` (3.1) used by PipelineForm.
-   **No placeholders:** each code step shows real code or exact commands; UI-composition tasks specify exact component signatures, test behaviors, and gating rules rather than full JSX (appropriate for this scale; the interfaces + tests pin the contract).
