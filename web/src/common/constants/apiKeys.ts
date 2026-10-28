/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Maximum lifetime of user-level (self-service) API keys, in days from the
// key's creation date. User-created keys require an expiration within this
// window, and later edits cannot extend it past creation + this many days --
// after that the user must create a new key (rotation). Mirrors
// USER_API_KEY_MAX_EXPIRATION_DAYS in backend/backend/models/apiKeys.py;
// keep the two in sync.
export const USER_API_KEY_MAX_EXPIRATION_DAYS = 365;

const DAY_MS = 24 * 60 * 60 * 1000;
// A new key's creation time is set when the request reaches the backend, so the latest expiry
// offered for a new key stays this far inside the window.
const NEW_KEY_EXPIRY_MARGIN_MS = 60 * 1000;
/** The default expiry time of day (UTC). */
export const END_OF_DAY_TIME = "23:59:59";

/** Parse an ISO 8601 instant, reading a value with no UTC offset as UTC, as the backend does. */
function parseUtcMs(value: string): number {
    let text = value.trim().replace(/(\.\d{3})\d+/, "$1");
    if (/T/.test(text) && !/(Z|[+-]\d{2}:?\d{2})$/.test(text)) text += "Z";
    return Date.parse(text);
}

/**
 * The latest expiry instant (ms) a user-level key may have: the key's createdAt plus
 * USER_API_KEY_MAX_EXPIRATION_DAYS for an existing key, or now plus that window minus a
 * one-minute margin for a key not yet created.
 */
export function latestUserApiKeyExpiryMs(
    createdAt?: string | null,
    nowMs: number = Date.now()
): number {
    const createdMs = createdAt ? parseUtcMs(createdAt) : NaN;
    if (!Number.isNaN(createdMs)) {
        return createdMs + USER_API_KEY_MAX_EXPIRATION_DAYS * DAY_MS;
    }
    return nowMs + USER_API_KEY_MAX_EXPIRATION_DAYS * DAY_MS - NEW_KEY_EXPIRY_MARGIN_MS;
}

/**
 * isDateEnabled for a user-level key's expiry DatePicker. The picker passes local midnight of
 * the shown date and submits that calendar date as a UTC date, so the calendar date is compared
 * with the UTC date of the latest instant. Dates up to and including today stay disabled.
 */
export function isUserApiKeyExpiryDateEnabled(
    date: Date,
    latestMs: number,
    nowMs: number = Date.now()
): boolean {
    const today = new Date(nowMs);
    today.setHours(0, 0, 0, 0);
    if (!(date > today)) return false;
    const picked = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
    const latest = new Date(latestMs);
    const lastDay = Date.UTC(latest.getUTCFullYear(), latest.getUTCMonth(), latest.getUTCDate());
    return picked <= lastDay;
}

/**
 * The latest HH:MM:SS (UTC) allowed on a chosen expiry date ("YYYY-MM-DD"): the latest
 * instant's time on its own date, and 23:59:59 on every earlier date.
 */
export function latestUserApiKeyExpiryTime(dateValue: string, latestMs: number): string {
    const latest = new Date(latestMs).toISOString();
    return dateValue === latest.slice(0, 10) ? latest.slice(11, 19) : END_OF_DAY_TIME;
}

// Keys requested per page of an API key listing. Matches the backend's own
// default page size (API_KEY_LISTING_PAGE_SIZE in
// backend/backend/handlers/auth/apiKeyService.py), so an ordinary deployment
// answers in one page and the table's filter and sort still see every key;
// paging takes over only beyond that.
export const API_KEY_LISTING_PAGE_SIZE = 1000;
