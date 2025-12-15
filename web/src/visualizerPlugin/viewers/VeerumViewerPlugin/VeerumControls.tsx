/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useState } from "react";

interface VeerumControlsProps {
    viewerController: any;
    loadedModels: any[];
    initError: string | null;
    onClose?: () => void;
}

const VeerumControls: React.FC<VeerumControlsProps> = ({
    viewerController,
    loadedModels,
    initError,
    onClose,
}) => {
    // UI States
    const [advancedMode, setAdvancedMode] = useState(false);
    const [currentView, setCurrentView] = useState<string>("perspective");

    // Camera & Navigation States
    const [cameraType, setCameraTypeState] = useState<"perspective" | "orthographic">("perspective");
    const [controlsType, setControlsTypeState] = useState<"orbit" | "fly">("orbit");
    const [flySpeed, setFlySpeed] = useState<number>(1.0);

    // Visual States
    const [background, setBackgroundState] = useState<string>("gradient");
    const [edlEnabled, setEdlEnabled] = useState<boolean>(true);
    const [navCubeVisible, setNavCubeVisible] = useState<boolean>(true);
    const [boundingBoxVisible, setBoundingBoxVisible] = useState<boolean>(false);

    // Point Cloud States
    const [pointSize, setPointSize] = useState<number>(1);
    const [pointBudget, setPointBudget] = useState<number>(1000000);
    const [pointShape, setPointShape] = useState<string>("circle");
    const [highDefinition, setHighDefinition] = useState<boolean>(true);
    const [gradient, setGradient] = useState<string>("NONE");

    // Visual Adjustment States
    const [brightness, setBrightness] = useState<number>(0);
    const [contrast, setContrast] = useState<number>(0);
    const [gamma, setGamma] = useState<number>(1);
    const [opacity, setOpacity] = useState<number>(1);

    // Model Visibility States
    const [modelVisibility, setModelVisibility] = useState<Record<number, boolean>>({});
    const [isFrozen, setIsFrozen] = useState<boolean>(false);

    // Initialize model visibility
    useEffect(() => {
        const initialVisibility: Record<number, boolean> = {};
        loadedModels.forEach((_, index) => {
            initialVisibility[index] = true;
        });
        setModelVisibility(initialVisibility);
    }, [loadedModels]);

    // Camera Control Functions
    const setCameraView = useCallback(
        async (view: string) => {
            if (!viewerController) return;

            try {
                setCurrentView(view);
                
                // For top view, use the dedicated method
                if (view === "top") {
                    await viewerController.fitCameraToSceneFromTop();
                } else {
                    // For other views, use positionAndRotateCamera with calculated positions
                    // Get the scene to calculate appropriate camera positions
                    const scene = viewerController.getScene();
                    if (scene && loadedModels.length > 0) {
                        const model = loadedModels[0];
                        const boundingSphere = model.boundingSphere || model.worldBoundingSphere;
                        
                        if (boundingSphere) {
                            const center = boundingSphere.center;
                            const radius = boundingSphere.radius;
                            const distance = radius * 2.5;
                            
                            // Calculate camera position based on view
                            let cameraPos;
                            switch (view) {
                                case "front":
                                    cameraPos = center.clone().add({ x: 0, y: 0, z: distance });
                                    break;
                                case "side":
                                    cameraPos = center.clone().add({ x: distance, y: 0, z: 0 });
                                    break;
                                case "isometric":
                                    cameraPos = center.clone().add({ x: distance * 0.7, y: distance * 0.7, z: distance * 0.7 });
                                    break;
                                default:
                                    return;
                            }
                            
                            await viewerController.positionAndRotateCamera(cameraPos, center);
                        }
                    }
                }

                console.log(`Camera set to ${view} view`);
                
                // Reset highlight after animation
                setTimeout(() => setCurrentView("perspective"), 1000);
            } catch (error) {
                console.error(`Error setting camera view to ${view}:`, error);
            }
        },
        [viewerController, loadedModels]
    );

    const fitToScene = useCallback(async () => {
        if (!viewerController) return;
        try {
            await viewerController.fitCameraToSceneFromTop();
            console.log("Camera fitted to scene");
        } catch (error) {
            console.error("Error fitting camera to scene:", error);
        }
    }, [viewerController]);

    const zoomCamera = useCallback(
        async (steps: number) => {
            if (!viewerController) return;
            try {
                await viewerController.zoomCameraByStep(steps);
            } catch (error) {
                console.error("Error zooming camera:", error);
            }
        },
        [viewerController]
    );

    const toggleCameraType = useCallback(() => {
        if (!viewerController) return;
        try {
            const newType = cameraType === "perspective" ? "orthographic" : "perspective";
            viewerController.setCameraType(newType.toUpperCase());
            setCameraTypeState(newType);
            console.log(`Camera type set to ${newType}`);
        } catch (error) {
            console.error("Error toggling camera type:", error);
        }
    }, [viewerController, cameraType]);

    const toggleControlsType = useCallback(() => {
        if (!viewerController) return;
        try {
            const newType = controlsType === "orbit" ? "fly" : "orbit";
            viewerController.setControls(newType.toUpperCase());
            setControlsTypeState(newType);
            console.log(`Controls type set to ${newType}`);
        } catch (error) {
            console.error("Error toggling controls type:", error);
        }
    }, [viewerController, controlsType]);

    const updateFlySpeed = useCallback(
        (speed: number) => {
            if (!viewerController) return;
            try {
                setFlySpeed(speed);
                viewerController.setFlyMoveSpeed(speed);
                console.log(`Fly speed set to ${speed}`);
            } catch (error) {
                console.error("Error setting fly speed:", error);
            }
        },
        [viewerController]
    );

    // Visual Control Functions
    const changeBackground = useCallback(
        (bg: string) => {
            if (!viewerController) return;
            try {
                setBackgroundState(bg);
                viewerController.setBackground(bg);
                console.log(`Background set to ${bg}`);
            } catch (error) {
                console.error("Error changing background:", error);
            }
        },
        [viewerController]
    );

    const toggleEDL = useCallback(() => {
        if (!viewerController) return;
        try {
            const newState = !edlEnabled;
            setEdlEnabled(newState);
            viewerController.setEDL(newState);
            console.log(`EDL ${newState ? "enabled" : "disabled"}`);
        } catch (error) {
            console.error("Error toggling EDL:", error);
        }
    }, [viewerController, edlEnabled]);

    const toggleNavCube = useCallback(() => {
        if (!viewerController) return;
        try {
            const newState = !navCubeVisible;
            setNavCubeVisible(newState);
            viewerController.setNavCubeVisibility(newState);
            console.log(`Navigation cube ${newState ? "shown" : "hidden"}`);
        } catch (error) {
            console.error("Error toggling nav cube:", error);
        }
    }, [viewerController, navCubeVisible]);

    const toggleBoundingBox = useCallback(() => {
        if (!viewerController) return;
        try {
            const newState = !boundingBoxVisible;
            setBoundingBoxVisible(newState);
            viewerController.setModelVisuals({
                boundingBoxVisibility: newState,
            });
            console.log(`Bounding boxes ${newState ? "shown" : "hidden"}`);
        } catch (error) {
            console.error("Error toggling bounding boxes:", error);
        }
    }, [viewerController, boundingBoxVisible]);

    // Point Cloud Control Functions
    const updatePointSize = useCallback(
        (size: number) => {
            if (!viewerController) return;
            try {
                setPointSize(size);
                viewerController.setModelVisuals({
                    pointCloudOptions: {
                        pointSize: size,
                    },
                });
            } catch (error) {
                console.error("Error setting point size:", error);
            }
        },
        [viewerController]
    );

    const updatePointBudget = useCallback(
        (budget: number) => {
            if (!viewerController) return;
            try {
                setPointBudget(budget);
                viewerController.setModelVisuals({
                    pointCloudOptions: {
                        pointBudget: budget,
                    },
                });
            } catch (error) {
                console.error("Error setting point budget:", error);
            }
        },
        [viewerController]
    );

    const togglePointShape = useCallback(() => {
        if (!viewerController) return;
        try {
            const newShape = pointShape === "circle" ? "square" : "circle";
            setPointShape(newShape);
            
            // Apply the point shape change
            // Note: The shape enum values should be "CIRCLE" or "SQUARE"
            viewerController.setModelVisuals({
                pointCloudOptions: {
                    pointShape: newShape === "circle" ? "CIRCLE" : "SQUARE",
                },
            });
            
            console.log(`Point shape set to ${newShape}`);
        } catch (error) {
            console.error("Error toggling point shape:", error);
        }
    }, [viewerController, pointShape]);

    const toggleHighDefinition = useCallback(() => {
        if (!viewerController) return;
        try {
            const newState = !highDefinition;
            setHighDefinition(newState);
            viewerController.setModelVisuals({
                pointCloudOptions: {
                    highDefinition: newState,
                },
            });
        } catch (error) {
            console.error("Error toggling high definition:", error);
        }
    }, [viewerController, highDefinition]);

    const changeGradient = useCallback(
        (grad: string) => {
            if (!viewerController) return;
            try {
                setGradient(grad);
                viewerController.setModelVisuals({
                    gradient: grad,
                });
            } catch (error) {
                console.error("Error changing gradient:", error);
            }
        },
        [viewerController]
    );

    // Visual Adjustment Functions
    const updateBrightness = useCallback(
        (value: number) => {
            if (!viewerController) return;
            try {
                setBrightness(value);
                viewerController.setModelVisuals({ brightness: value });
            } catch (error) {
                console.error("Error setting brightness:", error);
            }
        },
        [viewerController]
    );

    const updateContrast = useCallback(
        (value: number) => {
            if (!viewerController) return;
            try {
                setContrast(value);
                viewerController.setModelVisuals({ contrast: value });
            } catch (error) {
                console.error("Error setting contrast:", error);
            }
        },
        [viewerController]
    );

    const updateGamma = useCallback(
        (value: number) => {
            if (!viewerController) return;
            try {
                setGamma(value);
                viewerController.setModelVisuals({ gamma: value });
            } catch (error) {
                console.error("Error setting gamma:", error);
            }
        },
        [viewerController]
    );

    const updateOpacity = useCallback(
        (value: number) => {
            if (!viewerController) return;
            try {
                setOpacity(value);
                viewerController.setModelVisuals({ opacity: value });
            } catch (error) {
                console.error("Error setting opacity:", error);
            }
        },
        [viewerController]
    );

    // Action Functions
    const resetScene = useCallback(() => {
        if (!viewerController) return;
        try {
            // Reset all visual settings to defaults
            setBackgroundState("gradient");
            setEdlEnabled(true);
            setNavCubeVisible(true);
            setBoundingBoxVisible(false);
            setPointSize(1);  // Default point size is 1
            setPointBudget(1000000);
            setPointShape("circle");
            setHighDefinition(true);
            setGradient("NONE");
            setBrightness(0);
            setContrast(0);
            setGamma(1);
            setOpacity(1);
            setCameraTypeState("perspective");
            setControlsTypeState("orbit");
            setIsFrozen(false);

            // Apply to viewer
            viewerController.setBackground("gradient");
            viewerController.setEDL(true);
            viewerController.setNavCubeVisibility(true);
            viewerController.setCameraType("PERSPECTIVE");
            viewerController.setControls("ORBIT");
            
            viewerController.setModelVisuals({
                boundingBoxVisibility: false,
                brightness: 0,
                contrast: 0,
                gamma: 1,
                opacity: 1,
                gradient: "NONE",
                pointCloudOptions: {
                    pointSize: 1,  // Default point size is 1
                    pointBudget: 1000000,
                    pointShape: "CIRCLE",
                    highDefinition: true,
                },
            });

            // Ensure all models are visible
            loadedModels.forEach((model) => {
                model.visible = true;
            });

            // Reset model visibility state
            const resetVisibility: Record<number, boolean> = {};
            loadedModels.forEach((_, index) => {
                resetVisibility[index] = true;
            });
            setModelVisibility(resetVisibility);

            // Unfreeze if frozen
            if (isFrozen) {
                viewerController.unfreeze();
            }

            // Fit camera to scene
            viewerController.fitCameraToSceneFromTop();

            console.log("Scene reset to defaults");
        } catch (error) {
            console.error("Error resetting scene:", error);
        }
    }, [viewerController, loadedModels, isFrozen]);

    const toggleFreeze = useCallback(() => {
        if (!viewerController) return;
        try {
            const newState = !isFrozen;
            setIsFrozen(newState);
            
            if (newState) {
                viewerController.freeze();
            } else {
                viewerController.unfreeze();
            }
        } catch (error) {
            console.error("Error toggling freeze:", error);
        }
    }, [viewerController, isFrozen]);

    const toggleModelVisibility = useCallback(
        (index: number) => {
            if (index >= loadedModels.length) return;
            
            try {
                const model = loadedModels[index];
                const newVisibility = !modelVisibility[index];
                
                model.visible = newVisibility;
                setModelVisibility((prev) => ({
                    ...prev,
                    [index]: newVisibility,
                }));
            } catch (error) {
                console.error(`Error toggling model ${index} visibility:`, error);
            }
        },
        [loadedModels, modelVisibility]
    );

    const cycleBackground = useCallback(() => {
        const backgrounds = ["black", "white", "gradient", "skybox", "none"];
        const currentIndex = backgrounds.indexOf(background);
        const nextIndex = (currentIndex + 1) % backgrounds.length;
        changeBackground(backgrounds[nextIndex]);
    }, [background]);

    // Keyboard Shortcuts
    useEffect(() => {
        const handleKeyPress = (event: KeyboardEvent) => {
            if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
                return;
            }

            switch (event.key.toLowerCase()) {
                case 't':
                    setCameraView("top");
                    break;
                case 'f':
                    fitToScene();
                    break;
                case 'p':
                    toggleCameraType();
                    break;
                case 'b':
                    cycleBackground();
                    break;
                case 'e':
                    toggleEDL();
                    break;
                case 'a':
                    setAdvancedMode((prev) => !prev);
                    break;
                case 'escape':
                    if (onClose) onClose();
                    break;
            }
        };

        window.addEventListener('keydown', handleKeyPress);
        return () => window.removeEventListener('keydown', handleKeyPress);
    }, [setCameraView, fitToScene, toggleCameraType, cycleBackground, toggleEDL, onClose]);

    if (!viewerController || loadedModels.length === 0) {
        return null;
    }

    const gradientOptions = [
        "NONE", "RAINBOW", "INVERTED_RAINBOW", "GRAYSCALE", "INFERNO", 
        "PLASMA", "VIRIDIS", "TURBO", "SPECTRAL", "BLUES", 
        "CONTOUR", "BLUEPRINT", "SAFETY_VEST", "YELLOW_GREEN", "DEBUG"
    ];

    return (
        <>
            {/* Simple/Advanced Mode Toggle */}
            <div style={{ marginBottom: "16px" }}>
                <button
                    onClick={() => setAdvancedMode(!advancedMode)}
                    style={{
                        width: "100%",
                        background: advancedMode ? "#4CAF50" : "#2196F3",
                        border: "none",
                        color: "white",
                        padding: "8px 12px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.9em",
                        fontWeight: "bold",
                    }}
                    title="Toggle advanced controls (A)"
                >
                    {advancedMode ? "📊 Advanced Mode" : "⚙️ Simple Mode"}
                </button>
            </div>

            {/* Camera Views */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Camera Views
                </h5>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}>
                    {["top", "front", "side", "isometric"].map((view) => (
                        <button
                            key={view}
                            onClick={() => setCameraView(view)}
                            style={{
                                background:
                                    currentView === view
                                        ? "#4CAF50"
                                        : "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "6px 8px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                                textTransform: "capitalize",
                            }}
                            title={view === "top" ? "Top view (T)" : `${view} view`}
                        >
                            {view}
                        </button>
                    ))}
                </div>
            </div>

            {/* Quick Actions */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Quick Actions
                </h5>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    <button
                        onClick={fitToScene}
                        style={{
                            background: "#2196F3",
                            border: "none",
                            color: "white",
                            padding: "8px 12px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                        }}
                        title="Fit to scene (F)"
                    >
                        🎯 Fit to Scene
                    </button>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}>
                        <button
                            onClick={() => zoomCamera(1)}
                            style={{
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "6px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                            }}
                        >
                            🔍 Zoom In
                        </button>
                        <button
                            onClick={() => zoomCamera(-1)}
                            style={{
                                background: "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "6px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                            }}
                        >
                            🔍 Zoom Out
                        </button>
                    </div>
                </div>
            </div>

            {/* Background */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Background
                </h5>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
                    {["black", "white", "gradient"].map((bg) => (
                        <button
                            key={bg}
                            onClick={() => changeBackground(bg)}
                            style={{
                                background: background === bg ? "#4CAF50" : "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "6px 4px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.75em",
                                textTransform: "capitalize",
                            }}
                            title={bg === "gradient" ? "Gradient (B)" : bg}
                        >
                            {bg}
                        </button>
                    ))}
                </div>
            </div>

            {/* Visual Toggles */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Visual Options
                </h5>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                        <input
                            type="checkbox"
                            checked={edlEnabled}
                            onChange={toggleEDL}
                            style={{ marginRight: "8px" }}
                        />
                        Eye-Dome Lighting (E)
                    </label>
                    {advancedMode && (
                        <>
                            <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                                <input
                                    type="checkbox"
                                    checked={navCubeVisible}
                                    onChange={toggleNavCube}
                                    style={{ marginRight: "8px" }}
                                />
                                Navigation Cube
                            </label>
                            <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                                <input
                                    type="checkbox"
                                    checked={boundingBoxVisible}
                                    onChange={toggleBoundingBox}
                                    style={{ marginRight: "8px" }}
                                />
                                Bounding Boxes
                            </label>
                        </>
                    )}
                </div>
            </div>

            {/* Point Cloud Controls */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Point Cloud
                </h5>
                <div style={{ marginBottom: "8px" }}>
                    <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                        Point Size: {pointSize}
                    </label>
                    <input
                        type="range"
                        min="0.5"
                        max="10"
                        step="0.5"
                        value={pointSize}
                        onChange={(e) => updatePointSize(parseFloat(e.target.value))}
                        style={{ width: "100%" }}
                    />
                </div>
                {advancedMode && (
                    <>
                        <div style={{ marginBottom: "8px" }}>
                            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                                Point Budget: {(pointBudget / 1000000).toFixed(1)}M
                            </label>
                            <input
                                type="range"
                                min="100000"
                                max="2000000"
                                step="100000"
                                value={pointBudget}
                                onChange={(e) => updatePointBudget(parseInt(e.target.value))}
                                style={{ width: "100%" }}
                            />
                        </div>
                        <div style={{ marginBottom: "8px" }}>
                            <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                                <input
                                    type="checkbox"
                                    checked={pointShape === "square"}
                                    onChange={togglePointShape}
                                    style={{ marginRight: "8px" }}
                                />
                                Square Points
                            </label>
                        </div>
                        <div style={{ marginBottom: "8px" }}>
                            <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                                <input
                                    type="checkbox"
                                    checked={highDefinition}
                                    onChange={toggleHighDefinition}
                                    style={{ marginRight: "8px" }}
                                />
                                High Definition
                            </label>
                        </div>
                        <div style={{ marginBottom: "8px" }}>
                            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                                Color Gradient
                            </label>
                            <select
                                value={gradient}
                                onChange={(e) => changeGradient(e.target.value)}
                                style={{
                                    width: "100%",
                                    padding: "4px",
                                    borderRadius: "4px",
                                    backgroundColor: "rgba(255, 255, 255, 0.1)",
                                    color: "white",
                                    border: "1px solid rgba(255, 255, 255, 0.2)",
                                    fontSize: "0.8em",
                                }}
                            >
                                {gradientOptions.map((opt) => (
                                    <option key={opt} value={opt} style={{ backgroundColor: "#333" }}>
                                        {opt.replace(/_/g, " ")}
                                    </option>
                                ))}
                            </select>
                        </div>
                    </>
                )}
            </div>

            {/* Advanced Controls */}
            {advancedMode && (
                <>
                    {/* Camera & Navigation */}
                    <div style={{ marginBottom: "16px" }}>
                        <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                            Camera & Navigation
                        </h5>
                        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                            <button
                                onClick={toggleCameraType}
                                style={{
                                    background: "rgba(255, 255, 255, 0.1)",
                                    border: "1px solid rgba(255, 255, 255, 0.2)",
                                    color: "white",
                                    padding: "6px 8px",
                                    borderRadius: "4px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                }}
                                title="Toggle camera type (P)"
                            >
                                📷 {cameraType === "perspective" ? "Perspective" : "Orthographic"}
                            </button>
                            <button
                                onClick={toggleControlsType}
                                style={{
                                    background: "rgba(255, 255, 255, 0.1)",
                                    border: "1px solid rgba(255, 255, 255, 0.2)",
                                    color: "white",
                                    padding: "6px 8px",
                                    borderRadius: "4px",
                                    cursor: "pointer",
                                    fontSize: "0.8em",
                                }}
                            >
                                🎮 {controlsType === "orbit" ? "Orbit" : "Fly"} Controls
                            </button>
                            {controlsType === "fly" && (
                                <div>
                                    <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                                        Fly Speed: {flySpeed.toFixed(1)}
                                    </label>
                                    <input
                                        type="range"
                                        min="0.1"
                                        max="5"
                                        step="0.1"
                                        value={flySpeed}
                                        onChange={(e) => updateFlySpeed(parseFloat(e.target.value))}
                                        style={{ width: "100%" }}
                                    />
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Visual Adjustments */}
                    <div style={{ marginBottom: "16px" }}>
                        <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                            Visual Adjustments
                        </h5>
                        <div style={{ marginBottom: "8px" }}>
                            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                                Brightness: {brightness.toFixed(2)}
                            </label>
                            <input
                                type="range"
                                min="0"
                                max="2"
                                step="0.1"
                                value={brightness}
                                onChange={(e) => updateBrightness(parseFloat(e.target.value))}
                                style={{ width: "100%" }}
                            />
                        </div>
                        <div style={{ marginBottom: "8px" }}>
                            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                                Contrast: {contrast.toFixed(2)}
                            </label>
                            <input
                                type="range"
                                min="0"
                                max="2"
                                step="0.1"
                                value={contrast}
                                onChange={(e) => updateContrast(parseFloat(e.target.value))}
                                style={{ width: "100%" }}
                            />
                        </div>
                        <div style={{ marginBottom: "8px" }}>
                            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                                Gamma: {gamma.toFixed(2)}
                            </label>
                            <input
                                type="range"
                                min="0"
                                max="3"
                                step="0.1"
                                value={gamma}
                                onChange={(e) => updateGamma(parseFloat(e.target.value))}
                                style={{ width: "100%" }}
                            />
                        </div>
                        <div style={{ marginBottom: "8px" }}>
                            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8em" }}>
                                Opacity: {opacity.toFixed(2)}
                            </label>
                            <input
                                type="range"
                                min="0"
                                max="1"
                                step="0.05"
                                value={opacity}
                                onChange={(e) => updateOpacity(parseFloat(e.target.value))}
                                style={{ width: "100%" }}
                            />
                        </div>
                    </div>

                </>
            )}

            {/* Action Buttons */}
            <div style={{ marginBottom: "16px" }}>
                <h5 style={{ margin: "0 0 8px 0", fontSize: "0.9em", color: "#ccc" }}>
                    Actions
                </h5>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    <button
                        onClick={resetScene}
                        style={{
                            background: "#FF9800",
                            border: "none",
                            color: "white",
                            padding: "8px 12px",
                            borderRadius: "4px",
                            cursor: "pointer",
                            fontSize: "0.8em",
                        }}
                    >
                        🔄 Reset Scene
                    </button>
                    {advancedMode && (
                        <button
                            onClick={toggleFreeze}
                            style={{
                                background: isFrozen ? "#F44336" : "rgba(255, 255, 255, 0.1)",
                                border: "1px solid rgba(255, 255, 255, 0.2)",
                                color: "white",
                                padding: "8px 12px",
                                borderRadius: "4px",
                                cursor: "pointer",
                                fontSize: "0.8em",
                            }}
                        >
                            {isFrozen ? "▶️ Unfreeze" : "⏸️ Freeze"} LOD
                        </button>
                    )}
                </div>
            </div>

            {/* Keyboard Shortcuts Help */}
            <div style={{ fontSize: "0.75em", color: "#999", marginTop: "16px", paddingTop: "12px", borderTop: "1px solid rgba(255,255,255,0.1)" }}>
                <div style={{ fontWeight: "bold", marginBottom: "4px" }}>Keyboard Shortcuts:</div>
                <div>T: Top view | F: Fit scene</div>
                <div>P: Toggle camera | E: Toggle EDL</div>
                <div>B: Cycle background | A: Advanced</div>
                <div>Esc: Close panel</div>
            </div>
        </>
    );
};

export default VeerumControls;
