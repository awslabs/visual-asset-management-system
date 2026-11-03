/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` validates the embedding model against the table the backend's embedding adapter reads
 * (`backend/backend/common/vectorsearch/embeddingModels.json`), so the two cannot disagree about which
 * model and width an index may be built from.
 *
 * The rows are pinned here and in `backend/tests/common/vectorsearch/test_embeddings.py`, so a table
 * edit is a visible change on both language sides; the dispatch cases are the Python suite's, so the
 * TypeScript and Python regular-expression engines are held to the same answers.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";

const TABLE = path.resolve(
    __dirname,
    "../../../backend/backend/common/vectorsearch/embeddingModels.json"
);

describe("the shared embedding model table", () => {
    test("config.ts reads the backend's table as it is on disk", () => {
        const onDisk = JSON.parse(fs.readFileSync(TABLE, "utf8")).families;
        expect(Config.EMBEDDING_MODEL_FAMILIES).toEqual(onDisk);
    });

    test("the shipped rows", () => {
        expect(
            Config.EMBEDDING_MODEL_FAMILIES.map((f) => [f.family, f.dimensions, f.maxInputChars])
        ).toEqual([
            ["titan-v2", [256, 512, 1024], 30000],
            ["nova-mme", [256, 384, 1024, 3072], 8192],
            ["cohere-v3", [1024], 2048],
        ]);
    });

    test("the default embedding model and width are in the table", () => {
        const family = Config.embeddingModelFamily(Config.VECTOR_SEARCH_DEFAULT_EMBEDDING_MODEL_ID);
        expect(family?.family).toBe("titan-v2");
        expect(family?.dimensions).toContain(Config.VECTOR_SEARCH_DEFAULT_EMBEDDING_DIMENSIONS);
    });

    test("each family's example id belongs to the family", () => {
        for (const entry of Config.EMBEDDING_MODEL_FAMILIES) {
            expect(Config.embeddingModelFamily(entry.exampleModelId)?.family).toBe(entry.family);
        }
    });

    test.each([
        ["amazon.titan-embed-text-v2:0", "titan-v2"],
        ["amazon.nova-2-multimodal-embeddings-v1:0", "nova-mme"],
        ["cohere.embed-multilingual-v3", "cohere-v3"],
        ["cohere.embed-english-v3", "cohere-v3"],
    ])("%s is dispatched to %s, as the adapter does", (modelId, family) => {
        expect(Config.embeddingModelFamily(modelId)?.family).toBe(family);
    });

    test.each([
        "cohere.embed-v4:0",
        "amazon.titan-embed-image-v1",
        "anthropic.claude-haiku-4-5",
        "us.cohere.embed-english-v3",
        "",
    ])("%p belongs to no family, as the adapter decides", (modelId) => {
        expect(Config.embeddingModelFamily(modelId)).toBeUndefined();
    });
});
