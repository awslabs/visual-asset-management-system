/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export interface MaterialLibraryItem {
    id: string;
    name: string;
    material: any; // THREE.Material
    usedBy: Set<string>; // UUIDs of objects using this material
    isCustom: boolean;
    originalMaterial: any; // THREE.Material - for reset functionality
}

export interface TransformData {
    position: { x: number; y: number; z: number };
    rotation: { x: number; y: number; z: number };
    scale: { x: number; y: number; z: number };
    isTopLevel?: boolean;
    worldPosition?: { x: number; y: number; z: number };
    worldRotation?: { x: number; y: number; z: number };
    worldScale?: { x: number; y: number; z: number };
}

export interface FileError {
    file: string;
    error: string;
}

export interface ViewerInstance {
    scene: any; // THREE.Scene
    camera: any; // THREE.Camera
    renderer: any; // THREE.WebGLRenderer
    fileGroups: any[]; // THREE.Group[]
    controls: any; // MouseControls
    clickHandler: (event: MouseEvent) => void;
}
