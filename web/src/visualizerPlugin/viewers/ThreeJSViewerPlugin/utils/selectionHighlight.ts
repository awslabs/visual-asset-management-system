/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * How the Three.js viewer marks the objects selected in its panel, for every kind of material.
 *
 * A selected object is drawn with a highlight clone of its base material (the material library's
 * entry for it). A lit mesh material gets a green emissive glow. A material with no emissive
 * colour (`PointsMaterial` for a point cloud, line and sprite materials, `MeshBasicMaterial`) is
 * tinted green instead, and a vertex-coloured one stops reading its vertex colours, since the
 * tint would otherwise only darken them (#1275).
 *
 * Every clone made here is remembered with the material it was cloned from, so deselecting puts
 * back that exact material by identity and disposes the clone. Nothing is inferred from the
 * clone's colour values, which a user can change in the material editor.
 */

import type { MaterialLibraryItem } from "../ThreeJSMaterialLibrary";

/** The highlight green, the same as the scene graph's selected row. */
export const HIGHLIGHT_COLOR = 0x4caf50;
export const HIGHLIGHT_EMISSIVE_INTENSITY = 0.5;

/** Each highlight clone, keyed to the material it was cloned from. */
const highlightBase = new WeakMap<object, any>();

/** Point, line and sprite materials, which three.js draws without lighting or faces. */
function isUnlitPrimitiveMaterial(material: any): boolean {
    return !!(
        material?.isPointsMaterial ||
        material?.isLineBasicMaterial ||
        material?.isSpriteMaterial
    );
}

/**
 * Whether the material lights itself through an `emissive` colour (the lit mesh materials). A
 * point material is excluded even when the material editor has set an `emissive` on it: three.js
 * never reads it there.
 */
export function hasEmissive(material: any): boolean {
    return !!material?.emissive?.isColor && !isUnlitPrimitiveMaterial(material);
}

/**
 * Whether the material draws a wireframe. Points, lines and sprites have no faces to outline and
 * three.js gives their materials no `wireframe` property.
 */
export function supportsWireframe(material: any): boolean {
    return !!material && !isUnlitPrimitiveMaterial(material) && "wireframe" in material;
}

/**
 * Marks `material` (a highlight clone) as selected, in place: the emissive glow on a lit mesh
 * material, otherwise a green tint with vertex colours off.
 */
export function applySelectionHighlight(THREE: any, material: any): void {
    if (!material) return;
    if (hasEmissive(material)) {
        material.emissive = new THREE.Color(HIGHLIGHT_COLOR);
        material.emissiveIntensity = HIGHLIGHT_EMISSIVE_INTENSITY;
    } else {
        if (material.color?.isColor) material.color = new THREE.Color(HIGHLIGHT_COLOR);
        if (material.vertexColors) material.vertexColors = false;
    }
    material.needsUpdate = true;
}

/** A highlight clone of `base`, remembered so it can be told apart and undone. */
export function createHighlightMaterial(THREE: any, base: any): any {
    const highlight = base?.clone
        ? base.clone()
        : new THREE.MeshStandardMaterial({
              color: base?.color,
              metalness: base?.metalness || 0.5,
              roughness: base?.roughness || 0.5,
              opacity: base?.opacity || 1.0,
              transparent: base?.transparent || false,
          });
    applySelectionHighlight(THREE, highlight);
    highlightBase.set(highlight, base);
    return highlight;
}

/** Whether `material` is a highlight clone made by `createHighlightMaterial`. */
export function isHighlightMaterial(material: any): boolean {
    return !!material && typeof material === "object" && highlightBase.has(material);
}

/** The material a highlight clone was made from. */
export function highlightBaseOf(material: any): any {
    return isHighlightMaterial(material) ? highlightBase.get(material) : undefined;
}

/** Puts `material` back on `object` in place of its highlight clone, and frees the clone. */
export function releaseHighlight(object: any, material: any): void {
    const highlight = object?.material;
    if (!isHighlightMaterial(highlight)) return;
    object.material = material;
    highlightBase.delete(highlight);
    if (typeof highlight.dispose === "function") highlight.dispose();
}

/** The library material used by the object `uuid` (the last entry naming it, as before). */
function libraryMaterialFor(items: MaterialLibraryItem[], uuid: string): any {
    let base: any = null;
    items.forEach((item) => {
        if (item.usedBy.has(uuid)) base = item.material;
    });
    return base;
}

/**
 * Brings the scene's highlights in line with the selection: a deselected object gets its library
 * material back (or, when the library no longer names it, the material its clone was made from),
 * and a selected object gets a highlight clone of its library material, unless it already wears
 * one of that material.
 */
export function syncSelectionHighlight(
    THREE: any,
    scene: any,
    selectedObjects: any[],
    library: Iterable<MaterialLibraryItem>
): void {
    const items = Array.from(library);
    const selectedUuids = new Set(selectedObjects.map((obj) => obj.uuid));

    scene.traverse((obj: any) => {
        if (selectedUuids.has(obj.uuid) || !isHighlightMaterial(obj.material)) return;
        const restore = libraryMaterialFor(items, obj.uuid) ?? highlightBaseOf(obj.material);
        releaseHighlight(obj, restore);
    });

    selectedObjects.forEach((obj) => {
        if (!obj.material) return;
        const base = libraryMaterialFor(items, obj.uuid);
        if (!base) return;
        if (highlightBaseOf(obj.material) === base) return;
        if (isHighlightMaterial(obj.material)) releaseHighlight(obj, base);
        obj.material = createHighlightMaterial(THREE, base);
    });
}
