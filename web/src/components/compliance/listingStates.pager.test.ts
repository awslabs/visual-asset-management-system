/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A token-paged listing's pager follows the end of the walk, not the page shown: once the walk has
 * reached its last page, stepping back to an earlier page offers no page past the last one, and a
 * page without a token is not loaded as if it were the first page.
 */

import { pageReachable, tokenPagerCounts } from "./listingStates";

describe("tokenPagerCounts", () => {
    it("offers the next page while the furthest page loaded reported a NextToken", () => {
        expect(tokenPagerCounts({ 1: "t1" }, 1)).toEqual({ pagesCount: 2, openEnd: true });
    });

    it("pages 1, last, 1: offers no page past the last one", () => {
        // Page 1 gave t1, page 2 (the last) gave nothing, then page 1 is shown again.
        expect(tokenPagerCounts({ 1: "t1" }, 2)).toEqual({ pagesCount: 2, openEnd: false });
    });

    it("a fresh walk shows one page", () => {
        expect(tokenPagerCounts({}, 1)).toEqual({ pagesCount: 1, openEnd: false });
    });
});

describe("pageReachable", () => {
    it("reaches the first page and every page the walk holds a token for", () => {
        expect(pageReachable({ 1: "t1" }, 1)).toBe(true);
        expect(pageReachable({ 1: "t1" }, 2)).toBe(true);
    });

    it("does not reach the page after the last", () => {
        expect(pageReachable({ 1: "t1" }, 3)).toBe(false);
    });
});
