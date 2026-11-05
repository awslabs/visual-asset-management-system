/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { SearchMode } from "../types";

/**
 * Which search engines a deployment offers, from the two feature switches: NOOPENSEARCH (negative,
 * pushed when no OpenSearch mode is enabled) and VECTORSEARCH (positive).
 */
export type SearchModeCase = "both" | "nlp-only" | "keyword-only" | "none";

export function resolveSearchModeCase(
    noOpenSearch: boolean,
    vectorSearch: boolean
): SearchModeCase {
    if (vectorSearch && !noOpenSearch) return "both";
    if (vectorSearch) return "nlp-only";
    if (!noOpenSearch) return "keyword-only";
    return "none";
}

/** The mode the container searches with: the preference decides only when both engines are on. */
export function effectiveSearchMode(
    modeCase: SearchModeCase,
    preferred: SearchMode | undefined
): SearchMode {
    if (modeCase === "nlp-only") return "nlp";
    if (modeCase === "both") return preferred === "nlp" ? "nlp" : "keyword";
    return "keyword";
}
