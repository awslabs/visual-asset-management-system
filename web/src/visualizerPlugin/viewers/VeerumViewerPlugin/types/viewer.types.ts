/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ViewerPluginProps } from "../../../core/types";

/**
 * Props for the VEERUM Viewer Component
 * Extends the base ViewerPluginProps with VEERUM-specific properties
 */
export interface VeerumViewerProps extends ViewerPluginProps {
    assetId: string;
    databaseId: string;
    assetKey?: string;
    versionId?: string;
}

/**
 * VEERUM ViewerController interface
 * Main controller for interacting with the VEERUM Viewer
 */
export interface ViewerController {
    add(object: any): Promise<void>;
    remove(object: any): void;
    zoomCameraToObject(object: any): Promise<void>;
    setCameraPosition(x: number, y: number, z: number): void;
    getCameraPosition(): { x: number; y: number; z: number };
    dispose(): void;
}

/**
 * VEERUM Viewer React Component Props
 */
export interface VeerumViewerComponentProps {
    viewerControllerRef: React.MutableRefObject<ViewerController | null>;
    style?: React.CSSProperties;
}

/**
 * Model types for VEERUM Viewer
 */
export interface TileModel {
    id: string;
    url: string;
}

export interface CubeObject {
    id: string;
    name?: string;
    position: {
        set(x: number, y: number, z: number): void;
    };
}

/**
 * VEERUM Viewer load event
 */
export interface VeerumLoadEvent {
    detail?: {
        time?: number;
    };
}

/**
 * VEERUM Viewer error event
 */
export interface VeerumErrorEvent {
    detail?: {
        message?: string;
    };
}
