/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `infra/config/config.json` is the file `cdk synth` reads. The three templates are synthesized by the
 * T1 harness and validated by the config tests, but every one of those mocks `readFileSync` or bypasses
 * `getConfig()`, so nothing exercised the file itself: a block removed from the templates but left here,
 * or a key `getConfig()` rejects, surfaced only at an operator's synth. This reads the file as it is on
 * disk — no mock, no mutation — and runs `getConfig()` over it.
 *
 * Durable (root CLAUDE.md Rule 13): any future edit to this file or to `getConfig()` can break the pair.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import { newTestApp } from "../support/testApp";

const CONFIG_JSON = path.resolve(__dirname, "../../config/config.json");

describe("the tracked config.json", () => {
    let warn: jest.SpyInstance;
    beforeEach(() => {
        warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });
    afterEach(() => warn.mockRestore());

    const parsed = () => JSON.parse(fs.readFileSync(CONFIG_JSON, "utf8"));

    test("exists and parses as JSON with an app.pipelines object", () => {
        // Positive control for the assertions below.
        expect(fs.existsSync(CONFIG_JSON)).toBe(true);
        expect(typeof parsed().app.pipelines).toBe("object");
    });

    test("passes getConfig() exactly as it is on disk", () => {
        expect(() => Config.getConfig(newTestApp())).not.toThrow();
    });

    test("carries both new blocks explicitly and neither rejected key", () => {
        const config = parsed();
        expect(typeof config.app.vectorSearch.enabled).toBe("boolean");
        expect(typeof config.app.pipelines.useSystemGenAiMetadata.enabled).toBe("boolean");
        expect(config.app.pipelines).not.toHaveProperty("useGenAiMetadata3dLabeling");
        expect(config.app.pipelines).not.toHaveProperty("useConversionCadMeshMetadataExtraction");
    });

    test("getConfig() prints no vectorSearch backfill warning for it", () => {
        // An explicit `enabled` value is what keeps the shipped file quiet at every synth.
        Config.getConfig(newTestApp());
        const warnings = warn.mock.calls.map((call) => String(call[0])).join("\n");
        expect(warnings).not.toContain("app.vectorSearch is not set");
    });
});
