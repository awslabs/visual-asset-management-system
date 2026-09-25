/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The vector index name `getConfig()` derives from `app.vectorSearch`, for the T1 storage tests.
 *
 * `templateSynth.ts` builds its Config from a raw shipped template without calling `getConfig()` and
 * fills `vectorIndexName` itself from `deriveVectorIndexName`. A T1 arm that mutates the embedding
 * model or width needs the name that mutated synth should carry, derived from the arm's own
 * `vectorSearch` block rather than read back from the harness. This module is that derivation over
 * the same `config.ts` export — the repository has one slug rule — and
 * `vectorIndexNameHarnessParity.test.ts` pins it to what `getConfig()` produces for the same template.
 */

import { deriveVectorIndexName, slugModelId } from "../../config/config";

export { slugModelId };

export interface VectorSearchNaming {
    embeddingModelId: string;
    embeddingDimensions: number;
}

/** `vec-<slug(embeddingModelId)>-<embeddingDimensions>`; undefined when the config has no vectorSearch block. */
export function vectorIndexNameFor(
    vectorSearch: VectorSearchNaming | undefined
): string | undefined {
    if (!vectorSearch) return undefined;
    return deriveVectorIndexName(vectorSearch.embeddingModelId, vectorSearch.embeddingDimensions);
}
