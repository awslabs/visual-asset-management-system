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
 * The meshes of an OCCT import result. OCCT reports a file it cannot read with `success: false`
 * and no meshes, which is turned into a short message instead of a script error.
 */
export function occtResultMeshes(result: any, format: string): any[] {
    if (!result?.success || !Array.isArray(result.meshes) || result.meshes.length === 0) {
        throw new Error(
            `The ${format} file could not be read. It may be damaged, incomplete or not a valid ${format} file.`
        );
    }
    return result.meshes;
}

/**
 * Shown instead of a blank canvas when OCCT reads a CAD file but none of its meshes has geometry.
 * Only STEP names the 3D Basic Conversion pipeline, which reads STEP but not IGES or BREP.
 */
export function occtNoGeometryMessage(format: string): string {
    const how =
        format === "STEP"
            ? "Convert it to a format such as GLB, for example with the 3D Basic Conversion pipeline, to view it."
            : "Convert it to a format such as GLB with a CAD application to view it.";
    return `The viewer could not build any geometry from this ${format} file. ${how}`;
}

/** True for the message occtNoGeometryMessage returns, for any format. */
export function isOcctNoGeometryError(message: string): boolean {
    return /^The viewer could not build any geometry from this \w+ file\./.test(message);
}

function arrayLength(value: any): number {
    return value && typeof value.length === "number" ? value.length : 0;
}

/**
 * True when an OCCT mesh can be drawn: at least one triangle of positions (whole xyz triples) and
 * an index of whole triangles. occt-import-js can return meshes with no vertices for a file it
 * reads but cannot tessellate (#1734).
 */
export function isDrawableOcctMesh(resultMesh: any): boolean {
    const positions = arrayLength(resultMesh?.attributes?.position?.array);
    const indices = arrayLength(resultMesh?.index?.array);
    return positions >= 9 && positions % 3 === 0 && indices >= 3 && indices % 3 === 0;
}

/**
 * The drawable meshes of an OCCT import result. A file OCCT cannot read keeps its short message;
 * meshes without geometry are left out, and a result where none is left fails with
 * occtNoGeometryMessage instead of opening an empty scene.
 */
export function occtDrawableMeshes(result: any, format: string): any[] {
    const meshes = occtResultMeshes(result, format);
    const drawable = meshes.filter(isDrawableOcctMesh);
    if (drawable.length === 0) {
        throw new Error(occtNoGeometryMessage(format));
    }
    if (drawable.length < meshes.length) {
        console.log(
            `OCCT: ${meshes.length - drawable.length} of ${
                meshes.length
            } ${format} meshes have no geometry and were left out`
        );
    }
    return drawable;
}

/** A three.js object holding one mesh per drawable mesh of an OCCT import result. */
export function buildOcctObject(result: any, format: string, THREE: any): any {
    const targetObject = new THREE.Object3D();
    for (const resultMesh of occtDrawableMeshes(result, format)) {
        const geometry = new THREE.BufferGeometry();
        const positions = resultMesh.attributes.position.array;
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
        if (resultMesh.attributes.normal) {
            geometry.setAttribute(
                "normal",
                new THREE.Float32BufferAttribute(resultMesh.attributes.normal.array, 3)
            );
        }
        // A 16-bit index wraps past 65,535 vertices, so larger meshes get a 32-bit one.
        const vertexCount = arrayLength(positions) / 3;
        const index =
            vertexCount > 65535
                ? Uint32Array.from(resultMesh.index.array)
                : Uint16Array.from(resultMesh.index.array);
        geometry.setIndex(new THREE.BufferAttribute(index, 1));

        const material = resultMesh.color
            ? new THREE.MeshPhongMaterial({
                  color: new THREE.Color(
                      resultMesh.color[0],
                      resultMesh.color[1],
                      resultMesh.color[2]
                  ),
              })
            : new THREE.MeshPhongMaterial({ color: 0xcccccc });

        targetObject.add(new THREE.Mesh(geometry, material));
    }
    return targetObject;
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

    // Initialize occt-import-js
    const occt = await occtimportjs({
        locateFile: (name: string) => {
            console.log("OCCT locateFile:", name);
            return wasmUrl;
        },
    });

    // Download the file
    const response = await fetch(fileUrl);
    const buffer = await response.arrayBuffer();

    // Read the imported step file
    const fileBuffer = new Uint8Array(buffer);
    const result = occt.ReadStepFile(fileBuffer);

    return buildOcctObject(result, "STEP", THREE);
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

    // Initialize occt-import-js
    const occt = await occtimportjs({
        locateFile: (name: string) => {
            console.log("OCCT locateFile:", name);
            return wasmUrl;
        },
    });

    // Download the file
    const response = await fetch(fileUrl);
    const buffer = await response.arrayBuffer();

    // Read the imported IGES file
    const fileBuffer = new Uint8Array(buffer);
    const result = occt.ReadIgesFile(fileBuffer);

    return buildOcctObject(result, "IGES", THREE);
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

    // Initialize occt-import-js
    const occt = await occtimportjs({
        locateFile: (name: string) => {
            console.log("OCCT locateFile:", name);
            return wasmUrl;
        },
    });

    // Download the file
    const response = await fetch(fileUrl);
    const buffer = await response.arrayBuffer();

    // Read the imported BREP file
    const fileBuffer = new Uint8Array(buffer);
    const result = occt.ReadBrepFile(fileBuffer);

    return buildOcctObject(result, "BREP", THREE);
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
        case "igs":
        case "iges":
            return LoadIges(fileUrl, THREE);
        case "brep":
            return LoadBrep(fileUrl, THREE);
        default:
            throw new Error(`Unsupported OCCT format: ${ext}`);
    }
}
