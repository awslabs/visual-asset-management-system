/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect, Locator, Page } from "@playwright/test";

/**
 * Reusable Playwright harness for the VAMS orchestration UI.
 *
 * Environment-agnostic by design: nothing here assumes a particular pipeline, workflow, or
 * execution exists. Helpers either work against whatever the environment happens to contain, or
 * skip the test when a precondition is genuinely absent. That makes the specs built on this
 * harness safe to run against any sandbox — empty, freshly seeded, or long-lived.
 *
 * The selector helpers are the durable value here: they encode where the app's controls actually
 * live, which is what breaks when markup changes.
 */

/** Orchestration pages are scoped under `.orchestration-root`; the global nav also has a Search. */
export function orchestrationRoot(page: Page): Locator {
    return page.locator(".orchestration-root");
}

/** The orchestration filter-bar search input. Labelled (no placeholder) and NOT the global search. */
export function searchBox(page: Page): Locator {
    return orchestrationRoot(page).getByLabel("Search", { exact: true }).first();
}

/** A native <select> facet, e.g. "Status", "Execution Type", "Database", "Group by". */
export function facet(page: Page, label: string): Locator {
    return page.getByLabel(label, { exact: true });
}

/** Rows currently rendered in the page's data table. */
export function tableRows(page: Page): Locator {
    return page.locator("table tbody tr");
}

/** The execute dialog's step rail — one navigation landmark, present on every step of the dialog. */
export function wizardRail(page: Page): Locator {
    return page.getByRole("navigation", { name: "Execution steps" });
}

/**
 * Navigate to an orchestration page and wait for it to finish its first load. Waits on the page's
 * own heading — never on specific data — so an empty environment is a valid state.
 */
export async function gotoOrchestration(
    page: Page,
    route: "pipelines" | "workflows" | "executions",
    heading: RegExp | string
): Promise<void> {
    await page.goto(`/#/${route}/`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible({
        timeout: 60_000,
    });
    // The list issues its first request after mount; settle before asserting on content.
    await page.waitForLoadState("networkidle").catch(() => undefined);
}

/**
 * The id of the first card in a card list (pipelines / workflows), or null when the environment has
 * none. Cards expose an "Actions for {id}" button, which is the stable way to read their identity.
 */
export async function firstCardId(page: Page): Promise<string | null> {
    const actions = page.getByRole("button", { name: /Actions for/i }).first();
    if ((await actions.count()) === 0) return null;
    const label = (await actions.getAttribute("aria-label")) || "";
    const m = label.match(/Actions for\s+(.+)$/i);
    return m ? m[1].trim() : null;
}

/**
 * Open a card's actions menu, filtering the list down to it first so a server-paginated list
 * (pageSize 50) does not hide the target. Returns the menu items locator.
 */
export async function openCardMenu(page: Page, cardId: string): Promise<Locator> {
    await searchBox(page).fill(cardId);
    await expect(page.getByText(cardId, { exact: false }).first()).toBeVisible({ timeout: 45_000 });
    const card = page
        .locator("div", { hasText: cardId })
        .filter({ has: page.getByRole("button", { name: /Actions for/i }) })
        .last();
    await card.scrollIntoViewIfNeeded();
    await card.getByRole("button", { name: /Actions for/i }).click();
    return page.getByRole("menuitem");
}

/**
 * The floating surface a menu item belongs to, reached from the item rather than from `[role="menu"]`.
 *
 * A page carries a dozen or more zero-size Cloudscape `<ul role="menu">` option lists (every closed
 * Select/ButtonDropdown keeps one in the DOM), and they precede the portalled Radix menu in document
 * order — so `page.locator('[role="menu"]').first()` resolves to a hidden one with a transparent
 * background. Walking up from a visible item picks the surface that is actually open.
 */
export function menuSurface(items: Locator): Locator {
    return items.first().locator('xpath=ancestor::*[@role="menu"][1]');
}

/**
 * The value cell of a label/value row in a detail or quick-view panel. Rows render the label and its
 * value as adjacent spans, so the value is the label's next sibling — scoping to it keeps an assertion
 * about one field from matching text anywhere else on the page.
 */
export function rowValue(page: Page, label: string): Locator {
    return page.getByText(label, { exact: true }).first().locator("xpath=following-sibling::*[1]");
}

/** Collect uncaught page errors for the duration of a test (crash-regression assertions). */
export function collectPageErrors(page: Page): string[] {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    return errors;
}

/**
 * Assert a table either has rows or shows its empty state — never that specific data exists. Use
 * this to prove a page renders in ANY environment.
 */
export async function expectTableRendered(page: Page): Promise<number> {
    const rows = tableRows(page);
    await expect
        .poll(
            async () =>
                (await rows.count()) > 0 || (await page.getByText(/no .*found/i).count()) > 0,
            {
                timeout: 60_000,
            }
        )
        .toBe(true);
    return rows.count();
}

// ---------------------------------------------------------------------------------------------
// Asset file viewer
// ---------------------------------------------------------------------------------------------

/**
 * Click a locator, retrying when the element is present but not yet actionable.
 *
 * The asset page re-renders while its tree and detail panel settle, so a click can find its target and
 * still fail the actionability checks — Playwright reports `locator.click: Timeout 20000ms exceeded`
 * with no hint that the element was there all along. That produced intermittent failures in the viewer
 * specs on roughly one run in three, on a different case each time.
 *
 * Retrying is correct here rather than papering over a defect: the target is proven present by the
 * caller's own visibility assertion before this is reached.
 */
async function clickWhenActionable(target: Locator, what: string, attempts = 4): Promise<void> {
    let last: unknown;
    for (let attempt = 0; attempt < attempts; attempt++) {
        try {
            await target.scrollIntoViewIfNeeded().catch(() => undefined);
            await target.click({ timeout: 8_000 });
            return;
        } catch (err) {
            last = err;
            await target.page().waitForTimeout(1_500);
        }
    }
    throw new Error(
        `could not click ${what} after ${attempts} attempts: ${String(last).slice(0, 200)}`
    );
}

/**
 * Open one of an asset's files in the File Visualizer, the way a user does.
 *
 * The File Manager navigates to `.../assets/{id}/file` and passes the file through React Router
 * STATE; a direct `.../file/<encoded key>` link also opens a file, but this helper takes the path a
 * user takes. Three things about that page are easy to get wrong and cost a long detour each:
 *
 *  - Its own heading is an **h2**. The orchestration routes have an h1; this page does not, so waiting
 *    on `heading, level: 1` matches nothing and times out.
 *  - Files are **tree nodes, not table rows**, and need an EXACT text match — the panel repeats file
 *    names in its summary text, so a substring match resolves to a container.
 *  - **View File is hidden when the asset is not distributable.** A non-distributable asset therefore
 *    looks like a missing button rather than a permissions/flag condition.
 *
 * A render crash is also reported distinctly. The page's error boundary keeps the shell alive, so a
 * crash's only symptom is the heading never arriving — which otherwise reads as "the file is not
 * listed" and blames the data.
 */
export async function openAssetFile(
    page: Page,
    databaseId: string,
    assetId: string,
    filename: string
): Promise<void> {
    await page.goto(`/#/databases/${databaseId}/assets/${assetId}`, {
        waitUntil: "domcontentloaded",
    });
    try {
        await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible({
            timeout: 60_000,
        });
    } catch (err) {
        if (await page.getByText(/Something went wrong on this page/i).count()) {
            throw new Error(
                `the asset detail page hit its error boundary while opening ${filename}. The shell ` +
                    `survived, but a component threw during render — React error boundaries do not ` +
                    `surface as 'pageerror', so check the console transcript and network log.`
            );
        }
        throw err;
    }
    await page.waitForLoadState("networkidle").catch(() => undefined);

    const tab = page.getByRole("tab", { name: /file manager/i }).first();
    if ((await tab.count()) && (await tab.getAttribute("aria-selected")) !== "true") {
        await tab.click();
    }

    // Wait for the tree itself, not just the network. `networkidle` resolves before the File Manager
    // has rendered its nodes, so clicking straight after it races an empty tree — which surfaces as
    // "<file> is not listed" and looks like missing data.
    await expect
        .poll(async () => await page.getByText(/Hold Ctrl or Shift to select/i).count(), {
            timeout: 60_000,
        })
        .toBeGreaterThan(0);

    // A file inside a folder is not visible until its folder is expanded, so walk the path segments and
    // click each one. Passing "tileset/tileset.json" as a single label matches nothing.
    const segments = filename.split("/").filter(Boolean);
    for (const segment of segments) {
        const node = treeNode(page, segment);
        await expect(node, `${segment} is not listed on ${databaseId}/${assetId}`).toBeVisible({
            timeout: 60_000,
        });
        await clickWhenActionable(node, `tree node "${segment}"`);
        if (segment !== segments[segments.length - 1]) {
            // Give the tree a moment to render the newly revealed children.
            await page.waitForTimeout(1200);
        }
    }

    const view = page.getByRole("button", { name: /view file/i }).first();
    await expect(view, "View File is absent — is the asset distributable?").toBeVisible({
        timeout: 30_000,
    });
    await clickWhenActionable(view, "the View File button");
    await page.waitForLoadState("networkidle").catch(() => undefined);
}

/**
 * A row in the asset File Manager's file tree, by name.
 *
 * Two facts make a plain `getByText(name, { exact: true })` wrong here, and both fail in ways that do
 * not look like selector problems:
 *
 *  1. **The page mounts TWO trees.** The visible one is `.directory-tree` (rows `.tree-item-name`); a
 *     second, folder-only `.folder-tree-view` (rows `.folder-tree-item-name`) is mounted for the
 *     move/copy destination picker and sits inside a `display:none` ancestor. Its folder labels ARE
 *     exact matches, so `getByText` resolves to the hidden copy and the assertion fails as "not
 *     visible" — reading like absent data rather than the wrong element. The give-away is a rect of
 *     0×0 at x=0,y=0, which is what `getBoundingClientRect` returns inside `display:none`.
 *  2. **A folder row's label includes its child count** — the node for `3DTiles` reads `3DTiles(2)`.
 *     So exact matching cannot find a folder in the visible tree at all, whatever the scope.
 *
 * Scoping to `.directory-tree` and allowing the optional `(n)` suffix handles files and folders alike.
 */
export function treeNode(page: Page, name: string): Locator {
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // e2e test helper: the tree-node name is escaped with the standard metacharacter escape before
    // it is interpolated into the RegExp below.
    return page
        .locator(".directory-tree .tree-item-name")
        .filter({ hasText: new RegExp(`^${escaped}(\\(\\d+\\))?$`) }) // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp
        .first();
}

/**
 * Choose a viewer in the File Visualizer's picker, by KEYBOARD.
 *
 * When more than one viewer claims a file's extension the page deliberately selects none and shows
 * "Select viewer (required)" / "No Viewer Component Selected". That is correct behaviour, but it means
 * any assertion about such a file's rendered content has to make the selection first — and `.csv`,
 * `.json`, `.glb`, `.stp` and `.sog` all match two or more viewers.
 *
 * The selection mechanics live in `chooseSelectOption`; what is specific here is the confirmation. A
 * selection that silently failed would leave the page on its placeholder, and the caller's content
 * assertion would then fail for the wrong reason — blaming the viewer for a picker problem.
 */
export async function chooseViewer(page: Page, name: RegExp): Promise<void> {
    await chooseSelectOption(page, page.getByLabel(/select viewer/i), name, "the viewer picker");
    await expect
        .poll(async () => await page.getByText(/No Viewer Component Selected/i).count(), {
            timeout: 30_000,
        })
        .toBe(0);
}

/**
 * Choose an option in a Cloudscape Select, by KEYBOARD.
 *
 * The trigger is passed as a LOCATOR rather than a label, because these controls are not labelled
 * consistently: the viewer picker is reachable by label, while the File Manager's version selector is
 * only reachable by its button role/name. Requiring one lookup strategy would exclude the other.
 *
 * Mouse routes into these controls do not work. Recorded so they are not retried: a normal
 * `option.click()` races the dropdown's entry animation and its option-list re-render ("element is not
 * stable", then "detached from the DOM"); a forced click lands after the list has closed; dispatching
 * `.click()` on the option node in-page raises no error but never applies the selection; and a manual
 * `mouse.down()/up()` on the option's box likewise does nothing.
 *
 * The keyboard route works because the control implements the ARIA listbox pattern: opening it moves
 * focus to the listbox and highlights the FIRST option. So move by INDEX — read the option texts, count
 * ArrowDown presses, then Enter. Watching `aria-activedescendant` instead is unreliable, because
 * `document.querySelector("[aria-activedescendant]")` can land on another widget entirely (the file
 * tree carries one) and read an empty value while the listbox is perfectly healthy.
 *
 * Callers should confirm the selection took, in whatever terms their page expresses it — this helper
 * can only verify that an option matching `option` existed to be chosen.
 */
export async function chooseSelectOption(
    page: Page,
    trigger: Locator,
    option: RegExp,
    describeTrigger = "the select"
): Promise<void> {
    const picker = trigger.first();
    await expect(picker, `${describeTrigger} is absent`).toBeVisible({ timeout: 60_000 });

    // Open it, and RETRY if the option list is not there a moment later. The file page is still
    // re-rendering right after navigation — `networkidle` resolves before it settles — and a click that
    // lands mid-render opens the dropdown and then loses it, so the option list reads as empty and the
    // helper reports "no viewer option matching ..." with nothing offered.
    let texts: string[] = [];
    for (let attempt = 0; attempt < 5 && texts.length === 0; attempt++) {
        if (attempt > 0) await page.waitForTimeout(2000);
        if ((await page.locator('[role="option"]').count()) === 0) {
            await clickWhenActionable(picker, describeTrigger);
        }
        await page.waitForTimeout(800);
        texts = await page.locator('[role="option"]').allTextContents();
    }

    // Move by INDEX rather than by watching `aria-activedescendant`. Reading that attribute is not
    // reliable here: `document.querySelector("[aria-activedescendant]")` returns the first match in
    // document order, and the File Manager's own file tree carries one too, so the lookup can land on
    // the tree and read an empty value while the listbox is perfectly healthy. That produced an empty
    // "highlights walked" list and a false "no viewer option matching" failure.
    //
    // Opening the control highlights option 0, so pressing ArrowDown exactly `index` times lands on the
    // wanted option. The option texts give the index directly, and this needs no attribute at all.
    const index = texts.findIndex((t) => option.test(t));
    expect(
        index,
        `no option matching ${option} in ${describeTrigger}; offered: ` +
            texts.map((t) => t.slice(0, 34)).join(" | ")
    ).toBeGreaterThanOrEqual(0);
    for (let i = 0; i < index; i++) {
        await page.keyboard.press("ArrowDown");
        await page.waitForTimeout(200);
    }
    await page.keyboard.press("Enter");
}

// ---------------------------------------------------------------------------------------------
// API subject lookup
// ---------------------------------------------------------------------------------------------

/** The app's API base and bearer token, read from what the running app itself holds.
 *
 * Two things had to be learned from the deployment rather than assumed:
 *
 * - **The API is NOT same-origin `/api/*`.** The app calls the API Gateway origin directly (which is
 *   why `connect-src` names it). A request to `https://<web-host>/api/database` reaches a path that
 *   expects SigV4 and answers 403 with "Invalid key=value pair ... in Authorization header", while an
 *   unknown path falls through to the S3 origin and answers AccessDenied. Both look like auth
 *   failures and neither is.
 * - **`page.request` carries no Authorization header**, and every route behind the custom authorizer
 *   answers 401 without one. The token the authorizer validates is the Cognito ID token.
 */
export async function apiContext(page: Page): Promise<{ base: string; token: string } | null> {
    if (!page.url().startsWith("http")) {
        await page.goto("/", { waitUntil: "domcontentloaded" });
    }
    const deadline = Date.now() + 20_000;
    for (;;) {
        const ctx = await page.evaluate(() => {
            let base: string | null = null;
            try {
                const raw = localStorage.getItem("vams_cache_config");
                if (raw) {
                    const parsed = JSON.parse(raw);
                    const config = parsed?.value ?? parsed ?? {};
                    base = config.api ?? null;
                }
            } catch {
                base = null;
            }
            const keys = Object.keys(localStorage);
            const idKey = keys.find((k) => /CognitoIdentityServiceProvider.*\.idToken$/.test(k));
            const token = (idKey && localStorage.getItem(idKey)) || null;
            return { base, token };
        });
        if (ctx.base && ctx.token) {
            return { base: ctx.base.replace(/\/+$/, ""), token: ctx.token };
        }
        if (Date.now() > deadline) return null;
        await page.waitForTimeout(1000);
    }
}

/** A file an asset holds, addressed the way the ViewFile route addresses it. */
export interface AssetFileRef {
    databaseId: string;
    assetId: string;
    /** Asset-relative key with a leading slash, e.g. `/BoomBox.glb`. */
    key: string;
}

export interface FindAssetFilesOptions {
    /** Search only this asset, written `databaseId/assetId`. */
    asset?: string;
    /** Skip assets not flagged distributable; the download API refuses their files. */
    distributableOnly?: boolean;
    /** Skip files larger than this many bytes. */
    maxBytes?: number;
    /** How many databases to scan, in listing order. */
    maxDatabases?: number;
    /** How many assets of each database to scan, in listing order. */
    maxAssetsPerDatabase?: number;
}

/**
 * The first file found for each of `extensions`, keyed by the lower-cased extension, or null when the
 * running app exposes no API base or ID token to look files up with.
 *
 * An extension with no file in the scanned assets is absent from the map, which is the caller's cue to
 * `test.skip` with a reason. The scan stops once every extension has a file and is bounded by
 * `maxDatabases` × `maxAssetsPerDatabase`, so a large environment costs a fixed number of requests.
 * Files are listed in basic mode, which skips the per-object enrichment a key match does not need.
 *
 * A named `asset` is listed without the scan's filters, and fails the calling test when its files
 * cannot be listed, so a mistyped or unreadable id does not pass for an asset with no such files.
 */
export async function findAssetFiles(
    page: Page,
    extensions: string[],
    options: FindAssetFilesOptions = {}
): Promise<Map<string, AssetFileRef> | null> {
    const ctx = await apiContext(page);
    if (!ctx) return null;
    const headers = { Authorization: `Bearer ${ctx.token}` };
    const asJson = async (path: string) => {
        const r = await page.request.get(`${ctx.base}${path}`, { headers });
        if (!r.ok()) return null;
        const b = await r.json().catch(() => null);
        return b?.message ?? b ?? null;
    };
    const itemsOf = (body: any): any[] => {
        const items = body?.Items ?? body?.items ?? body ?? [];
        return Array.isArray(items) ? items : [];
    };

    const wanted = Array.from(new Set(extensions.map((e) => e.toLowerCase())));
    const found = new Map<string, AssetFileRef>();
    const collect = async (databaseId: string, assetId: string, maxBytes = Infinity) => {
        const files = await asJson(
            `/database/${databaseId}/assets/${assetId}/listFiles?basic=true`
        );
        for (const f of itemsOf(files)) {
            const relative: string = f?.relativePath ?? f?.key ?? "";
            if (!relative || f.isFolder || Number(f.size) > maxBytes) continue;
            const lower = relative.toLowerCase();
            const ext = wanted.find((e) => !found.has(e) && lower.endsWith(e));
            if (ext) {
                const key = relative.startsWith("/") ? relative : `/${relative}`;
                found.set(ext, { databaseId, assetId, key });
            }
        }
        return files !== null;
    };

    if (options.asset) {
        const [databaseId, assetId] = options.asset.split("/");
        const listed = Boolean(databaseId && assetId) && (await collect(databaseId, assetId));
        expect(
            listed,
            `the files of ${options.asset} could not be listed; write it as ` +
                `<databaseId>/<assetId> and name an asset this user can read`
        ).toBe(true);
        return found;
    }

    const databases = itemsOf(await asJson("/database"));
    for (const db of databases.slice(0, options.maxDatabases ?? 8)) {
        if (found.size === wanted.length) break;
        const databaseId = db?.databaseId;
        if (!databaseId) continue;
        const assets = itemsOf(await asJson(`/database/${databaseId}/assets`));
        for (const asset of assets.slice(0, options.maxAssetsPerDatabase ?? 20)) {
            if (found.size === wanted.length) break;
            const assetId = asset?.assetId;
            if (!assetId) continue;
            if (options.distributableOnly && asset.isDistributable !== true) continue;
            await collect(databaseId, assetId, options.maxBytes);
        }
    }
    return found;
}
