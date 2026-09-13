/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Synonyms from "../../synonyms";
import type { SearchFeatureCondition, SearchProviderConfig } from "./types";

/**
 * Availability and ordering rules for search providers. Kept apart from the registry so they can be
 * unit tested: the registry uses import.meta.glob, which Jest cannot parse.
 */

export function conditionHolds(condition: SearchFeatureCondition, features: string[]): boolean {
    if ("featureEnabled" in condition) {
        return features.includes(condition.featureEnabled);
    }
    return !features.includes(condition.featureDisabled);
}

export function isProviderAvailable(config: SearchProviderConfig, features: string[]): boolean {
    if (config.enabled === false) return false;
    if ("always" in config.availability) return config.availability.always === true;
    return config.availability.anyOf.some((condition) => conditionHolds(condition, features));
}

/** The providers to offer, lowest priority number first (ties by id). */
export function availableProviders(
    catalog: SearchProviderConfig[],
    features: string[]
): SearchProviderConfig[] {
    return catalog
        .filter((config) => isProviderAvailable(config, features))
        .sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id));
}

export function defaultProviderId(providers: SearchProviderConfig[]): string | undefined {
    return providers[0]?.id;
}

/** The tab label: the catalog name with the whole words Asset/Assets replaced by the deployment's synonyms. */
export function providerLabel(config: SearchProviderConfig): string {
    return config.name
        .replace(/\bAssets\b/g, Synonyms.Assets)
        .replace(/\bAsset\b/g, Synonyms.Asset);
}
