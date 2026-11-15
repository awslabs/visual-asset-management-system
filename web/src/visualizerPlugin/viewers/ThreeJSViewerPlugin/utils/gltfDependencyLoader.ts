/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * GLTF Dependency Loader
 * Pre-downloads all GLTF dependencies (textures, .bin files) with authentication
 * before passing to ThreeJS loader. Downloads go through the shared AssetDependencyResolver,
 * which applies its path rules and limits.
 */

import { AssetDependencyResolver } from "./assetDependencyResolver";

interface DependencyLoaderConfig {
    assetId: string;
    databaseId: string;
    baseFileKey: string;
    apiEndpoint: string;
    assetVersionId?: string;
    // Note: versionId is intentionally NOT included here.
    // Dependencies are always loaded without versionId because they are
    // part of the same asset version as the main file.
    // However, assetVersionId IS included so the server resolves
    // dependencies from the correct logical asset version.
}

/**
 * Extract dependencies from GLTF JSON
 */
export function extractGLTFDependencies(gltfJson: any): string[] {
    const dependencies: string[] = [];

    // Extract buffer URIs
    if (gltfJson.buffers) {
        gltfJson.buffers.forEach((buffer: any) => {
            if (buffer.uri && !buffer.uri.startsWith("data:")) {
                dependencies.push(buffer.uri);
            }
        });
    }

    // Extract image URIs
    if (gltfJson.images) {
        gltfJson.images.forEach((image: any) => {
            if (image.uri && !image.uri.startsWith("data:")) {
                dependencies.push(image.uri);
            }
        });
    }

    return dependencies;
}

/**
 * Downloads the files a .gltf names through `resolver` and returns the glTF with each resolved
 * URI replaced by a blob URL. URIs that did not resolve are left as written; the loading
 * manager's URL modifier answers them without a request.
 */
export async function resolveGLTFDependencies(
    gltfArrayBuffer: ArrayBuffer,
    baseFileKey: string,
    resolver: AssetDependencyResolver,
    onProgress?: (current: number, total: number) => void
): Promise<{ gltfArrayBuffer: ArrayBuffer; blobUrls: string[] }> {
    const blobUrls: string[] = [];

    try {
        // Parse GLTF JSON
        const decoder = new TextDecoder();
        const gltfText = decoder.decode(gltfArrayBuffer);
        const gltfJson = JSON.parse(gltfText);

        // Extract dependencies
        const dependencies = extractGLTFDependencies(gltfJson);

        if (dependencies.length === 0) {
            console.log("No external dependencies found in GLTF");
            return { gltfArrayBuffer, blobUrls };
        }

        console.log(`Found ${dependencies.length} dependencies in GLTF`);

        const result = await resolver.resolveDependencies(baseFileKey, dependencies, {
            depth: 1,
            onProgress,
        });

        const dependencyMap = new Map<string, string>();
        result.resolved.forEach((dependency, uri) => {
            const blobUrl = resolver.blobUrlFor(dependency);
            dependencyMap.set(uri, blobUrl);
            blobUrls.push(blobUrl);
        });

        // Replace URIs in GLTF JSON with blob URLs
        if (gltfJson.buffers) {
            gltfJson.buffers.forEach((buffer: any) => {
                if (buffer.uri && dependencyMap.has(buffer.uri)) {
                    buffer.uri = dependencyMap.get(buffer.uri);
                }
            });
        }

        if (gltfJson.images) {
            gltfJson.images.forEach((image: any) => {
                if (image.uri && dependencyMap.has(image.uri)) {
                    image.uri = dependencyMap.get(image.uri);
                }
            });
        }

        // Convert modified JSON back to ArrayBuffer
        const modifiedGltfText = JSON.stringify(gltfJson);
        const encoder = new TextEncoder();
        const modifiedArrayBuffer = encoder.encode(modifiedGltfText);

        return { gltfArrayBuffer: modifiedArrayBuffer.buffer as ArrayBuffer, blobUrls };
    } catch (error: any) {
        if (error?.name === "AbortError") {
            throw error;
        }
        console.error("Error preloading GLTF dependencies:", error);
        return { gltfArrayBuffer, blobUrls };
    }
}

/**
 * Pre-load all GLTF dependencies and return modified GLTF with blob URLs
 */
export async function preloadGLTFDependencies(
    gltfArrayBuffer: ArrayBuffer,
    config: DependencyLoaderConfig,
    onProgress?: (current: number, total: number) => void
): Promise<{ gltfArrayBuffer: ArrayBuffer; blobUrls: string[] }> {
    const resolver = new AssetDependencyResolver({
        assetId: config.assetId,
        databaseId: config.databaseId,
        apiEndpoint: config.apiEndpoint,
        assetVersionId: config.assetVersionId,
    });
    return resolveGLTFDependencies(gltfArrayBuffer, config.baseFileKey, resolver, onProgress);
}

/**
 * Clean up blob URLs
 */
export function cleanupBlobUrls(blobUrls: string[]) {
    blobUrls.forEach((url) => {
        URL.revokeObjectURL(url);
    });
}
