/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Thin localStorage cache replacing Amplify Cache.
 * Drop-in API-compatible replacement with same setItem/getItem/removeItem interface.
 */
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
}

export const appCache = new AppCache();
