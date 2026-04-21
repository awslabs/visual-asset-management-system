/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * File loader utilities for THREE.js
 * Handles loading various 3D file formats
 */

export interface LoaderResult {
    object: any; // THREE.Object3D or THREE.Group
    animations?: any[]; // THREE.AnimationClip[]
}

/**
 * Get file extension from file key
 */
export function getFileExtension(fileKey: string): string {
    return fileKey.toLowerCase().split(".").pop() || "";
}

/**
 * Check if file is a supported THREE.js format
 */
export function isSupportedThreeFormat(fileKey: string): boolean {
    const ext = getFileExtension(fileKey);
    const supportedFormats = ["gltf", "glb", "obj", "fbx", "stl", "ply", "dae", "3ds", "3mf"];
    return supportedFormats.includes(ext);
}

/**
 * Check if file is an OCCT format (CAD)
 */
export function isOCCTFormat(fileKey: string): boolean {
    const ext = getFileExtension(fileKey);
    const occtFormats = ["stp", "step", "iges", "brep"];
    return occtFormats.includes(ext);
}

/**
 * Load GLTF/GLB file
 * Note: For .gltf files, dependencies should be pre-loaded using preloadGLTFDependencies
 * For .glb files, everything is embedded so no pre-loading needed
 */
export async function loadGLTF(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    GLTFLoader: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        const loader = new GLTFLoader();

        loader.parse(
            arrayBuffer,
            "", // Empty base path since we pre-load dependencies with blob URLs
            (gltf: any) => {
                console.log(
                    `GLTF loaded: ${fileName}, animations: ${gltf.animations?.length || 0}`
                );
                resolve({
                    object: gltf.scene,
                    animations: gltf.animations || [],
                });
            },
            (error: any) => {
                reject(new Error(`Failed to load GLTF: ${error.message || error}`));
            }
        );
    });
}

/**
 * Load OBJ file
 */
export async function loadOBJ(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    OBJLoader: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new OBJLoader();
            const text = new TextDecoder().decode(arrayBuffer);
            const object = loader.parse(text);
            resolve({ object });
        } catch (error: any) {
            reject(new Error(`Failed to load OBJ: ${error.message || error}`));
        }
    });
}

/**
 * Load FBX file
 */
export async function loadFBX(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    FBXLoader: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new FBXLoader();
            const object = loader.parse(arrayBuffer, "");
            resolve({ object });
        } catch (error: any) {
            reject(new Error(`Failed to load FBX: ${error.message || error}`));
        }
    });
}

/**
 * Load STL file
 */
export async function loadSTL(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    STLLoader: any,
    THREE: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new STLLoader();
            const geometry = loader.parse(arrayBuffer);

            // Create mesh with default material
            const material = new THREE.MeshStandardMaterial({ color: 0xcccccc });
            const mesh = new THREE.Mesh(geometry, material);
            mesh.name = fileName;

            resolve({ object: mesh });
        } catch (error: any) {
            reject(new Error(`Failed to load STL: ${error.message || error}`));
        }
    });
}

/**
 * Load PLY file
 */
export async function loadPLY(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    PLYLoader: any,
    THREE: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new PLYLoader();
            const geometry = loader.parse(arrayBuffer);

            // Create mesh with default material
            const material = new THREE.MeshStandardMaterial({ color: 0xcccccc });
            const mesh = new THREE.Mesh(geometry, material);
            mesh.name = fileName;

            resolve({ object: mesh });
        } catch (error: any) {
            reject(new Error(`Failed to load PLY: ${error.message || error}`));
        }
    });
}

/**
 * Load COLLADA file
 */
export async function loadCollada(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    ColladaLoader: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new ColladaLoader();
            const text = new TextDecoder().decode(arrayBuffer);
            const collada = loader.parse(text);
            resolve({ object: collada.scene });
        } catch (error: any) {
            reject(new Error(`Failed to load COLLADA: ${error.message || error}`));
        }
    });
}

/**
 * Load 3DS file
 */
export async function load3DS(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    TDSLoader: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new TDSLoader();
            const object = loader.parse(arrayBuffer);
            resolve({ object });
        } catch (error: any) {
            reject(new Error(`Failed to load 3DS: ${error.message || error}`));
        }
    });
}

/**
 * Load 3MF file
 */
export async function load3MF(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    ThreeMFLoader: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new ThreeMFLoader();
            const object = loader.parse(arrayBuffer);
            resolve({ object });
        } catch (error: any) {
            reject(new Error(`Failed to load 3MF: ${error.message || error}`));
        }
    });
}

/**
 * Load file based on extension
 */
export async function loadFile(
    arrayBuffer: ArrayBuffer,
    extension: string,
    fileName: string,
    manager?: any
): Promise<LoaderResult> {
    // Check if this is an OCCT format
    const occtFormats = ["stp", "step", "iges", "igs", "brep"];
    if (occtFormats.includes(extension.toLowerCase())) {
        // CRITICAL: Check for SharedArrayBuffer support FIRST
        // CAD files require WASM which needs SharedArrayBuffer
        if (typeof SharedArrayBuffer === "undefined") {
            throw new Error(
                `CAD Format Support Not Available.\n\n` +
                    `This CAD file format requires WebAssembly with SharedArrayBuffer support, which is not currently available. This may be due to:\n\n` +
                    `• Missing or incorrect web server headers (Cross-Origin-Embedder-Policy: credentialless and Cross-Origin-Opener-Policy: same-origin).\n` +
                    `• Browser restrictions or unsupported browser version.\n` +
                    `• Safari browser limitations (does not support required 'credentialless' policy).\n\n` +
                    `Note: Safari browser does not support the required 'credentialless' policy. Please use Chrome, Firefox, or Edge for CAD file viewing.\n\n` +
                    `Please contact your system administrator to enable WASM support for CAD files.`
            );
        }

        // Check if OCCT loader is available
        const bundle = (window as any).THREEBundle;
        if (!bundle || !bundle.occtimportjs) {
            throw new Error(
                `CAD format support not enabled. The ${extension.toUpperCase()} format requires the OCCT library. ` +
                    `Please contact your administrator to enable CAD format support for this viewer.`
            );
        }

        // Try to load with OCCT loader
        try {
            const { loadOCCTFile } = await import("./occtLoader");

            // Create blob URL from arrayBuffer for OCCT loader
            const blob = new Blob([arrayBuffer]);
            const blobUrl = URL.createObjectURL(blob);

            try {
                const object = await loadOCCTFile(blobUrl, fileName, bundle.THREE);
                URL.revokeObjectURL(blobUrl); // Clean up blob URL
                return { object };
            } catch (error) {
                URL.revokeObjectURL(blobUrl); // Clean up blob URL on error
                throw error;
            }
        } catch (error: any) {
            throw new Error(`Failed to load CAD file: ${error.message || error}`);
        }
    }

    // Get the bundle from global window object
    const bundle = (window as any).THREEBundle;

    if (!bundle) {
        throw new Error("ThreeJS bundle not loaded");
    }

    const {
        THREE,
        GLTFLoader,
        OBJLoader,
        FBXLoader,
        STLLoader,
        PLYLoader,
        ColladaLoader,
        TDSLoader,
        ThreeMFLoader,
    } = bundle;

    switch (extension) {
        case "gltf":
        case "glb":
            return loadGLTF(arrayBuffer, fileName, GLTFLoader);
        case "obj":
            return loadOBJ(arrayBuffer, fileName, OBJLoader);
        case "fbx":
            return loadFBX(arrayBuffer, fileName, FBXLoader);
        case "stl":
            return loadSTL(arrayBuffer, fileName, STLLoader, THREE);
        case "ply":
            return loadPLY(arrayBuffer, fileName, PLYLoader, THREE);
        case "dae":
            return loadCollada(arrayBuffer, fileName, ColladaLoader);
        case "3ds":
            return load3DS(arrayBuffer, fileName, TDSLoader);
        case "3mf":
            return load3MF(arrayBuffer, fileName, ThreeMFLoader);
        default:
            throw new Error(`Unsupported file format: ${extension}`);
    }
}
