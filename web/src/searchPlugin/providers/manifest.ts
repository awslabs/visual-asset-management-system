/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Provider manifest: constant paths so the bundler can statically see every provider chunk the
// registry's import.meta.glob may load. Keys are the catalog's componentPath values.
export const PROVIDER_COMPONENTS = {
    "AssetListProvider/AssetListProviderComponent": "AssetListProvider/AssetListProviderComponent",
    "UnifiedSearchProvider/UnifiedSearchProviderComponent":
        "UnifiedSearchProvider/UnifiedSearchProviderComponent",
} as const;

// Adding a provider:
// 1. Create providers/MyProvider/MyProviderComponent.tsx (default export: React.FC<SearchProviderProps>)
// 2. Add its entry to PROVIDER_COMPONENTS above
// 3. Add its catalog entry to config/searchProviderConfig.json
// 4. Add its row to the catalog table in web/CLAUDE.md (searchProviderConfig.test.ts guards it)
