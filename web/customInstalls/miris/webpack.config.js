/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const path = require("path");

// The SDK packages are ES modules with a top-level await, so the bundle is emitted as an ES module
// (a UMD bundle cannot carry a top-level await) and its chunks import each other by relative URL.
//
// `import.meta` is left to the browser rather than resolved at build time. The SDK locates its
// WebAssembly with `new URL("<name>.wasm", import.meta.url)` and its Emscripten engine derives its
// script directory from `import.meta.url`, so both resolve against the served chunk, beside which
// mirisInstall.js copies the SDK's .wasm files. Resolving them at build time would embed this
// machine's file paths in the bundle.
module.exports = {
    mode: "production",
    entry: "./mirisSdkEntry.js",
    experiments: {
        outputModule: true,
    },
    output: {
        path: path.resolve(__dirname, "dist"),
        publicPath: "auto",
        filename: "miris-sdk.js",
        chunkFilename: "[name]-[contenthash:8].js",
        module: true,
        chunkFormat: "module",
        chunkLoading: "import",
        library: {
            type: "module",
        },
        clean: true,
    },
    module: {
        parser: {
            javascript: {
                importMeta: false,
            },
        },
    },
    performance: {
        hints: false,
    },
    // No source maps: the bundle is published to the web app bucket, which serves anonymous reads.
    devtool: false,
};
