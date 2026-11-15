/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

/**
 * The IFC/BIM viewer loads web-ifc's WASM and the Fragments model worker from files that
 * web/customInstalls/thatopenwebifc/webpack.config.js copies next to the bundle. If an upgrade of
 * web-ifc or @thatopen/fragments renames one of those files, the copy must fail the build: a
 * silently skipped copy ships a viewer whose WASM or worker URL 404s at runtime. The config is read
 * as text so this guard runs even when the viewer is disabled and its install has no node_modules.
 */

import fs from "fs";
import path from "path";

const INSTALL_DIR = path.resolve(__dirname, "../../../../customInstalls/thatopenwebifc");
const CONFIG = fs.readFileSync(path.join(INSTALL_DIR, "webpack.config.js"), "utf8");
const COMPONENT = fs.readFileSync(
    path.join(__dirname, "ThatOpenWebIfcViewerComponent.tsx"),
    "utf8"
);

/** Every { from, to, noErrorOnMissing } entry of the copyPatterns array. */
function copyPatterns(): { from: string; to: string; noErrorOnMissing: string }[] {
    const block = CONFIG.slice(CONFIG.indexOf("const copyPatterns = ["));
    const body = block.slice(0, block.indexOf("];"));
    const entries = body.match(/\{[^{}]*\}/g) ?? [];
    return entries.map((e) => ({
        from: /from:\s*"([^"]+)"/.exec(e)?.[1] ?? "",
        to: /to:\s*"([^"]+)"/.exec(e)?.[1] ?? "",
        noErrorOnMissing: /noErrorOnMissing:\s*(\w+)/.exec(e)?.[1] ?? "",
    }));
}

describe("thatopenwebifc webpack copy patterns", () => {
    const patterns = copyPatterns();

    it("copies both browser web-ifc WASM builds and the Fragments worker", () => {
        expect(patterns.map((p) => p.from).sort()).toEqual([
            "node_modules/@thatopen/fragments/dist/Worker/worker.min.mjs",
            "node_modules/web-ifc/web-ifc-mt.wasm",
            "node_modules/web-ifc/web-ifc.wasm",
        ]);
    });

    it("fails the build when any copied runtime file is missing", () => {
        for (const p of patterns) {
            expect({ from: p.from, noErrorOnMissing: p.noErrorOnMissing }).toEqual({
                from: p.from,
                noErrorOnMissing: "false",
            });
        }
    });

    it("copies the worker to the URL the viewer passes to FragmentsManager.init()", () => {
        const worker = patterns.find((p) => p.from.includes("@thatopen/fragments"));
        expect(worker?.to).toBe("thatopenwebifc-fragments-worker.js");
        expect(COMPONENT).toContain(`"/viewers/thatopenwebifc/${worker?.to}"`);
    });

    it("names files the installed packages ship, when the install has run", () => {
        // Only meaningful after `npm install` in the custom install (the web postinstall does it
        // when the viewer is enabled); otherwise the text checks above still apply.
        const present = patterns.filter((p) =>
            fs.existsSync(path.join(INSTALL_DIR, path.dirname(p.from)))
        );
        for (const p of present) {
            expect({ from: p.from, exists: fs.existsSync(path.join(INSTALL_DIR, p.from)) }).toEqual(
                { from: p.from, exists: true }
            );
        }
    });
});
