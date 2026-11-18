# VAMS Web Frontend Development Workflow & Rules

This document provides comprehensive guidelines for developing and extending the VAMS web frontend application. Follow these rules to ensure consistency, quality, and maintainability across all frontend implementations.

> **Steering Document Sync (bidirectional):** This document (together with `WEB_FRONTEND.md`) mirrors the Claude Code steering in `web/CLAUDE.md` (and cross-cutting rules in the root `CLAUDE.md`). Its "End-to-End Testing (Playwright)" section is the Kiro counterpart for `web/e2e/CLAUDE.md`. Whenever you change a rule, pattern, or convention here, make the equivalent change in the matching `CLAUDE.md` file(s) in the same change — and whenever those `CLAUDE.md` files change, reflect it back here. Keep the two sets of documents saying the same thing.

> **Add-on steering:** an add-on's own steering lives in `.kiro/steering/ADDON_*.md` (`inclusion: fileMatch`), which loads when you edit that add-on's files; this document covers core and the lanes an add-on plugs into.

## 🏗️ **Architecture Overview**

### **Technology Stack**

| Technology       | Version/Details                                                |
| ---------------- | -------------------------------------------------------------- |
| React            | 18.3 (upgraded from 17.0.2)                                    |
| TypeScript       | ^5.0.0 (upgraded from 4.4.4)                                   |
| Vite             | ^8.0.16 (upgraded from ^6.0.0)                                 |
| UI Library       | AWS Cloudscape Design System (`@cloudscape-design/components`) |
| Auth             | AWS Amplify v6 + `@badgateway/oauth2-client` (dual-mode)       |
| Routing          | React Router v7 with HashRouter                                |
| State Management | React Context API + useReducer (NO global state libraries)     |
| API Client       | Custom fetch-based apiClient with auto auth headers            |
| Package Manager  | npm 11.10.0+ (NEVER yarn)                                      |

### **File Structure Standards**

```
web/
  package.json              # npm, React 18, Vite scripts
  e2e/                      # Playwright specs against a deployed stack
  customInstalls/           # Viewer plugin custom install scripts
  src/
    App.tsx                 # Root app shell, HashRouter, TopNavigation (Settings menu, profile menu)
    routes.tsx              # Centralized route table (core + add-on + custom), lazyWithRetry, permission filtering
    config.ts               # Static config (VAMSConfig: APP_TITLE, DEV_API_ENDPOINT)
    synonyms.tsx            # Configurable display names (Asset, Database, Comment)
    index.tsx               # Entry point, createRoot

    features/orchestration/ # Pipeline/workflow/execution management (Tailwind + Radix)
      api/                  # Services + TanStack Query hooks + qk key factory
                            #   pipelines.ts workflows.ts executions.ts assets.ts databases.ts
                            #   triggerCache.ts
      permissions/          # useAllowedRoutes.ts (Tier-1 gating), routeDisabledReason.ts (disabledReason text),
                            #   systemRecordReasons.ts (disabledReason text for system records)
      components/           # Cloudscape-free primitives (DataTable, StatusBadge, ContextMenu,
                            #   Stepper, Breadcrumb, ConfigEditor, Dialog, Callout, ToastProvider,
                            #   DisabledReason (tooltip + button for a disabled control's reason), ...)
      pipelines/            # PipelinesPage, PipelineForm (wizard), TemplateEditor, TemplateForm,
                            #   TagSchemaBuilder, TemplateOverridesEditor, pipelineValidation
      workflows/            # WorkflowsPage, WorkflowBuilder, PipelineOrderList, TriggersEditor (live),
                            #   TriggerDraftsEditor (create), TriggerList, TriggerForm, triggerDraft,
                            #   WorkflowSystemConfigFields, DagPreview, workflowValidation
      executions/           # ExecutionsBoard, ExecutionDetailPage, ExecutionLogViewer,
                            #   ExecutionQuickView, ExecuteWorkflowModal, SubProcessesSection, StageTimeline,
                            #   logSearch, resourceTypeLabels (sub-process type labels; unknown types render
                            #   raw with a not-abortable note when abortable === false)
      wizard/               # ExecuteWizard (+ExecuteWizardBody), WizardRail, RequirementsStrip, WorkflowPicker,
                            #   pipeline/input/review stages, InputFileSelector, MetadataSourceSelector,
                            #   SelectedInputFilesList, BulkFilePicker, selectedInputFiles (multi-file
                            #   selection: windowed list, bulk/paste picker, dedupe, 1000 cap),
                            #   railSteps, reviewBlockers, resolveRestrictions, resolveTemplate
      types.ts reservedTagKeys.ts

    FedAuth/                # Authentication orchestrator
      Auth.tsx              # Dual-mode auth: Cognito OR External OAuth2
      oauthSignInState.ts   # External OAuth: opaque state, same-origin return page, one-time values

    services/               # API and data services (with features/orchestration/api/, an add-on
                            #   module's own api/ directory and, in a fork, custom/services/, the
                            #   ONLY places apiClient is imported)
      APIService.ts         # Main API service (~3,500 lines, 110+ exports)
      apiClient.ts          # Custom fetch wrapper (internal, never import from components)
      appCache.ts           # localStorage cache (replaces Amplify Cache)
      webRoutesCheck.ts     # Batched + cached web-route (Tier-1) checks
      AssetUploadService.ts # S3 multipart upload logic
      AssetVersionService.ts
      FileOperationsService.ts
      MetadataService.ts
      MetadataSchemaService.ts
      ComplianceService.ts  # Compliance schemas, bindings, evaluations, quarantine, cascades, audit
      addons/               # Add-on API calls (physnaAPIService.ts)

    addons/                 # Add-on web lane: addonRoutes.tsx, addonNavigation.ts, addonFeatures.ts
                            #   (add-on feature-switch names) — see WEB_FRONTEND.md 7.5, 7.6
      edh/                  #   EDH Workstations add-on module (Tailwind + Radix, pages/ route shells)
                            #   — see .kiro/steering/ADDON_EDH_WORKSTATIONS.md
    custom/                 # FORK EXTENSION POINT (shipped empty): customRoutes.tsx, customNavigation.ts,
                            #   customFeatures.ts, services/, pages/ — see WEB_FRONTEND.md 7.5, 7.6

    context/                # React Context providers
      AssetContext.ts        # NOTE: typo is intentional, do NOT rename
      AssetDetailContext.ts  # useReducer-based context
      RuntimeConfigContext.ts # Live runtime config from Auth (useFeaturesEnabled); falls back to appCache outside the provider

    components/             # Domain/feature components
      asset/                # Asset viewing (ViewAsset.tsx is the main detail page)
        tabs/               # FileManager, Versions, AssetLinks, Comments, AssetExecutions, Compliance tabs
        versions/           # Asset version management
      common/               # ErrorBoundary, LoadingSpinner, StatusMessage
      compliance/           # ComplianceSchemaEditor.tsx (JSON + visual builder over vams-rules-v1 rules:
                            #   "Add rule" for all six types, the pipeline-rule input-file selector,
                            #   the metadata-rule scope form (scope, include/exclude paths, link
                            #   direction + type), typed fileStructure / versionCoverage /
                            #   assetAttributes checks)
                            #   + complianceSchemaRules.ts;
                            #   ReasonModal.tsx (Cloudscape reason dialog); complianceStateBadge.tsx
                            #   (shared state -> StatusIndicator map, `exception` = info);
                            #   EvaluationHistoryModal.tsx; ExecutionFailureReasons.tsx (a failed
                            #   pipeline rule's execution failure reason, when the API returns it);
                            #   RevokeExceptionModal.tsx; rowActions.ts; bindingConfirmations.ts
                            #   (the Bind/Change/Remove confirmation text); cascadeNodes.tsx (node
                            #   outcomes, paged and sorted in the browser)
      createupdate/         # CreateDatabase, UpdateAsset + form definitions
      filemanager/          # File tree and file operations
      form/                 # Shared upload control + uploadPaths.ts (the one upload path rule)
      list/                 # TableList.tsx (standard list page); ListToolbar.tsx (search + refresh
                            #   left, filters right, for a page with its own Table); tableSorting.ts
                            #   (column comparators, useSortedItems, useBrowserPagedItems)
      loading/
      metadata/
      metadataSchema/
      metadataV2/
      modals/
      search/               # ModernSearchContainer.tsx - main search UI
      searchSmall/
      selectors/
      single/               # Single-entity views (ViewFile, AssetIngestion, Metadata)
      table/

    pages/                  # Thin page wrappers composing components
      AssetDownload.tsx
      AssetUpload/
      auth/                 # Constraints, Roles, UserRoles, CognitoUsers, ApiKeys
      Databases.tsx
      LandingPage.tsx
      ListPage.tsx ListPageNoDatabase.tsx MetadataSchema.tsx
      search/
      Subscription/ Tag/
      # Compliance pages — permission-filtered via webRoutes() like every other page (no feature
      # flag); each deploys unconditionally and the nav/routes filter hides them without access.
      ComplianceSchemas.tsx ComplianceQuarantine.tsx ComplianceCascades.tsx
      ComplianceAuditLog.tsx DatabaseCompliance.tsx
      # Orchestration route shells rendering the matching features/orchestration component
      PipelinesPage2.tsx PipelineBuilderPage.tsx
      TemplateListPage.tsx TemplateBuilderPage.tsx
      WorkflowsPage2.tsx WorkflowBuilderPage.tsx WorkflowTriggersPage.tsx
      ExecutionsPage.tsx ExecutionDetail.tsx

    visualizerPlugin/       # 3D/media viewer plugin system
      core/
        PluginRegistry.ts   # Singleton registry
        streamFailure.ts    # Names why a streamed file failed to load
        types.ts
      config/
        viewerConfig.json   # Plugin configuration
      viewers/              # Individual viewer plugins
        manifest.ts

    common/                 # Shared utilities, helpers, and feature-switch constants
    constants/uploadLimits.ts
    hooks/                  # usePageTitle.ts, useThemeSettings.ts,
                            #   useAssetPageAccess.ts (may the caller open an assets/asset page)
    layout/
      navigationRegistry.ts # The one navigation registry: core + add-on + custom, side/profile placement
      Navigation.tsx        # Left sidebar navigation (side entries)
      useProfileMenuItems.ts # Top-bar profile menu items (profile entries, current page checked, then Sign out)
    utils/
      apiEndpoint.ts        # Resolves the API base URL
      chunkLoadRecovery.ts  # lazyWithRetry, chunk-failure detection, guarded one-time reload
      authTokenUtils.ts     # getDualValidAccessToken, getDualAuthorizationHeader, resolveExternalUserId
      sessionManager.ts     # Idle/expiry session handling
      queryRetry.ts         # createAppQueryClient, shouldRetryQuery: a final 4xx is never retried
    styles/
      theme.css             # CSS custom properties for dark/light theming
      tailwind.css          # Tailwind entry (scoped content glob; preflight disabled)
```

---

## 📋 **Development Workflow Checklist**

### **Phase 1: Pre-Implementation**

-   [ ] **Read existing files**: Read the file(s) you intend to modify before making changes
-   [ ] **Check existing patterns**: Review nearby files for conventions
-   [ ] **Plan component structure**: Pages are thin wrappers; components hold logic
-   [ ] **Plan API integration**: Identify which service file needs new functions
-   [ ] **Plan state management**: Determine if Context or local state is sufficient
-   [ ] **Plan routing**: Identify new routes needed in `routes.tsx`
-   [ ] **Plan Synonyms usage**: Identify user-visible text that needs `Synonyms`
-   [ ] **Plan documentation**: Identify Docusaurus pages that need updating

### **Phase 2: Implementation**

#### **Step 1: Service Layer (if new API calls needed)**

-   [ ] **Add API function**: Add to `APIService.ts` or appropriate service file
-   [ ] **Follow return tuple pattern**: Return `[boolean, data]` tuples
-   [ ] **Use apiClient**: Import from `./apiClient` (only in service files)
-   [ ] **Handle errors**: Use try/catch, return `[false, error?.message]`

#### **Step 2: Component Implementation**

-   [ ] **Add copyright header**: Apache-2.0 header with `2026` year
-   [ ] **Use Cloudscape components**: Import from individual subpaths (new imports; see Rule 2)
-   [ ] **Use Synonyms**: For all user-visible "Asset", "Database", "Comment" text
-   [ ] **Use TypeScript**: All new files must be `.ts` or `.tsx`
-   [ ] **Import from service layer**: Never import `apiClient` directly
-   [ ] **Use Context for shared state**: Follow `AssetDetailContext.ts` patterns
-   [ ] **Handle API responses**: Check boolean flag before using data
-   [ ] **List toolbar and sorting**: A list page puts the search and the refresh button on the left and the filter drop-downs on the right (`TableList.tsx`, or `components/list/ListToolbar.tsx`), and every column that offers sorting sorts (`components/list/tableSorting.ts`; see `WEB_FRONTEND.md` section 7.7)

#### **Step 3: Page and Route Setup**

-   [ ] **Create thin page wrapper**: In `src/pages/`
-   [ ] **Add lazy import**: `lazyWithRetry(() => import("./pages/MyPage"))` in `routes.tsx`
-   [ ] **Add to routeTable**: With `path`, `Page`, and `active` properties
-   [ ] **Add navigation entry**: In `src/layout/navigationRegistry.ts` if needed (`placement: "profile"` for a page of the signed-in user)

### **Phase 3: Quality Assurance**

#### **Step 4: Verification**

-   [ ] **No TypeScript errors**: Run `npm run typecheck` or check IDE (`npm run build` does not type-check)
-   [ ] **No broken imports**: Verify relative paths (no path aliases in this project)
-   [ ] **Cloudscape subpath imports**: Not barrel exports (new imports; see Rule 2)
-   [ ] **Service layer compliance**: No direct `apiClient` imports in components/pages
-   [ ] **Synonyms compliance**: No hardcoded "Asset"/"Database"/"Comment" in user-visible text
-   [ ] **HashRouter compliance**: No `BrowserRouter` usage
-   [ ] **Lazy loading compliance**: All pages lazy-loaded in `routes.tsx`
-   [ ] **React 18 migration**: Entry point uses `createRoot`; React 18 APIs (`useId`, `useTransition`) allowed in orchestration module

#### **Step 5: Documentation Updates**

-   [ ] **Update Docusaurus docs**: If UI navigation or features changed
-   [ ] **Update user guide**: `documentation/docusaurus-site/docs/user-guide/` if user-facing
-   [ ] **Update developer docs**: `documentation/docusaurus-site/docs/developer/frontend.md`
-   [ ] **Update CLAUDE.md**: If structural changes made (see root CLAUDE.md Rule 11)

---

## 🚨 **Mandatory Rules**

### **Rule 1: All API Calls Go Through Service Layer Files**

Components and pages MUST NEVER import `apiClient` directly. Only service files in `src/services/` (including `src/services/addons/` for add-on calls), `src/features/orchestration/api/`, an add-on module's own `api/` directory under `src/addons/<addon>/` and, in a fork, `src/custom/services/` may import `apiClient`.

```typescript
// CORRECT -- import from a service file
import { fetchAssets, deleteAsset } from "../../services/APIService";

// INCORRECT -- never import apiClient in components or pages
import { apiClient } from "../../services/apiClient";

// INCORRECT -- never use fetch or axios directly
const response = await fetch("/api/databases");
```

When adding a new API endpoint, add the function to the appropriate service file (or `APIService.ts` if no specific service exists). Follow the `[boolean, data]` return tuple pattern. An add-on's calls go in `services/addons/`, or in its module's `api/` directory when it ships a module of its own, never in `APIService.ts`.

### **Rule 2: Use Cloudscape Components (with orchestration exception)**

The EXISTING app uses AWS Cloudscape Design System. The NEW orchestration module (`src/features/orchestration/**`) is built with **Tailwind CSS + Radix UI** (the seed of the future design system) and is Cloudscape-free. This boundary is intentional:

-   **Existing pages** (Assets, Databases, Search, etc.) continue to use Cloudscape.
-   **`features/orchestration/**`\*\* (Pipelines, Workflows, Executions pages + wizard) uses Tailwind + Radix.
-   **Never leak Tailwind's preflight** into Cloudscape pages (preflight is disabled; Tailwind's content glob covers `src/features/orchestration/**`, each Tailwind add-on module under `src/addons/<addon>/**`, and the nine orchestration route shells in `src/pages/`, each named individually rather than as `src/pages/**` or `src/addons/**`). A new orchestration shell or Tailwind add-on module must be added to that list in `web/tailwind.config.js` or its Tailwind classes are not emitted; a Cloudscape page must **not** be added.
-   **Tailwind's UTILITY CSS is global, even though its content glob is not.** The glob decides which files Tailwind _scans_ for class names; every utility it emits lands in one stylesheet loaded on every page. A Cloudscape page that happens to use a class named like a Tailwind utility therefore picks up Tailwind's rule. **Never name a plain layout div after a Tailwind utility** — `container`, `hidden`, `block`, `flex`, `grid`, `fixed` (verified present in the built CSS; the emitted set depends on what the orchestration module uses, so treat this as examples rather than a closed list). Outside the orchestration module use a VAMS-defined class or no class at all.

    Real instance: `<div className="container">` wrapped the asset-view comment editor. VAMS defines no `.container` rule, so the only match was Tailwind's `.container` utility and its responsive max-widths (640/768/1024/1280/1536px), which capped the comment box on any wide viewport. The component's own styles gave no hint — the editor filled its parent correctly and the parent was the clamped element — so it was only findable by measuring the rendered DOM. When a width or spacing problem has no explanation in the component's own styles, walk the ancestors' computed `max-width` in the browser before changing the component.

Import Cloudscape from individual subpaths, NEVER from the barrel export.

This applies to new imports. A new file, or a Cloudscape component newly imported into an existing file, uses a subpath import, even in a file that already imports from the barrel. Existing barrel imports stay as they are; do not convert them.

```typescript
// CORRECT -- tree-shakeable individual imports
import Button from "@cloudscape-design/components/button";
import Table from "@cloudscape-design/components/table";
import Header from "@cloudscape-design/components/header";

// INCORRECT -- causes entire library to be bundled
import { Button, Table, Header } from "@cloudscape-design/components";
```

Do NOT introduce Material UI, Ant Design, Chakra, or any other UI library.

### **Rule 3: Use HashRouter for All Routing**

The app uses `HashRouter`, meaning URLs are `/#/path`. Never use `BrowserRouter`.

```typescript
// CORRECT
import { HashRouter } from "react-router-dom";
navigate("/databases/mydb/assets");

// INCORRECT
import { BrowserRouter } from "react-router-dom";
window.location.href = "/databases/mydb/assets";
```

### **Rule 4: Lazy Load All Pages in routes.tsx**

Every page component in `routes.tsx` MUST be lazy-loaded with `lazyWithRetry` (`src/utils/chunkLoadRecovery.ts`), which retries a chunk that failed to download and then reloads the page once:

```typescript
// CORRECT
const MyPage = lazyWithRetry(() => import("./pages/MyPage"));

// INCORRECT -- defeats code splitting
import MyPage from "./pages/MyPage";
```

### **Rule 5: No Global State Libraries (TanStack Query for orchestration server state)**

This codebase uses NO global state library (no Redux, Zustand, MobX, Recoil, Jotai). Use React Context API + `useReducer` for shared state. The Tailwind modules (`features/orchestration/` and the add-on modules under `addons/`) use **TanStack Query v5** for server-state management (caching, background refetch, smart polling via `computeRefetchInterval` that keeps polling active only while non-terminal executions are visible); each module has its own `qk` key factory.

```typescript
// CORRECT
const MyContext = createContext<MyContextType | undefined>(undefined);

// INCORRECT
import { createStore } from "redux";
import create from "zustand";
```

### **Rule 6: Use Synonyms for All User-Visible Entity Names**

All user-visible text containing "Asset", "Database", or "Comment" (Comments tab only) must use `Synonyms` instead of hardcoded strings.

```typescript
import Synonyms from "../../synonyms";

// CORRECT
<Header>{Synonyms.Assets}</Header>
<FormField label={`${Synonyms.Asset} Name`}>
placeholder={`Search ${Synonyms.assets} and files...`}
setError(`${Synonyms.Asset} not found`);

// INCORRECT
<Header>Assets</Header>
<p>Select a Database</p>
```

**Synonyms apply to**: headers, labels, descriptions, placeholders, alt text, error messages, success messages, button text, modal titles, empty state text.

**Synonyms do NOT apply to**: API request body values, variable names, property names, type names, route paths, CSS classes, `console.log` messages. Comment synonyms apply only to the Comments tab feature, not version comment fields.

### **Rule 7: TypeScript Only**

All new source files MUST be TypeScript (`.ts`/`.tsx`). Only `src/__mocks__/*.js` files remain as `.js`.

### **Rule 8: Preserve Copyright Headers**

Every source file MUST begin with the Apache-2.0 copyright header:

```typescript
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
```

### **Rule 9: npm Only -- Never Use yarn**

**Package manager:** npm 11.10.0+. Node 22.x ships with npm 10.9.x, which does **not** honor `min-release-age` (introduced in npm 11.10.0). Always pin npm before installing:

```bash
# Pin npm to the required version first
npm install -g npm@11.19.1

# CORRECT
npm install
npm run start
npm run build

# INCORRECT
yarn install
```

The viewer installs the `postinstall` chain runs under `web/customInstalls/*` do not inherit `web/.npmrc`, because npm reads a project `.npmrc` only from the directory of the `package.json` it installs. Each viewer package directory therefore carries its own `.npmrc` (a copy of `web/.npmrc`), and an install inside a cloned upstream repository (SuperSplat, Potree, Needle USD) passes `--min-release-age=7` on the command line. `src/visualizerPlugin/config/customInstallsNpmIntake.test.ts` fails when a new viewer directory or clone install is not covered (#334).

#### Keep the platform-specific native bindings in `optionalDependencies`

`vite build` needs three compiled binaries for the machine running it: **rolldown's** binding (the bundler), **esbuild** (imported directly by the `jsxInJs` plugin in `vite.config.ts`), and **lightningcss** (the CSS minifier used by `vite:css-post`). npm records only the binaries matching the platform that generated the lockfile ([npm/cli#4828](https://github.com/npm/cli/issues/4828)), and a later `npm install` on Linux does **not** add the missing one — so a lockfile written on Windows breaks the Linux CI build with one of:

```
Error: Cannot find native binding.
  cause: Cannot find module '@rolldown/binding-linux-x64-gnu'

[plugin vite:css-post] [lightningcss minify] Cannot find module '../lightningcss.linux-x64-gnu.node'
```

`web/package.json` declares the other platforms explicitly — `@rolldown/binding-{linux-x64-gnu,darwin-arm64,darwin-x64}`, `@esbuild/{linux-x64,darwin-arm64,darwin-x64}`, and `lightningcss-{linux-x64-gnu,darwin-arm64,darwin-x64}`. Each carries its own `os`/`cpu` constraints, so a developer only installs their own platform's binary.

**The versions are coupled to `vite`/`esbuild`/`lightningcss` and no test catches drift.** When bumping any of them: read the resolved versions with `npm ls rolldown esbuild lightningcss`, re-pin every entry, run `npm install`, then confirm all platforms are still recorded —

```bash
node -e "const l=require('./package-lock.json');Object.entries(l.packages).filter(([,v])=>v.os).forEach(([k,v])=>console.log(k,v.os))"
```

Expect `darwin`, `linux`, **and** `win32` for all three families. If one is missing, `npm install --package-lock-only --save-optional <pkg>@<version>` adds it; `npm install --force` and the `--os`/`--cpu` flags do **not** repair an already-pruned lockfile.

**Verify a Linux build locally instead of iterating through CI** — copy `package.json`, `package-lock.json`, `index.html`, the Vite/TS/Tailwind/PostCSS/Babel configs, `logo_*.png`, `src/`, and `public/` into a scratch directory, delete the `postinstall` script (viewer clones are not build inputs), then:

```bash
docker run --rm -v "$B:/app" -w /app node:22 bash -c "npm install --no-audit --no-fund && npm run build.lowmem"
```

`node:22` matches the GitHub runner. `customInstalls/` is excluded by `vite.config.ts` and is not needed.

`@napi-rs/canvas`, `@parcel/watcher`, and `@unrs/resolver-binding` are also Windows-only in the lockfile and are deliberately **not** pinned — the Linux build succeeds without them (optional canvas rendering, Sass watching, and the ESLint resolver are not loaded by the production build). `infra/` needs the same treatment for `@esbuild/*` (see `.kiro/steering/CDK_DEVELOPMENT_WORKFLOW.md`).

### **Rule 10: React 18 (Upgraded)**

This project uses React 18.3 (upgraded from 17.0.2). The entry point (`index.tsx`) uses `createRoot`. React 18 APIs (`useId`, `useTransition`, `useDeferredValue`) are allowed in the orchestration module but should be used sparingly elsewhere to maintain consistency with the existing codebase conventions.

### **Rule 11: Use appCache, Not Amplify Cache**

```typescript
// CORRECT
import { appCache } from "../services/appCache";
appCache.setItem("config", data);

// INCORRECT
import { Cache } from "aws-amplify";
Cache.setItem("config", data);
```

### **Rule 12: Permission Graying (Orchestration Module)**

The orchestration module implements Tier-1 permission graying via `useAllowedRoutes()` (fetches and caches `GET /auth/routes/api/allowed`). Components call `can(method, pathTemplate)` to check if the current user may invoke an endpoint, and gray/hide actions accordingly. Admin-only actions (Logs, Permanent-Delete) are hidden in menus and action rows when the route is not allowed. The trigger list (`TriggersEditor`, on the Triggers page and the builder's Triggers step) offers Add, Edit and Retry only to a role that may call the trigger `PUT` route and Delete only to one that may call its `DELETE`; the create flow's Triggers step is gated on the `PUT` as a whole. Navigational surfaces are the exception: the execution detail page's Logs tab stays present whatever the permission, and its panel states that logs are not viewable rather than rendering an empty viewer — removing a tab makes the capability undiscoverable, while an absent menu entry simply narrows a list of actions. Gate the panel's contents, not the tab. Tier-2 is not pre-checked client-side — the backend filters lists to what the user can access.

The core pages use the same check: the database list (Create, Edit, Delete Selected), the asset page (Delete, Upload Files, the metadata editor) and the asset search list (Create Asset, Delete Selected). A Cloudscape button there is disabled with `disabledReason` from `routeDisabledReason(loading, allowed, deniedReason)` (`features/orchestration/permissions/routeDisabledReason.ts`), which also disables it, with "Checking your permissions.", while the check loads. `can()` sees only what the listing reports, and the listing evaluates each `{param}` segment as a placeholder: a grant written for a concrete value (`starts_with /database/<id>/`, as the `database-user` template grants `PUT`) is not listed. Do not gate an action on a route a shipped template grants only that way, or that role loses an action it may perform. `features/orchestration/permissions/templateRoleRoutes.testdata.ts` holds the listing for the `database-readonly` and `database-user` templates, and `backend/tests/handlers/auth/test_permission_templates_web_action_routes.py` checks it against the templates.

```typescript
const { can } = useAllowedRoutes();
const canDelete = can("DELETE", "/workflows/executions/{executionId}/permanent");
<Button disabled={!canDelete}>Permanent Delete</Button>;
```

System records (`isSystem: true` on a pipeline or workflow) are graded from the record rather than the route: the Pipelines and Workflows pages show a **System** badge and open them in a **View** page where locked fields and steps are disabled and greyed, the pipeline drag handle is locked, a template allows only its config body, web form and tag schema, the trigger editor allows only Enabled, and a save sends only `{enabled}`. View replaces Edit in the actions menu. Every other action the API would refuse stays listed, disabled with its reason from `features/orchestration/permissions/systemRecordReasons.ts`: **Archive** in the Pipelines and Workflows menus (`ContextMenu` `disabledReason`), **Create Template** and **Delete** on the Templates board, and **Add** and **Delete** in the trigger list (`DisabledReasonButton`). The route check runs first: an action the role may not call stays hidden whatever the record. A disabled control with a reason stays focusable (`aria-disabled`, not `disabled`), is announced with its reason (`aria-description`), and shows the reason on hover and focus (`DisabledReasonTooltip`, which opens above the control, stays inside the viewport at any width, and Escape dismisses); choosing it does nothing. This is presentation only; the backend guards in `common/workflows/systemRecords.py` enforce it (mirrors `web/CLAUDE.md` 6.4).

### **Rule 13: Toast Notifications (Orchestration Module)**

Every mutation in `features/orchestration/**` reports through `useToast()` (`components/ToastProvider.tsx`) so a failure is always visible and a success is always confirmed. It renders through the **same Cloudscape `Flashbar`** the rest of the app notifies with (`components/search/SearchNotifications/ToastManager.tsx`) — same fixed top-right position, same durations (8s error / 5s otherwise), same shared `ToastNotification` shape from `components/search/types` — so an orchestration notification is indistinguishable from a search one. This is a deliberate exception to the Cloudscape-free rule: that rule governs page content, while the toast layer is a global overlay mounted from `App.tsx`, which already renders Cloudscape. Import from the subpath (`@cloudscape-design/components/flashbar`), never the barrel.

```typescript
import { useToast, toastErrorMessage } from "../components/ToastProvider";

const toast = useToast();
try {
    await archiveMutation.mutateAsync({ databaseId, pipelineId });
    toast.success("Pipeline archived", { description: pipeline.pipelineName });
} catch (err) {
    toast.error("Archive failed", {
        description: `${pipeline.pipelineName}: ${toastErrorMessage(err)}`,
    });
}
```

Match the app's message convention: a short header naming the outcome (`"Archive failed"` / `"Pipeline archived"`, mirroring `"Search failed"` / `"Search completed"`), with the entity name and backend message in the `description` — never the entity in the header. Never leave a mutation's `catch` as `console.error` only and never use `alert()`. Always confirm success when the surface disappears (a form that closes, a page that navigates away). Keep an inline message as well where it has context; the toast is additive. `toastErrorMessage(err)` normalizes an `Error`, a raw string, or a `{message|error|detail}` object instead of rendering `[object Object]`. The provider adds lifecycle safety over the search hook: timers cleared on unmount, identical repeats collapsed, stack capped at four, and z-index 4000 so a failure raised inside a dialog (z 3001) is not painted underneath it.

### **Rule 14: Use Dual-Mode Auth Token Utilities**

```typescript
// CORRECT
import { getDualValidAccessToken } from "../utils/authTokenUtils";
const token = await getDualValidAccessToken();

// INCORRECT -- manually getting tokens
const session = await AmplifyAuth.currentSession();
```

### **Rule 15: Do NOT Rename Intentional Typos**

`AssetContext.ts` -- the file name is intentional. NEVER rename it.

### **Rule 16: Update Documentation When Making Frontend Changes**

When frontend changes affect user-facing functionality, update the relevant Docusaurus documentation:

| Change Type          | Documentation to Update                                         |
| -------------------- | --------------------------------------------------------------- |
| UI navigation change | `user-guide/web-interface.md`, `user-guide/getting-started.mdx` |
| New feature/page     | `overview/features.md`, relevant user guide page                |
| New viewer plugin    | `developer/viewer-plugins.md`, `additional/viewer-plugins.md`   |
| Config/feature flag  | `deployment/configuration-reference.md`                         |
| Search UI change     | `user-guide/search-and-discovery.md`                            |
| Upload flow change   | `user-guide/upload-first-asset.md`                              |

### **Rule 17: Update Steering Files When Standards Change**

When system-wide frontend standards change (new rules, new patterns, new conventions), update all three locations:

1. `web/CLAUDE.md` -- frontend steering document
2. `.kiro/steering/WEB_FRONTEND.md` -- Kiro mirror of `web/CLAUDE.md`
3. `.kiro/steering/WEB_DEVELOPMENT_WORKFLOW.md` -- this file

A change to an add-on module's own conventions goes in that module's `CLAUDE.md` (`src/addons/<addon>/CLAUDE.md`) and its `.kiro/steering/ADDON_*.md` file instead.

### **Rule 18: Regenerate the CSP Hashes After Editing an Inline Script in `index.html`**

`web/index.html` carries inline `<script>` blocks (the `__publicField` polyfill, the `SharedArrayBuffer` probe, the pre-render theme application). The CDK Content-Security-Policy allows them by **SHA-256 hash** rather than by `'unsafe-inline'`, so an injected inline script is still blocked.

A CSP hash covers the **exact text content** of the element -- every byte between the tags, indentation included. Adding a line, renaming a variable, or letting Prettier reindent the block invalidates its hash. The browser then refuses to run that script, the app breaks at runtime, and **nothing fails at build time**.

**Any edit to an inline `<script>` block in `web/index.html` -- including a pure reformat -- requires regenerating the hashes and updating the CDK constant in the same change:**

```bash
cd web && npm run build                        # hash the HTML that is actually served
node scripts/cspInlineScriptHashes.js --ts     # emit the TypeScript constant
# paste over INDEX_HTML_INLINE_SCRIPT_HASHES in infra/lib/helper/cspInlineScriptHashes.ts
cd ../infra && npx jest test/web/cspInlineScriptHashes.test.ts   # drift guard must pass
```

Omit `--ts` for a readable per-block listing.

| File                                           | Role                                                                     |
| ---------------------------------------------- | ------------------------------------------------------------------------ |
| `web/index.html`                               | The inline scripts being hashed                                          |
| `web/scripts/cspInlineScriptHashes.js`         | Generator -- hashes every inline block, skips any with a `src` attribute |
| `infra/lib/helper/cspInlineScriptHashes.ts`    | The generated constant. **Generated -- do not hand-edit**                |
| `infra/lib/helper/security.ts`                 | `generateContentSecurityPolicy()` spreads the constant into `script-src` |
| `infra/test/web/cspInlineScriptHashes.test.ts` | Recomputes from `index.html` and fails on drift                          |

**A hash and `'unsafe-inline'` are mutually exclusive.** A CSP may allow inline script by hash **or** by the `'unsafe-inline'` keyword, never both -- when a hash source is present, browsers ignore `'unsafe-inline'` entirely. The two are not additive, so `'unsafe-inline'` cannot be kept as a fallback.

For that reason `generateContentSecurityPolicy()` emits `'unsafe-inline'` in **no** configuration, the Physna add-on included. The add-on's viewer frames Physna's own HTTPS origin rather than a `blob:` document, so that page loads under Physna's own CSP and its inline scripts are outside this policy's reach; the keyword would also be ignored on a VAMS page, because the hash sources are present. The add-on contributes `frame-src` and `connect-src` origins and no `script-src` source.

If a **new** viewer plugin needs inline script, hash that document's own inline blocks rather than returning `'unsafe-inline'` to `script-src` -- a hash source makes the keyword inert, so adding it would relax nothing and remove the protection the hashes give every other page.

An external `<script src="...">` needs no hash; it is matched by host-source. It may still need its origin added to `script-src`/`connect-src`.

---

## 📐 **Implementation Standards**

### **API Integration: Return Tuple Pattern**

Most `APIService.ts` functions return `[boolean, data/errorMessage]` tuples:

```typescript
export const fetchSomething = async ({ databaseId }) => {
    try {
        const response = await apiClient.get(`database/${databaseId}/something`);
        if (response?.success === false) {
            return [false, response?.message ?? response];
        }
        return [true, response?.message ?? response];
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};
```

**Consuming API results:**

```typescript
const result = await fetchAssets({ databaseId });
if (result === false || result[0] === false) {
    setError(result ? result[1] : "Unknown error");
    return;
}
const data = result[1];
```

A `403` the deployment's AWS WAF web ACL blocked is not a permission denial. `isRequestBlocked(error)` (in `apiClient.ts`, re-exported from `APIService.ts`, since components never import `apiClient`) recognizes its body (`blockedBy: "waf"`), and `parseErrorResponse` appends ` (request ID <id>)` to its message, so code that maps a `403` to a permission sentence checks `isRequestBlocked` first and shows the message.

A request whose response the browser never saw rejects with a `TypeError` ("Failed to fetch"). `isNetworkFailure(error)` recognizes it, and the service returns or throws `NETWORK_FAILURE_MESSAGE` (both in `apiClient.ts`) in its place, with no status, so the page says the service could not be reached and can offer Retry instead of a "not found" or permission message.

A TanStack Query hook never retries a final answer. The app's query client (`createAppQueryClient` in `utils/queryRetry.ts`) retries a failed query once, except when the error carries a 4xx status other than 408 or 429: a 403 is the API's authorization decision and a 404 says the object is not there, so the page shows the error at once. `toTuple` / `toTupleWithWarnings` tag a failed tuple with the call's status (`failureStatus(result)`, non-enumerable, so the tuple still destructures and compares as `[false, message]`), and the hooks in `features/orchestration/api/queries.ts` throw through `callService`, which raises a `ServiceCallError` carrying it. A hook that throws on a tuple itself goes through `callService` too, or its 403 is re-sent (#1270). A service that rebuilds its tuple drops the status and keeps the old retry-once behaviour

### **Authentication: Dual-Mode System**

The app supports Cognito and External OAuth2 modes. The auth orchestrator is `src/FedAuth/Auth.tsx`:

-   `window.DISABLE_COGNITO === true` --> External OAuth2 mode
-   `window.COGNITO_FEDERATED === true` --> Federated Cognito mode
-   Otherwise --> Standard Cognito mode

Always use `getDualValidAccessToken()` and `getDualAuthorizationHeader()` from `src/utils/authTokenUtils.ts`.

### **State Management: Context + useReducer**

Follow the pattern in `src/context/AssetDetailContext.ts`:

```typescript
export interface MyAction {
    type: string;
    payload: any;
}

export const myReducer = (state: MyState, action: MyAction): MyState => {
    switch (action.type) {
        case "SET_DATA":
            return action.payload;
        default:
            return state;
    }
};

export type MyContextType = {
    state: MyState;
    dispatch: any;
};

export const MyContext = createContext<MyContextType | undefined>(undefined);
```

### **Theme System**

VAMS supports dark/light themes (default: dark):

-   CSS custom properties in `src/styles/theme.css`
-   `.awsui-dark-mode` class on `body` triggers dark mode
-   Use Cloudscape design tokens or CSS custom properties from `theme.css`

```scss
// CORRECT
@use "@cloudscape-design/design-tokens" as awsui;
.my-container {
    padding: awsui.$space-l;
    color: awsui.$color-text-body-default;
}

// INCORRECT
.my-container {
    padding: 20px;
    color: #16191f;
}
```

### **Feature Flags**

Feature flags are read from `/api/secure-config` and stored in `appCache`:

```typescript
const config = appCache.getItem("config");
if (config?.featuresEnabled?.includes("LOCATIONSERVICES")) {
    // Enable map features
}
```

**Download as Zip** is gated twice: the `ZIPDOWNLOADS` feature switch decides whether the controls exist at all,
and `useAllowedRoutes().can()` on the zip routes (a server decision) decides whether the user sees them. The
file manager's **Export** menu offers **Download as Zip** for file, folder, root and mixed selections (hidden for
an asset that is not distributable or is archived). `buildZipSelection` lists archived or deleted selected items in
`refused`, and the dialog then refuses the whole job (names them, no Create), as the API does; items inside a selected
folder collapse into it, and more than `ZIP_MAX_SELECTION_ENTRIES` (1,000) entries is refused with a plain message. The asset tab `zipDownloads` (**Download Queue**) and the
global page `#/zipdownloads` (**My Download Queue**) list the caller's jobs and poll every 5 s only while a non-terminal job is visible.
The user-facing names come from `DOWNLOAD_QUEUE_PAGE_TITLE` / `DOWNLOAD_QUEUE_TAB_LABEL` and do not say "zip", so other kinds of
download job can join the queue; the route, the tab id, the API routes and the CLI/MCP names keep `zip`. Both lists search the
rows already loaded (`filterZipJobs`: asset ID, asset name, request ID, archive name) with the refresh button beside the
search; the API pages the list, so **Load more** adds older jobs, which the search then covers. The asset tab offers **Show all users** (a `SegmentedControl` right-aligned in the table's filter row through the `pagination` slot, default **My jobs**) only when `useAllowedRoutes().can("GET", ZIP_ROUTES.allUsers)` allows the all-users route; that view lists `fetchAssetZipDownloadsAllUsers` and shows **Download** and **Cancel**/**Delete** only from the row flags the API returns (`canDownload`, `canRemove`), never from a role or ownership rule in the client. The My jobs views (the asset tab's default and the **My Download Queue** page) get the same flags on their rows: a finished job with `canDownload: false` (a quarantined asset) offers no **Download**, and `canRemove: false` greys **Cancel**/**Delete**; the role's route decisions still gate the controls, and a response without the flags is read as before (#1125 D37). A created job's success message links to the asset's own **Download Queue** tab: `assetDownloadQueueLink()` sets the asset page's `?tab=zipDownloads` query (`ASSET_TAB_QUERY_PARAM`) and keeps its other parameters, and `ViewAsset` hands `?tab=` to `TabbedContainer` (`requestedTabId`), which switches tabs in place without remounting and reports a tab the user picks back to the query (`onTabChange`; File Manager drops it).

Files: `src/components/zipDownloads/` (`zipDownloads.ts` rules and route templates, `CreateZipDownloadModal.tsx`,
`ZipDownloadsTable.tsx`), `src/components/asset/tabs/ZipDownloadsTab.tsx` (tab id `ZIP_DOWNLOADS_TAB_ID` in
`TabbedContainer.tsx`), `src/pages/ZipDownloadsPage.tsx`, and the profile-menu entry in `src/layout/navigationRegistry.ts`
(`featuresEnabledRestriction: [ZIPDOWNLOADS]`).

A surface that stays mounted across a login (the side navigation) or that gates whole pages reads the flags through `useFeaturesEnabled()` from `context/RuntimeConfigContext.ts` instead: the cached config carries no `featuresEnabled` until the secure config lands after the first login, and only the context re-renders a mounted consumer when it does.

Any side-navigation entry (a section, link or group in the core, add-on or custom lists) may carry `featuresEnabledRestriction: string[]`, the field name and all-of meaning the viewer config uses: `layout/Navigation.tsx` drops the entry until every listed switch is enabled, re-runs the web-route check when the switches arrive, and strips the field before the items reach `SideNavigation`. An add-on's switch names live in `addons/addonFeatures.ts` (`addonFeaturesEnabled`), each one declared in `infra/common/addonAppFeatures.ts` (`addons/addonFeatures.test.ts`); a fork's live in `custom/customFeatures.ts`.

---

## 📐 **Gold Standard Reference Files**

| Purpose              | File                                              |
| -------------------- | ------------------------------------------------- |
| Route definitions    | `src/routes.tsx`                                  |
| Auth orchestrator    | `src/FedAuth/Auth.tsx`                            |
| API service patterns | `src/services/APIService.ts`                      |
| API client           | `src/services/apiClient.ts`                       |
| App cache            | `src/services/appCache.ts`                        |
| Context + Reducer    | `src/context/AssetDetailContext.ts`               |
| Plugin registry      | `src/visualizerPlugin/core/PluginRegistry.ts`     |
| Viewer config        | `src/visualizerPlugin/config/viewerConfig.json`   |
| Complex component    | `src/components/asset/ViewAsset.tsx`              |
| Search UI            | `src/components/search/ModernSearchContainer.tsx` |
| Upload service       | `src/services/AssetUploadService.ts`              |
| Token utilities      | `src/utils/authTokenUtils.ts`                     |
| Synonyms config      | `src/synonyms.tsx`                                |
| Navigation registry  | `src/layout/navigationRegistry.ts`                |
| Navigation           | `src/layout/Navigation.tsx`                       |
| App shell            | `src/App.tsx`                                     |

---

## 📝 **Development Templates**

### **New Page Template**

Every top-level page (a route in `src/routes.tsx`, `src/addons/addonRoutes.tsx` or `src/custom/customRoutes.tsx`) sets its own outer padding and heading. The `AppLayout` in `routes.tsx` runs with `disableContentPaddings` while the side navigation is open, so a page whose root is a bare `Table` or `Container` sits flush against the navigation and the header. A page opens with one of two page shells:

-   **Cloudscape pages:** the root is `<Box padding={{ top: "m", horizontal: "l" }}>`; inside it, the page `h1` in a `TextContent` in its own `Grid` row, then the content (a `variant="container"` `Table` or a `Container`) in the next `Grid` row, then `<div style={{ paddingBottom: "20px" }} />`. References: `src/pages/auth/ApiKeys.tsx` (a list opened from the profile menu, with search, refresh and Load more), `src/pages/ListPageNoDatabase.tsx` (Roles, Constraints, Tags) and `src/pages/ZipDownloadsPage.tsx`. A page that opens with a breadcrumb uses a smaller top (`"s"`, or `"xs"` as on the asset page) and keeps `horizontal: "l"`.
-   **Tailwind pages (orchestration module, add-ons):** the root element carries `className="orchestration-root orchestration-page ..."` (`src/styles/tailwind.css`, matched to the Cloudscape shell). Reference: `src/features/orchestration/pipelines/PipelinesPage.tsx`.

The page also calls `usePageTitle`. A feature component that renders both as a page and inside an asset tab (such as `ZipDownloadsTable`) has no outer padding of its own; the page adds the shell around it. A list table searches like the other VAMS tables: a Cloudscape `TextFilter` in the table's `filter` slot with the refresh icon button beside it, not in the header actions (`ApiKeys.tsx`, `components/list/TableList.tsx`).

**Table filter row:** a table's filter row has the search on the left, the refresh icon button right beside the search, and any view toggle or select (a `Toggle`, `SegmentedControl` or `Select` that changes which rows are listed, such as "Show archived" or "My jobs / Show all users") right-aligned. Put the search and refresh in the table's `filter` slot inside `<SpaceBetween direction="horizontal" size="xs">`, and the toggles in the table's `pagination` slot (in the same `SpaceBetween` when there are several, followed by the `Pagination` when the table has one). Cloudscape draws that slot in the right-aligned group of the filter row. Do not put toggles in the `filter` slot or the header actions, and do not build the row with hand-rolled flex styles. A toggle that depends on a permission renders only when the API's route listing allows its route. References: `src/components/asset/versions/components/AssetVersionList.tsx` (the Versions tab: search and refresh left, "Show archived" right) and `src/components/zipDownloads/ZipDownloadsTable.tsx` (the Download Queue tab: search and refresh left, "My jobs / Show all users" right).

**Table width:** a table fits its container at 1280 px without sideways scrolling. Give no column a `minWidth` to stop wrapping; keep a status on one line with `<StatusIndicator wrapText={false}>`, let names and IDs with no spaces break anywhere (`overflowWrap: "anywhere"`), and put several row actions in icon buttons (`variant="icon"` with an `ariaLabel`, which Cloudscape also shows as the tooltip). `inline-link` and `link` buttons drop `disabledReason`, so do not use them for an action that can be greyed with a reason. Reference: `components/zipDownloads/ZipDownloadsTable.tsx` (#1125 D30).

`src/pages/pageShell.test.ts` reads every routed page and the local modules it imports directly, and fails when none of them opens with a shell. Its `EXEMPT` map lists the pages with a layout of their own, each with its reason (the landing page); a list page never belongs there.

```tsx
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Box from "@cloudscape-design/components/box";
import Grid from "@cloudscape-design/components/grid";
import TextContent from "@cloudscape-design/components/text-content";
import MyComponent from "../components/myfeature/MyComponent";
import { usePageTitle } from "../hooks/usePageTitle";

const MyPage: React.FC = () => {
    usePageTitle("My Feature");
    return (
        <Box padding={{ top: "m", horizontal: "l" }}>
            <Grid gridDefinition={[{ colspan: 12 }]}>
                <div>
                    <TextContent>
                        <h1>My Feature</h1>
                    </TextContent>
                </div>
            </Grid>
            <Grid gridDefinition={[{ colspan: 12 }]}>
                <MyComponent />
            </Grid>
            <div style={{ paddingBottom: "20px" }} />
        </Box>
    );
};

export default MyPage;
```

Then in `routes.tsx`:

```typescript
const MyPage = lazyWithRetry(() => import("./pages/MyPage"));

// In routeTable array:
{
    path: "/myfeature",
    Page: MyPage,
    active: "#/myfeature/",
},
```

### **New Component Template**

```tsx
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useCallback } from "react";
import { useParams } from "react-router-dom";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Synonyms from "../../synonyms";

interface MyComponentProps {
    databaseId?: string;
}

const MyComponent: React.FC<MyComponentProps> = ({ databaseId }) => {
    const { databaseId: paramDbId } = useParams();
    const effectiveDbId = databaseId || paramDbId;

    const [loading, setLoading] = useState(true);
    const [items, setItems] = useState<any[]>([]);
    const [error, setError] = useState<string | null>(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        try {
            const result = await someApiCall({ databaseId: effectiveDbId });
            if (result === false || result[0] === false) {
                setError(result ? result[1] : "Failed to load data");
                return;
            }
            setItems(result[1] || result);
        } catch (err: any) {
            setError(err?.message || "Unknown error");
        } finally {
            setLoading(false);
        }
    }, [effectiveDbId]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    return (
        <SpaceBetween size="l">
            <Header variant="h1">{Synonyms.Assets}</Header>
            <Table
                loading={loading}
                items={items}
                columnDefinitions={[]}
                empty={
                    <Box textAlign="center" color="inherit">
                        <b>No items</b>
                    </Box>
                }
            />
        </SpaceBetween>
    );
};

export default MyComponent;
```

### **New Service Function Template**

```typescript
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "./apiClient";

/**
 * Description of what this function does.
 * @param {Object} params - Parameters
 * @param {string} params.databaseId - Database ID
 * @returns {Promise<[boolean, any]>}
 */
export const myServiceFunction = async ({ databaseId }) => {
    try {
        const response = await apiClient.get(`database/${databaseId}/myendpoint`);
        if (response?.success === false) {
            return [false, response?.message ?? response];
        }
        return [true, response?.message ?? response];
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};
```

### **New Context Template**

```typescript
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createContext, useReducer } from "react";

export interface MyAction {
    type: string;
    payload: any;
}

export interface MyState {
    data: any[];
    loading: boolean;
}

export const initialState: MyState = {
    data: [],
    loading: false,
};

export const myReducer = (state: MyState, action: MyAction): MyState => {
    switch (action.type) {
        case "SET_DATA":
            return { ...state, data: action.payload };
        case "SET_LOADING":
            return { ...state, loading: action.payload };
        default:
            return state;
    }
};

export type MyContextType = {
    state: MyState;
    dispatch: any;
};

export const MyContext = createContext<MyContextType | undefined>(undefined);
```

---

## 🚀 **Development Commands**

```bash
cd web
npm install           # Install dependencies + runs postinstall (viewer installs)
npm run start         # Dev server (port 3001)
npm run build         # Production build (output: web/dist/)
npm run typecheck     # Type-check only (tsc --noEmit); the Vite build does not type-check
npm test              # Run tests with coverage (Jest 30 + @testing-library/react)
npx jest              # Run tests without coverage (faster)
```

**Label a temporary test with a `TEMPORARY-TEST` comment.** Jest has no marker system, so a test written to
prove one specific change landed — a removed prop, a deleted branch, a reworded string — carries a
`TEMPORARY-TEST` token in a comment directly above its `it(...)`, naming what it pins:

```ts
// TEMPORARY-TEST: pins the removal of the duplicate upload summary branch; drop once released.
it("no longer renders the second summary", () => {
```

Release cleanup finds them with `grep -rn "TEMPORARY-TEST" web/src web/e2e`. The token is needed because a
temporary test and a durable guardrail read identically afterwards — both may assert an absence and both
explain themselves.

Do **not** label a test whose forbidden construct is still writable: a CSP that must not gain
`'unsafe-inline'`, a component that must not import `apiClient` directly, a div that must not be named after
a Tailwind utility. Those must keep holding. Full criterion: root `CLAUDE.md` Rule 13; Claude Code
counterpart: `web/CLAUDE.md` section 11.4.1.

**End-to-end specs** (`web/e2e/`, Playwright against a deployed stack) follow the rules in "End-to-End Testing (Playwright)" below.

**Lint and format (from project root):**

```bash
npm run lint          # Lint check (web/src + infra/lib + infra/bin + infra/test)
npm run lint-fix      # Auto-fix lint issues
npm run prettier-check
npm run prettier-fix
```

---

## 🧪 **End-to-End Testing (Playwright)**

Mirrors `web/e2e/CLAUDE.md`; a change to either lands in both (root `CLAUDE.md` Rule 11).

Playwright drives the **deployed** application, so it is the only layer that proves a frontend change reached
users. `E2E_BASE_URL` is required: `playwright.config.ts` throws when it is unset. A fix that exists only in
`web/src/` fails until the front end is rebuilt (`cd web && npm run build`) and deployed. Confirm the
deployment took by matching the served main-bundle hash to the local one
(`curl -s https://<host>/index.html | grep -oE '/assets/index-[A-Za-z0-9_-]+\.js'` against
`ls web/dist/assets/index-*.js`). A passing jest suite says nothing about what is deployed.

### **Two kinds of spec**

|           | **Core specs (tracked)**                                                                                                                                                                                                                                                                                                                                                      | **Ad-hoc specs (untracked)**                                |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Purpose   | Permanent smoke coverage of a page or shared component                                                                                                                                                                                                                                                                                                                        | Prove one specific change or fix works                      |
| Data      | **Must not require specific data**                                                                                                                                                                                                                                                                                                                                            | May target known seed data                                  |
| Naming    | `orchestration.{page}.spec.ts`; `{area}.spec.ts` or `{area}.{page}.spec.ts` for another page or shared concern (`databases`, `tags`, `search.counts`, `security`, `navigation.profile-menu`, `asset.archived`, `zipdownloads`, `viewers`); `seeded.{feature}.spec.ts` for a spec that reads a seed fixture the environment names; `{addon}.{page}.spec.ts` for an add-on page | `*.reviewfixes.spec.ts`, `_probe.spec.ts`, anything scratch |
| Committed | Yes                                                                                                                                                                                                                                                                                                                                                                           | No: keep out of git                                         |

-   Add a core spec only when a new page or shared component is added. A fix to existing behaviour gets an
    ad-hoc spec, or a new assertion in the relevant core spec when the behaviour is permanent.
-   A `seeded.` spec hardcodes nothing: a seed script writes a JSON fixture, an `E2E_*` variable names it, and
    the whole file skips with the reason when it is unset (`seeded.compare.spec.ts` reads `E2E_COMPARE_SEED`).
-   Prefix throwaway probe specs with `_` and delete them when finished; a probe left behind runs in every
    later suite.

### **Rule E1: Core specs pass against ANY environment**

-   Never depend on a particular pipeline, workflow, execution, asset or count existing. Derive the subject
    from whatever exists (`firstCardId(page)`) and `test.skip(!id, "<reason>")` when there is none; never a
    hardcoded seed id.
-   Never wait on data to prove the page loaded: wait on the page's own heading (`gotoOrchestration`), then
    assert the list rendered rows **or** its empty state (`expectTableRendered`).
-   A spec that needs a file of a particular type finds one with `findAssetFiles` and skips when there is
    none, never a fixed database and asset id.
-   A skip with a reason is the honest outcome when a precondition is absent; a test that passes because its
    assertion never ran is not. Card lists are server-paginated (pageSize 50), so a spec that waits for one
    seeded id fails once newer items push it off page 1.

### **Rule E2: Core specs do not mutate the environment**

-   Open dialogs and wizards to assert they work, then dismiss them (`Escape`); never Launch.
-   For a destructive action, assert the confirm appears and says the right thing, then dismiss it (capture a
    native `confirm()` with `page.once("dialog", …)` and `d.dismiss()`).
-   Only an ad-hoc spec may create and clean up its own data. A core spec that must mutate data or needs a
    second user does so only behind an `E2E_*` opt-in variable and otherwise skips with the reason:
    `zipdownloads.spec.ts` takes `E2E_ZIP_CREATE=1` and `E2E_SECOND_USERNAME` / `E2E_SECOND_PASSWORD`
    (signed in with `signInAs`, never hardcoded). A spec lists its other opt-in variables in its header comment.

### **Rule E3: Use the shared harness, don't re-derive selectors**

`web/e2e/support/fixtures.ts` holds the durable selector knowledge; import from it, and when a page's markup
changes fix the helper once. An add-on's page helpers sit beside it in `support/{addon}Fixtures.ts`. The full
helper table is in `web/e2e/CLAUDE.md` Rule 3; the groups are:

-   **Navigation and page state:** `gotoOrchestration`, `orchestrationRoot`, `searchBox`, `facet`,
    `firstCardId`, `openCardMenu`, `tableRows` / `expectTableRendered` (takes the page's own empty-state
    regex), `menuSurface`, `wizardRail`, `rowValue`, `collectPageErrors`, `sideNavigation`,
    `profileMenuName` / `profileMenuTrigger` / `openProfileMenu` / `profileMenuLink`, `signInAs`.
-   **Feature switches:** `cachedFeaturesEnabled`, `featuresEnabled` / `missingFeatures`, `featureSwitches`;
    `test.skip` on an absent switch.
-   **Files and viewers:** `openAssetFile`, `openFileManager`, `selectTreePath`, `treeNode`,
    `selectedTreeNode`, `chooseViewer`, `chooseSelectOption`, `apiContext`, `findAssetFiles`,
    `findNestedAssetFile`, `findAssetsWithFiles`, `viewerSamples`, `compareSeed`, `openViewerFile`,
    `pickViewerIfAsked`, `expectViewerSelected`, `dataGrid`, `largestCanvas`, `canvasStats`,
    `expectCanvasRendered`, `expectSuperSplatRendered`.
-   **Layout:** `VIEWPORTS` (phone 390, tablet 768, laptop 1280, desktop 1440), `pageOverflowX` /
    `expectNoPageOverflowX`, `expectReachable`.
-   **Security and hygiene:** `watchConsoleErrors` (ignores only `CONSOLE_ERROR_IGNORE`),
    `watchCspViolations` (call before navigating) / `expectNoNewCspViolations`, `watchRequestHosts`
    (`thirdParty()`), `SUPERSPLAT_CSP_ALLOWED`, `CSP_CONSOLE_REPORT`.

Selector facts worth not rediscovering:

-   The orchestration search input is `getByLabel("Search")`, matched by label, not placeholder; there are two
    "Search" inputs (filter bar and global nav), so scope to `.orchestration-root`.
-   Filters are native `<select>`s (`Status`, `Execution Type`, `Database`, `Group by`, `Filter by status`,
    `Filter by trigger`, `Filter by group ID`, `Time window`). Cards expose `Actions for {id}` buttons, the
    stable way to read a card's identity. The template panel heading is an `h1` reading exactly `Templates`.
    Execution trigger values use the stored vocabulary (`Manual`, `File-Upload`).
-   The pipeline form is a three-step wizard ("1 Basic", "2 Execution", "3 Settings"); later steps' fields do
    not exist until you advance with the form's own `Next`. Never locate a step by name:
    `getByRole("button", { name: /Settings/ })` matches the global navigation's Settings button.
-   The execute flow is one dialog: its first step is the workflow picker (`role=option` rows in a
    `role=listbox`, search labelled `Workflow`) except from a workflow card's Execute action, and `Launch`
    exists only on the final step; an Inputs-step assertion targets `Next`.
-   The executions board names the workflow's database `Workflow Database` and has no `Group` column.
-   `[role="menu"]` is ambiguous on every page: closed Cloudscape Selects keep zero-size menus earlier in
    document order. Reach the open surface with `menuSurface(items)`.
-   A detail panel's field value is the label's next sibling; use `rowValue(page, label)`, never a page-wide
    `getByText` for a value's shape.

### **Rule E4: Assert behaviour, not implementation**

Prefer role-based, user-visible assertions. A crash regression asserts no uncaught errors
(`collectPageErrors` empty), not only that something rendered. A layering fix proves the dialog receives the
click (`document.elementFromPoint` at the dialog lands inside it); a z-index alone proves nothing.

### **Viewer render proof (shared by every viewer PR)**

`viewers.spec.ts` "viewer render proof (seeded samples)" proves a viewer DRAWS a known file. Add a viewer's
cases to `renderCases` there; never start another spec or harness.

-   **Samples** are entries of the tracked `e2e/fixtures/viewer-samples.json` (schema checked by
    `web/src/visualizerPlugin/config/viewerSamplesManifest.test.ts`): `id`, `viewer` (a config viewer id, or
    `none` for a `not-offered` subject), `path`, `licence`, `source`, and exactly one of a download (`url`
    pinned to a tag or commit, `size`, `sha256`; the seeder refuses a mismatch), `generated`, or
    `derivedFrom` plus `transform`. Optional `requiresFeature` and `expect` thresholds. Verify the licence at
    its source before adding a download; no sample binary is ever committed.
-   **Seeding:** `tools/VamsCLI/examples/seed_viewer_samples.py --profile <name>` uploads the samples to one
    distributable asset and writes `e2e/fixtures/viewer-samples-seed.json`; export its path as
    `E2E_VIEWER_SAMPLES`.
-   **A case** names `sample`, `viewer` (the exact config `name`; the case asserts that viewer opened) and
    `kind` (`canvas`, `image`, `raster-notice`, `pdf`, `grid`, `text`, `text-binary`, `text-encoding`,
    `not-offered`, `potree`, `refused`, `supersplat`), with optional `select`, `requiresFeature` /
    `withoutFeature`, `fetches`, `thresholds`, `cspAllowed`, `servedBundle`, `consoleAllowed`, `reads` and
    `selectInPanel`. VNTANA and VEERUM have no feature flag, so their cases skip when the viewer is not
    offered.
-   **Every case fails** on console or page errors (`watchConsoleErrors`; never widen
    `CONSOLE_ERROR_IGNORE` for one case, use `consoleAllowed`), on any CSP violation outside `cspAllowed`
    (list the development baseline's violations exactly, so only new ones fail) and on any request host
    outside the deployment (`watchRequestHosts`). Run the spec on the development build first and record its
    pixel stats; a case that passes there and fails on a change is a regression.
-   **Potree cases** read the octree the Potree pipeline wrote, so run that pipeline on each `potree-*` sample
    after seeding; the case skips, naming the pipeline, until `preview/PotreeViewer/metadata.json` exists. It
    asserts `Potree.lru.numPoints > 0` and point pixels by red channel.
-   **SuperSplat** renders only through WebGPU, which headless Chromium hides. `E2E_WEBGPU=1` runs those cases
    in a separate `chromium-webgpu` project and takes them out of `chromium`; without an adapter they skip,
    and `E2E_REQUIRE_WEBGPU=1` makes that a failure. Run them where a GPU is available: Chromium's software
    adapter loses the device while the editor loads a file.

### **Running the suite**

```bash
cd web
export E2E_BASE_URL=https://<your-deployment-host>   # required
export E2E_USERNAME=<user> E2E_PASSWORD=<pass>       # never hardcode credentials
npm run e2e                                           # all specs
npx playwright test e2e/orchestration.pipelines.spec.ts --retries=0 --workers=2
E2E_WEBGPU=1 E2E_REQUIRE_WEBGPU=1 npx playwright test e2e/viewers.spec.ts   # SuperSplat render checks
```

-   `retries: 1` reports a fail-then-pass as **flaky**, and Playwright's exit code counts flaky as success;
    read the summary line, not the exit status (a shell pipeline makes the exit code the pipeline's).
-   `auth.setup.ts` signs in once and saves `e2e/.auth/admin.json` for 45 minutes (rapid Cognito sign-ins can
    trip the edge WAF); `E2E_FORCE_LOGIN=1` refreshes it, and an MFA account runs `npm run e2e:auth` headed
    once. Keep `auth.setup.ts` tracked and working: every spec depends on it.

### **Adding a core spec for a new page**

1. Add a new orchestration route to the `gotoOrchestration` union in `support/fixtures.ts`.
2. Assert, in order: the page renders without a crash; its controls exist; its filters offer the expected
   values; one interaction per row action.
3. Derive every subject from the environment (Rule E1) and mutate nothing (Rule E2).
4. Put new selector knowledge in `support/fixtures.ts`, not inline.
5. Run against a deployed environment; the result is `passed` or an explicit `skipped`, never a silent pass.

Anti-patterns: hardcoded seed ids; waiting on data to prove page load; mutating shared state in a core spec;
inline selectors; committing an ad-hoc or `_probe` spec; hardcoded credentials or base URL; calling a web fix
verified without checking the deployed bundle hash.

---

## 🔍 **Code Review Checklist**

### **Compliance Checks**

-   [ ] Copyright headers present on all new/modified files
-   [ ] Cloudscape components imported from subpaths (not barrel) (new imports; see Rule 2)
-   [ ] API calls go through service layer (no direct `apiClient` imports in components)
-   [ ] `Synonyms` used for all user-visible entity names
-   [ ] All pages lazy-loaded in `routes.tsx`
-   [ ] HashRouter used (no BrowserRouter)
-   [ ] React 18 APIs kept to the orchestration module unless there is a reason otherwise
-   [ ] TypeScript used for all new files
-   [ ] `appCache` used instead of Amplify Cache
-   [ ] `getDualValidAccessToken` used for auth tokens
-   [ ] No global state libraries introduced
-   [ ] npm used (no yarn)
-   [ ] Relative imports used (no path aliases)
-   [ ] Theme-aware styles (Cloudscape design tokens or theme.css custom properties)
-   [ ] List pages use the standard toolbar (search and refresh left, filters right) and every sortable column sorts (`WEB_FRONTEND.md` section 7.7)

### **Documentation Checks**

-   [ ] Docusaurus docs updated if user-facing features changed
-   [ ] `web/CLAUDE.md` updated if structural changes made
-   [ ] Steering files updated if frontend standards changed

---

## 🛠️ **Common Tasks Quick Reference**

### **Adding a new Cloudscape table page**

1. Create component in `src/components/myfeature/MyFeatureTable.tsx`
2. Use `@cloudscape-design/collection-hooks` for filtering/sorting
3. Lay out the toolbar and wire the column sorting per `WEB_FRONTEND.md` section 7.7 (search and refresh on the left, filters on the right; every sortable column sorts)
4. Create page wrapper in `src/pages/MyFeaturePage.tsx`
5. Add lazy import + route in `src/routes.tsx`
6. Add a navigation entry in `src/layout/navigationRegistry.ts` (`placement: "profile"` for a page of the signed-in user)

### **Adding a new API call**

1. Add function to `src/services/APIService.ts` (or appropriate service file)
2. Use `apiClient` for API calls (auth headers injected automatically)
3. Return `[boolean, data]` tuple
4. Handle errors with `try/catch`, return `[false, error?.message]`

### **Adding a new viewer plugin**

1. Create viewer directory in `src/visualizerPlugin/viewers/MyViewerPlugin/`
2. Implement component with `ViewerPluginProps` interface
3. Add to `manifest.ts`
4. Add to `viewerConfig.json`
5. If external dependencies, add custom install script in `customInstalls/`

### **Adding a new context**

1. Create context file in `src/context/` following `AssetDetailContext.ts` pattern
2. Export context, reducer, action types
3. Wrap provider around the relevant component tree
4. Consume with `useContext()` in child components

### **Modifying authentication flow**

1. Read `src/FedAuth/Auth.tsx` thoroughly before changes
2. Token logic lives in `src/utils/authTokenUtils.ts`
3. Test BOTH Cognito and External OAuth2 modes
4. Be aware of `window.DISABLE_COGNITO` and `window.COGNITO_FEDERATED` globals

---

## 🚫 **Anti-Patterns Summary**

1. **Importing apiClient in components/pages** -- use service layer files
2. **Importing Cloudscape from barrel export** -- use individual subpaths (new imports; see Rule 2)
3. **Using BrowserRouter** -- must use HashRouter
4. **Eagerly importing pages** -- must use lazyWithRetry
5. **Adding Redux/Zustand/MobX** -- use Context + useReducer
6. **Using Amplify Cache** -- use appCache
7. **Manually getting auth tokens** -- use getDualValidAccessToken
8. **Hardcoding entity display names** -- use Synonyms
9. **Spreading React 18 APIs across the codebase** -- allowed in `features/orchestration/`, used sparingly elsewhere
10. **Using yarn** -- project uses npm
11. **Creating .js files** -- all new files must be TypeScript
12. **Hardcoding colors/spacing** -- use Cloudscape design tokens or theme.css
13. **Over-documenting or narrating changes in comments** -- match surrounding comment density; describe what code is, not why it was added; never reference "upgrades", "new in vX", or the prompting change request in source comments (changelog narration belongs in `CHANGELOG.md` and the docs revision history)
