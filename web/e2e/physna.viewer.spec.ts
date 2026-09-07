/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core spec: the Physna viewer renders after a sync, and does so without tripping the CSP.
 *
 * What the add-on actually costs the CSP, verified against the deployed policy rather than assumed:
 * **nothing in `script-src`.** The viewer's `<iframe src>` is Physna's own HTTPS origin, so that
 * document loads under Physna's CSP and its inline scripts are outside this policy's reach. The add-on
 * needs only the Physna origin in `frame-src` (and `connect-src` for auxiliary fetches), which is what
 * `generateContentSecurityPolicy()` adds. Observed on the deployed policy: `frame-src 'self' blob:
 * https://app-api.physna.com`, with no `'unsafe-inline'` anywhere in `script-src`.
 *
 * That matters for what this spec can claim. An earlier reading of FIX-012 assumed the viewer used a
 * `blob:` iframe — which WOULD inherit the parent CSP and need `'unsafe-inline'`, and would make VAMS's
 * own inline-script hashes inert whenever the add-on was on. The implementation resolved it the better
 * way instead, so the hashes stay load-bearing in every configuration and this spec's CSP assertions
 * are meaningful with the add-on either on or off.
 *
 * `security.spec.ts` asserts the policy's shape and that VAMS's own inline blocks run. This one
 * asserts the third-party surface: that framing Physna does not trip the policy.
 *
 * ## The distinction this spec is built around
 *
 * The Physna viewer legitimately shows an error/pending state when a file has been uploaded but
 * Physna has not finished indexing it — that is normal and is not a defect. A CSP block ALSO renders
 * as a broken viewer. Conflating the two is the trap: a genuine `script-src` refusal would read as
 * "not indexed yet" and the spec would pass while the viewer was broken for every user.
 *
 * So the assertions are split:
 *
 * 1. **CSP violations are fatal, always.** A `securitypolicyviolation` for a script/frame directive
 *    fails the test regardless of whether the file is indexed — it is never a valid outcome.
 * 2. **The viewer mounting is asserted separately**, and a Physna "not indexed / not found" state is
 *    reported as a skip with its message, not as a pass and not as a failure.
 *
 * ## Subject selection
 *
 * Per `e2e/CLAUDE.md` Rule 1 the subject is derived from whatever the environment holds: the spec
 * queries the API for a file whose extension is in the Physna viewer's supported set and skips when
 * there is none. It mutates nothing (Rule 2) — no upload, no delete.
 */

import { test, expect, type Page } from "@playwright/test";

/** The Physna viewer's supported set, from visualizerPlugin/config/viewerConfig.json. */
const PHYSNA_EXTENSIONS = [
    ".3ds",
    ".asm",
    ".catpart",
    ".catproduct",
    ".glb",
    ".iam",
    ".iges",
    ".igs",
    ".ipt",
    ".jt",
    ".obj",
    ".par",
    ".prt",
    ".sldasm",
    ".sldprt",
    ".stl",
    ".step",
    ".stp",
    ".x_b",
    ".x_t",
];

/** Benign console noise, mirroring viewers.spec.ts. CSP messages are deliberately NOT ignored. */
const IGNORE = [
    /favicon/i,
    /Failed to load resource.*404/i,
    /net::ERR_ABORTED/i,
    /ResizeObserver loop/i,
    /Download the React DevTools/i,
    /getAmplifyConfig: Fetch error/i,
    /Failed to refresh amplify-config/i,
    /Error getting secure-config/i,
];

type Violation = { directive: string; blockedURI: string };

async function watchCsp(page: Page): Promise<{ violations: Violation[]; errors: string[] }> {
    const violations: Violation[] = [];
    const errors: string[] = [];

    await page.exposeFunction("__recordPhysnaCsp", (v: Violation) => violations.push(v));
    await page.addInitScript(() => {
        document.addEventListener("securitypolicyviolation", (e) => {
            const ev = e as SecurityPolicyViolationEvent;
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            (window as any).__recordPhysnaCsp({
                directive: ev.effectiveDirective,
                blockedURI: ev.blockedURI,
            });
        });
    });

    page.on("console", (msg) => {
        if (msg.type() !== "error") return;
        const t = msg.text();
        if (!IGNORE.some((re) => re.test(t))) errors.push(`console: ${t}`);
    });
    page.on("pageerror", (err) => {
        const t = err.message || String(err);
        if (!IGNORE.some((re) => re.test(t))) errors.push(`pageerror: ${t}`);
    });

    return { violations, errors };
}

/** The deployment's feature list, read from the page's own cached config.
 *
 * NOT via `page.request.get("/api/secure-config")`: that endpoint requires the Bearer token the app
 * attaches in-page from its auth session, and `page.request` does not carry it. The call returns 401,
 * `!response.ok()` yields an empty list, and every feature then reads as DISABLED — which silently
 * inverted the Physna branch of this spec's reporting. Reading the app's own `appCache` entry
 * (localStorage `vams_cache_config`) sees exactly what the running application sees.
 */
async function features(page: Page): Promise<string[]> {
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
async function apiContext(page: Page): Promise<{ base: string; token: string } | null> {
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

/** Find any file in the environment the Physna viewer supports. Null when there is none. */
async function findPhysnaFile(
    page: Page
): Promise<{ databaseId: string; assetId: string; key: string } | null> {
    const ctx = await apiContext(page);
    if (!ctx) return null;
    const headers = { Authorization: `Bearer ${ctx.token}` };
    const asJson = async (path: string) => {
        const r = await page.request.get(`${ctx.base}${path}`, { headers });
        if (!r.ok()) return null;
        const b = await r.json().catch(() => null);
        return b?.message ?? b ?? null;
    };

    const dbs = await asJson("/database");
    const dbItems = dbs?.Items ?? dbs?.items ?? [];
    for (const db of dbItems.slice(0, 6)) {
        const databaseId = db.databaseId;
        if (!databaseId) continue;
        const assets = await asJson(`/database/${databaseId}/assets`);
        const assetItems = assets?.Items ?? assets?.items ?? [];
        for (const asset of assetItems.slice(0, 12)) {
            const assetId = asset.assetId;
            if (!assetId) continue;
            const files = await asJson(`/database/${databaseId}/assets/${assetId}/listFiles`);
            const fileItems = files?.Items ?? files?.items ?? files ?? [];
            for (const f of Array.isArray(fileItems) ? fileItems : []) {
                const key: string = f.relativePath ?? f.key ?? "";
                if (!key || f.isFolder) continue;
                if (PHYSNA_EXTENSIONS.some((ext) => key.toLowerCase().endsWith(ext))) {
                    return { databaseId, assetId, key };
                }
            }
        }
    }
    return null;
}

test.describe("Physna viewer", () => {
    test("renders under the deployed CSP without a policy violation", async ({ page }) => {
        // A skip must name the precondition it is missing, or a run that skipped everything reads
        // the same as a run that passed everything.
        const featureList = await features(page);
        const enabled = featureList.some((f) => f.toUpperCase().includes("PHYSNA"));
        console.log(
            `[physna] featuresEnabled=${JSON.stringify(featureList)} addonEnabled=${enabled}`
        );
        test.skip(
            !enabled,
            `The Physna add-on is not enabled (features: ${featureList.join(",")})`
        );

        // A run can name a file known to be SYNCED AND INDEXED in Physna, which is the only way to
        // reach an actually-rendered viewer: indexing happens on Physna's side and takes minutes, so
        // a file uploaded by a test is normally still pending when the test looks at it.
        //   E2E_PHYSNA_FILE="databaseId/assetId//path/to/file.glb"
        const override = process.env.E2E_PHYSNA_FILE;
        let subject: { databaseId: string; assetId: string; key: string } | null = null;
        if (override) {
            const [databaseId, assetId, ...rest] = override.split("/");
            subject = { databaseId, assetId, key: "/" + rest.join("/").replace(/^\/+/, "") };
        } else {
            subject = await findPhysnaFile(page);
        }
        console.log(`[physna] subject=${JSON.stringify(subject)}`);
        test.skip(
            !subject,
            "No file with a Physna-supported extension was found via the API (checked the first " +
                "few databases and assets)"
        );

        const { violations, errors } = await watchCsp(page);

        // The stored key is asset-relative with a leading slash; ViewFile parses the segment after
        // /file/ as the key, so it must carry that slash.
        const key = subject!.key.startsWith("/") ? subject!.key : `/${subject!.key}`;
        await page.goto(
            `/#/databases/${subject!.databaseId}/assets/${subject!.assetId}` +
                `/file/${encodeURIComponent(key)}`,
            { waitUntil: "domcontentloaded" }
        );

        // Wait on the file's own heading before touching the viewer controls. `viewers.spec.ts`
        // establishes this order for a reason: the ViewFile route renders its shell, heading and
        // viewer picker asynchronously, so a picker lookup issued right after `goto` finds nothing,
        // silently skips the selection, and then times out waiting for a viewer that was never chosen.
        // A plain substring match, not a constructed RegExp: a file name carries characters that are
        // regex metacharacters (`.` at minimum, and CAD names often carry `(`, `+`, `[`), so building
        // a pattern from it needs escaping that is easy to get wrong for no benefit here.
        const fileName = key.split("/").filter(Boolean).pop() ?? key;
        await expect(page.getByRole("heading", { name: fileName, exact: false })).toBeVisible({
            timeout: 30_000,
        });

        // The extension maps to several viewers (Three.js / Physna / VNTANA for .glb), so none
        // auto-loads and the picker must be used.
        const picker = page.getByRole("button", { name: /select viewer/i });
        if (await picker.isVisible().catch(() => false)) {
            await picker.click();
            const option = page.getByRole("option", { name: /Physna/i }).first();
            if (await option.isVisible().catch(() => false)) {
                await option.click();
            } else {
                test.skip(true, "The Physna viewer is not offered for this file");
            }
        } else {
            test.skip(
                true,
                "No viewer picker appeared for this file, so Physna cannot be selected"
            );
        }

        // Give the iframe time to load the Physna-hosted document and run its inline scripts —
        // which is the moment a CSP refusal would occur.
        await page.waitForTimeout(10_000);

        // ---- assertion 1: a CSP refusal is never acceptable, indexed or not ----
        const blocking = violations.filter(
            (v) => v.directive?.includes("script") || v.directive?.includes("frame")
        );
        expect(
            blocking,
            `the Physna viewer tripped the Content-Security-Policy. This add-on is the only reason ` +
                `'unsafe-inline' is in script-src, so a violation here means that exception is not ` +
                `doing its job: ${JSON.stringify(blocking)}`
        ).toEqual([]);

        // ---- assertion 2: no refused-script console error ----
        const refused = errors.filter(
            (e) => /Content Security Policy/i.test(e) || /Refused to (execute|frame|load)/i.test(e)
        );
        expect(refused, `console reported a CSP refusal: ${refused.join(" | ")}`).toEqual([]);

        // ---- assertion 3: the viewer surface, reported separately ----
        // A file uploaded but not yet indexed by Physna is a legitimate pending state. It is skipped
        // with its own message rather than passed or failed, so it can never be confused with the
        // CSP outcome asserted above.
        const surface = page.locator("iframe, canvas").first();
        const mounted = await surface.isVisible().catch(() => false);
        if (!mounted) {
            const bodyText =
                (await page
                    .locator("body")
                    .innerText()
                    .catch(() => "")) ?? "";
            // The viewer's own copy, read off the deployment rather than guessed: "This file has not
            // been synced to Physna yet. Checking again in 30s…". An earlier pattern here missed it
            // because it did not allow for "has not BEEN synced", so a perfectly normal pending state
            // was reported as a broken viewer.
            const pending =
                /not been synced|not (yet )?(indexed|found|available|synced)|checking again in|still (indexing|processing)/i.test(
                    bodyText
                );
            test.skip(
                pending,
                "Physna has not finished indexing this file, so the viewer shows its pending " +
                    "state. The CSP assertions above still ran and passed."
            );
            expect(
                mounted,
                `the Physna viewer mounted no iframe or canvas and showed no recognisable pending ` +
                    `state. Page text: ${bodyText.slice(0, 400)}`
            ).toBe(true);
        }

        // ---- assertion 4: no uncaught viewer errors ----
        expect(errors, `viewer errors: ${errors.join(" | ")}`).toEqual([]);
    });

    test("the add-on costs script-src nothing and is granted only a frame-src origin", async ({
        page,
    }) => {
        // The invariant, asserted from the deployed policy rather than from source. This is the
        // regression guard for the resolution FIX-012 actually took: if someone reintroduces
        // 'unsafe-inline' for this viewer, every inline-script hash in the policy silently goes inert
        // (a source list carrying a hash makes browsers ignore the keyword), and the protection that
        // security.spec.ts verifies would be lost without any test failing.
        const response = await page.goto("/");
        const csp = response!.headers()["content-security-policy"] ?? "";
        const directive = (name: string) =>
            csp
                .split(";")
                .find((d) => d.trim().startsWith(name))
                ?.trim() ?? "";
        const scriptSrc = directive("script-src");
        const frameSrc = directive("frame-src");
        const enabled = (await features(page)).some((f) => f.toUpperCase().includes("PHYSNA"));

        expect(scriptSrc, "no script-src directive was served").toBeTruthy();
        expect(
            scriptSrc.includes("'unsafe-inline'"),
            `script-src carries 'unsafe-inline'. The Physna viewer frames Physna's own HTTPS origin, ` +
                `so the framed document has its own CSP and needs nothing relaxed here — and the ` +
                `keyword would make every inline-script hash in this policy inert. script-src: ${scriptSrc}`
        ).toBe(false);

        if (enabled) {
            // What the add-on IS granted: its origin in frame-src, so the viewer iframe can load.
            expect(
                /physna/i.test(frameSrc),
                `the add-on is enabled but frame-src does not name a Physna origin, so the viewer's ` +
                    `iframe cannot load: ${frameSrc}`
            ).toBe(true);
        }
    });
});
