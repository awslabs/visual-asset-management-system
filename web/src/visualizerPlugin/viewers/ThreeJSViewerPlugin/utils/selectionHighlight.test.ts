/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Which materials have an emissive colour or a wireframe, and how a highlight clone is told apart
 * from the material it was made from (#1275).
 */

import {
    createHighlightMaterial,
    hasEmissive,
    highlightBaseOf,
    isHighlightMaterial,
    releaseHighlight,
    supportsWireframe,
} from "./selectionHighlight";

class Color {
    isColor = true;
    constructor(public hex = 0xffffff) {}
    getHex() {
        return this.hex;
    }
}

const THREE = { Color, MeshStandardMaterial: class {} };

function material(fields: Record<string, any>): any {
    const mat: any = { ...fields, dispose: jest.fn() };
    mat.clone = () => material({ ...fields, color: new Color(fields.color?.hex) });
    return mat;
}

describe("selectionHighlight", () => {
    it("names emissive and wireframe support by material kind", () => {
        const standard = material({ emissive: new Color(0), wireframe: false });
        const points = material({ isPointsMaterial: true, color: new Color() });
        // The material editor can set an emissive on a point material; three.js never reads it.
        const editedPoints = material({ isPointsMaterial: true, emissive: new Color(0) });
        const line = material({ isLineBasicMaterial: true });
        const basic = material({ wireframe: false, color: new Color() });

        expect(hasEmissive(standard)).toBe(true);
        expect(hasEmissive(points)).toBe(false);
        expect(hasEmissive(editedPoints)).toBe(false);
        expect(hasEmissive(basic)).toBe(false);
        expect(supportsWireframe(standard)).toBe(true);
        expect(supportsWireframe(basic)).toBe(true);
        expect(supportsWireframe(points)).toBe(false);
        expect(supportsWireframe({ ...points, wireframe: true })).toBe(false);
        expect(supportsWireframe(line)).toBe(false);
        expect(supportsWireframe(null)).toBe(false);
    });

    it("tints a material without emissive and remembers which material the clone came from", () => {
        const base = material({
            isPointsMaterial: true,
            color: new Color(0x123456),
            vertexColors: true,
        });
        const highlight = createHighlightMaterial(THREE, base);

        expect(highlight.color.getHex()).toBe(0x4caf50);
        expect(highlight.vertexColors).toBe(false);
        expect(isHighlightMaterial(highlight)).toBe(true);
        expect(isHighlightMaterial(base)).toBe(false);
        expect(highlightBaseOf(highlight)).toBe(base);

        const object = { material: highlight };
        releaseHighlight(object, base);
        expect(object.material).toBe(base);
        expect(highlight.dispose).toHaveBeenCalledTimes(1);
        expect(isHighlightMaterial(highlight)).toBe(false);
    });

    it("does not touch an object that does not wear a highlight", () => {
        const base = material({ color: new Color() });
        const object = { material: base };
        releaseHighlight(object, material({}));
        expect(object.material).toBe(base);
        expect(base.dispose).not.toHaveBeenCalled();
    });
});
