/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * OCCT (Open CASCADE Technology) loader for CAD file formats
 * Supports STEP, IGES, and BREP files
 *
 * NOTE: This requires occt-import-js to be installed and bundled
 * See README.md for instructions on enabling OCCT support
 */

// Use local WASM file from public directory (copied during build if OCCT is installed)
const wasmUrl = "/viewers/threejs/occt-import-js.wasm";

/**
 * Check if OCCT is available
 */
export function isOCCTAvailable(): boolean {
    const bundle = (window as any).THREEBundle;
    return !!(bundle && bundle.occtimportjs);
}

/**
 * Get OCCT error message for unsupported files
 */
export function getOCCTUnavailableMessage(): string {
    return "STEP/BREP/IGES file viewing is not available for the viewer until the library is enabled and packaged with the ThreeJS dynamic viewer. See VAMS admin documentation to enable.";
}

/**
 * Load STEP file using OCCT
 */
export async function LoadStep(fileUrl: string, THREE: any): Promise<any> {
    const bundle = (window as any).THREEBundle;

    if (!bundle || !bundle.occtimportjs) {
        throw new Error(getOCCTUnavailableMessage());
    }

    const occtimportjs = bundle.occtimportjs;
    const targetObject = new THREE.Object3D();

    // Initialize occt-import-js
    const occt = await occtimportjs({
        locateFile: (name: string) => {
            console.log("OCCT locateFile:", name);
            return wasmUrl;
        },
    });

    // Download the file
    let response = await fetch(fileUrl);
    let buffer = await response.arrayBuffer();

    // Read the imported step file
    let fileBuffer = new Uint8Array(buffer);
    let result = occt.ReadStepFile(fileBuffer);

    // Process the geometries of the result
    for (let resultMesh of result.meshes) {
        let geometry = new THREE.BufferGeometry();

        geometry.setAttribute(
            "position",
            new THREE.Float32BufferAttribute(resultMesh.attributes.position.array, 3)
        );
        if (resultMesh.attributes.normal) {
            geometry.setAttribute(
                "normal",
                new THREE.Float32BufferAttribute(resultMesh.attributes.normal.array, 3)
            );
        }
        const index = Uint16Array.from(resultMesh.index.array);
        geometry.setIndex(new THREE.BufferAttribute(index, 1));

        let material = null;
        if (resultMesh.color) {
            const color = new THREE.Color(
                resultMesh.color[0],
                resultMesh.color[1],
                resultMesh.color[2]
            );
            material = new THREE.MeshPhongMaterial({ color: color });
        } else {
            material = new THREE.MeshPhongMaterial({ color: 0xcccccc });
        }

        const mesh = new THREE.Mesh(geometry, material);
        targetObject.add(mesh);
    }

    return targetObject;
}

/**
 * Load IGES file using OCCT
 */
export async function LoadIges(fileUrl: string, THREE: any): Promise<any> {
    const bundle = (window as any).THREEBundle;

    if (!bundle || !bundle.occtimportjs) {
        throw new Error(getOCCTUnavailableMessage());
    }

    const occtimportjs = bundle.occtimportjs;
    const targetObject = new THREE.Object3D();

    // Initialize occt-import-js
    const occt = await occtimportjs({
        locateFile: (name: string) => {
            console.log("OCCT locateFile:", name);
            return wasmUrl;
        },
    });

    // Download the file
    let response = await fetch(fileUrl);
    let buffer = await response.arrayBuffer();

    // Read the imported IGES file
    let fileBuffer = new Uint8Array(buffer);
    let result = occt.ReadIgesFile(fileBuffer);

    // Process the geometries of the result
    for (let resultMesh of result.meshes) {
        let geometry = new THREE.BufferGeometry();

        geometry.setAttribute(
            "position",
            new THREE.Float32BufferAttribute(resultMesh.attributes.position.array, 3)
        );
        if (resultMesh.attributes.normal) {
            geometry.setAttribute(
                "normal",
                new THREE.Float32BufferAttribute(resultMesh.attributes.normal.array, 3)
            );
        }
        const index = Uint16Array.from(resultMesh.index.array);
        geometry.setIndex(new THREE.BufferAttribute(index, 1));

        let material = null;
        if (resultMesh.color) {
            const color = new THREE.Color(
                resultMesh.color[0],
                resultMesh.color[1],
                resultMesh.color[2]
            );
            material = new THREE.MeshPhongMaterial({ color: color });
        } else {
            material = new THREE.MeshPhongMaterial({ color: 0xcccccc });
        }

        const mesh = new THREE.Mesh(geometry, material);
        targetObject.add(mesh);
    }

    return targetObject;
}

/**
 * Load BREP file using OCCT
 */
export async function LoadBrep(fileUrl: string, THREE: any): Promise<any> {
    const bundle = (window as any).THREEBundle;

    if (!bundle || !bundle.occtimportjs) {
        throw new Error(getOCCTUnavailableMessage());
    }

    const occtimportjs = bundle.occtimportjs;
    const targetObject = new THREE.Object3D();

    // Initialize occt-import-js
    const occt = await occtimportjs({
        locateFile: (name: string) => {
            console.log("OCCT locateFile:", name);
            return wasmUrl;
        },
    });

    // Download the file
    let response = await fetch(fileUrl);
    let buffer = await response.arrayBuffer();

    // Read the imported BREP file
    let fileBuffer = new Uint8Array(buffer);
    let result = occt.ReadBrepFile(fileBuffer);

    // Process the geometries of the result
    for (let resultMesh of result.meshes) {
        let geometry = new THREE.BufferGeometry();

        geometry.setAttribute(
            "position",
            new THREE.Float32BufferAttribute(resultMesh.attributes.position.array, 3)
        );
        if (resultMesh.attributes.normal) {
            geometry.setAttribute(
                "normal",
                new THREE.Float32BufferAttribute(resultMesh.attributes.normal.array, 3)
            );
        }
        const index = Uint16Array.from(resultMesh.index.array);
        geometry.setIndex(new THREE.BufferAttribute(index, 1));

        let material = null;
        if (resultMesh.color) {
            const color = new THREE.Color(
                resultMesh.color[0],
                resultMesh.color[1],
                resultMesh.color[2]
            );
            material = new THREE.MeshPhongMaterial({ color: color });
        } else {
            material = new THREE.MeshPhongMaterial({ color: 0xcccccc });
        }

        const mesh = new THREE.Mesh(geometry, material);
        targetObject.add(mesh);
    }

    return targetObject;
}

/**
 * Load OCCT file based on extension
 */
export async function loadOCCTFile(fileUrl: string, fileName: string, THREE: any): Promise<any> {
    const ext = fileName.toLowerCase().split(".").pop();

    switch (ext) {
        case "stp":
        case "step":
            return LoadStep(fileUrl, THREE);
        case "iges":
            return LoadIges(fileUrl, THREE);
        case "brep":
            return LoadBrep(fileUrl, THREE);
        default:
            throw new Error(`Unsupported OCCT format: ${ext}`);
    }
}
