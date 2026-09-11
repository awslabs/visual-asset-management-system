/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import fs from "fs";
import path from "path";

/**
 * maplibre-gl ships its web worker as a separate ES module and, by default, resolves it as a
 * sibling of the main module's `import.meta.url`. Inside the Vite bundle that sibling is never
 * emitted, so a map whose module graph does not run `setWorkerUrl()` first requests a worker
 * that does not exist and renders nothing. Every module that mounts a maplibre map must therefore
 * import the shared setup module for its side effect. This scans the source tree so a new map
 * component cannot omit it.
 */
const SRC = path.resolve(__dirname, "..", "..");
const SETUP_MODULE = path.join(SRC, "common", "utils", "maplibreWorker.ts");

const walk = (dir: string): string[] =>
    fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return entry.name === "__mocks__" ? [] : walk(full);
        return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [full] : [];
    });

const importsMaplibreRuntime = (source: string): boolean =>
    /from\s+["']react-map-gl\/maplibre["']/.test(source) ||
    /^import\s+(?!type\b)[^;]*from\s+["']maplibre-gl["']/m.test(source);

describe("maplibre worker setup", () => {
    it("routes the worker through Vite's worker pipeline and registers it with maplibre", () => {
        expect(fs.existsSync(SETUP_MODULE)).toBe(true);
        const source = fs.readFileSync(SETUP_MODULE, "utf8");
        // `?worker&url` bundles the worker with its `maplibre-gl-shared.mjs` import; a plain `?url`
        // copies the file alone and the worker fails on its first import.
        expect(source).toMatch(
            /from\s+["']maplibre-gl\/dist\/maplibre-gl-worker\.mjs\?worker&url["']/
        );
        expect(source).toMatch(/\bsetWorkerUrl\(/);
    });

    it("is imported by every module that mounts a maplibre map", () => {
        const consumers = walk(SRC)
            .filter((file) => file !== SETUP_MODULE)
            .filter((file) => importsMaplibreRuntime(fs.readFileSync(file, "utf8")));
        // Positive control: the scan must find the known map components, or a broken regex would
        // make the assertion below pass vacuously.
        expect(consumers.map((f) => path.basename(f))).toEqual(
            expect.arrayContaining(["SearchPageMapView.tsx", "MapThumbnail.tsx"])
        );

        const missing = consumers.filter(
            (file) => !/common\/utils\/maplibreWorker["']/.test(fs.readFileSync(file, "utf8"))
        );
        expect(missing.map((f) => path.relative(SRC, f))).toEqual([]);
    });
});

describe("maplibre-gl / react-map-gl compatibility", () => {
    it("pairs maplibre-gl 6+ with a react-map-gl that no longer reads map.transform", () => {
        // maplibre-gl 6 removed the public `map.transform` property; the maplibre wrapper re-exported
        // by react-map-gl read `transform.center` on every camera update until 8.1.2, so an older pair
        // crashes each map into the page error boundary.
        const version = (pkg: string): number[] =>
            JSON.parse(
                fs.readFileSync(path.join(SRC, "..", "node_modules", pkg, "package.json"), "utf8")
            )
                .version.split(".")
                .map(Number);
        const [maplibreMajor] = version("maplibre-gl");
        const [major, minor, patch] = version("react-map-gl");
        if (maplibreMajor >= 6) {
            expect(major * 1e6 + minor * 1e3 + patch).toBeGreaterThanOrEqual(8 * 1e6 + 1 * 1e3 + 2);
        }
    });
});
