/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { describeSegmentMatch, formatRelevancePercent } from "./relevance";
import type { NlpVectorInfo } from "../types";

describe("formatRelevancePercent", () => {
    it("renders 1 - distance as a whole percentage", () => {
        expect(formatRelevancePercent(0.874)).toBe("87%");
        expect(formatRelevancePercent(0.005)).toBe("1%");
    });

    it("clamps to the 0..100 range", () => {
        expect(formatRelevancePercent(1.7)).toBe("100%");
        expect(formatRelevancePercent(-0.2)).toBe("0%");
    });

    it("renders a dash when the hit carries no score", () => {
        expect(formatRelevancePercent(undefined)).toBe("-");
        expect(formatRelevancePercent(null)).toBe("-");
    });
});

const vector = (over: Partial<NlpVectorInfo>): NlpVectorInfo => ({
    distance: 0.1,
    embeddingModelId: "m",
    sourceModalities: ["render"],
    indexedAt: "2026-09-08T00:00:00Z",
    fileClass: "document",
    segmentHits: 0,
    bestSegment: null,
    ...over,
});

describe("describeSegmentMatch", () => {
    it("names the best segment and counts the matching chunks or windows", () => {
        expect(
            describeSegmentMatch(
                vector({
                    segmentHits: 2,
                    bestSegment: {
                        segmentKey: "c000004",
                        segmentKind: "textChunk",
                        segmentLabel: "chunk 5/12 · page 5",
                        segmentStartMs: null,
                        segmentEndMs: null,
                    },
                })
            )
        ).toBe("chunk 5/12 · page 5 (2 matching chunks)");
        expect(
            describeSegmentMatch(
                vector({
                    fileClass: "video",
                    segmentHits: 1,
                    bestSegment: {
                        segmentKey: "t0000080000",
                        segmentKind: "videoTime",
                        segmentLabel: "00:01:20.000–00:01:30.000",
                        segmentStartMs: 80000,
                        segmentEndMs: 90000,
                    },
                })
            )
        ).toBe("00:01:20.000–00:01:30.000 (1 matching window)");
    });

    it("counts alone when the whole-file vector won, and is null without segment hits", () => {
        expect(describeSegmentMatch(vector({ fileClass: "video", segmentHits: 3 }))).toBe(
            "3 matching windows"
        );
        expect(describeSegmentMatch(vector({ segmentHits: 3 }))).toBe("3 matching chunks");
        expect(describeSegmentMatch(vector({}))).toBeNull();
        expect(describeSegmentMatch(undefined)).toBeNull();
    });
});
