/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ViewerPluginProps } from "../../../core/types";

// Re-export the base props
export interface Online3DViewerProps extends ViewerPluginProps {}

// Viewer state management
export interface ViewerState {
    viewer: any | null;
    model: any | null;
    isLoading: boolean;
    error: string | null;
    scriptsLoaded: boolean;
    viewerInitialized: boolean;
}

// Settings and configuration
export interface ViewerSettings {
    themeId: "light" | "dark";
    backgroundColor: RGBColor;
    defaultColor: RGBColor;
    defaultLineColor: RGBColor;
    showEdges: boolean;
    edgeSettings: EdgeSettings;
    environmentMapName: string;
    backgroundIsEnvMap: boolean;
}

export interface CameraSettings {
    navigationMode: "FixedUpVector" | "FreeOrbit";
    projectionMode: "Perspective" | "Orthographic";
}

export interface EdgeSettings {
    showEdges: boolean;
    edgeColor: RGBColor;
    edgeThreshold: number;
}

export interface RGBColor {
    r: number;
    g: number;
    b: number;
}

// Model and mesh types
export interface ModelInfo {
    name: string;
    triangleCount: number;
    vertexCount: number;
    boundingBox: BoundingBox;
    materials: MaterialInfo[];
    meshes: MeshInfo[];
}

export interface MaterialInfo {
    index: number;
    name: string;
    color: RGBColor;
    opacity: number;
    metallic?: number;
    roughness?: number;
    emissive?: RGBColor;
}

export interface MeshInfo {
    id: string;
    name: string;
    triangleCount: number;
    vertexCount: number;
    materialIndices: number[];
    visible: boolean;
    boundingBox: BoundingBox;
}

export interface BoundingBox {
    min: Coord3D;
    max: Coord3D;
}

export interface Coord3D {
    x: number;
    y: number;
    z: number;
}

// Selection and interaction
export interface Selection {
    type: "Material" | "Mesh" | null;
    materialIndex?: number;
    meshInstanceId?: string;
}

// Panel and UI types
export interface PanelProps {
    isVisible: boolean;
    onToggleVisibility: (visible: boolean) => void;
    onResize?: () => void;
}

export interface ToolbarButtonProps {
    icon: string;
    title: string;
    onClick: () => void;
    isSelected?: boolean;
    className?: string;
    disabled?: boolean;
}

export interface DialogProps {
    isOpen: boolean;
    onClose: () => void;
    title: string;
}

// File handling
export interface FileInfo {
    name: string;
    size: number;
    type: string;
    url?: string;
}

export interface ImportResult {
    success: boolean;
    model?: any;
    mainFile: string;
    missingFiles: string[];
    error?: string;
}

// Measurement tool
export interface MeasurementPoint {
    position: Coord3D;
    normal?: Coord3D;
}

export interface MeasurementResult {
    type: "distance" | "angle";
    value: number;
    unit: string;
    points: MeasurementPoint[];
}

// Export options
export interface ExportOptions {
    format: "obj" | "stl" | "ply" | "gltf" | "3dm";
    onlyVisible: boolean;
    precision?: number;
}

// Theme configuration
export interface ThemeConfig {
    id: "light" | "dark";
    colors: {
        background: string;
        foreground: string;
        border: string;
        toolbar: string;
        toolbarSelected: string;
        hover: string;
        button: string;
        panel: string;
    };
}

// Context types
export interface ViewerContextType {
    state: ViewerState;
    settings: ViewerSettings;
    cameraSettings: CameraSettings;
    selection: Selection;
    updateState: (updates: Partial<ViewerState>) => void;
    updateSettings: (updates: Partial<ViewerSettings>) => void;
    updateCameraSettings: (updates: Partial<CameraSettings>) => void;
    setSelection: (selection: Selection) => void;
    loadModel: (files: File[] | string[]) => Promise<void>;
    clearModel: () => void;
    fitModelToWindow: () => void;
    switchTheme: (themeId: "light" | "dark") => void;
}

// Hook return types
export interface UseViewerReturn {
    viewer: any | null;
    isInitialized: boolean;
    error: string | null;
    initializeViewer: (canvas: HTMLCanvasElement) => void;
    destroyViewer: () => void;
}

export interface UseModelReturn {
    model: any | null;
    isLoading: boolean;
    error: string | null;
    loadModel: (files: File[] | string[]) => Promise<void>;
    clearModel: () => void;
}

export interface UseSettingsReturn {
    settings: ViewerSettings;
    cameraSettings: CameraSettings;
    updateSettings: (updates: Partial<ViewerSettings>) => void;
    updateCameraSettings: (updates: Partial<CameraSettings>) => void;
    saveSettings: () => void;
    loadSettings: () => void;
}
