/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { Cache } from "aws-amplify";
import { ViewerPluginProps } from "../../core/types";
import { NeedleUSDDependencyManager } from "./dependencies";
import { downloadAsset } from "../../../services/APIService";
import { getDualAuthorizationHeader } from "../../../utils/authTokenUtils";
import { MouseControls } from "./MouseControls";
import NeedleUSDPanel from "./NeedleUSDPanel";
import LoadingSpinner from "../../components/LoadingSpinner";
import { MaterialLibraryItem } from "./NeedleUSDMaterialLibrary";

const NeedleUSDViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    multiFileKeys,
    versionId,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [config] = useState(Cache.getItem("config"));

    const [isLoading, setIsLoading] = useState(true);
    const [loadingMessage, setLoadingMessage] = useState("Initializing viewer...");
    const [error, setError] = useState<string | null>(null);
    const [fileErrors, setFileErrors] = useState<Array<{ file: string; error: string }>>([]);
    const [additionalDepsWarning, setAdditionalDepsWarning] = useState<{
        loaded: number;
        unresolved: number;
    }>({ loaded: 0, unresolved: 0 });
    const viewerInstanceRef = useRef<any>(null);
    const [showPanel, setShowPanel] = useState(true);
    const [sceneReady, setSceneReady] = useState(false);
    const [selectedObjects, setSelectedObjects] = useState<any[]>([]);
    const [loadedFileGroups, setLoadedFileGroups] = useState<any[]>([]);
    const raycasterRef = useRef<any>(null);
    const mouseRef = useRef<any>(null);
    const initializationRef = useRef(false);
    const originalTransformsRef = useRef<Map<string, any>>(new Map());

    // Loading cancellation flag
    const loadingCancelledRef = useRef(false);

    // Console override for WASM error capture (scoped to component lifecycle)
    const originalConsoleWarnRef = useRef<typeof console.warn | null>(null);
    const capturedWASMErrorsRef = useRef<string[]>([]);

    // Animation control (default to paused/off)
    const [animationPaused, setAnimationPaused] = useState(true);
    const animationFrameRef = useRef<number | null>(null);
    const animationPausedRef = useRef(true);

    // Animation timing
    const endTimeCodeRef = useRef<number>(1);
    const timeoutRef = useRef<number>(40);

    // 3D selection toggle
    const [enable3DSelection, setEnable3DSelection] = useState(true);
    const enable3DSelectionRef = useRef(true);

    // Keep refs in sync with state
    useEffect(() => {
        enable3DSelectionRef.current = enable3DSelection;
    }, [enable3DSelection]);

    useEffect(() => {
        animationPausedRef.current = animationPaused;
    }, [animationPaused]);

    // Material Library State
    const [materialLibrary, setMaterialLibrary] = useState<Map<string, MaterialLibraryItem>>(
        new Map()
    );
    const [selectedMaterialId, setSelectedMaterialId] = useState<string | null>(null);
    const materialCounterRef = useRef(1);

    // Build material library when scene is ready
    useEffect(() => {
        if (!sceneReady || !viewerInstanceRef.current?.scene) return;

        const THREE = (window as any).THREE;
        if (!THREE) return;

        const scene = viewerInstanceRef.current.scene;
        const discoveredMaterials = new Map<any, Set<string>>();

        // Scan scene for unique materials
        scene.traverse((obj: any) => {
            if (obj.material) {
                if (!discoveredMaterials.has(obj.material)) {
                    discoveredMaterials.set(obj.material, new Set());
                }
                discoveredMaterials.get(obj.material)!.add(obj.uuid);
            }
        });

        // Build material library
        const library = new Map<string, MaterialLibraryItem>();
        let counter = 1;

        Array.from(discoveredMaterials.entries()).forEach(([material, usedBy]) => {
            const materialId = material.uuid || `material_${Date.now()}_${counter}`;
            const materialName = material.name || `Material_${String(counter).padStart(3, "0")}`;

            // Clone material for original reference
            const originalMat = material.clone ? material.clone() : material;

            library.set(materialId, {
                id: materialId,
                name: materialName,
                material: material,
                usedBy: usedBy,
                isCustom: false,
                originalMaterial: originalMat, // Store original for reset
            });

            counter++;
        });

        setMaterialLibrary(library);
        materialCounterRef.current = counter;

        console.log(`Material library initialized with ${library.size} materials`);
    }, [sceneReady]);

    // Handle highlighting at parent level to persist across tab switches
    useEffect(() => {
        if (!viewerInstanceRef.current?.scene) return;

        const THREE = (window as any).THREE;
        if (!THREE) return;

        const scene = viewerInstanceRef.current.scene;
        const selectedUuids = new Set(selectedObjects.map((obj) => obj.uuid));

        // Remove highlighting from deselected objects
        scene.traverse((obj: any) => {
            if (obj.material && !selectedUuids.has(obj.uuid)) {
                // Check if this object has a highlight (green emissive)
                if (obj.material.emissive && obj.material.emissive.getHex() === 0x4caf50) {
                    // Find the material in the library and restore it
                    Array.from(materialLibrary.values()).forEach((item) => {
                        if (item.usedBy.has(obj.uuid)) {
                            obj.material = item.material;
                        }
                    });
                }
            }
        });

        // Add highlighting to selected objects
        selectedObjects.forEach((obj) => {
            if (obj.material) {
                // Find the base material from library
                let baseMaterial: any = null;
                Array.from(materialLibrary.values()).forEach((item) => {
                    if (item.usedBy.has(obj.uuid)) {
                        baseMaterial = item.material;
                    }
                });

                if (baseMaterial) {
                    // Create highlight material - check if clone method exists
                    const highlightMaterial = baseMaterial.clone
                        ? baseMaterial.clone()
                        : new THREE.MeshStandardMaterial({
                              color: baseMaterial.color,
                              metalness: baseMaterial.metalness || 0.5,
                              roughness: baseMaterial.roughness || 0.5,
                              opacity: baseMaterial.opacity || 1.0,
                              transparent: baseMaterial.transparent || false,
                          });
                    highlightMaterial.emissive = new THREE.Color(0x4caf50);
                    highlightMaterial.emissiveIntensity = 0.5;
                    obj.material = highlightMaterial;
                }
            }
        });
    }, [selectedObjects, materialLibrary]);

    useEffect(() => {
        const hasFiles = assetKey || (multiFileKeys && multiFileKeys.length > 0);
        if (!hasFiles || initializationRef.current || !config || !containerRef.current) {
            return;
        }
        initializationRef.current = true;

        // ============================================================================
        // WASM FILESYSTEM CLEANUP
        // ============================================================================

        /**
         * Clean up WASM filesystem to prevent memory leaks
         */
        const cleanupWASMFilesystem = (USD: any) => {
            try {
                console.log("Cleaning WASM filesystem...");

                // Helper to recursively get all files in a directory
                const getAllFiles = (path: string): string[] => {
                    const files: string[] = [];
                    try {
                        const entries = USD.FS_readdir(path);
                        for (const entry of entries) {
                            if (entry === "." || entry === "..") continue;
                            const fullPath = path === "/" ? `/${entry}` : `${path}/${entry}`;
                            try {
                                const stat = USD.FS_analyzePath(fullPath);
                                if (stat.object && stat.object.isFolder) {
                                    files.push(...getAllFiles(fullPath));
                                } else {
                                    files.push(fullPath);
                                }
                            } catch {}
                        }
                    } catch {}
                    return files;
                };

                // Delete all /file_* directories
                for (let i = 0; i < 100; i++) {
                    try {
                        const dirPath = `/file_${i}`;
                        const stat = USD.FS_analyzePath(dirPath);
                        if (stat.exists) {
                            const files = getAllFiles(dirPath);
                            files.forEach((f) => {
                                try {
                                    USD.FS_unlink(f);
                                } catch {}
                            });
                            // Remove empty directories recursively
                            try {
                                USD.FS_rmdir(dirPath);
                            } catch {}
                        }
                    } catch {}
                }
                console.log("WASM filesystem cleaned");
            } catch (error) {
                console.warn("Error cleaning WASM filesystem:", error);
            }
        };

        // ============================================================================
        // USD DEPENDENCY RESOLUTION HELPER FUNCTIONS
        // ============================================================================

        /**
         * Detect if a USD file is text-based or binary format
         */
        const detectUSDFormat = (content: ArrayBuffer): "text" | "binary" => {
            const header = new Uint8Array(content.slice(0, 8));

            // USDC files start with "PXR-USDC" magic bytes
            const usdcMagic = [0x50, 0x58, 0x52, 0x2d, 0x55, 0x53, 0x44, 0x43];
            const isUSDC = usdcMagic.every((byte, i) => header[i] === byte);

            if (isUSDC) return "binary";

            // Try to decode as text
            try {
                const sample = new TextDecoder("utf-8", { fatal: true }).decode(
                    content.slice(0, 1024)
                );
                if (sample.includes("#usda") || sample.includes("def ") || sample.includes("@")) {
                    return "text";
                }
            } catch {
                return "binary";
            }

            return "text"; // Default to text
        };

        /**
         * Extract ASCII strings from binary data
         */
        const extractASCIIStrings = (bytes: Uint8Array, minLength: number = 4): string[] => {
            const strings: string[] = [];
            let currentString = "";

            for (let i = 0; i < bytes.length; i++) {
                const byte = bytes[i];

                // Printable ASCII range (space to ~)
                if (byte >= 0x20 && byte <= 0x7e) {
                    currentString += String.fromCharCode(byte);
                } else {
                    if (currentString.length >= minLength) {
                        strings.push(currentString);
                    }
                    currentString = "";
                }
            }

            if (currentString.length >= minLength) {
                strings.push(currentString);
            }

            return strings;
        };

        /**
         * Clean file path extracted from binary data
         * Preserves relative path prefixes like ./ and ../
         */
        const cleanFilePath = (path: string): string | null => {
            let cleaned = path.trim();

            // Remove @ symbols
            cleaned = cleaned.replace(/@/g, "");

            // Remove leading non-path characters BUT preserve ./ and ../
            // First, check if it starts with relative path indicators
            const startsWithRelative = cleaned.startsWith("./") || cleaned.startsWith("../");

            if (!startsWithRelative) {
                // Remove leading non-path characters (but keep . for relative paths)
                cleaned = cleaned.replace(/^[^a-zA-Z0-9._\/]+/, "");
            }

            // Remove trailing non-path characters
            cleaned = cleaned.replace(/[^a-zA-Z0-9._\/]+$/, "");

            // Must have a filename (not just extension or directory)
            const parts = cleaned.split("/");
            const fileName = parts[parts.length - 1];

            // Skip if no filename or filename is just . or ..
            if (!fileName || fileName === "." || fileName === "..") {
                return null;
            }

            // Skip if filename starts with . but has no extension (hidden file without name)
            if (fileName.startsWith(".") && !fileName.includes(".", 1)) {
                return null;
            }

            // Must contain a file extension
            if (!fileName.includes(".")) {
                return null;
            }

            // Must contain at least one path separator or be a simple filename with extension
            if (cleaned.includes("/") || cleaned.includes(".")) {
                return cleaned;
            }

            return null;
        };

        /**
         * Resolve relative path based on source file location
         * Handles ./ and ../ paths correctly.
         *
         * The boundary is the assetId level - paths can go up to but not above the assetId.
         * For example:
         * - Source: "source/asset/file.usd" (path within assetId)
         * - Reference: "../../../data/pot.usd"
         * - Result: "data/pot.usd" (relative to assetId root)
         *
         * The sourceFilePath should be the path relative to the assetId (assetId prefix stripped).
         */
        const resolveRelativePath = (
            referencePath: string,
            sourceFilePath: string,
            baseDirectory: string = ""
        ): string => {
            // Remove @ symbols if present
            const cleanPath = referencePath.replace(/@/g, "").trim();

            // Strip assetId from sourceFilePath if present (for consistent handling)
            let normalizedSourcePath = sourceFilePath;
            if (normalizedSourcePath.startsWith(assetId + "/")) {
                normalizedSourcePath = normalizedSourcePath.substring(assetId.length + 1);
            }

            // Get directory of source file
            const lastSlash = normalizedSourcePath.lastIndexOf("/");
            const sourceDir = lastSlash >= 0 ? normalizedSourcePath.substring(0, lastSlash) : "";

            let resolvedPath: string;

            // Handle different path types
            if (cleanPath.startsWith("./")) {
                // Relative to current directory
                resolvedPath = sourceDir
                    ? `${sourceDir}/${cleanPath.substring(2)}`
                    : cleanPath.substring(2);
            } else if (cleanPath.startsWith("../")) {
                // Parent directory reference
                const sourceParts = sourceDir.split("/").filter((p) => p);
                const refParts = cleanPath.split("/").filter((p) => p);

                // Process parent references (..)
                // Allow going up as far as needed - the boundary is at the assetId level
                // Any additional .. beyond the root are simply consumed
                while (refParts.length > 0 && refParts[0] === "..") {
                    refParts.shift();
                    if (sourceParts.length > 0) {
                        sourceParts.pop();
                    }
                    // If sourceParts is empty, we've reached the assetId root
                    // Continue consuming .. but don't go negative
                }

                resolvedPath = [...sourceParts, ...refParts].join("/");
            } else if (cleanPath.startsWith("/")) {
                // Absolute path from root (relative to assetId)
                resolvedPath = cleanPath.substring(1);
            } else {
                // Relative path without ./ (same as ./)
                resolvedPath = sourceDir ? `${sourceDir}/${cleanPath}` : cleanPath;
            }

            // Normalize the path (remove any double slashes, trailing slashes)
            resolvedPath = resolvedPath
                .replace(/\/+/g, "/") // Replace multiple slashes with single
                .replace(/^\//, "") // Remove leading slash
                .replace(/\/$/, ""); // Remove trailing slash

            // Debug logging for relative path resolution
            if (cleanPath.includes("../")) {
                console.log(
                    `  [resolveRelativePath] "${referencePath}" from "${sourceFilePath}" -> "${resolvedPath}"`
                );
            }

            return resolvedPath;
        };

        /**
         * Extract dependencies from text-based USD file
         * Handles all USD reference patterns including:
         * - @path@ asset references
         * - sublayers = [@path@, ...]
         * - payload/references = @path@
         * - prepend payload = @path@
         * - info:mdl:sourceAsset = @path@
         * - inputs:file = @path@
         * - inputs:*_texture = @path@
         * - asset ... = @path@
         */
        const extractFromText = (
            text: string,
            sourceFilePath: string,
            dependencies: Set<string>
        ): void => {
            let match;

            // Helper to add dependency if valid
            const addDependency = (refPath: string) => {
                if (
                    refPath &&
                    !refPath.startsWith("http") &&
                    !refPath.startsWith("//") &&
                    refPath.length > 0 &&
                    refPath !== "@@" // Skip empty asset references
                ) {
                    const resolved = resolveRelativePath(refPath, sourceFilePath);
                    if (resolved && resolved.length > 0) {
                        dependencies.add(resolved);
                    }
                }
            };

            // Pattern 1: Generic @path@ asset references (most common)
            // Matches: @./path/to/file.usd@, @../path/file.png@, @path/file.mdl@
            const assetPattern = /@([^@\s][^@]*\.[a-zA-Z0-9]+)@/g;
            while ((match = assetPattern.exec(text)) !== null) {
                addDependency(match[1].trim());
            }

            // Pattern 2: Sublayers array
            // Matches: subLayers = [@path1@, @path2@]
            const sublayerPattern = /subLayers\s*=\s*\[([^\]]*)\]/gi;
            while ((match = sublayerPattern.exec(text)) !== null) {
                const sublayerContent = match[1];
                const sublayerRefs = sublayerContent.match(/@([^@]+)@/g);
                if (sublayerRefs) {
                    sublayerRefs.forEach((ref) => {
                        const path = ref.replace(/@/g, "").trim();
                        addDependency(path);
                    });
                }
            }

            // Pattern 3: Payload/References (with optional prepend/append)
            // Matches: payload = @path@, prepend payload = @path@, references = @path@
            const payloadPattern =
                /(?:prepend\s+|append\s+)?(?:payload|references)\s*=\s*@([^@]+)@/gi;
            while ((match = payloadPattern.exec(text)) !== null) {
                addDependency(match[1].trim());
            }

            // Pattern 4: MDL source asset references
            // Matches: info:mdl:sourceAsset = @path/to/material.mdl@
            const mdlPattern = /info:mdl:sourceAsset\s*=\s*@([^@]+)@/gi;
            while ((match = mdlPattern.exec(text)) !== null) {
                addDependency(match[1].trim());
            }

            // Pattern 5: Shader texture file inputs
            // Matches: inputs:file = @path/to/texture.jpg@
            const inputsFilePattern = /inputs:file\s*=\s*@([^@]+)@/gi;
            while ((match = inputsFilePattern.exec(text)) !== null) {
                addDependency(match[1].trim());
            }

            // Pattern 6: Various texture input references
            // Matches: inputs:diffuse_texture = @path@, inputs:normalmap_texture = @path@, etc.
            const textureInputPattern = /inputs:[a-zA-Z_]+(?:_texture|Texture)\s*=\s*@([^@]+)@/gi;
            while ((match = textureInputPattern.exec(text)) !== null) {
                addDependency(match[1].trim());
            }

            // Pattern 7: Generic asset type declarations
            // Matches: asset inputs:ao_texture = @path@, uniform asset info:mdl:sourceAsset = @path@
            const assetDeclPattern = /(?:uniform\s+)?asset\s+[a-zA-Z:_]+\s*=\s*@([^@]+)@/gi;
            while ((match = assetDeclPattern.exec(text)) !== null) {
                addDependency(match[1].trim());
            }

            // Pattern 8: Default value asset references in customData
            // Matches: asset default = @path/to/default.png@
            const defaultAssetPattern = /asset\s+default\s*=\s*@([^@]+)@/gi;
            while ((match = defaultAssetPattern.exec(text)) !== null) {
                addDependency(match[1].trim());
            }
        };

        /**
         * Extract dependencies from binary USD file using heuristic byte search
         * Enhanced to better capture relative paths like ../../Assets/...
         */
        const extractFromBinary = (
            content: ArrayBuffer,
            sourceFilePath: string,
            dependencies: Set<string>
        ): void => {
            const bytes = new Uint8Array(content);

            // Common file extensions to look for
            const extensions = [
                ".usd",
                ".usda",
                ".usdc",
                ".usdz", // USD files
                ".png",
                ".jpg",
                ".jpeg",
                ".exr", // Textures
                ".hdr",
                ".tif",
                ".tiff", // HDR/Textures
                ".obj",
                ".fbx",
                ".gltf",
                ".glb", // Other 3D formats
                ".mdl", // Material files
                ".bin",
                ".animation",
                ".ply",
                ".json",
            ];

            // Search for ASCII strings in binary data with lower minimum length
            // to catch short relative paths
            const strings = extractASCIIStrings(bytes, 3);

            // Also try to find paths by looking for common patterns
            const foundPaths = new Set<string>();

            for (const str of strings) {
                const lowerStr = str.toLowerCase();
                const hasExtension = extensions.some((ext) => lowerStr.endsWith(ext));

                if (hasExtension && !str.startsWith("http")) {
                    // Try to extract the path - it might be embedded in a longer string
                    // Look for patterns like: ../path/file.usd or ./path/file.usd or path/file.usd

                    // Pattern 1: Find relative paths starting with ../ or ./
                    const relativeMatch = str.match(/(\.\.\/[^\s@<>"|?*]+\.[a-zA-Z0-9]+)/);
                    if (relativeMatch) {
                        foundPaths.add(relativeMatch[1]);
                    }

                    const currentDirMatch = str.match(/(\.\/[^\s@<>"|?*]+\.[a-zA-Z0-9]+)/);
                    if (currentDirMatch) {
                        foundPaths.add(currentDirMatch[1]);
                    }

                    // Pattern 2: Find paths that look like directory/file.ext
                    const pathMatch = str.match(
                        /([a-zA-Z0-9_][a-zA-Z0-9_\-./]*\/[a-zA-Z0-9_][a-zA-Z0-9_\-.]*\.[a-zA-Z0-9]+)/
                    );
                    if (pathMatch) {
                        foundPaths.add(pathMatch[1]);
                    }

                    // Pattern 3: Clean the whole string as a path
                    const cleanPath = cleanFilePath(str);
                    if (cleanPath) {
                        foundPaths.add(cleanPath);
                    }
                }
            }

            // Process all found paths
            Array.from(foundPaths).forEach((path) => {
                if (path && path.length > 0) {
                    const resolved = resolveRelativePath(path, sourceFilePath);
                    if (resolved && resolved.length > 0) {
                        dependencies.add(resolved);
                        console.log(`    Binary extraction: ${path} -> ${resolved}`);
                    }
                }
            });
        };

        /**
         * Extract all dependencies from a USD file (text or binary)
         */
        const extractDependencies = (content: ArrayBuffer, sourceFilePath: string): string[] => {
            const dependencies: Set<string> = new Set();

            try {
                const format = detectUSDFormat(content);
                console.log(`  USD format detected: ${format} for ${sourceFilePath}`);

                if (format === "text") {
                    const text = new TextDecoder().decode(content);
                    extractFromText(text, sourceFilePath, dependencies);
                } else {
                    extractFromBinary(content, sourceFilePath, dependencies);
                }
            } catch (error) {
                console.warn(`Error extracting dependencies from ${sourceFilePath}:`, error);
            }

            return Array.from(dependencies);
        };

        /**
         * Check if a file is a USD file based on extension
         */
        const isUSDFile = (path: string): boolean => {
            const ext = path.toLowerCase().split(".").pop();
            return ext === "usd" || ext === "usda" || ext === "usdc" || ext === "usdz";
        };

        /**
         * Store file in WASM filesystem with proper directory structure
         * @param fileKey - Original file path (e.g., "x659317c9.../Materials/model.usd")
         * @param content - File content as ArrayBuffer
         * @param USD - USD WASM module
         * @param baseDirectory - Base directory prefix (e.g., "/file_0")
         */
        const storeInWASM = (
            fileKey: string,
            content: ArrayBuffer,
            USD: any,
            baseDirectory: string = ""
        ): string => {
            let parts = fileKey.split("/");

            // Remove assetId from path if it's the first component
            // The assetId is passed in from the component props
            if (parts.length > 0 && parts[0] === assetId) {
                parts.shift(); // Remove assetId
                console.log(`    Trimmed assetId from path: ${fileKey} -> ${parts.join("/")}`);
            }

            const fileName = parts.pop()!;

            // Construct full directory path with base directory
            let dirPath: string;
            if (parts.length > 0) {
                dirPath = baseDirectory
                    ? `${baseDirectory}/${parts.join("/")}`
                    : "/" + parts.join("/");
            } else {
                dirPath = baseDirectory || "/";
            }

            const fullPath = `${dirPath}/${fileName}`;

            // Check if file already exists - if so, unlink and replace
            // The USD driver may create placeholder/empty files, so we replace with actual content
            try {
                const stat = USD.FS_analyzePath(fullPath);
                if (stat.exists) {
                    console.log(`    File exists, unlinking and replacing: ${fullPath}`);
                    try {
                        USD.FS_unlink(fullPath);
                    } catch (unlinkError) {
                        console.warn(`    Failed to unlink ${fullPath}:`, unlinkError);
                        // Continue anyway - the createDataFile might still work
                    }
                }
            } catch (e) {
                // Error checking path - proceed with creation
            }

            // Create directory structure level by level
            // This is necessary because FS_createPath doesn't always handle deep nesting
            const pathParts = dirPath.split("/").filter((p) => p);
            let currentPath = "";
            for (const part of pathParts) {
                const parentPath = currentPath || "/";
                currentPath = currentPath + "/" + part;
                try {
                    // Try to create this directory level
                    USD.FS_createPath(parentPath, part, true, true);
                } catch (e: any) {
                    // Directory might already exist, that's fine
                }
            }

            // Store file
            USD.FS_createDataFile(dirPath, fileName, new Uint8Array(content), true, true, true);

            console.log(`    Stored in WASM: ${fullPath} (${content.byteLength} bytes)`);
            return fullPath;
        };

        /**
         * Download a file from the streaming endpoint
         */
        const downloadFile = async (
            fileKey: string,
            authHeader: string,
            isMainFile: boolean = false,
            isSingleFile: boolean = false
        ): Promise<ArrayBuffer | null> => {
            try {
                const pathSegments = fileKey.split("/");
                const encodedSegments = pathSegments.map((segment) => encodeURIComponent(segment));
                const encodedFileKey = encodedSegments.join("/");
                let assetUrl = `${config.api}database/${databaseId}/assets/${assetId}/download/stream/${encodedFileKey}`;

                // Add versionId query parameter for single file mode
                if (isSingleFile && versionId) {
                    assetUrl += `?versionId=${encodeURIComponent(versionId)}`;
                }

                const response = await fetch(assetUrl, {
                    headers: { Authorization: authHeader },
                });

                if (!response.ok) {
                    if (isMainFile) {
                        throw new Error(`Failed to load: ${fileKey} (${response.status})`);
                    } else {
                        console.warn(
                            `Dependency not found (skipping): ${fileKey} (${response.status})`
                        );
                        return null;
                    }
                }

                return await response.arrayBuffer();
            } catch (error) {
                if (isMainFile) throw error;
                console.warn(`Error loading dependency: ${fileKey}`, error);
                return null;
            }
        };

        // ============================================================================
        // WASM ERROR PARSING FOR DEPENDENCY DISCOVERY
        // ============================================================================

        /**
         * Parse WASM console warnings to extract missing asset paths with their file index
         * Example error: "Could not open asset @/file_0/Assets/Vegetation/Plant_Tropical/Buddha_Belly_Bamboo.usd@"
         * Returns array of {fileIndex, path} objects
         */
        const parseWASMErrorsForMissingAssets = (
            errors: string[]
        ): Array<{ fileIndex: number; path: string }> => {
            const missingAssets: Map<string, { fileIndex: number; path: string }> = new Map();

            console.log(`[parseWASMErrors] Parsing ${errors.length} captured errors`);

            for (let i = 0; i < errors.length; i++) {
                const error = errors[i];

                // Only log first few errors in detail
                if (i < 3) {
                    console.log(
                        `[parseWASMErrors] Error ${i}: length=${error.length}, full="${error}"`
                    );
                }

                // Find the "Could not open asset" phrase
                const couldNotOpenIdx = error.indexOf("Could not open asset");
                if (couldNotOpenIdx < 0) {
                    continue; // Skip errors without this phrase
                }

                // Extract the portion after "Could not open asset"
                const afterPhrase = error.substring(couldNotOpenIdx);

                // Look for the pattern: @/file_X/path/to/file.ext@
                // The path is wrapped in @...@ (not quotes!)
                // Pattern: @/file_0/Assets/ArchVis/Residential/Decor/Vases/Prime_Large.usd@

                // Find @/file_X/ pattern
                const fileStartMatch = afterPhrase.match(/@\/file_(\d+)\//);
                let filePathMatch: RegExpMatchArray | null = null;

                if (fileStartMatch) {
                    // Found @/file_X/, now extract the path until the closing @
                    const startIdx = fileStartMatch.index! + fileStartMatch[0].length;
                    const remaining = afterPhrase.substring(startIdx);

                    // Find the closing @ (the path ends with .usd@ or similar)
                    const endIdx = remaining.indexOf("@");

                    if (endIdx > 0) {
                        const path = remaining.substring(0, endIdx);
                        // Create a match-like array for compatibility
                        filePathMatch = [
                            `@/file_${fileStartMatch[1]}/${path}@`,
                            fileStartMatch[1],
                            path,
                        ] as RegExpMatchArray;
                    }

                    if (i < 3) {
                        console.log(`[parseWASMErrors] fileStartMatch:`, fileStartMatch[0]);
                        console.log(
                            `[parseWASMErrors] remaining (first 80): "${remaining.substring(
                                0,
                                80
                            )}"`
                        );
                        console.log(`[parseWASMErrors] endIdx (@):`, endIdx);
                        if (filePathMatch) {
                            console.log(`[parseWASMErrors] Extracted path: "${filePathMatch[2]}"`);
                        }
                    }
                } else if (i < 3) {
                    console.log(
                        `[parseWASMErrors] No @/file_X/ pattern found in: "${afterPhrase.substring(
                            0,
                            80
                        )}"`
                    );
                }

                if (filePathMatch && filePathMatch[2]) {
                    // filePathMatch[1] is the file number (e.g., "0", "1", "123")
                    // filePathMatch[2] is the path without the trailing @ (e.g., "Assets/ArchVis/.../Prime_Large.usd")
                    const fileIndex = parseInt(filePathMatch[1], 10);
                    let path = filePathMatch[2].trim();

                    if (path.length > 0 && !isNaN(fileIndex)) {
                        // Use path as key to deduplicate, but store fileIndex with it
                        missingAssets.set(path, { fileIndex, path });
                        if (i < 5) {
                            console.log(`[parseWASMErrors] ✓ Found: file_${fileIndex}/${path}`);
                        }
                    }
                } else {
                    // Try alternate pattern without @ (for paths that don't end with @)
                    const altMatch = afterPhrase.match(/'file_(\d+)\/([^']+)'/);
                    if (altMatch && altMatch[1] && altMatch[2]) {
                        const fileIndex = parseInt(altMatch[1], 10);
                        let path = altMatch[2].trim();
                        // Remove trailing @ if present
                        if (path.endsWith("@")) {
                            path = path.slice(0, -1);
                        }
                        if (path.length > 0 && !isNaN(fileIndex)) {
                            missingAssets.set(path, { fileIndex, path });
                            if (i < 5) {
                                console.log(
                                    `[parseWASMErrors] ✓ Found (alt): file_${fileIndex}/${path}`
                                );
                            }
                        }
                    } else if (i < 3) {
                        // Debug: Show what we're trying to match
                        console.log(
                            `[parseWASMErrors] No match. afterPhrase first 100 chars: "${afterPhrase.substring(
                                0,
                                100
                            )}"`
                        );
                    }
                }
            }

            const results = Array.from(missingAssets.values());
            console.log(`[parseWASMErrors] Total unique missing assets: ${results.length}`);
            if (results.length > 0) {
                console.log(
                    `[parseWASMErrors] First few:`,
                    results.slice(0, 5).map((r) => `file_${r.fileIndex}/${r.path}`)
                );
            }
            return results;
        };

        /**
         * Wait for WASM to settle (finish loading and reporting errors)
         */
        const waitForWASMToSettle = (ms: number = 2000): Promise<void> => {
            return new Promise((resolve) => setTimeout(resolve, ms));
        };

        // ============================================================================
        // MAIN LOADING FUNCTION WITH DEPENDENCY RESOLUTION
        // ============================================================================

        // Main loading function using streaming URLs with recursive dependency resolution
        // This implementation:
        // 1. Loads all primary files and their initial dependencies
        // 2. Creates drivers and verifies dependencies via WASM errors
        // 3. Destroys drivers, downloads missing dependencies in parallel
        // 4. Recreates drivers and verifies again (repeat until done)
        const loadAssets = async () => {
            try {
                // ============================================================
                // CRITICAL: Check for SharedArrayBuffer support BEFORE loading WASM
                // WASM requires SharedArrayBuffer which needs specific HTTP headers:
                // - Cross-Origin-Embedder-Policy: credentialless
                // - Cross-Origin-Opener-Policy: same-origin
                // ============================================================
                if (typeof SharedArrayBuffer === "undefined") {
                    throw new Error(
                        "WebAssembly (WASM) Support Not Available.\n\n" +
                            "The Needle USD viewer requires WebAssembly with SharedArrayBuffer support, which is not currently available. This may be due to:\n\n" +
                            "• Missing or incorrect web server headers (Cross-Origin-Embedder-Policy and Cross-Origin-Opener-Policy).\n" +
                            "• Browser restrictions or unsupported browser version.\n" +
                            "• Safari browser limitations (does not support required 'credentialless' policy).\n\n" +
                            "Please contact your system administrator to enable WASM support or try a different browser (Chrome, Firefox, or Edge recommended)."
                    );
                }

                // Detect Deep Deps Setting
                const allowDeepDepLoading = true; //navigator.userAgent.toLowerCase().includes("firefox");

                // ============================================================
                // CRITICAL: Set up console.warn override BEFORE loading ANY
                // WASM-related code. The emHdBindings.js captures console.warn
                // at load time, so we must override it first.
                // SKIP FOR FIREFOX - causes browser lockups
                // ============================================================
                capturedWASMErrorsRef.current = [];

                // Store original console.warn for cleanup
                const originalConsoleWarn = console.warn.bind(console);
                const originalConsoleLog = console.log.bind(console);
                originalConsoleWarnRef.current = originalConsoleWarn;

                // Create capture function - only used for non-Firefox browsers
                let isCapturing = false;
                let printErrBuffer = "";

                const captureWASMError = (message: string) => {
                    if (isCapturing || !allowDeepDepLoading) return; // Skip for non deep deps

                    if (
                        message.includes("Could not open asset") ||
                        message.includes("_ReportErrors") ||
                        (message.includes("Warning:") && message.includes("stage.cpp"))
                    ) {
                        capturedWASMErrorsRef.current.push(message);
                        isCapturing = true;
                        originalConsoleLog("[WASM ERROR CAPTURED]:", message.length, "chars");
                        isCapturing = false;
                    }
                };

                const handlePrintErr = (text: string) => {
                    if (!allowDeepDepLoading) return; // Skip for non deep deps

                    printErrBuffer += text;

                    if (printErrBuffer.includes("<0x") && printErrBuffer.endsWith(")")) {
                        captureWASMError(printErrBuffer);
                        printErrBuffer = "";
                    } else if (
                        text.endsWith("\n") &&
                        printErrBuffer.includes("Could not open asset")
                    ) {
                        captureWASMError(printErrBuffer.trim());
                        printErrBuffer = "";
                    }
                };

                setLoadingMessage("Loading USD Viewer dependencies...");
                await NeedleUSDDependencyManager.loadUSDViewer();
                originalConsoleLog("USD Viewer dependencies loaded successfully");

                setLoadingMessage("Initializing 3D scene...");

                const bundle = NeedleUSDDependencyManager.getUSDBundle();
                const THREE = bundle.THREE;
                const ThreeRenderDelegateInterface = bundle.ThreeRenderDelegateInterface;
                const getUsdModule = bundle.getUsdModule;

                const scene = new THREE.Scene();
                scene.background = new THREE.Color(0x333333);
                const camera = new THREE.PerspectiveCamera(
                    60,
                    containerRef.current!.clientWidth / containerRef.current!.clientHeight,
                    0.1,
                    1000
                );
                camera.position.set(5, 5, 5);
                const renderer = new THREE.WebGLRenderer({ antialias: true });
                renderer.setSize(
                    containerRef.current!.clientWidth,
                    containerRef.current!.clientHeight
                );
                renderer.setPixelRatio(window.devicePixelRatio);
                containerRef.current!.appendChild(renderer.domElement);

                scene.add(new THREE.AmbientLight(0xffffff, 0.5));
                const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
                dirLight.position.set(5, 10, 7.5);
                scene.add(dirLight);

                setLoadingMessage("Initializing USD WASM module...");

                // const onDemandDownloads = new Map<string, ArrayBuffer>();
                // // Define urlModifier function that will be called by WASM
                // // IMPORTANT: When called from worker threads via callHandlerAsync,
                // // the args are passed as an object with numeric keys, not an array.
                // // We need to handle both calling conventions.
                // const urlModifierFunc = async (...args: any[]) => {
                //     // Handle both array args and object args (from worker threads)
                //     let url: string;
                //     if (args.length > 0 && typeof args[0] === 'string') {
                //         // Normal call: args is a real array with string URL
                //         url = args[0];
                //     } else if (args.length > 0 && typeof args[0] === 'object' && args[0] !== null && '0' in args[0]) {
                //         // Worker thread call: args[0] is an object with numeric keys like {0: url, uniqueMessageId: "..."}
                //         url = args[0]['0'];
                //     } else if (args.length === 0) {
                //         // No args provided - shouldn't happen but handle gracefully
                //         console.warn('[urlModifier] Called with no arguments');
                //         return undefined;
                //     } else {
                //         // Unknown format - log and return first arg
                //         console.warn('[urlModifier] Unknown args format:', args);
                //         url = String(args[0]);
                //     }

                //     // url format: "/file_0/path/to/dependency.usd"
                //     const match = url.match(/^\/file_(\d+)\/(.+)$/);
                //     if (!match) return url;

                //     const relativePath = match[2];
                //     const fileName = relativePath.split('/').pop() || 'file';

                //     // Already downloaded?
                //     if (onDemandDownloads.has(relativePath)) {
                //         const content = onDemandDownloads.get(relativePath)!;
                //         // Return object with getFile method
                //         return {
                //             getFile: async () => {
                //                 return new File([content], fileName);
                //             }
                //         };
                //     }

                //     if (failedDownloads.has(relativePath)) {
                //         return url; // Let it fail
                //     }

                //     // Download using existing streams API logic
                //     console.log(`[urlModifier] On-demand download: ${relativePath}`);
                //     const content = await downloadFile(relativePath, authHeader, false, false);
                //     if (content) {
                //         onDemandDownloads.set(relativePath, content);
                //         console.log(`[urlModifier] Downloaded: ${relativePath} (${content.byteLength} bytes)`);
                //         // Return object with getFile method
                //         return {
                //             getFile: async () => {
                //                 return new File([content], fileName);
                //             }
                //         };
                //     }

                //     console.warn(`[urlModifier] Failed to download: ${relativePath}`);
                //     failedDownloads.add(relativePath);
                //     return url;
                // };

                const USD = await getUsdModule({
                    locateFile: (path: string, prefix: string) =>
                        (prefix || "/viewers/needletools_usd_viewer/") + path,
                    //urlModifier: urlModifierFunc,
                    printErr: (text: string) => {
                        handlePrintErr(text);
                        originalConsoleWarn(text);
                    },
                    print: (text: string) => {
                        handlePrintErr(text);
                        originalConsoleLog(text);
                    },
                });

                const filesToLoad =
                    multiFileKeys && multiFileKeys.length > 0
                        ? multiFileKeys
                        : assetKey
                        ? [assetKey]
                        : [];
                if (filesToLoad.length === 0) throw new Error("No files specified");

                const authHeader = await getDualAuthorizationHeader();
                const loadedFiles = new Set<string>();
                const failedDownloads = new Set<string>();
                const errors: Array<{ file: string; error: string }> = [];

                // Store file info for driver creation
                const fileInfos: Array<{
                    fileKey: string;
                    fileName: string;
                    directory: string;
                    fileGroup: any;
                }> = [];

                // ============================================================
                // PHASE 1: Load all primary files and their initial dependencies
                // ============================================================
                for (let i = 0; i < filesToLoad.length; i++) {
                    if (loadingCancelledRef.current) {
                        console.log("Loading cancelled");
                        return;
                    }

                    const fileKey = filesToLoad[i];
                    const fileName = fileKey.split("/").pop() || `model_${i}.usd`;
                    const directory = `/file_${i}`;

                    console.log(
                        `\n=== Loading primary file ${i + 1}/${filesToLoad.length}: ${fileKey} ===`
                    );
                    setLoadingMessage(
                        `Loading file ${i + 1}/${filesToLoad.length}: ${fileName}...`
                    );

                    try {
                        // Download main file
                        const isSingleFile = filesToLoad.length === 1;
                        const arrayBuffer = await downloadFile(
                            fileKey,
                            authHeader,
                            true,
                            isSingleFile
                        );
                        if (!arrayBuffer) throw new Error("Failed to download file");

                        // Store main file in WASM filesystem
                        USD.FS_createPath("", directory, true, true);
                        USD.FS_createDataFile(
                            directory,
                            fileName,
                            new Uint8Array(arrayBuffer),
                            true,
                            true,
                            true
                        );
                        loadedFiles.add(fileKey);

                        console.log(
                            `Main file loaded: ${fileKey} (${arrayBuffer.byteLength} bytes)`
                        );

                        // Load initial dependencies (from file parsing)
                        if (isUSDFile(fileKey)) {
                            setLoadingMessage(`Resolving dependencies for ${fileName}...`);

                            const dependencies = extractDependencies(arrayBuffer, fileKey);
                            const pendingFiles: string[] = [...dependencies];

                            if (pendingFiles.length > 0) {
                                console.log(`Found ${pendingFiles.length} initial dependencies`);
                                let depCount = 0;

                                // Download dependencies in parallel (5 concurrent)
                                const PARALLEL_LIMIT = 5;
                                while (pendingFiles.length > 0) {
                                    if (loadingCancelledRef.current) return;

                                    const batch = pendingFiles.splice(0, PARALLEL_LIMIT);
                                    const batchPromises = batch.map(async (depPath) => {
                                        if (
                                            loadedFiles.has(depPath) ||
                                            failedDownloads.has(depPath)
                                        )
                                            return;

                                        const content = await downloadFile(
                                            depPath,
                                            authHeader,
                                            false,
                                            false
                                        );
                                        if (content) {
                                            storeInWASM(depPath, content, USD, directory);
                                            loadedFiles.add(depPath);
                                            depCount++;
                                            setLoadingMessage(
                                                `Loading dependencies for ${fileName} (${depCount})...`
                                            );

                                            // Extract sub-dependencies
                                            if (isUSDFile(depPath)) {
                                                const subDeps = extractDependencies(
                                                    content,
                                                    depPath
                                                );
                                                for (const subDep of subDeps) {
                                                    if (
                                                        !loadedFiles.has(subDep) &&
                                                        !failedDownloads.has(subDep) &&
                                                        !pendingFiles.includes(subDep)
                                                    ) {
                                                        pendingFiles.push(subDep);
                                                    }
                                                }
                                            }
                                        } else {
                                            failedDownloads.add(depPath);
                                        }
                                    });

                                    await Promise.all(batchPromises);
                                }

                                console.log(
                                    `Loaded ${depCount} initial dependencies for ${fileKey}`
                                );
                            }
                        }

                        // Create file group for scene
                        const fileGroup = new THREE.Group();
                        fileGroup.name = fileName;
                        fileGroup.userData.sourceFile = fileKey;
                        scene.add(fileGroup);

                        fileInfos.push({ fileKey, fileName, directory, fileGroup });
                    } catch (fileError: any) {
                        console.error(`Error loading ${fileKey}:`, fileError);
                        errors.push({
                            file: fileKey,
                            error: fileError?.message || "Unknown error",
                        });
                    }
                }

                if (fileInfos.length === 0) {
                    throw new Error("No files loaded successfully");
                }

                // ============================================================
                // PHASE 2: Create single driver, verify, download missing, repeat
                // Key insight: Only ONE driver should be created for all files
                // ============================================================
                const maxRetries = 10;
                let retryCount = 0;
                let driver: any = null;
                const loadedGroups: any[] = [];
                let remainingMissingCount = 0;

                // Create single driver - it stays active throughout the verification loop
                console.log("\n=== Creating single driver for all files ===");
                setLoadingMessage("Verifying dependencies...");

                // Use the first file's group as the root for the driver
                const mainFileInfo = fileInfos[0];
                const delegateConfig = {
                    usdRoot: mainFileInfo.fileGroup,
                    paths: [],
                    driver: () => driver,
                };

                let renderInterface = new ThreeRenderDelegateInterface(delegateConfig);
                driver = new USD.HdWebSyncDriver(
                    renderInterface,
                    mainFileInfo.directory + "/" + mainFileInfo.fileName
                );
                if (driver instanceof Promise) driver = await driver;

                console.log(
                    `Driver created for: ${mainFileInfo.directory}/${mainFileInfo.fileName}`
                );

                await waitForWASMToSettle(1500);

                // Initial draw to trigger error reports
                try {
                    driver.Draw();
                } catch (drawError) {
                    console.warn("Initial draw error (expected for missing deps):", drawError);
                }

                // Wait for WASM to settle
                await waitForWASMToSettle(1500);

                // Get stage info z-axis
                if (driver) {
                    try {
                        let stage = driver.GetStage();
                        if (stage instanceof Promise) {
                            stage = await stage;
                            stage = driver.GetStage();
                        }
                        // Handle Z-up axis
                        if (stage.GetUpAxis && String.fromCharCode(stage.GetUpAxis()) === "z") {
                            let upAxis = String.fromCharCode(stage.GetUpAxis());
                            console.log("Up Axis from USD:", upAxis);
                            if (upAxis === "z") {
                                fileInfos[0].fileGroup.rotation.x = -Math.PI / 2;
                                console.log("Setting Z axis up");
                            }
                        }
                    } catch (e) {
                        console.warn("Error getting stage info:", e);
                    }
                }

                // Track total additional dependencies loaded during verification
                let totalAdditionalDepsLoaded = 0;

                // Skip verification loop for Deep Deps
                if (!allowDeepDepLoading) {
                    console.log("Skipping verification loop of loading deep dependencies");
                }

                // Main verification loop - drivers stay active, we just add files and redraw
                // Skip for Firefox due to WASM HTTP fallback issues
                while (retryCount < maxRetries && allowDeepDepLoading) {
                    if (loadingCancelledRef.current) {
                        console.log("Loading cancelled during verification");
                        break;
                    }

                    console.log(`\n=== Verification attempt ${retryCount + 1}/${maxRetries} ===`);

                    // Parse errors for missing assets
                    const missingAssets = parseWASMErrorsForMissingAssets(
                        capturedWASMErrorsRef.current
                    );
                    const newMissingAssets = missingAssets.filter(
                        (asset) => !failedDownloads.has(asset.path) && !loadedFiles.has(asset.path)
                    );

                    console.log(`Found ${newMissingAssets.length} new missing assets`);

                    // Clear captured errors for next iteration (including deps form this iteration)
                    capturedWASMErrorsRef.current = [];

                    if (newMissingAssets.length === 0) {
                        // No more missing assets - we're done!
                        console.log("All dependencies resolved!");
                        break;
                    }

                    //Destroy the driver and we'll recreate again
                    driver.delete();
                    let oldDriver = driver;
                    renderInterface = null;
                    driver = null;
                    console.log("Destroying driver to add additional dependencies");

                    // Download missing dependencies in parallel (5 concurrent)
                    // Files are added to WASM FS while drivers remain active
                    const totalMissing = newMissingAssets.length;
                    let downloadedCount = 0;
                    setLoadingMessage(`Downloading additional dependencies (0/${totalMissing})...`);

                    const PARALLEL_LIMIT = 5;
                    for (let i = 0; i < newMissingAssets.length; i += PARALLEL_LIMIT) {
                        if (loadingCancelledRef.current) break;

                        const batch = newMissingAssets.slice(i, i + PARALLEL_LIMIT);
                        const batchPromises = batch.map(async (asset) => {
                            const { fileIndex, path: assetPath } = asset;
                            const targetDirectory = `/file_${fileIndex}`;

                            console.log(
                                `[Verification] Downloading: ${assetPath} for file_${fileIndex}`
                            );
                            const content = await downloadFile(assetPath, authHeader, false, false);
                            // console.log(
                            //     `[Verification] Download result for ${assetPath}: ${
                            //         content ? `${content.byteLength} bytes` : "null (404)"
                            //     }`
                            // );

                            if (content) {
                                try {
                                    console.log(
                                        `[Verification] Storing ${assetPath} in ${targetDirectory}`
                                    );
                                    storeInWASM(assetPath, content, USD, targetDirectory);
                                    loadedFiles.add(assetPath);
                                    downloadedCount++;
                                    setLoadingMessage(
                                        `Downloading additional dependencies (${downloadedCount}/${totalMissing})...`
                                    );

                                    // Extract sub-dependencies from USD files
                                    if (isUSDFile(assetPath)) {
                                        const subDeps = extractDependencies(content, assetPath);
                                        for (const subDep of subDeps) {
                                            if (
                                                !loadedFiles.has(subDep) &&
                                                !failedDownloads.has(subDep)
                                            ) {
                                                // Queue for next verification iteration
                                                capturedWASMErrorsRef.current.push(
                                                    `Could not open asset @/file_${fileIndex}/${subDep}@`
                                                );
                                            }
                                        }
                                    }
                                } catch (storeError: any) {
                                    console.warn(`Error storing ${assetPath} in WASM:`, storeError);
                                    console.warn(
                                        `  Error details: errno=${storeError?.errno}, message=${storeError?.message}`
                                    );
                                    // Still mark as loaded to prevent infinite retries
                                    loadedFiles.add(assetPath);
                                }
                            } else {
                                console.log(`[Verification] Failed to download: ${assetPath}`);
                                failedDownloads.add(assetPath);
                            }
                        });

                        await Promise.all(batchPromises);
                    }

                    console.log(`Downloaded ${downloadedCount} additional dependencies`);
                    totalAdditionalDepsLoaded += downloadedCount;

                    if (downloadedCount === 0) {
                        // No new files downloaded - all remaining are 404s
                        remainingMissingCount = newMissingAssets.length - downloadedCount;
                        console.warn(
                            `No new files downloaded. ${remainingMissingCount} dependencies could not be found.`
                        );
                        break;
                    }

                    //Wait until we deleted the old driver
                    while (!oldDriver.isDeleted) {}

                    renderInterface = new ThreeRenderDelegateInterface(delegateConfig);
                    driver = new USD.HdWebSyncDriver(
                        renderInterface,
                        mainFileInfo.directory + "/" + mainFileInfo.fileName
                    );
                    if (driver instanceof Promise) driver = await driver;

                    await waitForWASMToSettle(1500);

                    console.log(
                        `Driver re-created for: ${mainFileInfo.directory}/${mainFileInfo.fileName}`
                    );

                    // Redraw driver to re-resolve references with newly added files
                    setLoadingMessage("Verifying dependencies...");
                    console.log("Redrawing driver with newly added files...");
                    try {
                        driver.Draw();
                    } catch (drawError) {
                        console.warn("Draw error during verification (expected):", drawError);
                    }

                    // Wait for WASM to settle again
                    await waitForWASMToSettle(2000);

                    retryCount++;
                }

                // Check if we hit max retries
                if (retryCount >= maxRetries) {
                    const finalMissing = parseWASMErrorsForMissingAssets(
                        capturedWASMErrorsRef.current
                    );
                    remainingMissingCount = finalMissing.filter(
                        (asset) => !failedDownloads.has(asset.path) && !loadedFiles.has(asset.path)
                    ).length;
                    if (remainingMissingCount > 0) {
                        console.warn(
                            `Max retries reached. ${remainingMissingCount} dependencies still missing.`
                        );
                    }
                }

                // Populate loadedGroups from fileInfos
                for (const info of fileInfos) {
                    loadedGroups.push(info.fileGroup);
                }

                // Get stage info from driver for animation
                if (driver) {
                    try {
                        let stage = driver.GetStage();
                        if (stage instanceof Promise) {
                            stage = await stage;
                            stage = driver.GetStage();
                        }
                        if (stage.GetEndTimeCode) {
                            endTimeCodeRef.current = stage.GetEndTimeCode();
                        }
                        if (stage.GetTimeCodesPerSecond) {
                            timeoutRef.current = 1000 / stage.GetTimeCodesPerSecond();
                        }
                    } catch (e) {
                        console.warn("Error getting stage info:", e);
                    }
                }

                // Set file errors if any
                if (errors.length > 0) setFileErrors(errors);
                if (loadedGroups.length === 0) throw new Error("No files loaded successfully");

                // Show warning if additional dependencies were loaded during verification
                // These may not render properly in the Needle viewer
                if (totalAdditionalDepsLoaded > 0 || remainingMissingCount > 0) {
                    setAdditionalDepsWarning({
                        loaded: totalAdditionalDepsLoaded,
                        unresolved: remainingMissingCount,
                    });
                    console.log(
                        `Warning: ${totalAdditionalDepsLoaded} additional deep dependencies were loaded during verification. ${remainingMissingCount} dependencies could not be resolved.`
                    );
                }

                console.log(`\n=== Total files loaded: ${loadedFiles.size} ===`);

                setLoadedFileGroups(loadedGroups);
                setLoadingMessage("Positioning camera...");

                const combinedBox = new THREE.Box3();
                loadedGroups.forEach((g) => combinedBox.union(new THREE.Box3().setFromObject(g)));
                const size = combinedBox.getSize(new THREE.Vector3());
                const center = combinedBox.getCenter(new THREE.Vector3());
                const maxSize = Math.max(size.x, size.y, size.z);
                const distance =
                    1.5 *
                    Math.max(
                        maxSize / (2 * Math.tan((Math.PI * camera.fov) / 360)),
                        maxSize / (2 * Math.tan((Math.PI * camera.fov) / 360)) / camera.aspect
                    );

                camera.position.set(distance, distance, distance);
                camera.lookAt(center);
                camera.near = distance / 100;
                camera.far = distance * 100;
                camera.updateProjectionMatrix();

                const mouseControls = new MouseControls(camera, renderer.domElement, THREE);
                mouseControls.setTarget(center.x, center.y, center.z);

                raycasterRef.current = new THREE.Raycaster();
                mouseRef.current = new THREE.Vector2();

                // IMPORTANT: Store original transforms AFTER all files are loaded and scene is set up
                // This ensures we capture the actual USD geometry positions after driver.Draw()
                console.log("Storing original transforms for all objects...");
                loadedGroups.forEach((group: any) => {
                    // Mark top-level children of each file group
                    const topLevelUuids = new Set(group.children.map((child: any) => child.uuid));

                    group.traverse((obj: any) => {
                        if (
                            obj.position &&
                            obj.rotation &&
                            obj.scale &&
                            !originalTransformsRef.current.has(obj.uuid)
                        ) {
                            const isTopLevel = topLevelUuids.has(obj.uuid);

                            originalTransformsRef.current.set(obj.uuid, {
                                position: {
                                    x: obj.position.x,
                                    y: obj.position.y,
                                    z: obj.position.z,
                                },
                                rotation: {
                                    x: obj.rotation.x,
                                    y: obj.rotation.y,
                                    z: obj.rotation.z,
                                },
                                scale: { x: obj.scale.x, y: obj.scale.y, z: obj.scale.z },
                                isTopLevel: isTopLevel,
                            });
                        }
                    });
                });
                console.log(
                    `Stored original transforms for ${originalTransformsRef.current.size} objects`
                );

                // Also store world coordinates for proper reset
                loadedGroups.forEach((group: any) => {
                    group.traverse((obj: any) => {
                        const stored = originalTransformsRef.current.get(obj.uuid);
                        if (stored && !stored.worldPosition) {
                            const worldPos = new THREE.Vector3();
                            obj.getWorldPosition(worldPos);

                            const worldQuat = new THREE.Quaternion();
                            obj.getWorldQuaternion(worldQuat);
                            const worldEuler = new THREE.Euler().setFromQuaternion(worldQuat);

                            const worldScale = new THREE.Vector3();
                            obj.getWorldScale(worldScale);

                            stored.worldPosition = { x: worldPos.x, y: worldPos.y, z: worldPos.z };
                            stored.worldRotation = {
                                x: worldEuler.x,
                                y: worldEuler.y,
                                z: worldEuler.z,
                            };
                            stored.worldScale = {
                                x: worldScale.x,
                                y: worldScale.y,
                                z: worldScale.z,
                            };
                        }
                    });
                });
                console.log("Added world coordinates to stored transforms");

                const handleCanvasClick = (event: MouseEvent) => {
                    // Check if 3D selection is enabled using ref (not state)
                    if (!enable3DSelectionRef.current) {
                        console.log("3D View: Selection disabled");
                        return;
                    }

                    // Don't process selection if camera was moved
                    if (mouseControls.hasMoved) {
                        console.log("3D View: Ignoring click - camera was moved");
                        return;
                    }

                    const rect = renderer.domElement.getBoundingClientRect();
                    mouseRef.current.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
                    mouseRef.current.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
                    raycasterRef.current.setFromCamera(mouseRef.current, camera);
                    const allChildren: any[] = [];
                    loadedGroups.forEach((g) => allChildren.push(...g.children));
                    const intersects = raycasterRef.current.intersectObjects(allChildren, true);

                    // Only process selection if we hit an object
                    if (intersects.length > 0) {
                        const clickedObject = intersects[0].object;
                        console.log(
                            "3D View: Object clicked",
                            clickedObject.name,
                            "Ctrl:",
                            event.ctrlKey
                        );

                        if (event.ctrlKey) {
                            // Ctrl+Click: Toggle selection (add/remove)
                            setSelectedObjects((prev) => {
                                const exists = prev.find((obj) => obj.uuid === clickedObject.uuid);
                                return exists
                                    ? prev.filter((obj) => obj.uuid !== clickedObject.uuid)
                                    : [...prev, clickedObject];
                            });
                        } else {
                            // Regular click: Toggle if already selected, otherwise select
                            setSelectedObjects((prev) => {
                                const isAlreadySelected =
                                    prev.length === 1 && prev[0].uuid === clickedObject.uuid;
                                return isAlreadySelected ? [] : [clickedObject];
                            });
                        }
                    } else {
                        // Clicked empty space: Do nothing (keep current selection)
                        console.log("3D View: Clicked empty space - keeping current selection");
                    }
                };

                renderer.domElement.addEventListener("click", handleCanvasClick);

                viewerInstanceRef.current = {
                    scene,
                    camera,
                    renderer,
                    driver: driver,
                    USD,
                    fileGroups: loadedGroups,
                    controls: mouseControls,
                    clickHandler: handleCanvasClick,
                };

                const animate = () => {
                    // Store frame ID for cancellation
                    animationFrameRef.current = requestAnimationFrame(animate);

                    // Calculate animation time (based on reference implementation)
                    const secs = Date.now() / 1000;
                    const time = (secs * (1000 / timeoutRef.current)) % endTimeCodeRef.current;

                    // Update animation time if not paused (use ref, not state)
                    if (!animationPausedRef.current && driver?.SetTime) {
                        driver.SetTime(time);
                    }

                    // Always draw and render to keep scene visible
                    if (driver?.Draw) {
                        driver.Draw();
                    }
                    renderer.render(scene, camera);
                };
                animate();

                setSceneReady(true);
                window.addEventListener("resize", () => {
                    if (containerRef.current) {
                        camera.aspect =
                            containerRef.current.clientWidth / containerRef.current.clientHeight;
                        camera.updateProjectionMatrix();
                        renderer.setSize(
                            containerRef.current.clientWidth,
                            containerRef.current.clientHeight
                        );
                    }
                });

                setIsLoading(false);
            } catch (error) {
                console.error("Error loading USD assets:", error);
                setError(error instanceof Error ? error.message : "Failed to load USD files");
                setIsLoading(false);
            }
        };

        loadAssets();

        return () => {
            console.log("NeedleUSD Viewer: Cleanup initiated");

            // Cancel any ongoing loading operations
            loadingCancelledRef.current = true;

            // Cancel animation frame
            if (animationFrameRef.current !== null) {
                cancelAnimationFrame(animationFrameRef.current);
                animationFrameRef.current = null;
            }

            if (viewerInstanceRef.current) {
                const { renderer, controls, clickHandler, USD } = viewerInstanceRef.current;

                // Remove event listeners
                if (clickHandler && renderer?.domElement) {
                    renderer.domElement.removeEventListener("click", clickHandler);
                }

                // Dispose controls
                controls?.dispose();

                // Clean WASM filesystem
                if (USD) {
                    cleanupWASMFilesystem(USD);
                }

                // Remove renderer from DOM
                if (renderer?.domElement?.parentNode) {
                    renderer.domElement.parentNode.removeChild(renderer.domElement);
                }

                // Dispose renderer
                renderer?.dispose();
            }

            NeedleUSDDependencyManager.cleanup();
            console.log("NeedleUSD Viewer: Cleanup complete");
        };
    }, [assetKey, multiFileKeys, assetId, databaseId, versionId, config]);

    useEffect(() => {
        const handleKeyPress = (event: KeyboardEvent) => {
            if (
                event.target instanceof HTMLInputElement ||
                event.target instanceof HTMLTextAreaElement
            )
                return;

            const key = event.key.toLowerCase();

            if (key === "escape" && showPanel) {
                setShowPanel(false);
            } else if (key === "f" && viewerInstanceRef.current) {
                // F key: Fit to scene
                const { camera, controls, fileGroups } = viewerInstanceRef.current;
                const THREE = (window as any).THREE;

                if (camera && controls && fileGroups && THREE) {
                    const fileGroupsArray = Array.isArray(fileGroups) ? fileGroups : [fileGroups];
                    const box = new THREE.Box3();
                    fileGroupsArray.forEach((g: any) =>
                        box.union(new THREE.Box3().setFromObject(g))
                    );

                    const size = box.getSize(new THREE.Vector3());
                    const center = box.getCenter(new THREE.Vector3());
                    const maxSize = Math.max(size.x, size.y, size.z);
                    const fitHeightDistance =
                        maxSize / (2 * Math.tan((Math.PI * camera.fov) / 360));
                    const fitWidthDistance = fitHeightDistance / camera.aspect;
                    const distance = 1.5 * Math.max(fitHeightDistance, fitWidthDistance);

                    camera.position.set(
                        center.x + distance * 0.5,
                        center.y + distance * 0.5,
                        center.z + distance * 0.5
                    );
                    camera.lookAt(center);
                    controls.setTarget(center.x, center.y, center.z);
                    camera.near = distance / 100;
                    camera.far = distance * 100;
                    camera.updateProjectionMatrix();

                    console.log("Fit to scene (F key)");
                }
            }
        };
        window.addEventListener("keydown", handleKeyPress);
        return () => window.removeEventListener("keydown", handleKeyPress);
    }, [showPanel]);

    // Material Library Operations
    const handleCreateMaterial = () => {
        const THREE = (window as any).THREE;
        if (!THREE) return;

        const newMaterial = new THREE.MeshStandardMaterial({
            color: 0xffffff,
            metalness: 0.5,
            roughness: 0.5,
            opacity: 1.0,
            transparent: false,
        });

        const materialId = `custom_material_${Date.now()}`;
        const materialName = `Material_${String(materialCounterRef.current).padStart(3, "0")}`;
        materialCounterRef.current++;

        const newItem: MaterialLibraryItem = {
            id: materialId,
            name: materialName,
            material: newMaterial,
            usedBy: new Set(),
            isCustom: true,
            originalMaterial: newMaterial.clone(),
        };

        setMaterialLibrary((prev) => new Map(prev).set(materialId, newItem));
        setSelectedMaterialId(materialId);

        console.log(`Created new material: ${materialName}`);
    };

    const handleRenameMaterial = (materialId: string, newName: string) => {
        setMaterialLibrary((prev) => {
            const newLib = new Map(prev);
            const item = newLib.get(materialId);
            if (item) {
                item.name = newName;
                item.material.name = newName;
                newLib.set(materialId, { ...item });
            }
            return newLib;
        });
        console.log(`Renamed material to: ${newName}`);
    };

    const handleMaterialChange = (materialId: string, materialState: any) => {
        const THREE = (window as any).THREE;
        if (!THREE) return;

        const item = materialLibrary.get(materialId);
        if (!item) return;

        const mat = item.material;

        // Apply changes to the material
        if (materialState.color) {
            mat.color = new THREE.Color(materialState.color);
        }
        if (materialState.emissive) {
            mat.emissive = new THREE.Color(materialState.emissive);
        }
        if (mat.metalness !== undefined) {
            mat.metalness = materialState.metalness;
        }
        if (mat.roughness !== undefined) {
            mat.roughness = materialState.roughness;
        }
        mat.opacity = materialState.opacity;
        mat.transparent = materialState.transparent;
        mat.wireframe = materialState.wireframe;
        mat.needsUpdate = true;

        // Update all objects using this material (including highlighted ones)
        if (viewerInstanceRef.current?.scene) {
            viewerInstanceRef.current.scene.traverse((obj: any) => {
                if (item.usedBy.has(obj.uuid)) {
                    // Check if object is selected (has highlight)
                    const isSelected = selectedObjects.some((sel) => sel.uuid === obj.uuid);

                    if (isSelected) {
                        // Update highlight material
                        if (materialState.color)
                            obj.material.color = new THREE.Color(materialState.color);
                        obj.material.emissive = new THREE.Color(0x4caf50); // Keep green highlight
                        obj.material.emissiveIntensity = 0.5;
                        if (obj.material.metalness !== undefined)
                            obj.material.metalness = materialState.metalness;
                        if (obj.material.roughness !== undefined)
                            obj.material.roughness = materialState.roughness;
                        obj.material.opacity = materialState.opacity;
                        obj.material.transparent = materialState.transparent;
                        obj.material.wireframe = materialState.wireframe;
                        obj.material.needsUpdate = true;
                    }
                    // Non-selected objects already use the base material, so they update automatically
                }
            });
        }

        console.log(`Material ${item.name} updated`);
    };

    const handleResetMaterial = (materialId: string) => {
        const THREE = (window as any).THREE;
        if (!THREE) return;

        const item = materialLibrary.get(materialId);
        if (!item || !item.originalMaterial) return;

        const original = item.originalMaterial;
        const mat = item.material;

        // Reset material properties
        if (original.color) mat.color = original.color.clone();
        if (original.emissive) mat.emissive = original.emissive.clone();
        if (original.metalness !== undefined) mat.metalness = original.metalness;
        if (original.roughness !== undefined) mat.roughness = original.roughness;
        mat.opacity = original.opacity;
        mat.transparent = original.transparent;
        mat.wireframe = original.wireframe;
        mat.needsUpdate = true;

        // Update highlighted objects too
        if (viewerInstanceRef.current?.scene) {
            viewerInstanceRef.current.scene.traverse((obj: any) => {
                if (item.usedBy.has(obj.uuid)) {
                    const isSelected = selectedObjects.some((sel) => sel.uuid === obj.uuid);
                    if (isSelected && obj.material) {
                        if (original.color) obj.material.color = original.color.clone();
                        obj.material.emissive = new THREE.Color(0x4caf50);
                        obj.material.emissiveIntensity = 0.5;
                        if (original.metalness !== undefined)
                            obj.material.metalness = original.metalness;
                        if (original.roughness !== undefined)
                            obj.material.roughness = original.roughness;
                        obj.material.opacity = original.opacity;
                        obj.material.transparent = original.transparent;
                        obj.material.wireframe = original.wireframe;
                        obj.material.needsUpdate = true;
                    }
                }
            });
        }

        console.log(`Material ${item.name} reset to original`);
    };

    const handleDuplicateMaterial = (materialId: string) => {
        const THREE = (window as any).THREE;
        if (!THREE) return;

        const item = materialLibrary.get(materialId);
        if (!item) return;

        const duplicatedMaterial = item.material.clone();
        const newMaterialId = `duplicated_${Date.now()}`;
        const newMaterialName = `${item.name}_Copy`;

        const newItem: MaterialLibraryItem = {
            id: newMaterialId,
            name: newMaterialName,
            material: duplicatedMaterial,
            usedBy: new Set(),
            isCustom: true,
            originalMaterial: duplicatedMaterial.clone(),
        };

        setMaterialLibrary((prev) => new Map(prev).set(newMaterialId, newItem));
        setSelectedMaterialId(newMaterialId);

        console.log(`Duplicated material: ${item.name} -> ${newMaterialName}`);
    };

    const handleDeleteMaterial = (materialId: string) => {
        const item = materialLibrary.get(materialId);
        if (!item) return;

        if (item.usedBy.size > 0) {
            console.warn(`Cannot delete material ${item.name} - still in use`);
            return;
        }

        setMaterialLibrary((prev) => {
            const newLib = new Map(prev);
            newLib.delete(materialId);
            return newLib;
        });

        if (selectedMaterialId === materialId) {
            setSelectedMaterialId(null);
        }

        console.log(`Deleted material: ${item.name}`);
    };

    const handleAssignMaterial = (objectUuid: string, materialId: string) => {
        const THREE = (window as any).THREE;
        if (!THREE || !viewerInstanceRef.current?.scene) return;

        const item = materialLibrary.get(materialId);
        if (!item) return;

        const scene = viewerInstanceRef.current.scene;
        const object = scene.getObjectByProperty("uuid", objectUuid);
        if (!object) return;

        // Remove object from old material's usedBy
        Array.from(materialLibrary.values()).forEach((libItem) => {
            if (libItem.usedBy.has(objectUuid)) {
                libItem.usedBy.delete(objectUuid);
            }
        });

        // Add object to new material's usedBy
        item.usedBy.add(objectUuid);

        // Assign material (instance/share)
        const isSelected = selectedObjects.some((sel) => sel.uuid === objectUuid);
        if (isSelected) {
            // Create highlight version
            const highlightMaterial = item.material.clone();
            highlightMaterial.emissive = new THREE.Color(0x4caf50);
            highlightMaterial.emissiveIntensity = 0.5;
            object.material = highlightMaterial;
        } else {
            // Use base material
            object.material = item.material;
        }

        // Update library to trigger re-render
        setMaterialLibrary(new Map(materialLibrary));

        console.log(`Assigned material ${item.name} to object ${object.name}`);
    };

    const handleMakeUnique = (objectUuid: string) => {
        const THREE = (window as any).THREE;
        if (!THREE || !viewerInstanceRef.current?.scene) return;

        const scene = viewerInstanceRef.current.scene;
        const object = scene.getObjectByProperty("uuid", objectUuid);
        if (!object || !object.material) return;

        // Find current material
        const currentItem = Array.from(materialLibrary.values()).find((item) =>
            item.usedBy.has(objectUuid)
        );

        if (!currentItem) return;

        // Remove from current material's usedBy
        currentItem.usedBy.delete(objectUuid);

        // Create new unique material
        const uniqueMaterial = currentItem.material.clone();
        const newMaterialId = `unique_${Date.now()}`;
        const newMaterialName = `${currentItem.name}_Unique`;

        const newItem: MaterialLibraryItem = {
            id: newMaterialId,
            name: newMaterialName,
            material: uniqueMaterial,
            usedBy: new Set([objectUuid]),
            isCustom: true,
            originalMaterial: uniqueMaterial.clone(),
        };

        // Add to library
        setMaterialLibrary((prev) => new Map(prev).set(newMaterialId, newItem));

        // Assign to object
        const isSelected = selectedObjects.some((sel) => sel.uuid === objectUuid);
        if (isSelected) {
            const highlightMaterial = uniqueMaterial.clone();
            highlightMaterial.emissive = new THREE.Color(0x4caf50);
            highlightMaterial.emissiveIntensity = 0.5;
            object.material = highlightMaterial;
        } else {
            object.material = uniqueMaterial;
        }

        console.log(`Made material unique for object ${object.name}`);
    };

    const handleCreateAndAssign = (objectUuid: string) => {
        const THREE = (window as any).THREE;
        if (!THREE || !viewerInstanceRef.current?.scene) return;

        // Create new material
        const newMaterial = new THREE.MeshStandardMaterial({
            color: 0xffffff,
            metalness: 0.5,
            roughness: 0.5,
            opacity: 1.0,
            transparent: false,
        });

        const materialId = `custom_material_${Date.now()}`;
        const materialName = `Material_${String(materialCounterRef.current).padStart(3, "0")}`;
        materialCounterRef.current++;

        const newItem: MaterialLibraryItem = {
            id: materialId,
            name: materialName,
            material: newMaterial,
            usedBy: new Set([objectUuid]),
            isCustom: true,
            originalMaterial: newMaterial.clone(),
        };

        // Remove object from old material's usedBy
        Array.from(materialLibrary.values()).forEach((libItem) => {
            if (libItem.usedBy.has(objectUuid)) {
                libItem.usedBy.delete(objectUuid);
            }
        });

        // Add to library
        setMaterialLibrary((prev) => new Map(prev).set(materialId, newItem));

        // Assign to object
        const scene = viewerInstanceRef.current.scene;
        const object = scene.getObjectByProperty("uuid", objectUuid);
        if (object) {
            const isSelected = selectedObjects.some((sel) => sel.uuid === objectUuid);
            if (isSelected) {
                const highlightMaterial = newMaterial.clone();
                highlightMaterial.emissive = new THREE.Color(0x4caf50);
                highlightMaterial.emissiveIntensity = 0.5;
                object.material = highlightMaterial;
            } else {
                object.material = newMaterial;
            }
        }

        console.log(`Created and assigned material ${materialName} to object`);
    };

    const handleResetAllTransforms = () => {
        if (!originalTransformsRef.current || !viewerInstanceRef.current?.fileGroups) return;

        const THREE = (window as any).THREE;
        if (!THREE) return;

        const fileGroups = Array.isArray(viewerInstanceRef.current.fileGroups)
            ? viewerInstanceRef.current.fileGroups
            : [viewerInstanceRef.current.fileGroups];

        fileGroups.forEach((group: any) => {
            group.traverse((obj: any) => {
                const original = originalTransformsRef.current.get(obj.uuid);
                if (original) {
                    if (
                        original.isTopLevel &&
                        original.worldPosition &&
                        original.worldRotation &&
                        original.worldScale
                    ) {
                        // Top-level object: Use world coordinates
                        const parent = obj.parent;
                        if (parent) {
                            // Get parent's inverse world matrix
                            const parentWorldMatrix = new THREE.Matrix4();
                            parent.updateMatrixWorld(true);
                            parentWorldMatrix.copy(parent.matrixWorld);
                            const parentInverse = new THREE.Matrix4()
                                .copy(parentWorldMatrix)
                                .invert();

                            // Create target world transform
                            const targetWorldPos = new THREE.Vector3(
                                original.worldPosition.x,
                                original.worldPosition.y,
                                original.worldPosition.z
                            );
                            const targetWorldRot = new THREE.Euler(
                                original.worldRotation.x,
                                original.worldRotation.y,
                                original.worldRotation.z
                            );
                            const targetWorldScale = new THREE.Vector3(
                                original.worldScale.x,
                                original.worldScale.y,
                                original.worldScale.z
                            );

                            // Convert world transform to local space
                            const worldMatrix = new THREE.Matrix4();
                            worldMatrix.compose(
                                targetWorldPos,
                                new THREE.Quaternion().setFromEuler(targetWorldRot),
                                targetWorldScale
                            );

                            const localMatrix = new THREE.Matrix4();
                            localMatrix.multiplyMatrices(parentInverse, worldMatrix);

                            // Extract local transform
                            const localPos = new THREE.Vector3();
                            const localQuat = new THREE.Quaternion();
                            const localScale = new THREE.Vector3();
                            localMatrix.decompose(localPos, localQuat, localScale);
                            const localRot = new THREE.Euler().setFromQuaternion(localQuat);

                            obj.position.copy(localPos);
                            obj.rotation.copy(localRot);
                            obj.scale.copy(localScale);
                        } else {
                            // No parent, world = local
                            obj.position.set(
                                original.worldPosition.x,
                                original.worldPosition.y,
                                original.worldPosition.z
                            );
                            obj.rotation.set(
                                original.worldRotation.x,
                                original.worldRotation.y,
                                original.worldRotation.z
                            );
                            obj.scale.set(
                                original.worldScale.x,
                                original.worldScale.y,
                                original.worldScale.z
                            );
                        }
                    } else {
                        // Sub-object: Use local coordinates
                        obj.position.set(
                            original.position.x,
                            original.position.y,
                            original.position.z
                        );
                        obj.rotation.set(
                            original.rotation.x,
                            original.rotation.y,
                            original.rotation.z
                        );
                        obj.scale.set(original.scale.x, original.scale.y, original.scale.z);
                    }
                    obj.updateMatrix();
                    obj.updateMatrixWorld(true);
                }
            });
        });
        console.log("All object transforms reset to original (using smart reset logic)");
    };

    const handleEditMaterial = (materialId: string) => {
        // Select the material and the Panel will handle showing it in the Material Library tab
        setSelectedMaterialId(materialId);
        console.log(`Edit material requested: ${materialId}`);
    };

    const handleResetAllMaterials = () => {
        const THREE = (window as any).THREE;
        if (!THREE || !viewerInstanceRef.current?.scene) return;

        // Reset all materials in the library to their original state
        Array.from(materialLibrary.values()).forEach((item) => {
            if (item.originalMaterial) {
                const original = item.originalMaterial;
                const mat = item.material;

                // Reset material properties
                if (original.color) mat.color = original.color.clone();
                if (original.emissive) mat.emissive = original.emissive.clone();
                if (original.metalness !== undefined) mat.metalness = original.metalness;
                if (original.roughness !== undefined) mat.roughness = original.roughness;
                mat.opacity = original.opacity;
                mat.transparent = original.transparent;
                mat.wireframe = original.wireframe;
                mat.needsUpdate = true;
            }
        });

        // Update all objects in the scene to use their library materials (remove highlights)
        const scene = viewerInstanceRef.current.scene;
        scene.traverse((obj: any) => {
            if (obj.material) {
                // Find the material in library for this object
                Array.from(materialLibrary.values()).forEach((item) => {
                    if (item.usedBy.has(obj.uuid)) {
                        obj.material = item.material;
                    }
                });
            }
        });

        console.log("All materials reset to original");
    };

    if (error) {
        return (
            <div
                style={{ position: "relative", height: "100%", backgroundColor: "#f5f5f5" }}
                id="usd-viewer-root"
            >
                <div
                    style={{
                        color: "#d13212",
                        fontSize: "1.4em",
                        lineHeight: "1.5",
                        width: "100%",
                        position: "absolute",
                        top: "50%",
                        left: "50%",
                        transform: "translate(-50%, -50%)",
                        textAlign: "center",
                        padding: "20px",
                    }}
                >
                    {error}
                    <br />
                    <br />
                    <span style={{ fontSize: ".9em", color: "#d13212" }}>
                        Please ensure the file is a valid USD format (.usd, .usda, .usdz)
                    </span>
                </div>
            </div>
        );
    }

    return (
        <div style={{ position: "relative", height: "100%", width: "100%" }} id="usd-viewer-root">
            <div
                ref={containerRef}
                style={{ width: "100%", height: "100%" }}
                id="usd-viewer-container"
            />

            {fileErrors.length > 0 && !isLoading && (
                <div
                    style={{
                        position: "absolute",
                        top: "0",
                        left: "0",
                        right: "0",
                        backgroundColor: "#fff3cd",
                        border: "1px solid #ffc107",
                        borderRadius: "4px",
                        padding: "12px 16px",
                        margin: "8px",
                        zIndex: 1001,
                        fontSize: "0.85em",
                        maxHeight: "150px",
                        overflowY: "auto",
                    }}
                >
                    <div style={{ color: "#856404", fontWeight: "bold", marginBottom: "8px" }}>
                        ⚠️{" "}
                        {loadedFileGroups.length === 0
                            ? "All files failed to load"
                            : "Some files failed to load"}{" "}
                        ({fileErrors.length}/{multiFileKeys?.length || 1})
                    </div>
                    {fileErrors.map((err, idx) => (
                        <div
                            key={idx}
                            style={{
                                color: "#666",
                                marginBottom: "4px",
                                paddingLeft: "8px",
                                borderLeft: "2px solid #ffc107",
                            }}
                        >
                            <strong>{err.file.split("/").pop()}</strong>: {err.error}
                        </div>
                    ))}
                    <button
                        onClick={() => setFileErrors([])}
                        style={{
                            position: "absolute",
                            top: "8px",
                            right: "8px",
                            background: "none",
                            border: "none",
                            color: "#856404",
                            cursor: "pointer",
                            fontSize: "16px",
                            padding: "0",
                            width: "20px",
                            height: "20px",
                        }}
                        title="Dismiss warnings"
                    >
                        ×
                    </button>
                </div>
            )}

            {(additionalDepsWarning.loaded > 0 || additionalDepsWarning.unresolved > 0) &&
                !isLoading && (
                    <div
                        style={{
                            position: "absolute",
                            top: fileErrors.length > 0 ? "170px" : "0",
                            left: "0",
                            right: "0",
                            backgroundColor: "#cce5ff",
                            border: "1px solid #004085",
                            borderRadius: "4px",
                            padding: "12px 16px",
                            margin: "8px",
                            zIndex: 1001,
                            fontSize: "0.85em",
                        }}
                    >
                        <div style={{ color: "#004085", fontWeight: "bold", marginBottom: "4px" }}>
                            ℹ️ {additionalDepsWarning.loaded} additional compressed dependencies
                            were recognized and loaded
                            {additionalDepsWarning.unresolved > 0 &&
                                `. ${additionalDepsWarning.unresolved} dependencies could not be resolved.`}
                        </div>
                        <div style={{ color: "#004085", fontSize: "0.9em" }}>
                            Note: The Needle USD viewer may not properly reload these dependencies.
                            Some textures or referenced files may not display correctly.
                        </div>
                        <button
                            onClick={() => setAdditionalDepsWarning({ loaded: 0, unresolved: 0 })}
                            style={{
                                position: "absolute",
                                top: "8px",
                                right: "8px",
                                background: "none",
                                border: "none",
                                color: "#004085",
                                cursor: "pointer",
                                fontSize: "16px",
                                padding: "0",
                                width: "20px",
                                height: "20px",
                            }}
                            title="Dismiss"
                        >
                            ×
                        </button>
                    </div>
                )}

            {isLoading && <LoadingSpinner message={loadingMessage} />}

            {sceneReady && viewerInstanceRef.current && showPanel && (
                <NeedleUSDPanel
                    scene={viewerInstanceRef.current.scene}
                    camera={viewerInstanceRef.current.camera}
                    renderer={viewerInstanceRef.current.renderer}
                    usdRoot={viewerInstanceRef.current.fileGroups}
                    controls={viewerInstanceRef.current.controls}
                    selectedObjects={selectedObjects}
                    onSelectObjects={setSelectedObjects}
                    onClose={() => setShowPanel(false)}
                    originalTransforms={originalTransformsRef.current}
                    materialLibrary={materialLibrary}
                    selectedMaterialId={selectedMaterialId}
                    onSelectMaterial={setSelectedMaterialId}
                    onCreateMaterial={handleCreateMaterial}
                    onRenameMaterial={handleRenameMaterial}
                    onMaterialChange={handleMaterialChange}
                    onResetMaterial={handleResetMaterial}
                    onDuplicateMaterial={handleDuplicateMaterial}
                    onDeleteMaterial={handleDeleteMaterial}
                    onAssignMaterial={handleAssignMaterial}
                    onMakeUnique={handleMakeUnique}
                    onCreateAndAssign={handleCreateAndAssign}
                    onEditMaterial={handleEditMaterial}
                    onResetAllTransforms={handleResetAllTransforms}
                    onResetAllMaterials={handleResetAllMaterials}
                    onClearSelection={() => setSelectedObjects([])}
                    enable3DSelection={enable3DSelection}
                    onToggle3DSelection={setEnable3DSelection}
                    animationPaused={animationPaused}
                    onToggleAnimation={setAnimationPaused}
                />
            )}

            {sceneReady && !showPanel && (
                <button
                    onClick={() => setShowPanel(true)}
                    style={{
                        position: "absolute",
                        top: fileErrors.length > 0 ? "170px" : "20px",
                        left: "10px",
                        backgroundColor: "rgba(0, 0, 0, 0.7)",
                        color: "white",
                        border: "1px solid rgba(255, 255, 255, 0.2)",
                        padding: "8px 12px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.8em",
                        zIndex: 1000,
                    }}
                    title="Show controls panel"
                >
                    ⚙️ Panel
                </button>
            )}

            {sceneReady && viewerInstanceRef.current && (
                <div
                    style={{
                        position: "absolute",
                        top: fileErrors.length > 0 ? "auto" : "10px",
                        bottom: fileErrors.length > 0 ? "10px" : "auto",
                        right: "10px",
                        color: "white",
                        fontSize: "12px",
                        backgroundColor: "rgba(0,0,0,0.7)",
                        padding: "8px",
                        borderRadius: "4px",
                        zIndex: 1000,
                    }}
                >
                    <div style={{ fontWeight: "bold", marginBottom: "4px" }}>Needle USD Viewer</div>
                    <div style={{ fontSize: "0.9em", opacity: 0.9 }}>
                        Mouse: Rotate | Wheel: Zoom | Right-click: Pan
                    </div>
                    {(() => {
                        const scene = viewerInstanceRef.current.scene;
                        let totalObjects = 0;
                        let totalVertices = 0;

                        scene.traverse((obj: any) => {
                            if (
                                obj.type !== "Scene" &&
                                !obj.type.includes("Camera") &&
                                !obj.type.includes("Light")
                            ) {
                                totalObjects++;
                            }
                            if (obj.geometry?.attributes?.position) {
                                totalVertices += obj.geometry.attributes.position.count;
                            }
                        });

                        return (
                            <>
                                {loadedFileGroups.length > 0 && (
                                    <div
                                        style={{
                                            fontSize: "0.85em",
                                            marginTop: "6px",
                                            color: "#4CAF50",
                                        }}
                                    >
                                        📁 {loadedFileGroups.length} file
                                        {loadedFileGroups.length !== 1 ? "s" : ""}
                                    </div>
                                )}
                                {totalObjects > 0 && (
                                    <div
                                        style={{
                                            fontSize: "0.85em",
                                            marginTop: "4px",
                                            color: "#2196F3",
                                        }}
                                    >
                                        📦 {totalObjects.toLocaleString()} object
                                        {totalObjects !== 1 ? "s" : ""}
                                    </div>
                                )}
                                {materialLibrary.size > 0 && (
                                    <div
                                        style={{
                                            fontSize: "0.85em",
                                            marginTop: "4px",
                                            color: "#9C27B0",
                                        }}
                                    >
                                        🎨 {materialLibrary.size} material
                                        {materialLibrary.size !== 1 ? "s" : ""}
                                    </div>
                                )}
                                {totalVertices > 0 && (
                                    <div
                                        style={{
                                            fontSize: "0.85em",
                                            marginTop: "4px",
                                            color: "#FF9800",
                                        }}
                                    >
                                        ▲ {totalVertices.toLocaleString()} vertices
                                    </div>
                                )}
                                {fileErrors.length > 0 && (
                                    <div
                                        style={{
                                            fontSize: "0.85em",
                                            marginTop: "6px",
                                            color: "#ffc107",
                                        }}
                                    >
                                        ⚠️ {fileErrors.length} file(s) failed
                                    </div>
                                )}
                            </>
                        );
                    })()}
                </div>
            )}
        </div>
    );
};

export default NeedleUSDViewerComponent;
