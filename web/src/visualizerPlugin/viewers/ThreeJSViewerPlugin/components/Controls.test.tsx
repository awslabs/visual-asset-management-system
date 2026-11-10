/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Three.js viewer's Wireframe Mode outlines the faces of mesh materials. A point cloud's
 * `PointsMaterial` (and a line material) has no faces and three.js gives it no `wireframe`
 * property, so the toggle leaves it alone, and the control says it applies to meshes (#1275).
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import Controls from "./Controls";

function makeGroup(children: any[]): any {
    return {
        children,
        traverse(callback: (obj: any) => void) {
            callback(this);
            children.forEach((child) => callback(child));
        },
    };
}

function renderControls(threeRoot: any) {
    render(
        <Controls
            scene={{}}
            camera={{}}
            renderer={{}}
            threeRoot={threeRoot}
            controls={{}}
            enable3DSelection={true}
            onToggle3DSelection={jest.fn()}
        />
    );
    return screen.getByRole("checkbox", { name: "Wireframe Mode" });
}

describe("Controls wireframe mode", () => {
    beforeEach(() => {
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("flips mesh materials and leaves point and line materials untouched", () => {
        const meshMaterial = { type: "MeshStandardMaterial", wireframe: false };
        const pointsMaterial = { type: "PointsMaterial", isPointsMaterial: true, size: 0.01 };
        const lineMaterial = { type: "LineBasicMaterial", isLineBasicMaterial: true };
        const multiMaterials: any[] = [
            { type: "MeshPhongMaterial", wireframe: false },
            pointsMaterial,
        ];
        const root = makeGroup([
            { material: meshMaterial },
            { material: pointsMaterial },
            { material: lineMaterial },
            { material: multiMaterials },
        ]);

        const toggle = renderControls(root);
        fireEvent.click(toggle);

        expect(toggle).toBeChecked();
        expect(meshMaterial.wireframe).toBe(true);
        expect(multiMaterials[0].wireframe).toBe(true);
        expect("wireframe" in pointsMaterial).toBe(false);
        expect("wireframe" in lineMaterial).toBe(false);

        fireEvent.click(toggle);
        expect(meshMaterial.wireframe).toBe(false);
        expect("wireframe" in pointsMaterial).toBe(false);
    });

    it("tells the user the control applies to meshes", () => {
        const toggle = renderControls(makeGroup([]));
        expect(toggle).toHaveAccessibleDescription(/Applies to meshes/);
    });
});
