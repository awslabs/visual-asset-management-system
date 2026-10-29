/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** One feature-switch test. Feature names are the values of `featuresEnabled` (common/constants). */
export type SearchFeatureCondition = { featureEnabled: string } | { featureDisabled: string };

/** When a provider is offered: unconditionally, or when any listed condition holds. */
export type SearchProviderAvailability = { always: true } | { anyOf: SearchFeatureCondition[] };

export interface SearchProviderConfig {
    id: string;
    name: string;
    description?: string;
    /** Key into PROVIDER_COMPONENTS (providers/manifest.ts). */
    componentPath: string;
    /** Lower renders first and is the default tab. */
    priority: number;
    enabled?: boolean;
    availability: SearchProviderAvailability;
}

export interface SearchProviderCatalog {
    providers: SearchProviderConfig[];
}

/** What the tab host hands every provider body. */
export interface SearchProviderProps {
    /** The route's database, when the page is database-locked. */
    databaseId?: string;
    /** True for the selected tab; a body may skip work while inactive. */
    isActive: boolean;
}
