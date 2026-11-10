/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { gpuLabel, hasGpus, hoursWord, isStartStalled, leaseRemaining, updatedAgo } from "./labels";

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;

// A fixed instant, so every countdown below reads the same on every run.
const NOW = Date.parse("2026-09-17T12:00:00Z");
const at = (offsetMs: number) => new Date(NOW + offsetMs).toISOString();

const GRACE = { graceHours: 24 };

describe("leaseRemaining", () => {
    it("reads nothing for a row the lease does not cover", () => {
        expect(leaseRemaining({}, GRACE, NOW)).toBeNull();
        expect(leaseRemaining({ leaseExpiresAt: null }, GRACE, NOW)).toBeNull();
        expect(leaseRemaining({ leaseExpiresAt: "the day after tomorrow" }, GRACE, NOW)).toBeNull();
    });

    it("counts more than a day down in days and hours", () => {
        expect(leaseRemaining({ leaseExpiresAt: at(52 * HOUR) }, GRACE, NOW)).toEqual({
            text: "in 2 d 4 h",
            tone: "normal",
        });
        expect(leaseRemaining({ leaseExpiresAt: at(48 * HOUR) }, GRACE, NOW)).toEqual({
            text: "in 2 d",
            tone: "normal",
        });
    });

    it("counts more than an hour down in hours and minutes", () => {
        expect(leaseRemaining({ leaseExpiresAt: at(3 * HOUR + 20 * MINUTE) }, GRACE, NOW)).toEqual({
            text: "in 3 h 20 min",
            tone: "normal",
        });
        expect(leaseRemaining({ leaseExpiresAt: at(2 * HOUR) }, GRACE, NOW)).toEqual({
            text: "in 2 h",
            tone: "normal",
        });
    });

    it("warns for the last hour, down to the last minute", () => {
        expect(leaseRemaining({ leaseExpiresAt: at(40 * MINUTE) }, GRACE, NOW)).toEqual({
            text: "in 40 min",
            tone: "warning",
        });
        expect(leaseRemaining({ leaseExpiresAt: at(HOUR) }, GRACE, NOW)).toEqual({
            text: "in 1 h",
            tone: "warning",
        });
        expect(leaseRemaining({ leaseExpiresAt: at(5000) }, GRACE, NOW)).toEqual({
            text: "in 1 min",
            tone: "warning",
        });
        expect(leaseRemaining({ leaseExpiresAt: at(59 * MINUTE + 55 * 1000) }, GRACE, NOW)).toEqual(
            { text: "in 59 min", tone: "warning" }
        );
        expect(leaseRemaining({ leaseExpiresAt: at(90 * 1000) }, GRACE, NOW)).toEqual({
            text: "in 1 min",
            tone: "warning",
        });
    });

    it("reads as stopping while the desktop is still up past the expiry", () => {
        expect(leaseRemaining({ leaseExpiresAt: at(-10 * MINUTE) }, GRACE, NOW)).toEqual({
            text: "Expired, stopping",
            tone: "expired",
        });
        expect(
            leaseRemaining({ leaseExpiresAt: at(-10 * MINUTE), leaseStoppedAt: null }, GRACE, NOW)
        ).toEqual({ text: "Expired, stopping", tone: "expired" });
    });

    it("counts the grace window down from the later of the expiry and the stop", () => {
        expect(
            leaseRemaining(
                { leaseExpiresAt: at(-5 * HOUR), leaseStoppedAt: at(-5 * HOUR) },
                GRACE,
                NOW
            )
        ).toEqual({ text: "Expired, removed in 19 h", tone: "expired" });
        expect(
            leaseRemaining(
                { leaseExpiresAt: at(-5 * HOUR), leaseStoppedAt: at(-3 * HOUR) },
                GRACE,
                NOW
            )
        ).toEqual({ text: "Expired, removed in 21 h", tone: "expired" });
    });

    it("reads as removing once the grace window is over, and where there is none", () => {
        expect(
            leaseRemaining(
                { leaseExpiresAt: at(-30 * HOUR), leaseStoppedAt: at(-30 * HOUR) },
                GRACE,
                NOW
            )
        ).toEqual({ text: "Expired, removing", tone: "expired" });
        expect(
            leaseRemaining(
                { leaseExpiresAt: at(-1 * HOUR), leaseStoppedAt: at(-1 * HOUR) },
                { graceHours: 0 },
                NOW
            )
        ).toEqual({ text: "Expired, removing", tone: "expired" });
    });

    it("names a unit with every number, so no value reads as a bare count", () => {
        const records = [
            { leaseExpiresAt: at(52 * HOUR) },
            { leaseExpiresAt: at(3 * HOUR + 20 * MINUTE) },
            { leaseExpiresAt: at(40 * MINUTE) },
            { leaseExpiresAt: at(-5 * HOUR), leaseStoppedAt: at(-5 * HOUR) },
        ];
        for (const record of records) {
            expect(leaseRemaining(record, GRACE, NOW)?.text).toMatch(/\d+ (min|h|d)\b/);
        }
    });
});

describe("hoursWord", () => {
    it("reads a single hour in the singular", () => {
        expect(hoursWord(1)).toBe("1 hour");
        expect(hoursWord(8)).toBe("8 hours");
    });
});

describe("isStartStalled", () => {
    it.each([
        ["CREATING", null, "START_STALLED", true],
        ["CREATING", undefined, "START_STALLED", true],
        // The service reports no activity on a stalled row; one that does is moving again.
        ["CREATING", "starting", "START_STALLED", false],
        ["CREATING", null, null, false],
        ["CREATING", null, "START_FAILED", false],
        // A stop or a running desktop moves the row out of CREATING and clears the code.
        ["STOPPED", null, "START_STALLED", false],
        ["ACTIVE", null, "START_STALLED", false],
    ] as const)("%s, activity %s, lastError %s -> %s", (state, activity, lastError, expected) => {
        expect(isStartStalled({ state, activity, lastError })).toBe(expected);
    });
});

describe("updatedAgo", () => {
    const readAt = Date.parse("2026-11-03T12:00:00Z");
    it.each([
        [0, "Updated just now"],
        [9_999, "Updated just now"],
        [10_000, "Updated 10 seconds ago"],
        [59_000, "Updated 59 seconds ago"],
        [60_000, "Updated 1 minute ago"],
        [5 * 60_000 + 30_000, "Updated 5 minutes ago"],
        [3600_000, "Updated 1 hour ago"],
        [3 * 3600_000 + 59 * 60_000, "Updated 3 hours ago"],
    ])("%d ms after the read reads %p", (elapsed, text) => {
        expect(updatedAgo(readAt, readAt + elapsed)).toBe(text);
    });

    it("reads a clock behind the read as just now", () => {
        expect(updatedAgo(readAt, readAt - 5_000)).toBe("Updated just now");
    });
});

describe("hasGpus", () => {
    it("is true only for a type the catalog states a GPU for", () => {
        expect(hasGpus({ gpus: [{ count: 1, name: "A10G" }] })).toBe(true);
        expect(hasGpus({ gpus: [] })).toBe(false);
        expect(hasGpus({ gpus: [{ count: 0 }] })).toBe(false);
        expect(hasGpus({})).toBe(false);
    });

    it("is true for a type given part of a GPU", () => {
        expect(hasGpus({ gpus: [{ count: 1, name: "L4", share: 0.125 }] })).toBe(true);
    });
});

describe("gpuLabel", () => {
    it.each([
        [
            { count: 1, manufacturer: "NVIDIA", name: "A10G", memoryGiB: 22.35 },
            "1 × NVIDIA A10G (22.35 GiB)",
        ],
        [
            { count: 1, manufacturer: "NVIDIA", name: "L4", memoryGiB: 2.79, share: 0.125 },
            "1/8 × NVIDIA L4 (2.79 GiB)",
        ],
        [{ count: 2, manufacturer: "NVIDIA", name: "L4", share: 0.5 }, "2 × 1/2 × NVIDIA L4"],
        [{ count: 1, name: "L4", share: 0.375 }, "37.5% × L4"],
        [{ count: 1, name: "L4", share: 1 }, "1 × L4"],
    ])("reads %j as %s", (gpu, text) => {
        expect(gpuLabel(gpu)).toBe(text);
    });
});
