/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// IFC loading utilities for the That Open Engine viewer.
//
// Two layers live here:
//  1. Pure, unit-tested helpers for .ifczip detection and archive entry
//     selection (no WASM / no browser APIs).
//  2. Engine-bound helpers that drive the That Open IfcLoader / FragmentsManager
//     using the runtime bundle (window.ThatOpenWebIfcBundle). These are not unit
//     tested (they require the WASM engine + WebGL) and are verified in-app.

/** Base path where the customInstall copies the bundle, WASM, and workers. */
export const THATOPEN_ASSET_PATH = "/viewers/thatopenwebifc/";

/** Returns true when the file name has a .ifczip extension (case-insensitive). */
export function isIfcZip(fileName: string): boolean {
    return fileName.toLowerCase().endsWith(".ifczip");
}

/**
 * Picks the IFC entry to load from a list of archive entry names. Returns the
 * first entry ending in .ifc (case-insensitive), or null if none is present.
 */
export function pickIfcEntryName(entryNames: string[]): string | null {
    for (const name of entryNames) {
        if (name.toLowerCase().endsWith(".ifc")) {
            return name;
        }
    }
    return null;
}

/**
 * Extracts raw IFC bytes from a downloaded file buffer.
 * - For .ifc: returns the bytes unchanged.
 * - For .ifczip: unzips with fflate (from the bundle) and returns the first
 *   .ifc entry's bytes.
 *
 * @param bundle window.ThatOpenWebIfcBundle (provides unzipSync)
 * @param arrayBuffer the downloaded file bytes
 * @param fileName the file name (used to detect .ifczip)
 */
export function extractIfcBytes(
    bundle: any,
    arrayBuffer: ArrayBuffer,
    fileName: string
): Uint8Array {
    const bytes = new Uint8Array(arrayBuffer);
    if (!isIfcZip(fileName)) {
        return bytes;
    }

    // .ifczip — unzip and pull out the .ifc entry.
    const unzipped: Record<string, Uint8Array> = bundle.unzipSync(bytes);
    const entryName = pickIfcEntryName(Object.keys(unzipped));
    if (!entryName) {
        throw new Error("No .ifc file found inside the .ifczip archive");
    }
    return unzipped[entryName];
}

/**
 * Configures the That Open IfcLoader to use the self-hosted web-ifc WASM and
 * loads an IFC model from in-memory bytes. Returns the loaded Fragments model.
 *
 * Engine-bound; verified in-app.
 *
 * @param bundle window.ThatOpenWebIfcBundle
 * @param components OBC.Components instance (already init()'d)
 * @param bytes raw IFC bytes (from extractIfcBytes)
 * @param modelId stable id for the model in the FragmentsManager list
 * @param onProgress optional 0-100 progress callback
 */
export async function loadIfcModel(
    bundle: any,
    components: any,
    bytes: Uint8Array,
    modelId: string,
    onProgress?: (percent: number) => void
): Promise<any> {
    const { OBC } = bundle;
    const ifcLoader = components.get(OBC.IfcLoader);

    // Use the self-hosted WASM (copied next to the bundle). autoSetWasm:false
    // disables CDN auto-resolution so air-gapped/GovCloud works.
    await ifcLoader.setup({
        autoSetWasm: false,
        wasm: { path: THATOPEN_ASSET_PATH, absolute: true },
    });

    // load(data, coordinate, name, config?) — confirmed signature for
    // @thatopen/components 3.4.x. coordinate=false keeps original coordinates.
    const model = await ifcLoader.load(bytes, false, modelId, {
        processData: {
            progressCallback: (p: number) => onProgress?.(Math.round(p * 100)),
        },
    });
    return model;
}

/**
 * Frames the camera to fit all loaded model geometry using BoundingBoxer.
 *
 * @param bundle window.ThatOpenWebIfcBundle
 * @param components OBC.Components instance
 * @param world the active World (provides camera.controls)
 */
export async function fitCameraToModels(bundle: any, components: any, world: any): Promise<void> {
    const { OBC, THREE } = bundle;
    const boxer = components.get(OBC.BoundingBoxer);
    boxer.list.clear();
    boxer.addFromModels();
    const box = boxer.get();
    boxer.list.clear();

    const sphere = new THREE.Sphere();
    box.getBoundingSphere(sphere);
    if (sphere.radius > 0 && isFinite(sphere.radius)) {
        await world.camera.controls.fitToSphere(sphere, true);
    }
}

/** Named camera viewpoints offered by the Tools tab. */
export type CameraView = "top" | "front" | "back" | "left" | "right" | "iso";

/**
 * Computes the loaded models' bounding sphere (center + radius) via BoundingBoxer.
 * Returns null when nothing measurable is loaded yet.
 *
 * @param bundle window.ThatOpenWebIfcBundle
 * @param components OBC.Components instance
 */
function getSceneSphere(bundle: any, components: any): { center: any; radius: number } | null {
    const { OBC, THREE } = bundle;
    const boxer = components.get(OBC.BoundingBoxer);
    boxer.list.clear();
    boxer.addFromModels();
    const box = boxer.get();
    boxer.list.clear();

    const sphere = new THREE.Sphere();
    box.getBoundingSphere(sphere);
    if (!(sphere.radius > 0) || !isFinite(sphere.radius)) {
        return null;
    }
    return { center: sphere.center, radius: sphere.radius };
}

/**
 * Moves the camera to a named orthographic-style viewpoint around the model and
 * frames the whole model. Uses the camera-controls `setLookAt` API on
 * `world.camera.controls` (yomotsu camera-controls, exposed by SimpleCamera).
 *
 * @param bundle window.ThatOpenWebIfcBundle
 * @param components OBC.Components instance
 * @param world the active World
 * @param view which named viewpoint to move to
 */
export async function setCameraView(
    bundle: any,
    components: any,
    world: any,
    view: CameraView
): Promise<void> {
    const sphere = getSceneSphere(bundle, components);
    if (!sphere) return;

    const { center, radius } = sphere;
    // Pull the camera back far enough to frame the bounding sphere comfortably.
    const d = radius * 2.5;
    const cx = center.x;
    const cy = center.y;
    const cz = center.z;

    // Eye position per view (target is always the model center).
    let eye: [number, number, number];
    switch (view) {
        case "top":
            eye = [cx, cy + d, cz + 0.0001]; // tiny z offset keeps the up-vector stable
            break;
        case "front":
            eye = [cx, cy, cz + d];
            break;
        case "back":
            eye = [cx, cy, cz - d];
            break;
        case "left":
            eye = [cx - d, cy, cz];
            break;
        case "right":
            eye = [cx + d, cy, cz];
            break;
        case "iso":
        default:
            eye = [cx + d * 0.6, cy + d * 0.6, cz + d * 0.6];
            break;
    }

    await world.camera.controls.setLookAt(
        eye[0],
        eye[1],
        eye[2],
        cx,
        cy,
        cz,
        true // enable transition animation
    );
}

/**
 * Dollies the camera in or out by a fixed step. `direction` > 0 zooms in.
 * Uses camera-controls `dolly(distance, enableTransition)`; the step scales with
 * the model size so it feels consistent across small and large models.
 *
 * @param bundle window.ThatOpenWebIfcBundle
 * @param components OBC.Components instance
 * @param world the active World
 * @param direction +1 to zoom in, -1 to zoom out
 */
export function zoomCamera(bundle: any, components: any, world: any, direction: number): void {
    const sphere = getSceneSphere(bundle, components);
    const step = sphere ? sphere.radius * 0.4 : 1;
    // dolly() moves the camera along its view direction: positive = closer.
    world.camera.controls.dolly(direction > 0 ? step : -step, true);
}
