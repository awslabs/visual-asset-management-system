/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ViewerPluginProps } from "../../../core/types";

// Re-export the base props
export interface BabylonJSGaussianSplatViewerProps extends ViewerPluginProps {}

// BabylonJS-specific types for Gaussian Splat viewing
export interface GaussianSplatOptions {
    keepInRam?: boolean;
    progressiveUpdateAmount?: number;
}

export interface XRState {
    supported: boolean;
    active: boolean;
    helper?: any; // BabylonJS XR helper type
}

export interface ViewerState {
    loaded: boolean;
    loading: boolean;
    error: string | null;
    xrState: XRState;
    viewer?: any; // BabylonJS viewer instance
    scene?: any; // BabylonJS Scene type
    engine?: any; // BabylonJS Engine type
    camera?: any; // BabylonJS Camera type
}

export interface CameraState {
    position: any; // BabylonJS Vector3 type
    rotation: any; // BabylonJS Vector3 type
    target: any; // BabylonJS Vector3 type
    radius: number;
}

export interface SplatControls {
    rotate: () => void;
    reset: () => void;
    fitToScreen: () => void;
}

export type PlyType = 'splat' | 'pc';

// Settings and configuration specific to Gaussian Splat viewing
export interface ViewerSettings {
    backgroundColor: { r: number; g: number; b: number };
    showGrid: boolean;
    enableXR: boolean;
    autoRotate: boolean;
    rotationSpeed: number;
    enableControls: boolean;
    gaussianSplatOptions: GaussianSplatOptions;
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
    splatMesh?: any; // BabylonJS GaussianSplatMesh type
    error?: string;
    fileInfo: SplatFileInfo;
}

// Context types for BabylonJS viewer
export interface BabylonJSViewerContextType {
    state: ViewerState;
    settings: ViewerSettings;
    controls: SplatControls;
    updateState: (updates: Partial<ViewerState>) => void;
    updateSettings: (updates: Partial<ViewerSettings>) => void;
    loadSplat: (file: File | string) => Promise<LoadResult>;
    clearSplat: () => void;
    resetCamera: () => void;
    toggleXR: () => Promise<void>;
}

// Hook return types
export interface UseBabylonJSViewerReturn {
    viewer: any | null;
    isInitialized: boolean;
    error: string | null;
    initializeViewer: (canvas: HTMLCanvasElement) => Promise<void>;
    destroyViewer: () => void;
}

export interface UseSplatLoaderReturn {
    isLoading: boolean;
    error: string | null;
    loadSplat: (file: File | string) => Promise<LoadResult>;
    clearSplat: () => void;
    splatMesh: any | null;
}

export interface UseXRReturn {
    isSupported: boolean;
    isActive: boolean;
    isLoading: boolean;
    error: string | null;
    toggleXR: () => Promise<void>;
    exitXR: () => void;
}
