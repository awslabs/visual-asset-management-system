/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search total helpers. Both hits.total.value and aggregationTotal count only records the caller
 * can open; the display takes the larger and marks a minimum ("gte") with "+".
 */
import { formatTotalResultCount, getTotalResultCount, isTotalLowerBound } from "./types";

function result(value: number, relation: string, aggregationTotal?: number | null): any {
    return { hits: { hits: [], total: { value, relation } }, aggregationTotal };
}

describe("search totals", () => {
    it("is zero without a result", () => {
        expect(getTotalResultCount(null)).toBe(0);
        expect(isTotalLowerBound(undefined)).toBe(false);
    });

    it("takes the larger of the hit total and the aggregation total", () => {
        expect(getTotalResultCount(result(300, "eq", 300))).toBe(300);
        expect(getTotalResultCount(result(40, "eq", 30))).toBe(40);
        expect(getTotalResultCount(result(20, "eq", 120))).toBe(120);
        expect(getTotalResultCount(result(7, "eq", null))).toBe(7);
    });

    it("marks a minimum with +", () => {
        expect(isTotalLowerBound(result(30, "gte"))).toBe(true);
        expect(formatTotalResultCount(result(1200, "gte", 1100))).toBe(
            `${(1200).toLocaleString()}+`
        );
        expect(formatTotalResultCount(result(4, "eq", 4))).toBe("4");
    });
});
