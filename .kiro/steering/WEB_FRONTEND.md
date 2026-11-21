# VAMS Web Frontend Steering (Kiro)

This is the Kiro front-end steering document for the VAMS (Visual Asset Management System) web application (`web/`). It mirrors the canonical `web/CLAUDE.md` content for the Kiro coding agent, and its viewer plugin sections are also the Kiro counterpart for `web/src/visualizerPlugin/CLAUDE.md`. When front-end standards change, update both this file and the matching `CLAUDE.md`.

> **Add-on steering:** an add-on's own steering lives in `.kiro/steering/ADDON_*.md` (`inclusion: fileMatch`), which loads when you edit that add-on's files; this document covers core and the lanes an add-on plugs into.

---

## 1. Architecture Overview

VAMS frontend is a **React 18.3 + TypeScript ^5.0.0** single-page application built with Vite. All source files in `src/` have been converted to `.ts`/`.tsx` (the only remaining `.js` files are Jest mocks in `src/__mocks__/`).

**Primary UI library:** AWS Cloudscape Design System (`@cloudscape-design/components ^3.0.196`).

**Package manager:** npm. NEVER use yarn in this project.

**Key design decisions:**

-   NO global state library (no Redux, no Zustand, no MobX)
-   React Context API + useReducer for shared state
-   Custom apiClient (fetch-based) for API calls, AWS Amplify v6 for authentication
-   HashRouter (`#/` URLs) for all routing
-   Plugin-based 3D viewer architecture (23 viewer plugin directories, 25 registered viewers)
-   `lazyWithRetry` (React.lazy plus chunk-download retry) + Suspense for route-level code splitting

---

## 2. Directory Structure

> **Maintenance note:** Update this tree when adding new components, pages, services, or viewer plugins. See root `CLAUDE.md` Rule 11.

```
web/
  package.json              # npm, React 18, Vite scripts
  e2e/                      # Playwright specs against a deployed stack — see e2e/CLAUDE.md
  customInstalls/           # Viewer plugin custom install scripts
    babylonjs/
    cesium/
    needletools-usd-viewer/
    online3dviewer/
    playcanvas/
    potree/
    robotics/                 # Lichtblick robotics data viewer, built from source with a VAMS overlay
    supersplat/
    thatopenwebifc/           # That Open Engine (web-ifc) IFC/BIM viewer bundle
    threejs/
    utility/
    veerum/
    vntana/
  src/
    App.tsx                 # Root app shell, HashRouter, TopNavigation (Settings menu, profile menu)
    routes.tsx              # Centralized route table (core + add-on + custom), lazyWithRetry, permission filtering
    config.ts               # Static config (VAMSConfig: APP_TITLE, DEV_API_ENDPOINT)
    synonyms.tsx            # Configurable display names (Asset, Database, Comment)
    index.tsx               # Entry point, createRoot
    setupTests.ts           # Jest setup

    features/orchestration/ # Pipeline/workflow/execution management (Tailwind + Radix)
      api/                  # Services + TanStack Query hooks + qk key factory
                            #   pipelines.ts workflows.ts executions.ts assets.ts databases.ts
                            #   client.ts queries.ts triggerCache.ts
      permissions/useAllowedRoutes.ts  # Tier-1 permission gating
      permissions/routeDisabledReason.ts  # disabledReason text for a route-gated button
      permissions/systemRecordReasons.ts  # disabledReason text for actions refused on system records
      components/           # Cloudscape-free primitives (DataTable, StatusBadge, ContextMenu,
                            #   Stepper, Breadcrumb, SearchableSelect, ConfigEditor, Dialog (+DialogFooter),
                            #   Callout, VirtualList (fixed-row-height windowed list), ...)
                            #   DisabledReason.tsx — tooltip + button for a disabled control's reason
                            #   BusyControls.tsx — BusyButton, BusyInput, openWhileBusy: a control whose own
                            #     request is in flight keeps focus (aria-disabled, not disabled); Dialog `busy`
                            #     holds a dialog open while its footer's request runs
                            #   LoadMoreButton.tsx, useFocusHandOff.ts — focus moves on, not to the page body,
                            #     when the answer to a request removes the control that holds it
                            #   useReturnFocus.ts — a dialog returns focus to its opener as it closes (Dialog
                            #     and Drawer call it); rowActionsFor / rowActionsTrigger / controlNamed name
                            #     a row-menu dialog's target (see 6.4)
                            #   RowActionsButton.tsx — three-dot SVG trigger for a row's ContextMenu
                            #   ToastProvider.tsx — notifications for the whole module
      pipelines/            # PipelinesPage.tsx, PipelineForm.tsx (wizard; execution-type fields
                            #   live under executionConfig.sqs / executionConfig.eventBridge),
                            #   TemplateEditor.tsx TemplateForm.tsx TagSchemaBuilder.tsx
                            #   TemplateOverridesEditor.tsx pipelineValidation.ts
                            #   templateBodyValidation.ts (tag placeholder quoting + the json
                            #   body shape check mirrored from the backend)
      workflows/            # WorkflowsPage.tsx WorkflowBuilder.tsx PipelineOrderList.tsx
                            #   TriggersEditor.tsx (live) TriggerDraftsEditor.tsx (create) over
                            #   TriggerList.tsx TriggerForm.tsx triggerDraft.ts triggerStyles.ts
                            #   WorkflowSystemConfigFields.tsx DagPreview.tsx
                            #   WorkflowValidationPanel.tsx workflowValidation.ts
      executions/           # ExecutionsBoard.tsx ExecutionDetailPage.tsx ExecutionLogViewer.tsx
                            #   ExecutionQuickView.tsx ExecutionRowActions.tsx
                            #   ExecuteWorkflowButton.tsx ExecuteWorkflowModal.tsx logSearch.ts
                            #   SubProcessesSection.tsx StageTimeline.tsx (details Sub-processes + stages)
                            #   resourceTypeLabels.ts (sub-process type labels; unknown types render raw,
                            #   with a not-abortable note when abortable === false)
      wizard/               # ExecuteWizard.tsx (ExecuteWizardBody) + WizardRail, RequirementsStrip,
                            #   WorkflowPicker, WizardPipelineStage/WizardInputStage/WizardReviewStage,
                            #   railSteps.ts reviewBlockers.ts InputFileSelector, MetadataSourceSelector,
                            #   SelectedInputFilesList + BulkFilePicker + selectedInputFiles.ts (the
                            #   multi-file selection: windowed list, bulk/paste picker, dedupe, 1000 cap),
                            #   RestrictionSummary, resolveRestrictions.ts resolveTemplate.ts
      types.ts reservedTagKeys.ts

    FedAuth/                # Authentication orchestrator
      Auth.tsx              # Dual-mode auth: Cognito OR External OAuth2
      oauthSignInState.ts   # External OAuth: opaque state, same-origin return page, one-time values
      allowedApiRoutesCache.ts # Caches the API allowed-routes listing and tells the navigation

    authenticator/          # Cognito Authenticator UI components
      Header.tsx
      Footer.tsx
      SignInHeader.tsx
      SignInFooter.tsx

    services/               # API and data services (may import apiClient — Rule 3 lists the other directories that may)
      APIService.ts         # Main API service (~3,500 lines, 110+ exports)
      AssetUploadService.ts # S3 multipart upload logic
      AssetVersionService.ts  # Includes updateAssetVersion, archiveAssetVersion, unarchiveAssetVersion
      FileOperationsService.ts
      MetadataService.ts    # Metadata CRUD operations
      MetadataSchemaService.ts  # Schema management
      ComplianceService.ts  # Compliance schemas, bindings, evaluations, quarantine, cascades, audit
      apiClient.ts          # Custom fetch-based client, injects auth headers
      networkFailure.ts     # NETWORK_FAILURE_MESSAGE, isNetworkFailure, requestFailureMessage (re-exported by apiClient)
      appCache.ts           # Replaces Amplify Cache for runtime config
      webRoutesCheck.ts     # Batched + cached web-route (Tier-1) checks
      addons/physnaAPIService.ts  # Add-on API calls (Physna viewer metadata)

    addons/                 # Add-on web lane: addonRoutes.tsx, addonNavigation.ts, addonFeatures.ts (add-on feature-switch names)
      edh/                  #   EDH Workstations add-on module (Tailwind + Radix, pages/ route shells) — see .kiro/steering/ADDON_EDH_WORKSTATIONS.md
    custom/                 # FORK EXTENSION POINT (shipped empty, never edited upstream)
      customRoutes.tsx      #   customRouteTable, appended to routeTable in routes.tsx
      customNavigation.ts   #   customNavSections, appended by layout/navigationRegistry.ts
      customFeatures.ts     #   names of the fork's feature switches
      services/             #   fork API calls (may import apiClient)
      pages/                #   fork pages

    context/                # React Context providers
      AssetContext.ts        # NOTE: typo is intentional, do NOT rename
      AssetDetailContext.ts  # useReducer-based context
      RuntimeConfigContext.ts # Live runtime config from Auth (useFeaturesEnabled); falls back to appCache outside the provider

    components/             # Domain/feature components
      asset/                # Asset viewing (ViewAsset.tsx is the main detail page)
        tabs/               # FileManager, Versions, AssetLinks, Comments, AssetExecutions,
                            #   Compliance tabs
        versions/           # Asset version management
          AssetVersionManager.tsx
          AssetVersionComparison.tsx
          components/       # Edit and archive/unarchive version modals
          hooks/
      common/               # ErrorBoundary, LoadingSpinner, StatusMessage
      compliance/           # ComplianceSchemaEditor.tsx (JSON + visual builder over vams-rules-v1
                            #   rules: "Add rule" for all six types, the pipeline-rule input-file
                            #   selector (mode / globs / explicit paths), the metadata-rule scope
                            #   form (scope select, include/exclude paths, link direction + type),
                            #   typed checks for fileStructure / versionCoverage / assetAttributes);
                            #   complianceSchemaRules.ts (pure pipelineRef fields/validation, rule-draft
                            #   round-trip, example rule per type, metadata-scope switching/validation
                            #   and check validators carrying the API's messages);
                            #   ReasonModal.tsx (Cloudscape modal collecting a reason: mandatory to
                            #   grant a quarantine exception or release from quarantine, `optional`
                            #   to approve or reject a cascade); complianceStateBadge.tsx
                            #   (shared compliance-state -> StatusIndicator map + Badge color accessor;
                            #   `exception` renders as info/blue; schemaSourceLabel says a binding is
                            #   inherited or set on the asset; EvaluationErrorIndicator flags a
                            #   record whose lastEvaluationStatus is `error`);
                            #   EvaluationHistoryModal.tsx (one asset's paged evaluation history with schema
                            #   version and rule results, opened from the database overview);
                            #   ExecutionFailureReasons.tsx (a failed pipeline rule's execution failure
                            #   reason, under the rule result, the asset tab's state and the quarantine
                            #   reason; the API returns it only to a caller who may read the execution);
                            #   RevokeExceptionModal.tsx (the revoke confirmation shared by the asset tab
                            #   and the database overview); bindingConfirmations.ts (what the
                            #   confirmations of a database binding's Bind, Change and Remove say,
                            #   shared by the database overview and the database editor);
                            #   cascadeNodes.tsx (a cascade's node outcomes, paged and sorted in the
                            #   browser); rowActions.ts (ROW_ACTIONS_STICKY_COLUMNS: the
                            #   compliance tables pin their inline-link Actions column last and wrap the rest);
                            #   listingFilters.tsx (URL-backed filters: useUrlFilters(keys, pageRoute) reads
                            #   and writes only the page's own route's URL; debounce, DatabaseFilter,
                            #   useLatestRequest)
      createupdate/         # CreateDatabase.tsx, UpdateAsset.tsx + form definitions
      filemanager/          # File tree and file operations; EnhancedFileManager lazy-loads the
                            #   orchestration execution quick view for a file's "View execution" link
        components/         # FileDetailsPanel, AutomationActions (lazy-loads the execute modal),
                            #   tree views, preview thumbnails, splitters
        utils/              # automationSelection.ts maps a selection to workflow input files;
                            #   executionLinks.ts maps workflow-execution provenance to the execution
                            #   detail route and the Tier-1 route the link is gated on
      form/                 # DragDropFileUpload (shared upload control, onAdd(roots)), uploadPaths.ts
                            #   (the one upload path rule), UploadPreview; see section 7.8
      list/                 # TableList.tsx (the standard list page: search + refresh left, filters
                            #   right, collection-hooks filtering/sorting/paging); ListToolbar.tsx (that
                            #   toolbar for a page with its own Table, 7.7); tableSorting.ts (column
                            #   comparators byText/byDate/byNumber, useSortedItems for a page the server
                            #   returned, useBrowserPagedItems for a whole list, PAGE_SORTING_NOTE)
      loading/              # Loading screens and spinners
      metadata/
      metadataSchema/
      metadataV2/
      modals/
      search/               # ModernSearchContainer.tsx - unified keyword / natural-language search
                            #   (the body of the search page's "Search" view)
                            #   SearchLayout/lockedDatabase.ts — trigger text of the Database filter
                            #   a database route locks (names the database, not "All databases")
      searchSmall/
      selectors/
      single/               # Single-entity views (ViewFile, AssetIngestion, Metadata)
      table/

    pages/                  # Thin page wrappers composing components
      AssetDownload.tsx
      AssetUpload/          # Upload page and modify uploads
      auth/                 # Constraints, Roles, UserRoles, CognitoUsers, ApiKeys, AuditLogSearch
        ApiKeys.tsx           # API key list page
        CreateApiKey.tsx      # Create API key modal
        UpdateApiKey.tsx      # Update API key modal
      Databases.tsx
      LandingPage.tsx
      ListPage.tsx          # keys its body by the route's databaseId: the routes that share
                            #   one page component keep it mounted (#1228)
      ListPageNoDatabase.tsx
      MetadataSchema.tsx
      search/               # SearchPage.tsx — breadcrumbs + searchPlugin's SearchTabsHost (title row, views)
      Subscription/
      Tag/

      # Compliance pages — permission-filtered via webRoutes() like every other page (no feature
      # flag); each deploys unconditionally and the nav/routes filter hides them without access.
      ComplianceSchemas.tsx
      ComplianceQuarantine.tsx
      ComplianceCascades.tsx
      ComplianceAuditLog.tsx
      DatabaseCompliance.tsx

      # Orchestration route shells — each reads route params and renders the matching
      # features/orchestration component; keep the page logic in the feature module.
      PipelinesPage2.tsx
      PipelineBuilderPage.tsx
      TemplateListPage.tsx
      TemplateBuilderPage.tsx
      WorkflowsPage2.tsx
      WorkflowBuilderPage.tsx
      WorkflowTriggersPage.tsx
      ExecutionsPage.tsx
      ExecutionDetail.tsx

    visualizerPlugin/       # 3D/media viewer plugin system
      index.ts              # Public API
      README.md             # Plugin development docs
      config/
        viewerConfig.json   # Plugin configuration (core)
        addonViewerConfig.json   # Add-on viewers (Physna and the other add-ons'; licensed VNTANA, VEERUM)
        customViewerConfig.json  # FORK EXTENSION POINT (shipped empty)
      core/
        PluginRegistry.ts   # Singleton registry (manages all viewers)
        viewerSources.ts    # Merges core + add-on + custom configs and manifests
        StylesheetManager.ts
        streamFailure.ts    # Names why a streamed or downloaded file failed to load
        types.ts
      components/           # Shared viewer UI components
      viewers/              # Individual viewer plugins
        manifest.ts         # Static analysis manifest
        addonManifest.ts    # Add-on viewer manifest
        custom/             # FORK EXTENSION POINT: customManifest.ts + fork viewer plugins
        AudioViewerPlugin/
        BabylonJSGaussianSplatViewerPlugin/
        CesiumViewerPlugin/
        ColumnarViewerPlugin/
        HTMLViewerPlugin/
        ImageViewerPlugin/
        NeedleUSDViewerPlugin/
        Online3dViewerPlugin/
        PDFViewerPlugin/
        PhysnaViewerPlugin/
        PlayCanvasGaussianSplatViewerPlugin/
        PotreeViewerPlugin/
        RoboticsDataViewerPlugin/    # MCAP / ROS bag / PX4 ULog viewer (framed Lichtblick app)
        SuperSplatViewerPlugin/
        TextViewerPlugin/
        ThatOpenWebIfcViewerPlugin/  # IFC/BIM viewer (That Open Engine, web-ifc)
        ThreeJSViewerPlugin/
        VeerumViewerPlugin/
        VideoViewerPlugin/
        VntanaViewerPlugin/

    searchPlugin/           # Search page provider host (title row + view switch); see WEB_FRONTEND.md 8.5
      index.ts
      config/searchProviderConfig.json  # Provider catalog (searchProviderConfig.test.ts guards it)
      core/                 # SearchProviderRegistry.ts, providerAvailability.ts,
                            #   useSearchProvidersReady.ts, types.ts
      components/SearchTabsHost.tsx  # title row (h1, view switch, titleSlots) + active body
      providers/            # manifest.ts + AssetListProvider/, UnifiedSearchProvider/

    common/                 # Shared utilities and helpers
      GlobalHeader.tsx
      common-components.tsx
      constants/            # apiKeys.ts authRoutes.ts featuresEnabled.ts fileFormats.ts
      helpers/              # labels.ts tableCounterStrings.ts
      utils/                # utils.ts fileSize.ts

    constants/
      uploadLimits.ts

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
      fileExtensionValidation.ts
      fileHandleCompat.ts
      maplibreWorker.ts     # setWorkerUrl() for maplibre-gl's bundled worker; import for side effect before a map mounts

    styles/                 # Global styles
      theme.css             # CSS custom properties for dark/light theming
      tailwind.css          # Tailwind entry (scoped content glob; preflight disabled)
      base.scss
      components/
      dashboard.scss
      form.scss
      header.scss
      index.scss
      landing-page.scss
      onboarding.scss
      table-date-filter.scss
      table-select.scss
      wizard.scss

    resources/              # Static assets (images, logos)
    @types/                 # Custom TypeScript declarations
    __mocks__/              # Jest module mocks (the only .js files in src/)
```

---

`common/constants/fileFormats.ts` is the one definition of the formats and size the web shows as a file's own preview: `previewFileFormats` (mirrors the backend's `ALLOWED_PREVIEW_FILE_EXTENSIONS`; `.tga`, `.tif` and `.tiff` are never in it because browsers cannot display them), `PREVIEW_FILE_SIZE_LIMIT_BYTES` (5 MiB) and `isSelfPreviewable(fileName, size)` (extension in the list and size known and STRICTLY under the limit). The search thumbnail (`FilePreviewThumbnailCell.tsx`) and the file details panel call the helper instead of repeating the check, and the 3D thumbnail pipeline writes no preview for such a file. Drift tests pin the lists and the limit to the backend and the container.

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

The existing app uses AWS Cloudscape Design System. The orchestration module (`src/features/orchestration/**`) is built with **Tailwind CSS + Radix UI** and is Cloudscape-free. This boundary is intentional:

-   **Existing pages** (Assets, Databases, Search, etc.) continue to use Cloudscape.
-   **`features/orchestration/**`\*\* (Pipelines, Workflows, Executions pages + wizard) uses Tailwind + Radix.
-   **Never leak Tailwind's preflight** into Cloudscape pages (preflight is disabled; Tailwind's content glob covers `src/features/orchestration/**`, each Tailwind add-on module under `src/addons/<addon>/**`, and the nine orchestration route shells in `src/pages/`, each named individually rather than as `src/pages/**` or `src/addons/**`). A new orchestration shell or Tailwind add-on module must be added to that list in `web/tailwind.config.js` or its Tailwind classes are not emitted; a Cloudscape page must **not** be added, because scanning it is what makes the collision below possible.
-   **Tailwind's UTILITY CSS is global, even though its content glob is not.** The glob decides which files Tailwind _scans_ for class names; every utility it emits lands in one stylesheet loaded on every page, so a Cloudscape page using a class named like a Tailwind utility picks up Tailwind's rule. **Never name a plain layout div after a Tailwind utility** — `container`, `hidden`, `block`, `flex`, `grid`, `fixed` (examples verified in the built CSS; the emitted set depends on what the orchestration module uses). Outside the orchestration module, use a VAMS-defined class or no class at all.
-   **An add-on module's glob widens that global sheet on every deployment.** The utilities a Tailwind add-on module under `src/addons/<addon>/**` uses are emitted into the same stylesheet whether or not the add-on is enabled (the EDH module adds about 25), so the naming rule above covers them too.

Do NOT introduce Material UI, Ant Design, Chakra, or any other UI library outside this boundary.

```typescript
// CORRECT (Cloudscape pages)
import Button from "@cloudscape-design/components/button";
import Table from "@cloudscape-design/components/table";
import SpaceBetween from "@cloudscape-design/components/space-between";

// INCORRECT -- never import from the barrel export
import { Button, Table } from "@cloudscape-design/components";

// CORRECT (orchestration module: Radix primitives styled with Tailwind, as in components/Dialog.tsx)
import * as RadixDialog from "@radix-ui/react-dialog";
<RadixDialog.Content className="rounded-md bg-white p-4 shadow-lg">...</RadixDialog.Content>;
```

**Always import individual Cloudscape components from their subpath**, not from the barrel export. This is critical for bundle size. In the orchestration module, use Tailwind utility classes and Radix primitives.

This applies to new imports. A new file, or a Cloudscape component newly imported into an existing file, uses a subpath import, even in a file that already imports from the barrel. Existing barrel imports stay as they are; do not convert them.

### Rule 3: All API Calls Go Through Service Layer Files

All API calls MUST go through service-layer files in `src/services/`. Components and pages MUST NEVER import `apiClient` directly.

```typescript
// CORRECT — import from a service file
import { fetchAssets, deleteAsset } from "../../services/APIService";
const result = await fetchAssets({ databaseId });

// INCORRECT — never import apiClient in components or pages
import { apiClient } from "../../services/apiClient";
const response = await apiClient.get(`database/${databaseId}/assets`);

// INCORRECT — never use fetch or axios directly
const response = await fetch(`/api/database/${databaseId}/assets`);
```

**Service files** (`src/services/`, `src/features/orchestration/api/`, an add-on module's own `api/` directory under `src/addons/<addon>/` and, in a fork, `src/custom/services/`) are the ONLY files that may import `apiClient`:

-   `APIService.ts` — main API service (general CRUD, auth, search, subscriptions, tags, etc.)
-   `AssetUploadService.ts` — S3 upload operations
-   `AssetVersionService.ts` — version management
-   `FileOperationsService.ts` — file operations
-   `MetadataService.ts` — metadata CRUD
-   `MetadataSchemaService.ts` — schema management
-   `addons/*.ts` — add-on API calls (`addons/physnaAPIService.ts` for the Physna viewer, and one per add-on)
-   `features/orchestration/api/*.ts` — orchestration services (pipelines, workflows, executions, templates, triggers)
-   `addons/<addon>/api/*.ts` (under `src/`) — the services of an add-on that ships its own module, beside its types and query hooks
-   `custom/services/*.ts` — the fork extension point for a fork's own API calls (shipped empty)

When adding a new API endpoint, add the function to the appropriate service file (or `APIService.ts` if no specific service exists). Follow the `[boolean, data]` return tuple pattern. The orchestration services also use this tuple pattern (via a `toTuple` helper). An add-on's calls go in `services/addons/`, or in its module's `api/` directory when it ships a module of its own, never in `APIService.ts`.

### Rule 4: npm Only

**Package manager:** npm 11.10.0+. Node 22.x ships with npm 10.9.x, which does **not** honor `min-release-age` (introduced in npm 11.10.0). Always pin npm before installing:

```bash
# Pin npm to the required version first
npm install -g npm@11.19.1

# CORRECT
npm install
npm run start
npm run build

# INCORRECT -- NEVER use yarn
yarn install
```

The viewer installs the `postinstall` chain runs under `web/customInstalls/*` do not inherit `web/.npmrc`, because npm reads a project `.npmrc` only from the directory of the `package.json` it installs. Each viewer package directory therefore carries its own `.npmrc` (a copy of `web/.npmrc`), and an install inside a cloned upstream repository (SuperSplat, Potree, Needle USD) passes `--min-release-age=7` on the command line. `src/visualizerPlugin/config/customInstallsNpmIntake.test.ts` fails when a new viewer directory or clone install is not covered (#334).

### Rule 6: HashRouter URLs

All routing uses `HashRouter`, meaning URLs are `/#/path`. When constructing internal links or navigation:

```typescript
// CORRECT - use relative paths, HashRouter handles the #
<Link to="/databases/mydb/assets">Assets</Link>;
navigate("/databases/mydb/assets");

// INCORRECT - never construct hash URLs manually for React Router
window.location.href = "/#/databases/mydb/assets"; // Only use for full-page redirect
```

### Rule 7: Lazy Load All Pages

Every page component in `routes.tsx` MUST be lazy-loaded:

```typescript
// CORRECT
const MyNewPage = lazyWithRetry(() => import("./pages/MyNewPage"));

// INCORRECT
import MyNewPage from "./pages/MyNewPage";
```

### Rule 8: TypeScript Codebase

-   All source files are TypeScript (`.ts`/`.tsx`) -- new files MUST also be TypeScript
-   Only `src/__mocks__/*.js` files remain as `.js` (Jest CommonJS requirement)
-   Use `any` sparingly but pragmatically (the codebase uses it extensively)

### Rule 9: Regenerate the CSP Hashes After Touching an Inline Script in `index.html`

`index.html` contains inline `<script>` blocks (the `__publicField` polyfill, the `SharedArrayBuffer`
probe, and the pre-render theme application). The CDK Content-Security-Policy allows them by
**SHA-256 hash**, not by `'unsafe-inline'`, so an injected inline script is still blocked.

A CSP hash covers the **exact text content** of the element -- every byte between the opening and
closing tag, indentation included. Adding a line, renaming a variable, or letting Prettier reindent
the block invalidates its hash. The browser then silently refuses to run that script and the app
breaks at runtime, with nothing failing at build time.

**Any edit to an inline `<script>` block in `web/index.html` -- including a reformat -- requires
regenerating the hashes and updating the CDK constant in the same change:**

```bash
# 1. Build, so the hashes are taken from the HTML that is actually served
cd web && npm run build

# 2. Emit the TypeScript constant
node scripts/cspInlineScriptHashes.js --ts

# 3. Paste the output over INDEX_HTML_INLINE_SCRIPT_HASHES in
#    infra/lib/helper/cspInlineScriptHashes.ts

# 4. Confirm the drift guard passes
cd ../infra && npx jest test/web/cspInlineScriptHashes.test.ts
```

Run the generator with no `--ts` for a human-readable listing of each block and its hash.

| File                                           | Role                                                                     |
| ---------------------------------------------- | ------------------------------------------------------------------------ |
| `web/index.html`                               | The inline scripts being hashed                                          |
| `web/scripts/cspInlineScriptHashes.js`         | Generator -- hashes every inline block (skips any with `src`)            |
| `infra/lib/helper/cspInlineScriptHashes.ts`    | The generated constant. **Generated -- do not hand-edit**                |
| `infra/lib/helper/security.ts`                 | `generateContentSecurityPolicy()` spreads the constant into `script-src` |
| `infra/test/web/cspInlineScriptHashes.test.ts` | Recomputes from `index.html` and fails on drift                          |

**A hash and `'unsafe-inline'` are mutually exclusive.** A CSP may allow inline script by hash **or**
by the `'unsafe-inline'` keyword, never both -- when a hash source is present browsers ignore
`'unsafe-inline'` entirely. The two are not additive, so `'unsafe-inline'` cannot be left in as a
safety net.

Because of that, `generateContentSecurityPolicy()` adds `'unsafe-inline'` in **no** configuration,
the Physna add-on included. The add-on's viewer frames Physna's own HTTPS origin rather than a
`blob:` document, so that page loads under Physna's own CSP and its inline scripts are outside this
policy's reach; the keyword would also be ignored on a VAMS page, because the hash sources are
present. The add-on contributes `frame-src` and `connect-src` origins and no `script-src` source.

If a **new** viewer plugin needs inline script, hash that document's own inline blocks rather than
moving `'unsafe-inline'` into `script-src` -- a hash source makes the keyword inert, so adding it
would relax nothing while removing the protection the hashes give every other page.

Adding a `<script src="...">` (external) needs no hash; it is matched by host-source instead. It may
still need its origin added to `script-src`/`connect-src`.

### Rule 10: A maplibre Map Needs the Worker Setup Module and react-map-gl >= 8.1.2

`maplibre-gl` 6 is ESM-only and ships its web worker as a separate module (`dist/maplibre-gl-worker.mjs`,
which imports `maplibre-gl-shared.mjs`). By default it resolves that worker as a sibling of the main
module's `import.meta.url`, a file the Vite bundle never emits, so the worker request fails and no map
renders. `src/common/utils/maplibreWorker.ts` registers a bundled copy through `setWorkerUrl()` using
Vite's `?worker&url` import (plain `?url` copies the worker without its shared chunk and breaks on its
first import). **Every module that imports `react-map-gl/maplibre` or a runtime value from `maplibre-gl`
imports that setup module for its side effect** before the map mounts.

maplibre-gl 6 also removed the public `map.transform` property; `react-map-gl` versions before 8.1.2 read
`transform.center` on every camera update and crash each map into the page error boundary. Keep
`react-map-gl` at 8.1.2 or later while `maplibre-gl` is on 6.x.

Both rules are held in place by `src/common/utils/maplibreWorker.test.ts`, which scans the source tree
for map consumers and checks the installed versions.

Under Jest neither import resolves: `maplibre-gl` has no `require` export condition and `?worker&url` is
Vite-only, so `jest.config.js` maps both to `src/__mocks__/emptyModule.js`, as it already does for the
Monaco worker imports. A suite that reaches a map component through a page import therefore loads a
no-op setup module rather than failing to resolve.

---

## 4. API Integration Patterns

### 4.1 The Return Tuple Pattern

Most `APIService.ts` functions return `[boolean, data/errorMessage]` tuples:

```typescript
// CORRECT - follow the established return pattern
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

Key patterns to follow:

1. Use `apiClient` for all API calls (auth headers injected automatically)
2. Failure is decided by `response.success === false` or a thrown request (non-2xx), never by searching the message for "error": a success message can echo caller-chosen text such as an asset ID
3. Return `[false, errorMessage]` on error and `[true, data]` on success
4. Always log errors with `console.log` (NOT `console.error` -- match existing convention)
5. A `403` the deployment's AWS WAF web ACL blocked is not a permission denial. `isRequestBlocked(error)` (in `apiClient.ts`, re-exported from `APIService.ts`, since components never import `apiClient`) recognizes its body (`blockedBy: "waf"`), and `parseErrorResponse` appends ` (request ID <id>)` to its message, so code that maps a `403` to a permission sentence checks `isRequestBlocked` first and shows the message (`fetchAssetWithStatus`, `fetchFileInfo`, `downloadAsset`, `deleteAssetLink`, `deleteComment`)
6. A request whose response the browser never saw (a network failure, or a cross-origin preflight a web ACL blocked) is not an API answer: `fetch` rejects with a `TypeError` whose text ("Failed to fetch") means nothing to a user. `isNetworkFailure(error)` recognizes it, and the service returns or throws `NETWORK_FAILURE_MESSAGE` in its place, with no status, so the page can say the service could not be reached and offer Retry rather than a "not found" or permission message (`fetchAssetWithStatus`, `fetchFileInfo`, `fetchFileVersions`, `fetchMetadata`, `downloadAsset`; the asset and file pages, and the viewers and previews through `downloadFailureMessage` and `downloadRefusalReason`). Both live in `services/networkFailure.ts`, re-exported by `apiClient.ts`, so a service whose tests mock `apiClient` still gets the real check; a `catch` that returns the thrown message uses `requestFailureMessage(error, fallback)` from the same file. The orchestration and EDH `toTuple`, the EDH mutations, the zip download service, `ComplianceService` and the asset page's comments, relationships and versions reads all word a no-response failure this way. A create or launch that got no reply may have reached the API, so it says the outcome is unknown and where to look before retrying (`LAUNCH_OUTCOME_UNKNOWN_MESSAGE` for an orchestration run and an EDH workstation, `ZIP_CREATE_OUTCOME_UNKNOWN_MESSAGE` for a zip download)
7. A TanStack Query hook never retries a final answer. The app's query client (`createAppQueryClient` in `utils/queryRetry.ts`) retries a failed query once, except when the error carries a 4xx status other than 408 or 429: a 403 is the API's authorization decision and a 404 says the object is not there, so the page shows the error at once. `toTuple` / `toTupleWithWarnings` tag a failed tuple with the call's status (`failureStatus(result)`, non-enumerable, so the tuple still destructures and compares as `[false, message]`), and the hooks in `features/orchestration/api/queries.ts` throw through `callService`, which raises a `ServiceCallError` carrying it. A hook that throws on a tuple itself goes through `callService` too, or its 403 is re-sent (#1270). A service that rebuilds its tuple drops the status and keeps the old retry-once behaviour. The core services that are not TanStack hooks return the status as a visible third entry instead, `[false, message, status?]` (the zip download calls, `downloadAsset`, `fetchFileInfo`, `ComplianceService.getDatabaseBindings`), so a caller reads `result[2]`.

### 4.2 Consuming API Results

```typescript
// CORRECT -- always check the boolean flag
const result = await fetchAssets({ databaseId });
if (result === false || result[0] === false) {
    // Handle error
    setError(result ? result[1] : "Unknown error");
    return;
}
const data = result[1]; // or result for non-tuple responses

// INCORRECT -- never assume success
const data = await fetchAssets({ databaseId });
setAssets(data); // Will crash if API returns error tuple
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
POST   /assets                                      # Create asset
PUT    /database/{databaseId}/assets/{assetId}      # Update asset
DELETE /database/{databaseId}/assets/{assetId}/archiveAsset  # Archive asset
DELETE /database/{databaseId}/assets/{assetId}/deleteAsset   # Permanently delete asset
POST   /database/{databaseId}/assets/{assetId}/download  # Download
POST   /auth/routes                                 # Check allowed routes
GET    /secure-config                               # Get runtime config
```

**An id in a URL keeps every character it has (#1476).** An asset id may end in `#` or `%`. `apiClient.buildUrl` percent-encodes any `#` or `?` in a path before `new URL()`, which would read either as the start of a fragment or a query, and any `%` that does not start an escape (a `%` followed by two hex digits is kept, because callers pass segments they encoded themselves), so service paths interpolate ids as they are and pass query parameters in `queryStringParameters`, never inline. A URL built outside `apiClient` (a viewer's `${config.api}database/.../download/stream/...`) and every in-app link to an asset route wrap the asset id in `encodeURIComponent`; the router decodes route parameters, so the page receives the id as it is. `src/__tests__/assetIdInUrls.test.ts` fails on a raw asset id in either.

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
                    +---------+----------+
                    |                    |
              Cognito Flow      External OAuth2 Flow
              (Amplify v6 Auth:     (Custom TokenProvider +
               fetchAuthSession,     @badgateway/oauth2-client)
               getCurrentUser,
               signInWithRedirect,
               signOut)
                    |                    |
                    +--------+-----------+
                             |
                   getDualValidAccessToken()
                   getDualAuthorizationHeader()
                             |
                    API calls via apiClient
                    (apiClient injects auth header automatically)
```

**Amplify v6 migration notes:**

-   Auth imports come from `aws-amplify/auth` (e.g., `fetchAuthSession`, `getCurrentUser`, `signOut`, `signInWithRedirect`)
-   Custom `TokenProvider` is used for external OAuth token integration
-   `appCache` replaces Amplify Cache for runtime config storage
-   Hub event names changed: `signIn` -> `signedIn`, `signOut` -> `signedOut`
-   Every sign-out path awaits `revokeSession()` (`utils/sessionManager.ts`; Amplify `signOut()`, bounded at 5 s) BEFORE `clearStoredSession()` and `clearLocalStore()`. Amplify reads the stored tokens to revoke the refresh token, so clearing localStorage first skips the revocation.
-   External OAuth sign-in (`FedAuth/oauthSignInState.ts`) sends an opaque random `state`, never the page URL: an IdP that returns `state` without percent-encoding would cut a URL at `#`. The page to return to is stored under `auth_return_to`, followed only when it is on `window.location.origin` (otherwise the app root), and `auth_state`, `auth_return_to` and `code_verifier` are removed once the callback succeeds or fails, or when the authorize step fails. A page load with those values stored but no `code`, `error` or `state` in the URL (`isOAuthCallback`) is a sign-in left at the IdP: the values are removed and the sign-in page shows, with no error. The callback is refused unless its `state` equals the stored one.

### 5.4 User State

```typescript
// User is stored in localStorage as JSON
const user = JSON.parse(localStorage.getItem("user"));
```

There is no separate `email` key. Nothing writes one and nothing reads one — the signed-in user's
identity comes from the `user` entry above. Storing it a second time under its own key put a user
identifier in `localStorage` for no consumer, and wrote the literal string `"undefined"` when there
was no signed-in user.

In external OAuth mode `user.username` is the user id the API authorizer resolves from the access
token (`resolveExternalUserId` in `src/utils/authTokenUtils.ts`). With amplify-config
`externalOAuthIdpUserIdClaim` set, a token without that claim opens no session: storing it throws
`MissingUserIdClaimError` (sign-in, refresh, page load, amplify-config refresh), the app signs out,
and the sign-in page names the claim from `MISSING_USER_ID_CLAIM_KEY`.

amplify-config also carries `externalOAuthIdpAudienceClaims` (the claim names the authorizer reads
the audience from). The web app does not read it: the API alone decides whether a token's audience
is accepted, and `vams-mcp setup-remote` is its consumer.

---

## 6. State Management

### 6.1 No Global Store

This codebase uses NO global state library. State is managed through:

1. **React Context + useReducer** for cross-component shared state
2. **Component-local useState/useReducer** for component state
3. **localStorage** for persistence across sessions
4. **appCache** (`src/services/appCache.ts`) for runtime config and preferences
5. **URL params** via React Router v7 `useParams()`
6. **TanStack Query** for server state in the Tailwind modules (`features/orchestration/` and the add-on modules under `addons/`) — caching, background refetch, smart polling (`computeRefetchInterval` keeps polling active only while non-terminal executions are visible); each module has its own `qk` key factory

### 6.2 Context Pattern

```typescript
// CORRECT -- follow the existing context pattern
// In context file:
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

| Context                | File                              | Purpose                                                                                                                                                                                                                                                                                                                                                      |
| ---------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AssetContext`         | `context/AssetContext.ts`         | Asset list state                                                                                                                                                                                                                                                                                                                                             |
| `AssetDetailContext`   | `context/AssetDetailContext.ts`   | Single asset detail with reducer                                                                                                                                                                                                                                                                                                                             |
| `RuntimeConfigContext` | `context/RuntimeConfigContext.ts` | The live runtime config, provided by `FedAuth/Auth.tsx` around the logged-in app; `useFeaturesEnabled()` re-evaluates when the secure config lands, so a feature-gated surface (a feature-restricted navigation entry, an add-on page) appears on a first login without a reload. Outside the provider both hooks fall back to `appCache.getItem("config")`. |

The orchestration module (`features/orchestration/**`) holds its shared server state in TanStack Query (`features/orchestration/api/queries.ts`) rather than a React context, so a new pipeline/workflow/execution surface adds a query key there instead of a provider here.

### 6.4 Busy Controls and Focus (Orchestration Module)

**Busy controls and focus.** A control whose own request is in flight is a `BusyButton` or `BusyInput` (`components/BusyControls.tsx`: `aria-disabled` and the busy look, not `disabled`, which drops keyboard focus to the page body), a dialog whose footer sent the request passes `busy`, a control the answer removes hands focus on (`LoadMoreButton`, `useFocusHandOff`), and a dialog returns focus to the control that opened it as it closes (`Dialog` and `Drawer` do this through `useReturnFocus`; a dialog opened from a row's menu passes `returnFocusTo`, naming the row's actions button or, once the action removed the row, a control the list keeps).

---

## 7. Component Patterns

### 7.1 Component Organization

-   **Pages** (`src/pages/`) -- thin wrappers that compose components, lazy-loaded in routes
-   **Components** (`src/components/`) -- organized by domain/feature
-   **Features** (`src/features/orchestration/`) -- the pipelines/workflows/executions module, which owns its own pages, components, and API layer; its `src/pages/` entries are route shells only
-   **Layout** (`src/layout/`) -- Navigation shell components
-   **Common** (`src/common/`) -- shared utilities, helpers, labels, and feature-switch constants

### 7.2 Component Template (New Feature Component)

```tsx
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
            // Use APIService functions
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
                columnDefinitions={
                    [
                        // column definitions
                    ]
                }
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

### 7.4 Adding a New Route

1. Create the page component in `src/pages/`
2. Add the lazy import in `src/routes.tsx`
3. Add the route entry to `routeTable` array

```typescript
// In routes.tsx:
const MyPage = lazyWithRetry(() => import("./pages/MyPage"));

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

### 7.5 Adding Pages in a Fork

An add-on is optional, upstream-maintained code enabled by configuration: it either integrates VAMS with an external solution deployed and run separately (Garnet Framework, Physna), supplying the VAMS-side components, or is a stand-alone non-core component that VAMS deploys in full when enabled (the licensed VNTANA and VEERUM viewers). Custom is fork code, which upstream ships empty. Upstream functionality goes in the add-on lane when it integrates an external solution, depends on an AWS service outside the tier-1 set, or implements a use-case lens for an industry; viewers and pipelines (with the pipeline system that runs them) keep their own lanes, and a fork uses the custom lane. The tier-1 AWS services are Amazon S3, DynamoDB, API Gateway, Lambda, IAM, Cognito, CloudFront, Application Load Balancer, Route 53, Amazon VPC and its endpoints, CloudFormation, AWS KMS, Step Functions, EventBridge, SQS, SNS, Secrets Manager, Systems Manager Parameter Store, CloudWatch, CloudTrail, AWS WAF and Certificate Manager. Search (Amazon OpenSearch Service) and the maps (Amazon Location Service) are core today and move to the add-on lane later, and the AWS Deadline Cloud pipeline execution type (`app.pipelines.deadlineCloudExecutionTypeEnabled`) stays core as part of the optional pipeline system; no new feature that needs one of those services joins them in core. Every add-on has an on/off toggle in configuration and is off by default. Definition, exemptions and current add-ons: `documentation/docusaurus-site/docs/developer/addons-overview.md`.

A fork adds pages without editing `routes.tsx` or `layout/navigationRegistry.ts`: both splice in fork-owned files under `src/custom/`, which upstream ships empty and never edits. The add-on lane sits between core and custom: `src/addons/addonRoutes.tsx` (`addonRouteTable`) and `src/addons/addonNavigation.ts` (`addonNavMainSections` for the main menu, placed after "Orchestrate & Automate"; `addonNavAdminSections` for the admin area, placed after the core "Admin - ..." sections), upstream-owned, hold an add-on's pages, and `src/addons/addonFeatures.ts` (`addonFeaturesEnabled`) names the add-ons' feature switches. An add-on route carries its add-on's switches in `featuresEnabledRestriction`, as its navigation entries do (see 7.6): without them `routeTableFor()` (`routes.tsx`) leaves it out, so its URL reaches the not-found panel, the web-route check is not sent it, and the Create Constraint editor does not offer it (`src/addons/addonFeatureRoutes.test.tsx`). `AppRoutes` runs the web-route check again when the switches change (they can land after the first check) and on Retry, and uses only the latest check's answer: an earlier check that answers or fails after it is ignored, and a successful latest check clears the could-not-check panel. The merge order is core, add-on, custom for the route table and, within each placement, for the navigation (`src/addons/addonLanes.test.tsx`), with the fork's entries after every core and add-on entry, so a path that repeats a core path never replaces the core page. The page goes in `src/custom/pages/`, its route in `customRouteTable` (`src/custom/customRoutes.tsx`, same `RouteOption` shape and `lazyWithRetry` rule), its navigation entry in `customNavSections` (`src/custom/customNavigation.ts`; side navigation by default, or `placement: "profile"` for the top-bar profile menu, see 7.6), its API calls in `src/custom/services/`, and its feature-switch names in `src/custom/customFeatures.ts`. Custom routes and nav links pass through the same `checkWebRoutesAllowed` filter as core ones. Paths under `/custom/` are recommended, not enforced. Full walkthrough: `documentation/docusaurus-site/docs/developer/customizing-vams.md`.

### 7.6 Navigation Placement

Every page link is declared in one registry, `src/layout/navigationRegistry.ts`, which `allNavEntries()` composes from the core entries (`coreNavEntries()`, a function because `window.DISABLE_COGNITO` is read at runtime), the two add-on lists and `customNavSections`. A top-level entry's optional `placement` decides where it renders: `side` (the default) in the side navigation (`layout/Navigation.tsx`), `profile` in the top-bar profile menu (`layout/useProfileMenuItems.ts`, rendered by `App.tsx` before Sign out, which is always last). The side navigation runs core main menu, `addonNavMainSections`, divider, core admin sections, `addonNavAdminSections`, then the fork's entries; the profile menu runs the core profile entries, then the add-on entries of both lists, then the fork's. A profile entry is a `link` or a `section` of links (a menu group headed by its `text`); other shapes are skipped with a console warning. Any entry may carry `featuresEnabledRestriction: string[]`, the deployment's feature switches (`useFeaturesEnabled()`) that must all be on: an entry whose switches are off is dropped and its route is not checked, and both placements check again when the switches arrive after mount (only the latest check's answer is used). `entriesFor(placement, enabledFeatures)` returns one placement in registry order and strips the registry-only fields (`placement`, `requiresApiRoute`, `featuresEnabledRestriction`) before Cloudscape sees them.

Both placements use the same `checkWebRoutesAllowed` check and share its one batched `auth/routes` call. They fail closed: the profile menu lists no page links while the check is loading or after it failed (it retries on the next `hashchange`, as the top bar sits outside the HashRouter and has no Retry), and Sign out always works. The page the user is on is shown as current: Cloudscape's public menu items have no current-page state or `aria-current`, so `toMenuItems(entries, currentHash)` keeps every page a link and gives the current one a `labelTag` of "Current page" and an accessible name ending ", current page" (not a checkbox item, which would be announced as a checked toggle). `isCurrentPage(href, hash)` matches the route path (`normaliseHref`, so a trailing slash does not matter) and every query parameter the entry's href sets, so `#/auth/api-keys/?mode=user` is current on the user view of the API keys page and not on its admin view; `pages/auth/ApiKeys.tsx` keeps `?mode=` on the view it resolved. The hook re-renders on every `hashchange`, so the mark follows navigation. It applies to every `placement: "profile"` entry (core, add-on and custom lanes). When the side navigation has no page beyond Home but profile pages are allowed, it says the pages are in the profile menu instead of "No Access". A link may also set `requiresApiRoute: { method, path }`: it is then shown only when the cached API allowed-routes listing (`isApiRouteAllowed`) allows that route, and hidden while the listing is unknown or failed. The auth flow fetches that listing on every load and renews it at half its TTL, and `cacheAllowedApiRoutes` (`FedAuth/allowedApiRoutesCache.ts`) dispatches `ALLOWED_API_ROUTES_CACHED_EVENT`, on which both placements render again, so such a link appears as soon as the listing lands. It is advisory; the API still enforces every call. Core uses it for Admin - Auth's "API Keys (all users)" (`#/auth/api-keys/?mode=admin`, needs `GET /auth/api-keys`), while the profile menu's "API Key Management" opens `?mode=user`; `pages/auth/ApiKeys.tsx` treats `?mode=` as a preference validated against the modes the listing allowed, so a URL never opens admin mode for a role the listing does not allow. `normaliseHref` drops a `?query` before the route check. A route's `active` href is static, so a page serving several views may override the highlighted side link with `useActiveNavHref(href)` (`layout/activeNavHref.tsx`; `null` keeps the route's value, `""` highlights nothing, cleared on unmount); `ApiKeys.tsx` passes the Admin - Auth href only when its resolved mode is admin.

### 7.7 List Toolbar: Search, Refresh and Filters

Every list page lays out the toolbar it passes as the Cloudscape Table's `filter` the same way: **the search field on the left with the refresh button right next to it, and the filter drop-downs aligned to the right of the table.** When the filters do not fit beside the search (on a narrow screen, or a page with many filters such as the compliance Audit Log), they wrap onto a line of their own and stay aligned to the right. The refresh is an icon button whose `ariaLabel` names what it reloads; it sits in the toolbar, not in the header actions, and a list without a search keeps it at the toolbar's left.

`components/list/TableList.tsx` lays out its own filter slot this way. A page with its own `Table` uses `components/list/ListToolbar.tsx`, which has the same layout and keeps a wrapped filter line at the right:

```tsx
<Table
    filter={
        <ListToolbar
            search={<TextFilterInput value={filters.text} onCommit={...} ariaLabel="Search ..." />}
            onRefresh={loadData}
            refreshing={loading}
            refreshLabel={`Refresh quarantined ${Synonyms.assets}`}
            filters={<DatabaseFilter value={filters.databaseId} onChange={...} />}
        />
    }
    items={sortedItems}
    {...sortingProps}
/>
```

**Column sorting.** A column that offers sorting must sort. Wire `sortingColumn`, `sortingDescending` and `onSortingChange` with `components/list/tableSorting.ts`, or remove the column's `sortingField`. Keep the `sortingField` (the table matches the sorted column by it) and give the column a `sortingComparator` from the same module, so a date sorts by its value (`byDate`), a status by the label it shows (`byText` over the label) and text with case ignored and its numbers by value (`byText`).

-   **A list the server pages** (token paging) sorts the page shown with `useSortedItems`. While more than one page exists, the table header's description says `PAGE_SORTING_NOTE` ("Column sorting applies to the page shown."). An order across the whole list is a server-side filter (the database compliance overview's **Sort by**), not a column header. Choosing such an order clears the column-header sort (`clearSorting` from `useSortedItems`), so the rows follow the order just chosen.
-   **A list the API returns whole** is filtered, sorted and paged in the browser with `useBrowserPagedItems` (15 rows to a page, as `TableList` pages), so its filters and sorting cover the whole list. Call `toFirstPage()` when a filter changes.

### 7.8 The Upload Control and the Upload Path Rule

The Create Asset wizard (`pages/AssetUpload/AssetUpload.tsx`, `AssetFileInfo`) and the Add Files page (`pages/AssetUpload/ModifyAssetsUploads.tsx`) share one upload control, `components/form/DragDropFileUpload.tsx`: **Add files** (a plain multi-file input), **Add folder** (a `webkitdirectory` input in every browser; `showDirectoryPicker` is only an enhancement and falls back to the input on any error other than a cancel) and one drop zone for files and folders together. Its contract is `onAdd(roots: UploadRoot[])`: it reports what the user added, never asset paths.

Every asset path comes from `components/form/uploadPaths.ts`. A page stores the roots (Create Asset in the wizard state, `UploadRoots` and `folderNameMode`, so they survive step changes) and derives the files with `planUploadPaths(roots, { flow, destination, folderNameMode })` over the whole selection each time, then maps them onto `FileUploadTableItem`s with `plannedToUploadItems` (`pages/AssetUpload/uploadSelection.ts`) right before the upload workflow. The rule (#1462, #1471): on Create Asset a single folder added on its own drops its name; on Add Files a single folder keeps it; either flow's single folder can be switched with the override; every other selection keeps every folder's name; loose files keep their file name; the destination (`normalizeDestination`, `/` or `/a/b/`) is joined with one slash. Duplicate final paths are errors on every affected row and block the upload; never skip or dedupe them. Pass the database's `restrictFileUploadsToExtensions` in the context: files it refuses, and preview files without a preview image extension, become row errors by the same rule as `validateFiles` (`extensionViolation`), so they count in the preview's errors, its Errors view and the blocking reason; do not add a separate invalid-files alert. Do not build, prefix or re-derive upload paths in a page, and do not add a second picker: extend the root builders in `uploadPaths.ts` so every way of adding items gives identical roots.

`components/form/UploadPreview.tsx` shows the preview (final path, size, status, counters, filters, removal), the single-folder override, the folder-rule info line and the hint for loose files in a referencing format (`referencingFileExtensions` / `referencingFileNames` in `common/constants/fileFormats.ts`). Add Files keeps its destination in `?folder=` and marks files at existing or archived paths from the list-files API (`fetchExistingPaths`, prefix-scoped, capped at 10,000 keys). That mark is advisory: a refusal or failure shows "Could not check all existing files" and marks nothing, and the API still enforces the upload.

---

## 8. Viewer Plugin System

### 8.1 Architecture

The 3D/media viewer system uses a plugin-based architecture:

-   **PluginRegistry** (`src/visualizerPlugin/core/PluginRegistry.ts`) -- Singleton that manages all viewer plugins
-   **viewerConfig.json** (`src/visualizerPlugin/config/viewerConfig.json`) -- JSON configuration for the core plugins; add-on viewers (Physna, the other add-ons' viewers and the licensed VNTANA and VEERUM viewers) are configured in `addonViewerConfig.json` and a fork's in `customViewerConfig.json` (fork extension point, shipped `{ "viewers": [] }`)
-   **manifest.ts** (`src/visualizerPlugin/viewers/manifest.ts`) -- Vite static analysis paths for dynamic imports; `viewers/addonManifest.ts` and `viewers/custom/customManifest.ts` hold the add-on and fork entries
-   **viewerSources.ts** (`src/visualizerPlugin/core/viewerSources.ts`) -- merges core, add-on and custom configs and manifests; a custom viewer whose `id` repeats a shipped one is skipped with a console error, and a shipped manifest key always wins
-   **StylesheetManager** -- Per-plugin CSS lifecycle management
-   **extensionMatching.ts** (`src/visualizerPlugin/core/extensionMatching.ts`) -- the one filename rule (`filenameExtension`, `extensionToken`), the `NO_EXTENSION` token `(none)`, `describeExtensionToken` and `supportsAllExtensions` (see 8.4.1)
-   **textContent.ts** (`src/visualizerPlugin/core/textContent.ts`) -- `classifyTextSample` and `loadTextFile`, the bounded loader both text viewers use (see 8.4.1)
-   **streamFailure.ts** (`src/visualizerPlugin/core/streamFailure.ts`) -- `probeStreamFailure(url, headers)` re-requests a streamed file whose loader failed without its HTTP status, and `describeStreamFailure(subject, failure)` names the cause (an AWS WAF block with its request ID, `401`, `403`, `404`, `429`, or no response at all). The Potree viewer reports its load failures through both, the Cesium viewer through `CesiumViewerPlugin/tilesetFailure.ts`, which reads the status off Cesium's `RequestErrorEvent` `downloadFailureMessage(response, subject)` turns a failed `downloadAsset` tuple (`[false, message, status?, apiMessage?]`, the fourth entry the API's own `message` from the response body) into the sentence a viewer shows, from what the API answered only: 401 `DOWNLOAD_UNAUTHENTICATED_MESSAGE` ("Your session has expired. Sign in again to view this file."), 403 `DOWNLOAD_FORBIDDEN_MESSAGE` ("You do not have permission to view or download this file."), 410 archived, 404 not found, another 4xx with a message the API's own message (`apiRefusalMessage`, for example the `400` "Asset is quarantined and cannot be downloaded" of the quarantine download block, D166), a tuple with no status because no API answer came back (`[false, message, undefined]`, which `downloadAsset` marks with `markUnanswered` from `services/networkFailure.ts` for these two only: an AWS WAF block with its request ID, or `NETWORK_FAILURE_MESSAGE`) its own message, anything else `subject`; `downloadStatusMessage(status, subject, apiMessage?)` does the same for a viewer that fetched the streaming route itself, and `streamResponseFailureMessage(response, subject)` reads the API's message from that response's body (Needle USD, ThatOpen IFC). `streamResponseFailure(response, subject)` returns the Error such a viewer throws: an `ApiRefusalError` (`isApiRefusalError`) carrying the API's reason when the API refused the file with one, or when a `403` says more than a plain denial (an AWS WAF block's message with its request ID, the stream route's "Asset not distributable"), as `describeStreamFailure` reads it, else an `Error` with the text above; a viewer that collects per-file errors keeps that flag, so a load where no file loaded shows the refusal's reason rather than a generic "No files loaded" sentence (Needle USD, D172). A viewer whose error view adds a format or file hint under its message ("Please ensure the file is a supported 3D format", the Needle USD and ThatOpen IFC format lines, Cesium's "Supported format: .json") leaves it out when the error is such a refusal (D170): Three.js, Needle USD and ThatOpen IFC keep an `errorIsRefusal` flag from `isApiRefusalError`, and Cesium from `isTilesetRefusal` (`CesiumViewerPlugin/tilesetFailure.ts`; in a multi-tileset load only when every failed tileset was refused). The 401/403/404/410 sentences keep the hint as before (#1211). `describeStreamFailure` names such a refusal too. A preview, thumbnail or download button with fixed failure text shows `downloadRefusalReason(response)` (the API's refusal message, or the message of a download with no API answer, else null) in its place (the asset and file preview thumbnails and modals, the asset details pane, the search result thumbnails, the file version download buttons). Nothing in the browser reads a compliance state to decide a refusal: the API decides and the web shows its answer. Every core viewer that downloads a file uses them (Online 3D, PDF, Text, Image, TIFF/TGA, Columnar, Audio, Video, HTML, SuperSplat, both Gaussian splat viewers, ThatOpen IFC, Needle USD, Three.js and the Robotics Data Viewer), and the Text Diff Viewer's per-side messages use the same constants (#1211). The licensed VNTANA viewer uses `downloadFailureMessage` too; Cesium and Potree name stream failures through `describeStreamFailure`, and VEERUM probes a refused point cloud or tileset (`probeStreamFailure`) and names it the same way when the API gave its own reason. Tests of a refusal render the API's real answer from `core/quarantineRefusal.testdata.ts`: its body, `core/quarantineRefusalBody.testdata.json`, is compared with each file route's real response by the backend handler tests (`backend/tests/quarantineRefusalBody.py`, D169), so a test never mocks a message the API does not send.

### 8.2 Current Viewers (plugins)

| ID                                 | Name                               | Category | Extensions                                                                                                                               | Status                                              |
| ---------------------------------- | ---------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `online3d-viewer`                  | Online 3D Viewer                   | 3d       | .amf, .bim, .off, .wrl                                                                                                                   | enabled                                             |
| `online3d-viewer-rhino`            | Online 3D Viewer (Rhinoceros 3D)   | 3d       | .3dm                                                                                                                                     | enabled (requires ALLOWUNSAFEEVAL)                  |
| `potree-viewer`                    | Potree Viewer                      | 3d       | .e57, .las, .laz, .ply                                                                                                                   | enabled                                             |
| `image-viewer`                     | Image Viewer                       | media    | .png, .jpg, .jpeg, .svg, .gif, .webp, .bmp                                                                                               | enabled                                             |
| `tiff-tga-viewer`                  | TIFF/TGA Image Viewer              | media    | .tga, .tif, .tiff                                                                                                                        | enabled                                             |
| `html-viewer`                      | HTML Viewer                        | document | .html                                                                                                                                    | enabled                                             |
| `video-viewer`                     | Video Player                       | media    | .mp4, .webm, .mov, .mkv, .m4v                                                                                                            | enabled                                             |
| `audio-viewer`                     | Audio Player                       | media    | .mp3, .wav, .ogg, .aac, .flac, .m4a                                                                                                      | enabled                                             |
| `columnar-viewer`                  | Columnar Data Viewer               | data     | .fcs, .csv                                                                                                                               | enabled                                             |
| `geotiff-map-viewer`               | GeoTIFF Map Viewer                 | data     | .tif, .tiff                                                                                                                              | enabled (requires LOCATIONSERVICES)                 |
| `pdf-viewer`                       | PDF Viewer                         | document | .pdf                                                                                                                                     | enabled                                             |
| `cesium-viewer`                    | Cesium 3D Tileset Viewer           | 3d       | .json                                                                                                                                    | enabled                                             |
| `text-viewer`                      | Text Viewer                        | document | .txt, .json, .xml, .html, .yaml, .md, .py, .js, .ts, .sql, etc., and files with no extension (`(none)`)                                  | enabled                                             |
| `text-diff-viewer`                 | Text Diff Viewer                   | document | .txt, .json, .xml, .yaml, .md, .py, .js, .ts, .sql, etc., and files with no extension (same list as `text-viewer`)                       | enabled (compare-only: 2 files, cross-asset)        |
| `gaussian-splat-viewer-babylonjs`  | Gaussian Splat Viewer (BabylonJS)  | 3d       | .ply, .spz, .splat, .sog                                                                                                                 | enabled                                             |
| `supersplat-viewer`                | SuperSplat Editor (PlayCanvas)     | 3d       | .ksplat, .lcc, .ply, .sog, .splat, .spz                                                                                                  | enabled (requires ALLOWUNSAFEEVAL, iframe-embedded) |
| `gaussian-splat-viewer-playcanvas` | Gaussian Splat Viewer (PlayCanvas) | 3d       | .ply, .sog                                                                                                                               | enabled                                             |
| `vntana-viewer`                    | VNTANA 3D Viewer                   | 3d       | .glb                                                                                                                                     | **disabled** (licensed add-on)                      |
| `veerum-viewer`                    | VEERUM 3D Viewer                   | 3d       | .e57, .las, .laz, .ply, .json                                                                                                            | **disabled** (licensed add-on)                      |
| `needletools-usd-viewer`           | Needle USD Viewer (Experimental)   | 3d       | .usd, .usda, .usdc, .usdz                                                                                                                | enabled (requires ALLOWUNSAFEEVAL)                  |
| `threejs-viewer`                   | Three.js Viewer                    | 3d       | .gltf, .glb, .obj, .fbx, .stl, .ply, .dae, .3ds, .3mf, .usd, .usda, .usdc, .usdz, .stp, .step, .iges, .igs, .brep                        | enabled                                             |
| `physna-viewer`                    | Physna Viewer                      | 3d       | .3ds, .asm, .catpart, .catproduct, .glb, .iam, .iges, .igs, .ipt, .jt, .obj, .par, .prt, .sldasm, .sldprt, .stl, .step, .stp, .x_b, .x_t | enabled (requires PHYSNA_ADDON)                     |
| `miris-stream-viewer`              | Miris Spatial Streaming            | 3d       | .usd, .usda, .usdc, .usdz                                                                                                                | enabled (requires MIRIS_ADDON, ALLOWUNSAFEEVAL)     |
| `thatopenwebifc-viewer`            | ThatOpen IFC BIM Viewer            | 3d       | .ifc, .ifczip                                                                                                                            | enabled (requires ALLOWUNSAFEEVAL)                  |
| `robotics-data-viewer`             | Robotics Data Viewer (Lichtblick)  | data     | .mcap, .bag, .db3, .ulg, .ulog                                                                                                           | enabled (requires ALLOWUNSAFEEVAL, iframe-embedded) |
| `preview-viewer`                   | Preview Viewer                     | preview  | \* (wildcard)                                                                                                                            | enabled                                             |

> Note: `supersplat-viewer` is a **iframe-embedded** viewer — it self-hosts a from-source SuperSplat build under `public/viewers/supersplat/` and loads files via a presigned URL `?load=` parameter (see 8.6 before copying that pattern). The build is WebGPU-only (no WebGL2 fallback). Under the production CSP its `<base>` element, inline script, and embedded `pc-icon` data-URI font are blocked without breaking the editor. The install removes the editor's WebMCP tool registration (`registerWebMCP` in upstream `src/main.ts`, v3.7.0+) before building and fails if the built bundle still references `modelContext`: the framed editor is same-origin with VAMS, so its tools must not be offered to a browser agent.

> Note: `robotics-data-viewer` is an **iframe-embedded** viewer — `web/customInstalls/robotics/roboticsViewerInstall.js` builds the Lichtblick web app (MPL-2.0, pinned tag and commit) from source with the VAMS overlay in `web/customInstalls/robotics/overlay/` into `public/viewers/robotics/`. The host component presigns each file under its own asset and hands the URLs over in the iframe's URL **fragment** (see 8.6). `.mcap` (several files on one timeline) and `.bag` are read with HTTP Range requests; `.db3` (one file or a ROS 2 split bag) and `.ulg`/`.ulog` are downloaded whole, limited to `customParameters.maxWholeFileBytes` (1 GiB) across the selection. No Lichtblick extension can be installed or loaded: an extension is code run in the framed document, which is same-origin with VAMS, so the overlay entrypoint renders upstream `SharedRoot` with an empty extension loader list instead of upstream `WebRoot` (which always adds IndexedDB loaders). The install's build check and `roboticsBuild.test.ts` fail if that changes. User Scripts stay available (they run in a web worker).

> Note: `geotiff-map-viewer` (GeoTIFF Map Viewer, gated on `LOCATIONSERVICES`) draws rasters with the shared code in `web/src/common/cogRaster/`, which the Search map's **Show raster** popup action also uses (`components/search/SearchRasterLayers.tsx`, lazy-loaded). Keep a single implementation there. `rasterResolver.prepareRaster` makes the decision. It refuses from the `ext_crs` and `ext_georeferenced` attributes (#1600) without opening the file (no georeference, unsupported CRS), prefers a converted COG named by the source's `cog_link_cogFile` attribute (#1601, flat `cog_link_*` keys, the value an asset-relative path with a leading slash), falling back to the source, and then always reads the TIFF header, which decides the path: stale or wrong attributes cannot send a file to the tile path. **Only a COG in EPSG:3857 (or 102113, the only alias the library accepts) takes the tile path** (`@geomatico/maplibre-cog-protocol`, `cog://<presigned URL>`, registered once in `cogProtocol.ts`). That library does not reproject and draws a blank layer, with no error, for any other CRS. A small georeferenced file in a CRS bundled in `projections.ts` (no network lookup) is drawn on the placed-image path (`placedImage.ts`). That path uses geotiff.js ranged reads of the smallest overview of at least 2048 px, capped at 2048 px, then a PNG blob on four proj4-projected corners, labelled "approximate placement (not reprojected)" unless the CRS is Web Mercator. Its limits are in `rasterEligibility.ts`: `PLACED_IMAGE_MAX_FILE_BYTES` (128 MiB, also applied to the chosen image's strip or tile byte counts), `PLACED_IMAGE_MAX_DECODE_PIXELS` (100 Mpx) and `PLACED_IMAGE_MAX_DECODE_BYTES` (256 MiB) on the image actually decoded, checked from the header before any pixel is read (geotiff.js decodes the chosen image at full resolution), and `PLACED_IMAGE_MAX_DIAGONAL_KM` (100 km). The placed path fails closed: without a decode plan, an extent, and either the file size (caller, `Content-Range` of the header read, or `fileInfo`) or the byte counts, it refuses. `RasterLayerManager.ts` owns layer order (below the Search map's `geojson-*` layers), opacity, the 8-overlay cap and cleanup (it revokes blob URLs). It refreshes a tile source's presigned URL at 80% of its lifetime (at most 45 minutes, `presignedUrl.ts`) and after a read error on that source, because maplibre reports a refused tile read only as "Request failed"; a read error within 30 s of that fresh URL gets one automatic retry after a short wait (a passing S3 outage clears), and a second such error, or a fourth error refresh, marks the row failed (`entry.failed`, the layer leaves the map) instead of leaving a blank layer; a failed tile row has a **Retry** button (`retry()` on the manager and hook) that rebuilds the layer from a fresh URL, unless the API refused the file: a `RasterDownloadRefused` (403, quarantine 400, 410, 404) gives up at once in `keepFresh` and `retry()`, the row shows the API's own reason and `entry.retryable` is false (no Retry); only transient failures (network, 5xx, tile reads) are retried with backoff. A file is listed once (the manager compares the drawn file by asset, path and effective version, so a source resolved to its COG and the same COG selected by itself are one row, while a pinned version that is not the latest is its own row labelled `name (version X)`; a pin on the latest version and the unpinned file are one row. The file manager names a file by its full stored key and a source's `cog_link_cogFile` by the path inside the asset, so when a listed file has the same file name but another key or version (`possibleDuplicates`), the hook asks the file-info route for each file's `relativePath` and latest version id (`fileIdentity`) and the manager compares those; when the API cannot say, the two stay separate rows). Three failed renewals in a row (backoff 1, 2, 4 min) do the same. The viewer fills the visualizer frame it is placed in (`height: 100%`, an opaque `--vams-bg-primary` background), and sets no height of its own: a fixed height clipped the map and its controls below a shorter frame and let the frame's striped backdrop (`.visualizer-container`) show under it in a taller one or in fullscreen. The map and layer list are wrapping flex items (map basis 480 px and growth 999, list basis 300 px); side by side both fill the frame and the list scrolls inside itself, and below `STACKED_BELOW_PX` the list wraps under the map (the map takes 65% of the frame, at least 360 px, so the top of the list stays in view) and the viewer scrolls. The Search map is drawn flat (`projection="mercator"`, because the basemap style selects the globe, whose fit-to-bounds frames the wrong place for results spread over the world) and frames its hits with `fitBounds` over `locationDataBounds` (`components/search/utils/mapBounds.ts`): the longitude range is the covering arc of each shape part's own interval, so a footprint split at 180 beside Bay Area files frames the Pacific (`east` may exceed 180), and one wide shape (a latitude-cropped global raster) frames the world. In the GeoTIFF Map Viewer's layer list (`RasterLayerPanel` with `visibilityToggle`) each raster has a **Visible** switch (Cloudscape `Toggle`, checked while the raster is shown, with one accessible name for both states, `Show "<label>" on the map`, so the checked state carries on/off for a screen reader, and Space operates it; there is no separate Hidden badge; a polite status line says hidden, shown or could not be shown once the manager has applied the change, never at the press; a hidden row's label uses the `text-status-inactive` colour token, which differs from a shown label in both themes, measured 5.6:1 light and 7.0:1 dark (a shown label is 18.5:1 and 10.2:1), not an opacity) instead of Remove: `RasterLayerManager.setVisible` takes a hidden layer's map layer and source off the map (no tile is fetched, the link renewal and any pending retry stop), keeps the row, opacity and order, keeps a placed image's blob URL until the layer is removed or the map goes, and on Show asks for a fresh link first (a refusal shows the API's reason, no Retry). Map errors that still arrive for a hidden layer's removed source are ignored. Zoom to layer is disabled while a layer is hidden; the 8-layer cap counts hidden layers so Show always succeeds. The Search map's panel keeps Remove. The refusal messages say what is wrong (`rasterEligibility.ts`): (a) a Web Mercator TIFF that is too large to place: not cloud-optimized only when the header says `isCog === false`, a COG in an alias the tile reader refuses (900913, 3785, 102100; reason `web-mercator-alias`, re-save advice where the COG Conversion pipeline would skip it, `PIPELINE_WEB_MERCATOR_CODES`), or layout unknown (`layout-unknown`), (b) a COG or TIFF in another projection too large to place as an approximate image (named with `crsLabel`, for example `EPSG:32610 (UTM zone 10N)`, and `COG` or `TIFF` from the header), or in a CRS the viewer does not bundle, (c) too many pixels to draw as an image, (d) no georeferencing (no conversion offered), (e) antimeridian (split it, no conversion offered). Each conversion hint names the COG Conversion pipeline as its documentation does and says it writes `<file>.cog.tif` beside the file in EPSG:3857. A click on a footprint opens the smallest footprint under it, by its polygons' area on the flat map (shoelace over the outer rings in Web Mercator, so a rotated quad counts its real area), the topmost when equal ( `pickFootprint` in `components/search/utils/mapPick.ts`) and anchors its popup at the click, not at the centre of the shape's box, which for a footprint split at longitude 180 is the other side of the world; a popup opened without a click falls back to the centre of the largest part's box (`footprintFallbackAnchor`). A raster whose WGS84 extent passes longitude 180 is refused with its own message (`crosses-antimeridian`), since neither the viewer nor the COG pipeline can handle it. The Search map's popup button toggles Show and Hide raster from the shown set that `SearchRasterLayers` reports. The **Show raster files** switch on the Search map's results-title row (`SearchPageMapView`, on by default, not stored) is a view filter only: off removes the `canShowRaster` hits from the plotted set (so the count and `fitBounds` follow the visible hits) and passes `hidden` to `SearchRasterLayers`, which hides the drawn rasters with `setVisible` and restores exactly those on again; the API still decides each download. A tile layer frames from the header's WGS84 extent, or from the source's own bounds on `sourcedata`; the viewer frames only once `zoomTo` succeeds. The library's geotiff worker pool (one blob worker per core) is module-level and cannot be released; its caches are bounded (16 files, 1,024 tiles, 1 h). Unit fixtures are in `common/cogRaster/__fixtures__/`, regenerated by `generate_fixtures.py`.

### 8.3 Adding a New Viewer Plugin

**Step 1:** Create the viewer directory:

```
src/visualizerPlugin/viewers/MyViewerPlugin/
  MyViewerComponent.tsx     # The React component
  dependencies.ts           # Optional: dependency loader
  MyViewer.module.css       # Optional: scoped styles
```

**Step 2:** Create the component implementing `ViewerPluginProps`:

```tsx
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef } from "react";
import { ViewerPluginProps } from "../../core/types";

const MyViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    assetVersionId,
    viewerMode,
    customParameters,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        // Initialize viewer for assetKey at assetVersionId when set, else at versionId
        return () => {
            // Cleanup on unmount
        };
    }, [assetId, databaseId, assetKey, versionId, assetVersionId]);

    return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
};

export default MyViewerComponent;
```

`ViewerPluginProps` (`src/visualizerPlugin/core/types.ts`) is the whole contract a viewer receives. A single file arrives as `assetKey` and `versionId`; a multi-file selection arrives as `multiFileKeys`, with each file's owning asset and database in `multiFiles` (build each file's URL from its own entry, falling back to the top-level `assetId` and `databaseId`). A single-file selection also arrives as `fileInfo` (the host's `FileInfo`: size and the generated preview key `previewFile`), absent when the host did not have them; the TIFF/TGA viewer reads its byte cap and fallback preview from it. `assetVersionId`, when set, is the asset version to read files from. `viewerMode` is the host's display mode (`"collapse"` when the file page or the file-viewer modal opens, then `"wide"` or `"fullscreen"`); the host's footer controls request a change through `onViewerModeChange`, and the host decides the resulting mode. `customParameters` is the entry's `customParameters` object from `viewerConfig.json`. When `ViewerPluginProps` changes, update the interface listings in `src/visualizerPlugin/README.md` and `documentation/docusaurus-site/docs/developer/viewer-plugins.md`.

**Step 3:** Add to `manifest.ts`:

```typescript
export const VIEWER_COMPONENTS = {
    // ... existing entries
    "./viewers/MyViewerPlugin/MyViewerComponent": "MyViewerPlugin/MyViewerComponent",
};
```

**Step 4:** Add to `viewerConfig.json`:

```json
{
    "id": "my-viewer",
    "name": "My Viewer",
    "description": "Description of the viewer",
    "componentPath": "./viewers/MyViewerPlugin/MyViewerComponent",
    "supportedExtensions": [".xyz"],
    "supportsMultiFile": false,
    "canFullscreen": true,
    "priority": 1,
    "dependencies": [],
    "loadStrategy": "lazy",
    "category": "3d",
    "enabled": true
}
```

**Step 5:** If the viewer has external dependencies, create a custom install script in `web/customInstalls/myviewer/` and add it to the `postinstall` chain in `package.json`.

**Add-on and fork viewers.** (Add-on as defined in `documentation/docusaurus-site/docs/developer/addons-overview.md`.) An add-on's viewer, and a licensed viewer that ships disabled (VNTANA, VEERUM), is registered in `config/addonViewerConfig.json` + `viewers/addonManifest.ts`, never in the core files; its plugin directory stays under `viewers/`. To enable a licensed viewer, set `"enabled": true` on its entry in `config/addonViewerConfig.json`; its install script (`web/customInstalls/vntana/`, `web/customInstalls/veerum/`, unchanged in `postinstall`) checks the core, add-on and custom config files through `web/customInstalls/utility/checkViewerEnabled.js`. A fork's viewer lives in `viewers/custom/<Name>Plugin/` and is registered in `config/customViewerConfig.json` + `viewers/custom/customManifest.ts` (fork extension point, shipped empty); its install steps go in `web/customInstalls/custom/customInstall.js`, which `postinstall` already runs. `core/viewerSources.ts` merges core, add-on and custom in that order, so a priority tie between a core and an add-on viewer resolves to the core one; a custom entry whose `id` repeats a shipped `id` is skipped with a console error and a shipped manifest key always wins. Ids prefixed `custom-` are recommended, not enforced.

### 8.3.1 Stacking Over the Scene

`core/viewerLayers.ts` holds the one stacking scale for what a viewer draws over its scene, `VIEWER_LAYER`, lowest first: `info` (1000, the read-only box with the viewer name, controls hint and scene stats), `panel` (1010, the side panel with its tabs, buttons, scene tree and resize handle, and the Panel button that shows it again), `notice` (1020, dismissible notices across the top, such as files that failed to load) and `cover` (1030, the shared `LoadingSpinner` and a message that replaces the scene). A viewer with a side panel takes each such element's `zIndex` from it and writes no number of its own; at an equal z-index the element later in the document is drawn above. The scale mirrors `features/orchestration/components/zLayers.ts`.

-   **Local to the viewer.** `DynamicViewer` mounts every viewer inside `.visualizer-container-canvases`, whose `transform` (`web/src/styles/index.scss`) makes it a stacking context and the containing block of a panel's `position: fixed`. The values order one viewer's elements only: they cannot rise above the application header (1002), toasts (4000) or Cloudscape modals, and the host's wide and fullscreen controls (`.visualizer-footer`, painted after that container) stay above the whole viewer. Do not raise a viewer value to win against something outside the viewer.
-   **Inside a panel.** The panel is a stacking context of its own, so a menu opened in it (the material dropdown in `ObjectMaterialAssignment.tsx` / `NeedleUSDObjectMaterialAssignment.tsx`) is ordered within the panel and keeps its own value.
-   **The info box beside the open panel.** On a narrow container the open panel covers the info box, whose text would show through the panel's translucent background. `useInfoBoxClearOfPanel()` (`core/useOverlap.ts`) measures the two elements' rectangles (after every render of the viewer, when either element or its container changes size, and when the window does) and gives the info box `visibility: hidden` while they overlap: it keeps its place in the layout, so it is still measured, and takes no clicks. It shows when there is room beside the panel (a wide window, wide or fullscreen mode) and while the panel is hidden. A viewer passes `panelRef` to its panel's root element, and `infoBoxRef` and `infoBoxVisibility` to its info box. `useOverlap(a, b)` is the measurement on its own.
-   **Who uses it.** The Three.js, Needle USD, BabylonJS and PlayCanvas Gaussian splat, ThatOpen IFC and VEERUM viewers, and `LoadingSpinner`. `core/viewerLayers.test.ts` fails when one of those files writes a z-index number; each of the six viewers' `*ViewerComponent.stacking.test.tsx` renders it with its real panel and checks the rendered order and the info box beside the open panel. A new viewer with a side panel uses the scale and `useInfoBoxClearOfPanel()`, gets a stacking test, and joins that list. The Cesium viewer still writes its own values: its controls hint and measurement instructions sit over the bottom of its panel at every width, and its picked-feature box, which has a Close button, overlaps the panel on a narrow window, so it needs a layout decision rather than a z-index. The Potree, VNTANA and Online 3D viewers have no side panel and info box pair.
-   **Prove it with a click.** A z-index alone does not prove an element is clickable (`web/e2e/CLAUDE.md` Rule 4): hit-test the target with `document.elementFromPoint` at a narrow width (414 px) as well as a wide one.
-   **The error panel of a viewer that appends its canvas outside React.** The Three.js (#832) and Needle USD (D176) viewers append the renderer's canvas to their scene container themselves. Their error root carries a `key` apart from the viewer root's (`threejs-viewer-error` / `threejs-viewer`, `usd-viewer-error` / `usd-viewer`), so React unmounts the scene container, and the canvas with it, instead of reusing that node for the message, where the canvas pushed the text out of the panel. The message is centred with flex and scrolls (`maxHeight: 100%`, `overflowY: auto`), so a narrow panel never clips it. A new viewer that appends a canvas follows the same pattern and pins it with a test that the canvas is outside the panel and detached (`ThreeJSViewerComponent.test.tsx`, `NeedleUSDViewerComponent.errorPanel.test.tsx`).

Source: `web/src/visualizerPlugin/CLAUDE.md`, "Stacking Over the Scene".

### 8.3.2 CSP / `unsafe-eval`

Some viewers require the `ALLOWUNSAFEEVAL` feature flag because their loaders (WASM or JIT) use `eval`. Five carry the gate in `viewerConfig.json` (`featuresEnabledRestriction: ["ALLOWUNSAFEEVAL"]`), so the registry does not offer them at all when the deployment has not enabled `allowUnsafeEvalFeatures`: Needle USD Viewer, SuperSplat Editor (also iframe-embedded), ThatOpen IFC BIM Viewer (web-ifc), Robotics Data Viewer (Lichtblick, also iframe-embedded) and Online 3D Viewer (Rhinoceros 3D, rhino3dm).

-   **The Three.js viewer is deliberately not gated.** Its mesh formats (.glb, .obj, .stl, …) and USD (.usd/.usda/.usdc/.usdz, parsed by three.js's pure-JavaScript `USDLoader`) need no `eval`, and gating the whole plugin would remove them too. So a deployment without `allowUnsafeEvalFeatures` still opens USD; with it, Needle and Three.js both match USD at priority 1 and the user picks. Only its OCCT CAD path (.stp/.step/.iges/.igs/.brep) has the heavier requirements, and `loadFile()` in `ThreeJSViewerPlugin/utils/fileLoaders.ts` reports them as a message on the file rather than hiding the viewer: it checks for `SharedArrayBuffer` (the COI headers) and for the OCCT bundle before loading.
-   **`cesium-viewer` is not gated either.** The viewer builds a `CesiumWidget` from the widget-less `@cesium/engine` (`CesiumViewerComponent.tsx`), which drops the `@cesium/widgets` Knockout layer whose `new Function` binding compiler was what required `unsafe-eval`; `'wasm-unsafe-eval'` in the base CSP covers what remains. See `web/customInstalls/cesium/README.md` for the build. KTX2/Basis textures and `.spz` splats are the known content types that still need the broader directive.
-   **The Online 3D Viewer is registered twice for this reason (#921).** `online3d-viewer` offers .amf/.bim/.off/.wrl ungated, and `online3d-viewer-rhino` offers .3dm only under the gate, both rendering `Online3dViewerComponent` from the one bundle `web/customInstalls/online3dviewer/` builds when either is enabled. If eval is refused anyway, `Online3dViewerPlugin/utils/evalBlocked.ts` replaces the viewer's "Failed to load rhino3dm" with a message naming `app.webUi.allowUnsafeEvalFeatures`.
-   **A new viewer that needs `eval`.** Add the feature-flag gate and update the deployment configuration reference in `documentation/docusaurus-site/docs/deployment/configuration-reference.md`.

Source: `web/src/visualizerPlugin/CLAUDE.md`, "CSP / `unsafe-eval`".

### 8.4 Plugin Config Fields

| Field                        | Type              | Description                                                                   |
| ---------------------------- | ----------------- | ----------------------------------------------------------------------------- |
| `id`                         | string            | Unique plugin identifier                                                      |
| `componentPath`              | string            | Path for manifest lookup                                                      |
| `dependencyManager`          | string?           | Path to dependency loader module                                              |
| `dependencyManagerClass`     | string?           | Class name in dependency module                                               |
| `dependencyManagerMethod`    | string?           | Load method name                                                              |
| `dependencyCleanupMethod`    | string?           | Cleanup method name                                                           |
| `supportedExtensions`        | string[]          | Lowercase `.ext` values, `*` (any file) or `(none)` (files with no extension) |
| `supportsMultiFile`          | boolean           | Can handle multiple files at once                                             |
| `canFullscreen`              | boolean           | Supports fullscreen mode                                                      |
| `priority`                   | number            | Lower = listed first when multiple viewers match                              |
| `autoSelect`                 | boolean?          | Auto-open as the sole top-priority match (visualize)                          |
| `loadStrategy`               | "lazy" \| "eager" | Not read; every viewer loads on first use                                     |
| `category`                   | string            | Viewer category (3d, media, document, data, preview)                          |
| `featuresEnabledRestriction` | string[]?         | Required feature flags                                                        |
| `isPreviewViewer`            | boolean?          | True for the preview-only viewer                                              |
| `enabled`                    | boolean           | Whether the plugin is active                                                  |
| `customParameters`           | object?           | Viewer-specific configuration                                                 |
| `compareMode`                | object?           | Compare-mode opt-in (see 8.5)                                                 |

### 8.4.1 Files Without an Extension and the Binary Guard

A viewer opts into files with no extension (README, LICENSE, Makefile, `.gitignore`) by listing the
reserved token `(none)` (`NO_EXTENSION`, `core/extensionMatching.ts`) in `supportedExtensions`; only
`text-viewer` and `text-diff-viewer` list it (pinned by `config/viewerConfig.test.ts` and
`core/viewerSelection.test.ts`). One filename rule, `filenameExtension` (last path segment, split like
Python's `os.path.splitext`): `.gitignore`, `.bashrc` and `file.` have no extension, `.eslintrc.json`
has `.json`, `.env.example` has `.example`. `extensionToken` lowercases it, gives `(none)` when there is
none and undefined for a folder key. The token rides the usual every-file-must-match rule, so `LICENSE`
with `LICENSE.txt` is admitted by the differ and `README` with `model.glb` by nothing; `*` matches it.
Entry points gate by NAME (`isViewableFilename`, `isFilenameComparableAsVersions`,
`areFilenamesViewableTogether`, `areFilesComparableTogether`, `availableModesForFiles`; search rows by
`str_key` through `isViewableSearchRow`, never `str_fileext`); `extensionOfFilename` is display-only.
User text names the token with `describeExtensionToken` ("no extension").

Both text viewers load through `loadTextFile` (`core/textContent.ts`): `Range: bytes=0-8191`, classify
the first 8 KiB with `classifyTextSample`, stop when it is not text (a binary file is never downloaded
whole or highlighted), one more full GET for a larger text file, a 200 read as a stream and cancelled on
binary, S3's 416 on an empty object read as empty text, a network `TypeError` retried once without
`Range`. UTF-16 with a BOM is decoded; UTF-32 and BOM-less UTF-16 are an unsupported encoding; any
other NUL byte, or too many control bytes (10 %, or 2 % when not valid UTF-8), is binary. Notices:
`text-viewer-binary-notice` / `text-viewer-encoding-notice`, and per side in the differ
`text-diff-side-binary` / `text-diff-side-encoding` while the other side renders. Text over
`TEXT_VIEWER_MAX_BYTES` (20 MiB) is `too-large` (`text-viewer-too-large-notice`, `text-diff-side-too-large`)
and is not downloaded past its sample; over `TEXT_HIGHLIGHT_MAX_CHARS` the Text Viewer renders plain text
(`text-viewer-plain`). `textReadFailureMessage` words a failed presigned read (an S3 403/404 is a link to
refresh, a `TypeError` a connection problem). Full account:
`web/src/visualizerPlugin/CLAUDE.md`, "Files Without an Extension and the Binary Guard".

### 8.5 Compare Mode (cross-asset / multi-version)

`PluginRegistry.getCompatibleViewers(exts, isMultiFile, isPreview, mode, compareContext)` accepts
`mode: "compare"`, which surfaces only viewers declaring `compareMode.enabled` whose
`[minFiles, maxFiles]` window and shape flags admit the selection. The per-viewer admission predicates
for both paths are pure and unit-tested in `src/visualizerPlugin/core/viewerSelection.ts`
(`admitsVisualizeSelection`, `admitsCompareSelection`); the classification is pure and
unit-tested in `src/visualizerPlugin/core/compareShape.ts` (`deriveCompareContext(files)` →
`{ fileCount, shape, crossAsset }`); the registry re-exports both. **File identity is database + asset +
key, never the key alone** — the same key under two assets is `"different-files"` + `crossAsset: true`.

`compareMode` fields: `enabled`, `compareOnly?`, `minFiles`, `maxFiles`,
`allowSameFileDifferentVersions`, `allowDifferentFiles`, and `allowCrossAsset?` (default: not allowed).

**Compare-only viewers** (`compareMode.compareOnly: true`, e.g. `text-diff-viewer`) render nothing but
`compareFiles`, so the visualize path never offers them — one file or many, regardless of
`supportsMultiFile` or extension match — because visualize never passes `compareFiles`. Keep such a
viewer's `supportsMultiFile: false` (that flag is the visualize `multiFileKeys` capability; compare
file count is `minFiles`/`maxFiles`). Without `compareOnly`, compare capability is orthogonal to
visualize.

Contract for `compareFiles` entries (`ViewerPluginProps.compareFiles`, index 0 = left/base):

-   Each entry is a resolved `{ databaseId, assetId, key, versionId? }`. `DynamicViewer` fills a missing
    per-entry db/asset from its **top-level** props (never from `files[0]`) before classifying and before
    handing the list to the viewer.
-   Missing `versionId` = **latest** (search selections arrive this way via `searchRowToFileInfo`).
-   The viewer fetches every entry under **its own** db/asset via `downloadAsset` — each asset is
    Casbin-authorized independently — and renders a per-entry 401/403/410/404 as **that entry's** state
    while the other entry still renders. `downloadAsset` returns `[false, message, status]` on failure.
-   A viewer opting into `allowCrossAsset` offers a per-entry version picker (`fetchFileVersions`, one
    list per db+asset+key) and re-fetches only the entry whose version changed.

Reference implementation: `viewers/TextDiffViewerPlugin/TextDiffViewerComponent.tsx` (per-side
state, per-side error panel, per-side `Select` version picker; diff library dynamically imported;
compact controls for layout, Line/Word/Character granularity, line numbers, and collapse-unchanged
with context lines; single-line ellipsis-truncated side labels and library titles).
Surfaces: search results "Compare Selected" (rows may span assets), the file manager's "Compare
Selected Files" icon (`FileDetailsPanel.tsx`), and the version-compare actions in
`AssetVersionComparison.tsx` / `FileVersionsList.tsx`, all hosted by `FileViewerModal`.

**Mode availability — one rule for every entry point.** A viewer is EITHER a compare-differ
(`compareMode.compareOnly`) OR a regular viewer; never a hybrid (guarded over the shipped
`viewerConfig.json` by `viewerSelection.test.ts`). A surface offers **Visualize** only when
`hasVisualizeViewer` / `areFilenamesViewableTogether` says a non-compare viewer admits the selection,
and **Compare** only when `hasCompareViewer` / `areFilesComparableTogether` says a compare viewer admits
its file count, shape and types (`isFilenameComparableAsVersions(name)` for a per-row version Compare).
`FileViewerModal` consults `availableModesForFiles(files)`: both modes → toggle (top-right); one →
that mode, no toggle; none → "No viewer for this selection". `ViewerSelector` additionally filters
compare-only viewers out of the Visualize dropdown (`listableViewers`) regardless of the list it is
handed. Never gate a Visualize/Compare control with a hand-written extension list; use these
predicates behind `useViewerRegistryReady()`.

**Single-file compare.** A LONE file is judged for compare as two versions of itself:
`availableViewerModes` (pure, `viewerSelection.ts`) maps a one-file selection onto
`LONE_FILE_COMPARE_CONTEXT` (`compareContextForSelection`), the same question
`isFilenameComparableAsVersions` asks, so a single `.txt` (or `README`) admits BOTH modes while a single `.png`/`.glb`
stays Visualize-only — admission, never an extension list. The host seeds the pair with
`seedCompareFromSingleFile(file)` (`compareShape.ts`): `[the viewed entry (its pinned `versionId`, or
latest), the same file at latest]`; when the viewed side is latest both start at latest and the differ
shows its identical-versions notice while the user picks the other version in the per-side picker.
Toggling back to Visualize shows the one file again. Hosts: `FileViewerModal` (single-file view from
`FileDetailsPanel`) and the `ViewFile` page (where "View File" from `FileVersionsList` /
`AssetVersionComparison` lands; its toggle sits beside the File/Preview control and only on the File
tab). The version lists' per-row Compare keeps seeding `[snapshot@version, latest]`.

**Differ inside `DynamicViewer`.** Every plugin mounts inside `.visualizer-container-canvases`, which
`src/styles/index.scss` gives `text-align: center` and `line-height: 100%` for the 3D canvases. A
text-rendering viewer must reset both (`TextViewerPlugin` on its highlighter, `TextDiffViewerComponent`
at its root) or its lines render centered and stop wrapping. The differ also passes
`styles={{ titleBlock: { pre: { margin: 0 } } }}` to `react-diff-viewer-continued`: the library's
`pre` margin reset covers only the table, and the UA `1em` margin on the title `<pre>` pushed the
labels down and clipped them inside the fixed-height title block.

### 8.5.1 Text Diff Viewer Controls

`TextDiffViewerPlugin/TextDiffViewerComponent.tsx` exposes the `react-diff-viewer-continued` modes as compact controls (`data-testid="text-diff-controls"`):

-   **Layout**: Side-by-side / Inline → `splitView`.
-   **Granularity**: Line / Word / Character → `compareMethod` `DiffMethod.LINES` / `WORDS` / `CHARS`. Line also sets `disableWordDiff`, since the library keeps marking words inside a changed line under any `compareMethod`; without it "Line" still shows word-level marks.
-   **Line numbers** toggle → `hideLineNumbers`.
-   **Collapse unchanged** toggle → `showDiffOnly`, with a context-lines picker (`extraLinesSurroundingDiff`, `data-testid="text-diff-context-lines"`) shown only while collapsing.

The per-side labels (`text-diff-side-label-{left,right}`) and the titles handed to the library (`text-diff-title-{left,right}`) are single-line, ellipsis-truncated elements with the full label as a tooltip: the library's title block is a fixed 2.4em with a wrapping `<pre>`, so a long "name @ version · asset" label was otherwise cut off. In inline layout only the left title block renders, so it carries both names ("left → right").

When both sides hold identical text the viewer shows an info notice (`data-testid="text-diff-identical-notice"`, "No differences to show — the selected versions are identical") above the diff: with **Collapse unchanged** on, the library folds an identical file into a single `@@ -0,N +0,N @@` expander and the pane otherwise reads as empty. This is the state a lone file's Compare seed (latest vs latest) starts in; the fold row stays so the content can be expanded.

The `styles={{ titleBlock: { pre: { margin: 0 } } }}` object (8.5, "Differ inside `DynamicViewer`") is hoisted, because the library memoizes on the reference. `renderContent` highlights each line with react-syntax-highlighter's `Light` build as an INLINE run: `PreTag`/`CodeTag` spans, `display: inline`, every metric (font, size, line-height, colour, wrapping) inherited from the cell, the code tag's `white-space: pre` overridden to `inherit` so long lines wrap with the row, and the hljs theme passed explicitly (`Light` ships none; without it the tokens carry no colour). Do not give the per-line highlighter block metrics of its own.

Source: `web/src/visualizerPlugin/CLAUDE.md`, "Text Diff Viewer controls".

### 8.6 A Framed Viewer Must Not Receive a Signed URL in the Query String

A presigned Amazon S3 URL is a bearer credential: anyone holding it can read the object until it
expires. Putting one in an iframe's **query string** writes it into CloudFront or ALB access logs, which
are not treated as containing credentials and stay in the web app access-logs bucket until its
lifecycle rule removes them (`WebAppAccessLogsBucket` in `staticWebBuilder-nestedStack.ts`): the bucket
is versioned, so a log object expires after 30 days and its noncurrent version is deleted 30 days later.
When `app.useWaf` is on, the query string and the `Referer` headers also reach the `aws-waf-logs-vams-*`
log group of the web ACL in front of the site (`wafv2-basic-construct.ts`; in `us-east-1` for the
CloudFront ACL), which keeps events for one year and redacts only the `authorization` header. The
operator-facing account of both channels and their controls is in `troubleshooting/known-limitations.md`
("SuperSplat Editor File URLs in Web Logs").

`supersplat-viewer` currently does this (`SuperSplatViewerComponent.tsx` builds
`?load=<presigned>&filename=…`) — one object, expiring, but logged. **Do not copy the pattern into a new
viewer.** Use the **URL fragment** instead: browsers do not transmit a fragment, so nothing reaches the
server or its logs.

For a vendored build that reads `location.search` and cannot be changed at the call site, inject a shim
into its `index.html` from that viewer's `customInstalls/` script which moves the fragment into the query
string with `history.replaceState` before the bundle runs. Write the shim as a file beside `index.html`
and load it with a classic `<script src>` placed ahead of the bundle's: the CSP's `script-src` admits an
inline script only by the hashes of `web/index.html`'s own blocks (`cspInlineScriptHashes.ts`), so an
inline shim in a viewer page never runs. Patch the HTML entry rather than the minified bundle: the bundle
is regenerated from a pinned upstream tag on every `npm install`, so a regex against its internals breaks
on the next version bump while injected elements do not.

The shimmed page also needs `<meta name="referrer" content="no-referrer">` at the top of its `<head>`,
ahead of the shim and of every `<script>` and `<link>`. `replaceState` issues no request, but it
changes the document URL, and the browser builds the `Referer` of every later request the page makes
from that URL. The CloudFront distribution sends `Referrer-Policy: strict-origin-when-cross-origin`
(`cloudfront-s3-website-construct.ts`) and the ALB sends none, so the browser default applies, which is
the same policy. Both put the full URL, query string included, in the `Referer` of a same-origin request,
and a vendored viewer keeps fetching its own files after it starts (SuperSplat loads `static/locales/`
and `static/lib/webp/webp.wasm` from `/viewers/supersplat/`). Without the `<meta>`, each of those requests
carries the presigned URL into the web access logs: CloudFront standard logs record the `Referer`, and on
the ALB path so do the web bucket's S3 server access logs. A `<meta>` referrer policy replaces the
response header's for the requests the document makes after the element is parsed, which is why it goes
at the top, and it is the only control that covers both distributions, because the ALB cannot add a
`Referrer-Policy` header. The iframe's `referrerPolicy` attribute, which `HTMLViewerComponent.tsx` sets,
is not a substitute: it governs only the request that loads the framed page, not the requests that page
makes. A viewer that reads `location.hash` itself needs none of this, because a `Referer` never carries
the fragment.

`robotics-data-viewer` is the reference for the fragment pattern. `RoboticsDataViewerPlugin/roboticsDeepLink.ts`
builds `<basePath>#ds=<source>&ds.url=<url>…` with one `encodeURIComponent` per value and nothing in the
query. The framed Lichtblick app reads its deep link from `location.search`, so its VAMS entrypoint
(`web/customInstalls/robotics/overlay/entrypoint.tsx`) moves the fragment into the query from inside the
bundle rather than from a shim ahead of it: the move runs after the app has awaited its fonts, because a
CSS font request sends the document URL as its `Referer` whatever the page's referrer policy. Once the app
has read its deep link, the entrypoint replaces the document URL with one that carries no signed URL; the
app's own address-bar sync rewrites the URL through `decodeURIComponent`, which would otherwise split each
signed URL's `X-Amz-*` parameters into the page's query for the life of the page. The page still opens
`<head>` with the `no-referrer` `<meta>` and loads its former inline script as `vams-init.js`
(`web/customInstalls/robotics/postprocessIndexHtml.js`). A vendored app can also show or store the URL it
was given: the robotics overlay names the player by file name (the app bar and document title showed the
URL), takes the URLs out of the parameters the app saves as a recent source in IndexedDB, and rewrites
upstream error text that quotes the URL. Check those surfaces for any framed viewer that receives a signed
URL.

Note the encoding interaction if you move an existing viewer: SuperSplat decodes `load` **twice**, so its
value is deliberately double-encoded (see the comment in `SuperSplatViewerComponent.tsx`). Moving the
same string to a fragment must preserve that double encoding byte-for-byte, or the presigned signature
breaks and S3 returns 400.

### 8.7 TIFF/TGA Viewer (`RasterImageViewerPlugin/`)

Mirrors `web/src/visualizerPlugin/CLAUDE.md` "TIFF/TGA Viewer". `tiff-tga-viewer` opens `.tga`, `.tif` and `.tiff`, which no browser draws. It downloads the file, decodes it with `utif2` (TIFF) and `tga-js` (TGA), pure JavaScript with no `eval` and no CSP change, and draws to a `<canvas>`. `RasterImageDependencyManager` loads the decoders (and `pako`) with a dynamic `import()` so they stay out of the base bundle.

-   **Untrusted input.** `rasterDecode.ts` reads the TIFF directory chain itself (`inspectTiff`): `UTIF.decode` follows a cyclic chain forever and builds an array of any length a tag names. Pixel and decoded-size caps, strip and tile bounds, a bounded inflate of deflate strips and a JPEG frame-size check run before `UTIF.decodeImage`. A new variant is refused rather than drawn wrongly (BigTIFF, separate colour planes, old-style JPEG, unknown compression, 16-bit palette).
-   **Caps.** `customParameters.maxDecodeBytes` (256 MiB) and `maxDecodePixels` (100 megapixels) in `viewerConfig.json`. Above a cap or on a decode failure the viewer shows `fileInfo.previewFile` with a notice, else a plain message. A refused download (401/403/404/410) shows the shared message and never falls back.
-   **Conversion.** Samples of more than 8 bits and floats are scaled between the 0.5th and 99.5th percentile; CMYK becomes RGB; 16-bit TGA is shown opaque (`tga-js` reads its alpha bit the wrong way round).
-   **Decode worker.** Decoding runs in `rasterWorker.ts` (a same-origin module worker made by `createRasterWorker.ts`; the base CSP already allows `worker-src 'self' blob: data:`, so nothing changes there), so a large image does not freeze the tab and an out-of-memory worker ends in the fallback notice. The worker posts `{type: "ready"}` as its last statement and the page sends the file only after it. `rasterSession.ts` opens a session over the worker and falls back to decoding on the page (`RasterEngine`, same caps) when no worker can be created, when its script fails to load (blocked, 404 after a redeploy, no module worker support: an `error` before `ready`) or when it does not start within 15 s; only a worker that dies after it started ends in the "decoder stopped unexpectedly" notice. `dispose()` rejects a request still in flight. Module workers need Chrome/Edge 80, Firefox 114, Safari 15; `createRasterWorker.ts` holds `import.meta.url` so tests replace it with `jest.mock`. `pakoGlobal.ts` must be imported before `utif2` in the worker: `utif2` reads `self.pako` when `require` is not a function.
-   **Display.** The TIFF Orientation tag (274, values 1-8) is applied after conversion (`orient`); the page picker lists oriented sizes. A truncated run-length TGA is refused (`rleCovers`) because `tga-js` would leave the missing pixels black.
-   **Tests.** `rasterDecode.test.ts` builds its TIFF and TGA files in code (`rasterFixtures.testdata.ts`); real-file cases are in `web/e2e/viewers.spec.ts`.

### 8.8 No Runtime CDN Fetches

Mirrors `web/src/visualizerPlugin/CLAUDE.md` "CSP / `unsafe-eval`", "No runtime CDN fetches". A viewer library that loads a helper module from a public CDN at run time (unpkg, jsDelivr) is blocked by the CSP and cannot work in an air-gapped, GovCloud or EU Sovereign deployment. The install script rewrites each such URL in the built bundle to a same-origin copy (or turns the feature off), fails when an expected URL is missing, and fails when any reachable CDN URL remains: `web/customInstalls/online3dviewer/online3dviewerInstall.js` and `web/customInstalls/babylonjs/runtimeFetches.js`. Babylon.js 9's `@adobe/spz` path (SPZ version 4) stays off because Babylon loads it through an inline module script and it needs `unsafe-eval`; the BabylonJS viewer refuses SPZ version 4 with a message (`splatFileHead.ts`).

### 8.9 Three.js Selection Highlight (`ThreeJSViewerPlugin/utils/selectionHighlight.ts`)

Mirrors `web/src/visualizerPlugin/CLAUDE.md` "Three.js Selection Highlight".

-   A selected object wears a highlight clone of its material library entry, made only by `createHighlightMaterial` and put back only by `releaseHighlight` / `syncSelectionHighlight`, which track each clone by identity (a `WeakMap` to the material it came from) and dispose it on deselect. Never detect a highlight from a colour value; the material editor can set any colour.
-   A lit mesh material glows through `emissive` (0x4caf50, intensity 0.5). A material with no emissive colour (`PointsMaterial`, line and sprite materials, `MeshBasicMaterial`) is tinted green with `vertexColors` off.
-   `supportsWireframe(material)` is false for point, line and sprite materials: Wireframe Mode (Controls tab), the material editor's Wireframe option and the material reset skip them. Never add a `wireframe` property to them.

### 8.10 Three.js Referenced Files (`ThreeJSViewerPlugin/utils/`)

Mirrors `web/src/visualizerPlugin/CLAUDE.md` "Three.js Referenced Files". Every Three.js format that names other files goes through one resolver, `assetDependencyResolver.ts`; never fetch a referenced file anywhere else.

The viewer reads the main file and every referenced file from a presigned S3 URL that the authorized download route issues (`downloadAsset` through `core/presignedFetch.ts`, which caches a URL in memory for at most 60 seconds and never past its own `X-Amz-Expires`, less a margin of 10 seconds or 20 percent of its lifetime, whichever is smaller; `clearStoredSession()` clears the cache on every sign-out), with no `Authorization` header and no credentials. It does not follow the streaming route, whose 307 to the presigned URL is a cross-origin redirect that an external bucket's CORS rule refuses. A refused presign shows the API's own answer through `streamFailure.ts`; an error status from S3 itself is worded as an unavailable or expired link (`PresignedReadError`), after one re-presign when a reused URL got a 403, and is never shown as a VAMS permission or not-found answer. The presign request carries the viewer's abort signal. Needle USD, ThatOpen IFC, Cesium and VEERUM still read the streaming route.

-   `assetDependencyResolver.ts`: path rules (relative to the naming file; nothing above the asset root, absolute, or with a scheme is fetched), named limits (`DEPENDENCY_LIMITS`), one download per key, presigned-URL reads (`core/presignedFetch.ts`), and the `LoadingManager` URL modifier: resolved names map to blob URLs, `blob:`/`data:` pass through, anything else gets a transparent placeholder and is recorded for the viewer's notice. `revokeAll()` runs in viewer cleanup.
-   `dependencyLoading.ts`: `loadWithDependencies()` per format (glTF URI rewrite, OBJ `mtllib` + `MTLLoader`, COLLADA `<init_from>` pre-scan, 3DS/FBX probe parse). A USD load with no mesh, point or line throws `USD_NO_GEOMETRY_MESSAGE`, which the component shows instead of an empty canvas.
-   `usdDependencies.ts`: collects USD asset paths (USDA `@…@` scan; USDC only asset-path-typed fields, via the bundled `USDCParser`); `subLayers` entries are listed, not downloaded, while `USD_FETCH_SUBLAYERS` is `false`. `checkZipSize()` (`ZIP_LIMITS`) runs before any `.usdz` is extracted, and before every `.3mf` (also a zip package) is parsed, whatever its first bytes; without the bundle's `unzipSync` a `.3mf` is not parsed (`load3MF` in `fileLoaders.ts`).
-   `fileLoaders.ts`: `loadFile()` passes a `LoadingManager` to every loader; without one it uses an isolating manager, so no loader fetches by URL on its own. `USD_FORMATS` and `OCCT_FORMATS` are checked against `viewerConfig.json` by `config/viewerConfig.test.ts`.
-   The bundle (`web/customInstalls/threejs/threejsInstall.js`) exports `USDLoader`, `USDCParser`, `MTLLoader` and `fflate` (`zipSync`, `unzipSync`) for these modules.

---

### 8.5 Search Provider Registry

The search page is a set of **search provider** views under `src/searchPlugin/`, built the same way as the viewer plugins: a JSON catalog (`config/searchProviderConfig.json`), a manifest (`providers/manifest.ts`) so Vite sees every provider chunk, a singleton `core/SearchProviderRegistry.ts` (lazy `import.meta.glob` loading; availability read from `appCache` config), a pure `core/providerAvailability.ts` Jest can import, `core/useSearchProvidersReady.ts`, and `components/SearchTabsHost.tsx`. The host renders the page title row, then the active provider's body, loaded through `lazyWithRetry` like a route page. The title row holds the one h1 ("Assets and Files", or "Assets for <databaseId>" on a database route, in the deployment's asset synonym; `searchPageTitle`) and, when more than one provider is available, the view switch on its right: a Cloudscape `SegmentedControl` labelled `Page view` (Cloudscape shows it as a select below 688 px). The active view is the `?tab=` hash query, written with `replace`; an unknown or absent value selects the default view. Only the active body is mounted, and it receives `databaseId`, `isActive`, `assetActionDisabledReasons` and `titleSlots`. A body renders no page heading of its own: it portals its title-row content into `titleSlots.info` (beside the h1, the Search view's result count) and `titleSlots.actions` (before the switch, **Clear all filters**), and the asset list passes `hidePageHeading` to `ListPage`. The switch sits outside the body, so the control the user pressed stays mounted and keeps focus across a switch. Availability is `{ "always": true }` or `{ "anyOf": [ { "featureEnabled": X } | { "featureDisabled": X } ] }` over `featuresEnabled`; the lowest priority number is the default view. There is no runtime registration API and no add-on or fork lane for providers: adding a provider is a source change in the three places `manifest.ts` lists, plus a row here. The catalog holds the core providers only, so the switch is always a segmented control.

| ID               | Name       | Priority | Availability                                |
| ---------------- | ---------- | -------- | ------------------------------------------- |
| `asset-list`     | Asset list | 100      | always                                      |
| `unified-search` | Search     | 10       | VECTORSEARCH enabled or NOOPENSEARCH absent |

`config/searchProviderConfig.test.ts` fails when this table and the catalog disagree. The Search provider offers a `Keyword | Natural language` mode toggle when both engines are enabled, a reduced filter sidebar (database, file type, archived, **Search inside files**) when `NOOPENSEARCH` is present, and relevance as a percentage with a modality popover on natural-language hits. A finished search's count is not toasted (the toast stack sits over the title row's controls): `ModernSearchContainer` puts `Found N results` in a hidden Cloudscape `LiveRegion` for screen readers, and toasts only failures, **Search cleared** and row-action confirmations. The rare natural-language warnings show in a dismissible **Search notice** Alert above the results, derived from the answer on screen, and the live region reads their text after the count; `truncated:window` and `segments:window_full` raise no notice and are stated under the results instead. A keyword answer shows only its own codes there (`KEYWORD_WARNING_CODES`: `truncated:time`, a page the search time bound cut short, #1590). Pipeline, workflow, and trigger records with `isSystem: true` show a **System** badge and open read-only except for their `enabled` switches and template `configBody`/`tagSchema`.

The Search view's answers follow these rules (`ModernSearchContainer.tsx`):

-   **Newest search wins.** Each search takes the next number (`latestSearchRef`, through `startSearch`), and only the newest applies its answer, its error and the end of the loading state, so a page change, refresh or auto-refresh still running never replaces a newer answer. **Clear all filters** takes a number too and clears `answeredMode`, so no count shows until the reloaded list answers. Pass the number to `runSearch`; do not call the search API from a view.
-   **Clear all filters only for a change from the defaults.** `hasActiveFilters({ databaseLocked })` asks `hasUserSearchCriteria` (`hooks/useSearchState.tsx`): a query, a metadata filter row, or a filter that narrows or changes the search. The record type, the result explanation preference and a database route's locked database never count, and **Keyword Search Scope** and **Search inside files** count only once cleared. `clearSearch(["str_databaseid"])` keeps the locked database.
-   **One result and one dispatch for every view.** The table and the Map view both get `displayedResult` and `handleListDispatch`, so in natural-language mode both page through the answer held in the browser and a page change runs no search; the map's paginator sizes itself from `getPagedResultCount`.
-   **The Map view scopes by request.** A keyword search on the map adds `HAS_LOCATION_FILTER` (`_exists_:geo_MD_location`, `hooks/searchRequestBuilders.ts`) through `executeSearch`'s `requireLocation` option. It never adds metadata filter rows or changes the metadata mode or operator, and entering or leaving the map runs the keyword search again from page 1; in natural-language mode a view switch runs no search. A natural-language asset hit's `MD_` holds only its asset's location keys (`hasLocationOnlyMetadata`, `utils/recordType.ts`), so neither the map popup nor the Metadata column lists them as metadata.
-   **A 503 is a failed search, not a feature-off state.** `POST /search/nlp` answers 503 when OpenSearch cannot apply a metadata, tag or location filter, when Amazon Bedrock or Amazon DynamoDB is throttled or times out, or while the vector index is built, and `POST /search` when OpenSearch is enabled but unreachable. The page reports each as a **Search failed** toast with the API's message and keeps its controls; `opensearch:enrichment_failed` (sent only for a request with no such filter) is a Search notice.
-   **Controls only in the mode that uses them.** **Keyword Search Scope** (`includeMetadataInKeywordSearch`) and **Show result explanation** (`showResultExplanation`) are not rendered in natural-language mode, which never sends them.
-   **Filter panels.** The Database multiselect reads its selection through `selectedValues` (`hooks/searchRequestBuilders.ts`), because a database route locks the filter with `value` only, and the locked control names that database (`SearchLayout/lockedDatabase.ts`). Facet counts carry `+` when the count beside them is a minimum (`nlpCountsAreMinimums` in natural-language mode, `isTotalLowerBound` for a keyword answer). In a filtered search the type and tag lists keep the values of the last unfiltered search but show the current answer's counts, and none for a value it has no bucket for. A facet whose `sum_other_doc_count` is above 0 is cut (`isFacetCut`, `SearchLayout/BasicFiltersPanel.tsx`): the Database filter shows no count for a database without a bucket, and a type or tag list built from a cut facet (the answer's, or the cached unfiltered search's) shows `facetCutNote` under it.
-   **Breadcrumb.** `pages/search/SearchPage.tsx` ends the trail with `searchPageTitle(databaseId)` and adds only **Databases** before it on a database route; no step links to the page itself.
-   **"About this count".** The table header opens it from a Cloudscape `Button variant="inline-icon" iconName="status-info"` named "About this count", so the keyboard reaches and opens it.

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
2. `GET /secure-config` --> Additional config requiring auth
3. Both are stored in `appCache.setItem("config", ...)` for runtime access

When the cached config has no `featuresEnabled` (every sign-out clears it), `Auth.tsx` renders the deployment banner (`<GlobalHeader />`) and `<LoadingScreen />` instead of the app until secure-config has applied the feature switches, has given up after its last attempt, or has run past `SECURE_CONFIG_HOLD_MAX_MS` (10 seconds), because pages and the viewer registry read the switches once, when they mount.

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

A surface that stays mounted across a login (the side navigation) or that gates whole pages reads the
flags through `useFeaturesEnabled()` from `context/RuntimeConfigContext.ts` instead: the cached config
carries no `featuresEnabled` until the secure config lands after the first login, and only the context
re-renders a mounted consumer when it does.

Any side-navigation entry (a section, link or group in the core, add-on or custom lists) may carry
`featuresEnabledRestriction: string[]`, the field name and all-of meaning the viewer config uses:
`layout/Navigation.tsx` drops the entry until every listed switch is enabled, re-runs the web-route
check when the switches arrive, and strips the field before the items reach `SideNavigation`. An
add-on's switch names live in `addons/addonFeatures.ts` (`addonFeaturesEnabled`), each one declared in
`infra/common/addonAppFeatures.ts` (`addons/addonFeatures.test.ts`); a fork's live in
`custom/customFeatures.ts`.

Known feature flags:

-   `LOCATIONSERVICES` -- Map/geospatial features
-   `NOOPENSEARCH` -- Disable OpenSearch-dependent features
-   `ALLOWUNSAFEEVAL` -- Required for Needle USD, SuperSplat Editor, ThatOpen IFC BIM, Robotics Data Viewer, Online 3D Viewer .3dm files, and Three.js CAD formats (WASM loaders use eval). The Three.js viewer opens USD (.usd/.usda/.usdc/.usdz) without it; with it, Needle and Three.js both match USD and the user picks.
-   Three.js referenced files (textures, `.mtl`, USD layers) are fetched only through `ThreeJSViewerPlugin/utils/assetDependencyResolver.ts` (path rules, limits, URL modifier with placeholder) and, like the main file, read from presigned URLs of the download route through `core/presignedFetch.ts` rather than the redirecting streaming route (#1623); the Needle USD and ThatOpen IFC viewers read their files the same way, and the Cesium viewer reads every tileset file through a request hook (`CesiumViewerPlugin/presignedStreamReads.ts`) that turns a stream-route request into a presign call (#1630); Cesium needs no `null` in an external bucket's CORS rule only while that hook is active (without it the viewer falls back to the streaming route); the VEERUM add-on still reads the streaming route and needs `null` in an external bucket's CORS `AllowedOrigins`; see "Three.js Referenced Files" in `web/src/visualizerPlugin/CLAUDE.md`.
-   `VECTORSEARCH` -- Natural-language (vector) search: enables the NLP mode of the search page's Search view and `POST /search/nlp`; with `NOOPENSEARCH` also present the view runs in its reduced (NLP-only) form. The Executions board offers its `System-Reindex` trigger filter only with it (only the vector reindexer starts such runs)
-   Add-on flags (`addons/addonFeatures.ts`) are described in each add-on's own steering
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
4. **Inline styles** -- Acceptable for simple one-off styles

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

// INCORRECT -- don't hardcode colors/spacing that Cloudscape provides
.my-container {
    padding: 20px;
    color: #16191f;
}
```

### 10.5 A Resizable Table Column's Width Is Seeded Once

With `resizableColumns`, Cloudscape stores column widths in state and seeds each column **once**: on the render in which that column id first becomes visible. Its follow-up effect only seeds ids missing from both the stored widths and the previous visible list, so once an id has been rendered with one declared `width`, later changes to that declaration are **silently ignored**. The header keeps the first-seeded width, and the first resize drag makes it jump to the value you expected.

Raising `width` in `columnDefinitions` therefore appears to do nothing. The symptom looks like CSS compressing the column toward its `minWidth`, so the tempting fix is to raise `minWidth`, but that is not the lever. Cloudscape applies `table-layout: fixed` with `width: 100%` and `overflow-x: auto` on the wrapper, and the browser honors an over-budget column sum by widening the table and scrolling: a declared 700px column measures 700px even when the declared widths total more than the viewport.

The practical rule: **the value that selects the visible column set and the value that selects a column's declared width must come from one source, resolved in the same render.** A set chosen from component state while the width branch reads a value written from a `useEffect` leaves one render in which the column is visible under the wrong declaration, and that is the one that sticks. This is why `SearchPageListView` derives `isFileMode` from the container's `recordType` rather than from the `_rectype` filter.

Source: `web/CLAUDE.md`, "10.5 A Resizable Table Column's Width Is Seeded Once".

---

## 11. Testing

### 11.1 Test Setup

-   **Framework:** Jest 30 (`jsdom` environment via `jest-environment-jsdom`), `@testing-library/react`
-   **Config:** `web/jest.config.js`; jest-dom matchers registered via `setupFilesAfterEnv` -> `src/setupTests.ts`
-   **Cloudscape preset:** `@cloudscape-design/jest-preset` (transforms Cloudscape CSS/JS)
-   **Coverage thresholds:** the suite's measured global coverage rounded down to a whole percent (statements, lines and branches 28%, functions 30%; set in `jest.config.js`). CI runs `npx jest --ci --coverage`, so a change that drops coverage below them fails the build — raise them as coverage grows
-   **Run tests:** `npm test` (with coverage) or `npx jest` for a faster run

### 11.2 Test File Conventions

-   Test files are colocated with source: `MyComponent.test.tsx` next to `MyComponent.tsx`
-   OR in `__tests__/` directories
-   `npx jest --listTests | wc -l` (from `web/`) prints the current test-file count -- adding tests is welcome

### 11.3 Test Template

```tsx
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

-   Axios requires special mapping: `"^axios$": "axios/dist/browser/axios.cjs"` (the browser CommonJS build; `dist/axios.js` is not in axios's `exports` map, so Jest cannot resolve it)
-   Cloudscape components need custom transformers (configured in `jest.config.js`)
-   `transformIgnorePatterns` must stay a SINGLE pattern: a file matching ANY ignore pattern is excluded from transformation, so every ESM package that needs transforming (Cloudscape, d3-\*, internmap, react-leaflet, axios) must be exempted in one combined negative lookahead
-   Jest 30 removed deprecated matcher aliases (`toBeCalled`, `toBeCalledWith`, ...) — use the `toHaveBeenCalled*` forms

---

## 12. Development Workflow

### 12.1 Local Development

```bash
cd web
npm install           # Install dependencies + runs postinstall (viewer installs)
npm run start         # Start dev server (port 3001)
```

The dev server has no API proxy: `web/vite.config.ts` sets only port 3001 and the COOP/COEP headers. Set `DEV_API_ENDPOINT` in `src/config.ts` to point the dev server at a remote API or a local backend.

### 12.2 Build

```bash
npm run build         # Production build (output: web/dist/)
npm run typecheck     # Type-check only (tsc --noEmit); the Vite build does not type-check
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

After modifying code:

-   [ ] No new TypeScript errors (`npm run typecheck`, or check IDE; `npm run build` does not type-check)
-   [ ] No broken imports (check relative paths carefully)
-   [ ] Cloudscape components imported from subpaths (not barrel exports) (new imports; see Rule 2)
-   [ ] API calls go through service-layer files (`src/services/`), NOT direct `apiClient` imports in components
-   [ ] New routes added to `routeTable` in `routes.tsx` with `lazyWithRetry`
-   [ ] No hardcoded strings where `Synonyms` should be used

---

## 13. Anti-Patterns

### 13.1 Do NOT Import Cloudscape from Barrel Export (new imports; see Rule 2)

```typescript
// INCORRECT -- causes entire library to be bundled
import { Button, Table, Header } from "@cloudscape-design/components";

// CORRECT -- tree-shakeable individual imports
import Button from "@cloudscape-design/components/button";
import Table from "@cloudscape-design/components/table";
import Header from "@cloudscape-design/components/header";
```

### 13.2 Do NOT Import apiClient in Components/Pages

```typescript
// INCORRECT -- components/pages must never import apiClient directly
import { apiClient } from "../../services/apiClient";
const response = await apiClient.get("databases");

// INCORRECT -- never use raw fetch/axios either
const response = await fetch("/api/databases");
const response = await axios.get("/api/databases");

// CORRECT -- import from a service file
import { fetchDatabases } from "../../services/APIService";
const result = await fetchDatabases();
```

Only service files in `src/services/` may import `apiClient`.

### 13.2.1 Do NOT Use Amplify Cache

```typescript
// INCORRECT -- Amplify Cache is no longer used
import { Cache } from "aws-amplify";
Cache.setItem("config", data);

// CORRECT -- use appCache
import { appCache } from "../services/appCache";
appCache.setItem("config", data);
```

### 13.3 Do NOT Add Global State Libraries

```typescript
// INCORRECT -- no Redux, Zustand, MobX, Recoil, Jotai
import { createStore } from "redux";
import create from "zustand";

// CORRECT -- use React Context + useReducer
const MyContext = createContext<MyContextType | undefined>(undefined);
```

### 13.4 Do NOT Use BrowserRouter

```typescript
// INCORRECT -- the app uses HashRouter
import { BrowserRouter } from "react-router-dom";

// CORRECT
import { HashRouter } from "react-router-dom";
```

### 13.5 Do NOT Eagerly Import Page Components

```typescript
// INCORRECT -- defeats code splitting
import SearchPage from "./pages/search/SearchPage";

// CORRECT -- lazy load for route-level splitting
const SearchPage = lazyWithRetry(() => import("./pages/search/SearchPage"));
```

### 13.6 Do NOT Bypass the Auth Token Utilities

```typescript
// INCORRECT -- manually getting tokens
const session = await AmplifyAuth.currentSession();
const token = session.getAccessToken().getJwtToken();

// CORRECT -- use the dual-mode token utility
import { getDualValidAccessToken } from "../utils/authTokenUtils";
const token = await getDualValidAccessToken();
```

### 13.7 Do NOT Hardcode Display Names

```typescript
// INCORRECT
<Header>Assets</Header>
<p>Select a Database</p>

// CORRECT -- use Synonyms for customizable display names
import Synonyms from "../../synonyms";
<Header>{Synonyms.Assets}</Header>
<p>{`Select the ${Synonyms.database} to continue`}</p>
```

### 13.8 Do NOT Over-Document or Narrate Changes in Comments

Match the comment density and style already present in the file. Describe **what** a piece of code is, not why it was added. Never write comments that reference "upgrades", "new in vX", "added for", or the change request that prompted the edit — a comment should read as if the code had always been there. Changelog narration belongs in `CHANGELOG.md` and the docs revision history, not in source comments.

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
| `react-router-dom`              | ^7.18.2              | Client-side routing                  |
| `three`                         | (via customInstalls) | 3D rendering engine                  |
| `maplibre-gl`                   | ^6.9.0               | Map rendering                        |
| `react-pdf`                     | ^10.1.0              | PDF viewing                          |
| `papaparse`                     | ^5.4.1               | CSV parsing                          |
| `dompurify`                     | ^3.4.11              | HTML sanitization                    |
| `sanitize-html`                 | ^2.11.0              | HTML sanitization                    |
| `@dnd-kit/core`                 | ^6.3.1               | Drag and drop                        |
| `@testing-library/react`        | 14                   | Testing utilities (upgraded from 11) |

### 14.1 Key Service Files

-   **`src/services/apiClient.ts`** -- Custom fetch-based API client, injects auth headers automatically via `getDualAuthorizationHeader()`; `isRequestBlocked(error)` recognizes a `403` the deployment's AWS WAF web ACL blocked, whose message `parseErrorResponse` extends with the request ID; `isNetworkFailure(error)` recognizes a request whose response never arrived, which services report as `NETWORK_FAILURE_MESSAGE`
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
| Search page views    | `src/searchPlugin/components/SearchTabsHost.tsx`  |
| Upload service       | `src/services/AssetUploadService.ts`              |
| File operations      | `src/services/FileOperationsService.ts`           |
| Token utilities      | `src/utils/authTokenUtils.ts`                     |
| Synonyms config      | `src/synonyms.tsx`                                |
| Navigation registry  | `src/layout/navigationRegistry.ts`                |
| Navigation           | `src/layout/Navigation.tsx`                       |
| App shell            | `src/App.tsx`                                     |
| List page and table  | `src/components/list/TableList.tsx`               |
| List toolbar         | `src/components/list/ListToolbar.tsx`             |
| Column sorting       | `src/components/list/tableSorting.ts`             |

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

---

## 17. Common Tasks Quick Reference

### Adding a new Cloudscape table page

1. Create component in `src/components/myfeature/MyFeatureTable.tsx`
2. Use `@cloudscape-design/collection-hooks` for filtering/sorting
3. Lay out the toolbar and wire the column sorting per section 7.7
4. Create page wrapper in `src/pages/MyFeaturePage.tsx`
5. Add lazy import + route in `src/routes.tsx`
6. Add a navigation entry in `src/layout/navigationRegistry.ts` (`placement: "profile"` for a page of the signed-in user)

### Adding a new API call

1. Add the function to `src/services/APIService.ts` following section 16 template
2. Use `apiClient` for API calls (auth headers injected automatically)
3. Return `[boolean, data]` tuple
4. Handle errors with `try/catch`, return `[false, error?.message]`

### Adding a new viewer plugin

Follow the complete steps in section 8.3.

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

`vite.config.ts` defines no `server.proxy`. In local development the web app calls the API at `DEV_API_ENDPOINT` (`src/config.ts`) directly.

### 18.4 COI Service Worker

A Cross-Origin Isolation (COI) service worker is included for WASM-based viewers that require `SharedArrayBuffer`. This is loaded automatically in production.

### 18.5 Browser Support

Production targets: `>0.2%`, `not dead`, `not op_mini all`
Development targets: Latest Chrome, Firefox, Safari.

### 18.6 React Version

This project uses **React 18.3**. The entry point (`index.tsx`) mounts with `createRoot` from `react-dom/client`.

React 18 APIs (`useId`, `useSyncExternalStore`, `useTransition`, `useDeferredValue`, automatic batching) are allowed in the orchestration module (`features/orchestration/`) but should be used sparingly elsewhere to stay consistent with the existing codebase conventions.

### Audit Log Search (`pages/auth/AuditLogSearch.tsx`, `components/auditLogs/`)

Core page at `#/auth/audit-log-search/` (Admin - Auth), behind the `AUDITLOGSEARCH` feature switch (`featuresEnabled.AUDITLOGSEARCH`, in the feature list unless `app.auditLogging.search.enabled` is false; `components/auditLogs/auditSearchEnabled.ts` `isAuditLogSearchEnabled()` reads it). With the switch off the nav entry (`featuresEnabledRestriction`) and the Compliance Audit Log cross-link are hidden, and a direct URL shows the short state 'Audit Log Search is not enabled on this deployment' without any `/audit` call. The switch only avoids a dead page and grants nothing. With it on, the nav entry and the page appear only from the `POST /auth/routes` decision (the registry link and `routes.tsx` entry; a denied user gets `NoAccessPanel`). The Compliance Audit Log page links to it through `hooks/useWebRouteAllowed.ts`, which is false until that decision says yes, and only while the switch is on. The page derives no permission: log groups, actions, entity types, outcomes and limits come only from `GET /audit/log-groups` (`fetchAuditLogGroups`), and a 403 or an empty list shows a panel that names no group.

-   **Request model.** `auditSearchUrl.ts` (filters <-> hash query, defensive reading, a link restores filters and never runs the search), `auditSearchValidation.ts` (the server's value classes and the `limits` object, advisory only), `auditSearchApiModel.ts` (`buildStartBody`, response readers for the contract: `logGroups`, `Items`, `NextToken`, `statistics`, `warnings`), `auditFilterQuery.ts` (filters <-> `PropertyFilter` tokens and the `DateRangePicker` value; the picker works in UTC), `auditPollLoop.ts` (the long-poll loop: sequential calls, floor between calls, stops at once on 400/403/404, retries a dropped connection, client deadline), `useAuditSearch.ts` (start, poll, stop, server paging by `NextToken`, order change re-reads page 1).
-   **Query text is never sent.** The body holds named filters only; the server builds the query.
-   **Record content is untrusted text.** Render it only as React text nodes through `visibleText` (control, bidi and zero-width characters shown as `\uXXXX`) or `AuditValueTree` (depth and node bounded). No HTML sink, no link from a record value except an asset link built from validated ids with `encodeURIComponent`. The API returns every record time as `2026-11-17T01:00:00.000Z` (UTC, one format for legacy and v1 records); a server may send `2026-11-17 01:00:00.000` (UTC, no zone), so read it with `auditTimeMs`, never `new Date(string)`.
-   **Counts.** The page shows only what the API returns for the groups the caller may search. There is no count of rows the server removed on its recheck, and no "matched" figure (CloudWatch's raw match count would be that count): the progress text shows only records scanned. A stopped search shows no records (the API returns none), so `stop` reads no page. A link that brings other filters while results show drops the results and any running search (`reset`). The table shows one line per row (`OneLine` cells with a tooltip, column widths set, Wrap lines preference off by default).
-   **Notices.** Warnings and errors are toasts through `useToast()` (`features/orchestration/components/ToastProvider.tsx`), raised once per search (a ref set cleared when a new search starts); incomplete-result warnings use a 10-minute backstop (`WARNING_TOAST_BACKSTOP_MS`) and the page dismisses them (by id) when a new search starts, the results are cleared (phase `idle`) and on unmount, since the toast stack is app-wide. Field validation (contains too short, group not allowed) stays an inline `FormField` error with Search disabled, deliberately not a toast: a toast would vanish while the problem stays. Control row: aligned to the filter input row (label row to label row), Stop is always present (disabled when idle) and Search has a fixed minimum width so the row never changes width when a search starts; the status sits on its own line (`width:0; min-width:100%`) under the controls inside a live region that exists before its text. A Log group token reads as the group label through the property's `format` operator. Information is an info icon (`AuditInfoPopover.tsx`: Cloudscape `Popover` with a `formAction="none"` icon button, because the icon sits inside the search form). Live progress and `Search stopped` are a `StatusIndicator` beside the Search button. The time range, order and Search sit right of the property filter (flex row, wraps below at narrow widths). Blocking states (no access, no groups) stay page content.
-   **Export** joins the server's chunks (`csv`, `ndjson`) and downloads the file; the page rewrites no cell.
-   **Tests.** Jest: `auditLogs/auditSearch.model.test.ts`, `pages/auth/AuditLogSearch.test.tsx`; e2e: `e2e/auth.auditlogsearch.spec.ts` with the scripted API in `e2e/support/auditSearchMocks.ts`.
