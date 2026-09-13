/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense } from "react";
import { useSearchParams } from "react-router-dom";
import Alert from "@cloudscape-design/components/alert";
import Tabs from "@cloudscape-design/components/tabs";
import ErrorBoundary from "../../components/common/ErrorBoundary";
import { LoadingSpinner } from "../../components/common/LoadingSpinner";
import { SearchProviderRegistry } from "../core/SearchProviderRegistry";
import { useSearchProvidersReady } from "../core/useSearchProvidersReady";
import { defaultProviderId, providerLabel } from "../core/providerAvailability";
import type { SearchProviderProps } from "../core/types";

/** The hash-query key that names the active tab (`/#/assets/?tab=unified-search`). */
export const SEARCH_TAB_QUERY_PARAM = "tab";

// One React.lazy per provider id, created once so the loaded chunk survives re-renders.
const lazyProviders = new Map<
    string,
    React.LazyExoticComponent<React.ComponentType<SearchProviderProps>>
>();

function lazyProvider(id: string) {
    let component = lazyProviders.get(id);
    if (!component) {
        component = React.lazy(() =>
            SearchProviderRegistry.getInstance()
                .loadProvider(id)
                .then((Component) => ({ default: Component }))
        );
        lazyProviders.set(id, component);
    }
    return component;
}

interface SearchTabsHostProps {
    databaseId?: string;
}

/**
 * The search page body: one tab per available provider. The active tab lives in the `?tab=` hash
 * query so a tab can be linked to; an unknown or absent value selects the lowest-priority provider.
 * Only the active tab's body is mounted; each body receives the route database and `isActive`.
 */
export const SearchTabsHost: React.FC<SearchTabsHostProps> = ({ databaseId }) => {
    const { ready, providers } = useSearchProvidersReady();
    const [searchParams, setSearchParams] = useSearchParams();

    const requested = searchParams.get(SEARCH_TAB_QUERY_PARAM);
    const activeTabId = providers.some((provider) => provider.id === requested)
        ? (requested as string)
        : defaultProviderId(providers);

    if (!ready) {
        return <LoadingSpinner text="Loading search..." />;
    }

    if (!activeTabId) {
        return (
            <Alert type="warning" header="Search is not available">
                No search provider is enabled for this deployment.
            </Alert>
        );
    }

    return (
        <ErrorBoundary componentName="Search Tabs">
            <Tabs
                activeTabId={activeTabId}
                onChange={({ detail }) =>
                    setSearchParams(
                        (previous) => {
                            const next = new URLSearchParams(previous);
                            next.set(SEARCH_TAB_QUERY_PARAM, detail.activeTabId);
                            return next;
                        },
                        { replace: true }
                    )
                }
                tabs={providers.map((provider) => {
                    const Provider = lazyProvider(provider.id);
                    const label = providerLabel(provider);
                    return {
                        id: provider.id,
                        label,
                        content: (
                            <Suspense fallback={<LoadingSpinner text={`Loading ${label}...`} />}>
                                <Provider
                                    databaseId={databaseId}
                                    isActive={activeTabId === provider.id}
                                />
                            </Suspense>
                        ),
                    };
                })}
            />
        </ErrorBoundary>
    );
};

export default SearchTabsHost;
