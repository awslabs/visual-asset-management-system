/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What a compliance listing's table says when it shows no row. The listings are filtered on the
 * server and by the reader's permissions page by page, so an empty page can mean that nothing
 * exists, that nothing matches the filters, that the rows read for the page are not the reader's,
 * or that the walk has ended after pages that listed rows; and a page that failed to load is none
 * of these (`ListingLoadFailed`).
 */

import React from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";

/** Where an empty page sits in the listing's walk. */
export interface ListingWalk {
    /** The page that loaded reported a NextToken: rows may follow on later pages. */
    hasMore: boolean;
    /** The page shown, 1-based. */
    pageIndex: number;
    /** An earlier page of this walk listed at least one row. */
    listedEarlier: boolean;
    /** A filter of the listing is set. */
    filtered: boolean;
}

/**
 * Whether a page before `pageIndex` (1-based) listed a row. `listed` maps each loaded page
 * (0-based, as the pages are fetched) to whether it listed one.
 */
export const listedBefore = (listed: Record<number, boolean>, pageIndex: number): boolean =>
    Object.entries(listed).some(([page, any]) => any && Number(page) < pageIndex - 1);

/**
 * The text of an empty page. `rows` names the listing's rows in the plural ("audit entries");
 * `nothing` is the empty first page of a listing without filters ("No compliance audit events
 * have been recorded.").
 */
export const listingEmptyText = (walk: ListingWalk, rows: string, nothing: string): string => {
    if (walk.hasMore) {
        return walk.filtered
            ? `The ${rows} read for this page do not match the filters or are not visible to you. Open the next page to continue.`
            : `None of the ${rows} read for this page are visible to you. Open the next page to continue.`;
    }
    if (walk.pageIndex > 1 && walk.listedEarlier) {
        return walk.filtered
            ? `No more ${rows} match the filters. The earlier pages list all of them.`
            : `No more ${rows}. The earlier pages list all of them.`;
    }
    if (walk.filtered) {
        return `No ${rows} match the filters.`;
    }
    // A later page of a walk whose earlier pages listed nothing: rows exist (the server read them)
    // but none was the reader's.
    return walk.pageIndex > 1 ? `No ${rows} are visible to you.` : nothing;
};

/**
 * The Cloudscape Pagination counts of a token-paged walk. `tokens[i]` is the startingToken of page
 * i (0-based; page 0 has none) and `loadedPages` how many pages the walk has fetched. The pager
 * offers a page past the furthest one loaded only while that page reported a NextToken; the page
 * shown does not decide it, so stepping back after the walk ended offers no page beyond its end.
 */
export const tokenPagerCounts = (
    tokens: Record<number, string | undefined>,
    loadedPages: number
): { pagesCount: number; openEnd: boolean } => {
    const more = tokens[loadedPages] !== undefined;
    return { pagesCount: more ? loadedPages + 1 : loadedPages, openEnd: more };
};

/**
 * Whether page `pageIndex` (1-based) can be loaded: the first page, or a page the walk holds a
 * token for. A later page without one would load the first page again under its number.
 */
export const pageReachable = (
    tokens: Record<number, string | undefined>,
    pageIndex: number
): boolean => pageIndex === 1 || tokens[pageIndex - 1] !== undefined;

/**
 * The flash of a listing whose load failed: what could not be loaded, then the reason the service
 * gave ("Could not load the audit log: The request could not reach the service. ...").
 */
export const loadFailureMessage = (what: string, reason: unknown): string => {
    const text = typeof reason === "string" ? reason.trim() : "";
    return text ? `Could not load ${what}: ${text}` : `Could not load ${what}.`;
};

/**
 * The table body of a listing whose load failed, in place of its empty state: the list could not
 * be loaded, and how to try again, with the refresh button of the table's header or, for a table
 * without one, the Retry button shown here (`onRetry`). A table showing it shows no counter.
 */
export const ListingLoadFailed: React.FC<{
    /** What could not be loaded, as the sentence names it ("The audit log"). */
    what: string;
    onRetry?: () => void;
}> = ({ what, onRetry }) => (
    <Box textAlign="center" padding="l">
        <b>{what} could not be loaded</b>
        {onRetry ? (
            <Box padding={{ top: "s" }}>
                <Button onClick={onRetry}>Retry</Button>
            </Box>
        ) : (
            <Box variant="p" color="inherit">
                Use the refresh button to try again.
            </Box>
        )}
    </Box>
);
