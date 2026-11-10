/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * File loader utilities for THREE.js
 * Handles loading various 3D file formats
 */

import { createLoadingManager, isolatedUrlModifier } from "./assetDependencyResolver";
import { checkUsdzSize, isUsdzBytes } from "./usdDependencies";

export interface LoaderResult {
    object: any; // THREE.Object3D or THREE.Group
    animations?: any[]; // THREE.AnimationClip[]
    /** Referenced files that were not loaded, one human-readable line each. */
    warnings?: string[];
}

export interface LoadFileOptions {
    /** Materials for an OBJ file, from its material libraries (an MTLLoader MaterialCreator). */
    materials?: any;
}

/**
 * CAD formats routed to the OCCT loader, without the leading dot. Every entry
 * must also appear in the `threejs-viewer` entry's `supportedExtensions` in
 * config/viewerConfig.json, or the registry never offers this viewer for it.
 */
export const OCCT_FORMATS = ["stp", "step", "iges", "igs", "brep"];

/**
 * USD formats routed to three.js's USDLoader, without the leading dot. Every entry must also
 * appear in the `threejs-viewer` entry's `supportedExtensions` in config/viewerConfig.json.
 */
export const USD_FORMATS = ["usd", "usda", "usdc", "usdz"];

/** Mesh formats three.js loads directly, without the leading dot. */
export const THREE_MESH_FORMATS = ["gltf", "glb", "obj", "fbx", "stl", "ply", "dae", "3ds", "3mf"];

/**
 * True for the CAD setup messages loadFile throws when the browser or deployment cannot run the
 * OCCT loader (SharedArrayBuffer, the OCCT bundle, or the Content Security Policy), as opposed to
 * a file that failed to load.
 */
export function isCadSupportError(message: string): boolean {
    return (
        message.includes("CAD Format Support Not Available") ||
        message.includes("CAD format support not enabled") ||
        message.includes("OCCT library") ||
        message.includes("SharedArrayBuffer")
    );
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
    return THREE_MESH_FORMATS.includes(ext) || USD_FORMATS.includes(ext);
}

/**
 * Check if file is an OCCT format (CAD)
 */
export function isOCCTFormat(fileKey: string): boolean {
    return OCCT_FORMATS.includes(getFileExtension(fileKey));
}

/**
 * True when an error is the browser refusing `eval`/`new Function` under the Content Security
 * Policy: an EvalError (matched by name too, in case it comes from another realm), or an error
 * whose message is the browser's CSP violation text.
 */
function isEvalBlockedError(error: any): boolean {
    if (error instanceof EvalError || error?.name === "EvalError") {
        return true;
    }
    const message = String(error?.message ?? error ?? "");
    return /unsafe-eval|Content Security Policy/i.test(message);
}

/**
 * Load GLTF/GLB file
 * Note: For .gltf files, dependencies should be pre-loaded using preloadGLTFDependencies
 * For .glb files, everything is embedded so no pre-loading needed
 */
export async function loadGLTF(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    GLTFLoader: any,
    manager?: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        const loader = new GLTFLoader(manager);

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
    OBJLoader: any,
    manager?: any,
    materials?: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new OBJLoader(manager);
            if (materials) {
                loader.setMaterials(materials);
            }
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
    FBXLoader: any,
    manager?: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new FBXLoader(manager);
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
 * WebGL has no 64-bit float vertex attributes, and PLYLoader keeps `double` properties as
 * Float64Array. Each such attribute is replaced by a Float32Array copy.
 */
export function convertFloat64Attributes(geometry: any, THREE: any): void {
    const attributes = geometry?.attributes || {};
    Object.keys(attributes).forEach((name) => {
        const attribute = attributes[name];
        if (attribute?.array instanceof Float64Array) {
            geometry.setAttribute(
                name,
                new THREE.BufferAttribute(
                    new Float32Array(attribute.array),
                    attribute.itemSize,
                    attribute.normalized
                )
            );
        }
    });
}

/** The grey a PLY file without per-vertex colours is drawn in. */
export const PLY_DEFAULT_COLOR = 0xcccccc;

/**
 * Point size for a PLY point cloud, in world units (the material uses `sizeAttenuation`): a
 * fraction of the cloud's bounding-box diagonal, so a point is a pixel or two wide when the camera
 * frames the whole cloud and grows as the user zooms in. The viewer has no point-size control.
 */
export const PLY_POINT_SIZE_FRACTION = 1 / 200;

/** The bounding-box diagonal of a position attribute, read from its array (stride `itemSize`). */
function positionDiagonal(position: any): number {
    const array = position?.array;
    const stride = position?.itemSize || 3;
    if (!array || array.length < 3) {
        return 0;
    }
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i + 2 < array.length; i += stride) {
        for (let axis = 0; axis < 3; axis++) {
            const value = array[i + axis];
            if (Number.isFinite(value)) {
                if (value < min[axis]) min[axis] = value;
                if (value > max[axis]) max[axis] = value;
            }
        }
    }
    if (!Number.isFinite(min[0]) || !Number.isFinite(min[1]) || !Number.isFinite(min[2])) {
        return 0;
    }
    return Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
}

/**
 * The object for a parsed PLY geometry. A PLY with no faces (no index) is a point cloud and is
 * drawn as `THREE.Points`; one with faces is a `THREE.Mesh`, given vertex normals when the file has
 * none so it shades. A `color` attribute (the file's red/green/blue vertex properties) is drawn
 * with `vertexColors`; without one the object keeps the default grey.
 */
export function createPLYObject(geometry: any, fileName: string, THREE: any): any {
    const attributes = geometry?.attributes || {};
    const hasColors = !!attributes.color;
    const isPointCloud = !geometry?.index;

    let object: any;
    if (isPointCloud) {
        const diagonal = positionDiagonal(attributes.position);
        const material = new THREE.PointsMaterial({
            size: diagonal > 0 ? diagonal * PLY_POINT_SIZE_FRACTION : 1,
            sizeAttenuation: true,
            vertexColors: hasColors,
            color: hasColors ? 0xffffff : PLY_DEFAULT_COLOR,
        });
        object = new THREE.Points(geometry, material);
    } else {
        if (!attributes.normal && typeof geometry.computeVertexNormals === "function") {
            geometry.computeVertexNormals();
        }
        const material = new THREE.MeshStandardMaterial({
            vertexColors: hasColors,
            color: hasColors ? 0xffffff : PLY_DEFAULT_COLOR,
        });
        object = new THREE.Mesh(geometry, material);
    }
    object.name = fileName;
    return object;
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
            convertFloat64Attributes(geometry, THREE);

            resolve({ object: createPLYObject(geometry, fileName, THREE) });
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
    ColladaLoader: any,
    manager?: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new ColladaLoader(manager);
            const text = new TextDecoder().decode(arrayBuffer);
            const collada = loader.parse(text, "");
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
    TDSLoader: any,
    manager?: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new TDSLoader(manager);
            const object = loader.parse(arrayBuffer, "");
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
    ThreeMFLoader: any,
    manager?: any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            const loader = new ThreeMFLoader(manager);
            const object = loader.parse(arrayBuffer);
            resolve({ object });
        } catch (error: any) {
            reject(new Error(`Failed to load 3MF: ${error.message || error}`));
        }
    });
}

/**
 * Load a USD file (.usd, .usda, .usdc or .usdz). A .usdz is size-checked before it is parsed.
 * Resolves once the scene and its textures are ready.
 */
export async function loadUSD(
    arrayBuffer: ArrayBuffer,
    fileName: string,
    USDLoader: any,
    manager?: any,
    unzipSync?: (data: Uint8Array, opts: any) => any
): Promise<LoaderResult> {
    return new Promise((resolve, reject) => {
        try {
            if (!USDLoader) {
                throw new Error("this viewer bundle has no USD loader");
            }
            const bytes = new Uint8Array(arrayBuffer);
            if (isUsdzBytes(bytes) && unzipSync) {
                checkUsdzSize(bytes, unzipSync);
            }
            const loader = new USDLoader(manager);
            loader.parse(
                arrayBuffer,
                "",
                (group: any) => {
                    console.log(
                        `USD loaded: ${fileName}, animations: ${group?.animations?.length || 0}`
                    );
                    resolve({ object: group, animations: group?.animations || [] });
                },
                (error: any) => {
                    reject(new Error(`Failed to load USD: ${error?.message || error}`));
                }
            );
        } catch (error: any) {
            reject(new Error(`Failed to load USD: ${error?.message || error}`));
        }
    });
}

/**
 * Load file based on extension.
 *
 * `manager` is the three.js LoadingManager every loader requests referenced files through. When
 * none is given, one that answers every external URL with a placeholder is used, so a loader never
 * fetches a referenced file by URL on its own.
 */
export async function loadFile(
    arrayBuffer: ArrayBuffer,
    extension: string,
    fileName: string,
    manager?: any,
    options: LoadFileOptions = {}
): Promise<LoaderResult> {
    // Check if this is an OCCT format
    if (OCCT_FORMATS.includes(extension.toLowerCase())) {
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
            // The OCCT Embind glue compiles functions with `new Function`, which a script-src
            // without 'unsafe-eval' refuses. Name the deployment setting rather than showing the
            // browser's CSP violation text, which quotes the whole directive.
            if (isEvalBlockedError(error)) {
                throw new Error(
                    `CAD Format Support Not Available.\n\n` +
                        `CAD format viewing is blocked by the Content Security Policy. The OpenCascade loader used for STEP, IGES and BREP files needs app.webUi.allowUnsafeEvalFeatures set to true in the deployment configuration.\n\n` +
                        `Please contact your administrator to enable CAD format support for this viewer.`
                );
            }
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
        USDLoader,
        fflate,
    } = bundle;

    const loadingManager = manager ?? createLoadingManager(THREE, isolatedUrlModifier);

    switch (extension) {
        case "gltf":
        case "glb":
            return loadGLTF(arrayBuffer, fileName, GLTFLoader, loadingManager);
        case "obj":
            return loadOBJ(arrayBuffer, fileName, OBJLoader, loadingManager, options.materials);
        case "fbx":
            return loadFBX(arrayBuffer, fileName, FBXLoader, loadingManager);
        case "stl":
            return loadSTL(arrayBuffer, fileName, STLLoader, THREE);
        case "ply":
            return loadPLY(arrayBuffer, fileName, PLYLoader, THREE);
        case "dae":
            return loadCollada(arrayBuffer, fileName, ColladaLoader, loadingManager);
        case "3ds":
            return load3DS(arrayBuffer, fileName, TDSLoader, loadingManager);
        case "3mf":
            return load3MF(arrayBuffer, fileName, ThreeMFLoader, loadingManager);
        case "usd":
        case "usda":
        case "usdc":
        case "usdz":
            return loadUSD(arrayBuffer, fileName, USDLoader, loadingManager, fflate?.unzipSync);
        default:
            throw new Error(`Unsupported file format: ${extension}`);
    }
}
