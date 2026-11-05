/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export { SearchProviderRegistry } from "./core/SearchProviderRegistry";
export { useSearchProvidersReady } from "./core/useSearchProvidersReady";
export {
    availableProviders,
    conditionHolds,
    defaultProviderId,
    isProviderAvailable,
    providerLabel,
} from "./core/providerAvailability";
export { SearchTabsHost, SEARCH_TAB_QUERY_PARAM } from "./components/SearchTabsHost";
export type {
    SearchFeatureCondition,
    SearchProviderAvailability,
    SearchProviderCatalog,
    SearchProviderConfig,
    SearchProviderProps,
} from "./core/types";
