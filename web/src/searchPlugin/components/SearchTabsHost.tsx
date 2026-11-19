/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import { spaceScaledXs } from "@cloudscape-design/design-tokens";
import ErrorBoundary from "../../components/common/ErrorBoundary";
import { LoadingSpinner } from "../../components/common/LoadingSpinner";
import { lazyWithRetry } from "../../utils/chunkLoadRecovery";
import Synonyms from "../../synonyms";
import { SearchProviderRegistry } from "../core/SearchProviderRegistry";
import { useSearchProvidersReady } from "../core/useSearchProvidersReady";
import { defaultProviderId, providerLabel } from "../core/providerAvailability";
import type { SearchProviderProps, SearchTitleSlots } from "../core/types";

/** The hash-query key that names the active view (`/#/assets/?tab=unified-search`). */
export const SEARCH_TAB_QUERY_PARAM = "tab";

/** The accessible name of the view switch (and its visible label where it renders as a select). */
export const SEARCH_VIEW_SWITCH_LABEL = "Page view";

// The title row's right side: a body's actions, then the view switch, wrapping on a narrow screen.
// Bottom-aligned: below 688 px the switch is a select with its label above it, and the actions line
// up with the select rather than with the label.
const titleActionsStyle: React.CSSProperties = {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "flex-end",
    gap: spaceScaledXs,
};

/** The page h1: every view shares it, so switching views never moves or renames the heading. */
export function searchPageTitle(databaseId?: string): string {
    return databaseId ? `${Synonyms.Assets} for ${databaseId}` : `${Synonyms.Assets} and Files`;
}

// One lazy component per provider id, created once so the loaded chunk survives re-renders. A provider
// body is the page's content, so its chunk recovers like a route page's (lazyWithRetry): a failed download
// is retried, then the page reloads once; a persistent failure reaches the boundary below.
const lazyProviders = new Map<
    string,
    React.LazyExoticComponent<React.ComponentType<SearchProviderProps>>
>();

function lazyProvider(id: string) {
    let component = lazyProviders.get(id);
    if (!component) {
        component = lazyWithRetry(() =>
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
    /** Passed through to every provider body (see SearchProviderProps). */
    assetActionDisabledReasons?: SearchProviderProps["assetActionDisabledReasons"];
}

/**
 * The search page body: one title row, then the active provider's body. The title row holds the h1
 * and, with more than one provider available, the view switch; a body adds its own title-row
 * content (a count, a page action) through `titleSlots`. The row sits outside the body, so a switch
 * keeps the control the user pressed mounted and focused.
 *
 * The active view lives in the `?tab=` hash query so a view can be linked to; an unknown or absent
 * value selects the lowest-priority provider. Only the active body is mounted; it receives the
 * route database, `isActive`, the page's asset action reasons and the title slots.
 */
export const SearchTabsHost: React.FC<SearchTabsHostProps> = ({
    databaseId,
    assetActionDisabledReasons,
}) => {
    const { providers } = useSearchProvidersReady();
    const [searchParams, setSearchParams] = useSearchParams();
    // Callback refs: a body renders into the slots once they exist, on the render after this row
    // mounts.
    const [infoSlot, setInfoSlot] = useState<HTMLElement | null>(null);
    const [actionsSlot, setActionsSlot] = useState<HTMLElement | null>(null);
    const titleSlots = useMemo<SearchTitleSlots>(
        () => ({ info: infoSlot, actions: actionsSlot }),
        [infoSlot, actionsSlot]
    );

    const requested = searchParams.get(SEARCH_TAB_QUERY_PARAM);
    const activeId = providers.some((provider) => provider.id === requested)
        ? (requested as string)
        : defaultProviderId(providers);
    const active = providers.find((provider) => provider.id === activeId);

    if (!active) {
        return (
            <Alert type="warning" header="Search is not available">
                No search provider is enabled for this deployment.
            </Alert>
        );
    }

    const Provider = lazyProvider(active.id);
    const label = providerLabel(active);

    const selectView = (id: string) =>
        setSearchParams(
            (previous) => {
                const next = new URLSearchParams(previous);
                next.set(SEARCH_TAB_QUERY_PARAM, id);
                return next;
            },
            { replace: true }
        );

    return (
        <>
            <Box padding={{ top: "xs", horizontal: "l" }}>
                <Header
                    variant="h1"
                    info={<span ref={setInfoSlot} />}
                    actions={
                        // The slot is `display: contents`, so a body's actions lay out as items of
                        // this row and an empty slot adds no gap before the switch.
                        <div style={titleActionsStyle}>
                            <span ref={setActionsSlot} style={{ display: "contents" }} />
                            {providers.length > 1 && (
                                <SegmentedControl
                                    label={SEARCH_VIEW_SWITCH_LABEL}
                                    selectedId={active.id}
                                    onChange={({ detail }) => selectView(detail.selectedId)}
                                    options={providers.map((provider) => ({
                                        id: provider.id,
                                        text: providerLabel(provider),
                                    }))}
                                />
                            )}
                        </div>
                    }
                >
                    {searchPageTitle(databaseId)}
                </Header>
            </Box>

            {/* Keyed by view, so a failed body does not keep its error after a switch. */}
            <ErrorBoundary key={active.id} componentName={label}>
                <Suspense fallback={<LoadingSpinner text={`Loading ${label}...`} />}>
                    <Provider
                        databaseId={databaseId}
                        isActive={true}
                        assetActionDisabledReasons={assetActionDisabledReasons}
                        titleSlots={titleSlots}
                    />
                </Suspense>
            </ErrorBoundary>
        </>
    );
};

export default SearchTabsHost;
