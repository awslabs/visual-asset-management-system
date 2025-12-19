/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

declare module "@veerum/viewer" {
    import * as React from "react";

    export interface ViewerController {
        add(object: any): Promise<void>;
        remove(object: any): void;
        zoomCameraToObject(object: any): Promise<void>;
        setCameraPosition(x: number, y: number, z: number): void;
        getCameraPosition(): { x: number; y: number; z: number };
        dispose(): void;
    }

    export interface VeerumViewerProps {
        viewerControllerRef: React.MutableRefObject<ViewerController | null>;
        style?: React.CSSProperties;
    }

    export const VeerumViewer: React.FC<VeerumViewerProps>;

    export class TileModel {
        constructor(id: string, url: string);
        id: string;
        url: string;
    }

    export class CubeObject {
        constructor(id: string, options?: { name?: string });
        id: string;
        name?: string;
        position: {
            set(x: number, y: number, z: number): void;
        };
    }

    export interface InsertionOptions {
        method?: "Inserting" | "Editing" | "Putting";
        autoScale?: boolean;
        onStart?: (data: any) => void;
        onUpdate?: (data: any) => void;
        onEnd?: (data: any) => void;
    }
}

// Extend window object to include VeerumViewer module
declare global {
    interface Window {
        VeerumViewer?: any;
    }
}
