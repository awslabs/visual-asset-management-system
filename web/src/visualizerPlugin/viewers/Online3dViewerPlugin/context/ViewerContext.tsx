/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { createContext, useContext, useReducer, useCallback, useEffect, ReactNode } from "react";
import {
    ViewerContextType,
    ViewerState,
    ViewerSettings,
    CameraSettings,
    Selection,
    RGBColor,
    EdgeSettings,
} from "../types/viewer.types";

// Initial state
const initialViewerState: ViewerState = {
    viewer: null,
    model: null,
    isLoading: false,
    error: null,
    scriptsLoaded: false,
    viewerInitialized: false,
};

const initialViewerSettings: ViewerSettings = {
    themeId: "light",
    backgroundColor: { r: 245, g: 245, b: 245 },
    defaultColor: { r: 200, g: 200, b: 200 },
    defaultLineColor: { r: 0, g: 0, b: 0 },
    showEdges: false,
    edgeSettings: {
        showEdges: false,
        edgeColor: { r: 0, g: 0, b: 0 },
        edgeThreshold: 1,
    },
    environmentMapName: "fishermans_bastion",
    backgroundIsEnvMap: false,
};

const initialCameraSettings: CameraSettings = {
    navigationMode: "FixedUpVector",
    projectionMode: "Perspective",
};

const initialSelection: Selection = {
    type: null,
};

// Action types
type ViewerAction =
    | { type: "UPDATE_STATE"; payload: Partial<ViewerState> }
    | { type: "UPDATE_SETTINGS"; payload: Partial<ViewerSettings> }
    | { type: "UPDATE_CAMERA_SETTINGS"; payload: Partial<CameraSettings> }
    | { type: "SET_SELECTION"; payload: Selection }
    | { type: "RESET_STATE" };

// Combined state
interface CombinedState {
    viewerState: ViewerState;
    settings: ViewerSettings;
    cameraSettings: CameraSettings;
    selection: Selection;
}

const initialCombinedState: CombinedState = {
    viewerState: initialViewerState,
    settings: initialViewerSettings,
    cameraSettings: initialCameraSettings,
    selection: initialSelection,
};

// Reducer
function viewerReducer(state: CombinedState, action: ViewerAction): CombinedState {
    switch (action.type) {
        case "UPDATE_STATE":
            return {
                ...state,
                viewerState: { ...state.viewerState, ...action.payload },
            };
        case "UPDATE_SETTINGS":
            return {
                ...state,
                settings: { ...state.settings, ...action.payload },
            };
        case "UPDATE_CAMERA_SETTINGS":
            return {
                ...state,
                cameraSettings: { ...state.cameraSettings, ...action.payload },
            };
        case "SET_SELECTION":
            return {
                ...state,
                selection: action.payload,
            };
        case "RESET_STATE":
            return initialCombinedState;
        default:
            return state;
    }
}

// Context
const ViewerContext = createContext<ViewerContextType | undefined>(undefined);

// Provider component
interface ViewerProviderProps {
    children: ReactNode;
}

export const ViewerProvider: React.FC<ViewerProviderProps> = ({ children }) => {
    const [state, dispatch] = useReducer(viewerReducer, initialCombinedState);

    // Action creators
    const updateState = useCallback((updates: Partial<ViewerState>) => {
        dispatch({ type: "UPDATE_STATE", payload: updates });
    }, []);

    const updateSettings = useCallback(
        (updates: Partial<ViewerSettings>) => {
            dispatch({ type: "UPDATE_SETTINGS", payload: updates });
            // Save to localStorage
            try {
                const newSettings = { ...state.settings, ...updates };
                localStorage.setItem("ov_viewer_settings", JSON.stringify(newSettings));
            } catch (error) {
                console.warn("Failed to save settings to localStorage:", error);
            }
        },
        [state.settings]
    );

    // Enhanced selection handling with callbacks
    const setSelectionWithCallbacks = useCallback(
        (selection: Selection) => {
            dispatch({ type: "SET_SELECTION", payload: selection });

            // Trigger viewer updates based on selection
            if (state.viewerState.viewer && state.viewerState.model) {
                try {
                    // Get the underlying viewer - try multiple approaches
                    let viewer = null;
                    if (state.viewerState.viewer.GetViewer) {
                        viewer = state.viewerState.viewer.GetViewer();
                    } else if (state.viewerState.viewer.viewer) {
                        viewer = state.viewerState.viewer.viewer;
                    } else if (state.viewerState.viewer.embeddedViewer) {
                        viewer = state.viewerState.viewer.embeddedViewer;
                    } else {
                        viewer = state.viewerState.viewer;
                    }

                    if (selection.type === "Material" && selection.materialIndex !== undefined) {
                        // Update mesh highlighting for material selection
                        if (viewer && viewer.SetMeshesHighlight) {
                            const highlightColor = { r: 142, g: 201, b: 240 }; // Light blue highlight
                            viewer.SetMeshesHighlight(highlightColor, (meshUserData: any) => {
                                // Check if mesh uses this material
                                if (meshUserData.originalMaterials) {
                                    return meshUserData.originalMaterials.indexOf(selection.materialIndex) !== -1;
                                }
                                // Fallback - check material property
                                if (meshUserData.material !== undefined) {
                                    return meshUserData.material === selection.materialIndex;
                                }
                                return false;
                            });
                            viewer.Render();
                            console.log("Material selection highlighting applied:", selection.materialIndex);
                        }
                    } else if (selection.type === "Mesh" && selection.meshInstanceId) {
                        // Update mesh highlighting for mesh selection
                        if (viewer && viewer.SetMeshesHighlight) {
                            const highlightColor = { r: 142, g: 201, b: 240 }; // Light blue highlight
                            viewer.SetMeshesHighlight(highlightColor, (meshUserData: any) => {
                                // Try multiple ways to match mesh ID
                                const targetId = selection.meshInstanceId;
                                
                                // Direct ID comparison
                                if (meshUserData.originalMeshInstance?.id === targetId) {
                                    return true;
                                }
                                
                                // String comparison
                                const meshIdStr = targetId?.toString() || "";
                                const meshUserDataIdStr = meshUserData.originalMeshInstance?.id?.toString() || "";
                                if (meshIdStr && meshUserDataIdStr && meshIdStr === meshUserDataIdStr) {
                                    return true;
                                }
                                
                                // Check if mesh user data itself has the ID
                                if (meshUserData.id === targetId) {
                                    return true;
                                }
                                
                                return false;
                            });
                            viewer.Render();
                            console.log("Mesh selection highlighting applied:", selection.meshInstanceId);
                        }
                    } else {
                        // Clear highlighting
                        if (viewer && viewer.SetMeshesHighlight) {
                            viewer.SetMeshesHighlight(null, () => false);
                            viewer.Render();
                            console.log("Selection highlighting cleared");
                        }
                    }
                } catch (error) {
                    console.warn("Error updating viewer selection:", error);
                }
            }
        },
        [state.viewerState.viewer, state.viewerState.model]
    );

    const updateCameraSettings = useCallback(
        (updates: Partial<CameraSettings>) => {
            dispatch({ type: "UPDATE_CAMERA_SETTINGS", payload: updates });

            // Save to localStorage
            try {
                const newCameraSettings = { ...state.cameraSettings, ...updates };
                localStorage.setItem("ov_camera_settings", JSON.stringify(newCameraSettings));
            } catch (error) {
                console.warn("Failed to save camera settings to localStorage:", error);
            }
        },
        [state.cameraSettings]
    );

    const setSelection = useCallback((selection: Selection) => {
        dispatch({ type: "SET_SELECTION", payload: selection });
    }, []);

    // Handle selection highlighting in a separate effect
    useEffect(() => {
        if (state.viewerState.viewer && state.viewerState.model && state.selection.type) {
            try {
                // Get the underlying viewer
                let viewer = null;
                if (state.viewerState.viewer.GetViewer) {
                    viewer = state.viewerState.viewer.GetViewer();
                } else if (state.viewerState.viewer.viewer) {
                    viewer = state.viewerState.viewer.viewer;
                } else {
                    viewer = state.viewerState.viewer;
                }

                if (state.selection.type === "Material" && state.selection.materialIndex !== undefined) {
                    // Update mesh highlighting for material selection
                    if (viewer && (viewer as any).SetMeshesHighlight) {
                        const highlightColor = { r: 142, g: 201, b: 240 };
                        (viewer as any).SetMeshesHighlight(highlightColor, (meshUserData: any) => {
                            if (meshUserData.originalMaterials) {
                                return meshUserData.originalMaterials.indexOf(state.selection.materialIndex) !== -1;
                            }
                            if (meshUserData.material !== undefined) {
                                return meshUserData.material === state.selection.materialIndex;
                            }
                            return false;
                        });
                        (viewer as any).Render();
                        console.log("Material selection highlighting applied:", state.selection.materialIndex);
                    }
                } else if (state.selection.type === "Mesh" && state.selection.meshInstanceId) {
                    // Update mesh highlighting for mesh selection
                    if (viewer && (viewer as any).SetMeshesHighlight) {
                        const highlightColor = { r: 142, g: 201, b: 240 };
                        console.log("Attempting to highlight mesh with ID:", state.selection.meshInstanceId);
                        
                        let matchFound = false;
                        (viewer as any).SetMeshesHighlight(highlightColor, (meshUserData: any) => {
                            const targetId = state.selection.meshInstanceId;
                            
                            // Log for debugging (only first mesh)
                            if (!matchFound) {
                                console.log("Checking mesh:", {
                                    targetId,
                                    targetIdType: typeof targetId,
                                    meshUserData,
                                    originalMeshInstanceId: meshUserData.originalMeshInstance?.id,
                                    meshUserDataId: meshUserData.id
                                });
                            }
                            
                            // If targetId is a string like "mesh_0", extract the index and compare with mesh index
                            if (typeof targetId === 'string' && targetId.startsWith('mesh_')) {
                                const meshIndex = parseInt(targetId.replace('mesh_', ''));
                                
                                // Check if this mesh's index matches
                                if (meshUserData.originalMeshInstance?.id?.meshIndex === meshIndex) {
                                    matchFound = true;
                                    console.log("Match found via meshIndex comparison");
                                    return true;
                                }
                                
                                // Try comparing with the mesh index directly
                                if (meshUserData.meshIndex === meshIndex) {
                                    matchFound = true;
                                    console.log("Match found via meshUserData.meshIndex");
                                    return true;
                                }
                            }
                            
                            // If targetId is a MeshInstanceId object, use IsEqual method
                            if (targetId && typeof targetId === 'object' && (targetId as any).IsEqual) {
                                if (meshUserData.originalMeshInstance?.id && (targetId as any).IsEqual(meshUserData.originalMeshInstance.id)) {
                                    matchFound = true;
                                    console.log("Match found via MeshInstanceId.IsEqual");
                                    return true;
                                }
                            }
                            
                            // Direct object comparison
                            if (meshUserData.originalMeshInstance?.id === targetId) {
                                matchFound = true;
                                console.log("Match found via direct object comparison");
                                return true;
                            }
                            
                            return false;
                        });
                        (viewer as any).Render();
                        console.log("Mesh selection highlighting applied. Match found:", matchFound);
                    }
                }
            } catch (error) {
                console.warn("Error updating viewer selection:", error);
            }
        } else if (state.viewerState.viewer && !state.selection.type) {
            // Clear highlighting when selection is cleared
            try {
                let viewer = null;
                if (state.viewerState.viewer.GetViewer) {
                    viewer = state.viewerState.viewer.GetViewer();
                } else if (state.viewerState.viewer.viewer) {
                    viewer = state.viewerState.viewer.viewer;
                } else {
                    viewer = state.viewerState.viewer;
                }

                if (viewer && (viewer as any).SetMeshesHighlight) {
                    (viewer as any).SetMeshesHighlight(null, () => false);
                    (viewer as any).Render();
                    console.log("Selection highlighting cleared");
                }
            } catch (error) {
                console.warn("Error clearing viewer selection:", error);
            }
        }
    }, [state.selection, state.viewerState.viewer, state.viewerState.model]);

    // Model loading function
    const loadModel = useCallback(
        async (files: File[] | string[]) => {
            updateState({ isLoading: true, error: null });

            try {
                // The actual model loading will be handled by the viewer component
                // This is just a placeholder that updates the state
                console.log("Loading model files:", files);

                // Set a model placeholder to indicate something is loaded
                updateState({
                    isLoading: false,
                    model: { loaded: true, files: files },
                });
            } catch (error) {
                console.error("Error loading model:", error);
                updateState({
                    isLoading: false,
                    error: error instanceof Error ? error.message : "Failed to load model",
                });
            }
        },
        [updateState]
    );

    const clearModel = useCallback(() => {
        updateState({
            model: null,
            error: null,
        });
        setSelection({ type: null });
    }, [updateState, setSelection]);

    const fitModelToWindow = useCallback(() => {
        if (state.viewerState.viewer && state.viewerState.model) {
            // This would be implemented by the actual viewer
            console.log("Fitting model to window");
        }
    }, [state.viewerState.viewer, state.viewerState.model]);

    const switchTheme = useCallback(
        (themeId: "light" | "dark") => {
            const newSettings: Partial<ViewerSettings> = { themeId };

            // Update theme-specific colors
            if (themeId === "dark") {
                newSettings.backgroundColor = { r: 42, g: 42, b: 42 };
                newSettings.defaultColor = { r: 200, g: 200, b: 200 };
                newSettings.defaultLineColor = { r: 255, g: 255, b: 255 };
            } else {
                newSettings.backgroundColor = { r: 245, g: 245, b: 245 };
                newSettings.defaultColor = { r: 200, g: 200, b: 200 };
                newSettings.defaultLineColor = { r: 0, g: 0, b: 0 };
            }

            updateSettings(newSettings);
        },
        [updateSettings]
    );

    // Context value
    const contextValue: ViewerContextType = {
        state: state.viewerState,
        settings: state.settings,
        cameraSettings: state.cameraSettings,
        selection: state.selection,
        updateState,
        updateSettings,
        updateCameraSettings,
        setSelection,
        loadModel,
        clearModel,
        fitModelToWindow,
        switchTheme,
    };

    return <ViewerContext.Provider value={contextValue}>{children}</ViewerContext.Provider>;
};

// Hook to use the context
export const useViewerContext = (): ViewerContextType => {
    const context = useContext(ViewerContext);
    if (context === undefined) {
        throw new Error("useViewerContext must be used within a ViewerProvider");
    }
    return context;
};

// Load settings from localStorage on initialization
export const loadSettingsFromStorage = (): {
    settings: ViewerSettings;
    cameraSettings: CameraSettings;
} => {
    let settings = initialViewerSettings;
    let cameraSettings = initialCameraSettings;

    try {
        const savedSettings = localStorage.getItem("ov_viewer_settings");
        if (savedSettings) {
            settings = { ...settings, ...JSON.parse(savedSettings) };
        }

        const savedCameraSettings = localStorage.getItem("ov_camera_settings");
        if (savedCameraSettings) {
            cameraSettings = { ...cameraSettings, ...JSON.parse(savedCameraSettings) };
        }
    } catch (error) {
        console.warn("Failed to load settings from localStorage:", error);
    }

    return { settings, cameraSettings };
};
