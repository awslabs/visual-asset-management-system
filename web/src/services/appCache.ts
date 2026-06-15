/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Thin localStorage cache replacing Amplify Cache.
 * Drop-in API-compatible replacement with same setItem/getItem/removeItem interface.
 *
 * Entries written with setItemWithExpiry are wrapped in an envelope carrying an
 * expiry timestamp; getItemWithExpiry returns null once the entry is stale (the
 * stale entry is removed). Plain setItem/getItem entries never expire.
 */
interface ExpiringCacheEnvelope {
    __vamsCacheExpiresAt: number;
    value: any;
}

class AppCache {
    setItem(key: string, value: any): void {
        try {
            localStorage.setItem(`vams_cache_${key}`, JSON.stringify(value));
        } catch (error) {
            console.error(`AppCache: Failed to set item '${key}':`, error);
        }
    }

    getItem(key: string): any {
        try {
            const item = localStorage.getItem(`vams_cache_${key}`);
            return item ? JSON.parse(item) : null;
        } catch (error) {
            console.error(`AppCache: Failed to get item '${key}':`, error);
            return null;
        }
    }

    removeItem(key: string): void {
        localStorage.removeItem(`vams_cache_${key}`);
    }

    /** Store a value that expires after ttlMillis. Read it back with getItemWithExpiry. */
    setItemWithExpiry(key: string, value: any, ttlMillis: number): void {
        const envelope: ExpiringCacheEnvelope = {
            __vamsCacheExpiresAt: Date.now() + ttlMillis,
            value: value,
        };
        this.setItem(key, envelope);
    }

    /** Read a value stored with setItemWithExpiry; returns null (and evicts) when expired. */
    getItemWithExpiry(key: string): any {
        const envelope = this.getItem(key);
        if (!envelope || typeof envelope.__vamsCacheExpiresAt !== "number") {
            return null;
        }
        if (Date.now() > envelope.__vamsCacheExpiresAt) {
            this.removeItem(key);
            return null;
        }
        return envelope.value;
    }
}

export const appCache = new AppCache();
