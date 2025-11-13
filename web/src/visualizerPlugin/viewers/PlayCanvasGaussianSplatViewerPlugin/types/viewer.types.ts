/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ViewerPluginProps } from "../../../core/types";

// NOTE: PlayCanvas types are loaded dynamically at runtime.
// Using 'any' types to avoid conflicts with existing PlayCanvas type definitions.
// The actual PlayCanvas API is available through dynamic imports in the component.

// ========================================
// PlayCanvas Gaussian Splat Viewer Types
// ========================================

// Re-export the base props
export interface PlayCanvasGaussianSplatViewerProps extends ViewerPluginProps {}

// PlayCanvas-specific types for Gaussian Splat viewing
export interface GaussianSplatOptions {
    keepInRam?: boolean;
    progressiveUpdateAmount?: number;
    quality?: "low" | "medium" | "high";
}

export interface ViewerState {
    loaded: boolean;
    loading: boolean;
    error: string | null;
    app?: any; // PlayCanvas Application type
    entity?: any; // PlayCanvas Entity type with gaussian splat
    camera?: any; // PlayCanvas Camera entity
    initialized: boolean;
}

export interface CameraState {
    position: { x: number; y: number; z: number };
    rotation: { x: number; y: number; z: number };
    target: { x: number; y: number; z: number };
    distance: number;
}

export interface SplatControls {
    rotate: () => void;
    reset: () => void;
    fitToScreen: () => void;
    zoom: (delta: number) => void;
    pan: (deltaX: number, deltaY: number) => void;
}

export type PlyType = "splat" | "pc";

// Settings and configuration specific to PlayCanvas Gaussian Splat viewing
export interface ViewerSettings {
    backgroundColor: { r: number; g: number; b: number };
    showGrid: boolean;
    autoRotate: boolean;
    rotationSpeed: number;
    enableControls: boolean;
    gaussianSplatOptions: GaussianSplatOptions;
    lighting: LightingSettings;
}

export interface LightingSettings {
    enableDirectionalLight: boolean;
    directionalLightIntensity: number;
    enableAmbientLight: boolean;
    ambientLightIntensity: number;
}

// File handling for Gaussian Splat formats
export interface SplatFileInfo {
    name: string;
    size: number;
    type: string;
    url?: string;
    plyType: PlyType;
}

export interface LoadResult {
    success: boolean;
    entity?: any; // PlayCanvas Entity with GSplat component
    asset?: any; // PlayCanvas Asset
    error?: string;
    fileInfo: SplatFileInfo;
}

// Input handling
export interface InputState {
    mouse: {
        x: number;
        y: number;
        deltaX: number;
        deltaY: number;
        buttons: number;
    };
    keyboard: {
        pressed: Set<string>;
    };
    touch: {
        touches: TouchState[];
        scale: number;
        deltaScale: number;
    };
}

export interface TouchState {
    id: number;
    x: number;
    y: number;
    deltaX: number;
    deltaY: number;
}

// Context types for PlayCanvas viewer
export interface PlayCanvasViewerContextType {
    state: ViewerState;
    settings: ViewerSettings;
    controls: SplatControls;
    cameraState: CameraState;
    updateState: (updates: Partial<ViewerState>) => void;
    updateSettings: (updates: Partial<ViewerSettings>) => void;
    updateCamera: (updates: Partial<CameraState>) => void;
    loadSplat: (file: File | string) => Promise<LoadResult>;
    clearSplat: () => void;
    resetCamera: () => void;
    fitToScreen: () => void;
}

// Hook return types
export interface UsePlayCanvasViewerReturn {
    app: any | null;
    isInitialized: boolean;
    error: string | null;
    initializeViewer: (canvas: HTMLCanvasElement) => Promise<void>;
    destroyViewer: () => void;
}

export interface UsePlayCanvasSplatLoaderReturn {
    isLoading: boolean;
    error: string | null;
    loadSplat: (file: File | string) => Promise<LoadResult>;
    clearSplat: () => void;
    splatEntity: any | null;
}

export interface UseOrbitControlsReturn {
    isEnabled: boolean;
    setEnabled: (enabled: boolean) => void;
    resetCamera: () => void;
    fitToScreen: () => void;
    getCameraState: () => CameraState;
    setCameraState: (state: Partial<CameraState>) => void;
}

// Asset management
export interface AssetInfo {
    name: string;
    type: string;
    file?: File;
    url?: string;
    loaded: boolean;
    error?: string;
}

export interface AssetRegistry {
    assets: Map<string, AssetInfo>;
    loadAsset: (file: File | string) => Promise<any>;
    removeAsset: (name: string) => void;
    clearAll: () => void;
}
