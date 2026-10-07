/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import catalog from "../config/searchProviderConfig.json";
import { PROVIDER_COMPONENTS } from "../providers/manifest";
import { appCache } from "../../services/appCache";
import { normalizeFeaturesEnabled } from "../../common/constants/featuresEnabled";
import { availableProviders } from "./providerAvailability";
import type { SearchProviderCatalog, SearchProviderConfig, SearchProviderProps } from "./types";

/**
 * The search-provider registry: the catalog, the availability gate over the cached feature switches,
 * and lazy loading of provider components (one Vite chunk per provider).
 */
export class SearchProviderRegistry {
    private static instance: SearchProviderRegistry;

    // Lazy-loaded provider component modules (Vite creates a chunk for each at build time)
    private static providerModules = import.meta.glob<{
        default: React.ComponentType<SearchProviderProps>;
    }>("../providers/**/*ProviderComponent.tsx");

    private loaded = new Map<string, React.ComponentType<SearchProviderProps>>();

    static getInstance(): SearchProviderRegistry {
        if (!SearchProviderRegistry.instance) {
            SearchProviderRegistry.instance = new SearchProviderRegistry();
        }
        return SearchProviderRegistry.instance;
    }

    getCatalog(): SearchProviderConfig[] {
        return (catalog as SearchProviderCatalog).providers;
    }

    /**
     * The providers the current `appCache` config allows, in tab order. Read on every call, so a
     * host that mounts after the secure-config fetch sees the switches that fetch delivered.
     */
    getAvailableProviders(): SearchProviderConfig[] {
        const features = normalizeFeaturesEnabled(appCache.getItem("config")?.featuresEnabled);
        return availableProviders(this.getCatalog(), features);
    }

    async loadProvider(id: string): Promise<React.ComponentType<SearchProviderProps>> {
        const cached = this.loaded.get(id);
        if (cached) return cached;

        const config = this.getCatalog().find((provider) => provider.id === id);
        if (!config) {
            throw new Error(`Search provider not in catalog: ${id}`);
        }
        const relativePath =
            PROVIDER_COMPONENTS[config.componentPath as keyof typeof PROVIDER_COMPONENTS];
        if (!relativePath) {
            throw new Error(
                `Component path not found in manifest: ${config.componentPath}. Add it to PROVIDER_COMPONENTS in providers/manifest.ts`
            );
        }
        const loader = SearchProviderRegistry.providerModules[`../providers/${relativePath}.tsx`];
        if (!loader) {
            throw new Error(
                `Provider module not found for: ${relativePath}. Available: ${Object.keys(
                    SearchProviderRegistry.providerModules
                ).join(", ")}`
            );
        }
        const module = await loader();
        this.loaded.set(id, module.default);
        return module.default;
    }
}
