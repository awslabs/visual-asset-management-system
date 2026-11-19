/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { NlpVectorInfo } from "../types";

/** A natural-language hit's `_score` (1 - cosine distance, already clamped server-side) as a percentage. */
export function formatRelevancePercent(score: number | undefined | null): string {
    if (score == null || Number.isNaN(score)) return "-";
    const clamped = Math.min(1, Math.max(0, score));
    return `${Math.round(clamped * 100)}%`;
}

/**
 * The popover's segment line for a hit matched through its segment vectors: the best window's or
 * chunk's label (when a segment, not the whole-file vector, scored best) and how many matched.
 * `null` when no segment vector matched. Video windows are "windows", everything else "chunks".
 */
export function describeSegmentMatch(vector: NlpVectorInfo | undefined | null): string | null {
    if (!vector || !(vector.segmentHits > 0)) return null;
    const kind = vector.bestSegment?.segmentKind;
    const isWindow =
        kind === "videoTime" || kind === "animationTime" || (!kind && vector.fileClass === "video");
    const unit = isWindow ? "window" : "chunk";
    const count = `${vector.segmentHits} matching ${unit}${vector.segmentHits === 1 ? "" : "s"}`;
    const label = vector.bestSegment?.segmentLabel;
    return label ? `${label} (${count})` : count;
}
