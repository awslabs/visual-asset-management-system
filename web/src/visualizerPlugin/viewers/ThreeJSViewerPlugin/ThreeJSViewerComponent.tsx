/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { Cache } from "aws-amplify";
import { ViewerPluginProps } from "../../core/types";
import { ThreeJSDependencyManager } from "./dependencies";
import { getDualAuthorizationHeader } from "../../../utils/authTokenUtils";
import { MouseControls } from "./MouseControls";
import ThreeJSPanel from "./ThreeJSPanel";
import LoadingSpinner from "../../components/LoadingSpinner";
import { MaterialLibraryItem } from "./ThreeJSMaterialLibrary";
import { loadFile } from "./utils/fileLoaders";
import { preloadGLTFDependencies, cleanupBlobUrls } from "./utils/gltfDependencyLoader";

const ThreeJSViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    multiFileKeys,
    versionId,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [config] = useState(Cache.getItem("config"));
    const [threeReady, setThreeReady] = useState(false);
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

    // 3D selection toggle
    const [enable3DSelection, setEnable3DSelection] = useState(true);
    const enable3DSelectionRef = useRef(true);

    // Animation state
    const [animations, setAnimations] = useState<any[]>([]);
    const [animationPaused, setAnimationPaused] = useState(true);
    const animationMixerRef = useRef<any>(null);
    const animationClockRef = useRef<any>(null);
    const animationPausedRef = useRef(true);

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
                originalMaterial: originalMat,
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
                    // Create highlight material
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
        const initializeThreeJS = async () => {
            try {
                setLoadingMessage("Loading ThreeJS dependencies...");
                await ThreeJSDependencyManager.loadThreeJS();
                setThreeReady(true);
            } catch (error) {
                console.error("Failed to initialize ThreeJS:", error);
                setError("Failed to load ThreeJS dependencies");
                setIsLoading(false);
            }
        };

        if (!threeReady) {
            initializeThreeJS();
        }
    }, [threeReady]);

    useEffect(() => {
        const hasFiles = assetKey || (multiFileKeys && multiFileKeys.length > 0);
        if (
            !hasFiles ||
            initializationRef.current ||
            !threeReady ||
            !config ||
            !containerRef.current
        ) {
            return;
        }
        initializationRef.current = true;

        const loadAssets = async () => {
            try {
                setLoadingMessage("Initializing 3D scene...");
                const THREE = (window as any).THREE;

                if (!THREE) {
                    throw new Error("ThreeJS dependencies not loaded");
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

                const filesToLoad =
                    multiFileKeys && multiFileKeys.length > 0
                        ? multiFileKeys
                        : assetKey
                        ? [assetKey]
                        : [];
                if (filesToLoad.length === 0) throw new Error("No files specified");

                const loadedGroups: any[] = [];
                const errors: Array<{ file: string; error: string }> = [];
                const allAnimations: any[] = [];
                const allBlobUrls: string[] = [];
                const fileDependencyCounts = new Map<string, number>(); // Track dependency count per file

                // Get authorization header for streaming endpoint
                const authHeader = await getDualAuthorizationHeader();

                for (let i = 0; i < filesToLoad.length; i++) {
                    if (loadingCancelledRef.current) {
                        console.log("Loading cancelled by component unmount");
                        return;
                    }

                    const fileKey = filesToLoad[i];
                    const fileName = fileKey.split("/").pop() || `model_${i}`;
                    const fileExtension = fileName.split(".").pop()?.toLowerCase() || "";

                    console.log(
                        `\n=== Loading file ${i + 1}/${filesToLoad.length}: ${fileKey} ===`
                    );
                    setLoadingMessage(
                        `Loading file ${i + 1}/${filesToLoad.length}: ${fileName}...`
                    );

                    try {
                        if (loadingCancelledRef.current) {
                            console.log("Loading cancelled");
                            return;
                        }

                        // Download file using streaming endpoint
                        const pathSegments = fileKey.split("/");
                        const encodedSegments = pathSegments.map((segment) =>
                            encodeURIComponent(segment)
                        );
                        const encodedFileKey = encodedSegments.join("/");
                        let assetUrl = `${config.api}database/${databaseId}/assets/${assetId}/download/stream/${encodedFileKey}`;

                        // Add versionId query parameter for single file mode
                        const isSingleFile = filesToLoad.length === 1;
                        if (isSingleFile && versionId) {
                            assetUrl += `?versionId=${encodeURIComponent(versionId)}`;
                        }

                        const response = await fetch(assetUrl, {
                            headers: { Authorization: authHeader },
                        });

                        if (!response.ok) {
                            throw new Error(`Failed to load: ${fileName} (${response.status})`);
                        }

                        let arrayBuffer = await response.arrayBuffer();
                        console.log(`Downloaded: ${fileName} (${arrayBuffer.byteLength} bytes)`);

                        // For GLTF files, pre-load dependencies
                        // Note: Dependencies are loaded without versionId - they are part of the same asset version
                        if (fileExtension === "gltf") {
                            setLoadingMessage(`Resolving dependencies for ${fileName}...`);
                            const depResult = await preloadGLTFDependencies(
                                arrayBuffer,
                                {
                                    assetId,
                                    databaseId,
                                    baseFileKey: fileKey,
                                    apiEndpoint: config.api,
                                },
                                (current, total) => {
                                    setLoadingMessage(
                                        `Loading dependencies for ${fileName} (${current}/${total})...`
                                    );
                                }
                            );
                            arrayBuffer = depResult.gltfArrayBuffer;
                            allBlobUrls.push(...depResult.blobUrls);

                            // Track dependency count for this file
                            const depCount = depResult.blobUrls.length;
                            if (depCount > 0) {
                                fileDependencyCounts.set(fileName, depCount);
                                console.log(`Loaded ${depCount} dependencies for ${fileName}`);
                            }
                        }

                        // Load file using appropriate loader
                        setLoadingMessage(`Processing ${fileName}...`);
                        const result = await loadFile(arrayBuffer, fileExtension, fileName);

                        if (!result.object) {
                            throw new Error("Failed to parse file");
                        }

                        // Store animations if present
                        if (result.animations && result.animations.length > 0) {
                            console.log(
                                `Found ${result.animations.length} animations in ${fileName}`
                            );
                            allAnimations.push(...result.animations);
                        }

                        // Create file group
                        const fileGroup = new THREE.Group();
                        fileGroup.name = fileName;
                        fileGroup.userData.sourceFile = fileKey;

                        // Store dependency count if available
                        const depCount = fileDependencyCounts.get(fileName);
                        if (depCount !== undefined) {
                            fileGroup.userData.dependencyCount = depCount;
                        }

                        fileGroup.add(result.object);
                        scene.add(fileGroup);

                        loadedGroups.push(fileGroup);

                        console.log(`Successfully loaded ${fileName}`);
                    } catch (fileError: any) {
                        console.error(`Error loading ${fileKey}:`, fileError);
                        errors.push({
                            file: fileKey,
                            error: fileError?.message || "Unknown error",
                        });
                    }
                }

                // Store animations and create mixer if animations found
                if (allAnimations.length > 0) {
                    setAnimations(allAnimations);
                    const mixer = new THREE.AnimationMixer(scene);
                    const clock = new THREE.Clock();

                    // Play first animation by default (but paused)
                    const action = mixer.clipAction(allAnimations[0]);
                    action.play();

                    animationMixerRef.current = mixer;
                    animationClockRef.current = clock;

                    console.log(
                        `Animation system initialized with ${allAnimations.length} animations`
                    );
                }

                console.log(`\n=== Total files loaded: ${loadedGroups.length} ===`);

                if (errors.length > 0) setFileErrors(errors);

                // If no files loaded, show the actual error message
                if (loadedGroups.length === 0) {
                    // Check if any error is CAD/WASM-related (SharedArrayBuffer, OCCT, etc.)
                    const cadError = errors.find(
                        (err) =>
                            err.error.includes("CAD Format Support Not Available") ||
                            err.error.includes("CAD format support not enabled") ||
                            err.error.includes("OCCT library") ||
                            err.error.includes("SharedArrayBuffer")
                    );

                    if (cadError) {
                        // Show CAD/WASM error prominently
                        throw new Error(cadError.error);
                    } else if (errors.length > 0) {
                        // Show the first error if available
                        throw new Error(errors[0].error);
                    } else {
                        throw new Error("No files loaded successfully");
                    }
                }

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

                // Store original transforms
                console.log("Storing original transforms for all objects...");
                loadedGroups.forEach((group: any) => {
                    const topLevelUuids = new Set(group.children.map((child: any) => child.uuid));

                    group.traverse((obj: any) => {
                        if (
                            obj.position &&
                            obj.rotation &&
                            obj.scale &&
                            !originalTransformsRef.current.has(obj.uuid)
                        ) {
                            const isTopLevel = topLevelUuids.has(obj.uuid);

                            const worldPos = new THREE.Vector3();
                            obj.getWorldPosition(worldPos);

                            const worldQuat = new THREE.Quaternion();
                            obj.getWorldQuaternion(worldQuat);
                            const worldEuler = new THREE.Euler().setFromQuaternion(worldQuat);

                            const worldScale = new THREE.Vector3();
                            obj.getWorldScale(worldScale);

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
                                worldPosition: { x: worldPos.x, y: worldPos.y, z: worldPos.z },
                                worldRotation: {
                                    x: worldEuler.x,
                                    y: worldEuler.y,
                                    z: worldEuler.z,
                                },
                                worldScale: {
                                    x: worldScale.x,
                                    y: worldScale.y,
                                    z: worldScale.z,
                                },
                                isTopLevel: isTopLevel,
                            });
                        }
                    });
                });
                console.log(
                    `Stored original transforms for ${originalTransformsRef.current.size} objects`
                );

                const handleCanvasClick = (event: MouseEvent) => {
                    if (!enable3DSelectionRef.current) {
                        console.log("3D View: Selection disabled");
                        return;
                    }

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
                            setSelectedObjects((prev) => {
                                const isAlreadySelected =
                                    prev.length === 1 && prev[0].uuid === clickedObject.uuid;
                                return isAlreadySelected ? [] : [clickedObject];
                            });
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
                    fileGroups: loadedGroups,
                    controls: mouseControls,
                    clickHandler: handleCanvasClick,
                    blobUrls: allBlobUrls,
                };

                const animate = () => {
                    requestAnimationFrame(animate);

                    // Update animation mixer if animations are playing
                    if (
                        animationMixerRef.current &&
                        animationClockRef.current &&
                        !animationPausedRef.current
                    ) {
                        const delta = animationClockRef.current.getDelta();
                        animationMixerRef.current.update(delta);
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
                console.error("Error loading ThreeJS assets:", error);
                setError(error instanceof Error ? error.message : "Failed to load 3D files");
                setIsLoading(false);
            }
        };

        loadAssets();

        return () => {
            console.log("ThreeJS Viewer: Cleanup initiated");
            loadingCancelledRef.current = true;

            if (viewerInstanceRef.current) {
                const { renderer, controls, clickHandler, blobUrls } = viewerInstanceRef.current;

                if (clickHandler && renderer?.domElement) {
                    renderer.domElement.removeEventListener("click", clickHandler);
                }

                controls?.dispose();

                if (renderer?.domElement?.parentNode) {
                    renderer.domElement.parentNode.removeChild(renderer.domElement);
                }

                renderer?.dispose();

                // Cleanup blob URLs
                if (blobUrls && blobUrls.length > 0) {
                    cleanupBlobUrls(blobUrls);
                    console.log(`Cleaned up ${blobUrls.length} blob URLs`);
                }
            }

            // Clear global THREE.js reference to prevent contamination
            delete (window as any).THREE;
            delete (window as any).THREEBundle;
            console.log("ThreeJS Viewer: Cleared global THREE.js references");

            ThreeJSDependencyManager.cleanup();
            console.log("ThreeJS Viewer: Cleanup complete");
        };
    }, [threeReady, assetKey, multiFileKeys, assetId, databaseId, versionId, config]);

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

        if (viewerInstanceRef.current?.scene) {
            viewerInstanceRef.current.scene.traverse((obj: any) => {
                if (item.usedBy.has(obj.uuid)) {
                    const isSelected = selectedObjects.some((sel) => sel.uuid === obj.uuid);

                    if (isSelected) {
                        if (materialState.color)
                            obj.material.color = new THREE.Color(materialState.color);
                        obj.material.emissive = new THREE.Color(0x4caf50);
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

        if (original.color) mat.color = original.color.clone();
        if (original.emissive) mat.emissive = original.emissive.clone();
        if (original.metalness !== undefined) mat.metalness = original.metalness;
        if (original.roughness !== undefined) mat.roughness = original.roughness;
        mat.opacity = original.opacity;
        mat.transparent = original.transparent;
        mat.wireframe = original.wireframe;
        mat.needsUpdate = true;

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

        Array.from(materialLibrary.values()).forEach((libItem) => {
            if (libItem.usedBy.has(objectUuid)) {
                libItem.usedBy.delete(objectUuid);
            }
        });

        item.usedBy.add(objectUuid);

        const isSelected = selectedObjects.some((sel) => sel.uuid === objectUuid);
        if (isSelected) {
            const highlightMaterial = item.material.clone();
            highlightMaterial.emissive = new THREE.Color(0x4caf50);
            highlightMaterial.emissiveIntensity = 0.5;
            object.material = highlightMaterial;
        } else {
            object.material = item.material;
        }

        setMaterialLibrary(new Map(materialLibrary));

        console.log(`Assigned material ${item.name} to object ${object.name}`);
    };

    const handleMakeUnique = (objectUuid: string) => {
        const THREE = (window as any).THREE;
        if (!THREE || !viewerInstanceRef.current?.scene) return;

        const scene = viewerInstanceRef.current.scene;
        const object = scene.getObjectByProperty("uuid", objectUuid);
        if (!object || !object.material) return;

        const currentItem = Array.from(materialLibrary.values()).find((item) =>
            item.usedBy.has(objectUuid)
        );

        if (!currentItem) return;

        currentItem.usedBy.delete(objectUuid);

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

        setMaterialLibrary((prev) => new Map(prev).set(newMaterialId, newItem));

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

        Array.from(materialLibrary.values()).forEach((libItem) => {
            if (libItem.usedBy.has(objectUuid)) {
                libItem.usedBy.delete(objectUuid);
            }
        });

        setMaterialLibrary((prev) => new Map(prev).set(materialId, newItem));

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
                        const parent = obj.parent;
                        if (parent) {
                            const parentWorldMatrix = new THREE.Matrix4();
                            parent.updateMatrixWorld(true);
                            parentWorldMatrix.copy(parent.matrixWorld);
                            const parentInverse = new THREE.Matrix4()
                                .copy(parentWorldMatrix)
                                .invert();

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

                            const worldMatrix = new THREE.Matrix4();
                            worldMatrix.compose(
                                targetWorldPos,
                                new THREE.Quaternion().setFromEuler(targetWorldRot),
                                targetWorldScale
                            );

                            const localMatrix = new THREE.Matrix4();
                            localMatrix.multiplyMatrices(parentInverse, worldMatrix);

                            const localPos = new THREE.Vector3();
                            const localQuat = new THREE.Quaternion();
                            const localScale = new THREE.Vector3();
                            localMatrix.decompose(localPos, localQuat, localScale);
                            const localRot = new THREE.Euler().setFromQuaternion(localQuat);

                            obj.position.copy(localPos);
                            obj.rotation.copy(localRot);
                            obj.scale.copy(localScale);
                        } else {
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
        console.log("All object transforms reset to original");
    };

    const handleEditMaterial = (materialId: string) => {
        setSelectedMaterialId(materialId);
        console.log(`Edit material requested: ${materialId}`);
    };

    const handleResetAllMaterials = () => {
        const THREE = (window as any).THREE;
        if (!THREE || !viewerInstanceRef.current?.scene) return;

        Array.from(materialLibrary.values()).forEach((item) => {
            if (item.originalMaterial) {
                const original = item.originalMaterial;
                const mat = item.material;

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

        const scene = viewerInstanceRef.current.scene;
        scene.traverse((obj: any) => {
            if (obj.material) {
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
                id="threejs-viewer-root"
            >
                <div
                    style={{
                        color: "#d13212",
                        fontSize: "1.4em",
                        lineHeight: "1.5",
                        maxWidth: "800px",
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
                        Please ensure the file is a supported 3D format
                    </span>
                </div>
            </div>
        );
    }

    return (
        <div
            style={{ position: "relative", height: "100%", width: "100%" }}
            id="threejs-viewer-root"
        >
            <div
                ref={containerRef}
                style={{ width: "100%", height: "100%" }}
                id="threejs-viewer-container"
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
                <ThreeJSPanel
                    scene={viewerInstanceRef.current.scene}
                    camera={viewerInstanceRef.current.camera}
                    renderer={viewerInstanceRef.current.renderer}
                    threeRoot={viewerInstanceRef.current.fileGroups}
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
                    animations={animations}
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
                        top: "10px",
                        right: "10px",
                        color: "white",
                        fontSize: "12px",
                        backgroundColor: "rgba(0,0,0,0.7)",
                        padding: "8px",
                        borderRadius: "4px",
                        zIndex: 1000,
                    }}
                >
                    <div style={{ fontWeight: "bold", marginBottom: "4px" }}>ThreeJS Viewer</div>
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

export default ThreeJSViewerComponent;
