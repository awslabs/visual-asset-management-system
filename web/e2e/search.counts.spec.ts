/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec for search totals and facet counts.
 *
 * Totals and aggregation buckets count only records the signed-in user can open. An exact total
 * (`hits.total.relation` "eq") is shown as is; a minimum ("gte"), which some permission rules
 * produce, is shown with "+". A natural-language total above zero is always a minimum, because the
 * vector index matches approximately; that case skips where vector search is off. Run it once per
 * role under test: the assertions hold for any role, and a constrained role is where a count over
 * records the user cannot open would show.
 *
 * Non-mutating (Rule 2): it searches and reads. Data-independent (Rule 1): every expectation is
 * derived from what the search returns, and a check that needs records skips when there are none.
 */

import { test, expect, Page } from "@playwright/test";
import {
    apiContext,
    collectPageErrors,
    gotoSearch,
    readFeaturesEnabled,
    searchCountBadge,
    searchCountBadgeText,
    searchModeCase,
    searchQueryBox,
    selectSearchMode,
} from "./support/fixtures";

const PAGE_SIZE = 100;
/** A description most indexed content matches somewhat; any answer, zero included, is checked. */
const NLP_QUERY = "a part of a machine";

interface Bucket {
    key: string;
    doc_count: number;
}

interface SearchAnswer {
    hits: { hits: any[]; total: { value: number; relation?: string } };
    aggregations?: Record<string, { buckets?: Bucket[] }>;
    aggregationTotal?: number | null;
}

async function search(page: Page, body: Record<string, unknown>): Promise<SearchAnswer | null> {
    const ctx = await apiContext(page);
    if (!ctx) return null;
    const response = await page.request.post(`${ctx.base}/search`, {
        headers: { Authorization: `Bearer ${ctx.token}`, "Content-Type": "application/json" },
        data: body,
    });
    expect(response.status(), await response.text()).toBe(200);
    return (await response.json()) as SearchAnswer;
}

function bucketCounts(answer: SearchAnswer, name: string): Map<string, number> {
    const counts = new Map<string, number>();
    for (const bucket of answer.aggregations?.[name]?.buckets ?? []) {
        counts.set(String(bucket.key), bucket.doc_count);
    }
    return counts;
}

function countsFromHits(hits: any[], field: string): Map<string, number> {
    const counts = new Map<string, number>();
    for (const hit of hits) {
        const raw = hit._source?.[field];
        const values = Array.isArray(raw) ? raw : raw === undefined || raw === null ? [] : [raw];
        for (const value of values) counts.set(String(value), (counts.get(String(value)) ?? 0) + 1);
    }
    return counts;
}

test.describe("Search counts", () => {
    test("asset totals and facets count only listed records", async ({ page }) => {
        const answer = await search(page, {
            aggregations: true,
            entityTypes: ["asset"],
            from: 0,
            size: PAGE_SIZE,
        });
        test.skip(!answer, "No API context in this environment");
        const { total } = answer!.hits;
        expect(["eq", "gte"]).toContain(total.relation);

        if (answer!.aggregationTotal != null) {
            expect(answer!.aggregationTotal).toBeLessThanOrEqual(total.value);
        }
        const typeBuckets = bucketCounts(answer!, "str_assettype");
        const bucketSum = [...typeBuckets.values()].reduce((a, b) => a + b, 0);
        expect(bucketSum).toBeLessThanOrEqual(total.value);

        test.skip(total.value === 0, "No assets this user can open");
        test.skip(
            total.relation !== "eq" || total.value > PAGE_SIZE,
            "Total is a minimum or spans several pages; the bucket-by-bucket check needs one exact page"
        );
        const hits = answer!.hits.hits;
        expect(hits.length).toBe(total.value);
        expect(typeBuckets).toEqual(countsFromHits(hits, "str_assettype"));
        expect(bucketCounts(answer!, "list_tags")).toEqual(countsFromHits(hits, "list_tags"));
    });

    test("every counted asset on the first page can be opened", async ({ page }) => {
        const answer = await search(page, { entityTypes: ["asset"], from: 0, size: 10 });
        test.skip(!answer || answer.hits.hits.length === 0, "No assets this user can open");
        const ctx = await apiContext(page);
        for (const hit of answer!.hits.hits.slice(0, 5)) {
            const databaseId = hit._source.str_databaseid;
            const assetId = hit._source.str_assetid;
            const response = await page.request.get(
                `${ctx!.base}/database/${encodeURIComponent(
                    databaseId
                )}/assets/${encodeURIComponent(assetId)}`,
                { headers: { Authorization: `Bearer ${ctx!.token}` } }
            );
            expect(response.status(), `${databaseId}/${assetId}`).toBe(200);
        }
    });

    test("the search page shows the same total, with + for a minimum", async ({ page }) => {
        const errors = collectPageErrors(page);
        const answer = await search(page, {
            aggregations: true,
            entityTypes: ["asset"],
            from: 0,
            size: PAGE_SIZE,
        });
        test.skip(!answer, "No API context in this environment");

        const pageAnswer = page.waitForResponse(
            (r) => r.request().method() === "POST" && /\/search$/.test(new URL(r.url()).pathname)
        );
        await page.goto("/#/search");
        const shown = await (await pageAnswer).json();
        const total = Math.max(shown.aggregationTotal ?? 0, shown.hits?.total?.value ?? 0);
        test.skip(total === 0, "No search results for this user");

        // The page's own wording: "+" only for a minimum, and "1 result" for exactly one. Matched
        // exactly, so "1 result" cannot pass on a page that reads "1 results" (or "11 results").
        const expected = searchCountBadgeText({
            value: total,
            relation: shown.hits?.total?.relation,
        });
        await expect(page.getByText(expected, { exact: true }).first()).toBeVisible({
            timeout: 30000,
        });
        expect(errors).toEqual([]);
    });

    test("a natural-language total shows with + unless it is an exact zero", async ({ page }) => {
        const errors = collectPageErrors(page);
        await gotoSearch(page);
        const features = await readFeaturesEnabled(page);
        test.skip(features === null, "No cached secure-config; the app has not loaded it yet");
        const engines = searchModeCase(features ?? []);
        test.skip(
            engines !== "both" && engines !== "nlp-only",
            "Vector search (VECTORSEARCH) is not enabled in this environment"
        );
        await gotoSearch(page, { tab: "unified-search" });
        if (engines === "both") await selectSearchMode(page, "nlp");

        const pageAnswer = page.waitForResponse(
            (r) =>
                r.request().method() === "POST" && /\/search\/nlp$/.test(new URL(r.url()).pathname),
            { timeout: 60_000 }
        );
        await searchQueryBox(page).fill(NLP_QUERY);
        await searchQueryBox(page).press("Enter");
        const response = await pageAnswer;
        test.skip(response.status() === 503, "The vector index is being built");
        expect(response.status(), await response.text()).toBe(200);
        const answer = (await response.json()) as SearchAnswer;

        // The page counts what the route counted, never fewer than the hits it returned.
        const { total } = answer.hits;
        const value = Math.max(total.value ?? 0, answer.hits.hits.length);
        expect(["eq", "gte"]).toContain(total.relation);
        // Matching is approximate, so the route reports every total above zero as a minimum and
        // the badge carries a "+"; only an exact zero reads "0 results".
        if (value > 0) expect(total.relation).toBe("gte");
        const expected = searchCountBadgeText({ value, relation: total.relation });
        test.info().annotations.push({ type: "natural-language total", description: expected });
        if (value > 0) expect(expected).toBe(`${value.toLocaleString()}+ results`);
        if (value === 0 && total.relation === "eq") expect(expected).toBe("0 results");
        await expect(searchCountBadge(page)).toHaveText(expected, { timeout: 30_000 });
        expect(errors).toEqual([]);
    });
});
