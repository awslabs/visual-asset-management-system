# VAMS Web Frontend Development Workflow & Rules

This document provides comprehensive guidelines for developing and extending the VAMS web frontend application. Follow these rules to ensure consistency, quality, and maintainability across all frontend implementations.

## 🏗️ **Architecture Overview**

### **Technology Stack**

| Technology       | Version/Details                                                |
| ---------------- | -------------------------------------------------------------- |
| React            | 17.0.2 (NOT React 18)                                          |
| TypeScript       | 4.4.4                                                          |
| Vite             | ^6.0.0                                                         |
| UI Library       | AWS Cloudscape Design System (`@cloudscape-design/components`) |
| Auth             | AWS Amplify v6 + `@badgateway/oauth2-client` (dual-mode)       |
| Routing          | React Router v6 with HashRouter                                |
| State Management | React Context API + useReducer (NO global state libraries)     |
| API Client       | Custom fetch-based apiClient with auto auth headers            |
| Package Manager  | npm (NEVER yarn)                                               |

### **File Structure Standards**

```
web/
  package.json              # npm, React 17, Vite scripts
  customInstalls/           # Viewer plugin custom install scripts
  src/
    App.tsx                 # Root app shell, HashRouter, TopNavigation
    routes.tsx              # Centralized route table, React.lazy, permission filtering
    config.ts               # Static config (VAMSConfig: APP_TITLE, DEV_API_ENDPOINT)
    synonyms.tsx            # Configurable display names (Asset, Database, Comment)
    index.tsx               # Entry point, ReactDOM.render

    FedAuth/                # Authentication orchestrator
      Auth.tsx              # Dual-mode auth: Cognito OR External OAuth2

    services/               # API and data services (ONLY place apiClient is imported)
      APIService.ts         # Main API service (~900+ lines, 40+ exports)
      apiClient.ts          # Custom fetch wrapper (internal, never import from components)
      appCache.ts           # localStorage cache (replaces Amplify Cache)
      AssetUploadService.ts # S3 multipart upload logic
      AssetVersionService.ts
      FileOperationsService.ts
      MetadataService.ts
      MetadataSchemaService.ts

    context/                # React Context providers
      AssetContext.ts        # NOTE: typo is intentional, do NOT rename
      AssetDetailContext.ts  # useReducer-based context
      WorkflowContext.ts     # NOTE: typo is intentional, do NOT rename

    components/             # Domain/feature components
      asset/                # Asset viewing (ViewAsset.tsx is the main detail page)
      common/               # Shared components
      containers/
      createupdate/         # Workflow create/update
      filemanager/          # File tree and file operations
      form/
      interactive/          # Map/geospatial components
      list/
      loading/
      metadata/
      metadataSchema/
      metadataV2/
      modals/
      search/               # ModernSearchContainer.tsx - main search UI
      selectors/
      single/               # Single-entity views
      table/

    pages/                  # Thin page wrappers composing components
      AssetDownload.tsx
      AssetUpload/
      Assets.tsx
      auth/                 # Constraints, Roles, UserRoles, CognitoUsers, ApiKeys
      Databases.tsx
      LandingPage.tsx
      Pipelines.tsx
      search/
      Workflows.tsx

    visualizerPlugin/       # 3D/media viewer plugin system (17 plugins)
      core/
        PluginRegistry.ts   # Singleton registry
        types.ts
      config/
        viewerConfig.json   # Plugin configuration
      viewers/              # Individual viewer plugins
        manifest.ts

    common/                 # Shared utilities and helpers
    layout/
      Navigation.tsx        # Left sidebar navigation
    utils/
      authTokenUtils.ts     # getDualValidAccessToken, getDualAuthorizationHeader
    styles/
      theme.css             # CSS custom properties for dark/light theming
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
-   [ ] **Use Cloudscape components**: Import from individual subpaths
-   [ ] **Use Synonyms**: For all user-visible "Asset", "Database", "Comment" text
-   [ ] **Use TypeScript**: All new files must be `.ts` or `.tsx`
-   [ ] **Import from service layer**: Never import `apiClient` directly
-   [ ] **Use Context for shared state**: Follow `AssetDetailContext.ts` patterns
-   [ ] **Handle API responses**: Check boolean flag before using data

#### **Step 3: Page and Route Setup**

-   [ ] **Create thin page wrapper**: In `src/pages/`
-   [ ] **Add lazy import**: `React.lazy(() => import("./pages/MyPage"))` in `routes.tsx`
-   [ ] **Add to routeTable**: With `path`, `Page`, and `active` properties
-   [ ] **Add navigation item**: In `src/layout/Navigation.tsx` if needed

### **Phase 3: Quality Assurance**

#### **Step 4: Verification**

-   [ ] **No TypeScript errors**: Run `npm run build` or check IDE
-   [ ] **No broken imports**: Verify relative paths (no path aliases in this project)
-   [ ] **Cloudscape subpath imports**: Not barrel exports
-   [ ] **Service layer compliance**: No direct `apiClient` imports in components/pages
-   [ ] **Synonyms compliance**: No hardcoded "Asset"/"Database"/"Comment" in user-visible text
-   [ ] **HashRouter compliance**: No `BrowserRouter` usage
-   [ ] **Lazy loading compliance**: All pages lazy-loaded in `routes.tsx`
-   [ ] **React 17 compliance**: No React 18 APIs (`createRoot`, `useId`, etc.)

#### **Step 5: Documentation Updates**

-   [ ] **Update Docusaurus docs**: If UI navigation or features changed
-   [ ] **Update user guide**: `documentation/docusaurus-site/docs/user-guide/` if user-facing
-   [ ] **Update developer docs**: `documentation/docusaurus-site/docs/developer/frontend.md`
-   [ ] **Update CLAUDE.md**: If structural changes made (see root CLAUDE.md Rule 11)

---

## 🚨 **Mandatory Rules**

### **Rule 1: All API Calls Go Through Service Layer Files**

Components and pages MUST NEVER import `apiClient` directly. Only service files in `src/services/` may import `apiClient`.

```typescript
// CORRECT -- import from a service file
import { fetchAssets, deleteAsset } from "../../services/APIService";

// INCORRECT -- never import apiClient in components or pages
import { apiClient } from "../../services/apiClient";

// INCORRECT -- never use fetch or axios directly
const response = await fetch("/api/databases");
```

When adding a new API endpoint, add the function to the appropriate service file (or `APIService.ts` if no specific service exists). Follow the `[boolean, data]` return tuple pattern.

### **Rule 2: Use Cloudscape Components with Subpath Imports**

All UI components MUST use AWS Cloudscape Design System. Import from individual subpaths, NEVER from the barrel export.

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

Every page component in `routes.tsx` MUST be lazy-loaded with `React.lazy`:

```typescript
// CORRECT
const MyPage = React.lazy(() => import("./pages/MyPage"));

// INCORRECT -- defeats code splitting
import MyPage from "./pages/MyPage";
```

### **Rule 5: No Global State Libraries**

This codebase uses NO global state library (no Redux, Zustand, MobX, Recoil, Jotai). Use React Context API + `useReducer` for shared state.

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

```bash
# CORRECT
npm install
npm run start
npm run build

# INCORRECT
yarn install
```

### **Rule 10: React 17 Compatibility**

This project uses React 17.0.2. Do NOT use React 18 APIs:

-   No `createRoot` (use `ReactDOM.render`)
-   No `useId`, `useSyncExternalStore`, `useTransition`, `useDeferredValue`
-   No automatic batching assumptions

### **Rule 11: Use appCache, Not Amplify Cache**

```typescript
// CORRECT
import { appCache } from "../services/appCache";
appCache.setItem("config", data);

// INCORRECT
import { Cache } from "aws-amplify";
Cache.setItem("config", data);
```

### **Rule 12: Use Dual-Mode Auth Token Utilities**

```typescript
// CORRECT
import { getDualValidAccessToken } from "../utils/authTokenUtils";
const token = await getDualValidAccessToken();

// INCORRECT -- manually getting tokens
const session = await AmplifyAuth.currentSession();
```

### **Rule 13: Do NOT Rename Intentional Typos**

`AssetContext.ts` and `WorkflowContext.ts` -- the file names are intentional. NEVER rename them.

### **Rule 14: Update Documentation When Making Frontend Changes**

When frontend changes affect user-facing functionality, update the relevant Docusaurus documentation:

| Change Type          | Documentation to Update                                        |
| -------------------- | -------------------------------------------------------------- |
| UI navigation change | `user-guide/web-interface.md`, `user-guide/getting-started.md` |
| New feature/page     | `overview/features.md`, relevant user guide page               |
| New viewer plugin    | `developer/viewer-plugins.md`, `additional/viewer-plugins.md`  |
| Config/feature flag  | `deployment/configuration-reference.md`                        |
| Search UI change     | `user-guide/search.md`                                         |
| Upload flow change   | `user-guide/upload-tutorial.md`                                |

### **Rule 15: Update Steering Files When Standards Change**

When system-wide frontend standards change (new rules, new patterns, new conventions), update all three locations:

1. `web/CLAUDE.md` -- frontend steering document
2. `.kiro/steering/WEB_DEVELOPMENT_WORKFLOW.md` -- this file
3. `.clinerules/workflows/WEB_DEVELOPMENT_WORKFLOW.md` -- identical copy

---

## 📐 **Implementation Standards**

### **API Integration: Return Tuple Pattern**

Most `APIService.ts` functions return `[boolean, data/errorMessage]` tuples:

```typescript
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

**Consuming API results:**

```typescript
const result = await fetchAssets({ databaseId });
if (result === false || result[0] === false) {
    setError(result ? result[1] : "Unknown error");
    return;
}
const data = result[1];
```

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
| Navigation           | `src/layout/Navigation.tsx`                       |
| App shell            | `src/App.tsx`                                     |

---

## 📝 **Development Templates**

### **New Page Template**

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

Then in `routes.tsx`:

```typescript
const MyPage = React.lazy(() => import("./pages/MyPage"));

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
            return response;
        }
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
npm test              # Run tests (Vitest + @testing-library/react)
```

**Lint and format (from project root):**

```bash
npm run lint          # Lint check (web/src + infra/lib + infra/bin + infra/test)
npm run lint-fix      # Auto-fix lint issues
npm run prettier-check
npm run prettier-fix
```

---

## 🔍 **Code Review Checklist**

### **Compliance Checks**

-   [ ] Copyright headers present on all new/modified files
-   [ ] Cloudscape components imported from subpaths (not barrel)
-   [ ] API calls go through service layer (no direct `apiClient` imports in components)
-   [ ] `Synonyms` used for all user-visible entity names
-   [ ] All pages lazy-loaded in `routes.tsx`
-   [ ] HashRouter used (no BrowserRouter)
-   [ ] React 17 compatible (no React 18 APIs)
-   [ ] TypeScript used for all new files
-   [ ] `appCache` used instead of Amplify Cache
-   [ ] `getDualValidAccessToken` used for auth tokens
-   [ ] No global state libraries introduced
-   [ ] npm used (no yarn)
-   [ ] Relative imports used (no path aliases)
-   [ ] Theme-aware styles (Cloudscape design tokens or theme.css custom properties)

### **Documentation Checks**

-   [ ] Docusaurus docs updated if user-facing features changed
-   [ ] `web/CLAUDE.md` updated if structural changes made
-   [ ] Steering files updated if frontend standards changed

---

## 🛠️ **Common Tasks Quick Reference**

### **Adding a new Cloudscape table page**

1. Create component in `src/components/myfeature/MyFeatureTable.tsx`
2. Use `@cloudscape-design/collection-hooks` for filtering/sorting
3. Create page wrapper in `src/pages/MyFeaturePage.tsx`
4. Add lazy import + route in `src/routes.tsx`
5. Add navigation item in `src/layout/Navigation.tsx`

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
2. **Importing Cloudscape from barrel export** -- use individual subpaths
3. **Using BrowserRouter** -- must use HashRouter
4. **Eagerly importing pages** -- must use React.lazy
5. **Adding Redux/Zustand/MobX** -- use Context + useReducer
6. **Using Amplify Cache** -- use appCache
7. **Manually getting auth tokens** -- use getDualValidAccessToken
8. **Hardcoding entity display names** -- use Synonyms
9. **Using React 18 APIs** -- project is React 17
10. **Using yarn** -- project uses npm
11. **Creating .js files** -- all new files must be TypeScript
12. **Hardcoding colors/spacing** -- use Cloudscape design tokens or theme.css
