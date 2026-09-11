/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const path = require("path");
const CopyPlugin = require("copy-webpack-plugin");

// The web-ifc WASM binaries and the Fragments model worker must be served as
// static files (fetched at runtime), NOT statically bundled. We copy them next
// to the UMD bundle so the viewer can load them same-origin from
// /viewers/thatopenwebifc/ — required for air-gapped/GovCloud and the COEP
// 'credentialless' isolation boundary. This mirrors how the threejs install
// copies occt-import-js *.wasm files.
//
// Note on filenames (verified against the installed packages):
//  - web-ifc ships web-ifc.wasm (single-thread), web-ifc-mt.wasm (multithread,
//    SharedArrayBuffer) and web-ifc-node.wasm (Node only). The multithread
//    worker is embedded in the JS glue, so there is no separate web-ifc worker
//    file to copy. We copy web-ifc.wasm and web-ifc-mt.wasm and skip the
//    node-only build.
//  - @thatopen/fragments ships its model worker at dist/Worker/worker.min.mjs.
const copyPatterns = [
    // web-ifc browser WASM binaries (single-thread + multithread). The
    // node-only build is intentionally excluded.
    {
        from: "node_modules/web-ifc/web-ifc.wasm",
        to: "web-ifc.wasm",
        noErrorOnMissing: true,
    },
    {
        from: "node_modules/web-ifc/web-ifc-mt.wasm",
        to: "web-ifc-mt.wasm",
        noErrorOnMissing: true,
    },
    // @thatopen/fragments model worker. It is fully minified and self-contained
    // (no bare import specifiers), so it can be served standalone. Copy it to a
    // fixed, MIME-safe .js name that the viewer's FragmentsManager.init() points
    // at. (.js — not .mjs — so it is served with a JavaScript MIME type across
    // all VAMS web hosts; the library still loads it as an ES module worker.)
    {
        from: "node_modules/@thatopen/fragments/dist/Worker/worker.min.mjs",
        to: "thatopenwebifc-fragments-worker.js",
        noErrorOnMissing: true,
    },
];

module.exports = {
    mode: "production",
    entry: "./thatOpenWebIfcEntry.js",
    output: {
        path: path.resolve(__dirname, "dist"),
        filename: "thatopenwebifc.min.js",
        library: {
            name: "ThatOpenWebIfcBundle",
            type: "umd",
            export: "default",
        },
        globalObject: "this",
        // @thatopen/fragments internally spawns its model worker via
        // `new Worker(new URL("./worker.mjs", import.meta.url))`. Webpack turns
        // that into a separately-emitted, content-hashed chunk and loads it at
        // runtime relative to this publicPath. The whole dist/ is copied to
        // public/viewers/thatopenwebifc/ by the install script, so pin the
        // publicPath to that same-origin location to guarantee the internal
        // worker chunk resolves correctly (deterministic, vs. "auto").
        publicPath: "/viewers/thatopenwebifc/",
    },
    resolve: {
        extensions: [".js", ".mjs", ".json"],
        fallback: {
            // Node core modules referenced by some deps but unneeded in browser.
            fs: false,
            path: false,
            crypto: false,
        },
    },
    plugins: [new CopyPlugin({ patterns: copyPatterns })],
    optimization: {
        minimize: true,
    },
    performance: {
        // The BIM engine + web-ifc is large; suppress size hints (cf. threejs config).
        hints: false,
        maxEntrypointSize: 8_000_000,
        maxAssetSize: 8_000_000,
    },
    // Silence "Critical dependency: the request of a dependency is an expression"
    // warnings that web-ifc/emscripten glue can emit; they are non-fatal.
    ignoreWarnings: [/Critical dependency/],
};
