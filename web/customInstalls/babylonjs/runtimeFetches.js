/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Removes the third-party URLs the Babylon.js Gaussian splat loader would fetch at run time, so the
 * viewer never reaches a public CDN. A CDN fetch is blocked by the VAMS Content Security Policy and is
 * not possible at all in an air-gapped, GovCloud or EU Sovereign deployment.
 *
 * Babylon.js 9 ships three such defaults in @babylonjs/loaders/SPLAT:
 *
 * - `spzLibraryUrl` (splatFileLoader.pure.js) points at the @adobe/spz WebAssembly module on unpkg.
 *   Babylon loads it through an inline `<script type="module">`, which the hash-based `script-src`
 *   blocks without firing an error event, so the load would hang. @adobe/spz is also built with
 *   embind, which compiles functions with `new Function` and so needs `'unsafe-eval'`. The default is
 *   therefore rewritten to `undefined`, which selects Babylon's built-in SPZ parser (SPZ versions 2
 *   and 3, the same parser Babylon.js 8 used).
 * - `deflateURL` falls back to fflate on unpkg (splatFileLoader.pure.js and gaussianSplattingStream.js)
 *   when `window.fflate` is not set. src/index.js sets it, and the fallback is pointed at a same-origin
 *   copy of fflate's UMD build in case it is ever reached.
 *
 * Every rewrite must match: a Babylon.js upgrade that moves or renames a default fails the install
 * instead of shipping a bundle that reaches out at run time. After the rewrites no unpkg or jsDelivr
 * URL may remain except the ones listed in UNREACHABLE_CDN_URLS, which belong to Babylon.js features
 * the splat viewer never calls.
 */

/** Same-origin path the fflate UMD build is copied to (public/viewers/babylonjs/fflate/index.js). */
const FFLATE_PUBLIC_PATH = "/viewers/babylonjs/fflate/index.js";

const REWRITES = [
    {
        name: "@adobe/spz WebAssembly module (spzLibraryUrl default)",
        pattern: /"https:\/\/unpkg\.com\/@adobe\/spz@[^"]+"/g,
        replacement: "void 0",
        expectedCount: 1,
    },
    {
        name: "fflate UMD fallback (deflateURL default)",
        pattern: /"https:\/\/unpkg\.com\/fflate\/umd\/index\.js"/g,
        replacement: JSON.stringify(FFLATE_PUBLIC_PATH),
        expectedCount: 2,
    },
];

/**
 * CDN URLs that stay in the bundle because only Babylon.js features the splat viewer never uses can
 * fetch them: CSG2 (manifold-3d) and the EXR texture loader (fflate for compressed EXR).
 */
const UNREACHABLE_CDN_URLS = ["https://unpkg.com/manifold-3d@", "https://unpkg.com/fflate@"];

const CDN_URL =
    /https?:\/\/(?:unpkg\.com|cdn\.jsdelivr\.net|[a-z0-9.-]*jsdelivr\.net)\/[^"'`)\s]*/g;

/**
 * Applies the rewrites to the main bundle text. Throws when a rewrite does not match the expected
 * number of times.
 * @param {string} bundle main bundle source
 * @returns {{ bundle: string, applied: string[] }}
 */
const rewriteRuntimeFetches = (bundle) => {
    const applied = [];
    let out = bundle;
    for (const rewrite of REWRITES) {
        const count = (out.match(rewrite.pattern) || []).length;
        if (count !== rewrite.expectedCount) {
            throw new Error(
                `BabylonJS: expected ${rewrite.expectedCount} occurrence(s) of the ${rewrite.name} ` +
                    `in the bundle, found ${count}. Babylon.js changed how the splat loader fetches ` +
                    `it; update web/customInstalls/babylonjs/runtimeFetches.js.`
            );
        }
        out = out.replace(rewrite.pattern, rewrite.replacement);
        applied.push(`${rewrite.name}: ${count} -> ${rewrite.replacement}`);
    }
    return { bundle: out, applied };
};

/**
 * Lists the unpkg / jsDelivr URLs left in a bundle file that are not known to be unreachable.
 * @param {string} source bundle or chunk source
 * @returns {string[]}
 */
const findReachableCdnUrls = (source) => {
    const found = source.match(CDN_URL) || [];
    return [...new Set(found)].filter(
        (url) => !UNREACHABLE_CDN_URLS.some((allowed) => url.startsWith(allowed))
    );
};

module.exports = {
    FFLATE_PUBLIC_PATH,
    REWRITES,
    UNREACHABLE_CDN_URLS,
    rewriteRuntimeFetches,
    findReachableCdnUrls,
};
