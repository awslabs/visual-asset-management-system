/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { createContext, useContext, useReducer, useCallback, ReactNode } from "react";
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
                    const viewer = state.viewerState.viewer.GetViewer
                        ? state.viewerState.viewer.GetViewer()
                        : state.viewerState.viewer;

                    if (selection.type === "Material" && selection.materialIndex !== undefined) {
                        // Update mesh highlighting for material selection
                        if (viewer.SetMeshesHighlight) {
                            const highlightColor = { r: 142, g: 201, b: 240 }; // Light blue highlight
                            viewer.SetMeshesHighlight(highlightColor, (meshUserData: any) => {
                                return (
                                    meshUserData.originalMaterials?.indexOf(
                                        selection.materialIndex
                                    ) !== -1
                                );
                            });
                            viewer.Render();
                        }
                    } else if (selection.type === "Mesh" && selection.meshInstanceId) {
                        // Update mesh highlighting for mesh selection
                        if (viewer.SetMeshesHighlight) {
                            const highlightColor = { r: 142, g: 201, b: 240 }; // Light blue highlight
                            viewer.SetMeshesHighlight(highlightColor, (meshUserData: any) => {
                                // Simple string matching for mesh ID
                                const meshIdStr = selection.meshInstanceId?.toString() || "";
                                const meshUserDataIdStr =
                                    meshUserData.originalMeshInstance?.id?.toString() || "";
                                return (
                                    meshIdStr === meshUserDataIdStr ||
                                    meshUserDataIdStr.includes(meshIdStr)
                                );
                            });
                            viewer.Render();
                        }
                    } else {
                        // Clear highlighting
                        if (viewer.SetMeshesHighlight) {
                            viewer.SetMeshesHighlight(null, () => false);
                            viewer.Render();
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
