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
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [config] = useState(Cache.getItem("config"));
    const [usdViewerReady, setUsdViewerReady] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [loadingMessage, setLoadingMessage] = useState("Initializing viewer...");
    const [error, setError] = useState<string | null>(null);
    const [fileErrors, setFileErrors] = useState<Array<{ file: string; error: string }>>([]);
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
        const initializeUSDViewer = async () => {
            try {
                setLoadingMessage("Loading USD Viewer dependencies...");
                await NeedleUSDDependencyManager.loadUSDViewer();
                setUsdViewerReady(true);
            } catch (error) {
                console.error("Failed to initialize USD Viewer:", error);
                setError("Failed to load USD Viewer dependencies");
                setIsLoading(false);
            }
        };

        if (!usdViewerReady) {
            initializeUSDViewer();
        }
    }, [usdViewerReady]);

    useEffect(() => {
        const hasFiles = assetKey || (multiFileKeys && multiFileKeys.length > 0);
        if (
            !hasFiles ||
            initializationRef.current ||
            !usdViewerReady ||
            !config ||
            !containerRef.current
        ) {
            return;
        }
        initializationRef.current = true;

        // PRESERVED: Original implementation using downloadAsset API (unused, kept for reference)
        // This function downloads files using the downloadAsset API which requires pre-signed URLs.
        // It has been replaced by loadAssets() which uses direct streaming URLs instead.
        const loadAssetsFromDownloadAPI = async () => {
            try {
                setLoadingMessage("Initializing 3D scene...");
                const THREE = (window as any).THREE;
                const ThreeRenderDelegateInterface = (window as any).ThreeRenderDelegateInterface;
                const getUsdModule = (globalThis as any)["NEEDLE:USD:GET"];

                if (!THREE || !ThreeRenderDelegateInterface || !getUsdModule) {
                    throw new Error("USD Viewer dependencies not loaded");
                }

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
                const USD = await getUsdModule({
                    locateFile: (path: string, prefix: string) =>
                        (prefix || "/viewers/needletools_usd_viewer/") + path,
                });

                const filesToLoad =
                    multiFileKeys && multiFileKeys.length > 0
                        ? multiFileKeys
                        : assetKey
                        ? [assetKey]
                        : [];
                if (filesToLoad.length === 0) throw new Error("No files specified");

                const loadedGroups: any[] = [];
                const errors: Array<{ file: string; error: string }> = [];
                const allDrivers: any[] = [];

                for (let i = 0; i < filesToLoad.length; i++) {
                    const fileKey = filesToLoad[i];
                    const fileName = fileKey.split("/").pop() || `model_${i}.usd`;
                    setLoadingMessage(
                        `Downloading file ${i + 1}/${filesToLoad.length}: ${fileName}...`
                    );

                    try {
                        const response = await downloadAsset({
                            assetId,
                            databaseId,
                            key: fileKey,
                            versionId: "",
                            downloadType: "assetFile",
                        });
                        if (!response || !Array.isArray(response) || response[0] === false)
                            throw new Error("Download failed");

                        setLoadingMessage(
                            `Loading file ${i + 1}/${filesToLoad.length}: ${fileName}...`
                        );
                        const fileResponse = await fetch(response[1]);
                        if (!fileResponse.ok)
                            throw new Error(`Fetch failed: ${fileResponse.statusText}`);

                        const arrayBuffer = await fileResponse.arrayBuffer();
                        const fileGroup = new THREE.Group();
                        fileGroup.name = fileName;
                        fileGroup.userData.sourceFile = fileKey;
                        scene.add(fileGroup);

                        const directory = `/file_${i}`;
                        USD.FS_createPath("", directory, true, true);
                        USD.FS_createDataFile(
                            directory,
                            fileName,
                            new Uint8Array(arrayBuffer),
                            true,
                            true,
                            true
                        );

                        const delegateConfig = {
                            usdRoot: fileGroup,
                            paths: [],
                            driver: () => driver,
                        };
                        const renderInterface = new ThreeRenderDelegateInterface(delegateConfig);
                        let driver = new USD.HdWebSyncDriver(
                            renderInterface,
                            directory + "/" + fileName
                        );
                        if (driver instanceof Promise) driver = await driver;

                        driver.Draw();
                        let stage = driver.GetStage();
                        if (stage instanceof Promise) {
                            stage = await stage;
                            stage = driver.GetStage();
                        }

                        // Store animation timing information
                        if (stage.GetEndTimeCode) {
                            endTimeCodeRef.current = stage.GetEndTimeCode();
                        }
                        if (stage.GetTimeCodesPerSecond) {
                            timeoutRef.current = 1000 / stage.GetTimeCodesPerSecond();
                        }

                        if (stage.GetUpAxis && String.fromCharCode(stage.GetUpAxis()) === "z") {
                            fileGroup.rotation.x = -Math.PI / 2;
                        }

                        loadedGroups.push(fileGroup);
                        allDrivers.push(driver);
                    } catch (fileError: any) {
                        errors.push({
                            file: fileKey,
                            error: fileError?.message || "Unknown error",
                        });
                    }
                }

                if (errors.length > 0) setFileErrors(errors);
                if (loadedGroups.length === 0) throw new Error("No files loaded successfully");

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

                const handleCanvasClick = (event: MouseEvent) => {
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

                    if (intersects.length > 0) {
                        const clickedObject = intersects[0].object;
                        console.log(
                            "3D View: Object clicked",
                            clickedObject.name,
                            "Ctrl:",
                            event.ctrlKey
                        );

                        if (event.ctrlKey) {
                            setSelectedObjects((prev) => {
                                const exists = prev.find((obj) => obj.uuid === clickedObject.uuid);
                                return exists
                                    ? prev.filter((obj) => obj.uuid !== clickedObject.uuid)
                                    : [...prev, clickedObject];
                            });
                        } else {
                            setSelectedObjects([clickedObject]);
                        }
                    } else {
                        console.log("3D View: Clicked empty space - keeping current selection");
                    }
                };

                renderer.domElement.addEventListener("click", handleCanvasClick);

                viewerInstanceRef.current = {
                    scene,
                    camera,
                    renderer,
                    drivers: allDrivers,
                    USD,
                    fileGroups: loadedGroups,
                    controls: mouseControls,
                    clickHandler: handleCanvasClick,
                };

                const animate = () => {
                    requestAnimationFrame(animate);
                    allDrivers.forEach((d) => d?.Draw?.());
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
         */
        const cleanFilePath = (path: string): string | null => {
            let cleaned = path.trim();

            // Remove @ symbols
            cleaned = cleaned.replace(/@/g, "");

            // Remove leading/trailing non-path characters
            cleaned = cleaned.replace(/^[^a-zA-Z0-9._\/]+/, "");
            cleaned = cleaned.replace(/[^a-zA-Z0-9._\/]+$/, "");

            // Must have a filename (not just extension)
            const parts = cleaned.split("/");
            const fileName = parts[parts.length - 1];
            if (!fileName || fileName.startsWith(".")) {
                return null; // Invalid: no filename or just extension
            }

            // Must contain at least one path separator or be a simple filename
            if (cleaned.includes("/") || cleaned.includes(".")) {
                return cleaned;
            }

            return null;
        };

        /**
         * Resolve relative path based on source file location
         */
        const resolveRelativePath = (referencePath: string, sourceFilePath: string): string => {
            // Remove @ symbols if present
            const cleanPath = referencePath.replace(/@/g, "").trim();

            // Get directory of source file
            const lastSlash = sourceFilePath.lastIndexOf("/");
            const sourceDir = lastSlash >= 0 ? sourceFilePath.substring(0, lastSlash) : "";

            // Handle different path types
            if (cleanPath.startsWith("./")) {
                // Relative to current directory
                return sourceDir
                    ? `${sourceDir}/${cleanPath.substring(2)}`
                    : cleanPath.substring(2);
            } else if (cleanPath.startsWith("../")) {
                // Parent directory reference
                const parts = sourceDir.split("/").filter((p) => p);
                const refParts = cleanPath.split("/").filter((p) => p);

                // Remove parent references and corresponding source parts
                while (refParts.length > 0 && refParts[0] === "..") {
                    refParts.shift();
                    if (parts.length > 0) parts.pop();
                }

                return [...parts, ...refParts].join("/");
            } else if (cleanPath.startsWith("/")) {
                // Absolute path from root
                return cleanPath.substring(1);
            } else {
                // Relative path without ./
                return sourceDir ? `${sourceDir}/${cleanPath}` : cleanPath;
            }
        };

        /**
         * Extract dependencies from text-based USD file
         */
        const extractFromText = (
            text: string,
            sourceFilePath: string,
            dependencies: Set<string>
        ): void => {
            // Pattern 1: @path@ asset references
            const assetPattern = /@([^@\s]+(?:\.[a-zA-Z0-9]+)?)@/g;
            let match;
            while ((match = assetPattern.exec(text)) !== null) {
                const refPath = match[1];
                if (refPath && !refPath.startsWith("http") && !refPath.startsWith("//")) {
                    const resolved = resolveRelativePath(refPath, sourceFilePath);
                    dependencies.add(resolved);
                }
            }

            // Pattern 2: Sublayers
            const sublayerPattern = /sublayers\s*=\s*\[([^\]]*)\]/g;
            const sublayerMatch = sublayerPattern.exec(text);
            if (sublayerMatch) {
                const sublayerContent = sublayerMatch[1];
                const sublayerRefs = sublayerContent.match(/@([^@]+)@/g);
                if (sublayerRefs) {
                    sublayerRefs.forEach((ref) => {
                        const path = ref.replace(/@/g, "").trim();
                        if (path) {
                            const resolved = resolveRelativePath(path, sourceFilePath);
                            dependencies.add(resolved);
                        }
                    });
                }
            }

            // Pattern 3: Payload/References
            const payloadPattern = /(?:payload|references)\s*=\s*@([^@]+)@/g;
            while ((match = payloadPattern.exec(text)) !== null) {
                const refPath = match[1].trim();
                if (refPath) {
                    const resolved = resolveRelativePath(refPath, sourceFilePath);
                    dependencies.add(resolved);
                }
            }
        };

        /**
         * Extract dependencies from binary USD file using heuristic byte search
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
            ];

            // Search for ASCII strings in binary data
            const strings = extractASCIIStrings(bytes, 4);

            for (const str of strings) {
                const lowerStr = str.toLowerCase();
                const hasExtension = extensions.some((ext) => lowerStr.endsWith(ext));

                if (hasExtension && !str.startsWith("http")) {
                    const cleanPath = cleanFilePath(str);
                    if (cleanPath) {
                        const resolved = resolveRelativePath(cleanPath, sourceFilePath);
                        dependencies.add(resolved);
                    }
                }
            }
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

            // Create directory structure
            USD.FS_createPath("", dirPath, true, true);

            // Store file
            USD.FS_createDataFile(dirPath, fileName, new Uint8Array(content), true, true, true);

            const fullPath = `${dirPath}/${fileName}`;
            console.log(`    Stored in WASM: ${fullPath}`);
            return fullPath;
        };

        /**
         * Download a file from the streaming endpoint
         */
        const downloadFile = async (
            fileKey: string,
            authHeader: string,
            isMainFile: boolean = false
        ): Promise<ArrayBuffer | null> => {
            try {
                const pathSegments = fileKey.split("/");
                const encodedSegments = pathSegments.map((segment) => encodeURIComponent(segment));
                const encodedFileKey = encodedSegments.join("/");
                const assetUrl = `${config.api}database/${databaseId}/assets/${assetId}/download/stream/${encodedFileKey}`;

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

        /**
         * Recursively load a file and all its dependencies
         */
        const loadFileWithDependencies = async (
            fileKey: string,
            loadedFiles: Set<string>,
            pendingFiles: string[],
            authHeader: string,
            USD: any,
            baseDirectory: string,
            isMainFile: boolean = false
        ): Promise<void> => {
            // Check if loading was cancelled
            if (loadingCancelledRef.current) {
                console.log(`  Loading cancelled, skipping: ${fileKey}`);
                return;
            }

            // Skip if already loaded
            if (loadedFiles.has(fileKey)) {
                console.log(`  Skipping already loaded: ${fileKey}`);
                return;
            }

            // Download file
            const fileContent = await downloadFile(fileKey, authHeader, isMainFile);
            if (!fileContent) return; // Failed to download dependency

            // Check cancellation after async operation
            if (loadingCancelledRef.current) {
                console.log(`  Loading cancelled after download: ${fileKey}`);
                return;
            }

            // Store in WASM filesystem with base directory
            storeInWASM(fileKey, fileContent, USD, baseDirectory);
            loadedFiles.add(fileKey);

            console.log(`  Loaded: ${fileKey} (${fileContent.byteLength} bytes)`);

            // Extract dependencies if USD file
            if (isUSDFile(fileKey)) {
                const dependencies = extractDependencies(fileContent, fileKey);

                if (dependencies.length > 0) {
                    console.log(`  Found ${dependencies.length} dependencies in ${fileKey}`);

                    // Add to queue
                    for (const dep of dependencies) {
                        if (!loadedFiles.has(dep) && !pendingFiles.includes(dep)) {
                            pendingFiles.push(dep);
                            console.log(`    → Queued: ${dep}`);
                        }
                    }
                }
            }
        };
        // ============================================================================
        // MAIN LOADING FUNCTION WITH DEPENDENCY RESOLUTION
        // ============================================================================

        // NEW: Main loading function using streaming URLs with recursive dependency resolution
        // This implementation uses direct streaming URLs instead of the downloadAsset API,
        // eliminating the need for pre-signed URL fetching while maintaining the WASM filesystem
        // approach for handling USD file dependencies.
        const loadAssets = async () => {
            try {
                setLoadingMessage("Initializing 3D scene...");
                const THREE = (window as any).THREE;
                const ThreeRenderDelegateInterface = (window as any).ThreeRenderDelegateInterface;
                const getUsdModule = (globalThis as any)["NEEDLE:USD:GET"];

                if (!THREE || !ThreeRenderDelegateInterface || !getUsdModule) {
                    throw new Error("USD Viewer dependencies not loaded");
                }

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
                const USD = await getUsdModule({
                    locateFile: (path: string, prefix: string) =>
                        (prefix || "/viewers/needletools_usd_viewer/") + path,
                });

                const filesToLoad =
                    multiFileKeys && multiFileKeys.length > 0
                        ? multiFileKeys
                        : assetKey
                        ? [assetKey]
                        : [];
                if (filesToLoad.length === 0) throw new Error("No files specified");

                const loadedGroups: any[] = [];
                const errors: Array<{ file: string; error: string }> = [];
                const allDrivers: any[] = [];

                // Get authorization header for streaming endpoint
                const authHeader = await getDualAuthorizationHeader();

                // Track all loaded files to prevent duplicates
                const loadedFiles = new Set<string>();

                for (let i = 0; i < filesToLoad.length; i++) {
                    // Check if loading was cancelled
                    if (loadingCancelledRef.current) {
                        console.log("Loading cancelled by component unmount");
                        return;
                    }

                    const fileKey = filesToLoad[i];
                    const fileName = fileKey.split("/").pop() || `model_${i}.usd`;

                    console.log(
                        `\n=== Loading main file ${i + 1}/${filesToLoad.length}: ${fileKey} ===`
                    );
                    setLoadingMessage(
                        `Loading file ${i + 1}/${filesToLoad.length}: ${fileName}...`
                    );

                    try {
                        // Check cancellation before download
                        if (loadingCancelledRef.current) {
                            console.log("Loading cancelled");
                            return;
                        }

                        // Download main file
                        const arrayBuffer = await downloadFile(fileKey, authHeader, true);
                        if (!arrayBuffer) throw new Error("Failed to download file");

                        // Store main file in WASM filesystem
                        const directory = `/file_${i}`;
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

                        // NEW: Recursively load dependencies
                        if (isUSDFile(fileKey)) {
                            setLoadingMessage(`Resolving dependencies for ${fileName}...`);

                            const dependencies = extractDependencies(arrayBuffer, fileKey);
                            const pendingFiles: string[] = [...dependencies];

                            if (pendingFiles.length > 0) {
                                console.log(`Found ${pendingFiles.length} dependencies to load`);

                                // Process all dependencies recursively
                                let depCount = 0;
                                while (pendingFiles.length > 0) {
                                    // Check cancellation in loop
                                    if (loadingCancelledRef.current) {
                                        console.log("Dependency loading cancelled");
                                        return;
                                    }

                                    const depPath = pendingFiles.shift()!;
                                    depCount++;
                                    setLoadingMessage(
                                        `Loading dependency ${depCount} for ${fileName}...`
                                    );

                                    await loadFileWithDependencies(
                                        depPath,
                                        loadedFiles,
                                        pendingFiles,
                                        authHeader,
                                        USD,
                                        directory, // Pass base directory for dependencies
                                        false
                                    );
                                }

                                console.log(`Loaded ${depCount} dependencies for ${fileKey}`);
                            } else {
                                console.log(`No dependencies found for ${fileKey}`);
                            }
                        }

                        // Create file group and load USD
                        setLoadingMessage(`Rendering ${fileName}...`);
                        const fileGroup = new THREE.Group();
                        fileGroup.name = fileName;
                        fileGroup.userData.sourceFile = fileKey;
                        scene.add(fileGroup);

                        const delegateConfig = {
                            usdRoot: fileGroup,
                            paths: [],
                            driver: () => driver,
                        };
                        const renderInterface = new ThreeRenderDelegateInterface(delegateConfig);
                        let driver = new USD.HdWebSyncDriver(
                            renderInterface,
                            directory + "/" + fileName
                        );
                        if (driver instanceof Promise) driver = await driver;

                        driver.Draw();
                        let stage = driver.GetStage();
                        if (stage instanceof Promise) {
                            stage = await stage;
                            stage = driver.GetStage();
                        }

                        // Store animation timing information
                        if (stage.GetEndTimeCode) {
                            const endTime = stage.GetEndTimeCode();
                            if (endTime > endTimeCodeRef.current) {
                                endTimeCodeRef.current = endTime;
                            }
                        }
                        if (stage.GetTimeCodesPerSecond) {
                            timeoutRef.current = 1000 / stage.GetTimeCodesPerSecond();
                        }

                        console.log(
                            `Animation timing for ${fileName}: endTimeCode=${
                                endTimeCodeRef.current
                            }, fps=${(1000 / timeoutRef.current).toFixed(1)}`
                        );

                        if (stage.GetUpAxis && String.fromCharCode(stage.GetUpAxis()) === "z") {
                            fileGroup.rotation.x = -Math.PI / 2;
                        }

                        loadedGroups.push(fileGroup);
                        allDrivers.push(driver);

                        console.log(`Successfully loaded ${fileKey} with all dependencies`);
                    } catch (fileError: any) {
                        console.error(`Error loading ${fileKey}:`, fileError);
                        errors.push({
                            file: fileKey,
                            error: fileError?.message || "Unknown error",
                        });
                    }
                }

                console.log(`\n=== Total files loaded: ${loadedFiles.size} ===`);

                if (errors.length > 0) setFileErrors(errors);
                if (loadedGroups.length === 0) throw new Error("No files loaded successfully");

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
                    drivers: allDrivers,
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
                    if (!animationPausedRef.current) {
                        allDrivers.forEach((d) => {
                            if (d?.SetTime) {
                                d.SetTime(time);
                            }
                        });
                    }

                    // Always draw and render to keep scene visible
                    allDrivers.forEach((d) => d?.Draw?.());
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
    }, [usdViewerReady, assetKey, multiFileKeys, assetId, databaseId, config]);

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
            <div style={{ position: "relative", height: "100%" }} id="usd-viewer-root">
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
