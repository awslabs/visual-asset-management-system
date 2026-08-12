# CLAUDE.md - VAMS Frontend (web/)

This is the frontend-specific Claude Code steering document for the VAMS (Visual Asset Management System) web application. It is auto-loaded when working within the `web/` directory.

---

## 1. Architecture Overview

VAMS frontend is a **React 18.3 + TypeScript ^5.0.0** single-page application built with Vite. All source files in `src/` are `.ts`/`.tsx` (only remaining `.js` files are Jest mocks in `src/__mocks__/`).

**Primary UI library:** AWS Cloudscape Design System (`@cloudscape-design/components ^3.0.196`).

**Package manager:** npm. NEVER use yarn in this project.

**Key design decisions:**

-   NO global state library (no Redux, no Zustand, no MobX)
-   React Context API + useReducer for shared state
-   Custom apiClient (fetch-based) for API calls, AWS Amplify v6 for authentication
-   HashRouter (`#/` URLs) for all routing
-   Plugin-based 3D viewer architecture (see `src/visualizerPlugin/CLAUDE.md`)
-   React.lazy + Suspense for route-level code splitting

---

## 2. Directory Structure

> **Maintenance note:** Update this tree when adding new components, pages, services, or viewer plugins. See root `CLAUDE.md` Rule 11.

```
web/
  package.json              # npm, React 18, Vite scripts
  customInstalls/           # Per-viewer custom install scripts (one dir per viewer)
  e2e/                      # Playwright specs against a deployed stack — see e2e/CLAUDE.md (11.5)
  src/
    App.tsx                 # Root app shell, HashRouter, TopNavigation
    routes.tsx              # Centralized route table, React.lazy, permission filtering
    config.ts               # Static config (VAMSConfig: APP_TITLE, DEV_API_ENDPOINT)
    synonyms.tsx            # Configurable display names (Asset, Database, Comment)
    index.tsx               # Entry point, createRoot
    reportWebVitals.ts      # Web vitals reporting
    setupTests.ts           # Jest setup

    features/orchestration/ # Pipeline/workflow/execution management (React 18, Tailwind, Radix)
      api/                  # Services + TanStack Query hooks + qk key factory
                            #   pipelines.ts workflows.ts executions.ts assets.ts databases.ts
                            #   client.ts queries.ts
      permissions/          # useAllowedRoutes.ts (Tier-1 gating)
      components/           # Cloudscape-free primitives (DataTable, StatusBadge, ContextMenu,
                            #   Stepper, Breadcrumb, SearchableSelect, ConfigEditor, ...)
                            #   ToastProvider.tsx — notifications for the whole module (see 6.5)
      pipelines/            # PipelinesPage.tsx, PipelineForm.tsx (wizard; execution-type fields
                            #   live under executionConfig.sqs / executionConfig.eventBridge),
                            #   TemplateEditor.tsx TemplateForm.tsx TagSchemaBuilder.tsx
                            #   TemplateOverridesEditor.tsx pipelineValidation.ts
      workflows/            # WorkflowsPage.tsx WorkflowBuilder.tsx PipelineOrderList.tsx
                            #   TriggersEditor.tsx WorkflowSystemConfigFields.tsx DagPreview.tsx
                            #   WorkflowValidationPanel.tsx workflowValidation.ts
      executions/           # ExecutionsBoard.tsx ExecutionDetailPage.tsx ExecutionLogViewer.tsx
                            #   ExecutionQuickView.tsx ExecutionRowActions.tsx
                            #   ExecuteWorkflowButton.tsx ExecuteWorkflowModal.tsx logSearch.ts
      wizard/               # ExecuteWizard.tsx + WizardPipelineStage/WizardInputStage/
                            #   WizardReviewStage, InputFileSelector, MetadataSourceSelector,
                            #   RestrictionSummary, resolveRestrictions.ts resolveTemplate.ts
      types.ts reservedTagKeys.ts

    FedAuth/Auth.tsx        # Dual-mode auth orchestrator (Cognito OR External OAuth2)
    authenticator/          # Cognito Authenticator UI components (Header, Footer, SignIn*)

    services/               # API and data services (ONLY files that may import apiClient)
      APIService.ts         # Main API service (~900+ lines, 40+ exports)
      AssetUploadService.ts # S3 multipart upload logic
      AssetVersionService.ts  # updateAssetVersion, archiveAssetVersion, unarchiveAssetVersion
      FileOperationsService.ts
      MetadataService.ts
      MetadataSchemaService.ts
      apiClient.ts          # Custom fetch-based client, injects auth headers
      appCache.ts           # Replaces Amplify Cache for runtime config
      webRoutesCheck.ts     # Batched + cached web-route (Tier-1) checks for routes.tsx/Navigation

    context/                # React Context providers
      AssetContext.ts        # NOTE: typo is intentional, do NOT rename
      AssetDetailContext.ts  # useReducer-based context

    components/             # Domain/feature components (organized by domain)
      asset/                  # Asset viewing (ViewAsset.tsx is the main detail page)
        tabs/                 #   FileManager, Versions, AssetLinks, Comments, AssetExecutions tabs
        versions/             # Asset version management (list, comparison, edit/archive modals)
      common/ createupdate/ form/
      filemanager/            # Asset file manager (Cloudscape)
        components/             #   FileDetailsPanel toolbar: Export | Automation | operations.
                                #   AutomationActions.tsx lazy-loads the orchestration execute modal,
                                #   so this Cloudscape tree never bundles that module.
        utils/                  #   automationSelection.ts — maps a selection (whole asset / folder /
                                #   one file / many) to workflow input files
      list/ loading/ metadata/ metadataSchema/ metadataV2/ modals/
      search/                 # ModernSearchContainer.tsx - main search UI
      searchSmall/ selectors/
      single/                 # Single-entity views (ViewFile, AssetIngestion, Metadata)
      table/

    pages/                  # Thin page wrappers composing components
      AssetDownload.tsx AssetUpload/
      auth/                   # Constraints, Roles, UserRoles, CognitoUsers, ApiKeys (Create/Update)
      Databases.tsx LandingPage.tsx ListPage.tsx ListPageNoDatabase.tsx MetadataSchema.tsx
      search/                 # SearchPage.tsx
      Subscription/ Tag/

      # Orchestration route shells — each reads route params and renders the matching
      # features/orchestration component; keep the page logic in the feature module.
      PipelinesPage2.tsx PipelineBuilderPage.tsx
      TemplateListPage.tsx TemplateBuilderPage.tsx
      WorkflowsPage2.tsx WorkflowBuilderPage.tsx WorkflowTriggersPage.tsx
      ExecutionsPage.tsx ExecutionDetail.tsx

    visualizerPlugin/       # 3D/media viewer plugin system — see CLAUDE.md there
      index.ts README.md
      config/viewerConfig.json  # Plugin configuration
      core/                     # PluginRegistry.ts, StylesheetManager.ts, types.ts
      components/               # Shared viewer UI components
      viewers/                  # Individual viewer plugins + manifest.ts

    common/                 # Shared utilities and helpers
      GlobalHeader.tsx common-components.tsx
      constants/              # apiKeys.ts authRoutes.ts featuresEnabled.ts fileFormats.ts
      helpers/                # labels.ts tableCounterStrings.ts
      utils/                  # utils.ts fileSize.ts

    constants/uploadLimits.ts
    hooks/                  # usePageTitle.ts, useThemeSettings.ts
    layout/Navigation.tsx   # Left sidebar navigation

    utils/
      apiEndpoint.ts        # Resolves the API base URL
      authTokenUtils.ts     # getDualValidAccessToken, getDualAuthorizationHeader
      sessionManager.ts     # Idle/expiry session handling
      fileExtensionValidation.ts
      fileHandleCompat.ts

    styles/                 # Global styles
      theme.css             # CSS custom properties for dark/light theming
      tailwind.css          # Tailwind entry (scoped content glob; preflight disabled — see Rule 2)
      index.scss base.scss dashboard.scss form.scss header.scss landing-page.scss
      onboarding.scss wizard.scss table-date-filter.scss table-select.scss
      components/

    resources/              # Static assets (images, logos)
    @types/                 # Custom TypeScript declarations
    __mocks__/              # Jest module mocks (the only .js files in src/)
```

---

## 3. Critical Rules

### Rule 1: Preserve Copyright Headers

Every source file MUST begin with the Apache-2.0 copyright header:

```typescript
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
```

NEVER remove or modify these headers. Use the `2026` year for new files.

### Rule 2: Use Cloudscape Components (with orchestration exception)

The EXISTING app uses AWS Cloudscape Design System. The NEW orchestration module (`src/features/orchestration/**`) is built with **Tailwind CSS + Radix UI** (the seed of the future design system) and is Cloudscape-free. This boundary is intentional:

-   **Existing pages** (Assets, Databases, Search, etc.) continue to use Cloudscape.
-   **`features/orchestration/**`\*\* (Pipelines, Workflows, Executions pages + wizard) uses Tailwind + Radix.
-   **Never leak Tailwind's preflight** into Cloudscape pages (preflight is disabled; Tailwind scoped to `src/features/orchestration/**` content glob).
-   **Tailwind's UTILITY CSS is global, even though its content glob is not.** The glob decides which files Tailwind _scans_ for class names; every utility it emits lands in one stylesheet loaded on every page. So a Cloudscape page that happens to use a class named like a Tailwind utility picks up Tailwind's rule. **Never name a plain layout div after a Tailwind utility** — `container`, `hidden`, `block`, `flex`, `grid`, `fixed` (verified present in the built CSS; the emitted set depends on what the orchestration module uses, so treat this as examples rather than a closed list). Outside the orchestration module, either use a VAMS-defined class or no class at all.

    This is not hypothetical: `<div className="container">` wrapped the asset-view comment editor, and because VAMS defines no `.container` rule, the only match was Tailwind's `.container` utility with its responsive max-widths (640/768/1024/1280/1536px). It capped the comment box on any wide viewport. Nothing in the source pointed at it — the editor was filling its parent correctly, the parent was the clamped element — so the cause was only visible by measuring the rendered DOM. When a width or spacing problem has no explanation in the component's own styles, walk the ancestors' computed `max-width` in the browser before changing the component.

Do NOT introduce Material UI, Ant Design, Chakra, or any other UI library outside this boundary.

```typescript
// CORRECT (Cloudscape pages)
import Button from "@cloudscape-design/components/button";
import Table from "@cloudscape-design/components/table";
import SpaceBetween from "@cloudscape-design/components/space-between";

// INCORRECT (barrel import — bundles entire library)
import { Button, Table } from "@cloudscape-design/components";

// CORRECT (orchestration module)
import { Button } from "@radix-ui/themes";
<div className="flex gap-2">...</div>;
```

**Always import individual Cloudscape components from their subpath**, not from the barrel export. This is critical for bundle size. In the orchestration module, use Tailwind utility classes and Radix primitives.

### Rule 3: All API Calls Go Through Service Layer Files

All API calls MUST go through service-layer files in `src/services/`. Components and pages MUST NEVER import `apiClient` directly.

```typescript
// CORRECT — import from a service file
import { fetchAssets, deleteAsset } from "../../services/APIService";
const result = await fetchAssets({ databaseId });

// INCORRECT — direct apiClient/fetch/axios in a component
import { apiClient } from "../../services/apiClient";
const response = await fetch(`/api/database/${databaseId}/assets`);
```

**Service files** (`src/services/` and `src/features/orchestration/api/`) are the ONLY files that may import `apiClient`:

-   `APIService.ts` — main API service (general CRUD, auth, search, subscriptions, tags, etc.)
-   `AssetUploadService.ts` — S3 upload operations
-   `AssetVersionService.ts` — version management
-   `FileOperationsService.ts` — file operations
-   `MetadataService.ts` — metadata CRUD
-   `MetadataSchemaService.ts` — schema management
-   `features/orchestration/api/*.ts` — orchestration services (pipelines, workflows, executions, templates, triggers)

When adding a new API endpoint, add the function to the appropriate service file (or `APIService.ts` if no specific service exists). Follow the `[boolean, data]` return tuple pattern. The orchestration services also use this tuple pattern (via a `toTuple` helper).

### Rule 4: npm Only

```bash
# CORRECT
npm install
npm run start
npm run build

# INCORRECT — NEVER use yarn
yarn install
```

### Rule 5: Keep the Platform-Specific Native Bindings in `optionalDependencies`

`vite build` needs three compiled binaries for the machine running it: **rolldown's** binding (Vite's bundler), **esbuild** (imported directly by the `jsxInJs` plugin in `vite.config.ts`), and **lightningcss** (Vite's CSS minifier, used by `vite:css-post`). npm records only the binaries matching the platform that generated the lockfile ([npm/cli#4828](https://github.com/npm/cli/issues/4828)) — a lockfile written on Windows lists `@rolldown/binding-win32-x64-msvc` and nothing else, and a later `npm install` on Linux does **not** add the missing one. The build then dies with one of:

```
Error: Cannot find native binding.
  cause: Cannot find module '@rolldown/binding-linux-x64-gnu'

[plugin vite:css-post] [lightningcss minify] Cannot find module '../lightningcss.linux-x64-gnu.node'
```

`web/package.json` therefore declares the bindings for every platform VAMS is built on explicitly:

```json
"optionalDependencies": {
    "@esbuild/darwin-arm64": "^0.28.1",
    "@esbuild/darwin-x64": "^0.28.1",
    "@esbuild/linux-x64": "^0.28.1",
    "@rolldown/binding-darwin-arm64": "^1.1.5",
    "@rolldown/binding-darwin-x64": "^1.1.5",
    "@rolldown/binding-linux-x64-gnu": "^1.1.5",
    "lightningcss-darwin-arm64": "^1.32.0",
    "lightningcss-darwin-x64": "^1.32.0",
    "lightningcss-linux-x64-gnu": "^1.32.0"
}
```

Each package carries its own `os`/`cpu` constraints, so a developer only ever installs their own platform's binary — declaring them costs nothing on disk and keeps the Linux CI build working. The Windows bindings stay in the lockfile through the ordinary dependency graph.

**These versions are coupled to `vite` and `esbuild` and no test catches drift.** When bumping `vite`, `rolldown`, `esbuild`, or `lightningcss`:

1. Read the resolved versions: `npm ls rolldown esbuild lightningcss`.
2. Re-pin every entry above to match, then `npm install`.
3. Confirm all platforms are still recorded — a bump can silently prune them again:

    ```bash
    node -e "const l=require('./package-lock.json');Object.entries(l.packages).filter(([,v])=>v.os).forEach(([k,v])=>console.log(k,v.os))"
    ```

    Expect `darwin`, `linux`, **and** `win32` rows for `@rolldown/binding-*`, `@esbuild/*`, and `lightningcss-*`. If a platform is missing, `npm install --package-lock-only --save-optional <pkg>@<version>` adds it. Note `npm install --force` and `--os`/`--cpu` do **not** repair an already-pruned lockfile.

A mismatch between the pinned binding version and the version `vite` resolves is the failure mode to watch for: npm installs the stale binding, the tool asks for the new one, and the build fails exactly as above.

**Verify a Linux build without waiting for CI.** Guessing which binding breaks next costs a CI round trip each time; running the real thing does not:

```bash
# From web/. customInstalls is excluded by vite.config.ts, so the build does not need it.
B=/tmp/linuxbuild && rm -rf $B && mkdir -p $B
cp package.json package-lock.json index.html vite.config.ts tsconfig.json \
   tailwind.config.js postcss.config.js babel.config.js vite-env.d.ts logo_*.png $B/
cp -r src public $B/
# Drop the postinstall viewer clones (git + private registry); they are not build inputs.
node -e "const p=require('$B/package.json');delete p.scripts.postinstall;require('fs').writeFileSync('$B/package.json',JSON.stringify(p,null,4))"
docker run --rm -v "$B:/app" -w /app node:22 bash -c "npm install --no-audit --no-fund && npm run build.lowmem"
```

`node:22` matches the GitHub runner (Node 22, glibc). Omitting `logo_*.png` produces an `UNRESOLVED_IMPORT` for `../../logo_white.png` — a gap in the copied fixture, not a real defect.

`@napi-rs/canvas`, `@parcel/watcher`, and `@unrs/resolver-binding` are also Windows-only in the lockfile and are deliberately **not** pinned: the Linux build above succeeds without them (they back optional canvas rendering, Sass file watching, and the ESLint resolver, none of which the production build loads). `infra/` carries the same `@esbuild/*` entries for its own reason — see `infra/CLAUDE.md`.

### Rule 6: HashRouter URLs

All routing uses `HashRouter`, meaning URLs are `/#/path`. When constructing internal links or navigation:

```typescript
// CORRECT - use relative paths, HashRouter handles the #
<Link to="/databases/mydb/assets">Assets</Link>;
navigate("/databases/mydb/assets");

// INCORRECT - hand-built hash URL for React Router (only OK for full-page redirect)
window.location.href = "/#/databases/mydb/assets";
```

### Rule 7: Lazy Load All Pages

Every page component in `routes.tsx` MUST be lazy-loaded:

```typescript
// CORRECT
const MyNewPage = React.lazy(() => import("./pages/MyNewPage"));

// INCORRECT — eager import defeats route-level code splitting
import MyNewPage from "./pages/MyNewPage";
```

### Rule 8: TypeScript Codebase

-   All source files are TypeScript (`.ts`/`.tsx`) -- new files MUST also be TypeScript
-   Only `src/__mocks__/*.js` files remain as `.js` (Jest CommonJS requirement)
-   Use `any` sparingly but pragmatically (the codebase uses it extensively)

---

## 4. API Integration Patterns

### 4.1 The Return Tuple Pattern

Most `APIService.ts` functions return `[boolean, data/errorMessage]` tuples. The new orchestration services (`features/orchestration/api/*.ts`) also use this pattern, wrapping raw API responses via a `toTuple` helper. TanStack Query hooks consume these tuples and expose the data to components:

```typescript
// CORRECT - follow the established return pattern
export const fetchSomething = async ({ databaseId }) => {
    try {
        const response = await apiClient.get(`database/${databaseId}/something`);
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};
```

Key patterns to follow:

1. Use `apiClient` for all API calls (auth headers injected automatically)
2. Error detection uses `response.message.indexOf("error") !== -1` (legacy pattern, maintain consistency)
3. Return `[false, errorMessage]` on error, `[true, data]` on success, `false` for unknown failures
4. Always log errors with `console.log` (NOT `console.error` -- match existing convention)

### 4.2 Consuming API Results

```typescript
// CORRECT -- always check the boolean flag
const result = await fetchAssets({ databaseId });
if (result === false || result[0] === false) {
    setError(result ? result[1] : "Unknown error");
    return;
}
const data = result[1]; // or result for non-tuple responses

// INCORRECT — assumes success, crashes on error tuple
const data = await fetchAssets({ databaseId });
setAssets(data);
```

### 4.3 Pagination Pattern

Backend uses `NextToken`-based pagination:

```typescript
let allItems = [];
let nextToken = null;
do {
    const response = await apiClient.get(endpoint, {
        queryStringParameters: {
            ...(nextToken && { startingToken: nextToken }),
        },
    });
    allItems = [...allItems, ...(response.items || [])];
    nextToken = response.nextToken;
} while (nextToken);
```

### 4.4 API Endpoint Patterns

Backend API endpoints follow REST conventions:

```
GET    /database/{databaseId}/assets              # List assets
GET    /database/{databaseId}/assets/{assetId}     # Get single asset
POST   /database/{databaseId}/assets               # Create asset
PUT    /database/{databaseId}/assets/{assetId}      # Update asset
DELETE /database/{databaseId}/assets/{assetId}      # Delete asset
POST   /database/{databaseId}/assets/{assetId}/download  # Download
POST   /auth/routes                                 # Check allowed routes
GET    /secure-config                               # Get runtime config
```

---

## 5. Authentication System

### 5.1 Dual Auth Architecture

The app supports two authentication modes, determined at runtime from `/api/amplify-config`:

1. **Cognito Mode** (default): Uses `@aws-amplify/ui-react` Authenticator component
2. **External OAuth2 Mode**: Uses `@badgateway/oauth2-client` with PKCE flow

The auth orchestrator is `src/FedAuth/Auth.tsx`. It decides which flow to use based on config:

-   `window.DISABLE_COGNITO === true` --> External OAuth2 mode
-   `window.COGNITO_FEDERATED === true` --> Federated Cognito mode
-   Otherwise --> Standard Cognito mode

### 5.2 Token Utilities

```typescript
import { getDualValidAccessToken, getDualAuthorizationHeader } from "../utils/authTokenUtils";

// Gets valid access token from whichever auth mode is active
const token = await getDualValidAccessToken();

// Gets authorization header for manual requests
const header = await getDualAuthorizationHeader();
```

### 5.3 Auth Data Flow

```
/api/amplify-config  -->  Amplify.configure() (v6)  -->  Auth.tsx decides mode
                                                           |
                     +-----------------+-----------------+
                     |                                   |
                Cognito Flow                    External OAuth2 Flow
        (aws-amplify/auth: fetchAuthSession,   (Custom TokenProvider +
         getCurrentUser, signInWithRedirect,    @badgateway/oauth2-client)
         signOut)
                     |                                   |
                     +-----------------+-----------------+
                                       |
                          getDualValidAccessToken()
                          getDualAuthorizationHeader()
                                       |
                      API calls via apiClient (auth header injected)
```

**Amplify v6 migration notes:**

-   Auth imports come from `aws-amplify/auth` (e.g., `fetchAuthSession`, `getCurrentUser`, `signOut`, `signInWithRedirect`)
-   Custom `TokenProvider` is used for external OAuth token integration
-   `appCache` replaces Amplify Cache for runtime config storage
-   Hub event names changed: `signIn` -> `signedIn`, `signOut` -> `signedOut`

### 5.4 User State

```typescript
// User is stored in localStorage as JSON
const user = JSON.parse(localStorage.getItem("user"));
// Email is stored separately
const email = localStorage.getItem("email");
```

---

## 6. State Management

### 6.1 No Global Store

This codebase uses NO global state library. State is managed through:

1. **React Context + useReducer** for cross-component shared state
2. **Component-local useState/useReducer** for component state
3. **localStorage** for persistence across sessions
4. **appCache** (`src/services/appCache.ts`) for runtime config and preferences
5. **URL params** via React Router v6 `useParams()`
6. **TanStack Query** for server state in the orchestration module (`features/orchestration/`) — caching, background refetch, smart polling (`computeRefetchInterval` keeps polling active only while non-terminal executions are visible)

### 6.2 Context Pattern

```typescript
// CORRECT -- follow the existing context pattern
import { createContext, useReducer } from "react";

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
    dispatch: any; // Match existing pattern -- don't over-type dispatch
};

export const MyContext = createContext<MyContextType | undefined>(undefined);
```

### 6.3 Existing Contexts

| Context              | File                            | Purpose                          |
| -------------------- | ------------------------------- | -------------------------------- |
| `AssetContext`       | `context/AssetContext.ts`       | Asset list state                 |
| `AssetDetailContext` | `context/AssetDetailContext.ts` | Single asset detail with reducer |

The orchestration module (`features/orchestration/**`) holds its shared server state in TanStack Query (`features/orchestration/api/queries.ts`) rather than a React context, so a new pipeline/workflow/execution surface adds a query key there instead of a provider here.

### 6.4 Permission Graying (Orchestration Module)

The orchestration module (`features/orchestration/`) implements **Tier-1 permission graying** via `useAllowedRoutes()` (fetches and caches `GET /auth/routes/api/allowed`). Components call `can(method, pathTemplate)` to check if the current user may invoke an endpoint, and gray/hide actions accordingly:

```typescript
const { can } = useAllowedRoutes();
const canDelete = can("DELETE", "/workflows/executions/{executionId}/permanent");
<Button disabled={!canDelete}>Permanent Delete</Button>;
```

**Admin-only actions** (Logs, Permanent-Delete) are hidden in menus and action rows when the route is not allowed. **Tier-2 is not pre-checked client-side** — the backend filters lists to what the user can access, so inaccessible objects never appear. A per-object action that returns 403 surfaces a clean inline message.

A **navigational surface** is the exception: the execution detail page's Logs tab stays present whatever the permission, and its panel states that logs are not viewable rather than rendering an empty viewer. Removing a tab from a strip makes the capability undiscoverable and reads as though the deployment has no logs at all, whereas an unavailable menu entry simply narrows a list of actions. Gate the panel's contents, not the tab.

---

### 6.5 Toast Notifications (Orchestration Module)

Every mutation in `features/orchestration/**` reports through `useToast()`
(`components/ToastProvider.tsx`) — a failure is always visible and a success is always confirmed.

**It renders through the same Cloudscape `Flashbar` the rest of the app notifies with**
(`components/search/SearchNotifications/ToastManager.tsx`), in the same fixed top-right position, with
the same durations (8s error / 5s otherwise) and the same shared `ToastNotification` shape from
`components/search/types`. A pipeline notification is indistinguishable from a search one. This is a
deliberate exception to the module's Cloudscape-free rule (Rule 2): that rule governs _page content_,
whereas the toast layer is a global overlay mounted from `App.tsx`, which already renders Cloudscape
(`TopNavigation`). Tailwind's preflight is disabled, so there is no style bleed either way.

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

Rules:

-   **Match the app's message convention**: a short header naming the outcome (`"Archive failed"`,
    `"Pipeline archived"` — mirroring `"Search failed"` / `"Search completed"`), with the entity name
    and the backend's message in the `description`. Do not put the entity name in the header.
-   **Never leave a mutation's `catch` as `console.error` only, and never use `alert()`.** A silent
    failure is indistinguishable from success; a native `alert()` is a blocking modal.
-   **Always confirm success when the surface disappears.** A form that closes or a page that navigates
    away on save gives no other feedback.
-   **Keep an inline message too where it has context** — a dialog-scoped failure, or a validation
    summary next to the offending fields. The toast is additive.
-   **`toastErrorMessage(err)`** normalizes an `Error`, a raw string, or a `{message|error|detail}`
    object into displayable text instead of `[object Object]`.
-   Import Cloudscape from its **subpath** (`@cloudscape-design/components/flashbar`), never the barrel.

What the provider adds over the search hook is lifecycle safety and mutation ergonomics: timers are
cleared on unmount, identical repeats collapse instead of stacking, and the stack is capped at four (a
group abort over many executions would otherwise fill the viewport). Its z-index is 4000 rather than
the search manager's 1000, because the module's dialogs sit at 3001 — a failure raised from inside a
modal would otherwise paint underneath it. `useToast()` outside a provider degrades to a logging no-op
rather than throwing.

---

## 7. Component Patterns

### 7.1 Component Organization

-   **Pages** (`src/pages/`) — thin wrappers composing components, lazy-loaded in routes
-   **Components** (`src/components/`) — organized by domain/feature
-   **Features** (`src/features/orchestration/`) — the pipelines/workflows/executions module, which
    owns its own pages, components, and API layer; its `src/pages/` entries are route shells only
-   **Layout** (`src/layout/`) — Navigation shell components
-   **Common** (`src/common/`) — shared utilities, helpers, labels, and feature-switch constants

### 7.2 Component Template (New Feature Component)

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

### 7.3 Page Template (New Page Wrapper)

```tsx
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import MyComponent from "../components/myfeature/MyComponent";

const MyPage: React.FC = () => {
    return <MyComponent />;
};

export default MyPage;
```

### 7.4 Adding a New Route

1. Create the page component in `src/pages/`
2. Add the lazy import in `src/routes.tsx`
3. Add the route entry to `routeTable` array

```typescript
// In routes.tsx:
const MyPage = React.lazy(() => import("./pages/MyPage"));

// In routeTable array:
{
    path: "/myfeature",
    Page: MyPage,
    active: "#/myfeature/",
},
{
    path: "/databases/:databaseId/myfeature",
    Page: MyPage,
    active: "#/myfeature/",
},
```

The route is automatically permission-filtered via `webRoutes()` API call. The backend must also allow the route.

---

## 8. Viewer Plugin System

The 3D/media viewer system is a plugin-based architecture under `src/visualizerPlugin/`:

-   **PluginRegistry** singleton at `visualizerPlugin/core/PluginRegistry.ts` manages all viewers
-   **Extension mapping** and per-viewer metadata (name, priority, category, `enabled`, `featuresEnabledRestriction`) live in `visualizerPlugin/config/viewerConfig.json`
-   **`viewers/manifest.ts`** exposes componentPath -> import paths so Vite can statically analyze dynamic imports
-   Some viewers require the `ALLOWUNSAFEEVAL` feature flag (Needle USD, SuperSplat Editor, Three.js CAD formats); the SuperSplat Editor is iframe-embedded under `public/viewers/supersplat/`
-   Per-viewer install steps live in `web/customInstalls/` and run via the `postinstall` chain in `web/package.json`

For the current viewer catalog, plugin config field reference, and the step-by-step "adding a new viewer plugin" walkthrough, see `web/src/visualizerPlugin/CLAUDE.md` (auto-loaded when editing viewer-plugin code).

---

## 9. Configuration System

### 9.1 Static Configuration

`src/config.ts` defines the `VAMSConfig` interface:

```typescript
interface VAMSConfig {
    APP_TITLE: string; // Display title
    CUSTOMER_LOGO?: string; // Optional custom logo URL
    DEV_API_ENDPOINT: string; // API endpoint (empty string = same origin)
}
```

### 9.2 Runtime Configuration

Configuration is loaded at startup in this order:

1. `GET /api/amplify-config` --> Cached in appCache, configures Amplify v6
2. `GET /api/secure-config` --> Additional config requiring auth
3. Both are stored in `appCache.setItem("config", ...)` for runtime access

**Note:** `appCache` (from `src/services/appCache.ts`) replaces Amplify Cache:

```typescript
import { appCache } from "../services/appCache";
const config = appCache.getItem("config");
```

### 9.3 Feature Flags

Feature flags are stored in `config.featuresEnabled` (array of strings):

```typescript
const config = appCache.getItem("config");
if (config?.featuresEnabled?.includes("LOCATIONSERVICES")) {
    // Enable map features
}
```

Known feature flags:

-   `LOCATIONSERVICES` -- Map/geospatial features
-   `NOOPENSEARCH` -- Disable OpenSearch-dependent features
-   `ALLOWUNSAFEEVAL` -- Required for Needle USD, SuperSplat Editor, and Three.js CAD formats (WASM loaders use eval)
-   Additional flags may exist in deployed configurations

### 9.4 Synonyms (Display Name Customization)

`src/synonyms.tsx` defines configurable display names for core entity types. **All user-visible text** containing "Asset", "Database", or "Comment" (Comments tab only) must use Synonyms instead of hardcoded strings.

Available synonyms: `Asset/Assets/asset/assets`, `Database/Databases/database/databases`, `Comment/Comments/comment/comments`.

```typescript
import Synonyms from "../../synonyms";

// Headers, labels, placeholders, error messages — use Synonyms
<Header>{Synonyms.Assets}</Header>
<FormField label={`${Synonyms.Asset} Name`}>
placeholder={`Search ${Synonyms.assets} and files...`}
setError(`${Synonyms.Asset} not found`);
```

**Critical rules:**

-   **Use Synonyms for**: headers, labels, descriptions, placeholders, alt text, error messages, success messages, button text, modal titles, empty state text — any text the user sees.
-   **Do NOT use Synonyms for**: API request body values (`entityName: "Asset"`), variable names, property names, type names, route paths, CSS classes, or `console.log` messages. API payloads must remain hardcoded.
-   **Comment synonyms** apply only to the Comments tab feature. The word "Comment" on version records (version comment fields, labels) should remain hardcoded.
-   **Match casing**: Use `Synonyms.Asset` for title case, `Synonyms.asset` for lowercase.

---

## 10. Styling

### 10.1 Style Approaches (in order of preference)

1. **Cloudscape component props** -- Use Cloudscape's built-in styling props first
2. **SCSS** -- Global styles in `src/styles/`, uses 7-1 SCSS architecture
3. **CSS Modules** -- Scoped styles (e.g., `loginbox.module.css`)
4. **styled-components** -- Used in some components
5. **Inline styles** -- Acceptable for simple one-off styles

### 10.2 SCSS Structure

```
src/styles/
  index.scss          # Main entry, imports all partials
  abstracts/          # Variables, mixins, functions
  base/               # Reset, typography, base styles
  components/         # Component-specific SCSS
  layout/             # Layout-level styles
  pages/              # Page-specific styles
  utilities/          # Utility classes
  *.scss              # Page-specific top-level files
```

### 10.3 Theming System

VAMS supports dark/light theme modes (default: dark):

-   **`src/styles/theme.css`** -- CSS custom properties for theme-aware colors (`.awsui-dark-mode` class on `body` triggers dark mode values)
-   **`useThemeSettings` hook** -- Manages theme preference (dark/light only, no System/Density), persisted to localStorage
-   **Settings dropdown** in TopNavigation allows users to switch between Light and Dark themes
-   **Login page** includes a TopNavigation with theme toggle via the `LoginHeader` component in `Auth.tsx`
-   Cloudscape's `applyMode()` is used to toggle Cloudscape's own dark mode
-   Amplify Authenticator dark mode CSS overrides in `loginbox.module.css`
-   Jodit editor dark mode CSS overrides in `Comments.css`
-   ReactFlow workflow editor dark mode in `theme.css`

When adding new styles, use CSS custom properties from `theme.css` or Cloudscape design tokens to ensure dark mode compatibility.

### 10.4 Style Rules

```scss
// CORRECT -- use Cloudscape design tokens when possible
@use "@cloudscape-design/design-tokens" as awsui;

.my-container {
    padding: awsui.$space-l;
    color: awsui.$color-text-body-default;
}

// INCORRECT — hardcoded colors/spacing that Cloudscape provides
.my-container {
    padding: 20px;
    color: #16191f;
}
```

---

## 11. Testing

### 11.1 Test Setup

-   **Framework:** Jest 30 (`jsdom` environment via `jest-environment-jsdom`), `@testing-library/react`
-   **Config:** `web/jest.config.js`; jest-dom matchers registered via `setupFilesAfterEnv` -> `src/setupTests.ts`
-   **Cloudscape preset:** `@cloudscape-design/jest-preset` (transforms Cloudscape CSS/JS)
-   **Coverage thresholds:** Very low (~1%, set in `jest.config.js`) — raise as coverage grows
-   **Run tests:** `npm test` (with coverage) or `npx jest` for a faster run

### 11.2 Test File Conventions

-   Test files are colocated with source: `MyComponent.test.tsx` next to `MyComponent.tsx`
-   OR in `__tests__/` directories
-   Coverage is sparse (~10 test files total) -- adding tests is welcome

### 11.3 Test Template

```tsx
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import MyComponent from "./MyComponent";

// Mock APIService
jest.mock("../../services/APIService", () => ({
    fetchSomething: jest.fn(),
}));

describe("MyComponent", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("renders loading state", () => {
        render(
            <MemoryRouter>
                <MyComponent />
            </MemoryRouter>
        );
        expect(screen.getByText(/loading/i)).toBeInTheDocument();
    });

    it("renders data after fetch", async () => {
        const { fetchSomething } = require("../../services/APIService");
        fetchSomething.mockResolvedValue([true, [{ id: "1", name: "Test" }]]);

        render(
            <MemoryRouter>
                <MyComponent />
            </MemoryRouter>
        );

        await waitFor(() => {
            expect(screen.getByText("Test")).toBeInTheDocument();
        });
    });
});
```

### 11.4 Jest Configuration Notes

-   Axios requires special mapping: `"^axios$": "axios/dist/axios.js"`
-   Cloudscape components need custom transformers (configured in `jest.config.js`)
-   `transformIgnorePatterns` must stay a SINGLE pattern: a file matching ANY ignore pattern is excluded from transformation, so every ESM package that needs transforming (Cloudscape, d3-\*, internmap, react-leaflet, axios) must be exempted in one combined negative lookahead
-   Jest 30 removed deprecated matcher aliases (`toBeCalled`, `toBeCalledWith`, ...) — use the `toHaveBeenCalled*` forms

### 11.5 End-to-End Tests (Playwright)

`web/e2e/` holds Playwright specs that drive the **deployed** application — the only layer that proves
a frontend change reached users. Jest proves a component behaves; Playwright proves the published
bundle behaves, so a fix living only in `src/` will still fail until the front end is rebuilt
(`npm run build`) and deployed.

Core specs (tracked) must pass against **any** sandbox — empty, freshly seeded, or long-lived — so
they derive their subject from whatever exists and skip when a precondition is absent. Per-change
specs stay untracked. Shared selector knowledge lives in `e2e/support/fixtures.ts`; `e2e/auth.setup.ts`
performs the one-time Cognito login and saves `storageState` for the rest of the suite.

See `web/e2e/CLAUDE.md` for the full rules, the tracked-vs-ad-hoc boundary, and the selector reference.

---

## 12. Development Workflow

### 12.1 Local Development

```bash
cd web
npm install           # Install dependencies + runs postinstall (viewer installs)
npm run start         # Start dev server (port 3001)
```

The dev server proxies API calls via Vite's proxy config. Set `DEV_API_ENDPOINT` in `config.ts` to point to a remote API or local backend.

### 12.2 Build

```bash
npm run build         # Production build (output: web/dist/)
```

### 12.3 Important: Postinstall Script

The `postinstall` script runs custom viewer install scripts. If `npm install` fails, check:

1. Individual viewer install scripts in `customInstalls/`
2. Node.js version compatibility

### 12.4 Pre-change Checklist

Before modifying any code:

-   [ ] Read the file(s) you intend to modify
-   [ ] Check for existing patterns in nearby files
-   [ ] Verify import paths (this project uses relative imports, NOT path aliases)
-   [ ] Ensure copyright header is present
-   [ ] Use Cloudscape components, not third-party UI
-   [ ] Follow existing code style (even if imperfect)

### 12.5 Post-change Checklist

After modifying code, verify: no new TypeScript errors (`npm run build`), no broken imports, Cloudscape imported from subpaths (Rule 2), API calls go through `src/services/` (Rule 3), new routes are lazy-loaded in `routeTable` (Rule 7), and user-visible entity names use `Synonyms` (section 9.4).

---

## 13. Anti-Patterns

These extend the Critical Rules above. Where a rule already covers the anti-pattern in section 3, only the one-line reminder is repeated here.

-   **Cloudscape barrel imports** — see Rule 2. Always import from the subpath.
-   **Naming a div after a Tailwind utility outside the orchestration module** (`container`, `hidden`, `block`, `flex`, …) — see Rule 2. Tailwind's utility CSS is global even where its content glob is not, so the class silently applies its rule to a Cloudscape page.
-   **`apiClient` / raw `fetch` / `axios` in components or pages** — see Rule 3. Only files in `src/services/` may import `apiClient`.
-   **`BrowserRouter`** — see Rule 6. The app uses `HashRouter`.
-   **Eagerly importing page components in `routes.tsx`** — see Rule 7. All pages must be `React.lazy`-loaded.
-   **yarn** — see Rule 4. npm only.

### 13.1 Do NOT Use Amplify Cache

```typescript
// INCORRECT — Amplify Cache is no longer used
import { Cache } from "aws-amplify";
Cache.setItem("config", data);

// CORRECT -- use appCache
import { appCache } from "../services/appCache";
appCache.setItem("config", data);
```

### 13.2 Do NOT Add Global State Libraries

No Redux, Zustand, MobX, Recoil, or Jotai. Use React Context + `useReducer` (see section 6.2) for shared state.

### 13.3 Do NOT Bypass the Auth Token Utilities

```typescript
// INCORRECT — manually getting tokens
const session = await AmplifyAuth.currentSession();
const token = session.getAccessToken().getJwtToken();

// CORRECT -- use the dual-mode token utility
import { getDualValidAccessToken } from "../utils/authTokenUtils";
const token = await getDualValidAccessToken();
```

### 13.4 Do NOT Hardcode Display Names

```typescript
// INCORRECT — hardcoded user-visible entity names
<Header>Assets</Header>

// CORRECT -- use Synonyms for customizable display names
import Synonyms from "../../synonyms";
<Header>{Synonyms.Assets}</Header>
<p>Select a {Synonyms.Database}</p>
```

See section 9.4 for the full Synonyms rules.

---

## 14. Key Dependencies

| Package                         | Version              | Purpose                              |
| ------------------------------- | -------------------- | ------------------------------------ |
| `react`                         | 18.3                 | UI framework (upgraded from 17.0.2)  |
| `typescript`                    | ^5.0.0               | Type system (upgraded from 4.4.4)    |
| `vite`                          | ^8.0.0               | Build tooling (upgraded from ^6)     |
| `aws-amplify`                   | v6 (latest)          | Auth integration (v6)                |
| `@cloudscape-design/components` | ^3.0.196             | AWS Cloudscape UI (existing pages)   |
| `@tanstack/react-query`         | v5                   | Server-state (orchestration module)  |
| `@tanstack/react-table`         | v8                   | Headless tables (orchestration)      |
| `tailwindcss`                   | latest               | Utility-first CSS (orchestration)    |
| `@radix-ui/*`                   | latest               | Headless primitives (orchestration)  |
| `@rjsf/core`                    | v5                   | Dynamic tag forms (orchestration)    |
| `@monaco-editor/react`          | latest               | Config editor (lazy-loaded)          |
| `react-hook-form`               | v7                   | Form validation                      |
| `zod`                           | latest               | Schema validation                    |
| `reactflow`                     | v11                  | DAG preview (replaced v9 EOL)        |
| `@badgateway/oauth2-client`     | 2.4.2                | External OAuth2 PKCE flow            |
| `react-router-dom`              | ^6.0.0               | Client-side routing                  |
| `styled-components`             | ^5.3.3               | CSS-in-JS (legacy usage)             |
| `three`                         | (via customInstalls) | 3D rendering engine                  |
| `maplibre-gl`                   | ^5.8.0               | Map rendering                        |
| `react-pdf`                     | ^10.1.0              | PDF viewing                          |
| `papaparse`                     | ^5.4.1               | CSV parsing                          |
| `dompurify`                     | ^3.4.11              | HTML sanitization                    |
| `sanitize-html`                 | ^2.11.0              | HTML sanitization                    |
| `@dnd-kit/core`                 | ^6.3.1               | Drag and drop                        |
| `@testing-library/react`        | 14                   | Testing utilities (upgraded from 11) |

### 14.1 Key Service Files

-   **`src/services/apiClient.ts`** -- Custom fetch-based API client, injects auth headers automatically via `getDualAuthorizationHeader()`
-   **`src/services/appCache.ts`** -- Replaces Amplify Cache for runtime config and preference storage

---

## 15. Gold Standard Reference Files

When in doubt about patterns, reference these well-structured files:

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
| File operations      | `src/services/FileOperationsService.ts`           |
| Token utilities      | `src/utils/authTokenUtils.ts`                     |
| Synonyms config      | `src/synonyms.tsx`                                |
| Navigation           | `src/layout/Navigation.tsx`                       |
| App shell            | `src/App.tsx`                                     |

---

## 16. New Service Function Template

When adding API service functions to `APIService.ts` or creating new service files:

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
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            // For list endpoints, data is typically in a named property
            return response;
        }
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};
```

---

## 17. Common Tasks Quick Reference

### Adding a new Cloudscape table page

1. Create component in `src/components/myfeature/MyFeatureTable.tsx`
2. Use `@cloudscape-design/collection-hooks` for filtering/sorting
3. Create page wrapper in `src/pages/MyFeaturePage.tsx`
4. Add lazy import + route in `src/routes.tsx`
5. Add navigation item in `src/layout/Navigation.tsx`

### Adding a new API call

1. Add the function to `src/services/APIService.ts` following section 16 template
2. Use `apiClient` for API calls (auth headers injected automatically)
3. Return `[boolean, data]` tuple
4. Handle errors with `try/catch`, return `[false, error?.message]`

### Adding a new viewer plugin

See `src/visualizerPlugin/CLAUDE.md`.

### Adding a new context

1. Create context file in `src/context/` following the `AssetDetailContext.ts` pattern
2. Export context, reducer, action types
3. Wrap provider around the relevant component tree
4. Consume with `useContext()` in child components

### Modifying authentication flow

1. Read `src/FedAuth/Auth.tsx` thoroughly before changes
2. Token logic lives in `src/utils/authTokenUtils.ts`
3. Test BOTH Cognito and External OAuth2 modes
4. Be aware of `window.DISABLE_COGNITO` and `window.COGNITO_FEDERATED` globals

---

## 18. Environment and Build Notes

### 18.1 Build Output

Build output is `web/dist/` (not `web/build/`). Vite does not require the 8GB heap flags that CRA needed.

### 18.2 Environment Variables

Environment variables use the `VITE_*` prefix instead of `REACT_APP_*`. Access them via `import.meta.env.VITE_MY_VAR`.

### 18.3 Proxy

Vite's `server.proxy` in `vite.config.ts` configures the development proxy. API calls to `/api/*` are proxied to the backend.

### 18.4 COI Service Worker

A Cross-Origin Isolation (COI) service worker is included for WASM-based viewers that require `SharedArrayBuffer`. This is loaded automatically in production.

### 18.5 Browser Support

Production targets: `>0.2%`, `not dead`, `not op_mini all`
Development targets: Latest Chrome, Firefox, Safari.

### 18.6 React Version

This project uses **React 18.3** (upgraded from 17.0.2). The entry point (`index.tsx`) uses `createRoot` (React 18 style). React 18 APIs (`useId`, `useTransition`, `useDeferredValue`, automatic batching) are allowed in the orchestration module (`features/orchestration/`) but should be used sparingly elsewhere to maintain consistency with the existing codebase conventions.
