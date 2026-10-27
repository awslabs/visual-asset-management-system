/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A self-service key's expiry may be at most USER_API_KEY_MAX_EXPIRATION_DAYS after its creation.
 * The last date the picker offers, combined with the time the form allows on it, must stay
 * within that window. Dates are built as the picker builds them (local midnight), so the results
 * do not depend on the time zone the suite runs in.
 */

import {
    END_OF_DAY_TIME,
    USER_API_KEY_MAX_EXPIRATION_DAYS,
    isUserApiKeyExpiryDateEnabled,
    latestUserApiKeyExpiryMs,
    latestUserApiKeyExpiryTime,
} from "./apiKeys";

const DAY_MS = 24 * 60 * 60 * 1000;
const NOW = Date.parse("2026-03-10T10:00:00Z");

/** The local-midnight Date the picker passes for a calendar date "YYYY-MM-DD". */
function pickerDate(value: string): Date {
    const [y, m, d] = value.split("-").map(Number);
    return new Date(y, m - 1, d);
}

function lastEnabledDate(latestMs: number): string {
    // Walk forward from tomorrow and return the last calendar date the check accepts.
    let last = "";
    for (let offset = 1; offset <= USER_API_KEY_MAX_EXPIRATION_DAYS + 3; offset++) {
        const value = new Date(NOW + offset * DAY_MS).toISOString().slice(0, 10);
        if (isUserApiKeyExpiryDateEnabled(pickerDate(value), latestMs, NOW)) last = value;
    }
    return last;
}

describe.each([
    ["a new key", null, NOW + USER_API_KEY_MAX_EXPIRATION_DAYS * DAY_MS],
    [
        "an existing key",
        "2026-02-01T08:30:15.123456+00:00",
        Date.parse("2026-02-01T08:30:15.123Z") + USER_API_KEY_MAX_EXPIRATION_DAYS * DAY_MS,
    ],
])("user API key expiry limits for %s", (_label, createdAt, windowEndMs) => {
    const latestMs = latestUserApiKeyExpiryMs(createdAt, NOW);

    it("never allows an expiry past the end of the window", () => {
        expect(latestMs).toBeLessThanOrEqual(windowEndMs);
    });

    it("offers the latest instant's UTC date as the last date, and not the day after", () => {
        const latestDate = new Date(latestMs).toISOString().slice(0, 10);
        expect(lastEnabledDate(latestMs)).toBe(latestDate);
        const dayAfter = new Date(latestMs + DAY_MS).toISOString().slice(0, 10);
        expect(isUserApiKeyExpiryDateEnabled(pickerDate(dayAfter), latestMs, NOW)).toBe(false);
    });

    it("keeps the last date with its latest allowed time inside the window", () => {
        const date = lastEnabledDate(latestMs);
        const time = latestUserApiKeyExpiryTime(date, latestMs);
        expect(Date.parse(`${date}T${time}Z`)).toBeLessThanOrEqual(windowEndMs);
        // The default time would have been past the window on that date.
        expect(Date.parse(`${date}T${END_OF_DAY_TIME}Z`)).toBeGreaterThan(windowEndMs);
    });

    it("allows the whole day on earlier dates", () => {
        const earlier = new Date(latestMs - DAY_MS).toISOString().slice(0, 10);
        expect(latestUserApiKeyExpiryTime(earlier, latestMs)).toBe(END_OF_DAY_TIME);
    });
});

describe("user API key expiry lower bound", () => {
    it("does not enable today", () => {
        const today = new Date(NOW);
        today.setHours(0, 0, 0, 0);
        expect(isUserApiKeyExpiryDateEnabled(today, latestUserApiKeyExpiryMs(null, NOW), NOW)).toBe(
            false
        );
    });

    it("reads an offset-less createdAt as UTC", () => {
        expect(latestUserApiKeyExpiryMs("2026-02-01T08:30:15", NOW)).toBe(
            Date.parse("2026-02-01T08:30:15Z") + USER_API_KEY_MAX_EXPIRATION_DAYS * DAY_MS
        );
    });
});
