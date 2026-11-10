/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The add-on feature-switch names the web checks travel through /api/secure-config as opaque
 * strings, so a name that differs from the one the deployment publishes is a page or a navigation
 * entry that never shows, with no error anywhere. Each value must therefore be declared in
 * ADDON_APP_FEATURES (infra/common/addonAppFeatures.ts, read here as text), and none may repeat a
 * core or custom name.
 */

import * as fs from "fs";
import * as path from "path";
import { addonFeaturesEnabled } from "./addonFeatures";
import { featuresEnabled } from "../common/constants/featuresEnabled";
import { customFeaturesEnabled } from "../custom/customFeatures";

const ADDON_APP_FEATURES_TS = path.resolve(
    __dirname,
    "..",
    "..",
    "..",
    "infra",
    "common",
    "addonAppFeatures.ts"
);

/** The string values of the ADDON_APP_FEATURES enum members. */
function declaredAddonFeatureValues(source: string): string[] {
    const body = /export\s+enum\s+ADDON_APP_FEATURES\s*\{([^}]*)\}/.exec(source);
    if (!body) {
        throw new Error("ADDON_APP_FEATURES enum not found in addonAppFeatures.ts");
    }
    return Array.from(body[1].matchAll(/^\s*[A-Z][A-Z0-9_]*\s*=\s*"([^"]+)"/gm), (m) => m[1]);
}

describe("addonFeaturesEnabled", () => {
    const declared = declaredAddonFeatureValues(fs.readFileSync(ADDON_APP_FEATURES_TS, "utf8"));

    it("reads the add-on enum (control: the Physna add-on's switch is found)", () => {
        expect(declared).toContain("PHYSNA_ADDON");
    });

    it("rejects a value the enum does not declare (control)", () => {
        expect(declared).not.toContain("ADDON_PROBE_NOT_DECLARED");
    });

    it.each(Object.entries(addonFeaturesEnabled))(
        "%s names a switch declared in ADDON_APP_FEATURES",
        (_key, value) => {
            expect(declared).toContain(value);
        }
    );

    it("names each switch by its own value", () => {
        for (const [key, value] of Object.entries(addonFeaturesEnabled)) {
            expect(value).toBe(key);
        }
    });

    it("repeats no core or custom feature-switch name", () => {
        const others = new Set([
            ...Object.values(featuresEnabled),
            ...Object.values(customFeaturesEnabled),
        ]);
        expect(Object.values(addonFeaturesEnabled).filter((value) => others.has(value))).toEqual(
            []
        );
    });
});
