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
    /** Lower comes first in the view switch and is the default view. */
    priority: number;
    enabled?: boolean;
    availability: SearchProviderAvailability;
}

export interface SearchProviderCatalog {
    providers: SearchProviderConfig[];
}

import type {
    AssetActionDisabledReasons,
    SearchTitleSlots,
} from "../../components/search/SearchPageTypes";

export type { SearchTitleSlots };

/** What the search page hands every provider body. */
export interface SearchProviderProps {
    /** The route's database, when the page is database-locked. */
    databaseId?: string;
    /** True for the selected view; a body may skip work while inactive. */
    isActive: boolean;
    /**
     * The page title row's slots. The page renders the h1 and the view switch; a body that adds to
     * that row (a count, a page action) portals into these instead of rendering a heading of its own.
     */
    titleSlots?: SearchTitleSlots;
    /**
     * Why the signed-in role may not create or delete assets, from the API's route listing. A
     * provider disables those actions with the reason; an absent reason leaves the action enabled.
     */
    assetActionDisabledReasons?: AssetActionDisabledReasons;
}
