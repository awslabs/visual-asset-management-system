# CLAUDE.md - VAMS Frontend (web/)

This is the frontend-specific Claude Code steering document for the VAMS (Visual Asset Management System) web application. It is auto-loaded when working within the `web/` directory.

---

## 1. Architecture Overview

VAMS frontend is a **React 17.0.2 + TypeScript 4.4.4** single-page application built with Vite. All source files in `src/` have been converted to `.ts`/`.tsx` (the only remaining `.js` files are Jest mocks in `src/__mocks__/`).

**Primary UI library:** AWS Cloudscape Design System (`@cloudscape-design/components ^3.0.196`).

**Package manager:** npm. NEVER use yarn in this project.

**Key design decisions:**

-   NO global state library (no Redux, no Zustand, no MobX)
-   React Context API + useReducer for shared state
-   Custom apiClient (fetch-based) for API calls, AWS Amplify v6 for authentication
-   HashRouter (`#/` URLs) for all routing
-   Plugin-based 3D viewer architecture (17 viewer plugins)
-   React.lazy + Suspense for route-level code splitting

---

## 2. Directory Structure

> **Maintenance note:** Update this tree when adding new components, pages, services, or viewer plugins. See root `CLAUDE.md` Rule 11.

```
web/
  package.json              # npm, React 17, Vite scripts
  customInstalls/           # Viewer plugin custom install scripts
    babylonjs/
    cesium/
    needletools-usd-viewer/
    online3dviewer/
    playcanvas/
    potree/
    threejs/
    utility/
    veerum/
    vntana/
  src/
    App.tsx                 # Root app shell, HashRouter, TopNavigation
    routes.tsx              # Centralized route table, React.lazy, permission filtering
    config.ts               # Static config (VAMSConfig: APP_TITLE, DEV_API_ENDPOINT)
    config.json             # Build-time config
    synonyms.tsx            # Configurable display names (Asset, Database, Comment)
    index.tsx               # Entry point, ReactDOM.render
    reportWebVitals.ts      # Web vitals reporting
    setupTests.ts           # Jest setup

    FedAuth/                # Authentication orchestrator
      Auth.tsx              # Dual-mode auth: Cognito OR External OAuth2

    authenticator/          # Cognito Authenticator UI components
      Header.tsx
      Footer.tsx
      SignInHeader.tsx
      SignInFooter.tsx

    services/               # API and data services
      APIService.ts         # Main API service (~900+ lines, 40+ exports)
      AssetUploadService.ts # S3 multipart upload logic
      AssetVersionService.ts  # Includes updateAssetVersion, archiveAssetVersion, unarchiveAssetVersion
      FileOperationsService.ts
      MetadataService.ts    # Metadata CRUD operations
      MetadataSchemaService.ts  # Schema management

    context/                # React Context providers
      AssetContext.ts        # NOTE: typo is intentional, do NOT rename
      AssetDetailContext.ts  # useReducer-based context
      WorkflowContext.ts     # NOTE: typo is intentional, do NOT rename

    components/             # Domain/feature components
      asset/                # Asset viewing (ViewAsset.tsx is the main detail page)
        versions/           # Asset version management
          AssetVersionManager.tsx
          AssetVersionList.tsx
          AssetVersionComparison.tsx
          components/
            EditVersionModal.tsx      # Edit version alias/comment
            ArchiveVersionModal.tsx   # Archive/unarchive version
      common/               # Shared components
      containers/
      createupdate/         # Workflow create/update
      filemanager/          # File tree and file operations
      form/
      interactive/          # Map/geospatial components
      list/
      loading/              # Loading screens and spinners
      metadata/
      metadataSchema/
      metadataV2/
      modals/
      search/               # ModernSearchContainer.tsx - main search UI
      searchSmall/
      selectors/
      single/               # Single-entity views (ViewPipeline, ViewFile, AssetIngestion)
      table/

    pages/                  # Thin page wrappers composing components
      AssetDownload.tsx
      AssetUpload/          # Upload page and modify uploads
      Assets.tsx
      auth/                 # Constraints, Roles, UserRoles, CognitoUsers, ApiKeys
        ApiKeys.tsx           # API key list page
        CreateApiKey.tsx      # Create API key modal
        UpdateApiKey.tsx      # Update API key modal
      CommentListPage.tsx
      Databases.tsx
      Executions.tsx
      LandingPage.tsx
      ListPage.tsx
      ListPageNoDatabase.tsx
      MetadataSchema.tsx
      Pipelines.tsx         # Pipeline list; create/edit forms show conditional fields
                            #   for SQS (sqsQueueUrl) and EventBridge (busArn, source,
                            #   detailType) based on pipelineExecutionType selection.
                            #   Non-callback SQS/EventBridge pipelines display a
                            #   fire-and-forget alert (no files/previews/metadata returned).
      search/               # SearchPage.tsx
      Subscription/
      Tag/
      Workflows.tsx

    visualizerPlugin/       # 3D/media viewer plugin system
      index.ts              # Public API
      README.md             # Plugin development docs
      config/
        viewerConfig.json   # Plugin configuration (17 viewers)
      core/
        PluginRegistry.ts   # Singleton registry (manages all viewers)
        StylesheetManager.ts
        types.ts
      components/           # Shared viewer UI components
      viewers/              # Individual viewer plugins
        manifest.ts         # Webpack static analysis manifest
        AudioViewerPlugin/
        BabylonJSGaussianSplatViewerPlugin/
        CesiumViewerPlugin/
        ColumnarViewerPlugin/
        GameLiftStreamViewerPlugin/
        HTMLViewerPlugin/
        ImageViewerPlugin/
        NeedleUSDViewerPlugin/
        Online3dViewerPlugin/
        PDFViewerPlugin/
        PlayCanvasGaussianSplatViewerPlugin/
        PotreeViewerPlugin/
        TextViewerPlugin/
        ThreeJSViewerPlugin/
        VeerumViewerPlugin/
        VideoViewerPlugin/
        VntanaViewerPlugin/
      test/

    common/                 # Shared utilities and helpers
      apply-mode.ts
      clipboard.ts
      columnDefinitionsHelper.ts
      common-components.tsx
      constants/
      createPropertyStorage.ts
      External.tsx
      GlobalHeader.tsx
      helpers/
      i18nStrings.ts
      localStorage.ts
      property-filter/
      typeUtils.ts
      utils/

    constants/
      uploadLimits.ts

    external/               # External library integrations

    layout/
      Navigation.tsx        # Left sidebar navigation

    utils/
      authTokenUtils.ts     # getDualValidAccessToken, getDualAuthorizationHeader
      fileExtensionValidation.ts
      fileHandleCompat.ts

    styles/                 # Global styles
      theme.css             # CSS custom properties for dark/light theming
      abstracts/
      base/
      base.scss
      components/
      dashboard.scss
      form.scss
      header.scss
      index.scss
      landing-page.scss
      layout/
      onboarding.scss
      pages/
      utilities/
      wizard.scss

    resources/              # Static assets (images, logos)
    @types/                 # Custom TypeScript declarations
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

### Rule 2: Use Cloudscape Components

All UI components MUST use AWS Cloudscape Design System. Do NOT introduce Material UI, Ant Design, Chakra, or any other UI library.

```typescript
// CORRECT
import Button from "@cloudscape-design/components/button";
import Table from "@cloudscape-design/components/table";
import SpaceBetween from "@cloudscape-design/components/space-between";

// INCORRECT -- never import from the barrel export
import { Button, Table } from "@cloudscape-design/components";
```

**Always import individual Cloudscape components from their subpath**, not from the barrel export. This is critical for bundle size.

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

**Service files** (`src/services/`) are the ONLY files that may import `apiClient`:

-   `APIService.ts` — main API service (general CRUD, auth, search, subscriptions, tags, etc.)
-   `AssetUploadService.ts` — S3 upload operations
-   `AssetVersionService.ts` — version management
-   `FileOperationsService.ts` — file operations
-   `MetadataService.ts` — metadata CRUD
-   `MetadataSchemaService.ts` — schema management

When adding a new API endpoint, add the function to the appropriate service file (or `APIService.ts` if no specific service exists). Follow the `[boolean, data]` return tuple pattern.

### Rule 4: npm Only

```bash
# CORRECT
npm install
npm run start
npm run build

# INCORRECT -- NEVER use yarn
yarn install
```

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
const MyNewPage = React.lazy(() => import("./pages/MyNewPage"));

// INCORRECT
import MyNewPage from "./pages/MyNewPage";
```

### Rule 8: TypeScript Codebase

-   All source files are TypeScript (`.ts`/`.tsx`) -- new files MUST also be TypeScript
-   Only `src/__mocks__/*.js` files remain as `.js` (Jest CommonJS requirement)
-   Use `any` sparingly but pragmatically (the codebase uses it extensively)

---

## 4. API Integration Patterns

### 4.1 The Return Tuple Pattern

Most `APIService.ts` functions return `[boolean, data/errorMessage]` tuples:

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

| Context              | File                            | Purpose                          |
| -------------------- | ------------------------------- | -------------------------------- |
| `AssetContext`       | `context/AssetContext.ts`       | Asset list state                 |
| `AssetDetailContext` | `context/AssetDetailContext.ts` | Single asset detail with reducer |
| `WorkflowContext`    | `context/WorkflowContext.ts`    | Workflow state                   |

---

## 7. Component Patterns

### 7.1 Component Organization

-   **Pages** (`src/pages/`) -- thin wrappers that compose components, lazy-loaded in routes
-   **Components** (`src/components/`) -- organized by domain/feature
-   **Layout** (`src/layout/`) -- Navigation shell components
-   **Common** (`src/common/`) -- shared utilities, helpers, column definitions

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

```tsx
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

### 8.1 Architecture

The 3D/media viewer system uses a plugin-based architecture:

-   **PluginRegistry** (`src/visualizerPlugin/core/PluginRegistry.ts`) -- Singleton that manages all viewer plugins
-   **viewerConfig.json** (`src/visualizerPlugin/config/viewerConfig.json`) -- JSON configuration for all 17 plugins
-   **manifest.ts** (`src/visualizerPlugin/viewers/manifest.ts`) -- Vite static analysis paths for dynamic imports
-   **StylesheetManager** -- Per-plugin CSS lifecycle management

### 8.2 Current Viewers (17 plugins)

| ID                                 | Name                      | Category | Extensions                                                                       | Status                             |
| ---------------------------------- | ------------------------- | -------- | -------------------------------------------------------------------------------- | ---------------------------------- |
| `online3d-viewer`                  | Online 3D Viewer          | 3d       | .3dm, .amf, .bim, .off, .wrl                                                     | enabled                            |
| `potree-viewer`                    | Potree Viewer             | 3d       | .e57, .las, .laz, .ply                                                           | enabled                            |
| `image-viewer`                     | Image Viewer              | media    | .png, .jpg, .jpeg, .svg, .gif                                                    | enabled                            |
| `html-viewer`                      | HTML Viewer               | document | .html                                                                            | enabled                            |
| `video-viewer`                     | Video Player              | media    | .mp4, .webm, .mov, .avi, .mkv, .flv, .wmv, .m4v                                  | enabled                            |
| `audio-viewer`                     | Audio Player              | media    | .mp3, .wav, .ogg, .aac, .flac, .m4a                                              | enabled                            |
| `columnar-viewer`                  | Columnar Data Viewer      | data     | .rds, .fcs, .csv                                                                 | enabled                            |
| `pdf-viewer`                       | PDF Viewer                | document | .pdf                                                                             | enabled                            |
| `cesium-viewer`                    | Cesium 3D Tileset         | 3d       | .json                                                                            | enabled (requires ALLOWUNSAFEEVAL) |
| `text-viewer`                      | Text Viewer               | document | .txt, .json, .xml, .yaml, .md, .py, .js, .ts, etc.                               | enabled                            |
| `gaussian-splat-viewer-babylonjs`  | BabylonJS Gaussian Splat  | 3d       | .ply, .spz                                                                       | enabled                            |
| `gaussian-splat-viewer-playcanvas` | PlayCanvas Gaussian Splat | 3d       | .ply, .sog                                                                       | enabled                            |
| `vntana-viewer`                    | VNTANA 3D Viewer          | 3d       | .glb                                                                             | **disabled** (licensed)            |
| `veerum-viewer`                    | VEERUM 3D Viewer          | 3d       | .e57, .las, .laz, .ply, .json                                                    | **disabled** (licensed)            |
| `needletools-usd-viewer`           | Needle USD Viewer         | 3d       | .usd, .usda, .usdc, .usdz                                                        | enabled                            |
| `threejs-viewer`                   | Three.js Viewer           | 3d       | .gltf, .glb, .obj, .fbx, .stl, .ply, .dae, .3ds, .3mf, .stp, .step, .iges, .brep | enabled                            |
| `preview-viewer`                   | Preview Viewer            | preview  | \* (wildcard)                                                                    | enabled                            |

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
    asset,
    files,
    databaseId,
    onFullscreen,
    viewerConfig,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        // Initialize viewer
        return () => {
            // Cleanup on unmount
        };
    }, []);

    return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
};

export default MyViewerComponent;
```

**Step 3:** Add to `manifest.ts`:

```typescript
export const VIEWER_COMPONENTS = {
    // ... existing entries
    "./viewers/MyViewerPlugin/MyViewerComponent": "./MyViewerPlugin/MyViewerComponent",
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

### 8.4 Plugin Config Fields

| Field                        | Type              | Description                                          |
| ---------------------------- | ----------------- | ---------------------------------------------------- |
| `id`                         | string            | Unique plugin identifier                             |
| `componentPath`              | string            | Path for manifest lookup                             |
| `dependencyManager`          | string?           | Path to dependency loader module                     |
| `dependencyManagerClass`     | string?           | Class name in dependency module                      |
| `dependencyManagerMethod`    | string?           | Load method name                                     |
| `dependencyCleanupMethod`    | string?           | Cleanup method name                                  |
| `supportedExtensions`        | string[]          | File extensions this viewer handles                  |
| `supportsMultiFile`          | boolean           | Can handle multiple files at once                    |
| `canFullscreen`              | boolean           | Supports fullscreen mode                             |
| `priority`                   | number            | Lower = preferred when multiple viewers match        |
| `loadStrategy`               | "lazy" \| "eager" | When to load the component                           |
| `category`                   | string            | Viewer category (3d, media, document, data, preview) |
| `featuresEnabledRestriction` | string[]?         | Required feature flags                               |
| `isPreviewViewer`            | boolean?          | True for the preview-only viewer                     |
| `enabled`                    | boolean           | Whether the plugin is active                         |
| `customParameters`           | object?           | Viewer-specific configuration                        |

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
-   `ALLOWUNSAFEEVAL` -- Required for CesiumJS (uses eval)
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

// INCORRECT -- don't hardcode colors/spacing that Cloudscape provides
.my-container {
    padding: 20px;
    color: #16191f;
}
```

---

## 11. Testing

### 11.1 Test Setup

-   **Framework:** Vitest, `@testing-library/react`
-   **Cloudscape preset:** `@cloudscape-design/jest-preset` (transforms Cloudscape CSS/JS)
-   **Coverage thresholds:** Very low (branches: 6%, functions: 11%, lines: 11%, statements: 10%)
-   **Run tests:** `npm test`

### 11.2 Test File Conventions

-   Test files are colocated with source: `MyComponent.test.tsx` next to `MyComponent.tsx`
-   OR in `__tests__/` directories
-   Coverage is sparse (~10 test files total) -- adding tests is welcome

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

-   Axios requires special mapping: `"^axios$": "axios/dist/axios.js"`
-   Cloudscape components need custom transformers (configured in `package.json`)
-   Some `transformIgnorePatterns` are required for d3, axios, Cloudscape

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

After modifying code:

-   [ ] No new TypeScript errors (`npm run build` or check IDE)
-   [ ] No broken imports (check relative paths carefully)
-   [ ] Cloudscape components imported from subpaths (not barrel exports)
-   [ ] API calls go through service-layer files (`src/services/`), NOT direct `apiClient` imports in components
-   [ ] New routes added to `routeTable` in `routes.tsx` with `React.lazy`
-   [ ] No hardcoded strings where `Synonyms` should be used

---

## 13. Anti-Patterns

### 13.1 Do NOT Import Cloudscape from Barrel Export

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
const SearchPage = React.lazy(() => import("./pages/search/SearchPage"));
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
<p>Select a {Synonyms.Database}</p>
```

---

## 14. Key Dependencies

| Package                         | Version              | Purpose                     |
| ------------------------------- | -------------------- | --------------------------- |
| `react`                         | 17.0.2               | UI framework (NOT React 18) |
| `typescript`                    | 4.4.4                | Type system                 |
| `vite`                          | ^6.0.0               | Build tooling               |
| `aws-amplify`                   | v6 (latest)          | Auth integration (v6)       |
| `@cloudscape-design/components` | ^3.0.196             | AWS Cloudscape UI           |
| `@badgateway/oauth2-client`     | 2.4.2                | External OAuth2 PKCE flow   |
| `react-router-dom`              | ^6.0.0               | Client-side routing         |
| `styled-components`             | ^5.3.3               | CSS-in-JS (legacy usage)    |
| `three`                         | (via customInstalls) | 3D rendering engine         |
| `maplibre-gl`                   | ^5.8.0               | Map rendering               |
| `react-pdf`                     | ^10.1.0              | PDF viewing                 |
| `papaparse`                     | ^5.4.1               | CSV parsing                 |
| `dompurify`                     | ^2.3.6               | HTML sanitization           |
| `sanitize-html`                 | ^2.11.0              | HTML sanitization           |
| `@dnd-kit/core`                 | ^6.3.1               | Drag and drop               |

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

Vite's `server.proxy` in `vite.config.ts` configures the development proxy. API calls to `/api/*` are proxied to the backend.

### 18.4 COI Service Worker

A Cross-Origin Isolation (COI) service worker is included for WASM-based viewers that require `SharedArrayBuffer`. This is loaded automatically in production.

### 18.5 Browser Support

Production targets: `>0.2%`, `not dead`, `not op_mini all`
Development targets: Latest Chrome, Firefox, Safari.

### 18.6 React Version

This project uses **React 17**, not React 18. Do NOT use:

-   `createRoot` (React 18)
-   `useId` (React 18)
-   `useSyncExternalStore` (React 18)
-   `useTransition` / `useDeferredValue` (React 18)
-   Automatic batching assumptions (React 18)

The app uses `ReactDOM.render` (React 17 style).
