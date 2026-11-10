/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The OCCT (OpenCascade) loader's Embind glue compiles functions with `new Function`. Under the
 * default Content Security Policy, which has `'wasm-unsafe-eval'` but not `'unsafe-eval'`, the
 * browser refuses that and the load fails with its own CSP violation text. These tests pin that
 * the viewer names the deployment setting instead, and that every other failure is still wrapped.
 *
 * The USD tests pin how the four USD extensions reach three.js's USDLoader, that a .usdz is
 * size-checked before parsing, and that the loader's manager never lets an external URL through.
 */

import {
    convertFloat64Attributes,
    isCadSupportError,
    isSupportedThreeFormat,
    loadFile,
    loadPLY,
    PLY_DEFAULT_COLOR,
    PLY_POINT_SIZE_FRACTION,
    USD_FORMATS,
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

describe("loadFile USD", () => {
    class FakeLoadingManager {
        modifier: ((url: string) => string) | null = null;
        setURLModifier(modifier: (url: string) => string) {
            this.modifier = modifier;
            return this;
        }
    }

    let parse: jest.Mock;
    let constructed: any[];

    beforeEach(() => {
        constructed = [];
        parse = jest.fn((_buffer, _path, onLoad) => onLoad({ name: "usd", animations: ["clip"] }));
        class FakeUSDLoader {
            parse = parse;
            constructor(manager: any) {
                constructed.push(manager);
            }
        }
        (window as any).THREEBundle = {
            THREE: { LoadingManager: FakeLoadingManager },
            USDLoader: FakeUSDLoader,
            fflate: { unzipSync: jest.fn(() => ({})) },
        };
    });

    afterEach(() => {
        delete (window as any).THREEBundle;
    });

    it.each(USD_FORMATS)("routes .%s to USDLoader.parse with an empty base path", async (ext) => {
        const buffer = new ArrayBuffer(16);

        const result = await loadFile(buffer, ext, `scene.${ext}`);

        expect(parse).toHaveBeenCalledWith(buffer, "", expect.any(Function), expect.any(Function));
        expect(result.object.name).toBe("usd");
        expect(result.animations).toEqual(["clip"]);
    });

    it("gives USDLoader a manager that never lets an external URL through", async () => {
        await loadFile(new ArrayBuffer(4), "usda", "a.usda");

        const manager = constructed[0];
        expect(manager.modifier("textures/a.png")).toMatch(/^data:image\/png;base64,/);
        expect(manager.modifier("blob:x")).toBe("blob:x");
    });

    it("wraps a loader error", async () => {
        parse.mockImplementation((_b, _p, _onLoad, onError) => onError(new Error("bad layer")));

        await expect(loadFile(new ArrayBuffer(4), "usdc", "a.usdc")).rejects.toThrow(
            "Failed to load USD: bad layer"
        );
    });

    it("wraps a synchronous parse failure", async () => {
        parse.mockImplementation(() => {
            throw new Error("Invalid USDZ package");
        });

        await expect(loadFile(new ArrayBuffer(4), "usdz", "a.usdz")).rejects.toThrow(
            "Failed to load USD: Invalid USDZ package"
        );
    });

    it("rejects an oversize .usdz before parsing it", async () => {
        (window as any).THREEBundle.fflate.unzipSync = jest.fn((_bytes: any, opts: any) => {
            opts.filter({ name: "x.usdc", originalSize: 4 * 1024 * 1024 * 1024 });
            return {};
        });
        const usdz = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0, 0, 0, 0]).buffer;

        await expect(loadFile(usdz, "usdz", "bomb.usdz")).rejects.toThrow(/expands to/);
        expect(parse).not.toHaveBeenCalled();
    });
});

describe("format helpers", () => {
    it("recognizes USD and mesh formats", () => {
        expect(isSupportedThreeFormat("a/b/scene.USDZ")).toBe(true);
        expect(isSupportedThreeFormat("scene.usda")).toBe(true);
        expect(isSupportedThreeFormat("model.glb")).toBe(true);
        expect(isSupportedThreeFormat("doc.pdf")).toBe(false);
    });

    it("converts Float64 PLY attributes to Float32", () => {
        class BufferAttribute {
            constructor(public array: any, public itemSize: number, public normalized: boolean) {}
        }
        const position = { array: new Float64Array([1.5, 2, 3]), itemSize: 3, normalized: false };
        const color = { array: new Float32Array([1, 0, 0]), itemSize: 3, normalized: false };
        const geometry = {
            attributes: { position, color } as Record<string, any>,
            setAttribute(name: string, attribute: any) {
                this.attributes[name] = attribute;
            },
        };

        convertFloat64Attributes(geometry, { BufferAttribute });

        expect(geometry.attributes.position.array).toBeInstanceOf(Float32Array);
        expect(Array.from(geometry.attributes.position.array)).toEqual([1.5, 2, 3]);
        expect(geometry.attributes.position.itemSize).toBe(3);
        expect(geometry.attributes.color).toBe(color);
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
            setAttribute(name: string, attribute: any) {
                this.attributes[name] = attribute;
            },
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

    it("still converts Float64 positions before building the points", async () => {
        const geometry = plyGeometry({ colors: true });
        geometry.attributes.position = new BufferAttribute(
            new Float64Array([0, 0, 0, 3, 4, 12]),
            3
        );
        const object = await load(geometry);

        expect(object.geometry.attributes.position.array).toBeInstanceOf(Float32Array);
    });
});
