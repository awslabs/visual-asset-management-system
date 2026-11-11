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
// Note on filenames (verified against web-ifc 0.0.77 and @thatopen/fragments 3.6.0):
//  - web-ifc ships web-ifc.wasm (single-thread), web-ifc-mt.wasm (multithread,
//    SharedArrayBuffer) and web-ifc-node.wasm (Node only). The multithread
//    worker is embedded in the JS glue, so there is no separate web-ifc worker
//    file to copy. We copy web-ifc.wasm and web-ifc-mt.wasm and skip the
//    node-only build.
//  - @thatopen/fragments ships its model worker at dist/Worker/worker.min.mjs
//    (its "./worker" export points at the unminified dist/Worker/worker.mjs).
//
// Every entry below is required at runtime, so noErrorOnMissing is false: if a
// library upgrade renames or moves one of these files, the webpack build (and
// therefore the install) fails loudly instead of silently shipping a viewer
// whose WASM or worker URL 404s.
const copyPatterns = [
    // web-ifc browser WASM binaries (single-thread + multithread). The
    // node-only build is intentionally excluded.
    {
        from: "node_modules/web-ifc/web-ifc.wasm",
        to: "web-ifc.wasm",
        noErrorOnMissing: false,
    },
    {
        from: "node_modules/web-ifc/web-ifc-mt.wasm",
        to: "web-ifc-mt.wasm",
        noErrorOnMissing: false,
    },
    // @thatopen/fragments model worker. It is fully minified and self-contained
    // (no bare import specifiers), so it can be served standalone. Copy it to a
    // fixed, MIME-safe .js name that the viewer's FragmentsManager.init() points
    // at. (.js — not .mjs — so it is served with a JavaScript MIME type across
    // all VAMS web hosts; the library still loads it as an ES module worker.)
    {
        from: "node_modules/@thatopen/fragments/dist/Worker/worker.min.mjs",
        to: "thatopenwebifc-fragments-worker.js",
        noErrorOnMissing: false,
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
        alias: {
            // components-front 3.5+ lazily imports the OPTIONAL peer
            // potree-core, only from its opt-in PointCloudLoader (Potree
            // octrees), which this viewer never uses. potree-core is not
            // installed, so resolve it to an empty module instead of failing
            // the build. Point clouds are handled by VAMS's own Potree viewer.
            "potree-core": false,
        },
        fallback: {
            // Node core modules referenced by some deps but unneeded in browser.
            fs: false,
            path: false,
            crypto: false,
        },
    },
    module: {
        rules: [
            {
                // That Open 3.5+ ships an opt-in WebGPU path that reads
                // WebGPU-only names (WebGPURenderer, TSL, NodeMaterial, ...) off
                // `import * as THREE from "three"`. Those names exist only when an
                // app aliases `three` to `three/webgpu`; VAMS uses the WebGL
                // renderer, so they are undefined here and every access is
                // feature-detected (`typeof THREE.WebGPURenderer === "function"`,
                // `THREE.NodeMaterial ?? class {}`) or sits in the lazily
                // imported deferred-pipeline chunk that WebGL never loads.
                // Webpack's strict ESM check turns each access into a build
                // error, so downgrade it to a warning for these packages only;
                // the matching warnings are filtered in ignoreWarnings below.
                test: /[\\/]node_modules[\\/]@thatopen[\\/]/,
                parser: { importExportsPresence: "warn" },
            },
        ],
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
    // Also filter the expected "WebGPU-only export not found in 'three'"
    // warnings described in module.rules above. The list is exact so a missing
    // export of any other name still surfaces as a build warning.
    ignoreWarnings: [
        /Critical dependency/,
        /export '(WebGPURenderer|TSL|NodeMaterial|PointsNodeMaterial|MRTNode|Node|NodeUpdateType|StorageTexture|BlendMode|RenderPipeline)' \(imported as '[\w$]+'\) was not found in 'three'/,
    ],
};
