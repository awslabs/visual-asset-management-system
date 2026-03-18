/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { nodePolyfills } from "vite-plugin-node-polyfills";
import fs from "fs";

/**
 * Vite plugin to allow JSX syntax in .js files.
 * CRA allowed this but Vite/Rollup does not by default.
 * This plugin intercepts .js file loads in src/ and appends ?jsx to tell
 * esbuild to treat them as JSX.
 */
function jsxInJs(): Plugin {
    return {
        name: "jsx-in-js",
        enforce: "pre",
        async load(id) {
            // Only process .js files inside src/
            if (!id.endsWith(".js") || !id.replace(/\\/g, "/").includes("/src/")) {
                return null;
            }
            const code = await fs.promises.readFile(id, "utf-8");
            return {
                code,
                map: null,
            };
        },
        async transform(code, id) {
            if (!id.endsWith(".js") || !id.replace(/\\/g, "/").includes("/src/")) {
                return null;
            }
            // Use esbuild to transform .js files containing JSX and/or TypeScript
            const { transform } = await import("esbuild");
            const result = await transform(code, {
                loader: "tsx",
                jsx: "automatic",
                sourcefile: id,
                sourcemap: "inline",
            });
            return {
                code: result.code,
                map: result.map || null,
            };
        },
    };
}

export default defineConfig({
    plugins: [
        jsxInJs(),
        react(),
        nodePolyfills({
            include: ["buffer", "process", "stream"],
            globals: { Buffer: true, process: true },
        }),
    ],
    optimizeDeps: {
        esbuildOptions: {
            loader: {
                ".js": "tsx",
            },
        },
        // Only scan the app entry point — prevents Vite from scanning viewer plugin HTML files
        // in customInstalls/ and public/viewers/ directories
        entries: ["index.html"],
        exclude: ["maplibre-gl-js-amplify"],
    },
    server: {
        port: 3001,
        headers: {
            // COOP/COEP headers for SharedArrayBuffer support in local development.
            // These headers enable cross-origin isolation required by WASM-based viewer
            // plugins. In production, the COI service worker handles this, but during
            // local dev the service worker may not be active on the very first page load.
            "Cross-Origin-Embedder-Policy": "credentialless",
            "Cross-Origin-Opener-Policy": "same-origin",
        },
    },
    build: {
        outDir: "dist",
        sourcemap: true,
        target: "es2020",
        chunkSizeWarningLimit: 1500, // Large third-party libs: maplibre-gl (~1MB), pdf.worker (~1MB), jodit (~870KB)
        rollupOptions: {
            // Stub out @aws-amplify/geo — it's a transitive dep from
            // @aws-amplify/ui-react v5's maplibre-gl-js-amplify that
            // doesn't exist in Amplify v6. VAMS doesn't use Amplify Geo.
            external: ["@aws-amplify/geo"],
        },
    },
    define: {
        global: "globalThis",
    },
    css: {
        preprocessorOptions: {
            scss: {},
        },
    },
});
