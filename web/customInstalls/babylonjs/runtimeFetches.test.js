/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const {
    FFLATE_PUBLIC_PATH,
    rewriteRuntimeFetches,
    findReachableCdnUrls,
} = require("./runtimeFetches");

// The minified shapes of the three splat-loader defaults in the Babylon.js 9.33 bundle.
const SPZ_DEFAULT =
    'spzLibraryUrl:"object"==typeof WebAssembly?"https://unpkg.com/@adobe/spz@0.2.2/dist/spz.js":void 0';
const FFLATE_FALLBACK =
    'await ot.Tools.LoadScriptAsync(this._loadingOptions.deflateURL??"https://unpkg.com/fflate/umd/index.js")';
const bundleWith = (...parts) => parts.join(";");

describe("rewriteRuntimeFetches", () => {
    it("turns the @adobe/spz default off and points the fflate fallback at the same-origin copy", () => {
        const { bundle, applied } = rewriteRuntimeFetches(
            bundleWith(SPZ_DEFAULT, FFLATE_FALLBACK, FFLATE_FALLBACK)
        );

        expect(bundle).toContain('spzLibraryUrl:"object"==typeof WebAssembly?void 0:void 0');
        expect(bundle).toContain(`deflateURL??"${FFLATE_PUBLIC_PATH}"`);
        expect(bundle).not.toMatch(/unpkg\.com/);
        expect(applied).toHaveLength(2);
    });

    it("fails when Babylon.js moves the SPZ default, instead of shipping a CDN fetch", () => {
        expect(() =>
            rewriteRuntimeFetches(
                bundleWith("spzLibraryUrl:undefined", FFLATE_FALLBACK, FFLATE_FALLBACK)
            )
        ).toThrow(/expected 1 occurrence\(s\) of the @adobe\/spz WebAssembly module/);
    });

    it("fails when the fflate fallback count changes", () => {
        expect(() => rewriteRuntimeFetches(bundleWith(SPZ_DEFAULT, FFLATE_FALLBACK))).toThrow(
            /expected 2 occurrence\(s\) of the fflate UMD fallback/
        );
    });
});

describe("findReachableCdnUrls", () => {
    it("reports unpkg and jsDelivr URLs", () => {
        expect(
            findReachableCdnUrls(
                'a("https://unpkg.com/some-lib@1.0.0/dist/x.js");b("https://cdn.jsdelivr.net/npm/y@2")'
            )
        ).toEqual([
            "https://unpkg.com/some-lib@1.0.0/dist/x.js",
            "https://cdn.jsdelivr.net/npm/y@2",
        ]);
    });

    it("ignores the defaults of Babylon.js features the splat viewer never calls", () => {
        expect(
            findReachableCdnUrls(
                'm="https://unpkg.com/manifold-3d@3.4.0";e="https://unpkg.com/fflate@0.8.2"'
            )
        ).toEqual([]);
    });
});
