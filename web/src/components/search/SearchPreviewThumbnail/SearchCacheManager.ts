/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * SearchCacheManager - A sophisticated caching system for search page preview thumbnails
 *
 * Features:
 * - Three separate caches: Asset Details, File Details, Preview Images
 * - LRU (Least Recently Used) eviction policy
 * - TTL (Time To Live) expiration
 * - Size-based eviction for preview images (200MB limit)
 * - Singleton pattern for persistence across tab switches
 * - Memory-based (clears on page reload)
 */

interface CacheEntry<T> {
    data: T;
    timestamp: number;
    lastAccessed: number;
    size?: number; // For preview images
}

interface AssetDetails {
    previewKey: string;
    downloadType: "assetPreview" | "assetFile";
}

interface FileDetails {
    previewKey: string;
    downloadType: "assetPreview" | "assetFile";
    hasPreview: boolean;
}

interface PreviewImage {
    dataUrl: string;
}

class SearchCacheManager {
    // Cache storage
    private assetCache: Map<string, CacheEntry<AssetDetails>>;
    private fileCache: Map<string, CacheEntry<FileDetails>>;
    private previewCache: Map<string, CacheEntry<PreviewImage>>;

    // Track total size of preview cache
    private previewCacheSize: number;

    // Configuration constants
    private readonly MAX_ASSET_ENTRIES = 1000;
    private readonly MAX_FILE_ENTRIES = 1000;
    private readonly MAX_PREVIEW_SIZE_BYTES = 200 * 1024 * 1024; // 200MB
    private readonly ASSET_TTL_MS = 60 * 1000; // 60 seconds
    private readonly FILE_TTL_MS = 60 * 1000; // 60 seconds
    private readonly PREVIEW_TTL_MS = 5 * 60 * 1000; // 5 minutes

    constructor() {
        this.assetCache = new Map();
        this.fileCache = new Map();
        this.previewCache = new Map();
        this.previewCacheSize = 0;

        console.log("[SearchCacheManager] Initialized");
    }

    // ==================== Asset Cache Methods ====================

    /**
     * Get asset details from cache
     * Returns null if not found or expired
     */
    getAsset(key: string): AssetDetails | null {
        const entry = this.assetCache.get(key);

        if (!entry) {
            console.log(`[Cache CHECK] Asset key: ${key} - NOT FOUND in cache`);
            return null;
        }

        // Check if expired
        const now = Date.now();
        const age = now - entry.timestamp;
        if (age > this.ASSET_TTL_MS) {
            console.log(
                `[Cache CHECK] Asset key: ${key} - EXPIRED (age: ${age}ms, TTL: ${this.ASSET_TTL_MS}ms)`
            );
            this.assetCache.delete(key);
            return null;
        }

        console.log(`[Cache CHECK] Asset key: ${key} - FOUND (age: ${age}ms)`);

        // Update last accessed time (for LRU)
        entry.lastAccessed = now;

        return entry.data;
    }

    /**
     * Store asset details in cache
     */
    setAsset(key: string, data: AssetDetails): void {
        const now = Date.now();

        // Check if we need to evict (LRU)
        if (this.assetCache.size >= this.MAX_ASSET_ENTRIES && !this.assetCache.has(key)) {
            this.evictLRU(this.assetCache);
        }

        this.assetCache.set(key, {
            data,
            timestamp: now,
            lastAccessed: now,
        });

        console.log(
            `[Cache SET] Asset details for key: ${key}, cache size: ${this.assetCache.size}`
        );
    }

    // ==================== File Cache Methods ====================

    /**
     * Get file details from cache
     * Returns null if not found or expired
     */
    getFile(key: string): FileDetails | null {
        const entry = this.fileCache.get(key);

        if (!entry) {
            return null;
        }

        // Check if expired
        const now = Date.now();
        if (now - entry.timestamp > this.FILE_TTL_MS) {
            this.fileCache.delete(key);
            return null;
        }

        // Update last accessed time (for LRU)
        entry.lastAccessed = now;

        return entry.data;
    }

    /**
     * Store file details in cache
     */
    setFile(key: string, data: FileDetails): void {
        const now = Date.now();

        // Check if we need to evict (LRU)
        if (this.fileCache.size >= this.MAX_FILE_ENTRIES && !this.fileCache.has(key)) {
            this.evictLRU(this.fileCache);
        }

        this.fileCache.set(key, {
            data,
            timestamp: now,
            lastAccessed: now,
        });

        console.log(`[Cache SET] File details for key: ${key}, cache size: ${this.fileCache.size}`);
    }

    // ==================== Preview Cache Methods ====================

    /**
     * Get preview image from cache
     * Returns null if not found or expired
     */
    getPreview(key: string): PreviewImage | null {
        const entry = this.previewCache.get(key);

        if (!entry) {
            return null;
        }

        // Check if expired
        const now = Date.now();
        if (now - entry.timestamp > this.PREVIEW_TTL_MS) {
            // Remove from cache and update size
            this.previewCache.delete(key);
            if (entry.size) {
                this.previewCacheSize -= entry.size;
            }
            return null;
        }

        // Update last accessed time (for LRU)
        entry.lastAccessed = now;

        return entry.data;
    }

    /**
     * Store preview image in cache
     * Evicts old entries if size limit is exceeded
     */
    setPreview(key: string, data: PreviewImage, sizeBytes: number): void {
        const now = Date.now();

        // If updating existing entry, subtract old size first
        const existingEntry = this.previewCache.get(key);
        if (existingEntry && existingEntry.size) {
            this.previewCacheSize -= existingEntry.size;
        }

        // Evict entries until we have enough space
        while (
            this.previewCacheSize + sizeBytes > this.MAX_PREVIEW_SIZE_BYTES &&
            this.previewCache.size > 0
        ) {
            this.evictLRU(this.previewCache, true);
        }

        // Add new entry
        this.previewCache.set(key, {
            data,
            timestamp: now,
            lastAccessed: now,
            size: sizeBytes,
        });

        this.previewCacheSize += sizeBytes;
    }

    // ==================== Utility Methods ====================

    /**
     * Evict the least recently used entry from a cache
     */
    private evictLRU<T>(cache: Map<string, CacheEntry<T>>, updateSize: boolean = false): void {
        let oldestKey: string | null = null;
        let oldestTime = Infinity;

        // Find the least recently accessed entry
        // Use Array.from to avoid downlevelIteration requirement
        const entries = Array.from(cache.entries());
        for (const [key, entry] of entries) {
            if (entry.lastAccessed < oldestTime) {
                oldestTime = entry.lastAccessed;
                oldestKey = key;
            }
        }

        // Remove it
        if (oldestKey) {
            const entry = cache.get(oldestKey);
            cache.delete(oldestKey);

            // Update size if this is preview cache
            if (updateSize && entry && entry.size) {
                this.previewCacheSize -= entry.size;
            }
        }
    }

    /**
     * Estimate size of a data URL in bytes
     */
    estimateDataUrlSize(dataUrl: string): number {
        // Remove data URL prefix (e.g., "data:image/png;base64,")
        const base64Data = dataUrl.split(",")[1] || dataUrl;

        // Base64 encoding increases size by ~33%
        // So actual binary size is approximately 75% of base64 length
        return Math.ceil((base64Data.length * 3) / 4);
    }

    /**
     * Clear all caches
     * Note: This is called automatically on page reload (memory-based)
     */
    clear(): void {
        this.assetCache.clear();
        this.fileCache.clear();
        this.previewCache.clear();
        this.previewCacheSize = 0;

        console.log("[SearchCacheManager] All caches cleared");
    }

    /**
     * Get cache statistics (for debugging)
     */
    getStats() {
        return {
            assets: {
                count: this.assetCache.size,
                maxCount: this.MAX_ASSET_ENTRIES,
                ttl: this.ASSET_TTL_MS,
            },
            files: {
                count: this.fileCache.size,
                maxCount: this.MAX_FILE_ENTRIES,
                ttl: this.FILE_TTL_MS,
            },
            previews: {
                count: this.previewCache.size,
                sizeMB: (this.previewCacheSize / (1024 * 1024)).toFixed(2),
                maxSizeMB: this.MAX_PREVIEW_SIZE_BYTES / (1024 * 1024),
                ttl: this.PREVIEW_TTL_MS,
            },
        };
    }
}

// Export singleton instance
// This persists across tab switches but clears on page reload (memory-based)
let instance: SearchCacheManager | null = null;

function getInstance(): SearchCacheManager {
    if (!instance) {
        instance = new SearchCacheManager();
        console.log("[SearchCacheManager] Creating NEW singleton instance");
    } else {
        console.log("[SearchCacheManager] Reusing existing singleton instance");
    }
    return instance;
}

// Export the singleton instance directly, not the function
const cacheManagerInstance = getInstance();
export default cacheManagerInstance;
export type { AssetDetails, FileDetails, PreviewImage };
