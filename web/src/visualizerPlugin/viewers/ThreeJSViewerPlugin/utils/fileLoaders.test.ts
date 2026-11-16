/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The OCCT (OpenCascade) loader's Embind glue compiles functions with `new Function`. Under the
 * default Content Security Policy, which has `'wasm-unsafe-eval'` but not `'unsafe-eval'`, the
 * browser refuses that and the load fails with its own CSP violation text. These tests pin that
 * the viewer names the deployment setting instead, and that every other failure is still wrapped.
 */

import {
    isCadSupportError,
    loadFile,
    loadPLY,
    PLY_DEFAULT_COLOR,
    PLY_POINT_SIZE_FRACTION,
} from "./fileLoaders";
import { loadOCCTFile } from "./occtLoader";

jest.mock("./occtLoader", () => ({
    loadOCCTFile: jest.fn(),
}));

const mockedLoadOCCTFile = loadOCCTFile as jest.MockedFunction<typeof loadOCCTFile>;

/** Chromium's wording for a `new Function` call blocked by a script-src without 'unsafe-eval'. */
const CHROMIUM_CSP_TEXT =
    "Evaluating a string as JavaScript violates the following Content Security Policy directive " +
    "because 'unsafe-eval' is not an allowed source of script: \"script-src 'self' 'wasm-unsafe-eval'\".";

const SETTING = "app.webUi.allowUnsafeEvalFeatures";

/** loadFile's message for a failure, so a resolved load fails the test instead of passing it. */
async function failureMessage(): Promise<string> {
    try {
        await loadFile(new ArrayBuffer(8), "step", "part.step");
    } catch (error: any) {
        return error.message;
    }
    throw new Error("loadFile resolved; expected it to reject");
}

describe("loadFile OCCT error messages", () => {
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;

    beforeEach(() => {
        mockedLoadOCCTFile.mockReset();
        // jsdom implements neither, and loadFile hands the OCCT loader a blob URL.
        URL.createObjectURL = jest.fn(() => "blob:test");
        URL.revokeObjectURL = jest.fn();
        (window as any).THREEBundle = { THREE: {}, occtimportjs: jest.fn() };
    });

    afterEach(() => {
        URL.createObjectURL = originalCreateObjectURL;
        URL.revokeObjectURL = originalRevokeObjectURL;
        delete (window as any).THREEBundle;
    });

    it("names the allowUnsafeEvalFeatures setting when the loader throws an EvalError", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new EvalError("call to Function() blocked by CSP"));

        const message = await failureMessage();

        expect(message).toContain("blocked by the Content Security Policy");
        expect(message).toContain(SETTING);
        expect(message).not.toContain("call to Function() blocked by CSP");
        expect(message).not.toContain("Failed to load CAD file");
    });

    it("names the setting instead of the browser's CSP violation text", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new Error(CHROMIUM_CSP_TEXT));

        const message = await failureMessage();

        expect(message).toContain(SETTING);
        expect(message).not.toContain(CHROMIUM_CSP_TEXT);
        expect(message).not.toContain("script-src");
        expect(message).not.toContain("Failed to load CAD file");
    });

    it("leads with the CAD heading the viewer gives priority to across files", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new EvalError("blocked"));

        // ThreeJSViewerComponent shows a CAD error first when every selected file failed, and
        // recognizes one by this heading (shared with the SharedArrayBuffer message).
        expect(await failureMessage()).toMatch(/^CAD Format Support Not Available\./);
    });

    it("keeps wrapping every other loader failure", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new Error("Unsupported OCCT format: xyz"));

        const message = await failureMessage();

        expect(message).toBe("Failed to load CAD file: Unsupported OCCT format: xyz");
        expect(message).not.toContain(SETTING);
    });

    it("revokes the blob URL when the loader fails", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new EvalError("blocked"));

        await failureMessage();

        expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test");
    });
});

describe("isCadSupportError", () => {
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;

    beforeEach(() => {
        mockedLoadOCCTFile.mockReset();
        URL.createObjectURL = jest.fn(() => "blob:test");
        URL.revokeObjectURL = jest.fn();
    });

    afterEach(() => {
        URL.createObjectURL = originalCreateObjectURL;
        URL.revokeObjectURL = originalRevokeObjectURL;
        delete (window as any).THREEBundle;
    });

    it("recognizes the Content Security Policy message", async () => {
        (window as any).THREEBundle = { THREE: {}, occtimportjs: jest.fn() };
        mockedLoadOCCTFile.mockRejectedValue(new EvalError("blocked"));

        expect(isCadSupportError(await failureMessage())).toBe(true);
    });

    it("recognizes the message for a bundle built without OCCT", async () => {
        (window as any).THREEBundle = { THREE: {} };

        expect(isCadSupportError(await failureMessage())).toBe(true);
    });

    it("does not treat a CAD file that failed to load as a setup problem", async () => {
        (window as any).THREEBundle = { THREE: {}, occtimportjs: jest.fn() };
        mockedLoadOCCTFile.mockRejectedValue(new Error("Unsupported OCCT format: xyz"));

        expect(isCadSupportError(await failureMessage())).toBe(false);
        expect(isCadSupportError("Failed to load: part.glb (404)")).toBe(false);
    });
});

/**
 * #1257: a PLY with no faces is a point cloud and must be drawn as points, and a PLY with
 * red/green/blue vertex properties must be drawn in those colours. These tests drive loadPLY
 * with a stand-in PLYLoader and the THREE classes it builds, so they record what loadPLY made.
 */
describe("loadPLY (#1257)", () => {
    class BufferAttribute {
        constructor(public array: any, public itemSize: number, public normalized = false) {}
    }
    class Material {
        constructor(public params: Record<string, any>) {}
    }
    class MeshStandardMaterial extends Material {}
    class PointsMaterial extends Material {}
    class Mesh {
        name = "";
        isMesh = true;
        constructor(public geometry: any, public material: any) {}
    }
    class Points {
        name = "";
        isPoints = true;
        constructor(public geometry: any, public material: any) {}
    }
    const THREE = { BufferAttribute, MeshStandardMaterial, PointsMaterial, Mesh, Points };

    /** A geometry shaped like PLYLoader's: attributes, an index only when the file has faces. */
    function plyGeometry(options: { faces?: boolean; colors?: boolean; normals?: boolean }) {
        const attributes: Record<string, any> = {
            // Two corners of a 3 x 4 x 12 box: a diagonal of 13.
            position: new BufferAttribute(new Float32Array([0, 0, 0, 3, 4, 12, 1, 1, 1]), 3),
        };
        if (options.colors) {
            attributes.color = new BufferAttribute(new Float32Array(9).fill(0.5), 3);
        }
        if (options.normals) {
            attributes.normal = new BufferAttribute(new Float32Array(9), 3);
        }
        return {
            attributes,
            index: options.faces ? new BufferAttribute(new Uint32Array([0, 1, 2]), 1) : null,
            computeVertexNormals: jest.fn(),
        };
    }

    async function load(geometry: any) {
        class PLYLoader {
            parse() {
                return geometry;
            }
        }
        const { object } = await loadPLY(new ArrayBuffer(8), "cloud.ply", PLYLoader, THREE);
        return object;
    }

    it("draws a coloured PLY with no faces as points in its vertex colours", async () => {
        const object = await load(plyGeometry({ colors: true, normals: true }));

        expect(object).toBeInstanceOf(Points);
        expect(object.name).toBe("cloud.ply");
        expect(object.material).toBeInstanceOf(PointsMaterial);
        expect(object.material.params.vertexColors).toBe(true);
        expect(object.material.params.color).toBe(0xffffff);
        expect(object.material.params.sizeAttenuation).toBe(true);
        expect(object.material.params.size).toBeCloseTo(13 * PLY_POINT_SIZE_FRACTION);
    });

    it("draws a PLY with no faces and no colours as grey points", async () => {
        const object = await load(plyGeometry({}));

        expect(object).toBeInstanceOf(Points);
        expect(object.material.params.vertexColors).toBe(false);
        expect(object.material.params.color).toBe(PLY_DEFAULT_COLOR);
    });

    it("draws a coloured mesh PLY with vertex colours", async () => {
        const geometry = plyGeometry({ faces: true, colors: true, normals: true });
        const object = await load(geometry);

        expect(object).toBeInstanceOf(Mesh);
        expect(object.material).toBeInstanceOf(MeshStandardMaterial);
        expect(object.material.params.vertexColors).toBe(true);
        expect(object.material.params.color).toBe(0xffffff);
        expect(geometry.computeVertexNormals).not.toHaveBeenCalled();
    });

    it("draws a plain mesh PLY in grey and computes its missing normals", async () => {
        const geometry = plyGeometry({ faces: true });
        const object = await load(geometry);

        expect(object).toBeInstanceOf(Mesh);
        expect(object.material.params.vertexColors).toBe(false);
        expect(object.material.params.color).toBe(PLY_DEFAULT_COLOR);
        expect(geometry.computeVertexNormals).toHaveBeenCalledTimes(1);
    });
});
