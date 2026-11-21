/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * One setting enables the add-on: the web install bundles the Miris web SDK only when the deployment
 * configuration enables `app.addons.useMirisStreaming`, and the viewer entry ships enabled, offered
 * only when the deployment publishes MIRIS_ADDON and ALLOWUNSAFEEVAL.
 */

import fs from "fs";
import os from "os";
import path from "path";
import addonViewerConfig from "../../config/addonViewerConfig.json";

// The install helper is CommonJS; jest.requireActual loads it without an import shim.
const { mirisAddonEnabled, DEFAULT_CONFIG_PATH } = jest.requireActual(
    "../../../../customInstalls/miris/mirisAddonEnabled"
);

const dirs: string[] = [];
afterAll(() => dirs.forEach((dir) => fs.rmSync(dir, { recursive: true, force: true })));

/** A configuration file holding `body` (an object, or raw text). */
function configFile(body: unknown): string {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "miris-install-"));
    dirs.push(dir);
    const file = path.join(dir, "config.json");
    fs.writeFileSync(file, typeof body === "string" ? body : JSON.stringify(body));
    return file;
}

const withAddon = (enabled: unknown) => ({
    app: { addons: { useMirisStreaming: { enabled } } },
});

describe("the Miris SDK install follows the deployment configuration", () => {
    it("bundles the SDK only when the configuration enables the add-on", () => {
        expect(mirisAddonEnabled(configFile(withAddon(true)))).toBe(true);
        expect(mirisAddonEnabled(configFile(withAddon(false)))).toBe(false);
        expect(mirisAddonEnabled(configFile(withAddon("true")))).toBe(false);
        expect(mirisAddonEnabled(configFile({ app: { addons: {} } }))).toBe(false);
    });

    it("treats a missing or unreadable configuration as the add-on being off", () => {
        expect(mirisAddonEnabled(path.join(os.tmpdir(), "no-such-dir", "config.json"))).toBe(false);
        expect(mirisAddonEnabled(configFile("{not json"))).toBe(false);
    });

    it("reads VAMS_CONFIG_PATH when it is set, else infra/config/config.json", () => {
        expect(DEFAULT_CONFIG_PATH).toBe(
            path.resolve(__dirname, "../../../../../infra/config/config.json")
        );
        const previous = process.env.VAMS_CONFIG_PATH;
        try {
            process.env.VAMS_CONFIG_PATH = configFile(withAddon(true));
            expect(mirisAddonEnabled()).toBe(true);
        } finally {
            if (previous === undefined) delete process.env.VAMS_CONFIG_PATH;
            else process.env.VAMS_CONFIG_PATH = previous;
        }
    });

    it("the shipped configuration leaves the add-on off, so a default install bundles nothing", () => {
        expect(mirisAddonEnabled(DEFAULT_CONFIG_PATH)).toBe(false);
    });

    it("the viewer entry ships enabled and gated by the add-on's switches", () => {
        const entry = (addonViewerConfig as any).viewers.find(
            (viewer: any) => viewer.id === "miris-stream-viewer"
        );
        expect(entry.enabled).toBe(true);
        expect(entry.featuresEnabledRestriction).toEqual(["MIRIS_ADDON", "ALLOWUNSAFEEVAL"]);
    });
});
