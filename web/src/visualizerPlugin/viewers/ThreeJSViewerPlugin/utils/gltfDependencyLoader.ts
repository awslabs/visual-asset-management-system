/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * GLTF Dependency Loader
 * Pre-downloads all GLTF dependencies (textures, .bin files) with authentication
 * before passing to ThreeJS loader
 */

import { getDualAuthorizationHeader } from "../../../../utils/authTokenUtils";

interface DependencyLoaderConfig {
    assetId: string;
    databaseId: string;
    baseFileKey: string;
    apiEndpoint: string;
}

/**
 * Extract dependencies from GLTF JSON
 */
function extractGLTFDependencies(gltfJson: any): string[] {
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
 * Download a dependency file with authentication
 */
async function downloadDependency(
    relativePath: string,
    config: DependencyLoaderConfig,
    authHeader: string
): Promise<ArrayBuffer> {
    // Get base directory from main file
    const baseDir = config.baseFileKey.substring(0, config.baseFileKey.lastIndexOf("/"));

    // Resolve relative path
    let fullPath: string;
    if (relativePath.startsWith("./") || relativePath.startsWith("../")) {
        const parts = baseDir.split("/");
        const urlParts = relativePath.split("/");

        for (const part of urlParts) {
            if (part === "..") {
                parts.pop();
            } else if (part !== ".") {
                parts.push(part);
            }
        }

        fullPath = parts.join("/");
    } else {
        fullPath = `${baseDir}/${relativePath}`;
    }

    // Remove assetId from path if present (like Needle viewer)
    let pathSegments = fullPath.split("/");
    if (pathSegments.length > 0 && pathSegments[0] === config.assetId) {
        pathSegments.shift();
    }

    // Construct streaming URL
    const encodedSegments = pathSegments.map((segment) => encodeURIComponent(segment));
    const encodedPath = encodedSegments.join("/");
    const url = `${config.apiEndpoint}database/${config.databaseId}/assets/${config.assetId}/download/stream/${encodedPath}`;

    console.log(`Downloading dependency: ${relativePath} -> ${url}`);

    const response = await fetch(url, {
        headers: { Authorization: authHeader },
    });

    if (!response.ok) {
        throw new Error(`Failed to download ${relativePath}: ${response.status}`);
    }

    return await response.arrayBuffer();
}

/**
 * Pre-load all GLTF dependencies and return modified GLTF with blob URLs
 */
export async function preloadGLTFDependencies(
    gltfArrayBuffer: ArrayBuffer,
    config: DependencyLoaderConfig,
    onProgress?: (current: number, total: number) => void
): Promise<{ gltfArrayBuffer: ArrayBuffer; blobUrls: string[] }> {
    const authHeader = await getDualAuthorizationHeader();
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

        // Download all dependencies and create blob URLs
        const dependencyMap = new Map<string, string>();

        for (let i = 0; i < dependencies.length; i++) {
            const dep = dependencies[i];
            try {
                if (onProgress) {
                    onProgress(i + 1, dependencies.length);
                }

                const arrayBuffer = await downloadDependency(dep, config, authHeader);
                const blob = new Blob([arrayBuffer]);
                const blobUrl = URL.createObjectURL(blob);
                dependencyMap.set(dep, blobUrl);
                blobUrls.push(blobUrl);
                console.log(
                    `Downloaded dependency ${i + 1}/${dependencies.length}: ${dep} (${
                        arrayBuffer.byteLength
                    } bytes)`
                );
            } catch (error) {
                console.warn(`Failed to download dependency ${dep}:`, error);
                // Continue with other dependencies
            }
        }

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

        return { gltfArrayBuffer: modifiedArrayBuffer.buffer, blobUrls };
    } catch (error) {
        console.error("Error preloading GLTF dependencies:", error);
        return { gltfArrayBuffer, blobUrls };
    }
}

/**
 * Clean up blob URLs
 */
export function cleanupBlobUrls(blobUrls: string[]) {
    blobUrls.forEach((url) => {
        URL.revokeObjectURL(url);
    });
}
