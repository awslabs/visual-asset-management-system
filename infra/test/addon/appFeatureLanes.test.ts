/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The feature-switch lanes publish disjoint values.
 *
 * /api/secure-config publishes the core VAMS_APP_FEATURES values the core stack enables, the
 * ADDON_APP_FEATURES values AddonBuilderNestedStack enables, and the CUSTOM_APP_FEATURES values a
 * fork's buildCustomResources() returns, as one list. A value declared in two lanes would let one
 * stack switch on a feature the other owns, so an exact duplicate fails here, the same way the
 * custom stack refuses a core or add-on value at synth. The assertions hold for any content, so they
 * keep holding once a fork fills in customAppFeatures.ts.
 */

import { ADDON_APP_FEATURES } from "../../common/addonAppFeatures";
import { CUSTOM_APP_FEATURES } from "../../common/customAppFeatures";
import { VAMS_APP_FEATURES } from "../../common/vamsAppFeatures";

const LANES: Record<string, string[]> = {
    "VAMS_APP_FEATURES (common/vamsAppFeatures.ts)": Object.values(VAMS_APP_FEATURES),
    "ADDON_APP_FEATURES (common/addonAppFeatures.ts)": Object.values(ADDON_APP_FEATURES),
    "CUSTOM_APP_FEATURES (common/customAppFeatures.ts)": Object.values(CUSTOM_APP_FEATURES),
};

/** Every value declared in more than one lane, with the lanes that declare it. */
function duplicates(lanes: Record<string, string[]>): Record<string, string[]> {
    const owners: Record<string, string[]> = {};
    for (const [lane, values] of Object.entries(lanes)) {
        for (const value of new Set(values)) {
            (owners[value] = owners[value] ?? []).push(lane);
        }
    }
    return Object.fromEntries(Object.entries(owners).filter(([, lanesOf]) => lanesOf.length > 1));
}

describe("feature-switch lanes", () => {
    test("no value is declared in two lanes", () => {
        expect(duplicates(LANES)).toEqual({});
    });

    test("the Physna switch is an add-on switch, not a core one", () => {
        expect(Object.values(ADDON_APP_FEATURES)).toContain("PHYSNA_ADDON");
        expect(Object.values(VAMS_APP_FEATURES)).not.toContain("PHYSNA_ADDON");
    });

    test("[control] the detector reports a value two lanes declare", () => {
        expect(
            duplicates({ core: ["GOVCLOUD", "NOOPENSEARCH"], custom: ["CUSTOM_X", "GOVCLOUD"] })
        ).toEqual({ GOVCLOUD: ["core", "custom"] });
        expect(duplicates({ core: ["GOVCLOUD"], custom: ["CUSTOM_X"] })).toEqual({});
    });
});
