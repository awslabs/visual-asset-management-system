/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    availableProviders,
    conditionHolds,
    defaultProviderId,
    isProviderAvailable,
    providerLabel,
} from "./providerAvailability";
import type { SearchProviderConfig } from "./types";

const assetList: SearchProviderConfig = {
    id: "asset-list",
    name: "Basic Asset List",
    componentPath: "AssetListProvider/AssetListProviderComponent",
    priority: 100,
    enabled: true,
    availability: { always: true },
};
const unified: SearchProviderConfig = {
    id: "unified-search",
    name: "Primary Search",
    componentPath: "UnifiedSearchProvider/UnifiedSearchProviderComponent",
    priority: 10,
    enabled: true,
    availability: {
        anyOf: [{ featureEnabled: "VECTORSEARCH" }, { featureDisabled: "NOOPENSEARCH" }],
    },
};

describe("conditionHolds", () => {
    it("featureEnabled needs the flag present; featureDisabled needs it absent", () => {
        expect(conditionHolds({ featureEnabled: "X" }, ["X"])).toBe(true);
        expect(conditionHolds({ featureEnabled: "X" }, [])).toBe(false);
        expect(conditionHolds({ featureDisabled: "X" }, [])).toBe(true);
        expect(conditionHolds({ featureDisabled: "X" }, ["X"])).toBe(false);
    });
});

describe("isProviderAvailable", () => {
    it("always-available providers ignore the feature list", () => {
        expect(isProviderAvailable(assetList, [])).toBe(true);
        expect(isProviderAvailable(assetList, ["NOOPENSEARCH"])).toBe(true);
    });

    it("anyOf holds when one condition holds", () => {
        // OpenSearch on, vector off
        expect(isProviderAvailable(unified, [])).toBe(true);
        // OpenSearch off, vector on
        expect(isProviderAvailable(unified, ["NOOPENSEARCH", "VECTORSEARCH"])).toBe(true);
        // Both on
        expect(isProviderAvailable(unified, ["VECTORSEARCH"])).toBe(true);
        // Neither engine
        expect(isProviderAvailable(unified, ["NOOPENSEARCH"])).toBe(false);
    });

    it("a disabled catalog entry is never available", () => {
        expect(isProviderAvailable({ ...assetList, enabled: false }, [])).toBe(false);
    });
});

describe("availableProviders / defaultProviderId", () => {
    it("orders by priority, lowest number first, and defaults to the first", () => {
        const providers = availableProviders([assetList, unified], []);
        expect(providers.map((p) => p.id)).toEqual(["unified-search", "asset-list"]);
        expect(defaultProviderId(providers)).toBe("unified-search");
    });

    it("drops unavailable providers and defaults to what remains", () => {
        const providers = availableProviders([assetList, unified], ["NOOPENSEARCH"]);
        expect(providers.map((p) => p.id)).toEqual(["asset-list"]);
        expect(defaultProviderId(providers)).toBe("asset-list");
        expect(defaultProviderId([])).toBeUndefined();
    });
});

describe("providerLabel", () => {
    it("substitutes the Asset synonym tokens and leaves other names alone", () => {
        // Synonyms default to "Asset"/"Assets"; the substitution is what makes a renamed
        // deployment show its own word in the tab strip.
        expect(providerLabel(assetList)).toBe("Basic Asset List");
        expect(providerLabel({ ...assetList, name: "All Assets" })).toBe("All Assets");
        expect(providerLabel(unified)).toBe("Primary Search");
        expect(providerLabel({ ...unified, name: "Assetization" })).toBe("Assetization");
    });
});
