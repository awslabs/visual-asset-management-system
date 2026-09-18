/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { test, expect, Page } from "@playwright/test";
import {
    collectPageErrors,
    expectSearchRendered,
    gotoSearch,
    modeToolbar,
    readFeaturesEnabled,
    searchModeCase,
    searchQueryBox,
    searchTabs,
    selectSearchMode,
    textContrastRatio,
    SearchModeCase,
} from "./support/fixtures";

/**
 * Unified search — UX coverage of the keyword / natural-language layout.
 *
 * Each check maps to a usability heuristic the search page must keep satisfying: system status is
 * visible (loading, result count, truncation), the interface speaks the user's language
 * (mode-specific placeholder), errors are prevented (an empty natural-language query never leaves
 * the browser), controls are keyboard-operable and named, the layout does not overflow at common
 * desktop widths, and text keeps WCAG AA contrast in both themes.
 *
 * Environment-agnostic: which controls exist is derived from the feature switches the app cached
 * after login (both engines / natural language only / keyword only / none), and any check that needs
 * indexed content skips when a search returns nothing. Nothing here mutates the deployment.
 */

const NLP_PROBE_QUERY = "a part of a machine";

/** Open the unified tab and resolve which engines the deployment advertises; skip when none. */
async function openUnified(page: Page): Promise<SearchModeCase> {
    await gotoSearch(page);
    const features = await readFeaturesEnabled(page);
    test.skip(features === null, "No cached secure-config; the app has not loaded it yet");
    const mode = searchModeCase(features ?? []);
    test.skip(mode === "none", "Neither search engine is enabled in this environment");
    await gotoSearch(page, { tab: "unified-search" });
    return mode;
}

/** Put the container in natural-language mode, whichever way the deployment offers it. */
async function enterNlpMode(page: Page, mode: SearchModeCase): Promise<void> {
    test.skip(mode === "keyword-only", "Vector search is not enabled in this environment");
    if (mode === "both") await selectSearchMode(page, "nlp");
    await expect(searchQueryBox(page)).toHaveAttribute(
        "placeholder",
        /Describe what you are looking for/
    );
}

/**
 * Submit one natural-language query with Enter and return the parsed response plus how many
 * `/search/nlp` requests the submit produced within a settle window that outlasts the container's
 * 500 ms auto-refresh debounce — one submit must be exactly one request.
 */
async function runNlpSearch(page: Page, query: string): Promise<{ body: any; requests: number }> {
    let requests = 0;
    const onRequest = (r: { url(): string; method(): string }) => {
        if (r.url().includes("/search/nlp") && r.method() === "POST") requests++;
    };
    page.on("request", onRequest);
    const responsePromise = page.waitForResponse(
        (r) => r.url().includes("/search/nlp") && r.request().method() === "POST",
        { timeout: 60_000 }
    );
    await searchQueryBox(page).fill(query);
    await searchQueryBox(page).press("Enter");
    const response = await responsePromise;
    const body = await response.json().catch(() => null);
    await page.waitForTimeout(1_200);
    page.off("request", onRequest);
    return { body, requests };
}

test.describe("Unified search — layout and mode controls", () => {
    test("the provider tab strip is keyboard-operable and reports selection", async ({ page }) => {
        const errors = collectPageErrors(page);
        await gotoSearch(page);
        const tabs = searchTabs(page).getByRole("tab");
        const count = await tabs.count();
        expect(count).toBeGreaterThanOrEqual(1);
        for (let i = 0; i < count; i++) {
            await expect(tabs.nth(i)).toHaveAccessibleName(/\S/);
            await expect(tabs.nth(i)).toHaveAttribute("aria-selected", /true|false/);
        }
        test.skip(count < 2, "Only one provider tab in this environment");

        // Arrow keys move the selection along the strip and the hash follows it.
        const selected = searchTabs(page).getByRole("tab", { selected: true });
        const before = await selected.getAttribute("data-testid").catch(() => null);
        await selected.focus();
        await page.keyboard.press("ArrowRight");
        await page.keyboard.press("Enter");
        await expect
            .poll(async () => {
                const now = searchTabs(page).getByRole("tab", { selected: true });
                return (
                    (await now.getAttribute("data-testid").catch(() => null)) !== before ||
                    (await now.innerText()) !== (await selected.innerText().catch(() => ""))
                );
            })
            .toBe(true);
        await expect(page).toHaveURL(/[?&]tab=/);
        await expectSearchRendered(page);
        expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
    });

    test("the mode control follows the engines the deployment advertises", async ({ page }) => {
        const mode = await openUnified(page);
        const toolbar = modeToolbar(page);
        if (mode !== "both") {
            // One engine, nothing to choose: the switch must not be shown, whichever engine it is.
            // The top bar renders the query box and the switch in one pass, so anchoring on the box
            // makes the absence check meaningful rather than a pass on a not-yet-rendered bar.
            await expect(searchQueryBox(page)).toBeVisible();
            await expect(toolbar).toHaveCount(0);
            return;
        }
        await expect(toolbar).toBeVisible();
        const keyword = toolbar.getByRole("button", { name: "Keyword" });
        const natural = toolbar.getByRole("button", { name: "Natural language" });
        await expect(keyword).toHaveCount(1);
        await expect(natural).toHaveCount(1);
        await expect(keyword).toBeEnabled();
        await expect(natural).toBeEnabled();
        await expect(toolbar.getByRole("button", { pressed: true })).toHaveCount(1);
    });

    test("the query box tells the user how to phrase the query in each mode", async ({ page }) => {
        const mode = await openUnified(page);
        const box = searchQueryBox(page);
        if (mode === "keyword-only") {
            await expect(box).toHaveAttribute("placeholder", /Search by keywords/);
            return;
        }
        if (mode === "nlp-only") {
            await expect(box).toHaveAttribute("placeholder", /Describe what you are looking for/);
            return;
        }
        await selectSearchMode(page, "nlp");
        await selectSearchMode(page, "keyword");
        await selectSearchMode(page, "nlp");
    });

    test("the chosen mode survives a reload", async ({ page }) => {
        const mode = await openUnified(page);
        test.skip(mode !== "both", "The mode is only a choice when both engines are enabled");
        await selectSearchMode(page, "nlp");
        // The preference cookie is written 1 s after the change; reloading earlier loses it.
        await expect
            .poll(
                async () =>
                    (
                        await page.context().cookies()
                    ).some(
                        (c) =>
                            c.name === "vams-search-preferences" &&
                            decodeURIComponent(c.value).includes('"searchMode":"nlp"')
                    ),
                { timeout: 5_000 }
            )
            .toBe(true);
        await page.reload({ waitUntil: "domcontentloaded" });
        await expect(modeToolbar(page)).toBeVisible({ timeout: 60_000 });
        await expect(
            modeToolbar(page).getByRole("button", { name: "Natural language" })
        ).toHaveAttribute("aria-pressed", "true");
        await expect(searchQueryBox(page)).toHaveAttribute(
            "placeholder",
            /Describe what you are looking for/
        );
    });

    test("the Table/Map view switch appears only when a map can be shown", async ({ page }) => {
        await openUnified(page);
        const features = (await readFeaturesEnabled(page)) ?? [];
        const mapOffered =
            features.includes("LOCATIONSERVICES") && !features.includes("NOOPENSEARCH");
        const viewSwitch = page.getByRole("toolbar", { name: "Result view" });
        await expect(viewSwitch).toHaveCount(mapOffered ? 1 : 0);
        if (mapOffered) {
            await expect(viewSwitch.getByRole("button", { name: "Table" })).toBeVisible();
            await expect(viewSwitch.getByRole("button", { name: "Map" })).toBeVisible();
        }
    });

    test("the natural-language sidebar offers the segment toggle and, without OpenSearch, only the filters it can honour", async ({
        page,
    }) => {
        const mode = await openUnified(page);
        await enterNlpMode(page, mode);
        const segments = page.getByRole("checkbox", { name: "Search inside files" });
        await expect(segments).toHaveCount(1);
        await expect(segments).toBeChecked();
        if (mode === "nlp-only") {
            await expect(
                page.getByText(/Natural-language search matches on meaning/)
            ).toBeVisible();
            // OpenSearch-only panels are absent rather than shown and inert.
            await expect(page.getByRole("button", { name: "Metadata Search" })).toHaveCount(0);
            await expect(page.getByRole("button", { name: "Geospatial filter" })).toHaveCount(0);
        }
    });
});

test.describe("Unified search — natural-language behaviour", () => {
    test("an empty natural-language query sends no request and shows the empty state", async ({
        page,
    }) => {
        const mode = await openUnified(page);
        await enterNlpMode(page, mode);
        let requests = 0;
        page.on("request", (r) => {
            if (r.url().includes("/search/nlp")) requests++;
        });
        await searchQueryBox(page).fill("");
        await searchQueryBox(page).press("Enter");
        await page.getByRole("button", { name: "Search", exact: true }).click();
        await expect(page.getByText("No matches")).toBeVisible({ timeout: 30_000 });
        expect(requests, "an empty query has nothing to embed and must not be sent").toBe(0);
    });

    test("a search reports progress, then ranks by relevance with an accessible explanation", async ({
        page,
    }) => {
        const mode = await openUnified(page);
        await enterNlpMode(page, mode);
        const searchButton = page.getByRole("button", { name: "Search", exact: true });

        const { body, requests } = await runNlpSearch(page, NLP_PROBE_QUERY);
        // One submit is one request: a mode switch just before it must not add a second through
        // the auto-refresh debounce (each natural-language request embeds the query).
        expect(requests, "requests issued by one Enter").toBe(1);
        // The button reports the in-flight search and comes back when the response lands.
        await expect(searchButton).toBeEnabled({ timeout: 60_000 });
        await expect(searchButton).not.toHaveAttribute("aria-disabled", "true");

        const hits: any[] = body?.hits?.hits ?? [];
        test.skip(hits.length === 0, "No vector-indexed content in this environment");

        // Result count badge names the full set; the table shows at most one page of it.
        const badge = page.getByText(/^\d[\d,]* results$/);
        await expect(badge).toBeVisible();
        const badgeCount = Number((await badge.innerText()).replace(/[^\d]/g, ""));
        expect(badgeCount).toBe(hits.length);
        const rows = page.getByRole("row").filter({ has: page.getByRole("cell") });
        await expect.poll(() => rows.count()).toBeGreaterThan(0);
        expect(await rows.count()).toBeLessThanOrEqual(hits.length);

        // Relevance is the first data column and reads as a percentage.
        const headers = (await page.getByRole("columnheader").allInnerTexts()).map((t) => t.trim());
        const firstNamed = headers.find((t) => t.length > 0);
        expect(firstNamed).toBe("Relevance");
        const relevanceIndex = headers.indexOf("Relevance");
        const firstRelevance = rows.first().getByRole("cell").nth(relevanceIndex);
        await expect(firstRelevance).toHaveText(/^\d{1,3}%/);

        // Every explanation trigger is a named, focusable control that opens on Enter.
        const triggers = page.getByRole("button", { name: "Show what this result matched from" });
        const anyVector = hits.some(
            (h) => (h._vector?.sourceModalities ?? []).length > 0 || h._vector?.segmentHits > 0
        );
        if (anyVector) {
            await expect(triggers.first()).toBeVisible();
            await triggers.first().focus();
            await page.keyboard.press("Enter");
            await expect(page.getByText("Matched from", { exact: true })).toBeVisible();
            await page.keyboard.press("Escape");
        } else {
            await expect(triggers).toHaveCount(0);
        }
    });

    test("the truncation notice and warning toast appear exactly when the response says so", async ({
        page,
    }) => {
        const mode = await openUnified(page);
        await enterNlpMode(page, mode);
        const { body } = await runNlpSearch(page, NLP_PROBE_QUERY);
        test.skip(!body?.hits, "No natural-language response body to compare against");
        const notice = page.getByText(/^Showing the top \d+ semantic matches; more may exist\.$/);
        await expect(notice).toHaveCount(body.nlp?.truncated ? 1 : 0);
        const toast = page.getByText("Search notice");
        const warnings: any[] = body.warnings ?? [];
        await expect(toast).toHaveCount(warnings.length > 0 ? 1 : 0);
    });

    test("switching the record type keeps natural-language mode and re-queries", async ({
        page,
    }) => {
        const mode = await openUnified(page);
        await enterNlpMode(page, mode);
        const { body: first } = await runNlpSearch(page, NLP_PROBE_QUERY);
        test.skip(!first?.hits, "No natural-language response body");
        const responsePromise = page.waitForResponse(
            (r) => r.url().includes("/search/nlp") && r.request().method() === "POST",
            { timeout: 60_000 }
        );
        await page.getByRole("button", { name: "Files", exact: true }).click();
        const response = await responsePromise;
        const sent = response.request().postDataJSON();
        expect(sent.entityTypes).toEqual(["file"]);
        await expect(searchQueryBox(page)).toHaveAttribute(
            "placeholder",
            /Describe what you are looking for/
        );
        const headers = (await page.getByRole("columnheader").allInnerTexts()).map((t) => t.trim());
        expect(headers.find((t) => t.length > 0)).toBe("Relevance");
    });

    test("a database-locked route pins the database in the outgoing request", async ({ page }) => {
        const mode = await openUnified(page);
        test.skip(mode === "keyword-only", "Vector search is not enabled in this environment");
        // Derive a database from whatever the environment holds rather than a fixture: the first
        // hit of a probe search names the database it lives in.
        await enterNlpMode(page, mode);
        const { body: probe } = await runNlpSearch(page, NLP_PROBE_QUERY);
        const databaseId: string = probe?.hits?.hits?.[0]?._source?.str_databaseid ?? "";
        test.skip(!databaseId, "No natural-language hit to take a database from");

        await gotoSearch(page, { databaseId, tab: "unified-search" });
        await enterNlpMode(page, mode);
        const responsePromise = page.waitForResponse(
            (r) => r.url().includes("/search/nlp") && r.request().method() === "POST",
            { timeout: 60_000 }
        );
        await searchQueryBox(page).fill(NLP_PROBE_QUERY);
        await searchQueryBox(page).press("Enter");
        const sent = (await responsePromise).request().postDataJSON();
        expect(sent.databaseIds).toEqual([databaseId]);
    });
});

test.describe("Unified search — accessibility and layout", () => {
    test("every control in the search panel has an accessible name", async ({ page }) => {
        const mode = await openUnified(page);
        if (mode !== "keyword-only") await enterNlpMode(page, mode);
        // Open a filter dropdown so its filtering input (hidden while closed) is audited too.
        const databaseFilter = page.getByRole("button", { name: /^All databases/i }).first();
        if (await databaseFilter.count()) {
            await databaseFilter.click();
            await expect(page.getByRole("listbox").first()).toBeVisible();
        }
        const panel = page.getByRole("tabpanel").first();
        const unnamed: string[] = await panel.evaluate((root) => {
            const roles = new Set([
                "button",
                "tab",
                "textbox",
                "searchbox",
                "combobox",
                "checkbox",
                "radio",
                "link",
                "toolbar",
            ]);
            const implicit: Record<string, string> = {
                BUTTON: "button",
                A: "link",
                SELECT: "combobox",
                TEXTAREA: "textbox",
            };
            const nameOf = (el: Element): string => {
                const byId = (ids: string | null) =>
                    (ids || "")
                        .split(/\s+/)
                        .filter(Boolean)
                        .map((id) => document.getElementById(id)?.textContent?.trim() || "")
                        .join(" ")
                        .trim();
                const labelled = byId(el.getAttribute("aria-labelledby"));
                if (labelled) return labelled;
                const aria = (el.getAttribute("aria-label") || "").trim();
                if (aria) return aria;
                if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
                    const forLabel = el.id
                        ? document.querySelector(`label[for="${el.id}"]`)?.textContent?.trim()
                        : "";
                    if (forLabel) return forLabel;
                    const wrap = el.closest("label")?.textContent?.trim();
                    if (wrap) return wrap;
                    if (el.placeholder.trim()) return el.placeholder.trim();
                }
                const title = (el.getAttribute("title") || "").trim();
                if (title) return title;
                return (el.textContent || "").trim();
            };
            const out: string[] = [];
            root.querySelectorAll("*").forEach((el) => {
                const role = el.getAttribute("role") || implicit[el.tagName] || "";
                if (el.tagName === "INPUT") {
                    const t = (el as HTMLInputElement).type;
                    if (t === "hidden") return;
                    if (!role && !["checkbox", "radio", "search", "text"].includes(t)) return;
                }
                if (!roles.has(role) && el.tagName !== "INPUT") return;
                if (el.getAttribute("aria-hidden") === "true") return;
                if ((el as HTMLElement).offsetParent === null) return;
                if (!nameOf(el)) {
                    out.push(
                        `${el.tagName.toLowerCase()}[role=${role}] ${(el.className || "")
                            .toString()
                            .split(" ")
                            .slice(0, 2)
                            .join(" ")}`
                    );
                }
            });
            return out;
        });
        expect(unnamed, `controls without an accessible name:\n${unnamed.join("\n")}`).toEqual([]);
    });

    test("the query box, Search button and mode switch are reachable by keyboard", async ({
        page,
    }) => {
        const mode = await openUnified(page);
        await searchQueryBox(page).focus();
        await page.keyboard.press("Tab");
        await expect(page.getByRole("button", { name: "Search", exact: true })).toBeFocused();
        if (mode === "both") {
            await searchQueryBox(page).focus();
            await page.keyboard.press("Shift+Tab");
            const active = await page.evaluate(() => document.activeElement?.textContent?.trim());
            expect(["Keyword", "Natural language"]).toContain(active);
        }
    });

    for (const width of [1440, 1280, 1024]) {
        test(`the header controls stay visible without horizontal overflow at ${width}px`, async ({
            page,
        }) => {
            await page.setViewportSize({ width, height: 900 });
            const mode = await openUnified(page);
            const overflow = await page.evaluate(
                () => document.documentElement.scrollWidth - document.documentElement.clientWidth
            );
            expect(overflow, "document wider than the viewport").toBeLessThanOrEqual(0);
            await expect(searchQueryBox(page)).toBeInViewport();
            await expect(
                page.getByRole("button", { name: "Search", exact: true })
            ).toBeInViewport();
            if (mode === "both") await expect(modeToolbar(page)).toBeInViewport();
        });
    }

    for (const theme of ["dark", "light"] as const) {
        test(`mode switch and relevance text meet WCAG AA contrast in the ${theme} theme`, async ({
            page,
        }) => {
            await page.addInitScript((t) => {
                window.localStorage.setItem("vams-theme-preference", t);
            }, theme);
            const mode = await openUnified(page);
            const h1 = page.getByRole("heading", { level: 1 }).first();
            expect(await textContrastRatio(h1)).toBeGreaterThanOrEqual(4.5);
            if (mode !== "keyword-only") {
                if (mode === "both") {
                    const toolbar = modeToolbar(page);
                    for (const name of ["Keyword", "Natural language"]) {
                        const ratio = await textContrastRatio(
                            toolbar.getByRole("button", { name })
                        );
                        expect(ratio, `${name} segment in ${theme}`).toBeGreaterThanOrEqual(4.5);
                    }
                }
                await enterNlpMode(page, mode);
                const { body } = await runNlpSearch(page, NLP_PROBE_QUERY);
                if ((body?.hits?.hits ?? []).length > 0) {
                    const headers = (await page.getByRole("columnheader").allInnerTexts()).map(
                        (t) => t.trim()
                    );
                    const cell = page
                        .getByRole("row")
                        .filter({ has: page.getByRole("cell") })
                        .first()
                        .getByRole("cell")
                        .nth(headers.indexOf("Relevance"))
                        .locator("span")
                        .first();
                    expect(
                        await textContrastRatio(cell),
                        `relevance in ${theme}`
                    ).toBeGreaterThanOrEqual(4.5);
                }
            }
        });
    }
});
