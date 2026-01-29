/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Authenticated LoadingManager for ThreeJS
 * Handles loading of external dependencies (textures, .bin files, etc.) with authentication
 */

import { getDualAuthorizationHeader } from "../../../../utils/authTokenUtils";

interface LoadingManagerConfig {
    assetId: string;
    databaseId: string;
    baseFileKey: string; // The main file's key (e.g., "assetId/model.gltf")
    apiEndpoint: string;
}

/**
 * Create an authenticated LoadingManager for ThreeJS loaders
 * This manager intercepts resource requests and downloads them with authentication
 */
export async function createAuthenticatedLoadingManager(
    config: LoadingManagerConfig
): Promise<any> {
    const THREE = (window as any).THREE;
    if (!THREE) {
        throw new Error("THREE not loaded");
    }

    const manager = new THREE.LoadingManager();
    const authHeader = await getDualAuthorizationHeader();

    // Cache for loaded resources to avoid duplicate downloads
    const resourceCache = new Map<string, string>();

    // Extract base directory from the main file key
    const baseDir = config.baseFileKey.substring(0, config.baseFileKey.lastIndexOf("/"));

    manager.setURLModifier((url: string) => {
        // If it's already a blob URL or data URL, return as-is
        if (url.startsWith("blob:") || url.startsWith("data:")) {
            return url;
        }

        // Check cache first
        if (resourceCache.has(url)) {
            console.log(`LoadingManager: Using cached resource: ${url}`);
            return resourceCache.get(url)!;
        }

        console.log(`LoadingManager: Intercepted request for: ${url}`);

        // Resolve the relative path to full asset key
        let resourceKey: string;
        if (url.startsWith("./") || url.startsWith("../")) {
            // Relative path - resolve against base directory
            const parts = baseDir.split("/");
            const urlParts = url.split("/");

            // Handle ../ (parent directory)
            for (const part of urlParts) {
                if (part === "..") {
                    parts.pop();
                } else if (part !== ".") {
                    parts.push(part);
                }
            }

            resourceKey = parts.join("/");
        } else if (url.startsWith("/")) {
            // Absolute path from asset root
            resourceKey = `${config.assetId}${url}`;
        } else {
            // Relative to base directory
            resourceKey = `${baseDir}/${url}`;
        }

        console.log(`LoadingManager: Resolved to asset key: ${resourceKey}`);

        // Download the resource asynchronously and return a promise
        // Note: THREE.LoadingManager expects synchronous URL modification,
        // so we need to use a different approach - return the original URL
        // and let the loader's onLoad callback handle the authenticated fetch
        return url;
    });

    // Override the default loading behavior
    const originalLoad = manager.itemStart;
    manager.itemStart = function (url: string) {
        console.log(`LoadingManager: Starting load: ${url}`);
        originalLoad.call(this, url);
    };

    // Custom loader function that can be used by loaders
    (manager as any).loadWithAuth = async (url: string): Promise<ArrayBuffer> => {
        // Check cache
        if (resourceCache.has(url)) {
            const blobUrl = resourceCache.get(url)!;
            const response = await fetch(blobUrl);
            return await response.arrayBuffer();
        }

        // Resolve path
        let resourceKey: string;
        if (url.startsWith("./") || url.startsWith("../")) {
            const parts = baseDir.split("/");
            const urlParts = url.split("/");

            for (const part of urlParts) {
                if (part === "..") {
                    parts.pop();
                } else if (part !== ".") {
                    parts.push(part);
                }
            }

            resourceKey = parts.join("/");
        } else if (url.startsWith("/")) {
            resourceKey = `${config.assetId}${url}`;
        } else {
            resourceKey = `${baseDir}/${url}`;
        }

        console.log(`LoadingManager: Downloading dependency: ${resourceKey}`);

        // Remove assetId from path if it's the first component (like Needle viewer does)
        let pathSegments = resourceKey.split("/");
        if (pathSegments.length > 0 && pathSegments[0] === config.assetId) {
            pathSegments.shift(); // Remove assetId
            console.log(
                `LoadingManager: Stripped assetId from path: ${resourceKey} -> ${pathSegments.join(
                    "/"
                )}`
            );
        }

        // Download with authentication
        const encodedSegments = pathSegments.map((segment) => encodeURIComponent(segment));
        const encodedKey = encodedSegments.join("/");
        const assetUrl = `${config.apiEndpoint}database/${config.databaseId}/assets/${config.assetId}/download/stream/${encodedKey}`;

        const response = await fetch(assetUrl, {
            headers: { Authorization: authHeader },
        });

        if (!response.ok) {
            throw new Error(`Failed to load dependency: ${url} (${response.status})`);
        }

        const arrayBuffer = await response.arrayBuffer();
        console.log(`LoadingManager: Downloaded ${url} (${arrayBuffer.byteLength} bytes)`);

        // Create blob URL for caching
        const blob = new Blob([arrayBuffer]);
        const blobUrl = URL.createObjectURL(blob);
        resourceCache.set(url, blobUrl);

        return arrayBuffer;
    };

    return manager;
}

/**
 * Clean up blob URLs created by the loading manager
 */
export function cleanupLoadingManager(manager: any) {
    // Revoke all blob URLs to free memory
    if (manager && (manager as any).resourceCache) {
        const cache = (manager as any).resourceCache as Map<string, string>;
        cache.forEach((blobUrl) => {
            if (blobUrl.startsWith("blob:")) {
                URL.revokeObjectURL(blobUrl);
            }
        });
        cache.clear();
    }
}
