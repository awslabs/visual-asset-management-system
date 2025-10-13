/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { downloadAsset } from "../../../services/APIService";
import { PlayCanvasGaussianSplatDependencyManager } from './dependencies';
import { PlayCanvasGaussianSplatViewerProps } from "./types/viewer.types";
import LoadingSpinner from "../../components/LoadingSpinner";

// Simplified camera controls using direct mouse/touch handling
const createCameraControls = (app: any, camera: any, pc: any) => {
    let isMouseDown = false;
    let isPanning = false;
    let lastMouseX = 0;
    let lastMouseY = 0;
    let cameraDistance = 10;
    let cameraYaw = 0;
    let cameraPitch = 0.3;
    const target = new pc.Vec3(0, 0, 0);
    const canvas = app.graphicsDevice.canvas;

    const updateCameraPosition = () => {
        const x = target.x + cameraDistance * Math.sin(cameraYaw) * Math.cos(cameraPitch);
        const y = target.y + cameraDistance * Math.sin(cameraPitch);
        const z = target.z + cameraDistance * Math.cos(cameraYaw) * Math.cos(cameraPitch);
        
        camera.setPosition(x, y, z);
        camera.lookAt(target.x, target.y, target.z);
    };

    // Mouse controls
    const onMouseDown = (e: MouseEvent) => {
        isMouseDown = true;
        isPanning = e.button === 2; // Right mouse button for panning
        lastMouseX = e.clientX;
        lastMouseY = e.clientY;
        e.preventDefault();
    };

    const onMouseMove = (e: MouseEvent) => {
        if (!isMouseDown) return;

        const deltaX = e.clientX - lastMouseX;
        const deltaY = e.clientY - lastMouseY;

        if (isPanning) {
            // Pan the target
            const panSpeed = cameraDistance * 0.001;
            const right = new pc.Vec3();
            const up = new pc.Vec3();
            
            // Calculate right and up vectors from camera
            const cameraTransform = camera.getWorldTransform();
            cameraTransform.getX(right);
            cameraTransform.getY(up);
            
            right.mulScalar(-deltaX * panSpeed);
            up.mulScalar(deltaY * panSpeed);
            
            target.add(right).add(up);
        } else {
            // Rotate around target
            const rotateSpeed = 0.005;
            cameraYaw += deltaX * rotateSpeed;  // Fixed: + instead of - to match BabylonJS behavior
            cameraPitch = Math.max(-Math.PI/2 + 0.1, Math.min(Math.PI/2 - 0.1, cameraPitch + deltaY * rotateSpeed));
        }

        updateCameraPosition();
        
        lastMouseX = e.clientX;
        lastMouseY = e.clientY;
        e.preventDefault();
    };

    const onMouseUp = () => {
        isMouseDown = false;
        isPanning = false;
    };

    // Fixed zoom with correct direction
    const onWheel = (e: WheelEvent) => {
        const zoomSpeed = 0.001;
        const zoomDelta = e.deltaY * zoomSpeed * cameraDistance;
        cameraDistance = Math.max(0.5, Math.min(100, cameraDistance + zoomDelta)); // Fixed: + instead of -
        updateCameraPosition();
        e.preventDefault();
        e.stopPropagation();
    };

    // Touch controls for mobile
    let lastTouchDistance = 0;
    let lastTouchX = 0;
    let lastTouchY = 0;

    const onTouchStart = (e: TouchEvent) => {
        if (e.touches.length === 1) {
            lastTouchX = e.touches[0].clientX;
            lastTouchY = e.touches[0].clientY;
        } else if (e.touches.length === 2) {
            const dx = e.touches[0].clientX - e.touches[1].clientX;
            const dy = e.touches[0].clientY - e.touches[1].clientY;
            lastTouchDistance = Math.sqrt(dx * dx + dy * dy);
        }
        e.preventDefault();
    };

    const onTouchMove = (e: TouchEvent) => {
        if (e.touches.length === 1) {
            // Single finger - rotate
            const deltaX = e.touches[0].clientX - lastTouchX;
            const deltaY = e.touches[0].clientY - lastTouchY;
            
            const rotateSpeed = 0.005;
            cameraYaw += deltaX * rotateSpeed;  // Fixed: + instead of - to match mouse controls
            cameraPitch = Math.max(-Math.PI/2 + 0.1, Math.min(Math.PI/2 - 0.1, cameraPitch + deltaY * rotateSpeed));
            
            updateCameraPosition();
            
            lastTouchX = e.touches[0].clientX;
            lastTouchY = e.touches[0].clientY;
        } else if (e.touches.length === 2) {
            // Two fingers - zoom
            const dx = e.touches[0].clientX - e.touches[1].clientX;
            const dy = e.touches[0].clientY - e.touches[1].clientY;
            const distance = Math.sqrt(dx * dx + dy * dy);
            
            if (lastTouchDistance > 0) {
                const zoomSpeed = 0.01;
                const deltaDistance = distance - lastTouchDistance;
                cameraDistance = Math.max(0.5, Math.min(100, cameraDistance - deltaDistance * zoomSpeed));
                updateCameraPosition();
            }
            
            lastTouchDistance = distance;
        }
        e.preventDefault();
    };

    // Attach event listeners
    canvas.addEventListener('mousedown', onMouseDown);
    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mouseup', onMouseUp);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    canvas.addEventListener('touchstart', onTouchStart, { passive: false });
    canvas.addEventListener('touchmove', onTouchMove, { passive: false });

    // Initialize camera position
    updateCameraPosition();

    return {
        destroy: () => {
            canvas.removeEventListener('mousedown', onMouseDown);
            canvas.removeEventListener('mousemove', onMouseMove);
            canvas.removeEventListener('mouseup', onMouseUp);
            canvas.removeEventListener('wheel', onWheel);
            canvas.removeEventListener('touchstart', onTouchStart);
            canvas.removeEventListener('touchmove', onTouchMove);
        }
    };
};

const PlayCanvasGaussianSplatViewerComponent: React.FC<PlayCanvasGaussianSplatViewerProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const initializationRef = useRef(false);
    const cameraControlsRef = useRef<{ destroy: () => void } | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [loadingMessage, setLoadingMessage] = useState("Initializing viewer...");

    useEffect(() => {
        if (!assetKey || initializationRef.current) return;
        initializationRef.current = true;

        const initViewer = async () => {
            try {
                console.log("PlayCanvas Gaussian Splat Viewer: Starting initialization");
                setLoadingMessage("Initializing viewer...");
                
                // Create canvas directly in DOM
                const canvas = document.createElement('canvas');
                canvas.style.width = '100%';
                canvas.style.height = '100%';
                canvas.style.display = 'block';
                canvas.style.backgroundColor = '#1a1a1a';
                canvas.style.touchAction = 'none';
                
                // Prevent context menu and wheel scrolling
                canvas.addEventListener('contextmenu', (e) => e.preventDefault());
                canvas.addEventListener('wheel', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                }, { passive: false });
                
                if (containerRef.current) {
                    containerRef.current.appendChild(canvas);
                    console.log("PlayCanvas Gaussian Splat Viewer: Canvas added to DOM");
                }

                // Load PlayCanvas engine
                setLoadingMessage("Loading PlayCanvas engine...");
                const pc = await PlayCanvasGaussianSplatDependencyManager.loadPlayCanvas();
                console.log("PlayCanvas Gaussian Splat Viewer: PlayCanvas engine loaded");

                // Create PlayCanvas application with high resolution settings
                const app = new pc.Application(canvas, {
                    mouse: new pc.Mouse(canvas),
                    touch: new pc.TouchDevice(canvas),
                    keyboard: new pc.Keyboard(window),
                    graphicsDeviceOptions: {
                        antialias: true,
                        alpha: false,
                        preserveDrawingBuffer: false,
                        preferWebGl2: true,
                        powerPreference: "high-performance"
                    }
                });

                // Configure application settings for high quality
                app.scene.clusteredLightingEnabled = false;
                app.autoRender = true;
                
                // Set high resolution
                const pixelRatio = window.devicePixelRatio || 1;
                app.graphicsDevice.maxPixelRatio = pixelRatio;
                app.setCanvasFillMode(pc.FILLMODE_FILL_WINDOW);
                app.setCanvasResolution(pc.RESOLUTION_AUTO);

                // Create camera
                const camera = new pc.Entity('Camera');
                camera.addComponent('camera', {
                    clearColor: new pc.Color(0.1, 0.1, 0.1, 1.0),
                    fov: 75,
                    nearClip: 0.1,
                    farClip: 1000
                });
                camera.setPosition(0, 2, 5);
                app.root.addChild(camera);

                // Add PlayCanvas native camera controls
                const cameraControls = createCameraControls(app, camera, pc);
                cameraControlsRef.current = cameraControls;

                // Create light
                const light = new pc.Entity('DirectionalLight');
                light.addComponent('light', {
                    type: 'directional',
                    color: new pc.Color(1, 1, 1),
                    intensity: 1,
                    castShadows: false
                });
                light.setEulerAngles(45, 30, 0);
                app.root.addChild(light);

                // Handle window resize
                const handleResize = () => {
                    if (canvas && containerRef.current) {
                        const rect = containerRef.current.getBoundingClientRect();
                        const pixelRatio = window.devicePixelRatio || 1;
                        
                        // Update canvas size
                        canvas.width = rect.width * pixelRatio;
                        canvas.height = rect.height * pixelRatio;
                        canvas.style.width = rect.width + 'px';
                        canvas.style.height = rect.height + 'px';
                        
                        // Update PlayCanvas graphics device
                        app.graphicsDevice.setResolution(canvas.width, canvas.height);
                        
                        console.log("PlayCanvas Gaussian Splat Viewer: Resized to", canvas.width, "x", canvas.height);
                    }
                };

                // Set up resize observer
                const resizeObserver = new ResizeObserver(handleResize);
                if (containerRef.current) {
                    resizeObserver.observe(containerRef.current);
                }

                // Initial resize
                handleResize();

                // Start the application
                app.start();
                console.log("PlayCanvas Gaussian Splat Viewer: Application started");

                // Download and load asset
                console.log("PlayCanvas Gaussian Splat Viewer: Downloading asset");
                setLoadingMessage("Downloading asset...");
                const response = await downloadAsset({
                    assetId,
                    databaseId,
                    key: assetKey,
                    versionId: versionId || "",
                    downloadType: "assetFile",
                });

                if (response && Array.isArray(response) && response[0] !== false) {
                    console.log("PlayCanvas Gaussian Splat Viewer: Asset URL retrieved, downloading asset...");
                    // Keep "Downloading asset..." message since the actual download happens in pc.Asset
                    
                    try {
                        const assetUrl = response[1]; // URL from downloadAsset
                        const filename = assetKey || assetId;
                        
                        // Create PlayCanvas asset
                        const asset = new pc.Asset(filename, 'gsplat', {
                            url: assetUrl,
                            filename: filename
                        });

                        // Load the asset
                        asset.ready(() => {
                            try {
                                console.log("PlayCanvas Gaussian Splat Viewer: Asset ready, creating entity");
                                
                                // Create entity with Gaussian Splat component
                                const entity = new pc.Entity('GaussianSplat');
                                
                                // Add GSplat component
                                entity.addComponent('gsplat', {
                                    asset: asset
                                });

                                // Set initial rotation for proper orientation
                                entity.setEulerAngles(0, 0, 0);

                                // Add to scene
                                app.root.addChild(entity);

                                // Set up automatic sorting updates
                                if (entity.gsplat && entity.gsplat.instance && entity.gsplat.instance.sorter) {
                                    entity.gsplat.instance.sorter.on('updated', () => {
                                        // console.log("PlayCanvas Gaussian Splat Viewer: Splat sorting updated");
                                    });
                                }

                                // Focus camera on the loaded splat
                                const distance = 10;
                                camera.setPosition(distance, distance * 0.5, distance);
                                camera.lookAt(0, 0, 0);

                                console.log("PlayCanvas Gaussian Splat Viewer: Gaussian Splat loaded successfully");
                                
                                // Hide loading indicator
                                setIsLoading(false);
                                
                            } catch (error) {
                                console.error("PlayCanvas Gaussian Splat Viewer: Error creating GSplat entity:", error);
                                setIsLoading(false); // Hide loading on error
                            }
                        });

                        asset.on('error', (err: string) => {
                            console.error("PlayCanvas Gaussian Splat Viewer: Asset loading error:", err);
                            setIsLoading(false); // Hide loading on error
                        });

                        app.assets.add(asset);
                        app.assets.load(asset);
                        
                    } catch (error) {
                        console.error("PlayCanvas Gaussian Splat Viewer: Error processing asset:", error);
                        setIsLoading(false); // Hide loading on error
                    }
                } else {
                    console.error("PlayCanvas Gaussian Splat Viewer: Failed to download asset");
                    setIsLoading(false); // Hide loading on error
                }

            } catch (error) {
                console.error("PlayCanvas Gaussian Splat Viewer: Initialization error:", error);
                setIsLoading(false); // Hide loading on error
            }
        };

        initViewer();

        // Cleanup function
        return () => {
            console.log("PlayCanvas Gaussian Splat Viewer: Cleaning up");
            
            // Clean up camera controls event listeners
            if (cameraControlsRef.current && cameraControlsRef.current.destroy) {
                cameraControlsRef.current.destroy();
            }
        };
    }, [assetKey, assetId, databaseId, versionId]);

    return (
        <div 
            ref={containerRef}
            style={{ 
                width: "100%", 
                height: "100%", 
                backgroundColor: "#1a1a1a",
                position: "relative"
            }}
            onWheel={(e) => {
                e.preventDefault();
                e.stopPropagation();
            }}
        >
            {/* Loading overlay */}
            {isLoading && (
                <LoadingSpinner message={loadingMessage} />
            )}
            
            {/* Info Panel */}
            <div style={{
                position: "absolute",
                top: "10px",
                right: "10px",
                color: "white",
                fontSize: "12px",
                backgroundColor: "rgba(0,0,0,0.7)",
                padding: "8px",
                borderRadius: "4px",
                zIndex: 1000
            }}>
                PlayCanvas Gaussian Splat Viewer
                <br />
                Mouse: Rotate | Wheel: Zoom | Right-click: Pan
            </div>
        </div>
    );
};

export default PlayCanvasGaussianSplatViewerComponent;
