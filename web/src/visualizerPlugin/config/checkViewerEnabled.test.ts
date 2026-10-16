/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The viewer install scripts (web/customInstalls/<viewer>/, run by `postinstall`) decide whether to
 * fetch and bundle a viewer's vendor package with checkViewerEnabled(). It has to read the same
 * three configuration files the browser merges (core, add-on, custom), or a viewer configured in
 * the add-on file — the licensed VNTANA and VEERUM viewers — is reported as missing and its install
 * is skipped even when it is enabled.
 */

import fs from "fs";
import os from "os";
import path from "path";
import { ALL_VIEWER_CONFIGS } from "../core/viewerSources";

// The install script is CommonJS; jest.requireActual loads it without an import shim.
const { checkViewerEnabled, CONFIG_FILES } = jest.requireActual(
    "../../../customInstalls/utility/checkViewerEnabled"
);

const CONFIG_DIR = __dirname;
const FILES = ["viewerConfig.json", "addonViewerConfig.json", "customViewerConfig.json"];

/** A temporary copy of the three configuration files, with `edit` applied to the parsed copies. */
const copies: string[] = [];
function configCopy(edit: (configs: Record<string, any>) => void): any[] {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "viewer-config-"));
    copies.push(dir);
    const configs: Record<string, any> = {};
    for (const name of FILES) {
        configs[name] = JSON.parse(fs.readFileSync(path.join(CONFIG_DIR, name), "utf8"));
    }
    edit(configs);
    for (const name of FILES) {
        if (configs[name] !== undefined) {
            fs.writeFileSync(path.join(dir, name), JSON.stringify(configs[name]));
        }
    }
    // The script's own file list, re-pointed at the copy.
    return CONFIG_FILES.map((file: any) => ({ ...file, path: path.join(dir, file.name) }));
}

const setEnabled = (config: any, id: string, enabled: boolean) => {
    config.viewers.find((viewer: any) => viewer.id === id).enabled = enabled;
};

let warn: jest.SpyInstance;
beforeEach(() => {
    warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
});
afterEach(() => warn.mockRestore());
afterAll(() => copies.forEach((dir) => fs.rmSync(dir, { recursive: true, force: true })));

describe("checkViewerEnabled", () => {
    it("agrees with the merged registry for every shipped viewer", () => {
        expect(ALL_VIEWER_CONFIGS.length).toBeGreaterThan(10);
        for (const viewer of ALL_VIEWER_CONFIGS) {
            expect([viewer.id, checkViewerEnabled(viewer.id)]).toEqual([viewer.id, viewer.enabled]);
        }
        expect(checkViewerEnabled("vntana-viewer")).toBe(false);
        expect(checkViewerEnabled("threejs-viewer")).toBe(true);
    });

    it.each(["vntana-viewer", "veerum-viewer"])(
        "reports %s enabled once it is enabled in addonViewerConfig.json",
        (id) => {
            const files = configCopy((configs) =>
                setEnabled(configs["addonViewerConfig.json"], id, true)
            );
            expect(checkViewerEnabled(id, files)).toBe(true);
            // Control: the same copy without the edit reports it disabled.
            expect(
                checkViewerEnabled(
                    id,
                    configCopy(() => undefined)
                )
            ).toBe(false);
        }
    );

    it("lets the first file that declares an id decide, as the browser does", () => {
        // A custom entry repeating a core id is skipped in the browser, so it must not flip the
        // install decision either.
        const files = configCopy((configs) => {
            const core = configs["viewerConfig.json"].viewers.find(
                (viewer: any) => viewer.id === "threejs-viewer"
            );
            configs["customViewerConfig.json"].viewers.push({ ...core, enabled: false });
        });
        expect(checkViewerEnabled("threejs-viewer", files)).toBe(true);
    });

    it("reads a viewer a fork adds in customViewerConfig.json", () => {
        const files = configCopy((configs) => {
            configs["customViewerConfig.json"].viewers.push({ id: "custom-probe", enabled: true });
        });
        expect(checkViewerEnabled("custom-probe", files)).toBe(true);
    });

    it("returns false for an unknown id, a missing core file, or a malformed file", () => {
        expect(checkViewerEnabled("no-such-viewer")).toBe(false);
        expect(
            checkViewerEnabled(
                "threejs-viewer",
                configCopy((configs) => {
                    delete configs["viewerConfig.json"];
                })
            )
        ).toBe(false);
        expect(
            checkViewerEnabled(
                "vntana-viewer",
                configCopy((configs) => {
                    configs["addonViewerConfig.json"] = { notViewers: [] };
                })
            )
        ).toBe(false);
    });

    it("works when the add-on and custom files are absent", () => {
        const files = configCopy((configs) => {
            delete configs["addonViewerConfig.json"];
            delete configs["customViewerConfig.json"];
        });
        expect(checkViewerEnabled("threejs-viewer", files)).toBe(true);
        expect(checkViewerEnabled("vntana-viewer", files)).toBe(false);
    });
});
