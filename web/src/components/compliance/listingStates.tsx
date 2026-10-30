/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What a compliance listing's table says when it shows no row. The listings are filtered on the
 * server and by the reader's permissions page by page, so an empty page can mean that nothing
 * exists, that nothing matches the filters, that the rows read for the page are not the reader's,
 * or that the walk has ended after pages that listed rows.
 */

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
