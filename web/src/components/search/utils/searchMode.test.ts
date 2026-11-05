/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { effectiveSearchMode, resolveSearchModeCase } from "./searchMode";

describe("resolveSearchModeCase", () => {
    // (noOpenSearch, vectorSearch) -> case. NOOPENSEARCH is a negative switch, VECTORSEARCH positive.
    it.each([
        [false, true, "both"],
        [true, true, "nlp-only"],
        [false, false, "keyword-only"],
        [true, false, "none"],
    ] as const)("noOpenSearch=%s vectorSearch=%s -> %s", (noOpenSearch, vectorSearch, expected) => {
        expect(resolveSearchModeCase(noOpenSearch, vectorSearch)).toBe(expected);
    });
});

describe("effectiveSearchMode", () => {
    it("forces nlp when only vector search is on, whatever the preference says", () => {
        expect(effectiveSearchMode("nlp-only", "keyword")).toBe("nlp");
        expect(effectiveSearchMode("nlp-only", undefined)).toBe("nlp");
    });

    it("forces keyword when only OpenSearch is on", () => {
        expect(effectiveSearchMode("keyword-only", "nlp")).toBe("keyword");
        expect(effectiveSearchMode("none", "nlp")).toBe("keyword");
    });

    it("honours the preference when both are on and defaults to keyword", () => {
        expect(effectiveSearchMode("both", "nlp")).toBe("nlp");
        expect(effectiveSearchMode("both", "keyword")).toBe("keyword");
        expect(effectiveSearchMode("both", undefined)).toBe("keyword");
    });
});
