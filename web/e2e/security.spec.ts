/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec: the served Content-Security-Policy, and whether the page actually works under it.
 *
 * This is the half of FIX-012 / FIX-026 that no other layer can assert. The offline test
 * (`infra/test/cspInlineScriptHashes.test.ts`) proves the emitted hashes match `index.html` in the
 * checkout. The deployment smoke suite (`suite_deploy_residuals.py::web-csp-header`) proves the
 * response carries a hash source at all. Neither answers the only question that matters to a user:
 * **did the browser run the inline scripts?** A hash covers the exact bytes of the element, so a
 * reformat, a stale constant, or a rebuilt `index.html` invalidates it — and the browser then refuses
 * the script silently, with nothing failing at build time.
 *
 * ## Why the hashes are load-bearing in every configuration
 *
 * A CSP may allow inline script by hash **or** by `'unsafe-inline'`, never both: when a hash source is
 * present, browsers ignore the keyword. So anything adding `'unsafe-inline'` would make every
 * assertion below pass whether the hashes were right or wrong.
 *
 * Nothing does. `generateContentSecurityPolicy()` adds no `'unsafe-inline'` to `script-src` in ANY
 * configuration — including with the Physna add-on enabled, whose viewer frames Physna's own HTTPS
 * origin (so that document carries its own CSP) and is granted only a `frame-src` / `connect-src`
 * origin. Verified against the deployed policy: `frame-src 'self' blob: https://app-api.physna.com`,
 * and no `'unsafe-inline'` in `script-src`.
 *
 * An earlier reading of FIX-012 assumed a `blob:` iframe, which WOULD inherit the parent policy and
 * need the keyword — and would have made these hashes inert whenever the add-on was on. The
 * implementation resolved it the better way, so this spec means what it says on any deployment.
 *
 * ## Evidence used
 *
 * Each inline block in `index.html` has a distinct observable side effect, so each one can be checked
 * independently rather than inferring "scripts ran" from the page merely rendering:
 *
 * | Block                      | Observable effect                                             |
 * | -------------------------- | ------------------------------------------------------------- |
 * | `__publicField` polyfill   | `window.__publicField` is a function                          |
 * | `SharedArrayBuffer` probe  | a console line naming SharedArrayBuffer                        |
 * | pre-render theme           | `<html>` carries `awsui-dark-mode` / an inline `color-scheme`  |
 */

import { test, expect, type Page } from "@playwright/test";

type Violation = { directive: string; blockedURI: string };

/** Collect CSP violations the browser reports, plus console errors that name one. */
async function watchForCspViolations(page: Page): Promise<{
    violations: Violation[];
    consoleErrors: string[];
}> {
    const violations: Violation[] = [];
    const consoleErrors: string[] = [];

    // The DOM event is the authoritative signal: it fires for every refused resource, including the
    // inline blocks, and carries which directive did the refusing.
    await page.exposeFunction("__recordCspViolation", (v: Violation) => {
        violations.push(v);
    });
    await page.addInitScript(() => {
        document.addEventListener("securitypolicyviolation", (e) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            (window as any).__recordCspViolation({
                directive: (e as SecurityPolicyViolationEvent).effectiveDirective,
                blockedURI: (e as SecurityPolicyViolationEvent).blockedURI,
            });
        });
    });

    page.on("console", (msg) => {
        const text = msg.text();
        if (
            msg.type() === "error" &&
            (text.includes("Content Security Policy") || text.includes("Refused to execute"))
        ) {
            consoleErrors.push(text);
        }
    });

    return { violations, consoleErrors };
}

/** The deployment's feature list, read from the page's own cached config.
 *
 * NOT via `page.request.get("/api/secure-config")`: that endpoint requires the Bearer token the app
 * attaches in-page from its auth session, and `page.request` does not carry it. The call returns 401,
 * `!response.ok()` yields an empty list, and every feature then reads as DISABLED — which silently
 * inverted the Physna branch of this spec's reporting. Reading the app's own `appCache` entry
 * (localStorage `vams_cache_config`) sees exactly what the running application sees.
 */
async function featuresEnabled(page: Page): Promise<string[]> {
    // The cache entry is written after the app boots and fetches its config, which is LATER than
    // `page.goto` resolving. Reading once immediately after a navigation therefore saw an empty list
    // and reported every feature as disabled. Poll to a short deadline instead.
    if (!page.url().startsWith("http")) {
        await page.goto("/", { waitUntil: "domcontentloaded" });
    }
    const deadline = Date.now() + 20_000;
    for (;;) {
        const features = await page.evaluate(() => {
            try {
                const raw = localStorage.getItem("vams_cache_config");
                if (!raw) return null;
                const parsed = JSON.parse(raw);
                const config = parsed?.value ?? parsed ?? {};
                return (config.featuresEnabled ?? null) as string[] | null;
            } catch {
                return null;
            }
        });
        if (features && features.length) return features;
        if (Date.now() > deadline) return features ?? [];
        await page.waitForTimeout(1000);
    }
}

test.describe("Content-Security-Policy", () => {
    test("the response carries a policy with a script-src directive", async ({ page }) => {
        const response = await page.goto("/");
        expect(response, "the app origin did not respond").toBeTruthy();

        const csp =
            response!.headers()["content-security-policy"] ??
            response!.headers()["Content-Security-Policy"];
        expect(csp, "no Content-Security-Policy header was served").toBeTruthy();
        expect(csp).toContain("script-src");
    });

    test("describes the policy mode, so a pass is not over-read", async ({ page }) => {
        const response = await page.goto("/");
        const csp = response!.headers()["content-security-policy"] ?? "";
        const scriptSrc = csp.split(";").find((d) => d.trim().startsWith("script-src")) ?? "";
        const features = await featuresEnabled(page);

        const hasHash = scriptSrc.includes("sha256-");
        const hasUnsafeInline = scriptSrc.includes("'unsafe-inline'");
        const physnaOn = features.some((f) => f.toUpperCase().includes("PHYSNA"));

        // Printed for context, then asserted. The print names the mode so a reader of a green run
        // knows what it proved; the assertions are what keep it true.
        console.log(
            `[csp-mode] physnaAddon=${physnaOn} hashSource=${hasHash} unsafeInline=${hasUnsafeInline}`
        );

        // No configuration may carry 'unsafe-inline' in script-src. That single token would make every
        // inline-script hash inert and silently void the rest of this file.
        expect(
            hasUnsafeInline,
            `script-src carries 'unsafe-inline' (physnaAddon=${physnaOn}). A source list containing a ` +
                `hash makes browsers ignore the keyword, so the hashes would no longer be what allows ` +
                `the inline blocks and the assertions here would pass on a wrong hash: ${scriptSrc}`
        ).toBe(false);

        // A hash source is therefore the only thing that can allow them.
        expect(
            hasHash,
            `script-src carries no sha256- source, so the inline blocks in index.html cannot ` +
                `execute: ${scriptSrc}`
        ).toBe(true);
    });
});

test.describe("the inline scripts in index.html execute under the served policy", () => {
    test("no CSP violation is reported for a script directive", async ({ page }) => {
        const { violations, consoleErrors } = await watchForCspViolations(page);
        await page.goto("/", { waitUntil: "networkidle" });

        const scriptViolations = violations.filter((v) => v.directive?.includes("script"));
        expect(
            scriptViolations,
            `the browser refused inline script under the served policy. This is what a stale ` +
                `INDEX_HTML_INLINE_SCRIPT_HASHES constant looks like at runtime: ` +
                `${JSON.stringify(scriptViolations)}`
        ).toEqual([]);
        expect(
            consoleErrors.filter((e) => e.includes("Refused to execute")),
            `console reported a refused script: ${consoleErrors.join(" | ")}`
        ).toEqual([]);
    });

    test("the __publicField polyfill block ran", async ({ page }) => {
        await page.goto("/", { waitUntil: "domcontentloaded" });
        // The load-bearing one. Every Cloudscape/vite chunk can reference this identifier, so a
        // blocked polyfill breaks the bundle at import time rather than degrading gracefully.
        const kind = await page.evaluate(
            () => typeof (window as never as { __publicField: unknown }).__publicField
        );
        expect(
            kind,
            "window.__publicField is not a function, so the first inline block did not execute"
        ).toBe("function");
    });

    test("the pre-render theme block ran", async ({ page }) => {
        await page.goto("/", { waitUntil: "domcontentloaded" });
        // The theme block runs before render and is the reason the page does not flash light. It
        // reads a localStorage preference and defaults to dark, so on a fresh context the dark class
        // is the expected outcome; a saved "light" preference is equally valid, hence the pair.
        const applied = await page.evaluate(() => ({
            dark: document.documentElement.classList.contains("awsui-dark-mode"),
            colorScheme: document.documentElement.style.colorScheme,
            saved: localStorage.getItem("vams-theme-preference"),
        }));
        if (applied.saved === "light") {
            // Nothing to assert about the class; the block ran and chose not to add it.
            console.log("[theme] a light preference is saved, so the dark branch is expected off");
        } else {
            expect(
                applied.dark || applied.colorScheme === "dark",
                `neither the dark class nor an inline color-scheme was applied ` +
                    `(${JSON.stringify(applied)}), so the theme block did not execute`
            ).toBe(true);
        }
    });

    test("the application actually renders under the policy", async ({ page }) => {
        const { violations } = await watchForCspViolations(page);
        await page.goto("/", { waitUntil: "networkidle" });

        // A blank page is the visible symptom of a blocked polyfill, and it is the outcome a user
        // reports. Asserting it separately from the violation list means the spec still fails if a
        // future browser stops emitting the event.
        const rendered = await page.evaluate(() => {
            const root = document.getElementById("root") ?? document.body;
            return (root?.childElementCount ?? 0) > 0;
        });
        expect(
            rendered,
            `the app rendered nothing. Violations seen: ${JSON.stringify(violations)}`
        ).toBe(true);
    });
});

test.describe("published bundle hygiene", () => {
    test("source maps are not served from the web origin (FIX-069)", async ({ page }) => {
        const response = await page.goto("/");
        const html = (await response!.text()) ?? "";

        // Derive the main bundle from the served HTML rather than guessing a filename, so this works
        // against any build.
        const match = html.match(/\/assets\/index-[A-Za-z0-9_-]+\.js/);
        expect(
            match,
            "could not find the main bundle reference in the served index.html"
        ).toBeTruthy();

        const mapUrl = `${match![0]}.map`;
        const mapResponse = await page.request.get(mapUrl);
        expect(
            mapResponse.status(),
            `${mapUrl} is publicly readable (status ${mapResponse.status()}), which publishes the ` +
                `frontend source from the web bucket`
        ).not.toBe(200);

        // Positive control: the bundle itself MUST be readable, or a 403 on the map would prove
        // nothing about source maps and everything about a broken origin.
        const bundleResponse = await page.request.get(match![0]);
        expect(
            bundleResponse.status(),
            "the main bundle itself is not readable, so the map assertion above is meaningless"
        ).toBe(200);
    });
});
